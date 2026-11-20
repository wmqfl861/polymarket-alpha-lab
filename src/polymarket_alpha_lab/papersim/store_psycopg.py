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
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

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
    ScanSubmission,
    SettlementRecord,
    _as_decimal,
    bankroll_identity_difference,
    entry_mark_key,
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


def connect_managed(repo_root: Path, *, info: dict | None = None):
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
    import psycopg  # lazy: the audited validation already ran in managed_dsn
    with binding.bound(info['instance_id'], info['root_sha256'], info['system_identifier']):
        connection = psycopg.connect(dsn)
        try:
            binding.verify_connection(connection.cursor())
            connection.rollback()   # end the check's read-only transaction; hand off IDLE
        except BaseException:
            connection.close()      # fail closed: release, never hand off
            raise
    return connection


def connect_owner(repo_root: Path, *, info: dict | None = None):
    """Owner connection (pal_owner) for migration application only."""
    repo_root = Path(repo_root)
    if info is None:
        info = read_instance(repo_root)
    dsn = managed_dsn(info, owner=True, repo_root=repo_root)
    import psycopg  # lazy: the audited validation already ran in managed_dsn
    return psycopg.connect(dsn)


def connect_explicit(dsn: str, *, label: str = EXPLICIT_DSN_LABEL):
    """Validated explicit DSN connection (isolated/throwaway clusters only)."""
    dsn = _validated(dsn, label)  # pure Python: rejects before any driver import
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
                cursor.execute(_ENTRY_INSERT, (candidate_id, entry.legacy_source_id, entry.stake,
                                               entry.price, entry.shares, entry.fee_category,
                                               entry.fee_rate, entry.entry_fee, entry.fee_source,
                                               entry.tier, entry.entered_at, entry.is_legacy))
                entry_id = int(cursor.fetchone()[0])
                event_key = f'entry:{entry_id}:cash'
                cursor.execute(_CASH_INSERT, (event_key, entry.entered_at, 'USD',
                                              -(_as_decimal(entry.stake) + _as_decimal(entry.entry_fee)),
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
                      'RETURNING settlement_id')

_CASH_LEG_INSERT = ('INSERT INTO papersim.bankroll_events (event_key, occurred_at, currency, '
                    'amount, reason, entry_id, scan_id, note, is_legacy) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING event_id')

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


def settlement_record(conn, record: SettlementRecord) -> dict:
    """Insert one settlement and its single cash leg in ONE transaction (M2 3.6).

    The entry fee was already paid in SCAN_ENTRY; the cash leg carries the
    settlement cash flow only (natural/voided: proceeds; exit: proceeds
    minus the sell-leg fee). ``entry_id`` uniqueness plus exact content
    comparison make repeats idempotent; a different payload for a settled
    entry is ``papersim_settlement_conflict`` and never an update. Any
    failure (for example a broken evidence reference) rolls back both the
    settlement and the cash leg: no orphan cash movements survive.
    """
    record.validate()
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SELECT settlement_id, ' + _SETTLEMENT_COLUMNS
                       + ' FROM papersim.settlements WHERE entry_id = %s', (record.entry_id,))
        columns = ['settlement_id'] + [name for name in
                                       _SETTLEMENT_COLUMNS.replace(' ', '').split(',')]
        row = cursor.fetchone()
        if row is not None:
            stored = dict(zip(columns, row, strict=True))
            _compare_stored(stored, record.comparison(), 'papersim_settlement_conflict')
            settlement_id = int(stored['settlement_id'])
            inserted = False
        else:
            cursor.execute(_SETTLEMENT_INSERT, (record.entry_id,) + tuple(
                record.comparison()[name] for name in columns[1:]))
            settlement_id = int(cursor.fetchone()[0])
            cursor.execute(_CASH_LEG_INSERT,
                           (record.event_key(), record.confirmed_at, 'USD',
                            record.cash_amount(), record.cash_reason(), record.entry_id, None,
                            record.evidence, record.is_legacy))
            bankroll_event_id = int(cursor.fetchone()[0])
            inserted = True
        if not inserted:
            cursor.execute('SELECT event_id FROM papersim.bankroll_events WHERE event_key = %s '
                           'ORDER BY event_id', (record.event_key(),))
            legs = [int(leg[0]) for leg in cursor.fetchall()]
            if len(legs) != 1:
                raise PapersimStoreError('papersim_settlement_cash_missing')
            bankroll_event_id = legs[0]
    return {'settlement_id': settlement_id, 'inserted': inserted,
            'bankroll_event_id': bankroll_event_id}


# ---------------------------------------------------------------------------
# B3 settle snapshot loader (05 section 2 B3 row / DELIVERY_PLAN 80.8; the
# FX-29/N-4 consistent-snapshot read that feeds ``papersim.settle``).
# ---------------------------------------------------------------------------

_OPEN_ENTRIES_SQL = (
    'SELECT e.entry_id, e.candidate_id, c.team, c.side, e.stake, e.price, '
    'e.fee_category, e.fee_source, e.entered_at, c.condition_id, c.market_id, '
    'c.market_slug, '
    'EXISTS (SELECT 1 FROM papersim.marks m WHERE m.entry_id = e.entry_id '
    'AND m.is_close) AS exit_marked '
    'FROM papersim.entries e JOIN papersim.candidates c ON c.candidate_id = e.candidate_id '
    'WHERE NOT EXISTS (SELECT 1 FROM papersim.settlements s WHERE s.entry_id = e.entry_id) '
    'ORDER BY e.entry_id'
)


def _identity_of(row):
    """The outcome-lookup identity (evaluate-loader precedence)."""
    for column in ('condition_id', 'market_id', 'market_slug'):
        if row[column] is not None:
            return column, row[column]
    return None, None


def open_entries_for_settlement(conn):
    """Open entries + effective captured outcomes in ONE consistent snapshot.

    FX-29: the FIRST statement of the transaction sets
    ``SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY``; the
    open entries (minus already-settled ones) and every entry's effective
    captured outcome (the audited ``effective_market_outcome`` selection,
    plus the winning row's ``confirmed_at``) are read inside that single
    snapshot, so a concurrent capture or settlement cannot split the
    read.  The settle driver (``papersim.settle.run_settle``) receives
    this callable as its ``open_entries`` loader; the per-entry
    settlement writes stay in their own transactions exactly as
    ``settlement_record`` provides.
    """
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cursor.execute(_OPEN_ENTRIES_SQL)
        columns = [description.name for description in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        entries = []
        for row in rows:
            captured = settle.CapturedOutcome(None, 'UNKNOWN')
            column, identity = _identity_of(row)
            if column is not None:
                effective = effective_market_outcome(conn, **{column: identity})
                if effective.outcome_id is not None:
                    cursor.execute('SELECT confirmed_at FROM papersim.market_outcomes '
                                   'WHERE outcome_id = %s', (effective.outcome_id,))
                    confirmed_at = cursor.fetchone()[0]
                    captured = settle.captured_from_effective(effective,
                                                              confirmed_at=confirmed_at)
            entries.append(settle.OpenSettleEntry(
                entry_id=int(row['entry_id']), candidate_id=int(row['candidate_id']),
                team=str(row['team']), side=str(row['side']), stake=row['stake'],
                price=row['price'], fee_category=str(row['fee_category']),
                entered_at=row['entered_at'], fee_source=str(row['fee_source']),
                condition_id=row['condition_id'], market_id=row['market_id'],
                market_slug=row['market_slug'], exit_marked=bool(row['exit_marked']),
                outcome=captured))
    return tuple(entries)


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
        passed = abs(difference) <= tolerance
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
    with conn.transaction():
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
    with conn.transaction():
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
    with conn.transaction():
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

    from polymarket_alpha_lab.papersim.optimize.registry import derive_lineage_roles

    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(_ROLE_EVENT_SELECT + ' WHERE lineage = %s '
                       'ORDER BY effective_at, role_event_id', (lineage,))
        events = [_row_to_role_event(row) for row in cursor.fetchall()]
    if not events:
        return None
    batches = []
    current = []
    stamp = None
    for event in events:
        if current and event.effective_at != stamp:
            batches.append(current)
            current = []
        current.append(event)
        stamp = event.effective_at
    if current:
        batches.append(current)
    return derive_lineage_roles(batches)


def record_role_events(conn, batch: RoleEventBatch) -> dict:
    """Write one transaction's role events atomically (MO-2, 05).

    The batch IS the promotion-handover transaction unit: the prior
    state is loaded and validated by the pure registry FIRST, then all
    events insert inside ONE database transaction -- a handover can
    never land as half a pair. Idempotent replay: when every key of
    the batch already exists with identical content the original ids
    return (``inserted=False``); a PARTIAL replay is a conflict.

    Concurrency window (plan review P3): the prior check is not
    mutually exclusive -- two concurrent writers could both read the
    same prior and each validate a legal batch, breaching a cap (e.g.
    two different candidate registrations meeting MAX_CANDIDATES=2).
    The registered mitigation is the optimizer's single-writer rhythm
    (role events come from the scheduled decide/apply nodes, MO-8) --
    do NOT call this function from concurrent contexts; an advisory
    lock can be added if a second writer ever appears.
    """

    from polymarket_alpha_lab.papersim.optimize.registry import (
        derive_lineage_roles,
        validate_role_event_batch,
    )

    batch.validate()
    lineage = batch.events[0].lineage
    with conn.transaction():
        cursor = conn.cursor()
        keys = [event.key() for event in batch.events]
        cursor.execute('SELECT role_event_key FROM papersim.model_role_events '
                       'WHERE role_event_key = ANY(%s)', (keys,))
        present = {row[0] for row in cursor.fetchall()}
        if present:
            if len(present) != len(keys):
                raise PapersimStoreError('papersim_role_event_partial_replay')
            ids = {}
            for event in batch.events:
                cursor.execute('SELECT role_event_id, ' + _ROLE_EVENT_COLUMNS
                               + ' FROM papersim.model_role_events '
                               'WHERE role_event_key = %s', (event.key(),))
                columns = ['role_event_id'] \
                    + _ROLE_EVENT_COLUMNS.replace(' ', '').split(',')
                row = cursor.fetchone()
                stored = dict(zip(columns, row, strict=True))
                _compare_stored_row(stored, event.comparison(),
                                    'papersim_role_event_conflict')
                ids[event.role] = int(stored['role_event_id'])
            return {'lineage': lineage, 'role_event_ids': ids, 'inserted': False}
        prior = load_lineage_roles(conn, lineage)
        registry_events = [_submission_to_registry_event(event)
                           for event in batch.events]
        validate_role_event_batch(prior, registry_events)
        ids = {}
        for event in batch.events:
            columns = _ROLE_EVENT_COLUMNS.replace(' ', '').split(',')
            values = event.comparison()
            cursor.execute(_ROLE_EVENT_INSERT, (event.key(),) + tuple(
                _require_json_payload(values[name], name)
                if name == 'forward_plan' else values[name]
                for name in columns))
            ids[event.role] = int(cursor.fetchone()[0])
    return {'lineage': lineage, 'role_event_ids': ids, 'inserted': True}


def record_optimizer_control_event(
        conn, submission: OptimizerControlEventSubmission) -> dict:
    """Append one freeze/unfreeze row (idempotent on the control key)."""

    submission.validate()
    key = submission.key()
    with conn.transaction():
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
        cursor.execute(_CONTROL_INSERT, (key,) + tuple(
            submission.comparison()[name]
            for name in _CONTROL_COLUMNS.replace(' ', '').split(',')))
        return {'optimizer_control_id': int(cursor.fetchone()[0]),
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
    """

    from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

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
    if spec.spec_sha256() != spec_sha256:
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


def registry_freeze_state(conn) -> dict:
    """The effective freeze state: global plus per-lineage.

    A lineage is frozen when the GLOBAL state is frozen or its own
    latest LINEAGE event is a FREEZE. Read-only folding of the control
    log; the MO-8 decide node consults this before any handover.
    """

    cursor = conn.cursor()
    cursor.execute('SELECT scope, lineage, control_kind FROM '
                   'papersim.optimizer_control_events '
                   'ORDER BY occurred_at, optimizer_control_id')
    global_frozen = False
    frozen_lineages: set[str] = set()
    for scope, lineage, control_kind in cursor.fetchall():
        if scope == 'GLOBAL':
            global_frozen = control_kind == 'FREEZE'
        else:
            if control_kind == 'FREEZE':
                frozen_lineages.add(lineage)
            else:
                frozen_lineages.discard(lineage)
    return {'global_frozen': global_frozen,
            'frozen_lineages': sorted(frozen_lineages)}


def initialize_registry(conn, *, families, actor: str, code_commit: str,
                        effective_at) -> dict:
    """Write every family's baseline spec and initial roles; idempotent.

    One lineage at a time (each in its own transaction): when a
    ``role='BASELINE'`` row already exists for the lineage the stored
    digest must equal the family's current ``baseline_spec()`` digest
    -- equal means SKIP (the idempotent path), different means
    ``papersim_registry_init_conflict`` (the code re-identified the
    baseline; never silently rewritten, a human decides). A fresh
    lineage registers the spec row (``source='frozen_baseline'``) and
    the initialization pair ``[BASELINE, ACTIVE]`` of the same digest.
    The ``optimize init`` CLI wiring is MO-6; MO-2 provides the
    function only.
    """

    from polymarket_alpha_lab.papersim.optimize.registry import lineage_id

    if not isinstance(families, (list, tuple)) or not families:
        raise PapersimStoreError('papersim_registry_init_families_invalid')
    report = {}
    for family in families:
        baseline = family.baseline_spec()
        for target in family.targets():
            lineage = lineage_id(family.family_id, target)
            with conn.transaction():
                cursor = conn.cursor()
                cursor.execute("SELECT spec_sha256 FROM papersim.model_role_events "
                               "WHERE lineage = %s AND role = 'BASELINE'",
                               (lineage,))
                row = cursor.fetchone()
                if row is not None:
                    if row[0] != baseline.spec_sha256():
                        raise PapersimStoreError('papersim_registry_init_conflict')
                    report[lineage] = {'state': 'skipped',
                                       'spec_sha256': row[0]}
                    continue
                register_model_spec(conn, baseline, source='frozen_baseline',
                                    code_commit=code_commit)
                batch = RoleEventBatch(events=(
                    RoleEventSubmission(lineage=lineage,
                                        spec_sha256=baseline.spec_sha256(),
                                        role='BASELINE', effective_at=effective_at,
                                        reason='registry initialization',
                                        actor=actor, code_commit=code_commit),
                    RoleEventSubmission(lineage=lineage,
                                        spec_sha256=baseline.spec_sha256(),
                                        role='ACTIVE', effective_at=effective_at,
                                        reason='registry initialization',
                                        actor=actor, code_commit=code_commit)))
                record_role_events(conn, batch)
                report[lineage] = {'state': 'initialized',
                                   'spec_sha256': baseline.spec_sha256()}
    return report
