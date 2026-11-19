"""Retrieve completed regular-season records from Basketball Reference and ESPN."""

from __future__ import annotations

import argparse
import gzip
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from polymarket_alpha_lab.papersim import espn_nba

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
BR = "https://www.basketball-reference.com/teams"
ESPN_TO_BR = {
    "BKN": "BRK", "CHA": "CHO", "GS": "GSW", "NO": "NOP", "NY": "NYK",
    "PHX": "PHO", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS",
}
BR_TO_ESPN = {abbr: team for team, abbr in ESPN_TO_BR.items()}
ET = ZoneInfo("America/New_York")
USER_AGENT = espn_nba.USER_AGENT


def fetch(url: str, path: Path, pause: float) -> bytes:
    if path.is_file():
        return path.read_bytes()
    for attempt in range(3):
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                body = response.read()
            if body[:2] == bytes.fromhex("1f8b"):
                body = gzip.decompress(body)
            path.write_bytes(body)
            return body
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise
            print(f"retry {url}: {exc}", flush=True)
            time.sleep(12 * (attempt + 1))
        finally:
            remaining = pause - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    raise RuntimeError(f"unreachable response from {url}")


class GameLog(HTMLParser):
    """Read the regular-season table only."""

    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.game = None
        self.field = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == "team_game_log_reg":
            self.in_table = True
        if not self.in_table:
            return
        if tag == "tr" and attrs.get("id", "").startswith("team_game_log_reg."):
            self.game = {}
        if self.game is not None and tag == "td":
            self.field = attrs.get("data-stat")
            if self.field is not None:
                self.game[self.field] = ""

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        if tag == "td":
            self.field = None
        if tag == "tr" and self.game is not None:
            self.rows.append({key: value.strip() for key, value in self.game.items()})
            self.game = None

    def handle_data(self, data):
        if self.game is not None and self.field is not None:
            self.game[self.field] += data


def espn_events(year, cache):
    events = {}
    for team, (team_id, _) in espn_nba.NBA_TEAMS.items():
        url = f"{ESPN}/teams/{team_id}/schedule?{urlencode({'season': year, 'seasontype': 2})}"
        body = fetch(url, cache / f"schedule-{year}-{team}.json", 1.2)
        payload = json.loads(body)
        assert int(payload["team"]["id"]) == team_id
        assert payload["requestedSeason"]["year"] == year
        assert payload["requestedSeason"]["type"] == 2
        for event in payload["events"]:
            competition = event["competitions"][0]
            if not competition["status"]["type"]["completed"]:
                continue
            if any(note.get("headline") in ("NBA Cup Championship", "NBA Cup - Championship")
                   for note in competition.get("notes", [])):
                continue
            assert event["season"]["year"] == year and event["seasonType"]["id"] == "2"
            sides = {row["homeAway"]: row for row in competition["competitors"]}
            assert set(sides) == {"home", "away"}
            started = datetime.fromisoformat(event["date"])
            key = (started.astimezone(ET).date().isoformat(),
                   sides["home"]["team"]["abbreviation"], sides["away"]["team"]["abbreviation"])
            info = {
                "event_id": event["id"], "started": started.isoformat(),
                "home_pts": int(sides["home"]["score"]["value"]),
                "away_pts": int(sides["away"]["score"]["value"]),
                "neutral": bool(competition.get("neutralSite", False)),
            }
            assert key not in events or events[key] == info
            events[key] = info
    counts = Counter(team for _, home, away in events for team in (home, away))
    if len(events) != 1230 or set(counts.values()) != {82}:
        raise ValueError(f"incomplete ESPN regular season: {len(events)}, {counts}")
    return events


def br_games(year, cache):
    games = {}
    for team in espn_nba.NBA_TEAMS:
        abbr = ESPN_TO_BR.get(team, team)
        url = f"{BR}/{abbr}/{year}/gamelog/"
        body = fetch(url, cache / f"br-{abbr}-{year}.html", 6.0)
        parser = GameLog()
        parser.feed(body.decode())
        if len(parser.rows) != 82:
            raise ValueError(f"{team} {year}: expected 82 regular-season rows, got {len(parser.rows)}")
        for row in parser.rows:
            day = row["date"]
            opponent = BR_TO_ESPN.get(row["opp_name_abbr"], row["opp_name_abbr"])
            marker = row["game_location"]
            own_home = marker == ""
            if marker not in ("", "@", "N"):
                raise ValueError(f"unknown venue marker: {marker!r}")
            nums = {
                "fga": int(row["fga"]), "oreb": int(row["orb"]),
                "tov": int(row["tov"]), "fta": int(row["fta"]),
            }
            position = (day, team, opponent)
            if position in games:
                raise ValueError(f"duplicate Basketball Reference game {position}")
            games[position] = {
                "home": own_home if marker != "N" else None, "pts": int(row["team_game_score"]),
                "opp_pts": int(row["opp_team_game_score"]), "poss": espn_nba._possessions(nums),
                "poss_inputs": nums,
            }
    return games


def reconcile(events, logs):
    rows = []
    missed = []
    for key, info in sorted(events.items()):
        day, home, away = key
        own = logs.get((day, home, away))
        opp = logs.get((day, away, home))
        if own is None or opp is None:
            missed.append(key)
            continue
        if own["pts"] != info["home_pts"] or opp["pts"] != info["away_pts"]:
            raise ValueError(f"box score mismatch {key}")
        if own["opp_pts"] != opp["pts"] or opp["opp_pts"] != own["pts"]:
            raise ValueError(f"opponent box score mismatch {key}")
        if not info["neutral"] and (own["home"] is not True or opp["home"] is not False):
            raise ValueError(f"venue mismatch {key}")
        rows.append({
            "date": day, "home": home, "away": away, **info,
            "home_poss": own["poss"], "away_poss": opp["poss"],
            "home_poss_inputs": own["poss_inputs"], "away_poss_inputs": opp["poss_inputs"],
        })
    if missed:
        raise ValueError(f"{len(missed)} ESPN regular-season games lack Basketball Reference rows: {missed[:8]}")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True, help="ending year, e.g. 2026")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    events = espn_events(args.year, args.cache)
    print(f"ESPN {args.year}: {len(events)} games", flush=True)
    logs = br_games(args.year, args.cache)
    rows = reconcile(events, logs)
    data = {
        "season": f"{args.year - 1}-{args.year % 100:02d}",
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "sources": {
            "schedule": ESPN + "/teams/{ESPN team id}/schedule?season={year}&seasontype=2",
            "box_inputs": BR + "/{Basketball Reference abbreviation}/{year}/gamelog/",
        },
        "games": rows,
    }
    args.output.write_bytes(gzip.compress(json.dumps(data, sort_keys=True).encode(), mtime=0))
    print(f"{data['season']}: {len(rows)} games, {rows[0]['date']}..{rows[-1]['date']}", flush=True)


if __name__ == "__main__":
    main()
