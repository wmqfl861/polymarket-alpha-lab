"""Public, read-only Gamma and CLOB observations for the five European research leagues."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import math
import re
import unicodedata
from urllib.parse import urlencode

from . import fees, gates
from .adapters import polymarket as pm
from .euro_soccer_team_news import NEWS_MAX_AGE


LEAGUE_TAGS = {"por": "101772", "scop": "102872", "bel1": "105725", "nor": "102651", "tur": "102564"}
SERIES_SLUGS = {"por": "primeira-liga", "scop": "scottish-premiership", "bel1": "bel1-games",
                "nor": "norway-eliteserien", "tur": "tur-2025"}
PAGE_SIZE = 75  # Gamma caps a page at 100 even when a larger limit is requested.
MAX_PAGES = 20
MAX_AGE = timedelta(minutes=5)
HORIZON = timedelta(hours=48)
REGULATION = "first 90 minutes of regular play plus stoppage time"
FAMILIES = {"match_winner": ("home", "draw", "away"), "total_2_5": ("total_2_5",)}
MARKET_PROBABILITY_KEYS = {"match_winner": ("home", "draw", "away"), "total_2_5": ("over", "under")}
MODEL_PROBABILITY_KEYS = {"match_winner": ("home_win", "draw", "away_win"), "total_2_5": ("over_2_5", "under_2_5")}
CLUB_WORDS = frozenset("fc cf sk fk sc afc if bk krc ksv kv kaa rsc spor klubu jk il ik sv cd as ac".split())
SPELLINGS = str.maketrans({"ı": "i", "ø": "o", "æ": "ae", "ł": "l", "ð": "d"})
# Reviewed Gamma/FotMob pairs. Short names from the independent fixture can also be supplied.
TEAM_ALIASES = {
    "bel1": {"Union Saint-Gilloise": "Union St.Gilloise", "KVC Westerlo": "Westerlo",
             "Yellow-Red KV Mechelen": "KV Mechelen", "Oud-Heverlee Leuven": "OH Leuven",
             "Royal Charleroi SC": "Sporting Charleroi", "St. Truidense VV": "St.Truiden"},
    "nor": {"Aalesunds FK": "Aalesund", "Sandefjord Fotball": "Sandefjord",
            "Sarpsborg 08 FF": "Sarpsborg 08", "Vålerenga Fotball": "Vålerenga", "KFUM-Kameratene Oslo": "KFUM"},
    "por": {"Académico de Viseu FC": "Académico Viseu", "Sport Lisboa e Benfica": "Benfica",
            "CS Marítimo": "Marítimo", "Vitória SC": "Vitória de Guimarães", "Estoril Praia": "Estoril"},
    "tur": {"Çaykur Rizespor": "Rizespor", "Rams Başakşehir FK": "Başakşehir"},
}


@dataclass(frozen=True)
class PublicResponse:
    """Decoded public response and the receipt time and SHA-256 of its archived raw body."""

    payload: object
    received_at: datetime
    sha256: str


@dataclass(frozen=True)
class Listing:
    league: str
    kickoff: datetime
    home: str
    away: str
    game_id: str
    event_slug: str
    leg: str
    market_id: str
    condition: str
    tokens: tuple[tuple[str, str], ...]
    fee: float
    liquidity: float | None
    rules_sha256: str
    received_at: datetime
    source_sha256: str


def _utc(value):
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return dt.astimezone(UTC) if dt.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value):
    return value.isoformat().replace("+00:00", "Z")


def _usable(response):
    return (isinstance(getattr(response, "received_at", None), datetime) and _utc(response.received_at) is not None
            and isinstance(getattr(response, "sha256", None), str)
            and re.fullmatch(r"[0-9a-f]{64}", response.sha256) is not None)


def _id(value):
    text = str(value)
    return text if re.fullmatch(r"[1-9][0-9]{0,77}", text) else None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def probability_vector(row, keys):
    """Accept finite, normalized stored probabilities without clipping or filling missing sides."""
    if not isinstance(row, dict):
        return None
    values = tuple(row.get(key) for key in keys)
    if (not all(type(value) in (float, int) and 0 <= value <= 1 for value in values)
            or not math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=1e-9)):
        return None
    return values


def valid_source_hashes(values):
    return (isinstance(values, list) and bool(values)
            and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in values))


def news_scenario_issue(forecast):
    """A scenario needs captured, unblocked team news registered with its forecast."""
    if forecast.get("status") != "team_news_scenario":
        return "context_missing"
    news = forecast.get("news")
    if not isinstance(news, dict) or news.get("blockers") != []:
        return "news_unavailable_or_blocked"
    stamp, cutoff = _utc(news.get("fetched_at")), _utc(forecast.get("as_of"))
    if stamp is None or cutoff is None or stamp > cutoff or cutoff - stamp > NEWS_MAX_AGE:
        return "news_outside_forecast_window"
    if (not valid_source_hashes(news.get("source_refs")) or not valid_source_hashes(forecast.get("source_refs"))
            or not set(news["source_refs"]) <= set(forecast["source_refs"])):
        return "news_source_receipt_missing"
    return None


def _team_key(name):
    if not isinstance(name, str):
        return ""
    normalized = unicodedata.normalize("NFKD", name.casefold().translate(SPELLINGS))
    plain = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(word for word in re.findall(r"[a-z0-9]+", plain) if word not in CLUB_WORDS)


def _names(league, values):
    aliases = TEAM_ALIASES.get(league, {})
    keys = {_team_key(aliases.get(v, v)) for v in values if isinstance(v, str)}
    return keys - {""}


def _matches_rules(rules, home, away, leg):
    if not isinstance(rules, str):
        return False
    terms = " ".join(rules.casefold().split())
    if REGULATION not in terms:
        return False
    if leg == "total_2_5":
        over = f'this market will resolve to "over" if {home} and {away} combine to score 3 or more goals'
        return over.casefold() in terms and 'less than 3, this market will resolve to "under"' in terms
    winner = {"home": f"{home} wins", "away": f"{away} wins", "draw": "the game ends in a draw"}[leg]
    return (f'if {winner.casefold()}, this market will resolve to "yes"' in terms
            and 'otherwise, this market will resolve to "no"' in terms)


def _listing(league, row, market_type, start, end, source):
    if not (row.get("active") is True and row.get("closed") is False and row.get("archived") is False
            and row.get("acceptingOrders") is True and row.get("enableOrderBook") is True
            and row.get("sportsMarketType") == market_type and row.get("tradingEligible") is not False):
        return None
    kickoff = _utc(row.get("gameStartTime"))
    opened = _utc(row.get("acceptingOrdersTimestamp"))
    if kickoff is None or not start <= kickoff < end or opened is None or not opened <= source.received_at < kickoff:
        return None
    slug = re.fullmatch(rf"({league}-([a-z0-9]+)-([a-z0-9]+)-\d{{4}}-\d{{2}}-\d{{2}})-(.+)", str(row.get("slug")))
    if not slug or slug[2] == slug[3]:
        return None
    totals = market_type == "totals"
    expected_event = slug[1] + ("-more-markets" if totals else "")
    events = row.get("events")
    if not isinstance(events, list):
        return None
    matching = [e for e in events if isinstance(e, dict) and e.get("slug") == expected_event
                and e.get("seriesSlug") == SERIES_SLUGS[league] and _utc(e.get("startTime")) == kickoff
                and e.get("active") is True and e.get("closed") is False and e.get("archived") is False
                and not e.get("live") and not e.get("ended")]
    if len(matching) != 1:
        return None
    event = matching[0]
    title = str(event.get("title", "")).removesuffix(" - More Markets")
    teams = title.split(" vs. ")
    if len(teams) != 2 or not all(_team_key(team) for team in teams) or _team_key(teams[0]) == _team_key(teams[1]):
        return None
    home, away = teams
    label = row.get("groupItemTitle")
    if totals:
        if slug[4] != "total-2pt5" or _number(row.get("line")) != 2.5 or label != "O/U 2.5":
            return None
        leg, expected = "total_2_5", {"over", "under"}
    else:
        legs = {slug[2]: ("home", home), "draw": ("draw", f"Draw ({title})"), slug[3]: ("away", away)}
        if slug[4] not in legs or legs[slug[4]][1] != label or row.get("line") is not None:
            return None
        leg, expected = legs[slug[4]][0], {"yes", "no"}
    rules = row.get("description")
    if not _matches_rules(rules, home, away, leg):
        return None
    try:
        names = pm._maybe_json_list(row.get("outcomes"))
        assets = pm._maybe_json_list(row.get("clobTokenIds"))
    except ValueError:
        return None
    if (not isinstance(names, list) or not isinstance(assets, list) or len(names) != 2 or len(assets) != 2
            or not all(isinstance(k, str) for k in names) or {k.lower() for k in names} != expected
            or not all(isinstance(t, str) and _id(t) for t in assets) or assets[0] == assets[1]):
        return None
    cid = row.get("conditionId")
    mid, gid = _id(row.get("id")), _id(event.get("gameId"))
    if not mid or not gid or not isinstance(cid, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", cid):
        return None
    schedule = row.get("feeSchedule")
    if (row.get("feesEnabled") is not True or not isinstance(schedule, dict) or schedule.get("takerOnly") is not True
            or _number(schedule.get("exponent")) != 1 or _number(schedule.get("rate")) is None):
        return None
    try:
        fee = fees.resolve_taker_fee_rate("sports", market_rate=float(schedule["rate"]))
    except ValueError:
        return None
    liquidity = _number(row.get("liquidityClob"))
    return Listing(league, kickoff, home, away, gid, slug[1], leg, mid, cid.lower(),
                   tuple((k.lower(), t) for k, t in zip(names, assets, strict=True)), fee,
                   liquidity if liquidity is not None and liquidity >= 0 else None,
                   sha256(rules.encode()).hexdigest(), source.received_at, source.sha256)


def discover_markets(league, start, end, fetch):
    """Fetch bounded Gamma pages per market family within [start, end); report partial inventories."""
    start, end = _utc(start), _utc(end)
    if league not in LEAGUE_TAGS or start is None or end is None or end <= start:
        raise ValueError("invalid_europe_soccer_market_window")
    found, gaps = [], []
    for kind in ("moneyline", "totals"):
        collected, seen = [], set()
        # Gamma filters on endDate. Match rows by the actual gameStartTime inside this wider query.
        query = dict(tag_id=LEAGUE_TAGS[league], sports_market_types=kind, closed="false", active="true",
                     limit=PAGE_SIZE, order="id", ascending="true", end_date_min=_timestamp(start-timedelta(days=2)),
                     end_date_max=_timestamp(end+timedelta(days=2)))
        for page in range(MAX_PAGES):
            url = pm.GAMMA_MARKETS_BY_CONDITION_URL + "?" + urlencode(query | {"offset": page * PAGE_SIZE})
            try:
                response = fetch(url, None)
            except (OSError, ValueError):
                gaps.append(kind + "_gamma_unavailable")
                break
            if not _usable(response) or not isinstance(response.payload, list) or len(response.payload) > PAGE_SIZE:
                gaps.append(kind + "_gamma_invalid_page")
                break
            rows = response.payload
            ids = [_id(item.get("id")) for item in rows if isinstance(item, dict)]
            if len(ids) != len(rows) or None in ids or len(set(ids)) != len(ids) or seen.intersection(ids):
                gaps.append(kind + "_gamma_ambiguous_page")
                break
            seen.update(ids)
            for row in rows:
                item = _listing(league, row, kind, start, end, response)
                if item is not None:
                    collected.append(item)
            if len(rows) < PAGE_SIZE:
                found.extend(collected)
                break
        else:
            gaps.append(kind + "_gamma_page_limit")
    return found, tuple(gaps)


def _book(raw, token, listing, received, earliest):
    if not isinstance(raw, dict) or raw.get("market") != listing.condition or raw.get("asset_id") != token:
        return None
    try:
        snapshot = pm._parse_order_book(token, raw)
    except (pm._MarketRejected, ValueError):
        return None
    at = snapshot.book_observed_at
    if at is None or not earliest <= at <= received:
        return None
    bids = [(x.price, x.size) for x in snapshot.bids if x.size > 0]
    asks = [(x.price, x.size) for x in snapshot.asks if x.size > 0]
    if not bids or not asks or bids[0][0] >= asks[0][0]:
        return None
    bid, ask = bids[0][0], asks[0][0]
    walk = gates.walk_book(asks)
    if not math.isfinite(walk.depth_near2c):
        return None
    return {"token_id": token, "book_observed_at": _timestamp(at), "bid": bid, "ask": ask,
            "midpoint": (bid + ask) / 2, "fee_per_share_at_ask": fees.fee_per_share(ask, listing.fee),
            "research_notional_usd": walk.notional, "research_fill_usd": walk.filled_notional,
            "research_vwap": walk.vwap if walk.filled else None, "depth_near_2c_usd": walk.depth_near2c,
            "fee_per_share_at_research_vwap": fees.fee_per_share(walk.vwap, listing.fee) if walk.filled else None}


def _unavailable(reason):
    return {"status": "unavailable", "reasons": [reason], "contracts": {}, "market_probabilities": {}}


def _observed_family(members, books, received, earliest):
    contracts, mids = {}, {}
    for leg, (item,) in members.items():
        if not timedelta(0) <= received - item.received_at <= MAX_AGE or received >= item.kickoff:
            return _unavailable("gamma_stale_or_kickoff_passed")
        quotes = {}
        for label, token in item.tokens:
            raw = books.get(token, ())
            quote = _book(raw[0], token, item, received, earliest) if len(raw) == 1 else None
            if quote is None:
                return _unavailable("batch_book_missing_stale_or_invalid")
            quotes[label] = quote
            if leg == "total_2_5" or label == "yes":
                mids[label if leg == "total_2_5" else leg] = quote["midpoint"]
        contracts[leg] = {"market_id": item.market_id, "condition_id": item.condition, "fee_rate": item.fee,
                          "gamma_liquidity_usd": item.liquidity, "gamma_snapshot_sha256": item.source_sha256,
                          "gamma_received_at": _timestamp(item.received_at), "rules_sha256": item.rules_sha256,
                          "tokens": quotes}
    denominator = sum(mids.values())
    return {"status": "observed", "reasons": [], "contracts": contracts,
            "market_probabilities": {side: value / denominator for side, value in mids.items()}}


def capture_slot(kickoff, observed):
    """Registered time window shared by the collector and the outcome verifier."""
    lead = kickoff - observed
    if timedelta(0) < lead <= timedelta(minutes=3):
        return "closing"
    if timedelta(minutes=20) < lead <= timedelta(minutes=30):
        return "forward"
    if timedelta(minutes=30) < lead <= HORIZON:
        return "preview"
    return None


def observe_markets(league, home, away, kickoff, listings, fetch, *, home_aliases=(), away_aliases=(),
                    fixture_id=None):
    """Read all matched families in one CLOB response. A retry is an independent observation."""
    if league not in LEAGUE_TAGS or _utc(kickoff) is None:
        raise ValueError("invalid_europe_soccer_fixture")
    homes = _names(league, (home, *home_aliases))
    aways = _names(league, (away, *away_aliases))
    selected = [v for v in listings if v.league == league and v.kickoff == _utc(kickoff)
                and _names(league, (v.home,)) & homes and _names(league, (v.away,)) & aways]
    sources = sorted({v.source_sha256 for v in selected})
    report = {"status": "unavailable", "observed_at": None, "window": None, "book_snapshot_sha256": None,
              "fixture": {"league": league, "home": home, "away": away, "kickoff": _timestamp(_utc(kickoff))},
              "source_sha256s": sources, "families": {name: _unavailable("fixture_unmatched") for name in FAMILIES}}
    if fixture_id is not None:
        report["fixture"]["fixture_id"] = fixture_id
    if not selected:
        return report
    if homes & aways or len({(v.event_slug, v.game_id) for v in selected}) != 1:
        report["families"] = {name: _unavailable("ambiguous_gamma_fixture") for name in FAMILIES}
        return report

    by_leg = defaultdict(list)
    for item in selected:
        by_leg[item.leg].append(item)
    market_ids = Counter(v.market_id for v in selected)
    conditions = Counter(v.condition for v in selected)
    token_counts = Counter(t for v in selected for _, t in v.tokens)
    eligible = {}
    for name, legs in FAMILIES.items():
        members = {leg: by_leg[leg] for leg in legs}
        if any(len(entries) != 1 for entries in members.values()):
            report["families"][name] = _unavailable("contract_set_missing_or_ambiguous")
        elif any(market_ids[v.market_id] != 1 or conditions[v.condition] != 1 or
                 any(token_counts[t] != 1 for _, t in v.tokens) for (v,) in members.values()):
            report["families"][name] = _unavailable("contract_identity_collision")
        else:
            eligible[name] = members
    tokens = sorted({t for members in eligible.values() for (v,) in members.values() for _, t in v.tokens})
    if not tokens:
        return report
    try:
        response = fetch(pm.CLOB_BASE_URL + "/books", [{"token_id": t} for t in tokens])
        ok = _usable(response) and isinstance(response.payload, list)
    except (OSError, ValueError):
        ok = False
    if not ok:
        report["families"].update({name: _unavailable("clob_batch_unavailable") for name in eligible})
        return report
    received = response.received_at
    slot = capture_slot(_utc(kickoff), received)
    earliest = received - MAX_AGE
    if slot in ("forward", "closing"):
        window = slot
        span = timedelta(minutes=30 if slot == "forward" else 3)
        earliest = max(earliest, _utc(kickoff) - span)
    else:
        window = "pregame" if received < _utc(kickoff) else "at_or_after_kickoff"
    report.update(observed_at=_timestamp(received), window=window, book_snapshot_sha256=response.sha256,
                  source_sha256s=sorted(set(sources) | {response.sha256}))
    books = defaultdict(list)
    for raw in response.payload:
        if not isinstance(raw, dict) or raw.get("asset_id") not in tokens:
            report["families"].update({name: _unavailable("unexpected_clob_batch_item") for name in eligible})
            return report
        books[raw["asset_id"]].append(raw)
    for name, members in eligible.items():
        family = _observed_family(members, books, received, earliest)
        if family["status"] == "observed":
            family.update(book_snapshot_sha256=response.sha256, observed_at=_timestamp(received))
        report["families"][name] = family
    count = sum(f["status"] == "observed" for f in report["families"].values())
    report["status"] = {0: "unavailable", 1: "partial", 2: "complete"}[count]
    return report


def _edge_models(forecast, fixture, kickoff, book_at):
    """The forecast must be eligible at the public book's timestamp for the same fixture and window."""
    if (not isinstance(forecast, dict) or forecast.get("research_only") is not True
            or type(forecast.get("amount_cny")) not in (float, int) or forecast["amount_cny"] != 0
            or not _id(fixture.get("fixture_id")) or forecast.get("fixture_id") != fixture["fixture_id"]
            or forecast.get("league") != fixture.get("league")
            or not valid_source_hashes(forecast.get("source_refs"))
            or forecast.get("status") not in ("team_baseline", "team_news_scenario")):
        return {}
    at = _utc(forecast.get("as_of"))
    if at is None or at > book_at or capture_slot(kickoff, at) != capture_slot(kickoff, book_at):
        return {}
    models = {"baseline": forecast.get("baseline")}
    if news_scenario_issue(forecast) is None:
        models["context"] = forecast.get("context")
    return models


def research_edges(forecast_doc, market_doc):
    """Compare unit payouts with the fee-inclusive public ask on each distinct outcome."""
    template = dict(ask=None, fee_per_share=None, break_even=None, baseline=None, context=None)
    edges = {side: template.copy() for sides in MARKET_PROBABILITY_KEYS.values() for side in sides}
    if not isinstance(market_doc, dict) or market_doc.get("status") not in ("partial", "complete"):
        return edges
    fixture = market_doc.get("fixture")
    if not isinstance(fixture, dict) or fixture.get("league") not in LEAGUE_TAGS:
        return edges
    kickoff = _utc(fixture.get("kickoff"))
    book_at = _utc(market_doc.get("observed_at"))
    digest = market_doc.get("book_snapshot_sha256")
    refs = market_doc.get("source_sha256s")
    families = market_doc.get("families")
    if (kickoff is None or book_at is None or book_at >= kickoff or not isinstance(families, dict)
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not valid_source_hashes(refs) or digest not in refs):
        return edges
    slot = capture_slot(kickoff, book_at)
    window = "pregame" if slot == "preview" else slot
    if slot is None or market_doc.get("window") != window:
        return edges
    models = _edge_models(forecast_doc, fixture, kickoff, book_at)
    for family, sides in MARKET_PROBABILITY_KEYS.items():
        row = families.get(family)
        if (not isinstance(row, dict) or row.get("status") != "observed" or row.get("book_snapshot_sha256") != digest
                or _utc(row.get("observed_at")) != book_at or not isinstance(row.get("contracts"), dict)):
            continue
        probabilities = {name: probability_vector(model, MODEL_PROBABILITY_KEYS[family]) for name, model in models.items()}
        for index, side in enumerate(sides):
            leg, token = (side, "yes") if family == "match_winner" else (family, side)
            contract = row["contracts"].get(leg)
            if not isinstance(contract, dict) or not isinstance(contract.get("tokens"), dict):
                continue
            quote = contract["tokens"].get(token)
            if not isinstance(quote, dict):
                continue
            ask, fee = quote.get("ask"), quote.get("fee_per_share_at_ask")
            if type(ask) not in (float, int) or not 0 < ask < 1:
                continue
            edges[side]["ask"] = ask
            if type(fee) not in (float, int) or not 0 <= fee <= 1:
                continue
            break_even = ask + fee
            edges[side].update(fee_per_share=fee, break_even=break_even)
            for name, vector in probabilities.items():
                if vector is not None:
                    edges[side][name] = vector[index] - break_even
    return edges
