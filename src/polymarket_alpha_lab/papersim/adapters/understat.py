"""M2 A3 Understat adapter: bounded public league fetch + pure xG snapshot assembly.

Single responsibility (M2 plan section 2.4, FX-15 A3): fetch one
league/season block from Understat's public league-data endpoint through the
shared A0 budget/permission contracts, validate it with the recovery-mirror
invariants, and assemble validated blocks plus preserved historical blocks
into the ``{"<League>_<season>": {...}}`` wire snapshot consumed by
:mod:`polymarket_alpha_lab.papersim.xg`.

Canonical lineage: the server recovery mirror
``docs/paper-sim/server-mirror/tools/understat_refresh.py`` (validated
2026-11) plus its mirror test. This module keeps the mirror's verified
validation patterns:

- team identity continuity against a caller-provided baseline block, match
  ID uniqueness, and fixture identity stability for IDs seen in both;
- completed matches may not disappear without explanation (baseline
  completed IDs must remain completed);
- completed matches and per-team histories must correspond exactly in both
  directions (2 history rows per completed match, one per side, values in
  agreement within the mirror tolerances);
- xG and goals must be finite and non-negative; ``0`` is a valid value and
  stays distinct from missing (missing is ``None``);
- historical season blocks are carried forward under their ORIGINAL digest
  (``refetched: False``), never silently recalculated;
- completeness metrics, latest completed-match date and per-league source
  information ship inside the snapshot provenance;
- a failure never promotes partial data: failed fetches keep their raw
  evidence but carry no value, and assembly refuses non-success blocks.

The following mirror behaviors are deliberately NOT migrated (M2 plan
section 2.4 "迁移时去掉"):

- fixed round numbers, the fixed ``2026-11-01`` target day, fixed per-league
  "Sunday counts" and fixed team-count constants (identity continuity now
  comes from the baseline argument, not baked-in league sizes);
- file output, log appending, symlink/pointer publishing and temporary-file
  staging (this module performs ZERO file writes);
- the script's own DNS recovery, DoH fallback, IP pinning and the 20 MB
  curl read path (transport is one bounded ``read_public_body`` read).

Boundaries: ``paper_only``/``report_only``/``readonly``; no database, no
cache files, no JSONL, no credentials. The source identity is FIXED to
``"understat"``: when Understat is unavailable the adapter fails with
evidence - it never silently falls back to FotMob or any other source, and
snapshot provenance keeps cross-source scale differences visible. M2's
"latest valid input" selection reads validated snapshots and digests from
PostgreSQL (owned by later nodes); this module only provides the fetch and
the pure assembly functions, and old file pointers remain explicit,
read-only historical inputs via :class:`UnderstatPriorBlock`.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from polymarket_alpha_lab import public_http
from polymarket_alpha_lab.papersim.adapters.contracts import (
    AdapterResult,
    AdapterStatus,
    BudgetExhaustedError,
    FetchBudget,
    FetchEvidence,
    FetchPermissionError,
    PAPER_ONLY,
    READONLY,
    REASON_DEADLINE_EXCEEDED,
    REASON_PUBLIC_FETCH_NOT_ALLOWED,
    REASON_RESPONSE_BYTES_EXHAUSTED,
    REPORT_ONLY,
    require_public_fetch,
)

__all__ = [
    "PAPER_ONLY",
    "READONLY",
    "REPORT_ONLY",
    "SOURCE_KIND",
    "UNDERSTAT_LEAGUES",
    "UnderstatPriorBlock",
    "assemble_xg_snapshot",
    "fetch_league_snapshot",
    "LEAGUE_BLOCK_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "REASON_LEAGUE_UNSUPPORTED",
    "REASON_BASELINE_INVALID",
    "REASON_UPSTREAM_HTTP_ERROR",
    "REASON_UPSTREAM_UNREACHABLE",
    "REASON_UPSTREAM_ORIGIN_INVALID",
    "REASON_UPSTREAM_CONTENT_TYPE_INVALID",
    "REASON_PAYLOAD_PARSE_FAILED",
    "REASON_PAYLOAD_STRUCTURE_INVALID",
    "REASON_TEAM_IDENTITY_CHANGED",
    "REASON_FIXTURE_IDENTITY_CHANGED",
    "REASON_DUPLICATE_MATCH_ID",
    "REASON_COMPLETED_MATCH_MISSING",
    "REASON_FUTURE_COMPLETED_MATCH",
    "REASON_MATCH_OUTSIDE_SEASON",
    "REASON_DUPLICATE_TEAM_HISTORY",
    "REASON_HISTORY_COUNT_MISMATCH",
    "REASON_HISTORY_INCONSISTENT",
    "REASON_INVALID_NUMBER",
    "REASON_VALIDATED_BLOCK_NOT_SUCCESS",
    "REASON_VALIDATED_BLOCK_INVALID",
    "REASON_DUPLICATE_LEAGUE_BLOCK",
    "REASON_SEASON_MISMATCH",
    "REASON_INCOMPLETE_LEAGUE_COVERAGE",
    "REASON_PRIOR_BLOCK_INVALID",
    "REASON_PRIOR_BLOCK_CONFLICT",
    "REASON_DUPLICATE_PRIOR_BLOCK",
]

#: Fixed source identity. A source switch is a structured, reviewable event;
#: this adapter never emits or accepts another source's blocks.
SOURCE_KIND = "understat"

#: Closed supported-league set (Understat Big-5; M2 plan A3 scope).
UNDERSTAT_LEAGUES = ("EPL", "La_liga", "Serie_A", "Bundesliga", "Ligue_1")

UNDERSTAT_ORIGIN = "https://understat.com"

#: Schema tags for the fetch value envelope and the assembled snapshot.
LEAGUE_BLOCK_SCHEMA = "papersim-m2-understat-league-v1"
SNAPSHOT_SCHEMA = "papersim-m2-understat-snapshot-v1"

# A completed match must fall on/after July 1 of the season year (mirror's
# current-season window; the season string itself stays a parameter).
_SEASON_START_MONTH = 7
_MATCH_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Mirror tolerances for fixture-vs-history agreement (frozen; do not tune).
_FIT_REL_TOL = 1e-5
_FIT_ABS_TOL = 1e-4

_SEASON_RE = re.compile(r"[0-9]{4}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

# Transport mirror of the recovery script's request identity: the AJAX
# endpoint needs the XHR header and the league-page referer to answer.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
)
_ALLOWED_CONTENT_TYPES = frozenset(
    {"application/json", "text/json", "text/html", "application/xhtml+xml", "text/plain"}
)

# Fixed reason codes beyond the shared A0 budget/permission codes.
REASON_LEAGUE_UNSUPPORTED = "league_unsupported"
REASON_BASELINE_INVALID = "baseline_invalid"
REASON_UPSTREAM_HTTP_ERROR = "upstream_http_error"
REASON_UPSTREAM_UNREACHABLE = "upstream_unreachable"
REASON_UPSTREAM_ORIGIN_INVALID = "upstream_origin_invalid"
REASON_UPSTREAM_CONTENT_TYPE_INVALID = "upstream_content_type_invalid"
REASON_PAYLOAD_PARSE_FAILED = "payload_parse_failed"
REASON_PAYLOAD_STRUCTURE_INVALID = "payload_structure_invalid"
REASON_TEAM_IDENTITY_CHANGED = "team_identity_changed"
REASON_FIXTURE_IDENTITY_CHANGED = "fixture_identity_changed"
REASON_DUPLICATE_MATCH_ID = "duplicate_match_id"
REASON_COMPLETED_MATCH_MISSING = "completed_match_missing"
REASON_FUTURE_COMPLETED_MATCH = "future_completed_match"
REASON_MATCH_OUTSIDE_SEASON = "match_outside_season"
REASON_DUPLICATE_TEAM_HISTORY = "duplicate_team_history"
REASON_HISTORY_COUNT_MISMATCH = "history_count_mismatch"
REASON_HISTORY_INCONSISTENT = "history_inconsistent"
REASON_INVALID_NUMBER = "invalid_number"
REASON_VALIDATED_BLOCK_NOT_SUCCESS = "validated_block_not_success"
REASON_VALIDATED_BLOCK_INVALID = "validated_block_invalid"
REASON_DUPLICATE_LEAGUE_BLOCK = "duplicate_league_block"
REASON_SEASON_MISMATCH = "season_mismatch"
REASON_INCOMPLETE_LEAGUE_COVERAGE = "incomplete_league_coverage"
REASON_PRIOR_BLOCK_INVALID = "prior_block_invalid"
REASON_PRIOR_BLOCK_CONFLICT = "prior_block_conflict"
REASON_DUPLICATE_PRIOR_BLOCK = "duplicate_prior_block"


class _ValidationError(ValueError):
    """Internal validation stop with a fixed reason code (never a URL/body)."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _urlopen(request: urllib.request.Request, timeout: float):
    """Module seam for offline tests: the only place the network is touched."""
    return urllib.request.urlopen(request, timeout=timeout)


# ---------------------------------------------------------------------------
# number coercion (mirror's ``nonnegative``: 0 valid, bool/NaN/Inf/negative rejected)
# ---------------------------------------------------------------------------


def _finite_nonnegative(value: object, *, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _ValidationError(REASON_INVALID_NUMBER)
    try:
        number = float(value)
    except ValueError as exc:
        raise _ValidationError(REASON_INVALID_NUMBER) from exc
    if not math.isfinite(number) or number < 0:
        raise _ValidationError(REASON_INVALID_NUMBER)
    if integer and number != int(number):
        raise _ValidationError(REASON_INVALID_NUMBER)
    return number


# ---------------------------------------------------------------------------
# payload parsing: getLeagueData JSON, __JSON__ wrapper, HTML-embedded JSON.parse
# ---------------------------------------------------------------------------

_JSON_PARSE_VAR_RE = re.compile(
    r"var\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*JSON\.parse\(\s*'((?:\\.|[^'\\])*)'\s*\)"
)
_ESCAPE_RE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)")
_SIMPLE_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "0": "\0",
    "'": "'",
    '"': '"',
    "\\": "\\",
    "/": "/",
}


def _unescape_js_string(literal: str) -> str:
    """Decode JS string escapes (``\\xHH`` hex, ``\\uHHHH`` unicode, simple)."""

    def replace(match: re.Match[str]) -> str:
        escape = match.group(1)
        if escape.startswith("x") and len(escape) == 3:
            return chr(int(escape[1:], 16))
        if escape.startswith("u") and len(escape) == 5:
            return chr(int(escape[1:], 16))
        return _SIMPLE_ESCAPES.get(escape, escape)

    return _ESCAPE_RE.sub(replace, literal)


def _load_json_string(text: str) -> object | None:
    """Parse JSON from a plain or JS-escaped string; ``None`` when unusable."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return json.loads(_unescape_js_string(text))
    except ValueError:
        return None


def _extract_embedded_league(text: str) -> dict | None:
    """Rebuild a league block from an HTML page's ``JSON.parse('...')`` vars."""
    found: dict[str, object] = {}
    for name, literal in _JSON_PARSE_VAR_RE.findall(text):
        if name in found:
            continue
        value = _load_json_string(_unescape_js_string(literal))
        if value is not None:
            found[name] = value
    if not found:
        return None
    direct = found.get("leagueData")
    if isinstance(direct, dict) and {"teams", "dates"} <= set(direct):
        return direct
    dates_payload = found.get("datesData")
    teams_payload = found.get("teamsData")
    if isinstance(dates_payload, (list, dict)) and isinstance(teams_payload, dict):
        dates = (
            list(dates_payload.values())
            if isinstance(dates_payload, dict)
            else list(dates_payload)
        )
        players_payload = found.get("playersData")
        if isinstance(players_payload, dict):
            players: list = list(players_payload.values())
        elif isinstance(players_payload, list):
            players = list(players_payload)
        else:
            players = []
        return {"teams": teams_payload, "dates": dates, "players": players}
    return None


def _parse_league_payload(raw: bytes) -> dict:
    """Parse one Understat response body into a league block dict.

    Accepted wire forms: the getLeagueData JSON object (the mirror's parse
    path), a ``{"__JSON__": "<json string>"}`` wrapper, or an HTML page with
    embedded ``JSON.parse('...')`` league payloads. Anything else is a parse
    failure; the caller keeps the raw bytes as evidence.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _ValidationError(REASON_PAYLOAD_PARSE_FAILED) from exc
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        if set(payload) == {"__JSON__"} and isinstance(payload["__JSON__"], str):
            inner = _load_json_string(payload["__JSON__"])
            if isinstance(inner, dict):
                return inner
        return payload
    embedded = _extract_embedded_league(text)
    if embedded is not None:
        return embedded
    raise _ValidationError(REASON_PAYLOAD_PARSE_FAILED)


# ---------------------------------------------------------------------------
# league-block validation (mirror invariants, hardcodes removed)
# ---------------------------------------------------------------------------


def _validate_league_payload(league: str, season: str, data: object, *, now: datetime) -> dict:
    """Validate one parsed league block and return its normalized view.

    Structure checks, match-ID uniqueness, completed-match numeric checks
    (finite, non-negative; ``0`` valid; goals integral), the season window
    and the completed/history bidirectional correspondence all run here.
    Cross-block checks against a baseline live in
    :func:`_cross_check_baseline`.
    """
    if not isinstance(data, dict):
        raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
    teams = data.get("teams")
    dates = data.get("dates")
    players = data.get("players")
    if not isinstance(teams, dict) or not teams:
        raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
    if not isinstance(dates, list) or not dates:
        raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
    if not isinstance(players, list):
        raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
    for team in teams.values():
        if (
            not isinstance(team, dict)
            or not isinstance(team.get("title"), str)
            or not isinstance(team.get("history"), list)
        ):
            raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
    team_ids = frozenset(str(team_id) for team_id in teams)

    match_ids: set[str] = set()
    rows: list[dict] = []
    for row in dates:
        if not isinstance(row, dict) or row.get("id") is None:
            raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
        match_id = str(row["id"])
        if match_id in match_ids:
            raise _ValidationError(REASON_DUPLICATE_MATCH_ID)
        match_ids.add(match_id)
        raw_datetime = row.get("datetime")
        if not isinstance(raw_datetime, str):
            raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
        try:
            parsed = datetime.strptime(raw_datetime, _MATCH_TIME_FORMAT)
        except ValueError as exc:
            raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID) from exc
        side_ids: dict[str, str] = {}
        for side in ("h", "a"):
            side_ref = row.get(side)
            if not isinstance(side_ref, dict) or side_ref.get("id") is None:
                raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
            side_id = str(side_ref["id"])
            if side_id not in team_ids:
                raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
            side_ids[side] = side_id
        rows.append(
            {
                "id": match_id,
                "datetime": raw_datetime,
                "dt": parsed,
                "h": side_ids["h"],
                "a": side_ids["a"],
                "row": row,
            }
        )

    histories: dict[tuple[str, str, str], dict] = {}
    for raw_team_id, team in teams.items():
        team_id = str(raw_team_id)
        for entry in team["history"]:
            if not isinstance(entry, dict):
                raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
            home_away = entry.get("h_a")
            entry_date = entry.get("date")
            if home_away not in ("h", "a") or not isinstance(entry_date, str):
                raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
            key = (team_id, entry_date, home_away)
            if key in histories:
                raise _ValidationError(REASON_DUPLICATE_TEAM_HISTORY)
            for field in ("xG", "xGA"):
                _finite_nonnegative(entry.get(field))
            for field in ("scored", "missed"):
                _finite_nonnegative(entry.get(field), integer=True)
            histories[key] = entry

    completed = [row for row in rows if row["row"].get("isResult") is True]
    if len(histories) != 2 * len(completed):
        raise _ValidationError(REASON_HISTORY_COUNT_MISMATCH)

    season_start = datetime(int(season), _SEASON_START_MONTH, 1)
    now_naive = now.astimezone(timezone.utc).replace(tzinfo=None)
    used_histories: set[tuple[str, str, str]] = set()
    normalized: list[dict] = []
    for row in rows:
        is_result = row["row"].get("isResult") is True
        xg_values: dict[str, float] = {}
        goal_values: dict[str, int] = {}
        if is_result:
            if row["dt"] < season_start:
                raise _ValidationError(REASON_MATCH_OUTSIDE_SEASON)
            if row["dt"] > now_naive:
                raise _ValidationError(REASON_FUTURE_COMPLETED_MATCH)
            raw_xg = row["row"].get("xG")
            raw_goals = row["row"].get("goals")
            if not isinstance(raw_xg, dict) or not isinstance(raw_goals, dict):
                raise _ValidationError(REASON_PAYLOAD_STRUCTURE_INVALID)
            for side in ("h", "a"):
                xg_values[side] = _finite_nonnegative(raw_xg.get(side))
                goal_values[side] = int(
                    _finite_nonnegative(raw_goals.get(side), integer=True)
                )
            for side, other in (("h", "a"), ("a", "h")):
                key = (row[side], row["datetime"], side)
                entry = histories.get(key)
                if entry is None or key in used_histories:
                    raise _ValidationError(REASON_HISTORY_INCONSISTENT)
                used_histories.add(key)
                pairs = (
                    ("xG", xg_values[side]),
                    ("xGA", xg_values[other]),
                    ("scored", float(goal_values[side])),
                    ("missed", float(goal_values[other])),
                )
                for field, expected in pairs:
                    actual = _finite_nonnegative(
                        entry.get(field), integer=field in ("scored", "missed")
                    )
                    if not math.isclose(
                        actual,
                        expected,
                        rel_tol=_FIT_REL_TOL,
                        abs_tol=_FIT_ABS_TOL,
                    ):
                        raise _ValidationError(REASON_HISTORY_INCONSISTENT)
        normalized.append(
            {
                "id": row["id"],
                "datetime": row["datetime"],
                "home_team_id": row["h"],
                "away_team_id": row["a"],
                "is_result": is_result,
                # Completed: validated numbers. Scheduled: missing stays None
                # (never 0), regardless of upstream junk values.
                "home_xg": xg_values.get("h"),
                "away_xg": xg_values.get("a"),
                "home_goals": goal_values.get("h"),
                "away_goals": goal_values.get("a"),
            }
        )
    normalized.sort(key=lambda match: (match["datetime"], match["id"]))
    completed_dates = [row["datetime"] for row in completed]
    return {
        "league": league,
        "season": season,
        "team_ids": sorted(team_ids),
        "matches": normalized,
        "completed_matches": len(completed),
        "scheduled_matches": len(rows) - len(completed),
        "latest_match_date": max(completed_dates) if completed_dates else None,
    }


def _cross_check_baseline(fresh: dict, baseline: dict) -> None:
    """Mirror's cross-block rules: identity continuity, no silent disappearances."""
    if set(fresh["team_ids"]) != set(baseline["team_ids"]):
        raise _ValidationError(REASON_TEAM_IDENTITY_CHANGED)
    fresh_by_id = {match["id"]: match for match in fresh["matches"]}
    for baseline_match in baseline["matches"]:
        current = fresh_by_id.get(baseline_match["id"])
        if current is None:
            continue
        if (
            current["home_team_id"] != baseline_match["home_team_id"]
            or current["away_team_id"] != baseline_match["away_team_id"]
        ):
            raise _ValidationError(REASON_FIXTURE_IDENTITY_CHANGED)
    fresh_completed = {
        match["id"] for match in fresh["matches"] if match["is_result"]
    }
    baseline_completed = {
        match["id"] for match in baseline["matches"] if match["is_result"]
    }
    if not baseline_completed <= fresh_completed:
        raise _ValidationError(REASON_COMPLETED_MATCH_MISSING)


# ---------------------------------------------------------------------------
# transport (origin/status/content-type checks + one bounded read)
# ---------------------------------------------------------------------------


def _league_source_reference(league: str, season: str) -> str:
    return f"understat.com/getLeagueData/{league}/{season}"


def _open_league_response(league: str, season: str, timeout: float):
    request = urllib.request.Request(
        UNDERSTAT_ORIGIN + "/getLeagueData/" + league + "/" + season,
        method="GET",
        headers={
            "Accept": "application/json, text/html",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": UNDERSTAT_ORIGIN + "/league/" + league + "/" + season,
            "User-Agent": _USER_AGENT,
        },
    )
    return _urlopen(request, timeout=timeout)


def _verify_response_origin(response) -> None:
    """Status, final-origin and content-type checks before any body read."""
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    if status != 200:
        raise _ValidationError(REASON_UPSTREAM_HTTP_ERROR)
    final_url = response.geturl()
    if not isinstance(final_url, str):
        raise _ValidationError(REASON_UPSTREAM_ORIGIN_INVALID)
    parsed = urllib.parse.urlsplit(final_url)
    if parsed.scheme != "https" or parsed.hostname != "understat.com":
        raise _ValidationError(REASON_UPSTREAM_ORIGIN_INVALID)
    raw_type = response.headers.get("Content-Type")
    if not isinstance(raw_type, str):
        raise _ValidationError(REASON_UPSTREAM_CONTENT_TYPE_INVALID)
    media_type = raw_type.split(";", 1)[0].strip().lower()
    if media_type not in _ALLOWED_CONTENT_TYPES:
        raise _ValidationError(REASON_UPSTREAM_CONTENT_TYPE_INVALID)


def _budget_failure(reason_code: str) -> AdapterResult:
    if reason_code == REASON_DEADLINE_EXCEEDED:
        return AdapterResult.failure(AdapterStatus.DEADLINE_EXCEEDED, reason_code)
    return AdapterResult.failure(AdapterStatus.BUDGET_EXHAUSTED, reason_code)


def _require_fetch_inputs(league: object, season: object, now: object, budget: object) -> None:
    if type(league) is not str or not league:
        raise ValueError("league_invalid")
    if type(season) is not str or _SEASON_RE.fullmatch(season) is None:
        raise ValueError("season_invalid")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now_invalid")
    if not isinstance(budget, FetchBudget):
        raise ValueError("budget_invalid")


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def fetch_league_snapshot(
    league: str,
    season: str,
    baseline: dict | None,
    *,
    now: datetime,
    budget: FetchBudget,
    allow_public_fetch: bool = False,
    register_response=None,
) -> AdapterResult:
    """Fetch and validate one Understat league/season block.

    ``baseline`` is the previously validated RAW league block (the same
    ``{"teams": ..., "dates": ..., "players": ...}`` wire shape this adapter
    returns inside ``value["data"]``), or ``None`` for a first fetch with no
    prior valid input. The baseline itself must pass the same internal
    validation; a broken baseline fails offline with ``baseline_invalid``
    before any network work. ``now`` is the caller's clock (aware datetime)
    used for the completed-match season window and evidence timestamps.

    Success value: the league-block envelope (schema, fixed source identity,
    source provenance, raw validated ``data`` block, normalized ``matches``
    and a summary). Failures carry fixed reason codes and, once bytes were
    accepted, the raw response evidence - never a partial value.
    """
    _require_fetch_inputs(league, season, now, budget)
    if league not in UNDERSTAT_LEAGUES:
        return AdapterResult.failure(AdapterStatus.UNSUPPORTED, REASON_LEAGUE_UNSUPPORTED)
    baseline_view: dict | None = None
    if baseline is not None:
        if not isinstance(baseline, dict):
            raise ValueError("baseline_invalid_type")
        try:
            baseline_view = _validate_league_payload(league, season, baseline, now=now)
        except _ValidationError:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_BASELINE_INVALID
            )
    try:
        require_public_fetch(allow_public_fetch)
    except FetchPermissionError:
        return AdapterResult.failure(
            AdapterStatus.NETWORK_DENIED, REASON_PUBLIC_FETCH_NOT_ALLOWED
        )
    try:
        budget.charge_request()
        timeout = budget.effective_timeout_seconds()
        read_limit = budget.next_response_read_limit()
    except BudgetExhaustedError as exc:
        return _budget_failure(exc.reason_code)
    try:
        response = _open_league_response(league, season, timeout)
    except urllib.error.HTTPError:
        return AdapterResult.failure(AdapterStatus.UPSTREAM_ERROR, REASON_UPSTREAM_HTTP_ERROR)
    except (urllib.error.URLError, OSError):
        return AdapterResult.failure(AdapterStatus.UPSTREAM_ERROR, REASON_UPSTREAM_UNREACHABLE)
    try:
        try:
            _verify_response_origin(response)
            raw = public_http.read_public_body(response, read_limit)
            received_at = datetime.now(now.tzinfo) if register_response is not None else now
        finally:
            response.close()
    except _ValidationError as exc:
        return AdapterResult.failure(AdapterStatus.UPSTREAM_ERROR, exc.reason_code)
    except public_http.PublicBodyError as exc:
        reason = str(exc)
        if (
            reason == "public_response_too_large"
            and budget.remaining_response_bytes() < budget.max_response_bytes
        ):
            # The read cap only shrank below the configured per-response cap
            # because the SHARED round budget is out of response bytes.
            return AdapterResult.failure(
                AdapterStatus.BUDGET_EXHAUSTED, REASON_RESPONSE_BYTES_EXHAUSTED
            )
        return AdapterResult.failure(AdapterStatus.UPSTREAM_ERROR, reason)
    try:
        budget.charge_response_bytes(len(raw))
    except BudgetExhaustedError as exc:
        return _budget_failure(exc.reason_code)
    evidence = (
        FetchEvidence.from_bytes(
            source_kind=SOURCE_KIND,
            source_reference=_league_source_reference(league, season),
            fetched_at=received_at,
            raw_bytes=raw,
        ),
    )
    if register_response is not None:
        register_response(evidence[0])
    try:
        payload = _parse_league_payload(raw)
        view = _validate_league_payload(league, season, payload, now=now)
        if baseline_view is not None:
            _cross_check_baseline(view, baseline_view)
    except _ValidationError as exc:
        return AdapterResult.failure(AdapterStatus.UPSTREAM_ERROR, exc.reason_code, evidence)
    value = {
        "schema": LEAGUE_BLOCK_SCHEMA,
        "source_kind": SOURCE_KIND,
        "league": league,
        "season": season,
        "source": {
            "source_kind": SOURCE_KIND,
            "source_reference": _league_source_reference(league, season),
            "content_sha256": evidence[0].content_sha256,
            "fetched_at": received_at.isoformat(),
        },
        "data": payload,
        "matches": view["matches"],
        "summary": {
            "team_count": len(view["team_ids"]),
            "completed_matches": view["completed_matches"],
            "scheduled_matches": view["scheduled_matches"],
            "latest_match_date": view["latest_match_date"],
        },
        "paper_only": PAPER_ONLY,
        "report_only": REPORT_ONLY,
        "readonly": READONLY,
    }
    return AdapterResult.success(value, evidence)


@dataclass(frozen=True, slots=True)
class UnderstatPriorBlock:
    """A historical league block carried forward under its ORIGINAL digest.

    ``data`` is the raw wire block (same shape as ``value["data"]``) that a
    previous validated input produced; the assembler carries it BY REFERENCE
    and never recalculates or rewrites it. ``content_sha256`` is the digest
    recorded when the block's bytes were originally read (an explicit,
    read-only historical input - typically an old file pointer's digest), NOT
    a fresh digest computed here. The frozen dataclass prevents rebinding the
    fields; the dict payload itself is treated as immutable by convention.
    """

    league: str
    season: str
    data: dict
    content_sha256: str
    source_reference: str

    def __post_init__(self) -> None:
        if type(self.league) is not str or not self.league:
            raise ValueError("league_invalid")
        if type(self.season) is not str or _SEASON_RE.fullmatch(self.season) is None:
            raise ValueError("season_invalid")
        if type(self.data) is not dict:
            raise ValueError("data_invalid")
        if type(self.content_sha256) is not str or _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise ValueError("content_sha256_invalid")
        if type(self.source_reference) is not str or not self.source_reference:
            raise ValueError("source_reference_invalid")


def assemble_xg_snapshot(
    validated_blocks,
    prior_blocks=(),
) -> AdapterResult:
    """Assemble validated current-season blocks plus preserved priors.

    Pure function: no network, no database, no file writes. ``validated_blocks``
    is a sequence of SUCCESS :class:`AdapterResult` values returned by
    :func:`fetch_league_snapshot` for the SAME season; the closed Understat
    league set must be fully covered, otherwise the result is an
    ``incomplete_league_coverage`` failure - partial data is never promoted
    to a latest valid snapshot. Blocks carrying any source identity other
    than ``"understat"`` are refused (no silent cross-source mixing).
    ``prior_blocks`` is a sequence of :class:`UnderstatPriorBlock` historical
    inputs carried forward unchanged under their original digests.

    Success value: the wire snapshot ``{"<League>_<season>": block, ...}``
    (directly consumable by :mod:`polymarket_alpha_lab.papersim.xg`) plus a
    ``"_provenance"`` block with per-league source info, completeness
    metrics, the overall latest completed-match date and prior-block
    preservation records. Evidence tuples of the input blocks are passed
    through in input order.
    """
    blocks = tuple(validated_blocks)
    priors = tuple(prior_blocks)
    by_league: dict[str, dict] = {}
    evidence: list[FetchEvidence] = []
    season: str | None = None
    for result in blocks:
        if not isinstance(result, AdapterResult):
            raise ValueError("validated_block_invalid_type")
        if result.status is not AdapterStatus.SUCCESS:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_VALIDATED_BLOCK_NOT_SUCCESS
            )
        value = result.value
        if not isinstance(value, dict) or (
            value.get("schema") != LEAGUE_BLOCK_SCHEMA
            or value.get("source_kind") != SOURCE_KIND
        ):
            # Refuses foreign envelopes (e.g. a FotMob-shaped block): source
            # switches stay explicit and cross-source scales stay visible.
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_VALIDATED_BLOCK_INVALID
            )
        league = value.get("league")
        block_season = value.get("season")
        if league not in UNDERSTAT_LEAGUES or type(block_season) is not str:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_VALIDATED_BLOCK_INVALID
            )
        if league in by_league:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_DUPLICATE_LEAGUE_BLOCK
            )
        if season is None:
            season = block_season
        elif block_season != season:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_SEASON_MISMATCH
            )
        data = value.get("data")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("teams"), dict)
            or not isinstance(data.get("dates"), list)
        ):
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_VALIDATED_BLOCK_INVALID
            )
        by_league[league] = value
        evidence.extend(result.evidence)
    if len(by_league) != len(UNDERSTAT_LEAGUES):
        return AdapterResult.failure(
            AdapterStatus.INSUFFICIENT_DATA, REASON_INCOMPLETE_LEAGUE_COVERAGE
        )
    prior_slots: dict[tuple[str, str], UnderstatPriorBlock] = {}
    for prior in priors:
        if not isinstance(prior, UnderstatPriorBlock):
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_PRIOR_BLOCK_INVALID
            )
        if prior.league not in UNDERSTAT_LEAGUES:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_PRIOR_BLOCK_INVALID
            )
        teams = prior.data.get("teams")
        if not isinstance(teams, dict) or not teams:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_PRIOR_BLOCK_INVALID
            )
        if prior.season == season:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_PRIOR_BLOCK_CONFLICT
            )
        slot = (prior.league, prior.season)
        if slot in prior_slots:
            return AdapterResult.failure(
                AdapterStatus.INSUFFICIENT_DATA, REASON_DUPLICATE_PRIOR_BLOCK
            )
        prior_slots[slot] = prior

    snapshot: dict = {}
    for league in sorted(by_league):
        # By reference: the validated raw block is embedded unchanged.
        snapshot[league + "_" + season] = by_league[league]["data"]
    prior_provenance = []
    for league, prior_season in sorted(prior_slots):
        prior = prior_slots[(league, prior_season)]
        snapshot[league + "_" + prior_season] = prior.data
        prior_provenance.append(
            {
                "league": league,
                "season": prior_season,
                "content_sha256": prior.content_sha256,
                "source_reference": prior.source_reference,
                "preserved": True,
                "refetched": False,
            }
        )

    leagues_meta: dict[str, dict] = {}
    total_completed = 0
    total_scheduled = 0
    latest_match_date: str | None = None
    for league in sorted(by_league):
        value = by_league[league]
        summary = value["summary"]
        source = value["source"]
        leagues_meta[league] = {
            "team_count": summary["team_count"],
            "completed_matches": summary["completed_matches"],
            "scheduled_matches": summary["scheduled_matches"],
            "latest_match_date": summary["latest_match_date"],
            "source_kind": SOURCE_KIND,
            "source_reference": source["source_reference"],
            "content_sha256": source["content_sha256"],
            "fetched_at": source["fetched_at"],
        }
        total_completed += summary["completed_matches"]
        total_scheduled += summary["scheduled_matches"]
        league_latest = summary["latest_match_date"]
        if league_latest is not None and (
            latest_match_date is None or league_latest > latest_match_date
        ):
            latest_match_date = league_latest

    snapshot["_provenance"] = {
        "schema": SNAPSHOT_SCHEMA,
        "status": "complete",
        "source_kind": SOURCE_KIND,
        "season": season,
        "leagues": leagues_meta,
        "completeness": {
            "expected_leagues": len(UNDERSTAT_LEAGUES),
            "present_leagues": len(by_league),
            "all_present": True,
            "total_completed_matches": total_completed,
            "total_scheduled_matches": total_scheduled,
            "latest_match_date": latest_match_date,
            "prior_block_count": len(prior_slots),
        },
        "prior_block_provenance": prior_provenance,
        "preserved_historical_seasons": sorted({slot[1] for slot in prior_slots}),
        "paper_only": PAPER_ONLY,
        "report_only": REPORT_ONLY,
        "readonly": READONLY,
    }
    return AdapterResult.success(snapshot, tuple(evidence))
