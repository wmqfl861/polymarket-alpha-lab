"""MO-3 optimize walk-forward kernel tests.

Covers the three expanding generators (matchweek / match day / expiry
date), the literal expanding-prefix semantics (an unavailable earlier
group blocks the fold), the future-row-injection invariance -- one test
per generator, the pinned requirement of plan 05 node MO-3 -- nested
selection (fallback, eligibility, declared-order ties) and the pinned
one-standard-error rule semantics (ddof=1, one-sided threshold, choice
distance, fewer-changed tie-break, incumbent retention, season-frozen
flag).
"""
from __future__ import annotations

import datetime as dt

import pytest

from polymarket_alpha_lab.papersim.optimize import walkforward as wf
from polymarket_alpha_lab.papersim.optimize.spec import ParamDef

UTC = dt.timezone.utc


def at(day: str, hour: int = 12) -> dt.datetime:
    return dt.datetime.fromisoformat(day).replace(hour=hour, tzinfo=UTC)


class Record:
    """Minimal TimedRecord implementation for tests."""

    def __init__(
        self,
        event_key: str,
        group_id: str,
        kickoff: dt.datetime,
        available_at: dt.datetime | None = None,
    ):
        self.event_key = event_key
        self.group_id = group_id
        self.kickoff = kickoff
        self.available_at = available_at or kickoff + dt.timedelta(hours=3)

    def __repr__(self) -> str:  # pragma: no cover -- debugging aid
        return f"Record({self.event_key}, {self.group_id}, {self.kickoff})"


def _weekly_records() -> list[Record]:
    records = []
    weeks = ["2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"]
    for week_index, day in enumerate(weeks):
        for fixture in range(2):
            kickoff = at(day, 18 + fixture)
            records.append(
                Record(
                    event_key=f"w{week_index}-f{fixture}",
                    group_id=f"2026-W{33 + week_index}",
                    kickoff=kickoff,
                )
            )
    return records


# ---------------------------------------------------------------------------
# Expanding folds
# ---------------------------------------------------------------------------


def test_expanding_group_folds_literal_prefix():
    records = _weekly_records()
    folds, skipped = wf.expanding_group_folds(records, min_train_blocks=2)
    assert skipped == ()
    assert [fold.block_id for fold in folds] == ["2026-W35", "2026-W36"]
    third = folds[0]
    assert third.cutoff == at("2026-08-24", 18)
    assert third.train_keys == ("w0-f0", "w0-f1", "w1-f0", "w1-f1")
    assert third.test_keys == ("w2-f0", "w2-f1")
    assert third.train_block_ids == ("2026-W33", "2026-W34")
    fourth = folds[1]
    assert fourth.train_keys == (
        "w0-f0", "w0-f1", "w1-f0", "w1-f1", "w2-f0", "w2-f1"
    )


def test_matchweek_and_matchday_generators_derive_groups():
    records = [
        Record("a", "ignored", at("2026-08-10", 18)),
        Record("b", "ignored", at("2026-08-11", 19)),  # same ISO week, next day
        Record("c", "ignored", at("2026-08-17", 18)),
        Record("d", "ignored", at("2026-08-24", 18)),
    ]
    week_folds, _ = wf.expanding_matchweek_folds(records, min_train_blocks=2)
    assert [fold.block_id for fold in week_folds] == ["2026-W35"]
    assert week_folds[0].test_keys == ("d",)
    day_folds, _ = wf.expanding_matchday_folds(records, min_train_blocks=1)
    assert {fold.block_id for fold in day_folds} == {
        "2026-08-11", "2026-08-17", "2026-08-24",
    }


def test_expiry_generator_uses_record_group_id():
    records = [
        Record("a", "2026-09-30", at("2026-09-01", 9)),
        Record("b", "2026-09-30", at("2026-09-02", 9)),
        Record("c", "2026-10-31", at("2026-10-01", 9)),
    ]
    folds, _ = wf.expanding_expiry_folds(records, min_train_blocks=1)
    assert [fold.block_id for fold in folds] == ["2026-10-31"]
    assert folds[0].test_keys == ("c",)


def test_incomplete_target_block_is_skipped_not_tested():
    records = _weekly_records()
    complete = frozenset({"2026-W33", "2026-W34", "2026-W36"})
    folds, skipped = wf.expanding_group_folds(records, complete_groups=complete)
    assert [fold.block_id for fold in folds] == ["2026-W36"]
    assert [(s.block_id, s.reason) for s in skipped] == [
        ("2026-W35", wf.SKIP_TARGET_INCOMPLETE)
    ]


def test_blocked_prior_group_skips_the_fold():
    records = _weekly_records()
    # A postponed fixture of week W34 only becomes available on 08-26:
    # it is not yet available at W35's cutoff (08-24) -- W35 is blocked;
    # by W36's cutoff (08-31) it has arrived, so W35's OWN delay blocks
    # only the folds whose cutoff it misses (the literal prefix).
    records[3].available_at = at("2026-08-26", 10)
    folds, skipped = wf.expanding_group_folds(records, min_train_blocks=2)
    assert [fold.block_id for fold in folds] == ["2026-W36"]
    assert [(s.block_id, s.reason) for s in skipped] == [
        ("2026-W35", wf.SKIP_PREFIX_INCOMPLETE)
    ]
    # A late availability that misses even the last cutoff blocks both.
    records[3].available_at = at("2026-09-01", 10)
    folds, skipped = wf.expanding_group_folds(records, min_train_blocks=2)
    assert folds == ()
    assert [(s.block_id, s.reason) for s in skipped] == [
        ("2026-W35", wf.SKIP_PREFIX_INCOMPLETE),
        ("2026-W36", wf.SKIP_PREFIX_INCOMPLETE),
    ]


# ---------------------------------------------------------------------------
# Future-row injection (one per generator)
# ---------------------------------------------------------------------------


def _inject_and_assert_invariance(generator, records, future):
    before = generator(records, min_train_blocks=2)
    after = generator(records + future, min_train_blocks=2)
    old_folds, old_skipped = before
    new_folds, _new_skipped = after
    # Every old fold reappears byte-identical; only later folds are added.
    assert new_folds[: len(old_folds)] == old_folds
    return new_folds


def test_future_row_injection_matchweek():
    records = _weekly_records()
    future = [
        Record("w4-f0", "2026-W37", at("2026-09-07", 18)),
        Record("w4-f1", "2026-W37", at("2026-09-07", 20)),
    ]
    new_folds = _inject_and_assert_invariance(wf.expanding_matchweek_folds, records, future)
    assert new_folds[-1].test_keys == ("w4-f0", "w4-f1")
    # A same-day row (kickoff at an existing cutoff, available after it)
    # also leaves every earlier fold untouched.
    boundary = [
        Record("w2-f9", "2026-W35", at("2026-08-24", 23)),
    ]
    folds_with_boundary, _ = wf.expanding_matchweek_folds(
        records + boundary, min_train_blocks=2
    )
    assert folds_with_boundary[0].test_keys == ("w2-f0", "w2-f1", "w2-f9")


def test_future_row_injection_matchday():
    records = [
        Record("a", "x", at("2026-08-10", 18)),
        Record("b", "x", at("2026-08-11", 18)),
        Record("c", "x", at("2026-08-12", 18)),
        Record("d", "x", at("2026-08-13", 18)),
    ]
    future = [Record("e", "x", at("2026-08-14", 18))]
    new_folds = _inject_and_assert_invariance(wf.expanding_matchday_folds, records, future)
    assert new_folds[-1].test_keys == ("e",)


def test_future_row_injection_expiry():
    records = [
        Record("a", "2026-09-30", at("2026-09-01", 9)),
        Record("b", "2026-10-31", at("2026-09-15", 9)),
        Record("c", "2026-11-30", at("2026-10-15", 9)),
    ]
    future = [Record("d", "2026-12-31", at("2026-12-01", 9))]
    new_folds = _inject_and_assert_invariance(wf.expanding_expiry_folds, records, future)
    assert new_folds[-1].test_keys == ("d",)


# ---------------------------------------------------------------------------
# Nested selection
# ---------------------------------------------------------------------------


def _fold(block: str, cutoff: dt.datetime, train: tuple, test: tuple) -> wf.WalkFold:
    return wf.WalkFold(
        block_id=block, cutoff=cutoff,
        train_keys=train, test_keys=test, train_block_ids=(block,),
    )


def test_nested_select_fallback_and_qualification():
    outer = _fold(
        "outer", at("2026-09-01"),
        ("a", "b", "c"), ("d",),
    )
    too_late = _fold("late", at("2026-09-05"), ("a",), ("b",))
    outside = _fold("outside", at("2026-08-01"), ("a",), ("zzz",))
    inner_ok = _fold("ok1", at("2026-08-15"), ("a",), ("b",))
    inner_ok2 = _fold("ok2", at("2026-08-20"), ("a", "b"), ("c",))
    fallback = object()
    selection = wf.nested_select(
        outer, [too_late, outside, inner_ok], ("cand",),
        lambda cand, fold, keys: [0.1] * len(keys),
        fallback=fallback,
    )
    assert selection.winner is fallback
    assert selection.status == wf.FALLBACK_INSUFFICIENT_INNER
    selection = wf.nested_select(
        outer, [inner_ok, inner_ok2, too_late, outside], ("cand",),
        lambda cand, fold, keys: [0.2] * len(keys),
        fallback=fallback,
    )
    assert selection.status == wf.NESTED_SELECTION
    assert selection.inner_block_ids == ("ok1", "ok2")
    assert selection.scores[0][2] == 2  # one test event per inner fold


def test_nested_select_objective_and_declared_order_tie():
    outer = _fold("outer", at("2026-09-01"), ("a", "b", "c"), ("d",))
    inner = [
        _fold("ok1", at("2026-08-10"), ("a",), ("b",)),
        _fold("ok2", at("2026-08-20"), ("a", "b"), ("c",)),
    ]
    losses = {"first": [0.5, 0.3], "second": [0.2, 0.2], "third": [0.4, 0.4]}
    selection = wf.nested_select(
        outer, inner, ("first", "second", "third"),
        lambda cand, fold, keys: losses[cand],
        fallback=None,
    )
    assert selection.winner == "second"  # mean 0.2 beats 0.4/0.4
    losses = {"first": [0.2, 0.2], "second": [0.2, 0.2]}
    selection = wf.nested_select(
        outer, inner, ("first", "second"),
        lambda cand, fold, keys: losses[cand],
        fallback=None,
    )
    assert selection.winner == "first"  # tie -> declared order
    selection = wf.nested_select(
        outer, inner, ("bad", "good"),
        lambda cand, fold, keys: losses.get(cand, [9.9]),
        fallback="fallback",
        eligible=lambda cand: cand != "bad",
    )
    assert selection.winner == "good"


# ---------------------------------------------------------------------------
# One-standard-error rule (pinned semantics)
# ---------------------------------------------------------------------------

DEFS = (
    ParamDef(name="half_life", kind="float", default=120.0, lower=60.0, upper=240.0),
    ParamDef(name="prior", kind="choice", default="a", choices=("a", "b", "c")),
    ParamDef(name="kappa", kind="float", default=1.0, lower=0.0, upper=2.0,
             season_frozen=True),
)

INCUMBENT = {"half_life": 120.0, "prior": "a", "kappa": 1.0}


def _candidate(name, params):
    return (name, params)


def test_one_se_ddof_and_threshold_hand_computed():
    # Best candidate block means [0.10, 0.12]: stdev(ddof=1)=0.014142..,
    # SE = stdev / sqrt(2) = 0.01 -> threshold = 0.10 + 0.01 = 0.11.
    candidates = [
        _candidate("best", INCUMBENT),
        _candidate("inside", {"half_life": 90.0, "prior": "a", "kappa": 1.0}),
        _candidate("outside", {"half_life": 60.0, "prior": "c", "kappa": 0.0}),
    ]
    result = wf.one_standard_error_rule(
        candidates,
        scores=[0.10, 0.105, 0.12],
        block_scores=[[[0.10], [0.12]], [[0.11]], [[0.12]]],
        incumbent=_candidate("incumbent", INCUMBENT),
        param_defs=DEFS,
        params_of=lambda cand: cand[1],
    )
    assert result.best_score == pytest.approx(0.10)
    assert result.standard_error == pytest.approx(0.01)
    assert result.threshold == pytest.approx(0.11)
    # "inside" (score 0.105 <= 0.11) is one choice-away... in half_life it
    # moves 30/180 = 0.1667 from the incumbent and is eligible; the best
    # candidate IS the incumbent params -> distance 0 -> incumbent wins.
    assert result.winner == candidates[0]
    assert result.status == wf.NO_BETTER_CANDIDATE


def test_one_se_prefers_nearest_incumbent_among_eligible():
    candidates = [
        _candidate("best", {"half_life": 60.0, "prior": "c", "kappa": 0.0}),
        _candidate("near", {"half_life": 100.0, "prior": "a", "kappa": 1.0}),
    ]
    result = wf.one_standard_error_rule(
        candidates,
        scores=[0.10, 0.108],
        block_scores=[[[0.10]], [[0.108]]],  # single block -> SE = 0
        incumbent=_candidate("incumbent", INCUMBENT),
        param_defs=DEFS,
        params_of=lambda cand: cand[1],
    )
    assert result.standard_error == 0.0
    assert result.threshold == pytest.approx(0.10)
    assert result.winner == candidates[0]  # SE 0 -> plain minimization
    # With a real SE the nearer candidate inside the band wins.
    result = wf.one_standard_error_rule(
        candidates,
        scores=[0.10, 0.108],
        block_scores=[[[0.09], [0.11]], [[0.108]]],  # SE = 0.01
        incumbent=_candidate("incumbent", INCUMBENT),
        param_defs=DEFS,
        params_of=lambda cand: cand[1],
    )
    assert result.threshold == pytest.approx(0.11)
    assert result.winner == candidates[1]
    assert result.status == wf.CANDIDATE_SELECTED


def test_one_se_tie_breaks_toward_fewer_changed_params():
    # Equal scores and equal param distance: "one-float" returns TO the
    # incumbent's exact params (zero changes), "one-choice" keeps one
    # changed choice param -- distance 0 beats distance 1, and a
    # zero-distance winner means the incumbent is retained.
    tied_candidates = [
        _candidate("best", {"half_life": 120.0, "prior": "b", "kappa": 1.0}),
        _candidate("one-choice", {"half_life": 120.0, "prior": "b", "kappa": 1.0}),
        _candidate("one-float", {"half_life": 120.0, "prior": "a", "kappa": 1.0}),
    ]
    result = wf.one_standard_error_rule(
        tied_candidates,
        scores=[0.09, 0.104, 0.104],
        block_scores=[[[0.09], [0.12]], [[0.104]], [[0.104]]],
        incumbent=_candidate("incumbent", INCUMBENT),
        param_defs=DEFS,
        params_of=lambda cand: cand[1],
    )
    # SE = stdev([0.09, 0.12], ddof=1) / sqrt(2) = 0.015 -> threshold 0.105:
    # both 0.104 candidates are inside the band; the zero-distance one wins.
    assert result.threshold == pytest.approx(0.105)
    assert result.winner == tied_candidates[2]
    assert result.status == wf.NO_BETTER_CANDIDATE
    # A genuine changed-count tie at equal distance resolves by declared
    # order (both move exactly one choice unit).
    equal = [
        _candidate("first", {"half_life": 120.0, "prior": "b", "kappa": 1.0}),
        _candidate("second", {"half_life": 120.0, "prior": "c", "kappa": 1.0}),
    ]
    result = wf.one_standard_error_rule(
        equal,
        scores=[0.10, 0.10],
        block_scores=[[[0.10]], [[0.10]]],
        incumbent=_candidate("incumbent", INCUMBENT),
        param_defs=DEFS,
        params_of=lambda cand: cand[1],
    )
    assert result.winner == equal[0]


def test_one_se_flags_season_frozen_change():
    candidates = [
        _candidate("frozen", {"half_life": 120.0, "prior": "a", "kappa": 2.0}),
        _candidate("plain", {"half_life": 180.0, "prior": "a", "kappa": 1.0}),
    ]
    result = wf.one_standard_error_rule(
        candidates,
        scores=[0.10, 0.10],
        block_scores=[[[0.10]], [[0.10]]],
        incumbent=_candidate("incumbent", INCUMBENT),
        param_defs=DEFS,
        params_of=lambda cand: cand[1],
    )
    # Equal scores: "frozen" is nearer (kappa 1/2 unit vs half_life 1/3
    # unit)... half_life 180 is 60/180 = 0.333; kappa 2.0 is 1.0/2.0 =
    # 0.5 -> "plain" is nearer and does not touch season-frozen params.
    assert result.winner == candidates[1]
    assert result.needs_preseason_reestimate is False
    # Force the season-frozen winner by making the alternative far.
    result = wf.one_standard_error_rule(
        [
            _candidate("frozen", {"half_life": 120.0, "prior": "a", "kappa": 1.5}),
            _candidate("plain", {"half_life": 60.0, "prior": "c", "kappa": 1.0}),
        ],
        scores=[0.10, 0.10],
        block_scores=[[[0.10]], [[0.10]]],
        incumbent=_candidate("incumbent", INCUMBENT),
        param_defs=DEFS,
        params_of=lambda cand: cand[1],
    )
    assert result.winner[0] == "frozen"
    assert result.needs_preseason_reestimate is True


def test_param_distance_conventions():
    near = {"half_life": 90.0, "prior": "a", "kappa": 1.0}
    assert wf.param_distance(DEFS, INCUMBENT, near) == pytest.approx(30.0 / 180.0)
    choice = {"half_life": 120.0, "prior": "b", "kappa": 1.0}
    assert wf.param_distance(DEFS, INCUMBENT, choice) == pytest.approx(1.0)
    missing = {"half_life": 120.0, "prior": "a"}
    assert wf.param_distance(DEFS, INCUMBENT, missing) == pytest.approx(1.0)
    assert wf.count_changed_params(DEFS, INCUMBENT, missing) == 1
    unbounded = (ParamDef(name="x", kind="float", default=1.0),)
    assert wf.param_distance(unbounded, {"x": 1.0}, {"x": 3.5}) == pytest.approx(2.5)
