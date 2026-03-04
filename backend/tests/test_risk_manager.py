from app.risk.manager import (
    InstrumentConstraints,
    RiskManager,
    RiskParams,
    calculate_position_size,
    evaluate_entry_edge,
)


def test_position_size_formula():
    constraints = InstrumentConstraints(min_size=0.001, size_increment=0.001)
    qty_base, qty_quote = calculate_position_size(
        equity=10000,
        risk_per_trade_pct=1.0,
        entry=100,
        stop=95,
        constraints=constraints,
    )

    # risk_amount=100, risk_per_unit=5 => qty_base=20
    assert qty_base == 20.0
    assert qty_quote == 2000.0


def test_reject_on_limits():
    params = RiskParams(max_positions=1, max_trades_per_day=2)
    rm = RiskManager(params)

    decision = rm.assess_entry(
        equity=10000,
        entry=10,
        stop=9,
        constraints=InstrumentConstraints(min_size=0.1, size_increment=0.1),
        current_open_positions=1,
        trades_today=0,
        daily_loss_pct=0,
        weekly_loss_pct=0,
        consecutive_losses=0,
        drawdown_pct=0,
    )

    assert decision.allowed is False
    assert "max_positions" in decision.reason


def test_position_size_is_capped_by_quote_exposure():
    constraints = InstrumentConstraints(min_size=0.001, size_increment=0.001)
    qty_base, qty_quote = calculate_position_size(
        equity=10000,
        risk_per_trade_pct=1.0,
        entry=100,
        stop=99.5,
        constraints=constraints,
        max_quote_exposure=5000,
    )

    assert qty_base == 50.0
    assert qty_quote == 5000.0


def test_edge_filter_rejects_low_profit_to_cost_setup():
    decision = evaluate_entry_edge(
        entry=100.0,
        take=100.4,  # +0.4%
        maker_fee_pct=0.25,
        taker_fee_pct=0.4,
        market_exit_slippage_pct=0.05,
        min_profit_to_cost_ratio=1.2,
    )
    assert decision.allowed is False
    assert "edge_too_low" in decision.reason


def test_edge_filter_allows_when_ratio_is_sufficient():
    decision = evaluate_entry_edge(
        entry=100.0,
        take=101.2,  # +1.2%
        maker_fee_pct=0.25,
        taker_fee_pct=0.4,
        market_exit_slippage_pct=0.05,
        min_profit_to_cost_ratio=1.2,
    )
    assert decision.allowed is True
    assert decision.reward_to_cost_ratio >= 1.2
