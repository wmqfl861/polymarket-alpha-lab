"""Record forward football forecasts, closing quotes and regulation outcomes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import time

from psycopg import sql
from psycopg.types.json import Jsonb

import football_feed as feed
import football_model as model
import football_store as store
from polymarket_alpha_lab.papersim.pipeline import MODEL_VERSION_SOCCER


FIXED_MODEL_VERSION = "soccer-r23-fixed-20261122"
RULE = "regulation_90_plus_stoppage"
VERSION = re.compile(r"[A-Za-z0-9_.+-]{1,100}")
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}")
FORECAST_OPEN = timedelta(minutes=30)
FORECAST_END = timedelta(minutes=20)
CLOSE_OPEN = timedelta(minutes=3)
SETTLE_DELAY = timedelta(minutes=100)
SCHEMA_LOCK, COLLECT_LOCK = 8, 9
CONTRACT_KEY = ("fixture_key", "fixture_revision", "market_id", "outcome")
FORECAST_KEY = (*CONTRACT_KEY, "model_version")
SCHEMA = """
CREATE TABLE IF NOT EXISTS football_notify.football_forward_contract (
    fixture_key text NOT NULL, fixture_revision integer NOT NULL,
    market_id text NOT NULL, outcome text NOT NULL,
    kickoff timestamptz NOT NULL, league text NOT NULL,
    fixture_id text NOT NULL, team_ids text[] NOT NULL, provider_league_id text NOT NULL,
    market_kind text NOT NULL CHECK (market_kind IN ('total','team_total','btts','match_winner')),
    side text NOT NULL, line double precision, team_side text, matchweek integer,
    condition_id text NOT NULL, token_id text NOT NULL, rule text NOT NULL,
    source_url text NOT NULL, evidence jsonb NOT NULL,
    PRIMARY KEY (fixture_key, fixture_revision, market_id, outcome)
);
CREATE TABLE IF NOT EXISTS football_notify.football_forward_observation (
    fixture_key text NOT NULL, fixture_revision integer NOT NULL,
    market_id text NOT NULL, outcome text NOT NULL, model_version text NOT NULL,
    league text NOT NULL, market_kind text NOT NULL, side text NOT NULL,
    line double precision, team_side text, matchweek integer, full_matchweek boolean,
    cohort text NOT NULL CHECK (cohort IN ('official','shadow')),
    decision text NOT NULL CHECK (decision IN ('accepted','rejected','unavailable')),
    reason_codes text[] NOT NULL, executable boolean NOT NULL, counterfactual_pass boolean NOT NULL,
    kickoff timestamptz NOT NULL, observed_at timestamptz NOT NULL,
    model_observed_at timestamptz, book_observed_at timestamptz,
    model_probability double precision CHECK (model_probability BETWEEN 0 AND 1),
    baseline_probability double precision CHECK (baseline_probability BETWEEN 0 AND 1),
    min_probability double precision CHECK (min_probability BETWEEN 0 AND 1),
    market_price double precision CHECK (market_price > 0 AND market_price < 1),
    market_price_basis text, bid_price double precision, ask_price double precision,
    entry_price double precision, fee_rate double precision,
    net_edge double precision, worst_edge double precision, adjusted_edge double precision,
    evidence jsonb NOT NULL,
    CHECK (observed_at < kickoff),
    CHECK (model_observed_at IS NULL OR model_observed_at <= observed_at),
    CHECK (book_observed_at IS NULL OR book_observed_at <= observed_at),
    CHECK (NOT executable OR entry_price IS NOT NULL),
    CHECK (decision <> 'accepted' OR (cohort = 'official' AND executable AND counterfactual_pass)),
    PRIMARY KEY (fixture_key, fixture_revision, market_id, outcome, model_version),
    FOREIGN KEY (fixture_key, fixture_revision, market_id, outcome)
        REFERENCES football_notify.football_forward_contract
);
CREATE TABLE IF NOT EXISTS football_notify.football_forward_recommendation (
    fixture_key text NOT NULL, fixture_revision integer NOT NULL,
    market_id text NOT NULL, outcome text NOT NULL, model_version text NOT NULL,
    stage text NOT NULL, kickoff timestamptz NOT NULL, issued_at timestamptz NOT NULL,
    book_observed_at timestamptz NOT NULL, valid_until timestamptz NOT NULL,
    entry_price double precision NOT NULL CHECK (entry_price > 0 AND entry_price < 1),
    model_probability double precision NOT NULL CHECK (model_probability BETWEEN 0 AND 1),
    net_edge double precision NOT NULL, worst_edge double precision NOT NULL,
    adjusted_edge double precision NOT NULL, evidence jsonb NOT NULL,
    CHECK (book_observed_at <= issued_at AND issued_at < valid_until AND valid_until <= kickoff),
    PRIMARY KEY (fixture_key, fixture_revision, market_id, outcome, model_version, stage, book_observed_at)
);
CREATE TABLE IF NOT EXISTS football_notify.football_forward_chat_watch (
    fixture_key text NOT NULL, fixture_revision integer NOT NULL,
    market_id text NOT NULL, outcome text NOT NULL, model_version text NOT NULL,
    source_id text NOT NULL, kickoff timestamptz NOT NULL, quoted_at timestamptz NOT NULL,
    published_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
    price double precision CHECK (price > 0 AND price < 1),
    model_probability double precision CHECK (model_probability BETWEEN 0 AND 1),
    simulated_stake numeric NOT NULL DEFAULT 0 CHECK (simulated_stake = 0), evidence jsonb NOT NULL,
    CHECK (quoted_at <= published_at AND published_at < kickoff AND published_at <= recorded_at),
    PRIMARY KEY (source_id, fixture_key, fixture_revision, market_id, outcome)
);
CREATE TABLE IF NOT EXISTS football_notify.football_forward_close (
    fixture_key text NOT NULL, fixture_revision integer NOT NULL,
    market_id text NOT NULL, outcome text NOT NULL, model_version text NOT NULL,
    kickoff timestamptz NOT NULL, observed_at timestamptz NOT NULL,
    price double precision NOT NULL CHECK (price > 0 AND price < 1),
    price_basis text NOT NULL, bid_price double precision NOT NULL, ask_price double precision NOT NULL,
    entry_price double precision, evidence jsonb NOT NULL,
    CHECK (observed_at < kickoff),
    PRIMARY KEY (fixture_key, fixture_revision, market_id, outcome, model_version),
    FOREIGN KEY (fixture_key, fixture_revision, market_id, outcome)
        REFERENCES football_notify.football_forward_contract
);
CREATE TABLE IF NOT EXISTS football_notify.football_forward_result (
    fixture_key text NOT NULL, fixture_revision integer NOT NULL,
    market_id text NOT NULL, outcome text NOT NULL, model_version text NOT NULL,
    kickoff timestamptz NOT NULL, observed_at timestamptz NOT NULL,
    state text NOT NULL CHECK (state IN ('resolved','void')),
    won boolean, home_goals smallint, away_goals smallint, source_url text NOT NULL, evidence jsonb NOT NULL,
    CHECK (observed_at > kickoff),
    CHECK ((state = 'resolved' AND won IS NOT NULL AND home_goals IS NOT NULL AND away_goals IS NOT NULL
              AND home_goals >= 0 AND away_goals >= 0)
        OR (state = 'void' AND won IS NULL AND home_goals IS NULL AND away_goals IS NULL)),
    PRIMARY KEY (fixture_key, fixture_revision, market_id, outcome, model_version),
    FOREIGN KEY (fixture_key, fixture_revision, market_id, outcome)
        REFERENCES football_notify.football_forward_contract
);
CREATE TABLE IF NOT EXISTS football_notify.football_forward_work (
    fixture_key text NOT NULL, fixture_revision integer NOT NULL, phase text NOT NULL,
    next_at timestamptz, reason text NOT NULL, attempts integer NOT NULL,
    PRIMARY KEY (fixture_key, fixture_revision, phase)
);
CREATE INDEX IF NOT EXISTS football_forward_contract_kickoff ON football_notify.football_forward_contract (kickoff);
CREATE INDEX IF NOT EXISTS football_forward_recommendation_kickoff ON football_notify.football_forward_recommendation (kickoff);
"""


def ensure_schema(connection):
    found = connection.execute(
        "SELECT to_regclass('football_notify.football_forward_chat_watch') IS NOT NULL AS ready"
    ).fetchone()
    if found["ready"]:
        return
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s, %s)", (store.LOCK_NAMESPACE, SCHEMA_LOCK))
        connection.execute(SCHEMA)


def _insert(connection, table, row):
    columns = sql.SQL(", ").join(map(sql.Identifier, row))
    params = sql.SQL(", ").join(sql.Placeholder() for _ in row)
    query = sql.SQL("INSERT INTO football_notify.{} ({}) VALUES ({}) ON CONFLICT DO NOTHING")
    values = [Jsonb(value) if name == "evidence" else value for name, value in row.items()]
    return connection.execute(query.format(sql.Identifier(table), columns, params), values).rowcount


def _work(connection, key, phase, reason, next_at):
    connection.execute(
        """INSERT INTO football_notify.football_forward_work
           (fixture_key, fixture_revision, phase, next_at, reason, attempts) VALUES (%s,%s,%s,%s,%s,1)
           ON CONFLICT (fixture_key, fixture_revision, phase) DO UPDATE SET
              next_at = excluded.next_at, reason = excluded.reason,
              attempts = football_forward_work.attempts + 1""",
        (*key, phase, next_at, reason),
    )


def _identity(record, fields):
    return {name: record[name] for name in fields}


def _foot_id(info):
    return tuple(feed.numeric_id(info.get(field)) for field in (
        "fixture_id", "fotmob_league_id", "fotmob_home_id", "fotmob_away_id",
    ))


def _get_match(identity, clock, deadline):
    if not all(identity) or identity[2] == identity[3] or time.monotonic() >= deadline:
        return None
    try:
        payload = feed.fotmob.fetch_match_details(
            identity[0], allow_public_fetch=True, timeout=7, budget=feed.public_budget(deadline, requests=2),
        )
    except Exception:
        return None
    general, status, actual = model._prematch_identity(payload)
    if actual == identity:
        return payload, general, status, clock()
    return None


def _prematch(match, kickoff):
    if match is None:
        return False
    payload, general, status, observed = match
    return (observed < kickoff and feed.stamp(general.get("matchTimeUTCDate")) == kickoff
            and feed.stamp(status.get("utcTime")) == kickoff and payload.get("ongoing") is False
            and all(status.get(flag) is False for flag in ("started", "finished", "cancelled"))
            and status.get("postponed") is not True and status.get("awarded") is not True)


def _market_url(market_id):
    return f"{feed.pm.GAMMA_BASE_URL}/markets/{market_id}"


def _book_url(token):
    return f"{feed.pm.CLOB_BOOK_URL}?token_id={token}"


def _market_data(market_id, clock, deadline):
    try:
        data, at = feed._public(_market_url(market_id), feed.public_budget(deadline), clock)
    except Exception:
        return None, None
    return feed.record(data), at


def _direct_match(market, live, kickoff):
    if (not live or not feed._open_market(live) or not feed._regulation(live)
            or feed.stamp(live.get("gameStartTime")) != kickoff
            or feed.number(live.get("line")) != market.line
            or market.raw["description"].split() != live["description"].split()):
        return False
    fields = ("id", "slug", "question", "conditionId", "sportsMarketType", "groupItemTitle", "resolutionSource")
    try:
        return (all(str(live.get(name)) == str(market.raw.get(name)) for name in fields)
                and feed._contract_pairs(live) == feed._contract_pairs(market.raw))
    except (ValueError, TypeError):
        return False


def _markets(info, kickoff, clock, deadline):
    market_list = {}
    complete = True
    refs = feed._gamma_event_refs(info, expanded=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for ref, event in zip(refs, pool.map(lambda ref: feed._refresh_event(info, ref, clock, deadline), refs)):
            if not event:
                if ref[1] is not None or ref[0] == info["base_slug"]:
                    complete = False
                continue
            if (event.get("active") is not True or event.get("closed") is not False
                    or feed._gamma_state(event) != "scheduled" or feed.stamp(event.get("startTime")) != kickoff):
                complete = False
                continue
            for market in feed._event_markets(info, event, kickoff):
                if feed._regulation(market.raw):
                    market_list[market.condition_id] = market
    return sorted(market_list.values(), key=feed._contract_order), complete


def _books(tokens, clock, deadline):
    chunks = [tokens[index:index + feed._BATCH_SIZE] for index in range(0, len(tokens), feed._BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        books = {token: book for result in pool.map(lambda chunk: feed._book_batch(chunk, clock, deadline), chunks)
                 for token, book in result.items()}
        missing = [token for token in tokens if token not in books][:6]
        books.update(pool.map(lambda token: feed._book_single(token, clock, deadline), missing))
    return books


def _quote(condition, token, book, received, kickoff, earliest):
    if (not isinstance(book, dict) or received is None or received >= kickoff
            or book.get("asset_id") != token or book.get("market") != condition):
        return None
    try:
        parsed = feed.pm._parse_order_book(token, book)
    except (TypeError, ValueError, feed.pm._MarketRejected):
        return None
    observed = parsed.book_observed_at
    if observed is None or not earliest <= observed <= received or received - observed > feed.BOOK_AGE:
        return None
    asks = [(level.price, level.size) for level in parsed.asks if level.size > 0]
    ask = asks[0][0] if asks else None
    bid = next((level.price for level in parsed.bids if level.size > 0), None)
    if (ask is not None and not 0 < ask < 1 or bid is not None and not 0 < bid < 1
            or bid is not None and ask is not None and bid > ask):
        return None
    midpoint = (bid + ask) / 2 if bid is not None and ask is not None and bid < ask else None
    walk = feed.gates.walk_book(asks, side="ask", notional=250)
    return {"observed_at": observed, "received_at": received, "asks": asks,
            "bid": bid, "ask": ask, "midpoint": midpoint, "entry_price": walk.vwap if walk.filled else None,
            "filled_usd": walk.filled_notional, "near_two_cent_depth": walk.depth_near2c}


def _contracts(fixture, markets, identity, matchweek, complete):
    for market in markets:
        for option in market.options:
            yield market, option, {
                "fixture_key": fixture["id"], "fixture_revision": fixture["revision"],
                "market_id": market.id, "outcome": option.outcome, "kickoff": fixture["kickoff"],
                "league": fixture["info"]["league"], "fixture_id": identity[0],
                "provider_league_id": identity[1], "team_ids": list(identity[2:]), "market_kind": market.kind,
                "side": option.side, "line": market.line, "team_side": market.team_side, "matchweek": matchweek,
                "condition_id": market.condition_id, "token_id": option.token_id, "rule": RULE,
                "source_url": _market_url(market.id),
                "evidence": {"event_slug": market.event_slug, "source_url": _market_url(market.id),
                             "market_slug": market.raw["slug"], "description": market.raw["description"],
                             "pairs": feed._contract_pairs(market.raw), "complete_market_snapshot": complete,
                             "regulation_verified": True, "resolution_source": market.raw.get("resolutionSource")},
            }


@dataclass(frozen=True)
class ModelContext:
    version: str
    observed_at: datetime | None
    projection: tuple | None
    research: object | None = None


def _model_contexts(info, kickoff, now, cache, done, domestic_registration=None):
    if info["league"] in feed.MODELED_LEAGUES:
        projection = feed._model_projection(info, now)
        data = feed.record(info.get("model"))
        observed = feed.stamp(data.get("observed_at")) if projection else None
        contexts = [ModelContext(MODEL_VERSION_SOCCER, observed, projection)]
        registered_at = feed.stamp(feed.record(domestic_registration).get("registered_at"))
        if projection and registered_at and registered_at <= kickoff - FORECAST_OPEN:
            from football_totals_calibration import make_shadow_context

            candidate = make_shadow_context(info, now, domestic_registration, projection=projection)
            if candidate and candidate.model_version not in done:
                contexts.append(ModelContext(candidate.model_version, candidate.model_observed_at,
                                             candidate.projection, candidate))
        return info, contexts
    from football_research import available_forecasts

    candidates = available_forecasts(info, now, cache)
    contexts, keys, conflicts, identities = {}, {}, set(), set()
    pinned = _foot_id(info)
    for candidate in candidates:
        if candidate is None:
            continue
        candidate_info = candidate.fixture
        identity = _foot_id(candidate_info)
        version, observed_at = candidate.model_version, candidate.model_observed_at
        digest = candidate.evidence.get("sha256")
        registration = feed.stamp(candidate.evidence.get("registered_at"))
        if (not isinstance(version, str) or not VERSION.fullmatch(version)
                or not isinstance(observed_at, datetime) or observed_at.utcoffset() is None or observed_at > now
                or not store.is_hex(digest)
                or "registered_at" in candidate.evidence and (registration is None or registration > observed_at)
                or candidate_info.get("league") != info["league"] or candidate_info.get("id") != info.get("id")
                or feed.stamp(candidate_info.get("kickoff")) != kickoff or not all(identity)
                or any(left is not None and left != right for left, right in zip(pinned, identity))):
            continue
        identities.add(identity)
        if version in done:
            continue
        key = (observed_at, identity, digest)
        if version in keys and keys[version] != key:
            conflicts.add(version)
        elif version not in keys:
            keys[version] = key
            contexts[version] = candidate
    if len(identities) != 1:
        return info, []
    official = dict(zip(("fixture_id", "fotmob_league_id", "fotmob_home_id", "fotmob_away_id"),
                        identities.pop()))
    return info | official, [ModelContext(version, candidate.model_observed_at, candidate.projection, candidate)
                             for version, candidate in contexts.items() if version not in conflicts]


def _numbers(value):
    value = feed.record(value)
    probability = feed.number(value.get("model_median"))
    inputs = value.get("qs_variants")
    if not isinstance(inputs, (tuple, list)):
        return None
    variants = tuple(feed.number(item) for item in inputs)
    if (probability is None or not 0 <= probability <= 1 or not variants
            or any(item is None or not 0 <= item <= 1 for item in variants)):
        return None
    return {"model_median": probability, "qs_variants": variants}


def _gate(market, option, quote, book, live, news, projection):
    fee = feed._fee_rate(live) if live is not None else None
    liquidity = feed.number((live or {}).get("liquidityNum"))
    if liquidity is None:
        liquidity = feed.number((live or {}).get("liquidity"))
    reasons = []
    if fee is None or liquidity is None or liquidity < 0:
        reasons.append("fee_or_liquidity_unverified")
    if not projection:
        reasons.append("model_input_unavailable")
    if not quote:
        reasons.append("model_quote_unavailable")
    if not book or book["entry_price"] is None:
        reasons.append("fill_250")
    if reasons:
        return fee, None, reasons
    data, _, (home, away) = projection
    hits = data.get("over_2_5_hits")
    if (market.kind, option.side) in (("total", "over"), ("btts", "yes")) and (type(hits) is not int or not 0 <= hits <= 10):
        return fee, None, ["form_unavailable"]
    risky, _ = feed.gates.under_risk_check(
        kind=market.kind, side=option.side, o25_hits=hits or 0, lambda_home=home, lambda_away=away,
        lambda_total=home + away, line=market.line if market.line is not None else 2.5,
    )
    report = feed.gates.evaluate_candidate(
        quote, book["asks"], gamma_liquidity=liquidity, news=news, fee_rate=fee, under_risk=risky,
    )
    return fee, report, list(report.decision.failed)


def _observation(contract, market, option, context, quote, book, live, market_at,
                 fixture_match, news, news_reason, capture_at, admitted):
    projection, research = context.projection, context.research
    baseline = _numbers(research.baseline_quote(market, option)) if research else None
    fee, report, reasons = _gate(market, option, quote, book, live, news, projection)
    if not live:
        reasons.append("direct_contract_unverified")
    if not _prematch(fixture_match, contract["kickoff"]):
        reasons.append("independent_fixture_unverified")
    if not news.concordant:
        reasons.append(news_reason)
    shadow = not admitted
    passed = report is not None and report.decision.passed and not reasons
    if shadow:
        reasons.append("shadow_policy")
    if quote is None or live is None or book is None or fixture_match is None:
        decision = "unavailable"
    else:
        decision = "accepted" if passed and not shadow else "rejected"
    market_price = book["midpoint"] if book else None
    fields = (*CONTRACT_KEY, "league", "market_kind", "side", "line", "team_side", "matchweek", "kickoff")
    return _identity(contract, fields) | {
        "model_version": context.version, "cohort": "shadow" if shadow else "official", "decision": decision,
        "reason_codes": sorted(set(reasons)), "executable": bool(book and book["entry_price"] is not None),
        "counterfactual_pass": passed, "observed_at": capture_at, "model_observed_at": context.observed_at,
        "book_observed_at": book["observed_at"] if book else None,
        "model_probability": quote["model_median"] if quote else None,
        "baseline_probability": baseline["model_median"] if baseline else None,
        "min_probability": min(quote["qs_variants"]) if quote else None, "market_price": market_price,
        "market_price_basis": "clob_midpoint" if market_price is not None else None,
        "bid_price": book["bid"] if book else None, "ask_price": book["ask"] if book else None,
        "entry_price": book["entry_price"] if book else None, "fee_rate": fee,
        "net_edge": report.exec_edge if report else None, "worst_edge": report.worst_variant_edge if report else None,
        "adjusted_edge": report.exec_edge_haircut if report else None,
        "evidence": contract["evidence"] | {
            "rule": RULE, "fixture_id": contract["fixture_id"], "condition_id": contract["condition_id"],
            "token_id": contract["token_id"], "market_url": contract["source_url"],
            "market_observed_at": feed.utc_iso(market_at) if market_at else None,
            "fixture_source_url": model.details_url(contract["fixture_id"]),
            "fixture_observed_at": feed.utc_iso(fixture_match[3]) if fixture_match else None,
            "book_source_url": _book_url(contract["token_id"]),
            "book_received_at": feed.utc_iso(book["received_at"]) if book else None,
            "filled_usd": book["filled_usd"] if book else None,
            "near_two_cent_depth": book["near_two_cent_depth"] if book else None,
            "model_digest": projection[0].get("sha256") if projection else None,
            "model_source_url": (research.evidence.get("calendar_source") if research else
                                 projection[0].get("source") if projection else None),
            "variants": list(quote["qs_variants"]) if quote else [],
            "research": research.evidence if research else None, "lineup_sources": list(news.sources),
            "gamma_reference_price": option.reference, "book_has_two_sides": market_price is not None,
        },
    }


def _capture(fixture, now, cache, active, domestic_registration=None):
    from football_admission import scope_key

    kickoff = fixture["kickoff"]
    clock = feed._clock(now)
    deadline = time.monotonic() + 21
    completed_versions = set(fixture["completed_versions"])
    info, contexts = _model_contexts(fixture["info"], kickoff, now, cache, completed_versions,
                                    domestic_registration)
    identity = _foot_id(info)
    if not contexts or not all(identity) or now >= kickoff:
        return [], "existing_versions_current" if completed_versions else "model_or_fixture_unavailable", set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        match_work = pool.submit(_get_match, identity, clock, deadline)
        markets, complete = _markets(info, kickoff, clock, deadline)
        match = match_work.result()
    if not markets:
        return [], "no_verified_market_identity", set()
    if match and not _prematch(match, kickoff):
        return [], "fixture_no_longer_prematch", set()
    quotes = {}
    for market in markets:
        for option in market.options:
            use = []
            for context in contexts:
                if context.research:
                    quote = _numbers(context.research.market_quote(market, option))
                    if quote is None:
                        continue
                else:
                    quote = _numbers(feed._model_quote(market, option, context.projection[1])) if context.projection else None
                use.append((context, quote))
            if use:
                quotes[(market.id, option.outcome)] = use
    markets = [market for market in markets if any((market.id, option.outcome) in quotes for option in market.options)]
    if not markets:
        return [], "no_registered_market", set()
    round_id = feed.numeric_id(match[1].get("matchRound")) if match else None
    matchweek = int(round_id) if round_id and 1 <= int(round_id) <= 60 else None
    context = feed._lineup_evidence(match[0], info, match[3], model.details_url(identity[0])) if match else None
    lineup_audit = {}
    news, news_reason = feed._news(info, context, kickoff - FORECAST_OPEN, clock, audit=lineup_audit)
    tokens = list(dict.fromkeys(option.token_id for market in markets for option in market.options))
    with ThreadPoolExecutor(max_workers=8) as pool:
        direct = {market.id: pool.submit(_market_data, market.id, clock, deadline) for market in markets}
        raw_books = _books(tokens, clock, deadline)
        live = {market.id: direct[market.id].result() for market in markets}
    observed_at = clock()
    if observed_at >= kickoff:
        return [], "kickoff_reached", set()
    captured = []
    version_status = {}
    for market, option, contract in _contracts(fixture, markets, identity, matchweek, complete):
        raw, market_at = live[market.id]
        verified = _direct_match(market, raw, kickoff) and market_at < kickoff
        book = _quote(market.condition_id, option.token_id, *raw_books.get(option.token_id, (None, None)),
                      kickoff, now - feed.BOOK_AGE)
        contract["evidence"]["direct_contract_verified"] = verified
        for context, quote in quotes.get((market.id, option.outcome), ()):
            scope = scope_key(info["league"], market.kind, market.line, market.team_side, context.version)
            admitted = not fixture.get("legacy_id") and scope in active and feed.gates.suspension_policy(market.kind).eligible
            observed = _observation(
                contract, market, option, context, quote, book, raw if verified else None, market_at if verified else None,
                match, news, news_reason, observed_at, admitted,
            )
            observed["evidence"]["lineup_review"] = lineup_audit
            version_status[context.version] = (version_status.get(context.version, complete)
                                               and observed["decision"] != "unavailable")
            captured.append((contract, observed))
    completed = {version for version, complete in version_status.items() if complete}
    expected = {context.version for context in contexts}
    if info["league"] in feed.MODELED_LEAGUES and complete:
        # A fixture may have a verified market list without any registered match total.
        expected.intersection_update(version_status)
    state = "captured" if completed == expected else "partial_capture"
    return captured, state, completed


def _same_fixture(connection, fixture, now):
    current = connection.execute(
        """SELECT revision, kickoff, fixture_state, missing_scans, last_seen FROM football_notify.fixture
           WHERE id = %s FOR SHARE""", (fixture["id"],),
    ).fetchone()
    return (current and current["revision"] == fixture["revision"] and current["kickoff"] == fixture["kickoff"]
            and current["fixture_state"] == "scheduled" and current["missing_scans"] < 2
            and timedelta(0) <= now - current["last_seen"] <= store.FIXTURE_FRESHNESS)


def _quoted_row(connection, identity, before):
    return connection.execute(
        """SELECT evidence FROM football_notify.football_forward_contract
           WHERE fixture_key = %(fixture_key)s AND fixture_revision = %(fixture_revision)s
             AND market_id = %(market_id)s AND outcome = %(outcome)s AND kickoff > %(before)s""",
        identity | {"before": before},
    ).fetchone()


def _issue_evidence(info, actual):
    return {
        "fixture": {name: info.get(name) for name in ("id", "league", "base_slug", "home", "away", "home_alias",
                    "away_alias", "fixture_id", "fotmob_league_id", "fotmob_home_id", "fotmob_away_id")},
        "market_url": actual.get("url"), "market_kind": actual.get("market_kind"), "side": actual.get("side"),
        "line": feed.number(actual.get("line")), "team_side": actual.get("team_side"),
        "condition_id": actual.get("condition_id"), "token_id": actual.get("token_id"),
        "model_digest": actual.get("model_digest"), "fee_rate": feed.number(actual.get("fee_rate")),
    }


def record_recommendation(connection, fixture_row, entry, *, selected_at=None, signal=None):
    """Record the actual selected paper direction, using the original issue quote."""
    ensure_schema(connection)
    current = store.db_now(connection)
    issue_at = store.timestamp(selected_at) if selected_at is not None else current
    kickoff = store.timestamp(fixture_row["kickoff"])
    quote_at, expiry = store.timestamp(entry.observed_at), store.timestamp(entry.valid_until)
    if (fixture_row != entry.row or fixture_row.get("stage") not in {"t90", "t30"}
            or not quote_at <= issue_at <= current < expiry <= kickoff or issue_at - quote_at > feed.BOOK_AGE
            or not feed.numeric_id(entry.market_id) or entry.outcome not in {"Yes", "No", "Over", "Under"}):
        return False
    if (not 0 < entry.price < 1 or not 0 <= entry.model_probability <= 1
            or any(not -1 <= edge <= 1 for edge in (entry.net_edge, entry.worst_edge, entry.adjusted_edge))):
        return False
    info = fixture_row["info"]
    legacy = fixture_row.get("legacy_id")
    actual = feed.record(signal)
    signaled_version = actual.get("model_version")
    version = signaled_version if signaled_version is not None else FIXED_MODEL_VERSION if legacy else MODEL_VERSION_SOCCER
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        return False
    source_fields = {"market_id": entry.market_id, "outcome": entry.outcome, "price": entry.price,
                     "model_probability": entry.model_probability}
    if (any(field in actual and actual[field] != expected for field, expected in source_fields.items())
            or "book_observed_at" in actual and feed.stamp(actual["book_observed_at"]) != quote_at):
        return False
    identity = {"fixture_key": fixture_row["fixture_key"], "fixture_revision": fixture_row["revision"],
                "market_id": entry.market_id, "outcome": entry.outcome}
    contract = _quoted_row(connection, identity, issue_at)
    evidence = _issue_evidence(info, actual) | {
        "legacy_fixture_id": legacy, "direction": entry.direction, "quote_basis": "ask_vwap_250",
        "contract_known_at_issue": bool(contract and contract["evidence"].get("direct_contract_verified")),
    }
    return bool(_insert(connection, "football_forward_recommendation", identity | {
        "model_version": version,
        "stage": fixture_row["stage"], "kickoff": kickoff, "book_observed_at": quote_at, "issued_at": issue_at,
        "valid_until": expiry, "entry_price": entry.price, "model_probability": entry.model_probability,
        "net_edge": entry.net_edge, "worst_edge": entry.worst_edge, "adjusted_edge": entry.adjusted_edge, "evidence": evidence,
    }))


def record_chat_watch(connection, fixture_row, signal, *, quoted_at, published_at, source_id):
    """Import a timestamped chat watch with zero simulated stake."""
    ensure_schema(connection)
    current = store.db_now(connection)
    kickoff = store.timestamp(fixture_row["kickoff"])
    quoted, published = store.timestamp(quoted_at), store.timestamp(published_at)
    market_id, outcome = feed.numeric_id(signal.get("market_id")), signal.get("outcome")
    version = signal.get("model_version") or "chat-observation"
    price, probability = feed.number(signal.get("price")), feed.number(signal.get("model_probability"))
    label = feed.clean_label(signal.get("direction"), 100)
    if (not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id)
            or not isinstance(version, str) or not VERSION.fullmatch(version)
            or not market_id or outcome not in ("Yes", "No", "Over", "Under") or not label
            or not quoted <= published < kickoff or published > current
            or signal.get("price") is not None and (price is None or not 0 < price < 1)
            or signal.get("model_probability") is not None and (probability is None or not 0 <= probability <= 1)):
        return False
    info = fixture_row["info"]
    return bool(_insert(connection, "football_forward_chat_watch", {
        "fixture_key": fixture_row["fixture_key"], "fixture_revision": fixture_row["revision"],
        "market_id": market_id, "outcome": outcome, "model_version": version, "source_id": source_id,
        "kickoff": kickoff, "quoted_at": quoted, "published_at": published, "recorded_at": current,
        "price": price, "model_probability": probability,
        "evidence": _issue_evidence(info, signal) | {"direction": label, "published_in": "chat"},
    }))


def _rehydrate(recommendation, now):
    """Bind an already-issued direction to its exact contract without re-quoting it."""
    pinned = recommendation["evidence"]
    info = pinned["fixture"]
    fixture = {"id": recommendation["fixture_key"], "revision": recommendation["fixture_revision"],
               "kickoff": recommendation["kickoff"], "info": info}
    identity = _foot_id(info)
    slug = info.get("base_slug")
    if not all(identity) or not isinstance(slug, str) or not feed._SLUG.fullmatch(slug):
        return None
    clock = feed._clock(now)
    raw, at = _market_data(recommendation["market_id"], clock, time.monotonic() + 8)
    if (not raw or feed.numeric_id(raw.get("id")) != recommendation["market_id"]
            or feed.stamp(raw.get("gameStartTime")) != fixture["kickoff"] or not feed._regulation(raw)):
        return None
    contract_slug, condition = raw.get("slug"), raw.get("conditionId")
    if (not isinstance(contract_slug, str) or not contract_slug.startswith(slug + "-")
            or not isinstance(condition, str) or not feed._CONDITION_ID.fullmatch(condition)):
        return None
    try:
        pairs = feed._contract_pairs(raw)
        parsed = feed._market_options(info, raw, contract_slug.removeprefix(slug + "-"), pairs, (None, None))
    except (TypeError, ValueError):
        return None
    if not parsed:
        return None
    kind, line, side, options = parsed
    facts = {"market_kind": kind, "line": line, "team_side": side, "condition_id": condition}
    if any(pinned.get(name) is not None and pinned[name] != value for name, value in facts.items()):
        return None
    market = feed.Market(slug, raw, recommendation["market_id"], condition, kind, line, side, options)
    for _, option, contract in _contracts(fixture, [market], identity, None, False):
        if option.outcome == recommendation["outcome"]:
            if (pinned.get("side") not in (None, option.side) or pinned.get("token_id") not in (None, option.token_id)):
                return None
            contract["evidence"].update(direct_contract_verified=True, contract_observed_at=feed.utc_iso(at))
            return contract
    return None


def _closing_quotes(contracts, now):
    first = contracts[0]
    kickoff = first["kickoff"]
    clock = feed._clock(now)
    deadline = time.monotonic() + min(10, max(0, (kickoff - now).total_seconds()))
    identity = (first["fixture_id"], first["provider_league_id"], *first["team_ids"])
    match = _get_match(identity, clock, deadline)
    if not _prematch(match, kickoff):
        return []
    contracts = _verified_contracts(contracts, clock, deadline)
    tokens = list(dict.fromkeys(row["token_id"] for row in contracts))
    books = _books(tokens, clock, deadline)
    output = []
    for contract in contracts:
        token = contract["token_id"]
        orderbook = _quote(contract["condition_id"], token, *books.get(token, (None, None)), kickoff, kickoff - CLOSE_OPEN)
        if orderbook is None or orderbook["midpoint"] is None:
            continue
        output.append(_identity(contract, FORECAST_KEY) | {
            "kickoff": kickoff, "observed_at": orderbook["observed_at"], "price": orderbook["midpoint"],
            "price_basis": "clob_midpoint", "bid_price": orderbook["bid"], "ask_price": orderbook["ask"],
            "entry_price": orderbook["entry_price"], "evidence": {
                "condition_id": contract["condition_id"], "token_id": token, "source_url": _book_url(token),
                "received_at": feed.utc_iso(orderbook["received_at"]), "fixture_source_url": model.details_url(identity[0]),
                "fixture_observed_at": feed.utc_iso(match[3]), "filled_usd": orderbook["filled_usd"],
                "near_two_cent_depth": orderbook["near_two_cent_depth"],
            },
        })
    return output


def _regulation_result(match, kickoff):
    if match is None:
        return None
    payload, general, status, observed = match
    reason = feed.record(status.get("reason"))
    halves = feed.record(status.get("halfs"))
    if (not kickoff < observed or feed.stamp(general.get("matchTimeUTCDate")) != kickoff
            or feed.stamp(status.get("utcTime")) != kickoff or status.get("started") is not True
            or status.get("finished") is not True or status.get("cancelled") is not False
            or payload.get("ongoing") is not False
            or status.get("awarded") is True or status.get("postponed") is True
            or reason.get("shortKey") != "fulltime_short" or reason.get("short") != "FT"
            or status.get("whoLostOnPenalties") or halves.get("firstExtraHalfStarted")
            or halves.get("secondExtraHalfStarted")):
        return None
    score = model._score(status.get("scoreStr"))
    if score is None or len(score) != 2:
        return None
    return score


def _winning_selection(contract, score):
    home, away = score
    kind, side = contract["market_kind"], contract["side"]
    line = contract["line"]
    if kind == "match_winner":
        expected = "home" if home > away else "away" if away > home else "draw"
        return expected != side[:-3] if side.endswith("_no") else expected == side
    if kind == "btts":
        return ((home > 0 and away > 0) == (side == "yes"))
    goals = home + away if kind == "total" else home if contract["team_side"] == "home" else away
    return goals > line if side == "over" else goals < line


def _pinned_contract(contract, raw):
    evidence = contract["evidence"]
    if (not raw or feed.numeric_id(raw.get("id")) != contract["market_id"]
            or raw.get("conditionId") != contract["condition_id"]
            or raw.get("slug") != evidence["market_slug"]
            or raw.get("resolutionSource") != evidence.get("resolution_source")
            or not feed._regulation(raw) or raw["description"].split() != evidence["description"].split()):
        return False
    try:
        return tuple(map(tuple, evidence["pairs"])) == feed._contract_pairs(raw)
    except (TypeError, ValueError):
        return False


def _verified_contracts(contracts, clock, deadline):
    unverified = {row["market_id"]: row for row in contracts if not row["evidence"].get("direct_contract_verified")}
    if not unverified:
        return contracts
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = pool.map(lambda key: _market_data(key, clock, deadline), unverified)
        verified = {key for key, (raw, _) in zip(unverified, responses)
                    if _pinned_contract(unverified[key], raw)}
    return [row for row in contracts if row["evidence"].get("direct_contract_verified") or row["market_id"] in verified]


def _terminal_quote(contract, clock, deadline):
    raw, at = _market_data(contract["market_id"], clock, deadline)
    if (not _pinned_contract(contract, raw)
            or raw.get("closed") is not True or raw.get("umaResolutionStatus") != "resolved"):
        return None
    try:
        prices = feed.pm._maybe_json_list(raw.get("outcomePrices"))
    except (TypeError, ValueError):
        return None
    payout = tuple(feed.number(price) for price in prices) if prices else ()
    if payout == (0.5, 0.5):
        return at
    return None


def _settle(contracts, now):
    first = contracts[0]
    identity = (first["fixture_id"], first["provider_league_id"], *first["team_ids"])
    kickoff = first["kickoff"]
    clock = feed._clock(now)
    deadline = time.monotonic() + 14
    match = _get_match(identity, clock, deadline)
    score = _regulation_result(match, kickoff)
    terminal = {}
    if score is not None:
        contracts = _verified_contracts(contracts, clock, deadline)
    elif now >= kickoff + timedelta(hours=3) or match and match[2].get("cancelled") is True:
        by_id = {row["market_id"]: row for row in contracts}
        with ThreadPoolExecutor(max_workers=8) as pool:
            evidence = pool.map(lambda row: _terminal_quote(row, clock, deadline), by_id.values())
            terminal = dict(zip(by_id, evidence))
    results = []
    for row in contracts:
        market_id = row["market_id"]
        at = match[3] if score is not None else terminal.get(market_id)
        if at is None or at <= kickoff:
            continue
        results.append(_identity(row, FORECAST_KEY) | {
            "kickoff": kickoff, "observed_at": at, "state": "resolved" if score is not None else "void",
            "won": _winning_selection(row, score) if score is not None else None,
            "home_goals": score[0] if score is not None else None, "away_goals": score[1] if score is not None else None,
            "source_url": model.details_url(identity[0]) if score is not None else _market_url(market_id),
            "evidence": {"fixture_id": identity[0], "team_ids": list(identity[2:]), "condition_id": row["condition_id"],
                         "token_id": row["token_id"], "rule": RULE, "source_status": "FT" if score is not None else "resolved_50_50"},
        })
    return results


def _due_forecasts(connection, now):
    from football_research import COMPETITIONS

    modeled = list(feed.MODELED_LEAGUES)
    supported = sorted(set(modeled) | set(COMPETITIONS))
    return connection.execute(
        """SELECT f.id, f.revision, f.kickoff, f.info, f.legacy_id, scope.phase AS forecast_phase,
                  ARRAY(SELECT substr(done.phase, 10) FROM football_notify.football_forward_work done
                        WHERE (done.fixture_key,done.fixture_revision)=(f.id,f.revision)
                          AND done.phase LIKE 'forecast:%%' AND done.next_at IS NULL
                          AND done.reason='captured') AS completed_versions
           FROM football_notify.fixture f
           CROSS JOIN LATERAL (SELECT CASE WHEN f.info->>'league'=ANY(%s)
                              THEN 'forecast' ELSE 'forecast_versions' END AS phase) scope
           LEFT JOIN football_notify.football_forward_work w
             ON (w.fixture_key,w.fixture_revision,w.phase) = (f.id,f.revision,scope.phase)
           WHERE f.kickoff > %s AND f.kickoff <= %s AND f.fixture_state = 'scheduled'
             AND f.missing_scans < 2 AND f.last_seen BETWEEN %s AND %s
             AND f.info->>'league'=ANY(%s)
             AND (w.fixture_key IS NULL OR w.next_at <= %s)
           ORDER BY (scope.phase='forecast') DESC, f.kickoff, f.id LIMIT 12""",
        (modeled, now + FORECAST_END, now + FORECAST_OPEN, now - store.FIXTURE_FRESHNESS, now, supported, now),
    ).fetchall()


def _due_recommendations(connection, now):
    return connection.execute(
        """WITH issued AS (
            SELECT fixture_key, fixture_revision, market_id, outcome, kickoff, evidence, issued_at AS publication
            FROM football_notify.football_forward_recommendation
            UNION ALL
            SELECT fixture_key, fixture_revision, market_id, outcome, kickoff, evidence, published_at
            FROM football_notify.football_forward_chat_watch
        ), unmet AS (
            SELECT DISTINCT ON (r.fixture_key,r.fixture_revision,r.market_id,r.outcome) r.*
            FROM issued r
            LEFT JOIN football_notify.football_forward_work w
                 ON (w.fixture_key,w.fixture_revision,w.phase) =
                    (r.fixture_key,r.fixture_revision,'contract:'||r.market_id||':'||r.outcome)
            WHERE r.publication <= %s AND (w.next_at IS NULL OR w.next_at <= %s)
              AND NOT EXISTS (SELECT 1 FROM football_notify.football_forward_contract c
                  WHERE (c.fixture_key,c.fixture_revision,c.market_id,c.outcome) =
                        (r.fixture_key,r.fixture_revision,r.market_id,r.outcome))
            ORDER BY r.fixture_key,r.fixture_revision,r.market_id,r.outcome,r.publication
           )
           SELECT * FROM unmet ORDER BY (kickoff <= %s), kickoff, fixture_key LIMIT 16""",
        (now, now, now),
    ).fetchall()


def _due_contracts(connection, now, phase):
    phase_table = {"close": "football_forward_close", "settle": "football_forward_result"}[phase]
    query = sql.SQL("""
        WITH issued AS (
            SELECT fixture_key, fixture_revision, market_id, outcome, model_version, evidence
            FROM football_notify.football_forward_recommendation
            UNION ALL
            SELECT fixture_key, fixture_revision, market_id, outcome, model_version, evidence
            FROM football_notify.football_forward_chat_watch
        ), tracked AS (
            SELECT fixture_key, fixture_revision, market_id, outcome, model_version
            FROM football_notify.football_forward_observation
            UNION
            SELECT i.fixture_key, i.fixture_revision, i.market_id, i.outcome, i.model_version
            FROM issued i JOIN football_notify.football_forward_contract c USING (fixture_key,fixture_revision,market_id,outcome)
            WHERE coalesce(i.evidence->>'condition_id', c.condition_id) = c.condition_id
                AND coalesce(i.evidence->>'token_id', c.token_id) = c.token_id
                AND coalesce(i.evidence->>'market_kind', c.market_kind) = c.market_kind
                AND coalesce(i.evidence->>'side', c.side) = c.side
                AND coalesce(i.evidence->>'team_side', c.team_side) IS NOT DISTINCT FROM c.team_side
                AND (i.evidence->>'line' IS NULL OR (i.evidence->>'line')::double precision = c.line)
        ), pending AS (
            SELECT c.*, t.model_version,
                   dense_rank() OVER (ORDER BY coalesce(w.next_at,c.kickoff), c.fixture_key, c.fixture_revision) AS fixture_number
            FROM tracked t
            JOIN football_notify.football_forward_contract c USING (fixture_key,fixture_revision,market_id,outcome)
            LEFT JOIN football_notify.football_forward_work w
                 ON (w.fixture_key,w.fixture_revision,w.phase)=(c.fixture_key,c.fixture_revision,%s)
            WHERE {} AND (w.next_at IS NULL OR w.next_at <= %s)
              AND NOT EXISTS (SELECT 1 FROM football_notify.{} done
                WHERE (done.fixture_key,done.fixture_revision,done.market_id,done.outcome,done.model_version) =
                      (t.fixture_key,t.fixture_revision,t.market_id,t.outcome,t.model_version))
        )
        SELECT * FROM pending WHERE fixture_number <= 12
        ORDER BY fixture_number, market_id, outcome, model_version
    """)
    if phase == "close":
        bounds = sql.SQL("c.kickoff > %s AND c.kickoff <= %s")
        times = (now, now + CLOSE_OPEN)
    else:
        bounds = sql.SQL("c.kickoff <= %s")
        times = (now - SETTLE_DELAY,)
    records = connection.execute(query.format(bounds, sql.Identifier(phase_table)), (phase, *times, now)).fetchall()
    grouped = {}
    for row in records:
        grouped.setdefault((row["fixture_key"], row["fixture_revision"]), []).append(row)
    return grouped


def _collect_forecasts(connection, now):
    from football_admission import active_scopes
    from football_totals_calibration import read_registered

    due = _due_forecasts(connection, now)
    if not due:
        return 0, 0
    source = connection.execute("SELECT payload FROM football_notify.source_cache WHERE cache_key = 'research/1'").fetchone()
    cache = feed.record(source["payload"]) if source else {}
    registration = read_registered(connection) if any(row["info"]["league"] in feed.MODELED_LEAGUES for row in due) else None
    active = active_scopes(connection)
    inserted = 0

    def capture(row):
        try:
            return _capture(row, now, cache, active, registration)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, OverflowError):
            return [], "invalid_fixture_evidence", set()

    with ThreadPoolExecutor(max_workers=4) as pool:
        for fixture, (captured, reason, completed) in zip(due, pool.map(capture, due)):
            key = (fixture["id"], fixture["revision"])
            phase = fixture["forecast_phase"]
            with connection.transaction():
                current = store.db_now(connection)
                last_chance = current >= fixture["kickoff"] - FORECAST_END - timedelta(minutes=1, seconds=40)
                if not _same_fixture(connection, fixture, current) or current >= fixture["kickoff"]:
                    _work(connection, key, phase, "fixture_revision_changed", None)
                    continue
                registered = set()
                contracts = set()
                for contract, observation in captured:
                    if (not observation["observed_at"] <= current
                            or not fixture["kickoff"] - FORECAST_OPEN <= observation["observed_at"] < fixture["kickoff"] - FORECAST_END
                            or observation["decision"] == "unavailable" and not last_chance):
                        continue
                    market = (contract["market_id"], contract["outcome"])
                    if market not in contracts:
                        _insert(connection, "football_forward_contract", contract)
                        contracts.add(market)
                    inserted += _insert(connection, "football_forward_observation", observation)
                    registered.add(observation["model_version"])
                if phase == "forecast_versions":
                    for version in registered & completed:
                        _work(connection, key, "forecast:" + version, "captured", None)
                    # A newly registered version can enter the active window after
                    # the reference completed its own observations.
                    next_at = current + timedelta(seconds=30) if current + timedelta(seconds=30) < fixture["kickoff"] - FORECAST_END else None
                else:
                    next_at = None if reason == "captured" or last_chance else current + timedelta(seconds=30)
                _work(connection, key, phase, reason, next_at)
    return len(due), inserted


def _collect_contract_phase(connection, now, phase, fetch, table):
    due = _due_contracts(connection, now, phase)
    inserted = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (key, batch), outcomes in zip(due.items(), pool.map(lambda rows: fetch(rows, now), due.values())):
            with connection.transaction():
                current = store.db_now(connection)
                accepted = [row for row in outcomes if row["observed_at"] <= current]
                for row in accepted:
                    inserted += _insert(connection, table, row)
                if len(accepted) == len(batch):
                    next_at = None
                elif phase == "close":
                    next_at = current + timedelta(seconds=25)
                else:
                    delay = timedelta(minutes=5) if current - batch[0]["kickoff"] < timedelta(hours=6) else timedelta(hours=1)
                    next_at = current + delay
                _work(connection, key, phase, "captured" if next_at is None else "source_pending", next_at)
    return len(due), inserted


def collect(connection):
    """Run bounded collection on the validated local notification connection."""
    ensure_schema(connection)
    lock = (store.LOCK_NAMESPACE, COLLECT_LOCK)
    granted = connection.execute("SELECT pg_try_advisory_lock(%s,%s) AS granted", lock).fetchone()["granted"]
    if not granted:
        return {"state": "already_running"}
    try:
        now = store.db_now(connection)
        pending = _due_recommendations(connection, now)
        hydrated = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for issued, contract in zip(pending, pool.map(lambda row: _rehydrate(row, now), pending)):
                if contract:
                    hydrated += _insert(connection, "football_forward_contract", contract)
                else:
                    current = store.db_now(connection)
                    delay = timedelta(seconds=45) if current < issued["kickoff"] else timedelta(minutes=20)
                    phase = f"contract:{issued['market_id']}:{issued['outcome']}"
                    _work(connection, (issued["fixture_key"], issued["fixture_revision"]),
                          phase, "source_pending", current + delay)
        # The last pre-kickoff quote has the shortest deadline.
        closing_fixtures, closing_quotes = _collect_contract_phase(
            connection, store.db_now(connection), "close", _closing_quotes, "football_forward_close"
        )
        forecast_fixtures, forecasts = _collect_forecasts(connection, store.db_now(connection))
        settling_fixtures, results = _collect_contract_phase(
            connection, store.db_now(connection), "settle", _settle, "football_forward_result"
        )
        return {"state": "collected", "closing_fixtures": closing_fixtures, "closing_quotes": closing_quotes,
                "forecast_fixtures": forecast_fixtures, "observations": forecasts,
                "recommendation_contracts": hydrated, "settling_fixtures": settling_fixtures, "results": results}
    finally:
        connection.execute("SELECT pg_advisory_unlock(%s,%s)", lock)
