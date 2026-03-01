from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class InstrumentConstraints:
    min_size: float
    size_increment: float


@dataclass
class RiskParams:
    risk_per_trade_pct: float = 1.0
    daily_loss_limit_pct: float = 2.0
    weekly_loss_limit_pct: float = 5.0
    max_positions: int = 1
    max_trades_per_day: int = 2
    max_hold_hours: int = 72
    entry_ttl_minutes: int = 60
    consecutive_losses_pause: int = 2
    max_drawdown_pct: float = 10.0


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    qty_base: float = 0.0
    qty_quote: float = 0.0


def round_down_to_increment(value: float, increment: float) -> float:
    if increment <= 0:
        return value
    steps = math.floor(value / increment)
    return max(0.0, steps * increment)


def calculate_position_size(
    equity: float,
    risk_per_trade_pct: float,
    entry: float,
    stop: float,
    constraints: InstrumentConstraints,
) -> tuple[float, float]:
    risk = entry - stop
    if equity <= 0 or risk_per_trade_pct <= 0 or risk <= 0 or entry <= 0:
        return 0.0, 0.0

    # size_quote = equity * risk_pct / (entry - stop) * entry
    size_quote = equity * (risk_per_trade_pct / 100.0) / risk * entry
    qty_base = size_quote / entry

    qty_base = round_down_to_increment(qty_base, constraints.size_increment)
    if qty_base < constraints.min_size:
        return 0.0, 0.0

    qty_quote = qty_base * entry
    return qty_base, qty_quote


class RiskManager:
    def __init__(self, params: RiskParams):
        self.params = params

    def assess_entry(
        self,
        equity: float,
        entry: float,
        stop: float,
        constraints: InstrumentConstraints,
        current_open_positions: int,
        trades_today: int,
        daily_loss_pct: float,
        weekly_loss_pct: float,
        consecutive_losses: int,
        drawdown_pct: float,
    ) -> RiskDecision:
        if current_open_positions >= self.params.max_positions:
            return RiskDecision(False, "max_positions limit reached")

        if trades_today >= self.params.max_trades_per_day:
            return RiskDecision(False, "max_trades_per_day limit reached")

        if daily_loss_pct >= self.params.daily_loss_limit_pct:
            return RiskDecision(False, "daily_loss_limit reached")

        if weekly_loss_pct >= self.params.weekly_loss_limit_pct:
            return RiskDecision(False, "weekly_loss_limit reached")

        if consecutive_losses >= self.params.consecutive_losses_pause:
            return RiskDecision(False, "consecutive_losses pause active")

        if drawdown_pct >= self.params.max_drawdown_pct:
            return RiskDecision(False, "portfolio drawdown limit reached")

        qty_base, qty_quote = calculate_position_size(
            equity=equity,
            risk_per_trade_pct=self.params.risk_per_trade_pct,
            entry=entry,
            stop=stop,
            constraints=constraints,
        )
        if qty_base <= 0:
            return RiskDecision(False, "position below minimum size")

        return RiskDecision(True, "ok", qty_base=qty_base, qty_quote=qty_quote)
