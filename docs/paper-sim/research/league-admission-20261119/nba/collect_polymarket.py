"""Collect two-sided Polymarket CLOB history strictly before each NBA tip."""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from polymarket_alpha_lab.papersim import espn_nba

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TYPES = {"ml": "moneyline", "spread": "spreads", "total": "totals"}
STRICT = "explicit-full-game-including-OT"
DIAGNOSTIC = "NBA-final-score-OT-not-explicit"
PM_TEAM = {"GS": "gsw", "NY": "nyk", "SA": "sas", "UTAH": "uta", "WSH": "was", "NO": "nop"}
PM_LABELS = {"LAC": ("clippers", "la clippers", "los angeles clippers"),
             "LAL": ("lakers", "los angeles lakers"),
             "POR": ("trail blazers", "blazers", "portland trail blazers")}
START_MINUTES = 25
STOP_MINUTES = 5


def get(url, cache, allow_missing=False):
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json.gz")
    if path.is_file():
        return json.loads(gzip.decompress(path.read_bytes()))
    for attempt in range(3):
        request = urllib.request.Request(
            url, headers={"User-Agent": "polymarket-alpha-lab/research", "Accept-Encoding": "gzip"}
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
            if raw[:2] == bytes.fromhex("1f8b"):
                raw = gzip.decompress(raw)
            data = json.loads(raw)
            path.write_bytes(gzip.compress(raw, mtime=0))
            return data
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if allow_missing and isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                return None
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"unreachable response from {url}")


def epoch(value):
    return int(datetime.fromisoformat(str(value)).timestamp())


def team_is(label, team):
    target = espn_nba.NBA_TEAMS[team][1].casefold()
    value = label.strip().casefold()
    return value in PM_LABELS.get(team, ()) or value == target or target.endswith(" " + value)


def has_market_kind(market, game, kind):
    market_type = market.get("sportsMarketType")
    if market_type == TYPES[kind]:
        return True
    # Two 2025 contracts are tagged totals despite explicitly settling on
    # which team wins. Require the exact game slug and verify teams below.
    return (kind == "ml" and market_type == "totals" and market.get("line") is None
            and market.get("slug") in base_slugs(game)
            and str(market.get("description")).lower().count("win, the market will resolve") >= 2)


def parse_market(market, game, kind):
    if not has_market_kind(market, game, kind):
        raise ValueError("different market type")
    names = json.loads(market["outcomes"])
    tokens = json.loads(market["clobTokenIds"])
    if len(names) != 2 or len(tokens) != 2 or len(set(tokens)) != 2:
        raise ValueError("invalid market outcome tokens")
    if kind == "total":
        if set(names) != {"Over", "Under"}:
            raise ValueError("unexpected totals outcomes")
        line = float(market["line"])
        side = names.index("Over")
    else:
        home, away = game["home"], game["away"]
        sides = [i for i, label in enumerate(names)
                 if team_is(label, home) and team_is(names[1 - i], away)]
        if len(sides) != 1:
            raise ValueError("team outcome mapping failed")
        side = sides[0]
        line = 0.0
        if kind == "spread":
            m = re.fullmatch(r"Spread: (.+) \(([+-]?\d+(?:\.\d+)?)\)", market["question"])
            if m is None or not any(team_is(m[1], team) for team in (home, away)):
                raise ValueError("spread sign or team indeterminate")
            line = -float(m[2]) if team_is(m[1], home) else float(m[2])
            if abs(line) != abs(float(market["line"])):
                raise ValueError("spread line metadata mismatch")
    if kind != "ml" and abs(line) % 1 != 0.5:
        raise ValueError("non-half-point line")
    game_tip = epoch(game["started"])
    pm_tip = epoch(market["gameStartTime"])
    if abs(game_tip - pm_tip) > 30 * 60:
        raise ValueError("ESPN/Gamma start differs >30 minutes")
    tip = min(game_tip, pm_tip)
    listed = epoch(market.get("acceptingOrdersTimestamp") or market["createdAt"])
    if listed > tip - STOP_MINUTES * 60:
        raise ValueError("market listing after pregame cutoff")
    return line, [tokens[side], tokens[1 - side]], tip, listed


def settlement(market, game, kind, line, tokens, tip):
    """Reconcile the published contract, ordered tokens and binary final."""
    text = market["description"].replace("“", '"').replace("”", '"')
    clauses = [clause.strip() for clause in text.splitlines()
               if "will resolve" in clause.lower() and "50-50" not in clause]
    if (len(clauses) != 2 or game["date"] not in market["slug"]
            or not market["slug"].startswith(tuple(base_slugs(game)))):
        raise ValueError("unrecognized rules or matchup slug")
    home, away = game["home"], game["away"]
    if kind == "ml":
        teams_in_rules = set()
        for clause in clauses:
            match = re.fullmatch(r'If the (.+) win, the market will resolve to "([^"]+)"\.', clause, re.I)
            matching = [t for t in (home, away)
                        if match is not None and team_is(match[1], t) and team_is(match[2], t)]
            if len(matching) != 1:
                raise ValueError("moneyline rules/outcome direction mismatch")
            teams_in_rules.add(matching[0])
        if teams_in_rules != {home, away} or line != 0:
            raise ValueError("moneyline rules do not distinguish both teams")
    elif kind == "spread":
        first = re.fullmatch(
            r'This market will resolve to "([^"]+)" if the (.+) win the game by (\d+) or more points\.',
            clauses[0], re.I,
        )
        other = re.fullmatch(
            r'Otherwise, this market will resolve to "([^"]+)"\. If the game ends in a tie, '
            r'this market will resolve to "([^"]+)"\.',
            clauses[1], re.I,
        )
        favorite, opposing = (home, away) if line > 0 else (away, home)
        if (first is None or other is None or not all(team_is(first[i], favorite) for i in (1, 2))
                or not all(team_is(other[i], opposing) for i in (1, 2))
                or int(first[3]) != math.ceil(abs(line))):
            raise ValueError("spread rules/points/team direction mismatch")
    elif kind == "total":
        over = re.fullmatch(
            r'This market will resolve to "Over" if the (.+) and (.+) combine to score '
            r'(\d+) or more points in this game\.',
            clauses[0], re.I,
        )
        under = re.fullmatch(
            r'If the combined total is less than (\d+), this market will resolve to "Under"\.',
            clauses[1], re.I,
        )
        if (over is None or under is None or int(over[3]) != math.ceil(line)
                or int(under[1]) != math.ceil(line)
                or not any(team_is(over[1], a) and team_is(over[2], b)
                           for a, b in ((home, away), (away, home)))):
            raise ValueError("totals rules/points/matchup mismatch")
    scope = STRICT if re.search(
        r'(?:final|combined) (?:score|total) including any overtime periods\b', text, re.I
    ) else DIAGNOSTIC

    gamma_tokens = json.loads(market["clobTokenIds"])
    labels = json.loads(market["outcomes"])
    seen = [float(p) for p in json.loads(market["outcomePrices"])]
    if (market.get("closed") is not True or market.get("umaResolutionStatus") != "resolved"
            or sorted(seen) != [0.0, 1.0] or epoch(market["closedTime"]) <= tip):
        raise ValueError("no verified binary post-tip settlement")
    source = market.get("resolutionSource")
    if source != "https://www.nba.com/":
        raise ValueError("unexpected settlement source")
    actual = game["home_pts"] + game["away_pts"] if kind == "total" else game["home_pts"] - game["away_pts"]
    actual_yes = int(actual > line)
    primary = gamma_tokens.index(tokens[0])
    if (seen[primary], seen[1 - primary]) != (actual_yes, 1 - actual_yes):
        raise ValueError("settled outcome differs from scored NBA final")
    return {
        "scope": scope, "description": market["description"], "resolution_source": source,
        "gamma_question": market["question"], "gamma_market_type": market.get("sportsMarketType"),
        "gamma_outcomes": labels, "gamma_tokens": gamma_tokens, "gamma_terminal_prices": seen,
        "closed": market["closed"], "resolution_status": market["umaResolutionStatus"],
        "closed_time": market["closedTime"], "model_primary_index_in_gamma": primary,
        "actual_primary": actual_yes,
    }


def histories(tokens, tip, cache):
    start = tip - START_MINUTES * 60
    end = tip - STOP_MINUTES * 60
    last = []
    for token in tokens:
        query = urlencode({"market": token, "startTs": start, "endTs": end, "fidelity": 5})
        hist = get(f"{CLOB}/prices-history?{query}", cache).get("history", [])
        in_window = [(int(q["t"]), float(q["p"])) for q in hist
                     if start <= q["t"] <= end and 0 < q["p"] < 1]
        if not in_window:
            raise ValueError("missing token observation in pregame window")
        last.append(max(in_window))
    if abs(last[0][0] - last[1][0]) > 5 * 60:
        raise ValueError("pregame token observations over 5 minutes apart")
    return last


def find_quote(game, kind, candidates, cache, reference_line=None):
    parsed = []
    reasons = []
    for market in candidates:
        if not has_market_kind(market, game, kind):
            continue
        try:
            line, tokens, tip, listed = parse_market(market, game, kind)
        except (KeyError, TypeError, ValueError) as exc:
            reasons.append(str(exc))
            continue
        if reference_line is None or line == reference_line:
            parsed.append((listed, market["slug"], market, line, tokens, tip))
    if not parsed:
        return None, reasons[0] if reasons else "no matching market contract"
    # Historical alternate lines are always chosen by the earliest pregame listing.
    # This order excludes volume, price, team results and post-tip events.
    listed, slug, market, line, tokens, tip = min(parsed, key=lambda row: row[:2])
    try:
        rule = settlement(market, game, kind, line, tokens, tip)
        prices = histories(tokens, tip, cache)
    except (KeyError, TypeError, ValueError) as exc:
        return None, str(exc)
    return {
        "line": line, "p": (prices[0][1] + 1 - prices[1][1]) / 2,
        "slug": slug, "condition_id": market["conditionId"],
        "tokens": tokens, "token_observations": prices, "effective_tip": tip,
        "gamma_tip": market["gameStartTime"], "market_listed": listed,
        "settlement": rule,
    }, None


def base_slugs(game):
    home = PM_TEAM.get(game["home"], game["home"].lower())
    away = PM_TEAM.get(game["away"], game["away"].lower())
    return [f"nba-{first}-{second}-{game['date']}" for first, second in ((away, home), (home, away))]


def same_dk_contracts(game, cache):
    def points(value):
        return f"{abs(value):g}".replace(".", "pt")

    slugs = []
    for base in base_slugs(game):
        if "ml" in game["dk_proxy"]:
            slugs.append(base)
        if "spread" in game["dk_proxy"]:
            line = game["dk_proxy"]["spread"]["line"]
            if line != 0:
                slugs.append(f"{base}-spread-{'home' if line > 0 else 'away'}-{points(line)}")
        if "total" in game["dk_proxy"]:
            line = game["dk_proxy"]["total"]["line"]
            slugs.append(f"{base}-total-{points(line)}")
    if not slugs:
        return []
    query = urlencode([("closed", "true")] + [("slug", slug) for slug in slugs])
    payload = get(f"{GAMMA}/markets?{query}", cache)
    mapped = [market for market in payload if market.get("slug") in slugs]
    found = {market.get("sportsMarketType") for market in mapped}
    expected = {TYPES[kind] for kind in game["dk_proxy"]}
    if expected - found:
        # A new final may still be awaiting exchange settlement.
        query = urlencode([("closed", "false")] + [("slug", slug) for slug in slugs])
        opened = get(f"{GAMMA}/markets?{query}", cache)
        mapped.extend(market for market in opened if market.get("slug") in slugs
                      and market.get("sportsMarketType") not in found)
    return mapped


def historical_contracts(game, cache):
    for base in base_slugs(game):
        event = get(f"{GAMMA}/events/slug/{base}", cache, allow_missing=True)
        if event is not None and event.get("slug") == base:
            return event.get("markets", [])
    return []


def game_quotes(game, cohort, cache):
    recent = cohort == "same-DK-line"
    try:
        candidates = same_dk_contracts(game, cache) if recent else historical_contracts(game, cache)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"event_id": game["event_id"], "date": game["date"], "quotes": {},
                "missing": {kind: f"Gamma error: {exc}" for kind in TYPES}}
    quotes = {}
    missing = {}
    for kind in TYPES:
        if recent and kind not in game["dk_proxy"]:
            missing[kind] = "no DraftKings reference line of this family"
            continue
        reference = game["dk_proxy"][kind]["line"] if recent else None
        try:
            quote, reason = find_quote(game, kind, candidates, cache, reference)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            quote, reason = None, f"CLOB error: {exc}"
        if quote is None:
            missing[kind] = reason
        else:
            quotes[kind] = quote
    return {"event_id": game["event_id"], "date": game["date"],
            "quotes": quotes, "missing": missing}


def eligible_games(games):
    counts = Counter()
    by_day = {}
    for game in games:
        by_day.setdefault(game["date"], []).append(game)
    selected = []
    for day in sorted(by_day):
        slate = by_day[day]
        selected.extend(game for game in slate
                        if min(counts[game["home"]], counts[game["away"]]) >= 8)
        counts.update(team for game in slate for team in (game["home"], game["away"]))
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--market-from")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    data = json.loads(gzip.decompress(args.games.read_bytes()))
    games = eligible_games(data["games"])
    if args.market_from:
        games = [game for game in games if game["date"] >= args.market_from]
    eligible = len(games)
    if args.limit and args.limit < len(games):
        # Sample uniformly by chronological game index, using only dates and opponents.
        games = [games[math.floor((i + 0.5) * len(games) / args.limit)] for i in range(args.limit)]
    cohort = "same-DK-line" if data["season"] == "2026-27" else "earliest-listed-line"
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(game_quotes, game, cohort, args.cache) for game in games]
        rows = [task.result() for task in futures]
    counts = Counter(kind for row in rows for kind in row["quotes"])
    settlement_by_market = {
        scope: dict(Counter(kind for row in rows for kind, quote in row["quotes"].items()
                            if quote["settlement"]["scope"] == scope))
        for scope in (STRICT, DIAGNOSTIC)
    }
    out = {"season": data["season"], "cohort": cohort, "eligible_in_window": eligible,
           "market_from": args.market_from, "selected": len(games),
           "sources": {"contracts": GAMMA, "prices": CLOB + "/prices-history"},
           "pregame_minutes_before_tip": [STOP_MINUTES, START_MINUTES],
           "retrieved_at": datetime.now(timezone.utc).isoformat(), "counts": counts,
           "settlement_by_market": settlement_by_market, "rows": rows}
    args.output.write_bytes(gzip.compress(json.dumps(out, sort_keys=True).encode(), mtime=0))
    print(f"{data['season']}: {counts} from {len(games)} selected", flush=True)


if __name__ == "__main__":
    main()
