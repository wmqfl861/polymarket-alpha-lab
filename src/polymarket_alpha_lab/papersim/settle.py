"""B3 settle orchestration (plan 05 section 2 / DELIVERY_PLAN 80.8).

One settle run: a clean-worktree code-commit check (FX-03 discipline,
reusing :func:`papersim.scan.require_clean_worktree` lazily and refusing
to start -- before ANY write -- on a dirty worktree), ONE consistent
snapshot of the open paper entries and their EFFECTIVE captured
final-outcome evidence, a pure per-entry settlement plan, and one
idempotent :func:`papersim.store_psycopg.settlement_record` write per
settleable entry (settlement row + its single cash leg, one transaction
per entry).  Run receipts follow the ``run_scan`` pattern through
:func:`papersim.store_psycopg.record_command_run_event` (STARTED ->
SUCCEEDED, FAILED on error), default operation key
``settle|<team>|<stamp>|b3``.

Causality (B2 ordering): settle reads ONLY outcome evidence already
captured in ``papersim.market_outcomes`` (the
``store_psycopg.effective_market_outcome`` selection semantics: latest
non-UNKNOWN observation per market identity, UNKNOWN-only coverage kept
as an explicit gap).  An entry with no captured terminal outcome (no
observation at all, or UNKNOWN-only) is recorded in the receipt as
PENDING with a reason and stays open; a late capture settles on the next
run.  ``confirmed_at`` of every settlement is taken from the captured
evidence's ``confirmed_at`` (falling back to its ``observed_at``) only --
never from the settle clock.  No adapter call, no network, no settling
from un-captured sources.

Scope boundary: early EXIT settlement stays manual / operator-attested
(``ledger.settle_exit`` plus ``exit_mark_id``) -- this module automates
NATURAL win/loss and VOIDED_REFUND only.  An exit-marked entry without
natural/voided evidence is reported PENDING with reason
``exit_manual_attestation``; an exit needs a decision, not just captured
evidence.

BASKETBALL FULL-GAME INVARIANT (pinned for B3): moneyline, spread and
total settlements are OT-INCLUSIVE across leagues.  Settle consumes
captured MARKET-LEVEL outcome tokens (YES / NO / YES-basket / VOIDED)
and NEVER recomputes an outcome from scores -- the score comparison that
produced the token happened in the capture lane, not here.  Where
final-score materials accompany the evidence they must be completed-game
scores: ESPN finished-game rows (``papersim.espn_nba._completed_events``
accepts only competition status ``completed`` with whole-number scores)
are final and OT-inclusive, so a captured token backed by a completed
box score already includes any overtime periods.  In-progress or partial
score materials are NON-evidence here: they leave the entry unresolved
(PENDING, reason ``score_materials_incomplete``) even when a terminal
token is present, because an unfinished game must never settle a
full-game market.

Idempotency: the store's UNIQUE ``settlements.entry_id`` plus the single
cash leg ``bankroll_events.event_key = settle:{entry_id}`` (both in
``store_psycopg.settlement_record``) make a re-run with identical content
a safe no-op (``inserted=False``); a divergent re-settle surfaces
``papersim_settlement_conflict`` and settlements are append-only, so a
conflict never updates anything.  Run receipts are idempotent under the
operation key (same key + same attempt + same phase + same content
returns the original row).

Phase boundary: every row this module writes is research/paper evidence
(``paper_only``/``report_only``/``readonly`` CHECK constraints on every
table).  There is no order, account, credential, signing, cancellation
or external-write surface of any kind; the paper boundary of the
underlying tables is the boundary of this command.

FX-29 integration contract (the multi-query read uses ONE consistent
snapshot): the open-entries snapshot is INJECTED as the ``open_entries``
callable so this module adds no database function of its own (fix-list
03 section 0 rule 5: new DB functions live in ``papersim/
store_psycopg.py``).  The contract is::

    open_entries(conn) -> Iterable[OpenSettleEntry]

One REPEATABLE READ READ ONLY transaction per call; the first statement
is ``SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY``; the
open entries (``papersim.entries`` LEFT JOIN their candidates, minus
already-settled ones) and every entry's effective captured outcome are
read inside that single snapshot.  The planned shared-file-pass
promotes the test-side loader
``tests/test_papersim_settle_store_native.py::open_entries_for_settlement``
into ``store_psycopg.open_entries_for_settlement`` verbatim; the CLI then
passes ``open_entries=store_psycopg.open_entries_for_settlement``.  The
per-entry writes stay in their own transactions exactly as
``settlement_record`` provides.

Import purity: this module imports only the pure layers
(``fees``, ``ledger``, ``store``) at module import time; ``scan`` (for
the worktree check) and ``store_psycopg`` (for the writers) are imported
lazily inside functions, so importing ``settle`` never loads a database
driver, an adapter, the CLI or the optimize/provider surface (the
model registry is irrelevant to settle: no provider consult exists).
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from polymarket_alpha_lab.papersim import fees, ledger, store

__all__ = [
    "CapturedOutcome",
    "OpenSettleEntry",
    "ScoreMaterials",
    "SETTLE_COMMAND",
    "SettleDecision",
    "SettleError",
    "captured_from_effective",
    "is_completed_game",
    "plan_all",
    "plan_settlement",
    "run_settle",
    "settle_commit_check",
]

SETTLE_COMMAND = "settle"

#: Decision outcomes of the pure planner (one per open entry).
STATUS_SETTLED = "SETTLED"
STATUS_PENDING = "PENDING"

#: Fixed pending reasons (receipt-visible, part of the module contract).
REASON_NO_OUTCOME = "no_captured_outcome"
REASON_UNKNOWN = "outcome_unknown"
REASON_SCORES_INCOMPLETE = "score_materials_incomplete"
REASON_EXIT_MANUAL = "exit_manual_attestation"

#: FX-03 scan refusal codes mapped onto the settle error alphabet.
_COMMIT_CODE_MAP = {
    "papersim_scan_git_error": "papersim_settle_git_error",
    "papersim_scan_head_invalid": "papersim_settle_head_invalid",
    "papersim_scan_worktree_dirty": "papersim_settle_worktree_dirty",
}


class SettleError(ValueError):
    """One settle-level refusal with a fixed code (never a silent skip)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Captured evidence (the already-captured terminal-outcome materials)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoreMaterials:
    """Optional final-score materials accompanying captured evidence.

    ``status`` mirrors the upstream competition state (the ESPN face uses
    ``completed`` for finished games; anything else -- ``in_progress``,
    ``scheduled``, ``halftime`` ... -- is a non-terminal material).
    Only completed-game materials are settlement evidence, and
    completed-game scores are OT-INCLUSIVE by construction (the game is
    finished; ESPN finished rows carry the whole-game final score).  The
    scores are NEVER interpreted here -- settle consumes the captured
    outcome token, the materials only gate completion.
    """

    status: str
    home_score: int | None = None
    away_score: int | None = None
    note: str | None = None


def is_completed_game(materials: ScoreMaterials) -> bool:
    """True only for completed-game materials with whole-number scores.

    ``None`` fields, non-int scores or any status other than
    ``completed`` (for example an in-progress or period-partial box)
    are NOT evidence -- the full game has not finished, so no
    full-game market may settle on them.
    """
    if not isinstance(materials, ScoreMaterials) or materials.status != "completed":
        return False
    for score in (materials.home_score, materials.away_score):
        if type(score) is not int or score < 0:
            return False
    return True


@dataclass(frozen=True)
class CapturedOutcome:
    """The effective captured outcome of one market (evidence only).

    Mirrors ``store.EffectiveMarketOutcome`` (latest non-UNKNOWN
    observation per market identity, UNKNOWN kept as an explicit
    coverage gap) plus the two fields settlement needs that the store
    dataclass does not carry: ``confirmed_at`` (the plan's
    confirmed-at-from-evidence rule) and the optional score materials.
    """

    outcome_id: int | None
    outcome: str
    observed_at: datetime.datetime | None = None
    confirmed_at: datetime.datetime | None = None
    source_kind: str | None = None
    score_materials: ScoreMaterials | None = None

    @property
    def is_effective(self) -> bool:
        return self.outcome != "UNKNOWN"

    @property
    def evidence_time(self) -> datetime.datetime | None:
        """``confirmed_at`` or ``observed_at`` -- evidence time only."""
        return self.confirmed_at or self.observed_at


def captured_from_effective(effective: store.EffectiveMarketOutcome, *,
                            confirmed_at: datetime.datetime | None = None,
                            score_materials: ScoreMaterials | None = None,
                            ) -> CapturedOutcome:
    """Lift one ``store.EffectiveMarketOutcome`` into a ``CapturedOutcome``.

    The loader (FX-29 snapshot) calls the audited
    ``store_psycopg.effective_market_outcome`` selection and re-reads the
    winning row's ``confirmed_at``; this helper keeps that lift in ONE
    place so the wired path and the tests share it.
    """
    return CapturedOutcome(
        outcome_id=effective.outcome_id,
        outcome=effective.outcome,
        observed_at=effective.observed_at,
        confirmed_at=confirmed_at,
        source_kind=effective.source_kind,
        score_materials=score_materials,
    )


# ---------------------------------------------------------------------------
# The open-entry snapshot (produced by the injected FX-29 loader)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenSettleEntry:
    """One open paper entry joined with its candidate identity + evidence.

    Produced by the injected ``open_entries`` loader inside ONE
    repeatable-read read-only snapshot: the entry row, its candidate's
    market identity (used, in ``condition_id`` / ``market_id`` /
    ``market_slug`` precedence, as the effective-outcome lookup key --
    the same precedence the evaluate loader uses), and the entry's
    effective captured outcome (``None`` when the loader found no
    observation at all).  Entries whose candidates carry no market
    identity can never match captured evidence and stay pending with
    ``no_captured_outcome`` -- no key is fabricated.
    """

    entry_id: int
    candidate_id: int
    team: str
    side: str
    stake: Decimal | float | int
    price: Decimal | float | int
    fee_category: str
    entered_at: datetime.datetime | None = None
    fee_source: str = "schedule"
    condition_id: str | None = None
    market_id: str | None = None
    market_slug: str | None = None
    exit_marked: bool = False
    outcome: CapturedOutcome | None = None

    @property
    def market_identity(self) -> str | None:
        """The outcome-lookup identity (evaluate-loader precedence)."""
        return self.condition_id or self.market_id or self.market_slug

    def validate(self) -> "OpenSettleEntry":
        def _snapshot_fail(field: str) -> None:
            raise SettleError("papersim_settle_snapshot_invalid",
                              f"entry {self.entry_id}: {field}")

        if type(self.entry_id) is not int or self.entry_id < 1:
            _snapshot_fail("entry_id must be a positive int")
        if type(self.candidate_id) is not int or self.candidate_id < 1:
            _snapshot_fail("candidate_id must be a positive int")
        for name, value in (("team", self.team), ("fee_category", self.fee_category)):
            if type(value) is not str or not value.strip():
                _snapshot_fail(f"{name} must be non-empty text")
        if self.side not in store.SIDE_TOKENS:
            _snapshot_fail(f"side {self.side!r}")
        for name, value in (("stake", self.stake), ("price", self.price)):
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                _snapshot_fail(f"{name} must be a number")
            if not Decimal(str(value)).is_finite():
                _snapshot_fail(f"{name} must be finite")
        if Decimal(str(self.stake)) <= 0:
            _snapshot_fail("stake must be positive")
        if not Decimal(0) < Decimal(str(self.price)) < Decimal(1):
            _snapshot_fail("price must be inside the open interval (0, 1)")
        if self.fee_source not in store.FEE_SOURCES:
            _snapshot_fail(f"fee_source {self.fee_source!r}")
        if self.entered_at is not None and (not isinstance(self.entered_at, datetime.datetime)
                                            or self.entered_at.tzinfo is None):
            _snapshot_fail("entered_at must be timezone-aware")
        if type(self.exit_marked) is not bool:
            _snapshot_fail("exit_marked must be bool")
        if self.outcome is not None and not isinstance(self.outcome, CapturedOutcome):
            _snapshot_fail("outcome must be a CapturedOutcome or None")
        return self


# ---------------------------------------------------------------------------
# The pure planner
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SettleDecision:
    """The planned outcome for one open entry (no write happened yet)."""

    entry_id: int
    status: str
    reason: str | None = None
    classification: str | None = None
    record: store.SettlementRecord | None = None


def plan_settlement(entry: OpenSettleEntry, *,
                    operator: str = "unattended") -> SettleDecision:
    """Derive one entry's settlement purely from captured evidence.

    Decision order (fixed and deterministic):

    1. No effective terminal token (no observation, or UNKNOWN-only
       coverage) -> PENDING: ``exit_manual_attestation`` when the entry
       is exit-marked (EXIT stays manual), else ``no_captured_outcome``
       (no observation row at all) or ``outcome_unknown`` (UNKNOWN-only).
       The entry stays open; a late capture settles on the next run.
    2. Terminal token accompanied by score materials that are NOT a
       completed game -> PENDING ``score_materials_incomplete``
       (in-progress/partial scores are non-evidence).
    3. Token ``VOIDED`` -> VOIDED_REFUND: the stake is refunded
       (``proceeds = stake``, ``pnl_gross = fee_total = pnl_net = 0``,
       never a win or a loss -- the frozen voided rule).
    4. Token in natural outcome tokens (YES / NO / YES-basket) ->
       NATURAL: economics via ``ledger.settle_natural`` (win pays
       ``stake / price`` at $1.00, loss pays 0; win/loss via the
       ``_natural_hit`` side semantics YES/YES-basket vs NO/NO-basket;
       fee via ``fees.taker_fee_rate(entry.fee_category)``).
    5. Any other token is snapshot corruption and fails loudly.

    ``confirmed_at`` of the produced record is the captured evidence's
    ``confirmed_at`` (fallback ``observed_at``) only; ``confirmed_by``
    is the settle operator identity; ``source_outcome_id`` binds the
    settlement to its evidence row (FK provenance).
    """
    entry.validate()
    captured = entry.outcome
    if captured is None or not captured.is_effective:
        if entry.exit_marked:
            reason = REASON_EXIT_MANUAL
        elif captured is None or captured.outcome_id is None:
            reason = REASON_NO_OUTCOME
        else:
            reason = REASON_UNKNOWN
        return SettleDecision(entry.entry_id, STATUS_PENDING, reason=reason)
    if captured.score_materials is not None \
            and not is_completed_game(captured.score_materials):
        return SettleDecision(entry.entry_id, STATUS_PENDING,
                              reason=REASON_SCORES_INCOMPLETE)
    confirmed_at = captured.evidence_time
    if confirmed_at is None:
        raise SettleError("papersim_settle_evidence_time_missing",
                          f"entry {entry.entry_id}: outcome {captured.outcome_id}"
                          " carries neither confirmed_at nor observed_at")
    if captured.outcome == "VOIDED":
        record = store.SettlementRecord(
            entry_id=entry.entry_id,
            classification="VOIDED_REFUND",
            outcome_label="VOIDED",
            win_loss=None,
            exit_price=None,
            exit_fee=0,
            proceeds=entry.stake,
            pnl_gross=0,
            fee_total=0,
            fee_source=entry.fee_source,
            pnl_net=0,
            source_outcome_id=captured.outcome_id,
            exit_mark_id=None,
            evidence=None,
            confirmed_by=operator,
            confirmed_at=confirmed_at,
            is_legacy=False,
        )
        return SettleDecision(entry.entry_id, STATUS_SETTLED,
                              classification="VOIDED_REFUND", record=record)
    if captured.outcome in store.NATURAL_OUTCOME_TOKENS:
        fee_rate = fees.taker_fee_rate(entry.fee_category)
        paper = ledger.settle_natural(float(entry.stake), float(entry.price),
                                      entry.side, captured.outcome,
                                      fee_rate=fee_rate)
        # The store CHECK (and its validator) require the exact decimal
        # identity ``pnl_net = pnl_gross - fee_total``; the ledger floats
        # are the canonical arithmetic, and the recorded triple carries
        # them as their exact repr decimals with the difference computed
        # in Decimal so the identity holds bit-for-bit in PostgreSQL.
        pnl_gross = Decimal(str(paper.pnl))
        fee_total = Decimal(str(paper.fee_total))
        record = store.SettlementRecord(
            entry_id=entry.entry_id,
            classification="NATURAL",
            outcome_label=captured.outcome,
            win_loss=paper.win_loss,
            exit_price=None,
            exit_fee=0,
            proceeds=Decimal(str(paper.proceeds)),
            pnl_gross=pnl_gross,
            fee_total=fee_total,
            fee_source=entry.fee_source,
            pnl_net=pnl_gross - fee_total,
            source_outcome_id=captured.outcome_id,
            exit_mark_id=None,
            evidence=None,
            confirmed_by=operator,
            confirmed_at=confirmed_at,
            is_legacy=False,
        )
        return SettleDecision(entry.entry_id, STATUS_SETTLED,
                              classification="NATURAL", record=record)
    raise SettleError("papersim_settle_outcome_token_invalid", str(captured.outcome))


def plan_all(entries: Iterable[OpenSettleEntry], *,
             operator: str = "unattended") -> tuple[SettleDecision, ...]:
    """Plan every snapshot entry (order preserved, entry_id ascending)."""
    planned = tuple(plan_settlement(entry, operator=operator)
                    for entry in sorted(entries, key=lambda item: item.entry_id))
    return planned


# ---------------------------------------------------------------------------
# FX-03 clean-worktree commit check (scan reuse, settle error alphabet)
# ---------------------------------------------------------------------------

def settle_commit_check(repo_root, *, git_bin: str = "git") -> str:
    """Return the verified HEAD commit; refuse ANY dirty worktree state.

    Behavior mirrors ``papersim.scan.require_clean_worktree`` exactly
    (this module REUSES that audited implementation via a lazy import --
    one clean-worktree check in the codebase, no fork); only the refusal
    codes are mapped onto the settle alphabet
    (``papersim_settle_worktree_dirty`` / ``papersim_settle_git_error``
    / ``papersim_settle_head_invalid``).
    """
    from polymarket_alpha_lab.papersim import scan  # lazy: keeps import surface pure

    try:
        return scan.require_clean_worktree(repo_root, git_bin=git_bin)
    except scan.ScanError as error:
        raise SettleError(_COMMIT_CODE_MAP.get(error.code,
                                               "papersim_settle_git_error"),
                          error.detail) from error


# ---------------------------------------------------------------------------
# The stateful driver
# ---------------------------------------------------------------------------

def run_settle(team: str, *, conn, repo_root, now_utc: Callable[[], datetime.datetime],
               open_entries: Callable[[Any], Iterable[OpenSettleEntry]],
               operator: str = "unattended",
               operation_key: str | None = None,
               attempt: int = 1,
               git_bin: str = "git",
               commit_check: Callable[[], str] | None = None,
               settle_writer: Callable | None = None,
               receipt_writer: Callable | None = None) -> dict:
    """Run one settle pass against the store (receipts + per-entry writes).

    Every external dependency is an explicit argument so the offline
    suite can drive the whole orchestration through injected fakes and
    the shared-file pass can wire the real faces with a minimal delta:
    ``conn`` is the managed/explicit application connection (as the CLI
    opens it), ``open_entries`` is the FX-29 repeatable-read snapshot
    loader (the planned ``store_psycopg.open_entries_for_settlement``),
    ``commit_check`` defaults to :func:`settle_commit_check`,
    ``settle_writer`` defaults to ``store_psycopg.settlement_record``
    and ``receipt_writer`` to ``store_psycopg.record_command_run_event``
    (both resolved lazily).

    Order: clean-worktree commit check (refuses before ANY write, so a
    refusal leaves no receipt either), STARTED receipt, ONE snapshot
    load, the pure plan, one idempotent settlement write per SETTLED
    decision (each its own transaction -- already-written settlements
    are independent append-only facts and stay written if a later entry
    conflicts), SUCCEEDED receipt carrying one evidence line per entry
    (``settled:<id>:<classification>`` / ``pending:<id>:<reason>``).  A
    failure anywhere after STARTED appends a FAILED receipt with the
    error code and re-raises.

    Retry discipline: a RETRY of the same operation must advance
    ``attempt``.  The SUCCEEDED evidence describes what THIS run found
    (a retry after a fully successful first attempt finds no open
    entries), so replaying the same operation key at the SAME attempt
    with different store state is refused by the receipt writer as
    ``papersim_command_run_conflict`` -- the loud, truthful outcome;
    settlement idempotency itself lives one layer down in
    ``settlement_record`` (identical content -> ``inserted=False``).

    The return value is JSON-ready (the CLI prints it verbatim):
    ``operation_key``, ``command``, ``team``, ``code_commit``,
    ``settled`` (list of per-entry write results), ``pending`` (list of
    ``{entry_id, reason}``), ``settled_count``, ``pending_count`` and
    ``snapshot_entries``.
    """
    from polymarket_alpha_lab.papersim import store_psycopg  # lazy: driver only

    if type(team) is not str or not team.strip():
        raise SettleError("papersim_settle_team_invalid")
    if type(operator) is not str or not operator.strip():
        raise SettleError("papersim_settle_operator_invalid")
    if type(attempt) is not int or attempt < 1:
        raise SettleError("papersim_settle_attempt_invalid")
    if not callable(open_entries):
        raise SettleError("papersim_settle_loader_invalid")
    check = commit_check if commit_check is not None \
        else (lambda: settle_commit_check(repo_root, git_bin=git_bin))
    write_settlement = settle_writer if settle_writer is not None \
        else store_psycopg.settlement_record
    write_receipt = receipt_writer if receipt_writer is not None \
        else store_psycopg.record_command_run_event

    code_commit = check()
    started_at = now_utc()
    receipt_key = operation_key if operation_key is not None else (
        f"{SETTLE_COMMAND}|{team}|"
        f"{started_at.astimezone(datetime.UTC):%Y%m%dT%H%M%SZ}|b3"
    )

    def _receipt(phase: str, *, error_code: str | None = None,
                 output_evidence: Iterable[str] = ()) -> None:
        write_receipt(conn, store.CommandRunEventSubmission(
            operation_key=receipt_key,
            attempt=attempt,
            phase=phase,
            command=SETTLE_COMMAND,
            occurred_at=now_utc(),
            code_commit=code_commit,
            output_evidence=list(output_evidence),
            error_code=error_code,
        ))

    _receipt("STARTED")
    try:
        snapshot = tuple(open_entries(conn))
        decisions = plan_all(snapshot, operator=operator)
        settled: list[dict] = []
        pending: list[dict] = []
        evidence: list[str] = []
        for decision in decisions:
            if decision.status == STATUS_PENDING:
                pending.append({"entry_id": decision.entry_id,
                                "reason": decision.reason})
                evidence.append(f"pending:{decision.entry_id}:{decision.reason}")
                continue
            written = write_settlement(conn, decision.record)
            settled.append({
                "entry_id": decision.entry_id,
                "classification": decision.classification,
                "settlement_id": written["settlement_id"],
                "inserted": written["inserted"],
                "bankroll_event_id": written["bankroll_event_id"],
            })
            evidence.append(f"settled:{decision.entry_id}:{decision.classification}")
        _receipt("SUCCEEDED", output_evidence=evidence)
        return {
            "operation_key": receipt_key,
            "command": SETTLE_COMMAND,
            "team": team,
            "code_commit": code_commit,
            "settled": settled,
            "pending": pending,
            "settled_count": len(settled),
            "pending_count": len(pending),
            "snapshot_entries": len(decisions),
        }
    except Exception as error:  # noqa: BLE001 - the receipt is the record
        try:
            _receipt("FAILED", error_code=str(getattr(error, "code",
                                                     type(error).__name__)))
        finally:
            raise
