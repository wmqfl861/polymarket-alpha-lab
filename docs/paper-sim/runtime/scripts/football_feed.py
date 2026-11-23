"""Public football fixture discovery and pre-match paper review."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import math
import re
import time
import unicodedata
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

from polymarket_alpha_lab.papersim import fees, fotmob, gates, soccer_model
from polymarket_alpha_lab.papersim.adapters import polymarket as pm
from polymarket_alpha_lab.papersim.pipeline import MODEL_VERSION_SOCCER


BEIJING = ZoneInfo("Asia/Shanghai")
SOCCER_TAG = "100350"
DISCOVERY_TAGS = {"soccer": SOCCER_TAG, "games": "100639"}
PAGE_SIZE = 30
MAX_PAGES = 105
GAMMA_SCAN_SECONDS = 175
GAMMA_QUERY_SPAN = timedelta(hours=48)
BOOK_AGE = timedelta(minutes=5)
CONFIRMED_BASELINE = "confirmed_review_context"
CONFIRMED_OPENED_AT = "first_confirmed_lineup_at"
INITIAL_CONFIRMED_LINEUP = "first_confirmed_lineup"
CONFIRMED_LINEUPS = frozenset({"standard", "confirmed"})
LINEUP_BASELINE_LEAD = timedelta(minutes=75)
LINEUP_BASELINE_CUTOFF = timedelta(minutes=30)
MODELED_LEAGUES = {
    "epl": "英超", "lal": "西甲", "sea": "意甲", "bun": "德甲", "fl1": "法甲",
    "elc": "英冠", "ere": "荷甲",
}
LEAGUE_NAMES = MODELED_LEAGUES | {
    "ucl": "欧冠", "uel": "欧联杯", "uecl": "欧协联", "uwcl": "女足欧冠", "wsl": "英格兰女超",
    "mls": "美职联", "nwsl": "美国女足联赛", "bra": "巴甲", "arg": "阿甲", "mex": "墨超",
    "acle": "亚冠精英联赛", "afc": "亚洲杯", "copa-libertadores": "南美解放者杯",
    "es2": "西乙", "fr2": "法乙", "bl2": "德乙", "itsb": "意乙", "pt1": "葡超",
    "tur": "土超", "bel1": "比甲", "sco": "苏超", "jpn": "日职联", "kor": "韩K联",
}
HIGHLIGHT_LEAGUES = frozenset({"ucl", "uwcl", "uel", "fifwc", "wc", "wwc", "cwc"})
_FIXTURE_SLUG = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}-\d{2})(?:-|$)")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_TEAM_TITLE = re.compile(r"\s+vs?\.?\s+", re.I)
_DROP_NAME_PARTS = frozenset({"fc", "cf", "afc", "ac", "bc", "us", "sc", "sv", "fsv", "rc", "ca",
                              "fk", "sk", "sco", "1909", "1901", "1", "de", "del", "di", "and"})
_NAME_ALIASES = {
    "az": "az alkmaar", "feyenoord rotterdam": "feyenoord", "psv": "psv eindhoven",
    "nec": "nec nijmegen", "sc cambuur leeuwarden": "cambuur", "cambuur leeuwarden": "cambuur",
    "telstar 1963": "telstar", "fort sittard": "fortuna sittard", "ajax amsterdam": "ajax",
    "brighton hove albion": "brighton", "wolverhampton wanderers": "wolverhampton", "wolves": "wolverhampton",
    "hull city": "hull", "coventry city": "coventry", "ipswich town": "ipswich", "leeds united": "leeds",
    "tottenham hotspur": "tottenham", "man utd": "manchester united", "man city": "manchester city",
    "newcastle united": "newcastle", "west ham united": "west ham", "nottingham forest": "nottingham forest",
    "deportivo alaves": "alaves", "deportivo coruna": "deportivo la coruna", "deportivo a coruna": "deportivo la coruna",
    "real oviedo": "oviedo", "real sociedad futbol": "real sociedad", "club atletico madrid": "atletico madrid",
    "real racing club": "racing santander", "athletic bilbao": "athletic club", "athletic club bilbao": "athletic club",
    "real betis balompie": "real betis",
    "bayern munchen": "bayern munich", "bayer 04 leverkusen": "bayer leverkusen", "rb leipzig": "rasenballsport leipzig",
    "borussia monchengladbach": "borussia m gladbach", "gladbach": "borussia m gladbach", "koln": "cologne",
    "hamburger": "hamburger", "hamburg": "hamburger", "tsg 1899 hoffenheim": "hoffenheim",
    "paderborn 07": "paderborn", "07 elversberg": "elversberg",
    "internazionale milano": "inter", "inter milan": "inter", "internazionale": "inter", "milan": "milan",
    "udinese calcio": "udinese", "sassuolo calcio": "sassuolo", "genoa cfc": "genoa", "frosinone calcio": "frosinone",
    "parma calcio 1913": "parma", "hellas verona": "verona", "stade rennais": "rennes", "stade rennais fc": "rennes",
    "olympique marseille": "marseille", "olympique lyonnais": "lyon", "paris saint germain": "psg",
    "paris sg": "psg", "stade brestois 29": "brest", "stade brestois": "brest", "ogc nice": "nice",
    "angers sco": "angers", "as monaco": "monaco", "losc lille": "lille", "stade reims": "reims",
    "es troyes": "troyes",
    "willem ii tilburg": "willem ii",
}


def stamp(value, *, naive_utc=False):
    if not isinstance(value, str) or len(value) < 16:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC) if naive_utc else None
    return parsed.astimezone(UTC)


def utc_iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def document_digest(value):
    raw = json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def record(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def numeric_id(value):
    if type(value) not in (int, str):
        return None
    return str(value) if re.fullmatch(r"[0-9]{1,78}", str(value)) else None


def number(value):
    if type(value) not in (int, float, str, Decimal):
        return None
    try:
        val = float(value)
    except (ValueError, OverflowError):
        return None
    return val if math.isfinite(val) else None


def clean_label(value, limit=90):
    if not isinstance(value, str):
        return ""
    return " ".join(part for part in value.split() if part.isprintable())[:limit].strip()


def team_key(value):
    normalized = unicodedata.normalize("NFKD", clean_label(value)).lower()
    ascii_name = "".join(letter for letter in normalized if not unicodedata.combining(letter))
    words = re.findall(r"[a-z0-9]+", ascii_name)
    name = " ".join(word for word in words if word not in _DROP_NAME_PARTS)
    return _NAME_ALIASES.get(name, name)


def same_team(left, right):
    left_key, right_key = team_key(left), team_key(right)
    return bool(left_key and right_key and left_key == right_key)


def _clock(start):
    from_tick = time.monotonic()
    return lambda: start + timedelta(seconds=time.monotonic() - from_tick)


def public_budget(deadline, requests=1, limit=4 * 1024**2, total=None):
    return pm.FetchBudget(max_requests=requests, max_response_bytes=limit,
                          max_total_response_bytes=total or limit,
                          deadline_monotonic=deadline, request_timeout_seconds=7)


def _public(url, budget, clock, opener=None):
    raw, fetched = pm._fetch_bytes(url, opener=opener or pm.BoundedPublicOpener(), budget=budget, now_utc=clock)
    return pm._decode_json(raw), fetched


def _gamma_page(lane, tag, lower, upper, deadline, clock):
    budget = public_budget(deadline, requests=MAX_PAGES + 8, limit=2 * 1024**2, total=100 * 1024**2)
    events = {}
    pages, overlaps = 0, 0
    failure = "page_cap"
    for offset in range(0, PAGE_SIZE * MAX_PAGES, PAGE_SIZE):
        query = urlencode({
            "tag_id": tag, "active": "true", "closed": "false", "include_markets": "false",
            "order": "endDate", "ascending": "true", "limit": PAGE_SIZE, "offset": offset,
            "end_date_min": utc_iso(lower), "end_date_max": utc_iso(upper),
        })
        payload = None
        read_error = "upstream_unavailable"
        for _ in range(2):
            try:
                payload, _ = _public(pm.GAMMA_EVENTS_URL + "?" + query, budget, clock)
                break
            except Exception as error:
                if str(error) in pm.PUBLIC_READ_REASONS:
                    read_error = str(error)
        if not isinstance(payload, list) or len(payload) > PAGE_SIZE:
            failure = read_error if payload is None else "invalid_event_page"
            break
        pages += 1
        by_id = {str(item["id"]): item for item in rows(payload) if numeric_id(item.get("id"))}
        if len(by_id) != len(payload):
            failure = "invalid_event_page"
            break
        if by_id and not by_id.keys() - events.keys():
            failure = "repeated_page"
            break
        overlaps += len(by_id.keys() & events.keys())
        events.update(by_id)
        if len(payload) < PAGE_SIZE:
            failure = "overlap" if overlaps else None
            break
    result = {"pages": pages, "rows": len(events), "partial": failure is not None, "error": failure,
              "overlaps": overlaps, "tag": tag}
    return lane, events, result


def _registry(deadline, clock):
    try:
        payload, _ = _public(pm.GAMMA_BASE_URL + "/sports", public_budget(deadline, limit=2 * 1024**2), clock)
        if not isinstance(payload, list) or not payload:
            raise ValueError("empty sports registry")
        return {item["sport"]: item for item in rows(payload) if isinstance(item.get("sport"), str)}, None
    except Exception:
        return {}, "upstream_unavailable"


def _tags(value):
    if isinstance(value, str):
        return set(value.split(","))
    return {str(item.get("id")) for item in rows(value)}


def _soccer_registries(registry):
    soccer = {code: item for code, item in registry.items() if SOCCER_TAG in _tags(item.get("tags"))}
    by_series = {str(item["series"]): code for code, item in soccer.items() if item.get("series") is not None}
    counts = Counter(str(item.get("primaryTagId")) for item in soccer.values())
    by_tag = {str(item["primaryTagId"]): code for code, item in soccer.items()
              if counts[str(item.get("primaryTagId"))] == 1 and item.get("primaryTagId") is not None}
    return soccer, by_series, by_tag


def _competition(event, slug, registries, soccer_lane):
    soccer, by_series, by_tag = registries
    embedded = record(event.get("sport"))
    if SOCCER_TAG in _tags(embedded.get("tags")) and embedded.get("sport"):
        return embedded["sport"], embedded
    tags = _tags(event.get("tags"))
    series = {by_series[str(item.get("id"))] for item in rows(event.get("series")) if str(item.get("id")) in by_series}
    tagged = {by_tag[tag] for tag in tags if tag in by_tag}
    candidates = series or tagged
    prefix = slug.split("-", 1)[0]
    if prefix in candidates or prefix in soccer or prefix in MODELED_LEAGUES:
        return prefix, soccer.get(prefix, {})
    if len(candidates) == 1:
        code = next(iter(candidates))
        return code, soccer[code]
    if SOCCER_TAG in tags or soccer_lane:
        return prefix, embedded if embedded.get("sport") == prefix else {}
    return None, {}


def _gamma_teams(event):
    paired = defaultdict(list)
    for team in rows(event.get("teams")):
        if team.get("ordering") in ("home", "away"):
            paired[team["ordering"]].append(team)
    if len(paired["home"]) == len(paired["away"]) == 1:
        return paired["home"][0], paired["away"][0]
    title = clean_label(event.get("title"), 200).split(" - ", 1)[0]
    labels = _TEAM_TITLE.split(title, maxsplit=1)
    return tuple({"name": item} for item in labels) if len(labels) == 2 else ({}, {})


def _gamma_state(event):
    if event.get("cancelled") is True or event.get("canceled") is True:
        return "cancelled"
    if event.get("postponed") is True:
        return "postponed"
    if event.get("live") is True or event.get("ended") is True:
        return "started"
    return "scheduled"


def _event_fixtures(events, soccer_ids, now, end, registry):
    families = defaultdict(list)
    rejected = 0
    registries = _soccer_registries(registry)
    for event_id, event in events.items():
        kickoff = stamp(event.get("startTime"))
        if kickoff is None or not now <= kickoff < end:
            continue
        slug = clean_label(event.get("slug"), 180)
        if not _SLUG.fullmatch(slug):
            continue
        league, entry = _competition(event, slug, registries, event_id in soccer_ids)
        if not isinstance(league, str) or not _SLUG.fullmatch(league):
            continue
        match = _FIXTURE_SLUG.match(slug)
        game_id, parent = numeric_id(event.get("gameId")), numeric_id(event.get("parentEventId"))
        home, away = _gamma_teams(event)
        if (match is None and game_id is None and parent is None) or not (home.get("name") and away.get("name")):
            rejected += 1
            continue
        base = match[1] if match else slug
        family_key = (league, base if match else game_id or parent)
        families[family_key].append({
            "id": event_id, "slug": slug, "base": base, "kickoff": kickoff, "home": home, "away": away,
            "game_id": game_id, "parent_id": parent, "state": _gamma_state(event),
            "volume": number(event.get("volume24hr")) or number(event.get("volume")) or 0.0,
            "featured": event.get("featured") is True, "league_name": clean_label(entry.get("name")),
        })

    fixtures = {}
    for (league, _), listings in families.items():
        # A primary/more listing has the authoritative clock ahead of peripheral props.
        listings.sort(key=lambda row: (row["slug"] != row["base"], row["slug"] != row["base"] + "-more-markets",
                                       row["home"].get("id") is None, len(row["slug"]), row["id"]))
        primary = listings[0]
        game_ids = {item["game_id"] for item in listings if item["game_id"]}
        if len(game_ids) > 1:
            rejected += 1
            continue
        game_id = next(iter(game_ids), None)
        base = primary["base"]
        parent = next((item["parent_id"] for item in listings if item["parent_id"]), None)
        root_id = primary["id"] if primary["slug"] == base else parent
        identity = f"gamma:{league}:{game_id or root_id or base}"
        home, away = primary["home"], primary["away"]
        home_label, away_label = clean_label(home.get("name")), clean_label(away.get("name"))
        if not home_label or not away_label or home_label == away_label:
            rejected += 1
            continue
        state = primary["state"]
        related = [row for row in listings if row["kickoff"] == primary["kickoff"]
                   and _same_gamma_team(row["home"], home) and _same_gamma_team(row["away"], away)]
        if not related:
            rejected += 1
            continue
        traffic = sum(item["volume"] for item in related if item["slug"] in (base, base + "-more-markets"))
        highlight = league in HIGHLIGHT_LEAGUES or any(item["featured"] for item in related) or traffic >= 150_000
        supported = league in MODELED_LEAGUES
        fixture = {
            "id": identity, "kickoff": utc_iso(primary["kickoff"]), "home": home_label, "away": away_label,
            "home_id": numeric_id(home.get("id")), "away_id": numeric_id(away.get("id")),
            "home_alias": clean_label(home.get("alias")), "away_alias": clean_label(away.get("alias")),
            "league": league, "league_name": LEAGUE_NAMES.get(league, primary["league_name"] or league),
            "watch": state == "scheduled" and (highlight or supported), "highlight": highlight,
            "reason": "联赛模型复核" if supported else "重点比赛，联赛未纳入模型" if highlight else "联赛未纳入模型，仅观察",
            "state": state, "base_slug": base, "game_id": game_id, "root_event_id": root_id,
            "events": [{"id": row["id"], "slug": row["slug"]} for row in related],
        }
        existing = fixtures.get(identity)
        if existing and (fixture["home_id"], fixture["away_id"]) != (existing["home_id"], existing["away_id"]):
            rejected += 1
            continue
        if existing:
            choice = min((existing, fixture), key=lambda row: (row["root_event_id"] is None, row["kickoff"]))
            if existing["kickoff"] == fixture["kickoff"]:
                choice["events"] = list({item["id"]: item for item in existing["events"] + fixture["events"]}.values())
            fixtures[identity] = choice
        else:
            fixtures[identity] = fixture
    return sorted(fixtures.values(), key=lambda item: (item["kickoff"], item["id"])), rejected


def _same_gamma_team(left, right):
    left_id, right_id = numeric_id(left.get("id")), numeric_id(right.get("id"))
    if left_id and right_id:
        return left_id == right_id
    return left.get("name") == right.get("name") or same_team(left.get("name"), right.get("name"))


def discover(now: datetime, hours: int = 72, source_cache: dict | None = None) -> tuple[list[dict], dict]:
    """Discover active Polymarket soccer fixtures and refresh point-in-time model inputs."""
    if now.utcoffset() is None or type(hours) is not int or not 1 <= hours <= 168:
        raise ValueError("discovery requires an aware clock and an hourly horizon from 1 to 168")
    now = now.astimezone(UTC)
    end = now + timedelta(hours=hours)
    clock = _clock(now)
    deadline = time.monotonic() + GAMMA_SCAN_SECONDS
    lower = now.replace(microsecond=0) - timedelta(hours=10)
    upper = end.replace(microsecond=0) + timedelta(hours=36)
    windows = []
    while lower < upper:
        next_boundary = min(lower + GAMMA_QUERY_SPAN, upper)
        windows.append((lower, next_boundary))
        lower = next_boundary
    with ThreadPoolExecutor(max_workers=7) as pool:
        lookup = pool.submit(_registry, deadline, clock)
        feeds = [pool.submit(_gamma_page, lane, tag, begin, finish, deadline, clock)
                 for begin, finish in windows for lane, tag in DISCOVERY_TAGS.items()]
        registry, registry_error = lookup.result()
        finished_lanes = defaultdict(list)
        for job in feeds:
            lane, events, stats = job.result()
            finished_lanes[lane].append((events, stats))
    lanes = {}
    for lane, segments in finished_lanes.items():
        available = {key: item for events, _ in segments for key, item in events.items()}
        failures = [item["error"] for _, item in segments if item["partial"]]
        lanes[lane] = available, {
            "tag": DISCOVERY_TAGS[lane], "pages": sum(item["pages"] for _, item in segments),
            "rows": len(available), "partial": bool(failures), "errors": failures,
            "segments": [item for _, item in segments],
        }
    soccer_events = lanes["soccer"][0]
    events = lanes["games"][0] | soccer_events
    fixtures, rejected = _event_fixtures(events, soccer_events.keys(), now, end, registry)
    from football_model import refresh_models

    modeled, source_cache = refresh_models(fixtures, clock, source_cache)
    stats = {lane: stats for lane, (_, stats) in lanes.items()}
    stats["registry"] = {"rows": len(registry), "error": registry_error}
    return fixtures, {
        "partial": registry_error is not None or any(item.get("partial", False) for item in stats.values()),
        "window_start": utc_iso(now), "window_end": utc_iso(end), "observed_at": utc_iso(clock()),
        "fixtures": len(fixtures), "leagues": len({item["league"] for item in fixtures}),
        "supported": sum(item["league"] in MODELED_LEAGUES for item in fixtures),
        "modeled": sum("model" in item for item in fixtures), "matched": sum("fixture_id" in item for item in fixtures),
        "model_partial": any(item["league"] in MODELED_LEAGUES and item["state"] == "scheduled" and "model" not in item
                             for item in fixtures),
        "pages": sum(item.get("pages", 0) for item in stats.values()), "events": len(events), "rejected": rejected,
        "sources": stats | {"fotmob": modeled}, "source_cache": source_cache,
    }


_CONDITION_ID = re.compile(r"0x[0-9a-fA-F]{64}")
_REGULATION = "first 90 minutes of regular play plus stoppage time"
_HALF_BALL = r"\d{1,2}pt5"
_STAGE_WINDOWS = {"t90": (90, 30), "t30": (30, 15)}
_LATE = timedelta(minutes=10)
_MAX_REVIEW_SECONDS = 22
_BATCH_SIZE = 65
_MAX_BATCHES = 4


@dataclass(frozen=True)
class Selection:
    label: str
    outcome: str
    token_id: str
    side: str
    reference: float | None


@dataclass(frozen=True)
class Market:
    event_slug: str
    raw: dict
    id: str
    condition_id: str
    kind: str
    line: float | None
    team_side: str | None
    options: tuple[Selection, Selection]


def _contract_pairs(market):
    outcomes = pm._maybe_json_list(market.get("outcomes"))
    tokens = pm._maybe_json_list(market.get("clobTokenIds"))
    if (outcomes is None or tokens is None or len(outcomes) != 2 or len(tokens) != 2
            or not all(isinstance(item, str) for item in outcomes + tokens)
            or outcomes[0] == outcomes[1] or tokens[0] == tokens[1]
            or not all(numeric_id(item) for item in tokens)):
        raise ValueError("invalid binary outcomes")
    return tuple(zip(outcomes, tokens))


def _open_market(market):
    return (market.get("active") is True and market.get("closed") is False
            and market.get("archived") is not True and market.get("acceptingOrders") is True
            and market.get("enableOrderBook") is True and _gamma_state(market) == "scheduled")


def _side_from_group(fixture, group):
    if re.match(r"^draw\b", group, re.I):
        return "draw"
    found = [side for side in ("home", "away")
             if any(same_team(group, item) for item in (fixture.get(side), fixture.get(f"{side}_alias")))]
    return found[0] if len(found) == 1 else None


def _market_options(fixture, market, suffix, pairs, references):
    line = number(market.get("line"))
    kind = str(market.get("sportsMarketType") or "")
    group = clean_label(market.get("groupItemTitle"))
    labels = {"home": "主队胜", "away": "客队胜", "draw": "平局"}
    outcomes = {option.casefold() for option, _ in pairs}
    sides = {}
    team_side = None
    if kind == "moneyline" and re.fullmatch(r"(?:draw|[a-z0-9]+)", suffix) and outcomes == {"yes", "no"}:
        selected = _side_from_group(fixture, group)
        if selected is None or line is not None:
            return None
        kind = "match_winner"
        sides = {"yes": (selected, labels[selected]), "no": (selected + "_no", "非" + labels[selected])}
    elif kind in {"btts", "both_teams_to_score", "soccer_both_teams_to_score"} and suffix in {"btts", "both-teams-to-score"}:
        if line is not None or outcomes != {"yes", "no"}:
            return None
        kind = "btts"
        sides = {"yes": ("yes", "双方进球"), "no": ("no", "至少一队零进球")}
    else:
        full_total = kind == "totals" and re.fullmatch(r"total-(" + _HALF_BALL + r")", suffix)
        team_total = kind in {"soccer_team_totals", "team_totals"} and re.fullmatch(
            r"team-total-(home|away)-(" + _HALF_BALL + r")", suffix)
        match = full_total or team_total
        if not match or line is None or outcomes != {"over", "under"}:
            return None
        listed = float(match.groups()[-1].replace("pt", "."))
        if line != listed or not 0.5 <= line <= 19.5:
            return None
        threshold = re.search(r"\bO/U\s+([0-9]+(?:\.[0-9]+)?)\b", group, re.I)
        if threshold is None or number(threshold[1]) != line:
            return None
        if team_total:
            kind, team_side = "team_total", team_total[1]
            if _side_from_group(fixture, group[:threshold.start()].strip()) != team_side:
                return None
            subject = "主队进球" if team_side == "home" else "客队进球"
        else:
            kind, subject = "total", "总进球"
        sides = {"over": ("over", f"{subject}大于{line:g}"), "under": ("under", f"{subject}小于{line:g}")}
    options = []
    for (outcome, token), reference in zip(pairs, references):
        side, label = sides[outcome.casefold()]
        options.append(Selection(label, outcome, token, side, reference))
    return kind, line, team_side, tuple(options)


def _event_markets(fixture, event, kickoff):
    found = []
    base = fixture["base_slug"]
    for raw in rows(event.get("markets")):
        if not _open_market(raw) or stamp(raw.get("gameStartTime")) != kickoff:
            continue
        market_id, condition = numeric_id(raw.get("id")), raw.get("conditionId")
        slug, question = raw.get("slug"), clean_label(raw.get("question"), 200)
        if (not market_id or not isinstance(condition, str) or not _CONDITION_ID.fullmatch(condition)
                or not isinstance(slug, str) or not _SLUG.fullmatch(slug) or not slug.startswith(base + "-") or not question):
            continue
        try:
            pairs = _contract_pairs(raw)
        except (ValueError, TypeError):
            continue
        try:
            prices = [number(item) for item in pm._maybe_json_list(raw.get("outcomePrices")) or []]
        except (ValueError, TypeError):
            prices = []
        references = [price if price is not None and 0 < price < 1 else None for price in prices] if len(prices) == 2 else [None, None]
        parsed = _market_options(fixture, raw, slug.removeprefix(base + "-"), pairs, references)
        if parsed:
            kind, line, team_side, options = parsed
            found.append(Market(event["slug"], raw, market_id, condition, kind, line, team_side, options))
    return found


def _gamma_event_refs(fixture, expanded):
    base = fixture.get("base_slug")
    if not isinstance(base, str) or not _SLUG.fullmatch(base):
        return []
    by_slug = {item.get("slug"): item for item in rows(fixture.get("events")) if isinstance(item.get("slug"), str)}
    main = (base, numeric_id(record(by_slug.get(base)).get("id")) or numeric_id(fixture.get("root_event_id")))
    more = (base + "-more-markets", numeric_id(record(by_slug.get(base + "-more-markets")).get("id")))
    return [main, more] if expanded or more[1] is not None else [main]


def _refresh_event(fixture, ref, clock, deadline):
    expected_slug, event_id = ref
    path = "/events/" + event_id if event_id else "/events/slug/" + expected_slug
    try:
        raw, _ = _public(pm.GAMMA_BASE_URL + path, public_budget(deadline), clock)
    except Exception:
        return None
    event = record(raw)
    if (event_id is not None and numeric_id(event.get("id")) != event_id) or event.get("slug") != expected_slug:
        return None
    actual_game_id = numeric_id(event.get("gameId"))
    if fixture.get("game_id") and actual_game_id and actual_game_id != fixture["game_id"]:
        return None
    if not all(_same_gamma_team(actual, {"id": fixture.get(f"{side}_id"), "name": fixture.get(side)})
               for actual, side in zip(_gamma_teams(event), ("home", "away"))):
        return None
    return event


def _lineup_evidence(payload, fixture, fetched, source):
    lineup = record(record(payload.get("content")).get("lineup"))
    lineup_type = lineup.get("lineupType")
    sides = []
    if numeric_id(lineup.get("matchId")) == fixture["fixture_id"]:
        for side in ("home", "away"):
            team = record(lineup.get(f"{side}Team"))
            if numeric_id(team.get("id")) != fixture[f"fotmob_{side}_id"]:
                return None
            listed = team.get("starters")
            players = rows(listed)
            starters = [numeric_id(item.get("id")) for item in players]
            keepers = [numeric_id(item.get("id")) for item in players if numeric_id(item.get("positionId")) == "11"]
            unavailable = team.get("unavailable")
            complete_absences = isinstance(unavailable, list)
            absent = {}
            for player in rows(unavailable):
                player_id = numeric_id(player.get("id"))
                status = record(player.get("unavailability"))
                updated = stamp(status.get("lastUpdated"))
                cause = status.get("type")
                if (not player_id or player_id in absent or cause not in {"injury", "suspension", "illness", "internationalDuty"}
                        or (status.get("lastUpdated") and (updated is None or updated > fetched))):
                    complete_absences = False
                if player_id and cause in {"injury", "suspension", "illness", "internationalDuty"} and (updated is None or updated <= fetched):
                    absent[player_id] = cause
            sides.append({"formation": clean_label(team.get("formation"), 20), "absences": absent,
                          "absences_complete": complete_absences and len(absent) == len(unavailable),
                          "goalkeeper": keepers[0] if len(keepers) == 1 else None,
                          "starters_unpublished": listed is None or listed == [],
                          "starters": sorted(starters) if len(starters) == 11 and None not in starters and len(set(starters)) == 11 else []})
    summary = {"fixture_id": fixture["fixture_id"], "team_ids": [fixture["fotmob_home_id"], fixture["fotmob_away_id"]],
               "kickoff": fixture["kickoff"], "observed_at": utc_iso(fetched), "source": source,
               "lineup_type": lineup_type if lineup_type in {"standard", "confirmed", "predicted"} else None,
               "lineup_provider": clean_label(lineup.get("source"), 80),
               "lineups": sides}
    return summary | {"sha256": document_digest(summary)}


def _fotmob_evidence(fixture, clock, deadline):
    from football_model import _prematch_identity, details_url

    match_id = numeric_id(fixture.get("fixture_id"))
    expected = (match_id, fixture.get("fotmob_league_id"), fixture.get("fotmob_home_id"), fixture.get("fotmob_away_id"))
    if not all(expected):
        return "independent_fixture_missing", None, None
    try:
        payload = fotmob.fetch_match_details(match_id, allow_public_fetch=True, timeout=7,
                                             budget=public_budget(deadline, requests=2))
    except Exception:
        return "fotmob_unavailable", None, None
    fetched = clock()
    general, status, identity = _prematch_identity(payload)
    actual = stamp(status.get("utcTime"))
    if identity != expected or actual is None or stamp(general.get("matchTimeUTCDate")) != actual:
        return "independent_fixture_changed", None, None
    if fotmob._is_cancelled({"status": status}):
        return "cancelled", None, None
    original = stamp(fixture.get("kickoff"))
    if status.get("postponed") is True or actual != original:
        return "postponed", None, utc_iso(actual) if actual != original and actual > fetched else None
    if any(status.get(key) is True for key in ("started", "finished")) or payload.get("ongoing") is True:
        return "started", None, None
    if not all(status.get(key) is False for key in ("started", "finished", "cancelled")) or payload.get("ongoing") is not False:
        return "fotmob_unavailable", None, None
    return "verified", _lineup_evidence(payload, fixture, fetched, details_url(match_id)), None


def _complete_lineups(context):
    lineups = rows(context.get("lineups"))
    return (context.get("lineup_type") in {"predicted", "confirmed", "standard"} and len(lineups) == 2
            and all(isinstance(item.get("starters"), list) and len(item["starters"]) == 11
                    and all(numeric_id(player) for player in item["starters"]) and len(set(item["starters"])) == 11
                    and bool(clean_label(item.get("formation"), 20)) and isinstance(item.get("absences"), dict)
                    for item in lineups))


def confirmed_baseline(fixture, context):
    """Validate a complete, independently fetched lineup captured before T−30."""
    context = record(context)
    kickoff, observed = stamp(fixture.get("kickoff")), stamp(context.get("observed_at"))
    return (kickoff is not None and observed is not None
            and kickoff - LINEUP_BASELINE_LEAD <= observed < kickoff - LINEUP_BASELINE_CUTOFF
            and context.get("lineup_type") in CONFIRMED_LINEUPS and _complete_lineups(context)
            and _same_lineup_fixture(fixture, context))


def published_lineup_time(fixture, evidence):
    from football_model import details_url

    evidence = record(evidence)
    if (confirmed_baseline(fixture, evidence) and evidence.get("source") == details_url(fixture["fixture_id"])
            and evidence.get("sha256") == document_digest({k: v for k, v in evidence.items() if k != "sha256"})):
        return stamp(evidence["observed_at"])
    return None


def confirmed_personnel_digest(evidence):
    """Check the published XI and absence lists and hash the data that can change a decision."""
    from football_lineup_history import _complete

    evidence = record(evidence)
    sides = rows(evidence.get("lineups"))
    provider = clean_label(evidence.get("lineup_provider"))
    if (not provider or evidence.get("lineup_type") not in CONFIRMED_LINEUPS or len(sides) != 2
            or not all(_complete(side) and side.get("absences_complete") is True
                       and isinstance(side.get("absences"), dict)
                       and not set(side["starters"]).intersection(side["absences"]) for side in sides)
            or set(sides[0]["starters"]).intersection(sides[1]["starters"])):
        return None
    return document_digest({
        "provider": provider,
        "lineups": [{"formation": side["formation"], "starters": sorted(side["starters"]),
                     "goalkeeper": side["goalkeeper"], "absences": side["absences"]} for side in sides],
    })


def confirmed_review_opening(fixture):
    """Return the first verified lineup time for this domestic fixture revision."""
    if fixture.get("sport", "football") != "football" or fixture.get("league") not in MODELED_LEAGUES:
        return None
    latest = published_lineup_time(fixture, fixture.get(CONFIRMED_BASELINE))
    if latest is None:
        return None
    initial = published_lineup_time(fixture, fixture.get(INITIAL_CONFIRMED_LINEUP))
    if initial is not None:
        return initial if initial <= latest else None
    return latest


def collect_confirmed_baselines(claims, now):
    """Collect official lineups for claims bound to stored fixture revisions."""
    clock = _clock(now)
    deadline = time.monotonic() + _MAX_REVIEW_SECONDS

    def fetch(row):
        fixture, kickoff = row["info"], row["kickoff"]
        if stamp(fixture.get("kickoff")) != kickoff:
            return "independent_fixture_changed", None
        cutoff = kickoff - LINEUP_BASELINE_CUTOFF
        at = clock()
        if at >= cutoff:
            return "lineup_window_closed", None
        request_deadline = min(deadline, time.monotonic() + (cutoff - at).total_seconds())
        if time.monotonic() >= request_deadline:
            return "lineup_collection_budget_exhausted", None
        try:
            state, context, _ = _fotmob_evidence(fixture, clock, request_deadline)
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
            return "lineup_evidence_invalid", None
        if state != "verified":
            return state, None
        if not context or not context.get("lineups"):
            return "lineups_unavailable", None
        observed = stamp(context.get("observed_at"))
        if observed and observed >= cutoff:
            return "lineup_window_closed", None
        if context.get("lineup_type") not in CONFIRMED_LINEUPS:
            return "lineups_unconfirmed", None
        if not _complete_lineups(context):
            return "lineups_incomplete", None
        if not confirmed_baseline(fixture, context):
            return "lineup_evidence_invalid", None
        return "lineups_confirmed", context

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(fetch, claims))


def _same_lineup_fixture(fixture, context):
    teams = [fixture.get("fotmob_home_id"), fixture.get("fotmob_away_id")]
    return bool(fixture.get("fixture_id") and all(teams) and context.get("fixture_id") == fixture["fixture_id"]
                and context.get("team_ids") == teams and context.get("kickoff") == fixture.get("kickoff"))


def _availability_snapshot(context):
    sides = rows(context.get("lineups"))
    return len(sides) == 2 and all(side.get("absences_complete") is True for side in sides)


def _news_baseline(fixture, expected, opening, *, allow_absences_only=False):
    confirmed = record(fixture.get(CONFIRMED_BASELINE))
    candidates = (confirmed, record(fixture.get("review_context")))
    if not confirmed_baseline(fixture, confirmed):
        candidates = candidates[1:]
    for before in candidates:
        at = stamp(before.get("observed_at"))
        if (at and opening and timedelta(0) < opening - at <= timedelta(hours=7)
                and _same_lineup_fixture(fixture, before) and before.get("source") == expected.get("source")
                and before.get("sha256") == document_digest({key: value for key, value in before.items() if key != "sha256"})
                and (_complete_lineups(before) or allow_absences_only and _availability_snapshot(before))):
            return before
    return {}


def _lineup_changes(before, current):
    changes = []
    for side, left, right in zip(("home", "away"), before["lineups"], current["lineups"], strict=True):
        changes.extend(f"{side}_{field}_changed" for field in ("starters", "formation", "absences")
                       if left[field] != right[field])
        if left.get("goalkeeper") and right.get("goalkeeper") and left["goalkeeper"] != right["goalkeeper"]:
            changes.append(f"{side}_goalkeeper_changed")
        if set(right["starters"]) & right["absences"].keys():
            changes.append(f"{side}_listed_starter_unavailable")
    return changes


def should_save_pregame_context(fixture, current):
    at = stamp(current.get("observed_at"))
    if not at or not _same_lineup_fixture(fixture, current):
        return False
    previous = record(fixture.get("review_context"))
    if not _same_lineup_fixture(fixture, previous):
        previous = {}
    previous_at = stamp(previous.get("observed_at"))
    already_published = (previous.get("lineup_type") in CONFIRMED_LINEUPS
                         and any(side.get("starters") for side in rows(previous.get("lineups"))))
    if previous_at and at <= previous_at or _complete_lineups(previous) or already_published:
        return False
    if _availability_snapshot(previous):
        # Keep the independent pre-publication injuries for the history comparison.
        # A predicted XI with a full injury list is a richer pre-publication reference.
        return (current.get("lineup_type") == "predicted" and _complete_lineups(current)
                and _availability_snapshot(current))
    return _complete_lineups(current) or _availability_snapshot(current)


def _news(fixture, context, opening, clock, *, audit=None):
    state, changes = "news_baseline_missing", []
    expected = record(context)
    at, kickoff = stamp(expected.get("observed_at")), stamp(fixture.get("kickoff"))
    now = clock()
    publication_window = (opening and at and kickoff and opening < kickoff - LINEUP_BASELINE_CUTOFF
                          and kickoff - LINEUP_BASELINE_LEAD <= at < kickoff - LINEUP_BASELINE_CUTOFF)
    # During the additional window, a second download of the newly published XI
    # cannot establish that its personnel are consistent with the team xG model.
    source_fixture = fixture
    reference_cutoff = opening
    if publication_window:
        source_fixture = dict(fixture, **{CONFIRMED_BASELINE: None})
        reference_cutoff = confirmed_review_opening(fixture) or at
    before = _news_baseline(source_fixture, expected, reference_cutoff)
    current_confirmed = (at is not None and opening and opening <= at and timedelta(0) <= now - at <= BOOK_AGE
                         and expected.get("lineup_type") in CONFIRMED_LINEUPS and _complete_lineups(expected)
                         and _same_lineup_fixture(fixture, expected))
    if before:
        state = "lineups_unconfirmed"
        if current_confirmed:
            changes = _lineup_changes(before, expected)
            state = "news_changed" if changes else "verified"
    initial = record(fixture.get(INITIAL_CONFIRMED_LINEUP)) or record(fixture.get(CONFIRMED_BASELINE))
    official_change = []
    if (current_confirmed and confirmed_baseline(fixture, initial) and initial.get("source") == expected.get("source")
            and initial.get("sha256") == document_digest({k: v for k, v in initial.items() if k != "sha256"})
            and stamp(initial["observed_at"]) <= at):
        official_change = _lineup_changes(initial, expected)
        if official_change:
            state, changes = "news_changed", official_change
    proof = {"basis": state, "current_digest": expected.get("sha256"), "current_at": expected.get("observed_at"),
             "baseline_digest": before.get("sha256"), "baseline_at": before.get("observed_at")}
    history_applies = (state != "verified" and current_confirmed and kickoff and at >= kickoff - LINEUP_BASELINE_LEAD
                       and fixture.get("league") in MODELED_LEAGUES and fixture.get("lineup_history")
                       and not official_change and before.get("lineup_type") not in CONFIRMED_LINEUPS)
    if history_applies:
        from football_lineup_history import evaluate

        availability = _news_baseline(source_fixture, expected, reference_cutoff, allow_absences_only=True)
        accepted, code, details = evaluate(fixture, expected, availability, now)
        proof.update(history_reason=code, history=details, baseline_digest=availability.get("sha256"),
                     baseline_at=availability.get("observed_at"))
        if accepted:
            state = "verified_team_history"
            changes.clear()
        elif code:
            changes.append(code)
    if initial:
        proof["first_confirmed_digest"] = initial.get("sha256")
    proof["basis"] = state
    if audit is not None:
        audit.update(proof)
    concordant = state in {"verified", "verified_team_history"}
    check = gates.check_news(fixture["id"], "赛前首发复核", news_delta=gates.NEWS_UNCHANGED if concordant else gates.NEWS_UNKNOWN,
                             blockers=() if concordant else (state, *changes),
                             sources=(expected["source"],) if expected.get("source") else ())
    return check, state


@dataclass(frozen=True)
class _BooksRequest:
    tokens: tuple[str, ...]

    def open(self, url, *, timeout_seconds):
        body = json.dumps([{"token_id": token} for token in self.tokens], separators=(",", ":")).encode()
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "Content-Type": "application/json",
                   "User-Agent": "polymarket-alpha-lab/0.1"}
        request = Request(url, method="POST", data=body, headers=headers)
        return build_opener(ProxyHandler({}), pm._NoRedirect()).open(request, timeout=timeout_seconds)


def _book_batch(tokens, clock, deadline):
    try:
        data, fetched = _public(pm.CLOB_BASE_URL + "/books", public_budget(deadline), clock, _BooksRequest(tuple(tokens)))
    except Exception:
        return {}
    requested = set(tokens)
    received, duplicate = {}, set()
    for item in rows(data):
        token = item.get("asset_id")
        if isinstance(token, str) and token in requested:
            if token in received:
                duplicate.add(token)
            else:
                received[token] = item, fetched
    return {key: value for key, value in received.items() if key not in duplicate}


def _book_single(token, clock, deadline):
    try:
        path = "?" + urlencode({"token_id": token})
        data, fetched = _public(pm.CLOB_BOOK_URL + path, public_budget(deadline), clock)
        return token, (data, fetched)
    except Exception:
        return token, (None, None)


def _validated_book(contract, option, observed):
    raw, received = observed
    if not isinstance(raw, dict) or raw.get("asset_id") != option.token_id or raw.get("market") != contract.condition_id:
        return None
    try:
        book = pm._parse_order_book(option.token_id, raw)
    except (TypeError, ValueError, pm._MarketRejected):
        return None
    at = book.book_observed_at
    if at is None or received is None or not timedelta(0) <= received - at <= BOOK_AGE:
        return None
    asks = [(level.price, level.size) for level in book.asks if level.size > 0]
    bid = next((level.price for level in book.bids if level.size > 0), None)
    if not asks or bid is not None and bid > asks[0][0]:
        return None
    return asks, at


def _contract_order(market):
    kind_score = {"match_winner": 0, "total": 1, "btts": 2, "team_total": 3}
    return kind_score[market.kind], abs(market.line - 2.5) if market.line is not None else 0, market.id


def _quoted_markets(assembled, clock, deadline, expanded):
    markets = sorted(assembled, key=_contract_order)
    flattened = [(market, option) for market in markets for option in market.options
                 if expanded or not option.side.endswith("_no")]
    seen = set()
    sides = []
    for market, option in flattened:
        if option.token_id not in seen:
            sides.append((market, option))
            seen.add(option.token_id)
    limit = _BATCH_SIZE * _MAX_BATCHES if expanded else 6
    selection = sides[:limit]
    tokens = [option.token_id for _, option in selection]
    chunks = [tokens[index:index + _BATCH_SIZE] for index in range(0, len(tokens), _BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=_MAX_BATCHES) as pool:
        batches = [pool.submit(_book_batch, batch, clock, deadline) for batch in chunks]
        raw = {key: value for job in batches for key, value in job.result().items()}
        retries = [option.token_id for _, option in selection if option.token_id not in raw][:3]
        for key, value in pool.map(lambda token: _book_single(token, clock, deadline), retries):
            raw[key] = value
    quotes = []
    for market, option in selection:
        book = _validated_book(market, option, raw.get(option.token_id, (None, None)))
        quotes.append((market, option, book))
    return quotes, expanded and (len(selection) < len(sides) or any(book is None for _, _, book in quotes))


def _model_projection(fixture, now):
    from football_model import MODEL_AGE

    data = record(fixture.get("model"))
    observed = stamp(data.get("observed_at"))
    identity = (data.get("fixture_id"), data.get("kickoff"), data.get("team_ids"))
    expected = (fixture.get("fixture_id"), fixture.get("kickoff"), [fixture.get("fotmob_home_id"), fixture.get("fotmob_away_id")])
    if (identity != expected or observed is None or not timedelta(0) <= now - observed <= MODEL_AGE
            or not isinstance(data.get("sha256"), str) or data.get("variant_names") != list(soccer_model.SWEEP_NAMES)):
        return None
    lambdas = data.get("lambdas")
    if not isinstance(lambdas, list) or len(lambdas) != 10:
        return None
    converted = [tuple(number(x) for x in pair) for pair in lambdas if isinstance(pair, list) and len(pair) == 2]
    if len(converted) != 10 or any(rate is None or not 0 <= rate <= 20 for pair in converted for rate in pair):
        return None
    outputs = [soccer_model.variant_outputs(*pair, rho=spec.rho) for pair, spec in zip(converted, soccer_model.SWEEPS)]
    means = tuple(soccer_model.median([pair[side] for pair in converted]) for side in (0, 1))
    return data, outputs, means


def _model_quote(market, selection, outputs):
    if market.kind == "total":
        values = [soccer_model.over_probability(value, market.line) for value in outputs]
    elif market.kind == "team_total":
        values = [soccer_model.team_total_over(value, market.team_side, market.line) for value in outputs]
    elif market.kind == "btts":
        values = [soccer_model.both_teams_to_score(value) for value in outputs]
    else:
        attribute = {"home": "home_win", "away": "away_win", "draw": "draw"}[selection.side.removesuffix("_no")]
        values = [getattr(value, attribute) for value in outputs]
    if selection.side in {"under", "no"} or selection.side.endswith("_no"):
        values = [1.0 - value for value in values]
    if not all(0 <= value <= 1 for value in values):
        return None
    return {"model_median": soccer_model.median(values), "qs_variants": tuple(values)}


def _fee_rate(market):
    schedule = record(market.get("feeSchedule"))
    paid_rate = number(schedule.get("rate"))
    if market.get("feesEnabled") is not True or schedule.get("takerOnly") is not True or number(schedule.get("exponent")) != 1 or paid_rate is None:
        return None
    try:
        return fees.resolve_taker_fee_rate("sports", market_rate=paid_rate)
    except ValueError:
        return None


def _regulation(market):
    description = market.get("description")
    return isinstance(description, str) and _REGULATION in " ".join(description.casefold().split())


def _verify_live_contract(market, kickoff, clock, deadline):
    try:
        live, _ = _public(f"{pm.GAMMA_BASE_URL}/markets/{market.id}", public_budget(deadline), clock)
        if (not isinstance(live, dict) or not _open_market(live) or not _regulation(live)
                or stamp(live.get("gameStartTime")) != kickoff or _contract_pairs(live) != _contract_pairs(market.raw)):
            return None
        required = ("id", "slug", "question", "conditionId", "sportsMarketType", "groupItemTitle", "resolutionSource")
        same_identity = all(str(live.get(key)) == str(market.raw.get(key)) for key in required)
        same_terms = live["description"].split() == market.raw["description"].split()
        if not (same_identity and same_terms and number(live.get("line")) == market.line and _fee_rate(live) is not None):
            return None
        return live
    except Exception:
        return None


def _paper_projections(fixture, now):
    if fixture.get("league") in MODELED_LEAGUES:
        model = _model_projection(fixture, now)
        return (model,) if model is not None else ()
    if not fixture.get("research"):
        return ()
    from football_research import available_forecasts
    return available_forecasts(fixture, now)


def _needs_admission(league, kind, version):
    return (league not in MODELED_LEAGUES or version != MODEL_VERSION_SOCCER
            or (league in ("elc", "ere") and kind == "match_winner"))


def scope_permitted(fixture, kind, line, team_side, model_version):
    league = fixture.get("league")
    if not _needs_admission(league, kind, model_version):
        return True
    from football_admission import scope_key
    scope = scope_key(league, kind, line, team_side, model_version)
    return scope is not None and scope in fixture.get("_paper_scopes", ())


def _screen(fixture, market, selection, book, projection, news, raw=None):
    if book is None or not _regulation(market.raw) or not gates.suspension_policy(market.kind).eligible:
        return None
    model_version = getattr(projection, "model_version", MODEL_VERSION_SOCCER)
    if not scope_permitted(fixture, market.kind, market.line, market.team_side, model_version):
        return None
    fee = _fee_rate(raw if raw is not None else market.raw)
    if fee is None:
        return None
    liquidity = number((raw if raw is not None else market.raw).get("liquidityNum"))
    if liquidity is None:
        liquidity = number((raw if raw is not None else market.raw).get("liquidity"))
    if liquidity is None or liquidity < 0:
        return None
    model, outputs, (home, away) = getattr(projection, "projection", projection)
    check_form = (market.kind, selection.side) in (("total", "over"), ("btts", "yes"))
    hits = model.get("over_2_5_hits")
    if check_form and (type(hits) is not int or not 0 <= hits <= 10):
        return None
    under_risk, _ = gates.under_risk_check(kind=market.kind, side=selection.side, o25_hits=hits or 0,
                                          lambda_home=home, lambda_away=away, lambda_total=home + away,
                                          line=market.line if market.line is not None else 2.5)
    quote = (projection.market_quote(market, selection) if hasattr(projection, "market_quote")
             else _model_quote(market, selection, outputs))
    if quote is None:
        return None
    try:
        result = gates.evaluate_candidate(quote, book[0], gamma_liquidity=liquidity, news=news,
                                          fee_rate=fee, under_risk=under_risk)
    except (ValueError, TypeError, OverflowError):
        return None
    if not all(value for name, value in result.checks.items() if name != "news_concordant"):
        return None
    return result, quote, fee


def _roi_rank(probability, price, fee_rate, conservative_probability):
    from sports_portfolio import value_metrics

    value = value_metrics(probability, price, fee_rate, worst_probability=conservative_probability)
    return -value.conservative_roi, -value.expected_roi


def _paper_signal_rank(signal):
    value = _roi_rank(signal["model_probability"], signal["price"], signal["fee_rate"],
                      signal["conservative_probability"])
    return (*value, str(signal["market_id"]), signal["model_version"], signal["outcome"])


def _priced_direction(market, option, projection, screened, observed, cutoff):
    gate, quote, fee = screened
    return {
        "market_id": market.id, "outcome": option.outcome, "direction": option.label,
        "model_version": getattr(projection, "model_version", MODEL_VERSION_SOCCER),
        "model_digest": getattr(projection, "projection", projection)[0]["sha256"],
        "market_kind": market.kind, "line": market.line, "team_side": market.team_side, "side": option.side,
        "condition_id": market.condition_id, "token_id": option.token_id,
        "price": gate.entry_price, "book_observed_at": utc_iso(observed),
        "valid_until": utc_iso(min(cutoff, observed + BOOK_AGE)),
        "model_probability": quote["model_median"], "variants": list(quote["qs_variants"]),
        "conservative_probability": min(quote["qs_variants"]),
        "net_edge": gate.exec_edge, "adjusted_edge": gate.exec_edge_haircut, "worst_edge": gate.worst_variant_edge,
        "near_two_cent_depth": gate.walk.depth_near2c, "filled_usd": gate.walk.filled_notional, "fee_rate": fee,
        "fee_per_share": gates.fee_cost(gate.entry_price, fee), "gamma_liquidity": gate.gamma_liquidity,
        "url": f"https://polymarket.com/event/{market.event_slug}/{market.raw['slug']}",
    }


def _verified_signals(fixture, candidates, news, kickoff, cutoff, clock, deadline):
    verified = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {contract.id: pool.submit(_verify_live_contract, contract, kickoff, clock, deadline)
                for contract in {market.id: market for market, _, _, _ in candidates}.values()}
        for key, task in jobs.items():
            verified[key] = task.result()
    current = clock()
    signals = []
    for market, option, book, projection in candidates:
        if verified[market.id] is None:
            continue
        result = _screen(fixture, market, option, book, projection, news, raw=verified[market.id])
        if result is None or not result[0].decision.passed:
            continue
        observed = book[1]
        if current < min(cutoff, observed + BOOK_AGE):
            signals.append(_priced_direction(market, option, projection, result, observed, cutoff))
    return sorted(signals, key=_paper_signal_rank)


_WATCH_REASONS = {
    "unsupported_league": "该联赛未纳入可用模型", "model_unavailable": "两队xG或上季数据未通过复核",
    "independent_fixture_missing": "独立赛程当前未匹配", "fotmob_unavailable": "独立赛程或首发数据暂不可用",
    "independent_fixture_changed": "独立赛程的队伍或时间未通过核对", "news_baseline_missing": "缺少可比的早期首发证据",
    "lineups_unconfirmed": "最新首发尚未确认", "news_changed": "首发或伤停较早期预期有变化，影响待核",
    "markets_unavailable": "暂无经核对的挂牌盘口", "books_unavailable": "实时订单簿暂无有效卖盘",
    "thresholds_unmet": "价差、收费、深度或变体一致性未达到模拟门槛",
    "direct_contract_unverified": "盘口条款、收费或有效期复核未通过",
    "opening_pending": "复核窗口尚未开启", "late_review": "本轮复核已迟到", "advance": "临场将复核盘口与首发",
    "safety": "赛前补提醒，按观察处理",
}


def _match_text(fixture, kickoff):
    title = f"{clean_label(fixture.get('home'), 46)} 对 {clean_label(fixture.get('away'), 46)}"
    return f"{title}｜北京 {kickoff.astimezone(BEIJING):%m-%d %H:%M}"


def _watch(fixture, kickoff, reason, quotes=(), **updates):
    details = []
    for _, option, book in quotes:
        if book:
            observed = book[1].astimezone(BEIJING).strftime("%H:%M")
            details.append(f"{option.label}卖价 {book[0][0][0] * 100:.1f}¢（{observed}）")
        if len(details) == 2:
            break
    if not details:
        details = [f"{option.label}参考价 {option.reference * 100:.1f}¢" for _, option, _ in quotes if option.reference][:2]
        if details:
            details.insert(0, "Gamma挂牌")
    explanation = _WATCH_REASONS.get(reason, fixture.get("reason", "继续观察"))
    text = f"{_match_text(fixture, kickoff)}。仅观察，{explanation}。"
    if details:
        text += "，".join(details) + "。"
    elif reason not in {"markets_unavailable", "books_unavailable"}:
        text += "盘口暂无可核价格。"
    return {"status": "watch", "text": text, "reason_code": reason, **updates}


def _skip(reason, moved=None):
    payload = {"status": "skip", "text": "", "reason_code": reason}
    if moved:
        payload["moved_kickoff"] = moved
    return payload


def review(fixture: dict, now: datetime, stage: str) -> dict:
    """Evaluate the current fixture and venue for one scheduled alert stage."""
    kickoff = stamp(fixture.get("kickoff"))
    if now.utcoffset() is None or kickoff is None or stage not in (*_STAGE_WINDOWS, "advance", "safety"):
        return _skip("invalid_review")
    clock = _clock(now.astimezone(UTC))
    as_of = clock()
    if as_of >= kickoff:
        return _skip("at_or_after_kickoff")
    if fixture.get("state") in {"cancelled", "postponed", "started"}:
        return _skip(fixture["state"])
    stage_window = _STAGE_WINDOWS.get(stage)
    opening, cutoff = (kickoff - timedelta(minutes=stage_window[0]), kickoff - timedelta(minutes=stage_window[1])) if stage_window else (None, kickoff)
    paper_cutoff = min(cutoff, opening + _LATE) if opening else None
    confirmed_phase = False
    if stage == "t90" and fixture.get("league") in MODELED_LEAGUES and paper_cutoff <= as_of < cutoff:
        first = confirmed_review_opening(fixture)
        if first is None or as_of < first:
            return _watch(fixture, kickoff, "lineups_unconfirmed")
        opening, paper_cutoff, confirmed_phase = first, cutoff, True
    supported = fixture.get("league") in MODELED_LEAGUES or bool(fixture.get("research"))
    paper_time = bool(opening and opening <= as_of < paper_cutoff)
    expanded = paper_time and supported and (bool(fixture.get("research")) or isinstance(fixture.get("model"), dict))
    deadline = time.monotonic() + min(_MAX_REVIEW_SECONDS, max(0.05, (kickoff - as_of).total_seconds()))

    references = _gamma_event_refs(fixture, expanded)
    with ThreadPoolExecutor(max_workers=3) as pool:
        event_tasks = [pool.submit(_refresh_event, fixture, ref, clock, deadline) for ref in references]
        context_task = pool.submit(_fotmob_evidence, fixture, clock, deadline) if supported and fixture.get("fixture_id") else None
        events = [task.result() for task in event_tasks]
        state, context, moved = context_task.result() if context_task else ("independent_fixture_missing", None, None)
    if state in {"cancelled", "postponed", "started"}:
        return _skip(state, moved)
    if events and events[0]:
        primary = events[0]
        primary_state = _gamma_state(primary)
        if primary_state != "scheduled":
            return _skip(primary_state)
        new_time = stamp(primary.get("startTime"))
        if new_time is not None and new_time != kickoff:
            return _skip("postponed", utc_iso(new_time) if new_time > clock() else None)

    if confirmed_phase:
        observed = published_lineup_time(fixture, context) if state == "verified" else None
        personnel_digest = confirmed_personnel_digest(context) if observed is not None else None
        if (personnel_digest is None or observed < opening
                or not timedelta(0) <= clock() - observed <= BOOK_AGE):
            return _watch(fixture, kickoff, state if state != "verified" else "lineups_unconfirmed",
                          review_phase="confirmed_lineups", lineup_window_opened_at=utc_iso(opening))

    markets = {}
    for event in events:
        if (event and event.get("active") is True and event.get("closed") is False and event.get("archived") is not True
                and stamp(event.get("startTime")) == kickoff and _gamma_state(event) == "scheduled"):
            markets.update({market.condition_id: market for market in _event_markets(fixture, event, kickoff)})
    quotes, partial_books = _quoted_markets(markets.values(), clock, deadline, expanded)
    current = clock()
    if current >= kickoff:
        return _skip("at_or_after_kickoff")
    quotes = [(market, option, book if book and timedelta(0) <= current - book[1] <= BOOK_AGE
               and (not confirmed_phase or book[1] >= opening) else None)
              for market, option, book in quotes]
    extras = {"partial_books": True} if partial_books else {}
    if confirmed_phase:
        extras["review_phase"] = "confirmed_lineups"
        extras["lineup_window_opened_at"] = utc_iso(opening)
    if expanded and any(event is None for event in events):
        extras["partial_coverage"] = True
    current_context = record(context)
    if (stage in {"advance", "t90"} and current < kickoff - timedelta(minutes=30)
            and should_save_pregame_context(fixture, current_context)):
        # Before the XI is published, the match-specific availability report is already useful.
        extras["fixture_update"] = {"review_context": context}
    if not supported:
        return _watch(fixture, kickoff, "unsupported_league", quotes, **extras)
    if stage in ("advance", "safety"):
        reason = state if state != "verified" else stage
        return _watch(fixture, kickoff, reason, quotes, **extras)
    if as_of < opening:
        return _watch(fixture, kickoff, "opening_pending", quotes, **extras)
    if not paper_time or current >= paper_cutoff:
        return _watch(fixture, kickoff, "late_review", quotes, **extras)
    if state != "verified":
        return _watch(fixture, kickoff, state, quotes, **extras)
    projections = _paper_projections(fixture, current)
    if not projections:
        return _watch(fixture, kickoff, "model_unavailable", quotes, **extras)
    if fixture.get("league") not in MODELED_LEAGUES:
        versions = {scope[4] for scope in fixture.get("_paper_scopes", ())}
        projections = tuple(model for model in projections if model.model_version in versions)
        if not projections:
            return _watch(fixture, kickoff, "research_only", quotes, **extras)
    if not quotes or all(book is None for _, _, book in quotes):
        return _watch(fixture, kickoff, "books_unavailable" if markets else "markets_unavailable", quotes, **extras)
    lineup_review = {}
    news, reason = _news(fixture, context, opening, clock, audit=lineup_review)
    extras["lineup_review"] = lineup_review
    baseline = _news_baseline(fixture, record(context), opening)
    if baseline:
        extras["lineup_baseline"] = {field: baseline[field] for field in ("lineup_type", "observed_at", "sha256")}
    candidates, held = [], []
    for market, option, book in quotes:
        for projection in projections:
            screened = _screen(fixture, market, option, book, projection, news)
            if screened is None:
                continue
            candidates.append((market, option, book, projection))
            if not news.concordant:
                held.append(_priced_direction(market, option, projection, screened, book[1], paper_cutoff))
    if not news.concordant:
        extras["reason_codes"] = list(news.blockers)
        if reason == "news_changed":
            extras["lineup_comparison"] = {"baseline": baseline, "current": context}
    if not candidates:
        return _watch(fixture, kickoff, "thresholds_unmet", quotes, candidates=0, **extras)
    if not news.concordant:
        # These listing and book snapshots are for replay. Only direct-contract checks below can emit signals.
        extras["price_candidates"] = sorted(held, key=_paper_signal_rank)
        return _watch(fixture, kickoff, reason, quotes, candidates=len(candidates), **extras)
    lineup_expiry = stamp(context["observed_at"]) + BOOK_AGE
    signals = _verified_signals(fixture, candidates, news, kickoff, min(lineup_expiry, paper_cutoff), clock, deadline)
    if clock() >= kickoff:
        return _skip("at_or_after_kickoff")
    if not signals:
        return _watch(fixture, kickoff, "direct_contract_unverified", quotes, candidates=len(candidates), **extras)
    if confirmed_phase:
        for signal in signals:
            signal["review_phase"] = "confirmed_lineups"
            signal["lineup_window_opened_at"] = utc_iso(opening)
            signal["confirmed_personnel_sha256"] = personnel_digest
    for signal in signals:
        signal["lineup_review"] = lineup_review
    best = signals[0]
    direction_count = len({signal["token_id"] for signal in signals})
    text = (f"{_match_text(fixture, kickoff)}。纸上模拟｜{best['direction']}，250美元走盘均价 {best['price']*100:.1f}¢，"
            f"十变体中位 {best['model_probability']:.1%}，计费后优势 {best['net_edge']*100:+.1f} 个百分点。"
            f"{'官宣首发符合两队近五场轮换记录。' if reason == 'verified_team_history' else '首发核对一致。'}"
            f"{f'共{direction_count}个方向达标，提示其中优先项。' if direction_count > 1 else ''}")
    return {"status": "paper", "text": text, "valid_until": best["valid_until"], "reason_code": "qualified",
            "signal": best, "signals": signals, **extras}
