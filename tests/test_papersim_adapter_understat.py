"""A3 tests: the Understat adapter (M2 plan sections 2.4 and 6.2).

Pins, all offline with synthetic fixture data, a fake transport and a fake
clock (no real network, no database, zero file writes):

- mirror validation invariants: team identity continuity against the
  baseline, match-ID uniqueness, fixture identity stability, completed
  matches may not disappear, completed/history bidirectional
  correspondence, season window and future-completed rejection;
- numeric semantics: xG ``0`` is valid, missing stays ``None`` on
  scheduled rows, negative/NaN/Inf/bool xG and non-integral goals reject;
- failure never promotes: rejected fetches keep evidence but carry no
  value; assembly refuses non-success blocks and incomplete coverage, so
  partial data never becomes a latest valid snapshot;
- historical blocks preserved under their ORIGINAL digest, by reference,
  never recalculated (``refetched: False``);
- assembly is a pure function whose snapshot feeds the existing
  :mod:`polymarket_alpha_lab.papersim.xg` consumer unchanged;
- A0 contract consumption: exact-bool ``allow_public_fetch`` gate before
  any connection, one shared budget (request/byte/deadline exhaustion
  distinguished), one ``FetchEvidence`` per accepted response, bounded
  reads through ``public_http.read_public_body`` with framing checks;
- wire tolerance: getLeagueData JSON, ``{"__JSON__": ...}`` wrappers and
  HTML pages with embedded ``JSON.parse('...')`` payloads all parse;
  truncation and bad framing reject;
- fixed source identity ``understat``: FotMob-shaped blocks are refused,
  never silently mixed.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import builtins
import copy
import hashlib
import json
from http.client import HTTPResponse
from io import BytesIO
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime

import urllib.error

import pytest

from polymarket_alpha_lab.papersim.adapters import contracts
from polymarket_alpha_lab.papersim.adapters import understat

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "papersim" / "m2" / "understat.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(FIXTURE["now"])
SEASON = FIXTURE["season"]
PRIOR_SEASON = FIXTURE["prior_season"]
LEAGUES = tuple(understat.UNDERSTAT_LEAGUES)
EXPECTED = FIXTURE["expected"]


# ---------------------------------------------------------------------------
# helpers: budget, wire-level fake transport, write guard
# ---------------------------------------------------------------------------


def _budget(**overrides: object) -> contracts.FetchBudget:
    base: dict[str, object] = {
        "max_requests": 8,
        "max_response_bytes": 1 << 20,
        "deadline_monotonic": time.monotonic() + 60.0,
        "request_timeout_seconds": 5.0,
    }
    base.update(overrides)
    return contracts.FetchBudget(**base)


class _Socket:
    """Minimal socket double feeding wire bytes to stdlib HTTPResponse."""

    def __init__(self, wire: bytes) -> None:
        self.wire = wire

    def makefile(self, *args: object) -> BytesIO:
        return BytesIO(self.wire)


def _wire_response(
    body: bytes,
    *,
    status_line: bytes = b"HTTP/1.1 200 OK",
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),),
    content_length: int | None = None,
    url: str = "https://understat.com/getLeagueData/EPL/" + SEASON,
) -> HTTPResponse:
    """Real stdlib HTTPResponse over in-memory wire bytes; no network."""
    lines = list(headers)
    if content_length is None and not any(
        name.lower() == "content-length" for name, _ in lines
    ):
        lines.append(("Content-Length", str(len(body))))
    elif content_length is not None and not any(
        name.lower() == "content-length" for name, _ in lines
    ):
        lines.append(("Content-Length", str(content_length)))
    header_block = "".join(f"{name}: {value}\r\n" for name, value in lines)
    wire = status_line + b"\r\n" + header_block.encode("ascii") + b"\r\n" + body
    response = HTTPResponse(_Socket(wire))
    response.begin()
    response.url = url
    return response


def _league_url(league: str) -> str:
    return f"https://understat.com/getLeagueData/{league}/{SEASON}"


def _response_body(league: str) -> bytes:
    return json.dumps(FIXTURE["leagues"][league]["response"]).encode("utf-8")


def _install_transport(monkeypatch: pytest.MonkeyPatch, responder) -> list[tuple[object, object]]:
    """Replace the adapter's only network seam; record every call."""
    calls: list[tuple[object, object]] = []

    def fake_urlopen(request, timeout=None):
        calls.append((request, timeout))
        return responder(request, timeout)

    monkeypatch.setattr(understat, "_urlopen", fake_urlopen)
    return calls


def _ok_responder(league: str, body: bytes | None = None, **response_kwargs: object):
    payload = _response_body(league) if body is None else body
    response_kwargs.setdefault("url", _league_url(league))

    def respond(request, timeout):
        return _wire_response(payload, **response_kwargs)

    return respond


def _fetch_ok(monkeypatch: pytest.MonkeyPatch, league: str, *, baseline=None, **response_kwargs: object):
    calls = _install_transport(monkeypatch, _ok_responder(league, **response_kwargs))
    result = understat.fetch_league_snapshot(
        league,
        SEASON,
        FIXTURE["leagues"][league]["baseline"] if baseline is None else baseline,
        now=NOW,
        budget=_budget(),
        allow_public_fetch=True,
    )
    assert result.is_success, (result.status, result.reason_code)
    assert len(calls) == 1
    return result


def _forbid_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if the adapter opens any file for writing."""
    real_open = builtins.open

    def guarded(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            raise AssertionError(f"unexpected file write: {file} ({mode})")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)


def _prior_block(league: str, *, season: str = PRIOR_SEASON, data: dict | None = None):
    payload = FIXTURE["leagues"][league]["prior"] if data is None else data
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return understat.UnderstatPriorBlock(
        league=league,
        season=season,
        data=payload,
        content_sha256=digest,
        source_reference=f"readonly-file:xg_understat_hist.json#{league}_{season}",
    )


def _fetch_all(monkeypatch: pytest.MonkeyPatch):
    return [_fetch_ok(monkeypatch, league) for league in LEAGUES]


# ---------------------------------------------------------------------------
# module boundary
# ---------------------------------------------------------------------------


def test_module_keeps_paper_only_boundary_flags():
    assert understat.PAPER_ONLY is True
    assert understat.REPORT_ONLY is True
    assert understat.READONLY is True
    assert understat.SOURCE_KIND == "understat"


def test_module_import_is_offline_and_brings_no_heavy_clients():
    code = (
        "import sys\n"
        "import polymarket_alpha_lab.papersim.adapters.understat as u\n"
        "leaked = [n for n in sys.modules if n.split('.')[0] in"
        " {'psycopg', 'requests', 'urllib3', 'httpx'}]\n"
        "assert not leaked, leaked\n"
        "assert u.PAPER_ONLY and u.REPORT_ONLY and u.READONLY\n"
        "assert u.SOURCE_KIND == 'understat'\n"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True)


def test_reason_codes_are_fixed_short_identifiers():
    for name in dir(understat):
        if name.startswith("REASON_"):
            code = getattr(understat, name)
            assert isinstance(code, str), name
            assert re.fullmatch(r"[a-z0-9_]+", code), (name, code)


# ---------------------------------------------------------------------------
# fetch: happy paths
# ---------------------------------------------------------------------------


def test_fetch_success_returns_validated_block_and_evidence(monkeypatch):
    body = _response_body("EPL")
    result = _fetch_ok(monkeypatch, "EPL")
    value = result.value
    assert value["schema"] == understat.LEAGUE_BLOCK_SCHEMA
    assert value["source_kind"] == "understat"
    assert value["league"] == "EPL"
    assert value["season"] == SEASON
    # raw validated block preserved verbatim inside the envelope
    assert value["data"] == FIXTURE["leagues"]["EPL"]["response"]
    assert value["source"]["source_reference"] == f"understat.com/getLeagueData/EPL/{SEASON}"
    assert value["source"]["content_sha256"] == hashlib.sha256(body).hexdigest()
    assert value["source"]["fetched_at"] == NOW.isoformat()
    expected = EXPECTED["per_league"]["EPL"]
    assert value["summary"] == {
        "team_count": expected["team_count"],
        "completed_matches": expected["completed_matches"],
        "scheduled_matches": expected["scheduled_matches"],
        "latest_match_date": expected["latest_match_date"],
    }
    (evidence,) = result.evidence
    assert evidence.source_kind == "understat"
    assert evidence.source_reference == f"understat.com/getLeagueData/EPL/{SEASON}"
    assert evidence.fetched_at == NOW
    assert evidence.source_observed_at is None  # upstream time unknown, not faked
    assert evidence.raw_bytes == body
    assert evidence.content_sha256 == contracts.sha256_hexdigest(body)


def test_fetch_charges_one_request_and_the_exact_response_bytes(monkeypatch):
    body = _response_body("EPL")
    budget = _budget()
    calls = _install_transport(monkeypatch, _ok_responder("EPL"))
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, FIXTURE["leagues"]["EPL"]["baseline"],
        now=NOW, budget=budget, allow_public_fetch=True,
    )
    assert result.is_success
    assert len(calls) == 1
    assert budget.requests_used == 1
    assert budget.response_bytes_used == len(body)


def test_fetch_all_five_leagues_offline(monkeypatch):
    results = _fetch_all(monkeypatch)
    assert [r.value["league"] for r in results] == list(LEAGUES)
    for league, result in zip(LEAGUES, results):
        assert result.value["summary"] == {
            "team_count": EXPECTED["per_league"][league]["team_count"],
            "completed_matches": EXPECTED["per_league"][league]["completed_matches"],
            "scheduled_matches": EXPECTED["per_league"][league]["scheduled_matches"],
            "latest_match_date": EXPECTED["per_league"][league]["latest_match_date"],
        }


def test_fetch_without_baseline_is_a_valid_first_fetch(monkeypatch):
    result = _fetch_ok(monkeypatch, "Serie_A", baseline=None)
    assert result.value["summary"]["completed_matches"] == EXPECTED["per_league"]["Serie_A"]["completed_matches"]


def test_fetch_xg_zero_is_valid_and_missing_stays_none(monkeypatch):
    result = _fetch_ok(monkeypatch, "EPL")
    matches = {m["id"]: m for m in result.value["matches"]}
    # match id "2" completed with xG 0.0 on both sides: 0 is a valid value
    zero = matches["2"]
    assert zero["is_result"] is True
    assert zero["home_xg"] == 0.0 and zero["away_xg"] == 0.0
    assert zero["home_goals"] == 0 and zero["away_goals"] == 0
    # scheduled rows keep missing values as None, even with junk numbers
    scheduled = matches["3"]
    assert scheduled["is_result"] is False
    assert scheduled["home_xg"] is None and scheduled["away_xg"] is None
    assert scheduled["home_goals"] is None and scheduled["away_goals"] is None
    # matches are canonically ordered by (datetime, id)
    datetimes = [m["datetime"] for m in result.value["matches"]]
    assert datetimes == sorted(datetimes)


def test_fetch_html_embedded_jsonparse_payload_parses(monkeypatch):
    block = FIXTURE["leagues"]["EPL"]["response"]
    parts = []
    for name, key in (
        ("datesData", "dates"),
        ("teamsData", "teams"),
        ("playersData", "players"),
    ):
        literal = json.dumps(json.dumps(block[key]))[1:-1]
        parts.append(f"var {name} = JSON.parse('{literal}');")
    html = (
        "<html><head><title>Understat</title></head><body><script>\n"
        + "\n".join(parts)
        + "\n</script></body></html>"
    ).encode("utf-8")
    result = _fetch_ok(
        monkeypatch, "EPL", body=html, headers=(("Content-Type", "text/html; charset=utf-8"),)
    )
    assert result.is_success
    assert result.value["data"]["teams"] == block["teams"]
    assert result.value["data"]["dates"] == block["dates"]
    assert result.value["data"]["players"] == []
    assert result.value["summary"]["completed_matches"] == EXPECTED["per_league"]["EPL"]["completed_matches"]


def test_fetch_json_wrapper_payload_parses(monkeypatch):
    block = FIXTURE["leagues"]["La_liga"]["response"]
    body = json.dumps({"__JSON__": json.dumps(block)}).encode("utf-8")
    result = _fetch_ok(monkeypatch, "La_liga", body=body)
    assert result.is_success
    assert result.value["data"] == block


def test_fetch_html_content_type_is_accepted(monkeypatch):
    block = FIXTURE["leagues"]["Bundesliga"]["response"]
    result = _fetch_ok(
        monkeypatch,
        "Bundesliga",
        body=json.dumps(block).encode("utf-8"),
        headers=(("Content-Type", "text/html; charset=utf-8"),),
    )
    assert result.is_success


# ---------------------------------------------------------------------------
# fetch: validation rejections (upstream data fails the mirror invariants)
# ---------------------------------------------------------------------------


def _rejected(monkeypatch, league: str, mutate, *, reason: str, baseline=None):
    """Fetch a deep-copied mutated response; assert the fixed rejection."""
    block = copy.deepcopy(FIXTURE["leagues"][league]["response"])
    mutate(block)
    body = json.dumps(block).encode("utf-8")
    calls = _install_transport(monkeypatch, _ok_responder(league, body=body))
    result = understat.fetch_league_snapshot(
        league,
        SEASON,
        FIXTURE["leagues"][league]["baseline"] if baseline is None else baseline,
        now=NOW,
        budget=_budget(),
        allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == reason
    assert result.value is None  # failure never promotes partial data
    assert len(calls) == 1
    # the accepted response bytes survive as evidence
    assert result.evidence and result.evidence[0].raw_bytes == body
    return result


def test_reject_missing_xg_on_completed_match(monkeypatch):
    _rejected(
        monkeypatch, "EPL",
        lambda b: b["dates"][0]["xG"].__setitem__("h", None),
        reason=understat.REASON_INVALID_NUMBER,
    )


@pytest.mark.parametrize("value", ["-0.5", "NaN", "Infinity", True, "abc"])
def test_reject_bad_xg_values(monkeypatch, value):
    _rejected(
        monkeypatch, "EPL",
        lambda b: b["dates"][0]["xG"].__setitem__("h", value),
        reason=understat.REASON_INVALID_NUMBER,
    )


def test_reject_non_integral_goals(monkeypatch):
    _rejected(
        monkeypatch, "EPL",
        lambda b: b["dates"][0]["goals"].__setitem__("h", "1.5"),
        reason=understat.REASON_INVALID_NUMBER,
    )


def test_reject_completed_match_disappeared(monkeypatch):
    def drop_match_one(block):
        # the match and its two history rows vanish together (a consistent
        # upstream rebuild): the baseline still completed it -> rejection
        block["dates"] = [row for row in block["dates"] if row["id"] != "1"]
        block["teams"]["1"]["history"] = [
            h for h in block["teams"]["1"]["history"] if h["date"] != "2026-08-15 12:00:00"
        ]
        block["teams"]["2"]["history"] = [
            h for h in block["teams"]["2"]["history"] if h["date"] != "2026-08-15 12:00:00"
        ]

    _rejected(monkeypatch, "EPL", drop_match_one, reason=understat.REASON_COMPLETED_MATCH_MISSING)


def test_reject_future_completed_match(monkeypatch):
    _rejected(
        monkeypatch, "EPL",
        lambda b: b["dates"][3].__setitem__("datetime", "2026-11-10 19:00:00"),
        reason=understat.REASON_FUTURE_COMPLETED_MATCH,
    )


def test_reject_completed_match_before_season_window(monkeypatch):
    _rejected(
        monkeypatch, "EPL",
        lambda b: b["dates"][0].__setitem__("datetime", "2026-05-01 12:00:00"),
        reason=understat.REASON_MATCH_OUTSIDE_SEASON,
    )


def test_reject_team_identity_change(monkeypatch):
    def swap_six_for_seven(block):
        block["teams"]["7"] = {"title": "EPL FC 7", "history": []}
        del block["teams"]["6"]
        # scheduled rows must reference known teams to stay structurally valid
        for row in block["dates"]:
            for side in ("h", "a"):
                if row[side]["id"] == "6":
                    row[side]["id"] = "5"

    _rejected(monkeypatch, "EPL", swap_six_for_seven, reason=understat.REASON_TEAM_IDENTITY_CHANGED)


def test_reject_fixture_identity_change(monkeypatch):
    def swap_scheduled_sides(block):
        row = next(r for r in block["dates"] if r["id"] == "3")
        row["h"], row["a"] = row["a"], row["h"]

    _rejected(monkeypatch, "EPL", swap_scheduled_sides, reason=understat.REASON_FIXTURE_IDENTITY_CHANGED)


def test_reject_duplicate_match_id(monkeypatch):
    _rejected(
        monkeypatch, "EPL",
        lambda b: b["dates"].append(copy.deepcopy(b["dates"][0])),
        reason=understat.REASON_DUPLICATE_MATCH_ID,
    )


def test_reject_duplicate_team_history(monkeypatch):
    _rejected(
        monkeypatch, "EPL",
        lambda b: b["teams"]["1"]["history"].append(
            copy.deepcopy(b["teams"]["1"]["history"][0])
        ),
        reason=understat.REASON_DUPLICATE_TEAM_HISTORY,
    )


def test_reject_history_count_mismatch(monkeypatch):
    _rejected(
        monkeypatch, "EPL",
        lambda b: b["teams"]["1"]["history"].pop(),
        reason=understat.REASON_HISTORY_COUNT_MISMATCH,
    )


def test_reject_history_value_disagreement(monkeypatch):
    def mutate_away_xg(block):
        entry = block["teams"]["2"]["history"][0]
        assert entry["h_a"] == "a"
        entry["xG"] = 0.9  # match row says 0.5

    _rejected(monkeypatch, "EPL", mutate_away_xg, reason=understat.REASON_HISTORY_INCONSISTENT)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.__setitem__("dates", []),
        lambda b: b.__setitem__("teams", {}),
        lambda b: b.pop("players"),
        lambda b: b["dates"][0].pop("id"),
        lambda b: b["dates"][0].__setitem__("datetime", "2026/08/15 12:00"),
        lambda b: b["teams"]["1"].pop("history"),
        lambda b: b["dates"][0]["h"].__setitem__("id", "999"),
    ],
    ids=[
        "empty-dates", "empty-teams", "missing-players", "missing-id",
        "bad-datetime", "missing-history", "unknown-team-reference",
    ],
)
def test_reject_malformed_payload_structure(monkeypatch, mutate):
    _rejected(monkeypatch, "EPL", mutate, reason=understat.REASON_PAYLOAD_STRUCTURE_INVALID)


def test_scheduled_future_matches_are_kept_out_of_completed(monkeypatch):
    # already covered by the happy path: ids "3"/"12" are scheduled and in
    # the future relative to NOW; assert explicitly for the requirement.
    result = _fetch_ok(monkeypatch, "Ligue_1")
    scheduled = [m for m in result.value["matches"] if not m["is_result"]]
    assert {m["id"] for m in scheduled} == {"3", "12"}
    assert all(m["datetime"] > "2026-11-03" for m in scheduled)
    assert result.value["summary"]["scheduled_matches"] == 2
    assert result.value["summary"]["completed_matches"] == 4


def test_broken_baseline_fails_offline_before_any_network(monkeypatch):
    broken = copy.deepcopy(FIXTURE["leagues"]["EPL"]["baseline"])
    broken["dates"][0]["xG"]["h"] = None
    calls = _install_transport(
        monkeypatch, lambda request, timeout: pytest.fail("network must not be touched")
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, broken, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.INSUFFICIENT_DATA
    assert result.reason_code == understat.REASON_BASELINE_INVALID
    assert result.value is None
    assert calls == []


# ---------------------------------------------------------------------------
# fetch: permission, budget, transport and framing
# ---------------------------------------------------------------------------


def test_unsupported_league_fails_without_network(monkeypatch):
    calls = _install_transport(
        monkeypatch, lambda request, timeout: pytest.fail("network must not be touched")
    )
    result = understat.fetch_league_snapshot(
        "NBA", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UNSUPPORTED
    assert result.reason_code == understat.REASON_LEAGUE_UNSUPPORTED
    assert calls == []


def test_fetch_without_permission_never_touches_network(monkeypatch):
    calls = _install_transport(
        monkeypatch, lambda request, timeout: pytest.fail("network must not be touched")
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=False,
    )
    assert result.status is contracts.AdapterStatus.NETWORK_DENIED
    assert result.reason_code == contracts.REASON_PUBLIC_FETCH_NOT_ALLOWED
    assert result.value is None
    assert calls == []


def test_fetch_rejects_non_bool_permission(monkeypatch):
    _install_transport(
        monkeypatch, lambda request, timeout: pytest.fail("network must not be touched")
    )
    with pytest.raises(ValueError, match="allow_public_fetch_must_be_exact_bool"):
        understat.fetch_league_snapshot(
            "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=1,
        )


@pytest.mark.parametrize(
    ("league", "season", "now"),
    [
        (123, SEASON, NOW),
        ("EPL", "20x6", NOW),
        ("EPL", SEASON, NOW.replace(tzinfo=None)),
    ],
)
def test_fetch_rejects_invalid_input_types(monkeypatch, league, season, now):
    _install_transport(
        monkeypatch, lambda request, timeout: pytest.fail("network must not be touched")
    )
    with pytest.raises(ValueError):
        understat.fetch_league_snapshot(
            league, season, None, now=now, budget=_budget(), allow_public_fetch=True,
        )


def test_fetch_rejects_non_budget_object(monkeypatch):
    with pytest.raises(ValueError, match="budget_invalid"):
        understat.fetch_league_snapshot(
            "EPL", SEASON, None, now=NOW, budget=object(), allow_public_fetch=True,
        )


def test_requests_exhausted_is_distinct_and_precedes_network(monkeypatch):
    budget = _budget(max_requests=1)
    budget.charge_request()  # another adapter of the round already spent it
    calls = _install_transport(
        monkeypatch, lambda request, timeout: pytest.fail("network must not be touched")
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=budget, allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.BUDGET_EXHAUSTED
    assert result.reason_code == contracts.REASON_REQUESTS_EXHAUSTED
    assert calls == []


def test_deadline_exhaustion_is_its_own_status(monkeypatch):
    budget = _budget(deadline_monotonic=time.monotonic() - 1.0)
    calls = _install_transport(
        monkeypatch, lambda request, timeout: pytest.fail("network must not be touched")
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=budget, allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.DEADLINE_EXCEEDED
    assert result.reason_code == contracts.REASON_DEADLINE_EXCEEDED
    assert calls == []


def test_shared_response_byte_exhaustion_is_distinguished(monkeypatch):
    # 200 of 256 shared bytes already charged by earlier round fetches; the
    # per-response cap is untouched, so a 100-byte body cannot be served.
    budget = _budget(max_response_bytes=256, max_total_response_bytes=256)
    budget.charge_response_bytes(200)
    body = b"x" * 100
    calls = _install_transport(monkeypatch, _ok_responder("EPL", body=body))
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=budget, allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.BUDGET_EXHAUSTED
    assert result.reason_code == contracts.REASON_RESPONSE_BYTES_EXHAUSTED
    assert len(calls) == 1  # the attempt happened; the bounded read refused it


def test_genuinely_too_large_response_is_an_upstream_error(monkeypatch):
    budget = _budget(max_response_bytes=64)
    body = b"x" * 100
    calls = _install_transport(monkeypatch, _ok_responder("EPL", body=body))
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=budget, allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "public_response_too_large"
    assert len(calls) == 1


def test_truncated_body_rejected(monkeypatch):
    body = b"x" * 50
    calls = _install_transport(
        monkeypatch,
        _ok_responder(
            "EPL",
            body=body,
            headers=(("Content-Type", "application/json"), ("Content-Length", "100")),
        ),
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "public_response_incomplete"
    assert result.evidence == ()  # partial bytes are never accepted
    assert len(calls) == 1


def test_bad_framing_rejected(monkeypatch):
    body = b"{}"
    calls = _install_transport(
        monkeypatch,
        _ok_responder(
            "EPL",
            body=body,
            headers=(
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Content-Length", "2"),
            ),
        ),
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "public_response_framing_invalid"


def test_empty_body_rejected(monkeypatch):
    calls = _install_transport(monkeypatch, _ok_responder("EPL", body=b""))
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "public_response_empty"


def test_http_error_status_rejected(monkeypatch):
    calls = _install_transport(
        monkeypatch,
        _ok_responder("EPL", body=b"service unavailable", status_line=b"HTTP/1.1 503 Service Unavailable"),
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == understat.REASON_UPSTREAM_HTTP_ERROR


def test_httperror_exception_maps_to_fixed_code(monkeypatch):
    def raise_http_error(request, timeout):
        raise urllib.error.HTTPError("url", 503, "Service Unavailable", None, None)

    monkeypatch.setattr(understat, "_urlopen", raise_http_error)
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == understat.REASON_UPSTREAM_HTTP_ERROR


def test_urlerror_maps_to_upstream_unreachable(monkeypatch):
    def raise_url_error(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(understat, "_urlopen", raise_url_error)
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == understat.REASON_UPSTREAM_UNREACHABLE


def test_redirect_off_origin_rejected(monkeypatch):
    calls = _install_transport(
        monkeypatch,
        _ok_responder("EPL", url="https://mirror.example.com/getLeagueData/EPL/" + SEASON),
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == understat.REASON_UPSTREAM_ORIGIN_INVALID


def test_unexpected_content_type_rejected(monkeypatch):
    calls = _install_transport(
        monkeypatch,
        _ok_responder("EPL", headers=(("Content-Type", "application/xml"),)),
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, None, now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == understat.REASON_UPSTREAM_CONTENT_TYPE_INVALID


def test_parse_failure_keeps_evidence(monkeypatch):
    body = b"<html><body>plain error page, no embedded payloads</body></html>"
    calls = _install_transport(
        monkeypatch, _ok_responder("EPL", body=body, headers=(("Content-Type", "text/html"),))
    )
    result = understat.fetch_league_snapshot(
        "EPL", SEASON, FIXTURE["leagues"]["EPL"]["baseline"],
        now=NOW, budget=_budget(), allow_public_fetch=True,
    )
    assert result.status is contracts.AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == understat.REASON_PAYLOAD_PARSE_FAILED
    assert result.value is None
    assert result.evidence[0].raw_bytes == body  # evidence preserved on parse failure


# ---------------------------------------------------------------------------
# fetch: zero file writes
# ---------------------------------------------------------------------------


def test_fetch_and_assemble_write_no_files(monkeypatch, tmp_path):
    _forbid_writes(monkeypatch)
    results = _fetch_all(monkeypatch)
    assembled = understat.assemble_xg_snapshot(
        results, [_prior_block(league) for league in LEAGUES]
    )
    assert assembled.is_success
    assert list(tmp_path.iterdir()) == []  # nothing was ever written


# ---------------------------------------------------------------------------
# assemble: success shape and prior-block preservation
# ---------------------------------------------------------------------------


def test_assemble_full_snapshot(monkeypatch):
    results = _fetch_all(monkeypatch)
    priors = [_prior_block(league) for league in LEAGUES]
    result = understat.assemble_xg_snapshot(results, priors)
    assert result.is_success
    snapshot = result.value

    expected_blocks = {f"{league}_{SEASON}" for league in LEAGUES} | {
        f"{league}_{PRIOR_SEASON}" for league in LEAGUES
    }
    assert set(snapshot) == expected_blocks | {"_provenance"}

    for league, fetch_result in zip(LEAGUES, results):
        # current-season blocks embed the validated raw data BY REFERENCE
        assert snapshot[f"{league}_{SEASON}"] is fetch_result.value["data"]
        assert snapshot[f"{league}_{SEASON}"] == FIXTURE["leagues"][league]["response"]
        # prior blocks are carried forward unchanged, by reference
        assert snapshot[f"{league}_{PRIOR_SEASON}"] == FIXTURE["leagues"][league]["prior"]

    provenance = snapshot["_provenance"]
    assert provenance["schema"] == understat.SNAPSHOT_SCHEMA
    assert provenance["status"] == "complete"
    assert provenance["source_kind"] == "understat"
    assert provenance["season"] == SEASON
    assert provenance["paper_only"] is True
    assert provenance["report_only"] is True
    assert provenance["readonly"] is True
    for league in LEAGUES:
        meta = provenance["leagues"][league]
        expected = EXPECTED["per_league"][league]
        assert meta["completed_matches"] == expected["completed_matches"]
        assert meta["scheduled_matches"] == expected["scheduled_matches"]
        assert meta["latest_match_date"] == expected["latest_match_date"]
        assert meta["source_kind"] == "understat"
    completeness = provenance["completeness"]
    assert completeness["expected_leagues"] == 5
    assert completeness["present_leagues"] == 5
    assert completeness["all_present"] is True
    assert completeness["total_completed_matches"] == EXPECTED["total_completed_matches"]
    assert completeness["total_scheduled_matches"] == EXPECTED["total_scheduled_matches"]
    assert completeness["latest_match_date"] == EXPECTED["latest_match_date"]
    assert completeness["prior_block_count"] == 5
    assert provenance["preserved_historical_seasons"] == [PRIOR_SEASON]


def test_assemble_preserves_prior_blocks_under_original_digest(monkeypatch):
    results = _fetch_all(monkeypatch)
    priors = {league: _prior_block(league) for league in LEAGUES}
    snapshot = understat.assemble_xg_snapshot(results, list(priors.values())).value
    for record in snapshot["_provenance"]["prior_block_provenance"]:
        prior = priors[record["league"]]
        # the ORIGINAL digest is recorded verbatim, never recalculated
        assert record["content_sha256"] == prior.content_sha256
        assert record["source_reference"] == prior.source_reference
        assert record["refetched"] is False
        assert record["preserved"] is True
        # byte-exact preservation: same object, same content, untouched
        assert snapshot[record["league"] + "_" + record["season"]] is prior.data
        assert snapshot[record["league"] + "_" + record["season"]] == prior.data


def test_assemble_passes_fetch_evidence_through(monkeypatch):
    results = _fetch_all(monkeypatch)
    expected_digests = [
        evidence.content_sha256 for result in results for evidence in result.evidence
    ]
    assembled = understat.assemble_xg_snapshot(results, [_prior_block("EPL")])
    assert [e.content_sha256 for e in assembled.evidence] == expected_digests


def test_assembled_snapshot_feeds_the_existing_xg_consumer(monkeypatch):
    from polymarket_alpha_lab.papersim import xg

    results = _fetch_all(monkeypatch)
    snapshot = understat.assemble_xg_snapshot(
        results, [_prior_block(league) for league in LEAGUES]
    ).value
    rows = xg.team_rows(snapshot, "EPL", SEASON)
    assert set(rows) == {"1", "2", "3", "4", "5", "6"}
    fit = xg.league_goal_fit(snapshot, "EPL", SEASON)
    assert fit.home_rate > 0
    prior_rows = xg.team_rows(snapshot, "EPL", PRIOR_SEASON)
    assert prior_rows["1"].title == "EPL FC 1"


def test_assemble_is_pure_no_network_no_writes(monkeypatch, tmp_path):
    results = _fetch_all(monkeypatch)  # fetched before the guards go up
    monkeypatch.setattr(
        understat, "_urlopen", lambda request, timeout: pytest.fail("no network in assembly")
    )
    _forbid_writes(monkeypatch)
    result = understat.assemble_xg_snapshot(results, [_prior_block("EPL")])
    assert result.is_success
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# assemble: rejections (partial data is never promoted)
# ---------------------------------------------------------------------------


def _assemble_failure(monkeypatch, results, priors=(), *, reason):
    result = understat.assemble_xg_snapshot(results, priors)
    assert result.status is contracts.AdapterStatus.INSUFFICIENT_DATA
    assert result.reason_code == reason
    assert result.value is None
    return result


def test_assemble_rejects_failure_blocks(monkeypatch):
    results = _fetch_all(monkeypatch)
    broken = contracts.AdapterResult.failure(
        contracts.AdapterStatus.UPSTREAM_ERROR, understat.REASON_PAYLOAD_PARSE_FAILED
    )
    _assemble_failure(
        monkeypatch, [results[0], broken, *results[2:]],
        reason=understat.REASON_VALIDATED_BLOCK_NOT_SUCCESS,
    )


def test_assemble_rejects_foreign_source_blocks(monkeypatch):
    results = _fetch_all(monkeypatch)
    impostor = copy.deepcopy(results[1].value)
    impostor["source_kind"] = "fotmob"  # a FotMob-shaped block must never mix in
    wrapped = contracts.AdapterResult.success(impostor, results[1].evidence)
    _assemble_failure(
        monkeypatch, [results[0], wrapped, *results[2:]],
        reason=understat.REASON_VALIDATED_BLOCK_INVALID,
    )


def test_assemble_rejects_duplicate_league(monkeypatch):
    results = _fetch_all(monkeypatch)
    _assemble_failure(
        monkeypatch, [results[0], results[0], *results[2:]],
        reason=understat.REASON_DUPLICATE_LEAGUE_BLOCK,
    )


def test_assemble_rejects_season_mismatch(monkeypatch):
    results = _fetch_all(monkeypatch)
    drifted = copy.deepcopy(results[0].value)
    drifted["season"] = "2027"
    wrapped = contracts.AdapterResult.success(drifted, results[0].evidence)
    _assemble_failure(
        monkeypatch, [wrapped, *results[1:]], reason=understat.REASON_SEASON_MISMATCH
    )


def test_assemble_rejects_incomplete_league_coverage(monkeypatch):
    results = _fetch_all(monkeypatch)
    _assemble_failure(
        monkeypatch, results[:-1], reason=understat.REASON_INCOMPLETE_LEAGUE_COVERAGE
    )


def test_assemble_rejects_empty_inputs(monkeypatch):
    _assemble_failure(monkeypatch, [], reason=understat.REASON_INCOMPLETE_LEAGUE_COVERAGE)


def test_assemble_rejects_wrong_block_type(monkeypatch):
    with pytest.raises(ValueError, match="validated_block_invalid_type"):
        understat.assemble_xg_snapshot([{"not": "an AdapterResult"}])


def test_assemble_rejects_prior_block_for_current_season(monkeypatch):
    results = _fetch_all(monkeypatch)
    conflicting = _prior_block("EPL", season=SEASON)
    _assemble_failure(
        monkeypatch, results, [conflicting], reason=understat.REASON_PRIOR_BLOCK_CONFLICT
    )


def test_assemble_rejects_duplicate_prior_blocks(monkeypatch):
    results = _fetch_all(monkeypatch)
    _assemble_failure(
        monkeypatch,
        results,
        [_prior_block("EPL"), _prior_block("EPL")],
        reason=understat.REASON_DUPLICATE_PRIOR_BLOCK,
    )


def test_assemble_rejects_prior_block_outside_understat_scope(monkeypatch):
    results = _fetch_all(monkeypatch)
    foreign = understat.UnderstatPriorBlock(
        league="Championship",
        season=PRIOR_SEASON,
        data=copy.deepcopy(FIXTURE["leagues"]["EPL"]["prior"]),
        content_sha256=hashlib.sha256(b"synthetic").hexdigest(),
        source_reference="readonly-file:fotmob_hist.json#Championship_2025",
    )
    _assemble_failure(monkeypatch, results, [foreign], reason=understat.REASON_PRIOR_BLOCK_INVALID)


def test_assemble_rejects_empty_prior_block(monkeypatch):
    results = _fetch_all(monkeypatch)
    hollow = understat.UnderstatPriorBlock(
        league="EPL",
        season=PRIOR_SEASON,
        data={"teams": {}, "players": [], "dates": []},
        content_sha256=hashlib.sha256(b"{}").hexdigest(),
        source_reference="readonly-file:empty.json#EPL_2025",
    )
    _assemble_failure(monkeypatch, results, [hollow], reason=understat.REASON_PRIOR_BLOCK_INVALID)


def test_prior_block_rejects_malformed_constructor_input():
    with pytest.raises(ValueError):
        understat.UnderstatPriorBlock(
            league="EPL", season="20x5", data={}, content_sha256="0" * 64,
            source_reference="readonly-file:x.json",
        )
    with pytest.raises(ValueError):
        understat.UnderstatPriorBlock(
            league="EPL", season=PRIOR_SEASON, data={}, content_sha256="not-hex",
            source_reference="readonly-file:x.json",
        )
