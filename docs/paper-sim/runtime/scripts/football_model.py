"""Refresh the project soccer model from public results and FotMob schedules."""

from __future__ import annotations

from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import time

from polymarket_alpha_lab.papersim import fotmob, soccer_model, xg

from football_feed import (clean_label, document_digest, number, numeric_id, public_budget,
                           record, same_team, stamp, team_key, utc_iso)


@dataclass(frozen=True)
class Competition:
    model_league: str
    fotmob_id: int
    rounds: int
    matches_per_season: int


COMPETITIONS = {
    "epl": Competition("EPL", 47, 38, 380), "lal": Competition("La_liga", 87, 38, 380),
    "sea": Competition("Serie_A", 55, 38, 380), "bun": Competition("Bundesliga", 54, 34, 306),
    "fl1": Competition("Ligue_1", 53, 34, 306), "elc": Competition("Championship", 48, 46, 552),
    "ere": Competition("Eredivisie", 57, 34, 306),
}
# These public historical exports accelerate a cold start. The rolling public
# schedule and match sources also work when an export is absent or a season changes.
SEED_ROOT = Path(os.environ.get("PAL_FOOTBALL_SEED_ROOT") or Path.home() / "paper-sim").expanduser()
PUBLIC_SEEDS = (
    SEED_ROOT / "understat_latest.json",
    SEED_ROOT / "championship_20261108" / "xg_fotmob_championship.json",
    SEED_ROOT / "round41" / "raw" / "xg_fotmob_eredivisie.json",
)
# The ten-minute scan reaches FotMob after Gamma discovery, so leave room for
# normal request timing differences between successive scheduled scans.
CALENDAR_AGE = timedelta(minutes=9)
MODEL_AGE = timedelta(minutes=25)
CALENDAR_RECHECK = timedelta(days=7)
MODEL_REFRESH_SECONDS = 108
HISTORY_REQUEST_LIMIT = 96
HISTORY_RETRY = timedelta(minutes=40)
_SCORE = re.compile(r"(\d{1,2})\s*[-:–]\s*(\d{1,2})")


def _season(kickoff):
    return kickoff.year if kickoff.month >= 7 else kickoff.year - 1


def _calendar_url(competition, season):
    return f"https://{fotmob.HOST}/api/data/leagues?id={competition.fotmob_id}&season={season}%2F{season + 1}"


def details_url(match_id):
    return f"https://{fotmob.HOST}/api/data/matchDetails?matchId={match_id}"


def _score(value):
    matched = _SCORE.fullmatch(value) if isinstance(value, str) else None
    return tuple(map(int, matched.groups())) if matched else None


def _calendar(competition, season, deadline, clock):
    raw_digest = None

    def hashed(_, raw):
        nonlocal raw_digest
        raw_digest = hashlib.sha256(raw).hexdigest()

    payload = fotmob.fetch_fotmob_league(competition.fotmob_id, f"{season}/{season + 1}", timeout=7,
                                        allow_public_fetch=True, budget=public_budget(deadline, requests=2),
                                        response_sink=hashed)
    return _calendar_payload(competition, season, payload, raw_digest, clock())


def _calendar_payload(competition, season, payload, raw_digest, observed):
    if numeric_id(record(payload.get("details")).get("id")) != str(competition.fotmob_id):
        raise ValueError("unexpected FotMob league")
    matches = fotmob._parse_league_payload(payload, str(season))
    calendar = {}
    for match in matches:
        rd = numeric_id(match.get("round"))
        if rd is None or not 1 <= int(rd) <= competition.rounds:
            continue
        status = match["status"]
        kickoff = stamp(status.get("utcTime"))
        match_id = numeric_id(match.get("id"))
        home, away = match["home"], match["away"]
        home_id, away_id = numeric_id(home.get("id")), numeric_id(away.get("id"))
        if kickoff is None or match_id is None or home_id is None or away_id is None or home_id == away_id:
            raise ValueError("invalid FotMob fixture identifiers")
        if match_id in calendar:
            raise ValueError("duplicate FotMob fixture identifier")
        state = "scheduled"
        if fotmob._is_cancelled(match):
            state = "cancelled"
        elif status.get("postponed") is True:
            state = "postponed"
        elif status.get("finished") is True:
            state = "finished" if status.get("awarded") is not True else "awarded"
        elif status.get("started") is True:
            state = "started"
        calendar[match_id] = {
            "kickoff": utc_iso(kickoff), "home_id": home_id, "away_id": away_id,
            "home": clean_label(home.get("name")), "away": clean_label(away.get("name")),
            "home_short": clean_label(home.get("shortName")), "away_short": clean_label(away.get("shortName")),
            "round": int(rd),
            "state": state, "score": list(_score(status.get("scoreStr")) or ()),
        }
    if not calendar or raw_digest is None or len(calendar) > competition.matches_per_season * 2:
        raise ValueError("invalid FotMob regular season")
    return {"calendar": calendar, "calendar_at": utc_iso(observed), "calendar_sha256": raw_digest,
            "source": _calendar_url(competition, season)}


def _seed_rows(paths, desired):
    found = {}
    for path in paths:
        try:
            with path.open("rb") as source:
                raw = source.read(12 * 1024**2 + 1)
            if len(raw) > 12 * 1024**2:
                continue
            data = record(json.loads(raw))
        except (OSError, ValueError):
            continue
        digest = hashlib.sha256(raw).hexdigest()
        for key in desired & data.keys():
            rows = record(data[key]).get("dates")
            if isinstance(rows, list) and 0 < len(rows) <= 650:
                found.setdefault(key, (digest, rows))
    return found


def _result(home_xg, away_xg, home_goals, away_goals):
    hxg, axg = number(home_xg), number(away_xg)
    hg, ag = numeric_id(home_goals), numeric_id(away_goals)
    if hxg is None or axg is None or not (0 <= hxg <= 30 and 0 <= axg <= 30 and hg and ag):
        return None
    if int(hg) > 99 or int(ag) > 99:
        return None
    return [hxg, axg, int(hg), int(ag)]


def _seed_verified(seeded, calendar, verified):
    by_pair = defaultdict(list)
    matched_ids = {}
    for source in seeded:
        row = record(source)
        if row.get("isResult") is not True:
            continue
        at = stamp(row.get("datetime"), naive_utc=True)
        goals, expected = record(row.get("goals")), record(row.get("xG"))
        result = _result(expected.get("h"), expected.get("a"), goals.get("h"), goals.get("a"))
        if at is None or result is None:
            continue
        sides = record(row.get("h")), record(row.get("a"))
        left, right = (team_key(side.get("title")) for side in sides)
        if left and right and left != right:
            by_pair[(left, right)].append((at, result))
        if numeric_id(row.get("id")) and all(numeric_id(side.get("id")) for side in sides):
            matched_ids[str(row["id"])] = (at, result, tuple(str(side["id"]) for side in sides))
    for match_id, match in calendar.items():
        if match_id in verified or match["state"] != "finished" or len(match["score"]) != 2:
            continue
        at = stamp(match["kickoff"])
        direct = matched_ids.get(match_id)
        if direct and direct[2] == (match["home_id"], match["away_id"]) and direct[0] == at and direct[1][2:] == match["score"]:
            verified[match_id] = direct[1]
            continue
        # Understat and FotMob use different team IDs. The ordered team names,
        # verified final score, and a unique clock match establish the join.
        choices = [score for kickoff, score in by_pair[(team_key(match["home"]), team_key(match["away"]))]
                   if at is not None and abs(at - kickoff) <= timedelta(hours=2) and score[2:] == match["score"]]
        if len(choices) == 1:
            verified[match_id] = choices[0]


def _prematch_identity(payload):
    general = record(payload.get("general"))
    status = record(record(payload.get("header")).get("status"))
    return general, status, (
        numeric_id(general.get("matchId")),
        numeric_id(general.get("parentLeagueId")) or numeric_id(general.get("leagueId")),
        numeric_id(record(general.get("homeTeam")).get("id")),
        numeric_id(record(general.get("awayTeam")).get("id")),
    )


def _historical_result(competition, match_id, match, deadline):
    try:
        raw = fotmob.fetch_match_details(match_id, timeout=7, allow_public_fetch=True,
                                         budget=public_budget(deadline, requests=2))
        general, status, identity = _prematch_identity(raw)
        kickoff = stamp(match["kickoff"])
        if (identity != (match_id, str(competition.fotmob_id), match["home_id"], match["away_id"])
                or stamp(general.get("matchTimeUTCDate")) != kickoff or stamp(status.get("utcTime")) != kickoff
                or status.get("finished") is not True or status.get("awarded") is True
                or fotmob._is_cancelled({"status": status})):
            return None
        expected = fotmob._extract_xg_pair(raw, "Expected goals (xG)")
        goals = fotmob._extract_score(raw)
        if expected is not None and goals is not None and list(goals) == match["score"]:
            return _result(*expected, *goals)
    except Exception:
        return None
    return None


def _finished(calendar, as_of):
    return {match_id: row for match_id, row in calendar.items()
            if row["state"] == "finished" and stamp(row["kickoff"]) < as_of}


def _known_result(cached, candidate):
    if not isinstance(cached, list) or len(cached) != 4:
        return None
    valid = _result(*cached)
    return valid if valid and valid[2:] == candidate["score"] else None


def _block(absolute_ids, results, required_teams):
    teams = {team_id: {"title": team_id, "history": []} for team_id in required_teams}
    dates = []
    for match_id, row in sorted(absolute_ids.items(), key=lambda item: (item[1]["kickoff"], item[0])):
        home_xg, away_xg, home_goals, away_goals = results[match_id]
        dates.append({"xG": {"h": home_xg, "a": away_xg}, "isResult": True})
        for team_id, xg_for, xg_against, goals_for, goals_against in (
            (row["home_id"], home_xg, away_xg, home_goals, away_goals),
            (row["away_id"], away_xg, home_xg, away_goals, home_goals),
        ):
            if team_id in teams:
                teams[team_id]["history"].append({"date": row["kickoff"], "xG": xg_for, "xGA": xg_against,
                                                   "scored": goals_for, "missed": goals_against})
    return {"teams": teams, "dates": dates}


def _match_fixture(fixture, calendar):
    kickoff = stamp(fixture["kickoff"])

    def matching(row, side):
        return any(same_team(fixture[name], row[fotmob_name])
                   for name in (side, f"{side}_alias") for fotmob_name in (side, f"{side}_short"))

    options = [(match_id, row) for match_id, row in calendar.items()
               if stamp(row["kickoff"]) == kickoff and matching(row, "home") and matching(row, "away")]
    if len(options) != 1:
        return None
    return options[0]


def _fixture_model(fixture, match, setup, current, prior, as_of):
    season, competition = setup
    eligible_prior = _finished(prior.get("calendar", {}), as_of)
    if len(eligible_prior) != competition.matches_per_season or eligible_prior.keys() - prior["results"].keys():
        return None
    required = {match["home_id"], match["away_id"]}
    completed = _finished(current["calendar"], as_of)
    relevant = {match_id: row for match_id, row in completed.items()
                if {row["home_id"], row["away_id"]} & required}
    if relevant.keys() - current["results"].keys():
        return None
    league = competition.model_league
    previous = _block(eligible_prior, prior["results"], required)
    recent = _block(relevant, current["results"], required)
    if any(not recent["teams"][team_id]["history"] and not previous["teams"][team_id]["history"] for team_id in required):
        return None
    snapshot = {f"{league}_{season - 1}": previous, f"{league}_{season}": recent}
    home_id, away_id = match["home_id"], match["away_id"]
    variants = soccer_model.fixture_variants(snapshot, home_id, league, away_id, league, season=str(season))
    if variants is None or len(variants) != 10:
        return None
    forms = [xg.recent_form(snapshot, team_id, season=str(season)) for team_id in (home_id, away_id)]
    hits = sum(form.over_2_5_hits for form in forms) if all(form and form.games == 5 for form in forms) else None
    digest = document_digest({"fixture_id": fixture["fixture_id"], "kickoff": fixture["kickoff"], "snapshot": snapshot})
    return {"season": str(season), "league": league, "observed_at": current["calendar_at"],
            "source": current["source"], "sha256": digest, "history_count": [len(relevant), len(eligible_prior)],
            "variant_names": [variant.spec.name for variant in variants],
            "lambdas": [list(variant.lambdas) for variant in variants], "over_2_5_hits": hits,
            "fixture_id": fixture["fixture_id"], "team_ids": [home_id, away_id], "kickoff": fixture["kickoff"]}


def refresh_models(fixtures, clock, source_cache):
    """Attach freshly matched xG inputs and return a portable rolling source cache."""
    as_of = clock()
    touched = defaultdict(list)
    for fixture in fixtures:
        if fixture["league"] in COMPETITIONS and fixture["state"] == "scheduled":
            season = _season(stamp(fixture["kickoff"]))
            touched[(fixture["league"], season)].append(fixture)
    cache = record(source_cache)
    cached = record(cache.get("seasons")) if cache.get("version") == 1 else {}
    deadline = time.monotonic() + MODEL_REFRESH_SECONDS
    # Results and xG feed the domestic study even through byes and gaps in
    # Polymarket listings. Only the fixture modeling loop needs a listing.
    current_keys = set(touched) | {(code, _season(as_of)) for code in COMPETITIONS}
    targets = current_keys | {(code, year - 1) for code, year in current_keys}
    model_inputs = {}
    sources = {}

    def prepare(key):
        code, year = key
        previous = record(cached.get(f"{code}:{year}"))
        prior_at = stamp(previous.get("calendar_at"))
        max_age = CALENDAR_AGE if key in current_keys else CALENDAR_RECHECK
        if prior_at is not None and timedelta(0) <= as_of - prior_at <= max_age and previous.get("calendar"):
            return key, previous.copy(), None
        try:
            fetched = _calendar(COMPETITIONS[code], year, deadline, clock)
        except Exception:
            return key, previous.copy(), "calendar_unavailable"
        fetched.update(results=record(previous.get("results")), retry=record(previous.get("retry")),
                       seed_sha256=previous.get("seed_sha256"))
        return key, fetched, None

    with ThreadPoolExecutor(max_workers=5) as pool:
        for key, block, error in pool.map(prepare, sorted(targets)):
            model_inputs[key] = block
            sources[f"{key[0]}:{key[1]}"] = {"error": error, "scheduled": len(record(block.get("calendar")))}

    desired = {f"{COMPETITIONS[code].model_league}_{year}" for code, year in targets}
    seed = _seed_rows(PUBLIC_SEEDS, desired)
    model_stamp = clock()
    missing_by_league = defaultdict(deque)
    for key, block in model_inputs.items():
        code, year = key
        calendar = record(block.get("calendar"))
        if not calendar:
            continue
        finished = _finished(calendar, model_stamp)
        past = record(block.get("results"))
        saved = {match_id: score for match_id, row in finished.items() if (score := _known_result(past.get(match_id), row))}
        inputs = seed.get(f"{COMPETITIONS[code].model_league}_{year}")
        if inputs and len(saved) < len(finished):
            _seed_verified(inputs[1], finished, saved)
            block["seed_sha256"] = inputs[0]
        block["results"] = saved
        retry = record(block.get("retry"))
        block["retry"] = {mid: at for mid, at in retry.items() if mid in finished and mid not in saved and stamp(at)}
        required = set()
        for fixture in touched.get(key, ()):
            matching = _match_fixture(fixture, calendar)
            if matching:
                required.update((matching[1]["home_id"], matching[1]["away_id"]))
        by_priority = sorted(finished, key=lambda mid: (
            not bool(required & {finished[mid]["home_id"], finished[mid]["away_id"]}),
            -stamp(finished[mid]["kickoff"]).timestamp(), mid,
        ))
        for mid in by_priority:
            if mid not in saved and (mid not in block["retry"] or stamp(block["retry"][mid]) <= model_stamp):
                missing_by_league[key].append((key, mid, finished[mid]))

    # A cold league gets the same slice as its neighbors on each scan. Missing
    # or malformed rows back off so they do not starve the remaining calendar.
    pending = []
    while missing_by_league and len(pending) < HISTORY_REQUEST_LIMIT:
        for key in list(missing_by_league):
            pending.append(missing_by_league[key].popleft())
            if not missing_by_league[key]:
                del missing_by_league[key]
            if len(pending) == HISTORY_REQUEST_LIMIT:
                break

    def refresh(item):
        key, mid, match = item
        if time.monotonic() >= deadline:
            return key, mid, None, False
        return key, mid, _historical_result(COMPETITIONS[key[0]], mid, match, deadline), True

    with ThreadPoolExecutor(max_workers=7) as pool:
        for key, mid, score, tried in pool.map(refresh, pending):
            block = model_inputs[key]
            if score is not None:
                block["results"][mid] = score
                block["retry"].pop(mid, None)
            elif tried:
                block["retry"][mid] = utc_iso(clock() + HISTORY_RETRY)

    observed = clock()
    if touched:
        from football_totals_calibration import league_tail_inputs

    for (code, season), group in touched.items():
        competition = COMPETITIONS[code]
        current = model_inputs[(code, season)]
        prior = model_inputs[(code, season - 1)]
        calendar = record(current.get("calendar"))
        current_at = stamp(current.get("calendar_at"))
        if current_at is None or not timedelta(0) <= observed - current_at <= MODEL_AGE:
            for fixture in group:
                fixture["reason"] = "赛程源暂不可用，仅观察"
            continue
        try:
            league_totals = league_tail_inputs(competition, current, prior, observed)
        except (KeyError, TypeError, ValueError, OverflowError):
            league_totals = None
        for fixture in group:
            matching = _match_fixture(fixture, calendar)
            if not matching:
                fixture["reason"] = "独立赛程未匹配，仅观察"
                continue
            match_id, match = matching
            fixture.update(fixture_id=match_id, fotmob_home_id=match["home_id"], fotmob_away_id=match["away_id"],
                           fotmob_league_id=str(competition.fotmob_id))
            if match["state"] != "scheduled":
                fixture.update(state=match["state"] if match["state"] in {"cancelled", "postponed"} else "started",
                               watch=False, reason="独立赛程状态有变")
                continue
            if current.get("calendar"):
                try:
                    model = _fixture_model(fixture, match, (season, competition), current, prior, observed)
                except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
                    model = None
                if model is not None:
                    if league_totals is not None:
                        model["league_totals"] = league_totals | {"observed_at": utc_iso(observed)}
                    fixture.update(model=model, reason="有十变体模型，需核盘口和首发")
                else:
                    fixture["reason"] = "两队xG或上季基线不齐，仅观察"

    for (code, year), block in model_inputs.items():
        calendar = record(block.get("calendar"))
        sources[f"{code}:{year}"].update(result_rows=len(block.get("results", {})),
                                         completed=len(_finished(calendar, observed)),
                                         calendar_at=block.get("calendar_at"))
    # Preserve any still-relevant season blocks from leagues with no listings
    # in this scan so that interruptions and byes do not force a cold start.
    oldest = min(year for _, year in targets)
    preserved = {name: value for name, value in cached.items() if re.fullmatch(r"[a-z0-9]+:\d{4}", name)
                 and int(name.rsplit(":", 1)[1]) >= oldest and isinstance(value, dict)}
    preserved.update({f"{code}:{year}": block for (code, year), block in model_inputs.items() if block.get("calendar")})
    refreshed_cache = {"version": 1, "seasons": preserved}
    if touched:
        from football_lineup_history import refresh as refresh_starting_xis

        refresh_starting_xis(fixtures, refreshed_cache, cache.get("lineup_history"), clock, deadline)
    return sources, refreshed_cache
