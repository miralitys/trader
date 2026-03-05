from datetime import datetime, timedelta, timezone

from app.models.entities import Candle, Instrument


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
    for ticker in ["BTC", "ETH", "SOL", "XRP", "ADA"]:
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
