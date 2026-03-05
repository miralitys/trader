from datetime import datetime, timedelta, timezone

from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.mean_reversion_hard_stop import generate_mean_reversion_hard_stop_signal
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


def _trend_candles(base_price: float = 90.0, n: int = 260, step: float = 0.12) -> list[CandleData]:
    now = datetime.now(timezone.utc)
    rows: list[CandleData] = []
    for i in range(n):
        p = base_price + i * step
        rows.append(
            CandleData(
                ts=now + timedelta(minutes=5 * i),
                open=p - 0.05,
                high=p + 0.20,
                low=p - 0.20,
                close=p,
                volume=200 + i,
            )
        )
    return rows


def test_mean_reversion_hard_stop_signal_generation():
    candles = _trend_candles()

    setup = candles[-2]
    setup_close = setup.close - 4.2
    candles[-2] = CandleData(
        ts=setup.ts,
        open=setup.close - 0.2,
        high=setup.close - 0.1,
        low=setup_close - 0.3,
        close=setup_close,
        volume=setup.volume * 1.6,
    )

    trigger = candles[-1]
    trigger_close = setup_close + 2.6
    candles[-1] = CandleData(
        ts=trigger.ts,
        open=setup_close,
        high=trigger_close + 0.3,
        low=setup_close - 0.2,
        close=trigger_close,
        volume=trigger.volume * 1.2,
    )

    signal = generate_mean_reversion_hard_stop_signal(candles, regime_meta={"ema200_slope": 0.1})
    assert signal is not None
    assert signal.signal == "long"
    assert signal.strategy == "MeanReversionHardStop"
    assert signal.entry > signal.stop
    assert signal.take > signal.entry


def test_mean_reversion_hard_stop_blocked_below_ema200_guard():
    candles = _trend_candles()
    setup = candles[-2]
    trigger = candles[-1]
    candles[-2] = CandleData(
        ts=setup.ts,
        open=82.5,
        high=83.0,
        low=81.8,
        close=82.0,
        volume=setup.volume * 1.6,
    )
    candles[-1] = CandleData(
        ts=trigger.ts,
        open=82.0,
        high=83.2,
        low=81.9,
        close=83.0,
        volume=trigger.volume * 1.2,
    )

    signal = generate_mean_reversion_hard_stop_signal(candles, regime_meta={"ema200_slope": 0.1})
    assert signal is None


def test_mean_reversion_hard_stop_skips_when_stop_too_far():
    candles = _trend_candles()
    setup = candles[-2]
    setup_close = setup.close - 3.8
    candles[-2] = CandleData(
        ts=setup.ts,
        open=setup.close - 0.2,
        high=setup.close - 0.1,
        low=setup_close - 0.4,
        close=setup_close,
        volume=setup.volume * 1.5,
    )
    trigger = candles[-1]
    trigger_close = setup_close + 2.4
    candles[-1] = CandleData(
        ts=trigger.ts,
        open=setup_close,
        high=trigger_close + 0.2,
        low=setup_close - 0.1,
        close=trigger_close,
        volume=trigger.volume * 1.1,
    )

    deep_low = candles[-10]
    candles[-10] = CandleData(
        ts=deep_low.ts,
        open=deep_low.open,
        high=deep_low.high,
        low=deep_low.low - 8.0,
        close=deep_low.close,
        volume=deep_low.volume,
    )

    signal = generate_mean_reversion_hard_stop_signal(
        candles,
        max_stop_pct=0.03,
        regime_meta={"ema200_slope": 0.1},
    )
    assert signal is None
