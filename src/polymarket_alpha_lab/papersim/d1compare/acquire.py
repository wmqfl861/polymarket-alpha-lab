"""Collect public Polymarket evidence in a project-managed research database.

The batch manifest is the observation record. Its sequence, request
metadata and clocks are independent of the immutable raw snapshots, which
deduplicate by content. Acquiring bytes does not create paper decisions.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import InvalidOperation
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit

from polymarket_alpha_lab.papersim import snapshots, store, store_psycopg
from polymarket_alpha_lab.papersim.adapters import contracts, polymarket
from polymarket_alpha_lab.papersim.d1compare import journal
from polymarket_alpha_lab.research_resolution import condition

_VERSION = 2
_COMMAND = "capture-inputs"
_BATCH_KIND = "d1_public_capture_manifest"
_BODY_KIND = "d1_public_capture_response"
# The operation key is version-independent so an old scope returns its original batch.
_OPERATION_PREFIX = "d1:public-capture:v1"
_BATCH_SOURCES = {1: _OPERATION_PREFIX, _VERSION: "d1:public-capture:v2"}
_PUBLICATION_LOCK = "d1:public-capture:publish"
_BOOK_AGE_UNREGISTERED = "d1_capture_book_age_policy_unregistered"
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
_BINDING_ISSUE_ORDER = (
    "identity_drift", "token_condition_conflict", "outcome_tokens_invalid", "outcome_label_conflict",
    "contract_terms_unknown", "contract_terms_conflict", "market_view_conflict", "adapter_rejected",
)
_GAMMA_TRANSPORT_FAILURES = {
    "public_tls_failed", "public_response_too_large", "public_response_invalid", "public_http_rejected",
}


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


def _book_ages(source: datetime | None, received: datetime | None,
               cutoff: datetime | None, request_status: str | None) -> dict:
    """Record observed ages; the interval registration defines their limits."""
    if source is None or received is None:
        reason = "d1_capture_book_times_missing"
    elif cutoff is None:
        reason = "d1_capture_book_cutoff_missing"
    elif request_status != "received" or not source <= received <= cutoff:
        reason = "d1_capture_book_not_usable"
    else:
        reason = _BOOK_AGE_UNREGISTERED
    return {
        "source_age_seconds": (cutoff - source).total_seconds() if cutoff and source else None,
        "receipt_age_seconds": (cutoff - received).total_seconds() if cutoff and received else None,
        "status": "NOT_ASSESSABLE",
        "reason_code": reason,
    }


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
        row = {
            "sequence": self.sequence, "url": self.url, "source_kind": self.source_kind,
            "request_meta": self.request_meta, "sent": self.sent, "http_status": self.http_status,
            "started_at": _stamp(self.started_at), "finished_at": _stamp(self.finished_at),
            "utc_fetched_at": _stamp(fetched), "source_observed_at": _stamp(self.source_observed_at),
            "source_time_status": self.source_time_status, "window_status": window_status,
            "status": status, "reason_code": self.reason_code,
            "byte_length": self.byte_length, "content_sha256": self.content_sha256,
            "page": self.page,
        }
        if self.source_kind == polymarket.SOURCE_KIND_CLOB:
            row["freshness"] = _book_ages(self.source_observed_at, fetched, decision_as_of, status)
        return row


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


def _fetch_clob(token: str, budget: contracts.FetchBudget, transport: _Transport):
    offset = len(transport.requests)
    try:
        result = polymarket.fetch_order_books((token,), budget=budget, allow_public_fetch=True,
                                              opener=transport, now_utc=transport.received)
    except (OverflowError, OSError):
        # A typed epoch may be outside the platform's datetime range.
        result = contracts.AdapterResult.failure(contracts.AdapterStatus.UPSTREAM_ERROR,
                                                 "book_payload_invalid")
    is_budget_stop = result.status in (contracts.AdapterStatus.BUDGET_EXHAUSTED,
                                       contracts.AdapterStatus.DEADLINE_EXCEEDED)
    reason = result.reason_code
    if isinstance(result.value, polymarket.BooksSnapshot) and result.value.rejections:
        reason = result.value.rejections[0].reason_code
    return result, transport.finish(offset, reason, budget_stop=is_budget_stop)


def _parse_gamma_market(payload: object, fetched_at: datetime) -> tuple[polymarket.PolymarketMarket | None, str | None]:
    """Apply the existing Gamma row parser to each unaltered row independently."""
    try:
        market = polymarket._parse_market(payload)
        refusal = None
    except polymarket._MarketRejected as error:
        if error.reason_code != "outcomes_unsupported":
            return None, error.reason_code
        # A1 checks the label domain first. Validate context for the raw-pair
        # capture path with A1 too, discarding the synthetic side labels.
        try:
            market = polymarket._parse_market({**payload, "outcomes": ["Yes", "No"]})
        except polymarket._MarketRejected as rejection:
            return None, rejection.reason_code
        except (InvalidOperation, OverflowError, RecursionError):
            return None, "market_payload_invalid"
        refusal = error.reason_code
    except (InvalidOperation, OverflowError, RecursionError):
        return None, "market_payload_invalid"
    reason = _gamma_ineligible(payload, market.end_at, fetched_at)
    return (market, refusal) if reason is None else (None, reason)


def _gamma_ineligible(payload: dict, end_at: datetime, fetched_at: datetime) -> str | None:
    """Retain explicit public evidence that the market is unavailable for books."""
    if end_at <= fetched_at:
        return "market_expired"
    for field in ("acceptingOrders", "enableOrderBook", "closed", "archived", "active"):
        value = payload.get(field)
        if value is not None and type(value) is not bool:
            return "market_payload_invalid"
    if payload.get("acceptingOrders") is False or payload.get("enableOrderBook") is False:
        return "not_accepting_orders"
    if payload.get("closed") is True or payload.get("archived") is True or payload.get("active") is False:
        return "market_not_open"
    return None


def _discovery(tag: str, budget: contracts.FetchBudget, transport: _Transport) -> tuple[dict, tuple]:
    """Page with the existing A1 limits and retain gaps without losing later offsets."""
    query = polymarket.DiscoveryQuery(tag)
    offset = len(transport.requests)
    accepted: dict[str, polymarket.PolymarketMarket] = {}
    fingerprints: dict[str, tuple] = {}
    rejections = []
    rejected: set[str] = set()
    identities = set()
    duplicate_count = 0
    stopped = "page_cap"
    reason = None
    status = contracts.AdapterStatus.SUCCESS
    for number in range(polymarket.DISCOVERY_MAX_PAGES):
        if len(identities) >= polymarket.DISCOVERY_ACCUMULATION_CAP:
            stopped = "accumulation_cap"
            break
        url = polymarket.GAMMA_EVENTS_URL + "?" + urlencode({
            "tag_slug": tag, "closed": "true" if query.closed else "false",
            "limit": polymarket.GAMMA_EVENTS_PAGE_LIMIT, "offset": number * polymarket.GAMMA_EVENTS_PAGE_LIMIT,
        })
        page_offset = len(transport.requests)
        try:
            raw, fetched_at = polymarket._fetch_bytes(url, opener=transport, budget=budget, now_utc=transport.received)
        except contracts.BudgetExhaustedError as error:
            status = (contracts.AdapterStatus.DEADLINE_EXCEEDED
                      if error.reason_code == contracts.REASON_DEADLINE_EXCEEDED
                      else contracts.AdapterStatus.BUDGET_EXHAUSTED)
            reason = error.reason_code
            stopped = status.value
            transport.finish(page_offset, reason, budget_stop=True)
            break
        except polymarket._PublicReadError as error:
            reason = error.reason_code
            status = contracts.AdapterStatus.UPSTREAM_ERROR
            stopped = status.value
            attempts = transport.finish(page_offset, reason)
            if error.reason_code in _GAMMA_TRANSPORT_FAILURES or (attempts and not attempts[0].sent):
                break
            continue

        attempt = transport.requests[-1]
        attempt.source_time_status = "receipt_only"
        try:
            page = journal.decode_public_json(raw)
        except journal.JournalError as error:
            page = None
            attempt.reason_code = error.code
        attempt.page = journal._gamma_page_index(page)
        if not isinstance(page, list):
            attempt.reason_code = "public_payload_invalid"
        else:
            raw_markets = []
            for event in page:
                if isinstance(event, dict) and isinstance(event.get("markets"), list):
                    raw_markets.extend(event["markets"])
                else:
                    attempt.reason_code = "public_payload_invalid"
            page_sha = _digest(raw)
            for raw_market, row in zip(raw_markets, attempt.page["markets"], strict=True):
                identity = _condition_identity(row)
                if identity is not None:
                    identities.add(identity)
                market, refusal = _parse_gamma_market(raw_market, fetched_at)
                row["adapter_reason_code"] = refusal
                if market is not None:
                    row["fee_rate_bps"] = market.fee_rate_bps
                if refusal is not None:
                    rejections.append(_gamma_rejection(row, attempt, page_sha, refusal))
                    if identity is not None:
                        rejected.add(identity)
                        accepted.pop(identity, None)
                    continue
                # Compare only after each original quote passed A1. Open-market
                # reference prices move independently of the executable CLOB.
                fingerprint = (market.market_id, market.slug, market.yes_token_id, market.no_token_id,
                               market.fee_rate_bps, row["terms_sha256"])
                if identity in fingerprints:
                    if fingerprints[identity] == fingerprint:
                        duplicate_count += 1
                    else:
                        rejected.add(identity)
                        accepted.pop(identity, None)
                        rejections.append(_gamma_rejection(row, attempt, page_sha, "identity_drift"))
                else:
                    fingerprints[identity] = fingerprint
                    if identity not in rejected:
                        accepted[identity] = market

        if attempt.reason_code is not None:
            status = contracts.AdapterStatus.UPSTREAM_ERROR
            reason = attempt.reason_code
        if page == []:
            stopped = "last_page"
            break

    selected = sorted(accepted.values(), key=lambda market: (market.end_at, market.slug, market.condition_id))
    truncated = len(selected) > query.max_markets
    attempts = transport.requests[offset:]
    return ({
        "tag_slug": tag, "status": status.value, "reason_code": reason,
        "request_sequences": [attempt.sequence for attempt in attempts],
        "pagination": {
            "pages_fetched": sum(attempt.fetched_at is not None for attempt in attempts),
            "unique_markets_seen": len(identities), "duplicates_dropped": duplicate_count,
            "truncated_to_cap": truncated, "stopped_reason": stopped,
            "coverage_complete": stopped == "last_page" and not truncated and reason is None,
        },
        "rejections": rejections,
    }, tuple(selected[:query.max_markets]))


def _gamma_rejection(row: dict, attempt: _Request, page_sha: str, reason: str) -> dict:
    return {"condition_id": row["condition_id"], "slug": row["slug"],
            "reason_code": reason, "evidence_sha256": page_sha,
            "request_sequence": attempt.sequence, "path": row["path"]}


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
            if market is not None and identity in unresolved and market["binding_status"] == "explicit_yes_no":
                market.update(binding_status="unresolved_outcomes", side_tokens={})


def _gamma_appearances(attempts: Sequence[_Request]) -> Iterator[tuple[dict, dict]]:
    for attempt in attempts:
        if attempt.page is not None:
            for row in attempt.page["markets"]:
                yield {"sequence": attempt.sequence, "path": row["path"]}, row


def _condition_identity(row: dict) -> str | None:
    identity = row["condition_id"]
    try:
        condition(identity)
    except ValueError:
        return None
    return identity


def _market_aliases(row: dict) -> Iterator[tuple[str, str]]:
    for field in ("market_id", "slug"):
        value = row[field]
        if value is not None:
            yield field, value


def _index_market_observations(markets: dict, attempts: Sequence[_Request]) -> list[dict]:
    """Fold every raw occurrence before accepting a book target or side."""
    aliases: dict[tuple[str, str], str | None] = {}
    token_owners: dict[str, str | None] = {}
    for _, row in _gamma_appearances(attempts):
        identity = _condition_identity(row)
        if identity is None:
            continue
        markets.setdefault(identity, {
            "condition_id": identity, "market_id": row["market_id"], "slug": row["slug"],
            "side_tokens": {}, "observations": [], "binding_status": "adapter_unselected", "book_tokens": [],
        })
        for alias in _market_aliases(row):
            if aliases.setdefault(alias, identity) != identity:
                aliases[alias] = None  # More than one observed condition claims this identifier.
        for token in row["token_claims"]:
            if _TOKEN_ID.fullmatch(token) and token_owners.setdefault(token, identity) != identity:
                token_owners[token] = None
    for market in markets.values():
        market["binding_issues"] = []

    labels_seen: dict[str, dict[str, str]] = {}
    terms_seen: dict[str, str] = {}
    fees_seen: dict[str, float | None] = {}
    unbound = []
    for ref, row in _gamma_appearances(attempts):
        identity = _condition_identity(row)
        owners = {aliases[alias] for alias in _market_aliases(row) if alias in aliases}
        tokens = row["token_ids"]
        claims = {token_owners[token] for token in row["token_claims"] if token in token_owners}
        if identity is None:
            # Attach malformed identity to a uniquely identified market for
            # evidence. It still invalidates that market's binding.
            known = (owners | claims) - {None}
            if len(known) != 1:
                unbound.append(ref)
                for owner in sorted(known):
                    markets[owner]["binding_issues"].append({**ref, "reason_code": "identity_drift"})
                continue
            identity = next(iter(known))
        market = markets[identity]
        market["observations"].append(ref)
        issues = {}
        if (not row["identity_fields_valid"] or row["condition_id"] != identity or owners - {identity}
                or (row["market_id"], row["slug"]) != (market["market_id"], market["slug"])):
            issues["identity_drift"] = None

        if claims - {identity}:
            issues["token_condition_conflict"] = None
        labels = journal.gamma_outcomes_by_token(row)
        if labels is None or not _valid_pair(tokens):
            issues["outcome_tokens_invalid"] = None
        else:
            previous_labels = labels_seen.setdefault(identity, labels)
            if previous_labels.keys() != labels.keys():
                issues["identity_drift"] = None
            elif previous_labels != labels:
                issues["outcome_label_conflict"] = None

        terms = row["terms_sha256"]
        if terms is None:
            issues["contract_terms_unknown"] = None
        elif terms_seen.setdefault(identity, terms) != terms:
            issues["contract_terms_conflict"] = None
        refusal = row["adapter_reason_code"]
        if refusal is not None and refusal != "outcomes_unsupported":
            issues["adapter_rejected"] = refusal
        if "fee_rate_bps" in row and fees_seen.setdefault(identity, row["fee_rate_bps"]) != row["fee_rate_bps"]:
            issues["market_view_conflict"] = "fee_rate_bps"
        market["binding_issues"].extend({**ref, "reason_code": code, **({"detail": detail} if detail else {})}
                                         for code, detail in issues.items())

    for market in markets.values():
        codes = {issue["reason_code"] for issue in market["binding_issues"]}
        if codes:
            status = next(code for code in _BINDING_ISSUE_ORDER if code in codes)
            market.update(binding_status=status, side_tokens={}, book_tokens=[])
    return unbound


def _valid_pair(pair: object) -> bool:
    return (type(pair) is list and len(pair) == 2 and pair[0] != pair[1]
            and all(_TOKEN_ID.fullmatch(token) is not None for token in pair))


def _book(token: str, condition_id: str, budget: contracts.FetchBudget,
          transport: _Transport) -> dict:
    result, attempts = _fetch_clob(token, budget, transport)
    if not attempts:
        return {"token_id": token, "condition_id": condition_id, "request_sequence": None,
                "reason_code": result.reason_code}
    attempt = attempts[0]
    if attempt.body is not None:
        try:
            payload = journal.decode_public_json(attempt.body)
        except journal.JournalError:
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
    unique = {}
    for attempt in attempts:
        if attempt.body is None:
            continue
        digest = _digest(attempt.body)
        attempt.byte_length = len(attempt.body)
        attempt.content_sha256 = digest
        first = unique.get(digest)
        if first is None or attempt.fetched_at < first.fetched_at:
            unique[digest] = attempt
    for digest, attempt in sorted(unique.items()):
        _register(connection, attempt.body, _BODY_KIND, attempt.url, attempt.fetched_at, digest)
    for attempt in attempts:
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
    unbound_appearances = _index_market_observations(markets, transport.requests)
    token_owners: dict[str, set[str]] = {}
    for identity, market in markets.items():
        for token in market["book_tokens"]:
            token_owners.setdefault(token, set()).add(identity)
    books = []
    for token, owners in sorted(token_owners.items()):
        if len(owners) == 1:
            books.append(_book(token, next(iter(owners)), budget, transport))
        else:
            for identity in sorted(owners):
                books.append({"token_id": token, "condition_id": identity, "request_sequence": None,
                              "reason_code": "d1_capture_token_condition_conflict"})
    # Two different scopes can share arbitrary raw hashes. Serialize only the
    # D1 database publication, including the manifest hash computed below.
    connection.execute(_LOCK, (_PUBLICATION_LOCK,))
    _save_responses(connection, transport.requests)
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
    book_responses_in_window = sum(
        book["request_sequence"] is not None and
        by_sequence[book["request_sequence"]]["window_status"] == "in_window" for book in books)
    rejections_without_books = sum(
        rejection["reason_code"] != "outcomes_unsupported" or
        not markets.get(rejection["condition_id"], {}).get("book_tokens")
        for discovery in discoveries for rejection in discovery["rejections"])
    book_targets_known = (not unbound_appearances and rejections_without_books == 0
                          and all(row["book_tokens"] for row in markets.values()))
    clob_transport_complete = book_targets_known and book_responses_in_window == len(books)
    clob_inputs_complete = book_targets_known and books_received == len(books)
    no_book_targets = not books and gamma_complete and clob_transport_complete
    response_digests = [row["content_sha256"] for row in request_rows if row["content_sha256"] is not None]
    coverage = {
        "polymarket_complete": cutoff is not None and no_book_targets,
        "polymarket_transport_complete": cutoff is not None and gamma_complete and clob_transport_complete,
        "book_freshness": {
            "status": "NOT_NEEDED" if no_book_targets else "NOT_ASSESSABLE",
            "reason_code": None if no_book_targets else _BOOK_AGE_UNREGISTERED,
            "policy_ref": None,
        },
        "sources": {
            polymarket.SOURCE_KIND_GAMMA: "complete" if gamma_complete else "incomplete",
            polymarket.SOURCE_KIND_CLOB: (
                "incomplete" if not clob_inputs_complete else "not_assessable" if books
                else "no_targets" if gamma_complete else "unknown_targets"),
        },
        "uncollected_sources": list(_UNCOLLECTED_SOURCES),
        "window_status": window_status,
        "tags": tag_summaries, "markets_with_mapping": sum(bool(row["side_tokens"]) for row in markets.values()),
        "markets_with_unresolved_binding": sum(not row["side_tokens"] for row in markets.values()),
        "outcome_label_conflicts": sum(row["binding_status"] == "outcome_label_conflict" for row in markets.values()),
        "markets_with_binding_issues": sum(bool(row["binding_issues"]) for row in markets.values()),
        "unbound_market_appearances": len(unbound_appearances),
        "books_requested": sum(book["request_sequence"] is not None and
                               by_sequence[book["request_sequence"]]["sent"] for book in books),
        "books_received": books_received, "book_targets": len(books),
        "book_responses_in_window": book_responses_in_window,
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
        "unbound_market_appearances": unbound_appearances,
        "coverage": coverage,
    }


def _verify_read_connection(connection, repo_root: Path) -> None:
    """Verify the caller's active snapshot and its project binding without ending it."""
    from polymarket_alpha_lab.project_postgres import binding
    from polymarket_alpha_lab.project_postgres.files import Layout

    try:
        if connection.closed or connection.info.transaction_status.name != "INTRANS":
            raise AcquisitionError("d1_capture_read_transaction_invalid")
        root = Path(repo_root).resolve(strict=True)
        info = store_psycopg.read_instance(root)
        if info["root_sha256"] != Layout(root).root_hash:
            raise AcquisitionError("d1_capture_read_instance_mismatch")
        # A savepoint protects the caller's transaction if its role cannot
        # read the binding table. The caller retains the original snapshot.
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('transaction_isolation'), "
                           "current_setting('transaction_read_only')")
            isolation, readonly = cursor.fetchone()
            if isolation not in ("repeatable read", "serializable") or readonly != "on":
                raise AcquisitionError("d1_capture_read_transaction_invalid")
            with binding.bound(info["instance_id"], info["root_sha256"], info["system_identifier"]):
                binding.verify_connection(cursor)
    except AcquisitionError:
        raise
    except Exception:
        raise AcquisitionError("d1_capture_read_instance_mismatch") from None


@contextmanager
def _managed(repo_root: Path, *, _read_connection=None):
    try:
        if _read_connection is not None:
            _verify_read_connection(_read_connection, repo_root)
            yield _read_connection
            return
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
    version = record["schema_version"]
    if (record["kind"] != _BATCH_KIND or version not in _BATCH_SOURCES
            or record["source"] != _BATCH_SOURCES[version]):
        raise AcquisitionError("d1_capture_manifest_invalid")
    document = json.loads(record["content"].decode("ascii"))
    if (not isinstance(document, dict) or document.get("capture_version") != version
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
        if "freshness" in row:
            request_meta["freshness"] = row["freshness"]
        entries.append(journal.capture(
            row["url"], datetime.fromisoformat(row["utc_fetched_at"]),
            {**request_meta, "sequence": sequence, "source_kind": source_kind,
             "source_observed_at": row["source_observed_at"], "status": row["status"],
             "source_time_status": row["source_time_status"], "window_status": row["window_status"],
             "reason_code": row["reason_code"]},
            body, content_sha256=digest))
    return tuple(entries)


def _summary(document: dict, digest: str, *, reused: bool, occurred_at: datetime) -> dict:
    scope = document["scope"]
    coverage = document["coverage"]
    legacy = document["capture_version"] == 1
    if legacy:
        # Preserve the original manifest for forensic read-back. Its verdict
        # predates both the freshness record and the raw-label conflict check.
        coverage = {**coverage, "polymarket_complete": False,
                    "polymarket_transport_complete": None,
                    "book_freshness": {"status": "NOT_ASSESSABLE", "reason_code": _BOOK_AGE_UNREGISTERED,
                                       "policy_ref": None},
                    "original_coverage": document["coverage"]}
        coverage["sources"] = {**coverage["sources"], polymarket.SOURCE_KIND_CLOB: "not_assessable"}
    event_time = _stamp(occurred_at.astimezone(UTC))
    return {
        "batch_sha256": digest, "scope_sha256": _digest(_canonical(scope)),
        "capture_version": document["capture_version"],
        **scope, "started_at": document["started_at"],
        "decision_as_of": document["decision_as_of"], "sealed_at": document["sealed_at"],
        "occurred_at": event_time, "registered_at": None if legacy else event_time,
        "status": "sealed" if coverage["polymarket_complete"] else "partial",
        "reused": reused, "request_count": len(document["requests"]),
        "response_count": sum(row["content_sha256"] is not None for row in document["requests"]),
        "coverage": coverage,
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
    operation_key = f"{_OPERATION_PREFIX}:{scope_sha}"
    sealed_key = store.command_run_event_key(operation_key, 1, "SUCCEEDED")
    with _managed(repo_root) as connection, connection.transaction():
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
        cursor.execute(_LOCK, (operation_key,))
        cursor.execute(_SEALED_RECEIPT, (sealed_key,))
        existing = cursor.fetchone()
        if existing is not None:
            command, saved_scope, evidence, occurred_at = existing
            if (command != _COMMAND or saved_scope != scope_sha
                    or not isinstance(evidence, list) or len(evidence) != 1):
                raise AcquisitionError("d1_capture_receipt_conflict")
            digest = snapshots.snapshot_digest_argument(evidence[0])
            document = _load_batch(connection, digest)
            sealed_at = datetime.fromisoformat(document["sealed_at"])
            invalid_clock = (occurred_at != sealed_at if document["capture_version"] == 1
                             else occurred_at < sealed_at)
            if document["scope"] != scope or invalid_clock:
                raise AcquisitionError("d1_capture_receipt_conflict")
            _read_entries(connection, document)
            return _summary(document, digest, reused=True, occurred_at=occurred_at)
        started_at = _now_utc()
        if started_at < start:
            raise AcquisitionError("d1_capture_window_not_open")
        document = _collect(connection, scope, started_at, start, end)
        sealed_at = datetime.fromisoformat(document["sealed_at"])
        content = _canonical(document)
        digest = _digest(content)
        source = _BATCH_SOURCES[_VERSION]
        record = _register(connection, content, _BATCH_KIND, source, sealed_at, digest)
        if (record["kind"], record["source"], record["fetched_at"], record["schema_version"]) != (
                _BATCH_KIND, source, sealed_at, _VERSION):
            raise AcquisitionError("d1_capture_manifest_registration_conflict")
        registered_at = _now_utc()
        if registered_at < sealed_at:
            raise AcquisitionError("d1_capture_receipt_clock_anomaly")
        store_psycopg.record_command_run_event(connection, store.CommandRunEventSubmission(
            operation_key=operation_key, command=_COMMAND, phase="SUCCEEDED", scheduled_for=start,
            occurred_at=registered_at, config_digest=scope_sha, output_evidence=[digest]))
    return _summary(document, digest, reused=False, occurred_at=registered_at)


def read_capture(*, repo_root: Path, batch_sha256: str,
                 _connection=None) -> tuple[dict, tuple[journal.JournalEntry, ...]]:
    """Independently verify and read the manifest and exact responses in order.

    The manifest retains attempts without an admitted body. The returned
    journal contains every retained body, including late or invalid payloads.
    Version 1 preserves the original receipt for audit. Decision consumers
    need the interval's registered freshness assessment before using a book.
    A preflight caller can supply its already active read-only snapshot.
    """
    try:
        digest = snapshots.snapshot_digest_argument(batch_sha256)
    except ValueError:
        raise AcquisitionError("d1_capture_digest_invalid") from None
    with _managed(repo_root, _read_connection=_connection) as connection, connection.transaction():
        document = _load_batch(connection, digest)
        return document, _read_entries(connection, document)
