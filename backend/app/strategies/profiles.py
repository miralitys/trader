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

STRATEGY_PROFILES: dict[str, dict[str, Any]] = {
    "StrategyBreakoutRetest": {
        "risk": {
            **_COMMON_RISK,
            "max_hold_hours": 72,
            "entry_ttl_minutes": 45,
            "max_trades_per_day": 4,
            "min_profit_to_cost_ratio": 1.4,
        },
        "fees": {
            **_COMMON_FEES,
        },
        "signal": {
            "ema200_filter_1h": True,
            "atr_threshold_pct_1h": 5.0,
            "confirm_15m": True,
            "breakout_lookback": 20,
            "breakout_retest_k_atr": 0.2,
            "breakout_stop_atr_mult": 1.0,
            "breakout_tp_rr": 2.0,
            "breakout_min_volume_ratio": 0.0,
            "breakout_min_confidence": 0.0,
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
            "ema200_filter_1h": True,
            "atr_threshold_pct_1h": 5.0,
            "confirm_15m": True,
            "pullback_rsi_threshold": 55.0,
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
            "ema200_filter_1h": True,
            "atr_threshold_pct_1h": 3.5,
            "confirm_15m": True,
            "mr_bb_period": 20,
            "mr_bb_std": 2.2,
            "mr_rsi_period": 14,
            "mr_rsi_entry_threshold": 27.0,
            "mr_safety_ema_period": 200,
            "mr_lookback_stop": 20,
            "mr_stop_atr_buffer": 0.2,
            "mr_max_stop_pct": 0.02,
            "mr_tp_rr": 1.6,
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
            "ema200_filter_1h": True,
            "atr_threshold_pct_1h": 4.5,
            "confirm_15m": True,
            "tr70_ema_fast_period": 20,
            "tr70_ema_mid_period": 50,
            "tr70_ema_slow_period": 200,
            "tr70_pullback_lookback": 10,
            "tr70_pullback_depth_pct": 0.35,
            "tr70_reclaim_buffer_pct": 0.05,
            "tr70_rsi_period": 14,
            "tr70_rsi_min": 42.0,
            "tr70_rsi_max": 62.0,
            "tr70_stop_atr_mult": 0.7,
            "tr70_min_stop_pct": 0.7,
            "tr70_max_stop_pct": 1.8,
            "tr70_tp_rr": 2.1,
            "tr70_min_volume_ratio": 0.8,
        },
        "backtest": {
            "history_min_coverage_ratio": 0.005,
            "history_target_coverage_ratio": 0.005,
            "history_required_coverage_ratio": 0.20,
            "input_tickers": list(DEFAULT_UNIVERSE_INPUT),
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
