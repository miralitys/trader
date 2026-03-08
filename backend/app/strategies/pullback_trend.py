from __future__ import annotations

from app.strategies.pullback_to_trend import generate_pullback_to_trend_signal


def generate_pullback_signal(*args, **kwargs):
    return generate_pullback_to_trend_signal(*args, **kwargs)
