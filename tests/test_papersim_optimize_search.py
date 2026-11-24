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
import math
from polymarket_alpha_lab.papersim.optimize import search
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, ParamDef
from polymarket_alpha_lab.papersim.optimize.walkforward import WalkFold
from dataclasses import dataclass, replace
from zoneinfo import ZoneInfo
from polymarket_alpha_lab.papersim.optimize.metrics import ScoredEvent


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


UTC = dt.timezone.utc


def fold(block_id: str, *keys: str) -> WalkFold:
    return WalkFold(
        block_id=block_id,
        cutoff=dt.datetime(2026, 11, 1, tzinfo=UTC),
        train_keys=tuple(keys),
        test_keys=tuple(keys),
        train_block_ids=(block_id,),
    )


TOY_SPACE = (
    ParamDef("alpha", "choice", 1, choices=(1, 2, 3)),
    ParamDef("beta", "choice", "x", choices=("x", "y")),
)


def test_enumeration_order_pinned():
    candidates = search.enumerate_parameters(TOY_SPACE)
    assert candidates == (
        {"alpha": 1, "beta": "x"},
        {"alpha": 1, "beta": "y"},
        {"alpha": 2, "beta": "x"},
        {"alpha": 2, "beta": "y"},
        {"alpha": 3, "beta": "x"},
        {"alpha": 3, "beta": "y"},
    )
    # fold-major in declared dimension order: alpha varies slowest


def test_enumeration_single_dimension_and_empty_space():
    one = (ParamDef("alpha", "choice", 1, choices=(1, 2)),)
    assert search.enumerate_parameters(one) == ({"alpha": 1}, {"alpha": 2})
    assert search.enumerate_parameters(()) == ({},)


def test_continuous_grid_endpoints_default_and_dedup():
    space = (
        ParamDef("slope", "float", 0.47, lower=0.40, upper=0.55),
    )
    values = search.dimension_values(space[0], continuous_points=4)
    assert values[0] == pytest.approx(0.40)
    assert values[-1] == pytest.approx(0.55)
    assert values == tuple(sorted(set(values)))
    assert 0.47 == pytest.approx(list(values)[2])  # default on the 4-grid
    # a default OFF the grid is merged in, sorted and deduplicated
    off = (ParamDef("slope", "float", 0.50, lower=0.40, upper=0.55),)
    merged = search.dimension_values(off[0], continuous_points=4)
    assert len(merged) == len(set(merged))
    assert 0.50 == pytest.approx(list(merged)[2])


def test_unbounded_numeric_dimension_refused():
    with pytest.raises(ValueError, match="needs both bounds"):
        search.dimension_values(ParamDef("x", "float", 1.0, lower=0.0))
    with pytest.raises(ValueError, match="continuous_points"):
        search.dimension_values(
            ParamDef("x", "float", 1.0, lower=0.0, upper=2.0)
        )
    with pytest.raises(ValueError, match="invalid numeric bounds"):
        search.dimension_values(
            ParamDef("x", "float", 1.0, lower=3.0, upper=2.0),
            continuous_points=3,
        )


def test_frozen_numeric_dimension_and_integer_lattice():
    frozen = ParamDef("fixed", "float", 2.0, lower=2.0, upper=2.0, season_frozen=True)
    integer = ParamDef("window", "int", 3, lower=1.1, upper=5.9)
    assert search.enumerate_parameters((frozen,)) == ({"fixed": 2.0},)
    values = search.dimension_values(integer, continuous_points=3)
    assert values == (2, 3, 4, 5)
    assert all(type(value) is int for value in values)


def test_deadline_after_last_trial_preserves_final_score():
    checks = iter((False, True))
    result = search.run_trials(
        ({},),
        (fold("W01", "a"),),
        lambda _candidate, _fold, _keys: (0.5,),
        budget=search.SearchBudget(max_trials=1, deadline=lambda: next(checks)),
    )
    assert result.budget_exhausted
    assert result.evaluated_trials == 1
    assert result.trials[0].mean_loss == 0.5


def test_run_trials_exact_accounting_and_order():
    folds = (fold("b1", "k1"), fold("b2", "k2"), fold("b3", "k3"))
    candidates = search.enumerate_parameters(TOY_SPACE)
    seen: list[tuple[int, str]] = []

    def score(candidate, fold, keys):
        seen.append((candidate["alpha"], fold.block_id))
        return [float(len(keys))] * len(keys)

    budget = search.SearchBudget(max_trials=100, deadline=lambda: False)
    result = search.run_trials(candidates, folds, score, budget=budget)
    assert not result.budget_exhausted
    assert result.evaluated_trials == 18  # 6 candidates x 3 folds, exact
    assert result.is_complete()
    # fold-major, candidate-declared order within each fold
    assert seen[:6] == [
        (1, "b1"), (1, "b1"), (2, "b1"), (2, "b1"), (3, "b1"), (3, "b1"),
    ]
    assert seen[6:12] == [(1, "b2"), (1, "b2"), (2, "b2"), (2, "b2"),
                          (3, "b2"), (3, "b2")]
    assert result.trials[0].status == search.TRIAL_COMPLETED
    assert result.trials[0].n_events == 1
    assert result.trials[0].mean_loss == 1.0


def test_run_trials_empty_fold_recorded_not_dropped():
    folds = (fold("b1", "k1"),)
    candidates = search.enumerate_parameters((ParamDef("a", "choice", 1, choices=(1,)),))

    def score(candidate, fold, keys):
        return []

    result = search.run_trials(
        candidates, folds, score,
        budget=search.SearchBudget(max_trials=10, deadline=lambda: False),
    )
    assert result.trials[0].status == search.TRIAL_NO_EVENTS
    assert result.trials[0].mean_loss is None
    assert result.trials[0].n_events == 0


def test_trial_cap_stops_between_trials_and_preserves_partials():
    folds = (fold("b1", "k1"), fold("b2", "k2"))
    candidates = search.enumerate_parameters(TOY_SPACE)  # 6 x 2 = 12 planned
    budget = search.SearchBudget(max_trials=5, deadline=lambda: False)
    result = search.run_trials(
        candidates, folds, lambda c, f, k: [1.0], budget=budget
    )
    assert result.budget_exhausted
    assert result.evaluated_trials == 5  # exact, never estimated
    assert not result.is_complete()
    assert [t.trial_index for t in result.trials] == [0, 1, 2, 3, 4]
    # partials preserved: the five completed trials carry real scores
    assert all(t.mean_loss == 1.0 for t in result.trials)


def test_injected_deadline_stops_between_trials():
    folds = (fold("b1", "k1"), fold("b2", "k2"))
    candidates = search.enumerate_parameters(TOY_SPACE)
    calls = {"n": 0}

    def deadline():
        calls["n"] += 1
        return calls["n"] > 3  # stops after the fourth deadline poll

    result = search.run_trials(
        candidates, folds, lambda c, f, k: [0.5],
        budget=search.SearchBudget(max_trials=100, deadline=deadline),
    )
    assert result.budget_exhausted
    assert result.evaluated_trials == 3
    assert all(t.mean_loss == 0.5 for t in result.trials)


def test_select_candidate_incumbent_retained_when_eligible():
    # candidate 0 (alpha=1, beta=x) IS the incumbent's grid point and has
    # the best score; every rival sits above best + 1*SE, so the
    # incumbent is kept: no_better_candidate, holdout never touched.
    folds = (fold("b1", "k1"), fold("b2", "k2"))
    candidates = search.enumerate_parameters(TOY_SPACE)
    # per-candidate per-fold event-loss lists (one event per fold here)
    scores_by_index = [
        [[1.00], [0.98]],  # incumbent grid point: best, SE > 0
        [[1.05], [1.05]],
        [[1.10], [1.10]],
        [[1.20], [1.20]],
        [[1.30], [1.30]],
        [[1.40], [1.40]],
    ]
    result = _result_from_scores(candidates, folds, scores_by_index)
    picked = search.select_candidate(result, TOY_SPACE, {"alpha": 1, "beta": "x"})
    assert picked.status == "no_better_candidate"
    assert picked.winner == {"alpha": 1, "beta": "x"}


def test_select_candidate_shrinks_to_nearest_incumbent():
    # best is alpha=3/beta=y with block means [1.0, 0.98] (mean 0.99,
    # SE = stdev([1.0, 0.98])/sqrt(2) ~= 0.01414, threshold ~= 1.00414):
    # alpha=3/beta=x at 1.0 is also eligible. The incumbent (alpha=1,
    # beta=x) is far away, so among the eligible the nearer one wins:
    # alpha=3/beta=x changes ONE choice vs two for beta=y.
    folds = (fold("b1", "k1"), fold("b2", "k2"))
    candidates = search.enumerate_parameters(TOY_SPACE)
    scores_by_index = [
        [[2.0], [2.0]],
        [[1.9], [1.9]],
        [[1.5], [1.5]],
        [[1.4], [1.4]],
        [[1.0], [1.0]],   # alpha=3/beta=x, eligible at 1.0 <= threshold
        [[1.0], [0.98]],  # alpha=3/beta=y, best
    ]
    result = _result_from_scores(candidates, folds, scores_by_index)
    picked = search.select_candidate(result, TOY_SPACE, {"alpha": 1, "beta": "x"})
    assert picked.status == "candidate_selected"
    assert picked.winner == {"alpha": 3, "beta": "x"}


def test_select_candidate_refuses_incomplete_search():
    folds = (fold("b1", "k1"),)
    candidates = search.enumerate_parameters(TOY_SPACE)
    result = search.run_trials(
        candidates, folds, lambda c, f, k: [1.0],
        budget=search.SearchBudget(max_trials=2, deadline=lambda: False),
    )
    with pytest.raises(ValueError, match="complete"):
        search.select_candidate(result, TOY_SPACE, {"alpha": 1, "beta": "x"})


def test_determinism_under_key_order_shuffling():
    # the incumbent mapping arrives with shuffled key insertion order;
    # distances run through param_defs (declared order), so the winner
    # and every number are unchanged.
    folds = (fold("b1", "k1"), fold("b2", "k2"))
    candidates = search.enumerate_parameters(TOY_SPACE)
    scores_by_index = [
        [[2.0], [2.0]],
        [[1.9], [1.9]],
        [[1.5], [1.5]],
        [[1.4], [1.4]],
        [[1.0], [1.0]],
        [[1.0], [0.98]],
    ]
    result = _result_from_scores(candidates, folds, scores_by_index)
    incumbent_a = {"alpha": 1, "beta": "x"}
    incumbent_b = {"beta": "x", "alpha": 1}  # same content, shuffled order
    picked_a = search.select_candidate(result, TOY_SPACE, incumbent_a)
    picked_b = search.select_candidate(result, TOY_SPACE, incumbent_b)
    assert picked_a.winner == picked_b.winner
    assert picked_a.status == picked_b.status
    assert picked_a.threshold == picked_b.threshold
    assert picked_a.standard_error == picked_b.standard_error
    # and the trial stream itself is a pure function of the inputs
    again = _result_from_scores(candidates, folds, scores_by_index)
    assert again.trials == result.trials
    assert again.event_scores == result.event_scores


def _result_from_scores(candidates, folds, scores_by_index):
    """Build a complete SearchResult from per-candidate per-fold losses.

    ``scores_by_index[i][j]`` is candidate ``i``'s LIST of per-event
    losses on fold ``j``.
    """

    def score(candidate, fold, keys):
        index = candidates.index(candidate)
        fold_index = [f.block_id for f in folds].index(fold.block_id)
        return list(scores_by_index[index][fold_index])

    return search.run_trials(
        candidates,
        folds,
        score,
        budget=search.SearchBudget(max_trials=10_000, deadline=lambda: False),
    )


@dataclass(frozen=True)
class _Record:
    event_key: str
    group_id: str
    kickoff: dt.datetime | None = None
    available_at: dt.datetime | None = None


def _dated_records(counts, *, calendar="source-game-day"):
    start = dt.date(1970, 1, 1)
    records = []
    source_days = {}
    day_ids = []
    for offset, n_events in enumerate(counts):
        day = start + dt.timedelta(days=offset)
        day_id = day.isoformat()
        day_ids.append(day_id)
        for index in range(n_events):
            key = f"{day_id}:{index}"
            records.append(_Record(key, day_id))
            source_days[key] = search.SourceDay(calendar, day, day)
    return records, source_days, day_ids


def _cohort(records, source_days, holdout_ids):
    last_date = max(item.prediction_date for item in source_days.values())
    return search.validate_cohort(
        records, holdout_ids,
        data_cutoff=dt.datetime.combine(last_date + dt.timedelta(days=1), dt.time.min, UTC),
        source_days=source_days, source_timezone=UTC, last_complete_date=last_date,
    )


def _line(key, target, *, line="match", classes=("yes", "no")):
    return search.LineKey(key, target, line, "sealed-contract", classes)


def _forecast(key, probabilities, *, truth=0, fallback=None):
    return search.Forecast(
        ScoredEvent(key.event_key, key.line_key, ("fixture", key.event_key), probabilities, truth),
        fallback,
    )


def _toy_catalog():
    definitions = (ParamDef("alpha", "choice", 1, choices=(1, 2)),)
    active = ModelSpec("toy", "v1", {"alpha": 1}, (), "toy-v1")
    return search.build_catalog(active, definitions, lambda params: replace(active, params=params)), definitions


def _universes(folds, targets):
    return {
        (item.block_id, target): tuple(_line(key, target) for key in item.test_keys)
        for item in folds for target in targets
    }


def _budget(cap=2_000):
    return search.SearchBudget(cap, lambda: False)


def test_scope_is_normalized_before_evaluation():
    declared = ("moneyline", "totals")
    assert search.normalize_targets(declared) == declared
    assert search.normalize_targets(declared, reversed(declared)) == declared
    assert search.normalize_targets(declared, ("totals",)) == ("totals",)
    for scope, match in (((), "nonempty"), (("totals", "totals"), "duplicates"), (("extra",), "unknown")):
        with pytest.raises(ValueError, match=match):
            search.normalize_targets(declared, scope)


def test_nba_real_catalog_and_physical_fit_union():
    from polymarket_alpha_lab.papersim.optimize.families.nba import (
        NBA_PACE_EFFICIENCY,
        NbaFamilyFixture,
        NbaFamilyHistory,
        _envelope_mappings,
    )

    family = NBA_PACE_EFFICIENCY
    definitions = family.param_space()
    active = family.baseline_spec()

    def build(params):
        return replace(active, params=params, envelope=_envelope_mappings(float(params["half_life"])))

    targets = family.targets()
    catalog = search.build_catalog(active, definitions, build, one_coordinate=True)
    assert len(search.enumerate_parameters(definitions)) == 1_125
    assert len(catalog.entries) == 17
    assert catalog.entries[0].spec is active
    assert tuple(
        tuple(name for name in active.params if item.spec.params[name] != active.params[name])
        for item in catalog.entries
    ) == ((), *(
        (definition.name,) for definition in definitions for _ in range(len(definition.choices) - 1)
    ))
    assert all(len(spec.envelope) == 6 for spec in catalog.specs)

    records, source_days, days = _dated_records((1,) * 4 + (10,) * 30)
    cohort = _cohort(records, source_days, days[4:])
    assert len(cohort.holdout_records) == 300 and len(cohort.holdout_group_ids) == 30
    folds, skipped = search.selected_prefix_folds(cohort)
    assert not skipped
    assert tuple(item.block_id for item in folds) == tuple(days[2:4])
    universes = _universes(folds, targets)
    plan = search.plan_evaluations(folds, dict.fromkeys(targets, catalog))
    assert len(plan) == 34
    assert all(item.targets == targets for item in plan)
    assert len({item.fold_sha256 for item in plan}) == 2

    predictions = {}

    def evaluate(spec, item, target_mask):
        teams = {team: {"display": team, "games": []} for team in ("AAA", "BBB")}
        for index, key in enumerate(item.train_keys):
            day = source_days[key].prediction_date.isoformat()
            for team, opponent, home, points, opposing in (
                ("AAA", "BBB", True, 111 + index, 115), ("BBB", "AAA", False, 115, 111 + index),
            ):
                teams[team]["games"].append({
                    "date": day, "home": home, "opponent": opponent, "poss": 100.0,
                    "pts": points, "opp_pts": opposing, "ortg": float(points), "drtg": float(opposing),
                    "rest_days": 1, "b2b": False,
                })
        history = NbaFamilyHistory({"generated_at": "1970-02-05T00:00:00Z", "season": "1969-70", "teams": teams})
        fitted = family.fit(history, spec, item.cutoff)
        prediction = family.predict(fitted, NbaFamilyFixture("AAA", "BBB", before_date=item.block_id))
        assert prediction is not None and len(prediction.variants) == 6
        predictions[item.block_id, spec.spec_sha256()] = prediction
        quote = prediction.total_line_prob(225.5)
        probabilities = {"moneyline": (prediction.p_home, prediction.p_away), "totals": (quote.p_over, quote.p_under)}
        batches = {}
        for target in target_mask:
            keys = universes[item.block_id, target]
            rows = {key: _forecast(key, probabilities[target]) for key in keys}
            batches[target] = search.ForecastBatch(keys, rows, {})
        return batches

    result = search.run_scoped_trials(plan, evaluate, budget=_budget(34), universes=universes)
    assert result.is_complete(), result.issue
    assert result.completed_trials == result.evaluated_trials == 34
    assert result.shared_target_uses == 34
    assert len(result.batches) == 68
    baseline_row = predictions[folds[0].block_id, active.spec_sha256()]
    changed = next(entry for entry in catalog.entries if entry.spec.params["total_bias"] == 2.0)
    assert predictions[folds[0].block_id, changed.spec_sha256].exp_total > baseline_row.exp_total

    another_active = build({**active.params, "half_life": 15.0, "total_bias": 2.0})
    other = search.build_catalog(another_active, definitions, build, one_coordinate=True)
    catalogs = dict(zip(targets, (catalog, other), strict=True))
    union = {entry.spec_sha256 for lineage in catalogs.values() for entry in lineage.entries}
    two = search.plan_evaluations(folds, catalogs)
    assert len(two) == 2 * len(union) <= 68
    assert [item.spec_sha256 for item in two[:2]] == [catalog.incumbent_sha256, other.incumbent_sha256]
    separate = search.plan_evaluations(
        folds, catalogs, model_contexts={"moneyline": "one fit", "totals": "another fit"},
    )
    assert len(separate) == 68 and all(len(item.targets) == 1 for item in separate)


def test_real_off_grid_international_active_is_scored_and_retained_exactly():
    from polymarket_alpha_lab.papersim import intl_model
    from polymarket_alpha_lab.papersim.optimize.families.intl import INTL_ELO, IntlFamilyFixture, IntlHistory

    target, = INTL_ELO.targets()
    definitions = INTL_ELO.param_space()
    active = INTL_ELO.spec_with({"slope": 0.48, "home_advantage": 100.0})
    catalog = search.build_catalog(active, definitions, INTL_ELO.spec_with, continuous_points=5)
    assert len(catalog.entries) == 25
    manual, = [entry for entry in catalog.entries if not entry.automatic]
    assert manual.spec is active and manual.spec_sha256 == active.spec_sha256()
    folds = (fold("f1", "m1"), fold("f2", "m2"))
    universe = {
        (item.block_id, target): (_line(item.test_keys[0], target, classes=("home", "draw", "away")),)
        for item in folds
    }
    # Two different realized sides yield enough block dispersion for the
    # exact comparator to be one-SE eligible on its real predictions.
    truth = {"m1": 0, "m2": 2}
    history = IntlHistory({
        "generated_at": "2026-01-07T00:00:00Z", "teams": {}, "matches": [{
            "date": "2026-01-01", "home": "AAA", "away": "BBB", "home_goals": 2,
            "away_goals": 0, "tournament": "Friendly", "neutral": False, "venue": None,
        }],
    })

    def evaluate(spec, item, targets):
        key, = universe[item.block_id, target]
        fitted = INTL_ELO.fit(history, spec, item.cutoff)
        prediction = INTL_ELO.predict(fitted, IntlFamilyFixture(intl_model.IntlFixture("AAA", "BBB", "2026-02-01")))
        assert targets == (target,) and prediction is not None and not prediction.ratings_stale
        row = _forecast(key, (prediction.p_home, prediction.p_draw, prediction.p_away), truth=truth[key.event_key])
        return {target: search.ForecastBatch((key,), {key: row}, {})}

    plan = search.plan_evaluations(folds, {target: catalog})
    result = search.run_scoped_trials(plan, evaluate, budget=_budget(), universes=universe)
    assert result.is_complete(), result.issue
    assert result.evaluated_trials == 50
    assert sum(item.spec_sha256 == active.spec_sha256() for item in result.trials) == 2
    pick = search.select_common(result, catalog, definitions, target, ("f1", "f2"))
    assert pick.common_lines == 2
    assert pick.status == "no_better_candidate"
    assert pick.selection is not None and pick.winner is active


def test_off_grid_coordinate_anchor_is_the_legal_default_and_keeps_fixed_settings():
    space = (
        ParamDef("slope", "float", 0.5, lower=0.4, upper=0.6),
        ParamDef("weight", "choice", 1, choices=(1, 2, 3)),
    )
    incumbent = ModelSpec("toy", "v1", {"slope": 0.47, "weight": 2, "fixed": "same"}, (), "toy-v1")
    built = []

    def build(params):
        built.append(dict(params))
        return replace(incumbent, params=params)

    catalog = search.build_catalog(incumbent, space, build, continuous_points=3, one_coordinate=True)
    assert catalog.anchor == {"slope": 0.5, "weight": 1, "fixed": "same"}
    assert catalog.entries[0].spec is incumbent and not catalog.entries[0].automatic
    assert [(params["slope"], params["weight"]) for params in built] == [
        (0.5, 1), (0.4, 1), (0.6, 1), (0.5, 2), (0.5, 3),
    ]
    assert all(params["fixed"] == "same" for params in built)
    with pytest.raises(ValueError, match="outside the run's contract"):
        search.build_catalog(
            incumbent, space, lambda params: replace(incumbent, params=params, data_contract="foreign"),
            continuous_points=3,
        )


def test_the_exact_active_and_same_parameter_challenger_keep_separate_digests():
    definitions = (ParamDef("alpha", "choice", 1, choices=(1, 2)),)
    active = ModelSpec("toy", "v1", {"alpha": 1}, ({"name": "manual"},), "toy-v1")

    def build(params):
        return replace(active, params=params, envelope=({"name": "canonical"},))

    catalog = search.build_catalog(active, definitions, build)
    manual, canonical, _ = catalog.entries
    assert manual.spec is active and not manual.automatic
    assert canonical.spec.params == manual.spec.params
    assert canonical.automatic and canonical.spec_sha256 != manual.spec_sha256
    folds = (fold("f1", "a"), fold("f2", "b"))
    plan = search.plan_evaluations(folds, {"target": catalog})
    universe = _universes(folds, ("target",))

    def evaluate(boost_canonical):
        def score(spec, item, _targets):
            key, = universe[item.block_id, "target"]
            probabilities = (0.9, 0.1) if boost_canonical and spec is canonical.spec else (0.5, 0.5)
            return {"target": search.ForecastBatch((key,), {key: _forecast(key, probabilities)}, {})}
        return score

    equal = search.run_scoped_trials(plan, evaluate(False), budget=_budget(), universes=universe)
    assert search.select_common(equal, catalog, definitions, "target", ("f1", "f2")).winner is active
    better = search.run_scoped_trials(plan, evaluate(True), budget=_budget(), universes=universe)
    picked = search.select_common(better, catalog, definitions, "target", ("f1", "f2"))
    assert picked.status == "candidate_selected" and picked.winner is canonical.spec
    assert better.evaluated_trials == 6


def test_common_masks_remove_selective_refusal_advantage_and_are_target_specific():
    catalog, definitions = _toy_catalog()
    folds = (fold("f1", "easy-a", "hard-a"), fold("f2", "easy-b", "hard-b"))
    scoped = {"moneyline": catalog, "totals": catalog}
    universe = _universes(folds, scoped)
    plan = search.plan_evaluations(folds, scoped)

    def evaluate(spec, item, targets):
        incumbent = spec is catalog.incumbent
        batches = {}
        for target in targets:
            easy, hard = universe[item.block_id, target]
            if target == "moneyline":
                predictions = {easy: _forecast(easy, (0.95, 0.05), fallback="scored_fallback")} if incumbent else {
                    easy: _forecast(easy, (0.90, 0.10)),
                }
                if incumbent:
                    predictions[hard] = _forecast(hard, (0.05, 0.95))
                refusals = {} if incumbent else {hard: "missing_fit"}
            else:
                probabilities = (0.50, 0.50) if incumbent else (0.90, 0.10)
                predictions = {key: _forecast(key, probabilities) for key in (easy, hard)}
                refusals = {}
            batches[target] = search.ForecastBatch((hard, easy), predictions, refusals)
        return batches

    result = search.run_scoped_trials(plan, evaluate, budget=_budget(), universes=universe)
    assert result.is_complete(), result.issue
    assert result.trials[1].mean_loss < result.trials[0].mean_loss
    selections = {
        target: search.select_common(result, catalog, definitions, target, ("f1", "f2"))
        for target in scoped
    }
    moneyline, totals = selections.values()
    assert moneyline.winner is catalog.incumbent and totals.winner is catalog.specs[1]
    assert (moneyline.eligible_lines, moneyline.common_lines, moneyline.nonempty_blocks) == (4, 2, 2)
    assert (totals.eligible_lines, totals.common_lines, totals.nonempty_blocks) == (4, 4, 2)
    assert moneyline.blocks[0].coverages[0].fallback_reasons == (("scored_fallback", 1),)
    assert moneyline.blocks[0].coverages[1].refusal_reasons == (("missing_fit", 1),)
    assert moneyline.mask_sha256 != totals.mask_sha256
    assert moneyline.blocks[0].common_keys == (universe["f1", "moneyline"][0],)
    shuffled = {context: tuple(reversed(keys)) for context, keys in reversed(tuple(universe.items()))}
    rerun = search.run_scoped_trials(plan, evaluate, budget=_budget(), universes=shuffled)
    repeat = search.select_common(rerun, catalog, definitions, "moneyline", ("f1", "f2"))
    assert repeat == moneyline and rerun.trials == result.trials


def test_empty_later_block_is_visible_and_cannot_rewrite_inner_selection():
    catalog, definitions = _toy_catalog()
    records, source_days, days = _dated_records((2,) * 6)
    folds, _ = search.selected_prefix_folds(_cohort(records, source_days, days[-1:]))
    target = "target"
    plan = search.plan_evaluations(folds, {target: catalog})
    universe = _universes(folds, (target,))
    early = search.nested_fold_ids(folds[-1], folds)
    assert early == tuple(days[2:4])

    def evaluate(change_later):
        def score(spec, item, targets):
            keys = universe[item.block_id, target]
            incumbent = spec is catalog.incumbent
            probabilities = (0.8, 0.2) if incumbent else (0.95, 0.05)
            if not incumbent and item.block_id == folds[1].block_id:
                probabilities = (0.75, 0.25)
            if change_later and item is folds[-1]:
                keys_covered = keys[:1] if incumbent else keys[1:]
            else:
                keys_covered = keys
            return {target: search.ForecastBatch(
                keys, {key: _forecast(key, probabilities) for key in keys_covered},
                {key: "unavailable" for key in keys if key not in keys_covered},
            )}
        return score

    original = search.run_scoped_trials(plan, evaluate(False), budget=_budget(), universes=universe)
    changed = search.run_scoped_trials(plan, evaluate(True), budget=_budget(), universes=universe)
    first = search.select_common(original, catalog, definitions, target, early, require_all=False)
    after = search.select_common(changed, catalog, definitions, target, early, require_all=False)
    assert first == after and first.winner is catalog.incumbent
    assert first.selection is not None and first.selection.best_score < -math.log(0.80) <= first.selection.threshold
    assert tuple(original.batches[key] for key in original.batches if key[0] in early) == tuple(
        changed.batches[key] for key in changed.batches if key[0] in early
    )
    all_blocks = tuple(item.block_id for item in folds)
    final = search.select_common(changed, catalog, definitions, target, all_blocks)
    assert final.winner is catalog.incumbent and final.selection is None
    assert final.status == search.INSUFFICIENT_COMPARABLE_SEARCH_FOLDS
    assert (final.eligible_lines, final.common_lines, final.nonempty_blocks) == (6, 4, 2)
    assert final.blocks[-1].common_keys == ()
    assert search.select_common(changed, catalog, definitions, target, all_blocks, require_all=False).selection
    warmup = search.select_common(changed, catalog, definitions, target, early[:1], require_all=False)
    assert warmup.status == search.INSUFFICIENT_COMPARABLE_INNER_FOLDS
    assert warmup.selection is None and warmup.winner is catalog.incumbent


@pytest.mark.parametrize("cap, count, exhausted", [(3, 3, True), (4, 4, False), (5, 4, False)])
def test_physical_budget_stops_before_extra_fit_and_retains_refused_fits(cap, count, exhausted):
    catalog, definitions = _toy_catalog()
    folds = (fold("f1", "a"), fold("f2", "b"))
    calls = []
    plan = search.plan_evaluations(folds, {"target": catalog})
    universe = _universes(folds, ("target",))

    def evaluate(spec, item, targets):
        calls.append((item.block_id, spec.spec_sha256()))
        keys = universe[item.block_id, "target"]
        return {"target": search.ForecastBatch(keys, {}, dict.fromkeys(keys, "cold_start"))}

    result = search.run_scoped_trials(plan, evaluate, budget=_budget(cap), universes=universe)
    assert result.planned_trials == 4
    assert result.budget_exhausted is exhausted
    assert result.completed_trials == result.aborted_trials == 0
    assert result.empty_trials == result.evaluated_trials == len(calls) == count
    assert all(item.status == search.TRIAL_NO_EVENTS and item.mean_loss is None for item in result.trials)
    if not exhausted:
        selection = search.select_common(result, catalog, definitions, "target", ("f1", "f2"))
        assert selection.selection is None and selection.winner is catalog.incumbent
        assert (selection.eligible_lines, selection.common_lines, selection.nonempty_blocks) == (2, 0, 0)
        assert all(block.coverages[0].refusal_reasons == (("cold_start", 1),) for block in selection.blocks)


def test_scoped_trial_records_failed_callback_and_checks_after_last_score():
    catalog, _ = _toy_catalog()
    folds = (fold("f1", "a"),)
    plan = search.plan_evaluations(folds, {"target": catalog})
    universe = _universes(folds, ("target",))
    key, = universe["f1", "target"]
    expired = False

    def evaluate(spec, item, targets):
        nonlocal expired
        if spec is catalog.incumbent:
            return {"target": search.ForecastBatch((key,), {key: _forecast(key, (0.5, 0.5))}, {})}
        expired = True
        return {"target": search.ForecastBatch((key,), {key: _forecast(key, (0.6, 0.4))}, {})}

    result = search.run_scoped_trials(plan, evaluate, budget=search.SearchBudget(2, lambda: expired), universes=universe)
    assert result.budget_exhausted and result.completed_trials == 2
    assert result.trials[-1].mean_loss == -math.log(0.6)

    def malformed(spec, item, targets):
        if spec is catalog.incumbent:
            return evaluate(spec, item, targets)
        return {"target": search.ForecastBatch((key,), {}, {})}

    aborted = search.run_scoped_trials(plan, malformed, budget=_budget(), universes=universe)
    assert aborted.evaluated_trials == 2 and aborted.completed_trials == aborted.aborted_trials == 1
    assert aborted.trials[-1].status == search.TRIAL_ABORTED
    assert "exactly one" in aborted.issue
    assert len(aborted.batches) == 1


def test_sealed_universe_and_probability_validation_precede_any_scoring():
    catalog, _ = _toy_catalog()
    folds = (fold("f1", "a"),)
    plan = search.plan_evaluations(folds, {"target": catalog})
    key = _line("a", "target")
    alien = _line("outside-test-fold", "target")
    calls = []

    def evaluate(spec, item, targets):
        calls.append(spec)
        row = _forecast(key, (float("nan"), 0.5))
        return {"target": search.ForecastBatch((key,), {key: row}, {})}

    for keys in ((key, key), (alien,)):
        with pytest.raises(ValueError, match="duplicate keys|test fold"):
            search.run_scoped_trials(plan, evaluate, budget=_budget(), universes={("f1", "target"): keys})
    assert not calls
    malformed = search.run_scoped_trials(
        plan, evaluate, budget=_budget(), universes={("f1", "target"): (key,)},
    )
    assert malformed.aborted_trials == malformed.started_trials == len(calls) == 1
    assert malformed.trials[0].mean_loss is None
    assert "class order and realization" in malformed.issue


def test_precise_cutoff_and_first_holdout_barriers_fail_before_folds():
    first = dt.datetime(2026, 1, 1, 15, tzinfo=UTC)
    hour = dt.timedelta(hours=1)
    records = [
        _Record("older", "g1", first, first + hour),
        _Record("recent", "g2", first + 2 * hour, first + 4 * hour),
        _Record("holdout", "g3", first + 4 * hour, first + 5 * hour),
    ]
    cutoff = first + 6 * hour
    with pytest.raises(ValueError, match="strictly before the first holdout"):
        search.validate_cohort(records, ("g3",), data_cutoff=cutoff)
    with pytest.raises(ValueError, match="temporal suffix"):
        search.validate_cohort(records, ("g2",), data_cutoff=cutoff)
    with pytest.raises(ValueError, match="aware"):
        search.validate_cohort([replace(records[0], kickoff=first.replace(tzinfo=None)), *records[1:]], ("g3",), data_cutoff=cutoff)
    records[1] = replace(records[1], available_at=first + 4 * hour - dt.timedelta(microseconds=1))
    with pytest.raises(ValueError, match="before data_cutoff"):
        search.validate_cohort(records, ("g3",), data_cutoff=records[2].available_at)
    with pytest.raises(ValueError, match="incomplete"):
        search.validate_cohort(records, ("g3",), data_cutoff=cutoff, complete_groups={"g1", "g2"})
    cohort = search.validate_cohort(records, ("g3",), data_cutoff=cutoff)
    assert cohort.first_holdout_prediction == first + 4 * hour
    heldout, _ = search.holdout_prefix_folds(cohort)
    assert tuple(heldout[0].train_keys) == ("older", "recent")


def test_precise_temporal_comparison_uses_instants_across_repeated_local_hour():
    east = ZoneInfo("America/New_York")
    kickoff = dt.datetime(2026, 11, 1, 1, 10, tzinfo=east)
    published = kickoff.replace(minute=50)
    boundary = kickoff.replace(minute=5, fold=1)
    cutoff = boundary.replace(hour=3)
    records = (
        _Record("search", "one", kickoff, published),
        _Record("holdout", "two", boundary, boundary.replace(minute=40)),
    )
    cohort = search.validate_cohort(records, ("two",), data_cutoff=cutoff)
    assert cohort.first_holdout_prediction == boundary.astimezone(UTC)
    folds, skipped = search.holdout_prefix_folds(cohort, min_train_blocks=1)
    assert not skipped and tuple(folds[0].train_keys) == ("search",)


def test_source_timestamps_require_real_instants_and_preserve_microseconds():
    east = ZoneInfo("America/New_York")
    repeated = dt.datetime(2026, 11, 1, 1, 30, 0, 123456, tzinfo=east)
    for local, utc_hour in ((repeated, 5), (repeated.replace(fold=1), 6)):
        assert search.as_utc_instant(local, "cutoff") == dt.datetime(
            2026, 11, 1, utc_hour, 30, 0, 123456, tzinfo=UTC,
        )
    missing_hour = dt.datetime(2026, 3, 8, 2, 30, tzinfo=east)
    for value in (missing_hour, missing_hour.replace(fold=1), repeated.replace(tzinfo=None), "2026-11-01"):
        with pytest.raises(ValueError, match="cutoff"):
            search.as_utc_instant(value, "cutoff")
    records = (
        _Record("early", "a", missing_hour, missing_hour + dt.timedelta(hours=3)),
        _Record("later", "b", repeated, repeated + dt.timedelta(hours=3)),
    )
    with pytest.raises(ValueError, match="kickoff"):
        search.validate_cohort(records, ("b",), data_cutoff=repeated + dt.timedelta(days=1))


def test_duplicated_delayed_source_clocks_are_compared_by_instant():
    east = ZoneInfo("America/New_York")
    day = dt.date(2026, 11, 1)
    tomorrow = day + dt.timedelta(days=1)
    early = dt.datetime(2026, 11, 1, 1, 30, tzinfo=east)
    late = early.replace(fold=1)
    days = {"a": search.SourceDay("Eastern", day, day, late),
            "b": search.SourceDay("Eastern", tomorrow, tomorrow)}
    records = (_Record("a", "a", available_at=early), _Record("b", "b"))
    kwargs = dict(data_cutoff=dt.datetime(2026, 11, 3, 12, tzinfo=UTC), source_timezone=east,
                  last_complete_date=tomorrow, source_days=days)
    with pytest.raises(ValueError, match="inconsistent observed availability"):
        search.validate_cohort(records, ("b",), **kwargs)
    equal = (replace(records[0], available_at=late.astimezone(UTC)), records[1])
    cohort = search.validate_cohort(equal, ("b",), **kwargs)
    assert cohort.search_group_ids == ("a",) and cohort.holdout_group_ids == ("b",)


def test_group_collection_validation_precedes_search_and_holdout_selection():
    t = dt.datetime(2026, 1, 1, tzinfo=UTC)
    records = tuple(_Record(k, k, t + dt.timedelta(days=i), t + dt.timedelta(days=i, hours=1))
                    for i, k in enumerate(("a", "b", "c")))
    kwargs = dict(data_cutoff=t + dt.timedelta(days=4))
    for invalid in ("c", b"c", ("c", "c"), ("",), (17,), (["c"],), ("unknown",)):
        with pytest.raises(ValueError):
            search.validate_cohort(records, invalid, **kwargs)
    for invalid in ("abc", b"abc", ("a", "b", "c", "c"), ("a", "b", "c", "unknown")):
        with pytest.raises(ValueError):
            search.validate_cohort(records, ("c",), complete_groups=invalid, **kwargs)
    cohort = search.validate_cohort(records, ("c",), **kwargs)
    for invalid in ("a", b"a", ("a", "a"), ("z",), ([],)):
        with pytest.raises(ValueError):
            search.selected_prefix_folds(cohort, selected_group_ids=invalid)


def test_source_row_indices_preserve_simultaneous_fixture_order_in_prefixes():
    records, source_days, days = _dated_records((2, 1, 1))
    keys = [r.event_key for r in records]
    kwargs = dict(data_cutoff=dt.datetime(1970, 1, 4, tzinfo=UTC), source_days=source_days,
                  source_timezone=UTC, last_complete_date=dt.date(1970, 1, 3))
    positions = dict(zip(keys, (9649, 9648, 9658, 9700), strict=True))
    cohort = search.validate_cohort(records, days[-1:], source_order=positions, **kwargs)
    positions[keys[0]] = 17
    folds, skipped = search.holdout_prefix_folds(cohort)
    assert not skipped and tuple(folds[0].train_keys) == (keys[1], keys[0], keys[2])
    default = search.validate_cohort(records, days[-1:], **kwargs)
    assert tuple(default.search_records) == tuple(records[:3])
    for invalid in ({}, positions | {keys[0]: True}, positions | {keys[0]: -1},
                    positions | {keys[0]: 9648}, positions | {"unrelated": 9000}):
        with pytest.raises(ValueError, match="source_order"):
            search.validate_cohort(records, days[-1:], source_order=invalid, **kwargs)


def test_date_only_delayed_labels_respect_the_named_calendar():
    records, source_days, days = _dated_records((1,) * 6)
    prior = records[1].event_key
    source_days[prior] = replace(source_days[prior], label_date=source_days[records[2].event_key].prediction_date)
    cohort = _cohort(records, source_days, days[-1:])
    folds, skipped = search.selected_prefix_folds(cohort)
    assert [item.block_id for item in skipped] == days[2:3]
    assert skipped[0].reason == "prior_block_prefix_incomplete_at_cutoff"
    assert [item.block_id for item in folds] == days[3:-1]
    assert folds[0].source_day == dt.date.fromisoformat(days[3])
    assert folds[0].source_calendar == "source-game-day"
    assert cohort.first_holdout_prediction == dt.date.fromisoformat(days[-1])
    assert cohort.source_calendar == "source-game-day"
    source_days[prior] = replace(
        source_days[prior], observed_label_at=dt.datetime.fromisoformat(days[-1] + "T01:00:00+00:00"),
    )
    with pytest.raises(ValueError, match="strictly before the first holdout"):
        _cohort(records, source_days, days[-1:])


def test_large_source_inventory_only_constructs_selected_folds_with_shared_prefixes():
    records, source_days, days = _dated_records((1,) * 16_538)
    selected = days[-25:-1]
    cohort = _cohort(list(reversed(records)), source_days, days[-1:])
    folds, skipped = search.selected_prefix_folds(cohort, selected_group_ids=reversed(selected))
    assert not skipped and len(folds) == 24
    assert [item.block_id for item in folds] == selected
    assert len(folds[0].train_keys) == 16_513
    assert len(folds[-1].train_keys) == 16_536
    backing = folds[0].train_keys.keys
    assert len(backing) == 16_537 and all(item.train_keys.keys is backing for item in folds)
    assert folds[-1].train_keys[0] == records[0].event_key
    assert folds[-1].train_keys[-1] == records[-3].event_key
    assert search.nested_fold_ids(folds[-1], folds) == tuple(selected[:-1])


def test_training_only_days_count_in_each_season_and_never_become_outer_folds():
    records, source_days, days = _dated_records((2,) * 12)
    first_season, second_season = set(days[:5]), set(days[5:])
    source_days = {
        row.event_key: replace(source_days[row.event_key],
                               history_partition="first" if row.group_id in first_season else "second")
        for row in records
    }
    cohort = _cohort(records, source_days, days[-2:])
    training = (*days[:3], *days[5:8])
    outer, skipped = search.selected_prefix_folds(cohort, training_only_group_ids=training)

    assert not skipped
    assert cohort.search_group_ids == tuple(days[:-2])
    assert [fold.block_id for fold in outer] == [*days[3:5], *days[8:10]]
    assert outer[0].train_block_ids == tuple(days[:3])
    assert outer[2].train_block_ids == tuple(days[5:8])
    assert set(outer[0].train_keys) == {row.event_key for row in records if row.group_id in days[:3]}
    assert set(outer[2].train_keys) == {row.event_key for row in records if row.group_id in days[5:8]}
    assert search.nested_fold_ids(outer[1], outer) == (days[3],)
    assert search.nested_fold_ids(outer[2], outer) == ()
    assert search.nested_fold_ids(outer[3], outer) == (days[8],)

    holdout, holdout_skips = search.holdout_prefix_folds(cohort)
    assert not holdout_skips and [fold.block_id for fold in holdout] == days[-2:]
    assert all(set(fold.train_block_ids) <= second_season for fold in holdout)
    assert set(holdout[-1].train_block_ids) - set(holdout[0].train_block_ids) == {days[-2]}
    with pytest.raises(ValueError, match="cannot include training-only"):
        search.selected_prefix_folds(cohort, selected_group_ids=days[:-2], training_only_group_ids=training)
    with pytest.raises(ValueError, match="complete and inside"):
        search.selected_prefix_folds(cohort, training_only_group_ids=days[-2:])


def test_partitioned_folds_preserve_late_and_incomplete_day_reasons():
    records, source_days, days = _dated_records((1,) * 11)
    source_days = {
        row.event_key: replace(source_days[row.event_key], history_partition="first" if index < 5 else "second")
        for index, row in enumerate(records)
    }
    late_day = source_days[records[8].event_key].prediction_date
    source_days[records[2].event_key] = replace(
        source_days[records[2].event_key], observed_label_at=dt.datetime.combine(late_day, dt.time(1), UTC),
    )
    cohort = search.validate_cohort(
        records, days[-1:], data_cutoff=dt.datetime.fromisoformat(days[-1] + "T00:00:00+00:00") + dt.timedelta(days=1),
        source_days=source_days, source_timezone=UTC, last_complete_date=dt.date.fromisoformat(days[-1]),
        complete_groups=set(days) - {days[9]},
    )
    folds, skipped = search.selected_prefix_folds(cohort, training_only_group_ids=(*days[:3], *days[5:8]))
    assert [(skip.block_id, skip.reason) for skip in skipped] == [
        (days[3], "prior_block_prefix_incomplete_at_cutoff"),
        (days[4], "prior_block_prefix_incomplete_at_cutoff"),
        (days[9], "target_block_incomplete"),
    ]
    assert [fold.block_id for fold in folds] == [days[8]]
    assert tuple(folds[0].train_block_ids) == tuple(days[5:8])
    holdout, holdout_skips = search.holdout_prefix_folds(cohort)
    assert not holdout_skips and holdout[0].train_block_ids == tuple(days[5:9])
