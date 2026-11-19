"""FX-09L legacy history importer for the papersim v2 managed face.

Two-stage import (design 2026-11-15 v2, section 7): a pure DRY RUN is the
DEFAULT and is always computable offline from the archived repo snapshot;
production insertion is a separate authorization gate — the explicit
``--execute-production`` flag — and is refused without it. The legacy three
tables and two views are READ-ONLY sources (the archived CSV snapshot or a
read-only session against the frozen loopback cluster through the audited
papersim DSN face); this tool never writes the legacy face, never invents
unknown provenance (NULL stays NULL) and never converts a position label
into a natural market outcome.

Semantics pinned by the reviewed design:

- Natural outcomes (YES/NO/YES-basket) are market assertions; ``win`` and
  ``loss`` are POSITION labels imported as ``UNKNOWN`` market outcomes with
  the recorded label preserved verbatim (``outcome_detail``); ``EXITED`` is
  a discipline exit with NO market_outcomes row (the settlement carries
  classification ``EXIT``); ``VOIDED`` refunds the stake (proceeds equal to
  stake, pnl 0, no fees).
- A market-keyed resolution must follow every decision it covers. Shared
  resolutions also require compatible directions, settlement evidence, and
  aggregate costs and shares. Allocate verified pooled proceeds by legacy
  recorded shares in ascending decision id, round each result to cents, and
  give the last entry the remainder. Subtract each decision's own stake
  from its proceeds to derive P&L. Verified full losses use each decision's
  stake and verified voids refund each stake. Single-entry markets missing
  positive recorded shares remain INSUFFICIENT.
- Fees are back-computed ESTIMATES (``fee_source='estimated'``): entry leg
  ``stake * rate * (1 - price)``; ``EXITED`` rows add the sell leg
  ``shares * rate * exit_price * (1 - exit_price)`` at the exit price
  reconstructed from the allocated PnL (``(pnl + stake) / shares``, the
  queue_performance V-6 semantics); the rate comes from
  :func:`polymarket_alpha_lab.papersim.fees.taker_fee_rate` through the
  scan-lineage team classification — no rate number is hardcoded here and
  no net figure may be presented as a charged amount.
- ``entries.shares`` is reconstructed as the exact quotient ``stake / price``:
  ten legacy rows violate the managed schema's
  ``abs(shares * price - stake) < 0.01`` CHECK with their recorded integer
  shares (the 36-leg basket row is off by 28.53), so the recorded shares
  travel verbatim inside the candidate's ``input_payload`` and remain the
  allocation basis described above.
- Bankroll snapshots are reconciliation checkpoints, NOT events: exactly
  one ``INITIAL`` cash event is written from the first snapshot row's
  balance (``cash + deployed``, the ``ledger.bankroll_identity`` anchor, so
  the event ledger reconstructs the book from its origin); at every
  snapshot ts the event-recomputed bankroll identity is compared with the
  recorded one at half-cent tolerance (``bankroll_identity`` 口径) and
  every difference — including the cash-only deltas caused by timestamps
  landing between snapshots and by the estimated fee layer — is listed
  explicitly, never silently absorbed.
- All writes go through the single allowlisted adapter
  (``polymarket_alpha_lab.papersim.store_psycopg``) with the audited local
  DSN validator in front of every connection; the whole import is ONE
  transaction and every row carries an idempotency key, so a re-run
  inserts nothing new and returns the first mapping.

Paper evidence storage only: this tool grants no order, account or
execution authorization. FX-09L does NOT execute the production import
(separate operator authorization); it proves the machinery on disposable
clusters and in dry runs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Callable

# Repo/package bootstrap: prefer the installed package; fall back to the
# worktree's src/ so the script also runs from a plain checkout.
try:
    from polymarket_alpha_lab.papersim import fees, ledger, legacy_settlement, store
except ImportError:  # pragma: no cover - exercised only outside the venv
    _SRC = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(_SRC))
    from polymarket_alpha_lab.papersim import fees, ledger, legacy_settlement, store

SNAPSHOT_DIRECTORY = Path("docs/paper-sim/ledger-snapshots/2026-11-12")
DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / SNAPSHOT_DIRECTORY
DECISIONS_CSV = "decisions.csv"
RESOLUTIONS_CSV = "resolutions.csv"
BANKROLL_CSV = "bankroll.csv"

LEGACY_IMPORT_OPERATOR = "legacy_import"
LEGACY_IMPORT_TEAM = "legacy_mixed"
LEGACY_GATE_VERSION = "legacy-import-v1"
LEGACY_SCHEMA_VERSION = 1
LEGACY_MODEL_NAME = "legacy_book"
LEGACY_MODEL_VERSION = "book-of-record"
LEGACY_TIER = "legacy"
LEGACY_SOURCE_CLUSTER = "legacy_paper_sim_loopback"
INITIAL_EVENT_KEY = "legacy:bankroll:initial"
CENT = Decimal("0.01")
HALF_CENT = Decimal("0.005")

# D-5 (design 9.1): the legacy side vocabulary observed in the archived
# snapshot maps one-to-one onto the four-token candidate vocabulary. The
# table is explicit and test-pinned; any token outside it fails LOUDLY
# instead of being silently rewritten to a "nearest" canonical value —
# rewriting an unmapped side would change semantics (the raw text always
# survives verbatim in input_payload).
SIDE_MAP = {
    "YES": "YES",
    "NO": "NO",
    "YES-basket": "YES-basket",
    "NO-basket": "NO-basket",
}

NATURAL_LABELS = ("YES", "NO", "YES-basket")
SETTLED_LABELS = ("win", "loss")
EXITED_LABEL = "EXITED"
VOIDED_LABEL = "VOIDED"


class PapersimImportError(ValueError):
    """Fixed public error codes with optional legacy record identifiers."""


def _fail(code: str):
    raise PapersimImportError(code) from None


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _parse_ts(value: Any) -> datetime:
    """Parse a legacy timestamp; every legacy row is tz-anchored (+00)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            _fail("papersim_import_timestamp_invalid")
        return value.astimezone(UTC)
    if type(value) is not str or not value.strip():
        _fail("papersim_import_timestamp_invalid")
    text = value.strip()
    # '+00' style offsets (no minutes) are valid in Postgres output but not
    # for datetime.fromisoformat before the colon is supplied.
    if len(text) >= 3 and text[-3] in "+-" and text[-2:].isdigit():
        text += ":00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _fail("papersim_import_timestamp_invalid")
    if parsed.tzinfo is None:
        _fail("papersim_import_timestamp_invalid")
    return parsed.astimezone(UTC)


def _opt_decimal(value: Any) -> Decimal | None:
    """None stays None (unknown); everything else must be a finite Decimal."""
    if value is None or (type(value) is str and not value.strip()):
        return None
    try:
        number = Decimal(str(value).strip())
    except Exception:
        _fail("papersim_import_number_invalid")
    if not number.is_finite():
        _fail("papersim_import_number_invalid")
    return number


def _opt_int(value: Any) -> int | None:
    if value is None or (type(value) is str and not value.strip()):
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        _fail("papersim_import_number_invalid")


def _require_decimal(value: Any, name: str) -> Decimal:
    number = _opt_decimal(value)
    if number is None:
        _fail("papersim_import_number_invalid")
    return number


def _opt_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_rows_digest(decisions, resolutions, bankroll) -> str:
    """Content digest over the TYPED rows (source-independent scan identity)."""
    document = {
        "decisions": [{key: _jsonable(row.get(key)) for key in _DECISION_KEYS}
                      for row in decisions],
        "resolutions": [{key: _jsonable(row.get(key)) for key in _RESOLUTION_KEYS}
                        for row in resolutions],
        "bankroll": [{key: _jsonable(row.get(key)) for key in _BANKROLL_KEYS}
                     for row in bankroll],
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


_DECISION_KEYS = ("id", "ts", "market_id", "question", "side", "price", "shares",
                  "stake", "fair_low", "fair_high", "spot", "vol", "horizon_days",
                  "sizing", "reason", "team", "model_prediction", "market_type")
_RESOLUTION_KEYS = ("market_id", "ts", "outcome", "payoff", "pnl", "evidence")
_BANKROLL_KEYS = ("ts", "cash", "deployed", "note")


@dataclass(frozen=True)
class LegacySnapshot:
    """Typed, read-only view of the legacy book of record."""

    decisions: tuple[dict, ...]
    resolutions: tuple[dict, ...]
    bankroll: tuple[dict, ...]
    source_label: str
    source_files_sha256: dict[str, str] | None


def _typed_decision(row: dict) -> dict:
    return {
        "id": _require_int(row.get("id")),
        "ts": _parse_ts(row.get("ts")),
        "market_id": _require_text(row.get("market_id"), "market_id"),
        "question": _opt_text(row.get("question")),
        "side": _require_text(row.get("side"), "side"),
        "price": _require_decimal(row.get("price"), "price"),
        "shares": _opt_decimal(row.get("shares")),
        "stake": _require_decimal(row.get("stake"), "stake"),
        "fair_low": _opt_decimal(row.get("fair_low")),
        "fair_high": _opt_decimal(row.get("fair_high")),
        "spot": _opt_decimal(row.get("spot")),
        "vol": _opt_decimal(row.get("vol")),
        "horizon_days": _opt_decimal(row.get("horizon_days")),
        "sizing": _opt_text(row.get("sizing")),
        "reason": _opt_text(row.get("reason")),
        "team": _require_text(row.get("team"), "team"),
        "model_prediction": _opt_decimal(row.get("model_prediction")),
        "market_type": _opt_text(row.get("market_type")),
    }


def _typed_resolution(row: dict) -> dict:
    return {
        "market_id": _require_text(row.get("market_id"), "market_id"),
        "ts": _parse_ts(row.get("ts")),
        "outcome": _require_text(row.get("outcome"), "outcome"),
        "payoff": _opt_decimal(row.get("payoff")),
        "pnl": _require_decimal(row.get("pnl"), "pnl"),
        "evidence": _opt_text(row.get("evidence")),
    }


def _typed_bankroll(row: dict) -> dict:
    return {
        "ts": _parse_ts(row.get("ts")),
        "cash": _opt_decimal(row.get("cash")),
        "deployed": _opt_decimal(row.get("deployed")),
        "note": _opt_text(row.get("note")),
    }


def _require_int(value: Any) -> int:
    number = _opt_int(value)
    if number is None:
        _fail("papersim_import_number_invalid")
    return number


def _require_text(value: Any, name: str) -> str:
    text = _opt_text(value)
    if text is None:
        _fail(f"papersim_import_{name}_missing")
    return text


def load_snapshot(snapshot_dir: Path) -> LegacySnapshot:
    """Load and type the archived repo snapshot (offline, read-only)."""
    directory = Path(snapshot_dir)
    paths = {name: directory / name for name in
             (DECISIONS_CSV, RESOLUTIONS_CSV, BANKROLL_CSV)}
    for name, path in paths.items():
        if not path.is_file():
            _fail("papersim_import_snapshot_missing")
    decisions = tuple(sorted(
        (_typed_decision(row) for row in
         csv.DictReader(paths[DECISIONS_CSV].open(newline="", encoding="utf-8"))),
        key=lambda row: row["id"]))
    resolutions = tuple(sorted(
        (_typed_resolution(row) for row in
         csv.DictReader(paths[RESOLUTIONS_CSV].open(newline="", encoding="utf-8"))),
        key=lambda row: row["market_id"]))
    bankroll = tuple(
        _typed_bankroll(row) for row in
        csv.DictReader(paths[BANKROLL_CSV].open(newline="", encoding="utf-8")))
    if not decisions or not bankroll:
        _fail("papersim_import_snapshot_empty")
    return LegacySnapshot(
        decisions=decisions, resolutions=resolutions, bankroll=bankroll,
        source_label=f"repo-snapshot:{Path(snapshot_dir).as_posix()}",
        source_files_sha256={name: _file_sha256(path)
                             for name, path in paths.items()})


_LEGACY_QUERIES = {
    "decisions": "select id, ts, market_id, question, side, price, shares, stake, "
                 "fair_low, fair_high, spot, vol, horizon_days, sizing, reason, team, "
                 "model_prediction, market_type from public.decisions order by id",
    "resolutions": "select market_id, ts, outcome, payoff, pnl, evidence "
                   "from public.resolutions order by market_id",
    "bankroll": "select ts, cash, deployed, note from public.bankroll order by ts",
}


def load_snapshot_from_legacy_face(connect: Callable[[], Any]) -> LegacySnapshot:
    """Re-observe the legacy face read-only (operational source, alpha path).

    ``connect`` is a factory returning an ALREADY read-only legacy
    connection (the adapter's ``connect_legacy_readonly``); only SELECTs
    run here.
    """
    connection = connect()
    try:
        with connection:
            cursor = connection.execute(_LEGACY_QUERIES["decisions"])
            columns = [description.name for description in cursor.description]
            decisions = tuple(_typed_decision(dict(zip(columns, row, strict=True)))
                              for row in cursor.fetchall())
            cursor = connection.execute(_LEGACY_QUERIES["resolutions"])
            columns = [description.name for description in cursor.description]
            resolutions = tuple(_typed_resolution(dict(zip(columns, row, strict=True)))
                                for row in cursor.fetchall())
            cursor = connection.execute(_LEGACY_QUERIES["bankroll"])
            columns = [description.name for description in cursor.description]
            bankroll = tuple(_typed_bankroll(dict(zip(columns, row, strict=True)))
                             for row in cursor.fetchall())
    finally:
        connection.close()
    if not decisions or not bankroll:
        _fail("papersim_import_snapshot_empty")
    return LegacySnapshot(decisions=decisions, resolutions=resolutions,
                          bankroll=bankroll,
                          source_label="legacy-face:read-only-reobservation",
                          source_files_sha256=None)


def map_side(raw_side: str) -> str:
    """Explicit legacy->v2 side mapping (D-5); unknown tokens fail loudly."""
    try:
        return SIDE_MAP[raw_side]
    except KeyError:
        _fail("papersim_import_side_unmapped")


def map_market_kind(market_type: str | None) -> str:
    """Legacy market_type -> market_kind; touch markets keep their kind."""
    if market_type and market_type.endswith("_touch"):
        return "touch"
    return "terminal"


@dataclass(frozen=True)
class PlannedSettlement:
    """One settlement row to import (all money at cent precision)."""

    legacy_decision_id: int
    market_id: str
    legacy_outcome: str
    classification: str  # NATURAL | EXIT | VOIDED_REFUND
    outcome_label: str
    win_loss: str | None
    exit_price: Decimal | None
    proceeds: Decimal
    pnl_gross: Decimal
    fee_entry: Decimal
    fee_exit: Decimal
    fee_total: Decimal
    pnl_net: Decimal
    confirmed_at: datetime
    evidence: str | None

    def comparison_tuple(self) -> tuple:
        return (self.classification, self.outcome_label, self.win_loss,
                self.exit_price, self.proceeds, self.pnl_gross, self.fee_total,
                "estimated", self.pnl_net, LEGACY_IMPORT_OPERATOR,
                self.confirmed_at)


@dataclass(frozen=True)
class ImportPlan:
    """The complete, deterministic conversion of one legacy snapshot."""

    scan: store.ScanRunRecord
    submission: store.ScanSubmission
    outcomes: tuple[store.MarketOutcomeObservation, ...]
    settlements: tuple[PlannedSettlement, ...]
    insufficient_markets: tuple[str, ...]
    initial_event: tuple[str, datetime, Decimal, str]
    reconciliation: tuple[dict, ...]
    source_label: str
    rows: dict[str, int]
    content_sha256: str
    fee_schedule_as_of: str

    @property
    def scan_key(self) -> str:
        return self.scan.key()

    @property
    def payload_sha256(self) -> str:
        return self.submission.payload_sha256()

    def table_digests(self) -> dict[str, str]:
        """Per-target row count + key-column digest (double-run proof)."""
        candidate_rows = sorted(
            (candidate.legacy_source_id, candidate.side, candidate.conclusion,
             None if candidate.median_probability is None
             else str(candidate.median_probability))
            for candidate in self.submission.candidates)
        entry_rows = sorted(
            (candidate.entry.legacy_source_id, str(candidate.entry.stake),
             str(candidate.entry.price), str(candidate.entry.shares),
             str(candidate.entry.entry_fee))
            for candidate in self.submission.candidates
            if candidate.entry is not None)
        outcome_rows = sorted(
            (observation.market_id, observation.outcome,
             observation.observed_at.astimezone(UTC).isoformat())
            for observation in self.outcomes)
        settlement_rows = sorted(
            (str(row.legacy_decision_id), row.classification,
             str(row.pnl_gross), str(row.fee_total), str(row.pnl_net))
            for row in self.settlements)
        event_rows = sorted(
            [(INITIAL_EVENT_KEY, str(self.initial_event[2]))]
            + [(f"legacy:settle:{row.legacy_decision_id}", str(row.proceeds))
               for row in self.settlements]
            + [(f"entry-of:{candidate.legacy_source_id}",
                str(candidate.entry.stake + candidate.entry.entry_fee))
               for candidate in self.submission.candidates
               if candidate.entry is not None])
        digests = {}
        for name, rows in (("scan_runs", [(self.scan_key, self.payload_sha256)]),
                           ("candidates", candidate_rows), ("entries", entry_rows),
                           ("market_outcomes", outcome_rows),
                           ("settlements", settlement_rows),
                           ("bankroll_events", event_rows)):
            digests[name] = "{}:{}".format(
                len(rows),
                hashlib.sha256(json.dumps(rows, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")
                               ).hexdigest()[:16])
        return digests


def build_import_plan(snapshot: LegacySnapshot) -> ImportPlan:
    """Pure conversion: snapshot -> scan + candidates + entries + outcomes
    + settlements + the single INITIAL cash event + reconciliation.

    No I/O beyond the snapshot already in memory; identical snapshots
    produce byte-identical plans (the dry-run double-run proof).
    """
    decisions = snapshot.decisions
    ids = [row["id"] for row in decisions]
    if len(set(ids)) != len(ids):
        _fail("papersim_import_decision_duplicate")

    decisions_by_market: dict[str, list[dict]] = {}
    for row in decisions:
        decisions_by_market.setdefault(row["market_id"], []).append(row)
    resolutions: dict[str, dict] = {}
    for row in snapshot.resolutions:
        market_id = row["market_id"]
        if market_id in resolutions:
            _fail(str(legacy_settlement.LegacyResolutionError(
                "papersim_import_resolution_duplicate", market_id,
                decisions_by_market.get(market_id, []))))
        resolutions[market_id] = row

    insufficient: list[str] = []
    allocations: dict[int, Decimal] = {}
    for market_id in sorted(decisions_by_market.keys() & resolutions.keys()):
        market_rows = decisions_by_market[market_id]
        resolution = resolutions[market_id]
        try:
            pooled_pnl = legacy_settlement.attribute_resolution(
                market_id, market_rows, resolution)
        except legacy_settlement.LegacyResolutionError as error:
            _fail(str(error))
        if pooled_pnl is not None:
            allocations.update((int(key), pnl) for key, pnl in pooled_pnl.items())
            continue
        row, = market_rows
        if row["shares"] is None or row["shares"] <= 0:
            insufficient.append(market_id)
            continue
        allocations[row["id"]] = resolution["pnl"]

    # --- synthetic legacy scan identity (deterministic, idempotent) -------
    started_at = min(row["ts"] for row in decisions)
    finished_at = max(row["ts"] for row in decisions)
    content_sha256 = _canonical_rows_digest(snapshot.decisions,
                                            snapshot.resolutions,
                                            snapshot.bankroll)
    params = json.dumps({
        "marker": f"legacy:decisions:{max(ids)}",
        "content_sha256": content_sha256,
        "rows": {"decisions": len(decisions),
                 "resolutions": len(snapshot.resolutions),
                 "bankroll": len(snapshot.bankroll)},
        "format": "legacy-import-v1",
    }, sort_keys=True, separators=(",", ":"))
    scan = store.ScanRunRecord(
        team=LEGACY_IMPORT_TEAM, started_at=started_at, finished_at=finished_at,
        code_commit=None, schema_version=LEGACY_SCHEMA_VERSION,
        gate_version=LEGACY_GATE_VERSION, operator=LEGACY_IMPORT_OPERATOR,
        params=params, is_legacy=True)

    # --- candidates + entries (one per decision, all ACCEPTED) ------------
    candidates = []
    fee_rate_by_id: dict[int, Decimal] = {}
    for row in decisions:
        side = map_side(row["side"])  # loud fail on any unmapped token
        stake = row["stake"]
        price = row["price"]
        if stake <= 0:
            _fail("papersim_import_stake_invalid")
        if not Decimal(0) < price < Decimal(1):
            _fail("papersim_import_price_invalid")
        prediction = row["model_prediction"]
        if prediction is not None and not Decimal(0) <= prediction <= Decimal(1):
            _fail("papersim_import_probability_invalid")
        category = fees.legacy_scan_fee_category(row["team"], row["market_type"])
        rate = Decimal(str(fees.taker_fee_rate(category)))
        entry_fee = _cents(stake * rate * (Decimal(1) - price))
        shares = stake / price  # reconstructed exact quotient (module doc)
        legacy_id = str(row["id"])
        payload = {key: _jsonable(row[key]) for key in _DECISION_KEYS}
        entry = store.EntrySubmission(
            stake=stake, price=price, shares=shares, fee_category=category,
            fee_rate=rate, entry_fee=entry_fee, fee_source="estimated",
            tier=LEGACY_TIER, entered_at=row["ts"], legacy_source_id=legacy_id,
            is_legacy=True)
        candidates.append(store.CandidateSubmission(
            candidate_key=f"legacy-decision-{row['id']}", conclusion="ACCEPTED",
            side=side, market_kind=map_market_kind(row["market_type"]),
            team=row["team"], model_name=LEGACY_MODEL_NAME,
            model_version=LEGACY_MODEL_VERSION,
            input_payload=json.dumps(payload, sort_keys=True,
                                     separators=(",", ":")),
            gate_report='{"source":"legacy_import"}',
            evaluated_at=row["ts"], condition_id=None, market_id=row["market_id"],
            market_slug=None, yes_token_id=None, no_token_id=None,
            market_type=row["market_type"], binding_version=1,
            median_probability=prediction, variant_quotes="[]",
            snapshot_refs="[]", rejection_reasons="[]", order_book=None,
            book_fetched_at=None, legacy_source_id=legacy_id, is_legacy=True,
            price_evidence=(), entry=entry))
        fee_rate_by_id[row["id"]] = rate
    submission = store.ScanSubmission(scan=scan, candidates=tuple(candidates))

    # --- market_outcomes + settlements -------------------------------------
    outcomes = []
    outcome_ids_by_market: dict[str, dict] = {}
    for market_id in sorted(resolutions):
        resolution = resolutions[market_id]
        outcome_token = resolution["outcome"]
        observed_at = resolution["ts"]
        if outcome_token == EXITED_LABEL:
            continue  # position-level exit: no market assertion row
        if outcome_token in NATURAL_LABELS:
            mapped_outcome = outcome_token
            outcome_detail = None
            confirmed_at = observed_at
        elif outcome_token in SETTLED_LABELS:
            # Position label, NOT a natural outcome: never fabricated into
            # one; the recorded label survives verbatim (design 7.2/7.3).
            mapped_outcome = "UNKNOWN"
            outcome_detail = f"recorded_label={outcome_token}"
            confirmed_at = None
        elif outcome_token == VOIDED_LABEL:
            mapped_outcome = "VOIDED"
            outcome_detail = None
            confirmed_at = observed_at
        else:
            _fail("papersim_import_outcome_unmapped")
        outcomes.append(store.MarketOutcomeObservation(
            market_id=market_id, outcome=mapped_outcome,
            outcome_detail=outcome_detail, confirmed_at=confirmed_at,
            observed_at=observed_at, source_kind="legacy_import",
            source_ref=f"resolutions:{market_id}"))
        outcome_ids_by_market[market_id] = {
            "outcome": mapped_outcome, "label": outcome_token}

    settlements: list[PlannedSettlement] = []
    for market_id in sorted(resolutions):
        resolution = resolutions[market_id]
        outcome_token = resolution["outcome"]
        if market_id in insufficient:
            continue
        market_rows = sorted(decisions_by_market.get(market_id, []),
                             key=lambda row: row["id"])
        for row in market_rows:
            legacy_id = row["id"]
            pnl_gross = allocations[legacy_id]
            stake = row["stake"]
            price = row["price"]
            shares = stake / price
            rate = fee_rate_by_id[legacy_id]
            fee_entry = _cents(stake * rate * (Decimal(1) - price))
            exit_price = None
            fee_exit = Decimal("0.00")
            if outcome_token == EXITED_LABEL:
                classification = "EXIT"
                win_loss = None
                exit_price = (pnl_gross + stake) / shares
                if not Decimal(0) <= exit_price <= Decimal(1):
                    _fail("papersim_import_exit_price_invalid")
                fee_exit = _cents(shares * rate * exit_price
                                  * (Decimal(1) - exit_price))
            elif outcome_token == VOIDED_LABEL:
                classification = "VOIDED_REFUND"
                win_loss = None
                pnl_gross = Decimal("0.00")
                fee_entry = Decimal("0.00")
            elif outcome_token in NATURAL_LABELS:
                classification = "NATURAL"
                win_loss = "win" if store.natural_alignment(
                    map_side(row["side"]), outcome_token) else "loss"
            elif outcome_token in SETTLED_LABELS:
                classification = "NATURAL"
                win_loss = outcome_token
            else:  # pragma: no cover - rejected during outcome mapping
                _fail("papersim_import_outcome_unmapped")
            fee_total = fee_entry + fee_exit
            # Payout semantics: a natural loss pays 0. The legacy book's
            # cent drift (recorded market pnl versus per-entry stake on
            # integer-share rows) can push the naive difference a few
            # cents below zero; proceeds is the payout so it clamps at
            # zero while pnl_gross keeps the recorded allocation (no CHECK
            # ties proceeds to pnl_gross + stake).
            proceeds = max(pnl_gross + stake, Decimal("0.00"))
            settlements.append(PlannedSettlement(
                legacy_decision_id=legacy_id, market_id=market_id,
                legacy_outcome=outcome_token, classification=classification,
                outcome_label=outcome_token, win_loss=win_loss,
                exit_price=exit_price, proceeds=proceeds, pnl_gross=pnl_gross,
                fee_entry=fee_entry, fee_exit=fee_exit, fee_total=fee_total,
                pnl_net=pnl_gross - fee_total, confirmed_at=resolution["ts"],
                evidence=resolution["evidence"]))
    settlements.sort(key=lambda row: (row.market_id, row.legacy_decision_id))

    # --- the single INITIAL cash event + reconciliation checkpoints --------
    first = snapshot.bankroll[0]
    if first["cash"] is None or first["deployed"] is None:
        _fail("papersim_import_bankroll_invalid")
    opening_bankroll = _cents(first["cash"] + first["deployed"])
    initial_event = (INITIAL_EVENT_KEY, first["ts"], opening_bankroll,
                     "first legacy bankroll snapshot row balance "
                     "(cash + deployed, the ledger.bankroll_identity anchor)")
    timeline = [(first["ts"], initial_event[2])]
    by_legacy_id = {row["id"]: row for row in decisions}
    for candidate in candidates:
        entry = candidate.entry
        timeline.append((entry.entered_at,
                         -(entry.stake + entry.entry_fee)))
    for settlement in settlements:
        timeline.append((settlement.confirmed_at, settlement.proceeds))
    timeline.sort(key=lambda item: item[0])
    settled_by_id = {row.legacy_decision_id: row for row in settlements}
    reconciliation = []
    for row in snapshot.bankroll:
        moment = row["ts"]
        event_cash = sum((amount for at, amount in timeline if at <= moment),
                         Decimal("0.00"))
        event_deployed = sum(
            (candidate.entry.stake + candidate.entry.entry_fee
             for candidate in candidates
             if candidate.entry.entered_at <= moment
             and (int(candidate.entry.legacy_source_id) not in settled_by_id
                  or settled_by_id[int(candidate.entry.legacy_source_id)]
                  .confirmed_at > moment)),
            Decimal("0.00"))
        snapshot_cash = row["cash"]
        delta = None if snapshot_cash is None else event_cash - snapshot_cash
        snapshot_total = None if (row["cash"] is None or row["deployed"] is None) \
            else row["cash"] + row["deployed"]
        event_total = _cents(event_cash + event_deployed)
        delta_total = None if snapshot_total is None else event_total - snapshot_total
        # The match verdict uses the bankroll-identity lens (design 7.2:
        # "bankroll_identity 口径"): the event ledger must reproduce the
        # snapshot's cash + deployed total; the cash-only delta stays
        # listed because entry/settle timestamps land between snapshots.
        status = ("unknown" if snapshot_total is None
                  else "match" if abs(delta_total) <= HALF_CENT else "diff")
        reconciliation.append({
            "ts": moment, "snapshot_cash": snapshot_cash,
            "event_cash": _cents(event_cash), "delta": delta,
            "snapshot_total": snapshot_total, "event_total": event_total,
            "delta_total": delta_total, "status": status})

    return ImportPlan(
        scan=scan, submission=submission, outcomes=tuple(outcomes),
        settlements=tuple(settlements),
        insufficient_markets=tuple(sorted(insufficient)),
        initial_event=initial_event, reconciliation=tuple(reconciliation),
        source_label=snapshot.source_label,
        rows={"decisions": len(decisions),
              "resolutions": len(snapshot.resolutions),
              "bankroll": len(snapshot.bankroll)},
        content_sha256=content_sha256,
        fee_schedule_as_of=fees.FEE_SCHEDULE_AS_OF)


# ---------------------------------------------------------------------------
# Deterministic dry-run report
# ---------------------------------------------------------------------------


def render_report(plan: ImportPlan) -> str:
    """Deterministic multi-line dry-run report (double-run zero-diff)."""
    settlements = plan.settlements
    gross = sum((row.pnl_gross for row in settlements), Decimal("0.00"))
    fee_entry = sum((row.fee_entry for row in settlements), Decimal("0.00"))
    fee_exit = sum((row.fee_exit for row in settlements), Decimal("0.00"))
    fee_total = fee_entry + fee_exit
    net = sum((row.pnl_net for row in settlements), Decimal("0.00"))
    classes = {"NATURAL": 0, "EXIT": 0, "VOIDED_REFUND": 0}
    for row in settlements:
        classes[row.classification] += 1
    outcome_counts: dict[str, int] = {}
    for observation in plan.outcomes:
        outcome_counts[observation.outcome] = \
            outcome_counts.get(observation.outcome, 0) + 1
    exited_labels = len({row.market_id for row in settlements
                         if row.classification == "EXIT"})
    unknown_labels = outcome_counts.get("UNKNOWN", 0)
    side_counts: dict[str, int] = {}
    for candidate in plan.submission.candidates:
        side_counts[candidate.side] = side_counts.get(candidate.side, 0) + 1
    lines = [
        "papersim legacy import - DRY RUN (no database writes)",
        f"source: {plan.source_label}",
        "rows: decisions={decisions} resolutions={resolutions} "
        "bankroll={bankroll}".format(**plan.rows),
        f"content sha256: {plan.content_sha256}",
        "scan: key={key} payload={payload} team={team} operator={operator} "
        "code_commit=None started_at={started} finished_at={finished}".format(
            key=plan.scan_key, payload=plan.payload_sha256,
            team=plan.scan.team, operator=plan.scan.operator,
            started=plan.scan.started_at.isoformat(),
            finished=plan.scan.finished_at.isoformat()),
        "candidates: {count} accepted={accepted} sides({side_summary})".format(
            count=len(plan.submission.candidates),
            accepted=sum(1 for c in plan.submission.candidates
                         if c.conclusion == "ACCEPTED"),
            side_summary=", ".join(f"{side} x{side_counts[side]}"
                                   for side in sorted(side_counts))),
        "entries: {count} (shares reconstructed stake/price; recorded shares "
        "preserved in input_payload)".format(
            count=sum(1 for c in plan.submission.candidates if c.entry)),
        "market_outcomes: {count} (YES={yes} NO={no} YES-basket={yesb} "
        "VOIDED={voided} UNKNOWN(position labels)={unknown}); "
        "EXITED labels without a market row: {exited}".format(
            count=len(plan.outcomes), yes=outcome_counts.get("YES", 0),
            no=outcome_counts.get("NO", 0),
            yesb=outcome_counts.get("YES-basket", 0),
            voided=outcome_counts.get("VOIDED", 0), unknown=unknown_labels,
            exited=exited_labels),
        "settlements: {count} (NATURAL={nat} EXIT={exited} "
        "VOIDED_REFUND={voided}) open-entries (no resolution): {open_count}"
        .format(count=len(settlements), nat=classes["NATURAL"],
                exited=classes["EXIT"], voided=classes["VOIDED_REFUND"],
                open_count=sum(1 for c in plan.submission.candidates
                               if c.entry and int(c.entry.legacy_source_id)
                               not in {row.legacy_decision_id
                                       for row in settlements})),
        f"insufficient markets (no allocation, never zero-filled): "
        f"{len(plan.insufficient_markets)}"
        + (f" {','.join(plan.insufficient_markets)}"
           if plan.insufficient_markets else ""),
        f"fees: entry={fee_entry} exit={fee_exit} total={fee_total} "
        f"source=estimated schedule-as-of={plan.fee_schedule_as_of}",
        f"conservation: settlements pnl_gross sum {gross} "
        f"fee sum {fee_total} net sum {net}",
        f"net identity: pnl_gross - fee_total == pnl_net: "
        f"{'OK' if gross - fee_total == net else 'BROKEN'}",
    ]
    for row in settlements:
        if row.market_id == "3866000" or (
                len({r.legacy_decision_id for r in settlements
                     if r.market_id == row.market_id}) > 1):
            lines.append(
                f"allocation market {row.market_id}: legacy "
                f"{row.legacy_decision_id} -> {row.pnl_gross} "
                f"(fee {row.fee_total}, net {row.pnl_net})")
    market_sums: dict[str, Decimal] = {}
    for row in settlements:
        market_sums[row.market_id] = market_sums.get(
            row.market_id, Decimal("0.00")) + row.pnl_gross
    conservation_ok = all(
        sum((r.pnl_gross for r in settlements if r.market_id == market),
            Decimal("0.00")) == market_sums[market]
        for market in market_sums)
    lines.append(
        "per-market pnl conservation: "
        + ("OK" if conservation_ok else "BROKEN")
        + f" over {len(market_sums)} settled markets, total {gross}")
    digests = plan.table_digests()
    lines.append("plan digests (rows:sha16): " + " ".join(
        f"{name}={digest}" for name, digest in sorted(digests.items())))
    matches = sum(1 for row in plan.reconciliation if row["status"] == "match")
    diffs = sum(1 for row in plan.reconciliation if row["status"] == "diff")
    unknown = sum(1 for row in plan.reconciliation
                  if row["status"] == "unknown")
    lines.append(
        f"bankroll reconciliation: {len(plan.reconciliation)} snapshot rows "
        f"match={matches} diff={diffs} unknown={unknown} "
        "(half-cent tolerance; differences listed, never hidden)")
    for row in plan.reconciliation:
        lines.append(
            "  {ts} cash snap={cash} events={event} delta={delta} | total "
            "snap={st} events={et} delta={dt}: {status}".format(
                ts=row["ts"].isoformat(),
                cash=row["snapshot_cash"], event=row["event_cash"],
                delta=row["delta"], st=row["snapshot_total"],
                et=row["event_total"], dt=row["delta_total"],
                status=row["status"]))
    lines.append(
        "authorization: production insertion requires --execute-production "
        "(NOT executed by this dry run)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Execution (separate authorization gate; one transaction; idempotent)
# ---------------------------------------------------------------------------


def _adapter():
    """Lazily import the single allowlisted connector (offline-safe import)."""
    from polymarket_alpha_lab.papersim import store_psycopg
    return store_psycopg


def execute_import(conn, plan: ImportPlan) -> dict:
    """Write one import plan inside ONE transaction; idempotent per keys.

    A second run inserts nothing new and returns the first mapping
    (design 7.5): the scan key + payload guard the scan, evidence keys
    guard the outcomes, unique entry settlement rows are compared
    field-by-field and cash events are keyed.
    """
    store_psycopg = _adapter()
    inserted = {"scan_runs": 0, "candidates": 0, "entries": 0,
                "cash_events": 0, "market_outcomes": 0, "settlements": 0}
    with conn.transaction():
        scan_result = store_psycopg.record_scan(conn, plan.submission)
        scan_id = scan_result["scan_id"]
        if scan_result["inserted"]:
            inserted["scan_runs"] = 1
            inserted["candidates"] = len(scan_result["candidate_ids"])
            inserted["entries"] = len(scan_result["bankroll_event_keys"])
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT k.legacy_source_id, e.entry_id FROM papersim.entries e "
                "JOIN papersim.candidates k ON k.candidate_id = e.candidate_id "
                "WHERE k.scan_id = %s", (scan_id,))
            entry_ids = {int(legacy_id): int(entry_id)
                         for legacy_id, entry_id in cursor.fetchall()}
        outcome_ids: dict[str, int] = {}
        for observation in plan.outcomes:
            result = store_psycopg.record_market_outcome(conn, observation)
            outcome_ids[observation.market_id] = result["outcome_id"]
            if result["inserted"]:
                inserted["market_outcomes"] += 1
        with conn.cursor() as cursor:
            event_key, occurred_at, amount, note = plan.initial_event
            cursor.execute(
                "SELECT amount FROM papersim.bankroll_events "
                "WHERE event_key = %s", (event_key,))
            existing = cursor.fetchone()
            if existing is None:
                cursor.execute(
                    "INSERT INTO papersim.bankroll_events (event_key, "
                    "occurred_at, currency, amount, reason, entry_id, scan_id, "
                    "note, is_legacy) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (event_key, occurred_at, "USD", amount, "INITIAL",
                     None, scan_id, note, True))
                inserted["cash_events"] += 1
            elif existing[0] != amount:
                _fail("papersim_import_event_conflict")
            for settlement in plan.settlements:
                entry_id = entry_ids[settlement.legacy_decision_id]
                cursor.execute(
                    "SELECT classification, outcome_label, win_loss, exit_price, "
                    "proceeds, pnl_gross, fee_total, fee_source, pnl_net, "
                    "confirmed_by, confirmed_at FROM papersim.settlements "
                    "WHERE entry_id = %s", (entry_id,))
                existing = cursor.fetchone()
                if existing is not None:
                    if tuple(existing) != settlement.comparison_tuple():
                        _fail("papersim_import_settlement_conflict")
                    continue
                reason = {"NATURAL": "SETTLE_NATURAL", "EXIT": "SETTLE_EXIT",
                          "VOIDED_REFUND": "VOIDED_REFUND"}[
                    settlement.classification]
                source_outcome_id = outcome_ids.get(settlement.market_id) \
                    if settlement.classification != "EXIT" else None
                cursor.execute(
                    "INSERT INTO papersim.settlements (entry_id, classification, "
                    "outcome_label, win_loss, exit_price, proceeds, pnl_gross, "
                    "fee_total, fee_source, pnl_net, source_outcome_id, "
                    "exit_mark_id, evidence, confirmed_by, confirmed_at, is_legacy) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (entry_id, settlement.classification,
                     settlement.outcome_label, settlement.win_loss,
                     settlement.exit_price, settlement.proceeds,
                     settlement.pnl_gross, settlement.fee_total, "estimated",
                     settlement.pnl_net, source_outcome_id, None,
                     settlement.evidence, LEGACY_IMPORT_OPERATOR,
                     settlement.confirmed_at, True))
                cursor.execute(
                    "INSERT INTO papersim.bankroll_events (event_key, occurred_at, "
                    "currency, amount, reason, entry_id, scan_id, note, is_legacy) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (f"legacy:settle:{settlement.legacy_decision_id}",
                     settlement.confirmed_at, "USD", settlement.proceeds,
                     reason, entry_id, scan_id,
                     f"legacy resolutions:{settlement.market_id}", True))
                inserted["settlements"] += 1
                inserted["cash_events"] += 1
    return {"inserted": inserted, "scan_id": scan_id,
            "scan_key": plan.scan_key, "entry_ids": entry_ids,
            "outcome_ids": outcome_ids}


def register_baseline(owner_conn, legacy_connect_factory, *,
                      registered_by: str, registered_run: str) -> dict:
    """Append observed-baseline evidence rows from a read-only re-observation.

    Writes ONLY ``papersim_private.observed_baseline`` (owner face). The
    legacy face is read through the supplied read-only connection factory;
    unknown provenance stays NULL (legacy_created_at / legacy_operator).
    Each registration run appends its own observation rows — they are
    evidence, never applied-migration receipts.
    """
    observed_at = datetime.now(UTC)
    legacy = legacy_connect_factory()
    try:
        with legacy:
            system_identifier = str(legacy.execute(
                "SELECT system_identifier FROM pg_control_system()"
            ).fetchone()[0])
            relations = legacy.execute(
                "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                "AND c.relkind IN ('r','v') AND c.relname NOT LIKE 'pg_%' "
                "ORDER BY c.relname").fetchall()
            rows = []
            for name, kind in relations:
                if kind == "v":
                    definition = legacy.execute(
                        "SELECT pg_get_viewdef(%s::regclass, true)", (name,)
                    ).fetchone()[0]
                    row_count = None
                    definition_md5 = hashlib.md5(
                        definition.encode("utf-8")).hexdigest()
                    relation_kind = "view"
                else:
                    row_count = int(legacy.execute(
                        'SELECT count(*) FROM public."{}"'.format(name)
                    ).fetchone()[0])
                    definition_md5 = None
                    relation_kind = "table"
                rows.append((relation_kind, name, definition_md5, row_count))
    finally:
        legacy.close()
    with owner_conn.transaction():
        with owner_conn.cursor() as cursor:
            for relation_kind, name, definition_md5, row_count in rows:
                cursor.execute(
                    "INSERT INTO papersim_private.observed_baseline (observed_at, "
                    "relation_kind, relation_name, definition_md5, row_count, "
                    "source_system_identifier, source_cluster, legacy_created_at, "
                    "legacy_operator, registered_by, registered_run) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (observed_at, relation_kind, name, definition_md5, row_count,
                     system_identifier, LEGACY_SOURCE_CLUSTER, None, None,
                     registered_by, registered_run))
    return {"registered": len(rows), "observed_at": observed_at,
            "relations": tuple(name for _, name, _, _ in rows)}


# ---------------------------------------------------------------------------
# CLI (dry-run default; --execute-production is the separate authorization)
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="papersim_import_legacy",
        description=(
            "FX-09L legacy history importer. Default is a pure DRY RUN from "
            "the archived repo snapshot (no database writes). Production "
            "insertion requires the separate --execute-production "
            "authorization flag. Paper evidence storage only."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR,
        help="archived legacy snapshot directory (default: %(default)s)")
    source.add_argument(
        "--from-legacy-db", action="store_true",
        help="read-only re-observation of the legacy face through the "
             "audited PAPERSIM_PG_DSN (validator-first, read-only session)")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--dsn",
        help="explicit target DSN for the managed face (validated by the "
             "single audited local DSN validator; isolated clusters)")
    target.add_argument(
        "--managed", action="store_true",
        help="target the project-private managed instance (requires "
             "--repo-root; application connection with identity binding)")
    parser.add_argument(
        "--repo-root", type=Path, default=Path("."),
        help="repository root for --managed and catalog reads (default: .)")
    parser.add_argument(
        "--execute-production", action="store_true",
        help="SEPARATE AUTHORIZATION GATE: without it the tool refuses any "
             "production insertion and stays a dry run")
    parser.add_argument(
        "--register-baseline", action="store_true",
        help="append observed-baseline evidence rows (owner face) from a "
             "read-only re-observation; requires --from-legacy-db and an "
             "owner target (--dsn owner DSN or --managed)")
    parser.add_argument(
        "--baseline-registered-by", default="papersim_import_legacy",
        help="registered_by value for --register-baseline rows")
    parser.add_argument(
        "--baseline-run", default=None,
        help="registered_run value for --register-baseline rows "
             "(default: UTC ISO timestamp of the run)")
    return parser


def _resolve_target_connection(args):
    store_psycopg = _adapter()
    if args.managed:
        if args.register_baseline:
            return store_psycopg.connect_owner(args.repo_root)
        return store_psycopg.connect_managed(args.repo_root)
    return store_psycopg.connect_explicit(args.dsn)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.register_baseline and not args.from_legacy_db:
        print("--register-baseline observes the legacy face: pass "
              "--from-legacy-db", file=sys.stderr)
        return 2
    has_target = args.dsn is not None or args.managed
    if has_target and not args.execute_production and not args.register_baseline:
        print("production insertion is not authorized: default is dry-run; "
              "rerun with --execute-production (separate operator "
              "authorization)", file=sys.stderr)
        return 2
    if args.execute_production and not has_target:
        print("--execute-production requires a target: --dsn or --managed",
              file=sys.stderr)
        return 2

    try:
        if args.from_legacy_db:
            store_psycopg = _adapter()

            def connect_legacy():
                return store_psycopg.connect_legacy_readonly(ledger.papersim_dsn())

            snapshot = load_snapshot_from_legacy_face(connect_legacy)
        else:
            snapshot = load_snapshot(args.snapshot_dir)
        plan = build_import_plan(snapshot)
    except PapersimImportError as error:
        print(f"papersim legacy import blocked: {error}", file=sys.stderr)
        return 2
    print(render_report(plan))

    if args.register_baseline:
        registered_run = args.baseline_run or datetime.now(UTC).isoformat()
        owner = _resolve_target_connection(args)
        try:
            result = register_baseline(
                owner, connect_legacy,
                registered_by=args.baseline_registered_by,
                registered_run=registered_run)
        finally:
            owner.close()
        print("observed baseline registered: {count} relations "
              "(observed_at={observed_at}, run={run})".format(
                  count=result["registered"],
                  observed_at=result["observed_at"].isoformat(),
                  run=registered_run))

    if args.execute_production:
        target = _resolve_target_connection(args)
        try:
            result = execute_import(target, plan)
        finally:
            target.close()
        print("production import executed: " + json.dumps(
            {"inserted": result["inserted"], "scan_key": result["scan_key"]},
            sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
