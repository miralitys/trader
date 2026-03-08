from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

from app.strategies.indicators import atr, ema, rsi
from app.strategies.types import CandleData, SignalPlan

DEFAULT_PULLBACK_PARAMS = {
    "pt_ema_fast": 20,
    "pt_ema_slow": 50,
    "pt_rsi_period": 14,
    "pt_rsi_threshold": 45,
    "pt_stop_lookback": 10,
    "pt_tp_rr": 1.2,
    "pt_signal_ttl_minutes": 60,
}

LEGACY_PULLBACK_PARAM_ALIASES = {
    "pullback_rsi_threshold": "pt_rsi_threshold",
}


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


def _normalize_candles(candles_5m: Iterable[Any]) -> list[CandleData]:
    if hasattr(candles_5m, "to_dict") and hasattr(candles_5m, "columns"):
        return [_coerce_candle(item) for item in candles_5m.to_dict("records")]
    return [_coerce_candle(item) for item in candles_5m]


def _normalize_params(context: dict[str, Any] | None, overrides: dict[str, Any]) -> dict[str, Any]:
    params = DEFAULT_PULLBACK_PARAMS.copy()
    raw_params = context.get("params") if isinstance(context, dict) and isinstance(context.get("params"), dict) else {}
    params.update(raw_params)
    params.update({key: value for key, value in overrides.items() if value is not None})

    for legacy_key, canonical_key in LEGACY_PULLBACK_PARAM_ALIASES.items():
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


def generate_pullback_to_trend_signal(
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
    ema_fast_period = max(2, _to_int(params.get("pt_ema_fast"), DEFAULT_PULLBACK_PARAMS["pt_ema_fast"]))
    ema_slow_period = max(ema_fast_period + 1, _to_int(params.get("pt_ema_slow"), DEFAULT_PULLBACK_PARAMS["pt_ema_slow"]))
    rsi_period = max(2, _to_int(params.get("pt_rsi_period"), DEFAULT_PULLBACK_PARAMS["pt_rsi_period"]))
    rsi_threshold = _to_float(params.get("pt_rsi_threshold"), DEFAULT_PULLBACK_PARAMS["pt_rsi_threshold"])
    stop_lookback = max(2, _to_int(params.get("pt_stop_lookback"), DEFAULT_PULLBACK_PARAMS["pt_stop_lookback"]))
    tp_rr = max(0.1, _to_float(params.get("pt_tp_rr"), DEFAULT_PULLBACK_PARAMS["pt_tp_rr"]))
    ttl_minutes = max(1, _to_int(params.get("pt_signal_ttl_minutes"), DEFAULT_PULLBACK_PARAMS["pt_signal_ttl_minutes"]))

    min_len = max(ema_slow_period + 3, rsi_period + 3, stop_lookback + 2)
    if len(candles) < min_len:
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    ema_fast = ema(closes, ema_fast_period)
    ema_slow = ema(closes, ema_slow_period)
    rsi_values = rsi(closes, rsi_period)
    atr_values = atr(highs, lows, closes, period=min(14, max(2, rsi_period)))

    current = candles[-1]
    prev_close = closes[-2]
    current_close = closes[-1]
    current_ema_fast = ema_fast[-1]
    previous_ema_fast = ema_fast[-2]
    current_ema_slow = ema_slow[-1]
    current_rsi = rsi_values[-1]

    setup_closes = closes[-(stop_lookback + 1) : -1]
    setup_lows = lows[-(stop_lookback + 1) : -1]
    setup_highs = highs[-(stop_lookback + 1) : -1]
    setup_ema_slow = ema_slow[-(stop_lookback + 1) : -1]
    setup_rsi = rsi_values[-(stop_lookback + 1) : -1]
    if not setup_closes or not setup_ema_slow or not setup_rsi:
        return None

    distance_to_slow = min(
        abs(close - slow_value) / max(slow_value, 1e-8)
        for close, slow_value in zip(setup_closes, setup_ema_slow)
    )
    touched_ema_slow = any(
        low <= slow_value <= high
        for low, high, slow_value in zip(setup_lows, setup_highs, setup_ema_slow)
    )
    approached_ema_slow = distance_to_slow <= 0.01
    weakest_rsi = min(setup_rsi)
    pullback_ok = (touched_ema_slow or approached_ema_slow) and weakest_rsi < rsi_threshold
    trigger_ok = prev_close <= previous_ema_fast and current_close > current_ema_fast

    if not (pullback_ok and trigger_ok):
        return None

    entry = current_close
    atr_now = max(float(atr_values[-1]), 0.0)
    stop_floor = min(lows[-stop_lookback:])
    stop_buffer = max(entry * 0.001, atr_now * 0.2)
    stop = stop_floor - stop_buffer
    risk = entry - stop
    if risk <= 0:
        return None

    take = entry + tp_rr * risk

    closeness_score = max(0.0, min(1.0, 1.0 - (distance_to_slow / 0.0035))) if approached_ema_slow else 1.0
    rsi_score = max(0.0, min(1.0, (rsi_threshold - weakest_rsi) / max(rsi_threshold, 1.0)))
    confidence = max(0.05, min(0.99, 0.55 + 0.25 * closeness_score + 0.20 * rsi_score))

    instrument = context.get("instrument") if isinstance(context, dict) else None
    if isinstance(instrument, dict):
        symbol = str(instrument.get("symbol") or "").strip() or None
    else:
        symbol = str(getattr(instrument, "symbol", "")).strip() or None

    created_at = current.ts
    expires_at = created_at + timedelta(minutes=ttl_minutes)
    reason = (
        f"pullback_to_trend ema_fast={current_ema_fast:.6f} ema_slow={current_ema_slow:.6f} "
        f"rsi={current_rsi:.2f} entry={entry:.6f} stop={stop:.6f} tp={take:.6f}"
    )

    return SignalPlan(
        symbol=symbol,
        timeframe="5m",
        strategy="StrategyPullbackToTrend",
        signal="long",
        entry=entry,
        stop=stop,
        take=take,
        takes=[take],
        confidence=confidence,
        reason=reason,
        created_at=created_at,
        expires_at=expires_at,
        status="active",
        meta={
            "symbol": symbol,
            "ema_fast": current_ema_fast,
            "ema_slow": current_ema_slow,
            "rsi": current_rsi,
            "setup_rsi_min": weakest_rsi,
            "atr_5m": atr_now,
            "take": [take],
            "pt_ema_fast": ema_fast_period,
            "pt_ema_slow": ema_slow_period,
            "pt_rsi_period": rsi_period,
            "pt_rsi_threshold": rsi_threshold,
            "pt_stop_lookback": stop_lookback,
            "pt_tp_rr": tp_rr,
            "pt_signal_ttl_minutes": ttl_minutes,
            "stop_buffer": stop_buffer,
            "regime": regime_meta,
            "signal_on_close_only": True,
        },
    )
