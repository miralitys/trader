from app.strategies.profiles import get_strategy_profile, resolve_strategy_scope


def test_profile_backtest_defaults_for_mean_reversion():
    profile = get_strategy_profile("MeanReversionHardStop")
    backtest = profile["backtest"]

    assert backtest["history_min_coverage_ratio"] == 0.005
    assert backtest["history_target_coverage_ratio"] == 0.005
    assert backtest["history_required_coverage_ratio"] == 0.20
    assert isinstance(backtest["input_tickers"], list)
    assert len(backtest["input_tickers"]) > 0


def test_scope_resolution_supports_alias_and_both():
    both = resolve_strategy_scope("both")
    assert "StrategyBreakoutRetest" in both
    assert "StrategyPullbackToTrend" in both
    assert "MeanReversionHardStop" in both
    assert resolve_strategy_scope("mean_reversion") == ["MeanReversionHardStop"]
