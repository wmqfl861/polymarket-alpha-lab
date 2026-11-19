"""m2 A4 tests: bounded FotMob / ESPN NBA scan inputs (adapters/sports.py).

Pins (M2 plan sections 2.5 and 6.2, A4 row):

- the ORIGINAL parse results survive the bounded-read retrofit: for
  identical synthetic wire inputs the adapter's value is EXACTLY the
  frozen pre-A4 transform output (``fotmob.fotmob_to_understat`` /
  ``espn_nba.build_snapshot``), pinned by frozen SHA-256 anchors over
  the deterministic serialization;
- reads that run past the per-response limit, truncated bodies,
  non-allowlist origins (redirect drift included) and bad framing are
  rejected before any value is produced;
- explicit permission: without ``allow_public_fetch=True`` the result
  is ``NETWORK_DENIED`` and the transport is never called (zero
  network), at both the adapter entrance and the module fetch layer;
- the M2 ESPN path performs ZERO disk-cache writes (monkeypatched
  writer would fail; tmp dir stays empty) — the on-disk cache is the
  legacy ops path only;
- budget discipline: every actual attempt charges the shared budget;
  waits/retries clamp to the remaining round time; the three
  exhaustion kinds return distinct fixed reason codes
  (requests_exhausted / response_bytes_exhausted under
  BUDGET_EXHAUSTED, deadline_exceeded under DEADLINE_EXCEEDED);
- NBA stays SHADOW: the docstring states it and every success value
  carries ``shadow=True`` (data adapter availability is not entry
  authorization);
- every HTTP response yields one ``FetchEvidence`` whose
  ``content_sha256`` digests the exact served bytes.

All tests are offline and synthetic: fixture bytes + fake transport +
fake clock; no network, no database, no writes.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import email.message
import hashlib
import json
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pytest

from polymarket_alpha_lab import public_http
from polymarket_alpha_lab.papersim import espn_nba, fotmob
from polymarket_alpha_lab.papersim.adapters import contracts, sports

FIXTURE = Path(__file__).parent / "fixtures" / "papersim" / "m2" / "sports.json"
FIXTURE_DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))

FOTMOB_SCOPE = FIXTURE_DATA["fotmob"]["scope"]
NBA_SCOPE = FIXTURE_DATA["espn_nba"]["scope"]

# Frozen byte anchors (deterministic serialization of the frozen
# transform outputs over the fixture payloads; computed once against
# the pre-A4 transforms — see test_fotmob_value_identical_to_frozen_
# transform / test_nba_value_identical_to_frozen_build).
FOTMOB_SNAPSHOT_SHA256 = (
    "62fa6839216c11f37ebe628358ca259b60245cadbcbe70549a2b0101b6ef0043"
)
NBA_SNAPSHOT_SHA256 = (
    "58ee58fc8a2b7af8837cedd0d2007e710b9b47d0b9b05c22fe16e077414a596c"
)

# Fixed ESPN generated_at (fetch_nba_season stamps real now; the
# adapter test pins it so the byte anchor is deterministic).
FIXED_NOW = datetime(2026, 11, 10, 6, 30, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    """``datetime`` subclass with a frozen ``now`` (other behaviour intact)."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN202
        return FIXED_NOW if tz is not None else FIXED_NOW.replace(tzinfo=None)


# Closed A4 failure vocabulary: reason_code is always one of these
# fixed short strings (never URLs, query parameters or bodies).
A4_REASON_CODES = frozenset({
    contracts.REASON_PUBLIC_FETCH_NOT_ALLOWED,
    contracts.REASON_REQUESTS_EXHAUSTED,
    contracts.REASON_RESPONSE_BYTES_EXHAUSTED,
    contracts.REASON_DEADLINE_EXCEEDED,
    sports.REASON_SCOPE_INVALID,
    sports.REASON_BUDGET_INVALID,
    sports.REASON_SOURCE_SCHEMA_DRIFT,
    sports.REASON_UPSTREAM_UNAVAILABLE,
    sports.REASON_ORIGIN_REJECTED,
    sports.REASON_PUBLIC_BODY_REJECTED,
})


def _assert_failure(
    result: contracts.AdapterResult,
    status: contracts.AdapterStatus,
    reason_code: str,
) -> None:
    assert result.status is status, (result.status, result.reason_code)
    assert result.reason_code == reason_code
    assert result.value is None
    assert result.reason_code in A4_REASON_CODES
    assert "://" not in result.reason_code  # no URL echo in codes


def _budget(**overrides: object) -> contracts.FetchBudget:
    base: dict[str, object] = {
        "max_requests": 10,
        "max_response_bytes": 1 << 20,
        "deadline_monotonic": time.monotonic() + 60.0,
        "request_timeout_seconds": 5.0,
    }
    base.update(overrides)
    return contracts.FetchBudget(**base)


@pytest.fixture(autouse=True)
def _reset_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate both modules' polite-spacing stamps; silence sleeps."""
    monkeypatch.setattr(fotmob, "_last_fetch_monotonic", None)
    monkeypatch.setattr(espn_nba, "_last_fetch_monotonic", None)


class _WireResponse:
    """HTTP-shaped double for the bounded transport.

    Carries framing ``headers`` (Content-Length by default, overridable
    for the rejection tests), an ``amt``-bounded ``read`` and
    ``geturl()`` (the final response origin — redirect drift target).
    """

    def __init__(
        self,
        body: bytes,
        *,
        final_url: str,
        content_length: int | None = None,
        omit_content_length: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self._final_url = final_url
        self.headers = email.message.Message()
        if not omit_content_length:
            declared = len(body) if content_length is None else content_length
            self.headers["Content-Length"] = str(declared)
        for key, value in (extra_headers or {}).items():
            self.headers[key] = value

    def __enter__(self) -> "_WireResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def geturl(self) -> str:
        return self._final_url

    def read(self, amt: int | None = None) -> bytes:
        if amt is None or amt < 0:
            return self._body
        return self._body[:amt]


_TEAM_BY_ID = {str(ids[0]): abbrev for abbrev, ids in espn_nba.NBA_TEAMS.items()}


class _Router:
    """Fake transport serving the fixture's wire bytes by URL.

    Records every request; ``responses()`` returns the exact bytes
    served so evidence digests can be cross-checked. ``decorate``
    replaces the response construction for the rejection tests.
    """

    def __init__(
        self,
        *,
        final_url_override: str | None = None,
        decorate=None,  # noqa: ANN001
    ) -> None:
        self.requests: list = []
        self.calls: list[tuple[str, str]] = []
        self._final_url_override = final_url_override
        self._decorate = decorate
        self._served: list[bytes] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_Router":
        router = self

        def _fake_urlopen(request, timeout):  # noqa: ANN001, ANN202
            router.requests.append(request)
            body, final_url = router._route(request.full_url)
            response = _WireResponse(body, final_url=final_url)
            if router._decorate is not None:
                response = router._decorate(response)
            router._served.append(response.read())
            return response

        monkeypatch.setattr(fotmob.urllib.request, "urlopen", _fake_urlopen)
        monkeypatch.setattr(espn_nba.urllib.request, "urlopen", _fake_urlopen)
        return router

    def served(self) -> list[bytes]:
        return list(self._served)

    def _route(self, url: str) -> tuple[bytes, str]:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/data/leagues":
            self.calls.append(("fotmob_leagues", parsed.query))
            blob = json.dumps(FIXTURE_DATA["fotmob"]["league_payload"]).encode()
        elif parsed.path == "/api/data/matchDetails":
            mid = query["matchId"][0]
            self.calls.append(("fotmob_details", mid))
            blob = json.dumps(FIXTURE_DATA["fotmob"]["match_details"][mid]).encode()
        elif parsed.path.endswith("/schedule"):
            abbrev = _TEAM_BY_ID[parsed.path.split("/")[-2]]
            self.calls.append(("espn_schedule", abbrev))
            blob = json.dumps(FIXTURE_DATA["espn_nba"]["schedules"][abbrev]).encode()
        elif parsed.path.endswith("/summary"):
            eid = query["event"][0]
            self.calls.append(("espn_summary", eid))
            blob = json.dumps(FIXTURE_DATA["espn_nba"]["summaries"][eid]).encode()
        else:
            raise AssertionError("fake transport: unrouted request path")
        final_url = self._final_url_override or url
        return blob, final_url


# --------------------------------------------------------------------------
# parse identity: adapter value == frozen pre-A4 transform output
# --------------------------------------------------------------------------

def test_fotmob_value_identical_to_frozen_transform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    router = _Router().install(monkeypatch)
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    assert result.is_success
    league = FIXTURE_DATA["fotmob"]["league_payload"]
    details = FIXTURE_DATA["fotmob"]["match_details"]
    expected = fotmob.fotmob_to_understat(
        {"fixtures": league, "details": details},
        "Championship", "2026", max_round=46,
    )
    assert result.value["snapshot"] == expected
    assert result.value["source_kind"] == sports.SOURCE_KIND_FOTMOB
    assert result.value["league_key"] == "Championship"
    assert result.value["season_key"] == "2026"
    blob = json.dumps(result.value["snapshot"], sort_keys=True).encode()
    assert hashlib.sha256(blob).hexdigest() == FOTMOB_SNAPSHOT_SHA256
    # request shape: one league fetch, then details in kept order (m1, m2)
    assert [kind for kind, _ in router.calls] == [
        "fotmob_leagues", "fotmob_details", "fotmob_details",
    ]
    assert [key for _, key in router.calls[1:]] == ["m1", "m2"]
    assert router.requests[0].full_url == (
        "https://www.fotmob.com/api/data/leagues?id=48&season=2026%2F2027"
    )


def test_nba_value_identical_to_frozen_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(espn_nba.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(espn_nba, "datetime", _FixedDatetime)
    router = _Router().install(monkeypatch)
    result = sports.fetch_nba_inputs(
        NBA_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    assert result.is_success
    assert result.value["shadow"] is True  # NBA stays a shadow lane
    assert result.value["source_kind"] == sports.SOURCE_KIND_ESPN_NBA
    bundle = {
        "season": "2026-27",
        "season_year": 2027,
        "generated_at": FIXED_NOW.isoformat(),
        "schedules": FIXTURE_DATA["espn_nba"]["schedules"],
        "summaries": FIXTURE_DATA["espn_nba"]["summaries"],
    }
    expected = espn_nba.build_snapshot(bundle, expected_teams=["GS", "LAL"])
    assert result.value["snapshot"] == expected
    blob = json.dumps(result.value["snapshot"], sort_keys=True).encode()
    assert hashlib.sha256(blob).hexdigest() == NBA_SNAPSHOT_SHA256
    # one schedule per scope team (sorted) then each summary exactly once
    assert router.calls == [
        ("espn_schedule", "GS"), ("espn_schedule", "LAL"),
        ("espn_summary", "401809244"),
    ]
    # spot anchors: the ET-date and possession logic are the frozen ones
    lal = result.value["snapshot"]["teams"]["LAL"]["games"]
    assert [row["date"] for row in lal] == ["2026-10-21"]
    assert lal[0]["poss"] == pytest.approx(85 - 10 + 14 + 0.44 * 21)
    assert lal[0]["rest_days"] is None and lal[0]["b2b"] is False


def test_fotmob_match_window_bounds_the_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    router = _Router().install(monkeypatch)
    # a full kept-match window is byte-identical to the default fetch
    result = sports.fetch_fotmob_inputs(
        {**FOTMOB_SCOPE, "match_ids": ["m1", "m2"]},
        budget=_budget(), allow_public_fetch=True,
    )
    assert result.is_success
    assert len(result.evidence) == 3
    assert [row["id"] for row in result.value["snapshot"]["Championship_2026"]["dates"]] == ["m1", "m2"]
    # a partial window bounds the wire work (league + m1 only) but the
    # frozen transform refuses to promote the shrunken season: m2 is
    # skip-counted and the 50% skip rate trips the fail-closed ceiling
    # — no silent shrink is possible through the window
    partial = sports.fetch_fotmob_inputs(
        {**FOTMOB_SCOPE, "match_ids": ["m1"]},
        budget=_budget(), allow_public_fetch=True,
    )
    _assert_failure(
        partial,
        contracts.AdapterStatus.UPSTREAM_ERROR,
        sports.REASON_SOURCE_SCHEMA_DRIFT,
    )
    assert len(partial.evidence) == 2  # league + m1 responses kept
    # a window that misses every kept match is an invalid scope, with
    # the league response kept as partial evidence
    miss = sports.fetch_fotmob_inputs(
        {**FOTMOB_SCOPE, "match_ids": ["m5"]},  # m5 is cancelled
        budget=_budget(), allow_public_fetch=True,
    )
    _assert_failure(miss, contracts.AdapterStatus.UNSUPPORTED,
                    sports.REASON_SCOPE_INVALID)
    assert len(miss.evidence) == 1
    assert router.calls == [
        ("fotmob_leagues", "id=48&season=2026%2F2027"),
        ("fotmob_details", "m1"), ("fotmob_details", "m2"),
        ("fotmob_leagues", "id=48&season=2026%2F2027"),
        ("fotmob_details", "m1"),
        ("fotmob_leagues", "id=48&season=2026%2F2027"),
    ]


# --------------------------------------------------------------------------
# evidence: one FetchEvidence per response, digesting the exact bytes
# --------------------------------------------------------------------------

def test_evidence_per_response_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(espn_nba.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(espn_nba, "datetime", _FixedDatetime)
    _Router().install(monkeypatch)

    fotmob_result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    assert fotmob_result.is_success
    served = [
        json.dumps(FIXTURE_DATA["fotmob"]["league_payload"]).encode(),
        json.dumps(FIXTURE_DATA["fotmob"]["match_details"]["m1"]).encode(),
        json.dumps(FIXTURE_DATA["fotmob"]["match_details"]["m2"]).encode(),
    ]
    assert len(fotmob_result.evidence) == 3
    for evidence, raw in zip(fotmob_result.evidence, served):
        assert evidence.source_kind == sports.SOURCE_KIND_FOTMOB
        assert evidence.raw_bytes == raw
        assert evidence.content_sha256 == hashlib.sha256(raw).hexdigest()
        assert evidence.source_observed_at is None  # unknown, not faked
        assert evidence.fetched_at.tzinfo is not None

    nba_result = sports.fetch_nba_inputs(
        NBA_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    assert nba_result.is_success
    assert len(nba_result.evidence) == 3  # GS, LAL schedules + 1 summary
    for evidence in nba_result.evidence:
        assert evidence.source_kind == sports.SOURCE_KIND_ESPN_NBA
        assert (
            evidence.content_sha256
            == hashlib.sha256(evidence.raw_bytes).hexdigest()
        )


def test_evidence_source_reference_is_the_exact_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    _Router().install(monkeypatch)
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    assert result.is_success
    assert [e.source_reference for e in result.evidence] == [
        "https://www.fotmob.com/api/data/leagues?id=48&season=2026%2F2027",
        "https://www.fotmob.com/api/data/matchDetails?matchId=m1",
        "https://www.fotmob.com/api/data/matchDetails?matchId=m2",
    ]


# --------------------------------------------------------------------------
# explicit permission: denial happens before any connection
# --------------------------------------------------------------------------

def test_permission_denied_zero_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("transport must not be called without permission")

    monkeypatch.setattr(fotmob.urllib.request, "urlopen", _forbidden)
    monkeypatch.setattr(espn_nba.urllib.request, "urlopen", _forbidden)
    for call in (
        lambda: sports.fetch_fotmob_inputs(
            FOTMOB_SCOPE, budget=_budget(), allow_public_fetch=False
        ),
        lambda: sports.fetch_nba_inputs(
            NBA_SCOPE, budget=_budget(), allow_public_fetch=False
        ),
    ):
        result = call()
        _assert_failure(
            result,
            contracts.AdapterStatus.NETWORK_DENIED,
            contracts.REASON_PUBLIC_FETCH_NOT_ALLOWED,
        )
        assert result.evidence == ()
    # the omitted argument is the paper-only denial too
    assert sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=_budget()
    ).status is contracts.AdapterStatus.NETWORK_DENIED
    assert sports.fetch_nba_inputs(
        NBA_SCOPE, budget=_budget()
    ).status is contracts.AdapterStatus.NETWORK_DENIED


def test_module_fetch_layer_denies_before_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("transport must not be called without permission")

    monkeypatch.setattr(fotmob.urllib.request, "urlopen", _forbidden)
    monkeypatch.setattr(espn_nba.urllib.request, "urlopen", _forbidden)
    for call in (
        lambda: fotmob.fetch_fotmob_league(48, "2026/2027"),
        lambda: fotmob.fetch_match_details("m1"),
        lambda: espn_nba.fetch_team_schedule("POR", cache_dir=None),
        lambda: espn_nba.fetch_game_summary("1", cache_dir=None),
        lambda: espn_nba.fetch_nba_season("2026-27", cache_dir=None),
    ):
        with pytest.raises(contracts.FetchPermissionError):
            call()


# --------------------------------------------------------------------------
# bounded reads: origin, framing, truncation, size limits
# --------------------------------------------------------------------------

def test_redirect_outside_origin_allowlist_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(espn_nba.time, "sleep", lambda seconds: None)
    _Router(
        final_url_override="https://evil.example.com/api/data/leagues"
    ).install(monkeypatch)
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    _assert_failure(
        result, contracts.AdapterStatus.UPSTREAM_ERROR, sports.REASON_ORIGIN_REJECTED
    )
    assert result.evidence == ()  # bytes were never accepted as evidence


def test_nba_origin_allowlist_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(espn_nba.time, "sleep", lambda seconds: None)
    _Router(
        final_url_override="https://evil.example.com/apis/site/v2/sports"
    ).install(monkeypatch)
    result = sports.fetch_nba_inputs(
        NBA_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    _assert_failure(
        result, contracts.AdapterStatus.UPSTREAM_ERROR, sports.REASON_ORIGIN_REJECTED
    )


def test_truncated_body_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declared Content-Length longer than the delivered body: the
    framing gate refuses (a syntactically valid prefix is NOT a
    complete snapshot)."""
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)

    def _truncate(response: _WireResponse) -> _WireResponse:
        return _WireResponse(
            response.read()[:-1], final_url=response.geturl(),
            content_length=10 ** 6,
        )

    _Router(decorate=_truncate).install(monkeypatch)
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    _assert_failure(
        result,
        contracts.AdapterStatus.UPSTREAM_ERROR,
        sports.REASON_PUBLIC_BODY_REJECTED,
    )


def test_bad_transfer_encoding_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)

    def _reframe(response: _WireResponse) -> _WireResponse:
        return _WireResponse(
            response.read(), final_url=response.geturl(),
            omit_content_length=True,
            extra_headers={"Transfer-Encoding": "gzip"},
        )

    _Router(decorate=_reframe).install(monkeypatch)
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    _assert_failure(
        result,
        contracts.AdapterStatus.UPSTREAM_ERROR,
        sports.REASON_PUBLIC_BODY_REJECTED,
    )


def test_response_over_read_limit_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared body larger than the budgeted per-response read limit
    is refused before the wire bytes are consumed as a snapshot."""
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)

    def _grow(response: _WireResponse) -> _WireResponse:
        return _WireResponse(
            response.read(), final_url=response.geturl(),
            content_length=(1 << 20) + 1,
        )

    _Router(decorate=_grow).install(monkeypatch)
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE,
        budget=_budget(max_response_bytes=1 << 20),
        allow_public_fetch=True,
    )
    _assert_failure(
        result,
        contracts.AdapterStatus.UPSTREAM_ERROR,
        sports.REASON_PUBLIC_BODY_REJECTED,
    )


# --------------------------------------------------------------------------
# budget discipline: charging, clamped waits, three exhaustions
# --------------------------------------------------------------------------

def test_budget_requests_exhausted_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    router = _Router().install(monkeypatch)
    budget = _budget(max_requests=2)  # league + m1 fit; m2 cannot
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=budget, allow_public_fetch=True
    )
    _assert_failure(
        result,
        contracts.AdapterStatus.BUDGET_EXHAUSTED,
        contracts.REASON_REQUESTS_EXHAUSTED,
    )
    assert len(result.evidence) == 2  # partial evidence kept
    assert budget.requests_used == 2
    assert len(router.requests) == 2  # the third connection never opened


def test_budget_response_bytes_exhausted_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response that consumes the byte budget exactly leaves the next
    request unable to serve even one byte (charge_request refuses
    before any connection)."""
    body = json.dumps({"header": {"teams": [{"score": 1}, {"score": 1}]}}).encode()
    body = body + b" " * (400 - len(body))  # exactly 400 bytes
    router = _Router().install(monkeypatch)

    def _fixed(response: _WireResponse) -> _WireResponse:
        return _WireResponse(body, final_url=response.geturl())

    router._decorate = _fixed
    budget = _budget(
        max_response_bytes=400, max_total_response_bytes=400, max_requests=5
    )
    first = fotmob.fetch_match_details(
        "m1", allow_public_fetch=True, budget=budget
    )
    assert first == {"header": {"teams": [{"score": 1}, {"score": 1}]}}
    with pytest.raises(contracts.BudgetExhaustedError) as exc:
        fotmob.fetch_match_details("m2", allow_public_fetch=True, budget=budget)
    assert exc.value.reason_code == contracts.REASON_RESPONSE_BYTES_EXHAUSTED
    assert len(router.requests) == 1  # second connection never opened


def test_budget_deadline_exceeded_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    router = _Router().install(monkeypatch)
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE,
        budget=_budget(deadline_monotonic=time.monotonic() - 1.0),
        allow_public_fetch=True,
    )
    _assert_failure(
        result,
        contracts.AdapterStatus.DEADLINE_EXCEEDED,
        contracts.REASON_DEADLINE_EXCEEDED,
    )
    assert result.evidence == ()
    assert router.requests == []  # the deadline refused before connecting


def test_waits_clamped_by_deadline_stop_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polite spacing and backoff never run past the round deadline:
    a 1.3 s spacing want is clamped to a 1.1 s budget, the deadline
    then kills the loop before the retry backoff — one connection
    total instead of the frozen 4 attempts."""
    real_sleep = time.sleep
    sleeps: list[float] = []

    def _record_and_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr(fotmob.time, "sleep", _record_and_sleep)
    router = _Router().install(monkeypatch)
    router._decorate = lambda response: (_ for _ in ()).throw(OSError("down"))
    fotmob._last_fetch_monotonic = time.monotonic()  # spacing wants 1.3 s
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE,
        budget=_budget(deadline_monotonic=time.monotonic() + 1.1),
        allow_public_fetch=True,
    )
    _assert_failure(
        result,
        contracts.AdapterStatus.DEADLINE_EXCEEDED,
        contracts.REASON_DEADLINE_EXCEEDED,
    )
    # exactly one wait happened, clamped to the 1.1 s round window —
    # never the full 1.3 s spacing, never the 5 s backoff — and the
    # round died before the connection was ever opened
    assert len(sleeps) == 1
    assert 0.5 < sleeps[0] <= 1.1
    assert router.requests == []
    assert len(router.requests) < fotmob.FETCH_ATTEMPTS


def test_retry_charges_each_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries are actual requests: every attempt charges the shared
    budget (no per-retry reset); the two failed attempts leave no
    evidence because no body was ever accepted."""
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    router = _Router().install(monkeypatch)
    boom = {"count": 0}

    def _flaky(response: _WireResponse) -> _WireResponse:
        boom["count"] += 1
        if boom["count"] <= 2:
            raise OSError("down")
        return response

    router._decorate = _flaky
    budget = _budget(max_requests=5)
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=budget, allow_public_fetch=True,
    )
    assert result.is_success
    # the league endpoint was attempted three times (2 failures + 1
    # success) before the two detail fetches: five transport calls
    assert router.calls == [
        ("fotmob_leagues", "id=48&season=2026%2F2027"),
        ("fotmob_leagues", "id=48&season=2026%2F2027"),
        ("fotmob_leagues", "id=48&season=2026%2F2027"),
        ("fotmob_details", "m1"),
        ("fotmob_details", "m2"),
    ]
    # every actual attempt charged, retries included — exactly
    # exhausting the budget (no per-retry reset)
    assert budget.requests_used == 5
    assert len(router.requests) == 5


def test_module_timeout_clamped_by_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-attempt socket timeout never exceeds the budget's
    clamped effective timeout (frozen 30 s module default -> 2.0 s)."""
    captured: dict = {}

    def _fake_urlopen(request, timeout):  # noqa: ANN001, ANN202
        captured["timeout"] = timeout
        return _WireResponse(b"{}", final_url=request.full_url)

    monkeypatch.setattr(fotmob.urllib.request, "urlopen", _fake_urlopen)
    fotmob.fetch_match_details(
        "m1", allow_public_fetch=True,
        budget=_budget(
            request_timeout_seconds=2.0,
            deadline_monotonic=time.monotonic() + 30.0,
        ),
    )
    assert captured["timeout"] == 2.0


# --------------------------------------------------------------------------
# M2 ESPN path: zero disk-cache writes; NBA shadow boundary
# --------------------------------------------------------------------------

def test_m2_nba_path_writes_no_disk_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def _no_write(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("M2 path must not write the legacy disk cache")

    monkeypatch.setattr(espn_nba, "_atomic_write_json", _no_write)
    monkeypatch.setattr(espn_nba, "DEFAULT_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(espn_nba.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(espn_nba, "datetime", _FixedDatetime)
    _Router().install(monkeypatch)
    result = sports.fetch_nba_inputs(
        NBA_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    assert result.is_success
    assert list(tmp_path.iterdir()) == []  # tmp dir untouched


def test_nba_shadow_boundary_documented() -> None:
    docs = " ".join((sports.fetch_nba_inputs.__doc__ or "").split())
    assert "SHADOW ONLY" in docs
    assert "does NOT authorize NBA entry" in docs
    module_docs = " ".join((sports.__doc__ or "").split())
    assert "SHADOW POSITIONING" in module_docs
    assert "does NOT authorize NBA entry" in module_docs
    # value-level flag is asserted in test_nba_value_identical_to_frozen_build


# --------------------------------------------------------------------------
# scope validation (fixed reason codes; zero network)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("scope", [
    None,
    "league=48",
    [],
    {},
    {"season": "2026"},
    {"league": {"id": 48}},
    {"league": {"key": "Championship"}, "season": "2026"},
    {"league": {"id": "48", "key": "Championship"}, "season": "2026"},
    {"league": {"id": 48, "key": ""}, "season": "2026"},
    {"league": {"id": 48, "key": "Championship"}},
    {"league": {"id": 48, "key": "Championship"}, "season": "20x6"},
    {"league": {"id": 48, "key": "Championship", "max_round": "46"},
     "season": "2026"},
    {"league": {"id": 48, "key": "Championship", "team_count": "24"},
     "season": "2026"},
    {"league": {"id": 48, "key": "Championship"}, "season": "2026",
     "match_ids": "m1"},
    {"league": {"id": 48, "key": "Championship"}, "season": "2026",
     "match_ids": [None]},
])
def test_fotmob_scope_invalid(monkeypatch: pytest.MonkeyPatch, scope) -> None:
    def _forbidden(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("a valid scope must never reach the transport here")

    monkeypatch.setattr(fotmob.urllib.request, "urlopen", _forbidden)
    result = sports.fetch_fotmob_inputs(scope, budget=_budget(),
                                        allow_public_fetch=True)
    _assert_failure(
        result, contracts.AdapterStatus.UNSUPPORTED, sports.REASON_SCOPE_INVALID
    )
    assert result.evidence == ()


@pytest.mark.parametrize("scope", [
    None,
    "season=2026-27",
    {},
    {"season": "2026_27"},
    {"season": "2026-27", "teams": "GS"},
    {"season": "2026-27", "teams": ["XXX"]},
    {"season": "2026-27", "teams": []},
    {"season": "2026-27", "teams": ["GS", "GS-X"]},
])
def test_nba_scope_invalid(monkeypatch: pytest.MonkeyPatch, scope) -> None:
    def _forbidden(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("a valid scope must never reach the transport here")

    monkeypatch.setattr(espn_nba.urllib.request, "urlopen", _forbidden)
    result = sports.fetch_nba_inputs(scope, budget=_budget(),
                                     allow_public_fetch=True)
    _assert_failure(
        result, contracts.AdapterStatus.UNSUPPORTED, sports.REASON_SCOPE_INVALID
    )
    assert result.evidence == ()


def test_budget_object_required() -> None:
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=None, allow_public_fetch=True  # type: ignore[arg-type]
    )
    _assert_failure(
        result, contracts.AdapterStatus.UNSUPPORTED, sports.REASON_BUDGET_INVALID
    )
    result = sports.fetch_nba_inputs(
        NBA_SCOPE, budget="nope", allow_public_fetch=True  # type: ignore[arg-type]
    )
    _assert_failure(
        result, contracts.AdapterStatus.UNSUPPORTED, sports.REASON_BUDGET_INVALID
    )


def test_upstream_schema_drift_maps_to_fixed_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    _Router().install(monkeypatch)
    drift = dict(FOTMOB_SCOPE)
    drift["season"] = "2027"  # fixture payload still says 2026/2027
    result = sports.fetch_fotmob_inputs(
        drift, budget=_budget(), allow_public_fetch=True
    )
    _assert_failure(
        result,
        contracts.AdapterStatus.UPSTREAM_ERROR,
        sports.REASON_SOURCE_SCHEMA_DRIFT,
    )
    # the drift surfaces at the frozen parse boundary, right after the
    # league response: one evidence row, zero detail fetches wasted
    assert len(result.evidence) == 1


def test_transport_giving_up_maps_to_fixed_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fotmob.time, "sleep", lambda seconds: None)
    router = _Router().install(monkeypatch)
    router._decorate = lambda response: (_ for _ in ()).throw(OSError("down"))
    result = sports.fetch_fotmob_inputs(
        FOTMOB_SCOPE, budget=_budget(), allow_public_fetch=True
    )
    _assert_failure(
        result,
        contracts.AdapterStatus.UPSTREAM_ERROR,
        sports.REASON_UPSTREAM_UNAVAILABLE,
    )
    assert len(router.requests) == fotmob.FETCH_ATTEMPTS  # frozen retry count


def test_phase_boundaries_are_declared() -> None:
    assert sports.PAPER_ONLY is True
    assert sports.REPORT_ONLY is True
    assert sports.READONLY is True
