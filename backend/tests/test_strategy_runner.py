from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.entities import Candle, Instrument, Setting, Signal, Trade, User
from app.services import strategy_runner as strategy_runner_service
from app.strategies.types import CandleData, SignalPlan
from app.schemas.settings import DEFAULT_FEES, DEFAULT_RISK, DEFAULT_STRATEGY, DEFAULT_UNIVERSE
from app.services.strategy_runner import run_strategy_cycle


def _closed_candles(now: datetime, n: int = 260) -> list[CandleData]:
    rows: list[CandleData] = []
    for i in range(n):
        price = 100 + i * 0.1
        rows.append(
            CandleData(
                ts=now - timedelta(minutes=5 * (n - i)),
                open=price - 0.1,
                high=price + 0.2,
                low=price - 0.2,
                close=price,
                volume=100 + i,
            )
        )
    return rows


def _plan(strategy: str, created_at: datetime) -> SignalPlan:
    return SignalPlan(
        symbol="BTC-USDC",
        strategy=strategy,
        timeframe="5m",
        signal="long",
        entry=101.0,
        stop=99.0,
        take=103.4,
        takes=[103.4],
        confidence=0.7,
        reason=f"{strategy} candidate",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=60),
        status="active",
        meta={"take": [103.4]},
    )


def test_run_strategy_cycle_generates_breakout_retest_signal(db_session):
    now = datetime.now(timezone.utc)
    instrument = Instrument(
        symbol="BTC-USDC",
        base="BTC",
        quote="USDC",
        product_id="BTC-USDC",
        status="online",
        min_size=0.0001,
        size_increment=0.0001,
        price_increment=0.01,
    )
    user = User(email="runner@example.com", password_hash="x", role="admin")
    setting = Setting(
        user=user,
        paper_enabled=True,
        live_enabled=False,
        live_confirmed=False,
        risk_params_json=DEFAULT_RISK.copy(),
        strategy_params_json={
            **DEFAULT_STRATEGY,
            "trade_only_strategy": "StrategyBreakoutRetest",
            "confirm_15m": True,
        },
        universe_json={**DEFAULT_UNIVERSE, "top_symbols": ["BTC-USDC"]},
        fees_json=DEFAULT_FEES.copy(),
        kill_switch_paused=False,
        strict_mode=False,
    )
    db_session.add_all([user, instrument, setting])
    db_session.commit()
    db_session.refresh(instrument)

    candles: list[Candle] = []

    for i in range(260):
        close = 100 + i * 0.4
        ts = now - timedelta(hours=260 - i)
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="1h",
                ts=ts,
                open=close - 0.3,
                high=close + 0.6,
                low=close - 0.6,
                close=close,
                volume=1000 + i,
                source="test",
            )
        )

    for i in range(120):
        close = 150 + i * 0.12
        ts = now - timedelta(minutes=15 * (120 - i))
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="15m",
                ts=ts,
                open=close - 0.1,
                high=close + 0.3,
                low=close - 0.3,
                close=close,
                volume=600 + i,
                source="test",
            )
        )

    breakout_anchor = 200.0
    for i in range(60):
        ts = now - timedelta(minutes=5 * (60 - i))
        close = breakout_anchor + i * 0.08
        high = close + 0.25
        low = close - 0.25
        if i == 59:
            prior_high = breakout_anchor + 58 * 0.08 + 0.25
            close = prior_high + 0.75
            high = close + 0.2
            low = prior_high - 0.15
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=ts,
                open=close - 0.1,
                high=high,
                low=low,
                close=close,
                volume=5000 if i == 59 else 500 + i,
                source="test",
            )
        )

    db_session.add_all(candles)
    db_session.commit()

    result = run_strategy_cycle(db_session, setting)

    assert result["generated"] == 1
    signal = db_session.query(Signal).one()
    assert signal.strategy == "StrategyBreakoutRetest"
    assert signal.signal == "long"
    assert signal.entry > signal.stop
    assert signal.take > signal.entry
    assert signal.meta_json["partial_tp"] < signal.meta_json["final_tp"]
    assert signal.meta_json["take"] == [signal.meta_json["partial_tp"], signal.meta_json["final_tp"]]


def test_run_strategy_cycle_generates_pullback_to_trend_signal(db_session):
    now = datetime.now(timezone.utc)
    instrument = Instrument(
        symbol="ETH-USDC",
        base="ETH",
        quote="USDC",
        product_id="ETH-USDC",
        status="online",
        min_size=0.0001,
        size_increment=0.0001,
        price_increment=0.01,
    )
    user = User(email="runner-pullback@example.com", password_hash="x", role="admin")
    setting = Setting(
        user=user,
        paper_enabled=True,
        live_enabled=False,
        live_confirmed=False,
        risk_params_json=DEFAULT_RISK.copy(),
        strategy_params_json={
            **DEFAULT_STRATEGY,
            "trade_only_strategy": "StrategyPullbackToTrend",
            "confirm_15m": True,
            "pt_rsi_threshold": 55.0,
        },
        universe_json={**DEFAULT_UNIVERSE, "top_symbols": ["ETH-USDC"]},
        fees_json=DEFAULT_FEES.copy(),
        kill_switch_paused=False,
        strict_mode=False,
    )
    db_session.add_all([user, instrument, setting])
    db_session.commit()
    db_session.refresh(instrument)

    candles: list[Candle] = []

    for i in range(260):
        close = 100 + i * 0.35
        ts = now - timedelta(hours=260 - i)
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="1h",
                ts=ts,
                open=close - 0.2,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=900 + i,
                source="test",
            )
        )

    for i in range(120):
        close = 160 + i * 0.10
        ts = now - timedelta(minutes=15 * (120 - i))
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="15m",
                ts=ts,
                open=close - 0.1,
                high=close + 0.3,
                low=close - 0.3,
                close=close,
                volume=700 + i,
                source="test",
            )
        )

    base = 200.0
    for i in range(80):
        ts = now - timedelta(minutes=5 * (80 - i))
        close = base + i * 0.10
        if 68 <= i <= 78:
            close -= 1.6
        if i == 78:
            close -= 0.5
        if i == 79:
            close += 0.8
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=ts,
                open=close - 0.12,
                high=close + 0.25,
                low=close - 0.35,
                close=close,
                volume=1400 if i == 79 else 500 + i,
                source="test",
            )
        )

    db_session.add_all(candles)
    db_session.commit()

    result = run_strategy_cycle(db_session, setting)

    assert result["generated"] == 1
    signal = db_session.query(Signal).one()
    assert signal.strategy == "StrategyPullbackToTrend"
    assert signal.signal == "long"
    assert signal.entry > signal.stop
    assert signal.take > signal.entry
    assert signal.meta_json["take"] == [signal.take]


def test_run_strategy_cycle_generates_mean_reversion_signal(db_session):
    now = datetime.now(timezone.utc)
    instrument = Instrument(
        symbol="SOL-USDC",
        base="SOL",
        quote="USDC",
        product_id="SOL-USDC",
        status="online",
        min_size=0.0001,
        size_increment=0.0001,
        price_increment=0.01,
    )
    user = User(email="runner-mean-reversion@example.com", password_hash="x", role="admin")
    setting = Setting(
        user=user,
        paper_enabled=True,
        live_enabled=False,
        live_confirmed=False,
        risk_params_json=DEFAULT_RISK.copy(),
        strategy_params_json={
            **DEFAULT_STRATEGY,
            "trade_only_strategy": "MeanReversionHardStop",
            "confirm_15m": True,
        },
        universe_json={**DEFAULT_UNIVERSE, "top_symbols": ["SOL-USDC"]},
        fees_json=DEFAULT_FEES.copy(),
        kill_switch_paused=False,
        strict_mode=False,
    )
    db_session.add_all([user, instrument, setting])
    db_session.commit()
    db_session.refresh(instrument)

    candles: list[Candle] = []

    for i in range(260):
        close = 90 + i * 0.30
        ts = now - timedelta(hours=260 - i)
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="1h",
                ts=ts,
                open=close - 0.2,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=900 + i,
                source="test",
            )
        )

    for i in range(120):
        close = 130 + i * 0.09
        ts = now - timedelta(minutes=15 * (120 - i))
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="15m",
                ts=ts,
                open=close - 0.1,
                high=close + 0.3,
                low=close - 0.3,
                close=close,
                volume=650 + i,
                source="test",
            )
        )

    base = 170.0
    for i in range(260):
        close = base + i * 0.12
        if i == 258:
            close -= 4.2
        if i == 259:
            close = base + 258 * 0.12 - 1.4
        ts = now - timedelta(minutes=5 * (260 - i))
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=ts,
                open=close - 0.15,
                high=close + (0.3 if i < 259 else 1.1),
                low=close - (0.3 if i < 258 else 0.4),
                close=close,
                volume=1400 if i >= 258 else 300 + i,
                source="test",
            )
        )

    db_session.add_all(candles)
    db_session.commit()

    result = run_strategy_cycle(db_session, setting)

    assert result["generated"] == 1
    signal = db_session.query(Signal).one()
    assert signal.strategy == "MeanReversionHardStop"
    assert signal.signal == "long"
    assert signal.entry > signal.stop
    assert signal.take > signal.entry
    assert signal.meta_json["take"] == [signal.take]


def test_run_strategy_cycle_applies_priority_when_multiple_candidates(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    instrument = Instrument(
        symbol="BTC-USDC",
        base="BTC",
        quote="USDC",
        product_id="BTC-USDC",
        status="online",
        min_size=0.0001,
        size_increment=0.0001,
        price_increment=0.01,
    )
    user = User(email="priority@example.com", password_hash="x", role="admin")
    setting = Setting(
        user=user,
        paper_enabled=True,
        live_enabled=False,
        live_confirmed=False,
        risk_params_json=DEFAULT_RISK.copy(),
        strategy_params_json={**DEFAULT_STRATEGY, "trade_only_strategy": "both"},
        universe_json={**DEFAULT_UNIVERSE, "top_symbols": ["BTC-USDC"]},
        fees_json=DEFAULT_FEES.copy(),
        kill_switch_paused=False,
        strict_mode=False,
    )
    db_session.add_all([user, instrument, setting])
    db_session.commit()

    candles = _closed_candles(now)
    suppressed_events: list[dict] = []

    monkeypatch.setattr(strategy_runner_service, "_load_candles", lambda *args, **kwargs: candles)
    monkeypatch.setattr(strategy_runner_service, "_regime_filter", lambda *args, **kwargs: (True, {"ema200_slope": 1.0}))
    monkeypatch.setattr(strategy_runner_service, "_confirm_15m", lambda *args, **kwargs: (True, {"ema50_15m": 100.0}))
    monkeypatch.setattr(strategy_runner_service, "generate_breakout_retest_signal", lambda *args, **kwargs: _plan("StrategyBreakoutRetest", candles[-1].ts))
    monkeypatch.setattr(strategy_runner_service, "generate_pullback_to_trend_signal", lambda *args, **kwargs: _plan("StrategyPullbackToTrend", candles[-1].ts))
    monkeypatch.setattr(strategy_runner_service, "generate_mean_reversion_hard_stop_signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(strategy_runner_service, "generate_trend_retrace_70_signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        strategy_runner_service,
        "publish_event",
        lambda event_type, payload: suppressed_events.append({"type": event_type, "payload": payload}),
    )

    result = run_strategy_cycle(db_session, setting)

    assert result["generated"] == 1
    assert result["suppressed_due_priority"] == 1
    signal = db_session.query(Signal).one()
    assert signal.strategy == "StrategyBreakoutRetest"
    assert any(
        item["type"] == "signal_suppressed"
        and item["payload"]["strategy"] == "StrategyPullbackToTrend"
        for item in suppressed_events
    )


def test_run_strategy_cycle_blocks_when_active_signal_exists(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    instrument = Instrument(
        symbol="BTC-USDC",
        base="BTC",
        quote="USDC",
        product_id="BTC-USDC",
        status="online",
        min_size=0.0001,
        size_increment=0.0001,
        price_increment=0.01,
    )
    user = User(email="active-block@example.com", password_hash="x", role="admin")
    setting = Setting(
        user=user,
        paper_enabled=True,
        live_enabled=False,
        live_confirmed=False,
        risk_params_json=DEFAULT_RISK.copy(),
        strategy_params_json={**DEFAULT_STRATEGY, "trade_only_strategy": "StrategyBreakoutRetest"},
        universe_json={**DEFAULT_UNIVERSE, "top_symbols": ["BTC-USDC"]},
        fees_json=DEFAULT_FEES.copy(),
        kill_switch_paused=False,
        strict_mode=False,
    )
    active_signal = Signal(
        instrument=instrument,
        strategy="StrategyBreakoutRetest",
        timeframe="5m",
        signal="long",
        entry=100.0,
        stop=99.0,
        take=102.0,
        confidence=0.8,
        reason="existing",
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=30),
        status="active",
        meta_json={},
    )
    db_session.add_all([user, instrument, setting, active_signal])
    db_session.commit()

    candles = _closed_candles(now)
    suppressed_events: list[dict] = []
    monkeypatch.setattr(strategy_runner_service, "_load_candles", lambda *args, **kwargs: candles)
    monkeypatch.setattr(
        strategy_runner_service,
        "generate_breakout_retest_signal",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("strategy should not be called")),
    )
    monkeypatch.setattr(
        strategy_runner_service,
        "publish_event",
        lambda event_type, payload: suppressed_events.append({"type": event_type, "payload": payload}),
    )

    result = run_strategy_cycle(db_session, setting)

    assert result["generated"] == 0
    assert result["suppressed_due_active_signal"] == 1
    assert db_session.query(Signal).count() == 1
    assert any(item["type"] == "signal_suppressed" for item in suppressed_events)


def test_run_strategy_cycle_respects_symbol_cooldown(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    instrument = Instrument(
        symbol="BTC-USDC",
        base="BTC",
        quote="USDC",
        product_id="BTC-USDC",
        status="online",
        min_size=0.0001,
        size_increment=0.0001,
        price_increment=0.01,
    )
    user = User(email="cooldown@example.com", password_hash="x", role="admin")
    setting = Setting(
        user=user,
        paper_enabled=True,
        live_enabled=False,
        live_confirmed=False,
        risk_params_json=DEFAULT_RISK.copy(),
        strategy_params_json={
            **DEFAULT_STRATEGY,
            "trade_only_strategy": "StrategyBreakoutRetest",
            "strategy_signal_cooldown_minutes": 30,
        },
        universe_json={**DEFAULT_UNIVERSE, "top_symbols": ["BTC-USDC"]},
        fees_json=DEFAULT_FEES.copy(),
        kill_switch_paused=False,
        strict_mode=False,
    )
    recent_trade = Trade(
        mode="paper",
        instrument=instrument,
        side="buy",
        qty_base=1.0,
        qty_quote=100.0,
        entry_price=100.0,
        exit_price=101.0,
        fees=0.0,
        pnl=1.0,
        opened_at=now - timedelta(minutes=20),
        closed_at=now - timedelta(minutes=10),
        status="closed",
        order_ids_json={},
        meta_json={"strategy": "StrategyBreakoutRetest"},
    )
    db_session.add_all([user, instrument, setting, recent_trade])
    db_session.commit()

    candles = _closed_candles(now)
    suppressed_events: list[dict] = []
    monkeypatch.setattr(strategy_runner_service, "_load_candles", lambda *args, **kwargs: candles)
    monkeypatch.setattr(
        strategy_runner_service,
        "generate_breakout_retest_signal",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("strategy should not be called")),
    )
    monkeypatch.setattr(
        strategy_runner_service,
        "publish_event",
        lambda event_type, payload: suppressed_events.append({"type": event_type, "payload": payload}),
    )

    result = run_strategy_cycle(db_session, setting)

    assert result["generated"] == 0
    assert result["suppressed_due_cooldown"] == 1
    assert db_session.query(Signal).count() == 0
    assert any(
        item["type"] == "signal_suppressed" and item["payload"]["reason"] == "symbol_cooldown_active"
        for item in suppressed_events
    )
