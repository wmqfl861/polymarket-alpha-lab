"""Versioned NCAA moneyline research, with separate men's and women's ratings."""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from hashlib import sha256
from itertools import groupby
from pathlib import Path
from statistics import fmean
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import football_feed as feed


VERSION = "ncaa-elo-margin-v1-20261123"
KEY = "basketball/college/" + VERSION
ET = ZoneInfo("America/New_York")
LEAGUES = {"cbb": ("mens", "mbb", 2), "cwbb": ("womens", "wbb", 4)}
SEASON = 2027
# Fixed before the scoring run. Season-ending years follow ESPN.
SOURCES = {
    "cbb": {2025: "fd5e653f419033aafe7229bc6dbf5a5592e9b9ab99f62606555bb6436d6d3fff",
            2026: "4d3a8671fe8cfd2c4d4c2faaff4d70460afc4b55880e89d3d9bb8a4797639ac6",
            2027: "a3af2c4f3f4526f3c458bc297efef58d52d0e34a286ced451e9fdaa9b2a73047"},
    "cwbb": {2025: "3f216a766bbd41fb473e0a3df7e090a3d5d030129d31c9faa43dd094993d9a3a",
             2026: "02a3252b4324a4bd7ad56d8674509492a840b823f6c677372df8efdb94922ac1",
             2027: "07c11bb2619bb8cba9472725beb13c1fda7febb0cc0a9e3fe6126cd713280e25"},
}
# Both 2027 releases were published on Nov 22. Leave the entire preceding
# ET calendar day out of the seed to allow for late finals and schedule changes.
SEED_THROUGH = date(2026, 11, 20)
FROZEN_SCORES = os.environ.get("PAL_COLLEGE_SCORES_FILE")
FROZEN_SHA256 = "7b2288ef1f35c3558a13fe1c157acc05985ba82351142f390bb865d4131f1676"
RATING_HOME, RATING_K, SEASON_CARRY = 55.0, 28.0, .65
MIN_GAMES = 5


def season_day(kickoff):
    return kickoff.astimezone(ET).date()


def source_url(league, year):
    name, prefix, _ = LEAGUES[league]
    root = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
    return f"{root}/espn_{name}_college_basketball_schedules/{prefix}_schedule_{year}.csv"


def _whole(value):
    n = feed.number(value)
    return int(n) if n is not None and n.is_integer() and n >= 0 else None


@lru_cache(maxsize=1)
def _frozen_scores():
    if not FROZEN_SCORES:
        return {}
    try:
        raw = Path(FROZEN_SCORES).expanduser().read_bytes()
    except FileNotFoundError:
        return {}
    if sha256(raw).hexdigest() != FROZEN_SHA256:
        raise ValueError("college_frozen_score_digest_mismatch")
    return json.loads(gzip.decompress(raw))["assets"]


def archive_games(league, year):
    # The public current-season release is overwritten daily. This compact
    # snapshot contains the exact final scores used when the model registered.
    frozen = _frozen_scores().get(f"{league}:{year}")
    if frozen and frozen["source_sha256"] == SOURCES[league][year]:
        return frozen["games"]
    url = source_url(league, year)
    with urlopen(Request(url, headers={"User-Agent": "college-basketball-research"}), timeout=35) as response:
        raw = response.read(48 * 1024**2)
    if sha256(raw).hexdigest() != SOURCES[league][year]:
        raise ValueError(f"college_source_digest_mismatch:{league}:{year}")
    result = {}
    for row in csv.DictReader(io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8-sig")):
        if (row["status_type_name"] != "STATUS_FINAL" or row["status_type_state"] != "post"
                or row["status_type_completed"].lower() != "true" or row["season"] != str(year)
                or row["season_type"] not in {"2", "3"} or row["neutral_site"].lower() not in {"true", "false"}
                or row["id"] != row["game_id"] or not feed.numeric_id(row["id"])):
            continue
        at = feed.stamp(row["date"])
        a, b = (_whole(row[f"{side}_score"]) for side in ("home", "away"))
        teams = [feed.numeric_id(row[f"{side}_id"]) for side in ("home", "away")]
        period = _whole(row["status_period"])
        if (at is None or feed.stamp(row["start_date"]) != at or not all(teams) or teams[0] == teams[1]
                or period is None or period < LEAGUES[league][2] or not a or not b or a == b):
            continue
        game = [feed.utc_iso(at), *teams, row["neutral_site"].lower() == "true", a, b]
        existing = result.setdefault(row["id"], game)
        if game != existing:
            raise ValueError("college_archive_conflicting_identity")
    if len(result) < (100 if year == SEASON else 4000):
        raise ValueError("college_archive_incomplete")
    return result


def probability(ratings, home, away, neutral):
    home_rating = ratings.get(home, (0.0, 0))[0]
    away_rating = ratings.get(away, (0.0, 0))[0]
    diff = home_rating - away_rating + (0 if neutral else RATING_HOME)
    return 1 / (1 + 10 ** (-diff / 400))


def update_day(ratings, games):
    # Every game on the slate uses the same start-of-day ratings.
    deltas, counts = defaultdict(float), defaultdict(int)
    for _, home, away, neutral, a, b in games:
        p = probability(ratings, home, away, neutral)
        magnitude = min(2, max(1, math.log1p(abs(a - b)) / 2.3))
        change = RATING_K * magnitude * ((a > b) - p)
        deltas[home] += change
        deltas[away] -= change
        counts[home] += 1
        counts[away] += 1
    for team, change in deltas.items():
        old, n = ratings.get(team, (0.0, 0))
        ratings[team] = [old + change, n + counts[team]]


def offseason(ratings):
    return {team: [score * SEASON_CARRY, games] for team, (score, games) in ratings.items()}


def days(games):
    ordered = sorted(games.items(), key=lambda item: (item[1][0], item[0]))
    for day, slate in groupby(ordered, key=lambda item: season_day(feed.stamp(item[1][0]))):
        yield day, [game for _, game in slate]


def eligible(ratings, home, away):
    return all(ratings.get(team, (0, 0))[1] >= MIN_GAMES for team in (home, away))


def _metrics(rows):
    n = len(rows)
    if not n:
        return {"games": 0}
    return {"games": n, "brier": fmean((p - y) ** 2 for p, y in rows),
            "log_loss": fmean(-math.log(p if y else 1 - p) for p, y in rows),
            "accuracy": fmean((p > .5) == y if p != .5 else .5 for p, y in rows)}


def replay(seed, games, baseline):
    ratings, pending, outcomes, naive = dict(seed), [], [], []
    for day, slate in days(games):
        while pending and pending[0][0] <= day - timedelta(days=2):
            update_day(ratings, pending.pop(0)[1])
        for _, home, away, neutral, a, b in slate:
            if eligible(ratings, home, away):
                outcomes.append((probability(ratings, home, away, neutral), a > b))
                naive.append((.5 if neutral else baseline, a > b))
        pending.append((day, slate))
    for _, slate in pending:
        update_day(ratings, slate)
    return ratings, {"model": _metrics(outcomes), "venue_only": _metrics(naive), "all_verified": len(games)}


def build(league):
    warm = archive_games(league, 2025)
    baseline = fmean(a > b for _, _, _, neutral, a, b in warm.values() if not neutral)
    ratings, _ = replay({}, warm, baseline)
    ratings, held = replay(offseason(ratings), archive_games(league, 2026), baseline)
    current = {key: row for key, row in archive_games(league, SEASON).items()
               if season_day(feed.stamp(row[0])) <= SEED_THROUGH}
    ratings, recent = replay(offseason(ratings), current, baseline)
    return {"ratings": ratings, "through": str(SEED_THROUGH),
            "validation": {"2025-26": held, "2026-27_through_seed": recent, "home_rate_warm_season": baseline},
            "sources": {str(y): {"url": source_url(league, y), "sha256": digest}
                        for y, digest in SOURCES[league].items()}}


def register(connection):
    from football_learning import cached
    from football_store import db_now
    from psycopg.types.json import Jsonb

    saved = cached(connection, KEY)
    if not saved:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            seed = dict(zip(LEAGUES, pool.map(build, LEAGUES)))
        doc = {"version": VERSION, "registered_at": feed.utc_iso(db_now(connection)), "leagues": seed,
               "protocol": {"season": SEASON, "home_advantage": RATING_HOME, "k": RATING_K,
                            "season_carry": SEASON_CARRY, "min_prior_games": MIN_GAMES, "lag_et_days": 2,
                            "market": "full-game moneyline including OT", "research_only": True}}
        connection.execute("""INSERT INTO source_cache(cache_key,payload) VALUES(%s,%s)
                              ON CONFLICT DO NOTHING""", (KEY, Jsonb(doc)))
        saved = cached(connection, KEY)
    return {"version": saved["version"], "registered_at": saved["registered_at"],
            "validation": {lg: v["validation"] for lg, v in saved["leagues"].items()}}


if __name__ == "__main__":
    from football_store import connect
    with connect() as conn:
        print(json.dumps(register(conn)))
