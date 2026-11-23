"""Collect NCAA pregame predictions, matched market prices, and final scores."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import time

from psycopg.types.json import Jsonb

import basketball_college as model
import basketball_feed as basketball
import football_feed as feed
import football_store as store
from football_learning import cached


SCHEMA = """
CREATE TABLE IF NOT EXISTS football_notify.college_basketball_day (
    league text NOT NULL, day date NOT NULL, observed_at timestamptz NOT NULL, scores jsonb NOT NULL,
    PRIMARY KEY (league,day)
);
CREATE TABLE IF NOT EXISTS football_notify.college_basketball_forecast (
    model_version text NOT NULL, fixture_key text NOT NULL, revision integer NOT NULL,
    league text NOT NULL, kickoff timestamptz NOT NULL, observed_at timestamptz NOT NULL,
    official_id text NOT NULL, home_id text NOT NULL, away_id text NOT NULL, neutral boolean NOT NULL,
    ratings_through date NOT NULL, home_probability double precision NOT NULL CHECK(home_probability BETWEEN 0 AND 1),
    source_url text NOT NULL, market jsonb, result jsonb, CHECK(observed_at<kickoff),
    PRIMARY KEY(model_version,fixture_key,revision)
);
CREATE INDEX IF NOT EXISTS college_basketball_pending ON football_notify.college_basketball_forecast(kickoff)
    WHERE result IS NULL;
"""


def board(league, day, clock, deadline):
    gender, _, periods = model.LEAGUES[league]
    slug = gender + "-college-basketball"
    url = f"{basketball.ESPN}/{slug}/scoreboard?dates={day:%Y%m%d}&groups=50&limit=200"
    payload, at = basketball._get_safe(url, deadline, clock)
    data = feed.record(payload)
    events = data.get("events")
    if (at is None or not isinstance(events, list) or len(events) >= 200
            or [item.get("slug") for item in feed.rows(data.get("leagues"))] != [slug]):
        return None
    games, invalid_days, conflicts = {}, set(), set()
    for raw in events:
        if not isinstance(raw, dict):
            invalid_days.add(day)
            continue
        raw_day = feed.stamp(feed.record(raw).get("date"))
        actual_day = model.season_day(raw_day) if raw_day else day
        game = basketball._espn_fixture(raw, provider="espn:" + slug)
        if not game:
            comp = feed.rows(feed.record(raw).get("competitions"))
            slots = feed.rows(comp[0].get("competitors")) if len(comp) == 1 else []
            placeholder = any(feed.record(s.get("team")).get("abbreviation") == "TBD" for s in slots)
            if not placeholder:
                invalid_days.add(actual_day)
            continue
        comp = raw["competitions"][0]
        if type(comp.get("neutralSite")) is not bool or game["id"] in games:
            conflicts.add(game["id"])
            invalid_days.add(actual_day)
            continue
        if game["season_end_year"] != model.SEASON or game["season_type"] not in {2, 3}:
            continue
        for team in comp["competitors"]:
            # Both school designations and the nickname come from this
            # competitor's ESPN team ID on this particular league's board.
            details, side = team["team"], team["homeAway"]
            mascot = feed.clean_label(details.get("name"))
            alternate = mascot.removeprefix("Lady ")
            names = set(game[side + "_names"])
            for school in (details.get("location"), details.get("shortDisplayName")):
                if school and mascot:
                    names.update((f"{school} {mascot}", f"{school} {alternate}"))
            game[side + "_names"] = sorted(names)
        if game["state"] == "final":
            period = raw["status"].get("period")
            if type(period) is not int or period < periods:
                invalid_days.add(actual_day)
                continue
            for side in comp["competitors"]:
                lines = side.get("linescores")
                if lines:
                    values = [model._whole(line.get("displayValue")) for line in lines]
                    if (len(values) != period or None in values or sum(values) != model._whole(side["score"])
                            or [line.get("period") for line in lines] != list(range(1, period + 1))):
                        invalid_days.add(actual_day)
                        conflicts.add(game["id"])
            game["overtime"] = period > periods
        games[game["id"]] = game
    return {"url": url, "observed_at": at, "invalid_days": invalid_days,
            "games": {k: v for k, v in games.items() if k not in conflicts}}


def _event_day(boards, league, day):
    # ESPN can list games after midnight Eastern on the preceding day's slate.
    # Require both slates to call a history day complete. A partial schedule
    # still verifies the individual pregame identities it actually contains.
    merged, invalid, complete = {}, set(), True
    for lookup in (day - timedelta(days=1), day):
        src = boards.get((league, lookup))
        if not src:
            complete = False
            continue
        complete = complete and day not in src["invalid_days"]
        for game_id, game in src["games"].items():
            if model.season_day(feed.stamp(game["kickoff"])) != day:
                continue
            previous = merged.get(game_id)
            if previous and previous[0] != game:
                invalid.add(game_id)
            merged[game_id] = (game, src)
    usable = {k: v for k, v in merged.items() if k not in invalid}
    mature = complete and not invalid and all(g["state"] in {"final", "cancelled", "postponed"}
                                               for g, _ in usable.values())
    return usable, mature


def _scores(entries):
    return [[g["kickoff"], g["home_id"], g["away_id"], g["neutral"], g["home_score"], g["away_score"]]
            for _, (g, _) in sorted(entries.items()) if g["state"] == "final"]


def _reverse(game):
    pairs = {key.removeprefix("home_") for key in game if key.startswith("home_")}
    return game | {f"{a}_{key}": game[f"{b}_{key}"] for key in pairs
                   for a, b in (("home", "away"), ("away", "home"))}


def _matching_fixture(info, games, tip, *, states=("scheduled",)):
    choices = []
    for game, _ in games.values():
        if game["state"] not in states or feed.stamp(game["kickoff"]) != tip:
            continue
        # Neutral-site tournaments can designate opposite home teams. Preserve
        # the market's team order for both probability and final-score grading.
        orientations = (game, _reverse(game)) if game["neutral"] else (game,)
        choices.extend(option for option in orientations if basketball._match(info, [option], kickoff=tip))
    return choices[0] if len(choices) == 1 else None


def _market(fixture, clock, deadline):
    # Re-fetch the exact moneyline, then verify both books belong to its
    # condition. A price without an explicit OT rule cannot enter this sample.
    slug = fixture["base_slug"]
    raw, at = basketball._get_safe(f"{basketball.GAMMA}/markets/slug/{slug}", deadline, clock)
    official_event = feed.numeric_id(fixture.get("root_event_id"))
    if not official_event or not isinstance(raw, dict) or at is None:
        return None, "market_metadata_unavailable"
    rules = raw.get("description")
    if not isinstance(rules, str) or basketball._market_scope(rules) != basketball.EXPLICIT_OT:
        return None, "overtime_rule_unverified"
    market = basketball._contract(fixture, raw, official_event, slug)
    attached = feed.rows(raw.get("events"))
    if (not market or market.kind != "match_winner" or market.scope != basketball.EXPLICIT_OT
            or not any(feed.numeric_id(e.get("id")) == official_event and e.get("slug") == slug for e in attached)):
        return None, "exact_moneyline_unverified"
    books = basketball._books([market], clock, deadline)
    parsed = {op.token_id: basketball._parsed_book(market, op, books.get(op.token_id, (None, None)))
              for op in market.options}
    implied = basketball._two_side_price(market, parsed)
    if not implied:
        return None, "two_sided_book_unverified"
    end = clock()
    start = feed.stamp(fixture["kickoff"]) - timedelta(minutes=30)
    if not (start <= at <= end < start + timedelta(minutes=10)):
        return None, "market_outside_price_window"
    sides = {}
    for op in market.options:
        book = parsed[op.token_id]
        if not start <= book["at"] <= book["received"] <= end:
            return None, "book_outside_price_window"
        sides[op.side] = basketball._observation(fixture, market, op, book, model=None, prediction=None,
                                               implied=implied, raw=raw, reasons=[])
    if set(sides) != {"home", "away"}:
        return None, "market_team_sides_unverified"
    team_fields = (f"{side}{suffix}" for side in ("home", "away") for suffix in ("", "_alias", "_abbr"))
    contract = {"teams": {field: fixture.get(field) for field in team_fields}, "description": rules,
                "question": raw["question"], "resolution_source": raw["resolutionSource"],
                "token_order": [option.token_id for option in market.options],
                "event_id": market.event_id, "event_slug": market.event_slug, **market.document()}
    return {"observed_at": feed.utc_iso(end), "metadata_observed_at": feed.utc_iso(at),
            "sides": sides, "contract": contract}, None


def _official_result(row, games):
    game, source = games.get(row["official_id"], (None, None))
    if not game:
        return None
    provider_home = game["home_id"]
    if game["neutral"] and game["away_id"] == row["home_id"]:
        game = _reverse(game)
    if (game["state"] not in {"final", "cancelled"}
            or feed.stamp(game["kickoff"]) != row["kickoff"] or source["observed_at"] <= row["kickoff"]
            or (game["home_id"], game["away_id"], game["neutral"]) != (row["home_id"], row["away_id"], row["neutral"])):
        return None
    return {key: game[key] for key in ("id", "state", "home_score", "away_score", "home_id", "away_id", "overtime")} | {
        "source": source["url"], "observed_at": feed.utc_iso(source["observed_at"]), "provider_home_id": provider_home,
    }


def _terminal_market(row, official, clock, deadline):
    frozen = feed.record(row["market"])
    sides = feed.record(frozen.get("sides"))
    home, away = (feed.record(sides.get(side)) for side in ("home", "away"))
    kickoff = row["kickoff"]
    opens, expires = kickoff - timedelta(minutes=30), kickoff - timedelta(minutes=20)
    captured = feed.stamp(frozen.get("observed_at"))
    model_at = feed.stamp(frozen.get("model_observed_at"))
    if (captured is None or model_at is None or not opens <= model_at <= captured < expires
            or any(side.get("fixture_key") != row["fixture_key"] or side.get("league") != row["league"]
                   or feed.stamp(side.get("kickoff")) != kickoff or side.get("side") != direction
                   or side.get("market_kind") != "match_winner" or side.get("settlement_scope") != basketball.EXPLICIT_OT
                   or not feed.numeric_id(side.get("token_id")) or not feed.clean_label(side.get("outcome"))
                   for direction, side in (("home", home), ("away", away)))
            or any(home.get(key) != away.get(key) for key in ("market_id", "condition_id", "contract_digest", "event_id", "event_slug"))
            or home["token_id"] == away["token_id"]):
        return None, "frozen_market_incomplete"
    for side in (home, away):
        quote_at = feed.stamp(side.get("book_observed_at"))
        received_at = feed.stamp(side.get("book_received_at"))
        if quote_at is None or received_at is None or not opens <= quote_at <= received_at <= captured:
            return None, "frozen_book_clock_unverified"
    market_id, event_id = (feed.numeric_id(home.get(key)) for key in ("market_id", "event_id"))
    slug = home.get("event_slug")
    if not market_id or not event_id or not isinstance(slug, str) or not feed._SLUG.fullmatch(slug):
        return None, "frozen_market_incomplete"

    saved_terms = feed.record(frozen.get("contract"))
    # First captures have both sides and the digest, but predate the frozen aliases.
    teams = {"home": home["outcome"], "away": away["outcome"]} | feed.record(saved_terms.get("teams"))
    info = {**teams, "league": row["league"], "kickoff": feed.utc_iso(kickoff), "base_slug": slug}
    # The slug endpoint includes the linked event in addition to the payout.
    source = f"{basketball.GAMMA}/markets/slug/{slug}"
    raw, observed = basketball._get_safe(source, deadline, clock)
    if not isinstance(raw, dict) or observed is None:
        return None, "terminal_market_unavailable"
    terminal = basketball._terminal(raw, observed)
    if terminal is None:
        return None, "terminal_market_pending"
    prices, closed = terminal
    market = basketball._contract(info, raw, event_id, slug, terminal=True)
    events = feed.rows(raw.get("events"))
    if (market is None or market.document() != {key: home.get(key) for key in market.document()}
            or not any(feed.numeric_id(event.get("id")) == event_id and event.get("slug") == slug for event in events)
            or any((option.token_id, option.outcome) != (sides[option.side]["token_id"], sides[option.side]["outcome"])
                   for option in market.options)):
        return None, "terminal_contract_disagrees"
    if closed <= captured or (prices != [0.5, 0.5] and closed <= kickoff):
        return None, "terminal_clock_unverified"
    if prices != [0.5, 0.5] and prices != basketball._payout(market, official, closed):
        return None, "terminal_payout_disagrees"
    return {**market.document(), "event_id": event_id, "event_slug": slug,
            "state": "void" if prices == [0.5, 0.5] else "verified", "payouts": prices,
            "token_order": [option.token_id for option in market.options],
            "outcome_order": [option.outcome for option in market.options],
            "closed_at": feed.utc_iso(closed), "observed_at": feed.utc_iso(observed), "source": source}, None


def _reconcile(pending, event_days, clock):
    candidates = []
    for row in pending:
        slate = event_days.get((row["league"], model.season_day(row["kickoff"])))
        if slate is None:
            continue
        previous, latest = row["result"], _official_result(row, slate[0])
        proof = previous or latest
        score_fields = ("state", "home_score", "away_score", "overtime")
        if previous and latest and any(latest[key] != previous.get(key) for key in score_fields):
            prior = {key: value for key, value in previous.items() if key != "previous_official_results"}
            proof = latest | {"previous_official_results": [*previous.get("previous_official_results", []), prior]}
        if proof:
            candidates.append((row, proof))
    priced = [(row, proof) for row, proof in candidates if row["market"] is not None]
    deadline = time.monotonic() + 16
    terminal, gaps = {}, Counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = pool.map(lambda item: _terminal_market(*item, clock, deadline), priced)
        for (row, _), (proof, reason) in zip(priced, results):
            if proof:
                terminal[row["fixture_key"], row["revision"]] = proof
            else:
                gaps[reason] += 1
    reconciled = []
    for row, proof in candidates:
        receipt = terminal.get((row["fixture_key"], row["revision"]))
        if receipt:
            proof = proof | {"market_terminal": receipt}
        if proof != row["result"]:
            reconciled.append((row, proof))
    return reconciled, gaps


def _settle(connection, reconciled):
    finals, corrections, market_states = 0, 0, Counter()
    for row, proof in reconciled:
        previous = row["result"]
        saved = connection.execute("""
            UPDATE college_basketball_forecast SET result=%s
            WHERE model_version=%s AND fixture_key=%s AND revision=%s AND result IS NOT DISTINCT FROM %s
            """, (Jsonb(proof), model.VERSION, row["fixture_key"], row["revision"],
                  Jsonb(previous) if previous is not None else None)).rowcount
        if saved:
            finals += previous is None
            corrections += previous is not None and proof.get("previous_official_results") != previous.get("previous_official_results")
            if "market_terminal" in proof:
                market_states[proof["market_terminal"]["state"]] += 1
    return finals, corrections, market_states


def collect(connection, *, now=None, window_only=False):
    registry = cached(connection, model.KEY)
    if not registry:
        return {"state": "awaiting_registration"}
    now = now or store.db_now(connection)
    registered = feed.stamp(registry.get("registered_at"))
    if registry.get("version") != model.VERSION or registered is None or registered > now:
        raise ValueError("college_registration_invalid")
    end = now + (timedelta(minutes=30) if window_only else timedelta(hours=72))
    clock = feed._clock(now)
    eligible_day = model.season_day(now) - timedelta(days=2)
    if not connection.execute("SELECT to_regclass('football_notify.college_basketball_forecast') AS ready").fetchone()["ready"]:
        connection.execute(SCHEMA)
    fixtures = connection.execute("""
        SELECT f.id,f.revision,f.info,f.kickoff,p.fixture_key IS NOT NULL AS predicted,p.market IS NOT NULL AS priced
        FROM fixture f LEFT JOIN college_basketball_forecast p ON p.model_version=%s
              AND (p.fixture_key,p.revision)=(f.id,f.revision)
        WHERE f.info->>'league'=ANY(%s) AND f.fixture_state='scheduled' AND f.missing_scans<2
              AND f.kickoff>%s AND f.kickoff<=%s AND f.last_seen BETWEEN %s AND %s
              AND (NOT %s OR p.market IS NULL AND p.fixture_key IS NOT NULL)
        ORDER BY f.kickoff,f.id
        """, (model.VERSION, list(model.LEAGUES), now + (timedelta(minutes=20) if window_only else timedelta()),
              end, now - store.FIXTURE_FRESHNESS, now, window_only)).fetchall()
    if window_only and not fixtures:
        return {"state": "idle"}
    pending = [] if window_only else connection.execute("""
        SELECT * FROM college_basketball_forecast WHERE model_version=%s AND kickoff <=%s
          AND (result IS NULL OR (market IS NOT NULL
               AND COALESCE(result#>>'{market_terminal,state}','') NOT IN ('verified','void')))
        ORDER BY kickoff DESC
        """, (model.VERSION, now - timedelta(hours=2))).fetchall()
    saved = connection.execute("SELECT league,day,scores FROM college_basketball_day ORDER BY day").fetchall()
    date_scores = {(r["league"], r["day"]): r["scores"] for r in saved}
    requested = {(row["info"]["league"], model.season_day(row["kickoff"])) for row in fixtures
                 if not row["predicted"] or (not row["priced"] and
                     row["kickoff"] - timedelta(minutes=30) <= now < row["kickoff"] - timedelta(minutes=20))}
    # A long outage fills in history in order on successive scans. Keep
    # forecast coverage explicit while any required day is missing.
    for league in model.LEAGUES:
        missing = 0
        for offset in range(1, (eligible_day - model.SEED_THROUGH).days + 1):
            day = model.SEED_THROUGH + timedelta(days=offset)
            if (league, day) not in date_scores:
                requested.add((league, day))
                missing += 1
                if missing >= 4:
                    break
    result_days = sorted({(row["league"], model.season_day(row["kickoff"])) for row in pending},
                         key=lambda pair: pair[1], reverse=True)
    # Rotate older pending days so a permanently missing result cannot
    # starve later dates that need a verified score.
    if len(result_days) > 6:
        ix = (int(now.timestamp()) // 600 * 4) % len(result_days)
        requested.update(result_days[:2] + (result_days + result_days)[ix:ix+4])
    else:
        requested.update(result_days)
    deadline = time.monotonic() + 36
    keys = sorted(requested | {(league, day - timedelta(days=1)) for league, day in requested})
    with ThreadPoolExecutor(max_workers=4) as pool:
        boards = dict(zip(keys, pool.map(lambda key: board(*key, clock, deadline), keys)))
    event_days = {key: _event_day(boards, *key) for key in requested}
    reconciled, terminal_gaps = _reconcile(pending, event_days, clock)
    with connection.transaction():
        for key, (entries, mature) in event_days.items():
            if mature and model.SEED_THROUGH < key[1] <= eligible_day:
                scores = _scores(entries)
                date_scores[key] = scores
                connection.execute("""
                    INSERT INTO college_basketball_day(league,day,observed_at,scores) VALUES(%s,%s,%s,%s)
                    ON CONFLICT(league,day) DO UPDATE SET observed_at=excluded.observed_at,scores=excluded.scores
                        WHERE college_basketball_day.scores IS DISTINCT FROM excluded.scores
                    """, (*key, clock(), Jsonb(scores)))
        resolved, corrections, market_states = _settle(connection, reconciled)
    ratings, through = {}, {}
    for league in model.LEAGUES:
        ratings[league] = dict(registry["leagues"][league]["ratings"])
        day = date.fromisoformat(registry["leagues"][league]["through"])
        while day < eligible_day and (league, day + timedelta(days=1)) in date_scores:
            day += timedelta(days=1)
            model.update_day(ratings[league], date_scores[league, day])
        through[league] = day
    reasons, placed, newly, price_due = Counter(), Counter(), 0, []
    for row in fixtures:
        info, tip = row["info"], row["kickoff"]
        lg = info["league"]
        if row["predicted"] and (row["priced"] or not tip - timedelta(minutes=30) <= now < tip - timedelta(minutes=20)):
            placed[lg] += 1
            continue
        games, _ = event_days.get((lg, model.season_day(tip)), ({}, False))
        if not games and not boards.get((lg, model.season_day(tip))):
            reasons["board_unavailable"] += 1
            continue
        match = _matching_fixture(info, games, tip)
        if not match:
            reasons["scheduled_team_time_identity_unverified"] += 1
            continue
        source = games[match["id"]][1]
        if not model.eligible(ratings[lg], match["home_id"], match["away_id"]):
            reasons["team_history_sparse"] += 1
            continue
        if through[lg] != eligible_day:
            reasons["history_gap"] += 1
            continue
        captured = clock()
        if not source["observed_at"] <= captured < tip:
            continue
        if not row["predicted"]:
            p = model.probability(ratings[lg], match["home_id"], match["away_id"], match["neutral"])
            newly += connection.execute("""
                INSERT INTO college_basketball_forecast(model_version,fixture_key,revision,league,kickoff,observed_at,
                       official_id,home_id,away_id,neutral,ratings_through,home_probability,source_url)
                SELECT %s,f.id,f.revision,%s,f.kickoff,%s,%s,%s,%s,%s,%s,%s,%s FROM fixture f
                 WHERE f.id=%s AND f.revision=%s AND f.kickoff=%s AND f.fixture_state='scheduled'
                       AND f.missing_scans<2 AND f.kickoff>clock_timestamp()
                ON CONFLICT DO NOTHING
                """, (model.VERSION, lg, captured, match["id"], match["home_id"], match["away_id"], match["neutral"],
                      through[lg], p, source["url"], row["id"], row["revision"], tip)).rowcount
        placed[lg] += 1
        if not row["priced"] and registered <= tip - timedelta(minutes=30) <= captured < tip - timedelta(minutes=20):
            p = model.probability(ratings[lg], match["home_id"], match["away_id"], match["neutral"])
            price_due.append((row, match, {"home_probability": p, "model_observed_at": feed.utc_iso(captured),
                                           "ratings_through": str(through[lg])}))
    with ThreadPoolExecutor(max_workers=4) as pool:
        limit = time.monotonic() + 18
        prices = pool.map(lambda item: _market(item[0]["info"], clock, limit), price_due)
        captured_prices, price_gaps = 0, Counter()
        for (row, match, forecast), (price, reason) in zip(price_due, prices):
            if price:
                captured_prices += connection.execute("""
                    UPDATE college_basketball_forecast p SET market=%s FROM fixture f
                    WHERE p.model_version=%s AND p.fixture_key=%s AND p.revision=%s AND p.official_id=%s
                       AND p.home_id=%s AND p.away_id=%s AND p.neutral=%s AND p.market IS NULL
                       AND (f.id,f.revision,f.kickoff)=(p.fixture_key,p.revision,p.kickoff)
                       AND f.fixture_state='scheduled' AND f.missing_scans<2
                    """, (Jsonb(price | forecast), model.VERSION, row["id"], row["revision"], match["id"], match["home_id"],
                          match["away_id"], match["neutral"])).rowcount
            else:
                price_gaps[reason] += 1
    return {"state": "complete" if all(boards.values()) else "partial", "observed_at": feed.utc_iso(clock()),
            "registered_at": registry["registered_at"], "model_version": model.VERSION, "fixtures": len(fixtures),
            "forecast_by_league": dict(placed), "new_forecasts": newly, "gaps": dict(reasons), "verified_finals": resolved,
            "corrected_finals": corrections, "verified_market_payouts": market_states["verified"], "void_markets": market_states["void"],
            "terminal_market_gaps": dict(terminal_gaps),
            "price_window_fixtures": len(price_due), "new_strict_prices": captured_prices, "price_gaps": dict(price_gaps),
            "ratings_through": {lg: str(v) for lg, v in through.items()}, "research_only": True}


def performance(connection):
    if not connection.execute("SELECT to_regclass('football_notify.college_basketball_forecast') AS ready").fetchone()["ready"]:
        return {}
    samples = connection.execute("""
        SELECT league,official_id,kickoff,observed_at,home_probability,market IS NOT NULL AS priced,
               market->>'observed_at' AS price_at,market->>'home_probability' AS model_at_price,
               market#>>'{sides,home,market_probability}' AS price,result->>'state' AS state,
               result#>>'{market_terminal,state}' AS market_state,
               (result->>'home_score')::integer>(result->>'away_score')::integer AS home_won
          FROM college_basketball_forecast WHERE model_version=%s ORDER BY observed_at,fixture_key,revision
        """, (model.VERSION,)).fetchall()
    report = {}
    for lg in model.LEAGUES:
        selected = [row for row in samples if row["league"] == lg]
        scores, pairs, prices = {}, {}, set()
        for row in selected:
            key = row["official_id"], row["kickoff"]
            if row["priced"]:
                prices.add(key)
            if row["state"] != "final":
                continue
            win = row["home_won"]
            scores.setdefault(key, (row["home_probability"], win))
            if row["market_state"] != "verified":
                continue
            p, q = feed.number(row["model_at_price"]), feed.number(row["price"])
            if p is not None and q is not None and 0 < p < 1 and 0 < q < 1:
                priced_at = feed.stamp(row["price_at"])
                if priced_at and (key not in pairs or priced_at < pairs[key][0]):
                    pairs[key] = (priced_at, p, q, win)
        report[lg] = {"forecast_revisions": len(selected), "priced_fixtures": len(prices),
                      "discovery": model._metrics(list(scores.values())),
                      "strict_T30_model": model._metrics([(p, win) for _, p, _, win in pairs.values()]),
                      "strict_T30_market": model._metrics([(q, win) for _, _, q, win in pairs.values()])}
    return report
