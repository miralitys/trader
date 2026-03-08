from datetime import datetime, timedelta, timezone

from app.models.entities import Backtest, Candle, Instrument
from app.services import backtest_service
from app.services.backtest_service import (
    RawTrade,
    SimTrade,
    UniverseCandidate,
    _build_backtest_diagnostics,
    _select_top5_with_history,
    fail_stale_backtests,
    inspect_backtest_history_readiness,
    run_backtest,
)


def test_universe_selection_excludes_near_zero_coverage_and_replaces_below_target():
    candidates = [
        UniverseCandidate("A-USDC", "A-USDC", 1000.0, None, None, 0.001),
        UniverseCandidate("B-USDC", "B-USDC", 900.0, None, None, 0.25),
        UniverseCandidate("C-USDC", "C-USDC", 800.0, None, None, 0.22),
        UniverseCandidate("D-USDC", "D-USDC", 700.0, None, None, 0.21),
        UniverseCandidate("E-USDC", "E-USDC", 600.0, None, None, 0.04),
        UniverseCandidate("F-USDC", "F-USDC", 500.0, None, None, 0.30),
        UniverseCandidate("G-USDC", "G-USDC", 400.0, None, None, 0.28),
    ]

    selected = _select_top5_with_history(
        candidates=candidates,
        target_coverage_ratio=0.20,
        min_coverage_ratio=0.03,
    )

    selected_symbols = {item.symbol for item in selected}
    assert "A-USDC" not in selected_symbols
    assert len(selected) == 5
    assert all(item.coverage_ratio >= 0.03 for item in selected)
    assert all(item.selected for item in selected)

    low_floor = next(item for item in candidates if item.symbol == "A-USDC")
    assert low_floor.selection_reason == "excluded_coverage_below_floor"

    replaced = next(item for item in candidates if item.symbol == "E-USDC")
    assert replaced.selection_reason == "excluded_below_target"
    assert replaced.selected is False


def test_universe_selection_keeps_below_target_when_no_better_candidates():
    candidates = [
        UniverseCandidate("A-USDC", "A-USDC", 1000.0, None, None, 0.10),
        UniverseCandidate("B-USDC", "B-USDC", 900.0, None, None, 0.09),
        UniverseCandidate("C-USDC", "C-USDC", 800.0, None, None, 0.08),
        UniverseCandidate("D-USDC", "D-USDC", 700.0, None, None, 0.07),
        UniverseCandidate("E-USDC", "E-USDC", 600.0, None, None, 0.06),
    ]

    selected = _select_top5_with_history(
        candidates=candidates,
        target_coverage_ratio=0.20,
        min_coverage_ratio=0.03,
    )

    assert len(selected) == 5
    assert all(item.selected for item in selected)
    assert all(item.selection_reason == "kept_below_target_no_better_candidate" for item in selected)


def test_fail_stale_backtests_marks_only_old_running(db_session):
    now = datetime.now(timezone.utc)
    base_start = now - timedelta(days=2)
    base_end = now - timedelta(days=1)

    stale_running = Backtest(
        strategy="StrategyBreakoutRetest",
        universe_json=[],
        start_ts=base_start,
        end_ts=base_end,
        params_json={},
        metrics_json={},
        equity_curve_json=[],
        status="running",
        created_at=now - timedelta(minutes=61),
    )
    fresh_running = Backtest(
        strategy="StrategyBreakoutRetest",
        universe_json=[],
        start_ts=base_start,
        end_ts=base_end,
        params_json={},
        metrics_json={},
        equity_curve_json=[],
        status="running",
        created_at=now - timedelta(minutes=15),
    )
    queued_old = Backtest(
        strategy="StrategyBreakoutRetest",
        universe_json=[],
        start_ts=base_start,
        end_ts=base_end,
        params_json={},
        metrics_json={},
        equity_curve_json=[],
        status="queued",
        created_at=now - timedelta(minutes=120),
    )

    db_session.add_all([stale_running, fresh_running, queued_old])
    db_session.commit()

    result = fail_stale_backtests(db_session, stale_minutes=60)

    db_session.refresh(stale_running)
    db_session.refresh(fresh_running)
    db_session.refresh(queued_old)

    assert result["stale_marked_failed"] == 1
    assert stale_running.status == "failed"
    assert "stale_timeout" in stale_running.metrics_json["error"]
    assert fresh_running.status == "running"
    assert queued_old.status == "queued"


def test_history_readiness_auto_excludes_late_symbols_for_long_window(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    start_ts = now - timedelta(days=730)

    symbols = ["NEW", "OLD1", "OLD2", "OLD3", "OLD4", "OLD5"]
    for sym in symbols:
        instrument = Instrument(
            symbol=f"{sym}-USDC",
            base=sym,
            quote="USDC",
            product_id=f"{sym}-USDC",
            status="online",
            min_size=0.0001,
            size_increment=0.0001,
            price_increment=0.0001,
        )
        db_session.add(instrument)
        db_session.flush()

        coverage_days = 220
        first_ts = now - timedelta(days=coverage_days)
        db_session.add(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=first_ts,
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.0,
                volume=100.0,
                source="test",
            )
        )
        db_session.add(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=now,
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.0,
                volume=100.0,
                source="test",
            )
        )
    db_session.commit()

    monkeypatch.setattr(
        backtest_service.coinbase_client,
        "get_products",
        lambda: [
            {
                "product_id": "NEW-USDC",
                "base_currency_id": "NEW",
                "quote_currency_id": "USDC",
                "status": "online",
                "trading_disabled": False,
                "quote_volume_24h": "9000000",
                "new_at": now.isoformat(),
            },
            {
                "product_id": "OLD1-USDC",
                "base_currency_id": "OLD1",
                "quote_currency_id": "USDC",
                "status": "online",
                "trading_disabled": False,
                "quote_volume_24h": "8000000",
                "new_at": (start_ts - timedelta(days=30)).isoformat(),
            },
            {
                "product_id": "OLD2-USDC",
                "base_currency_id": "OLD2",
                "quote_currency_id": "USDC",
                "status": "online",
                "trading_disabled": False,
                "quote_volume_24h": "7000000",
                "new_at": (start_ts - timedelta(days=30)).isoformat(),
            },
            {
                "product_id": "OLD3-USDC",
                "base_currency_id": "OLD3",
                "quote_currency_id": "USDC",
                "status": "online",
                "trading_disabled": False,
                "quote_volume_24h": "6000000",
                "new_at": (start_ts - timedelta(days=30)).isoformat(),
            },
            {
                "product_id": "OLD4-USDC",
                "base_currency_id": "OLD4",
                "quote_currency_id": "USDC",
                "status": "online",
                "trading_disabled": False,
                "quote_volume_24h": "5000000",
                "new_at": (start_ts - timedelta(days=30)).isoformat(),
            },
            {
                "product_id": "OLD5-USDC",
                "base_currency_id": "OLD5",
                "quote_currency_id": "USDC",
                "status": "online",
                "trading_disabled": False,
                "quote_volume_24h": "4000000",
                "new_at": (start_ts - timedelta(days=30)).isoformat(),
            },
        ],
    )

    readiness = inspect_backtest_history_readiness(
        db_session,
        strategy="StrategyTrendRetrace70",
        start_ts=start_ts,
        end_ts=now,
        params={
            "input_tickers": symbols,
            "history_min_coverage_ratio": 0.005,
            "history_target_coverage_ratio": 0.005,
            "history_required_coverage_ratio": 0.20,
        },
    )

    assert readiness["ready"] is True
    assert readiness["coverage"]["auto_enforced_floor"] is True
    assert readiness["coverage"]["listing_age_filter_enabled"] is True
    assert readiness["coverage"]["min_ratio"] >= 0.20
    assert "NEW-USDC" not in readiness["universe"]["selected_top5"]
    assert len(readiness["universe"]["selected_top5"]) == 5


def test_run_backtest_returns_cancelled_without_processing(db_session):
    now = datetime.now(timezone.utc)
    row = Backtest(
        strategy="StrategyTrendRetrace70",
        universe_json=[],
        start_ts=now - timedelta(days=30),
        end_ts=now,
        params_json={},
        metrics_json={},
        equity_curve_json=[],
        status="cancelled",
        created_at=now,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    result = run_backtest(db_session, row.id)

    assert result.status == "cancelled"
    assert result.metrics_json.get("cancelled") is True


def test_history_readiness_rejects_sparse_history_even_when_degraded_requested(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    start_ts = now - timedelta(days=730)

    symbols = ["BTC", "ETH", "SOL", "LINK", "AVAX"]
    for sym in symbols:
        instrument = Instrument(
            symbol=f"{sym}-USDC",
            base=sym,
            quote="USDC",
            product_id=f"{sym}-USDC",
            status="online",
            min_size=0.0001,
            size_increment=0.0001,
            price_increment=0.0001,
        )
        db_session.add(instrument)
        db_session.flush()

        first_ts = now - timedelta(days=5)
        db_session.add(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=first_ts,
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.0,
                volume=100.0,
                source="test",
            )
        )
        db_session.add(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=now,
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.0,
                volume=100.0,
                source="test",
            )
        )
    db_session.commit()

    monkeypatch.setattr(
        backtest_service.coinbase_client,
        "get_products",
        lambda: [
            {
                "product_id": f"{sym}-USDC",
                "base_currency_id": sym,
                "quote_currency_id": "USDC",
                "status": "online",
                "trading_disabled": False,
                "quote_volume_24h": "1000000",
            }
            for sym in symbols
        ],
    )

    readiness = inspect_backtest_history_readiness(
        db_session,
        strategy="StrategyBreakoutRetest",
        start_ts=start_ts,
        end_ts=now,
        params={
            "input_tickers": symbols,
            "history_min_coverage_ratio": 0.5,
            "history_target_coverage_ratio": 0.7,
            "history_required_coverage_ratio": 0.5,
            "history_allow_degraded": True,
        },
    )

    assert readiness["ready"] is True
    assert readiness["reason"] == "ok"
    assert readiness["coverage"]["degraded_mode_applied"] is True
    assert readiness["coverage"]["allow_degraded_history"] is True


def test_build_backtest_diagnostics_reports_costs_and_anomalies():
    now = datetime.now(timezone.utc)
    raw_trades = [
        RawTrade(
            symbol="BTC-USDC",
            signal_ts=now,
            entry_ts=now + timedelta(minutes=5),
            exit_ts=now + timedelta(minutes=35),
            entry_raw=100.0,
            exit_raw=102.0,
            stop_price=99.0,
            exit_reason="take_profit",
            duration_min=30.0,
        ),
        RawTrade(
            symbol="BTC-USDC",
            signal_ts=now + timedelta(minutes=60),
            entry_ts=now + timedelta(minutes=65),
            exit_ts=now + timedelta(minutes=125),
            entry_raw=101.0,
            exit_raw=100.0,
            stop_price=99.5,
            exit_reason="timeout",
            duration_min=60.0,
        ),
    ]
    sim_trades = [
        SimTrade(
            symbol="BTC-USDC",
            signal_ts=raw_trades[0].signal_ts,
            entry_ts=raw_trades[0].entry_ts,
            exit_ts=raw_trades[0].exit_ts,
            entry_raw=100.0,
            exit_raw=102.0,
            entry_exec=100.1,
            exit_exec=101.9,
            fees_paid=0.5,
            entry_fee_paid=0.25,
            exit_fee_paid=0.25,
            entry_slippage_paid=0.1,
            exit_slippage_paid=0.1,
            pnl_quote=1.3,
            pnl_r=1.3,
            duration_min=30.0,
            exit_reason="take_profit",
        ),
        SimTrade(
            symbol="BTC-USDC",
            signal_ts=raw_trades[1].signal_ts,
            entry_ts=raw_trades[1].entry_ts,
            exit_ts=raw_trades[1].exit_ts,
            entry_raw=101.0,
            exit_raw=100.0,
            entry_exec=101.1,
            exit_exec=99.9,
            fees_paid=0.5,
            entry_fee_paid=0.25,
            exit_fee_paid=0.25,
            entry_slippage_paid=0.1,
            exit_slippage_paid=0.1,
            pnl_quote=-1.7,
            pnl_r=-1.13,
            duration_min=60.0,
            exit_reason="timeout",
        ),
    ]

    diagnostics = _build_backtest_diagnostics(raw_trades=raw_trades, sim_trades=sim_trades)

    assert diagnostics["signals_generated"] == 2
    assert diagnostics["trades_executed"] == 2
    assert diagnostics["stability"]["timeout_exits"] == 1
    assert diagnostics["entry_exit_validation"]["sequence_ok"] is True
    assert diagnostics["execution_costs"]["total_fees_quote"] == 1.0
    assert diagnostics["execution_costs"]["total_slippage_quote"] == 0.4
