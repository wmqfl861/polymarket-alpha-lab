"""Discover, review and settle Polymarket basketball fixtures on public data."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
import math
import re
import time
import unicodedata
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

from polymarket_alpha_lab.papersim import espn_nba, gates

import basketball_bbl as bbl
import basketball_models as models
import football_feed as common
from football_feed import clean_label, document_digest, number, numeric_id, record, rows, stamp, utc_iso


GAMMA = common.pm.GAMMA_BASE_URL
CLOB = common.pm.CLOB_BASE_URL
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball"
EUROLEAGUE = "https://api-live.euroleague.net/v2/competitions"
VTB = "https://api.vtb-league.com/v2/leagues/vtb/seasons"
BBL_CALENDAR = "https://api.basketball-bundesliga.de/calendar/ical/all-games"
BBL_HISTORY_SOURCE = "https://www.betexplorer.com/basketball/germany/bbl/results/"
BERLIN = ZoneInfo("Europe/Berlin")
POLICY_VERSION = "basketball-league-admission-20261119-v1"
NBA_REPORT_KEYS = {models.NBA_VERSION: "nba", models.NBA_RATINGS_VERSION: "nba_ratings"}
SPORT_TAG = "28"
EXTRA_TAGS = {"nba": "745", "wnba": "100254", "cbb": "101178", "nbasl": "105577", "euroleague": "102669"}
LEAGUES = {
    "nba": "NBA", "wnba": "WNBA", "cbb": "NCAA 男篮", "cwbb": "NCAA 女篮", "nbasl": "NBA 夏季联赛",
    "euroleague": "欧洲篮球联赛", "bkeurocup": "欧洲篮球杯", "bkcl": "篮球冠军联赛", "bkligend": "西班牙 ACB",
    "bkbbl": "德国 BBL", "bkseriea": "意大利篮甲", "bkfr1": "法国篮甲", "bkfr2": "法国篮乙", "bkvtb": "VTB 联赛",
}
RESEARCH_LEAGUES = frozenset({"nba", *models.EURO_LEAGUES})
INDEPENDENT_LEAGUES = frozenset({"nba", *models.EURO_CODES, "bkvtb", "bkbbl"})
MANAGED_EURO_TOTALS = frozenset({"bkbbl", "bkeurocup"})
GAME_BASE = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)+-\d{4}-\d{2}-\d{2})(?:-|$)")
CONDITION_ID = re.compile(r"0x[0-9a-fA-F]{64}")
EXPLICIT_OT = "full_game_including_overtime"
AMBIGUOUS_OT = "overtime_unverified"
_RESOLUTION_HOSTS = {
    "cbb": {"ncaa.com"}, "cwbb": {"ncaa.com"},
    "nba": {"nba.com"}, "wnba": {"wnba.com"}, "euroleague": {"euroleaguebasketball.net"},
    "bkeurocup": {"euroleaguebasketball.net"}, "bkvtb": {"vtb-league.com"},
    "bkbbl": {"easycredit-bbl.de"}, "bkligend": {"acb.com"}, "bkseriea": {"legabasket.it"},
    "bkfr1": {"lnb.fr"}, "bkfr2": {"lnb.fr"}, "bkcl": {"championsleague.basketball", "fiba.basketball"},
}
_NBA_PM_CODES = {"GS": "gsw", "NY": "nyk", "SA": "sas", "UTAH": "uta", "WSH": "was", "NO": "nop"}
_NBA_ALIASES = {"LAC": ("LA Clippers", "Los Angeles Clippers"), "POR": ("Portland Trail Blazers", "Blazers")}
_EURO_ALIASES = {
    "asvel": ("LDLC ASVEL Villeurbanne", "Lyon-Villeurbanne", "ASVEL", "Lyon Villeurbanne"),
    "baskonia": ("Baskonia", "Kosner Baskonia Vitoria-Gasteiz", "Baskonia Vitoria Gasteiz"),
    "bayern": ("Bayern Munchen", "Bayern Munich", "FC Bayern Munich"),
    "besiktas": ("Besiktas", "Besiktas Fuzul Istanbul", "Besiktas Fibabanka"),
    "bologna": ("Virtus Bologna", "Virtus Segafredo Bologna", "Virtus Olidata Bologna"),
    "crvena zvezda": ("Crvena Zvezda", "Crvena Zvezda Meridianbet Belgrade", "Crvena Zvezda Belgrade", "Red Star Belgrade"),
    "fenerbahce": ("Fenerbahce", "Fenerbahce Tarfin Istanbul", "Fenerbahce Beko Istanbul"),
    "hapoel tel aviv": ("Hapoel Tel Aviv", "Hapoel IBI Tel Aviv", "Hapoel Tel Aviv BC"),
    "maccabi tel aviv": ("Maccabi Tel Aviv", "Maccabi Playtika Tel Aviv", "Maccabi Rapyd Tel Aviv"),
    "milano": ("Olimpia Milano", "Armani Olimpia Milan", "EA7 Emporio Armani Milan", "Milano", "Milan"),
    "olympiacos": ("Olympiacos", "Olympiacos Piraeus"),
    "panathinaikos": ("Panathinaikos", "Panathinaikos AKTOR Athens", "Panathinaikos Athens"),
    "partizan": ("Partizan", "Partizan Belgrade", "Partizan Mozzart Bet Belgrade"),
    "valencia": ("Valencia", "Valencia Basket", "Valencia Basket Club"),
    "zalgiris": ("Zalgiris", "Zalgiris Kaunas", "BC Zalgiris Kaunas"),
    "dubai": ("Dubai", "Dubai Basketball", "BC Dubai"),
    "barcelona": ("Barcelona", "FC Barcelona", "Barca"),
    "anadolu efes": ("Anadolu Efes", "Anadolu Efes Istanbul", "Anadolu Efes SK"),
    "monaco": ("AS Monaco", "Monaco", "AS Monaco Basket"),
}
_SCHEDULE_AGE = timedelta(minutes=10)
_REPORT_AGE = timedelta(hours=2)
_MODEL_AGE = timedelta(hours=2)
_TIP_TOLERANCE = timedelta(minutes=15)
_MAX_REVIEW = 20
_FITNESS_RISKS = frozenset({"out", "doubtful", "questionable", "day-to-day", "day to day", "game time decision"})
_BBL_CALENDAR_NAMES = {
    "ALBA BERLIN": "Alba Berlin", "BMA365 Bamberg Baskets": "Bamberg", "Basketball Löwen Braunschweig": "Basketball Braunschweig",
    "EWE Baskets Oldenburg": "Oldenburg", "FC Bayern München Basketball": "Bayern", "Fitness First Würzburg Baskets": "Wurzburg",
    "MHP RIESEN Ludwigsburg": "Ludwigsburg", "NINERS Chemnitz": "Chemnitz", "Phoenix Hagen": "Phoenix Hagen",
    "RASTA Vechta": "Vechta", "ROSTOCK SEAWOLVES": "Rostock", "SKYLINERS": "Frankfurt", "SYNTAINICS MBC": "Syntainics MBC",
    "Science City Jena": "Jena", "Telekom Baskets Bonn": "Bonn", "VET-CONCEPT Gladiators Trier": "Trier",
    "Veolia Towers Hamburg": "Hamburg", "ratiopharm ulm": "Ulm",
}


class _ProviderOpener:
    def open(self, url, *, timeout_seconds):
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "Mozilla/5.0"}
        if url.startswith(EUROLEAGUE + "/"):
            headers["Referer"] = "https://www.euroleaguebasketball.net/"
        request = Request(url, headers=headers, method="GET")
        return build_opener(ProxyHandler({}), common.pm._NoRedirect()).open(request, timeout=timeout_seconds)


def _get(url, deadline, clock, limit=4 * 1024**2):
    budget = common.public_budget(deadline, limit=limit)
    return common._public(url, budget, clock, _ProviderOpener())


def _get_safe(url, deadline, clock, limit=4 * 1024**2):
    try:
        return _get(url, deadline, clock, limit)
    except Exception:
        return None, None


def _eq(label, value):
    return bool(_name(label) and _name(label) == _name(value))


def _name(value):
    value = unicodedata.normalize("NFKD", clean_label(value).casefold())
    no_marks = "".join(character for character in value if not unicodedata.combining(character))
    return " ".join(re.findall(r"[^\W_]+", no_marks, flags=re.UNICODE))


_EURO_NAMES = {_name(value): key for key, values in _EURO_ALIASES.items() for value in values}


def _aliases(values, league):
    plain = {_name(value) for value in values if _name(value)}
    if league in models.EURO_LEAGUES:
        return plain | {_EURO_NAMES.get(value, value) for value in plain}
    return plain


def _fixture_side_aliases(fixture, side):
    return _aliases([fixture.get(side), fixture.get(f"{side}_alias"), fixture.get(f"{side}_abbr")], fixture["league"])


def _side_matches(fixture, side, text):
    if not isinstance(text, str):
        return False
    if _fixture_side_aliases(fixture, side) & _aliases((text,), fixture["league"]):
        return True
    if fixture["league"] != "nba":
        return False
    aliases = _fixture_side_aliases(fixture, side)
    return any(_name(text) in _nba_names(team, display) and aliases & _nba_names(team, display)
               for team, (_, display) in espn_nba.NBA_TEAMS.items())


def _nba_names(code, name):
    short = _NBA_PM_CODES.get(code, code.lower())
    mascot = "Trail Blazers" if code == "POR" else name.split()[-1]
    return _aliases([code, name, short, mascot, *_NBA_ALIASES.get(code, ())], "nba")


def _league_registry(registry):
    return {code: item for code, item in registry.items()
            if SPORT_TAG in common._tags(item.get("tags")) or code in EXTRA_TAGS
            or code in LEAGUES or code.startswith("bk") or "basketball" in str(item.get("name", "")).lower()}


def _gamma_lane(tag, begin, end, clock, deadline):
    result, pages, cursor = {}, 0, None
    page_size, page_cap = 50, 60
    params = {"tag_id": tag, "active": "true", "closed": "false", "include_markets": "false",
              "order": "endDate", "ascending": "true", "limit": page_size,
              "end_date_min": utc_iso(begin), "end_date_max": utc_iso(end)}
    error = "page_cap"
    while pages < page_cap and time.monotonic() < deadline:
        query = params if cursor is None else params | {"after_cursor": cursor}
        url = GAMMA + "/events/keyset?" + urlencode(query)
        payload, _ = _get_safe(url, deadline, clock)
        data = record(payload).get("events")
        if not isinstance(data, list) or len(data) > page_size:
            error = "event_page_unavailable" if payload is None else "invalid_event_page"
            break
        parsed = {str(row["id"]): row for row in rows(data) if numeric_id(row.get("id"))}
        if len(parsed) != len(data) or result.keys() & parsed.keys():
            error = "duplicate_or_invalid_event"
            break
        result.update(parsed)
        pages += 1
        next_cursor = payload.get("next_cursor")
        if next_cursor in (None, ""):
            error = None
            break
        if not isinstance(next_cursor, str) or len(next_cursor) > 4096 or next_cursor == cursor or not data:
            error = "invalid_event_cursor"
            break
        cursor = next_cursor
    return result, {"tag": tag, "pages": pages, "rows": len(result), "partial": error is not None, "error": error}


def _collect_events(now, end, clock, deadline, cache):
    previous = record(cache.get("registry"))
    observed = stamp(previous.get("observed_at"))
    registry = previous.get("entries") if observed and timedelta(0) <= now - observed <= timedelta(hours=6) else None
    registry_error = None
    if not isinstance(registry, dict):
        data, at = _get_safe(GAMMA + "/sports", deadline, clock)
        if isinstance(data, list) and data and any(item.get("sport") == "nba" for item in rows(data)):
            registry = _league_registry({item["sport"]: item for item in rows(data) if isinstance(item.get("sport"), str)})
            cache["registry"] = {"observed_at": utc_iso(at), "entries": registry}
        else:
            registry = record(previous.get("entries"))
            registry_error = "basketball_registry_unavailable"
    missing_from_general = {code: numeric_id(item.get("primaryTagId")) for code, item in registry.items()
                            if SPORT_TAG not in common._tags(item.get("tags"))}
    tags = {SPORT_TAG, *(tag for tag in (EXTRA_TAGS | missing_from_general).values() if tag)}
    start = now - timedelta(hours=8)
    finish = end + timedelta(hours=36)
    windows = []
    while start < finish:
        boundary = min(finish, start + timedelta(hours=48))
        windows.append((start, boundary))
        start = boundary
    with ThreadPoolExecutor(max_workers=12) as pool:
        requests = [(tag, pool.submit(_gamma_lane, tag, lo, hi, clock, deadline))
                    for tag in sorted(tags) for lo, hi in windows]
        tagged, listings, lanes = {}, {}, defaultdict(list)
        for tag, job in requests:
            available, stats = job.result()
            for event_id, event in available.items():
                if event_id not in listings:
                    listings[event_id] = event
                tagged.setdefault(event_id, set()).add(tag)
            lanes[tag].append(stats)
    source = {tag: {"pages": sum(segment["pages"] for segment in segments),
                    "partial": any(segment["partial"] for segment in segments),
                    "errors": [segment["error"] for segment in segments if segment["error"]]}
              for tag, segments in lanes.items()}
    source["registry"] = {"rows": len(registry), "partial": registry_error is not None, "error": registry_error}
    return listings, tagged, registry, source


def _event_identity(event, tagged, registry):
    slug = event.get("slug")
    kickoff = stamp(event.get("startTime"))
    event_id = numeric_id(event.get("id"))
    if not isinstance(slug, str) or not common._SLUG.fullmatch(slug) or kickoff is None or event_id is None:
        return None
    base_match = GAME_BASE.match(slug)
    game_id, parent_id = numeric_id(event.get("gameId")), numeric_id(event.get("parentEventId"))
    embedded = record(event.get("sport"))
    league = embedded.get("sport") or slug.split("-", 1)[0]
    if not isinstance(league, str) or not common._SLUG.fullmatch(league):
        return None
    in_registry = league in registry or league in LEAGUES
    sport_tags = common._tags(event.get("tags"))
    is_basketball = SPORT_TAG in sport_tags or SPORT_TAG in tagged or in_registry
    if (not is_basketball or (base_match is None and game_id is None and parent_id is None)
            or not slug.startswith(league + "-")):
        return None
    team_rows = rows(event.get("teams"))
    home, away = common._gamma_teams(event)
    if not clean_label(home.get("name")) or not clean_label(away.get("name")) or _eq(home["name"], away["name"]):
        return None
    if team_rows and (len(team_rows) != 2 or home.get("ordering") != "home" or away.get("ordering") != "away"):
        return None
    if not team_rows and (embedded.get("ordering") or record(registry.get(league)).get("ordering")) == "away":
        home, away = away, home
    return {"id": event_id, "game_id": game_id, "parent_id": parent_id, "league": league,
            "base_slug": base_match[1] if base_match else slug, "slug": slug,
            "league_name": LEAGUES.get(league, clean_label(embedded.get("name")) or league),
            "kickoff": utc_iso(kickoff), "state": common._gamma_state(event), "home": home, "away": away,
            "featured": event.get("featured") is True}


def _identities(listings, tagged, registry, start, end):
    families = defaultdict(list)
    rejected = 0
    for event_id, event in listings.items():
        start_time = stamp(event.get("startTime"))
        if start_time is None or not start <= start_time < end:
            continue
        item = _event_identity(event, tagged[event_id], registry)
        if item is None:
            rejected += 1
            continue
        families[(item["league"], item["base_slug"])].append(item)
    stable = {}
    conflicts = set()
    for (league, base_slug), family in families.items():
        family.sort(key=lambda value: (value["slug"] != base_slug, value["slug"] != base_slug + "-more-markets",
                                       len(value["slug"]), value["id"]))
        primary = family[0]
        ids = {item["game_id"] for item in family if item["game_id"] is not None}
        if len(ids) > 1:
            rejected += len(family)
            continue
        game_id = next(iter(ids), None)
        root_event_id = primary["id"] if primary["slug"] == base_slug else primary["parent_id"]
        key = f"gamma:basketball:{league}:{game_id or root_event_id or base_slug}"
        related = [item for item in family if item["kickoff"] == primary["kickoff"]
                   and all(common._same_gamma_team(item[side], primary[side]) for side in ("home", "away"))]
        rejected += len(family) - len(related)
        if not related:
            continue
        fixture = {"id": key, "sport": "basketball", "game_id": game_id, "base_slug": base_slug,
                   "slug": base_slug, "root_event_id": root_event_id, "kickoff": primary["kickoff"],
                   "state": primary["state"], "league": league, "league_name": primary["league_name"],
                   "highlight": league in {"nba", "euroleague"} or primary["featured"],
                   "watch": league in RESEARCH_LEAGUES and primary["state"] == "scheduled",
                   "events": [{"id": item["id"], "slug": item["slug"]} for item in related]}
        for side in ("home", "away"):
            team = primary[side]
            fixture[side] = clean_label(team.get("name"))
            fixture[f"{side}_id"] = numeric_id(team.get("id"))
            fixture[f"{side}_alias"] = clean_label(team.get("alias"))
            fixture[f"{side}_abbr"] = clean_label(team.get("abbreviation"))
        other = stable.get(key)
        if other is not None:
            # A conflicting identity is excluded until the next authoritative scan.
            fields = ("home_id", "away_id", "home", "away", "kickoff", "base_slug")
            if any(fixture[name] != other[name] for name in fields):
                conflicts.add(key)
                rejected += 1
        stable[key] = fixture
    return [item for key, item in sorted(stable.items(), key=lambda pair: (pair[1]["kickoff"], pair[0]))
            if key not in conflicts], rejected


def _espn_state(status):
    name = status.get("name")
    if name in {"STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_ABANDONED"}:
        return "cancelled"
    if name in {"STATUS_POSTPONED", "STATUS_SUSPENDED", "STATUS_DELAYED"}:
        return "postponed"
    if name == "STATUS_FINAL" and status.get("state") == "post" and status.get("completed") is True:
        return "final"
    if name == "STATUS_SCHEDULED" and status.get("state") == "pre" and status.get("completed") is False:
        return "scheduled"
    return "started" if status.get("state") == "in" else "unconfirmed"


def _espn_fixture(raw, provider="espn:nba", *, summary=False):
    competitions = rows(raw.get("competitions"))
    if len(competitions) != 1:
        return None
    competition = competitions[0]
    kickoff = stamp(competition.get("date"))
    identity = numeric_id(raw.get("id"))
    if not identity or numeric_id(competition.get("id")) != identity or kickoff is None:
        return None
    if not summary and (stamp(raw.get("date")) != kickoff or raw.get("status") != competition.get("status")):
        return None
    status = record(competition.get("status"))
    state = _espn_state(record(status.get("type")))
    period = status.get("period")
    slots = rows(competition.get("competitors"))
    if len(slots) != 2 or {item.get("homeAway") for item in slots} != {"home", "away"}:
        return None
    if provider == "espn:nba" and state == "final":
        periods = [[number(line.get("displayValue", line.get("value"))) for line in rows(item.get("linescores"))] for item in slots]
        if (not all(len(line) >= 4 and all(value is not None and value >= 0 and value.is_integer() for value in line)
                    for line in periods) or len(periods[0]) != len(periods[1])
                or any(sum(line) != number(item.get("score")) for line, item in zip(periods, slots))
                or type(period) is int and period != len(periods[0])):
            return None
        period = len(periods[0])
    row = {"id": identity, "provider": provider, "kickoff": utc_iso(kickoff), "state": state,
           "neutral": competition.get("neutralSite") is True, "season_type": record(raw.get("season")).get("type"),
           "season_end_year": record(raw.get("season")).get("year"), "overtime": period > 4 if type(period) is int else None}
    for item in slots:
        side, team = item["homeAway"], record(item.get("team"))
        code, team_id = team.get("abbreviation"), numeric_id(team.get("id"))
        if not code or not team_id or team_id != numeric_id(item.get("id")):
            return None
        row[f"{side}_id"], row[f"{side}_code"] = team_id, code
        row[f"{side}_names"] = sorted({value for key in ("displayName", "shortDisplayName", "name", "abbreviation")
                                       if isinstance((value := team.get(key)), str) and value})
        if state == "final":
            score = number(item.get("score"))
            if score is None or score <= 0 or not score.is_integer():
                return None
            row[f"{side}_score"] = int(score)
        else:
            row[f"{side}_score"] = None
    if row["home_id"] == row["away_id"] or (state == "final" and row["home_score"] == row["away_score"]):
        return None
    return row


def _espn_board(day, clock, deadline):
    url = ESPN + "/nba/scoreboard?" + urlencode({"dates": day.strftime("%Y%m%d"), "limit": 100})
    payload, at = _get_safe(url, deadline, clock)
    events = record(payload).get("events")
    if not isinstance(events, list) or len(events) >= 100:
        return None
    games = [_espn_fixture(row) for row in rows(events)]
    if (len(games) != len(events) or any(game is None for game in games)
            or any(stamp(game["kickoff"]).astimezone(models.ET).date() != day for game in games)
            or len({game["id"] for game in games}) != len(games)):
        return None
    return {"observed_at": utc_iso(at), "url": url, "fixtures": games}


def _provider_board(league, year, clock, deadline):
    if league == "bkbbl":
        data = bbl.bbl_board(clock, deadline)
        if not data:
            return None
        local = [item for item in data["fixtures"] if item["season_year"] == year]
        return dict(data, fixtures=local, rejected=data.get("rejected", 0) + len(data["fixtures"]) - len(local)) if local else None
    if league in models.EURO_CODES:
        code = models.EURO_CODES[league]
        url = f"{EUROLEAGUE}/{code}/seasons/{code}{year}/games"
        parser = models.eurocl_fixture
    elif league == "bkvtb":
        url = f"{VTB}/{year + 1}/matches?limit=1000"
        parser = models.vtb_fixture
    else:
        return None
    data, at = _get_safe(url, deadline, clock)
    payload = record(data)
    games = payload.get("data")
    count = payload.get("total", record(payload.get("meta")).get("count"))
    if not isinstance(games, list) or not games or len(games) != count:
        return None
    parsed = [item for raw in rows(games) if (item := parser(raw)) is not None]
    if not parsed or len({item["id"] for item in parsed}) != len(parsed):
        return None
    return {"observed_at": utc_iso(at), "url": url, "fixtures": parsed, "rejected": len(games) - len(parsed)}


def _cache_boards(cache, now, end, fixtures, clock, deadline):
    nba_days = cache.setdefault("nba_boards", {})
    boards = cache.setdefault("boards", {})
    days = {stamp(item["kickoff"]).astimezone(models.ET).date() for item in fixtures if item["league"] == "nba"}
    year = int(models.nba_season(now).split("-")[0])
    leagues = {item["league"] for item in fixtures} & (INDEPENDENT_LEAGUES - {"nba"})

    def stale(value):
        last = stamp(record(value).get("observed_at"))
        return not last or not timedelta(0) <= now - last <= _SCHEDULE_AGE

    with ThreadPoolExecutor(max_workers=10) as pool:
        pending = [(day.isoformat(), nba_days, pool.submit(_espn_board, day, clock, deadline))
                   for day in sorted(days) if stale(nba_days.get(day.isoformat()))]
        pending.extend((league, boards, pool.submit(_provider_board, league, year, clock, deadline))
                       for league in sorted(leagues) if stale(boards.get(league)))
        errors = []
        for key, target, job in pending:
            board = job.result()
            if board:
                target[key] = board
                if target is boards:
                    _retain_finals(cache, key, board, year)
            else:
                errors.append(key)
    earliest = now.astimezone(models.ET).date() - timedelta(days=10)
    # A historical NBA board remains cached until its normalized games are stored.
    for key in list(nba_days):
        if key < earliest.isoformat() and key <= cache.get("nba_research_frontier", ""):
            del nba_days[key]
    return errors


def _retain_finals(cache, league, board, year):
    saved = cache.setdefault("europe_finals", {})
    last = {key: item for key, item in record(saved.get(league)).items() if item.get("season_year") == year}
    last.update({item["id"]: item for item in board["fixtures"] if item["state"] == "final" and item.get("season_year") == year})
    saved[league] = last


def _match(info, available, *, kickoff=None):
    kickoff = kickoff or stamp(info.get("kickoff"))
    if kickoff is None:
        return None
    matches = []
    for entry in available:
        official_tip = stamp(entry.get("kickoff"))
        if official_tip is None or abs(official_tip - kickoff) > _TIP_TOLERANCE:
            continue
        matched = []
        for side in ("home", "away"):
            code, names = entry.get(f"{side}_code"), entry.get(f"{side}_names", [])
            labels = _nba_names(code, espn_nba.NBA_TEAMS[code][1]) if info["league"] == "nba" and code in espn_nba.NBA_TEAMS else _aliases(names, info["league"])
            ours = _fixture_side_aliases(info, side)
            paths = [_fixture_side_aliases(info, team) & labels for team in ("home", "away")]
            matched.append(bool(ours & labels) and sum(bool(path) for path in paths) == 1)
        if all(matched):
            matches.append(entry)
    return matches[0] if len(matches) == 1 else None


def _independent(info, cache, now):
    league = info["league"]
    if league == "nba":
        day = stamp(info["kickoff"]).astimezone(models.ET).date().isoformat()
        board = record(record(cache.get("nba_boards")).get(day))
    else:
        board = record(record(cache.get("boards")).get(league))
    observed = stamp(board.get("observed_at"))
    if observed is None or not timedelta(0) <= now - observed <= timedelta(minutes=25):
        return None
    matched = _match(info, board.get("fixtures", []))
    return (matched | {"schedule_source": board["url"], "schedule_observed_at": board["observed_at"]}) if matched else None


def _nba_games(cache, now):
    season = models.nba_season(now)
    frozen, _ = models.nba_archive(season)
    known = {row["event_id"]: row for row in frozen if stamp(row["started"]) < now}
    if cache.get("nba_season") == season:
        known.update({key: value for key, value in record(cache.get("nba_final_games")).items()
                      if stamp(record(value).get("started")) is not None and stamp(value["started"]) < now})
    return list(known.values())


def discover(now: datetime, hours: int = 72, source_cache: dict | None = None, *, managed_scopes=()) -> tuple[list[dict], dict]:
    """Return every discoverable basketball game and bounded source diagnostics."""
    if now.utcoffset() is None or type(hours) is not int or not 1 <= hours <= 168:
        raise ValueError("basketball discovery requires an aware clock and a 1–168 hour horizon")
    now = now.astimezone(UTC)
    end, clock = now + timedelta(hours=hours), common._clock(now)
    cache = dict(source_cache or {})
    deadline = time.monotonic() + 38
    listings, tagged, registry, sources = _collect_events(now, end, clock, deadline, cache)
    fixtures, rejected = _identities(listings, tagged, registry, now, end)
    errors = _cache_boards(cache, now, end, fixtures, clock, deadline)
    snapshot = None
    games = _nba_games(cache, now)
    if games:
        try:
            snapshot = models.nba_snapshot(games, models.nba_season(now), now)
        except (ValueError, KeyError, espn_nba.EspnNbaError):
            errors.append("nba_snapshot_invalid")
    ratings_history, ratings_days, ratings_failures = [], {}, 0
    if snapshot is not None:
        try:
            ratings_history = models.nba_ratings_history(games, models.nba_season(now), cache.get("nba_prior_final_games"))
        except (OSError, KeyError, TypeError, ValueError):
            ratings_failures += 1
    report = record(cache.get("research_report"))
    nba_families = _approved_nba_families(report, now)
    managed = _managed_totals(managed_scopes)
    managed_leagues = {scope[0] for scope in managed}
    for fixture in fixtures:
        league = fixture["league"]
        official = _independent(fixture, cache, clock()) if league in INDEPENDENT_LEAGUES else None
        if official:
            fixture["basketball_official"] = official
            fixture["fixture_id"] = f"{official['provider']}:{official['id']}"
            if official["state"] in {"cancelled", "postponed", "started", "final"}:
                fixture["state"] = "started" if official["state"] == "final" else official["state"]
        projection = None
        comparison = None
        if official and official["state"] == "scheduled":
            try:
                projection = models.forecast(fixture, official, cache, now, snapshot)
            except (OSError, KeyError, ValueError, espn_nba.EspnNbaError):
                errors.append(f"{league}:model_input_unavailable")
            if league == "nba" and ratings_history:
                try:
                    comparison = models.nba_ratings_forecast(fixture, official, ratings_history, now, ratings_days)
                except (KeyError, TypeError, ValueError):
                    ratings_failures += 1
                else:
                    if comparison is not None:
                        fixture["nba_ratings_model"] = comparison
        if projection:
            fixture["model"] = projection
        if projection or comparison:
            admitted = league == "nba" and any(value and value["model_version"] in nba_families
                                                for value in (projection, comparison))
            fixture["model_status"] = "paper_gate_passed" if admitted else "research_only"
        else:
            history_bbl = record(cache.get("bbl_training"))
            fixture["model_status"] = "model_history_partial" if official and league == "bkbbl" and history_bbl.get("complete") is not True else "model_unavailable" if official else (
                "independent_fixture_missing" if league in INDEPENDENT_LEAGUES else
                "independent_provider_unavailable" if league in RESEARCH_LEAGUES else "unsupported_league")
        fixture["reason"] = fixture["model_status"]
    if (nba_families or managed) and time.monotonic() < deadline:
        eligible = [item for item in fixtures if item["model_status"] == "paper_gate_passed"
                    or (item["league"] in managed_leagues and record(item.get("model")).get("model_version") == models.EURO_VERSION)]
        euro_contracts = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for info, market_info in zip(eligible, pool.map(lambda item: _fixture_markets(item, clock, deadline), eligible)):
                _, _, markets, missing = market_info
                info["basketball_contracts"] = []
                for market in markets:
                    if market.scope != EXPLICIT_OT:
                        continue
                    if info["league"] == "nba":
                        info["basketball_contracts"].append(market.document())
                    elif _scope(info["league"], market.kind, market.line, market.team_side, models.EURO_VERSION) in managed:
                        euro_contracts.append((info, market))
                if missing:
                    errors.append(f"{info['league']}:discovery_market_metadata_partial")
            direct = [pool.submit(_market_direct, market.id, clock, deadline) for _, market in euro_contracts]
            for (info, market), task in zip(euro_contracts, direct):
                raw, observed = task.result()
                counterpart = _contract(info, raw, market.event_id, market.event_slug)
                if counterpart is None or counterpart.terms != market.terms or observed is None:
                    errors.append(f"{info['league']}:direct_market_metadata_unverified")
                    continue
                info["basketball_contracts"].append(market.document() | {"direct_contract_verified": True,
                    "direct_contract_observed_at": utc_iso(observed), "fixture_key": info["id"], "kickoff": info["kickoff"]})
        for info in eligible:
            if info["league"] in managed_leagues and approved_scopes(report, now=clock(), fixture=info, managed_scopes=managed):
                info["model_status"] = info["reason"] = "paper_gate_passed"
    sources["independent"] = {"errors": sorted(set(errors)), "partial": bool(errors),
                              "matched": sum("basketball_official" in item for item in fixtures)}
    league_count = Counter(item["league"] for item in fixtures)
    model_status = Counter(item["model_status"] for item in fixtures)
    stats = {"window_start": utc_iso(now), "window_end": utc_iso(end), "observed_at": utc_iso(clock()),
             "fixtures": len(fixtures), "leagues": len(league_count), "fixtures_by_league": dict(league_count),
             "supported": sum(item["league"] in RESEARCH_LEAGUES for item in fixtures),
             "matched": sources["independent"]["matched"], "modeled": sum(
                 "model" in item or "nba_ratings_model" in item for item in fixtures),
             "model_status": dict(model_status), "model_partial": bool(errors) or any(
                 item["league"] in RESEARCH_LEAGUES and item["state"] == "scheduled"
                 and "model" not in item and "nba_ratings_model" not in item for item in fixtures),
             "partial": any(value["partial"] for key, value in sources.items() if key != "independent"),
             "pages": sum(value.get("pages", 0) for value in sources.values()), "events": len(listings),
             "rejected": rejected, "sources": sources, "research": report, "source_cache": cache,
             "nba_ratings_modeled": sum("nba_ratings_model" in item for item in fixtures),
             "nba_ratings_failures": ratings_failures}
    return fixtures, stats


@dataclass(frozen=True)
class Selection:
    outcome: str
    token_id: str
    side: str
    label: str


@dataclass(frozen=True)
class Contract:
    id: str
    condition_id: str
    kind: str
    line: float | None
    team_side: str | None
    scope: str
    options: tuple[Selection, Selection]
    event_id: str
    event_slug: str
    raw: dict
    terms: str

    def document(self):
        return {"market_id": self.id, "condition_id": self.condition_id, "market_kind": self.kind,
                "line": self.line, "team_side": self.team_side, "settlement_scope": self.scope,
                "contract_digest": self.terms}


def _source_host(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname
            or parsed.netloc not in {parsed.hostname, parsed.hostname + ":443"}):
        return None
    return parsed.hostname.removeprefix("www.")


def _moneyline_rules(info, clauses):
    detected = []
    for clause in clauses:
        match = re.fullmatch(r'If (?:the )?(.+) win(?:s)?, the market will resolve to "([^"]+)"\.', clause, re.I)
        if not match:
            return False
        selected = [side for side in ("home", "away") if all(_side_matches(info, side, text) for text in match.groups())]
        if len(selected) != 1:
            return False
        detected.extend(selected)
    return sorted(detected) == ["away", "home"]


def _totals_rules(info, clauses, line):
    if len(clauses) != 2:
        return False
    over = re.fullmatch(r'This market will resolve to "Over" if (?:the )?(.+) and (.+) combine to score (\d+) or more points in this game\.', clauses[0], re.I)
    under = re.fullmatch(r'If the combined total is less than (\d+), this market will resolve to "Under"\.', clauses[1], re.I)
    return bool(over and under and int(over[3]) == int(under[1]) == math.ceil(line)
                and any(_side_matches(info, a, over[1]) and _side_matches(info, b, over[2])
                        for a, b in (("home", "away"), ("away", "home"))))


def _spread_rules(info, clauses, favorite, line):
    if len(clauses) != 2:
        return False
    win = re.fullmatch(r'This market will resolve to "([^"]+)" if (?:the )?(.+) win(?:s)? the game by (\d+) or more points\.', clauses[0], re.I)
    other = re.fullmatch(r'Otherwise, this market will resolve to "([^"]+)"\.(?: If the game ends in a tie, this market will resolve to "([^"]+)"\.)?', clauses[1], re.I)
    opponent = "away" if favorite == "home" else "home"
    return bool(win and other and int(win[3]) == math.ceil(abs(line))
                and all(_side_matches(info, favorite, win[index]) for index in (1, 2))
                and all(_side_matches(info, opponent, label) for label in other.groups() if label))


def _market_scope(description):
    normalized = " ".join(description.casefold().split())
    if re.search(r"(?:exclud(?:e|es|ing)|without|not includ(?:e|es|ing)) (?:any )?overtime|regulation(?: time)? only", normalized):
        return AMBIGUOUS_OT
    explicit = re.search(r"(?:final|combined|official) (?:score|total) includ(?:es|ing) (?:any )?overtime(?: periods?)?\b", normalized)
    return EXPLICIT_OT if explicit else AMBIGUOUS_OT


def _listed_at(raw):
    return stamp(raw.get("acceptingOrdersTimestamp")) or stamp(raw.get("startDate")) or stamp(raw.get("createdAt"))


def _contract(info, raw, event_id, event_slug, *, terminal=False, frozen_kickoff=None):
    raw = record(raw)
    kind = {"moneyline": "match_winner", "totals": "total", "spreads": "spread"}.get(raw.get("sportsMarketType"))
    market_id, condition, slug = numeric_id(raw.get("id")), raw.get("conditionId"), raw.get("slug")
    kickoff = stamp(info.get("kickoff"))
    if (kind is None or market_id is None or not isinstance(condition, str) or not CONDITION_ID.fullmatch(condition)
            or not isinstance(slug, str) or not common._SLUG.fullmatch(slug) or kickoff is None
            or stamp(raw.get("gameStartTime")) != kickoff or not isinstance(event_slug, str)
            or (not terminal and not common._open_market(raw))):
        return None
    descriptions = raw.get("description")
    question, source = raw.get("question"), raw.get("resolutionSource")
    if (not isinstance(descriptions, str) or not isinstance(question, str) or len(descriptions) > 16000
            or _source_host(source) not in _RESOLUTION_HOSTS.get(info["league"], ())):
        return None
    clauses = [line.strip().replace("“", '"').replace("”", '"') for line in descriptions.splitlines()
               if "will resolve to" in line.lower() and "50-50" not in line]
    try:
        pairs = common._contract_pairs(raw)
    except ValueError:
        return None
    options, line, team_side = [], None, None
    if kind == "total":
        line = number(raw.get("line"))
        if (line is None or line <= 0 or line % 1 != 0.5 or slug != f"{info['base_slug']}-total-{line:g}".replace(".", "pt")
                or {label.lower() for label, _ in pairs} != {"over", "under"} or not _totals_rules(info, clauses, line)):
            return None
        question_match = re.fullmatch(r"(.+): O/U (\d+(?:\.\d+)?)", question, re.I)
        if question_match is None or number(question_match[2]) != line:
            return None
        teams = common._TEAM_TITLE.split(question_match[1], maxsplit=1)
        if len(teams) != 2 or not any(_side_matches(info, side, teams[0]) and _side_matches(info, other, teams[1])
                                     for side, other in (("home", "away"), ("away", "home"))):
            return None
        for label, token in pairs:
            direction = "大" if label.lower() == "over" else "小"
            options.append(Selection(label, token, label.lower(), f"全场{direction} {line:g}（含加时）" if _market_scope(descriptions) == EXPLICIT_OT else f"全场{direction} {line:g}"))
    else:
        if kind == "match_winner":
            if slug != info["base_slug"] or raw.get("line") is not None or not _moneyline_rules(info, clauses):
                return None
        else:
            label = re.fullmatch(r"Spread: (.+) \(([+-]?\d+(?:\.\d+)?)\)", question, re.I)
            points = number(label[2]) if label else None
            favorites = [side for side in ("home", "away") if label and _side_matches(info, side, label[1])]
            supplied = number(raw.get("line"))
            if (points is None or points >= 0 or abs(points) % 1 != 0.5 or len(favorites) != 1
                    or supplied is None or abs(supplied) != abs(points)):
                return None
            team_side = favorites[0]
            line = -points if team_side == "home" else points
            if (slug != f"{info['base_slug']}-spread-{team_side}-{abs(points):g}".replace(".", "pt")
                    or not _spread_rules(info, clauses, team_side, line)):
                return None
        for label, token in pairs:
            matches = [side for side in ("home", "away") if _side_matches(info, side, label)]
            if len(matches) != 1:
                return None
            side = matches[0]
            if line is None:
                direction = f"{info[side]} 胜"
            else:
                spread = -line if side == "home" else line
                direction = f"{info[side]} {spread:+g}"
            options.append(Selection(label, token, side, direction))
        if {option.side for option in options} != {"home", "away"}:
            return None
    terms = document_digest({"market_id": market_id, "condition_id": condition, "slug": slug, "source": source,
                             "question": question, "rules": " ".join(descriptions.split()), "kickoff": frozen_kickoff or info["kickoff"],
                             "kind": kind, "line": line, "team_side": team_side, "pairs": [list(pair) for pair in pairs]})
    return Contract(market_id, condition, kind, line, team_side, _market_scope(descriptions), tuple(options),
                    event_id, event_slug, raw, terms)


def _gamma_event(info, ref, clock, deadline):
    event_id = numeric_id(ref.get("id"))
    if event_id is None:
        return None
    raw, _ = _get_safe(f"{GAMMA}/events/{event_id}", deadline, clock)
    if not isinstance(raw, dict):
        return None
    event = _event_identity(raw, {SPORT_TAG}, {info["league"]: {}})
    if event is None or any(event.get(key) != value for key, value in (("id", event_id), ("slug", ref["slug"]),
                              ("league", info["league"]), ("base_slug", info["base_slug"]))):
        return None
    if info.get("game_id") and info["game_id"] != event["game_id"]:
        return None
    for side in ("home", "away"):
        team = event[side]
        stored_id = info.get(f"{side}_id")
        if stored_id and numeric_id(team.get("id")) != stored_id or not _side_matches(info, side, team.get("name")):
            return None
    return raw


def _fixture_markets(info, clock, deadline):
    references = [ref for ref in rows(info.get("events")) if ref.get("slug") in {info["base_slug"], info["base_slug"] + "-more-markets"}]
    if not references:
        return "independent_fixture_missing", None, (), True
    with ThreadPoolExecutor(max_workers=2) as pool:
        fetched = list(pool.map(lambda ref: _gamma_event(info, ref, clock, deadline), references))
    main = fetched[0]
    if not main:
        return "event_unavailable", None, (), True
    state = common._gamma_state(main)
    if state != "scheduled":
        return state, None, (), False
    new_time = stamp(main.get("startTime"))
    if new_time != stamp(info["kickoff"]):
        return "postponed", utc_iso(new_time) if new_time and new_time > clock() else None, (), False
    if main.get("closed") is not False or main.get("active") is not True or main.get("archived") is True:
        return "event_unavailable", None, (), True
    contracts = {}
    conflicts = set()
    partial = False
    for event in fetched:
        if (not event or stamp(event.get("startTime")) != new_time or common._gamma_state(event) != "scheduled"
                or not isinstance(event.get("markets"), list)):
            partial = True
            continue
        for raw in rows(event.get("markets")):
            market = _contract(info, raw, str(event["id"]), event["slug"])
            if market:
                previous = contracts.get(market.condition_id)
                if previous and previous.terms != market.terms:
                    conflicts.add(market.condition_id)
                contracts[market.condition_id] = market
    markets = tuple(market for key, market in contracts.items() if key not in conflicts)
    return "scheduled", None, markets, partial or bool(conflicts)


def _official_id(item):
    return tuple(item.get(key) for key in ("provider", "id", "home_id", "away_id"))


def _official_summary(info, saved, clock, deadline):
    if saved.get("provider") == "espn:nba" and numeric_id(saved.get("id")):
        url = ESPN + "/nba/summary?" + urlencode({"event": saved["id"]})
        raw, observed = _get_safe(url, deadline, clock)
        exact = _espn_fixture(record(record(raw).get("header")), summary=True) if raw else None
    elif info["league"] == "bkbbl" and saved.get("provider") == bbl.PROVIDER and numeric_id(saved.get("id")):
        exact, proof = bbl.bbl_summary(saved["id"], clock, deadline)
        url, observed = (proof["source_url"], stamp(proof["observed_at"])) if proof else (None, None)
        raw = _bbl_lineup(record(record(proof).get("pre_match_stats")), exact, url, observed) if exact else None
    elif info["league"] == "bkeurocup" and (url := _eurocup_game_url(saved)):
        payload, observed = _get_safe(url, deadline, clock)
        exact, raw = models.eurocl_fixture(payload), None
        if exact and exact["state"] == "scheduled" and _official_id(exact) == _official_id(saved) and clock() < stamp(exact["kickoff"]):
            stats, checked = _get_safe(url + "/stats", deadline, clock)
            raw = _eurocup_lineup(record(stats), exact, url + "/stats", checked)
    elif info["league"] in INDEPENDENT_LEAGUES - {"nba"} and type(saved.get("season_year")) is int:
        board = _provider_board(info["league"], saved["season_year"], clock, deadline)
        candidates = [item for item in board["fixtures"] if _official_id(item) == _official_id(saved)] if board else []
        exact = candidates[0] if len(candidates) == 1 else None
        url, observed, raw = (board["url"], stamp(board["observed_at"]), None) if board else (None, None, None)
    else:
        return None, None
    if exact is None or _official_id(exact) != _official_id(saved):
        return None, None
    return exact | {"schedule_source": url, "schedule_observed_at": utc_iso(observed)}, raw


def _eurocup_game_url(official):
    identity = official.get("id")
    match = re.fullmatch(r"U(\d{4})_([1-9]\d{0,3})", identity) if isinstance(identity, str) else None
    if match and official.get("provider") == "euroleague" and int(match[1]) == official.get("season_year"):
        return f"{EUROLEAGUE}/U/seasons/U{match[1]}/games/{match[2]}"
    return None


def _official_rosters(evidence, official, url, checked, player):
    tip = stamp(official["kickoff"])
    if official["state"] != "scheduled" or tip is None or checked is None or checked >= tip or len(evidence) != 2:
        return None
    secured = {}
    for team, group in evidence:
        entries = rows(group)
        if not 5 <= len(entries) <= 20 or len(entries) != len(group):
            return None
        details = [player(value, team) for value in entries]
        if any(value is None for value in details) or len({value[0] for value in details}) != len(details):
            return None
        confirmed = sorted(key for key, starts in details if starts)
        # Some official feeds publish the registered players before marking starters.
        if len(confirmed) not in {0, 5}:
            return None
        secured[team] = {"available": sorted(key for key, _ in details), "starters": confirmed}
    return {"source": url, "observed_at": utc_iso(checked), "registered_players": secured}


def _bbl_lineup(stats, official, url, checked):
    # The stats transport can say LIVE while the game page is still PRE.
    # The exact fixture must have a zero-time stat sheet and no game actions.
    if (numeric_id(stats.get("gameId")) != official["id"] or stamp(stats.get("scheduledTime")) != stamp(official["kickoff"])
            or stats.get("actions") != []):
        return None
    groups = []
    for side, key in (("home", "homeTeam"), ("away", "guestTeam")):
        group, team = record(stats.get(key)), official[f"{side}_id"]
        line = record(group.get("gameStat"))
        team_record = record(line.get("seasonTeam"))
        if (numeric_id(group.get("id")) != team or numeric_id(team_record.get("teamId")) != team
                or team_record.get("seasonId") != official["season_year"] or numeric_id(line.get("gameId")) != official["id"]
                or not _bbl_pregame_stats(line)):
            return None
        groups.append((team, group.get("playerStats")))

    def player(item, reference_team):
        key = numeric_id(record(item.get("seasonPlayer")).get("playerId"))
        team = numeric_id(record(item.get("seasonTeam")).get("teamId"))
        starter = item.get("isStartingFive")
        if (key is not None and team == reference_team and numeric_id(item.get("gameId")) == official["id"]
                and type(starter) is bool and _bbl_pregame_stats(item)):
            return key, starter
        return None

    return _official_rosters(groups, official, url, checked, player)


def _bbl_pregame_stats(entry):
    seconds = entry.get("secondsPlayed")
    return (seconds is None or number(seconds) == 0) and number(entry.get("points")) == 0


def _eurocup_lineup(stats, official, url, checked):
    groups = [(official[f"{side}_id"], record(stats.get(key)).get("players"))
              for side, key in (("home", "local"), ("away", "road"))]
    expected_season = f"U{official['season_year']}"

    def player(entry, team):
        person, result = record(entry.get("player")), record(entry.get("stats"))
        key = record(person.get("person")).get("code")
        starter = result.get("startFive")
        if (isinstance(key, str) and re.fullmatch(r"[A-Z0-9]{1,12}", key) and person.get("active") is True
                and record(person.get("club")).get("code") == team and record(person.get("season")).get("code") == expected_season
                and type(starter) is bool and result.get("timePlayed") == 0 and result.get("points") == 0):
            return key, starter
        return None

    return _official_rosters(groups, official, url, checked, player)


def _news_snapshot(payload, official, info):
    source = _news_source(info, official)
    if source is None or not isinstance(payload, dict):
        return None
    signed = {"fixture_key": info["id"], "kickoff": info["kickoff"], "source": source,
              "provider": official["provider"], "source_fixture_id": official["id"],
              "team_ids": [official["home_id"], official["away_id"]]}
    if info["league"] in MANAGED_EURO_TOTALS:
        observed = stamp(payload.get("observed_at"))
        tip = stamp(official["kickoff"])
        if (payload.get("source") != source or observed is None or tip is None or observed >= tip
                or not _valid_registered_players(payload.get("registered_players"), signed["team_ids"])):
            return None
        signed.update({"observed_at": payload["observed_at"], "registered_players": payload["registered_players"]})
        return signed | {"sha256": document_digest(signed)}
    if not isinstance(payload.get("injuries"), list):
        return None
    team_ids = {official["home_id"], official["away_id"]}
    reported = set()
    statuses = {}
    for group in rows(payload.get("injuries")):
        team_id = numeric_id(record(group.get("team")).get("id"))
        if team_id not in team_ids or team_id in reported or not isinstance(group.get("injuries"), list):
            return None
        reported.add(team_id)
        for item in rows(group.get("injuries")):
            status = clean_label(item.get("status")).casefold()
            athlete_id = numeric_id(record(item.get("athlete")).get("id"))
            reported_at = stamp(item.get("date"))
            if (athlete_id is None or not status or reported_at is None or reported_at > stamp(official["schedule_observed_at"])
                    or (team_id, athlete_id) in statuses):
                return None
            statuses[(team_id, athlete_id)] = status, utc_iso(reported_at)
    signed.update({"observed_at": official["schedule_observed_at"],
              "reports": [[team, athlete, status, reported_at] for (team, athlete), (status, reported_at) in sorted(statuses.items())]})
    return signed | {"sha256": document_digest(signed)}


def _news_source(info, official):
    league, provider, key = info.get("league"), official.get("provider"), official.get("id")
    if league == "nba" and provider == "espn:nba" and numeric_id(key):
        return ESPN + "/nba/summary?" + urlencode({"event": key})
    if league == "bkbbl" and provider == bbl.PROVIDER and numeric_id(key):
        return f"{bbl.ORIGIN}/spiele/{key}"
    if league == "bkeurocup" and (url := _eurocup_game_url(official)):
        return url + "/stats"
    return None


def _valid_registered_players(value, teams):
    if not isinstance(value, dict) or len(set(teams)) != 2 or set(value) != set(teams):
        return False
    for group in value.values():
        available, starters = record(group).get("available"), record(group).get("starters")
        if not isinstance(available, list) or not isinstance(starters, list) or not 5 <= len(available) <= 20 or len(starters) not in {0, 5}:
            return False
        if not all(isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{1,20}", code) for code in available + starters):
            return False
        if len(set(available)) != len(available) or len(set(starters)) != len(starters) or not set(starters) <= set(available):
            return False
    return True


def validate_news_baseline(info, value):
    """Validate signed, occurrence-specific official basketball availability evidence."""
    context = record(value)
    official = record(info.get("basketball_official"))
    kickoff = stamp(info.get("kickoff"))
    observed = stamp(context.get("observed_at"))
    source = _news_source(info, official)
    if (kickoff is None or observed is None or not kickoff - timedelta(hours=8) <= observed < kickoff
            or not source or context.get("provider") != official.get("provider") or context.get("source") != source
            or context.get("source_fixture_id") != official.get("id")
            or context.get("fixture_key") != info.get("id") or context.get("kickoff") != info.get("kickoff")
            or context.get("team_ids") != [official.get("home_id"), official.get("away_id")]):
        return None
    if info.get("league") in MANAGED_EURO_TOTALS:
        if not _valid_registered_players(context.get("registered_players"), context["team_ids"]):
            return None
    else:
        reports = context.get("reports")
        if not isinstance(reports, list) or len(reports) > 80:
            return None
        seen = set()
        for row in reports:
            if (not isinstance(row, list) or len(row) != 4 or row[0] not in context["team_ids"] or numeric_id(row[1]) is None
                    or not isinstance(row[2], str) or not row[2] or len(row[2]) > 48
                    or stamp(row[3]) is None or stamp(row[3]) > observed or (row[0], row[1]) in seen):
                return None
            seen.add((row[0], row[1]))
    try:
        signed = {key: item for key, item in context.items() if key != "sha256"}
        if context.get("sha256") != document_digest(signed):
            return None
    except (TypeError, ValueError):
        return None
    return context


def _lineup_check(payload, official, info, stage, opening, live):
    source = _news_source(info, official) or official["schedule_source"]
    sources = (source,)
    if live is None:
        return gates.NewsCheck(info["id"], stage, False, ("basketball_availability_unavailable",), sources)
    baseline = validate_news_baseline(info, info.get("basketball_news_baseline"))
    comparable = bool(baseline and opening and stamp(baseline["observed_at"]) < opening < stamp(live["observed_at"]))
    if info["league"] in MANAGED_EURO_TOTALS:
        players = live["registered_players"]
        if comparable:
            earlier = baseline["registered_players"]
            changed = any(earlier[team]["available"] != roster["available"]
                          or (earlier[team]["starters"] and roster["starters"] and earlier[team]["starters"] != roster["starters"])
                          for team, roster in players.items())
            if changed:
                return gates.NewsCheck(info["id"], stage, False, ("pregame_basketball_availability_changed",), sources)
        confirmed = all(len(roster["starters"]) == 5 for roster in players.values())
        blockers = () if confirmed or comparable else ("basketball_availability_baseline_missing",)
        return gates.NewsCheck(info["id"], stage, not blockers, blockers, sources)
    teams = live["team_ids"]
    lineup = {}
    for group in rows(payload.get("rosters")):
        team = numeric_id(record(group.get("team")).get("id"))
        players = rows(group.get("roster")) or rows(group.get("athletes"))
        starters = {numeric_id(record(item.get("athlete")).get("id")) for item in players if item.get("starter") is True}
        if team in teams and len(starters) == 5 and None not in starters and team not in lineup:
            lineup[team] = starters
    confirmed = set(lineup) == set(teams)
    injuries = {(team, player): status for team, player, status, _ in live["reports"] if status in _FITNESS_RISKS}
    if comparable:
        previous = {(team, player): status for team, player, status, _ in baseline["reports"] if status in _FITNESS_RISKS}
    if comparable and previous != injuries:
        blockers = ("pregame_basketball_availability_changed",)
    elif confirmed and any(player in lineup[team] for team, player in injuries):
        blockers = ("starter_injury_status_conflict",)
    elif comparable or confirmed:
        blockers = ()
    else:
        blockers = ("basketball_availability_baseline_missing",)
    return gates.NewsCheck(info["id"], stage, not blockers, blockers, sources)


def _scope(league, kind, line, side, version):
    nba = league == "nba" and isinstance(version, str) and version in NBA_REPORT_KEYS
    euro_total = league in MANAGED_EURO_TOTALS and version == models.EURO_VERSION and kind == "total"
    if not (nba or euro_total):
        return None
    if kind == "match_winner" and line is None and side is None:
        return (league, kind, None, None, version)
    points = number(line)
    if kind == "total" and side is None and points is not None and points > 0 and points % 1 == 0.5:
        return (league, kind, points, None, version)
    return None


def _managed_totals(scopes):
    return frozenset(scope for value in scopes if isinstance(value, (tuple, list)) and len(value) == 5
                     and isinstance(value[0], str) and value[0] in MANAGED_EURO_TOTALS and (scope := _scope(*value)) is not None)


def _approved_families(report, now, version=models.NBA_VERSION):
    if version not in NBA_REPORT_KEYS:
        return frozenset()
    nba = record(report.get(NBA_REPORT_KEYS[version]))
    at = stamp(report.get("observed_at"))
    if (report.get("status") != "evaluated" or report.get("policy_version") != POLICY_VERSION
            or report.get("nba_season") != models.nba_season(now) or nba.get("model_version") != version
            or not at or not timedelta(0) <= now - at <= _REPORT_AGE
            or nba.get("retest_ready") is not True or type(nba.get("eligible_games")) is not int
            or nba["eligible_games"] < models.NBA_RETEST_GAMES
            or not all(record(nba.get(key)).get("passed") is True for key in ("A", "C", "D"))):
        return frozenset()
    promoted = set()
    gate_e = record(nba.get("E"))
    if gate_e.get("passed") is True and gate_e.get("polymarket_at_bar") is True:
        promoted.add("match_winner")
    totals = record(nba.get("same_line_totals"))
    if (record(nba.get("B")).get("passed") is True and totals.get("model_beats_coin") is True
            and totals.get("polymarket_strict_n", 0) >= models._wp().GATE_D_MIN_N):
        promoted.add("total")
    return frozenset(promoted)


def _approved_nba_families(report, now):
    return {version: families for version in NBA_REPORT_KEYS if (families := _approved_families(report, now, version))}


def _verified_model(info, official, now, *, ratings=False):
    key = "nba_ratings_model" if ratings else "model"
    model = record(info.get(key))
    observed = stamp(model.get("observed_at"))
    expected = [official.get(key) for key in ("provider", "id", "home_id", "away_id", "kickoff")]
    names, distributions = model.get("variant_names"), model.get("distributions")
    version = model.get("model_version")
    if ratings:
        cutoff = stamp(model.get("input_cutoff"))
        digest = model.get("input_sha256")
        if (info.get("league") != "nba" or not cutoff or not observed or cutoff > observed
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            return None
        expected_variants = {models.NBA_RATINGS_VERSION: list(models.ratings.VARIANT_NAMES)}
    else:
        expected_variants = {models.NBA_VERSION: list(models.nba_model.VARIANT_NAMES),
                             models.EURO_VERSION: ["baseline", "registered-candidate"]}
    if (model.get("fixture_key") != info["id"] or model.get("kickoff") != info["kickoff"]
            or model.get("official_identity") != expected or not observed or not timedelta(0) <= now - observed <= _MODEL_AGE
            or not isinstance(names, list) or not isinstance(distributions, list) or len(names) != len(distributions)
            or not isinstance(version, str) or names != expected_variants.get(version)):
        return None
    if any(not isinstance(values, list) or len(values) != 4 or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
           or values[1] <= 0 or values[3] <= 0 for values in distributions):
        return None
    if model.get("sha256") != document_digest({key: value for key, value in model.items() if key != "sha256"}):
        return None
    return model


def approved_scopes(report, *, now, fixture, managed_scopes=()):
    """Return fresh, exact basketball scopes that the current research evidence supports."""
    league = fixture.get("league")
    families = _approved_nba_families(report, now) if league == "nba" else {}
    managed = _managed_totals(managed_scopes) if league in MANAGED_EURO_TOTALS else frozenset()
    official = record(fixture.get("basketball_official"))
    if not (families or managed) or not official:
        return frozenset()
    result = set()
    projections = [_verified_model(fixture, official, now)]
    if models.NBA_RATINGS_VERSION in families:
        projections.append(_verified_model(fixture, official, now, ratings=True))
    contracts = [market for market in rows(fixture.get("basketball_contracts")) if market.get("settlement_scope") == EXPLICIT_OT]
    for model in filter(None, projections):
        version = model["model_version"]
        for market in contracts:
            scope = _scope(league, market.get("market_kind"), market.get("line"), market.get("team_side"), version)
            if scope and scope[1] in families.get(version, ()):
                result.add(scope)
            elif scope in managed and _fresh_managed_contract(fixture, market, now):
                result.add(scope)
    return frozenset(result)


def _fresh_managed_contract(info, market, now):
    observed = stamp(market.get("direct_contract_observed_at"))
    condition, digest = market.get("condition_id"), market.get("contract_digest")
    return (market.get("direct_contract_verified") is True and market.get("fixture_key") == info.get("id")
            and market.get("kickoff") == info.get("kickoff") and observed is not None
            and timedelta(0) <= now - observed <= _MODEL_AGE and numeric_id(market.get("market_id")) is not None
            and isinstance(condition, str) and CONDITION_ID.fullmatch(condition) is not None
            and isinstance(digest, str) and re.fullmatch(r"[\da-f]{64}", digest) is not None)


def _books(markets, clock, deadline):
    tokens = list(dict.fromkeys(option.token_id for market in markets for option in market.options))
    with ThreadPoolExecutor(max_workers=4) as pool:
        chunks = [tokens[index:index + 60] for index in range(0, len(tokens), 60)]
        batches = pool.map(lambda chunk: common._book_batch(chunk, clock, deadline), chunks)
        response = {token: entry for batch in batches for token, entry in batch.items()}
        missing = [token for token in tokens if token not in response][:3]
        response.update(pool.map(lambda token: common._book_single(token, clock, deadline), missing))
    return response


def _parsed_book(market, option, observed):
    raw, received = observed
    if not isinstance(raw, dict) or raw.get("asset_id") != option.token_id or raw.get("market") != market.condition_id or not received:
        return None
    try:
        book = common.pm._parse_order_book(option.token_id, raw)
    except (TypeError, ValueError, common.pm._MarketRejected):
        return None
    at = book.book_observed_at
    if at is None or not timedelta(0) <= received - at <= common.BOOK_AGE:
        return None
    asks = [(level.price, level.size) for level in book.asks if level.size > 0]
    bid = next((level.price for level in book.bids if level.size > 0), None)
    ask = asks[0][0] if asks else None
    if (ask is not None and not 0 < ask < 1 or bid is not None and not 0 < bid < 1
            or ask is not None and bid is not None and bid >= ask):
        return None
    midpoint = (bid + ask) / 2 if ask is not None and bid is not None else None
    return {"asks": asks, "bid": bid, "ask": ask, "midpoint": midpoint, "at": at, "received": received}


def _two_side_price(market, parsed):
    books = [parsed.get(option.token_id) for option in market.options]
    if (any(book is None or book["midpoint"] is None for book in books)
            or abs(books[0]["at"] - books[1]["at"]) > common.BOOK_AGE):
        return {}
    probabilities = [book["midpoint"] for book in books]
    total = sum(probabilities)
    return {option.token_id: p / total for option, p in zip(market.options, probabilities)} if 0.90 <= total <= 1.10 else {}


def _ordered_markets(markets, model):
    targets = {"total": model.get("expected_total", 200), "spread": model.get("expected_margin", 0)} if model else {"total": 200, "spread": 0}
    first, remaining = [], []
    for kind in ("match_winner", "total", "spread"):
        options = sorted((market for market in markets if market.kind == kind), key=lambda market: (
            market.scope != EXPLICIT_OT, abs((market.line or 0) - targets.get(kind, 0)), int(market.id)))
        first.extend(options[:3])
        remaining.extend(options[3:])
    return first + remaining


def _liquidity(raw):
    value = number(raw.get("liquidityNum"))
    return number(raw.get("liquidity")) if value is None else value


def _candidate(info, market, option, book, model, news, now, cutoff, raw):
    if book is None or not book["asks"] or not timedelta(0) <= now - book["at"] <= common.BOOK_AGE:
        return None
    fee = common._fee_rate(raw)
    liquidity = _liquidity(raw)
    prediction = models.quote(model, market.kind, market.line, option.side)
    if fee is None or liquidity is None or liquidity < 0 or prediction is None:
        return None
    gate = gates.evaluate_candidate(prediction, book["asks"], gamma_liquidity=liquidity, news=news, fee_rate=fee)
    return gate, fee, prediction, min(cutoff, book["at"] + common.BOOK_AGE)


def _market_direct(market_id, clock, deadline):
    if numeric_id(market_id) is None:
        return None, None
    return _get_safe(f"{GAMMA}/markets/{market_id}", deadline, clock)


def _observation(info, market, option, book, model, prediction, implied, raw, reasons):
    payload = market.document() | {"fixture_key": info["id"], "league": info["league"], "kickoff": info["kickoff"],
                                  "event_id": market.event_id, "event_slug": market.event_slug, "token_id": option.token_id,
                                  "outcome": option.outcome, "side": option.side, "direction": option.label,
                                  "fee_rate": common._fee_rate(raw), "gamma_liquidity": _liquidity(raw),
                                  "market_probability": implied.get(option.token_id), "reasons": reasons}
    official = record(info.get("basketball_official"))
    if official:
        payload["independent_fixture"] = official
    if prediction:
        payload.update({"model_probability": prediction["model_median"], "variants": prediction["qs_variants"],
                        "model_version": model.get("model_version"), "model_digest": model.get("sha256")})
    if book is not None:
        walk = gates.walk_book(book["asks"], side="ask", notional=gates.DEFAULT_NOTIONAL_USD)
        payload.update({"best_bid": book["bid"], "best_ask": book["ask"], "book_observed_at": utc_iso(book["at"]),
                        "book_received_at": utc_iso(book["received"]), "near_two_cent_depth": walk.depth_near2c,
                        "depth_filled_usd": walk.filled_notional, "entry_price": walk.vwap if walk.filled else None})
    return payload


def _watch(fixture, reason, **extra):
    return {"status": "watch", "reason_code": reason, "text": f"{fixture['home']} 对 {fixture['away']}｜篮球观察", **extra}


def _skip(reason, moved=None):
    output = {"status": "skip", "reason_code": reason, "text": ""}
    if moved:
        output["moved_kickoff"] = moved
    return output


def review(info: dict, now: datetime, stage: str) -> dict:
    """Evaluate both sides on the identical market line and preserve all refusals."""
    kickoff = stamp(info.get("kickoff"))
    if now.utcoffset() is None or kickoff is None or stage not in {"advance", "t90", "t30", "safety", "forward", "closing"}:
        return _skip("invalid_review")
    if now >= kickoff:
        return _skip("at_or_after_kickoff")
    if info.get("state") in {"cancelled", "postponed", "started"}:
        return _skip(info["state"])
    if info.get("league") not in RESEARCH_LEAGUES:
        return _watch(info, "unsupported_league")
    clock = common._clock(now.astimezone(UTC))
    frozen = record(info.get("basketball_official"))
    deadline = time.monotonic() + min(_MAX_REVIEW, (kickoff - now).total_seconds())
    with ThreadPoolExecutor(max_workers=2) as pool:
        listing = pool.submit(_fixture_markets, info, clock, deadline)
        schedule = pool.submit(_official_summary, info, frozen, clock, deadline) if frozen else None
        state, moved, markets, listing_partial = listing.result()
        official, summary = schedule.result() if schedule else (None, None)
    if state in {"cancelled", "postponed", "started"}:
        return _skip(state, moved)
    if clock() >= kickoff:
        return _skip("at_or_after_kickoff")
    if official and official["state"] in {"final", "started", "cancelled", "postponed"}:
        return _skip("started" if official["state"] == "final" else official["state"])
    if official and (official["kickoff"] != frozen.get("kickoff") or abs(stamp(official["kickoff"]) - kickoff) > _TIP_TOLERANCE):
        return _skip("postponed")
    effective_tip = min(kickoff, stamp(official["kickoff"])) if official else kickoff
    if clock() >= effective_tip:
        return _skip("at_or_after_kickoff")
    official = official if official and official["state"] == "scheduled" else None
    working = dict(info, basketball_official=official, basketball_contracts=[market.document() for market in markets])
    model = _verified_model(working, official, clock()) if official else None
    comparison_model = _verified_model(working, official, clock(), ratings=True) if official and info["league"] == "nba" else None
    news_snapshot = _news_snapshot(summary, official, info) if official else None
    updates = {}
    if (stage in {"advance", "t90"} and clock() < kickoff - timedelta(minutes=30) and news_snapshot
            and validate_news_baseline(working, working.get("basketball_news_baseline")) is None):
        updates["basketball_news_baseline"] = news_snapshot
    extra = {"updated_fixture": {key: value for key, value in (working | updates).items() if not key.startswith("_")}}
    if updates:
        extra["fixture_update"] = updates
    if not markets:
        return _watch(info, "event_unavailable" if listing_partial else "markets_unavailable", coverage_gap=listing_partial, **extra)
    if stage in {"advance", "safety"}:
        return _watch(info, "advance" if official else "independent_fixture_missing", coverage_gap=official is None, **extra)

    paper_window = {"t90": 90, "t30": 30}.get(stage)
    opening = kickoff - timedelta(minutes=paper_window) if paper_window else None
    cutoff = min(opening + timedelta(minutes=10), effective_tip) if opening else effective_tip
    paper_time = bool(opening and opening <= now < cutoff)
    if stage == "forward" and not kickoff - timedelta(minutes=30) <= now < kickoff - timedelta(minutes=20):
        return _watch(info, "forward_capture_window_closed", **extra)
    if stage == "closing" and not effective_tip - timedelta(minutes=3) <= now < effective_tip:
        return _watch(info, "closing_capture_window_closed", **extra)
    scopes = info.get("_paper_scopes", ())
    news = _lineup_check(summary, official, working, stage, opening, news_snapshot) if official else None
    ordered = _ordered_markets(markets, model or comparison_model)
    target_ids = set(info.get("_forward_market_ids", ()))
    if target_ids:
        ordered.sort(key=lambda market: market.id not in target_ids)
    kept = ordered[:96]
    reference_markets = [market for market in kept if market.scope == EXPLICIT_OT and (market.id in target_ids
                         or (market.kind == "total" and info["league"] in {"bkbbl", "bkeurocup", "nba"})
                         or (market.kind == "match_winner" and info["league"] == "nba"))][:24]
    with ThreadPoolExecutor(max_workers=10) as pool:
        book_task = pool.submit(_books, kept, clock, deadline)
        direct = {market.id: pool.submit(_market_direct, market.id, clock, deadline) for market in reference_markets}
        response = book_task.result()
        direct = {key: task.result() for key, task in direct.items()}
    current = clock()
    if current >= effective_tip:
        return _skip("at_or_after_kickoff")
    parsed = {option.token_id: _parsed_book(market, option, response.get(option.token_id, (None, None)))
              for market in kept for option in market.options}
    discovered = {numeric_id(item["market_id"]): item for item in rows(info.get("basketball_contracts"))
                  if _fresh_managed_contract(info, item, current)} if info["league"] in MANAGED_EURO_TOTALS else {}
    signals, observations, candidates = [], [], []
    for market in kept:
        market_model = model
        scope = _scope(info["league"], market.kind, market.line, market.team_side, model["model_version"]) if model else None
        if comparison_model and paper_time:
            ratings_scope = _scope(info["league"], market.kind, market.line, market.team_side, comparison_model["model_version"])
            if ratings_scope in scopes:
                market_model, scope = comparison_model, ratings_scope
        implied = _two_side_price(market, parsed)
        comparison = direct.get(market.id)
        directly_verified = False
        if comparison:
            counterpart = _contract(working, comparison[0], market.event_id, market.event_slug)
            directly_verified = counterpart is not None and counterpart.terms == market.terms
        discovery_terms_verified = (info["league"] not in MANAGED_EURO_TOTALS
                                    or record(discovered.get(market.id)).get("contract_digest") == market.terms)
        for option in market.options:
            book = parsed.get(option.token_id)
            if book and (not timedelta(0) <= current - book["at"] <= common.BOOK_AGE or book["received"] >= effective_tip):
                book = None
            prediction = models.quote(market_model, market.kind, market.line, option.side) if market_model else None
            reasons = []
            if official is None:
                reasons.append("independent_fixture_missing")
            if market_model is None:
                reasons.append("model_history_partial" if info.get("model_status") == "model_history_partial" else "model_unavailable")
            if market.scope != EXPLICIT_OT:
                reasons.append("overtime_rule_unverified")
            if scope is None or scope not in scopes:
                reasons.append("research_only")
            elif not discovery_terms_verified:
                reasons.append("scanned_contract_identity_unverified")
            if not paper_time:
                reasons.append("outside_paper_window")
            if not implied:
                reasons.append("two_sided_price_unavailable")
            if book is None:
                reasons.append("book_unavailable")
            screened = _candidate(info, market, option, book, market_model, news, current, cutoff, market.raw) if market_model else None
            if screened:
                reasons.extend(screened[0].decision.failed)
                if news and not news.concordant:
                    reasons.extend(news.blockers)
            elif book and market_model:
                reasons.append("fee_depth_or_liquidity_unverified")
            if comparison and not directly_verified:
                reasons.append("direct_contract_unverified")
            observation = _observation(working, market, option, book, market_model, prediction, implied,
                                       comparison[0] if directly_verified else market.raw, reasons)
            observation["direct_contract_verified"] = directly_verified
            shadow = models.quote(comparison_model, market.kind, market.line, option.side) if comparison_model else None
            if shadow is not None and market_model is not comparison_model:
                observation["shadow_predictions"] = {comparison_model["model_version"]: {
                    "model_probability": shadow["model_median"], "variants": shadow["qs_variants"],
                    "model_digest": comparison_model["sha256"], "observed_at": comparison_model["observed_at"],
                    "input_cutoff": comparison_model["input_cutoff"],
                }}
            if news_snapshot:
                observation["availability_evidence_sha256"] = news_snapshot["sha256"]
            observations.append(observation)
            if (paper_time and implied and scope in scopes and discovery_terms_verified and market.scope == EXPLICIT_OT and screened
                    and screened[0].decision.passed and current < screened[3]):
                candidates.append((market, option, book, implied, market_model))
    if candidates:
        unique = {market.id: market for market, _, _, _, _ in candidates if market.id not in direct}
        with ThreadPoolExecutor(max_workers=8) as pool:
            direct.update(dict(zip(unique, pool.map(lambda key: _market_direct(key, clock, deadline), unique))))
        for market, option, book, implied, market_model in candidates:
            raw, _ = direct[market.id]
            verified = _contract(working, raw, market.event_id, market.event_slug)
            if verified is None or verified.terms != market.terms:
                continue
            gate_result = _candidate(info, market, option, book, market_model, news, clock(), cutoff, raw)
            if not gate_result:
                continue
            gate, fee, prediction, expiry = gate_result
            if not gate.decision.passed or clock() >= expiry:
                continue
            signal = _observation(working, market, option, book, market_model, prediction, implied, raw, [])
            signal.update({"price": gate.entry_price, "valid_until": utc_iso(expiry), "net_edge": gate.exec_edge,
                           "conservative_probability": min(prediction["qs_variants"]),
                           "direct_contract_verified": True,
                           "availability_evidence_sha256": news_snapshot["sha256"],
                           "adjusted_edge": gate.exec_edge_haircut, "worst_edge": gate.worst_variant_edge,
                           "fee_per_share": gates.fee_cost(gate.entry_price, fee),
                           "url": f"https://polymarket.com/event/{market.event_slug}/{market.raw['slug']}"})
            signals.append(signal)
    if clock() >= effective_tip:
        return _skip("at_or_after_kickoff")
    extra.update({"signals": [], "observations": observations, "partial_books": listing_partial or len(kept) < len(ordered),
                  "market_count": len(markets)})
    if not signals:
        if official is None:
            reason = "independent_provider_unavailable" if info["league"] not in INDEPENDENT_LEAGUES else "independent_fixture_missing"
        elif not (model or comparison_model):
            reason = "model_history_partial" if info.get("model_status") == "model_history_partial" else "model_unavailable"
        elif not scopes:
            reason = "research_only"
        elif paper_window and not paper_time:
            reason = "opening_pending" if now < opening else "late_review"
        elif news and not news.concordant:
            reason = news.blockers[0]
        elif all(book is None for book in parsed.values()):
            reason = "books_unavailable"
        else:
            reason = "thresholds_unmet"
        return _watch(info, reason, coverage_gap=reason in {"independent_provider_unavailable", "independent_fixture_missing", "books_unavailable", "model_history_partial", "model_unavailable", "basketball_availability_unavailable", "basketball_availability_baseline_missing"}, **extra)
    signals.sort(key=common._paper_signal_rank)
    best = signals[0]
    text = f"{info['home']} 对 {info['away']}｜{best['direction']}，成交均价 {best['price'] * 100:.1f}¢，计费后优势 {best['net_edge']:+.1%}。"
    return {**extra, "status": "paper", "reason_code": "qualified", "text": text, "signal": best,
            "signals": signals, "valid_until": best["valid_until"]}


def _terminal(raw, now):
    if raw.get("closed") is not True or raw.get("umaResolutionStatus") != "resolved":
        return None
    time_closed = stamp(raw.get("closedTime"))
    prices = common.pm._maybe_json_list(raw.get("outcomePrices"))
    if not time_closed or time_closed > now or prices is None or len(prices) != 2:
        return None
    converted = [number(value) for value in prices]
    if converted not in ([0.0, 1.0], [1.0, 0.0], [0.5, 0.5]):
        return None
    return converted, time_closed


def _no_result_deadline(description):
    match = re.search(r"does not start by (?:[A-Za-z]+, )?([A-Za-z]+ \d{1,2}, \d{4}),? (\d{1,2}:\d{2} [AP]M) ET", description, re.I)
    if match:
        try:
            return datetime.strptime(" ".join(match.groups()), "%B %d, %Y %I:%M %p").replace(tzinfo=models.ET).astimezone(UTC)
        except ValueError:
            pass
    return None


def _payout(market, official, resolved=None):
    description = market.raw.get("description", "")
    if re.search(r"(?:will resolve|resolve) 50-50", description, re.I):
        if official.get("state") == "cancelled":
            return [0.5, 0.5]
        if official.get("state") == "postponed" and resolved:
            deadline = _no_result_deadline(description)
            if deadline is not None and resolved >= deadline:
                return [0.5, 0.5]
    if official.get("state") != "final" or official.get("overtime") is None:
        return None
    home, away = official.get("home_score"), official.get("away_score")
    if type(home) is not int or type(away) is not int or home == away:
        return None
    if market.kind == "total":
        winning = "over" if home + away > market.line else "under"
    else:
        winning = "home" if home - away > (market.line or 0.0) else "away"
    return [float(option.side == winning) for option in market.options]


def result(info: dict, signal: dict, now: datetime) -> dict | None:
    """Reconcile a frozen strict contract with its official final and exchange payout."""
    submitted = record(signal)
    kickoff = stamp(info.get("kickoff"))
    saved = record(submitted.get("independent_fixture"))
    market_id = numeric_id(submitted.get("market_id"))
    if (now.utcoffset() is None or kickoff is None or now <= kickoff or market_id is None or not saved
            or submitted.get("settlement_scope") != EXPLICIT_OT
            or submitted.get("fixture_key") not in {None, info.get("id")}):
        return None
    event_id = numeric_id(submitted.get("event_id"))
    references = [ref for ref in rows(info.get("events")) if ref.get("id") == event_id]
    if len(references) != 1:
        return None
    clock = common._clock(now.astimezone(UTC))
    deadline = time.monotonic() + 12
    with ThreadPoolExecutor(max_workers=2) as pool:
        direct = pool.submit(_market_direct, market_id, clock, deadline)
        score = pool.submit(_official_summary, info, saved, clock, deadline)
        raw, _ = direct.result()
        official, _ = score.result()
    if not official or not raw or not (source_tip := stamp(official.get("kickoff"))) or not (market_tip := stamp(raw.get("gameStartTime"))):
        return None
    frozen_source_tip = stamp(saved.get("kickoff"))
    if frozen_source_tip is None or (source_tip != frozen_source_tip) != (market_tip != kickoff):
        return None
    revised = dict(info, kickoff=utc_iso(market_tip))
    if _match(revised, [official]) is None:
        return None
    market = _contract(revised, raw, event_id, references[0]["slug"], terminal=True, frozen_kickoff=info["kickoff"])
    if (market is None or market.scope != EXPLICIT_OT or submitted.get("contract_digest") != market.terms
            or submitted.get("condition_id") != market.condition_id or submitted.get("market_kind") != market.kind
            or submitted.get("line") != market.line or submitted.get("team_side") != market.team_side):
        return None
    indices = [index for index, option in enumerate(market.options)
               if submitted.get("token_id") == option.token_id and submitted.get("outcome") == option.outcome
               and submitted.get("side") == option.side]
    terminal = _terminal(raw, clock())
    if len(indices) != 1 or not terminal or _payout(market, official, terminal[1]) != terminal[0]:
        return None
    prices, resolved = terminal
    if prices != [0.5, 0.5] and resolved <= max(kickoff, source_tip, market_tip):
        return None
    selected = prices[indices[0]]
    final = official["state"] == "final"
    home_score, away_score = official.get("home_score"), official.get("away_score")
    evidence = market.document() | {
        "official_fixture": official, "gamma_terminal_prices": prices, "selected_token": submitted["token_id"],
        "selected_payout": selected, "outcome_order": [option.outcome for option in market.options],
        "token_order": [option.token_id for option in market.options], "official_overtime": official.get("overtime"),
        "final_total": home_score + away_score if final else None, "final_margin": home_score - away_score if final else None,
        "source_url": f"{GAMMA}/markets/{market_id}", "verified_at": utc_iso(clock()),
        "resolution_source": raw["resolutionSource"], "market_closed_at": utc_iso(resolved),
        "entry_market_kickoff": info["kickoff"], "settled_market_kickoff": utc_iso(market_tip),
    }
    return {"result": {1.0: "win", 0.0: "loss", 0.5: "split"}[selected],
            "resolved_at": utc_iso(resolved), "evidence": evidence}


def _research_game(fixture, clock, deadline):
    url = ESPN + "/nba/summary?" + urlencode({"event": fixture["id"]})
    data, observed = _get_safe(url, deadline, clock)
    if not isinstance(data, dict):
        return None
    try:
        final = models.nba_summary(data, fixture)
        return final | {"final_observed_at": utc_iso(observed)} if observed else final
    except (TypeError, KeyError, ValueError, espn_nba.EspnNbaError):
        return None


def _research_scope_cache(cache, season, frozen):
    previous_season = cache.get("nba_season")
    if previous_season != season:
        earlier = int(season.split("-")[0])
        prior_seasons = [f"{year}-{(year + 1) % 100:02}" for year in range(earlier - 2, earlier)]
        previous = record(cache.get("nba_prior_final_games"))
        if previous_season in prior_seasons and cache.get("nba_final_games"):
            previous = previous | {previous_season: cache["nba_final_games"]}
        cache["nba_prior_final_games"] = {key: previous[key] for key in prior_seasons if key in previous}
        for key in ("nba_research_frontier", "nba_final_games", "nba_strict_pm", "nba_diagnostic_pm", "nba_predictions", "nba_research_failures"):
            cache.pop(key, None)
        cache["nba_season"] = season
    first = date(int(season.split("-")[0]), 10, 1)
    default_frontier = max((date.fromisoformat(row["date"]) for row in frozen), default=first - timedelta(days=1))
    frontier = date.fromisoformat(cache.get("nba_research_frontier", default_frontier.isoformat()))
    return default_frontier, frontier


def _latest_finals(cache, season, now, clock, deadline):
    frozen, _ = models.nba_archive(season)
    freeze_through, frontier = _research_scope_cache(cache, season, frozen)
    current_date = now.astimezone(models.ET).date()
    board_cache = cache.setdefault("nba_boards", {})
    finals = cache.setdefault("nba_final_games", {})
    coverage = {"freeze_through_date_et": freeze_through.isoformat(), "attempted_dates": [], "missing_boards": [],
                "missing_box_scores": [], "unfinalized_events": [], "fetched_box_scores": 0}
    through = min(current_date, frontier + timedelta(days=7))
    days = [frontier + timedelta(days=offset) for offset in range(1, (through - frontier).days + 1)]
    coverage["attempted_dates"] = [day.isoformat() for day in days]
    with ThreadPoolExecutor(max_workers=7) as pool:
        requested = {}
        for day in days:
            last = stamp(record(board_cache.get(day.isoformat())).get("observed_at"))
            if last is None or not timedelta(0) <= now - last <= _SCHEDULE_AGE:
                requested[day] = pool.submit(_espn_board, day, clock, deadline)
        for day, task in requested.items():
            result = task.result()
            key = day.isoformat()
            if result:
                board_cache[key] = result
            else:
                coverage["missing_boards"].append(key)
        regular = {}
        for day in days:
            key = day.isoformat()
            board = record(board_cache.get(key))
            if not board or key in coverage["missing_boards"]:
                continue
            games = [item for item in board["fixtures"] if item.get("season_type") == 2]
            regular[key] = games
        requested = {}
        for games in regular.values():
            for fixture in games:
                if (fixture["state"] == "final" and stamp(fixture["kickoff"]) < now
                        and fixture["id"] not in finals and len(requested) < 50):
                    requested[fixture["id"]] = pool.submit(_research_game, fixture, clock, deadline)
        for game_id, task in requested.items():
            row = task.result()
            if row:
                finals[game_id] = row
                coverage["fetched_box_scores"] += 1
    next_frontier = frontier
    blocked = False
    for day in days:
        key = day.isoformat()
        all_games = regular.get(key)
        missing = [game["id"] for game in all_games or [] if game["state"] == "final" and game["id"] not in finals]
        unresolved = [game["id"] for game in all_games or [] if stamp(game["kickoff"]) < now - timedelta(hours=6)
                      and game["state"] not in {"final", "cancelled", "postponed"}]
        coverage["missing_box_scores"].extend(missing)
        coverage["unfinalized_events"].extend(unresolved)
        day_waiting = any(game["state"] not in {"final", "cancelled", "postponed"} for game in all_games or [])
        if all_games is None or missing or day_waiting or day >= current_date:
            blocked = True
        if not blocked:
            next_frontier = day
    cache["nba_research_frontier"] = next_frontier.isoformat()
    coverage["verified_through_date_et"] = next_frontier.isoformat()
    coverage["days_pending"] = max(0, (current_date - next_frontier).days - 1)
    coverage["overview_ready"] = (coverage["days_pending"] == 0 and not coverage["missing_boards"]
                                  and not coverage["missing_box_scores"] and not coverage["unfinalized_events"])
    return coverage


def _nba_search_chunks(games, cache, now):
    final = record(cache.get("nba_strict_pm"))
    diagnostics = record(cache.get("nba_diagnostic_pm"))
    misses = record(cache.get("nba_research_failures"))
    previous_day = now.astimezone(models.ET).date() - timedelta(days=9)
    selected = [game for game in games if (game["event_id"] not in final or game["event_id"] not in diagnostics)
                and (game["date"] >= previous_day.isoformat() or game["event_id"] not in misses)
                and (stamp(misses.get(game["event_id"])) is None
                     or now - stamp(misses[game["event_id"]]) >= timedelta(hours=4))]
    selected.sort(key=lambda row: (row["started"], row["event_id"]))
    return selected[:36]


def _nba_lookup_spec(game):
    names = {side: espn_nba.NBA_TEAMS[game[side]][1] for side in ("home", "away")}
    abbr = {side: _NBA_PM_CODES.get(game[side], game[side].lower()) for side in ("home", "away")}
    day = game["date"]
    slugs = (f"nba-{abbr['away']}-{abbr['home']}-{day}", f"nba-{abbr['home']}-{abbr['away']}-{day}")
    return names, abbr, slugs


def _nba_market_slugs(base, odds):
    result = {base: "ml"}
    for kind in ("spread", "total"):
        line = number(record(odds.get(kind)).get("line"))
        if not line or abs(line) % 1 != 0.5:
            continue
        amount = f"{abs(line):g}".replace(".", "pt")
        suffix = f"spread-{'home' if line > 0 else 'away'}-{amount}" if kind == "spread" else f"total-{amount}"
        result[f"{base}-{suffix}"] = kind
    return result


def _collect_close_markets(games, clock, deadline):
    specs = [_nba_lookup_spec(game) for game in games]
    requested = {slug: (index, base, kind) for index, (game, (_, _, bases)) in enumerate(zip(games, specs))
                 for base in bases for slug, kind in _nba_market_slugs(base, record(game.get("dk_proxy"))).items()}
    slugs = list(requested)
    chunks = [slugs[offset:offset + 24] for offset in range(0, len(slugs), 24)]

    def fetch(chunk):
        query = urlencode([("closed", "true"), ("limit", 50), *[("slug", slug) for slug in chunk]])
        data, _ = _get_safe(GAMMA + "/markets?" + query, deadline, clock)
        if not isinstance(data, list):
            return []
        return [raw for raw in rows(data) if raw.get("slug") in chunk]

    jobs = []
    flags = Counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for values in pool.map(fetch, chunks):
            for raw in values:
                index, base, kind = requested[raw["slug"]]
                game = games[index]
                names, abbr, _ = specs[index]
                official_tip, market_tip = stamp(game["started"]), stamp(raw.get("gameStartTime"))
                listed = _listed_at(raw)
                if (market_tip is None or abs(market_tip - official_tip) > _TIP_TOLERANCE or listed is None
                        or listed > min(official_tip, market_tip) - timedelta(minutes=5)):
                    flags["clock_or_listing_unverified"] += 1
                    continue
                info = names | {"league": "nba", "base_slug": base, "kickoff": utc_iso(market_tip),
                                "home_abbr": abbr["home"], "away_abbr": abbr["away"]}
                market = _contract(info, raw, "", base, terminal=True)
                if market is None:
                    flags["same_line_terms_unverified"] += 1
                    continue
                terminal = _terminal(raw, clock())
                official = {"home_score": game["home_pts"], "away_score": game["away_pts"], "state": "final",
                            "overtime": game.get("overtime", False)}
                if not terminal or terminal[1] <= max(official_tip, market_tip) or terminal[0] != _payout(market, official):
                    flags["terminal_score_unreconciled"] += 1
                    continue
                if market.scope != EXPLICIT_OT:
                    flags[f"{kind}_overtime_unverified"] += 1
                jobs.append((index, kind, market, min(official_tip, market_tip), listed, terminal))
    return jobs, flags


def _history_last(token, tip, listed, clock, deadline):
    start = max(int((tip - timedelta(minutes=25)).timestamp()), int(listed.timestamp()))
    end = int((tip - timedelta(minutes=5)).timestamp())
    query = urlencode({"market": token, "startTs": start, "endTs": end, "fidelity": 5})
    data, _ = _get_safe(CLOB + "/prices-history?" + query, deadline, clock)
    candidates = [(item.get("t"), price) for item in rows(record(data).get("history"))
                  if type(item.get("t")) is int and start <= item["t"] <= end
                  and (price := number(item.get("p"))) is not None and 0 < price < 1]
    return max(candidates) if candidates else None


def _research_pm(cache, games, now, clock, deadline):
    final = cache.setdefault("nba_strict_pm", {})
    diagnostics = cache.setdefault("nba_diagnostic_pm", {})
    misses = cache.setdefault("nba_research_failures", {})
    selected = _nba_search_chunks(games, cache, now)
    if not selected or time.monotonic() >= deadline:
        return {"attempted_games": 0, "found_games": 0, "retained_games": len(final),
                "diagnostic_games": sum(bool(value) for value in diagnostics.values()), "rejected": {}}
    jobs, flags = _collect_close_markets(selected, clock, deadline)
    matched = defaultdict(dict)
    with ThreadPoolExecutor(max_workers=12) as pool:
        pending = [(index, kind, market, tip, listed, terminal,
                    [pool.submit(_history_last, option.token_id, tip, listed, clock, deadline) for option in market.options])
                   for index, kind, market, tip, listed, terminal in jobs]
        for index, kind, market, tip, listed, terminal, tasks in pending:
            timestamps = [task.result() for task in tasks]
            if (any(value is None for value in timestamps) or abs(timestamps[0][0] - timestamps[1][0]) > 300
                    or not .90 <= timestamps[0][1] + timestamps[1][1] <= 1.10):
                flags["two_sided_pregame_price_unverified"] += 1
                continue
            primary = next(i for i, option in enumerate(market.options) if option.side in {"home", "over"})
            first, second = timestamps[primary], timestamps[1 - primary]
            result = market.document() | {"p": (first[1] + 1.0 - second[1]) / 2.0,
                "line": market.line or 0.0, "token_observations": [list(first), list(second)],
                "tokens": [market.options[primary].token_id, market.options[1 - primary].token_id],
                "kickoff": utc_iso(tip), "listed_at": utc_iso(listed), "gamma_terminal_prices": terminal[0]}
            if kind in matched[index]:
                matched[index][kind] = None
                flags["ambiguous_same_line"] += 1
            else:
                matched[index][kind] = result
    found = 0
    for index, game in enumerate(selected):
        identity = game["event_id"]
        verified = {kind: market for kind, market in matched[index].items() if market is not None}
        strict = {kind: market for kind, market in verified.items() if market["settlement_scope"] == EXPLICIT_OT}
        diagnostic = {kind: market for kind, market in verified.items() if market["settlement_scope"] == AMBIGUOUS_OT}
        if strict:
            final[identity] = strict
        if diagnostic or (strict and time.monotonic() < deadline):
            diagnostics[identity] = diagnostic
        if strict and identity in diagnostics:
            misses.pop(identity, None)
            found += 1
        else:
            misses[identity] = utc_iso(now)
    return {"attempted_games": len(selected), "found_games": found, "retained_games": len(final),
            "diagnostic_games": sum(bool(value) for value in diagnostics.values()), "rejected": dict(flags)}


def _bbl_text(url, clock, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, None
    request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "Mozilla/5.0"})
    opener = build_opener(ProxyHandler({}), common.pm._NoRedirect())
    limit = 1024 * 1024
    try:
        with opener.open(request, timeout=min(7, remaining)) as response:
            if response.status != 200 or response.geturl() != url:
                return None, None
            raw = response.read(limit + 1)
        if len(raw) <= limit and time.monotonic() < deadline:
            return raw.decode("utf-8"), utc_iso(clock())
    except (OSError, UnicodeError, ValueError):
        pass
    return None, None


def _bbl_calendar(content, year):
    if not content or not content.startswith("BEGIN:VCALENDAR") or "X-WR-TIMEZONE:Europe/Berlin" not in content:
        return None
    content = re.sub(r"\r?\n[ \t]", "", content).replace("\r\n", "\n")
    fixture_rows = []
    seen = set()
    for block in content.split("BEGIN:VEVENT\n")[1:]:
        title = re.search(r"^SUMMARY:easyCredit BBL Spiel (.+)$", block, re.M)
        if title is None:
            continue
        raw_date = re.search(r"^DTSTART(?:;TZID=Europe/Berlin)?:(\d{8}T\d{6})$", block, re.M)
        uid = re.search(r"^UID:([\da-f-]{36})$", block, re.M)
        teams = title[1].split(" vs ")
        if not raw_date or not uid or len(teams) != 2:
            return None
        try:
            local = datetime.strptime(raw_date[1], "%Y%m%dT%H%M%S").replace(tzinfo=BERLIN)
        except ValueError:
            return None
        if not date(year, 7, 1) <= local.date() < date(year + 1, 7, 1):
            continue
        home, away = [_BBL_CALENDAR_NAMES.get(team.strip(), team.strip()) for team in teams]
        key = local.date().isoformat(), home, away
        if key in seen:
            return None
        seen.add(key)
        fixture_rows.append({"calendar_id": uid[1], "kickoff": utc_iso(local), "date": key[0], "home": home, "away": away})
    return fixture_rows or None


def _bbl_results(content, year):
    if (not content or f"URL Source: {BBL_HISTORY_SOURCE}" not in content or f"BBL {year}/{year + 1}" not in content):
        return None
    markets = []
    for line in content.splitlines():
        columns = line.split(" | ")
        if len(columns) != 5:
            continue
        target = re.fullmatch(r"\| \[(.+)\]\((https://www\.betexplorer\.com/basketball/germany/bbl/[a-z0-9-]+/([A-Za-z0-9]{8})/)\)", columns[0])
        finish = re.fullmatch(r"\[(\d{1,3}):(\d{1,3})( AET)?\]\(([^)]+)\)", columns[1])
        day = re.fullmatch(r"(\d\d)\.(\d\d)\. \|", columns[-1])
        if not target or not finish or not day or target[2] != finish[4]:
            continue
        pair = target[1].replace("**", "").split(" - ")
        hs, aws = int(finish[1]), int(finish[2])
        if len(pair) != 2 or min(hs, aws) < 1 or hs == aws:
            continue
        month = int(day[2])
        try:
            calendar_date = date(year + (month < 7), month, int(day[1])).isoformat()
        except ValueError:
            continue
        markets.append({"id": f"betexplorer-{target[3]}", "date": calendar_date, "home": pair[0], "away": pair[1],
                        "home_score": hs, "away_score": aws, "overtime": bool(finish[3]), "result_source": target[2]})
    return markets or None


def _bbl_training(cache, year, now, clock, deadline):
    previous = record(cache.get("bbl_history_sources"))
    fetched = stamp(previous.get("observed_at"))
    if (not fetched or previous.get("year") != year or not timedelta(0) <= now - fetched <= timedelta(hours=3)):
        with ThreadPoolExecutor(max_workers=2) as pool:
            calendar_task = pool.submit(_bbl_text, BBL_CALENDAR, clock, deadline)
            scores_task = pool.submit(_bbl_text, "https://r.jina.ai/" + BBL_HISTORY_SOURCE, clock, deadline)
            calendar, calendar_at = calendar_task.result()
            scores, scores_at = scores_task.result()
        schedule, score_rows = _bbl_calendar(calendar, year), _bbl_results(scores, year)
        if schedule and score_rows:
            previous = {"year": year, "observed_at": utc_iso(clock()), "calendar_at": calendar_at, "scores_at": scores_at,
                        "calendar_sha256": document_digest(schedule), "results_sha256": document_digest(score_rows),
                        "schedule": schedule, "scores": score_rows}
            cache["bbl_history_sources"] = previous
    if previous.get("year") != year:
        previous = {}
    official = record(record(cache.get("europe_finals")).get("bkbbl"))
    archive = [item for item in models.euro_archive("bkbbl") if item["season_year"] == year]
    archive_keys = {(item["schedule_date"], item["home_code"], item["away_code"]) for item in archive}
    final = {(stamp(item["kickoff"]).astimezone(BERLIN).date().isoformat(), item["home_code"], item["away_code"]): item
             for item in official.values()}
    secondary = defaultdict(list)
    for row in previous.get("scores", []):
        secondary[(row["date"], row["home"], row["away"])].append(row)
    past = [item for item in previous.get("schedule", []) if stamp(item["kickoff"]) + models.EURO_OT_RESULT_DELAY <= now]
    supplements, missing, discrepancies = [], [], []
    for item in past:
        key = (item["date"], item["home"], item["away"])
        sources = secondary.get(key, [])
        current = final.get(key)
        if current and len(sources) == 1 and any(current[field] != sources[0][field] for field in ("home_score", "away_score")):
            discrepancies.append({"date": key[0], "home": key[1], "away": key[2],
                                   "official_score": [current["home_score"], current["away_score"]],
                                   "secondary_score": [sources[0]["home_score"], sources[0]["away_score"]]})
        if key in archive_keys or current:
            continue
        if len(sources) != 1:
            missing.append({"date": key[0], "home": key[1], "away": key[2]})
            continue
        source = sources[0]
        supplements.append({**source, "provider": "betexplorer+bbl-calendar", "state": "final", "season_year": year,
                            "kickoff": item["kickoff"], "calendar_id": item["calendar_id"], "schedule_date": key[0],
                            "home_code": key[1], "away_code": key[2], "neutral": False})
    current, checked = stamp(previous.get("observed_at")), clock()
    fresh = current is not None and timedelta(0) <= checked - current <= timedelta(hours=24)
    summary = {"complete": bool(fresh and past and not missing), "season_year": year, "observed_at": utc_iso(checked),
               "calendar_due": len(past), "covered": len(past) - len(missing), "secondary_finals": len(supplements),
               "missing": missing, "recent_official_score_discrepancies": discrepancies,
               "schedule_source": BBL_CALENDAR, "supplemental_scores_source": BBL_HISTORY_SOURCE,
               "source_observed_at": previous.get("observed_at"), "calendar_sha256": previous.get("calendar_sha256"),
               "results_sha256": previous.get("results_sha256")}
    cache["bbl_training"] = summary | {"fixtures": supplements}
    return summary


def _europe_research(cache, now, clock, deadline):
    configs, historical, _ = models.euro_parameters()
    year = int(models.nba_season(now).split("-")[0])
    boards = cache.setdefault("boards", {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {league: pool.submit(_provider_board, league, year, clock, deadline)
                 for league in sorted(INDEPENDENT_LEAGUES - {"nba"})}
        unavailable = []
        for league, task in tasks.items():
            board = task.result()
            if not board:
                unavailable.append(league)
            else:
                boards[league] = board
                _retain_finals(cache, league, board, year)
    bbl_history = _bbl_training(cache, year, now, clock, deadline)
    result = {}
    for league, name in models.EURO_LEAGUES.items():
        live = record(boards.get(league))
        strict = {}
        for kind, market in record(historical[name].get("clob")).items():
            all_matches = record(market.get("all"))
            strict[kind] = all_matches.get("unique_games", all_matches.get("rows", 0))
        result[league] = {"status": "forward_research" if league in INDEPENDENT_LEAGUES else "source_gap",
                          "model_version": models.EURO_VERSION, "historical_holdout_games": configs[name]["holdout"]["games"],
                          "historical_strict_markets": strict,
                          "live_schedule_state": ("available" if live and league not in unavailable else "unavailable"),
                          "current_verified_final_count": sum(item["state"] == "final" and stamp(item["kickoff"]) < now for item in live.get("fixtures", [])),
                          "observed_at": live.get("observed_at"), "next_evidence": "same_line_two_sided_pregame_forward"}
        if league == "bkbbl":
            recent = record(record(cache.get("europe_finals")).get(league))
            years = [item for item in models.euro_archive("bkbbl") if item["season_year"] == year]
            result[league].update({"history_state": "complete" if bbl_history["complete"] else "partial",
                                   "frozen_results_through": max((item["schedule_date"] for item in years), default=None),
                                   "live_final_starts_at": min((item["kickoff"] for item in recent.values()), default=None),
                                   "retained_verified_final_count": len(recent), "history_coverage": bbl_history})
    return result, unavailable


def refresh_research(now: datetime, source_cache: dict | None = None) -> tuple[dict, dict]:
    """Refresh past-only NBA gates and existing European forward research status."""
    if now.utcoffset() is None:
        raise ValueError("basketball research requires an aware clock")
    now = now.astimezone(UTC)
    clock = common._clock(now)
    deadline = time.monotonic() + 44
    cache = dict(source_cache or {})
    season = models.nba_season(now)
    coverage = _latest_finals(cache, season, now, clock, min(deadline, time.monotonic() + 29))
    games = _nba_games(cache, now)
    frozen, frozen_pm = models.nba_archive(season)
    frozen_keys = {game["event_id"] for game in frozen}
    strict = {key: {kind: market for kind, market in quotes.items() if record(market.get("settlement")).get("scope") == "explicit-full-game-including-OT"}
              for key, quotes in frozen_pm.items()}
    diagnostic = {key: {kind: market for kind, market in quotes.items() if record(market.get("settlement")).get("scope") == "NBA-final-score-OT-not-explicit"}
                  for key, quotes in frozen_pm.items()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        europe_task = pool.submit(_europe_research, cache, now, clock, deadline)
        pm_task = pool.submit(_research_pm, cache, [row for row in games if row["event_id"] not in frozen_keys], now, clock, deadline)
        coverage["polymarket"] = pm_task.result()
        europe, missing_euro = europe_task.result()
    strict.update(record(cache.get("nba_strict_pm")))
    diagnostic.update(record(cache.get("nba_diagnostic_pm")))
    try:
        snapshot = models.nba_snapshot(games, season, now)
        nba, predictions = models.nba_retest(games, snapshot, strict, diagnostic, record(cache.get("nba_predictions")))
    except (ValueError, KeyError, espn_nba.EspnNbaError):
        coverage["nba_snapshot_invalid"] = True
        nba = {"model_version": models.NBA_VERSION, "required_games": models.NBA_RETEST_GAMES,
               "completed_games": len(games), "retest_ready": False}
    else:
        cache["nba_predictions"] = predictions
    nba_ratings = {"model_version": models.NBA_RATINGS_VERSION, "model_status": "input_unavailable"}
    if not coverage.get("nba_snapshot_invalid"):
        try:
            history = models.nba_ratings_history(games, season, cache.get("nba_prior_final_games"))
            nba_ratings = models.nba_ratings_retest(games, snapshot, history, strict, diagnostic, now)
        except (OSError, KeyError, TypeError, ValueError, espn_nba.EspnNbaError):
            coverage["nba_ratings_input_unavailable"] = True
    coverage["europe_sources_unavailable"] = missing_euro
    if coverage.get("nba_snapshot_invalid") or not coverage["overview_ready"]:
        status = "partial"
    else:
        status = "evaluated" if nba.get("retest_ready") or nba_ratings.get("retest_ready") else "waiting"
    report = {"policy_version": POLICY_VERSION, "observed_at": utc_iso(clock()), "status": status,
              "nba_season": season, "nba": nba, "nba_ratings": nba_ratings, "europe": europe, "coverage": coverage}
    admitted = _approved_nba_families(report, clock())
    report["approved_families_by_model"] = {version: sorted(families) for version, families in admitted.items()}
    report["approved_families"] = sorted(set().union(*admitted.values()))
    if models.NBA_RATINGS_VERSION in admitted:
        nba_ratings["model_status"] = "paper_gate_passed"
    cache["research_report"] = report
    return report, cache
