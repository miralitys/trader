from app.strategies.profiles import get_strategy_profile, resolve_strategy_scope
from app.schemas.settings import DEFAULT_STRATEGY


def test_profile_backtest_defaults_for_mean_reversion():
    profile = get_strategy_profile("MeanReversionHardStop")
    backtest = profile["backtest"]
    signal = profile["signal"]

    assert backtest["history_min_coverage_ratio"] == 0.005
    assert backtest["history_target_coverage_ratio"] == 0.005
    assert backtest["history_required_coverage_ratio"] == 0.20
    assert isinstance(backtest["input_tickers"], list)
    assert len(backtest["input_tickers"]) > 0
    assert signal["mr_signal_ttl_minutes"] == 60


def test_breakout_profile_uses_new_br_signal_defaults():
    profile = get_strategy_profile("StrategyBreakoutRetest")
    signal = profile["signal"]

    assert signal["br_lookback_n"] == 20
    assert signal["br_atr_period"] == 14
    assert signal["br_retest_atr_k"] == 0.3
    assert signal["br_stop_atr_mult"] == 1.0
    assert signal["br_tp1_rr"] == 1.0
    assert signal["br_tp2_rr"] == 2.0
    assert signal["br_trail_ema_period"] == 20
    assert signal["br_signal_ttl_minutes"] == 60


def test_pullback_profile_uses_new_pt_signal_defaults():
    profile = get_strategy_profile("StrategyPullbackToTrend")
    signal = profile["signal"]

    assert signal["pt_ema_fast"] == 20
    assert signal["pt_ema_slow"] == 50
    assert signal["pt_rsi_period"] == 14
    assert signal["pt_rsi_threshold"] == 45.0
    assert signal["pt_stop_lookback"] == 10
    assert signal["pt_tp_rr"] == 1.2
    assert signal["pt_signal_ttl_minutes"] == 60


def test_scope_resolution_supports_alias_and_both():
    both = resolve_strategy_scope("both")
    assert "StrategyBreakoutRetest" in both
    assert "StrategyPullbackToTrend" in both
    assert "MeanReversionHardStop" in both
    assert resolve_strategy_scope("mean_reversion") == ["MeanReversionHardStop"]


def test_trend_retrace_profile_uses_new_tr_defaults():
    profile = get_strategy_profile("StrategyTrendRetrace70")
    signal = profile["signal"]

    assert signal["tr_pivot_left_right"] == 3
    assert signal["tr_wave_tf"] == "15m"
    assert signal["tr_min_impulse_atr"] == 1.5
    assert signal["tr_retrace_target"] == 0.70
    assert signal["tr_retrace_zone_low"] == 0.62
    assert signal["tr_retrace_zone_high"] == 0.78
    assert signal["tr_retrace_tolerance"] == 0.05
    assert signal["tr_trigger_mode"] == "ema20"
    assert signal["tr_trigger_ema_period"] == 20
    assert signal["tr_trigger_lookback"] == 6
    assert signal["tr_stop_lookback"] == 12
    assert signal["tr_stop_atr_buffer"] == 0.2
    assert signal["tr_max_stop_pct"] == 0.04
    assert signal["tr_tp2_rr"] == 2.0
    assert signal["tr_signal_ttl_minutes"] == 180
    assert signal["tr_safety_ema_period"] == 200


def test_strategy_setting_namespaces_are_isolated():
    breakout_keys = {key for key in DEFAULT_STRATEGY if key.startswith("br_")}
    pullback_keys = {key for key in DEFAULT_STRATEGY if key.startswith("pt_")}
    mean_reversion_keys = {key for key in DEFAULT_STRATEGY if key.startswith("mr_")}
    trend_retrace_keys = {key for key in DEFAULT_STRATEGY if key.startswith("tr_")}

    assert breakout_keys
    assert pullback_keys
    assert mean_reversion_keys
    assert trend_retrace_keys
    assert breakout_keys.isdisjoint(pullback_keys)
    assert breakout_keys.isdisjoint(mean_reversion_keys)
    assert breakout_keys.isdisjoint(trend_retrace_keys)
    assert pullback_keys.isdisjoint(mean_reversion_keys)
    assert pullback_keys.isdisjoint(trend_retrace_keys)
    assert mean_reversion_keys.isdisjoint(trend_retrace_keys)
