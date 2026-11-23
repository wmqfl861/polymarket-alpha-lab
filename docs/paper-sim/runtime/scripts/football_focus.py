"""Find verified pregame football observations using admitted models and live books."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import time

import football_admission as admission
import football_feed as feed
import football_forward as forward
import football_model as model
import football_shortlist as shortlist
import football_store as store
from sports_portfolio import RESTART_KEY


MAX_SCAN_SECONDS = 24
WORKERS = 8
MARKET_LABELS = {"match_winner": "胜平负", "total": "全场总进球",
                 "team_total": "球队总进球", "btts": "双方进球"}
TEAM_LABELS = {"6504": "蒙扎", "8535": "佛罗伦萨", "9937": "布伦特福德", "8668": "埃弗顿",
               "8581": "莱万特", "10268": "埃尔切", "8634": "巴塞罗那", "10205": "比利亚雷亚尔"}


@dataclass(frozen=True)
class Fixture:
    row: dict
    projections: tuple
    report: dict

    @property
    def info(self):
        return self.row["info"]

    @property
    def kickoff(self):
        return self.row["kickoff"]

    @property
    def key(self):
        return self.row["id"]


def _projection_data(projection):
    return getattr(projection, "projection", projection)[0]


def _version(projection):
    return getattr(projection, "model_version", feed.MODEL_VERSION_SOCCER)


def _scope(fixture, market, version):
    return admission.scope_key(fixture.info["league"], market.kind, market.line, market.team_side, version)


def _inventory(connection, now, end, active):
    ready, reports, counts = [], [], Counter()
    eligible_models = {(key[0], key[4]) for key in active}
    rows = connection.execute(
        """SELECT f.id,f.revision,f.kickoff,f.info,f.last_seen,f.missing_scans,
                  EXISTS (SELECT 1 FROM football_notify.sports_portfolio_position p
                          WHERE p.fixture_key=f.id AND p.restart_key=%s) AS entered
           FROM football_notify.fixture f
           WHERE f.fixture_state='scheduled' AND f.kickoff>%s AND f.kickoff<=%s
                 AND coalesce(f.info->>'sport','football')='football'
           ORDER BY f.kickoff,f.id""",
        (RESTART_KEY, now, end),
    ).fetchall()
    for row in rows:
        info = row["info"]
        counts["scheduled"] += 1
        if info.get("league") not in feed.MODELED_LEAGUES and not info.get("research"):
            counts["no_model"] += 1
            continue
        report = {"fixture_key": row["id"], "kickoff": row["kickoff"], "league": info["league"],
                  "home": info.get("home"), "away": info.get("away")}
        reports.append(report)
        if (row["missing_scans"] >= 2 or not timedelta(0) <= now - row["last_seen"] <= store.FIXTURE_FRESHNESS
                or feed.stamp(info.get("kickoff")) != row["kickoff"] or info.get("state") != "scheduled"):
            report["status"] = "fixture_stale_or_changed"
            continue
        projections = tuple(p for p in feed._paper_projections(info, now)
                            if store.is_hex(_projection_data(p).get("sha256")))
        if not projections:
            report["status"] = "model_unavailable"
            continue
        counts["fresh_model"] += 1
        report["model_versions"] = [_version(p) for p in projections]
        admitted = tuple(p for p in projections if (info["league"], _version(p)) in eligible_models)
        if not admitted:
            report["status"] = "scope_not_admitted"
        elif not all(forward._foot_id(info)):
            report["status"] = "independent_fixture_missing"
        elif row["entered"]:
            report["status"] = "formal_position_exists"
        else:
            ready.append(Fixture(row, admitted, report))
    return ready, reports, counts


def _paired_books(market, books, checked, kickoff):
    pair = {}
    for option in market.options:
        raw, received = books.get(option.token_id, (None, None))
        quote = forward._quote(market.condition_id, option.token_id, raw, received,
                               kickoff, checked - feed.BOOK_AGE)
        if (quote is None or quote["received_at"] > checked or quote["midpoint"] is None
                or quote["entry_price"] is None):
            return None
        evidence = {"token_id": option.token_id, "condition_id": market.condition_id,
                    "observed_at": feed.utc_iso(quote["observed_at"]), "asks": quote["asks"], "bid": quote["bid"]}
        pair[option.token_id] = quote | {"sha256": feed.document_digest(evidence)}
    if len(pair) != 2:
        return None
    eps = feed.gates.GATE_EPSILON
    if (sum(book["bid"] for book in pair.values()) > 1 + eps
            or sum(book["ask"] for book in pair.values()) < 1 - eps):
        return None
    return pair


def _screen(market, option, book, projection, raw):
    data = getattr(projection, "projection", projection)
    quote = forward._numbers(projection.market_quote(market, option) if hasattr(projection, "market_quote")
                             else feed._model_quote(market, option, data[1]))
    if quote is None or len(quote["qs_variants"]) != 10:
        return None, ("model_variants_unavailable",)
    fee, gate, failures = forward._gate(market, option, quote, book, raw, None, data)
    blockers = tuple(reason for reason in failures if reason != "news_concordant")
    if gate is None or blockers:
        return None, blockers
    cost = feed.gates.fee_cost(gate.entry_price, fee)
    if gate.worst_variant_edge <= cost:
        return None, ("worst_edge_after_fees",)
    return (gate, quote, fee, cost), ()


def _fixture_lineup(fixture, match, clock):
    if not forward._prematch(match, fixture.kickoff):
        return None, "independent_fixture_unverified"
    expected = feed._lineup_evidence(match[0], fixture.info, match[3], model.details_url(fixture.info["fixture_id"]))
    context = feed.record(expected)
    current = clock()
    if current >= fixture.kickoff:
        return None, "at_or_after_kickoff"
    if not timedelta(0) <= current - match[3] <= feed.BOOK_AGE:
        return None, "lineups_stale"
    published = feed.confirmed_review_opening(fixture.info)
    if published and published <= current < fixture.kickoff - feed.LINEUP_BASELINE_CUTOFF:
        return None, "current_formal_review"
    if any(set(line.get("starters", ())) & feed.record(line.get("absences")).keys()
           for line in feed.rows(context.get("lineups"))):
        return None, "lineup_conflict"
    prior = feed._news_baseline(fixture.info, context, match[3], allow_absences_only=True)
    if not feed._complete_lineups(context):
        # A verified, still unpublished XI can support an early zero-stake watch.
        # Missing availability, contradictory data or a retracted XI cannot.
        if (fixture.info.get("league") in feed.MODELED_LEAGUES
                and current < fixture.kickoff - timedelta(minutes=feed._STAGE_WINDOWS["t90"][0])
                and feed._availability_snapshot(context) and feed._same_lineup_fixture(fixture.info, context)
                and all(side.get("starters_unpublished") is True for side in context["lineups"])
                and not feed._complete_lineups(prior)
                and (not prior or all(a["absences"] == b["absences"]
                                      for a, b in zip(prior["lineups"], context["lineups"], strict=True)))):
            return context, "已核对双方伤停，正式首发待公布"
        return None, "lineups_incomplete"
    if feed._complete_lineups(prior) and any(
        any(before[field] != after[field] for field in ("starters", "formation", "absences"))
        or before.get("goalkeeper") and after.get("goalkeeper") and before["goalkeeper"] != after["goalkeeper"]
        for before, after in zip(prior["lineups"], context["lineups"], strict=True)
    ):
        return None, "lineup_conflict"

    for lead, _ in feed._STAGE_WINDOWS.values():
        opening = fixture.kickoff - timedelta(minutes=lead)
        if opening <= current < opening + feed._LATE:
            news, state = feed._news(fixture.info, context, opening, clock)
            if news.concordant:
                return None, "current_formal_review"
            if state == "news_changed":
                return None, "lineup_conflict"
            reason = "双方仍为预测首发，待核正式首发" if context["lineup_type"] == "predicted" else "待核首发基线"
            return context, reason
        if current < opening:
            reason = "双方为预测首发" if context["lineup_type"] == "predicted" else "双方首发已核"
            return context, f"{reason}，待 T−{lead} 复核"
    return context, "双方为预测首发，等正式首发" if context["lineup_type"] == "predicted" else "双方首发已核，临场观察"


def _book_evidence(option, book):
    return {"token_id": option.token_id, "outcome": option.outcome, "bid": book["bid"], "ask": book["ask"],
            "price": book["entry_price"], "filled_usd": book["filled_usd"],
            "near_two_cent_depth": book["near_two_cent_depth"], "observed_at": book["observed_at"],
            "sha256": book["sha256"], "url": forward._book_url(option.token_id)}


def _candidate(fixture, market, option, projection, result, pair, context, reason, live_contract, checked):
    matched = fixture.info
    gate, quote, fee, cost = result
    names = dict(matched)
    display = {}
    for side, label in zip(("home", "away"), shortlist._teams(matched), strict=True):
        preferred = TEAM_LABELS.get(matched[f"fotmob_{side}_id"], label) if label == matched.get(side) else label
        display[f"{side}_display"] = names[f"{side}_alias"] = preferred
    direction = shortlist._display_direction(
        {"info": names}, {"market_kind": market.kind, "side": option.side, "outcome": option.outcome,
                          "line": market.line, "team_side": market.team_side}, option.label,
    )
    model_data = _projection_data(projection)
    paired_at = min(book["observed_at"] for book in pair.values())
    lineup_at = feed.stamp(context["observed_at"])
    model_at = feed.stamp(model_data.get("observed_at"))
    valid = min(fixture.kickoff, paired_at + feed.BOOK_AGE, lineup_at + feed.BOOK_AGE,
                model_at + model.MODEL_AGE, fixture.row["last_seen"] + store.FIXTURE_FRESHNESS)
    if checked >= valid:
        return None
    terms = {"market_id": market.id, "condition_id": market.condition_id, "line": market.line,
             "options": [(item.outcome, item.token_id) for item in market.options], "fee_rate": fee,
             "settlement": live_contract.get("description"), "kickoff": feed.utc_iso(fixture.kickoff)}
    return {
        "sport": "football", "league": feed.LEAGUE_NAMES.get(matched["league"], matched.get("league_name") or matched["league"]),
        "league_code": matched["league"], "fixture_key": fixture.key, "revision": fixture.row["revision"],
        "independent_identity": forward._foot_id(matched),
        "kickoff": fixture.kickoff, "home": matched["home"], "away": matched["away"], **display,
        "direction": direction, "market_kind": market.kind, "market_label": MARKET_LABELS[market.kind],
        "line": market.line, "team_side": market.team_side, "side": option.side, "outcome": option.outcome,
        "market_id": market.id, "token_id": option.token_id, "condition_id": market.condition_id,
        "model_version": _version(projection), "probability": quote["model_median"],
        "conservative_probability": min(quote["qs_variants"]),
        "price": gate.entry_price, "fee_rate": fee, "fee_per_share": cost, "price_with_fee": gate.entry_price + cost,
        "edge": gate.exec_edge, "adjusted_edge": gate.exec_edge_haircut, "worst_edge": gate.worst_variant_edge,
        "worst_net_edge": gate.worst_variant_edge - cost, "variant_spread": gate.variant_spread,
        "quoted_at": paired_at, "valid_until": valid, "reason": reason, "status": "watch", "stake_fen": 0,
        "book": {side.side: _book_evidence(side, pair[side.token_id]) for side in market.options},
        "market_terms_sha256": feed.document_digest(terms),
        "market_url": f"https://polymarket.com/event/{market.event_slug}/{market.raw['slug']}",
        "independent_fixture_url": context["source"], "lineup_observed_at": lineup_at,
        "lineup_type": context["lineup_type"], "lineup_digest": context["sha256"], "model_digest": model_data["sha256"],
        "lineup_pending": not feed._complete_lineups(context),
    }


def _read_current(connection, entries):
    rows = connection.execute(
        """SELECT f.id,f.revision,f.kickoff,f.fixture_state,f.last_seen,f.missing_scans,f.info,
                  EXISTS (SELECT 1 FROM football_notify.sports_portfolio_position p
                          WHERE p.fixture_key=f.id AND p.restart_key=%s) AS entered
           FROM football_notify.fixture f WHERE f.id=ANY(%s)""",
        (RESTART_KEY, sorted({entry["fixture_key"] for entry in entries})),
    ).fetchall()
    return {row["id"]: row for row in rows}


def _rank(entry):
    value = feed._roi_rank(entry["probability"], entry["price"], entry["fee_rate"],
                           entry["conservative_probability"])
    return (*value, entry["kickoff"],
            entry["market_id"], entry["fixture_key"], entry["model_version"])


def _still_current(entries, connection, clock, reports):
    if not entries:
        return []
    latest = _read_current(connection, entries)
    active = admission.active_scopes(connection)
    checked = clock()
    chosen, seen = [], set()
    models = {}
    for entry in sorted(entries, key=_rank):
        key = entry["fixture_key"]
        if reports[key]["status"] == "focus_candidate":
            continue
        row = latest.get(key)
        scope = admission.scope_key(entry["league_code"], entry["market_kind"], entry["line"],
                                    entry["team_side"], entry["model_version"])
        if (not row or row["revision"] != entry["revision"] or row["kickoff"] != entry["kickoff"]
                or row["fixture_state"] != "scheduled" or row["entered"] or row["missing_scans"] >= 2
                or not timedelta(0) <= checked - row["last_seen"] <= store.FIXTURE_FRESHNESS
                or checked >= entry["valid_until"] or scope not in active
                or forward._foot_id(row["info"]) != entry["independent_identity"]):
            reports[key]["status"] = "expired_or_changed_on_recheck"
            continue
        if key not in models:
            models[key] = {(_version(projection), _projection_data(projection)["sha256"])
                           for projection in feed._paper_projections(row["info"], checked)}
        if (entry["model_version"], entry["model_digest"]) not in models[key]:
            reports[key]["status"] = "model_changed_on_recheck"
            continue
        identity = entry["independent_identity"]
        if identity in seen:
            reports[key]["status"] = "duplicate_fixture"
            continue
        seen.add(identity)
        reports[key]["status"] = "focus_candidate"
        chosen.append(entry)
    return chosen


def scan(connection, now, end):
    """Evaluate every fresh football projection in the window, with a shared HTTP deadline."""
    if (not isinstance(now, datetime) or not isinstance(end, datetime) or now.utcoffset() is None
            or end.utcoffset() is None or end <= now):
        raise ValueError("football focus requires an increasing aware time window")
    now = now.astimezone(UTC)
    clock = feed._clock(now)
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    active = admission.active_scopes(connection)
    ready, reports, counts = _inventory(connection, now, end, active)
    market_tasks, book_tokens = {}, set()

    def listings(fixture):
        if time.monotonic() >= deadline:
            return None
        return forward._markets(fixture.info, fixture.kickoff, clock, deadline)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for fixture, found in zip(ready, pool.map(listings, ready), strict=True):
            if found is None:
                fixture.report["status"] = "http_budget_exhausted"
                continue
            markets, complete = found
            if not complete:
                fixture.report["status"] = "http_budget_exhausted" if time.monotonic() >= deadline else "market_listing_incomplete"
                continue
            eligible = [(market, p) for market in markets if feed.gates.suspension_policy(market.kind).eligible
                        for p in fixture.projections if _scope(fixture, market, _version(p)) in active]
            fixture.report["eligible_markets"] = len({market.id for market, _ in eligible})
            if not eligible:
                fixture.report["status"] = "no_verified_contract_in_scope"
                continue
            market_tasks[fixture.key] = eligible
            book_tokens.update(option.token_id for market, _ in eligible for option in market.options)

    books = forward._books(sorted(book_tokens), clock, deadline) if book_tokens and time.monotonic() < deadline else {}
    checked = clock()
    potential = {}
    for fixture in ready:
        eligible = market_tasks.get(fixture.key, ())
        if not eligible:
            continue
        pairs = {market.id: _paired_books(market, books, checked, fixture.kickoff) for market, _ in eligible}
        failure_counts, shortlisted = Counter(), []
        for market, projection in eligible:
            pair = pairs[market.id]
            if pair is None:
                continue
            for option in market.options:
                result, failures = _screen(market, option, pair[option.token_id], projection, market.raw)
                if not result:
                    failure_counts.update(failures)
                    continue
                shortlisted.append((market, option, projection, pair))
        paired_count = sum(pair is not None for pair in pairs.values())
        fixture.report.update(paired_books=paired_count, missing_book_pairs=len(pairs) - paired_count,
                              listing_screen_passes=len(shortlisted), gate_rejections=dict(failure_counts))
        if shortlisted:
            potential[fixture.key] = shortlisted
        elif not any(pairs.values()):
            fixture.report["status"] = "http_budget_exhausted" if time.monotonic() >= deadline else "paired_book_unavailable"
        else:
            fixture.report["status"] = "numeric_gates_failed" if paired_count == len(pairs) else "partial_paired_books"

    entries = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        identities = {fixture.key: pool.submit(forward._get_match, forward._foot_id(fixture.info), clock, deadline)
                      for fixture in ready if fixture.key in potential and time.monotonic() < deadline}
        markets = {market.id: (market, fixture.kickoff) for fixture in ready
                   for market, _, _, _ in potential.get(fixture.key, ())}
        verifications = {key: pool.submit(feed._verify_live_contract, market, kickoff, clock, deadline)
                         for key, (market, kickoff) in markets.items() if time.monotonic() < deadline}
        for fixture in ready:
            if fixture.key not in potential:
                continue
            task = identities.get(fixture.key)
            match = task.result() if task is not None else None
            context, reason = _fixture_lineup(fixture, match, clock)
            if context is None:
                fixture.report["status"] = "http_budget_exhausted" if match is None and time.monotonic() >= deadline else reason
                continue
            fixture.report["status"] = "direct_contract_unverified"
            for market, option, projection, pair in potential[fixture.key]:
                verification = verifications.get(market.id)
                raw = verification.result() if verification is not None else None
                if raw is None:
                    if time.monotonic() >= deadline:
                        fixture.report["status"] = "http_budget_exhausted"
                    continue
                result, _ = _screen(market, option, pair[option.token_id], projection, raw)
                if result is None:
                    fixture.report["status"] = "numeric_gates_failed_after_direct_check"
                    continue
                entry = _candidate(fixture, market, option, projection, result, pair, context, reason, raw, clock())
                if entry is not None:
                    entries.append(entry)
                else:
                    fixture.report["status"] = "expired_or_changed_on_recheck"
    result = _still_current(entries, connection, clock, {row["fixture_key"]: row for row in reports})
    return {"as_of": clock(), "window_start": now, "window_end": end, "scheduled_fixtures": counts["scheduled"],
            "fresh_model_fixtures": counts["fresh_model"], "fixtures_without_a_model": counts["no_model"],
            "http_budget_seconds": MAX_SCAN_SECONDS, "fixtures_checked": len(market_tasks),
            "eligible_contracts": sum(row.get("eligible_markets", 0) for row in reports),
            "paired_contracts": sum(row.get("paired_books", 0) for row in reports),
            "status_counts": dict(Counter(row["status"] for row in reports)), "fixtures": reports, "candidates": result}


def candidates(connection, now, end):
    """Return at most one executable zero-stake observation per fixture."""
    return scan(connection, now, end)["candidates"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, choices=range(1, 25), metavar="1-24")
    args = parser.parse_args()
    with store.connect(read_only=True) as connection:
        now = store.db_now(connection)
        report = scan(connection, now, now + timedelta(hours=args.hours))
    print(json.dumps(report, default=feed.utc_iso, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
