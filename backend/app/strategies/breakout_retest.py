from __future__ import annotations

from app.strategies.indicators import atr
from app.strategies.types import CandleData, SignalPlan


def generate_breakout_retest_signal(
    candles_5m: list[CandleData],
    lookback: int = 20,
    retest_k_atr: float = 0.3,
    stop_atr_mult: float = 1.0,
    tp_rr: float = 2.0,
    min_volume_ratio: float = 0.0,
    min_confidence: float = 0.0,
) -> SignalPlan | None:
    min_len = max(lookback + 2, 30)
    if len(candles_5m) < min_len:
        return None

    closes = [c.close for c in candles_5m]
    highs = [c.high for c in candles_5m]
    lows = [c.low for c in candles_5m]
    volumes = [c.volume for c in candles_5m]

    current = candles_5m[-1]
    prev_highs = highs[-(lookback + 1) : -1]
    breakout_level = max(prev_highs)

    if current.close <= breakout_level:
        return None

    atr_values = atr(highs, lows, closes, period=14)
    atr_now = max(atr_values[-1], 1e-8)

    avg_vol = sum(volumes[-20:]) / min(20, len(volumes))
    vol_ratio = current.volume / max(avg_vol, 1e-8)
    if vol_ratio < max(0.0, min_volume_ratio):
        return None

    entry = breakout_level - (retest_k_atr * atr_now)
    stop = entry - max(0.1, stop_atr_mult) * atr_now
    take = entry + max(0.1, tp_rr) * (entry - stop)

    vol_expansion = min(2.0, vol_ratio) / 2.0
    distance_from_level = min(1.0, max(0.0, (current.close - breakout_level) / max(atr_now, 1e-8)))
    confidence = max(0.05, min(0.99, 0.55 * vol_expansion + 0.45 * (1 - distance_from_level)))
    if confidence < max(0.0, min(1.0, min_confidence)):
        return None

    reason = (
        f"Breakout above {lookback}-candle high with close confirmation; "
        f"entry retest at {entry:.6f}, stop ATR-based at {stop:.6f}, "
        f"tp_rr={tp_rr:.2f}, vol_ratio={vol_ratio:.2f}"
    )

    one_r = entry - stop
    meta = {
        "breakout_level": breakout_level,
        "atr_5m": atr_now,
        "partial_tp": entry + one_r,
        "final_tp": take,
        "stop_policy": f"entry_minus_{max(0.1, stop_atr_mult):.2f}atr",
        "retest_k_atr": retest_k_atr,
        "lookback": lookback,
        "stop_atr_mult": stop_atr_mult,
        "tp_rr": tp_rr,
        "min_volume_ratio": min_volume_ratio,
        "min_confidence": min_confidence,
        "volume_ratio": vol_ratio,
    }

    return SignalPlan(
        strategy="StrategyBreakoutRetest",
        timeframe="5m",
        signal="long",
        entry=entry,
        stop=stop,
        take=take,
        confidence=confidence,
        reason=reason,
        meta=meta,
    )
