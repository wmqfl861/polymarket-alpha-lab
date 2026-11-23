"""Maintain prospective football evidence, studies and paper-only market admission."""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

import football_daily as daily
import football_store as store


BEIJING = ZoneInfo("Asia/Shanghai")
MODEL_LOCK = 12
SCHEMA = """
    CREATE TABLE IF NOT EXISTS football_notify.football_learning_study (
        study_day date PRIMARY KEY,
        started_at timestamptz NOT NULL,
        completed_at timestamptz NOT NULL,
        report jsonb NOT NULL
    );
    CREATE TABLE IF NOT EXISTS football_notify.football_domestic_study (
        study_day date PRIMARY KEY, completed_at timestamptz NOT NULL, report jsonb NOT NULL
    );
"""
FEEDBACK_VIEW = """
    CREATE VIEW football_notify.football_prediction_feedback AS
    WITH predictions AS (
        SELECT 'window'::text AS source, fixture_key, fixture_revision, market_id, outcome, model_version,
               kickoff, observed_at AS issued_at, model_probability, market_price AS midpoint, entry_price
          FROM football_forward_observation
        UNION ALL
        SELECT 'weixin_shortlist', fixture_key, fixture_revision, market_id, outcome, model_version,
               kickoff, issued_at, model_probability, NULL, entry_price
          FROM football_forward_recommendation
        UNION ALL
        SELECT 'chat_watch', fixture_key, fixture_revision, market_id, outcome, model_version,
               kickoff, published_at, model_probability, NULL, price
          FROM football_forward_chat_watch
    )
    SELECT p.*, c.league, c.market_kind, c.line, c.team_side, c.side, r.state AS result_state, r.won,
           r.home_goals, r.away_goals, r.observed_at AS result_at, q.price AS closing_midpoint,
           power(p.model_probability - r.won::int, 2) AS model_brier,
           power(p.midpoint - r.won::int, 2) AS market_brier
      FROM predictions p
      LEFT JOIN football_forward_contract c USING (fixture_key, fixture_revision, market_id, outcome, kickoff)
      LEFT JOIN football_forward_result r USING (fixture_key, fixture_revision, market_id, outcome, model_version, kickoff)
      LEFT JOIN football_forward_close q USING (fixture_key, fixture_revision, market_id, outcome, model_version, kickoff)
"""


def cached(connection, key):
    row = connection.execute("SELECT payload FROM source_cache WHERE cache_key = %s", (key,)).fetchone()
    return row["payload"] if row else {}


def save_cache(connection, key, value):
    connection.execute(
        """INSERT INTO source_cache (cache_key,payload) VALUES (%s,%s)
           ON CONFLICT (cache_key) DO UPDATE SET payload=excluded.payload
           WHERE source_cache.payload IS DISTINCT FROM excluded.payload""", (key, Jsonb(value)))


@contextmanager
def model_lock(connection):
    key = (store.LOCK_NAMESPACE, MODEL_LOCK)
    owned = connection.execute("SELECT pg_try_advisory_lock(%s,%s) AS ok", key).fetchone()["ok"]
    try:
        yield owned
    finally:
        if owned:
            connection.execute("SELECT pg_advisory_unlock(%s,%s)", key)


def publish_research(connection, rows, fixtures, now):
    by_id = {row["info"]["id"]: row for row in rows}
    matched = 0
    with connection.transaction():
        store.lock_queue(connection)
        for info in fixtures:
            research = info.get("research")
            if not research:
                continue
            row = by_id[info["id"]]
            patch = {name: info[name] for name in
                     ("research", "fixture_id", "fotmob_home_id", "fotmob_away_id", "fotmob_league_id")
                     if name in info}
            if len(patch) != 5:
                continue
            if any(row["info"].get(name) not in (None, patch[name]) for name in patch if name != "research"):
                continue
            fixture = connection.execute(
                """UPDATE fixture SET info = info || %s, watch = true
                   WHERE id=%s AND revision=%s AND fixture_state='scheduled'
                     AND kickoff=%s AND kickoff>clock_timestamp() AND missing_scans<2
                   RETURNING *""",
                (Jsonb(patch), row["id"], row["revision"], row["kickoff"])).fetchone()
            if fixture is None:
                continue
            matched += 1
            for stage, opening, cutoff in daily.stage_windows(fixture["kickoff"], True, False):
                if stage in {"t90", "t30"} and cutoff > now:
                    store.add_job(connection, stage, opening, cutoff, fixture=fixture)
    return matched


def domestic_study(connection, day):
    from football_domestic_study import run_daily

    last = connection.execute(
        "SELECT study_day, report FROM football_domestic_study WHERE study_day<=%s ORDER BY study_day DESC LIMIT 1",
        (day,)).fetchone()
    if last and last["study_day"] == day:
        return "already_recorded"
    report = run_daily(store.db_now(connection), store.source_cache(connection),
                       connection=connection, previous=last["report"] if last else None)
    connection.execute(
        """INSERT INTO football_domestic_study (study_day,completed_at,report)
           VALUES (%s,%s,%s) ON CONFLICT (study_day) DO NOTHING""",
        (day, store.db_now(connection), Jsonb(report)))
    return str(day)


def models(connection, *, study=False):
    import football_research as research

    with model_lock(connection) as owned:
        if not owned:
            return {"state": "another_model_update_running"}
        now = store.db_now(connection)
        local_day = (now.astimezone(BEIJING) - timedelta(hours=6)).date()
        cache = cached(connection, "research/1")
        rows = connection.execute(
            """SELECT id,revision,kickoff,info FROM fixture WHERE fixture_state='scheduled'
               AND missing_scans<2 AND kickoff>%s AND kickoff<=%s ORDER BY kickoff,id""",
            (now, now + timedelta(hours=daily.SCAN_HOURS))).fetchall()
        fixtures, cache = research.refresh([dict(row["info"]) for row in rows], now, cache)
        with connection.transaction():
            matched = publish_research(connection, rows, fixtures, store.db_now(connection))
            save_cache(connection, "research/1", cache)
        result = {"state": "complete", "scope_fixtures": matched, "fixtures": len(rows)}
        if study:
            from football_forward import ensure_schema
            ensure_schema(connection)
            connection.execute(SCHEMA)
            if not connection.execute("SELECT to_regclass('football_prediction_feedback') AS name").fetchone()["name"]:
                connection.execute(FEEDBACK_VIEW)
            existing = connection.execute(
                "SELECT 1 FROM football_learning_study WHERE study_day=%s", (local_day,)).fetchone()
            needs_registry = "model_registry" not in cache.get("research", {})
            if existing is None or needs_registry:
                report, cache = research.optimize(store.db_now(connection), cache, use_network=True,
                                                  force=True, connection=connection)
                if not isinstance(report, dict) or not report:
                    raise ValueError("football_study_report_missing")
                finished = store.db_now(connection)
                with connection.transaction():
                    save_cache(connection, "research/1", cache)
                    connection.execute(
                        """INSERT INTO football_learning_study VALUES (%s,%s,%s,%s)
                           ON CONFLICT (study_day) DO NOTHING""", (local_day, now, finished, Jsonb(report)))
                result["study"] = str(local_day)
                if needs_registry:
                    refreshed, cache = research.refresh(fixtures, store.db_now(connection), cache)
                    with connection.transaction():
                        result["scope_fixtures"] = publish_research(connection, rows, refreshed, store.db_now(connection))
                        save_cache(connection, "research/1", cache)
            else:
                result["study"] = "already_recorded"
            result["domestic_study"] = domestic_study(connection, local_day)
        save_cache(connection, "football_learning/models", {"observed_at": now.isoformat(), **result})
        return result


def evidence(connection):
    from football_forward import collect

    summary = collect(connection)
    save_cache(connection, "football_learning/evidence", {"observed_at": store.db_now(connection).isoformat(), **summary})
    return summary


def admission(connection):
    from football_admission import evaluate

    result = evaluate(connection)
    summary = {key: result[key] for key in ("evaluated_at", "counts", "admitted", "newly_admitted", "studies")}
    save_cache(connection, "football_learning/admission", summary)
    return summary


def status(connection):
    from football_admission import active_scopes

    totals = connection.execute("""
        SELECT (SELECT count(*) FROM football_forward_observation) AS predictions,
               (SELECT count(DISTINCT (fixture_key, fixture_revision)) FROM football_forward_observation)
                 AS predicted_fixtures,
               (SELECT count(*) FROM football_forward_chat_watch) AS chat_watches,
               (SELECT count(*) FROM football_forward_recommendation) AS selected_for_weixin,
               (SELECT count(*) FROM football_forward_close) AS closing_quotes,
               (SELECT count(*) FROM football_forward_result) AS results,
               (SELECT count(*) FROM football_learning_study) AS research_league_reports,
               (SELECT count(*) FROM football_domestic_study) AS domestic_league_reports
    """).fetchone()
    recent = connection.execute("""
        SELECT source, league, fixture_key, market_kind, line, side, issued_at, model_probability,
               entry_price, closing_midpoint, result_state, won, home_goals, away_goals, model_brier
        FROM football_prediction_feedback WHERE source IN ('chat_watch','weixin_shortlist')
        ORDER BY issued_at DESC, fixture_key, market_id, outcome LIMIT 10
    """).fetchall()
    return {"as_of": store.db_now(connection).isoformat(), "totals": totals, "recent_picks": recent,
            "evidence": cached(connection, "football_learning/evidence"),
            "models": cached(connection, "football_learning/models"),
            "admission": cached(connection, "football_learning/admission"),
            "active_scopes": sorted(active_scopes(connection), key=str)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=("models", "study", "evidence", "admission", "status"))
    mode = parser.parse_args().task
    with store.connect(read_only=mode == "status") as connection:
        if mode == "status":
            result = status(connection)
        elif mode == "evidence":
            result = evidence(connection)
        elif mode == "admission":
            result = admission(connection)
        else:
            result = models(connection, study=mode == "study")
    print(json.dumps(result, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
