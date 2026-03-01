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
