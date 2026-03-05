from __future__ import annotations

from app.strategies.indicators import atr, ema, rsi
from app.strategies.types import CandleData, SignalPlan


def generate_trend_retrace_70_signal(
    candles_5m: list[CandleData],
    *,
    ema_fast_period: int = 20,
    ema_mid_period: int = 50,
    ema_slow_period: int = 200,
    pullback_lookback: int = 10,
    pullback_depth_pct: float = 0.35,
    reclaim_buffer_pct: float = 0.05,
    rsi_period: int = 14,
    rsi_min: float = 42.0,
    rsi_max: float = 62.0,
    stop_atr_mult: float = 0.7,
    min_stop_pct: float = 0.7,
    max_stop_pct: float = 1.8,
    tp_rr: float = 2.1,
    min_volume_ratio: float = 0.8,
) -> SignalPlan | None:
    min_len = max(ema_slow_period + 8, pullback_lookback + 8, rsi_period + 8, 240)
    if len(candles_5m) < min_len:
        return None

    closes = [c.close for c in candles_5m]
    highs = [c.high for c in candles_5m]
    lows = [c.low for c in candles_5m]
    volumes = [c.volume for c in candles_5m]

    ema_fast = ema(closes, ema_fast_period)
    ema_mid = ema(closes, ema_mid_period)
    ema_slow = ema(closes, ema_slow_period)
    rsi_vals = rsi(closes, rsi_period)
    atr_vals = atr(highs, lows, closes, 14)

    cur_close = closes[-1]
    prev_close = closes[-2]
    fast_now = ema_fast[-1]
    fast_prev = ema_fast[-2]
    mid_now = ema_mid[-1]
    slow_now = ema_slow[-1]
    slow_prev = ema_slow[-6]
    rsi_now = rsi_vals[-1]
    atr_now = max(atr_vals[-1], 1e-8)

    trend_ok = (
        cur_close > fast_now > mid_now > slow_now
        and slow_now >= slow_prev
    )
    if not trend_ok:
        return None

    recent_lows = lows[-(pullback_lookback + 1) : -1]
    if not recent_lows:
        return None

    depth_ratio = max(0.0, pullback_depth_pct) / 100.0
    pullback_ok = min(recent_lows) <= fast_now * (1.0 - depth_ratio)
    if not pullback_ok:
        return None

    reclaim_buffer = max(0.0, reclaim_buffer_pct) / 100.0
    reclaim_ok = prev_close <= fast_prev * (1.0 + reclaim_buffer) and cur_close > fast_now * (
        1.0 + reclaim_buffer
    )
    if not reclaim_ok:
        return None

    if not (rsi_min <= rsi_now <= rsi_max):
        return None

    avg_volume = sum(volumes[-20:]) / min(20, len(volumes))
    volume_ratio = volumes[-1] / max(avg_volume, 1e-8)
    if volume_ratio < max(0.0, min_volume_ratio):
        return None

    entry = cur_close
    raw_stop = entry - max(0.1, stop_atr_mult) * atr_now
    stop_pct = ((entry - raw_stop) / max(entry, 1e-8)) * 100.0
    bounded_stop_pct = min(max(stop_pct, max(0.05, min_stop_pct)), max(min_stop_pct, max_stop_pct))
    stop = entry * (1.0 - bounded_stop_pct / 100.0)
    risk = entry - stop
    if risk <= 0:
        return None

    take = entry + max(0.1, tp_rr) * risk

    trend_strength = min(1.0, max(0.0, (fast_now - mid_now) / max(cur_close, 1e-8) * 200.0))
    rsi_center = (rsi_min + rsi_max) / 2.0
    rsi_distance = min(1.0, abs(rsi_now - rsi_center) / max((rsi_max - rsi_min) / 2.0, 1e-8))
    confidence = max(
        0.05,
        min(
            0.98,
            0.45 + 0.35 * trend_strength + 0.2 * min(1.0, volume_ratio / max(min_volume_ratio, 0.5))
            - 0.1 * rsi_distance,
        ),
    )

    reason = (
        "TrendRetrace70: uptrend (EMA20>EMA50>EMA200), pullback to EMA20, reclaim confirmation, "
        f"RSI in range and volume support; stop_pct={bounded_stop_pct:.2f}, tp_rr={tp_rr:.2f}"
    )

    return SignalPlan(
        strategy="StrategyTrendRetrace70",
        timeframe="5m",
        signal="long",
        entry=entry,
        stop=stop,
        take=take,
        confidence=confidence,
        reason=reason,
        meta={
            "ema_fast_period": ema_fast_period,
            "ema_mid_period": ema_mid_period,
            "ema_slow_period": ema_slow_period,
            "pullback_lookback": pullback_lookback,
            "pullback_depth_pct": pullback_depth_pct,
            "reclaim_buffer_pct": reclaim_buffer_pct,
            "rsi_period": rsi_period,
            "rsi_min": rsi_min,
            "rsi_max": rsi_max,
            "rsi_now": rsi_now,
            "atr_5m": atr_now,
            "stop_atr_mult": stop_atr_mult,
            "bounded_stop_pct": bounded_stop_pct,
            "tp_rr": tp_rr,
            "min_volume_ratio": min_volume_ratio,
            "volume_ratio": volume_ratio,
            "trend_strength": trend_strength,
            "no_lookahead": True,
        },
    )
