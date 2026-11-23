"""Read-only shortlist of basketball research observations for the next 24 hours."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import monotonic

from polymarket_alpha_lab.papersim import gates

import basketball_college as college
import basketball_college_forward as college_source
import basketball_feed as basketball
import basketball_models as models
import football_feed as feed
import football_store as store


LEAGUES = {"nba": "NBA", "cbb": "NCAA 男篮", "cwbb": "NCAA 女篮"}
TEAM_DISPLAY = {
    "nba": {"Minnesota Timberwolves": "森林狼", "Brooklyn Nets": "篮网"},
    "cbb": {"Utah Valley Wolverines": "犹他谷大学", "UT Martin Skyhawks": "田纳西大学马丁分校",
            "California-San Diego Tritons": "加州大学圣迭戈分校", "Seattle Redhawks": "西雅图大学",
            "Loyola Chicago Ramblers": "芝加哥洛约拉大学", "San Jose State Spartans": "圣何塞州立大学",
            "Tulane Green Wave": "杜兰大学", "Georgia State Panthers": "佐治亚州立大学",
            "Southern Illinois Salukis": "南伊利诺伊大学卡本代尔分校", "Middle Tennessee Blue Raiders": "中田纳西州立大学"},
}
BOOK_AGE = timedelta(minutes=2)
NBA_FIXTURES = 20
COLLEGE_FIXTURES_PER_LEAGUE = 44
NBA_MARKETS_PER_FIXTURE = 5
PUBLIC_SECONDS = 22
COLLEGE_PROBABILITY_STRESS = 0.03
COLLEGE_MAX_MARKET_GAP = 0.15
MIN_WORST_NET_EDGE = 0.02


def _fixtures(connection, now, end):
    args = (now, end, now - store.FIXTURE_FRESHNESS, now)
    nba = connection.execute("""
        SELECT id,revision,info,kickoff FROM fixture
        WHERE info->>'sport'='basketball' AND info->>'league'='nba' AND fixture_state='scheduled'
          AND kickoff>%s AND kickoff<=%s AND last_seen BETWEEN %s AND %s AND missing_scans<2
          AND info ? 'model' AND info ? 'nba_ratings_model'
        ORDER BY kickoff,id LIMIT %s
        """, (*args, NBA_FIXTURES)).fetchall()
    if not connection.execute("SELECT to_regclass('football_notify.college_basketball_forecast') AS ready").fetchone()["ready"]:
        return nba
    college_rows = connection.execute("""
        WITH ranked AS (
            SELECT f.id, f.revision, f.info, f.kickoff, p.official_id, p.home_id, p.away_id, p.neutral,
                   p.home_probability, p.ratings_through, p.observed_at AS model_observed_at, p.source_url,
                   row_number() OVER (PARTITION BY p.league ORDER BY f.kickoff,f.id) AS rank
            FROM fixture f JOIN college_basketball_forecast p
              ON (f.id,f.revision,f.kickoff)=(p.fixture_key,p.revision,p.kickoff)
                 AND f.info->>'league'=p.league
            WHERE p.model_version=%s AND p.league=ANY(%s) AND f.info->>'sport'='basketball'
              AND f.fixture_state='scheduled' AND f.kickoff>%s AND f.kickoff<=%s
              AND f.last_seen BETWEEN %s AND %s AND f.missing_scans<2
              AND p.observed_at<=%s AND p.observed_at<p.kickoff AND p.result IS NULL
              AND p.ratings_through BETWEEN %s AND %s
        ) SELECT * FROM ranked WHERE rank<=%s ORDER BY kickoff,id
        """, (college.VERSION, list(college.LEAGUES), *args, now,
              college.season_day(now) - timedelta(days=3), college.season_day(now) - timedelta(days=2),
              COLLEGE_FIXTURES_PER_LEAGUE)).fetchall()
    return nba + college_rows


def _nba_fixture(row, clock, deadline):
    info = row["info"]
    saved = feed.record(info.get("basketball_official"))
    official, _ = basketball._official_summary(info, saved, clock, deadline)
    if (not official or official["state"] != "scheduled" or official["neutral"]
            or feed.stamp(official["kickoff"]) != row["kickoff"] or row["kickoff"] <= clock()):
        return None
    baseline = basketball._verified_model(info, official, clock())
    updated = basketball._verified_model(info, official, clock(), ratings=True)
    if not baseline or not updated or baseline["model_version"] != models.NBA_VERSION:
        return None
    return baseline, updated


def _college_fixture(row, boards):
    info, tip = row["info"], row["kickoff"]
    slate = boards.get((info["league"], college.season_day(tip)), {})
    match = college_source._matching_fixture(info, slate, tip)
    if not match or any(match.get(id_key) != row.get(row_key) for id_key, row_key in (
            ("id", "official_id"), ("home_id", "home_id"), ("away_id", "away_id"), ("neutral", "neutral"))):
        return False
    probability = feed.number(row.get("home_probability"))
    return probability is not None and 0 < probability < 1


def _college_market(row, clock, deadline):
    info = row["info"]
    slug = info.get("base_slug")
    event_id = feed.numeric_id(info.get("root_event_id"))
    if not event_id or not isinstance(slug, str) or not feed._SLUG.fullmatch(slug):
        return None
    source = f"{basketball.GAMMA}/markets/slug/{slug}"
    raw, observed = basketball._get_safe(source, deadline, clock)
    if not isinstance(raw, dict) or observed is None or observed >= row["kickoff"]:
        return None
    exact = [event for event in feed.rows(raw.get("events"))
             if feed.numeric_id(event.get("id")) == event_id and event.get("slug") == slug]
    if len(exact) != 1:
        return None
    event = exact[0]
    if (feed.stamp(event.get("startTime")) != row["kickoff"] or feed._gamma_state(event) != "scheduled"
            or event.get("closed") is not False or event.get("active") is not True or event.get("archived") is True
            or (info.get("game_id") and feed.numeric_id(event.get("gameId")) != info["game_id"])):
        return None
    contract = basketball._contract(info, raw, event_id, slug)
    if contract is None or contract.kind != "match_winner" or contract.scope != basketball.EXPLICIT_OT:
        return None
    return contract


def _nba_markets(row, pair, clock, deadline):
    info = row["info"]
    state, _, contracts, _ = basketball._fixture_markets(info, clock, deadline)
    if state != "scheduled" or row["kickoff"] <= clock():
        return ()
    strict = [market for market in contracts if market.scope == basketball.EXPLICIT_OT and market.kind in {"match_winner", "total"}]
    return basketball._ordered_markets(strict, pair[-1])[:NBA_MARKETS_PER_FIXTURE]


def _probabilities(row, pair, market, option):
    if pair:
        forecast = [models.quote(model, market.kind, market.line, option.side) for model in pair]
        if not all(forecast):
            return None
        centres = {model["model_version"]: quote["model_median"] for model, quote in zip(pair, forecast)}
        worst = min(value for quote in forecast for value in quote["qs_variants"])
        return models.NBA_RATINGS_VERSION, centres, worst, feed.stamp(pair[-1]["observed_at"])
    home = row["home_probability"]
    p = home if option.side == "home" else 1 - home
    return college.VERSION, {college.VERSION: p}, p - COLLEGE_PROBABILITY_STRESS, row["model_observed_at"]


def _display_name(info, side):
    signed = feed.clean_label(info.get(f"{side}_display"))
    if signed:
        return signed
    official = feed.record(info.get("basketball_official"))
    team = models.espn_nba.NBA_TEAMS.get(official.get(f"{side}_code")) if info["league"] == "nba" else None
    name = team[1] if team else info[side]
    return TEAM_DISPLAY.get(info["league"], {}).get(name, name)


def _evaluate(row, pair, market, raw_books, now):
    tip, info = row["kickoff"], row["info"]
    if not now < tip:
        return None
    fee, liquidity = feed._fee_rate(market.raw), basketball._liquidity(market.raw)
    if fee is None or liquidity is None or liquidity < gates.GATES["liq_min"]:
        return None
    books = {option.token_id: basketball._parsed_book(market, option, raw_books.get(option.token_id, (None, None)))
             for option in market.options}
    if any(book is None or not now - BOOK_AGE <= book["at"] <= book["received"] <= now for book in books.values()):
        return None
    implied = basketball._two_side_price(market, books)
    if not implied:
        return None
    walks = {token: gates.walk_book(book["asks"]) for token, book in books.items()}
    if any(not walk.filled or walk.depth_near2c < gates.DEFAULT_NOTIONAL_USD for walk in walks.values()):
        return None
    focus = []
    display = {side: _display_name(info, side) for side in ("home", "away")}
    official = feed.record(info.get("basketball_official"))
    for option in market.options:
        token = option.token_id
        walk = walks[token]
        price = walk.vwap
        if not gates.ENTRY_WINDOW[0] <= price <= gates.ENTRY_WINDOW[1]:
            continue
        forecast = _probabilities(row, pair, market, option)
        if forecast is None:
            continue
        version, centres, worst, model_at = forecast
        gap = min(centres.values()) - implied[token]
        if (model_at is None or model_at > now or gap <= 0
                or (not pair and gap > COLLEGE_MAX_MARKET_GAP)):
            continue
        total_cost = price + gates.fee_cost(price, fee)
        net = centres[version] - total_cost
        worst_edge = worst - total_cost
        adjusted = min(gates.haircut_edge(p - total_cost, p) for p in centres.values())
        if adjusted < gates.GATES["exec_edge_min"] or worst_edge + gates.GATE_EPSILON < MIN_WORST_NET_EDGE:
            continue
        reason = {"nba": "两版同向，历史验证市场领先，观察0元",
                  "cbb": "男篮历史回放市场领先，当前费后有差，观察0元",
                  "cwbb": "女篮严格市场样本不足，当前费后有差，观察0元"}[info["league"]]
        winner = market.kind == "match_winner"
        direction = option.label
        if winner:
            team = display[option.side]
            direction = f"{team} 胜" if team.isascii() else f"{team}胜"
        focus.append({"sport": "basketball", "league": LEAGUES[info["league"]], "league_code": info["league"],
                      "fixture_key": row["id"], "revision": row["revision"], "kickoff": tip,
                      "home": info["home"], "away": info["away"], "direction": direction,
                      "home_display": display["home"], "away_display": display["away"],
                      "market_kind": "胜负" if winner else "总分",
                      "market_label": "全场胜负（含加时）" if winner else "全场大小分（含加时）",
                      "market_kind_code": market.kind, "line": market.line,
                      "market_id": market.id, "token_id": token, "condition_id": market.condition_id,
                      "condition_side": option.side, "contract_digest": market.terms,
                      "official_id": official.get("id") if pair else row.get("official_id"),
                      "official_source": official.get("schedule_source") if pair else row.get("source_url"),
                      "model_version": version, "model_observed_at": model_at, "probability": centres[version],
                      "conservative_probability": worst,
                      "model_probabilities": centres, "market_probability": implied[token],
                      "price": price, "fee_rate": fee, "fee_per_share": total_cost - price,
                      "quoted_at": books[token]["at"], "valid_until": min(tip, min(book["at"] for book in books.values()) + BOOK_AGE),
                      "depth_near2c_usd": walk.depth_near2c, "edge": net, "worst_edge": worst_edge,
                      "worst_net_edge": worst_edge,
                      "research_only": True, "amount_cny": 0, "reason": reason,
                      "url": f"https://polymarket.com/event/{market.event_slug}" + (f"/{market.raw['slug']}" if not winner else "")})
    return focus[0] if len(focus) == 1 else None


def _rank(entry):
    value = feed._roi_rank(entry["probability"], entry["price"], entry["fee_rate"],
                           entry["conservative_probability"])
    return (*value, entry["kickoff"], str(entry["market_id"]), entry["fixture_key"])


def candidates(connection, now, end):
    """Read existing fixture revisions and public books without creating a position."""
    if now.utcoffset() is None or end.utcoffset() is None or end <= now:
        raise ValueError("basketball focus requires an aware, future window")
    rows = _fixtures(connection, now, min(end, now + timedelta(hours=24)))
    if not rows:
        return []
    clock = feed._clock(now)
    deadline = monotonic() + PUBLIC_SECONDS
    college_days = {(row["info"]["league"], college.season_day(row["kickoff"])) for row in rows if "official_id" in row}
    board_keys = sorted(college_days | {(league, day - timedelta(days=1)) for league, day in college_days})
    with ThreadPoolExecutor(max_workers=12) as pool:
        board_requests = {key: pool.submit(college_source.board, *key, clock, deadline) for key in board_keys}
        nba_requests = {row["id"]: pool.submit(_nba_fixture, row, clock, deadline) for row in rows if "official_id" not in row}
        boards = {key: request.result() for key, request in board_requests.items()}
        slates = {key: college_source._event_day(boards, *key)[0] for key in college_days}
        work = []
        for row in rows:
            if "official_id" in row:
                if _college_fixture(row, slates):
                    work.append((row, None, pool.submit(_college_market, row, clock, deadline)))
            elif (pair := nba_requests[row["id"]].result()) is not None:
                work.append((row, pair, pool.submit(_nba_markets, row, pair, clock, deadline)))
        selected = []
        for row, pair, job in work:
            contracts = job.result()
            if contracts:
                selected.extend((row, pair, market) for market in (contracts if pair else (contracts,)))
        if not selected:
            return []
        # Match direct metadata to the originating event for NBA. NCAA already uses the direct slug endpoint.
        checks = {market.id: pool.submit(basketball._market_direct, market.id, clock, deadline)
                  for _, pair, market in selected if pair}
        book_request = pool.submit(basketball._books, [market for _, _, market in selected], clock, deadline)
        books = book_request.result()
        direct = {market_id: request.result() for market_id, request in checks.items()}
    observations = []
    for row, pair, market in selected:
        if pair:
            raw, observed = direct[market.id]
            verified = basketball._contract(row["info"], raw, market.event_id, market.event_slug)
            if (verified is None or observed is None or observed >= row["kickoff"] or verified.terms != market.terms
                    or feed._fee_rate(raw) != feed._fee_rate(market.raw)):
                continue
            market = verified
        observation = _evaluate(row, pair, market, books, clock())
        if observation:
            observations.append(observation)
    observations.sort(key=_rank)
    unique = {}
    for item in observations:
        unique.setdefault(item["fixture_key"], item)
    return list(unique.values())
