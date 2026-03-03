from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


DEFAULT_RISK = {
    "risk_per_trade_pct": 1.0,
    "daily_loss_limit_pct": 2.0,
    "weekly_loss_limit_pct": 5.0,
    "max_positions": 1,
    "max_trades_per_day": 2,
    "max_hold_hours": 72,
    "entry_ttl_minutes": 60,
    "consecutive_losses_pause": 2,
    "kill_switch_on_data_error": True,
    "kill_switch_on_reconciliation_error": True,
    "max_drawdown_pct": 10.0,
    "strict_mode_action": "pause",
    "max_position_notional_pct": 100.0,
}

DEFAULT_STRATEGY = {
    "ema200_filter_1h": True,
    "atr_threshold_pct_1h": 4.0,
    "confirm_15m": False,
    "breakout_lookback": 20,
    "breakout_retest_k_atr": 0.3,
    "pullback_rsi_threshold": 45.0,
    "trade_only_strategy": "both",
}

DEFAULT_FEES = {
    "maker_fee_pct": 0.4,
    "taker_fee_pct": 0.6,
    "market_exit_slippage_pct": 0.05,
    "backtest_entry_slippage_pct": 0.10,
    "backtest_exit_slippage_pct": 0.10,
    "backtest_stop_slippage_pct": 0.20,
    "backtest_execution_model": "CONSERVATIVE_TAKER_ONLY",
}

DEFAULT_UNIVERSE = {
    "input_tickers": [
        "DYDX",
        "INJ",
        "ICP",
        "GALA",
        "AXS",
        "TRB",
        "ONDO",
        "IOTA",
        "NOT",
        "FIL",
        "NEO",
        "ENJ",
        "HYPE",
        "STRK",
        "SLP",
        "ONE",
        "MINA",
        "RVN",
        "RUNE",
    ],
    "top_symbols": [],
    "ranked": [],
    "last_recomputed_at": None,
    "selection_basis": "30d_quote_volume",
}


class SettingsOut(BaseModel):
    id: int
    user_id: int
    paper_enabled: bool
    live_enabled: bool
    live_confirmed: bool
    risk_params_json: dict
    strategy_params_json: dict
    universe_json: dict
    fees_json: dict
    coinbase_api_key_hint: str | None
    kill_switch_paused: bool
    strict_mode: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SettingsUpdate(BaseModel):
    paper_enabled: bool | None = None
    live_enabled: bool | None = None
    live_confirmation_text: str | None = None
    risk_params_json: dict | None = None
    strategy_params_json: dict | None = None
    universe_json: dict | None = None
    fees_json: dict | None = None
    coinbase_api_key: str | None = None
    coinbase_api_secret: str | None = None
    strict_mode: bool | None = None

    @model_validator(mode="after")
    def validate_live_confirmation(self) -> "SettingsUpdate":
        if self.live_enabled and self.live_confirmation_text != "ENABLE LIVE":
            raise ValueError("To enable live mode, set live_confirmation_text to ENABLE LIVE")
        return self
