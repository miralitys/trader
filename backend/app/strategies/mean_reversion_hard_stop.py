from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

from app.strategies.indicators import atr, bollinger_bands, ema, rsi
from app.strategies.types import CandleData, SignalPlan

DEFAULT_MEAN_REVERSION_PARAMS = {
    "mr_bb_period": 20,
    "mr_bb_std": 2.0,
    "mr_rsi_period": 14,
    "mr_rsi_entry_threshold": 30.0,
    "mr_safety_ema_period": 200,
    "mr_lookback_stop": 15,
    "mr_stop_atr_buffer": 0.2,
    "mr_max_stop_pct": 0.03,
    "mr_tp_rr": 1.2,
    "mr_signal_ttl_minutes": 60,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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


def _regime_state_ok(context: dict[str, Any] | None, legacy_regime_meta: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    if isinstance(context, dict) and isinstance(context.get("regime_state"), dict):
        regime_state = context["regime_state"]
    elif isinstance(legacy_regime_meta, dict):
        regime_state = {"ok": True, **legacy_regime_meta}
    else:
        return True, {}

    if isinstance(regime_state.get("ok"), bool):
        return bool(regime_state["ok"]), regime_state
    if regime_state.get("reason") == "regime_not_ok":
        return False, regime_state
    for required_key in ("close_1h", "ema200_1h", "ema200_slope", "atr_pct_1h"):
        if required_key not in regime_state and required_key != "close_1h":
            return False, {"reason": "incomplete_regime_state", **regime_state}
    return True, regime_state


def _params_from_context(context: dict[str, Any] | None, overrides: dict[str, Any]) -> dict[str, Any]:
    params = DEFAULT_MEAN_REVERSION_PARAMS.copy()
    if isinstance(context, dict) and isinstance(context.get("params"), dict):
        params.update(context["params"])
    params.update({key: value for key, value in overrides.items() if value is not None})
    return params


def generate_mean_reversion_hard_stop_signal(
    candles_5m: Iterable[Any],
    context: dict[str, Any] | None = None,
    *,
    regime_meta: dict | None = None,
    bb_period: int | None = None,
    bb_std: float | None = None,
    rsi_period: int | None = None,
    rsi_entry_threshold: float | None = None,
    safety_ema_period: int | None = None,
    lookback_stop: int | None = None,
    stop_atr_buffer: float | None = None,
    max_stop_pct: float | None = None,
    tp_rr: float | None = None,
    mr_signal_ttl_minutes: int | None = None,
) -> SignalPlan | None:
    candles = _normalize_candles(candles_5m)
    params = _params_from_context(
        context,
        {
            "mr_bb_period": bb_period,
            "mr_bb_std": bb_std,
            "mr_rsi_period": rsi_period,
            "mr_rsi_entry_threshold": rsi_entry_threshold,
            "mr_safety_ema_period": safety_ema_period,
            "mr_lookback_stop": lookback_stop,
            "mr_stop_atr_buffer": stop_atr_buffer,
            "mr_max_stop_pct": max_stop_pct,
            "mr_tp_rr": tp_rr,
            "mr_signal_ttl_minutes": mr_signal_ttl_minutes,
        },
    )

    regime_ok, regime = _regime_state_ok(context, regime_meta)
    if not regime_ok:
        return None

    bb_period_v = max(2, _to_int(params.get("mr_bb_period"), DEFAULT_MEAN_REVERSION_PARAMS["mr_bb_period"]))
    bb_std_v = max(0.1, _to_float(params.get("mr_bb_std"), DEFAULT_MEAN_REVERSION_PARAMS["mr_bb_std"]))
    rsi_period_v = max(2, _to_int(params.get("mr_rsi_period"), DEFAULT_MEAN_REVERSION_PARAMS["mr_rsi_period"]))
    rsi_threshold_v = _to_float(
        params.get("mr_rsi_entry_threshold"),
        DEFAULT_MEAN_REVERSION_PARAMS["mr_rsi_entry_threshold"],
    )
    safety_ema_period_v = max(
        2,
        _to_int(params.get("mr_safety_ema_period"), DEFAULT_MEAN_REVERSION_PARAMS["mr_safety_ema_period"]),
    )
    lookback_stop_v = max(
        2,
        _to_int(params.get("mr_lookback_stop"), DEFAULT_MEAN_REVERSION_PARAMS["mr_lookback_stop"]),
    )
    stop_atr_buffer_v = max(
        0.0,
        _to_float(params.get("mr_stop_atr_buffer"), DEFAULT_MEAN_REVERSION_PARAMS["mr_stop_atr_buffer"]),
    )
    max_stop_pct_v = max(
        0.0,
        _to_float(params.get("mr_max_stop_pct"), DEFAULT_MEAN_REVERSION_PARAMS["mr_max_stop_pct"]),
    )
    tp_rr_v = max(0.1, _to_float(params.get("mr_tp_rr"), DEFAULT_MEAN_REVERSION_PARAMS["mr_tp_rr"]))
    ttl_minutes_v = max(
        1,
        _to_int(params.get("mr_signal_ttl_minutes"), DEFAULT_MEAN_REVERSION_PARAMS["mr_signal_ttl_minutes"]),
    )

    min_len = max(bb_period_v + 2, rsi_period_v + 3, safety_ema_period_v + 2, lookback_stop_v + 2)
    if len(candles) < min_len:
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    _, _, bb_low = bollinger_bands(closes, period=bb_period_v, std=bb_std_v)
    rsi_vals = rsi(closes, period=rsi_period_v)
    ema_guard = ema(closes, period=safety_ema_period_v)
    atr_vals = atr(highs, lows, closes, period=14)

    setup_idx = -2
    trigger_idx = -1

    setup_close = closes[setup_idx]
    trigger_close = closes[trigger_idx]
    setup_bb_low = bb_low[setup_idx]
    trigger_bb_low = bb_low[trigger_idx]
    setup_rsi = rsi_vals[setup_idx]
    trigger_rsi = rsi_vals[trigger_idx]
    setup_ema = ema_guard[setup_idx]
    trigger_ema = ema_guard[trigger_idx]

    if setup_close < setup_ema or trigger_close < trigger_ema:
        return None

    setup_oversold = setup_close < setup_bb_low or setup_rsi < rsi_threshold_v
    if not setup_oversold:
        return None

    trigger_back_inside_bb = setup_close <= setup_bb_low and trigger_close > trigger_bb_low
    trigger_rsi_cross_up = setup_rsi < rsi_threshold_v and trigger_rsi >= rsi_threshold_v
    if not (trigger_back_inside_bb or trigger_rsi_cross_up):
        return None

    entry = trigger_close
    if entry <= 0:
        return None

    atr_now = max(0.0, atr_vals[trigger_idx])
    stop_floor = min(lows[-lookback_stop_v:])
    stop = stop_floor - stop_atr_buffer_v * atr_now
    risk = entry - stop
    if risk <= 0:
        return None

    stop_pct = risk / entry
    if max_stop_pct_v > 0 and stop_pct > max_stop_pct_v:
        return None

    take = entry + tp_rr_v * risk

    bounce_score = 1.0 if trigger_back_inside_bb else 0.6
    rsi_score = max(0.0, min(1.0, (rsi_threshold_v - min(setup_rsi, trigger_rsi)) / max(rsi_threshold_v, 1.0)))
    slope_score = 1.0 if float(regime.get("ema200_slope", 0.0)) > 0 else 0.4
    confidence = _clamp(0.35 + 0.25 * bounce_score + 0.25 * rsi_score + 0.15 * slope_score, 0.0, 1.0)

    instrument = context.get("instrument") if isinstance(context, dict) else None
    if isinstance(instrument, dict):
        symbol = str(instrument.get("symbol") or "").strip() or None
    else:
        symbol = str(getattr(instrument, "symbol", "")).strip() or None

    created_at = candles[trigger_idx].ts
    expires_at = created_at + timedelta(minutes=ttl_minutes_v)
    reason = (
        "MeanReversionHardStop "
        f"setup_close={setup_close:.6f} bb_low={setup_bb_low:.6f} setup_rsi={setup_rsi:.2f} "
        f"trigger_close={trigger_close:.6f} trigger_rsi={trigger_rsi:.2f} "
        f"stop={stop:.6f} tp={take:.6f}"
    )

    return SignalPlan(
        symbol=symbol,
        strategy="MeanReversionHardStop",
        timeframe="5m",
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
            "take": [take],
            "bb_period": bb_period_v,
            "bb_std": bb_std_v,
            "rsi_period": rsi_period_v,
            "rsi_entry_threshold": rsi_threshold_v,
            "safety_ema_period": safety_ema_period_v,
            "setup_close": setup_close,
            "setup_bb_low": setup_bb_low,
            "trigger_close": trigger_close,
            "trigger_bb_low": trigger_bb_low,
            "setup_rsi": setup_rsi,
            "trigger_rsi": trigger_rsi,
            "atr_5m": atr_now,
            "lookback_stop": lookback_stop_v,
            "stop_atr_buffer": stop_atr_buffer_v,
            "stop_pct": stop_pct,
            "max_stop_pct": max_stop_pct_v,
            "tp_rr": tp_rr_v,
            "no_dca": True,
            "regime": regime,
            "signal_on_close_only": True,
        },
    )
