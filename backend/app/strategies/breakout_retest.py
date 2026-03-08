from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

from app.strategies.indicators import atr
from app.strategies.types import CandleData, SignalPlan

DEFAULT_BREAKOUT_PARAMS = {
    "br_lookback_n": 20,
    "br_atr_period": 14,
    "br_retest_atr_k": 0.3,
    "br_stop_atr_mult": 1.0,
    "br_tp1_rr": 1.0,
    "br_tp2_rr": 2.0,
    "br_trail_ema_period": 20,
    "br_signal_ttl_minutes": 60,
    "breakout_min_volume_ratio": 0.0,
    "breakout_min_confidence": 0.0,
}

LEGACY_BREAKOUT_PARAM_ALIASES = {
    "lookback": "br_lookback_n",
    "breakout_lookback": "br_lookback_n",
    "retest_k_atr": "br_retest_atr_k",
    "breakout_retest_k_atr": "br_retest_atr_k",
    "stop_atr_mult": "br_stop_atr_mult",
    "breakout_stop_atr_mult": "br_stop_atr_mult",
    "tp_rr": "br_tp2_rr",
    "breakout_tp_rr": "br_tp2_rr",
}


def _to_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN guard
        return default
    return parsed


def _to_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


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
        records = candles_5m.to_dict("records")
        return [_coerce_candle(item) for item in records]
    return [_coerce_candle(item) for item in candles_5m]


def _normalize_params(context: dict[str, Any] | None, overrides: dict[str, Any]) -> dict[str, Any]:
    params = DEFAULT_BREAKOUT_PARAMS.copy()
    raw_params = context.get("params") if isinstance(context, dict) and isinstance(context.get("params"), dict) else {}
    params.update(raw_params)
    params.update({key: value for key, value in overrides.items() if value is not None})

    for legacy_key, canonical_key in LEGACY_BREAKOUT_PARAM_ALIASES.items():
        if params.get(canonical_key) is None and params.get(legacy_key) is not None:
            params[canonical_key] = params[legacy_key]
    return params


def _regime_state_ok(context: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    regime_state = context.get("regime_state") if isinstance(context, dict) else None
    if regime_state is None:
        return True, {}
    if not isinstance(regime_state, dict):
        return False, {"reason": "invalid_regime_state"}

    regime_ok = regime_state.get("ok")
    if isinstance(regime_ok, bool):
        return regime_ok, regime_state

    if regime_state.get("reason") == "regime_not_ok":
        return False, regime_state

    for required_key in ("close_1h", "ema200_1h", "ema200_slope", "atr_pct_1h"):
        if required_key not in regime_state:
            return False, {"reason": "incomplete_regime_state", **regime_state}

    return True, regime_state


def generate_breakout_retest_signal(
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
    lookback_n = max(2, _to_int(params.get("br_lookback_n"), DEFAULT_BREAKOUT_PARAMS["br_lookback_n"]))
    atr_period = max(2, _to_int(params.get("br_atr_period"), DEFAULT_BREAKOUT_PARAMS["br_atr_period"]))
    retest_atr_k = max(0.0, _to_float(params.get("br_retest_atr_k"), DEFAULT_BREAKOUT_PARAMS["br_retest_atr_k"]))
    stop_atr_mult = max(0.1, _to_float(params.get("br_stop_atr_mult"), DEFAULT_BREAKOUT_PARAMS["br_stop_atr_mult"]))
    tp1_rr = max(0.1, _to_float(params.get("br_tp1_rr"), DEFAULT_BREAKOUT_PARAMS["br_tp1_rr"]))
    tp2_rr = max(tp1_rr, _to_float(params.get("br_tp2_rr"), DEFAULT_BREAKOUT_PARAMS["br_tp2_rr"]))
    trail_ema_period = max(
        2,
        _to_int(params.get("br_trail_ema_period"), DEFAULT_BREAKOUT_PARAMS["br_trail_ema_period"]),
    )
    ttl_minutes = max(
        1,
        _to_int(params.get("br_signal_ttl_minutes"), DEFAULT_BREAKOUT_PARAMS["br_signal_ttl_minutes"]),
    )
    min_volume_ratio = max(
        0.0,
        _to_float(params.get("breakout_min_volume_ratio"), DEFAULT_BREAKOUT_PARAMS["breakout_min_volume_ratio"]),
    )
    min_confidence = max(
        0.0,
        min(
            1.0,
            _to_float(params.get("breakout_min_confidence"), DEFAULT_BREAKOUT_PARAMS["breakout_min_confidence"]),
        ),
    )

    min_len = max(lookback_n + 2, atr_period + 2)
    if len(candles) < min_len:
        return None

    current = candles[-1]
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [max(float(c.volume), 0.0) for c in candles]

    breakout_level = max(highs[-(lookback_n + 1) : -1])
    if current.close <= breakout_level:
        return None

    atr_now = max(atr(highs, lows, closes, period=atr_period)[-1], 1e-8)
    entry = breakout_level - (retest_atr_k * atr_now)
    stop = entry - (stop_atr_mult * atr_now)
    risk_per_unit = entry - stop
    if risk_per_unit <= 0:
        return None

    tp1 = entry + tp1_rr * risk_per_unit
    tp2 = entry + tp2_rr * risk_per_unit

    candle_range = max(current.high - current.low, 0.0)
    range_expansion = min(2.0, candle_range / atr_now) / 2.0
    recent_volumes = volumes[-min(20, len(volumes)) :]
    avg_volume = sum(recent_volumes) / max(1, len(recent_volumes))
    vol_ratio = current.volume / max(avg_volume, 1e-8) if avg_volume > 0 else 1.0
    if vol_ratio < min_volume_ratio:
        return None
    volume_score = min(2.0, max(0.0, vol_ratio)) / 2.0
    close_strength = min(1.0, max(0.0, (current.close - breakout_level) / atr_now))
    confidence = max(0.05, min(0.99, 0.6 + 0.15 * range_expansion + 0.15 * volume_score + 0.10 * close_strength))
    if confidence < min_confidence:
        return None

    symbol = None
    instrument = context.get("instrument") if isinstance(context, dict) else None
    if isinstance(instrument, dict):
        symbol = str(instrument.get("symbol") or "").strip() or None
    else:
        raw_symbol = getattr(instrument, "symbol", None)
        symbol = str(raw_symbol).strip() if raw_symbol else None

    created_at = current.ts
    expires_at = created_at + timedelta(minutes=ttl_minutes)
    reason = (
        f"breakout_retest level={breakout_level:.6f} close={current.close:.6f} "
        f"entry={entry:.6f} stop={stop:.6f} tp1={tp1:.6f} tp2={tp2:.6f} "
        f"lookback={lookback_n} atr_period={atr_period} retest_k={retest_atr_k:.2f}"
    )

    meta = {
        "symbol": symbol,
        "breakout_level": breakout_level,
        "atr_5m": atr_now,
        "partial_tp": tp1,
        "final_tp": tp2,
        "take": [tp1, tp2],
        "rr_targets": [tp1_rr, tp2_rr],
        "br_lookback_n": lookback_n,
        "br_atr_period": atr_period,
        "br_retest_atr_k": retest_atr_k,
        "br_stop_atr_mult": stop_atr_mult,
        "br_tp1_rr": tp1_rr,
        "br_tp2_rr": tp2_rr,
        "br_trail_ema_period": trail_ema_period,
        "br_signal_ttl_minutes": ttl_minutes,
        "volume_ratio": vol_ratio,
        "breakout_min_volume_ratio": min_volume_ratio,
        "breakout_min_confidence": min_confidence,
        "range_expansion_vs_atr": candle_range / atr_now,
        "regime": regime_meta,
        "signal_on_close_only": True,
    }

    return SignalPlan(
        symbol=symbol,
        strategy="StrategyBreakoutRetest",
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
        meta=meta,
    )
