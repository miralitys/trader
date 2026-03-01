from datetime import datetime, timedelta, timezone

from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.types import CandleData


def test_no_lookahead_breakout_uses_only_available_candles():
    now = datetime.now(timezone.utc)
    candles: list[CandleData] = []

    for i in range(60):
        p = 100 + i * 0.01
        candles.append(
            CandleData(
                ts=now + timedelta(minutes=5 * i),
                open=p,
                high=p + 0.2,
                low=p - 0.2,
                close=p,
                volume=100,
            )
        )

    base_signal = generate_breakout_retest_signal(candles)
    assert base_signal is None

    # Append a future breakout bar; signal can appear only after adding that bar.
    future = CandleData(
        ts=now + timedelta(minutes=5 * 60),
        open=100.5,
        high=105.0,
        low=100.4,
        close=104.8,
        volume=1000,
    )
    with_future = generate_breakout_retest_signal(candles + [future])
    assert with_future is not None
