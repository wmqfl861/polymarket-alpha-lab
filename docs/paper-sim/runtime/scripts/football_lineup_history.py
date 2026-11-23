"""A pregame domestic-league starting-XI reference for the team xG model."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
import re
import time

from polymarket_alpha_lab.papersim import fotmob

from football_feed import (BOOK_AGE, CONFIRMED_LINEUPS, clean_label, document_digest, numeric_id,
                           public_budget, record, stamp, utc_iso)
from football_model import details_url


POLICY = "fotmob-last-five-league-starting-xis-v1"
HISTORY_GAMES = 5
COMPLETION_LAG = timedelta(hours=3)
REQUESTS_PER_SCAN = 20
COLLECTION_SECONDS = 12
RETRY_DELAY = timedelta(minutes=30)
SOURCE_CACHE_LIMIT = 2_000_000  # football_store.save_source_cache caps the entire feed/1 document.
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SIDES = ("home", "away")


def _ids(values, length):
    return (isinstance(values, list) and len(values) == length
            and all(isinstance(player, str) and numeric_id(player) == player and int(player) > 0 for player in values)
            and len(set(values)) == length)


def _signed(doc):
    return doc | {"sha256": document_digest(doc)}


def _has_signature(doc):
    return (isinstance(doc, dict) and bool(doc.get("sha256"))
            and doc["sha256"] == document_digest({k: v for k, v in doc.items() if k != "sha256"}))


def _target(fixture):
    return {"fixture_id": fixture.get("fixture_id"), "fixture_key": fixture.get("id"),
            "league_id": fixture.get("fotmob_league_id"), "competition": fixture.get("league"),
            "team_ids": [fixture.get("fotmob_home_id"), fixture.get("fotmob_away_id")],
            "kickoff": fixture.get("kickoff")}


def _match(match_id, row, league_id):
    return {"fixture_id": match_id, "league_id": league_id, "kickoff": row.get("kickoff"),
            "team_ids": [row.get("home_id"), row.get("away_id")]}


def _roster(team):
    players = team.get("starters")
    if not isinstance(players, list) or len(players) != 11 or not all(isinstance(p, dict) for p in players):
        return None
    starters = [numeric_id(player.get("id")) for player in players]
    keepers = [numeric_id(player.get("id")) for player in players if numeric_id(player.get("positionId")) == "11"]
    if not _ids(starters, 11) or len(keepers) != 1:
        return None
    return {"starters": sorted(starters), "goalkeeper": keepers[0]}


def _fetch(spec, clock, deadline):
    """Return only verified prior starting rosters, with their actual retrieval timestamp."""
    from football_model import _prematch_identity

    if time.monotonic() >= deadline:
        return None
    raw_hash = None

    def on_bytes(_, data):
        nonlocal raw_hash
        raw_hash = hashlib.sha256(data).hexdigest()

    try:
        payload = fotmob.fetch_match_details(spec["fixture_id"], allow_public_fetch=True, timeout=4,
                                             budget=public_budget(deadline, requests=2), response_sink=on_bytes)
        at = clock()
        general, status, identity = _prematch_identity(payload)
        teams = spec["team_ids"]
        kickoff = stamp(spec["kickoff"])
        if (identity != (spec["fixture_id"], spec["league_id"], *teams)
                or kickoff is None or at - kickoff < COMPLETION_LAG
                or stamp(general.get("matchTimeUTCDate")) != kickoff or stamp(status.get("utcTime")) != kickoff
                or status.get("finished") is not True or status.get("awarded") is True
                or status.get("postponed") is True or fotmob._is_cancelled({"status": status})):
            return None
        lineup = record(record(payload.get("content")).get("lineup"))
        provider = clean_label(lineup.get("source"))
        ordered = [record(lineup.get(side + "Team")) for side in _SIDES]
        records = [_roster(team) for team in ordered]
        if (not raw_hash or not provider or lineup.get("lineupType") not in CONFIRMED_LINEUPS
                or numeric_id(lineup.get("matchId")) != spec["fixture_id"]
                or [numeric_id(team.get("id")) for team in ordered] != teams or not all(records)
                or set(records[0]["starters"]) & set(records[1]["starters"])):
            return None
        return _signed(spec | {"lineups": records, "source": details_url(spec["fixture_id"]), "provider": provider,
                               "observed_at": utc_iso(at), "raw_sha256": raw_hash})
    except Exception:
        # An unavailable or changing public feed leaves the normal model and its direct lineup path usable.
        return None


def _valid_match(row, spec, as_of):
    r = record(row)
    observed, kickoff = stamp(r.get("observed_at")), stamp(r.get("kickoff"))
    sides = r.get("lineups")
    return (all(r.get(k) == v for k, v in spec.items()) and _has_signature(r)
            and r.get("source") == details_url(spec["fixture_id"]) and clean_label(r.get("provider"))
            and _DIGEST.fullmatch(str(r.get("raw_sha256") or "")) is not None
            and observed is not None and kickoff is not None and kickoff + COMPLETION_LAG <= observed <= as_of
            and isinstance(sides, list) and len(sides) == 2
            and all(isinstance(s, dict) and _ids(s.get("starters"), 11) and s.get("goalkeeper") in s["starters"] for s in sides)
            and not (set(sides[0]["starters"]) & set(sides[1]["starters"])))


def _last_five(fixture, calendar, as_of):
    target = _target(fixture)
    mid, teams, kickoff = target["fixture_id"], target["team_ids"], stamp(target["kickoff"])
    official = record(calendar.get(mid))
    if (not numeric_id(mid) or not numeric_id(target["league_id"]) or not _ids(teams, 2)
            or not kickoff or as_of >= kickoff or official.get("state") != "scheduled"
            or stamp(official.get("kickoff")) != kickoff or [official.get("home_id"), official.get("away_id")] != teams):
        return None
    output = []
    for team_id in teams:
        previous = []
        for prior_id, value in calendar.items():
            row = record(value)
            if (row.get("state") != "finished" or team_id not in (row.get("home_id"), row.get("away_id"))
                    or not numeric_id(prior_id) or mid == prior_id):
                continue
            at = stamp(row.get("kickoff"))
            if at and at <= as_of - COMPLETION_LAG:
                previous.append((at, prior_id, row))
        selected = sorted(previous, key=lambda item: (item[0], item[1]))[-HISTORY_GAMES:]
        if len(selected) != HISTORY_GAMES or len({item[0] for item in selected}) != HISTORY_GAMES:
            return None
        output.append([_match(i, row, target["league_id"]) for _, i, row in selected])
    return output


def _size(doc):
    return len(json.dumps(doc, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def refresh(fixtures, source_cache, old, clock, model_deadline):
    """Prepare nearby fixtures after the ordinary xG model, within the same scan deadline."""
    from football_model import COMPETITIONS, MODEL_AGE, _season

    now = clock()
    groups, needed = [], {}
    cache = record(old)
    prior = record(cache.get("matches")) if cache.get("policy") == POLICY else {}
    retry = record(cache.get("retry")) if cache.get("policy") == POLICY else {}
    seasons = record(source_cache.get("seasons"))
    for fixture in sorted(fixtures, key=lambda f: str(f.get("kickoff") or "")):
        code = fixture.get("league")
        kickoff = stamp(fixture.get("kickoff"))
        if code not in COMPETITIONS or fixture.get("state") != "scheduled" or not kickoff or not fixture.get("model"):
            continue
        comp = COMPETITIONS[code]
        block = record(seasons.get(f"{code}:{_season(kickoff)}"))
        cal_at, cal_hash = stamp(block.get("calendar_at")), block.get("calendar_sha256")
        if (cal_at is None or not timedelta(0) <= now - cal_at <= MODEL_AGE
                or not _DIGEST.fullmatch(str(cal_hash or "")) or fixture.get("fotmob_league_id") != str(comp.fotmob_id)):
            continue
        reference = _last_five(fixture, record(block.get("calendar")), cal_at)
        if reference:
            groups.append((fixture, reference, block["calendar_at"], cal_hash))
            for side in reference:
                for match in side:
                    needed.setdefault(match["fixture_id"], match)

    valid, delays, pending = {}, {}, []
    for mid, spec in needed.items():
        if _valid_match(prior.get(mid), spec, now):
            valid[mid] = prior[mid]
        elif (retry_at := stamp(retry.get(mid))) and now < retry_at <= now + RETRY_DELAY:
            delays[mid] = retry[mid]
        elif len(pending) < REQUESTS_PER_SCAN:
            pending.append(spec)
    deadline = min(model_deadline, time.monotonic() + COLLECTION_SECONDS)
    if pending and time.monotonic() < deadline:
        def collect(spec):
            if time.monotonic() >= deadline:
                return spec, None, False
            return spec, _fetch(spec, clock, deadline), True

        with ThreadPoolExecutor(max_workers=5) as pool:
            for spec, response, attempted in pool.map(collect, pending):
                mid = spec["fixture_id"]
                if response:
                    valid[mid] = response
                elif attempted:
                    delays[mid] = utc_iso(clock() + RETRY_DELAY)
    at = clock()
    for fixture, histories, calendar_at, calendar_hash in groups:
        ids = [[item["fixture_id"] for item in side] for side in histories]
        required = dict.fromkeys(mid for side in ids for mid in side)
        if all(mid in valid for mid in required) and at < stamp(fixture["kickoff"]):
            fixture["lineup_history"] = _signed({"policy": POLICY, "target": _target(fixture),
                "compiled_at": utc_iso(at), "calendar_observed_at": calendar_at, "calendar_sha256": calendar_hash,
                "chronological_ids": ids, "matches": {mid: valid[mid] for mid in required}})

    # The optional reference must never make an otherwise usable complete season cache unsavable.
    if valid or delays:
        saved = {"policy": POLICY, "matches": {}, "retry": delays}
        capacity = SOURCE_CACHE_LIMIT - _size(source_cache) - _size(saved) - 64
        for mid in needed:
            if mid not in valid:
                continue
            cost = _size({mid: valid[mid]}) + 2
            if cost <= capacity:
                saved["matches"][mid] = valid[mid]
                capacity -= cost
        if capacity >= 0:
            source_cache["lineup_history"] = saved


def _bound(context, fixture):
    data = record(context)
    return (data.get("fixture_id") == fixture.get("fixture_id") and bool(data.get("fixture_id"))
            and data.get("team_ids") == [fixture.get("fotmob_home_id"), fixture.get("fotmob_away_id")]
            and data.get("kickoff") == fixture.get("kickoff")
            and data.get("source") == details_url(fixture["fixture_id"]) and _has_signature(data)
            and isinstance(data.get("lineups"), list) and len(data["lineups"]) == 2
            and all(isinstance(team, dict) for team in data["lineups"]))


def _complete(team):
    starters = team.get("starters")
    formation = str(team.get("formation") or "").split("-")
    return (_ids(starters, 11) and team.get("goalkeeper") in starters
            and all(part.isascii() and part.isdigit() and 0 < len(part) < 3 for part in formation)
            and sum(map(int, formation)) == 10)


def _rotation(current, before, history):
    previous = [set(s["starters"]) for s in history]
    differences = [len(b-a) for a,b in zip(previous,previous[1:])]
    current_turnover = len(set(current["starters"])-previous[-1])
    known_keepers = {s["goalkeeper"] for s in history}
    prior_starts = Counter(p for s in previous for p in s)
    new_absences = sorted(current["absences"].keys() - before["absences"].keys())
    newly_missing = [p for p in new_absences if prior_starts[p] > HISTORY_GAMES // 2]
    issues = []
    if current["goalkeeper"] not in known_keepers:
        issues.append("unfamiliar_starting_goalkeeper")
    if current_turnover > max(differences):
        issues.append("abnormal_starting_xi_turnover")
    if newly_missing:
        issues.append("newly_unavailable_regular")
    return {"reasons": issues, "recent_turnovers": differences, "current_turnover": current_turnover,
            "starting_goalkeeper": current["goalkeeper"], "recent_goalkeepers": sorted(known_keepers),
            "new_absences": {p: prior_starts[p] for p in new_absences}, "newly_unavailable_regulars": newly_missing}


def evaluate(fixture, context, before, as_of):
    """Return (passes, reason, evidence). This route requires a fresh confirmed XI and genuine prior capture."""
    ctx, baseline, reference = record(context), record(before), record(fixture.get("lineup_history"))
    trace = {"policy": POLICY, "as_of": utc_iso(as_of), "target": _target(fixture), "teams": {},
             "current_sha256": ctx.get("sha256"), "baseline_sha256": baseline.get("sha256"),
             "reference_sha256": reference.get("sha256"), "reference_compiled_at": reference.get("compiled_at")}
    at, older, kickoff = stamp(ctx.get("observed_at")), stamp(baseline.get("observed_at")), stamp(fixture.get("kickoff"))
    if (not _bound(ctx, fixture) or not _bound(baseline, fixture) or not at or not older or not kickoff
            or not older < at <= as_of < kickoff or as_of - at > BOOK_AGE or at - older > timedelta(hours=7)):
        return False, "history_lineup_identity_or_clock_invalid", trace
    if (ctx.get("lineup_type") not in CONFIRMED_LINEUPS or not clean_label(ctx.get("lineup_provider"))
            or not all(_complete(side) for side in ctx["lineups"])
            or set(ctx["lineups"][0]["starters"]) & set(ctx["lineups"][1]["starters"])):
        return False, "history_current_xi_unconfirmed", trace
    if not all(team.get("absences_complete") is True and isinstance(team.get("absences"), dict)
               for observed in (ctx, baseline) for team in observed["lineups"]):
        return False, "history_availability_unknown", trace
    if any(set(team["starters"]) & team["absences"].keys() for team in ctx["lineups"]):
        return False, "history_starter_unavailable", trace
    if baseline.get("lineup_type") in CONFIRMED_LINEUPS:
        for a, b in zip(baseline["lineups"], ctx["lineups"]):
            if not _ids(a.get("starters"), 11):
                if a.get("starters_unpublished") is not True:
                    return False, "history_previous_confirmed_xi_incomplete", trace
                continue
            if (set(a["starters"]) != set(b["starters"]) or (a.get("formation") and a["formation"] != b["formation"])
                    or a["absences"] != b["absences"]
                    or (a.get("goalkeeper") is not None and a["goalkeeper"] != b["goalkeeper"])):
                return False, "history_confirmed_lineup_changed", trace

    compiled, cal_at = stamp(reference.get("compiled_at")), stamp(reference.get("calendar_observed_at"))
    sides, matches = reference.get("chronological_ids"), record(reference.get("matches"))
    if (not _has_signature(reference) or reference.get("policy") != POLICY or reference.get("target") != _target(fixture)
            or compiled is None or cal_at is None or not cal_at <= compiled <= as_of
            or not _DIGEST.fullmatch(str(reference.get("calendar_sha256") or ""))
            or not isinstance(sides, list) or len(sides) != 2
            or not all(_ids(group, HISTORY_GAMES) for group in sides)):
        return False, "history_reference_unavailable", trace
    for i, side in enumerate(_SIDES):
        team_id = ctx["team_ids"][i]
        records, dates = [], []
        for mid in sides[i]:
            r = record(matches.get(mid))
            teams = r.get("team_ids")
            spec = {"fixture_id": mid, "league_id": fixture.get("fotmob_league_id"),
                    "kickoff": r.get("kickoff"), "team_ids": teams}
            if (mid == ctx["fixture_id"] or not _ids(teams, 2) or team_id not in teams
                    or not _valid_match(r, spec, compiled) or stamp(r["kickoff"]) + COMPLETION_LAG > cal_at):
                return False, "history_prior_fixture_or_clock_invalid", trace
            dates.append(stamp(r["kickoff"]))
            records.append(r["lineups"][teams.index(team_id)])
        if dates != sorted(set(dates)):
            return False, "history_prior_order_ambiguous", trace
        trace["teams"][side] = _rotation(ctx["lineups"][i], baseline["lineups"][i], records) | {
            "fixture_ids": sides[i], "source_sha256": [matches[mid]["raw_sha256"] for mid in sides[i]],
            "observed_at": [matches[mid]["observed_at"] for mid in sides[i]]}
    issues = [why for side in _SIDES for why in trace["teams"][side]["reasons"]]
    return (False, "history_"+issues[0], trace) if issues else (True, "verified_lineup_history", trace)
