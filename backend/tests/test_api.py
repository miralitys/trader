from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.api.routes import backtests as backtests_route
from app.models.entities import Backtest, Candle, EquitySnapshot, Instrument, Order, Position, Trade
from app.services import backtest_service


def test_auth_flow_and_me(client):
    signup = client.post(
        "/api/auth/signup",
        json={"email": "api-user@example.com", "password": "password-12345"},
    )
    assert signup.status_code == 200

    login = client.post(
        "/api/auth/login",
        json={"email": "api-user@example.com", "password": "password-12345"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "api-user@example.com"


def test_settings_get_put(client, auth_header):
    before = client.get("/api/settings", headers=auth_header)
    assert before.status_code == 200
    assert before.json()["paper_enabled"] is True

    updated = client.put(
        "/api/settings",
        headers=auth_header,
        json={"paper_enabled": False, "risk_params_json": {"risk_per_trade_pct": 0.5}},
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["paper_enabled"] is False
    assert payload["risk_params_json"]["risk_per_trade_pct"] == 0.5


def test_settings_include_breakout_retest_2_preset(client, auth_header):
    resp = client.get("/api/settings", headers=auth_header)
    assert resp.status_code == 200

    strategy_params = resp.json()["strategy_params_json"]
    presets = strategy_params.get("strategy_presets", [])
    target = next((item for item in presets if item.get("name") == "StrategyBreakoutRetest 2"), None)

    assert target is not None
    assert target["base_strategy"] == "StrategyBreakoutRetest"
    assert target["backtest_params"]["history_min_coverage_ratio"] == 0.005
    assert target["backtest_params"]["history_target_coverage_ratio"] == 0.005
    assert target["backtest_params"]["history_required_coverage_ratio"] == 0.005
    for ticker in ["BTC", "ETH", "SOL", "LINK", "AVAX"]:
        assert ticker in target["backtest_params"]["input_tickers"]


def test_settings_include_trend_retrace_70_preset(client, auth_header):
    resp = client.get("/api/settings", headers=auth_header)
    assert resp.status_code == 200

    strategy_params = resp.json()["strategy_params_json"]
    presets = strategy_params.get("strategy_presets", [])
    target = next((item for item in presets if item.get("name") == "StrategyTrendRetrace70"), None)

    assert target is not None
    assert target["base_strategy"] == "StrategyTrendRetrace70"
    assert target["backtest_params"]["history_min_coverage_ratio"] == 0.005
    assert target["backtest_params"]["history_target_coverage_ratio"] == 0.005
    assert target["backtest_params"]["history_required_coverage_ratio"] == 0.2
    for ticker in ["BTC", "ETH", "SOL", "LINK", "AVAX"]:
        assert ticker in target["backtest_params"]["input_tickers"]


def test_candles_endpoint(client, auth_header, db_session):
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
    db_session.add(instrument)
    db_session.commit()
    db_session.refresh(instrument)

    now = datetime.now(timezone.utc)
    for i in range(10):
        db_session.add(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=now + timedelta(minutes=5 * i),
                open=100 + i,
                high=101 + i,
                low=99 + i,
                close=100.5 + i,
                volume=1000 + i,
                source="test",
            )
        )
    db_session.commit()

    resp = client.get(
        "/api/candles",
        params={"symbol": "BTC-USDC", "tf": "5m"},
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 10


def test_backtest_history_readiness_endpoint_returns_not_ready_for_sparse_data(
    client, auth_header, db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    start_ts = now - timedelta(days=730)
    end_ts = now

    instrument = Instrument(
        symbol="ONDO-USDC",
        base="ONDO",
        quote="USDC",
        product_id="ONDO-USDC",
        status="online",
        min_size=0.0001,
        size_increment=0.0001,
        price_increment=0.0001,
    )
    db_session.add(instrument)
    db_session.commit()
    db_session.refresh(instrument)
    monkeypatch.setattr(
        backtest_service.coinbase_client,
        "get_products",
        lambda: [
            {
                "product_id": "ONDO-USDC",
                "base_currency_id": "ONDO",
                "quote_currency_id": "USDC",
                "status": "online",
                "trading_disabled": False,
                "quote_volume_24h": "1000000",
            }
        ],
    )

    for i in range(288):
        ts = end_ts - timedelta(minutes=5 * (287 - i))
        db_session.add(
            Candle(
                instrument_id=instrument.id,
                timeframe="5m",
                ts=ts,
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.0,
                volume=1000.0,
                source="test",
            )
        )
    db_session.commit()

    resp = client.post(
        "/api/backtests/history-readiness",
        headers=auth_header,
        json={
            "strategy": "StrategyTrendRetrace70",
            "start_ts": start_ts.isoformat(),
            "end_ts": end_ts.isoformat(),
            "params": {"input_tickers": ["ONDO"]},
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ready"] is False
    assert payload["reason"] in {"insufficient_common_history", "no_symbols_with_min_coverage"}
    assert payload["coverage"]["required_ratio"] >= 0.2


def test_cancel_backtest_endpoint_marks_cancelled_and_revokes_task(client, auth_header, db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    row = Backtest(
        strategy="StrategyTrendRetrace70",
        universe_json=[],
        start_ts=now - timedelta(days=30),
        end_ts=now,
        params_json={"celery_task_id": "task-123"},
        metrics_json={},
        equity_curve_json=[],
        status="running",
        created_at=now,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    called: dict[str, str] = {}

    def _fake_revoke(task_id, terminate=False, signal=None):
        called["task_id"] = task_id
        called["terminate"] = str(terminate)
        called["signal"] = str(signal)

    monkeypatch.setattr(backtests_route.celery_app.control, "revoke", _fake_revoke)

    resp = client.post(f"/api/backtests/{row.id}/cancel", headers=auth_header)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "cancelled"

    db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.metrics_json.get("cancel_requested") is True
    assert row.metrics_json.get("error") == "cancelled_by_user"
    assert called["task_id"] == "task-123"
    assert called["terminate"] == "True"
    assert called["signal"] == "SIGTERM"


def test_run_all_backtests_endpoint_creates_four_runs(client, auth_header, db_session, monkeypatch):
    def _fake_send_task(name, args):
        return SimpleNamespace(id=f"task-{args[0]}")

    monkeypatch.setattr(backtests_route.celery_app, "send_task", _fake_send_task)

    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=730)

    resp = client.post(
        "/api/backtests/run-all",
        headers=auth_header,
        json={
            "start_ts": start_ts.isoformat(),
            "end_ts": end_ts.isoformat(),
        },
    )
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["batch_id"]
    assert payload["enqueue_errors"] == {}
    assert len(payload["backtests"]) == 4
    assert set(payload["strategies"]) == set(backtests_route.ALL_BACKTEST_STRATEGIES)

    for row in payload["backtests"]:
        assert row["status"] == "queued"
        db_row = db_session.get(Backtest, row["id"])
        assert db_row is not None
        assert db_row.strategy in backtests_route.ALL_BACKTEST_STRATEGIES
        assert db_row.params_json.get("batch_id") == payload["batch_id"]
        assert str(db_row.params_json.get("celery_task_id", "")).startswith("task-")


def test_run_all_backtests_endpoint_supports_subset_and_existing_batch_id(client, auth_header, db_session, monkeypatch):
    def _fake_send_task(name, args):
        return SimpleNamespace(id=f"task-{args[0]}")

    monkeypatch.setattr(backtests_route.celery_app, "send_task", _fake_send_task)

    existing_batch_id = "batch-fixed-001"
    resp = client.post(
        "/api/backtests/run-all",
        headers=auth_header,
        json={
            "batch_id": existing_batch_id,
            "strategies": ["MeanReversionHardStop", "StrategyTrendRetrace70"],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["batch_id"] == existing_batch_id
    assert payload["enqueue_errors"] == {}
    assert payload["strategies"] == ["MeanReversionHardStop", "StrategyTrendRetrace70"]
    assert len(payload["backtests"]) == 2
    for row in payload["backtests"]:
        db_row = db_session.get(Backtest, row["id"])
        assert db_row is not None
        assert db_row.params_json.get("batch_id") == existing_batch_id


def test_run_all_backtests_endpoint_rejects_unknown_strategy(client, auth_header):
    resp = client.post(
        "/api/backtests/run-all",
        headers=auth_header,
        json={"strategies": ["UnknownStrategy"]},
    )
    assert resp.status_code == 400
    assert "Unsupported strategy" in resp.json()["detail"]


def test_backtest_batch_stats_endpoint_aggregates_metrics(client, auth_header, db_session):
    now = datetime.now(timezone.utc)
    batch_id = "batch-stats-001"
    requested_start = now - timedelta(days=730)
    requested_end = now

    rows = [
        Backtest(
            strategy="StrategyBreakoutRetest",
            universe_json=["BTC-USDC"],
            start_ts=requested_start,
            end_ts=requested_end,
            params_json={
                "batch_id": batch_id,
                "batch_requested_start_ts": requested_start.isoformat(),
                "batch_requested_end_ts": requested_end.isoformat(),
            },
            metrics_json={
                "base": {"trades": 11, "winrate": 0.55, "profit_factor": 1.3},
                "stress_1_5x": {"profit_factor": 1.1},
                "stress_2_0x": {"profit_factor": 0.9},
            },
            equity_curve_json=[],
            status="completed",
            created_at=now - timedelta(minutes=3),
        ),
        Backtest(
            strategy="StrategyPullbackToTrend",
            universe_json=["ETH-USDC"],
            start_ts=requested_start,
            end_ts=requested_end,
            params_json={
                "batch_id": batch_id,
                "batch_requested_start_ts": requested_start.isoformat(),
                "batch_requested_end_ts": requested_end.isoformat(),
            },
            metrics_json={
                "base": {"trades": 7, "winrate": 0.42, "profit_factor": 0.9},
                "error": "insufficient_signals",
            },
            equity_curve_json=[],
            status="failed",
            created_at=now - timedelta(minutes=2),
        ),
        Backtest(
            strategy="MeanReversionHardStop",
            universe_json=["SOL-USDC"],
            start_ts=requested_start,
            end_ts=requested_end,
            params_json={
                "batch_id": batch_id,
                "batch_requested_start_ts": requested_start.isoformat(),
                "batch_requested_end_ts": requested_end.isoformat(),
            },
            metrics_json={},
            equity_curve_json=[],
            status="running",
            created_at=now - timedelta(minutes=1),
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    resp = client.get(f"/api/backtests/batches/{batch_id}/stats", headers=auth_header)
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["batch_id"] == batch_id
    assert payload["summary"]["total_strategies"] == 4
    assert payload["summary"]["completed"] == 1
    assert payload["summary"]["failed"] == 1
    assert payload["summary"]["running"] == 1
    assert payload["summary"]["missing"] == 1
    assert payload["summary"]["all_completed"] is False
    assert len(payload["strategies"]) == 4

    by_strategy = {item["strategy"]: item for item in payload["strategies"]}
    assert by_strategy["StrategyBreakoutRetest"]["status"] == "completed"
    assert by_strategy["StrategyBreakoutRetest"]["base"]["trades"] == 11
    assert by_strategy["StrategyPullbackToTrend"]["status"] == "failed"
    assert by_strategy["StrategyPullbackToTrend"]["error"] == "insufficient_signals"
    assert by_strategy["MeanReversionHardStop"]["status"] == "running"
    assert by_strategy["StrategyTrendRetrace70"]["status"] == "missing"


def test_reset_paper_state_clears_trading_tables_and_sets_limit(client, auth_header, db_session):
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
    db_session.add(instrument)
    db_session.commit()
    db_session.refresh(instrument)

    db_session.add(
        Position(
            mode="paper",
            instrument_id=instrument.id,
            side="buy",
            qty_base=0.1,
            avg_price=50000.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            status="open",
        )
    )
    db_session.add(
        Trade(
            mode="paper",
            instrument_id=instrument.id,
            side="buy",
            qty_base=0.1,
            qty_quote=5000.0,
            entry_price=50000.0,
            exit_price=None,
            fees=0.0,
            pnl=0.0,
            status="open",
            order_ids_json={},
            meta_json={},
        )
    )
    db_session.add(
        Order(
            mode="paper",
            instrument_id=instrument.id,
            client_order_id="reset-test-order-1",
            exchange_order_id=None,
            type="limit",
            side="buy",
            price=50000.0,
            size=0.1,
            status="open",
            raw_json={},
        )
    )
    db_session.add(
        EquitySnapshot(
            mode="paper",
            equity=9800.0,
            peak_equity=10000.0,
            drawdown_pct=2.0,
            ts=now,
        )
    )
    db_session.commit()

    assert db_session.query(Position).count() == 1
    assert db_session.query(Trade).count() == 1
    assert db_session.query(Order).count() == 1
    assert db_session.query(EquitySnapshot).count() == 1

    resp = client.post("/api/system/paper/reset", headers=auth_header, json={"limit_usd": 10000})
    assert resp.status_code == 200
    assert "Limit set to $10,000.00" in resp.json()["message"]

    assert db_session.query(Position).count() == 0
    assert db_session.query(Trade).count() == 0
    assert db_session.query(Order).count() == 0
    assert db_session.query(EquitySnapshot).count() == 0

    settings_resp = client.get("/api/settings", headers=auth_header)
    assert settings_resp.status_code == 200
    risk = settings_resp.json()["risk_params_json"]
    assert risk["initial_equity"] == 10000.0
    assert risk["max_position_notional_pct"] == 100.0
