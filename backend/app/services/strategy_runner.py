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
from app.strategies.pullback_trend import generate_pullback_signal
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

    strategy_params = setting.strategy_params_json
    risk_params = setting.risk_params_json

    generated = 0
    suppressed_due_active = 0

    for symbol in top_symbols:
        instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if not instrument:
            continue

        candles_5m = _load_candles(db, instrument.id, "5m", 400)
        candles_1h = _load_candles(db, instrument.id, "1h", 260)
        candles_15m = _load_candles(db, instrument.id, "15m", 120)

        regime_ok, regime_meta = _regime_filter(
            candles_1h,
            atr_threshold_pct=float(strategy_params.get("atr_threshold_pct_1h", 4.0)),
        )
        if not regime_ok:
            continue

        if strategy_params.get("confirm_15m", False):
            conf_ok, conf_meta = _confirm_15m(candles_15m)
            if not conf_ok:
                continue
            regime_meta.update(conf_meta)

        # One active signal per symbol across all strategies to avoid conflicting entries.
        if _has_active_signal(db, instrument.id):
            suppressed_due_active += 1
            continue

        only_strategy = strategy_params.get("trade_only_strategy", "both")
        created_for_symbol = False

        if only_strategy in ("both", "StrategyBreakoutRetest", "breakout") and not created_for_symbol:
            if not _signal_exists(db, instrument.id, "StrategyBreakoutRetest"):
                breakout_signal = generate_breakout_retest_signal(
                    candles_5m=candles_5m,
                    lookback=int(strategy_params.get("breakout_lookback", 20)),
                    retest_k_atr=float(strategy_params.get("breakout_retest_k_atr", 0.3)),
                )
                if breakout_signal:
                    breakout_signal.meta.update({"regime": regime_meta})
                    _persist_signal(
                        db,
                        instrument,
                        breakout_signal,
                        ttl_minutes=int(risk_params.get("entry_ttl_minutes", 60)),
                    )
                    generated += 1
                    created_for_symbol = True

        if only_strategy in ("both", "StrategyPullbackToTrend", "pullback") and not created_for_symbol:
            if not _signal_exists(db, instrument.id, "StrategyPullbackToTrend"):
                pullback_signal = generate_pullback_signal(
                    candles_5m=candles_5m,
                    rsi_threshold=float(strategy_params.get("pullback_rsi_threshold", 45.0)),
                )
                if pullback_signal:
                    pullback_signal.meta.update({"regime": regime_meta})
                    _persist_signal(
                        db,
                        instrument,
                        pullback_signal,
                        ttl_minutes=int(risk_params.get("entry_ttl_minutes", 60)),
                    )
                    generated += 1
                    created_for_symbol = True

        if only_strategy in ("both", "MeanReversionHardStop", "mean_reversion") and not created_for_symbol:
            if not _signal_exists(db, instrument.id, "MeanReversionHardStop"):
                mean_signal = generate_mean_reversion_hard_stop_signal(
                    candles_5m=candles_5m,
                    bb_period=int(strategy_params.get("mr_bb_period", 20)),
                    bb_std=float(strategy_params.get("mr_bb_std", 2.0)),
                    rsi_period=int(strategy_params.get("mr_rsi_period", 14)),
                    rsi_entry_threshold=float(strategy_params.get("mr_rsi_entry_threshold", 30.0)),
                    safety_ema_period=int(strategy_params.get("mr_safety_ema_period", 200)),
                    lookback_stop=int(strategy_params.get("mr_lookback_stop", 15)),
                    stop_atr_buffer=float(strategy_params.get("mr_stop_atr_buffer", 0.2)),
                    max_stop_pct=float(strategy_params.get("mr_max_stop_pct", 0.03)),
                    tp_rr=float(strategy_params.get("mr_tp_rr", 1.2)),
                    regime_meta=regime_meta,
                )
                if mean_signal:
                    mean_signal.meta.update({"regime": regime_meta})
                    _persist_signal(
                        db,
                        instrument,
                        mean_signal,
                        ttl_minutes=int(risk_params.get("entry_ttl_minutes", 60)),
                    )
                    generated += 1
                    created_for_symbol = True

    return {
        "generated": generated,
        "symbols_checked": len(top_symbols),
        "suppressed_due_active_signal": suppressed_due_active,
    }
