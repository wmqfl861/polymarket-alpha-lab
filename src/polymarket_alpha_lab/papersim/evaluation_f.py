"""FX-35 Phase A: pure evidence core for OD-G criterion F.

Signed basis (docs/handoffs/2026-11-19-audit-optimizer/03-fix-list.md
FX-35, lines 138-149 at base fa01be0):

- :144 scheduled-scan continuity — EVERY expected scheduled slot has one
  successful scan receipt, for 30 consecutive full UTC days, failures and
  retries counted honestly;
- :145 one passed daily cash-identity receipt per day;
- :146 a seeded, registered random replay sample, all replays agreeing;
- :147 every in-scope business row attributable to a recorded command
  receipt, zero unlinked — and the boundary sentence: this proves "no
  writes bypassed the recorded commands", NOT "nobody edited the database
  with the owner account";
- :149 the four acceptance cases (complete window passes; one missing
  day, one missing identity receipt, one unlinked row each fail).

Plan basis: v4 design (continue-fx35-plan-v4.md section 3/4/5) as
AMENDED by the v5 addendum (fx35-post86-plan-v5.md sections 2/3):
C1 slot-level coverage replaces v4's day-level rule (one successful slot
never masks another registered slot that is missing or failed);
C2 expected slots come ONLY from the registered schedule descriptor
(never derived from observed receipts — an omitted slot leaves no
receipt), so an unknown schedule or unknown cutover anchor makes F
UNEVALUABLE (NOT_ASSESSABLE with the shortfall stated, never PASS);
C3 PROVEN attribution is exact equality ``PAUSE.observed_at ==
scan_runs.started_at`` with a UNIQUE matching scan row (a shared
timestamp is ambiguous, hence unproven) — every other in-window PAUSE
row, including the recovered-retry pause that earlier reviews accepted,
is unproven, hence unmatched, hence FAIL (fail closed; the signed
zero-unlinked rule dominates); the nearest-STARTED computation survives
only as a reported diagnostic hypothesis that never affects a verdict;
C4 the MO-3 public bootstrap API is a read-only dependency
(tests/test_papersim_evaluation_bootstrap_public.py pins it).

Tri-state (v5 section 3): UNKNOWN (no registered anchor, no registered
schedule, no receipts at all, pending B2/B3 receipt contracts) leads to
NOT_ASSESSABLE with the shortfall stated; KNOWN ORPHAN (an in-window row
that cannot be proven-attributed, an expected registered slot with no
success, a failed identity day, a replay mismatch) leads to FAIL.
Pre-anchor key-less rows are disclosed per R6e and never fail F — F
judges the window only — while in-window unkeyed or ambiguous writes
fail closed.

Slot grid (R4, registered — never inferred from gaps): the descriptor
lists UTC times-of-day; each expected slot owns the half-open interval
[slot_time, next_slot_time); an operation group is attributed to the
slot whose interval contains its earliest receipt ``occurred_at``, with
per-row ``scheduled_for`` used in preference when present (still NULL in
production today).

Review-v5 section 7 P3 observation (recorded for the implementation
stage): under pure interval containment an ad-hoc attended ``run-scan``
landing inside a slot's interval could cover a slot whose scheduled run
was omitted. Once D2 persists ``scheduled_for`` per the registered
schedule, coverage should prefer (or require) ``scheduled_for``-matching
over pure interval containment. Neither C1 nor C2 is weakened by this
nuance: an omitted slot with no receipts still fails, and cross-slot
masking stays impossible.

As-of isolation and slot maturity (independent review fx35-3f0cac37
P2-2): the module is total over the records it is given, AND every
status computation is clamped at the judgeable cutoff ``min(as_of,
window_end)`` (``window_end`` when no as-of is supplied). A terminal
receipt at or after the cutoff never upgrades a group, so a post-window
``SUCCEEDED`` can no longer fill the last slot. A slot whose interval
ends after the cutoff is IMMATURE: its absence is a stated shortfall
(NOT_ASSESSABLE), never a failure — ten good completed days without
future evidence stay NOT_ASSESSABLE. The optional ``FEvidence.as_of``
timestamp also feeds the "n/30 full days elapsed" completeness check
(R1: the evaluation day itself never counts).

Replay sample (R2): sample size N and seed are OWNER-REGISTRATION
PLACEHOLDERS (``evaluation.F_REPLAY_SAMPLE_N`` / ``evaluation
.F_REPLAY_SEED``), never tuned to outcomes and never derived from
observed data. The draw is deterministic over DISTINCT in-window
candidates (one entity repeated twenty times is ONE eligible entity,
never a denominator of twenty — independent review P2-3) whose
``code_commit`` equals the window's latest scan commit, sorted by
``candidate_id``, drawn with ``random.Random(seed)``; older-commit
candidates are counted and listed, never silently skipped; fewer than N
distinct eligible is evidence-absent (NOT_ASSESSABLE), not a failure.
``replay_agreements`` values must be real booleans (None/NaN/anything
else is rejected at construction); an agreement can never be fabricated
from a missing value.

Receipt lineage (independent review P2-1): transitive linkage must
terminate at a receipt — an in-window business row chained through an
unkeyed or unreceipted parent (a pre-anchor NULL-key scan run, a
candidate whose scan has no run-scan receipt) is UNMATCHED. A
``scan_runs`` row keyed to a NON-scan command receipt (same
operation_key, wrong command) does not count as receipt-proven.
In-window ``is_legacy`` flags and in-window ``LEGACY_IMPORT`` bankroll
writes are NOT bypass exemptions (the legacy class is pre-anchor only);
a REVERSAL chain must transitively close at a receipt-proven row —
cycles (including mutual pairs), dead ends and unreceipted terminals
(base-reason/legacy/unlinked, in-window or pre-anchor) all fail closed;
a settle-written market outcome must carry a linked entry.

Degrade, never approximate (R3): families whose receipt contracts are
still pending (B2 mark capture, B3 settle — unmerged at base fa01be0)
are listed in ``FEvidence.pending_contracts`` and make F NOT_ASSESSABLE
with that shortfall; they are never guessed and never exempted beyond
the registered universe. Clearing the list is legitimate only in
clearly-labeled synthetic fixtures modeling the post-merge state.

Phase A scope: PURE functions over plain records. No I/O, no database,
no network, no imports of store/psycopg/scan modules, nothing written.
Production F cannot PASS until the owner registers the R1 cutover day
and the R4 slot grid and the B2/B3 contracts land; PASS is reachable
here only through synthetic fixtures.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from polymarket_alpha_lab.papersim.evaluation import (
    F_REPLAY_SAMPLE_N,
    F_REPLAY_SEED,
    VERDICT_FAIL,
    VERDICT_NOT_ASSESSABLE,
    VERDICT_PASS,
    CriterionResult,
)

# ---------------------------------------------------------------------------
# Frozen signed numbers and mirrored operation vocabulary
# ---------------------------------------------------------------------------

# Signed: 30 consecutive full UTC days (03:144; DELIVERY_PLAN 77.3.2).
# Not a tunable; changing it is a frozen-number change requiring owner
# approval.
F_WINDOW_DAYS = 30

# Mirrored from scan.py SCAN_COMMAND (:83) — mirrored locally because
# this module must not import the scan lane.
SCAN_COMMAND = "run-scan"

# Mirrored from store.py COMMAND_PHASES (:58): terminal phases are
# SUCCEEDED/FAILED; STARTED/PREPARED/RECOVERY_READ rows without a
# terminal receipt are a crash without a terminal receipt (NONTERMINAL).
PHASE_STARTED = "STARTED"
PHASE_PREPARED = "PREPARED"
PHASE_SUCCEEDED = "SUCCEEDED"
PHASE_FAILED = "FAILED"
PHASE_RECOVERY_READ = "RECOVERY_READ"
COMMAND_PHASES = (
    PHASE_STARTED, PHASE_PREPARED, PHASE_SUCCEEDED, PHASE_FAILED,
    PHASE_RECOVERY_READ,
)
TERMINAL_PHASES = (PHASE_SUCCEEDED, PHASE_FAILED)

# Mirrored from store.py BANKROLL_BASE_REASONS (:60) and the settle-cash
# reason tokens (BANKROLL_REASONS :55-57): base-funds rows are a defined
# class, disclosed and never failures; command-linked reasons must carry
# their link in-window.
BANKROLL_BASE_REASONS = ("INITIAL", "ADJUSTMENT", "LEGACY_IMPORT")
BANKROLL_ENTRY_LINKED_REASONS = (
    "SETTLE_NATURAL", "SETTLE_EXIT", "VOIDED_REFUND", "FEE",
)
BANKROLL_REVERSAL_REASON = "REVERSAL"
BANKROLL_SCAN_LINKED_REASONS = ("SCAN_ENTRY",)

QUEUE_CONTROL_PAUSE = "PAUSE"
QUEUE_CONTROL_RESUME = "RESUME"
QUEUE_CONTROL_KINDS = (QUEUE_CONTROL_PAUSE, QUEUE_CONTROL_RESUME)

MARKET_OUTCOME_WRITERS = ("legacy_import", "settle")

# R3 current lane state at base fa01be0: the B2 mark-capture and B3
# settle receipt contracts are unmerged, so settlements (5),
# candidate_marks (7), settle-written market_outcomes (8) and marks (9)
# degrade with a stated shortfall. Phase B wiring supplies the live
# truth; synthetic fixtures may clear the list only when clearly labeled
# as modeling the post-merge state.
PENDING_FAMILY_SETTLEMENTS = "settlements"
PENDING_FAMILY_CANDIDATE_MARKS = "candidate_marks"
PENDING_FAMILY_MARKET_OUTCOMES = "market_outcomes"
PENDING_FAMILY_MARKS = "marks"
KNOWN_PENDING_FAMILIES = (
    PENDING_FAMILY_SETTLEMENTS,
    PENDING_FAMILY_CANDIDATE_MARKS,
    PENDING_FAMILY_MARKET_OUTCOMES,
    PENDING_FAMILY_MARKS,
)
DEFAULT_PENDING_CONTRACTS = KNOWN_PENDING_FAMILIES

# The signed 03:147 boundary sentence, emitted with every evidence-full
# F verdict (never with the legacy evidence-absent stub, which is
# byte-frozen).
BOUNDARY_SENTENCE = (
    "boundary (03:147): zero unlinked rows proves no writes bypassed "
    "the recorded commands; it cannot prove nobody edited the database "
    "with the owner account"
)

GROUP_SUCCEEDED = "SUCCEEDED"
GROUP_FAILED = "FAILED"
GROUP_NONTERMINAL = "NONTERMINAL"

SLOT_COVERED = "covered"
SLOT_ABSENT = "absent"
SLOT_FAILED_ONLY = "failed-only"
SLOT_NONTERMINAL_ONLY = "nonterminal-only"
SLOT_IMMATURE = "immature"


def _require_aware(value: object, name: str) -> None:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


def _require_nonempty_str(value: object, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


# ---------------------------------------------------------------------------
# Input records (plain, synthetic-friendly)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FEvidenceSchedule:
    """R4-registered slot grid: UTC times-of-day, sorted and distinct.

    A documented once-daily schedule legitimately lists one time; the
    evaluator never hardcodes a cadence and never infers one from
    observed gaps (C2). Unknown/invalid descriptor ⇒ F unevaluable.
    """

    slot_times_utc: tuple[time, ...]

    def __post_init__(self) -> None:
        times = tuple(self.slot_times_utc)
        seen: set[time] = set()
        for moment in times:
            if type(moment) is not time or moment.tzinfo is not None:
                raise ValueError(
                    "slot times must be naive UTC time-of-day values"
                )
            if moment in seen:
                raise ValueError(
                    "duplicate slot time in the registered schedule"
                )
            seen.add(moment)
        if not times:
            raise ValueError(
                "slot_times_utc must list at least one UTC time-of-day"
            )
        if times != tuple(sorted(times)):
            raise ValueError("slot times must be sorted ascending")
        object.__setattr__(self, "slot_times_utc", times)


@dataclass(frozen=True)
class RunEventRecord:
    """One ``papersim.command_run_events`` receipt row (attempt phase).

    ``attempt`` rises on retries while ``operation_key`` stays stable (a
    retry may never mint a fresh identity from the current clock). Only
    ``command == SCAN_COMMAND`` rows form operation groups for the slot
    timeline.
    """

    operation_key: str
    attempt: int
    phase: str
    occurred_at: datetime
    command: str = SCAN_COMMAND
    scheduled_for: datetime | None = None
    error_code: str | None = None
    scan_id: int | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.operation_key, "operation_key")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be an integer >= 1")
        if self.phase not in COMMAND_PHASES:
            raise ValueError(f"unknown phase: {self.phase!r}")
        _require_aware(self.occurred_at, "occurred_at")
        _require_nonempty_str(self.command, "command")
        if self.scheduled_for is not None:
            _require_aware(self.scheduled_for, "scheduled_for")


@dataclass(frozen=True)
class ScanRunRecord:
    """One ``papersim.scan_runs`` row: the single persisted scan of an
    operation (``operation_key`` is nullable-UNIQUE; NULL is legal only
    for pre-cutover M1 rows — an in-window NULL is a bypass signal)."""

    scan_id: int
    started_at: datetime
    operation_key: str | None = None
    code_commit: str | None = None

    def __post_init__(self) -> None:
        if type(self.scan_id) is not int:
            raise ValueError("scan_id must be an integer")
        _require_aware(self.started_at, "started_at")
        if self.operation_key is not None:
            _require_nonempty_str(self.operation_key, "operation_key")


def _require_finite_optional(
    value: object, name: str, *, allow_negative: bool,
) -> None:
    """Exact cash receipts (independent review P3): a numeric field is
    either explicitly supplied and finite, or ``None`` (not recorded).
    Float zero is never fabricated as a default, NaN/Inf are illegal,
    and a tolerance may not be negative."""
    if value is None:
        return
    if type(value) is not float and type(value) is not int:
        raise ValueError(f"{name} must be a finite number or None")
    as_float = float(value)
    if not math.isfinite(as_float):
        raise ValueError(f"{name} must be finite (NaN/Inf are illegal)")
    if not allow_negative and as_float < 0.0:
        raise ValueError(f"{name} must be >= 0")


@dataclass(frozen=True)
class BankrollCheckRecord:
    """One ``papersim.bankroll_checks`` identity receipt. ``passed`` is
    the recorded verdict; the difference and tolerance are REPORTED and
    never re-judged here. ``None`` means "not recorded" — a numeric
    zero is never fabricated (independent review P3)."""

    as_of: datetime
    passed: bool
    identity_difference: float | None = None
    tolerance: float | None = None

    def __post_init__(self) -> None:
        _require_aware(self.as_of, "as_of")
        if type(self.passed) is not bool:
            raise ValueError("passed must be a bool")
        _require_finite_optional(
            self.identity_difference, "identity_difference",
            allow_negative=True,
        )
        _require_finite_optional(
            self.tolerance, "tolerance", allow_negative=False,
        )


@dataclass(frozen=True)
class ControlEventRecord:
    """One ``papersim.queue_control_events`` row.

    PAUSE is machine-written by a run-scan attempt; RESUME is a human
    action whose ``reviewed_by`` provenance is NOT NULL by schema."""

    control_kind: str
    observed_at: datetime
    reviewed_by: str | None = None
    queue_id: str | None = None

    def __post_init__(self) -> None:
        if self.control_kind not in QUEUE_CONTROL_KINDS:
            raise ValueError(f"unknown control_kind: {self.control_kind!r}")
        _require_aware(self.observed_at, "observed_at")
        if self.reviewed_by is not None:
            _require_nonempty_str(self.reviewed_by, "reviewed_by")


@dataclass(frozen=True)
class CandidateLinkRecord:
    """Family 2 — one ``papersim.candidates`` row (``written_at`` is
    ``evaluated_at``); linked transitively via ``scan_id`` -> scan_runs."""

    candidate_id: str
    scan_id: int
    written_at: datetime
    code_commit: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.candidate_id, "candidate_id")
        if type(self.scan_id) is not int:
            raise ValueError("scan_id must be an integer")
        _require_aware(self.written_at, "written_at")


@dataclass(frozen=True)
class CandidateChildRecord:
    """Families 3 (candidate_price_evidence) and 7 (candidate_marks):
    linked via ``candidate_id`` -> candidates -> scan_runs."""

    candidate_id: str
    written_at: datetime

    def __post_init__(self) -> None:
        _require_nonempty_str(self.candidate_id, "candidate_id")
        _require_aware(self.written_at, "written_at")


@dataclass(frozen=True)
class EntryRecord:
    """Family 4 — one ``papersim.entries`` row (``written_at`` is
    ``entered_at``); linked via ``candidate_id`` -> candidates."""

    entry_id: str
    candidate_id: str
    written_at: datetime

    def __post_init__(self) -> None:
        _require_nonempty_str(self.entry_id, "entry_id")
        _require_nonempty_str(self.candidate_id, "candidate_id")
        _require_aware(self.written_at, "written_at")


@dataclass(frozen=True)
class EntryChildRecord:
    """Families 5 (settlements, ``written_at`` = ``confirmed_at``) and
    9 (entry-level marks, ``written_at`` = ``marked_at``): linked via
    ``entry_id`` -> entries -> candidates -> scan_runs."""

    entry_id: str
    written_at: datetime

    def __post_init__(self) -> None:
        _require_nonempty_str(self.entry_id, "entry_id")
        _require_aware(self.written_at, "written_at")


@dataclass(frozen=True)
class BankrollEventRecord:
    """Family 6 — one ``papersim.bankroll_events`` row. Base-reason rows
    (INITIAL/ADJUSTMENT/LEGACY_IMPORT) are disclosed per class and never
    failures; SCAN_ENTRY carries ``scan_id``; settle-cash/FEE rows carry
    ``entry_id``; REVERSAL chains via ``reverses_event_id``."""

    event_id: str
    reason: str
    occurred_at: datetime
    scan_id: int | None = None
    entry_id: str | None = None
    reverses_event_id: str | None = None
    is_legacy: bool = False

    def __post_init__(self) -> None:
        _require_nonempty_str(self.event_id, "event_id")
        _require_nonempty_str(self.reason, "reason")
        _require_aware(self.occurred_at, "occurred_at")
        if type(self.is_legacy) is not bool:
            raise ValueError("is_legacy must be a bool")


@dataclass(frozen=True)
class MarketOutcomeRecord:
    """Family 8 — one ``papersim.market_outcomes`` row with its real
    writer class: ``legacy_import`` (the FX-09 pre-anchor import tool;
    an in-window write of this class is a real bypass signal, R6f) or
    ``settle`` (B3 contract, pending). A settle-written row must carry
    its ``entry_id`` link — the writer label alone proves nothing
    (independent review P2-1: no loose writer labels)."""

    writer: str
    observed_at: datetime
    entry_id: str | None = None

    def __post_init__(self) -> None:
        if self.writer not in MARKET_OUTCOME_WRITERS:
            raise ValueError(f"unknown market_outcome writer: {self.writer!r}")
        _require_aware(self.observed_at, "observed_at")
        if self.entry_id is not None:
            _require_nonempty_str(self.entry_id, "entry_id")
        if self.writer == "legacy_import" and self.entry_id is not None:
            raise ValueError(
                "legacy_import market_outcomes carry no entry link"
            )


@dataclass(frozen=True)
class SnapshotRecord:
    """Family 10 — one ``papersim.snapshots`` row; ``cited`` states that
    a candidate's snapshot_refs or a receipt's evidence cites it. An
    in-window uncited snapshot is unmatched (R6c)."""

    snapshot_id: str
    written_at: datetime
    cited: bool = False

    def __post_init__(self) -> None:
        _require_nonempty_str(self.snapshot_id, "snapshot_id")
        _require_aware(self.written_at, "written_at")
        if type(self.cited) is not bool:
            raise ValueError("cited must be a bool")


@dataclass(frozen=True)
class FEvidence:
    """The complete F evidence snapshot (pure records; nothing here is
    read from a database by this module — Phase B wiring loads it inside
    the FX-29 repeatable-read snapshot and hands it over).

    ``window_start`` is the owner-registered D2 cutover UTC calendar day
    (R1); ``schedule`` is the R4-registered slot grid; both must be
    registered before F is evaluable. ``pending_contracts`` defaults to
    the R3 current lane state (B2/B3 unmerged); clearing it is
    legitimate only in clearly-labeled synthetic fixtures.
    """

    window_start: date | None = None
    schedule: FEvidenceSchedule | None = None
    as_of: datetime | None = None
    run_events: tuple[RunEventRecord, ...] = ()
    scan_runs: tuple[ScanRunRecord, ...] = ()
    bankroll_checks: tuple[BankrollCheckRecord, ...] = ()
    control_events: tuple[ControlEventRecord, ...] = ()
    candidates: tuple[CandidateLinkRecord, ...] = ()
    price_evidence: tuple[CandidateChildRecord, ...] = ()
    entries: tuple[EntryRecord, ...] = ()
    settlements: tuple[EntryChildRecord, ...] = ()
    bankroll_events: tuple[BankrollEventRecord, ...] = ()
    candidate_marks: tuple[CandidateChildRecord, ...] = ()
    market_outcomes: tuple[MarketOutcomeRecord, ...] = ()
    marks: tuple[EntryChildRecord, ...] = ()
    snapshots: tuple[SnapshotRecord, ...] = ()
    replay_agreements: Mapping[str, bool] | None = None
    pending_contracts: tuple[str, ...] = DEFAULT_PENDING_CONTRACTS

    def __post_init__(self) -> None:
        if self.window_start is not None and (
            type(self.window_start) is not date
        ):
            raise ValueError("window_start must be a datetime.date")
        if self.schedule is not None and not (
            isinstance(self.schedule, FEvidenceSchedule)
        ):
            raise ValueError("schedule must be an FEvidenceSchedule")
        if self.as_of is not None:
            _require_aware(self.as_of, "as_of")
        for name in (
            "run_events", "scan_runs", "bankroll_checks", "control_events",
            "candidates", "price_evidence", "entries", "settlements",
            "bankroll_events", "candidate_marks", "market_outcomes",
            "marks", "snapshots",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        pending = tuple(self.pending_contracts)
        for family in pending:
            if family not in KNOWN_PENDING_FAMILIES:
                raise ValueError(
                    f"unknown pending-contract family: {family!r}"
                )
        object.__setattr__(self, "pending_contracts", pending)
        if self.replay_agreements is not None:
            if not isinstance(self.replay_agreements, Mapping):
                raise ValueError(
                    "replay_agreements must be a mapping of candidate_id "
                    "to bool"
                )
            for candidate_id, agreement in self.replay_agreements.items():
                _require_nonempty_str(
                    candidate_id, "replay_agreements candidate_id"
                )
                if type(agreement) is not bool:
                    raise ValueError(
                        "replay_agreements values must be bool; "
                        f"candidate {candidate_id!r} carries "
                        f"{agreement!r} (None/non-bool can never be an "
                        "agreement — independent review P2-3)"
                    )


# ---------------------------------------------------------------------------
# Window and registered slot grid
# ---------------------------------------------------------------------------


def window_bounds(
    window_start: date, *, window_days: int = F_WINDOW_DAYS,
) -> tuple[datetime, datetime]:
    """The window is exactly ``window_days`` full UTC calendar days,
    half-open [start, start + days): [00:00Z, 24:00Z) per day."""
    start = datetime.combine(window_start, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=window_days)


def expected_slot_intervals(
    window_start: date,
    schedule: FEvidenceSchedule,
    *,
    window_days: int = F_WINDOW_DAYS,
) -> tuple[tuple[datetime, datetime], ...]:
    """Every expected slot's half-open interval, chronological order.

    Expected slots come ONLY from this registered descriptor (C2). Each
    slot owns [slot_time, next_slot_time); the last slot of a day runs
    to the next day's first slot time; the final expected slot's
    interval is truncated at the window end. Receipts at or after the
    window end are out of window and judged nowhere.
    """
    times = schedule.slot_times_utc
    window_start_dt, window_end = window_bounds(
        window_start, window_days=window_days
    )
    intervals: list[tuple[datetime, datetime]] = []
    for day_offset in range(window_days):
        day = window_start + timedelta(days=day_offset)
        for index, moment in enumerate(times):
            start = datetime.combine(day, moment, tzinfo=timezone.utc)
            if index + 1 < len(times):
                end = datetime.combine(
                    day, times[index + 1], tzinfo=timezone.utc
                )
            else:
                end = datetime.combine(
                    day + timedelta(days=1), times[0], tzinfo=timezone.utc
                )
            intervals.append((start, min(end, window_end)))
    return tuple(intervals)


# ---------------------------------------------------------------------------
# Operation groups and the slot timeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptTimeline:
    """One attempt of one operation group: phases seen, first error
    code, and the attempt-level terminal status."""

    attempt: int
    phases: tuple[str, ...]
    error_code: str | None
    status: str  # SUCCEEDED | FAILED | NONTERMINAL


@dataclass(frozen=True)
class OperationGroup:
    """One operation_key's run-scan receipts with the attempt timeline
    and the attribution stamp (earliest ``scheduled_for`` when any row
    carries one, else earliest ``occurred_at``)."""

    operation_key: str
    events: tuple[RunEventRecord, ...]
    attempts: tuple[AttemptTimeline, ...]
    status: str  # SUCCEEDED | FAILED | NONTERMINAL
    attribution_stamp: datetime


def _attempt_status(events: Sequence[RunEventRecord]) -> str:
    if any(e.phase == PHASE_SUCCEEDED for e in events):
        return GROUP_SUCCEEDED
    if any(e.phase == PHASE_FAILED for e in events):
        return GROUP_FAILED
    return GROUP_NONTERMINAL


def build_operation_groups(
    run_events: Sequence[RunEventRecord],
) -> tuple[OperationGroup, ...]:
    """Group run-scan receipts by operation_key (first-appearance
    order) with per-attempt timelines; non-run-scan receipt rows are
    excluded from the slot timeline (their count is reported by the
    caller-facing timeline builder)."""
    grouped: dict[str, list[RunEventRecord]] = {}
    for event in run_events:
        if event.command != SCAN_COMMAND:
            continue
        grouped.setdefault(event.operation_key, []).append(event)
    groups: list[OperationGroup] = []
    for operation_key, events in grouped.items():
        ordered = tuple(sorted(events, key=lambda e: (e.occurred_at, e.attempt)))
        by_attempt: dict[int, list[RunEventRecord]] = {}
        for event in ordered:
            by_attempt.setdefault(event.attempt, []).append(event)
        attempts = tuple(
            AttemptTimeline(
                attempt=number,
                phases=tuple(
                    dict.fromkeys(e.phase for e in attempt_events)
                ),
                error_code=next(
                    (
                        e.error_code
                        for e in attempt_events
                        if e.phase == PHASE_FAILED and e.error_code
                    ),
                    None,
                ),
                status=_attempt_status(attempt_events),
            )
            for number, attempt_events in sorted(by_attempt.items())
        )
        if any(a.status == GROUP_SUCCEEDED for a in attempts):
            status = GROUP_SUCCEEDED
        elif any(a.status == GROUP_FAILED for a in attempts):
            status = GROUP_FAILED
        else:
            status = GROUP_NONTERMINAL
        scheduled = [
            e.scheduled_for for e in ordered if e.scheduled_for is not None
        ]
        stamp = min(scheduled) if scheduled else min(
            e.occurred_at for e in ordered
        )
        groups.append(
            OperationGroup(
                operation_key=operation_key,
                events=ordered,
                attempts=attempts,
                status=status,
                attribution_stamp=stamp,
            )
        )
    return tuple(groups)


@dataclass(frozen=True)
class SlotCoverage:
    """One expected slot and its attributed groups."""

    index: int
    start: datetime
    end: datetime
    groups: tuple[str, ...]
    covered: bool
    status: str  # covered | absent | failed-only | nonterminal-only | immature


@dataclass(frozen=True)
class SlotTimeline:
    """C1 enforcement grain: per-slot coverage plus honest window
    totals. Retries and failures are counted and NEVER rescue an
    uncovered slot. Slots whose interval ends after the judgeable
    cutoff are IMMATURE (absence there is a shortfall, never a
    failure); FAILED attempts are counted explicitly so a
    FAILED-then-SUCCEEDED retry can never hide the failed attempt
    (independent review P2-2)."""

    slots: tuple[SlotCoverage, ...]
    groups_succeeded: int
    groups_failed: int
    groups_nonterminal: int
    attempts_total: int
    retry_attempts: int
    attempts_failed: int
    attempts_nonterminal: int
    error_codes: tuple[str, ...]
    out_of_window_groups: int
    non_scan_receipt_rows: int
    post_cutoff_receipt_rows: int
    uncovered: tuple[SlotCoverage, ...]
    immature: tuple[SlotCoverage, ...]


def build_slot_timeline(
    run_events: Sequence[RunEventRecord],
    window_start: date,
    schedule: FEvidenceSchedule,
    *,
    window_days: int = F_WINDOW_DAYS,
    judgeable_until: datetime | None = None,
) -> SlotTimeline:
    """Attribute every in-window operation group to exactly one
    expected slot and classify per-slot coverage (C1).

    Attribution: the group's stamp (earliest ``scheduled_for`` when
    present, else earliest ``occurred_at``) must fall in a slot's
    half-open interval. P3 (review-v5 section 7): pure interval
    containment can let an ad-hoc attended run cover a slot whose
    scheduled run was omitted; prefer/require ``scheduled_for``-matching
    once D2 persists it per the registered schedule.

    ``judgeable_until`` (independent review P2-2, default the window
    end): receipts at or after this cutoff are excluded from every
    status computation (counted in ``post_cutoff_receipt_rows``), so a
    post-window terminal success can no longer fill the last slot; a
    slot whose interval ends after the cutoff is IMMATURE — its
    absence is judged nowhere here and reported as a shortfall by the
    caller, never as a failure.
    """
    intervals = expected_slot_intervals(
        window_start, schedule, window_days=window_days
    )
    window_start_dt, window_end = window_bounds(
        window_start, window_days=window_days
    )
    cutoff = (
        judgeable_until if judgeable_until is not None else window_end
    )
    in_scope_events = [
        e for e in run_events if e.occurred_at < cutoff
    ]
    post_cutoff = len(run_events) - len(in_scope_events)
    non_scan = sum(
        1 for e in run_events if e.command != SCAN_COMMAND
    )
    attributed: dict[int, list[OperationGroup]] = {}
    out_of_window = 0
    for group in build_operation_groups(in_scope_events):
        stamp = group.attribution_stamp
        if stamp < window_start_dt or stamp >= window_end:
            out_of_window += 1
            continue
        for index, (start, end) in enumerate(intervals):
            if start <= stamp < end:
                attributed.setdefault(index, []).append(group)
                break
    slots: list[SlotCoverage] = []
    for index, (start, end) in enumerate(intervals):
        groups = attributed.get(index, [])
        keys = tuple(g.operation_key for g in groups)
        statuses = [g.status for g in groups]
        if end > cutoff:
            slot_status, covered = SLOT_IMMATURE, False
        elif GROUP_SUCCEEDED in statuses:
            slot_status, covered = SLOT_COVERED, True
        elif not statuses:
            slot_status, covered = SLOT_ABSENT, False
        elif GROUP_FAILED in statuses:
            slot_status, covered = SLOT_FAILED_ONLY, False
        else:
            slot_status, covered = SLOT_NONTERMINAL_ONLY, False
        slots.append(
            SlotCoverage(
                index=index,
                start=start,
                end=end,
                groups=keys,
                covered=covered,
                status=slot_status,
            )
        )
    in_window_groups = [
        g for groups in attributed.values() for g in groups
    ]
    error_codes = sorted(
        {
            e.error_code
            for g in in_window_groups
            for e in g.events
            if e.phase == PHASE_FAILED and e.error_code
        }
    )
    return SlotTimeline(
        slots=tuple(slots),
        groups_succeeded=sum(
            1 for g in in_window_groups if g.status == GROUP_SUCCEEDED
        ),
        groups_failed=sum(
            1 for g in in_window_groups if g.status == GROUP_FAILED
        ),
        groups_nonterminal=sum(
            1 for g in in_window_groups if g.status == GROUP_NONTERMINAL
        ),
        attempts_total=sum(len(g.attempts) for g in in_window_groups),
        retry_attempts=sum(
            1 for g in in_window_groups for a in g.attempts if a.attempt > 1
        ),
        attempts_failed=sum(
            1 for g in in_window_groups
            for a in g.attempts if a.status == GROUP_FAILED
        ),
        attempts_nonterminal=sum(
            1 for g in in_window_groups
            for a in g.attempts if a.status == GROUP_NONTERMINAL
        ),
        error_codes=tuple(error_codes),
        out_of_window_groups=out_of_window,
        non_scan_receipt_rows=non_scan,
        post_cutoff_receipt_rows=post_cutoff,
        uncovered=tuple(
            s for s in slots if not s.covered and s.status != SLOT_IMMATURE
        ),
        immature=tuple(s for s in slots if s.status == SLOT_IMMATURE),
    )


# ---------------------------------------------------------------------------
# Daily cash identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityDay:
    """One window day's identity verdict: passed receipts count, failed
    receipts count with the reported difference/tolerance (never
    re-judged), or absence. ``mature`` is False while the day has not
    fully elapsed at the judgeable cutoff (an immature day is judged
    nowhere — independent review P2-2)."""

    day: date
    passed_count: int
    failed_count: int
    reported_difference: float | None
    reported_tolerance: float | None
    mature: bool

    @property
    def ok(self) -> bool:
        return self.passed_count >= 1


def _fmt_optional(value: float | None) -> str:
    """Render a possibly-unrecorded cash number (P3): ``None`` is
    "not recorded" — never a fabricated 0.0."""
    if value is None:
        return "not recorded"
    return repr(value)


def _receipt_order_key(check: BankrollCheckRecord) -> tuple:
    """Deterministic total order over one day's receipts, independent
    of input order (independent review P3): latest ``as_of`` first,
    a FAILED receipt outranks a passed one at the same instant, then
    recorded-ness and value of difference/tolerance."""
    difference = check.identity_difference
    tolerance = check.tolerance
    return (
        check.as_of,
        0 if check.passed else 1,
        difference is not None,
        difference if difference is not None else 0.0,
        tolerance is not None,
        tolerance if tolerance is not None else 0.0,
    )


def build_identity_days(
    checks: Sequence[BankrollCheckRecord],
    window_start: date,
    *,
    window_days: int = F_WINDOW_DAYS,
    judgeable_until: datetime | None = None,
) -> tuple[IdentityDay, ...]:
    """03:145: every window day needs >=1 in-day ``passed=true``
    receipt; a day with only failed receipts FAILs with the recorded
    difference and tolerance reported, never re-judged. The reported
    receipt is chosen by a deterministic order (latest as_of, a failed
    receipt preferred at ties), so input order can never change the
    reported difference. Days not fully elapsed at ``judgeable_until``
    (default: the window end) are immature and judged nowhere."""
    days: list[IdentityDay] = []
    for offset in range(window_days):
        day = window_start + timedelta(days=offset)
        day_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        in_day = [
            c for c in checks if day_start <= c.as_of < day_end
        ]
        passed = [c for c in in_day if c.passed]
        failed = [c for c in in_day if not c.passed]
        latest = (
            max(in_day, key=_receipt_order_key) if in_day else None
        )
        days.append(
            IdentityDay(
                day=day,
                passed_count=len(passed),
                failed_count=len(failed),
                reported_difference=(
                    latest.identity_difference if latest else None
                ),
                reported_tolerance=latest.tolerance if latest else None,
                mature=(
                    day_end <= judgeable_until
                    if judgeable_until is not None else True
                ),
            )
        )
    return tuple(days)


# ---------------------------------------------------------------------------
# Seeded replay sample (R2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplaySample:
    """The deterministic R2 draw. ``selected_ids`` is None when no
    trusted commit exists (nothing drawable). ``eligible_ids`` are
    DISTINCT candidate ids — one entity repeated twenty times is one
    eligible entity, never a denominator of twenty (independent review
    P2-3). Older-commit in-window candidates are counted
    (``older_commit_count``), never silently skipped."""

    trusted_commit: str | None
    eligible_ids: tuple[str, ...]
    older_commit_count: int
    selected_ids: tuple[str, ...] | None


def draw_replay_sample(
    evidence: FEvidence,
    *,
    seed: int = F_REPLAY_SEED,
    sample_n: int = F_REPLAY_SAMPLE_N,
) -> ReplaySample:
    """Deterministic draw over DISTINCT in-window candidates whose
    code_commit equals the window's latest scan commit, sorted by
    ``candidate_id``, drawn with ``random.Random(seed)`` (seed/N are
    owner-registration placeholders — never tuned, never derived from
    observed data; the denominator counts entities, never rows)."""
    if evidence.window_start is None:
        raise ValueError("draw_replay_sample requires a registered window_start")
    window_start_dt, window_end = window_bounds(evidence.window_start)
    in_window_scans = [
        s for s in evidence.scan_runs
        if window_start_dt <= s.started_at < window_end and s.code_commit
    ]
    if not in_window_scans:
        return ReplaySample(None, (), 0, None)
    trusted_scan = max(
        in_window_scans, key=lambda s: (s.started_at, s.scan_id)
    )
    trusted = trusted_scan.code_commit
    if trusted is None:
        return ReplaySample(None, (), 0, None)
    in_window_candidates = [
        c for c in evidence.candidates
        if window_start_dt <= c.written_at < window_end
    ]
    eligible = tuple(sorted(
        {
            c.candidate_id for c in in_window_candidates
            if c.code_commit == trusted
        }
    ))
    older = sum(
        1 for c in in_window_candidates
        if c.code_commit is not None and c.code_commit != trusted
    )
    if len(eligible) < sample_n:
        return ReplaySample(
            str(trusted), tuple(eligible), older, None
        )
    rng = random.Random(seed)
    selected = tuple(rng.sample(list(eligible), sample_n))
    return ReplaySample(str(trusted), tuple(eligible), older, selected)


# ---------------------------------------------------------------------------
# Universe reconciliation (v4 section 4 as amended by C3/R6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PauseDiagnostic:
    """Reported-only hypothesis for an unproven PAUSE row (C3): the
    nearest at-or-after STARTED run-scan receipt. NEVER affects the
    verdict and can never produce or permit a PASS."""

    observed_at: datetime
    queue_id: str | None
    hypothesis_operation_key: str | None
    hypothesis_attempt: int | None
    cause: str  # no-equality-match | ambiguous-equality-match


@dataclass(frozen=True)
class FamilyAdjudication:
    """Per-family linkage outcome: in-window/matched/unmatched counts,
    pre-anchor disclosures (R6e — never failures), post-window
    exclusions (F judges the window only) and family notes."""

    family: str
    in_window: int
    matched: int
    unmatched: tuple[str, ...]
    pre_anchor: int
    post_window: int
    disclosed: tuple[str, ...]


@dataclass(frozen=True)
class UniverseReport:
    families: tuple[FamilyAdjudication, ...]
    unmatched_total: int
    pause_diagnostics: tuple[PauseDiagnostic, ...]
    resume_count: int


def _split_window(
    rows: Sequence[tuple[datetime, object]],
    window_start_dt: datetime,
    window_end: datetime,
) -> tuple[list[object], int, int]:
    """Split rows by write timestamp into in-window / pre-anchor /
    post-window buckets (R6e: pre-anchor disclosed, never failed)."""
    in_window: list[object] = []
    pre_anchor = 0
    post_window = 0
    for written_at, row in rows:
        if written_at < window_start_dt:
            pre_anchor += 1
        elif written_at >= window_end:
            post_window += 1
        else:
            in_window.append(row)
    return in_window, pre_anchor, post_window


def _nearest_started_hypothesis(
    events: Sequence[RunEventRecord], observed_at: datetime,
) -> tuple[str | None, int | None]:
    """Diagnostic only (C3): the run-scan STARTED receipt nearest
    at-or-after ``observed_at``. Never a verdict input."""
    started = [
        e for e in events
        if e.command == SCAN_COMMAND and e.phase == PHASE_STARTED
        and e.occurred_at >= observed_at
    ]
    if not started:
        return None, None
    best = min(started, key=lambda e: (e.occurred_at, e.operation_key, e.attempt))
    return best.operation_key, best.attempt


def _reversal_terminal_proven(
    event: BankrollEventRecord,
    *,
    proven_scan_ids: set[int],
    proven_entry_ids: set[str],
) -> bool:
    """Whether a NON-reversal bankroll row terminates a REVERSAL chain
    at a receipt (re-review P2: reversal lineage must close at a proven
    row, exactly like every other family). Base-reason, legacy and
    unknown-reason terminals carry no receipt and do not terminate a
    chain as proven."""
    if event.reason in BANKROLL_SCAN_LINKED_REASONS:
        return (
            event.scan_id is not None
            and event.scan_id in proven_scan_ids
        )
    if event.reason in BANKROLL_ENTRY_LINKED_REASONS:
        return (
            event.entry_id is not None
            and event.entry_id in proven_entry_ids
        )
    return False


def _reversal_lineage_detail(
    row: BankrollEventRecord,
    events_by_id: Mapping[str, BankrollEventRecord],
    *,
    proven_scan_ids: set[int],
    proven_entry_ids: set[str],
) -> str | None:
    """Adjudicate one REVERSAL row's lineage (re-review P2).

    Walk ``reverses_event_id`` to the end: the chain must terminate at
    a receipt-proven row (a SCAN_ENTRY on a proven scan or an
    entry-linked row on a proven entry). A missing target (dead end),
    a cycle (including the two-row mutual pair), or a terminal that is
    itself unreceipted (a base-reason/legacy/unknown row, in-window or
    pre-anchor) leaves the reversal unproven — fail closed."""
    if (
        row.reverses_event_id is None
        or row.reverses_event_id not in events_by_id
    ):
        return (
            "bankroll_events row %r REVERSAL does not chain via "
            "reverses_event_id" % row.event_id
        )
    if row.reverses_event_id == row.event_id:
        return (
            "bankroll_events row %r REVERSAL chains to itself: "
            "a self-reversal proves nothing" % row.event_id
        )
    current = row
    visited = {row.event_id}
    while True:
        target = events_by_id.get(current.reverses_event_id)
        if target is None:
            return (
                "bankroll_events row %r REVERSAL chain dead-ends at "
                "unknown event %r" % (row.event_id, current.reverses_event_id)
            )
        if target.event_id in visited:
            return (
                "bankroll_events row %r REVERSAL chain cycles at %r: a "
                "reversal cycle never terminates at a receipt"
                % (row.event_id, target.event_id)
            )
        visited.add(target.event_id)
        if target.reason == BANKROLL_REVERSAL_REASON:
            current = target
            continue
        if _reversal_terminal_proven(
            target,
            proven_scan_ids=proven_scan_ids,
            proven_entry_ids=proven_entry_ids,
        ):
            return None  # chain closes at a receipt-proven row
        return (
            "bankroll_events row %r REVERSAL chain terminates at %r "
            "which is not receipt-proven (base-reason/legacy/unlinked "
            "terminal): reversal lineage must close at a receipt"
            % (row.event_id, target.event_id)
        )


def reconcile_universe(
    evidence: FEvidence,
    *,
    window_days: int = F_WINDOW_DAYS,
) -> UniverseReport:
    """Adjudicate the complete signed business-surface universe
    (03:147: unmatched must be 0), with family 11 amended per C3 and
    families 5/7/8/9 degraded per R3 (their pending status is a
    NOT_ASSESSABLE shortfall in evaluate_f_evidence, never an exemption
    here). Optimizer registry tables are out of scope by registration
    (R6a); shadow_predictions is conditionally out of scope while MO-7
    is unmerged (R6a).
    """
    assert evidence.window_start is not None
    window_start_dt, window_end = window_bounds(
        evidence.window_start, window_days=window_days
    )
    # Receipt-proven lineage (independent review P2-1): a scan run is
    # proven only when its operation_key belongs to a RUN-SCAN command
    # receipt (the same key on a non-scan command receipt is a wrong
    # command, not a proof); business rows are proven only through
    # parents that terminate at such a receipt.
    scan_receipt_keys = {
        e.operation_key for e in evidence.run_events
        if e.command == SCAN_COMMAND
    }
    proven_scan_ids = {
        s.scan_id for s in evidence.scan_runs
        if s.operation_key is not None
        and s.operation_key in scan_receipt_keys
    }
    all_scan_ids = {s.scan_id for s in evidence.scan_runs}
    proven_candidate_ids = {
        c.candidate_id for c in evidence.candidates
        if c.scan_id in proven_scan_ids
    }
    all_candidate_ids = {c.candidate_id for c in evidence.candidates}
    proven_entry_ids = {
        e.entry_id for e in evidence.entries
        if e.candidate_id in proven_candidate_ids
    }
    all_entry_ids = {e.entry_id for e in evidence.entries}
    bankroll_events_by_id = {
        b.event_id: b for b in evidence.bankroll_events
    }

    families: list[FamilyAdjudication] = []

    def adjudicate(
        family: str,
        rows_with_time: Sequence[tuple[datetime, object]],
        unmatched_detail,
        disclosed: Sequence[str] = (),
    ) -> None:
        in_window, pre_anchor, post_window = _split_window(
            rows_with_time, window_start_dt, window_end
        )
        unmatched = [
            detail for row in in_window
            if (detail := unmatched_detail(row)) is not None
        ]
        families.append(
            FamilyAdjudication(
                family=family,
                in_window=len(in_window),
                matched=len(in_window) - len(unmatched),
                unmatched=tuple(unmatched),
                pre_anchor=pre_anchor,
                post_window=post_window,
                disclosed=tuple(disclosed),
            )
        )

    # Family 1: scan_runs — operation_key present AND matching a
    # RUN-SCAN command receipt (a key owned by a non-scan command is a
    # wrong-command attribution, never a proof).
    adjudicate(
        "scan_runs",
        [(s.started_at, s) for s in evidence.scan_runs],
        lambda s: (
            "scan_runs row scan_id=%d in-window has NULL operation_key "
            "(nullable-unique exists for pre-cutover M1 rows only; an "
            "in-window NULL is a bypass signal)" % s.scan_id
            if s.operation_key is None
            else (
                "scan_runs row scan_id=%d operation_key=%r matches no "
                "run-scan command_run_events receipt (wrong command or "
                "no receipt)" % (s.scan_id, s.operation_key)
                if s.operation_key not in scan_receipt_keys else None
            )
        ),
    )

    # Family 2: candidates — transitive via scan_id -> #1; the chain
    # must terminate at a receipt-proven scan run.
    adjudicate(
        "candidates",
        [(c.written_at, c) for c in evidence.candidates],
        lambda c: (
            "candidates row %r references unknown scan_id=%d"
            % (c.candidate_id, c.scan_id)
            if c.scan_id not in all_scan_ids
            else (
                "candidates row %r references scan_id=%d whose scan run "
                "is not receipt-proven (unkeyed or unreceipted writer)"
                % (c.candidate_id, c.scan_id)
                if c.scan_id not in proven_scan_ids else None
            )
        ),
    )

    # Families 3 and 7: candidate_id -> #2 (7 also R3-pending); the
    # parent candidate must itself be receipt-proven.
    for family, rows in (
        ("candidate_price_evidence", evidence.price_evidence),
        ("candidate_marks", evidence.candidate_marks),
    ):
        adjudicate(
            family,
            [(r.written_at, r) for r in rows],
            lambda r: (
                "%s row references unknown candidate_id=%r"
                % (family, r.candidate_id)
                if r.candidate_id not in all_candidate_ids
                else (
                    "%s row references candidate_id=%r whose candidate "
                    "is not receipt-proven" % (family, r.candidate_id)
                    if r.candidate_id not in proven_candidate_ids else None
                )
            ),
        )

    # Family 4: entries — candidate_id -> #2 (receipt-proven parent).
    adjudicate(
        "entries",
        [(r.written_at, r) for r in evidence.entries],
        lambda r: (
            "entries row %r references unknown candidate_id=%r"
            % (r.entry_id, r.candidate_id)
            if r.candidate_id not in all_candidate_ids
            else (
                "entries row %r references candidate_id=%r whose "
                "candidate is not receipt-proven (unkeyed or unreceipted "
                "historical parent)" % (r.entry_id, r.candidate_id)
                if r.candidate_id not in proven_candidate_ids else None
            )
        ),
    )

    # Families 5 and 9: entry_id -> #4 (both R3-pending; receipt-proven
    # parent).
    for family, rows in (
        ("settlements", evidence.settlements),
        ("marks", evidence.marks),
    ):
        adjudicate(
            family,
            [(r.written_at, r) for r in rows],
            lambda r: (
                "%s row references unknown entry_id=%r" % (family, r.entry_id)
                if r.entry_id not in all_entry_ids
                else (
                    "%s row references entry_id=%r whose entry is not "
                    "receipt-proven" % (family, r.entry_id)
                    if r.entry_id not in proven_entry_ids else None
                )
            ),
        )

    # Family 6: bankroll_events — reason classes.
    def _bankroll_detail(row: BankrollEventRecord) -> str | None:
        # The legacy-import class is PRE-ANCHOR only (independent review
        # P2-1): an is_legacy flag or a LEGACY_IMPORT reason on an
        # IN-WINDOW row is never a bypass exemption. Pre-anchor rows are
        # disclosed by the window split and never reach this check.
        if row.reason == "LEGACY_IMPORT":
            return (
                "bankroll_events row %r reason LEGACY_IMPORT in-window: "
                "the legacy import class is pre-anchor only; an in-window "
                "legacy write is a bypass signal (R6f)" % row.event_id
            )
        reason = row.reason
        if reason in BANKROLL_BASE_REASONS:
            return None  # base-funds class: disclosed per class
        if reason in BANKROLL_SCAN_LINKED_REASONS:
            if row.scan_id is None or row.scan_id not in proven_scan_ids:
                return (
                    "bankroll_events row %r reason SCAN_ENTRY lacks a "
                    "receipt-proven scan_id" % row.event_id
                )
            return None
        if reason in BANKROLL_ENTRY_LINKED_REASONS:
            if row.entry_id is None or row.entry_id not in proven_entry_ids:
                return (
                    "bankroll_events row %r reason %s lacks a linked "
                    "entry_id" % (row.event_id, reason)
                )
            return None
        if reason == BANKROLL_REVERSAL_REASON:
            # Re-review P2: the chain must transitively close at a
            # receipt-proven row; cycles, dead ends and unreceipted
            # terminals (in-window or pre-anchor) all fail closed.
            return _reversal_lineage_detail(
                row,
                bankroll_events_by_id,
                proven_scan_ids=proven_scan_ids,
                proven_entry_ids=proven_entry_ids,
            )
        return (
            "bankroll_events row %r carries unknown reason %r"
            % (row.event_id, reason)
        )

    base_counts: dict[str, int] = {}
    legacy_count = 0
    for event in evidence.bankroll_events:
        if event.is_legacy or event.reason == "LEGACY_IMPORT":
            legacy_count += 1
        elif event.reason in BANKROLL_BASE_REASONS:
            base_counts[event.reason] = base_counts.get(event.reason, 0) + 1
    bankroll_disclosed = [
        f"base-reason rows disclosed per class (R6d): "
        + ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(base_counts.items())
        ),
        f"legacy rows disclosed: {legacy_count} (pre-anchor only; "
        "in-window legacy writes fail closed)",
    ]
    adjudicate(
        "bankroll_events",
        [(b.occurred_at, b) for b in evidence.bankroll_events],
        _bankroll_detail,
        bankroll_disclosed,
    )

    # Family 8: market_outcomes — writer classes (R6f); a settle-written
    # row must carry its entry link (no loose writer labels —
    # independent review P2-1).
    adjudicate(
        "market_outcomes",
        [(m.observed_at, m) for m in evidence.market_outcomes],
        lambda m: (
            "market_outcomes row attributable only to the unreceipted "
            "legacy import tool is unmatched in-window (R6f): real "
            "bypass signal"
            if m.writer == "legacy_import"
            else (
                "market_outcomes row writer=settle lacks a linked "
                "entry_id: the writer label alone proves nothing"
                if m.entry_id is None or m.entry_id not in proven_entry_ids
                else None
            )
        ),
    )

    # Family 10: snapshots — citation rule (R6c).
    adjudicate(
        "snapshots",
        [(s.written_at, s) for s in evidence.snapshots],
        lambda s: (
            "snapshots row %r in-window cited by neither a candidate's "
            "snapshot_refs nor a receipt's evidence (R6c)" % s.snapshot_id
            if not s.cited else None
        ),
    )

    # Family 11 (amended per C3): queue_control_events.
    scans_by_started_at: dict[datetime, list[ScanRunRecord]] = {}
    for scan in evidence.scan_runs:
        scans_by_started_at.setdefault(scan.started_at, []).append(scan)
    pause_unmatched: list[str] = []
    pause_diagnostics: list[PauseDiagnostic] = []
    resume_count = 0
    resume_unmatched: list[str] = []
    pre_anchor_control = 0
    post_window_control = 0
    for event in evidence.control_events:
        if event.observed_at < window_start_dt:
            pre_anchor_control += 1
            continue
        if event.observed_at >= window_end:
            post_window_control += 1
            continue
        if event.control_kind == QUEUE_CONTROL_RESUME:
            if event.reviewed_by is None:
                resume_unmatched.append(
                    "RESUME row at %s lacks reviewed_by provenance "
                    "(schema-impossible; fail closed)" % event.observed_at
                )
            else:
                resume_count += 1
            continue
        matches = scans_by_started_at.get(event.observed_at, ())
        if len(matches) == 1:
            continue  # PROVEN: exact equality with a UNIQUE scan row (C3)
        if len(matches) > 1:
            cause = "ambiguous-equality-match"
        else:
            cause = "no-equality-match"
        hypothesis_key, hypothesis_attempt = _nearest_started_hypothesis(
            evidence.run_events, event.observed_at
        )
        pause_diagnostics.append(
            PauseDiagnostic(
                observed_at=event.observed_at,
                queue_id=event.queue_id,
                hypothesis_operation_key=hypothesis_key,
                hypothesis_attempt=hypothesis_attempt,
                cause=cause,
            )
        )
        pause_unmatched.append(
            "PAUSE row at %s queue %r: no PROVEN attribution (%s); "
            "unproven in-window writes fail closed (C3)"
            % (event.observed_at, event.queue_id, cause)
        )
    families.append(
        FamilyAdjudication(
            family="queue_control_events",
            in_window=sum(
                1 for e in evidence.control_events
                if window_start_dt <= e.observed_at < window_end
            ),
            matched=sum(
                1 for e in evidence.control_events
                if window_start_dt <= e.observed_at < window_end
                and e.control_kind == QUEUE_CONTROL_PAUSE
                and len(scans_by_started_at.get(e.observed_at, ())) == 1
            ) + sum(
                1 for e in evidence.control_events
                if window_start_dt <= e.observed_at < window_end
                and e.control_kind == QUEUE_CONTROL_RESUME
                and e.reviewed_by is not None
            ),
            unmatched=tuple(pause_unmatched + resume_unmatched),
            pre_anchor=pre_anchor_control,
            post_window=post_window_control,
            disclosed=(
                f"{resume_count} human RESUME event(s) in window: linked "
                "by reviewed_by provenance, counted and reported, not "
                "failures (R5b)",
            ),
        ),
    )

    unmatched_total = sum(
        len(f.unmatched) for f in families
    )
    return UniverseReport(
        families=tuple(families),
        unmatched_total=unmatched_total,
        pause_diagnostics=tuple(pause_diagnostics),
        resume_count=resume_count,
    )


# ---------------------------------------------------------------------------
# Verdict assembly
# ---------------------------------------------------------------------------


def evaluate_f_evidence(evidence: FEvidence) -> CriterionResult:
    """Derive the criterion F verdict from one evidence snapshot.

    Precedence: the unknown shortfalls (no R1 anchor, no R4 schedule, a
    completely empty store) make F UNEVALUABLE — NOT_ASSESSABLE with
    the shortfall stated, never PASS. A KNOWN registration with unkeyed
    in-window evidence is NOT misclassified as unevaluable (independent
    review P2-1): with anchor and schedule registered the evaluation
    proceeds and in-window orphans FAIL. With a registered window,
    known orphans (an expected MATURE slot with no success per C1, a
    failed mature identity day, an unproven/unlinked in-window row per
    C3/03:147, a replay mismatch) FAIL, and FAIL beats the remaining
    NOT_ASSESSABLE shortfalls (pending R3 contracts, incomplete window
    at as-of, insufficient replay evidence). PASS requires everything
    green and no pending contracts. The signed 03:147 boundary sentence
    is emitted with every evidence-full verdict.
    """
    key = "F"
    shortfalls: list[str] = []
    if evidence.window_start is None:
        shortfalls.append(
            "cutover anchor not registered/supplied (R1): the 30-day "
            "window cannot be defined; production F is unevaluable "
            "until D2 is registered"
        )
    if evidence.schedule is None:
        shortfalls.append(
            "schedule descriptor not registered/supplied (R4): expected "
            "slots come only from the registered grid, never from "
            "observed receipts (C2)"
        )
    has_any_records = any((
        evidence.run_events, evidence.bankroll_checks,
        evidence.scan_runs, evidence.control_events,
        evidence.candidates, evidence.price_evidence,
        evidence.entries, evidence.settlements,
        evidence.bankroll_events, evidence.candidate_marks,
        evidence.market_outcomes, evidence.marks, evidence.snapshots,
    ))
    if not has_any_records:
        shortfalls.append(
            "no run receipts and no bankroll checks at all (empty store "
            "at cutover is UNKNOWN evidence, not a failure)"
        )
    if shortfalls:
        return CriterionResult(
            key, VERDICT_NOT_ASSESSABLE, tuple(shortfalls)
        )

    assert evidence.window_start is not None
    assert evidence.schedule is not None
    _, window_end = window_bounds(evidence.window_start)
    reasons: list[str] = []
    failures: list[str] = []

    # Judgeable cutoff (independent review P2-2): terminal receipts at
    # or after min(as_of, window_end) never upgrade a group; slots and
    # days whose interval ends after the cutoff are immature (absence
    # there is a shortfall, never a failure).
    judgeable_until = (
        min(evidence.as_of, window_end)
        if evidence.as_of is not None else window_end
    )
    if evidence.as_of is not None and evidence.as_of < window_end:
        elapsed = int(
            (evidence.as_of - window_bounds(evidence.window_start)[0])
            .total_seconds() // 86400
        )
        elapsed = max(0, min(elapsed, F_WINDOW_DAYS))
        shortfalls.append(
            f"window incomplete at as-of: {elapsed}/{F_WINDOW_DAYS} full "
            "UTC days elapsed (the evaluation day itself never counts, R1)"
        )
    if evidence.pending_contracts:
        shortfalls.append(
            "pending receipt contracts (R3): families "
            + ", ".join(evidence.pending_contracts)
            + " degrade with shortfall stated, never approximated"
        )

    # Sub-check 1 (03:144): slot-level scheduled-scan continuity (C1/C2).
    timeline = build_slot_timeline(
        evidence.run_events, evidence.window_start, evidence.schedule,
        judgeable_until=judgeable_until,
    )
    if timeline.immature:
        shortfalls.append(
            f"window incomplete at as-of: {len(timeline.immature)} "
            "expected slot(s) not yet mature (interval end after the "
            f"as-of {judgeable_until.isoformat()}); absent receipts "
            "there are shortfalls, never failures"
        )
    for slot in timeline.uncovered:
        failures.append(
            f"expected slot {slot.start.isoformat()} uncovered "
            f"({slot.status}): every registered slot needs >=1 "
            "SUCCEEDED operation group (C1)"
        )
    # Sub-check 3 (03:145): per-day cash identity. Days not fully
    # elapsed at the cutoff are immature and judged nowhere.
    identity_days = build_identity_days(
        evidence.bankroll_checks, evidence.window_start,
        judgeable_until=judgeable_until,
    )
    for day in identity_days:
        if not day.mature or day.ok:
            continue
        if day.failed_count:
            failures.append(
                f"day {day.day.isoformat()} cash identity failed: "
                f"{day.failed_count} receipt(s) with passed=false "
                f"(recorded difference {_fmt_optional(day.reported_difference)} vs "
                f"tolerance {_fmt_optional(day.reported_tolerance)}, "
                "reported not re-judged)"
            )
        else:
            failures.append(
                f"day {day.day.isoformat()} cash identity missing: no "
                "in-day bankroll_checks receipt (03:145)"
            )
    # Sub-check 4 (03:147): zero unlinked business rows.
    universe = reconcile_universe(evidence)
    for family in universe.families:
        for detail in family.unmatched:
            failures.append(f"unlinked row ({family.family}): {detail}")
        for note in family.disclosed:
            reasons.append(f"disclosed ({family.family}): {note}")
    if universe.unmatched_total:
        failures.append(
            f"unmatched business rows: {universe.unmatched_total} > 0 "
            "(03:147 requires zero)"
        )
    for diagnostic in universe.pause_diagnostics:
        reasons.append(
            "diagnostic only (never verdict-affecting): unproven PAUSE "
            f"at {diagnostic.observed_at.isoformat()} "
            f"({diagnostic.cause}); nearest-STARTED hypothesis: "
            f"operation "
            f"{diagnostic.hypothesis_operation_key!r} attempt "
            f"{diagnostic.hypothesis_attempt!r}"
        )
    # Sub-check 2 (03:146): seeded registered replay sample (R2).
    sample = draw_replay_sample(evidence)
    if sample.trusted_commit is None:
        shortfalls.append(
            "replay evidence absent: no in-window scan run carries a "
            "code_commit to trust"
        )
    elif sample.selected_ids is None:
        shortfalls.append(
            f"replay evidence absent: {len(sample.eligible_ids)} "
            f"eligible candidate(s) at trusted commit "
            f"{sample.trusted_commit!r} < registered N "
            f"{F_REPLAY_SAMPLE_N} (NOT_ASSESSABLE, not a failure)"
        )
    else:
        if sample.older_commit_count:
            reasons.append(
                f"{sample.older_commit_count} in-window candidate(s) at "
                "older commits listed and counted, never silently skipped"
            )
        if evidence.replay_agreements is None:
            shortfalls.append(
                f"replay results not supplied for the {len(sample.selected_ids)} "
                "selected candidate(s) (R2)"
            )
        else:
            missing = [
                cid for cid in sample.selected_ids
                if cid not in evidence.replay_agreements
            ]
            mismatched = [
                cid for cid in sample.selected_ids
                if evidence.replay_agreements.get(cid) is False
            ]
            if mismatched:
                failures.append(
                    "replay mismatch (03:146 全部一致): candidate(s) "
                    + ", ".join(sorted(mismatched))
                    + " did not reproduce"
                )
            elif missing:
                shortfalls.append(
                    f"replay results incomplete: {len(missing)} of "
                    f"{len(sample.selected_ids)} selected candidate(s) "
                    "lack a result"
                )
            else:
                reasons.append(
                    f"replay sample: {len(sample.selected_ids)} of "
                    f"{len(sample.eligible_ids)} eligible at trusted "
                    f"commit {sample.trusted_commit!r}, all agreed "
                    "(seeded registered draw)"
                )
    # Honest counts (03:144: failures and retries counted honestly; a
    # FAILED attempt stays counted even when a later attempt SUCCEEDED
    # — independent review P2-2).
    reasons.append(
        f"window totals: {timeline.groups_succeeded} SUCCEEDED / "
        f"{timeline.groups_failed} FAILED / "
        f"{timeline.groups_nonterminal} NONTERMINAL operation groups, "
        f"{timeline.attempts_total} attempts "
        f"({timeline.retry_attempts} retry, "
        f"{timeline.attempts_failed} failed, "
        f"{timeline.attempts_nonterminal} nonterminal), distinct error "
        f"codes {list(timeline.error_codes)}, "
        f"{timeline.non_scan_receipt_rows} non-run-scan receipt row(s), "
        f"{timeline.out_of_window_groups} out-of-window group(s), "
        f"{timeline.post_cutoff_receipt_rows} receipt row(s) at or "
        "after the judgeable cutoff excluded from every status"
    )
    disclosed_pre_anchor = sum(f.pre_anchor for f in universe.families)
    if disclosed_pre_anchor:
        reasons.append(
            f"pre-anchor key-less/legacy rows disclosed per R6e, never "
            f"failing F: {disclosed_pre_anchor} row(s) before the "
            "registered cutover"
        )
    reasons.append(BOUNDARY_SENTENCE)

    if failures:
        return CriterionResult(key, VERDICT_FAIL, tuple(failures + reasons))
    if shortfalls:
        return CriterionResult(
            key, VERDICT_NOT_ASSESSABLE, tuple(shortfalls + reasons)
        )
    return CriterionResult(key, VERDICT_PASS, tuple(reasons))
