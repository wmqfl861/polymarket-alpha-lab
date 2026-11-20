"""MO-8 promotion policy proofs (plan 05 node MO-8; design 04 sections 5/6).

Offline and pure: every table-driven boundary of the registered rule
numbers, the monotonicity and no-early-promotion properties, the
look-key vs evidence-ordinal semantics (review P2-1), the freeze and
shadow-expiry interplay (review P3-8), the rollback triggers and the
block/span derivations.  The registered-number cross-check against
DELIVERY_PLAN 80.7 lives in test_papersim_optimize_policy_numbers.py.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from polymarket_alpha_lab.papersim.optimize.policy import (
    BLOCKED_FROZEN,
    BLOCKED_HANDOVER_WINDOW,
    BLOCKED_INTEGRITY,
    BLOCKED_LINEAGE_PAUSED,
    BLOCKED_SEASON_FROZEN,
    CLOSE_DEVIATION_MIN_GAP,
    FIRST_EVIDENCE_CI_PERCENTILES,
    FORWARD_THRESHOLDS,
    HANDOVER_WINDOW_DAYS,
    OWNER_CONFIRM_QUEUE_ENTRIES,
    Verdict,
    classify_threshold,
    complete_matchweek_blocks,
    decide_forward,
    decide_rollback,
    forward_look_due,
    intl_window_blocks,
    rollback_look_due,
    span_days_of,
)

AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
REGISTERED = datetime(2026, 11, 1, tzinfo=UTC)
WATERMARK = datetime(2026, 11, 19, tzinfo=UTC)
OU = classify_threshold("club-soccer-frozen", "over_under")
WDL = classify_threshold("club-soccer-recalib", "match_winner")
NBA = classify_threshold("nba-pace-efficiency", "moneyline")
INTL = classify_threshold("intl-elo-poisson", "match_winner")
CRYPTO = classify_threshold("crypto-gbm", "touch")


def digest_of(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


ACTIVE = digest_of("active-spec")
CANDIDATE = digest_of("candidate-spec")
GUARD = digest_of("guard-spec")
NEW_ACTIVE = CANDIDATE


def facts(**overrides) -> object:
    base = dict(
        lineage="club-soccer-frozen/over_under",
        active_spec=ACTIVE,
        guard_spec=None,
        candidate_specs=(CANDIDATE,),
        candidate_registered_at={CANDIDATE: REGISTERED},
        last_handover_at=None,
        frozen=False,
        lineage_paused_or_stopped=False,
        active_queue_entries=0,
        touches_season_frozen_params=False,
        integrity_ok=True,
        guard_integrity_regression=False,
        guard_validation_gate_regression=False,
    )
    base.update(overrides)
    from polymarket_alpha_lab.papersim.optimize.policy import LineageFacts
    return LineageFacts(**base)


def stats(**overrides) -> object:
    base = dict(
        spec=CANDIDATE,
        comparator=ACTIVE,
        n_markets=200,
        n_event_clusters=160,
        n_complete_blocks=4,
        span_days=30,
        watermark=WATERMARK,
        paired_mean=-0.02,
        ci95=(-0.05, 0.001),
        ci99=(-0.06, 0.002),
        close_dev_markets=0,
        close_dev_gap=None,
        unpaired=0,
    )
    base.update(overrides)
    from polymarket_alpha_lab.papersim.optimize.policy import ForwardStats
    return ForwardStats(**base)


# ---------------------------------------------------------------------------
# Table-driven boundaries of every registered condition
# ---------------------------------------------------------------------------


def test_promote_mean_boundary_is_inclusive_zero():
    passing = decide_forward(facts(), stats(paired_mean=0.0), look_number=1,
                             evidence_ordinal=0, threshold=OU, as_of=AT)
    assert passing.conclusion == "PROMOTE"
    failing = decide_forward(facts(), stats(paired_mean=1e-9), look_number=1,
                             evidence_ordinal=0, threshold=OU, as_of=AT)
    assert failing.conclusion == "CONTINUE"


def test_promote_interval_upper_boundary_is_inclusive_plus_0005():
    # ordinal 1 applies the 95% interval.
    passing = decide_forward(facts(), stats(ci95=(-0.05, 0.005)),
                             look_number=2, evidence_ordinal=1,
                             threshold=OU, as_of=AT)
    assert passing.conclusion == "PROMOTE"
    failing = decide_forward(facts(), stats(ci95=(-0.05, 0.0051)),
                             look_number=2, evidence_ordinal=1,
                             threshold=OU, as_of=AT)
    assert failing.conclusion == "RETIRE"


def test_first_evidence_look_applies_the_tightened_99_interval():
    # ci95 would pass but ci99 does not: the FIRST evidence look fails.
    first = decide_forward(facts(), stats(ci95=(-0.05, 0.001),
                                          ci99=(-0.06, 0.006)),
                           look_number=1, evidence_ordinal=0,
                           threshold=OU, as_of=AT)
    assert first.conclusion == "CONTINUE"
    assert first.interval_level == "ci99"
    # The SAME numbers at the second (final) evidence look promote on 95%.
    second = decide_forward(facts(), stats(ci95=(-0.05, 0.001),
                                           ci99=(-0.06, 0.006)),
                            look_number=2, evidence_ordinal=1,
                            threshold=OU, as_of=AT)
    assert second.conclusion == "PROMOTE"
    assert second.interval_level == "ci95"


def test_blocked_look_does_not_consume_the_evidence_ordinal():
    # A frozen look 1 writes BLOCKED; the next key is STILL the first
    # evidence judgement and applies 99% (review P2-1).
    frozen = decide_forward(facts(frozen=True), stats(ci95=(-0.05, 0.001),
                                                     ci99=(-0.06, 0.006)),
                            look_number=1, evidence_ordinal=0,
                            threshold=OU, as_of=AT)
    assert frozen.conclusion == "BLOCKED"
    assert frozen.block_reason == BLOCKED_FROZEN
    after = decide_forward(facts(), stats(ci95=(-0.05, 0.001),
                                          ci99=(-0.06, 0.006)),
                           look_number=2, evidence_ordinal=0,
                           threshold=OU, as_of=AT)
    assert after.conclusion == "CONTINUE"
    assert after.interval_level == "ci99"


def test_close_deviation_boundary_and_the_thirty_market_flag():
    good = decide_forward(facts(), stats(close_dev_markets=30,
                                         close_dev_gap=CLOSE_DEVIATION_MIN_GAP),
                          look_number=1, evidence_ordinal=0,
                          threshold=OU, as_of=AT)
    assert good.conclusion == "CONTINUE"  # gap == -0.01: blocked (inclusive)
    better = decide_forward(facts(), stats(close_dev_markets=30,
                                           close_dev_gap=-0.009),
                            look_number=1, evidence_ordinal=0,
                            threshold=OU, as_of=AT)
    assert better.conclusion == "PROMOTE"
    # Under 30 would-bet markets the same violation is annotate-only.
    flagged = decide_forward(facts(), stats(close_dev_markets=29,
                                            close_dev_gap=-0.5),
                             look_number=1, evidence_ordinal=0,
                             threshold=OU, as_of=AT)
    assert flagged.conclusion == "PROMOTE"
    assert flagged.metrics["close_dev_flagged_only"] is True
    # A missing gap is annotation, never a hard block.
    missing = decide_forward(facts(), stats(close_dev_markets=40,
                                            close_dev_gap=None),
                             look_number=1, evidence_ordinal=0,
                             threshold=OU, as_of=AT)
    assert missing.conclusion == "PROMOTE"
    assert missing.metrics["close_dev_flagged_only"] is True


def test_owner_confirm_threshold_at_exactly_thirty_entries():
    below = decide_forward(facts(active_queue_entries=29), stats(),
                           look_number=1, evidence_ordinal=0,
                           threshold=OU, as_of=AT)
    assert below.conclusion == "PROMOTE"
    at = decide_forward(facts(active_queue_entries=30), stats(),
                        look_number=1, evidence_ordinal=0,
                        threshold=OU, as_of=AT)
    assert at.conclusion == "PROMOTE_PENDING_CONFIRM"
    assert OWNER_CONFIRM_QUEUE_ENTRIES == 30


def test_handover_window_boundary_28_days():
    inside = decide_forward(
        facts(last_handover_at=AT - timedelta(days=HANDOVER_WINDOW_DAYS - 1)),
        stats(), look_number=1, evidence_ordinal=0, threshold=OU, as_of=AT)
    assert inside.conclusion == "BLOCKED"
    assert inside.block_reason == BLOCKED_HANDOVER_WINDOW
    boundary = decide_forward(
        facts(last_handover_at=AT - timedelta(days=HANDOVER_WINDOW_DAYS)),
        stats(), look_number=1, evidence_ordinal=0, threshold=OU, as_of=AT)
    assert boundary.conclusion == "PROMOTE"


def test_look_due_boundaries_per_family_row():
    # ou row: 150 markets and 2 complete blocks.
    assert not forward_look_due(stats(n_markets=149, n_complete_blocks=2), OU, 0)
    assert not forward_look_due(stats(n_markets=150, n_complete_blocks=1), OU, 0)
    assert forward_look_due(stats(n_markets=150, n_complete_blocks=2), OU, 0)
    # second look at double the sample, blocks stay at the base level.
    assert not forward_look_due(stats(n_markets=299, n_complete_blocks=2), OU, 1)
    assert forward_look_due(stats(n_markets=300, n_complete_blocks=2), OU, 1)
    # no third evidence look.
    assert not forward_look_due(stats(n_markets=10_000, n_complete_blocks=99), OU, 2)
    # nba row: 120 markets and 14-day span.
    assert not forward_look_due(stats(n_markets=120, n_complete_blocks=0,
                                      span_days=13), NBA, 0)
    assert forward_look_due(stats(n_markets=120, span_days=14), NBA, 0)
    # crypto row: 60 markets and 28-day span.
    assert not forward_look_due(stats(n_markets=60, span_days=27), CRYPTO, 0)
    assert forward_look_due(stats(n_markets=60, span_days=28), CRYPTO, 0)
    # wdl row doubles to 600.
    assert not forward_look_due(stats(n_markets=599, n_complete_blocks=2), WDL, 1)
    assert forward_look_due(stats(n_markets=600, n_complete_blocks=2), WDL, 1)
    assert rollback_look_due(stats(n_markets=60, span_days=28), CRYPTO)


def test_family_rows_split_by_target_inside_one_family():
    # recalib's match_winner is the 300-market WDL row while its
    # over_under shares the 150-market OU row (review P3-9).
    assert WDL.min_markets == 300 and WDL.family_class == "club-soccer-wdl"
    assert OU.min_markets == 150 and OU.family_class == "club-soccer-ou"
    assert NBA.max_shadow_days == 70 and INTL.max_shadow_days == 365
    assert CRYPTO.max_shadow_days == 182 and OU.max_shadow_days == 84
    with pytest.raises(ValueError):
        classify_threshold("club-soccer-frozen", "match_winner")
    with pytest.raises(ValueError):
        classify_threshold("unknown-family", "over_under")


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def _promoted(verdict: Verdict) -> bool:
    return verdict.conclusion in ("PROMOTE", "PROMOTE_PENDING_CONFIRM")


def test_more_negative_paired_diffs_never_hurt_the_candidate():
    for start_mean, start_hi in ((-0.01, 0.004), (0.0, 0.005),
                                 (-0.005, 0.0), (-0.02, 0.001)):
        base = stats(paired_mean=start_mean, ci95=(start_mean - 0.04, start_hi),
                     ci99=(start_mean - 0.05, start_hi + 0.001))
        baseline = decide_forward(facts(), base, look_number=1,
                                  evidence_ordinal=0, threshold=OU, as_of=AT)
        for delta in (0.0001, 0.001, 0.01, 0.1, 1.0):
            shifted = stats(
                paired_mean=start_mean - delta,
                ci95=(start_mean - delta - 0.04, start_hi - delta),
                ci99=(start_mean - delta - 0.05, start_hi + 0.001 - delta))
            better = decide_forward(facts(), shifted, look_number=1,
                                    evidence_ordinal=0, threshold=OU, as_of=AT)
            assert not (_promoted(baseline) and not _promoted(better)), (
                start_mean, start_hi, delta)


def test_insufficient_sample_is_never_due_regardless_of_numbers():
    # However good the paired numbers, no look is due below the row's
    # minimum: the scheduler refuses before any promotion can exist.
    for n_markets in range(0, 150):
        assert not forward_look_due(
            stats(n_markets=n_markets, paired_mean=-99.0,
                  ci95=(-99.0, -99.0), ci99=(-99.0, -99.0)), OU, 0)
    for n_markets in range(150, 300):
        assert not forward_look_due(
            stats(n_markets=n_markets, paired_mean=-99.0,
                  ci95=(-99.0, -99.0), ci99=(-99.0, -99.0)), OU, 1)


# ---------------------------------------------------------------------------
# Hard blocks, freeze and shadow expiry (P3-8), pause inheritance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("overrides,reason", [
    ({"lineage_paused_or_stopped": True}, BLOCKED_LINEAGE_PAUSED),
    ({"frozen": True}, BLOCKED_FROZEN),
    ({"touches_season_frozen_params": True}, BLOCKED_SEASON_FROZEN),
    ({"integrity_ok": False}, BLOCKED_INTEGRITY),
])
def test_each_hard_block_overrides_an_otherwise_passing_look(overrides, reason):
    verdict = decide_forward(facts(**overrides), stats(), look_number=1,
                             evidence_ordinal=0, threshold=OU, as_of=AT)
    assert verdict.conclusion == "BLOCKED"
    assert verdict.block_reason == reason
    assert verdict.metrics == {}


def test_pause_survives_a_handover_lineage_level_not_model_level():
    # The pause fact is lineage-level: after a handover (new active
    # digest, new model_version) the SAME lineage facts still block.
    after_handover = facts(lineage_paused_or_stopped=True,
                           active_spec=NEW_ACTIVE,
                           candidate_specs=(),
                           candidate_registered_at={})
    # No candidate left to judge post-handover; judge the pre-handover
    # shape instead: the paused lineage blocks its candidate's look.
    blocked = decide_forward(facts(lineage_paused_or_stopped=True), stats(),
                             look_number=1, evidence_ordinal=0,
                             threshold=OU, as_of=AT)
    assert blocked.block_reason == BLOCKED_LINEAGE_PAUSED
    assert after_handover.lineage_paused_or_stopped is True


def test_shadow_expiry_retires_even_while_frozen():
    expired = stats(watermark=REGISTERED + timedelta(days=85))
    verdict = decide_forward(facts(frozen=True), expired, look_number=1,
                             evidence_ordinal=0, threshold=OU, as_of=AT)
    assert verdict.conclusion == "RETIRE"
    assert verdict.metrics["reason"] == "shadow_window_expired"
    assert verdict.metrics["max_shadow_days"] == 84
    # One day inside the window and the freeze wins again.
    not_expired = stats(watermark=REGISTERED + timedelta(days=84))
    frozen = decide_forward(facts(frozen=True), not_expired, look_number=1,
                            evidence_ordinal=0, threshold=OU, as_of=AT)
    assert frozen.conclusion == "BLOCKED"


def test_second_look_failure_retires_and_first_continues():
    first = decide_forward(facts(), stats(paired_mean=0.02), look_number=1,
                           evidence_ordinal=0, threshold=OU, as_of=AT)
    assert first.conclusion == "CONTINUE"
    second = decide_forward(facts(), stats(paired_mean=0.02), look_number=2,
                            evidence_ordinal=1, threshold=OU, as_of=AT)
    assert second.conclusion == "RETIRE"


# ---------------------------------------------------------------------------
# Rollback looks
# ---------------------------------------------------------------------------


def rollback_pair(**overrides):
    return facts(active_spec=NEW_ACTIVE, guard_spec=GUARD, **overrides), stats(
        spec=NEW_ACTIVE, comparator=GUARD, n_markets=200,
        n_complete_blocks=4, ci95=(0.001, 0.05), paired_mean=0.02,
        **overrides.get("_stats", {}))


def test_rollback_triggers():
    base_facts, base_stats = rollback_pair()
    significant = decide_rollback(base_facts, base_stats, look_number=1,
                                  threshold=OU, as_of=AT)
    assert significant.conclusion == "ROLLBACK"  # lower bound > 0
    hold = decide_rollback(base_facts, stats(spec=NEW_ACTIVE, comparator=GUARD,
                                             n_markets=200, n_complete_blocks=4,
                                             ci95=(0.0, 0.05)),
                           look_number=1, threshold=OU, as_of=AT)
    assert hold.conclusion == "HOLD"  # boundary: lower == 0 is NOT significant
    regression = decide_rollback(
        facts(active_spec=NEW_ACTIVE, guard_spec=GUARD,
              guard_integrity_regression=True),
        stats(spec=NEW_ACTIVE, comparator=GUARD, n_markets=1,
              ci95=(None, None), paired_mean=None),
        look_number=1, threshold=OU, as_of=AT)
    assert regression.conclusion == "ROLLBACK"
    gates = decide_rollback(
        facts(active_spec=NEW_ACTIVE, guard_spec=GUARD,
              guard_validation_gate_regression=True),
        stats(spec=NEW_ACTIVE, comparator=GUARD, n_markets=1,
              ci95=(None, None), paired_mean=None),
        look_number=1, threshold=OU, as_of=AT)
    assert gates.conclusion == "ROLLBACK"


def test_rollback_only_respects_the_freeze_block():
    frozen_facts, frozen_stats = rollback_pair(frozen=True)
    frozen = decide_rollback(frozen_facts, frozen_stats, look_number=1,
                             threshold=OU, as_of=AT)
    assert frozen.conclusion == "BLOCKED"
    assert frozen.block_reason == BLOCKED_FROZEN
    # Pause, a fresh handover and season-frozen params do NOT block a
    # rollback look (28-day window excludes rollbacks -- ruling 1).
    unblocked, unblocked_stats = rollback_pair(
        lineage_paused_or_stopped=True,
        last_handover_at=AT - timedelta(days=1),
        touches_season_frozen_params=True)
    assert decide_rollback(unblocked, unblocked_stats, look_number=1,
                           threshold=OU, as_of=AT).conclusion == "ROLLBACK"


def test_rollback_input_contracts():
    with pytest.raises(ValueError):
        decide_rollback(facts(), stats(), look_number=1, threshold=OU, as_of=AT)
    with pytest.raises(ValueError):
        decide_rollback(facts(guard_spec=GUARD), stats(), look_number=1,
                        threshold=OU, as_of=AT)


# ---------------------------------------------------------------------------
# Block/span derivations (versioned with the rule)
# ---------------------------------------------------------------------------


def test_complete_matchweek_counts_only_finished_weeks():
    starts = [datetime(2026, 11, 2, 18, tzinfo=UTC),   # ISO week 45 (Mon)
              datetime(2026, 11, 6, 20, tzinfo=UTC)]   # same week 45
    # Watermark Sunday 20:00 of week 45: the week has NOT ended (ends Mon 00).
    assert complete_matchweek_blocks(starts,
                                     datetime(2026, 11, 8, 20, tzinfo=UTC)) == 0
    # One hour into week 46: week 45 is complete -> one block.
    assert complete_matchweek_blocks(starts,
                                     datetime(2026, 11, 9, 0, 30, tzinfo=UTC)) == 1
    # Two separate finished weeks count as two.
    more = starts + [datetime(2026, 11, 16, 18, tzinfo=UTC)]
    assert complete_matchweek_blocks(
        more, datetime(2026, 11, 23, 12, tzinfo=UTC)) == 2


def test_intl_window_gap_rule_and_span():
    days = [datetime(2026, 9, 5, 12, tzinfo=UTC),
            datetime(2026, 9, 8, 12, tzinfo=UTC),
            datetime(2026, 9, 12, 12, tzinfo=UTC)]  # gaps 3, 4 -> one window
    assert intl_window_blocks(days) == 1
    split = days + [datetime(2026, 10, 10, 12, tzinfo=UTC)]  # gap 28 -> two
    assert intl_window_blocks(split) == 2
    assert span_days_of(days) == 7
    assert span_days_of([]) == 0


def test_first_look_interval_extraction_uses_the_same_frozen_draws():
    # The 99% pair is extracted from the SAME frozen bootstrap draws as
    # the 95% pair (same seed, same resample count): the 99% interval
    # must bracket the 95% one, on both bounds, for any cluster shape.
    from polymarket_alpha_lab.papersim.evaluation import (
        cluster_bootstrap_mean_ci,
    )
    clusters = [[-0.03, 0.01], [0.02], [-0.05, 0.0, 0.03], [0.04], [-0.02]]
    lo95, hi95 = cluster_bootstrap_mean_ci(clusters)
    lo99, hi99 = cluster_bootstrap_mean_ci(
        clusters, percentiles=FIRST_EVIDENCE_CI_PERCENTILES)
    assert lo99 is not None and hi99 is not None
    assert lo99 <= lo95 <= hi95 <= hi99


# ---------------------------------------------------------------------------
# Verdict shape validation
# ---------------------------------------------------------------------------


def test_verdict_shape_contracts():
    with pytest.raises(ValueError):
        Verdict(conclusion="BLOCKED", look_number=1, evidence_ordinal=0,
                block_reason=None)
    with pytest.raises(ValueError):
        Verdict(conclusion="PROMOTE", look_number=1, evidence_ordinal=0,
                block_reason=BLOCKED_FROZEN)
    with pytest.raises(ValueError):
        Verdict(conclusion="BLOCKED", look_number=1, evidence_ordinal=0,
                block_reason="free-text-reason")
    with pytest.raises(ValueError):
        Verdict(conclusion="MAYBE", look_number=1, evidence_ordinal=0)
    with pytest.raises(ValueError):
        Verdict(conclusion="PROMOTE", look_number=0, evidence_ordinal=0)


def test_threshold_table_covers_exactly_the_registered_inventory():
    # Pinned coverage (families/__init__._FAMILY_MODULES x targets;
    # league-bridge has targets=() and yields no lineage, so no row).
    expected = {
        ("club-soccer-frozen", "over_under"),
        ("club-soccer-frozen", "btts"),
        ("club-soccer-frozen", "team_total"),
        ("club-soccer-recalib", "over_under"),
        ("club-soccer-recalib", "match_winner"),
        ("nba-pace-efficiency", "moneyline"),
        ("nba-pace-efficiency", "totals"),
        ("intl-elo-poisson", "match_winner"),
        ("intl-elo-poisson", "over_under"),
        ("intl-elo-goal-residuals", "match_winner"),
        ("intl-elo-goal-residuals", "over_under"),
        ("crypto-gbm", "touch"),
        ("crypto-gbm", "terminal"),
    }
    assert set(FORWARD_THRESHOLDS) == expected
    for row in FORWARD_THRESHOLDS.values():
        assert row.min_markets > 0 and row.max_shadow_days > 0
        assert row.family_class in ("club-soccer-ou", "club-soccer-wdl",
                                    "nba", "intl", "crypto")
