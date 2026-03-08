from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable

from app.strategies.indicators import atr, ema
from app.strategies.types import CandleData, SignalPlan

DEFAULT_TREND_RETRACE_PARAMS = {
    "tr_pivot_left_right": 3,
    "tr_wave_tf": "15m",
    "tr_min_impulse_atr": 1.5,
    "tr_retrace_target": 0.70,
    "tr_retrace_zone_low": 0.62,
    "tr_retrace_zone_high": 0.78,
    "tr_retrace_tolerance": 0.05,
    "tr_trigger_mode": "ema20",
    "tr_trigger_ema_period": 20,
    "tr_trigger_lookback": 6,
    "tr_stop_lookback": 12,
    "tr_stop_atr_buffer": 0.2,
    "tr_max_stop_pct": 0.04,
    "tr_tp2_rr": 2.0,
    "tr_signal_ttl_minutes": 180,
    "tr_safety_ema_period": 200,
}

LEGACY_TREND_RETRACE_PARAM_ALIASES = {
    "tr70_ema_fast_period": "tr_trigger_ema_period",
    "tr70_ema_slow_period": "tr_safety_ema_period",
    "tr70_pullback_lookback": "tr_stop_lookback",
    "tr70_max_stop_pct": "tr_max_stop_pct",
    "tr70_tp_rr": "tr_tp2_rr",
}


@dataclass(frozen=True)
class PivotPoint:
    index: int
    ts: Any
    kind: str
    price: float


@dataclass(frozen=True)
class ImpulseWave:
    a: PivotPoint
    b: PivotPoint
    atr_at_b: float
    impulse_size: float


def _to_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return parsed


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_candle(item: Any) -> CandleData:
    if isinstance(item, CandleData):
        return item
    if isinstance(item, dict):
        return CandleData(
            ts=item["ts"],
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            volume=float(item.get("volume", 0.0)),
        )
    return CandleData(
        ts=item.ts,
        open=float(item.open),
        high=float(item.high),
        low=float(item.low),
        close=float(item.close),
        volume=float(getattr(item, "volume", 0.0)),
    )


def _normalize_candles(candles: Iterable[Any]) -> list[CandleData]:
    if hasattr(candles, "to_dict") and hasattr(candles, "columns"):
        return [_coerce_candle(item) for item in candles.to_dict("records")]
    return [_coerce_candle(item) for item in candles]


def _normalize_params(context: dict[str, Any] | None, overrides: dict[str, Any]) -> dict[str, Any]:
    params = DEFAULT_TREND_RETRACE_PARAMS.copy()
    raw_params = context.get("params") if isinstance(context, dict) and isinstance(context.get("params"), dict) else {}
    params.update(raw_params)
    params.update({key: value for key, value in overrides.items() if value is not None})
    for legacy_key, canonical_key in LEGACY_TREND_RETRACE_PARAM_ALIASES.items():
        if params.get(canonical_key) is None and params.get(legacy_key) is not None:
            params[canonical_key] = params[legacy_key]
    return params


def _regime_state_ok(context: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    regime_state = context.get("regime_state") if isinstance(context, dict) else None
    if regime_state is None:
        return True, {}
    if not isinstance(regime_state, dict):
        return False, {"reason": "invalid_regime_state"}
    if isinstance(regime_state.get("ok"), bool):
        return bool(regime_state["ok"]), regime_state
    if regime_state.get("reason") == "regime_not_ok":
        return False, regime_state
    for required_key in ("close_1h", "ema200_1h", "ema200_slope", "atr_pct_1h"):
        if required_key not in regime_state:
            return False, {"reason": "incomplete_regime_state", **regime_state}
    return True, regime_state


def _aggregate_candles_to_15m(candles_5m: list[CandleData]) -> list[CandleData]:
    groups: dict[Any, list[CandleData]] = {}
    order: list[Any] = []
    for candle in candles_5m:
        ts = candle.ts
        bucket = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
        if bucket not in groups:
            groups[bucket] = []
            order.append(bucket)
        groups[bucket].append(candle)

    aggregated: list[CandleData] = []
    for bucket in order:
        chunk = groups[bucket]
        if len(chunk) < 3:
            continue
        aggregated.append(
            CandleData(
                ts=bucket,
                open=chunk[0].open,
                high=max(item.high for item in chunk),
                low=min(item.low for item in chunk),
                close=chunk[-1].close,
                volume=sum(item.volume for item in chunk),
            )
        )
    return aggregated


def find_confirmed_pivots(candles: Iterable[Any], left_right: int = 3) -> list[PivotPoint]:
    normalized = _normalize_candles(candles)
    if left_right < 1 or len(normalized) < (left_right * 2) + 1:
        return []

    pivots: list[PivotPoint] = []
    highs = [candle.high for candle in normalized]
    lows = [candle.low for candle in normalized]

    for idx in range(left_right, len(normalized) - left_right):
        high_window = highs[idx - left_right : idx + left_right + 1]
        low_window = lows[idx - left_right : idx + left_right + 1]
        if highs[idx] == max(high_window):
            pivots.append(PivotPoint(index=idx, ts=normalized[idx].ts, kind="high", price=highs[idx]))
        if lows[idx] == min(low_window):
            pivots.append(PivotPoint(index=idx, ts=normalized[idx].ts, kind="low", price=lows[idx]))

    pivots.sort(key=lambda pivot: (pivot.index, 0 if pivot.kind == "low" else 1))
    return pivots


def find_latest_impulse_wave(
    candles_15m: Iterable[Any],
    *,
    left_right: int = 3,
    min_impulse_atr: float = 1.5,
) -> tuple[ImpulseWave | None, list[PivotPoint]]:
    normalized = _normalize_candles(candles_15m)
    pivots = find_confirmed_pivots(normalized, left_right=left_right)
    if len(normalized) < 15 or len(pivots) < 2:
        return None, pivots

    highs = [candle.high for candle in normalized]
    lows = [candle.low for candle in normalized]
    closes = [candle.close for candle in normalized]
    atr_values = atr(highs, lows, closes, period=14)

    for pivot_idx in range(len(pivots) - 2, -1, -1):
        pivot_a = pivots[pivot_idx]
        if pivot_a.kind != "low":
            continue
        pivot_b = next((pivot for pivot in pivots[pivot_idx + 1 :] if pivot.kind == "high"), None)
        if pivot_b is None:
            continue
        impulse_size = pivot_b.price - pivot_a.price
        atr_at_b = atr_values[pivot_b.index] if pivot_b.index < len(atr_values) else 0.0
        if impulse_size < max(0.0, min_impulse_atr) * max(atr_at_b, 1e-8):
            continue
        return ImpulseWave(a=pivot_a, b=pivot_b, atr_at_b=atr_at_b, impulse_size=impulse_size), pivots

    return None, pivots


def _find_retrace_pivot_low_after_b(pivots: list[PivotPoint], wave: ImpulseWave) -> PivotPoint | None:
    candidates = [pivot for pivot in pivots if pivot.kind == "low" and pivot.index > wave.b.index]
    return candidates[-1] if candidates else None


def _instrument_symbol(context: dict[str, Any] | None) -> str | None:
    instrument = context.get("instrument") if isinstance(context, dict) else None
    if isinstance(instrument, dict):
        return str(instrument.get("symbol") or "").strip() or None
    return str(getattr(instrument, "symbol", "")).strip() or None


def _trigger_state(
    *,
    candles_5m: list[CandleData],
    closes: list[float],
    highs: list[float],
    trigger_mode: str,
    trigger_ema_period: int,
    trigger_lookback: int,
) -> tuple[bool, str, float | None]:
    current_close = closes[-1]
    previous_close = closes[-2]

    if trigger_mode == "break_high":
        recent_high = max(highs[-(trigger_lookback + 1) : -1])
        return current_close > recent_high and previous_close <= recent_high, "break_high", recent_high

    trigger_ema = ema(closes, trigger_ema_period)
    current_ema = trigger_ema[-1]
    previous_ema = trigger_ema[-2]
    return current_close > current_ema and previous_close <= previous_ema, "ema20", current_ema


def _confidence(
    *,
    retrace: float,
    impulse_size: float,
    atr_15m_at_b: float,
    regime_meta: dict[str, Any],
) -> float:
    confidence = 0.6
    if abs(retrace - 0.70) < 0.03:
        confidence += 0.1
    if impulse_size > 2.0 * max(atr_15m_at_b, 1e-8):
        confidence += 0.1

    raw_strength = regime_meta.get("strength")
    if isinstance(raw_strength, (int, float)):
        if float(raw_strength) >= 0.6:
            confidence += 0.1
    else:
        slope = _to_float(regime_meta.get("ema200_slope"), 0.0)
        close_1h = _to_float(regime_meta.get("close_1h"), 0.0)
        ema200_1h = _to_float(regime_meta.get("ema200_1h"), 0.0)
        if slope > 0 and close_1h > ema200_1h * 1.01:
            confidence += 0.1

    return max(0.0, min(1.0, confidence))


def generate_trend_retrace_70_signal(
    candles_5m: Iterable[Any],
    context: dict[str, Any] | None = None,
    **legacy_kwargs: Any,
) -> SignalPlan | None:
    candles = _normalize_candles(candles_5m)
    if not candles:
        return None

    regime_ok, regime_meta = _regime_state_ok(context)
    if not regime_ok:
        return None

    params = _normalize_params(context, legacy_kwargs)
    left_right = max(1, _to_int(params.get("tr_pivot_left_right"), DEFAULT_TREND_RETRACE_PARAMS["tr_pivot_left_right"]))
    wave_tf = str(params.get("tr_wave_tf") or DEFAULT_TREND_RETRACE_PARAMS["tr_wave_tf"]).strip().lower()
    min_impulse_atr = max(0.1, _to_float(params.get("tr_min_impulse_atr"), DEFAULT_TREND_RETRACE_PARAMS["tr_min_impulse_atr"]))
    retrace_target = _to_float(params.get("tr_retrace_target"), DEFAULT_TREND_RETRACE_PARAMS["tr_retrace_target"])
    retrace_zone_low = _to_float(params.get("tr_retrace_zone_low"), DEFAULT_TREND_RETRACE_PARAMS["tr_retrace_zone_low"])
    retrace_zone_high = _to_float(params.get("tr_retrace_zone_high"), DEFAULT_TREND_RETRACE_PARAMS["tr_retrace_zone_high"])
    retrace_tolerance = max(0.0, _to_float(params.get("tr_retrace_tolerance"), DEFAULT_TREND_RETRACE_PARAMS["tr_retrace_tolerance"]))
    trigger_mode = str(params.get("tr_trigger_mode") or DEFAULT_TREND_RETRACE_PARAMS["tr_trigger_mode"]).strip().lower()
    trigger_ema_period = max(2, _to_int(params.get("tr_trigger_ema_period"), DEFAULT_TREND_RETRACE_PARAMS["tr_trigger_ema_period"]))
    trigger_lookback = max(2, _to_int(params.get("tr_trigger_lookback"), DEFAULT_TREND_RETRACE_PARAMS["tr_trigger_lookback"]))
    stop_lookback = max(2, _to_int(params.get("tr_stop_lookback"), DEFAULT_TREND_RETRACE_PARAMS["tr_stop_lookback"]))
    stop_atr_buffer = max(0.0, _to_float(params.get("tr_stop_atr_buffer"), DEFAULT_TREND_RETRACE_PARAMS["tr_stop_atr_buffer"]))
    max_stop_pct = max(0.0, _to_float(params.get("tr_max_stop_pct"), DEFAULT_TREND_RETRACE_PARAMS["tr_max_stop_pct"]))
    tp2_rr = max(1.0, _to_float(params.get("tr_tp2_rr"), DEFAULT_TREND_RETRACE_PARAMS["tr_tp2_rr"]))
    ttl_minutes = max(1, _to_int(params.get("tr_signal_ttl_minutes"), DEFAULT_TREND_RETRACE_PARAMS["tr_signal_ttl_minutes"]))
    safety_ema_period = max(2, _to_int(params.get("tr_safety_ema_period"), DEFAULT_TREND_RETRACE_PARAMS["tr_safety_ema_period"]))

    min_len = max(safety_ema_period + 3, trigger_ema_period + 3, stop_lookback + 3, 180)
    if len(candles) < min_len:
        return None

    closes = [candle.close for candle in candles]
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    atr_5m_values = atr(highs, lows, closes, period=14)
    safety_ema = ema(closes, safety_ema_period)

    current = candles[-1]
    current_close = closes[-1]
    if current_close < safety_ema[-1]:
        return None

    provided_15m = context.get("candles_15m") if isinstance(context, dict) else None
    candles_15m = _normalize_candles(provided_15m) if provided_15m else []
    if wave_tf != "15m" or not candles_15m:
        candles_15m = _aggregate_candles_to_15m(candles)

    wave, pivots = find_latest_impulse_wave(
        candles_15m,
        left_right=left_right,
        min_impulse_atr=min_impulse_atr,
    )
    if wave is None:
        return None

    wave_range = wave.b.price - wave.a.price
    if wave_range <= 0:
        return None

    retrace = (wave.b.price - current_close) / wave_range
    in_zone = retrace_zone_low <= retrace <= retrace_zone_high
    near_target = abs(retrace - retrace_target) <= retrace_tolerance
    if not (in_zone or near_target):
        return None

    trigger_ok, trigger_label, trigger_level = _trigger_state(
        candles_5m=candles,
        closes=closes,
        highs=highs,
        trigger_mode=trigger_mode,
        trigger_ema_period=trigger_ema_period,
        trigger_lookback=trigger_lookback,
    )
    if not trigger_ok:
        return None

    entry = current_close
    atr_5m_now = max(float(atr_5m_values[-1]), 0.0)
    retrace_pivot = _find_retrace_pivot_low_after_b(pivots, wave)
    stop_anchor = retrace_pivot.price if retrace_pivot is not None else min(lows[-stop_lookback:])
    stop = stop_anchor - (stop_atr_buffer * atr_5m_now)
    risk = entry - stop
    if risk <= 0:
        return None

    stop_pct = risk / max(entry, 1e-8)
    if max_stop_pct > 0 and stop_pct > max_stop_pct:
        return None

    tp1_rr_target = entry + risk
    tp1 = min(tp1_rr_target, wave.b.price)
    tp2_rr_target = entry + (tp2_rr * risk)
    tp2 = tp2_rr_target if wave.b.price > entry + (3.0 * risk) else max(tp2_rr_target, wave.b.price)
    if tp2 <= entry:
        return None

    confidence = _confidence(
        retrace=retrace,
        impulse_size=wave.impulse_size,
        atr_15m_at_b=wave.atr_at_b,
        regime_meta=regime_meta,
    )
    symbol = _instrument_symbol(context)
    created_at = current.ts
    expires_at = created_at + timedelta(minutes=ttl_minutes)

    pivot_meta = [
        {
            "index": pivot.index,
            "ts": pivot.ts.isoformat() if hasattr(pivot.ts, "isoformat") else str(pivot.ts),
            "kind": pivot.kind,
            "price": pivot.price,
        }
        for pivot in pivots[-8:]
    ]
    reason = (
        f"TrendRetrace70: A={wave.a.price:.6f}, B={wave.b.price:.6f}, retrace={retrace:.3f}, "
        f"trigger={trigger_label}, entry={entry:.6f}, SL={stop:.6f}, TP=[{tp1:.6f},{tp2:.6f}], regime=OK"
    )

    return SignalPlan(
        symbol=symbol,
        strategy="StrategyTrendRetrace70",
        timeframe="5m",
        signal="long",
        entry=entry,
        stop=stop,
        take=tp2,
        takes=[tp1, tp2],
        confidence=confidence,
        reason=reason,
        created_at=created_at,
        expires_at=expires_at,
        status="active",
        meta={
            "symbol": symbol,
            "take": [tp1, tp2],
            "A": {
                "index": wave.a.index,
                "ts": wave.a.ts.isoformat() if hasattr(wave.a.ts, "isoformat") else str(wave.a.ts),
                "price": wave.a.price,
            },
            "B": {
                "index": wave.b.index,
                "ts": wave.b.ts.isoformat() if hasattr(wave.b.ts, "isoformat") else str(wave.b.ts),
                "price": wave.b.price,
            },
            "retrace": retrace,
            "wave_tf": wave_tf,
            "impulse_size": wave.impulse_size,
            "atr_15m_at_b": wave.atr_at_b,
            "pivots": pivot_meta,
            "retrace_pivot_low": (
                {
                    "index": retrace_pivot.index,
                    "ts": retrace_pivot.ts.isoformat() if hasattr(retrace_pivot.ts, "isoformat") else str(retrace_pivot.ts),
                    "price": retrace_pivot.price,
                }
                if retrace_pivot is not None
                else None
            ),
            "trigger": {
                "mode": trigger_label,
                "level": trigger_level,
            },
            "atr_5m": atr_5m_now,
            "tr_pivot_left_right": left_right,
            "tr_min_impulse_atr": min_impulse_atr,
            "tr_retrace_target": retrace_target,
            "tr_retrace_zone_low": retrace_zone_low,
            "tr_retrace_zone_high": retrace_zone_high,
            "tr_retrace_tolerance": retrace_tolerance,
            "tr_trigger_mode": trigger_mode,
            "tr_trigger_ema_period": trigger_ema_period,
            "tr_trigger_lookback": trigger_lookback,
            "tr_stop_lookback": stop_lookback,
            "tr_stop_atr_buffer": stop_atr_buffer,
            "tr_max_stop_pct": max_stop_pct,
            "tr_tp2_rr": tp2_rr,
            "tr_signal_ttl_minutes": ttl_minutes,
            "tr_safety_ema_period": safety_ema_period,
            "stop_pct": stop_pct,
            "no_dca": True,
            "regime": regime_meta,
            "signal_on_close_only": True,
        },
    )
