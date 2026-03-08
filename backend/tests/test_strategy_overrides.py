from app.services.settings_service import _merge_strategy_overrides
from app.strategies.profiles import apply_strategy_overrides, get_strategy_profile


def test_apply_strategy_overrides_only_for_selected_strategy():
    strategy_params = {
        "strategy_overrides": {
            "StrategyTrendRetrace70": {
                "signal": {"tr70_tp_rr": 3.2},
                "risk": {"max_hold_hours": 24},
            },
            "StrategyBreakoutRetest": {
                "signal": {"breakout_lookback": 55},
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

    assert trend_profile["signal"]["tr70_tp_rr"] == 3.2
    assert trend_profile["risk"]["max_hold_hours"] == 24
    assert "breakout_lookback" not in trend_profile["signal"]

    assert breakout_profile["signal"]["breakout_lookback"] == 55
    assert "tr70_tp_rr" not in breakout_profile["signal"]


def test_merge_strategy_overrides_keeps_other_strategies_unchanged():
    existing = {
        "StrategyTrendRetrace70": {
            "signal": {"tr70_tp_rr": 2.1, "tr70_min_volume_ratio": 0.8},
        },
        "StrategyBreakoutRetest": {
            "signal": {"breakout_lookback": 20},
        },
    }
    incoming = {
        "StrategyTrendRetrace70": {
            "signal": {"tr70_tp_rr": 2.8},
        }
    }

    merged = _merge_strategy_overrides(existing, incoming)

    assert merged["StrategyTrendRetrace70"]["signal"]["tr70_tp_rr"] == 2.8
    assert merged["StrategyTrendRetrace70"]["signal"]["tr70_min_volume_ratio"] == 0.8
    assert merged["StrategyBreakoutRetest"]["signal"]["breakout_lookback"] == 20
