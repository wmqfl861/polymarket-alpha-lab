"""Dedicated PostgreSQL state for the recurring football reminders."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


SOCKET = Path(os.environ.get("PAL_PG_SOCKET_DIR", ""))
PGDATA = os.environ.get("PAL_PG_DATA_DIR", "")
DATABASE = os.environ.get("PAL_PG_DATABASE", "")
PORT = os.environ.get("PAL_PG_PORT", "")
ROLE = os.environ.get("PAL_PG_ROLE", "")
OWNER_UID = os.environ.get("PAL_PG_OWNER_UID", "")
LEGACY = Path(os.environ.get("PAL_LEGACY_STATE_DIR", ""))
OWNER_PROOF_ROUTE = os.environ.get("PAL_OWNER_PROOF_ROUTE", "")
WINDOWS_HOST = os.environ.get("PAL_WINDOWS_HOST", "")
LOCK_NAMESPACE = 1957521795
QUEUE_LOCK = 1
SCAN_LOCK = 2
NATIVE_LOCK = 3
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
HEX16 = re.compile(r"[0-9a-f]{16}\Z")
NATIVE_SESSION = re.compile(r"sess_[A-Za-z0-9-]{1,96}\Z")
REASON = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
EXPLICIT_REFUSALS = ["zcode_tool_permission_denied", "hermes_platform_write_denied"]
PREFLIGHT_LEASE_LENGTH = timedelta(minutes=6)
ROUND_LEASE_LENGTH = timedelta(minutes=12)
# The submitted lease covers the full post-claim native launch and receipt lookup.
SUBMITTED_LEASE_LENGTH = timedelta(minutes=12)
MIN_HANDOFF = timedelta(seconds=25)
FIXTURE_FRESHNESS = timedelta(minutes=35)
PREGAME_STAGES = ("advance", "t90", "t30", "safety")
FOCUS_KIND = "verified_owner_focus"
# A claimed handoff can be replaced only after a separate native audit has proved
# that the original session finished without invoking the sender.
VERIFIED_NATIVE_NOT_INVOKED_SQL = """
    coalesce(j.stage='note' AND j.detail->>'kind'='verified_owner_focus'
        AND j.state='suppressed' AND j.reason='verified_native_not_invoked'
        AND a.state='unknown' AND a.reason='verified_native_not_invoked'
        AND j.attempt_key=a.key AND a.stage_key='wf-focus-' || left(j.key,48)
        AND j.detail->'native_non_submission'->>'attempt_key'=a.key
        AND j.deadline<=clock_timestamp() AND a.deadline<=clock_timestamp()
        AND j.accepted_at IS NULL AND a.confirmed_at IS NULL AND a.receipt_digest IS NULL, false)
"""

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS football_notify;

CREATE TABLE IF NOT EXISTS football_notify.binding (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    target_digest text NOT NULL CHECK (target_digest ~ '^[0-9a-f]{64}$'),
    marker_digest text NOT NULL CHECK (marker_digest ~ '^[0-9a-f]{64}$'),
    legacy_receipt_prefix text NOT NULL CHECK (legacy_receipt_prefix ~ '^[0-9a-f]{16}$'),
    activated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS football_notify.native_binding (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton)
        REFERENCES football_notify.binding (singleton),
    session_id text NOT NULL CHECK (session_id ~ '^sess_[A-Za-z0-9-]{1,96}$'),
    bound_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS football_notify.scan_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    last_success_at timestamptz,
    last_complete_at timestamptz,
    result text NOT NULL CHECK (result IN ('complete', 'partial', 'failed')),
    summary jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS football_notify.source_cache (
    cache_key text PRIMARY KEY,
    payload jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS football_notify.fixture (
    id text PRIMARY KEY,
    revision integer NOT NULL CHECK (revision > 0),
    kickoff timestamptz NOT NULL,
    fixture_state text NOT NULL CHECK (fixture_state IN ('scheduled', 'cancelled', 'postponed', 'started')),
    info jsonb NOT NULL,
    watch boolean NOT NULL,
    highlight boolean NOT NULL,
    first_seen timestamptz NOT NULL,
    last_seen timestamptz NOT NULL,
    missing_scans smallint NOT NULL DEFAULT 0 CHECK (missing_scans BETWEEN 0 AND 2),
    legacy_id text
);

CREATE INDEX IF NOT EXISTS football_fixture_window ON football_notify.fixture (kickoff);

CREATE TABLE IF NOT EXISTS football_notify.fixture_alias (
    source_id text PRIMARY KEY,
    fixture_key text NOT NULL REFERENCES football_notify.fixture (id)
);

CREATE TABLE IF NOT EXISTS football_notify.digest_day (
    local_day date PRIMARY KEY,
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    catch_up boolean NOT NULL,
    partial boolean NOT NULL,
    fixture_count integer NOT NULL
);

CREATE TABLE IF NOT EXISTS football_notify.attempt (
    key text PRIMARY KEY CHECK (key ~ '^[0-9a-f]{32}$'),
    stage_key text NOT NULL,
    body_variant text NOT NULL CHECK (body_variant IN ('primary', 'fallback')),
    body_digest text NOT NULL CHECK (body_digest ~ '^[0-9a-f]{64}$'),
    submitted_at timestamptz NOT NULL,
    deadline timestamptz NOT NULL,
    lease_until timestamptz NOT NULL,
    state text NOT NULL CHECK (state IN ('submitted', 'unknown', 'accepted')),
    reason text,
    recovered_at timestamptz,
    confirmed_at timestamptz,
    receipt_digest text CHECK (receipt_digest ~ '^[0-9a-f]{64}$')
);

ALTER TABLE football_notify.attempt ADD COLUMN IF NOT EXISTS native_session_digest text
    CHECK (native_session_digest ~ '^[0-9a-f]{64}$');
ALTER TABLE football_notify.attempt ADD COLUMN IF NOT EXISTS native_session_verified boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS football_attempt_native_lease ON football_notify.attempt (native_session_digest, lease_until)
    WHERE state IN ('submitted', 'unknown');

CREATE TABLE IF NOT EXISTS football_notify.job (
    key text PRIMARY KEY,
    fixture_key text REFERENCES football_notify.fixture (id),
    revision integer,
    stage text NOT NULL CHECK (stage IN ('digest', 'advance', 't90', 't30', 'safety', 'change', 'note')),
    local_day date REFERENCES football_notify.digest_day (local_day),
    starts_at timestamptz NOT NULL,
    deadline timestamptz NOT NULL,
    next_try timestamptz NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'leased', 'submitted', 'accepted', 'unknown', 'expired', 'suppressed')),
    detail jsonb NOT NULL,
    lease_token text,
    lease_until timestamptz,
    attempt_key text REFERENCES football_notify.attempt (key),
    tries integer NOT NULL DEFAULT 0,
    accepted_at timestamptz,
    reason text,
    CHECK (starts_at < deadline),
    CHECK ((fixture_key IS NULL) = (revision IS NULL)),
    CHECK ((local_day IS NOT NULL) = (stage = 'digest'))
);

CREATE INDEX IF NOT EXISTS football_job_due ON football_notify.job (next_try, deadline) WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS football_job_fixture ON football_notify.job (fixture_key, revision, stage);
CREATE INDEX IF NOT EXISTS football_job_attempt ON football_notify.job (attempt_key) WHERE attempt_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS football_job_day ON football_notify.job (local_day) WHERE local_day IS NOT NULL;

CREATE TABLE IF NOT EXISTS football_notify.legacy_acceptance (
    fixture_id text NOT NULL,
    lead smallint NOT NULL CHECK (lead IN (90, 30)),
    confirmed_at timestamptz NOT NULL,
    receipt_digest text NOT NULL CHECK (receipt_digest ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (fixture_id, lead)
);
"""

ROUND_SCHEMA = """
CREATE TABLE IF NOT EXISTS football_notify.pregame_round (
    key text PRIMARY KEY,
    dispatch_at timestamptz NOT NULL,
    member_count integer NOT NULL CHECK (member_count > 0),
    completed_at timestamptz,
    displayed_count smallint CHECK (displayed_count BETWEEN 0 AND 5),
    reason text,
    attempt_key text UNIQUE REFERENCES football_notify.attempt (key)
);

CREATE INDEX IF NOT EXISTS football_round_dispatch ON football_notify.pregame_round (dispatch_at DESC);
CREATE INDEX IF NOT EXISTS football_job_round ON football_notify.job ((detail->>'pregame_round'))
    WHERE detail ? 'pregame_round';
"""
SCHEMA += ROUND_SCHEMA


class StoreError(Exception):
    """A fixed diagnostic suitable for the service journal."""


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("expected a timestamp with an offset")
    return value.astimezone(UTC)


def is_hex(value, pattern=HEX64):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def safe_reason(value, default):
    return value if isinstance(value, str) and REASON.fullmatch(value) else default


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def connect(*, read_only=False):
    if (
        not SOCKET.is_absolute()
        or not Path(PGDATA).is_absolute()
        or not DATABASE
        or not ROLE
        or not OWNER_UID
        or re.fullmatch(r"[1-9][0-9]{0,4}", PORT) is None
        or int(PORT) > 65535
    ):
        raise StoreError("notification_postgres_scope_unconfigured")
    try:
        directory = SOCKET.lstat()
    except OSError as error:
        raise StoreError("notification_postgres_socket_missing") from error
    if (
        not stat.S_ISDIR(directory.st_mode)
        or str(directory.st_uid) != OWNER_UID
        or directory.st_mode & 0o077
        or str(os.geteuid()) != OWNER_UID
    ):
        raise StoreError("notification_postgres_socket_not_private")
    connection = psycopg.connect(
        host=str(SOCKET), hostaddr="", port=PORT, dbname=DATABASE, user=ROLE, password="", passfile="/dev/null",
        connect_timeout=5, sslmode="disable", gssencmode="disable",
        options="-c search_path=football_notify,pg_catalog -c timezone=UTC -c statement_timeout=10000 -c lock_timeout=5000",
        application_name="paper-football-weixin", row_factory=dict_row, autocommit=True,
    )
    try:
        guard = connection.execute(
            """
            SELECT current_database() AS database, current_user AS role,
                   inet_server_addr() IS NULL AND inet_client_addr() IS NULL AS unix_peer,
                   current_setting('listen_addresses') AS listen_addresses,
                   current_setting('data_directory') AS data_directory,
                   current_setting('unix_socket_directories') AS socket_directory,
                   current_setting('port') AS port
            """
        ).fetchone()
        if guard != {
            "database": DATABASE, "role": ROLE, "unix_peer": True, "listen_addresses": "",
            "data_directory": PGDATA, "socket_directory": str(SOCKET), "port": str(PORT),
        }:
            raise StoreError("notification_postgres_scope_mismatch")
        if read_only:
            connection.execute("SET default_transaction_read_only = on")
        return connection
    except BaseException:
        connection.close()
        raise


def lock_queue(connection):
    connection.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_NAMESPACE, QUEUE_LOCK))


def lock_scan(connection):
    return connection.execute(
        "SELECT pg_try_advisory_lock(%s, %s) AS granted", (LOCK_NAMESPACE, SCAN_LOCK)
    ).fetchone()["granted"]


@contextmanager
def native_delivery_lock(connection):
    keys = (LOCK_NAMESPACE, NATIVE_LOCK)
    granted = connection.execute("SELECT pg_try_advisory_lock(%s, %s) AS granted", keys).fetchone()["granted"]
    try:
        yield granted
    finally:
        if granted and not connection.closed:
            connection.execute("SELECT pg_advisory_unlock(%s, %s)", keys)


def holds_native_lock(connection):
    return connection.execute(
        """SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted
                 AND pid = pg_backend_pid() AND classid = %s::oid AND objid = %s::oid AND objsubid = 2
                 AND mode = 'ExclusiveLock') AS held""",
        (LOCK_NAMESPACE, NATIVE_LOCK),
    ).fetchone()["held"]


def db_now(connection):
    return connection.execute("SELECT clock_timestamp() AS at").fetchone()["at"]


def read_private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or str(metadata.st_uid) != OWNER_UID or metadata.st_mode & 0o077:
            raise ValueError("unexpected proof permissions")
        body = stream.read(4097)
    if len(body) > 4096:
        raise ValueError("oversized proof")
    document = json.loads(body)
    if not isinstance(document, dict):
        raise ValueError("invalid proof")
    return document


def owner_proof():
    if not LEGACY.is_absolute() or not WINDOWS_HOST or not OWNER_PROOF_ROUTE:
        raise StoreError("owner_binding_unverified")
    try:
        owner = read_private_json(LEGACY / "weixin-owner-verified.json")
        probe = read_private_json(LEGACY / "weixin-test-platform.json")
        target = owner.get("private_target_digest")
        marker = owner.get("marker_digest")
        receipt = owner.get("probe_message_id_digest_prefix")
        session = owner.get("source_session")
        if (
            owner.get("verified") is not True
            or owner.get("scope") != "2026-11-22 current five fixtures / private simulated advice only"
            or owner.get("host") != WINDOWS_HOST
            or probe.get("host") != WINDOWS_HOST
            or probe.get("route") != OWNER_PROOF_ROUTE
            or probe.get("platform") != "weixin"
            or probe.get("platform_success") is not True
            or not is_hex(target)
            or not is_hex(marker)
            or not is_hex(receipt, HEX16)
            or not is_hex(probe.get("private_target_digest"))
            or not is_hex(probe.get("message_id_sha256_prefix"), HEX16)
            or not hmac.compare_digest(target, probe["private_target_digest"])
            or not hmac.compare_digest(receipt, probe["message_id_sha256_prefix"])
            or not is_hex(session, NATIVE_SESSION)
            or not is_hex(probe.get("test_session"), NATIVE_SESSION)
            or not hmac.compare_digest(session, probe["test_session"])
        ):
            raise ValueError("owner and platform proofs differ")
        return target, marker, receipt, session
    except (OSError, ValueError) as error:
        raise StoreError("owner_binding_unverified") from error


def binding(connection):
    row = connection.execute("SELECT * FROM binding WHERE singleton").fetchone()
    if row is None or not is_hex(row["target_digest"]):
        raise StoreError("notification_owner_binding_uninitialized")
    return row


def native_session(connection):
    row = connection.execute("SELECT session_id FROM native_binding WHERE singleton").fetchone()
    if row is None or not is_hex(row["session_id"], NATIVE_SESSION):
        raise StoreError("notification_native_session_uninitialized")
    return row["session_id"]


def initialize(connection):
    with connection.transaction():
        lock_queue(connection)
        connection.execute("SET LOCAL synchronous_commit = on")
        connection.execute(SCHEMA)
        row = connection.execute("SELECT * FROM binding WHERE singleton").fetchone()
        if connection.execute("SELECT 1 FROM native_binding WHERE singleton").fetchone() is None:
            target, marker, receipt, session = owner_proof()
            if not is_hex(session, NATIVE_SESSION):
                raise StoreError("owner_binding_unverified")
            if row is None:
                connection.execute(
                    "INSERT INTO binding (target_digest, marker_digest, legacy_receipt_prefix) VALUES (%s, %s, %s)",
                    (target, marker, receipt),
                )
            elif not all(hmac.compare_digest(row[field], value) for field, value in (
                ("target_digest", target), ("marker_digest", marker), ("legacy_receipt_prefix", receipt),
            )):
                raise StoreError("owner_binding_unverified")
            connection.execute(
                "INSERT INTO native_binding (session_id) VALUES (%s)", (session,),
            )
        native_session(connection)
        return binding(connection)["activated_at"]


def ensure_round_schema(connection):
    with connection.transaction():
        lock_queue(connection)
        connection.execute(ROUND_SCHEMA)


def scan_state(connection):
    return connection.execute("SELECT * FROM scan_state WHERE singleton").fetchone()


def source_cache(connection):
    row = connection.execute("SELECT payload FROM source_cache WHERE cache_key = 'feed/1'").fetchone()
    return row["payload"] if row else None


def save_source_cache(connection, cache):
    if not isinstance(cache, dict):
        return
    try:
        encoded = json.dumps(cache, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        return
    if len(encoded) > 2_000_000 or b"\\u0000" in encoded:
        return
    connection.execute(
        """
        INSERT INTO source_cache (cache_key, payload) VALUES ('feed/1', %s)
        ON CONFLICT (cache_key) DO UPDATE SET payload = excluded.payload
        WHERE source_cache.payload IS DISTINCT FROM excluded.payload
        """,
        (Jsonb(cache),),
    )


def save_scan_state(connection, started, finished, result, summary):
    connection.execute(
        """
        INSERT INTO scan_state (started_at, finished_at, result, summary, last_success_at, last_complete_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (singleton) DO UPDATE SET
          started_at = excluded.started_at, finished_at = excluded.finished_at,
          result = excluded.result, summary = excluded.summary,
          last_success_at = coalesce(excluded.last_success_at, scan_state.last_success_at),
          last_complete_at = coalesce(excluded.last_complete_at, scan_state.last_complete_at)
        """,
        (started, finished, result, Jsonb(summary), finished if result != "failed" else None,
         finished if result == "complete" else None),
    )


def add_job(connection, stage, starts, deadline, *, fixture=None, day=None, detail=None, legacy_window=False):
    if stage not in PREGAME_STAGES or starts >= deadline:
        return
    fixture_key, revision = (fixture["id"], fixture["revision"]) if fixture else (None, None)
    key = digest(json.dumps([fixture_key, revision, stage, str(day) if day else None]))
    connection.execute(
        """
        INSERT INTO job (key, fixture_key, revision, stage, local_day, starts_at, deadline, next_try, state, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
        ON CONFLICT (key) DO UPDATE SET
          deadline = excluded.deadline,
          state = CASE WHEN job.state = 'suppressed' THEN 'pending' ELSE job.state END,
          next_try = CASE WHEN job.state = 'suppressed' THEN excluded.next_try ELSE job.next_try END,
          reason = CASE WHEN job.state = 'suppressed' THEN NULL ELSE job.reason END
        WHERE (job.stage = 'advance' AND job.state IN ('pending', 'leased') AND job.deadline > excluded.deadline)
            OR (%s AND ((job.state = 'suppressed' AND job.reason = 'owned_by_fixed_nov22_timer')
                OR (job.state IN ('pending', 'leased') AND job.deadline <> excluded.deadline)))
        """,
        (key, fixture_key, revision, stage, day, starts, deadline, starts, Jsonb(detail or {}), legacy_window),
    )


def claim_lineup_attempts(connection, now, *, lead, cutoff):
    minute = now.replace(second=0, microsecond=0).isoformat()
    rows = connection.execute(
        """
        UPDATE job j SET detail = jsonb_set(j.detail, '{confirmed_lineup_attempts}',
            coalesce(j.detail->'confirmed_lineup_attempts', '{}'::jsonb) || jsonb_build_object(
                %(minute)s::text, jsonb_build_object(
                    'attempted_at', %(now)s::timestamptz, 'status', 'started', 'reason', 'collection_started',
                    'fixture_id', f.info->>'fixture_id', 'league_id', f.info->>'fotmob_league_id',
                    'team_ids', jsonb_build_array(f.info->>'fotmob_home_id', f.info->>'fotmob_away_id'),
                    'kickoff', f.kickoff)))
        FROM fixture f
        WHERE j.fixture_key = f.id AND j.revision = f.revision AND j.stage = 't30' AND j.state = 'pending'
          AND f.watch AND f.fixture_state = 'scheduled' AND f.legacy_id IS NULL AND f.missing_scans < 2
          AND coalesce(f.info->>'sport', 'football') = 'football' AND f.last_seen BETWEEN %(fresh)s AND %(now)s
          AND f.kickoff > %(now)s + %(cutoff)s AND f.kickoff <= %(now)s + %(lead)s
          AND j.starts_at = f.kickoff - %(cutoff)s
          AND NOT EXISTS (
              SELECT FROM unnest(ARRAY['fixture_id', 'fotmob_league_id', 'fotmob_home_id', 'fotmob_away_id']) AS id(field)
              WHERE jsonb_typeof(f.info->id.field) IS DISTINCT FROM 'string' OR f.info->>id.field !~ '^[0-9]{1,78}$'
          )
          AND NOT coalesce((j.detail->'confirmed_lineup_attempts') ? %(minute)s::text, false)
        RETURNING j.key, j.fixture_key, j.revision, f.kickoff, f.info
        """,
        {"minute": minute, "now": now, "fresh": now - FIXTURE_FRESHNESS, "lead": lead, "cutoff": cutoff},
    ).fetchall()
    return minute, sorted(rows, key=lambda row: (row["kickoff"], row["fixture_key"]))


def finish_lineup_attempt(connection, row, minute, *, reason, context, recorded_at):
    result = {"status": "captured" if context else "rejected", "reason": reason,
              "recorded_at": recorded_at.isoformat()}
    if context:
        result["evidence"] = context
    written = connection.execute(
        """
        UPDATE job SET detail = jsonb_set(detail, %(path)s::text[], (detail #> %(path)s::text[]) || %(result)s)
        WHERE key = %(key)s AND revision = %(revision)s AND detail #>> %(status_path)s::text[] = 'started'
        """,
        {"key": row["key"], "revision": row["revision"], "path": ["confirmed_lineup_attempts", minute],
         "status_path": ["confirmed_lineup_attempts", minute, "status"], "result": Jsonb(result)},
    ).rowcount
    if written != 1:
        raise StoreError("confirmed_lineup_attempt_not_recorded")


def wake_confirmed_review(connection, claim, first, now, retryable):
    """Let the same T−90 job re-enter after publication, preserving the first round's audit."""
    row = connection.execute(
        """
        UPDATE job SET state = 'pending', reason = NULL, next_try = %(now)s,
            detail = (CASE WHEN state = 'suppressed' THEN detail - 'pregame_round' ELSE detail END)
                     || jsonb_build_object('confirmed_lineup_review_opened_at', %(first)s::timestamptz)
        WHERE fixture_key = %(fixture)s AND revision = %(revision)s AND stage = 't90'
          AND deadline > %(last_handoff)s AND attempt_key IS NULL
          AND ((state = 'pending' AND NOT (detail ? 'confirmed_lineup_review_opened_at'))
                OR (state = 'suppressed' AND reason = ANY(%(reasons)s)))
        RETURNING key
        """,
        {"now": now, "first": first, "fixture": claim["fixture_key"], "revision": claim["revision"],
         "last_handoff": now + MIN_HANDOFF, "reasons": sorted(retryable)},
    ).fetchone()
    return bool(row)


def earlier_accepted_sql(job="j", fixture="f"):
    return f"""
    EXISTS (SELECT 1 FROM job prior WHERE prior.fixture_key = {job}.fixture_key
            AND prior.revision = {job}.revision AND prior.stage IN ('advance', 't90', 't30')
            AND prior.state = 'accepted')
    OR EXISTS (SELECT 1 FROM legacy_acceptance old WHERE old.fixture_id = {fixture}.legacy_id)
    """


PRIOR_ACCEPTED = earlier_accepted_sql()
LEGACY_STAGE_ACCEPTED = """
    j.stage IN ('t90', 't30') AND EXISTS (
        SELECT 1 FROM legacy_acceptance old WHERE old.fixture_id = f.legacy_id
          AND old.lead = CASE j.stage WHEN 't90' THEN 90 WHEN 't30' THEN 30 END
    )
"""
VERIFIED_FOCUS_NOTE = f"j.stage = 'note' AND j.detail->>'kind' = '{FOCUS_KIND}'"
ACTIVE_NOTICE = f"(j.stage IN ('advance', 't90', 't30', 'safety') OR ({VERIFIED_FOCUS_NOTE}))"
USABLE = f"""
    {ACTIVE_NOTICE}
    AND (j.fixture_key IS NULL OR (j.revision = f.revision AND
      (f.fixture_state = 'scheduled' AND f.kickoff > %(now)s
       AND ((j.stage IN ('t90', 't30') AND f.legacy_id IS NOT NULL)
         OR (f.missing_scans < 2 AND f.last_seen >= %(fresh)s)))))
    AND (j.stage <> 'safety' OR NOT ({PRIOR_ACCEPTED}))
    AND NOT ({LEGACY_STAGE_ACCEPTED})
"""
WORK_CLASS = """
    CASE WHEN j.stage IN ('safety', 't30', 'note') OR j.deadline <= %(urgent_by)s
         THEN 'urgent' ELSE 'routine' END
"""
ROW_SELECT = """
    SELECT j.*, f.info, f.kickoff, f.highlight, f.watch, f.legacy_id, f.fixture_state
    FROM job j LEFT JOIN fixture f ON f.id = j.fixture_key
"""
DUE_SQL = f"""
    WHERE j.state = 'pending' AND j.next_try <= %(now)s AND j.starts_at <= %(now)s
      AND j.deadline > %(cutoff)s AND {USABLE}
"""


def clock_parameters(now):
    return dict(now=now, fresh=now - FIXTURE_FRESHNESS, cutoff=now + MIN_HANDOFF,
                urgent_by=now + timedelta(minutes=8))


def due_classes(connection, now):
    rows = connection.execute(
        f"""SELECT {WORK_CLASS} AS worker, count(*) AS count
            FROM job j LEFT JOIN fixture f ON f.id = j.fixture_key {DUE_SQL} GROUP BY worker""",
        clock_parameters(now),
    ).fetchall()
    return {row["worker"]: row["count"] for row in rows}


def due_rows(connection, worker, now):
    return connection.execute(
        f"""
        {ROW_SELECT} {DUE_SQL} AND ({WORK_CLASS}) = %(worker)s
          AND ({VERIFIED_FOCUS_NOTE})
        ORDER BY j.deadline, j.key LIMIT 1
        """,
        {**clock_parameters(now), "worker": worker},
    ).fetchall()


def due_pregame_rows(connection, now):
    return connection.execute(
        f"""
        {ROW_SELECT} {DUE_SQL} AND j.stage = ANY(%(stages)s) AND j.fixture_key IS NOT NULL
          AND j.starts_at <= %(dispatch)s
        ORDER BY j.starts_at, j.deadline, j.key
        """,
        {**clock_parameters(now), "stages": list(PREGAME_STAGES), "dispatch": now.replace(second=0, microsecond=0)},
    ).fetchall()


def round_slots(connection, keys):
    rows = connection.execute(
        "SELECT key, completed_at IS NOT NULL AS closed FROM pregame_round WHERE key = ANY(%s)", (list(keys),),
    ).fetchall()
    return {row["key"]: row["closed"] for row in rows}


def round_origin_aliases(connection, minutes):
    return connection.execute(
        """
        SELECT DISTINCT origin.minute, r.key, r.dispatch_at, r.completed_at IS NOT NULL AS closed
        FROM unnest(%s::timestamptz[]) AS origin(minute)
        JOIN job j ON j.starts_at > origin.minute - interval '1 minute' AND j.starts_at <= origin.minute
        JOIN pregame_round r ON r.key = coalesce(j.detail #>> '{pregame_review_history,0,round_key}',
                                                j.detail->>'pregame_round')
        WHERE r.dispatch_at > origin.minute
        ORDER BY r.dispatch_at, r.key
        """,
        (list(minutes),),
    ).fetchall()


def open_round(connection, key, dispatch_at, rows):
    connection.execute(
        "INSERT INTO pregame_round (key, dispatch_at, member_count) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (key, dispatch_at, len(rows)),
    )


def close_round(connection, key, now, selected_count, *, reason, attempt_key=None):
    changed = connection.execute(
        """UPDATE pregame_round SET completed_at = %s, displayed_count = %s, reason = %s, attempt_key = %s
           WHERE key = %s AND completed_at IS NULL""",
        (now, selected_count, reason, attempt_key, key),
    ).rowcount
    if changed != 1:
        raise StoreError("pregame_round_completion_conflict")


def record_late_round_arrivals(connection, rows, key):
    connection.execute(
        """UPDATE job SET state = 'suppressed', reason = 'arrived_after_round_close',
                          detail = detail || jsonb_build_object('pregame_closed_slot', %s::text)
           WHERE key = ANY(%s) AND state = 'pending' AND NOT (detail ? 'pregame_round')""",
        (key, [row["key"] for row in rows]),
    )


def suppress_schedule_jobs(connection):
    return connection.execute(
        f"""
        UPDATE job j SET state = 'suppressed', reason = 'schedule_notifications_disabled',
                         lease_token = NULL, lease_until = NULL
        WHERE j.stage IN ('digest', 'change', 'note') AND j.state IN ('pending', 'leased')
          AND j.attempt_key IS NULL AND NOT coalesce(({VERIFIED_FOCUS_NOTE}), false)
        """,
    ).rowcount


def sweep(connection, now, *, reclaim_round=False):
    suppress_schedule_jobs(connection)
    connection.execute(
        """
        UPDATE attempt SET state = 'unknown', reason = 'worker_interrupted_after_submit'
        WHERE state = 'submitted' AND lease_until <= %s
        """,
        (now,),
    )
    connection.execute(
        """
        UPDATE job SET state = 'unknown', reason = 'worker_interrupted_after_submit',
                       lease_token = NULL, lease_until = NULL
        WHERE state = 'submitted' AND lease_until <= %s
        """,
        (now,),
    )
    connection.execute(
        """
        UPDATE job SET state = 'expired', reason = 'window_ended', lease_token = NULL, lease_until = NULL,
                       detail = CASE WHEN stage = 'note' THEN detail - 'body' ELSE detail END
        WHERE state IN ('pending', 'leased') AND deadline <= %s
        """,
        (now,),
    )
    connection.execute(
        """
        UPDATE job SET state = 'pending', reason = 'worker_interrupted_before_submit',
                       lease_token = NULL, lease_until = NULL, next_try = %s
        WHERE state = 'leased' AND (lease_until <= %s OR (detail ? 'pregame_round' AND (
             %s OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted
                AND classid = %s::oid AND objid = %s::oid AND objsubid = 2 AND mode = 'ExclusiveLock')
        )))
        """,
        (now, now, reclaim_round, LOCK_NAMESPACE, NATIVE_LOCK),
    )
    connection.execute(
        f"""
        UPDATE job j SET state = 'suppressed', reason = 'earlier_platform_acceptance',
                         lease_token = NULL, lease_until = NULL
        FROM fixture f WHERE f.id = j.fixture_key AND j.state IN ('pending', 'leased')
          AND ((j.stage = 'safety' AND ({PRIOR_ACCEPTED})) OR ({LEGACY_STAGE_ACCEPTED}))
        """
    )
    connection.execute(
        """
        UPDATE pregame_round r SET completed_at = %s, displayed_count = 0, reason = 'no_remaining_members'
        WHERE r.completed_at IS NULL AND NOT EXISTS (
            SELECT 1 FROM job j WHERE j.detail ? 'pregame_round' AND j.detail->>'pregame_round' = r.key
              AND j.state IN ('pending', 'leased')
        )
        """,
        (now,),
    )


def lease_jobs(connection, rows, token, now, *, round_key=None):
    connection.execute(
        """
        UPDATE job SET state = 'leased', lease_token = %s, lease_until = %s, tries = tries + 1,
          detail = CASE WHEN %s::text IS NULL THEN detail
                        ELSE detail || jsonb_build_object('pregame_round', %s::text) END
        WHERE key = ANY(%s) AND state = 'pending'
        """,
        (token, now + (ROUND_LEASE_LENGTH if round_key else PREFLIGHT_LEASE_LENGTH), round_key, round_key,
         [row["key"] for row in rows]),
    )


def release_jobs(connection, rows, token):
    connection.execute(
        """
        UPDATE job SET state = 'pending', lease_token = NULL, lease_until = NULL
        WHERE key = ANY(%s) AND state = 'leased' AND lease_token = %s
        """,
        ([row["key"] for row in rows], token),
    )


def native_submission_active(connection, session, now):
    return connection.execute(
        """SELECT EXISTS (SELECT 1 FROM attempt WHERE native_session_digest = %s AND lease_until > %s
                         AND state IN ('submitted', 'unknown')) AS busy""",
        (digest(session), now),
    ).fetchone()["busy"]


def eligible_leases(connection, rows, token, now, *, handoff_margin=False):
    return connection.execute(
        f"""
        {ROW_SELECT}
        WHERE j.key = ANY(%(keys)s) AND j.state = 'leased' AND j.lease_token = %(token)s
          AND j.lease_until > %(now)s AND j.starts_at <= %(now)s AND j.deadline > %(deadline)s AND {USABLE}
        """,
        {**clock_parameters(now), "keys": [row["key"] for row in rows], "token": token,
         "deadline": now + MIN_HANDOFF if handoff_margin else now},
    ).fetchall()


def record_round_reviews(connection, token, entries, *, round_key=None, recorded_at=None):
    connection.execute(
        """
        UPDATE job j SET
          detail = (CASE WHEN result.next_try IS NULL THEN j.detail ELSE j.detail - 'pregame_round' END)
                   || jsonb_build_object('pregame_review', result.review)
                   || CASE WHEN %(round_key)s::text IS NULL THEN '{}'::jsonb ELSE
                       jsonb_build_object('pregame_review_history',
                         coalesce(j.detail->'pregame_review_history', '[]'::jsonb) ||
                         jsonb_build_array(result.review || jsonb_build_object(
                           'round_key', %(round_key)s::text, 'recorded_at', %(at)s::timestamptz,
                           'next_try', result.next_try))) END,
          state = CASE WHEN result.next_try IS NOT NULL THEN 'pending'
                       WHEN result.reason IS NOT NULL THEN 'suppressed' ELSE j.state END,
          reason = CASE WHEN result.reason IS NULL THEN j.reason ELSE result.reason END,
          next_try = coalesce(result.next_try, j.next_try),
          lease_token = CASE WHEN result.reason IS NULL AND result.next_try IS NULL THEN j.lease_token ELSE NULL END,
          lease_until = CASE WHEN result.reason IS NULL AND result.next_try IS NULL THEN j.lease_until ELSE NULL END
        FROM jsonb_to_recordset(%(entries)s) AS result(key text, reason text, review jsonb, next_try timestamptz)
        WHERE j.key = result.key AND j.state IN ('leased', 'submitted') AND j.lease_token = %(token)s
          AND (%(round_key)s::text IS NULL OR j.detail->>'pregame_round' = %(round_key)s)
          AND (result.next_try IS NULL OR j.state = 'leased')
        """,
        {"entries": Jsonb(entries), "token": token, "round_key": round_key, "at": recorded_at},
    )


def submit(connection, rows, token, target, stage_key, deadline, *, attempt_key, body_variant, body_digest, session):
    if (not is_hex(attempt_key, HEX32) or not is_hex(body_digest) or body_variant not in {"primary", "fallback"}
            or not is_hex(session, NATIVE_SESSION)):
        return False
    keys = [row["key"] for row in rows]
    with connection.transaction():
        lock_queue(connection)
        connection.execute("SET LOCAL synchronous_commit = on")
        now = db_now(connection)
        if (now >= deadline or target != binding(connection)["target_digest"] or session != native_session(connection)
                or not holds_native_lock(connection) or native_submission_active(connection, session, now)):
            return False
        eligible = eligible_leases(connection, rows, token, now)
        if len(eligible) != len(keys):
            return False
        lease_until = now + SUBMITTED_LEASE_LENGTH
        inserted = connection.execute(
            """
            INSERT INTO attempt (key, stage_key, body_variant, body_digest, submitted_at, deadline, lease_until,
                                 state, native_session_digest)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'submitted', %s) ON CONFLICT DO NOTHING
            """,
            (attempt_key, stage_key, body_variant, body_digest, now, deadline, lease_until, digest(session)),
        )
        if inserted.rowcount != 1:
            return False
        connection.execute(
            """UPDATE job SET state = 'submitted', attempt_key = %s, lease_until = %s,
                              detail = CASE WHEN stage = 'note' THEN detail - 'body' ELSE detail END
               WHERE key = ANY(%s)""",
            (attempt_key, lease_until, keys),
        )
    return True


def accepted_receipt(result, attempt, now):
    if (
        result.get("state") != "accepted" or not is_hex(result.get("message_id_sha256"))
        or result.get("attempt_key") != attempt["key"] or result.get("body_variant") != attempt["body_variant"]
    ):
        return None
    try:
        accepted = timestamp(result.get("confirmed_at"))
    except (ValueError, TypeError):
        return None
    if (
        result.get("platform") != "weixin" or result.get("target_verified") is not True
        or not attempt["submitted_at"] - timedelta(seconds=2) <= accepted <= min(attempt["deadline"], now + timedelta(seconds=2))
    ):
        return None
    return accepted, result["message_id_sha256"]


def finish_attempt(connection, attempt, result, now):
    if attempt["state"] == "accepted":
        return "accepted"
    pinned = attempt["native_session_digest"] is not None
    verified = attempt["native_session_verified"] or (pinned and result.get("native_session_verified") is True)
    receipt = accepted_receipt(result, attempt, now) if not pinned or verified else None
    if receipt:
        confirmed_at, receipt_digest = receipt
        connection.execute(
            """UPDATE attempt SET state = 'accepted', confirmed_at = %s, receipt_digest = %s, reason = NULL,
                                  native_session_verified = %s WHERE key = %s""",
            (confirmed_at, receipt_digest, verified, attempt["key"]),
        )
        connection.execute(
            """UPDATE job SET state = 'accepted', accepted_at = %s, reason = NULL,
                              lease_token = NULL, lease_until = NULL
               WHERE attempt_key = %s AND state IN ('submitted', 'unknown')""",
            (confirmed_at, attempt["key"]),
        )
        return "accepted"
    reason = safe_reason(result.get("reason"), "platform_acceptance_unconfirmed")
    if attempt["state"] == "unknown" and reason == "receipt_unconfirmed" and attempt["reason"]:
        reason = attempt["reason"]
    stopped = pinned and result.get("native_turn_finished") is True
    connection.execute(
        """UPDATE attempt SET state = 'unknown', reason = %s, native_session_verified = %s,
                              lease_until = CASE WHEN %s THEN least(lease_until, %s) ELSE lease_until END
           WHERE key = %s""",
        (reason, verified, stopped, now, attempt["key"]),
    )
    connection.execute(
        """UPDATE job SET state = 'unknown', reason = %s, lease_token = NULL, lease_until = NULL
           WHERE attempt_key = %s AND state = 'submitted'""",
        (reason, attempt["key"]),
    )
    return "unknown"


def finish_delivery(connection, rows, token, result, *, retry_seconds=None):
    keys = [row["key"] for row in rows]
    with connection.transaction():
        lock_queue(connection)
        now = db_now(connection)
        attempt = connection.execute(
            """SELECT a.* FROM attempt a JOIN job j ON a.key = j.attempt_key
               WHERE j.key = ANY(%s) AND (j.lease_token = %s OR a.key = %s) LIMIT 1""",
            (keys, token, result.get("attempt_key") if is_hex(result.get("attempt_key"), HEX32) else None),
        ).fetchone()
        if attempt:
            return finish_attempt(connection, attempt, result, now)
        reason = safe_reason(result.get("reason"), "handoff_not_submitted")
        connection.execute(
            """
            UPDATE job SET
                state = CASE WHEN deadline <= %s THEN 'expired' ELSE 'pending' END,
                detail = CASE WHEN stage = 'note' AND deadline <= %s THEN detail - 'body' ELSE detail END,
                next_try = %s + make_interval(secs => coalesce(%s, least(300, 30 * tries))), reason = %s,
                lease_token = NULL, lease_until = NULL
            WHERE key = ANY(%s) AND state = 'leased' AND lease_token = %s
            """,
            (now + MIN_HANDOFF, now + MIN_HANDOFF, now, retry_seconds, reason, keys, token),
        )
        return "preflight_failed"


def reserve_recovery(connection, now):
    # Unknown outcomes are never sent again. A recovery reservation allows one read-only receipt lookup.
    row = connection.execute(
        """
        SELECT * FROM attempt WHERE state = 'unknown' AND recovered_at IS NULL
         AND (native_session_digest IS NULL OR native_session_verified)
         AND NOT (coalesce(reason, '') = ANY(%s::text[]))
         AND submitted_at BETWEEN %s AND %s ORDER BY deadline LIMIT 1
        """,
        (EXPLICIT_REFUSALS, now - timedelta(hours=24), now - timedelta(seconds=20)),
    ).fetchone()
    if row:
        connection.execute("UPDATE attempt SET recovered_at = %s WHERE key = %s", (now, row["key"]))
    return row


def recoverable(connection, now):
    return connection.execute(
        """
        SELECT count(*) AS count FROM attempt WHERE state = 'unknown' AND recovered_at IS NULL
          AND (native_session_digest IS NULL OR native_session_verified)
          AND NOT (coalesce(reason, '') = ANY(%s::text[]))
          AND submitted_at BETWEEN %s AND %s
        """,
        (EXPLICIT_REFUSALS, now - timedelta(hours=24), now - timedelta(seconds=20)),
    ).fetchone()["count"]
