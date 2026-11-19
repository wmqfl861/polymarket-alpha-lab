"""ESPN NBA snapshot adapter: polite fetch + frozen snapshot transform.

Single responsibility: wrap ESPN's public site API (no key, no
credentials) behind a rate-limited, fail-closed fetch layer with an
on-disk raw-payload cache, and transform the fetched payloads into the
FROZEN NBA snapshot shape consumed by the papersim NBA model. This is
the WP-NBA-1 data adapter, built per the round-46 feasibility study
(``paper-sim/round46/nba_feasibility.md``, server-side, 2026-11-10):
``stats.nba.com`` and ``cdn.nba.com`` are BLOCKED from the datacenter
IP, so ESPN is the sanctioned source (verified GREEN there: teams
schedule, per-game summary, scoreboard). basketball-reference stays a
server-side pace cross-check and is intentionally NOT fetched here.

House pattern: this module mirrors :mod:`polymarket_alpha_lab.papersim.
fotmob` (the WP-P3 adapter) — stdlib-only, shared module-wide polite
spacing, retry/backoff transport, fail-closed schema validation with
precise errors, and the finite/nonnegative value discipline of codex
review #8 (non-numeric / non-finite / negative rejected at the boundary;
NaN and Infinity literals rejected on load too). One deliberate
extension over fotmob: this adapter carries its own on-disk cache
(atomic writes, raw payloads verbatim) because a full-season NBA
backfill is ~210 requests that must be resumable, unlike the FotMob
per-league two-fetch bundle.

Network boundary: only :func:`fetch_team_schedule`,
:func:`fetch_game_summary` and the orchestrating
:func:`fetch_nba_season` touch the network, via stdlib ``urllib`` only,
sharing one module-wide polite-spacing guard (:data:`SLEEP_S` = 1.2 s
between any two requests). No credentials, no cookies, paper-only.

M2 A4 bounded-read boundary (M2 plan section 2.5): every
network call now reads its body through
:func:`polymarket_alpha_lab.public_http.read_public_body` (declared
framing enforced, body capped at :data:`_RESPONSE_READ_LIMIT_DEFAULT`
or ``budget.next_response_read_limit()`` when a shared
:class:`~polymarket_alpha_lab.papersim.adapters.contracts.FetchBudget`
is supplied), the request URL and the final (post-redirect) URL must
sit inside the frozen ``https://site.api.espn.com/`` origin allowlist,
and the explicit ``allow_public_fetch`` permission (paper-only default
``False``) is required before any connection is built. Retry/backoff
and polite-spacing semantics stay frozen; with a shared budget every
actual attempt charges the same round budget and every wait is
clamped to the remaining round time.

On-disk cache boundary (frozen-surface note): the read-through cache
below (:data:`DEFAULT_CACHE_DIR`, :func:`_atomic_write_json`,
:func:`_cache_read`) is the LEGACY WP-NBA-1 ops path kept verbatim for
the standalone backfill CLI (:func:`main`). The M2 adapter entrance
(:mod:`polymarket_alpha_lab.papersim.adapters.sports`) NEVER enables
it — it passes ``cache_dir=None`` (in-memory only; PostgreSQL
snapshots arrive with the S0+ storage wiring, not here), so M2 rounds
perform zero disk-cache writes.

NBA shadow positioning: this data adapter being usable does NOT
authorize NBA entry. NBA remains a shadow/research lane; the frozen
entry policy elsewhere in papersim is unchanged by anything here.

Snapshot contract (FROZEN — do not deviate; the model is built against
this exact JSON)::

    {"generated_at": "<iso8601>", "source": "espn-nba",
     "season": "2026-27",
     "teams": {"<espn abbrev>": {"display": str, "games": [
         {"date": "YYYY-MM-DD", "home": bool, "opponent": abbrev,
          "poss": float, "pts": int, "opp_pts": int, "ortg": float,
          "drtg": float, "rest_days": int|null, "b2b": bool}]}},
     "injuries": {"<abbrev>": [{"player": str, "status": str,
                                "date": iso}]}}

Possession accounting (documented split): each team-game ``poss`` is
that team's OWN share, estimated with the Dean Oliver formula
``poss = FGA - OREB + TOV + 0.44 * FTA`` from that team's own box line
(FT coefficient :data:`FT_COEFFICIENT`); the GAME total is the sum of
the two teams' estimates and the split between the two rows is exactly
those two estimates (they differ slightly because rebounds and
turnovers are not perfectly alternating — the split is kept per-team
rather than forced to 50/50). ``ortg`` / ``drtg`` are per-100 of the
team's OWN possessions: ``100 * pts / poss`` and
``100 * opp_pts / poss``, i.e. the conventional ~110/100 rating scale
matching the study's model design (pace sanity band 94-102 poss).

Calendar conventions: ESPN event dates are true UTC instants whose
``YYYY-MM-DD`` prefix can run a DAY AHEAD of the North American game
date for evening starts (verified on real 2026-27 data: Pistons-Knicks
Tue Oct 27 8pm ET is stamped ``2026-10-28T00:00Z``), so ``date`` is
the EASTERN-TIME calendar date derived from the UTC instant with the
US DST rules (:func:`_et_date`, stdlib-only, no zoneinfo/tzdata
dependency); ``rest_days`` is the calendar-day gap to the team's
previous game (``null`` for the first game of the season); ``b2b`` is
``rest_days == 1``. Injuries are the latest report per team: the
``injuries`` block of the latest-dated game summary involving that
team, de-duplicated per player.

Fail-closed contract: :func:`build_snapshot` refuses to emit a
snapshot when the payloads look like upstream schema change — season
echo drift (``requestedSeason`` vs the derived request year), team
abbreviation mismatch, malformed fixture/box rows, missing possession
inputs, non-numeric or negative stats, zero-possession games, scores
disagreeing between schedule and summary, per-team game counts above
:data:`MAX_GAMES_PER_TEAM`, duplicate game dates, or a team table that
does not match the expected 30 NBA teams. Nothing is returned in that
case; an :class:`EspnNbaSchemaError` names the offending condition.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Collection, Mapping

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
    "API_PATH",
    "DEFAULT_CACHE_DIR",
    "FETCH_ATTEMPTS",
    "FETCH_TIMEOUT_S",
    "FT_COEFFICIENT",
    "HOST",
    "MAX_GAMES_PER_TEAM",
    "NBA_TEAMS",
    "SCHEDULE_CACHE_MAX_AGE_S",
    "SLEEP_S",
    "SOURCE_KEY",
    "TEAM_COUNT",
    "USER_AGENT",
    "EspnNbaError",
    "EspnNbaSchemaError",
    "build_snapshot",
    "fetch_game_summary",
    "fetch_nba_season",
    "fetch_team_schedule",
    "load_nba_snapshot",
    "main",
    "season_year",
    "write_snapshot",
]

#: ESPN public site API host (unofficial-but-stable, no key). NOTE: the
#: study's endpoint table wrote ``site.api.espn.com/.../nba/scoreboard``
#: shorthand; the verbatim working path carries the ``/apis`` prefix
#: (verified 2026-11-10: without it the CDN answers 403 Access Denied).
HOST = "site.api.espn.com"

#: Path prefix shared by every endpoint used here.
API_PATH = "/apis/site/v2/sports/basketball/nba"

#: Desktop browser UA (frozen from the fotmob fetch policy).
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: Polite spacing between any two ESPN requests (WP-NBA-1 policy).
SLEEP_S = 1.2

#: Per-attempt HTTPS timeout for the fetch functions.
FETCH_TIMEOUT_S = 30.0

#: Fetch attempts (transport errors only) before giving up.
FETCH_ATTEMPTS = 4

#: Free-throw coefficient of the Dean Oliver possession estimate.
FT_COEFFICIENT = 0.44

#: Default on-disk cache location (raw payloads verbatim; created
#: lazily on first write). Pass ``cache_dir=None`` to disable caching.
#: LEGACY WP-NBA-1 OPS PATH ONLY (frozen-surface boundary): the M2
#: adapter entrance ``papersim.adapters.sports`` never uses it — it
#: pins ``cache_dir=None`` (in-memory data only, zero disk-cache
#: writes; PostgreSQL snapshots arrive with the S0+ wiring).
DEFAULT_CACHE_DIR = Path.home() / ".papersim_cache" / "espn_nba"

#: Schedules gain results over time, so cached schedule payloads expire
#: after six hours; summary payloads are immutable once a game is final
#: and are cached without expiry.
SCHEDULE_CACHE_MAX_AGE_S = 6.0 * 3600.0

#: ``source`` field of every emitted snapshot (frozen contract).
SOURCE_KEY = "espn-nba"

#: Fail-closed ceiling on per-team completed-game rows (an NBA regular
#: season is 82 games; 100 leaves headroom for data quirks, not abuse).
MAX_GAMES_PER_TEAM = 100

#: Exact frozen 30-team table, ``abbrev -> (espn team id, display)``.
#: Derived live 2026-11-10 UTC from each team's own schedule payload
#: (``teams/{id}/schedule?season=2027`` echoing ``team.abbreviation``);
#: frozen as a ``MappingProxyType`` so nothing downstream can mutate it.
#: ``fetch_team_schedule`` re-validates every payload against the
#: requested abbreviation, so a stale id here fails closed, loudly.
NBA_TEAMS: Mapping[str, tuple[int, str]] = MappingProxyType({
    "ATL": (1, "Atlanta Hawks"), "BOS": (2, "Boston Celtics"),
    "NO": (3, "New Orleans Pelicans"), "CHI": (4, "Chicago Bulls"),
    "CLE": (5, "Cleveland Cavaliers"), "DAL": (6, "Dallas Mavericks"),
    "DEN": (7, "Denver Nuggets"), "DET": (8, "Detroit Pistons"),
    "GS": (9, "Golden State Warriors"), "HOU": (10, "Houston Rockets"),
    "IND": (11, "Indiana Pacers"), "LAC": (12, "LA Clippers"),
    "LAL": (13, "Los Angeles Lakers"), "MIA": (14, "Miami Heat"),
    "MIL": (15, "Milwaukee Bucks"), "MIN": (16, "Minnesota Timberwolves"),
    "BKN": (17, "Brooklyn Nets"), "NY": (18, "New York Knicks"),
    "ORL": (19, "Orlando Magic"), "PHI": (20, "Philadelphia 76ers"),
    "PHX": (21, "Phoenix Suns"), "POR": (22, "Portland Trail Blazers"),
    "SAC": (23, "Sacramento Kings"), "SA": (24, "San Antonio Spurs"),
    "OKC": (25, "Oklahoma City Thunder"), "UTAH": (26, "Utah Jazz"),
    "WSH": (27, "Washington Wizards"), "TOR": (28, "Toronto Raptors"),
    "MEM": (29, "Memphis Grizzlies"), "CHA": (30, "Charlotte Hornets"),
})

#: League size used by the default fail-closed team-table check.
TEAM_COUNT = 30

#: Frozen top-level snapshot keys / per-game keys (load boundary).
_SNAPSHOT_KEYS = frozenset({"generated_at", "source", "season", "teams", "injuries"})
_GAME_KEYS = frozenset({
    "date", "home", "opponent", "poss", "pts", "opp_pts",
    "ortg", "drtg", "rest_days", "b2b",
})

_SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PAIRED_STAT_RE = re.compile(r"^(\d+)-(\d+)$")
_SINGLE_STAT_RE = re.compile(r"^\d+$")

#: Module-wide monotonic timestamp of the last ESPN request; enforces
#: :data:`SLEEP_S` spacing across ALL fetch functions. Tests reset it.
_last_fetch_monotonic: float | None = None


class EspnNbaError(RuntimeError):
    """Fatal ESPN NBA fetch/parse failure (transport gave up, non-JSON)."""


class EspnNbaSchemaError(EspnNbaError):
    """Fail-closed upstream schema change; no snapshot is emitted."""


# ---------------------------------------------------------------------------
# season labels, polite spacing, transport, cache
# ---------------------------------------------------------------------------

def season_year(season: str) -> int:
    """``"2026-27"`` -> ``2027`` (ESPN's ``season=`` request parameter).

    ESPN requests a season by its ENDING calendar year; the label's two
    trailing digits must equal ``(start + 1) % 100`` or the label is
    malformed.
    """
    match = _SEASON_RE.match(str(season))
    if match is None:
        raise ValueError(f"season label must be YYYY-YY, got {season!r}")
    start = int(match.group(1))
    if int(match.group(2)) != (start + 1) % 100:
        raise ValueError(f"season label {season!r} is not consecutive years")
    return start + 1


def _season_label(year: int) -> str:
    """``2027`` -> ``"2026-27"`` (inverse of :func:`season_year`)."""
    return f"{year - 1}-{year % 100:02d}"


#: Per-response read ceiling applied when no shared ``FetchBudget`` is
#: supplied: the A0 contract default (2 MiB), itself far under the 4 MiB
#: hard ceiling of ``public_http.read_public_body``.
_RESPONSE_READ_LIMIT_DEFAULT = DEFAULT_MAX_RESPONSE_BYTES

#: Frozen origin allowlist for every ESPN request (scheme + host).
_ALLOWED_ORIGINS = (f"https://{HOST}/",)

#: Receives ``(url, raw_bytes)`` for every successful bounded read; the
#: M2 adapters use it to build per-response evidence without changing
#: the fetch functions' return values.
ResponseSink = Callable[[str, bytes], None]


class EspnNbaOriginError(ValueError):
    """Request or final response URL outside the frozen ESPN origin
    allowlist (redirect drift included). Fixed message only — never
    carries the rejected URL. Subclasses :class:`ValueError` so the
    frozen retry loop never retries an origin rejection."""


def _verify_origin(url: object) -> None:
    """Refuse any URL outside ``https://site.api.espn.com/`` (fixed code)."""
    if not isinstance(url, str) or not url.startswith(_ALLOWED_ORIGINS):
        raise EspnNbaOriginError("espn_nba_origin_not_allowed")


def _sleep_bounded(seconds: float, budget: FetchBudget | None) -> None:
    """``time.sleep(seconds)`` clamped to the remaining round budget.

    Without a budget this is the plain WP-NBA-1 sleep (frozen
    semantics). With one, the wait never runs past
    ``budget.deadline_monotonic``: the sleep is clamped to the
    remaining seconds, and a wait that cannot fit before the deadline
    raises
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
        raise EspnNbaError(
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
    """Fetch with retry/backoff and polite spacing; fail via EspnNbaError.

    The retry/backoff rules keep their frozen WP-NBA-1 semantics
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
        except (urllib.error.URLError, OSError, EspnNbaError) as exc:
            last_exc = exc
            if attempt < FETCH_ATTEMPTS - 1:
                _sleep_bounded(5.0 * (attempt + 1), budget)
    raise EspnNbaError(f"giving up after {FETCH_ATTEMPTS} attempts: {url} ({last_exc})")


def _atomic_write_json(path: Path, payload: object) -> None:
    """Serialize with ``allow_nan=False`` and replace ``path`` atomically."""
    blob = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent),
        prefix=path.name + ".", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _cache_read(path: Path, max_age_s: float | None) -> dict | None:
    """Read a cached payload; unreadable/corrupt/stale entries are misses.

    A corrupt cache file is treated as a miss (refetch), never as a
    crash: the refetched payload is still fully validated upstream.
    """
    if not path.is_file():
        return None
    if max_age_s is not None:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return None
        if age > max_age_s:
            return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cached_fetch_json(
    url: str,
    cache_path: Path | None,
    max_age_s: float | None,
    *,
    timeout: float,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    budget: FetchBudget | None = None,
    response_sink: ResponseSink | None = None,
) -> dict:
    """Read-through cache around :func:`_fetch_json` (cache hit = no network).

    LEGACY ops path (frozen-surface boundary, M2 plan section 2.5): a
    non-``None`` ``cache_path`` is only ever supplied by the legacy
    WP-NBA-1 backfill tooling/CLI. M2 rounds pass ``cache_dir=None``
    end to end, so this wrapper performs zero disk writes for them. A
    cache hit charges no budget and builds no evidence (no request
    happened); a miss charges and reports exactly like a direct fetch.
    """
    if cache_path is not None:
        cached = _cache_read(cache_path, max_age_s)
        if cached is not None:
            return cached
    payload = _fetch_json(
        url,
        timeout=timeout,
        allow_public_fetch=allow_public_fetch,
        budget=budget,
        response_sink=response_sink,
    )
    if not isinstance(payload, dict):
        raise EspnNbaSchemaError(f"payload from {url} is not an object")
    if cache_path is not None:
        _atomic_write_json(cache_path, payload)
    return payload


# ---------------------------------------------------------------------------
# fetch layer (NETWORK, rate-limited, cached)
# ---------------------------------------------------------------------------

def _schedule_url(abbrev: str, year: int) -> str:
    team_id = NBA_TEAMS[abbrev][0]
    return (
        f"https://{HOST}{API_PATH}/teams/{team_id}/schedule"
        f"?season={year}&seasontype=2"
    )


def fetch_team_schedule(
    abbrev: str,
    season: str = "2026-27",
    *,
    cache_dir: Path | str | None = DEFAULT_CACHE_DIR,
    max_age_s: float | None = SCHEDULE_CACHE_MAX_AGE_S,
    timeout: float = FETCH_TIMEOUT_S,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    budget: FetchBudget | None = None,
    response_sink: ResponseSink | None = None,
) -> dict:
    """Fetch one team's regular-season schedule payload (NETWORK, cached).

    ``GET .../teams/{id}/schedule?season=<ending year>&seasontype=2``;
    ``season`` is the ``YYYY-YY`` label (``"2026-27"`` -> ``season=2027``).
    Returns the parsed payload verbatim (raw payloads preserved before
    normalization). Fail-closed at the fetch boundary: the payload must
    echo ``requestedSeason`` with the requested year and type 2, and the
    payload's own ``team.abbreviation`` must equal ``abbrev`` (guards the
    frozen id table against silent ESPN id changes).

    M2 A4: ``allow_public_fetch`` defaults to the paper-only denial —
    no connection is built without an explicit ``True`` (checked before
    any cache access too). With a shared ``budget`` every attempt/wait
    is charged/clamped against the round and ``response_sink`` receives
    ``(url, raw_bytes)`` per successful bounded read.
    """
    require_public_fetch(allow_public_fetch)
    if abbrev not in NBA_TEAMS:
        raise ValueError(f"unknown NBA abbreviation {abbrev!r}")
    year = season_year(season)
    cache_path = None
    if cache_dir is not None:
        cache_path = Path(cache_dir) / f"schedule_{abbrev}_{year}.json"
    payload = _cached_fetch_json(
        _schedule_url(abbrev, year), cache_path, max_age_s,
        timeout=timeout, allow_public_fetch=allow_public_fetch,
        budget=budget, response_sink=response_sink,
    )
    requested = payload.get("requestedSeason")
    if not isinstance(requested, dict) or (
        requested.get("year") != year or requested.get("type") != 2
    ):
        raise EspnNbaSchemaError(
            f"schedule {abbrev} {season}: requestedSeason {requested!r} "
            f"does not echo year={year} type=2"
        )
    team = payload.get("team")
    if not isinstance(team, dict) or team.get("abbreviation") != abbrev:
        raise EspnNbaSchemaError(
            f"schedule {abbrev} {season}: payload team block "
            f"{(team or {}).get('abbreviation')!r} is not {abbrev!r}"
        )
    return payload


def fetch_game_summary(
    event_id: str | int,
    *,
    cache_dir: Path | str | None = DEFAULT_CACHE_DIR,
    timeout: float = FETCH_TIMEOUT_S,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    budget: FetchBudget | None = None,
    response_sink: ResponseSink | None = None,
) -> dict:
    """Fetch one game's summary payload (NETWORK; immutable once cached).

    ``GET .../summary?event=<id>`` — the per-game payload holding the
    header (teams/scores), the box score (25 team-stat groups incl. all
    four possession inputs) and the injury report. Summary payloads of
    completed games never change, so the cache entry has no expiry.
    Same M2 A4 permission/budget/``response_sink`` parameters as
    :func:`fetch_team_schedule`.
    """
    require_public_fetch(allow_public_fetch)
    eid = str(event_id)
    if not eid.isdigit():
        raise ValueError(f"event id must be numeric, got {event_id!r}")
    cache_path = None
    if cache_dir is not None:
        cache_path = Path(cache_dir) / f"summary_{eid}.json"
    payload = _cached_fetch_json(
        f"https://{HOST}{API_PATH}/summary?event={eid}",
        cache_path, None, timeout=timeout,
        allow_public_fetch=allow_public_fetch,
        budget=budget, response_sink=response_sink,
    )
    if "header" not in payload or "boxscore" not in payload:
        raise EspnNbaSchemaError(
            f"summary {eid}: missing header/boxscore (probable schema change)"
        )
    return payload


def fetch_nba_season(
    season: str = "2026-27",
    *,
    cache_dir: Path | str | None = DEFAULT_CACHE_DIR,
    schedule_max_age_s: float | None = SCHEDULE_CACHE_MAX_AGE_S,
    timeout: float = FETCH_TIMEOUT_S,
    teams: Collection[str] | None = None,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    budget: FetchBudget | None = None,
    response_sink: ResponseSink | None = None,
) -> dict:
    """Fetch every schedule + every completed game summary (NETWORK).

    Orchestrates :func:`fetch_team_schedule` over the frozen 30-team
    table (``teams`` narrows it to an explicit subset for bounded
    scan windows; ``None`` keeps the full frozen table), collects the
    COMPLETED events (each game appears in both teams' schedules;
    de-duplicated by event id) and fetches each summary exactly once
    through :func:`fetch_game_summary`. Returns the raw bundle consumed
    by :func:`build_snapshot`::

        {"season": "2026-27", "season_year": 2027,
         "generated_at": "<iso8601 now>", "schedules": {abbrev: payload},
         "summaries": {event_id: payload}}

    Unstarted games are simply absent from the bundle (the snapshot is
    a to-date backfill); a completed event without its summary fails
    closed inside :func:`build_snapshot`.

    M2 A4: ``allow_public_fetch`` (paper-only default ``False``) and
    the shared ``budget``/``response_sink`` propagate to every schedule
    and summary fetch. The M2 adapter path pins ``cache_dir=None``
    (zero disk-cache writes); the on-disk cache remains the legacy
    WP-NBA-1 ops option.
    """
    require_public_fetch(allow_public_fetch)
    year = season_year(season)
    if teams is None:
        table: Mapping[str, tuple[int, str]] = NBA_TEAMS
    else:
        unknown = sorted(set(teams) - set(NBA_TEAMS))
        if unknown:
            raise ValueError(f"unknown NBA abbreviation(s) {unknown!r}")
        table = {abbrev: NBA_TEAMS[abbrev] for abbrev in teams}
    schedules: dict[str, dict] = {}
    completed: dict[str, dict] = {}  # event_id -> normalized event row
    for abbrev in sorted(table):
        payload = fetch_team_schedule(
            abbrev, season,
            cache_dir=cache_dir, max_age_s=schedule_max_age_s, timeout=timeout,
            allow_public_fetch=allow_public_fetch,
            budget=budget, response_sink=response_sink,
        )
        schedules[abbrev] = payload
        for event in _completed_events(payload, abbrev):
            prior = completed.get(event["id"])
            if prior is None:
                completed[event["id"]] = event
            elif prior != event:
                raise EspnNbaSchemaError(
                    f"event {event['id']} disagrees between two schedules"
                )
    summaries: dict[str, dict] = {}
    for eid in sorted(completed, key=int):
        summaries[eid] = fetch_game_summary(
            eid, cache_dir=cache_dir, timeout=timeout,
            allow_public_fetch=allow_public_fetch,
            budget=budget, response_sink=response_sink,
        )
    return {
        "season": season,
        "season_year": year,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schedules": schedules,
        "summaries": summaries,
    }


# ---------------------------------------------------------------------------
# payload extraction (pure; fail-closed on shape drift)
# ---------------------------------------------------------------------------

def _nth_sunday(year: int, month: int, n: int) -> date:
    """Nth Sunday of a month (US DST rule arithmetic, 2007+ rules)."""
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _et_date(timestamp: str, eid: str) -> date:
    """Eastern-Time calendar date of an ESPN UTC instant.

    ESPN stamps true UTC instants (``2026-10-28T00:00Z`` = Tue Oct 27
    8pm ET); taking the string's date prefix would shift every 8pm+
    ET start to the next day and corrupt rest/b2b flags (real-data
    example: Detroit played Oct 27 @ NYK and Oct 28 vs CHA, a genuine
    back-to-back whose stamps share the prefix ``2026-10-28``). Shift
    by the US DST offset (EDT -4 between the second Sunday of March
    and the first Sunday of November, EST -5 otherwise); date-level
    rules are exact for NBA game times (transitions happen at 2am local).
    """
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EspnNbaSchemaError(
            f"event {eid}: malformed timestamp {timestamp!r}"
        ) from exc
    offset = timedelta(
        hours=-4 if _nth_sunday(moment.year, 3, 2) <= moment.date()
        < _nth_sunday(moment.year, 11, 1) else -5
    )
    return (moment + offset).date()


def _completed_events(payload: Mapping[str, object], abbrev: str) -> list[dict]:
    """Normalize a schedule payload's finished games.

    Returns ``[{"id", "date", "home": {abbrev, score},
    "away": {abbrev, score}}]`` for events whose competition status is
    ``completed`` with both scores present; anything structurally off
    (missing events list, malformed competition/competitor rows) raises.
    The per-team abbreviation is used only for error messages — the
    authoritative teams come from each event row itself.
    """
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise EspnNbaSchemaError(f"schedule {abbrev}: events missing/empty")
    rows: list[dict] = []
    for event in events:
        if not isinstance(event, dict) or "id" not in event:
            raise EspnNbaSchemaError(f"schedule {abbrev}: malformed event row")
        competitions = event.get("competitions")
        if not isinstance(competitions, list) or len(competitions) != 1:
            raise EspnNbaSchemaError(
                f"event {event.get('id')}: malformed competitions"
            )
        comp = competitions[0]
        status = comp.get("status")
        state = status.get("type", {}) if isinstance(status, dict) else {}
        if not isinstance(state, dict) or state.get("completed") is not True:
            continue
        raw_date = event.get("date")
        if not isinstance(raw_date, str) or not _DATE_RE.match(raw_date[:10]):
            raise EspnNbaSchemaError(
                f"event {event['id']}: malformed date {raw_date!r}"
            )
        game_date = _et_date(raw_date, str(event["id"]))
        competitors = comp.get("competitors")
        if not isinstance(competitors, list) or len(competitors) != 2:
            raise EspnNbaSchemaError(
                f"event {event['id']}: malformed competitors"
            )
        sides: dict[str, dict] = {}
        for competitor in competitors:
            if not isinstance(competitor, dict):
                raise EspnNbaSchemaError(
                    f"event {event['id']}: non-object competitor"
                )
            side = competitor.get("homeAway")
            team = competitor.get("team")
            if side not in ("home", "away") or side in sides:
                raise EspnNbaSchemaError(
                    f"event {event['id']}: bad homeAway {side!r}"
                )
            if not isinstance(team, dict) or not isinstance(
                team.get("abbreviation"), str
            ):
                raise EspnNbaSchemaError(
                    f"event {event['id']}: competitor without abbreviation"
                )
            sides[side] = {
                "abbrev": team["abbreviation"],
                "score": _parse_score(competitor.get("score"), event["id"]),
            }
        if "home" not in sides or "away" not in sides:
            raise EspnNbaSchemaError(f"event {event['id']}: missing side")
        rows.append({
            "id": str(event["id"]),
            "date": game_date.isoformat(),
            "home": sides["home"],
            "away": sides["away"],
        })
    return rows


def _parse_score(raw: object, event_id: str) -> int:
    """Score as a nonnegative int (schedule ``{"value": 109.0}``,
    summary header bare int, wire strings — all accepted; no rounding)."""
    if isinstance(raw, Mapping):
        raw = raw.get("value")
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise EspnNbaSchemaError(
            f"event {event_id}: score {raw!r} is not numeric"
        ) from exc
    if not math.isfinite(value) or value < 0.0 or value != int(value):
        raise EspnNbaSchemaError(
            f"event {event_id}: score {raw!r} is not a whole nonnegative number"
        )
    return int(value)


def _stat_int(display: object, name: str, abbrev: str, eid: str) -> int:
    """One box-stat displayValue as a nonnegative int ("16" -> 16)."""
    text = str(display)
    if not _SINGLE_STAT_RE.match(text):
        raise EspnNbaSchemaError(
            f"game {eid} {abbrev}: stat {name} has malformed value {display!r}"
        )
    return int(text)


def _stat_pair_attended(display: object, name: str, abbrev: str, eid: str) -> int:
    """Attempted half of a paired displayValue ("45-85" -> 85)."""
    text = str(display)
    match = _PAIRED_STAT_RE.match(text)
    if match is None:
        raise EspnNbaSchemaError(
            f"game {eid} {abbrev}: stat {name} has malformed pair {display!r}"
        )
    return int(match.group(2))


def _box_line(
    summary: Mapping[str, object], eid: str
) -> dict[str, dict[str, int]]:
    """``{"H"/"A" abbrev -> stat dict}`` possession inputs for one game.

    Extracts FGA (from ``fieldGoalsMade-fieldGoalsAttempted``), OREB
    (``offensiveRebounds``), TOV (``totalTurnovers``, falling back to
    ``turnovers`` when ESPN omits the split) and FTA (from
    ``freeThrowsMade-freeThrowsAttempted``) for both boxscore teams,
    cross-checked against the header's home/away abbreviations and
    scores. Finite/nonnegative discipline (codex review #8): every
    input must be a nonnegative integer; anything else is schema drift.
    """
    box = summary.get("boxscore")
    header = summary.get("header")
    if not isinstance(box, dict) or not isinstance(header, dict):
        raise EspnNbaSchemaError(f"game {eid}: missing boxscore/header")
    header_comps = header.get("competitions")
    if not isinstance(header_comps, list) or not header_comps:
        raise EspnNbaSchemaError(f"game {eid}: missing header competition")
    header_teams = header_comps[0].get("competitors")
    if not isinstance(header_teams, list) or len(header_teams) != 2:
        raise EspnNbaSchemaError(f"game {eid}: malformed header competitors")
    by_side: dict[str, dict[str, object]] = {}
    header_scores: dict[str, int] = {}
    for competitor in header_teams:
        side = competitor.get("homeAway")
        team = competitor.get("team")
        if not isinstance(competitor, dict) or side not in ("home", "away") or (
            not isinstance(team, dict)
            or not isinstance(team.get("abbreviation"), str)
        ):
            raise EspnNbaSchemaError(f"game {eid}: malformed header team")
        header_scores[side] = _parse_score(competitor.get("score"), eid)
        by_side[side] = {"abbrev": team["abbreviation"]}
    teams = box.get("teams")
    if not isinstance(teams, list) or len(teams) != 2:
        raise EspnNbaSchemaError(f"game {eid}: boxscore.teams malformed")
    lines: dict[str, dict[str, int]] = {}
    for entry in teams:
        if not isinstance(entry, dict):
            raise EspnNbaSchemaError(f"game {eid}: non-object boxscore team")
        side = entry.get("homeAway")
        team = entry.get("team")
        if side not in ("home", "away") or (
            not isinstance(team, dict)
            or not isinstance(team.get("abbreviation"), str)
        ):
            raise EspnNbaSchemaError(f"game {eid}: malformed boxscore team")
        abbrev = team["abbreviation"]
        if by_side.get(side, {}).get("abbrev") != abbrev:
            raise EspnNbaSchemaError(
                f"game {eid}: boxscore {side} {abbrev} != header "
                f"{by_side.get(side, {}).get('abbrev')}"
            )
        stats = entry.get("statistics")
        if not isinstance(stats, list):
            raise EspnNbaSchemaError(f"game {eid} {abbrev}: no statistics list")
        table: dict[str, str] = {}
        for group in stats:
            if isinstance(group, dict) and isinstance(group.get("name"), str):
                table[group["name"]] = str(group.get("displayValue"))
        for required in (
            "fieldGoalsMade-fieldGoalsAttempted",
            "freeThrowsMade-freeThrowsAttempted",
            "offensiveRebounds",
        ):
            if required not in table:
                raise EspnNbaSchemaError(
                    f"game {eid} {abbrev}: missing stat group {required!r}"
                )
        tov_name = (
            "totalTurnovers" if "totalTurnovers" in table else "turnovers"
        )
        if tov_name not in table:
            raise EspnNbaSchemaError(
                f"game {eid} {abbrev}: missing stat group 'totalTurnovers'"
            )
        lines[abbrev] = {
            "fga": _stat_pair_attended(
                table["fieldGoalsMade-fieldGoalsAttempted"],
                "FGA", abbrev, eid,
            ),
            "oreb": _stat_int(table["offensiveRebounds"], "OREB", abbrev, eid),
            "tov": _stat_int(table[tov_name], "TOV", abbrev, eid),
            "fta": _stat_pair_attended(
                table["freeThrowsMade-freeThrowsAttempted"],
                "FTA", abbrev, eid,
            ),
            "pts": header_scores[side],
        }
    return lines


def _injuries_from_summary(
    summary: Mapping[str, object]
) -> dict[str, list[dict[str, str]]]:
    """``{abbrev: [{player, status, date}]}`` from one summary payload.

    ESPN shape: ``injuries: [{"team": {abbreviation},
    "injuries": [{status, date, athlete: {fullName}}]}]``. A missing
    ``injuries`` key means "no report" (empty); structurally malformed
    entries are schema drift and raise.
    """
    raw = summary.get("injuries")
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise EspnNbaSchemaError("summary injuries is not a list")
    report: dict[str, list[dict[str, str]]] = {}
    for block in raw:
        if not isinstance(block, dict):
            raise EspnNbaSchemaError("summary injuries block is not an object")
        team = block.get("team")
        entries = block.get("injuries")
        if not isinstance(team, dict) or not isinstance(
            team.get("abbreviation"), str
        ) or not isinstance(entries, list):
            raise EspnNbaSchemaError(
                f"summary injuries block for {team!r} is malformed"
            )
        rows: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise EspnNbaSchemaError("summary injuries entry is not an object")
            athlete = entry.get("athlete")
            status = entry.get("status")
            when = entry.get("date")
            if not isinstance(athlete, dict) or not isinstance(
                athlete.get("fullName"), str
            ) or not isinstance(status, str) or not isinstance(when, str):
                raise EspnNbaSchemaError(
                    "summary injuries entry missing player/status/date"
                )
            rows.append({
                "player": athlete["fullName"],
                "status": status,
                "date": when,
            })
        report[team["abbreviation"]] = rows
    return report


def _possessions(line: Mapping[str, int]) -> float:
    """Dean Oliver estimate from one team's own box line (float)."""
    return float(
        line["fga"] - line["oreb"] + line["tov"] + FT_COEFFICIENT * line["fta"]
    )


# ---------------------------------------------------------------------------
# snapshot build (pure, fail-closed) + load + write
# ---------------------------------------------------------------------------

def build_snapshot(
    raw: Mapping[str, object],
    *,
    season: str | None = None,
    expected_teams: Collection[str] | None = None,
) -> dict:
    """Transform a :func:`fetch_nba_season` bundle into the frozen snapshot.

    PURE (no network). Fail-closed: the bundle must carry the season
    label, generated_at, one validated schedule per expected team (30
    NBA teams by default; pass ``expected_teams`` for synthetic subsets)
    and a summary for every completed event; box rows must parse with
    finite nonnegative inputs and strictly positive possessions;
    schedule and summary must agree on teams and scores; per-team game
    counts stay within :data:`MAX_GAMES_PER_TEAM` with no duplicate
    dates. On any violation an :class:`EspnNbaSchemaError` names the
    condition and nothing is returned.
    """
    if not isinstance(raw, Mapping):
        raise EspnNbaSchemaError("bundle must be a mapping")
    bundle_season = raw.get("season")
    generated_at = raw.get("generated_at")
    if not isinstance(bundle_season, str):
        raise EspnNbaSchemaError("bundle has no season label")
    if season is not None and season != bundle_season:
        raise EspnNbaSchemaError(
            f"bundle season {bundle_season!r} != requested {season!r}"
        )
    try:
        season_year(bundle_season)
    except ValueError as exc:
        raise EspnNbaSchemaError(f"bundle season label: {exc}") from exc
    if not isinstance(generated_at, str) or not generated_at:
        raise EspnNbaSchemaError("bundle has no generated_at")
    schedules = raw.get("schedules")
    summaries = raw.get("summaries")
    if not isinstance(schedules, Mapping) or not schedules:
        raise EspnNbaSchemaError("bundle has no schedules mapping")
    if not isinstance(summaries, Mapping):
        raise EspnNbaSchemaError("bundle has no summaries mapping")

    if expected_teams is None:
        expected_teams = set(NBA_TEAMS)
    expected = set(expected_teams)
    unknown = set(schedules) - expected
    if unknown:
        raise EspnNbaSchemaError(
            f"bundle schedules {sorted(unknown)} outside expected teams"
        )
    missing = expected - set(schedules)
    if missing:
        raise EspnNbaSchemaError(
            f"bundle missing schedules for {sorted(missing)}"
        )

    # completed events, de-duplicated across the two participating teams
    events: dict[str, dict] = {}
    for abbrev in sorted(expected):
        payload = schedules[abbrev]
        if not isinstance(payload, Mapping):
            raise EspnNbaSchemaError(f"schedule for {abbrev} is not an object")
        for event in _completed_events(payload, abbrev):
            prior = events.get(event["id"])
            if prior is None:
                events[event["id"]] = event
            elif prior != event:
                raise EspnNbaSchemaError(
                    f"event {event['id']} disagrees between two schedules"
                )

    games: dict[str, list[dict]] = {abbrev: [] for abbrev in expected}
    displays: dict[str, str] = {}
    for abbrev in sorted(expected):
        team_block = schedules[abbrev].get("team")
        if not isinstance(team_block, dict) or not isinstance(
            team_block.get("displayName"), str
        ):
            raise EspnNbaSchemaError(f"schedule {abbrev}: no displayName")
        displays[abbrev] = team_block["displayName"]

    injuries_raw: dict[str, list[dict[str, str]]] = {}
    latest_team_event: dict[str, tuple[str, str]] = {}  # abbrev -> (date, id)
    injury_reports: dict[str, dict[str, list[dict[str, str]]]] = {}
    for eid in sorted(events, key=int):
        event = events[eid]
        summary = summaries.get(eid)
        if not isinstance(summary, Mapping):
            raise EspnNbaSchemaError(
                f"bundle has no summary for completed event {eid} "
                "(refetch with fetch_nba_season)"
            )
        injury_reports[eid] = _injuries_from_summary(summary)
        lines = _box_line(summary, eid)
        home_abbrev, away_abbrev = event["home"]["abbrev"], event["away"]["abbrev"]
        if set(lines) != {home_abbrev, away_abbrev}:
            raise EspnNbaSchemaError(
                f"game {eid}: box teams {sorted(lines)} != schedule "
                f"{sorted((home_abbrev, away_abbrev))}"
            )
        for side, own, opp in (
            ("home", home_abbrev, away_abbrev),
            ("away", away_abbrev, home_abbrev),
        ):
            schedule_score = event[side]["score"]
            if lines[own]["pts"] != schedule_score:
                raise EspnNbaSchemaError(
                    f"game {eid}: summary {own} pts {lines[own]['pts']} != "
                    f"schedule {schedule_score}"
                )
            poss = _possessions(lines[own])
            if not math.isfinite(poss) or poss <= 0.0:
                raise EspnNbaSchemaError(
                    f"game {eid} {own}: possession estimate {poss!r} is not "
                    "finite positive"
                )
            pts, opp_pts = lines[own]["pts"], lines[opp]["pts"]
            games[own].append({
                "date": event["date"],
                "home": side == "home",
                "opponent": opp,
                "poss": poss,
                "pts": pts,
                "opp_pts": opp_pts,
                "ortg": 100.0 * pts / poss,
                "drtg": 100.0 * opp_pts / poss,
                "rest_days": None,  # filled after per-team sorting
                "b2b": False,
            })
        for abbrev in (home_abbrev, away_abbrev):
            prior = latest_team_event.get(abbrev)
            if prior is None or (event["date"], eid) > prior:
                latest_team_event[abbrev] = (event["date"], eid)

    # latest injury report per team = report embedded in that team's
    # latest-dated game summary (a player listed earlier who has since
    # returned is absent from the latest report and must not persist)
    injuries: dict[str, list[dict[str, str]]] = {}
    for abbrev, (_when, eid) in latest_team_event.items():
        report = injury_reports[eid].get(abbrev)
        if report is not None:
            injuries[abbrev] = report

    teams_out: dict[str, dict] = {}
    for abbrev in sorted(games):
        rows = sorted(
            games[abbrev],
            key=lambda row: (row["date"], row["opponent"]),
        )
        if len(rows) > MAX_GAMES_PER_TEAM:
            raise EspnNbaSchemaError(
                f"{abbrev}: {len(rows)} games exceeds sanity ceiling "
                f"{MAX_GAMES_PER_TEAM}"
            )
        previous: date | None = None
        for row in rows:
            if not _DATE_RE.match(row["date"]):
                raise EspnNbaSchemaError(f"{abbrev}: bad date {row['date']!r}")
            current = date.fromisoformat(row["date"])
            if previous is not None and current <= previous:
                raise EspnNbaSchemaError(
                    f"{abbrev}: duplicate/non-increasing date {row['date']}"
                )
            rest = None if previous is None else (current - previous).days
            row["rest_days"] = rest
            row["b2b"] = rest == 1
            previous = current
        teams_out[abbrev] = {"display": displays[abbrev], "games": rows}

    injuries_out = {
        abbrev: sorted(injuries[abbrev], key=lambda row: row["player"])
        for abbrev in sorted(injuries)
    }
    return {
        "generated_at": generated_at,
        "source": SOURCE_KEY,
        "season": bundle_season,
        "teams": teams_out,
        "injuries": injuries_out,
    }


def _validate_snapshot(
    snapshot: object,
    *,
    expected_teams: Collection[str] | None = None,
) -> dict:
    """Shared structural validation for :func:`load_nba_snapshot`."""
    if not isinstance(snapshot, Mapping):
        raise ValueError("NBA snapshot root must be a JSON object")
    keys = set(snapshot)
    if keys != _SNAPSHOT_KEYS:
        raise ValueError(
            f"snapshot keys {sorted(keys)} != frozen contract "
            f"{sorted(_SNAPSHOT_KEYS)}"
        )
    if snapshot["source"] != SOURCE_KEY:
        raise ValueError(f"snapshot source {snapshot['source']!r} != {SOURCE_KEY!r}")
    season_year(str(snapshot["season"]))  # raises on malformed label
    generated_at = snapshot["generated_at"]
    if not isinstance(generated_at, str) or not generated_at:
        raise ValueError("snapshot generated_at must be a non-empty string")
    try:
        datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"snapshot generated_at {generated_at!r} is not iso8601"
        ) from exc
    teams = snapshot["teams"]
    if not isinstance(teams, Mapping) or not teams:
        raise ValueError("snapshot teams must be a non-empty object")
    expected = set(NBA_TEAMS) if expected_teams is None else set(expected_teams)
    if set(teams) != expected:
        raise ValueError(
            f"snapshot teams {sorted(teams)} != expected {sorted(expected)}"
        )
    for abbrev, block in teams.items():
        if not isinstance(block, Mapping) or set(block) != {"display", "games"}:
            raise ValueError(f"team {abbrev}: block keys must be display/games")
        if not isinstance(block["display"], str) or not block["display"]:
            raise ValueError(f"team {abbrev}: display must be non-empty")
        rows = block["games"]
        if not isinstance(rows, list):
            raise ValueError(f"team {abbrev}: games must be a list")
        if len(rows) > MAX_GAMES_PER_TEAM:
            raise ValueError(
                f"team {abbrev}: {len(rows)} games exceeds {MAX_GAMES_PER_TEAM}"
            )
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != _GAME_KEYS:
                raise ValueError(
                    f"team {abbrev}: game keys {sorted(row) if isinstance(row, Mapping) else row!r} "
                    f"!= frozen contract {sorted(_GAME_KEYS)}"
                )
            if row["opponent"] not in expected:
                raise ValueError(
                    f"team {abbrev}: unknown opponent {row['opponent']!r}"
                )
            if row["opponent"] == abbrev:
                raise ValueError(f"team {abbrev}: plays itself")
            if type(row["home"]) is not bool or type(row["b2b"]) is not bool:
                raise ValueError(f"team {abbrev}: home/b2b must be booleans")
            if type(row["pts"]) is not int or type(row["opp_pts"]) is not int:
                raise ValueError(f"team {abbrev}: pts/opp_pts must be ints")
            if row["pts"] < 0 or row["opp_pts"] < 0:
                raise ValueError(f"team {abbrev}: negative points")
            rest = row["rest_days"]
            if rest is not None and (type(rest) is not int or rest < 0):
                raise ValueError(
                    f"team {abbrev}: rest_days must be a nonnegative int or null"
                )
            if not _DATE_RE.match(row["date"]):
                raise ValueError(f"team {abbrev}: bad date {row['date']!r}")
            date.fromisoformat(row["date"])
            for field in ("poss", "ortg", "drtg"):
                value = row[field]
                if type(value) not in (int, float) or isinstance(value, bool):
                    raise ValueError(f"team {abbrev}: {field} must be numeric")
                value = float(value)
                if not math.isfinite(value) or value < 0.0:
                    raise ValueError(
                        f"team {abbrev}: {field} {row[field]!r} is not finite "
                        "nonnegative"
                    )
            if float(row["poss"]) <= 0.0:
                raise ValueError(
                    f"team {abbrev}: poss must be strictly positive "
                    f"(got {row['poss']!r})"
                )
    injuries = snapshot["injuries"]
    if not isinstance(injuries, Mapping):
        raise ValueError("snapshot injuries must be an object")
    for abbrev, report in injuries.items():
        if abbrev not in expected:
            raise ValueError(f"injuries: unknown team {abbrev!r}")
        if not isinstance(report, list):
            raise ValueError(f"injuries {abbrev}: must be a list")
        for entry in report:
            if not isinstance(entry, Mapping) or set(entry) != {
                "player", "status", "date"
            }:
                raise ValueError(
                    f"injuries {abbrev}: entry keys must be player/status/date"
                )
            for field in ("player", "status", "date"):
                if not isinstance(entry[field], str) or not entry[field]:
                    raise ValueError(
                        f"injuries {abbrev}: {field} must be a non-empty string"
                    )
    return snapshot  # type: ignore[return-value]


def load_nba_snapshot(
    path: str,
    *,
    expected_teams: Collection[str] | None = None,
) -> dict:
    """Load and validate a frozen-contract NBA snapshot (PURE, no network).

    Structural fail-closed validation mirroring the build boundary:
    exact frozen key sets, 30 NBA teams by default (``expected_teams``
    overrides for synthetic subsets), 0-:data:`MAX_GAMES_PER_TEAM`
    games per team, nonnegative int points, boolean home/b2b, int-or-
    null nonnegative rest_days, and finite nonnegative poss/ortg/drtg
    with poss strictly positive (``json.load`` accepts bare ``NaN`` /
    ``Infinity`` literals — those are rejected here, codex review #8
    discipline). Validation only rejects; accepted payloads are
    returned unmodified.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return _validate_snapshot(payload, expected_teams=expected_teams)


def write_snapshot(
    snapshot: Mapping[str, object],
    path: str | Path,
    *,
    expected_teams: Collection[str] | None = None,
) -> Path:
    """Validate, then atomically write a snapshot as deterministic JSON.

    ``allow_nan=False`` makes the write itself the last finite-value
    gate; ``sort_keys=True`` gives byte-deterministic output (raw
    payload discipline: the snapshot is emitted exactly as validated).
    ``expected_teams`` mirrors :func:`load_nba_snapshot` (the 30 NBA
    teams by default; synthetic subsets for tests).
    """
    _validate_snapshot(snapshot, expected_teams=expected_teams)
    target = Path(path)
    _atomic_write_json(target, snapshot)
    return target


# ---------------------------------------------------------------------------
# backfill entry point (WP-NBA-1 ops; writes OUTSIDE the repo)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI: backfill one season to date and write a snapshot file.

    ``python -m polymarket_alpha_lab.papersim.espn_nba --out PATH
    [--season 2026-27] [--cache-dir DIR] [--schedule-max-age-s SEC]
    --allow-public-fetch``.
    Fetches through the on-disk cache, builds the fail-closed snapshot,
    validates it again through :func:`load_nba_snapshot` and writes it
    atomically. Prints team/game/injury counts as evidence. The module
    is stdlib-only, so the same file runs standalone on the ops host.

    M2 A4: the network backfill now requires the explicit
    ``--allow-public-fetch`` opt-in (paper-only default: denied). This
    CLI stays the LEGACY WP-NBA-1 ops path; M2 rounds go through
    ``papersim.adapters.sports`` with ``cache_dir=None``.
    """
    parser = argparse.ArgumentParser(
        description="ESPN NBA snapshot backfill (paper-only, no credentials)"
    )
    parser.add_argument("--out", required=True, help="snapshot output path")
    parser.add_argument("--season", default="2026-27", help="YYYY-YY label")
    parser.add_argument(
        "--cache-dir", default=str(DEFAULT_CACHE_DIR),
        help="raw payload cache dir (empty string disables caching)",
    )
    parser.add_argument(
        "--schedule-max-age-s", type=float, default=SCHEDULE_CACHE_MAX_AGE_S,
        help="schedule cache freshness window in seconds",
    )
    parser.add_argument(
        "--allow-public-fetch", action="store_true",
        help="explicit network permission (paper-only default: denied)",
    )
    args = parser.parse_args(argv)
    cache_dir: Path | str | None = args.cache_dir
    if cache_dir == "":
        cache_dir = None
    raw = fetch_nba_season(
        args.season,
        cache_dir=cache_dir,
        schedule_max_age_s=args.schedule_max_age_s,
        allow_public_fetch=args.allow_public_fetch,
    )
    snapshot = build_snapshot(raw, season=args.season)
    target = write_snapshot(snapshot, args.out)
    reloaded = load_nba_snapshot(str(target))
    team_count = len(reloaded["teams"])
    game_count = sum(
        len(block["games"]) for block in reloaded["teams"].values()
    )
    injury_count = sum(
        len(report) for report in reloaded["injuries"].values()
    )
    print(
        f"nba snapshot {target}: season={reloaded['season']} "
        f"teams={team_count} games={game_count} injured_players={injury_count} "
        f"generated_at={reloaded['generated_at']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI guard
    raise SystemExit(main())
