from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.events import publish_event
from app.core.metrics import SIGNALS_CREATED
from app.models.entities import Candle, Instrument, Setting, Signal
from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.indicators import atr, ema
from app.strategies.mean_reversion_hard_stop import generate_mean_reversion_hard_stop_signal
from app.strategies.profiles import apply_strategy_overrides, get_strategy_profile, resolve_strategy_scope
from app.strategies.pullback_trend import generate_pullback_signal
from app.strategies.trend_retrace_70 import generate_trend_retrace_70_signal
from app.strategies.types import CandleData, SignalPlan


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


def _signal_exists(db: Session, instrument_id: int, strategy: str) -> bool:
    row = db.scalar(
        select(Signal).where(
            Signal.instrument_id == instrument_id,
            Signal.strategy == strategy,
            Signal.status == "active",
        )
    )
    return row is not None


def _has_active_signal(db: Session, instrument_id: int) -> bool:
    row = db.scalar(
        select(Signal).where(
            Signal.instrument_id == instrument_id,
            Signal.status == "active",
        )
    )
    return row is not None


def _persist_signal(db: Session, instrument: Instrument, plan: SignalPlan, ttl_minutes: int) -> Signal:
    now = datetime.now(timezone.utc)
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
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
        status="active",
        meta_json=plan.meta,
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

    strategy_preferences = setting.strategy_params_json or {}
    enabled_strategies = resolve_strategy_scope(strategy_preferences.get("trade_only_strategy", "both"))

    generated = 0
    suppressed_due_active = 0

    for symbol in top_symbols:
        instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if not instrument:
            continue

        candles_5m = _load_candles(db, instrument.id, "5m", 400)
        candles_1h = _load_candles(db, instrument.id, "1h", 260)
        candles_15m = _load_candles(db, instrument.id, "15m", 120)

        # One active signal per symbol across all strategies to avoid conflicting entries.
        if _has_active_signal(db, instrument.id):
            suppressed_due_active += 1
            continue

        created_for_symbol = False
        for strategy_name in enabled_strategies:
            if created_for_symbol:
                break
            if _signal_exists(db, instrument.id, strategy_name):
                continue

            profile = apply_strategy_overrides(
                get_strategy_profile(strategy_name),
                strategy_preferences,
                strategy_name,
            )
            signal_cfg = profile.get("signal", {})
            risk_cfg = profile.get("risk", {})

            regime_meta: dict = {}
            if bool(signal_cfg.get("ema200_filter_1h", True)):
                regime_ok, regime_meta = _regime_filter(
                    candles_1h,
                    atr_threshold_pct=float(signal_cfg.get("atr_threshold_pct_1h", 4.0)),
                )
                if not regime_ok:
                    continue
            else:
                regime_meta = {"reason": "ema200_filter_disabled"}

            if bool(signal_cfg.get("confirm_15m", False)):
                conf_ok, conf_meta = _confirm_15m(candles_15m)
                if not conf_ok:
                    continue
                regime_meta.update(conf_meta)

            plan = None
            if strategy_name == "StrategyBreakoutRetest":
                plan = generate_breakout_retest_signal(
                    candles_5m=candles_5m,
                    lookback=int(signal_cfg.get("breakout_lookback", 20)),
                    retest_k_atr=float(signal_cfg.get("breakout_retest_k_atr", 0.3)),
                    stop_atr_mult=float(signal_cfg.get("breakout_stop_atr_mult", 1.0)),
                    tp_rr=float(signal_cfg.get("breakout_tp_rr", 2.0)),
                    min_volume_ratio=float(signal_cfg.get("breakout_min_volume_ratio", 0.0)),
                    min_confidence=float(signal_cfg.get("breakout_min_confidence", 0.0)),
                )
            elif strategy_name == "StrategyPullbackToTrend":
                plan = generate_pullback_signal(
                    candles_5m=candles_5m,
                    rsi_threshold=float(signal_cfg.get("pullback_rsi_threshold", 45.0)),
                )
            elif strategy_name == "MeanReversionHardStop":
                plan = generate_mean_reversion_hard_stop_signal(
                    candles_5m=candles_5m,
                    bb_period=int(signal_cfg.get("mr_bb_period", 20)),
                    bb_std=float(signal_cfg.get("mr_bb_std", 2.0)),
                    rsi_period=int(signal_cfg.get("mr_rsi_period", 14)),
                    rsi_entry_threshold=float(signal_cfg.get("mr_rsi_entry_threshold", 30.0)),
                    safety_ema_period=int(signal_cfg.get("mr_safety_ema_period", 200)),
                    lookback_stop=int(signal_cfg.get("mr_lookback_stop", 15)),
                    stop_atr_buffer=float(signal_cfg.get("mr_stop_atr_buffer", 0.2)),
                    max_stop_pct=float(signal_cfg.get("mr_max_stop_pct", 0.03)),
                    tp_rr=float(signal_cfg.get("mr_tp_rr", 1.2)),
                    regime_meta=regime_meta,
                )
            elif strategy_name == "StrategyTrendRetrace70":
                plan = generate_trend_retrace_70_signal(
                    candles_5m=candles_5m,
                    ema_fast_period=int(signal_cfg.get("tr70_ema_fast_period", 20)),
                    ema_mid_period=int(signal_cfg.get("tr70_ema_mid_period", 50)),
                    ema_slow_period=int(signal_cfg.get("tr70_ema_slow_period", 200)),
                    pullback_lookback=int(signal_cfg.get("tr70_pullback_lookback", 10)),
                    pullback_depth_pct=float(signal_cfg.get("tr70_pullback_depth_pct", 0.35)),
                    reclaim_buffer_pct=float(signal_cfg.get("tr70_reclaim_buffer_pct", 0.05)),
                    rsi_period=int(signal_cfg.get("tr70_rsi_period", 14)),
                    rsi_min=float(signal_cfg.get("tr70_rsi_min", 42.0)),
                    rsi_max=float(signal_cfg.get("tr70_rsi_max", 62.0)),
                    stop_atr_mult=float(signal_cfg.get("tr70_stop_atr_mult", 0.7)),
                    min_stop_pct=float(signal_cfg.get("tr70_min_stop_pct", 0.7)),
                    max_stop_pct=float(signal_cfg.get("tr70_max_stop_pct", 1.8)),
                    tp_rr=float(signal_cfg.get("tr70_tp_rr", 2.1)),
                    min_volume_ratio=float(signal_cfg.get("tr70_min_volume_ratio", 0.8)),
                )

            if not plan:
                continue

            plan.meta.update({"regime": regime_meta})
            _persist_signal(
                db,
                instrument,
                plan,
                ttl_minutes=int(risk_cfg.get("entry_ttl_minutes", 60)),
            )
            generated += 1
            created_for_symbol = True

    return {
        "generated": generated,
        "symbols_checked": len(top_symbols),
        "suppressed_due_active_signal": suppressed_due_active,
    }
