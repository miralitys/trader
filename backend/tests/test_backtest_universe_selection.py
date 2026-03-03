from app.services.backtest_service import UniverseCandidate, _select_top5_with_history


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
