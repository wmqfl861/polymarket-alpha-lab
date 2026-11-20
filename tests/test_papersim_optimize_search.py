"""MO-6 optimize search tests: grid enumeration, budget guard, final selection.

Plan 05 node MO-6 (plan r2 section 4, T11): declared-order enumeration,
closed-set discipline (choice sets verbatim; numeric dimensions REQUIRE
an explicit point list), validate_param enforcement, canonical space
identity text, the dual budget guard (time and trials, phase-tagged),
and final_selection driving walkforward.one_standard_error_rule.
"""

from __future__ import annotations

import datetime as dt

import pytest

from polymarket_alpha_lab.papersim.optimize import search
from polymarket_alpha_lab.papersim.optimize.spec import ParamDef
from polymarket_alpha_lab.papersim.optimize.walkforward import WalkFold

UTC = dt.timezone.utc


def _space() -> tuple[ParamDef, ...]:
    return (
        ParamDef("half_life_days", "choice", 120.0, choices=(60.0, 120.0, 240.0)),
        ParamDef("team_prior_matches", "choice", 8.0, choices=(4.0, 8.0, 16.0)),
    )


def test_enumerate_grid_declared_order_last_dimension_fastest() -> None:
    grid = search.enumerate_grid(_space())
    assert [point.indices for point in grid] == [
        (0, 0), (0, 1), (0, 2),
        (1, 0), (1, 1), (1, 2),
        (2, 0), (2, 1), (2, 2),
    ]
    assert grid[0].params == {"half_life_days": 60.0, "team_prior_matches": 4.0}
    assert grid[-1].params == {"half_life_days": 240.0, "team_prior_matches": 16.0}


def test_enumerate_grid_numeric_dimension_requires_explicit_points() -> None:
    space = (ParamDef("slope", "float", 0.47, lower=0.40, upper=0.55),)
    with pytest.raises(ValueError, match="closed point list"):
        search.enumerate_grid(space)
    grid = search.enumerate_grid(space, overrides={"slope": (0.40, 0.47, 0.55)})
    assert [point.params["slope"] for point in grid] == [0.40, 0.47, 0.55]


def test_enumerate_grid_rejects_override_on_choice_dimension() -> None:
    with pytest.raises(ValueError, match="closed choice set"):
        search.enumerate_grid(
            _space(), overrides={"half_life_days": (60.0,)}
        )


def test_enumerate_grid_rejects_unknown_and_invalid_values() -> None:
    space = (
        ParamDef("slope", "float", 0.47, lower=0.40, upper=0.55),
        ParamDef("count", "int", 3, choices=(2, 3, 4)),
    )
    with pytest.raises(ValueError, match="undeclared dimensions"):
        search.enumerate_grid(space, overrides={"nope": (1.0,)})
    with pytest.raises(ValueError, match="above upper bound"):
        search.enumerate_grid(space, overrides={"slope": (0.60,)})
    with pytest.raises(ValueError, match="below lower bound"):
        search.enumerate_grid(space, overrides={"slope": (0.30,)})
    # Booleans never satisfy a numeric kind even as an int subclass.
    with pytest.raises(ValueError, match="expected a number"):
        search.enumerate_grid(
            space, overrides={"slope": (True,)}
        )


def test_grid_space_text_is_stable_and_value_exactly_spelled() -> None:
    text_one = search.grid_space_text(_space())
    assert text_one == search.grid_space_text(_space())
    assert "60.0" in text_one and "16.0" in text_one
    # canonical_value sorts keys, so declaring the dimensions in a
    # different order still maps to the SAME space identity.
    assert text_one == search.grid_space_text(
        (ParamDef("team_prior_matches", "choice", 8.0, choices=(4.0, 8.0, 16.0)),
         ParamDef("half_life_days", "choice", 120.0, choices=(60.0, 120.0, 240.0)))
    )
    summary = search.grid_space_summary(_space())
    assert summary["combinations"] == 9


def test_budget_guard_trials_and_time_limits() -> None:
    clock = {"now": 0.0}
    guard = search.BudgetGuard(
        max_seconds=10.0, max_trials=3, now_fn=lambda: clock["now"]
    )
    guard.set_phase("walk_forward")
    guard.charge()
    guard.charge()
    guard.charge()  # the third trial is still within the cap
    with pytest.raises(search.RunBudgetExceeded) as excinfo:
        guard.charge()
    assert excinfo.value.reason == "max_trials"
    assert excinfo.value.phase == "walk_forward"
    assert excinfo.value.trials == 3  # the completed count

    guard_two = search.BudgetGuard(max_seconds=5.0, now_fn=lambda: clock["now"])
    clock["now"] = 5.5
    with pytest.raises(search.RunBudgetExceeded) as excinfo_two:
        guard_two.charge(0)
    assert excinfo_two.value.reason == "max_seconds"
    assert excinfo_two.value.phase == ""


def _fold(block: str, day: int, train: tuple[str, ...], test: tuple[str, ...]):
    return WalkFold(
        block_id=block,
        cutoff=dt.datetime(2026, 1, day, tzinfo=UTC),
        train_keys=train,
        test_keys=test,
        train_block_ids=(),
    )


def test_final_selection_shrinks_to_incumbent_and_reports_one_se() -> None:
    # Candidate "b" is the raw best on every fold, but "a" ties it
    # within one standard error of the best candidate's block means
    # (identical block means -> SE 0 shrinks the tie to declared
    # order), and the incumbent sits at "a".
    folds = (
        _fold("w1", 1, ("t0",), ("e1", "e2")),
        _fold("w2", 2, ("t0", "e1", "e2"), ("e3", "e4")),
    )
    candidates = ["a", "b"]
    incumbent = "a"

    def score(candidate, fold, keys):
        return [1.0 if candidate == "b" else 1.5 for _ in keys]

    selection, one_se = search.final_selection(
        folds, candidates, score, incumbent,
        (ParamDef("choice", "choice", "a", choices=("a", "b")),),
        lambda item: {"choice": item},
    )
    assert selection.status == one_se.status
    assert one_se.winner == "b"  # SE = 0 (identical block means): plain min
    # With genuinely varying block means, the incumbent wins the tie.
    def score_varying(candidate, fold, keys):
        if candidate == "b":
            return [1.0 + 0.5 * index for index, _ in enumerate(keys)]
        return [1.2 for _ in keys]

    selection_two, one_se_two = search.final_selection(
        folds, candidates, score_varying, incumbent,
        (ParamDef("choice", "choice", "a", choices=("a", "b")),),
        lambda item: {"choice": item},
    )
    assert one_se_two.winner == "a"
    assert selection_two.status == "no_better_candidate"
    assert selection_two.scores[0][1] == pytest.approx(1.2)


def test_final_selection_requires_scoring_evidence() -> None:
    folds = (_fold("w1", 1, ("t0",), ("e1",)),)
    with pytest.raises(ValueError, match="at least one candidate"):
        search.final_selection(
            folds, (), lambda *_: [1.0], "x", (), lambda item: {}
        )
