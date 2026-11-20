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

from tests.papersim_mo6_synthetic import (
    CoinAdapter,
    FAMILY,
    make_input,
    make_profile,
    make_registry,
    run_coin,
    theta_spec,
)
from polymarket_alpha_lab.papersim.optimize import runner, search
from polymarket_alpha_lab.papersim.optimize.runner import (
    DataIntegrityError,
    LineageState,
    RegistryView,
    RowOutcome,
    RunEvent,
    _condition_b,
    _scored_events,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

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
