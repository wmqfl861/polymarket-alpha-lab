"""FotMob xG snapshot adapter: polite fetch + Understat wire transform.

Single responsibility: wrap FotMob's public unofficial JSON API (no key,
no credentials) behind a rate-limited, fail-closed fetch layer, and
transform the fetched payloads into the Understat snapshot shape
consumed by :mod:`polymarket_alpha_lab.papersim.xg`. This is the WP-P3
mechanism that reaches domestic leagues OUTSIDE Understat's Big-5
coverage: the EFL Championship was validated in Phase 1 (five gates,
4 PASS) and Eredivisie was validated through the adapter on 2026-11-07
(round-39 run, artifacts on the server: gates A-D PASS, gate E FAIL —
totals-family enterable under the same frozen domestic policy).

Canonical lineage: WP-P3 Phase 1 server-side tooling
``paper-sim/tools/fotmob_adapter.py`` -> ``fotmob_validate.py``
(validated 2026-11-03, report ``paper-sim/round31/
fotmob_validation_report.md``). This module is the Phase 2 promotion:
the fetch/transform core of that adapter as a stdlib-only library
module. The server-side tool keeps its DoH + ``curl --resolve``
transport and on-disk matchDetails cache; those stay outside the
library (hosts with pinned DNS for fotmob.com keep using the tool).

Network boundary (library convention, same as the usage doc): loaders
and transforms are PURE. Only :func:`fetch_fotmob_league` and
:func:`fetch_match_details` touch the network, via stdlib ``urllib``
only, and both share one module-wide polite-spacing guard
(:data:`SLEEP_S` = 1.3 s between any two requests, matching the Phase
1 fetch policy). No credentials, no cookies, paper-only.

M2 A4 bounded-read boundary (M2 plan section 2.5): every
network call now reads its body through
:func:`polymarket_alpha_lab.public_http.read_public_body` (declared
framing enforced, body capped at :data:`_RESPONSE_READ_LIMIT_DEFAULT`
or ``budget.next_response_read_limit()`` when a shared
:class:`~polymarket_alpha_lab.papersim.adapters.contracts.FetchBudget`
is supplied), the request URL and the final (post-redirect) URL must
sit inside the frozen ``https://www.fotmob.com/`` origin allowlist,
and the explicit ``allow_public_fetch`` permission (paper-only
default ``False``) is required before any connection is built. The
retry/backoff and polite-spacing rules keep their frozen Phase 1
semantics; with a shared budget each actual attempt charges the same
round budget and every wait is clamped to the remaining round time,
so budget exhaustion stops the loop instead of retrying past the
round. Transform, dedup, cancelled-match, skip-rate, team-count,
season and value checks below are frozen and untouched.

Fail-closed contract (inherited from Phase 1): the transform refuses
to emit a block when the payload shape looks like an upstream schema
change — season drift (``selectedSeason`` vs the derived season
window), malformed fixture rows, malformed xG values (non-numeric,
non-finite or negative — 0 is valid; codex review #8, 2026-11-07), a
team table that does not match the league size, or an xG-missing skip
rate above :data:`MAX_SKIP_RATE`.
Nothing is returned in that case; a :class:`FotMobSchemaError` names
the offending condition. Mild per-match gaps (matchDetails absent or
missing the xG row) are skipped and counted, bounded by the skip-rate
ceiling.

Validation status (WP-P3 Phase 1, frozen 2026-11-03): gates A-D PASS
(schema/coverage, league goal fit, xG-goal correlation, walk-forward
backtest vs a league-average baseline); gate E FAIL — 1X2 favorite
flips when teams new to the snapshot season (relegated/promoted sides)
fall back to league-average multipliers. Championship snapshots are
therefore sanctioned for the totals-family channels (O/U, BTTS, team
totals) through the standard domestic 10-variant path; 1X2 stays
deprioritized under the frozen domestic match_winner policy.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from enum import Enum
from typing import Callable, Mapping

from polymarket_alpha_lab import public_http
from polymarket_alpha_lab.papersim.adapters.contracts import (
    DEFAULT_ALLOW_PUBLIC_FETCH,
    DEFAULT_MAX_RESPONSE_BYTES,
    FetchBudget,
    BudgetExhaustedError,
    REASON_DEADLINE_EXCEEDED,
    require_public_fetch,
)

__all__ = [
    "FETCH_ATTEMPTS",
    "FETCH_TIMEOUT_S",
    "FotMobError",
    "FotMobLeague",
    "FotMobSchemaError",
    "HOST",
    "MAX_SKIP_RATE",
    "SLEEP_S",
    "USER_AGENT",
    "fetch_fotmob_league",
    "fetch_match_details",
    "fotmob_to_understat",
    "load_fotmob_snapshot",
]

#: FotMob public API host (unofficial, no key; data provider = Opta).
HOST = "www.fotmob.com"

#: Desktop browser UA, frozen from the Phase 1 adapter fetch policy.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: Polite spacing between any two FotMob requests (Phase 1 policy).
SLEEP_S = 1.3

#: Per-attempt HTTPS timeout for the fetch functions.
FETCH_TIMEOUT_S = 30.0

#: Fetch attempts (transport errors only) before giving up.
FETCH_ATTEMPTS = 4

#: Fail-closed ceiling on the per-match xG-missing skip rate (>5% of
#: kept matches without an xG row => probable upstream schema change).
MAX_SKIP_RATE = 0.05

#: Module-wide monotonic timestamp of the last FotMob request; enforces
#: :data:`SLEEP_S` spacing across BOTH fetch functions. Tests reset it.
_last_fetch_monotonic: float | None = None


class FotMobError(RuntimeError):
    """Fatal FotMob fetch/parse failure (transport gave up, non-JSON)."""


class FotMobSchemaError(FotMobError):
    """Fail-closed upstream schema change; no snapshot block is emitted."""


class FotMobLeague(Enum):
    """Verified FotMob league ids usable with the fetch/transform pair.

    Members carry ``(league_id, league_key, max_round, team_count)``:
    the numeric API id, the Understat-style snapshot block key, the
    numeric league-phase round bound (playoff rounds excluded) and the
    league size used by the fail-closed team-table check.

    Verification provenance: ``CHAMPIONSHIP`` is the Phase 1 validated
    configuration (720 matches, 2026-11-03); ``EREDIVISIE`` was run
    through the adapter and validated on 2026-11-07 (round-39 run,
    artifacts on the server: gates A-D PASS, gate E FAIL —
    totals-family enterable under the same frozen domestic policy);
    its shape constants held (18 clubs, 34 rounds). Further
    verified-as-existing ids (checked 2026-11-04, no adapter run):
    47 Premier League, 55 Serie A, 87 LaLiga, 53 Ligue 1, 54
    Bundesliga — all Understat-covered, so the FotMob path is not the
    sanctioned source for them. Add new members only with a verified
    id and a validated adapter run recorded in the lineage above.
    """

    CHAMPIONSHIP = (48, "Championship", 46, 24)
    EREDIVISIE = (57, "Eredivisie", 34, 18)

    def __init__(
        self,
        league_id: int,
        league_key: str,
        max_round: int,
        team_count: int,
    ) -> None:
        self.league_id = league_id
        self.league_key = league_key
        self.max_round = max_round
        self.team_count = team_count


def _league_constants(league: str | FotMobLeague) -> tuple[str, int, int | None]:
    """Resolve ``(league_key, max_round, team_count)`` for a league spec.

    A :class:`FotMobLeague` member contributes its own constants; a bare
    string key keeps the Phase 1 Championship defaults (46 numeric
    rounds, no strict team-count check).
    """
    if isinstance(league, FotMobLeague):
        return league.league_key, league.max_round, league.team_count
    return str(league), 46, None


def _league_id(league: int | FotMobLeague) -> int:
    if isinstance(league, FotMobLeague):
        return league.league_id
    return int(league)


#: Per-response read ceiling applied when no shared ``FetchBudget`` is
#: supplied: the A0 contract default (2 MiB), itself far under the 4 MiB
#: hard ceiling of ``public_http.read_public_body``.
_RESPONSE_READ_LIMIT_DEFAULT = DEFAULT_MAX_RESPONSE_BYTES

#: Frozen origin allowlist for every FotMob request (scheme + host).
_ALLOWED_ORIGINS = (f"https://{HOST}/",)

#: Receives ``(url, raw_bytes)`` for every successful bounded read; the
#: M2 adapters use it to build per-response evidence without changing
#: the fetch functions' return values.
ResponseSink = Callable[[str, bytes], None]


class FotMobOriginError(ValueError):
    """Request or final response URL outside the frozen FotMob origin
    allowlist (redirect drift included). Fixed message only — never
    carries the rejected URL. Subclasses :class:`ValueError` so the
    frozen retry loop never retries an origin rejection."""


def _verify_origin(url: object) -> None:
    """Refuse any URL outside ``https://www.fotmob.com/`` (fixed code)."""
    if not isinstance(url, str) or not url.startswith(_ALLOWED_ORIGINS):
        raise FotMobOriginError("fotmob_origin_not_allowed")


def _sleep_bounded(seconds: float, budget: FetchBudget | None) -> None:
    """``time.sleep(seconds)`` clamped to the remaining round budget.

    Without a budget this is the plain Phase 1 sleep (frozen semantics).
    With one, the wait never runs past ``budget.deadline_monotonic``:
    the sleep is clamped to the remaining seconds, and a wait that
    cannot fit before the deadline raises
    :class:`~polymarket_alpha_lab.papersim.adapters.contracts.BudgetExhaustedError`
    so the caller stops instead of sleeping or retrying past the round.
    """
    if seconds <= 0.0:
        return
    if budget is None:
        time.sleep(seconds)
        return
    remaining = budget.remaining_seconds()
    if remaining <= 0.0:
        raise BudgetExhaustedError(REASON_DEADLINE_EXCEEDED)
    time.sleep(min(float(seconds), remaining))


def _polite_pause(budget: FetchBudget | None = None) -> None:
    """Sleep the remainder of :data:`SLEEP_S` since the last request.

    With a shared budget the spacing sleep is additionally clamped to
    the remaining round time (M2 plan section 2.5: every wait stays
    inside the round budget).
    """
    global _last_fetch_monotonic
    now = time.monotonic()
    if _last_fetch_monotonic is not None:
        remaining = SLEEP_S - (now - _last_fetch_monotonic)
        if remaining > 0.0:
            _sleep_bounded(remaining, budget)
    _last_fetch_monotonic = time.monotonic()


def _http_get_json(
    url: str,
    *,
    timeout: float,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    budget: FetchBudget | None = None,
    response_sink: ResponseSink | None = None,
) -> object:
    """One HTTPS GET returning parsed JSON (raises on transport/JSON errors).

    M2 A4 bounded read (M2 plan section 2.5): the body comes through
    :func:`polymarket_alpha_lab.public_http.read_public_body` — declared
    framing enforced, body capped at ``budget.next_response_read_limit()``
    (:data:`_RESPONSE_READ_LIMIT_DEFAULT` without a budget) — and both
    the request URL and the final (post-redirect) URL must sit inside
    the frozen origin allowlist. The explicit network permission is
    re-checked here so no connection is ever built without an
    ``allow_public_fetch=True`` somewhere in the chain.
    """
    require_public_fetch(allow_public_fetch)
    _verify_origin(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    effective_timeout = float(timeout)
    if budget is not None:
        effective_timeout = min(
            effective_timeout, budget.effective_timeout_seconds()
        )
    with urllib.request.urlopen(request, timeout=effective_timeout) as response:
        _verify_origin(response.geturl())
        read_limit = (
            budget.next_response_read_limit()
            if budget is not None
            else _RESPONSE_READ_LIMIT_DEFAULT
        )
        body = public_http.read_public_body(response, read_limit)
    if budget is not None:
        budget.charge_response_bytes(len(body))
    if response_sink is not None:
        response_sink(url, body)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise FotMobError(
            f"non-JSON response from {url} ({len(body)} bytes): {exc}"
        ) from exc


def _fetch_json(
    url: str,
    *,
    timeout: float,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    budget: FetchBudget | None = None,
    response_sink: ResponseSink | None = None,
) -> object:
    """Fetch with retry/backoff and polite spacing; fail via FotMobError.

    The retry/backoff rules keep their frozen Phase 1 semantics
    (:data:`FETCH_ATTEMPTS` transport-error attempts, ``5 s x attempt``
    backoff, :data:`SLEEP_S` polite spacing). With a shared budget every
    actual attempt charges ``budget.charge_request()`` and every wait is
    clamped to the remaining round time, so budget or deadline
    exhaustion stops the loop immediately — never retried past.
    """
    require_public_fetch(allow_public_fetch)
    last_exc: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        if budget is not None:
            budget.charge_request()
        _polite_pause(budget)
        try:
            return _http_get_json(
                url,
                timeout=timeout,
                allow_public_fetch=allow_public_fetch,
                budget=budget,
                response_sink=response_sink,
            )
        except (urllib.error.URLError, OSError, FotMobError) as exc:
            last_exc = exc
            if attempt < FETCH_ATTEMPTS - 1:
                _sleep_bounded(5.0 * (attempt + 1), budget)
    raise FotMobError(f"giving up after {FETCH_ATTEMPTS} attempts: {url} ({last_exc})")


def fetch_fotmob_league(
    league_id: int | FotMobLeague,
    season: str | None = None,
    *,
    timeout: float = FETCH_TIMEOUT_S,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    budget: FetchBudget | None = None,
    response_sink: ResponseSink | None = None,
) -> dict:
    """Fetch one league-season fixtures payload (NETWORK, rate-limited).

    ``GET https://www.fotmob.com/api/data/leagues?id=<league_id>`` with
    an optional ``season=YYYY%2FYYYY`` parameter (FotMob season strings
    like ``"2025/2026"``; ``None`` takes the current season). Returns
    the parsed payload verbatim (``details`` / ``fixtures.allMatches``
    shape) for feeding :func:`fotmob_to_understat`; no normalization,
    no caching — resumable on-disk caches stay with the server-side
    Phase 1 tool. Raises :class:`FotMobError` when the payload is not a
    JSON object or all retries fail.

    M2 A4: ``allow_public_fetch`` defaults to the paper-only denial —
    no connection is built without an explicit ``True``. With a shared
    ``budget`` every attempt/wait is charged/clamped against the round
    and ``response_sink`` receives ``(url, raw_bytes)`` per successful
    bounded read.
    """
    require_public_fetch(allow_public_fetch)
    url = f"https://{HOST}/api/data/leagues?id={_league_id(league_id)}"
    if season is not None:
        url += "&season=" + urllib.parse.quote(str(season), safe="")
    payload = _fetch_json(
        url,
        timeout=timeout,
        allow_public_fetch=allow_public_fetch,
        budget=budget,
        response_sink=response_sink,
    )
    if not isinstance(payload, dict):
        raise FotMobSchemaError(f"leagues payload for {url} is not an object")
    return payload


def fetch_match_details(
    match_id: str | int,
    *,
    timeout: float = FETCH_TIMEOUT_S,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    budget: FetchBudget | None = None,
    response_sink: ResponseSink | None = None,
) -> dict:
    """Fetch one match's details payload (NETWORK, rate-limited).

    ``GET https://www.fotmob.com/api/data/matchDetails?matchId=<id>`` —
    the per-match payload holding the score header and the Periods.All
    xG rows the transform extracts. Same contract as
    :func:`fetch_fotmob_league`: parsed payload verbatim, no cache;
    same M2 A4 permission/budget/``response_sink`` parameters.
    """
    require_public_fetch(allow_public_fetch)
    url = f"https://{HOST}/api/data/matchDetails?matchId={match_id}"
    payload = _fetch_json(
        url,
        timeout=timeout,
        allow_public_fetch=allow_public_fetch,
        budget=budget,
        response_sink=response_sink,
    )
    if not isinstance(payload, dict):
        raise FotMobSchemaError(f"matchDetails payload for {url} is not an object")
    return payload


# --------------------------------------------------------------------------
# payload extraction (pure; fail-closed on shape drift)
# --------------------------------------------------------------------------

def _expected_season(season_key: str) -> str:
    """'2026' -> '2026/2027' (September-May season window)."""
    try:
        year = int(season_key)
    except ValueError as exc:
        raise FotMobSchemaError(
            f"season_key must be an integer year string, got {season_key!r}"
        ) from exc
    return f"{year}/{year + 1}"


def _parse_league_payload(data: object, season_key: str) -> list:
    """Validate the leagues payload shape and return ``fixtures.allMatches``."""
    if not isinstance(data, dict):
        raise FotMobSchemaError(f"leagues payload for {season_key} is not an object")
    details = data.get("details")
    fixtures = data.get("fixtures")
    if not isinstance(details, dict) or not isinstance(fixtures, dict):
        raise FotMobSchemaError(f"leagues payload for {season_key} missing details/fixtures")
    expected = _expected_season(season_key)
    selected = details.get("selectedSeason")
    if selected != expected:
        raise FotMobSchemaError(
            f"season drift for {season_key}: selectedSeason={selected!r} "
            f"expected={expected!r}"
        )
    matches = fixtures.get("allMatches")
    if not isinstance(matches, list) or not matches:
        raise FotMobSchemaError(f"allMatches missing/empty for {season_key}")
    for match in matches:
        if not isinstance(match, dict) or not isinstance(match.get("status"), dict):
            raise FotMobSchemaError(f"malformed fixture row in {season_key}")
        for side in ("home", "away"):
            team = match.get(side)
            if not isinstance(team, dict) or "id" not in team or "name" not in team:
                raise FotMobSchemaError(
                    f"malformed {side} team in fixture {match.get('id')}"
                )
    return matches


def _is_cancelled(match: Mapping[str, object]) -> bool:
    """True for cancelled/abandoned fixtures (codex review #5, 2026-11-04).

    Cached FotMob sources contain abandoned games whose only markers are
    ``status.cancelled=true`` and reason ``longKey="aborted"`` while
    ``finished`` is also true (e.g. Blackburn-Ipswich 4825094 and its
    replay 5008942). The pre-fix text-only check matched ``"cancel"`` and
    kept those rows, double-counting abandoned games alongside their
    replays. Reject the explicit boolean flag first, then match both
    ``cancel`` and ``abort`` reason keys.
    """
    status = match.get("status") or {}
    if status.get("cancelled") is True:
        return True
    reason = status.get("reason")
    if isinstance(reason, dict):
        key = (str(reason.get("longKey", "")) + str(reason.get("shortKey", ""))).lower()
        return "cancel" in key or "abort" in key
    return False


def _partition_matches(matches: list, max_round: int) -> dict[str, list]:
    """Split fixtures into kept (numeric round, finished, not cancelled)
    and excluded buckets (playoff / cancelled / unfinished) with reasons."""
    kept: list = []
    playoff: list = []
    cancelled: list = []
    unfinished: list = []
    for match in matches:
        round_raw = str(match.get("round", ""))
        if not (round_raw.isdigit() and 1 <= int(round_raw) <= max_round):
            playoff.append(match)
        elif _is_cancelled(match):
            cancelled.append(match)
        elif not match["status"].get("finished"):
            unfinished.append(match)
        else:
            kept.append(match)
    return {"kept": kept, "playoff": playoff, "cancelled": cancelled,
            "unfinished": unfinished}


def _extract_xg_pair(md: Mapping[str, object], title: str) -> tuple[str, str] | None:
    """First non-null ``[home, away]`` row named ``title`` in Periods.All."""
    try:
        groups = md["content"]["stats"]["Periods"]["All"]["stats"]
    except (KeyError, TypeError):
        return None
    if not isinstance(groups, list):
        return None
    for group in groups:
        if not isinstance(group, dict):
            continue
        for stat in group.get("stats", []) or []:
            if not isinstance(stat, dict) or stat.get("title") != title:
                continue
            values = stat.get("stats")
            if (isinstance(values, list) and len(values) == 2
                    and values[0] is not None and values[1] is not None):
                return str(values[0]), str(values[1])
    return None


def _parse_xg_number(raw: object) -> float:
    """``float(raw)`` iff the xG-family value is finite and ``>= 0``.

    Shared value check for the transform and load boundaries (codex
    review #8, 2026-11-07): FotMob can emit ``"NaN"`` / ``"inf"`` xG
    strings, and a bare ``float()`` would admit them into team
    histories long before the downstream gate-evidence finite check
    runs. Non-numeric, non-finite and negative inputs raise
    ``ValueError`` (each boundary wraps it in its own error type);
    ``0`` / ``"0.0"`` are VALID — a side can generate zero expected
    goals. This check only REJECTS malformed data: accepted values
    convert to exactly ``float(raw)`` and the wire strings are never
    reformatted, so valid snapshots stay byte-identical.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value {raw!r} is not numeric") from exc
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"value {raw!r} is not finite nonnegative")
    return value


def _parse_xg_value(raw: str, match_id: str, field: str) -> float:
    """:func:`_parse_xg_number` wrapped in the transform's house error."""
    try:
        return _parse_xg_number(raw)
    except ValueError as exc:
        raise FotMobSchemaError(f"match {match_id}: {field} {exc}") from exc


def _extract_score(md: Mapping[str, object]) -> tuple[int, int] | None:
    try:
        home, away = md["header"]["teams"][0], md["header"]["teams"][1]
        if home.get("score") is not None and away.get("score") is not None:
            return int(home["score"]), int(away["score"])
    except (KeyError, TypeError, IndexError, ValueError):
        pass
    return None


def _fmt_date(utc: str) -> str:
    """'2026-11-03T19:45:00.000Z' -> '2026-11-03 19:45:00' (Understat shape)."""
    return utc.replace("T", " ").replace("Z", "").split(".")[0]


def _result_code(goals_for: int, goals_against: int) -> str:
    if goals_for > goals_against:
        return "w"
    if goals_for == goals_against:
        return "d"
    return "l"


def _team_index(matches: list) -> dict[str, dict[str, str]]:
    """``id -> {title, short_title}`` over ALL fixtures (incl. playoffs)."""
    index: dict[str, dict[str, str]] = {}
    for match in matches:
        for side in ("home", "away"):
            team = match[side]
            index[str(team["id"])] = {
                "title": team["name"],
                "short_title": team.get("shortName", ""),
            }
    return index


def _build_block(
    kept: list,
    details: Mapping[str, object],
    teams_index: Mapping[str, Mapping[str, str]],
) -> tuple[dict, list]:
    """Build one Understat-format block ``{teams, dates}`` plus skipped ids."""
    histories: dict[str, list] = {}
    dates: list = []
    skipped: list = []
    for match in kept:
        mid = str(match["id"])
        md = details.get(mid)
        xg = _extract_xg_pair(md, "Expected goals (xG)") if md else None
        score = _extract_score(md) if md else None
        if xg is None or score is None:
            skipped.append(mid)
            continue
        npxg = _extract_xg_pair(md, "xG non-penalty") or xg
        home_goals, away_goals = score
        home_xg, away_xg = xg
        home_npxg, away_npxg = npxg
        # Validated conversion (codex review #8, 2026-11-07): a malformed
        # xG string fails closed BEFORE entering histories; accepted values
        # are exactly float(<wire string>) and the strings stay verbatim in
        # the dates row below, so valid snapshots remain byte-identical.
        home_xg_f = _parse_xg_value(home_xg, mid, "home xG")
        away_xg_f = _parse_xg_value(away_xg, mid, "away xG")
        home_npxg_f = _parse_xg_value(home_npxg, mid, "home npxG")
        away_npxg_f = _parse_xg_value(away_npxg, mid, "away npxG")
        date_str = _fmt_date(match["status"]["utcTime"])
        home, away = match["home"], match["away"]
        home_id, away_id = str(home["id"]), str(away["id"])
        histories.setdefault(home_id, []).append({
            "h_a": "h", "xG": home_xg_f, "xGA": away_xg_f,
            "npxG": home_npxg_f, "npxGA": away_npxg_f,
            "scored": home_goals, "missed": away_goals,
            "result": _result_code(home_goals, away_goals), "date": date_str,
        })
        histories.setdefault(away_id, []).append({
            "h_a": "a", "xG": away_xg_f, "xGA": home_xg_f,
            "npxG": away_npxg_f, "npxGA": home_npxg_f,
            "scored": away_goals, "missed": home_goals,
            "result": _result_code(away_goals, home_goals), "date": date_str,
        })
        dates.append({
            "id": mid, "isResult": True,
            "h": {"id": home_id, "title": home["name"],
                  "short_title": home.get("shortName", "")},
            "a": {"id": away_id, "title": away["name"],
                  "short_title": away.get("shortName", "")},
            "goals": {"h": str(home_goals), "a": str(away_goals)},
            "xG": {"h": home_xg, "a": away_xg},
            "datetime": date_str,
        })
    teams: dict[str, dict] = {}
    for team_id, meta in teams_index.items():
        history = sorted(histories.get(team_id, []), key=lambda entry: entry["date"])
        teams[team_id] = {"id": team_id, "title": meta["title"], "history": history}
    dates.sort(key=lambda row: row["datetime"])
    return {"teams": teams, "dates": dates}, skipped


def fotmob_to_understat(
    raw: Mapping[str, object],
    league_key: str | FotMobLeague,
    season_key: str,
    *,
    max_round: int | None = None,
    team_count: int | None = None,
    max_skip_rate: float = MAX_SKIP_RATE,
) -> dict[str, Mapping[str, object]]:
    """Transform fetched FotMob payloads into one Understat snapshot block.

    ``raw`` is the two-fetch bundle: ``{"fixtures": <leagues payload
    from fetch_fotmob_league>, "details": {match_id: <matchDetails
    payload from fetch_match_details>}}``. PURE — no network access;
    per-match ``details`` entries may be missing (skipped and counted)
    so partially fetched inputs fail closed rather than silently shrink.

    Returns the single-block snapshot
    ``{f"{league_key}_{season_key}": {"teams": ..., "dates": ...}}`` in
    the exact wire shape :mod:`polymarket_alpha_lab.papersim.xg`
    consumes (history xG floats, dates xG strings,
    ``YYYY-MM-DD HH:MM:SS`` UTC datetimes), ready to merge into a
    larger snapshot mapping.

    Fail-closed (raises :class:`FotMobSchemaError`, returns nothing):
    season drift vs the derived ``YYYY/YYYY+1`` window, malformed
    fixture rows, malformed xG values (non-numeric, non-finite or
    negative — 0 is valid; unlike xG-missing rows these are NOT
    skip-counted), a team table not matching ``team_count`` when the
    check is enabled, and an xG-missing skip rate above
    ``max_skip_rate`` (or zero usable matches). Pass a
    :class:`FotMobLeague` member as ``league_key`` to inherit its
    round/team constants; a bare string uses the Phase 1 Championship
    defaults (46 numeric rounds, no strict team-count check).
    """
    resolved_key, default_round, default_count = _league_constants(league_key)
    if max_round is None:
        max_round = default_round
    if team_count is None:
        team_count = default_count

    fixtures = raw.get("fixtures")
    if not isinstance(fixtures, dict):
        raise FotMobSchemaError(f"raw bundle for {resolved_key} missing fixtures payload")
    details = raw.get("details")
    if not isinstance(details, Mapping):
        raise FotMobSchemaError(f"raw bundle for {resolved_key} missing details mapping")

    matches = _parse_league_payload(fixtures, season_key)
    parts = _partition_matches(matches, max_round)
    kept = parts["kept"]

    index = _team_index(matches)
    if team_count is not None and len(index) != team_count:
        raise FotMobSchemaError(
            f"season {season_key}: expected {team_count} teams, got {len(index)}"
        )

    block, skipped = _build_block(kept, details, index)
    skip_rate = len(skipped) / max(len(kept), 1)
    if skip_rate > max_skip_rate or (len(kept) - len(skipped)) == 0:
        raise FotMobSchemaError(
            f"season {season_key}: xG-missing rate {skip_rate:.1%} ({len(skipped)} of "
            f"{len(kept)}) exceeds fail-closed threshold {max_skip_rate:.0%} - "
            "probable upstream schema change; no block emitted"
        )
    titles = {row["title"] for row in block["teams"].values()}
    if team_count is not None and (
        len(titles) != team_count or len(block["teams"]) != team_count
    ):
        raise FotMobSchemaError(
            f"season {season_key}: block team table is not {team_count} unique teams"
        )
    return {f"{resolved_key}_{season_key}": block}


def load_fotmob_snapshot(path: str) -> dict[str, Mapping[str, object]]:
    """Load a pre-fetched FotMob->Understat snapshot (PURE, no network).

    Structural validation with precise errors: the root must be a JSON
    object; every block must be a mapping with a non-empty ``teams``
    mapping and a ``dates`` list; every ``dates`` row must be a mapping
    with an ``xG`` object carrying ``h`` / ``a``. Every xG-family value
    (dates ``xG.h`` / ``xG.a``, history ``xG`` / ``xGA`` / ``npxG`` /
    ``npxGA``) must parse as a finite nonnegative number (codex review
    #8, 2026-11-07: ``json.load`` accepts bare ``NaN`` / ``Infinity``
    literals, and pre-fetched Phase 1 files predate the transform-side
    check). Validation only rejects — accepted payloads are returned
    unmodified, byte-identical (raw payloads preserved before
    normalization), ready for
    :func:`polymarket_alpha_lab.papersim.xg.load_xg_payload`.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("FotMob snapshot root must be a JSON object")
    for key, block in payload.items():
        if not isinstance(block, Mapping):
            raise ValueError(f"snapshot block {key!r} must be an object")
        teams = block.get("teams")
        dates = block.get("dates")
        if not isinstance(teams, Mapping) or not teams:
            raise ValueError(f"snapshot block {key!r} has no teams mapping")
        if not isinstance(dates, list):
            raise ValueError(f"snapshot block {key!r} has no dates list")
        for team_id, team in teams.items():
            if not isinstance(team, Mapping):
                raise ValueError(
                    f"snapshot block {key!r} team {team_id!r} must be an object"
                )
            history = team.get("history")
            if not isinstance(history, list):
                raise ValueError(
                    f"snapshot block {key!r} team {team_id!r} has no history list"
                )
            for entry in history:
                if not isinstance(entry, Mapping):
                    raise ValueError(
                        f"snapshot block {key!r} team {team_id!r} has a "
                        "non-object history entry"
                    )
                for field in ("xG", "xGA", "npxG", "npxGA"):
                    try:
                        _parse_xg_number(entry[field])
                    except KeyError as exc:
                        raise ValueError(
                            f"snapshot block {key!r} team {team_id!r} "
                            f"history entry has no {field}"
                        ) from exc
                    except ValueError as exc:
                        raise ValueError(
                            f"snapshot block {key!r} team {team_id!r} "
                            f"history {field} {exc}"
                        ) from exc
        for row in dates:
            if not isinstance(row, Mapping):
                raise ValueError(f"snapshot block {key!r} has a non-object dates row")
            xg = row.get("xG")
            if not isinstance(xg, Mapping) or "h" not in xg or "a" not in xg:
                raise ValueError(
                    f"snapshot block {key!r} has a dates row without xG.h/xG.a"
                )
            for side in ("h", "a"):
                try:
                    _parse_xg_number(xg[side])
                except ValueError as exc:
                    raise ValueError(
                        f"snapshot block {key!r} dates xG.{side} {exc}"
                    ) from exc
    return payload
