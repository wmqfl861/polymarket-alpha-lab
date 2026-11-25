"""M2 A2 price adapter: bounded public BTC/ETH candles and spot reads.

Single responsibility (M2 plan section 2.3, FX-15 A2): turn an
explicitly bound BTC/ETH venue request into ascending, deduplicated,
complete six-hour closed candles plus an independent spot observation,
under the A0 budget/evidence contract, with zero persistence and zero
credential handling. This module opens no database sessions, writes no
cache files and no JSONL journals, and carries the Phase 1 boundary
flags ``PAPER_ONLY`` / ``REPORT_ONLY`` / ``READONLY``.

Source bindings are explicit and isolated (FX-03 mirror compatibility):

- ``binance`` keeps the mirror ``scan_crypto_33.py`` reading path
  (BTCUSDT/ETHUSDT, quote currency USDT) as a minimal public
  compatibility path inside this same file; it is never silently
  replaced by another venue and is never mixed with USD-quoted series.
- ``coinbase`` and ``kraken`` serve USD-quoted products (BTC-USD /
  ETH-USD, XBTUSD / ETHUSD) as explicitly configured sources or
  verification sources. A request to one venue can never yield another
  venue's series, and the venue/pair/currency binding is part of every
  returned observation. There is no caller-supplied pair, so a
  BTC-USD series and a BTCUSDT series cannot be cross-requested; only
  the closed asset vocabulary ``BTC`` / ``ETH`` is accepted.

Client limitations honored, never faked:

- Coinbase serves at most 48 candles per request (the frozen
  ``CryptoCandleWindow`` cap); long windows are split into a finite,
  explicit sequence of at-most-48-bar sub-windows and complete coverage
  is verified after the merge.
- Kraken's shared intervals contain no six-hour bar; six-hour bars are
  aggregated from complete hourly rows on UTC-aligned bucket boundaries
  (00/06/12/18), and the response's final row is always dropped as
  uncommitted, mirroring the frozen Kraken decoder's semantics.
- Kraken's recent OHLC history is bounded (at most 720 rows per
  response, 719 usable); this adapter issues exactly one bounded
  request per window and reports ``insufficient_data`` when coverage
  falls short. It never re-pages to fabricate arbitrary-length history.
- Only complete (closed) bars are ever returned in ``bars``; unfinished
  bars are excluded and counted in ``excluded_unfinished_count``.
  ``closes_for_vol_window`` refuses to hand the frozen
  ``crypto_model.realized_vol_daily`` / ``realized_vol_windows``
  estimators fewer closes than a frozen ``VOL_WINDOW_BARS`` label
  requires, so the frozen estimator's r19-faithful "slice whatever
  exists" compatibility behavior cannot be exploited through this
  adapter to pass a short history off as a complete 30/60/87-day
  window. The frozen math itself (uncentered RMS of log returns,
  ``SIGNATURE_WINDOW`` / ``VOL_WINDOW_BARS``) is neither reimplemented
  nor modified here.

Settlement independence: a market-data source can never substitute for
the settlement source a contract designates. Model inputs (these candle
and spot observations) and settlement evidence are two independent
bindings; consumers must keep them separate.

Transport: every network entry point takes ``allow_public_fetch`` and
calls ``contracts.require_public_fetch`` before building any request.
Each actual request charges the shared ``FetchBudget`` first, reads
through ``public_http.read_public_body`` with
``FetchBudget.next_response_read_limit()``, times out with
``FetchBudget.effective_timeout_seconds()``, and yields exactly one
``FetchEvidence`` per HTTP response. Failures are fixed short reason
codes only (``REASON_CODES``); URLs, query parameters, headers and
response bodies are never embedded in errors.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from http.client import IncompleteRead
import re
from types import MappingProxyType
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from polymarket_alpha_lab.papersim.adapters.contracts import (
    REASON_DEADLINE_EXCEEDED,
    REASON_PUBLIC_FETCH_NOT_ALLOWED,
    REASON_REQUESTS_EXHAUSTED,
    REASON_RESPONSE_BYTES_EXHAUSTED,
    PAPER_ONLY,
    READONLY,
    REPORT_ONLY,
    AdapterResult,
    AdapterStatus,
    BudgetExhaustedError,
    FetchBudget,
    FetchEvidence,
    FetchPermissionError,
    require_public_fetch,
)
from polymarket_alpha_lab.papersim.crypto_model import VOL_WINDOW_BARS
from polymarket_alpha_lab.public_http import PublicBodyError, read_public_body
from polymarket_alpha_lab.team_research_agent_types import strict_json
from polymarket_alpha_lab.team_research_crypto_candles import (
    MAX_CANDLES as COINBASE_MAX_CANDLES_PER_WINDOW,
)
from polymarket_alpha_lab.team_research_crypto_candles import CryptoCandleWindow
from polymarket_alpha_lab.team_research_kraken_candles import PAIRS as KRAKEN_PAIRS

# Phase 1 boundary flags (project iron rule 3): bounded public reads for
# paper evidence only; no execution, no credentials, no persistence.
PAPER_ONLY = True
REPORT_ONLY = True
READONLY = True

# Closed input vocabulary. Only exact members are accepted; anything
# else (including lowercase spellings and product ids like "BTC-USD" or
# "BTCUSDT") is an unsupported value, not a parse error.
SUPPORTED_ASSETS = ("BTC", "ETH")
SUPPORTED_SOURCES = ("binance", "coinbase", "kraken")
SUPPORTED_BAR_HOURS = 6
BAR_SECONDS = SUPPORTED_BAR_HOURS * 3600

# Coinbase client limit (frozen CryptoCandleWindow cap): <= 48 bars per
# request, so long windows are sliced into explicit sub-windows.
COINBASE_GRANULARITY_SECONDS = BAR_SECONDS

# Kraken client limits: shared intervals hold no 6h bar, so bars are
# aggregated from 1h rows; the API returns at most 720 rows and its
# final row is always uncommitted (dropped), leaving at most 719 usable.
KRAKEN_INTERVAL_MINUTES = 60
KRAKEN_HOUR_SECONDS = 3600
KRAKEN_MAX_ROWS = 720
KRAKEN_USABLE_ROWS = KRAKEN_MAX_ROWS - 1

_BINANCE_BASE_URL = "https://api.binance.com/api/v3"
_BINANCE_INTERVAL = "6h"
_BINANCE_MAX_BARS_PER_REQUEST = 1000
_COINBASE_BASE_URL = "https://api.exchange.coinbase.com"
_KRAKEN_BASE_URL = "https://api.kraken.com/0/public"

_MAX_DECIMAL = Decimal("1e18")
_DECIMAL_STRING = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_EPOCH_MAX = 253402300799

# Fixed reason codes (closed vocabulary; never wrap URLs or bodies).
REASON_UNSUPPORTED_ASSET = "unsupported_asset"
REASON_UNSUPPORTED_SOURCE = "unsupported_source"
REASON_UNSUPPORTED_BAR_INTERVAL = "unsupported_bar_interval"
REASON_WINDOW_BOUNDS_INVALID = "window_bounds_invalid"
REASON_WINDOW_NOT_CLOSED = "window_not_closed"
REASON_WINDOW_INCOMPLETE = "window_incomplete"
REASON_RECENT_HISTORY_LIMIT = "recent_history_limit"
REASON_UPSTREAM_UNREACHABLE = "upstream_unreachable"
REASON_UPSTREAM_STATUS_INVALID = "upstream_status_invalid"
REASON_UPSTREAM_ORIGIN_INVALID = "upstream_origin_invalid"
REASON_UPSTREAM_CONTENT_TYPE_INVALID = "upstream_content_type_invalid"
REASON_PUBLIC_RESPONSE_FRAMING_INVALID = "public_response_framing_invalid"
REASON_PUBLIC_RESPONSE_TOO_LARGE = "public_response_too_large"
REASON_PUBLIC_RESPONSE_INCOMPLETE = "public_response_incomplete"
REASON_PUBLIC_RESPONSE_EMPTY = "public_response_empty"
REASON_INVALID_CANDLE_PAYLOAD = "invalid_candle_payload"
REASON_DUPLICATE_BAR_CONFLICT = "duplicate_bar_conflict"
REASON_INVALID_SPOT_PAYLOAD = "invalid_spot_payload"
REASON_KRAKEN_API_ERROR = "kraken_api_error"

REASON_CODES = frozenset(
    (
        REASON_UNSUPPORTED_ASSET,
        REASON_UNSUPPORTED_SOURCE,
        REASON_UNSUPPORTED_BAR_INTERVAL,
        REASON_WINDOW_BOUNDS_INVALID,
        REASON_WINDOW_NOT_CLOSED,
        REASON_WINDOW_INCOMPLETE,
        REASON_RECENT_HISTORY_LIMIT,
        REASON_UPSTREAM_UNREACHABLE,
        REASON_UPSTREAM_STATUS_INVALID,
        REASON_UPSTREAM_ORIGIN_INVALID,
        REASON_UPSTREAM_CONTENT_TYPE_INVALID,
        REASON_PUBLIC_RESPONSE_FRAMING_INVALID,
        REASON_PUBLIC_RESPONSE_TOO_LARGE,
        REASON_PUBLIC_RESPONSE_INCOMPLETE,
        REASON_PUBLIC_RESPONSE_EMPTY,
        REASON_INVALID_CANDLE_PAYLOAD,
        REASON_DUPLICATE_BAR_CONFLICT,
        REASON_INVALID_SPOT_PAYLOAD,
        REASON_KRAKEN_API_ERROR,
        REASON_REQUESTS_EXHAUSTED,
        REASON_RESPONSE_BYTES_EXHAUSTED,
        REASON_DEADLINE_EXCEEDED,
        REASON_PUBLIC_FETCH_NOT_ALLOWED,
    )
)


@dataclass(frozen=True, slots=True)
class _Binding:
    """Explicit venue/pair/currency binding for one source."""

    source: str
    venue: str
    currency: str
    pairs: Mapping[str, str]

    def pair_for(self, asset: str) -> str:
        return self.pairs[asset]


_BINDINGS = MappingProxyType(
    {
        "binance": _Binding(
            source="binance",
            venue="binance",
            currency="USDT",
            pairs=MappingProxyType({"BTC": "BTCUSDT", "ETH": "ETHUSDT"}),
        ),
        "coinbase": _Binding(
            source="coinbase",
            venue="coinbase",
            currency="USD",
            pairs=MappingProxyType({"BTC": "BTC-USD", "ETH": "ETH-USD"}),
        ),
        "kraken": _Binding(
            source="kraken",
            venue="kraken",
            currency="USD",
            pairs=MappingProxyType({"BTC": "XBTUSD", "ETH": "ETHUSD"}),
        ),
    }
)

# Kraken request pair -> result key (frozen venue mapping, reused).
_KRAKEN_RESULT_KEYS = MappingProxyType(
    {request_pair: result_key for request_pair, result_key in KRAKEN_PAIRS.values()}
)


class _UpstreamFailure(Exception):
    """Classified upstream/payload failure carrying a fixed reason code."""

    def __init__(self, reason_code: str) -> None:
        if reason_code not in REASON_CODES:
            raise ValueError("upstream_failure_reason_unknown")
        super().__init__(reason_code)
        self.reason_code = reason_code


class _Unsupported(Exception):
    """Unsupported input value carrying a fixed reason code."""

    def __init__(self, reason_code: str) -> None:
        if reason_code not in REASON_CODES:
            raise ValueError("unsupported_reason_unknown")
        super().__init__(reason_code)
        self.reason_code = reason_code


class _InsufficientCoverage(Exception):
    """Deterministic coverage shortfall carrying a fixed reason code."""

    def __init__(self, reason_code: str) -> None:
        if reason_code not in REASON_CODES:
            raise ValueError("coverage_reason_unknown")
        super().__init__(reason_code)
        self.reason_code = reason_code


class _NoRedirect(HTTPRedirectHandler):
    """Refuse redirects so the fixed origin check cannot be bypassed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _monotonic() -> float:
    """Clock seam for budget charging (monkeypatched in tests)."""
    return time.monotonic()


def _utcnow() -> datetime:
    """Clock seam for fetch times and closed-window clamping."""
    return datetime.now(UTC)


def _epoch_seconds(value: datetime) -> int:
    return int(value.timestamp())


def _urlopen(request, timeout: float):
    """Transport seam: one direct, proxy-free, redirect-free open."""
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _open_public_json(
    url: str, timeout_seconds: float, read_limit: int
) -> tuple[bytes, datetime]:
    """One bounded GET returning raw JSON bytes plus the fetch time.

    Origin, status, content type and framing are checked before the
    body is used; the body itself is read only through
    ``public_http.read_public_body``. Every failure raises
    ``_UpstreamFailure`` with a fixed reason code only.
    """
    response = None
    try:
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "polymarket-alpha-lab/0.1",
            },
        )
        response = _urlopen(request, timeout_seconds)
        if response.geturl() != url:
            raise _UpstreamFailure(REASON_UPSTREAM_ORIGIN_INVALID)
        if response.status != 200:
            raise _UpstreamFailure(REASON_UPSTREAM_STATUS_INVALID)
        if response.headers.get_content_type() != "application/json":
            raise _UpstreamFailure(REASON_UPSTREAM_CONTENT_TYPE_INVALID)
        if (
            response.headers.get("Content-Encoding", "identity").strip().lower()
            != "identity"
        ):
            raise _UpstreamFailure(REASON_PUBLIC_RESPONSE_FRAMING_INVALID)
        try:
            raw = read_public_body(response, read_limit)
        except IncompleteRead:
            raise _UpstreamFailure(REASON_PUBLIC_RESPONSE_INCOMPLETE) from None
        except PublicBodyError as body_error:
            code = str(body_error)
            if code == REASON_PUBLIC_RESPONSE_TOO_LARGE:
                raise _UpstreamFailure(REASON_PUBLIC_RESPONSE_TOO_LARGE) from None
            if code == REASON_PUBLIC_RESPONSE_INCOMPLETE:
                raise _UpstreamFailure(REASON_PUBLIC_RESPONSE_INCOMPLETE) from None
            if code == REASON_PUBLIC_RESPONSE_EMPTY:
                raise _UpstreamFailure(REASON_PUBLIC_RESPONSE_EMPTY) from None
            raise _UpstreamFailure(REASON_PUBLIC_RESPONSE_FRAMING_INVALID) from None
        return raw, _utcnow()
    except _UpstreamFailure:
        raise
    except HTTPError as error:
        error.close()
        raise _UpstreamFailure(REASON_UPSTREAM_STATUS_INVALID) from None
    except Exception:
        raise _UpstreamFailure(REASON_UPSTREAM_UNREACHABLE) from None
    finally:
        if response is not None:
            response.close()


def _bounded_get(
    url: str, budget: FetchBudget, source_kind: str
) -> tuple[bytes, FetchEvidence]:
    """Charge, fetch and evidence one actual public request.

    Fail-closed order: the budget is charged before the request is
    built; the read limit comes from ``next_response_read_limit()`` so a
    bounded read can never race past the shared budget; a response that
    only fails because the remaining byte budget cannot hold it is
    reported as budget exhaustion, not as an upstream defect.
    """
    now = _monotonic()
    budget.charge_request(now)
    timeout_seconds = budget.effective_timeout_seconds(now)
    read_limit = budget.next_response_read_limit()
    try:
        raw, fetched_at = _open_public_json(url, timeout_seconds, read_limit)
    except _UpstreamFailure as failure:
        if (
            failure.reason_code == REASON_PUBLIC_RESPONSE_TOO_LARGE
            and read_limit < budget.max_response_bytes
        ):
            raise BudgetExhaustedError(REASON_RESPONSE_BYTES_EXHAUSTED) from None
        raise
    budget.charge_response_bytes(len(raw))
    evidence = FetchEvidence.from_bytes(
        source_kind=source_kind,
        source_reference=url,
        fetched_at=fetched_at,
        raw_bytes=raw,
    )
    return raw, evidence


# ---------------------------------------------------------------------------
# normalized values
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClosedBar:
    """One complete (closed) OHLCV bar with UTC boundaries."""

    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        for name in ("open_time", "close_time"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError(f"{name}_invalid")
        if not self.open_time < self.close_time:
            raise ValueError("bar_boundaries_invalid")
        for name in ("open", "high", "low", "close"):
            value = getattr(self, name)
            if type(value) is not float or value <= 0.0:
                raise ValueError(f"{name}_invalid")
        if type(self.volume) is not float or self.volume < 0.0:
            raise ValueError("volume_invalid")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("bar_ohlc_inconsistent")


@dataclass(frozen=True, slots=True)
class CandleWindowObservation:
    """Complete, ascending, deduplicated closed bars for one window.

    ``bars`` contains complete bars only; unfinished bars were excluded
    (counted in ``excluded_unfinished_count``) and duplicates removed
    (counted in ``duplicate_count``). ``covered_*`` is the portion of
    the requested window that is verifiably closed: the clamp to the
    last closed six-hour boundary. On SUCCESS ``gap_count`` is zero by
    construction - a window with a gap is reported as
    ``insufficient_data`` instead of a partial value.
    """

    asset: str
    source: str
    venue: str
    pair: str
    currency: str
    bar_hours: int
    requested_start: datetime
    requested_end: datetime
    covered_start: datetime
    covered_end: datetime
    bars: tuple[ClosedBar, ...]
    expected_bar_count: int
    actual_bar_count: int
    gap_count: int
    duplicate_count: int
    excluded_unfinished_count: int
    excluded_out_of_window_count: int
    fetched_at: datetime
    source_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("asset", "source", "venue", "pair", "currency"):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise ValueError(f"{name}_invalid")
        if type(self.bar_hours) is not int or self.bar_hours <= 0:
            raise ValueError("bar_hours_invalid")
        for name in (
            "requested_start",
            "requested_end",
            "covered_start",
            "covered_end",
            "fetched_at",
        ):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError(f"{name}_invalid")
        if self.source_observed_at is not None and (
            not isinstance(self.source_observed_at, datetime)
            or self.source_observed_at.tzinfo is None
        ):
            raise ValueError("source_observed_at_invalid")
        if not self.requested_start < self.requested_end:
            raise ValueError("requested_window_invalid")
        if not self.covered_start < self.covered_end <= self.requested_end:
            raise ValueError("covered_window_invalid")
        bar_seconds = self.bar_hours * 3600
        if (
            _epoch_seconds(self.covered_start) % bar_seconds
            or _epoch_seconds(self.covered_end) % bar_seconds
            or _epoch_seconds(self.covered_start) <= 0
            or _epoch_seconds(self.covered_end) > _EPOCH_MAX
        ):
            raise ValueError("covered_window_unaligned")
        for name in (
            "expected_bar_count",
            "actual_bar_count",
            "gap_count",
            "duplicate_count",
            "excluded_unfinished_count",
            "excluded_out_of_window_count",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name}_invalid")
        if type(self.bars) is not tuple or any(
            type(bar) is not ClosedBar for bar in self.bars
        ):
            raise ValueError("bars_invalid")
        if not self.bars:
            raise ValueError("bars_empty")
        previous_open: int | None = None
        for bar in self.bars:
            open_epoch = _epoch_seconds(bar.open_time)
            if open_epoch % bar_seconds or (
                _epoch_seconds(bar.close_time) - open_epoch != bar_seconds
            ):
                raise ValueError("bar_alignment_invalid")
            if previous_open is not None and open_epoch <= previous_open:
                raise ValueError("bars_not_strictly_ascending")
            previous_open = open_epoch
        expected = (
            _epoch_seconds(self.covered_end) - _epoch_seconds(self.covered_start)
        ) // bar_seconds
        if self.expected_bar_count != expected:
            raise ValueError("expected_bar_count_mismatch")
        if self.actual_bar_count != len(self.bars):
            raise ValueError("actual_bar_count_mismatch")
        if self.gap_count != expected - self.actual_bar_count:
            raise ValueError("gap_count_mismatch")
        if (
            self.bars[0].open_time != self.covered_start
            or self.bars[-1].close_time != self.covered_end
        ):
            raise ValueError("bars_do_not_span_covered_window")

    @property
    def closes(self) -> tuple[float, ...]:
        """Close prices of the complete bars, oldest first."""
        return tuple(bar.close for bar in self.bars)


@dataclass(frozen=True, slots=True)
class SpotObservation:
    """One independent spot price observation from one venue."""

    asset: str
    source: str
    venue: str
    pair: str
    currency: str
    price: float
    fetched_at: datetime
    source_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("asset", "source", "venue", "pair", "currency"):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise ValueError(f"{name}_invalid")
        if type(self.price) is not float or self.price <= 0.0:
            raise ValueError("price_invalid")
        if not isinstance(self.fetched_at, datetime) or self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at_invalid")
        if self.source_observed_at is not None and (
            not isinstance(self.source_observed_at, datetime)
            or self.source_observed_at.tzinfo is None
        ):
            raise ValueError("source_observed_at_invalid")


def closes_for_vol_window(
    observation: CandleWindowObservation, label: str
) -> tuple[float, ...] | None:
    """Closes for a frozen vol-window label, or ``None`` when incomplete.

    Returns exactly the ``VOL_WINDOW_BARS[label]`` most recent closes of
    complete bars, and ``None`` when the observation does not carry the
    full bar count. This is the only sanctioned bridge into
    ``crypto_model.realized_vol_daily`` / ``realized_vol_windows``: the
    frozen estimator's "use whatever closes exist" compatibility slice
    must never be fed a short history through this adapter and be
    presented as a complete 30/60/87-day window.
    """
    if type(observation) is not CandleWindowObservation:
        raise ValueError("observation_invalid")
    if type(label) is not str:
        raise ValueError("window_label_invalid")
    required = VOL_WINDOW_BARS.get(label)
    if required is None:
        raise ValueError("window_label_unknown")
    if observation.gap_count != 0 or observation.actual_bar_count < required:
        return None
    return observation.closes[-required:]


# ---------------------------------------------------------------------------
# strict payload decoding
# ---------------------------------------------------------------------------


def _bounded_decimal(value: object, *, allow_zero: bool) -> Decimal:
    """Decode one bounded non-negative decimal (number or decimal string)."""
    if type(value) is int:
        number = Decimal(value)
    elif type(value) is Decimal:
        number = value
    elif (
        type(value) is str
        and len(value) <= 64
        and _DECIMAL_STRING.fullmatch(value) is not None
    ):
        number = Decimal(value)
    else:
        raise ValueError("number_invalid")
    if not number.is_finite() or number > _MAX_DECIMAL:
        raise ValueError("number_out_of_range")
    if not allow_zero and number <= 0:
        raise ValueError("number_not_positive")
    if allow_zero and number < 0:
        raise ValueError("number_negative")
    return number


def _check_ohlc(open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> None:
    if not low <= min(open_, close) <= max(open_, close) <= high:
        raise ValueError("ohlc_inconsistent")


def _parse_binance_klines(raw: bytes) -> tuple[tuple[object, ...], ...]:
    """Decode Binance kline rows into normalized ``(t, o, h, l, c, v)``.

    Rows must carry the full documented 12-column layout with the
    close-time column exactly one millisecond before the next boundary,
    so a truncated or re-shaped payload fails closed.
    """
    try:
        payload = strict_json(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD) from None
    if type(payload) is not list or len(payload) > _BINANCE_MAX_BARS_PER_REQUEST:
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
    rows: list[tuple[object, ...]] = []
    for row in payload:
        if type(row) is not list or len(row) != 12:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        open_ms = row[0]
        close_ms = row[6]
        if type(open_ms) is not int or type(close_ms) is not int:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        if open_ms < 0 or open_ms % (BAR_SECONDS * 1000):
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        if close_ms != open_ms + BAR_SECONDS * 1000 - 1:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        try:
            open_ = _bounded_decimal(row[1], allow_zero=False)
            high = _bounded_decimal(row[2], allow_zero=False)
            low = _bounded_decimal(row[3], allow_zero=False)
            close = _bounded_decimal(row[4], allow_zero=False)
            volume = _bounded_decimal(row[5], allow_zero=True)
            _check_ohlc(open_, high, low, close)
            for index in (7, 9, 10):
                _bounded_decimal(row[index], allow_zero=True)
            if type(row[8]) is not int or row[8] < 0:
                raise ValueError("trades_invalid")
            if type(row[11]) is not str:
                raise ValueError("ignore_column_invalid")
        except ValueError:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD) from None
        rows.append((open_ms // 1000, open_, high, low, close, volume))
    return tuple(rows)


def _parse_coinbase_candles(raw: bytes) -> tuple[tuple[object, ...], ...]:
    """Decode Coinbase candle rows into normalized ``(t, o, h, l, c, v)``.

    Wire contract matches the frozen Coinbase decoder: JSON numbers
    (never strings), ``[time, low, high, open, close, volume]`` buckets.
    """
    try:
        payload = strict_json(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD) from None
    if type(payload) is not list or len(payload) > 300:
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
    rows: list[tuple[object, ...]] = []
    for row in payload:
        if type(row) is not list or len(row) != 6:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        stamp = row[0]
        if type(stamp) is not int or not 0 <= stamp <= _EPOCH_MAX:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        if stamp % COINBASE_GRANULARITY_SECONDS:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        try:
            if any(type(value) not in (int, Decimal) for value in row[1:6]):
                raise ValueError("candle_value_not_a_number")
            low = _bounded_decimal(row[1], allow_zero=False)
            high = _bounded_decimal(row[2], allow_zero=False)
            open_ = _bounded_decimal(row[3], allow_zero=False)
            close = _bounded_decimal(row[4], allow_zero=False)
            volume = _bounded_decimal(row[5], allow_zero=True)
            _check_ohlc(open_, high, low, close)
        except ValueError:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD) from None
        rows.append((stamp, open_, high, low, close, volume))
    return tuple(rows)


def _parse_kraken_ohlc(
    raw: bytes, result_key: str
) -> tuple[tuple[tuple[object, ...], ...], int]:
    """Decode one Kraken OHLC envelope into hourly rows plus its cursor.

    Mirrors the frozen Kraken decoder: the error channel must be empty,
    the result must bind exactly the requested pair and the ``last``
    cursor, rows must be chronological, and the response's final row is
    always dropped as uncommitted - even when its timestamp looks
    closed - because Kraken never commits the newest bucket it shows.
    """
    try:
        payload = strict_json(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD) from None
    if type(payload) is not dict or type(payload.get("error")) is not list:
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
    if payload["error"]:
        raise _UpstreamFailure(REASON_KRAKEN_API_ERROR)
    result = payload.get("result")
    if type(result) is not dict or set(result) != {result_key, "last"}:
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
    cursor = result["last"]
    if type(cursor) is not int or not 0 <= cursor <= _EPOCH_MAX:
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
    raw_rows = result[result_key]
    if type(raw_rows) is not list or not 1 <= len(raw_rows) <= KRAKEN_MAX_ROWS:
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
    rows: list[tuple[object, ...]] = []
    for row in raw_rows:
        if type(row) is not list or len(row) != 8:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        stamp = row[0]
        if type(stamp) is not int or not 0 <= stamp <= _EPOCH_MAX:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        if stamp % KRAKEN_HOUR_SECONDS:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
        try:
            if any(type(value) is not str for value in row[1:7]):
                raise ValueError("kraken_value_not_a_string")
            open_ = _bounded_decimal(row[1], allow_zero=False)
            high = _bounded_decimal(row[2], allow_zero=False)
            low = _bounded_decimal(row[3], allow_zero=False)
            close = _bounded_decimal(row[4], allow_zero=False)
            _bounded_decimal(row[5], allow_zero=False)
            volume = _bounded_decimal(row[6], allow_zero=True)
            if type(row[7]) is not int or row[7] < 0:
                raise ValueError("trade_count_invalid")
            _check_ohlc(open_, high, low, close)
        except ValueError:
            raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD) from None
        rows.append((stamp, open_, high, low, close, volume))
    stamps = [row[0] for row in rows]
    if len(set(stamps)) != len(stamps) or stamps != sorted(stamps):
        raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
    committed = tuple(rows[:-1])
    return committed, cursor


def _parse_spot_price(raw: bytes, source: str, binding: _Binding, asset: str) -> float:
    """Decode one spot price from the source's spot payload."""
    try:
        payload = strict_json(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        raise _UpstreamFailure(REASON_INVALID_SPOT_PAYLOAD) from None
    if type(payload) is not dict:
        raise _UpstreamFailure(REASON_INVALID_SPOT_PAYLOAD)
    if source == "binance":
        pair = binding.pair_for(asset)
        if payload.get("symbol") != pair or "price" not in payload:
            raise _UpstreamFailure(REASON_INVALID_SPOT_PAYLOAD)
        price_value = payload["price"]
    elif source == "coinbase":
        if "price" not in payload:
            raise _UpstreamFailure(REASON_INVALID_SPOT_PAYLOAD)
        price_value = payload["price"]
    else:
        pair = binding.pair_for(asset)
        result = payload.get("result")
        if type(result) is not dict or type(payload.get("error")) is not list:
            raise _UpstreamFailure(REASON_INVALID_SPOT_PAYLOAD)
        if payload["error"]:
            raise _UpstreamFailure(REASON_KRAKEN_API_ERROR)
        ticker = result.get(_KRAKEN_RESULT_KEYS[pair])
        if type(ticker) is not dict or type(ticker.get("c")) is not list:
            raise _UpstreamFailure(REASON_INVALID_SPOT_PAYLOAD)
        closes = ticker["c"]
        if not closes:
            raise _UpstreamFailure(REASON_INVALID_SPOT_PAYLOAD)
        price_value = closes[0]
    try:
        price = _bounded_decimal(price_value, allow_zero=False)
    except ValueError:
        raise _UpstreamFailure(REASON_INVALID_SPOT_PAYLOAD) from None
    return float(price)


# ---------------------------------------------------------------------------
# merge and aggregation
# ---------------------------------------------------------------------------


def _merge_rows(
    row_lists: tuple[tuple[tuple[object, ...], ...], ...],
    start_s: int,
    end_s: int,
    now_s: int,
) -> tuple[tuple[ClosedBar, ...], int, int, int, int]:
    """Merge normalized rows into closed bars with explicit bookkeeping.

    Returns ``(bars, missing_bar_count, duplicate_count,
    excluded_unfinished_count, excluded_out_of_window_count)``. Rows are
    classified in a fixed order: an unfinished row (bar end after
    ``now``) is never kept even when its bucket is inside the window; a
    row outside ``[start, end)`` is excluded as out-of-window; an
    identical duplicate is dropped and counted; a conflicting duplicate
    fails closed.
    """
    seen: dict[int, tuple[object, ...]] = {}
    duplicate_count = 0
    unfinished_count = 0
    out_of_window_count = 0
    for rows in row_lists:
        for row in rows:
            stamp = row[0]
            if type(stamp) is not int or stamp % BAR_SECONDS:
                raise _UpstreamFailure(REASON_INVALID_CANDLE_PAYLOAD)
            if stamp + BAR_SECONDS > now_s:
                unfinished_count += 1
                continue
            if not start_s <= stamp < end_s:
                out_of_window_count += 1
                continue
            existing = seen.get(stamp)
            if existing is not None:
                if existing != row:
                    raise _UpstreamFailure(REASON_DUPLICATE_BAR_CONFLICT)
                duplicate_count += 1
                continue
            seen[stamp] = row
    expected = set(range(start_s, end_s, BAR_SECONDS))
    missing = len(expected - set(seen))
    bars = tuple(
        ClosedBar(
            open_time=datetime.fromtimestamp(stamp, tz=UTC),
            close_time=datetime.fromtimestamp(stamp + BAR_SECONDS, tz=UTC),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for stamp, row in sorted(seen.items())
    )
    return bars, missing, duplicate_count, unfinished_count, out_of_window_count


def _aggregate_hourly(
    rows: tuple[tuple[object, ...], ...],
    start_s: int,
    end_s: int,
    now_s: int,
) -> tuple[tuple[ClosedBar, ...], int, int, int, int]:
    """Aggregate committed hourly rows into UTC-aligned six-hour bars.

    A six-hour bucket becomes a bar only when all six of its hourly
    buckets are present and closed; otherwise the bucket counts as
    missing. Hourly rows are classified with the same order as
    ``_merge_rows``: unfinished first, then out-of-window.
    """
    duplicate_count = 0
    unfinished_count = 0
    out_of_window_count = 0
    hourly: dict[int, tuple[object, ...]] = {}
    for row in rows:
        stamp = row[0]
        if stamp + KRAKEN_HOUR_SECONDS > now_s:
            unfinished_count += 1
            continue
        if not start_s <= stamp < end_s:
            out_of_window_count += 1
            continue
        if stamp in hourly:
            if hourly[stamp] != row:
                raise _UpstreamFailure(REASON_DUPLICATE_BAR_CONFLICT)
            duplicate_count += 1
            continue
        hourly[stamp] = row
    bars: list[ClosedBar] = []
    missing = 0
    for bucket in range(start_s, end_s, BAR_SECONDS):
        hours = [hourly.get(bucket + offset) for offset in range(0, BAR_SECONDS, KRAKEN_HOUR_SECONDS)]
        if any(hour is None for hour in hours):
            missing += 1
            continue
        opens = [hour[1] for hour in hours]
        highs = [hour[2] for hour in hours]
        lows = [hour[3] for hour in hours]
        closes = [hour[4] for hour in hours]
        volumes = [hour[5] for hour in hours]
        bars.append(
            ClosedBar(
                open_time=datetime.fromtimestamp(bucket, tz=UTC),
                close_time=datetime.fromtimestamp(bucket + BAR_SECONDS, tz=UTC),
                open=float(opens[0]),
                high=float(max(highs)),
                low=float(min(lows)),
                close=float(closes[-1]),
                volume=float(sum(volumes)),
            )
        )
    return tuple(bars), missing, duplicate_count, unfinished_count, out_of_window_count


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def _validate_common(
    asset: object, source: object, budget: object
) -> tuple[str, str, _Binding]:
    if type(budget) is not FetchBudget:
        raise ValueError("budget_invalid")
    if type(asset) is not str:
        raise ValueError("asset_must_be_a_string")
    if asset not in SUPPORTED_ASSETS:
        raise _Unsupported(REASON_UNSUPPORTED_ASSET)
    if type(source) is not str:
        raise ValueError("source_must_be_a_string")
    if source not in SUPPORTED_SOURCES:
        raise _Unsupported(REASON_UNSUPPORTED_SOURCE)
    return asset, source, _BINDINGS[source]


def _validate_window(start: object, end: object, bar_hours: object) -> tuple[int, int]:
    if type(bar_hours) is not int:
        raise ValueError("bar_hours_must_be_an_int")
    if bar_hours != SUPPORTED_BAR_HOURS:
        raise _Unsupported(REASON_UNSUPPORTED_BAR_INTERVAL)
    for name, value in (("start", start), ("end", end)):
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{name}_must_be_utc_aware")
    start_s = _epoch_seconds(start)
    end_s = _epoch_seconds(end)
    if (
        start.microsecond
        or end.microsecond
        or start_s % BAR_SECONDS
        or end_s % BAR_SECONDS
        or not 0 < start_s < _EPOCH_MAX
        or not 0 < end_s <= _EPOCH_MAX
    ):
        raise _Unsupported(REASON_WINDOW_BOUNDS_INVALID)
    if start_s >= end_s:
        raise _Unsupported(REASON_WINDOW_BOUNDS_INVALID)
    return start_s, end_s


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def fetch_closed_candles(
    asset: str,
    source: str,
    start: datetime,
    end: datetime,
    *,
    bar_hours: int = SUPPORTED_BAR_HOURS,
    budget: FetchBudget,
    allow_public_fetch: bool = False,
    register_response=None,
) -> AdapterResult:
    """Fetch complete six-hour closed candles for BTC or ETH.

    Returns ascending, deduplicated, complete bars covering the closed
    portion of ``[start, end)`` - clamped to the last closed six-hour
    boundary at fetch time - plus per-response evidence. Coverage
    shortfalls are reported as ``insufficient_data`` with the raw
    evidence kept, never as a partial success value.
    """
    try:
        require_public_fetch(allow_public_fetch)
    except FetchPermissionError as error:
        return AdapterResult.failure(
            AdapterStatus.NETWORK_DENIED, error.reason_code
        )
    try:
        asset, source, binding = _validate_common(asset, source, budget)
        start_s, end_s = _validate_window(start, end, bar_hours)
    except _Unsupported as unsupported:
        return AdapterResult.failure(
            AdapterStatus.UNSUPPORTED, unsupported.reason_code
        )

    now_utc = _utcnow()
    now_s = _epoch_seconds(now_utc)
    closed_boundary = now_s - (now_s % BAR_SECONDS)
    effective_end_s = min(end_s, closed_boundary)
    if effective_end_s <= start_s:
        return AdapterResult.failure(
            AdapterStatus.INSUFFICIENT_DATA, REASON_WINDOW_NOT_CLOSED
        )
    source_kind = f"crypto-candles-{source}"
    evidence: list[FetchEvidence] = []
    try:
        if source == "binance":
            bars, missing, duplicates, unfinished, out_of_window = (
                _fetch_binance_and_merge(
                    asset, binding, start_s, effective_end_s, now_s, budget, source_kind, evidence,
                    register_response,
                )
            )
        elif source == "coinbase":
            bars, missing, duplicates, unfinished, out_of_window = (
                _fetch_coinbase_and_merge(
                    asset, binding, start_s, effective_end_s, now_s, budget, source_kind, evidence,
                    register_response,
                )
            )
        else:
            bars, missing, duplicates, unfinished, out_of_window = _fetch_kraken_candles(
                asset, binding, start_s, effective_end_s, now_s, budget, source_kind, evidence,
                register_response,
            )
    except _InsufficientCoverage as coverage:
        return AdapterResult.failure(
            AdapterStatus.INSUFFICIENT_DATA, coverage.reason_code, tuple(evidence)
        )
    except BudgetExhaustedError as error:
        if error.reason_code == REASON_DEADLINE_EXCEEDED:
            return AdapterResult.failure(
                AdapterStatus.DEADLINE_EXCEEDED, error.reason_code, tuple(evidence)
            )
        return AdapterResult.failure(
            AdapterStatus.BUDGET_EXHAUSTED, error.reason_code, tuple(evidence)
        )
    except _UpstreamFailure as failure:
        return AdapterResult.failure(
            AdapterStatus.UPSTREAM_ERROR, failure.reason_code, tuple(evidence)
        )
    if missing:
        return AdapterResult.failure(
            AdapterStatus.INSUFFICIENT_DATA, REASON_WINDOW_INCOMPLETE, tuple(evidence)
        )
    observation = CandleWindowObservation(
        asset=asset,
        source=source,
        venue=binding.venue,
        pair=binding.pair_for(asset),
        currency=binding.currency,
        bar_hours=bar_hours,
        requested_start=datetime.fromtimestamp(start_s, tz=UTC),
        requested_end=datetime.fromtimestamp(end_s, tz=UTC),
        covered_start=datetime.fromtimestamp(start_s, tz=UTC),
        covered_end=datetime.fromtimestamp(effective_end_s, tz=UTC),
        bars=bars,
        expected_bar_count=(effective_end_s - start_s) // BAR_SECONDS,
        actual_bar_count=len(bars),
        gap_count=0,
        duplicate_count=duplicates,
        excluded_unfinished_count=unfinished,
        excluded_out_of_window_count=out_of_window,
        fetched_at=evidence[0].fetched_at,
    )
    return AdapterResult.success(value=observation, evidence=tuple(evidence))


def _fetch_binance_and_merge(
    asset: str,
    binding: _Binding,
    start_s: int,
    effective_end_s: int,
    now_s: int,
    budget: FetchBudget,
    source_kind: str,
    evidence: list[FetchEvidence],
    register_response=None,
) -> tuple[tuple[ClosedBar, ...], int, int, int, int]:
    pair = binding.pair_for(asset)
    row_lists: list[tuple[tuple[object, ...], ...]] = []
    cursor = start_s
    while cursor < effective_end_s:
        page_bars = min(
            _BINANCE_MAX_BARS_PER_REQUEST,
            (effective_end_s - cursor) // BAR_SECONDS,
        )
        query = urlencode(
            {
                "symbol": pair,
                "interval": _BINANCE_INTERVAL,
                "limit": page_bars,
                "startTime": cursor * 1000,
                "endTime": (cursor + page_bars * BAR_SECONDS) * 1000 - 1,
            }
        )
        url = f"{_BINANCE_BASE_URL}/klines?{query}"
        raw, fetch_evidence = _bounded_get(url, budget, source_kind)
        if register_response is not None:
            register_response(fetch_evidence)
        evidence.append(fetch_evidence)
        row_lists.append(_parse_binance_klines(raw))
        cursor += page_bars * BAR_SECONDS
    return _merge_rows(tuple(row_lists), start_s, effective_end_s, now_s)


def _fetch_coinbase_and_merge(
    asset: str,
    binding: _Binding,
    start_s: int,
    effective_end_s: int,
    now_s: int,
    budget: FetchBudget,
    source_kind: str,
    evidence: list[FetchEvidence],
    register_response=None,
) -> tuple[tuple[ClosedBar, ...], int, int, int, int]:
    pair = binding.pair_for(asset)
    row_lists: list[tuple[tuple[object, ...], ...]] = []
    cursor = start_s
    while cursor < effective_end_s:
        chunk_bars = min(
            COINBASE_MAX_CANDLES_PER_WINDOW,
            (effective_end_s - cursor) // BAR_SECONDS,
        )
        chunk = CryptoCandleWindow(
            product_id=pair,
            start=datetime.fromtimestamp(cursor, tz=UTC),
            end=datetime.fromtimestamp(cursor + chunk_bars * BAR_SECONDS, tz=UTC),
            granularity_seconds=COINBASE_GRANULARITY_SECONDS,
        )
        url = chunk.source_reference
        raw, fetch_evidence = _bounded_get(url, budget, source_kind)
        if register_response is not None:
            register_response(fetch_evidence)
        evidence.append(fetch_evidence)
        row_lists.append(_parse_coinbase_candles(raw))
        cursor += chunk_bars * BAR_SECONDS
    return _merge_rows(tuple(row_lists), start_s, effective_end_s, now_s)


def _fetch_kraken_candles(
    asset: str,
    binding: _Binding,
    start_s: int,
    effective_end_s: int,
    now_s: int,
    budget: FetchBudget,
    source_kind: str,
    evidence: list[FetchEvidence],
    register_response=None,
) -> tuple[tuple[ClosedBar, ...], int, int, int, int]:
    pair = binding.pair_for(asset)
    hours_needed = (effective_end_s - start_s) // KRAKEN_HOUR_SECONDS
    if hours_needed > KRAKEN_USABLE_ROWS:
        # The OHLC endpoint ignores paging for hourly intervals and
        # serves at most the most-recent window; requesting a longer
        # history cannot succeed, so refuse it before consuming budget.
        raise _InsufficientCoverage(REASON_RECENT_HISTORY_LIMIT)
    query = urlencode(
        {
            "pair": pair,
            "interval": KRAKEN_INTERVAL_MINUTES,
            "since": start_s,
        }
    )
    url = f"{_KRAKEN_BASE_URL}/OHLC?{query}"
    raw, fetch_evidence = _bounded_get(url, budget, source_kind)
    if register_response is not None:
        register_response(fetch_evidence)
    evidence.append(fetch_evidence)
    rows, _cursor = _parse_kraken_ohlc(raw, _KRAKEN_RESULT_KEYS[pair])
    return _aggregate_hourly(rows, start_s, effective_end_s, now_s)


def fetch_spot(
    asset: str,
    source: str,
    *,
    budget: FetchBudget,
    allow_public_fetch: bool = False,
    register_response=None,
) -> AdapterResult:
    """Fetch one independent spot price observation for BTC or ETH.

    The spot observation is independent of any candle window: separate
    endpoint, separate evidence, separate value. It is a market-data
    observation, never a settlement input.
    """
    try:
        require_public_fetch(allow_public_fetch)
    except FetchPermissionError as error:
        return AdapterResult.failure(
            AdapterStatus.NETWORK_DENIED, error.reason_code
        )
    try:
        asset, source, binding = _validate_common(asset, source, budget)
    except _Unsupported as unsupported:
        return AdapterResult.failure(
            AdapterStatus.UNSUPPORTED, unsupported.reason_code
        )
    pair = binding.pair_for(asset)
    if source == "binance":
        url = f"{_BINANCE_BASE_URL}/ticker/price?{urlencode({'symbol': pair})}"
    elif source == "coinbase":
        url = f"{_COINBASE_BASE_URL}/products/{pair}/ticker"
    else:
        url = f"{_KRAKEN_BASE_URL}/Ticker?{urlencode({'pair': pair})}"
    source_kind = f"crypto-spot-{source}"
    evidence: list[FetchEvidence] = []
    try:
        raw, fetch_evidence = _bounded_get(url, budget, source_kind)
        if register_response is not None:
            register_response(fetch_evidence)
        evidence.append(fetch_evidence)
        price = _parse_spot_price(raw, source, binding, asset)
    except BudgetExhaustedError as error:
        if error.reason_code == REASON_DEADLINE_EXCEEDED:
            return AdapterResult.failure(
                AdapterStatus.DEADLINE_EXCEEDED, error.reason_code, tuple(evidence)
            )
        return AdapterResult.failure(
            AdapterStatus.BUDGET_EXHAUSTED, error.reason_code, tuple(evidence)
        )
    except _UpstreamFailure as failure:
        return AdapterResult.failure(
            AdapterStatus.UPSTREAM_ERROR, failure.reason_code, tuple(evidence)
        )
    observation = SpotObservation(
        asset=asset,
        source=source,
        venue=binding.venue,
        pair=pair,
        currency=binding.currency,
        price=price,
        fetched_at=fetch_evidence.fetched_at,
    )
    return AdapterResult.success(value=observation, evidence=tuple(evidence))


__all__ = (
    "BAR_SECONDS",
    "ClosedBar",
    "CandleWindowObservation",
    "PAPER_ONLY",
    "READONLY",
    "REASON_CODES",
    "REPORT_ONLY",
    "SUPPORTED_ASSETS",
    "SUPPORTED_BAR_HOURS",
    "SUPPORTED_SOURCES",
    "SpotObservation",
    "closes_for_vol_window",
    "fetch_closed_candles",
    "fetch_spot",
)
