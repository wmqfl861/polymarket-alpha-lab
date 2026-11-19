"""A1 Polymarket public-interface adapter: bounded Gamma/CLOB reads (FX-15).

Single responsibility (M2 plan section 2.2): the four public
entry points :func:`discover_markets`, :func:`fetch_market`,
:func:`fetch_order_books` and :func:`fetch_resolution` turn bounded,
permission-gated reads of the public Polymarket interfaces into
:class:`~polymarket_alpha_lab.papersim.adapters.contracts.AdapterResult`
values. This module opens no database session, writes no cache file, no
JSONL journal and no file at all; it holds no credentials and grants no
execution authorization (``paper_only`` / ``report_only`` /
``readonly`` restate the frozen contract flags).

Reuse map (M2 plan section 2.2):

- Every single response body is read through
  :func:`polymarket_alpha_lab.public_http.read_public_body` with the
  exact ``budget.next_response_read_limit()``; the default-unbounded
  ``api.UrlopenTransport.get_json`` path is never used. Endpoint
  organization (gamma/clob base URLs plus encoded GET parameters)
  mirrors ``api.PolymarketPublicClient`` without importing its
  transport.
- Single-market Gamma context reads prefer the existing bounded
  :class:`~polymarket_alpha_lab.team_research_gamma.GammaResearchReader`
  whenever the shared budget's exact read limit admits the reader's
  fixed 1 MiB cap; a tighter budget takes the adapter's own bounded
  GET so the read limit stays exact in every case. The existing V1
  clients are not refactored.

Data-source order (M2 plan section 2.2): Gamma first (events, markets,
rules, times, token mapping, settlement state), CLOB second
(executable order books and terminal-state cross-validation). The
Data API is NOT enabled in A1: no A1 function needs a field that has
been verified as officially provided there, and the M2 plan forbids
treating it as an assumed universal settlement interface. Web scraping
is not a data path here.

Semantic guarantees pinned by tests:

- Gamma ``outcomePrices`` are market observation prices (reference
  last) only and live in :attr:`PolymarketMarket.reference_last`;
  executable quote models (:class:`OrderBookSnapshot`) have no
  reference-price field at all.
- Price ``0`` is a value, missing is ``None`` and unknown booleans
  stay ``None``; YES/NO token correspondence is explicit and resolved
  by outcome label, so reversed ``["No", "Yes"]`` payloads map
  correctly.
- Candidate-level problems (token mapping conflict, unresolvable
  direction, uninterpretable fee unit, conflicting pagination
  duplicates / identity drift) reject that candidate and keep the raw
  page evidence; Gamma/CLOB terminal conflicts are recorded as a
  ``conflict`` verdict, never resolved by picking a favorable side.
- ``/events`` pagination uses ``limit=50`` (larger limits are rejected
  upstream with HTTP 406), is bounded by the shared budget, a page
  cap and an accumulation cap, and stops on the first empty page.
  Results beyond ``FIRST_VERSION_MAX_MARKETS_PER_ROUND`` are
  truncated in the frozen order (end time, slug, condition id) with
  ``coverage_complete=False``; a truncated scan never claims to have
  seen the whole market universe.
- Failures carry fixed machine-readable ``reason_code`` strings only;
  URLs, query parameters, upstream exception text and response bodies
  never enter them, and nothing is printed to stdout.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import IncompleteRead
import math
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from polymarket_alpha_lab.papersim.adapters.contracts import (
    AdapterResult,
    AdapterStatus,
    BudgetExhaustedError,
    FetchBudget,
    FetchEvidence,
    FetchPermissionError,
    FIRST_VERSION_MAX_MARKETS_PER_ROUND,
    PAPER_ONLY,
    READONLY,
    REASON_DEADLINE_EXCEEDED,
    REASON_PUBLIC_FETCH_NOT_ALLOWED,
    REPORT_ONLY,
    require_public_fetch,
    sha256_hexdigest,
)
from polymarket_alpha_lab.public_http import PublicBodyError, read_public_body
from polymarket_alpha_lab.research_resolution import condition as _require_condition_id
from polymarket_alpha_lab.team_research_agent_types import strict_json
from polymarket_alpha_lab.team_research_gamma import GammaResearchReader
from polymarket_alpha_lab.team_research_intake import (
    GAMMA_MARKET_PREFIX,
    MAX_GAMMA_RESPONSE_BYTES,
    require_market_slug,
)

# Phase 1 boundary flags (project iron rule 3), restating the frozen
# contract constants for this module's self-description.
assert PAPER_ONLY is True and REPORT_ONLY is True and READONLY is True

# Fixed public origins (mirrors api.PolymarketPublicClient's base URLs;
# no other origin is ever requested and redirects are refused).
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
GAMMA_EVENTS_URL = GAMMA_BASE_URL + "/events"
GAMMA_MARKETS_BY_CONDITION_URL = GAMMA_BASE_URL + "/markets"
CLOB_BASE_URL = "https://clob.polymarket.com"
CLOB_BOOK_URL = CLOB_BASE_URL + "/book"

# Known operational fact: Gamma /events rejects pagination limits above
# 50 with HTTP 406, so every discovery page requests exactly 50 events.
GAMMA_EVENTS_PAGE_LIMIT = 50

# Bounded discovery guards (M2 plan section 2.2: bounded, no infinite
# paging; the shared FetchBudget provides the per-round bound).
DISCOVERY_MAX_PAGES = 40
DISCOVERY_ACCUMULATION_CAP = 400

# Terminal cross-validation epsilon: a CLOB best bid is consistent with
# a Gamma terminal corner only inside this distance of 0/1.
TERMINAL_PRICE_EPSILON = 0.01

MAX_TOKEN_ID_DIGITS = 78
_TOKEN_ID = re.compile(r"[0-9]{1,%d}" % MAX_TOKEN_ID_DIGITS)
_TAG_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

SOURCE_KIND_GAMMA = "polymarket-gamma"
SOURCE_KIND_CLOB = "polymarket-clob"

# Fixed upstream-read reason codes (closed vocabulary; no URLs/bodies).
PUBLIC_READ_REASONS = (
    "public_http_rejected",
    "public_tls_failed",
    "public_timeout",
    "public_connection_interrupted",
    "public_response_incomplete",
    "public_response_framing_invalid",
    "public_response_too_large",
    "public_response_empty",
    "public_response_invalid",
    "public_payload_invalid",
    "public_fetch_failed",
    "gamma_slug_read_failed",
)
_PUBLIC_BODY_REASONS = (
    "public_response_framing_invalid",
    "public_response_too_large",
    "public_response_incomplete",
    "public_response_empty",
)

# Fixed candidate-rejection reason codes (kept per candidate with the
# raw page evidence; the same codes close the single-market failure
# vocabulary through _REJECTION_STATUS below).
CANDIDATE_REJECTION_REASONS = (
    "market_payload_invalid",
    "market_time_invalid",
    "market_not_found",
    "market_identity_mismatch",
    "market_identity_ambiguous",
    "token_mapping_missing",
    "token_mapping_conflict",
    "outcomes_unsupported",
    "fee_unit_uninterpretable",
    "identity_drift",
    "book_payload_invalid",
)

# Discovery stop reasons: "last_page" means an empty page proved the end
# of the tag's window; the other two mean coverage stopped early.
DISCOVERY_STOP_REASONS = ("last_page", "page_cap", "accumulation_cap")

# Gamma terminal states and cross-validation verdicts.
GAMMA_TERMINAL_OUTCOMES = ("not_terminal", "terminal_yes", "terminal_no")
TERMINAL_CONSISTENCY_VERDICTS = (
    "consistent",
    "conflict",
    "inconclusive",
    "unverified",
)

_REJECTION_STATUS: Mapping[str, AdapterStatus] = {
    "market_not_found": AdapterStatus.INSUFFICIENT_DATA,
    "market_identity_mismatch": AdapterStatus.INSUFFICIENT_DATA,
    "market_identity_ambiguous": AdapterStatus.INSUFFICIENT_DATA,
    "market_time_invalid": AdapterStatus.INSUFFICIENT_DATA,
    "token_mapping_missing": AdapterStatus.INSUFFICIENT_DATA,
    "token_mapping_conflict": AdapterStatus.INSUFFICIENT_DATA,
    "outcomes_unsupported": AdapterStatus.UNSUPPORTED,
    "fee_unit_uninterpretable": AdapterStatus.UNSUPPORTED,
    "market_payload_invalid": AdapterStatus.UPSTREAM_ERROR,
}


class _PublicReadError(Exception):
    """One failed public read; ``str()`` is a fixed reason code only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _MarketRejected(Exception):
    """One unusable market/book payload; fixed reason code only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# input models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    """Bounded Gamma ``/events`` discovery window for one queue tag.

    ``tag_slug`` scopes the discovery (for example ``bitcoin`` or
    ``ethereum`` for the V1 crypto queues); ``closed`` mirrors Gamma's
    own filter parameter exactly (``False`` requests open events);
    ``max_markets`` is capped at ``FIRST_VERSION_MAX_MARKETS_PER_ROUND``
    and result truncation above it is always recorded, never hidden.
    """

    tag_slug: str
    closed: bool = False
    max_markets: int = FIRST_VERSION_MAX_MARKETS_PER_ROUND

    def __post_init__(self) -> None:
        if (
            type(self.tag_slug) is not str
            or len(self.tag_slug) > 100
            or _TAG_SLUG.fullmatch(self.tag_slug) is None
        ):
            raise ValueError("discovery_tag_slug_invalid")
        if type(self.closed) is not bool:
            raise ValueError("discovery_closed_invalid")
        if (
            type(self.max_markets) is not int
            or not 1 <= self.max_markets <= FIRST_VERSION_MAX_MARKETS_PER_ROUND
        ):
            raise ValueError("discovery_max_markets_invalid")


@dataclass(frozen=True, slots=True)
class MarketRef:
    """Validated public market identifiers for single-market reads.

    At least one of ``condition_id`` (``0x`` plus 64 lowercase hex) and
    ``slug`` (canonical Gamma slug) must be present. When both are
    given the slug is the primary read path and the returned payload's
    condition id must match, otherwise the market is rejected for
    identity drift.
    """

    condition_id: str | None = None
    slug: str | None = None

    def __post_init__(self) -> None:
        if self.condition_id is not None:
            if type(self.condition_id) is not str:
                raise ValueError("market_ref_condition_invalid")
            _require_condition_id(self.condition_id)
        if self.slug is not None:
            if type(self.slug) is not str:
                raise ValueError("market_ref_slug_invalid")
            require_market_slug(self.slug)
        if self.condition_id is None and self.slug is None:
            raise ValueError("market_ref_missing")


# ---------------------------------------------------------------------------
# output models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReferencePrices:
    """Gamma ``outcomePrices`` as YES/NO-aligned observation prices.

    Market observation (reference last) prices only - never executable
    bid/ask quotes. Each side is a parsed float in ``[0, 1]``, ``None``
    when the upstream value is missing or blank.
    """

    yes_last: float | None
    no_last: float | None

    def __post_init__(self) -> None:
        for name in ("yes_last", "no_last"):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not float
                or not math.isfinite(value)
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError("reference_price_invalid")


@dataclass(frozen=True, slots=True)
class PolymarketMarket:
    """One normalized binary market with an explicit YES/NO token map.

    ``outcome_labels`` is stored YES-first regardless of the payload's
    label order, so ``yes_token_id``/``no_token_id`` correspondence is
    explicit. ``fee_rate_bps`` is Gamma fee metadata in basis points
    (``0.0`` is a value; ``None`` means the field is absent/blank).
    ``reference_last`` is the segregated Gamma observation price.
    """

    condition_id: str
    market_id: str | None
    slug: str
    question: str
    outcome_labels: tuple[str, str]
    yes_token_id: str
    no_token_id: str
    start_at: datetime | None
    game_start_at: datetime | None
    end_at: datetime
    closed: bool | None
    active: bool | None
    archived: bool | None
    category: str | None
    description: str | None
    resolution_status: str | None
    fee_rate_bps: float | None
    reference_last: ReferencePrices | None

    def __post_init__(self) -> None:
        _require_condition_id(self.condition_id)
        if self.market_id is not None and type(self.market_id) is not str:
            raise ValueError("market_id_invalid")
        require_market_slug(self.slug)
        if type(self.question) is not str or not self.question or len(self.question) > 2000:
            raise ValueError("question_invalid")
        if (
            type(self.outcome_labels) is not tuple
            or len(self.outcome_labels) != 2
            or any(type(label) is not str or not label for label in self.outcome_labels)
            or self.outcome_labels[0].lower() != "yes"
            or self.outcome_labels[1].lower() != "no"
        ):
            raise ValueError("outcome_labels_invalid")
        for name in ("yes_token_id", "no_token_id"):
            value = getattr(self, name)
            if type(value) is not str or _TOKEN_ID.fullmatch(value) is None:
                raise ValueError("token_id_invalid")
        if self.yes_token_id == self.no_token_id:
            raise ValueError("token_ids_not_distinct")
        for name in ("start_at", "game_start_at"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise ValueError("market_time_invalid")
        if not isinstance(self.end_at, datetime) or self.end_at.tzinfo is None:
            raise ValueError("market_time_invalid")
        for name in ("closed", "active", "archived"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise ValueError("market_flag_invalid")
        for name in ("category", "description", "resolution_status"):
            value = getattr(self, name)
            if value is not None and type(value) is not str:
                raise ValueError("market_metadata_invalid")
        if self.fee_rate_bps is not None and (
            type(self.fee_rate_bps) is not float
            or not math.isfinite(self.fee_rate_bps)
            or not 0.0 <= self.fee_rate_bps <= 10000.0
        ):
            raise ValueError("fee_rate_invalid")


@dataclass(frozen=True, slots=True)
class MarketRejection:
    """One rejected candidate with a fixed reason and page digest.

    ``condition_id``/``slug`` are best-effort identity fields from the
    rejected payload (either may be ``None`` when unparseable). No
    upstream text is carried; ``evidence_sha256`` links the rejection
    to the raw page evidence kept on the result.
    """

    condition_id: str | None
    slug: str | None
    reason_code: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        for name in ("condition_id", "slug"):
            value = getattr(self, name)
            if value is not None and type(value) is not str:
                raise ValueError("rejection_identity_invalid")
        if self.reason_code not in CANDIDATE_REJECTION_REASONS:
            raise ValueError("rejection_reason_invalid")
        if re.fullmatch(r"[0-9a-f]{64}", self.evidence_sha256) is None:
            raise ValueError("rejection_evidence_invalid")


@dataclass(frozen=True, slots=True)
class DiscoveryCoverage:
    """What one discovery pass actually saw; never overclaims.

    ``unique_markets_seen`` counts distinct market identities folded
    (accepted, rejected or drifted); payloads without any parseable
    identity appear only in ``rejections``.
    """

    pages_fetched: int
    unique_markets_seen: int
    duplicates_dropped: int
    truncated_to_cap: bool
    coverage_complete: bool
    stopped_reason: str

    def __post_init__(self) -> None:
        for name in ("pages_fetched", "unique_markets_seen", "duplicates_dropped"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError("coverage_count_invalid")
        for name in ("truncated_to_cap", "coverage_complete"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("coverage_flag_invalid")
        if self.stopped_reason not in DISCOVERY_STOP_REASONS:
            raise ValueError("coverage_stop_reason_invalid")


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    """Accepted market identities, per-candidate rejections, coverage.

    An empty ``markets`` tuple with ``coverage.stopped_reason ==
    "last_page"`` is a legitimate successful empty set.
    """

    markets: tuple[PolymarketMarket, ...]
    rejections: tuple[MarketRejection, ...]
    coverage: DiscoveryCoverage

    def __post_init__(self) -> None:
        if type(self.markets) is not tuple or any(
            type(item) is not PolymarketMarket for item in self.markets
        ):
            raise ValueError("discovery_markets_invalid")
        if type(self.rejections) is not tuple or any(
            type(item) is not MarketRejection for item in self.rejections
        ):
            raise ValueError("discovery_rejections_invalid")
        if type(self.coverage) is not DiscoveryCoverage:
            raise ValueError("discovery_coverage_invalid")
        if self.coverage.truncated_to_cap and len(self.markets) > FIRST_VERSION_MAX_MARKETS_PER_ROUND:
            raise ValueError("discovery_truncation_invalid")


@dataclass(frozen=True, slots=True)
class BookLevel:
    """One executable ladder level; price is in the open interval (0, 1)."""

    price: float
    size: float

    def __post_init__(self) -> None:
        for name in ("price", "size"):
            value = getattr(self, name)
            if type(value) is not float or not math.isfinite(value):
                raise ValueError("book_level_invalid")
        if not 0.0 < self.price < 1.0:
            raise ValueError("book_level_price_invalid")
        if self.size < 0.0:
            raise ValueError("book_level_size_invalid")


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """One token's executable CLOB book; deliberately no reference price.

    Bids are sorted descending and asks ascending by price. Empty sides
    are valid observations (``best_bid``/``best_ask`` ``None``).
    ``book_observed_at`` is the upstream book timestamp when present
    and parsable, ``None`` otherwise (unknown, never faked). This model
    structurally cannot carry Gamma ``outcomePrices``.
    """

    token_id: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    book_observed_at: datetime | None

    def __post_init__(self) -> None:
        if type(self.token_id) is not str or _TOKEN_ID.fullmatch(self.token_id) is None:
            raise ValueError("book_token_invalid")
        for name in ("bids", "asks"):
            levels = getattr(self, name)
            if type(levels) is not tuple or any(
                type(level) is not BookLevel for level in levels
            ):
                raise ValueError("book_levels_invalid")
        for one, two in zip(self.bids, self.bids[1:]):
            if one.price < two.price:
                raise ValueError("book_bids_not_sorted")
        for one, two in zip(self.asks, self.asks[1:]):
            if one.price > two.price:
                raise ValueError("book_asks_not_sorted")
        if self.book_observed_at is not None and (
            not isinstance(self.book_observed_at, datetime)
            or self.book_observed_at.tzinfo is None
        ):
            raise ValueError("book_time_invalid")

    @property
    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None


@dataclass(frozen=True, slots=True)
class TokenRejection:
    """One token whose book read failed; fixed reason code only."""

    token_id: str
    reason_code: str

    def __post_init__(self) -> None:
        if type(self.token_id) is not str or _TOKEN_ID.fullmatch(self.token_id) is None:
            raise ValueError("token_rejection_token_invalid")
        if self.reason_code not in PUBLIC_READ_REASONS + CANDIDATE_REJECTION_REASONS:
            raise ValueError("token_rejection_reason_invalid")


@dataclass(frozen=True, slots=True)
class BooksSnapshot:
    """Per-token book results plus per-token rejections, one batch."""

    books: tuple[OrderBookSnapshot, ...]
    rejections: tuple[TokenRejection, ...]

    def __post_init__(self) -> None:
        if type(self.books) is not tuple or any(
            type(item) is not OrderBookSnapshot for item in self.books
        ):
            raise ValueError("books_invalid")
        if type(self.rejections) is not tuple or any(
            type(item) is not TokenRejection for item in self.rejections
        ):
            raise ValueError("book_rejections_invalid")
        tokens = [book.token_id for book in self.books]
        if len(set(tokens)) != len(tokens):
            raise ValueError("book_tokens_not_unique")


@dataclass(frozen=True, slots=True)
class ResolutionObservation:
    """Gamma terminal state plus the CLOB cross-validation verdict.

    ``gamma_terminal_outcome`` is a corner only when Gamma is closed
    AND shows an exact 0/1 ``outcomePrices`` pair. ``terminal_consistency``
    keeps both sources' evidence: ``conflict`` records a disagreement
    without picking a favorable side, ``unverified`` means the CLOB leg
    could not be observed, and ``inconclusive`` means neither source
    shows a definitive terminal state.
    """

    condition_id: str
    market_id: str | None
    slug: str
    question: str
    end_at: datetime
    closed: bool | None
    active: bool | None
    archived: bool | None
    resolution_status: str | None
    terminal_reference: ReferencePrices | None
    gamma_terminal_outcome: str
    clob_books: tuple[OrderBookSnapshot, ...]
    terminal_consistency: str

    def __post_init__(self) -> None:
        _require_condition_id(self.condition_id)
        require_market_slug(self.slug)
        if type(self.question) is not str or not self.question:
            raise ValueError("resolution_question_invalid")
        if not isinstance(self.end_at, datetime) or self.end_at.tzinfo is None:
            raise ValueError("resolution_time_invalid")
        for name in ("closed", "active", "archived"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise ValueError("resolution_flag_invalid")
        if self.resolution_status is not None and type(self.resolution_status) is not str:
            raise ValueError("resolution_status_invalid")
        if self.gamma_terminal_outcome not in GAMMA_TERMINAL_OUTCOMES:
            raise ValueError("gamma_terminal_outcome_invalid")
        if type(self.clob_books) is not tuple or any(
            type(item) is not OrderBookSnapshot for item in self.clob_books
        ):
            raise ValueError("resolution_books_invalid")
        if self.terminal_consistency not in TERMINAL_CONSISTENCY_VERDICTS:
            raise ValueError("terminal_consistency_invalid")


# ---------------------------------------------------------------------------
# bounded transport
# ---------------------------------------------------------------------------


class PublicResponseOpener:
    """Transport seam: open one fixed-origin GET, return a response.

    Real runs use :class:`BoundedPublicOpener`; offline tests inject a
    fake that returns framed in-memory responses. The opener never
    decodes bodies - origin, status, content type, framing, the read
    limit and budget charging all stay in this adapter.
    """

    def open(self, url: str, *, timeout_seconds: float):  # pragma: no cover - seam
        raise NotImplementedError("public_opener_unimplemented")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True, slots=True)
class BoundedPublicOpener(PublicResponseOpener):
    """Default real transport: no redirects, no proxy, fixed headers.

    Construction performs no network work; the permission gate runs
    before this default is ever instantiated.
    """

    user_agent: str = "polymarket-alpha-lab/0.1"

    def open(self, url: str, *, timeout_seconds: float):
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": self.user_agent,
            },
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        return opener.open(request, timeout=timeout_seconds)


def _verify_public_response(response: object, url: str) -> None:
    """Origin, status and content-type gate before any body is read."""
    geturl = getattr(response, "geturl", None)
    status = getattr(response, "status", None)
    headers = getattr(response, "headers", None)
    if not callable(geturl) or geturl() != url:
        raise _PublicReadError("public_response_invalid")
    if status != 200:
        raise _PublicReadError("public_response_invalid")
    if headers is None or headers.get_content_type() != "application/json":
        raise _PublicReadError("public_response_invalid")


def _map_transport_error(error: Exception) -> _PublicReadError:
    """Fixed reason codes only; never URLs, headers or bodies."""
    if isinstance(error, HTTPError):
        error.close()
        return _PublicReadError("public_http_rejected")
    if isinstance(error, URLError):
        error = error.reason if isinstance(error.reason, Exception) else error
    if isinstance(error, ssl.SSLError):
        return _PublicReadError("public_tls_failed")
    if isinstance(error, IncompleteRead):
        return _PublicReadError("public_response_incomplete")
    if isinstance(error, TimeoutError):
        return _PublicReadError("public_timeout")
    if isinstance(error, (ConnectionResetError, ConnectionAbortedError)):
        return _PublicReadError("public_connection_interrupted")
    if isinstance(error, PublicBodyError):
        code = str(error)
        if code in _PUBLIC_BODY_REASONS:
            return _PublicReadError(code)
    return _PublicReadError("public_fetch_failed")


def _bounded_get(
    url: str,
    *,
    opener: PublicResponseOpener,
    budget: FetchBudget,
    now_utc: Callable[[], datetime],
    timeout_seconds: float,
    read_limit: int,
) -> tuple[bytes, datetime]:
    """One charged request already accounted; read, verify, charge bytes."""
    try:
        with opener.open(url, timeout_seconds=timeout_seconds) as response:
            _verify_public_response(response, url)
            raw = read_public_body(response, read_limit)
    except _PublicReadError:
        raise
    except Exception as error:  # noqa: BLE001 - fixed-code fail-closed mapping
        raise _map_transport_error(error) from None
    budget.charge_response_bytes(len(raw))
    return raw, now_utc()


def _fetch_bytes(
    url: str,
    *,
    opener: PublicResponseOpener,
    budget: FetchBudget,
    now_utc: Callable[[], datetime],
) -> tuple[bytes, datetime]:
    """Charge one actual request, then perform the bounded GET."""
    budget.charge_request()
    return _bounded_get(
        url,
        opener=opener,
        budget=budget,
        now_utc=now_utc,
        timeout_seconds=budget.effective_timeout_seconds(),
        read_limit=budget.next_response_read_limit(),
    )


def _reader_timeout_seconds(timeout_seconds: float) -> int:
    return min(60, max(1, int(math.floor(timeout_seconds))))


def _now_utc() -> datetime:
    """Default wall clock for evidence timestamps (injectable in tests)."""
    return datetime.now(UTC)


def _read_gamma_slug(
    slug: str,
    *,
    opener: PublicResponseOpener,
    budget: FetchBudget,
    now_utc: Callable[[], datetime],
) -> tuple[bytes, datetime]:
    """Single-market Gamma context read (M2 plan section 2.2).

    Prefers the existing bounded ``GammaResearchReader`` whenever the
    shared budget's exact read limit admits its fixed 1 MiB cap; the
    reader path uses the reader's own observation clock. A tighter
    budget takes this adapter's bounded GET with the exact limit.
    """
    budget.charge_request()
    timeout_seconds = budget.effective_timeout_seconds()
    read_limit = budget.next_response_read_limit()
    if read_limit >= MAX_GAMMA_RESPONSE_BYTES:
        reader = GammaResearchReader(
            allow_public_fetch=True,
            timeout_seconds=_reader_timeout_seconds(timeout_seconds),
        )
        try:
            snapshot = reader.fetch(market_slug=slug)
        except Exception as error:  # noqa: BLE001 - reader raises fixed ValueError
            raise _PublicReadError("gamma_slug_read_failed") from None
        raw = snapshot.raw_json
        budget.charge_response_bytes(len(raw))
        return raw, snapshot.fetched_at
    return _bounded_get(
        GAMMA_MARKET_PREFIX + slug,
        opener=opener,
        budget=budget,
        now_utc=now_utc,
        timeout_seconds=timeout_seconds,
        read_limit=read_limit,
    )


def _decode_json(raw: bytes) -> object:
    """Strict UTF-8 JSON decode (duplicate keys rejected)."""
    try:
        return strict_json(raw.decode("utf-8"))
    except Exception as error:
        raise _PublicReadError("public_payload_invalid") from None


# ---------------------------------------------------------------------------
# payload parsing
# ---------------------------------------------------------------------------


def _maybe_json_list(value: object) -> list | None:
    """Decode Gamma's string-encoded list fields; ``None`` when absent."""
    if value is None:
        return None
    if type(value) is str:
        if not value.strip():
            return None
        decoded = strict_json(value)
        if type(decoded) is not list:
            raise ValueError("not_a_list")
        return decoded
    if type(value) is list:
        return value
    raise ValueError("not_a_list")


def _parse_float_or_none(value: object) -> float | None:
    """Numeric-string/number to float; blank/None stays ``None``."""
    if value is None:
        return None
    if type(value) is str:
        if not value.strip():
            return None
        parsed = float(value)
    elif type(value) is int:
        parsed = float(value)
    elif type(value) is float:
        parsed = value
    else:
        raise ValueError("not_a_number")
    if not math.isfinite(parsed):
        raise ValueError("not_finite")
    return parsed


def _parse_iso_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("not_a_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("naive_timestamp")
    return parsed.astimezone(UTC)


def _parse_epoch_millis(value: object) -> datetime | None:
    if value is None:
        return None
    if type(value) is str:
        if not re.fullmatch(r"[0-9]{1,14}", value):
            raise ValueError("bad_epoch")
        milliseconds = int(value)
    elif type(value) is int:
        milliseconds = value
    else:
        raise ValueError("bad_epoch")
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=UTC)


def _exact_bool_or_none(value: object) -> bool | None:
    if value is None:
        return None
    if type(value) is bool:
        return value
    raise ValueError("not_a_bool")


def _token_pair(market: Mapping) -> tuple[str, str, str, str, int, int]:
    """Explicit YES/NO labels and tokens, resolved by outcome label.

    Returns ``(yes_token_id, no_token_id, yes_label, no_label,
    yes_index, no_index)``. Label matching is case-insensitive and
    positional order is irrelevant, so reversed ``["No", "Yes"]``
    payloads map correctly.
    """
    labels = _maybe_json_list(market.get("outcomes"))
    if labels is None or len(labels) != 2 or any(
        type(label) is not str or not label for label in labels
    ):
        raise _MarketRejected("outcomes_unsupported")
    yes_index = no_index = None
    for index, label in enumerate(labels):
        normalized = label.strip().lower()
        if normalized == "yes" and yes_index is None:
            yes_index = index
        elif normalized == "no" and no_index is None:
            no_index = index
    if yes_index is None or no_index is None:
        raise _MarketRejected("outcomes_unsupported")
    tokens = _maybe_json_list(market.get("clobTokenIds"))
    if tokens is None or len(tokens) == 0:
        raise _MarketRejected("token_mapping_missing")
    if len(tokens) != 2:
        raise _MarketRejected("token_mapping_conflict")
    for token in tokens:
        if type(token) is not str or _TOKEN_ID.fullmatch(token) is None:
            raise _MarketRejected("token_mapping_conflict")
    if tokens[0] == tokens[1]:
        raise _MarketRejected("token_mapping_conflict")
    return (
        tokens[yes_index],
        tokens[no_index],
        labels[yes_index],
        labels[no_index],
        yes_index,
        no_index,
    )


def _parse_fee_rate_bps(value: object) -> float | None:
    """Gamma fee metadata in basis points; unusable units are rejected."""
    if value is None:
        return None
    try:
        parsed = _parse_float_or_none(value)
    except ValueError as error:
        raise _MarketRejected("fee_unit_uninterpretable") from None
    if parsed is None:
        return None
    if not 0.0 <= parsed <= 10000.0:
        raise _MarketRejected("fee_unit_uninterpretable")
    return parsed


def _reference_prices(market: Mapping, yes_index: int, no_index: int) -> ReferencePrices | None:
    prices = _maybe_json_list(market.get("outcomePrices"))
    if prices is None:
        return None
    if len(prices) != 2:
        raise _MarketRejected("market_payload_invalid")
    try:
        yes_last = _parse_float_or_none(prices[yes_index])
        no_last = _parse_float_or_none(prices[no_index])
        reference = ReferencePrices(yes_last=yes_last, no_last=no_last)
    except ValueError as error:
        raise _MarketRejected("market_payload_invalid") from None
    return reference


def _best_effort_identity(market: object) -> tuple[str | None, str | None]:
    if type(market) is not dict:
        return None, None
    condition_id = market.get("conditionId")
    slug = market.get("slug")
    return (
        condition_id if type(condition_id) is str else None,
        slug if type(slug) is str else None,
    )


def _parse_market(
    market: object,
    *,
    expected_condition_id: str | None = None,
    expected_slug: str | None = None,
) -> PolymarketMarket:
    """Normalize one Gamma market payload or reject it with a fixed code."""
    if type(market) is not dict:
        raise _MarketRejected("market_payload_invalid")
    try:
        condition_id = market.get("conditionId")
        if type(condition_id) is not str:
            raise ValueError("condition")
        _require_condition_id(condition_id)
        slug = market.get("slug")
        if type(slug) is not str:
            raise ValueError("slug")
        require_market_slug(slug)
        question = market.get("question")
        if type(question) is not str or not question or len(question) > 2000:
            raise ValueError("question")
        market_id = market.get("id")
        if market_id is not None and type(market_id) is not str:
            raise ValueError("market_id")
        (
            yes_token,
            no_token,
            yes_label,
            no_label,
            yes_index,
            no_index,
        ) = _token_pair(market)
        try:
            end_at = _parse_iso_datetime(market.get("endDate"))
        except ValueError as error:
            raise _MarketRejected("market_time_invalid") from None
        if end_at is None:
            raise _MarketRejected("market_time_invalid")
        start_at = _parse_iso_datetime(market.get("startDate"))
        game_start_at = _parse_iso_datetime(market.get("gameStartTime"))
        closed = _exact_bool_or_none(market.get("closed"))
        active = _exact_bool_or_none(market.get("active"))
        archived = _exact_bool_or_none(market.get("archived"))
        category = market.get("category")
        if category is not None and type(category) is not str:
            raise ValueError("category")
        description = market.get("description")
        if description is not None and type(description) is not str:
            raise ValueError("description")
        resolution_status = market.get("umaResolutionStatus")
        if resolution_status is not None and type(resolution_status) is not str:
            raise ValueError("resolution_status")
        fee_rate_bps = _parse_fee_rate_bps(market.get("feeRateBps"))
        reference = _reference_prices(market, yes_index, no_index)
        market = PolymarketMarket(
            condition_id=condition_id,
            market_id=market_id,
            slug=slug,
            question=question,
            outcome_labels=(yes_label, no_label),
            yes_token_id=yes_token,
            no_token_id=no_token,
            start_at=start_at,
            game_start_at=game_start_at,
            end_at=end_at,
            closed=closed,
            active=active,
            archived=archived,
            category=category,
            description=description,
            resolution_status=resolution_status,
            fee_rate_bps=fee_rate_bps,
            reference_last=reference,
        )
    except _MarketRejected:
        raise
    except ValueError:
        raise _MarketRejected("market_payload_invalid") from None
    if expected_condition_id is not None and market.condition_id != expected_condition_id:
        raise _MarketRejected("market_identity_mismatch")
    if expected_slug is not None and market.slug != expected_slug:
        raise _MarketRejected("market_identity_mismatch")
    return market


def _parse_book_side(levels: object) -> tuple[BookLevel, ...]:
    if type(levels) is not list:
        raise ValueError("not_a_list")
    parsed: list[BookLevel] = []
    for level in levels:
        if type(level) is not dict:
            raise ValueError("not_an_object")
        price = _parse_float_or_none(level.get("price"))
        size = _parse_float_or_none(level.get("size"))
        if price is None or size is None:
            raise ValueError("level_value_missing")
        parsed.append(BookLevel(price=price, size=size))
    return tuple(parsed)


def _parse_order_book(token_id: str, payload: object) -> OrderBookSnapshot:
    if type(payload) is not dict:
        raise _MarketRejected("book_payload_invalid")
    try:
        bids = _parse_book_side(payload.get("bids"))
        asks = _parse_book_side(payload.get("asks"))
        observed_at = _parse_epoch_millis(payload.get("timestamp"))
        book = OrderBookSnapshot(
            token_id=token_id,
            bids=tuple(sorted(bids, key=lambda level: level.price, reverse=True)),
            asks=tuple(sorted(asks, key=lambda level: level.price)),
            book_observed_at=observed_at,
        )
    except _MarketRejected:
        raise
    except ValueError:
        raise _MarketRejected("book_payload_invalid") from None
    return book


# ---------------------------------------------------------------------------
# shared read helpers
# ---------------------------------------------------------------------------


def _gamma_evidence(url: str, raw: bytes, fetched_at: datetime) -> FetchEvidence:
    return FetchEvidence.from_bytes(
        source_kind=SOURCE_KIND_GAMMA,
        source_reference=url,
        fetched_at=fetched_at,
        raw_bytes=raw,
    )


def _clob_evidence(
    url: str, raw: bytes, fetched_at: datetime, observed_at: datetime | None
) -> FetchEvidence:
    return FetchEvidence.from_bytes(
        source_kind=SOURCE_KIND_CLOB,
        source_reference=url,
        fetched_at=fetched_at,
        raw_bytes=raw,
        source_observed_at=observed_at,
    )


def _read_single_market(
    market_ref: MarketRef,
    *,
    opener: PublicResponseOpener,
    budget: FetchBudget,
    now_utc: Callable[[], datetime],
    evidence: list[FetchEvidence],
) -> PolymarketMarket:
    """One Gamma market read (slug preferred), evidence before parsing."""
    if market_ref.slug is not None:
        url = GAMMA_MARKET_PREFIX + market_ref.slug
        raw, fetched_at = _read_gamma_slug(
            market_ref.slug, opener=opener, budget=budget, now_utc=now_utc
        )
        evidence.append(_gamma_evidence(url, raw, fetched_at))
        payload = _decode_json(raw)
        if type(payload) is not dict:
            raise _PublicReadError("public_payload_invalid")
    else:
        url = GAMMA_MARKETS_BY_CONDITION_URL + "?" + urlencode(
            {"condition_id": market_ref.condition_id}
        )
        raw, fetched_at = _fetch_bytes(
            url, opener=opener, budget=budget, now_utc=now_utc
        )
        evidence.append(_gamma_evidence(url, raw, fetched_at))
        payload = _decode_json(raw)
        if type(payload) is not list:
            raise _PublicReadError("public_payload_invalid")
        if len(payload) == 0:
            raise _MarketRejected("market_not_found")
        if len(payload) > 1:
            raise _MarketRejected("market_identity_ambiguous")
        payload = payload[0]
        if type(payload) is not dict:
            raise _PublicReadError("public_payload_invalid")
    return _parse_market(
        payload,
        expected_condition_id=market_ref.condition_id,
        expected_slug=market_ref.slug,
    )


def _fetch_book(
    token_id: str,
    *,
    opener: PublicResponseOpener,
    budget: FetchBudget,
    now_utc: Callable[[], datetime],
    evidence: list[FetchEvidence],
) -> OrderBookSnapshot:
    """One token's CLOB book; evidence is kept even for a rejected book."""
    url = CLOB_BOOK_URL + "?" + urlencode({"token_id": token_id})
    raw, fetched_at = _fetch_bytes(url, opener=opener, budget=budget, now_utc=now_utc)
    try:
        book = _parse_order_book(token_id, _decode_json(raw))
    except _MarketRejected:
        evidence.append(_clob_evidence(url, raw, fetched_at, None))
        raise
    evidence.append(_clob_evidence(url, raw, fetched_at, book.book_observed_at))
    return book


def _normalize_token_refs(token_refs: object) -> tuple[str, ...]:
    if isinstance(token_refs, (str, bytes)):
        raise ValueError("token_refs_not_a_sequence")
    if not isinstance(token_refs, Sequence):
        raise ValueError("token_refs_not_a_sequence")
    refs: list[str] = []
    for token_ref in token_refs:
        if type(token_ref) is not str or _TOKEN_ID.fullmatch(token_ref) is None:
            raise ValueError("token_ref_invalid")
        if token_ref not in refs:
            refs.append(token_ref)
    if not refs:
        raise ValueError("token_refs_empty")
    return tuple(refs)


# ---------------------------------------------------------------------------
# discovery internals
# ---------------------------------------------------------------------------


def _discover_markets(
    query: DiscoveryQuery,
    *,
    opener: PublicResponseOpener,
    budget: FetchBudget,
    now_utc: Callable[[], datetime],
    evidence: list[FetchEvidence],
) -> DiscoverySnapshot:
    accepted: dict[str, PolymarketMarket] = {}
    rejections: dict[str, MarketRejection] = {}
    fingerprints: dict[str, tuple] = {}
    state: dict[str, str] = {}
    duplicates = 0
    pages = 0
    stopped_reason = "page_cap"
    for page_index in range(DISCOVERY_MAX_PAGES):
        if len(fingerprints) >= DISCOVERY_ACCUMULATION_CAP:
            stopped_reason = "accumulation_cap"
            break
        url = GAMMA_EVENTS_URL + "?" + urlencode(
            {
                "tag_slug": query.tag_slug,
                "closed": "true" if query.closed else "false",
                "limit": GAMMA_EVENTS_PAGE_LIMIT,
                "offset": page_index * GAMMA_EVENTS_PAGE_LIMIT,
            }
        )
        raw, fetched_at = _fetch_bytes(
            url, opener=opener, budget=budget, now_utc=now_utc
        )
        evidence.append(_gamma_evidence(url, raw, fetched_at))
        pages += 1
        page = _decode_json(raw)
        if type(page) is not list:
            raise _PublicReadError("public_payload_invalid")
        page_sha = sha256_hexdigest(raw)
        for event in page:
            if type(event) is not dict or type(event.get("markets")) is not list:
                raise _PublicReadError("public_payload_invalid")
            for market_payload in event["markets"]:
                if (
                    _absorb_discovery_market(
                        market_payload,
                        page_sha=page_sha,
                        accepted=accepted,
                        rejections=rejections,
                        fingerprints=fingerprints,
                        state=state,
                    )
                    == "duplicate"
                ):
                    duplicates += 1
        if len(page) == 0:
            stopped_reason = "last_page"
            break
    ordered = sorted(
        accepted.values(), key=lambda market: (market.end_at, market.slug, market.condition_id)
    )
    truncated = len(ordered) > query.max_markets
    final = tuple(ordered[: query.max_markets])
    coverage_complete = stopped_reason == "last_page" and not truncated
    return DiscoverySnapshot(
        markets=final,
        rejections=tuple(rejections.values()),
        coverage=DiscoveryCoverage(
            pages_fetched=pages,
            unique_markets_seen=len(state),
            duplicates_dropped=duplicates,
            truncated_to_cap=truncated,
            coverage_complete=coverage_complete,
            stopped_reason=stopped_reason,
        ),
    )


def _absorb_discovery_market(
    market_payload: object,
    *,
    page_sha: str,
    accepted: dict[str, PolymarketMarket],
    rejections: dict[str, MarketRejection],
    fingerprints: dict[str, tuple],
    state: dict[str, str],
) -> str:
    """Fold one discovered market; returns the fold action taken.

    Actions: ``accepted`` (first occurrence, usable), ``rejected``
    (first occurrence unusable), ``duplicate`` (identical repeat,
    dropped) or ``drifted`` (same identity with a different
    fingerprint - the candidate is rejected outright as
    ``identity_drift`` and can never re-enter the accepted set).
    """
    try:
        market = _parse_market(market_payload)
    except _MarketRejected as rejection:
        condition_id, slug = _best_effort_identity(market_payload)
        key = condition_id or (f"slug:{slug}" if slug else None)
        if key is None:
            rejections[f"__anonymous_{len(rejections)}"] = MarketRejection(
                condition_id=None, slug=None,
                reason_code=rejection.reason_code, evidence_sha256=page_sha,
            )
            return "rejected"
        if key in state:
            return "duplicate"
        state[key] = "rejected"
        rejections[key] = MarketRejection(
            condition_id=condition_id,
            slug=slug,
            reason_code=rejection.reason_code,
            evidence_sha256=page_sha,
        )
        return "rejected"
    key = market.condition_id
    fingerprint = dataclasses.astuple(market)
    if key in state and key not in fingerprints:
        # First occurrence of this identity was rejected; a later
        # parseable occurrence is a content conflict we cannot
        # fingerprint-compare, so the rejection stands (no favorable
        # side is picked and the identity never lands in both sets).
        return "duplicate"
    if key in fingerprints:
        if fingerprints[key] == fingerprint:
            return "duplicate"
        accepted.pop(key, None)
        rejections.pop(key, None)
        state[key] = "drifted"
        fingerprints[key] = fingerprint
        rejections[key] = MarketRejection(
            condition_id=key,
            slug=market.slug,
            reason_code="identity_drift",
            evidence_sha256=page_sha,
        )
        return "drifted"
    fingerprints[key] = fingerprint
    state[key] = "accepted"
    accepted[key] = market
    return "accepted"


# ---------------------------------------------------------------------------
# resolution internals
# ---------------------------------------------------------------------------


def _gamma_terminal_outcome(market: PolymarketMarket) -> str:
    """Terminal corner only when closed AND an exact 0/1 price pair."""
    reference = market.reference_last
    if market.closed is not True or reference is None:
        return "not_terminal"
    if reference.yes_last == 1.0 and reference.no_last == 0.0:
        return "terminal_yes"
    if reference.yes_last == 0.0 and reference.no_last == 1.0:
        return "terminal_no"
    return "not_terminal"


def _terminal_consistency(
    gamma_outcome: str, market: PolymarketMarket, books: tuple[OrderBookSnapshot, ...]
) -> str:
    if gamma_outcome == "not_terminal":
        return "inconclusive"
    by_token = {book.token_id: book for book in books}
    winning_token = market.yes_token_id if gamma_outcome == "terminal_yes" else market.no_token_id
    losing_token = market.no_token_id if gamma_outcome == "terminal_yes" else market.yes_token_id
    winning = by_token.get(winning_token)
    losing = by_token.get(losing_token)
    if winning is None or losing is None:
        return "unverified"
    winning_bid = winning.best_bid
    losing_bid = losing.best_bid
    if winning_bid is None and losing_bid is None:
        return "inconclusive"
    if (
        winning_bid is not None and winning_bid.price < 1.0 - TERMINAL_PRICE_EPSILON
    ) or (losing_bid is not None and losing_bid.price > TERMINAL_PRICE_EPSILON):
        return "conflict"
    return "consistent"


def _resolution_observation(
    market: PolymarketMarket, books: tuple[OrderBookSnapshot, ...]
) -> ResolutionObservation:
    gamma_outcome = _gamma_terminal_outcome(market)
    return ResolutionObservation(
        condition_id=market.condition_id,
        market_id=market.market_id,
        slug=market.slug,
        question=market.question,
        end_at=market.end_at,
        closed=market.closed,
        active=market.active,
        archived=market.archived,
        resolution_status=market.resolution_status,
        terminal_reference=market.reference_last,
        gamma_terminal_outcome=gamma_outcome,
        clob_books=books,
        terminal_consistency=_terminal_consistency(gamma_outcome, market, books),
    )


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def _deny_network() -> AdapterResult:
    return AdapterResult.failure(
        AdapterStatus.NETWORK_DENIED, REASON_PUBLIC_FETCH_NOT_ALLOWED
    )


def _budget_failure(error: BudgetExhaustedError, evidence: tuple[FetchEvidence, ...]) -> AdapterResult:
    if error.reason_code == REASON_DEADLINE_EXCEEDED:
        return AdapterResult.failure(
            AdapterStatus.DEADLINE_EXCEEDED, REASON_DEADLINE_EXCEEDED, evidence
        )
    return AdapterResult.failure(AdapterStatus.BUDGET_EXHAUSTED, error.reason_code, evidence)


def _require_budget(budget: object) -> FetchBudget:
    if type(budget) is not FetchBudget:
        raise ValueError("budget_invalid")
    return budget


def discover_markets(
    query: DiscoveryQuery,
    *,
    budget: FetchBudget,
    allow_public_fetch: bool,
    opener: PublicResponseOpener | None = None,
    now_utc: Callable[[], datetime] | None = None,
) -> AdapterResult:
    """Discover candidate markets for one queue tag via Gamma ``/events``.

    Pagination requests ``limit=50`` pages (larger limits are rejected
    upstream), stops on the first empty page, and is additionally
    bounded by the shared budget, a 40-page cap and a 400-unique-market
    accumulation cap. Results are ordered by the frozen key (end time,
    slug, condition id) and truncated at ``query.max_markets`` with
    ``coverage_complete=False`` when more existed. A genuinely empty
    result set is a SUCCESS.
    """
    if type(query) is not DiscoveryQuery:
        raise ValueError("query_invalid")
    _require_budget(budget)
    try:
        require_public_fetch(allow_public_fetch)
    except FetchPermissionError:
        return _deny_network()
    if opener is None:
        opener = BoundedPublicOpener()
    if now_utc is None:
        now_utc = _now_utc
    evidence: list[FetchEvidence] = []
    try:
        value = _discover_markets(
            query, opener=opener, budget=budget, now_utc=now_utc, evidence=evidence
        )
    except BudgetExhaustedError as error:
        return _budget_failure(error, tuple(evidence))
    except _PublicReadError as error:
        return AdapterResult.failure(AdapterStatus.UPSTREAM_ERROR, error.reason_code, tuple(evidence))
    return AdapterResult.success(value, tuple(evidence))


def fetch_market(
    market_ref: MarketRef,
    *,
    budget: FetchBudget,
    allow_public_fetch: bool,
    opener: PublicResponseOpener | None = None,
    now_utc: Callable[[], datetime] | None = None,
) -> AdapterResult:
    """Fetch one market's normalized Gamma context (rules, times, tokens).

    Single-market context reads prefer the existing bounded
    ``GammaResearchReader`` (slug path, budget permitting). Candidate
    defects (identity drift, token mapping, direction, fee unit) reject
    the market with a fixed reason code while the raw response stays in
    the result's evidence.
    """
    if type(market_ref) is not MarketRef:
        raise ValueError("market_ref_invalid")
    _require_budget(budget)
    try:
        require_public_fetch(allow_public_fetch)
    except FetchPermissionError:
        return _deny_network()
    if opener is None:
        opener = BoundedPublicOpener()
    if now_utc is None:
        now_utc = _now_utc
    evidence: list[FetchEvidence] = []
    try:
        market = _read_single_market(
            market_ref, opener=opener, budget=budget, now_utc=now_utc, evidence=evidence
        )
    except BudgetExhaustedError as error:
        return _budget_failure(error, tuple(evidence))
    except _PublicReadError as error:
        return AdapterResult.failure(AdapterStatus.UPSTREAM_ERROR, error.reason_code, tuple(evidence))
    except _MarketRejected as rejection:
        status = _REJECTION_STATUS.get(rejection.reason_code, AdapterStatus.UPSTREAM_ERROR)
        return AdapterResult.failure(status, rejection.reason_code, tuple(evidence))
    return AdapterResult.success(market, tuple(evidence))


def fetch_order_books(
    token_refs: Sequence[str],
    *,
    budget: FetchBudget,
    allow_public_fetch: bool,
    opener: PublicResponseOpener | None = None,
    now_utc: Callable[[], datetime] | None = None,
) -> AdapterResult:
    """Fetch executable CLOB order books for validated token ids.

    Each token is one charged request producing one
    :class:`OrderBookSnapshot` (empty sides are valid observations with
    ``None`` best quotes) or one :class:`TokenRejection` with a fixed
    reason; every response keeps its raw evidence. Budget or deadline
    exhaustion aborts the whole batch with no partial value.
    """
    refs = _normalize_token_refs(token_refs)
    _require_budget(budget)
    try:
        require_public_fetch(allow_public_fetch)
    except FetchPermissionError:
        return _deny_network()
    if opener is None:
        opener = BoundedPublicOpener()
    if now_utc is None:
        now_utc = _now_utc
    evidence: list[FetchEvidence] = []
    books: list[OrderBookSnapshot] = []
    rejections: list[TokenRejection] = []
    try:
        for token_id in refs:
            try:
                books.append(
                    _fetch_book(
                        token_id, opener=opener, budget=budget, now_utc=now_utc, evidence=evidence
                    )
                )
            except _PublicReadError as error:
                rejections.append(TokenRejection(token_id, error.reason_code))
            except _MarketRejected as rejection:
                rejections.append(TokenRejection(token_id, rejection.reason_code))
    except BudgetExhaustedError as error:
        return _budget_failure(error, tuple(evidence))
    return AdapterResult.success(
        BooksSnapshot(books=tuple(books), rejections=tuple(rejections)), tuple(evidence)
    )


def fetch_resolution(
    market_ref: MarketRef,
    *,
    budget: FetchBudget,
    allow_public_fetch: bool,
    opener: PublicResponseOpener | None = None,
    now_utc: Callable[[], datetime] | None = None,
) -> AdapterResult:
    """Observe one market's Gamma terminal state, cross-checked on CLOB.

    Gamma supplies the terminal reference (closed plus an exact 0/1
    ``outcomePrices`` pair); CLOB books for both tokens cross-validate
    it. A Gamma/CLOB disagreement is recorded as ``terminal_consistency
    == "conflict"`` with both observations kept - the adapter never
    picks the favorable side, and ``inconclusive``/``unverified``
    stay distinct from a confirmed terminal state.
    """
    if type(market_ref) is not MarketRef:
        raise ValueError("market_ref_invalid")
    _require_budget(budget)
    try:
        require_public_fetch(allow_public_fetch)
    except FetchPermissionError:
        return _deny_network()
    if opener is None:
        opener = BoundedPublicOpener()
    if now_utc is None:
        now_utc = _now_utc
    evidence: list[FetchEvidence] = []
    try:
        market = _read_single_market(
            market_ref, opener=opener, budget=budget, now_utc=now_utc, evidence=evidence
        )
        books: list[OrderBookSnapshot] = []
        for token_id in (market.yes_token_id, market.no_token_id):
            try:
                books.append(
                    _fetch_book(
                        token_id, opener=opener, budget=budget, now_utc=now_utc, evidence=evidence
                    )
                )
            except (_PublicReadError, _MarketRejected):
                continue
    except BudgetExhaustedError as error:
        return _budget_failure(error, tuple(evidence))
    except _PublicReadError as error:
        return AdapterResult.failure(AdapterStatus.UPSTREAM_ERROR, error.reason_code, tuple(evidence))
    except _MarketRejected as rejection:
        status = _REJECTION_STATUS.get(rejection.reason_code, AdapterStatus.UPSTREAM_ERROR)
        return AdapterResult.failure(status, rejection.reason_code, tuple(evidence))
    return AdapterResult.success(
        _resolution_observation(market, tuple(books)), tuple(evidence)
    )


__all__ = (
    "BoundedPublicOpener",
    "BooksSnapshot",
    "BookLevel",
    "CANDIDATE_REJECTION_REASONS",
    "DISCOVERY_ACCUMULATION_CAP",
    "DISCOVERY_MAX_PAGES",
    "DISCOVERY_STOP_REASONS",
    "DiscoveryCoverage",
    "DiscoveryQuery",
    "DiscoverySnapshot",
    "GAMMA_EVENTS_PAGE_LIMIT",
    "GAMMA_EVENTS_URL",
    "GAMMA_MARKETS_BY_CONDITION_URL",
    "MarketRejection",
    "MarketRef",
    "OrderBookSnapshot",
    "PAPER_ONLY",
    "PUBLIC_READ_REASONS",
    "PolymarketMarket",
    "READONLY",
    "ReferencePrices",
    "REPORT_ONLY",
    "ResolutionObservation",
    "TERMINAL_CONSISTENCY_VERDICTS",
    "TERMINAL_PRICE_EPSILON",
    "TokenRejection",
    "discover_markets",
    "fetch_market",
    "fetch_order_books",
    "fetch_resolution",
)
