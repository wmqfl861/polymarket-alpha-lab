"""The single papersim connection and transaction adapter.

This is the ONLY new module in this change set that imports a database
driver; it is the single new entry in the frozen connector allowlist
guarded by ``tests/test_database_persistence_iron_rule.py`` (reviewed
D-CONN diff: exactly one exact path, no globs).

Contract (09D design v2 sections 3.3/3.4 and 5.8):

- Every raw DSN passes the single audited ``validate_local_postgres_dsn``
  (canonical import ``local_postgres_dsn``) BEFORE any connection, wrapper
  or callback is constructed; SQL is fully parameterized.
- The driver import is lazy (a function-body ``import psycopg`` placed
  after the audited validation) so a driverless offline environment still
  gets the validator's rejection instead of ``ImportError`` at module
  import time; the four connection entrypoints keep their signatures and
  behavior (M1 CI fix: offline run 43741792290).
- ``managed_dsn(info, owner=...)`` reproduces the V1 ``ProjectPostgres``
  DSN construction parameter-for-parameter (byte-equal; pinned by an
  equivalence test against ``ProjectPostgres._dsn`` so a V1 change turns
  the papersim tests red). Passwords travel only through libpq passfiles
  and are never read, logged or committed by this module.
- ``connect_managed`` additionally asserts the instance identity triple
  plus database/role on every managed application connection via
  ``project_postgres.binding`` (same guard as V1 research sessions).
- ``connect_legacy_readonly`` serves the frozen legacy paper_sim cluster
  through the audited read-only face only (default_transaction_read_only),
  with the operator password file convention; it never writes.
- Scan writes are ONE transaction: scan_runs + all candidates (+ price
  evidence) + entries + the matching cash events commit together, the
  same scan key with the same payload returns the original result, a
  changed payload is a conflict, and any exception leaves no half scan.
  Database failures never degrade to a file journal.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path, PurePosixPath
from selectors import DefaultSelector
import time
from uuid import uuid4

from polymarket_alpha_lab.local_postgres_dsn import validate_local_postgres_dsn
from polymarket_alpha_lab.papersim import settle
from polymarket_alpha_lab.papersim.store import (
    BANKROLL_BASE_REASONS,
    BankrollCheckRequest,
    BankrollCheckResult,
    CandidateMarkCapture,
    CommandRunEventSubmission,
    EffectiveMarketOutcome,
    MarketOutcomeObservation,
    PapersimStoreError,
    QueueControlSubmission,
    SETTLEMENT_CASH_REASONS,
    ScanSubmission,
    SettlementRecord,
    _as_decimal,
    bankroll_identity_difference,
    command_run_event_key,
    entry_mark_key,
    exact_sum,
    validate_evidence_time_order,
)
from polymarket_alpha_lab.project_postgres import binding, sql
from polymarket_alpha_lab.project_postgres.files import no_links, read_private

MANAGED_DSN_LABEL = 'PAPERSIM_MANAGED_PG_DSN'
OWNER_DSN_LABEL = 'PAPERSIM_OWNER_PG_DSN'
LEGACY_DSN_LABEL = 'PAPERSIM_PG_DSN'
EXPLICIT_DSN_LABEL = 'PAPERSIM_MIGRATIONS_PG_DSN'


def _quote(value: str) -> str:
    # Identical to the V1 server-side quoting so managed_dsn stays byte-equal
    # to ProjectPostgres._dsn (equivalence test pins this).
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def _validated(dsn: str, label: str) -> str:
    validate_local_postgres_dsn(dsn, env_var_name=label)
    return dsn


def read_instance(repo_root: Path) -> dict:
    """Read the project-private instance descriptor (no credentials inside)."""
    path = Path(repo_root).resolve() / '.local' / 'postgres' / 'instance.json'
    return json.loads(read_private(path))


def managed_dsn(info: dict, *, owner: bool = False, repo_root: Path = Path('.')) -> str:
    """Keyword DSN byte-equal to ``ProjectPostgres(repo_root)._dsn(info, owner=...)``."""
    home = Path(repo_root).resolve() / '.local' / 'postgres'
    values = {'host': '127.0.0.1', 'port': str(info['port']),
              'dbname': sql.DATABASE, 'user': sql.ADMIN if owner else sql.APPLICATION,
              'passfile': str(home / ('owner.pgpass' if owner else 'app.pgpass')),
              'connect_timeout': '5', 'sslmode': 'disable', 'gssencmode': 'disable',
              'application_name': 'polymarket-alpha-lab', 'options': '-c timezone=UTC'}
    dsn = ' '.join(f'{key}={_quote(value)}' for key, value in values.items())
    return _validated(dsn, OWNER_DSN_LABEL if owner else MANAGED_DSN_LABEL)


def _connect_with_deadline(dsn: str, clock: OptimizationCommandClock):
    """Open one audited DSN within the shorter of its timeout and the command bound."""
    from psycopg import Connection
    from psycopg.conninfo import conninfo_to_dict, timeout_from_conninfo
    from psycopg.generators import connect

    stage = 'connect'
    connect_start = time.monotonic_ns()
    _remaining_ns(clock.hard_ns, stage, minimum_ns=1)
    timeout = timeout_from_conninfo(conninfo_to_dict(dsn))
    cutoff_ns = min(clock.hard_ns, connect_start + timeout * _NS_PER_SECOND)
    with closing(connect(dsn, timeout=timeout)) as generator:
        pgconn = _wait_for_database(generator, cutoff_ns, stage)
    try:
        connection = Connection(pgconn)
        _remaining_ns(cutoff_ns, stage, minimum_ns=1)
    except BaseException:
        pgconn.finish()
        raise
    return connection


def connect_managed(repo_root: Path, *, info: dict | None = None, clock=None):
    """Application connection (pal_app) with per-connection identity assertion.

    Returns a verified IDLE connection, or raises with the connection
    closed: the identity check's read-only transaction is rolled back
    before hand-off (nothing from the check persists, and a caller's
    first ``conn.transaction()`` block is the OUTERMOST transaction and
    therefore commits); any check failure closes the connection first.
    """
    repo_root = Path(repo_root)
    if info is None:
        info = read_instance(repo_root)
    dsn = managed_dsn(info, owner=False, repo_root=repo_root)
    with binding.bound(info['instance_id'], info['root_sha256'], info['system_identifier']):
        if clock is None:
            import psycopg  # lazy: the audited validation already ran in managed_dsn

            connection = psycopg.connect(dsn)
        else:
            connection = _connect_with_deadline(dsn, clock)
        try:
            if clock is None:
                binding.verify_connection(connection.cursor())
                connection.rollback()
            else:
                with optimization_database_phase(connection, clock, stage='connection_identity', closeout=True):
                    binding.verify_connection(connection.cursor())
                    connection.rollback()
            connection._papersim_managed_root = repo_root.resolve()
        except BaseException:
            connection.close()      # fail closed: release, never hand off
            raise
    return connection


def d1_managed_identity(conn, repo_root: Path) -> dict:
    """Require the same verified, idle managed application session for D1."""
    root = Path(repo_root).resolve()
    if getattr(conn, '_papersim_managed_root', None) != root:
        raise PapersimStoreError('d1_operation_managed_required')
    _require_d1_idle(conn)
    info = read_instance(root)
    with binding.bound(info['instance_id'], info['root_sha256'], info['system_identifier']):
        with conn.transaction():
            binding.verify_connection(conn.cursor())
    return {'instance_id': info['instance_id'], 'application_role': sql.APPLICATION}


def d1_resolution_identity(conn, *, instance_id: str) -> str:
    """Recheck the marked managed session inside the caller's scope transaction."""
    root = getattr(conn, '_papersim_managed_root', None)
    if root is None or conn.info.transaction_status.name != 'INTRANS':
        raise PapersimStoreError('d1_operation_managed_required')
    info = read_instance(root)
    if info['instance_id'] != instance_id:
        raise PapersimStoreError('d1_operation_instance_conflict')
    with binding.bound(info['instance_id'], info['root_sha256'], info['system_identifier']):
        binding.verify_connection(conn.cursor())
    mode = conn.execute("SELECT current_setting('transaction_isolation'), "
                        "current_setting('transaction_read_only')").fetchone()
    if mode != ('repeatable read', 'on'):
        raise PapersimStoreError('d1_resolution_read_view_required')
    return sql.APPLICATION


def _require_d1_idle(conn) -> None:
    if conn.info.transaction_status.name != 'IDLE':
        raise PapersimStoreError('d1_operation_transaction_active')


@contextmanager
def d1_operation_lock(conn, operation_key: str):
    """Hold a parent lock across committed planning and item transactions."""
    _require_d1_idle(conn)
    key = 'papersim/d1/operation/' + operation_key
    locked = False
    try:
        try:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '30s'")
                conn.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', (key,))
                locked = True
        except Exception as error:
            if getattr(error, 'sqlstate', None) == '55P03':
                raise PapersimStoreError('d1_operation_lock_timeout') from None
            raise
        yield
    finally:
        if locked and not conn.closed:
            # A connection that cannot release its session lock must not be reused.
            try:
                _require_d1_idle(conn)
                with conn.transaction():
                    unlocked, = conn.execute(
                        'SELECT pg_advisory_unlock(hashtextextended(%s, 0))', (key,)).fetchone()
                    if not unlocked:
                        raise PapersimStoreError('d1_operation_lock_lost')
            except BaseException:
                conn.close()
                raise


@contextmanager
def d1_read_view(conn):
    """Pin the initial planning reads to one FX-29 read-only transaction."""
    _require_d1_idle(conn)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        yield


def d1_database_time(conn) -> datetime:
    """Sample the database wall clock, including inside a transaction."""
    with conn.transaction():
        at, = conn.execute('SELECT clock_timestamp()').fetchone()
    return at


def connect_owner(repo_root: Path, *, info: dict | None = None):
    """Owner connection (pal_owner) for migration application only."""
    repo_root = Path(repo_root)
    if info is None:
        info = read_instance(repo_root)
    dsn = managed_dsn(info, owner=True, repo_root=repo_root)
    import psycopg  # lazy: the audited validation already ran in managed_dsn
    return psycopg.connect(dsn)


def connect_explicit(dsn: str, *, label: str = EXPLICIT_DSN_LABEL, clock=None):
    """Validated explicit DSN connection (isolated/throwaway clusters only)."""
    dsn = _validated(dsn, label)  # pure Python: rejects before any driver import
    if clock is not None:
        return _connect_with_deadline(dsn, clock)
    import psycopg  # lazy: only a validated DSN may load the driver
    return psycopg.connect(dsn)


def connect_legacy_readonly(dsn: str, *, password_file: str | None = None):
    """Read-only connection to the frozen legacy paper_sim cluster.

    The DSN keeps flowing through the audited papersim configuration face;
    the session is forced read-only at connect time. The optional password
    file is the operator's one-line ``PAPERSIM_PG_PASSWORD_FILE``; its
    content is read here at connect time only and never logged or stored.
    """
    kwargs = {'connect_timeout': 5, 'options': '-c default_transaction_read_only=on'}
    if password_file is not None:
        path = Path(password_file)
        no_links(path)
        kwargs['password'] = path.read_text(encoding='utf-8').splitlines()[0]
    dsn = _validated(dsn, LEGACY_DSN_LABEL)  # pure Python, before any driver import
    import psycopg  # lazy: only a validated DSN may load the driver
    return psycopg.connect(dsn, **kwargs)


_OUTCOME_COLUMNS = ('evidence_key, condition_id, market_id, market_slug, yes_token_id, no_token_id, '
                    'outcome, outcome_detail, confirmed_at, observed_at, source_kind, source_ref, '
                    'source_snapshot_sha256')


def _outcome_row(cursor) -> dict:
    columns = [description.name for description in cursor.description]
    return dict(zip(columns, cursor.fetchone(), strict=True))


def _normalize(value):
    if value is None or isinstance(value, (bool, str, datetime)):
        return value
    return Decimal(str(value))


def record_market_outcome(conn, observation: MarketOutcomeObservation) -> dict:
    """Insert one outcome observation; idempotent per evidence_key (09D v2 section 4.1-4).

    A unique-key collision re-reads the stored row: an identical payload
    returns the original outcome_id; any difference raises
    ``papersim_outcome_evidence_conflict`` without overwriting the stored row.
    """
    import psycopg  # lazy: a live connection implies the native lane
    observation.validate()
    params = (observation.evidence_key(), observation.condition_id, observation.market_id,
              observation.market_slug, observation.yes_token_id, observation.no_token_id,
              observation.outcome, observation.outcome_detail, observation.confirmed_at,
              observation.observed_at, observation.source_kind, observation.source_ref,
              observation.source_snapshot_sha256)
    try:
        with conn.transaction():
            cursor = conn.cursor()
            cursor.execute('INSERT INTO papersim.market_outcomes (' + _OUTCOME_COLUMNS + ') '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING outcome_id', params)
            return {'outcome_id': int(cursor.fetchone()[0]), 'inserted': True}
    except psycopg.errors.UniqueViolation:
        pass  # rolled back above; the idempotent read-back follows
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT outcome_id, ' + _OUTCOME_COLUMNS + ' FROM papersim.market_outcomes '
                       'WHERE evidence_key = %s', (observation.evidence_key(),))
        stored = _outcome_row(cursor)
    expected = observation.comparison()
    for column, value in expected.items():
        if _normalize(stored[column]) != _normalize(value):
            raise PapersimStoreError('papersim_outcome_evidence_conflict')
    return {'outcome_id': int(stored['outcome_id']), 'inserted': False}


_IDENTITY_COLUMNS = ('condition_id', 'market_id', 'market_slug')


def effective_market_outcome(conn, *, condition_id=None, market_id=None, market_slug=None):
    """Effective outcome per 09D v2 section 4.1-3, corrected DISTINCT ON form.

    ``SELECT DISTINCT ON (<identity>) ... ORDER BY <identity>,
    observed_at DESC, outcome_id DESC``: the leftmost ORDER BY expressions
    must match the DISTINCT ON expressions, so the identity column leads
    the ordering and the recursion stays expressible (09D v2 N3 fix).
    """
    supplied = [(name, value) for name, value in zip(_IDENTITY_COLUMNS,
                                                      (condition_id, market_id, market_slug),
                                                      strict=True) if value is not None]
    if len(supplied) != 1:
        raise PapersimStoreError('papersim_market_identity_missing')
    column, identity = supplied[0]
    template = ('SELECT DISTINCT ON ({column}) outcome_id, outcome, confirmed_at, source_kind, '
                'observed_at FROM papersim.market_outcomes WHERE {column} = %s '
                'AND outcome {predicate} ORDER BY {column}, observed_at DESC, outcome_id DESC')
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(template.format(column=column, predicate='<> \'UNKNOWN\''), (identity,))
        row = cursor.fetchone()
        if row is None:
            cursor.execute(template.format(column=column, predicate='= \'UNKNOWN\''), (identity,))
            row = cursor.fetchone()
    if row is None:
        return EffectiveMarketOutcome(None, 'UNKNOWN')
    return EffectiveMarketOutcome(int(row[0]), row[1], row[4], row[3])


_DECISION_COLUMNS = ('evidence_id, evidence_key, candidate_id, evidence_role, observed_at, '
                     'model_probability, market_ref_kind, market_ref, token_ref, side, bid, ask, '
                     'mid, last, book_sha256, snapshot_sha256, source_kind, source_ref')


def decision_time_evidence(conn, candidate_id: int) -> dict | None:
    """The candidate's decision-time observation: smallest DECISION evidence_id (P3-8)."""
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT ' + _DECISION_COLUMNS + ' FROM papersim.candidate_price_evidence '
                       'WHERE candidate_id = %s AND evidence_role = \'DECISION\' '
                       'ORDER BY evidence_id LIMIT 1', (candidate_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    columns = [description.name for description in cursor.description]
    return dict(zip(columns, row, strict=True))


_CANDIDATE_INSERT = ('INSERT INTO papersim.candidates (scan_id, candidate_key, legacy_source_id, '
                     'condition_id, market_id, market_slug, yes_token_id, no_token_id, side, '
                     'market_kind, market_type, event_key, event_start_at, team, binding_version, '
                     'model_name, model_version, median_probability, variant_quotes, '
                     'input_payload, snapshot_refs, order_book, book_fetched_at, conclusion, '
                     'gate_report, rejection_reasons, evaluated_at, is_legacy) VALUES '
                     '(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'
                     '%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s) RETURNING candidate_id')

_EVIDENCE_INSERT = ('INSERT INTO papersim.candidate_price_evidence (evidence_key, candidate_id, '
                    'evidence_role, observed_at, model_probability, market_ref_kind, market_ref, '
                    'token_ref, side, bid, ask, mid, last, book_sha256, snapshot_sha256, '
                    'source_kind, source_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)')

_ENTRY_INSERT = ('INSERT INTO papersim.entries (candidate_id, legacy_source_id, stake, price, shares, '
                 'fee_category, fee_rate, entry_fee, fee_source, tier, entered_at, is_legacy) '
                 'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING entry_id')

_CASH_INSERT = ('INSERT INTO papersim.bankroll_events (event_key, occurred_at, currency, amount, '
                'reason, entry_id, scan_id, note, is_legacy) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)')


def record_scan(conn, submission: ScanSubmission) -> dict:
    """Write one complete scan in a single transaction (09D v2 section 5.8).

    Same scan key + same payload digest returns the original ids
    (``inserted=False``); a changed payload raises
    ``papersim_scan_payload_conflict``; any failure rolls the whole scan
    back so no half scan ever survives.
    """
    submission.validate()
    scan = submission.scan
    key = scan.key()
    payload = submission.payload_sha256()
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT scan_id, scan_payload_sha256 FROM papersim.scan_runs '
                       'WHERE scan_key = %s', (key,))
        existing = cursor.fetchone()
        if existing is not None:
            if existing[1] != payload:
                raise PapersimStoreError('papersim_scan_payload_conflict')
            scan_id = int(existing[0])
            return {'scan_id': scan_id, 'scan_key': key, 'inserted': False,
                    'candidate_ids': _read_back_candidates(cursor, scan_id),
                    'bankroll_event_keys': _read_back_event_keys(cursor, scan_id)}
        cursor.execute('INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, team, '
                       'started_at, finished_at, code_commit, schema_version, gate_version, '
                       'operator, params, is_legacy, operation_key) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) '
                       'RETURNING scan_id',
                       (key, payload, scan.team, scan.started_at, scan.finished_at, scan.code_commit,
                        scan.schema_version, scan.gate_version, scan.operator, scan.params,
                        scan.is_legacy, scan.operation_key))
        scan_id = int(cursor.fetchone()[0])
        candidate_ids: dict[str, int] = {}
        bankroll_keys: list[str] = []
        for candidate in submission.candidates:
            references = json.loads(candidate.snapshot_refs)
            if references:
                # 09D v2 5.3/D-4: snapshot references are validated jsonb
                # refs; the store layer proves each referenced snapshot is
                # already registered inside the same transaction.
                cursor.execute('SELECT content_sha256 FROM papersim.snapshots '
                               'WHERE content_sha256 = ANY(%s)', (references,))
                present = {row[0] for row in cursor.fetchall()}
                if any(reference not in present for reference in references):
                    raise PapersimStoreError('papersim_snapshot_ref_missing')
            cursor.execute(_CANDIDATE_INSERT, _candidate_params(scan_id, candidate))
            candidate_id = int(cursor.fetchone()[0])
            candidate_ids[candidate.candidate_key] = candidate_id
            for evidence in candidate.price_evidence:
                cursor.execute(_EVIDENCE_INSERT, _evidence_params(candidate_id, evidence))
            if candidate.entry is not None:
                entry = candidate.entry
                stake, fee = _as_decimal(entry.stake), _as_decimal(entry.entry_fee)
                cursor.execute(_ENTRY_INSERT, (candidate_id, entry.legacy_source_id, stake,
                                               _as_decimal(entry.price), _as_decimal(entry.shares),
                                               entry.fee_category, _as_decimal(entry.fee_rate),
                                               fee, entry.fee_source,
                                               entry.tier, entry.entered_at, entry.is_legacy))
                entry_id = int(cursor.fetchone()[0])
                event_key = f'entry:{entry_id}:cash'
                debit = exact_sum(stake, fee)
                cursor.execute(_CASH_INSERT, (event_key, entry.entered_at, 'USD',
                                              debit.copy_negate(),
                                              'SCAN_ENTRY', entry_id, scan_id, None, entry.is_legacy))
                bankroll_keys.append(event_key)
    return {'scan_id': scan_id, 'scan_key': key, 'inserted': True,
            'candidate_ids': candidate_ids, 'bankroll_event_keys': tuple(bankroll_keys)}


def _read_back_candidates(cursor, scan_id: int) -> dict:
    cursor.execute('SELECT candidate_id, candidate_key FROM papersim.candidates '
                   'WHERE scan_id = %s ORDER BY candidate_id', (scan_id,))
    return {key: int(identifier) for identifier, key in cursor.fetchall()}


def _read_back_event_keys(cursor, scan_id: int) -> tuple:
    cursor.execute('SELECT event_key FROM papersim.bankroll_events WHERE scan_id = %s '
                   'ORDER BY event_id', (scan_id,))
    return tuple(key for (key,) in cursor.fetchall())


def _candidate_params(scan_id: int, candidate) -> tuple:
    return (scan_id, candidate.candidate_key, candidate.legacy_source_id, candidate.condition_id,
            candidate.market_id, candidate.market_slug, candidate.yes_token_id, candidate.no_token_id,
            candidate.side, candidate.market_kind, candidate.market_type, candidate.event_key,
            candidate.event_start_at, candidate.team,
            candidate.binding_version, candidate.model_name, candidate.model_version,
            candidate.median_probability, candidate.variant_quotes, candidate.input_payload,
            candidate.snapshot_refs, candidate.order_book, candidate.book_fetched_at,
            candidate.conclusion, candidate.gate_report, candidate.rejection_reasons,
            candidate.evaluated_at, candidate.is_legacy)


def _evidence_params(candidate_id: int, evidence) -> tuple:
    return (evidence.evidence_key_with(candidate_id), candidate_id, evidence.evidence_role,
            evidence.observed_at, evidence.model_probability, evidence.market_ref_kind,
            evidence.market_ref, evidence.token_ref, evidence.side, evidence.bid, evidence.ask,
            evidence.mid, evidence.last, evidence.book_sha256, evidence.snapshot_sha256,
            evidence.source_kind, evidence.source_ref)


# ---------------------------------------------------------------------------
# M2 closed-loop operations (S0; M2 plan sections 3.5, 3.6, 5.3). Every
# statement is parameterized; every operation is ONE transaction with the
# record_scan pre-read idempotency pattern (same key + same content returns
# the original ids, changed content is a conflict, nothing is updated).
# ---------------------------------------------------------------------------

_CANDIDATE_MARK_COLUMNS = ('candidate_id, sampling_kind, observed_at, market_ref_kind, '
                           'market_ref, token_ref, side, bid, ask, mid, last, source_kind, '
                           'source_ref, snapshot_sha256')

_CANDIDATE_MARK_INSERT = ('INSERT INTO papersim.candidate_marks (candidate_mark_key, '
                          + _CANDIDATE_MARK_COLUMNS + ') '
                          'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                          'RETURNING candidate_mark_id')

_ENTRY_MARK_INSERT = ('INSERT INTO papersim.marks (entry_id, mark_key, marked_at, bid, ask, '
                      'mid, is_close, source_kind, source_ref) '
                      'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING mark_id')

_CLOSE_EVIDENCE_INSERT = ('INSERT INTO papersim.candidate_price_evidence (evidence_key, '
                          'candidate_id, evidence_role, observed_at, model_probability, '
                          'market_ref_kind, market_ref, token_ref, side, bid, ask, mid, last, '
                          'book_sha256, snapshot_sha256, source_kind, source_ref) '
                          'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                          'RETURNING evidence_id')

_SETTLEMENT_COLUMNS = ('classification, outcome_label, win_loss, exit_price, proceeds, '
                       'pnl_gross, fee_total, fee_source, pnl_net, source_outcome_id, '
                       'exit_mark_id, evidence, confirmed_by, confirmed_at, is_legacy')

_SETTLEMENT_INSERT = ('INSERT INTO papersim.settlements (entry_id, ' + _SETTLEMENT_COLUMNS
                      + ') VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                      'ON CONFLICT (entry_id) DO NOTHING RETURNING settlement_id')

_BANKROLL_CHECK_INSERT = ('INSERT INTO papersim.bankroll_checks (check_key, as_of, queue_id, '
                          'event_watermark, cash_total, deployed_total, initial_and_adjustments, '
                          'realized_net, open_entry_fees, identity_difference, tolerance, passed, '
                          'code_commit, report_digest) '
                          'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING check_id')


def _columns_of(cursor) -> list[str]:
    return [description.name for description in cursor.description]


def _compare_stored(stored: dict, expected: dict, code: str) -> None:
    for column, value in expected.items():
        if _normalize(stored[column]) != _normalize(value):
            raise PapersimStoreError(code)


def _lock_capture_target(cursor, candidate_id: int, mark_key: str) -> None:
    # The candidate lock serializes the unique CLOSE across distinct mark
    # keys. Take it first even for PERIODIC marks, before the natural-key lock.
    for key in (f'papersim/mark-candidate/{candidate_id}', f'papersim/mark/{mark_key}'):
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (key,))


def d1_lock_capture_target(conn, candidate_id: int, mark_key: str) -> None:
    """Protect a B2 read/write/witness transaction against competing writers."""
    if conn.info.transaction_status.name != 'INTRANS':
        raise PapersimStoreError('d1_operation_item_transaction_required')
    _lock_capture_target(conn.cursor(), candidate_id, mark_key)


def d1_capture_rows(conn, *, candidate_id: int, mark_key: str, close_key: str,
                    planned_close: bool) -> dict:
    """Read both natural keys and any other CLOSE that could block the write."""
    cursor = conn.cursor()
    query = ('SELECT candidate_mark_id, candidate_mark_key, ' + _CANDIDATE_MARK_COLUMNS
             + ' FROM papersim.candidate_marks WHERE candidate_mark_key = %s')
    params = (mark_key,)
    if planned_close:
        query += " OR (candidate_id = %s AND sampling_kind = 'CLOSE')"
        params += (candidate_id,)
    cursor.execute(query + ' ORDER BY candidate_mark_id', params)
    columns = _columns_of(cursor)
    marks = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    query = ('SELECT ' + _DECISION_COLUMNS
             + ' FROM papersim.candidate_price_evidence WHERE evidence_key = %s')
    params = (close_key,)
    if planned_close:
        query += " OR (candidate_id = %s AND evidence_role = 'CLOSE')"
        params += (candidate_id,)
    cursor.execute(query + ' ORDER BY evidence_id', params)
    columns = _columns_of(cursor)
    closes = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    return {'marks': marks, 'close_evidence': closes}


def d1_compare_capture_rows(rows: dict, capture: CandidateMarkCapture, close_evidence) -> dict:
    """Require the full, exact B2 row set and return the actual row IDs."""
    marks, closes = rows['marks'], rows['close_evidence']
    if len(marks) != 1 or marks[0]['candidate_mark_key'] != capture.mark_key():
        raise PapersimStoreError('papersim_candidate_mark_conflict')
    _compare_stored(marks[0], capture.comparison(), 'papersim_candidate_mark_conflict')
    evidence_id = None
    if close_evidence is None:
        if closes:
            raise PapersimStoreError('papersim_close_evidence_conflict')
    else:
        if len(closes) != 1:
            raise PapersimStoreError('papersim_close_evidence_conflict')
        columns = _DECISION_COLUMNS.replace(' ', '').split(',')[1:]
        expected = dict(zip(columns, _evidence_params(capture.candidate_id, close_evidence),
                            strict=True))
        _compare_stored(closes[0], expected, 'papersim_close_evidence_conflict')
        evidence_id = closes[0]['evidence_id']
    return {'candidate_mark_id': marks[0]['candidate_mark_id'], 'close_evidence_id': evidence_id}


def mark_capture_upsert(conn, capture: CandidateMarkCapture, *, entry_id: int | None = None,
                        close_evidence=None) -> dict:
    """Write one mark capture atomically (M2 plan section 3.5; FX-34).

    ``papersim.candidate_marks`` always receives the observation (marks do
    not depend on entries); the entry-level ``papersim.marks`` row is
    written when ``entry_id`` is given, with ``is_close`` mirroring the
    sampling kind; the CLOSE price evidence row is written when
    ``close_evidence`` (role CLOSE) is supplied, after the P3-9 time-order
    guard against the candidate's decision-time observation. At most one
    CLOSE exists per candidate and per entry; same key + same content
    returns the original ids; changed content is a conflict.

    FX-34 (fix-list section 2): any CLOSE attempt -- a CLOSE sampling
    kind and/or a CLOSE evidence row -- reads the candidate's FX-26
    ``papersim.candidates.event_start_at`` and refuses, inside the same
    transaction, when the stored start is NULL
    (``papersim_close_event_start_missing``: a candidate without an event
    start produces no closing price at all) or when the close observation
    is NOT strictly before the start
    (``papersim_evidence_time_order_invalid`` via
    ``store.validate_evidence_time_order``; equality refused). The
    B2 orchestrator downgrades such observations to PERIODIC before ever
    reaching this writer; this guard is the independent, fail-closed
    backstop for any other caller.
    """
    capture.validate()
    if entry_id is not None and type(entry_id) is not int:
        raise PapersimStoreError('papersim_entry_id_invalid')
    if close_evidence is not None:
        close_evidence.validate()
        if close_evidence.evidence_role != 'CLOSE':
            raise PapersimStoreError('papersim_close_evidence_role_invalid')
    mark_key = capture.mark_key()
    is_close = capture.sampling_kind == 'CLOSE'
    with conn.transaction():
        cursor = conn.cursor()
        _lock_capture_target(cursor, capture.candidate_id, mark_key)
        if is_close or close_evidence is not None:
            # FX-34 backstop: the boundary comes from the stored candidate
            # row itself (never from the caller), so a post-start or
            # start-less CLOSE can never be written through this face.
            cursor.execute('SELECT event_start_at FROM papersim.candidates '
                           'WHERE candidate_id = %s', (capture.candidate_id,))
            boundary_row = cursor.fetchone()
            if boundary_row is not None:
                event_start_at = boundary_row[0]
                if event_start_at is None:
                    raise PapersimStoreError('papersim_close_event_start_missing')
                close_observed_at = (close_evidence.observed_at
                                     if close_evidence is not None
                                     else capture.observed_at)
                decision = decision_time_evidence(conn, capture.candidate_id)
                validate_evidence_time_order(
                    None if decision is None else decision['observed_at'],
                    close_observed_at,
                    event_start_at=event_start_at)
        cursor.execute('SELECT candidate_mark_id, ' + _CANDIDATE_MARK_COLUMNS
                       + ' FROM papersim.candidate_marks WHERE candidate_mark_key = %s',
                       (mark_key,))
        columns = ['candidate_mark_id'] + _CANDIDATE_MARK_COLUMNS.replace(' ', '').split(',')
        row = cursor.fetchone()
        if row is None:
            if is_close:
                cursor.execute("SELECT 1 FROM papersim.candidate_marks "
                               "WHERE candidate_id = %s AND sampling_kind = 'CLOSE'",
                               (capture.candidate_id,))
                if cursor.fetchone() is not None:
                    raise PapersimStoreError('papersim_candidate_close_conflict')
            cursor.execute(_CANDIDATE_MARK_INSERT, (mark_key,) + tuple(
                capture.comparison()[name] for name in
                _CANDIDATE_MARK_COLUMNS.replace(' ', '').split(',')))
            candidate_mark_id = int(cursor.fetchone()[0])
            mark_inserted = True
        else:
            stored = dict(zip(columns, row, strict=True))
            _compare_stored(stored, capture.comparison(), 'papersim_candidate_mark_conflict')
            candidate_mark_id = int(stored['candidate_mark_id'])
            mark_inserted = False
        entry_mark_id = None
        entry_mark_inserted = False
        if entry_id is not None:
            marked_key = entry_mark_key(entry_id, capture.source_kind, capture.observed_at)
            cursor.execute('SELECT mark_id, bid, ask, mid, is_close, source_kind, source_ref '
                           'FROM papersim.marks WHERE mark_key = %s', (marked_key,))
            columns = ['mark_id', 'bid', 'ask', 'mid', 'is_close', 'source_kind', 'source_ref']
            row = cursor.fetchone()
            if row is None:
                if is_close:
                    cursor.execute('SELECT 1 FROM papersim.marks '
                                   'WHERE entry_id = %s AND is_close', (entry_id,))
                    if cursor.fetchone() is not None:
                        raise PapersimStoreError('papersim_entry_mark_conflict')
                cursor.execute(_ENTRY_MARK_INSERT,
                               (entry_id, marked_key, capture.observed_at, capture.bid,
                                capture.ask, capture.mid, is_close, capture.source_kind,
                                capture.source_ref))
                entry_mark_id = int(cursor.fetchone()[0])
                entry_mark_inserted = True
            else:
                stored = dict(zip(columns, row, strict=True))
                _compare_stored(stored, {'bid': capture.bid, 'ask': capture.ask,
                                         'mid': capture.mid, 'is_close': is_close,
                                         'source_kind': capture.source_kind,
                                         'source_ref': capture.source_ref},
                                'papersim_entry_mark_conflict')
                entry_mark_id = int(stored['mark_id'])
        close_evidence_id = None
        close_evidence_inserted = False
        if close_evidence is not None:
            # The P3-9/FX-34 guards (decision order + event-start boundary)
            # already ran in the CLOSE gate above, inside this transaction.
            key = close_evidence.evidence_key_with(capture.candidate_id)
            cursor.execute('SELECT ' + _DECISION_COLUMNS
                           + ' FROM papersim.candidate_price_evidence WHERE evidence_key = %s',
                           (key,))
            columns = [name for name in _DECISION_COLUMNS.replace(' ', '').split(',')]
            row = cursor.fetchone()
            if row is None:
                cursor.execute(_CLOSE_EVIDENCE_INSERT,
                               _evidence_params(capture.candidate_id, close_evidence))
                close_evidence_id = int(cursor.fetchone()[0])
                close_evidence_inserted = True
            else:
                stored = dict(zip(columns, row, strict=True))
                expected = _evidence_params(capture.candidate_id, close_evidence)
                _compare_stored(stored, dict(zip(columns[1:], expected, strict=True)),
                                'papersim_close_evidence_conflict')
                close_evidence_id = int(stored['evidence_id'])
    return {'candidate_mark_id': candidate_mark_id, 'candidate_mark_inserted': mark_inserted,
            'entry_mark_id': entry_mark_id, 'entry_mark_inserted': entry_mark_inserted,
            'close_evidence_id': close_evidence_id,
            'close_evidence_inserted': close_evidence_inserted}


def candidate_capture_target(conn, *, scan_id=None, scan_operation_key=None,
                             candidate_key: str) -> dict | None:
    """Resolve ONE mark-pack reference to its stored candidate (M2 B2).

    Exactly one of ``scan_id`` / ``scan_operation_key`` must be given: a
    candidate key is unique only inside its scan, so the pack names the
    producing scan either by id or by the B1 scheduling identity
    (``papersim.scan_runs.operation_key``, unique). The read returns
    ``{'candidate_id': int, 'event_start_at': datetime | None}`` -- the
    FX-26 column the orchestrator classifies PERIODIC vs CLOSE against
    (FX-34) -- or ``None`` when no such candidate exists (the caller
    fails loudly; marks are never attributed by guesswork). Read-only
    SELECT; the write side of B2 goes through ``mark_capture_upsert``.
    """
    if type(candidate_key) is not str or not candidate_key:
        raise PapersimStoreError('papersim_candidate_key_invalid')
    if (scan_id is None) == (scan_operation_key is None):
        raise PapersimStoreError('papersim_capture_scan_ref_invalid')
    with conn.transaction():
        cursor = conn.cursor()
        if scan_id is not None:
            if type(scan_id) is not int:
                raise PapersimStoreError('papersim_scan_id_invalid')
            cursor.execute('SELECT candidate_id, event_start_at FROM papersim.candidates '
                           'WHERE scan_id = %s AND candidate_key = %s',
                           (scan_id, candidate_key))
        else:
            cursor.execute('SELECT c.candidate_id, c.event_start_at FROM papersim.candidates c '
                           'JOIN papersim.scan_runs s ON s.scan_id = c.scan_id '
                           'WHERE s.operation_key = %s AND c.candidate_key = %s',
                           (scan_operation_key, candidate_key))
        row = cursor.fetchone()
    if row is None:
        return None
    return {'candidate_id': int(row[0]), 'event_start_at': row[1]}


def d1_candidate_capture_target(conn, *, scan_id=None, scan_operation_key=None,
                                candidate_key: str, settlement: bool = False) -> dict | None:
    """Resolve the full B1 identity, actual decision evidence and provenance."""
    if type(candidate_key) is not str or not candidate_key:
        raise PapersimStoreError('papersim_candidate_key_invalid')
    if (scan_id is None) == (scan_operation_key is None):
        raise PapersimStoreError('papersim_capture_scan_ref_invalid')
    if scan_id is not None and type(scan_id) is not int:
        raise PapersimStoreError('papersim_scan_id_invalid')

    columns = ('c.candidate_id, c.scan_id, c.candidate_key, c.team, c.market_id, '
               'c.condition_id, c.market_slug, c.side, c.yes_token_id, c.no_token_id, '
               'c.event_start_at, c.book_fetched_at, c.snapshot_refs, c.input_payload, '
               'c.is_legacy, s.operation_key AS scan_operation_key, s.team AS scan_team, '
               's.code_commit AS scan_code_commit, s.started_at AS scan_started_at, '
               's.finished_at AS scan_finished_at')
    if settlement:
        columns += ', c.market_kind, c.market_type, c.model_name, c.event_key, c.evaluated_at'
    ref_column, ref = ('c.scan_id', scan_id) if scan_id is not None else (
        's.operation_key', scan_operation_key)
    with conn.transaction():
        cursor = conn.execute(
            f'SELECT {columns} FROM papersim.candidates c '
            f'JOIN papersim.scan_runs s ON s.scan_id = c.scan_id '
            f'WHERE {ref_column} = %s AND c.candidate_key = %s', (ref, candidate_key))
        row = cursor.fetchone()
        if row is None:
            return None
        candidate = dict(zip(_columns_of(cursor), row, strict=True))
        cursor.execute('SELECT ' + _DECISION_COLUMNS + ' FROM papersim.candidate_price_evidence '
                       "WHERE candidate_id = %s AND evidence_role = 'DECISION' ORDER BY evidence_id",
                       (candidate['candidate_id'],))
        decision_columns = _columns_of(cursor)
        candidate['decisions'] = [dict(zip(decision_columns, decision, strict=True))
                                  for decision in cursor.fetchall()]
        candidate['scan_receipts'] = _d1_scan_receipts(conn, candidate)
    return candidate


def _d1_scan_receipts(conn, candidate: dict) -> list[dict]:
    # Select the original successful scan's attempt. Later scan attempts do
    # not change the provenance of a candidate already in a D1 read set.
    key = candidate['scan_operation_key']
    if key is None:
        return []
    receipts = d1_operation_receipts(conn, key)
    for receipt in receipts:
        if (receipt['command'] == 'run-scan' and receipt['phase'] == 'SUCCEEDED'
                and receipt['scan_id'] == candidate['scan_id']):
            return [row for row in receipts if row['command'] == 'run-scan'
                    and row['attempt'] == receipt['attempt']]
    return []


class SettlementCashDiscrepancy(PapersimStoreError):
    """A historical settlement with a missing or inconsistent cash leg."""

    def __init__(self, entry_id, settlement_id, expected_amount, leg, event_ids):
        super().__init__('papersim_settlement_cash_missing' if leg is None
                         else 'papersim_settlement_cash_mismatch')
        self.entry_id = entry_id
        self.settlement_id = settlement_id
        self.cash_event_id = None if leg is None else leg['event_id']
        self.expected_amount = expected_amount
        self.actual_amount = None if leg is None else leg['amount']
        self.occurred_at = None if leg is None else leg['occurred_at']
        self.event_ids = event_ids


def settlement_record(conn, record: SettlementRecord) -> dict:
    """Write or replay an operator-supplied settlement and its cash leg."""
    return _record_settlement(conn, record, automatic=None)


def record_automatic_settlement(conn, record: SettlementRecord,
                                automatic: settle.AutomaticSettlement) -> dict:
    """Write a B3 decision against the immutable facts selected at planning."""
    if not isinstance(automatic, settle.AutomaticSettlement):
        raise PapersimStoreError('papersim_automatic_settlement_context_missing')
    return _record_settlement(conn, record, automatic=automatic)


def _settlement_values(record: SettlementRecord) -> dict:
    values = record.comparison()
    for name in ('exit_price', 'proceeds', 'pnl_gross', 'fee_total', 'pnl_net'):
        if values[name] is not None:
            values[name] = _as_decimal(values[name])
    values['confirmed_at'] = settle.utc_instant(values['confirmed_at'])
    return values


def d1_lock_settlement_entry(conn, entry_id: int) -> None:
    """Hold the entry's writer lock until the active transaction ends."""
    if conn.info.transaction_status.name != 'INTRANS':
        raise PapersimStoreError('d1_operation_item_transaction_required')
    _lock_settlement_entry(conn.cursor(), entry_id)


def _lock_settlement_entry(cursor, entry_id: int) -> None:
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                   (f'papersim/settlement-entry/{entry_id}',))


def _terminal_cash_values(record: SettlementRecord, at: datetime, paid_fee) -> dict:
    return {'event_key': record.event_key(), 'occurred_at': at, 'currency': 'USD',
            'amount': record.cash_amount(entry_fee=paid_fee), 'reason': record.cash_reason(),
            'entry_id': record.entry_id, 'scan_id': None, 'note': record.evidence, 'is_legacy': record.is_legacy}


_CASH_READ_COLUMNS = ('event_id, event_key, occurred_at, currency, amount, reason, '
                     'entry_id, scan_id, note, is_legacy, reverses_event_id')


def _terminal_cash_rows(cursor, entry_id: int, event_key: str) -> list[dict]:
    cursor.execute('SELECT ' + _CASH_READ_COLUMNS + ' FROM papersim.bankroll_events WHERE event_key = %s '
                   'OR (entry_id = %s AND reason = ANY(%s)) ORDER BY event_id',
                   (event_key, entry_id, list(SETTLEMENT_CASH_REASONS.values())))
    columns = _columns_of(cursor)
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _record_settlement(conn, record: SettlementRecord, *, automatic) -> dict:
    record.validate()
    values = _settlement_values(record)
    with conn.transaction():
        cursor = conn.cursor()
        if getattr(conn, '_papersim_managed_root', None) is not None:
            _lock_settlement_entry(cursor, record.entry_id)
        paid_fee = None
        if automatic is not None:
            paid_fee = _validate_automatic_settlement(cursor, record, values, automatic)

        cursor.execute(_SETTLEMENT_INSERT, (record.entry_id, *(
            values[name] for name in _SETTLEMENT_COLUMNS.split(', '))))
        row = cursor.fetchone()
        inserted = row is not None
        if inserted:
            settlement_id = int(row[0])
        else:
            # In READ COMMITTED this statement sees the pair committed by the
            # writer that won the unique key. Stronger isolation can raise a
            # serialization failure, which leaves this transaction via rollback.
            cursor.execute('SELECT settlement_id, ' + _SETTLEMENT_COLUMNS
                           + ' FROM papersim.settlements WHERE entry_id = %s',
                           (record.entry_id,))
            row = cursor.fetchone()
            if row is None:
                raise PapersimStoreError('papersim_settlement_conflict')
            stored = dict(zip(_columns_of(cursor), row, strict=True))
            stored['confirmed_at'] = settle.utc_instant(stored['confirmed_at'])
            _compare_stored(stored, values, 'papersim_settlement_conflict')
            settlement_id = int(stored['settlement_id'])

        if record.classification == 'VOIDED_REFUND' and paid_fee is None:
            cursor.execute('SELECT stake, entry_fee FROM papersim.entries WHERE entry_id = %s',
                           (record.entry_id,))
            stake, paid_fee = cursor.fetchone()
            if stake != values['proceeds']:
                raise PapersimStoreError('papersim_voided_proceeds_mismatch')
        cash = _terminal_cash_values(record, values['confirmed_at'], paid_fee)
        if inserted:
            cursor.execute(_CASH_INSERT, tuple(cash.values()))
        legs = _terminal_cash_rows(cursor, record.entry_id, cash['event_key'])
        leg = next((row for row in legs if row['event_key'] == cash['event_key']), None)
        if leg is not None:
            leg['occurred_at'] = settle.utc_instant(leg['occurred_at'])
        if (len(legs) != 1 or leg is None or leg['reverses_event_id'] is not None
                or any(leg[name] != value for name, value in cash.items())):
            raise SettlementCashDiscrepancy(record.entry_id, settlement_id, cash['amount'],
                                            leg, tuple(row['event_id'] for row in legs))
        bankroll_event_id = int(leg['event_id'])
    return {'settlement_id': settlement_id, 'inserted': inserted,
            'bankroll_event_id': bankroll_event_id}


_ENTRY_JOIN = ('FROM papersim.entries e '
               'JOIN papersim.candidates c ON c.candidate_id = e.candidate_id ')
_ENTRY_COLUMNS = ('e.entry_id, e.candidate_id, c.team, c.side, e.stake, e.price, '
                  'e.fee_category, e.fee_source, e.entered_at, c.condition_id, c.market_id, '
                  'c.market_slug, c.scan_id, e.entry_fee, e.is_legacy')
_OPEN_ENTRIES_SQL = (
    'SELECT ' + _ENTRY_COLUMNS + ', '
    'EXISTS (SELECT 1 FROM papersim.marks m WHERE m.entry_id = e.entry_id '
    'AND m.is_close) AS exit_marked '
    + _ENTRY_JOIN
    + 'WHERE NOT EXISTS (SELECT 1 FROM papersim.settlements s WHERE s.entry_id = e.entry_id) '
    'ORDER BY e.entry_id'
)
_CAPTURED_OUTCOME_SELECT = (
    'SELECT outcome_id, condition_id, market_id, market_slug, outcome, observed_at, '
    'confirmed_at, source_kind FROM papersim.market_outcomes '
)


def _opening_cash(cursor, entry: dict) -> settle.OpeningCash | settle.EntryCashDiscrepancy:
    return _check_opening_cash(entry, _opening_cash_rows(cursor, entry['entry_id']))


def _opening_cash_rows(cursor, entry_id: int) -> list[dict]:
    cursor.execute(
        'SELECT b.event_id, b.event_key, b.occurred_at, b.currency, b.amount, b.reason, '
        'b.entry_id, b.scan_id, b.note, b.is_legacy, b.reverses_event_id, '
        'EXISTS (SELECT 1 FROM papersim.bankroll_events r '
        'WHERE r.reverses_event_id = b.event_id) AS reversed '
        'FROM papersim.bankroll_events b WHERE b.event_key = %s '
        "OR (b.entry_id = %s AND b.reason = 'SCAN_ENTRY') ORDER BY b.event_id",
        (f'entry:{entry_id}:cash', entry_id),
    )
    columns = _columns_of(cursor)
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _check_opening_cash(entry: dict, rows: list[dict]) -> settle.OpeningCash | settle.EntryCashDiscrepancy:
    entry_id = entry['entry_id']
    event_key = f'entry:{entry_id}:cash'
    stake, fee = entry['stake'], entry['entry_fee']
    expected = exact_sum(stake, fee).copy_negate()
    if len(rows) != 1:
        state = 'missing' if not rows else 'ambiguous'
        return settle.EntryCashDiscrepancy(entry_id, state,
                                          tuple(row['event_id'] for row in rows), expected)
    event = rows[0]
    amount = event['amount']
    if (event['event_key'] != event_key or event['entry_id'] != entry_id
            or event['scan_id'] != entry['scan_id'] or event['currency'] != 'USD'
            or event['reason'] != 'SCAN_ENTRY' or event['is_legacy'] != entry['is_legacy']
            or settle.utc_instant(event['occurred_at']) != settle.utc_instant(entry['entered_at'])
            or event['reverses_event_id'] is not None or event['reversed']):
        return settle.EntryCashDiscrepancy(entry_id, 'metadata',
                                          (event['event_id'],), expected, amount)
    paid_fee = exact_sum(amount.copy_negate(), stake.copy_negate())
    if amount != expected or paid_fee != fee or paid_fee < 0:
        return settle.EntryCashDiscrepancy(entry_id, 'amount',
                                          (event['event_id'],), expected, amount)
    return settle.OpeningCash(event['event_id'], amount, paid_fee)


def _validate_automatic_settlement(cursor, record, values, automatic) -> Decimal:
    cursor.execute('SELECT ' + _ENTRY_COLUMNS + ' ' + _ENTRY_JOIN
                   + 'WHERE e.entry_id = %s', (record.entry_id,))
    row = cursor.fetchone()
    if row is None:
        raise PapersimStoreError('papersim_automatic_settlement_entry_missing')
    entry = dict(zip(_columns_of(cursor), row, strict=True))
    selected = automatic.outcome
    cursor.execute(_CAPTURED_OUTCOME_SELECT + 'WHERE outcome_id = %s',
                   (selected.outcome_id,))
    row = cursor.fetchone()
    if row is None:
        raise PapersimStoreError('papersim_automatic_settlement_evidence_mismatch')
    actual = settle.CapturedOutcome.from_row(dict(zip(_columns_of(cursor), row, strict=True)))
    identity = settle.MarketIdentity.from_row(entry)
    if (actual.identity != selected.identity or actual.outcome != selected.outcome
            or actual.source_kind != selected.source_kind
            or settle.utc_instant(actual.observed_at) != settle.utc_instant(selected.observed_at)
            or settle.utc_instant(actual.confirmed_at) != settle.utc_instant(selected.confirmed_at)
            or identity.match(actual.identity) != automatic.market_match):
        raise PapersimStoreError('papersim_automatic_settlement_evidence_mismatch')

    opening = _opening_cash(cursor, entry)
    if isinstance(opening, settle.EntryCashDiscrepancy):
        raise settle.EntryCashMismatch(opening)
    if opening != automatic.opening_cash:
        raise settle.EntryCashMismatch(settle.EntryCashDiscrepancy(
            record.entry_id, 'snapshot_changed', (opening.event_id,),
            automatic.opening_cash.amount, opening.amount))
    snapshot = settle.OpenSettleEntry(**{key: value for key, value in entry.items() if key != 'scan_id'},
                                      outcome=actual, opening_cash=opening)
    expected = settle.plan_settlement(snapshot, operator=record.confirmed_by).record
    if (expected is None or _settlement_values(expected) != values
            or _as_decimal(expected.exit_fee) != _as_decimal(record.exit_fee)
            or expected.cash_amount(entry_fee=opening.paid_fee) !=
            record.cash_amount(entry_fee=opening.paid_fee)):
        raise PapersimStoreError('papersim_automatic_settlement_record_mismatch')
    return opening.paid_fee


def open_entries_for_settlement(conn):
    """Read the open entries, captured history and opening cash in one FX-29 snapshot."""
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cursor.execute(_OPEN_ENTRIES_SQL)
        columns = _columns_of(cursor)
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        identities = tuple(list({row[column] for row in rows if row[column] is not None})
                           for column in settle.MARKET_IDENTITY_COLUMNS)
        indexed = {}
        if any(identities):
            cursor.execute(_CAPTURED_OUTCOME_SELECT
                           + 'WHERE condition_id = ANY(%s) OR market_id = ANY(%s) '
                           'OR market_slug = ANY(%s)', identities)
            columns = _columns_of(cursor)
            indexed = settle.index_captured_outcomes(
                dict(zip(columns, row, strict=True)) for row in cursor.fetchall())
        entries = []
        for row in rows:
            identity = settle.MarketIdentity.from_row(row)
            captured = settle.select_entry_outcome(
                row['entry_id'], row['entered_at'], identity,
                settle.captured_for_identity(indexed, identity))
            opening, discrepancy = None, None
            if captured is not None and captured.is_effective:
                result = _opening_cash(cursor, row)
                if isinstance(result, settle.EntryCashDiscrepancy):
                    discrepancy = result
                else:
                    opening = result
            entries.append(settle.OpenSettleEntry(
                **{key: value for key, value in row.items() if key != 'scan_id'},
                outcome=captured, opening_cash=opening, cash_discrepancy=discrepancy))
    return tuple(entries)


def d1_read_settlement_snapshot(conn, sha256: str) -> dict | None:
    """Read and rehash a registered source body in the active evidence view."""
    from polymarket_alpha_lab.papersim import snapshots

    row = snapshots.read_snapshot(conn, sha256)
    if row is not None and (len(row['content']) != row['byte_length']
                            or snapshots.snapshot_content_sha256(row['content']) != sha256):
        raise PapersimStoreError('d1_settlement_source_snapshot_conflict')
    return row


def d1_resolution_entries(conn, *, team: str, entry_ids: list[int]) -> dict[int, dict]:
    """Read the actual typed identities of entries nominated for acquisition."""
    if not entry_ids:
        return {}
    cursor = conn.execute(
        'SELECT e.entry_id, e.candidate_id, c.condition_id, c.market_id, c.market_slug, '
        'c.yes_token_id, c.no_token_id ' + _ENTRY_JOIN
        + 'WHERE c.team = %s AND e.entry_id = ANY(%s)', (team, entry_ids))
    columns = _columns_of(cursor)
    return {row[0]: dict(zip(columns, row, strict=True)) for row in cursor.fetchall()}


_D1_SETTLE_ENTRIES = (
    'SELECT ' + _ENTRY_COLUMNS + ', c.candidate_key, '
    'EXISTS (SELECT 1 FROM papersim.marks m WHERE m.entry_id = e.entry_id AND m.is_close) AS exit_marked, '
    'EXISTS (SELECT 1 FROM papersim.settlements s WHERE s.entry_id = e.entry_id) AS already_settled '
    + _ENTRY_JOIN + 'WHERE c.team = %s AND e.entry_id = ANY(%s)'
)
_D1_OUTCOME_SELECT = 'SELECT outcome_id, ' + _OUTCOME_COLUMNS + ' FROM papersim.market_outcomes '


def _cash_reversals(cursor, events: list[dict]) -> list[dict]:
    if not events:
        return []
    cursor.execute('SELECT ' + _CASH_READ_COLUMNS + ' FROM papersim.bankroll_events '
                   'WHERE reverses_event_id = ANY(%s) ORDER BY event_id',
                   ([event['event_id'] for event in events],))
    columns = _columns_of(cursor)
    return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]


def d1_settlement_targets(conn, *, team: str, entry_ids: list[int],
                          source_outcome_id: int | None = None) -> list[dict]:
    """Read each nominated entry's facts in their original order and active view.

    Give the scoped attempt every candidate outcome during planning. The
    lock-time read supplies the originally selected ID and retrieves that
    exact row, even if newer outcomes have appeared in the meantime.
    """
    if not entry_ids:
        return []
    cursor = conn.execute(_D1_SETTLE_ENTRIES, (team, entry_ids))
    columns = _columns_of(cursor)
    indexed = {row[0]: dict(zip(columns, row, strict=True)) for row in cursor.fetchall()}
    results = []
    for entry_id in entry_ids:
        raw = indexed.get(entry_id)
        if raw is None:
            results.append({'entry_id': entry_id, 'state': 'MISSING', 'entry': None,
                            'candidate': None, 'outcomes': [], 'cash_rows': [], 'opening_cash': None})
            continue
        state = 'ALREADY_SETTLED' if raw.pop('already_settled') else 'OPEN'
        candidate = d1_candidate_capture_target(conn, scan_id=raw['scan_id'],
                                                candidate_key=raw.pop('candidate_key'), settlement=True)
        if source_outcome_id is None:
            cursor.execute(_D1_OUTCOME_SELECT + 'WHERE condition_id = %s OR market_id = %s OR market_slug = %s '
                           'ORDER BY outcome_id', tuple(raw[name] for name in settle.MARKET_IDENTITY_COLUMNS))
        else:
            cursor.execute(_D1_OUTCOME_SELECT + 'WHERE outcome_id = %s', (source_outcome_id,))
        outcome_columns = _columns_of(cursor)
        outcomes = [dict(zip(outcome_columns, row, strict=True)) for row in cursor.fetchall()]
        cash = _opening_cash_rows(cursor, entry_id)
        all_cash = sorted([*cash, *_cash_reversals(cursor, cash)], key=lambda row: row['event_id'])
        results.append({'entry_id': entry_id, 'state': state, 'entry': raw, 'candidate': candidate,
                        'outcomes': outcomes, 'cash_rows': all_cash, 'opening_cash': _check_opening_cash(raw, cash)})
    return results


def d1_settlement_rows(conn, record: SettlementRecord) -> dict:
    """Read a settlement and every terminal cash row that could conflict."""
    cursor = conn.execute('SELECT settlement_id, entry_id, ' + _SETTLEMENT_COLUMNS
                          + ' FROM papersim.settlements WHERE entry_id = %s', (record.entry_id,))
    columns = _columns_of(cursor)
    settled = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    cash = _terminal_cash_rows(cursor, record.entry_id, record.event_key())
    return {'settlements': settled, 'cash': cash, 'reversals': _cash_reversals(cursor, cash)}


def d1_settlement_opening_rows(conn, entry_id: int) -> list[dict]:
    """Read the entry's original opening debit and all immediate reversals."""
    cursor = conn.cursor()
    cash = _opening_cash_rows(cursor, entry_id)
    return sorted([*cash, *_cash_reversals(cursor, cash)], key=lambda row: row['event_id'])


def d1_compare_settlement_rows(rows: dict, record: SettlementRecord, opening: settle.OpeningCash) -> dict:
    """Check the exact ledger decision and its single reasoned USD cash leg."""
    values = _settlement_values(record)
    settled, cash = rows['settlements'], rows['cash']
    if len(settled) != 1 or settled[0]['entry_id'] != record.entry_id:
        raise PapersimStoreError('papersim_settlement_conflict')
    _compare_stored(settled[0], values, 'papersim_settlement_conflict')
    expected = _terminal_cash_values(record, values['confirmed_at'], opening.paid_fee)
    if (len(cash) != 1 or cash[0]['reverses_event_id'] is not None or rows['reversals']
            or any(cash[0][name] != value for name, value in expected.items())):
        raise PapersimStoreError('papersim_settlement_cash_mismatch')
    return {'settlement_id': settled[0]['settlement_id'], 'cash_event_id': cash[0]['event_id']}


def bankroll_daily_check(conn, request: BankrollCheckRequest) -> BankrollCheckResult:
    """Recompute the four-way cash identity and append the receipt (M2 5.3).

    Event cash, base funds, open principal, realized net PnL and open
    entry fees are recomputed independently inside ONE read transaction
    limited by ``as_of`` (no post-as_of rows mix in), the watermark is the
    highest included event id, and the receipt is appended with the
    day+commit+watermark idempotency key. The row is evidence: it never
    repairs the identity.

    FX-29 (N-4): the multi-statement identity recomputation reads ONE
    consistent snapshot — the FIRST statement of the transaction raises
    the isolation level to REPEATABLE READ, so an entry + cash event
    committed by another connection between this function's statements
    can never split the read (all sums see the same pre- or post-commit
    state). The transaction stays read-write because the receipt insert
    belongs to the same snapshot-bounded transaction; on a static
    database the results are bit-identical to the previous per-statement
    default isolation.
    """
    request.validate()
    tolerance = _as_decimal(request.tolerance)
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        cursor.execute('SELECT coalesce(max(event_id), 0) FROM papersim.bankroll_events '
                       'WHERE occurred_at <= %s', (request.as_of,))
        watermark = int(cursor.fetchone()[0])
        cursor.execute('SELECT coalesce(sum(amount), 0) FROM papersim.bankroll_events '
                       'WHERE occurred_at <= %s', (request.as_of,))
        cash = _as_decimal(cursor.fetchone()[0])
        cursor.execute('SELECT coalesce(sum(amount), 0) FROM papersim.bankroll_events '
                       'WHERE reason = ANY(%s) AND occurred_at <= %s',
                       (list(BANKROLL_BASE_REASONS), request.as_of))
        base = _as_decimal(cursor.fetchone()[0])
        cursor.execute('SELECT coalesce(sum(e.stake), 0), coalesce(sum(e.entry_fee), 0) '
                       'FROM papersim.entries e WHERE e.entered_at <= %s AND NOT EXISTS '
                       '(SELECT 1 FROM papersim.settlements s WHERE s.entry_id = e.entry_id '
                       'AND s.confirmed_at <= %s)', (request.as_of, request.as_of))
        deployed, open_fees = (_as_decimal(value) for value in cursor.fetchone())
        cursor.execute('SELECT coalesce(sum(pnl_net), 0) FROM papersim.settlements '
                       'WHERE confirmed_at <= %s', (request.as_of,))
        realized = _as_decimal(cursor.fetchone()[0])
        difference = bankroll_identity_difference(cash, deployed, base, realized, open_fees)
        passed = difference.copy_abs() <= tolerance
        key = request.key(watermark)
        cursor.execute('SELECT check_id, event_watermark, cash_total, deployed_total, '
                       'initial_and_adjustments, realized_net, open_entry_fees, '
                       'identity_difference, tolerance, passed FROM papersim.bankroll_checks '
                       'WHERE check_key = %s', (key,))
        columns = ['check_id', 'event_watermark', 'cash_total', 'deployed_total',
                   'initial_and_adjustments', 'realized_net', 'open_entry_fees',
                   'identity_difference', 'tolerance', 'passed']
        row = cursor.fetchone()
        if row is not None:
            stored = dict(zip(columns, row, strict=True))
            expected = {'event_watermark': watermark, 'cash_total': cash,
                        'deployed_total': deployed, 'initial_and_adjustments': base,
                        'realized_net': realized, 'open_entry_fees': open_fees,
                        'identity_difference': difference, 'tolerance': tolerance,
                        'passed': passed}
            _compare_stored(stored, expected, 'papersim_bankroll_check_conflict')
            check_id = int(stored['check_id'])
            inserted = False
        else:
            cursor.execute(_BANKROLL_CHECK_INSERT,
                           (key, request.as_of, request.queue_id, watermark, cash, deployed,
                            base, realized, open_fees, difference, tolerance, passed,
                            request.code_commit, request.report_digest))
            check_id = int(cursor.fetchone()[0])
            inserted = True
    return BankrollCheckResult(check_id=check_id, check_key=key, inserted=inserted,
                               as_of=request.as_of, event_watermark=watermark,
                               cash_total=cash, deployed_total=deployed,
                               initial_and_adjustments=base, realized_net=realized,
                               open_entry_fees=open_fees,
                               identity_difference=difference, tolerance=tolerance,
                               passed=passed)


# ---------------------------------------------------------------------------
# B1 run-scan operation writes (M2 plan section 3.1: the command run
# receipts and the FX-17 queue-control latch; 05 section 2 B1 row).
#
# MO-7 SEAM (B1 plan D7, fourth wiring point): the shadow-prediction
# writer joins THIS section when the registry exists (it writes
# papersim.shadow_predictions from the scan's non-active roles); until
# then there is intentionally no shadow write anywhere -- with the
# registry empty the non-active role set is empty, so a shadow row here
# would break the registered "bit-identical output" requirement.
# ---------------------------------------------------------------------------

_COMMAND_RUN_INSERT = ('INSERT INTO papersim.command_run_events (run_event_key, '
                       'operation_key, attempt, phase, command, queue_id, scheduled_for, '
                       'occurred_at, code_commit, config_digest, input_evidence, '
                       'output_evidence, error_code, scan_id) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) '
                       'RETURNING run_event_id')

_COMMAND_RUN_READ = ('SELECT run_event_id, run_event_key, operation_key, attempt, phase, command, '
                     'queue_id, scheduled_for, occurred_at, code_commit, config_digest, '
                     'input_evidence, output_evidence, error_code, scan_id, '
                     'paper_only, report_only, readonly FROM papersim.command_run_events ')

_QUEUE_CONTROL_INSERT = ('INSERT INTO papersim.queue_control_events (control_key, queue_id, '
                         'control_kind, rule, rule_version, metrics, evidence_watermark, '
                         'reason, reviewed_by, observed_at, scan_id) '
                         'VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s) '
                         'RETURNING control_event_id')


def _json_text(value) -> str:
    """Canonical JSON text (the jsonb parameters are always bound as text)."""
    if type(value) is str:
        return value
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _compare_rows(stored: dict, expected: dict, code: str) -> None:
    """Idempotency comparison over mixed scalar/jsonb columns.

    ``_compare_stored`` normalizes every value through ``Decimal`` and
    therefore only handles scalars; jsonb columns come back from the
    driver as lists/dicts and compare structurally instead.
    """
    for column in expected:
        if stored.get(column) != expected[column]:
            if isinstance(expected[column], (list, dict)):
                raise PapersimStoreError(code)
            if _normalize(stored.get(column)) != _normalize(expected[column]):
                raise PapersimStoreError(code)


def read_command_run_event(conn, operation_key: str, attempt: int, phase: str) -> dict | None:
    """Read the immutable first row's actual database ID and receipt clock."""
    key = command_run_event_key(operation_key, attempt, phase)
    with conn.transaction():
        cursor = conn.execute(_COMMAND_RUN_READ + 'WHERE run_event_key = %s', (key,))
        row = cursor.fetchone()
        if row is None:
            return None
        receipt = dict(zip(_columns_of(cursor), row, strict=True))
        if (receipt['operation_key'], receipt['attempt'], receipt['phase']) != (operation_key, attempt, phase):
            raise PapersimStoreError('papersim_command_run_conflict')
        return receipt


def d1_operation_receipts(conn, operation_key: str) -> list[dict]:
    """Read the ordered append-only history for one operation identity."""
    with conn.transaction():
        cursor = conn.execute(_COMMAND_RUN_READ + 'WHERE operation_key = %s ORDER BY run_event_id',
                              (operation_key,))
        columns = _columns_of(cursor)
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def d1_first_failed_item(conn, item_keys, *, command: str, before_attempt: int) -> dict | None:
    """Find a committed item error even if its parent never recorded failure."""
    with conn.transaction():
        cursor = conn.execute(
            _COMMAND_RUN_READ + "WHERE operation_key = ANY(%s) AND command = %s "
            "AND attempt < %s AND phase = 'FAILED' ORDER BY run_event_id LIMIT 1",
            (list(item_keys), command, before_attempt))
        row = cursor.fetchone()
        return None if row is None else dict(zip(_columns_of(cursor), row, strict=True))


def record_command_run_event(conn, submission: CommandRunEventSubmission) -> dict:
    """Append one run receipt row (idempotent on the scheduling identity).

    Same run-event key + same content returns the original row
    (``inserted=False``); a changed content under the same key is a
    conflict; rows are never updated (append-only triggers on the
    table), so every attempt phase of an operation stays as history.
    """
    submission.validate()
    key = submission.key()
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT operation_key, attempt, phase, command, queue_id, '
                       'scheduled_for, code_commit, config_digest, input_evidence, '
                       'output_evidence, error_code, scan_id '
                       'FROM papersim.command_run_events WHERE run_event_key = %s', (key,))
        columns = ['operation_key', 'attempt', 'phase', 'command', 'queue_id',
                   'scheduled_for', 'code_commit', 'config_digest', 'input_evidence',
                   'output_evidence', 'error_code', 'scan_id']
        row = cursor.fetchone()
        content = (submission.operation_key, submission.attempt, submission.phase,
                   submission.command, submission.queue_id, submission.scheduled_for,
                   submission.code_commit, submission.config_digest,
                   json.loads(_json_text(submission.input_evidence)),
                   json.loads(_json_text(submission.output_evidence)),
                   submission.error_code, submission.scan_id)
        if row is not None:
            stored = dict(zip(columns, row, strict=True))
            expected = dict(zip(columns, content, strict=True))
            _compare_rows(stored, expected, 'papersim_command_run_conflict')
            return {'run_event_id': None, 'run_event_key': key, 'inserted': False}
        cursor.execute(_COMMAND_RUN_INSERT,
                       (key, submission.operation_key, submission.attempt,
                        submission.phase, submission.command, submission.queue_id,
                        submission.scheduled_for, submission.occurred_at,
                        submission.code_commit, submission.config_digest,
                        _json_text(submission.input_evidence),
                        _json_text(submission.output_evidence),
                        submission.error_code, submission.scan_id))
        run_event_id = int(cursor.fetchone()[0])
    return {'run_event_id': run_event_id, 'run_event_key': key, 'inserted': True}


def record_queue_control_event(conn, submission: QueueControlSubmission) -> dict:
    """Append one queue-control (pause latch) evidence row, idempotently.

    ``queue_control_key`` includes ``observed_at`` (store.py), so two
    scans tripping the same rule append two DISTINCT rows by design --
    pause rows are appended evidence, and only a reviewed RESUME row may
    follow a pause.  Same key + same content returns the original row.
    """
    submission.validate()
    key = submission.key()
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT queue_id, control_kind, rule, rule_version, metrics, '
                       'evidence_watermark, reason, reviewed_by, scan_id '
                       'FROM papersim.queue_control_events WHERE control_key = %s', (key,))
        columns = ['queue_id', 'control_kind', 'rule', 'rule_version', 'metrics',
                   'evidence_watermark', 'reason', 'reviewed_by', 'scan_id']
        row = cursor.fetchone()
        content = (submission.queue_id, submission.control_kind, submission.rule,
                   submission.rule_version, json.loads(_json_text(submission.metrics)),
                   json.loads(_json_text(submission.evidence_watermark)),
                   submission.reason, submission.reviewed_by, submission.scan_id)
        if row is not None:
            stored = dict(zip(columns, row, strict=True))
            expected = dict(zip(columns, content, strict=True))
            _compare_rows(stored, expected, 'papersim_queue_control_conflict')
            return {'control_event_id': None, 'control_key': key, 'inserted': False}
        cursor.execute(_QUEUE_CONTROL_INSERT,
                       (key, submission.queue_id, submission.control_kind,
                        submission.rule, submission.rule_version,
                        _json_text(submission.metrics),
                        _json_text(submission.evidence_watermark), submission.reason,
                        submission.reviewed_by, submission.observed_at,
                        submission.scan_id))
        control_event_id = int(cursor.fetchone()[0])
    return {'control_event_id': control_event_id, 'control_key': key, 'inserted': True}


def latched_pauses(conn) -> dict:
    """Fold the pause latch per queue from the append-only control rows.

    Rows are read in append order (``observed_at``, then the identity
    column as the tie-break).  Folding is per queue, matching the
    migration's "only a reviewed RESUME row may follow one [pause]":
    every PAUSE sets the latch of its rule family (``fx17*`` -> review,
    anything else -> the blueprint universal stop); a RESUME clears ALL
    latches of its queue (per-queue unlatch).  Any number of rows folds
    idempotently: repeated PAUSE evidence between two RESUMEs leaves
    the flags unchanged, and ``scan.py`` feeds these flags straight
    into ``queue_policy.queue_pause_input`` (decisions only stay or
    escalate -- nothing here auto-resumes).
    """
    review: dict[str, bool] = {}
    stop: dict[str, bool] = {}
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT queue_id, control_kind, rule FROM '
                       'papersim.queue_control_events ORDER BY observed_at, '
                       'control_event_id')
        for queue_id, control_kind, rule in cursor.fetchall():
            queue = str(queue_id)
            if str(control_kind) == 'RESUME':
                review[queue] = False
                stop[queue] = False
                continue
            rule_text = str(rule or '')
            if rule_text.startswith('fx17'):
                review[queue] = True
            else:
                stop[queue] = True
    return {queue: (review.get(queue, False), stop.get(queue, False))
            for queue in sorted(set(review) | set(stop))}


# ---------------------------------------------------------------------------
# MO-7 shadow-prediction writes (04 section 10 step 3; plan review R2,
# decision 2: ONE separate transaction after the scan's own atomic
# write, so an optimizer fault never stops the scan).
# ---------------------------------------------------------------------------

_SHADOW_COLUMNS = ('candidate_id, spec_sha256, role_at_prediction, probability, '
                   'variant_quotes, refusal_reason, gate_pass, gate_reason, '
                   'hypothetical_fill_price, predicted_at')
_SHADOW_INSERT = ('INSERT INTO papersim.shadow_predictions (shadow_key, '
                  + _SHADOW_COLUMNS + ') VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s) '
                    'RETURNING shadow_prediction_id')


def record_shadow_predictions(conn, bindings, candidate_ids) -> dict:
    """Append one scan's shadow predictions in ONE transaction (MO-7).

    Two-layer idempotency like the writers above: same shadow key +
    same content returns the original row (``inserted`` counts only
    the fresh rows), a changed content under the same key raises
    ``papersim_shadow_conflict``, and nothing is ever updated (the
    append-only triggers on the table).  ``bindings`` are the scan's
    ``scan.ShadowBinding`` rows (candidate ids still unresolved): each
    ``candidate_key`` resolves through ``candidate_ids`` -- the map
    ``record_scan`` returns from the candidate inserts (or reads back
    on the idempotent replay path) -- and materializes one validated
    ``store.ShadowPredictionSubmission``.  Shadow predictions never
    carry an entry or a cash leg (04 section 10 step 3).
    """

    from polymarket_alpha_lab.papersim.store import (
        ShadowPredictionSubmission,
        _require_json_payload,
    )

    rows = 0
    inserted = 0
    with conn.transaction():
        cursor = conn.cursor()
        columns = _SHADOW_COLUMNS.replace(' ', '').split(',')
        for binding in bindings:
            try:
                candidate_id = candidate_ids[binding.candidate_key]
            except (KeyError, TypeError) as error:
                raise PapersimStoreError('papersim_shadow_candidate_missing') from error
            variant_text = ('[]' if binding.variant_quotes is None else json.dumps(
                [float(value) for value in binding.variant_quotes],
                separators=(',', ':'), allow_nan=False))
            # numeric columns bind exact decimals (the bind_entry /
            # PriceEvidenceObservation pattern): psycopg's float literal
            # is shorter than repr, so a float bind would make the
            # idempotent replay compare unequal decimals.
            probability = (None if binding.probability is None
                           else Decimal(str(binding.probability)))
            fill_price = (None if binding.hypothetical_fill_price is None
                          else Decimal(str(binding.hypothetical_fill_price)))
            submission = ShadowPredictionSubmission(
                candidate_id=candidate_id,
                spec_sha256=binding.spec_sha256,
                role_at_prediction=binding.role_at_prediction,
                probability=probability,
                variant_quotes=variant_text,
                refusal_reason=binding.refusal_reason,
                gate_pass=binding.gate_pass,
                gate_reason=binding.gate_reason,
                hypothetical_fill_price=fill_price,
                predicted_at=binding.predicted_at)
            submission.validate()
            key = submission.key()
            cursor.execute('SELECT ' + _SHADOW_COLUMNS
                           + ' FROM papersim.shadow_predictions WHERE shadow_key = %s',
                           (key,))
            row = cursor.fetchone()
            content = submission.comparison()
            if row is not None:
                stored = dict(zip(columns, row, strict=True))
                _compare_stored_row(stored, content, 'papersim_shadow_conflict')
                rows += 1
                continue
            cursor.execute(_SHADOW_INSERT, (key,) + tuple(
                _require_json_payload(content[name], name)
                if name == 'variant_quotes' else content[name]
                for name in columns))
            cursor.fetchone()
            rows += 1
            inserted += 1
    return {'rows': rows, 'inserted': inserted}


def load_shadow_predictions(conn, candidate_id: int) -> list[dict]:
    """Read back one candidate row's shadow predictions (replay side)."""

    cursor = conn.cursor()
    cursor.execute('SELECT shadow_key, ' + _SHADOW_COLUMNS
                   + ' FROM papersim.shadow_predictions WHERE candidate_id = %s'
                   ' ORDER BY shadow_prediction_id', (candidate_id,))
    columns = ['shadow_key'] + _SHADOW_COLUMNS.replace(' ', '').split(',')
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# MO-2 model registry writes and reads (04 section 9; 05 node MO-2).
# Same two-layer idempotency as the blocks above: stable writer-side
# key + exact content comparison; nothing is ever updated. The optimize
# subpackage is imported INSIDE functions only -- store_psycopg sits on
# the CLI's database path and the import-purity / startup-module-set
# contracts of tests/test_papersim_optimize_spec.py must not see
# optimize modules at import time.
# ---------------------------------------------------------------------------

from polymarket_alpha_lab.papersim.store import (  # noqa: E402  (MO-2 append block)
    ModelSpecSubmission,
    OptimizationRunSubmission,
    OptimizerControlEventSubmission,
    PromotionDecisionSubmission,
    RoleEventBatch,
    RoleEventSubmission,
    model_role_event_key,
    normalize_json_number,
    _require_json_payload,
)

_SPEC_COLUMNS = ('canonical_text, family, procedure_version, model_version, '
                 'legacy_model_version, params, envelope, data_contract, source, '
                 'parent_spec_sha256, optimization_id, code_commit')
_SPEC_INSERT = ('INSERT INTO papersim.model_specs (spec_sha256, ' + _SPEC_COLUMNS
                + ') VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s) '
                  'RETURNING 1')

_RUN_COLUMNS = ('family, lineages, data_cutoff, dataset_summary, snapshot_refs, '
                'search_space, rule_version, random_seed, trial_count, budget, '
                'active_specs, outcome, scorecard, artifacts_sha256, code_commit')
_RUN_INSERT = ('INSERT INTO papersim.optimization_runs (optimization_key, '
               + _RUN_COLUMNS + ') VALUES (%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,'
                 '%s::jsonb,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s) '
                 'RETURNING optimization_id')

_PROMOTION_COLUMNS = ('lineage, decision_class, candidate_spec_sha256, '
                     'active_spec_sha256, rule_version, look_number, '
                     'evidence_watermark, metrics, conclusion, block_reason, '
                     'decided_by, decided_at')
_PROMOTION_INSERT = ('INSERT INTO papersim.promotion_decisions (decision_key, '
                    + _PROMOTION_COLUMNS + ') VALUES (%s,%s,%s,%s,%s,%s,%s,'
                      '%s::jsonb,%s::jsonb,%s,%s,%s,%s) RETURNING decision_id')

_ROLE_EVENT_COLUMNS = ('lineage, spec_sha256, role, effective_at, reason, '
                       'forward_plan, decision_id, actor, code_commit')
_ROLE_EVENT_INSERT = ('INSERT INTO papersim.model_role_events (role_event_key, '
                      + _ROLE_EVENT_COLUMNS + ') VALUES (%s,%s,%s,%s,%s,%s,'
                        '%s::jsonb,%s,%s,%s) RETURNING role_event_id')

_CONTROL_COLUMNS = ('scope, lineage, control_kind, reason, actor, occurred_at')
_CONTROL_INSERT = ('INSERT INTO papersim.optimizer_control_events '
                   '(optimizer_control_key, ' + _CONTROL_COLUMNS + ') '
                   'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING optimizer_control_id')


def _advisory_lock_number(label: str) -> int:
    return int.from_bytes(sha256(label.encode('utf-8')).digest()[:4], 'big', signed=True)


# The two-integer key space is separate from the project's bigint migration lock.
_REGISTRY_LOCK = (_advisory_lock_number('papersim.registry.writer.v1'), 0)
_OPERATION_NAMESPACE = _advisory_lock_number('papersim.optimization.operation.v1')
_NS_PER_SECOND = 1_000_000_000
_NS_PER_MILLISECOND = 1_000_000
_OPTIMIZATION_CLOSEOUT_NS = 60 * _NS_PER_SECOND
_REGISTRY_WAIT_NS = 30 * _NS_PER_SECOND


class ManagedOptimizationError(PapersimStoreError):
    """A sanitized failure with the last proven command stage and receipt IDs."""

    def __init__(self, code, *, stage, indeterminate=False, retryable=False,
                 started_event_id=None, attempt_event_id=None):
        super().__init__(code)
        self.stage = stage
        self.indeterminate = indeterminate
        self.retryable = retryable or indeterminate
        self.started_event_id = started_event_id
        self.attempt_event_id = attempt_event_id


@dataclass(frozen=True)
class OptimizationCommandClock:
    start_ns: int
    compute_ns: int
    hard_ns: int

    def compute_expired(self) -> bool:
        return time.monotonic_ns() >= self.compute_ns

    def allocation(self) -> dict:
        return {'command_nanoseconds': self.hard_ns - self.start_ns,
                'compute_nanoseconds': self.compute_ns - self.start_ns,
                'closeout_nanoseconds': self.hard_ns - self.compute_ns}


def optimization_command_clock(start_ns: int) -> OptimizationCommandClock:
    """Allocate the command's single one-hour bound at CLI entry."""
    return _registered_optimization_clock(start_ns)


def _registered_optimization_clock(start_ns: int) -> OptimizationCommandClock:
    from polymarket_alpha_lab.papersim.optimize.runner import TIME_BUDGET_SECONDS

    hard_ns = start_ns + round(TIME_BUDGET_SECONDS * _NS_PER_SECOND)
    return OptimizationCommandClock(start_ns, hard_ns - _OPTIMIZATION_CLOSEOUT_NS, hard_ns)


def _validate_optimization_clock(clock):
    if (type(clock) is not OptimizationCommandClock
            or any(type(value) is not int for value in (clock.start_ns, clock.compute_ns, clock.hard_ns))
            or not 0 <= clock.start_ns <= time.monotonic_ns()
            or not clock.start_ns < clock.compute_ns < clock.hard_ns
            or clock != _registered_optimization_clock(clock.start_ns)):
        raise PapersimStoreError('papersim_optimize_clock_invalid')


def _remaining_ns(cutoff_ns: int, stage: str, *, minimum_ns: int = _NS_PER_MILLISECOND) -> int:
    remaining = cutoff_ns - time.monotonic_ns()
    if remaining < minimum_ns:
        raise ManagedOptimizationError('papersim_optimize_deadline', stage=stage, retryable=True)
    return remaining


def _wait_for_database(generator, cutoff_ns, stage, *, connection=None, before_send=None):
    """Drive psycopg's nonblocking I/O with the same absolute bound as SQL."""
    minimum_ns = 1 if connection is None else _NS_PER_MILLISECOND
    _remaining_ns(cutoff_ns, stage, minimum_ns=minimum_ns)
    if before_send is not None:
        before_send()
    try:
        wait = next(generator)
        with DefaultSelector() as selector:
            while True:
                fileno, interest = wait if connection is None else (connection.fileno(), wait)
                selector.register(fileno, interest)
                remaining = _remaining_ns(cutoff_ns, stage, minimum_ns=minimum_ns)
                ready = selector.select(remaining / _NS_PER_SECOND)
                selector.unregister(fileno)
                _remaining_ns(cutoff_ns, stage, minimum_ns=minimum_ns)
                if ready:
                    wait = generator.send(ready[0][1])
    except StopIteration as done:
        return done.value
    except (ManagedOptimizationError, OSError):
        if connection is not None:
            generator.close()
            connection.close()
        raise


@contextmanager
def optimization_database_phase(conn, clock, *, stage, closeout=False, _before_send=None):
    """Bound the socket of a validated connection for the enclosing phase."""
    cutoff_ns = clock.hard_ns if closeout else clock.compute_ns
    _remaining_ns(cutoff_ns, stage)
    original_wait = conn.wait

    def wait(generator, interval=None):
        return _wait_for_database(generator, cutoff_ns, stage, connection=conn,
                                  before_send=_before_send)

    conn.wait = wait
    try:
        yield
    finally:
        conn.wait = original_wait


def _try_advisory_lock(conn, *, key, transaction, cutoff_ns, stage):
    query = ('SELECT pg_try_advisory_xact_lock(%s, %s)' if transaction
             else 'SELECT pg_try_advisory_lock(%s, %s)')
    with conn.cursor() as cursor:
        while True:
            _remaining_ns(cutoff_ns, stage)
            cursor.execute(query, key)
            acquired = cursor.fetchone()[0]
            remaining = _remaining_ns(cutoff_ns, stage)
            if acquired:
                return
            time.sleep(min(0.02, remaining / _NS_PER_SECOND))


@contextmanager
def registry_write_transaction(conn):
    """Serialize registry state reads and append-only mutations in one transaction."""
    with conn.transaction():
        # A lock serializes writers, but cannot refresh a caller-owned
        # REPEATABLE READ or SERIALIZABLE snapshot established before it.
        with conn.cursor() as cursor:
            cursor.execute('SHOW transaction_isolation')
            if cursor.fetchone()[0] != 'read committed':
                raise PapersimStoreError('papersim_registry_write_requires_read_committed')
        _try_advisory_lock(conn, key=_REGISTRY_LOCK, transaction=True,
                           cutoff_ns=time.monotonic_ns() + _REGISTRY_WAIT_NS,
                           stage='registry_lock')
        yield


def _json_like(value):
    """Normalize one comparison operand: JSON text parses, structures stay.

    jsonb columns come back from psycopg as dict/list (auto-loaded)
    while the submission side carries canonical JSON text -- parsing
    the text makes the comparison value-level (key-order free) instead
    of string-level.
    """

    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _compare_stored_row(stored: dict, expected: dict, code: str) -> None:
    """Content comparison over mixed jsonb/datetime/text columns."""

    for column, value in expected.items():
        left = _json_like(stored[column])
        right = _json_like(value)
        if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
            if left != right:
                raise PapersimStoreError(code)
        elif _normalize(left) != _normalize(right):
            raise PapersimStoreError(code)


def _plain_json(value):
    """Recursively turn deep-frozen spec containers into plain JSON types.

    ``ModelSpec.__post_init__`` wraps every nested mapping in
    ``MappingProxyType``; ``json.dumps`` refuses proxies, so the writer
    unfolds them (and tuples) into plain dicts/lists first.
    """

    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _spec_submission_from_spec(spec, *, source, code_commit,
                               parent_spec_sha256=None, optimization_id=None):
    """Expand one ``optimize.spec.ModelSpec`` into a :class:`ModelSpecSubmission`.

    The scalar expansion is the ONLY place a ModelSpec meets the store
    layer, keeping ``papersim.store`` free of optimize imports.
    """

    from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec  # noqa: F401

    if not isinstance(spec, ModelSpec):
        raise PapersimStoreError('papersim_spec_object_invalid')
    return ModelSpecSubmission(
        spec_sha256=spec.spec_sha256(),
        canonical_text=spec.canonical_json(),
        family=spec.family,
        procedure_version=spec.procedure_version,
        model_version=spec.model_version(),
        legacy_model_version=spec.legacy_model_version,
        params=json.dumps(_plain_json(spec.params), sort_keys=True,
                          separators=(',', ':')),
        envelope=json.dumps(_plain_json(spec.envelope), sort_keys=True,
                            separators=(',', ':')),
        data_contract=spec.data_contract,
        source=source,
        parent_spec_sha256=parent_spec_sha256,
        optimization_id=optimization_id,
        code_commit=code_commit)


def register_model_spec(conn, spec, *, source, code_commit,
                        parent_spec_sha256=None, optimization_id=None) -> dict:
    """Register one model spec; the content hash IS the idempotency key.

    Same digest + same content returns the original row
    (``inserted=False``); same digest with any changed column is a
    ``papersim_model_spec_conflict``. The database additionally
    re-derives the digest from ``canonical_text`` (UTF-8), so a forged
    (digest, text) pair cannot land even through raw SQL.
    """

    submission = _spec_submission_from_spec(
        spec, source=source, code_commit=code_commit,
        parent_spec_sha256=parent_spec_sha256, optimization_id=optimization_id)
    submission.validate()
    with registry_write_transaction(conn):
        cursor = conn.cursor()
        cursor.execute('SELECT ' + _SPEC_COLUMNS + ' FROM papersim.model_specs '
                       'WHERE spec_sha256 = %s', (submission.spec_sha256,))
        columns = _SPEC_COLUMNS.replace(' ', '').split(',')
        row = cursor.fetchone()
        if row is None:
            cursor.execute(_SPEC_INSERT, (submission.spec_sha256,) + tuple(
                submission.comparison()[name] for name in columns))
            cursor.fetchone()
            return {'spec_sha256': submission.spec_sha256, 'inserted': True}
        stored = dict(zip(columns, row, strict=True))
        _compare_stored_row(stored, submission.comparison(),
                            'papersim_model_spec_conflict')
        return {'spec_sha256': submission.spec_sha256, 'inserted': False}


def record_optimization_run(conn, submission: OptimizationRunSubmission) -> dict:
    """Append one optimization run row (idempotent on the run key).

    ``snapshot_refs`` and the spec digests inside ``active_specs`` are
    proven to exist inside the same transaction -- the same pattern as
    the record_scan snapshot proof; no FK crosses runs and specs (a
    run references MANY specs and its own row may not exist yet when
    those specs were written).
    """

    submission.validate()
    key = submission.key()
    with registry_write_transaction(conn):
        cursor = conn.cursor()
        cursor.execute('SELECT optimization_id, ' + _RUN_COLUMNS
                       + ' FROM papersim.optimization_runs WHERE optimization_key = %s',
                       (key,))
        columns = ['optimization_id'] + _RUN_COLUMNS.replace(' ', '').split(',')
        row = cursor.fetchone()
        if row is not None:
            stored = dict(zip(columns, row, strict=True))
            _compare_stored_row(stored, submission.comparison(),
                                'papersim_optimization_run_conflict')
            return {'optimization_id': int(stored['optimization_id']),
                    'optimization_key': key, 'inserted': False}
        references = _json_like(submission.snapshot_refs)
        if references:
            cursor.execute('SELECT content_sha256 FROM papersim.snapshots '
                           'WHERE content_sha256 = ANY(%s)', (references,))
            present = {r[0] for r in cursor.fetchall()}
            if any(reference not in present for reference in references):
                raise PapersimStoreError('papersim_snapshot_ref_missing')
        active = _json_like(submission.active_specs)
        if active:
            cursor.execute('SELECT spec_sha256 FROM papersim.model_specs '
                           'WHERE spec_sha256 = ANY(%s)', (list(active.values()),))
            present = {r[0] for r in cursor.fetchall()}
            if any(digest not in present for digest in active.values()):
                raise PapersimStoreError('papersim_active_spec_missing')
        columns = _RUN_COLUMNS.replace(' ', '').split(',')
        values = submission.comparison()
        cursor.execute(_RUN_INSERT, (key,) + tuple(
            _require_json_payload(values[name], name)
            if name in ('lineages', 'dataset_summary', 'snapshot_refs',
                        'search_space', 'budget', 'active_specs', 'scorecard')
            else values[name] for name in columns))
        return {'optimization_id': int(cursor.fetchone()[0]),
                'optimization_key': key, 'inserted': True}


def record_promotion_decision(conn, submission: PromotionDecisionSubmission) -> dict:
    """Append one promotion decision row (idempotent on the decision key)."""

    submission.validate()
    key = submission.key()
    with registry_write_transaction(conn):
        cursor = conn.cursor()
        cursor.execute('SELECT decision_id, ' + _PROMOTION_COLUMNS
                       + ' FROM papersim.promotion_decisions WHERE decision_key = %s',
                       (key,))
        columns = ['decision_id'] + _PROMOTION_COLUMNS.replace(' ', '').split(',')
        row = cursor.fetchone()
        if row is not None:
            stored = dict(zip(columns, row, strict=True))
            _compare_stored_row(stored, submission.comparison(),
                                'papersim_promotion_decision_conflict')
            return {'decision_id': int(stored['decision_id']),
                    'decision_key': key, 'inserted': False}
        columns = _PROMOTION_COLUMNS.replace(' ', '').split(',')
        values = submission.comparison()
        cursor.execute(_PROMOTION_INSERT, (key,) + tuple(
            _require_json_payload(values[name], name)
            if name in ('evidence_watermark', 'metrics') else values[name]
            for name in columns))
        return {'decision_id': int(cursor.fetchone()[0]), 'decision_key': key,
                'inserted': True}


def _submission_to_registry_event(submission: RoleEventSubmission):
    """Convert one store RoleEventSubmission into a registry RoleEvent."""

    from polymarket_alpha_lab.papersim.optimize.registry import RoleEvent

    plan = submission.forward_plan
    if plan is not None:
        plan = json.loads(plan) if isinstance(plan, str) else dict(plan)
    return RoleEvent(lineage=submission.lineage,
                     spec_sha256=submission.spec_sha256, role=submission.role,
                     effective_at=submission.effective_at,
                     reason=submission.reason, forward_plan=plan,
                     decision_id=submission.decision_id, actor=submission.actor,
                     code_commit=submission.code_commit)


def _row_to_role_event(row):
    from polymarket_alpha_lab.papersim.optimize.registry import RoleEvent

    return RoleEvent(lineage=row[0], spec_sha256=row[1], role=row[2],
                     effective_at=row[3], reason=row[4], forward_plan=row[5],
                     decision_id=row[6], actor=row[7], code_commit=row[8],
                     role_event_id=row[9])


_ROLE_EVENT_SELECT = ('SELECT lineage, spec_sha256, role, effective_at, reason, '
                      'forward_plan, decision_id, actor, code_commit, role_event_id '
                      'FROM papersim.model_role_events')


def load_all_role_events(conn) -> dict:
    """All role events grouped per lineage into writer-transaction batches.

    Rows arrive ordered by ``(lineage, effective_at, role_event_id)``
    and consecutive events of one lineage sharing ``effective_at``
    reform one batch (the writer wrote them in one transaction). The
    pure registry folds the returned structure; an EMPTY registry
    yields ``{}`` which the caller maps to all-baseline fallback.
    """

    from polymarket_alpha_lab.papersim.optimize.registry import (
        group_events_by_lineage,
    )

    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(_ROLE_EVENT_SELECT + ' ORDER BY lineage, effective_at, '
                       'role_event_id')
        events = [_row_to_role_event(row) for row in cursor.fetchall()]
    return group_events_by_lineage([[event] for event in events])


def load_lineage_roles(conn, lineage: str):
    """The folded role view of ONE lineage, or ``None`` when it is empty.

    ``None`` is the registry-empty / lineage-unknown signal: the
    caller (the MO-7 provider) falls back to the family baseline and
    records the fallback -- a registry failure never stops a scan
    (04 section 11).
    """

    from polymarket_alpha_lab.papersim.optimize.registry import (
        derive_lineage_roles,
        group_events_by_lineage,
    )

    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(_ROLE_EVENT_SELECT + ' WHERE lineage = %s '
                       'ORDER BY effective_at, role_event_id', (lineage,))
        events = [_row_to_role_event(row) for row in cursor.fetchall()]
    batches = group_events_by_lineage([[event] for event in events]).get(lineage)
    return derive_lineage_roles(batches) if batches else None


def load_decision_role_events(conn, lineage: str, decision_id: int) -> tuple:
    """Return the original linked batch, with its full timestamp membership verified."""
    with conn.transaction(), conn.cursor() as cursor:
        cursor.execute(_ROLE_EVENT_SELECT + ' WHERE decision_id = %s ORDER BY role_event_id',
                       (decision_id,))
        events = tuple(_row_to_role_event(row) for row in cursor.fetchall())
        if not events:
            return ()
        instant = events[0].effective_at
        if any(event.lineage != lineage or event.effective_at != instant for event in events):
            raise PapersimStoreError('papersim_role_decision_batch_conflict')
        cursor.execute('SELECT role_event_id FROM papersim.model_role_events '
                       'WHERE lineage = %s AND effective_at = %s ORDER BY role_event_id',
                       (lineage, instant))
        if tuple(row[0] for row in cursor.fetchall()) != tuple(event.role_event_id for event in events):
            raise PapersimStoreError('papersim_role_decision_batch_conflict')
    return events


def _validate_role_decision(conn, batch, *, replay_ids):
    ids = {event.decision_id for event in batch.events}
    if ids == {None}:
        return
    if len(ids) != 1 or None in ids:
        raise PapersimStoreError('papersim_role_decision_batch_conflict')
    decision_id = next(iter(ids))
    first = batch.events[0]
    with conn.cursor() as cursor:
        cursor.execute('SELECT lineage, decided_at FROM papersim.promotion_decisions '
                       'WHERE decision_id = %s', (decision_id,))
        if cursor.fetchone() != (first.lineage, first.effective_at):
            raise PapersimStoreError('papersim_role_decision_batch_conflict')
    linked = load_decision_role_events(conn, first.lineage, decision_id)
    if tuple(event.role_event_id for event in linked) != tuple(replay_ids):
        raise PapersimStoreError('papersim_role_decision_batch_conflict')


def record_role_events(conn, batch: RoleEventBatch) -> dict:
    """Append a complete forward-only role batch or verify its exact replay."""
    from polymarket_alpha_lab.papersim.optimize.registry import validate_role_event_batch

    batch.validate()
    first = batch.events[0]
    lineage, instant = first.lineage, first.effective_at.astimezone(UTC)
    keys = [event.key() for event in batch.events]
    if len(set(keys)) != len(keys):
        raise PapersimStoreError('papersim_role_event_duplicate_key')
    with registry_write_transaction(conn):
        cursor = conn.cursor()
        columns = _ROLE_EVENT_COLUMNS.replace(' ', '').split(',')
        cursor.execute('SELECT role_event_key, role_event_id, ' + _ROLE_EVENT_COLUMNS
                       + ' FROM papersim.model_role_events WHERE role_event_key = ANY(%s) '
                       'OR (lineage = %s AND effective_at = %s) ORDER BY role_event_id',
                       (keys, lineage, instant))
        stored = [dict(zip(['role_event_key', 'role_event_id', *columns], row, strict=True))
                  for row in cursor.fetchall()]
        existing_keys = [row['role_event_key'] for row in stored]
        if set(keys).intersection(existing_keys):
            if existing_keys != keys:
                raise PapersimStoreError('papersim_role_event_partial_replay')
            ids = {}
            for event, row in zip(batch.events, stored, strict=True):
                _compare_stored_row(row, event.comparison(),
                                    'papersim_role_event_conflict')
                ids[event.role] = int(row['role_event_id'])
            _validate_role_decision(conn, batch, replay_ids=ids.values())
            return {'lineage': lineage, 'role_event_ids': ids, 'inserted': False}
        cursor.execute('SELECT max(effective_at) FROM papersim.model_role_events WHERE lineage = %s',
                       (lineage,))
        watermark = cursor.fetchone()[0]
        if watermark is not None and instant <= watermark:
            raise PapersimStoreError('papersim_role_event_out_of_order')
        prior = load_lineage_roles(conn, lineage)
        registry_events = [_submission_to_registry_event(event)
                           for event in batch.events]
        expected = validate_role_event_batch(prior, registry_events)
        _validate_role_decision(conn, batch, replay_ids=())
        ids = {}
        for event in batch.events:
            values = event.comparison()
            cursor.execute(_ROLE_EVENT_INSERT, (event.key(),) + tuple(
                _require_json_payload(values[name], name)
                if name == 'forward_plan' else values[name]
                for name in columns))
            ids[event.role] = int(cursor.fetchone()[0])
        if load_lineage_roles(conn, lineage) != expected:
            raise PapersimStoreError('papersim_role_event_fold_mismatch')
    return {'lineage': lineage, 'role_event_ids': ids, 'inserted': True}


def record_optimizer_control_event(
        conn, submission: OptimizerControlEventSubmission) -> dict:
    """Append one freeze/unfreeze row (idempotent on the control key)."""

    submission.validate()
    key = submission.key()
    with registry_write_transaction(conn):
        cursor = conn.cursor()
        cursor.execute('SELECT optimizer_control_id, ' + _CONTROL_COLUMNS
                       + ' FROM papersim.optimizer_control_events '
                       'WHERE optimizer_control_key = %s', (key,))
        columns = ['optimizer_control_id'] + _CONTROL_COLUMNS.replace(' ', '').split(',')
        row = cursor.fetchone()
        if row is not None:
            stored = dict(zip(columns, row, strict=True))
            _compare_stored_row(stored, submission.comparison(),
                                'papersim_optimizer_control_conflict')
            return {'optimizer_control_id': int(stored['optimizer_control_id']),
                    'optimizer_control_key': key, 'inserted': False}
        cursor.execute('SELECT occurred_at, optimizer_control_id '
                       'FROM papersim.optimizer_control_events WHERE scope = %s '
                       'AND lineage IS NOT DISTINCT FROM %s '
                       'ORDER BY occurred_at DESC, optimizer_control_id DESC LIMIT 1',
                       (submission.scope, submission.lineage))
        predecessor = cursor.fetchone()
        if predecessor is not None and submission.occurred_at < predecessor[0]:
            raise PapersimStoreError('papersim_optimizer_control_out_of_order')
        cursor.execute(_CONTROL_INSERT, (key,) + tuple(
            submission.comparison()[name]
            for name in _CONTROL_COLUMNS.replace(' ', '').split(',')))
        control_id = int(cursor.fetchone()[0])
        if predecessor is not None and (submission.occurred_at, control_id) <= predecessor:
            raise PapersimStoreError('papersim_optimizer_control_out_of_order')
        return {'optimizer_control_id': control_id,
                'optimizer_control_key': key, 'inserted': True}


def _spec_sequences_to_tuples(value):
    """Rebuild spec sequences as tuples (MO-7 read-side normalization).

    jsonb round-trips arrays as lists while every family's param
    vocabulary (and the built specs) uses tuples; the canonical text
    treats them identically, so the digest round-trip cannot catch the
    drift -- the type normalization belongs here, at the DB-to-spec
    boundary, so a rebuilt spec fits exactly like the registered one.
    """

    if isinstance(value, list):
        return tuple(_spec_sequences_to_tuples(item) for item in value)
    if isinstance(value, dict):
        return {key: _spec_sequences_to_tuples(item) for key, item in value.items()}
    return value


def load_model_spec(conn, spec_sha256: str):
    """Rebuild the ``ModelSpec`` of one row; identity must round-trip.

    Returns ``None`` when the digest is unknown (the caller falls back
    to the baseline). The rebuilt spec's ``spec_sha256()`` MUST equal
    the primary key -- the spelling-flip guard of plan review P3-4.

    The SELECT completes its own transaction on an idle connection and
    uses a savepoint inside a caller-owned transaction.
    """

    from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT ' + _SPEC_COLUMNS + ' FROM papersim.model_specs '
                       'WHERE spec_sha256 = %s', (spec_sha256,))
        row = cursor.fetchone()
    if row is None:
        return None
    columns = _SPEC_COLUMNS.replace(' ', '').split(',')
    stored = dict(zip(columns, row, strict=True))
    params = _spec_sequences_to_tuples(normalize_json_number(stored['params']))
    envelope = tuple(_spec_sequences_to_tuples(normalize_json_number(item))
                     for item in stored['envelope'])
    spec = ModelSpec(family=stored['family'],
                     procedure_version=stored['procedure_version'],
                     params=params, envelope=envelope,
                     data_contract=stored['data_contract'],
                     legacy_model_version=stored['legacy_model_version'])
    if (spec.spec_sha256() != spec_sha256 or spec.canonical_json() != stored['canonical_text']
            or spec.model_version() != stored['model_version']):
        raise PapersimStoreError('papersim_model_spec_roundtrip_mismatch')
    return spec


def load_optimization_runs(conn, family: str | None = None) -> list[dict]:
    """Read optimization runs (newest first), optionally one family."""

    cursor = conn.cursor()
    if family is None:
        cursor.execute('SELECT optimization_id, optimization_key, ' + _RUN_COLUMNS
                       + ' FROM papersim.optimization_runs '
                       'ORDER BY optimization_id DESC')
    else:
        cursor.execute('SELECT optimization_id, optimization_key, ' + _RUN_COLUMNS
                       + ' FROM papersim.optimization_runs WHERE family = %s '
                       'ORDER BY optimization_id DESC', (family,))
    columns = ['optimization_id', 'optimization_key'] \
        + _RUN_COLUMNS.replace(' ', '').split(',')
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def load_promotion_decisions(conn, lineage: str | None = None) -> list[dict]:
    """Read promotion decisions (newest first), optionally one lineage."""

    cursor = conn.cursor()
    if lineage is None:
        cursor.execute('SELECT decision_id, decision_key, ' + _PROMOTION_COLUMNS
                       + ' FROM papersim.promotion_decisions '
                       'ORDER BY decision_id DESC')
    else:
        cursor.execute('SELECT decision_id, decision_key, ' + _PROMOTION_COLUMNS
                       + ' FROM papersim.promotion_decisions WHERE lineage = %s '
                       'ORDER BY decision_id DESC', (lineage,))
    columns = ['decision_id', 'decision_key'] + _PROMOTION_COLUMNS.replace(' ', '').split(',')
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _registry_controls(conn) -> dict:
    controls = {'global': None, 'lineages': {}}
    with conn.cursor() as cursor:
        cursor.execute('SELECT DISTINCT ON (scope, lineage) scope, lineage, control_kind, '
                       'occurred_at, optimizer_control_id FROM papersim.optimizer_control_events '
                       'ORDER BY scope, lineage, occurred_at DESC, optimizer_control_id DESC')
        for scope, lineage, kind, occurred_at, control_id in cursor.fetchall():
            event = {'kind': kind, 'occurred_at': _optimize_time(occurred_at), 'id': control_id}
            if scope == 'GLOBAL':
                controls['global'] = event
            else:
                controls['lineages'][lineage] = event
    return controls


def _freeze_state(controls) -> dict:
    return {'global_frozen': (controls['global'] or {}).get('kind') == 'FREEZE',
            'frozen_lineages': sorted(lineage for lineage, event in controls['lineages'].items()
                                      if event['kind'] == 'FREEZE')}


def registry_freeze_state(conn) -> dict:
    """The effective freeze state: global plus per-lineage.

    A lineage is frozen when the GLOBAL state is frozen or its own
    latest LINEAGE event is a FREEZE. Read-only folding of the control
    log; the MO-8 decide node consults this before any handover.
    """

    return _freeze_state(_registry_controls(conn))


def initialize_registry(conn, *, families, actor: str, code_commit: str,
                        effective_at) -> dict:
    """Atomically verify or initialize all requested lineage baselines."""

    from polymarket_alpha_lab.papersim.optimize.registry import lineage_id

    if not isinstance(families, (list, tuple)) or not families:
        raise PapersimStoreError('papersim_registry_init_families_invalid')
    report = {}
    with registry_write_transaction(conn):
        for family in sorted(families, key=lambda item: item.family_id):
            baseline = family.baseline_spec()
            digest = baseline.spec_sha256()
            registered = load_model_spec(conn, digest)
            if registered is not None and (registered != baseline or registered.model_version() != baseline.model_version()):
                raise PapersimStoreError('papersim_registry_init_conflict')
            for target in sorted(family.targets()):
                lineage = lineage_id(family.family_id, target)
                roles = load_lineage_roles(conn, lineage)
                if roles is not None:
                    if roles.baseline != digest or registered != baseline:
                        raise PapersimStoreError('papersim_registry_init_conflict')
                    report[lineage] = {'state': 'skipped',
                                       'spec_sha256': digest}
                    continue
                if registered is None:
                    register_model_spec(conn, baseline, source='frozen_baseline',
                                        code_commit=code_commit)
                    registered = baseline
                batch = RoleEventBatch(events=(
                    RoleEventSubmission(lineage=lineage,
                                        spec_sha256=digest,
                                        role='BASELINE', effective_at=effective_at,
                                        reason='registry initialization',
                                        actor=actor, code_commit=code_commit),
                    RoleEventSubmission(lineage=lineage,
                                        spec_sha256=digest,
                                        role='ACTIVE', effective_at=effective_at,
                                        reason='registry initialization',
                                        actor=actor, code_commit=code_commit)))
                record_role_events(conn, batch)
                report[lineage] = {'state': 'initialized',
                                   'spec_sha256': digest}
    return report


# MO-6 managed experiments. The session lock serializes one logical request.
# The transaction lock protects only the claim and final publication views.
_OPTIMIZE_COMMAND = 'optimize-run'
_OPTIMIZE_CLAIM = 'papersim.optimize/claim/v1'
_OPTIMIZE_ATTEMPT = 'papersim.optimize/attempt/v1'
_OPTIMIZE_TERMINAL = 'papersim.optimize/terminal/v1'
_OPTIMIZE_ARTIFACT = 'papersim.optimize/managed-artifact/v2'
_OPTIMIZE_MANIFEST_KIND = 'optimizer_experiment_manifest'
_OPTIMIZE_ARTIFACT_KIND = 'optimizer_run_artifact'
_OPTIMIZE_BUDGET_CONFLICT = 'papersim_optimize_budget_conflict'
_NBA_ODR_BINDING_REQUIRED = 'papersim_optimize_nba_odr_registration_required'
_NBA_ODR_MANAGED_REQUIRED = 'papersim_optimize_nba_odr_managed_required'
_NBA_ODR_INTERRUPTED = 'papersim_optimize_nba_odr_interrupted'
_MISSING_OPTIMIZER_COMPUTE = object()


def _optimize_time(moment: datetime) -> str:
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise PapersimStoreError('papersim_optimize_timestamp_invalid')
    return moment.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _optimize_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _optimize_digest(value) -> str:
    return sha256(_optimize_json(value)).hexdigest()


def _optimize_operation_key(run_key):
    return 'optimize-run|' + sha256(run_key.encode('utf-8')).hexdigest()


def _optimize_snapshot(conn, digest, length, kind, schema_version):
    from polymarket_alpha_lab.papersim.snapshots import read_snapshot

    row = read_snapshot(conn, digest)
    if (row is None or row['kind'] != kind or row['schema_version'] != schema_version
            or row['byte_length'] != length or len(row['content']) != length
            or sha256(row['content']).hexdigest() != digest):
        raise PapersimStoreError('papersim_optimize_snapshot_identity_invalid')
    return row['content']


def _nba_odr_registration_snapshot_content(conn, ref, *, kind, source=None):
    # Type the lookup before fetching content, including for an untrusted pointer
    # or a reference taken from a corrupted registration event.
    query = ('SELECT content FROM papersim.snapshots WHERE content_sha256 = %s '
             'AND kind = %s AND schema_version = 1 AND byte_length = %s')
    params = (ref.sha256, kind, ref.byte_length)
    if source is not None:
        query += ' AND source = %s'
        params += (source,)
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    if row is None or len(row[0]) != ref.byte_length or sha256(row[0]).hexdigest() != ref.sha256:
        raise PapersimStoreError('papersim_optimize_snapshot_identity_invalid')
    return bytes(row[0])


def _optimize_manifest_sources(manifest, manifest_sha256):
    from polymarket_alpha_lab.papersim.snapshots import snapshot_digest_argument

    error = 'papersim_optimize_manifest_invalid'
    fields = {'role', 'key', 'kind', 'schema_version', 'sha256', 'byte_length'}
    allowed = fields | {'required', 'absent_reason', 'provenance'}
    entries = manifest['sources']
    if not isinstance(entries, list) or not entries:
        raise PapersimStoreError(error)
    sources, roles = {}, {}
    for source in entries:
        if not isinstance(source, dict) or not fields <= set(source) <= allowed:
            raise PapersimStoreError(error)
        role, key, kind = (source[name] for name in ('role', 'key', 'kind'))
        if any(not isinstance(value, str) or not value.strip() for value in (role, key, kind)):
            raise PapersimStoreError(error)
        path = PurePosixPath(key)
        if (path.is_absolute() or '..' in path.parts or str(path) != key or key == '.'
                or ':' in key or '\\' in key or (role, key) in roles):
            raise PapersimStoreError(error)
        provenance = source.get('provenance', {})
        if (not isinstance(provenance, dict) or set(provenance) - {'clock_basis', 'captured_at', 'provider_published_at'}
                or ('clock_basis' in provenance and provenance['clock_basis'] not in (
                    'snapshot_generated_at', 'provider_timestamp', 'retained_archive_mtime'))):
            raise PapersimStoreError(error)
        for name in ('captured_at', 'provider_published_at'):
            if name in provenance:
                _optimize_time(datetime.fromisoformat(provenance[name]))
        digest, length, schema = (source[name] for name in ('sha256', 'byte_length', 'schema_version'))
        required = source.get('required', True)
        if type(schema) is not int or schema < 1 or type(required) is not bool:
            raise PapersimStoreError(error)
        roles[role, key] = source
        if digest is None:
            reason = source.get('absent_reason')
            if required or length is not None or not isinstance(reason, str) or not reason.strip():
                raise PapersimStoreError(error)
            continue
        if (digest == manifest_sha256 or snapshot_digest_argument(digest) != digest
                or type(length) is not int or length < 1 or 'absent_reason' in source):
            raise PapersimStoreError(error)
        metadata = (length, kind, schema)
        if sources.setdefault(digest, metadata) != metadata:
            raise PapersimStoreError(error)
    return sources, roles


def _verify_optimizer_source_roles(family_id, roles, archives):
    """Verify the registered reader's source inventory before admitting a claim."""
    from re import fullmatch

    code = 'papersim_optimize_manifest_source_unregistered'
    known = set(roles)
    present = {item for item, source in roles.items() if source['sha256'] is not None}
    if family_id in {'intl-elo-goal-residuals', 'intl-elo-poisson'}:
        if known != {('intl_snapshot', 'snapshot')} or present != known:
            raise PapersimStoreError(code)
    elif family_id == 'nba-pace-efficiency':
        base = ('nba_snapshot', 'snapshot')
        if base not in present:
            raise PapersimStoreError(code)
        from polymarket_alpha_lab.papersim.espn_nba import NBA_TEAMS, season_year

        season = season_year(json.loads(archives[roles[base]['sha256']])['season'])
        schedules = {('team_schedule', f'team_schedule/schedule_{team}_{season}.json') for team in NBA_TEAMS}
        for role, key in present - {base} - schedules:
            if role != 'event_summary' or not fullmatch(r'event_summary/summary_[0-9]+\.json', key):
                raise PapersimStoreError(code)
        absent_reasons = {
            ('event_summary', 'original_nov10_event_summaries'):
                'matching_original_nov10_event_summary_bundle_unavailable',
            ('team_schedule', 'original_nov10_team_schedules'):
                'matching_original_nov10_team_schedule_bundle_unavailable',
        }
        for role, key in known - present:
            reason = absent_reasons.get((role, key))
            if reason is None or roles[role, key] != {
                'role': role, 'key': key, 'absent_reason': reason,
                'kind': 'registered_research_input', 'schema_version': 1,
                'required': False, 'sha256': None, 'byte_length': None,
            }:
                raise PapersimStoreError(code)
    elif family_id == 'club-soccer-recalib':
        from polymarket_alpha_lab.papersim.optimize import dataset

        required = {('club_archive', key) for key in (
            dataset.ARCHIVE_POINTER_PATH, dataset.ARCHIVE_BIG5_PATH, *dataset.ARCHIVE_LEAGUE_PATHS.values(),
        )}
        if not required <= present:
            raise PapersimStoreError(code)
        files = {key: archives[roles[role, key]['sha256']] for role, key in present if role == 'club_archive'}
        allowed = {('club_archive', key) for key in (*dataset.archive_read_paths(), *dataset.archive_fixture_paths(files))}
        if not known <= allowed:
            raise PapersimStoreError(code)


def _optimize_request(*, clock, family, data_cutoff, random_seed, code_commit, attempt,
                      targets, schedule_id, manifest_sha256, manifest_byte_length, manifest, source_refs):
    from polymarket_alpha_lab.papersim.optimize import runner

    family_id = getattr(family, 'family_id', None)
    try:
        registered = runner._historical_family(family_id) if type(family_id) is str else None
    except ValueError:
        registered = None
    if registered is None or family is not registered:
        raise PapersimStoreError('papersim_optimize_family_unregistered')
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'protocol', 'sources'}
            or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
            or not isinstance(manifest['protocol'], dict)):
        raise PapersimStoreError('papersim_optimize_request_conflict')
    if schedule_id == runner.NBA_ODR_SCHEDULE_ID:
        materials = manifest['protocol'].get('registration_materials')
        if family_id != runner._NBA_ODR_FAMILY_ID or not isinstance(materials, dict):
            raise PapersimStoreError('papersim_optimize_request_conflict')
        protocol = runner.nba_odr_historical_protocol(
            runner.SourceArtifactRef(materials.get('sha256'), materials.get('byte_length')),
        )
    else:
        protocol = runner.historical_protocol(registered)
    cutoff = _optimize_time(data_cutoff)
    if (type(random_seed) is not int or random_seed != protocol['seed']
            or cutoff != _optimize_time(datetime.fromisoformat(protocol['data_cutoff']))
            or schedule_id != protocol['schedule_id'] or isinstance(targets, (str, bytes))
            or list(targets) != protocol['targets']):
        raise PapersimStoreError('papersim_optimize_request_conflict')
    if _optimize_json(manifest['protocol']) != _optimize_json(protocol):
        raise PapersimStoreError('papersim_optimize_request_conflict')
    normalized = _optimize_json(manifest)
    if (type(attempt) is not int or attempt < 1 or type(manifest_byte_length) is not int
            or manifest_byte_length != len(normalized) or sha256(normalized).hexdigest() != manifest_sha256):
        raise PapersimStoreError('papersim_optimize_request_invalid')
    sources, _ = _optimize_manifest_sources(manifest, manifest_sha256)
    if (not sources or not isinstance(source_refs, Mapping)
            or any(type(length) is not int for length in source_refs.values())
            or {digest: metadata[0] for digest, metadata in sources.items()} != dict(source_refs)):
        raise PapersimStoreError('papersim_optimize_request_conflict')
    descriptor = runner.optimization_search_descriptor(
        registered, targets=protocol['targets'], schedule_id=protocol['schedule_id'], data_cutoff=data_cutoff,
    )
    lineages = [f'{family_id}/{target}' for target in protocol['targets']]
    submission = OptimizationRunSubmission(
        family=family_id, data_cutoff=data_cutoff, lineages=lineages,
        search_space=descriptor, rule_version=protocol['rule_version'], random_seed=random_seed,
        snapshot_refs=sorted(sources), code_commit=code_commit,
    ).validate()
    identity = {'run_key': submission.key(), 'family': family.family_id,
                'rule_version': protocol['rule_version'], 'data_cutoff': cutoff, 'random_seed': random_seed,
                'search_space': descriptor, 'lineages': lineages, 'code_commit': code_commit,
                'manifest': {'sha256': manifest_sha256, 'byte_length': manifest_byte_length},
                'sources': {digest: list(metadata) for digest, metadata in sorted(sources.items())},
                'budget': {**clock.allocation(), 'max_trials': runner.MAX_TRIALS}}
    return identity


def _verify_optimizer_inputs(conn, identity, manifest, *, nba_odr_context=None):
    from polymarket_alpha_lab.papersim.optimize import runner

    manifest_ref = identity['manifest']
    if identity['search_space']['schedule_id'] == runner.NBA_ODR_SCHEDULE_ID:
        if nba_odr_context is None:
            raise PapersimStoreError(_NBA_ODR_MANAGED_REQUIRED)
        facts = _read_nba_odr_facts(conn, nba_odr_context,
                                   expected_manifest=runner.SourceArtifactRef(**manifest_ref))
        if facts is None:
            raise PapersimStoreError(_NBA_ODR_BINDING_REQUIRED)
        if (facts.manifest_bytes != _optimize_json(manifest) or facts.plan.run_key != identity['run_key']
                or facts.policy_context.registry.keys() != set(identity['lineages'])):
            raise PapersimStoreError('papersim_optimize_request_conflict')
        return facts
    content = _optimize_snapshot(conn, manifest_ref['sha256'], manifest_ref['byte_length'],
                                 _OPTIMIZE_MANIFEST_KIND, 1)
    document = json.loads(content)
    if content != _optimize_json(manifest):
        raise PapersimStoreError('papersim_optimize_request_conflict')
    _, roles = _optimize_manifest_sources(document, manifest_ref['sha256'])
    archives = {digest: _optimize_snapshot(conn, digest, *metadata)
                for digest, metadata in identity['sources'].items()}
    _verify_optimizer_source_roles(identity['family'], roles, archives)
    return archives


def _role_watermarks(conn, lineage):
    with conn.cursor() as cursor:
        cursor.execute('SELECT DISTINCT ON (role) role, spec_sha256, effective_at, role_event_id '
                       'FROM papersim.model_role_events WHERE lineage = %s '
                       'ORDER BY role, effective_at DESC, role_event_id DESC', (lineage,))
        return {role: {'spec_sha256': digest, 'effective_at': _optimize_time(at), 'id': event_id}
                for role, digest, at, event_id in cursor.fetchall()}


def _registry_comparison_snapshot(conn, family, lineages):
    from polymarket_alpha_lab.papersim.optimize.registry import RegistryInvariantError

    baseline = family.baseline_spec()
    facts = {}
    for lineage in sorted(lineages):
        watermarks = _role_watermarks(conn, lineage)
        issue, roles, spec = None, None, None
        try:
            roles = load_lineage_roles(conn, lineage)
        except RegistryInvariantError:
            issue = 'registry_role_history_invalid'
        if roles is not None:
            if roles.baseline != baseline.spec_sha256():
                issue = 'registry_baseline_digest_mismatch'
            try:
                for digest in sorted({roles.active, roles.baseline, *roles.candidates,
                                      *roles.retired} | ({roles.guard} if roles.guard else set())):
                    registered = load_model_spec(conn, digest)
                    if registered is None:
                        issue = 'registry_referenced_spec_missing'
                    elif (registered.family, registered.procedure_version, registered.data_contract) != (
                            baseline.family, baseline.procedure_version, baseline.data_contract):
                        issue = 'registry_spec_contract_mismatch'
                    elif digest == roles.active:
                        spec = registered
            except (ValueError, TypeError, KeyError):
                issue = 'registry_referenced_spec_invalid'
        spec_payload = None if spec is None else {
            'family': spec.family, 'procedure_version': spec.procedure_version,
            'params': _plain_json(spec.params), 'envelope': _plain_json(spec.envelope),
            'data_contract': spec.data_contract, 'legacy_model_version': spec.legacy_model_version,
        }
        facts[lineage] = {'roles': None if roles is None else _plain_json(asdict(roles)),
                          'spec': spec_payload, 'baseline_spec_sha256': baseline.spec_sha256(),
                          'issue': issue, 'watermarks': watermarks,
                          'head': max(watermarks.values(), default=None,
                                      key=lambda row: (datetime.fromisoformat(row['effective_at']), row['id']))}
    return facts


def _optimization_registry_snapshot(conn, family, lineages, *, comparison=None):
    declared = [f'{family.family_id}/{target}' for target in family.targets()]
    controls = _registry_controls(conn)
    controls['lineages'] = {lineage: event for lineage, event in controls['lineages'].items()
                            if lineage in declared}
    with conn.cursor() as cursor:
        cursor.execute('SELECT DISTINCT ON (lineage) lineage, decision_id, decided_at '
                       'FROM papersim.promotion_decisions WHERE lineage = ANY(%s) '
                       'ORDER BY lineage, decided_at DESC, decision_id DESC', (declared,))
        decisions = {lineage: {'id': decision_id, 'decided_at': _optimize_time(decided_at)}
                     for lineage, decision_id, decided_at in cursor.fetchall()}
    lineages = _registry_comparison_snapshot(conn, family, lineages) if comparison is None else comparison
    return {'lineages': lineages, 'controls': controls,
            'decision_heads': decisions, 'prior_rejections': _optimization_rejections(conn, family.family_id)}


def _decode_optimizer_comparisons(rows):
    from polymarket_alpha_lab.papersim.optimize.registry import LineageRoles
    from polymarket_alpha_lab.papersim.optimize.runner import RegistryComparison
    from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

    registry = {}
    for lineage, row in rows.items():
        roles, spec = row['roles'], row['spec']
        if spec is not None:
            spec = ModelSpec(**_spec_sequences_to_tuples(spec))
        registry[lineage] = RegistryComparison(None if roles is None else LineageRoles(**roles), spec,
                                               row['issue'])
    return registry


def _optimization_policy(original):
    from polymarket_alpha_lab.papersim.optimize.runner import PolicyContext

    fact = _optimization_event_evidence(original, 'input_evidence', _OPTIMIZE_CLAIM)
    snapshot = fact['registry']
    if fact['registry_sha256'] != _optimize_digest(snapshot):
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    registry = _decode_optimizer_comparisons(snapshot['lineages'])
    return PolicyContext(original['occurred_at'], registry, _rejection_receipts(snapshot['prior_rejections']),
                         watermark=fact['registry_sha256'], **_freeze_state(snapshot['controls']))


def _rejection_receipts(rows):
    from polymarket_alpha_lab.papersim.optimize.runner import LegacyRejection, RejectionReceipt

    receipts = []
    for row in rows:
        if 'rejected_at' in row:
            receipts.append(RejectionReceipt(**{**row, 'rejected_at': datetime.fromisoformat(row['rejected_at'])}))
        else:
            receipts.append(LegacyRejection(**row))
    return tuple(receipts)


def _optimization_event_evidence(event, column, schema):
    evidence = event[column]
    if (not isinstance(evidence, list) or len(evidence) != 1 or not isinstance(evidence[0], dict)
            or evidence[0].get('schema') != schema):
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    return evidence[0]


def _optimization_events(conn, operation_key):
    columns = ('run_event_id', 'run_event_key', 'attempt', 'phase', 'occurred_at', 'scheduled_for',
               'code_commit', 'config_digest', 'input_evidence', 'output_evidence', 'error_code')
    with conn.cursor() as cursor:
        cursor.execute('SELECT ' + ', '.join(columns) + ' FROM papersim.command_run_events '
                       'WHERE operation_key = %s AND command = %s ORDER BY run_event_id',
                       (operation_key, _OPTIMIZE_COMMAND))
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _optimization_run_row(cursor, where, params):
    columns = ['optimization_id', 'optimization_key'] + _RUN_COLUMNS.replace(' ', '').split(',')
    cursor.execute('SELECT ' + ', '.join(columns) + ' FROM papersim.optimization_runs '
                   + where + ' ORDER BY optimization_id DESC LIMIT 1', params)
    row = cursor.fetchone()
    return None if row is None else dict(zip(columns, row, strict=True))


def _attempt_link(original, identity):
    return {'schema': _OPTIMIZE_ATTEMPT, 'original_started_event_id': original['run_event_id'],
            'original_started_event_key': original['run_event_key'], 'request_sha256': _optimize_digest(identity)}


def _optimization_operation(conn, run_key, identity=None):
    events = _optimization_events(conn, _optimize_operation_key(run_key))
    with conn.cursor() as cursor:
        run = _optimization_run_row(cursor, 'WHERE optimization_key = %s', (run_key,))
    if not events:
        if run is not None and identity is not None:
            raise PapersimStoreError('papersim_optimize_linkage_invalid')
        return None, events, run, None
    original = events[0]
    if original['phase'] != 'STARTED' or original['error_code'] is not None or original['output_evidence'] != []:
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    claim = _optimization_event_evidence(original, 'input_evidence', _OPTIMIZE_CLAIM)
    registered_request = claim['request']
    if registered_request['run_key'] != run_key:
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    if identity is not None and identity != registered_request:
        raise PapersimStoreError('papersim_optimize_request_conflict')
    expected_config = _optimize_digest(registered_request)
    expected_cutoff = registered_request['data_cutoff']
    started_attempts = {original['attempt']}
    finished_attempts = set()
    linked_terminal = None
    attempt_link = [_attempt_link(original, registered_request)]
    operation_key = _optimize_operation_key(run_key)
    from polymarket_alpha_lab.papersim.store import command_run_event_key

    for event in events:
        attempt, phase = event['attempt'], event['phase']
        if (event['config_digest'] != expected_config or event['code_commit'] != registered_request['code_commit']
                or _optimize_time(event['scheduled_for']) != expected_cutoff
                or event['occurred_at'] < original['occurred_at']
                or event['run_event_key'] != command_run_event_key(operation_key, attempt, phase)):
            raise PapersimStoreError('papersim_optimize_linkage_invalid')
        if event is original:
            continue
        if linked_terminal is not None or event['input_evidence'] != attempt_link or attempt in finished_attempts:
            raise PapersimStoreError('papersim_optimize_linkage_invalid')
        if phase == 'STARTED' and attempt not in started_attempts and event['output_evidence'] == []:
            started_attempts.add(attempt)
        elif phase in {'SUCCEEDED', 'FAILED'} and attempt in started_attempts:
            finished_attempts.add(attempt)
            output = event['output_evidence']
            if isinstance(output, list) and len(output) == 1 and isinstance(output[0], dict):
                if output[0].get('schema') == _OPTIMIZE_TERMINAL:
                    linked_terminal = event
                    continue
                if phase == 'FAILED' and event['error_code'] and output[0].get('schema') == _OPTIMIZE_ATTEMPT:
                    continue
            raise PapersimStoreError('papersim_optimize_linkage_invalid')
        else:
            raise PapersimStoreError('papersim_optimize_linkage_invalid')
    if (run is None) != (linked_terminal is None):
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    return original, events, run, linked_terminal


def _publication_role_ids(conn, run_key, dispositions, code_commit):
    with conn.cursor() as cursor:
        cursor.execute("SELECT lineage, role_event_id, spec_sha256, effective_at, forward_plan, "
                       "code_commit, reason, actor, decision_id "
                       "FROM papersim.model_role_events WHERE role = 'CANDIDATE' "
                       "AND forward_plan->>'source_run_key' = %s ORDER BY lineage", (run_key,))
        ids = {}
        for lineage, event_id, digest, instant, plan, commit, reason, actor, decision_id in cursor.fetchall():
            disposition = dispositions.get(lineage, {})
            if (lineage in ids or disposition.get('status') != 'registered'
                    or digest != disposition.get('spec_sha256') or commit != code_commit
                    or _optimize_time(instant) != disposition.get('effective_at')
                    or plan != disposition.get('forward_plan') or reason != disposition.get('reason')
                    or actor != f'auto:{plan.get("stage1_rule_version")}' or decision_id is not None):
                raise PapersimStoreError('papersim_optimize_linkage_invalid')
            ids[lineage] = event_id
    if ids.keys() != {lineage for lineage, row in dispositions.items() if row['status'] == 'registered'}:
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    return ids


def _optimization_terminal(conn, original, run, terminal, *, replayed, registration=None):
    claim = _optimization_event_evidence(original, 'input_evidence', _OPTIMIZE_CLAIM)
    identity = claim['request']
    linkage = _optimization_event_evidence(terminal, 'output_evidence', _OPTIMIZE_TERMINAL)
    failed = run['outcome'] == 'RUN_FAILED'
    snapshot_refs = sorted(set(identity['sources']) | {identity['manifest']['sha256']})
    if (linkage['run_key'] != run['optimization_key'] or linkage['optimization_id'] != run['optimization_id']
            or linkage['artifacts_sha256'] != run['artifacts_sha256'] or linkage['outcome'] != run['outcome']
            or terminal['phase'] != ('FAILED' if failed else 'SUCCEEDED')
            or bool(terminal['error_code']) != failed or linkage['exit_code'] != int(failed)
            or run['optimization_key'] != identity['run_key'] or run['code_commit'] != identity['code_commit']
            or run['rule_version'] != identity['rule_version'] or run['family'] != identity['family']
            or run['search_space'] != identity['search_space'] or run['random_seed'] != identity['random_seed']
            or run['lineages'] != identity['lineages'] or run['snapshot_refs'] != snapshot_refs
            or _optimize_time(run['data_cutoff']) != identity['data_cutoff']):
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    content = _optimize_snapshot(conn, linkage['artifacts_sha256'], linkage['artifact_byte_length'],
                                 _OPTIMIZE_ARTIFACT_KIND, 2)
    artifact = json.loads(content)
    pure = artifact['pure']
    dispositions = artifact['dispositions']
    rejections = artifact['rejections']
    scorecard = {name: pure[name] for name in ('candidate_scorecards', 'incumbent_scorecards', 'diagnostic_scorecards')}
    scorecard['stage1_rejections'] = [rejections[lineage] for lineage in sorted(rejections)]
    if (_optimize_json(artifact) != content or artifact['schema'] != _OPTIMIZE_ARTIFACT
            or artifact['request'] != identity or artifact['original_started'] != _original_start_proof(original, claim)
            or claim['registry_sha256'] != _optimize_digest(claim['registry'])
            or artifact['outcome'] != run['outcome'] or artifact['budget'] != run['budget']
            or any(run['budget'].get(name) != value for name, value in identity['budget'].items())
            or bool(run['budget'].get('violations')) != (terminal['error_code'] == _OPTIMIZE_BUDGET_CONFLICT)
            or artifact['pure_sha256'] != _optimize_digest(pure)
            or pure['active_specs'] != run['active_specs'] or pure['counts']['evaluated_trials'] != run['trial_count']
            or pure['dataset_summary'] != run['dataset_summary'] or scorecard != run['scorecard']
            or pure['family'] != run['family'] or pure['run_key'] != run['optimization_key']
            or pure['code_commit'] != run['code_commit'] or pure['rule_version'] != run['rule_version']
            or pure['data_cutoff'] != identity['data_cutoff'] or pure['search_space'] != run['search_space']
            or pure['random_seed'] != run['random_seed'] or pure['snapshot_refs'] != sorted(identity['sources'])
            or pure['policy_started_at'] != _optimize_time(original['occurred_at'])
            or pure['policy_watermark'] != claim['registry_sha256']
            or dispositions != linkage['dispositions'] or set(dispositions) != set(run['lineages'])
            or _publication_role_ids(conn, run['optimization_key'], dispositions, run['code_commit']) != linkage['role_event_ids']
            or bool(linkage['role_event_ids']) != (run['outcome'] == 'CANDIDATE_REGISTERED')
            or not set(rejections).issubset(run['lineages'])
            or (run['outcome'] in {'RUN_FAILED', 'INSUFFICIENT_DATA'} and rejections)
            or not all(_verified_rejection(pure, lineage, row) for lineage, row in rejections.items())):
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    if registration is not None:
        _validate_nba_odr_artifact(pure, identity, original, registration)
    return {'outcome': run['outcome'], 'run_key': run['optimization_key'], 'optimization_id': run['optimization_id'],
            'started_event_id': original['run_event_id'], 'terminal_event_id': terminal['run_event_id'],
            'started_event_key': original['run_event_key'], 'terminal_event_key': terminal['run_event_key'],
            'artifacts_sha256': run['artifacts_sha256'], 'artifact_byte_length': len(content),
            'artifact_bytes': content, 'registered_lineages': sorted(linkage['role_event_ids']),
            'dispositions': dispositions, 'replayed': replayed, 'scorecard': run['scorecard'],
            'exit_code': linkage['exit_code'], 'error_code': terminal['error_code'],
            'counts': pure['counts'], 'flags': pure['flags'], 'budget': artifact['budget'],
            'attempt_telemetry': linkage['attempt_telemetry']}


def _original_start_proof(original, claim):
    return {'event_key': original['run_event_key'], 'occurred_at': _optimize_time(original['occurred_at']),
            'registry_sha256': claim['registry_sha256'], 'registry': claim['registry']}


def _verified_rejection(pure, lineage, row):
    holdout = pure['holdout']
    coverage = holdout['comparisons'].get(lineage, {}).get('coverage', {})
    sides = {item['side']: item for item in holdout['callbacks']
             if item['lineage'] == lineage and item['status'] == 'completed'}
    decision = next((item for item in pure['decisions'] if item['lineage'] == lineage), {})
    return (row['lineage'] == lineage and row in pure['statistical_rejections']
            and decision.get('attempted') is True and decision.get('status') == 'statistically_rejected'
            and row['n_events'] == coverage.get('paired_games') and row['n_blocks'] == coverage.get('paired_blocks')
            and row['n_events'] >= holdout['floors']['min_events'] and row['n_blocks'] >= holdout['floors']['min_blocks']
            and set(sides) == {'candidate', 'incumbent'}
            and sides['candidate']['spec_sha256'] == row['spec_sha256']
            and sides['incumbent']['spec_sha256'] == pure['active_specs'].get(lineage)
            and lineage in pure['candidate_scorecards'] and lineage in pure['incumbent_scorecards'])


def _optimization_rejections(conn, family):
    """Accept only floor-qualified judgments joined to their committed terminal."""
    evidence = []
    for run in load_optimization_runs(conn, family):
        # Managed rows index their verified rejection lineage/digest pairs in scorecard.
        # Older rows can identify a suspected judgment, but lack its proof chain.
        card = run['scorecard']
        if not isinstance(card, dict):
            continue
        rows = card.get('stage1_rejections', [])
        legacy = card.get('lineages', {})
        if not isinstance(legacy, dict):
            legacy = {}
        rows = rows or [{'lineage': lineage, 'spec_sha256': row['winner_spec']}
                        for lineage, row in legacy.items() if isinstance(row, dict) and row.get('winner_spec') and not row.get('registered')
                        and row['winner_spec'] != row.get('incumbent_spec')]
        if not rows or run['outcome'] in {'RUN_FAILED', 'INSUFFICIENT_DATA'}:
            continue
        verified, terminal, artifact = None, None, None
        try:
            original, _, linked_run, terminal = _optimization_operation(conn, run['optimization_key'])
            if terminal is not None and terminal['phase'] == 'SUCCEEDED':
                verified = _optimization_terminal(conn, original, linked_run, terminal, replayed=True)
                artifact = json.loads(verified['artifact_bytes'])
        except (ValueError, TypeError, KeyError):
            pass
        for row in rows:
            if not isinstance(row, dict):
                continue
            lineage, digest = row.get('lineage'), row.get('spec_sha256')
            if (not isinstance(lineage, str) or lineage not in run['lineages'] or not lineage.startswith(family + '/')
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in '0123456789abcdef' for char in digest)):
                continue
            claim = {'run_key': run['optimization_key'], 'family': family, 'lineage': lineage, 'spec_sha256': digest}
            if artifact is not None and artifact['rejections'].get(lineage) == row:
                evidence.append({**claim, 'receipt_id': str(terminal['run_event_id']),
                                 'artifact_sha256': verified['artifacts_sha256'],
                                 'rejected_at': _optimize_time(terminal['occurred_at'])})
            else:
                evidence.append({**claim, 'reason': 'unverified_stage1_terminal'})
    return evidence


def latest_optimization_for_lineage(conn, lineage: str) -> dict | None:
    """Read the last run containing this lineage inside the caller's snapshot."""
    with conn.transaction(), conn.cursor() as cursor:
        run = _optimization_run_row(cursor, 'WHERE lineages @> %s::jsonb', (_json_text([lineage]),))
        if run is None:
            return None
        original, _, _, terminal = _optimization_operation(conn, run['optimization_key'])
        if terminal is None:
            from polymarket_alpha_lab.papersim.snapshots import verify_snapshot

            size, issue = None, 'papersim_optimize_legacy_terminal_unverified'
            digest = run['artifacts_sha256']
            if digest is not None:
                try:
                    size = verify_snapshot(conn, digest)['byte_length']
                except ValueError:
                    issue = 'papersim_optimize_snapshot_identity_invalid'
            return {**run, 'artifact_byte_length': size, 'terminal_event_id': None, 'artifact_issue': issue}
        result = _optimization_terminal(conn, original, run, terminal, replayed=True)
        return {**run, 'artifact_byte_length': result['artifact_byte_length'],
                'terminal_event_id': result['terminal_event_id'], 'artifact_issue': None}


def _validate_nba_odr_artifact(pure, identity, original, registration):
    from polymarket_alpha_lab.papersim.optimize import runner, search

    code = 'papersim_optimize_pure_result_invalid'
    plan = registration.plan
    physical, reported = plan.trials, pure['planned_evaluations']
    recorded, counts = pure['trials'], pure['counts']
    holdout = pure['holdout']
    holdout_folds = [fold.block_id for fold in plan.holdout_folds]
    expected_link = {**identity['nba_odr_registration'], 'started_event_id': original['run_event_id']}
    if (pure['schema'] != 'papersim.optimize.runner/artifact/v2' or pure['nba_odr_registration'] != expected_link
            or len(physical) != runner.NBA_ODR_MAX_TRIALS or len(reported) != len(physical)
            or len(recorded) > len(physical) or holdout['planned_folds'] != holdout_folds
            or holdout['group_ids'] != list(plan.cohort.holdout_group_ids)
            or holdout['n_events'] != len(plan.cohort.holdout_records) or holdout['n_blocks'] != len(holdout_folds)):
        raise PapersimStoreError(code)
    for row, trial in zip(reported, physical, strict=True):
        if not isinstance(row, dict) or type(row.get('index')) is not int or row != {
            'index': trial.trial_index, 'fold': trial.fold.block_id, 'spec_sha256': trial.spec_sha256,
            'targets': list(trial.targets), 'model_context': trial.model_context,
            'fold_sha256': trial.fold_sha256, 'season': trial.fold.history_partition,
        }:
            raise PapersimStoreError(code)
    status_counts = {search.TRIAL_COMPLETED: 0, search.TRIAL_NO_EVENTS: 0, search.TRIAL_ABORTED: 0}
    for index, record in enumerate(recorded):
        trial = physical[index]
        if (not isinstance(record, dict) or type(record.get('trial_index')) is not int
                or type(record.get('candidate_index')) is not int
                or record['trial_index'] != trial.trial_index or record['fold_block_id'] != trial.fold.block_id
                or record['candidate_index'] != trial.candidate_index or record['spec_sha256'] != trial.spec_sha256
                or record['targets'] != list(trial.targets) or record['model_context'] != trial.model_context
                or record['status'] not in status_counts):
            raise PapersimStoreError(code)
        status_counts[record['status']] += 1
    started, planned = len(recorded), len(physical)
    planned_uses = sum(len(trial.targets) for trial in physical)
    target_starts = sum(len(trial.targets) for trial in physical[:started])
    callbacks = holdout['callbacks']
    callback_keys = set()
    for call in callbacks:
        key = (call['lineage'], call['side'])
        if (key in callback_keys or call['lineage'] not in identity['lineages']
                or call['side'] not in {'candidate', 'incumbent'} or call['status'] not in {'completed', 'aborted'}
                or call['target'] != call['lineage'].split('/', 1)[1] or call['folds'] != holdout_folds):
            raise PapersimStoreError(code)
        callback_keys.add(key)
    expected = {'started_trials': started, 'evaluated_trials': started, 'planned_trials': planned,
                'completed_trials': status_counts[search.TRIAL_COMPLETED],
                'empty_trials': status_counts[search.TRIAL_NO_EVENTS],
                'aborted_trials': status_counts[search.TRIAL_ABORTED], 'unexecuted_trials': planned - started,
                'target_uses_planned': planned_uses, 'target_uses_started': target_starts,
                'shared_target_uses_planned': planned_uses - planned,
                'shared_target_uses_completed': target_starts - started - status_counts[search.TRIAL_ABORTED],
                'holdout_callbacks': len(callbacks),
                'completed_holdout_callbacks': sum(call['status'] == 'completed' for call in callbacks),
                'aborted_holdout_callbacks': sum(call['status'] == 'aborted' for call in callbacks)}
    if (any(type(counts.get(name)) is not int or counts[name] != value for name, value in expected.items())
            or (pure['outcome'] != 'RUN_FAILED' and (started != planned or expected['aborted_trials']))):
        raise PapersimStoreError(code)


def _validate_optimization_result(result, identity, policy, *, registration=None, original=None):
    from polymarket_alpha_lab.papersim.optimize.runner import OptimizationRunResult, _judgment_reasons, _trial_payload
    from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

    code = 'papersim_optimize_pure_result_invalid'
    if not isinstance(result, OptimizationRunResult):
        raise PapersimStoreError(code)
    submission = result.submission.validate()
    pure = json.loads(result.artifact_bytes)
    planned_trials = pure['planned_evaluations']
    expected_active = {lineage: row.roles.active for lineage, row in policy.registry.items()
                       if row.roles is not None and row.spec is not None and row.issue is None}
    if (_optimize_json(pure) != result.artifact_bytes or not isinstance(result.artifact_bytes, bytes)
            or sha256(result.artifact_bytes).hexdigest() != result.artifacts_sha256
            or submission.artifacts_sha256 != result.artifacts_sha256 or result.registered_lineages
            or result.run_key != identity['run_key'] or submission.key() != identity['run_key']
            or list(submission.lineages) != identity['lineages'] or submission.code_commit != identity['code_commit']
            or sorted(submission.snapshot_refs) != sorted(identity['sources'])
            or submission.trial_count != len(result.trials) or submission.outcome != result.outcome
            or submission.active_specs != dict(result.verified_active_specs) or submission.active_specs != expected_active
            or pure['outcome'] != result.outcome or pure['run_key'] != identity['run_key']
            or pure['data_cutoff'] != identity['data_cutoff'] or pure['code_commit'] != identity['code_commit']
            or pure['family'] != identity['family'] or pure['random_seed'] != identity['random_seed']
            or pure['search_space'] != identity['search_space'] or pure['snapshot_refs'] != sorted(identity['sources'])
            or pure['policy_started_at'] != _optimize_time(policy.started_at) or pure['policy_watermark'] != policy.watermark
            or pure['active_specs'] != expected_active or pure['counts']['evaluated_trials'] != len(result.trials)
            or pure['trials'] != _plain_json([_trial_payload(trial) for trial in result.trials])
            or pure['counts']['planned_trials'] != len(planned_trials) or len(result.trials) > len(planned_trials)
            or pure['decisions'] != _plain_json([asdict(decision) for decision in result.decisions])):
        raise PapersimStoreError(code)
    if registration is not None:
        _validate_nba_odr_artifact(pure, identity, original, registration)
    for index, trial in enumerate(result.trials):
        plan = planned_trials[index]
        if (index != trial.trial_index or index != plan['index'] or trial.fold_block_id != plan['fold']
                or trial.spec_sha256 != plan['spec_sha256'] or list(trial.targets) != plan['targets']
                or trial.model_context != plan['model_context']):
            raise PapersimStoreError(code)
    if ((result.outcome in {'RUN_FAILED', 'INSUFFICIENT_DATA'} and result.statistical_rejections)
            or (result.budget_exhausted and result.outcome != 'RUN_FAILED')
            or bool(result.proposed_specs) != (result.outcome == 'CANDIDATE_REGISTERED')):
        raise PapersimStoreError(code)
    decisions = {decision.lineage: decision for decision in result.decisions}
    if set(decisions) != set(identity['lineages']) or len(decisions) != len(result.decisions):
        raise PapersimStoreError(code)
    proposals = {}
    for lineage, spec in result.proposed_specs.items():
        active = result.comparison_specs.get(lineage)
        if (lineage not in expected_active or not isinstance(spec, ModelSpec) or not isinstance(active, ModelSpec)
                or spec != result.winner_specs.get(lineage) or active.spec_sha256() != expected_active[lineage]
                or (spec.family, spec.procedure_version, spec.data_contract) != (
                    identity['family'], active.procedure_version, active.data_contract)
                or not decisions[lineage].statistically_eligible or decisions[lineage].status != 'proposed'):
            raise PapersimStoreError(code)
        proposals[lineage] = spec.spec_sha256()
    if proposals != pure['proposed_specs']:
        raise PapersimStoreError(code)
    rejections = {}
    for rejection in result.statistical_rejections:
        lineage = rejection.lineage
        decision, winner = decisions.get(lineage), result.winner_specs.get(lineage)
        coverage = pure['holdout']['comparisons'].get(lineage, {}).get('coverage', {})
        floors = pure['holdout']['floors']
        if (lineage in rejections or not decision or not winner
                or decision.status != 'statistically_rejected' or not decision.attempted
                or winner.spec_sha256() != rejection.spec_sha256 or not rejection.reasons
                or _judgment_reasons(decision) != rejection.reasons
                or rejection.n_events != decision.n_events or rejection.n_blocks != decision.n_blocks
                or rejection.n_events != coverage.get('paired_games') or rejection.n_blocks != coverage.get('paired_blocks')
                or rejection.n_events < floors['min_events'] or rejection.n_blocks < floors['min_blocks']):
            raise PapersimStoreError(code)
        rejections[lineage] = _plain_json(asdict(rejection))
    if list(rejections.values()) != pure['statistical_rejections']:
        raise PapersimStoreError(code)
    return pure, rejections


def _optimizer_forward_plan(identity, lineage, instant, digest, active_digest):
    from polymarket_alpha_lab.papersim.optimize import policy

    row = policy.classify_threshold(identity['family'], lineage.split('/', 1)[1])
    return {
        'rule_version': policy.RULE_VERSION, 'stage1_rule_version': identity['rule_version'],
        'source_run_key': identity['run_key'], 'source_manifest': identity['manifest'],
        'active_spec_sha256': active_digest, 'candidate_spec_sha256': digest,
        'start': _optimize_time(instant), 'primary_metric': 'mean_log_loss',
        'first_look_min_events': row.min_markets,
        'final_look_min_events': policy.SECOND_LOOK_SAMPLE_MULTIPLIER * row.min_markets,
        'min_complete_blocks': row.min_complete_blocks, 'min_span_days': row.min_span_days,
        'max_shadow_days': row.max_shadow_days,
        'first_look_ci_percentiles': list(policy.FIRST_EVIDENCE_CI_PERCENTILES),
        'final_look_ci_percentiles': list(policy.SECOND_EVIDENCE_CI_PERCENTILES),
        'paired_mean_max': policy.PROMOTE_PAIRED_MEAN_MAX,
        'ci_upper_max': policy.PROMOTE_CI_UPPER_MAX,
        'close_deviation_min_gap': policy.CLOSE_DEVIATION_MIN_GAP,
        'close_deviation_min_markets': policy.CLOSE_DEVIATION_MIN_MARKETS,
    }


def _optimizer_publication_veto(lineage, digest, start, publish, bans):
    prior, current = start['lineages'][lineage], publish['lineages'][lineage]
    for context in (start, publish):
        freeze = _freeze_state(context['controls'])
        if freeze['global_frozen'] or lineage in freeze['frozen_lineages']:
            return 'registry_frozen'
    if current['issue']:
        return current['issue']
    if prior['roles'] is None or current['roles'] is None:
        return 'registry_lineage_uninitialized'
    if prior['watermarks'].get('ACTIVE') != current['watermarks'].get('ACTIVE'):
        return 'registry_active_changed'
    if digest in bans:
        return bans[digest]['reason']
    return None


def _optimizer_dispositions(result, rejections, identity, original, publication, instant):
    from polymarket_alpha_lab.papersim.optimize.registry import MAX_CANDIDATES
    from polymarket_alpha_lab.papersim.optimize.runner import banned_spec_digests

    claim = _optimization_event_evidence(original, 'input_evidence', _OPTIMIZE_CLAIM)
    bans = banned_spec_digests(_rejection_receipts(publication['prior_rejections']), now=instant,
                             family=identity['family'], run_key=identity['run_key'])
    dispositions, publishable = {}, {}
    for decision in result.decisions:
        lineage = decision.lineage
        spec = result.winner_specs.get(lineage)
        digest = spec.spec_sha256() if spec is not None else None
        row = {'status': decision.status, 'spec_sha256': digest}
        dispositions[lineage] = row
        if lineage not in result.proposed_specs and lineage not in rejections:
            continue
        veto = _optimizer_publication_veto(lineage, digest, claim['registry'], publication, bans)
        if lineage in result.proposed_specs and veto is None:
            current = publication['lineages'][lineage]
            roles = current['roles']
            if digest in {roles['baseline'], roles['active'], roles['guard'], *roles['candidates'], *roles['retired']}:
                veto = 'spec_already_has_registry_role'
            elif len(roles['candidates']) >= MAX_CANDIDATES:
                veto = 'registry_candidate_capacity'
            elif instant <= datetime.fromisoformat(current['head']['effective_at']):
                veto = 'registry_role_chronology_veto'
        if veto is not None:
            row.update(status=veto, statistical_status=decision.status)
            rejections.pop(lineage, None)
            continue
        if lineage in result.proposed_specs:
            plan = _optimizer_forward_plan(identity, lineage, instant, digest, roles['active'])
            reason = f'stage-one eligibility for {identity["run_key"]}'
            row.update(status='registered', effective_at=_optimize_time(instant), forward_plan=plan, reason=reason)
            publishable[lineage] = spec
    outcome = result.outcome
    if result.proposed_specs and not publishable:
        outcome = 'RUN_FAILED'
    if outcome in {'RUN_FAILED', 'INSUFFICIENT_DATA'}:
        for lineage in rejections:
            dispositions[lineage].update(status='judgment_not_published', statistical_status='statistically_rejected')
        rejections.clear()
    return outcome, dispositions, publishable


def _optimization_budget(identity, result, pure, *, compute_expired, registration=None):
    budget = identity['budget']
    reported = _json_like(result.submission.budget)
    limits = reported if isinstance(reported, dict) else {}
    maximum = budget['max_trials']
    duration = budget['command_nanoseconds'] / _NS_PER_SECOND
    trial_count = result.submission.trial_count
    planned = pure['counts']['planned_trials'] if registration is None else len(registration.plan.trials)
    cap_reached = trial_count == maximum and planned > maximum
    violations = []
    if type(limits.get('max_trials')) is not int or limits['max_trials'] != maximum:
        violations.append('reported_max_trials')
    if type(limits.get('time_budget_seconds')) not in (int, float) or limits['time_budget_seconds'] != duration:
        violations.append('reported_time_budget_seconds')
    if trial_count > maximum:
        violations.append('physical_trial_cap_exceeded')
    if result.outcome != 'RUN_FAILED' and (planned > maximum or compute_expired):
        violations.append('outcome_exceeds_registered_budget')
    if result.budget_exhausted and not (cap_reached or compute_expired):
        violations.append('unverified_exhaustion')
    if violations:
        reported_limits = {}
        for name in ('max_trials', 'time_budget_seconds'):
            value = limits.get(name)
            reported_limits[name] = value if type(value) is int or type(value) is float and isfinite(value) else None
        return {**budget, 'stopping_causes': ['registered_budget_conflict'], 'stopping_stage': 'validate_result',
                'reported_limits': reported_limits, 'violations': violations,
                'unexecuted_planned_trials': planned - trial_count}
    if result.budget_exhausted:
        causes = []
        if compute_expired:
            causes.append('compute_time')
        if cap_reached:
            causes.append('trial_cap')
        stopped = (pure['failure'] or {}).get('step') or ('search' if planned > trial_count else 'scientific_finalization')
    else:
        causes = ['scientific_failure'] if result.outcome == 'RUN_FAILED' else []
        stopped = (pure['failure'] or {}).get('step') or (causes[0] if causes else 'completed')
    return {**budget, 'stopping_causes': causes, 'stopping_stage': stopped,
            'unexecuted_planned_trials': planned - trial_count}


def _optimizer_attempt_telemetry(events, original, attempt, pure, *, resumed_attempt):
    attempts = {}
    for event in events:
        number = event['attempt']
        if event['phase'] == 'STARTED' and number != attempt:
            attempts[number] = {'attempt': number, 'progress': 'unknown_interrupted', 'trial_ids': []}
        elif event['phase'] == 'FAILED' and number in attempts:
            output = _optimization_event_evidence(event, 'output_evidence', _OPTIMIZE_ATTEMPT)
            if output.get('witnessed') is not None:
                attempts[number] = {'attempt': number, 'progress': 'witnessed', **output['witnessed']}
    witness = _optimizer_progress(pure)
    attempts[attempt] = {'attempt': attempt, 'progress': 'witnessed', **witness}
    admitted = [tuple(identity) for row in attempts.values() for identity in row['trial_ids']]
    return {'original_attempt': original['attempt'], 'terminal_attempt': attempt,
            'attempts': [attempts[number] for number in sorted(attempts)],
            'physical_starts_lower_bound': len(admitted), 'distinct_trial_ids_observed': len(set(admitted)),
            'known_duplicate_starts': len(admitted) - len(set(admitted)),
            'reused_started_attempt': resumed_attempt,
            'has_unknown_predecessor_progress': resumed_attempt or any(
                row['progress'] == 'unknown_interrupted' for row in attempts.values())}


def _optimizer_progress(pure):
    return {'trial_ids': [[row['trial_index'], row['spec_sha256'],
                           pure['planned_evaluations'][row['trial_index']]['fold_sha256'],
                           row['model_context']] for row in pure['trials']],
            'counts': {key: pure['counts'][key] for key in ('evaluated_trials', 'completed_trials',
                       'empty_trials', 'aborted_trials', 'holdout_callbacks', 'completed_holdout_callbacks',
                       'aborted_holdout_callbacks')}}


def _set_optimization_timeouts(conn, cutoff_ns, stage):
    ms = str(_remaining_ns(cutoff_ns, stage) // _NS_PER_MILLISECOND)
    conn.execute("SELECT set_config('statement_timeout', %s, true), "
                 "set_config('lock_timeout', %s, true), set_config('transaction_timeout', %s, true)", (ms, ms, ms))


@contextmanager
def optimization_read_transaction(conn, clock, *, stage, closeout):
    """Read a repeatable snapshot on the idle command connection within its deadline."""
    cutoff_ns = clock.hard_ns if closeout else clock.compute_ns
    with optimization_database_phase(conn, clock, stage=stage, closeout=closeout), conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        _set_optimization_timeouts(conn, cutoff_ns, stage)
        yield
        _remaining_ns(cutoff_ns, stage)


_managed_read_transaction = optimization_read_transaction


@contextmanager
def _managed_write_transaction(conn, clock, *, stage, closeout):
    """Own an outer READ COMMITTED transaction and its exact COMMIT boundary."""
    from psycopg import Error
    from psycopg.pq import TransactionStatus

    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise PapersimStoreError('papersim_optimize_connection_not_idle')
    cutoff_ns = clock.hard_ns if closeout else clock.compute_ns
    with optimization_database_phase(conn, clock, stage=stage, closeout=closeout):
        conn.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
        sent = False

        def sending_commit():
            nonlocal sent
            sent = True

        try:
            lock_stage = f'{stage}_lock'
            with optimization_database_phase(conn, clock, stage=lock_stage, closeout=closeout):
                _try_advisory_lock(conn, key=_REGISTRY_LOCK, transaction=True,
                                   cutoff_ns=cutoff_ns, stage=lock_stage)
            _set_optimization_timeouts(conn, cutoff_ns, stage)
            yield
            with optimization_database_phase(conn, clock, stage=f'{stage}_precommit', closeout=closeout,
                                             _before_send=sending_commit):
                conn.execute('COMMIT')
                _remaining_ns(cutoff_ns, f'{stage}_acknowledgment')
        except BaseException as error:
            uncertain = sent and (conn.closed or isinstance(error, (ManagedOptimizationError, OSError))
                                  or isinstance(error, Error) and error.sqlstate is None)
            if uncertain:
                conn.close()
                raise ManagedOptimizationError('papersim_optimize_commit_unconfirmed',
                                               stage=f'{stage}_acknowledgment', indeterminate=True) from None
            if not conn.closed:
                try:
                    conn.execute('ROLLBACK')
                except (Error, ManagedOptimizationError, OSError):
                    conn.close()
            raise


@dataclass(frozen=True)
class _NBAODRManagedContext:
    root: Path
    code_commit: str
    instance: tuple[str, str, str]
    migration_version: str

    def registration_identity(self):
        fields = ('instance_id', 'root_sha256', 'system_identifier')
        return {'code_commit': self.code_commit, 'migration_version': self.migration_version,
                'instance_sha256': _optimize_digest(dict(zip(fields, self.instance, strict=True)))}


def _require_nba_odr_connection(conn, root):
    from psycopg.pq import TransactionStatus

    if root is None or getattr(conn, '_papersim_managed_root', None) != root:
        raise PapersimStoreError(_NBA_ODR_MANAGED_REQUIRED)
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise PapersimStoreError('papersim_optimize_connection_not_idle')
    conn.autocommit = True


def _nba_odr_managed_context(conn, clock, code_commit, *, repo_root=None):
    from polymarket_alpha_lab.papersim.migrations import PapersimMigrationError, papersim_migration_catalog
    from polymarket_alpha_lab.papersim.scan import ScanError, require_clean_worktree
    from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError

    root = getattr(conn, '_papersim_managed_root', None) if repo_root is None else Path(repo_root).resolve()
    _validate_optimization_clock(clock)
    _remaining_ns(clock.hard_ns, 'registration_identity')
    _require_nba_odr_connection(conn, root)
    try:
        if require_clean_worktree(root) != code_commit:
            raise PapersimStoreError('papersim_nba_odr_registration_identity_invalid')
    except ScanError as error:
        raise PapersimStoreError(error.code) from None
    try:
        info = read_instance(root)
        instance = (info['instance_id'], info['root_sha256'], info['system_identifier'])
        # Bind the schema version required by this checkout. The operator
        # separately verifies the applied hashes in the owner-only ledger.
        catalog = papersim_migration_catalog(root)
    except (ProjectDatabaseError, PapersimMigrationError) as error:
        raise PapersimStoreError(str(error)) from None
    _remaining_ns(clock.hard_ns, 'registration_identity')
    return _NBAODRManagedContext(root, code_commit, instance, catalog[-1][0].split('_', 1)[0])


def _guard_nba_odr_registration_references(conn):
    from polymarket_alpha_lab.papersim import nba_odr_registration as registration
    from polymarket_alpha_lab.papersim.optimize.runner import SourceArtifactRef

    # The pin set and reconstructed plan determine the other snapshot digests.
    # Only the materials and comparison digests are followed from stored links.
    with conn.cursor() as cursor:
        cursor.execute("SELECT command, phase, output_evidence->0->'materials'->>'sha256', "
                       "output_evidence->0->'materials'->>'byte_length' "
                       'FROM papersim.command_run_events WHERE run_event_key = %s',
                       (registration.NBA_ODR_REGISTRATION_EVENT_KEY,))
        row = cursor.fetchone()
    if row is None:
        return
    command, phase, digest, length = row
    if (command, phase) != ('optimize-register-experiment', 'SUCCEEDED'):
        raise PapersimStoreError('papersim_nba_odr_registration_conflict')
    try:
        materials = _nba_odr_registration_snapshot_content(
            conn, SourceArtifactRef(digest, int(length)), kind=registration._MATERIALS_KIND,
            source=registration._generated_source(registration._MATERIALS_NAME),
        )
        comparison = json.loads(materials)['comparison']
        _nba_odr_registration_snapshot_content(
            conn, SourceArtifactRef(comparison['sha256'], comparison['byte_length']),
            kind=registration._COMPARE_KIND,
            source=registration._generated_source(registration._COMPARISON_NAME),
        )
    except (ValueError, TypeError, KeyError) as error:
        if isinstance(error, PapersimStoreError):
            raise
        raise PapersimStoreError('papersim_nba_odr_registration_evidence_invalid') from None


def _read_nba_odr_facts(conn, context, *, expected_manifest=None):
    from polymarket_alpha_lab.papersim import nba_odr_registration as registration
    from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError

    try:
        with binding.bound(*context.instance), conn.cursor() as cursor:
            binding.verify_connection(cursor)
    except ProjectDatabaseError as error:
        raise PapersimStoreError(str(error)) from None
    _guard_nba_odr_registration_references(conn)
    return registration._read_nba_odr_registration(
        conn, **context.registration_identity(), decode_comparisons=_decode_optimizer_comparisons,
        expected_manifest=expected_manifest,
    )


def _bind_nba_odr_request(conn, identity, manifest, context):
    from polymarket_alpha_lab.papersim.optimize import runner

    facts = _verify_optimizer_inputs(conn, identity, manifest, nba_odr_context=context)
    plan = facts.plan
    registration = {
        'registration_event_id': facts.run_event_id, 'registration_event_key': facts.run_event_key,
        'registration_config_digest': facts.config_digest, 'manifest': asdict(facts.manifest_ref),
        'materials': asdict(facts.materials_ref), 'physical_plan': asdict(plan.plan_ref),
        'metadata': asdict(plan.metadata_ref), 'training_only_group_ids': list(plan.training_only_group_ids),
    }
    return {**identity, 'nba_odr_registration': registration,
            'budget': {**identity['budget'], 'max_trials': runner.NBA_ODR_MAX_TRIALS}}, facts


def _read_nba_odr_registration_archives(repo_root):
    from polymarket_alpha_lab.papersim.optimize.nba_odr import NBA_ODR_ARCHIVE_PINS

    archives = {}
    for season, pin in NBA_ODR_ARCHIVE_PINS.items():
        path = repo_root / pin.relpath
        no_links(path)
        with path.open('rb') as stream:
            archives[season] = stream.read(pin.gzip_byte_length + 1)
    return archives


def _register_nba_odr(conn, clock, context, originals, *, stage, confirm=False):
    from polymarket_alpha_lab.papersim import nba_odr_registration as registration
    from polymarket_alpha_lab.papersim.optimize import runner

    key = (_advisory_lock_number(registration.NBA_ODR_REGISTRATION_LOCK_NAMESPACE),
           _advisory_lock_number(runner.NBA_ODR_SCHEDULE_ID))
    family = runner._historical_family(runner._NBA_ODR_FAMILY_ID)
    with _managed_write_transaction(conn, clock, stage=stage, closeout=True):
        _try_advisory_lock(conn, key=key, transaction=True, cutoff_ns=clock.hard_ns, stage=f'{stage}_schedule_lock')
        if confirm:
            facts = _read_nba_odr_facts(conn, context)
            if facts is not None:
                return facts
        with binding.bound(*context.instance), conn.cursor() as cursor:
            binding.verify_connection(cursor)
        _guard_nba_odr_registration_references(conn)
        return registration._write_nba_odr_registration(
            conn, **context.registration_identity(), **originals,
            read_comparisons=lambda: _registry_comparison_snapshot(conn, family, registration.NBA_ODR_REGISTRATION_LINEAGES),
            decode_comparisons=_decode_optimizer_comparisons, record_command_run_event=record_command_run_event,
            wall_clock=lambda: d1_database_time(conn),
        )


def register_managed_nba_odr_experiment(conn, *, clock, repo_root, code_commit, approval_bytes,
                                       source_inventory_bytes, reconnect=None):
    """Verify and commit the fixed NBA v2 registration before exposing its pointer."""
    from psycopg import Error
    from polymarket_alpha_lab.papersim import nba_odr_registration as registration
    from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError

    stage = 'registration_identity'
    try:
        context = _nba_odr_managed_context(conn, clock, code_commit, repo_root=repo_root)
        stage = 'registration_originals'
        archives = _read_nba_odr_registration_archives(context.root)
        originals = {'approval_bytes': approval_bytes, 'source_inventory_bytes': source_inventory_bytes,
                     'content_by_season': archives}
        # The same originals must still be proved if another caller wins after
        # a rolled-back COMMIT, so verify these before either transaction.
        submitted = {'approval': approval_bytes, 'source_inventory': source_inventory_bytes, **archives}
        pins = registration._pinned_originals()
        if submitted.keys() != pins.keys():
            raise PapersimStoreError('papersim_nba_odr_registration_conflict')
        for name, pin in pins.items():
            pin.registration(submitted[name])
        stage = 'registration'
        try:
            return _register_nba_odr(conn, clock, context, originals, stage=stage)
        except ManagedOptimizationError as error:
            if not error.indeterminate or reconnect is None:
                raise
            stage = 'confirm_registration'
            _remaining_ns(clock.hard_ns, stage)
            try:
                with reconnect() as fresh:
                    _require_nba_odr_connection(fresh, context.root)
                    return _register_nba_odr(fresh, clock, context, originals, stage=stage, confirm=True)
            except (Error, OSError, ManagedOptimizationError):
                raise error from None
    except ProjectDatabaseError as error:
        raise ManagedOptimizationError(str(error), stage=stage) from None
    except (Error, OSError, TypeError, KeyError, ValueError) as error:
        raise _optimizer_error(error, stage=stage) from None


def read_managed_nba_odr_registration(conn, *, clock, repo_root, code_commit, expected_manifest=None):
    """Independently reconstruct a committed registration using one managed read view."""
    from psycopg import Error

    stage = 'registration_readback'
    try:
        context = _nba_odr_managed_context(conn, clock, code_commit, repo_root=repo_root)
        with _managed_read_transaction(conn, clock, stage=stage, closeout=True):
            return _read_nba_odr_facts(conn, context, expected_manifest=expected_manifest)
    except (Error, OSError, TypeError, KeyError, ValueError) as error:
        raise _optimizer_error(error, stage=stage) from None


@contextmanager
def _optimizer_operation_lock(conn, clock, run_key, *, confirmation=False):
    key = (_OPERATION_NAMESPACE, _advisory_lock_number(run_key))
    locked = False
    from psycopg import Error

    try:
        if not confirmation:
            with optimization_database_phase(conn, clock, stage='operation_lock', closeout=True):
                locked = conn.execute('SELECT pg_try_advisory_lock(%s, %s)', key).fetchone()[0]
        if not locked:
            cutoff_ns = clock.hard_ns if confirmation else clock.compute_ns
            with optimization_database_phase(conn, clock, stage='operation_lock', closeout=confirmation):
                _try_advisory_lock(conn, key=key, transaction=False, cutoff_ns=cutoff_ns, stage='operation_lock')
                locked = True
        yield
    finally:
        if not conn.closed:
            if locked:
                try:
                    with optimization_database_phase(conn, clock, stage='operation_unlock', closeout=True):
                        conn.execute('SELECT pg_advisory_unlock(%s, %s)', key)
                except (Error, ManagedOptimizationError, OSError):
                    conn.close()
            else:
                # Even a try-lock whose reply was interrupted may hold a session lock.
                conn.close()


def _optimizer_write_event(conn, identity, *, phase, attempt, at, inputs, outputs=(), error_code=None):
    operation_key = _optimize_operation_key(identity['run_key'])
    event = CommandRunEventSubmission(
        operation_key=operation_key, attempt=attempt, phase=phase, command=_OPTIMIZE_COMMAND,
        scheduled_for=datetime.fromisoformat(identity['data_cutoff']), occurred_at=at,
        code_commit=identity['code_commit'], config_digest=_optimize_digest(identity),
        input_evidence=list(inputs), output_evidence=list(outputs), error_code=error_code,
    )
    written = record_command_run_event(conn, event)
    readback = next((row for row in _optimization_events(conn, operation_key)
                     if row['run_event_key'] == written['run_event_key']), None)
    if (not written['inserted'] or readback is None or readback['run_event_id'] != written['run_event_id']
            or readback['occurred_at'] != at or readback['input_evidence'] != list(inputs)
            or readback['output_evidence'] != list(outputs)):
        raise PapersimStoreError('papersim_optimize_linkage_invalid')
    return readback


_NBA_ODR_CLAIM_ISSUER = object()
_NBA_ODR_SESSION_PREFIX = 'papersim-nba-odr:'


class _NBAODRComputeClaim:
    """A process-local handoff of the one durable claim to the private runner."""

    __slots__ = ('_available',)

    def __init__(self, inputs, *, issuer):
        if issuer is not _NBA_ODR_CLAIM_ISSUER:
            raise PapersimStoreError(_NBA_ODR_BINDING_REQUIRED)
        self._available = [inputs]

    def _take_for_runner(self):
        try:
            return self._available.pop()
        except IndexError:
            raise PapersimStoreError(_NBA_ODR_BINDING_REQUIRED) from None


@dataclass
class _NBAODRInvocation:
    token: str
    prepared_start: dict | None = None
    entered_compute: bool = False
    publication: _OptimizationPublication | None = None
    result: tuple | None = None


@contextmanager
def _nba_odr_claim_activity(conn, clock, token):
    # Tie preview's live state to the lock holder for this invocation, even
    # when the caller has reconnected to confirm its own original claim.
    from psycopg import Error

    with optimization_database_phase(conn, clock, stage='claim_activity', closeout=True):
        original, = conn.execute("SELECT current_setting('application_name')").fetchone()
        conn.execute("SELECT set_config('application_name', %s, false)", (_NBA_ODR_SESSION_PREFIX + token,))
    try:
        yield
    finally:
        if not conn.closed:
            try:
                with optimization_database_phase(conn, clock, stage='claim_activity_restore', closeout=True):
                    conn.execute("SELECT set_config('application_name', %s, false)", (original,))
            except (Error, ManagedOptimizationError, OSError):
                conn.close()


def _verify_nba_odr_comparators(family, lineages, registered, snapshot, instant):
    from polymarket_alpha_lab.papersim.optimize import runner

    code = 'papersim_optimize_nba_odr_comparison_conflict'
    current = _decode_optimizer_comparisons(snapshot['lineages'])
    for lineage in lineages:
        observed, expected = current[lineage], registered.policy_context.registry[lineage]
        if (observed.issue is not None or observed.roles is None or observed.spec is None
                or (observed.roles.baseline, observed.roles.active, observed.spec.model_version()) !=
                (expected.roles.baseline, expected.roles.active, expected.spec.model_version())
                or observed.spec != expected.spec):
            raise PapersimStoreError(code)
    contexts = dict.fromkeys(runner._NBA_ODR_TARGETS, 'shared')
    metadata = json.loads(registered.plan.metadata_bytes)
    try:
        baseline, catalogs, _ = runner._nba_odr_catalogs(family, runner.PolicyContext(instant, current), contexts)
    except ValueError:
        raise PapersimStoreError(code) from None
    specs = [{'spec_sha256': spec.spec_sha256(), 'model_version': spec.model_version(),
              'canonical_spec': spec.canonical_json()} for spec in catalogs[runner._NBA_ODR_TARGETS[0]].specs]
    if (instant < registered.occurred_at or contexts != metadata['model_contexts']
            or metadata['baseline'] != {'spec_sha256': baseline.spec_sha256(), 'model_version': baseline.model_version()}
            or specs != metadata['ordered_specs']):
        raise PapersimStoreError(code)


def _optimizer_claim(conn, clock, identity, family, attempt, original, events, *, registration=None, invocation=None):
    if registration is not None and (original is not None or events or invocation is None):
        raise PapersimStoreError(_NBA_ODR_INTERRUPTED)
    if any(event['phase'] == 'FAILED' and event['attempt'] == attempt for event in events):
        raise ManagedOptimizationError('papersim_optimize_attempt_already_failed', stage='claim',
                                       retryable=True, started_event_id=original['run_event_id'])
    if events and attempt < max(event['attempt'] for event in events):
        raise ManagedOptimizationError('papersim_optimize_attempt_out_of_order', stage='claim',
                                       retryable=True, started_event_id=original['run_event_id'])
    if any(event['phase'] == 'STARTED' and event['attempt'] == attempt for event in events):
        _remaining_ns(clock.compute_ns, 'claim')
        return original, events
    with _managed_write_transaction(conn, clock, stage='claim', closeout=False):
        instant = datetime.now(UTC)
        if original is None:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM papersim.command_run_events WHERE command = %s "
                               "AND phase = 'STARTED' AND scheduled_for = %s "
                               "AND input_evidence->0->>'schema' = %s "
                               "AND input_evidence->0->'request'->>'family' = %s "
                               "AND input_evidence->0->'request'->'search_space'->>'schedule_id' = %s "
                               "AND input_evidence->0->'request'->>'rule_version' = %s LIMIT 1",
                               (_OPTIMIZE_COMMAND, identity['data_cutoff'], _OPTIMIZE_CLAIM, identity['family'],
                                identity['search_space']['schedule_id'], identity['rule_version']))
                if cursor.fetchone() is not None:
                    raise PapersimStoreError('papersim_optimize_existing_holdout_assignment')
            if instant < datetime.fromisoformat(identity['data_cutoff']):
                raise PapersimStoreError('papersim_optimize_cutoff_in_future')
            if registration is None:
                facts = _optimization_registry_snapshot(conn, family, identity['lineages'])
            else:
                comparison = _registry_comparison_snapshot(conn, family, identity['lineages'])
                _verify_nba_odr_comparators(family, identity['lineages'], registration, {'lineages': comparison}, instant)
                facts = _optimization_registry_snapshot(conn, family, identity['lineages'], comparison=comparison)
            claim = {'schema': _OPTIMIZE_CLAIM, 'request': identity,
                     'registry': facts, 'registry_sha256': _optimize_digest(facts)}
            if registration is not None:
                claim['claim_token'] = invocation.token
            original = _optimizer_write_event(conn, identity, phase='STARTED', attempt=attempt, at=instant,
                                              inputs=[claim])
            if registration is not None:
                invocation.prepared_start = original
            added = original
        else:
            added = _optimizer_write_event(conn, identity, phase='STARTED', attempt=attempt, at=instant,
                                           inputs=[_attempt_link(original, identity)])
    return original, [*events, added]


def _optimizer_record_failed_attempt(conn, clock, identity, original, attempt, error, pure):
    from psycopg import Error

    if original is None or conn.closed or error.indeterminate or error.stage == 'claim':
        return
    if error.stage == 'operation_lock':
        return
    try:
        with _managed_write_transaction(conn, clock, stage='attempt_failure', closeout=True):
            current, events, _, terminal = _optimization_operation(conn, identity['run_key'], identity)
            if (terminal is not None or current != original
                    or not any(event['phase'] == 'STARTED' and event['attempt'] == attempt for event in events)
                    or any(event['phase'] == 'FAILED' and event['attempt'] == attempt for event in events)):
                return
            row = _optimizer_write_event(conn, identity, phase='FAILED', attempt=attempt, at=datetime.now(UTC),
                                         inputs=[_attempt_link(original, identity)], error_code=error.code,
                                         outputs=[{'schema': _OPTIMIZE_ATTEMPT, 'stage': error.stage,
                                                   'witnessed': None if pure is None else _optimizer_progress(pure)}])
        error.attempt_event_id = row['run_event_id']
    except (Error, ValueError, OSError) as receipt_error:
        if isinstance(receipt_error, ManagedOptimizationError) and receipt_error.indeterminate:
            raise receipt_error from None


@dataclass(frozen=True)
class _OptimizationPublication:
    instant: datetime
    registry: dict
    artifact: bytes
    budget: dict
    outcome: str
    dispositions: dict
    publishable: dict
    rejections: dict


def _prepare_optimizer_publication(conn, identity, family, original, result, pure, rejections, budget):
    publication = _optimization_registry_snapshot(conn, family, identity['lineages'])
    instant = datetime.now(UTC)
    if instant < original['occurred_at']:
        raise PapersimStoreError('papersim_optimize_publication_time_invalid')
    rejections = rejections.copy()
    if budget.get('violations'):
        outcome, publishable = 'RUN_FAILED', {}
        rejections.clear()
        dispositions = {decision.lineage: {'status': 'registered_budget_conflict',
                        'statistical_status': decision.status} for decision in result.decisions}
    else:
        outcome, dispositions, publishable = _optimizer_dispositions(
            result, rejections, identity, original, publication, instant)
        if outcome != result.outcome:
            budget = {**budget, 'stopping_causes': ['publication_veto'], 'stopping_stage': 'publication_veto'}
    claim = _optimization_event_evidence(original, 'input_evidence', _OPTIMIZE_CLAIM)
    artifact = _optimize_json({
        'schema': _OPTIMIZE_ARTIFACT, 'request': identity, 'budget': budget, 'outcome': outcome,
        'original_started': _original_start_proof(original, claim),
        'publication': {'observed_at': _optimize_time(instant), 'registry': publication},
        'pure_sha256': result.artifacts_sha256, 'pure': pure, 'dispositions': dispositions, 'rejections': rejections,
    })
    return _OptimizationPublication(instant, publication, artifact, budget, outcome, dispositions, publishable,
                                     rejections)


def _optimizer_publish(conn, clock, identity, family, original, events, attempt, result, pure,
                       rejections, budget, *, resumed_attempt, registration=None, invocation=None):
    from polymarket_alpha_lab.papersim.snapshots import SnapshotRegistration, register_snapshot

    budget_conflict = bool(budget.get('violations'))
    stage = 'failure_publication' if result.outcome == 'RUN_FAILED' or budget_conflict else 'publication'
    with _managed_write_transaction(conn, clock, stage=stage, closeout=True):
        prepared = None if invocation is None else invocation.publication
        if prepared is None:
            prepared = _prepare_optimizer_publication(conn, identity, family, original, result, pure,
                                                      rejections, budget)
            if invocation is not None:
                invocation.publication = prepared
        elif prepared.publishable or prepared.rejections:
            # The first publication released the registry lock when its
            # connection was lost. Recheck any proposed registry effects.
            if _optimization_registry_snapshot(conn, family, identity['lineages']) != prepared.registry:
                raise PapersimStoreError('papersim_optimize_publication_veto')
        instant, artifact, budget, outcome = prepared.instant, prepared.artifact, prepared.budget, prepared.outcome
        dispositions, publishable, rejections = prepared.dispositions, prepared.publishable, prepared.rejections
        _remaining_ns(clock.hard_ns, f'{stage}_serialization')
        stored = register_snapshot(conn, SnapshotRegistration(
            content=artifact, kind=_OPTIMIZE_ARTIFACT_KIND, source=_OPTIMIZE_COMMAND,
            fetched_at=instant, schema_version=2,
        ))
        digest = sha256(artifact).hexdigest()
        if (stored['content_sha256'] != digest or stored['byte_length'] != len(artifact)
                or stored['kind'] != _OPTIMIZE_ARTIFACT_KIND or stored['schema_version'] != 2):
            raise PapersimStoreError('papersim_optimize_linkage_invalid')
        scorecard = {name: pure[name] for name in ('candidate_scorecards', 'incumbent_scorecards', 'diagnostic_scorecards')}
        scorecard['stage1_rejections'] = [rejections[lineage] for lineage in sorted(rejections)]
        submission = replace(result.submission, budget=budget, artifacts_sha256=digest, outcome=outcome, scorecard=scorecard,
                             snapshot_refs=sorted(set(identity['sources']) | {identity['manifest']['sha256']}))
        written = record_optimization_run(conn, submission)
        if not written['inserted']:
            raise PapersimStoreError('papersim_optimize_linkage_invalid')
        role_event_ids = {}
        for lineage, spec in sorted(publishable.items()):
            row = dispositions[lineage]
            registered = load_model_spec(conn, spec.spec_sha256())
            if registered is None:
                register_model_spec(conn, spec, source='optimizer', code_commit=identity['code_commit'],
                                    parent_spec_sha256=result.verified_active_specs[lineage],
                                    optimization_id=written['optimization_id'])
            elif registered != spec:
                raise PapersimStoreError('papersim_optimize_spec_identity_invalid')
            batch = RoleEventBatch(events=(RoleEventSubmission(
                lineage=lineage, spec_sha256=spec.spec_sha256(), role='CANDIDATE', effective_at=instant,
                reason=row['reason'], forward_plan=row['forward_plan'], actor=f'auto:{identity["rule_version"]}',
                code_commit=identity['code_commit'],
            ),))
            role = record_role_events(conn, batch)
            if not role['inserted']:
                raise PapersimStoreError('papersim_optimize_linkage_invalid')
            role_event_ids[lineage] = role['role_event_ids']['CANDIDATE']
        linkage = {
            'schema': _OPTIMIZE_TERMINAL, 'run_key': identity['run_key'], 'optimization_id': written['optimization_id'],
            'artifacts_sha256': digest, 'artifact_byte_length': len(artifact), 'outcome': outcome,
            'dispositions': dispositions, 'role_event_ids': role_event_ids, 'exit_code': int(outcome == 'RUN_FAILED'),
            'attempt_telemetry': _optimizer_attempt_telemetry(events, original, attempt, pure,
                                                              resumed_attempt=resumed_attempt),
        }
        error_code = None
        if outcome == 'RUN_FAILED':
            if budget_conflict:
                error_code = _OPTIMIZE_BUDGET_CONFLICT
            elif result.budget_exhausted:
                error_code = 'papersim_optimize_budget_exhausted'
            elif result.outcome != outcome:
                error_code = 'papersim_optimize_publication_veto'
            else:
                error_code = 'papersim_optimize_scientific_failure'
        terminal = _optimizer_write_event(conn, identity, phase='FAILED' if error_code else 'SUCCEEDED',
                                          attempt=attempt, at=datetime.now(UTC), inputs=[_attempt_link(original, identity)],
                                          outputs=[linkage], error_code=error_code)
        with conn.cursor() as cursor:
            run = _optimization_run_row(cursor, 'WHERE optimization_key = %s', (identity['run_key'],))
        if registration is None:
            confirmed = _optimization_terminal(conn, original, run, terminal, replayed=False)
        else:
            confirmed = _optimization_terminal(conn, original, run, terminal, replayed=False,
                                               registration=registration)
    return confirmed


def _optimizer_error(error, *, stage, original=None):
    if isinstance(error, ManagedOptimizationError):
        failure = error
    elif isinstance(error, PapersimStoreError):
        failure = ManagedOptimizationError(error.code, stage=stage)
    elif isinstance(error, (TypeError, KeyError, ValueError)):
        code = 'papersim_optimize_input_invalid' if stage in {'request', 'compute'} else 'papersim_optimize_linkage_invalid'
        failure = ManagedOptimizationError(code, stage=stage)
    else:
        failure = ManagedOptimizationError('papersim_optimize_transport_failure', stage=stage, retryable=True)
    if original is not None:
        failure.started_event_id = original['run_event_id']
    return failure


def _execute_optimizer_operation(conn, clock, identity, family, manifest, attempt, compute, state):
    from psycopg import Error

    run_key = identity['run_key']
    stage = 'operation_lock'
    try:
        with _optimizer_operation_lock(conn, clock, run_key):
            stage = 'read_operation'
            with _managed_read_transaction(conn, clock, stage=stage, closeout=True):
                original, events, run, terminal = _optimization_operation(conn, run_key, identity)
                state['original'] = original
                if terminal is not None:
                    _verify_optimizer_inputs(conn, identity, manifest)
                    return _optimization_terminal(conn, original, run, terminal, replayed=True)
            stage = 'verify_inputs'
            with _managed_read_transaction(conn, clock, stage=stage, closeout=False):
                archives = _verify_optimizer_inputs(conn, identity, manifest)
            stage = 'claim'
            resumed_attempt = any(event['phase'] == 'STARTED' and event['attempt'] == attempt for event in events)
            original, events = _optimizer_claim(conn, clock, identity, family, attempt, original, events)
            state['original'] = original
            pure = None
            stage = 'compute'
            try:
                observed_expiry = False

                def deadline():
                    nonlocal observed_expiry
                    observed_expiry = observed_expiry or clock.compute_expired()
                    return observed_expiry

                _remaining_ns(clock.compute_ns, stage)
                policy = _optimization_policy(original)
                result = compute(policy_context=policy, deadline=deadline, source_artifacts=archives)
                stage = 'validate_result'
                _remaining_ns(clock.hard_ns, stage)
                pure, rejections = _validate_optimization_result(result, identity, policy)
                budget = _optimization_budget(identity, result, pure, compute_expired=observed_expiry)
                stage = 'failure_publication' if result.outcome == 'RUN_FAILED' or budget.get('violations') else 'publication'
                state['publication'] = (original, events, attempt, result, pure, rejections, budget, resumed_attempt)
                return _optimizer_publish(conn, clock, identity, family, original, events, attempt, result, pure,
                                          rejections, budget, resumed_attempt=resumed_attempt)
            except (Error, OSError, TypeError, KeyError, ValueError) as error:
                failure = _optimizer_error(error, stage=stage, original=original)
                _optimizer_record_failed_attempt(conn, clock, identity, original, attempt, failure, pure)
                raise failure from None
    except (Error, OSError, TypeError, KeyError, ValueError) as error:
        raise _optimizer_error(error, stage=stage, original=state.get('original')) from None


def _confirm_optimizer_commit(clock, identity, family, manifest, attempt, compute, reconnect, state, error):
    from psycopg import Error

    run_key = identity['run_key']
    original, confirmed_absence = None, False
    try:
        _remaining_ns(clock.hard_ns, 'confirm_commit')
        with reconnect() as fresh:
            fresh.autocommit = True
            with _optimizer_operation_lock(fresh, clock, run_key, confirmation=True):
                with _managed_read_transaction(fresh, clock, stage='confirm_commit', closeout=True):
                    original, events, run, terminal = _optimization_operation(fresh, run_key, identity)
                    _verify_optimizer_inputs(fresh, identity, manifest)
                    if terminal is not None:
                        return _optimization_terminal(fresh, original, run, terminal, replayed=True)
                    confirmed_absence = True
                    failed_attempt = next((row for row in events if row['phase'] == 'FAILED'
                                           and row['attempt'] == attempt), None)
                    if failed_attempt is not None:
                        raise ManagedOptimizationError('papersim_optimize_attempt_already_failed', stage='confirm_commit',
                                                       retryable=True, started_event_id=original['run_event_id'],
                                                       attempt_event_id=failed_attempt['run_event_id'])
                proved = state.get('publication')
                if proved is not None and proved[0] == original:
                    start, events, number, result, pure, rejections, budget, resumed = proved
                    return _optimizer_publish(fresh, clock, identity, family, start, events, number, result, pure,
                                              rejections, budget, resumed_attempt=resumed)
            if not clock.compute_expired() and not error.stage.startswith('attempt_failure'):
                return _execute_optimizer_operation(fresh, clock, identity, family, manifest, attempt, compute, state)
    except (Error, OSError, TypeError, KeyError, ValueError) as confirmation_error:
        if not confirmed_absence and not isinstance(confirmation_error, (ValueError, KeyError, TypeError)):
            raise error from None
        if (not confirmed_absence and isinstance(confirmation_error, ManagedOptimizationError)
                and confirmation_error.retryable):
            raise error from None
        raise _optimizer_error(confirmation_error, stage='confirm_commit', original=original) from None
    raise ManagedOptimizationError('papersim_optimize_commit_absent', stage='confirm_commit', retryable=True,
                                   started_event_id=None if original is None else original['run_event_id']) from None


def _nba_odr_publish(conn, clock, identity, family, original, events, registration, invocation):
    result, pure, rejections, budget = invocation.result
    return _optimizer_publish(conn, clock, identity, family, original, events, original['attempt'],
                              result, pure, rejections, budget, resumed_attempt=False,
                              registration=registration, invocation=invocation)


def _compute_nba_odr_claim(conn, clock, identity, family, original, events, registration, invocation):
    from psycopg import Error
    from polymarket_alpha_lab.papersim.optimize import runner

    stage, pure = 'compute', None
    try:
        _remaining_ns(clock.hard_ns, stage)
        policy = _optimization_policy(original)
        observed_expiry = False

        def deadline():
            nonlocal observed_expiry
            observed_expiry = observed_expiry or clock.compute_expired()
            return observed_expiry

        plan = registration.plan
        inputs = runner._NBAODRClaimInputs(
            registration, identity['run_key'], identity['code_commit'], registration.run_event_id,
            original['run_event_id'], registration.manifest_ref, registration.materials_ref,
            plan.plan_ref, plan.metadata_ref, policy, deadline,
        )
        claim = _NBAODRComputeClaim(inputs, issuer=_NBA_ODR_CLAIM_ISSUER)
        invocation.entered_compute = True
        result = runner._execute_claimed_nba_odr(claim)
        expired = deadline()
        stage = 'validate_result'
        _remaining_ns(clock.hard_ns, stage)
        pure, rejections = _validate_optimization_result(result, identity, policy,
                                                          registration=registration, original=original)
        budget = _optimization_budget(identity, result, pure, compute_expired=expired, registration=registration)
        invocation.result = (result, pure, rejections, budget)
        stage = 'failure_publication' if result.outcome == 'RUN_FAILED' or budget.get('violations') else 'publication'
        return _nba_odr_publish(conn, clock, identity, family, original, events, registration, invocation)
    except (Error, OSError, TypeError, KeyError, ValueError) as error:
        failure = _optimizer_error(error, stage=stage, original=original)
        try:
            _optimizer_record_failed_attempt(conn, clock, identity, original, original['attempt'], failure, pure)
        except ManagedOptimizationError:
            # A failed-attempt receipt with an uncertain COMMIT must not
            # replace the first failure or lead to another compute call.
            pass
        if not failure.indeterminate:
            failure.retryable = False
        raise failure from None


def _execute_nba_odr_operation(conn, clock, request, family, manifest, attempt, context, invocation,
                               *, confirmation=False):
    from psycopg import Error

    run_key = request['run_key']
    original, stage = None, 'operation_lock'
    try:
        with _optimizer_operation_lock(conn, clock, run_key, confirmation=confirmation):
            stage = 'confirm_commit' if confirmation else 'verify_inputs'
            with _managed_read_transaction(conn, clock, stage=stage, closeout=True):
                identity, registration = _bind_nba_odr_request(conn, request, manifest, context)
                original, events, run, terminal = _optimization_operation(conn, run_key, identity)
                if terminal is not None:
                    return _optimization_terminal(conn, original, run, terminal, replayed=True,
                                                  registration=registration)
            own_start = confirmation and original is not None and original == invocation.prepared_start
            if original is not None and (not own_start or len(events) != 1):
                failed_attempt = next((row for row in events if row['phase'] == 'FAILED'), None)
                raise ManagedOptimizationError(
                    _NBA_ODR_INTERRUPTED, stage='claim', started_event_id=original['run_event_id'],
                    attempt_event_id=None if failed_attempt is None else failed_attempt['run_event_id'],
                )
            if invocation.entered_compute and (not own_start or invocation.publication is None):
                raise PapersimStoreError(_NBA_ODR_INTERRUPTED)
            with _nba_odr_claim_activity(conn, clock, invocation.token):
                if invocation.entered_compute:
                    stage = 'confirm_publication'
                    return _nba_odr_publish(conn, clock, identity, family, original, events, registration, invocation)
                if original is None:
                    stage = 'claim'
                    original, events = _optimizer_claim(conn, clock, identity, family, attempt, original, events,
                                                        registration=registration, invocation=invocation)
                stage = 'compute'
                return _compute_nba_odr_claim(conn, clock, identity, family, original, events, registration, invocation)
    except (Error, OSError, TypeError, KeyError, ValueError) as error:
        raise _optimizer_error(error, stage=stage, original=original) from None


def _run_managed_nba_odr(conn, clock, identity, family, manifest, attempt, reconnect):
    from psycopg import Error
    from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError

    try:
        context = _nba_odr_managed_context(conn, clock, identity['code_commit'])
    except (Error, OSError, TypeError, KeyError, ValueError) as error:
        raise _optimizer_error(error, stage='registration_identity') from None
    invocation = _NBAODRInvocation(uuid4().hex)
    try:
        return _execute_nba_odr_operation(conn, clock, identity, family, manifest, attempt, context, invocation)
    except ManagedOptimizationError as error:
        if not error.indeterminate or reconnect is None:
            raise
        fresh = None
        try:
            _remaining_ns(clock.hard_ns, 'confirm_commit')
            fresh = reconnect()
            _require_nba_odr_connection(fresh, context.root)
        except (Error, OSError, PapersimStoreError, ProjectDatabaseError):
            if fresh is not None:
                fresh.close()
            raise error from None
        with fresh:
            return _execute_nba_odr_operation(fresh, clock, identity, family, manifest, attempt,
                                              context, invocation, confirmation=True)


def _managed_optimizer_request(conn, *, clock, family, data_cutoff, random_seed, code_commit, attempt, targets,
                               schedule_id, manifest_sha256, manifest_byte_length, manifest, source_refs,
                               operation_key):
    from psycopg.pq import TransactionStatus
    from polymarket_alpha_lab.papersim.optimize import runner

    try:
        if schedule_id == runner.NBA_ODR_SCHEDULE_ID:
            _require_nba_odr_connection(conn, getattr(conn, '_papersim_managed_root', None))
        elif not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
            raise ManagedOptimizationError('papersim_optimize_connection_not_idle', stage='connect')
        _validate_optimization_clock(clock)
        _remaining_ns(clock.hard_ns, 'request')
        identity = _optimize_request(clock=clock, family=family, data_cutoff=data_cutoff, random_seed=random_seed,
                                     code_commit=code_commit, attempt=attempt, targets=targets, schedule_id=schedule_id,
                                     manifest_sha256=manifest_sha256, manifest_byte_length=manifest_byte_length,
                                     manifest=manifest, source_refs=source_refs)
        if operation_key is not None and operation_key != _optimize_operation_key(identity['run_key']):
            raise PapersimStoreError('papersim_optimize_request_conflict')
        return identity
    except (TypeError, KeyError, ValueError) as error:
        raise _optimizer_error(error, stage='request') from None


def _nba_odr_started_is_live(cursor, run_key, token):
    cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks AS locks "
                   'JOIN pg_catalog.pg_stat_activity AS activity USING (pid) '
                   "WHERE locks.locktype = 'advisory' AND locks.granted AND locks.objsubid = 2 "
                   "AND locks.mode = 'ExclusiveLock' AND activity.datname = current_database() "
                   'AND locks.classid = %s::oid AND locks.objid = %s::oid AND activity.application_name = %s)',
                   (_OPERATION_NAMESPACE & 0xffffffff, _advisory_lock_number(run_key) & 0xffffffff,
                    _NBA_ODR_SESSION_PREFIX + token))
    return cursor.fetchone()[0]


def _preview_nba_odr_state(conn, identity, registration):
    code = 'papersim_optimize_linkage_invalid'
    run_key = identity['run_key']
    operation_key = _optimize_operation_key(run_key)
    config_digest = _optimize_digest(identity)
    plan = registration.plan
    receipt = {
        'status': 'not_run', 'code_commit': identity['code_commit'], 'outcome': None, 'exit_code': 0,
        'registration_event_id': registration.run_event_id, 'registration_event_key': registration.run_event_key,
        'registration_config_digest': registration.config_digest,
        'planned_trials': len(plan.trials), 'target_uses_planned': sum(len(row.targets) for row in plan.trials),
        'planned_holdout_days': len(plan.holdout_folds), 'planned_holdout_matches': len(plan.cohort.holdout_records),
        'started_event_id': None, 'started_event_key': None, 'attempt_event_id': None, 'attempt_event_key': None,
        'terminal_event_id': None, 'terminal_event_key': None, 'optimization_id': None, 'error_code': None,
    }
    columns = ('id', 'key', 'attempt', 'phase', 'commit', 'config', 'cutoff', 'at', 'error',
               'input_schema', 'request_matches', 'token', 'original_id', 'original_key', 'request_sha256',
               'output_empty', 'output_schema', 'output_run_key', 'output_optimization_id', 'output_outcome',
               'output_artifact_sha256', 'output_exit')
    with conn.cursor() as cursor:
        cursor.execute("SELECT run_event_id, run_event_key, attempt, phase, code_commit, config_digest, "
                       "scheduled_for, occurred_at, error_code, input_evidence->0->>'schema', "
                       "input_evidence->0->'request' = %s::jsonb, input_evidence->0->>'claim_token', "
                       "input_evidence->0->>'original_started_event_id', "
                       "input_evidence->0->>'original_started_event_key', input_evidence->0->>'request_sha256', "
                       "output_evidence->0 IS NULL, output_evidence->0->>'schema', "
                       "output_evidence->0->>'run_key', output_evidence->0->>'optimization_id', "
                       "output_evidence->0->>'outcome', output_evidence->0->>'artifacts_sha256', "
                       "output_evidence->0->>'exit_code' FROM papersim.command_run_events "
                       'WHERE operation_key = %s AND command = %s ORDER BY run_event_id LIMIT 3',
                       (_optimize_json(identity).decode(), operation_key, _OPTIMIZE_COMMAND))
        events = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        cursor.execute('SELECT optimization_id, code_commit, outcome, artifacts_sha256 '
                       'FROM papersim.optimization_runs WHERE optimization_key = %s', (run_key,))
        run = cursor.fetchone()
        if not events:
            if run is not None:
                raise PapersimStoreError(code)
            return receipt
        root = events[0]
        token = root['token']
        if (len(events) > 2 or root['phase'] != 'STARTED' or root['input_schema'] != _OPTIMIZE_CLAIM
                or not root['output_empty'] or root['error'] is not None
                or not isinstance(token, str) or len(token) != 32
                or any(char not in '0123456789abcdef' for char in token)):
            raise PapersimStoreError(code)
        if not root['request_matches']:
            raise PapersimStoreError('papersim_optimize_request_conflict')
        for event in events:
            if (event['commit'] != identity['code_commit'] or event['config'] != config_digest
                    or _optimize_time(event['cutoff']) != identity['data_cutoff'] or event['at'] < root['at']
                    or event['key'] != command_run_event_key(operation_key, event['attempt'], event['phase'])):
                raise PapersimStoreError(code)
        receipt.update(started_event_id=root['id'], started_event_key=root['key'])
        if len(events) == 1:
            if run is not None:
                raise PapersimStoreError(code)
            live = _nba_odr_started_is_live(cursor, run_key, token)
            return {**receipt, 'status': 'in_progress' if live else 'interrupted'}

    tail = events[1]
    if (tail['input_schema'] != _OPTIMIZE_ATTEMPT or tail['original_id'] != str(root['id'])
            or tail['original_key'] != root['key'] or tail['request_sha256'] != config_digest
            or tail['attempt'] != root['attempt'] or tail['phase'] not in {'FAILED', 'SUCCEEDED'}):
        raise PapersimStoreError(code)
    if tail['phase'] == 'FAILED' and tail['output_schema'] == _OPTIMIZE_ATTEMPT and tail['error'] and run is None:
        return {**receipt, 'status': 'interrupted', 'attempt_event_id': tail['id'],
                'attempt_event_key': tail['key'], 'error_code': tail['error']}
    if run is None or tail['output_schema'] != _OPTIMIZE_TERMINAL:
        raise PapersimStoreError(code)
    optimization_id, commit, outcome, artifact_sha256 = run
    failed = outcome == 'RUN_FAILED'
    if (commit != identity['code_commit'] or tail['output_run_key'] != run_key
            or tail['output_optimization_id'] != str(optimization_id) or tail['output_outcome'] != outcome
            or tail['output_artifact_sha256'] != artifact_sha256 or tail['output_exit'] != str(int(failed))
            or tail['phase'] != ('FAILED' if failed else 'SUCCEEDED') or bool(tail['error']) != failed):
        raise PapersimStoreError(code)
    return {**receipt, 'status': 'sealed_failure' if failed else 'sealed_success', 'outcome': outcome,
            'optimization_id': optimization_id, 'terminal_event_id': tail['id'], 'terminal_event_key': tail['key'],
            'error_code': tail['error']}


def preview_managed_optimization(conn, *, clock, family, data_cutoff, random_seed, code_commit, attempt, targets,
                                 schedule_id, manifest_sha256, manifest_byte_length, manifest, source_refs,
                                 operation_key=None) -> dict:
    """Verify the registered request and read its current state in one read-only snapshot."""
    from psycopg import Error
    from polymarket_alpha_lab.papersim.optimize import runner

    identity = _managed_optimizer_request(
        conn, clock=clock, family=family, data_cutoff=data_cutoff, random_seed=random_seed, code_commit=code_commit,
        attempt=attempt, targets=targets, schedule_id=schedule_id, manifest_sha256=manifest_sha256,
        manifest_byte_length=manifest_byte_length, manifest=manifest, source_refs=source_refs,
        operation_key=operation_key,
    )
    run_key = identity['run_key']
    preview = {'dry_run': True, 'family': identity['family'], 'run_key': run_key,
               'operation_key': _optimize_operation_key(run_key), 'manifest_sha256': manifest_sha256,
               'manifest_byte_length': manifest_byte_length}
    original, stage = None, 'verify_inputs'
    try:
        if schedule_id == runner.NBA_ODR_SCHEDULE_ID:
            context = _nba_odr_managed_context(conn, clock, code_commit)
            with _managed_read_transaction(conn, clock, stage=stage, closeout=True):
                identity, registration = _bind_nba_odr_request(conn, identity, manifest, context)
                stage = 'read_operation'
                return {**preview, **_preview_nba_odr_state(conn, identity, registration)}
        with _managed_read_transaction(conn, clock, stage=stage, closeout=True):
            _verify_optimizer_inputs(conn, identity, manifest)
            stage = 'read_operation'
            original, _, run, terminal = _optimization_operation(conn, run_key, identity)
            if terminal is not None:
                result = _optimization_terminal(conn, original, run, terminal, replayed=True)
                return {**result, **preview, 'status': 'sealed',
                        'run_scorecard': result['scorecard'], 'trial_count': run['trial_count']}
            if original is not None:
                preview.update(started_event_id=original['run_event_id'], started_event_key=original['run_event_key'])
            return {**preview, 'status': 'not_run' if original is None else 'in_progress',
                    'outcome': None, 'run_scorecard': None, 'trial_count': None, 'artifacts_sha256': None,
                    'artifact_byte_length': None, 'exit_code': 0}
    except (Error, OSError, TypeError, KeyError, ValueError) as error:
        raise _optimizer_error(error, stage=stage, original=original) from None


def run_managed_optimization(conn, *, clock, family, data_cutoff, random_seed, code_commit, attempt, targets,
                             schedule_id, manifest_sha256, manifest_byte_length, manifest, source_refs,
                             compute=_MISSING_OPTIMIZER_COMPUTE, reconnect=None, operation_key=None) -> dict:
    """Claim, compute outside a transaction, and seal one managed experiment."""
    from polymarket_alpha_lab.papersim.optimize import runner

    nba_odr = schedule_id == runner.NBA_ODR_SCHEDULE_ID
    if nba_odr and compute is not _MISSING_OPTIMIZER_COMPUTE:
        raise ManagedOptimizationError('papersim_optimize_nba_odr_callback_forbidden', stage='request')
    if not nba_odr and compute is _MISSING_OPTIMIZER_COMPUTE:
        raise TypeError("run_managed_optimization() missing required keyword-only argument: 'compute'")
    identity = _managed_optimizer_request(
        conn, clock=clock, family=family, data_cutoff=data_cutoff, random_seed=random_seed, code_commit=code_commit,
        attempt=attempt, targets=targets, schedule_id=schedule_id, manifest_sha256=manifest_sha256,
        manifest_byte_length=manifest_byte_length, manifest=manifest, source_refs=source_refs,
        operation_key=operation_key,
    )
    if nba_odr:
        return _run_managed_nba_odr(conn, clock, identity, family, manifest, attempt, reconnect)
    state = {}
    try:
        return _execute_optimizer_operation(conn, clock, identity, family, manifest, attempt, compute, state)
    except ManagedOptimizationError as error:
        if error.indeterminate and reconnect is not None:
            return _confirm_optimizer_commit(clock, identity, family, manifest, attempt, compute, reconnect, state, error)
        raise


# ---------------------------------------------------------------------------
# B4 report pipeline-state readers (plan 05 section 2 / DELIVERY_PLAN 80.8).
# Read-only SELECTs over the append-only pipeline evidence; the whole
# multi-query read runs inside ONE transaction opened with REPEATABLE READ
# READ ONLY, so the report observes a single consistent store snapshot
# (the B4-local read-consistency technique; the FX-29 deliverables in
# ``bankroll_daily_check`` and the evaluate loader stay B3's scope). The
# statement text is lifted verbatim from the B4 native proof module
# (tests/test_papersim_report_store_native.py), which keeps exercising
# the same surface end-to-end against a real cluster.
# ---------------------------------------------------------------------------

from polymarket_alpha_lab.papersim import snapshots  # noqa: E402  (B4 append block)

#: B4 re-export: the report driver (and its native proof module) register
#: the canonical report bytes through the audited snapshots recording
#: face, so every papersim write the ``report`` command performs stays
#: behind this single adapter module.
register_snapshot = snapshots.register_snapshot

_REPORT_WRITE_TRANSACTION = 'SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE'
_REPORT_ARCHIVE_LOCK = 'SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))'
_REPORT_STORED_PHASES = (
    'SELECT phase, run_event_id, occurred_at FROM papersim.command_run_events '
    'WHERE operation_key = %s AND attempt = %s ORDER BY run_event_id')


def archive_report(conn, assembly, *, attempt=1, clock=None) -> dict:
    """Publish both receipts and their immutable bytes in one transaction.

    The post-lock lookup is a separate READ COMMITTED statement. A waiter
    therefore observes the previous publisher's commit even when the caller's
    session default is repeatable read. Other commands keep their own locks.
    """
    from polymarket_alpha_lab.papersim import report

    if clock is None:
        clock = lambda: datetime.now(UTC)
    key = assembly.report_key
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(_REPORT_WRITE_TRANSACTION)
        cursor.execute(_REPORT_ARCHIVE_LOCK, (key,))
        cursor.execute(_REPORT_STORED_PHASES, (key, attempt))
        phases = {phase: {'run_event_id': row_id, 'occurred_at': at}
                  for phase, row_id, at in cursor.fetchall()}
        if tuple(phases) not in ((), ('STARTED',), ('STARTED', 'SUCCEEDED')):
            raise PapersimStoreError('papersim_report_archive_phase_conflict')
        stored = snapshots.read_snapshot(conn, assembly.content_sha256)
        if 'SUCCEEDED' in phases and stored is None:
            raise PapersimStoreError('papersim_report_archive_snapshot_missing')

        # The common receipt writer validates the entire immutable non-clock
        # payload on replay. Sample each clock only at its actual operation.
        def append_phase(phase, evidence=()):
            previous = phases.get(phase)
            at = previous['occurred_at'] if previous else clock()
            saved = record_command_run_event(conn, report.report_receipt(
                operation_key=key, phase=phase, occurred_at=at,
                attempt=attempt, output_evidence=evidence))
            return previous or {'run_event_id': saved['run_event_id'], 'occurred_at': at}

        started = append_phase('STARTED')
        registration = report.report_snapshot_registration(
            assembly.content, fetched_at=stored['fetched_at'] if stored else clock())
        if stored is None:
            stored = register_snapshot(conn, registration)
        # Another operator may have registered identical bytes under a different
        # origin. A matching digest alone does not establish report provenance.
        if (stored['content_sha256'] != assembly.content_sha256
                or stored['byte_length'] != len(assembly.content)
                or stored['kind'] != registration.kind
                or stored['source'] != registration.source
                or stored['schema_version'] != registration.schema_version
                or ('content' in stored and bytes(stored['content']) != assembly.content)):
            raise PapersimStoreError('papersim_report_archive_snapshot_conflict')
        succeeded = append_phase('SUCCEEDED', (assembly.content_sha256,))
    return {'started': started, 'succeeded': succeeded,
            'snapshot': {'content_sha256': assembly.content_sha256, 'fetched_at': stored['fetched_at']}}

_REPORT_RECEIPTS_SQL = (
    'SELECT command, operation_key, attempt, phase, occurred_at, run_event_id, '
    'code_commit, error_code, scan_id FROM papersim.command_run_events '
    'ORDER BY run_event_id')
_REPORT_SCANS_SQL = (
    'SELECT scan_id, team, started_at, finished_at, code_commit, scan_key, '
    'operation_key FROM papersim.scan_runs ORDER BY scan_id')
_REPORT_CONTROLS_SQL = (
    'SELECT control_event_id, queue_id, control_kind, rule, observed_at '
    'FROM papersim.queue_control_events ORDER BY control_event_id')
_REPORT_CHECKS_SQL = (
    'SELECT check_id, as_of, passed, event_watermark, identity_difference '
    'FROM papersim.bankroll_checks ORDER BY check_id')
#: Markets with an EFFECTIVE (non-UNKNOWN) outcome observation, matched on
#: the candidates' strongest bound identity (condition_id, else market_id,
#: else market_slug) -- the same identity precedence the evaluate loader
#: uses, joined so coverage never counts a market the outcomes table has
#: only seen UNKNOWN rows for.
_REPORT_MARKETS_WITH_OUTCOME_SQL = (
    'SELECT count(DISTINCT t.ref) FROM ('
    '  SELECT coalesce(c.condition_id, c.market_id, c.market_slug) AS ref'
    '  FROM papersim.candidates c'
    "  WHERE coalesce(c.condition_id, c.market_id, c.market_slug) IS NOT NULL"
    '    AND EXISTS (SELECT 1 FROM papersim.market_outcomes mo'
    "                WHERE mo.outcome <> 'UNKNOWN'"
    '                  AND ((c.condition_id IS NOT NULL'
    '                       AND mo.condition_id = c.condition_id)'
    '                   OR (c.condition_id IS NULL AND c.market_id IS NOT NULL'
    '                       AND mo.market_id = c.market_id)'
    '                   OR (c.condition_id IS NULL AND c.market_id IS NULL'
    '                       AND c.market_slug IS NOT NULL'
    '                       AND mo.market_slug = c.market_slug)))) t')
#: The eight honest coverage scalars behind the report stage matrix
#: (name -> SELECT; the effective-outcome join is the member above).
_REPORT_COVERAGE_SQL = (
    ('candidates_total',
     'SELECT count(*) FROM papersim.candidates'),
    ('candidates_with_marks',
     'SELECT count(DISTINCT candidate_id) FROM papersim.candidate_marks'),
    ('mark_observations',
     'SELECT count(*) FROM papersim.candidate_marks'),
    ('markets_bound',
     'SELECT count(DISTINCT coalesce(condition_id, market_id, market_slug)) '
     'FROM papersim.candidates '
     'WHERE coalesce(condition_id, market_id, market_slug) IS NOT NULL'),
    ('markets_with_outcome', _REPORT_MARKETS_WITH_OUTCOME_SQL),
    ('outcome_observations',
     'SELECT count(*) FROM papersim.market_outcomes'),
    ('entries_total',
     'SELECT count(*) FROM papersim.entries'),
    ('entries_settled',
     'SELECT count(*) FROM papersim.entries e JOIN papersim.settlements s '
     'ON s.entry_id = e.entry_id'),
)


_REPORT_STAGE_TIMES_SQL = (
    'SELECT (SELECT max(observed_at) FROM papersim.candidate_marks) AS marks, '
    '(SELECT max(greatest(observed_at, confirmed_at)) FROM papersim.market_outcomes) AS outcomes, '
    '(SELECT max(s.confirmed_at) FROM papersim.settlements s '
    ' JOIN papersim.entries e ON e.entry_id = s.entry_id) AS settle')

_REPORT_BASKETBALL_CANDIDATES_SQL = (
    'SELECT candidate_id, candidate_key, team, model_name, market_type, market_kind, side, '
    'condition_id, market_id, market_slug, yes_token_id, no_token_id, '
    'event_key, event_start_at, evaluated_at, snapshot_refs '
    'FROM papersim.candidates ORDER BY candidate_id')
_REPORT_BASKETBALL_QUOTES_SQL = """
    SELECT candidate_id, evidence_role, market_ref_kind, market_ref, token_ref, side,
           observed_at, snapshot_sha256, source_kind, source_ref
    FROM (
        SELECT DISTINCT ON (candidate_id) candidate_id, evidence_role, market_ref_kind,
               market_ref, token_ref, side, observed_at, snapshot_sha256, source_kind, source_ref
        FROM papersim.candidate_price_evidence
        WHERE candidate_id = ANY(%s) AND evidence_role = 'DECISION'
        ORDER BY candidate_id, evidence_id
    ) AS decision
    UNION ALL
    SELECT candidate_id, evidence_role, market_ref_kind, market_ref, token_ref, side,
           observed_at, snapshot_sha256, source_kind, source_ref
    FROM papersim.candidate_price_evidence
    WHERE candidate_id = ANY(%s) AND evidence_role = 'CLOSE' AND snapshot_sha256 IS NOT NULL
    UNION ALL
    SELECT candidate_id, sampling_kind, market_ref_kind, market_ref, token_ref, side,
           observed_at, snapshot_sha256, source_kind, source_ref
    FROM papersim.candidate_marks WHERE candidate_id = ANY(%s) AND snapshot_sha256 IS NOT NULL
    ORDER BY candidate_id, observed_at, evidence_role, snapshot_sha256
"""
_REPORT_BASKETBALL_OUTCOMES_SQL = """
    SELECT c.candidate_id, o.outcome_id, o.outcome, o.observed_at, o.confirmed_at,
           o.source_snapshot_sha256, o.source_kind, o.source_ref, o.condition_id,
           o.market_id, o.market_slug, o.yes_token_id, o.no_token_id
    FROM papersim.candidates c JOIN papersim.market_outcomes o ON (
        (c.condition_id IS NOT NULL AND o.condition_id = c.condition_id) OR
        (c.condition_id IS NULL AND c.market_id IS NOT NULL AND o.market_id = c.market_id) OR
        (c.condition_id IS NULL AND c.market_id IS NULL AND o.market_slug = c.market_slug))
    WHERE c.candidate_id = ANY(%s)
    ORDER BY c.candidate_id, o.observed_at, o.outcome_id
"""
_REPORT_BASKETBALL_METADATA_SQL = """
    SELECT content_sha256, byte_length, kind, source, fetched_at, schema_version
    FROM papersim.snapshots
    WHERE content_sha256 = ANY(%s) AND kind = ANY(%s) AND schema_version = 1
      AND byte_length BETWEEN 1 AND %s
    ORDER BY content_sha256
"""
_REPORT_BASKETBALL_BODIES_SQL = (
    'SELECT content_sha256, content FROM papersim.snapshots '
    'WHERE content_sha256 = ANY(%s) ORDER BY content_sha256')


def _report_rows(conn, sql: str, params=None) -> list[dict]:
    cursor = conn.cursor()
    if params is None:
        cursor.execute(sql)
    else:
        cursor.execute(sql, params)
    columns = _columns_of(cursor)
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _basketball_body_batches(references, metadata):
    """Keep shared bodies together and apply the round limits to each position."""
    from polymarket_alpha_lab.papersim import report
    from polymarket_alpha_lab.papersim.adapters import contracts

    ids, digests, deferred = [], set(), set()
    byte_count = 0
    for candidate_id, refs in references.items():
        needed, oversized = report.bounded_basketball_snapshots(
            {candidate_id: refs}, metadata,
            max_bytes=contracts.DEFAULT_MAX_TOTAL_RESPONSE_BYTES,
            max_rows=contracts.FIRST_VERSION_MAX_REQUESTS_PER_ROUND)
        extra = set(needed).difference(digests)
        extra_bytes = sum(metadata[key]['byte_length'] for key in extra)
        # This target bounds ordinary I/O batches. A single position may need
        # several larger, individually valid bodies to corroborate its terms.
        if ids and byte_count + extra_bytes > contracts.HARD_MAX_RESPONSE_BYTES:
            yield ids, digests, deferred
            ids, digests, deferred = [], set(), set()
            extra = set(needed)
            byte_count = 0
            extra_bytes = sum(metadata[key]['byte_length'] for key in extra)
        ids.append(candidate_id)
        deferred.update(oversized)
        digests.update(extra)
        byte_count += extra_bytes
    if ids:
        yield ids, digests, deferred


def _report_basketball_evidence(conn):
    from polymarket_alpha_lab.papersim import report
    from polymarket_alpha_lab.papersim.adapters import contracts

    candidates = [row for row in _report_rows(conn, _REPORT_BASKETBALL_CANDIDATES_SQL)
                  if report.is_basketball_candidate(row)]
    finals, unavailable = [], []
    batch_size = contracts.FIRST_VERSION_MAX_MARKETS_PER_ROUND
    for start in range(0, len(candidates), batch_size):
        group = candidates[start:start + batch_size]
        ids = [row['candidate_id'] for row in group]
        quotes = _report_rows(conn, _REPORT_BASKETBALL_QUOTES_SQL, (ids, ids, ids))
        outcomes = _report_rows(conn, _REPORT_BASKETBALL_OUTCOMES_SQL, (ids,))
        inputs = dict(candidates=group, quotes=quotes, outcomes=outcomes)
        references = report.basketball_snapshot_references(**inputs)
        cited = sorted({key for refs in references.values() for key in refs})
        metadata = {row['content_sha256']: row for row in _report_rows(
            conn, _REPORT_BASKETBALL_METADATA_SQL,
            (cited, list(report.BASKETBALL_SNAPSHOT_KINDS), contracts.HARD_MAX_RESPONSE_BYTES))} if cited else {}
        references = report.basketball_snapshot_references(**inputs, snapshot_metadata=metadata)
        for batch_ids, digests, deferred in _basketball_body_batches(references, metadata):
            keys = set(batch_ids)
            bodies = _report_rows(conn, _REPORT_BASKETBALL_BODIES_SQL, (sorted(digests),)) if digests else ()
            checked = report.verify_basketball_finals(
                candidates=[row for row in group if row['candidate_id'] in keys],
                quotes=[row for row in quotes if row['candidate_id'] in keys],
                outcomes=[row for row in outcomes if row['candidate_id'] in keys],
                snapshots={row['content_sha256']: {**metadata[row['content_sha256']], **row}
                           for row in bodies}, deferred_candidates=frozenset(deferred))
            finals.extend(checked.finals)
            unavailable.extend(checked.unavailable)
    return report.BasketballFinalsEvidence(tuple(finals), tuple(unavailable))


def _report_scalar(conn, sql: str) -> int:
    cursor = conn.cursor()
    cursor.execute(sql)
    return int(cursor.fetchone()[0])


def read_pipeline_state(conn, *, evaluation_loader=None) -> dict:
    """Read every B4 report input inside ONE consistent store snapshot.

    The whole multi-query read runs inside a single transaction whose
    FIRST statement is ``SET TRANSACTION ISOLATION LEVEL REPEATABLE READ
    READ ONLY``, so the snapshot is taken once for every query: pipeline
    receipts, scan runs, queue-control latch rows, bankroll checks and
    the eight coverage scalars. When supplied, ``evaluation_loader(conn)``
    reads the candidate queue within the repeatable-read, read-only
    snapshot this function has already opened on ``conn``. The CLI passes
    ``_evaluation_candidates_in_snapshot`` so the report's queue metrics
    and pipeline rows observe the same state.  Every statement is a SELECT;
    nothing here writes, and the transaction is closed by the ``with``
    block before the caller assembles anything.
    """
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        receipts = _report_rows(conn, _REPORT_RECEIPTS_SQL)
        scan_runs = _report_rows(conn, _REPORT_SCANS_SQL)
        controls = _report_rows(conn, _REPORT_CONTROLS_SQL)
        checks = _report_rows(conn, _REPORT_CHECKS_SQL)
        coverage = {name: _report_scalar(conn, sql)
                    for name, sql in _REPORT_COVERAGE_SQL}
        stage_evidence_times, = _report_rows(conn, _REPORT_STAGE_TIMES_SQL)
        basketball = _report_basketball_evidence(conn)
        evaluation_candidates = (evaluation_loader(conn)
                                 if evaluation_loader is not None else ())
    return {
        'command_run_events': tuple(receipts),
        'scan_runs': tuple(scan_runs),
        'queue_control_events': tuple(controls),
        'bankroll_checks': tuple(checks),
        'coverage': coverage,
        'stage_evidence_times': stage_evidence_times,
        'basketball': basketball,
        'evaluation_candidates': tuple(evaluation_candidates),
    }
