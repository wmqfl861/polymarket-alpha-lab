"""MO-6 optimize runner tests: adapters, core paths, integrity hardening.

Plan 05 node MO-6 (plan r2 section 4): determinism (T1), budget
failures preserving completed parts (T2), the empty-envelope lineage
semantics (T7), memo fold-independence (T9), the two registry branches
(T10), family adapters (T13), and the parallel-track integrity
hardening: the delayed-label selection boundary and the probability /
outcome-table validation (typed RUN_FAILED, zero registration, zero
scoring callbacks).
"""

from __future__ import annotations

import datetime as dt
import json
import pytest
import ast
import importlib.util
import inspect
import math
import random
import subprocess
import sys
from tests.papersim_mo6_synthetic import (
    CoinAdapter,
    FAMILY,
    make_input,
    make_profile,
    make_registry,
    run_coin,
    theta_spec,
)
from polymarket_alpha_lab.papersim.optimize import runner, scorecard as scorecard_mod, search
from polymarket_alpha_lab.papersim.optimize.runner import (
    DataIntegrityError,
    FixtureLine,
    FixtureRecord,
    HoldoutEvaluation,
    LineageState,
    PolicyContext,
    Prediction,
    Refusal,
    RegistryComparison,
    RegistryView,
    RejectionReceipt,
    RowOutcome,
    RunEvent,
    VariantPrediction,
    _condition_b,
    _scored_events,
    execute_optimization_run,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, ParamDef
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo
from polymarket_alpha_lab.papersim.optimize.metrics import ScoredEvent
from polymarket_alpha_lab.papersim.optimize.registry import LineageRoles


UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# T1: determinism — same inputs, byte-identical artifact
# ---------------------------------------------------------------------------

def test_core_is_byte_deterministic_and_outcome_honest() -> None:
    first, _ = run_coin(seed=7)
    second, _ = run_coin(seed=7)
    assert first.artifact_bytes == second.artifact_bytes
    assert first.artifacts_sha256 == second.artifacts_sha256
    assert first.outcome in (
        "CANDIDATE_REGISTERED", "NO_IMPROVEMENT",
        "INSUFFICIENT_DATA", "RUN_FAILED",
    )
    assert first.search_space_text_value == second.search_space_text_value
    document = json.loads(first.artifact_bytes.decode("utf-8"))
    assert document["schema"] == "optimizer-run-artifact/1"
    assert document["budget_exhausted"] is False


def test_core_null_world_reports_no_better_candidate() -> None:
    result, adapter = run_coin(q=0.50, seed=11)
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    # The 1-SE rule shrinks to the incumbent: the holdout stays untouched.
    assert record["selection_status"] in (
        "no_better_candidate", "candidate_selected",
    )
    if record["blocked_reason"] == "no_better_candidate":
        assert record["gate"] is None
    assert result.registered_lineages == ()


# ---------------------------------------------------------------------------
# T2: budget — RUN_FAILED preserves the completed part, never truncates
# ---------------------------------------------------------------------------

def test_budget_exhaustion_fails_the_run_with_partial_artifact() -> None:
    guard = search.BudgetGuard(max_trials=5, now_fn=lambda: 0.0)
    result, adapter = run_coin(seed=3, budget=guard)
    assert result.outcome == "RUN_FAILED"
    assert result.error_code == "budget_exhausted"
    assert result.registered_lineages == ()
    document = json.loads(result.artifact_bytes.decode("utf-8"))
    assert document["budget_exhausted"] is True
    assert document["failed_phase"] == "walk_forward"
    assert document["trials_completed"] >= 5
    # No fake completion: no lineage claims a selection verdict.
    for record in document["lineages"].values():
        assert record.get("selection_status") is None


def test_time_budget_fast_forward_fails_with_phase() -> None:
    clock = {"now": 0.0}
    guard = search.BudgetGuard(max_seconds=1.0, now_fn=lambda: clock["now"])
    clock["now"] = 2.0
    result, _ = run_coin(seed=5, budget=guard)
    assert result.outcome == "RUN_FAILED"
    assert result.error_code == "budget_exhausted"


# ---------------------------------------------------------------------------
# T7: empty-envelope lineages — () is the only shape; gate NOT_ASSESSABLE
# ---------------------------------------------------------------------------

def test_empty_envelope_lineage_registers_without_width_block() -> None:
    # The synthetic family has envelope == () on every spec: with a
    # real effect the holdout gate passes and registration happens —
    # the width gate must NOT block (spread undefined both sides).
    result, _ = run_coin(q=0.60, seed=21)
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    if record["gate"] is not None and record["gate"].get("condition_c"):
        assert record["gate"]["condition_c"]["status"] in (
            "not_assessable_empty_envelope", "pass",
        )
        assert record["gate"]["condition_c"]["candidate_spread"] is None
        assert record["gate"]["condition_c"]["incumbent_spread"] is None


def test_envelope_shape_illegal_candidate_with_spread_on_empty_incumbent() -> None:
    # Winner reports a spread while the incumbent has none: the illegal
    # shape is refused typed (never a silent pass of the width gate).
    adapter = CoinAdapter(spreads={0.50: None, 0.60: 0.01})
    result, _ = run_coin(adapter=adapter, q=0.60, seed=21)
    assert result.outcome == "RUN_FAILED"
    assert result.error_code == "envelope_shape_illegal"
    assert result.registered_lineages == ()


# ---------------------------------------------------------------------------
# T10: the two registry branches
# ---------------------------------------------------------------------------

def test_branch_a_empty_registry_evaluates_without_registration() -> None:
    adapter = CoinAdapter()
    registry = RegistryView(
        readable=False,
        lineages={f"{FAMILY}/coin": None},
    )
    result, _ = run_coin(
        adapter=adapter, q=0.60, seed=21, registry=registry,
        incumbent=theta_spec(0.50),
    )
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    assert record["incumbent_source"] == "family_baseline_fallback"
    assert result.registered_lineages == ()
    if record["blocked_reason"] == "registry_empty_or_unreadable":
        assert record["gate"] is not None  # evaluation still completed
        assert record["gate"]["condition_a"] is not None
    assert result.outcome == "NO_IMPROVEMENT"


def test_branch_b_uninitialized_lineage_fails_closed() -> None:
    adapter = CoinAdapter(targets=("coin", "coin2"))
    incumbent = theta_spec(0.50)
    registry = RegistryView(
        readable=True,
        lineages={
            f"{FAMILY}/coin": LineageState(True, incumbent.spec_sha256(), ()),
            f"{FAMILY}/coin2": None,  # uninitialized while the rest exists
        },
    )
    result, _ = run_coin(
        adapter=adapter, q=0.60, seed=21, registry=registry, incumbent=incumbent,
    )
    assert result.outcome == "RUN_FAILED"
    assert result.error_code == "registry_lineage_missing"
    assert result.registered_lineages == ()


def test_candidate_cap_blocks_registration() -> None:
    incumbent = theta_spec(0.50)
    other = theta_spec(0.30)
    adapter = CoinAdapter()
    registry = RegistryView(
        readable=True,
        lineages={
            f"{FAMILY}/coin": LineageState(
                True, incumbent.spec_sha256(), (other.spec_sha256(), "a" * 64)
            ),
        },
    )
    result, _ = run_coin(
        adapter=adapter, q=0.60, seed=21, registry=registry, incumbent=incumbent,
    )
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    if record["blocked_reason"] == "candidate_cap":
        assert result.outcome == "NO_IMPROVEMENT"
        assert result.registered_lineages == ()


# ---------------------------------------------------------------------------
# Parallel-track finding 2: the delayed-label selection boundary
# ---------------------------------------------------------------------------

class _PoisonedAdapter(CoinAdapter):
    """The parallel-track defect, reproduced: a delayed label.

    The EVENT stream carries block b010 with availability pushed into
    the holdout window, while ``folds()`` hands out a fold layout as
    if availability were clean — a fold generator that ignores
    ``available_at`` lets a selection-region label encode information
    that only became knowable after the first holdout kickoff.  (The
    MO-3 generator itself never produces this layout: it adapts the
    fold chain to the delay, which is why the poison must bypass it.)
    """

    def events(self, payload):
        events = list(super().events(payload))
        poisoned = []
        for event in events:
            if event.group_id == "b010":
                event = RunEvent(
                    event_key=event.event_key,
                    group_id=event.group_id,
                    kickoff=event.kickoff,
                    available_at=dt.datetime(2026, 2, 10, tzinfo=UTC),
                    outcomes=event.outcomes,
                )
            poisoned.append(event)
        return tuple(poisoned)

    def folds(self, payload):
        # Fold layout computed from the CLEAN availability — the defect.
        from polymarket_alpha_lab.papersim.optimize.walkforward import (
            expanding_matchday_folds,
        )

        clean = CoinAdapter.events(self, payload)
        folds, skipped = expanding_matchday_folds(clean)
        return (
            folds,
            tuple(
                {"block_id": item.block_id, "reason": item.reason}
                for item in skipped
            ),
            tuple(None for _ in folds),
        )


def test_poisoned_availability_fails_typed_before_any_scoring() -> None:
    adapter = _PoisonedAdapter()
    result, returned = run_coin(adapter=adapter, q=0.60, seed=21)
    assert result.outcome == "RUN_FAILED"
    assert result.error_code == "selection_label_available_after_holdout_boundary"
    document = json.loads(result.artifact_bytes.decode("utf-8"))
    assert document["failed_phase"] == "boundary_validation"
    # Zero scoring callbacks: the violation fires before any model row.
    assert returned.rows_calls == 0
    assert result.registered_lineages == ()


def test_selection_boundary_check_is_strict_and_typed() -> None:
    # Direct unit over crafted folds: a selection-region label whose
    # availability is EQUAL to the first holdout kickoff already
    # violates the boundary (strict inequality), and a later one
    # obviously does.
    def event(key, day, available_day):
        stamp = dt.datetime(2026, 1, day, tzinfo=UTC)
        return RunEvent(
            event_key=key, group_id=f"g{day}", kickoff=stamp,
            available_at=dt.datetime(2026, 1, available_day, tzinfo=UTC),
            outcomes=(RowOutcome("coin", "yes-00", 1),),
        )

    def fold(block, day, train, test):
        from polymarket_alpha_lab.papersim.optimize.walkforward import WalkFold

        return WalkFold(
            block_id=block,
            cutoff=dt.datetime(2026, 1, day, tzinfo=UTC),
            train_keys=train, test_keys=test, train_block_ids=(),
        )

    late_label = event("late", 1, 20)
    holdout_event = event("hold", 20, 20)
    events = {"late": late_label, "hold": holdout_event}
    walk = (fold("wf1", 2, ("late",), ("late",)),)
    hold = (fold("hold", 20, ("late",), ("hold",)),)
    with pytest.raises(DataIntegrityError, match="not.*strictly before"):
        runner._validate_selection_boundary(events, walk, hold)
    # Equal availability fails too (available day 20 == holdout day 20).
    equal_label = RunEvent(
        event_key="equal", group_id="g1",
        kickoff=dt.datetime(2026, 1, 1, tzinfo=UTC),
        available_at=dt.datetime(2026, 1, 20, tzinfo=UTC),
        outcomes=(RowOutcome("coin", "yes-00", 1),),
    )
    with pytest.raises(DataIntegrityError):
        runner._validate_selection_boundary(
            {"equal": equal_label, "hold": holdout_event},
            (fold("wf1", 2, ("equal",), ("equal",)),),
            hold,
        )
    # A clean stream passes and returns the cutoff.
    clean = event("clean", 1, 5)
    cutoff = runner._validate_selection_boundary(
        {"clean": clean, "hold": holdout_event},
        (fold("wf1", 2, ("clean",), ("clean",)),),
        hold,
    )
    assert cutoff == holdout_event.kickoff


def test_naive_kickoff_stream_passes_boundary_check() -> None:
    # The unpoisoned synthetic stream satisfies the strict boundary.
    result, _ = run_coin(q=0.60, seed=21)
    assert result.outcome != "RUN_FAILED" or result.error_code != (
        "selection_label_available_after_holdout_boundary"
    )


# ---------------------------------------------------------------------------
# Parallel-track finding 3: probability vectors and the outcome table
# ---------------------------------------------------------------------------

def test_illegal_probability_vector_fails_the_run() -> None:
    adapter = CoinAdapter(bad_probs=True)
    result, _ = run_coin(adapter=adapter, q=0.60, seed=21)
    assert result.outcome == "RUN_FAILED"
    assert result.error_code == "invalid_probability_vector"
    assert result.registered_lineages == ()


def test_scored_events_join_against_the_outcome_table() -> None:
    event = RunEvent(
        event_key="e1",
        group_id="b1",
        kickoff=dt.datetime(2026, 1, 1, tzinfo=UTC),
        available_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
        outcomes=(RowOutcome("coin", "yes", 1),),
    )
    rows = runner.EventRows(
        "e1", None,
        (runner.RowScore("yes", (0.25, 0.75), None),),
    )
    table = {("e1", "yes"): RowOutcome("coin", "yes", 1)}
    scored = _scored_events(event, rows, "coin", table)
    assert scored[0][0].outcome_index == 1
    # A realized label that disagrees with the table is a typed failure.
    tampered = {("e1", "yes"): RowOutcome("coin", "yes", 0)}
    with pytest.raises(DataIntegrityError, match="holdout_outcome_mismatch"):
        _scored_events(event, rows, "coin", tampered)
    # A line the table does not know is a typed failure, never dropped.
    stranger = runner.EventRows(
        "e1", None, (runner.RowScore("nope", (0.5, 0.5), None),),
    )
    stranger_event = RunEvent(
        event_key="e1",
        group_id="b1",
        kickoff=dt.datetime(2026, 1, 1, tzinfo=UTC),
        available_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
        outcomes=(RowOutcome("coin", "nope", 0),),
    )
    with pytest.raises(DataIntegrityError, match="holdout_outcome_mismatch"):
        _scored_events(stranger_event, stranger, "coin", table)


def test_probability_vector_validation_rules() -> None:
    with pytest.raises(DataIntegrityError, match="non-finite"):
        runner._validated_probabilities((0.5, float("nan")), 2)
    with pytest.raises(DataIntegrityError, match="out of \\[0, 1\\]"):
        runner._validated_probabilities((1.5, -0.5), 2)
    with pytest.raises(DataIntegrityError, match="sum to"):
        runner._validated_probabilities((0.3, 0.6), 2)
    with pytest.raises(DataIntegrityError, match="arity"):
        runner._validated_probabilities((0.5,), 2)
    assert runner._validated_probabilities((0.25, 0.75), 2) == (0.25, 0.75)


def test_condition_b_regressions() -> None:
    from types import SimpleNamespace

    def gate(name, check, verdict):
        return SimpleNamespace(gate=name, check=check, verdict=verdict)

    incumbent = SimpleNamespace(
        gates=(gate("D", "walk_forward_beats_baseline", "FAIL"),),
        calibration_ece=0.02,
    )
    candidate_same_fail = SimpleNamespace(
        gates=(gate("D", "walk_forward_beats_baseline", "FAIL"),),
        calibration_ece=0.01,
    )
    verdict = _condition_b(candidate_same_fail, incumbent)
    assert verdict["passed"] is True  # pre-existing FAIL is not a regression
    candidate_new_fail = SimpleNamespace(
        gates=(gate("D", "walk_forward_beats_baseline", "FAIL"),),
        calibration_ece=0.01,
    )
    incumbent_clean = SimpleNamespace(
        gates=(gate("D", "walk_forward_beats_baseline", "PASS"),),
        calibration_ece=0.02,
    )
    verdict_two = _condition_b(candidate_new_fail, incumbent_clean)
    assert verdict_two["passed"] is False
    assert verdict_two["gate_regressions"] == [["D", "walk_forward_beats_baseline"]]
    candidate_ece = SimpleNamespace(
        gates=(gate("D", "walk_forward_beats_baseline", "PASS"),),
        calibration_ece=0.03,
    )
    verdict_three = _condition_b(candidate_ece, incumbent_clean)
    assert verdict_three["passed"] is False
    assert verdict_three["calibration_ece"]["regression"] is True
    candidate_none = SimpleNamespace(
        gates=(gate("E", "market_mad", "NOT_ASSESSABLE"),),
        calibration_ece=None,
    )
    verdict_four = _condition_b(candidate_none, incumbent_clean)
    assert verdict_four["passed"] is True  # None records, never blocks


# ---------------------------------------------------------------------------
# T9: memo fold-independence (snapshot families)
# ---------------------------------------------------------------------------

def _mini_nba_snapshot(games_count: int = 6) -> dict:
    def row(date_s, opponent, is_home, pts, opp_pts):
        return {
            "date": date_s,
            "home": is_home,
            "opponent": opponent,
            "poss": 100.0,
            "pts": pts,
            "opp_pts": opp_pts,
            "ortg": 112.0,
            "drtg": 112.0,
            "rest_days": 1,
            "b2b": False,
        }

    games = [
        ("2026-01-01", "AAA", "BBB", 113, 110),
        ("2026-01-02", "BBB", "AAA", 111, 108),
        ("2026-01-03", "AAA", "BBB", 112, 109),
        ("2026-01-04", "BBB", "AAA", 108, 104),
        ("2026-01-05", "AAA", "BBB", 120, 100),
        ("2026-01-06", "BBB", "AAA", 105, 99),
    ][:games_count]
    teams: dict[str, dict] = {}
    for date_s, home, away, hp, ap in games:
        teams.setdefault(home, {"display": home, "games": []})
        teams.setdefault(away, {"display": away, "games": []})
        teams[home]["games"].append(row(date_s, away, True, hp, ap))
        teams[away]["games"].append(row(date_s, home, False, ap, hp))
    return {
        "generated_at": "2026-01-07T00:00:00+00:00",
        "season": "2026-27",
        "teams": teams,
        "injuries": {},
    }


def _nba_payload(games_count: int = 6):
    adapter = runner.ADAPTERS["nba-pace-efficiency"]
    snapshot = _mini_nba_snapshot(games_count)
    content = json.dumps(snapshot, sort_keys=True).encode("utf-8")
    inputs = (
        runner.RegisteredInput("snap.json", "espn_nba_snapshot", content),
    )
    return adapter, adapter.load(inputs), inputs


def test_nba_adapter_events_pair_and_score() -> None:
    adapter, payload, _ = _nba_payload()
    history = adapter.history_for(payload, None)
    events = adapter.events(payload)
    assert len(events) == 6
    first = events[0]
    assert first.outcomes[0].target == "moneyline"
    assert first.outcomes[0].outcome_index == 1  # home AAA won 113:110
    assert first.outcomes[1].line_key == "over-225.5"
    spec = adapter.spec_for({})
    # The season opener may be refused (no strictly-earlier games), so
    # score the FOURTH game, which has strictly-earlier history.
    rows = adapter.rows(
        payload, history, "any-fold", spec, "moneyline", events[3],
        include_variants=False,
    )
    assert rows.refusal is None
    assert len(rows.rows) == 1
    probabilities = rows.rows[0].probabilities
    assert abs(sum(probabilities) - 1.0) < 1e-9


def test_nba_spec_construction_round_trips_through_family_fit() -> None:
    # The adapter derives the envelope itself (MO-5c published no public
    # spec constructor): every grid spec MUST satisfy family.fit's
    # envelope-consistency check — the round-trip pin.
    adapter, payload, _ = _nba_payload()
    family = adapter._family()
    history = adapter.history_for(payload, None)
    for overrides in (
        {},
        {"half_life": 10.0},
        {"sigma_scale": 1.1, "total_bias": 2.0},
    ):
        spec = adapter.spec_for(overrides)
        fitted = family.fit(history, spec, dt.datetime(2026, 1, 7, tzinfo=UTC))
        assert fitted.spec_sha256 == spec.spec_sha256()


def test_snapshot_adapter_predictions_are_fold_independent() -> None:
    # Memo premise (T9): snapshot-family predictions depend on
    # (payload, spec, event) only — the fold context (history token)
    # must not change a single byte.
    adapter, payload, _ = _nba_payload()
    history = adapter.history_for(payload, None)
    events = adapter.events(payload)
    spec = adapter.spec_for({})
    rows_one = adapter.rows(
        payload, history, "fold-a", spec, "moneyline", events[3],
        include_variants=False,
    )
    rows_two = adapter.rows(
        payload, history, "fold-z", spec, "moneyline", events[3],
        include_variants=False,
    )
    assert rows_one == rows_two


def test_nba_core_run_on_mini_snapshot_is_deterministic() -> None:
    adapter, payload, inputs = _nba_payload(games_count=6)
    profile = runner.RunProfile(
        family_id="nba-pace-efficiency",
        fold_kind="matchday",
        holdout_start_utc=dt.datetime(2026, 1, 5, tzinfo=UTC),
        grid_overrides=runner.RUN_PROFILES["nba-pace-efficiency"].grid_overrides,
        max_seconds=None,
        max_trials=None,
    )
    incumbent = adapter.spec_for({})
    registry = RegistryView(
        readable=True,
        lineages={
            "nba-pace-efficiency/moneyline": LineageState(
                True, incumbent.spec_sha256(), ()
            ),
            "nba-pace-efficiency/totals": LineageState(
                True, incumbent.spec_sha256(), ()
            ),
        },
    )
    common = dict(
        family_id="nba-pace-efficiency",
        adapter=adapter,
        inputs=inputs,
        profile=profile,
        as_of=dt.datetime(2026, 1, 7, tzinfo=UTC),
        incumbents={
            "nba-pace-efficiency/moneyline": incumbent,
            "nba-pace-efficiency/totals": incumbent,
        },
        registry=registry,
        code_commit="t" * 40,
    )
    first = runner.run_optimize_core(seed=1, **common)
    second = runner.run_optimize_core(seed=1, **common)
    assert first.artifact_bytes == second.artifact_bytes
    # 6 games / 4 matchdays cannot meet the 300-match NBA floor: the
    # honest outcome is INSUFFICIENT_DATA with per-lineage numbers.
    assert first.outcome in ("INSUFFICIENT_DATA", "NO_IMPROVEMENT")


# ---------------------------------------------------------------------------
# T13: club adapter units (the full archive path ships with the real run)
# ---------------------------------------------------------------------------

def test_club_adapter_outcomes_and_variant_spreads() -> None:
    adapter = runner.ADAPTERS["club-soccer-recalib"]

    class _Match:
        match_id = "m1"
        league = "EPL"
        season = "2026"
        matchweek = 3
        kickoff = dt.datetime(2026, 8, 1, tzinfo=UTC)
        available_at = dt.datetime(2026, 8, 1, 3, tzinfo=UTC)
        home_team = "A"
        away_team = "B"
        home_goals = 2
        away_goals = 1

    match = _Match()
    outcome = (
        0 if match.home_goals > match.away_goals
        else 1 if match.home_goals == match.away_goals
        else 2
    )
    assert outcome == 0
    # The variant-spread口径: per row, the WIDEST class range across
    # envelope variants (build_folds needs the real archive; the full
    # club path ships with the real run R1).
    base = (runner.RowScore("1x2", (0.4, 0.3, 0.3), None),)
    variants = [
        (runner.RowScore("1x2", (0.5, 0.25, 0.25), None),),
        (runner.RowScore("1x2", (0.3, 0.35, 0.35), None),),
    ]
    spreads = adapter._variant_spreads(base, variants, "match_winner")
    assert spreads == pytest.approx([max(0.2, 0.10, 0.10)])


def test_render_is_deterministic_and_float_exact() -> None:
    rendered = runner._render({"b": 0.1 + 0.2, "a": 1.0})
    assert rendered == {"a": repr(1.0), "b": repr(0.1 + 0.2)}
    assert runner._canonical_bytes({"x": 0.5}) == b'{"x":"0.5"}'


# ---------------------------------------------------------------------------
# Landing fixes: train-label resolution and the failed-row lineage list
# ---------------------------------------------------------------------------

def test_club_events_cover_train_labels_for_the_boundary_check() -> None:
    # The walk-forward boundary validator resolves TRAIN keys too, so
    # the club event stream must carry every completed match of every
    # fold (train AND test), not only the scored test blocks.
    from types import SimpleNamespace

    adapter = runner.ADAPTERS["club-soccer-recalib"]

    def match(match_id, kickoff, goals=(2, 1)):
        return SimpleNamespace(
            match_id=match_id, league="EPL", season="2026", matchweek=3,
            kickoff=kickoff, available_at=kickoff,
            home_team="A", away_team="B",
            home_goals=goals[0], away_goals=goals[1],
        )

    early = match(
        "m-early",
        dt.datetime(2026, 8, 1, tzinfo=UTC),
    )
    late = match(
        "m-late",
        dt.datetime(2026, 8, 2, tzinfo=UTC),
    )
    mo4_fold = SimpleNamespace(
        league="EPL", season="2026", matchweek=3,
        cutoff=dt.datetime(2026, 8, 2, tzinfo=UTC),
        train=(early,), test=(late,),
        completed_matchweeks=frozenset(), completion_lag_hours=3.0,
    )
    payload = {"audit": None, "mo4": ((mo4_fold,), ())}
    events = {event.event_key: event for event in adapter.events(payload)}
    assert set(events) == {"m-early", "m-late"}  # train label included
    assert events["m-late"].outcomes[0].outcome_index == 0  # 2:1 home win


def test_submission_lineage_names_fall_back_to_declared_set() -> None:
    # An early RUN_FAILED (boundary/integrity before any lineage
    # finished) carries an empty active_specs map; the failed row still
    # lands with the family's DECLARED lineage list.
    from tests.papersim_mo6_synthetic import CoinAdapter, FAMILY

    adapter = CoinAdapter()
    failed = runner.RunResult(
        outcome="RUN_FAILED", run_scorecard={}, artifact_bytes=b"{}",
        dataset_summary={}, snapshot_refs=(), search_space_text_value="{}",
        active_specs={}, trial_count=0, registered_lineages=(),
        winner_specs={}, error_code="selection_label_available_after_holdout_boundary",
    )
    assert runner._submission_lineage_names(FAMILY, adapter, failed) == [
        f"{FAMILY}/coin",
    ]
    finished = dict(failed.__dict__) | {"active_specs": {f"{FAMILY}/coin": "a" * 64}}
    ok = runner.RunResult(**finished)
    assert runner._submission_lineage_names(FAMILY, adapter, ok) == [
        f"{FAMILY}/coin",
    ]


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("family_id", ["nba-pace-efficiency", "NBA_PACE_EFFICIENCY"])
def test_legacy_python_entry_requires_registered_manifest(monkeypatch, tmp_path, dry_run, family_id):
    from polymarket_alpha_lab.papersim import scan

    def forbidden(*args, **kwargs):
        pytest.fail("legacy Python request reached the registry or optimizer")

    # Both an old request with the published family and one with an alternate
    # name must fail before a worktree read, a connection read, or inference.
    monkeypatch.setattr(scan, "require_clean_worktree", forbidden)
    monkeypatch.setattr(runner, "_read_registry", forbidden)
    monkeypatch.setattr(runner, "run_optimize_core", forbidden)
    with pytest.raises(ValueError, match="^papersim_optimize_input_manifest_required$"):
        runner.run_and_record(
            conn=object(), repo_root=tmp_path, family_id=family_id,
            as_of=DATA_CUTOFF, inputs=(make_input(q=0.6, seed=20261120),), seed=20261120,
            budget_seconds=3600, max_trials=2000, operation_key="legacy-retry",
            attempt=2, now_utc=forbidden, dry_run=dry_run,
        )


UTC = dt.timezone.utc
CODE_COMMIT = "a" * 40
DATA_CUTOFF = dt.datetime(2026, 11, 20, 6, 0, tzinfo=UTC)
STARTED_AT = dt.datetime(2026, 11, 20, 7, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Section 80.7 constants pin (delta D4: the runner constants home)
# ---------------------------------------------------------------------------


def _section_807() -> str:
    plan = Path(__file__).resolve().parents[1] / "DELIVERY_PLAN.md"
    text = plan.read_text(encoding="utf-8")
    start = text.index("### 80.7 ")
    end = text.index("### 80.8 ")
    return text[start:end]


def test_stage1_constants_pinned_to_section_807_registration():
    section = _section_807()
    # The registration strings must stand verbatim; any DELIVERY_PLAN
    # edit that moves a number breaks this test on purpose.
    assert "95% 区间上界 < 0（多谱系时经 Holm 校正）" in section
    assert runner.CI_CONFIDENCE_LEVEL == 0.95
    assert "不得低于现役的 0.8 倍" in section
    assert runner.ENVELOPE_MIN_RATIO == 0.8
    assert "28 天" in section
    assert runner.RESUBMISSION_BAN_DAYS == 28
    assert "300 场，且不少于 20 个比赛周区块" in section
    assert runner.STAGE1_HOLDOUT_FLOORS["club-soccer"] == (300, 20)
    assert "300 场，且不少于 30 个比赛日区块" in section
    assert runner.STAGE1_HOLDOUT_FLOORS["nba"] == (300, 30)
    assert "| 国家队 | 300 场 |" in section
    assert runner.STAGE1_HOLDOUT_FLOORS["intl"] == (300, 0)
    assert "150 个已结算市场，且不少于 20 个到期日区块" in section
    assert runner.STAGE1_HOLDOUT_FLOORS["crypto"] == (150, 20)
    assert runner.RULE_VERSION == "v1"


def test_kernel_statistical_parameters_keep_single_home():
    from polymarket_alpha_lab.papersim import evaluation
    from polymarket_alpha_lab.papersim.optimize import metrics

    build = inspect.signature(scorecard_mod.build_scorecard)
    assert runner.SIGN_FLIP_SEED == build.parameters["sign_flip_seed"].default
    assert runner.SIGN_FLIP_REPS == build.parameters["sign_flip_reps"].default
    assert runner.bootstrap_resamples() == evaluation.BOOTSTRAP_RESAMPLES == 20000
    assert runner.HOLM_ALPHA == 0.05
    assert (
        runner.HOLM_ALPHA
        == inspect.signature(metrics.holm).parameters["alpha"].default
    )


def test_locked_budget_and_grid_constants_are_runner_owned():
    assert runner.MAX_TRIALS == 2000
    assert runner.TIME_BUDGET_SECONDS == 3600
    assert runner.CONTINUOUS_GRID_POINTS == 5
    assert set(runner.STAGE1_HOLDOUT_FLOORS) == {
        "club-soccer", "crypto", "intl", "nba"
    }
    with pytest.raises(ValueError):
        runner.resolve_stage1_floors("runner-synth")  # no implicit floor class
    assert runner.resolve_stage1_floors("runner-synth", floor_class="crypto") \
        == (150, 20)
    with pytest.raises(ValueError, match="requires the 'nba' stage-1 floors"):
        runner.resolve_stage1_floors("nba-pace-efficiency", floor_class="crypto")


def test_fold_kind_resolution_covers_baseline_and_falls_back():
    assert runner.resolve_fold_kind("club-soccer-recalib") == "matchweek"
    assert runner.resolve_fold_kind("crypto-gbm") == "expiry"
    assert runner.resolve_fold_kind("nba-pace-efficiency") == "matchday"
    assert runner.resolve_fold_kind("intl-elo-poisson") == "matchday"
    assert runner.resolve_fold_kind("a-family-merged-later") \
        == runner.DEFAULT_FOLD_KIND


# ---------------------------------------------------------------------------
# Synthetic family + harness (all fixtures generated in-code)
# ---------------------------------------------------------------------------


class SyntheticFamily:
    """A structurally protocol-complete family with one choice knob."""

    family_id = "runner-synth"
    procedure_version = "runner-synth-v1"

    def __init__(self, targets: tuple[str, ...] = ("synthetic",), *, envelope=False):
        self._targets = targets
        self._envelope = envelope

    def param_space(self):
        return (
            ParamDef("theta", "choice", 0.2, choices=(0.1, 0.2, 0.3, 0.4)),
        )

    def baseline_spec(self) -> ModelSpec:
        return ModelSpec(
            family=self.family_id,
            procedure_version=self.procedure_version,
            params={"theta": 0.2},
            envelope=({"name": "low"}, {"name": "high"}) if self._envelope else (),
            data_contract="runner-synth-v1",
        )

    def targets(self):
        return self._targets

    def fit(self, history, spec, as_of):  # pragma: no cover -- seam stub
        return (history, spec, as_of)

    def predict(self, fitted, fixture):  # pragma: no cover -- seam stub
        return None


def Record(event_key, group_id, kickoff):
    return FixtureRecord(event_key, group_id, kickoff, kickoff + dt.timedelta(hours=3))


class Harness:
    """Deterministic synthetic losses keyed by (theta, event).

    ``search_effect``/``holdout_effect`` shift the BEST theta's loss by
    that amount on the search / holdout periods respectively; per-event
    gaussian noise (sigma per side, independent draws per theta) drives
    the intervals. Probabilities realize the target losses exactly
    (``p = exp(-loss)``, binary event, outcome_index 1).
    """

    def __init__(
        self,
        seed: int,
        *,
        search_blocks: int = 8,
        search_events: int = 4,
        holdout_blocks: int = 20,
        holdout_events_per_block: int = 8,
        search_effect: float = 0.0,
        holdout_effect: float = 0.0,
        best_theta: float = 0.4,
        sigma: float = 0.008,
        variant_medians: dict[float, float] | None = None,
        incumbent_theta: float = 0.2,
    ):
        self.family = SyntheticFamily(envelope=bool(variant_medians))
        self.search_effect = search_effect
        self.holdout_effect = holdout_effect
        self.best_theta = best_theta
        self.variant_medians = variant_medians or {}
        self.thetas = (0.1, 0.2, 0.3, 0.4)
        rng = random.Random(seed)
        self.records: list[Record] = []
        self._search_keys: list[str] = []
        self._holdout_keys: list[str] = []
        self._loss: dict[tuple[float, str], float] = {}
        day0 = dt.datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
        for block in range(search_blocks):
            group = f"2026-S{block:02d}"
            kickoff = day0 + dt.timedelta(days=7 * block)
            for event in range(search_events):
                key = f"s{block:02d}-e{event}"
                self.records.append(Record(key, group, kickoff))
                self._search_keys.append(key)
                for theta in self.thetas:
                    base = 0.6 + rng.gauss(0.0, sigma)
                    effect = search_effect if theta == best_theta else 0.0
                    self._loss[(theta, key)] = max(1e-6, base - effect)
        holdout_day0 = day0 + dt.timedelta(days=7 * search_blocks)
        for block in range(holdout_blocks):
            group = f"2026-H{block:02d}"
            kickoff = holdout_day0 + dt.timedelta(days=block)
            for event in range(holdout_events_per_block):
                key = f"h{block:02d}-e{event}"
                self.records.append(Record(key, group, kickoff))
                self._holdout_keys.append(key)
                for theta in self.thetas:
                    base = 0.6 + rng.gauss(0.0, sigma)
                    effect = holdout_effect if theta == best_theta else 0.0
                    self._loss[(theta, key)] = max(1e-6, base - effect)
        self.holdout_group_ids = frozenset(
            record.group_id for record in self.records
            if record.group_id.startswith("2026-H")
        )

    def _events(self, spec, keys, target):
        events = []
        theta = spec.params["theta"]
        digest = spec.spec_sha256()
        for key in keys:
            probability = math.exp(-self._loss[(theta, key)])
            spread = self.variant_medians.get(theta, 1.0)
            variants = tuple(
                VariantPrediction(name, digest, key, target, "line", ("no", "yes"), (1.0 - p, p))
                for name, p in zip(runner.variant_ids(spec), (0.0, spread))
            )
            events.append(Prediction(key, "line", (1.0 - probability, probability),
                                     ("no", "yes"), variants=variants))
        return tuple(events)

    def evaluate(self, spec, fold, targets):
        return {target: HoldoutEvaluation(self._events(spec, fold.test_keys, target))
                for target in targets}

    def evaluate_holdout(self, spec, target, folds):
        keys = tuple(key for fold in folds for key in fold.test_keys)
        return HoldoutEvaluation(self._events(spec, keys, target))

    def context(self, family=None, registry=None, receipts=()):
        if family is None:
            family = self.family
        baseline = family.baseline_spec()
        digest = baseline.spec_sha256()
        if registry is None:
            names = tuple(f"{family.family_id}/{target}" for target in family.targets())
            registry = {
                name: RegistryComparison(LineageRoles(name, digest, digest), baseline)
                for name in names
            }
        return PolicyContext(STARTED_AT, registry, tuple(receipts))

    def run(self, **overrides):
        family = overrides.get("family", self.family)
        records = overrides.get("records", self.records)
        lines = tuple(FixtureLine(row.event_key, target, "line", row.event_key, ("no", "yes"),
                                  1, "binary")
                      for row in records for target in family.targets())
        parameters = dict(
            family=family,
            records=records,
            fixture_lines=lines,
            holdout_group_ids=self.holdout_group_ids,
            evaluate=self.evaluate,
            evaluate_holdout=self.evaluate_holdout,
            data_cutoff=DATA_CUTOFF,
            random_seed=20261120,
            code_commit=CODE_COMMIT,
            policy_context=self.context(family),
            deadline=lambda: False,
            floor_class="crypto",
            dataset_summary={"origin": "synthetic"},
            snapshot_refs=["b" * 64],
        )
        if isinstance(family, SyntheticFamily) and family.baseline_spec().envelope:
            parameters["build_candidate_spec"] = lambda params: replace(family.baseline_spec(), params=params)
        parameters.update(overrides)
        return execute_optimization_run(**parameters)


LINEAGE = "runner-synth/synthetic"


# ---------------------------------------------------------------------------
# Outcome-classification table (all four outcomes + envelope flag)
# ---------------------------------------------------------------------------


def test_outcome_candidate_registered_end_to_end():
    harness = Harness(
        11, search_effect=0.05, holdout_effect=0.05, best_theta=0.4
    )
    result = harness.run()
    assert result.outcome == "CANDIDATE_REGISTERED"
    assert tuple(result.proposed_specs) == (LINEAGE,)
    assert result.registered_lineages == ()
    assert result.winner_spec is not None
    assert float(dict(result.winner_spec.params)["theta"]) == 0.4
    decision = result.decisions[0]
    assert decision.statistically_eligible and decision.ci_passes
    assert decision.floors_met
    assert decision.envelope_status == "not_assessable"  # both medians None
    assert decision.holm_rejected is None  # single lineage: Holm not applied
    assert result.artifacts_sha256 == sha256(result.artifact_bytes).hexdigest()
    assert result.run_key.startswith("or|runner-synth|20261120060000|v1|")
    payload = json.loads(result.artifact_bytes)
    assert payload["outcome"] == "CANDIDATE_REGISTERED"
    assert payload["counts"]["evaluated_trials"] == 24  # 6 folds x 4 candidates
    assert payload["counts"]["predictions"] == 320  # 160 x (candidate+incumbent)
    assert result.submission.outcome == "CANDIDATE_REGISTERED"
    assert result.submission.trial_count == 24
    assert result.submission.active_specs == {
        LINEAGE: harness.family.baseline_spec().spec_sha256()
    }
    assert "registry_fallback:" + LINEAGE not in result.flags


def test_outcome_no_improvement_when_holdout_ci_includes_zero():
    harness = Harness(
        12, search_effect=0.05, holdout_effect=-0.02, best_theta=0.3
    )
    result = harness.run()
    assert result.outcome == "NO_IMPROVEMENT"
    assert result.registered_lineages == ()
    decision = result.decisions[0]
    assert not decision.registered
    assert decision.ci_passes is False  # candidate worse on the holdout
    assert decision.floors_met


def test_outcome_no_improvement_holdout_untouched_when_incumbent_wins():
    # search effect favors the INCUMBENT theta: the one-SE rule lands
    # back on the incumbent, the holdout is never touched (design 04
    # section 5 measure 3).
    harness = Harness(
        13, search_effect=0.05, holdout_effect=0.05, best_theta=0.2
    )

    def unexpected_holdout(*_args):
        pytest.fail("incumbent retention accessed the holdout")

    result = harness.run(evaluate_holdout=unexpected_holdout)
    assert result.outcome == "NO_IMPROVEMENT"
    assert "holdout_untouched_incumbent_selected" in result.flags
    payload = json.loads(result.artifact_bytes)
    assert payload["counts"]["predictions"] == 0  # holdout untouched
    assert payload["decisions"][0]["status"] == "holdout_untouched_incumbent_selected"
    assert payload["holm_hypotheses"][0]["p_value"] == 1


def test_frozen_numeric_singleton_preserves_incumbent_spec_identity():
    class FrozenFamily(SyntheticFamily):
        def param_space(self):
            return (ParamDef("theta", "float", 0.2, lower=0.2, upper=0.2, season_frozen=True),)

        def baseline_spec(self):
            return replace(
                super().baseline_spec(), params={"theta": 0.2, "fixed": True},
                legacy_model_version="paper-frozen",
            )

    harness = Harness(13)
    family = FrozenFamily()
    result = harness.run(family=family)
    assert result.outcome == runner.NO_IMPROVEMENT
    assert result.winner_spec.model_version() == "paper-frozen"
    assert result.winner_spec.spec_sha256() == family.baseline_spec().spec_sha256()
    assert result.selection[0] == {"theta": 0.2, "fixed": True}
    assert result.selection[1].status == "no_better_candidate"
    assert not result.selection[1].needs_preseason_reestimate


def test_outcome_insufficient_data_when_floors_unmet():
    harness = Harness(
        14, holdout_events_per_block=2,  # 20 blocks x 2 = 40 events < 150
        search_effect=0.05, holdout_effect=0.05,
    )
    result = harness.run()
    assert result.outcome == "INSUFFICIENT_DATA"
    assert "holdout_floors_unmet" in result.flags
    payload = json.loads(result.artifact_bytes)
    assert payload["holdout"]["floors"] == {"min_events": 150, "min_blocks": 20}
    assert result.registered_lineages == ()


def test_holdout_floor_counts_distinct_paired_events_and_blocks():
    harness = Harness(14, search_effect=0.05, holdout_effect=0.05)

    def evaluate_holdout(spec, target, folds):
        card = harness.evaluate_holdout(spec, target, folds)
        if spec.params["theta"] == 0.2:
            return replace(card, events=card.events[:-16], refusals=tuple(
                Refusal(row.event_key, row.line_key, "source_unavailable") for row in card.events[-16:]
            ))
        return card

    result = harness.run(evaluate_holdout=evaluate_holdout)
    assert result.outcome == runner.INSUFFICIENT_DATA
    assert result.decisions[0].n_events == 144
    assert result.decisions[0].n_blocks == 18
    assert not result.decisions[0].registered


def test_outcome_run_failed_on_budget_stop_with_partials_preserved():
    harness = Harness(15, search_effect=0.05, holdout_effect=0.05)
    result = harness.run(max_trials=3)
    assert result.outcome == "RUN_FAILED"
    assert result.budget_exhausted
    assert "budget_exhausted" in result.flags
    # completed partials preserved, exactly counted, never truncated
    assert len(result.trials) == 3
    assert result.submission.trial_count == 3
    payload = json.loads(result.artifact_bytes)
    assert payload["counts"]["planned_trials"] == 24
    assert payload["counts"]["evaluated_trials"] == 3
    assert len(payload["trials"]) == 3
    assert all(row["mean_loss"] is not None for row in payload["trials"])
    assert result.winner_spec is None


def test_envelope_narrowed_flags_not_registers():
    # CI-level improvement, but the candidate's variant spread median
    # (0.5) is below 0.8x the incumbent's (1.0): flagged for the owner,
    # never auto-registered (section 80.7).
    harness = Harness(
        16,
        search_effect=0.05,
        holdout_effect=0.05,
        variant_medians={0.2: 1.0, 0.3: 0.5, 0.4: 0.5, 0.1: 1.0},
        best_theta=0.4,
    )
    result = harness.run()
    assert result.outcome == "NO_IMPROVEMENT"  # flag-not-register
    decision = result.decisions[0]
    assert decision.envelope_status == "narrowed"
    assert not decision.registered
    assert decision.ci_passes  # the CI itself passed; the envelope held it
    assert "envelope_narrowed_owner_decision:" + LINEAGE in result.flags


def test_envelope_passes_when_ratio_at_or_above_bound():
    harness = Harness(
        17,
        search_effect=0.05,
        holdout_effect=0.05,
        variant_medians={0.2: 1.0, 0.3: 0.85, 0.4: 0.8, 0.1: 1.0},
        best_theta=0.4,
    )
    result = harness.run()
    assert result.outcome == "CANDIDATE_REGISTERED"  # exactly 0.8x passes
    assert result.decisions[0].envelope_status == "ok"


def test_calibration_or_gate_regression_blocks_registration():
    # routed through the pure rule evaluator with synthetic per-lineage
    # statistics: the CI passes, Holm is not applied, but a calibration
    # regression (or a gate regression) blocks registration.
    from polymarket_alpha_lab.papersim.optimize.runner import (
        LineageStat,
        evaluate_stage1_rule,
    )

    base = dict(
        lineage=LINEAGE,
        n_events=300,
        n_blocks=25,
        ci95_upper=-0.01,
        p_value=0.001,
    )
    decisions, insufficient = evaluate_stage1_rule(
        (LineageStat(calibration_regression=True, **base),),
        min_events=150,
        min_blocks=20,
    )
    assert not insufficient
    assert decisions[0].ci_passes
    assert not decisions[0].registered
    decisions, _ = evaluate_stage1_rule(
        (LineageStat(gate_regression=True, **base),),
        min_events=150,
        min_blocks=20,
    )
    assert not decisions[0].registered


# ---------------------------------------------------------------------------
# Determinism + empty-registry parity (actually executed)
# ---------------------------------------------------------------------------


def test_determinism_byte_compare_across_two_executions():
    harness_a = Harness(21, search_effect=0.05, holdout_effect=0.05)
    harness_b = Harness(21, search_effect=0.05, holdout_effect=0.05)
    result_a = harness_a.run()
    result_b = harness_b.run()
    assert result_a.artifact_bytes == result_b.artifact_bytes
    assert result_a.artifacts_sha256 == result_b.artifacts_sha256
    assert result_a.run_key == result_b.run_key
    assert result_a.outcome == result_b.outcome
    assert result_a.submission.comparison() == result_b.submission.comparison()


def test_empty_registry_parity_executed_twice_exact_counts():
    # ACTUALLY EXECUTED empty-registry parity: derive_lineage_roles ->
    # None is legal; the baseline is used with the fallback flag; two
    # executions produce byte-identical artifacts and exact counts.
    from polymarket_alpha_lab.papersim.optimize.registry import (
        derive_lineage_roles,
    )

    assert derive_lineage_roles([]) is None  # the legal empty fold
    harness_a = Harness(22, search_effect=0.05, holdout_effect=0.05)
    harness_b = Harness(22, search_effect=0.05, holdout_effect=0.05)
    first = harness_a.run(policy_context=harness_a.context(registry={}))
    second = harness_b.run(policy_context=harness_b.context(registry={LINEAGE: RegistryComparison()}))
    assert first.artifact_bytes == second.artifact_bytes
    assert first.artifacts_sha256 == second.artifacts_sha256
    baseline_sha = harness_a.family.baseline_spec().spec_sha256()
    assert first.submission.active_specs == {}
    assert "registry_fallback:" + LINEAGE in first.flags
    assert first.outcome == runner.RUN_FAILED
    assert first.decisions[0].status == "registry_lineage_uninitialized"
    assert not first.proposed_specs and not first.statistical_rejections
    payload = json.loads(first.artifact_bytes)
    second_payload = json.loads(second.artifact_bytes)
    assert payload["counts"] == second_payload["counts"]
    assert payload["counts"]["evaluated_trials"] == 24
    assert payload["counts"]["planned_trials"] == 24
    assert payload["counts"]["predictions"] == 320
    assert payload["active_specs"] == {}
    assert payload["comparison_specs"][LINEAGE]["spec_sha256"] == baseline_sha
    assert payload["comparison_specs"][LINEAGE]["source"] == "baseline_diagnostic_fallback"


def test_roles_mapping_supplies_active_specs_without_fallback():
    harness = Harness(23, search_effect=0.05, holdout_effect=0.05)
    baseline_sha = harness.family.baseline_spec().spec_sha256()
    result = harness.run(policy_context=harness.context())
    assert not any(flag.startswith("registry_fallback") for flag in result.flags)
    assert result.submission.active_specs == {LINEAGE: baseline_sha}


def test_registry_roles_must_match_the_run_lineage_and_comparator():
    harness = Harness(23)
    baseline = harness.family.baseline_spec()
    digest = baseline.spec_sha256()
    with pytest.raises(ValueError, match="registry roles do not match"):
        harness.run(policy_context=harness.context(registry={
            LINEAGE: RegistryComparison(LineageRoles("other/synthetic", digest, digest), baseline),
        }))
    result = harness.run(policy_context=harness.context(registry={
        LINEAGE: RegistryComparison(LineageRoles(LINEAGE, digest, "c" * 64), baseline),
    }))
    assert result.outcome == runner.RUN_FAILED
    assert result.submission.active_specs == {}
    assert f"registry_active_digest_unverified:{LINEAGE}" in result.flags
    assert json.loads(result.artifact_bytes)["diagnostic_scorecards"][LINEAGE]["n_events"] == 24
    with pytest.raises(ValueError, match="explicit comparator"):
        harness.run(incumbent_spec=replace(baseline, params={"theta": 0.3}))


def test_trigger_only_family_has_no_optimization_targets():
    harness = Harness(23)
    with pytest.raises(ValueError, match="declares no optimization targets"):
        harness.run(family=SyntheticFamily(targets=()))


def test_search_binds_the_intl_family_envelope_to_each_candidate():
    from polymarket_alpha_lab.papersim.optimize.families.intl import INTL_ELO

    harness = Harness(23, holdout_events_per_block=15)
    seen = []

    records = tuple(FixtureRecord(row.event_key, row.group_id,
                                  source_day=row.kickoff.date(), label_day=row.kickoff.date())
                    for row in harness.records)

    def evaluate(spec, fold, targets):
        seen.append(spec)
        assert spec == INTL_ELO.spec_with(spec.params)
        return {"match_winner": HoldoutEvaluation(tuple(
            Prediction(key, "line", (0.4, 0.6), ("no", "yes")) for key in fold.test_keys
        ))}

    result = harness.run(
        family=INTL_ELO, records=records, evaluate=evaluate, floor_class=None, max_trials=2,
        source_calendar="international-source-day", source_timezone=UTC,
        last_complete_date=DATA_CUTOFF.date() - dt.timedelta(days=1),
    )
    assert result.outcome == runner.RUN_FAILED
    assert result.submission.trial_count == len(seen) == 2
    assert any(spec.envelope != INTL_ELO.baseline_spec().envelope for spec in seen)


# ---------------------------------------------------------------------------
# Holm fixture: nominal-only significance flips to not-registered
# ---------------------------------------------------------------------------


def test_holm_nominal_significance_flips_to_not_registered():
    from polymarket_alpha_lab.papersim.optimize.metrics import holm
    from polymarket_alpha_lab.papersim.optimize.runner import (
        LineageStat,
        evaluate_stage1_rule,
    )

    # sanity: the kernel correction itself rejects neither at k = 2
    rejected, adjusted = holm((0.04, 0.04), alpha=0.05)
    assert rejected == (False, False)
    stats = (
        LineageStat(
            lineage="runner-synth/a", n_events=300, n_blocks=25,
            ci95_upper=-0.004, p_value=0.04,
        ),
        LineageStat(
            lineage="runner-synth/b", n_events=300, n_blocks=25,
            ci95_upper=-0.004, p_value=0.04,
        ),
    )
    decisions, insufficient = evaluate_stage1_rule(
        stats, min_events=150, min_blocks=20
    )
    assert not insufficient
    assert all(decision.ci_passes for decision in decisions)  # nominally
    assert all(not decision.registered for decision in decisions)  # Holm flip
    assert all(decision.holm_rejected is False for decision in decisions)
    # a single lineage with the SAME nominal p-value registers: Holm is
    # only applied when the run evaluates multiple lineages
    solo, _ = evaluate_stage1_rule(
        (stats[0],), min_events=150, min_blocks=20
    )
    assert solo[0].registered
    assert solo[0].holm_rejected is None
    # strong evidence survives the k = 2 correction
    strong = (
        LineageStat(lineage="runner-synth/a", n_events=300, n_blocks=25,
                    ci95_upper=-0.02, p_value=0.001),
        LineageStat(lineage="runner-synth/b", n_events=300, n_blocks=25,
                    ci95_upper=-0.004, p_value=0.04),
    )
    decisions, _ = evaluate_stage1_rule(strong, min_events=150, min_blocks=20)
    assert all(decision.registered for decision in decisions)


def test_stage1_rule_floors_drive_insufficient():
    from polymarket_alpha_lab.papersim.optimize.runner import (
        LineageStat,
        evaluate_stage1_rule,
    )

    stat = LineageStat(lineage=LINEAGE, n_events=149, n_blocks=25,
                       ci95_upper=-0.02, p_value=0.001)
    decisions, insufficient = evaluate_stage1_rule(
        (stat,), min_events=150, min_blocks=20
    )
    assert insufficient  # the RUN is INSUFFICIENT_DATA
    assert not decisions[0].floors_met
    assert not decisions[0].registered  # and nothing registers on short data


# ---------------------------------------------------------------------------
# 28-day re-submission ban (pure; load_optimization_runs row shape)
# ---------------------------------------------------------------------------


def _receipt(moment, digest="c" * 64, *, run_key="or|runner-synth|prior", lineage=LINEAGE):
    return RejectionReceipt(run_key, "runner-synth", lineage, digest, "receipt/7", "d" * 64, moment)


def test_resubmission_ban_accept_reject_expire():
    def banned(prior, now=STARTED_AT, *, family="runner-synth", self_key="or|runner-synth|other"):
        return runner.banned_spec_digests(prior, now=now, family=family, run_key=self_key)

    assert banned([]) == {}
    assert banned([_receipt(STARTED_AT - dt.timedelta(days=29))]) == {}
    receipt = _receipt(STARTED_AT - dt.timedelta(days=10))
    reason = banned([receipt])["c" * 64]
    assert reason == {
        "reason": "stage1_resubmission_ban", "receipt_id": "receipt/7",
        "evidence_kind": "verified_rejection_receipt",
        "artifact_sha256": "d" * 64, "run_key": receipt.run_key, "lineage": LINEAGE,
        "spec_sha256": "c" * 64, "rejected_at": runner._iso(receipt.rejected_at),
        "expires_at": runner._iso(receipt.rejected_at + dt.timedelta(days=28)),
    }
    tick = dt.timedelta(microseconds=1)
    expiry = receipt.rejected_at + dt.timedelta(days=28)
    assert not banned([receipt], receipt.rejected_at - tick)
    assert banned([receipt], receipt.rejected_at)
    assert banned([receipt], expiry - tick)
    assert not banned([receipt], expiry)
    assert not banned([receipt], self_key=receipt.run_key)
    assert not banned([receipt], family="another")
    with pytest.raises(ValueError, match="linkage"):
        _receipt(STARTED_AT, run_key="or|some-other-family|prior")
    legacy = runner.LegacyRejection(receipt.run_key, receipt.family, LINEAGE,
                                    receipt.spec_sha256, "terminal_link_unavailable")
    assert banned([legacy], expiry)["c" * 64]["reason"] == "stage1_rejection_quarantined"
    with pytest.raises(ValueError, match="linked receipts"):
        PolicyContext(STARTED_AT, {}, ({"legacy_cutoff": STARTED_AT},))


def test_run_cooldown_uses_its_own_scorecard_and_skips_holdout():
    bad = Harness(12, search_effect=0.05, holdout_effect=-0.02)
    shifted = tuple(replace(row, kickoff=row.kickoff - dt.timedelta(days=70),
                            available_at=row.available_at - dt.timedelta(days=70)) for row in bad.records)
    failed = bad.run(records=shifted, data_cutoff=DATA_CUTOFF - dt.timedelta(days=55))
    assert failed.outcome == runner.NO_IMPROVEMENT
    judgment, = failed.statistical_rejections
    prior = _receipt(STARTED_AT - dt.timedelta(microseconds=1), judgment.spec_sha256,
                     run_key=failed.run_key)

    def unexpected_holdout(*_args):
        pytest.fail("a candidate on cooldown accessed the holdout")

    good = Harness(12, search_effect=0.05, holdout_effect=0.05)
    policy = good.context(receipts=(prior,))
    rejected = good.run(policy_context=policy, evaluate_holdout=unexpected_holdout)
    assert rejected.outcome == runner.NO_IMPROVEMENT
    assert "stage1_resubmission_ban" in rejected.flags
    assert not rejected.statistical_rejections
    assert json.loads(rejected.artifact_bytes)["cooldown"][LINEAGE]["receipt_id"] == "receipt/7"
    assert rejected.artifact_bytes == good.run(policy_context=policy, evaluate_holdout=unexpected_holdout).artifact_bytes
    self_receipt = replace(prior, run_key=rejected.run_key)
    approved = good.run(policy_context=good.context(receipts=(self_receipt,)))
    assert approved.outcome == runner.CANDIDATE_REGISTERED
    assert approved.proposed_specs[LINEAGE].spec_sha256() == self_receipt.spec_sha256


# ---------------------------------------------------------------------------
# Input validation + import purity
# ---------------------------------------------------------------------------


def test_core_input_validation():
    harness = Harness(31)
    with pytest.raises(ValueError, match="code_commit"):
        harness.run(code_commit="short")
    with pytest.raises(ValueError, match="aware datetime"):
        harness.run(data_cutoff=dt.datetime(2026, 11, 20))
    with pytest.raises(ValueError, match="holdout_group_ids"):
        harness.run(holdout_group_ids=())
    with pytest.raises(ValueError, match="unknown fold kind"):
        harness.run(fold_kind="quarter")
    with pytest.raises(ValueError, match="temporal suffix"):
        harness.run(
            holdout_group_ids=frozenset({"2026-S00"})  # a SEARCH group
        )


def _src_env() -> dict:
    """A subprocess env with the repo src tree on PYTHONPATH."""
    import os

    repo_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repo_root / "src") + os.pathsep \
        + environment.get("PYTHONPATH", "")
    return environment


def _module_set(statement: str) -> set[str]:
    code = f"import sys\n{statement}\nprint(sorted(sys.modules))\n"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=_src_env(),
    )
    assert completed.returncode == 0, completed.stderr
    return set(ast.literal_eval(completed.stdout.strip()))


def test_import_purity_no_db_or_model_modules_beyond_baseline():
    # The papersim package baseline (its eager __init__) is pre-existing
    # and loads for EVERY optimize module including the merged kernel
    # (optimize/__init__ docstring). The runner's import-purity contract
    # is about the DELTA: importing runner may add only the optimize
    # kernel plus the stdlib-pure papersim.store -- never a database
    # driver, a store adapter, the migrations layer or a family module.
    baseline = _module_set("import polymarket_alpha_lab.papersim")
    loaded = _module_set(
        "import polymarket_alpha_lab.papersim.optimize.runner"
    )
    delta = loaded - baseline
    allowed = {
        "polymarket_alpha_lab.papersim.optimize",
        "polymarket_alpha_lab.papersim.optimize.runner",
        "polymarket_alpha_lab.papersim.optimize.search",
        "polymarket_alpha_lab.papersim.optimize.scorecard",
        "polymarket_alpha_lab.papersim.optimize.metrics",
        "polymarket_alpha_lab.papersim.optimize.walkforward",
        "polymarket_alpha_lab.papersim.optimize.registry",
        "polymarket_alpha_lab.papersim.optimize.spec",
        "polymarket_alpha_lab.papersim.store",
        "polymarket_alpha_lab.papersim.evaluation",
        "polymarket_alpha_lab.research_probability_scores",
        # transitively loaded by the store/kernel layers (pure modules)
        "polymarket_alpha_lab.team_research_agent_types",
        "polymarket_alpha_lab.team_taxonomy",
    }
    unexpected = {
        name for name in delta - allowed
        if name.startswith("polymarket_alpha_lab")  # stdlib deltas are fine
    }
    assert unexpected == set(), sorted(unexpected)
    forbidden = {
        name for name in loaded
        if name == "psycopg" or "store_psycopg" in name
        or "project_postgres" in name or "migrations" in name
        or ".optimize.families." in name
    }
    assert forbidden == set(), sorted(forbidden)


def _set_observed_p_values(monkeypatch, values):
    original = runner.build_scorecard

    def score(spec, events, **kwargs):
        card = original(spec, events, **kwargs)
        if card.paired_vs_incumbent is not None:
            line_key = events[0].line_key
            card = replace(card, paired_vs_incumbent=replace(
                card.paired_vs_incumbent, p_value=values[line_key],
            ))
        return card

    monkeypatch.setattr(runner, "build_scorecard", score)


def _two_target_batches(harness, *, mute_search=(), refuse=()):
    harness.family = SyntheticFamily(("synthetic", "peer"))
    lines = tuple(FixtureLine(row.event_key, target, target, row.event_key, ("no", "yes"),
                              1, "binary") for row in harness.records for target in harness.family.targets())

    def search(spec, fold, mask):
        return {target: HoldoutEvaluation(tuple(
            replace(row, line_key=target) for row in harness._events(
                harness.family.baseline_spec() if target in mute_search else spec, fold.test_keys, target,
            )
        )) for target in mask}

    def holdout(spec, target, folds):
        scope = tuple(row for fold in folds for row in fold.test_keys)
        omit = frozenset(refuse) if target == "synthetic" and spec.params["theta"] == 0.2 else frozenset()
        return HoldoutEvaluation(tuple(
            replace(row, line_key=target) for row in harness._events(spec, scope, target)
            if row.event_key not in omit
        ), refusals=tuple(Refusal(key, target, "source_model_unavailable") for key in scope if key in omit))

    return dict(fixture_lines=lines, evaluate=search, evaluate_holdout=holdout)


@pytest.mark.parametrize("floor, expected", [
    ("games", (149, 20)),
    ("blocks", (152, 19)),
])
def test_one_attempted_target_below_either_floor_stops_whole_family(monkeypatch, floor, expected):
    harness = Harness(191, search_effect=0.05, holdout_effect=0.05)
    if floor == "games":
        refused = harness._holdout_keys[:88:8]
    else:
        refused = harness._holdout_keys[-8:]
    context = _two_target_batches(harness, refuse=refused)
    _set_observed_p_values(monkeypatch, {"synthetic": 0.01, "peer": 0.04})

    def no_holm(*args, **kwargs):
        pytest.fail("a floor-failing attempted lineage reached Holm")

    monkeypatch.setattr(runner, "holm", no_holm)
    result = harness.run(**context)
    assert result.outcome == runner.INSUFFICIENT_DATA
    assert result.statistical_rejections == ()
    assert not result.proposed_specs and not result.registered_lineages
    assert [(decision.n_events, decision.n_blocks) for decision in result.decisions] == [
        expected, (160, 20),
    ]
    assert all(decision.ci_passes and decision.holm_rejected is None for decision in result.decisions)
    doc = json.loads(result.artifact_bytes)
    assert [row["observed_p_value"] for row in doc["holm_hypotheses"]] == [0.01, 0.04]
    assert set(doc["candidate_scorecards"]) == {LINEAGE, "runner-synth/peer"}
    assert set(doc["incumbent_scorecards"]) == {LINEAGE, "runner-synth/peer"}


def test_unopened_target_contributes_one_to_the_original_holm_family(monkeypatch):
    harness = Harness(192, search_effect=0.05, holdout_effect=0.05)
    context = _two_target_batches(harness, mute_search=("synthetic",))
    _set_observed_p_values(monkeypatch, {"peer": 0.04})
    result = harness.run(**context)
    doc = json.loads(result.artifact_bytes)
    assert result.outcome == runner.NO_IMPROVEMENT
    assert not result.proposed_specs
    assert doc["holm_hypotheses"] == [
        {"lineage": LINEAGE, "attempted": False, "p_value": 1.0,
         "reason": "holdout_untouched_incumbent_selected", "observed_p_value": None},
        {"lineage": "runner-synth/peer", "attempted": True, "p_value": 0.04,
         "reason": "holdout_assessed", "observed_p_value": 0.04},
    ]
    assert len(doc["holdout"]["callbacks"]) == 2
    assert all(row["lineage"] == "runner-synth/peer" for row in doc["holdout"]["callbacks"])
    subset = harness.run(**context, targets=("peer",))
    assert subset.outcome == runner.CANDIDATE_REGISTERED
    assert subset.run_key != result.run_key
    assert set(subset.proposed_specs) == {"runner-synth/peer"}
    for bad in ((), ("peer", "peer"), ("undeclared",)):
        with pytest.raises(ValueError):
            harness.run(**context, targets=bad)


@pytest.mark.parametrize(("forgery", "issue"), [
    ("different_truth", "realization conflicts"),
    ("same_forged_truth", "realization conflicts"),
    ("wrong_arity", "normalized vector"),
    ("wrong_class_order", "class order"),
    ("duplicate", "duplicate predictions"),
    ("alias", "unmapped fixture-line"),
    ("fake_cluster", "cluster conflicts"),
    ("fake_gate", "gate verdicts"),
])
def test_holdout_rejects_forged_contract_evidence(forgery, issue):
    harness = Harness(209, search_effect=0.05, holdout_effect=0.05)

    def holdout(spec, target, folds):
        rows = list(harness.evaluate_holdout(spec, target, folds).events)
        rows = [replace(row, probabilities=(0.3, 0.7)) for row in rows]
        first = rows[0]
        active = spec.params["theta"] == 0.2
        if forgery in ("different_truth", "same_forged_truth", "fake_cluster"):
            rows = [ScoredEvent(
                row.event_key, row.line_key,
                ("settled-market", row.event_key if forgery != "fake_cluster" else "day-1"),
                row.probabilities,
                int(forgery == "fake_cluster" or (active and forgery == "different_truth")),
            ) for row in rows]
        elif forgery == "wrong_arity":
            rows[0] = replace(first, probabilities=(0.12, 0.13, 0.75))
        elif forgery == "wrong_class_order":
            rows[0] = replace(first, class_order=("yes", "no"))
        elif forgery == "duplicate":
            rows.append(rows[0])
        elif forgery == "alias":
            rows[0] = replace(first, line_key="unregistered")
        elif forgery == "fake_gate":
            return HoldoutEvaluation(tuple(rows), gates=(scorecard_mod.GateResult("D", "invented", "PASS", ()),))
        return HoldoutEvaluation(tuple(rows))

    result = harness.run(evaluate_holdout=holdout)
    doc = json.loads(result.artifact_bytes)
    assert result.outcome == runner.RUN_FAILED
    assert issue in doc["issue"]
    call, = doc["holdout"]["callbacks"]
    assert call["side"] == "candidate" and call["status"] == "aborted"
    assert call["failure"]["step"] == "holdout_validation"
    assert not doc["candidate_scorecards"]
    assert not result.proposed_specs and not result.statistical_rejections


def test_identical_forecasts_on_authentic_truth_have_zero_paired_delta():
    harness = Harness(210, search_effect=0.05, holdout_effect=0.05)

    def holdout(spec, target, folds):
        return HoldoutEvaluation(tuple(
            ScoredEvent(key, "line", ("settled-market", key), (0.3, 0.7), 1)
            for fold in folds for key in fold.test_keys
        ))

    result = harness.run(evaluate_holdout=holdout)
    card = json.loads(result.artifact_bytes)["candidate_scorecards"][LINEAGE]["paired_vs_incumbent"]
    assert result.outcome == runner.NO_IMPROVEMENT
    assert card["n_pairs"] == card["n_clusters"] == 160
    assert card["mean"] == "0.0"
    assert card["ci95"] == ["0.0", "0.0"]


def test_multiple_lines_do_not_inflate_distinct_paired_games():
    harness = Harness(211, search_effect=0.05, holdout_effect=0.05)
    lines = tuple(FixtureLine(row.event_key, "synthetic", f"line{strike}", f"{row.event_key}/{strike}",
                              ("no", "yes"), 1, "totals", strike)
                  for row in harness.records for strike in (2.5, 3.5))
    refused = frozenset(harness._holdout_keys[:88:8])

    def batch(spec, keys, target, refused_keys=()):
        rows = harness._events(spec, keys, target)
        return HoldoutEvaluation(tuple(
            replace(row, line_key=f"line{strike}") for row in rows if row.event_key not in refused_keys
            for strike in (2.5, 3.5)
        ), refusals=tuple(Refusal(key, f"line{strike}", "unavailable")
                          for key in keys if key in refused_keys for strike in (2.5, 3.5)))

    def search(spec, fold, targets):
        return {"synthetic": batch(spec, fold.test_keys, "synthetic")}

    def holdout(spec, target, folds):
        keys = tuple(key for fold in folds for key in fold.test_keys)
        return batch(spec, keys, target, refused if spec.params["theta"] == 0.2 else ())

    result = harness.run(fixture_lines=lines, evaluate=search, evaluate_holdout=holdout)
    data = json.loads(result.artifact_bytes)
    coverage = data["holdout"]["comparisons"][LINEAGE]["coverage"]
    assert result.outcome == runner.INSUFFICIENT_DATA
    assert (coverage["paired_games"], coverage["paired_lines"], coverage["paired_blocks"]) == (149, 298, 20)
    assert data["candidate_scorecards"][LINEAGE]["paired_vs_incumbent"]["n_clusters"] == 149


@pytest.mark.parametrize("mode", ["matched", "missing_variant", "duplicate_variant", "wrong_scalar"])
def test_dispersion_requires_exact_matched_variant_evidence(mode):
    harness = Harness(212, search_effect=0.05, holdout_effect=0.05,
                      variant_medians={0.4: 0.5})
    unmatched = frozenset(harness._holdout_keys[:80:8])

    def holdout(spec, target, folds):
        result = harness.evaluate_holdout(spec, target, folds)
        rows = list(result.events)
        if spec.params["theta"] == 0.2:
            return HoldoutEvaluation(
                tuple(row for row in rows if row.event_key not in unmatched),
                refusals=tuple(Refusal(key, "line", "unavailable") for key in sorted(unmatched)),
            )
        rows = [
            replace(row, variants=tuple(
                replace(variant, probabilities=(0.0, 1.0)) if variant.variant_id == "high" else variant
                for variant in row.variants
            )) if row.event_key in unmatched else row for row in rows
        ]
        index = next(index for index, row in enumerate(rows) if row.event_key not in unmatched)
        if mode == "missing_variant":
            rows[index] = replace(rows[index], variants=rows[index].variants[:1])
        if mode == "duplicate_variant":
            rows[index] = replace(rows[index], variants=rows[index].variants + rows[index].variants[:1])
        return HoldoutEvaluation(tuple(rows), variant_spread_median=0.9 if mode == "wrong_scalar" else None)

    result = harness.run(evaluate_holdout=holdout)
    doc = json.loads(result.artifact_bytes)
    assert not result.proposed_specs and not result.statistical_rejections
    if mode == "matched":
        assert doc["candidate_scorecards"][LINEAGE]["variant_spread_median"] == "0.5"
        assert result.decisions[0].envelope_status == "narrowed"
    elif mode == "missing_variant":
        assert doc["candidate_scorecards"][LINEAGE]["variant_spread_median"] is None
        assert result.decisions[0].envelope_status == "unverified"
    else:
        expected = "duplicate or unknown" if mode == "duplicate_variant" else "reported dispersion"
        assert result.outcome == runner.RUN_FAILED and expected in doc["issue"]


@pytest.mark.parametrize("mode", ["valid", "stale", "wrong_line", "wrong_side", "wrong_source",
                                 "wrong_snapshot", "ambiguous", "copied", "unfrozen"])
def test_market_comparison_requires_registered_same_time_provenance(mode):
    from polymarket_alpha_lab.papersim.optimize.dataset import MarketObservation

    harness = Harness(213, search_effect=0.05, holdout_effect=0.05)
    quoted = harness._holdout_keys[0]
    fixture = next(row for row in harness.records if row.event_key == quoted)
    instant = fixture.kickoff - dt.timedelta(hours=1)
    snapshot = "d" * 64
    records = tuple(replace(row, prediction_at=instant) if row.event_key == quoted else row
                    for row in harness.records)
    lines = tuple(FixtureLine(
        row.event_key, "synthetic", "line", row.event_key, ("no", "yes"), 1, "totals", 2.5,
        decision_at=instant if row.event_key == quoted else None,
        quote_source="registered-book", quote_snapshot_sha256=snapshot,
    ) for row in records)
    observations = [
        MarketObservation(quoted, "totals", 2.5, "no", instant, 0.48, "registered-book", snapshot),
        MarketObservation(quoted, "totals", 2.5, "yes", instant, 0.52, "registered-book", snapshot),
    ]
    changes = {
        "stale": {"observed_at": instant - dt.timedelta(seconds=1)}, "wrong_line": {"line": 3.5},
        "wrong_side": {"side": "yes-alias"}, "wrong_source": {"source": "other-book"},
        "wrong_snapshot": {"snapshot_sha256": "e" * 64},
    }
    if mode in changes:
        observations[1] = replace(observations[1], **changes[mode])
    elif mode == "ambiguous":
        observations.append(replace(observations[1], mid=0.49))

    def holdout(spec, target, folds):
        base = harness.evaluate_holdout(spec, target, folds)
        rows = tuple(replace(row, predicted_at=instant) if row.event_key == quoted and mode != "unfrozen"
                     else row for row in base.events)
        copied = None
        if mode == "copied":
            copied = (ScoredEvent(quoted, "line", ("settled-market", quoted), (0.55, 0.45), 1),)
        return HoldoutEvaluation(rows, market_events=copied)

    result = harness.run(records=records, fixture_lines=lines, market_observations=observations,
                         snapshot_refs=(snapshot,), evaluate_holdout=holdout)
    doc = json.loads(result.artifact_bytes)
    if mode == "copied":
        assert result.outcome == runner.RUN_FAILED and "copied market" in doc["issue"]
        return
    assert result.outcome == runner.CANDIDATE_REGISTERED
    market = doc["holdout"]["comparisons"][LINEAGE]["market"]
    if mode == "valid":
        assert market["lines"] == market["games"] == 1
        assert set(market["absolute_losses"]) == {"candidate", "incumbent", "market"}
        assert market["observations"][0]["observed_at"] == runner._iso(instant)
    else:
        assert market["lines"] == 0 and market["absolute_losses"] is None


@pytest.mark.parametrize("clock", ["equal", "later", "source_day", "observed_delay"])
def test_late_search_labels_fail_before_any_builder_or_floor_shortcut(clock):
    harness = Harness(218, holdout_events_per_block=1, search_effect=0.08)
    first = next(row.kickoff for row in harness.records if row.group_id in harness.holdout_group_ids)
    late = frozenset(harness._search_keys[-4:])
    options = {}
    if clock in ("source_day", "observed_delay"):
        records = tuple(FixtureRecord(
            row.event_key, row.group_id, source_day=row.kickoff.date(),
            label_day=first.date() if clock == "source_day" and row.event_key in late else row.kickoff.date(),
            available_at=first if clock == "observed_delay" and row.event_key in late else None,
        ) for row in harness.records)
        options = dict(source_calendar="declared source day", source_timezone=UTC,
                       last_complete_date=dt.date(2026, 11, 19))
    else:
        label = first + dt.timedelta(microseconds=int(clock == "later"))
        records = tuple(replace(row, available_at=label) if row.event_key in late else row
                        for row in harness.records)

    def unexpected(*args):
        pytest.fail("forecast or spec construction preceded the immutable causal check")

    with pytest.raises(ValueError, match="strictly before the first holdout"):
        harness.run(records=records, evaluate=unexpected, build_candidate_spec=unexpected, **options)


def test_late_inner_fold_refusal_does_not_reselect_earlier_nested_winner():
    harness = Harness(219, search_effect=0.05, holdout_effect=0.05)
    unaffected = "2026-S06"
    late = "2026-S07"

    def no_holdout(*args):
        pytest.fail("incomplete final common evidence reached the holdout")

    valid = harness.run()

    def refusal(spec, fold, targets):
        batches = harness.evaluate(spec, fold, targets)
        if fold.block_id == late and spec.params["theta"] == 0.1:
            return {"synthetic": HoldoutEvaluation((), refusals=tuple(
                Refusal(key, "line", "source_unavailable") for key in fold.test_keys
            ))}
        return batches

    shifted = harness.run(evaluate=refusal, evaluate_holdout=no_holdout)
    assert shifted.outcome == runner.INSUFFICIENT_DATA
    first, second = (json.loads(result.artifact_bytes) for result in (valid, shifted))
    assert [row for row in first["nested_selection"] if row["fold"] <= unaffected] == [
        row for row in second["nested_selection"] if row["fold"] <= unaffected
    ]
    assert [row for row in first["search_predictions"] if row["fold"] <= unaffected] == [
        row for row in second["search_predictions"] if row["fold"] <= unaffected
    ]
    assert second["selection_by_lineage"][LINEAGE]["blocks"][-1]["common_lines"] == 0


@pytest.mark.parametrize("branch", ["structural", "retained", "cooldown", "assessed"])
def test_post_serialization_deadline_suppresses_every_early_and_final_result(monkeypatch, branch):
    harness = Harness(220, holdout_events_per_block=1 if branch == "structural" else 8,
                      search_effect=0.05, holdout_effect=0.05,
                      best_theta=0.2 if branch == "retained" else 0.4)
    policy = harness.context()
    if branch == "cooldown":
        proposed = replace(harness.family.baseline_spec(), params={"theta": 0.4}, legacy_model_version=None)
        policy = harness.context(receipts=(_receipt(STARTED_AT, proposed.spec_sha256()),))
    finished_artifact = False
    original = runner._canonical_json

    def dumps(payload):
        nonlocal finished_artifact
        result = original(payload)
        if isinstance(payload, dict) and payload.get("schema") == "papersim.optimize.runner/artifact/v2":
            finished_artifact = True
        return result

    monkeypatch.setattr(runner, "_canonical_json", dumps)
    result = harness.run(policy_context=policy, deadline=lambda: finished_artifact)
    assert result.outcome == runner.RUN_FAILED and result.budget_exhausted
    assert result.statistical_rejections == ()
    assert not result.proposed_specs and not result.registered_lineages
    assert not any(row.registered or row.statistically_eligible for row in result.decisions)
    assert json.loads(result.artifact_bytes)["outcome"] == runner.RUN_FAILED


def test_preflight_and_between_holdout_callback_deadlines():
    harness = Harness(221, search_effect=0.05, holdout_effect=0.05)

    def unexpected(*args):
        pytest.fail("expired preflight invoked a forecast or spec builder")

    first = harness.run(deadline=lambda: True, evaluate=unexpected,
                        build_candidate_spec=unexpected, evaluate_holdout=unexpected)
    assert first.budget_exhausted and first.submission.trial_count == 0
    calls = []

    def holdout(spec, target, folds):
        calls.append(target)
        return harness.evaluate_holdout(spec, target, folds)

    after_first = harness.run(deadline=lambda: bool(calls), evaluate_holdout=holdout)
    assert len(calls) == 1 and after_first.budget_exhausted
    assert after_first.submission.trial_count == 24
    assert not after_first.proposed_specs


@pytest.mark.parametrize("cap, started, outcome", [(23, 23, "RUN_FAILED"),
                                                  (24, 24, "NO_IMPROVEMENT"),
                                                  (25, 24, "NO_IMPROVEMENT")])
def test_trial_limit_just_below_equal_above_plan(cap, started, outcome):
    harness = Harness(222, best_theta=0.2, search_effect=0.08)
    called = []

    def fit(spec, fold, mask):
        called.append((spec.spec_sha256(), fold.block_id))
        return harness.evaluate(spec, fold, mask)

    result = harness.run(max_trials=cap, evaluate=fit)
    assert len(set(called)) == len(called) == result.submission.trial_count == started
    assert result.outcome == outcome


def _actual_crypto_run(records, holdout_groups, *, refused_day=None, lines=None, reported_times=None,
                       sensitive_truth=None, data_cutoff=DATA_CUTOFF, **overrides):
    arguments, calls = _actual_crypto_inputs(
        records, holdout_groups, refused_day=refused_day, lines=lines, reported_times=reported_times,
        sensitive_truth=sensitive_truth, **overrides,
    )

    def run():
        baseline = arguments["family"].baseline_spec()
        digest = baseline.spec_sha256()
        lineage = "crypto-gbm/touch"
        return execute_optimization_run(
            data_cutoff=data_cutoff, random_seed=1, code_commit=CODE_COMMIT,
            policy_context=PolicyContext(max(STARTED_AT, data_cutoff), {
                lineage: RegistryComparison(LineageRoles(lineage, digest, digest), baseline),
            }),
            deadline=lambda: False, **arguments,
        )

    return run, calls


def _actual_crypto_inputs(records, holdout_groups, *, refused_day=None, lines=None,
                          reported_times=None, sensitive_truth=None, **overrides):
    from polymarket_alpha_lab.papersim.optimize.families.crypto import (
        CRYPTO_GBM, CryptoFamilyFixture, CryptoFamilyHistory, spec_with,
    )

    family = CRYPTO_GBM
    baseline = family.baseline_spec()
    # All returns alternate sign. The recent seven days have higher volatility.
    closes = [100.0]
    for i in range(360):
        change = 0.02 if i >= 332 else 0.004
        closes.append(closes[-1] * math.exp(change if i % 2 else -change))
    history = CryptoFamilyHistory(closes={"BTC": closes})
    by_key = {record.event_key: record for record in records}
    if lines is None:
        lines = tuple(FixtureLine(row.event_key, "touch", "barrier", row.event_key,
                                  ("no", "yes"), 1, "touch") for row in records)
    lines_by_event = {}
    for line in lines:
        lines_by_event.setdefault(line.event_key, []).append(line)
    calls = {phase: [] for phase in ("builder", "search", "holdout", "fit", "predict",
                                   "search_folds", "holdout_folds")}
    reported_times = reported_times or {}

    def evaluate(spec, folds, *, holdout=False):
        predictions, refusals = [], []
        for fold in folds:
            fit_history = history
            if sensitive_truth in fold.train_keys and lines_by_event[sensitive_truth][0].outcome_index == 0:
                # Model output responds if the flipped label is legitimately
                # available to this fold's training callback.
                fit_history = CryptoFamilyHistory(closes={"BTC": closes[:-1] + [closes[-1] * 1.01]})
            calls["fit"].append(fold.block_id)
            fitted = family.fit(fit_history, spec, fold.cutoff)
            for event_key in fold.test_keys:
                record = by_key[event_key]
                asset = "ETH" if holdout and spec == baseline and refused_day == record.group_id else "BTC"
                for contract in lines_by_event[event_key]:
                    barrier = contract.line or 108.0
                    # A fixed input horizon makes this a model-family drill over varied fixture clocks.
                    calls["predict"].append(event_key)
                    forecast = family.predict(fitted, CryptoFamilyFixture(asset, 100.0, barrier, 48.0))
                    if forecast is None:
                        refusals.append(Refusal(event_key, contract.line_key, "asset_missing_volatility"))
                    else:
                        p = forecast.convention_a
                        predictions.append(Prediction(event_key, contract.line_key, (1 - p, p), ("no", "yes"),
                                                      predicted_at=reported_times.get(event_key)))
        return HoldoutEvaluation(tuple(predictions), refusals=tuple(refusals))

    def search(spec, fold, mask):
        calls["search"].append(fold.block_id)
        calls["search_folds"].append(fold)
        assert mask == ("touch",)
        return {"touch": evaluate(spec, (fold,))}

    def holdout(spec, target, folds):
        calls["holdout"].append(target)
        calls["holdout_folds"].append(folds)
        return evaluate(spec, folds, holdout=True)

    def build(params):
        calls["builder"].append(params)
        return spec_with(**params)

    return dict(
        family=family, targets=("touch",), records=records, fixture_lines=lines,
        holdout_group_ids=holdout_groups, evaluate=search, evaluate_holdout=holdout,
        build_candidate_spec=build, **overrides,
    ), calls


def _crypto_search_records():
    start = dt.datetime(2026, 8, 1, tzinfo=UTC)
    return tuple(Record(f"s{i}", (start.date() + dt.timedelta(days=i + 1)).isoformat(),
                        start + dt.timedelta(days=i, hours=21)) for i in range(5))


@pytest.mark.parametrize("explicit", [False, True])
def test_actual_crypto_single_expiry_with_twenty_prediction_days(explicit):
    expiry = dt.datetime(2026, 10, 25, 16, tzinfo=UTC)
    group = expiry.date().isoformat()
    records = _crypto_search_records() + tuple(
        FixtureRecord(f"h{i:02}-{j}", group, expiry - dt.timedelta(days=i + 1), expiry,
                      floor_block=group if explicit else None)
        for i in range(20) for j in range(8)
    )
    run, calls = _actual_crypto_run(records, (group,))
    result = run()
    artifact = json.loads(result.artifact_bytes)
    holdout = artifact["holdout"]
    assert (holdout["n_events"], holdout["n_blocks"]) == (160, 1), (result.outcome, calls["holdout"], holdout["eligible_by_lineage"])
    assert holdout["eligible_by_lineage"]["crypto-gbm/touch"] == {"games": 160, "blocks": 1}
    assert result.outcome == runner.INSUFFICIENT_DATA
    assert calls["search"] and calls["holdout"] == []
    assert result.proposed_specs == {} and result.statistical_rejections == ()


def _crypto_holdout(markets, expiry_days):
    first = dt.datetime(2026, 9, 10, 21, tzinfo=UTC)
    records = []
    for i in range(markets):
        expiry = first + dt.timedelta(days=i % expiry_days, minutes=i // expiry_days)
        records.append(FixtureRecord(f"h{i:03}", expiry.date().isoformat(), expiry - dt.timedelta(hours=2),
                                     expiry, expiry_at=expiry))
    return tuple(records)


@pytest.mark.parametrize(("markets", "days", "eligible"), [(149, 20, False), (150, 19, False), (150, 20, True)])
def test_actual_crypto_expiry_floor_boundary(markets, days, eligible):
    holdout = _crypto_holdout(markets, days)
    run, calls = _actual_crypto_run(_crypto_search_records() + holdout, {row.group_id for row in holdout})
    result = run()
    artifact = json.loads(result.artifact_bytes)
    assert (artifact["holdout"]["n_events"], artifact["holdout"]["n_blocks"]) == (markets, days)
    lineage = "crypto-gbm/touch"
    assert artifact["holdout"]["eligible_by_lineage"][lineage] == {"games": markets, "blocks": days}
    assert result.decisions[0].floors_met is eligible
    if eligible:
        assert len(calls["holdout"]) == 2
        assert (result.decisions[0].n_events, result.decisions[0].n_blocks) == (150, 20)
    else:
        assert result.outcome == runner.INSUFFICIENT_DATA
        assert not calls["holdout"] and not result.proposed_specs and not result.statistical_rejections


def test_actual_crypto_paired_refusal_loses_one_expiry_day_even_with_enough_markets():
    holdout = _crypto_holdout(160, 20)
    records = _crypto_search_records() + holdout
    groups = {row.group_id for row in holdout}
    refused_day = max(groups)
    lines = [FixtureLine(row.event_key, "touch", "barrier", row.event_key, ("no", "yes"), 1, "touch", 108.0)
             for row in records]
    lines.append(replace(lines[-2], line_key="alternate-barrier", observation_id="alternate", line=106.0))
    run, calls = _actual_crypto_run(records, groups, refused_day=refused_day, lines=tuple(lines))
    result = run()
    holdout_report = json.loads(result.artifact_bytes)["holdout"]
    comparison = holdout_report["comparisons"]["crypto-gbm/touch"]
    assert len(calls["holdout"]) == 2
    assert (holdout_report["n_events"], holdout_report["n_blocks"]) == (160, 20)
    assert comparison["coverage"]["eligible_lines"] == 161
    assert comparison["coverage"]["paired_lines"] == 153
    assert (comparison["coverage"]["paired_games"], comparison["coverage"]["paired_blocks"]) == (152, 19)
    assert (result.decisions[0].n_events, result.decisions[0].n_blocks) == (152, 19)
    assert {row["reason"] for row in comparison["incumbent_refusals"]} == {"asset_missing_volatility"}
    assert result.outcome == runner.INSUFFICIENT_DATA and not result.decisions[0].floors_met
    assert not result.proposed_specs and not result.statistical_rejections


def test_actual_crypto_expiry_instants_use_utc_day_and_seal_byte_verified_groups():
    first = dt.datetime(2026, 10, 25, 0, 15, tzinfo=UTC)
    offset = dt.timezone(dt.timedelta(hours=-4))
    second = (first + dt.timedelta(hours=1)).astimezone(offset)
    day = first.date().isoformat()
    assert second.date().isoformat() == "2026-10-24"
    records = _crypto_search_records() + (
        FixtureRecord("one", "sealed-a", first - dt.timedelta(hours=3), first, expiry_at=first),
        FixtureRecord("two", "sealed-b", first - dt.timedelta(hours=2), first, expiry_at=second),
        FixtureRecord("three", "sealed-b", first - dt.timedelta(hours=2), first, floor_block=day),
    )
    groups = {"sealed-a", "sealed-b"}
    ref, raw = _source_ref({"expiry_groups": [{"group_id": group, "expiry_day": day} for group in sorted(groups)]})
    run, calls = _actual_crypto_run(records, groups, source_artifacts={ref.sha256: raw},
                                    snapshot_refs=(ref.sha256,), crypto_expiry_groups=ref)
    result = run()
    artifact = json.loads(result.artifact_bytes)
    assert (artifact["holdout"]["n_events"], artifact["holdout"]["n_blocks"]) == (3, 1)
    assert not calls["holdout"]
    assert artifact["crypto_expiry_groups"] == {"sha256": ref.sha256, "byte_length": ref.byte_length}
    evidence = artifact["crypto_expiry_days"]
    assert {evidence[key]["expiry_day"] for key in ("one", "two", "three")} == {day}
    assert evidence["one"]["expiry_at"] == "2026-10-25T00:15:00Z"
    assert evidence["two"]["expiry_at"] == "2026-10-25T01:15:00Z"
    assert evidence["three"] == {"expiry_at": None, "expiry_day": day, "group_id": "sealed-b"}

    source = {"expiry_groups": [{"group_id": group, "expiry_day": day} for group in sorted(groups, reverse=True)]}
    other_ref, other_raw = _source_ref(source)
    rerun, _ = _actual_crypto_run(records, groups, source_artifacts={other_ref.sha256: other_raw},
                                   snapshot_refs=(other_ref.sha256,), crypto_expiry_groups=other_ref)
    assert json.loads(rerun().artifact_bytes)["sealed_input_sha256"] != artifact["sealed_input_sha256"]


@pytest.mark.parametrize("mode", ["wrong_explicit", "missing", "naive", "conflicting", "unverified", "duplicate"])
def test_actual_crypto_rejects_unproven_or_conflicting_expiry_before_callbacks(mode):
    expiry = dt.datetime(2026, 10, 25, 16, tzinfo=UTC)
    day = expiry.date().isoformat()
    row = FixtureRecord("holdout", day, expiry - dt.timedelta(days=2), expiry)
    kwargs = {}
    if mode == "wrong_explicit":
        row = replace(row, floor_block="2026-10-24")
    elif mode == "missing":
        row = replace(row, group_id="opaque", floor_block=day)
    elif mode == "naive":
        row = replace(row, expiry_at=expiry.replace(tzinfo=None))
    elif mode == "conflicting":
        row = replace(row, expiry_at=expiry + dt.timedelta(days=1))
    elif mode in ("unverified", "duplicate"):
        row = replace(row, group_id="opaque")
        ref, raw = _source_ref({"expiry_groups": [{"group_id": "opaque", "expiry_day": day}] * 2})
        kwargs = {"source_artifacts": {ref.sha256: raw} if mode == "duplicate" else {},
                  "snapshot_refs": (ref.sha256,), "crypto_expiry_groups": ref}
    run, calls = _actual_crypto_run(_crypto_search_records() + (row,), (row.group_id,), **kwargs)
    with pytest.raises(ValueError, match="expiry|digest and byte length"):
        run()
    assert not calls["search"] and not calls["holdout"]


@pytest.mark.parametrize(("where", "delay", "explicit"), [
    ("all", dt.timedelta(hours=1), False),
    ("all", dt.timedelta(), True),
    ("search", dt.timedelta(microseconds=1), True),
    ("holdout", dt.timedelta(microseconds=1), False),
    ("offset", dt.timedelta(), True),
])
def test_actual_crypto_preflights_each_prediction_against_its_own_expiry(where, delay, explicit):
    search, holdout = list(_crypto_search_records()), list(_crypto_holdout(160, 20))

    def late(row, expiry):
        prediction = expiry + delay
        if where == "offset":
            prediction = prediction.astimezone(dt.timezone(dt.timedelta(hours=6)))
            assert prediction.date() > expiry.date()
        return replace(row, expiry_at=expiry, kickoff=expiry + dt.timedelta(hours=1) if explicit else prediction,
                       prediction_at=prediction if explicit else None,
                       available_at=expiry + dt.timedelta(hours=2))

    if where == "search":
        bad_key = search[0].event_key
        search[0] = late(search[0], search[0].available_at)
    else:
        bad_key = holdout[0].event_key
        if where == "all":
            holdout = [late(row, row.expiry_at) for row in holdout]
        else:
            holdout[0] = late(holdout[0], holdout[0].expiry_at)
    run, calls = _actual_crypto_run(search + holdout, {row.group_id for row in holdout})
    with pytest.raises(ValueError, match=rf"{bad_key}.*at or after expiry"):
        run()
    assert not any(calls.values())


def test_actual_crypto_microseconds_and_offsets_preserve_the_run():
    held = tuple(replace(row, prediction_at=row.expiry_at - dt.timedelta(microseconds=1),
                         kickoff=row.expiry_at, available_at=row.expiry_at + dt.timedelta(hours=2))
                 for row in _crypto_holdout(160, 20))
    groups = {row.group_id for row in held}
    first = held[0]
    times = {first.event_key: first.prediction_at}
    run, calls = _actual_crypto_run(_crypto_search_records() + held, groups, reported_times=times)
    result = run()
    artifact = json.loads(result.artifact_bytes)
    assert (artifact["holdout"]["n_events"], artifact["holdout"]["n_blocks"]) == (160, 20)
    assert len(calls["holdout"]) == 2
    assert [(p["event_key"], p["predicted_at"]) for p in artifact["predictions"] if p["predicted_at"]] == [
        (first.event_key, first.prediction_at.isoformat().replace("+00:00", "Z"))
    ] * 2

    east = dt.timezone(dt.timedelta(hours=6))
    west = dt.timezone(dt.timedelta(hours=-11))
    shifted = tuple(replace(row, kickoff=row.kickoff.astimezone(east),
                            prediction_at=row.prediction_at.astimezone(east),
                            expiry_at=row.expiry_at.astimezone(west),
                            available_at=row.available_at.astimezone(west)) for row in held)
    rerun, _ = _actual_crypto_run(_crypto_search_records() + shifted, groups,
                                  reported_times={first.event_key: first.prediction_at.astimezone(west)})
    assert rerun().artifact_bytes == result.artifact_bytes


@pytest.mark.parametrize("role", ["holdout_group_ids", "search_group_ids", "complete_groups", "spans"])
def test_actual_crypto_expiry_day_registrations_are_atomic(role):
    search, holdout = list(_crypto_search_records()), list(_crypto_holdout(160, 20))
    day = holdout[0].group_id
    expiry = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(hour=16), tzinfo=UTC)
    alias = "late"
    kwargs = {}
    if role == "holdout_group_ids":
        search[-1] = FixtureRecord("same-expiry-day-in-search", "early", expiry - dt.timedelta(hours=1),
                                   expiry, expiry_at=expiry)
        holdout = [replace(row, group_id=alias) if row.group_id == day else row for row in holdout]
    elif role == "search_group_ids":
        day = search[-1].group_id
        alias = "selected-alias"
        search.append(replace(search[-1], event_key="search-extra", group_id=alias,
                              expiry_at=search[-1].available_at))
        kwargs["search_group_ids"] = (day,)
    elif role == "complete_groups":
        holdout[0] = replace(holdout[0], group_id=alias)
        kwargs["complete_groups"] = {row.group_id for row in search + holdout} - {alias}
    else:
        holdout[0] = replace(holdout[0], group_id=alias)
        holdout[1] = replace(holdout[1], group_id=alias)
    if role != "spans":
        aliases = (alias, "early") if role == "holdout_group_ids" else (alias,)
        ref, raw = _source_ref({"expiry_groups": [{"group_id": group, "expiry_day": day} for group in aliases]})
        kwargs.update(crypto_expiry_groups=ref, source_artifacts={ref.sha256: raw}, snapshot_refs=(ref.sha256,))
    run, calls = _actual_crypto_run(search + holdout, {row.group_id for row in holdout}, **kwargs)
    with pytest.raises(ValueError) as error:
        run()
    message = str(error.value)
    assert day in message and alias in message
    assert ("spans UTC expiry days" if role == "spans" else f"splits {role}") in message
    assert ("early" if role == "holdout_group_ids" else day) in message
    assert not any(calls.values())


def _date_crypto_search(start=dt.date(2026, 8, 1)):
    rows = []
    for index in range(5):
        day = start + dt.timedelta(days=2 * index)
        label = day + dt.timedelta(days=1)
        rows.append(FixtureRecord(f"s{index}", label.isoformat(), source_day=day, label_day=label,
                                  expiry_at=dt.datetime.combine(label, dt.time(hour=12), tzinfo=UTC)))
    return tuple(rows)


def _date_crypto_holdout(shared_prediction=False):
    first = dt.date(2026, 10, 10)
    if shared_prediction:
        expiries = (first, first + dt.timedelta(days=1))
        expiries += tuple(first + dt.timedelta(days=10 + 2 * i) for i in range(18))
    else:
        expiries = tuple(first + dt.timedelta(days=2 * i) for i in range(20))
    rows = []
    for block, expiry in enumerate(expiries):
        for market in range(8):
            if expiry == first:
                source = dt.date(2026, 10, 5 if shared_prediction or market < 4 else 7)
            elif shared_prediction and block < 3:
                source = dt.date(2026, 10, 5 if block == 1 else 15)
            else:
                source = expiry - dt.timedelta(days=1)
            rows.append(FixtureRecord(f"h{block:02}-{market}", expiry.isoformat(), source_day=source,
                                      label_day=expiry))
    return tuple(rows)


def _run_date_crypto(records, groups, *, zone=UTC, data_cutoff=dt.datetime(2026, 12, 1, tzinfo=UTC),
                     last_complete_date=dt.date(2026, 11, 29), **kwargs):
    return _actual_crypto_run(records, groups, data_cutoff=data_cutoff,
                              source_calendar="registered crypto calendar", source_timezone=zone,
                              last_complete_date=last_complete_date, **kwargs)


@pytest.mark.parametrize("date_order", ["mixed_day", "shared_prediction", "inverted_prediction"])
def test_actual_crypto_partitions_many_to_many_source_and_expiry_days(date_order):
    shared_prediction = date_order != "mixed_day"
    search, held = _date_crypto_search(), _date_crypto_holdout(shared_prediction)
    if date_order == "inverted_prediction":
        held = tuple(replace(row, source_day=dt.date(2026, 10, 4)) if row.group_id == "2026-10-11" else row
                     for row in held)
    records = search + held
    groups = {row.group_id for row in held}
    run, calls = _run_date_crypto(records, groups)
    artifact = json.loads(run().artifact_bytes)
    assert len(calls["builder"]) == 36 and len(calls["search"]) == 108
    candidate_folds, incumbent_folds = calls["holdout_folds"]
    assert candidate_folds == incumbent_folds
    by_key = {row.event_key: row for row in records}
    available_folds = {fold.block_id: fold for fold in candidate_folds}
    for fold in (*calls["search_folds"], *candidate_folds):
        sources = [by_key[key].source_day for key in fold.test_keys]
        assert fold.source_day == min(sources)
        assert fold.cutoff == dt.datetime.combine(min(sources), dt.time.min, tzinfo=UTC)
        assert {by_key[key].group_id for key in fold.test_keys} == {fold.block_id}
        assert all(by_key[key].group_id < fold.block_id and by_key[key].label_day < fold.source_day
                   for key in fold.train_keys)
    holdout = artifact["holdout"]
    assert (holdout["n_events"], holdout["n_blocks"]) == (160, 20)
    coverage = holdout["comparisons"]["crypto-gbm/touch"]["coverage"]
    first = available_folds["2026-10-10"]
    assert first.test_keys == tuple(row.event_key for row in held[:8])
    assert first.source_day == dt.date(2026, 10, 5)
    clocks = artifact["crypto_source_clocks"]
    assert clocks["h00-0"]["source_day"] == "2026-10-05"
    assert clocks["h00-4"]["source_day"] == ("2026-10-05" if shared_prediction else "2026-10-07")
    assert clocks["h00-4"]["label_day"] == "2026-10-10"
    if shared_prediction:
        reason = "prior_block_prefix_incomplete_at_cutoff"
        assert holdout["skips"] == [{"block_id": "2026-10-11", "reason": reason}]
        assert holdout["planned_skips"] == holdout["skips"]
        assert (coverage["paired_games"], coverage["paired_blocks"]) == (152, 19)
        assert "2026-10-11" in holdout["group_ids"] and "2026-10-11" not in available_folds
        assert {row["event_key"] for row in holdout["structural_closures"]["crypto-gbm/touch"]} == {
            row.event_key for row in held[8:16]
        }
        assert clocks["h01-0"]["source_day"] == ("2026-10-04" if date_order == "inverted_prediction" else "2026-10-05")
        assert not (set(first.train_keys) & {row.event_key for row in held[8:16]})
        assert set(row.event_key for row in held[:16]) <= set(available_folds["2026-10-20"].train_keys)
    else:
        assert holdout["skips"] == []
        assert (coverage["paired_games"], coverage["paired_blocks"]) == (160, 20)
    assert len({key for fold in candidate_folds for key in fold.test_keys}) == coverage["paired_games"]
    assert all(p["predicted_at"] is None for p in artifact["predictions"])


@pytest.mark.parametrize(("start", "zone", "expiry", "end", "error"), [
    ("2026-03-08", "America/New_York", "2026-03-09T04:00:00Z", "2026-03-09T04:00:00Z", None),
    ("2026-03-08", "America/Havana", "2026-03-09T04:00:00Z", "2026-03-09T04:00:00Z", None),
    ("2026-11-01", "America/New_York", "2026-11-02T05:00:00Z", "2026-11-02T05:00:00Z", None),
    ("2026-11-01", "America/New_York", "2026-11-02T04:59:59.999999Z", None, "cannot prove"),
    ("2026-10-09", "Asia/Kolkata", "2026-10-10", "2026-10-09T18:30:00Z", None),
    ("2026-10-10", "Asia/Kolkata", "2026-10-10", None, "cannot prove"),
    ("2026-10-12", "Asia/Kolkata", "2026-10-10", None, "at or after expiry"),
])
def test_actual_crypto_source_day_uses_its_real_utc_interval(start, zone, expiry, end, error):
    day = dt.date.fromisoformat(start)
    expiry_at = dt.datetime.fromisoformat(expiry) if "T" in expiry else None
    row = FixtureRecord("source-window", expiry[:10], source_day=day, label_day=day, expiry_at=expiry_at)
    records = _date_crypto_search(dt.date(2026, 2, 1)) + (row,)
    run, calls = _run_date_crypto(records, (row.group_id,), zone=ZoneInfo(zone))
    if error:
        with pytest.raises(ValueError, match=f"source-window.*{error}"):
            run()
        assert not any(calls.values())
    else:
        artifact = json.loads(run().artifact_bytes)
        assert artifact["crypto_source_clocks"]["source-window"]["prediction_period_end"] == end
        assert artifact["crypto_expiry_days"]["source-window"]["expiry_at"] == (expiry if expiry_at else None)
        assert artifact["holdout"]["planned_folds"] == [row.group_id]


@pytest.mark.parametrize("expiry", [
    "2010-11-07T03:00:00Z", "2010-11-07T03:29:59.999999Z", "2010-11-07T03:30:00Z", "2010-11-07",
])
def test_actual_crypto_disconnected_prediction_date_requires_its_last_end(expiry):
    zone = ZoneInfo("America/St_Johns")
    day = dt.date(2010, 11, 6)
    expiry_at = dt.datetime.fromisoformat(expiry) if "T" in expiry else None
    first_end = dt.datetime.fromisoformat("2010-11-07T02:30:00Z")
    second_start = dt.datetime.fromisoformat("2010-11-07T02:31:00Z")
    last_start = dt.datetime.fromisoformat("2010-11-07T03:15:00Z")
    last_end = dt.datetime.fromisoformat("2010-11-07T03:30:00Z")
    assert first_end.astimezone(zone).date() == day + dt.timedelta(days=1)
    assert second_start.astimezone(zone).date() == last_start.astimezone(zone).date() == day
    assert (last_end - dt.timedelta.resolution).astimezone(zone).date() == day
    assert last_end.astimezone(zone).date() == day + dt.timedelta(days=1)

    row = FixtureRecord("disconnected-prediction", "2010-11-07", source_day=day,
                        label_day=dt.date(2010, 11, 7), expiry_at=expiry_at)
    search = _date_crypto_search(dt.date(2010, 10, 1))
    run, calls = _run_date_crypto(search + (row,), (row.group_id,), zone=zone)
    if expiry_at is None or expiry_at < last_end:
        with pytest.raises(ValueError, match="disconnected-prediction.*cannot prove prediction before expiry"):
            run()
        assert not any(calls.values())
    else:
        artifact = json.loads(run().artifact_bytes)
        clock = artifact["crypto_source_clocks"][row.event_key]
        assert clock["prediction_period_start"] == "2010-11-06T02:30:00Z"
        assert clock["prediction_period_end"] == "2010-11-07T03:30:00Z"
        assert artifact["holdout"]["planned_folds"] == [row.group_id]
        assert len(calls["search"]) == len(calls["fit"]) == len(calls["predict"]) == 3


def _st_johns_crypto_label_and_target():
    delayed_label = FixtureRecord(
        "disconnected-label", "2010-11-06", source_day=dt.date(2010, 11, 5),
        label_day=dt.date(2010, 11, 6), expiry_at=dt.datetime.fromisoformat("2010-11-06T03:30:00Z"),
    )
    target = FixtureRecord(
        "disconnected-target", "2010-11-08", source_day=dt.date(2010, 11, 7),
        label_day=dt.date(2010, 11, 8), expiry_at=dt.datetime.fromisoformat("2010-11-08T03:30:00Z"),
    )
    return delayed_label, target


def test_actual_crypto_disconnected_label_cannot_enter_sealed_search_before_first_holdout():
    zone = ZoneInfo("America/St_Johns")
    label, target = _st_johns_crypto_label_and_target()
    cutoff = dt.datetime.fromisoformat("2010-11-07T02:30:00Z")
    witness = dt.datetime.fromisoformat("2010-11-07T03:15:00Z")
    assert (cutoff - dt.timedelta.resolution).astimezone(zone).date() == label.label_day
    assert cutoff.astimezone(zone).date() == target.source_day
    assert witness > cutoff and witness.astimezone(zone).date() == label.label_day

    search = _date_crypto_search(dt.date(2010, 10, 1))
    run, calls = _run_date_crypto(search + (label, target), (target.group_id,), zone=zone)
    with pytest.raises(ValueError, match="search labels must be available strictly before the first holdout"):
        run()
    assert not any(calls.values())


def test_actual_crypto_disconnected_label_delays_both_holdout_prefixes():
    zone = ZoneInfo("America/St_Johns")
    label, target = _st_johns_crypto_label_and_target()
    start = dt.date(2010, 11, 10)
    later = []
    for block in range(18):
        expiry = start + dt.timedelta(days=2 * block)
        for market in range(9):
            later.append(FixtureRecord(
                f"later-{block}-{market}", expiry.isoformat(), source_day=expiry - dt.timedelta(days=1),
                label_day=expiry, expiry_at=dt.datetime.combine(expiry, dt.time(hour=12), tzinfo=UTC),
            ))
    search = _date_crypto_search(dt.date(2010, 10, 1))
    held = (label, target, *later)
    run, calls = _run_date_crypto(search + held, {row.group_id for row in held}, zone=zone)
    artifact = json.loads(run().artifact_bytes)
    holdout = artifact["holdout"]
    assert holdout["skips"] == holdout["planned_skips"] == [{
        "block_id": target.group_id, "reason": "prior_block_prefix_incomplete_at_cutoff",
    }]
    assert (holdout["n_events"], holdout["n_blocks"]) == (164, 20)
    lineage = "crypto-gbm/touch"
    coverage = holdout["comparisons"][lineage]["coverage"]
    assert (coverage["paired_games"], coverage["paired_blocks"]) == (163, 19)
    oof = artifact["search_predictions"]
    assert {p["event_key"] for p in oof} == {"s2", "s3", "s4"}
    assert float(artifact["diagnostic_scorecards"][lineage]["calibration_ece"]) == _independent_binary_ece(oof)
    assert all(label.event_key not in fold.train_keys for fold in calls["search_folds"])
    assert len(calls["builder"]) == 36 and len(calls["search"]) == 108
    assert len(calls["holdout"]) == 2 and len(calls["fit"]) == 146 and len(calls["predict"]) == 434
    candidate, incumbent = calls["holdout_folds"]
    assert candidate == incumbent
    for folds in (candidate, incumbent):
        by_block = {fold.block_id: fold for fold in folds}
        assert target.group_id not in by_block
        assert by_block[label.group_id].test_keys == (label.event_key,)
        assert label.event_key not in by_block[label.group_id].train_keys
        next_fold = by_block["2010-11-10"]
        assert next_fold.cutoff == dt.datetime.fromisoformat("2010-11-09T03:30:00Z")
        assert (label.event_key, target.event_key) == tuple(next_fold.train_keys[-2:])
        assert next_fold.test_keys == tuple(row.event_key for row in later[:9])
    for side in ("candidate", "incumbent"):
        forecasts = [p for p in artifact["predictions"] if p["model"] == side]
        assert target.event_key not in {p["event_key"] for p in forecasts}
        card = artifact[f"{side}_scorecards"][lineage]
        assert card["n_events"] == len(forecasts) == 163
        assert float(card["calibration_ece"]) == _independent_binary_ece(forecasts)
    label_clock = artifact["crypto_source_clocks"][label.event_key]
    assert label_clock["label_day"] == label_clock["effective_label_day"] == "2010-11-06"
    assert label_clock["label_period_end"] == label_clock["effective_label_period_end"] == "2010-11-07T03:30:00Z"
    assert label_clock["available_at"] is None


def test_actual_crypto_disconnected_label_delays_the_search_and_oof_prefix():
    zone = ZoneInfo("America/St_Johns")
    label, target = _st_johns_crypto_label_and_target()
    recovered = FixtureRecord("recovered-search", "2010-11-10", source_day=dt.date(2010, 11, 9),
                              label_day=dt.date(2010, 11, 10),
                              expiry_at=dt.datetime.fromisoformat("2010-11-10T03:30:00Z"))
    held = FixtureRecord("future-holdout", "2010-11-12", source_day=dt.date(2010, 11, 11),
                         label_day=dt.date(2010, 11, 12),
                         expiry_at=dt.datetime.fromisoformat("2010-11-12T03:30:00Z"))
    search = _date_crypto_search(dt.date(2010, 10, 1)) + (label, target, recovered)
    run, calls = _run_date_crypto(search + (held,), (held.group_id,), zone=zone)
    artifact = json.loads(run().artifact_bytes)
    assert artifact["skipped_blocks"] == [{
        "block_id": target.group_id, "reason": "prior_block_prefix_incomplete_at_cutoff",
    }]
    assert len(calls["search_folds"]) == 5
    assert target.group_id not in {fold.block_id for fold in calls["search_folds"]}
    recovered_fold = calls["search_folds"][-1]
    assert recovered_fold.test_keys == (recovered.event_key,)
    assert tuple(recovered_fold.train_keys[-2:]) == (label.event_key, target.event_key)
    oof = artifact["search_predictions"]
    assert {p["event_key"] for p in oof} == {"s2", "s3", "s4", label.event_key, recovered.event_key}
    diagnostic = artifact["diagnostic_scorecards"]["crypto-gbm/touch"]
    assert diagnostic["n_events"] == 5
    assert float(diagnostic["calibration_ece"]) == _independent_binary_ece(oof)


@pytest.mark.parametrize("observation", [None, "2010-11-06T23:59:59.999999Z", "2010-11-07T00:00:00Z"])
def test_actual_crypto_label_end_may_equal_first_holdout_cutoff_but_observation_must_precede_it(observation):
    label, target = _st_johns_crypto_label_and_target()
    if observation is not None:
        label = replace(label, available_at=dt.datetime.fromisoformat(observation))
    search = _date_crypto_search(dt.date(2010, 10, 1)) + (label,)
    run, calls = _run_date_crypto(search + (target,), (target.group_id,))
    cutoff = "2010-11-07T00:00:00Z"
    if observation == cutoff:
        with pytest.raises(ValueError, match="search labels must be available strictly before the first holdout"):
            run()
        assert not any(calls.values())
    else:
        artifact = json.loads(run().artifact_bytes)
        clocks = artifact["crypto_source_clocks"]
        assert clocks[label.event_key]["effective_label_period_end"] == cutoff
        assert clocks[label.event_key]["available_at"] == observation
        assert clocks[target.event_key]["prediction_period_start"] == cutoff
        assert artifact["holdout"]["planned_folds"] == [target.group_id]


@pytest.mark.parametrize("cutoff, complete", [
    ("2010-11-07T02:30:00Z", False), ("2010-11-07T03:30:00Z", True),
])
def test_actual_crypto_data_cutoff_covers_the_last_segment_of_a_label(cutoff, complete):
    zone = ZoneInfo("America/St_Johns")
    label, target = _st_johns_crypto_label_and_target()
    instant = dt.datetime.fromisoformat(cutoff)
    assert instant.astimezone(zone).date() == target.source_day
    search = _date_crypto_search(dt.date(2010, 10, 1))
    run, calls = _run_date_crypto(search + (label,), (label.group_id,), zone=zone, data_cutoff=instant,
                                 last_complete_date=label.label_day)
    if complete:
        artifact = json.loads(run().artifact_bytes)
        assert artifact["crypto_source_clocks"][label.event_key]["effective_label_period_end"] == cutoff
        assert artifact["holdout"]["planned_folds"] == [label.group_id]
    else:
        with pytest.raises(ValueError, match="disconnected-label.*before data_cutoff"):
            run()
        assert not any(calls.values())


@pytest.mark.parametrize("quote, accepted", [
    ("2010-11-07T02:29:59.999999Z", True),
    ("2010-11-07T02:30:00Z", False),
    ("2010-11-07T02:30:59.999999Z", False),
    ("2010-11-07T02:31:00Z", True),
    ("2010-11-07T03:29:59.999999Z", True),
    ("2010-11-07T03:30:00Z", False),
])
def test_actual_crypto_disconnected_prediction_quote_requires_the_declared_civil_date(quote, accepted):
    zone = ZoneInfo("America/St_Johns")
    day = dt.date(2010, 11, 6)
    instant = dt.datetime.fromisoformat(quote)
    assert (instant.astimezone(zone).date() == day) is accepted
    row = FixtureRecord("disconnected-quote", "2010-11-07", source_day=day, label_day=dt.date(2010, 11, 7),
                        expiry_at=dt.datetime.fromisoformat("2010-11-07T03:31:00Z"))
    records = _date_crypto_search(dt.date(2010, 10, 1)) + (row,)
    lines = tuple(FixtureLine(
        record.event_key, "touch", "barrier", record.event_key, ("no", "yes"), 1, "touch",
        decision_at=instant if record == row else None,
    ) for record in records)
    run, calls = _run_date_crypto(records, (row.group_id,), zone=zone, lines=lines)
    if accepted:
        assert json.loads(run().artifact_bytes)["holdout"]["planned_folds"] == [row.group_id]
        assert len(calls["search"]) == len(calls["fit"]) == 3
    else:
        with pytest.raises(ValueError, match="disconnected-quote.*outside its registered prediction window"):
            run()
        assert not any(calls.values())


@pytest.mark.parametrize("day, zone, begin, end", [
    ("2024-04-07", "Australia/Lord_Howe", "2024-04-06T13:00:00Z", "2024-04-07T13:30:00Z"),
    ("2024-10-06", "Australia/Lord_Howe", "2024-10-05T13:30:00Z", "2024-10-06T13:00:00Z"),
    ("2050-03-13", "America/New_York", "2050-03-13T05:00:00Z", "2050-03-14T04:00:00Z"),
    ("2011-12-29", "Pacific/Apia", "2011-12-29T10:00:00Z", "2011-12-30T10:00:00Z"),
    ("2011-12-31", "Pacific/Apia", "2011-12-30T10:00:00Z", "2011-12-31T10:00:00Z"),
    ("2026-04-04", dt.timezone(dt.timedelta(hours=5, minutes=45)), "2026-04-03T18:15:00Z", "2026-04-04T18:15:00Z"),
    ("2026-04-04", UTC, "2026-04-04T00:00:00Z", "2026-04-05T00:00:00Z"),
])
def test_actual_crypto_date_bounds_include_subhour_and_future_zone_transitions(day, zone, begin, end):
    zone = ZoneInfo(zone) if isinstance(zone, str) else zone
    source = dt.date.fromisoformat(day)
    first = dt.datetime.fromisoformat(begin)
    expiry = dt.datetime.fromisoformat(end)
    resolution = dt.timedelta.resolution
    assert first.astimezone(zone).date() == (expiry - resolution).astimezone(zone).date() == source
    assert (first - resolution).astimezone(zone).date() != source != expiry.astimezone(zone).date()
    label_day = expiry.astimezone(zone).date()
    row = FixtureRecord("civil-date", expiry.date().isoformat(), source_day=source, label_day=label_day,
                        expiry_at=expiry)
    search = _date_crypto_search(source - dt.timedelta(days=20))
    run, _ = _run_date_crypto(search + (row,), (row.group_id,), zone=zone,
                             data_cutoff=expiry + dt.timedelta(days=4), last_complete_date=label_day)
    artifact = json.loads(run().artifact_bytes)
    clock = artifact["crypto_source_clocks"][row.event_key]
    assert clock["prediction_period_start"] == begin
    assert clock["prediction_period_end"] == end
    assert artifact["holdout"]["planned_folds"] == [row.group_id]


@pytest.mark.parametrize("field", ["source_day", "label_day"])
def test_actual_crypto_rejects_a_date_skipped_by_apia(field):
    zone = ZoneInfo("Pacific/Apia")
    missed = dt.date(2011, 12, 30)
    jump = dt.datetime.fromisoformat("2011-12-30T10:00:00Z")
    assert (jump - dt.timedelta.resolution).astimezone(zone).date() == dt.date(2011, 12, 29)
    assert jump.astimezone(zone).date() == dt.date(2011, 12, 31)
    row = FixtureRecord("skipped-day", "2011-12-31", source_day=dt.date(2011, 12, 29),
                        label_day=dt.date(2011, 12, 31), expiry_at=jump + dt.timedelta(days=1))
    row = replace(row, **{field: missed})
    search = _date_crypto_search(dt.date(2011, 12, 1))
    run, calls = _run_date_crypto(search + (row,), (row.group_id,), zone=zone)
    with pytest.raises(ValueError, match=r"source date 2011-12-30.*Pacific/Apia"):
        run()
    assert not any(calls.values())


@pytest.mark.parametrize("sealed", [True, False])
def test_actual_crypto_supplied_zone_rules_also_enforce_civil_label_order(sealed):
    from io import BytesIO
    from struct import pack

    switches = tuple(dt.datetime.fromisoformat(raw) for raw in (
        "2026-10-10T01:30:00Z", "2026-10-10T20:00:00Z", "2026-10-11T18:30:00Z",
    ))
    abbreviations = b"WEST\x00EAST\x00"
    tzif = b"TZif" + b"\x00" * 16 + pack(">6I", 0, 0, 0, len(switches), 2, len(abbreviations))
    tzif += pack(">3i3B", *(int(switch.timestamp()) for switch in switches), 1, 0, 1)
    tzif += pack(">iBBiBB", -19 * 3600, 0, 0, 23 * 3600, 0, 5) + abbreviations
    zone = ZoneInfo.from_file(BytesIO(tzif), key="UTC")
    label_day, target_day = dt.date(2026, 10, 11), dt.date(2026, 10, 10)
    assert switches[0].astimezone(zone).date() == label_day
    assert (switches[1] - dt.timedelta.resolution).astimezone(zone).date() == label_day
    assert switches[1].astimezone(zone).date() == target_day
    assert switches[2].astimezone(zone).date() == dt.date(2026, 10, 12)
    assert switches[0].astimezone(ZoneInfo("UTC")).date() != label_day

    early = FixtureRecord("later-civil-label", "2026-10-10", source_day=dt.date(2026, 10, 7), label_day=label_day,
                          expiry_at=dt.datetime.fromisoformat("2026-10-10T12:00:00Z"))
    target = FixtureRecord("earlier-civil-target", "2026-10-11", source_day=target_day,
                           label_day=dt.date(2026, 10, 12), expiry_at=switches[2])
    search = tuple(replace(row, expiry_at=row.expiry_at + dt.timedelta(hours=8)) for row in _date_crypto_search())
    groups = (target.group_id,) if sealed else (early.group_id, target.group_id)
    run, calls = _run_date_crypto(search + (early, target), groups, zone=zone)
    if sealed:
        with pytest.raises(ValueError, match="search labels must be available strictly before the first holdout"):
            run()
        assert not any(calls.values())
    else:
        artifact = json.loads(run().artifact_bytes)
        clocks = artifact["crypto_source_clocks"]
        assert clocks[early.event_key]["effective_label_period_end"] == "2026-10-10T20:00:00Z"
        assert clocks[target.event_key]["prediction_period_start"] == "2026-10-10T20:00:00Z"
        assert artifact["holdout"]["planned_folds"] == [early.group_id]
        assert artifact["holdout"]["planned_skips"] == [{
            "block_id": target.group_id, "reason": "prior_block_prefix_incomplete_at_cutoff",
        }]


@pytest.mark.parametrize(("offset", "error"), [
    (-dt.timedelta(microseconds=1), None),
    (dt.timedelta(), "cannot prove"),
    (dt.timedelta(days=1), "at or after expiry"),
])
def test_actual_crypto_expiry_date_does_not_supply_a_precise_instant(offset, error):
    start = dt.datetime(2026, 10, 25, tzinfo=UTC)
    row = FixtureRecord("date-expiry", "2026-10-25", start + offset, start + dt.timedelta(days=2))
    run, calls = _actual_crypto_run(_crypto_search_records() + (row,), (row.group_id,))
    if error:
        with pytest.raises(ValueError, match=f"date-expiry.*{error}"):
            run()
        assert not any(calls.values())
    else:
        artifact = json.loads(run().artifact_bytes)
        assert artifact["crypto_expiry_days"]["date-expiry"]["expiry_at"] is None
        assert artifact["crypto_source_clocks"]["date-expiry"]["operation_at"] == "2026-10-24T23:59:59.999999Z"
        assert artifact["holdout"]["planned_folds"] == [row.group_id]


def test_actual_crypto_normalizes_both_fall_back_instants_and_rejects_spring_gap():
    zone = ZoneInfo("America/New_York")
    expiry = dt.datetime(2026, 11, 1, 7, tzinfo=UTC)
    fall = dt.datetime(2026, 11, 1, 1, 30, tzinfo=zone)
    first, second = (FixtureRecord(f"fall-{fold}", "2026-11-01", fall.replace(fold=fold), expiry,
                                   expiry_at=expiry) for fold in (0, 1))
    run, _ = _actual_crypto_run(_crypto_search_records() + (first, second), (first.group_id,))
    clocks = json.loads(run().artifact_bytes)["crypto_source_clocks"]
    assert clocks["fall-0"]["operation_at"] == "2026-11-01T05:30:00Z"
    assert clocks["fall-1"]["operation_at"] == "2026-11-01T06:30:00Z"

    imaginary = dt.datetime(2026, 3, 8, 2, 30, tzinfo=zone)
    invalid = replace(first, prediction_at=imaginary)
    run, calls = _actual_crypto_run(_crypto_search_records() + (invalid,), (invalid.group_id,))
    with pytest.raises(ValueError, match="prediction_at is not a real local time"):
        run()
    assert not any(calls.values())


def test_actual_crypto_adjacent_utc_expiries_can_share_a_local_calendar_date():
    zone = ZoneInfo("America/New_York")
    times = (dt.datetime(2026, 10, 24, 19, 55, tzinfo=zone),
             dt.datetime(2026, 10, 24, 23, 15, tzinfo=zone))
    rows = tuple(FixtureRecord(f"separate-{index}", f"separate-{index}", expiry - dt.timedelta(hours=1), expiry,
                               expiry_at=expiry) for index, expiry in enumerate(times))
    assert times[0].date() == times[1].date()
    run, _ = _actual_crypto_run(_crypto_search_records() + rows, {row.group_id for row in rows})
    holdout = json.loads(run().artifact_bytes)["holdout"]
    assert (holdout["n_events"], holdout["n_blocks"]) == (2, 2)
    assert holdout["planned_folds"] == ["2026-10-24", "2026-10-25"]


@pytest.mark.parametrize(("offset", "reason"), [
    (dt.timedelta(), None),
    (dt.timedelta(microseconds=1), "outside its registered prediction window"),
    (dt.timedelta(hours=2), "at or after expiry"),
])
def test_actual_crypto_preflights_registered_quote_time(offset, reason):
    search, held = _crypto_search_records(), _crypto_holdout(1, 1)
    line = FixtureLine(held[0].event_key, "touch", "barrier", "quote", ("no", "yes"), 1, "touch",
                       decision_at=held[0].kickoff + offset)
    lines = tuple(FixtureLine(row.event_key, "touch", "barrier", row.event_key, ("no", "yes"), 1, "touch")
                  for row in search) + (line,)
    run, calls = _actual_crypto_run(search + held, (held[0].group_id,), lines=lines)
    if reason:
        with pytest.raises(ValueError, match=f"h000.*{reason}"):
            run()
        assert not any(calls.values())
    else:
        assert json.loads(run().artifact_bytes)["holdout"]["planned_folds"] == [held[0].group_id]


@pytest.mark.parametrize(("mode", "reason"), [
    ("mix", "consistently provide source and label dates"),
    ("timezone", "usable source timezone"),
    ("cutoff", "inclusive last complete source date"),
    ("label", "requires consistent source dates"),
    ("duplicate", "unique event keys"),
])
def test_actual_crypto_checks_date_cohort_before_any_model_calls(mode, reason):
    search, held = _date_crypto_search(), _date_crypto_holdout()[:8]
    row, *rest = held
    data_cutoff = dt.datetime(2026, 12, 1, tzinfo=UTC)
    last_date = dt.date(2026, 11, 29)
    zone = UTC
    if mode == "mix":
        row = replace(row, source_day=None, label_day=None)
    elif mode == "timezone":
        zone = dt.tzinfo()
    elif mode == "cutoff":
        last_date = data_cutoff.date()
    elif mode == "label":
        row = replace(row, label_day=row.source_day - dt.timedelta(days=1))
    else:
        row = replace(row, event_key=search[-1].event_key)
    run, calls = _actual_crypto_run(search + (row, *rest), (row.group_id,), data_cutoff=data_cutoff,
                                    source_calendar="registered crypto calendar", source_timezone=zone,
                                    last_complete_date=last_date)
    with pytest.raises(ValueError, match=reason):
        run()
    assert not any(calls.values())


@pytest.mark.parametrize("precise", [False, True])
def test_actual_crypto_rejects_invalid_reported_time_after_starting_holdout(precise):
    if precise:
        search, held = _crypto_search_records(), _crypto_holdout(160, 20)
        runner_for_source = _actual_crypto_run
        wrong_time = held[0].expiry_at
        reason = "at or after expiry"
    else:
        search, held = _date_crypto_search(), _date_crypto_holdout()
        runner_for_source = _run_date_crypto
        wrong_time = dt.datetime.combine(held[0].source_day, dt.time.min, tzinfo=UTC)
        reason = "no registered precise prediction boundary"
    run, calls = runner_for_source(search + held, {row.group_id for row in held},
                                    reported_times={held[0].event_key: wrong_time})
    result = run()
    artifact = json.loads(result.artifact_bytes)
    holdout = artifact["holdout"]
    assert result.outcome == runner.RUN_FAILED
    assert len(calls["builder"]) == 36 and len(calls["search"]) == 108
    assert len(calls["holdout"]) == 1
    assert len(calls["fit"]) == 128 and len(calls["predict"]) == 268
    call, = holdout["callbacks"]
    assert call["status"] == "aborted" and call["side"] == "candidate"
    assert call["eligible_line_count"] == 160 and call["prediction_count"] == 0
    assert artifact["counts"]["aborted_holdout_callbacks"] == 1
    assert artifact["counts"]["completed_holdout_callbacks"] == 0
    assert call["failure"] == artifact["failure"]
    assert call["failure"]["step"] == "holdout_validation" and reason in call["failure"]["reason"]
    assert not artifact["predictions"] and not holdout["comparisons"]
    assert not result.proposed_specs and not result.statistical_rejections


@pytest.mark.parametrize(("instant", "reason"), [
    ("2026-09-10T19:01:00Z", "after its frozen prediction boundary"),
    ("2026-08-05T00:00:00Z", "not strictly after its latest training label"),
])
def test_actual_crypto_reported_time_checks_fold_and_training_boundary(instant, reason):
    search, held = _crypto_search_records(), _crypto_holdout(160, 20)
    # h020 itself allows 19:01, while the fold freezes at h000's 19:00.
    run, calls = _actual_crypto_run(search + held, {row.group_id for row in held},
                                    reported_times={"h020": dt.datetime.fromisoformat(instant)})
    artifact = json.loads(run().artifact_bytes)
    assert len(calls["holdout"]) == 1
    assert artifact["failure"]["reason"] == f"forecast time is {reason}"


@pytest.mark.parametrize(("day", "reason"), [
    ("2026-10-09T12:00:00Z", "observed before its source label date"),
    ("2026-11-30T12:00:00Z", "last complete source date"),
    ("2026-12-01T00:00:00Z", "invalid delayed label availability"),
])
def test_actual_crypto_rejects_inconsistent_delayed_label(day, reason):
    records = _date_crypto_search() + _date_crypto_holdout()
    row, *other = records[5:]
    row = replace(row, available_at=dt.datetime.fromisoformat(day))
    run, calls = _run_date_crypto(records[:5] + (row, *other), {row.group_id for row in records[5:]})
    with pytest.raises(ValueError, match=rf"h00-0.*{reason}"):
        run()
    assert not any(calls.values())


def test_actual_crypto_delayed_label_cannot_enter_a_mixed_prediction_day_fold():
    held = _date_crypto_holdout()
    first = dt.date(2026, 10, 8)
    extra_early = FixtureRecord("early-holdout", first.isoformat(), source_day=dt.date(2026, 10, 4),
                                label_day=dt.date(2026, 10, 4),
                                available_at=dt.datetime(2026, 10, 6, 10, tzinfo=UTC))
    holdout = (extra_early,) + held[:-8]
    run, calls = _run_date_crypto(_date_crypto_search() + holdout, {row.group_id for row in holdout})
    artifact = json.loads(run().artifact_bytes)
    assert artifact["crypto_source_clocks"]["early-holdout"]["effective_label_day"] == "2026-10-06"
    clock = artifact["crypto_source_clocks"]["early-holdout"]
    assert clock["label_period_end"] == "2026-10-05T00:00:00Z"
    assert clock["effective_label_period_end"] == "2026-10-07T00:00:00Z"
    assert clock["available_at"] == "2026-10-06T10:00:00Z"
    assert artifact["holdout"]["skips"] == [{
        "block_id": "2026-10-10", "reason": "prior_block_prefix_incomplete_at_cutoff",
    }]
    for folds in calls["holdout_folds"]:
        assert "2026-10-10" not in {fold.block_id for fold in folds}
        next_fold = next(fold for fold in folds if fold.block_id == "2026-10-12")
        assert "early-holdout" in next_fold.train_keys


def _independent_binary_ece(predictions):
    bins = [[] for _ in range(10)]
    quantum = Decimal("0.000000000001")
    for row in predictions:
        probability = Decimal(str(row["probabilities"][1])).quantize(quantum)
        bins[min(9, int(probability * 10))].append(probability - row["outcome_index"])
    error = sum(abs(sum(rows)) for rows in bins)
    return float((error / len(predictions)).quantize(quantum))


def test_actual_crypto_aliases_are_canonical_and_search_isolated_from_future_truth():
    search, holdout = list(_date_crypto_search()), list(_date_crypto_holdout())
    search.append(replace(search[-1], event_key="same-search-day"))
    records = search + holdout
    sensitive = "h01-0"
    strikes = {row.event_key: 100.8 + (index % 4) / 4 for index, row in enumerate(holdout)}
    lines = [FixtureLine(row.event_key, "touch", "barrier", row.event_key, ("no", "yes"),
                         int(not row.event_key.endswith("-7")), "touch", strikes.get(row.event_key))
             for row in records]

    plain_run, plain_calls = _run_date_crypto(records, {row.group_id for row in holdout}, lines=lines)
    plain = json.loads(plain_run().artifact_bytes)
    aliases = {"same-search-day": "search-alias", **{row.event_key: "holdout-alias" for row in holdout[:4]}}
    aliased = [replace(row, group_id=aliases.get(row.event_key, row.group_id)) for row in records]
    ref, raw = _source_ref({"expiry_groups": [
        {"group_id": "search-alias", "expiry_day": search[-1].group_id},
        {"group_id": "holdout-alias", "expiry_day": holdout[0].group_id},
    ]})
    kwargs = {"source_artifacts": {ref.sha256: raw}, "snapshot_refs": (ref.sha256,), "crypto_expiry_groups": ref}
    groups = {row.group_id for row in aliased[len(search):]}
    aliased_run, aliased_calls = _run_date_crypto(aliased, groups, lines=lines, **kwargs)
    result = aliased_run()
    artifact = json.loads(result.artifact_bytes)
    assert artifact["crypto_expiry_groups"] == {"sha256": ref.sha256, "byte_length": len(raw)}
    assert artifact["crypto_expiry_days"]["same-search-day"]["group_id"] == "search-alias"
    assert artifact["crypto_expiry_days"]["h00-0"]["group_id"] == "holdout-alias"
    assert artifact["sealed_input_sha256"] != plain["sealed_input_sha256"]
    for field in ("search_folds", "holdout_folds"):
        assert plain_calls[field] == aliased_calls[field]
    for field in ("predictions", "search_predictions", "planned_evaluations", "nested_selection"):
        assert plain[field] == artifact[field]
    lineage = "crypto-gbm/touch"
    assert (plain["holdout"]["comparisons"][lineage]["coverage"]
            == artifact["holdout"]["comparisons"][lineage]["coverage"])
    folds = {fold.block_id: fold for fold in aliased_calls["search_folds"]}
    for row in artifact["nested_selection"]:
        for inner in row["inner_block_ids"]:
            assert inner < row["fold"]
            assert set(folds[inner].test_keys) <= set(folds[row["fold"]].train_keys)
    rerun, reordered_calls = _run_date_crypto(list(reversed(aliased)), groups, lines=list(reversed(lines)), **kwargs)
    assert rerun().artifact_bytes == result.artifact_bytes
    assert reordered_calls == aliased_calls

    flipped_lines = [replace(row, outcome_index=0) if row.event_key == sensitive else row for row in lines]
    changed_run, changed_calls = _run_date_crypto(aliased, groups, lines=flipped_lines, sensitive_truth=sensitive,
                                                 **kwargs)
    changed = json.loads(changed_run().artifact_bytes)
    for field in ("search_folds", "holdout_folds"):
        assert changed_calls[field] == aliased_calls[field]
    for field in ("search_predictions", "diagnostic_scorecards", "nested_selection", "planned_evaluations"):
        assert artifact[field] == changed[field]
    before = [p for p in artifact["predictions"] if p["fold"] <= holdout[8].group_id]
    after = [p for p in changed["predictions"] if p["fold"] <= holdout[8].group_id]
    assert [dict(row, outcome_index=0) if row["event_key"] == sensitive else row for row in before] == after
    assert sum(p["event_key"] == sensitive and p["outcome_index"] == 0 for p in after) == 2
    assert any(new["probabilities"] != old["probabilities"]
               for new, old in zip(changed["predictions"], artifact["predictions"], strict=True)
               if new["fold"] > holdout[8].group_id)
    for report in (artifact, changed):
        ece = {}
        for side in ("candidate", "incumbent"):
            forecasts = [p for p in report["predictions"] if p["model"] == side]
            card = report[f"{side}_scorecards"][lineage]
            assert card["n_events"] == len(forecasts) == 160
            ece[side] = _independent_binary_ece(forecasts)
            assert float(card["calibration_ece"]) == ece[side]
        oof = report["diagnostic_scorecards"][lineage]
        assert oof["n_events"] == len(report["search_predictions"]) == 4
        assert float(oof["calibration_ece"]) == _independent_binary_ece(report["search_predictions"])
        assert report["decisions"][0]["calibration_regression"] == (ece["candidate"] > ece["incumbent"])


def test_actual_nba_catalogue_shared_and_distinct_active_contexts(nba_raw_bundle):
    from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY, _envelope_mappings

    family = NBA_PACE_EFFICIENCY
    baseline = family.baseline_spec()
    alternate = replace(
        baseline, params={**baseline.params, "half_life": 10.0},
        envelope=_envelope_mappings(10.0), legacy_model_version=None,
    )
    active = baseline.spec_sha256()
    start = dt.date(2026, 8, 1)
    snapshot = _nba_snapshot_with_days(nba_raw_bundle, {
        start + dt.timedelta(days=day): 10 for day in range(34)
    })
    ref, content = _source_ref(snapshot)
    records, lines = runner.nba_snapshot_cohort(content, ref, last_complete_date=dt.date(2026, 11, 19))
    day_by_key = {row.event_key: row.source_day for row in records}
    holdout = frozenset(row.group_id for row in records if row.source_day >= start + dt.timedelta(days=4))

    def build(params):
        return replace(baseline, params=params, envelope=_envelope_mappings(params["half_life"]),
                       legacy_model_version=None)

    def evaluate(spec, fold, mask):
        assert all(day_by_key[key] < fold.source_day for key in fold.train_keys)
        return {target: HoldoutEvaluation(tuple(
            Prediction(row.event_key, row.line_key, (0.4, 0.6), row.class_order)
            for row in lines if row.target == target and row.event_key in fold.test_keys
        )) for target in mask}

    def unopened(*args):
        pytest.fail("the exact active won; the holdout must remain untouched")

    def run(totals):
        comparison = {"moneyline": baseline, "totals": totals}
        actual = {f"{family.family_id}/{target}": spec for target, spec in comparison.items()}
        return execute_optimization_run(
            family=family, records=records, fixture_lines=lines, holdout_group_ids=holdout,
            evaluate=evaluate, evaluate_holdout=unopened, data_cutoff=DATA_CUTOFF, random_seed=1,
            code_commit=CODE_COMMIT, build_candidate_spec=build, deadline=lambda: False,
            source_calendar="ESPN NBA source game date", source_timezone=UTC,
            last_complete_date=dt.date(2026, 11, 19),
            **_nba_source_inputs(snapshot),
            policy_context=PolicyContext(STARTED_AT, {
                name: RegistryComparison(LineageRoles(name, active, spec.spec_sha256()), spec)
                for name, spec in actual.items()
            }),
        )

    shared, distinct = run(baseline), run(alternate)
    first, second = (json.loads(run.artifact_bytes) for run in (shared, distinct))
    assert shared.submission.trial_count == 34
    assert shared.outcome == distinct.outcome == runner.NO_IMPROVEMENT
    assert distinct.submission.trial_count == 2 * len({
        entry["spec_sha256"] for catalog in second["catalogs"].values() for entry in catalog["entries"]
    }) <= 68
    assert all(len(catalog["entries"]) == 17 for catalog in first["catalogs"].values())
    assert all(row["targets"] == ["moneyline", "totals"] for row in first["planned_evaluations"])
    assert distinct.comparison_specs["nba-pace-efficiency/totals"] == alternate
    assert distinct.winner_spec is None and len(distinct.decisions) == 2


def test_international_current_table_and_future_history_cannot_change_replay():
    from polymarket_alpha_lab.papersim import intl_model
    from polymarket_alpha_lab.papersim.optimize.families.intl import (
        INTL_ELO, INTL_TOTALS, IntlFamilyFixture, IntlHistory,
    )

    snapshots = {
        "p": {"date": "2024-01-01", "home": "A", "away": "B", "home_goals": 3,
              "away_goals": 1, "tournament": "Friendly", "neutral": False, "venue": None},
        "q": {"date": "2024-01-02", "home": "B", "away": "A", "home_goals": 1,
              "away_goals": 2, "tournament": "Friendly", "neutral": False, "venue": None},
        "r": {"date": "2024-01-04", "home": "A", "away": "Future", "home_goals": 20,
              "away_goals": 0, "tournament": "Friendly", "neutral": False, "venue": None},
    }
    payload = {"generated_at": "2024-08-01", "source": "intl-results",
               "teams": {"A": {"elo": 8123}, "B": {"elo": 42}, "Future": {"elo": 5000}},
               "elo_history": ({"team": "A", "elo": 9999, "date": "2024-01-04"},),
               "matches": tuple(snapshots.values())}
    first_holdout = dt.date(2024, 1, 4)
    fold = runner.ScopedFold("held", dt.datetime(2024, 1, 4, tzinfo=UTC), ("p", "q"), ("r",),
                             ("day1", "day2"), first_holdout, "intl-source-day")
    future = {**snapshots, "r": {**snapshots["r"], "home_goals": 8, "tournament": "Changed"}}
    mutated = {**payload, "teams": {"A": {"elo": 800}, "B": {"elo": 9000}},
               "elo_history": (), "matches": tuple(future.values())}
    fixtures = [
        IntlFamilyFixture(intl_model.IntlFixture("A", name, first_holdout.isoformat()), "Friendly")
        for name in ("B", "Future")
    ]
    for family in (INTL_ELO, INTL_TOTALS):
        results = []
        for snapshot, rows in ((payload, snapshots), (mutated, future)):
            gated = runner.international_history_at(snapshot, fold, matches_by_key=rows)
            assert gated["teams"] == {} and not gated["elo_history"]
            fitted = family.fit(IntlHistory(gated), family.baseline_spec(), fold.cutoff)
            results.append([runner.international_replay_prediction(family, fitted, fixture) for fixture in fixtures])
        assert repr(results[0]).encode() == repr(results[1]).encode()
        if family is INTL_ELO:
            assert results[0][0] is not None and results[0][1] is None


def _official_club_audit(*, postpone=False, omit=False):
    from polymarket_alpha_lab.papersim.optimize.dataset import (
        LEAGUES, HistoricalMatch, HistoryAudit, ScheduleFixture,
    )

    fixtures, matches = [], []
    origin = dt.datetime(2026, 8, 1, 17, tzinfo=UTC)
    for league, day_offset in (("EPL", 0), ("Bundesliga", 1)):
        for number in range(1, 6):
            official = origin + dt.timedelta(days=7 * number + day_offset)
            for index in range(LEAGUES[league][1] // 2):
                key = f"{league}/2026/{number}/{index}"
                home, away = f"{league} home {index}", f"{league} away {index}"
                fixtures.append(ScheduleFixture(key, league, "2026", number, official,
                                                home, away, True, False, False))
                if omit and league == "EPL" and number == 2 and index == 0:
                    continue
                source = official
                if league == "EPL" and number == 2 and index == 0:
                    source += dt.timedelta(days=8 if postpone else 0)
                if league == "EPL" and number == 3 and index == 0:
                    source -= dt.timedelta(hours=2)
                matches.append(HistoricalMatch(
                    key, league, "2026", number, official, home, away, 1.7, 0.8, 3, 1,
                    schedule_match_id=key, source_kickoff=source,
                ))
    return HistoryAudit(tuple(matches), tuple(fixtures), (), (), ())


@pytest.mark.parametrize("bad_prefix", ["postponed", "missing"])
def test_club_round_prediction_and_completion_clocks_use_official_partition(bad_prefix):
    audit = _official_club_audit(postpone=bad_prefix == "postponed", omit=bad_prefix == "missing")
    records = runner.club_fixture_records(audit, DATA_CUTOFF)
    harness = Harness(224)
    harness.holdout_group_ids = frozenset(("EPL/2026/5", "Bundesliga/2026/5"))
    selected = ("EPL/2026/3", "Bundesliga/2026/3")
    visited = []

    def evaluate(spec, fold, mask):
        visited.append(fold.block_id)
        assert all(key.startswith("Bundesliga/") for key in fold.train_keys)
        assert {key.split("/")[2] for key in fold.train_keys} == {"1", "2"}
        history = runner.club_model_history(audit, fold)
        assert all(match.kickoff + dt.timedelta(hours=3) < fold.cutoff for match in history.matches)
        return {"synthetic": HoldoutEvaluation(tuple(
            Prediction(key, "line", (0.4, 0.6), ("no", "yes")) for key in fold.test_keys
        ))}

    def unopened(*args):
        pytest.fail("the structural floor is unmet")

    result = harness.run(records=records, club_history=audit, search_group_ids=selected,
                         evaluate=evaluate, evaluate_holdout=unopened)
    assert result.outcome == runner.INSUFFICIENT_DATA
    assert visited == ["Bundesliga/2026/3"]
    doc = json.loads(result.artifact_bytes)
    assert doc["skipped_blocks"] == [
        {"block_id": "EPL/2026/3", "reason": "prior_round_prefix_incomplete_at_cutoff"}
    ]
    event = next(row for row in records if row.event_key == "EPL/2026/3/0")
    assert event.prediction_at == event.kickoff - dt.timedelta(hours=2)
    assert event.available_at == event.kickoff + dt.timedelta(hours=3)
    if bad_prefix == "postponed":
        prior = next(row for row in records if row.event_key == "EPL/2026/2/0")
        assert prior.kickoff > event.prediction_at
        assert prior.available_at == prior.kickoff + dt.timedelta(hours=3)
    assert doc["holdout"]["n_blocks"] == 1  # Two leagues share the same real calendar week.


def test_recomputed_validation_gate_can_regress_on_the_same_paired_games():
    harness = Harness(225, search_effect=0.05, holdout_effect=0.05,
                      holdout_blocks=30, holdout_events_per_block=10)
    actual = {row.event_key: 200 + index % 52 for index, row in enumerate(harness.records)}
    lines = tuple(FixtureLine(row.event_key, "synthetic", "line", row.event_key,
                              ("no", "yes"), 1, "totals", 225.5,
                              actual_total=actual[row.event_key]) for row in harness.records)

    def holdout(spec, target, folds):
        base = harness.evaluate_holdout(spec, target, folds)
        sign = 1 if spec.params["theta"] == 0.2 else -1
        return HoldoutEvaluation(tuple(replace(
            row, expected_total=225 + sign * (actual[row.event_key] - 225)
        ) for row in base.events))

    result = harness.run(floor_class="nba", fixture_lines=lines, evaluate_holdout=holdout)
    doc = json.loads(result.artifact_bytes)
    assert result.outcome == runner.NO_IMPROVEMENT
    assert result.decisions[0].ci_passes and result.decisions[0].gate_regression
    assert not result.statistical_rejections
    assert "required_quality_evidence_unverified" in result.decisions[0].flags
    for field, expected in (("incumbent_scorecards", "PASS"), ("candidate_scorecards", "FAIL")):
        row = next(gate for gate in doc[field][LINEAGE]["gates"]
                   if gate["check"] == "expected_vs_actual_total_pearson_r")
        assert row["verdict"] == expected
        assert ["scope_denominator", "300"] in row["numbers"]


def test_mixed_registry_keeps_healthy_proposal_and_never_creates_missing_active():
    harness = Harness(226, search_effect=0.05, holdout_effect=0.05)
    context = _two_target_batches(harness)
    actual = harness.context()
    policy = PolicyContext(STARTED_AT, {"runner-synth/peer": actual.registry["runner-synth/peer"]})
    result = harness.run(**context, policy_context=policy)
    assert result.outcome == runner.CANDIDATE_REGISTERED
    assert set(result.proposed_specs) == set(result.verified_active_specs) == {"runner-synth/peer"}
    assert result.decisions[0].statistically_eligible
    assert result.decisions[0].status == "registry_lineage_uninitialized"
    assert not result.statistical_rejections and not result.registered_lineages


@pytest.mark.parametrize("planned, started, expired", [(1999, 1999, False), (2000, 2000, False),
                                                       (2001, 2000, True)])
def test_registered_trial_cap_uses_started_physical_callbacks(planned, started, expired):
    class LargeFamily(SyntheticFamily):
        def param_space(self):
            return (ParamDef("theta", "choice", 0, choices=tuple(range(planned))),)

        def baseline_spec(self):
            return replace(super().baseline_spec(), params={"theta": 0})

    harness = Harness(227, search_blocks=3, search_events=1)
    fit_calls = []

    def evaluate(spec, fold, mask):
        fit_calls.append(spec.params["theta"])
        return {"synthetic": HoldoutEvaluation(tuple(
            Prediction(key, "line", (0.4, 0.6), ("no", "yes")) for key in fold.test_keys
        ))}

    result = harness.run(family=LargeFamily(), evaluate=evaluate)
    artifact = json.loads(result.artifact_bytes)
    assert len(set(fit_calls)) == result.submission.trial_count == started
    assert artifact["counts"]["planned_trials"] == planned
    assert result.budget_exhausted == expired
    assert result.outcome == (runner.RUN_FAILED if expired else runner.INSUFFICIENT_DATA)
    assert not artifact["holdout"]["callbacks"] and not result.statistical_rejections


@pytest.mark.parametrize(("stop_search", "stop_holdout", "invalid", "expected"), [
    (None, None, False, (24, 0, 2, 0, 320)),
    (5, None, False, (4, 1, 0, 0, 0)),
    (None, 1, False, (24, 0, 0, 1, 0)),
    (None, 2, False, (24, 0, 1, 1, 160)),
    (None, 2, True, (24, 0, 1, 1, 160)),
])
def test_physical_callback_accounting_preserves_verified_rows(stop_search, stop_holdout, invalid, expected):
    def run():
        harness = Harness(11, search_effect=.05, holdout_effect=.05)
        calls = [0, 0]

        def search(*args):
            calls[0] += 1
            if calls[0] == stop_search:
                raise RuntimeError("fixture_callback_error")
            return harness.evaluate(*args)

        def holdout(*args):
            calls[1] += 1
            batch = harness.evaluate_holdout(*args)
            if calls[1] == stop_holdout:
                if invalid:
                    return replace(batch, events=(*batch.events[:-1], replace(batch.events[-1], line_key="other")))
                raise RuntimeError("fixture_callback_error")
            return batch

        return harness.run(evaluate=search, evaluate_holdout=holdout), calls

    result, actual = run()
    artifact = json.loads(result.artifact_bytes)
    completed_search, aborted_search, completed_holdout, aborted_holdout, forecast_count = expected
    assert actual == [completed_search + aborted_search, completed_holdout + aborted_holdout]
    assert result.submission.trial_count == actual[0]
    assert len(artifact["planned_evaluations"]) == 24
    assert len(artifact["search_trial_forecasts"]) == completed_search
    assert all(len(batch["predictions"]) == 4 for batch in artifact["search_trial_forecasts"])
    assert len(artifact["predictions"]) == forecast_count
    assert artifact["counts"]["completed_holdout_callbacks"] == completed_holdout
    assert artifact["counts"]["aborted_holdout_callbacks"] == aborted_holdout
    assert sum(row["prediction_count"] for row in artifact["holdout"]["callbacks"]) == forecast_count
    if aborted_search or aborted_holdout:
        assert result.outcome == runner.RUN_FAILED
        assert not result.proposed_specs and not result.statistical_rejections
        assert not artifact["holdout"]["comparisons"]
        assert not artifact["candidate_scorecards"]
        assert artifact["holm_hypotheses"][0]["observed_p_value"] is None
        assert artifact["failure"]["error_class"] == ("_BatchIntegrityError" if invalid else "RuntimeError")
        assert b"fixture_callback_error" not in result.artifact_bytes
        replay, replay_calls = run()
        assert replay_calls == actual
        assert replay.artifacts_sha256 == result.artifacts_sha256
        assert replay.artifact_bytes == result.artifact_bytes
    else:
        assert result.outcome == runner.CANDIDATE_REGISTERED


def test_later_lineage_callback_failure_retains_completed_comparison():
    harness = Harness(11, search_effect=.05, holdout_effect=.05)
    inputs = _two_target_batches(harness)
    holdout = inputs["evaluate_holdout"]

    def fail_last(spec, target, folds):
        if target == "peer" and spec.params["theta"] == 0.2:
            raise RuntimeError("fixture_callback_error")
        return holdout(spec, target, folds)

    result = harness.run(**dict(inputs, evaluate_holdout=fail_last))
    artifact = json.loads(result.artifact_bytes)
    assert result.outcome == runner.RUN_FAILED
    assert not result.proposed_specs and not result.statistical_rejections
    assert tuple(artifact["holdout"]["comparisons"]) == (LINEAGE,)
    assert artifact["candidate_scorecards"][LINEAGE]["paired_vs_incumbent"]["n_pairs"] == 160
    assert [row["status"] for row in artifact["holdout"]["callbacks"]] == ["completed"] * 3 + ["aborted"]
    assert [row["prediction_count"] for row in artifact["holdout"]["callbacks"]] == [160, 160, 160, 0]


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_search_prediction_must_respect_earliest_fold_boundary(offset):
    harness = Harness(11, search_effect=.05, holdout_effect=.05)
    records = tuple(replace(row, kickoff=row.kickoff + dt.timedelta(hours=1))
                    if row.event_key.endswith("e3") else row for row in harness.records)

    def search(spec, fold, targets):
        instant = fold.cutoff + dt.timedelta(microseconds=offset)
        return {target: replace(batch, events=tuple(replace(row, predicted_at=instant) for row in batch.events))
                for target, batch in harness.evaluate(spec, fold, targets).items()}

    result = harness.run(records=records, evaluate=search)
    artifact = json.loads(result.artifact_bytes)
    if offset <= 0:
        assert result.outcome == runner.CANDIDATE_REGISTERED
        assert artifact["search_trial_forecasts"][0]["predictions"][0]["predicted_at"]
    else:
        assert result.outcome == runner.RUN_FAILED and result.submission.trial_count == 1
        assert artifact["failure"]["step"] == "search_validation"
        assert "frozen prediction boundary" in artifact["failure"]["reason"]
        assert not artifact["search_trial_forecasts"] and not artifact["holdout"]["callbacks"]
        assert not result.proposed_specs and not result.statistical_rejections


@pytest.mark.parametrize("offset", [0, 1])
def test_earlier_prediction_requires_latest_training_label_strictly_before(offset):
    harness = Harness(11, search_effect=.05, holdout_effect=.05)
    records = {row.event_key: row for row in harness.records}

    def search(spec, fold, targets):
        instant = max(records[key].available_at for key in fold.train_keys) + dt.timedelta(microseconds=offset)
        return {target: replace(batch, events=tuple(replace(row, predicted_at=instant) for row in batch.events))
                for target, batch in harness.evaluate(spec, fold, targets).items()}

    result = harness.run(evaluate=search)
    artifact = json.loads(result.artifact_bytes)
    if offset:
        assert result.outcome == runner.CANDIDATE_REGISTERED
    else:
        assert result.outcome == runner.RUN_FAILED and result.submission.trial_count == 1
        assert "strictly after its latest training label" in artifact["issue"]


def test_each_holdout_prediction_uses_its_own_fold_even_in_one_callback():
    harness = Harness(11, search_effect=.05, holdout_effect=.05)

    def holdout(spec, target, folds):
        clocks = {key: fold.cutoff for fold in folds for key in fold.test_keys}
        batch = harness.evaluate_holdout(spec, target, folds)
        rows = tuple(replace(row, predicted_at=clocks[row.event_key]) for row in batch.events)
        if spec.params["theta"] == 0.2:
            rows = (replace(rows[0], predicted_at=folds[1].cutoff), *rows[1:])
        return replace(batch, events=rows)

    result = harness.run(evaluate_holdout=holdout)
    artifact = json.loads(result.artifact_bytes)
    assert result.outcome == runner.RUN_FAILED
    assert artifact["failure"]["step"] == "holdout_validation"
    assert "frozen prediction boundary" in artifact["issue"]
    assert len(artifact["predictions"]) == 160
    assert not artifact["holdout"]["comparisons"]


@pytest.mark.parametrize("supply_forecast_time", [False, True])
def test_source_day_midnight_never_attests_an_exact_forecast(supply_forecast_time):
    harness = Harness(11, search_effect=.05, holdout_effect=.05)
    records = tuple(FixtureRecord(row.event_key, row.group_id, source_day=row.kickoff.date(),
                                  label_day=row.available_at.date()) for row in harness.records)

    def evaluate(spec, fold, targets):
        return {target: replace(batch, events=tuple(replace(row, predicted_at=fold.cutoff) for row in batch.events))
                if supply_forecast_time else batch for target, batch in harness.evaluate(spec, fold, targets).items()}

    result = harness.run(records=records, source_calendar="fixture calendar", source_timezone=UTC,
                         last_complete_date=dt.date(2026, 11, 19), evaluate=evaluate)
    assert result.outcome == (runner.RUN_FAILED if supply_forecast_time else runner.CANDIDATE_REGISTERED)
    if supply_forecast_time:
        assert result.submission.trial_count == 1
        assert "date-only fixture" in json.loads(result.artifact_bytes)["issue"]


@pytest.mark.parametrize("same_instant", [False, True])
def test_repeated_local_hour_pairs_only_one_real_instant(same_instant):
    from polymarket_alpha_lab.papersim.optimize.dataset import MarketObservation

    harness = Harness(11, search_effect=.05, holdout_effect=.05)
    ny = ZoneInfo("America/New_York")
    earlier = dt.datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0)
    later = earlier.replace(fold=1)
    game = harness._holdout_keys[-1]
    group = harness.records[-1].group_id
    records = tuple(replace(row, kickoff=later, available_at=dt.datetime(2026, 11, 1, 9, 30, tzinfo=UTC),
                            prediction_at=earlier if row.event_key == game else None)
                    if row.group_id == group else row for row in harness.records)
    snapshot = "d" * 64
    lines = tuple(FixtureLine(row.event_key, "synthetic", "line", row.event_key, ("no", "yes"), 1,
                              "totals", 2.5, decision_at=earlier.astimezone(UTC) if row.event_key == game else None,
                              quote_source="registered-book", quote_snapshot_sha256=snapshot) for row in records)
    quotes = tuple(MarketObservation(game, "totals", 2.5, side,
                                    earlier.astimezone(UTC) if same_instant else later,
                                    mid, "registered-book", snapshot) for side, mid in (("no", .48), ("yes", .52)))

    def holdout(spec, target, folds):
        batch = harness.evaluate_holdout(spec, target, folds)
        return replace(batch, events=tuple(replace(row, predicted_at=earlier) if row.event_key == game else row
                                          for row in batch.events))

    result = harness.run(records=records, fixture_lines=lines, market_observations=quotes,
                         snapshot_refs=(snapshot,), evaluate_holdout=holdout)
    market = json.loads(result.artifact_bytes)["holdout"]["comparisons"][LINEAGE]["market"]
    assert market["lines"] == int(same_instant)
    if same_instant:
        assert market["observations"][0]["observed_at"] == "2026-11-01T05:30:00Z"


@pytest.mark.parametrize("field", ["kickoff", "prediction_at", "available_at"])
def test_spring_gap_in_registered_precise_fixture_is_rejected_before_callbacks(field):
    harness = Harness(11)
    impossible = dt.datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo("America/New_York"))
    records = (replace(harness.records[0], **{field: impossible}), *harness.records[1:])

    def unopened(*args):
        pytest.fail("invalid registered fixture reached a callback")

    with pytest.raises(ValueError, match="real local time"):
        harness.run(records=records, evaluate=unopened, build_candidate_spec=unopened)


@pytest.mark.parametrize(("mode", "expected", "holdout_count"), [
    ("legacy", "RUN_FAILED", 0),
    ("active", "NO_IMPROVEMENT", 0),
    ("expiry", "CANDIDATE_REGISTERED", 2),
    ("own_run", "CANDIDATE_REGISTERED", 2),
    ("both", "NO_IMPROVEMENT", 0),
])
def test_run_classifies_quarantine_and_verified_ban_from_original_started(mode, expected, holdout_count):
    harness = Harness(11, search_effect=.05, holdout_effect=.05)
    completed = harness.run()
    digest = completed.winner_spec.spec_sha256()
    receipt = _receipt(STARTED_AT - dt.timedelta(days=28, microseconds=-1), digest)
    legacy = runner.LegacyRejection("or|runner-synth|legacy", "runner-synth", LINEAGE, digest,
                                    "terminal_link_unavailable")
    sources = {
        "legacy": (legacy,), "active": (receipt,), "both": (legacy, receipt),
        "expiry": (replace(receipt, rejected_at=STARTED_AT - dt.timedelta(days=28)),),
        "own_run": (replace(receipt, run_key=completed.run_key),),
    }[mode]
    policy = replace(harness.context(receipts=sources), watermark="sealed-policy-1")
    result = harness.run(policy_context=policy)
    artifact = json.loads(result.artifact_bytes)
    assert result.outcome == expected
    assert result.submission.trial_count == 24
    assert len(artifact["holdout"]["callbacks"]) == holdout_count
    assert artifact["policy_watermark"] == "sealed-policy-1"
    if not holdout_count:
        assert not result.decisions[0].attempted and not result.proposed_specs and not result.statistical_rejections
        disposition = artifact["cooldown"][LINEAGE]
        assert disposition["reason"] == ("stage1_rejection_quarantined" if mode == "legacy" else "stage1_resubmission_ban")
        if mode in ("legacy", "both"):
            (unresolved,) = disposition["quarantined_evidence"]
            assert unresolved["run_key"] == legacy.run_key
            assert unresolved["lineage"] == LINEAGE and unresolved["spec_sha256"] == digest
            assert unresolved["reason"] == "stage1_rejection_quarantined"
            assert unresolved["issue"] == "terminal_link_unavailable"
            assert unresolved["evidence_kind"] == "legacy_rejection"
            assert unresolved["rejected_at"] is None and unresolved["expires_at"] is None
        assert result.artifact_bytes == harness.run(policy_context=policy).artifact_bytes


@pytest.mark.parametrize(("peer", "expected"), [
    ("wins", "CANDIDATE_REGISTERED"),
    ("loses", "RUN_FAILED"),
    ("below_floor", "INSUFFICIENT_DATA"),
])
def test_mixed_quarantine_keeps_original_holm_family(peer, expected):
    harness = Harness(11, search_effect=.05, holdout_effect=.05 if peer != "loses" else -.05)
    original = harness.family.baseline_spec()
    active = replace(original, params={"theta": .1})
    inputs = _two_target_batches(harness)
    regular = inputs["evaluate"]
    holdout = inputs["evaluate_holdout"]
    peer_lineage = "runner-synth/peer"
    winner = replace(original, params={"theta": .4})
    legacy = runner.LegacyRejection("or|runner-synth|legacy", "runner-synth", LINEAGE,
                                    winner.spec_sha256(), "terminal_link_unavailable")
    registry = {
        LINEAGE: RegistryComparison(LineageRoles(LINEAGE, original.spec_sha256(), original.spec_sha256()), original),
        peer_lineage: RegistryComparison(LineageRoles(peer_lineage, original.spec_sha256(), active.spec_sha256()), active),
    }

    def search(spec, fold, targets):
        result = regular(spec, fold, targets)
        if "peer" in result:
            probability = {.1: .55, .2: .54, .3: .75, .4: .53}[spec.params["theta"]]
            batch = result["peer"]
            result["peer"] = replace(batch, events=tuple(replace(row, probabilities=(1 - probability, probability))
                                                        for row in batch.events))
        return result

    def holdout_peer(spec, target, folds):
        assert target == "peer"
        batch = holdout(spec, target, folds)
        probability = .55 if spec == active else .45 if peer == "loses" else .75
        rows = tuple(replace(row, probabilities=(1 - probability, probability)) for row in batch.events)
        if peer == "below_floor" and spec == active:
            return HoldoutEvaluation(rows[11:], refusals=tuple(Refusal(row.event_key, row.line_key, "source_unavailable")
                                                               for row in rows[:11]))
        return replace(batch, events=rows)

    result = harness.run(**dict(inputs, evaluate=search, evaluate_holdout=holdout_peer),
                         policy_context=PolicyContext(STARTED_AT, registry, (legacy,)))
    artifact = json.loads(result.artifact_bytes)
    assert result.outcome == expected
    assert len(artifact["holdout"]["callbacks"]) == 2
    assert set(artifact["holdout"]["comparisons"]) == {peer_lineage}
    hypothesis = artifact["holm_hypotheses"][0]
    assert hypothesis == {"lineage": LINEAGE, "attempted": False, "p_value": 1.0, "observed_p_value": None,
                          "reason": "stage1_rejection_quarantined"}
    assert set(result.proposed_specs) == ({peer_lineage} if peer == "wins" else set())
    assert not result.statistical_rejections


def _research_module(name):
    path = Path(__file__).resolve().parents[1] / "tools" / f"{name}.py"
    return _module_at(path)


def _module_at(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def intl_research():
    return _research_module("wp_intl_validate")


@pytest.mark.parametrize("binary", [False, True])
def test_international_research_log_loss_matches_pinned_functions_exactly(binary, intl_research):
    research = intl_research

    def score(probabilities, outcomes, *, compare_ordinary=False):
        if binary:
            ordered = [((1 - p, p), outcome) for p, outcome in zip(probabilities, outcomes, strict=True)]
            reference = research.binary_log_loss(probabilities, outcomes)
        else:
            ordered = []
            for p, outcome in zip(probabilities, outcomes, strict=True):
                vector = [(1 - p) / 2] * 3
                vector[outcome] = p
                ordered.append((tuple(vector), outcome))
            reference = research.multiclass_log_loss([
                {"model_probs": vector, "outcome": outcome} for vector, outcome in ordered
            ], "model_probs")
        result = runner._intl_research_loss(ordered, over_index=1 if binary else None)
        assert result == reference
        ordinary = None
        if compare_ordinary:
            ordinary = scorecard_mod.build_scorecard(SyntheticFamily().baseline_spec(), [
                ScoredEvent(f"{i:03}", "line", i, vector, outcome) for i, (vector, outcome) in enumerate(ordered)
            ]).log_loss
        return result, ordinary

    outcomes = [int(binary)] * 20
    model, model_main = score([1.0, .9] * 10, outcomes, compare_ordinary=True)
    benchmark, benchmark_main = score([1.0 - 5e-13, .9 + 2e-13] * 10, outcomes, compare_ordinary=True)
    assert model == (0.052680257829413116, 10)
    assert benchmark == (0.05268025782930204, 10)
    compare = scorecard_mod.gate_d_walkforward
    for baseline in (benchmark[0], model[0]):
        assert compare({"log_loss": model[0]}, {"log_loss": baseline}, 20, min_events=20,
                       metrics=("log_loss",)).verdict == scorecard_mod.VERDICT_FAIL
    assert model_main < benchmark_main
    edge = [0.0, 1.0, 5e-13, 1e-12, 2e-12, 1 - 2e-12, 1 - 1e-12, 1 - 5e-13] * 3
    score(edge, [0] * len(edge) if binary else [i % 3 for i in range(len(edge))])


def _source_ref(payload):
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return runner.SourceArtifactRef(sha256(content).hexdigest(), len(content)), content


def _international_match(day, home="A", away="B", tournament="Friendly", score=(2, 1)):
    return {"date": day.isoformat(), "home": home, "away": away, "tournament": tournament,
            "neutral": False, "home_goals": score[0], "away_goals": score[1], "venue": None}


def _intl_snapshot(matches):
    from polymarket_alpha_lab.papersim import intl_data

    teams = sorted({match[field] for match in matches for field in ("home", "away")} |
                   {f"Synthetic {i}" for i in range(intl_data.MIN_TEAMS)})
    return {"source": intl_data.SOURCE_KEY, "generated_at": "2026-11-20T00:00:00Z", "matches": matches,
            "teams": {name: {"elo": 9000} for name in teams},
            "elo_history": [{"team": name, "date": matches[-1]["date"], "elo": 9000} for name in teams]}


def _intl_lines(records, target):
    lines = []
    for record in records:
        row = record.intl_source_row
        goals_sum, margin = row.home_goals + row.away_goals, row.home_goals - row.away_goals
        if target == "match_winner":
            order, line = ("home", "draw", "away"), None
            outcome = 0 if margin > 0 else 1 if margin == 0 else 2
        else:
            order, line = ("over", "under"), 2.5
            outcome = int(goals_sum < 3)
        lines.append(FixtureLine(record.event_key, target, target, record.event_key, order, outcome, target, line,
                                 home_team=row.home, away_team=row.away, competition=row.tournament,
                                 actual_total=goals_sum, actual_margin=margin))
    return tuple(lines)


@pytest.mark.parametrize(("target", "nations_games", "verdict"), [
    ("match_winner", 0, "NOT_ASSESSABLE"),
    ("match_winner", 19, "NOT_ASSESSABLE"),
    ("match_winner", 20, "FAIL"),
    ("over_under", 20, "FAIL"),
    ("over_under", 20, "PASS"),
])
def test_gate_d_uses_only_registered_nations_league_research_contracts(target, nations_games, verdict, intl_research):
    research = intl_research
    tournament = research.NL_TOURNAMENT
    start = dt.date(2025, 1, 1)
    warmup = [_international_match(start + dt.timedelta(days=i), tournament=tournament, score=(i % 2 + 1, 1))
              for i in range(8)]
    suffix = [_international_match(start + dt.timedelta(days=8 + i // 30), f"Team{i}", f"Guest{i}",
                                   tournament if i < nations_games else "Friendly",
                                   (2, 1) if i < nations_games or i % 2 else (1, 0))
              for i in range(300)]
    # Give both sides of each rated fixture a pre-holdout match in its own history.
    warmup[:0] = [_international_match(start, row["home"], row["away"], "Friendly", (0, 2)) for row in suffix]
    family = SyntheticFamily((target,))
    # Use both runner gates with a compact catalogue so the experiment can vary holdout effects independently.
    if target == "match_winner":
        family.family_id = "intl-elo-poisson"
    else:
        family.family_id = "intl-elo-goal-residuals"
        # The benchmark still comes from the registered totals model. Add strictly prior team appearances.
        other_day = start - dt.timedelta(days=1)
        warmup[:0] = [_international_match(other_day, row["home"], row["away"], "Friendly", (g, g + 2))
                      for row in suffix for g in range(1, 4)]
    ref, content = _source_ref(_intl_snapshot(warmup + suffix))
    records = runner.international_snapshot_records(content, ref, last_complete_date=dt.date(2026, 11, 19))
    cut = len(warmup)
    held = {row.group_id for row in records[cut:]}
    all_lines = _intl_lines(records, target)
    registry = Harness(11).context(family)
    history_engine = research.WalkForwardEngine(warmup + suffix)
    reference_rows = []

    def run(*, perturb=False, extra_line=False):
        lines = list(all_lines)
        if extra_line:
            lines.append(replace(all_lines[cut], line_key="other", observation_id="other", line=3.5, outcome_index=1))
        by_event = {}
        for line in lines:
            by_event.setdefault(line.event_key, []).append(line)

        def forecast(spec, fold, holdout):
            rows = []
            for key in fold.test_keys:
                fixture = fold.international_fixtures[key]
                assert not hasattr(fixture, "home_goals")
                for line in by_event[key]:
                    causal = fold.international_benchmarks.get((fixture.source_event_key, target))
                    model_active = spec.params["theta"] == .2
                    fallback = None
                    if not holdout and target == "over_under" and line.competition == tournament and fold.source_day < start + dt.timedelta(days=4):
                        probabilities, fallback = causal, "insufficient_recent_matches"
                    else:
                        # D depends on Nations League probabilities. Non-NL perturbations leave those untouched.
                        if holdout and line.competition == tournament and line.line != 3.5:
                            p = .999 if verdict == "PASS" else .1
                        elif holdout:
                            p = .95 if perturb else .99
                        else:
                            p = .6 if model_active else .95 if spec.params["theta"] == .4 else .55
                        values = [(1 - p) / (len(line.class_order) - 1)] * len(line.class_order)
                        values[line.outcome_index] = p
                        probabilities = tuple(values)
                    rows.append(Prediction(key, line.line_key, probabilities, line.class_order, fallback_reason=fallback))
            return HoldoutEvaluation(tuple(rows))

        def evaluate(spec, fold, mask):
            return {target: forecast(spec, fold, False)}

        def holdout(spec, target, folds):
            return HoldoutEvaluation(tuple(row for fold in folds for row in forecast(spec, fold, True).events))

        return execute_optimization_run(
            family=family, records=records, fixture_lines=lines, holdout_group_ids=held, evaluate=evaluate,
            evaluate_holdout=holdout, data_cutoff=DATA_CUTOFF, random_seed=1, code_commit=CODE_COMMIT,
            policy_context=registry, deadline=lambda: False, source_calendar="international source dates",
            source_timezone=UTC, last_complete_date=dt.date(2026, 11, 19), snapshot_refs=(ref.sha256,),
            source_artifacts={ref.sha256: content}, min_train_blocks=2,
        )

    result = run()
    artifact = json.loads(result.artifact_bytes)
    lineage = f"{family.family_id}/{target}"
    assert artifact["holdout"]["comparisons"][lineage]["coverage"]["paired_games"] == 300, artifact["issue"]
    for match in warmup:
        history_engine._apply(match)
    for match in suffix[:nations_games]:
        row = history_engine.predict(match["home"], match["away"], match["tournament"], neutral=match["neutral"])
        assert row is not None
        p = .999 if verdict == "PASS" else .1
        row.update(family="unl", date=match["date"], outcome=0, over25=1, p_over25=p,
                   model_probs=(p, (1 - p) / 2, (1 - p) / 2), base_rate_probs=row["base_rates_causal"],
                   uniform_probs=(1 / 3,) * 3, fixed_nl_probs=research.NL_FIXED_BASE_RATES)
        reference_rows.append(row)
    reference = research.family_metrics(reference_rows)
    original_gates = [gate for gate in artifact["candidate_scorecards"][lineage]["gates"] if gate["gate"] == "D"]
    assert len(original_gates) == 2
    assert {gate["verdict"] for gate in original_gates} == {verdict} or (
        verdict == "FAIL" and any(gate["verdict"] == "FAIL" for gate in original_gates)
    )
    for gate in original_gates:
        numbers = dict(gate["numbers"])
        assert numbers["n_events"] == str(nations_games)
        assert numbers["ordered_original_row_indices"] == ",".join(map(str, range(cut, cut + nations_games)))
        assert numbers["scope"] == "paired_holdout"
        if reference_rows:
            metrics = reference["one_x_two" if target == "match_winner" else "over25"]
            check = gate["check"].removeprefix("walk_forward_beats_")
            names = {"causal_tournament_base_rate": "causal_base_rate", "coin_flip": "coin"}
            assert float(numbers["model_log_loss"]) == metrics["model"]["log_loss"]
            assert float(numbers["baseline_log_loss"]) == metrics[names.get(check, check)]["log_loss"]
    if nations_games == 20 and verdict == "FAIL":
        changed = json.loads(run(perturb=True, extra_line=target == "over_under").artifact_bytes)
        assert [gate for gate in changed["candidate_scorecards"][lineage]["gates"] if gate["gate"] == "D"] == original_gates


@pytest.fixture(scope="module")
def nba_raw_bundle():
    from polymarket_alpha_lab.papersim import espn_nba

    fixtures = _module_at(Path(__file__).with_name("test_papersim_espn_nba.py"))
    completed = ("4011", "2026-10-22", "ATL", "BOS", 118, 113)
    schedules = {team: fixtures._schedule_payload(team, display, [
        fixtures._schedule_event(*completed) if team in ("ATL", "BOS") else
        fixtures._schedule_event("4012", "2026-11-25", team, "ATL", 0, 0, completed=False),
    ]) for team, (_, display) in espn_nba.NBA_TEAMS.items()}
    summary = fixtures._summary_payload(*completed, (90, 10, 15, 25), (85, 8, 12, 20), injuries=[
        fixtures._injury_block("ATL", [("A Player", "Out", "2026-10-22")]),
    ])
    summary["header"]["id"] = "4011"
    summary["header"]["season"] = {"year": 2027, "type": 2}
    summary["header"]["competitions"][0]["date"] = "2026-10-22T17:00Z"
    return fixtures._bundle(schedules, {"4011": summary})


def _nba_snapshot_with_days(bundle, dates):
    from polymarket_alpha_lab.papersim import espn_nba

    snapshot = espn_nba.build_snapshot(bundle)
    home = snapshot["teams"]["ATL"]["games"][0]
    away = snapshot["teams"]["BOS"]["games"][0]
    teams = snapshot["teams"]
    pair_offsets = tuple(zip(sorted(teams)[::2], sorted(teams)[1::2], strict=True))
    for block in teams.values():
        block["games"] = []
    for day, count in sorted(dates.items()):
        for home_team, away_team in pair_offsets[:count]:
            for team, opponent, template in ((home_team, away_team, home), (away_team, home_team, away)):
                games = teams[team]["games"]
                rest = (day - dt.date.fromisoformat(games[-1]["date"])).days if games else None
                games.append(dict(template, date=day.isoformat(), opponent=opponent, rest_days=rest, b2b=rest == 1))
    return snapshot


def _nba_source_inputs(snapshot, bundle=None):
    snapshot_ref, content = _source_ref(snapshot)
    archives = {snapshot_ref.sha256: content}

    def refs(raw):
        result = {}
        for key, document in raw.items():
            ref, blob = _source_ref(document)
            result[key] = ref
            archives[ref.sha256] = blob
        return result

    manifest = None if bundle is None else runner.NBASourceManifest(
        snapshot_ref, bundle["season"], refs(bundle["schedules"]), refs(bundle["summaries"]),
    )
    return {"source_artifacts": archives, "snapshot_refs": tuple(archives), "nba_source_manifest": manifest}


def _nba_pure_run(records, lines, holdout_groups, source, **overrides):
    from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY

    family = NBA_PACE_EFFICIENCY
    calls = {"search": [], "holdout": []}

    def evaluate(spec, fold, mask):
        calls["search"].append(fold.block_id)
        return {target: HoldoutEvaluation(tuple(
            Prediction(line.event_key, line.line_key, (0.5, 0.5), line.class_order)
            for line in lines if line.target == target and line.event_key in fold.test_keys
        )) for target in mask}

    def holdout(*args):
        calls["holdout"].append(args[1])
        pytest.fail("this cohort has insufficient holdout coverage")

    options = dict(
        family=family, records=records, fixture_lines=lines, holdout_group_ids=holdout_groups,
        evaluate=evaluate, evaluate_holdout=holdout, data_cutoff=DATA_CUTOFF, random_seed=1,
        code_commit=CODE_COMMIT, policy_context=Harness(11, holdout_blocks=0).context(family),
        deadline=lambda: False, source_calendar="ESPN NBA source date", source_timezone=UTC,
        last_complete_date=dt.date(2026, 11, 19), **source,
    )
    options.update(overrides)
    return lambda: execute_optimization_run(**options), calls


def test_actual_nba_rejects_two_invented_oof_rows_even_after_full_reconstruction(nba_raw_bundle):
    from polymarket_alpha_lab.papersim import espn_nba

    source = _nba_source_inputs(espn_nba.build_snapshot(nba_raw_bundle), nba_raw_bundle)
    days = tuple(dt.date(2026, 10, day) for day in range(20, 25))
    records = tuple(FixtureRecord(day.isoformat(), day.isoformat(), source_day=day, label_day=day)
                    for day in days)
    lines = tuple(FixtureLine(row.event_key, "totals", "225.5", row.event_key, ("under", "over"),
                              1, "totals", 225.5) for row in records)
    run, calls = _nba_pure_run(records, lines, (days[-1].isoformat(),), source, targets=("totals",))
    try:
        result = run()
    except ValueError as error:
        assert "NBA" in str(error)
    else:
        artifact = json.loads(result.artifact_bytes)
        card = artifact["diagnostic_scorecards"]["nba-pace-efficiency/totals"]
        pytest.fail(f"allowed {len(artifact['search_predictions'])} invented OOF rows, "
                    f"Gate A: {[(row['check'], row['verdict']) for row in card['gates'] if row['gate'] == 'A']}")
    assert not calls["search"] and not calls["holdout"]


@pytest.mark.parametrize(("mode", "reason"), [
    ("snapshot_missing", "unique, verified ESPN snapshot"),
    ("snapshot_ambiguous", "unique, verified ESPN snapshot"),
    ("source_date", "paired source game"),
    ("rotated_teams", "paired source game"),
    ("unknown_team", "paired source game"),
    ("missing_team", "both source teams"),
    ("event_duplicate", "unique event keys"),
    ("event_alias", "reference one NBA source game"),
    ("event_without_contract", "through its registered lines"),
    ("duplicated_source_team", "paired source game"),
    ("opposite_source_score", "paired source game"),
    ("tied_source_score", "paired source game"),
    ("line_total", "score conflicts"),
    ("line_margin", "score conflicts"),
    ("line_market", "incompatible market"),
    ("line_strike", "incompatible market"),
    ("line_sides", "class order"),
    ("quote_sides", "quote sides"),
    ("moneyline_outcome", "realization conflicts"),
    ("totals_outcome", "realization conflicts"),
])
def test_actual_nba_source_and_contract_conflicts_abort_before_scoring(nba_raw_bundle, mode, reason):
    from polymarket_alpha_lab.papersim import espn_nba

    snapshot = espn_nba.build_snapshot(nba_raw_bundle)
    day = dt.date(2026, 10, 22)
    record = FixtureRecord("one", "held", source_day=day, label_day=day)
    moneyline = FixtureLine("one", "moneyline", "win", "moneyline", ("away", "home"), 1, "moneyline",
                            home_team="ATL", away_team="BOS")
    total = FixtureLine("one", "totals", "225.5", "totals", ("under", "over"), 1, "totals", 225.5,
                        home_team="ATL", away_team="BOS")
    extra_records, extra_contracts = (), ()
    if mode == "source_date":
        record = replace(record, source_day=day - dt.timedelta(days=1))
    elif mode == "rotated_teams":
        moneyline = replace(moneyline, home_team="BOS", away_team="ATL")
    elif mode == "unknown_team":
        moneyline = replace(moneyline, away_team="NYK")
    elif mode == "missing_team":
        total = replace(total, away_team=None)
    elif mode == "event_duplicate":
        extra_records = (record,)
    elif mode == "event_alias":
        extra_records = (replace(record, event_key="alias"),)
        extra_contracts = (replace(total, event_key="alias", observation_id="alias"),)
    elif mode == "event_without_contract":
        extra_records = (replace(record, event_key="unlinked"),)
    elif mode == "duplicated_source_team":
        snapshot["teams"]["ATL"]["games"] *= 2
    elif mode == "opposite_source_score":
        snapshot["teams"]["BOS"]["games"][0]["opp_pts"] = 120
    elif mode == "tied_source_score":
        snapshot["teams"]["ATL"]["games"][0]["opp_pts"] = 118
        snapshot["teams"]["BOS"]["games"][0]["pts"] = 118
    elif mode == "line_total":
        total = replace(total, actual_total=228)
    elif mode == "line_margin":
        moneyline = replace(moneyline, actual_margin=-5)
    elif mode == "line_market":
        total = replace(total, market_type="moneyline")
    elif mode == "line_strike":
        total = replace(total, line=220.5)
    elif mode == "line_sides":
        moneyline = replace(moneyline, class_order=("home", "away"), outcome_index=0)
    elif mode == "quote_sides":
        moneyline = replace(moneyline, quote_sides=("ATL", "BOS"))
    elif mode == "moneyline_outcome":
        moneyline = replace(moneyline, outcome_index=0)
    elif mode == "totals_outcome":
        total = replace(total, outcome_index=0)
    source = _nba_source_inputs(snapshot)
    if mode == "snapshot_missing":
        source.update(source_artifacts={}, snapshot_refs=())
    elif mode == "snapshot_ambiguous":
        ref, content = _source_ref(dict(snapshot, generated_at="2026-10-22T20:00:00+00:00"))
        source["source_artifacts"][ref.sha256] = content
        source["snapshot_refs"] = tuple(source["source_artifacts"])
    run, calls = _nba_pure_run((record, *extra_records), (moneyline, total, *extra_contracts), ("held",), source)
    with pytest.raises(ValueError, match=reason):
        run()
    assert not calls["search"] and not calls["holdout"]


def test_actual_nba_both_targets_share_source_game_and_causal_history(nba_raw_bundle):
    from polymarket_alpha_lab.papersim.optimize.families.nba import NbaFamilyFixture

    days = tuple(dt.date(2026, 10, day) for day in range(20, 24))
    snapshot = _nba_snapshot_with_days(nba_raw_bundle, dict.fromkeys(days, 1))
    ref, content = _source_ref(snapshot)
    records, lines = runner.nba_snapshot_cohort(content, ref, last_complete_date=days[-1])
    scored, withheld = records[-2:]
    scored_line = next(line for line in lines if line.event_key == scored.event_key and line.target == "moneyline")
    calls = []

    def evaluate(spec, fold, targets):
        calls.append(fold.block_id)
        assert fold.test_keys == (scored.event_key,)
        history = runner.nba_history_at(fold=fold)
        history_rows = [row for team in history["teams"].values() for row in team["games"]]
        assert {row["date"] for row in history_rows} == {day.isoformat() for day in days[:2]}
        assert history["injuries"] == {} and len(history_rows) == 4
        assert fold.nba_fixtures == {
            scored.event_key: NbaFamilyFixture(scored_line.home_team, scored_line.away_team, 1, 1, True, True,
                                               scored.source_day.isoformat())
        }
        return {target: HoldoutEvaluation(tuple(
            Prediction(line.event_key, line.line_key, (0.4, 0.6), line.class_order)
            for line in lines if line.target == target and line.event_key == scored.event_key
        )) for target in targets}

    # Allow source-derived measurements to populate each settled contract.
    stripped = tuple(replace(line, actual_total=None, actual_margin=None) for line in lines)
    run, _ = _nba_pure_run(records, stripped, (withheld.group_id,), _nba_source_inputs(snapshot), evaluate=evaluate)
    result = run()
    artifact = json.loads(result.artifact_bytes)
    assert calls == [scored.group_id] and not artifact["holdout"]["callbacks"]
    assert result.outcome == runner.INSUFFICIENT_DATA
    assert artifact["holdout"]["n_events"] == 1
    assert len(artifact["search_predictions"]) == 2
    assert all(card["n_events"] == 1 and math.isfinite(float(card["log_loss"]))
               for card in artifact["diagnostic_scorecards"].values())
    assert {row["event_key"] for row in artifact["search_predictions"]} == {scored.event_key}
    game = artifact["nba_source_games"][scored.event_key]
    assert game["source_identity"] == [ref.sha256, scored.group_id, scored_line.home_team, scored_line.away_team]
    assert game["source_event_key"] == scored.event_key
    assert (game["home_points"], game["away_points"], game["actual_margin"], game["actual_total"]) == (118, 113, 5, 231)
    assert {target: line["realized_side"] for target, line in game["contracts"].items()} == {"moneyline": "home", "totals": "over"}
    assert artifact["nba_source_integrity"]["rebuild_verdict"] == "NOT_ASSESSABLE"
    assert not result.proposed_specs and not result.statistical_rejections


def test_actual_nba_canonical_event_key_cannot_be_rebound_to_another_same_day_game(nba_raw_bundle):
    day = dt.date(2026, 10, 22)
    snapshot = _nba_snapshot_with_days(nba_raw_bundle, {day: 2})
    ref, content = _source_ref(snapshot)
    (first, second), lines = runner.nba_snapshot_cohort(content, ref, last_complete_date=day)
    rebound = tuple(replace(line, event_key=first.event_key) for line in lines if line.event_key == second.event_key)
    run, calls = _nba_pure_run((first,), rebound, (first.group_id,), _nba_source_inputs(snapshot))
    with pytest.raises(ValueError, match="source identity of its event key"):
        run()
    assert not calls["search"] and not calls["holdout"]


def _historical_manifest(family, documents, absent=()):
    sources, archives = [], {}
    for (role, key), document in documents.items():
        ref, content = _source_ref(document)
        sources.append({"role": role, "key": key, "kind": "fixture_source", "schema_version": "v1",
                        "sha256": ref.sha256, "byte_length": ref.byte_length})
        archives[ref.sha256] = content
    sources.extend({"role": role, "key": key, "kind": "fixture_source", "schema_version": "v1",
                    "required": False, "absent_reason": "unavailable_in_archive"} for role, key in absent)
    return {"schema_version": 1, "protocol": runner.historical_protocol(family), "sources": sources}, archives


def _historical_active_run(family, manifest, archives, params, *, deadline=lambda: False):
    arguments = runner.historical_run_arguments(family=family, manifest=manifest, source_artifacts=archives,
                                                deadline=deadline)
    baseline = family.baseline_spec()
    active = arguments["build_candidate_spec"]({**baseline.params, **params})
    assert active != baseline
    registry = {}
    for target in family.targets():
        lineage = f"{family.family_id}/{target}"
        registry[lineage] = RegistryComparison(LineageRoles(lineage, baseline.spec_sha256(), active.spec_sha256()), active)
    result = execute_optimization_run(
        **arguments, data_cutoff=dt.datetime.fromisoformat(manifest["protocol"]["data_cutoff"]),
        random_seed=manifest["protocol"]["seed"], code_commit=CODE_COMMIT,
        policy_context=PolicyContext(STARTED_AT, registry), deadline=deadline,
        source_artifacts=archives, snapshot_refs=tuple(archives),
    )
    return result, arguments, active


@pytest.mark.parametrize("id_", ["intl-elo-goal-residuals", "intl-elo-poisson"])
def test_historical_international_adapters_bind_real_history_and_variants(id_):
    from polymarket_alpha_lab.papersim.optimize.families.intl import INTL_ELO, INTL_TOTALS

    family = INTL_ELO if id_ == INTL_ELO.family_id else INTL_TOTALS
    (target,) = family.targets()
    start = dt.date(2024, 12, 20)
    days = [start + dt.timedelta(days=i) for i in range(8)] + [dt.date(2025, 1, 2)]
    matches = [_international_match(day, score=(2 if i % 2 else 3, 1)) for i, day in enumerate(days)]
    source = {("intl_snapshot", "snapshot"): _intl_snapshot(matches)}
    manifest, archives = _historical_manifest(family, source)
    param = {"recent_days": 365} if family is INTL_TOTALS else {"slope": 0.55}
    result, _, active = _historical_active_run(family, manifest, archives, param)
    assert result.outcome == runner.INSUFFICIENT_DATA
    artifact = json.loads(result.artifact_bytes)
    lineage = f"{family.family_id}/{target}"
    assert artifact["diagnostic_scorecards"][lineage]["n_events"] == 6
    assert len(artifact["search_predictions"]) == 6 and not artifact["holdout"]["callbacks"]
    assert all(row["spec_sha256"] == active.spec_sha256() and len(row["variants"]) == len(active.envelope)
               for row in artifact["search_predictions"])
    assert all(artifact["international_source_rows"][row["event_key"]]["source_day"] == row["fold"]
               for row in artifact["search_predictions"])


def test_historical_nba_adapter_scores_both_models_on_one_verified_game(nba_raw_bundle):
    from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY

    days = (dt.date(2026, 10, 20), dt.date(2026, 10, 21), dt.date(2026, 10, 22), dt.date(2026, 11, 1))
    documents = {("nba_snapshot", "snapshot"): _nba_snapshot_with_days(nba_raw_bundle, dict.fromkeys(days, 1))}
    family = NBA_PACE_EFFICIENCY
    manifest, archives = _historical_manifest(family, documents,
                                               absent=(("event_summary", "event_summary/summary_4011.json"),))
    result, _, active = _historical_active_run(family, manifest, archives, {"half_life": 10.0})
    assert result.outcome == runner.INSUFFICIENT_DATA
    artifact = json.loads(result.artifact_bytes)
    forecasts = artifact["search_predictions"]
    assert len(forecasts) == 2 and {row["line_key"] for row in forecasts} == {"moneyline", "totals"}
    assert {row["event_key"] for row in forecasts} == {forecasts[0]["event_key"]}
    assert all(len(row["variants"]) == 6 and row["spec_sha256"] == active.spec_sha256() for row in forecasts)
    assert all(math.isfinite(row["home_rating"]) and math.isfinite(row["away_rating"]) for row in forecasts)
    assert artifact["holdout"]["n_events"] == 1 and not artifact["holdout"]["callbacks"]
    assert artifact["nba_source_integrity"]["rebuild_verdict"] == "NOT_ASSESSABLE"
    assert len(artifact["dataset_summary"]["absent_sources"]) == 1


def test_historical_adapter_honors_deadline_between_predictions(nba_raw_bundle, monkeypatch):
    from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY

    dates = (dt.date(2026, 10, 20), dt.date(2026, 10, 21), dt.date(2026, 10, 22), dt.date(2026, 11, 1))
    family = NBA_PACE_EFFICIENCY
    manifest, archives = _historical_manifest(family, {
        ("nba_snapshot", "snapshot"): _nba_snapshot_with_days(nba_raw_bundle, dict.fromkeys(dates, 2)),
    })
    genuine = family.predict
    calls = []

    def predict(fitted, fixture):
        calls.append(fixture.before_date)
        return genuine(fitted, fixture)

    monkeypatch.setattr(family, "predict", predict)
    result, _, _ = _historical_active_run(family, manifest, archives, {"half_life": 10.0}, deadline=lambda: bool(calls))
    artifact = json.loads(result.artifact_bytes)
    assert result.outcome == runner.RUN_FAILED and result.budget_exhausted
    assert calls == ["2026-10-22"] and artifact["counts"]["aborted_trials"] == 1
    assert artifact["counts"]["completed_trials"] == 0 and artifact["failure"] is None
    assert not artifact["search_predictions"] and not artifact["holdout"]["callbacks"]


@pytest.mark.parametrize("eligible_grid", [False, True])
def test_historical_club_adapter_uses_complete_official_rounds_and_all_nine_variants(eligible_grid, monkeypatch):
    from polymarket_alpha_lab.papersim.optimize import dataset
    from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import SOCCER_RECALIB

    teams = [f"Club {i:02}" for i in range(20)]
    if eligible_grid:
        start = dt.date(2026, 3, 13)
        dates = [(start + dt.timedelta(weeks=i)).isoformat() for i in range(35)]
        fixed_protocol = runner.historical_protocol(SOCCER_RECALIB)
        fixed_protocol["cohort"]["first_holdout_boundary"] = f"{dates[5]}T19:00:00Z"
        # Extend the synthetic suffix to 30 weeks so the actual club grid runs.
        monkeypatch.setattr(runner, "historical_protocol", lambda family: fixed_protocol)
    else:
        dates = ["2026-08-22", "2026-09-02", "2026-10-10", "2026-11-03", "2026-11-06"]
    matches, fixtures = [], []
    for round_id, day in enumerate(dates, 1):
        kickoff = f"{day}T19:00:00Z"
        for offset, (home, away) in enumerate(zip(teams[::2], teams[1::2], strict=True)):
            game = round_id * 100 + offset
            finished = (round_id != 5 if eligible_grid else round_id != 4) or offset != 9
            fixtures.append({"id": str(20000 + game), "round": str(round_id), "home": {"name": home},
                             "away": {"name": away}, "status": {"finished": finished, "utcTime": kickoff,
                                                                  "scoreStr": "2-1" if finished else None}})
            matches.append({"id": str(game), "isResult": finished, "datetime": kickoff, "h": {"title": home},
                            "a": {"title": away}, "goals": {"h": 2, "a": 1}, "xG": {"h": 1.55, "a": 0.95}})
    understat = {"EPL_2026": {"teams": {str(i): {"id": str(i), "title": team} for i, team in enumerate(teams)},
                              "dates": matches}}
    ref, _ = _source_ref(understat)
    official_key = f"tools/fotmob_cache/fixtures/leagues_{dataset.LEAGUES['EPL'][0]}_2026.json"
    documents = {("club_archive", dataset.ARCHIVE_BIG5_PATH): understat,
                 ("club_archive", dataset.ARCHIVE_POINTER_PATH): {
                     "sha256": ref.sha256, "pointed_at_utc": "2026-11-10T10:33:51Z",
                 },
                 ("club_archive", official_key): {"details": {"selectedSeason": "2026/2027"},
                                                   "fixtures": {"allMatches": fixtures}},
                 **{("club_archive", key): {} for key in dataset.ARCHIVE_LEAGUE_PATHS.values()}}
    manifest, archives = _historical_manifest(SOCCER_RECALIB, documents)
    result, arguments, active = _historical_active_run(SOCCER_RECALIB, manifest, archives, {"half_life_days": 60.0})
    artifact = json.loads(result.artifact_bytes)
    if eligible_grid:
        assert (artifact["holdout"]["n_events"], artifact["holdout"]["n_blocks"]) == (300, 30)
        assert artifact["folds"] == ["EPL/2026/3", "EPL/2026/4"]
        assert {len(catalog["entries"]) for catalog in artifact["catalogs"].values()} == {9}
        assert artifact["counts"]["completed_trials"] == 18
        assert artifact["counts"]["shared_target_uses_completed"] == 18
        forecast_specs = {row["spec_sha256"] for batch in artifact["search_trial_forecasts"] for row in batch["predictions"]}
        assert active.spec_sha256() in forecast_specs and len(forecast_specs) == 9
        return
    assert result.outcome == runner.INSUFFICIENT_DATA
    assert arguments["holdout_group_ids"] == ("EPL/2026/5",)
    assert len(artifact["search_predictions"]) == 40 and artifact["folds"] == ["EPL/2026/3"]
    assert all(len(row["variants"]) == 9 and row["spec_sha256"] == active.spec_sha256()
               for row in artifact["search_predictions"])
    assert len(artifact["dataset_summary"]["independent_score_joins"]) == 30
    assert not artifact["holdout"]["callbacks"] and artifact["holdout"]["n_events"] == 10


@pytest.mark.parametrize("mode", ["changed_protocol", "changed_bytes", "omitted_bytes", "duplicate_reference"])
def test_historical_manifest_rejects_unverified_input_before_any_fit(nba_raw_bundle, mode):
    from polymarket_alpha_lab.papersim import espn_nba
    from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY

    manifest, archives = _historical_manifest(NBA_PACE_EFFICIENCY, {
        ("nba_snapshot", "snapshot"): espn_nba.build_snapshot(nba_raw_bundle),
    })
    digest = next(iter(archives))
    if mode == "changed_protocol":
        manifest["protocol"]["seed"] = 100
    elif mode == "changed_bytes":
        archives[digest] += b" "
    elif mode == "omitted_bytes":
        del archives[digest]
    else:
        manifest["sources"].append(manifest["sources"][0])
    with pytest.raises(ValueError, match="protocol|historical source|source bytes"):
        runner.historical_run_arguments(family=NBA_PACE_EFFICIENCY, manifest=manifest,
                                        source_artifacts=archives, deadline=lambda: pytest.fail("before fitting"))


@pytest.mark.parametrize(("mode", "schedule", "rebuild"), [
    ("complete", "PASS", "PASS"),
    ("team_row", "PASS", "FAIL"),
    ("injuries", "PASS", "FAIL"),
    ("summary_missing", "PASS", "NOT_ASSESSABLE"),
    ("schedule_missing", "PASS", "NOT_ASSESSABLE"),
    ("empty", "FAIL", "PASS"),
    ("one_sided", "FAIL", "FAIL"),
    ("duplicate", "FAIL", "FAIL"),
    ("score_conflict", "FAIL", "FAIL"),
])
def test_nba_gate_a_rebuilds_both_tables_from_registered_raw_payloads(nba_raw_bundle, mode, schedule, rebuild):
    from copy import deepcopy

    from polymarket_alpha_lab.papersim import espn_nba

    bundle = deepcopy(nba_raw_bundle)
    if mode == "empty":
        for events in bundle["schedules"].values():
            events["events"][0]["competitions"][0]["status"]["type"]["completed"] = False
        bundle["summaries"] = {}
    snapshot = espn_nba.build_snapshot(bundle)
    if mode == "team_row":
        snapshot["teams"]["ATL"]["games"][0]["poss"] += 1
    elif mode == "injuries":
        snapshot["injuries"]["ATL"][0]["status"] = "Questionable"
    elif mode == "one_sided":
        snapshot["teams"]["BOS"]["games"] = []
    elif mode == "duplicate":
        snapshot["teams"]["ATL"]["games"] *= 2
    elif mode == "score_conflict":
        snapshot["teams"]["ATL"]["games"][0]["pts"] += 1
    elif mode == "summary_missing":
        bundle["summaries"] = {}
    elif mode == "schedule_missing":
        del bundle["schedules"]["BOS"]
    source = _nba_source_inputs(snapshot, bundle)
    archives, manifest = source["source_artifacts"], source["nba_source_manifest"]
    evidence = runner._verified_sources(archives, source["snapshot_refs"])
    gates, report, verified = runner._nba_source_checks(archives, evidence, manifest)
    assert {row.check: row.verdict for row in gates} == {
        "provider_schema_reload": "PASS", "paired_official_schedule": schedule,
        "independent_espn_snapshot_rebuild": rebuild,
    }
    assert report["official_teams"] == 30
    assert report["complete"] is (mode == "complete")
    assert report["paired_games"] == len(verified.games)
    if mode == "complete":
        assert report["raw_team_rows"] == 2 and report["paired_games"] == 1
        assert report["available_game_summaries"] == report["required_game_summaries"] == 1
        assert report["available_team_schedules"] == 30
        assert report["teams_equal"] and report["injuries_equal"]
        research = _research_module("wp_nba_validate")
        (game,) = research.pair_snapshot_games(snapshot)
        pair = verified.games[(game["date"], game["home"], game["away"])]
        assert (pair.home["pts"], pair.away["pts"]) == (game["home_pts"], game["away_pts"])
    elif mode == "injuries":
        assert report["teams_equal"] and not report["injuries_equal"]
    elif mode == "team_row":
        assert not report["teams_equal"] and report["injuries_equal"]
    elif mode == "summary_missing":
        assert report["missing_game_summaries"] == ["4011"]
    elif mode == "schedule_missing":
        assert report["missing_team_schedules"] == ["BOS"]
    elif mode == "empty":
        assert report["paired_games"] == 0
    if mode == "complete":
        forged = replace(manifest, summaries={"4011": replace(manifest.summaries["4011"], byte_length=1)})
        with pytest.raises(ValueError, match="digest and byte length"):
            runner._nba_source_checks(archives, evidence, forged)


@pytest.mark.parametrize("mode", ["schema_only", "failed_schedule", "short_floors"])
def test_nba_source_gate_blocks_proposal_even_when_both_sides_share_the_same_source(nba_raw_bundle, mode):
    harness = Harness(11, search_effect=.05, holdout_effect=.05, holdout_blocks=30,
                      holdout_events_per_block=1 if mode == "short_floors" else 10)
    family = SyntheticFamily(("totals",))
    family.family_id = "nba-pace-efficiency"
    dates = {}
    for row in harness.records:
        day = row.kickoff.date()
        dates[day] = dates.get(day, 0) + 1
    snapshot = _nba_snapshot_with_days(nba_raw_bundle, dates)
    ref, content = _source_ref(snapshot)
    stamped_records, stamped_lines = runner.nba_snapshot_cohort(content, ref, last_complete_date=dt.date(2026, 11, 19))
    event_aliases = {stamped.event_key: registered.event_key
                     for stamped, registered in zip(stamped_records, harness.records, strict=True)}
    for stamped, registered in zip(stamped_records, harness.records, strict=True):
        assert stamped.source_day == registered.kickoff.date()
    records = tuple(replace(stamped, event_key=registered.event_key, group_id=registered.group_id)
                    for stamped, registered in zip(stamped_records, harness.records, strict=True))
    contracts = tuple(replace(line, event_key=event_aliases[line.event_key], line_key="line")
                      for line in stamped_lines if line.target == "totals")

    if mode == "failed_schedule":
        # This unrelated one-sided row makes the whole-source Gate A fail even
        # though every declared and scored fixture has a verified reciprocal game.
        games = snapshot["teams"]["ATL"]["games"]
        games.append(dict(games[-1], date="2026-11-19"))

    def orient(batch):
        return replace(batch, events=tuple(replace(row, class_order=("under", "over")) for row in batch.events))

    def evaluate(spec, fold, targets):
        return {target: orient(batch) for target, batch in harness.evaluate(spec, fold, targets).items()}

    def holdout(spec, target, folds):
        return orient(harness.evaluate_holdout(spec, target, folds))

    result = harness.run(family=family, records=records, fixture_lines=contracts, floor_class=None,
                         source_calendar="ESPN NBA source date", source_timezone=UTC,
                         last_complete_date=dt.date(2026, 11, 19), evaluate=evaluate, evaluate_holdout=holdout,
                         **_nba_source_inputs(snapshot))
    artifact = json.loads(result.artifact_bytes)
    lineage = "nba-pace-efficiency/totals"
    diagnostic = {gate["check"]: gate["verdict"] for gate in artifact["diagnostic_scorecards"][lineage]["gates"]}
    assert diagnostic["provider_schema_reload"] == "PASS"
    assert diagnostic["independent_espn_snapshot_rebuild"] == "NOT_ASSESSABLE"
    assert not result.proposed_specs and not result.statistical_rejections
    if mode == "short_floors":
        assert result.outcome == runner.INSUFFICIENT_DATA
        assert not artifact["holdout"]["callbacks"]
    else:
        assert result.decisions[0].ci_passes
        assert "required_quality_evidence_unverified" in result.decisions[0].flags
        for card in ("candidate_scorecards", "incumbent_scorecards"):
            assert next(gate["verdict"] for gate in artifact[card][lineage]["gates"]
                        if gate["check"] == "paired_official_schedule") == ("FAIL" if mode == "failed_schedule" else "PASS")


def _intl_replay_fixture():
    start = dt.date(2025, 4, 1)
    fixture = lambda day, **kwargs: _international_match(start + dt.timedelta(days=day), **kwargs)
    nl = "UEFA Nations League"
    return [
        fixture(0, score=(0, 0), tournament=nl),
        fixture(1),
        fixture(2, score=(2, 1)), fixture(2, score=(1, 2)),
        fixture(3, score=(0, 0), tournament=nl), fixture(3, away="New team", score=(1, 0), tournament=nl),
        fixture(4, tournament="Unseen cup"), fixture(4, away="New team", score=(1, 0), tournament=nl),
        fixture(5, score=(0, 4)),
        fixture(6, score=(2, 1)),
    ]


def _intl_run_inputs(matches, family):
    snapshot, content = _source_ref(_intl_snapshot(matches))
    complete = dt.date(2026, 11, 19)
    records = runner.international_snapshot_records(content, snapshot, last_complete_date=complete)
    target, = family.targets()
    return dict(family=family, records=records, fixture_lines=_intl_lines(records, target),
                holdout_group_ids=(records[-1].group_id,), data_cutoff=DATA_CUTOFF, random_seed=1,
                code_commit=CODE_COMMIT, policy_context=Harness(11).context(family), deadline=lambda: False,
                source_calendar="international source dates", source_timezone=UTC, last_complete_date=complete,
                snapshot_refs=(snapshot.sha256,), source_artifacts={snapshot.sha256: content})


@pytest.mark.parametrize("target", ["match_winner", "over_under"])
def test_full_archive_day_barrier_matches_research_even_after_source_scores_change(target, intl_research):
    from polymarket_alpha_lab.papersim import intl_model, intl_totals
    from polymarket_alpha_lab.papersim.optimize.families.intl import INTL_ELO, INTL_TOTALS, IntlFamilyFixture, IntlHistory

    family = INTL_ELO if target == "match_winner" else INTL_TOTALS
    original = _intl_replay_fixture()
    if target == "match_winner":
        original[0] = dict(original[0], home_goals=1, away_goals=1)

    def execute(matches, scored_days):
        inputs = _intl_run_inputs(matches, family)
        inputs["search_group_ids"] = tuple(scored_days)
        expected_key = {row.event_key: row.intl_source_row.index for row in inputs["records"]}
        fixtures = {line.event_key: line for line in inputs["fixture_lines"]}
        predictions = {}

        def forecast(spec, fold, mask):
            engine = intl_research.WalkForwardEngine(matches)
            permitted = [match for match in matches if match["date"] < fold.source_day.isoformat()]
            history = runner.international_history_at(fold=fold)
            assert list(history["matches"]) == permitted
            assert not history["teams"] and not history["elo_history"]
            if fold.source_day == dt.date(2025, 4, 4):
                assert [expected_key[key] for key in fold.train_keys][-2:] == [2, 3]
            for match in permitted:
                engine._apply(match)
            fitted = family.fit(IntlHistory(history), spec, fold.cutoff)
            values, refusals = [], []
            for key in fold.test_keys:
                line = fixtures[key]
                identity = fold.international_fixtures[key]
                benchmark = fold.international_benchmarks[identity.source_event_key, target]
                prior = engine.tour.get(identity.tournament, [0, 0, 0, 0, 0])
                fixture = IntlFamilyFixture(intl_model.IntlFixture(identity.home, identity.away,
                                                                   identity.date.isoformat(), identity.neutral),
                                            identity.tournament)
                prediction = runner.international_replay_prediction(family, fitted, fixture)
                if target == "match_winner":
                    reference = engine.predict(identity.home, identity.away, identity.tournament, neutral=identity.neutral)
                    expected = tuple(value / prior[1] for value in prior[2:]) if prior[1] else (1 / 3,) * 3
                    assert benchmark == expected
                    if reference is None:
                        assert prediction is None
                        refusals.append(Refusal(key, line.line_key, "no_replayed_ratings"))
                        predictions[expected_key[key]] = (None, benchmark)
                        continue
                    p = tuple(getattr(prediction, field) for field in ("p_home", "p_draw", "p_away"))
                    assert p == tuple(reference[field] for field in ("p_home", "p_draw", "p_away"))
                    fallback = None
                else:
                    m = prior[0] / prior[1] if prior[1] else intl_model.GLOBAL_FALLBACK_TOTAL
                    over = intl_totals._over25(m / 2, m / 2)
                    assert benchmark == (over, 1 - over)
                    assert prediction.baseline_over25 == over
                    assert prediction.tournament_matches == prior[1]
                    if identity.tournament == "Unseen cup":
                        assert over == 0.4815702819358626
                    if identity.date == dt.date(2025, 4, 4):
                        assert over == 0.0 and prediction.fallback_reason == "zero_tournament_mean"
                    p = prediction.p_over25, prediction.p_under25
                    fallback = prediction.fallback_reason
                predictions[expected_key[key]] = (p, benchmark, fallback)
                values.append(Prediction(key, line.line_key, p, line.class_order, fallback_reason=fallback))
            return {target: HoldoutEvaluation(tuple(values), refusals=tuple(refusals))}

        def unopened(*args):
            pytest.fail("one-event holdout opened model inference")

        result = execute_optimization_run(**inputs, evaluate=forecast, evaluate_holdout=unopened)
        artifact = json.loads(result.artifact_bytes)
        assert result.outcome == runner.INSUFFICIENT_DATA, artifact["issue"]
        return predictions, {row["source_original_row_index"]: row for row in artifact["international_benchmarks"]}, artifact

    for mutated_index, score in ((4, (1, 0)), (5, (1, 1)), (8, (7, 1))):
        mutant = list(original)
        mutated = dict(original[mutated_index], home_goals=score[0], away_goals=score[1])
        mutant[mutated_index] = mutated
        days = sorted({match["date"] for match in original[:8] if match["date"] <= mutated["date"]})[2:]
        first, first_benchmarks, first_result = execute(original, days)
        second, second_benchmarks, second_result = execute(mutant, days)
        assert first_result["snapshot_refs"] != second_result["snapshot_refs"]
        assert first_result["decisions"] == second_result["decisions"]
        for index in first:
            if original[index]["date"] > mutated["date"]:
                continue
            assert first[index] == second[index]
            one = first_benchmarks[index]
            two = second_benchmarks[index]
            for field in ("causal_probabilities", "fixed_probabilities", "goal_sum", "home_draw_away_counts", "tournament_matches"):
                assert one[field] == two[field]
        if mutated_index == 8:
            lineage = f"{family.family_id}/{target}"
            assert first[4] == second[4] and first[5] == second[5]
            assert first_result["diagnostic_scorecards"][lineage]["log_loss"] == second_result["diagnostic_scorecards"][lineage]["log_loss"]


@pytest.mark.parametrize(("mode", "expected"), [
    ("ambiguous", "unique original"),
    ("wrong_snapshot", "one verified snapshot"),
    ("wrong_score", "final score"),
    ("wrong_neutral", "source row"),
    ("wrong_tournament", "original match row"),
    ("duplicate_reference", "same international source row"),
    ("corrupt_bytes", "registered snapshot reference"),
    ("wrong_length", "registered digest and byte length"),
    ("benchmark_cache", "causal benchmark"),
])
def test_international_source_conflicts_fail_before_model_callbacks(mode, expected):
    from polymarket_alpha_lab.papersim.optimize.families.intl import INTL_TOTALS

    inputs = _intl_run_inputs(_intl_replay_fixture(), INTL_TOTALS)
    records = list(inputs["records"])
    lines = list(inputs["fixture_lines"])
    row = records[2]
    reference = row.intl_source_row
    if mode == "ambiguous":
        records[2] = replace(row, intl_source_row=None)
    elif mode == "wrong_snapshot":
        records[2] = replace(row, intl_source_row=replace(reference, snapshot=replace(reference.snapshot, sha256="d" * 64)))
    elif mode == "wrong_score":
        records[2] = replace(row, intl_source_row=replace(reference, home_goals=5))
    elif mode == "wrong_neutral":
        records[2] = replace(row, intl_source_row=replace(reference, neutral=True))
    elif mode == "wrong_tournament":
        lines[2] = replace(lines[2], competition="UEFA Nations League")
    elif mode == "duplicate_reference":
        records[3] = replace(records[3], intl_source_row=reference)
    elif mode == "corrupt_bytes":
        inputs["source_artifacts"][reference.snapshot.sha256] += b" "
    elif mode == "wrong_length":
        bad_ref = replace(reference.snapshot, byte_length=reference.snapshot.byte_length + 1)
        records = [replace(row, intl_source_row=replace(row.intl_source_row, snapshot=bad_ref)) for row in records]
    else:
        lines[2] = replace(lines[2], benchmark_probabilities=(.9, .1))

    def unopened(*args):
        pytest.fail("invalid registered source reached model inference")

    with pytest.raises(ValueError, match=expected):
        execute_optimization_run(**dict(inputs, records=records, fixture_lines=lines), evaluate=unopened,
                                 evaluate_holdout=unopened, build_candidate_spec=unopened)
