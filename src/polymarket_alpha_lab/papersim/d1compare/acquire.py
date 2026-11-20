"""Collect public Polymarket evidence in a project-managed research database.

The batch manifest is the observation record. Its sequence, request
metadata and clocks are independent of the immutable raw snapshots, which
deduplicate by content. Acquiring bytes does not create paper decisions.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit

from polymarket_alpha_lab.papersim import snapshots, store, store_psycopg
from polymarket_alpha_lab.papersim.adapters import contracts, polymarket
from polymarket_alpha_lab.papersim.d1compare import journal
from polymarket_alpha_lab.research_resolution import condition
from polymarket_alpha_lab.team_research_agent_types import strict_json

_VERSION = 1
_COMMAND = "capture-inputs"
_BATCH_KIND = "d1_public_capture_manifest"
_BODY_KIND = "d1_public_capture_response"
_BATCH_SOURCE = "d1:public-capture:v1"
_UNCOLLECTED_SOURCES = (
    "espn-soccer-schedule", "espn-nba-results", "understat-xg", "fotmob-xg",
    "soccer-kappa", "binance-btc-eth", "stooq-wti",
)
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
_SEALED_RECEIPT = (
    "SELECT command, config_digest, output_evidence, occurred_at "
    "FROM papersim.command_run_events WHERE run_event_key = %s"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_TOKEN_ID = re.compile(r"[0-9]{1,%d}" % polymarket.MAX_TOKEN_ID_DIGITS)


class AcquisitionError(ValueError):
    """An operator-facing error with a fixed code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise AcquisitionError("d1_capture_time_invalid")
    return value.astimezone(UTC)


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical(document: dict) -> bytes:
    return json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("ascii")


def _digest(content: bytes) -> str:
    return snapshots.snapshot_content_sha256(content)


def _request_fields(url: str) -> tuple[str, dict]:
    """Keep only the fixed public GET endpoints and their public parameters."""
    split = urlsplit(url)
    endpoint = f"{split.scheme}://{split.netloc}{split.path}"
    pairs = parse_qsl(split.query, keep_blank_values=True, strict_parsing=True)
    query = dict(pairs)
    if split.fragment or len(pairs) != len(query):
        raise AcquisitionError("d1_capture_public_request_invalid")
    if endpoint == polymarket.GAMMA_EVENTS_URL and query.keys() == {"tag_slug", "closed", "limit", "offset"}:
        source = polymarket.SOURCE_KIND_GAMMA
    elif endpoint == polymarket.CLOB_BOOK_URL and query.keys() == {"token_id"}:
        source = polymarket.SOURCE_KIND_CLOB
    else:
        raise AcquisitionError("d1_capture_public_request_invalid")
    return source, {"method": "GET", "query": query}


@dataclass
class _Request:
    sequence: int
    url: str
    source_kind: str
    request_meta: dict
    started_at: datetime
    sent: bool = False
    http_status: int | None = None
    read_at: datetime | None = None
    fetched_at: datetime | None = None
    finished_at: datetime | None = None
    body: bytes | None = None
    source_observed_at: datetime | None = None
    source_time_status: str = "unavailable"
    reason_code: str | None = None
    page: dict | None = None
    byte_length: int | None = None
    content_sha256: str | None = None

    def document(self, *, window_start: datetime, window_end: datetime,
                 decision_as_of: datetime | None) -> dict:
        fetched = self.fetched_at
        if fetched is None:
            window_status = None
        elif fetched < window_start or fetched < self.started_at:
            window_status = "clock_anomaly"
        elif fetched >= window_end:
            window_status = "late"
        elif decision_as_of is not None and fetched <= decision_as_of:
            window_status = "in_window"
        else:
            window_status = "without_cutoff"
        if self.reason_code:
            status = "failed" if self.sent else "not_attempted"
        elif window_status == "in_window":
            status = "received"
        else:
            status = window_status
        return {
            "sequence": self.sequence, "url": self.url, "source_kind": self.source_kind,
            "request_meta": self.request_meta, "sent": self.sent, "http_status": self.http_status,
            "started_at": _stamp(self.started_at), "finished_at": _stamp(self.finished_at),
            "utc_fetched_at": _stamp(fetched), "source_observed_at": _stamp(self.source_observed_at),
            "source_time_status": self.source_time_status, "window_status": window_status,
            "status": status, "reason_code": self.reason_code,
            "byte_length": self.byte_length, "content_sha256": self.content_sha256,
            "page": self.page,
        }


class _Response:
    """Remember the bytes returned to the adapter's existing framing check."""

    def __init__(self, response: Any, request: _Request) -> None:
        self.response = response
        self.request = request

    @property
    def headers(self):
        return self.response.headers

    @property
    def status(self):
        return self.response.status

    def geturl(self):
        return self.response.geturl()

    def __enter__(self):
        self.response.__enter__()
        return self

    def __exit__(self, *exc):
        return self.response.__exit__(*exc)

    def read(self, size: int) -> bytes:
        raw = self.response.read(size)
        self.request.read_at = _now_utc()
        self.request.body = raw
        return raw


class _Transport(polymarket.PublicResponseOpener):
    """Observe adapter requests without changing their transport or byte limits."""

    def __init__(self, window_start: datetime, window_end: datetime) -> None:
        self.window_start = window_start
        self.window_end = window_end
        self.requests: list[_Request] = []
        self.upstream = polymarket.BoundedPublicOpener()

    def open(self, url: str, *, timeout_seconds: float):
        if self.requests and self.requests[-1].finished_at is None:
            self.requests[-1].finished_at = _now_utc()
        source, fields = _request_fields(url)
        requested_at = _now_utc()
        attempt = _Request(len(self.requests) + 1, url, source, fields, requested_at)
        self.requests.append(attempt)
        remaining = (self.window_end - requested_at).total_seconds()
        if requested_at < self.window_start or remaining <= 0:
            attempt.reason_code = "d1_capture_window_closed"
            raise TimeoutError
        attempt.sent = True
        try:
            response = self.upstream.open(url, timeout_seconds=min(timeout_seconds, remaining))
        except HTTPError as error:
            if type(error.code) is int:
                attempt.http_status = error.code
            raise
        if type(response.status) is int:
            attempt.http_status = response.status
        return _Response(response, attempt)

    def received(self) -> datetime:
        # The adapter calls this only after validating the complete HTTP body.
        attempt = self.requests[-1]
        if attempt.read_at is None or type(attempt.body) is not bytes:
            raise AcquisitionError("d1_capture_response_unwitnessed")
        attempt.fetched_at = attempt.read_at
        attempt.finished_at = attempt.read_at
        return attempt.read_at

    def finish(self, offset: int, reason: str | None, *, budget_stop: bool = False) -> list[_Request]:
        attempts = self.requests[offset:]
        if not attempts:
            return attempts
        last = attempts[-1]
        if last.finished_at is None:
            last.finished_at = _now_utc()
        if reason is not None and not budget_stop and last.reason_code is None:
            last.reason_code = reason
        for attempt in attempts:
            if attempt.fetched_at is None:
                attempt.body = None
        return attempts


def _run_adapter(adapter, input_value, budget: contracts.FetchBudget, transport: _Transport):
    offset = len(transport.requests)
    try:
        result = adapter(input_value, budget=budget, allow_public_fetch=True,
                         opener=transport, now_utc=transport.received)
    except (OverflowError, OSError, ValueError):
        # Invalid upstream fields can also cause datetime/JSON conversion errors.
        result = contracts.AdapterResult.failure(contracts.AdapterStatus.UPSTREAM_ERROR,
                                                 "public_payload_invalid")
    is_budget_stop = result.status in (contracts.AdapterStatus.BUDGET_EXHAUSTED,
                                       contracts.AdapterStatus.DEADLINE_EXCEEDED)
    reason = result.reason_code
    if isinstance(result.value, polymarket.BooksSnapshot) and result.value.rejections:
        reason = result.value.rejections[0].reason_code
    return result, transport.finish(offset, reason, budget_stop=is_budget_stop)


def _discovery(tag: str, budget: contracts.FetchBudget, transport: _Transport) -> tuple[dict, tuple]:
    result, attempts = _run_adapter(polymarket.discover_markets,
                                    polymarket.DiscoveryQuery(tag), budget, transport)
    for attempt in attempts:
        if attempt.body is not None:
            attempt.source_time_status = "receipt_only"
            attempt.page = journal.gamma_page_index(attempt.body)
    value = result.value
    return ({
        "tag_slug": tag, "status": result.status.value, "reason_code": result.reason_code,
        "request_sequences": [attempt.sequence for attempt in attempts],
        "pagination": asdict(value.coverage) if value is not None else None,
        "rejections": [asdict(row) for row in value.rejections] if value is not None else [],
    }, value.markets if value is not None else ())


def _add_markets(markets: dict, accepted: tuple, rejections: list, attempts: Sequence[_Request]) -> None:
    for market in accepted:
        identity = market.condition_id
        sides = {"YES": market.yes_token_id, "NO": market.no_token_id}
        current = markets.setdefault(identity, {
            "condition_id": identity, "market_id": market.market_id, "slug": market.slug,
            "side_tokens": sides, "observations": [], "binding_status": "explicit_yes_no",
            "book_tokens": sorted(sides.values()),
        })
        if (current["slug"], current["market_id"], current["book_tokens"]) != (
                market.slug, market.market_id, sorted(sides.values())) or (
                current["side_tokens"] and current["side_tokens"] != sides):
            current["binding_status"] = "identity_drift"
            current["side_tokens"] = {}
            current["book_tokens"] = []

    # A1 intentionally only normalizes literal Yes/No. Preserve the two books
    # for an unambiguous raw token pair without assigning sides to other labels.
    raw_slots = contracts.FIRST_VERSION_MAX_MARKETS_PER_ROUND - len(accepted)
    unresolved = {row["condition_id"] for row in rejections if row["reason_code"] == "outcomes_unsupported"}
    drifted = {row["condition_id"] for row in rejections if row["reason_code"] == "identity_drift"}
    for identity in drifted & markets.keys():
        markets[identity].update(binding_status="identity_drift", side_tokens={}, book_tokens=[])
    for attempt in attempts:
        if attempt.page is None:
            continue
        for row in attempt.page["markets"]:
            identity = row["condition_id"]
            market = markets.get(identity)
            pair = row["token_ids"]
            if market is None and identity in unresolved and _valid_pair(pair):
                try:
                    condition(identity)
                except ValueError:
                    continue
                market = markets[identity] = {
                    "condition_id": identity, "market_id": row["market_id"], "slug": row["slug"],
                    "side_tokens": {}, "observations": [],
                    "binding_status": "unresolved_outcomes" if raw_slots > 0 else "target_cap",
                    "book_tokens": sorted(pair) if raw_slots > 0 else [],
                }
                raw_slots -= 1
            if market is None:
                continue
            if market["book_tokens"] and (row["slug"] != market["slug"]
                    or row["market_id"] != market["market_id"]
                    or (_valid_pair(pair) and market["book_tokens"] != sorted(pair))):
                market.update(binding_status="identity_drift", side_tokens={}, book_tokens=[])
            elif identity in unresolved and market["binding_status"] == "explicit_yes_no":
                market.update(binding_status="unresolved_outcomes", side_tokens={})
            market["observations"].append({"sequence": attempt.sequence, "path": row["path"]})


def _valid_pair(pair: object) -> bool:
    return (type(pair) is list and len(pair) == 2 and pair[0] != pair[1]
            and all(_TOKEN_ID.fullmatch(token) is not None for token in pair))


def _book(token: str, condition_id: str, budget: contracts.FetchBudget,
          transport: _Transport) -> dict:
    result, attempts = _run_adapter(polymarket.fetch_order_books, (token,), budget, transport)
    if not attempts:
        return {"token_id": token, "condition_id": condition_id, "request_sequence": None,
                "reason_code": result.reason_code}
    attempt = attempts[0]
    if attempt.body is not None:
        try:
            payload = strict_json(attempt.body.decode("utf-8"))
        except (ValueError, RecursionError):
            payload = None
        if isinstance(payload, dict):
            raw_timestamp = payload.get("timestamp")
            stamp = result.evidence[0].source_observed_at if result.evidence else None
            # Some rejected bodies still carry a valid upstream time.
            if stamp is None and (type(raw_timestamp) is int or
                                  (type(raw_timestamp) is str and raw_timestamp.isascii()
                                   and raw_timestamp.isdecimal() and len(raw_timestamp) <= 14)):
                try:
                    stamp = _EPOCH + timedelta(milliseconds=int(raw_timestamp))
                except OverflowError:
                    pass
            attempt.source_observed_at = stamp
            if stamp is not None:
                attempt.source_time_status = "upstream"
            else:
                attempt.source_time_status = "missing" if raw_timestamp is None else "invalid"
            if attempt.reason_code is None:
                if payload.get("asset_id") != token:
                    attempt.reason_code = "d1_capture_book_token_mismatch"
                elif payload.get("market") not in (None, condition_id):
                    attempt.reason_code = "d1_capture_book_condition_mismatch"
                elif stamp is None:
                    attempt.reason_code = "d1_capture_book_source_time_missing"
                elif stamp > attempt.fetched_at:
                    attempt.reason_code = "d1_capture_book_source_time_in_future"
    return {"token_id": token, "condition_id": condition_id, "request_sequence": attempt.sequence,
            "reason_code": attempt.reason_code}


def _save_responses(connection, attempts: Sequence[_Request]) -> None:
    for attempt in attempts:
        if attempt.body is None:
            continue
        digest = _digest(attempt.body)
        _register(connection, attempt.body, _BODY_KIND, attempt.url, attempt.fetched_at, digest)
        attempt.byte_length = len(attempt.body)
        attempt.content_sha256 = digest
        attempt.body = None


def _collect(connection, scope: dict, started_at: datetime,
             window_start: datetime, window_end: datetime) -> dict:
    budget = contracts.FetchBudget(deadline_monotonic=time.monotonic() + min(
        contracts.FIRST_VERSION_ROUND_DEADLINE_SECONDS, (window_end - started_at).total_seconds()))
    transport = _Transport(window_start, window_end)
    discoveries = []
    markets: dict[str, dict] = {}
    for tag in scope["tag_slugs"]:
        offset = len(transport.requests)
        discovery, accepted = _discovery(tag, budget, transport)
        discoveries.append(discovery)
        attempts = transport.requests[offset:]
        _add_markets(markets, accepted, discovery["rejections"], attempts)
        _save_responses(connection, attempts)
    token_owners: dict[str, set[str]] = {}
    for identity, market in markets.items():
        for token in market["book_tokens"]:
            token_owners.setdefault(token, set()).add(identity)
    books = []
    for token, owners in sorted(token_owners.items()):
        if len(owners) == 1:
            offset = len(transport.requests)
            books.append(_book(token, next(iter(owners)), budget, transport))
            _save_responses(connection, transport.requests[offset:])
        else:
            for identity in sorted(owners):
                books.append({"token_id": token, "condition_id": identity, "request_sequence": None,
                              "reason_code": "d1_capture_token_condition_conflict"})
    sealed_at = _now_utc()
    cutoff = sealed_at if started_at <= sealed_at < window_end else None
    if started_at >= window_end:
        window_status = "missed"
    elif sealed_at < started_at:
        window_status = "clock_anomaly"
    elif cutoff is None:
        window_status = "closed_before_seal"
    else:
        window_status = "sealed_inside"
    request_rows = [attempt.document(window_start=window_start, window_end=window_end,
                                     decision_as_of=cutoff) for attempt in transport.requests]
    by_sequence = {row["sequence"]: row for row in request_rows}
    tag_summaries = []
    for discovery in discoveries:
        pages = [by_sequence[number] for number in discovery["request_sequences"]]
        pagination = discovery["pagination"]
        tag_summaries.append({
            "tag_slug": discovery["tag_slug"], "status": discovery["status"],
            "reason_code": discovery["reason_code"], "pages_received": sum(row["page"] is not None for row in pages),
            "rejection_count": len(discovery["rejections"]), "pagination": pagination,
            "coverage_complete": bool(pagination and pagination["coverage_complete"]
                                      and all(page["status"] == "received" for page in pages)),
        })
    gamma_complete = all(row["coverage_complete"] for row in tag_summaries)
    books_received = sum(
        book["request_sequence"] is not None and
        by_sequence[book["request_sequence"]]["status"] == "received" for book in books)
    rejections_without_books = sum(
        rejection["reason_code"] != "outcomes_unsupported" or
        not markets.get(rejection["condition_id"], {}).get("book_tokens")
        for discovery in discoveries for rejection in discovery["rejections"])
    clob_complete = (books_received == len(books) and rejections_without_books == 0
                     and all(row["book_tokens"] for row in markets.values()))
    response_digests = [row["content_sha256"] for row in request_rows if row["content_sha256"] is not None]
    coverage = {
        "polymarket_complete": cutoff is not None and gamma_complete and clob_complete,
        "sources": {
            polymarket.SOURCE_KIND_GAMMA: "complete" if gamma_complete else "incomplete",
            polymarket.SOURCE_KIND_CLOB: (
                "incomplete" if not clob_complete else "complete" if books
                else "no_targets" if gamma_complete else "unknown_targets"),
        },
        "uncollected_sources": list(_UNCOLLECTED_SOURCES),
        "window_status": window_status,
        "tags": tag_summaries, "markets_with_mapping": sum(bool(row["side_tokens"]) for row in markets.values()),
        "markets_with_unresolved_binding": sum(not row["side_tokens"] for row in markets.values()),
        "books_requested": sum(book["request_sequence"] is not None and
                               by_sequence[book["request_sequence"]]["sent"] for book in books),
        "books_received": books_received, "book_targets": len(books),
        "rejections_without_books": rejections_without_books,
        "repeated_response_bodies": len(response_digests) - len(set(response_digests)),
        "late_responses": sum(row["window_status"] == "late" for row in request_rows),
        "failed_requests": sum(row["status"] == "failed" for row in request_rows),
        "budget": {
            "max_requests": budget.max_requests, "max_response_bytes": budget.max_response_bytes,
            "max_total_response_bytes": budget.max_total_response_bytes,
            "request_timeout_seconds": budget.request_timeout_seconds,
            "requests_charged": budget.requests_used, "response_bytes_charged": budget.response_bytes_used,
        },
    }
    return {
        "capture_version": _VERSION, "scope": scope,
        "started_at": _stamp(started_at), "sealed_at": _stamp(sealed_at),
        "decision_as_of": _stamp(cutoff), "requests": request_rows,
        "discoveries": discoveries, "markets": list(markets.values()), "books": books,
        "coverage": coverage,
    }


@contextmanager
def _managed(repo_root: Path):
    try:
        with closing(store_psycopg.connect_managed(Path(repo_root))) as connection:
            yield connection
    except AcquisitionError:
        raise
    except Exception:
        # Driver and configuration diagnostics may include private connection material.
        raise AcquisitionError("d1_capture_managed_failed") from None


def _register(connection, body: bytes, kind: str, source: str, at: datetime, digest: str) -> dict:
    record = snapshots.register_snapshot(connection, snapshots.SnapshotRegistration(
        content=body, kind=kind, source=source, fetched_at=at, schema_version=_VERSION))
    if record["content_sha256"] != digest or record["byte_length"] != len(body):
        raise AcquisitionError("d1_capture_snapshot_registration_mismatch")
    return record


def _snapshot(connection, digest: str) -> dict:
    record = snapshots.read_snapshot(connection, digest)
    if record is None:
        raise AcquisitionError("d1_capture_snapshot_missing")
    body = record["content"]
    if _digest(body) != digest or len(body) != record["byte_length"]:
        raise AcquisitionError("d1_capture_snapshot_integrity_failed")
    return record


def _load_batch(connection, digest: str) -> dict:
    record = _snapshot(connection, digest)
    if (record["kind"], record["source"], record["schema_version"]) != (
            _BATCH_KIND, _BATCH_SOURCE, _VERSION):
        raise AcquisitionError("d1_capture_manifest_invalid")
    document = json.loads(record["content"].decode("ascii"))
    if (not isinstance(document, dict) or document.get("capture_version") != _VERSION
            or document.get("sealed_at") != _stamp(record["fetched_at"].astimezone(UTC))
            or _canonical(document) != record["content"]):
        raise AcquisitionError("d1_capture_manifest_invalid")
    return document


def _read_entries(connection, document: dict) -> tuple[journal.JournalEntry, ...]:
    entries = []
    bodies = {}
    for sequence, row in enumerate(document["requests"], 1):
        source_kind, request_meta = _request_fields(row["url"])
        if (row["sequence"] != sequence or row["request_meta"] != request_meta
                or row["source_kind"] != source_kind):
            raise AcquisitionError("d1_capture_manifest_request_invalid")
        digest = row["content_sha256"]
        if digest is None:
            if row["utc_fetched_at"] is not None or row["byte_length"] is not None:
                raise AcquisitionError("d1_capture_manifest_request_invalid")
            continue
        if digest not in bodies:
            bodies[digest] = _snapshot(connection, digest)["content"]
        body = bodies[digest]
        if len(body) != row["byte_length"] or row["utc_fetched_at"] is None:
            raise AcquisitionError("d1_capture_manifest_request_invalid")
        entries.append(journal.capture(
            row["url"], datetime.fromisoformat(row["utc_fetched_at"]),
            {**request_meta, "sequence": sequence, "source_kind": source_kind,
             "source_observed_at": row["source_observed_at"], "status": row["status"],
             "source_time_status": row["source_time_status"], "window_status": row["window_status"],
             "reason_code": row["reason_code"]},
            body, content_sha256=digest))
    return tuple(entries)


def _summary(document: dict, digest: str, *, reused: bool) -> dict:
    scope = document["scope"]
    return {
        "batch_sha256": digest, "scope_sha256": _digest(_canonical(scope)),
        **scope, "started_at": document["started_at"],
        "decision_as_of": document["decision_as_of"], "sealed_at": document["sealed_at"],
        "status": "sealed" if document["coverage"]["polymarket_complete"] else "partial",
        "reused": reused, "request_count": len(document["requests"]),
        "response_count": sum(row["content_sha256"] is not None for row in document["requests"]),
        "coverage": document["coverage"],
    }


def capture_inputs(*, repo_root: Path, window_start: datetime, window_end: datetime,
                   tag_slugs: Sequence[str], allow_public_fetch: bool) -> dict:
    """Collect once per UTC window and tag set, and return its managed receipt.

    Use the project root of the designated D1 research instance. Retry of an
    already sealed scope verifies and returns the same bytes without networking.
    The window is inclusive at its start and exclusive at its end.
    """
    try:
        contracts.require_public_fetch(allow_public_fetch)
    except (contracts.FetchPermissionError, ValueError):
        raise AcquisitionError("public_fetch_not_allowed") from None
    start, end = _utc(window_start), _utc(window_end)
    if start >= end:
        raise AcquisitionError("d1_capture_window_invalid")
    if isinstance(tag_slugs, (str, bytes)) or not isinstance(tag_slugs, Sequence) or not tag_slugs:
        raise AcquisitionError("d1_capture_tags_invalid")
    try:
        tags = sorted({polymarket.DiscoveryQuery(tag).tag_slug for tag in tag_slugs})
    except ValueError:
        raise AcquisitionError("d1_capture_tags_invalid") from None
    scope = {"window_start": _stamp(start), "window_end": _stamp(end), "tag_slugs": tags}
    scope_sha = _digest(_canonical(scope))
    operation_key = f"{_BATCH_SOURCE}:{scope_sha}"
    sealed_key = store.command_run_event_key(operation_key, 1, "SUCCEEDED")
    with _managed(repo_root) as connection, connection.transaction():
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
        cursor.execute(_LOCK, (operation_key,))
        cursor.execute(_SEALED_RECEIPT, (sealed_key,))
        existing = cursor.fetchone()
        if existing is not None:
            command, saved_scope, evidence, sealed_at = existing
            if (command != _COMMAND or saved_scope != scope_sha
                    or not isinstance(evidence, list) or len(evidence) != 1):
                raise AcquisitionError("d1_capture_receipt_conflict")
            digest = snapshots.snapshot_digest_argument(evidence[0])
            document = _load_batch(connection, digest)
            if document["scope"] != scope or document["sealed_at"] != _stamp(sealed_at.astimezone(UTC)):
                raise AcquisitionError("d1_capture_receipt_conflict")
            _read_entries(connection, document)
            return _summary(document, digest, reused=True)
        started_at = _now_utc()
        if started_at < start:
            raise AcquisitionError("d1_capture_window_not_open")
        document = _collect(connection, scope, started_at, start, end)
        sealed_at = datetime.fromisoformat(document["sealed_at"])
        content = _canonical(document)
        digest = _digest(content)
        record = _register(connection, content, _BATCH_KIND, _BATCH_SOURCE, sealed_at, digest)
        if (record["kind"], record["source"], record["fetched_at"], record["schema_version"]) != (
                _BATCH_KIND, _BATCH_SOURCE, sealed_at, _VERSION):
            raise AcquisitionError("d1_capture_manifest_registration_conflict")
        store_psycopg.record_command_run_event(connection, store.CommandRunEventSubmission(
            operation_key=operation_key, command=_COMMAND, phase="SUCCEEDED", scheduled_for=start,
            occurred_at=sealed_at, config_digest=scope_sha, output_evidence=[digest]))
    return _summary(document, digest, reused=False)


def read_capture(*, repo_root: Path, batch_sha256: str) -> tuple[dict, tuple[journal.JournalEntry, ...]]:
    """Independently verify and read the manifest and exact responses in order.

    The manifest retains attempts without an admitted body. The returned
    journal contains every retained body, including late or invalid payloads.
    A consumer must check each request's status before selecting an input.
    """
    try:
        digest = snapshots.snapshot_digest_argument(batch_sha256)
    except ValueError:
        raise AcquisitionError("d1_capture_digest_invalid") from None
    with _managed(repo_root) as connection, connection.transaction():
        document = _load_batch(connection, digest)
        return document, _read_entries(connection, document)
