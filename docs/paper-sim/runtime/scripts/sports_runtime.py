"""Refresh public basketball research and fixtures and settle the shared CNY paper account."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from psycopg.types.json import Jsonb

import football_daily as daily
import football_store as store
from football_learning import BEIJING, cached, save_cache


CACHE = "basketball/1"
LATEST_SCAN = "basketball/scan"
LATEST_STUDY = "basketball/study"
LATEST_COLLEGE = "basketball/college-scan"
LATEST_COLLEGE_WINDOW = "basketball/college-price-window"
LATEST_SETTLE = "sports/settlement"
PUBLIC_LOCK = 20
SETTLE_LOCK = 21
COLLEGE_LOCK = 26
HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS football_notify.basketball_daily_study (
    study_day date PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    report jsonb NOT NULL
)
"""


@contextmanager
def task_lock(connection, number):
    keys = (store.LOCK_NAMESPACE, number)
    held = connection.execute("SELECT pg_try_advisory_lock(%s,%s) AS held", keys).fetchone()["held"]
    try:
        yield held
    finally:
        if held:
            connection.execute("SELECT pg_advisory_unlock(%s,%s)", keys)


def _scope(league, kind, line, side, version):
    if not all(isinstance(item, str) and item for item in (league, kind, version)):
        return None
    try:
        line = None if line is None else Decimal(str(line))
    except (ValueError, InvalidOperation):
        return None
    if line is not None and not line.is_finite():
        return None
    if side is not None and not isinstance(side, str):
        return None
    return (league, kind, line, side, version)


def basketball_scope_permitted(info, signal):
    candidate = _scope(info.get("league"), signal.get("market_kind"), signal.get("line"),
                       signal.get("team_side"), signal.get("model_version"))
    return (info.get("sport") == "basketball" and candidate is not None
            and candidate in info.get("_paper_scopes", ()))


def basketball_scopes(connection):
    import basketball_feed as basketball
    import basketball_forward

    saved = connection.execute("""
        SELECT payload->'research_report' AS report FROM source_cache WHERE cache_key=%s
        """, (CACHE,)).fetchone()
    report = (saved["report"] if saved else None) or {}
    now = store.db_now(connection)
    managed = basketball_forward.active_scopes(connection, now=now)
    if not managed and (not report or not report.get("approved_families", True)):
        return frozenset()
    fixtures = connection.execute("""
        SELECT info FROM fixture WHERE info->>'sport'='basketball' AND fixture_state='scheduled'
        AND missing_scans<2 AND kickoff>%s AND last_seen >= %s ORDER BY kickoff,id
        """, (now, now - store.FIXTURE_FRESHNESS)).fetchall()
    valid = set()
    for row in fixtures:
        info = row["info"]
        for entry in basketball.approved_scopes(report, now=now, fixture=info, managed_scopes=managed):
            scope = _scope(*entry)
            if scope and scope[0] == info.get("league"):
                valid.add(scope)
    return frozenset(valid)


def review_basketball(info, now, stage):
    import basketball_feed as basketball

    return basketball.review(info, now, stage)


def basketball_scan_state(connection):
    snapshot = cached(connection, LATEST_SCAN)
    if not snapshot:
        return None
    college = cached(connection, LATEST_COLLEGE)
    summary = dict(snapshot, partial=snapshot["state"] != "complete")
    if college:
        summary["college_research"] = college
    return {"result": snapshot["state"], "finished_at": store.timestamp(snapshot["observed_at"]),
            "last_complete_at": store.timestamp(snapshot["last_complete_at"]) if snapshot.get("last_complete_at") else None,
            "summary": summary}


def scan(connection):
    import basketball_feed as basketball
    import basketball_forward
    with task_lock(connection, PUBLIC_LOCK) as held:
        if not held:
            return {"state": "public_update_running"}
        start = store.db_now(connection)
        end = start + timedelta(hours=daily.SCAN_HOURS)
        previous = cached(connection, CACHE)
        try:
            managed = basketball_forward.active_scopes(connection, now=start)
            fixtures, stats = basketball.discover(start, hours=daily.SCAN_HOURS, source_cache=previous,
                                                 managed_scopes=managed)
        except Exception as exc:
            save_cache(connection, LATEST_SCAN, {
                "state": "failed", "observed_at": store.db_now(connection).isoformat(),
                "last_complete_at": cached(connection, LATEST_SCAN).get("last_complete_at"),
                "window_start": start.isoformat(), "window_end": end.isoformat(), "error": type(exc).__name__,
            })
            raise
        observations, rejected = daily.validated_observations(fixtures, start, end)
        if any(info.get("sport") != "basketball" for info, _ in observations):
            raise ValueError("basketball_fixture_namespace_mismatch")
        summary = daily.scan_summary(stats, observations, rejected)
        partial = summary["partial"] or bool(rejected)
        now = store.db_now(connection)
        report = {"state": "partial" if partial else "complete", "observed_at": now.isoformat(),
                  "last_complete_at": cached(connection, LATEST_SCAN).get("last_complete_at") if partial else now.isoformat(),
                  "window_start": start.isoformat(), "window_end": end.isoformat(),
                  "fixtures": len(observations), "leagues": dict(Counter(info["league"] for info, _ in observations)),
                  "modeled": sum(bool(info.get("model") or info.get("nba_ratings_model")) for info, _ in observations),
                  "nba_ratings_modeled": stats.get("nba_ratings_modeled", 0),
                  "matched": sum(bool(info.get("basketball_official")) for info, _ in observations),
                  "model_status": dict(Counter(info.get("model_status", "unknown") for info, _ in observations)),
                  "model_partial": bool(stats.get("model_partial")),
                  "rejected": summary["rejected"], "sources": summary["sources"]}
        with connection.transaction():
            store.lock_queue(connection)
            seen = [daily.replace_fixture(connection, info, kickoff, now)["id"]
                    for info, kickoff in observations if kickoff > now]
            if not partial:
                connection.execute("""
                    UPDATE fixture SET missing_scans=least(2,missing_scans+1)
                    WHERE info->>'sport'='basketball' AND fixture_state='scheduled' AND last_seen<%s
                      AND kickoff>%s AND kickoff<%s AND NOT(id=ANY(%s))
                    """, (start, now, end, seen))
            if isinstance(stats.get("source_cache"), dict):
                save_cache(connection, CACHE, previous | stats["source_cache"])
            save_cache(connection, LATEST_SCAN, report)
    report["college_research"] = college_scan(connection)
    return report


def college_scan(connection, *, window_only=False):
    import basketball_college_forward as college

    with task_lock(connection, COLLEGE_LOCK) as held:
        if not held:
            return {"state": "college_scan_running"}
        try:
            report = college.collect(connection, window_only=window_only)
            if not window_only and report["state"] in {"complete", "partial"}:
                report["forward_results"] = college.performance(connection)
        except Exception as exc:
            # Other sports have already committed their scan. Preserve the
            # failed study's status so the next scan can retry independently.
            report = {"state": "failed", "error": type(exc).__name__,
                      "observed_at": store.db_now(connection).isoformat()}
        if not window_only or report["state"] not in {"idle", "awaiting_registration"}:
            save_cache(connection, LATEST_COLLEGE_WINDOW if window_only else LATEST_COLLEGE, report)
        return report


def study(connection):
    import basketball_feed as basketball

    with task_lock(connection, PUBLIC_LOCK) as held:
        if not held:
            return {"state": "public_update_running"}
        start = store.db_now(connection)
        previous = cached(connection, CACHE)
        try:
            report, cache = basketball.refresh_research(start, source_cache=previous)
        except Exception as exc:
            save_cache(connection, LATEST_STUDY, {"state": "failed", "observed_at": store.db_now(connection).isoformat(),
                                                  "error": type(exc).__name__})
            raise
        if not isinstance(report, dict) or not report or not isinstance(cache, dict):
            raise ValueError("basketball_study_missing")
        now = store.db_now(connection)
        day = (start.astimezone(BEIJING) - timedelta(hours=6)).date()
        result = {"state": "complete", "observed_at": now.isoformat(), "study_day": str(day)}
        result["research_status"] = report.get("status")
        nba = report.get("nba") or {}
        result["nba"] = {key: nba[key] for key in ("eligible_games", "required_games", "completed_games",
                                                    "retest_ready", "last_completed_date_et") if key in nba}
        result["approved_families"] = report.get("approved_families", [])
        if ratings := report.get("nba_ratings"):
            result["nba_ratings"] = {key: ratings[key] for key in ("model_version", "model_status", "eligible_games")
                                     if key in ratings}
        with connection.transaction():
            store.lock_queue(connection)
            connection.execute(HISTORY_SQL)
            connection.execute("""
                INSERT INTO basketball_daily_study(study_day,observed_at,report) VALUES (%s,%s,%s)
                ON CONFLICT (study_day) DO UPDATE SET observed_at=excluded.observed_at,report=excluded.report
                WHERE basketball_daily_study.report IS DISTINCT FROM excluded.report
                """, (day, now, Jsonb(report)))
            save_cache(connection, CACHE, previous | cache | {"research_report": report})
            save_cache(connection, LATEST_STUDY, result)
        return result


def football_result(connection, bet):
    """Match the exact frozen recommendation, contract, source fixture revision and outcome."""
    result = connection.execute("""
        SELECT r.state,r.won,r.home_goals,r.away_goals,r.observed_at,r.source_url,r.evidence
        FROM football_forward_result r
        JOIN football_forward_contract c USING (fixture_key,fixture_revision,market_id,outcome,kickoff)
        WHERE r.fixture_key=%s AND r.fixture_revision=%s AND r.market_id=%s AND r.outcome=%s
        AND r.model_version=%s AND r.kickoff=%s AND c.token_id=%s AND c.condition_id=%s
        AND r.evidence->>'token_id'=c.token_id AND r.evidence->>'condition_id'=c.condition_id
        AND r.evidence->>'rule'='regulation_90_plus_stoppage'
        """, (bet["fixture_key"], bet["fixture_revision"], bet["market_id"], bet["outcome"],
              bet["model_version"], bet["kickoff"], bet["token_id"], bet["condition_id"])).fetchone()
    if not result:
        return None
    if result["state"] == "resolved" and type(result["won"]) is bool:
        outcome = "win" if result["won"] else "loss"
    elif result["state"] == "void" and result["evidence"].get("source_status") == "resolved_50_50":
        outcome = "split"
    else:
        return None
    proof = result["evidence"] | {"source_url": result["source_url"], "home_goals": result["home_goals"],
                                 "away_goals": result["away_goals"], "model_version": bet["model_version"]}
    return {"result": outcome, "evidence": proof, "resolved_at": result["observed_at"]}


def basketball_result(bet, now):
    import basketball_feed as basketball

    saved = bet["settlement_evidence"]
    info = saved["fixture"]
    signal = saved["signal"]
    if any(str(signal.get(field)) != str(bet[field]) for field in ("market_id","outcome","token_id","condition_id","model_version")):
        raise ValueError("basketball_settlement_identity_mismatch")
    if store.timestamp(info["kickoff"]) != bet["kickoff"]:
        raise ValueError("basketball_settlement_kickoff_mismatch")
    return basketball.result(info, signal, now)


def settle(connection):
    import sports_portfolio as portfolio

    with task_lock(connection, SETTLE_LOCK) as held:
        if not held:
            return {"state": "another_settlement_running"}
        now = store.db_now(connection)
        pending = portfolio.open_positions(connection, due_before=now)
        outcomes = Counter()
        errors = []
        for bet in pending:
            try:
                if bet["sport"] == "football":
                    resolved = football_result(connection, bet)
                else:
                    resolved = basketball_result(bet, now)
                if resolved is None:
                    outcomes["awaiting_source"] += 1
                    continue
                identity = {key: bet[key] for key in ("sport", "fixture_key", "fixture_revision", "market_id",
                                                       "outcome", "token_id", "condition_id", "model_version")}
                portfolio.settle(connection, **identity, result=resolved["result"], evidence=resolved["evidence"],
                                 resolved_at=resolved["resolved_at"])
                outcomes[resolved["result"]] += 1
            except (KeyError, ValueError, OSError, portfolio.PortfolioError) as exc:
                errors.append({"market": bet["market_id"], "error": type(exc).__name__})
        report = {"state": "needs_retry" if errors else "complete", "observed_at": now.isoformat(),
                  "due": len(pending), "outcomes": dict(outcomes), "errors": errors}
        save_cache(connection, LATEST_SETTLE, report)
        return report


def settle_and_collect(connection):
    import basketball_forward

    wallet = settle(connection)
    forward = basketball_forward.tick(connection)
    return {"settlement": wallet, "basketball_observations": forward,
            "college_price_window": college_scan(connection, window_only=True)}


def scan_and_notify(connection):
    report = scan(connection)
    if report["state"] in {"complete", "partial"}:
        import sports_focus

        try:
            notice = sports_focus.publish(connection)
            report["owner_focus"] = {key: notice[key] for key in ("state", "existing", "source_errors") if key in notice}
            if "fixture_keys" in notice:
                report["owner_focus"]["fixtures"] = len(notice["fixture_keys"])
        except Exception as exc:
            report["owner_focus"] = {"state": "failed", "error": type(exc).__name__}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=("basketball-scan", "basketball-study", "settle", "status"))
    mode = parser.parse_args().task
    with store.connect(read_only=mode == "status") as connection:
        if mode == "status":
            import basketball_forward
            import sports_portfolio as portfolio
            answer = {"as_of": store.db_now(connection).isoformat(), "portfolio": portfolio.status(connection),
                      "basketball_scan": cached(connection, LATEST_SCAN), "basketball_study": cached(connection, LATEST_STUDY),
                      "college_research": cached(connection, LATEST_COLLEGE),
                      "college_price_window": cached(connection, LATEST_COLLEGE_WINDOW),
                      "settlement": cached(connection, LATEST_SETTLE),
                      "basketball_observations": basketball_forward.status(connection)}
        else:
            answer = {"basketball-scan": scan_and_notify, "basketball-study": study, "settle": settle_and_collect}[mode](connection)
    print(json.dumps(answer, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
