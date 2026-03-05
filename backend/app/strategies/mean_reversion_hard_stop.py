from __future__ import annotations

from app.strategies.indicators import atr, bollinger_bands, ema, rsi
from app.strategies.types import CandleData, SignalPlan


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def generate_mean_reversion_hard_stop_signal(
    candles_5m: list[CandleData],
    *,
    bb_period: int = 20,
    bb_std: float = 2.0,
    rsi_period: int = 14,
    rsi_entry_threshold: float = 30.0,
    safety_ema_period: int = 200,
    lookback_stop: int = 15,
    stop_atr_buffer: float = 0.2,
    max_stop_pct: float = 0.03,
    tp_rr: float = 1.2,
    regime_meta: dict | None = None,
) -> SignalPlan | None:
    min_len = max(bb_period + 2, rsi_period + 3, safety_ema_period + 2, lookback_stop + 2)
    if len(candles_5m) < min_len:
        return None

    closes = [c.close for c in candles_5m]
    highs = [c.high for c in candles_5m]
    lows = [c.low for c in candles_5m]

    _bb_mid, _bb_up, bb_low = bollinger_bands(closes, period=bb_period, std=bb_std)
    rsi_vals = rsi(closes, period=rsi_period)
    ema_guard = ema(closes, period=safety_ema_period)
    atr_vals = atr(highs, lows, closes, period=14)

    setup_idx = -2
    trigger_idx = -1

    setup_close = closes[setup_idx]
    trigger_close = closes[trigger_idx]
    setup_bb_low = bb_low[setup_idx]
    trigger_bb_low = bb_low[trigger_idx]
    setup_rsi = rsi_vals[setup_idx]
    trigger_rsi = rsi_vals[trigger_idx]

    # Safety guard: avoid catching a knife below EMA200 on signal timeframe.
    if setup_close < ema_guard[setup_idx] or trigger_close < ema_guard[trigger_idx]:
        return None

    setup_oversold = setup_close < setup_bb_low or setup_rsi < rsi_entry_threshold
    if not setup_oversold:
        return None

    trigger_back_inside_bb = setup_close <= setup_bb_low and trigger_close > trigger_bb_low
    trigger_rsi_cross_up = setup_rsi < rsi_entry_threshold and trigger_rsi >= rsi_entry_threshold
    if not (trigger_back_inside_bb or trigger_rsi_cross_up):
        return None

    entry = trigger_close
    if entry <= 0:
        return None

    stop_floor = min(lows[-lookback_stop:])
    atr_now = max(0.0, atr_vals[trigger_idx])
    if atr_now > 0:
        stop = stop_floor - stop_atr_buffer * atr_now
        stop_policy = "min_low_lookback_minus_atr_buffer"
    else:
        stop = stop_floor * (1 - 0.001)
        stop_policy = "min_low_lookback_minus_0.1pct"

    risk = entry - stop
    if risk <= 0:
        return None

    stop_pct = risk / entry
    if max_stop_pct > 0 and stop_pct > max_stop_pct:
        return None

    take = entry + tp_rr * risk

    confidence = 0.40
    if min(setup_rsi, trigger_rsi) < 25:
        confidence += 0.20
    bb_distance = max(0.0, setup_bb_low - setup_close)
    if atr_now > 0 and bb_distance > 0.5 * atr_now:
        confidence += 0.20

    slope_1h = float((regime_meta or {}).get("ema200_slope", 0.0))
    if slope_1h > 0:
        confidence += 0.20

    confidence = _clamp(confidence, 0.0, 1.0)

    reason = (
        "MeanReversionHardStop: "
        f"close<BB_low/RSI_oversold setup, RSI={trigger_rsi:.2f}, "
        f"trigger close>BB_low={trigger_back_inside_bb}, RSI_cross_up_30={trigger_rsi_cross_up}, "
        f"SL={stop:.6f}, TP={take:.6f}, regime=OK"
    )

    return SignalPlan(
        strategy="MeanReversionHardStop",
        timeframe="5m",
        signal="long",
        entry=entry,
        stop=stop,
        take=take,
        confidence=confidence,
        reason=reason,
        meta={
            "bb_period": bb_period,
            "bb_std": bb_std,
            "rsi_period": rsi_period,
            "rsi_entry_threshold": rsi_entry_threshold,
            "setup_close": setup_close,
            "setup_bb_low": setup_bb_low,
            "trigger_close": trigger_close,
            "trigger_bb_low": trigger_bb_low,
            "setup_rsi": setup_rsi,
            "trigger_rsi": trigger_rsi,
            "atr_5m": atr_now,
            "lookback_stop": lookback_stop,
            "stop_atr_buffer": stop_atr_buffer,
            "stop_pct": stop_pct,
            "max_stop_pct": max_stop_pct,
            "tp_rr": tp_rr,
            "stop_policy": stop_policy,
            "no_dca": True,
        },
    )
