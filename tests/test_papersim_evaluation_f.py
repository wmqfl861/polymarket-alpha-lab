"""FX-35 Phase A unit tests for the pure criterion-F evidence core.

ALL FIXTURES HERE ARE SYNTHETIC. Nothing in this file claims a real D2
cutover date, a real 30-day observed window, a real registered slot
grid, a real replay seed registration, or any production ledger state:
the signed anchors (a 2026-10-01 window start, 06:00/18:00 slot grids,
seed/N placeholders, code commits) are invented test data exercising
the v5 semantics. Production F stays NOT_ASSESSABLE (or
NOT_ASSESSABLE_M1 without evidence) until the owner registers R1/R4
and the B2/B3 contracts land.

Required matrix (fx35-post86-plan-v5.md section 4, plus the retained
v4 cases with unchanged outcomes):

1. multi-slot day with one omitted scheduled slot -> FAIL although the
   day's other slot SUCCEEDED (kills the C1 masking shortcut; proves
   C2 detection once the grid is registered);
2. same-slot retry FAILED -> SUCCEEDED in one slot interval -> slot
   covered, failures/retries counted honestly;
3. NONTERMINAL-only slot -> FAIL at slot grain;
4. as-of isolation: a SUCCEEDED receipt not in the supplied snapshot ->
   slot uncovered at that as-of;
5. concurrent-teams PAUSE counterexample -> diagnostic names the wrong
   team as hypothesis, attribution stays UNPROVEN, verdict fails closed;
6. pause-on-failed-attempt (the v3 scenario re-ruled per C3) -> no
   proven attribution -> unmatched -> FAIL (R5c remedy documented);
7. unknown schedule and unknown cutover anchor -> NOT_ASSESSABLE, never
   PASS;
plus the four signed 03:149 cases (case 1's fixture adjusted per C3:
the recovered retry carries no PAUSE row, or its pause-attempt is the
succeeding one so equality proves it), absence tri-state byte-identity,
seeded-sample reproducibility, overall-verdict ordering including the
new all-PASS branch, frozen-regression-unchanged, and pre-anchor
disclosure per R6e.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from polymarket_alpha_lab.papersim import evaluation as ev
from polymarket_alpha_lab.papersim import evaluation_f as ef
from polymarket_alpha_lab.papersim.evaluation_f import (
    DEFAULT_PENDING_CONTRACTS,
    BankrollCheckRecord,
    BankrollEventRecord,
    CandidateChildRecord,
    CandidateLinkRecord,
    ControlEventRecord,
    EntryChildRecord,
    EntryRecord,
    FEvidence,
    FEvidenceSchedule,
    MarketOutcomeRecord,
    RunEventRecord,
    ScanRunRecord,
    SnapshotRecord,
    draw_replay_sample,
)

ANCHOR = date(2026, 10, 1)  # synthetic; not a registered D2 date
COMMIT = "a" * 40  # synthetic 40-hex commit, schema-shaped
OLD_COMMIT = "b" * 40


def _dt(day_offset: int, hour: int = 6, minute: int = 0) -> datetime:
    return datetime(2026, 10, 1, hour, minute, tzinfo=timezone.utc) + (
        timedelta(days=day_offset)
    )


def _schedule(*hhmm: str) -> FEvidenceSchedule:
    return FEvidenceSchedule(
        slot_times_utc=tuple(
            time(int(token[:2]), int(token[3:])) for token in hhmm
        )
    )


def _grid(slot_times, *, status_for=None, scan_id_start=1):
    """Cover every expected slot of the synthetic 30-day window once.

    ``status_for(day, slot_index)`` defaults to SUCCEEDED; None omits
    the slot's receipts entirely (the omitted-slot shortcut fixture).
    """
    events: list[RunEventRecord] = []
    scans: list[ScanRunRecord] = []
    scan_id = scan_id_start
    for day in range(ef.F_WINDOW_DAYS):
        for index, hhmm in enumerate(slot_times):
            status = (
                status_for(day, index) if status_for is not None
                else "SUCCEEDED"
            )
            if status is None:
                continue
            hour, minute = int(hhmm[:2]), int(hhmm[3:])
            moment = _dt(day, hour, minute)
            operation = f"op-{day:02d}-{index}-{hhmm}"
            events.append(
                RunEventRecord(operation, 1, "STARTED", moment)
            )
            if status == "SUCCEEDED":
                events.append(
                    RunEventRecord(
                        operation, 1, "SUCCEEDED", moment + timedelta(minutes=5)
                    )
                )
                scans.append(
                    ScanRunRecord(
                        scan_id=scan_id, started_at=moment,
                        operation_key=operation, code_commit=COMMIT,
                    )
                )
                scan_id += 1
            elif status == "FAILED":
                events.append(
                    RunEventRecord(
                        operation, 1, "FAILED",
                        moment + timedelta(minutes=5),
                        error_code="SCAN_FAILED",
                    )
                )
            # NONTERMINAL: crash without a terminal receipt, no scan row.
    return events, scans


def _checks(failed_days=(), days=range(ef.F_WINDOW_DAYS)):
    return [
        BankrollCheckRecord(
            as_of=_dt(day, 23),
            passed=day not in failed_days,
            identity_difference=0.0 if day not in failed_days else 0.5,
            tolerance=0.005,
        )
        for day in days
    ]


def _candidates(count=25, *, day=0, scan_id=1, commit=COMMIT):
    return [
        CandidateLinkRecord(
            candidate_id=f"cand-{index:03d}", scan_id=scan_id,
            written_at=_dt(day, 7), code_commit=commit,
        )
        for index in range(count)
    ]


def _green(slot_times=("06:00",), *, status_for=None, **overrides) -> FEvidence:
    """A fully green synthetic window (signed case 1 shape): every
    expected slot SUCCEEDED, every day's identity passed, replay
    drawable and all-agree. pending_contracts is cleared — legitimate
    ONLY as a clearly-labeled synthetic model of the post-B2/B3 state."""
    events, scans = _grid(slot_times, status_for=status_for)
    candidates = _candidates()
    parts = dict(
        window_start=ANCHOR,
        schedule=_schedule(*slot_times),
        run_events=tuple(events),
        scan_runs=tuple(scans),
        bankroll_checks=tuple(_checks()),
        candidates=tuple(candidates),
        replay_agreements={c.candidate_id: True for c in candidates},
        pending_contracts=(),
    )
    parts.update(overrides)
    return FEvidence(**parts)


# ---------------------------------------------------------------------------
# v5 section 4 cases 1-7
# ---------------------------------------------------------------------------


def test_v5_case1_omitted_slot_fails_despite_sibling_success():
    """v5 case 1 (kills the C1 masking shortcut): two planned slots per
    day, day 5's 18:00 slot omitted entirely. The day's 06:00 slot
    SUCCEEDED — and F still FAILs, naming the omitted slot. The omitted
    slot leaves NO receipt anywhere (C2: expected slots come only from
    the registered descriptor, never from observed receipts), yet the
    registered grid detects it."""
    evidence = _green(
        ("06:00", "18:00"),
        status_for=lambda day, index: None if (day == 5 and index == 1)
        else "SUCCEEDED",
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "2026-10-06T18:00:00+00:00" in reason and "uncovered" in reason
        and "(absent)" in reason
        for reason in result.reasons
    )
    timeline = ef.build_slot_timeline(
        evidence.run_events, ANCHOR, evidence.schedule
    )
    assert len(timeline.uncovered) == 1
    assert timeline.uncovered[0].index == 5 * 2 + 1
    # The sibling success on the same day did not mask the omission.
    assert timeline.slots[5 * 2].covered is True


def test_v5_case2_same_slot_retry_is_covered_with_honest_counts():
    """v5 case 2: attempt 1 FAILED, attempt 2 SUCCEEDED inside one slot
    interval -> the slot is covered; failures and retries are counted
    honestly (03:144) and never fail a covered slot."""
    events, scans = _grid(("06:00",))
    retry_op = "op-retry-03"
    events = [
        e for e in events
        if not e.operation_key.startswith("op-03-")
    ]
    t0 = _dt(3, 6, 0)
    events += [
        RunEventRecord(retry_op, 1, "STARTED", t0),
        RunEventRecord(
            retry_op, 1, "FAILED", t0 + timedelta(minutes=1),
            error_code="E_RETRYABLE",
        ),
        RunEventRecord(retry_op, 2, "STARTED", t0 + timedelta(minutes=2)),
        RunEventRecord(
            retry_op, 2, "SUCCEEDED", t0 + timedelta(minutes=3)
        ),
    ]
    scans = [s for s in scans if s.operation_key != "op-03-0-06:00"]
    scans.append(
        ScanRunRecord(
            scan_id=99, started_at=t0 + timedelta(minutes=2),
            operation_key=retry_op, code_commit=COMMIT,
        )
    )
    evidence = _green(
        run_events=tuple(sorted(events, key=lambda e: e.occurred_at)),
        scan_runs=tuple(scans),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_PASS
    timeline = ef.build_slot_timeline(
        evidence.run_events, ANCHOR, evidence.schedule
    )
    assert timeline.slots[3].covered is True
    assert timeline.retry_attempts == 1
    assert timeline.attempts_total == 31
    assert timeline.error_codes == ("E_RETRYABLE",)
    assert any("1 retry" in reason for reason in result.reasons)
    # Independent review P2-2: the FAILED attempt stays counted even
    # though attempt 2 SUCCEEDED — FAILED->SUCCEEDED must not hide the
    # failed-attempt count.
    assert timeline.attempts_failed == 1
    assert any("1 failed" in reason for reason in result.reasons)


def test_v5_case3_nonterminal_only_slot_fails():
    """v5 case 3 (v4 case 2 retained, now at slot grain): a slot whose
    only group is NONTERMINAL (crash without a terminal receipt) fails
    F even though every other slot succeeded."""
    evidence = _green(
        status_for=lambda day, index: "NONTERMINAL" if day == 7
        else "SUCCEEDED",
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "2026-10-08T06:00:00+00:00" in reason and "(nonterminal-only)" in reason
        for reason in result.reasons
    )


def test_v5_case4_asof_isolation_snapshot_only():
    """v5 case 4: the slot's SUCCEEDED receipts were committed AFTER
    the evaluation snapshot, so the snapshot does not contain them; the
    slot is judged uncovered at that as-of even though the full data
    would cover it. The verdict is computed from the snapshot only."""
    full = _green()
    snapshot = _green(
        status_for=lambda day, index: None if day == 10 else "SUCCEEDED",
    )
    assert ev.criterion_f(evidence=full).verdict == ev.VERDICT_PASS
    result = ev.criterion_f(evidence=snapshot)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "2026-10-11T06:00:00+00:00" in reason and "uncovered" in reason
        for reason in result.reasons
    )


def test_v5_case5_concurrent_teams_pause_counterexample():
    """v5 case 5: team A's attempt writes PAUSE(t); team B's STARTED is
    the nearest at-or-after t. The diagnostic names team B as the
    hypothesis; the attribution stays UNPROVEN; no PASS is possible and
    the verdict fails closed (C3)."""
    team_b = "teamB-op-02"
    extra_events = [
        RunEventRecord(team_b, 1, "STARTED", _dt(2, 6, 6)),
        RunEventRecord(team_b, 1, "SUCCEEDED", _dt(2, 6, 7)),
    ]
    extra_scans = [
        ScanRunRecord(
            scan_id=98, started_at=_dt(2, 6, 6),
            operation_key=team_b, code_commit=COMMIT,
        )
    ]
    pause = ControlEventRecord(
        "PAUSE", _dt(2, 6, 5), queue_id="teamA-queue"
    )
    evidence = _green(
        run_events=tuple(
            sorted(_grid(("06:00",))[0] + extra_events,
                   key=lambda e: e.occurred_at)
        ),
        scan_runs=tuple(_grid(("06:00",))[1] + extra_scans),
        control_events=(pause,),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "no PROVEN attribution" in reason and "fail closed" in reason
        for reason in result.reasons
    )
    universe = ef.reconcile_universe(evidence)
    assert len(universe.pause_diagnostics) == 1
    diagnostic = universe.pause_diagnostics[0]
    assert diagnostic.cause == "no-equality-match"
    # The hypothesis names team B — and it is diagnostic-only.
    assert diagnostic.hypothesis_operation_key == team_b
    assert diagnostic.hypothesis_attempt == 1
    assert any(
        "diagnostic only" in reason and team_b in reason
        for reason in result.reasons
    )


def test_v5_case6_pause_on_failed_attempt_fails_closed():
    """v5 case 6 (the v3 scenario, re-ruled per C3): a pause escalation
    written by a FAILED attempt of an operation later recovered by a
    higher attempt. The single persisted scan row carries the
    SUCCEEDING attempt's started_at, so the PAUSE timestamp can never
    equality-match: no proven attribution -> unmatched -> FAIL. The
    structural remedy is R5c (persist the operation receipt key on the
    queue-control write; scan/DB-lane change) and D2 must sequence it
    BEFORE the 30-day window starts — otherwise any pause-escalation on
    a non-scanned attempt voids the window."""
    events, scans = _grid(
        ("06:00",), status_for=lambda day, index: (
            None if day == 4 else "SUCCEEDED"
        ),
    )
    recovered = "op-recovered-04"
    t_failed = _dt(4, 6, 30)
    events += [
        RunEventRecord(recovered, 1, "STARTED", t_failed),
        RunEventRecord(
            recovered, 1, "FAILED", t_failed + timedelta(minutes=1),
            error_code="E_PAUSE_ESCALATION",
        ),
        RunEventRecord(
            recovered, 2, "STARTED", t_failed + timedelta(minutes=2)
        ),
        RunEventRecord(
            recovered, 2, "SUCCEEDED", t_failed + timedelta(minutes=3)
        ),
    ]
    scans.append(
        ScanRunRecord(
            scan_id=97, started_at=t_failed + timedelta(minutes=2),
            operation_key=recovered, code_commit=COMMIT,
        )
    )
    pause = ControlEventRecord("PAUSE", t_failed, queue_id="main")
    evidence = _green(
        run_events=tuple(sorted(events, key=lambda e: e.occurred_at)),
        scan_runs=tuple(scans),
        control_events=(pause,),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "no PROVEN attribution (no-equality-match)" in reason
        for reason in result.reasons
    )


def test_v5_case7_unknown_schedule_and_anchor_are_not_assessable():
    """v5 case 7: unknown schedule descriptor and unknown cutover anchor
    each make F UNEVALUABLE -> NOT_ASSESSABLE with the shortfall
    stated, never PASS (C2)."""
    green = _green()
    no_anchor = _green(window_start=None)
    no_schedule = _green(schedule=None)
    for evidence in (no_anchor, no_schedule):
        result = ev.criterion_f(evidence=evidence)
        assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
        assert result.reasons
    assert any(
        "cutover anchor not registered" in reason
        for reason in ev.criterion_f(evidence=no_anchor).reasons
    )
    assert any(
        "schedule descriptor not registered" in reason
        for reason in ev.criterion_f(evidence=no_schedule).reasons
    )
    # Sanity: the registered fixture passes (never PASS without them).
    assert ev.criterion_f(evidence=green).verdict == ev.VERDICT_PASS


# ---------------------------------------------------------------------------
# The four signed 03:149 cases (outcomes unchanged; case 1 per C3)
# ---------------------------------------------------------------------------


def test_signed_case_1_complete_window_passes_and_flips_overall():
    """Signed case 1 (30 天齐全 -> 通过), fixture adjusted per C3: the
    recovered retry carries NO PAUSE row (a pause row from the failed
    attempt would be unproven under C3 and void the window). Includes a
    NONTERMINAL crash group on a covered day (counted honestly, slot
    still covered), all 30 identity receipts passed, seeded sample all
    consistent, zero unmatched. pending_contracts=() is the
    clearly-labeled synthetic post-B2/B3 model."""
    events, scans = _grid(("06:00",))
    # Recovered retry on day 2 (attempt 1 FAILED, attempt 2 SUCCEEDED,
    # no PAUSE row).
    recovered = "op-recovered-02"
    t0 = _dt(2, 6, 0)
    events = [e for e in events if e.operation_key != "op-02-0-06:00"]
    events += [
        RunEventRecord(recovered, 1, "STARTED", t0),
        RunEventRecord(
            recovered, 1, "FAILED", t0 + timedelta(minutes=1),
            error_code="E_TRANSIENT",
        ),
        RunEventRecord(recovered, 2, "STARTED", t0 + timedelta(minutes=2)),
        RunEventRecord(recovered, 2, "SUCCEEDED", t0 + timedelta(minutes=3)),
    ]
    scans = [s for s in scans if s.operation_key != "op-02-0-06:00"]
    scans.append(
        ScanRunRecord(
            scan_id=96, started_at=t0 + timedelta(minutes=2),
            operation_key=recovered, code_commit=COMMIT,
        )
    )
    # NONTERMINAL crash group on a covered day (honest count, no FAIL).
    crash = "op-crash-09"
    events.append(RunEventRecord(crash, 1, "STARTED", _dt(9, 9)))
    evidence = _green(
        run_events=tuple(sorted(events, key=lambda e: e.occurred_at)),
        scan_runs=tuple(scans),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_PASS
    assert any("window totals" in reason and "30 SUCCEEDED" in reason
               for reason in result.reasons)
    assert any("1 NONTERMINAL" in reason for reason in result.reasons)
    assert any("all agreed" in reason for reason in result.reasons)
    assert any(reason.startswith("boundary (03:147)") for reason in result.reasons)

    # End-to-end (v4 section 3.6): an A-E-green queue flips
    # NOT_ASSESSABLE_M1 -> PASS only through the evidence.
    rows = _a_to_e_green_queue()
    baseline = ev.evaluate_queues(rows)
    assert baseline.queues[0].promotion[-1].verdict == (
        ev.VERDICT_NOT_ASSESSABLE_M1
    )
    assert baseline.queues[0].overall_verdict == ev.VERDICT_NOT_ASSESSABLE_M1
    with_evidence = ev.evaluate_queues(rows, f_evidence=evidence)
    f_result = [c for c in with_evidence.queues[0].promotion if c.key == "F"]
    assert f_result[0].verdict == ev.VERDICT_PASS
    assert with_evidence.queues[0].overall_verdict == ev.VERDICT_PASS


def test_signed_case_2_missing_day_fails():
    """Signed case 2 (缺一天 -> 不通过): one window day with only a
    NONTERMINAL crash group — crash-without-success is actual failure,
    never NOT_ASSESSABLE."""
    evidence = _green(
        status_for=lambda day, index: "NONTERMINAL" if day == 12
        else "SUCCEEDED",
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any("2026-10-13" in reason and "uncovered" in reason
               for reason in result.reasons)


def test_signed_case_3_missing_identity_day_fails():
    """Signed case 3 (缺一次恒等式 -> 不通过): daily identity is an
    independent gate — one day without a receipt fails F."""
    evidence = _green(bankroll_checks=tuple(_checks(days=range(12))))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "2026-10-13" in reason and "identity missing" in reason
        for reason in result.reasons
    )
    # A day whose only receipt failed is a failed identity day, with the
    # recorded difference/tolerance reported and never re-judged.
    failed_day = _green(bankroll_checks=tuple(_checks(failed_days=(18,))))
    failed_result = ev.criterion_f(evidence=failed_day)
    assert failed_result.verdict == ev.VERDICT_FAIL
    assert any(
        "2026-10-19" in reason and "identity failed" in reason
        and "0.5" in reason and "0.005" in reason
        for reason in failed_result.reasons
    )


def test_signed_case_4_unlinked_rows_fail():
    """Signed case 4 (有一行关联不上 -> 不通过), both fixtures: a
    scan_runs row with NULL operation_key, and an unattributable PAUSE
    row (no equality match and no STARTED at-or-after at all)."""
    null_key_scan = ScanRunRecord(
        scan_id=500, started_at=_dt(3, 12), operation_key=None
    )
    evidence = _green(
        scan_runs=tuple(_grid(("06:00",))[1]) + (null_key_scan,),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "NULL operation_key" in reason for reason in result.reasons
    )
    assert any("unmatched business rows: 1 > 0" in reason
               for reason in result.reasons)

    orphan_pause = ControlEventRecord("PAUSE", _dt(29, 23, 30), queue_id="q")
    evidence_pause = _green(control_events=(orphan_pause,))
    result_pause = ev.criterion_f(evidence=evidence_pause)
    assert result_pause.verdict == ev.VERDICT_FAIL
    assert any(
        "no PROVEN attribution (no-equality-match)" in reason
        for reason in result_pause.reasons
    )
    universe = ef.reconcile_universe(evidence_pause)
    assert universe.pause_diagnostics[0].hypothesis_operation_key is None


# ---------------------------------------------------------------------------
# Retained v4 complementary cases
# ---------------------------------------------------------------------------


def test_absence_tri_state_byte_identity():
    """Absence tri-state: evidence omitted (None) leaves every output
    byte-identical to the pre-change behavior."""
    assert ev.criterion_f(evidence=None) == ev.criterion_f()
    assert repr(ev.criterion_f(evidence=None)) == repr(ev.criterion_f())
    rows = _a_to_e_green_queue()
    assert repr(ev.evaluate_queues(rows, f_evidence=None)) == repr(
        ev.evaluate_queues(rows)
    )
    # A completely empty evidence snapshot is UNKNOWN, not a failure.
    empty = ev.criterion_f(evidence=FEvidence())
    assert empty.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert any("no run receipts" in reason for reason in empty.reasons)


def test_seeded_sample_reproducibility_and_insufficiency():
    """Seeded-sample reproducibility (R2): the same fixed seed draws the
    same replay sample every time; fewer than N eligible candidates is
    evidence-absent (NOT_ASSESSABLE, counts stated), never a failure;
    older-commit candidates are counted, never silently skipped."""
    evidence = _green()
    first = draw_replay_sample(evidence)
    second = draw_replay_sample(evidence)
    assert first.selected_ids == second.selected_ids
    assert len(first.selected_ids) == ev.F_REPLAY_SAMPLE_N == 20
    assert first.eligible_ids == tuple(sorted(first.eligible_ids))
    # All-agree selected sample contributes the PASS reason.
    assert any("all agreed" in reason
               for reason in ev.criterion_f(evidence=evidence).reasons)

    few = _green(
        candidates=tuple(_candidates(count=10)),
        replay_agreements={c.candidate_id: True
                           for c in _candidates(count=10)},
    )
    result = ev.criterion_f(evidence=few)
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert any(
        "replay evidence absent" in reason and "10 eligible" in reason
        for reason in result.reasons
    )

    mixed = _green(
        candidates=tuple(_candidates(count=25))
        + tuple(_candidates(count=5, day=1, scan_id=2, commit=OLD_COMMIT)),
    )
    sample = draw_replay_sample(mixed)
    assert sample.older_commit_count == 5
    assert any(
        "older commits listed" in reason
        for reason in ev.criterion_f(evidence=mixed).reasons
    )

    # A selected candidate that did not reproduce is a signed FAIL
    # (03:146 全部一致), not a shortfall.
    agreements = {c.candidate_id: True for c in _candidates()}
    selected = draw_replay_sample(evidence).selected_ids
    agreements[selected[0]] = False
    mismatch = _green(replay_agreements=agreements)
    result = ev.criterion_f(evidence=mismatch)
    assert result.verdict == ev.VERDICT_FAIL
    assert any("replay mismatch" in reason for reason in result.reasons)


def test_overall_verdict_ordering_including_new_pass_branch():
    """Overall-verdict ordering with the new all-PASS branch (canonical
    detailed ordering test lives in tests/test_papersim_evaluation.py)."""
    all_pass = [
        ev.CriterionResult(key, ev.VERDICT_PASS, ())
        for key in ("A", "B", "C", "D", "E", "F")
    ]
    assert ev.overall_verdict(all_pass) == ev.VERDICT_PASS
    assert ev.overall_verdict(
        all_pass[:-1] + [ev.CriterionResult("F", ev.VERDICT_FAIL, ())]
    ) == ev.VERDICT_FAIL
    assert ev.overall_verdict(
        all_pass[:-1] + [ev.CriterionResult("F", ev.VERDICT_NOT_ASSESSABLE, ())]
    ) == ev.VERDICT_NOT_ASSESSABLE
    assert ev.overall_verdict(
        all_pass[:-1] + [ev.criterion_f()]
    ) == ev.VERDICT_NOT_ASSESSABLE_M1


def test_frozen_regression_unchanged():
    """Frozen-regression-unchanged: the pre-existing frozen surfaces F
    relies on are untouched — the no-evidence stub text byte-identical,
    the PR86 public bootstrap API importable (pinned elsewhere), the
    rules version and bootstrap seed unretuned, and the F seed/N
    placeholders pinned as owner-registration placeholders."""
    assert ev.criterion_f().reasons == (
        "requires M2 unattended-run evidence: 30 days of scheduled scans, "
        "no manual DB edits, daily bankroll identity, per-candidate replay",
    )
    for name in (
        "cluster_rows", "cluster_bootstrap_ratio_ci",
        "cluster_bootstrap_mean_ci", "CLUSTER_PRIORITY",
    ):
        assert hasattr(ev, name), name
    assert ev.PROMOTION_RULES_VERSION == "odg-registered-2026-11-13+odk"
    assert ev.BOOTSTRAP_SEED == 20261114
    # Owner-registration placeholders (R2), never tuned, never derived.
    assert ev.F_REPLAY_SEED == 20261120
    assert ev.F_REPLAY_SAMPLE_N == 20


def test_pre_anchor_disclosure_r6e():
    """Pre-anchor key-less rows are disclosed per family and never fail
    F (R6e: F judges the window only; the managed store starts empty at
    cutover). An IN-WINDOW legacy-import outcome is a real bypass
    signal and fails closed (R6f)."""
    pre_anchor_rows = (
        MarketOutcomeRecord("legacy_import", _dt(-3, 12)),
        ScanRunRecord(999, _dt(-2, 6), operation_key=None),
    )
    evidence = _green(
        market_outcomes=(pre_anchor_rows[0],),
        scan_runs=tuple(_grid(("06:00",))[1]) + (pre_anchor_rows[1],),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_PASS
    assert any(
        "pre-anchor" in reason and "2 row(s)" in reason
        for reason in result.reasons
    )
    in_window = _green(
        market_outcomes=(MarketOutcomeRecord("legacy_import", _dt(3, 12)),)
    )
    result_in_window = ev.criterion_f(evidence=in_window)
    assert result_in_window.verdict == ev.VERDICT_FAIL
    assert any(
        "legacy import tool" in reason and "unmatched in-window" in reason
        for reason in result_in_window.reasons
    )


# ---------------------------------------------------------------------------
# Registration semantics, degradation and remaining rule surfaces
# ---------------------------------------------------------------------------


def test_pending_contracts_degrade_never_approximate():
    """R3 default: with the B2/B3 contracts still pending (the real
    current lane state at fa01be0), even a fully green window stays
    NOT_ASSESSABLE with the shortfall stated — never approximated,
    never exempted, never PASS."""
    evidence = _green(pending_contracts=DEFAULT_PENDING_CONTRACTS)
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert any(
        "pending receipt contracts (R3)" in reason
        and "settlements" in reason and "marks" in reason
        for reason in result.reasons
    )


def test_window_incomplete_at_asof_is_not_assessable():
    """R1: the window counts only full UTC days elapsed at the as-of;
    the evaluation day itself never counts."""
    evidence = _green(as_of=_dt(10, 0))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert any(
        "window incomplete at as-of" in reason and "10/30" in reason
        for reason in result.reasons
    )
    # At exactly the window end the 30 days are complete.
    complete = _green(as_of=_dt(30, 0))
    assert ev.criterion_f(evidence=complete).verdict == ev.VERDICT_PASS


def test_ten_good_completed_days_without_future_evidence_stay_not_assessable():
    """Independent review P2-2 (exact counterexample): ten good
    completed days whose future days simply have no evidence yet must
    be NOT_ASSESSABLE with the incompleteness stated — the future
    slots/days are IMMATURE, so their absence is a shortfall, never a
    'uncovered'/'missing' failure."""
    evidence = _green(as_of=_dt(10, 0))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert not any("uncovered" in reason for reason in result.reasons)
    assert not any("identity missing" in reason for reason in result.reasons)
    assert not any("identity failed" in reason for reason in result.reasons)
    assert any(
        "not yet mature" in reason for reason in result.reasons
    )
    timeline = ef.build_slot_timeline(
        evidence.run_events, ANCHOR, evidence.schedule,
        judgeable_until=_dt(10, 0),
    )
    assert timeline.uncovered == ()
    assert len(timeline.immature) > 0
    assert all(s.status == ef.SLOT_IMMATURE for s in timeline.immature)
    # Receipts after the as-of are excluded from every status decision
    # and counted honestly instead.
    assert timeline.post_cutoff_receipt_rows > 0


def test_post_window_terminal_success_cannot_cover_last_slot():
    """Independent review P2-2 (exact counterexample): an operation
    STARTED inside the last slot whose SUCCEEDED receipt lands after
    the window end must NOT cover the last slot. At window close the
    slot had no in-window terminal success — the old code upgraded the
    group from a post-window receipt and passed."""
    late_op = "op-late-29"
    events, scans = _grid(("06:00",))
    # Remove day 29's healthy group and replace it with the late one.
    events = [
        e for e in events
        if not e.operation_key.startswith("op-29-")
    ]
    scans = [s for s in scans if s.operation_key != "op-29-0-06:00"]
    started = _dt(29, 23, 59)
    events += [
        RunEventRecord(late_op, 1, "STARTED", started),
        # Terminal receipt AFTER the window end (day 31 00:00).
        RunEventRecord(
            late_op, 1, "SUCCEEDED", _dt(30, 0, 1)
        ),
    ]
    scans.append(
        ScanRunRecord(
            scan_id=95, started_at=started,
            operation_key=late_op, code_commit=COMMIT,
        )
    )
    evidence = _green(
        run_events=tuple(sorted(events, key=lambda e: e.occurred_at)),
        scan_runs=tuple(scans),
    )
    timeline = ef.build_slot_timeline(
        evidence.run_events, ANCHOR, evidence.schedule
    )
    last = timeline.slots[-1]
    assert last.status == ef.SLOT_NONTERMINAL_ONLY
    assert last.covered is False
    assert timeline.post_cutoff_receipt_rows == 1
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "2026-10-30T06:00:00+00:00" in reason and "(nonterminal-only)"
        in reason
        for reason in result.reasons
    )
    # Even a LATER as-of (after the success landed) cannot retrofit the
    # post-window success into the window: the cutoff is clamped at the
    # window end.
    later = _green(
        run_events=tuple(sorted(events, key=lambda e: e.occurred_at)),
        scan_runs=tuple(scans),
        as_of=_dt(32, 0),
    )
    assert ev.criterion_f(evidence=later).verdict == ev.VERDICT_FAIL


def test_scheduled_for_takes_precedence_for_attribution():
    """Per-row scheduled_for is used when present (still NULL in
    production today). P3 (review-v5 section 7) is recorded here: pure
    interval containment could let an ad-hoc attended run cover a slot
    whose scheduled run was omitted; once D2 persists scheduled_for per
    the registered schedule, coverage should prefer (or require)
    scheduled_for-matching over interval containment."""
    adhoc = "op-adhoc-06"
    events, scans = _grid(("06:00", "18:00"))
    events.append(
        RunEventRecord(
            adhoc, 1, "STARTED", _dt(6, 19),  # 19:00 is inside the 18:00 slot
            scheduled_for=_dt(6, 6, 30),      # registered intent: 06:00 slot
        )
    )
    events.append(RunEventRecord(adhoc, 1, "SUCCEEDED", _dt(6, 19, 5),
                                 scheduled_for=_dt(6, 6, 30)))
    timeline = ef.build_slot_timeline(
        tuple(sorted(events, key=lambda e: e.occurred_at)),
        ANCHOR, _schedule("06:00", "18:00"),
    )
    assert adhoc in timeline.slots[6 * 2].groups      # day 6, 06:00 slot
    assert adhoc not in timeline.slots[6 * 2 + 1].groups  # not the 18:00 slot


def test_ambiguous_pause_equality_is_unproven():
    """C3 ambiguity branch: two scan rows sharing the PAUSE timestamp
    make the equality match ambiguous, hence unproven, hence
    unmatched, hence FAIL."""
    shared = _dt(8, 12)
    extra_events = [
        RunEventRecord("amb-1", 1, "STARTED", shared),
        RunEventRecord("amb-1", 1, "SUCCEEDED", shared + timedelta(minutes=1)),
        RunEventRecord("amb-2", 1, "STARTED", shared),
        RunEventRecord("amb-2", 1, "SUCCEEDED", shared + timedelta(minutes=1)),
    ]
    extra_scans = [
        ScanRunRecord(scan_id=801, started_at=shared,
                      operation_key="amb-1", code_commit=COMMIT),
        ScanRunRecord(scan_id=802, started_at=shared,
                      operation_key="amb-2", code_commit=COMMIT),
    ]
    pause = ControlEventRecord("PAUSE", shared, queue_id="q")
    evidence = _green(
        run_events=tuple(
            sorted(_grid(("06:00",))[0] + extra_events,
                   key=lambda e: e.occurred_at)
        ),
        scan_runs=tuple(_grid(("06:00",))[1] + extra_scans),
        control_events=(pause,),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    universe = ef.reconcile_universe(evidence)
    assert universe.pause_diagnostics[0].cause == "ambiguous-equality-match"


def test_proven_pause_via_unique_equality_is_linked():
    """The C3 positive branch: a PAUSE row whose observed_at equals the
    started_at of exactly one scan row is PROVEN and linked (this is
    the adjusted signed-case-1 shape: the pause-attempt is the
    succeeding one, so equality proves it)."""
    pause_moment = _dt(11, 6, 0)
    pause = ControlEventRecord("PAUSE", pause_moment, queue_id="q")
    evidence = _green(control_events=(pause,))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_PASS
    universe = ef.reconcile_universe(evidence)
    assert universe.unmatched_total == 0
    assert universe.pause_diagnostics == ()


def test_resume_events_are_reported_not_failures():
    """R5b default reading: in-window RESUME rows are linked by
    reviewed_by provenance, counted and reported, and are not
    failures. A RESUME without reviewed_by is schema-impossible and
    fails closed."""
    resume = ControlEventRecord(
        "RESUME", _dt(15, 10), reviewed_by="owner"
    )
    evidence = _green(control_events=(resume,))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_PASS
    assert any(
        "1 human RESUME event(s)" in reason for reason in result.reasons
    )
    malformed = ControlEventRecord("RESUME", _dt(15, 10))
    bad = _green(control_events=(malformed,))
    assert ev.criterion_f(evidence=bad).verdict == ev.VERDICT_FAIL


def test_bankroll_event_reason_classes():
    """Family 6: base-reason rows are disclosed per class and never
    failures (R6d); command-linked reasons must carry their link
    in-window; REVERSAL chains via reverses_event_id; unknown reasons
    fail closed."""
    base = BankrollEventRecord("be-1", "INITIAL", _dt(1, 8))
    disclosed = _green(bankroll_events=(base,))
    assert ev.criterion_f(evidence=disclosed).verdict == ev.VERDICT_PASS
    assert any("INITIAL=1" in reason
               for reason in ev.criterion_f(evidence=disclosed).reasons)

    orphan = BankrollEventRecord("be-2", "SCAN_ENTRY", _dt(1, 8))
    result = ev.criterion_f(evidence=_green(bankroll_events=(orphan,)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any("SCAN_ENTRY lacks a receipt-proven scan_id" in reason
               for reason in result.reasons)

    settle_orphan = BankrollEventRecord("be-3", "SETTLE_NATURAL", _dt(1, 8))
    result = ev.criterion_f(
        evidence=_green(bankroll_events=(settle_orphan,))
    )
    assert result.verdict == ev.VERDICT_FAIL
    assert any("SETTLE_NATURAL lacks a linked entry_id" in reason
               for reason in result.reasons)

    reversal = BankrollEventRecord(
        "be-4", "REVERSAL", _dt(1, 8), reverses_event_id="be-404"
    )
    result = ev.criterion_f(evidence=_green(bankroll_events=(reversal,)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any("does not chain" in reason for reason in result.reasons)

    unknown = BankrollEventRecord("be-5", "MYSTERY", _dt(1, 8))
    result = ev.criterion_f(evidence=_green(bankroll_events=(unknown,)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any("unknown reason" in reason for reason in result.reasons)


def test_linkage_families_reject_orphans():
    """Families 2/3/4/5/9/10: an in-window row whose key resolves to
    nothing supplied is unmatched and fails F (03:147 zero-unlinked).
    With pending contracts the same orphan still fails closed."""
    orphan_candidate = CandidateLinkRecord(
        "cand-orphan", scan_id=404, written_at=_dt(2, 8),
        code_commit=COMMIT,
    )
    result = ev.criterion_f(
        evidence=_green(candidates=tuple(_candidates()) + (orphan_candidate,))
    )
    assert result.verdict == ev.VERDICT_FAIL
    assert any("unknown scan_id=404" in reason for reason in result.reasons)

    entry = EntryRecord("entry-1", "cand-000", _dt(3, 9))
    evidence = _green(
        entries=(entry,),
        marks=(EntryChildRecord("entry-404", _dt(3, 10)),),
        candidate_marks=(CandidateChildRecord("cand-404", _dt(3, 10)),),
        snapshots=(SnapshotRecord("snap-1", _dt(4, 10), cited=False),),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any("unknown entry_id='entry-404'" in reason
               for reason in result.reasons)
    assert any("unknown candidate_id='cand-404'" in reason
               for reason in result.reasons)
    assert any("cited by neither" in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# Independent-review fx35-3f0cac37 regression tests (one per finding)
# ---------------------------------------------------------------------------


def test_p2_1_in_window_entry_via_unkeyed_historical_parent_fails():
    """P2-1 exact counterexample: a window ENTRY linked through an
    unkeyed HISTORICAL parent (pre-anchor NULL-key scan run) used to be
    classified matched and could produce PASS. Transitive linkage must
    terminate at a receipt; the in-window write fails closed."""
    pre_anchor_scan = ScanRunRecord(
        4242, _dt(-5, 6), operation_key=None
    )
    pre_anchor_candidate = CandidateLinkRecord(
        "cand-old", scan_id=4242, written_at=_dt(-5, 7),
        code_commit=OLD_COMMIT,
    )
    window_entry = EntryRecord("entry-hist", "cand-old", _dt(3, 9))
    events, scans = _grid(("06:00",))
    evidence = FEvidence(
        window_start=ANCHOR,
        schedule=_schedule("06:00"),
        run_events=tuple(events),
        scan_runs=tuple(scans) + (pre_anchor_scan,),
        bankroll_checks=tuple(_checks()),
        candidates=tuple(_candidates()) + (pre_anchor_candidate,),
        entries=(window_entry,),
        replay_agreements={c.candidate_id: True for c in _candidates()},
        pending_contracts=(),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "entries row 'entry-hist' references candidate_id='cand-old' "
        "whose candidate is not receipt-proven" in reason
        for reason in result.reasons
    )
    # The pre-anchor rows themselves stay disclosed, never failures
    # (R6e) — only the IN-WINDOW write fails.
    assert any("pre-anchor" in reason for reason in result.reasons)


def test_p2_1_scan_keyed_to_wrong_command_receipt_fails():
    """P2-1 exact counterexample: a scan_runs row whose operation_key
    matches only a NON-scan command receipt (same key attributed to the
    wrong command) used to count as receipt-proven. Only run-scan
    command receipts prove a scan run."""
    wrong_key = "op-owned-by-settle"
    events, scans = _grid(("06:00",))
    events = list(events) + [
        RunEventRecord(
            wrong_key, 1, "SUCCEEDED", _dt(3, 12),
            command="settle-cash",
        ),
    ]
    scans = list(scans) + [
        ScanRunRecord(
            scan_id=777, started_at=_dt(3, 12),
            operation_key=wrong_key, code_commit=COMMIT,
        ),
    ]
    evidence = _green(
        run_events=tuple(sorted(events, key=lambda e: e.occurred_at)),
        scan_runs=tuple(scans),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "operation_key='op-owned-by-settle' matches no run-scan "
        "command_run_events receipt (wrong command or no receipt)"
        in reason
        for reason in result.reasons
    )


def test_p2_1_self_reversal_and_legacy_bypasses_fail():
    """P2-1 exact counterexamples: (a) a REVERSAL chaining to itself
    proves nothing; (b) unbound in-window cash merely flagged is_legacy
    is not a bypass exemption; (c) an in-window LEGACY_IMPORT bankroll
    write is a bypass signal (the legacy class is pre-anchor only)."""
    self_reversal = BankrollEventRecord(
        "be-self", "REVERSAL", _dt(1, 8), reverses_event_id="be-self"
    )
    result = ev.criterion_f(evidence=_green(bankroll_events=(self_reversal,)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any("chains to itself" in reason for reason in result.reasons)

    legacy_flagged = BankrollEventRecord(
        "be-leg", "SETTLE_NATURAL", _dt(1, 8), is_legacy=True
    )
    result = ev.criterion_f(evidence=_green(bankroll_events=(legacy_flagged,)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "SETTLE_NATURAL lacks a linked entry_id" in reason
        for reason in result.reasons
    )

    in_window_legacy_import = BankrollEventRecord(
        "be-li", "LEGACY_IMPORT", _dt(2, 8)
    )
    result = ev.criterion_f(
        evidence=_green(bankroll_events=(in_window_legacy_import,))
    )
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "LEGACY_IMPORT in-window" in reason for reason in result.reasons
    )


def test_p2_1_settle_market_outcome_requires_entry_link():
    """P2-1 exact counterexample (loose writer label): with the pending
    contracts cleared (clearly-labeled synthetic post-B3 model), a
    settle-written market_outcome row with NO entry link used to pass on
    the writer label alone. The label proves nothing; the entry link
    does."""
    unlinked = MarketOutcomeRecord("settle", _dt(3, 12))
    evidence = _green(market_outcomes=(unlinked,))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "writer=settle lacks a linked entry_id" in reason
        for reason in result.reasons
    )

    linked = MarketOutcomeRecord("settle", _dt(3, 12), entry_id="entry-1")
    evidence = _green(
        market_outcomes=(linked,),
        entries=(EntryRecord("entry-1", "cand-000", _dt(3, 11)),),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_PASS


def test_p2_1_unkeyed_in_window_evidence_with_known_registration_fails():
    """P2-1 exact counterexample (misclassification): with a KNOWN
    anchor and schedule and zero run receipts / zero checks but an
    in-window unkeyed scan_runs row, the old code returned the
    empty-store NOT_ASSESSABLE before ever looking at the universe.
    Known registration + unkeyed in-window evidence is a KNOWN ORPHAN:
    FAIL, never NOT_ASSESSABLE."""
    unkeyed = ScanRunRecord(500, _dt(3, 12), operation_key=None)
    evidence = FEvidence(
        window_start=ANCHOR,
        schedule=_schedule("06:00"),
        run_events=(),
        scan_runs=(unkeyed,),
        bankroll_checks=(),
        pending_contracts=(),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any("NULL operation_key" in reason for reason in result.reasons)
    # The store is NOT empty: the empty-store NOT_ASSESSABLE must not
    # appear.
    assert not any("empty store" in reason for reason in result.reasons)


def test_p2_3_replay_denominator_counts_distinct_entities():
    """P2-3 exact counterexample: ONE eligible entity repeated twenty
    times used to satisfy N=20 (one replay then 'agreed'). The
    denominator counts DISTINCT candidate ids; one entity is one
    eligible candidate and the draw is evidence-absent."""
    duplicated = tuple(
        CandidateLinkRecord(
            candidate_id="cand-dup", scan_id=1, written_at=_dt(0, 7),
            code_commit=COMMIT,
        )
        for _ in range(20)
    )
    evidence = _green(
        candidates=duplicated,
        replay_agreements={"cand-dup": True},
    )
    sample = draw_replay_sample(evidence)
    assert sample.eligible_ids == ("cand-dup",)
    assert sample.selected_ids is None
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert any(
        "replay evidence absent" in reason and "1 eligible" in reason
        for reason in result.reasons
    )


def test_p2_3_replay_agreements_must_be_real_booleans():
    """P2-3 exact counterexample: twenty None outcomes used to pass
    (None is neither False nor a missing key). Non-bool agreement values
    are rejected at construction — an agreement can never be fabricated
    from a missing/None value."""
    with pytest.raises(ValueError, match="must be bool"):
        FEvidence(replay_agreements={"cand-000": None})
    with pytest.raises(ValueError, match="must be bool"):
        FEvidence(replay_agreements={"cand-000": 1})
    with pytest.raises(ValueError, match="must be bool"):
        FEvidence(replay_agreements={"cand-000": "yes"})
    # Keys must be non-empty strings.
    with pytest.raises(ValueError, match="non-empty string"):
        FEvidence(replay_agreements={"": True})
    # A real bool mapping constructs fine.
    assert FEvidence(replay_agreements={"cand-000": True})


def test_p2_4_overall_pass_requires_complete_criterion_set():
    """P2-4 exact counterexamples: criterion A PASSing alone (or any
    partial A-F set) used to yield an overall PASS, and an unknown
    verdict value used to fall through to PASS. Both fail closed to
    NOT_ASSESSABLE now; only the complete A-F set all-PASS passes."""
    a_pass = [ev.CriterionResult("A", ev.VERDICT_PASS, ())]
    assert ev.overall_verdict(a_pass) == ev.VERDICT_NOT_ASSESSABLE
    a_to_e_pass = [
        ev.CriterionResult(key, ev.VERDICT_PASS, ())
        for key in ("A", "B", "C", "D", "E")
    ]
    assert ev.overall_verdict(a_to_e_pass) == ev.VERDICT_NOT_ASSESSABLE
    unknown = [
        ev.CriterionResult("A", "MAYBE", ()),
        *[
            ev.CriterionResult(key, ev.VERDICT_PASS, ())
            for key in ("B", "C", "D", "E", "F")
        ],
    ]
    assert ev.overall_verdict(unknown) == ev.VERDICT_NOT_ASSESSABLE
    # Empty input stays byte-compatible with the pre-FX-35 behavior.
    assert ev.overall_verdict([]) == ev.VERDICT_NOT_ASSESSABLE_M1
    # FAIL still beats the fail-closed unknown branch.
    with_fail = [
        ev.CriterionResult("A", "MAYBE", ()),
        ev.CriterionResult("B", ev.VERDICT_FAIL, ()),
    ]
    assert ev.overall_verdict(with_fail) == ev.VERDICT_FAIL


def test_p3_cash_receipt_exactness_guards():
    """P3 exact counterexamples: float-zero defaults fabricated a
    recorded 0.0/0.0 for failed receipts, and invalid tolerances
    (negative, NaN, Inf) were accepted. Defaults are now None (not
    recorded) and values must be finite with tolerance >= 0."""
    with pytest.raises(ValueError, match="tolerance must be >= 0"):
        BankrollCheckRecord(
            as_of=_dt(0, 23), passed=False, tolerance=-0.001
        )
    with pytest.raises(ValueError, match="must be finite"):
        BankrollCheckRecord(
            as_of=_dt(0, 23), passed=False,
            identity_difference=float("nan"),
        )
    with pytest.raises(ValueError, match="must be finite"):
        BankrollCheckRecord(
            as_of=_dt(0, 23), passed=False, tolerance=float("inf"),
        )
    with pytest.raises(ValueError, match="finite number or None"):
        BankrollCheckRecord(
            as_of=_dt(0, 23), passed=False, identity_difference="0.5",
        )
    # Default is None (not recorded), never a fabricated 0.0.
    plain = BankrollCheckRecord(as_of=_dt(0, 23), passed=False)
    assert plain.identity_difference is None
    assert plain.tolerance is None
    # A failed day whose receipt recorded nothing reports "not
    # recorded", never a fabricated zero.
    evidence = _green(bankroll_checks=(plain,))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "identity failed" in reason and "not recorded" in reason
        for reason in result.reasons
    )
    assert not any(
        "identity failed" in reason and "0.0 vs" in reason
        for reason in result.reasons
    )


def test_p3_identity_report_is_input_order_deterministic():
    """P3 exact counterexample: the reported failing difference used to
    be ``in_day[-1]`` — input order decided which receipt was reported.
    The report now follows a deterministic order (latest as_of, failed
    receipts ranked ahead of passed ones at the same instant, then the
    recorded values), so both permutations report the same difference."""
    early_failed = BankrollCheckRecord(
        as_of=_dt(5, 22), passed=False, identity_difference=0.1,
        tolerance=0.005,
    )
    late_failed = BankrollCheckRecord(
        as_of=_dt(5, 23, 30), passed=False, identity_difference=0.7,
        tolerance=0.005,
    )
    checks = _checks(days=[d for d in range(30) if d != 5])
    forward = tuple(checks + [early_failed, late_failed])
    reverse = tuple(checks + [late_failed, early_failed])
    forward_result = ev.criterion_f(
        evidence=_green(bankroll_checks=forward)
    )
    reverse_result = ev.criterion_f(
        evidence=_green(bankroll_checks=reverse)
    )
    assert forward_result.verdict == ev.VERDICT_FAIL
    assert reverse_result.verdict == ev.VERDICT_FAIL
    forward_line = next(
        r for r in forward_result.reasons if "identity failed" in r
    )
    reverse_line = next(
        r for r in reverse_result.reasons if "identity failed" in r
    )
    assert forward_line == reverse_line
    # The latest receipt's difference is reported in either order.
    assert "0.7" in forward_line
    # Same instant, two failed receipts with different recorded
    # differences: the deterministic tiebreak (recorded value) reports
    # the same receipt in either input order.
    tie_small = BankrollCheckRecord(
        as_of=_dt(6, 23), passed=False, identity_difference=0.1,
        tolerance=0.005,
    )
    tie_large = BankrollCheckRecord(
        as_of=_dt(6, 23), passed=False, identity_difference=0.9,
        tolerance=0.005,
    )
    base = _checks(days=[d for d in range(30) if d != 6])
    for pair in ((tie_small, tie_large), (tie_large, tie_small)):
        result = ev.criterion_f(
            evidence=_green(bankroll_checks=tuple(list(base) + list(pair)))
        )
        assert result.verdict == ev.VERDICT_FAIL
        line = next(r for r in result.reasons if "identity failed" in r)
        assert "0.9" in line


def test_input_validation_guards():
    with pytest.raises(ValueError, match="at least one UTC time-of-day"):
        FEvidenceSchedule(slot_times_utc=())
    with pytest.raises(ValueError, match="duplicate slot time"):
        FEvidenceSchedule(
            slot_times_utc=(time(6, 0), time(6, 0))
        )
    with pytest.raises(ValueError, match="sorted ascending"):
        FEvidenceSchedule(slot_times_utc=(time(18, 0), time(6, 0)))
    with pytest.raises(ValueError, match="unknown phase"):
        RunEventRecord("op", 1, "VANISHED", _dt(0, 6))
    with pytest.raises(ValueError, match="timezone-aware"):
        RunEventRecord(
            "op", 1, "STARTED", datetime(2026, 10, 1, 6)
        )
    with pytest.raises(ValueError, match="unknown pending-contract family"):
        FEvidence(pending_contracts=("not_a_family",))
    with pytest.raises(ValueError, match="unknown market_outcome writer"):
        MarketOutcomeRecord("mystery_writer", _dt(1, 6))


# ---------------------------------------------------------------------------
# Shared synthetic A-E-green queue (mirrors the frozen test recipe)
# ---------------------------------------------------------------------------


def _a_to_e_green_queue():
    """210 synthetic accepted+settled candidates spanning 30 days —
    the same shape tests/test_papersim_evaluation.py uses to prove the
    A-E PASS wiring; reused here only for the overall-verdict flip."""
    t0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(210):
        day = index // 7
        hit = (index % 20) < 13
        rows.append(ev.EvaluationCandidate(
            candidate_id=f"fx35-{index}", model_key="soccer-v1",
            market_type="ou_over", gate_version="gates-v1",
            market_key=f"mkfx{index}", model_probability=0.65,
            market_mid=0.50, accepted=True, stake=100.0, fill_price=0.50,
            side="YES", fee_category="sports",
            fill_provenance=ev.FILL_BOOK_WALK,
            outcome="YES" if hit else "NO",
            recorded_pnl=100.0 if hit else -100.0,
            decision_time=t0 + timedelta(days=day),
            settled_time=t0 + timedelta(days=day, hours=6),
            close_price=0.60, match_key=None,
        ))
    return rows
