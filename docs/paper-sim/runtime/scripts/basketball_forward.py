"""Prospective basketball market observations kept apart from the paper cash account."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path

from psycopg import Error as PostgresError
from psycopg.types.json import Jsonb

import basketball_feed as basketball
import basketball_models as models
import football_feed as feed
import football_store as store


STUDY = "basketball-forward-20261123-v1"
LOCK = 24
WINDOWS = {"forward": (30, 20), "closing": (3, 0)}
LEAGUE_SCOPES = {
    "bkbbl": (models.EURO_VERSION, frozenset({"total"})),
    "bkeurocup": (models.EURO_VERSION, frozenset({"total"})),
    "nba": (models.NBA_VERSION, frozenset({"match_winner", "total"})),
}
EURO_TOTALS = ("bkbbl", "bkeurocup")
CAPTURE_LIMIT = 18
RESULT_LIMIT = 24
RESULT_DELAY = timedelta(hours=2)
RESULT_RETRY = timedelta(minutes=30)
SCHEMA = """
CREATE SCHEMA IF NOT EXISTS football_notify;

CREATE TABLE IF NOT EXISTS football_notify.basketball_forward_study (
    study_key text PRIMARY KEY,
    registered_at timestamptz NOT NULL,
    protocol jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS football_notify.basketball_forward_snapshot (
    sample_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    study_key text NOT NULL REFERENCES football_notify.basketball_forward_study,
    fixture_key text NOT NULL,
    fixture_revision integer NOT NULL CHECK (fixture_revision > 0),
    league text NOT NULL,
    kickoff timestamptz NOT NULL,
    phase text NOT NULL CHECK (phase IN ('forward', 'closing')),
    started_at timestamptz NOT NULL,
    captured_at timestamptz NOT NULL,
    complete boolean NOT NULL,
    reason_code text NOT NULL,
    fixture jsonb NOT NULL CHECK (jsonb_typeof(fixture) = 'object'),
    observations jsonb NOT NULL CHECK (jsonb_typeof(observations) = 'array'),
    UNIQUE (study_key, fixture_key, fixture_revision, phase, started_at),
    CHECK (started_at <= captured_at),
    CHECK ((phase = 'forward' AND started_at >= kickoff - interval '30 minutes'
             AND captured_at < kickoff - interval '20 minutes')
        OR (phase = 'closing' AND started_at >= kickoff - interval '3 minutes' AND captured_at < kickoff))
);

CREATE TABLE IF NOT EXISTS football_notify.basketball_forward_result (
    study_key text NOT NULL REFERENCES football_notify.basketball_forward_study,
    fixture_key text NOT NULL,
    fixture_revision integer NOT NULL,
    market_id text NOT NULL,
    token_id text NOT NULL,
    contract_digest text NOT NULL,
    sample_id bigint NOT NULL REFERENCES football_notify.basketball_forward_snapshot,
    league text NOT NULL,
    observed_at timestamptz NOT NULL,
    result text NOT NULL CHECK (result IN ('win', 'loss', 'split')),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    PRIMARY KEY (study_key, fixture_key, fixture_revision, market_id, token_id, contract_digest)
);

CREATE TABLE IF NOT EXISTS football_notify.basketball_forward_result_retry (
    study_key text NOT NULL REFERENCES football_notify.basketball_forward_study,
    sample_id bigint NOT NULL REFERENCES football_notify.basketball_forward_snapshot,
    token_id text NOT NULL,
    next_at timestamptz NOT NULL,
    reason_code text NOT NULL,
    PRIMARY KEY (study_key, sample_id, token_id)
);

CREATE INDEX IF NOT EXISTS basketball_forward_fixture_window ON football_notify.basketball_forward_snapshot
    (study_key, fixture_key, fixture_revision, phase, captured_at);
"""


def _exists(connection):
    return connection.execute("""
        SELECT to_regclass('football_notify.basketball_forward_study') IS NOT NULL AS ready
    """).fetchone()["ready"]


def _register(connection):
    if _exists(connection):
        row = connection.execute("""
            SELECT registered_at FROM football_notify.basketball_forward_study WHERE study_key = %s
        """, (STUDY,)).fetchone()
        if row:
            return row["registered_at"]
    protocol = {
        "markets": {league: {"model_version": version, "families": sorted(families)}
                    for league, (version, families) in LEAGUE_SCOPES.items()},
        "lead_minutes": WINDOWS, "settlement_scope": basketball.EXPLICIT_OT,
    }
    with connection.transaction():
        connection.execute(SCHEMA)
        return connection.execute("""
            INSERT INTO football_notify.basketball_forward_study (study_key, registered_at, protocol)
            VALUES (%s, clock_timestamp(), %s) RETURNING registered_at
        """, (STUDY, Jsonb(protocol))).fetchone()["registered_at"]


def _in_window(kickoff, observed_at, phase):
    start, stop = WINDOWS[phase]
    return kickoff - timedelta(minutes=start) <= observed_at < kickoff - timedelta(minutes=stop)


def _due_fixtures(connection, now, registered_at):
    return connection.execute("""
        WITH windows(phase, opens, closes) AS (VALUES ('forward', 30, 20), ('closing', 3, 0))
        SELECT f.id, f.revision, f.kickoff, f.info, w.phase,
               ARRAY(SELECT DISTINCT item->>'market_id'
                     FROM football_notify.basketball_forward_snapshot shot
                     CROSS JOIN LATERAL jsonb_array_elements(shot.observations) AS item
                     WHERE (shot.study_key, shot.fixture_key, shot.fixture_revision, shot.phase)
                            = (%(study)s, f.id, f.revision, 'forward')) AS forward_market_ids
        FROM football_notify.fixture f CROSS JOIN windows w
        WHERE f.info->>'sport' = 'basketball' AND f.info->>'league' = ANY(%(leagues)s)
          AND f.fixture_state = 'scheduled' AND f.missing_scans < 2
          AND f.last_seen BETWEEN %(fresh)s AND %(now)s
          AND f.kickoff - w.opens * interval '1 minute' >= %(registered)s
          AND f.kickoff - w.opens * interval '1 minute' <= %(now)s
          AND f.kickoff - w.closes * interval '1 minute' > %(now)s
          AND NOT EXISTS (SELECT 1 FROM football_notify.basketball_forward_snapshot shot
              WHERE (shot.study_key, shot.fixture_key, shot.fixture_revision, shot.phase)
                    = (%(study)s, f.id, f.revision, w.phase)
                AND (shot.captured_at >= %(previous_minute)s OR (w.phase = 'forward' AND shot.complete)))
        ORDER BY f.kickoff, f.id LIMIT %(limit)s
    """, {"study": STUDY, "leagues": list(LEAGUE_SCOPES), "fresh": now - store.FIXTURE_FRESHNESS,
          "now": now, "registered": registered_at, "previous_minute": now.replace(second=0, microsecond=0),
          "limit": CAPTURE_LIMIT}).fetchall()


def _observations(row, report, started, captured):
    info, kickoff, phase = row["info"], row["kickoff"], row["phase"]
    version, families = LEAGUE_SCOPES[info["league"]]
    observations = []
    for item in feed.rows(report.get("observations")):
        if (item.get("league") != info["league"] or item.get("fixture_key") != info["id"]
                or feed.stamp(item.get("kickoff")) != kickoff or item.get("market_kind") not in families
                or not feed.numeric_id(item.get("market_id")) or not feed.numeric_id(item.get("token_id"))):
            continue
        observed = feed.stamp(item.get("book_observed_at"))
        received = feed.stamp(item.get("book_received_at"))
        quote_in_window = bool(received and observed and started <= received <= captured
                               and timedelta(0) <= received - observed <= feed.BOOK_AGE)
        reasons = item.get("reasons")
        reasons = [reason for reason in reasons if isinstance(reason, str)] if isinstance(reasons, list) else []
        if not quote_in_window:
            reasons.append("book_clock_unverified")
        if item.get("model_version") not in (None, version):
            reasons.append("study_model_version_mismatch")
        if item.get("direct_contract_verified") is not True:
            reasons.append("direct_contract_unverified")
        observations.append(item | {"reasons": sorted(set(reasons)), "book_in_window": quote_in_window})
    return observations


def _record(connection, row, report, started, captured):
    phase, kickoff = row["phase"], row["kickoff"]
    if not _in_window(kickoff, started, phase) or not _in_window(kickoff, captured, phase):
        return None
    info = row["info"]
    fixture = feed.record(report.get("updated_fixture")) or info
    if fixture.get("id") != info["id"] or feed.stamp(fixture.get("kickoff")) != kickoff:
        return None
    # Scope, news and gate refusals retain their observations. Missing source evidence can retry.
    observations = _observations(row, report, started, captured)
    complete = (not report.get("coverage_gap") and not report.get("partial_books") and bool(observations)
                and all(item["book_in_window"] and item.get("market_probability") is not None
                        and item.get("direct_contract_verified") is True and item.get("model_probability") is not None
                        and item.get("model_version") == LEAGUE_SCOPES[info["league"]][0] for item in observations))
    reason = store.safe_reason(report.get("reason_code"), "observation_unavailable")
    fixture = {key: value for key, value in fixture.items() if not key.startswith("_")}
    return connection.execute("""
        INSERT INTO football_notify.basketball_forward_snapshot
            (study_key, fixture_key, fixture_revision, league, kickoff, phase, started_at, captured_at,
             complete, reason_code, fixture, observations)
        SELECT %s, f.id, f.revision, %s, f.kickoff, %s, %s, %s, %s, %s, %s, %s
        FROM football_notify.fixture f WHERE (f.id, f.revision, f.kickoff) = (%s, %s, %s)
            AND f.fixture_state = 'scheduled'
        ON CONFLICT DO NOTHING RETURNING sample_id
    """, (STUDY, info["league"], phase, started, captured, complete, reason, Jsonb(fixture), Jsonb(observations),
          row["id"], row["revision"], kickoff)).fetchone()


def _review(row, clock):
    started = clock()
    if not _in_window(row["kickoff"], started, row["phase"]):
        return row, None, started, started
    info = row["info"] | {"_forward_market_ids": row["forward_market_ids"]}
    try:
        report = basketball.review(info, started, row["phase"])
    except (OSError, ValueError, KeyError):
        report = {"reason_code": "review_unavailable", "coverage_gap": True}
    return row, report, started, clock()


def _due_results(connection, now):
    return connection.execute("""
        WITH first_marks AS (
            SELECT DISTINCT ON (shot.fixture_key, shot.fixture_revision, item->>'market_id',
                                item->>'token_id', item->>'contract_digest')
                   shot.sample_id, shot.fixture_key, shot.fixture_revision, shot.league, shot.kickoff,
                   shot.fixture, shot.captured_at,
                   item->>'market_id' AS market_id, item->>'token_id' AS token_id,
                   item->>'contract_digest' AS contract_digest, item AS signal
            FROM football_notify.basketball_forward_snapshot shot
            CROSS JOIN LATERAL jsonb_array_elements(shot.observations) AS item
            WHERE shot.study_key = %(study)s AND shot.kickoff <= %(latest)s
              AND item->>'settlement_scope' = %(scope)s AND item->>'direct_contract_verified' = 'true'
              AND item->>'contract_digest' IS NOT NULL AND jsonb_typeof(item->'independent_fixture') = 'object'
            ORDER BY shot.fixture_key, shot.fixture_revision, item->>'market_id', item->>'token_id',
                     item->>'contract_digest', shot.captured_at
        )
        SELECT mark.* FROM first_marks mark
        LEFT JOIN football_notify.basketball_forward_result result ON result.study_key = %(study)s AND
           (result.fixture_key, result.fixture_revision, result.market_id, result.token_id, result.contract_digest) =
           (mark.fixture_key, mark.fixture_revision, mark.market_id, mark.token_id, mark.contract_digest)
        LEFT JOIN football_notify.basketball_forward_result_retry retry ON retry.study_key = %(study)s AND
           (retry.sample_id, retry.token_id) = (mark.sample_id, mark.token_id)
        WHERE result.sample_id IS NULL AND (retry.next_at IS NULL OR retry.next_at <= %(now)s)
        ORDER BY mark.kickoff, mark.market_id, mark.token_id LIMIT %(limit)s
    """, {"study": STUDY, "scope": basketball.EXPLICIT_OT, "latest": now - RESULT_DELAY,
          "now": now, "limit": RESULT_LIMIT}).fetchall()


def _result(rows, clock):
    first = rows[0]
    try:
        settled = basketball.result(first["fixture"], first["signal"], clock())
    except (OSError, ValueError, KeyError):
        settled = None
    yield first, settled
    proof = feed.record(feed.record(settled).get("evidence"))
    tokens, outcomes = proof.get("token_order"), proof.get("outcome_order")
    prices = proof.get("gamma_terminal_prices")
    full_payout = (isinstance(tokens, list) and len(tokens) == 2 and all(isinstance(token, str) for token in tokens)
                   and tokens[0] != tokens[1]
                   and isinstance(outcomes, list) and len(outcomes) == 2 and prices in ([1, 0], [0, 1], [0.5, 0.5]))
    fields = ("provider", "id", "home_id", "away_id", "kickoff")
    fixture = first["signal"]["independent_fixture"]
    for row in rows[1:]:
        token, signal = row["token_id"], row["signal"]
        if (not full_payout or token not in tokens or outcomes[tokens.index(token)] != signal.get("outcome")
                or any(signal["independent_fixture"].get(name) != fixture.get(name) for name in fields)):
            yield row, None
            continue
        payout = prices[tokens.index(token)]
        yield row, {"result": {1: "win", 0: "loss", 0.5: "split"}[payout], "resolved_at": settled["resolved_at"],
                    "evidence": proof | {"selected_token": token, "selected_payout": payout}}


def _write_result(connection, row, settled, now):
    proof = feed.record(feed.record(settled).get("evidence"))
    at = feed.record(settled).get("resolved_at")
    at = feed.stamp(at) if isinstance(at, str) else at
    result = feed.record(settled).get("result")
    token = row["token_id"]
    if (result in ("win", "loss", "split") and at is not None and row["captured_at"] <= at <= now
            and (result == "split" or row["kickoff"] < at)
            and proof.get("contract_digest") == row["contract_digest"]
            and str(proof.get("market_id")) == row["market_id"] and str(proof.get("selected_token")) == token
            and proof.get("condition_id") == row["signal"].get("condition_id")
            and proof.get("selected_payout") == {"win": 1, "loss": 0, "split": 0.5}[result]):
        connection.execute("""
            INSERT INTO football_notify.basketball_forward_result
                (study_key, fixture_key, fixture_revision, market_id, token_id, contract_digest,
                 sample_id, league, observed_at, result, evidence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
        """, (STUDY, row["fixture_key"], row["fixture_revision"], row["market_id"], token, row["contract_digest"],
              row["sample_id"], row["league"], at, result, Jsonb(proof)))
        return result
    reason = "result_evidence_mismatch" if settled else "result_source_pending"
    connection.execute("""
        INSERT INTO football_notify.basketball_forward_result_retry (study_key, sample_id, token_id, next_at, reason_code)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT (study_key, sample_id, token_id)
        DO UPDATE SET next_at = excluded.next_at, reason_code = excluded.reason_code
    """, (STUDY, row["sample_id"], token, now + RESULT_RETRY, reason))
    return reason


def _managed_contracts(connection, now):
    """Bind only registered, directly verified forward contract terms to native identities."""
    rows = connection.execute("""
        SELECT study.registered_at, shot.league, shot.kickoff, item AS observation
        FROM football_notify.basketball_forward_snapshot shot
        JOIN football_notify.basketball_forward_study study USING (study_key)
        CROSS JOIN LATERAL jsonb_array_elements(shot.observations) AS item
        WHERE shot.study_key = %(study)s AND shot.phase = 'forward' AND shot.league = ANY(%(leagues)s)
          AND shot.kickoff - interval '30 minutes' >= study.registered_at AND shot.captured_at <= %(now)s
          AND study.protocol #> ARRAY['markets', shot.league, 'families'] @> '["total"]'::jsonb
          AND item->>'league' = shot.league AND item->>'fixture_key' = shot.fixture->>'id'
          AND item->>'market_kind' = 'total' AND item->>'team_side' IS NULL
          AND item->>'settlement_scope' = %(settlement_scope)s
          AND item->'direct_contract_verified' = 'true'::jsonb
          AND item->>'model_version' = %(version)s
          AND item->>'model_version' = study.protocol #>> ARRAY['markets', shot.league, 'model_version']
          AND item->>'model_version' = shot.fixture->'model'->>'model_version'
          AND item->>'model_digest' = shot.fixture->'model'->>'sha256'
    """, {"study": STUDY, "leagues": list(EURO_TOTALS), "now": now,
          "settlement_scope": basketball.EXPLICIT_OT, "version": models.EURO_VERSION}).fetchall()
    by_contract = defaultdict(set)
    for row in rows:
        item = row["observation"]
        market = feed.numeric_id(item.get("market_id"))
        condition = item.get("condition_id")
        if (not market or not isinstance(condition, str) or not basketball.CONDITION_ID.fullmatch(condition)
                or not store.is_hex(item.get("contract_digest")) or not store.is_hex(item.get("model_digest"))
                or feed.stamp(item.get("kickoff")) != row["kickoff"]):
            continue
        try:
            line = Decimal(str(item.get("line")))
            is_half_point = line.is_finite() and line > 0 and line % 1 == Decimal("0.5")
        except InvalidOperation:
            continue
        if not is_half_point:
            continue
        scope = (row["league"], "total", line, None, item["model_version"])
        by_contract[(condition, market, row["kickoff"])].add((scope, item["contract_digest"], row["registered_at"]))

    bindings, registered = {}, {}
    for identity, matches in by_contract.items():
        if len(matches) == 1:
            scope, _, started = next(iter(matches))
            bindings[identity] = scope[:4]
            registered[scope] = {"registered": started}
    return bindings, registered


def _managed_promotion(connection, now):
    from football_admission import PROJECT_ROOT, _read_managed_promotion
    from polymarket_alpha_lab.papersim import evaluation, queue_policy, store_psycopg

    configured = os.environ.get("PAPERSIM_MANAGED_REPO_ROOT")
    root = Path(configured).expanduser() if configured else PROJECT_ROOT
    if not root.is_absolute():
        return {}, "managed_papersim_project_root_invalid"
    if not (root / ".local" / "postgres" / "instance.json").is_file():
        return {}, "managed_papersim_database_unavailable"
    if not _exists(connection):
        return {}, "managed_scope_contract_bindings_unavailable"
    bindings, registered = _managed_contracts(connection, now)
    if not bindings:
        return {}, "managed_scope_contract_bindings_unavailable"
    try:
        with store_psycopg.connect_managed(root) as native:
            with native.transaction():
                native.execute("SET default_transaction_read_only = on")
                native.execute("SET statement_timeout = '5s'")
            decisions = _read_managed_promotion(native, bindings, registered, now)
            if not decisions:
                return {}, "exact_managed_paper_queue_unverified"
            # The shared evaluator handles receipts and live metrics. Include both native
            # queue scopes when checking any subsequently latched pauses.
            latches = store_psycopg.latched_pauses(native)
    except (OSError, ValueError, TypeError, KeyError, PostgresError, store_psycopg.PapersimStoreError):
        return {}, "managed_papersim_evidence_unavailable"

    assessed = {}
    queue_scopes = (queue_policy.SCOPE_DOMESTIC, queue_policy.SCOPE_CROSS_LEAGUE)
    for scope, decision in decisions.items():
        queue = decision["queue"]
        identity = (queue["model_key"], queue["market_type"], queue["gate_version"])
        prefixes = tuple("|".join((queue_scope, *identity, "")) for queue_scope in queue_scopes)
        if any(any(flags) and queue_id.startswith(prefixes) for queue_id, flags in latches.items()):
            decision = decision | {"status": evaluation.VERDICT_NOT_ASSESSABLE, "paused": True}
        assessed[scope] = decision
    return assessed, "managed_od_g_evaluated"


def _passed_scopes(decisions):
    from polymarket_alpha_lab.papersim import evaluation

    return frozenset(scope for scope, decision in decisions.items()
                     if decision["status"] == evaluation.VERDICT_PASS and not decision["paused"]
                     and decision["managed_report_covers_scope"] and store.is_hex(decision["managed_report_sha256"]))


def active_scopes(connection, *, now=None):
    """Return the exact BBL and EuroCup CNY paper scopes earned in the native managed ledger."""
    current = store.timestamp(now) if now is not None else store.db_now(connection)
    decisions, _ = _managed_promotion(connection, current)
    return _passed_scopes(decisions)


def status(connection):
    """Report research coverage and verified native OD-G scope decisions independently."""
    from football_admission import _scope_document

    counts = {league: {"forward_windows": 0, "closing_windows": 0, "paired_quotes": 0, "terminal_results": 0}
              for league in LEAGUE_SCOPES}
    decisions, reason = _managed_promotion(connection, store.db_now(connection))
    passed = _passed_scopes(decisions)
    report = {"study": STUDY, "leagues": counts, "entry_admission": "specific_scopes" if passed else "research_only",
              "managed_promotion": {"reason_code": reason, "active_scopes": [_scope_document(scope) for scope in sorted(passed)],
                                    "assessments": [_scope_document(scope) | decision for scope, decision in sorted(decisions.items())]}}
    if not _exists(connection):
        return report | {"state": "unregistered"}
    rows = connection.execute("""
        WITH attempts AS (
            SELECT league, count(DISTINCT (fixture_key, fixture_revision)) FILTER (WHERE phase = 'forward') AS forward_windows,
                count(DISTINCT (fixture_key, fixture_revision)) FILTER (WHERE phase = 'closing') AS closing_windows
            FROM football_notify.basketball_forward_snapshot WHERE study_key = %(study)s GROUP BY league
        ), paired AS (
            SELECT shot.league, count(DISTINCT (fixture_key, fixture_revision, item->>'market_id', item->>'token_id')) AS paired_quotes
            FROM football_notify.basketball_forward_snapshot shot CROSS JOIN LATERAL jsonb_array_elements(shot.observations) item
            WHERE study_key = %(study)s AND phase = 'forward' AND item->>'book_in_window' = 'true'
              AND item->>'direct_contract_verified' = 'true'
              AND item->>'settlement_scope' = %(scope)s AND item->>'model_probability' IS NOT NULL
              AND NOT (item->'reasons' ? 'study_model_version_mismatch')
              AND item->>'market_probability' IS NOT NULL GROUP BY shot.league
        ), results AS (
            SELECT league, count(*) AS terminal_results FROM football_notify.basketball_forward_result
            WHERE study_key = %(study)s GROUP BY league
        ) SELECT * FROM attempts LEFT JOIN paired USING (league) LEFT JOIN results USING (league)
    """, {"study": STUDY, "scope": basketball.EXPLICIT_OT}).fetchall()
    for row in rows:
        counts[row["league"]].update({key: row[key] or 0 for key in counts[row["league"]]})
    return report | {"state": "registered"}


def tick(connection):
    """Capture real-time research windows and retry independently verified terminal results."""
    keys = (store.LOCK_NAMESPACE, LOCK)
    if not connection.execute("SELECT pg_try_advisory_lock(%s,%s) AS held", keys).fetchone()["held"]:
        return {"state": "running"}
    try:
        registered = _register(connection)
        now = store.db_now(connection)
        clock = feed._clock(now)
        captures = _due_fixtures(connection, now, registered)
        captured = Counter()
        results = Counter()
        with ThreadPoolExecutor(max_workers=8) as workers:
            for row, report, started, finished in workers.map(lambda row: _review(row, clock), captures):
                if report is not None and _record(connection, row, report, started, finished):
                    captured[row["phase"]] += 1
            due = defaultdict(list)
            for row in _due_results(connection, clock()):
                identity = tuple(row[name] for name in ("fixture_key", "fixture_revision", "market_id", "contract_digest"))
                due[identity].append(row)
            for settled_rows in workers.map(lambda rows: list(_result(rows, clock)), due.values()):
                for row, settled in settled_rows:
                    results[_write_result(connection, row, settled, store.db_now(connection))] += 1
        return status(connection) | {"captured_this_run": dict(captured), "results_this_run": dict(results)}
    finally:
        connection.execute("SELECT pg_advisory_unlock(%s,%s)", keys)
