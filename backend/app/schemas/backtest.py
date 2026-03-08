from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class BacktestRunRequest(BaseModel):
    strategy: str = Field(default="StrategyBreakoutRetest")
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_period(self) -> "BacktestRunRequest":
        if self.start_ts and self.end_ts and self.start_ts >= self.end_ts:
            raise ValueError("start_ts must be earlier than end_ts")
        return self


class BacktestHistoryReadinessRequest(BaseModel):
    strategy: str = Field(default="StrategyBreakoutRetest")
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_period(self) -> "BacktestHistoryReadinessRequest":
        if self.start_ts and self.end_ts and self.start_ts >= self.end_ts:
            raise ValueError("start_ts must be earlier than end_ts")
        return self


class BacktestHistoryReadinessOut(BaseModel):
    ready: bool
    reason: str
    strategy_requested: str
    strategy_runtime: str
    period_requested: dict
    period_effective: dict
    coverage: dict
    universe: dict
    data_availability: list[dict]


class BacktestOut(BaseModel):
    id: int
    strategy: str
    universe_json: list
    start_ts: datetime
    end_ts: datetime
    params_json: dict
    metrics_json: dict
    equity_curve_json: list
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BacktestBatchRunRequest(BaseModel):
    batch_id: str | None = None
    strategies: list[str] | None = None
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    common_params: dict = Field(default_factory=dict)
    per_strategy_params: dict[str, dict] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_period(self) -> "BacktestBatchRunRequest":
        if self.start_ts and self.end_ts and self.start_ts >= self.end_ts:
            raise ValueError("start_ts must be earlier than end_ts")
        return self


class BacktestBatchRunOut(BaseModel):
    batch_id: str
    start_ts: datetime
    end_ts: datetime
    strategies: list[str]
    backtests: list[BacktestOut]
    enqueue_errors: dict[str, str] = Field(default_factory=dict)


class BacktestBatchStrategyStatsOut(BaseModel):
    strategy: str
    status: str
    backtest_id: int | None = None
    created_at: datetime | None = None
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    base: dict = Field(default_factory=dict)
    stress_1_5x: dict = Field(default_factory=dict)
    stress_2_0x: dict = Field(default_factory=dict)
    error: str | None = None


class BacktestBatchStatsOut(BaseModel):
    batch_id: str
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    summary: dict
    strategies: list[BacktestBatchStrategyStatsOut]


class BacktestProgressTimeframeOut(BaseModel):
    timeframe: str
    candles: int
    instruments: int
    oldest_ts: datetime | None = None
    latest_ts: datetime | None = None


class BacktestProgressStrategyOut(BaseModel):
    strategy: str
    ready: bool
    reason: str
    effective_ratio: float
    required_ratio: float
    selected_top5: list[str] = Field(default_factory=list)


class BackfillRuntimeStatusOut(BaseModel):
    state: str
    updated_at: datetime | None = None
    details: dict = Field(default_factory=dict)


class BacktestProgressOut(BaseModel):
    generated_at: datetime
    summary: dict
    backfill_status: BackfillRuntimeStatusOut
    timeframes: list[BacktestProgressTimeframeOut]
    strategies: list[BacktestProgressStrategyOut]
