from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CandleData:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class SignalPlan:
    strategy: str
    timeframe: str
    signal: str
    entry: float
    stop: float
    take: float
    confidence: float
    reason: str
    meta: dict
