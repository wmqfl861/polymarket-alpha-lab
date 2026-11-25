"""D1 pure journal-to-pack builder (Path N market preparation).

Builds prepared-market pack records from the day's capture journal
(:mod:`.journal`) so the EXISTING managed pipeline consumes the SAME
contemporaneous capture the frozen Path L scripts read.  Every record
conforms to the B1 pack contract (``papersim/scan.py``
``_record_from_mapping`` / ``records_from_pack``): each carries the
frozen ``replay.replay_bundle_payload`` text of an
:class:`~polymarket_alpha_lab.papersim.pipeline.EntryRequest` built from
the existing field set, the FX-26 ``events`` materials, the book
snapshot digest and ``source_ref`` = journal url + sha256.

Purity: this module performs no I/O of its own -- journal entries and
market observations come in as objects, pack documents go out as plain
mappings.  Model evidence (probability records, variant quotes) is NOT
in the capture journal: those fields stay ``None`` (unknown) on the
built requests and the managed pipeline's own construction/provider
steps supply them at authorized run time -- nothing is fabricated here
(null/0/unknown stay distinct throughout).

Market-kind and side classification is likewise not re-derived from
question text: the operator's day plan states them per market
(:class:`MarketPlan`), which keeps terms verbatim and classification an
explicit, recorded decision.

Persistence boundary (binding review note 3): pack documents are
STAGING INPUTS whose digests reach the managed project-private
PostgreSQL cluster only through the reviewed paths (``run-scan`` on the
pack, ``snapshots.register_snapshot`` for the journal) inside
authorized run steps; a pack file is never a durable file-backed
substitute for PG registration.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import scan
from polymarket_alpha_lab.papersim.d1compare import journal as d1_journal
from polymarket_alpha_lab.papersim.pipeline import EntryRequest

__all__ = [
    "MarketPlan",
    "PackBuildError",
    "PackSpec",
    "ProvenanceMap",
    "build_entry_request",
    "build_pack",
    "build_pack_record",
    "events_materials",
    "observations_from_journal",
    "pack_document",
]

#: Market classes for the FX-26 event-key materials.
MARKET_CLASS_SPORTS = "sports"
MARKET_CLASS_CRYPTO = "crypto"


class PackBuildError(ValueError):
    """One pack-building refusal with a fixed code (never a silent skip)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PackSpec:
    """The queue-level identity stamped on every record of one pack."""

    queue: str
    team: str
    lineage: str
    model_name: str
    market_type: str | None = None
    source_kind: str = "clob_book"


@dataclass(frozen=True)
class MarketPlan:
    """The operator's per-market decision-shape statement (not derived).

    ``market_kind`` uses the pipeline vocabulary (``match_winner``/``ml``
    aliases, ``total``, ``spread``, ``btts``, ``team_total``,
    ``crypto_touch``, ``crypto_terminal``); ``side`` is the outcome
    label exactly as the market states it (e.g. ``"over"``, ``"yes"``,
    ``"home"``).  ``buy_token`` selects the bought token by its raw
    clobTokenIds value; when ``None`` the builder buys the token whose
    outcome label matches ``side`` and refuses on ambiguity.  All other
    fields are optional FX-26/event materials the journal cannot state
    by itself.
    """

    market_id: str
    market_kind: str
    side: str
    fixture: str
    buy_token: str | None = None
    line: float | None = None
    league: str | None = None
    home: str | None = None
    away: str | None = None
    asset: str | None = None
    gamma_event_id: str | None = None
    event_title: str | None = None
    market_class: str = MARKET_CLASS_SPORTS
    fee_category: str | None = None


@dataclass(frozen=True)
class ProvenanceMap:
    """One provenance map per pack: digests and per-record source refs."""

    pack_sha256: str
    journal_sha256: str
    record_count: int
    record_source_refs: tuple[str, ...]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "pack_sha256": self.pack_sha256,
            "journal_sha256": self.journal_sha256,
            "record_count": self.record_count,
            "record_source_refs": list(self.record_source_refs),
        }


def _require_text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise PackBuildError("pack_field_invalid", f"{name}: non-empty text required")
    return value


def _parse_stamp(raw: str | None, *, name: str) -> datetime.datetime | None:
    """Parse a raw Gamma timestamp; absent stays absent, never guessed."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError as error:
        raise PackBuildError("pack_timestamp_invalid",
                             f"{name}: {raw!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _buy_snapshot(observation: d1_journal.MarketObservation,
                  plan: MarketPlan) -> d1_journal.BookSnapshot:
    """The captured book of the token being bought (explicit, never guessed)."""
    if plan.buy_token is not None:
        snapshot = observation.books.get(plan.buy_token)
        if snapshot is None:
            raise PackBuildError("pack_book_missing",
                                 f"{plan.market_id}: token {plan.buy_token}")
        return snapshot
    side = plan.side.strip().lower()
    matches = [token for token, label in
               zip(observation.clob_token_ids, observation.outcomes, strict=False)
               if str(label).strip().lower() == side]
    if len(matches) != 1:
        raise PackBuildError("pack_buy_token_ambiguous",
                             f"{plan.market_id}: side {plan.side!r} matched "
                             f"{len(matches)} tokens")
    snapshot = observation.books.get(matches[0])
    if snapshot is None:
        raise PackBuildError("pack_book_missing",
                             f"{plan.market_id}: token {matches[0]} "
                             "has no captured book")
    return snapshot


def events_materials(observation: d1_journal.MarketObservation,
                     plan: MarketPlan) -> dict[str, Any]:
    """The FX-26 event-key materials mapping (``scan.EventKeyMaterials``).

    The Gamma EVENT id is not part of a market-row capture, so it comes
    from the plan when the enumeration recorded it and stays ``None``
    otherwise (the adapter-layer gap documented on
    ``scan.EventKeyMaterials``; the key falls back or refuses there --
    never fabricated here).
    """
    kickoff = _parse_stamp(observation.game_start_time_raw, name="gameStartTime")
    expiry = _parse_stamp(observation.end_date_raw, name="endDate")
    boundary = expiry if plan.market_class == MARKET_CLASS_CRYPTO else kickoff
    return {
        "market_class": plan.market_class,
        "gamma_event_id": plan.gamma_event_id or observation.gamma_event_id,
        "league": plan.league or observation.league_key,
        "home": plan.home,
        "away": plan.away,
        "kickoff_day": kickoff.date().isoformat() if kickoff else None,
        "asset": plan.asset,
        "expiry_at": expiry.isoformat() if expiry else None,
        "market_ref": observation.market_id,
        "event_start_at": boundary.isoformat() if boundary else None,
    }


def build_entry_request(observation: d1_journal.MarketObservation,
                        plan: MarketPlan, *,
                        now: datetime.datetime | None = None) -> EntryRequest:
    """Construct one ``EntryRequest`` from journal raw (existing field set).

    ``book`` is the RAW ask ladder of the bought token (price, size
    pairs off the captured levels -- the pipeline walks it afresh, a
    caller-computed walk is never trusted).  ``book_time`` is the book
    capture's ``utc_fetched_at`` and ``now`` the decision as-of (the
    market capture stamp when the caller does not state one).  Model
    evidence fields (``model_quote``, ``probability``, ``news`` ...)
    are left ``None``: they are unknown at pack time and the pipeline
    reports them as blocked/unknown rather than defaulting.
    """
    _require_text(plan.market_id, "plan.market_id")
    _require_text(plan.market_kind, "plan.market_kind")
    _require_text(plan.side, "plan.side")
    _require_text(plan.fixture, "plan.fixture")
    market_id = observation.market_id
    if market_id is None:
        raise PackBuildError("pack_market_id_unknown",
                             f"plan {plan.market_id}: journal market has no id")
    if market_id != plan.market_id:
        raise PackBuildError("pack_market_id_mismatch",
                             f"plan {plan.market_id} != journal {market_id}")
    snapshot = _buy_snapshot(observation, plan)
    ladder = snapshot.ask_ladder()
    if not ladder:
        raise PackBuildError("pack_book_ladder_empty",
                             f"{market_id}: no executable ask levels captured")
    if observation.question is None:
        raise PackBuildError("pack_question_missing", market_id)
    book_time = observation.as_of()
    as_of = now or book_time
    if as_of is None:
        raise PackBuildError("pack_as_of_unknown", market_id)
    return EntryRequest(
        market_id=market_id,
        question=observation.question,
        fixture=plan.fixture,
        market_kind=plan.market_kind,
        side=plan.side,
        book=list(ladder),
        fee_category=plan.fee_category,
        gamma_liquidity=observation.liquidity_num,
        line=plan.line,
        book_time=book_time,
        now=as_of,
    )


def build_pack_record(observation: d1_journal.MarketObservation, plan: MarketPlan,
                      spec: PackSpec) -> dict[str, Any]:
    """One pack-record mapping conforming to the ``scan.py`` record contract.

    The returned mapping round-trips through
    ``scan.records_from_pack``: required text fields, ISO
    ``evaluated_at``, ``request_bundle`` (the existing codec), FX-26
    ``events`` materials, ``book_snapshot_sha256`` (the journal book
    content digest) and ``source_ref`` = journal url + sha256.
    """
    request = build_entry_request(observation, plan)
    snapshot = _buy_snapshot(observation, plan)
    market_source = observation.market_source_ref()
    if market_source is None:
        raise PackBuildError("pack_source_ref_unknown", plan.market_id)
    book_source = f"{snapshot.source_url}#{snapshot.content_sha256}"
    evaluated_at = observation.as_of()
    if evaluated_at is None:
        raise PackBuildError("pack_as_of_unknown", plan.market_id)
    return {
        "queue": _require_text(spec.queue, "spec.queue"),
        "candidate_key": f"{plan.market_id}|{plan.side.strip().lower()}|"
                         f"{plan.market_kind.strip().lower()}",
        "team": _require_text(spec.team, "spec.team"),
        "side": plan.side,
        "market_kind": plan.market_kind,
        "lineage": _require_text(spec.lineage, "spec.lineage"),
        "model_name": _require_text(spec.model_name, "spec.model_name"),
        "evaluated_at": evaluated_at.isoformat(),
        "market_ref_kind": "market_id",
        "condition_id": observation.condition_id,
        "market_id": observation.market_id,
        "market_slug": observation.market_slug,
        "yes_token_id": observation.clob_token_ids[0]
        if observation.clob_token_ids else None,
        "no_token_id": observation.clob_token_ids[1]
        if len(observation.clob_token_ids) > 1 else None,
        "market_type": spec.market_type,
        "median_probability": None,
        "variant_quotes": "[]",
        "snapshot_refs": [book_source, market_source],
        "book_snapshot_sha256": snapshot.content_sha256,
        "source_kind": spec.source_kind,
        "source_ref": market_source,
        "events": events_materials(observation, plan),
        "request_bundle": replay.replay_bundle_payload(request),
    }


def observations_from_journal(
        entries: Sequence[d1_journal.JournalEntry],
        market_ids: Sequence[str],
        *, league_keys: Mapping[str, str] | None = None) \
        -> tuple[d1_journal.MarketObservation, ...]:
    """Join journal entries into market observations for the given ids.

    The join is deterministic over the capture: Gamma market payloads
    are indexed by their payload ``id`` and CLOB book payloads by their
    ``asset_id``; each requested market takes its own entry plus the
    books of ITS clobTokenIds.  A requested market with no captured
    payload is an explicit refusal; a market with no captured book
    yields an observation without books (the request builder refuses
    later, at the point the plan needs the ladder).
    """
    league_keys = league_keys or {}
    market_entries: dict[str, tuple[d1_journal.JournalEntry, dict[str, Any]]] = {}
    book_entries: dict[str, list[d1_journal.JournalEntry]] = {}
    for entry in entries:
        try:
            payload = json.loads(entry.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue  # non-JSON captures (CSV/text) are not market rows
        if isinstance(payload, Mapping) and "question" in payload \
                and payload.get("id") is not None:
            market_entries[str(payload["id"])] = (entry, payload)
        elif isinstance(payload, Mapping) and "bids" in payload \
                and "asks" in payload:
            token = payload.get("asset_id") or payload.get("token_id")
            if token is not None:
                book_entries.setdefault(str(token), []).append(entry)
    observations: list[d1_journal.MarketObservation] = []
    for market_id in market_ids:
        found = market_entries.get(str(market_id))
        if found is None:
            raise PackBuildError("pack_market_not_captured", str(market_id))
        entry, payload = found
        tokens_raw = payload.get("clobTokenIds")
        tokens = d1_journal._parse_listy(tokens_raw)  # noqa: SLF001 - same package join
        tokens = tokens if isinstance(tokens, list) else []
        books = [book for token in tokens
                 for book in book_entries.get(str(token), [])]
        observations.append(d1_journal.market_from_entries(
            entry, books, league_key=league_keys.get(str(market_id))))
    return tuple(observations)


def _pack_digest(records: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps([dict(record) for record in records], sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False,
                           allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_pack(observations: Sequence[d1_journal.MarketObservation],
               plans: Sequence[MarketPlan], spec: PackSpec, *,
               journal_entries: Sequence[d1_journal.JournalEntry] = (),
               ) -> tuple[list[dict[str, Any]], ProvenanceMap]:
    """Build one pack (records + its provenance map) from journal material.

    ``plans`` select and shape markets; every plan must find exactly one
    observation.  ``journal_entries`` (the full day journal) feed the
    journal digest on the provenance map when supplied.
    """
    by_id = {observation.market_id: observation for observation in observations
             if observation.market_id is not None}
    records: list[dict[str, Any]] = []
    source_refs: list[str] = []
    for plan in plans:
        observation = by_id.get(plan.market_id)
        if observation is None:
            raise PackBuildError("pack_market_missing", plan.market_id)
        record = build_pack_record(observation, plan, spec)
        records.append(record)
        source_refs.append(str(record["source_ref"]))
    digest = _pack_digest(records)
    journal_sha = (d1_journal.journal_digest(journal_entries)
                   if journal_entries else "")
    provenance = ProvenanceMap(
        pack_sha256=digest,
        journal_sha256=journal_sha,
        record_count=len(records),
        record_source_refs=tuple(source_refs),
    )
    return records, provenance


def pack_document(records: Sequence[Mapping[str, Any]],
                  provenance: ProvenanceMap) -> dict[str, Any]:
    """The staging pack document: the JSON array plus its provenance map.

    The ``records`` list is the pack array ``run-scan`` loads (write it
    as JSON text); the provenance map travels beside it for digest
    registration through the reviewed PG paths at authorized run time.
    """
    return {"pack_version": 1, "pack_sha256": provenance.pack_sha256,
            "records": [dict(record) for record in records],
            "provenance": provenance.as_mapping()}


def verify_pack_document(document: Mapping[str, Any]) -> str:
    """Recompute a pack document's digest and validate its records.

    Returns the recomputed pack sha256; raises on any mismatch or on a
    record that ``scan.records_from_pack`` would refuse (the B1 contract
    is the authority, so the check IS the existing loader).
    """
    if not isinstance(document, Mapping) or document.get("pack_version") != 1:
        raise PackBuildError("pack_document_invalid", "pack_version")
    records = document.get("records")
    if not isinstance(records, list):
        raise PackBuildError("pack_document_invalid", "records must be a list")
    digest = _pack_digest(records)
    stated = document.get("pack_sha256")
    if stated != digest:
        raise PackBuildError("pack_digest_mismatch", f"{stated} != {digest}")
    scan.records_from_pack(json.dumps(records))  # contract authority
    return digest
