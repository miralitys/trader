from app.services.settings_service import _merge_strategy_overrides
from app.strategies.profiles import apply_strategy_overrides, get_strategy_profile


def test_apply_strategy_overrides_only_for_selected_strategy():
    strategy_params = {
        "strategy_overrides": {
            "StrategyTrendRetrace70": {
                "signal": {"tr_tp2_rr": 3.2},
                "risk": {"max_hold_hours": 24},
            },
            "StrategyBreakoutRetest": {
                "signal": {"br_lookback_n": 55},
            },
        }
    }

    trend_profile = apply_strategy_overrides(
        get_strategy_profile("StrategyTrendRetrace70"),
        strategy_params,
        "StrategyTrendRetrace70",
    )
    breakout_profile = apply_strategy_overrides(
        get_strategy_profile("StrategyBreakoutRetest"),
        strategy_params,
        "StrategyBreakoutRetest",
    )

    assert trend_profile["signal"]["tr_tp2_rr"] == 3.2
    assert trend_profile["risk"]["max_hold_hours"] == 24
    assert "br_lookback_n" not in trend_profile["signal"]

    assert breakout_profile["signal"]["br_lookback_n"] == 55
    assert "tr_tp2_rr" not in breakout_profile["signal"]


def test_merge_strategy_overrides_keeps_other_strategies_unchanged():
    existing = {
        "StrategyTrendRetrace70": {
            "signal": {"tr_tp2_rr": 2.0, "tr_retrace_tolerance": 0.05},
        },
        "StrategyBreakoutRetest": {
            "signal": {"br_lookback_n": 20},
        },
    }
    incoming = {
        "StrategyTrendRetrace70": {
            "signal": {"tr_tp2_rr": 2.8},
        }
    }

    merged = _merge_strategy_overrides(existing, incoming)

    assert merged["StrategyTrendRetrace70"]["signal"]["tr_tp2_rr"] == 2.8
    assert merged["StrategyTrendRetrace70"]["signal"]["tr_retrace_tolerance"] == 0.05
    assert merged["StrategyBreakoutRetest"]["signal"]["br_lookback_n"] == 20
