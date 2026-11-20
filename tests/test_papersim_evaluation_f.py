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

import hashlib
import json
import sys
from collections import Counter
from contextlib import contextmanager, nullcontext
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from inspect import Parameter, signature
from random import Random

import pytest

from polymarket_alpha_lab.papersim import capture, report, settle, store
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
    VisibilitySnapshot,
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
            identity_difference=Decimal("0") if day not in failed_days else Decimal("0.5"),
            tolerance=Decimal("0.005"),
            check_id=day + 1,
        )
        for day in days
    ]


def _candidates(count=25, *, day=0, scan_id=1, commit=COMMIT):
    return [
        CandidateLinkRecord(
            candidate_id=f"cand-{index:03d}" if day == 0 else f"day-{day}-cand-{index:03d}",
            scan_id=scan_id, written_at=_dt(day, 7), code_commit=commit,
            candidate_key=f"candidate-{index:03d}",
        )
        for index in range(count)
    ]


def _green(slot_times=("06:00",), *, status_for=None, **overrides) -> FEvidence:
    """A fully green synthetic window (signed case 1 shape): every
    expected slot SUCCEEDED, every day's identity passed, replay
    drawable and all-agree. The synthetic reports supply visible raw
    receipt evidence for any writer families exercised."""
    events, scans = _grid(slot_times, status_for=status_for)
    candidates = _candidates()
    parts = dict(
        window_start=ANCHOR,
        schedule=_schedule(*slot_times),
        as_of=_dt(30, 0),
        run_events=tuple(events),
        scan_runs=tuple(scans),
        bankroll_checks=tuple(_checks()),
        candidates=tuple(candidates),
        replay_agreements={c.candidate_id: True for c in candidates},
        pending_contracts=(),
    )
    parts.update(overrides)
    parts["scan_runs"] = tuple(
        scan if scan.operation_key is None or scan.scan_key is not None
        else replace(scan, scan_key=hashlib.sha256(scan.operation_key.encode()).hexdigest())
        for scan in parts["scan_runs"]
    )
    by_operation = {scan.operation_key: scan for scan in parts["scan_runs"]}
    parts["run_events"] = tuple(
        replace(event, scan_id=scan.scan_id, output_evidence=(scan.scan_key,),
                code_commit=scan.code_commit)
        if event.command == "run-scan" and event.phase == "SUCCEEDED"
        and event.scan_id is None and not event.output_evidence
        and (scan := by_operation.get(event.operation_key)) is not None
        else event
        for event in parts["run_events"]
    )
    if "control_events" in parts:
        parts["control_events"] = tuple(
            replace(row, control_event_id=index) if row.control_event_id is None else row
            for index, row in enumerate(parts["control_events"], 1)
        )
    return FEvidence(**parts)


def _inventory(snapshot_id, moment, *rows, absent=(), unreceipted=(), unreceipted_source=None):
    """An explicitly synthetic, independently observed subset of row keys."""
    return VisibilitySnapshot(
        snapshot_id, moment, (),
        tuple(ef.row_fingerprint(family, row) for family, row in rows),
        tuple(ef.row_fingerprint(family, row) for family, row in unreceipted),
        checked_absences=absent, unreceipted_source=unreceipted_source,
    )


def _known_window(evidence, *rows, unreceipted=(), unreceipted_source=None):
    families = [(family, ef.row_fingerprint(family, row).row_key) for family, row in rows]
    return replace(evidence, visibility_snapshots=(
        *evidence.visibility_snapshots,
        _inventory("synthetic-window-baseline", _dt(0, 0), absent=families),
        _inventory("synthetic-window-observation", _dt(29, 23), *rows,
                   unreceipted=unreceipted, unreceipted_source=unreceipted_source),
    ))


def _family(evidence, family):
    return next(item for item in ef.reconcile_universe(evidence).families if item.family == family)



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

    scans = tuple(_grid(("06:00",))[1])
    mixed = _green(
        scan_runs=(scans[0], replace(scans[1], code_commit=OLD_COMMIT), *scans[2:]),
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


def _replay_registration(sample_n=3, seed=-41):
    return ef.FReplayRegistration(
        sample_n, seed, "sorted-id-python-random-sample-v1",
        hashlib.sha256(f"synthetic prior approval {sample_n} {seed}".encode()).hexdigest(),
    )


def _replay_evidence(**overrides):
    scans = _grid(("06:00",))[1]
    scans[1] = replace(scans[1], code_commit=OLD_COMMIT)
    current = tuple(
        replace(candidate, candidate_id=index)
        for index, candidate in enumerate(_candidates(), 1)
    )
    older = _candidates(count=1, day=1, scan_id=2, commit=OLD_COMMIT)[0]
    parts = dict(
        scan_runs=tuple(scans),
        candidates=(*current, current[0], older),
        replay_agreements={str(row.candidate_id): True for row in current},
    )
    parts.update(overrides)
    return _green(**parts)


def _assert_replay_verdict(evidence, verdict):
    direct = ev.criterion_f(evidence=evidence)
    (queue,) = ev.evaluate_queues(
        _a_to_e_green_queue()[:1], f_evidence=evidence,
    ).queues
    assert queue.promotion[-1] == direct
    assert direct.key == "F"
    assert direct.verdict == verdict
    return direct.reasons


def test_omitted_replay_registration_keeps_legacy_draw_and_report():
    evidence = _replay_evidence()
    default = draw_replay_sample(evidence)
    assert default.selected_ids == (
        "22", "3", "24", "20", "18", "15", "13", "21", "12", "25",
        "11", "23", "9", "8", "7", "16", "14", "17", "10", "2",
    )
    assert draw_replay_sample(evidence, seed=20261120, sample_n=20) == default
    assert draw_replay_sample(evidence, seed=-41, sample_n=3).selected_ids == ("20", "19", "16")
    assert draw_replay_sample(evidence, sample_n=3).selected_ids == ("22", "3", "24")
    assert draw_replay_sample(evidence, sample_n=0).selected_ids == ()
    assert draw_replay_sample(evidence, seed=-41).selected_ids == (
        "20", "19", "16", "14", "9", "3", "18", "25", "17", "5",
        "21", "2", "23", "4", "1", "12", "8", "7", "10", "15",
    )
    reasons = _assert_replay_verdict(evidence, ev.VERDICT_PASS)
    assert tuple(reason for reason in reasons if "replay" in reason) == (
        f"replay sample: 20 of 25 eligible at trusted commit {COMMIT!r}, "
        "all agreed (seeded registered draw)",
    )
    assert "1 in-window candidate(s) at older commits listed: day-1-cand-000" in reasons


def test_replay_registration_is_immutable_and_keyword_only_on_evidence():
    registration = _replay_registration()
    assert signature(FEvidence).parameters["replay_registration"].kind == Parameter.KEYWORD_ONLY
    evidence = FEvidence(replay_registration=registration)
    assert evidence.replay_registration is registration
    with pytest.raises(FrozenInstanceError):
        registration.seed = 0
    with pytest.raises(FrozenInstanceError):
        evidence.replay_registration = None


@pytest.mark.parametrize(("sample_n", "seed", "expected"), [
    (1, 1, ("13",)),
    (3, -41, ("20", "19", "16")),
    (7, 0, ("20", "21", "10", "17", "24", "23", "9")),
])
def test_registered_replay_draw_results_and_final_denominator(sample_n, seed, expected):
    registration = _replay_registration(sample_n, seed)
    supplied = dict.fromkeys(expected, True)
    evidence = _replay_evidence(replay_registration=registration, replay_agreements=supplied)
    sample = draw_replay_sample(evidence)
    assert sample.trusted_commit == COMMIT
    assert sample.eligible_ids == tuple(sorted(map(str, range(1, 26))))
    assert sample.older_commit_ids == ("day-1-cand-000",)
    assert sample.older_commit_count == 1
    assert sample.selected_ids == expected
    assert draw_replay_sample(evidence, seed=seed, sample_n=sample_n) == sample
    assert draw_replay_sample(evidence, seed=seed) == sample
    assert draw_replay_sample(evidence, sample_n=sample_n) == sample

    reasons = _assert_replay_verdict(evidence, ev.VERDICT_PASS)
    assert any(
        all(value in reason for value in (
            "(R2)", registration.rule_version, registration.source_sha256,
            f"seed {seed}", f"N {sample_n}",
        ))
        for reason in reasons
    )
    assert (
        f"replay sample: {sample_n} of 25 eligible at trusted commit {COMMIT!r}, "
        "all agreed (seeded registered draw)"
    ) in reasons
    assert tuple(reason for reason in reasons if reason.startswith("replay result (R2)")) == tuple(
        f"replay result (R2) candidate {candidate_id!r} matched at trusted commit {COMMIT!r}"
        for candidate_id in expected
    )
    assert "1 in-window candidate(s) at older commits listed: day-1-cand-000" in reasons

    unselected = set(sample.eligible_ids).difference(expected) | set(sample.older_commit_ids)
    permuted = replace(
        evidence,
        candidates=evidence.candidates[::-1],
        replay_agreements={**dict.fromkeys(unselected, False), **supplied},
    )
    assert draw_replay_sample(permuted) == sample
    assert _assert_replay_verdict(permuted, ev.VERDICT_PASS) == reasons


@pytest.mark.parametrize(("parameter", "wrong"), [
    ("seed", 20261120), ("sample_n", 20),
    ("seed", True), ("sample_n", True),
    ("seed", 1.0), ("sample_n", 1.0),
    ("seed", None), ("sample_n", None),
])
def test_registered_replay_draw_rejects_conflicting_explicit_parameters(parameter, wrong):
    evidence = _replay_evidence(replay_registration=_replay_registration(1, 1))
    with pytest.raises(ValueError, match=rf"{parameter}.*registration"):
        draw_replay_sample(evidence, **{parameter: wrong})


def test_registered_replay_discloses_missing_and_mismatched_selected_results():
    expected = ("20", "19", "16")
    evidence = _replay_evidence(replay_registration=_replay_registration())
    agreements = dict(evidence.replay_agreements)
    del agreements[expected[0]]
    missing = replace(evidence, replay_agreements=agreements)
    reasons = _assert_replay_verdict(missing, ev.VERDICT_NOT_ASSESSABLE)
    assert "replay results incomplete: 1 of 3 selected candidate(s) lack a result: 20" in reasons
    assert f"replay result (R2) candidate '20' missing at trusted commit {COMMIT!r}" in reasons

    agreements[expected[1]] = False
    mixed = replace(evidence, replay_agreements=agreements)
    reasons = _assert_replay_verdict(mixed, ev.VERDICT_FAIL)
    assert "replay mismatch (03:146 全部一致): candidate(s) 19 did not reproduce" in reasons
    assert f"replay result (R2) candidate '19' mismatched at trusted commit {COMMIT!r}" in reasons
    assert f"replay result (R2) candidate '20' missing at trusted commit {COMMIT!r}" in reasons
    assert f"replay result (R2) candidate '16' matched at trusted commit {COMMIT!r}" in reasons

    no_results = replace(evidence, replay_agreements=None)
    reasons = _assert_replay_verdict(no_results, ev.VERDICT_NOT_ASSESSABLE)
    assert "replay results not supplied for the 3 selected candidate(s) (R2)" in reasons
    assert tuple(reason for reason in reasons if reason.startswith("replay result (R2)")) == tuple(
        f"replay result (R2) candidate {candidate_id!r} missing at trusted commit {COMMIT!r}"
        for candidate_id in expected
    )


def test_registered_replay_insufficient_pool_uses_registered_n():
    evidence = _replay_evidence(replay_registration=_replay_registration(26, 0))
    sample = draw_replay_sample(evidence)
    assert len(sample.eligible_ids) == 25
    assert sample.selected_ids is None
    assert (
        f"replay evidence absent: 25 eligible candidate(s) at trusted commit {COMMIT!r} < registered N 26"
    ) in _assert_replay_verdict(evidence, ev.VERDICT_NOT_ASSESSABLE)


@pytest.fixture(params=["criterion_f", "evaluate_queues"])
def registered_replay_entry_point(request):
    if request.param == "criterion_f":
        return ev.criterion_f

    def evaluate_queue(evidence):
        (queue,) = ev.evaluate_queues(
            _a_to_e_green_queue()[:1], f_evidence=evidence,
        ).queues
        return queue.promotion[-1]

    return evaluate_queue


@contextmanager
def _decimal_conversion_limit(limit):
    original_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(limit)
    try:
        yield
    finally:
        sys.set_int_max_str_digits(original_limit)


@pytest.fixture
def integer_above_decimal_digit_limit():
    default_limit = sys.int_info.default_max_str_digits
    limit = min(sys.get_int_max_str_digits() or default_limit, default_limit)
    with _decimal_conversion_limit(limit):
        yield 10**limit


@pytest.mark.parametrize("sign", [1, -1], ids=["positive", "negative"])
def test_registered_replay_reports_extreme_signed_seed(
    registered_replay_entry_point, integer_above_decimal_digit_limit, sign,
):
    seed = sign * integer_above_decimal_digit_limit
    registration = replace(_replay_registration(), seed=seed)
    evidence = _replay_evidence(replay_registration=registration)
    sample = draw_replay_sample(evidence)
    assert sample == draw_replay_sample(evidence, seed=seed, sample_n=3)
    assert sample.selected_ids is not None
    assert len(sample.selected_ids) == 3
    evidence = replace(evidence, replay_agreements=dict.fromkeys(sample.selected_ids, True))

    limit = sys.get_int_max_str_digits()
    result = registered_replay_entry_point(evidence)
    assert sys.get_int_max_str_digits() == limit
    assert result.key == "F"
    assert result.verdict == ev.VERDICT_PASS
    assert (
        f"replay registration (R2) fixes N 3, seed {hex(seed)}, "
        f"rule {registration.rule_version}, and source SHA-256 {registration.source_sha256}"
    ) in result.reasons


def test_registered_replay_reports_extreme_n_when_pool_is_insufficient(
    registered_replay_entry_point, integer_above_decimal_digit_limit,
):
    sample_n = integer_above_decimal_digit_limit
    registration = replace(_replay_registration(seed=0), sample_n=sample_n)
    evidence = _replay_evidence(replay_registration=registration)
    sample = draw_replay_sample(evidence)
    assert sample == draw_replay_sample(evidence, sample_n=sample_n, seed=0)
    assert len(sample.eligible_ids) == 25
    assert sample.selected_ids is None

    limit = sys.get_int_max_str_digits()
    result = registered_replay_entry_point(evidence)
    assert sys.get_int_max_str_digits() == limit
    assert result.key == "F"
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert (
        f"replay registration (R2) fixes N {hex(sample_n)}, seed 0, "
        f"rule {registration.rule_version}, and source SHA-256 {registration.source_sha256}"
    ) in result.reasons
    assert (
        "replay evidence absent: 25 eligible candidate(s) "
        f"at trusted commit {COMMIT!r} < registered N {hex(sample_n)}"
    ) in result.reasons


def test_registered_replay_marks_extreme_negative_n_as_missing_evidence(
    registered_replay_entry_point, integer_above_decimal_digit_limit,
):
    registration = replace(_replay_registration(), sample_n=-integer_above_decimal_digit_limit)
    evidence = _replay_evidence(replay_registration=registration)
    sample = draw_replay_sample(evidence)
    assert sample.selected_ids is None
    assert sample.shortfalls == ("replay registration sample_n must be a positive integer (R2)",)
    result = registered_replay_entry_point(evidence)
    assert result.key == "F"
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert sample.shortfalls[0] in result.reasons


@pytest.mark.parametrize("drawable", [True, False], ids=["drawable", "insufficient"])
def test_registered_replay_serializes_extreme_integers_with_unlimited_decimal_conversion(drawable):
    digits = sys.int_info.default_max_str_digits
    magnitude = 10**digits
    decimal_magnitude = "1" + "0" * digits
    seed = -magnitude
    sample_n = 3 if drawable else magnitude
    expected_n = "3" if drawable else decimal_magnitude
    registration = replace(_replay_registration(), sample_n=sample_n, seed=seed)
    evidence = _replay_evidence(replay_registration=registration)

    with _decimal_conversion_limit(0):
        sample = draw_replay_sample(evidence)
        assert len(sample.eligible_ids) == 25
        if drawable:
            expected_ids = tuple(Random(seed).sample(sample.eligible_ids, sample_n))
            assert sample.selected_ids == expected_ids
            evidence = replace(evidence, replay_agreements=dict.fromkeys(expected_ids, True))
            verdict = ev.VERDICT_PASS
            replay_reason = (
                f"replay sample: 3 of 25 eligible at trusted commit {COMMIT!r}, "
                "all agreed (seeded registered draw)"
            )
        else:
            assert sample.selected_ids is None
            verdict = ev.VERDICT_NOT_ASSESSABLE
            replay_reason = (
                "replay evidence absent: 25 eligible candidate(s) "
                f"at trusted commit {COMMIT!r} < registered N {expected_n}"
            )

        direct = ev.criterion_f(evidence=evidence)
        queue_report = ev.evaluate_queues(_a_to_e_green_queue()[:1], f_evidence=evidence)
        (queue,) = queue_report.queues
        assert queue.promotion[-1] == direct
        assert direct.verdict == verdict

        payload = report.canonical_report_json(report.report_json_value(queue_report))
        serialized = json.loads(payload)["queues"][0]["promotion"][-1]
        assert serialized == {"key": "F", "verdict": verdict, "reasons": list(direct.reasons)}
        assert (
            f"replay registration (R2) fixes N {expected_n}, seed -{decimal_magnitude}, "
            f"rule {registration.rule_version}, and source SHA-256 {registration.source_sha256}"
        ) in serialized["reasons"]
        assert replay_reason in serialized["reasons"]
        assert evidence.replay_registration is registration
        assert type(registration.sample_n) is int and registration.sample_n == sample_n
        assert type(registration.seed) is int and registration.seed == seed
        assert sys.get_int_max_str_digits() == 0


def test_registered_replay_does_not_fall_back_to_an_earlier_trusted_scan():
    evidence = _replay_evidence(replay_registration=_replay_registration())
    latest = replace(evidence.scan_runs[-1], code_commit=None)
    untrusted = replace(evidence, scan_runs=(*evidence.scan_runs[:-1], latest))
    sample = draw_replay_sample(untrusted)
    assert sample.trusted_commit is None
    assert sample.selected_ids is None
    (shortfall,) = sample.shortfalls
    assert f"latest in-window scan {latest.scan_id}" in shortfall
    assert shortfall in _assert_replay_verdict(untrusted, ev.VERDICT_NOT_ASSESSABLE)


@pytest.mark.parametrize(("field", "invalid"), [
    ("sample_n", 0), ("sample_n", -1), ("sample_n", True), ("sample_n", 3.0),
    ("sample_n", None), ("sample_n", "3"), ("seed", False), ("seed", -41.0),
    ("seed", None), ("seed", "0"), ("rule_version", "next-v9"),
    ("rule_version", ""), ("rule_version", 1), ("source_sha256", ""),
    ("source_sha256", "0" * 63), ("source_sha256", "A" * 64),
    ("source_sha256", "x" * 64), ("source_sha256", None),
])
def test_invalid_replay_registration_reports_a_shortfall(field, invalid):
    registration = replace(_replay_registration(), **{field: invalid})
    evidence = _replay_evidence(replay_registration=registration)
    sample = draw_replay_sample(evidence, seed=-41, sample_n=3)
    assert sample.selected_ids is None
    shortfall, *_ = sample.shortfalls
    assert field in shortfall and "(R2)" in shortfall
    assert shortfall in _assert_replay_verdict(evidence, ev.VERDICT_NOT_ASSESSABLE)


@pytest.mark.parametrize("registration", [None, {}], ids=["missing", "invalid record"])
def test_missing_or_untyped_replay_registration_preserves_known_orphan(registration):
    evidence = _replay_evidence(replay_registration=registration)
    sample = draw_replay_sample(evidence, seed=20261120, sample_n=20)
    assert sample.selected_ids is None
    shortfall, *_ = sample.shortfalls
    assert "replay" in shortfall and "(R2)" in shortfall
    assert shortfall in _assert_replay_verdict(evidence, ev.VERDICT_NOT_ASSESSABLE)
    assert draw_replay_sample(replace(evidence, window_start=None)).shortfalls == (shortfall,)

    orphan = ScanRunRecord(scan_id=500, started_at=_dt(3, 12), operation_key=None)
    for as_of in (evidence.as_of, _dt(10, 12)):
        with_orphan = replace(evidence, as_of=as_of, scan_runs=(*evidence.scan_runs, orphan))
        reasons = _assert_replay_verdict(with_orphan, ev.VERDICT_FAIL)
        assert any("NULL operation_key" in reason and "500" in reason for reason in reasons)
        assert shortfall in reasons

    for window in (replace(evidence, as_of=_dt(10, 12)), replace(evidence, window_start=None)):
        assert shortfall in _assert_replay_verdict(window, ev.VERDICT_NOT_ASSESSABLE)


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
        MarketOutcomeRecord("legacy_import", _dt(-3, 12), outcome_id=990, outcome="YES"),
        ScanRunRecord(999, _dt(-2, 6), operation_key=None),
    )
    evidence = _green(
        market_outcomes=(pre_anchor_rows[0],),
        scan_runs=tuple(_grid(("06:00",))[1]) + (pre_anchor_rows[1],),
        visibility_snapshots=(_inventory(
            "legacy-cutover", _dt(-1), ("scan_runs", pre_anchor_rows[1]),
            ("market_outcomes", pre_anchor_rows[0]),
        ),),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_PASS
    assert any(
        "pre-anchor" in reason and "2 row(s)" in reason
        for reason in result.reasons
    )
    imported = MarketOutcomeRecord("legacy_import", _dt(3, 12), outcome_id=991, outcome="YES")
    in_window = _known_window(_green(market_outcomes=(imported,)), ("market_outcomes", imported),
                              unreceipted=(("market_outcomes", imported),),
                              unreceipted_source="verified synthetic legacy import tool")
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
    result = ev.criterion_f(evidence=_known_window(_green(bankroll_events=(orphan,)), ("bankroll_events", orphan)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any("SCAN_ENTRY lacks a receipt-proven scan_id" in reason
               for reason in result.reasons)

    settle_orphan = BankrollEventRecord("be-3", "SETTLE_NATURAL", _dt(1, 8))
    result = ev.criterion_f(
        evidence=_known_window(_green(bankroll_events=(settle_orphan,)), ("bankroll_events", settle_orphan))
    )
    assert result.verdict == ev.VERDICT_FAIL
    assert any("SETTLE_NATURAL lacks a linked entry_id" in reason
               for reason in result.reasons)

    reversal = BankrollEventRecord(
        "be-4", "REVERSAL", _dt(1, 8), reverses_event_id="be-404"
    )
    result = ev.criterion_f(evidence=_known_window(_green(bankroll_events=(reversal,)), ("bankroll_events", reversal)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any("does not chain" in reason for reason in result.reasons)

    unknown = BankrollEventRecord("be-5", "MYSTERY", _dt(1, 8))
    result = ev.criterion_f(evidence=_known_window(_green(bankroll_events=(unknown,)), ("bankroll_events", unknown)))
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
    result = ev.criterion_f(evidence=_known_window(
        _green(candidates=tuple(_candidates()) + (orphan_candidate,)), ("candidates", orphan_candidate),
    ))
    assert result.verdict == ev.VERDICT_FAIL
    assert any("unknown scan_id=404" in reason for reason in result.reasons)

    entry = EntryRecord("entry-1", "cand-000", _dt(3, 9))
    mark = EntryChildRecord("entry-404", _dt(3, 10), row_key=81)
    candidate_mark = CandidateChildRecord("cand-404", _dt(3, 10), row_key=82)
    snapshot = SnapshotRecord("snap-1", _dt(4, 10), cited=False)
    evidence = _known_window(
        _green(entries=(entry,), marks=(mark,), candidate_marks=(candidate_mark,), snapshots=(snapshot,)),
        ("marks", mark), ("candidate_marks", candidate_mark), ("snapshots", snapshot),
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
        visibility_snapshots=(_inventory("legacy-parent-cutover", _dt(-1),
                                         ("scan_runs", pre_anchor_scan),
                                         ("candidates", pre_anchor_candidate)),),
        replay_agreements={c.candidate_id: True for c in _candidates()},
        pending_contracts=(),
    )
    evidence = _known_window(evidence, ("entries", window_entry))
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
    evidence = _known_window(evidence, ("scan_runs", evidence.scan_runs[-1]))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "operation_key='op-owned-by-settle' matches no run-scan "
        "command_run_events receipt (wrong command or no receipt)"
        in reason
        for reason in result.reasons
    )


def test_p2_reversal_mutual_pair_fails():
    """Re-review P2 exact counterexample: two IN-WINDOW REVERSAL rows
    pointing at each other (zero receipts anywhere) used to be
    classified matched with unmatched_total == 0 and could produce
    PASS. The reversal chain must transitively close at a receipt; a
    cycle (mutual pair included) fails closed."""
    be_a = BankrollEventRecord(
        "be-a", "REVERSAL", _dt(1, 8), reverses_event_id="be-b"
    )
    be_b = BankrollEventRecord(
        "be-b", "REVERSAL", _dt(1, 9), reverses_event_id="be-a"
    )
    evidence = _known_window(_green(bankroll_events=(be_a, be_b)),
                             ("bankroll_events", be_a), ("bankroll_events", be_b))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "REVERSAL chain cycles at 'be-a'" in reason
        or "REVERSAL chain cycles at 'be-b'" in reason
        for reason in result.reasons
    )
    universe = ef.reconcile_universe(evidence)
    # BOTH directions are orphaned — the pair proves nothing either way.
    assert universe.unmatched_total == 2
    assert not any(
        "unmatched business rows: 0" in reason for reason in result.reasons
    )


def test_p2_reversal_to_pre_anchor_or_base_terminal_fails():
    """Re-review P2 variant: an in-window REVERSAL whose chain
    terminates at a PRE-ANCHOR disclosed row (or any unreceipted
    terminal — a base-reason owner action included) used to pass. A
    reversal lineage must close at a receipt-proven row; disclosed or
    base terminals do not terminate it as proven."""
    pre_anchor_initial = BankrollEventRecord(
        "be-pre", "INITIAL", _dt(-2, 8)
    )
    to_pre_anchor = BankrollEventRecord(
        "be-win", "REVERSAL", _dt(1, 8), reverses_event_id="be-pre"
    )
    evidence = _known_window(
        _green(bankroll_events=(pre_anchor_initial, to_pre_anchor), visibility_snapshots=(
            _inventory("prior-base", _dt(-1), ("bankroll_events", pre_anchor_initial)),
        )), ("bankroll_events", to_pre_anchor),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "REVERSAL chain terminates at 'be-pre' which is not "
        "receipt-proven" in reason
        for reason in result.reasons
    )
    # The pre-anchor base row itself stays disclosed, never a failure
    # (R6e) — only the IN-WINDOW reversal fails.
    assert any("pre-anchor" in reason for reason in result.reasons)

    # An in-window base-reason terminal is equally unreceipted.
    in_window_adjustment = BankrollEventRecord(
        "be-base", "ADJUSTMENT", _dt(2, 8)
    )
    to_base = BankrollEventRecord(
        "be-win2", "REVERSAL", _dt(2, 9), reverses_event_id="be-base"
    )
    result = ev.criterion_f(
        evidence=_known_window(_green(bankroll_events=(in_window_adjustment, to_base)),
                               ("bankroll_events", in_window_adjustment), ("bankroll_events", to_base))
    )
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "REVERSAL chain terminates at 'be-base' which is not "
        "receipt-proven" in reason
        for reason in result.reasons
    )


def test_p2_reversal_long_chain_terminating_proven_passes():
    """Re-review P2 positive control: a long reversal chain whose
    terminal is a receipt-proven row (SETTLE_NATURAL on a proven entry)
    is matched — the lineage closes at a receipt through the chain."""
    # The published positive stub named an entry. This fixture proves its
    # terminal cash via the exact #104 settlement and writer receipt.
    grouped = _settlement_fixture()
    terminal, = _terminal_cash(grouped)
    middle = BankrollEventRecord(
        "be-r2", "REVERSAL", _dt(6, 13), reverses_event_id=terminal.event_id
    )
    head = BankrollEventRecord(
        "be-r1", "REVERSAL", _dt(6, 14), reverses_event_id="be-r2"
    )
    evidence = _known_window(
        replace(grouped, bankroll_events=(*grouped.bankroll_events, head, middle)),
        ("bankroll_events", head), ("bankroll_events", middle),
    )
    universe = ef.reconcile_universe(evidence)
    assert universe.unmatched_total == 0
    assert ev.criterion_f(evidence=evidence).verdict == ev.VERDICT_PASS
    # The same chain with the terminal's entry link removed is no
    # longer proven: the head reversal fails with the whole chain.
    orphan_terminal = BankrollEventRecord(
        "be-term2", "SETTLE_NATURAL", _dt(3, 12)
    )
    orphan_middle = BankrollEventRecord(
        "be-r2", "REVERSAL", _dt(3, 13), reverses_event_id="be-term2"
    )
    orphan_head = BankrollEventRecord(
        "be-r1", "REVERSAL", _dt(3, 14), reverses_event_id="be-r2"
    )
    orphan = _known_window(
        _green(bankroll_events=(orphan_head, orphan_middle, orphan_terminal)),
        *(("bankroll_events", row) for row in (orphan_head, orphan_middle, orphan_terminal)),
    )
    orphan_result = ev.criterion_f(evidence=orphan)
    assert orphan_result.verdict == ev.VERDICT_FAIL
    assert any(
        "REVERSAL chain terminates at 'be-term2' which is not "
        "receipt-proven" in reason
        for reason in orphan_result.reasons
    )


def test_p2_1_self_reversal_and_legacy_bypasses_fail():
    """P2-1 exact counterexamples: (a) a REVERSAL chaining to itself
    proves nothing; (b) unbound in-window cash merely flagged is_legacy
    is not a bypass exemption; (c) an in-window LEGACY_IMPORT bankroll
    write is a bypass signal (the legacy class is pre-anchor only)."""
    self_reversal = BankrollEventRecord(
        "be-self", "REVERSAL", _dt(1, 8), reverses_event_id="be-self"
    )
    result = ev.criterion_f(evidence=_known_window(_green(bankroll_events=(self_reversal,)), ("bankroll_events", self_reversal)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any("chains to itself" in reason for reason in result.reasons)

    legacy_flagged = BankrollEventRecord(
        "be-leg", "SETTLE_NATURAL", _dt(1, 8), is_legacy=True
    )
    result = ev.criterion_f(evidence=_known_window(_green(bankroll_events=(legacy_flagged,)), ("bankroll_events", legacy_flagged)))
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "SETTLE_NATURAL lacks a linked entry_id" in reason
        for reason in result.reasons
    )

    in_window_legacy_import = BankrollEventRecord(
        "be-li", "LEGACY_IMPORT", _dt(2, 8)
    )
    result = ev.criterion_f(
        evidence=_known_window(_green(bankroll_events=(in_window_legacy_import,)), ("bankroll_events", in_window_legacy_import))
    )
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "LEGACY_IMPORT in-window" in reason for reason in result.reasons
    )


def test_p2_1_settle_market_outcome_requires_entry_link():
    """P2-1 exact counterexample (loose writer label): with the pending
    contracts cleared (clearly-labeled synthetic post-B3 model), a
    settle-written market_outcome row with NO entry link used to pass on
    the writer label alone. The published synthetic entry-link assertion
    (PASS) changes to R3 because the actual B3 writer reads outcomes."""
    unlinked = MarketOutcomeRecord("settle", _dt(3, 12))
    evidence = _green(market_outcomes=(unlinked,))
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_FAIL
    assert any(
        "writer=settle lacks a linked entry_id" in reason
        for reason in result.reasons
    )

    linked = MarketOutcomeRecord("settle", _dt(3, 12), "entry-1")
    assert linked.entry_id == "entry-1" and linked.outcome_id is None
    evidence = _green(
        market_outcomes=(linked,),
        entries=(EntryRecord("entry-1", "cand-000", _dt(3, 11)),),
    )
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert _family(evidence, "market_outcomes").matched == 0
    assert any("legacy entry reference 'entry-1'" in reason and "settle consumes outcomes" in reason
               for reason in result.reasons)


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


def _snapshot(content, *, at=None, kind=None):
    return SnapshotRecord(
        hashlib.sha256(content).hexdigest(), at or _dt(2, 8),
        content=content, byte_length=len(content), kind=kind,
    )


def _capture_success(packs, *, targets=(), attempt=1, hour=10):
    observations = [row for pack in packs for row in capture.observations_from_pack(pack.content)]
    assert len(observations) == len(targets)
    marks = close = missing_quote = 0
    downgrades = {capture.DOWNGRADE_MISSING_EVENT_START: 0,
                  capture.DOWNGRADE_OBSERVED_AT_AFTER_EVENT_START: 0}
    for observation, target in zip(observations, targets, strict=True):
        if not capture.has_any_price(observation):
            missing_quote += 1
            continue
        marks += 1
        kind, reason = capture.classify_observation(
            observation.sampling_kind, observation.observed_at, target.event_start_at,
        )
        close += kind == "CLOSE"
        if reason:
            downgrades[reason] += 1
    summary = {
        "command": "capture-marks",
        "team": "football",
        "operator": "synthetic-fixture",
        "code_commit": COMMIT,
        "packs": [
            {"pack": f"pack-{index}.json", "snapshot_sha256": pack.snapshot_id}
            for index, pack in enumerate(packs)
        ],
        "candidates_seen": len({target.candidate_id for target in targets}),
        "observations": len(observations),
        "marks_written": marks,
        "close_eligible": close,
        "close_evidence_written": close,
        "missing_quote": missing_quote,
        "missing_event_start": len({target.candidate_id for target in targets if target.event_start_at is None}),
        "downgraded_to_periodic": sum(downgrades.values()),
        "downgrade_reasons": downgrades,
        "source_kind_counts": dict(Counter(row.source_kind for row in observations)),
        "coverage_terms": capture.COVERAGE_TERMS,
    }
    return RunEventRecord(
        "capture-marks-synthetic", attempt, "SUCCEEDED", _dt(5, hour),
        command="capture-marks", code_commit=COMMIT,
        output_evidence=(json.dumps(summary, sort_keys=True, separators=(",", ":")),),
    )


def _quote_rows(raw, candidate, pack):
    observation, = capture.observations_from_pack(json.dumps([raw]).encode())
    kind, _reason = capture.classify_observation(
        observation.sampling_kind, observation.observed_at, candidate.event_start_at,
    )
    stored = capture.capture_from_observation(
        observation, candidate_id=candidate.candidate_id, sampling_kind=kind,
        pack_snapshot_sha256=pack.snapshot_id,
    ).validate()
    mark_row = CandidateChildRecord(
        stored.candidate_id, stored.observed_at, row_key=501,
        candidate_mark_key=stored.mark_key(), **{
            key: value for key, value in stored.comparison().items()
            if key not in ("candidate_id", "observed_at")
        },
    )
    close_row = None
    if kind == "CLOSE":
        close = capture.close_evidence_from_observation(
            observation, pack_snapshot_sha256=pack.snapshot_id,
        ).validate()
        close_row = CandidateChildRecord(
            stored.candidate_id, close.observed_at, row_key=502,
            evidence_role=close.evidence_role,
            evidence_key=close.evidence_key_with(stored.candidate_id),
            market_ref_kind=close.market_ref_kind, market_ref=close.market_ref,
            token_ref=close.token_ref, side=close.side, source_kind=close.source_kind,
            source_ref=close.source_ref, snapshot_sha256=close.snapshot_sha256,
            bid=close.bid, ask=close.ask, mid=close.mid, last=close.last,
        )
    return mark_row, close_row


def _capture_fixture(evidence=None, *, old_observation=False, explicit_digest=False):
    evidence = evidence or _green()
    source = _snapshot(b"synthetic quote reply", at=_dt(-3, 8))
    scan = evidence.scan_runs[0]
    candidate = CandidateLinkRecord(
        51, scan.scan_id, _dt(0, 7), COMMIT, candidate_key="capture-target",
        event_start_at=_dt(5, 18),
    )
    quote = {
        "candidate_key": candidate.candidate_key, "scan_operation_key": scan.operation_key,
        "sampling_kind": "CLOSE", "observed_at": _dt(-2 if old_observation else 5, 8).isoformat(),
        "market_ref": "synthetic-market", "token_ref": "synthetic-token", "bid": 0.4,
        "ask": 0.6, "mid": 0.5, "last": 0.55, "source_kind": "clob_book",
        "source_ref": "synthetic-book",
    }
    if explicit_digest:
        quote["snapshot_sha256"] = source.snapshot_id
    pack = _snapshot(json.dumps([quote]).encode(), at=_dt(-2, 7), kind="capture_mark_pack")
    mark, close = _quote_rows(quote, candidate, pack)
    alternate = replace(candidate, candidate_id=52, candidate_key="capture-alternate")
    evidence = replace(
        evidence,
        candidates=(*evidence.candidates, candidate, alternate),
        candidate_marks=(mark,), price_evidence=(close,),
        snapshots=(pack, source) if explicit_digest else (pack,),
        replay_agreements={**evidence.replay_agreements, "51": True, "52": True},
        run_events=(*evidence.run_events, _capture_success((pack,), targets=(candidate,))),
    )
    return evidence, quote


def _settle_success(*items, attempt=1):
    return RunEventRecord(
        "settle-synthetic", attempt, "SUCCEEDED", _dt(5, 10 + attempt),
        command="settle", code_commit=COMMIT, output_evidence=items,
    )


def _settlement_fixture(
    evidence=None, *, two_entries=False, outcome_token="YES", fee=Decimal("0.10"),
    legacy=False, first_confirmed=True,
):
    evidence = evidence or _green()
    rows = [(101, 201, outcome_token)]
    if two_entries:
        rows.append((102, 202, "VOIDED"))
    entries, outcomes, settlements, cash_legs, openings, claims = [], [], [], [], [], []
    candidates = list(evidence.candidates)
    historical = ScanRunRecord(600, _dt(-5, 6))
    previous = [("scan_runs", historical)]
    for index, (entry_id, outcome_id, result) in enumerate(rows):
        outcome_time = _dt(-3, 9 + index)
        confirmed_at = outcome_time if first_confirmed or index else None
        identity = settle.MarketIdentity(condition_id=f"market-{index}")
        market = dict(condition_id=identity.condition_id, market_id=None, market_slug=None)
        candidate = CandidateLinkRecord(
            61 + index, historical.scan_id, _dt(-5, 7), COMMIT,
            candidate_key=f"settlement-target-{index}", side="YES", **market,
        )
        candidates.append(candidate)
        outcome = MarketOutcomeRecord(
            None, outcome_time, outcome_id=outcome_id, outcome=result,
            confirmed_at=confirmed_at, source_kind="fixture", **market,
        )
        outcomes.append(outcome)
        stake, price = Decimal("25"), Decimal("0.4")
        fee_source = "estimated" if legacy else "schedule"
        entry = EntryRecord(entry_id, candidate.candidate_id, _dt(-5, 8),
                            stake=stake, entry_fee=fee, price=price, fee_source=fee_source,
                            fee_category="sports", is_legacy=legacy)
        entries.append(entry)
        debit = BankrollEventRecord(
            390 + index, "SCAN_ENTRY", entry.written_at, scan_id=historical.scan_id,
            entry_id=entry_id, event_key=f"entry:{entry_id}:cash",
            amount=store.exact_sum(stake, fee).copy_negate(), is_legacy=legacy,
        )
        openings.append(debit)
        planned = settle.plan_settlement(settle.OpenSettleEntry(
            entry_id, candidate.candidate_id, "football", "YES", stake, price, "sports",
            entry.written_at, fee_source=fee_source, entry_fee=fee, is_legacy=legacy,
            opening_cash=settle.OpeningCash(debit.event_id, debit.amount, fee),
            outcome=settle.CapturedOutcome(
                outcome_id, result, outcome_time, confirmed_at, source_kind="fixture", identity=identity,
            ), **market,
        ), operator="synthetic")
        assert planned.status == "SETTLED" and planned.automatic is not None
        record = planned.record
        assert record.classification == ("VOIDED_REFUND" if result == "VOIDED" else "NATURAL")
        assert record.source_outcome_id == outcome_id
        settlements.append(EntryChildRecord(
            entry_id, record.confirmed_at, row_key=300 + index, classification=record.classification,
            source_outcome_id=record.source_outcome_id, proceeds=record.proceeds, is_legacy=legacy,
            outcome_label=record.outcome_label, evidence=record.evidence,
            **{name: getattr(record, name) for name in (
                "win_loss", "pnl_gross", "fee_total", "fee_source", "pnl_net",
                "exit_price", "exit_mark_id", "confirmed_by",
            )},
        ))
        cash_legs.append(BankrollEventRecord(
            400 + index, record.cash_reason(), record.confirmed_at, entry_id=entry_id,
            event_key=record.event_key(), amount=record.cash_amount(entry_fee=fee),
            note=record.evidence, is_legacy=legacy,
        ))
        previous.extend((("candidates", candidate), ("entries", entry), ("market_outcomes", outcome),
                         ("bankroll_events", debit)))
        claims.append(f"settled:{entry_id}:{record.classification}")
    inventory = _inventory("synthetic-previous-trades", _dt(-1), *previous)
    return replace(
        evidence, candidates=tuple(candidates), scan_runs=(*evidence.scan_runs, historical),
        entries=tuple(entries), settlements=tuple(settlements),
        bankroll_events=(*openings, *cash_legs), market_outcomes=tuple(outcomes),
        visibility_snapshots=(*evidence.visibility_snapshots, inventory),
        settlement_writer_revisions=(ef.SettlementWriterRevision(COMMIT, "synthetic #104 writer", True),),
        run_events=(*evidence.run_events, _settle_success(*claims)),
    )


def _terminal_cash(evidence):
    return tuple(row for row in evidence.bankroll_events if row.event_key.startswith("settle:"))


def _assert_reason(evidence, verdict, *parts):
    result = ev.criterion_f(evidence=evidence)
    assert result.verdict == verdict, result.reasons
    assert any(all(part in reason for part in parts) for reason in result.reasons), result.reasons


def _without_command(evidence, command):
    return tuple(row for row in evidence.run_events if row.command != command)


def _interrupted_attempt(command, *, failed):
    events = (RunEventRecord(f"{command}-synthetic", 1, "STARTED", _dt(5, 9), command=command),)
    if failed:
        events += (RunEventRecord(
            f"{command}-synthetic", 1, "FAILED", _dt(5, 10), command=command,
            error_code="synthetic_write_conflict",
        ),)
    return events


def test_exact_capture_proof_ensures_old_mark_and_close_rows_once():
    evidence, _quote = _capture_fixture(old_observation=True, explicit_digest=True)
    duplicate = replace(evidence.run_events[-1], attempt=2, occurred_at=_dt(5, 12))
    pre_anchor = _inventory("synthetic-old-pack", _dt(-1), ("snapshots", evidence.snapshots[0]))
    evidence = replace(evidence, run_events=(*evidence.run_events, duplicate),
                       visibility_snapshots=(pre_anchor,))
    _assert_reason(evidence, ev.VERDICT_PASS, "snapshots", "existed pre-anchor", "reused")
    marks = _family(evidence, "candidate_marks")
    assert marks.in_window == marks.matched == 1
    assert all(term in marks.disclosed[0] for term in ("1, 'SUCCEEDED'", "2, 'SUCCEEDED'", "item 0"))
    assert _family(evidence, "candidate_price_evidence").matched == 1
    assert _family(evidence, "marks").matched == 0
    assert _family(evidence, "snapshots").matched == 2


@pytest.mark.parametrize("changed", ["no-price", "downgrade", "other-target", "source", "price", "key"])
def test_capture_summary_requires_actual_matching_item(changed):
    evidence, quote = _capture_fixture()
    assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
    mark, = evidence.candidate_marks
    close, = evidence.price_evidence
    if changed == "no-price":
        quote = {name: value for name, value in quote.items() if name not in ("bid", "ask", "mid", "last")}
    elif changed == "downgrade":
        quote["sampling_kind"] = "PERIODIC"
    elif changed == "other-target":
        quote["candidate_key"] = "capture-alternate"
    elif changed == "source":
        mark = replace(mark, source_ref="other-source")
    elif changed == "price":
        mark = replace(mark, bid=Decimal("0.41"))
    elif changed == "key":
        mark = replace(mark, candidate_mark_key="cm|wrong")
    pack = evidence.snapshots[0]
    if changed in ("no-price", "downgrade", "other-target"):
        pack = _snapshot(json.dumps([quote]).encode(), kind="capture_mark_pack")
    quoted_target = evidence.candidates[-1] if changed == "other-target" else evidence.candidates[-2]
    receipt = _capture_success((pack,), targets=(quoted_target,))
    evidence = _known_window(replace(
        evidence, run_events=(*_without_command(evidence, "capture-marks"), receipt),
        candidate_marks=(mark,), price_evidence=(close,), snapshots=(pack,),
    ), ("candidate_marks", mark), ("candidate_price_evidence", close))
    if changed in ("other-target", "key"):
        evidence = replace(evidence, visibility_snapshots=(*evidence.visibility_snapshots,
            _complete_view("capture-marks-after", _dt(5, 12), "candidate_marks", (mark,)),
            _complete_view("capture-closes-after", _dt(5, 12), "candidate_price_evidence", (close,)),
        ))
    _assert_reason(evidence, ev.VERDICT_FAIL, "candidate_marks row 501", "capture")
    assert _family(evidence, "candidate_marks").matched == 0
    if changed in ("no-price", "downgrade", "other-target"):
        _assert_reason(evidence, ev.VERDICT_FAIL, "candidate_price_evidence row 502", "capture")


@pytest.mark.parametrize("failed", [True, False])
def test_capture_partial_commit_requires_exact_successful_retry(failed):
    positive, quote = _capture_fixture()
    pack, = positive.snapshots
    mark, = positive.candidate_marks
    close, = positive.price_evidence
    history = _without_command(positive, "capture-marks") + _interrupted_attempt("capture-marks", failed=failed)
    partial = _known_window(replace(positive, run_events=history),
                            ("candidate_marks", mark), ("candidate_price_evidence", close), ("snapshots", pack))
    _assert_reason(partial, ev.VERDICT_NOT_ASSESSABLE, "candidate_marks row 501", "per-item evidence unavailable")
    _assert_reason(partial, ev.VERDICT_NOT_ASSESSABLE, "snapshots", "completed reference")
    retry = replace(partial, run_events=(*history, replace(positive.run_events[-1], attempt=2, occurred_at=_dt(5, 12))))
    assert ev.criterion_f(retry).verdict == ev.VERDICT_PASS
    wrong_retry = replace(partial, run_events=(*history, _capture_success((), attempt=2, hour=12)))
    _assert_reason(wrong_retry, ev.VERDICT_NOT_ASSESSABLE, "candidate_marks row 501", "per-item")
    other_quote = {**quote, "candidate_key": "capture-alternate"}
    other = _snapshot(json.dumps([other_quote]).encode())
    other_target = positive.candidates[-1]
    other_mark, other_close = _quote_rows(other_quote, other_target, other)
    other_retry = replace(
        partial, snapshots=(*partial.snapshots, other),
        candidate_marks=(mark, replace(other_mark, row_key=503)),
        price_evidence=(close, replace(other_close, row_key=504)),
        run_events=(*history, _capture_success((other,), targets=(other_target,), attempt=2, hour=12)),
    )
    _assert_reason(other_retry, ev.VERDICT_NOT_ASSESSABLE, "candidate_marks row 501", "per-item")
    bypass = _known_window(replace(positive, run_events=history),
                           ("candidate_marks", mark), unreceipted=(("candidate_marks", mark),))
    _assert_reason(bypass, ev.VERDICT_FAIL, "candidate_marks row 501", "unreceipted writer")


def test_capture_close_confirmation_checks_the_atomic_counterpart():
    evidence, _quote = _capture_fixture()
    assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
    missing = replace(evidence, price_evidence=(), visibility_snapshots=(
        VisibilitySnapshot("verified-close-absence", _dt(5, 12), ("candidate_price_evidence",), ()),
    ))
    _assert_reason(missing, ev.VERDICT_FAIL, "candidate_marks row 501", "atomic capture counterpart")
    assert _family(missing, "candidate_marks").matched == 0


def test_declared_periodic_only_capture_is_valid_without_a_close_counterpart():
    evidence, quote = _capture_fixture()
    target = next(row for row in evidence.candidates if row.candidate_id == 51)
    equal_time = replace(target, event_start_at=datetime.fromisoformat(quote["observed_at"]))
    candidates = tuple(equal_time if row == target else row for row in evidence.candidates)
    mark, close = _quote_rows(quote, equal_time, evidence.snapshots[0])
    assert close is None and mark.sampling_kind == "PERIODIC"
    receipt = _capture_success(evidence.snapshots, targets=(equal_time,))
    periodic = replace(evidence, candidates=candidates, candidate_marks=(mark,), price_evidence=(),
                       run_events=(*_without_command(evidence, "capture-marks"), receipt))
    assert ev.criterion_f(periodic).verdict == ev.VERDICT_PASS
    assert _family(periodic, "candidate_marks").matched == 1


def test_settle_prior_success_survives_empty_retry_and_uses_older_outcome():
    evidence = _settlement_fixture()
    retry = replace(evidence, run_events=(*evidence.run_events, _settle_success(attempt=2)))
    assert ev.criterion_f(retry).verdict == ev.VERDICT_PASS
    assert _family(retry, "settlements").matched == 1
    assert _family(retry, "bankroll_events").matched == 1
    assert _family(retry, "market_outcomes").pre_anchor == 1


@pytest.mark.parametrize("failed", [True, False])
def test_settle_partial_commit_cannot_be_backfilled_by_empty_or_other_entry_retry(failed):
    positive = _settlement_fixture(two_entries=True)
    first, second = positive.settlements
    first_cash, second_cash = _terminal_cash(positive)
    history = _without_command(positive, "settle") + _interrupted_attempt("settle", failed=failed)
    partial = _known_window(
        replace(positive, run_events=history),
        ("settlements", first), ("bankroll_events", first_cash),
        ("settlements", second), ("bankroll_events", second_cash),
    )
    empty = replace(partial, run_events=(*history, _settle_success(attempt=2)))
    _assert_reason(empty, ev.VERDICT_NOT_ASSESSABLE, "settlements row 300", "per-item evidence")
    _assert_reason(empty, ev.VERDICT_NOT_ASSESSABLE, "bankroll_events row 400", "per-item evidence")
    retry = replace(partial, run_events=(*history, _settle_success("settled:102:VOIDED_REFUND", attempt=2)))
    assert _family(retry, "settlements").matched == 1
    assert _family(retry, "bankroll_events").matched == 1
    _assert_reason(retry, ev.VERDICT_NOT_ASSESSABLE, "settlements row 300", "entry 101")
    if failed:
        _assert_reason(retry, ev.VERDICT_NOT_ASSESSABLE, "attempt 1", "synthetic_write_conflict")
    else:
        _assert_reason(retry, ev.VERDICT_NOT_ASSESSABLE, "attempt 1", "NONTERMINAL")


@pytest.mark.parametrize("changed", ["pending", "classification", "cash-key", "amount", "source"])
def test_settle_receipt_requires_exact_entry_result_and_paired_cash(changed):
    evidence = _settlement_fixture()
    assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
    settlement, = evidence.settlements
    leg, = _terminal_cash(evidence)
    receipt = _settle_success("settled:101:NATURAL")
    if changed == "pending":
        receipt = _settle_success("pending:101:no_captured_outcome")
    elif changed == "classification":
        receipt = _settle_success("settled:101:VOIDED_REFUND")
    elif changed == "cash-key":
        leg = replace(leg, event_key="settle:999")
    elif changed == "amount":
        leg = replace(leg, amount=Decimal("999"))
    else:
        settlement = replace(settlement, source_outcome_id=999)
    evidence = _known_window(replace(
        evidence, run_events=(*_without_command(evidence, "settle"), receipt),
        settlements=(settlement,),
        bankroll_events=(*(cash for cash in evidence.bankroll_events if cash.event_id != leg.event_id), leg),
    ), ("settlements", settlement), ("bankroll_events", leg))
    if changed == "cash-key":
        witnessed = tuple(ef.row_fingerprint("bankroll_events", cash) for cash in evidence.bankroll_events)
        evidence = replace(evidence, visibility_snapshots=(*evidence.visibility_snapshots,
            VisibilitySnapshot("terminal-cash-inventory", _dt(5, 12), ("bankroll_events",), witnessed),
        ))
    if changed == "source":
        evidence = replace(evidence, visibility_snapshots=(*evidence.visibility_snapshots,
            _inventory("outcome-lookup-after-settle", _dt(5, 12), absent=(("market_outcomes", 999),)),
        ))
    reason = {
        "pending": "pending", "classification": "classification conflicts",
        "cash-key": "cash leg", "amount": "cash leg", "source": "source_outcome_id",
    }[changed]
    _assert_reason(evidence, ev.VERDICT_FAIL, "settlements row 300", reason)
    assert _family(evidence, "settlements").matched == 0
    assert _family(evidence, "bankroll_events").matched == 0


def test_market_outcome_and_entry_mark_never_borrow_driver_receipts():
    capture_window, _quote = _capture_fixture()
    evidence = _settlement_fixture(capture_window)
    mark, = evidence.candidate_marks
    entry = EntryRecord(104, mark.candidate_id, _dt(0, 8))
    entry_mark = EntryChildRecord(
        entry.entry_id, mark.written_at, row_key=901, is_close=True, source_kind=mark.source_kind,
        source_ref=mark.source_ref, bid=mark.bid, ask=mark.ask, mid=mark.mid,
    )
    outcome, = evidence.market_outcomes
    outcome = replace(outcome, writer="settle")
    previous, = evidence.visibility_snapshots
    original = next(row for row in previous.rows if row.family == "market_outcomes")
    parents = replace(previous, rows=tuple(row for row in previous.rows if row != original))
    data = replace(evidence, entries=(*evidence.entries, entry), marks=(entry_mark,),
                   market_outcomes=(outcome,), visibility_snapshots=(parents,))
    data = _known_window(data, ("marks", entry_mark), ("market_outcomes", outcome))
    _assert_reason(data, ev.VERDICT_NOT_ASSESSABLE, "marks row 901", "no production writer receipt")
    _assert_reason(data, ev.VERDICT_NOT_ASSESSABLE, "market_outcomes row 201", "settle consumes")
    assert _family(data, "marks").matched == 0
    assert _family(data, "market_outcomes").matched == 0
    bypass = _known_window(replace(data, visibility_snapshots=(parents,)),
                           ("marks", entry_mark), ("market_outcomes", outcome),
                           unreceipted=(("market_outcomes", outcome),))
    _assert_reason(bypass, ev.VERDICT_FAIL, "market_outcomes row 201", "unmatched in-window")
    _assert_reason(bypass, ev.VERDICT_FAIL, "marks row 901", "no production writer receipt")


def test_scan_parent_history_requires_evidence_for_each_window_child():
    evidence = _green()
    old_scan = ScanRunRecord(902, _dt(-3), scan_key="synthetic-old")
    old_candidate = CandidateLinkRecord(903, old_scan.scan_id, _dt(-3, 8), COMMIT)
    old = _inventory("old-inventory", _dt(-1),
                     ("scan_runs", old_scan), ("candidates", old_candidate))
    evidence = replace(evidence, scan_runs=(*evidence.scan_runs, old_scan),
                       candidates=(*evidence.candidates, old_candidate), visibility_snapshots=(old,))
    assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
    entry = EntryRecord(904, old_candidate.candidate_id, _dt(3))
    orphan = _known_window(replace(evidence, entries=(entry,)), ("entries", entry))
    _assert_reason(orphan, ev.VERDICT_FAIL, "entries row 904", "NULL operation_key")
    linked = replace(entry, candidate_id=evidence.candidates[0].candidate_id, written_at=_dt(0, 6, 2),
                     stake=Decimal("25"), entry_fee=Decimal("0.10"))
    opening = BankrollEventRecord(905, "SCAN_ENTRY", linked.written_at, scan_id=1, entry_id=904,
                                  event_key="entry:904:cash", amount=Decimal("-25.10"))
    evidence = replace(evidence, bankroll_events=(opening,))
    assert ev.criterion_f(_known_window(replace(evidence, entries=(linked,)), ("entries", linked))).verdict == ev.VERDICT_PASS


@pytest.mark.parametrize("mutation", ["wrong-command", "scan-id", "scan-key", "post-window-receipt"])
def test_scan_receipts_cannot_be_substituted_or_backfilled(mutation):
    evidence = _green()
    scan = evidence.scan_runs[1]
    def mutate(row):
        if row.operation_key != scan.operation_key:
            return row
        if mutation == "wrong-command":
            return replace(row, command="settle")
        if row.phase == "SUCCEEDED":
            if mutation == "scan-id":
                return replace(row, scan_id=999)
            if mutation == "scan-key":
                return replace(row, output_evidence=("conflicting-key",))
        if mutation == "post-window-receipt":
            return replace(row, occurred_at=_dt(30, 1))
        return row
    bad = _known_window(
        replace(evidence, run_events=tuple(mutate(row) for row in evidence.run_events)),
        ("scan_runs", scan),
    )
    _assert_reason(bad, ev.VERDICT_FAIL, f"scan_runs row {scan.scan_id}", "receipt")


def test_known_orphan_with_no_receipts_fails_while_empty_window_is_unknown():
    empty = FEvidence(window_start=ANCHOR, schedule=_schedule("06:00"), as_of=_dt(30, 0),
                      pending_contracts=())
    _assert_reason(empty, ev.VERDICT_NOT_ASSESSABLE, "no run receipts")
    scan = ScanRunRecord(404, _dt(1), operation_key=None)
    bypass = _known_window(replace(empty, scan_runs=(scan,)), ("scan_runs", scan))
    _assert_reason(bypass, ev.VERDICT_FAIL, "scan_runs row 404", "NULL operation_key")


@pytest.mark.parametrize("reason", ["SCAN_ENTRY", "SETTLE_NATURAL"])
def test_legacy_flag_cannot_exempt_command_cash(reason):
    cash = BankrollEventRecord(918, reason, _dt(3, 9), is_legacy=True)
    evidence = _known_window(_green(bankroll_events=(cash,)), ("bankroll_events", cash))
    _assert_reason(evidence, ev.VERDICT_FAIL, "bankroll_events row 918", reason)


def test_reversal_chain_terminates_only_at_valid_source():
    base = BankrollEventRecord(701, "ADJUSTMENT", _dt(2, 7))
    valid = BankrollEventRecord(702, "REVERSAL", _dt(2, 8), reverses_event_id=701)
    evidence = _known_window(_green(bankroll_events=(base, valid)), ("bankroll_events", valid))
    _assert_reason(evidence, ev.VERDICT_FAIL, "ADJUSTMENT=1")
    _assert_reason(evidence, ev.VERDICT_FAIL, "REVERSAL chain terminates at 701", "not receipt-proven")
    invalid = BankrollEventRecord(703, "SCAN_ENTRY", _dt(2, 9))
    for other, expected in (
        ((replace(valid, reverses_event_id=702),), "chains to itself"),
        ((replace(valid, reverses_event_id=703), replace(valid, event_id=703, reverses_event_id=702)), "cycle"),
        ((replace(valid, reverses_event_id=703), invalid), "not receipt-proven"),
    ):
        broken = _known_window(
            _green(bankroll_events=(base, *other)), *(("bankroll_events", row) for row in other),
        )
        _assert_reason(broken, ev.VERDICT_FAIL, "bankroll_events row 702", expected)


def test_scan_circuit_verifies_decision_price_cash_and_shadow_relationships():
    evidence = _green()
    scan = evidence.scan_runs[0]
    candidate = CandidateLinkRecord(905, scan.scan_id, _dt(0, 7), COMMIT,
                                    candidate_key="synthetic-b1")
    entry = EntryRecord(906, candidate.candidate_id, candidate.written_at,
                        stake=Decimal("3.25"), entry_fee=Decimal("0.125"))
    decision = store.PriceEvidenceObservation(
        observed_at=candidate.written_at, market_ref_kind="market_slug",
        market_ref="synthetic-market", side="YES", source_kind="clob_book",
        source_ref="synthetic-source", ask=Decimal("0.5"),
    ).validate()
    stored = CandidateChildRecord(
        candidate.candidate_id, decision.observed_at, row_key=907,
        evidence_role="DECISION", evidence_key=decision.evidence_key_with(candidate.candidate_id),
        market_ref_kind=decision.market_ref_kind, market_ref=decision.market_ref,
        side=decision.side, source_kind=decision.source_kind, source_ref=decision.source_ref,
        ask=decision.ask,
    )
    cash = BankrollEventRecord(908, "SCAN_ENTRY", entry.written_at,
                              scan_id=scan.scan_id, entry_id=entry.entry_id,
                              event_key=f"entry:{entry.entry_id}:cash", amount=Decimal("-3.375"))
    shadow = CandidateChildRecord(candidate.candidate_id, candidate.written_at, row_key=909)
    linked = replace(
        evidence, candidates=(*evidence.candidates, candidate),
        entries=(entry,), price_evidence=(stored,), bankroll_events=(cash,),
        shadow_predictions=(shadow,), replay_agreements={**evidence.replay_agreements, "905": True},
    )
    assert ev.criterion_f(linked).verdict == ev.VERDICT_PASS
    for family in ("candidate_price_evidence", "entries", "bankroll_events", "shadow_predictions"):
        assert _family(linked, family).matched == 1
    wrong_cash = replace(cash, amount=Decimal("-3.25"), is_legacy=True)
    broken = replace(linked, bankroll_events=(wrong_cash,))
    _assert_reason(_known_window(broken, ("bankroll_events", wrong_cash)), ev.VERDICT_FAIL,
                   "bankroll_events row 908", "cash key, amount")
    wrong_price = replace(stored, evidence_key="supplied-but-wrong")
    _assert_reason(replace(linked, price_evidence=(wrong_price,)), ev.VERDICT_FAIL,
                   "candidate_price_evidence row 907", "evidence key")


def test_snapshot_citation_must_be_exact_and_old_business_date_is_unknown_alone():
    snapshot = replace(_snapshot(b"unbound", at=_dt(-4)), cited=True)
    evidence = _green(snapshots=(snapshot,))
    _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, snapshot.snapshot_id, "writing period unproven")
    known = _known_window(evidence, ("snapshots", snapshot))
    _assert_reason(known, ev.VERDICT_FAIL, snapshot.snapshot_id, "cited by neither")
    candidate, *others = evidence.candidates
    cited = replace(evidence, candidates=(replace(candidate, snapshot_refs=(snapshot.snapshot_id,)), *others))
    assert ev.criterion_f(cited).verdict == ev.VERDICT_PASS
    assert _family(cited, "snapshots").matched == 1


@pytest.mark.parametrize("late_by", [timedelta(0), timedelta(microseconds=1)])
def test_last_scan_terminal_at_or_after_end_cannot_cover_last_slot(late_by):
    evidence = _green()
    end = _dt(30, 0)
    last = evidence.run_events[-1]
    late = replace(evidence, as_of=end + timedelta(hours=1),
                   run_events=(*evidence.run_events[:-1], replace(last, occurred_at=end + late_by)))
    _assert_reason(late, ev.VERDICT_FAIL, "2026-10-30T06:00:00", "uncovered")
    assert ef.build_slot_timeline(late.run_events, ANCHOR, late.schedule, as_of=late.as_of).attempts_nonterminal == 1
    before = replace(late, run_events=(*evidence.run_events[:-1], replace(last, occurred_at=end - timedelta(microseconds=1))))
    assert ev.criterion_f(before).verdict == ev.VERDICT_PASS


def test_incomplete_today_only_reports_elapsed_slot_then_fails_at_next_midnight():
    evidence = _green(
        ("06:00", "09:00"), status_for=lambda day, index: "SUCCEEDED" if day < 10 else None,
        bankroll_checks=tuple(_checks(days=range(10))), as_of=_dt(10, 12),
    )
    _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, "10/30")
    _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, "current UTC day diagnostic", "2026-10-11T06:00")
    assert len(ef.build_identity_days(evidence.bankroll_checks, ANCHOR, as_of=evidence.as_of)) == 10
    tomorrow = replace(evidence, as_of=_dt(11, 0))
    _assert_reason(tomorrow, ev.VERDICT_FAIL, "expected slot 2026-10-11T06:00", "uncovered")
    _assert_reason(tomorrow, ev.VERDICT_FAIL, "2026-10-11", "identity missing")
    failed_day = replace(evidence, bankroll_checks=tuple(_checks(days=range(9))))
    _assert_reason(failed_day, ev.VERDICT_FAIL, "2026-10-10", "identity missing")


def test_phase_cropping_preserves_earliest_stamp_and_only_visible_attempt_phases():
    start = RunEventRecord("pre", 1, "STARTED", _dt(-1, 23))
    late = RunEventRecord("pre", 1, "SUCCEEDED", _dt(0, 6, 10))
    future_retry = RunEventRecord("pre", 2, "FAILED", _dt(1, 9), error_code="later")
    canvas = ef.build_slot_timeline((late, start, future_retry), ANCHOR, _schedule("06:00"),
                                    as_of=_dt(0, 10))
    assert canvas.out_of_window_groups == 1
    assert not canvas.slots[0].covered
    assert canvas.retry_attempts == 0
    assert canvas.error_codes == ()
    assert canvas.uncovered == ()
    _assert_reason(replace(_green(), as_of=None), ev.VERDICT_NOT_ASSESSABLE, "maturity is unknown")
    _assert_reason(replace(_green(), as_of=_dt(0, 9)), ev.VERDICT_NOT_ASSESSABLE, "0/30")


def test_explicit_inventory_excludes_rows_invisible_at_report_and_after_window():
    evidence = _green()
    snapshot = _snapshot(b"late registration", at=_dt(-3))
    row = ("snapshots", snapshot)
    absent = (("snapshots", snapshot.snapshot_id),)
    late = replace(evidence, snapshots=(snapshot,), visibility_snapshots=(
        _inventory("synthetic-report-cutoff", _dt(30, 0), absent=absent),
        _inventory("synthetic-late-registration", _dt(30, 1), row),
    ))
    assert ev.criterion_f(late).verdict == ev.VERDICT_PASS
    assert _family(late, "snapshots").in_window == 0
    after = replace(late, as_of=_dt(30, 2))
    assert ev.criterion_f(after).verdict == ev.VERDICT_PASS
    assert _family(after, "snapshots").post_window == 1


def test_snapshot_absences_exclude_backdated_check_candidate_and_receipt():
    evidence = _green()
    visible_candidates, after_candidate = evidence.candidates[:-1], evidence.candidates[-1]
    after_check = evidence.bankroll_checks[-1]
    late_success = replace(evidence.run_events[-1], run_event_id=173)
    receipts = (*evidence.run_events[:-1], late_success)
    cutoff = _inventory(
        "synthetic-repeatable-read", evidence.as_of,
        absent=(
            ("command_run_events", late_success.run_event_id),
            ("bankroll_checks", after_check.check_id),
            ("candidates", after_candidate.candidate_id),
        ),
    )
    supplied = replace(evidence, run_events=receipts, visibility_snapshots=(cutoff,))
    _assert_reason(supplied, ev.VERDICT_FAIL, "2026-10-30T06:00:00", "uncovered")
    _assert_reason(supplied, ev.VERDICT_FAIL, "2026-10-30", "identity missing")
    assert draw_replay_sample(supplied).eligible_ids == tuple(row.candidate_id for row in visible_candidates)
    assert ev.criterion_f(replace(supplied, visibility_snapshots=())).verdict == ev.VERDICT_PASS


def test_scan_with_exact_start_before_terminal_can_be_linked_without_covering_slot():
    evidence = _green()
    scan = evidence.scan_runs[2]
    events = tuple(
        replace(event, phase="FAILED", output_evidence=(), scan_id=None, error_code="shadow-error")
        if event.operation_key == scan.operation_key and event.phase == "SUCCEEDED" else event
        for event in evidence.run_events
    )
    partial = replace(evidence, run_events=events)
    assert _family(partial, "scan_runs").matched == 30
    _assert_reason(partial, ev.VERDICT_FAIL, "2026-10-03T06:00:00", "failed-only")
    assert _family(partial, "scan_runs").unmatched == ()


def test_replay_only_draws_unique_rows_with_consistent_scan_commit():
    evidence = _green()
    candidate = evidence.candidates[0]
    repeated = replace(evidence, candidates=(candidate,) * ef.F_REPLAY_SAMPLE_N,
                       replay_agreements={candidate.candidate_id: True})
    assert draw_replay_sample(repeated).eligible_ids == (candidate.candidate_id,)
    _assert_reason(repeated, ev.VERDICT_NOT_ASSESSABLE, "1 eligible candidate", "registered N 20")
    for conflicting in (
        replace(candidate, scan_id=candidate.scan_id + 1),
        replace(candidate, candidate_key="content-from-another-row"),
        replace(candidate, content_sha256="0" * 64),
    ):
        for pair in ((candidate, conflicting), (conflicting, candidate)):
            with pytest.raises(ValueError, match="duplicate or conflicting candidate_id"):
                draw_replay_sample(replace(evidence, candidates=pair))
            with pytest.raises(ValueError, match="candidate_id"):
                ev.criterion_f(replace(evidence, candidates=pair))
    latest = evidence.scan_runs[-1]
    untrusted = replace(evidence, scan_runs=(*evidence.scan_runs[:-1], replace(latest, code_commit=None)))
    sample = draw_replay_sample(untrusted)
    assert sample.trusted_commit is None
    assert str(latest.scan_id) in sample.shortfalls[0]
    _assert_reason(untrusted, ev.VERDICT_NOT_ASSESSABLE, f"latest in-window scan {latest.scan_id}", "code_commit")
    assert draw_replay_sample(replace(evidence, candidates=evidence.candidates[::-1])) == draw_replay_sample(evidence)


@pytest.mark.parametrize("wrong", [None, 0, 1, "true"])
def test_replay_invalid_results_are_rejected(wrong):
    evidence = _green()
    selected = draw_replay_sample(evidence).selected_ids
    with pytest.raises(ValueError, match="must be bool"):
        ev.criterion_f(replace(evidence, replay_agreements=dict.fromkeys(selected, wrong)))


def test_replay_missing_and_false_are_each_reported_for_selected_ids():
    evidence = _green()
    selected = draw_replay_sample(evidence).selected_ids
    missing, differs, *_ = selected
    extras = dict(evidence.replay_agreements)
    del extras[missing]
    extras["synthetic-unselected"] = True
    _assert_reason(replace(evidence, replay_agreements=extras), ev.VERDICT_NOT_ASSESSABLE,
                   "1 of 20", missing)
    extras[differs] = False
    mismatch = replace(evidence, replay_agreements=extras)
    _assert_reason(mismatch, ev.VERDICT_FAIL, "replay mismatch", differs)
    _assert_reason(mismatch, ev.VERDICT_FAIL, "1 of 20", missing)


@pytest.mark.parametrize("field,bad", [
    ("identity_difference", 0.0), ("identity_difference", False),
    ("identity_difference", "0"), ("identity_difference", Decimal("NaN")),
    ("identity_difference", Decimal("sNaN")), ("identity_difference", Decimal("-Infinity")),
    ("tolerance", Decimal("Infinity")), ("tolerance", Decimal("-0.1")),
    ("tolerance", 0.0), ("tolerance", True), ("passed", 1),
])
def test_daily_cash_check_rejects_non_decimal_or_non_bool_fields(field, bad):
    with pytest.raises(ValueError, match=field):
        replace(_checks()[0], **{field: bad})


def test_daily_cash_check_keeps_latest_receipt_exact_and_order_independent():
    evidence = _green()
    moment = _dt(3, 22)
    negative = Decimal("-0.00000000000000000000000000011")
    threshold = Decimal("0.00000000000000000000000000029")
    first = BankrollCheckRecord(moment, False, Decimal("0"), Decimal("1.000"), check_id=801)
    with_offset = moment.astimezone(timezone(timedelta(hours=-4)))
    second = BankrollCheckRecord(with_offset, False, negative, threshold, check_id=802)
    kept = tuple(check for check in evidence.bankroll_checks if check.check_id != 4)
    for moment_checks in ((first, second), (second, first)):
        bad = replace(evidence, bankroll_checks=(*kept, *moment_checks))
        _assert_reason(bad, ev.VERDICT_FAIL, "cash identity failed", "check 802", str(negative), str(threshold))
        day = ef.build_identity_days(bad.bankroll_checks, ANCHOR, as_of=bad.as_of)[3]
        assert (day.passed_count, day.failed_count, day.check_id) == (0, 2, 802)
        assert (day.reported_difference, day.reported_tolerance) == (negative, threshold)
    passed = BankrollCheckRecord(moment, True, Decimal(42), Decimal(0), check_id=803)
    mixed = replace(evidence, bankroll_checks=(*kept, first, second, passed))
    _assert_reason(mixed, ev.VERDICT_PASS, "cash identity mixed", "1 passed", "2 receipt(s)", "check 802")
    invalid = replace(mixed, bankroll_checks=(*mixed.bankroll_checks, replace(second, passed=True)))
    with pytest.raises(ValueError, match="conflicting bankroll check_id"):
        ev.criterion_f(invalid)


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


@pytest.mark.parametrize("selector", ["scan_id", "scan_operation_key"])
def test_capture_success_cannot_read_a_later_scan(selector):
    positive, quote = _capture_fixture()
    assert ev.criterion_f(positive).verdict == ev.VERDICT_PASS
    scan = positive.scan_runs[7]
    target = next(row for row in positive.candidates if row.candidate_id == 51)
    target = replace(target, scan_id=scan.scan_id)
    quote = {key: value for key, value in quote.items() if key not in ("scan_id", "scan_operation_key")}
    quote[selector] = getattr(scan, "operation_key" if selector == "scan_operation_key" else "scan_id")
    pack = _snapshot(json.dumps([quote]).encode(), kind="capture_mark_pack")
    mark, close = _quote_rows(quote, target, pack)
    late_parent = replace(
        positive,
        candidates=tuple(target if row.candidate_id == 51 else row for row in positive.candidates),
        snapshots=(pack,), candidate_marks=(mark,), price_evidence=(close,),
        run_events=(*_without_command(positive, "capture-marks"), _capture_success((pack,), targets=(target,))),
    )
    _assert_reason(late_parent, ev.VERDICT_FAIL, "capture-marks-synthetic", "parent scan_runs row 8",
                   "2026-10-08T06:00:00")
    report = ef.reconcile_universe(late_parent)
    assert report.writer_failures
    assert _family(late_parent, "candidate_marks").matched == 0
    assert _family(late_parent, "candidate_price_evidence").matched == 0


@pytest.mark.parametrize("family", [
    "scan_runs", "candidates", "snapshots", "candidate_marks", "candidate_price_evidence",
])
@pytest.mark.parametrize("complete", [False, True])
def test_capture_checks_each_rows_visibility_at_its_own_receipt(family, complete):
    positive, _quote = _capture_fixture()
    assert ev.criterion_f(positive).verdict == ev.VERDICT_PASS
    target = {
        "scan_runs": positive.scan_runs[0], "candidates": positive.candidates[-2],
        "snapshots": positive.snapshots[0], "candidate_marks": positive.candidate_marks[0],
        "candidate_price_evidence": positive.price_evidence[0],
    }[family]
    ref = ef.row_fingerprint(family, target)
    if complete:
        # A complete inventory explicitly retains the other known rows of
        # the family, so the missing key is the only changed fact.
        complete_rows = {"scan_runs": positive.scan_runs, "candidates": positive.candidates}
        others = tuple(ef.row_fingerprint(family, row)
                       for row in complete_rows.get(family, ()) if row != target
                       and (family != "scan_runs" or row.started_at < _dt(5, 11)))
        absence = VisibilitySnapshot("complete-after-capture", _dt(5, 11), (family,), others)
    else:
        absence = _inventory("absent-after-capture", _dt(5, 11), absent=((family, ref.row_key),))
    appeared = _inventory("present-after-capture", _dt(5, 12), (family, target))
    evidence = replace(positive, visibility_snapshots=(absence, appeared))
    _assert_reason(evidence, ev.VERDICT_FAIL, "capture-marks-synthetic", family, "still absent")
    assert _family(evidence, "candidate_marks").matched == 0
    assert _family(evidence, "candidate_price_evidence").matched == 0


def test_capture_invalidates_all_items_when_an_unpriced_target_is_too_late():
    positive, quote = _capture_fixture()
    alternate = positive.candidates[-1]
    late = positive.scan_runs[7]
    alternate = replace(alternate, scan_id=late.scan_id)
    second = {name: value for name, value in quote.items()
              if name not in ("bid", "ask", "mid", "last", "scan_operation_key")}
    second.update(scan_id=late.scan_id, candidate_key=alternate.candidate_key)
    pack = _snapshot(json.dumps([quote, second]).encode(), kind="capture_mark_pack")
    target = positive.candidates[-2]
    mark, close = _quote_rows(quote, target, pack)
    changed = replace(
        positive, candidates=(*positive.candidates[:-1], alternate),
        snapshots=(pack,), candidate_marks=(mark,), price_evidence=(close,),
        run_events=(*_without_command(positive, "capture-marks"), _capture_success((pack,), targets=(target, alternate))),
    )
    _assert_reason(changed, ev.VERDICT_FAIL, "capture-marks-synthetic", "item 1", "parent scan_runs row 8")
    assert _family(changed, "candidate_marks").matched == 0

    # The old claim still contradicts the inventory. A later valid success
    # may independently prove the unchanged mark and CLOSE rows.
    initial, _ = _capture_fixture()
    absent = _inventory("absent-before-retry", _dt(5, 11), absent=(("candidate_marks", 501),))
    visible = _inventory("present-before-retry", _dt(5, 12), ("candidate_marks", initial.candidate_marks[0]))
    retry = replace(initial.run_events[-1], attempt=2, occurred_at=_dt(5, 13))
    recovered = replace(initial, visibility_snapshots=(absent, visible),
                        run_events=(*initial.run_events, retry))
    _assert_reason(recovered, ev.VERDICT_FAIL, "capture-marks-synthetic", "candidate_marks row 501", "still absent")
    assert _family(recovered, "candidate_marks").matched == 1
    assert _family(recovered, "candidate_price_evidence").matched == 1
    assert "2, 'SUCCEEDED'" in _family(recovered, "candidate_marks").disclosed[0]


def test_scan_atomic_children_and_candidate_snapshot_are_available_at_success():
    base = _green()
    candidate = base.candidates[0]
    raw = _snapshot(b"synthetic source snapshot", at=_dt(-3))
    for family, child, evidence in (
        ("candidates", candidate, base),
        ("snapshots", raw, replace(base, snapshots=(raw,), candidates=(
            replace(candidate, snapshot_refs=(raw.snapshot_id,)), *base.candidates[1:],
        ))),
    ):
        assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
        reference = ef.row_fingerprint(family, child)
        changed = replace(evidence, visibility_snapshots=(
            _inventory(f"{family}-after-b1", _dt(0, 7), absent=((family, reference.row_key),)),
            _inventory(f"{family}-later", _dt(1, 8), (family, child)),
        ))
        _assert_reason(changed, ev.VERDICT_FAIL, "op-00-0-06:00", family, "still absent")


def _settle_read_view(evidence, *, stage, competitor):
    settlement = evidence.settlements[0]
    cash = _terminal_cash(evidence)[0]
    on_entry = ("bankroll_events", competitor)
    foreign = (("bankroll_events", competitor.event_id),)
    pair_keys = (("settlements", settlement.row_key), ("bankroll_events", cash.event_id))
    p_rows = (on_entry,) if stage == "planning" else ()
    p_absent = (*pair_keys, *(() if p_rows else foreign))
    p_view = _inventory("planning-p", _dt(5, 9, 15), *p_rows, absent=p_absent)
    v_view = _inventory("cash-v101", _dt(5, 10, 15), on_entry) if stage == "recheck" else None
    snapshots = [*evidence.visibility_snapshots,
                 _inventory("foreign-baseline", _dt(0, 0), absent=foreign), p_view]
    if v_view:
        snapshots.append(v_view)
    if stage in ("early-after", "late-after"):
        pair_hour = 10 if stage == "early-after" else 11
        snapshots.append(_inventory(
            "pair-visible-before-foreign", _dt(5, pair_hour, 30),
            ("settlements", settlement), ("bankroll_events", cash), absent=foreign,
        ))
    first_seen = _dt(5, 10, 30) if stage == "straddle" else _dt(5, 12)
    snapshots.append(_inventory("foreign-observation", first_seen, on_entry))
    return replace(
        evidence,
        run_events=(*evidence.run_events, RunEventRecord(
            "settle-synthetic", 1, "STARTED", _dt(5, 9), command="settle", code_commit=COMMIT,
        )),
        settlement_reads=(ef.SettlementReadEvidence(
            "settle-synthetic", 1, 101, "synthetic read order", p_view.snapshot_id,
            v_view.snapshot_id if v_view else None,
        ),),
        bankroll_events=(*evidence.bankroll_events, competitor),
        visibility_snapshots=tuple(snapshots),
    )


@pytest.mark.parametrize("foreign_reason", ["SCAN_ENTRY", "REVERSAL"])
@pytest.mark.parametrize("stage", ["planning", "recheck", "straddle", "early-after", "late-after"])
def test_b3_opening_cash_is_verified_at_planning_and_each_entry_recheck(foreign_reason, stage):
    green = _settlement_fixture(two_entries=True)
    assert ev.criterion_f(green).verdict == ev.VERDICT_PASS
    opening = green.bankroll_events[0]
    if foreign_reason == "SCAN_ENTRY":
        competitor = replace(opening, event_id=900, event_key="other-opening-key")
    else:
        competitor = BankrollEventRecord(
            900, "REVERSAL", opening.occurred_at, event_key="foreign-reversal",
            amount=opening.amount.copy_negate(), reverses_event_id=opening.event_id,
        )
    evidence = _settle_read_view(green, stage=stage, competitor=competitor)
    report = ef.reconcile_universe(evidence)
    settlements = next(family for family in report.families if family.family == "settlements")
    cash = next(family for family in report.families if family.family == "bankroll_events")
    assert any("row 301" in detail and "settled:102:VOIDED_REFUND" in detail
               for detail in settlements.disclosed)
    assert any("row 401" in detail and "settled:102:VOIDED_REFUND" in detail for detail in cash.disclosed)
    if stage in ("planning", "recheck"):
        phase = "planning P" if stage == "planning" else "cash recheck V_101"
        _assert_reason(evidence, ev.VERDICT_FAIL, "settle-synthetic", "entry 101", "event 900", phase)
        assert settlements.matched == 1
        assert any("row 400" in detail for detail in cash.unmatched)
    elif stage == "straddle":
        assert settlements.matched == 1
        assert any("entry 101" in detail and "event 900" in detail and "V_101" in detail and "terminal T" in detail
                   for detail in report.shortfalls)
        assert not any("row 400" in detail for detail in cash.disclosed)
        # The separate foreign row is proven in-window and orphaned. The
        # unsettled timing of the original pair is reported alongside it.
        _assert_reason(evidence, ev.VERDICT_FAIL, "event 900", "unresolved planning P", "(R3)")
    else:
        assert settlements.matched == 2
        assert any("row 400" in detail and "settled:101:NATURAL" in detail for detail in cash.disclosed)
        _assert_reason(evidence, ev.VERDICT_FAIL, "bankroll_events row 900")


@pytest.mark.parametrize("stage", ["before-p", "after-p", "straddles-p", "selected-after-p"])
def test_b3_outcome_selection_uses_planning_snapshot(stage):
    evidence = _settlement_fixture()
    selected = evidence.market_outcomes[0]
    competitor = replace(
        selected, outcome_id=299, outcome="NO", observed_at=_dt(-2, 12),
        confirmed_at=_dt(-2, 12), writer=None,
    )
    start = RunEventRecord("settle-synthetic", 1, "STARTED", _dt(5, 9), command="settle",
                           code_commit=COMMIT)
    ref = ("market_outcomes", competitor.outcome_id)
    p_rows = (("market_outcomes", competitor),) if stage == "before-p" else ()
    p_absent = (ref,) if stage == "after-p" else ()
    previous, = evidence.visibility_snapshots
    if stage == "selected-after-p":
        p_absent = (("market_outcomes", selected.outcome_id), ref)
        prior_rows = tuple(row for row in previous.rows
                           if row.family != "market_outcomes")
        previous = replace(previous, rows=prior_rows)
    planning = _inventory("outcome-p", _dt(5, 9, 15), *p_rows, absent=p_absent)
    present = _inventory("outcome-later", _dt(5, 10), ("market_outcomes", competitor),
                         ("market_outcomes", selected))
    data = replace(
        evidence, market_outcomes=(selected, competitor), run_events=(*evidence.run_events, start),
        visibility_snapshots=(previous, planning, present),
        settlement_reads=(ef.SettlementReadEvidence(
            "settle-synthetic", 1, 101, "synthetic planning reader", planning.snapshot_id,
        ),),
    )
    outcomes = _family(data, "market_outcomes")
    assert outcomes.matched == 0
    if stage == "after-p":
        assert _family(data, "settlements").matched == 1
        assert _family(data, "bankroll_events").matched == 1
    else:
        assert _family(data, "settlements").matched == 0
        assert _family(data, "bankroll_events").matched == 0
        if stage == "straddles-p":
            _assert_reason(data, ev.VERDICT_NOT_ASSESSABLE, "planning P", "299", "(R3)")
        elif stage == "selected-after-p":
            _assert_reason(data, ev.VERDICT_FAIL, "market_outcomes row 201", "absent at planning P")
        else:
            _assert_reason(data, ev.VERDICT_FAIL, "source_outcome_id 201", "writer selects 299")


@pytest.mark.parametrize("token,legacy,fee", [
    ("YES", False, Decimal("0.10")),
    ("NO", False, Decimal("0")),
    ("VOIDED", True, Decimal("0.12345678901234567890123456789012345")),
])
def test_b3_exact_automatic_amounts_come_from_booked_fees(token, legacy, fee):
    from decimal import localcontext

    with localcontext() as ctx:
        ctx.prec = 6
        evidence = _settlement_fixture(outcome_token=token, fee=fee, legacy=legacy)
        settlement, = evidence.settlements
        opening, cash = evidence.bankroll_events
        expected = {
            "YES": (Decimal("62.5"), Decimal("37.40")),
            "NO": (Decimal("0"), Decimal("-25")),
            "VOIDED": (Decimal("25.12345678901234567890123456789012345"), Decimal("0")),
        }[token]
        assert (cash.amount, settlement.pnl_net) == expected
        assert opening.event_key == "entry:101:cash" and cash.event_key == "settle:101"
        assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
        assert _family(evidence, "settlements").matched == _family(evidence, "bankroll_events").matched == 1
        if token == "VOIDED":
            wrong = replace(evidence, bankroll_events=(opening, replace(cash, amount=Decimal("25"))))
            _assert_reason(wrong, ev.VERDICT_FAIL, "entry 101", "cash leg", "amount")
            assert _family(wrong, "settlements").matched == _family(wrong, "bankroll_events").matched == 0
            prior = replace(wrong, settlement_writer_revisions=(
                ef.SettlementWriterRevision(COMMIT, "synthetic earlier deployment", False),
            ), settlements=(replace(settlement, is_legacy=False),),
                bankroll_events=(opening, replace(cash, amount=Decimal("25"), is_legacy=False)))
            assert ev.criterion_f(prior).verdict == ev.VERDICT_PASS
            unverified = replace(prior, settlement_writer_revisions=())
            _assert_reason(unverified, ev.VERDICT_NOT_ASSESSABLE, "unverified B3 writer revision")


def test_b3_consumed_outcome_has_independent_history_or_bypass_evidence():
    green = _settlement_fixture(first_confirmed=False)
    assert ev.criterion_f(green).verdict == ev.VERDICT_PASS
    outcome, = green.market_outcomes
    assert outcome.confirmed_at is None and _family(green, "market_outcomes").pre_anchor == 1
    original, = green.visibility_snapshots
    parents = replace(original, rows=tuple(row for row in original.rows if row.family != "market_outcomes"))
    unknown = replace(green, visibility_snapshots=(parents,))
    _assert_reason(unknown, ev.VERDICT_NOT_ASSESSABLE, "market_outcomes row 201", "settle consumes outcomes")
    assert _family(unknown, "settlements").matched == _family(unknown, "bankroll_events").matched == 1
    bypassed = _known_window(
        unknown, ("market_outcomes", outcome), unreceipted=(("market_outcomes", outcome),),
        unreceipted_source="synthetic direct writer",
    )
    _assert_reason(bypassed, ev.VERDICT_FAIL, "market_outcomes row 201", "synthetic direct writer")
    assert _family(bypassed, "market_outcomes").matched == 0
    assert _family(bypassed, "settlements").matched == _family(bypassed, "bankroll_events").matched == 1


@pytest.mark.parametrize("family,row,changes,conflict,digest", [
    ("candidates", CandidateLinkRecord(11, 7, _dt(0, 0), candidate_key="candidate-A"),
     {"side": "YES", "condition_id": "condition-A", "market_id": None}, {"candidate_key": "changed"},
     "ef50025d0b82148f669b2a6b37f07eb585353ef9c7b1f270c2638223f74499d1"),
    ("entries", EntryRecord(21, 11, _dt(0, 0), stake=Decimal("25"), entry_fee=Decimal("0.10")),
     {"price": Decimal("0.40"), "fee_source": "estimated", "is_legacy": True},
     {"entry_fee": Decimal("0.11")},
     "009e132b4534207ef8084d9fa6aae4dcc2370c8571ec9b23c674815e741f8484"),
    ("settlements", EntryChildRecord(21, _dt(0, 0), row_key=31, classification="NATURAL",
                                     source_outcome_id=41, proceeds=Decimal("62.5"), outcome_label="YES"),
     {"win_loss": "WIN", "pnl_gross": Decimal("37.5"), "exit_mark_id": None}, {"source_outcome_id": 42},
     "98d8588b9fbdec056b42f9691181e965c7a7497fa092ba3b9d46bb5ef122ba40"),
    ("market_outcomes", MarketOutcomeRecord(
        None, _dt(0, 0), outcome_id=41, outcome="YES", confirmed_at=_dt(0, 0)),
     {"condition_id": "condition-A", "source_kind": "public"}, {"confirmed_at": _dt(0, 1)},
     "5fd8ffcce03d33126f594763e07d7e8f600dc83e0cc1f50fc856edf0d1d981df"),
    ("bankroll_events", BankrollEventRecord(51, "SCAN_ENTRY", _dt(0, 0), scan_id=7, entry_id=21,
                                             event_key="entry:21:cash", amount=Decimal("-25.10")),
     {}, {"amount": Decimal("-25.11")},
     "7fc6866ebff8184f6ffc0d06f6b8ab5c572f2ac4a8de0ee58f168633faa0ce30"),
    ("marks", EntryChildRecord(21, _dt(0, 0), row_key=31, classification="NATURAL",
                               source_outcome_id=41, proceeds=Decimal("62.5"), outcome_label="YES"),
     {"win_loss": "WIN", "pnl_gross": Decimal("37.5"), "exit_mark_id": None}, {"source_outcome_id": 42},
     "98d8588b9fbdec056b42f9691181e965c7a7497fa092ba3b9d46bb5ef122ba40"),
])
def test_prep_inventory_fingerprints_remain_stable_and_check_content(family, row, changes, conflict, digest):
    base = _green()
    fingerprint = ef.RowFingerprint(family, ef.row_fingerprint(family, row).row_key, digest)
    history = VisibilitySnapshot("original-cutover", _dt(-1), (), (fingerprint,))
    for compatible in (row, replace(row), replace(row, **changes)):
        assert ef.row_fingerprint(family, compatible) == fingerprint
        data = replace(base, **{family: (*getattr(base, family), compatible)},
                       visibility_snapshots=(history,))
        assert ev.criterion_f(data).verdict == ev.VERDICT_PASS
        assert _family(data, family).pre_anchor == 1
        absent = VisibilitySnapshot("cutover-absent", _dt(0, 0), (), (),
                                    checked_absences=((family, fingerprint.row_key),))
        bypass = replace(history, snapshot_id="original-source", observed_at=_dt(1, 8),
                         unreceipted_writes=(fingerprint,), unreceipted_source="verified synthetic import")
        written = replace(data, visibility_snapshots=(absent, bypass))
        _assert_reason(written, ev.VERDICT_FAIL, family, "verified synthetic import")
    broken = replace(row, **conflict)
    with pytest.raises(ValueError, match="conflicting inventory content"):
        ev.criterion_f(replace(base, **{family: (*getattr(base, family), broken)},
                               visibility_snapshots=(history,)))


def test_capture_target_can_exist_before_its_scan_command_finishes():
    evidence, _quote = _capture_fixture()
    first = evidence.scan_runs[0]
    shifted = tuple(
        replace(event, occurred_at=_dt(5, 12))
        if event.command == "run-scan" and event.operation_key == first.operation_key
        and event.phase == "SUCCEEDED" else event for event in evidence.run_events
    )
    target = evidence.candidates[-2]
    before_capture = _inventory(
        "parent-commit-before-capture", _dt(5, 9), ("scan_runs", first), ("candidates", target),
    )
    overlapped = replace(evidence, run_events=shifted, visibility_snapshots=(before_capture,))
    for scenario in (overlapped, replace(overlapped, visibility_snapshots=())):
        assert ev.criterion_f(scenario).verdict == ev.VERDICT_PASS
        assert _family(scenario, "candidate_marks").matched == 1
        assert _family(scenario, "candidate_price_evidence").matched == 1


@pytest.mark.parametrize("stage", ["planning", "recheck", "no-conflict"])
def test_actual_b3_driver_emits_pending_or_the_exact_settled_entry(stage):
    evidence = _settlement_fixture(two_entries=True)
    entries = []
    raw_receipts = []
    committed = []
    planning_view = _inventory("actual-driver-P", _dt(5, 9, 1))
    observed_views = [planning_view]
    observed_reads = {}

    for entry, outcome, opening in zip(
        evidence.entries, evidence.market_outcomes, evidence.bankroll_events[:2], strict=True,
    ):
        candidate = next(c for c in evidence.candidates if c.candidate_id == entry.candidate_id)
        identity = settle.MarketIdentity(candidate.condition_id, candidate.market_id, candidate.market_slug)
        mismatch = settle.EntryCashDiscrepancy(entry.entry_id, "ambiguous", (opening.event_id, 900))
        entries.append(settle.OpenSettleEntry(
            entry.entry_id, entry.candidate_id, "football", candidate.side, entry.stake,
            entry.price, entry.fee_category, entry.written_at, fee_source=entry.fee_source,
            entry_fee=entry.entry_fee, condition_id=candidate.condition_id,
            outcome=settle.CapturedOutcome(
                outcome.outcome_id, outcome.outcome, outcome.observed_at,
                outcome.confirmed_at, outcome.source_kind, identity=identity,
            ),
            opening_cash=settle.OpeningCash(opening.event_id, opening.amount, entry.entry_fee),
            cash_discrepancy=mismatch if stage == "planning" and entry.entry_id == 101 else None,
        ))

    def write_entry(_connection, record):
        minute = 10 if record.entry_id == 101 else 20
        recheck = _inventory(f"actual-driver-V{record.entry_id}", _dt(5, 10, minute))
        observed_views.append(recheck)
        observed_reads[record.entry_id] = ef.SettlementReadEvidence(
            "settle-synthetic", 1, record.entry_id, "actual driver traversal with in-memory reads",
            planning_snapshot_id=planning_view.snapshot_id, recheck_snapshot_id=recheck.snapshot_id,
        )
        if stage == "recheck" and record.entry_id == 101:
            raise settle.EntryCashMismatch(settle.EntryCashDiscrepancy(101, "metadata", (390,)))
        terminal = _inventory(f"actual-driver-terminal-{record.entry_id}", _dt(5, 10, minute + 1))
        observed_views.append(terminal)
        observed_reads[record.entry_id] = replace(
            observed_reads[record.entry_id], terminal_cash_snapshot_id=terminal.snapshot_id,
        )
        committed.append(record)
        return {"settlement_id": 300 + len(committed) - 1,
                "inserted": True, "bankroll_event_id": 400 + len(committed) - 1}

    clock = iter((_dt(5, 9), _dt(5, 9), _dt(5, 11)))
    result = settle.run_settle(
        conn=None, repo_root=None, team="football", operator="synthetic",
        open_entries=lambda _connection: entries, now_utc=lambda: next(clock),
        operation_key="settle-synthetic", commit_check=lambda: COMMIT,
        settle_writer=write_entry, receipt_writer=lambda _connection, event: raw_receipts.append(event),
    )
    success = raw_receipts[-1]
    assert (success.command, success.phase, success.output_evidence[-1]) == (
        "settle", "SUCCEEDED", "settled:102:VOIDED_REFUND",
    )
    if stage == "no-conflict":
        assert success.output_evidence[0] == "settled:101:NATURAL"
        assert [record.entry_id for record in committed] == [101, 102]
        assert result["settled_count"] == 2
    else:
        assert success.output_evidence[0] == "pending:101:entry_cash_mismatch"
        assert [record.entry_id for record in committed] == [102]
        assert result["settled_count"] == result["pending_count"] == 1

    report_rows = tuple(
        RunEventRecord(receipt.operation_key, receipt.attempt, receipt.phase, receipt.occurred_at,
                       command=receipt.command, code_commit=receipt.code_commit,
                       output_evidence=receipt.output_evidence)
        for receipt in raw_receipts
    )
    kept_ids = {record.entry_id for record in committed}
    if stage == "planning":
        assert 101 not in observed_reads
        observed_reads[101] = ef.SettlementReadEvidence(
            "settle-synthetic", 1, 101, "actual driver planning only", planning_snapshot_id=planning_view.snapshot_id,
        )
    if stage == "recheck":
        assert observed_reads[101].recheck_snapshot_id
        assert observed_reads[101].terminal_cash_snapshot_id is None
    assert {entry_id for entry_id, read in observed_reads.items() if read.terminal_cash_snapshot_id} == kept_ids
    verified = replace(
        evidence, run_events=(*_without_command(evidence, "settle"), *report_rows),
        settlements=tuple(row for row in evidence.settlements if row.entry_id in kept_ids),
        bankroll_events=tuple(row for row in evidence.bankroll_events
                              if not row.event_key.startswith("settle:") or row.entry_id in kept_ids),
        settlement_reads=tuple(observed_reads.values()),
        visibility_snapshots=(*evidence.visibility_snapshots, *observed_views),
    )
    assert ev.criterion_f(verified).verdict == ev.VERDICT_PASS
    assert _family(verified, "settlements").matched == len(kept_ids)
    assert _family(verified, "bankroll_events").matched == len(kept_ids)


def _complete_view(name, stamp, family, rows=(), *, receipt=None):
    refs = tuple(ef.row_fingerprint(family, row) for row in rows)
    if receipt is not None:
        refs += (ef.row_fingerprint("command_run_events", receipt),)
    return VisibilitySnapshot(name, stamp, (family,), refs)


def _verified_receipt_failure(evidence, *parts):
    report = ef.reconcile_universe(evidence)
    assert any(all(part in reason for part in parts) for reason in report.writer_failures), report
    _assert_reason(evidence, ev.VERDICT_FAIL, *parts)
    return report


@pytest.mark.parametrize("proof", ["actual-pk", "complete", "same-receipt-snapshot", "same-time-only", "partial"])
def test_scan_success_requires_its_persisted_scan_even_when_it_ran_no_candidates(proof):
    base = _green()
    scan = base.scan_runs[-1]
    terminal = replace(base.run_events[-1], run_event_id=501)
    base = replace(base, run_events=(*base.run_events[:-1], terminal))
    assert not any(row.scan_id == scan.scan_id for row in base.candidates)
    assert ev.criterion_f(base).verdict == ev.VERDICT_PASS
    if proof in ("actual-pk", "partial"):
        keys = (("scan_runs", scan.scan_id),) if proof == "actual-pk" else (("scan_runs", 999),)
        after = _inventory("scan-lookup", _dt(29, 7), absent=keys)
    else:
        stamp = _dt(29, 7) if proof == "complete" else terminal.occurred_at
        after = _complete_view("scan-view", stamp, "scan_runs", base.scan_runs[:-1],
                               receipt=terminal if proof == "same-receipt-snapshot" else None)
    missing = replace(base, scan_runs=base.scan_runs[:-1], visibility_snapshots=(after,))
    if proof in ("partial", "same-time-only"):
        _assert_reason(missing, ev.VERDICT_NOT_ASSESSABLE, "scan_id=30", "(R3)")
        assert not ef.reconcile_universe(missing).writer_failures
    else:
        result = _verified_receipt_failure(missing, "scan_runs scan_id=30", "absent")
        assert result.unmatched_total == 0
    assert _family(missing, "scan_runs").matched == 29


def test_scan_bad_attempt_and_later_exact_retry_are_independent():
    evidence = _green()
    terminal = evidence.run_events[-1]
    missing_id = replace(terminal, scan_id=999)
    retry = replace(terminal, attempt=2)
    broken_then_proved = replace(evidence, run_events=(*evidence.run_events[:-1], missing_id, retry))
    _verified_receipt_failure(broken_then_proved, "1, 'SUCCEEDED'", "conflicting scan_id")
    family = _family(broken_then_proved, "scan_runs")
    assert family.matched == 30
    assert "2, 'SUCCEEDED'" in next(reason for reason in family.disclosed if "row 30 " in reason)


@pytest.mark.parametrize("missing", [("candidate_marks",), ("candidate_price_evidence",),
                                     ("candidate_marks", "candidate_price_evidence")])
@pytest.mark.parametrize("complete", [True, False])
def test_capture_receipt_checks_missing_atomic_legs_in_both_directions(missing, complete):
    base, _quote = _capture_fixture()
    assert ev.criterion_f(base).verdict == ev.VERDICT_PASS
    field = {"candidate_marks": "candidate_marks", "candidate_price_evidence": "price_evidence"}
    after = tuple(
        _complete_view(f"after-{family}", _dt(5, 12), family)
        if complete else _inventory(f"unrelated-lookup-{family}", _dt(5, 12), absent=((family, 999),))
        for family in missing
    )
    evidence = replace(base, visibility_snapshots=after, **{field[family]: () for family in missing})
    report = ef.reconcile_universe(evidence)
    if complete:
        _verified_receipt_failure(evidence, "capture-marks-synthetic", "item 0", "atomic capture counterpart")
        for family in missing:
            assert any(family in reason for reason in report.writer_failures)
        if len(missing) == 2:
            assert report.unmatched_total == 0
    else:
        assert not report.writer_failures
        _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, "atomic capture counterpart", "(R3)")
    for family in field:
        assert _family(evidence, family).matched == 0


def _change_capture_summary(evidence, **updates):
    original = evidence.run_events[-1]
    values = json.loads(original.output_evidence[0])
    values.update(updates)
    return replace(evidence, run_events=(*evidence.run_events[:-1], replace(
        original, output_evidence=(json.dumps(values, sort_keys=True, separators=(",", ":")),),
    )))


@pytest.mark.parametrize("counter", ["observations", "marks_written", "close_eligible",
                                     "close_evidence_written", "candidates_seen"])
def test_capture_summary_cannot_claim_zero_for_a_verified_item(counter):
    green, _ = _capture_fixture()
    broken = _change_capture_summary(green, **{counter: 0})
    _verified_receipt_failure(broken, "capture-marks-synthetic", f"{counter}=0", "disagrees with 1")
    assert _family(broken, "candidate_marks").matched == 0


def test_capture_partial_pack_can_disprove_a_zero_summary():
    green, _ = _capture_fixture()
    actual, = green.snapshots
    missing = hashlib.sha256(b"missing unrelated synthetic pack").hexdigest()
    summary = json.loads(green.run_events[-1].output_evidence[0])
    summary["packs"].append({"pack": "second.json", "snapshot_sha256": missing})
    broken = _change_capture_summary(green, packs=summary["packs"], marks_written=0)
    _verified_receipt_failure(broken, "marks_written=0", "at least 1")
    _assert_reason(broken, ev.VERDICT_FAIL, missing, "raw pack snapshot is unavailable")
    genuine_zero = replace(green, candidate_marks=(), price_evidence=(), snapshots=(),
                           run_events=(*_without_command(green, "capture-marks"), _capture_success(())))
    assert ev.criterion_f(genuine_zero).verdict == ev.VERDICT_PASS
    overstated = _change_capture_summary(genuine_zero, marks_written=1)
    _verified_receipt_failure(overstated, "marks_written=1", "disagrees with 0")
    assert _family(green, "snapshots").matched == 1
    absent = replace(green, snapshots=(), visibility_snapshots=(
        _inventory("pack-lookup", _dt(5, 11), absent=(("snapshots", actual.snapshot_id),)),
    ))
    _verified_receipt_failure(absent, actual.snapshot_id, "registered snapshots", "absent")


def _replay_capture_driver(monkeypatch, configured_packs, selectors, *, attempt=1, moment=None):
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg

    receipts, queries, writes = [], [], []
    monkeypatch.setattr(capture, "require_clean_worktree", lambda *_args, **_kwargs: COMMIT)
    monkeypatch.setattr(snapshots, "register_snapshot", lambda _connection, record: {
        "content_sha256": hashlib.sha256(record.content).hexdigest(),
    })
    monkeypatch.setattr(store_psycopg, "record_command_run_event",
                        lambda _connection, receipt: receipts.append(receipt))

    def find(_connection, *, scan_id, scan_operation_key, candidate_key):
        queries.append((scan_id, scan_operation_key, candidate_key))
        candidate = selectors[queries[-1]]
        return {"candidate_id": candidate.candidate_id, "event_start_at": candidate.event_start_at}

    def write(_connection, mark, *, close_evidence):
        writes.append((mark, close_evidence))
        return {"close_evidence_id": len(writes) if close_evidence is not None else None}

    monkeypatch.setattr(store_psycopg, "candidate_capture_target", find)
    monkeypatch.setattr(store_psycopg, "mark_capture_upsert", write)
    summary = capture.run_capture(
        capture.CaptureConfig(team="football"),
        tuple(capture.MarkPack(f"pack-{index}.json", pack.content)
              for index, pack in enumerate(configured_packs)),
        conn=None, repo_root=None, now_utc=lambda: moment or _dt(5, 10),
        operation_key="capture-marks-synthetic", attempt=attempt,
    )
    assert json.loads(receipts[-1].output_evidence[0]) == summary
    events = tuple(RunEventRecord(
        receipt.operation_key, receipt.attempt, receipt.phase, receipt.occurred_at,
        command=receipt.command, code_commit=receipt.code_commit, output_evidence=receipt.output_evidence,
    ) for receipt in receipts)
    return summary, queries, writes, events


@pytest.mark.parametrize("ordering", ["ab", "ba", "one-pack", "unpriced-first"])
def test_b2_capture_targets_use_first_selector_across_whole_attempt(monkeypatch, ordering):
    _example, quote = _capture_fixture()
    base = _green()
    ambiguous_scan = base.scan_runs[7]
    ambiguous_scan = replace(ambiguous_scan, operation_key="7")
    old_operation = base.scan_runs[7].operation_key
    candidates = (
        CandidateLinkRecord(11, 7, _dt(6, 8), COMMIT, candidate_key="K", event_start_at=_dt(11)),
        CandidateLinkRecord(12, 8, _dt(7, 8), COMMIT, candidate_key="K", event_start_at=None),
    )
    run_events = tuple(
        replace(event, operation_key="7") if event.operation_key == old_operation else event
        for event in base.run_events
    )
    base = replace(
        base, scan_runs=(*base.scan_runs[:7], ambiguous_scan, *base.scan_runs[8:]),
        run_events=run_events, candidates=(*base.candidates, *candidates),
        replay_agreements={**base.replay_agreements, "11": True, "12": True},
    )
    template = {key: value for key, value in quote.items() if key != "scan_operation_key"}
    a = {**template, "candidate_key": "K", "scan_id": 7, "sampling_kind": "PERIODIC",
         "observed_at": _dt(10, 7).isoformat()}
    b = {**template, "candidate_key": "K", "scan_operation_key": "7", "sampling_kind": "CLOSE",
         "observed_at": _dt(10, 8).isoformat()}
    if ordering == "unpriced-first":
        a = {key: value for key, value in a.items() if key not in ("bid", "ask", "mid", "last")}
    items = [b, a] if ordering == "ba" else [a, b]
    groups = [items] if ordering == "one-pack" else [[item] for item in items]
    packs = tuple(_snapshot(json.dumps(group).encode(), kind="capture_mark_pack") for group in groups)
    summary, queries, writes, receipts = _replay_capture_driver(
        monkeypatch, packs, {(7, None, "K"): candidates[0], (None, "7", "K"): candidates[1]},
        moment=_dt(10, 10),
    )
    target = candidates[1] if ordering == "ba" else candidates[0]
    assert queries == ([(None, "7", "K")] if ordering == "ba" else [(7, None, "K")])
    assert [mark.candidate_id for mark, _ in writes] == [target.candidate_id] * (
        1 if ordering == "unpriced-first" else 2
    )
    assert summary["candidates_seen"] == 1 and summary["observations"] == 2
    assert summary["close_evidence_written"] == (0 if ordering == "ba" else 1)
    assert summary["missing_event_start"] == (1 if ordering == "ba" else 0)
    expected_rows = [
        _quote_rows(raw, target, pack)
        for group, pack in zip(groups, packs, strict=True) for raw in group
        if any(key in raw for key in ("bid", "ask", "mid", "last"))
    ]
    marks = tuple(replace(mark, row_key=501 + index) for index, (mark, _close) in enumerate(expected_rows))
    closes = tuple(replace(close, row_key=601 + index) for index, (_mark, close) in enumerate(expected_rows)
                   if close is not None)
    assert [mark.candidate_mark_key for mark in marks] == [row.mark_key() for row, _ in writes]
    evidence = replace(base, run_events=(*base.run_events, *receipts), snapshots=packs,
                       candidate_marks=marks, price_evidence=closes)
    assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
    assert _family(evidence, "candidate_marks").matched == len(marks)
    assert _family(evidence, "candidate_price_evidence").matched == len(closes)
    bad_summary = _change_capture_summary(evidence, candidates_seen=2)
    _verified_receipt_failure(bad_summary, "capture summary candidates_seen=2")
    other = candidates[0] if target is candidates[1] else candidates[1]
    moved = replace(marks[0], candidate_id=other.candidate_id,
                    candidate_mark_key=marks[0].candidate_mark_key.replace(f"|{target.candidate_id}|",
                                                                         f"|{other.candidate_id}|"))
    supplied = (moved, *marks[1:])
    after = _complete_view("wrong-market-after", _dt(10, 12), "candidate_marks", supplied)
    bad = replace(evidence, candidate_marks=supplied, visibility_snapshots=(*evidence.visibility_snapshots, after))
    _verified_receipt_failure(bad, "candidate_marks", marks[0].candidate_mark_key, "absent")
    if len(packs) == 2:
        gap = replace(evidence, snapshots=packs[1:])
        _assert_reason(gap, ev.VERDICT_NOT_ASSESSABLE, "earlier pack or item unavailable for first lookup")
        assert not ef.reconcile_universe(gap).writer_failures
    selected_only = replace(evidence, candidates=tuple(row for row in evidence.candidates if row != other))
    assert ev.criterion_f(selected_only).verdict == ev.VERDICT_PASS


def test_actual_capture_repeated_item_counts_ensures_and_deduplicates_rows(monkeypatch):
    green, quote = _capture_fixture()
    target = green.candidates[-2]
    pack = _snapshot(json.dumps([quote, quote]).encode(), kind="capture_mark_pack")
    selectors = {(None, quote["scan_operation_key"], quote["candidate_key"]): target}
    summary, calls, writes, receipts = _replay_capture_driver(monkeypatch, (pack,), selectors)
    assert len(calls) == 1 and len(writes) == 2
    assert summary["marks_written"] == summary["close_evidence_written"] == 2
    mark, close = _quote_rows(quote, target, pack)
    repeated = replace(green, run_events=(*_without_command(green, "capture-marks"), *receipts),
                       snapshots=(pack,), candidate_marks=(mark,), price_evidence=(close,))
    assert ev.criterion_f(repeated).verdict == ev.VERDICT_PASS
    assert _family(repeated, "candidate_marks").matched == 1
    assert _family(repeated, "candidate_price_evidence").matched == 1


@pytest.mark.parametrize("missing", [("settlements",), ("bankroll_events",), ("settlements", "bankroll_events")])
@pytest.mark.parametrize("complete", [True, False])
def test_settle_receipt_checks_absent_pair_independent_of_existing_rows(missing, complete):
    base = _settlement_fixture()
    assert ev.criterion_f(base).verdict == ev.VERDICT_PASS
    openings = base.bankroll_events[:1]
    supplied = {}
    after = []
    for family in missing:
        rows = openings if family == "bankroll_events" else ()
        supplied[family] = rows
        if complete:
            after.append(_complete_view(f"complete-{family}", _dt(5, 12), family, rows))
        else:
            after.append(_inventory(f"partial-{family}", _dt(5, 12), absent=((family, 999),)))
    evidence = replace(base, **supplied, visibility_snapshots=(*base.visibility_snapshots, *after))
    if complete:
        report = _verified_receipt_failure(evidence, "settled entry 101 at item 0", "absent")
        if len(missing) == 2:
            assert report.unmatched_total == 0
    else:
        assert not ef.reconcile_universe(evidence).writer_failures
        _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, "settled entry 101 at item 0", "projection unavailable")
    assert _family(evidence, "settlements").matched == 0
    assert _family(evidence, "bankroll_events").matched == 0


def _settlement_plan(evidence, opened, *, excluded=(), complete=True):
    planning = _inventory("complete-open-query", _dt(5, 9, 15))
    report = ef.SettlementPlanningEvidence(
        "settle-synthetic", 1, planning.snapshot_id, "synthetic P open query", opened, excluded,
        complete=complete,
    )
    return replace(evidence, settlement_planning=(report,),
                   visibility_snapshots=(*evidence.visibility_snapshots, planning))


@pytest.mark.parametrize("change", ["extra-pending", "reverse", "omitted", "duplicate"])
def test_settle_actual_complete_planning_matches_success_array_one_to_one(change):
    green = _settlement_plan(_settlement_fixture(two_entries=True), (102, 101))
    assert ev.criterion_f(green).verdict == ev.VERDICT_PASS
    terminal = green.run_events[-1]
    first, second = terminal.output_evidence
    items = {
        "extra-pending": (first, second, "pending:999:no_captured_outcome"),
        "reverse": (second, first), "omitted": (first,), "duplicate": (first, first, second),
    }[change]
    evidence = replace(green, run_events=(*green.run_events[:-1], replace(terminal, output_evidence=items)))
    expected = {
        "extra-pending": ("outside planning P", "999"),
        "reverse": ("result order is reversed", "entry 102 at index 0", "entry 101 at index 1"),
        "omitted": ("102", "have no result item"),
        "duplicate": ("multiple B3 result items", "entry 101", "indices 0 and 1"),
    }[change]
    _verified_receipt_failure(evidence, *expected)

    if change == "extra-pending":
        partial = replace(evidence.settlement_planning[0], complete=False)
        data = replace(evidence, settlement_planning=(partial,))
        assert not ef.reconcile_universe(data).writer_failures
        _assert_reason(data, ev.VERDICT_NOT_ASSESSABLE, "membership unavailable", "999")
        stated_excluded = replace(partial, checked_excluded_ids=(999,))
        _verified_receipt_failure(replace(data, settlement_planning=(stated_excluded,)), "outside planning P", "999")
    if change == "reverse":
        no_plan = replace(evidence, settlement_planning=())
        _verified_receipt_failure(no_plan, *expected)
        projection = replace(evidence.run_events[-1], output_order_available=False)
        reordered = replace(evidence, run_events=(*evidence.run_events[:-1], projection))
        assert not ef.reconcile_universe(reordered).writer_failures
        _assert_reason(reordered, ev.VERDICT_NOT_ASSESSABLE, "raw success array order is unavailable")


def test_real_settle_writer_emits_numeric_pending_order_and_allows_empty_retry():
    receipts = []

    def actual_run(ids, attempt):
        entries = tuple(settle.OpenSettleEntry(entry_id, entry_id + 1, "team", "YES",
                                              Decimal("25"), Decimal("0.4"), "sports", _dt(-3))
                        for entry_id in ids)
        settle.run_settle(
            conn=None, repo_root=None, team="football", operator="synthetic",
            open_entries=lambda _conn: entries, now_utc=lambda: _dt(5, 11),
            operation_key="settle-synthetic", attempt=attempt, commit_check=lambda: COMMIT,
            settle_writer=lambda *_args: pytest.fail("pending entries never write"),
            receipt_writer=lambda _conn, row: receipts.append(row),
        )
        result = receipts[-1]
        return RunEventRecord(result.operation_key, result.attempt, result.phase, result.occurred_at,
                              command=result.command, code_commit=result.code_commit,
                              output_evidence=result.output_evidence)

    terminal = actual_run((10, 2), 1)
    assert terminal.output_evidence == ("pending:2:no_captured_outcome", "pending:10:no_captured_outcome")
    green = _green(run_events=(*_green().run_events, terminal))
    green = _settlement_plan(green, (10, 2))
    assert ev.criterion_f(green).verdict == ev.VERDICT_PASS
    nothing = actual_run((), 2)
    assert nothing.output_evidence == ()
    assert ev.criterion_f(replace(green, run_events=(*green.run_events, nothing))).verdict == ev.VERDICT_PASS


@pytest.mark.parametrize("stamps,expected", [
    ((20, 15), ev.VERDICT_FAIL), ((15, 20), ev.VERDICT_PASS), ((15, 15), ev.VERDICT_PASS),
])
def test_settle_v_rechecks_respect_numeric_plan_order_only_within_attempt(stamps, expected):
    green = _settlement_fixture(two_entries=True)
    planning = _inventory("common-planning", _dt(5, 9))
    first = _inventory("V-101", _dt(5, 10, stamps[0]))
    second = _inventory("V-102", _dt(5, 10, stamps[1]))
    reads = (
        ef.SettlementReadEvidence("settle-synthetic", 1, 101, "synthetic P and V", planning.snapshot_id,
                                  first.snapshot_id),
        ef.SettlementReadEvidence("settle-synthetic", 1, 102, "synthetic P and V", planning.snapshot_id,
                                  second.snapshot_id),
    )
    evidence = replace(green, settlement_reads=reads[::-1],
                       visibility_snapshots=(*green.visibility_snapshots, planning, second, first))
    assert ev.criterion_f(evidence).verdict == expected
    if expected == ev.VERDICT_FAIL:
        _verified_receipt_failure(evidence, "cash recheck order reversed", "entry 101", "V-101", "entry 102", "V-102")
        assert _family(evidence, "settlements").matched == 0
        receipt_1 = _settle_success("settled:101:NATURAL")
        receipt_2 = _settle_success("settled:102:VOIDED_REFUND", attempt=2)
        independently_ordered = replace(evidence,
            run_events=(*_without_command(evidence, "settle"), receipt_1, receipt_2),
            settlement_reads=(reads[0], replace(reads[1], attempt=2)),
        )
        assert ev.criterion_f(independently_ordered).verdict == ev.VERDICT_PASS
    else:
        assert _family(evidence, "settlements").matched == 2


@pytest.mark.parametrize("stage", ["after-t", "after-query-before-t", "before-query", "straddle", "after-as-of"])
def test_extra_terminal_leg_is_adjudicated_at_last_cash_query(stage):
    green = _settlement_fixture()
    settlement, = green.settlements
    cash, = _terminal_cash(green)
    extra = replace(cash, event_id=900, event_key="extra-terminal")
    foreign = ("bankroll_events", extra)
    absent = (("bankroll_events", 900),)
    planning_checks = (("settlements", settlement.row_key),)
    if stage != "straddle":
        planning_checks += absent
    planning = _inventory("planning-for-extra", _dt(5, 8), absent=planning_checks)
    view = _inventory("terminal-cash-view", _dt(5, 10, 15), foreign) if stage == "before-query" else None
    views = [*green.visibility_snapshots, planning]
    if view:
        views.append(view)
    if stage in ("after-t", "after-query-before-t"):
        stamp = _dt(5, 12) if stage == "after-t" else _dt(5, 10, 30)
        views.append(_inventory("pair-before-extra", stamp, ("settlements", settlement),
                                ("bankroll_events", cash), absent=absent))
    if stage == "after-as-of":
        views.append(_inventory("cutoff-without-extra", green.as_of, absent=absent))
        views.append(_inventory("extra-after-cutoff", _dt(30, 1), foreign))
    else:
        views.append(_inventory("extra-visible", _dt(5, 13), foreign))
    data = replace(green, bankroll_events=(*green.bankroll_events, extra), visibility_snapshots=tuple(views),
                   settlement_reads=(ef.SettlementReadEvidence(
                       "settle-synthetic", 1, 101, "synthetic exact read witness", planning.snapshot_id,
                       terminal_cash_snapshot_id=view.snapshot_id if view else None,
                   ),))
    if stage == "before-query":
        _verified_receipt_failure(data, "extra event 900", "already visible by the terminal cash query")
        assert _family(data, "settlements").matched == 0
        assert _family(data, "bankroll_events").matched == 0
    elif stage == "straddle":
        _assert_reason(data, ev.VERDICT_NOT_ASSESSABLE, "extra event 900", "visibility unproven at terminal cash query")
        assert _family(data, "settlements").matched == 0
        assert _family(data, "bankroll_events").matched == 0
        assert not ef.reconcile_universe(data).writer_failures
    elif stage == "after-as-of":
        assert ev.criterion_f(data).verdict == ev.VERDICT_PASS
        assert _family(data, "settlements").matched == _family(data, "bankroll_events").matched == 1
    else:
        assert _family(data, "settlements").matched == _family(data, "bankroll_events").matched == 1
        # A separate external writer row has no source attribution even
        # when the earlier ensured pair was completely evidenced.
        _assert_reason(data, ev.VERDICT_FAIL, "bankroll_events row 900")
        assert all("row 400" not in reason for reason in _family(data, "bankroll_events").unmatched)
        assert not _family(data, "settlements").unmatched


def test_r6e_old_shape_and_business_time_need_independent_history():
    cash = BankrollEventRecord("legacy-cash", "SETTLE_NATURAL", _dt(-3), is_legacy=True)
    old_outcome = MarketOutcomeRecord("legacy_import", _dt(-3))
    copied_amount = BankrollEventRecord("legacy-import", "LEGACY_IMPORT", _dt(-3))
    for family, row in (("bankroll_events", cash), ("market_outcomes", old_outcome),
                        ("bankroll_events", copied_amount)):
        evidence = _green(**{family: (row,)})
        _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, family, "writing period unproven (R6e)")
        assert _family(evidence, family).pre_anchor == 0
        proved = replace(row, outcome_id=901, outcome="YES") if family == "market_outcomes" else row
        history = _inventory("known-before-cutover", _dt(-1), (family, proved))
        historic = _green(**{family: (proved,)}, visibility_snapshots=(history,))
        assert ev.criterion_f(historic).verdict == ev.VERDICT_PASS
        assert _family(historic, family).pre_anchor == 1
        late = _known_window(_green(**{family: (proved,)}), (family, proved),
                             unreceipted=((family, proved),), unreceipted_source="known legacy import")
        _assert_reason(late, ev.VERDICT_FAIL, family, "unmatched")
        assert _family(late, family).pre_anchor == 0
        for unrelated in (
            replace(proved, outcome_id=902) if family == "market_outcomes" else replace(proved, event_id="other-cash"),
        ):
            _assert_reason(replace(historic, **{family: (unrelated,)}),
                           ev.VERDICT_NOT_ASSESSABLE, family, "writing period unproven")
        changed = (
            replace(proved, outcome="NO") if family == "market_outcomes" else replace(proved, amount=Decimal("1"))
        )
        with pytest.raises(ValueError, match="conflicting inventory content"):
            ev.criterion_f(replace(historic, **{family: (changed,)}))


@pytest.mark.parametrize("family,projected", [
    ("candidates", CandidateLinkRecord("unknown-old", 999, _dt(-3), COMMIT)),
    ("entries", EntryRecord("unknown-old", "unknown-child-parent", _dt(-3))),
    ("marks", EntryChildRecord("unknown-old", _dt(-3))),
    ("candidate_marks", CandidateChildRecord("unknown-old", _dt(-3))),
    ("snapshots", SnapshotRecord("old-source", _dt(-3), cited=True)),
])
def test_legacy_projection_of_even_an_old_business_date_is_not_history(family, projected):
    if family == "candidates":
        evidence = _green(candidates=(*_green().candidates, projected))
    else:
        evidence = _green(**{family: (projected,)})
    _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, family, "writing period unproven")
    assert _family(evidence, family).pre_anchor == 0


def test_settle_complete_planning_view_reconstructs_the_open_join():
    green = _settlement_fixture(two_entries=True)
    projected = tuple(
        ef.row_fingerprint(family, row)
        for family, rows in (("candidates", green.candidates), ("entries", green.entries))
        for row in rows
    )
    planning = VisibilitySnapshot(
        "actual-P-complete-tables", _dt(5, 9), ("candidates", "entries", "settlements"), projected,
    )
    green = replace(green, visibility_snapshots=(*green.visibility_snapshots, planning),
                    settlement_reads=(ef.SettlementReadEvidence(
                        "settle-synthetic", 1, 101, "synthetic bound complete P", planning.snapshot_id,
                    ),))
    assert ev.criterion_f(green).verdict == ev.VERDICT_PASS
    success = green.run_events[-1]
    omitted = replace(green, run_events=(*green.run_events[:-1], replace(
        success, output_evidence=success.output_evidence[:1],
    )))
    _verified_receipt_failure(omitted, planning.snapshot_id, "102", "have no result item")


def test_settle_recheck_of_later_entry_requires_earlier_pair_already_committed():
    green = _settlement_fixture(two_entries=True)
    first, _second = green.settlements
    cash = _terminal_cash(green)[0]
    later = _inventory("actual-V102", _dt(5, 10, 20),
                       absent=(("settlements", first.row_key), ("bankroll_events", cash.event_id)))
    with_order = replace(green, visibility_snapshots=(*green.visibility_snapshots, later),
                         settlement_reads=(ef.SettlementReadEvidence(
                             "settle-synthetic", 1, 102, "synthetic V for second write",
                             recheck_snapshot_id=later.snapshot_id,
                         ),))
    _verified_receipt_failure(with_order, "entry 101 committed before entry 102 recheck",
                              "actual-V102", "settlement cash leg 'settle:101'", "absent")
    assert _family(with_order, "settlements").matched == 0


def test_settle_source_identifier_separates_missing_projection_null_and_known_absent_row():
    green = _settlement_fixture()
    known, = green.settlements
    assert ev.criterion_f(green).verdict == ev.VERDICT_PASS
    omitted = replace(known, source_outcome_id=EntryChildRecord(101, known.written_at).source_outcome_id)
    gap = replace(green, settlements=(omitted,))
    _assert_reason(gap, ev.VERDICT_NOT_ASSESSABLE, "source_outcome_id is not projected")
    assert not ef.reconcile_universe(gap).writer_failures
    supplied_null = replace(green, settlements=(replace(known, source_outcome_id=None),))
    _verified_receipt_failure(supplied_null, "source_outcome_id", "SQL NULL")
    original = green.market_outcomes[0]
    opaque = replace(known, source_outcome_id=999)
    unknown = replace(green, settlements=(opaque,))
    _assert_reason(unknown, ev.VERDICT_NOT_ASSESSABLE, "source_outcome_id 999 projection unavailable")
    reviewed = _inventory("selected-source-lookup", _dt(5, 12), ("market_outcomes", original),
                          absent=(("market_outcomes", 999),))
    bad = replace(unknown, visibility_snapshots=(*unknown.visibility_snapshots, reviewed))
    _verified_receipt_failure(bad, "source_outcome_id 999 absent", "selected-source-lookup")


def test_capture_complete_view_without_resolved_keys_cannot_prove_logical_absence():
    green, _ = _capture_fixture()
    unrelated = ef.RowFingerprint("candidate_marks", 1024, "8" * 64)
    partial = VisibilitySnapshot("unknown-key-projection", _dt(5, 12), ("candidate_marks",), (unrelated,))
    evidence = replace(green, candidate_marks=(), visibility_snapshots=(partial,))
    _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, "candidate_marks", "projection unavailable")
    assert not ef.reconcile_universe(evidence).writer_failures
    assert _family(evidence, "candidate_price_evidence").matched == 0


def test_b3_verified_pair_and_other_attempt_terminal_cash_gap_are_both_reported():
    evidence = _settlement_fixture()
    settlement, = evidence.settlements
    cash, = _terminal_cash(evidence)
    extra = replace(cash, event_id=900, event_key="later-terminal")
    pair = (("settlements", settlement), ("bankroll_events", cash))
    first, = (event for event in evidence.run_events if event.command == "settle")
    second = replace(first, operation_key="second-settle-synthetic", occurred_at=_dt(5, 12))
    prior = _inventory("before-first-and-second-P", _dt(5, 9), absent=(
        ("settlements", settlement.row_key), ("bankroll_events", cash.event_id),
    ))
    after_first = _inventory("after-first-success", _dt(5, 11, 20), *pair,
                             absent=(("bankroll_events", extra.event_id),))
    after_both = _inventory("extra-visible-later", _dt(5, 12, 20), ("bankroll_events", extra))
    reads = tuple(ef.SettlementReadEvidence(
        event.operation_key, event.attempt, 101, "synthetic overlapping P", prior.snapshot_id,
    ) for event in (first, second))
    evidence = replace(
        evidence, run_events=(*evidence.run_events, second), settlement_reads=reads,
        bankroll_events=(*evidence.bankroll_events, extra),
        visibility_snapshots=(*evidence.visibility_snapshots, prior, after_first, after_both),
    )
    _assert_reason(evidence, ev.VERDICT_FAIL, "bankroll_events row 900")
    settlements = _family(evidence, "settlements")
    assert settlements.matched == _family(evidence, "bankroll_events").matched == 1
    assert any("'settle-synthetic'" in detail for detail in settlements.disclosed)
    assert all("second-settle-synthetic" not in detail for detail in settlements.disclosed)
    report = ef.reconcile_universe(evidence)
    assert any("second-settle-synthetic" in reason and "entry 101" in reason and "extra event 900" in reason
               and "terminal cash query" in reason and "(R3)" in reason for reason in report.shortfalls)
    _assert_reason(evidence, ev.VERDICT_FAIL, "second-settle-synthetic", "entry 101", "terminal cash query", "(R3)")

    proof_after_second = _inventory("after-second-success", _dt(5, 12, 10), *pair,
                                    absent=(("bankroll_events", extra.event_id),))
    confirmed = replace(evidence, visibility_snapshots=(*evidence.visibility_snapshots, proof_after_second))
    assert not ef.reconcile_universe(confirmed).shortfalls
    assert any("second-settle-synthetic" in detail for detail in _family(confirmed, "settlements").disclosed)
    assert _family(confirmed, "settlements").matched == _family(confirmed, "bankroll_events").matched == 1


@pytest.mark.parametrize("stage", ["terminal-before-prior-v", "terminal-excludes-prior-pair"])
def test_b3_later_terminal_read_alone_bounds_previous_entry_commit(stage):
    green = _settlement_fixture(two_entries=True)
    first, _second = green.settlements
    cash = _terminal_cash(green)[0]
    recheck = _inventory("actual-V101", _dt(5, 10, 20))
    absent = (("settlements", first.row_key), ("bankroll_events", cash.event_id))
    earlier = stage == "terminal-before-prior-v"
    terminal = _inventory("actual-cash-query-102", _dt(5, 10, 15 if earlier else 35),
                          absent=() if earlier else absent)
    reads = (
        ef.SettlementReadEvidence("settle-synthetic", 1, 101, "synthetic cash recheck",
                                  recheck_snapshot_id=recheck.snapshot_id),
        ef.SettlementReadEvidence("settle-synthetic", 1, 102, "synthetic last terminal cash query",
                                  terminal_cash_snapshot_id=terminal.snapshot_id),
    )
    evidence = replace(green, settlement_reads=reads, visibility_snapshots=(
        *green.visibility_snapshots, recheck, terminal,
    ))
    _verified_receipt_failure(evidence, "entry 101", "entry 102", terminal.snapshot_id)
    assert _family(evidence, "settlements").matched == 0
    assert _family(evidence, "bankroll_events").matched == 0

    after_and_visible = _inventory(
        terminal.snapshot_id, _dt(5, 10, 35), ("settlements", first), ("bankroll_events", cash),
    )
    ordered = replace(evidence, visibility_snapshots=(*green.visibility_snapshots, recheck, after_and_visible))
    assert ev.criterion_f(ordered).verdict == ev.VERDICT_PASS
    assert _family(ordered, "settlements").matched == _family(ordered, "bankroll_events").matched == 2


def test_b3_visible_pair_does_not_bind_this_success_when_another_started_could_commit():
    base = _settlement_fixture()
    settlement, = base.settlements
    cash, = _terminal_cash(base)
    extra = replace(cash, event_id=900, event_key="external-terminal")
    pair = (("settlements", settlement), ("bankroll_events", cash))
    early = _inventory("planning-no-pair", _dt(5, 9, 15), absent=(
        ("settlements", settlement.row_key), ("bankroll_events", cash.event_id),
    ))
    pair_view = _inventory("first-pair-visible", _dt(5, 10), *pair, absent=(("bankroll_events", 900),))
    after = _inventory("extra-visible-before-T", _dt(5, 10, 30), ("bankroll_events", extra))
    start = RunEventRecord("settle-synthetic", 1, "STARTED", _dt(5, 9), command="settle", code_commit=COMMIT)
    competitor = replace(start, operation_key="competing-settlement", occurred_at=_dt(5, 9, 20))
    reads = (ef.SettlementReadEvidence(
        start.operation_key, 1, 101, "synthetic bound P", early.snapshot_id,
    ),)
    base = replace(
        base, settlement_reads=reads, run_events=(*base.run_events, start),
        bankroll_events=(*base.bankroll_events, extra),
        visibility_snapshots=(*base.visibility_snapshots, early, pair_view, after),
    )
    _assert_reason(base, ev.VERDICT_FAIL, "bankroll_events row 900")
    assert _family(base, "settlements").matched == _family(base, "bankroll_events").matched == 1
    uncertain = replace(base, run_events=(*base.run_events, competitor))
    _assert_reason(uncertain, ev.VERDICT_FAIL, "entry 101", "extra event 900", "terminal cash query", "(R3)")
    assert _family(uncertain, "settlements").in_window == 1
    assert _family(uncertain, "settlements").matched == _family(uncertain, "bankroll_events").matched == 0
    assert not ef.reconcile_universe(uncertain).writer_failures
    assert not _family(uncertain, "settlements").unmatched

    started_after_commit = replace(competitor, occurred_at=_dt(5, 10, 15))
    outside_commit_interval = replace(base, run_events=(*base.run_events, started_after_commit))
    assert _family(outside_commit_interval, "settlements").matched == 1
    assert _family(outside_commit_interval, "bankroll_events").matched == 1

    earlier_start = replace(competitor, occurred_at=_dt(5, 8))
    earlier_fail = replace(earlier_start, phase="FAILED", occurred_at=_dt(5, 8, 50), error_code="synthetic")
    already_ended = replace(base, run_events=(*base.run_events, earlier_start, earlier_fail))
    assert _family(already_ended, "settlements").matched == 1
    assert _family(already_ended, "bankroll_events").matched == 1

    # The competitor can still have inserted the pair. A direct check of
    # this attempt's terminal query can prove its independent ensure.
    query_view = _inventory("this-attempt-terminal-query", _dt(5, 10, 10), *pair,
                            absent=(("bankroll_events", 900),))
    with_query = replace(
        uncertain, visibility_snapshots=(*uncertain.visibility_snapshots, query_view),
        settlement_reads=(replace(reads[0], terminal_cash_snapshot_id=query_view.snapshot_id),),
    )
    _assert_reason(with_query, ev.VERDICT_FAIL, "bankroll_events row 900")
    assert _family(with_query, "settlements").matched == _family(with_query, "bankroll_events").matched == 1
    assert not any("terminal cash query" in detail for detail in ef.reconcile_universe(with_query).shortfalls)


def test_b2_missing_earlier_numeric_selector_pack_cannot_erase_raw_ensure_lower_bound():
    green, quote = _capture_fixture()
    quote = {name: value for name, value in quote.items() if name != "scan_operation_key"}
    quote["scan_id"] = 1
    pack = _snapshot(json.dumps([quote]).encode(), kind="capture_mark_pack")
    target = green.candidates[-2]
    mark, close = _quote_rows(quote, target, pack)
    receipt = _capture_success((pack,), targets=(target,))
    green = replace(green, snapshots=(pack,), candidate_marks=(mark,), price_evidence=(close,),
                    run_events=(*_without_command(green, "capture-marks"), receipt))
    assert ev.criterion_f(green).verdict == ev.VERDICT_PASS
    digest = hashlib.sha256(b"unknown prefix pack").hexdigest()
    original = json.loads(receipt.output_evidence[0])
    incomplete = _change_capture_summary(
        green, packs=[{"pack": "unknown-prefix.json", "snapshot_sha256": digest}, *original["packs"]],
    )
    _assert_reason(incomplete, ev.VERDICT_NOT_ASSESSABLE, "earlier pack or item unavailable")
    assert not ef.reconcile_universe(incomplete).writer_failures
    contradicts = _change_capture_summary(incomplete, marks_written=0)
    _verified_receipt_failure(contradicts, "capture summary marks_written=0", "at least 1")
    _assert_reason(contradicts, ev.VERDICT_FAIL, "earlier pack or item unavailable", "(R3)")
    assert _family(contradicts, "candidate_marks").matched == 0


@pytest.mark.parametrize("family,identity", [("entries", 101), ("candidates", 61), ("scan_runs", 600)])
def test_settlement_parent_projection_gap_requires_actual_absence_evidence(family, identity):
    green = _settlement_fixture()
    assert ev.criterion_f(green).verdict == ev.VERDICT_PASS
    projected_id = {"entries": "entry_id", "candidates": "candidate_id", "scan_runs": "scan_id"}[family]
    rows = tuple(row for row in getattr(green, family) if getattr(row, projected_id) != identity)
    missing = replace(green, **{family: rows})
    _assert_reason(missing, ev.VERDICT_NOT_ASSESSABLE, family, str(identity), "projection unavailable", "(R3)")
    assert not ef.reconcile_universe(missing).writer_failures
    assert _family(missing, "settlements").matched == _family(missing, "bankroll_events").matched == 0

    previous, = missing.visibility_snapshots
    earlier = replace(previous, rows=tuple(
        row for row in previous.rows if (row.family, row.row_key) != (family, str(identity))
    ))
    after = _inventory("real-parent-lookup", _dt(5, 12), absent=((family, identity),))
    known_absent = replace(missing, visibility_snapshots=(earlier, after))
    _verified_receipt_failure(known_absent, "entry 101", family, str(identity), "real-parent-lookup")


def test_invalid_older_capture_pack_success_cannot_supply_history_for_its_snapshot():
    green, quote = _capture_fixture()
    valid_pack = green.snapshots[0]
    invalid_pack = _snapshot(json.dumps(quote).encode(), at=_dt(-4), kind="capture_mark_pack")
    summary = json.loads(green.run_events[-1].output_evidence[0])
    summary["packs"][0]["snapshot_sha256"] = invalid_pack.snapshot_id
    old = replace(green.run_events[-1], occurred_at=_dt(-2), output_evidence=(json.dumps(summary),))
    base = _green(snapshots=(invalid_pack,))
    evidence = replace(base, run_events=(*base.run_events, old))
    _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, invalid_pack.snapshot_id, "writing period unproven")
    assert _family(evidence, "snapshots").pre_anchor == 0
    historical = _inventory("independent-pre-anchor-archive", _dt(-1), ("snapshots", invalid_pack))
    verified = replace(evidence, visibility_snapshots=(historical,))
    assert ev.criterion_f(verified).verdict == ev.VERDICT_PASS
    assert _family(verified, "snapshots").pre_anchor == 1

    working = replace(green, visibility_snapshots=())
    assert ev.criterion_f(working).verdict == ev.VERDICT_PASS
    assert _family(working, "snapshots").matched == 1
    assert valid_pack.snapshot_id != invalid_pack.snapshot_id


def test_valid_older_pack_claim_only_supplies_history_with_consistent_success_contract():
    _example, quote = _capture_fixture()
    scan = ScanRunRecord(
        600, _dt(-7, 6), "prior-capture-target", COMMIT, hashlib.sha256(b"prior-capture-target").hexdigest(),
    )
    scan_terminal = RunEventRecord(
        scan.operation_key, 1, "SUCCEEDED", _dt(-7, 7),
        scan_id=scan.scan_id, code_commit=COMMIT, output_evidence=(scan.scan_key,),
    )
    candidate = CandidateLinkRecord(
        51, scan.scan_id, _dt(-7, 6, 30), COMMIT, candidate_key=quote["candidate_key"], event_start_at=_dt(-2),
    )
    quote = {**quote, "scan_operation_key": scan.operation_key, "observed_at": _dt(-5).isoformat()}
    pack = _snapshot(json.dumps([quote]).encode(), at=_dt(-4), kind="capture_mark_pack")
    mark, close = _quote_rows(quote, candidate, pack)
    receipt = replace(_capture_success((pack,), targets=(candidate,)), occurred_at=_dt(-4, 10))
    green = _green()
    evidence = replace(
        green, run_events=(*green.run_events, scan_terminal, receipt), scan_runs=(*green.scan_runs, scan),
        candidates=(*green.candidates, candidate), snapshots=(pack,), candidate_marks=(mark,),
        price_evidence=(close,),
    )
    assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
    assert _family(evidence, "snapshots").pre_anchor == 1

    wrong_summary = _change_capture_summary(evidence, marks_written=0)
    _assert_reason(wrong_summary, ev.VERDICT_NOT_ASSESSABLE, pack.snapshot_id, "writing period unproven")
    assert _family(wrong_summary, "snapshots").pre_anchor == 0
    missing_pair = replace(evidence, candidate_marks=(), price_evidence=())
    _assert_reason(missing_pair, ev.VERDICT_NOT_ASSESSABLE, pack.snapshot_id, "writing period unproven")
    assert _family(missing_pair, "snapshots").pre_anchor == 0


def _b1_scan_with_cash_and_source():
    green = _green()
    scan = green.scan_runs[0]
    source = _snapshot(b"previously registered scan source", at=_dt(-1))
    candidate = CandidateLinkRecord(905, scan.scan_id, _dt(0, 6, 2), COMMIT,
                                    candidate_key="source-of-entry", snapshot_refs=(source.snapshot_id,))
    entry = EntryRecord(906, candidate.candidate_id, candidate.written_at,
                        stake=Decimal("3.25"), entry_fee=Decimal("0.125"), is_legacy=False)
    debit = BankrollEventRecord(908, "SCAN_ENTRY", entry.written_at, scan_id=scan.scan_id,
                               entry_id=entry.entry_id, event_key="entry:906:cash", amount=Decimal("-3.375"))
    return replace(green, candidates=(*green.candidates, candidate), entries=(entry,), snapshots=(source,),
                   bankroll_events=(debit,), replay_agreements={**green.replay_agreements, "905": True})


@pytest.mark.parametrize("family", ["snapshots", "bankroll_events"])
@pytest.mark.parametrize("proof", ["complete", "exact-pk", "opaque-fingerprint", "partial", "unmapped-pk"])
def test_real_scan_success_requires_its_known_reverse_dependencies(family, proof):
    base = _b1_scan_with_cash_and_source()
    assert ev.criterion_f(base).verdict == ev.VERDICT_PASS
    dependency, = getattr(base, family)
    identity = dependency.snapshot_id if family == "snapshots" else dependency.event_id
    key = dependency.snapshot_id if family == "snapshots" else dependency.event_key
    if proof == "complete":
        view = _complete_view("after-B1-complete", _dt(0, 7), family)
    elif proof in ("exact-pk", "unmapped-pk"):
        view = _inventory("after-B1-exact-lookup", _dt(0, 7), absent=((family, identity),))
        if family == "bankroll_events" and proof == "exact-pk":
            view = replace(view, bankroll_event_keys=((identity, key),))
    elif proof == "opaque-fingerprint":
        view = _complete_view("after-B1-only-fingerprint", _dt(0, 7), family, (dependency,))
    else:
        view = _inventory("after-B1-partial", _dt(0, 7), absent=((family, 9876),))
    evidence = replace(base, **{family: ()}, visibility_snapshots=(view,))
    origin = "candidate 905" if family == "snapshots" else "entry 906"
    if proof in ("complete", "exact-pk") or (proof == "unmapped-pk" and family == "snapshots"):
        _verified_receipt_failure(evidence, origin, key, view.snapshot_id, "absent")
    else:
        _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, origin, key, "projection unavailable", "(R3)")
        assert not ef.reconcile_universe(evidence).writer_failures
    assert _family(evidence, family).matched == 0
    claimed_family = "candidates" if family == "snapshots" else "entries"
    claimed_id = 905 if family == "snapshots" else 906
    assert all(f"row {claimed_id} " not in reason for reason in _family(evidence, claimed_family).disclosed)
    restored = replace(base, visibility_snapshots=(
        _complete_view("after-B1-dependency-loaded", _dt(0, 7), family, (dependency,)),
    ))
    assert ev.criterion_f(restored).verdict == ev.VERDICT_PASS


@pytest.mark.parametrize("scenario", ["two-planning-reads", "first-recheck-is-earlier", "terminal-read-is-earlier"])
def test_b3_attempt_has_one_planning_snapshot_before_every_proved_item_read(scenario):
    base = _settlement_fixture(two_entries=True)
    early = _inventory("P-0900", _dt(5, 9))
    late = _inventory("P-1030", _dt(5, 10, 30))
    first = _inventory("entry-101-1020", _dt(5, 10, 20))
    second = _inventory("V102-1045", _dt(5, 10, 45))
    reads = (
        ef.SettlementReadEvidence("settle-synthetic", 1, 101, "synthetic shared P",
            planning_snapshot_id=early.snapshot_id if scenario == "two-planning-reads" else None,
            recheck_snapshot_id=first.snapshot_id if scenario != "terminal-read-is-earlier" else None,
            terminal_cash_snapshot_id=first.snapshot_id if scenario == "terminal-read-is-earlier" else None),
        ef.SettlementReadEvidence("settle-synthetic", 1, 102, "synthetic shared P",
            planning_snapshot_id=late.snapshot_id, recheck_snapshot_id=second.snapshot_id),
    )
    evidence = replace(base, settlement_reads=reads,
                       visibility_snapshots=(*base.visibility_snapshots, early, late, first, second))
    report = _verified_receipt_failure(evidence, "planning P", late.snapshot_id, "entry 101")
    assert _family(evidence, "settlements").matched == 0
    assert _family(evidence, "bankroll_events").matched == 0
    assert report.writer_failures
    once = replace(evidence, settlement_reads=(reads[0], replace(reads[1], planning_snapshot_id=early.snapshot_id)))
    assert ev.criterion_f(once).verdict == ev.VERDICT_PASS
    assert _family(once, "settlements").matched == _family(once, "bankroll_events").matched == 2
    same_time = replace(late, observed_at=early.observed_at)
    distinct_views = replace(once, settlement_reads=(
        replace(reads[0], planning_snapshot_id=early.snapshot_id), reads[1],
    ), visibility_snapshots=(*base.visibility_snapshots, early, same_time, first, second))
    _verified_receipt_failure(distinct_views, "planning P", early.snapshot_id, late.snapshot_id)


@pytest.mark.parametrize("state", ["opaque", "complete-empty", "complete-unrelated", "exact-pk", "unmapped-pk"])
def test_b3_opening_key_absence_needs_complete_resolved_inventory(state):
    base = _settlement_fixture()
    assert ev.criterion_f(base).verdict == ev.VERDICT_PASS
    opening = base.bankroll_events[0]
    assert opening.event_id == 390
    unrelated = BankrollEventRecord(777, "INITIAL", _dt(-2), event_key="other-cash-key")
    if state in ("exact-pk", "unmapped-pk"):
        planning = _inventory("P-bankroll-lookup", _dt(5, 9), absent=(("bankroll_events", opening.event_id),))
        if state == "exact-pk":
            planning = replace(planning, bankroll_event_keys=((opening.event_id, opening.event_key),))
    else:
        witnessed = {"opaque": (opening,), "complete-empty": (), "complete-unrelated": (unrelated,)}[state]
        planning = _complete_view("P-bankroll-census", _dt(5, 9), "bankroll_events", witnessed)
    reads = (ef.SettlementReadEvidence("settle-synthetic", 1, 101, "synthetic actual P", planning.snapshot_id),)
    prior, = base.visibility_snapshots
    if state != "opaque":
        prior = replace(prior, rows=tuple(row for row in prior.rows if row.row_key != str(opening.event_id)
                                        or row.family != "bankroll_events"))
    evidence = replace(base, bankroll_events=(*_terminal_cash(base), *(() if state != "complete-unrelated" else (unrelated,))),
                       visibility_snapshots=(prior, planning), settlement_reads=reads)
    if state in ("opaque", "unmapped-pk"):
        _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, "entry:101:cash", "(R3)")
        assert not ef.reconcile_universe(evidence).writer_failures
        if state == "opaque":
            with_projection = replace(evidence, bankroll_events=base.bankroll_events)
            assert ev.criterion_f(with_projection).verdict == ev.VERDICT_PASS
    else:
        _verified_receipt_failure(evidence, "entry:101:cash", "absent")
    assert _family(evidence, "settlements").matched == _family(evidence, "bankroll_events").matched == 0


@pytest.mark.parametrize("view_kind", ["P", "V", "terminal"])
def test_b3_missing_selected_outcome_projection_with_attested_read_absence_fails(view_kind):
    base = _settlement_fixture()
    assert ev.criterion_f(base).verdict == ev.VERDICT_PASS
    selected, = base.market_outcomes
    prior, = base.visibility_snapshots
    prior = replace(prior, rows=tuple(row for row in prior.rows if row.family != "market_outcomes"))
    absent = _inventory(f"actual-{view_kind}-outcome", _dt(5, 10), absent=(("market_outcomes", 201),))
    field = {"P": "planning_snapshot_id", "V": "recheck_snapshot_id", "terminal": "terminal_cash_snapshot_id"}[view_kind]
    witness = ef.SettlementReadEvidence("settle-synthetic", 1, 101, "actual read-order witness", **{field: absent.snapshot_id})
    missing = replace(base, market_outcomes=(), visibility_snapshots=(prior, absent), settlement_reads=(witness,))
    _verified_receipt_failure(missing, f"source_outcome_id {selected.outcome_id}", absent.snapshot_id, "absent")
    assert _family(missing, "settlements").matched == _family(missing, "bankroll_events").matched == 0
    unnamed = replace(absent, checked_absences=(("market_outcomes", 999),))
    partial = replace(missing, visibility_snapshots=(prior, unnamed))
    _assert_reason(partial, ev.VERDICT_NOT_ASSESSABLE, "source_outcome_id 201", "projection unavailable")
    assert not ef.reconcile_universe(partial).writer_failures

    two_gaps = replace(missing, entries=())
    _verified_receipt_failure(two_gaps, "source_outcome_id 201", absent.snapshot_id, "absent")
    _assert_reason(two_gaps, ev.VERDICT_FAIL, "entries row 101", "projection unavailable", "(R3)")


def test_b1_missing_or_invalid_required_cash_cannot_borrow_later_scan_retry():
    base = _b1_scan_with_cash_and_source()
    cash, = base.bankroll_events
    event = next(row for row in base.run_events if row.command == "run-scan" and row.scan_id == cash.scan_id)
    lookups = (
        _inventory("first-success-cash-lookup", _dt(0, 6, 20), absent=(("bankroll_events", cash.event_id),)),
        _inventory("cash-visible-before-retry", _dt(0, 6, 30), ("bankroll_events", cash)),
    )
    retry = replace(event, attempt=2, occurred_at=_dt(0, 6, 40))
    evidence = replace(base, run_events=(*base.run_events, retry), visibility_snapshots=lookups)
    _verified_receipt_failure(evidence, "entry 906", "first-success-cash-lookup", "1, 'SUCCEEDED'")
    entries = _family(evidence, "entries")
    assert entries.matched == 1
    assert "2, 'SUCCEEDED'" in entries.disclosed[0] and "1, 'SUCCEEDED'" not in entries.disclosed[0]
    invalid = replace(base, bankroll_events=(replace(cash, reason="INITIAL"),))
    _verified_receipt_failure(invalid, "entry 906", "entry:906:cash", "disagrees with the scan writer")


def test_b3_global_planning_census_cannot_bind_a_different_snapshot_than_item_reads():
    base = _settlement_plan(_settlement_fixture(two_entries=True), (101, 102))
    census, = base.settlement_planning
    other = _inventory("other-identified-P", _dt(5, 9, 15))
    read = ef.SettlementReadEvidence("settle-synthetic", 1, 102, "explicit per-item P", other.snapshot_id)
    evidence = replace(base, settlement_reads=(read,), visibility_snapshots=(*base.visibility_snapshots, other))
    _verified_receipt_failure(evidence, "planning P", "open-entry census", census.snapshot_id, other.snapshot_id)
    confirmed = replace(evidence, settlement_reads=(replace(read, planning_snapshot_id=census.snapshot_id),))
    assert ev.criterion_f(confirmed).verdict == ev.VERDICT_PASS


def test_real_scan_writer_checks_source_before_ensuring_entry_and_opening_cash():
    from polymarket_alpha_lab.papersim.store_psycopg import record_scan

    evidence = _b1_scan_with_cash_and_source()
    scan = evidence.scan_runs[0]
    candidate = evidence.candidates[-1]
    entry, = evidence.entries
    cash, = evidence.bankroll_events
    source, = evidence.snapshots
    submission = store.ScanSubmission(
        scan=store.ScanRunRecord(
            team="football", started_at=scan.started_at, finished_at=_dt(0, 6, 4),
            code_commit=COMMIT, gate_version="fixture", operation_key=scan.operation_key,
        ),
        candidates=(store.CandidateSubmission(
            candidate_key=candidate.candidate_key, conclusion="ACCEPTED", team="football",
            model_name="synthetic", model_version="fixture", evaluated_at=candidate.written_at,
            snapshot_refs=json.dumps([source.snapshot_id]),
            entry=store.EntrySubmission(
                stake=entry.stake, entry_fee=entry.entry_fee, price=Decimal("0.5"), shares=Decimal("6.5"),
                fee_category="sports", tier="standard", entered_at=entry.written_at,
            ),
        ),),
    )

    class InMemoryScanConnection:
        def __init__(self, registered):
            self.registered = registered
            self.queries = []
            self.result = None

        def transaction(self):
            return nullcontext()

        def cursor(self):
            return self

        def execute(self, query, arguments):
            self.queries.append((query, arguments))
            if query.startswith("INSERT INTO papersim.scan_runs"):
                self.result = (scan.scan_id,)
            elif query.startswith("INSERT INTO papersim.candidates"):
                self.result = (candidate.candidate_id,)
            elif query.startswith("INSERT INTO papersim.entries"):
                self.result = (entry.entry_id,)
            elif query.startswith("SELECT content_sha256 FROM papersim.snapshots"):
                self.result = [(source.snapshot_id,)] if self.registered else []
            else:
                self.result = None

        def fetchone(self):
            return self.result

        def fetchall(self):
            return self.result

    connection = InMemoryScanConnection(registered=True)
    written = record_scan(connection, submission)
    assert written["scan_id"] == scan.scan_id
    assert written["candidate_ids"] == {candidate.candidate_key: candidate.candidate_id}
    assert written["bankroll_event_keys"] == (cash.event_key,)
    lookup = next(params for query, params in connection.queries
                  if query.startswith("SELECT content_sha256"))
    assert lookup == ([source.snapshot_id],)
    debit = next(params for query, params in connection.queries
                 if query.startswith("INSERT INTO papersim.bankroll_events"))
    assert debit == (cash.event_key, cash.occurred_at, "USD", Decimal("-3.375"),
                     "SCAN_ENTRY", entry.entry_id, scan.scan_id, None, False)

    missing = InMemoryScanConnection(registered=False)
    with pytest.raises(store.PapersimStoreError, match="papersim_snapshot_ref_missing"):
        record_scan(missing, submission)
    assert not any(query.startswith(("INSERT INTO papersim.candidates", "INSERT INTO papersim.entries"))
                   for query, _params in missing.queries)

    scans = (replace(scan, scan_key=written["scan_key"], finished_at=_dt(0, 6, 4)), *evidence.scan_runs[1:])
    events = tuple(replace(row, output_evidence=(written["scan_key"],))
                   if row.scan_id == scan.scan_id and row.phase == "SUCCEEDED" else row
                   for row in evidence.run_events)
    verified = replace(evidence, scan_runs=scans, run_events=events)
    assert ev.criterion_f(verified).verdict == ev.VERDICT_PASS
    assert _family(verified, "entries").matched == _family(verified, "bankroll_events").matched == 1


@pytest.mark.parametrize("bound_entry", [101, 102])
def test_b3_complete_planning_evidence_is_used_even_when_its_entry_is_omitted(bound_entry):
    full = _settlement_fixture(two_entries=True)
    planning = VisibilitySnapshot(
        "actual-P-for-both-open-entries", _dt(5, 9), ("candidates", "entries", "settlements"),
        tuple(ef.row_fingerprint(family, row)
              for family, rows in (("candidates", full.candidates), ("entries", full.entries)) for row in rows),
    )
    observed = ef.SettlementReadEvidence(
        "settle-synthetic", 1, bound_entry, "actual planning P attestation",
        planning_snapshot_id=planning.snapshot_id,
    )
    full = replace(full, settlement_reads=(observed,), visibility_snapshots=(*full.visibility_snapshots, planning))
    assert ev.criterion_f(full).verdict == ev.VERDICT_PASS
    assert _family(full, "settlements").matched == _family(full, "bankroll_events").matched == 2

    terminal = full.run_events[-1]
    partial_result = replace(
        full, run_events=(*full.run_events[:-1], replace(terminal, output_evidence=terminal.output_evidence[:1])),
        settlements=full.settlements[:1],
        bankroll_events=tuple(cash for cash in full.bankroll_events if cash.event_key != "settle:102"),
    )
    assert any(row.entry_id == 102 for row in partial_result.entries)
    assert any(row.event_key == "entry:102:cash" for row in partial_result.bankroll_events)
    _verified_receipt_failure(partial_result, planning.snapshot_id, "entries [102]", "have no result item")
    assert _family(partial_result, "settlements").matched == 0

    other_attempt = replace(partial_result, settlement_reads=(replace(observed, attempt=2),))
    assert ev.criterion_f(other_attempt).verdict == ev.VERDICT_PASS
    unproved_open_set = replace(
        partial_result,
        visibility_snapshots=(*partial_result.visibility_snapshots[:-1], replace(planning, complete_families=())),
    )
    assert not ef.reconcile_universe(unproved_open_set).writer_failures


@pytest.mark.parametrize("dependency", ["scan-opening", "settle-opening", "settle-terminal"])
def test_complete_cash_inventory_can_exclude_an_opaque_row_with_a_known_unrelated_key(dependency):
    unrelated = BankrollEventRecord(777, "INITIAL", _dt(-2), event_key="unrelated-initial-funding")
    if dependency == "scan-opening":
        base = _b1_scan_with_cash_and_source()
        required = "entry:906:cash"
        kept_cash = ()
        stamp = _dt(0, 7)
    elif dependency == "settle-opening":
        base = _settlement_fixture()
        required = "entry:101:cash"
        kept_cash = _terminal_cash(base)
        stamp = _dt(5, 9)
    else:
        base = _settlement_fixture()
        required = "settle:101"
        kept_cash = base.bankroll_events[:1]
        stamp = _dt(5, 12)
    assert ev.criterion_f(base).verdict == ev.VERDICT_PASS
    expected_row = next(row for row in base.bankroll_events if row.event_key == required)
    prior = tuple(replace(snapshot, rows=tuple(
        reference for reference in snapshot.rows
        if (reference.family, reference.row_key) != ("bankroll_events", str(expected_row.event_id))
    )) for snapshot in base.visibility_snapshots)
    # At P the terminal pair is still absent. At the post-B3 inventory
    # the opening cash row is already visible and stays in the census.
    inventory_cash = (*kept_cash, unrelated) if dependency == "settle-terminal" else (unrelated,)
    view = _complete_view("actual-cash-census-with-opaque-row", stamp, "bankroll_events", inventory_cash)
    read = (ef.SettlementReadEvidence(
        "settle-synthetic", 1, 101, "actual P cash lookup", planning_snapshot_id=view.snapshot_id,
    ),) if dependency == "settle-opening" else ()
    supplied = replace(base, bankroll_events=kept_cash, visibility_snapshots=(*prior, view), settlement_reads=read)
    _assert_reason(supplied, ev.VERDICT_NOT_ASSESSABLE, required, "(R3)")
    assert not ef.reconcile_universe(supplied).writer_failures

    mapped = replace(view, bankroll_event_keys=((unrelated.event_id, unrelated.event_key),))
    resolved_without_amounts = replace(supplied, visibility_snapshots=(*prior, mapped))
    _verified_receipt_failure(resolved_without_amounts, required, "absent", view.snapshot_id)
    resolved_with_projection = replace(supplied, bankroll_events=(*kept_cash, unrelated))
    _verified_receipt_failure(resolved_with_projection, required, "absent", view.snapshot_id)

    found = replace(view, rows=(*view.rows, ef.row_fingerprint("bankroll_events", expected_row)),
                    bankroll_event_keys=((expected_row.event_id, required),))
    still_missing_fields = replace(supplied, visibility_snapshots=(*prior, found))
    _assert_reason(still_missing_fields, ev.VERDICT_NOT_ASSESSABLE, required, "(R3)")
    assert not ef.reconcile_universe(still_missing_fields).writer_failures


def _b3_success_with_item_read(result, *, read_kind=None, bound_to=None, projection="visible"):
    base = _settlement_fixture(two_entries=True)
    first = base.run_events[-1]
    items = first.output_evidence[:1] if result is None else (*first.output_evidence[:1], result)
    event = replace(first, output_evidence=items)
    settled = result is not None and result.startswith("settled:")
    settlements = base.settlements if settled else base.settlements[:1]
    cash = base.bankroll_events if settled else tuple(
        row for row in base.bankroll_events if row.event_key != "settle:102"
    )
    # A sparse projection of the actual P read alone does not establish
    # which entries were open or which decisions were made.
    history, = base.visibility_snapshots
    no_outcome = replace(history, rows=tuple(
        ref for ref in history.rows if (ref.family, ref.row_key) != ("market_outcomes", "202")
    ))
    if result == "pending:102:no_captured_outcome":
        outcomes, history = base.market_outcomes[:1], no_outcome
    else:
        outcomes = base.market_outcomes
    data = replace(base, run_events=(*base.run_events[:-1], event),
                   settlements=settlements, bankroll_events=cash, market_outcomes=outcomes,
                   visibility_snapshots=(history,))
    if read_kind is None:
        return data, None
    field = {
        "P": "planning_snapshot_id", "V": "recheck_snapshot_id",
        "terminal cash": "terminal_cash_snapshot_id",
    }[read_kind]
    stamp = _dt(5, 10, 30) if projection != "future" else _dt(30, 1)
    view = _inventory(f"actual-102-{read_kind}", stamp)
    operation, attempt = bound_to or (event.operation_key, event.attempt)
    read = ef.SettlementReadEvidence(operation, attempt, 102, "synthetic observed B3 read", **{field: view.snapshot_id})
    snapshots = (history,) if projection == "missing" else (*data.visibility_snapshots, view)
    return replace(data, settlement_reads=(read,), visibility_snapshots=snapshots), view


@pytest.mark.parametrize("read_kind,result", [
    ("V", None),
    ("terminal cash", None),
    ("V", "pending:102:no_captured_outcome"),
    ("terminal cash", "pending:102:entry_cash_mismatch"),
])
def test_b3_same_attempt_item_read_requires_its_reachable_success_result(read_kind, result):
    healthy, _ = _b3_success_with_item_read("settled:102:VOIDED_REFUND", read_kind=read_kind)
    assert ev.criterion_f(healthy).verdict == ev.VERDICT_PASS
    assert _family(healthy, "settlements").matched == _family(healthy, "bankroll_events").matched == 2

    evidence, view = _b3_success_with_item_read(result, read_kind=read_kind)
    receipt = evidence.run_events[-1]
    assert not evidence.settlement_planning
    assert all(row.entry_id != 102 for row in evidence.settlements)
    expected = "no result item" if result is None else result
    report = _verified_receipt_failure(
        evidence, repr((receipt.command, receipt.operation_key, receipt.attempt, receipt.phase)),
        "entry 102", view.snapshot_id, expected,
    )
    assert report.unmatched_total == 0
    assert _family(evidence, "settlements").matched == _family(evidence, "bankroll_events").matched == 0


@pytest.mark.parametrize("read_kind,result,bound_to,projection", [
    pytest.param(None, None, None, "visible", id="no-read"),
    pytest.param("P", None, None, "visible", id="P-alone-does-not-prove-open-membership"),
    pytest.param("V", None, ("independent-settle", 1), "visible", id="other-operation"),
    pytest.param("V", None, ("settle-synthetic", 2), "visible", id="other-attempt"),
    pytest.param("V", "pending:102:entry_cash_mismatch", None, "visible", id="correct-cash-mismatch-after-V"),
    pytest.param("P", "pending:102:no_captured_outcome", None, "visible", id="planner-pending-without-V"),
    pytest.param("V", None, None, "missing", id="unavailable-V-cannot-prove-read"),
    pytest.param("terminal cash", None, None, "future", id="future-terminal-view-cannot-prove-read"),
])
def test_b3_only_trusted_matching_item_reads_restrict_success_results(read_kind, result, bound_to, projection):
    evidence, view = _b3_success_with_item_read(result, read_kind=read_kind, bound_to=bound_to,
                                                projection=projection)
    report = ef.reconcile_universe(evidence)
    assert not report.writer_failures
    if projection == "visible":
        assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
    else:
        verdict = ev.criterion_f(evidence)
        assert verdict.verdict in (ev.VERDICT_PASS, ev.VERDICT_NOT_ASSESSABLE)
        assert not any("no result item" in line and view.snapshot_id in line for line in verdict.reasons)
    assert _family(evidence, "settlements").matched == _family(evidence, "bankroll_events").matched == 1


@pytest.mark.parametrize("projection", ["unknown-writer", "previous-writer", "unordered-output"])
def test_b3_item_read_does_not_upgrade_unverified_writer_or_receipt(projection):
    data, view = _b3_success_with_item_read("pending:102:no_captured_outcome", read_kind="V")
    terminal = replace(data.run_events[-1], output_evidence=("pending:102:no_captured_outcome",))
    revisions = data.settlement_writer_revisions
    if projection == "unknown-writer":
        revisions = ()
    elif projection == "previous-writer":
        revisions = (ef.SettlementWriterRevision(COMMIT, "synthetic earlier B3 release", False),)
    else:
        terminal = replace(terminal, output_order_available=False)
    evidence = replace(
        data, run_events=(*data.run_events[:-1], terminal), settlement_writer_revisions=revisions,
        settlements=(), bankroll_events=tuple(row for row in data.bankroll_events
                                               if not row.event_key.startswith("settle:")),
    )
    result = ev.criterion_f(evidence)
    assert result.verdict in (ev.VERDICT_PASS, ev.VERDICT_NOT_ASSESSABLE)
    assert not ef.reconcile_universe(evidence).writer_failures
    assert not any(view.snapshot_id in reason and "cannot produce" in reason for reason in result.reasons)


@pytest.mark.parametrize("phase", ["V", "terminal cash"])
@pytest.mark.parametrize("listed", [True, False], ids=["listed", "omitted"])
@pytest.mark.parametrize("timing", ["before-start", "after-success"])
def test_b3_visible_item_read_obeys_attempt_interval_independent_of_result_membership(phase, listed, timing):
    item = "settled:102:VOIDED_REFUND" if listed else None
    evidence, view = _b3_success_with_item_read(item, read_kind=phase)
    receipt = evidence.run_events[-1]
    started = replace(receipt, phase="STARTED", occurred_at=_dt(5, 9), output_evidence=())
    stamp = _dt(5, 8, 59) if timing == "before-start" else _dt(5, 11, 1)
    evidence = replace(evidence, run_events=(*evidence.run_events[:-1], started, receipt),
                       visibility_snapshots=(*evidence.visibility_snapshots[:-1], replace(view, observed_at=stamp)))
    report = _verified_receipt_failure(
        evidence, repr((receipt.command, receipt.operation_key, receipt.attempt, receipt.phase)),
        "entry 102", view.snapshot_id, "attempt interval",
    )
    assert report.unmatched_total == 0
    assert not any(view.snapshot_id in reason for reason in report.shortfalls)


@pytest.mark.parametrize("phase", ["V", "terminal cash"])
@pytest.mark.parametrize("timing", ["at-start", "at-success", "after-as-of", "missing", "other-attempt"])
def test_b3_item_read_interval_keeps_equal_boundaries_and_unavailable_views_distinct(phase, timing):
    item = "settled:102:VOIDED_REFUND" if timing in ("at-start", "at-success") else None
    projection = "future" if timing == "after-as-of" else "missing" if timing == "missing" else "visible"
    scope = ("settle-synthetic", 2) if timing == "other-attempt" else None
    evidence, view = _b3_success_with_item_read(item, read_kind=phase, bound_to=scope, projection=projection)
    receipt = evidence.run_events[-1]
    started = replace(receipt, phase="STARTED", occurred_at=_dt(5, 9), output_evidence=())
    stamp = {
        "at-start": started.occurred_at, "at-success": receipt.occurred_at,
        "after-as-of": view.observed_at, "missing": view.observed_at,
        "other-attempt": _dt(5, 8),
    }[timing]
    snapshots = evidence.visibility_snapshots
    if timing != "missing":
        snapshots = (*snapshots[:-1], replace(view, observed_at=stamp))
    evidence = replace(evidence, run_events=(*evidence.run_events[:-1], started, receipt), visibility_snapshots=snapshots)
    report = ef.reconcile_universe(evidence)
    assert not report.writer_failures
    if timing in ("after-as-of", "missing"):
        _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, view.snapshot_id, "unavailable", "(R3)")
    else:
        assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
        assert report.unmatched_total == 0
        expected = 1 if timing == "other-attempt" else 2
        assert _family(evidence, "settlements").matched == _family(evidence, "bankroll_events").matched == expected
    if timing in ("at-start", "at-success"):
        omitted = replace(
            evidence, run_events=(*evidence.run_events[:-1], replace(receipt, output_evidence=receipt.output_evidence[:1])),
            settlements=evidence.settlements[:1],
            bankroll_events=tuple(row for row in evidence.bankroll_events if row.event_key != "settle:102"),
        )
        missing_item = _verified_receipt_failure(omitted, "entry 102", view.snapshot_id, "no result item")
        assert not any("attempt interval" in message for message in missing_item.writer_failures)


def test_b3_item_read_interval_of_historical_attempt_does_not_change_current_attempt():
    data, _ = _b3_success_with_item_read("settled:102:VOIDED_REFUND")
    current = data.run_events[-1]
    prior_success = replace(current, operation_key="actual-pre-anchor-attempt", occurred_at=_dt(-2, 10),
                            output_evidence=())
    late = _inventory("old-read-after-old-receipt", _dt(-2, 11))
    read = ef.SettlementReadEvidence(prior_success.operation_key, prior_success.attempt, 102,
                                     "actual older writer read", terminal_cash_snapshot_id=late.snapshot_id)
    data = replace(data, run_events=(*data.run_events, prior_success), settlement_reads=(read,),
                   visibility_snapshots=(*data.visibility_snapshots, late))
    assert ev.criterion_f(data).verdict == ev.VERDICT_PASS
    assert not ef.reconcile_universe(data).writer_failures
    assert _family(data, "settlements").matched == _family(data, "bankroll_events").matched == 2


def _b3_project_success(evidence, projection, *, first_result=None):
    receipt = evidence.run_events[-1]
    first, last = receipt.output_evidence
    if first_result is not None:
        first = first_result
    observed = {
        "complete": (first, last),
        "both": (last, first),  # A summary can lose the original array order.
        "early-only": (first,),
        "late-only": (last,),
        "neither": (),
    }[projection]
    return replace(evidence, run_events=(*evidence.run_events[:-1], replace(
        receipt, output_evidence=observed, output_order_available=projection == "complete",
    )))


def _b3_attempt_read(view, phase, entry_id, *, operation="settle-synthetic", attempt=1):
    field = {"V": "recheck_snapshot_id", "C": "terminal_cash_snapshot_id"}[phase]
    return ef.SettlementReadEvidence(
        operation, attempt, entry_id, "synthetic independently attested item read", **{field: view.snapshot_id},
    )


@pytest.mark.parametrize("earlier,later", [("V", "V"), ("V", "C"), ("C", "V"), ("C", "C")])
@pytest.mark.parametrize("projection", ["complete", "both", "early-only", "late-only", "neither"])
def test_b3_cross_entry_clock_relations_do_not_depend_on_projected_success_items(earlier, later, projection):
    base = _b3_project_success(_settlement_fixture(two_entries=True), projection)
    first = _inventory(f"entry-101-{earlier}", _dt(5, 10, 20))
    second = _inventory(f"entry-102-{later}", _dt(5, 10, 15))
    reads = (_b3_attempt_read(second, later, 102), _b3_attempt_read(first, earlier, 101))
    evidence = replace(base, settlement_reads=reads, visibility_snapshots=(*base.visibility_snapshots, second, first))
    report = _verified_receipt_failure(
        evidence, "cash recheck order reversed", "entry 101", first.snapshot_id, "entry 102", second.snapshot_id,
    )
    if projection != "complete":
        assert any("raw success array order is unavailable" in reason for reason in report.shortfalls)
        _assert_reason(evidence, ev.VERDICT_FAIL, "raw success array order is unavailable", "(R3)")
    for stamp in (_dt(5, 10, 20), _dt(5, 10, 30)):
        control = replace(evidence, visibility_snapshots=(
            *base.visibility_snapshots, first, replace(second, observed_at=stamp),
        ))
        assert not ef.reconcile_universe(control).writer_failures
        expected = ev.VERDICT_PASS if projection == "complete" else ev.VERDICT_NOT_ASSESSABLE
        assert ev.criterion_f(control).verdict == expected


@pytest.mark.parametrize("projection", ["complete", "early-only", "late-only", "neither"])
def test_b3_next_cash_read_cannot_exclude_a_pair_proved_by_an_earlier_terminal_query(projection):
    green = _settlement_fixture(two_entries=True)
    first_settlement = green.settlements[0]
    first_cash = _terminal_cash(green)[0]
    data = _b3_project_success(green, projection)
    first = _inventory("actual-C101", _dt(5, 10, 10))
    second = _inventory("actual-V102", _dt(5, 10, 20),
                        absent=(("settlements", first_settlement.row_key), ("bankroll_events", first_cash.event_id)))
    reads = (_b3_attempt_read(first, "C", 101), _b3_attempt_read(second, "V", 102))
    evidence = replace(data, settlement_reads=reads, visibility_snapshots=(*green.visibility_snapshots, first, second))
    report = _verified_receipt_failure(
        evidence, "entry 101 committed before entry 102 recheck", "actual-V102", "settlement for entry 101", "absent",
    )
    if projection != "complete":
        assert any("raw success array order is unavailable" in reason for reason in report.shortfalls)
        _assert_reason(evidence, ev.VERDICT_FAIL, "raw success array order is unavailable", "(R3)")
    recorded = _inventory(second.snapshot_id, second.observed_at, ("settlements", first_settlement),
                          ("bankroll_events", first_cash))
    consistent = replace(evidence, visibility_snapshots=(*green.visibility_snapshots, first, recorded))
    assert not ef.reconcile_universe(consistent).writer_failures
    assert ev.criterion_f(consistent).verdict == (
        ev.VERDICT_PASS if projection == "complete" else ev.VERDICT_NOT_ASSESSABLE
    )

    # The physical ID of an unprojected settlement cannot be inferred
    # from an entry ID or from an unrelated lookup with the same number.
    opaque_pair = replace(evidence, settlements=green.settlements[1:],
                          bankroll_events=tuple(row for row in green.bankroll_events if row != first_cash))
    report = ef.reconcile_universe(opaque_pair)
    assert not any("committed before entry 102" in reason for reason in report.writer_failures)
    assert ev.criterion_f(opaque_pair).verdict == ev.VERDICT_NOT_ASSESSABLE


@pytest.mark.parametrize("earlier_field,later_field", [
    ("planning_snapshot_id", "recheck_snapshot_id"),
    ("planning_snapshot_id", "terminal_cash_snapshot_id"),
    ("recheck_snapshot_id", "terminal_cash_snapshot_id"),
])
def test_b3_order_checks_reads_within_one_entry_when_success_items_are_unavailable(earlier_field, later_field):
    base = _b3_project_success(_settlement_fixture(two_entries=True), "neither")
    earlier = _inventory(earlier_field, _dt(5, 10, 20))
    later = _inventory(later_field, _dt(5, 10, 15))
    read = ef.SettlementReadEvidence(
        "settle-synthetic", 1, 101, "synthetic actual reads for entry 101",
        **{earlier_field: earlier.snapshot_id, later_field: later.snapshot_id},
    )
    evidence = replace(base, settlement_reads=(read,),
                       visibility_snapshots=(*base.visibility_snapshots, earlier, later))
    _verified_receipt_failure(evidence, "entry 101", "attempt interval", earlier.snapshot_id, later.snapshot_id)
    _assert_reason(evidence, ev.VERDICT_FAIL, "raw success array order is unavailable", "(R3)")
    consistent = replace(evidence, visibility_snapshots=(
        *base.visibility_snapshots, earlier, replace(later, observed_at=earlier.observed_at),
    ))
    assert not ef.reconcile_universe(consistent).writer_failures
    _assert_reason(consistent, ev.VERDICT_NOT_ASSESSABLE, "raw success array order is unavailable", "(R3)")


def test_b3_recheck_followed_by_cash_mismatch_does_not_prove_a_prior_commit():
    green = _settlement_fixture(two_entries=True)
    first = _inventory("only-V101", _dt(5, 10, 10))
    second = _inventory("actual-V102", _dt(5, 10, 20),
                        absent=(("settlements", green.settlements[0].row_key),
                                ("bankroll_events", _terminal_cash(green)[0].event_id)))
    evidence = _b3_project_success(green, "complete", first_result="pending:101:entry_cash_mismatch")
    evidence = replace(evidence, settlements=green.settlements[1:],
                       bankroll_events=tuple(row for row in green.bankroll_events if row.event_key != "settle:101"),
                       settlement_reads=(_b3_attempt_read(first, "V", 101), _b3_attempt_read(second, "V", 102)),
                       visibility_snapshots=(*green.visibility_snapshots, first, second))
    assert ev.criterion_f(evidence).verdict == ev.VERDICT_PASS
    assert not ef.reconcile_universe(evidence).writer_failures
    assert _family(evidence, "settlements").matched == _family(evidence, "bankroll_events").matched == 1


@pytest.mark.parametrize("boundary", ["unseen-middle", "missing", "after-as-of", "different-attempt", "different-operation"])
def test_b3_order_uses_all_numeric_read_ids_without_crossing_attempt_or_report_cutoffs(boundary):
    base = _b3_project_success(_settlement_fixture(two_entries=True), "neither")
    first = _inventory("read-for-entry-2", _dt(5, 10, 30))
    second = _inventory("read-for-entry-10", _dt(30, 1) if boundary == "after-as-of" else _dt(5, 10, 15))
    planning = _inventory("planning-only-middle-entry-5", _dt(5, 9))
    middle = ef.SettlementReadEvidence(
        "settle-synthetic", 1, 5, "same actual P for a middle entry", planning_snapshot_id=planning.snapshot_id,
    )
    reads = (
        _b3_attempt_read(second, "C", 10, attempt=2 if boundary == "different-attempt" else 1,
                         operation="independent-operation" if boundary == "different-operation" else "settle-synthetic"),
        middle, _b3_attempt_read(first, "V", 2),
    )
    evidence = replace(base, settlement_reads=reads, visibility_snapshots=(
        *base.visibility_snapshots, first, planning, *(() if boundary == "missing" else (second,)),
    ))
    if boundary == "unseen-middle":
        _verified_receipt_failure(evidence, "cash recheck order reversed", "entry 2 at", "entry 10 at")
    else:
        assert not ef.reconcile_universe(evidence).writer_failures
        _assert_reason(evidence, ev.VERDICT_NOT_ASSESSABLE, "raw success array order is unavailable")
