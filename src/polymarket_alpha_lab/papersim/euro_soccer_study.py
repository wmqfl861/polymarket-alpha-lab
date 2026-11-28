"""Prospective team-news and market observations in the managed football study."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys

from . import euro_soccer_markets as markets
from . import euro_soccer_sources as sources
from .euro_soccer_markets import HORIZON, capture_slot
from .euro_soccer_model import MODEL_VERSION, forecast, parse_prematch_news
from .euro_soccer_outcomes import score_outcome
from .euro_soccer_store import StudyStore
from .store_psycopg import connect_managed


CALENDAR_TTL = timedelta(minutes=10)
ARCHIVE_TTL = timedelta(days=7)
SOURCE_ERRORS = (OSError, ValueError, KeyError, TypeError)


class FixtureChanged(ValueError):
    """A fresh match report contradicts the scheduled game or its pregame status."""


def utc_now():
    return datetime.now(UTC)


def _key(f):
    season = f.season.replace("/", "-")
    kickoff = f.kickoff.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{MODEL_VERSION}/{f.league}/{f.fixture_id}/{season}/{f.phase}/{f.home_id}-{f.away_id}/{kickoff}/"


def _identity(f):
    return dict(league=f.league, season=f.season, phase=f.phase, fixture_id=f.fixture_id,
                home_id=f.home_id, away_id=f.away_id, home_name=f.home_name, away_name=f.away_name,
                kickoff=f.kickoff.isoformat())


def _calendar(store, league, season, ttl):
    evidence = store.fetch(sources.calendar_url(league, season), max_age=ttl)
    return sources.parse_calendar(league, season, evidence.raw, observed_at=evidence.received_at)


def _observe(store, target, calendar, listings, discovery_gaps, slot, clock):
    ident = _identity(target)
    gaps = list(discovery_gaps)
    details = {}
    start = clock()
    if capture_slot(target.kickoff, start) != slot:
        return None
    for game in sources.recent_fixtures(target, calendar, as_of=start):
        try:
            item = store.fetch(sources.match_url(game.fixture_id), max_age=ARCHIVE_TTL)
            details[game.fixture_id] = sources.parse_history_detail(game, item.raw, observed_at=item.received_at)
        except SOURCE_ERRORS:
            gaps.append("unavailable_history_detail/" + game.fixture_id)

    news = None
    url = sources.match_url(target.fixture_id)
    try:
        item = store.fetch(url)
        news = parse_prematch_news(target, item.raw, source_url=url, fetched_at=item.received_at,
                                   registered_sha256=item.sha256, as_of=clock())
    except SOURCE_ERRORS:
        gaps.append("unavailable_prematch_news")
    if news and {"news_not_prematch", "news_fixture_identity_mismatch"}.intersection(news.blockers):
        raise FixtureChanged()

    as_of = clock()
    if capture_slot(target.kickoff, as_of) != slot:
        return None
    history = sources.team_history(target, calendar, details, as_of=as_of)
    prediction = forecast(target, history, news=news, as_of=as_of)
    prices = markets.observe_markets(target.league, target.home_name, target.away_name,
                                    target.kickoff, listings, store.fetch, fixture_id=target.fixture_id)
    observed = clock()
    if capture_slot(target.kickoff, observed) != slot:
        return None
    if prices["observed_at"] is not None:
        received = datetime.fromisoformat(prices["observed_at"])
        if not as_of <= received <= observed or capture_slot(target.kickoff, received) != slot:
            return None
    calendar_receipts = sorted({(g.season, g.source_sha256, g.observed_at.isoformat()) for g in calendar})
    return {"kind": "observation", "fixture": ident, **ident, "slot": slot, "model_version": MODEL_VERSION,
            "observation_id": _key(target) + slot, "observed_at": observed.isoformat(),
            "calendar_sha256": target.source_sha256, "forecast": prediction, "markets": prices,
            "best_ask_edges": markets.research_edges(prediction, prices),
            "calendar_receipts": [{"season": season, "sha256": digest, "received_at": stamp}
                                  for season, digest, stamp in calendar_receipts],
            "history_detail_receipts": {fid: {"sha256": row.source_sha256, "received_at": row.observed_at.isoformat()}
                                        for fid, row in sorted(details.items())},
            "source_gaps": gaps, "research_only": True, "amount_cny": 0}


def _record_results(store, calendar, keys, clock):
    count = 0
    for game in calendar:
        if game.status != "finished" or game.phase != "regular":
            continue
        prefix = _key(game)
        if prefix + "result" in keys or not any(prefix + slot in keys for slot in ("forward", "closing", "preview")):
            continue
        observed = clock()
        scores = {}
        for slot in ("preview", "forward", "closing"):
            observation = store.latest(prefix + slot) if prefix + slot in keys else None
            scores[slot] = score_outcome(observation, game, as_of=observed) if observation else None
        if not any(scores.values()):
            continue
        ident = _identity(game)
        store.save(prefix + "result", {"kind": "result", **ident, "model_version": MODEL_VERSION,
                   "score": list(game.score), "calendar_sha256": game.source_sha256,
                   "observed_at": observed.isoformat(), **scores, "research_only": True, "amount_cny": 0})
        count += 1
    return count


def scan_league(store, league, *, clock=utc_now):
    at = clock()
    seasons = sources.seasons_for(league, at)
    report = {"league": league, "captured": {}, "results_added": 0, "gaps": []}
    try:
        current = _calendar(store, league, seasons[1], CALENDAR_TTL)
    except SOURCE_ERRORS:
        report["gaps"].append("current_calendar_unavailable")
        return report
    keys = store.keys(f"{MODEL_VERSION}/{league}/")
    due = []
    for game in current:
        slot = capture_slot(game.kickoff, at)
        if (game.status == "scheduled" and game.phase == "regular" and slot is not None
                and _key(game) + slot not in keys):
            due.append((slot, game))
    # Closing and forward windows are finite. Spread the unhurried previews over ticks.
    rank = {"closing": 0, "forward": 1, "preview": 2}
    due.sort(key=lambda pair: (rank[pair[0]], pair[1].kickoff, pair[1].fixture_id))
    due_now = [pair for pair in due if pair[0] != "preview"] + [pair for pair in due if pair[0] == "preview"][:1]
    previous = ()
    previous_prefix = "/" + seasons[0].replace("/", "-") + "/"
    if due_now or any(previous_prefix in key for key in keys):
        try:
            previous = _calendar(store, league, seasons[0], timedelta(days=1))
        except SOURCE_ERRORS:
            report["gaps"].append("previous_calendar_unavailable")
    calendar = previous + current
    if due_now:
        discovered, gaps = markets.discover_markets(league, at, at + HORIZON + timedelta(seconds=1), store.fetch)
        report["gaps"].extend(gaps)
        common_gaps = tuple(report["gaps"])
        captured = Counter()
        for slot, game in due_now:
            if capture_slot(game.kickoff, clock()) != slot:
                report["gaps"].append("capture_window_elapsed/" + game.fixture_id)
                continue
            try:
                doc = _observe(store, game, calendar, discovered, common_gaps, slot, clock)
            except FixtureChanged:
                report["gaps"].append("fresh_match_report_conflicts_with_schedule/" + game.fixture_id)
                continue
            if doc is None:
                report["gaps"].append("capture_window_elapsed/" + game.fixture_id)
                continue
            store.save(_key(game) + slot, doc)
            captured[slot] += 1
        report["captured"] = dict(captured)
    report["results_added"] = _record_results(store, calendar, keys, clock)
    return report


def _scan_managed_league(root, league):
    with closing(connect_managed(root)) as conn:
        return scan_league(StudyStore(conn), league)


def _command(action, repo_root):
    from .euro_soccer_report import summarize

    root = Path(repo_root)
    with closing(connect_managed(root)) as conn:
        store = StudyStore(conn)
        if action == "status":
            print(json.dumps(summarize(store.records(MODEL_VERSION + "/")), ensure_ascii=False, sort_keys=True))
            return 0
        with store.scan_lock() as acquired:
            if not acquired:
                print(json.dumps({"status": "scan_already_running"}))
                return 0
            # Each lane has its own verified PostgreSQL session and independent public inputs.
            with ThreadPoolExecutor(max_workers=len(sources.LEAGUES)) as pool:
                jobs = [pool.submit(_scan_managed_league, root, league) for league in sources.LEAGUES]
                reports = [job.result() for job in jobs]
    print(json.dumps({"model_version": MODEL_VERSION, "leagues": reports, "research_only": True,
                      "amount_cny": 0}, ensure_ascii=False, sort_keys=True))
    return int(any("current_calendar_unavailable" in r["gaps"] for r in reports))


def command(action, repo_root):
    # Native driver exception text can contain the private connection location.
    try:
        import psycopg
    except ModuleNotFoundError:
        print("euro-soccer requires the postgres package extra", file=sys.stderr)
        return 1
    try:
        return _command(action, repo_root)
    except (psycopg.Error, RuntimeError, OSError, ValueError) as exc:
        print("euro-soccer failed (" + type(exc).__name__ + ")", file=sys.stderr)
        return 1
