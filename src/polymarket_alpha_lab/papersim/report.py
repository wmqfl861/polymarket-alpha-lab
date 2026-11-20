"""B4 report orchestration: pure assembly of the pipeline report (plan 05
section 2 / DELIVERY_PLAN 80.8).

One report is a read-only, reproducible, timestamped view of the
capture->settle pipeline state in causal order -- scan, marks, outcomes,
settle, evaluate -- over rows handed to this module.  Stages whose command
is not wired (B2 ``capture-marks`` and B3 ``settle`` do not exist at this
node's base) are labeled ``STAGE_NOT_WIRED``; wired stages without rows
are ``NO_DATA``; missing evidence is never zero-filled or synthesized
(coverage ratios stay ``None`` when the denominator is zero, partial
basketball scores stay unresolved).

Purity profile: this module opens no connection, reads no clock and
touches no file.  ``assemble_report`` is a pure function over explicit
inputs; the receipt/snapshot builders return the existing
``store.CommandRunEventSubmission`` / ``snapshots.SnapshotRegistration``
records so the CLI driver (and the native proof lane) perform the only
writes -- one ``papersim.snapshots`` registration of the canonical report
bytes (kind ``papersim-report``) plus ``command='report'`` run receipts.

Determinism contract: the report content is canonical JSON (sorted keys,
compact separators) whose ``as_of`` values are derived from the DATA of
each stage (latest receipt / row timestamp), never from the writing
clock.  The report identity is content-addressed from
``scope|as_of|content digest``; a re-run over unchanged evidence yields
the same key and inserts nothing, while late-arriving evidence mints a
new append-only record.  Receipts of the ``report`` command itself are
excluded from the document BY COMMAND so that the run's own receipts can
never place the writing clock inside the content digest.

Settlement display contract (binding domain rule): basketball FULL-GAME
moneyline/spread/total settlements are overtime-INCLUSIVE across leagues
(NBA and international alike) -- the final score of a completed game is
regulation plus overtime by construction, exactly the ESPN ``completed``
score the adapters parse, and no regulation-only filtering exists
anywhere on the basketball path.  Soccer stays pinned to
``regulation_90_plus_stoppage`` (``papersim/adapters/soccer_totals.py``).
Partial or incomplete basketball scores remain unresolved: never a
forced win or loss.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans)
are original-missing unless a receipt is cited in-line; the reference
map with in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs, urlsplit

from polymarket_alpha_lab.papersim import espn_nba, evaluation, snapshots, store
from polymarket_alpha_lab.papersim.adapters.contracts import HARD_MAX_RESPONSE_BYTES

__all__ = [
    "BASKETBALL_FULL_GAME_KINDS",
    "BASKETBALL_SNAPSHOT_KINDS",
    "BasketballFinalsEvidence",
    "BasketballMarketSettlement",
    "CAPTURE_MARKS_COMMAND",
    "PipelineCoverage",
    "REPORT_ALGORITHM_VERSION",
    "REPORT_COMMAND",
    "REPORT_SCOPE_ALL",
    "REPORT_SNAPSHOT_KIND",
    "ReportAssembly",
    "ReportError",
    "ReportInputs",
    "SETTLE_COMMAND",
    "SOCCER_SETTLEMENT_PERIOD",
    "STAGE_COMMANDS",
    "STAGE_ORDER",
    "STAGE_STATUS_NO_DATA",
    "STAGE_STATUS_NOT_WIRED",
    "STAGE_STATUS_OK",
    "STAGE_STATUS_PARTIAL",
    "assemble_report",
    "basketball_full_game_outcome",
    "basketball_snapshot_references",
    "bounded_basketball_snapshots",
    "canonical_report_json",
    "is_basketball_candidate",
    "report_json_value",
    "report_operation_key",
    "report_receipt",
    "report_snapshot_registration",
    "verify_basketball_finals",
]

#: The CLI subcommand and receipt command of this node (FX-16 fourth command).
REPORT_COMMAND = "report"

#: The ``papersim.snapshots`` kind of a registered canonical report.
REPORT_SNAPSHOT_KIND = "papersim-report"

#: Algorithm version stamped into every report document.
REPORT_ALGORITHM_VERSION = "papersim-report-v1"

#: Scope value covering the whole managed store (no per-queue filter yet).
REPORT_SCOPE_ALL = "*"

#: The causal stage order (plan section 5 causality guarantee):
#: scan -> marks -> outcomes -> settle -> evaluate/report.
STAGE_ORDER = ("scan", "marks", "outcomes", "settle", "evaluate")

#: The M2 command each stage waits on.  ``run-scan`` is B1 (merged; the
#: canonical constant lives at ``papersim/scan.py`` ``SCAN_COMMAND``) and
#: ``evaluate`` is the FX-11 command; ``capture-marks`` (B2) and ``settle``
#: (B3) are the remaining FX-16 commands whose modules do not exist at
#: this node's base -- when they land, the shared wiring pass may swap
#: these literals for the owning modules' constants (same names, same
#: values).
STAGE_COMMANDS = {
    "scan": "run-scan",
    "marks": "capture-marks",
    "outcomes": "settle",
    "settle": "settle",
    "evaluate": "evaluate",
}

CAPTURE_MARKS_COMMAND = "capture-marks"
SETTLE_COMMAND = "settle"

STAGE_STATUS_OK = "OK"
STAGE_STATUS_NO_DATA = "NO_DATA"
STAGE_STATUS_NOT_WIRED = "STAGE_NOT_WIRED"
STAGE_STATUS_PARTIAL = "PARTIAL"

#: Soccer settlement stays pinned to regulation plus stoppage time
#: (``papersim/adapters/soccer_totals.py`` ``_PERIOD``); basketball
#: full-game settlement includes overtime by construction.  The report
#: module pins the soccer constant so the contrast is testable in-repo.
SOCCER_SETTLEMENT_PERIOD = "regulation_90_plus_stoppage"

#: Basketball full-game market kinds settled on the final score.
BASKETBALL_FULL_GAME_KINDS = ("moneyline", "spread", "total")

_BASKETBALL_LEAGUES = frozenset({
    "nba", "wnba", "ncaab", "ncaaw", "cbb", "euroleague", "eurocup", "fiba",
    "liga-acb", "bbl", "basketball",
})
_GAMMA_HOST = "gamma-api.polymarket.com"
_GAMMA_KIND = "polymarket-gamma"
_GAMMA_PROJECTION_KIND = "polymarket-gamma-event-projection"
_ESPN_CACHE_KIND = "espn-nba-summary-cache"
_ESPN_SCOREBOARD_KIND = "espn-nba-scoreboard"
BASKETBALL_SNAPSHOT_KINDS = (_GAMMA_KIND, _GAMMA_PROJECTION_KIND,
                            espn_nba.SOURCE_KEY, _ESPN_CACHE_KIND, _ESPN_SCOREBOARD_KIND)
_GAMMA_ABBREVIATIONS = {
    "GS": "gsw", "NY": "nyk", "SA": "sas", "UTAH": "uta", "WSH": "was", "NO": "nop",
}
_GAMMA_NBA_TEAMS = {_GAMMA_ABBREVIATIONS.get(team, team.lower()): team
                    for team in espn_nba.NBA_TEAMS}
# These are the collector's explicit aliases, supplementing ESPN's team table.
_NBA_TEAM_ALIASES = {
    "LAC": ("LA Clippers", "Los Angeles Clippers", "Clippers"),
    "POR": ("Portland Trail Blazers", "Trail Blazers", "Blazers"),
}
_NBA_SLUG = re.compile(r"nba-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d\d-\d\d)")
_MARKET_KINDS = {"moneyline": "moneyline", "spread": "spread", "spreads": "spread",
                 "total": "total", "totals": "total"}
_MARKET_IDENTITY = {"condition_id": "conditionId", "market_id": "id", "market_slug": "slug"}

# The derived inputs have no authenticated parent body in the managed store.
# Only these reviewed file identities and this exact projection are admitted.
_NBA_CACHE_FILES = {
    "401909093": (875_922, "5c408eb0cded2b6076fd6fc2d691d7875ecc8246e6393a0ebebbef6060bd589e"),
    "401909942": (873_944, "9ad12f75b4a8fa32587db75de02f2f2812d71abe0d24be4468a82dbe1100dc38"),
}
_NBA_SCOREBOARD_FILE = "round13/raw/espn_nba_20261022_final.json"
_NBA_SCOREBOARD_SHA = "1accadfcea27dccb13a984779e50486bd4a055a9532eeb89ff7d51d5bc6f4edb"
_GAMMA_HISTORICAL_CAPTURE_SHA = "5f29c395d9688740eaf08b03f44eb59454267475ffc9631016135f99aaec58ca"
_GAMMA_PROJECTION_PARENT_SHA = "36a30c25c7d5096c9e21e38eb1ed03672c59dce0a596252d1e1194634144ab79"
_GAMMA_PROJECTION_SHA = "e2b9d733a2bdb77ea5f801e7c8561a084e44e9c1d8891d44a6ef7b8f1f351db7"
_GAMMA_PROJECTION_EVENT = "1197295"
_GAMMA_PROJECTION_MARKETS = ("5765056", "5765057")
_GAMMA_PROJECTION_SOURCE = (
    f"round14/raw/gamma_nba.json#sha256={_GAMMA_PROJECTION_PARENT_SHA}"
    f"&event={_GAMMA_PROJECTION_EVENT}&markets={','.join(_GAMMA_PROJECTION_MARKETS)}"
)

#: Side tokens of the basketball resolver: moneyline/spread pick a team
#: side, totals pick Over/Under.
_BASKETBALL_TEAM_SIDES = ("home", "away")
_BASKETBALL_TOTAL_SIDES = ("Over", "Under")


class ReportError(ValueError):
    """One report-level refusal with a fixed code (never a silent skip)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Row-shape helpers (rows arrive as driver/JSON-ready mappings)
# ---------------------------------------------------------------------------


def _as_datetime(value: Any, name: str) -> datetime.datetime:
    """Normalize a row timestamp: aware datetime or aware ISO-8601 text."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            raise ReportError("papersim_report_row_invalid",
                              f"{name}: naive datetime")
        if value.utcoffset() is None:
            raise ReportError("papersim_report_row_invalid",
                              f"{name}: naive datetime")
        return value.astimezone(datetime.UTC)
    if type(value) is str:
        try:
            parsed = datetime.datetime.fromisoformat(value)
        except ValueError as error:
            raise ReportError("papersim_report_row_invalid",
                              f"{name}: bad iso datetime") from error
        if parsed.tzinfo is None:
            raise ReportError("papersim_report_row_invalid",
                              f"{name}: naive iso datetime")
        return parsed.astimezone(datetime.UTC)
    raise ReportError("papersim_report_row_invalid",
                      f"{name}: datetime required")


def _require_text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ReportError("papersim_report_row_invalid", name)
    return value


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or type(value) is not int:
        raise ReportError("papersim_report_row_invalid", name)
    return value


def _require_int(value: Any, name: str) -> int:
    result = _optional_int(value, name)
    if result is None:
        raise ReportError("papersim_report_row_invalid", name)
    return result


def _iso(moment: datetime.datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _max_datetime(values: Iterable[datetime.datetime | None]) \
        -> datetime.datetime | None:
    moments = [value for value in values if value is not None]
    return max(moments) if moments else None


def _ratio(numerator: int, denominator: int) -> float | None:
    """Coverage ratio; ``None`` when the denominator is zero (never 0.0)."""
    if denominator == 0:
        return None
    return numerator / denominator


# ---------------------------------------------------------------------------
# Pipeline coverage (the honest counts behind the stage matrix)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineCoverage:
    """Pipeline row counts handed to the assembly (read from the store).

    Every count is an observation count; each ``*_with_*``/``*_settled``
    member is bounded by its denominator so a coverage ratio never
    silently exceeds 1.  Zero rows are honest zeros -- the derived ratio
    is ``None``, never a synthesized value.
    """

    candidates_total: int = 0
    candidates_with_marks: int = 0
    mark_observations: int = 0
    markets_bound: int = 0
    markets_with_outcome: int = 0
    outcome_observations: int = 0
    entries_total: int = 0
    entries_settled: int = 0

    def validate(self) -> "PipelineCoverage":
        for name in ("candidates_total", "candidates_with_marks",
                     "mark_observations", "markets_bound",
                     "markets_with_outcome", "outcome_observations",
                     "entries_total", "entries_settled"):
            value = getattr(self, name)
            if isinstance(value, bool) or type(value) is not int or value < 0:
                raise ReportError("papersim_report_coverage_invalid", name)
        pairs = (("candidates_with_marks", "candidates_total"),
                 ("markets_with_outcome", "markets_bound"),
                 ("entries_settled", "entries_total"))
        for part, whole in pairs:
            if getattr(self, part) > getattr(self, whole):
                raise ReportError("papersim_report_coverage_invalid",
                                  f"{part}>{whole}")
        return self


# ---------------------------------------------------------------------------
# Basketball full-game settlement (overtime-inclusive, all leagues)
# ---------------------------------------------------------------------------


def basketball_full_game_outcome(*, market_kind: str, side: str,
                                 line: Decimal | float | int | None = None,
                                 home_score: int | None = None,
                                 away_score: int | None = None,
                                 completed: bool = True) -> str | None:
    """Settle one basketball FULL-GAME market on the final score.

    The final score of a completed basketball game is REGULATION PLUS
    OVERTIME, across leagues (NBA and international leagues alike): this
    resolver receives only the completed final score -- exactly the ESPN
    ``completed`` score the adapter parses, overtime included -- and never
    a period breakdown, so no regulation-only filtering can occur anywhere
    on this path (soccer stays pinned to ``regulation_90_plus_stoppage``
    in its own adapter).  Partial or incomplete scores, and games not yet
    completed, remain UNRESOLVED (``None``): never a forced win or loss.

    Returns ``"win"``/``"loss"`` for the asked ``side`` or ``None``:

    - ``moneyline`` (``side`` in ``home``/``away``): the asked team wins
      iff its final score is strictly greater.  A completed basketball
      game never ties (overtime plays on), so an equal-score input is an
      impossible-in-nature shape and stays unresolved rather than forced.
    - ``spread`` (``side`` in ``home``/``away``, ``line`` required): the
      asked side's final margin plus the handicap; a whole-number push
      (adjusted margin exactly zero) stays unresolved -- Polymarket lines
      are half-point steps, so a push never occurs for real books.
    - ``total`` (``side`` in ``Over``/``Under``, ``line`` required): the
      combined final score against the line; an exact push stays
      unresolved for the same reason.
    """
    if market_kind not in BASKETBALL_FULL_GAME_KINDS:
        raise ReportError("papersim_report_basketball_kind_invalid",
                          str(market_kind))
    if type(completed) is not bool:
        raise ReportError("papersim_report_basketball_completed_invalid")
    if not completed:
        return None
    for name, score in (("home_score", home_score), ("away_score", away_score)):
        if score is None:
            continue
        if isinstance(score, bool) or type(score) is not int or score < 0:
            raise ReportError("papersim_report_basketball_score_invalid", name)
    if home_score is None or away_score is None:
        return None
    if market_kind == "moneyline":
        if side not in _BASKETBALL_TEAM_SIDES:
            raise ReportError("papersim_report_basketball_side_invalid", side)
        if home_score == away_score:
            return None
        asked_side_is_home = side == "home"
        home_won = home_score > away_score
        return "win" if asked_side_is_home == home_won else "loss"
    if line is None or isinstance(line, bool) \
            or not isinstance(line, (int, float, Decimal)):
        raise ReportError("papersim_report_basketball_line_invalid")
    margin = Decimal(str(line))
    if not margin.is_finite():
        raise ReportError("papersim_report_basketball_line_invalid")
    if market_kind == "spread":
        if side not in _BASKETBALL_TEAM_SIDES:
            raise ReportError("papersim_report_basketball_side_invalid", side)
        raw = (home_score - away_score) if side == "home" \
            else (away_score - home_score)
        adjusted = raw + margin
        if adjusted == 0:
            return None
        return "win" if adjusted > 0 else "loss"
    if side not in _BASKETBALL_TOTAL_SIDES:
        raise ReportError("papersim_report_basketball_side_invalid", side)
    total = home_score + away_score
    if total == margin:
        return None
    over = total > margin
    return "win" if over == (side == "Over") else "loss"


@dataclass(frozen=True)
class BasketballMarketSettlement:
    """One basketball full-game market settlement display row.

    ``home_score``/``away_score`` are the COMPLETED final scores
    (regulation plus overtime -- the ESPN ``completed`` score).  The
    display never strips overtime and never forces a resolution from a
    partial score; ``unresolved_reason`` states why a ``None`` settlement
    stays open.
    """

    market_ref: str
    league: str
    market_kind: str
    side: str
    line: float | int | None = None
    home_score: int | None = None
    away_score: int | None = None
    completed: bool = False

    def validate(self) -> "BasketballMarketSettlement":
        _require_text(self.market_ref, "market_ref")
        _require_text(self.league, "league")
        if self.market_kind not in BASKETBALL_FULL_GAME_KINDS:
            raise ReportError("papersim_report_basketball_kind_invalid",
                              self.market_kind)
        _require_text(self.side, "side")
        if type(self.completed) is not bool:
            raise ReportError("papersim_report_basketball_completed_invalid")
        if self.line is not None and (isinstance(self.line, bool)
                                      or not isinstance(self.line,
                                                        (int, float, Decimal))
                                      or not math.isfinite(float(self.line))):
            raise ReportError("papersim_report_basketball_line_invalid")
        for name, score in (("home_score", self.home_score),
                            ("away_score", self.away_score)):
            if score is not None and (isinstance(score, bool)
                                      or type(score) is not int or score < 0):
                raise ReportError("papersim_report_basketball_score_invalid",
                                  name)
        return self

    def settlement(self) -> str | None:
        """``"win"``/``"loss"``/``None`` on the final score (OT included)."""
        self.validate()
        return basketball_full_game_outcome(
            market_kind=self.market_kind, side=self.side, line=self.line,
            home_score=self.home_score, away_score=self.away_score,
            completed=self.completed)

    def unresolved_reason(self) -> str | None:
        """Why the settlement stays open; ``None`` once resolved."""
        if self.settlement() is not None:
            return None
        if not self.completed:
            return "not_completed"
        if self.home_score is None or self.away_score is None:
            return "incomplete_score"
        return "push_or_tie"

    def display(self) -> dict:
        """The document row: final score as the full-game final, OT included."""
        settlement = self.settlement()
        final = None
        if self.home_score is not None and self.away_score is not None:
            final = {"home": self.home_score, "away": self.away_score}
        return {
            "market_ref": self.market_ref,
            "league": self.league,
            "market_kind": self.market_kind,
            "side": self.side,
            "line": (None if self.line is None else float(self.line)),
            "completed": self.completed,
            "final_score": final,
            # Constant by construction: basketball full-game finals are
            # regulation plus overtime, in every league.
            "final_includes_overtime": True,
            "settlement": settlement,
            "unresolved_reason": self.unresolved_reason(),
        }


@dataclass(frozen=True)
class _BasketballFinal:
    candidate_id: int
    position: BasketballMarketSettlement
    score: _BasketballScoreboard
    contract: _BasketballContract
    evidence_at: datetime.datetime

    def display(self) -> dict:
        return {
            **self.position.display(),
            "candidate_id": self.candidate_id,
            "home_team": _GAMMA_ABBREVIATIONS.get(self.score.home, self.score.home).upper(),
            "away_team": _GAMMA_ABBREVIATIONS.get(self.score.away, self.score.away).upper(),
            "resolution_period": "full_game_including_overtime",
            "regulation_score": self.score.regulation_score,
            "overtime_periods": self.score.overtime_periods,
            "evidence_as_of": _iso(self.evidence_at),
            "provenance": {
                "gamma": self.contract.source.citation(),
                "scoreboard": self.score.source.citation(),
                "gamma_event_id": self.contract.event_id,
                "gamma_game_id": self.contract.game_id,
                "espn_event_id": self.score.event_id,
                "scheduled_tip": _iso(self.contract.tip),
            },
        }


@dataclass(frozen=True)
class _UnavailableBasketballFinal:
    candidate_id: int
    market_ref: str | None
    reason: str


@dataclass(frozen=True)
class BasketballFinalsEvidence:
    """Deterministic finals and unavailable positions from registered sources."""

    finals: tuple[_BasketballFinal, ...] = ()
    unavailable: tuple[_UnavailableBasketballFinal, ...] = ()

    @property
    def as_of(self) -> datetime.datetime | None:
        return _max_datetime(final.evidence_at for final in self.finals)

    def summary(self) -> dict:
        verified = len(self.finals)
        total = verified + len(self.unavailable)
        if not total:
            status = "NO_CANDIDATES"
        elif not self.unavailable:
            status = "OK"
        else:
            status = "PARTIAL" if verified else "UNAVAILABLE"
        return {
            "status": status,
            "candidates": total,
            "verified": verified,
            "unavailable": len(self.unavailable),
            "reason": "no_basketball_candidates" if not total else None,
            "unavailable_positions": [dataclasses.asdict(item) for item in self.unavailable],
        }


class _BasketballProofError(ValueError):
    """A privacy-safe reason for an unavailable managed final."""


def _proof(assertion: bool, reason: str = "conflict") -> None:
    if not assertion:
        raise _BasketballProofError(reason)


def _proof_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _proof_time(value) -> datetime.datetime | None:
    try:
        return _as_datetime(value, "basketball_source_time")
    except (ReportError, OverflowError):
        return None


def _provider_id(value) -> str:
    if type(value) is int:
        value = str(value)
    return value if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) else ""


def _proof_digest(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _json_object(pairs) -> dict:
    result = dict(pairs)
    _proof(len(result) == len(pairs), "missing_terms_or_reference")
    return result


def _reject_json_constant(_value) -> None:
    raise _BasketballProofError("missing_terms_or_reference")


def _evidence_json(content):
    return json.loads(content, object_pairs_hook=_json_object, parse_constant=_reject_json_constant)


def is_basketball_candidate(candidate: Mapping[str, Any]) -> bool:
    """Identify basketball from stored league, sport or model labels."""
    event_key = _proof_text(candidate.get("event_key")).casefold()
    slug = _proof_text(candidate.get("market_slug")).casefold()
    labels = " ".join(_proof_text(candidate.get(field)).casefold()
                      for field in ("team", "market_type", "model_name"))
    return any(
        slug.startswith(f"{league}-") or event_key.startswith(f"match:{league}|")
        or re.search(rf"(?<![a-z0-9]){league}(?![a-z0-9])", labels)
        for league in _BASKETBALL_LEAGUES
    )


@dataclass(frozen=True)
class _BasketballSource:
    digest: str
    kind: str
    source: str
    fetched_at: datetime.datetime

    def citation(self) -> dict:
        evidence = {"kind": self.kind, "content_sha256": self.digest}
        if self.kind == _GAMMA_PROJECTION_KIND:
            evidence["parent_content_sha256"] = _GAMMA_PROJECTION_PARENT_SHA
        return evidence


def _source_url(source: str, host: str) -> tuple[str, dict[str, list[str]]]:
    _proof(
        len(source) <= 8192 and source.isascii() and not any(ch.isspace() for ch in source),
        "missing_terms_or_reference")
    url = urlsplit(source)
    _proof((url.scheme, url.netloc, url.fragment) == ("https", host, ""),
           "missing_terms_or_reference")
    return url.path, parse_qs(url.query, strict_parsing=True, max_num_fields=128)


def _gamma_query(query, *, resource: str) -> None:
    filters = {"limit", "offset", "closed", "active"}
    if resource == "markets":
        filters |= {"slug", "condition_id", "condition_ids", "id"}
    else:
        filters |= {"tag_slug", "tag_id", "order", "ascending"}
    _proof(query.keys() <= filters, "missing_terms_or_reference")
    for key, values in query.items():
        if key in ("closed", "active", "ascending"):
            valid = len(values) == 1 and values[0] in ("true", "false")
        elif key in ("offset", "limit", "id", "tag_id"):
            valid = all(re.fullmatch(r"[0-9]{1,20}", value) for value in values)
        elif key in ("condition_id", "condition_ids"):
            valid = all(re.fullmatch(r"0x[0-9a-fA-F]{64}", value) for value in values)
        else:
            valid = all(re.fullmatch(r"[a-zA-Z0-9_-]+", value) for value in values)
        _proof(valid, "missing_terms_or_reference")


def _market_parents(market: dict) -> list:
    parents = market.get("events")
    _proof(isinstance(parents, list) and len(parents) == 1
           and isinstance(parents[0], dict), "missing_terms_or_reference")
    return parents


def _gamma_markets(source: _BasketballSource, payload, content: bytes) -> tuple:
    if source.kind == _GAMMA_PROJECTION_KIND:
        _proof(source.source == _GAMMA_PROJECTION_SOURCE
               and source.digest == _GAMMA_PROJECTION_SHA and len(content) == 11_506,
               "missing_terms_or_reference")
        _proof(isinstance(payload, dict)
               and _provider_id(payload.get("id")) == _GAMMA_PROJECTION_EVENT,
               "missing_terms_or_reference")
        events = [payload]
    else:
        _proof(source.digest != _GAMMA_PROJECTION_SHA, "missing_terms_or_reference")
        path, query = _source_url(source.source, _GAMMA_HOST)
        single = re.fullmatch(r"/(events|markets)/(slug/)?([a-zA-Z0-9-]+)", path)
        if single and path != "/events/pagination":
            resource, by_slug, identity = single.groups()
            _proof(not query and isinstance(payload, dict), "missing_terms_or_reference")
            value = _proof_text(payload.get("slug")) if by_slug else _provider_id(payload.get("id"))
            _proof(identity == value, "missing_terms_or_reference")
            if resource == "markets":
                return tuple((payload, parent) for parent in _market_parents(payload))
            events = [payload]
        elif path == "/markets":
            _gamma_query(query, resource="markets")
            _proof(isinstance(payload, list) and all(isinstance(row, dict) for row in payload),
                   "missing_terms_or_reference")
            markets = []
            for market in payload:
                for fields, value in ((("slug",), _proof_text(market.get("slug"))),
                                      (("id",), _provider_id(market.get("id"))),
                                      (("condition_id", "condition_ids"), _proof_text(market.get("conditionId")))):
                    wanted = {item for field_name in fields for item in query.get(field_name, ())}
                    _proof(not wanted or value in wanted, "missing_terms_or_reference")
                if "closed" in query:
                    _proof(market.get("closed") is (query["closed"][0] == "true"),
                           "missing_terms_or_reference")
                # A discovery result can contain markets outside this sport.
                parents = market.get("events")
                if isinstance(parents, list):
                    markets.extend((market, parent) for parent in parents if isinstance(parent, dict))
            return tuple(markets)
        elif path == "/events":
            _gamma_query(query, resource="events")
            events = payload
        else:
            _proof(path == "/events/pagination", "missing_terms_or_reference")
            _gamma_query(query, resource="events")
            _proof(isinstance(payload, dict) and set(payload) == {"$schema", "data", "pagination"}
                   and payload["$schema"] == f"https://{_GAMMA_HOST}/schemas/EventsPagination.json",
                   "missing_terms_or_reference")
            pagination = payload["pagination"]
            _proof(isinstance(pagination, dict) and set(pagination) == {"hasMore", "totalResults"}
                   and type(pagination["hasMore"]) is bool
                   and type(pagination["totalResults"]) is int and pagination["totalResults"] >= 0,
                   "missing_terms_or_reference")
            events = payload["data"]
    _proof(isinstance(events, list), "missing_terms_or_reference")
    markets = []
    for event in events:
        _proof(isinstance(event, dict) and isinstance(event.get("markets"), list),
               "missing_terms_or_reference")
        for market in event["markets"]:
            _proof(isinstance(market, dict), "missing_terms_or_reference")
            markets.append((market, event))
    if source.kind == _GAMMA_PROJECTION_KIND:
        _proof(tuple(_provider_id(market.get("id")) for market, _ in markets)
               == _GAMMA_PROJECTION_MARKETS, "missing_terms_or_reference")
    return tuple(markets)


@dataclass(frozen=True)
class _BasketballScoreboard:
    source: _BasketballSource
    event_id: str
    tip: datetime.datetime
    home: str
    away: str
    home_score: int | None
    away_score: int | None
    regulation_score: dict | None
    overtime_periods: int | None
    reason: str | None


def _score(value) -> int | None:
    if type(value) is int and 0 <= value <= 999:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value):
        return int(value)
    return None


def _period_scores(periods: list) -> tuple[int, ...]:
    scores = []
    for index, period in enumerate(periods, start=1):
        _proof(isinstance(period, dict), "incomplete_score")
        score = _score(period.get("displayValue"))
        _proof(score is not None, "incomplete_score")
        if "period" in period:
            _proof(_score(period["period"]) == index, "conflict")
        if "value" in period:
            _proof(_score(period["value"]) == score, "conflict")
        scores.append(score)
    return tuple(scores)


def _espn_teams(competition: dict) -> dict:
    competitors = competition.get("competitors")
    _proof(isinstance(competitors, list) and len(competitors) == 2, "incomplete_score")
    teams = {}
    for competitor in competitors:
        _proof(isinstance(competitor, dict) and isinstance(competitor.get("team"), dict),
               "incomplete_score")
        direction = competitor.get("homeAway")
        team = competitor["team"]
        abbreviation = team.get("abbreviation")
        _proof(direction in ("home", "away") and direction not in teams
               and isinstance(abbreviation, str) and abbreviation in espn_nba.NBA_TEAMS,
               "ambiguous_match")
        espn_id = espn_nba.NBA_TEAMS[abbreviation][0]
        _proof("id" not in team or _provider_id(team["id"]) == str(espn_id))
        teams[direction] = (abbreviation, competitor)
    _proof(teams["home"][0] != teams["away"][0], "ambiguous_match")
    return teams


def _espn_summary(source: _BasketballSource, payload, content: bytes) -> _BasketballScoreboard:
    _proof(isinstance(payload, dict) and isinstance(payload.get("header"), dict)
           and isinstance(payload.get("boxscore"), dict), "missing_terms_or_reference")
    header = payload["header"]
    event_id = _provider_id(header.get("id"))
    _proof(bool(event_id), "missing_terms_or_reference")
    encoded_cache = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")
    if source.kind == _ESPN_CACHE_KIND:
        _proof(source.source == f"papersim.espn_nba.fetch_game_summary:summary_{event_id}.json"
               and _NBA_CACHE_FILES.get(event_id) == (len(content), source.digest)
               and encoded_cache == content, "missing_terms_or_reference")
    else:
        path, query = _source_url(source.source, espn_nba.HOST)
        _proof(path == f"{espn_nba.API_PATH}/summary" and query == {"event": [event_id]}
               and encoded_cache != content, "missing_terms_or_reference")
    _proof(_is_nba_league(header.get("league")), "ambiguous_match")
    competitions = header.get("competitions")
    _proof(isinstance(competitions, list) and len(competitions) == 1
           and isinstance(competitions[0], dict), "ambiguous_match")
    return _espn_game(source, event_id, competitions[0])


def _is_nba_league(league) -> bool:
    return isinstance(league, dict) and league.get("abbreviation") == "NBA" and league.get("slug") == "nba"


def _espn_partial_scoreboard(source: _BasketballSource, payload, content: bytes) -> _BasketballScoreboard:
    # The reviewed historical scoreboard has a live reading for this game.
    # Its persisted file proves an observation of incompleteness only.
    _proof(source.source == _NBA_SCOREBOARD_FILE and source.digest == _NBA_SCOREBOARD_SHA
           and len(content) == 33_994 and isinstance(payload, dict)
           and set(payload) == {"events", "leagues", "provider"}
           and isinstance(payload["leagues"], list)
           and len(payload["leagues"]) == 1 and _is_nba_league(payload["leagues"][0])
           and isinstance(payload["events"], list), "missing_terms_or_reference")
    event_id = "401909093"
    events = [event for event in payload["events"]
              if isinstance(event, dict) and _provider_id(event.get("id")) == event_id]
    _proof(len(events) == 1, "ambiguous_match")
    event = events[0]
    competitions = event.get("competitions")
    _proof(isinstance(competitions, list) and len(competitions) == 1
           and isinstance(competitions[0], dict), "ambiguous_match")
    status = event.get("status")
    _proof(isinstance(status, dict) and isinstance(status.get("type"), dict)
           and status["type"].get("completed") is False, "conflict")
    result = _espn_game(source, event_id, competitions[0])
    _proof(result.reason == "not_completed" and result.tip == _proof_time(event.get("date")))
    return result


def _espn_game(source: _BasketballSource, event_id: str, competition: dict) -> _BasketballScoreboard:
    tip = _proof_time(competition.get("date"))
    _proof(_provider_id(competition.get("id")) == event_id and tip is not None, "ambiguous_match")
    teams = _espn_teams(competition)
    home, home_row = teams["home"]
    away, away_row = teams["away"]
    status = competition.get("status")
    status = status.get("type") if isinstance(status, dict) else None
    reason = "not_completed"
    home_score = away_score = regulation = overtime = None
    if isinstance(status, dict) and status.get("completed") is True:
        try:
            _proof(status.get("state") == "post" and status.get("name") == "STATUS_FINAL")
            _proof(source.kind != espn_nba.SOURCE_KEY or source.fetched_at > tip)
            home_score, away_score = (_score(row.get("score")) for row in (home_row, away_row))
            _proof(home_score is not None and away_score is not None, "incomplete_score")
            _proof(home_score != away_score)
            home_periods, away_periods = (row.get("linescores") for row in (home_row, away_row))
            ot_claim = bool(re.search(r"\bOT\b|\d+OT\b", _proof_text(status.get("detail")), re.I))
            if home_periods is not None or away_periods is not None or ot_claim:
                _proof(isinstance(home_periods, list) and isinstance(away_periods, list)
                       and len(home_periods) == len(away_periods) >= 4, "incomplete_score")
                home_by_period = _period_scores(home_periods)
                away_by_period = _period_scores(away_periods)
                _proof(sum(home_by_period) == home_score and sum(away_by_period) == away_score)
                overtime = len(home_by_period) - 4
                _proof(not ot_claim or overtime > 0)
                regulation = {"home": sum(home_by_period[:4]), "away": sum(away_by_period[:4])}
                margin = regulation["home"] - regulation["away"]
                for home_period, away_period in zip(home_by_period[4:], away_by_period[4:], strict=True):
                    _proof(margin == 0)
                    margin += home_period - away_period
            reason = None
        except _BasketballProofError as error:
            reason = str(error)
    return _BasketballScoreboard(source, event_id, tip, home, away, home_score, away_score,
                                regulation, overtime, reason)


def _decode_basketball_source(digest: str, row: Mapping[str, Any]) -> tuple | _BasketballScoreboard:
    body = row.get("content")
    length = row.get("byte_length")
    kind = row.get("kind")
    source = row.get("source")
    fetched_at = _proof_time(row.get("fetched_at"))
    _proof(_proof_digest(digest) and row.get("content_sha256") == digest
           and type(row.get("schema_version")) is int
           and row["schema_version"] == snapshots.SNAPSHOT_DEFAULT_SCHEMA_VERSION
           and type(length) is int and 0 < length <= HARD_MAX_RESPONSE_BYTES
           and isinstance(body, (bytes, memoryview, bytearray))
           and (body.nbytes if isinstance(body, memoryview) else len(body)) == length
           and fetched_at is not None and isinstance(source, str) and source,
           "missing_terms_or_reference")
    _proof(kind in BASKETBALL_SNAPSHOT_KINDS, "missing_terms_or_reference")
    content = bytes(body)
    _proof(sha256(content).hexdigest() == digest, "missing_terms_or_reference")
    receipt = _BasketballSource(digest, kind, source, fetched_at)
    payload = _evidence_json(content)
    if kind in (_GAMMA_KIND, _GAMMA_PROJECTION_KIND):
        return receipt, _gamma_markets(receipt, payload, content)
    if kind == _ESPN_SCOREBOARD_KIND:
        return _espn_partial_scoreboard(receipt, payload, content)
    return _espn_summary(receipt, payload, content)


def _nba_team(value) -> str | None:
    name = _proof_text(value).casefold()
    if name.upper() in espn_nba.NBA_TEAMS:
        return name.upper()
    if name in _GAMMA_NBA_TEAMS:
        return _GAMMA_NBA_TEAMS[name]
    for abbreviation, (_, display) in espn_nba.NBA_TEAMS.items():
        aliases = _NBA_TEAM_ALIASES.get(abbreviation, (display, display.rsplit(" ", 1)[-1]))
        if any(name == alias.casefold() for alias in aliases):
            return abbreviation
    return None


def _nba_pair(value: str) -> tuple[str | None, ...]:
    return tuple(_nba_team(part) for part in re.split(r" vs\.? ", value, flags=re.I))


def _json_pair(value, name: str) -> tuple:
    try:
        pair = _evidence_json(value) if isinstance(value, str) else value
    except (ValueError, RecursionError):
        raise _BasketballProofError(name) from None
    _proof(isinstance(pair, (list, tuple)) and len(pair) == 2, name)
    return tuple(pair)


def _decimal(value) -> Decimal | None:
    if type(value) not in (str, int, float, Decimal):
        return None
    candidate = str(value)
    if not re.fullmatch(r"[+-]?\d{1,7}(?:\.\d{1,6})?", candidate):
        return None
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


_FULL_GAME_RULE = "The result will be determined based on the final score including any overtime periods."
_RESOLUTION_EXCEPTIONS = (
    re.compile(r"If the game is postponed, this market will remain open until "
               r"(?:the game has been )?completed\.", re.I),
    re.compile(r"If the game is canceled entirely(?:, with no make-up game)?, "
               r"this market will resolve 50-50\.", re.I),
    re.compile(r"If this market's outcome was definitively determined under its rules by the listed "
               r"resolution sources before abandonment, that outcome will stand\. Otherwise, the "
               r"following no-result provisions apply\. If the game is canceled, does not start by "
               r"[^.]+, or is abandoned with no official result by that time, this market will resolve "
               r"50-50\.", re.I),
)
_RULE_NUMBER = r"(\d+(?:\.\d+)?)"
_RULE_TEAM = r'"([^"]+)"'
_PART_GAME = re.compile(r"\b(?:first|second|third|fourth|[1-4](?:st|nd|rd|th))[- ]"
                        r"(?:half|quarter)\b|\b[1-4][qh]\b|\bq[1-4]\b", re.I)


def _resolution_clauses(description, ordered_teams: tuple[str, str]) -> tuple[str, ...]:
    if not isinstance(description, str):
        raise _BasketballProofError("missing_terms_or_reference")
    lines = tuple(
        " ".join(line.replace("“", '"').replace("”", '"').split())
        for line in description.splitlines() if line.strip())
    _proof(any(line.casefold() == _FULL_GAME_RULE.casefold() for line in lines), "unsupported_period")
    clauses = []
    for line in lines:
        if line.casefold() == _FULL_GAME_RULE.casefold():
            continue
        introductory = re.fullmatch(
            r"In the upcoming NBA game between (.+?) and (.+?), "
            r"(?:originally )?scheduled for [^:]+(?::\d\d[^:]*)?:", line, re.I)
        if introductory:
            _proof(tuple(_nba_team(introductory[i]) for i in (1, 2)) == ordered_teams)
        elif not any(pattern.fullmatch(line) for pattern in _RESOLUTION_EXCEPTIONS):
            clauses.append(line)
    return tuple(clauses)


def _moneyline_rules(clauses: tuple[str, ...], ordered_teams: tuple[str, str]) -> None:
    winners = set()
    for clause in clauses:
        match = re.fullmatch(r'If the (.+) win, the market will resolve to ' + _RULE_TEAM + r"\.",
                             clause, re.I)
        _proof(match is not None)
        winner, outcome = (_nba_team(match[i]) for i in (1, 2))
        _proof(winner in ordered_teams and winner == outcome)
        winners.add(winner)
    _proof(len(clauses) == 2 and winners == set(ordered_teams))


def _total_rules(clauses: tuple[str, ...], line: Decimal, ordered_teams: tuple[str, str]) -> None:
    matched = set()
    for clause in clauses:
        strict = re.fullmatch(
            rf'If the combined final score is (more|less) than {_RULE_NUMBER} points, '
            r'the market will resolve to "(Over|Under)"\.', clause, re.I)
        inclusive = re.fullmatch(
            r'This market will resolve to "Over" if the (.+) and (.+) combine to score '
            r'(\d+) or more points in this game\.', clause, re.I)
        under = re.fullmatch(
            r'If the combined total is less than (\d+), this market will resolve to "Under"\.',
            clause, re.I)
        push = re.fullmatch(
            rf"If the combined final score is exactly {_RULE_NUMBER} points, "
            r"the market will resolve 50-50\.", clause, re.I)
        if strict:
            direction = "Over" if strict[1].casefold() == "more" else "Under"
            _proof(_decimal(strict[2]) == line and strict[3].casefold() == direction.casefold())
        elif inclusive:
            direction = "Over"
            _proof(set(_nba_team(inclusive[i]) for i in (1, 2)) == set(ordered_teams)
                   and _decimal(inclusive[3]) == line + Decimal("0.5"))
        elif under:
            direction = "Under"
            _proof(_decimal(under[1]) == line + Decimal("0.5"))
        else:
            direction = "push"
            _proof(push is not None and _decimal(push[1]) == line and line % 1 == 0)
        _proof(direction not in matched)
        matched.add(direction)
    expected = {"Over", "Under", "push"} if line % 1 == 0 else {"Over", "Under"}
    _proof(matched == expected)


def _spread_rules(clauses: tuple[str, ...], handicap: Decimal, favorite: str, other: str) -> None:
    threshold = -handicap
    strict = set()
    integer = set()
    for clause in clauses:
        winner = re.fullmatch(
            rf"If (?:the )?(.+?) wins? by more than {_RULE_NUMBER} points, "
            rf"the market will resolve to {_RULE_TEAM}\.", clause, re.I)
        underdog = re.fullmatch(
            rf"If (?:the )?(.+?) loses? by fewer than {_RULE_NUMBER} points, or wins? the "
            rf"game outright, the market will resolve to {_RULE_TEAM}\.", clause, re.I)
        push = re.fullmatch(
            rf"If (?:the )?(.+?) wins? by exactly {_RULE_NUMBER} points, the market will resolve 50-50\.",
            clause, re.I)
        inclusive = re.fullmatch(
            rf"This market will resolve to {_RULE_TEAM} if the (.+) win the game by "
            r"(\d+) or more points\.", clause, re.I)
        otherwise = re.fullmatch(
            rf"Otherwise, this market will resolve to {_RULE_TEAM}\. If the game ends in a tie, "
            rf"this market will resolve to {_RULE_TEAM}\.", clause, re.I)
        if winner:
            _proof(_nba_team(winner[1]) == _nba_team(winner[3]) == favorite
                   and _decimal(winner[2]) == threshold)
            strict.add("favorite")
        elif underdog:
            _proof(_nba_team(underdog[1]) == _nba_team(underdog[3]) == other
                   and _decimal(underdog[2]) == threshold)
            strict.add("other")
        elif push:
            _proof(_nba_team(push[1]) == favorite and _decimal(push[2]) == threshold
                   and threshold % 1 == 0)
            strict.add("push")
        elif inclusive:
            _proof(_nba_team(inclusive[1]) == _nba_team(inclusive[2]) == favorite
                   and _decimal(inclusive[3]) == threshold + Decimal("0.5"))
            integer.add("favorite")
        else:
            _proof(otherwise is not None and _nba_team(otherwise[1]) == _nba_team(otherwise[2]) == other)
            integer.add("other")
    expected = {"favorite", "other", "push"} if threshold % 1 == 0 else {"favorite", "other"}
    _proof((strict == expected and len(clauses) == len(strict))
           or (threshold % 1 != 0 and integer == expected and len(clauses) == len(integer)))


@dataclass(frozen=True)
class _BasketballContract:
    source: _BasketballSource
    market_id: str
    event_id: str
    game_id: str
    tip: datetime.datetime
    event_day: str
    teams: tuple[str, str]
    venue_roles: tuple[str, str] | None
    gamma_score: tuple[int, int] | None
    market_kind: str
    selection: str
    line: Decimal | None
    terminal_price: Decimal
    selection_token: str
    other_token: str
    closed_at: datetime.datetime

    def facts(self) -> tuple:
        return (self.market_id, self.event_id, self.game_id, self.tip, self.event_day,
                self.teams, self.market_kind, self.selection, self.line, self.terminal_price,
                self.selection_token, self.other_token)


def _quote_matches_candidate(candidate, row) -> bool:
    if (row.get("candidate_id") != candidate.get("candidate_id")
            or row.get("evidence_role") not in ("DECISION", "CLOSE", "PERIODIC")
            or row.get("side") != candidate.get("side") or not _proof_text(row.get("source_kind"))):
        return False
    reference, value = row.get("market_ref_kind"), row.get("market_ref")
    if not _proof_text(value):
        return False
    yes_token, no_token = (candidate.get(field) for field in ("yes_token_id", "no_token_id"))
    known_tokens = {yes_token, no_token} - {None}
    held = yes_token if row["side"] == "YES" else no_token
    valid_tokens = {held} if held is not None else known_tokens
    # bind_candidate's historical DECISION shape stores the YES leg even
    # when its non-token market reference accompanies a NO decision.
    if row["evidence_role"] == "DECISION" and reference != "token_id" and yes_token is not None:
        valid_tokens.add(yes_token)
    token = row.get("token_ref")
    if token is not None and valid_tokens and token not in valid_tokens:
        return False
    if reference in _MARKET_IDENTITY:
        return value == candidate.get(reference)
    # A token-only reference must already be anchored to the candidate or
    # to a DECISION whose token also appears in the exact Gamma market.
    return (reference == "token_id"
            and (token is None or token == value)
            and (value in valid_tokens or (not valid_tokens and row["evidence_role"] == "DECISION"
                                           and value == token)))


def _held_token_index(candidate, tokens: tuple, quotes: Sequence) -> int:
    side = candidate.get("side")
    _proof(side in ("YES", "NO"))
    yes_token, no_token = (candidate.get(field) for field in ("yes_token_id", "no_token_id"))
    _proof(all(token is None or token in tokens for token in (yes_token, no_token))
           and (yes_token is None or no_token is None or yes_token != no_token))
    held, opposite = (yes_token, no_token) if side == "YES" else (no_token, yes_token)
    if held is None and opposite is not None:
        held = tokens[1 - tokens.index(opposite)]
    if held is None:
        # The existing replay mapper writes its YES token in DECISION even
        # for a NO candidate. A typed token reference can state a NO token
        # directly. Without that reference, NO needs an explicit binding.
        decisions = {row.get("token_ref") for row in quotes if row["evidence_role"] == "DECISION"
                     and (side == "YES" or row["market_ref_kind"] == "token_id")} - {None}
        _proof(len(decisions) == 1, "missing_terms_or_reference")
        held = decisions.pop()
    _proof(held in tokens)
    return tokens.index(held)


def _gamma_event_identity(event: dict) -> tuple:
    event_id = _provider_id(event.get("id"))
    game_id = _provider_id(event.get("gameId"))
    base_slug = _proof_text(event.get("slug"))
    slug = _NBA_SLUG.fullmatch(base_slug)
    sport = event.get("sport")
    league_proven = (re.fullmatch(r"nba-\d{4}", _proof_text(event.get("seriesSlug")))
                     or (isinstance(sport, dict) and sport.get("sport") == "nba"))
    _proof(bool(event_id and game_id and slug and league_proven), "ambiguous_match")
    teams = tuple(_GAMMA_NBA_TEAMS.get(slug[i]) for i in (1, 2))
    _proof(all(teams) and teams[0] != teams[1]
           and _nba_pair(_proof_text(event.get("title"))) == teams, "ambiguous_match")
    _proof(event.get("eventDate") == slug[3], "ambiguous_match")
    tip = _proof_time(event.get("startTime"))
    _proof(tip is not None, "ambiguous_match")
    _proof(slug[3] in (tip.date().isoformat(), (tip.date() - datetime.timedelta(days=1)).isoformat()),
           "ambiguous_match")
    venue_roles = None
    if isinstance(sport, dict):
        _proof(sport.get("sport") == "nba", "ambiguous_match")
        if "ordering" in sport:
            _proof(sport["ordering"] in ("away", "home"), "ambiguous_match")
            venue_roles = teams if sport["ordering"] == "home" else teams[::-1]
    if "teams" in event:
        _proof(isinstance(event["teams"], list) and len(event["teams"]) == 2, "ambiguous_match")
        roles = {}
        for team in event["teams"]:
            _proof(isinstance(team, dict) and team.get("league") == "nba", "ambiguous_match")
            abbreviation = _nba_team(team.get("abbreviation"))
            direction = team.get("ordering")
            _proof(abbreviation in teams and direction in ("home", "away")
                   and direction not in roles and abbreviation not in roles.values(),
                   "ambiguous_match")
            roles[direction] = abbreviation
        explicit = (roles["home"], roles["away"])
        _proof(venue_roles is None or venue_roles == explicit, "ambiguous_match")
        venue_roles = explicit
    return event_id, game_id, base_slug, slug[3], tip, teams, venue_roles


def _contract_from_market(candidate, market: dict, event: dict, source: _BasketballSource,
                          quotes: Sequence) -> _BasketballContract:
    for field_name, market_field in _MARKET_IDENTITY.items():
        bound = candidate.get(field_name)
        _proof(bound is None or str(market.get(market_field)) == bound)
    market_id = _provider_id(market.get("id"))
    _proof(bool(market_id))
    event_id, game_id, base_slug, day, tip, teams, roles = _gamma_event_identity(event)
    market_tip = _proof_time(market.get("gameStartTime"))
    _proof(tip == market_tip == _proof_time(candidate.get("event_start_at")), "ambiguous_match")
    event_key = candidate.get("event_key")
    if not isinstance(event_key, str):
        raise _BasketballProofError("ambiguous_match")
    if event_key.startswith("gamma:"):
        _proof(event_key == store.candidate_event_key(market_class="sports", gamma_event_id=event_id),
               "ambiguous_match")
    else:
        _proof(event_key.startswith("match:"), "ambiguous_match")
    if market.get("events"):
        inner_identity = _gamma_event_identity(_market_parents(market)[0])
        _proof(inner_identity[:6] == (event_id, game_id, base_slug, day, tip, teams)
               and (inner_identity[6] is None or roles is None or inner_identity[6] == roles),
               "ambiguous_match")
        roles = roles or inner_identity[6]
    market_slug = _proof_text(market.get("slug"))
    _proof(market_slug == base_slug or market_slug.startswith(f"{base_slug}-"), "ambiguous_match")
    market_kind = _MARKET_KINDS.get(_proof_text(market.get("sportsMarketType")).casefold())
    question = _proof_text(market.get("question"))
    _proof(market_kind is not None and not _PART_GAME.search(f"{market_slug} {question}"),
           "unsupported_period")
    clauses = _resolution_clauses(market.get("description"), teams)
    tokens = _json_pair(market.get("clobTokenIds"), "missing_terms_or_reference")
    labels = _json_pair(market.get("outcomes"), "missing_terms_or_reference")
    _proof(all(isinstance(token, str) and re.fullmatch(r"[0-9]{1,80}", token) for token in tokens)
           and tokens[0] != tokens[1])
    index = _held_token_index(candidate, tokens, quotes)
    line = _decimal(market.get("line"))
    if market_kind == "total":
        matchup = re.fullmatch(rf"(.+): O/U {_RULE_NUMBER}", question, re.I)
        _proof(matchup is not None and _nba_pair(matchup[1]) == teams and line is not None
               and line > 0 and line % 1 in (0, Decimal("0.5")) and _decimal(matchup[2]) == line
               and all(isinstance(label, str) for label in labels) and set(labels) == {"Over", "Under"})
        _total_rules(clauses, line, teams)
        selection = labels[index]
    else:
        label_teams = tuple(_nba_team(label) for label in labels)
        _proof(set(label_teams) == set(teams))
        selection = label_teams[index]
        if market_kind == "moneyline":
            _proof(_nba_pair(question) == teams and market.get("line") is None)
            _moneyline_rules(clauses, teams)
        else:
            matchup = re.fullmatch(rf"Spread: (.+) \(-{_RULE_NUMBER}\)", question, re.I)
            _proof(matchup is not None and line is not None and line < 0
                   and abs(line) % 1 in (0, Decimal("0.5")) and _decimal(matchup[2]) == -line)
            favorite = _nba_team(matchup[1])
            _proof(favorite in teams)
            other = next(team for team in teams if team != favorite)
            _spread_rules(clauses, line, favorite, other)
            line = line if selection == favorite else -line
    _proof(market.get("closed") is True
           and market.get("umaResolutionStatus") == "resolved"
           and market.get("resolved") is not False, "not_completed")
    _proof(market.get("resolutionSource") == "https://www.nba.com/", "missing_terms_or_reference")
    prices = tuple(_decimal(value) for value in _json_pair(market.get("outcomePrices"), "incomplete_score"))
    _proof(None not in prices and sorted(prices) in ([0, 1], [Decimal("0.5")] * 2))
    closed_at = _proof_time(market.get("closedTime"))
    _proof(closed_at is not None and closed_at > tip, "incomplete_score")
    if event.get("finishedTimestamp") is not None:
        finished = _proof_time(event["finishedTimestamp"])
        _proof(finished is not None and finished > tip)
        closed_at = max(closed_at, finished)
    _proof(source.kind != _GAMMA_KIND or source.fetched_at >= closed_at)
    gamma_score = None
    serialized_score = _proof_text(event.get("score"))
    numeric_score = re.fullmatch(r"(\d{1,3})-(\d{1,3})", serialized_score)
    if numeric_score:
        gamma_score = (int(numeric_score[1]), int(numeric_score[2]))
    return _BasketballContract(source, market_id, event_id, game_id, tip, day, teams, roles,
                               gamma_score, market_kind, selection, line, prices[index],
                               tokens[index], tokens[1 - index], closed_at)


def _matching_position(candidate, contract: _BasketballContract, score: _BasketballScoreboard) -> bool:
    if contract.tip != score.tip or set(contract.teams) != {score.home, score.away}:
        return False
    if contract.venue_roles is not None and contract.venue_roles != (score.home, score.away):
        return False
    event_key = candidate["event_key"]
    if event_key.startswith("gamma:"):
        return True
    parts = event_key.removeprefix("match:").split("|")
    return (len(parts) == 4 and parts[0].casefold() == "nba"
            and (_nba_team(parts[1]), _nba_team(parts[2])) == (score.home, score.away)
            and parts[3] == contract.event_day)


def _outcome_matches_candidate(candidate, row) -> bool:
    if row.get("candidate_id") is not None and row["candidate_id"] != candidate["candidate_id"]:
        return False
    token_fields = ("yes_token_id", "no_token_id")
    candidate_tokens = {candidate.get(field) for field in token_fields} - {None}
    outcome_tokens = {row.get(field) for field in token_fields if row.get(field) is not None}
    if len(candidate_tokens) == 2 and not outcome_tokens <= candidate_tokens:
        return False
    if candidate_tokens and outcome_tokens and candidate_tokens.isdisjoint(outcome_tokens):
        return False
    for field_name in _MARKET_IDENTITY:
        if candidate.get(field_name) is not None:
            # The strongest stored identity must agree even when the outlet
            # supplied only a partial set of corroborating market fields.
            return row.get(field_name) == candidate[field_name] and all(
                not candidate.get(field) or not row.get(field) or candidate[field] == row[field]
                for field in _MARKET_IDENTITY)
    return False


def _source_agrees(row, source_kind: str, registered_source: str) -> bool:
    claimed_kind = row.get("source_kind")
    source_ref = _proof_text(row.get("source_ref"))
    if claimed_kind in BASKETBALL_SNAPSHOT_KINDS:
        return claimed_kind == source_kind and source_ref == registered_source
    if claimed_kind == "gamma_close" and source_kind not in (_GAMMA_KIND, _GAMMA_PROJECTION_KIND):
        return False
    if source_ref.startswith(f"https://{_GAMMA_HOST}/"):
        return source_kind == _GAMMA_KIND and source_ref == registered_source
    if source_ref.startswith(f"https://{espn_nba.HOST}/"):
        return source_kind == espn_nba.SOURCE_KEY and source_ref == registered_source
    return True


def _snapshot_references(candidate, quotes, outcomes, metadata) -> tuple[str, ...]:
    refs = candidate.get("snapshot_refs")
    if isinstance(refs, str):
        try:
            refs = _evidence_json(refs)
        except (ValueError, RecursionError):
            refs = ()
    bound = {digest for digest in refs if _proof_digest(digest)} if isinstance(refs, (list, tuple)) else set()
    for rows, column in ((quotes, "snapshot_sha256"), (outcomes, "source_snapshot_sha256")):
        for row in rows:
            digest = row.get(column)
            if not _proof_digest(digest):
                continue
            if metadata is not None:
                source = metadata.get(digest)
                if source is None or not _source_agrees(row, source["kind"], source["source"]):
                    continue
            bound.add(digest)
    return tuple(sorted(bound))


def _bound_basketball_rows(candidates, quotes, outcomes):
    quotes_by_candidate: dict[int, list] = {}
    for row in quotes:
        quotes_by_candidate.setdefault(row["candidate_id"], []).append(row)
    outcomes_by_candidate: dict[int | None, list] = {}
    for row in outcomes:
        outcomes_by_candidate.setdefault(row.get("candidate_id"), []).append(row)
    for candidate in sorted(candidates, key=lambda row: row["candidate_id"]):
        if not is_basketball_candidate(candidate):
            continue
        candidate_id = candidate["candidate_id"]
        bound_quotes = tuple(row for row in quotes_by_candidate.get(candidate_id, ())
                             if _quote_matches_candidate(candidate, row))
        bound_outcomes = tuple(
            row for row in (*outcomes_by_candidate.get(candidate_id, ()), *outcomes_by_candidate.get(None, ()))
            if _outcome_matches_candidate(candidate, row))
        yield candidate, bound_quotes, bound_outcomes


def basketball_snapshot_references(*, candidates, quotes, outcomes, snapshot_metadata=None) -> dict[int, tuple[str, ...]]:
    """Return candidate-scoped citations for registry lookup.

    Apply this again to the returned metadata before reading the bodies.
    A quote or outcome declaring a provider source must match that registry row.
    """
    return {
        candidate["candidate_id"]: _snapshot_references(candidate, bound_quotes, bound_outcomes, snapshot_metadata)
        for candidate, bound_quotes, bound_outcomes in _bound_basketball_rows(candidates, quotes, outcomes)
    }


def _bounded_source_length(digest, row) -> int:
    if (row is None or row.get("content_sha256") != digest or not _proof_digest(digest)
            or type(row.get("schema_version")) is not int
            or row["schema_version"] != snapshots.SNAPSHOT_DEFAULT_SCHEMA_VERSION
            or row.get("kind") not in BASKETBALL_SNAPSHOT_KINDS
            or not _proof_text(row.get("source")) or _proof_time(row.get("fetched_at")) is None):
        return 0
    length = row.get("byte_length")
    return length if type(length) is int and 0 < length <= HARD_MAX_RESPONSE_BYTES else 0


def bounded_basketball_snapshots(
    references: Mapping[int, Sequence[str]], snapshot_metadata: Mapping[str, Mapping[str, Any]],
    *, max_bytes: int | None = None, max_rows: int | None = None,
) -> tuple[tuple[str, ...], frozenset[int]]:
    """Choose bodies under both limits and identify skipped candidate evidence.

    Read each digest once. If a candidate's usable references cannot all
    fit, mark that candidate unavailable so a missing conflicting source
    cannot masquerade as a complete proof.
    """
    chosen: set[str] = set()
    deferred = set()
    total = 0
    for candidate_id, digests in sorted(references.items()):
        lengths = {digest: _bounded_source_length(digest, snapshot_metadata.get(digest))
                   for digest in set(digests).difference(chosen)}
        additional = {digest for digest, length in lengths.items() if length}
        additional_bytes = sum(lengths.values())
        if ((max_rows is not None and len(chosen) + len(additional) > max_rows)
                or (max_bytes is not None and total + additional_bytes > max_bytes)):
            deferred.add(candidate_id)
            continue
        chosen.update(additional)
        total += additional_bytes
    return tuple(sorted(chosen)), frozenset(deferred)


def _bound_outcome_clocks(
    outcomes: Sequence[Mapping[str, Any]],
    verified_sources: Mapping[str, _BasketballSource],
    contract: _BasketballContract,
    settlement: str | None,
) -> Iterable[datetime.datetime | None]:
    for outcome in outcomes:
        digest = outcome.get("source_snapshot_sha256")
        source = verified_sources.get(digest) if _proof_digest(digest) else None
        if source is None or not _source_agrees(outcome, source.kind, source.source):
            continue
        label = outcome.get("outcome")
        if label == "UNKNOWN":
            continue
        if label == "VOIDED":
            _proof(settlement is None)
        else:
            _proof(label in ("YES", "NO"))
            token = outcome.get("yes_token_id" if label == "YES" else "no_token_id")
            _proof(token in (contract.selection_token, contract.other_token)
                   and settlement is not None
                   and (settlement == "win") == (token == contract.selection_token))
        yield _proof_time(outcome.get("observed_at"))
        yield _proof_time(outcome.get("confirmed_at"))


def _verify_basketball_position(candidate, quotes, outcomes, sources, metadata) -> _BasketballFinal:
    condition = candidate.get("condition_id")
    _proof(bool(condition and (candidate.get("market_id") or candidate.get("market_slug"))),
           "missing_terms_or_reference")
    bound = _snapshot_references(candidate, quotes, outcomes, metadata)
    contracts = []
    scoreboards = []
    reasons = set()
    for digest in bound:
        if digest not in sources:
            continue
        source, content = sources[digest]
        if isinstance(content, _BasketballScoreboard):
            scoreboards.append(content)
            continue
        matches = [(market, event) for market, event in content
                   if market.get("conditionId") == condition]
        if not matches and any(
            str(market.get(field_name)) == candidate.get(candidate_field)
            for market, _ in content
            for candidate_field, field_name in (("market_id", "id"), ("market_slug", "slug"))
            if candidate.get(candidate_field)
        ):
            reasons.add("conflict")
        if len(matches) > 1:
            raise _BasketballProofError("ambiguous_match")
        for market, event in matches:
            try:
                contracts.append(_contract_from_market(candidate, market, event, source, quotes))
            except _BasketballProofError as error:
                reasons.add(str(error))
    if not contracts:
        for reason in ("conflict", "ambiguous_match", "unsupported_period", "not_completed", "incomplete_score"):
            if reason in reasons:
                raise _BasketballProofError(reason)
        raise _BasketballProofError("missing_terms_or_reference")
    _proof(not reasons.intersection({"conflict", "ambiguous_match", "unsupported_period"})
           and len({contract.facts() for contract in contracts}) == 1
           and len({item.venue_roles for item in contracts if item.venue_roles is not None}) <= 1)
    contract = max(contracts, key=lambda row: (
        row.venue_roles is not None, row.gamma_score is not None, row.closed_at, row.source.digest))
    matching = [row for row in scoreboards if _matching_position(candidate, contract, row)]
    if not matching:
        raise _BasketballProofError("ambiguous_match" if scoreboards else "missing_terms_or_reference")
    _proof(len({row.event_id for row in matching}) == 1, "ambiguous_match")
    _proof(all(row in matching or row.event_id != matching[0].event_id for row in scoreboards))
    completed = [row for row in matching if row.reason is None]
    _proof(not any(row.reason == "conflict" for row in matching))
    if not completed:
        reason = "incomplete_score" if any(row.reason == "incomplete_score" for row in matching) else "not_completed"
        raise _BasketballProofError(reason)
    _proof(len({(row.home_score, row.away_score) for row in completed}) == 1)
    _proof(len({(row.overtime_periods, row.regulation_score["home"], row.regulation_score["away"])
                for row in completed if row.regulation_score is not None}) <= 1)
    scoreboard = max(completed, key=lambda row: (
        row.overtime_periods is not None, row.source.kind == espn_nba.SOURCE_KEY,
        row.source.fetched_at if row.source.kind == espn_nba.SOURCE_KEY else contract.closed_at,
        row.source.digest))
    for cited in contracts:
        if cited.gamma_score is None:
            continue
        final_score = {scoreboard.home: scoreboard.home_score, scoreboard.away: scoreboard.away_score}
        published = tuple(final_score[team] for team in cited.teams)
        # The numeric Gamma score has an established order only when the
        # event also explicitly defines the venue roles of those teams.
        _proof(cited.gamma_score == published if cited.venue_roles is not None
               else sorted(cited.gamma_score) == sorted(published))
    side = contract.selection
    if contract.market_kind != "total":
        side = "home" if side == scoreboard.home else "away"
    position = BasketballMarketSettlement(
        market_ref=contract.market_id, league="nba", market_kind=contract.market_kind,
        side=side, line=contract.line, completed=True, home_score=scoreboard.home_score,
        away_score=scoreboard.away_score)
    settlement = position.settlement()
    _proof(contract.terminal_price == {"win": 1, "loss": 0, None: Decimal("0.5")}[settlement])
    evidence_times = [max(row.closed_at for row in contracts)]
    # This one historical Gamma body came from a retained collector cache
    # with no original fetch timestamp. For it only, the registered time
    # is merely local registration; the provider's own clocks still apply.
    evidence_times.extend(row.source.fetched_at for row in contracts
                          if row.source.kind == _GAMMA_KIND
                          and row.source.digest != _GAMMA_HISTORICAL_CAPTURE_SHA)
    verified_sources = {row.source.digest: row.source for row in (*contracts, *completed)}
    evidence_times.extend(_bound_outcome_clocks(outcomes, verified_sources, contract, settlement))
    evidence_times.extend(row.source.fetched_at for row in completed
                          if row.source.kind == espn_nba.SOURCE_KEY)
    evidence_at = _max_datetime(evidence_times)
    return _BasketballFinal(candidate["candidate_id"], position, scoreboard, contract, evidence_at)


def verify_basketball_finals(*, candidates: Sequence[Mapping[str, Any]],
                             quotes: Sequence[Mapping[str, Any]],
                             outcomes: Sequence[Mapping[str, Any]],
                             snapshots: Mapping[str, Mapping[str, Any]],
                             deferred_candidates: frozenset[int] = frozenset()) -> BasketballFinalsEvidence:
    """Verify candidate-bound, registered source bytes from one report snapshot."""
    sources = {}
    for digest, row in snapshots.items():
        try:
            decoded = _decode_basketball_source(digest, row)
        except (TypeError, ValueError, OverflowError, RecursionError):
            continue
        if isinstance(decoded, _BasketballScoreboard):
            sources[digest] = (decoded.source, decoded)
        else:
            sources[digest] = decoded
    finals = []
    unavailable = []
    for candidate, bound_quotes, bound_outcomes in _bound_basketball_rows(candidates, quotes, outcomes):
        candidate_id = candidate["candidate_id"]
        try:
            _proof(candidate_id not in deferred_candidates, "evidence_budget_exceeded")
            finals.append(_verify_basketball_position(candidate, bound_quotes, bound_outcomes, sources, snapshots))
        except _BasketballProofError as error:
            unavailable.append(_UnavailableBasketballFinal(
                candidate_id=candidate_id, market_ref=_provider_id(candidate.get("market_id")) or None,
                reason=str(error)))
    return BasketballFinalsEvidence(tuple(finals), tuple(unavailable))


# ---------------------------------------------------------------------------
# JSON-ready conversion (evaluate-command parity)
# ---------------------------------------------------------------------------


def report_json_value(value: Any) -> Any:
    """Recursive JSON-ready conversion of the frozen report dataclasses.

    Same semantics as the evaluate command's output conversion (the
    ``_jsonify`` face of ``papersim/cli.py``): ``Decimal`` becomes
    ``float``, datetimes become ISO-8601 text, tuples become lists and
    dataclasses expand by field.  The queue-metrics block built with this
    conversion is bit-for-bit the ``evaluate`` output over the same store.
    """
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [report_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): report_json_value(item) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: report_json_value(getattr(value, field.name))
                for field in dataclasses.fields(value)}
    raise ReportError("papersim_report_value_unserializable",
                      type(value).__qualname__)


def canonical_report_json(document: Mapping[str, Any]) -> bytes:
    """Canonical report bytes: sorted keys, compact separators, UTF-8."""
    try:
        text = json.dumps(document, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ReportError("papersim_report_document_unserializable",
                          str(error)) from error
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Inputs and assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportInputs:
    """Everything the assembly needs, handed in by the reader (pure data).

    ``command_run_events``/``scan_runs``/``queue_control_events``/
    ``bankroll_checks`` are read-only SELECT outputs (mappings); rows of
    the ``report`` command itself are ignored BY COMMAND so the writing
    clock can never enter the content digest.  ``wired_commands`` is the
    CLI subcommand registry at run time.  ``evaluation`` is the
    ``evaluation.evaluate_queues`` report over the SAME store read (the
    single metric face; never recomputed here).
    ``pending_recorded_by_queue`` counts entered positions with an observed
    market outcome awaiting a recorded settlement in that snapshot.
    """

    scope: str = REPORT_SCOPE_ALL
    wired_commands: frozenset[str] = frozenset()
    command_run_events: tuple[Mapping[str, Any], ...] = ()
    scan_runs: tuple[Mapping[str, Any], ...] = ()
    queue_control_events: tuple[Mapping[str, Any], ...] = ()
    bankroll_checks: tuple[Mapping[str, Any], ...] = ()
    coverage: PipelineCoverage = PipelineCoverage()
    basketball_finals: tuple[BasketballMarketSettlement, ...] = ()
    evaluation: evaluation.EvaluationReport | None = None
    stage_evidence_times: Mapping[str, datetime.datetime | None] = field(default_factory=dict)
    basketball_evidence: BasketballFinalsEvidence = BasketballFinalsEvidence()
    pending_recorded_by_queue: Mapping[tuple[str, str, str], int] = field(default_factory=dict)

    def validate(self) -> "ReportInputs":
        if type(self.scope) is not str or not self.scope \
                or self.scope != self.scope.strip():
            raise ReportError("papersim_report_scope_invalid")
        if "|" in self.scope:
            raise ReportError("papersim_report_scope_invalid",
                              "scope must not contain '|'")
        if type(self.wired_commands) is not frozenset:
            raise ReportError("papersim_report_wired_commands_invalid")
        for name in self.wired_commands:
            if type(name) is not str or not name.strip():
                raise ReportError("papersim_report_wired_commands_invalid")
        if not isinstance(self.coverage, PipelineCoverage):
            raise ReportError("papersim_report_coverage_invalid")
        self.coverage.validate()
        if not isinstance(self.evaluation, evaluation.EvaluationReport):
            raise ReportError("papersim_report_evaluation_missing")
        pending = self.pending_recorded_by_queue
        if not isinstance(pending, Mapping):
            raise ReportError("papersim_report_recorded_pnl_gap_invalid")
        if pending:
            queue_counts = {(row.queue.model_key, row.queue.market_type, row.queue.gate_version): row.counts
                            for row in self.evaluation.queues}
            if len(queue_counts) != len(self.evaluation.queues):
                raise ReportError("papersim_report_recorded_pnl_gap_invalid")
            for key, count in pending.items():
                counts = queue_counts.get(key)
                if counts is None or type(count) is not int or not 0 < count <= min(
                        counts.unresolved, counts.entered - counts.settled):
                    raise ReportError("papersim_report_recorded_pnl_gap_invalid")
        _normalized_receipts(self.command_run_events)
        _normalized_scans(self.scan_runs)
        _normalized_controls(self.queue_control_events)
        _normalized_checks(self.bankroll_checks)
        if not isinstance(self.basketball_finals, tuple):
            raise ReportError("papersim_report_basketball_rows_invalid")
        for row in self.basketball_finals:
            if not isinstance(row, BasketballMarketSettlement):
                raise ReportError("papersim_report_basketball_rows_invalid")
            row.validate()
        if not isinstance(self.stage_evidence_times, Mapping):
            raise ReportError("papersim_report_stage_evidence_invalid")
        for stage, moment in self.stage_evidence_times.items():
            if stage not in STAGE_ORDER[1:]:
                raise ReportError("papersim_report_stage_evidence_invalid")
            if moment is not None:
                _as_datetime(moment, "stage_evidence_times")
        if not isinstance(self.basketball_evidence, BasketballFinalsEvidence):
            raise ReportError("papersim_report_basketball_rows_invalid")
        return self


def _normalized_receipt(row: Mapping[str, Any]) -> dict:
    """Validate one receipt row and normalize its timestamps to datetimes."""
    if not isinstance(row, Mapping):
        raise ReportError("papersim_report_row_invalid", "receipt row")
    command = _require_text(row.get("command"), "command")
    _require_text(row.get("operation_key"), "operation_key")
    phase = _require_text(row.get("phase"), "phase")
    if phase not in store.COMMAND_PHASES:
        raise ReportError("papersim_report_row_invalid", f"phase {phase}")
    normalized = dict(row)
    normalized["command"] = command
    normalized["occurred_at"] = _as_datetime(row.get("occurred_at"),
                                             "occurred_at")
    normalized["run_event_id"] = _optional_int(row.get("run_event_id"),
                                               "run_event_id")
    return normalized


def _normalized_receipts(rows) -> tuple[dict, ...]:
    return tuple(_normalized_receipt(row) for row in rows)


def _normalized_scan(row: Mapping[str, Any]) -> dict:
    if not isinstance(row, Mapping):
        raise ReportError("papersim_report_row_invalid", "scan row")
    _require_int(row.get("scan_id"), "scan_id")
    _require_text(row.get("team"), "team")
    normalized = dict(row)
    normalized["started_at"] = _as_datetime(row.get("started_at"), "started_at")
    normalized["finished_at"] = _as_datetime(row.get("finished_at"),
                                             "finished_at")
    return normalized


def _normalized_scans(rows) -> tuple[dict, ...]:
    return tuple(_normalized_scan(row) for row in rows)


def _normalized_control(row: Mapping[str, Any]) -> dict:
    if not isinstance(row, Mapping):
        raise ReportError("papersim_report_row_invalid", "control row")
    _require_text(row.get("queue_id"), "queue_id")
    kind = _require_text(row.get("control_kind"), "control_kind")
    if kind not in store.QUEUE_CONTROL_KINDS:
        raise ReportError("papersim_report_row_invalid", f"control {kind}")
    _require_text(row.get("rule"), "rule")
    normalized = dict(row)
    normalized["observed_at"] = _as_datetime(row.get("observed_at"),
                                             "observed_at")
    normalized["control_event_id"] = _optional_int(
        row.get("control_event_id"), "control_event_id")
    return normalized


def _normalized_controls(rows) -> tuple[dict, ...]:
    return tuple(_normalized_control(row) for row in rows)


def _normalized_check(row: Mapping[str, Any]) -> dict:
    if not isinstance(row, Mapping):
        raise ReportError("papersim_report_row_invalid", "check row")
    passed = row.get("passed")
    if type(passed) is not bool:
        raise ReportError("papersim_report_row_invalid", "passed")
    normalized = dict(row)
    normalized["as_of"] = _as_datetime(row.get("as_of"), "as_of")
    normalized["check_id"] = _optional_int(row.get("check_id"), "check_id")
    normalized["event_watermark"] = _optional_int(row.get("event_watermark"),
                                                  "event_watermark")
    return normalized


def _normalized_checks(rows) -> tuple[dict, ...]:
    return tuple(_normalized_check(row) for row in rows)


def _pipeline_receipts(rows) -> tuple[dict, ...]:
    """Receipt rows minus the report's own command (clock-free content).

    Every row is validated first (a malformed row of ANY command refuses
    the assembly), then the report command's own rows are dropped.
    """
    return tuple(row for row in _normalized_receipts(rows)
                 if row["command"] != REPORT_COMMAND)


def _last_phase_by_operation(receipts: Sequence[Mapping[str, Any]]) -> list[dict]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in receipts:
        grouped.setdefault((str(row["command"]), str(row["operation_key"])),
                           []).append(row)
    result = []
    for (command, operation_key) in sorted(grouped):
        rows = grouped[(command, operation_key)]
        latest = max(rows, key=lambda row: (row["occurred_at"],
                                            row.get("run_event_id") or 0))
        result.append({
            "command": command,
            "operation_key": operation_key,
            "last_phase": str(latest["phase"]),
            "last_occurred_at": _iso(latest["occurred_at"]),
            "receipt_count": len(rows),
        })
    return result


def _latches(controls: Sequence[Mapping[str, Any]]) -> dict:
    """Fold the pause latch per queue (mirrors the store reader's fold)."""
    review: dict[str, bool] = {}
    stop: dict[str, bool] = {}
    ordered = sorted(controls, key=lambda row: (row["observed_at"],
                                                row.get("control_event_id") or 0))
    for row in ordered:
        queue = str(row["queue_id"])
        if str(row["control_kind"]) == "RESUME":
            review[queue] = False
            stop[queue] = False
            continue
        if str(row.get("rule") or "").startswith("fx17"):
            review[queue] = True
        else:
            stop[queue] = True
    return {queue: (review.get(queue, False), stop.get(queue, False))
            for queue in sorted(set(review) | set(stop))}


def _stage_as_of(stage: str, receipts: Sequence[Mapping[str, Any]],
                 evidence_time: datetime.datetime | None) \
        -> datetime.datetime | None:
    """Data-derived stage timestamp: the stage's latest own evidence."""
    command = STAGE_COMMANDS[stage]
    moments = [_as_datetime(row["occurred_at"], "occurred_at")
               for row in receipts if str(row["command"]) == command]
    if evidence_time is not None:
        moments.append(_as_datetime(evidence_time, "stage_evidence_times"))
    return _max_datetime(moments)


@dataclass(frozen=True)
class ReportAssembly:
    """One complete, validated report plus its content-addressed identity."""

    document: dict
    content: bytes
    content_sha256: str
    report_key: str
    as_of: datetime.datetime | None


def report_operation_key(scope: str, as_of: datetime.datetime | None,
                         content_sha256: str) -> str:
    """The content-addressed report operation identity (never the clock).

    ``report|<scope>|<as_of utc stamp or no-data>|<content digest>``: two
    assemblies over identical evidence derive the identical key, and the
    key changes exactly when the content (or the data-derived ``as_of``)
    changes.  The writing clock never appears.
    """
    if type(scope) is not str or not scope or scope != scope.strip() \
            or "|" in scope:
        raise ReportError("papersim_report_scope_invalid")
    if type(content_sha256) is not str or len(content_sha256) != 64 \
            or any(c not in "0123456789abcdef" for c in content_sha256):
        raise ReportError("papersim_report_digest_invalid")
    stamp = "no-data" if as_of is None else store.utc_stamp(as_of)
    return "|".join((REPORT_COMMAND, scope, stamp, content_sha256))


def assemble_report(inputs: ReportInputs) -> ReportAssembly:
    """Assemble the pipeline report document from handed-in rows (pure).

    Determinism: identical inputs assemble to byte-identical canonical
    JSON (sorted keys, compact separators), the same content digest and
    the same report key.  Every ``as_of`` is data-derived; report-command
    receipts are excluded by command; nothing is zero-filled or
    synthesized (missing coverage stays ``None``, unwired stages stay
    ``STAGE_NOT_WIRED``).
    """
    inputs.validate()
    receipts = _pipeline_receipts(inputs.command_run_events)
    scan_runs = _normalized_scans(inputs.scan_runs)
    controls = _normalized_controls(inputs.queue_control_events)
    checks = _normalized_checks(inputs.bankroll_checks)
    coverage = inputs.coverage

    counts_by_command: dict[str, int] = {}
    for row in receipts:
        counts_by_command[str(row["command"])] = \
            counts_by_command.get(str(row["command"]), 0) + 1

    # --- stages, in causal order -------------------------------------
    stages: dict[str, dict] = {}
    stage_evidence = {stage: _as_datetime(moment, "stage_evidence_times")
                      for stage, moment in inputs.stage_evidence_times.items() if moment is not None}
    stage_evidence["scan"] = _max_datetime(row["finished_at"] for row in scan_runs)
    stage_evidence["settle"] = _max_datetime((stage_evidence.get("settle"),
                                               inputs.basketball_evidence.as_of))
    stage_as_of = {stage: _stage_as_of(stage, receipts, stage_evidence.get(stage))
                   for stage in STAGE_ORDER}

    wired_scan = STAGE_COMMANDS["scan"] in inputs.wired_commands
    scan_rows = sorted(scan_runs,
                       key=lambda row: (row["finished_at"], row["scan_id"]))
    scan_last = scan_rows[-1] if scan_rows else None
    scan_status = (STAGE_STATUS_OK if scan_rows
                   else (STAGE_STATUS_NO_DATA if wired_scan
                         else STAGE_STATUS_NOT_WIRED))
    stages["scan"] = {
        "command": STAGE_COMMANDS["scan"],
        "wired": wired_scan,
        "status": scan_status,
        "row_count": len(inputs.scan_runs),
        "as_of": _iso(stage_as_of["scan"]),
        "scan_runs": len(inputs.scan_runs),
        "candidates": coverage.candidates_total,
        "entries": coverage.entries_total,
        "last_scan": (None if scan_last is None else {
            "scan_id": int(scan_last["scan_id"]),
            "team": str(scan_last["team"]),
            "started_at": _iso(scan_last["started_at"]),
            "finished_at": _iso(scan_last["finished_at"]),
            "scan_key": (None if scan_last.get("scan_key") is None
                         else str(scan_last["scan_key"])),
            "code_commit": (None if scan_last.get("code_commit") is None
                            else str(scan_last["code_commit"])),
        }),
    }

    wired_marks = STAGE_COMMANDS["marks"] in inputs.wired_commands
    stages["marks"] = {
        "command": STAGE_COMMANDS["marks"],
        "wired": wired_marks,
        "status": (STAGE_STATUS_OK if coverage.mark_observations
                   else (STAGE_STATUS_NO_DATA if wired_marks
                         else STAGE_STATUS_NOT_WIRED)),
        "row_count": coverage.mark_observations,
        "as_of": _iso(stage_as_of["marks"]),
        "candidates_total": coverage.candidates_total,
        "candidates_with_marks": coverage.candidates_with_marks,
        "coverage_ratio": _ratio(coverage.candidates_with_marks,
                                 coverage.candidates_total),
    }

    wired_settle = STAGE_COMMANDS["outcomes"] in inputs.wired_commands
    stages["outcomes"] = {
        "command": STAGE_COMMANDS["outcomes"],
        "wired": wired_settle,
        "status": (STAGE_STATUS_OK if coverage.outcome_observations
                   else (STAGE_STATUS_NO_DATA if wired_settle
                         else STAGE_STATUS_NOT_WIRED)),
        "row_count": coverage.outcome_observations,
        "as_of": _iso(stage_as_of["outcomes"]),
        "markets_bound": coverage.markets_bound,
        "markets_with_outcome": coverage.markets_with_outcome,
        "coverage_ratio": _ratio(coverage.markets_with_outcome,
                                 coverage.markets_bound),
    }

    basketball_rows = sorted(
        [row.display() for row in inputs.basketball_finals]
        + [row.display() for row in inputs.basketball_evidence.finals],
        key=lambda row: (row["league"], row["market_ref"], row.get("candidate_id", 0)))
    stages["settle"] = {
        "command": STAGE_COMMANDS["settle"],
        "wired": wired_settle,
        "status": (STAGE_STATUS_OK if coverage.entries_settled
                   else (STAGE_STATUS_NO_DATA if wired_settle
                         else STAGE_STATUS_NOT_WIRED)),
        "row_count": coverage.entries_settled,
        "as_of": _iso(stage_as_of["settle"]),
        "entries_total": coverage.entries_total,
        "entries_settled": coverage.entries_settled,
        "coverage_ratio": _ratio(coverage.entries_settled,
                                 coverage.entries_total),
        # Full-game finals (regulation + overtime, every league); partial
        # scores stay unresolved with their reason.
        "basketball_finals": basketball_rows,
        "basketball_evidence": inputs.basketball_evidence.summary(),
    }

    wired_evaluate = STAGE_COMMANDS["evaluate"] in inputs.wired_commands
    queue_keys = sorted(
        ({"model_key": queue.queue.model_key,
          "market_type": queue.queue.market_type,
          "gate_version": queue.queue.gate_version}
         for queue in inputs.evaluation.queues),
        key=lambda item: (item["model_key"], item["market_type"],
                          item["gate_version"]))
    stages["evaluate"] = {
        "command": STAGE_COMMANDS["evaluate"],
        "wired": wired_evaluate,
        "status": (STAGE_STATUS_OK if inputs.evaluation.queues
                   else (STAGE_STATUS_NO_DATA if wired_evaluate
                         else STAGE_STATUS_NOT_WIRED)),
        "row_count": len(inputs.evaluation.queues),
        "as_of": _iso(stage_as_of["evaluate"]),
        "queue_count": len(inputs.evaluation.queues),
        "queues": queue_keys,
    }
    if inputs.pending_recorded_by_queue:
        pending = inputs.pending_recorded_by_queue
        stages["evaluate"]["status"] = STAGE_STATUS_PARTIAL
        stages["evaluate"]["recorded_pnl_gap"] = {
            "reason": "settlement_not_recorded",
            "entries": sum(pending.values()),
            "by_queue": [
                {"model_key": model_key, "market_type": market_type, "gate_version": gate_version,
                 "entries": count, "recorded_pnl": None}
                for (model_key, market_type, gate_version), count in sorted(pending.items())
            ],
        }

    # --- auxiliary sections -------------------------------------------
    latched = _latches(controls)
    control_rows = sorted(controls,
                          key=lambda row: (row["observed_at"],
                                           row.get("control_event_id") or 0))
    control_last = control_rows[-1] if control_rows else None
    check_rows = sorted(checks,
                        key=lambda row: (row["as_of"],
                                         row.get("check_id") or 0))
    check_last = check_rows[-1] if check_rows else None

    overall_as_of = _max_datetime(
        list(stage_as_of.values())
        + [row["observed_at"] for row in control_rows]
        + [row["as_of"] for row in check_rows])

    document = {
        "report_command": REPORT_COMMAND,
        "algorithm_version": REPORT_ALGORITHM_VERSION,
        "scope": inputs.scope,
        "as_of": _iso(overall_as_of),
        "stage_order": list(STAGE_ORDER),
        "stages": stages,
        "receipts": {
            "counts_by_command": dict(sorted(counts_by_command.items())),
            "last_phase_by_operation": _last_phase_by_operation(receipts),
            # The report is a view over the pipeline, never over itself:
            # its own receipts are excluded BY COMMAND so the writing
            # clock cannot enter the content digest.
            "self_receipts_excluded": True,
        },
        "queue_controls": {
            "row_count": len(inputs.queue_control_events),
            "latch_by_queue": {queue: {"pause_review": flags[0],
                                       "auto_stop": flags[1]}
                               for queue, flags in sorted(latched.items())},
            "last": (None if control_last is None else {
                "queue_id": str(control_last["queue_id"]),
                "control_kind": str(control_last["control_kind"]),
                "rule": str(control_last["rule"]),
                "observed_at": _iso(control_last["observed_at"]),
            }),
        },
        "bankroll": {
            "check_count": len(inputs.bankroll_checks),
            "last_check": (None if check_last is None else {
                "as_of": _iso(check_last["as_of"]),
                "passed": bool(check_last["passed"]),
                "event_watermark": _optional_int(check_last.get("event_watermark"),
                                                 "event_watermark"),
                "identity_difference": report_json_value(
                    check_last.get("identity_difference")),
            }),
        },
        # Bit-for-bit the evaluate command output over the same store:
        # one metric face (evaluate_queues), never a second one here.
        "evaluation": report_json_value(inputs.evaluation),
        "paper_only": True,
        "report_only": True,
        "readonly": True,
    }
    content = canonical_report_json(document)
    digest = sha256(content).hexdigest()
    return ReportAssembly(document=document, content=content,
                          content_sha256=digest,
                          report_key=report_operation_key(inputs.scope,
                                                          overall_as_of,
                                                          digest),
                          as_of=overall_as_of)


# ---------------------------------------------------------------------------
# Evidence writers (pure builders over the existing store records)
# ---------------------------------------------------------------------------


def report_receipt(*, operation_key: str, phase: str,
                   occurred_at: datetime.datetime,
                   code_commit: str | None = None,
                   attempt: int = 1,
                   output_evidence: Sequence[str] = (),
                   error_code: str | None = None,
                   config_digest: str | None = None,
                   ) -> store.CommandRunEventSubmission:
    """One ``command='report'`` receipt row (validated by the store layer).

    The SUCCEEDED receipt carries the report content digest in
    ``output_evidence``; the operation key is the content-addressed
    report key, so a re-run over unchanged evidence replays the same
    scheduling identity instead of minting a fresh one from the clock.
    """
    return store.CommandRunEventSubmission(
        operation_key=operation_key,
        attempt=attempt,
        phase=phase,
        command=REPORT_COMMAND,
        occurred_at=occurred_at,
        code_commit=code_commit,
        config_digest=config_digest,
        output_evidence=list(output_evidence),
        error_code=error_code,
    )


def report_snapshot_registration(content: bytes, *, fetched_at: datetime.datetime,
                                 source: str = "papersim:report",
                                 schema_version: int =
                                 snapshots.SNAPSHOT_DEFAULT_SCHEMA_VERSION,
                                 ) -> snapshots.SnapshotRegistration:
    """The canonical report bytes as one ``papersim-report`` snapshot.

    The snapshot digest is SHA-256 of the content only (no wall clock
    inside the digest), so re-registering the same canonical bytes is
    idempotent and returns the original row.
    """
    if not isinstance(content, (bytes, bytearray)):
        raise ReportError("papersim_report_content_bytes_required")
    return snapshots.SnapshotRegistration(
        content=bytes(content),
        kind=REPORT_SNAPSHOT_KIND,
        source=source,
        fetched_at=fetched_at,
        schema_version=schema_version,
    )
