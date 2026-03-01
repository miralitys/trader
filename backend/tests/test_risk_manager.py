from app.risk.manager import (
    InstrumentConstraints,
    RiskManager,
    RiskParams,
    calculate_position_size,
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
