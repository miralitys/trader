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

    before_trigger = generate_mean_reversion_hard_stop_signal(
        candles,
        context={"regime_state": {"ok": True, "close_1h": 120.0, "ema200_1h": 100.0, "ema200_slope": 0.1, "atr_pct_1h": 2.0}},
    )
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
        context={"regime_state": {"ok": True, "close_1h": 120.0, "ema200_1h": 100.0, "ema200_slope": 0.1, "atr_pct_1h": 2.0}},
    )
    assert after_trigger is not None


def _trend_retrace_15m_candles() -> list[CandleData]:
    now = datetime.now(timezone.utc)
    closes = (
        [90.0 + (0.5 * idx) for idx in range(20)]
        + [100.0 + (0.5 * idx) for idx in range(30)]
        + [
            115.0,
            114.0,
            113.0,
            112.0,
            113.0,
            115.0,
            118.0,
            121.0,
            124.0,
            127.0,
            130.0,
            128.0,
            126.0,
            123.0,
            120.0,
            118.0,
            116.8,
            116.2,
            116.7,
            117.1,
            117.3,
            117.5,
            117.7,
            117.9,
        ]
    )
    rows: list[CandleData] = []
    previous_close = closes[0]
    for idx, close in enumerate(closes):
        open_price = previous_close if idx else close - 0.2
        rows.append(
            CandleData(
                ts=now + timedelta(minutes=15 * idx),
                open=open_price,
                high=max(open_price, close) + 0.4,
                low=min(open_price, close) - 0.4,
                close=close,
                volume=500 + idx * 10,
            )
        )
        previous_close = close
    return rows


def _expand_15m_to_5m(candles_15m: list[CandleData]) -> list[CandleData]:
    rows: list[CandleData] = []
    for candle in candles_15m:
        step = (candle.close - candle.open) / 3.0
        current_open = candle.open
        for offset in range(3):
            close = candle.close if offset == 2 else candle.open + step * (offset + 1)
            rows.append(
                CandleData(
                    ts=candle.ts + timedelta(minutes=5 * offset),
                    open=current_open,
                    high=max(current_open, close) + 0.2,
                    low=min(current_open, close) - 0.2,
                    close=close,
                    volume=candle.volume / 3.0,
                )
            )
            current_open = close
    return rows


def test_no_lookahead_trend_retrace_70_requires_trigger_close():
    candles_15m = _trend_retrace_15m_candles()
    candles_5m = _expand_15m_to_5m(candles_15m)

    for idx, close in zip(range(-3, 0), [117.0, 117.1, 117.8]):
        candle = candles_5m[idx]
        candles_5m[idx] = CandleData(
            ts=candle.ts,
            open=close - 0.2,
            high=close + 0.35,
            low=close - 0.4,
            close=close,
            volume=candle.volume * 1.3,
        )

    before_trigger = generate_trend_retrace_70_signal(
        candles_5m[:-1],
        context={
            "regime_state": {"ok": True, "close_1h": 130.0, "ema200_1h": 118.0, "ema200_slope": 1.5, "atr_pct_1h": 2.0},
            "candles_15m": candles_15m,
        },
    )
    assert before_trigger is None

    after_trigger = generate_trend_retrace_70_signal(
        candles_5m,
        context={
            "regime_state": {"ok": True, "close_1h": 130.0, "ema200_1h": 118.0, "ema200_slope": 1.5, "atr_pct_1h": 2.0},
            "candles_15m": candles_15m,
        },
    )
    assert after_trigger is not None
