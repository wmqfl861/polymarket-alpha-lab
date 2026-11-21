"""MO-6 rule tests: the registration rule's own error rates (plan r2 T3,
T4, T8, T12).

The 200-seed false-positive and detection runs walk the REAL runner
core (same nested selection, one-SE shrink, holdout paired gate, Holm
and three-condition registration as the production families) over the
synthetic coin family in tests/papersim_mo6_synthetic.py — never a
re-implementation of the decision path.

Registered acceptance (DELIVERY_PLAN 80.7 / plan 05 MO-6):
- null world (the two parameter sets are EQUALLY good): the share of
  seeds that register a candidate must be <= 5%;
- effect world (the best model is truly better by ~0.02 log loss):
  the share must be >= 80%.

Runtime note: each run is dominated by the frozen 20,000-rep cluster
bootstrap on 60 holdout clusters (~0.3-0.6 s); 200 seeds per world
cost a few minutes by design.  Do not parallelize or reduce: the seed
count IS the registered acceptance.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tests.papersim_mo6_synthetic import (
    FAMILY,
    CoinAdapter,
    make_profile,
    make_registry,
    run_coin,
    run_shaped,
    theta_spec,
)
from polymarket_alpha_lab.papersim.optimize import runner
from polymarket_alpha_lab.papersim.optimize.metrics import holm
from polymarket_alpha_lab.papersim.optimize.runner import RUN_RULES_V1

UTC = dt.timezone.utc
SEEDS = tuple(range(1, 201))


def _registered(result) -> bool:
    return bool(result.registered_lineages)


def test_rule_false_positive_rate_null_world_200_seeds() -> None:
    registered = sum(
        _registered(run_coin(q=0.50, seed=seed)[0]) for seed in SEEDS
    )
    assert registered / len(SEEDS) <= RUN_RULES_V1["fp_rate_max"], (
        f"null world registered {registered}/{len(SEEDS)} seeds"
    )


def test_rule_detection_rate_effect_world_200_seeds() -> None:
    detected = sum(
        _registered(run_coin(q=0.60, seed=seed)[0]) for seed in SEEDS
    )
    assert detected / len(SEEDS) >= RUN_RULES_V1["detection_min"], (
        f"effect world registered {detected}/{len(SEEDS)} seeds"
    )


def test_effect_world_registers_with_recorded_numbers() -> None:
    result, _ = run_coin(q=0.60, seed=21)
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    if record["registered"]:
        gate = record["gate"]
        assert gate["condition_a"] is True
        assert float(gate["paired"]["ci95"][1]) < 0.0
        assert gate["holm_p_adjusted"] is not None
        assert gate["condition_b"]["passed"] is True
        # Empty-envelope synthetic: the width gate is NOT_ASSESSABLE.
        assert gate["condition_c"]["status"] == "not_assessable_empty_envelope"
        assert result.outcome == "CANDIDATE_REGISTERED"


def test_weak_effect_fails_condition_a_without_registration() -> None:
    result, _ = run_coin(q=0.52, seed=31)
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    if record["gate"] is not None and gate_has_verdict(record):
        assert record["registered"] is False
        assert record["blocked_reason"] in (
            "no_better_candidate", "holdout_not_significant",
        )
    assert result.registered_lineages == ()


def gate_has_verdict(record) -> bool:
    gate = record.get("gate")
    return bool(gate) and gate.get("condition_a") is not None


# ---------------------------------------------------------------------------
# T8: the three-condition conjunction, one condition at a time
# ---------------------------------------------------------------------------

def test_condition_c_narrower_envelope_is_owner_decision_not_registration() -> None:
    # A real effect passes condition A, but the winner's variant spread
    # is far below 0.8x the incumbent's: NOT auto-registered, the
    # numbers stay on the scorecard for the owner.
    adapter = CoinAdapter(spreads={0.50: 0.02, 0.60: 0.005})
    result, _ = run_coin(adapter=adapter, q=0.60, seed=21)
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    gate = record["gate"]
    if gate is not None and gate.get("condition_c", {}).get("status") == (
        "narrower_owner_decision"
    ):
        assert record["registered"] is False
        assert record["blocked_reason"] == "envelope_narrower_owner_decision"
        assert float(gate["condition_c"]["ratio"]) < 0.8
        assert result.outcome == "NO_IMPROVEMENT"


def test_condition_c_passes_above_ratio_boundary() -> None:
    adapter = CoinAdapter(spreads={0.50: 0.02, 0.60: 0.017})
    result, _ = run_coin(adapter=adapter, q=0.60, seed=21)
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    gate = record["gate"]
    if gate is not None and gate.get("condition_c"):
        assert float(gate["condition_c"]["ratio"]) == pytest.approx(0.85)
        assert gate["condition_c"]["status"] == "pass"


# ---------------------------------------------------------------------------
# T4: Holm across lineages, the 28-day block, mixed outcomes
# ---------------------------------------------------------------------------

def test_holm_correction_matches_metrics_and_strong_lineage_registers() -> None:
    # Two lineages: coin has a strong true effect, coin2 a weak one.
    # The recorded adjusted p-values must equal metrics.holm over the
    # recorded raw p-values, and the strong lineage may register while
    # the weak one does not.
    adapter = CoinAdapter(targets=("coin", "coin2"))
    registry, incumbent = make_registry(adapter)
    result, _ = run_coin(
        adapter=adapter, q=0.60, q2=0.52, seed=21,
        registry=registry, incumbent=incumbent,
    )
    raw = []
    names = []
    for lineage, record in result.run_scorecard["lineages"].items():
        gate = record.get("gate")
        if gate is not None and gate.get("paired"):
            raw.append(float(gate["paired"]["p_value"]))
            names.append(lineage)
    if len(raw) >= 2:
        _rejected, adjusted = holm(raw, alpha=RUN_RULES_V1["alpha"])
        for index, lineage in enumerate(names):
            gate = result.run_scorecard["lineages"][lineage]["gate"]
            assert gate["holm_p_adjusted"] == pytest.approx(adjusted[index])
    for lineage in result.registered_lineages:
        assert lineage == f"{FAMILY}/coin"


def _recent_run(cutoff: dt.datetime, lineage: str, winner_sha: str,
                registered: bool) -> dict:
    return {
        "data_cutoff": cutoff,
        "scorecard": {
            "lineages": {
                lineage: {"winner_spec": winner_sha, "registered": registered}
            }
        },
    }


def test_resubmission_block_28_days() -> None:
    incumbent = theta_spec(0.50)
    winner = theta_spec(0.60)
    as_of = dt.datetime(2026, 6, 1, tzinfo=UTC)
    blocked = _recent_run(
        as_of - dt.timedelta(days=10), f"{FAMILY}/coin",
        winner.spec_sha256(), registered=False,
    )
    assert runner.resubmission_blocked(
        (blocked,), f"{FAMILY}/coin", winner.spec_sha256(), as_of, days=28
    )
    expired = _recent_run(
        as_of - dt.timedelta(days=29), f"{FAMILY}/coin",
        winner.spec_sha256(), registered=False,
    )
    assert not runner.resubmission_blocked(
        (expired,), f"{FAMILY}/coin", winner.spec_sha256(), as_of, days=28
    )
    promoted = _recent_run(
        as_of - dt.timedelta(days=10), f"{FAMILY}/coin",
        winner.spec_sha256(), registered=True,
    )
    assert not runner.resubmission_blocked(
        (promoted,), f"{FAMILY}/coin", winner.spec_sha256(), as_of, days=28
    )


def test_resubmission_within_window_blocks_registration() -> None:
    winner_sha = theta_spec(0.60).spec_sha256()
    recent = _recent_run(
        dt.datetime(2026, 5, 25, tzinfo=UTC), f"{FAMILY}/coin",
        winner_sha, registered=False,
    )
    result, _ = run_coin(q=0.60, seed=21, recent_runs=(recent,))
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    assert record["blocked_reason"] == "resubmission_within_28d"
    assert result.registered_lineages == ()
    assert result.outcome == "NO_IMPROVEMENT"


def test_insufficient_holdout_reports_data_insufficient() -> None:
    # The club-class floors (300 events / 20 blocks) apply via the
    # family_id argument; the synthetic holdout is trimmed to a late
    # window far below the floor.
    profile = make_profile(
        holdout_start_utc=dt.datetime(2026, 4, 6, tzinfo=UTC)
    )
    result, _ = run_coin(
        q=0.60, seed=21, family_id="club-soccer-recalib", profile=profile,
    )
    record = result.run_scorecard["lineages"]["club-soccer-recalib/coin"]
    assert record["sample_status"] == "insufficient"
    assert record["gate"]["blocked_reason"] == "insufficient_holdout_sample"
    assert result.outcome == "INSUFFICIENT_DATA"
    assert result.registered_lineages == ()


def test_mixed_lineage_outcomes_report_honestly() -> None:
    # One lineage registers (real effect), the other keeps the
    # incumbent (pure noise around its own optimum): the run outcome
    # is CANDIDATE_REGISTERED and the second lineage says so plainly.
    # (Per-target INSUFFICIENT divergence is impossible by
    # construction — the floors are per family and the holdout is
    # shared — so the honest mixed case is registered + no_better.)
    adapter = CoinAdapter(targets=("coin", "coin2"))
    registry, incumbent = make_registry(adapter)
    result, _ = run_coin(
        adapter=adapter, q=0.60, q2=0.50, seed=21,
        registry=registry, incumbent=incumbent,
    )
    if result.registered_lineages == (f"{FAMILY}/coin",):
        other = result.run_scorecard["lineages"][f"{FAMILY}/coin2"]
        assert other["registered"] is False
        assert result.outcome == "CANDIDATE_REGISTERED"


# ---------------------------------------------------------------------------
# Review P1-1: the forward plan is per LINEAGE (letter assertions)
# ---------------------------------------------------------------------------

def test_forward_plan_numbers_are_per_lineage_letters() -> None:
    from polymarket_alpha_lab.papersim.optimize.runner import _forward_plan

    as_of = dt.datetime(2026, 11, 21, tzinfo=UTC)
    letters = {
        # (family, target): (first, final, shadow days) — 80.7 rows.
        ("club-soccer-recalib", "match_winner"): (300, 600, 84),
        ("club-soccer-frozen", "match_winner"): (300, 600, 84),
        ("club-soccer-recalib", "over_under"): (150, 300, 84),
        ("club-soccer-frozen", "btts"): (150, 300, 84),
        ("club-soccer-frozen", "team_total"): (150, 300, 84),
        ("nba-pace-efficiency", "moneyline"): (120, 240, 70),
        ("nba-pace-efficiency", "totals"): (120, 240, 70),
        ("intl-elo-poisson", "match_winner"): (60, 120, 365),
        ("intl-elo-goal-residuals", "over_under"): (60, 120, 365),
        ("crypto-gbm", "touch"): (60, 120, 182),
        ("crypto-gbm", "terminal"): (60, 120, 182),
    }
    for (family_id, target), (first, final, days) in letters.items():
        plan = _forward_plan(family_id, target, as_of)
        assert plan["first_look_min_events"] == first, (family_id, target)
        assert plan["final_look_min_events"] == final, (family_id, target)
        assert plan["max_shadow_days"] == days, (family_id, target)
        assert plan["primary_metric"] == "mean_log_loss"
    # The transcription pin and the policy table agree row by row (the
    # T12 closing direction: one source, the other the pin).
    stage2 = RUN_RULES_V1["stage2_forward_minimums"]
    assert stage2["club-soccer(match_winner)"]["min_events"] == 300
    assert stage2["club-soccer(ou/btts/tt)"]["min_events"] == 150
    assert stage2["nba"]["min_events"] == 120
    assert stage2["intl"]["min_events"] == 60
    assert stage2["crypto"]["min_events"] == 60
    # A test-double family with no policy row falls back to the
    # family-class default instead of guessing a policy row.
    fallback = _forward_plan(FAMILY, "coin", as_of)
    assert fallback["first_look_min_events"] == 60  # the else-class default


# ---------------------------------------------------------------------------
# Review P1-2: the holdout floor counts MATCHES, never rows
# ---------------------------------------------------------------------------

def test_holdout_floor_counts_matches_not_lines() -> None:
    # 100 matches x 3 lines each = 300 rows: the row-count reading met
    # the 300 floor; the registered unit is MATCHES (场), so this is
    # INSUFFICIENT_DATA with the honest numbers on the record.
    result = run_shaped(
        q=0.60, seed=41, wf_blocks=80, holdout_blocks=100,
        rows_per_event=3, family_id="club-soccer-recalib",
    )
    record = result.run_scorecard["lineages"]["club-soccer-recalib/coin"]
    assert record["sample_status"] == "insufficient"
    sample = record["gate"]["sample"]
    assert sample["holdout_events"] == 100
    assert sample["holdout_rows"] == 300
    assert sample["min_events"] == 300
    assert result.outcome == "INSUFFICIENT_DATA"
    assert result.registered_lineages == ()


def test_synthetic_family_has_no_registered_floor() -> None:
    # The synthetic family itself has no registered floor (synthetic
    # id): the sample record still reports matches AND rows honestly,
    # and no insufficient path bites.
    result = run_shaped(
        q=0.60, seed=41, wf_blocks=80, holdout_blocks=100,
        rows_per_event=3,
    )
    record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
    assert record["sample_status"] == "ok"
    if record.get("gate") is not None:
        assert record["gate"]["sample"]["holdout_events"] == 100
        assert record["gate"]["sample"]["holdout_rows"] == 300


# ---------------------------------------------------------------------------
# T12: the registered rule-v1 numbers, pinned literally (MO-8 closing pin)
# ---------------------------------------------------------------------------

def test_run_rules_v1_numbers_are_the_registered_table() -> None:
    # DELIVERY_PLAN 80.7, five rows with the club WDL row's "同上"
    # expanded to the same numbers (NO sixth invented group).
    assert RUN_RULES_V1["stage1_holdout_minimums"] == {
        "club-soccer-recalib": {"min_events": 300, "min_blocks": 20},
        "club-soccer-frozen": {"min_events": 300, "min_blocks": 20},
        "intl-elo-poisson": {"min_events": 300, "min_blocks": 0},
        "intl-elo-goal-residuals": {"min_events": 300, "min_blocks": 0},
        "nba-pace-efficiency": {"min_events": 300, "min_blocks": 30},
        "crypto-gbm": {"min_events": 150, "min_blocks": 20},
    }
    assert RUN_RULES_V1["alpha"] == 0.05
    assert RUN_RULES_V1["envelope_width_ratio"] == 0.8
    assert RUN_RULES_V1["resubmission_days"] == 28
    assert RUN_RULES_V1["fp_rate_max"] == 0.05
    assert RUN_RULES_V1["detection_min"] == 0.80
    # Stage-2 numbers transcribed verbatim for MO-8's policy.py
    # convergence (MO-6 does not enforce them).
    assert RUN_RULES_V1["stage2_forward_minimums"] == {
        "club-soccer(ou/btts/tt)": {"min_events": 150, "min_blocks": 2},
        "club-soccer(match_winner)": {"min_events": 300, "min_blocks": 2},
        "nba": {"min_events": 120, "min_blocks": 14},
        "intl": {"min_events": 60, "min_blocks": 2},
        "crypto": {"min_events": 60, "min_blocks": 28},
    }
    assert RUN_RULES_V1["max_shadow_windows"] == {
        "club": "12w", "nba": "10w", "intl": "12mo", "crypto": "6mo",
    }
    assert RUN_RULES_V1["promote_ci_upper_max"] == 0.005
    assert RUN_RULES_V1["first_look_ci_tighten"] == "99pct"
    assert RUN_RULES_V1["rule_version"] == "v1"


@pytest.mark.parametrize(("candidate", "incumbent", "status", "ratio", "managed", "outcome"), [
    (0.01732755748083972, 0.02165944685104965, "narrower_owner_decision",
     "0.7999999999999999", "ok", "NO_IMPROVEMENT"),
    (0.0, 0.0, "narrower_owner_decision", None, "ok", "NO_IMPROVEMENT"),
    (None, None, "not_assessable_empty_envelope", None, "not_assessable", "CANDIDATE_REGISTERED"),
    (None, 0.5, "narrower_owner_decision", None, "not_assessable", "NO_IMPROVEMENT"),
    (0.5, None, None, None, "not_assessable", "RUN_FAILED"),
])
def test_envelope_paths_keep_their_original_arithmetic(candidate, incumbent, status, ratio, managed, outcome):
    adapter = CoinAdapter(spreads={0.50: incumbent, 0.60: candidate})
    result, _ = run_coin(adapter=adapter, q=0.60, seed=21)
    assert result.outcome == outcome
    assert runner.envelope_status(candidate, incumbent) == managed
    if status is None:
        assert result.error_code == "envelope_shape_illegal"
        assert not result.registered_lineages
    else:
        record = result.run_scorecard["lineages"][f"{FAMILY}/coin"]
        assert record["gate"]["condition_c"] == {
            "candidate_spread": None if candidate is None else repr(candidate),
            "incumbent_spread": None if incumbent is None else repr(incumbent),
            "ratio": ratio, "status": status,
        }
        assert record["registered"] is (outcome == "CANDIDATE_REGISTERED")
        if status == "narrower_owner_decision":
            assert record["blocked_reason"] == "envelope_narrower_owner_decision"


@pytest.mark.parametrize(("offset_us", "legacy", "managed"), [
    (-1, True, True), (0, True, False), (1, False, False),
])
def test_resubmission_paths_keep_their_original_28_day_endpoints(offset_us, legacy, managed):
    cutoff = dt.datetime(2026, 9, 1, tzinfo=UTC)
    rejected_at = cutoff + dt.timedelta(hours=5)
    delta = dt.timedelta(days=28, microseconds=offset_us)
    candidate = theta_spec(0.60).spec_sha256()
    lineage = f"{FAMILY}/coin"
    runs = (_recent_run(cutoff, lineage, candidate, registered=False),)
    assert runner.resubmission_blocked(
        runs, lineage, candidate, cutoff + delta, days=RUN_RULES_V1["resubmission_days"],
    ) is legacy
    receipt = runner.RejectionReceipt(
        f"or|{FAMILY}|prior", FAMILY, lineage, candidate, "receipt/7", "d" * 64, rejected_at,
    )
    def bans(now, *, run_key=f"or|{FAMILY}|current"):
        return runner.banned_spec_digests((receipt,), now=now, family=FAMILY, run_key=run_key)

    assert (candidate in bans(rejected_at + delta)) is managed
    assert not bans(rejected_at - dt.timedelta(microseconds=1))
    assert candidate in bans(rejected_at)
    assert not bans(rejected_at, run_key=receipt.run_key)
    if offset_us == 0:
        # Compare at one physical instant while the two clocks differ.
        assert candidate in bans(cutoff + delta)
        same_time = rejected_at + delta
        assert not runner.resubmission_blocked(runs, lineage, candidate, same_time, days=28)
        assert candidate not in bans(same_time)


@pytest.mark.parametrize(("family", "targets", "min_events", "min_blocks"), [
    ("club-soccer", ("club-soccer-recalib", "club-soccer-frozen"), 300, 20),
    ("crypto", ("crypto-gbm",), 150, 20),
    ("intl", ("intl-elo-poisson", "intl-elo-goal-residuals"), 300, 0),
    ("nba", ("nba-pace-efficiency",), 300, 30),
])
def test_each_stage1_floor_requires_the_registered_event_and_block_minimum(family, targets, min_events, min_blocks):
    assert runner.resolve_stage1_floors(family) == (min_events, min_blocks)
    for target in targets:
        assert runner.resolve_stage1_floors(target) == (min_events, min_blocks)
        assert RUN_RULES_V1["stage1_holdout_minimums"][target] == {
            "min_events": min_events, "min_blocks": min_blocks,
        }
    for events, blocks, expected in (
        (min_events - 1, min_blocks, False),
        (min_events, min_blocks - 1, False),
        (min_events, min_blocks, True),
    ):
        stat = runner.LineageStat(f"{targets[0]}/synthetic", events, blocks, -0.01, 0.001)
        decisions, insufficient = runner.evaluate_stage1_rule(
            (stat,), min_events=min_events, min_blocks=min_blocks,
        )
        assert insufficient is not expected
        assert decisions[0].floors_met is decisions[0].registered is expected


@pytest.mark.parametrize(("upper", "passes"), [
    (None, False), (-1e-20, True), (-0.0, False), (0.0, False), (1e-20, False),
])
def test_stage1_zero_is_excluded_with_and_without_legacy_serialization(upper, passes):
    lineage = "synthetic/target"
    stats = runner.LineageStat(lineage, 150, 20, upper, 0.005)
    decisions, insufficient = runner.evaluate_stage1_rule((stats,), min_events=150, min_blocks=20)
    assert not insufficient
    assert decisions[0].ci_passes is decisions[0].registered is passes

    record = {"_holm_p": 0.005, "gate": {"paired": {"ci95": [None, None if upper is None else repr(upper)]}}}
    runner._apply_holm({lineage: record}, seed=7)
    assert record["gate"]["condition_a"] is passes
    assert record["gate"]["holm_p_adjusted"] == "0.005"


@pytest.mark.parametrize(("family", "target", "min_events", "day_window"), [
    ("club-soccer-future", "match_winner", 300, 84),
    ("club-soccer-future", "future_target", 150, 84),
    ("nbaFuture", "future_target", 120, 70),
    ("intlFuture", "future_target", 60, 365),
    ("synthetic", "future_target", 60, 182),
])
def test_unregistered_forward_pair_uses_legacy_fallback_and_managed_rejection(family, target, min_events, day_window):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize import policy

    at = dt.datetime(2026, 11, 21, tzinfo=UTC)
    assert runner._forward_plan(family, target, at) == {
        "primary_metric": "mean_log_loss", "start": at.isoformat(),
        "first_look_min_events": min_events, "final_look_min_events": min_events * 2,
        "max_shadow_days": day_window, "rule_version": "v1",
    }
    for plan in (
        lambda: policy.classify_threshold(family, target),
        lambda: store_psycopg._optimizer_forward_plan({"family": family}, f"{family}/{target}", at, "a", "b"),
    ):
        with pytest.raises(ValueError, match="^papersim_policy_threshold_unknown$"):
            plan()


def test_managed_holdout_floor_requires_valid_unambiguous_override():
    with pytest.raises(ValueError, match="pass floor_class explicitly"):
        runner.resolve_stage1_floors("synthetic")
    with pytest.raises(ValueError, match="unknown stage-1 floor class"):
        runner.resolve_stage1_floors("synthetic", floor_class="unregistered")
    with pytest.raises(ValueError, match="requires the 'club-soccer' stage-1 floors"):
        runner.resolve_stage1_floors("club-soccer-future", floor_class="intl")
    assert runner.resolve_stage1_floors("synthetic", floor_class="intl") == (300, 0)
