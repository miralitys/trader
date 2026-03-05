from datetime import datetime, timedelta, timezone

from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.mean_reversion_hard_stop import generate_mean_reversion_hard_stop_signal
from app.strategies.trend_retrace_70 import generate_trend_retrace_70_signal
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


def test_no_lookahead_trend_retrace_70_requires_reclaim_candle():
    now = datetime.now(timezone.utc)
    candles: list[CandleData] = []

    for i in range(320):
        p = 80 + i * 0.10
        candles.append(
            CandleData(
                ts=now + timedelta(minutes=5 * i),
                open=p - 0.05,
                high=p + 0.20,
                low=p - 0.20,
                close=p,
                volume=120 + i,
            )
        )

    # Create pullback bars while keeping last close below reclaim threshold.
    for i in range(-10, 0):
        c = candles[i]
        candles[i] = CandleData(
            ts=c.ts,
            open=c.open - 1.6,
            high=c.high - 0.6,
            low=c.low - 2.1,
            close=c.close - 1.8,
            volume=c.volume * 1.08,
        )

    # Keep the latest candle below reclaim threshold.
    latest = candles[-1]
    candles[-1] = CandleData(
        ts=latest.ts,
        open=latest.open - 1.5,
        high=latest.high - 0.7,
        low=latest.low - 2.2,
        close=latest.close - 2.0,
        volume=latest.volume * 1.2,
    )

    before_reclaim = generate_trend_retrace_70_signal(candles, rsi_max=95.0)
    assert before_reclaim is None

    reclaim = CandleData(
        ts=now + timedelta(minutes=5 * 320),
        open=candles[-1].close + 0.1,
        high=candles[-1].close + 2.6,
        low=candles[-1].close - 0.2,
        close=candles[-1].close + 2.2,
        volume=candles[-1].volume * 1.8,
    )
    after_reclaim = generate_trend_retrace_70_signal(candles + [reclaim], rsi_max=95.0)
    assert after_reclaim is not None
