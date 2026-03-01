from __future__ import annotations

from app.strategies.indicators import ema, rsi
from app.strategies.types import CandleData, SignalPlan


def generate_pullback_signal(candles_5m: list[CandleData], rsi_threshold: float = 45.0) -> SignalPlan | None:
    if len(candles_5m) < 80:
        return None

    closes = [c.close for c in candles_5m]
    lows = [c.low for c in candles_5m]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    rsi14 = rsi(closes, 14)

    prev_close = closes[-2]
    cur_close = closes[-1]
    prev_ema20 = ema20[-2]
    cur_ema20 = ema20[-1]
    cur_ema50 = ema50[-1]

    recent_pullback_close = min(closes[-5:])
    pullback_ok = recent_pullback_close <= cur_ema50 * 1.01 and rsi14[-1] < rsi_threshold
    reclaim_ok = prev_close <= prev_ema20 * 1.005 and cur_close > cur_ema20

    if not (pullback_ok and reclaim_ok):
        return None

    entry = min(cur_ema20, cur_close)
    stop = min(lows[-10:])
    risk = entry - stop
    if risk <= 0:
        return None
    take = entry + 1.2 * risk

    trend_strength = max(0.0, min(1.0, (cur_ema20 - cur_ema50) / max(cur_close, 1e-8) * 100))
    rsi_factor = max(0.0, min(1.0, (50 - rsi14[-1]) / 20))
    confidence = max(0.05, min(0.95, 0.5 + 0.3 * trend_strength + 0.2 * rsi_factor))

    reason = (
        "Pullback to EMA50 with RSI weakness and close reclaim above EMA20. "
        "Entry near EMA20, stop below pullback low."
    )

    return SignalPlan(
        strategy="StrategyPullbackToTrend",
        timeframe="5m",
        signal="long",
        entry=entry,
        stop=stop,
        take=take,
        confidence=confidence,
        reason=reason,
        meta={
            "ema20": cur_ema20,
            "ema50": cur_ema50,
            "rsi14": rsi14[-1],
            "stop_policy": "pullback_low",
        },
    )
