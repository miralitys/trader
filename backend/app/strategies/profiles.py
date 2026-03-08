from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.config import DEFAULT_UNIVERSE_INPUT

SUPPORTED_STRATEGIES = (
    "StrategyBreakoutRetest",
    "StrategyPullbackToTrend",
    "MeanReversionHardStop",
    "StrategyTrendRetrace70",
)

STRATEGY_ALIASES = {
    "breakout": "StrategyBreakoutRetest",
    "pullback": "StrategyPullbackToTrend",
    "mean_reversion": "MeanReversionHardStop",
    "trend_retrace_70": "StrategyTrendRetrace70",
}

PROFILE_SECTION_KEYS = ("signal", "risk", "fees", "backtest")

DEFAULT_INITIAL_EQUITY = 10000.0

_COMMON_RISK = {
    "risk_per_trade_pct": 1.0,
    "daily_loss_limit_pct": 2.0,
    "weekly_loss_limit_pct": 5.0,
    "max_positions": 1,
    "max_trades_per_day": 2,
    "max_hold_hours": 72,
    "entry_ttl_minutes": 60,
    "consecutive_losses_pause": 2,
    "max_drawdown_pct": 10.0,
    "strict_mode_action": "pause",
    "max_position_notional_pct": 100.0,
    "min_profit_to_cost_ratio": 1.2,
}

_COMMON_FEES = {
    "maker_fee_pct": 0.25,
    "taker_fee_pct": 0.4,
    "market_exit_slippage_pct": 0.05,
    "backtest_entry_slippage_pct": 0.10,
    "backtest_exit_slippage_pct": 0.10,
    "backtest_stop_slippage_pct": 0.20,
    "backtest_execution_model": "CONSERVATIVE_TAKER_ONLY",
}

_TREND_RETRACE_70_BACKTEST_INPUT = [
    "BTC",
    "ETH",
    "SOL",
    "AVAX",
    "LINK",
]

STRATEGY_PROFILES: dict[str, dict[str, Any]] = {
    "StrategyBreakoutRetest": {
        "risk": {
            **_COMMON_RISK,
            "max_hold_hours": 72,
            "entry_ttl_minutes": 60,
            "max_trades_per_day": 4,
            "min_profit_to_cost_ratio": 1.4,
        },
        "fees": {
            **_COMMON_FEES,
        },
        "signal": {
            "br_ema200_filter_1h": True,
            "br_atr_threshold_pct_1h": 5.0,
            "br_confirm_15m": True,
            "br_lookback_n": 20,
            "br_atr_period": 14,
            "br_retest_atr_k": 0.3,
            "br_stop_atr_mult": 1.0,
            "br_tp1_rr": 1.0,
            "br_tp2_rr": 2.0,
            "br_trail_ema_period": 20,
            "br_signal_ttl_minutes": 60,
        },
        "backtest": {
            "history_min_coverage_ratio": 0.005,
            "history_target_coverage_ratio": 0.005,
            "history_required_coverage_ratio": 0.20,
            "input_tickers": list(DEFAULT_UNIVERSE_INPUT),
        },
    },
    "StrategyPullbackToTrend": {
        "risk": {
            **_COMMON_RISK,
            "max_hold_hours": 48,
            "entry_ttl_minutes": 45,
            "max_trades_per_day": 4,
            "min_profit_to_cost_ratio": 1.1,
        },
        "fees": {
            **_COMMON_FEES,
        },
        "signal": {
            "pt_ema200_filter_1h": True,
            "pt_atr_threshold_pct_1h": 5.0,
            "pt_confirm_15m": True,
            "pt_ema_fast": 20,
            "pt_ema_slow": 50,
            "pt_rsi_period": 14,
            "pt_rsi_threshold": 45.0,
            "pt_stop_lookback": 10,
            "pt_tp_rr": 1.2,
            "pt_signal_ttl_minutes": 60,
        },
        "backtest": {
            "history_min_coverage_ratio": 0.005,
            "history_target_coverage_ratio": 0.005,
            "history_required_coverage_ratio": 0.20,
            "input_tickers": list(DEFAULT_UNIVERSE_INPUT),
        },
    },
    "MeanReversionHardStop": {
        "risk": {
            **_COMMON_RISK,
            "max_hold_hours": 18,
            "entry_ttl_minutes": 60,
            "max_trades_per_day": 2,
            "min_profit_to_cost_ratio": 1.35,
        },
        "fees": {
            **_COMMON_FEES,
        },
        "signal": {
            "mr_ema200_filter_1h": True,
            "mr_atr_threshold_pct_1h": 3.5,
            "mr_confirm_15m": True,
            "mr_bb_period": 20,
            "mr_bb_std": 2.2,
            "mr_rsi_period": 14,
            "mr_rsi_entry_threshold": 27.0,
            "mr_safety_ema_period": 200,
            "mr_lookback_stop": 20,
            "mr_stop_atr_buffer": 0.2,
            "mr_max_stop_pct": 0.02,
            "mr_tp_rr": 1.6,
            "mr_signal_ttl_minutes": 60,
        },
        "backtest": {
            "history_min_coverage_ratio": 0.005,
            "history_target_coverage_ratio": 0.005,
            "history_required_coverage_ratio": 0.20,
            "input_tickers": list(DEFAULT_UNIVERSE_INPUT),
        },
    },
    "StrategyTrendRetrace70": {
        "risk": {
            **_COMMON_RISK,
            "max_hold_hours": 48,
            "entry_ttl_minutes": 60,
            "max_trades_per_day": 4,
            "min_profit_to_cost_ratio": 1.8,
        },
        "fees": {
            **_COMMON_FEES,
        },
        "signal": {
            "tr_ema200_filter_1h": True,
            "tr_atr_threshold_pct_1h": 4.5,
            "tr_confirm_15m": True,
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
        },
        "backtest": {
            "history_min_coverage_ratio": 0.005,
            "history_target_coverage_ratio": 0.005,
            "history_required_coverage_ratio": 0.20,
            "input_tickers": list(_TREND_RETRACE_70_BACKTEST_INPUT),
        },
    },
}


def normalize_strategy_name(strategy: str | None) -> str:
    if strategy in SUPPORTED_STRATEGIES:
        return strategy or "StrategyBreakoutRetest"
    return STRATEGY_ALIASES.get((strategy or "").strip(), "StrategyBreakoutRetest")


def resolve_strategy_scope(value: str | None) -> list[str]:
    v = (value or "both").strip()
    if v == "both":
        return list(SUPPORTED_STRATEGIES)

    normalized = normalize_strategy_name(v)
    if normalized in SUPPORTED_STRATEGIES:
        return [normalized]
    return ["StrategyBreakoutRetest"]


def get_strategy_profile(strategy: str | None) -> dict[str, Any]:
    normalized = normalize_strategy_name(strategy)
    profile = STRATEGY_PROFILES.get(normalized, STRATEGY_PROFILES["StrategyBreakoutRetest"])
    return deepcopy(profile)


def get_strategy_overrides(strategy_params: dict[str, Any] | None, strategy: str | None) -> dict[str, dict[str, Any]]:
    normalized = normalize_strategy_name(strategy)
    payload = strategy_params if isinstance(strategy_params, dict) else {}
    raw_overrides = payload.get("strategy_overrides")
    if not isinstance(raw_overrides, dict):
        return {}

    strategy_override = raw_overrides.get(normalized)
    if not isinstance(strategy_override, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for section in PROFILE_SECTION_KEYS:
        section_payload = strategy_override.get(section)
        if isinstance(section_payload, dict):
            result[section] = section_payload.copy()
    return result


def apply_strategy_overrides(
    profile: dict[str, Any],
    strategy_params: dict[str, Any] | None,
    strategy: str | None,
) -> dict[str, Any]:
    merged = deepcopy(profile)
    overrides = get_strategy_overrides(strategy_params, strategy)
    if not overrides:
        return merged

    for section, section_override in overrides.items():
        base_section = merged.get(section)
        if isinstance(base_section, dict):
            merged_section = base_section.copy()
            merged_section.update(section_override)
            merged[section] = merged_section
        else:
            merged[section] = section_override.copy()
    return merged
