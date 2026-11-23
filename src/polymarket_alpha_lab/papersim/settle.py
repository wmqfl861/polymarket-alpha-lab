"""B3 settle orchestration (plan 05 section 2 / DELIVERY_PLAN 80.8).

One settle run: a clean-worktree code-commit check (FX-03 discipline,
reusing :func:`papersim.scan.require_clean_worktree` lazily and refusing
to start -- before ANY write -- on a dirty worktree), ONE consistent
snapshot of the open paper entries, their opening debits and applicable
captured outcome evidence, a pure per-entry settlement plan, and one
guarded idempotent write per settleable entry. The settlement row and its
single cash leg share a transaction. Run receipts follow the ``run_scan`` pattern through
:func:`papersim.store_psycopg.record_command_run_event` (STARTED ->
SUCCEEDED, FAILED on error), default operation key
``settle|<team>|<stamp>|b3``.

Causality (B2 ordering): settle reads ONLY outcome evidence already
captured in ``papersim.market_outcomes``. Each open entry needs an
observation attributable to its own holding period and typed identity.
An entry with no applicable terminal outcome (no
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
open entries, their applicable captured outcomes and their opening cash
are read inside that snapshot. Per-entry writes revalidate the selected
immutable source and opening cash in their own transactions.

Import purity: this module imports only the pure layers
(``ledger``, ``store``) at module import time; ``scan`` (for
the worktree check) and ``store_psycopg`` (for the writers) are imported
lazily inside functions, so importing ``settle`` never loads a database
driver, an adapter, the CLI or the optimize/provider surface (the
model registry is irrelevant to settle: no provider consult exists).
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from polymarket_alpha_lab.papersim import ledger, store

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
REASON_ENTRY_CASH = "entry_cash_mismatch"

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


MARKET_IDENTITY_COLUMNS = ("condition_id", "market_id", "market_slug")


@dataclass(frozen=True)
class MarketIdentity:
    condition_id: str | None = None
    market_id: str | None = None
    market_slug: str | None = None

    @classmethod
    def from_row(cls, row: Mapping) -> "MarketIdentity":
        return cls(*(row[column] for column in MARKET_IDENTITY_COLUMNS))

    def keys(self) -> tuple[tuple[str, str], ...]:
        return tuple((column, value) for column in MARKET_IDENTITY_COLUMNS
                     if (value := getattr(self, column)) is not None)

    def match(self, other: "MarketIdentity") -> tuple[str, str] | None:
        for column in MARKET_IDENTITY_COLUMNS[:2]:
            left, right = getattr(self, column), getattr(other, column)
            if left is not None and right is not None and left != right:
                return None
        return next((key for key in self.keys() if key in other.keys()), None)


def utc_instant(value: datetime.datetime | None) -> datetime.datetime | None:
    if (isinstance(value, datetime.datetime) and value.tzinfo is not None
            and value.utcoffset() is not None):
        return value.astimezone(datetime.UTC)
    return None


@dataclass(frozen=True)
class CapturedOutcome:
    """One captured market observation, including its typed identities."""

    outcome_id: int | None
    outcome: str
    observed_at: datetime.datetime | None = None
    confirmed_at: datetime.datetime | None = None
    source_kind: str | None = None
    score_materials: ScoreMaterials | None = None
    identity: MarketIdentity = MarketIdentity()

    @classmethod
    def from_row(cls, row: Mapping) -> "CapturedOutcome":
        return cls(
            outcome_id=row["outcome_id"], outcome=row["outcome"],
            observed_at=row["observed_at"], confirmed_at=row["confirmed_at"],
            source_kind=row["source_kind"], identity=MarketIdentity.from_row(row),
        )

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

    Market-level consumers may carry that result independently of an entry.
    """
    return CapturedOutcome(
        outcome_id=effective.outcome_id,
        outcome=effective.outcome,
        observed_at=effective.observed_at,
        confirmed_at=confirmed_at,
        source_kind=effective.source_kind,
        score_materials=score_materials,
    )


def index_captured_outcomes(rows: Iterable[Mapping]) -> dict:
    """Index the captured history by each typed identity it carries."""
    indexed: dict[tuple[str, str], list[CapturedOutcome]] = {}
    for row in rows:
        captured = CapturedOutcome.from_row(row)
        for key in captured.identity.keys():
            indexed.setdefault(key, []).append(captured)
    return indexed


def captured_for_identity(index: Mapping, identity: MarketIdentity):
    return {row.outcome_id: row for key in identity.keys()
            for row in index.get(key, ())}.values()


def select_entry_outcome(entry_id: int, entered_at: datetime.datetime,
                         identity: MarketIdentity,
                         observations: Iterable[CapturedOutcome]) -> CapturedOutcome | None:
    """Select the highest-priority captured outcome attributable to this entry."""
    entered = utc_instant(entered_at)
    if entered is None:
        raise SettleError("papersim_settle_snapshot_invalid",
                          f"entry {entry_id}: entered_at must be timezone-aware")
    ranked = []
    prior_terminal = False
    for captured in observations:
        match = identity.match(captured.identity)
        if match is None or (captured.outcome_id is None and not captured.is_effective):
            continue
        if captured.outcome not in store.OUTCOME_TOKENS:
            raise SettleError("papersim_settle_outcome_token_invalid", str(captured.outcome))
        detail = f"entry {entry_id}: outcome {captured.outcome_id}"
        if captured.observed_at is None and captured.confirmed_at is None:
            raise SettleError("papersim_settle_evidence_time_missing", detail)
        observed = utc_instant(captured.observed_at)
        confirmed = utc_instant(captured.confirmed_at)
        if observed is None or (captured.confirmed_at is not None and confirmed is None):
            raise SettleError("papersim_settle_evidence_time_invalid", detail)
        if type(captured.outcome_id) is not int or captured.outcome_id < 1:
            raise SettleError("papersim_settle_evidence_id_invalid", detail)
        if observed < entered or (confirmed is not None and confirmed < entered):
            prior_terminal |= captured.is_effective
            continue
        strength = -MARKET_IDENTITY_COLUMNS.index(match[0])
        priority = (captured.is_effective, strength, observed, captured.outcome_id)
        ranked.append((priority, captured))

    eligible = ((priority, row) for priority, row in ranked
                if not prior_terminal or not row.is_effective or row.confirmed_at is not None)
    return max(eligible, default=(None, None), key=lambda item: item[0])[1]


@dataclass(frozen=True)
class OpeningCash:
    event_id: int
    amount: Decimal
    paid_fee: Decimal


@dataclass(frozen=True)
class EntryCashDiscrepancy:
    entry_id: int
    state: str
    event_ids: tuple[int, ...] = ()
    expected_amount: Decimal | None = None
    actual_amount: Decimal | None = None

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "event_ids": list(self.event_ids),
            "expected_amount": None if self.expected_amount is None
            else str(self.expected_amount),
            "actual_amount": None if self.actual_amount is None else str(self.actual_amount),
        }


class EntryCashMismatch(store.PapersimStoreError):
    def __init__(self, discrepancy: EntryCashDiscrepancy):
        super().__init__(REASON_ENTRY_CASH)
        self.discrepancy = discrepancy


@dataclass(frozen=True)
class AutomaticSettlement:
    outcome: CapturedOutcome
    market_match: tuple[str, str]
    opening_cash: OpeningCash
    d1_binding: dict | None = None


# ---------------------------------------------------------------------------
# The open-entry snapshot (produced by the injected FX-29 loader)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenSettleEntry:
    """One open entry with the outcome and opening cash visible in its snapshot."""

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
    entry_fee: Decimal | float | int | None = None
    is_legacy: bool = False
    opening_cash: OpeningCash | None = None
    cash_discrepancy: EntryCashDiscrepancy | None = None

    @property
    def identity(self) -> MarketIdentity:
        return MarketIdentity(self.condition_id, self.market_id, self.market_slug)

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
        if utc_instant(self.entered_at) is None:
            _snapshot_fail("entered_at must be timezone-aware")
        for name in ("exit_marked", "is_legacy"):
            if type(getattr(self, name)) is not bool:
                _snapshot_fail(f"{name} must be bool")
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
    automatic: AutomaticSettlement | None = None
    cash_discrepancy: EntryCashDiscrepancy | None = None


def plan_settlement(entry: OpenSettleEntry, *,
                    operator: str = "unattended") -> SettleDecision:
    """Derive the ledger realization from applicable evidence and booked cash."""
    entry.validate()
    observations = () if entry.outcome is None else (entry.outcome,)
    captured = select_entry_outcome(entry.entry_id, entry.entered_at, entry.identity,
                                    observations)
    if captured is None or not captured.is_effective:
        if entry.exit_marked:
            reason = REASON_EXIT_MANUAL
        elif captured is None:
            reason = REASON_NO_OUTCOME
        else:
            reason = REASON_UNKNOWN
        return SettleDecision(entry.entry_id, STATUS_PENDING, reason=reason)
    if captured.score_materials is not None and not is_completed_game(captured.score_materials):
        return SettleDecision(entry.entry_id, STATUS_PENDING, reason=REASON_SCORES_INCOMPLETE)
    if entry.cash_discrepancy is not None:
        return SettleDecision(entry.entry_id, STATUS_PENDING, reason=REASON_ENTRY_CASH,
                              cash_discrepancy=entry.cash_discrepancy)

    stake = store._as_decimal(entry.stake)
    fee = store._as_decimal(store._require_number(entry.entry_fee, "entry_fee"))
    if fee < 0:
        raise SettleError("papersim_settle_snapshot_invalid",
                          f"entry {entry.entry_id}: entry_fee must be nonnegative")
    automatic = None
    if entry.opening_cash is not None:
        automatic = AutomaticSettlement(captured, entry.identity.match(captured.identity),
                                        entry.opening_cash)
        fee = entry.opening_cash.paid_fee

    if captured.outcome == "VOIDED":
        classification = "VOIDED_REFUND"
        win_loss = None
        proceeds = stake
        gross = fee_total = net = Decimal(0)
    else:
        classification = "NATURAL"
        paper = ledger.settle_natural(float(entry.stake), float(entry.price),
                                      entry.side, captured.outcome)
        win_loss = paper.win_loss
        # Keep the frozen payoff representation, then account for it exactly.
        proceeds = Decimal(str(paper.proceeds))
        gross = store.exact_sum(proceeds, stake.copy_negate())
        fee_total = fee
        net = store.exact_sum(gross, fee_total.copy_negate())
    record = store.SettlementRecord(
        entry_id=entry.entry_id,
        classification=classification,
        outcome_label=captured.outcome,
        win_loss=win_loss,
        proceeds=proceeds,
        pnl_gross=gross,
        fee_total=fee_total,
        fee_source=entry.fee_source,
        pnl_net=net,
        source_outcome_id=captured.outcome_id,
        confirmed_by=operator,
        confirmed_at=utc_instant(captured.evidence_time),
        is_legacy=entry.is_legacy,
    )
    return SettleDecision(entry.entry_id, STATUS_SETTLED, classification=classification,
                          record=record, automatic=automatic)


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
# D1 settlement preparation and entry-specific evidence
# ---------------------------------------------------------------------------

_D1_RESOLUTION_SOURCE = "d1:public-resolution:"
_D1_SIDE = {"YES": "YES", "NO": "NO", "YES-basket": "YES", "NO-basket": "NO"}


def _d1_time(value) -> datetime.datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    return utc_instant(value)


def _d1_require(condition, code: str) -> None:
    if not condition:
        raise SettleError(code)


def _d1_snapshot(conn, digest: str | None) -> dict:
    from polymarket_alpha_lab.papersim import store_psycopg

    _d1_require(digest is not None, "d1_settlement_snapshot_missing")
    try:
        row = store_psycopg.d1_read_settlement_snapshot(conn, digest)
    except store.PapersimStoreError as error:
        if error.code.startswith("papersim_snapshot_") or error.code == "d1_settlement_source_snapshot_conflict":
            raise SettleError(error.code) from error
        raise
    _d1_require(row is not None, "d1_settlement_snapshot_missing")
    return row


def _d1_citation(snapshot: dict) -> dict:
    return {key: snapshot[key] for key in (
        "content_sha256", "byte_length", "kind", "source", "fetched_at", "schema_version")}


def _d1_cites(row: dict, snapshot: dict) -> bool:
    source = snapshot["source"]
    return row.get("source_ref") in (source, f"{source}#{snapshot['content_sha256']}")


def _d1_buy_book(conn, candidate: dict, decision: dict, token: str, entered_at) -> dict:
    from polymarket_alpha_lab.papersim.adapters import polymarket
    from polymarket_alpha_lab.papersim.d1compare import acquire, journal

    snapshot = _d1_snapshot(conn, decision["snapshot_sha256"])
    digest, condition = snapshot["content_sha256"], candidate["condition_id"]
    refs = candidate["snapshot_refs"]
    _d1_require(isinstance(refs, list) and digest in refs
                and decision["source_kind"] == "clob_book" and _d1_cites(decision, snapshot)
                and snapshot["source"] == f"{polymarket.CLOB_BOOK_URL}?{urlencode({'token_id': token})}"
                and snapshot["kind"] in (polymarket.SOURCE_KIND_CLOB, acquire._BODY_KIND),
                "d1_settlement_buy_book_source_mismatch")
    try:
        body = journal.decode_public_json(snapshot["content"])
        book = polymarket._parse_order_book(token, body)
    except (journal.JournalError, polymarket._MarketRejected, ValueError, OverflowError) as error:
        raise SettleError("d1_settlement_buy_book_invalid") from error
    _d1_require(body.get("asset_id") == token and body.get("market") == condition
                and book.best_ask is not None, "d1_settlement_buy_book_identity_mismatch")
    observed, decision_at = _d1_time(book.book_observed_at), _d1_time(decision["observed_at"])
    fetched = _d1_time(snapshot["fetched_at"])
    _d1_require(None not in (observed, fetched, decision_at, entered_at)
                and observed <= fetched <= decision_at <= entered_at,
                "d1_settlement_buy_book_time_mismatch")
    return {**_d1_citation(snapshot), "source_observed_at": observed, "token_id": token,
            "condition_id": condition}


def _d1_buy_binding(conn, entry: dict, candidate: dict | None) -> dict:
    from polymarket_alpha_lab import replay

    _d1_require(candidate is not None and candidate["candidate_id"] == entry["candidate_id"]
                and candidate["scan_id"] == entry["scan_id"] and candidate["team"] == entry["team"]
                and candidate["scan_team"] == entry["team"] and candidate["side"] == entry["side"]
                and candidate["market_id"] and candidate["condition_id"]
                and MarketIdentity.from_row(candidate) == MarketIdentity.from_row(entry),
                "d1_settlement_candidate_binding_missing")
    receipts = {row["phase"]: row for row in candidate["scan_receipts"]}
    started, succeeded = receipts.get("STARTED"), receipts.get("SUCCEEDED")
    _d1_require(started is not None and succeeded is not None
                and started["attempt"] == succeeded["attempt"]
                and started["code_commit"] == succeeded["code_commit"] == candidate["scan_code_commit"]
                and succeeded["scan_id"] == candidate["scan_id"]
                and candidate["scan_started_at"] <= started["occurred_at"]
                <= candidate["scan_finished_at"] <= succeeded["occurred_at"],
                "d1_settlement_scan_receipt_missing")

    yes, no = candidate["yes_token_id"], candidate["no_token_id"]
    side = entry["side"]
    _d1_require(yes and no and yes != no and side in _D1_SIDE, "d1_settlement_buy_token_unproven")
    side_tokens = {"YES": yes, "NO": no}
    buy_token = side_tokens[_D1_SIDE[side]]
    try:
        request = replay.request_from_replay_bundle(candidate["input_payload"])
    except replay.PapersimReplayError as error:
        raise SettleError("d1_settlement_entry_request_invalid") from error
    request_side = request.buy_side or request.side
    request_side = request_side.upper() if isinstance(request_side, str) else None
    _d1_require(request.market_id == entry["market_id"] and request.buy_token in (None, buy_token)
                and request.yes_token in (None, yes) and request.no_token in (None, no)
                and (request_side == side or request_side not in _D1_SIDE and request.buy_token == buy_token),
                "d1_settlement_entry_request_conflict")

    decisions = candidate["decisions"]
    market_refs = {**dict(MarketIdentity.from_row(entry).keys()), "token_id": buy_token}
    _d1_require(decisions and all(row["candidate_id"] == entry["candidate_id"]
                and row["side"] == side and row["token_ref"] == buy_token
                and market_refs.get(row["market_ref_kind"]) == row["market_ref"] for row in decisions),
                "d1_settlement_decision_binding_conflict")
    books = [_d1_buy_book(conn, candidate, row, buy_token, _d1_time(entry["entered_at"]))
             for row in decisions]
    return {"entry_id": entry["entry_id"], "candidate_id": entry["candidate_id"], "scan_id": entry["scan_id"],
            "side": side, "side_tokens": side_tokens, "buy_token_id": buy_token,
            "question": request.question, "decisions": decisions, "books": books}


def _d1_gamma_result(snapshot: dict, entry: dict, selected: dict, buy: dict) -> tuple[str, dict]:
    from polymarket_alpha_lab.papersim.adapters import polymarket
    from polymarket_alpha_lab.papersim.d1compare import acquire, journal

    slug, condition = entry["market_slug"], entry["condition_id"]
    by_slug = f"{polymarket.GAMMA_MARKET_PREFIX}{slug}" if slug else None
    by_condition = f"{polymarket.GAMMA_MARKETS_BY_CONDITION_URL}?{urlencode({'condition_id': condition})}"
    _d1_require(selected["source_kind"] == "gamma_close" and _d1_cites(selected, snapshot)
                and snapshot["kind"] in (polymarket.SOURCE_KIND_GAMMA, acquire._BODY_KIND)
                and snapshot["source"] in (by_slug, by_condition), "d1_settlement_outcome_source_unproven")
    try:
        body = journal.decode_public_json(snapshot["content"])
        if snapshot["source"] == by_condition:
            (body,) = body if type(body) is list else ()
        yes, no, _, _, yes_index, no_index = polymarket._token_pair(body)
        prices = journal._gamma_array(body.get("outcomePrices"))
        _d1_require(prices is not None and len(prices) == 2, "d1_settlement_gamma_evidence_invalid")
        terminal = tuple(Decimal(str(prices[index])) for index in (yes_index, no_index))
        _d1_require(all(price.is_finite() for price in terminal), "d1_settlement_gamma_evidence_invalid")
        ends = _d1_time(body.get("endDate"))
        closed = _d1_time(body.get("closedTime")) if body.get("closedTime") is not None else None
    except (journal.JournalError, polymarket._MarketRejected, ValueError,
            AttributeError, InvalidOperation) as error:
        if isinstance(error, SettleError):
            raise
        raise SettleError("d1_settlement_gamma_evidence_invalid") from error
    _d1_require(body.get("conditionId") == condition and body.get("id") == entry["market_id"]
                and (slug is None or body.get("slug") == slug) and body.get("question") == buy["question"]
                and (yes, no) == (buy["side_tokens"]["YES"], buy["side_tokens"]["NO"]),
                "d1_settlement_outcome_market_conflict")
    fetched, observed = _d1_time(snapshot["fetched_at"]), _d1_time(selected["observed_at"])
    _d1_require(body.get("closed") is True and body.get("umaResolutionStatus") in (None, "resolved")
                and ends is not None and observed <= fetched
                and ((body.get("closedTime") is None and ends <= observed == fetched)
                     or (closed is not None and closed <= observed and closed <= fetched)),
                "d1_settlement_outcome_time_conflict")
    winner = {(Decimal(1), Decimal(0)): "YES", (Decimal(0), Decimal(1)): "NO"}.get(terminal)
    _d1_require(winner is not None and winner == selected["outcome"], "d1_settlement_outcome_result_conflict")
    return buy["side_tokens"][winner], {"type": "registered_gamma", "snapshot": _d1_citation(snapshot),
                                       "closed_at": closed, "market_end_at": ends}


def _d1_basketball_result(conn, candidate: dict, selected: dict, snapshot: dict,
                          buy: dict, as_of: datetime.datetime) -> tuple[str | None, dict]:
    from polymarket_alpha_lab.papersim import report, store_psycopg

    _d1_require(_d1_cites(selected, snapshot) and selected["source_kind"] in ("gamma_close", "data_api_resolution"),
                "d1_settlement_outcome_source_unproven")
    digest = snapshot["content_sha256"]
    originals = {digest: snapshot}
    for ref in candidate["snapshot_refs"]:
        if ref not in originals:
            row = store_psycopg.d1_read_settlement_snapshot(conn, ref)
            if row is not None and row["kind"] in report.BASKETBALL_SNAPSHOT_KINDS:
                originals[ref] = row
    verified = report.verify_basketball_finals(candidates=[candidate], quotes=buy["decisions"],
                                                outcomes=[selected], snapshots=originals)
    _d1_require(len(verified.finals) == 1, REASON_SCORES_INCOMPLETE)
    final = verified.finals[0]
    final_sources = (final.contract.source.digest, final.score.source.digest)
    _d1_require(digest in final_sources and final.evidence_at <= as_of,
                "d1_settlement_outcome_source_unproven")
    booked = final.position.settlement()
    outcome = selected["outcome"]
    winner = None if outcome == "VOIDED" else buy["side_tokens"][_D1_SIDE[outcome]]
    expected = None if winner is None else ("win" if winner == buy["buy_token_id"] else "loss")
    _d1_require(booked == expected, "d1_settlement_outcome_result_conflict")
    return winner, final.display()


def _d1_outcome_binding(conn, entry: dict, candidate: dict, selected: dict, buy: dict,
                        as_of: datetime.datetime) -> dict:
    from polymarket_alpha_lab.papersim import report

    source_ref = selected["source_ref"]
    if isinstance(source_ref, str) and source_ref.startswith(_D1_RESOLUTION_SOURCE):
        raise SettleError("d1_settlement_first_write_unproven")
    market = MarketIdentity.from_row(entry)
    _d1_require(market.match(MarketIdentity.from_row(selected)) is not None
                and all(selected[field] in (None, value) for field, value in market.keys())
                and (selected["yes_token_id"], selected["no_token_id"])
                == (buy["side_tokens"]["YES"], buy["side_tokens"]["NO"]),
                "d1_settlement_outcome_market_conflict")
    observation = store.MarketOutcomeObservation(**{key: selected[key] for key in (
        *MARKET_IDENTITY_COLUMNS, "yes_token_id", "no_token_id", "outcome", "outcome_detail",
        "observed_at", "confirmed_at", "source_kind", "source_ref", "source_snapshot_sha256")})
    _d1_require(observation.evidence_key() == selected["evidence_key"], "d1_settlement_outcome_key_conflict")
    snapshot = _d1_snapshot(conn, selected["source_snapshot_sha256"])
    observed, confirmed = observation.observed_at, observation.confirmed_at
    fetched = _d1_time(snapshot["fetched_at"])
    _d1_require(fetched is not None and entry["entered_at"] <= observed <= (confirmed or observed) <= as_of
                and entry["entered_at"] <= fetched <= as_of,
                "d1_settlement_outcome_time_conflict")
    if report.is_basketball_candidate(candidate):
        winner, proof = _d1_basketball_result(conn, candidate, selected, snapshot, buy, as_of)
    else:
        winner, proof = _d1_gamma_result(snapshot, entry, selected, buy)
    return {"buy": buy, "selected_outcome": selected, "winning_token_id": winner,
            "outcome_proof": proof, "outcome_first_write": None}


def _d1_entry(facts: dict, captured: CapturedOutcome | None) -> OpenSettleEntry:
    raw = facts["entry"]
    cash = facts["opening_cash"]
    return OpenSettleEntry(**{key: value for key, value in raw.items() if key != "scan_id"}, outcome=captured,
                           opening_cash=cash if isinstance(cash, OpeningCash) else None,
                           cash_discrepancy=cash if isinstance(cash, EntryCashDiscrepancy) else None)


def _d1_check_contemporaneous_outcomes(conn, entry, candidate, selected, buy, outcomes, as_of) -> None:
    for row in outcomes:
        if (row["outcome"] in ("UNKNOWN", selected["outcome"])
                or row["observed_at"] != selected["observed_at"]):
            continue
        try:
            _d1_outcome_binding(conn, entry, candidate, row, buy, as_of)
        except SettleError:
            continue
        raise SettleError("d1_settlement_outcome_result_conflict")


def _d1_plan_entry(conn, facts: dict, *, operator: str, as_of: datetime.datetime) -> dict:
    entry, candidate, outcomes = (facts[key] for key in ("entry", "candidate", "outcomes"))
    cash = facts["opening_cash"]
    target = {"identity": {"entry_id": facts["entry_id"]}, "state": facts["state"], "entry": entry,
              "candidate": candidate, "outcomes": outcomes, "cash_rows": facts["cash_rows"],
              "opening_cash": asdict(cash) if cash is not None else None,
              "status": STATUS_PENDING, "reason": None, "source_outcome_id": None,
              "record": None, "automatic": None, "binding": None, "cash_discrepancy": None}
    if facts["state"] != "OPEN":
        target["reason"] = ("d1_settlement_entry_already_settled" if facts["state"] == "ALREADY_SETTLED"
                            else "d1_settlement_entry_missing")
        return target

    captured = select_entry_outcome(entry["entry_id"], entry["entered_at"], MarketIdentity.from_row(entry),
                                    (CapturedOutcome.from_row(row) for row in outcomes))
    decision = plan_settlement(_d1_entry(facts, captured), operator=operator)
    target["reason"] = decision.reason
    if decision.cash_discrepancy is not None:
        target["cash_discrepancy"] = decision.cash_discrepancy.as_dict()
    if captured is not None:
        target["source_outcome_id"] = captured.outcome_id
    try:
        buy = _d1_buy_binding(conn, entry, candidate)
        target["binding"] = {"buy": buy}
        if captured is not None and captured.is_effective:
            selected = next(row for row in outcomes if row["outcome_id"] == captured.outcome_id)
            binding = _d1_outcome_binding(conn, entry, candidate, selected, buy, as_of)
            target["binding"] = binding
            _d1_check_contemporaneous_outcomes(conn, entry, candidate, selected, buy, outcomes, as_of)
            if decision.status == STATUS_SETTLED:
                winner = binding["winning_token_id"]
                expected = None if winner is None else ("win" if winner == buy["buy_token_id"] else "loss")
                _d1_require(decision.record.win_loss == expected, "d1_settlement_outcome_result_conflict")
                target.update(status="READY", record=asdict(decision.record),
                              automatic=asdict(replace(decision.automatic, d1_binding=binding)))
    except SettleError as error:
        target["binding"] = {**(target["binding"] or {}), "reason": error.code}
        if target["reason"] is None:
            target["reason"] = error.code
    return target


def _d1_frozen_settlement(target: dict, *, operator: str) -> SettleDecision:
    from polymarket_alpha_lab.papersim.d1compare import operations

    raw = target["entry"]
    entry = {**raw, "entered_at": _d1_time(raw["entered_at"]),
             **{key: Decimal(raw[key]) for key in ("stake", "price", "entry_fee")}}
    row = target["binding"]["selected_outcome"]
    outcome = CapturedOutcome.from_row({**row, "observed_at": _d1_time(row["observed_at"]),
                                        "confirmed_at": _d1_time(row["confirmed_at"])})
    opening = target["opening_cash"]
    cash = OpeningCash(opening["event_id"], Decimal(opening["amount"]), Decimal(opening["paid_fee"]))
    decision = plan_settlement(_d1_entry({"entry": entry, "opening_cash": cash}, outcome), operator=operator)
    _d1_require(decision.status == STATUS_SETTLED, "d1_settlement_prepared_record_conflict")
    guard = replace(decision.automatic, d1_binding=target["binding"])
    _d1_require(operations.canonical_document(asdict(decision.record)) == target["record"]
                and operations.canonical_document(asdict(guard)) == target["automatic"],
                "d1_settlement_prepared_record_conflict")
    return replace(decision, automatic=guard)


def _d1_recheck_entry(conn, target: dict, latest: dict, *, as_of: datetime.datetime) -> dict:
    from polymarket_alpha_lab.papersim.d1compare import operations

    entry, candidate, outcomes = (latest[key] for key in ("entry", "candidate", "outcomes"))
    binding = {"entry": entry, "candidate": candidate, "outcomes": outcomes}
    reason = None
    try:
        _d1_require(entry is not None and operations.canonical_document(entry) == target["entry"],
                    "d1_settlement_entry_changed")
        _d1_require(len(outcomes) == 1 and outcomes[0]["outcome_id"] == target["source_outcome_id"],
                    "d1_settlement_selected_outcome_missing")
        buy = _d1_buy_binding(conn, entry, candidate)
        binding["verified"] = _d1_outcome_binding(conn, entry, candidate, outcomes[0], buy, as_of)
        _d1_require(operations.canonical_document(binding["verified"]) == target["binding"]
                    and operations.canonical_document(candidate) == target["candidate"],
                    "d1_settlement_locked_binding_changed")
    except SettleError as error:
        reason = error.code
    return {"reason": reason, "binding": binding}


def _run_d1_settle(team: str, *, conn, repo_root, operator: str, operation_key: str,
                   attempt: int, code_commit: str) -> dict:
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import operations

    config_digest = snapshots.snapshot_content_sha256(operations.document_bytes({"team": team, "operator": operator}))
    with operations.open_operation(conn, repo_root=repo_root, operation_key=operation_key, attempt=attempt,
                                   command=SETTLE_COMMAND, team=team, code_commit=code_commit,
                                   config_digest=config_digest, input_evidence=[]) as operation:
        if operation.completed_summary is not None:
            return operation.completed_summary

        def build():
            as_of = store_psycopg.d1_database_time(conn)
            targets = store_psycopg.d1_settlement_targets(conn, team=team, entry_ids=operation.slot.scope["entry_ids"])
            return {"evidence_as_of": as_of, "targets": [_d1_plan_entry(conn, facts, operator=operator, as_of=as_of)
                                                        for facts in targets]}

        plan = operation.prepare(build)
        as_of = _d1_time(plan["evidence_as_of"])
        _d1_require(as_of is not None, "d1_settlement_prepared_clock_invalid")
        settled, pending, items = [], [], []
        for target in plan["targets"]:
            identity = target["identity"]
            entry_id = identity["entry_id"]
            decision = _d1_frozen_settlement(target, operator=operator) if target["status"] == "READY" else None
            result = operation.settlement_item(
                target, record=decision.record if decision is not None else None,
                automatic=decision.automatic if decision is not None else None,
                revalidate=lambda latest: _d1_recheck_entry(conn, target, latest, as_of=as_of))
            items.append({"identity": identity, **result})
            if result["status"] == STATUS_PENDING:
                row = {"entry_id": entry_id, "reason": result["reason"]}
                discrepancy = result.get("cash_discrepancy") or target["cash_discrepancy"]
                if discrepancy is not None:
                    row["cash_discrepancy"] = discrepancy
                pending.append(row)
            elif result["status"] in (STATUS_SETTLED, "REUSED"):
                settled.append({"entry_id": entry_id, "classification": decision.classification,
                                "settlement_id": result["settlement_id"], "bankroll_event_id": result["cash_event_id"],
                                "inserted": result["status"] == STATUS_SETTLED})
            else:
                raise SettleError(result["reason"] or "d1_settlement_target_failed")
        return operation.finish({"operation_key": operation_key, "command": SETTLE_COMMAND, "team": team,
                                 "code_commit": code_commit, "settled": settled, "pending": pending,
                                 "settled_count": len(settled), "pending_count": len(pending),
                                 "snapshot_entries": len(plan["targets"]), "items": items})


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
               receipt_writer: Callable | None = None,
               managed: bool = False) -> dict:
    """Run one settle pass against the store (receipts + per-entry writes).

    Every external dependency is an explicit argument so the offline
    suite can drive the whole orchestration through injected fakes and
    the CLI uses the guarded store interfaces:
    ``conn`` is the managed/explicit application connection (as the CLI
    opens it), ``open_entries`` is the FX-29 repeatable-read snapshot
    loader (``store_psycopg.open_entries_for_settlement``),
    ``commit_check`` defaults to :func:`settle_commit_check`,
    ``settle_writer`` defaults to the guarded automatic store writer
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
    from polymarket_alpha_lab.papersim.d1compare import operations

    if type(team) is not str or not team.strip():
        raise SettleError("papersim_settle_team_invalid")
    if type(operator) is not str or not operator.strip():
        raise SettleError("papersim_settle_operator_invalid")
    if type(attempt) is not int or attempt < 1:
        raise SettleError("papersim_settle_attempt_invalid")
    if not callable(open_entries):
        raise SettleError("papersim_settle_loader_invalid")
    if operations.is_d1_operation_key(operation_key):
        if managed is not True:
            raise SettleError("d1_operation_managed_required")
        return _run_d1_settle(team, conn=conn, repo_root=repo_root, operator=operator,
                              operation_key=operation_key, attempt=attempt,
                              code_commit=settle_commit_check(repo_root, git_bin=git_bin))
    check = commit_check if commit_check is not None \
        else (lambda: settle_commit_check(repo_root, git_bin=git_bin))
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

        def _pending(entry_id, reason, discrepancy=None) -> None:
            row = {"entry_id": entry_id, "reason": reason}
            if discrepancy is not None:
                row["cash_discrepancy"] = discrepancy.as_dict()
            pending.append(row)
            evidence.append(f"pending:{entry_id}:{reason}")

        for decision in decisions:
            if decision.status == STATUS_PENDING:
                _pending(decision.entry_id, decision.reason, decision.cash_discrepancy)
                continue
            try:
                if settle_writer is None:
                    written = store_psycopg.record_automatic_settlement(
                        conn, decision.record, decision.automatic)
                else:
                    written = settle_writer(conn, decision.record)
            except EntryCashMismatch as error:
                _pending(decision.entry_id, error.code, error.discrepancy)
                continue
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
