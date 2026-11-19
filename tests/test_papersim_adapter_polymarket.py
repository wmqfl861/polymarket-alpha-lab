"""A1 tests: Polymarket public-interface adapter (M2 plan section 6.2).

All offline and synthetic: responses come from
``tests/fixtures/papersim/m2/polymarket.json`` plus generated pages,
served through a fake opener that returns real ``http.client``
responses over in-memory wire bytes, with an injected fixed clock. No
socket is ever opened, no database touched, no file written.

Pins (M2 plan section 2.2 and the A0/A1 row of section 6.2):

- unpermitted calls are denied before any transport is constructed or
  called (zero network), with the contract's NETWORK_DENIED result;
- request, response-byte and deadline caps map to distinct states and
  reason codes (BUDGET_EXHAUSTED vs DEADLINE_EXCEEDED);
- abnormal framing, truncated bodies, wrong origin/status/content type
  are refused with fixed upstream reason codes;
- pagination duplicates and missing coverage are detected; discovery
  beyond 100 markets truncates in the frozen order and records
  incomplete coverage without claiming a full scan;
- token/fee/direction mapping defects (reversed YES/NO labels, missing
  or conflicting tokens, uninterpretable fee units, identity drift)
  reject the candidate while the raw page evidence is kept;
- Gamma ``outcomePrices`` can never appear in executable quote models
  (structural field assertions), and price ``0`` / missing ``None``
  stay distinct;
- single-market slug reads prefer the existing bounded
  ``GammaResearchReader`` when the budget admits its fixed cap, and
  fall back to the adapter's exact-limit bounded GET otherwise.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from email.message import Message
import hashlib
from http.client import HTTPResponse
from io import BytesIO
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit

import pytest

from polymarket_alpha_lab.papersim.adapters import contracts
from polymarket_alpha_lab.papersim.adapters import polymarket as adapter
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot

FIXTURE = json.loads(
    (
        Path(__file__).parent / "fixtures" / "papersim" / "m2" / "polymarket.json"
    ).read_text(encoding="utf-8")
)

FIXED_CLOCK = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
READER_CLOCK = datetime(2026, 11, 15, 12, 0, 1, tzinfo=UTC)

C_M1 = "0x" + "1" * 64
C_M2 = "0x" + "2" * 64
C_M3 = "0x" + "3" * 64
C_M4 = "0x" + "4" * 64
C_M5 = "0x" + "5" * 64
C_M6 = "0x" + "6" * 64
C_M9 = "0x" + "9" * 64
C_M10 = "0x" + "a" * 64
C_SLUG = "0x" + "b" * 64
C_RESOLVED = "0x" + "c" * 64
C_FEE = "0x" + "d" * 64
C_WRONG = "0x" + "e" * 64
C_ETH4K = "0x" + "2a" * 32
C_NONE = "0x" + "4a" * 32
C_AMBIG = "0x" + "5a" * 32

T_YES_M1 = "910000000001"
T_NO_M1 = "910000000002"


def _dumps(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _budget(**overrides: object) -> contracts.FetchBudget:
    """Default test budget: tight enough that every slug read stays on
    the injectable opener path (below the GammaResearchReader 1 MiB
    cap); reader-path tests override the caps explicitly."""
    base: dict[str, object] = {
        "max_requests": 200,
        "max_response_bytes": 65536,
        "max_total_response_bytes": 4 * 1024 * 1024,
        "deadline_monotonic": time.monotonic() + 300.0,
        "request_timeout_seconds": 15.0,
    }
    base.update(overrides)
    return contracts.FetchBudget(**base)


def _reader_budget(**overrides: object) -> contracts.FetchBudget:
    """Budget whose read limit admits GammaResearchReader's fixed 1 MiB cap."""
    return _budget(
        max_response_bytes=contracts.DEFAULT_MAX_RESPONSE_BYTES,
        max_total_response_bytes=64 * 1024 * 1024,
        **overrides,
    )


def _clock() -> datetime:
    return FIXED_CLOCK


class _Socket:
    def __init__(self, wire: bytes) -> None:
        self.wire = wire

    def makefile(self, *args: object) -> BytesIO:
        return BytesIO(self.wire)


def _response(
    body: bytes,
    *,
    url: str | None = None,
    status: bytes = b"200",
    content_type: str = "application/json",
    extra_headers: tuple[bytes, ...] = (),
    content_length: int | None = None,
) -> HTTPResponse:
    """Real stdlib HTTPResponse over in-memory wire bytes; no network."""
    if content_length is None:
        content_length = len(body)
    headers = [b"Content-Type: " + content_type.encode("ascii") + b"\r\n"]
    headers.append(b"Content-Length: " + str(content_length).encode("ascii") + b"\r\n")
    for header in extra_headers:
        headers.append(header + b"\r\n")
    wire = (
        b"HTTP/1.1 " + status + b" OK\r\n"
        + b"".join(headers)
        + b"\r\n"
        + body
    )
    response = HTTPResponse(_Socket(wire))
    response.begin()
    if url is not None:
        response.url = url
    return response


class _RouteMiss(BaseException):
    """Escapes the adapter's fail-closed ``except Exception`` mapping."""


class FakeOpener:
    """Route ``(path, sorted query)`` keys to offline framed responses."""

    def __init__(self, routes: dict) -> None:
        self.routes = dict(routes)
        self.calls: list[tuple[str, float]] = []

    def open(self, url: str, *, timeout_seconds: float):
        self.calls.append((url, timeout_seconds))
        parsed = urlsplit(url)
        key = (parsed.path, tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))))
        if key not in self.routes:
            raise _RouteMiss(f"unexpected open: {parsed.path}")
        body = self.routes[key]
        if isinstance(body, Exception):
            raise body
        return _response(body, url=url)


def _events_key(tag: str, offset: int) -> tuple:
    return (
        "/events",
        (("closed", "false"), ("limit", "50"), ("offset", str(offset)), ("tag_slug", tag)),
    )


def _slug_key(slug: str) -> tuple:
    return ("/markets/slug/" + slug, ())


def _condition_key(condition_id: str) -> tuple:
    return ("/markets", (("condition_id", condition_id),))


def _book_key(token_id: str) -> tuple:
    return ("/book", (("token_id", token_id),))


def _btc_routes() -> dict:
    return {
        _events_key("bitcoin", 0): _dumps(FIXTURE["gamma_events"]["btc_page0"]),
        _events_key("bitcoin", 50): _dumps(FIXTURE["gamma_events"]["btc_page1"]),
        _events_key("bitcoin", 100): _dumps(FIXTURE["gamma_events"]["btc_page2"]),
    }


def _slug_routes(*slugs: str) -> dict:
    return { _slug_key(slug): _dumps(FIXTURE["gamma_market_slug"][slug]) for slug in slugs }


def _book_routes(*token_ids: str) -> dict:
    return {
        _book_key(token): _dumps(FIXTURE["clob_books"][token])
        for token in token_ids
        if token in FIXTURE["clob_books"]
    }


def _http_error() -> HTTPError:
    return HTTPError("https://clob.polymarket.com/book", 404, "Not Found", Message(), None)


def _install_stub_reader(monkeypatch, raw: bytes, *, error: Exception | None = None) -> list:
    created: list[dict] = []

    class StubReader:
        def __init__(self, *, allow_public_fetch: bool, timeout_seconds: int) -> None:
            created.append(
                {"allow_public_fetch": allow_public_fetch, "timeout_seconds": timeout_seconds}
            )

        def fetch(self, *, market_slug: str) -> GammaMarketSnapshot:
            if error is not None:
                raise error
            return GammaMarketSnapshot(market_slug, READER_CLOCK, raw)

    monkeypatch.setattr(adapter, "GammaResearchReader", StubReader)
    return created


def _assert_evidence_digest(result) -> None:
    for item in result.evidence:
        assert item.content_sha256 == hashlib.sha256(item.raw_bytes).hexdigest()


# ---------------------------------------------------------------------------
# module boundary
# ---------------------------------------------------------------------------


def test_module_boundary_flags_and_no_stdout_and_fixed_origins():
    assert adapter.PAPER_ONLY is True
    assert adapter.REPORT_ONLY is True
    assert adapter.READONLY is True
    source = Path(adapter.__file__).read_text(encoding="utf-8")
    assert "print(" not in source
    assert "data-api" not in source
    # Only the two fixed public origins appear as URLs in this module.
    assert source.count("https://") == 2


def test_events_page_limit_is_50_per_known_upstream_cap():
    assert adapter.GAMMA_EVENTS_PAGE_LIMIT == 50


# ---------------------------------------------------------------------------
# permission gate: zero network before allowance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda opener: adapter.discover_markets(
            adapter.DiscoveryQuery(tag_slug="bitcoin"),
            budget=_budget(),
            allow_public_fetch=False,
            opener=opener,
        ),
        lambda opener: adapter.fetch_market(
            adapter.MarketRef(slug="btc-100k-by-dec31"),
            budget=_budget(),
            allow_public_fetch=False,
            opener=opener,
        ),
        lambda opener: adapter.fetch_order_books(
            (T_YES_M1,),
            budget=_budget(),
            allow_public_fetch=False,
            opener=opener,
        ),
        lambda opener: adapter.fetch_resolution(
            adapter.MarketRef(slug="btc-above-95k-oct"),
            budget=_budget(),
            allow_public_fetch=False,
            opener=opener,
        ),
    ],
)
def test_unpermitted_fetch_is_denied_before_any_transport(call, monkeypatch):
    constructed: list[str] = []

    class NeverOpener:
        def __init__(self) -> None:
            constructed.append("constructed")

    monkeypatch.setattr(adapter, "BoundedPublicOpener", NeverOpener)
    opener = FakeOpener({})
    result = call(opener)
    assert result.status is contracts.AdapterStatus.NETWORK_DENIED
    assert result.reason_code == contracts.REASON_PUBLIC_FETCH_NOT_ALLOWED
    assert result.value is None
    assert result.evidence == ()
    assert opener.calls == []
    assert constructed == []


def test_unpermitted_fetch_with_default_opener_constructs_nothing(monkeypatch):
    constructed: list[str] = []

    class NeverOpener:
        def __init__(self) -> None:
            constructed.append("constructed")

    monkeypatch.setattr(adapter, "BoundedPublicOpener", NeverOpener)
    result = adapter.fetch_market(
        adapter.MarketRef(slug="btc-100k-by-dec31"),
        budget=_budget(),
        allow_public_fetch=False,
    )
    assert result.status is contracts.AdapterStatus.NETWORK_DENIED
    assert constructed == []


def test_non_bool_permission_is_rejected_loudly():
    with pytest.raises(ValueError):
        adapter.discover_markets(
            adapter.DiscoveryQuery(tag_slug="bitcoin"),
            budget=_budget(),
            allow_public_fetch=1,
            opener=FakeOpener({}),
        )


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_discover_bitcoin_pages_rejections_duplicates_and_evidence():
    opener = FakeOpener(_btc_routes())
    budget = _budget()
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bitcoin"),
        budget=budget,
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    assert result.reason_code is None
    snapshot = result.value
    assert type(snapshot) is adapter.DiscoverySnapshot

    # Accepted: reversed-label market and the market without prices; the
    # drifted condition id was rejected outright.
    assert [market.slug for market in snapshot.markets] == [
        "eth-touch-3k-week",
        "eth-steady-2k",
    ]
    reversed_market = snapshot.markets[0]
    assert reversed_market.yes_token_id == "910000000011"
    assert reversed_market.no_token_id == "910000000012"
    assert reversed_market.outcome_labels == ("Yes", "No")
    assert reversed_market.reference_last.yes_last == 0.53
    assert reversed_market.reference_last.no_last == 0.47
    steady = snapshot.markets[1]
    assert steady.reference_last is None
    assert steady.fee_rate_bps is None

    reasons = {
        (rejection.condition_id or rejection.slug): rejection.reason_code
        for rejection in snapshot.rejections
    }
    assert reasons == {
        C_M1: "identity_drift",
        C_M3: "token_mapping_missing",
        C_M4: "token_mapping_conflict",
        C_M5: "outcomes_unsupported",
        C_M6: "market_time_invalid",
        C_M9: "fee_unit_uninterpretable",
    }
    for rejection in snapshot.rejections:
        assert rejection.evidence_sha256 in {
            item.content_sha256 for item in result.evidence
        }

    assert snapshot.coverage.pages_fetched == 3
    assert snapshot.coverage.unique_markets_seen == 8
    assert snapshot.coverage.duplicates_dropped == 1
    assert snapshot.coverage.truncated_to_cap is False
    assert snapshot.coverage.coverage_complete is True
    assert snapshot.coverage.stopped_reason == "last_page"

    assert len(result.evidence) == 3
    assert all(item.source_kind == "polymarket-gamma" for item in result.evidence)
    assert all(item.fetched_at == FIXED_CLOCK for item in result.evidence)
    assert [item.raw_bytes for item in result.evidence] == [
        _btc_routes()[_events_key("bitcoin", 0)],
        _btc_routes()[_events_key("bitcoin", 50)],
        _btc_routes()[_events_key("bitcoin", 100)],
    ]
    _assert_evidence_digest(result)
    assert budget.requests_used == 3
    assert budget.response_bytes_used == sum(len(item.raw_bytes) for item in result.evidence)

    # Every page requests the operationally capped limit and correct offsets.
    offsets = []
    for url, _timeout in opener.calls:
        query = dict(parse_qsl(urlsplit(url).query))
        assert query["limit"] == "50"
        assert query["tag_slug"] == "bitcoin"
        assert query["closed"] == "false"
        offsets.append(int(query["offset"]))
    assert offsets == [0, 50, 100]


def test_discover_truncates_in_frozen_order_and_records_incomplete_coverage():
    routes = {
        _events_key("ethx", 0): _dumps(FIXTURE["gamma_events"]["ethx_page0"]),
        _events_key("ethx", 50): _dumps(FIXTURE["gamma_events"]["ethx_page1"]),
    }
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="ethx", max_markets=3),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    assert [market.slug for market in snapshot.markets] == [
        "trunc-e2",
        "trunc-e3",
        "trunc-e1",
    ]
    assert snapshot.coverage.truncated_to_cap is True
    assert snapshot.coverage.coverage_complete is False
    assert snapshot.coverage.stopped_reason == "last_page"


def test_discovery_query_caps_markets_at_first_version_limit():
    assert adapter.FIRST_VERSION_MAX_MARKETS_PER_ROUND == 100
    with pytest.raises(ValueError):
        adapter.DiscoveryQuery(tag_slug="bitcoin", max_markets=101)
    with pytest.raises(ValueError):
        adapter.DiscoveryQuery(tag_slug="bitcoin", max_markets=0)
    with pytest.raises(ValueError):
        adapter.DiscoveryQuery(tag_slug="Bitcoin")
    with pytest.raises(ValueError):
        adapter.DiscoveryQuery(tag_slug="bit coin")
    with pytest.raises(ValueError):
        adapter.DiscoveryQuery(tag_slug="bitcoin", closed="false")
    with pytest.raises(ValueError):
        adapter.discover_markets(
            "bitcoin", budget=_budget(), allow_public_fetch=True, opener=FakeOpener({})
        )
    with pytest.raises(ValueError):
        adapter.discover_markets(
            adapter.DiscoveryQuery(tag_slug="bitcoin"),
            budget=object(),
            allow_public_fetch=True,
            opener=FakeOpener({}),
        )


def _overflow_market(index: int) -> dict:
    return {
        "id": str(1000 + index),
        "conditionId": "0x" + f"{index:064d}",
        "slug": f"overflow-market-{index}",
        "question": f"Overflow market {index}",
        "outcomes": "[\"Yes\", \"No\"]",
        "clobTokenIds": json.dumps([str(940000000000 + 2 * index), str(940000000001 + 2 * index)]),
        "outcomePrices": "[\"0.5\", \"0.5\"]",
        "endDate": (datetime(2026, 12, 1, tzinfo=UTC) + timedelta(days=index))
        .isoformat()
        .replace("+00:00", "Z"),
        "active": True,
        "closed": False,
    }


def test_discover_over_100_markets_truncates_with_incomplete_coverage():
    routes = {}
    for page in range(3):
        body = _dumps([{"id": f"ev{page}-{i}", "markets": [_overflow_market(page * 50 + i)]}
                       for i in range(50)])
        routes[_events_key("overflow", page * 50)] = body
    routes[_events_key("overflow", 150)] = _dumps([])
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="overflow"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    assert snapshot.coverage.unique_markets_seen == 150
    assert len(snapshot.markets) == 100
    assert snapshot.coverage.truncated_to_cap is True
    assert snapshot.coverage.coverage_complete is False
    # Frozen order: earliest end time first, ties by slug then condition id.
    assert [market.slug for market in snapshot.markets] == [
        f"overflow-market-{index}" for index in range(100)
    ]


def test_discover_page_cap_stops_bounded_and_reports_empty_success():
    routes = {
        _events_key("neverending", page * 50): _dumps(
            [{"id": f"ev{page}-{i}", "markets": []} for i in range(50)]
        )
        for page in range(40)
    }
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="neverending"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    assert snapshot.markets == ()
    assert snapshot.coverage.pages_fetched == 40
    assert snapshot.coverage.stopped_reason == "page_cap"
    assert snapshot.coverage.coverage_complete is False


def test_discover_empty_tag_is_a_successful_empty_set():
    routes = {_events_key("ghost", 0): _dumps([])}
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="ghost"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    assert snapshot.markets == ()
    assert snapshot.rejections == ()
    assert snapshot.coverage.coverage_complete is True
    assert snapshot.coverage.stopped_reason == "last_page"


def test_discover_rejected_identity_never_reenters_via_later_occurrence():
    routes = {
        _events_key("second", 0): _dumps(FIXTURE["gamma_events"]["second_page0"]),
        _events_key("second", 50): _dumps(FIXTURE["gamma_events"]["second_page1"]),
        _events_key("second", 100): _dumps(FIXTURE["gamma_events"]["second_page2"]),
    }
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="second"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    # First occurrence (broken: no tokens) rejected; the later valid
    # occurrence of the same identity stays rejected - the identity can
    # never appear in both sets and no favorable side is picked.
    assert snapshot.markets == ()
    assert [(r.condition_id, r.reason_code) for r in snapshot.rejections] == [
        ("0x" + "6a" * 32, "token_mapping_missing")
    ]
    assert snapshot.coverage.duplicates_dropped == 1
    assert snapshot.coverage.coverage_complete is True


def test_discover_accumulation_cap_stops_early():
    routes = {}
    for page in range(9):
        body = _dumps([{"id": f"ev{page}-{i}", "markets": [_overflow_market(page * 50 + i)]}
                       for i in range(50)])
        routes[_events_key("bigtag", page * 50)] = body
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bigtag"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    assert snapshot.coverage.stopped_reason == "accumulation_cap"
    assert snapshot.coverage.pages_fetched == 8
    assert snapshot.coverage.unique_markets_seen == 400
    assert len(snapshot.markets) == 100
    assert snapshot.coverage.truncated_to_cap is True


def test_discover_requests_budget_exhaustion_keeps_page_evidence():
    opener = FakeOpener(_btc_routes())
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bitcoin"),
        budget=_budget(max_requests=2),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.BUDGET_EXHAUSTED
    assert result.reason_code == contracts.REASON_REQUESTS_EXHAUSTED
    assert result.value is None
    assert len(result.evidence) == 2
    _assert_evidence_digest(result)


def test_discover_deadline_before_any_request():
    opener = FakeOpener(_btc_routes())
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bitcoin"),
        budget=_budget(deadline_monotonic=time.monotonic() - 1.0),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.DEADLINE_EXCEEDED
    assert result.reason_code == contracts.REASON_DEADLINE_EXCEEDED
    assert result.evidence == ()
    assert opener.calls == []


def test_discover_deadline_mid_pagination_distinct_from_request_cap():
    class SlowFirstOpener(FakeOpener):
        def open(self, url: str, *, timeout_seconds: float):
            if not self.calls:
                time.sleep(0.2)
            return super().open(url, timeout_seconds=timeout_seconds)

    opener = SlowFirstOpener(_btc_routes())
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bitcoin"),
        budget=_budget(deadline_monotonic=time.monotonic() + 0.1),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.DEADLINE_EXCEEDED
    assert result.reason_code == contracts.REASON_DEADLINE_EXCEEDED
    assert result.value is None
    assert len(result.evidence) == 1


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda body: _response(body, url="https://evil.example/events"),
        lambda body: _response(body, url=None),
        lambda body: _response(body, url="https://gamma-api.polymarket.com/events", status=b"406"),
        lambda body: _response(body, url="https://gamma-api.polymarket.com/events", content_type="text/html"),
        lambda body: _response(
            body,
            url="https://gamma-api.polymarket.com/events",
            extra_headers=(b"Content-Length: 2",),
        ),
        lambda body: _response(
            body,
            url="https://gamma-api.polymarket.com/events",
            content_length=len(body) + 10,
        ),
        lambda body: _response(
            b"", url="https://gamma-api.polymarket.com/events"
        ),
    ],
    ids=[
        "wrong-origin",
        "missing-origin",
        "wrong-status-406",
        "wrong-content-type",
        "duplicate-content-length",
        "truncated-body",
        "empty-body",
    ],
)
def test_discover_refuses_bad_responses_with_fixed_codes(response_factory):
    body = _dumps(FIXTURE["gamma_events"]["btc_page0"])

    class BadOpener(FakeOpener):
        def open(self, url: str, *, timeout_seconds: float):
            self.calls.append((url, timeout_seconds))
            return response_factory(body)

    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bitcoin"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=BadOpener({}),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code in adapter.PUBLIC_READ_REASONS
    assert "http" not in result.reason_code.replace("public_http_rejected", "")
    assert result.value is None


def test_discover_response_too_large_for_budget():
    body = _dumps(FIXTURE["gamma_events"]["btc_page0"])
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bitcoin"),
        budget=_budget(max_response_bytes=64, max_total_response_bytes=1024),
        allow_public_fetch=True,
        opener=FakeOpener({_events_key("bitcoin", 0): body}),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "public_response_too_large"


def test_discover_invalid_page_payload():
    routes = {_events_key("bitcoin", 0): b"not-json{"}
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bitcoin"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "public_payload_invalid"

    routes = {_events_key("bitcoin", 0): _dumps({"events": []})}
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="bitcoin"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "public_payload_invalid"


# ---------------------------------------------------------------------------
# fetch_market
# ---------------------------------------------------------------------------


def test_fetch_market_by_slug_prefers_gamma_research_reader(monkeypatch):
    raw = _dumps(FIXTURE["gamma_market_slug"]["btc-100k-by-dec31"])
    created = _install_stub_reader(monkeypatch, raw)
    opener = FakeOpener({})  # any open is a _RouteMiss
    budget = _reader_budget()
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_SLUG, slug="btc-100k-by-dec31"),
        budget=budget,
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    assert created == [{"allow_public_fetch": True, "timeout_seconds": 15}]
    assert opener.calls == []
    assert budget.requests_used == 1
    assert budget.response_bytes_used == len(raw)

    market = result.value
    assert market.condition_id == C_SLUG
    assert market.slug == "btc-100k-by-dec31"
    assert market.question == "Will Bitcoin close above $100,000 on December 31, 2026?"
    assert market.description is not None and market.description.startswith("Resolves YES")
    assert market.category == "crypto"
    assert market.start_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert market.game_start_at == datetime(2026, 12, 31, 21, 0, tzinfo=UTC)
    assert market.end_at == datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert market.closed is False
    assert market.active is True
    assert market.archived is False
    assert market.fee_rate_bps == 100.0
    assert market.outcome_labels == ("Yes", "No")
    assert market.yes_token_id == "910000000041"
    assert market.no_token_id == "910000000042"
    assert market.reference_last.yes_last == 0.642
    assert market.reference_last.no_last == 0.358

    assert len(result.evidence) == 1
    assert result.evidence[0].raw_bytes == raw
    assert result.evidence[0].fetched_at == READER_CLOCK
    assert result.evidence[0].source_kind == "polymarket-gamma"
    assert result.evidence[0].content_sha256 == hashlib.sha256(raw).hexdigest()


def test_fetch_market_tight_budget_uses_exact_limit_bounded_get(monkeypatch):
    created = _install_stub_reader(
        monkeypatch, _dumps(FIXTURE["gamma_market_slug"]["btc-100k-by-dec31"])
    )
    opener = FakeOpener(_slug_routes("btc-100k-by-dec31"))
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_SLUG, slug="btc-100k-by-dec31"),
        budget=_budget(max_response_bytes=65536, max_total_response_bytes=1 * 1024 * 1024),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    assert created == []
    assert len(opener.calls) == 1
    assert opener.calls[0][0] == "https://gamma-api.polymarket.com/markets/slug/btc-100k-by-dec31"
    assert result.evidence[0].fetched_at == FIXED_CLOCK
    assert result.value.slug == "btc-100k-by-dec31"


@pytest.mark.parametrize(
    "timeout,expected",
    [(90.0, 60), (0.5, 1)],
)
def test_fetch_market_reader_timeout_is_clamped(monkeypatch, timeout, expected):
    raw = _dumps(FIXTURE["gamma_market_slug"]["btc-100k-by-dec31"])
    created = _install_stub_reader(monkeypatch, raw)
    result = adapter.fetch_market(
        adapter.MarketRef(slug="btc-100k-by-dec31"),
        budget=_reader_budget(
            request_timeout_seconds=timeout, deadline_monotonic=time.monotonic() + 600.0
        ),
        allow_public_fetch=True,
        opener=FakeOpener({}),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    assert created[0]["timeout_seconds"] == expected


def test_fetch_market_reader_failure_maps_fixed_reason(monkeypatch):
    created = _install_stub_reader(
        monkeypatch, b"", error=ValueError("gamma_public_fetch_failed")
    )
    result = adapter.fetch_market(
        adapter.MarketRef(slug="btc-100k-by-dec31"),
        budget=_reader_budget(),
        allow_public_fetch=True,
        opener=FakeOpener({}),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "gamma_slug_read_failed"
    assert result.value is None
    assert created != []
    assert result.evidence == ()


def test_fetch_market_by_condition_found_and_normalized():
    routes = {_condition_key(C_SLUG): _dumps(FIXTURE["gamma_market_condition"][C_SLUG])}
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_SLUG),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    assert result.value.slug == "btc-100k-by-dec31"
    assert result.evidence[0].source_reference.endswith("condition_id=" + C_SLUG)


def test_fetch_market_by_condition_not_found_keeps_evidence():
    routes = {_condition_key(C_NONE): _dumps(FIXTURE["gamma_market_condition"][C_NONE])}
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_NONE),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.INSUFFICIENT_DATA
    assert result.reason_code == "market_not_found"
    assert result.value is None
    assert len(result.evidence) == 1
    _assert_evidence_digest(result)


def test_fetch_market_by_condition_ambiguous_identity():
    routes = {_condition_key(C_AMBIG): _dumps(FIXTURE["gamma_market_condition"][C_AMBIG])}
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_AMBIG),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.INSUFFICIENT_DATA
    assert result.reason_code == "market_identity_ambiguous"


def test_fetch_market_reversed_labels_map_yes_and_no_explicitly():
    routes = {_condition_key(C_M2): _dumps(FIXTURE["gamma_market_condition"][C_M2])}
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_M2),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    market = result.value
    assert market.outcome_labels == ("Yes", "No")
    assert market.yes_token_id == "910000000011"
    assert market.no_token_id == "910000000012"
    assert market.reference_last.yes_last == 0.53
    assert market.reference_last.no_last == 0.47


def test_fetch_market_identity_mismatch_rejects_with_evidence():
    opener = FakeOpener(_slug_routes("btc-wrong-id"))
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_M1, slug="btc-wrong-id"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.INSUFFICIENT_DATA
    assert result.reason_code == "market_identity_mismatch"
    assert result.value is None
    assert len(result.evidence) == 1


def test_fetch_market_uninterpretable_fee_unit_is_unsupported():
    opener = FakeOpener(_slug_routes("btc-fee-weird"))
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_FEE, slug="btc-fee-weird"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.UNSUPPORTED
    assert result.reason_code == "fee_unit_uninterpretable"
    assert len(result.evidence) == 1


def test_market_ref_input_validation():
    with pytest.raises(ValueError):
        adapter.MarketRef()
    with pytest.raises(ValueError):
        adapter.MarketRef(condition_id="0x1234")
    with pytest.raises(ValueError):
        adapter.MarketRef(slug="Not A Slug")
    with pytest.raises(ValueError):
        adapter.fetch_market(
            "btc-100k-by-dec31", budget=_budget(), allow_public_fetch=True, opener=FakeOpener({})
        )


# ---------------------------------------------------------------------------
# fetch_order_books
# ---------------------------------------------------------------------------


def test_discover_closed_flag_maps_to_gamma_query_parameter():
    def _events_key_closed(tag: str, offset: int) -> tuple:
        return (
            "/events",
            (
                ("closed", "true"),
                ("limit", "50"),
                ("offset", str(offset)),
                ("tag_slug", tag),
            ),
        )

    opener = FakeOpener({_events_key_closed("closedtag", 0): _dumps([])})
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="closedtag", closed=True),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    assert "closed=true" in opener.calls[0][0]


def test_fetch_market_accepts_decoded_list_fields():
    decoded = {
        "id": "901",
        "conditionId": C_M2,
        "slug": "eth-touch-3k-week",
        "question": "Will Ethereum touch $3,000 this week?",
        "outcomes": ["No", "Yes"],
        "clobTokenIds": ["910000000012", "910000000011"],
        "outcomePrices": ["0.47", "0.53"],
        "endDate": "2026-11-20T00:00:00Z",
        "active": True,
        "closed": False,
    }
    opener = FakeOpener({_slug_key("eth-touch-3k-week"): _dumps(decoded)})
    result = adapter.fetch_market(
        adapter.MarketRef(condition_id=C_M2, slug="eth-touch-3k-week"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    market = result.value
    assert market.yes_token_id == "910000000011"
    assert market.no_token_id == "910000000012"
    assert market.reference_last.yes_last == 0.53


def test_fetch_order_books_normalizes_and_sorts_ladders():
    tokens = ("910000000001", "910000000002", "910000000003")
    opener = FakeOpener(_book_routes(*tokens))
    budget = _budget()
    result = adapter.fetch_order_books(
        tokens,
        budget=budget,
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    assert snapshot.rejections == ()
    assert [book.token_id for book in snapshot.books] == list(tokens)

    first = snapshot.books[0]
    assert [(level.price, level.size) for level in first.bids] == [
        (0.55, 40.0),
        (0.53, 210.0),
        (0.51, 95.0),
    ]
    assert [(level.price, level.size) for level in first.asks] == [
        (0.56, 80.0),
        (0.57, 120.0),
    ]
    assert first.best_bid == adapter.BookLevel(price=0.55, size=40.0)
    assert first.best_ask == adapter.BookLevel(price=0.56, size=80.0)
    assert first.book_observed_at == datetime.fromtimestamp(1762300800000 / 1000.0, tz=UTC)

    second = snapshot.books[1]
    assert second.best_bid is None
    assert second.best_ask == adapter.BookLevel(price=0.44, size=300.0)

    third = snapshot.books[2]
    assert third.best_bid is None
    assert third.best_ask is None
    assert third.bids == () and third.asks == ()

    assert len(result.evidence) == 3
    assert all(item.source_kind == "polymarket-clob" for item in result.evidence)
    assert result.evidence[0].source_observed_at == first.book_observed_at
    _assert_evidence_digest(result)
    assert budget.requests_used == 3
    assert opener.calls[0][1] == 15.0


def test_fetch_order_books_timeout_clamped_by_deadline():
    opener = FakeOpener(_book_routes(T_YES_M1))
    adapter.fetch_order_books(
        (T_YES_M1,),
        budget=_budget(
            request_timeout_seconds=30.0,
            deadline_monotonic=time.monotonic() + 2.0,
        ),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    recorded = opener.calls[0][1]
    assert 0.0 < recorded <= 2.0


def test_fetch_order_books_invalid_level_rejects_token_keeps_evidence():
    opener = FakeOpener(_book_routes("910000000004"))
    result = adapter.fetch_order_books(
        ("910000000004",),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    assert snapshot.books == ()
    assert [(r.token_id, r.reason_code) for r in snapshot.rejections] == [
        ("910000000004", "book_payload_invalid")
    ]
    assert len(result.evidence) == 1
    assert result.evidence[0].raw_bytes == _dumps(FIXTURE["clob_books"]["910000000004"])
    _assert_evidence_digest(result)


def test_fetch_order_books_http_error_records_token_rejection():
    routes = {
        _book_key(T_YES_M1): _dumps(FIXTURE["clob_books"][T_YES_M1]),
        _book_key(T_NO_M1): _http_error(),
    }
    result = adapter.fetch_order_books(
        (T_YES_M1, T_NO_M1),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    snapshot = result.value
    assert len(snapshot.books) == 1
    assert [(r.token_id, r.reason_code) for r in snapshot.rejections] == [
        (T_NO_M1, "public_http_rejected")
    ]


def test_fetch_order_books_budget_exhaustion_aborts_whole_batch():
    opener = FakeOpener(_book_routes(T_YES_M1, T_NO_M1))
    result = adapter.fetch_order_books(
        (T_YES_M1, T_NO_M1),
        budget=_budget(max_requests=1),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.BUDGET_EXHAUSTED
    assert result.reason_code == contracts.REASON_REQUESTS_EXHAUSTED
    assert result.value is None
    assert len(result.evidence) == 1
    assert len(opener.calls) == 1


def test_fetch_order_books_input_validation_and_dedupe():
    with pytest.raises(ValueError):
        adapter.fetch_order_books(
            ("not-a-token",), budget=_budget(), allow_public_fetch=True, opener=FakeOpener({})
        )
    with pytest.raises(ValueError):
        adapter.fetch_order_books(
            [], budget=_budget(), allow_public_fetch=True, opener=FakeOpener({})
        )
    with pytest.raises(ValueError):
        adapter.fetch_order_books(
            "910000000001", budget=_budget(), allow_public_fetch=True, opener=FakeOpener({})
        )
    opener = FakeOpener(_book_routes(T_YES_M1))
    result = adapter.fetch_order_books(
        (T_YES_M1, T_YES_M1),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    assert len(result.value.books) == 1
    assert len(opener.calls) == 1


# ---------------------------------------------------------------------------
# fetch_resolution
# ---------------------------------------------------------------------------


def test_fetch_resolution_terminal_yes_cross_validated_consistent():
    opener = FakeOpener(
        {
            **_slug_routes("btc-above-95k-oct"),
            **_book_routes("910000000051", "910000000052"),
        }
    )
    budget = _budget()
    result = adapter.fetch_resolution(
        adapter.MarketRef(condition_id=C_RESOLVED, slug="btc-above-95k-oct"),
        budget=budget,
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    observation = result.value
    assert observation.gamma_terminal_outcome == "terminal_yes"
    assert observation.terminal_consistency == "consistent"
    assert observation.terminal_reference.yes_last == 1.0
    assert observation.terminal_reference.no_last == 0.0
    assert observation.closed is True
    assert observation.resolution_status == "resolved"
    assert [book.token_id for book in observation.clob_books] == [
        "910000000051",
        "910000000052",
    ]
    assert budget.requests_used == 3
    assert len(result.evidence) == 3
    assert result.evidence[1].source_observed_at is not None
    _assert_evidence_digest(result)


def test_fetch_resolution_gamma_clob_conflict_is_recorded_not_resolved():
    opener = FakeOpener(
        {
            **_slug_routes("eth-above-3k-nov"),
            **_book_routes("910000000061", "910000000062"),
        }
    )
    result = adapter.fetch_resolution(
        adapter.MarketRef(slug="eth-above-3k-nov"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    observation = result.value
    assert observation.gamma_terminal_outcome == "terminal_yes"
    assert observation.terminal_consistency == "conflict"
    # Both sides kept: Gamma corner AND the contradicting CLOB bid.
    assert observation.terminal_reference.yes_last == 1.0
    assert observation.clob_books[0].best_bid.price == 0.90


def test_fetch_resolution_open_market_is_inconclusive():
    opener = FakeOpener(
        {
            **_slug_routes("btc-open-corner"),
            **_book_routes("910000000017", "910000000018"),
        }
    )
    result = adapter.fetch_resolution(
        adapter.MarketRef(slug="btc-open-corner"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    observation = result.value
    assert observation.gamma_terminal_outcome == "not_terminal"
    assert observation.terminal_consistency == "inconclusive"


def test_fetch_resolution_missing_book_is_unverified():
    routes = {
        **_slug_routes("eth-below-4k-nov"),
        _book_key("910000000071"): _dumps(FIXTURE["clob_books"]["910000000071"]),
        _book_key("910000000072"): _http_error(),
    }
    result = adapter.fetch_resolution(
        adapter.MarketRef(condition_id=C_ETH4K, slug="eth-below-4k-nov"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    observation = result.value
    assert observation.gamma_terminal_outcome == "terminal_no"
    assert observation.terminal_consistency == "unverified"
    assert len(observation.clob_books) == 1
    # price 0 is a value, not missing
    assert observation.terminal_reference.yes_last == 0.0
    assert observation.terminal_reference.no_last == 1.0


def test_fetch_resolution_terminal_with_empty_books_is_inconclusive():
    opener = FakeOpener(
        {
            **_slug_routes("btc-eq-50k-nov"),
            **_book_routes("910000000081", "910000000082"),
        }
    )
    result = adapter.fetch_resolution(
        adapter.MarketRef(slug="btc-eq-50k-nov"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=opener,
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    observation = result.value
    assert observation.gamma_terminal_outcome == "terminal_yes"
    assert observation.terminal_consistency == "inconclusive"


def test_fetch_resolution_budget_exhaustion_mid_books_keeps_gamma_evidence():
    routes = {
        **_slug_routes("btc-above-95k-oct"),
        **_book_routes("910000000051"),
    }
    result = adapter.fetch_resolution(
        adapter.MarketRef(slug="btc-above-95k-oct"),
        budget=_budget(max_requests=2),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.BUDGET_EXHAUSTED
    assert result.reason_code == contracts.REASON_REQUESTS_EXHAUSTED
    assert result.value is None
    assert len(result.evidence) == 2


# ---------------------------------------------------------------------------
# structural guarantees
# ---------------------------------------------------------------------------


def test_gamma_reference_prices_structurally_excluded_from_quote_models():
    quote_fields = {field.name for field in dataclasses.fields(adapter.OrderBookSnapshot)}
    assert quote_fields == {"token_id", "bids", "asks", "book_observed_at"}
    assert {field.name for field in dataclasses.fields(adapter.BookLevel)} == {"price", "size"}
    assert {field.name for field in dataclasses.fields(adapter.BooksSnapshot)} == {
        "books",
        "rejections",
    }
    # Reference prices live only on the market/observation models.
    assert "reference_last" in {f.name for f in dataclasses.fields(adapter.PolymarketMarket)}
    assert "terminal_reference" in {
        f.name for f in dataclasses.fields(adapter.ResolutionObservation)
    }
    serialized = repr(adapter.OrderBookSnapshot(
        token_id=T_YES_M1, bids=(), asks=(), book_observed_at=None
    ))
    assert "reference" not in serialized
    assert "last" not in serialized


def test_price_zero_missing_and_unknown_stay_distinct():
    # Zero is a value (fee 0, reference 0.0); absence is None.
    assert adapter.ReferencePrices(yes_last=0.0, no_last=1.0).yes_last == 0.0
    assert adapter.ReferencePrices(yes_last=None, no_last=None).yes_last is None
    with pytest.raises(ValueError):
        adapter.ReferencePrices(yes_last=-0.1, no_last=None)
    with pytest.raises(ValueError):
        adapter.ReferencePrices(yes_last=1.5, no_last=None)
    with pytest.raises(ValueError):
        adapter.BookLevel(price=0.0, size=1.0)
    with pytest.raises(ValueError):
        adapter.BookLevel(price=1.0, size=1.0)


def test_discovery_fee_zero_is_a_value_not_unknown():
    routes = {
        _events_key("feezero", 0): _dumps(
            [{"id": "fz", "markets": [FIXTURE["gamma_events"]["btc_page0"][0]["markets"][0]]}]
        ),
        _events_key("feezero", 50): _dumps([]),
    }
    result = adapter.discover_markets(
        adapter.DiscoveryQuery(tag_slug="feezero"),
        budget=_budget(),
        allow_public_fetch=True,
        opener=FakeOpener(routes),
        now_utc=_clock,
    )
    assert result.status is contracts.AdapterStatus.SUCCESS
    market = result.value.markets[0]
    assert market.slug == "btc-touch-95k-week"
    assert market.fee_rate_bps == 0.0
    assert market.reference_last.yes_last == 0.55


def test_rejection_reason_vocabulary_is_closed():
    assert "identity_drift" in adapter.CANDIDATE_REJECTION_REASONS
    assert "token_mapping_conflict" in adapter.CANDIDATE_REJECTION_REASONS
    assert set(adapter.DISCOVERY_STOP_REASONS) == {
        "last_page",
        "page_cap",
        "accumulation_cap",
    }
    with pytest.raises(ValueError):
        adapter.MarketRejection(
            condition_id=None,
            slug=None,
            reason_code="made_up_reason",
            evidence_sha256="0" * 64,
        )
