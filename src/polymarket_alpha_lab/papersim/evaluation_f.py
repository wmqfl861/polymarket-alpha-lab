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

F applies the signed 30-day cutoff to receipts and confirms business-row
lineage from the report's consistent snapshot. For append-only rows,
command evidence can establish an association, while an existing
visibility inventory can bound insertion independently of the row's
business timestamp. Period and writer shortfalls remain explicit (R3/R6e).
Phase B supplies the underlying consistent read and replay outcomes.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType

from polymarket_alpha_lab.papersim import fees, ledger, settle, store
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

# The Phase A draw over lexically sorted, normalized candidate IDs.
F_REPLAY_RULE_VERSION = "sorted-id-python-random-sample-v1"

# Mirrored from scan.py SCAN_COMMAND (:83) — mirrored locally because
# this module must not import the scan lane.
SCAN_COMMAND = "run-scan"
CAPTURE_COMMAND = "capture-marks"
SETTLE_COMMAND = "settle"

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
# reason tokens (BANKROLL_REASONS :55-57). Base funds are disclosed;
# an independently established in-window legacy import remains a bypass.
BANKROLL_BASE_REASONS = ("INITIAL", "ADJUSTMENT", "LEGACY_IMPORT")
BANKROLL_ENTRY_LINKED_REASONS = (
    "SETTLE_NATURAL", "SETTLE_EXIT", "VOIDED_REFUND", "FEE",
)
BANKROLL_REVERSAL_REASON = "REVERSAL"

QUEUE_CONTROL_PAUSE = "PAUSE"
QUEUE_CONTROL_RESUME = "RESUME"
QUEUE_CONTROL_KINDS = (QUEUE_CONTROL_PAUSE, QUEUE_CONTROL_RESUME)

MARKET_OUTCOME_WRITERS = ("legacy_import", "settle")

# R3: a caller can report incomplete extraction of these contracts.
# B2/B3 success can prove individual candidate marks and settlements.
# The drivers have no per-item proof of their partial commits or writer
# proofs for entry marks and market outcomes.
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
    if type(value) is not datetime or value.utcoffset() is None:
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
    code_commit: str | None = None
    input_evidence: tuple[str, ...] = ()
    output_evidence: tuple[str, ...] = ()
    run_event_id: int | None = None
    output_order_available: bool = field(default=True, kw_only=True)

    def __post_init__(self) -> None:
        _require_nonempty_str(self.operation_key, "operation_key")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be an integer >= 1")
        if self.phase not in COMMAND_PHASES:
            raise ValueError(f"unknown phase: {self.phase!r}")
        _require_aware(self.occurred_at, "occurred_at")
        _require_nonempty_str(self.command, "command")
        if type(self.output_order_available) is not bool:
            raise ValueError("output_order_available must be a bool")
        if self.scheduled_for is not None:
            _require_aware(self.scheduled_for, "scheduled_for")
        if self.scan_id is not None and type(self.scan_id) is not int:
            raise ValueError("scan_id must be an integer")
        for name in ("input_evidence", "output_evidence"):
            raw = getattr(self, name)
            if isinstance(raw, str) or not isinstance(raw, Sequence):
                raise ValueError(f"{name} must be a sequence of strings")
            values = tuple(raw)
            if any(type(value) is not str for value in values):
                raise ValueError(f"{name} must contain strings")
            object.__setattr__(self, name, values)


@dataclass(frozen=True)
class ScanRunRecord:
    """One ``papersim.scan_runs`` row: the single persisted scan of an
    operation (``operation_key`` is nullable-UNIQUE; NULL is legal only
    for pre-cutover M1 rows — an in-window NULL is a bypass signal)."""

    scan_id: int
    started_at: datetime
    operation_key: str | None = None
    code_commit: str | None = None
    scan_key: str | None = None
    finished_at: datetime | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.scan_id) is not int:
            raise ValueError("scan_id must be an integer")
        _require_aware(self.started_at, "started_at")
        if self.operation_key is not None:
            _require_nonempty_str(self.operation_key, "operation_key")
        if self.scan_key is not None:
            _require_nonempty_str(self.scan_key, "scan_key")
        if self.finished_at is not None:
            _require_aware(self.finished_at, "finished_at")
            if _utc(self.finished_at) < _utc(self.started_at):
                raise ValueError("finished_at cannot precede started_at")


def _require_finite_optional(
    value: object, name: str, *, allow_negative: bool,
) -> None:
    """Exact cash receipts (independent review P3): a numeric field is
    either explicitly supplied and finite, or ``None`` (not recorded).
    Float zero is never fabricated as a default, NaN/Inf are illegal,
    and a tolerance may not be negative."""
    if value is None:
        return
    if type(value) not in (Decimal, float, int):
        raise ValueError(f"{name} must be a finite number or None")
    if _decimal(value) is None:
        raise ValueError(f"{name} must be finite (NaN/Inf are illegal)")
    if not allow_negative and value < 0:
        raise ValueError(f"{name} must be >= 0")


@dataclass(frozen=True)
class BankrollCheckRecord:
    """One ``papersim.bankroll_checks`` identity receipt. ``passed`` is
    the recorded verdict; the difference and tolerance are REPORTED and
    never re-judged here."""

    as_of: datetime
    passed: bool
    identity_difference: Decimal | float | int | None = None
    tolerance: Decimal | float | int | None = None
    check_id: int | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _require_aware(self.as_of, "as_of")
        if type(self.passed) is not bool:
            raise ValueError("passed must be a bool")
        if self.check_id is None:
            # Published receipts have no physical ID and may omit their numbers.
            _require_finite_optional(
                self.identity_difference, "identity_difference", allow_negative=True,
            )
            _require_finite_optional(self.tolerance, "tolerance", allow_negative=False)
            return
        if type(self.check_id) is not int or self.check_id < 1:
            raise ValueError("check_id must be a positive integer")
        for name in ("identity_difference", "tolerance"):
            value = getattr(self, name)
            if type(value) is not Decimal or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
        if self.tolerance < 0:
            raise ValueError("tolerance must be non-negative")


@dataclass(frozen=True)
class ControlEventRecord:
    """One ``papersim.queue_control_events`` row.

    PAUSE is machine-written by a run-scan attempt; RESUME is a human
    action whose ``reviewed_by`` provenance is NOT NULL by schema."""

    control_kind: str
    observed_at: datetime
    reviewed_by: str | None = None
    queue_id: str | None = None
    control_event_id: int | None = None
    scan_id: int | None = None

    def __post_init__(self) -> None:
        if self.control_kind not in QUEUE_CONTROL_KINDS:
            raise ValueError(f"unknown control_kind: {self.control_kind!r}")
        _require_aware(self.observed_at, "observed_at")
        if self.reviewed_by is not None:
            _require_nonempty_str(self.reviewed_by, "reviewed_by")


class _Projection(Enum):
    MISSING = "not projected"


_MISSING = _Projection.MISSING
_MARKET_COLUMNS = settle.MARKET_IDENTITY_COLUMNS


@dataclass(frozen=True)
class CandidateLinkRecord:
    """Family 2 — one ``papersim.candidates`` row (``written_at`` is
    ``evaluated_at``); linked transitively via ``scan_id`` -> scan_runs."""

    candidate_id: int | str
    scan_id: int
    written_at: datetime
    code_commit: str | None = None
    candidate_key: str | None = None
    event_start_at: datetime | None | _Projection = _MISSING
    snapshot_refs: tuple[str, ...] = ()
    content_sha256: str | None = None

    side: str | _Projection = field(default=_MISSING, kw_only=True)
    condition_id: str | None | _Projection = field(default=_MISSING, kw_only=True)
    market_id: str | None | _Projection = field(default=_MISSING, kw_only=True)
    market_slug: str | None | _Projection = field(default=_MISSING, kw_only=True)

    def __post_init__(self) -> None:
        _require_row_id(self.candidate_id, "candidate_id")
        if type(self.scan_id) is not int:
            raise ValueError("scan_id must be an integer")
        _require_aware(self.written_at, "written_at")
        if self.candidate_key is not None:
            _require_nonempty_str(self.candidate_key, "candidate_key")
        if self.event_start_at not in (None, _MISSING):
            _require_aware(self.event_start_at, "event_start_at")
        object.__setattr__(self, "snapshot_refs", tuple(self.snapshot_refs))
        for reference in self.snapshot_refs:
            _require_sha256(reference, "snapshot_refs")
        if self.content_sha256 is not None:
            _require_sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True)
class CandidateChildRecord:
    """Candidate price evidence, candidate marks or a shadow prediction.

    ``written_at`` projects business observation time. The physical row ID
    is ``row_key``; the applicable writer supplies its idempotency key.
    """

    candidate_id: int | str
    written_at: datetime
    row_key: str | int | None = None
    content_sha256: str | None = None
    evidence_role: str | None = None
    sampling_kind: str | None = None
    evidence_key: str | None = None
    candidate_mark_key: str | None = None
    model_probability: Decimal | float | int | None = None
    book_sha256: str | None = None
    market_ref_kind: str | None = None
    market_ref: str | None = None
    token_ref: str | None = None
    side: str | None = None
    bid: Decimal | float | int | None = None
    ask: Decimal | float | int | None = None
    mid: Decimal | float | int | None = None
    last: Decimal | float | int | None = None
    source_kind: str | None = None
    source_ref: str | None = None
    snapshot_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_row_id(self.candidate_id, "candidate_id")
        _require_aware(self.written_at, "written_at")
        if self.content_sha256 is not None:
            _require_sha256(self.content_sha256, "content_sha256")
        if self.snapshot_sha256 is not None:
            _require_sha256(self.snapshot_sha256, "snapshot_sha256")


@dataclass(frozen=True)
class EntryRecord:
    """Family 4 — one ``papersim.entries`` row (``written_at`` is
    ``entered_at``); linked via ``candidate_id`` -> candidates."""

    entry_id: int | str
    candidate_id: int | str
    written_at: datetime
    stake: Decimal | float | int | None = None
    entry_fee: Decimal | float | int | None = None
    content_sha256: str | None = None

    price: Decimal | _Projection = field(default=_MISSING, kw_only=True)
    fee_category: str | _Projection = field(default=_MISSING, kw_only=True)
    fee_source: str | _Projection = field(default=_MISSING, kw_only=True)
    is_legacy: bool | _Projection = field(default=_MISSING, kw_only=True)

    def __post_init__(self) -> None:
        _require_row_id(self.entry_id, "entry_id")
        _require_row_id(self.candidate_id, "candidate_id")
        _require_aware(self.written_at, "written_at")
        if self.content_sha256 is not None:
            _require_sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True)
class EntryChildRecord:
    """A settlement or entry mark, projected by its row ID and entry ID.

    Business time is the recorded confirmation or mark time.
    """

    entry_id: int | str
    written_at: datetime
    row_key: str | int | None = None
    content_sha256: str | None = None
    classification: str | None = None
    is_legacy: bool = False
    source_outcome_id: int | None | _Projection = _MISSING
    proceeds: Decimal | float | int | None = None
    outcome_label: str | None = None
    evidence: str | None = None
    mark_key: str | None = None
    is_close: bool | None = None
    source_kind: str | None = None
    source_ref: str | None = None
    bid: Decimal | float | int | None = None
    ask: Decimal | float | int | None = None
    mid: Decimal | float | int | None = None

    win_loss: str | None | _Projection = field(default=_MISSING, kw_only=True)
    pnl_gross: Decimal | _Projection = field(default=_MISSING, kw_only=True)
    fee_total: Decimal | _Projection = field(default=_MISSING, kw_only=True)
    fee_source: str | _Projection = field(default=_MISSING, kw_only=True)
    pnl_net: Decimal | _Projection = field(default=_MISSING, kw_only=True)
    exit_price: Decimal | None | _Projection = field(default=_MISSING, kw_only=True)
    exit_mark_id: int | None | _Projection = field(default=_MISSING, kw_only=True)
    confirmed_by: str | _Projection = field(default=_MISSING, kw_only=True)

    def __post_init__(self) -> None:
        _require_row_id(self.entry_id, "entry_id")
        _require_aware(self.written_at, "written_at")
        if type(self.is_legacy) is not bool:
            raise ValueError("is_legacy must be a bool")
        if self.content_sha256 is not None:
            _require_sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True)
class BankrollEventRecord:
    """Family 6 — one ``papersim.bankroll_events`` row. Base-reason rows
    (INITIAL/ADJUSTMENT/LEGACY_IMPORT) are disclosed per class and never
    failures; SCAN_ENTRY carries ``scan_id``; settle-cash/FEE rows carry
    ``entry_id``; REVERSAL chains via ``reverses_event_id``."""

    event_id: int | str
    reason: str
    occurred_at: datetime
    scan_id: int | None = None
    entry_id: int | str | None = None
    reverses_event_id: int | str | None = None
    is_legacy: bool = False
    event_key: str | None = None
    amount: Decimal | float | int | None = None
    currency: str = "USD"
    note: str | None = None
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_row_id(self.event_id, "event_id")
        _require_nonempty_str(self.reason, "reason")
        _require_aware(self.occurred_at, "occurred_at")
        if type(self.is_legacy) is not bool:
            raise ValueError("is_legacy must be a bool")
        if self.content_sha256 is not None:
            _require_sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True)
class MarketOutcomeRecord:
    """Family 8 — a projected outcome, including B3's read-side fields.

    A legacy writer label is descriptive. Attribution uses the row ID and
    content together with an independently documented write (R3/R6f).
    """

    writer: str | None
    observed_at: datetime
    entry_id: str | None = None
    outcome_id: int | str | None = field(default=None, kw_only=True)
    outcome: str | None = field(default=None, kw_only=True)
    confirmed_at: datetime | None | _Projection = field(default=_MISSING, kw_only=True)
    evidence_key: str | None = field(default=None, kw_only=True)
    content_sha256: str | None = field(default=None, kw_only=True)

    source_kind: str | _Projection = field(default=_MISSING, kw_only=True)
    condition_id: str | None | _Projection = field(default=_MISSING, kw_only=True)
    market_id: str | None | _Projection = field(default=_MISSING, kw_only=True)
    market_slug: str | None | _Projection = field(default=_MISSING, kw_only=True)

    def __post_init__(self) -> None:
        if self.writer is not None and self.writer not in MARKET_OUTCOME_WRITERS:
            raise ValueError(f"unknown market_outcome writer: {self.writer!r}")
        _require_aware(self.observed_at, "observed_at")
        if self.entry_id is not None:
            _require_nonempty_str(self.entry_id, "entry_id")
            if self.writer == "legacy_import":
                raise ValueError("legacy_import market_outcomes carry no entry link")
        if self.confirmed_at not in (None, _MISSING):
            _require_aware(self.confirmed_at, "confirmed_at")
        if self.outcome_id is not None:
            _require_row_id(self.outcome_id, "outcome_id")
        if self.content_sha256 is not None:
            _require_sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True)
class SnapshotRecord:
    """Family 10 — raw bytes and metadata of an immutable snapshot.

    ``cited`` preserves the old projection shape. Citation is derived
    from the candidate or command evidence (R6c).
    """

    snapshot_id: str
    written_at: datetime
    cited: bool = False
    content: bytes | None = None
    byte_length: int | None = None
    kind: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.snapshot_id, "snapshot_id")
        _require_aware(self.written_at, "written_at")
        if type(self.cited) is not bool:
            raise ValueError("cited must be a bool")
        if self.content is not None and type(self.content) is not bytes:
            raise ValueError("snapshot content must be bytes")
        if self.byte_length is not None and type(self.byte_length) is not int:
            raise ValueError("snapshot byte_length must be an integer")


@dataclass(frozen=True)
class RowFingerprint:
    family: str
    row_key: int | str
    content_sha256: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.family, "family")
        _require_row_id(self.row_key, "row_key")
        object.__setattr__(self, "row_key", str(self.row_key))
        _require_sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True)
class VisibilitySnapshot:
    """An existing inventory with affirmative row matches and known absences.

    ``complete_families`` permits an absence for an omitted key. An inventory
    may instead include individual lookup results in ``checked_absences``.
    ``unreceipted_writes`` lists row fingerprints independently identified by
    the cited source as written through a path without command receipts.
    ``bankroll_event_keys`` carries independently verified primary-key and
    logical-key pairs, which can identify the subject of an exact lookup.
    """

    snapshot_id: str
    observed_at: datetime
    complete_families: tuple[str, ...]
    rows: tuple[RowFingerprint, ...]
    unreceipted_writes: tuple[RowFingerprint, ...] = ()
    checked_absences: tuple[tuple[str, int | str], ...] = ()
    unreceipted_source: str | None = None
    bankroll_event_keys: tuple[tuple[int | str, str], ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        _require_nonempty_str(self.snapshot_id, "snapshot_id")
        _require_aware(self.observed_at, "observed_at")
        object.__setattr__(self, "complete_families", tuple(self.complete_families))
        object.__setattr__(self, "rows", tuple(self.rows))
        keys = [(row.family, row.row_key) for row in self.rows]
        if len(keys) != len(set(keys)):
            raise ValueError("visibility snapshot contains duplicate row keys")
        object.__setattr__(self, "unreceipted_writes", tuple(self.unreceipted_writes))
        if not set(self.unreceipted_writes).issubset(self.rows):
            raise ValueError("unreceipted writes must identify rows in the inventory")
        absences = tuple((family, str(row_id)) for family, row_id in self.checked_absences)
        if set(absences).intersection(keys):
            raise ValueError("the same inventory records a row both present and absent")
        object.__setattr__(self, "checked_absences", absences)
        if self.unreceipted_source is not None:
            _require_nonempty_str(self.unreceipted_source, "unreceipted_source")
        event_keys = []
        for event_id, event_key in self.bankroll_event_keys:
            _require_row_id(event_id, "bankroll event_id")
            _require_nonempty_str(event_key, "bankroll event_key")
            identity = "bankroll_events", str(event_id)
            if identity not in keys and identity not in absences and "bankroll_events" not in self.complete_families:
                raise ValueError("a bankroll event key must be bound to an inventory row or check")
            event_keys.append((str(event_id), event_key))
        object.__setattr__(self, "bankroll_event_keys", tuple(event_keys))


@dataclass(frozen=True)
class SettlementReadEvidence:
    """Identify existing inventories of the actual planning and recheck reads.

    The source establishes that the snapshots describe those reads. An
    observation near the receipt time alone cannot identify either read.
    """

    operation_key: str
    attempt: int
    entry_id: int
    source: str
    planning_snapshot_id: str | None = None
    recheck_snapshot_id: str | None = None
    terminal_cash_snapshot_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _require_nonempty_str(self.operation_key, "operation_key")
        _require_nonempty_str(self.source, "settlement read source")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be an integer >= 1")
        if type(self.entry_id) is not int or self.entry_id < 1:
            raise ValueError("entry_id must be a positive integer")
        for name in ("planning_snapshot_id", "recheck_snapshot_id", "terminal_cash_snapshot_id"):
            value = getattr(self, name)
            if value is not None:
                _require_nonempty_str(value, name)


@dataclass(frozen=True)
class SettlementPlanningEvidence:
    """The open-entry IDs from the actual P query, with their source.

    A partial extract can establish listed members and separately checked
    nonmembers. Only the complete query can exclude all unlisted IDs.
    """

    operation_key: str
    attempt: int
    snapshot_id: str
    source: str
    open_entry_ids: tuple[int, ...]
    checked_excluded_ids: tuple[int, ...] = ()
    complete: bool = field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        for name in ("operation_key", "snapshot_id", "source"):
            _require_nonempty_str(getattr(self, name), name)
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be an integer >= 1")
        if type(self.complete) is not bool:
            raise ValueError("complete must be a bool")
        for name in ("open_entry_ids", "checked_excluded_ids"):
            values = tuple(getattr(self, name))
            if any(type(entry_id) is not int or entry_id < 1 for entry_id in values):
                raise ValueError(f"{name} must contain positive integer IDs")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicate entry IDs")
            object.__setattr__(self, name, values)
        if set(self.open_entry_ids).intersection(self.checked_excluded_ids):
            raise ValueError("the same P query records an entry both open and excluded")


@dataclass(frozen=True)
class SettlementWriterRevision:
    """Writer behavior established for a receipt's deployed code commit."""

    code_commit: str
    source: str
    booked_fees: bool

    def __post_init__(self) -> None:
        if not _is_sha1(self.code_commit):
            raise ValueError("settlement writer code_commit must be a commit SHA-1")
        _require_nonempty_str(self.source, "settlement writer source")
        if type(self.booked_fees) is not bool:
            raise ValueError("booked_fees must be a bool")


class _ReplayOmitted(Enum):
    VALUE = "omitted replay argument"


@dataclass(frozen=True)
class FReplayRegistration:
    """The R2 parameters and digest of their prior approval supplied as evidence.

    The replay resolver checks the record so invalid evidence can be reported
    alongside independently established failures.
    """

    sample_n: int
    seed: int
    rule_version: str
    source_sha256: str


@dataclass(frozen=True)
class FEvidence:
    """The complete F evidence snapshot (pure records; nothing here is
    read from a database by this module — Phase B wiring loads it inside
    the FX-29 repeatable-read snapshot and hands it over).

    ``window_start`` is the first registered complete UTC day after the D2
    cutover (R1). ``schedule`` supplies the registered R4 grid.
    ``replay_registration`` supplies the R2 draw. Explicit ``None`` records
    missing registration. Omitting it retains the legacy pure-fixture draw.
    ``pending_contracts`` records missing source evidence (R3).
    ``visibility_snapshots`` supplies existing inventories when receipt
    evidence cannot establish the period or could describe idempotent reuse.
    A business timestamp in a row can predate its insertion.
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
    shadow_predictions: tuple[CandidateChildRecord, ...] = field(default=(), kw_only=True)
    visibility_snapshots: tuple[VisibilitySnapshot, ...] = field(default=(), kw_only=True)
    settlement_reads: tuple[SettlementReadEvidence, ...] = field(default=(), kw_only=True)
    settlement_planning: tuple[SettlementPlanningEvidence, ...] = field(default=(), kw_only=True)
    settlement_writer_revisions: tuple[SettlementWriterRevision, ...] = field(default=(), kw_only=True)
    replay_registration: FReplayRegistration | _ReplayOmitted | None = field(
        default=_ReplayOmitted.VALUE, kw_only=True, repr=False,
    )

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
            "marks", "snapshots", "shadow_predictions", "visibility_snapshots",
            "settlement_reads", "settlement_planning", "settlement_writer_revisions",
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
            object.__setattr__(self, "replay_agreements", MappingProxyType(dict(self.replay_agreements)))


def _is_hex(value: object, length: int) -> bool:
    return type(value) is str and len(value) == length and all(
        character in "0123456789abcdef" for character in value
    )


def _is_sha1(value: object) -> bool:
    return _is_hex(value, 40)


def _is_sha256(value: object) -> bool:
    return _is_hex(value, 64)


def _require_sha256(value: object, name: str) -> None:
    if not _is_sha256(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _require_row_id(value: object, name: str) -> None:
    if type(value) is not int:
        _require_nonempty_str(value, name)


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
    command: str = SCAN_COMMAND


def _receipt_id(event: RunEventRecord) -> tuple[str, str, int, str]:
    return event.command, event.operation_key, event.attempt, event.phase


def _receipt_order(event: RunEventRecord) -> tuple:
    return event.occurred_at.astimezone(timezone.utc), _receipt_id(event)


def _visible_events(
    events: Sequence[RunEventRecord], as_of: datetime | None,
) -> tuple[RunEventRecord, ...]:
    visible: dict[tuple, RunEventRecord] = {}
    for event in events:
        if as_of is not None and _utc(event.occurred_at) > _utc(as_of):
            continue
        key = _receipt_id(event)
        if key in visible and visible[key] != event:
            raise ValueError(f"conflicting command receipt {key!r}")
        visible[key] = event
    _unique_rows(visible.values(), key=lambda event: event.run_event_id, label="run_event_id")
    return tuple(sorted(visible.values(), key=_receipt_order))


def _full_window_end(start: datetime, end: datetime, as_of: datetime | None) -> datetime:
    if as_of is None:
        return start
    midnight = datetime.combine(_utc(as_of).date(), time.min, tzinfo=timezone.utc)
    return max(start, min(midnight, end))


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc)


def _outside_settlement_attempt(
    observed: datetime, start: datetime | None, terminal: datetime,
) -> bool:
    return observed > terminal or (start is not None and observed < start)


def build_operation_groups(
    run_events: Sequence[RunEventRecord],
    *,
    as_of: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[OperationGroup, ...]:
    """Aggregate visible receipt phases by command/operation/attempt.

    An earlier STARTED still fixes attribution when a later phase
    crosses the window start. Only phases inside the window contribute
    to the resulting group state.
    """
    grouped: dict[tuple[str, str], list[RunEventRecord]] = defaultdict(list)
    for event in _visible_events(run_events, as_of):
        grouped[event.command, event.operation_key].append(event)
    groups = []
    for (command, operation_key), events in grouped.items():
        # Use the original visible phases for slot attribution. A later
        # phase outside the window cannot supply a new scheduled stamp.
        earlier = [
            event for event in events
            if window_end is None or _utc(event.occurred_at) < window_end
        ]
        if not earlier:
            continue
        scheduled = [_utc(event.scheduled_for) for event in earlier
                     if event.scheduled_for is not None]
        stamp = min(scheduled) if scheduled else _utc(earlier[0].occurred_at)
        counted = tuple(
            event for event in earlier
            if window_start is None or _utc(event.occurred_at) >= window_start
        )
        if not counted:
            continue
        by_attempt: dict[int, list[RunEventRecord]] = defaultdict(list)
        for event in counted:
            by_attempt[event.attempt].append(event)
        attempts = []
        for number, attempt_events in sorted(by_attempt.items()):
            phases = tuple(event.phase for event in attempt_events)
            terminal = set(phases).intersection(TERMINAL_PHASES)
            if len(terminal) > 1:
                raise ValueError(
                    f"attempt {number} has conflicting terminal phases "
                    f"for {command} operation {operation_key!r}"
                )
            error = next(
                (event.error_code for event in attempt_events
                 if event.phase == PHASE_FAILED and event.error_code), None,
            )
            attempts.append(AttemptTimeline(
                number, phases, error, next(iter(terminal), GROUP_NONTERMINAL),
            ))
        statuses = {attempt.status for attempt in attempts}
        status = next(
            status for status in (GROUP_SUCCEEDED, GROUP_FAILED, GROUP_NONTERMINAL)
            if status in statuses
        )
        groups.append(OperationGroup(operation_key, counted, tuple(attempts), status, stamp, command))
    return tuple(groups)


@dataclass(frozen=True)
class SlotCoverage:
    """One expected slot and its attributed groups."""

    index: int
    start: datetime
    end: datetime
    groups: tuple[str, ...]
    covered: bool
    status: str


@dataclass(frozen=True)
class SlotTimeline:
    """Registered slot coverage with group and attempt counts."""

    slots: tuple[SlotCoverage, ...]
    groups_succeeded: int
    groups_failed: int
    groups_nonterminal: int
    attempts_total: int
    retry_attempts: int
    error_codes: tuple[str, ...]
    out_of_window_groups: int
    non_scan_receipt_rows: int
    uncovered: tuple[SlotCoverage, ...]
    attempts_succeeded: int
    attempts_failed: int
    attempts_nonterminal: int
    diagnostic_uncovered: tuple[SlotCoverage, ...]
    other_groups: tuple[OperationGroup, ...]
    post_cutoff_receipt_rows: int
    immature: tuple[SlotCoverage, ...]


def build_slot_timeline(
    run_events: Sequence[RunEventRecord],
    window_start: date,
    schedule: FEvidenceSchedule,
    *,
    window_days: int = F_WINDOW_DAYS,
    as_of: datetime | None = None,
    judgeable_until: datetime | None = None,
) -> SlotTimeline:
    """Attribute each scan operation to one slot of the registered grid.

    Use scheduled_for when available; the original receipt stamp supplies
    the v5 interval fallback. A retry can cover its own operation's slot.
    The terminal phase must occur inside the observation window.
    """
    intervals = expected_slot_intervals(window_start, schedule, window_days=window_days)
    start, end = window_bounds(window_start, window_days=window_days)
    if judgeable_until is not None:
        if as_of is not None:
            raise ValueError("supply either as_of or judgeable_until")
        # The published cutoff excludes its exact endpoint. An explicit
        # report snapshot includes receipts at its instant, up to window end.
        as_of = _utc(judgeable_until)
        events = tuple(event for event in run_events if _utc(event.occurred_at) < as_of)
    else:
        events = run_events
    cutoff = end if as_of is None else min(end, _utc(as_of))
    full_end = end if as_of is None else _full_window_end(start, end, as_of)
    groups = build_operation_groups(
        events, as_of=as_of, window_start=start, window_end=end,
    )
    attributed: dict[int, list[OperationGroup]] = defaultdict(list)
    other_groups = []
    out_of_window = 0
    for group in groups:
        if group.command != SCAN_COMMAND:
            other_groups.append(group)
            continue
        for index, (lower, upper) in enumerate(intervals):
            if lower <= group.attribution_stamp < upper:
                attributed[index].append(group)
                break
        else:
            out_of_window += 1

    slots = []
    for index, (lower, upper) in enumerate(intervals):
        slot_groups = attributed.get(index, ())
        statuses = {group.status for group in slot_groups}
        status = SLOT_ABSENT
        if GROUP_SUCCEEDED in statuses:
            status = SLOT_COVERED
        elif GROUP_FAILED in statuses:
            status = SLOT_FAILED_ONLY
        elif statuses:
            status = SLOT_NONTERMINAL_ONLY
        if upper > cutoff and status != SLOT_COVERED:
            status = SLOT_IMMATURE
        slots.append(SlotCoverage(
            index, lower, upper, tuple(group.operation_key for group in slot_groups),
            status == SLOT_COVERED, status,
        ))
    in_window_groups = [group for members in attributed.values() for group in members]
    attempts = [attempt for group in in_window_groups for attempt in group.attempts]
    group_counts = Counter(group.status for group in in_window_groups)
    attempt_counts = Counter(attempt.status for attempt in attempts)
    ended_uncovered = tuple(slot for slot in slots if not slot.covered and slot.end <= cutoff)
    return SlotTimeline(
        slots=tuple(slots),
        groups_succeeded=group_counts[GROUP_SUCCEEDED],
        groups_failed=group_counts[GROUP_FAILED],
        groups_nonterminal=group_counts[GROUP_NONTERMINAL],
        attempts_total=len(attempts),
        retry_attempts=sum(attempt.attempt > 1 for attempt in attempts),
        error_codes=tuple(sorted({attempt.error_code for attempt in attempts if attempt.error_code})),
        out_of_window_groups=out_of_window,
        non_scan_receipt_rows=sum(len(group.events) for group in other_groups),
        uncovered=tuple(slot for slot in ended_uncovered if slot.start < full_end),
        attempts_succeeded=attempt_counts[GROUP_SUCCEEDED],
        attempts_failed=attempt_counts[GROUP_FAILED],
        attempts_nonterminal=attempt_counts[GROUP_NONTERMINAL],
        diagnostic_uncovered=ended_uncovered,
        other_groups=tuple(other_groups),
        post_cutoff_receipt_rows=sum(
            _utc(event.occurred_at) >= end
            or (as_of is not None and _utc(event.occurred_at) > _utc(as_of))
            or (judgeable_until is not None and _utc(event.occurred_at) == as_of)
            for event in run_events
        ),
        immature=tuple(slot for slot in slots if slot.status == SLOT_IMMATURE),
    )


# ---------------------------------------------------------------------------
# Daily cash identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityDay:
    """One window day's identity verdict: passed receipts count, failed
    receipts count with the reported difference/tolerance (never
    re-judged), or absence."""

    day: date
    passed_count: int
    failed_count: int
    reported_difference: Decimal | float | int | None
    reported_tolerance: Decimal | float | int | None
    mature: bool = True
    check_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.passed_count >= 1


def build_identity_days(
    checks: Sequence[BankrollCheckRecord],
    window_start: date,
    *,
    window_days: int = F_WINDOW_DAYS,
    as_of: datetime | None = None,
    judgeable_until: datetime | None = None,
) -> tuple[IdentityDay, ...]:
    """Build one cash-identity decision for each completed UTC day."""
    start, end = window_bounds(window_start, window_days=window_days)
    if as_of is not None and judgeable_until is not None:
        raise ValueError("supply either as_of or judgeable_until")
    cutoff = as_of if as_of is not None else judgeable_until
    full_end = end if cutoff is None else _full_window_end(start, end, cutoff)
    visible: dict[int, BankrollCheckRecord] = {}
    anonymous = []
    for check in checks:
        if cutoff is not None and _utc(check.as_of) > _utc(cutoff):
            continue
        if check.check_id is None:
            anonymous.append(check)
            continue
        previous = visible.get(check.check_id)
        if previous is not None and (
            previous != check or str(previous.identity_difference) != str(check.identity_difference)
            or str(previous.tolerance) != str(check.tolerance)
        ):
            raise ValueError(f"conflicting bankroll check_id: {check.check_id!r}")
        visible[check.check_id] = check
    by_day: dict[date, list[BankrollCheckRecord]] = defaultdict(list)
    for check in (*visible.values(), *anonymous):
        if start <= _utc(check.as_of) < full_end:
            by_day[_utc(check.as_of).date()].append(check)

    days = []
    day_count = window_days if judgeable_until is not None else (full_end - start).days
    for offset in range(day_count):
        day = window_start + timedelta(days=offset)
        in_day = by_day[day]
        passed = sum(check.passed for check in in_day)
        failed = len(in_day) - passed
        # A mixed day's diagnostic presents the most recent failed
        # receipt. An all-passing day presents the most recent pass.
        receipts = [check for check in in_day if not check.passed] if failed else in_day
        def receipt_order(check):
            difference, tolerance = check.identity_difference, check.tolerance
            return (
                _utc(check.as_of), check.check_id is not None, check.check_id or 0,
                difference is not None, difference or 0, repr(difference),
                tolerance is not None, tolerance or 0, repr(tolerance),
            )

        latest = max(receipts, key=receipt_order, default=None)
        days.append(IdentityDay(
            day, passed, failed,
            latest.identity_difference if latest else None,
            latest.tolerance if latest else None,
            mature=offset < (full_end - start).days,
            check_id=latest.check_id if latest else None,
        ))
    return tuple(days)


# ---------------------------------------------------------------------------
# Seeded replay sample (R2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplaySample:
    """The deterministic R2 draw and its denominator."""

    trusted_commit: str | None
    eligible_ids: tuple[str, ...]
    older_commit_count: int
    selected_ids: tuple[str, ...] | None
    older_commit_ids: tuple[str, ...] = ()
    shortfalls: tuple[str, ...] = ()


def _resolve_replay_parameters(
    evidence: FEvidence,
    *,
    seed: int | _ReplayOmitted = _ReplayOmitted.VALUE,
    sample_n: int | _ReplayOmitted = _ReplayOmitted.VALUE,
) -> tuple[int, int] | str:
    """Return the seed and sample size or the R2 shortfall that prevents drawing."""
    registration = evidence.replay_registration
    if registration is _ReplayOmitted.VALUE:
        return (
            F_REPLAY_SEED if seed is _ReplayOmitted.VALUE else seed,
            F_REPLAY_SAMPLE_N if sample_n is _ReplayOmitted.VALUE else sample_n,
        )
    if registration is None:
        return "replay registration is explicitly missing (R2)"
    if not isinstance(registration, FReplayRegistration):
        return "replay registration has an unrecognized record (R2)"
    if type(registration.sample_n) is not int or registration.sample_n <= 0:
        return "replay registration sample_n must be a positive integer (R2)"
    if type(registration.seed) is not int:
        return "replay registration seed must be an integer (R2)"
    if (
        type(registration.rule_version) is not str
        or registration.rule_version != F_REPLAY_RULE_VERSION
    ):
        return "replay registration rule_version is unrecognized (R2)"
    if not _is_sha256(registration.source_sha256):
        return "replay registration source_sha256 must be a lowercase SHA-256 digest (R2)"
    if seed is not _ReplayOmitted.VALUE and (type(seed) is not int or seed != registration.seed):
        raise ValueError("seed must be an integer matching the replay registration")
    if sample_n is not _ReplayOmitted.VALUE and (
        type(sample_n) is not int or sample_n != registration.sample_n
    ):
        raise ValueError("sample_n must be a positive integer matching the replay registration")
    return registration.seed, registration.sample_n


def draw_replay_sample(
    evidence: FEvidence,
    *,
    seed: int | _ReplayOmitted = _ReplayOmitted.VALUE,
    sample_n: int | _ReplayOmitted = _ReplayOmitted.VALUE,
) -> ReplaySample:
    """Draw without replacement from sorted IDs at the latest scan commit.

    With a valid R2 registration, explicit parameters must agree with it.
    Legacy callers omitting the registration may supply their own parameters.
    """
    parameters = _resolve_replay_parameters(evidence, seed=seed, sample_n=sample_n)
    if isinstance(parameters, str):
        return ReplaySample(None, (), 0, None, shortfalls=(parameters,))
    if evidence.window_start is None:
        raise ValueError("draw_replay_sample requires a registered window_start")
    return _draw_replay_sample(_LedgerEvidence(evidence, F_WINDOW_DAYS), *parameters)


def _draw_replay_sample(ledger: _LedgerEvidence, seed: int, sample_n: int) -> ReplaySample:
    scans = [
        scan for scan in ledger.scans.values()
        if ledger.period("scan_runs", scan) not in ("pre-anchor", "post-window", "not-visible")
        and (
            ledger.start <= _utc(scan.started_at) < ledger.end
            or ledger.scan_has_window_receipt(scan)
            or ledger.period("scan_runs", scan) == "in-window"
        )
    ]
    latest = max(scans, key=lambda scan: (_utc(scan.started_at), scan.scan_id), default=None)
    if latest is None:
        return ReplaySample(None, (), 0, None, shortfalls=("no in-window scan to trust",))
    latest_link = ledger.scan_link(latest)
    if not _is_sha1(latest.code_commit) or latest_link.failure or not latest_link.receipts:
        return ReplaySample(None, (), 0, None, shortfalls=(
            f"latest in-window scan {latest.scan_id} lacks a trusted code_commit or command receipt",
        ))

    eligible = []
    older = []
    shortfalls = []
    for candidate_id, candidate in sorted(ledger.candidates.items()):
        period = ledger.period("candidates", candidate)
        if period in ("pre-anchor", "post-window", "not-visible"):
            continue
        scan = ledger.scans.get(candidate.scan_id)
        link = ledger.candidate_link(candidate)
        if link.failure or link.shortfall:
            shortfalls.append(f"candidate {candidate_id!r} replay lineage: {link.failure or link.shortfall}")
            continue
        if not ledger.in_window(period, link.receipts):
            continue
        if scan is None or not _is_sha1(scan.code_commit) or candidate.code_commit != scan.code_commit:
            shortfalls.append(
                f"candidate {candidate_id!r} replay lineage lacks the parent scan's code_commit"
            )
            continue
        if candidate.code_commit == latest.code_commit:
            eligible.append(candidate_id)
        else:
            older.append(candidate_id)
    selected = tuple(random.Random(seed).sample(eligible, sample_n)) if len(eligible) >= sample_n else None
    return ReplaySample(
        latest.code_commit, tuple(eligible), len(older), selected, tuple(older), tuple(shortfalls),
    )


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
    shortfalls: tuple[str, ...] = ()
    writer_failures: tuple[str, ...] = ()


def _nearest_started_hypothesis(
    events: Sequence[RunEventRecord], observed_at: datetime,
) -> tuple[str | None, int | None]:
    for event in events:
        if event.command == SCAN_COMMAND and event.phase == PHASE_STARTED \
                and _utc(event.occurred_at) >= _utc(observed_at):
            return event.operation_key, event.attempt
    return None, None


def _canonical_value(value: object) -> object:
    if value is _MISSING:
        return None
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    return value


def _row_id(family: str, row: object) -> int | str | None:
    if family == "scan_runs":
        return row.scan_id
    if family == "candidates":
        return row.candidate_id
    if family == "entries":
        return row.entry_id
    if family == "bankroll_events":
        return row.event_id
    if family == "market_outcomes":
        return row.outcome_id
    if family == "snapshots":
        return row.snapshot_id
    if family == "queue_control_events":
        return row.control_event_id
    if family == "command_run_events":
        return row.run_event_id
    if family == "bankroll_checks":
        return row.check_id
    return row.row_key


_FIELDS_ADDED_SINCE_INVENTORY = {
    RunEventRecord: {"output_order_available"},
    ScanRunRecord: {"finished_at"},
    CandidateLinkRecord: {"side", *_MARKET_COLUMNS},
    EntryRecord: {"price", "fee_category", "fee_source", "is_legacy"},
    EntryChildRecord: {
        "win_loss", "pnl_gross", "fee_total", "fee_source", "pnl_net",
        "exit_price", "exit_mark_id", "confirmed_by",
    },
    MarketOutcomeRecord: {"entry_id", "source_kind", *_MARKET_COLUMNS},
}


def row_fingerprint(family: str, row: object) -> RowFingerprint:
    """Fingerprint the projected immutable row for comparison with an inventory.

    Writer labels and deprecated citation flags are outside row content.
    A projection may also include a digest of fields it leaves opaque.
    """
    excluded = {"writer", "cited"} | _FIELDS_ADDED_SINCE_INVENTORY.get(type(row), set())
    values = {
        name: _canonical_value(value)
        for name, value in vars(row).items()
        if name not in excluded
    }
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return RowFingerprint(
        family, _row_id(family, row), hashlib.sha256(payload.encode()).hexdigest(),
    )


def _unique_rows(rows, *, key, label: str) -> dict:
    result = {}
    for row in rows:
        identity = key(row)
        if identity is None:
            continue
        if identity in result and result[identity] != row:
            raise ValueError(f"duplicate or conflicting {label}: {identity!r}")
        result[identity] = row
    return result


def _snapshot_content_problem(snapshot: SnapshotRecord) -> str | None:
    if snapshot.content is None or snapshot.byte_length is None:
        return "raw bytes or byte length are unavailable"
    if snapshot.byte_length <= 0 or snapshot.byte_length != len(snapshot.content):
        return "raw byte length disagrees with the snapshot"
    if hashlib.sha256(snapshot.content).hexdigest() != snapshot.snapshot_id:
        return "raw bytes disagree with the snapshot SHA-256"
    return None


def _decimal(value: object) -> Decimal | None:
    if type(value) not in (Decimal, int, float):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _numeric_equal(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is right
    value = _decimal(left)
    return value is not None and value == _decimal(right)


class _CausalityError(ValueError):
    """A receipt asserts a relationship that the visibility evidence excludes."""


class _EvidenceGap(ValueError):
    """A projected absence or unreadable source cannot prove a contradiction."""


@dataclass(frozen=True)
class _Link:
    receipts: tuple[RunEventRecord, ...] = ()
    failure: str | None = None
    shortfall: str | None = None
    proof: tuple[str, ...] = ()
    historical_receipts: tuple[RunEventRecord, ...] | None = None


@dataclass(frozen=True)
class _CaptureItem:
    receipt: RunEventRecord
    pack_digest: str
    index: int
    candidate: CandidateLinkRecord
    observed_at: datetime
    kind: str
    market_ref_kind: str
    market_ref: str
    token_ref: str | None
    side: str
    bid: int | float | None
    ask: int | float | None
    mid: int | float | None
    last: int | float | None
    source_kind: str
    source_ref: str | None
    snapshot_sha256: str

    def key(self, family: str) -> str:
        if family == "candidate_price_evidence":
            return self.close_record().evidence_key_with(self.candidate.candidate_id)
        return self.mark_record().mark_key()

    def _prices(self) -> dict:
        return {
            name: getattr(self, name) for name in (
                "observed_at", "market_ref_kind", "market_ref", "token_ref", "side",
                "bid", "ask", "mid", "last", "source_kind", "source_ref", "snapshot_sha256",
            )
        }

    def close_record(self) -> store.PriceEvidenceObservation:
        return store.PriceEvidenceObservation(evidence_role="CLOSE", **self._prices())

    def mark_record(self) -> store.CandidateMarkCapture:
        return store.CandidateMarkCapture(
            candidate_id=self.candidate.candidate_id, sampling_kind=self.kind, **self._prices(),
        )

    def matches(self, family: str, row: CandidateChildRecord) -> bool:
        if family == "candidate_price_evidence":
            if (self.kind != "CLOSE" or row.evidence_role != "CLOSE"
                    or row.evidence_key != self.key(family)
                    or row.model_probability is not None or row.book_sha256 is not None):
                return False
        elif row.sampling_kind != self.kind or row.candidate_mark_key != self.key(family):
            return False
        return (
            _utc(row.written_at) == _utc(self.observed_at)
            and all(getattr(row, field) == getattr(self, field) for field in (
                "market_ref_kind", "market_ref", "token_ref", "side",
                "source_kind", "source_ref", "snapshot_sha256",
            ))
            and all(_numeric_equal(getattr(row, field), getattr(self, field))
                    for field in ("bid", "ask", "mid", "last"))
        )


def _capture_text(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    _require_nonempty_str(value, "capture observation text")
    return value


@dataclass(frozen=True)
class _CaptureQuote:
    scan_id: int | None
    scan_operation_key: str | None
    candidate_key: str
    observed_at: datetime
    kind: str
    market_ref_kind: str
    market_ref: str
    token_ref: str | None
    side: str
    prices: tuple[int | float | None, ...]
    source_kind: str
    source_ref: str | None
    snapshot_sha256: str

    @property
    def has_price(self) -> bool:
        return any(price is not None for price in self.prices)


@dataclass(frozen=True)
class _CaptureObservation:
    candidate: CandidateLinkRecord
    item: _CaptureItem | None
    downgrade: str | None = None


def _resolve_capture_target(
    ledger, receipt, scan_id, operation, candidate_key, targets, missing_prefix,
) -> CandidateLinkRecord:
    cache_key = str(scan_id if scan_id is not None else operation), candidate_key
    if cache_key in targets:
        return targets[cache_key]
    selector = cache_key[0]
    if missing_prefix and selector.isascii() and selector.isdigit() and not selector.startswith("0"):
        raise _EvidenceGap(f"earlier pack or item unavailable for first lookup of {cache_key!r}")
    scan = ledger.scans.get(scan_id) if scan_id is not None else ledger.scans_by_operation.get(operation)
    if scan is None:
        absence = ledger.receipt_absence(
            receipt, "scan_runs", row_key=scan_id,
            may_match=lambda _id, row: row is None or row.operation_key == operation,
        )
        if absence:
            raise _CausalityError(f"capture target scan for {cache_key!r} is absent in {absence}")
        raise _EvidenceGap(f"capture target scan for {cache_key!r} is unavailable")
    candidate = ledger.candidates_by_key.get((scan.scan_id, candidate_key))
    if candidate is None:
        absence = ledger.receipt_absence(
            receipt, "candidates", may_match=lambda _id, row: row is None or (
                row.scan_id == scan.scan_id and row.candidate_key in (None, candidate_key)
            ),
        )
        if absence:
            raise _CausalityError(f"capture target candidate {cache_key!r} was still absent in {absence}")
        raise _EvidenceGap(f"capture target candidate {cache_key!r} is unavailable")
    for family, row in (("scan_runs", scan), ("candidates", candidate)):
        late = ledger.unavailable_by(family, row, receipt.occurred_at)
        absence = ledger.receipt_absence(receipt, family, row_key=_row_id(family, row))
        if late or absence:
            raise _CausalityError(late or f"{family} row {_row_id(family, row)!r} was still absent in {absence}")
        if not ledger.parent_available(family, row):
            raise _EvidenceGap(f"capture target {cache_key!r} has no visible {family} projection")
    if type(candidate.candidate_id) is not int:
        raise _EvidenceGap("resolved capture candidate_id is unavailable")
    targets[cache_key] = candidate
    return candidate


def _capture_quote(raw: Mapping[str, object], digest: str) -> _CaptureQuote:
    # capture.observations_from_pack accepts these fields and defaults.
    if not isinstance(raw, dict) or set(raw) - {
        "candidate_key", "scan_id", "scan_operation_key", "sampling_kind",
        "observed_at", "market_ref_kind", "market_ref", "token_ref", "side",
        "bid", "ask", "mid", "last", "source_kind", "source_ref", "snapshot_sha256",
    }:
        raise ValueError("invalid capture observation fields")
    scan_id, operation = raw.get("scan_id"), raw.get("scan_operation_key")
    if (scan_id is None) == (operation is None):
        raise ValueError("capture observation needs exactly one scan selector")
    if scan_id is None:
        _capture_text(operation)
    elif type(scan_id) is not int:
        raise ValueError("capture scan_id must be an integer")
    candidate_key = _capture_text(raw.get("candidate_key"))
    observed_at = datetime.fromisoformat(_capture_text(raw.get("observed_at")))
    _require_aware(observed_at, "capture observed_at")
    market_ref_kind = raw.get("market_ref_kind", "market_slug")
    market_ref = _capture_text(raw.get("market_ref"))
    token_ref = _capture_text(raw.get("token_ref"), optional=True)
    side = raw.get("side", "YES")
    kind = raw.get("sampling_kind", "PERIODIC")
    source_kind = _capture_text(raw.get("source_kind", "clob_book"))
    source_ref = _capture_text(raw.get("source_ref"), optional=True)
    if (market_ref_kind not in ("condition_id", "market_id", "token_id", "market_slug")
            or side not in ("YES", "NO", "YES-basket", "NO-basket")
            or kind not in ("PERIODIC", "CLOSE")):
        raise ValueError("invalid capture observation market, side or sampling kind")
    snapshot_digest = raw.get("snapshot_sha256")
    if snapshot_digest is None:
        snapshot_digest = digest
    _require_sha256(snapshot_digest, "capture snapshot_sha256")
    prices = tuple(raw.get(field) for field in ("bid", "ask", "mid", "last"))
    numbers = tuple(_decimal(price) for price in prices)
    for price, number in zip(prices, numbers, strict=True):
        if price is not None and (type(price) not in (int, float)
                                  or number is None or not 0 <= number <= 1):
            raise ValueError("invalid capture observation price")
    return _CaptureQuote(
        scan_id, operation, candidate_key, observed_at, kind, market_ref_kind,
        market_ref, token_ref, side, prices, source_kind, source_ref, snapshot_digest,
    )


def _capture_observation(
    quote: _CaptureQuote,
    ledger: _LedgerEvidence,
    receipt: RunEventRecord,
    digest: str,
    index: int,
    targets: dict[tuple[str, str], CandidateLinkRecord],
    missing_prefix: bool,
) -> _CaptureObservation:
    candidate = _resolve_capture_target(
        ledger, receipt, quote.scan_id, quote.scan_operation_key, quote.candidate_key, targets, missing_prefix,
    )
    if candidate.event_start_at is _MISSING:
        raise _EvidenceGap(f"candidate {candidate.candidate_id!r} event_start_at is not projected")
    if not quote.has_price:
        return _CaptureObservation(candidate, None)
    kind = quote.kind
    downgrade = None
    if kind == "CLOSE":
        if candidate.event_start_at is None:
            downgrade = "missing_event_start"
        elif _utc(quote.observed_at) >= _utc(candidate.event_start_at):
            downgrade = "observed_at_after_event_start"
        if downgrade:
            kind = "PERIODIC"
    if kind == "CLOSE":
        decision_at = ledger.decision_times.get(str(candidate.candidate_id))
        if decision_at is not None and _utc(quote.observed_at) < decision_at:
            raise ValueError("CLOSE observation precedes the decision")
    item = _CaptureItem(
        receipt, digest, index, candidate, quote.observed_at, kind, quote.market_ref_kind,
        quote.market_ref, quote.token_ref, quote.side, *quote.prices, quote.source_kind,
        quote.source_ref, quote.snapshot_sha256,
    )
    item.mark_record().validate()
    if kind == "CLOSE":
        item.close_record().validate()
    return _CaptureObservation(candidate, item, downgrade)


def _receipt_name(event: RunEventRecord) -> str:
    return repr(_receipt_id(event))


def _capture_packs(event: RunEventRecord) -> tuple[dict, tuple[str, ...]]:
    if len(event.output_evidence) != 1:
        raise ValueError("expected one stable capture summary")
    summary = json.loads(event.output_evidence[0])
    if (not isinstance(summary, dict) or summary.get("command") != CAPTURE_COMMAND
            or not isinstance(summary.get("packs"), list)):
        raise ValueError("expected a capture summary with packs")
    digests = []
    for pack in summary["packs"]:
        if not isinstance(pack, dict):
            raise ValueError("invalid capture pack reference")
        _require_nonempty_str(pack.get("pack"), "capture pack name")
        _require_sha256(pack.get("snapshot_sha256"), "capture pack snapshot_sha256")
        digests.append(pack["snapshot_sha256"])
    return summary, tuple(digests)


_BANKROLL_SETTLEMENT_REASONS = {
    "NATURAL": "SETTLE_NATURAL",
    "VOIDED_REFUND": "VOIDED_REFUND",
}


class _LedgerEvidence:
    """Indexes one consistent read and its existing visibility inventories."""

    def __init__(self, evidence: FEvidence, window_days: int):
        self.evidence = evidence
        self.start, self.end = window_bounds(evidence.window_start, window_days=window_days)
        self.as_of = None if evidence.as_of is None else _utc(evidence.as_of)
        inventories = sorted(
            _unique_rows(evidence.visibility_snapshots, key=lambda snap: snap.snapshot_id,
                         label="visibility snapshot_id").values(),
            key=lambda snap: (_utc(snap.observed_at), snap.snapshot_id),
        )
        self.inventories = tuple(
            snapshot for snapshot in inventories
            if self.as_of is None or _utc(snapshot.observed_at) <= self.as_of
        )
        self.inventory_by_id = {snapshot.snapshot_id: snapshot for snapshot in self.inventories}
        self.inventory_rows = {
            snap.snapshot_id: {(row.family, row.row_key): row for row in snap.rows}
            for snap in self.inventories
        }
        event_ids_by_key: dict[str, str] = {}
        event_keys_by_id: dict[str, str] = {}
        for snapshot in self.inventories:
            for event_id, event_key in snapshot.bankroll_event_keys:
                if (event_key in event_ids_by_key and event_ids_by_key[event_key] != event_id
                        or event_id in event_keys_by_id and event_keys_by_id[event_id] != event_key):
                    raise ValueError(f"conflicting bankroll event key {event_key!r} and event ID {event_id!r}")
                event_ids_by_key[event_key] = event_id
                event_keys_by_id[event_id] = event_key
        self.event_ids_by_key = event_ids_by_key
        self.event_keys_by_id = event_keys_by_id
        self.periods: dict[tuple[str, int], str] = {}
        self.observations: dict[tuple[str, int], tuple] = {}
        settlement_reads = _unique_rows(
            evidence.settlement_reads,
            key=lambda read: (read.operation_key, read.attempt, read.entry_id),
            label="settlement read evidence",
        )
        self.settlement_reads: dict[tuple[str, int], dict[int, SettlementReadEvidence]] = defaultdict(dict)
        for (operation, attempt, entry_id), read in settlement_reads.items():
            self.settlement_reads[operation, attempt][entry_id] = read
        self.settlement_revisions = _unique_rows(
            evidence.settlement_writer_revisions, key=lambda row: row.code_commit,
            label="settlement writer revision",
        )
        self.settlement_plans = _unique_rows(
            evidence.settlement_planning, key=lambda row: (row.operation_key, row.attempt),
            label="settlement planning evidence",
        )
        visible_events = tuple(
            event for event in _visible_events(evidence.run_events, self.as_of)
            if self.period("command_run_events", event) != "not-visible"
        )
        self.events = tuple(
            event for event in visible_events
            if _utc(event.occurred_at) < self.end
            and self.period("command_run_events", event) != "post-window"
        )
        self.checks = tuple(
            check for check in evidence.bankroll_checks
            if self.period("bankroll_checks", check) not in ("post-window", "not-visible")
        )
        self.current_events = tuple(event for event in self.events if self.is_current(event))
        self.attempt_starts = {
            _receipt_id(event)[:3]: _utc(event.occurred_at)
            for event in self.events if event.phase == PHASE_STARTED
        }
        self.scans = _unique_rows(evidence.scan_runs, key=lambda scan: scan.scan_id, label="scan_id")
        self.scans_by_operation = _unique_rows(
            self.scans.values(), key=lambda scan: scan.operation_key, label="scan operation_key",
        )
        self.scans_by_key = _unique_rows(self.scans.values(), key=lambda scan: scan.scan_key, label="scan_key")
        self.candidates = _unique_rows(
            tuple(row for row in evidence.candidates
                  if self.period("candidates", row) != "not-visible"),
            key=lambda row: str(row.candidate_id),
            label="candidate_id",
        )
        self.scan_candidates: dict[int, list[CandidateLinkRecord]] = defaultdict(list)
        for _candidate_id, candidate in sorted(self.candidates.items()):
            self.scan_candidates[candidate.scan_id].append(candidate)
        self.candidates_by_key = _unique_rows(
            evidence.candidates,
            key=lambda row: (row.scan_id, row.candidate_key) if row.candidate_key is not None else None,
            label="(scan_id, candidate_key)",
        )
        self.entries = _unique_rows(evidence.entries, key=lambda row: str(row.entry_id), label="entry_id")
        self.entries_by_candidate = _unique_rows(
            self.entries.values(), key=lambda row: str(row.candidate_id), label="entry candidate_id",
        )
        self.outcomes = _unique_rows(
            evidence.market_outcomes, key=lambda row: _str_id(row.outcome_id),
            label="outcome_id",
        )
        self.snapshots = _unique_rows(evidence.snapshots, key=lambda row: row.snapshot_id, label="snapshot_id")
        self.price_rows = _unique_rows(
            evidence.price_evidence, key=lambda row: row.evidence_key, label="evidence_key",
        )
        self.mark_rows = _unique_rows(
            evidence.candidate_marks, key=lambda row: row.candidate_mark_key, label="candidate_mark_key",
        )
        _unique_rows(evidence.marks, key=lambda row: row.mark_key, label="mark_key")
        _unique_rows(self.outcomes.values(), key=lambda row: row.evidence_key, label="outcome evidence_key")
        self.cash = _unique_rows(
            evidence.bankroll_events, key=lambda row: str(row.event_id), label="bankroll event_id",
        )
        self.cash_by_key = _unique_rows(
            self.cash.values(), key=lambda row: row.event_key, label="bankroll event_key",
        )
        for row in self.cash.values():
            event_id = str(row.event_id)
            if row.event_key is not None and (
                event_id in event_keys_by_id and event_keys_by_id[event_id] != row.event_key
                or row.event_key in event_ids_by_key and event_ids_by_key[row.event_key] != event_id
            ):
                raise ValueError(f"bankroll event {event_id!r} conflicts with its inventory key")
        self.settlements = _unique_rows(
            evidence.settlements, key=lambda row: str(row.entry_id), label="settlement entry_id",
        )
        self.inventory_projections = {
            family: _unique_rows(rows, key=lambda row: _str_id(_row_id(family, row)), label=family)
            for family, rows in (
                ("scan_runs", evidence.scan_runs), ("candidates", evidence.candidates),
                ("entries", evidence.entries), ("market_outcomes", evidence.market_outcomes),
                ("snapshots", evidence.snapshots), ("candidate_marks", evidence.candidate_marks),
                ("candidate_price_evidence", evidence.price_evidence),
                ("settlements", evidence.settlements), ("bankroll_events", evidence.bankroll_events),
            )
        }
        self.scan_events: dict[str, list[RunEventRecord]] = defaultdict(list)
        for event in self.events:
            if event.command == SCAN_COMMAND:
                self.scan_events[event.operation_key].append(event)
        self.decision_times = {}
        for row in evidence.price_evidence:
            if row.evidence_role == "DECISION" and self.parent_available("candidate_price_evidence", row):
                key, moment = str(row.candidate_id), _utc(row.written_at)
                self.decision_times[key] = min(moment, self.decision_times.get(key, moment))
        self.partial_attempts = self._partial_attempts()
        self.capture_items: dict[str, list[_CaptureItem]] = defaultdict(list)
        self.capture_links: dict[_CaptureItem, _Link] = {}
        self.pack_receipts: dict[str, list[RunEventRecord]] = defaultdict(list)
        self.capture_shortfalls: list[str] = []
        self.writer_failures: list[str] = []
        self.writer_shortfalls: list[str] = []
        self.scan_successes: dict[int, list[RunEventRecord]] = defaultdict(list)
        self.scan_failures: dict[int, list[RunEventRecord]] = defaultdict(list)
        self.scan_dependency_issues: dict[tuple[str, int, RunEventRecord], list[_Link]] = defaultdict(list)
        self.settle_claims: dict[str, list[tuple[str, RunEventRecord]]] = defaultdict(list)
        self.settle_attempt_results: dict[tuple[str, str, int], dict[int, str]] = {}
        self.settle_planning_ids: dict[tuple[str, str, int], str] = {}
        self.pending_entries: dict[str, list[RunEventRecord]] = defaultdict(list)
        self.settlement_links: dict[int, _Link] = {}
        self._index_writer_evidence()

    def is_current(self, receipt: RunEventRecord) -> bool:
        return self.start <= _utc(receipt.occurred_at) < self.end

    def in_window(self, period: str, receipts: Sequence[RunEventRecord]) -> bool:
        return period == "in-window" or any(self.is_current(event) for event in receipts)

    def period(self, family: str, row: object) -> str:
        cache_key = family, id(row)
        if cache_key not in self.periods:
            self.periods[cache_key] = self._period(family, row)
        return self.periods[cache_key]

    def _period(self, family: str, row: object) -> str:
        first_seen, prior_absence = self.observed_bounds(family, row)
        if first_seen is not None or prior_absence is not None:
            if first_seen is None and prior_absence == self.as_of:
                return "not-visible"
            if first_seen is None:
                first_seen = self.as_of
            if first_seen is not None and first_seen < self.start:
                return "pre-anchor"
            if prior_absence is not None and first_seen is not None:
                if self.start <= prior_absence < first_seen < self.end:
                    return "in-window"
                if self.end <= prior_absence < first_seen:
                    return "post-window"

        # The scan and control drivers originate these clocks locally.
        # A row cannot have been written before the command began.
        if family in ("scan_runs", "queue_control_events"):
            moment = _utc((row.finished_at or row.started_at)
                          if family == "scan_runs" else row.observed_at)
            if self.as_of is not None and moment > self.as_of:
                return "not-visible"
            if moment >= self.end:
                return "post-window"
            if self.start <= moment and (
                family == "queue_control_events" or self.as_of is None or self.as_of < self.end
                or (row.operation_key is None and self.as_of == self.end)
                or (row.scan_key is None and self.as_of == self.end)
            ):
                return "in-window"
        return "unknown"

    def inventory_state(self, snapshot: VisibilitySnapshot, family: str, row: object) -> bool | None:
        row_id = _row_id(family, row)
        if row_id is None:
            return None
        key = family, str(row_id)
        recorded = self.inventory_rows[snapshot.snapshot_id].get(key)
        if recorded is not None:
            if recorded != row_fingerprint(family, row):
                raise ValueError(f"conflicting inventory content for {family} row {row_id!r}")
            return True
        if family in snapshot.complete_families or key in snapshot.checked_absences:
            return False
        return None

    def observed_bounds(self, family: str, row: object) -> tuple[datetime | None, datetime | None]:
        key = family, id(row)
        if key not in self.observations:
            present, absent = [], []
            for snapshot in self.inventories:
                state = self.inventory_state(snapshot, family, row)
                if state is not None:
                    (present if state else absent).append(_utc(snapshot.observed_at))
            first_seen, last_absent = min(present, default=None), max(absent, default=None)
            if first_seen is not None and last_absent is not None and first_seen < last_absent:
                raise ValueError(f"immutable row {family} {_row_id(family, row)!r} disappears in inventory")
            self.observations[key] = first_seen, last_absent
        return self.observations[key]

    def unavailable_by(self, family: str, row: object, moment: datetime) -> str | None:
        last_absent = self.observed_bounds(family, row)[1]
        row_id = _row_id(family, row)
        cutoff = _utc(moment)
        if last_absent is not None and last_absent > cutoff:
            return f"{family} row {row_id!r} was still absent at {last_absent.isoformat()}"
        if family == "scan_runs":
            lower = _utc(row.finished_at or row.started_at)
            if lower > cutoff:
                return f"parent scan_runs row {row_id!r} could first exist at {lower.isoformat()}"
        return None

    def unreceipted_write(self, family: str, row: object) -> tuple[str, ...]:
        row_id = _row_id(family, row)
        if row_id is None:
            return ()
        return tuple(
            (f"{snapshot.unreceipted_source} ({snapshot.snapshot_id})"
             if snapshot.unreceipted_source else snapshot.snapshot_id)
            for snapshot in self.inventories
            if any(ref.family == family and ref.row_key == str(row_id)
                   and ref == row_fingerprint(family, row) for ref in snapshot.unreceipted_writes)
        )

    def parent_available(self, family: str, row: object | None) -> bool:
        return row is not None and self.period(family, row) not in ("post-window", "not-visible")

    def receipt_conflict(self, receipt: RunEventRecord, detail: str) -> str:
        message = f"{_receipt_name(receipt)} at {_utc(receipt.occurred_at).isoformat()}: {detail}"
        if self.is_current(receipt) and message not in self.writer_failures:
            self.writer_failures.append(message)
        return message

    def receipt_gap(self, receipt: RunEventRecord, detail: str) -> None:
        message = f"{_receipt_name(receipt)} at {_utc(receipt.occurred_at).isoformat()}: {detail} (R3)"
        if self.is_current(receipt) and message not in self.writer_shortfalls:
            self.writer_shortfalls.append(message)

    def inventory_after_receipt(self, snapshot: VisibilitySnapshot, receipt: RunEventRecord) -> bool:
        observed, terminal = _utc(snapshot.observed_at), _utc(receipt.occurred_at)
        return observed > terminal or (
            observed == terminal and self.inventory_state(snapshot, "command_run_events", receipt) is True
        )

    def _snapshot_excludes(
        self, snapshot: VisibilitySnapshot, family: str, *, row_key=None, may_match=None,
    ) -> bool:
        if row_key is not None:
            key = family, str(row_key)
            if key in self.inventory_rows[snapshot.snapshot_id]:
                return False
            if key in snapshot.checked_absences or family in snapshot.complete_families:
                return True
        if family not in snapshot.complete_families or may_match is None:
            return False
        for (entry_family, key), _reference in self.inventory_rows[snapshot.snapshot_id].items():
            if family != entry_family:
                continue
            projection = self.inventory_projections[family].get(key)
            if projection is not None:
                self.inventory_state(snapshot, family, projection)
            if may_match(key, projection):
                return False
        return True

    def receipt_absence(self, receipt, family, *, row_key=None, may_match=None) -> str | None:
        for snapshot in self.inventories:
            if self.inventory_after_receipt(snapshot, receipt) and self._snapshot_excludes(
                snapshot, family, row_key=row_key, may_match=may_match,
            ):
                return f"{snapshot.snapshot_id!r} at {_utc(snapshot.observed_at).isoformat()}"
        return None

    def dependency_absence(
        self, receipt: RunEventRecord, family: str, *, row_key=None, may_match=None, read_views=(),
    ) -> str | None:
        """Locate absence proved by an actual writer read or a later inventory."""
        for view in read_views:
            if view is not None and self._snapshot_excludes(
                view, family, row_key=row_key, may_match=may_match,
            ):
                return f"{view.snapshot_id!r} at {_utc(view.observed_at).isoformat()}"
        return self.receipt_absence(receipt, family, row_key=row_key, may_match=may_match)

    def _cash_by_event_key(self, key: str) -> tuple[BankrollEventRecord | None, int | str | None]:
        cash = self.cash_by_key.get(key)
        row_key = cash.event_id if cash is not None else self.event_ids_by_key.get(key)
        return cash, row_key

    def _cash_key_may_match(self, expected_key, event_id, projected) -> bool:
        # A complete inventory can rule out an unrelated key even when
        # the row's monetary content was not projected.
        seen_key = projected.event_key if projected is not None else None
        if seen_key is None:
            seen_key = self.event_keys_by_id.get(event_id)
        return seen_key is None or seen_key == expected_key

    def _cash_key_absence(self, receipt, key, *, read_views=()) -> str | None:
        _cash, row_key = self._cash_by_event_key(key)
        return self.dependency_absence(
            receipt, "bankroll_events", row_key=row_key, read_views=read_views,
            may_match=lambda event_id, row: self._cash_key_may_match(key, event_id, row),
        )

    def scan_atomic_link(self, family: str, row: object, link: _Link) -> _Link:
        if link.failure:
            return link
        valid = []
        conflicts = []
        for receipt in link.receipts:
            late = (
                self.unavailable_by(family, row, receipt.occurred_at)
                if receipt.phase == PHASE_SUCCEEDED else None
            )
            if receipt.phase == PHASE_SUCCEEDED and not late:
                absent = self.receipt_absence(receipt, family, row_key=_row_id(family, row))
                if absent:
                    late = f"{family} row {_row_id(family, row)!r} was still absent in {absent}"
            if late:
                conflicts.append(self.receipt_conflict(receipt, late))
            else:
                valid.append(receipt)
        if conflicts and not any(receipt.phase == PHASE_SUCCEEDED for receipt in valid):
            return _Link(link.receipts, failure="; ".join(conflicts))
        return replace(link, receipts=tuple(valid))

    def _index_scan_success(self, event: RunEventRecord) -> None:
        if (event.scan_id is None or len(event.output_evidence) != 1
                or not event.output_evidence[0] or event.code_commit is None):
            self.receipt_gap(event, "scan success lacks its persisted scan_id, scan_key or code_commit")
            return
        scan_key, = event.output_evidence
        scan = self.scans.get(event.scan_id)
        absence = self.receipt_absence(event, "scan_runs", row_key=event.scan_id)
        by_operation = self.scans_by_operation.get(event.operation_key)
        conflicting = scan or by_operation or self.scans_by_key.get(scan_key)
        if absence:
            detail = f"scan_runs scan_id={event.scan_id} scan_key={scan_key!r} absent in {absence}"
        elif conflicting is not None and (
            conflicting.scan_id != event.scan_id or conflicting.operation_key != event.operation_key
            or (conflicting.scan_key is not None and conflicting.scan_key != scan_key)
            or (conflicting.code_commit is not None and conflicting.code_commit != event.code_commit)
        ):
            detail = f"run-scan receipt has a conflicting scan_id, scan_key or commit for scan {conflicting.scan_id}"
        elif scan is None or scan.scan_key is None or scan.code_commit is None:
            self.receipt_gap(event, f"scan_runs scan_id={event.scan_id} has incomplete projection")
            return
        elif late := self.unavailable_by("scan_runs", scan, event.occurred_at):
            detail = late
        elif not self.parent_available("scan_runs", scan):
            self.receipt_gap(event, f"scan_runs scan_id={event.scan_id} is unavailable")
            return
        else:
            self.scan_successes[scan.scan_id].append(event)
            self._verify_scan_dependencies(event, scan)
            return
        self.receipt_conflict(event, detail)
        if conflicting is not None:
            self.scan_failures[conflicting.scan_id].append(event)

    def scan_link(self, scan: ScanRunRecord) -> _Link:
        if scan.operation_key is None:
            return _Link(failure="NULL operation_key (no recorded run-scan writer)")
        events = self.scan_events.get(scan.operation_key, ())
        if not events:
            return _Link(failure=f"operation_key={scan.operation_key!r} matches no run-scan "
                                 "command_run_events receipt (wrong command or no receipt)")
        exact = self.scan_successes.get(scan.scan_id)
        if exact:
            return _Link(tuple(exact))
        if failures := self.scan_failures.get(scan.scan_id):
            return _Link(tuple(failures), failure="run-scan receipt has a conflicting scan_id, scan_key or commit or timing")
        started = tuple(event for event in events if event.phase == PHASE_STARTED
                        and (event.scan_id is None or event.scan_id == scan.scan_id)
                        and (event.code_commit is None or event.code_commit == scan.code_commit))
        if started and scan.scan_key is not None:
            # Operation identity, rather than STARTED alone, binds the scan
            # after its transaction when an attempt has no successful phase.
            return _Link(started)
        successful = tuple(event for event in events if event.phase == PHASE_SUCCEEDED)
        if successful:
            return _Link(successful, shortfall="run-scan receipts lack a complete attributable scan reference (R3)")
        return _Link(failure="run-scan receipts lack an attributable scan reference")

    def scan_has_window_receipt(self, scan: ScanRunRecord) -> bool:
        return any(self.is_current(event) for event in self.scan_link(scan).receipts)

    def candidate_link(self, row: CandidateLinkRecord) -> _Link:
        scan = self.scans.get(row.scan_id)
        if not self.parent_available("scan_runs", scan):
            return _Link(failure=f"unknown scan_id={row.scan_id} or outside the visible window")
        link = self.scan_link(scan)
        if link.failure:
            return _Link(link.receipts, failure=f"scan_id={scan.scan_id} has no writer proof: {link.failure}")
        if row.candidate_key is None and type(row.candidate_id) is int:
            return _Link(link.receipts, shortfall="candidate key is unavailable (R3)")
        link = self.scan_atomic_link("candidates", row, link)
        return self._with_scan_dependency_evidence("candidates", row, link)

    def entry_link(self, row: EntryRecord) -> _Link:
        candidate = self.candidates.get(str(row.candidate_id))
        if not self.parent_available("candidates", candidate):
            return _Link(failure=f"unknown candidate_id={row.candidate_id!r} or outside the visible window")
        link = self.candidate_link(candidate)
        if link.failure:
            return replace(link, failure=f"references candidate_id={row.candidate_id!r} "
                                         f"whose candidate is not receipt-proven: {link.failure}")
        link = self.scan_atomic_link("entries", row, link)
        return self._with_scan_dependency_evidence("entries", row, link)

    def _verify_scan_dependencies(self, event: RunEventRecord, scan: ScanRunRecord) -> None:
        for candidate in self.scan_candidates.get(scan.scan_id, ()):
            if not self.parent_available("candidates", candidate):
                continue
            for digest in candidate.snapshot_refs:
                label = f"candidate {candidate.candidate_id!r} snapshot {digest!r}"
                self._verify_scan_dependency(
                    event, ("candidates", id(candidate)), "snapshots", self.snapshots.get(digest), label,
                    absent=self.dependency_absence(event, "snapshots", row_key=digest),
                )
            entry = self.entries_by_candidate.get(str(candidate.candidate_id))
            if entry is None or type(entry.entry_id) is not int or not self.parent_available("entries", entry):
                continue
            key = f"entry:{entry.entry_id}:cash"
            cash = self.cash_by_key.get(key)
            failure = shortfall = None
            if cash is not None:
                failure, shortfall = self._scan_cash_issue(cash, entry, scan.scan_id)
            self._verify_scan_dependency(
                event, ("entries", id(entry)), "bankroll_events", cash,
                f"entry {entry.entry_id!r} opening cash leg {key!r}",
                absent=self._cash_key_absence(event, key), failure=failure, shortfall=shortfall,
            )

    def _verify_scan_dependency(
        self, event, subject, family, projection, label, *, absent, failure=None, shortfall=None,
    ):
        if absent:
            failure = f"{family} dependency was still absent in {absent}"
        elif not self.parent_available(family, projection):
            shortfall = f"{family} dependency projection unavailable"
        subject_key = (*subject, event)
        if failure:
            message = self.receipt_conflict(event, f"{label}: {failure}")
            self.scan_dependency_issues[subject_key].append(_Link(failure=message))
        elif shortfall:
            message = f"{label}: {shortfall}"
            self.receipt_gap(event, message)
            self.scan_dependency_issues[subject_key].append(_Link(shortfall=f"{message} (R3)"))

    def _with_scan_dependency_evidence(self, family, row, link: _Link) -> _Link:
        if link.failure or link.shortfall:
            return link
        valid, failures, gaps = [], [], []
        for receipt in link.receipts:
            issues = self.scan_dependency_issues.get((family, id(row), receipt), ())
            if not issues:
                valid.append(receipt)
            failures.extend(issue.failure for issue in issues if issue.failure)
            gaps.extend(issue.shortfall for issue in issues if issue.shortfall)
        if valid:
            return replace(link, receipts=tuple(valid))
        return replace(link, failure="; ".join(failures) or None, shortfall="; ".join(gaps) or None)

    def _scan_cash_issue(
        self, row: BankrollEventRecord, entry: EntryRecord, scan_id: int,
    ) -> tuple[str | None, str | None]:
        stake, fee = _decimal(entry.stake), _decimal(entry.entry_fee)
        if (row.event_key != f"entry:{entry.entry_id}:cash" or row.currency != "USD"
                or row.reason != "SCAN_ENTRY" or row.scan_id != scan_id or row.entry_id != entry.entry_id
                or row.reverses_event_id is not None or row.note is not None
                or (entry.is_legacy is not _MISSING and row.is_legacy != entry.is_legacy)
                or _utc(row.occurred_at) != _utc(entry.written_at)
                or (stake is not None and fee is not None and row.amount is not None
                    and not _numeric_equal(row.amount, store.exact_sum(stake, fee).copy_negate()))):
            return "SCAN_ENTRY cash key, amount or entry time disagrees with the scan writer", None
        if stake is None or fee is None or row.amount is None:
            return None, "SCAN_ENTRY cash amount or booked entry principal/fee projection unavailable"
        return None, None

    def _candidate_parent(self, candidate_id: int | str) -> _Link:
        candidate = self.candidates.get(str(candidate_id))
        if not self.parent_available("candidates", candidate):
            return _Link(failure=f"unknown candidate_id={candidate_id!r} or outside the visible window")
        scan = self.scans.get(candidate.scan_id)
        if not self.parent_available("scan_runs", scan):
            return _Link(failure=f"unknown scan_id={candidate.scan_id} or outside the visible window")
        return _Link()

    def _entry_parent(self, entry_id: int | str | None) -> _Link:
        entry = self.entries.get(_str_id(entry_id))
        if not self.parent_available("entries", entry):
            return _Link(failure=f"unknown entry_id={entry_id!r} or outside the visible window")
        return self._candidate_parent(entry.candidate_id)

    def _partial_attempts(self) -> dict[str, tuple[RunEventRecord, ...]]:
        # A prior attempt which failed or never reached a terminal
        # receipt may have committed an item. A different success
        # cannot fill in the missing item proof.
        by_attempt: dict[tuple[str, str, int], list[RunEventRecord]] = defaultdict(list)
        for event in self.events:
            by_attempt[_receipt_id(event)[:3]].append(event)
        incomplete: dict[str, list[RunEventRecord]] = defaultdict(list)
        for (command, _operation, _attempt), events in by_attempt.items():
            terminal = next((event for event in events if event.phase in TERMINAL_PHASES), None)
            if terminal is not None and (
                terminal.phase == PHASE_SUCCEEDED or _utc(terminal.occurred_at) < self.start
            ):
                continue
            incomplete[command].extend(events)
        return {command: tuple(events) for command, events in incomplete.items()}

    def _index_writer_evidence(self) -> None:
        for event in self.events:
            if event.phase != PHASE_SUCCEEDED:
                continue
            if event.command == SCAN_COMMAND:
                self._index_scan_success(event)
            elif event.command == SETTLE_COMMAND:
                self._index_settle_success(event)
            elif event.command == CAPTURE_COMMAND:
                self._index_capture_success(event)

    def _index_settle_success(self, receipt: RunEventRecord) -> None:
        items = []
        positions: dict[int, int] = {}
        bad = False
        for index, text in enumerate(receipt.output_evidence):
            fields = text.split(":", 2)
            if (len(fields) != 3 or fields[0] not in ("settled", "pending")
                    or not fields[1].isascii() or not fields[1].isdigit()
                    or fields[1].startswith("0") or len(fields[1]) > 19 or not fields[2]):
                self.receipt_conflict(receipt, f"invalid B3 entry item at index {index}: {text!r}")
                bad = True
                continue
            kind, raw_id, result = fields
            entry_id = int(raw_id)
            if kind == "settled" and result not in _BANKROLL_SETTLEMENT_REASONS:
                self.receipt_conflict(receipt, f"unsupported B3 settled classification {result!r} at item {index}")
                bad = True
            if entry_id in positions:
                self.receipt_conflict(receipt, f"multiple B3 result items for entry {entry_id} at "
                                               f"indices {positions[entry_id]} and {index}")
                bad = True
            elif receipt.output_order_available and items and items[-1][1] > entry_id:
                prior_index, prior_id, *_rest = items[-1]
                self.receipt_conflict(receipt, f"B3 result order is reversed: entry {prior_id} at index "
                                               f"{prior_index} precedes entry {entry_id} at index {index}")
                bad = True
            positions.setdefault(entry_id, index)
            items.append((index, entry_id, kind, result))

        if not receipt.output_order_available:
            self.receipt_gap(receipt, "B3 raw success array order is unavailable")
        planning_bound = self._bind_settlement_planning(receipt)
        if not planning_bound:
            bad = True
        else:
            bad = not self._verify_settlement_planning(receipt, set(positions)) or bad
        bad = not self._verify_settlement_order(receipt, items, planning_bound=planning_bound) or bad
        bad = not self._verify_settlement_read_results(receipt, items) or bad
        for index, entry_id, kind, result in items:
            if kind == "settled":
                self._settlement_pair(receipt, entry_id, item_index=index)
        if bad or not receipt.output_order_available:
            return
        self.settle_attempt_results[_receipt_id(receipt)[:3]] = {
            entry_id: kind for _index, entry_id, kind, _result in items
        }
        for _index, entry_id, kind, result in items:
            if kind == "settled":
                self.settle_claims[str(entry_id)].append((result, receipt))
            else:
                self.pending_entries[str(entry_id)].append(receipt)

    def _verify_settlement_read_results(
        self, receipt: RunEventRecord, items: Sequence[tuple[int, int, str, str]],
    ) -> bool:
        """Differentiate a planning read from an actual per-entry write path."""
        if not receipt.output_order_available:
            return True
        reads = self.settlement_reads.get((receipt.operation_key, receipt.attempt), {})
        results = {entry_id: (kind, outcome) for _index, entry_id, kind, outcome in items}
        start = self.attempt_starts.get(_receipt_id(receipt)[:3])
        terminal = _utc(receipt.occurred_at)
        revision = self.settlement_revisions.get(receipt.code_commit)
        valid = True
        for entry_id, read in sorted(reads.items()):
            phases = (("terminal cash query", read.terminal_cash_snapshot_id, False),
                      ("opening cash recheck V", read.recheck_snapshot_id, True))
            for phase, snapshot_id, accepts_cash_mismatch in phases:
                if snapshot_id is None:
                    continue
                detail = f"B3 entry {entry_id} {phase} in inventory {snapshot_id!r}"
                view = self.inventory_by_id.get(snapshot_id)
                if view is None:
                    self.receipt_gap(receipt, f"{detail} is unavailable within the attempt")
                    continue
                if _outside_settlement_attempt(_utc(view.observed_at), start, terminal):
                    # The timing check reports the visible contradiction.
                    # This read cannot also establish a result obligation.
                    continue
                if revision is None or not revision.booked_fees:
                    self.receipt_gap(receipt, f"{detail} has no verified per-entry read contract for this revision")
                    break
                result = results.get(entry_id)
                if result is None:
                    self.receipt_conflict(receipt, f"{detail} has no result item in the success array")
                    valid = False
                elif result[0] != "settled" and not (
                    accepts_cash_mismatch and result == ("pending", settle.REASON_ENTRY_CASH)
                ):
                    kind, outcome = result
                    self.receipt_conflict(
                        receipt, f"{detail} cannot produce {kind}:{entry_id}:{outcome} in the success array",
                    )
                    valid = False
                break
        return valid

    def _bind_settlement_planning(self, receipt: RunEventRecord) -> bool:
        key = _receipt_id(receipt)[:3]
        bindings: dict[str, list[str]] = defaultdict(list)
        plan = self.settlement_plans.get(key[1:])
        if plan is not None:
            bindings[plan.snapshot_id].append("open-entry census")
        # A result item may itself be missing. The attempt owns the
        # recorded P reads regardless of the output array's membership.
        for entry_id, read in sorted(self.settlement_reads.get(key[1:], {}).items()):
            if read.planning_snapshot_id is not None:
                bindings[read.planning_snapshot_id].append(f"entry {entry_id}")
        if len(bindings) > 1:
            detail = "; ".join(
                f"{snapshot_id!r} for {', '.join(sources)}" for snapshot_id, sources in bindings.items()
            )
            self.receipt_conflict(receipt, f"conflicting planning P snapshots for one attempt: {detail}")
            return False
        if bindings:
            snapshot_id, = bindings
            self.settle_planning_ids[key] = snapshot_id
            if snapshot_id not in self.inventory_by_id:
                self.receipt_gap(receipt, f"shared planning P inventory {snapshot_id!r} is unavailable")
        return True

    def _verify_settlement_planning(self, receipt: RunEventRecord, reported: set[int]) -> bool:
        plan = self.settlement_plans.get((receipt.operation_key, receipt.attempt))
        view = self.inventory_by_id.get(self.settle_planning_ids.get(_receipt_id(receipt)[:3]))
        if view is None:
            return True
        start = self.attempt_starts.get(_receipt_id(receipt)[:3])
        observed = _utc(view.observed_at)
        if _outside_settlement_attempt(observed, start, _utc(receipt.occurred_at)):
            self.receipt_conflict(receipt, f"planning P inventory {view.snapshot_id!r} lies outside the attempt interval")
            return False
        if plan is None:
            required = {"entries", "candidates", "settlements"}
            if not required.issubset(view.complete_families):
                return True
            opened = self._open_ids_in_planning_view(view)
            if opened is None:
                self.receipt_gap(receipt, f"planning P {view.snapshot_id!r} lacks row projections for its open census")
                return True
            excluded = reported - opened
        else:
            opened = set(plan.open_entry_ids)
            excluded = reported - opened if plan.complete else reported.intersection(plan.checked_excluded_ids)
        omitted = opened - reported if receipt.output_order_available else set()
        if excluded:
            self.receipt_conflict(receipt, f"result entry IDs {sorted(excluded)} were outside planning P {view.snapshot_id!r}")
        if omitted:
            self.receipt_conflict(receipt, f"planning P {view.snapshot_id!r} entries {sorted(omitted)} have no result item")
        unknown = reported - opened - excluded
        if unknown:
            self.receipt_gap(receipt, f"planning P membership unavailable for entry IDs {sorted(unknown)}")
        return not (excluded or omitted)

    def _open_ids_in_planning_view(self, view: VisibilitySnapshot) -> set[int] | None:
        projected = {"entries": [], "candidates": [], "settlements": []}
        for reference in view.rows:
            if reference.family in projected:
                row = self.inventory_projections[reference.family].get(reference.row_key)
                if row is None:
                    return None
                self.inventory_state(view, reference.family, row)
                projected[reference.family].append(row)
        candidate_ids = {row.candidate_id for row in projected["candidates"]}
        settled_ids = {row.entry_id for row in projected["settlements"]}
        if any(type(row.entry_id) is not int for row in projected["entries"]):
            return None
        return {row.entry_id for row in projected["entries"]
                if row.candidate_id in candidate_ids and row.entry_id not in settled_ids}

    def _verify_settlement_order(self, receipt, items, *, planning_bound: bool) -> bool:
        start = self.attempt_starts.get(_receipt_id(receipt)[:3])
        terminal = _utc(receipt.occurred_at)
        revision = self.settlement_revisions.get(receipt.code_commit)
        previous = None
        settled_entries = set()
        valid = True
        kinds = {entry_id: kind for _index, entry_id, kind, _result in items}
        reads = self.settlement_reads.get((receipt.operation_key, receipt.attempt), {})
        for entry_id in sorted(kinds.keys() | reads.keys()):
            planning, recheck, cash_read = self._settlement_views(receipt, entry_id)
            if not planning_bound:
                planning = None
            observations = [("P", planning), (f"V_{entry_id}", recheck), ("terminal cash", cash_read)]
            observed = [(name, view) for name, view in observations if view is not None]
            seen = [_utc(view.observed_at) for _name, view in observed]
            timing_valid = seen == sorted(seen) and not any(
                _outside_settlement_attempt(moment, start, terminal) for moment in seen
            )
            if not timing_valid:
                detail = ", ".join(f"{name} {view.snapshot_id!r} at {view.observed_at.isoformat()}"
                                   for name, view in observed)
                self.receipt_conflict(
                    receipt, f"B3 entry {entry_id} planning P or item reads contradict the attempt interval: {detail}",
                )
                valid = False

            item_reads = [
                (operation, view, _utc(view.observed_at))
                for operation, view in (("recheck", recheck), ("terminal cash query", cash_read))
                if view is not None and not _outside_settlement_attempt(_utc(view.observed_at), start, terminal)
            ]
            if item_reads and previous is not None:
                prior_id, prior_view = previous
                _operation, first_read, first_stamp = min(item_reads, key=lambda item: item[2])
                if _utc(prior_view.observed_at) > first_stamp:
                    self.receipt_conflict(
                        receipt, f"B3 cash recheck order reversed: entry {prior_id} at "
                                 f"{prior_view.snapshot_id!r} {prior_view.observed_at.isoformat()} precedes "
                                 f"entry {entry_id} at {first_read.snapshot_id!r} "
                                 f"{first_read.observed_at.isoformat()} in the plan",
                    )
                    valid = False
            for operation, view, _stamp in item_reads:
                for prior_id in sorted(settled_entries):
                    absent = self._pair_absences(prior_id, snapshot=view)
                    if absent:
                        self.receipt_conflict(
                            receipt, f"B3 entry {prior_id} committed before entry {entry_id} {operation} "
                                     f"{view.snapshot_id!r}, which still shows {', '.join(absent)} absent",
                        )
                        valid = False
            if item_reads:
                _operation, last_read, last_stamp = max(item_reads, key=lambda item: item[2])
                if previous is None or _utc(previous[1].observed_at) <= last_stamp:
                    previous = entry_id, last_read
            # The current writer cannot finish successfully after reaching
            # the terminal cash query without committing this item's pair.
            # Reaching V alone can still end in pending cash mismatch.
            if kinds.get(entry_id) == "settled" or (
                cash_read is not None and timing_valid and revision is not None and revision.booked_fees
            ):
                settled_entries.add(entry_id)
        return valid

    def _settlement_views(self, receipt, entry_id):
        read = self.settlement_reads.get((receipt.operation_key, receipt.attempt), {}).get(entry_id)
        plan = self.settlement_plans.get((receipt.operation_key, receipt.attempt))
        p_id = self.settle_planning_ids.get(_receipt_id(receipt)[:3])
        if p_id is None:
            p_id = plan.snapshot_id if plan else read.planning_snapshot_id if read else None
        return (
            self.inventory_by_id.get(p_id),
            self.inventory_by_id.get(read.recheck_snapshot_id) if read else None,
            self.inventory_by_id.get(read.terminal_cash_snapshot_id) if read else None,
        )

    def _pair_rows(self, entry_id):
        key = f"settle:{entry_id}"
        cash, event_id = self._cash_by_event_key(key)
        settlement = self.settlements.get(str(entry_id))
        return (
            ("settlements", settlement, settlement.row_key if settlement else None, f"settlement for entry {entry_id}",
             lambda _id, projection: projection is None or str(projection.entry_id) == str(entry_id)),
            ("bankroll_events", cash, event_id, f"settlement cash leg {key!r}",
             lambda event_id, row: self._cash_key_may_match(key, event_id, row)),
        )

    def _pair_absences(self, entry_id: int, *, snapshot: VisibilitySnapshot) -> list[str]:
        return [
            label for family, _row, row_key, label, may_match in self._pair_rows(entry_id)
            if self._snapshot_excludes(
                snapshot, family, row_key=row_key, may_match=may_match,
            )
        ]

    def _settlement_pair(self, receipt, entry_id, *, item_index=None):
        pair = self._pair_rows(entry_id)
        failed, missing = [], []
        for family, row, row_key, label, may_match in pair:
            absence = self.dependency_absence(receipt, family, row_key=row_key, may_match=may_match)
            if absence:
                failed.append(f"{label} absent in {absence}")
            elif not self.parent_available(family, row):
                missing.append(label)
        location = f"settled entry {entry_id}" + (f" at item {item_index}" if item_index is not None else "")
        settlement, cash = (item[1] for item in pair)
        if failed:
            detail = "; ".join(failed)
            if item_index is not None:
                self.receipt_conflict(receipt, f"{location}: {detail}")
        if missing:
            detail = ", ".join(missing) + " projection unavailable"
            if item_index is not None:
                self.receipt_gap(receipt, f"{location}: {detail}")
        if failed:
            return settlement, cash, _Link((receipt,), failure="; ".join(failed))
        if missing:
            return settlement, cash, _Link((receipt,), shortfall=f"{detail} (R3)")
        return settlement, cash, _Link()

    def _index_capture_success(self, event: RunEventRecord) -> None:
        try:
            summary, packs = _capture_packs(event)
        except (TypeError, ValueError) as error:
            self._capture_gap(event, str(error))
            return
        if not event.output_order_available:
            self._capture_gap(event, "capture pack order unavailable")
        targets: dict[tuple[str, str], CandidateLinkRecord] = {}
        quotes = []
        observations = []
        observed_count = 0
        incomplete = not event.output_order_available
        raw_packs_complete = True
        contradicted = False
        for digest in packs:
            try:
                rows = self._index_capture_pack(event, digest)
            except _EvidenceGap as error:
                self._capture_gap(event, f"pack {digest}: {error}")
                incomplete = True
                raw_packs_complete = False
                continue
            except (TypeError, ValueError, OverflowError) as error:
                self.receipt_conflict(event, f"pack {digest}: {error}")
                contradicted = incomplete = True
                raw_packs_complete = False
                continue
            observed_count += len(rows)
            for index, raw in enumerate(rows):
                try:
                    quote = _capture_quote(raw, digest)
                    quotes.append(quote)
                    if not event.output_order_available:
                        continue
                    observation = _capture_observation(quote, self, event, digest, index, targets, incomplete)
                except _EvidenceGap as error:
                    self._capture_gap(event, f"pack {digest} item {index}: {error}")
                    incomplete = True
                except (TypeError, ValueError, OverflowError) as error:
                    self.receipt_conflict(event, f"pack {digest} item {index}: {error}")
                    contradicted = incomplete = True
                else:
                    observations.append(observation)
        summary_ok, summary_known = self._verify_capture_counts(
            event, summary, quotes, observations, observed_count, incomplete, raw_packs_complete,
        )
        if contradicted or not summary_ok:
            return
        verified = not incomplete and summary_known
        for observation in observations:
            if observation.item is None:
                continue
            item = observation.item
            link = self._capture_item_link(item)
            if link.failure or link.shortfall:
                verified = False
            elif not summary_known:
                link = _Link((event,), shortfall="capture success summary is unavailable (R3)")
            self.capture_links[item] = link
            self.capture_items[str(item.candidate.candidate_id)].append(item)
        if verified:
            for digest in dict.fromkeys(packs):
                self.pack_receipts[digest].append(event)

    def _capture_gap(self, event: RunEventRecord, detail: str) -> None:
        if self.is_current(event):
            self.capture_shortfalls.append(f"{_receipt_name(event)}: {detail} (R3)")

    def _index_capture_pack(self, event: RunEventRecord, digest: str) -> list:
        snapshot = self.snapshots.get(digest)
        if snapshot and (late := self.unavailable_by("snapshots", snapshot, event.occurred_at)):
            raise _CausalityError(late)
        absence = self.receipt_absence(event, "snapshots", row_key=digest)
        if absence:
            raise _CausalityError(f"registered snapshots row {digest!r} was still absent in {absence}")
        if not self.parent_available("snapshots", snapshot):
            raise _EvidenceGap("raw pack snapshot is unavailable")
        if problem := _snapshot_content_problem(snapshot):
            if snapshot.content is None or snapshot.byte_length is None:
                raise _EvidenceGap(problem)
            raise ValueError(problem)
        items = json.loads(snapshot.content)
        if not isinstance(items, list):
            raise ValueError("pack must be a JSON array")
        return items

    def _verify_capture_counts(
        self, receipt, summary, quotes, observations, observed_count, incomplete, raw_packs_complete,
    ):
        candidates = {observation.candidate.candidate_id: observation.candidate for observation in observations}
        priced_count = sum(quote.has_price for quote in quotes)
        close = sum(o.item is not None and o.item.kind == "CLOSE" for o in observations)
        sources = Counter(quote.source_kind for quote in quotes)
        downgraded = Counter(o.downgrade for o in observations if o.downgrade is not None)
        expected = {
            "candidates_seen": len(candidates), "observations": observed_count,
            "marks_written": priced_count, "close_eligible": close, "close_evidence_written": close,
            "missing_event_start": sum(candidate.event_start_at is None for candidate in candidates.values()),
            "missing_quote": len(quotes) - priced_count,
            "downgraded_to_periodic": sum(downgraded.values()),
            "downgrade_reasons": {name: downgraded[name] for name in
                                  ("missing_event_start", "observed_at_after_event_start")},
            "source_kind_counts": dict(sources),
        }
        missing = []
        conflict = False
        for name, count in expected.items():
            if name not in summary:
                missing.append(name)
                continue
            observed = summary[name]
            lower_bound = (
                not raw_packs_complete
                if name in ("observations", "marks_written", "missing_quote", "source_kind_counts")
                else incomplete
            )
            if isinstance(count, dict) and isinstance(observed, dict):
                good = all(type(value) is int and value >= 0 for value in observed.values())
                same = good and (observed == count if not lower_bound else all(
                    observed.get(key, 0) >= value for key, value in count.items()
                ))
            else:
                good = type(observed) is int and observed >= 0 and type(count) is int
                same = good and (observed >= count if lower_bound else observed == count)
            if not (good and same):
                expected_count = f"at least {count!r}" if lower_bound else repr(count)
                self.receipt_conflict(receipt, f"capture summary {name}={observed!r} disagrees with {expected_count}")
                conflict = True
        if missing:
            self._capture_gap(receipt, f"stable capture summary lacks {', '.join(missing)}")
        return not conflict, not missing

    def _capture_item_link(self, item: _CaptureItem) -> _Link:
        receipt = item.receipt
        location = f"pack {item.pack_digest} item {item.index}"
        required = [("candidate_marks", self.mark_rows, "candidate_mark_key")]
        if item.kind == "CLOSE":
            required.append(("candidate_price_evidence", self.price_rows, "evidence_key"))
        failed, missing = [], []
        for family, index, key_field in required:
            key = item.key(family)
            row = index.get(key)
            absence = self.receipt_absence(
                receipt, family, row_key=_row_id(family, row) if row else None,
                may_match=lambda _id, projection: projection is None or getattr(projection, key_field) in (None, key),
            )
            late = self.unavailable_by(family, row, receipt.occurred_at) if row else None
            if absence or late:
                failure = late or f"{family} key {key!r} was still absent in {absence}"
                failed.append(self.receipt_conflict(receipt, f"{location}: atomic capture counterpart {failure}"))
            elif not self.parent_available(family, row):
                missing.append(f"{family} key {key!r}")
            elif not item.matches(family, row):
                failed.append(self.receipt_conflict(
                    receipt, f"{location}: {family} key {key!r} disagrees with stored fields",
                ))
        if missing:
            self._capture_gap(receipt, f"{location}: atomic capture counterpart projection unavailable: {', '.join(missing)}")
        if failed:
            return _Link((receipt,), failure="; ".join(failed))
        if missing:
            return _Link((receipt,), shortfall=f"atomic capture counterpart unavailable: {', '.join(missing)} (R3)")
        return _Link((receipt,), proof=(f"{_receipt_name(receipt)} {location}",))

    def _missing_write(self, command: str, family: str, message: str) -> _Link:
        partial = self.partial_attempts.get(command, ())
        if partial:
            names = ", ".join(_receipt_name(event) for event in partial)
            return _Link(shortfall=f"{message}; per-item evidence unavailable for {names} (R3)")
        if command == CAPTURE_COMMAND and self.capture_shortfalls:
            return _Link(shortfall=f"{message}; capture pack or summary unavailable (R3)")
        if family in self.evidence.pending_contracts:
            return _Link(shortfall=f"{message}; pending receipt contract (R3)")
        return _Link(failure=message)

    def capture_link(self, family: str, row: CandidateChildRecord) -> _Link:
        parent = self._candidate_parent(row.candidate_id)
        if parent.failure:
            return parent
        key = row.evidence_key if family == "candidate_price_evidence" else row.candidate_mark_key
        claims = [
            self.capture_links[item] for item in self.capture_items.get(str(row.candidate_id), ())
            if item.key(family) == key and (family != "candidate_price_evidence" or item.kind == "CLOSE")
        ]
        valid = [claim for claim in claims if not (claim.failure or claim.shortfall)]
        if valid:
            return _Link(
                tuple(dict.fromkeys(receipt for claim in valid for receipt in claim.receipts)),
                proof=tuple(dict.fromkeys(
                    claim.proof[0] for claim in valid if self.is_current(claim.receipts[0])
                )),
            )
        if claims:
            receipts = tuple(dict.fromkeys(receipt for claim in claims for receipt in claim.receipts))
            failures = tuple(dict.fromkeys(claim.failure for claim in claims if claim.failure))
            if failures:
                return _Link(receipts, failure="; ".join(failures))
            return _Link(receipts, shortfall="; ".join(dict.fromkeys(claim.shortfall for claim in claims)))
        return self._missing_write(
            CAPTURE_COMMAND, "candidate_marks",
            f"candidate {row.candidate_id!r} key {key!r} has no exact successful capture-pack item",
        )

    def price_link(self, row: CandidateChildRecord) -> _Link:
        parent = self._candidate_parent(row.candidate_id)
        if parent.failure:
            return parent
        if row.evidence_role == "DECISION":
            candidate = self.candidates[str(row.candidate_id)]
            link = self.candidate_link(candidate)
            fields = (row.market_ref_kind, row.market_ref, row.side, row.source_kind)
            if type(row.candidate_id) is not int or any(not value for value in fields):
                return _Link(link.receipts, failure="scan DECISION evidence lacks writer key fields")
            expected = "|".join((
                "cpe", str(row.candidate_id), "DECISION", *fields,
                _utc(row.written_at).strftime("%Y%m%d%H%M%S"),
            ))
            if row.evidence_key != expected or _utc(row.written_at) != _utc(candidate.written_at):
                return _Link(link.receipts, failure="scan DECISION evidence key or timestamp disagrees")
            return self.scan_atomic_link("candidate_price_evidence", row, link)
        if row.evidence_role == "CLOSE":
            return self.capture_link("candidate_price_evidence", row)
        return _Link(failure="price-evidence role is absent or invalid")

    def settlement_link(self, row: EntryChildRecord) -> _Link:
        if id(row) not in self.settlement_links:
            self.settlement_links[id(row)] = self._settlement_link(row)
        return self.settlement_links[id(row)]

    def _settlement_link(self, row: EntryChildRecord) -> _Link:
        entry_id = str(row.entry_id)
        claims = self.settle_claims.get(entry_id, ())
        valid, failures, gaps = [], [], []
        for classification, receipt in claims:
            if classification != row.classification:
                failure = f"settle receipt classification conflicts for entry {entry_id}"
                failures.append(self.receipt_conflict(receipt, failure))
                continue
            link = self._settlement_item(row, receipt)
            if link.failure:
                failures.append(self.receipt_conflict(receipt, f"entry {entry_id}: {link.failure}"))
                if link.shortfall:
                    self.receipt_gap(receipt, f"entry {entry_id}: {link.shortfall.removesuffix(' (R3)')}")
            elif link.shortfall:
                gaps.append((receipt, link.shortfall))
            else:
                valid.append(receipt)

        if valid or failures:
            for receipt, detail in gaps:
                self.receipt_gap(receipt, f"entry {entry_id}: {detail.removesuffix(' (R3)')}")
        if valid:
            return _Link(
                tuple(valid),
                proof=tuple(
                    f"{_receipt_name(receipt)} settled:{entry_id}:{row.classification} "
                    f"({self.settlement_revisions[receipt.code_commit].source})"
                    for receipt in valid if self.is_current(receipt)
                ),
            )
        receipts = tuple(receipt for _classification, receipt in claims)
        if failures:
            return _Link(receipts, failure="; ".join(failures))
        if gaps:
            detail = "; ".join(
                f"{_receipt_name(receipt)} entry {entry_id}: {gap}" for receipt, gap in gaps
            )
            return _Link(receipts, shortfall=detail)

        parent = self._entry_parent(row.entry_id)
        if parent.failure:
            return parent
        pending = self.pending_entries.get(entry_id, ())
        detail = f"entry {entry_id} has no exact successful B3 item"
        if pending:
            detail += "; " + ", ".join(_receipt_name(event) for event in pending) + " reports it pending"
        return self._missing_write(SETTLE_COMMAND, "settlements", detail)

    def _missing_settlement_parent(self, receipt, family, row_key, views, *, label=None) -> _Link:
        label = label or f"settlement parent {family} row {row_key!r}"
        absence = self.dependency_absence(receipt, family, row_key=row_key, read_views=views)
        if absence:
            return _Link(failure=f"{label} absent in {absence}")
        return _Link(shortfall=f"{label} projection unavailable (R3)")

    def _settlement_item(self, row: EntryChildRecord, receipt: RunEventRecord) -> _Link:
        entry_id = row.entry_id
        planning, recheck, terminal_cash = self._settlement_views(receipt, entry_id)
        terminal = _utc(receipt.occurred_at)
        start = self.attempt_starts.get(_receipt_id(receipt)[:3])
        read_views = (("planning P", planning), (f"cash recheck V_{entry_id}", recheck),
                      ("terminal cash query", terminal_cash))

        entry = self.entries.get(str(entry_id))
        candidate = self.candidates.get(str(entry.candidate_id)) if entry is not None else None
        scan = self.scans.get(candidate.scan_id) if candidate is not None else None
        outcome_id = row.source_outcome_id
        outcome = self.outcomes.get(str(outcome_id)) if outcome_id not in (None, _MISSING) else None
        parents = [("entries", entry_id, entry, None)]
        if entry is not None:
            parents.append(("candidates", entry.candidate_id, candidate, None))
        if candidate is not None:
            parents.append(("scan_runs", candidate.scan_id, scan, None))
        if outcome_id not in (None, _MISSING):
            parents.append(("market_outcomes", outcome_id, outcome, f"settlement source_outcome_id {outcome_id!r}"))

        failures = []
        gaps = []
        for family, row_id, parent, label in parents:
            if parent is None:
                issue = self._missing_settlement_parent(
                    receipt, family, row_id, (view for _phase, view in read_views), label=label,
                )
                if issue.failure:
                    failures.append(issue.failure)
                else:
                    gaps.append(issue.shortfall)
                continue
            for phase, view in read_views:
                if self.read_state(family, parent, view, start, terminal) is False:
                    failures.append(f"{family} row {row_id!r} was absent at {phase}")
                    break
            else:
                if absence := self.dependency_absence(receipt, family, row_key=row_id):
                    failures.append(f"{family} row {row_id!r} was absent in {absence}")
                elif not self.parent_available(family, parent):
                    gaps.append(f"{family} row {row_id!r} projection unavailable in the visible window (R3)")
        if outcome_id is None:
            failures.append("settlement source_outcome_id is recorded as SQL NULL")
        elif outcome_id is _MISSING:
            gaps.append("settlement source_outcome_id is not projected (R3)")
        elif outcome is not None and outcome.outcome != row.outcome_label:
            failures.append(f"settlement source_outcome_id {outcome_id!r} is missing or inconsistent")

        cash_key = f"settle:{entry_id}"
        _settlement, cash, pair_link = self._settlement_pair(receipt, entry_id)
        if pair_link.failure:
            failures.append(pair_link.failure)
        if pair_link.shortfall:
            gaps.append(pair_link.shortfall)
        witness = self.settlement_reads.get((receipt.operation_key, receipt.attempt), {}).get(entry_id)
        key = _receipt_id(receipt)[:3]
        if (self.settle_planning_ids.get(key) and planning is None) or witness and any((
            witness.recheck_snapshot_id and recheck is None,
            witness.terminal_cash_snapshot_id and terminal_cash is None,
        )):
            gaps.append(f"unavailable P, V_{entry_id} or terminal cash inventory (R3)")
        if failures or gaps:
            return _Link(failure="; ".join(failures) or None, shortfall="; ".join(gaps) or None)
        for family, related in (("settlements", row), ("bankroll_events", cash)):
            if late := self.unavailable_by(family, related, receipt.occurred_at):
                return _Link(failure=late)

        if self.read_state("settlements", row, planning, start, terminal) is True:
            return _Link(failure=f"settlement {row.row_key!r} already existed at planning P")
        if terminal_cash is not None:
            if absent := self._pair_absences(entry_id, snapshot=terminal_cash):
                return _Link(failure=f"{', '.join(absent)} absent in terminal cash query {terminal_cash.snapshot_id!r}")

        reason = _BANKROLL_SETTLEMENT_REASONS.get(row.classification)
        if (reason is None or cash.entry_id != entry_id or cash.reason != reason
                or cash.scan_id is not None or cash.currency != "USD"
                or cash.reverses_event_id is not None or cash.is_legacy != row.is_legacy
                or row.evidence is not None or cash.note is not None
                or _utc(cash.occurred_at) != _utc(row.written_at)):
            return _Link(failure=f"settlement cash leg {cash_key!r} has conflicting metadata")
        extra_issue = self._settlement_extra_cash(receipt, row, cash, planning, recheck, terminal_cash, start)
        if extra_issue.failure or extra_issue.shortfall:
            return extra_issue

        revision = self.settlement_revisions.get(receipt.code_commit)
        if revision is None:
            return _Link(shortfall=f"unverified B3 writer revision for commit {receipt.code_commit!r} (R3)")

        stake, price, fee = _decimal(entry.stake), _decimal(entry.price), _decimal(entry.entry_fee)
        required = {
            "candidate.side": candidate.side, **{f"candidate.{column}": getattr(candidate, column)
                                                   for column in _MARKET_COLUMNS},
            "entry.price": entry.price, "entry.fee_source": entry.fee_source,
            "outcome.confirmed_at": outcome.confirmed_at, "outcome.source_kind": outcome.source_kind,
            **{f"outcome.{column}": getattr(outcome, column) for column in _MARKET_COLUMNS},
            **{f"settlement.{name}": getattr(row, name)
               for name in ("win_loss", "pnl_gross", "fee_total", "fee_source", "pnl_net",
                            "exit_price", "exit_mark_id", "confirmed_by")},
        }
        if revision.booked_fees:
            required["entry.is_legacy"] = entry.is_legacy
        elif row.classification == "NATURAL":
            required["entry.fee_category"] = entry.fee_category
        missing = [name for name, value in required.items() if value is _MISSING]
        if entry.stake is None:
            missing.append("entry.stake")
        if revision.booked_fees and entry.entry_fee is None:
            missing.append("entry.entry_fee")
        if missing:
            return _Link(shortfall=f"missing B3 projection {', '.join(missing)} (R3)")
        market_values = tuple(getattr(item, column) for item in (candidate, outcome)
                              for column in _MARKET_COLUMNS)
        if (type(entry_id) is not int or type(candidate.candidate_id) is not int
                or candidate.side not in store.SIDE_TOKENS or entry.fee_source not in store.FEE_SOURCES
                or any(value is not None and (type(value) is not str or not value.strip())
                       for value in market_values)
                or type(outcome.source_kind) is not str or not outcome.source_kind.strip()
                or stake is None or stake <= 0 or price is None or not 0 < price < 1
                or (revision.booked_fees and (fee is None or fee < 0 or type(entry.is_legacy) is not bool))
                or type(row.confirmed_by) is not str or not row.confirmed_by.strip()):
            return _Link(failure="settlement entry, candidate or operator projection has invalid fields")

        identity = settle.MarketIdentity(*(getattr(candidate, column) for column in _MARKET_COLUMNS))
        source_issue = self._settlement_source(
            row, entry, identity, outcome, revision, planning, start, terminal,
        )
        if source_issue.failure or source_issue.shortfall:
            return source_issue

        if revision.booked_fees:
            opening, issue = self._opening_cash_at_settlement(
                receipt, row, cash, entry, scan, stake, fee, planning, recheck, start,
            )
            if opening is None:
                return issue
            snapshot = settle.OpenSettleEntry(
                entry_id, candidate.candidate_id, "criterion-f", candidate.side, stake, price,
                "criterion-f", entry.written_at, fee_source=entry.fee_source,
                entry_fee=fee, is_legacy=entry.is_legacy, opening_cash=opening,
                outcome=_captured_outcome(outcome), **dict(identity.keys()),
            )
            expected = settle.plan_settlement(snapshot, operator=row.confirmed_by).record
            if expected is None:
                return _Link(failure=f"settlement source_outcome_id {outcome.outcome_id!r} cannot settle this entry")
            expected_cash = expected.cash_amount(entry_fee=opening.paid_fee)
        else:
            expected = _older_settlement_record(entry, candidate, outcome, stake, price)
            expected_cash = _decimal(expected.proceeds)

        for name in ("classification", "win_loss", "fee_source", "exit_price", "exit_mark_id", "is_legacy"):
            if getattr(row, name) != getattr(expected, name):
                return _Link(failure=f"settlement field {name} disagrees with the deployed B3 writer")
        for name in ("proceeds", "pnl_gross", "fee_total", "pnl_net"):
            if not _numeric_equal(getattr(row, name), getattr(expected, name)):
                return _Link(failure=f"settlement field {name} disagrees with the deployed B3 writer")
        if _utc(row.written_at) != _utc(expected.confirmed_at):
            return _Link(failure=f"settlement source_outcome_id {outcome.outcome_id!r} has conflicting confirmation time")
        if not _numeric_equal(cash.amount, expected_cash):
            return _Link(failure=f"settlement cash leg {cash_key!r} amount disagrees with the deployed B3 writer")
        return _Link()

    def _settlement_extra_cash(self, receipt, row, cash, planning, recheck, terminal_cash, start) -> _Link:
        terminal = _utc(receipt.occurred_at)
        uncertain = []
        for extra in self.cash.values():
            if (extra is cash or extra.entry_id != row.entry_id
                    or extra.reason not in store.SETTLEMENT_CASH_REASONS.values()
                    or not self.parent_available("bankroll_events", extra)):
                continue
            detail = f"settlement cash leg {cash.event_key!r}, extra event {extra.event_id!r}"
            states = tuple(self.read_state("bankroll_events", extra, view, start, terminal)
                           for view in (planning, recheck, terminal_cash))
            if True in states:
                return _Link(failure=f"{detail} was already visible by the terminal cash query")
            if states[-1] is False or self.receipt_absence(
                receipt, "bankroll_events", row_key=extra.event_id,
            ) or self._committed_before_competing_cash(receipt, row, cash, extra, planning, start):
                continue
            later_rechecks = any(
                self.inventory_state(view, "bankroll_events", extra) is False
                for view in self._later_settlement_reads(receipt, row.entry_id)
                if self.inventory_state(view, "settlements", row) is True
                and self.inventory_state(view, "bankroll_events", cash) is True
            )
            if not later_rechecks:
                uncertain.append(detail)
        if uncertain:
            return _Link(shortfall="; ".join(uncertain) + " visibility unproven at terminal cash query (R3)")
        return _Link()

    def _later_settlement_reads(self, receipt, entry_id):
        for later in self.settle_attempt_results.get(_receipt_id(receipt)[:3], ()):
            if later > entry_id:
                _planning, recheck, terminal_cash = self._settlement_views(receipt, later)
                yield from (view for view in (recheck, terminal_cash) if view is not None)

    def read_state(
        self, family: str, row: object, view: VisibilitySnapshot | None,
        earliest: datetime | None, latest: datetime,
    ) -> bool | None:
        """Place an immutable row relative to one bounded read."""
        if view is not None:
            state = self.inventory_state(view, family, row)
            if state is not None:
                return state
            earliest = latest = _utc(view.observed_at)
        if self.unavailable_by(family, row, latest):
            return False
        first_seen, _last_absent = self.observed_bounds(family, row)
        if earliest is not None and first_seen is not None and first_seen < earliest:
            return True
        return None

    def _settlement_source(
        self, row: EntryChildRecord, entry: EntryRecord, identity: settle.MarketIdentity,
        selected: MarketOutcomeRecord, revision: SettlementWriterRevision,
        planning: VisibilitySnapshot | None, earliest: datetime | None, terminal: datetime,
    ) -> _Link:
        selected_id = selected.outcome_id
        if (type(selected_id) is not int or selected_id < 1
                or selected.outcome not in (*store.NATURAL_OUTCOME_TOKENS, "VOIDED")):
            return _Link(failure=f"invalid settlement source_outcome_id {selected_id!r}")
        captured_selected = _captured_outcome(selected)
        if revision.booked_fees and (
            identity.match(captured_selected.identity) is None
            or _utc(selected.observed_at) < _utc(entry.written_at)
            or (selected.confirmed_at is not None and _utc(selected.confirmed_at) < _utc(entry.written_at))
        ):
            return _Link(failure=f"settlement source_outcome_id {selected_id!r} does not match this entry's "
                                 "typed market and holding period at planning P")

        def choose(observations):
            if revision.booked_fees:
                return settle.select_entry_outcome(entry.entry_id, entry.written_at, identity, observations)
            first_key = next(iter(identity.keys()), None)
            matching = [item for item in observations if first_key in item.identity.keys()]
            return max(matching, key=lambda item: (item.is_effective, _utc(item.observed_at), item.outcome_id),
                       default=None)

        certain, uncertain, missing = [captured_selected], [], []
        for _outcome_id, outcome in sorted(self.outcomes.items()):
            if outcome is selected or outcome.outcome == "UNKNOWN":
                continue
            state = self.read_state("market_outcomes", outcome, planning, earliest, terminal)
            if state is False:
                continue
            possible_fields = tuple(getattr(outcome, column) for column in _MARKET_COLUMNS)
            if _MISSING in possible_fields or outcome.confirmed_at is _MISSING:
                if any(
                    getattr(outcome, column) is _MISSING or
                    getattr(outcome, column) == value for column, value in identity.keys()
                ):
                    missing.append(outcome.outcome_id)
                continue
            captured = _captured_outcome(outcome)
            (certain if state else uncertain).append(captured)
        try:
            winner = choose(certain)
            possibilities = [(item.outcome_id, choose((*certain, item))) for item in uncertain]
        except (ValueError, TypeError) as error:
            return _Link(failure=f"settlement source_outcome_id {selected_id!r} has invalid market history: {error}")
        winner_id = winner.outcome_id if winner else None
        changing = [item_id for item_id, possible in possibilities
                    if (possible.outcome_id if possible else None) != winner_id]
        may_select_claimed = any(possible and possible.outcome_id == selected_id
                                 for _item_id, possible in possibilities)
        if missing or (changing and (winner_id == selected_id or may_select_claimed)):
            return _Link(shortfall=f"source_outcome_id {selected_id!r} planning P visibility/projection "
                                   f"unproven for outcome(s) {(*missing, *changing)!r} (R3)")
        if winner_id != selected_id:
            return _Link(failure=f"settlement source_outcome_id {selected_id!r} cannot be selected at planning P "
                                 f"(writer selects {winner_id!r})")
        return _Link()

    def _opening_cash_at_settlement(
        self, receipt: RunEventRecord, row: EntryChildRecord, cash: BankrollEventRecord,
        entry: EntryRecord, scan: ScanRunRecord, stake: Decimal, fee: Decimal,
        planning: VisibilitySnapshot | None, recheck: VisibilitySnapshot | None,
        earliest: datetime | None,
    ) -> tuple[settle.OpeningCash | None, _Link]:
        entry_id = entry.entry_id
        key = f"entry:{entry_id}:cash"
        expected = self.cash_by_key.get(key)
        terminal = _utc(receipt.occurred_at)
        if absence := self._cash_key_absence(receipt, key, read_views=(planning, recheck)):
            return None, _Link(failure=f"opening cash leg {key!r} was absent in {absence}")
        if expected is None:
            return None, _Link(shortfall=f"opening cash leg {key!r} and its historical coverage are missing (R3)")
        if (
            expected.entry_id != entry_id or expected.scan_id != scan.scan_id
            or expected.currency != "USD" or expected.reason != "SCAN_ENTRY"
            or expected.is_legacy != entry.is_legacy or expected.reverses_event_id is not None
            or _utc(expected.occurred_at) != _utc(entry.written_at)
        ):
            return None, _Link(failure=f"opening cash leg {key!r} event {expected.event_id!r} has conflicting metadata")
        opening_amount = _decimal(expected.amount)
        if (opening_amount != store.exact_sum(stake, fee).copy_negate()
                or store.exact_sum(opening_amount, stake).copy_negate() != fee):
            return None, _Link(failure=f"opening cash leg {key!r} event {expected.event_id!r} has conflicting amount")
        for phase, view in (("planning P", planning), (f"cash recheck V_{entry_id}", recheck)):
            if self.read_state("bankroll_events", expected, view, earliest, terminal) is False:
                return None, _Link(failure=f"opening cash leg {key!r} event {expected.event_id!r} was absent at {phase}")

        unresolved = []
        for _event_id, event in sorted(self.cash.items()):
            if event is expected or not self.parent_available("bankroll_events", event):
                continue
            if not ((event.entry_id == entry_id and event.reason == "SCAN_ENTRY")
                    or event.reverses_event_id == expected.event_id):
                continue
            states = tuple(self.read_state("bankroll_events", event, view, earliest, terminal)
                           for view in (planning, recheck))
            detail = f"entry_cash_mismatch: opening cash {key!r}, competing event {event.event_id!r}"
            if states[0] is True:
                return None, _Link(failure=f"{detail} was visible at planning P")
            if states[1] is True:
                return None, _Link(failure=f"{detail} was visible at cash recheck V_{entry_id}")
            if states[1] is False or self._committed_before_competing_cash(
                receipt, row, cash, event, planning, earliest,
            ):
                continue
            unresolved.append(detail)
        if unresolved:
            lower = _utc(planning.observed_at) if planning else earliest
            return None, _Link(shortfall="; ".join(unresolved) + f": unresolved planning P {lower}, "
                                         f"cash recheck V_{entry_id} before terminal T {terminal} (R3)")
        return settle.OpeningCash(expected.event_id, opening_amount, fee), _Link()

    def _committed_before_competing_cash(
        self, receipt: RunEventRecord, row: EntryChildRecord, cash: BankrollEventRecord,
        competing: BankrollEventRecord, planning: VisibilitySnapshot | None, earliest: datetime | None,
    ) -> bool:
        terminal = _utc(receipt.occurred_at)
        if self.read_state("settlements", row, planning, earliest, terminal) is not False:
            return False
        absent_at = (
            _utc(planning.observed_at) if planning is not None
            else self.observed_bounds("settlements", row)[1]
        )
        outside_writer = (
            self.unreceipted_write("settlements", row) or self.unreceipted_write("bankroll_events", cash)
        )
        for snapshot in self.inventories:
            observed = _utc(snapshot.observed_at)
            if absent_at is not None and observed <= absent_at:
                continue
            if (self.inventory_state(snapshot, "settlements", row) is not True
                    or self.inventory_state(snapshot, "bankroll_events", cash) is not True
                    or self.inventory_state(snapshot, "bankroll_events", competing) is not False):
                continue
            # A witness after the attempt proves its reads came before
            # the external row. An earlier witness bounds those reads only
            # when another attempt could not have committed the same pair.
            if self.inventory_after_receipt(snapshot, receipt) or (
                not outside_writer
                and not self._other_settle_attempt_possible(receipt, row, absent_at, observed, planning)
            ):
                return True
        return False

    def _other_settle_attempt_possible(self, receipt, row, absent_at, observed_at, absence_view) -> bool:
        current = _receipt_id(receipt)[:3]
        attempts: dict[tuple[str, str, int], list[RunEventRecord]] = defaultdict(list)
        for event in self.events:
            key = _receipt_id(event)[:3]
            if event.command == SETTLE_COMMAND and key != current:
                attempts[key].append(event)
        for key, phases in attempts.items():
            start = self.attempt_starts.get(key)
            if start is not None and start > observed_at:
                continue
            terminal = next((event for event in phases if event.phase in TERMINAL_PHASES), None)
            if terminal is not None and (
                (absent_at is not None and _utc(terminal.occurred_at) < absent_at)
                or (absence_view is not None and self.inventory_after_receipt(absence_view, terminal))
            ):
                continue
            results = self.settle_attempt_results.get(key)
            if results is not None and results.get(row.entry_id) != "settled":
                continue
            probe = terminal or phases[0]
            other_planning, _recheck, _cash = self._settlement_views(probe, row.entry_id)
            if other_planning is not None:
                planned_at = _utc(other_planning.observed_at)
                if (start is not None and planned_at < start) or (
                    terminal is not None and planned_at > _utc(terminal.occurred_at)
                ):
                    return True
                if planned_at > observed_at or self.inventory_state(other_planning, "settlements", row) is True:
                    continue
                plan = self.settlement_plans.get(key[1:])
                if plan is not None and plan.snapshot_id == other_planning.snapshot_id and (
                    row.entry_id in plan.checked_excluded_ids
                    or (plan.complete and row.entry_id not in plan.open_entry_ids)
                ):
                    continue
            return True
        return False

    def entry_mark_link(self, row: EntryChildRecord) -> _Link:
        parent = self._entry_parent(row.entry_id)
        if parent.failure:
            return parent
        return _Link(shortfall="entry mark has no production writer receipt (R3)")

    def outcome_link(self, row: MarketOutcomeRecord) -> _Link:
        if row.outcome_id is None and row.writer == "legacy_import":
            return _Link(shortfall=f"legacy_import outcome at {_utc(row.observed_at).isoformat()} "
                                   "has no physical outcome_id or verified writer receipt (R3)")
        if row.outcome_id is None and row.writer == SETTLE_COMMAND:
            if row.entry_id is None or str(row.entry_id) not in self.entries:
                return _Link(failure="writer=settle lacks a linked entry_id")
            return _Link(shortfall=f"legacy entry reference {row.entry_id!r} lacks a production outcome writer "
                                   "receipt; settle consumes outcomes (R3)")
        return _Link(shortfall="market_outcome has no production writer receipt; settle consumes outcomes (R3)")

    def cash_link(self, row: BankrollEventRecord) -> _Link:
        if row.reason == BANKROLL_REVERSAL_REASON:
            return self.reversal_link(row)
        if row.reason in BANKROLL_BASE_REASONS:
            return _Link()
        if row.reason == "SCAN_ENTRY":
            if row.scan_id is None or row.scan_id not in self.scans:
                return _Link(failure="SCAN_ENTRY lacks a receipt-proven scan_id")
            entry = self.entries.get(_str_id(row.entry_id))
            if not self.parent_available("entries", entry):
                return _Link(failure="SCAN_ENTRY lacks a linked entry_id")
            candidate = self.candidates.get(str(entry.candidate_id))
            if not self.parent_available("candidates", candidate) or candidate.scan_id != row.scan_id:
                return _Link(failure="SCAN_ENTRY entry and scan chains disagree")
            failure, shortfall = self._scan_cash_issue(row, entry, candidate.scan_id)
            if failure:
                return _Link(failure=failure)
            if shortfall:
                return _Link(shortfall=f"{shortfall} (R3)")
            return self.scan_atomic_link("bankroll_events", row, self.entry_link(entry))
        if row.reason in BANKROLL_ENTRY_LINKED_REASONS:
            entry_id = _str_id(row.entry_id)
            if row.entry_id is None or (
                entry_id not in self.entries
                and (row.event_key != f"settle:{row.entry_id}" or not self.settle_claims.get(entry_id))
            ):
                return _Link(failure=f"{row.reason} lacks a linked entry_id")
            if row.reason == "FEE":
                parent = self._entry_parent(row.entry_id)
                return parent if parent.failure else _Link(shortfall="FEE has no applicable per-event writer proof (R3)")
            settlement = self.settlements.get(_str_id(row.entry_id))
            if not self.parent_available("settlements", settlement):
                if row.event_key == f"settle:{row.entry_id}":
                    for _classification, receipt in self.settle_claims.get(str(row.entry_id), ()):
                        _settlement, _cash, link = self._settlement_pair(receipt, row.entry_id)
                        if link.failure or link.shortfall:
                            return link
                return _Link(failure=f"{row.reason} lacks its visible settlement")
            if row.event_key != f"settle:{row.entry_id}" or row.reason != _BANKROLL_SETTLEMENT_REASONS.get(settlement.classification):
                return _Link(
                    failure=f"{row.reason} has a conflicting settlement cash key or classification for entry {row.entry_id}",
                )
            return self.settlement_link(settlement)
        return _Link(failure=f"unknown reason {row.reason!r}")

    def reversal_link(self, row: BankrollEventRecord) -> _Link:
        source = row
        seen: set[str] = set()
        while source.reason == BANKROLL_REVERSAL_REASON:
            source_id = str(source.event_id)
            if source_id in seen:
                return _Link(failure=f"REVERSAL chain cycles at {source_id!r} (REVERSAL cycle)")
            seen.add(source_id)
            next_event = self.cash.get(_str_id(source.reverses_event_id))
            if next_event is source:
                return _Link(failure="REVERSAL chains to itself: a self-reversal proves nothing")
            if not self.parent_available("bankroll_events", next_event):
                return _Link(failure=f"REVERSAL does not chain via reverses_event_id {source.reverses_event_id!r}")
            seen_at, _absent = self.observed_bounds("bankroll_events", source)
            if seen_at is not None and self.unavailable_by("bankroll_events", next_event, seen_at):
                return _Link(failure=f"REVERSAL precedes its source event {next_event.event_id!r}")
            source = next_event
        link = self.cash_link(source)
        if (source.reason in BANKROLL_BASE_REASONS or not link.receipts
                or link.failure or link.shortfall or self.unreceipted_write("bankroll_events", source)):
            detail = link.failure or link.shortfall or "unreceipted source"
            return _Link(failure=f"REVERSAL chain terminates at {source.event_id!r} "
                                 f"which is not receipt-proven: {detail}")
        # Source lineage satisfies the chain, but does not establish the
        # reversal's own insertion interval.
        return _Link()

    def snapshot_link(self, row: SnapshotRecord) -> _Link:
        problem = _snapshot_content_problem(row)
        if problem is not None:
            if not _is_sha256(row.snapshot_id) and row.content is None:
                return _Link(failure="cited by neither an attributable candidate's snapshot_refs "
                                     "nor a receipt's evidence (R6c)")
            if row.content is None or row.byte_length is None:
                return _Link(shortfall=f"cannot verify snapshot content: {problem}")
            return _Link(failure=problem)
        registrations = list(self.pack_receipts.get(row.snapshot_id, ()))
        receipts = list(registrations)
        direct = _Link(tuple(
            event for event in self.events
            if event.command != CAPTURE_COMMAND
            and (row.snapshot_id in event.input_evidence or row.snapshot_id in event.output_evidence)
        ))
        direct = self.scan_atomic_link("snapshots", row, direct)
        receipts.extend(direct.receipts if not direct.failure else ())
        references = [
            item for items in self.capture_items.values() for item in items
            if item.snapshot_sha256 == row.snapshot_id
            and not (self.capture_links[item].failure or self.capture_links[item].shortfall)
        ]
        receipts.extend(item.receipt for item in references)
        for candidate in self.candidates.values():
            if row.snapshot_id in candidate.snapshot_refs and self.parent_available("candidates", candidate):
                link = self.candidate_link(candidate)
                if not link.failure and not link.shortfall:
                    link = self.scan_atomic_link("snapshots", row, link)
                    if not link.failure:
                        receipts.extend(link.receipts)
                        registrations.extend(link.receipts)
        if receipts:
            return _Link(tuple(dict.fromkeys(receipts)), historical_receipts=tuple(dict.fromkeys(registrations)))
        if row.kind == "capture_mark_pack" and (self.capture_shortfalls or self.partial_attempts.get(CAPTURE_COMMAND)):
            return _Link(shortfall="capture pack has no specific completed reference (R3)")
        return _Link(failure="cited by neither an attributable candidate's snapshot_refs nor a receipt's evidence (R6c)")

    def control_link(self, row: ControlEventRecord, matches: Sequence[ScanRunRecord]) -> _Link:
        if row.control_kind == QUEUE_CONTROL_RESUME:
            return _Link() if row.reviewed_by is not None else _Link(failure="RESUME lacks reviewed_by (R5b)")
        if len(matches) != 1:
            cause = "ambiguous-equality-match" if matches else "no-equality-match"
            return _Link(failure=cause)
        scan = matches[0]
        if row.scan_id is not None and row.scan_id != scan.scan_id:
            return _Link(failure="conflicting-scan-id")
        link = self.scan_link(scan)
        return link if link.receipts and not link.failure else _Link(failure="unattributable-scan")

    def shadow_link(self, row: CandidateChildRecord) -> _Link:
        parent = self._candidate_parent(row.candidate_id)
        if parent.failure:
            return parent
        link = self.candidate_link(self.candidates[str(row.candidate_id)])
        if link.failure:
            return link
        successful = tuple(event for event in link.receipts if event.phase == PHASE_SUCCEEDED)
        if not successful:
            return _Link(shortfall="shadow prediction lacks a successful scan confirmation (R6a)")
        return _Link(successful)


def _captured_outcome(row: MarketOutcomeRecord) -> settle.CapturedOutcome:
    return settle.CapturedOutcome(
        row.outcome_id, row.outcome, observed_at=row.observed_at, confirmed_at=row.confirmed_at,
        source_kind=row.source_kind,
        identity=settle.MarketIdentity(*(getattr(row, column) for column in _MARKET_COLUMNS)),
    )


def _older_settlement_record(entry, candidate, outcome, stake, price):
    # Earlier B3 used the fee schedule and refunded principal alone.
    naturally_settled = outcome.outcome != "VOIDED"
    if naturally_settled:
        realized = ledger.settle_natural(
            float(stake), float(price), candidate.side, outcome.outcome,
            fee_rate=fees.taker_fee_rate(entry.fee_category),
        )
        proceeds, gross, fee = (Decimal(str(value)) for value in
                                (realized.proceeds, realized.pnl, realized.fee_total))
        win_loss = realized.win_loss
    else:
        proceeds, gross, fee, win_loss = stake, Decimal(0), Decimal(0), None
    return store.SettlementRecord(
        classification="NATURAL" if naturally_settled else "VOIDED_REFUND",
        win_loss=win_loss, proceeds=proceeds, pnl_gross=gross, fee_total=fee,
        pnl_net=store.exact_sum(gross, fee.copy_negate()), fee_source=entry.fee_source,
        confirmed_at=outcome.confirmed_at or outcome.observed_at,
    )


def _str_id(row_id: int | str | None) -> str | None:
    return None if row_id is None else str(row_id)


def reconcile_universe(
    evidence: FEvidence,
    *,
    window_days: int = F_WINDOW_DAYS,
) -> UniverseReport:
    """Reconcile signed business families against the visible receipt index."""
    if evidence.window_start is None:
        raise ValueError("reconcile_universe requires a registered window_start")
    return _reconcile_universe(_LedgerEvidence(evidence, window_days))


def _reconcile_universe(ledger: _LedgerEvidence) -> UniverseReport:
    evidence = ledger.evidence
    families = []
    shortfalls = list(ledger.capture_shortfalls)
    pause_diagnostics = []
    resume_count = 0
    base_counts = Counter()

    def adjudicate(family: str, rows: Sequence, link_for) -> None:
        distinct = _unique_rows(rows, key=lambda row: _str_id(_row_id(family, row)),
                                label=f"{family} primary key")
        unnamed = [row for row in rows if _row_id(family, row) is None]
        in_window = matched = pre_anchor = post_window = 0
        unmatched = []
        disclosed = []
        for row in (*[distinct[key] for key in sorted(distinct)], *unnamed):
            row_key = _row_id(family, row)
            label = f"{family} row {row_key!r}"
            period = ledger.period(family, row)
            if period == "not-visible":
                continue
            if period == "post-window":
                post_window += 1
                continue
            if family == "bankroll_events" and row.reason in BANKROLL_BASE_REASONS:
                base_counts[row.reason] += 1
                if period == "pre-anchor":
                    pre_anchor += 1
                elif period == "in-window":
                    in_window += 1
                    if row.reason == "LEGACY_IMPORT":
                        source = ledger.unreceipted_write(family, row)
                        evidence_note = f", evidenced by {', '.join(source)}" if source else ""
                        unmatched.append(f"{label}: LEGACY_IMPORT in-window is an unmatched write (R6f){evidence_note}")
                    else:
                        matched += 1
                elif row.reason == "LEGACY_IMPORT":
                    shortfalls.append(f"{label}: LEGACY_IMPORT writing period unproven (R6e)")
                continue
            link = link_for(row)
            if (family == "market_outcomes" and row.outcome_id is None and row.writer == SETTLE_COMMAND
                    and ledger.start <= _utc(row.observed_at) < ledger.end
                    and (ledger.as_of is None or _utc(row.observed_at) <= ledger.as_of)
                    and link.failure):
                # The published anonymous shape makes a specific, malformed
                # writer declaration. It cannot establish any historical row.
                unmatched.append(f"{label}: {link.failure}")
                continue
            current_receipts = tuple(event for event in link.receipts if ledger.is_current(event))
            if period == "unknown" and not (link.failure or link.shortfall):
                historical = link.receipts if link.historical_receipts is None else link.historical_receipts
                if family != "shadow_predictions" and any(
                    event.phase == PHASE_SUCCEEDED and _utc(event.occurred_at) < ledger.start
                    for event in historical
                ):
                    period = "pre-anchor"
            if period == "pre-anchor":
                pre_anchor += 1
                if current_receipts:
                    disclosed.append(f"{label} existed pre-anchor and was reused by a window receipt (R6e)")
                else:
                    continue
            if not ledger.in_window(period, current_receipts):
                detail = link.failure or link.shortfall
                suffix = f"; {detail}" if detail else ""
                shortfalls.append(f"{label}: writing period unproven (R6e){suffix}")
                continue
            in_window += 1
            unreceipted = ledger.unreceipted_write(family, row)
            if period == "in-window" and unreceipted:
                unmatched.append(
                    f"{label}: unreceipted writer is unmatched in-window, evidenced by {', '.join(unreceipted)}"
                )
            elif link.failure:
                separator = " " if link.failure.startswith("references ") else ": "
                unmatched.append(f"{label}{separator}{link.failure}")
            elif link.shortfall:
                shortfalls.append(f"{label}: {link.shortfall}")
            elif unreceipted and period == "unknown":
                shortfalls.append(f"{label}: documented unreceipted write has no proven insertion period (R6e)")
            elif link.receipts and not current_receipts:
                unmatched.append(f"{label}: window row has only pre-anchor command evidence")
            elif row_key is None:
                shortfalls.append(f"{label}: actual row primary key is unavailable (R3)")
            else:
                matched += 1
                if current_receipts:
                    proof = ", ".join(link.proof or tuple(_receipt_name(event) for event in current_receipts))
                    disclosed.append(f"{label} linked to {proof}")
        families.append(FamilyAdjudication(
            family, in_window, matched, tuple(unmatched), pre_anchor, post_window, tuple(disclosed),
        ))

    scans_by_time: dict[datetime, list[ScanRunRecord]] = defaultdict(list)
    for scan in ledger.scans.values():
        if ledger.parent_available("scan_runs", scan):
            scans_by_time[_utc(scan.started_at)].append(scan)

    def control_link(row: ControlEventRecord) -> _Link:
        nonlocal resume_count
        link = ledger.control_link(row, scans_by_time[_utc(row.observed_at)])
        if link.failure and row.control_kind == QUEUE_CONTROL_PAUSE:
            operation, attempt = _nearest_started_hypothesis(ledger.current_events, row.observed_at)
            pause_diagnostics.append(PauseDiagnostic(
                row.observed_at, row.queue_id, operation, attempt, link.failure,
            ))
            return _Link(failure=f"no PROVEN attribution ({link.failure}); unproven PAUSE writes fail closed (C3)")
        if not link.failure and row.control_kind == QUEUE_CONTROL_RESUME:
            resume_count += 1
        return link

    adjudicate("scan_runs", evidence.scan_runs, ledger.scan_link)
    adjudicate("candidates", evidence.candidates, ledger.candidate_link)
    adjudicate("candidate_price_evidence", evidence.price_evidence, ledger.price_link)
    adjudicate("entries", evidence.entries, ledger.entry_link)
    adjudicate("settlements", evidence.settlements, ledger.settlement_link)
    adjudicate("bankroll_events", evidence.bankroll_events, ledger.cash_link)
    adjudicate("candidate_marks", evidence.candidate_marks,
               lambda row: ledger.capture_link("candidate_marks", row))
    adjudicate("market_outcomes", evidence.market_outcomes, ledger.outcome_link)
    adjudicate("marks", evidence.marks, ledger.entry_mark_link)
    adjudicate("snapshots", evidence.snapshots, ledger.snapshot_link)
    adjudicate("queue_control_events", evidence.control_events, control_link)
    adjudicate("shadow_predictions", evidence.shadow_predictions, ledger.shadow_link)

    for index, family in enumerate(families):
        if family.family == "bankroll_events" and base_counts:
            notes = ", ".join(f"{reason}={count}" for reason, count in sorted(base_counts.items()))
            families[index] = replace(family, disclosed=(*family.disclosed, f"base-reason rows (R6d): {notes}"))
        if family.family == "queue_control_events":
            notes = f"{resume_count} human RESUME event(s) in window with reviewed_by (R5b)"
            families[index] = replace(family, disclosed=(*family.disclosed, notes))
    return UniverseReport(
        families=tuple(families),
        unmatched_total=sum(len(family.unmatched) for family in families),
        pause_diagnostics=tuple(pause_diagnostics),
        resume_count=resume_count,
        shortfalls=(*shortfalls, *ledger.writer_shortfalls),
        writer_failures=tuple(ledger.writer_failures),
    )


# ---------------------------------------------------------------------------
# Verdict assembly
# ---------------------------------------------------------------------------


def evaluate_f_evidence(evidence: FEvidence) -> CriterionResult:
    """Evaluate the four signed sub-checks against one report snapshot."""
    key = "F"
    shortfalls = []
    if evidence.window_start is None:
        shortfalls.append(
            "cutover anchor not registered/supplied (R1): the 30-day window "
            "requires the first complete UTC day after D2"
        )
    if evidence.schedule is None:
        shortfalls.append(
            "schedule descriptor not registered/supplied (R4): expected "
            "slots come from the registered grid (C2)"
        )
    window_registered = not shortfalls
    replay_parameters = _resolve_replay_parameters(evidence)
    if isinstance(replay_parameters, str):
        shortfalls.append(replay_parameters)
    if not window_registered:
        if not evidence.run_events and not evidence.bankroll_checks:
            shortfalls.append("no run receipts and no bankroll checks at all within the visible window")
        return CriterionResult(key, VERDICT_NOT_ASSESSABLE, (*shortfalls, BOUNDARY_SENTENCE))
    if evidence.replay_agreements is not None:
        for candidate_id, agrees in evidence.replay_agreements.items():
            if type(agrees) is not bool:
                raise ValueError(f"replay result for candidate {candidate_id!r} must be a bool")

    ledger = _LedgerEvidence(evidence, F_WINDOW_DAYS)
    full_end = _full_window_end(ledger.start, ledger.end, evidence.as_of)
    days_elapsed = (full_end - ledger.start).days
    reasons: list[str] = []
    failures: list[str] = []
    if evidence.as_of is None:
        shortfalls.append("explicit as-of is missing; window maturity is unknown (R1)")
    elif days_elapsed < F_WINDOW_DAYS:
        shortfalls.append(
            f"window incomplete at as-of: {days_elapsed}/{F_WINDOW_DAYS} full "
            "UTC days elapsed (R1)"
        )
    if evidence.pending_contracts:
        shortfalls.append(
            "pending receipt contracts (R3): families " + ", ".join(evidence.pending_contracts)
        )

    universe = _reconcile_universe(ledger)
    failures.extend(universe.writer_failures)
    shortfalls.extend(universe.shortfalls)
    for family in universe.families:
        failures.extend(f"unlinked row ({family.family}): {detail}" for detail in family.unmatched)
        reasons.extend(f"disclosed ({family.family}): {note}" for note in family.disclosed)
    if universe.unmatched_total:
        failures.append(f"unmatched business rows: {universe.unmatched_total} > 0 (03:147 requires zero)")
    for diagnostic in universe.pause_diagnostics:
        reasons.append(
            f"diagnostic only: unproven PAUSE at {diagnostic.observed_at.isoformat()} "
            f"({diagnostic.cause}); nearest-STARTED hypothesis: "
            f"operation {diagnostic.hypothesis_operation_key!r} "
            f"attempt {diagnostic.hypothesis_attempt!r}"
        )

    check_receipts = any(
        ledger.start <= _utc(check.as_of) < ledger.end
        and (ledger.as_of is None or _utc(check.as_of) <= ledger.as_of)
        for check in ledger.checks
    )
    has_receipts = bool(ledger.current_events) or check_receipts
    if not has_receipts:
        shortfalls.append("no run receipts and no bankroll checks at all within the visible window")
    if evidence.as_of is not None:
        timeline = build_slot_timeline(
            ledger.events, evidence.window_start, evidence.schedule, as_of=evidence.as_of,
        )
        if has_receipts:
            for slot in timeline.uncovered:
                failures.append(
                    f"expected slot {slot.start.isoformat()} uncovered ({slot.status}): "
                    "every registered slot needs >=1 SUCCEEDED operation group (C1)"
                )
            for day in build_identity_days(
                ledger.checks, evidence.window_start, as_of=evidence.as_of,
            ):
                if day.failed_count:
                    status = "mixed" if day.ok else "failed"
                    target = reasons if day.ok else failures
                    target.append(
                        f"day {day.day.isoformat()} cash identity {status}: "
                        f"{day.passed_count} passed and {day.failed_count} receipt(s) with passed=false "
                        f"({f'check {day.check_id}, ' if day.check_id is not None else ''}"
                        f"recorded difference {_cash_number(day.reported_difference)} "
                        f"vs tolerance {_cash_number(day.reported_tolerance)})"
                    )
                elif not day.ok:
                    failures.append(
                        f"day {day.day.isoformat()} cash identity missing: "
                        "no in-day bankroll_checks receipt (03:145)"
                    )
        for slot in timeline.diagnostic_uncovered:
            if slot.start >= full_end:
                reasons.append(
                    f"current UTC day diagnostic: slot {slot.start.isoformat()} "
                    f"uncovered ({slot.status})"
                )

        reasons.append(
            f"window totals: {timeline.groups_succeeded} SUCCEEDED / "
            f"{timeline.groups_failed} FAILED / "
            f"{timeline.groups_nonterminal} NONTERMINAL operation groups, "
            f"{timeline.attempts_total} attempts ({timeline.retry_attempts} retry), "
            f"{timeline.attempts_succeeded} SUCCEEDED / {timeline.attempts_failed} FAILED / "
            f"{timeline.attempts_nonterminal} NONTERMINAL attempts, "
            f"{timeline.attempts_failed} failed, "
            f"distinct error codes {list(timeline.error_codes)}, "
            f"{timeline.non_scan_receipt_rows} non-run-scan receipt row(s), "
            f"{timeline.out_of_window_groups} out-of-window group(s)"
        )
        if timeline.immature:
            shortfalls.append(f"{len(timeline.immature)} slot(s) not yet mature (R1)")
        for group in timeline.other_groups:
            for attempt in group.attempts:
                if attempt.status != GROUP_SUCCEEDED:
                    reasons.append(
                        f"{group.command} operation {group.operation_key!r} attempt "
                        f"{attempt.attempt}: {attempt.status}, error {attempt.error_code!r}"
                    )

    if days_elapsed == F_WINDOW_DAYS and not isinstance(replay_parameters, str):
        seed, sample_n = replay_parameters
        _report_replay(ledger, failures, shortfalls, reasons, seed=seed, sample_n=sample_n)
    disclosed_pre_anchor = sum(family.pre_anchor for family in universe.families)
    if disclosed_pre_anchor:
        reasons.append(f"pre-anchor rows disclosed (R6e): {disclosed_pre_anchor} row(s)")
    reasons.append(BOUNDARY_SENTENCE)
    if failures:
        return CriterionResult(key, VERDICT_FAIL, tuple(failures + shortfalls + reasons))
    if shortfalls:
        return CriterionResult(key, VERDICT_NOT_ASSESSABLE, tuple(shortfalls + reasons))
    return CriterionResult(key, VERDICT_PASS, tuple(reasons))


def _cash_number(value: Decimal | float | int | None) -> str:
    return "not recorded" if value is None else str(value)


def _format_replay_integer(value: int) -> str:
    """Use exact hexadecimal if the interpreter limits decimal conversion."""
    try:
        return str(value)
    except ValueError:
        return hex(value)


def _report_replay(
    ledger: _LedgerEvidence, failures: list[str], shortfalls: list[str], reasons: list[str],
    *, seed: int, sample_n: int,
) -> None:
    registration = ledger.evidence.replay_registration
    registered = isinstance(registration, FReplayRegistration)
    sample_size = _format_replay_integer(sample_n)
    if registered:
        reasons.append(
            f"replay registration (R2) fixes N {sample_size}, seed {_format_replay_integer(seed)}, "
            f"rule {registration.rule_version}, and source SHA-256 {registration.source_sha256}"
        )
    sample = _draw_replay_sample(ledger, seed, sample_n)
    shortfalls.extend(sample.shortfalls)
    if sample.older_commit_ids:
        reasons.append(
            f"{sample.older_commit_count} in-window candidate(s) at older commits listed: "
            + ", ".join(sample.older_commit_ids)
        )
    if sample.trusted_commit is None:
        shortfalls.append("replay evidence absent: no trusted latest scan code_commit")
        return
    if sample.selected_ids is None:
        shortfalls.append(
            f"replay evidence absent: {len(sample.eligible_ids)} eligible candidate(s) "
            f"at trusted commit {sample.trusted_commit!r} < registered N {sample_size}"
        )
        return
    results = ledger.evidence.replay_agreements
    count = len(sample.selected_ids)
    missing = []
    mismatched = []
    for candidate_id in sample.selected_ids:
        agreement = results.get(candidate_id) if results is not None else None
        if agreement is None:
            missing.append(candidate_id)
            status = "missing"
        elif agreement is False:
            mismatched.append(candidate_id)
            status = "mismatched"
        else:
            status = "matched"
        if registered:
            reasons.append(
                f"replay result (R2) candidate {candidate_id!r} {status} "
                f"at trusted commit {sample.trusted_commit!r}"
            )
    if results is None:
        shortfalls.append(f"replay results not supplied for the {count} selected candidate(s) (R2)")
        return
    if mismatched:
        failures.append(
            "replay mismatch (03:146 全部一致): candidate(s) "
            + ", ".join(sorted(mismatched)) + " did not reproduce"
        )
    if missing:
        shortfalls.append(
            f"replay results incomplete: {len(missing)} of {count} selected candidate(s) "
            "lack a result: " + ", ".join(sorted(missing))
        )
    if not missing and not mismatched:
        reasons.append(
            f"replay sample: {count} of {len(sample.eligible_ids)} eligible at trusted "
            f"commit {sample.trusted_commit!r}, all agreed (seeded registered draw)"
        )
