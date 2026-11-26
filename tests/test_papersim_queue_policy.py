"""FX-17 cross-league pause decision layer tests (M2 node C1).

Covers the pure ``papersim.queue_policy`` contract (M2 plan section
3.8, OD-G numbers registered 2026-11-13, ``DELIVERY_PLAN.md`` 77.3.2):

- every rule's equality / adjacent-point / boundary behaviour:
  FX-17 net PnL (settled 9/10, -600 equality vs strictly lower),
  FX-17 CLV (entered 19/20, upper bound 0 vs strictly negative),
  universal 40-entry rule (entered 39/40, CLV clause and the
  $2,500 drawdown equality vs strictly higher);
- UNKNOWN (``None``) metrics never trigger a pause and are never
  treated as 0;
- FX-17 branch isolation: only CROSS_LEAGUE queues act on FX-17
  reasons, DOMESTIC queues report them suppressed; the universal rule
  applies to both scopes;
- latch semantics: a previously written pause survives restart,
  missing data and metric rebound; decisions only stay or escalate;
- multi-queue batches stay independent and input-ordered;
- the decision layer is pure: thresholds are reused from
  ``evaluation.pause_recommendation`` (equivalence grid), no IO, no
  driver import, deterministic;
- the kappa freeze gate: one pre-season digest per season, mid-season
  drift or a missing digest blocks the input while keeping evidence.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import itertools
import socket
import sys

import pytest

from polymarket_alpha_lab.papersim import evaluation as ev
from polymarket_alpha_lab.papersim import queue_policy as qp

_T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def mk(**overrides):
    """One synthetic accepted+settled candidate (evaluation input)."""
    base = dict(
        candidate_id="c1",
        model_key="soccer-v1",
        market_type="terminal",
        gate_version="gates-v1",
        market_key="mk1",
        model_probability=0.65,
        market_mid=0.50,
        accepted=True,
        stake=100.0,
        fill_price=0.50,
        side="YES",
        fee_category="sports",
        fill_provenance=ev.FILL_BOOK_WALK,
        outcome="YES",
        recorded_pnl=100.0,
        decision_time=_T0,
        settled_time=_T0 + timedelta(days=1),
        close_price=None,
        match_key=None,
    )
    base.update(overrides)
    return ev.EvaluationCandidate(**base)


def qi(**overrides):
    """One cross-league queue input with healthy defaults."""
    base = dict(
        queue_id="cross|soccer-v1|terminal|gates-v1|dsv1",
        league_scope=qp.SCOPE_CROSS_LEAGUE,
        model_key="soccer-v1",
        market_type="terminal",
        gate_version="gates-v1",
        data_source_version="dsv1",
        entered=5,
        settled=5,
        clv_upper_bound=0.5,
        max_drawdown=100.0,
        net_pnl=100.0,
    )
    base.update(overrides)
    return qp.QueuePauseInput(**base)


# ---------------------------------------------------------------------------
# Module surface and versions
# ---------------------------------------------------------------------------


def test_frozen_surface_and_versions():
    assert qp.QUEUE_POLICY_RULES_VERSION == "queue-policy-fx17-v1"
    # The thresholds themselves stay in evaluation (OD-G registry); this
    # module must not declare a second set of numbers.
    assert qp.ACTION_CONTINUE == "CONTINUE"
    assert qp.ACTION_PAUSE_REVIEW == "PAUSE_REVIEW"
    assert qp.ACTION_AUTO_STOP_RECOMMEND == "AUTO_STOP_RECOMMEND"
    assert qp.SCOPE_DOMESTIC == "DOMESTIC"
    assert qp.SCOPE_CROSS_LEAGUE == "CROSS_LEAGUE"
    decision = qp.evaluate_pause([qi()])[0]
    assert decision.paper_only is True
    assert decision.report_only is True
    assert decision.readonly is True
    assert decision.rules_version == qp.QUEUE_POLICY_RULES_VERSION
    assert (
        decision.suspension_rules_version == ev.SUSPENSION_RULES_VERSION
    )
    assert "review" in decision.note


# ---------------------------------------------------------------------------
# FX-17 net-PnL clause boundaries
# ---------------------------------------------------------------------------


def test_fx17_net_pnl_settled_boundary_9_vs_10():
    below = qi(settled=10, entered=10, net_pnl=-600.01, clv_upper_bound=0.5)
    assert qp.evaluate_pause([below])[0].action == qp.ACTION_PAUSE_REVIEW
    one_short = qi(settled=9, entered=10, net_pnl=-600.01,
                   clv_upper_bound=0.5)
    assert qp.evaluate_pause([one_short])[0].action == qp.ACTION_CONTINUE


def test_fx17_net_pnl_equality_never_triggers():
    # -600 exactly is not "below -600"; only strictly lower triggers.
    equal = qi(settled=10, entered=10, net_pnl=-600.00, clv_upper_bound=0.5)
    decision = qp.evaluate_pause([equal])[0]
    assert decision.action == qp.ACTION_CONTINUE
    assert decision.review_reasons == ()
    adjacent = qi(settled=10, entered=10, net_pnl=-599.99,
                  clv_upper_bound=0.5)
    assert qp.evaluate_pause([adjacent])[0].action == qp.ACTION_CONTINUE
    just_below = qi(settled=10, entered=10, net_pnl=-600.000001,
                    clv_upper_bound=0.5)
    assert (
        qp.evaluate_pause([just_below])[0].action == qp.ACTION_PAUSE_REVIEW
    )


def test_fx17_net_pnl_unknown_never_triggers():
    unknown = qi(settled=10, entered=10, net_pnl=None, clv_upper_bound=0.5)
    decision = qp.evaluate_pause([unknown])[0]
    assert decision.action == qp.ACTION_CONTINUE
    # UNKNOWN is not 0 and not a trigger: no reason mentions net pnl.
    assert decision.review_reasons == ()


# ---------------------------------------------------------------------------
# FX-17 CLV clause boundaries
# ---------------------------------------------------------------------------


def test_fx17_clv_entry_boundary_19_vs_20():
    triggers = qi(entered=20, settled=5, clv_upper_bound=-0.01, net_pnl=100.0)
    assert qp.evaluate_pause([triggers])[0].action == qp.ACTION_PAUSE_REVIEW
    one_short = qi(entered=19, settled=5, clv_upper_bound=-0.01,
                   net_pnl=100.0)
    assert qp.evaluate_pause([one_short])[0].action == qp.ACTION_CONTINUE


def test_fx17_clv_upper_bound_zero_is_not_negative():
    equal_zero = qi(entered=25, settled=5, clv_upper_bound=0.0, net_pnl=100.0)
    decision = qp.evaluate_pause([equal_zero])[0]
    assert decision.action == qp.ACTION_CONTINUE
    assert decision.review_reasons == ()
    barely_negative = qi(entered=25, settled=5, clv_upper_bound=-0.000001,
                         net_pnl=100.0)
    assert (
        qp.evaluate_pause([barely_negative])[0].action == qp.ACTION_PAUSE_REVIEW
    )


def test_fx17_clv_unknown_never_triggers():
    unknown = qi(entered=25, settled=5, clv_upper_bound=None, net_pnl=100.0)
    decision = qp.evaluate_pause([unknown])[0]
    assert decision.action == qp.ACTION_CONTINUE
    assert decision.review_reasons == ()


# ---------------------------------------------------------------------------
# Universal (blueprint) 40-entry rule boundaries
# ---------------------------------------------------------------------------


def test_universal_entry_boundary_39_vs_40_via_drawdown():
    # CLV neutralized (positive bound) so only the drawdown clause can
    # fire; FX-17 clauses are neutral too (net pnl positive, few settled).
    base = dict(entered=40, settled=5, clv_upper_bound=0.5, net_pnl=100.0,
                max_drawdown=2500.01)
    triggers = qi(**base)
    assert (
        qp.evaluate_pause([triggers])[0].action == qp.ACTION_AUTO_STOP_RECOMMEND
    )
    one_short = qi(**{**base, "entered": 39})
    assert qp.evaluate_pause([one_short])[0].action == qp.ACTION_CONTINUE


def test_universal_drawdown_equality_never_triggers():
    base = dict(entered=40, settled=5, clv_upper_bound=0.5, net_pnl=100.0,
                max_drawdown=2500.00)
    equal = qi(**base)
    decision = qp.evaluate_pause([equal])[0]
    assert decision.action == qp.ACTION_CONTINUE
    assert decision.stop_reasons == ()
    barely_above = qi(**{**base, "max_drawdown": 2500.000001})
    assert (
        qp.evaluate_pause([barely_above])[0].action
        == qp.ACTION_AUTO_STOP_RECOMMEND
    )


def test_universal_clv_clause_at_40_entries():
    base = dict(entered=40, settled=5, clv_upper_bound=-0.01, net_pnl=100.0,
                max_drawdown=100.0)
    decision = qp.evaluate_pause([qi(**base)])[0]
    assert decision.action == qp.ACTION_AUTO_STOP_RECOMMEND
    assert decision.stop_reasons
    zero = qi(**{**base, "clv_upper_bound": 0.0})
    assert qp.evaluate_pause([zero])[0].action == qp.ACTION_CONTINUE


def test_universal_unknown_metrics_never_trigger():
    all_unknown = qi(entered=40, settled=10, clv_upper_bound=None,
                     max_drawdown=None, net_pnl=None)
    decision = qp.evaluate_pause([all_unknown])[0]
    assert decision.action == qp.ACTION_CONTINUE
    assert decision.stop_reasons == () and decision.review_reasons == ()


def test_universal_escalates_over_fx17_review():
    # Both FX-17 and universal clauses fire: auto-stop wins.
    both = qi(entered=40, settled=10, clv_upper_bound=-0.01,
              max_drawdown=3000.0, net_pnl=-700.0)
    decision = qp.evaluate_pause([both])[0]
    assert decision.action == qp.ACTION_AUTO_STOP_RECOMMEND
    assert decision.stop_reasons and decision.review_reasons


# ---------------------------------------------------------------------------
# Cross-league vs domestic isolation
# ---------------------------------------------------------------------------


def test_fx17_only_acts_on_cross_league_queues():
    metrics = dict(entered=10, settled=10, clv_upper_bound=0.5,
                   max_drawdown=100.0, net_pnl=-650.0)
    cross = qp.evaluate_pause([
        qi(**metrics, queue_id="q-cross", league_scope=qp.SCOPE_CROSS_LEAGUE)
    ])[0]
    domestic = qp.evaluate_pause([
        qi(**metrics, queue_id="q-dom", league_scope=qp.SCOPE_DOMESTIC)
    ])[0]
    assert cross.action == qp.ACTION_PAUSE_REVIEW
    assert cross.review_reasons and cross.fx17_suppressed_reasons == ()
    # The identical domestic queue must not use the FX-17 branch.
    assert domestic.action == qp.ACTION_CONTINUE
    assert domestic.review_reasons == ()
    assert domestic.fx17_suppressed_reasons == cross.review_reasons


def test_universal_rule_applies_to_domestic_queues_too():
    domestic = qp.evaluate_pause([
        qi(entered=40, settled=5, clv_upper_bound=0.5, net_pnl=100.0,
           max_drawdown=2500.01, league_scope=qp.SCOPE_DOMESTIC,
           queue_id="q-dom-40")
    ])[0]
    assert domestic.action == qp.ACTION_AUTO_STOP_RECOMMEND
    assert domestic.stop_reasons


def test_domestic_clv_fx17_reasons_are_suppressed_not_acted_on():
    domestic = qp.evaluate_pause([
        qi(entered=25, settled=5, clv_upper_bound=-0.05, net_pnl=100.0,
           league_scope=qp.SCOPE_DOMESTIC, queue_id="q-dom-clv")
    ])[0]
    assert domestic.action == qp.ACTION_CONTINUE
    assert domestic.fx17_suppressed_reasons
    assert all("CLV" in reason for reason in domestic.fx17_suppressed_reasons)
    # The evidence suggestion itself is retained untouched.
    assert domestic.pause_suggestion.fx17_pause_review is True


def test_scope_is_mandatory_two_value_enum():
    with pytest.raises(ValueError, match="league_scope"):
        qi(league_scope="cross-league")
    with pytest.raises(ValueError, match="league_scope"):
        qi(league_scope="")


# ---------------------------------------------------------------------------
# Latch: pause survives restart, missing data and rebound
# ---------------------------------------------------------------------------


def test_latched_pause_survives_metric_rebound():
    healthy = qi(entered=45, settled=40, clv_upper_bound=0.9, net_pnl=500.0,
                 max_drawdown=100.0, pause_review_latched=True,
                 queue_id="q-rebound")
    decision = qp.evaluate_pause([healthy])[0]
    assert decision.action == qp.ACTION_PAUSE_REVIEW
    assert decision.keeps_latched_pause is True


def test_latched_auto_stop_survives_missing_data():
    unknown = qi(entered=45, settled=40, clv_upper_bound=None,
                 max_drawdown=None, net_pnl=None, auto_stop_latched=True,
                 queue_id="q-unknown-latch")
    decision = qp.evaluate_pause([unknown])[0]
    assert decision.action == qp.ACTION_AUTO_STOP_RECOMMEND
    assert decision.keeps_latched_pause is True
    assert decision.stop_reasons == ()  # latch alone holds the stop


def test_latch_never_deescalates_and_can_escalate():
    rebound = qi(entered=45, settled=40, clv_upper_bound=0.9, net_pnl=500.0,
                 max_drawdown=100.0, pause_review_latched=True,
                 queue_id="q-hold")
    held = qp.evaluate_pause([rebound])[0]
    assert held.action == qp.ACTION_PAUSE_REVIEW
    worsened = qi(**{**rebound.__dict__, "max_drawdown": 3000.0})
    escalated = qp.evaluate_pause([worsened])[0]
    assert escalated.action == qp.ACTION_AUTO_STOP_RECOMMEND
    assert escalated.stop_reasons


def test_restart_simulation_feeds_previous_decision_back():
    """Two consecutive runs model a restart: the decision written by the
    first run is fed back as latch state; the pause must hold."""
    first = qp.evaluate_pause([
        qi(entered=10, settled=10, net_pnl=-650.0, clv_upper_bound=0.5,
           queue_id="q-restart")
    ])[0]
    assert first.action == qp.ACTION_PAUSE_REVIEW
    assert first.keeps_latched_pause is False
    after_restart = qp.evaluate_pause([
        qi(entered=10, settled=10, net_pnl=650.0, clv_upper_bound=0.9,
           max_drawdown=50.0, queue_id="q-restart",
           pause_review_latched=first.paused)
    ])[0]
    assert after_restart.action == qp.ACTION_PAUSE_REVIEW
    assert after_restart.keeps_latched_pause is True


def test_no_latch_no_pause_from_healthy_queue():
    decision = qp.evaluate_pause([qi()])[0]
    assert decision.action == qp.ACTION_CONTINUE
    assert decision.paused is False
    assert decision.keeps_latched_pause is False


# ---------------------------------------------------------------------------
# Multi-queue batches
# ---------------------------------------------------------------------------


def test_multi_queue_batch_is_independent_and_input_ordered():
    queues = [
        qi(queue_id="q-a"),
        qi(queue_id="q-b", entered=10, settled=10, net_pnl=-650.0),
        qi(queue_id="q-c", entered=40, max_drawdown=3000.0,
           clv_upper_bound=0.5, net_pnl=100.0, settled=5),
        qi(queue_id="q-d", league_scope=qp.SCOPE_DOMESTIC, entered=10,
           settled=10, net_pnl=-650.0),
    ]
    decisions = qp.evaluate_pause(queues)
    assert [d.queue_id for d in decisions] == ["q-a", "q-b", "q-c", "q-d"]
    assert [d.action for d in decisions] == [
        qp.ACTION_CONTINUE,
        qp.ACTION_PAUSE_REVIEW,
        qp.ACTION_AUTO_STOP_RECOMMEND,
        qp.ACTION_CONTINUE,
    ]
    # One queue's pause never bleeds into its neighbours.
    assert decisions[3].review_reasons == ()


def test_duplicate_queue_id_in_one_batch_is_rejected():
    with pytest.raises(ValueError, match="duplicate queue_id"):
        qp.evaluate_pause([qi(queue_id="q-same"), qi(queue_id="q-same")])


def test_empty_batch_returns_empty_list():
    assert qp.evaluate_pause([]) == []


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_invalid_counts_and_identity_are_rejected():
    with pytest.raises(ValueError, match="entered"):
        qi(entered=-1)
    with pytest.raises(ValueError, match="settled"):
        qi(settled=-3)
    with pytest.raises(ValueError, match="entered"):
        qi(entered=3.0)
    for field in ("queue_id", "model_key", "market_type", "gate_version",
                  "data_source_version"):
        with pytest.raises(ValueError, match=field):
            qi(**{field: ""})


# ---------------------------------------------------------------------------
# Reuse proof: decisions track evaluation.pause_recommendation exactly
# ---------------------------------------------------------------------------


def test_decisions_equal_evaluation_suggestion_on_boundary_grid():
    entered_values = (0, 9, 10, 19, 20, 39, 40, 41)
    settled_values = (0, 9, 10, 11)
    clv_values = (None, -0.01, 0.0, 0.01)
    drawdown_values = (None, 100.0, 2500.0, 2500.01)
    pnl_values = (None, 100.0, -600.0, -600.01)
    for entered, settled, clv, drawdown, pnl in itertools.product(
        entered_values, settled_values, clv_values, drawdown_values,
        pnl_values,
    ):
        suggestion = ev.pause_recommendation(
            entered=entered, clv_upper_bound=clv, max_drawdown=drawdown,
            settled=settled, net_pnl=pnl,
        )
        for scope, queue_id in (
            (qp.SCOPE_CROSS_LEAGUE, "grid-cross"),
            (qp.SCOPE_DOMESTIC, "grid-dom"),
        ):
            decision = qp.evaluate_pause([qi(
                queue_id=queue_id, league_scope=scope, entered=entered,
                settled=settled, clv_upper_bound=clv, max_drawdown=drawdown,
                net_pnl=pnl,
            )])[0]
            assert decision.pause_suggestion == suggestion
            assert decision.stop_reasons == suggestion.reasons
            if scope == qp.SCOPE_CROSS_LEAGUE:
                assert decision.review_reasons == suggestion.fx17_reasons
                assert decision.fx17_suppressed_reasons == ()
                expected_review = bool(suggestion.fx17_reasons)
            else:
                assert decision.review_reasons == ()
                assert (
                    decision.fx17_suppressed_reasons
                    == suggestion.fx17_reasons
                )
                expected_review = False
            expected_stop = bool(suggestion.reasons)
            expected = (
                qp.ACTION_AUTO_STOP_RECOMMEND if expected_stop
                else qp.ACTION_PAUSE_REVIEW if expected_review
                else qp.ACTION_CONTINUE
            )
            assert decision.action == expected, (
                entered, settled, clv, drawdown, pnl, scope
            )


# ---------------------------------------------------------------------------
# Bridge from evaluation outputs
# ---------------------------------------------------------------------------


def test_queue_pause_input_maps_evaluation_output_exactly():
    rows = [
        mk(candidate_id=f"m{i}", market_key=f"mk{i}", fill_price=0.50,
           close_price=0.40, decision_time=_T0 + timedelta(days=i),
           settled_time=_T0 + timedelta(days=i, hours=5))
        for i in range(10)
    ]
    queue = ev.evaluate_queues(rows).queues[0]
    mapped = qp.queue_pause_input(
        queue, league_scope=qp.SCOPE_CROSS_LEAGUE,
        data_source_version="dsv1",
    )
    assert mapped.entered == queue.counts.entered == 10
    assert mapped.settled == queue.counts.settled == 10
    assert mapped.clv_upper_bound == queue.clv.ci[1]
    assert mapped.max_drawdown == queue.drawdown.max_drawdown
    assert mapped.net_pnl == queue.roi.net_pnl
    assert mapped.league_scope == qp.SCOPE_CROSS_LEAGUE
    assert mapped.data_source_version == "dsv1"
    assert mapped.model_key == queue.queue.model_key
    assert mapped.market_type == queue.queue.market_type
    assert mapped.gate_version == queue.queue.gate_version
    assert mapped.queue_id == (
        "CROSS_LEAGUE|soccer-v1|terminal|gates-v1|dsv1"
    )
    # Same metrics through the direct constructor decide identically.
    direct = qp.evaluate_pause([qp.QueuePauseInput(
        queue_id="direct", league_scope=qp.SCOPE_CROSS_LEAGUE,
        model_key=queue.queue.model_key,
        market_type=queue.queue.market_type,
        gate_version=queue.queue.gate_version,
        data_source_version="dsv1",
        entered=mapped.entered, settled=mapped.settled,
        clv_upper_bound=mapped.clv_upper_bound,
        max_drawdown=mapped.max_drawdown, net_pnl=mapped.net_pnl,
    )])[0]
    through_map = qp.evaluate_pause([mapped])[0]
    assert direct.action == through_map.action
    assert direct.review_reasons == through_map.review_reasons
    assert direct.stop_reasons == through_map.stop_reasons


def test_queue_pause_input_unknowns_when_nothing_settled():
    rows = [
        mk(candidate_id=f"u{i}", market_key=f"uk{i}", outcome=None,
           recorded_pnl=None, settled_time=None)
        for i in range(25)
    ]
    queue = ev.evaluate_queues(rows).queues[0]
    assert queue.counts.entered == 25 and queue.roi.counted == 0
    mapped = qp.queue_pause_input(
        queue, league_scope=qp.SCOPE_CROSS_LEAGUE,
        data_source_version="dsv1",
    )
    assert mapped.net_pnl is None and mapped.max_drawdown is None
    assert mapped.clv_upper_bound is None  # no close prices either
    decision = qp.evaluate_pause([mapped])[0]
    assert decision.action == qp.ACTION_CONTINUE


def test_queue_pause_input_rejects_invalid_scope():
    rows = [mk()]
    queue = ev.evaluate_queues(rows).queues[0]
    with pytest.raises(ValueError, match="league_scope"):
        qp.queue_pause_input(queue, league_scope="cross",
                             data_source_version="dsv1")


# ---------------------------------------------------------------------------
# Purity: no IO, no driver, deterministic
# ---------------------------------------------------------------------------


def test_evaluate_pause_is_pure_no_io(monkeypatch):
    # Earlier native tests can leave the driver loaded before evaluate_pause runs.
    for name in tuple(sys.modules):
        if name == "psycopg" or name.startswith("psycopg."):
            monkeypatch.delitem(sys.modules, name)

    def refuse(*args, **kwargs):
        raise AssertionError("queue_policy must not touch the filesystem")

    monkeypatch.setattr("builtins.open", refuse)
    real_socket = socket.socket
    monkeypatch.setattr(socket, "socket", refuse)
    queues = [
        qi(queue_id="p1", entered=40, clv_upper_bound=-0.01, settled=10,
           net_pnl=-700.0, max_drawdown=3000.0),
        qi(queue_id="p2"),
    ]
    decisions = qp.evaluate_pause(queues)
    assert [d.action for d in decisions] == [
        qp.ACTION_AUTO_STOP_RECOMMEND, qp.ACTION_CONTINUE,
    ]
    assert "psycopg" not in sys.modules
    monkeypatch.setattr(socket, "socket", real_socket)


def test_evaluate_pause_is_deterministic():
    queues = [qi(queue_id=f"d{i}", entered=10 + i, settled=10,
                 net_pnl=-650.0) for i in range(5)]
    first = qp.evaluate_pause(queues)
    second = qp.evaluate_pause(queues)
    assert first == second


def test_check_kappa_freeze_is_pure(monkeypatch):
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no file access")))
    check = qp.check_kappa_freeze("EPL_2026", "a" * 64, "a" * 64)
    assert check.status == qp.KAPPA_FREEZE_MATCH


# ---------------------------------------------------------------------------
# Kappa freeze gate
# ---------------------------------------------------------------------------


def test_kappa_freeze_match_is_usable():
    digest = "b" * 64
    check = qp.check_kappa_freeze("EPL_2026", digest, digest)
    assert check.status == qp.KAPPA_FREEZE_MATCH
    assert check.usable is True
    assert check.blocked_reason is None
    assert check.season == "EPL_2026"
    assert check.frozen_sha256 == check.observed_sha256 == digest


def test_kappa_freeze_midseason_drift_blocks_and_keeps_evidence():
    frozen, drifted = "c" * 64, "d" * 64
    check = qp.check_kappa_freeze("EPL_2026", frozen, drifted)
    assert check.status == qp.KAPPA_FREEZE_DRIFTED
    assert check.usable is False
    assert check.blocked_reason and "drifted" in check.blocked_reason
    # Both digests retained as evidence for the review.
    assert (check.frozen_sha256, check.observed_sha256) == (frozen, drifted)


def test_kappa_freeze_missing_digest_is_unverified():
    digest = "e" * 64
    no_frozen = qp.check_kappa_freeze("EPL_2026", None, digest)
    assert no_frozen.status == qp.KAPPA_FREEZE_UNVERIFIED
    assert no_frozen.usable is False and no_frozen.blocked_reason
    no_observed = qp.check_kappa_freeze("EPL_2026", digest, None)
    assert no_observed.status == qp.KAPPA_FREEZE_UNVERIFIED
    assert no_observed.usable is False


def test_kappa_freeze_invalid_arguments_rejected():
    with pytest.raises(ValueError, match="season"):
        qp.check_kappa_freeze("", "f" * 64, "f" * 64)
    with pytest.raises(ValueError, match="frozen_sha256"):
        qp.check_kappa_freeze("EPL_2026", "", "f" * 64)
    with pytest.raises(ValueError, match="observed_sha256"):
        qp.check_kappa_freeze("EPL_2026", "f" * 64, "")
