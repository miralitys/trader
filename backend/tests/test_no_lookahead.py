from datetime import datetime, timedelta, timezone

from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.mean_reversion_hard_stop import generate_mean_reversion_hard_stop_signal
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


def test_no_lookahead_mean_reversion_triggers_only_after_confirmation_close():
    now = datetime.now(timezone.utc)
    candles: list[CandleData] = []

    for i in range(260):
        p = 90 + i * 0.12
        candles.append(
            CandleData(
                ts=now + timedelta(minutes=5 * i),
                open=p - 0.05,
                high=p + 0.20,
                low=p - 0.20,
                close=p,
                volume=200 + i,
            )
        )

    setup = candles[-1]
    setup_close = setup.close - 4.0
    candles[-1] = CandleData(
        ts=setup.ts,
        open=setup.close - 0.2,
        high=setup.close - 0.1,
        low=setup_close - 0.3,
        close=setup_close,
        volume=setup.volume * 1.5,
    )

    before_trigger = generate_mean_reversion_hard_stop_signal(candles, regime_meta={"ema200_slope": 0.1})
    assert before_trigger is None

    trigger = CandleData(
        ts=now + timedelta(minutes=5 * 260),
        open=setup_close,
        high=setup_close + 3.0,
        low=setup_close - 0.2,
        close=setup_close + 2.5,
        volume=500,
    )
    after_trigger = generate_mean_reversion_hard_stop_signal(
        candles + [trigger],
        regime_meta={"ema200_slope": 0.1},
    )
    assert after_trigger is not None
