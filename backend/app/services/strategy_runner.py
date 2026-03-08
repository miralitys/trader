from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.events import publish_event
from app.core.metrics import SIGNALS_CREATED
from app.models.entities import Candle, Instrument, Position, Setting, Signal, Trade
from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.indicators import atr, ema
from app.strategies.mean_reversion_hard_stop import generate_mean_reversion_hard_stop_signal
from app.strategies.profiles import apply_strategy_overrides, get_strategy_profile, resolve_strategy_scope
from app.strategies.pullback_to_trend import generate_pullback_to_trend_signal
from app.strategies.trend_retrace_70 import generate_trend_retrace_70_signal
from app.strategies.types import CandleData, SignalPlan

logger = logging.getLogger(__name__)
DEFAULT_STRATEGY_PRIORITY = [
    "StrategyBreakoutRetest",
    "StrategyPullbackToTrend",
    "MeanReversionHardStop",
    "StrategyTrendRetrace70",
]


def _strategy_bool(signal_cfg: dict, canonical: str, legacy: str, default: bool) -> bool:
    if canonical in signal_cfg:
        return bool(signal_cfg.get(canonical))
    if legacy in signal_cfg:
        return bool(signal_cfg.get(legacy))
    return default


def _strategy_float(signal_cfg: dict, canonical: str, legacy: str, default: float) -> float:
    if canonical in signal_cfg:
        return float(signal_cfg.get(canonical))
    if legacy in signal_cfg:
        return float(signal_cfg.get(legacy))
    return default


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _load_candles(db: Session, instrument_id: int, timeframe: str, limit: int) -> list[CandleData]:
    rows = db.scalars(
        select(Candle)
        .where(Candle.instrument_id == instrument_id, Candle.timeframe == timeframe)
        .order_by(Candle.ts.desc())
        .limit(limit)
    ).all()
    rows = list(reversed(rows))
    return [
        CandleData(
            ts=row.ts,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
        for row in rows
    ]


def _regime_filter(
    candles_1h: list[CandleData],
    atr_threshold_pct: float,
) -> tuple[bool, dict]:
    if len(candles_1h) < 220:
        return False, {"reason": "insufficient_1h_history"}

    closes = [x.close for x in candles_1h]
    highs = [x.high for x in candles_1h]
    lows = [x.low for x in candles_1h]

    ema200 = ema(closes, 200)
    ema_now = ema200[-1]
    ema_prev = ema200[-5]
    slope = ema_now - ema_prev

    atr_1h = atr(highs, lows, closes, 14)[-1]
    atr_pct = (atr_1h / max(closes[-1], 1e-8)) * 100

    passed = closes[-1] > ema_now and slope >= 0 and atr_pct < atr_threshold_pct
    return passed, {
        "close_1h": closes[-1],
        "ema200_1h": ema_now,
        "ema200_slope": slope,
        "atr_pct_1h": atr_pct,
    }


def _confirm_15m(candles_15m: list[CandleData]) -> tuple[bool, dict]:
    if len(candles_15m) < 60:
        return False, {"reason": "insufficient_15m_history"}
    closes = [x.close for x in candles_15m]
    ema50 = ema(closes, 50)
    ok = closes[-1] > ema50[-1]
    return ok, {"close_15m": closes[-1], "ema50_15m": ema50[-1]}


def expire_stale_signals(db: Session, now: datetime | None = None) -> int:
    ts_now = now or datetime.now(timezone.utc)
    rows = db.scalars(
        select(Signal).where(
            Signal.status == "active",
            Signal.expires_at <= ts_now,
        )
    ).all()
    if not rows:
        return 0

    for row in rows:
        row.status = "expired"
    db.commit()
    return len(rows)


def _blocking_active_signal(db: Session, instrument_id: int, now: datetime) -> Signal | None:
    return db.scalar(
        select(Signal)
        .where(
            Signal.instrument_id == instrument_id,
            Signal.status == "active",
            Signal.expires_at > now,
        )
        .order_by(Signal.created_at.desc())
        .limit(1)
    )


def _has_open_position(db: Session, instrument_id: int) -> bool:
    row = db.scalar(
        select(Position).where(
            Position.instrument_id == instrument_id,
            Position.status == "open",
        )
    )
    return row is not None


def _last_closed_trade_at(db: Session, instrument_id: int) -> datetime | None:
    row = db.scalar(
        select(Trade)
        .where(
            Trade.instrument_id == instrument_id,
            Trade.status == "closed",
            Trade.closed_at.is_not(None),
        )
        .order_by(desc(Trade.closed_at))
        .limit(1)
    )
    return _as_utc(row.closed_at) if row and row.closed_at else None


def _latest_closed_5m_candle_ts(candles_5m: list[CandleData], now: datetime) -> datetime | None:
    if not candles_5m:
        return None
    latest = candles_5m[-1]
    latest_ts = _as_utc(latest.ts)
    if latest_ts is None:
        return None
    if latest_ts + timedelta(minutes=5) > now:
        return None
    return latest_ts


def _resolve_priority(enabled_strategies: list[str], configured: object) -> list[str]:
    priority: list[str] = []
    if isinstance(configured, list):
        for item in configured:
            strategy = str(item).strip()
            if strategy and strategy not in priority:
                priority.append(strategy)

    for strategy in DEFAULT_STRATEGY_PRIORITY:
        if strategy not in priority:
            priority.append(strategy)
    for strategy in enabled_strategies:
        if strategy not in priority:
            priority.append(strategy)

    enabled = set(enabled_strategies)
    return [strategy for strategy in priority if strategy in enabled]


def _publish_signal_suppressed(
    *,
    symbol: str,
    reason: str,
    strategy: str | None = None,
    detail: dict | None = None,
) -> None:
    payload = {
        "symbol": symbol,
        "reason": reason,
    }
    if strategy:
        payload["strategy"] = strategy
    if detail:
        payload.update(detail)
    publish_event("signal_suppressed", payload)
    logger.info("signal_suppressed", extra={"context": payload})


def _persist_signal(db: Session, instrument: Instrument, plan: SignalPlan, ttl_minutes: int) -> Signal:
    now = datetime.now(timezone.utc)
    created_at = plan.created_at or now
    expires_at = plan.expires_at or (created_at + timedelta(minutes=ttl_minutes))
    meta = plan.meta.copy() if isinstance(plan.meta, dict) else {}
    if plan.takes:
        meta.setdefault("take", plan.takes)
        meta.setdefault("take_targets", plan.takes)
    signal = Signal(
        instrument_id=instrument.id,
        strategy=plan.strategy,
        timeframe=plan.timeframe,
        signal=plan.signal,
        entry=plan.entry,
        stop=plan.stop,
        take=plan.take,
        confidence=plan.confidence,
        reason=plan.reason,
        created_at=created_at,
        expires_at=expires_at,
        status=plan.status or "active",
        meta_json=meta,
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)

    SIGNALS_CREATED.labels(strategy=signal.strategy, symbol=instrument.symbol).inc()
    publish_event(
        "signal_created",
        {
            "signal_id": signal.id,
            "symbol": instrument.symbol,
            "strategy": signal.strategy,
            "entry": signal.entry,
            "stop": signal.stop,
            "take": signal.take,
            "confidence": signal.confidence,
        },
    )
    return signal


def run_strategy_cycle(db: Session, setting: Setting) -> dict:
    top_symbols = setting.universe_json.get("top_symbols", [])
    if not top_symbols:
        return {"generated": 0, "reason": "empty_universe"}

    now = datetime.now(timezone.utc)
    expired = expire_stale_signals(db, now=now)
    strategy_preferences = setting.strategy_params_json or {}
    enabled_strategies = resolve_strategy_scope(strategy_preferences.get("trade_only_strategy", "both"))
    priority_order = _resolve_priority(enabled_strategies, strategy_preferences.get("strategy_priority"))
    raw_cooldown = strategy_preferences.get("strategy_signal_cooldown_minutes", 30)
    cooldown_minutes = max(0, int(30 if raw_cooldown is None else raw_cooldown))

    generated = 0
    suppressed_due_active = 0
    suppressed_due_open_position = 0
    suppressed_due_cooldown = 0
    suppressed_due_candle_not_closed = 0
    suppressed_due_priority = 0

    for symbol in top_symbols:
        instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if not instrument:
            continue

        candles_5m = _load_candles(db, instrument.id, "5m", 400)
        candles_1h = _load_candles(db, instrument.id, "1h", 260)
        candles_15m = _load_candles(db, instrument.id, "15m", 120)
        closed_candle_ts = _latest_closed_5m_candle_ts(candles_5m, now)
        if closed_candle_ts is None:
            suppressed_due_candle_not_closed += 1
            _publish_signal_suppressed(symbol=symbol, reason="latest_5m_candle_not_closed")
            continue

        active_signal = _blocking_active_signal(db, instrument.id, now)
        if active_signal:
            suppressed_due_active += 1
            _publish_signal_suppressed(
                symbol=symbol,
                strategy=active_signal.strategy,
                reason="active_signal_exists",
                detail={"active_signal_id": active_signal.id},
            )
            continue

        if _has_open_position(db, instrument.id):
            suppressed_due_open_position += 1
            _publish_signal_suppressed(symbol=symbol, reason="open_position_exists")
            continue

        last_closed_trade_at = _last_closed_trade_at(db, instrument.id)
        if last_closed_trade_at and last_closed_trade_at + timedelta(minutes=cooldown_minutes) > now:
            suppressed_due_cooldown += 1
            _publish_signal_suppressed(
                symbol=symbol,
                reason="symbol_cooldown_active",
                detail={
                    "cooldown_minutes": cooldown_minutes,
                    "last_closed_trade_at": last_closed_trade_at.isoformat(),
                },
            )
            continue

        candidates: list[SignalPlan] = []
        for strategy_name in enabled_strategies:
            profile = apply_strategy_overrides(
                get_strategy_profile(strategy_name),
                strategy_preferences,
                strategy_name,
            )
            signal_cfg = profile.get("signal", {})
            risk_cfg = profile.get("risk", {})

            regime_meta: dict = {}
            regime_filter_enabled = True
            atr_threshold_pct = 4.0
            confirm_15m_enabled = False
            if strategy_name == "StrategyBreakoutRetest":
                regime_filter_enabled = _strategy_bool(signal_cfg, "br_ema200_filter_1h", "ema200_filter_1h", True)
                atr_threshold_pct = _strategy_float(signal_cfg, "br_atr_threshold_pct_1h", "atr_threshold_pct_1h", 5.0)
                confirm_15m_enabled = _strategy_bool(signal_cfg, "br_confirm_15m", "confirm_15m", True)
            elif strategy_name == "StrategyPullbackToTrend":
                regime_filter_enabled = _strategy_bool(signal_cfg, "pt_ema200_filter_1h", "ema200_filter_1h", True)
                atr_threshold_pct = _strategy_float(signal_cfg, "pt_atr_threshold_pct_1h", "atr_threshold_pct_1h", 5.0)
                confirm_15m_enabled = _strategy_bool(signal_cfg, "pt_confirm_15m", "confirm_15m", True)
            elif strategy_name == "MeanReversionHardStop":
                regime_filter_enabled = _strategy_bool(signal_cfg, "mr_ema200_filter_1h", "ema200_filter_1h", True)
                atr_threshold_pct = _strategy_float(signal_cfg, "mr_atr_threshold_pct_1h", "atr_threshold_pct_1h", 3.5)
                confirm_15m_enabled = _strategy_bool(signal_cfg, "mr_confirm_15m", "confirm_15m", True)
            elif strategy_name == "StrategyTrendRetrace70":
                regime_filter_enabled = _strategy_bool(signal_cfg, "tr_ema200_filter_1h", "ema200_filter_1h", True)
                atr_threshold_pct = _strategy_float(signal_cfg, "tr_atr_threshold_pct_1h", "atr_threshold_pct_1h", 4.5)
                confirm_15m_enabled = _strategy_bool(signal_cfg, "tr_confirm_15m", "confirm_15m", True)

            if regime_filter_enabled:
                regime_ok, regime_meta = _regime_filter(
                    candles_1h,
                    atr_threshold_pct=atr_threshold_pct,
                )
                if not regime_ok:
                    continue
            else:
                regime_meta = {"reason": "ema200_filter_disabled"}

            if confirm_15m_enabled:
                conf_ok, conf_meta = _confirm_15m(candles_15m)
                if not conf_ok:
                    continue
                regime_meta.update(conf_meta)

            plan = None
            if strategy_name == "StrategyBreakoutRetest":
                breakout_ttl = int(
                    signal_cfg.get(
                        "br_signal_ttl_minutes",
                        risk_cfg.get("entry_ttl_minutes", 60),
                    )
                )
                plan = generate_breakout_retest_signal(
                    candles_5m=candles_5m,
                    context={
                        "regime_state": {"ok": True, **regime_meta},
                        "params": {
                            "br_lookback_n": signal_cfg.get("br_lookback_n", signal_cfg.get("breakout_lookback", 20)),
                            "br_atr_period": signal_cfg.get("br_atr_period", 14),
                            "br_retest_atr_k": signal_cfg.get("br_retest_atr_k", signal_cfg.get("breakout_retest_k_atr", 0.3)),
                            "br_stop_atr_mult": signal_cfg.get("br_stop_atr_mult", signal_cfg.get("breakout_stop_atr_mult", 1.0)),
                            "br_tp1_rr": signal_cfg.get("br_tp1_rr", 1.0),
                            "br_tp2_rr": signal_cfg.get("br_tp2_rr", signal_cfg.get("breakout_tp_rr", 2.0)),
                            "br_trail_ema_period": signal_cfg.get("br_trail_ema_period", 20),
                            "br_signal_ttl_minutes": breakout_ttl,
                        },
                        "instrument": {"symbol": instrument.symbol},
                    },
                )
            elif strategy_name == "StrategyPullbackToTrend":
                pullback_ttl = int(
                    signal_cfg.get(
                        "pt_signal_ttl_minutes",
                        risk_cfg.get("entry_ttl_minutes", 60),
                    )
                )
                plan = generate_pullback_to_trend_signal(
                    candles_5m=candles_5m,
                    context={
                        "regime_state": {"ok": True, **regime_meta},
                        "params": {
                            "pt_ema_fast": signal_cfg.get("pt_ema_fast", 20),
                            "pt_ema_slow": signal_cfg.get("pt_ema_slow", 50),
                            "pt_rsi_period": signal_cfg.get("pt_rsi_period", 14),
                            "pt_rsi_threshold": signal_cfg.get("pt_rsi_threshold", signal_cfg.get("pullback_rsi_threshold", 45.0)),
                            "pt_stop_lookback": signal_cfg.get("pt_stop_lookback", 10),
                            "pt_tp_rr": signal_cfg.get("pt_tp_rr", 1.2),
                            "pt_signal_ttl_minutes": pullback_ttl,
                        },
                        "instrument": {"symbol": instrument.symbol},
                    },
                )
            elif strategy_name == "MeanReversionHardStop":
                mean_reversion_ttl = int(
                    signal_cfg.get(
                        "mr_signal_ttl_minutes",
                        risk_cfg.get("entry_ttl_minutes", 60),
                    )
                )
                plan = generate_mean_reversion_hard_stop_signal(
                    candles_5m=candles_5m,
                    context={
                        "regime_state": {"ok": True, **regime_meta},
                        "params": {
                            "mr_bb_period": signal_cfg.get("mr_bb_period", 20),
                            "mr_bb_std": signal_cfg.get("mr_bb_std", 2.0),
                            "mr_rsi_period": signal_cfg.get("mr_rsi_period", 14),
                            "mr_rsi_entry_threshold": signal_cfg.get("mr_rsi_entry_threshold", 30.0),
                            "mr_safety_ema_period": signal_cfg.get("mr_safety_ema_period", 200),
                            "mr_lookback_stop": signal_cfg.get("mr_lookback_stop", 15),
                            "mr_stop_atr_buffer": signal_cfg.get("mr_stop_atr_buffer", 0.2),
                            "mr_max_stop_pct": signal_cfg.get("mr_max_stop_pct", 0.03),
                            "mr_tp_rr": signal_cfg.get("mr_tp_rr", 1.2),
                            "mr_signal_ttl_minutes": mean_reversion_ttl,
                        },
                        "instrument": {"symbol": instrument.symbol},
                    },
                )
            elif strategy_name == "StrategyTrendRetrace70":
                trend_ttl = int(
                    signal_cfg.get(
                        "tr_signal_ttl_minutes",
                        risk_cfg.get("entry_ttl_minutes", 180),
                    )
                )
                plan = generate_trend_retrace_70_signal(
                    candles_5m=candles_5m,
                    context={
                        "regime_state": {"ok": True, **regime_meta},
                        "params": {
                            "tr_pivot_left_right": signal_cfg.get("tr_pivot_left_right", 3),
                            "tr_wave_tf": signal_cfg.get("tr_wave_tf", "15m"),
                            "tr_min_impulse_atr": signal_cfg.get("tr_min_impulse_atr", 1.5),
                            "tr_retrace_target": signal_cfg.get("tr_retrace_target", 0.70),
                            "tr_retrace_zone_low": signal_cfg.get("tr_retrace_zone_low", 0.62),
                            "tr_retrace_zone_high": signal_cfg.get("tr_retrace_zone_high", 0.78),
                            "tr_retrace_tolerance": signal_cfg.get("tr_retrace_tolerance", 0.05),
                            "tr_trigger_mode": signal_cfg.get("tr_trigger_mode", "ema20"),
                            "tr_trigger_ema_period": signal_cfg.get("tr_trigger_ema_period", 20),
                            "tr_trigger_lookback": signal_cfg.get("tr_trigger_lookback", 6),
                            "tr_stop_lookback": signal_cfg.get("tr_stop_lookback", 12),
                            "tr_stop_atr_buffer": signal_cfg.get("tr_stop_atr_buffer", 0.2),
                            "tr_max_stop_pct": signal_cfg.get("tr_max_stop_pct", 0.04),
                            "tr_tp2_rr": signal_cfg.get("tr_tp2_rr", 2.0),
                            "tr_signal_ttl_minutes": trend_ttl,
                            "tr_safety_ema_period": signal_cfg.get("tr_safety_ema_period", 200),
                        },
                        "instrument": {"symbol": instrument.symbol},
                        "candles_15m": candles_15m,
                    },
                )

            if not plan:
                continue

            plan.meta.update({"regime": regime_meta})
            candidates.append(plan)

        if not candidates:
            continue

        priority_rank = {name: idx for idx, name in enumerate(priority_order)}
        candidates.sort(key=lambda item: (priority_rank.get(item.strategy, 999), -float(item.confidence)))
        selected = candidates[0]
        for suppressed in candidates[1:]:
            suppressed_due_priority += 1
            _publish_signal_suppressed(
                symbol=symbol,
                strategy=suppressed.strategy,
                reason="lower_priority_than_selected_signal",
                detail={"selected_strategy": selected.strategy},
            )

        ttl_minutes = int(get_strategy_profile(selected.strategy).get("risk", {}).get("entry_ttl_minutes", 60))
        if selected.strategy == "StrategyBreakoutRetest":
            ttl_minutes = int(selected.meta.get("br_signal_ttl_minutes", ttl_minutes))
        elif selected.strategy == "StrategyPullbackToTrend":
            ttl_minutes = int(selected.meta.get("pt_signal_ttl_minutes", ttl_minutes))
        elif selected.strategy == "MeanReversionHardStop":
            ttl_minutes = int(selected.meta.get("mr_signal_ttl_minutes", ttl_minutes))
        elif selected.strategy == "StrategyTrendRetrace70":
            ttl_minutes = int(selected.meta.get("tr_signal_ttl_minutes", ttl_minutes))

        _persist_signal(
            db,
            instrument,
            selected,
            ttl_minutes=ttl_minutes,
        )
        generated += 1

    return {
        "generated": generated,
        "symbols_checked": len(top_symbols),
        "expired_signals": expired,
        "suppressed_due_active_signal": suppressed_due_active,
        "suppressed_due_open_position": suppressed_due_open_position,
        "suppressed_due_cooldown": suppressed_due_cooldown,
        "suppressed_due_candle_not_closed": suppressed_due_candle_not_closed,
        "suppressed_due_priority": suppressed_due_priority,
    }
