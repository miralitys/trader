from datetime import datetime, timedelta, timezone

from app.strategies.breakout_retest import generate_breakout_retest_signal
from app.strategies.mean_reversion_hard_stop import generate_mean_reversion_hard_stop_signal
from app.strategies.pullback_to_trend import generate_pullback_to_trend_signal
from app.strategies.trend_retrace_70 import (
    find_confirmed_pivots,
    find_latest_impulse_wave,
    generate_trend_retrace_70_signal,
)
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

    signal = generate_breakout_retest_signal(
        candles,
        context={
            "regime_state": {
                "ok": True,
                "close_1h": 105.0,
                "ema200_1h": 100.0,
                "ema200_slope": 1.0,
                "atr_pct_1h": 2.5,
            },
            "params": {
                "br_lookback_n": 20,
                "br_atr_period": 14,
                "br_retest_atr_k": 0.3,
                "br_stop_atr_mult": 1.0,
                "br_tp1_rr": 1.0,
                "br_tp2_rr": 2.0,
                "br_trail_ema_period": 20,
                "br_signal_ttl_minutes": 60,
            },
            "instrument": {"symbol": "BTC-USDC"},
        },
    )
    assert signal is not None
    assert signal.signal == "long"
    assert signal.entry > signal.stop
    assert signal.takes == [signal.meta["partial_tp"], signal.meta["final_tp"]]
    assert signal.take == signal.meta["final_tp"]
    assert signal.expires_at is not None
    assert signal.expires_at > signal.created_at


def test_breakout_signal_respects_closed_bar_only_no_lookahead():
    candles = _candles()
    prev_high = max(c.high for c in candles[-21:-1])

    pre_break_signal = generate_breakout_retest_signal(
        candles[:-1],
        context={"regime_state": {"ok": True, "close_1h": 105.0, "ema200_1h": 100.0, "ema200_slope": 1.0, "atr_pct_1h": 2.5}},
    )
    assert pre_break_signal is None

    candles[-1] = CandleData(
        ts=candles[-1].ts,
        open=prev_high - 0.1,
        high=prev_high + 1.2,
        low=prev_high - 0.2,
        close=prev_high + 0.9,
        volume=1200,
    )

    signal = generate_breakout_retest_signal(
        candles,
        context={"regime_state": {"ok": True, "close_1h": 105.0, "ema200_1h": 100.0, "ema200_slope": 1.0, "atr_pct_1h": 2.5}},
    )
    assert signal is not None


def test_breakout_signal_returns_none_when_regime_not_ok():
    candles = _candles()
    prev_high = max(c.high for c in candles[-21:-1])
    candles[-1] = CandleData(
        ts=candles[-1].ts,
        open=prev_high - 0.1,
        high=prev_high + 1.0,
        low=prev_high - 0.2,
        close=prev_high + 0.8,
        volume=1000,
    )

    signal = generate_breakout_retest_signal(
        candles,
        context={"regime_state": {"ok": False, "reason": "regime_not_ok"}},
    )
    assert signal is None


def test_pullback_signal_generated_after_reclaim():
    candles = _candles(base_price=50, n=140)

    # create pullback near EMA50 then reclaim above EMA20 at last candle
    for i in range(-10, -1):
        c = candles[i]
        candles[i] = CandleData(
            ts=c.ts,
            open=c.open - 1.3,
            high=c.high - 0.8,
            low=c.low - 1.5,
            close=c.close - 1.4,
            volume=c.volume * 1.05,
        )

    pre_last = candles[-2]
    candles[-2] = CandleData(
      ts=pre_last.ts,
      open=pre_last.open - 1.1,
      high=pre_last.high - 0.8,
      low=pre_last.low - 1.3,
      close=pre_last.close - 1.0,
      volume=pre_last.volume * 1.05,
    )

    last = candles[-1]
    candles[-1] = CandleData(
        ts=last.ts,
        open=last.open - 0.3,
        high=last.high + 0.8,
        low=last.low - 0.2,
        close=last.close + 0.7,
        volume=last.volume * 1.2,
    )

    signal = generate_pullback_to_trend_signal(
        candles,
        context={
            "regime_state": {
                "ok": True,
                "close_1h": 55.0,
                "ema200_1h": 50.0,
                "ema200_slope": 1.0,
                "atr_pct_1h": 2.0,
            },
            "params": {
                "pt_ema_fast": 20,
                "pt_ema_slow": 50,
                "pt_rsi_period": 14,
                "pt_rsi_threshold": 55,
                "pt_stop_lookback": 10,
                "pt_tp_rr": 1.2,
                "pt_signal_ttl_minutes": 60,
            },
            "instrument": {"symbol": "ETH-USDC"},
        },
    )
    assert signal is not None
    assert signal.entry > signal.stop
    assert signal.take > signal.entry
    assert signal.takes == [signal.take]
    assert signal.strategy == "StrategyPullbackToTrend"


def test_pullback_signal_blocked_when_regime_not_ok():
    candles = _candles(base_price=50, n=140)
    last = candles[-1]
    candles[-1] = CandleData(
        ts=last.ts,
        open=last.open,
        high=last.high + 1.0,
        low=last.low - 0.5,
        close=last.close + 1.2,
        volume=last.volume * 1.3,
    )

    signal = generate_pullback_to_trend_signal(
        candles,
        context={"regime_state": {"ok": False, "reason": "regime_not_ok"}},
    )
    assert signal is None


def test_pullback_signal_has_no_lookahead():
    candles = _candles(base_price=50, n=140)

    baseline = generate_pullback_to_trend_signal(
        candles[:-1],
        context={"regime_state": {"ok": True, "close_1h": 55.0, "ema200_1h": 50.0, "ema200_slope": 1.0, "atr_pct_1h": 2.0}},
    )
    assert baseline is None

    for i in range(-10, -1):
        c = candles[i]
        candles[i] = CandleData(
            ts=c.ts,
            open=c.open - 1.3,
            high=c.high - 0.8,
            low=c.low - 1.5,
            close=c.close - 1.4,
            volume=c.volume * 1.05,
        )
    prev = candles[-2]
    candles[-2] = CandleData(
        ts=prev.ts,
        open=prev.open - 1.0,
        high=prev.high - 0.8,
        low=prev.low - 1.3,
        close=prev.close - 1.0,
        volume=prev.volume * 1.1,
    )
    last = candles[-1]
    candles[-1] = CandleData(
        ts=last.ts,
        open=last.open - 0.2,
        high=last.high + 0.8,
        low=last.low - 0.2,
        close=last.close + 0.7,
        volume=last.volume * 1.2,
    )

    signal = generate_pullback_to_trend_signal(
        candles,
        context={"regime_state": {"ok": True, "close_1h": 55.0, "ema200_1h": 50.0, "ema200_slope": 1.0, "atr_pct_1h": 2.0}},
    )
    assert signal is not None


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
        high = max(open_price, close) + 0.4
        low = min(open_price, close) - 0.4
        rows.append(
            CandleData(
                ts=now + timedelta(minutes=15 * idx),
                open=open_price,
                high=high,
                low=low,
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
            high = max(current_open, close, candle.high - 0.15)
            low = min(current_open, close, candle.low + 0.15)
            rows.append(
                CandleData(
                    ts=candle.ts + timedelta(minutes=5 * offset),
                    open=current_open,
                    high=high,
                    low=low,
                    close=close,
                    volume=candle.volume / 3.0,
                )
            )
            current_open = close
    return rows


def _trend_retrace_fixture() -> tuple[list[CandleData], list[CandleData]]:
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

    return candles_5m, candles_15m


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

    signal = generate_mean_reversion_hard_stop_signal(
        candles,
        context={
            "regime_state": {
                "ok": True,
                "close_1h": 120.0,
                "ema200_1h": 100.0,
                "ema200_slope": 0.1,
                "atr_pct_1h": 2.0,
            },
            "params": {
                "mr_bb_period": 20,
                "mr_bb_std": 2.0,
                "mr_rsi_period": 14,
                "mr_rsi_entry_threshold": 30.0,
                "mr_safety_ema_period": 200,
                "mr_lookback_stop": 15,
                "mr_stop_atr_buffer": 0.2,
                "mr_max_stop_pct": 0.03,
                "mr_tp_rr": 1.2,
                "mr_signal_ttl_minutes": 60,
            },
            "instrument": {"symbol": "SOL-USDC"},
        },
    )
    assert signal is not None
    assert signal.signal == "long"
    assert signal.strategy == "MeanReversionHardStop"
    assert signal.entry > signal.stop
    assert signal.take > signal.entry
    assert signal.takes == [signal.take]
    assert signal.expires_at is not None
    assert signal.expires_at > signal.created_at


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

    signal = generate_mean_reversion_hard_stop_signal(
        candles,
        context={"regime_state": {"ok": True, "close_1h": 120.0, "ema200_1h": 100.0, "ema200_slope": 0.1, "atr_pct_1h": 2.0}},
    )
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
        context={
            "regime_state": {"ok": True, "close_1h": 120.0, "ema200_1h": 100.0, "ema200_slope": 0.1, "atr_pct_1h": 2.0},
            "params": {"mr_max_stop_pct": 0.03},
        },
    )
    assert signal is None


def test_pivot_confirmation_no_lookahead():
    now = datetime.now(timezone.utc)
    candles = [
        CandleData(ts=now + timedelta(minutes=15 * idx), open=value, high=value, low=value, close=value, volume=100.0)
        for idx, value in enumerate([10.0, 11.0, 12.0, 15.0, 14.0, 13.0])
    ]

    before_confirmation = find_confirmed_pivots(candles, left_right=3)
    assert before_confirmation == []

    candles.append(
        CandleData(
            ts=now + timedelta(minutes=15 * len(candles)),
            open=12.0,
            high=12.0,
            low=12.0,
            close=12.0,
            volume=100.0,
        )
    )
    after_confirmation = find_confirmed_pivots(candles, left_right=3)
    assert any(pivot.kind == "high" and pivot.index == 3 for pivot in after_confirmation)


def test_wave_ab_detect():
    candles_15m = _trend_retrace_15m_candles()
    wave, pivots = find_latest_impulse_wave(candles_15m, left_right=3, min_impulse_atr=1.5)

    assert wave is not None
    assert len(pivots) >= 2
    assert wave.a.kind == "low"
    assert wave.b.kind == "high"
    assert round(wave.a.price, 2) == 111.60
    assert round(wave.b.price, 2) == 130.40
    assert wave.impulse_size > 0


def test_retrace_zone_trigger_generates_trend_retrace_signal():
    candles_5m, candles_15m = _trend_retrace_fixture()
    signal = generate_trend_retrace_70_signal(
        candles_5m,
        context={
            "regime_state": {
                "ok": True,
                "close_1h": 130.0,
                "ema200_1h": 118.0,
                "ema200_slope": 1.5,
                "atr_pct_1h": 2.0,
                "strength": 0.8,
            },
            "params": {
                "tr_signal_ttl_minutes": 180,
            },
            "instrument": {"symbol": "BTC-USDC"},
            "candles_15m": candles_15m,
        },
    )
    assert signal is not None
    assert signal.strategy == "StrategyTrendRetrace70"
    assert signal.signal == "long"
    assert signal.takes is not None
    assert len(signal.takes) == 2
    assert signal.takes[1] == signal.take
    assert signal.entry > signal.stop
    assert signal.takes[0] > signal.entry
    assert signal.meta["A"]["price"] < signal.meta["B"]["price"]
    assert 0.62 <= signal.meta["retrace"] <= 0.78


def test_trend_retrace_safety_guard_blocks_below_ema200():
    _, candles_15m = _trend_retrace_fixture()
    now = datetime.now(timezone.utc)
    candles_5m: list[CandleData] = []
    for idx in range(210):
        base = 126.0 - (idx * 0.01)
        candles_5m.append(
            CandleData(
                ts=now + timedelta(minutes=5 * idx),
                open=base + 0.1,
                high=base + 0.2,
                low=base - 0.2,
                close=base,
                volume=200.0,
            )
        )
    for close in [117.0, 116.8, 116.5, 116.9, 117.2, 117.4, 117.8]:
        prev_ts = candles_5m[-1].ts
        candles_5m.append(
            CandleData(
                ts=prev_ts + timedelta(minutes=5),
                open=close - 0.2,
                high=close + 0.3,
                low=close - 0.4,
                close=close,
                volume=240.0,
            )
        )

    signal = generate_trend_retrace_70_signal(
        candles_5m,
        context={
            "regime_state": {
                "ok": True,
                "close_1h": 130.0,
                "ema200_1h": 118.0,
                "ema200_slope": 1.5,
                "atr_pct_1h": 2.0,
            },
            "params": {"tr_trigger_mode": "break_high"},
            "candles_15m": candles_15m,
        },
    )
    assert signal is None


def test_trend_retrace_max_stop_pct_filter():
    candles_5m, candles_15m = _trend_retrace_fixture()
    deep = candles_5m[-6]
    candles_5m[-6] = CandleData(
        ts=deep.ts,
        open=deep.open,
        high=deep.high,
        low=deep.low - 12.0,
        close=deep.close,
        volume=deep.volume,
    )

    signal = generate_trend_retrace_70_signal(
        candles_5m,
        context={
            "regime_state": {
                "ok": True,
                "close_1h": 130.0,
                "ema200_1h": 118.0,
                "ema200_slope": 1.5,
                "atr_pct_1h": 2.0,
            },
            "params": {"tr_max_stop_pct": 0.04},
            "candles_15m": candles_15m[:47],
        },
    )
    assert signal is None
