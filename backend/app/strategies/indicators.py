from __future__ import annotations

from math import isnan


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    if period <= 1:
        return values.copy()
    multiplier = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append((value - out[-1]) * multiplier + out[-1])
    return out


def atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float]:
    if len(high) != len(low) or len(low) != len(close):
        raise ValueError("OHLC lists must have the same length")
    if len(close) < 2:
        return [0.0 for _ in close]

    true_ranges: list[float] = [high[0] - low[0]]
    for idx in range(1, len(close)):
        tr = max(
            high[idx] - low[idx],
            abs(high[idx] - close[idx - 1]),
            abs(low[idx] - close[idx - 1]),
        )
        true_ranges.append(tr)
    return ema(true_ranges, period)


def rsi(values: list[float], period: int = 14) -> list[float]:
    if len(values) < 2:
        return [50.0 for _ in values]

    gains: list[float] = [0.0]
    losses: list[float] = [0.0]

    for idx in range(1, len(values)):
        diff = values[idx] - values[idx - 1]
        gains.append(max(diff, 0.0))
        losses.append(abs(min(diff, 0.0)))

    avg_gain = ema(gains, period)
    avg_loss = ema(losses, period)
    out: list[float] = []

    for g, l in zip(avg_gain, avg_loss):
        if l == 0:
            out.append(100.0)
            continue
        rs = g / l
        value = 100 - (100 / (1 + rs))
        out.append(50.0 if isnan(value) else value)
    return out


def bollinger_bands(
    values: list[float],
    period: int = 20,
    std: float = 2.0,
) -> tuple[list[float], list[float], list[float]]:
    if not values:
        return [], [], []

    lookback = max(1, period)
    mid: list[float] = []
    upper: list[float] = []
    lower: list[float] = []

    for idx in range(len(values)):
        start = max(0, idx - lookback + 1)
        window = values[start : idx + 1]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        sigma = variance**0.5

        mid.append(mean)
        upper.append(mean + std * sigma)
        lower.append(mean - std * sigma)

    return mid, upper, lower
