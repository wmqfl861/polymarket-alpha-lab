"""A4 sports scan inputs: bounded FotMob / ESPN NBA reads (m2 FX-15).

Single responsibility (M2 plan section 2.5): give the scan
side ONE bounded, permission-gated entrance per sports source —
:func:`fetch_fotmob_inputs` and :func:`fetch_nba_inputs` — over the
existing frozen fetch/transform modules
(:mod:`polymarket_alpha_lab.papersim.fotmob`,
:mod:`polymarket_alpha_lab.papersim.espn_nba`). This module adds no
transport of its own: bounded reads, origin allowlists, framing
checks, per-attempt budget charging and deadline-clamped waits live in
those two modules' M2 transport layer; the transforms, dedup,
cancelled-match, skip-rate, team-count, season and value checks are
frozen there and are NOT re-implemented or relaxed here.

Boundary flags (project iron rule 3 and the A0 contract):

- ``PAPER_ONLY`` / ``REPORT_ONLY`` / ``READONLY`` are ``True``; these
  functions only produce research inputs and evidence.
- No credentials, no cookies, no private keys; public unauthenticated
  endpoints only.
- No database: this module opens no sessions. Raw evidence is
  returned in-memory to the caller (PostgreSQL snapshot registration
  arrives with the S0+ storage wiring, not here).
- No disk writes: the ESPN path pins ``cache_dir=None`` (the legacy
  WP-NBA-1 on-disk cache stays an ops-only option), and FotMob has no
  cache at all.
- Every entry point takes the explicit ``allow_public_fetch`` paper
  permission (default ``False``); denial is returned as
  :attr:`AdapterStatus.NETWORK_DENIED` before any connection is built.
- Failures are machine-readable: fixed short ``reason_code`` strings
  only — never URLs, query parameters, headers, upstream exception
  text or response bodies.

NBA SHADOW POSITIONING: :func:`fetch_nba_inputs` exists so shadow
comparison and research can run on ESPN data. A usable NBA data
adapter does NOT authorize NBA entry — the value it returns carries
``shadow=True``, and the frozen entry policy elsewhere in papersim is
untouched by this module.

M2 scope contract (deterministic, bounded windows):

- FotMob ``scope``: a mapping with ``league`` (a
  :class:`~polymarket_alpha_lab.papersim.fotmob.FotMobLeague` member,
  or a mapping ``{"id": int, "key": str, "max_round": int|None,
  "team_count": int|None}``), a required integer-year ``season`` key
  (``"2026"`` -> wire season ``"2026/2027"``, the same derivation the
  frozen transform uses), and an optional ``match_ids`` list of
  explicit match-id windows (kept matches only). Without
  ``match_ids`` every KEPT match of the league payload gets one
  matchDetails fetch (unfinished/playoff/cancelled rows are excluded
  by the frozen partition, not silently refetched).
- ESPN NBA ``scope``: a mapping with a required ``YYYY-YY`` ``season``
  (``"2026-27"``) and an optional ``teams`` list of known
  abbreviations (``None`` -> the frozen 30-team table).

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Collection, Mapping

from polymarket_alpha_lab import public_http
from polymarket_alpha_lab.papersim import espn_nba, fotmob
from polymarket_alpha_lab.papersim.adapters.contracts import (
    AdapterResult,
    AdapterStatus,
    BudgetExhaustedError,
    DEFAULT_ALLOW_PUBLIC_FETCH,
    FetchBudget,
    FetchEvidence,
    FetchPermissionError,
    PAPER_ONLY,
    READONLY,
    REASON_DEADLINE_EXCEEDED,
    REASON_PUBLIC_FETCH_NOT_ALLOWED,
    REPORT_ONLY,
    require_public_fetch,
)

__all__ = [
    "PAPER_ONLY",
    "READONLY",
    "REPORT_ONLY",
    "REASON_BUDGET_INVALID",
    "REASON_ORIGIN_REJECTED",
    "REASON_PUBLIC_BODY_REJECTED",
    "REASON_SCOPE_INVALID",
    "REASON_SOURCE_SCHEMA_DRIFT",
    "REASON_UPSTREAM_UNAVAILABLE",
    "SOURCE_KIND_ESPN_NBA",
    "SOURCE_KIND_FOTMOB",
    "fetch_fotmob_inputs",
    "fetch_nba_inputs",
]

#: Evidence source kinds (also the ``source_kind`` of every evidence row).
SOURCE_KIND_FOTMOB = "fotmob"
SOURCE_KIND_ESPN_NBA = "espn-nba"

# A4-local fixed reason codes (closed vocabulary; never URLs/bodies).
REASON_SCOPE_INVALID = "scope_invalid"
REASON_BUDGET_INVALID = "budget_invalid"
REASON_SOURCE_SCHEMA_DRIFT = "source_schema_drift"
REASON_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
REASON_ORIGIN_REJECTED = "origin_rejected"
REASON_PUBLIC_BODY_REJECTED = "public_body_rejected"

_EVIDENCE = list[FetchEvidence]
_Sink = Callable[[str, bytes], None]


def _make_sink(source_kind: str, evidence: _EVIDENCE, register_response=None) -> _Sink:
    """Capture one :class:`FetchEvidence` per successful bounded read.

    ``fetched_at`` is stamped at observation time; ESPN/FotMob payloads
    carry no trustworthy upstream observation timestamp, so
    ``source_observed_at`` stays ``None`` (unknown, never faked).
    """

    def sink(url: str, raw_bytes: bytes) -> None:
        observation = FetchEvidence.from_bytes(
            source_kind=source_kind,
            source_reference=url,
            fetched_at=datetime.now(timezone.utc),
            raw_bytes=raw_bytes,
        )
        if register_response is not None:
            register_response(observation)
        evidence.append(observation)

    return sink


def _failure_from_exception(
    exc: BaseException, evidence: _EVIDENCE, *, espn: bool
) -> AdapterResult:
    """Map one transport/transform exception to a fixed-code failure."""
    bank_error = espn_nba.EspnNbaError if espn else fotmob.FotMobError
    if isinstance(exc, FetchPermissionError):
        return AdapterResult.failure(
            AdapterStatus.NETWORK_DENIED,
            REASON_PUBLIC_FETCH_NOT_ALLOWED,
            tuple(evidence),
        )
    if isinstance(exc, BudgetExhaustedError):
        status = (
            AdapterStatus.DEADLINE_EXCEEDED
            if exc.reason_code == REASON_DEADLINE_EXCEEDED
            else AdapterStatus.BUDGET_EXHAUSTED
        )
        return AdapterResult.failure(status, exc.reason_code, tuple(evidence))
    if isinstance(exc, public_http.PublicBodyError):
        return AdapterResult.failure(
            AdapterStatus.UPSTREAM_ERROR,
            REASON_PUBLIC_BODY_REJECTED,
            tuple(evidence),
        )
    if isinstance(exc, espn_nba.EspnNbaOriginError) or isinstance(
        exc, fotmob.FotMobOriginError
    ):
        return AdapterResult.failure(
            AdapterStatus.UPSTREAM_ERROR, REASON_ORIGIN_REJECTED, tuple(evidence)
        )
    if isinstance(exc, (espn_nba.EspnNbaSchemaError, fotmob.FotMobSchemaError)):
        return AdapterResult.failure(
            AdapterStatus.UPSTREAM_ERROR,
            REASON_SOURCE_SCHEMA_DRIFT,
            tuple(evidence),
        )
    if isinstance(exc, bank_error):
        return AdapterResult.failure(
            AdapterStatus.UPSTREAM_ERROR,
            REASON_UPSTREAM_UNAVAILABLE,
            tuple(evidence),
        )
    raise exc


def _validate_budget(budget: object) -> AdapterResult | None:
    """Refuse anything that is not the shared A0 budget object."""
    if isinstance(budget, FetchBudget):
        return None
    return AdapterResult.failure(
        AdapterStatus.UNSUPPORTED, REASON_BUDGET_INVALID
    )


def fetch_fotmob_inputs(
    scope: Mapping[str, object],
    *,
    budget: FetchBudget,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
    register_response=None,
) -> AdapterResult:
    """Fetch one FotMob league-season bundle and transform it (bounded).

    One league fixtures fetch plus one matchDetails fetch per KEPT
    match (or per explicit ``scope["match_ids"]`` window), all through
    the frozen :mod:`...papersim.fotmob` fetch/transform pair with the
    M2 bounded transport: origin allowlist, framing-checked bounded
    reads, per-attempt budget charging and deadline-clamped waits.

    SUCCESS value shape::

        {"source_kind": "fotmob", "league_key": str, "season_key": str,
         "snapshot": {f"{league_key}_{season_key}": <Understat block>}}

    ``snapshot`` is the exact :func:`fotmob.fotmob_to_understat` output
    (frozen wire shape, byte-identical for identical payloads);
    ``evidence`` holds one :class:`FetchEvidence` per HTTP response.

    Failures return fixed ``reason_code`` strings; partial evidence is
    kept. ``None`` transforms to INSUFFICIENT_DATA never happen here:
    the frozen transform either emits the block or fails closed.
    """
    evidence: _EVIDENCE = []
    invalid_budget = _validate_budget(budget)
    if invalid_budget is not None:
        return invalid_budget
    try:
        require_public_fetch(allow_public_fetch)
        if not isinstance(scope, Mapping):
            return AdapterResult.failure(
                AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
            )
        league_spec = scope.get("league")
        season_key = scope.get("season")

        if isinstance(league_spec, fotmob.FotMobLeague):
            league_ref: int | fotmob.FotMobLeague = league_spec
            transform_key: object = league_spec
            league_key = league_spec.league_key
            max_round: int | None = league_spec.max_round
            team_count: int | None = league_spec.team_count
        elif isinstance(league_spec, Mapping):
            league_id = league_spec.get("id")
            key = league_spec.get("key")
            if type(league_id) is not int or type(key) is not str or not key:
                return AdapterResult.failure(
                    AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
                )
            league_ref = league_id
            transform_key = key
            league_key = key
            raw_round = league_spec.get("max_round", None)
            raw_count = league_spec.get("team_count", None)
            if (raw_round is not None and type(raw_round) is not int) or (
                raw_count is not None and type(raw_count) is not int
            ):
                return AdapterResult.failure(
                    AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
                )
            max_round = raw_round
            team_count = raw_count
        else:
            return AdapterResult.failure(
                AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
            )

        if type(season_key) is not str or not season_key:
            return AdapterResult.failure(
                AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
            )
        try:
            int(season_key)
        except ValueError:
            return AdapterResult.failure(
                AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
            )
        season_wire = fotmob._expected_season(season_key)

        match_window = scope.get("match_ids", None)
        wanted: set[str] | None = None
        if match_window is not None:
            if not isinstance(match_window, (list, tuple)):
                return AdapterResult.failure(
                    AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
                )
            wanted = set()
            for mid in match_window:
                if isinstance(mid, int):
                    mid = str(mid)
                if type(mid) is not str or not mid:
                    return AdapterResult.failure(
                        AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
                    )
                wanted.add(mid)

        sink = _make_sink(SOURCE_KIND_FOTMOB, evidence, register_response)
        fixtures_payload = fotmob.fetch_fotmob_league(
            league_ref,
            season_wire,
            allow_public_fetch=allow_public_fetch,
            budget=budget,
            response_sink=sink,
        )
        # Same frozen partition the transform itself runs, so the set
        # of matchDetails fetches is exactly the set it consumes.
        matches = fotmob._parse_league_payload(fixtures_payload, season_key)
        partition_round = 46 if max_round is None else max_round
        parts = fotmob._partition_matches(matches, partition_round)
        kept_ids = [str(match["id"]) for match in parts["kept"]]
        if wanted is not None:
            kept_ids = [mid for mid in kept_ids if mid in wanted]
            if not kept_ids:
                return AdapterResult.failure(
                    AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID,
                    tuple(evidence),
                )
        details: dict[str, Mapping[str, object]] = {}
        for mid in kept_ids:
            details[mid] = fotmob.fetch_match_details(
                mid,
                allow_public_fetch=allow_public_fetch,
                budget=budget,
                response_sink=sink,
            )
        snapshot = fotmob.fotmob_to_understat(
            {"fixtures": fixtures_payload, "details": details},
            transform_key,
            season_key,
            max_round=max_round,
            team_count=team_count,
        )
        return AdapterResult.success(
            {
                "source_kind": SOURCE_KIND_FOTMOB,
                "league_key": league_key,
                "season_key": season_key,
                "snapshot": snapshot,
            },
            tuple(evidence),
        )
    except (
        FetchPermissionError,
        BudgetExhaustedError,
        public_http.PublicBodyError,
        fotmob.FotMobOriginError,
        fotmob.FotMobError,
    ) as exc:
        return _failure_from_exception(exc, evidence, espn=False)


def fetch_nba_inputs(
    scope: Mapping[str, object],
    *,
    budget: FetchBudget,
    allow_public_fetch: bool = DEFAULT_ALLOW_PUBLIC_FETCH,
) -> AdapterResult:
    """Fetch one ESPN NBA season window and build the frozen snapshot.

    SHADOW ONLY: this data adapter being usable does NOT authorize NBA
    entry. The success value carries ``shadow=True`` so downstream
    shadow comparison can select it and the production entry path can
    ignore it; the frozen entry policy elsewhere in papersim is not
    changed by this function.

    Every schedule and summary fetch goes through the frozen
    :mod:`...papersim.espn_nba` fetch/transform pair with the M2
    bounded transport AND ``cache_dir=None`` — the M2 path performs
    ZERO disk-cache writes (the legacy WP-NBA-1 on-disk cache stays an
    ops-only option; PostgreSQL snapshots arrive with the S0+ storage
    wiring, not here).

    SUCCESS value shape::

        {"source_kind": "espn-nba", "shadow": True, "season": str,
         "snapshot": <frozen NBA snapshot>}

    ``snapshot`` is the exact :func:`espn_nba.build_snapshot` output
    (frozen contract, byte-identical for identical payloads);
    ``evidence`` holds one :class:`FetchEvidence` per HTTP response.
    """
    evidence: _EVIDENCE = []
    invalid_budget = _validate_budget(budget)
    if invalid_budget is not None:
        return invalid_budget
    try:
        require_public_fetch(allow_public_fetch)
        if not isinstance(scope, Mapping):
            return AdapterResult.failure(
                AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
            )
        season = scope.get("season")
        if type(season) is not str or not season:
            return AdapterResult.failure(
                AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
            )
        try:
            espn_nba.season_year(season)
        except ValueError:
            return AdapterResult.failure(
                AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
            )
        teams_spec = scope.get("teams", None)
        expected_teams: Collection[str] | None = None
        if teams_spec is not None:
            if not isinstance(teams_spec, (list, tuple, set, frozenset)):
                return AdapterResult.failure(
                    AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
                )
            abbrevs = sorted(set(teams_spec))
            if not abbrevs or any(
                abbrev not in espn_nba.NBA_TEAMS for abbrev in abbrevs
            ):
                return AdapterResult.failure(
                    AdapterStatus.UNSUPPORTED, REASON_SCOPE_INVALID
                )
            expected_teams = abbrevs

        sink = _make_sink(SOURCE_KIND_ESPN_NBA, evidence)
        raw = espn_nba.fetch_nba_season(
            season,
            cache_dir=None,  # M2 path: no disk cache, in-memory only
            allow_public_fetch=allow_public_fetch,
            budget=budget,
            response_sink=sink,
            teams=expected_teams,
        )
        snapshot = espn_nba.build_snapshot(
            raw, season=season, expected_teams=expected_teams
        )
        return AdapterResult.success(
            {
                "source_kind": SOURCE_KIND_ESPN_NBA,
                "shadow": True,
                "season": season,
                "snapshot": snapshot,
            },
            tuple(evidence),
        )
    except (
        FetchPermissionError,
        BudgetExhaustedError,
        public_http.PublicBodyError,
        espn_nba.EspnNbaOriginError,
        espn_nba.EspnNbaError,
    ) as exc:
        return _failure_from_exception(exc, evidence, espn=True)
