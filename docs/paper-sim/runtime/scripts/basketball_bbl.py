"""Read independent German BBL match clocks and scores from the league's public pages."""

from __future__ import annotations

import json
import re
import time
from hashlib import sha256
from urllib.request import ProxyHandler, Request, build_opener

import football_feed as feed


ORIGIN = "https://www.easycredit-bbl.de"
PROVIDER = "easycredit-bbl"
_PAGE_LIMIT = 3 * 1024**2
_NEXT = re.compile(rb'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>', re.S)
# Stable league team IDs to the historical scoring model's exact club labels.
_HISTORY_NAMES = {
    "413": "Alba Berlin", "486": "Bayern", "420": "Bamberg", "415": "Bonn", "540": "Wurzburg",
    "418": "Ulm", "541": "Vechta", "414": "Trier", "551": "Rostock", "433": "Ludwigsburg",
    "430": "Oldenburg", "485": "Chemnitz", "428": "Syntainics MBC", "554": "Hamburg",
    "426": "Frankfurt", "483": "Jena", "473": "Phoenix Hagen", "422": "Basketball Braunschweig",
}
_DISPLAY_ALIASES = {
    "486": ("FC Bayern Munich", "Bayern Munich"), "554": ("Hamburg Towers",),
    "415": ("Baskets Bonn", "Telekom Baskets"), "418": ("Ratiopharm Ulm",),
    "426": ("Frankfurt Skyliners", "Skyliners Frankfurt"), "422": ("Braunschweig",),
    "540": ("Wuerzburg Baskets", "Wurzburg Baskets", "Würzburg Baskets"),
}


def _page(url, clock, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, None, None
    request = Request(url, method="GET", headers={"Accept": "text/html", "Accept-Encoding": "identity",
                      "Cache-Control": "no-cache", "User-Agent": "Mozilla/5.0"})
    opener = build_opener(ProxyHandler({}), feed.pm._NoRedirect())
    try:
        with opener.open(request, timeout=min(7, remaining)) as response:
            age = response.headers.get("Age", "0")
            if (response.status != 200 or response.geturl() != url or response.headers.get_content_type() != "text/html"
                    or not age.isdigit() or int(age) > 600):
                return None, None, None
            raw = response.read(_PAGE_LIMIT + 1)
        at = clock()
        if len(raw) > _PAGE_LIMIT or time.monotonic() > deadline:
            return None, None, None
        matches = _NEXT.findall(raw)
        if len(matches) != 1:
            return None, None, None
        props = feed.record(feed.record(json.loads(matches[0])).get("props"))
        return feed.record(props.get("pageProps")), at, sha256(matches[0]).hexdigest()
    except (OSError, ValueError, TypeError):
        return None, None, None


def _navigation(page):
    return {team["id"]: team for team in feed.rows(page.get("teamNavigatorData")) if isinstance(team.get("id"), str)}


def _full_time(raw):
    result = feed.record(raw.get("result"))
    period = result.get("latestPeriod")
    if type(period) is int and 4 <= period <= 13:
        period = "Q4" if period == 4 else f"OT{period - 4}"
    if not isinstance(period, str) or not re.fullmatch(r"Q4|OT[1-9]", period):
        return None
    periods = [f"Q{i}" for i in range(1, 5)]
    if period.startswith("OT"):
        periods.extend(f"OT{i}" for i in range(1, int(period[-1]) + 1))
    scores = []
    for side in ("homeTeam", "guestTeam"):
        total = result.get(f"{side}FinalScore")
        individual = [result.get(f"{side}{q}Score") for q in periods]
        if (type(total) is not int or total <= 0 or any(type(points) is not int or points < 0 for points in individual)
                or sum(individual) != total):
            return None
        scores.append(total)
    return (*scores, period.startswith("OT")) if scores[0] != scores[1] else None


def _game(raw, navigation):
    game_id = feed.numeric_id(raw.get("id"))
    tip, season = feed.stamp(raw.get("scheduledTime")), raw.get("seasonId")
    if (game_id is None or tip is None or type(season) is not int or raw.get("competition") != "BBL"
            or raw.get("isTBD") is not False or raw.get("isHidden") is not False):
        return None
    states = {"PRE": "scheduled", "LIVE": "started", "OFFICIAL": "final",
              "CANCELLED": "cancelled", "POSTPONED": "postponed"}
    state = states.get(raw.get("status"), "unconfirmed")
    final = _full_time(raw) if state == "final" and raw.get("gameProgress") == "E" else None
    if state == "final" and final is None:
        return None
    points_home, points_away, overtime = final or (None, None, None)
    row = {"id": game_id, "provider": PROVIDER, "kickoff": feed.utc_iso(tip), "state": state,
           "season_year": season, "home_score": points_home, "away_score": points_away,
           "neutral": raw.get("isNeutralVenue") is True, "overtime": overtime}
    for side, key in (("home", "homeTeam"), ("away", "guestTeam")):
        team = feed.record(raw.get(key))
        team_id = feed.numeric_id(team.get("teamId"))
        if team.get("isForeign") is not False or team_id is None or not team.get("tlc") or not team.get("name"):
            return None
        official = navigation.get(team_id, {})
        code = _HISTORY_NAMES.get(team_id, team["name"])
        aliases = (team["name"], code, team["tlc"], official.get("nameFull"), official.get("nameShort"),
                   *_DISPLAY_ALIASES.get(team_id, ()))
        row[f"{side}_id"] = team_id
        row[f"{side}_code"] = code
        row[f"{side}_names"] = sorted({name for name in aliases if isinstance(name, str) and name})
    return row if row["home_id"] != row["away_id"] else None


def bbl_board(clock, deadline):
    """Read the league's date-bounded Game Center, including its recent official finals."""
    url = ORIGIN + "/"
    page, at, digest = _page(url, clock, deadline)
    if not page:
        return None
    widgets = [value for key, value in feed.record(page.get("preloadedWidgetData")).items()
               if key.startswith("acf/bbl-game-center-")]
    if len(widgets) != 1 or not all(isinstance(feed.record(widgets[0]).get(key), list)
                                    for key in ("past", "present", "future")):
        return None
    navigation = _navigation(page)
    fixtures, rejected = {}, 0
    for key in ("past", "present", "future"):
        for raw in feed.rows(widgets[0][key]):
            if raw.get("competition") != "BBL":
                continue
            game = _game(raw, navigation)
            if game is None:
                rejected += 1
                continue
            if game["id"] in fixtures and game != fixtures[game["id"]]:
                return None
            fixtures[game["id"]] = game
    if not fixtures:
        return None
    return {"observed_at": feed.utc_iso(at), "url": url, "source_sha256": digest,
            "fixtures": list(fixtures.values()), "rejected": rejected,
            "coverage": "official_game_center_recent"}


def bbl_summary(game_id, clock, deadline):
    """Recheck a single frozen game on its canonical official game page."""
    key = feed.numeric_id(game_id)
    if key is None:
        return None, None
    url = f"{ORIGIN}/spiele/{key}"
    page, at, digest = _page(url, clock, deadline)
    if not page:
        return None, None
    raw = feed.record(page.get("initialGameData"))
    game = _game(raw, _navigation(page))
    if game is None or game["id"] != key:
        return None, None
    if game["state"] == "final":
        stats = feed.record(page.get("initialGameStats"))
        if (str(stats.get("gameId")) != key or stats.get("status") != "OFFICIAL"
                or feed.stamp(stats.get("scheduledTime")) != feed.stamp(game["kickoff"])):
            return None, None
        for side, team in (("home", "homeTeam"), ("away", "guestTeam")):
            box = feed.record(feed.record(stats.get(team)).get("gameStat"))
            if (box.get("points") != game[f"{side}_score"]
                    or str(feed.record(box.get("seasonTeam")).get("teamId")) != game[f"{side}_id"]):
                return None, None
    observed = {"source_url": url, "observed_at": feed.utc_iso(at), "source_sha256": digest}
    proof = dict(observed)
    if game["state"] == "scheduled" and isinstance(page.get("initialGameStats"), dict):
        proof["pre_match_stats"] = page["initialGameStats"]
    return game | observed, proof
