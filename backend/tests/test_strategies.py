from datetime import datetime, timedelta, timezone

from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.pullback_trend import generate_pullback_signal
from app.strategies.types import CandleData


def _candles(base_price: float = 100.0, n: int = 120) -> list[CandleData]:
    now = datetime.now(timezone.utc)
    rows: list[CandleData] = []
    for i in range(n):
        p = base_price + (i * 0.05)
        rows.append(
            CandleData(
                ts=now + timedelta(minutes=5 * i),
                open=p,
                high=p + 0.4,
                low=p - 0.4,
                close=p + 0.1,
                volume=100 + i,
            )
        )
    return rows


def test_breakout_signal_generated_on_closed_bar_break():
    candles = _candles()
    # force breakout on last bar above prior highs
    prev_high = max(c.high for c in candles[-21:-1])
    candles[-1] = CandleData(
        ts=candles[-1].ts,
        open=prev_high - 0.1,
        high=prev_high + 1.0,
        low=prev_high - 0.2,
        close=prev_high + 0.8,
        volume=1000,
    )

    signal = generate_breakout_retest_signal(candles, lookback=20, retest_k_atr=0.3)
    assert signal is not None
    assert signal.signal == "long"
    assert signal.entry > signal.stop


def test_pullback_signal_generated_after_reclaim():
    candles = _candles(base_price=50, n=140)

    # create pullback then reclaim above EMA20 at last candle
    for i in range(-8, -1):
        c = candles[i]
        candles[i] = CandleData(
            ts=c.ts,
            open=c.open,
            high=c.high,
            low=c.low - 0.5,
            close=c.close - 1.2,
            volume=c.volume,
        )

    last = candles[-1]
    candles[-1] = CandleData(
        ts=last.ts,
        open=last.open,
        high=last.high + 1.0,
        low=last.low,
        close=last.close + 1.5,
        volume=last.volume,
    )

    signal = generate_pullback_signal(candles, rsi_threshold=90)
    assert signal is not None
    assert signal.entry > signal.stop
    assert signal.take > signal.entry
