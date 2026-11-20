"""D1 capture journal: record types plus pure verification and digest.

The journal is the ONE canonical contemporaneous capture that feeds both
comparison paths: Path L (the designated frozen mirror scripts) consumes
the recorded bytes in-stage through the recording urllib layer
(:mod:`.frozenrun`), and Path N packs are built from the same journal by
:mod:`.packbuild`.  Every entry carries exactly the capture tuple of the
accepted plan: ``(url, utc_fetched_at, request-meta-without-credentials,
response-bytes, sha256)``.

Preservation contract (plan section 4 / engineering defaults):

- **Origin**: the response URL, verbatim.
- **As-of**: ``utc_fetched_at``, timezone-aware UTC, never naive.
- **Executable odds**: raw CLOB book levels -- full bid/ask ladders with
  sizes, never a midpoint-only summary (:class:`BookSnapshot`,
  :class:`BookLevel` keep the raw string quotes alongside parsed floats).
- **Terms verbatim**: raw market question/outcome strings as the market
  stated them (basketball ML/spread/totals OT-inclusion is carried
  exactly as stated, never re-derived) -- :class:`MarketObservation`
  stores raw text fields, not normalized ones.
- **League/model keys, sides, time**: Gamma event id, league tag,
  outcome labels and ``gameStartTime``/``endDate`` raw strings.
- ``null`` / ``0`` / ``unknown`` are distinct: absent fields are
  ``None``, present zeros are ``0``, and unparseable material is kept as
  raw text with a ``None`` parse.

Persistence boundary (binding review note 3): journal documents are
STAGING INPUTS.  Their digests are registered into the managed
project-private PostgreSQL cluster only through the already-reviewed
paths (``snapshots.register_snapshot`` and ``run-scan``) inside
authorized run steps.  A journal file is never a durable file-backed
substitute for PG registration and never durable business state; tests
use temporary directories and clean up.

The functions here are pure over their explicit inputs: no I/O side
effects beyond the byte payloads handed in, no network, no DB, no
timers.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from polymarket_alpha_lab.team_research_agent_types import strict_json

__all__ = [
    "BookLevel",
    "BookSnapshot",
    "JournalEntry",
    "JournalError",
    "MarketObservation",
    "book_from_entry",
    "book_ladder",
    "capture",
    "entry_source_ref",
    "gamma_outcomes_by_token",
    "journal_digest",
    "journal_document",
    "entries_from_document",
    "gamma_page_index",
    "market_from_entries",
    "sanitize_query",
    "sanitize_request_meta",
    "verify_journal",
]


class JournalError(ValueError):
    """One journal refusal with a fixed code (never a silent skip)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


#: Request-meta keys that NEVER enter a journal entry, case-insensitive.
#: Credentials are not recorded anywhere in the capture (plan section 4).
CREDENTIAL_META_KEYS = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-api-key", "api-key", "apikey", "token", "access-token",
    "x-auth-token", "secret", "password", "credential", "credentials",
})

#: Query-parameter names scrubbed from recorded URLs/meta (same rule).
CREDENTIAL_QUERY_KEYS = frozenset({
    "key", "apikey", "api_key", "token", "access_token", "secret",
    "client_secret", "signature", "password", "credential",
})


def _scrub(mapping: Mapping[str, Any], blocked: frozenset[str]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in mapping.items():
        if str(key).strip().lower() in blocked:
            continue
        kept[str(key)] = value
    return kept


def sanitize_request_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Drop every credential-shaped key from request metadata.

    The sanitized copy is what journal entries store: method, host,
    path, query and the non-secret headers (e.g. ``User-Agent``).
    """
    if not isinstance(meta, Mapping):
        raise JournalError("journal_request_meta_invalid", type(meta).__name__)
    kept = _scrub(meta, CREDENTIAL_META_KEYS)
    if isinstance(kept.get("headers"), Mapping):
        kept["headers"] = _scrub(kept["headers"], CREDENTIAL_META_KEYS)
    if isinstance(kept.get("query"), Mapping):
        kept["query"] = sanitize_query(kept["query"])
    return kept


def sanitize_query(query: Mapping[str, Any]) -> dict[str, Any]:
    """Drop credential-shaped query parameters from a recorded query."""
    if not isinstance(query, Mapping):
        raise JournalError("journal_query_invalid", type(query).__name__)
    return _scrub(query, CREDENTIAL_QUERY_KEYS)


def _sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_utc(moment: datetime.datetime, name: str) -> datetime.datetime:
    if not isinstance(moment, datetime.datetime):
        raise JournalError("journal_timestamp_invalid", f"{name}: datetime required")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise JournalError("journal_timestamp_naive", name)
    return moment.astimezone(datetime.timezone.utc)


@dataclass(frozen=True)
class JournalEntry:
    """One recorded network read: origin, as-of, meta, bytes, digest.

    ``content`` is the RAW response payload; ``content_sha256`` is its
    SHA-256 hex digest.  ``request_meta`` is already credential-free
    (built through :func:`capture`, which sanitizes).
    """

    url: str
    utc_fetched_at: datetime.datetime
    request_meta: Mapping[str, Any]
    content: bytes
    content_sha256: str

    def source_ref(self) -> str:
        """Provenance reference: journal url + sha256."""
        return entry_source_ref(self)


def entry_source_ref(entry: JournalEntry) -> str:
    """``<url>#<sha256>`` -- the source-ref form used by packs."""
    return f"{entry.url}#{entry.content_sha256}"


def capture(url: str, utc_fetched_at: datetime.datetime,
            request_meta: Mapping[str, Any], content: bytes, *,
            content_sha256: str | None = None) -> JournalEntry:
    """Build one sanitized, digest-bound journal entry.

    ``content`` may be ``None`` only as an explicit miss marker is NOT
    supported: a failed read is not a capture.  The digest is recomputed
    and enforced when supplied.
    """
    if not isinstance(url, str) or not url.strip():
        raise JournalError("journal_url_invalid", "url must be non-empty text")
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise JournalError("journal_content_invalid", "response bytes required")
    payload = bytes(content)
    digest = _sha256_hex(payload)
    if content_sha256 is not None:
        if not isinstance(content_sha256, str) or content_sha256.strip().lower() != digest:
            raise JournalError("journal_digest_mismatch",
                               f"{content_sha256} != {digest}")
    return JournalEntry(
        url=url,
        utc_fetched_at=_require_utc(utc_fetched_at, "utc_fetched_at"),
        request_meta=sanitize_request_meta(request_meta),
        content=payload,
        content_sha256=digest,
    )


# ---------------------------------------------------------------------------
# Verification and digest (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JournalVerification:
    """Per-entry verification outcome plus the whole-journal verdict."""

    ok: bool
    entry_count: int
    failures: tuple[tuple[int, str, str], ...] = ()
    duplicate_digests: tuple[str, ...] = ()

    def summary(self) -> str:
        return (f"journal entries={self.entry_count} failures={len(self.failures)} "
                f"duplicate_digests={len(self.duplicate_digests)} "
                f"ok={self.ok}")


def verify_journal(entries: Sequence[JournalEntry]) -> JournalVerification:
    """Verify every entry: digest, url, timestamp, sanitized meta.

    Each entry's SHA-256 is RECOMPUTED over its bytes and compared with
    the recorded digest; the url must be non-empty, the timestamp
    timezone-aware UTC, and the request meta free of credential keys
    (the capture-time sanitizer is re-checked, not trusted).  Duplicate
    ``(url, sha256)`` pairs are reported separately (a re-read is legal
    evidence, but the duplication is surfaced, never hidden).
    """
    failures: list[tuple[int, str, str]] = []
    seen: dict[tuple[str, str], int] = {}
    duplicates: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, JournalEntry):
            failures.append((index, "journal_entry_invalid", type(entry).__name__))
            continue
        if not isinstance(entry.url, str) or not entry.url.strip():
            failures.append((index, "journal_url_invalid", entry.url[:80]))
        if _sha256_hex(entry.content) != entry.content_sha256:
            failures.append((index, "journal_digest_mismatch", entry.content_sha256))
        try:
            _require_utc(entry.utc_fetched_at, "utc_fetched_at")
        except JournalError as error:
            failures.append((index, error.code, error.detail))
        leaked = sorted(set(str(key).lower() for key in entry.request_meta)
                        & CREDENTIAL_META_KEYS)
        if leaked:
            failures.append((index, "journal_credential_meta_leak", ",".join(leaked)))
        headers = entry.request_meta.get("headers")
        if isinstance(headers, Mapping):
            leaked_headers = sorted(set(str(key).lower() for key in headers)
                                    & CREDENTIAL_META_KEYS)
            if leaked_headers:
                failures.append((index, "journal_credential_meta_leak",
                                 ",".join(leaked_headers)))
        key = (entry.url, entry.content_sha256)
        if key in seen:
            duplicates.add(entry.content_sha256)
        seen[key] = index
    return JournalVerification(
        ok=not failures,
        entry_count=len(entries),
        failures=tuple(failures),
        duplicate_digests=tuple(sorted(duplicates)),
    )


def journal_digest(entries: Sequence[JournalEntry]) -> str:
    """Canonical SHA-256 over the whole journal (order-independent).

    The preimage is canonical JSON of ``[utc_fetched_at, url,
    content_sha256]`` triples sorted lexicographically: byte content is
    represented by its per-entry digest, so the journal digest binds
    origins, as-of stamps and byte digests without duplicating payloads.
    """
    rows = sorted(
        (entry.utc_fetched_at.astimezone(datetime.timezone.utc).isoformat(),
         entry.url, entry.content_sha256)
        for entry in entries
    )
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return _sha256_hex(canonical.encode("utf-8"))


# ---------------------------------------------------------------------------
# Staging document (interchange only; never durable business state)
# ---------------------------------------------------------------------------


def journal_document(entries: Sequence[JournalEntry]) -> dict[str, Any]:
    """Interchange document for a day's journal (staging input).

    Content bytes are base64.  The document exists so the authorized
    run steps can hand the SAME journal bytes to pack building and
    register the journal digest through the reviewed PG paths; it is not
    a persistence layer.
    """
    return {
        "journal_version": 1,
        "journal_sha256": journal_digest(entries),
        "entries": [
            {
                "url": entry.url,
                "utc_fetched_at": entry.utc_fetched_at
                    .astimezone(datetime.timezone.utc).isoformat(),
                "request_meta": dict(entry.request_meta),
                "content_b64": base64.b64encode(entry.content).decode("ascii"),
                "content_sha256": entry.content_sha256,
            }
            for entry in entries
        ],
    }


def entries_from_document(document: Mapping[str, Any]) -> tuple[JournalEntry, ...]:
    """Rebuild journal entries from an interchange document (strict)."""
    if not isinstance(document, Mapping) or document.get("journal_version") != 1:
        raise JournalError("journal_document_invalid", "journal_version")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)):
        raise JournalError("journal_document_invalid", "entries must be a list")
    entries: list[JournalEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise JournalError("journal_document_invalid", "entry must be an object")
        try:
            content = base64.b64decode(str(raw.get("content_b64", "")), validate=True)
        except (binascii.Error, ValueError) as error:
            raise JournalError("journal_document_invalid", "content_b64") from error
        stamp = raw.get("utc_fetched_at")
        if isinstance(stamp, str):
            try:
                parsed = datetime.datetime.fromisoformat(stamp)
            except ValueError as inner:
                raise JournalError("journal_document_invalid",
                                   "utc_fetched_at") from inner
        else:
            parsed = stamp  # capture() validates the type
        entries.append(capture(
            url=str(raw.get("url", "")),
            utc_fetched_at=parsed,
            request_meta=raw.get("request_meta") or {},
            content=content,
            content_sha256=raw.get("content_sha256"),
        ))
    verification = verify_journal(entries)
    if not verification.ok:
        first = verification.failures[0]
        raise JournalError("journal_document_entry_invalid",
                           f"{first[1]}: {first[2]}")
    stated = document.get("journal_sha256")
    if isinstance(stated, str) and stated != journal_digest(entries):
        raise JournalError("journal_digest_mismatch", "journal_sha256")
    return tuple(entries)


# ---------------------------------------------------------------------------
# Parsed market material (terms verbatim, executable odds)
# ---------------------------------------------------------------------------


def _payload_json(entry: JournalEntry) -> Any:
    try:
        return json.loads(entry.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JournalError("journal_payload_not_json",
                           f"{entry.url}: {error}") from error


def _parse_listy(raw: Any) -> Any:
    """Gamma string-encoded lists ('["Yes","No"]') -> list; else as-is."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


def gamma_page_index(content: bytes) -> dict[str, Any] | None:
    """Locate events and markets inside one unchanged Gamma response.

    Paths are JSON Pointers into the full page bytes. The index retains
    label and token positions and leaves contract text in the cited page.
    An empty page is a valid index with an event count of zero.
    """
    try:
        payload = strict_json(content.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, list):
        return None
    events = []
    markets = []

    def label_array(value: Any) -> list[str] | None:
        if isinstance(value, str):
            try:
                value = strict_json(value)
            except (ValueError, RecursionError):
                return None
        if isinstance(value, list) and all(type(item) is str for item in value):
            return value
        return None  # Malformed arrays stay available through the raw path.

    for event_index, event_value in enumerate(payload):
        event = event_value if isinstance(event_value, dict) else {}
        event_path = f"/{event_index}"
        event_id = _text(event.get("id"))
        rows = event.get("markets")
        events.append({
            "path": event_path, "event_id": event_id, "event_slug": _text(event.get("slug")),
            "market_count": len(rows) if isinstance(rows, list) else None,
        })
        if not isinstance(rows, list):
            continue
        for market_index, market_value in enumerate(rows):
            market = market_value if isinstance(market_value, dict) else {}
            markets.append({
                "path": f"{event_path}/markets/{market_index}",
                "event_id": event_id,
                "market_id": _text(market.get("id")),
                "slug": _text(market.get("slug")),
                "condition_id": _text(market.get("conditionId")),
                "outcomes": label_array(market.get("outcomes")),
                "token_ids": label_array(market.get("clobTokenIds")),
            })
    return {"event_count": len(payload), "events": events, "markets": markets}


def gamma_outcomes_by_token(row: Mapping[str, Any]) -> dict[str, str] | None:
    """Compare two literal label/token pairs independently of array order."""
    labels = row["outcomes"]
    tokens = row["token_ids"]
    if labels is None or tokens is None or len(labels) != 2 or len(tokens) != 2 or tokens[0] == tokens[1]:
        return None
    return {token: label.strip().casefold() for token, label in zip(tokens, labels, strict=True)}


@dataclass(frozen=True)
class BookLevel:
    """One raw book level: parsed floats plus the ORIGINAL strings.

    ``price``/``size`` are ``None`` when the raw quote is absent or not
    a finite number (null/0/unknown stay distinct: a raw ``"0"`` parses
    to ``0.0``, an absent key is ``None``).
    """

    raw_price: str | None
    raw_size: str | None
    price: float | None
    size: float | None


@dataclass(frozen=True)
class BookSnapshot:
    """Executable odds: the full bid/ask ladder of ONE CLOB token book."""

    token_id: str | None
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    content_sha256: str
    source_url: str

    def best_bid(self) -> float | None:
        prices = [level.price for level in self.bids if level.price is not None]
        return max(prices) if prices else None

    def best_ask(self) -> float | None:
        prices = [level.price for level in self.asks if level.price is not None]
        return min(prices) if prices else None

    def ask_ladder(self) -> tuple[tuple[float, float], ...]:
        """The RAW ask ladder (price, size) used as ``EntryRequest.book``."""
        return tuple((level.price, level.size) for level in self.asks
                     if level.price is not None and level.size is not None)

    def bid_ladder(self) -> tuple[tuple[float, float], ...]:
        return tuple((level.price, level.size) for level in self.bids
                     if level.price is not None and level.size is not None)


def _book_level(raw: Any) -> BookLevel | None:
    if not isinstance(raw, Mapping):
        return None
    raw_price = raw.get("price")
    raw_size = raw.get("size")

    def _number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    return BookLevel(raw_price=raw_price if isinstance(raw_price, str) else None,
                     raw_size=raw_size if isinstance(raw_size, str) else None,
                     price=_number(raw_price), size=_number(raw_size))


def book_from_entry(entry: JournalEntry) -> BookSnapshot:
    """Parse one CLOB ``/book`` response into a full bid/ask ladder.

    Unparseable levels are kept as raw text with ``None`` parses where
    possible; a payload without ``bids``/``asks`` lists is an explicit
    refusal (a book capture must be a book).
    """
    payload = _payload_json(entry)
    if not isinstance(payload, Mapping):
        raise JournalError("journal_book_payload_invalid", entry.url)
    bids_raw = payload.get("bids")
    asks_raw = payload.get("asks")
    if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
        raise JournalError("journal_book_payload_invalid",
                           f"{entry.url}: bids/asks lists required")
    bids = tuple(level for level in (_book_level(raw) for raw in bids_raw)
                 if level is not None)
    asks = tuple(level for level in (_book_level(raw) for raw in asks_raw)
                 if level is not None)
    token = payload.get("asset_id") or payload.get("token_id")
    return BookSnapshot(
        token_id=str(token) if token is not None else None,
        bids=bids,
        asks=asks,
        content_sha256=entry.content_sha256,
        source_url=entry.url,
    )


def book_ladder(snapshot: BookSnapshot, side: str) -> tuple[tuple[float, float], ...]:
    """The (price, size) ladder of one side (``bid``/``ask``)."""
    normalized = str(side).strip().lower()
    if normalized == "bid":
        return snapshot.bid_ladder()
    if normalized == "ask":
        return snapshot.ask_ladder()
    raise JournalError("journal_book_side_invalid", side)


@dataclass(frozen=True)
class MarketObservation:
    """One market as the journal saw it: terms VERBATIM, never re-derived.

    Raw Gamma text fields (question, outcome labels, gameStartTime,
    endDate) are stored exactly as the market stated them; parsed views
    are computed on demand, never substituted.  ``books`` maps the raw
    clobTokenIds to their captured books (a missing book is an absent
    key, not an empty book).  OT-inclusion wording for basketball
    ML/spreads/totals lives inside the verbatim question/description
    text and is carried, never re-derived.
    """

    market_id: str | None
    question: str | None
    outcomes: tuple[str, ...]
    condition_id: str | None
    clob_token_ids: tuple[str, ...]
    market_slug: str | None
    gamma_event_id: str | None
    event_title: str | None
    event_slug: str | None
    league_key: str | None
    game_start_time_raw: str | None
    end_date_raw: str | None
    liquidity_num: float | None = None
    volume_24hr: float | None = None
    books: Mapping[str, BookSnapshot] = field(default_factory=dict)
    market_entry: JournalEntry | None = None

    def as_of(self) -> datetime.datetime | None:
        return self.market_entry.utc_fetched_at if self.market_entry else None

    def market_source_ref(self) -> str | None:
        return entry_source_ref(self.market_entry) if self.market_entry else None


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw)
    return text if text.strip() else None


def _optional_number(raw: Any) -> float | None:
    """Gamma numeric-or-null field: absent -> None, real 0 -> 0.0."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def market_from_entries(market_entry: JournalEntry,
                        book_entries: Sequence[JournalEntry] = (),
                        *, league_key: str | None = None) -> MarketObservation:
    """Build one market observation from its Gamma entry plus book entries.

    ``league_key`` is the league/model key when the caller can state it
    from the capture context (e.g. the tag the enumeration swept); it is
    ``None`` when unknown -- never guessed from team names.
    """
    payload = _payload_json(market_entry)
    if isinstance(payload, Mapping) and isinstance(payload.get("markets"), list) \
            and payload.get("question") is None:
        # An events-pagination row: take the FIRST market of the event
        # only when the caller captured the event; per-market entries
        # are the canonical form and refuse ambiguity otherwise.
        raise JournalError("journal_market_payload_ambiguous",
                           f"{market_entry.url}: event row; capture the market row")
    if not isinstance(payload, Mapping):
        raise JournalError("journal_market_payload_invalid", market_entry.url)
    outcomes_raw = _parse_listy(payload.get("outcomes"))
    outcomes = tuple(str(item) for item in outcomes_raw) \
        if isinstance(outcomes_raw, list) else ()
    tokens_raw = _parse_listy(payload.get("clobTokenIds"))
    tokens = tuple(str(item) for item in tokens_raw) \
        if isinstance(tokens_raw, list) else ()
    books: dict[str, BookSnapshot] = {}
    for book_entry in book_entries:
        snapshot = book_from_entry(book_entry)
        if snapshot.token_id is not None and snapshot.token_id in tokens:
            books[snapshot.token_id] = snapshot
        elif snapshot.token_id is None:
            # Book payload without an asset id: bind only when exactly
            # one token is in play, otherwise the binding is unknown.
            if len(tokens) == 1:
                books[tokens[0]] = snapshot
            else:
                raise JournalError("journal_book_token_unknown", book_entry.url)
        else:
            raise JournalError("journal_book_token_mismatch",
                               f"{book_entry.url}: {snapshot.token_id}")
    return MarketObservation(
        market_id=_text(payload.get("id")),
        question=_text(payload.get("question")),
        outcomes=outcomes,
        condition_id=_text(payload.get("conditionId")),
        clob_token_ids=tokens,
        market_slug=_text(payload.get("slug")),
        gamma_event_id=_text(payload.get("eventId")),
        event_title=None,
        event_slug=None,
        league_key=league_key if isinstance(league_key, str) else None,
        game_start_time_raw=_text(payload.get("gameStartTime")),
        end_date_raw=_text(payload.get("endDate")),
        liquidity_num=_optional_number(payload.get("liquidityNum")),
        volume_24hr=_optional_number(payload.get("volume24hr")),
        books=books,
        market_entry=market_entry,
    )
