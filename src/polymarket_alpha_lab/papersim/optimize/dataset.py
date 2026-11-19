"""Read-only, provenance-preserving historical inputs for WP-P4 shadow research.

Library home (plan 05 node MO-4) of the archive parsing, chronology and
fold logic that previously lived in ``tools/wp_p4_history.py``; the tool
module is now a thin wrapper that keeps the on-disk archive reading
(``load_history``) and re-exports every historical public name.

These archives are retrospective downloads. ``kickoff + 3h`` is an explicit
completion assumption, not a claim about when xG was first published. Official
FotMob rounds are joined by provider ID or exact canonical pair and UTC time;
rounds are never inferred by dividing a sorted match list into equal chunks.

Purity discipline (design 04 section 8): standard library only, no network,
no database driver, no clock reads, no file I/O. Every entry point takes
already-read bytes or in-memory values; the only filesystem touch of this
pipeline stays in the tool wrapper (``tools/wp_p4_history.load_history``).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import unicodedata

UTC = timezone.utc
LEAGUES = {
    "EPL": (47, 20, 38), "La_liga": (87, 20, 38),
    "Serie_A": (55, 20, 38), "Bundesliga": (54, 18, 34),
    "Ligue_1": (53, 18, 34), "Championship": (48, 24, 46),
    "Eredivisie": (57, 18, 34),
}
ALIASES = {
    "EPL": {"AFC Bournemouth": "Bournemouth", "Brighton & Hove Albion": "Brighton",
            "Coventry City": "Coventry", "Hull City": "Hull", "Ipswich Town": "Ipswich",
            "Leeds United": "Leeds", "Tottenham Hotspur": "Tottenham"},
    "La_liga": {"Deportivo A Coruña": "Deportivo La Coruna", "Deportivo Alavés": "Alaves"},
    "Serie_A": {"Milan": "AC Milan", "Parma": "Parma Calcio 1913"},
    "Bundesliga": {"1. FC Köln": "FC Cologne", "Bayern München": "Bayern Munich",
                   "Borussia Mönchengladbach": "Borussia M.Gladbach",
                   "RB Leipzig": "RasenBallsport Leipzig"},
}


def utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            # Documented Understat/FotMob normalized wire format is UTC.
            result = result.replace(tzinfo=UTC)
    else:
        raise ValueError("timestamp must be an ISO string or datetime")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class HistoricalMatch:
    match_id: str
    league: str
    season: str
    matchweek: int | None
    kickoff: datetime
    home_team: str
    away_team: str
    home_xg: float
    away_xg: float
    home_goals: int
    away_goals: int
    source_match_id: str = ""
    schedule_match_id: str | None = None
    source_path: str = ""
    available_at: datetime | None = None
    source_kickoff: datetime | None = None


@dataclass(frozen=True)
class ScheduleFixture:
    fixture_id: str
    league: str
    season: str
    matchweek: int
    kickoff: datetime
    home_team: str
    away_team: str
    finished: bool
    cancelled: bool
    awarded: bool
    score: str | None = None


@dataclass(frozen=True)
class HistoryAudit:
    matches: tuple[HistoricalMatch, ...]
    schedule: tuple[ScheduleFixture, ...]
    sources: tuple[dict, ...]
    exclusions: tuple[dict, ...]
    rosters: tuple[tuple[str, str, str, str], ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fold:
    league: str
    season: str
    matchweek: int
    cutoff: datetime
    train: tuple[HistoricalMatch, ...]
    test: tuple[HistoricalMatch, ...]
    completed_matchweeks: frozenset[tuple[str, str, int | None]]
    completion_lag_hours: float = 3.0

    @property
    def block_id(self) -> str:
        return f"{self.league}:{self.season}:round-{self.matchweek}"


def _norm(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


def canonical_team(league: str, name: str, roster: set[str]) -> str:
    target = ALIASES.get(league, {}).get(name, name)
    if target in roster:
        return target
    hits = [title for title in roster if _norm(title) == _norm(target)]
    if len(hits) != 1:
        raise ValueError(f"unresolved/ambiguous {league} team alias: {name!r}")
    return hits[0]


def parse_schedule(payload: dict, league: str, season: str,
                   roster: set[str]) -> tuple[ScheduleFixture, ...]:
    if payload.get("details", {}).get("selectedSeason") != f"{season}/{int(season) + 1}":
        raise ValueError(f"official schedule season mismatch: {league}/{season}")
    limit = LEAGUES[league][2]
    result = []
    seen = set()
    for row in payload["fixtures"]["allMatches"]:
        round_text = str(row.get("round", ""))
        if not round_text.isdigit() or not 1 <= int(round_text) <= limit:
            continue  # Playoffs are outside the domestic league-season contract.
        mid = str(row["id"])
        if mid in seen:
            raise ValueError(f"duplicate schedule ID: {league}/{season}/{mid}")
        seen.add(mid)
        status = row["status"]
        result.append(ScheduleFixture(
            mid, league, season, int(round_text), utc(status["utcTime"]),
            canonical_team(league, row["home"]["name"], roster),
            canonical_team(league, row["away"]["name"], roster),
            status.get("finished") is True, status.get("cancelled") is True,
            status.get("awarded") is True, status.get("scoreStr"),
        ))
    return tuple(result)


def _number(value: object, *, integer: bool = False) -> float | int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a match statistic")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (integer and number != int(number)):
        raise ValueError("match statistics must be finite nonnegative values")
    return int(number) if integer else number


def parse_matches(block: dict, league: str, season: str,
                  schedule: tuple[ScheduleFixture, ...], source_path: str = ""
                  ) -> tuple[tuple[HistoricalMatch, ...], tuple[dict, ...]]:
    """Use dates once; team.history is a duplicated view and is never ingested."""
    by_id = {r.fixture_id: r for r in schedule}
    by_pair = defaultdict(list)
    by_clubs = defaultdict(list)
    for row in schedule:
        by_pair[(row.home_team, row.away_team, row.kickoff)].append(row)
        by_clubs[(row.home_team, row.away_team)].append(row)
    matches, exclusions, seen_ids, seen_pairs = [], [], set(), set()
    for row in block["dates"]:
        if row.get("isResult") is not True:
            continue
        source_id = str(row["id"])
        home, away = row["h"]["title"], row["a"]["title"]
        kickoff = utc(row["datetime"])
        original_kickoff = kickoff
        key = (home, away, kickoff)
        hg, ag = _number(row["goals"]["h"], integer=True), _number(row["goals"]["a"], integer=True)
        official = by_id.get(source_id)
        if official is None and schedule:
            candidates = by_pair.get(key, [])
            if len(candidates) != 1:
                # Providers retain scheduled versus delayed kickoffs differently.
                # A unique oriented pair AND exact score AND at most 48h drift
                # establishes identity. Use the later time for training safety;
                # build_folds uses the earlier time for prediction cutoffs.
                candidates = [f for f in by_clubs.get((home, away), [])
                              if f.finished and not f.cancelled and not f.awarded
                              and f.score is not None and f.score.replace(" ", "") == f"{hg}-{ag}"
                              and abs(f.kickoff - kickoff) <= timedelta(hours=48)]
            if len(candidates) != 1:
                exclusions.append({"league": league, "season": season,
                                   "source_match_id": source_id,
                                   "reason": "official_identity_or_time_conflict",
                                   "home_team": home, "away_team": away,
                                   "source_kickoff": iso(kickoff)})
                continue
            official = candidates[0]
        if official is not None:
            if (official.home_team, official.away_team) != (home, away):
                raise ValueError(f"schedule identity/time conflict: {league}/{season}/{source_id}")
            if official.cancelled or official.awarded or not official.finished:
                exclusions.append({"league": league, "season": season,
                                   "source_match_id": source_id,
                                   "reason": "cancelled_abandoned_awarded_or_unfinished",
                                   "official_fixture_id": official.fixture_id})
                continue
            kickoff = max(kickoff, official.kickoff)
        if official is not None and official.score is not None:
            if official.score.replace(" ", "") != f"{hg}-{ag}":
                raise ValueError(f"schedule/result score conflict: {league}/{season}/{source_id}")
        if source_id in seen_ids or key in seen_pairs:
            raise ValueError(f"duplicate normalized match: {league}/{season}/{source_id}")
        seen_ids.add(source_id)
        seen_pairs.add(key)
        matches.append(HistoricalMatch(
            f"{league}:{season}:{source_id}", league, season,
            official.matchweek if official else None, kickoff, home, away,
            _number(row["xG"]["h"]), _number(row["xG"]["a"]), hg, ag,
            source_id, official.fixture_id if official else None, source_path,
            source_kickoff=original_kickoff,
        ))
    return tuple(sorted(matches, key=lambda m: (m.kickoff, m.match_id))), tuple(exclusions)


#: Archive-relative paths the reader consults unconditionally, in read order.
#: ``.meta.json`` companions are optional presence probes, exactly as the
#: filesystem reader treated them.
ARCHIVE_POINTER_PATH = "understat_latest_pointer.json"
ARCHIVE_BIG5_PATH = "understat_latest.json"
ARCHIVE_LEAGUE_PATHS = {
    "Championship": "round31/raw/xg_fotmob_championship.json",
    "Eredivisie": "round39/raw/xg_fotmob_eredivisie.json",
}


def archive_read_paths() -> tuple[str, ...]:
    """Every relative path the archive parser may consult, in read order.

    Fixture-map paths depend on the league-season blocks found in the
    payloads, so :func:`archive_fixture_paths` derives them separately once
    the fixed files are read. Tool readers use these two helpers to collect
    exactly the bytes the pure parser is allowed to see.
    """
    paths = [ARCHIVE_POINTER_PATH, ARCHIVE_BIG5_PATH]
    for path in ARCHIVE_LEAGUE_PATHS.values():
        paths.append(path)
        paths.append(path + ".meta.json")
    paths.append(ARCHIVE_BIG5_PATH + ".meta.json")
    return tuple(paths)


def archive_fixture_paths(files) -> tuple[str, ...]:
    """Official FotMob fixture-map paths implied by the payload blocks.

    Given the already-read fixed archive files, enumerate the
    ``tools/fotmob_cache/fixtures/leagues_<id>_<season>.json`` paths the
    parser would probe, so a filesystem reader knows the complete declared
    set before handing bytes to :func:`history_from_archive`.
    """
    payloads = []
    for path in (ARCHIVE_BIG5_PATH, *ARCHIVE_LEAGUE_PATHS.values()):
        blob = files.get(path)
        if blob is None:
            continue
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue  # history_from_archive re-raises with archive context.
        if not isinstance(payload, dict):
            continue
        payloads.append(payload)
    seen, result = set(), []
    for payload in payloads:
        for key in payload:
            if not isinstance(key, str) or "_" not in key:
                continue
            league = key.rsplit("_", 1)[0]
            if league not in LEAGUES:
                continue
            season = key[len(league) + 1:]
            relative = f"tools/fotmob_cache/fixtures/leagues_{LEAGUES[league][0]}_{season}.json"
            if relative not in seen:
                seen.add(relative)
                result.append(relative)
    return tuple(result)


def history_from_archive(files) -> HistoryAudit:
    """Parse the declared archive bytes; never fetch, execute tools, or write.

    ``files`` maps archive-relative paths to the exact bytes the reader may
    see: a key's PRESENCE plays the role the filesystem existence check
    played before (meta companions and official fixture maps are optional;
    every other declared file must be present or the parse fails closed).
    Unknown extra keys are ignored. Source records carry ``path``/``sha256``/
    ``bytes``; the filesystem reader re-attaches ``resolved_path``.
    """
    sources, notes, exclusions, matches, schedule, rosters = [], [], [], [], [], []
    read_cache: dict[str, object] = {}

    def read(relative: str):
        if relative not in files:
            raise ValueError(f"declared archive file missing: {relative}")
        if relative not in read_cache:
            content = files[relative]
            read_cache[relative] = json.loads(content)
            sources.append({"path": relative,
                            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
        return read_cache[relative]

    pointer = read(ARCHIVE_POINTER_PATH)
    big5 = read(ARCHIVE_BIG5_PATH)
    digest = next(s["sha256"] for s in sources if s["path"] == ARCHIVE_BIG5_PATH)
    if pointer.get("sha256") != digest:
        raise ValueError("understat_latest pointer SHA256 does not match target bytes")
    notes.append("Latest big-five snapshot mixes Understat through 2026-10-31 with FotMob recovery rows on 2026-11-01/02; sources are not independent replications.")
    snapshots = {lg: (ARCHIVE_BIG5_PATH, big5) for lg in LEAGUES if lg not in {"Championship", "Eredivisie"}}
    for lg, path in ARCHIVE_LEAGUE_PATHS.items():
        snapshots[lg] = (path, read(path))
        if (path + ".meta.json") in files:
            read(path + ".meta.json")
    if (ARCHIVE_BIG5_PATH + ".meta.json") in files:
        read(ARCHIVE_BIG5_PATH + ".meta.json")
    for league, (lid, team_count, _) in LEAGUES.items():
        path, payload = snapshots[league]
        blocks = sorted((key, value) for key, value in payload.items() if key.startswith(league + "_"))
        for key, block in blocks:
            season = key.removeprefix(league + "_")
            roster = {row["title"] for row in block["teams"].values()}
            if len(roster) != team_count:
                raise ValueError(f"unexpected roster count: {key}")
            rosters.extend((league, season, row["title"], str(row["id"])) for row in block["teams"].values())
            fixture_path = f"tools/fotmob_cache/fixtures/leagues_{lid}_{season}.json"
            official = parse_schedule(read(fixture_path), league, season, roster) if fixture_path in files else ()
            if not official:
                notes.append(f"{key}: official round map absent; eligible only as verified complete prior-season warmup, not round-level evaluation.")
            parsed, omitted = parse_matches(block, league, season, official, path)
            for match in parsed:
                if match.source_kickoff != match.kickoff:
                    notes.append(f"{match.match_id}: kickoff conflict reconciled by oriented pair/exact score; original={iso(match.source_kickoff)} safe-completion-clock={iso(match.kickoff)}; prediction cutoff uses earlier source clock.")
            matches.extend(parsed)
            exclusions.extend(omitted)
            schedule.extend(official)
    notes.append("Event-time replay only: archived final xG and final fixture dates lack original publication timestamps/revision histories; available_at remains null.")
    return HistoryAudit(tuple(sorted(matches, key=lambda m: (m.league, m.season, m.kickoff, m.match_id))),
                        tuple(schedule), tuple(sources), tuple(exclusions), tuple(rosters), tuple(notes))


def _complete_season(audit: HistoryAudit, league: str, season: str,
                     rows: tuple[HistoricalMatch, ...]) -> bool:
    n = LEAGUES[league][1]
    if len(rows) != n * (n - 1):
        return False
    homes, aways = Counter(m.home_team for m in rows), Counter(m.away_team for m in rows)
    pairs = {(m.home_team, m.away_team) for m in rows}
    return (len(pairs) == n * (n - 1) and len(homes) == len(aways) == n
            and all(value == n - 1 for value in [*homes.values(), *aways.values()]))


def _round_rows(audit: HistoryAudit, league: str, season: str):
    expected = LEAGUES[league][1] // 2
    fixtures, results = defaultdict(list), defaultdict(list)
    for row in audit.schedule:
        if row.league == league and row.season == season and not row.cancelled and not row.awarded:
            fixtures[row.matchweek].append(row)
    for row in audit.matches:
        if row.league == league and row.season == season and row.matchweek is not None:
            results[row.matchweek].append(row)
    complete = {}
    for rnd, fx in fixtures.items():
        rows = results.get(rnd, [])
        teams = [name for f in fx for name in (f.home_team, f.away_team)]
        complete[rnd] = (len(fx) == expected and len(set(teams)) == 2 * expected
                         and all(f.finished for f in fx) and len(rows) == expected
                         and {r.schedule_match_id for r in rows} == {f.fixture_id for f in fx})
    return fixtures, results, complete


def history_at(audit: HistoryAudit, league: str, season: str, as_of: datetime,
               *, completion_lag_hours: float = 3.0,
               maximum_round: int | None = None) -> tuple[tuple[HistoricalMatch, ...], frozenset]:
    """Safe complete groups at a time; scans may use a noncontiguous group set.

    ``build_folds`` adds the stronger contiguous-prefix requirement for T3.
    Prior seasons must pass the full double-round-robin cardinality check.
    """
    if not math.isfinite(completion_lag_hours) or completion_lag_hours < 3:
        raise ValueError("completion lag must be finite and at least three hours")
    cutoff = utc(as_of)
    lag = timedelta(hours=completion_lag_hours)
    train, groups = [], set()
    for prior in sorted({m.season for m in audit.matches if m.league == league and int(m.season) < int(season)}):
        rows = tuple(m for m in audit.matches if m.league == league and m.season == prior)
        if _complete_season(audit, league, prior, rows) and all(m.kickoff + lag < cutoff for m in rows):
            train.extend(rows)
            groups.update((league, prior, m.matchweek) for m in rows)
    _, results, complete = _round_rows(audit, league, season)
    for rnd in sorted(results):
        if maximum_round is not None and rnd > maximum_round:
            continue
        rows = results[rnd]
        if complete.get(rnd) and all(m.kickoff + lag < cutoff for m in rows):
            train.extend(rows)
            groups.add((league, season, rnd))
    return tuple(sorted(train, key=lambda m: (m.kickoff, m.match_id))), frozenset(groups)


def build_folds(audit: HistoryAudit, league: str | None = None, season: str | None = None,
                *, min_train_rounds: int = 2, completion_lag_hours: float = 3.0
                ) -> tuple[tuple[Fold, ...], tuple[dict, ...]]:
    """Literal expanding prefix: rounds 1..k train; complete round k+1 test.

    A postponed old-round game blocks the affected fold. A complete target
    round's predictions are frozen before its earliest kickoff, even when its
    other fixtures were later rescheduled. Such final-schedule replay cannot
    establish the historical availability of fixture announcements.
    """
    if isinstance(min_train_rounds, bool) or min_train_rounds < 1:
        raise ValueError("at least one training round is required")
    folds, skipped = [], []
    blocks = sorted({(m.league, m.season) for m in audit.matches
                     if (league is None or m.league == league) and (season is None or m.season == season)})
    lag = timedelta(hours=completion_lag_hours)
    for lg, yr in blocks:
        fixtures, results, complete = _round_rows(audit, lg, yr)
        if not fixtures:
            skipped.append({"league": lg, "season": yr, "reason": "missing_official_round_map"})
            continue
        for target in sorted(fixtures):
            if target <= min_train_rounds:
                continue
            common = {"league": lg, "season": yr, "matchweek": target}
            if not complete.get(target):
                skipped.append({**common, "reason": "target_round_incomplete"})
                continue
            cutoff = min([f.kickoff for f in fixtures[target]] +
                         [m.source_kickoff or m.kickoff for m in results[target]])
            missing = [rnd for rnd in range(1, target) if not complete.get(rnd)
                       or any(m.kickoff + lag >= cutoff for m in results.get(rnd, []))]
            if missing:
                skipped.append({**common, "reason": "prior_round_prefix_incomplete_at_cutoff",
                                "cutoff": iso(cutoff), "rounds": missing})
                continue
            train, groups = history_at(audit, lg, yr, cutoff,
                                       completion_lag_hours=completion_lag_hours, maximum_round=target - 1)
            test = tuple(sorted(results[target], key=lambda m: (m.kickoff, m.match_id)))
            folds.append(Fold(lg, yr, target, cutoff, train, test, groups, completion_lag_hours))
    return tuple(sorted(folds, key=lambda f: (f.cutoff, f.league, f.season, f.matchweek))), tuple(skipped)


def to_model_history(fold: Fold):
    from polymarket_alpha_lab.papersim.recalib import History, Match
    fields = ("match_id", "league", "season", "matchweek", "kickoff", "home_team", "away_team",
              "home_xg", "away_xg", "home_goals", "away_goals", "available_at")
    return History(as_of=fold.cutoff, matches=tuple(Match(**{key: getattr(m, key) for key in fields}) for m in fold.train),
                   completed_matchweeks=fold.completed_matchweeks, target_seasons={fold.league: fold.season},
                   information_mode="event_time_replay")


def production_snapshot(audit: HistoryAudit, fold: Fold) -> dict | None:
    """Rebuild both production views from the same permitted observations once."""
    previous = str(int(fold.season) - 1)
    prior = tuple(m for m in fold.train if m.season == previous)
    if not prior or not _complete_season(audit, fold.league, previous, prior):
        return None  # Never synthesize an unavailable 2024 season or reuse current as last.
    result = {}
    for yr in (previous, fold.season):
        team_ids = {title: tid for lg, sy, title, tid in audit.rosters if lg == fold.league and sy == yr}
        teams = {tid: {"id": tid, "title": title, "history": []} for title, tid in team_ids.items()}
        dates = []
        for m in fold.train:
            if m.season != yr:
                continue
            stamp = m.kickoff.strftime("%Y-%m-%d %H:%M:%S")
            for side, title, xgf, xga, gf, ga in [
                ("h", m.home_team, m.home_xg, m.away_xg, m.home_goals, m.away_goals),
                ("a", m.away_team, m.away_xg, m.home_xg, m.away_goals, m.home_goals),
            ]:
                teams[team_ids[title]]["history"].append({"h_a": side, "date": stamp,
                    "xG": xgf, "xGA": xga, "scored": gf, "missed": ga,
                    "result": "w" if gf > ga else "l" if gf < ga else "d"})
            dates.append({"id": m.source_match_id or m.match_id, "isResult": True,
                          "h": {"id": team_ids[m.home_team], "title": m.home_team},
                          "a": {"id": team_ids[m.away_team], "title": m.away_team},
                          "datetime": stamp, "goals": {"h": str(m.home_goals), "a": str(m.away_goals)},
                          "xG": {"h": str(m.home_xg), "a": str(m.away_xg)}})
        result[f"{fold.league}_{yr}"] = {"teams": teams, "dates": dates}
    return result


def inventory(audit: HistoryAudit) -> list[dict]:
    result = []
    for lg, yr in sorted({(m.league, m.season) for m in audit.matches}):
        rows = [m for m in audit.matches if m.league == lg and m.season == yr]
        _, _, completed = _round_rows(audit, lg, yr)
        now = {title for l, s, title, _ in audit.rosters if (l, s) == (lg, yr)}
        before = {title for l, s, title, _ in audit.rosters if l == lg and s == str(int(yr) - 1)}
        result.append({"league": lg, "season": yr, "completed_matches": len(rows),
                       "first_kickoff": iso(min(m.kickoff for m in rows)),
                       "last_kickoff": iso(max(m.kickoff for m in rows)),
                       "official_rounds_available": bool(completed),
                       "complete_rounds_at_archive": sorted(k for k, v in completed.items() if v),
                       "prior_roster_observed": bool(before),
                       "new_to_league": sorted(now - before) if before else None})
    return result
