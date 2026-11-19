"""M2 full papersim catalog proofs (S0, M2 plan sections 3.1 and 6.3).

FX-26 re-anchor (fix-list rule 0.3): these are the frozen FOUR-ENTRY
history proofs. The offline manifest/glob/hash checks and the native
empty-face/upgrade proofs run against the frozen pre-FX26 catalog tree
(``tests/papersim_fx26_catalog.py``) with their assertions unchanged;
the current FIVE-entry live manifest and its native apply proofs live in
``tests/test_papersim_fx26_migrations.py``. The frozen M1 fixture stays
self-consistent. Native (opt-in disposable cluster): full apply of the
frozen tree from an empty face, upgrade from the M1 nine-table face with
M1 rows unchanged, M2 drift stop with receipt rollback, append
protection and the new uniqueness contracts.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from tests.papersim_fx26_catalog import write_frozen_fx26_catalog
from tests.papersim_m1_catalog import M1_MIGRATIONS, write_frozen_m1_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / 'database/papersim/migrations'
MANIFEST_PATH = ROOT / 'database/papersim/migrations.lock.json'
M2_MIGRATION = ('20261116000000_papersim_m2_operations.sql',
                '428a8fd5ebf84f945dc4ae78d02f99f61ba39b8743c0bd78b2380e67a013df2d')
V2_MIGRATION = ('20261116000010_papersim_reporting_views.sql',
                '87b802b547f1ffc1d25fa3c8cb401ef643fc5a34f35e6450e5bc56261af1a293')
FULL_CATALOG = M1_MIGRATIONS + (M2_MIGRATION, V2_MIGRATION)
M2_TABLES = ('command_run_events', 'candidate_marks', 'queue_control_events', 'bankroll_checks')
PAPERSIM_VIEWS = ('candidate_scoring_view', 'natural_settlement_summary',
                  'queue_summary_view')
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim migration proof is opt-in')


def at(minutes: int) -> datetime:
    return datetime(2026, 11, 16, 0, 0, tzinfo=UTC) + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Offline full-catalog contract
# ---------------------------------------------------------------------------

def test_full_manifest_lists_the_frozen_m1_pair_plus_the_m2_entry(tmp_path):
    # FX-26: anchored to the frozen four-entry pre-FX26 tree; the live
    # five-entry manifest is proven in tests/test_papersim_fx26_migrations.py.
    tree = write_frozen_fx26_catalog(tmp_path / 'frozen')
    directory = tree / 'database' / 'papersim' / 'migrations'
    manifest = json.loads((tree / 'database' / 'papersim' / 'migrations.lock.json')
                          .read_text(encoding='utf-8'))
    assert [(entry['name'], entry['sha256']) for entry in manifest['migrations']] \
        == list(FULL_CATALOG)
    assert [name for name, _ in FULL_CATALOG] == sorted(path.name for path
                                                        in directory.glob('*.sql'))
    assert all(entry['transaction_wrapper'] is True for entry in manifest['migrations'])


def test_full_catalog_hashes_match_file_bytes_and_strip_wrappers(tmp_path):
    # FX-26: anchored to the frozen four-entry pre-FX26 tree; the live
    # five-entry catalog is proven in tests/test_papersim_fx26_migrations.py.
    tree = write_frozen_fx26_catalog(tmp_path / 'frozen')
    directory = tree / 'database' / 'papersim' / 'migrations'
    catalog = pm.papersim_migration_catalog(tree)
    # Full-catalog inventory count 3 -> 4 (M3/V2 option 1: the fourth
    # managed migration 20261116000010_papersim_reporting_views.sql).
    assert len(catalog) == 4
    for (name, digest, body), (expected_name, expected_digest) in zip(catalog, FULL_CATALOG,
                                                                      strict=True):
        assert name == expected_name
        assert digest == expected_digest
        assert digest == sha256((directory / name).read_bytes()).hexdigest()
        assert 'begin;' not in body.lower() and 'commit;' not in body.lower()
        assert not pm.FORBIDDEN_BODY_TOKENS.search(body)


def test_frozen_m1_fixture_stays_self_consistent(tmp_path):
    tree = write_frozen_m1_catalog(tmp_path / 'repo')
    catalog = pm.papersim_migration_catalog(tree)
    assert [(name, digest) for name, digest, _ in catalog] == list(M1_MIGRATIONS)
    assert pm.papersim_pending_migrations(catalog, []) == catalog


def test_m2_sql_carries_no_url_or_dsn_tokens():
    text = (CATALOG_DIR / M2_MIGRATION[0]).read_text(encoding='utf-8').lower()
    for token in ('postgresql://', 'postgres://', 'http://', 'https://', '://',
                  'database_url', 'sqlite:', '.jsonl'):
        assert token not in text, token


# ---------------------------------------------------------------------------
# Native proofs (disposable cluster only; one fresh database per test)
# ---------------------------------------------------------------------------

def _connect(dsn, *, autocommit=True):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _fresh_managed_database():
    """A private managed-shaped database inside the throwaway cluster."""
    psycopg = pytest.importorskip('psycopg')
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_m2_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind_database(OWNER_DSN, name)
    bootstrap = psycopg.connect(owner_dsn)
    bootstrap.autocommit = True
    with bootstrap.cursor() as cursor:
        from polymarket_alpha_lab.project_postgres import sql
        cursor.execute(sql.bootstrap_sql(uuid.uuid4().hex, 'b' * 64, system_id))
    bootstrap.close()
    return owner_dsn


def _ledger(owner):
    with owner.cursor() as cursor:
        cursor.execute("SELECT name, sha256 FROM papersim_private.migrations ORDER BY name")
        return cursor.fetchall()


def _papersim_tables(owner) -> list:
    # Restricted to BASE TABLE so the M3/V2 reporting views are not
    # counted as business relations (M3/V2 option 1); the three
    # designated views have their own independent assertion below.
    with owner.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.tables "
                       "WHERE table_schema = 'papersim' "
                       "AND table_type = 'BASE TABLE' ORDER BY table_name")
        return [row[0] for row in cursor.fetchall()]


def _seed_one_scan(app_dsn, *, accepted=False):
    """One minimal scan through the M1 store contract; returns ids + app conn."""
    from polymarket_alpha_lab.papersim import store, store_psycopg
    app = store_psycopg.connect_explicit(app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    app.autocommit = True
    scan = store.ScanRunRecord(team='crypto_eth', started_at=at(0), finished_at=at(6),
                               code_commit='c' * 40, schema_version=1, gate_version='g1',
                               operator='unattended',
                               params='{"run": "%s"}' % uuid.uuid4().hex[:8])
    candidate = store.CandidateSubmission(
        candidate_key='c-' + uuid.uuid4().hex[:6],
        conclusion='ACCEPTED' if accepted else 'REJECTED', side='YES', market_kind='terminal',
        team='crypto_eth', model_name='gbm', model_version='r19', input_payload='{}',
        gate_report='{}', evaluated_at=at(5), market_slug='mkt-' + uuid.uuid4().hex[:8])
    if accepted:
        candidate = store.CandidateSubmission(
            candidate_key=candidate.candidate_key, conclusion='ACCEPTED', side='YES',
            market_kind='terminal', team='crypto_eth', model_name='gbm', model_version='r19',
            input_payload='{}', gate_report='{}', evaluated_at=at(5),
            market_slug=candidate.market_slug,
            entry=store.EntrySubmission(stake=250, price=0.56, shares=446.4286,
                                        fee_category='crypto', fee_rate=0.07, entry_fee=10.08,
                                        tier='standard', entered_at=at(6)))
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=scan, candidates=(candidate,)))
    return app, result, candidate


def _seed_one_m1_scan_row(app_dsn):
    """One accepted scan seeded via M1-era column SQL (FX-26 note).

    ``_seed_one_scan`` walks ``record_scan``, whose candidate INSERT grew
    the FX-26 ``event_key``/``event_start_at`` columns -- columns an M1
    face does not have. The upgrade proof seeds its pre-upgrade row with
    the M1 column set directly so the seed stays schema-accurate for the
    face under test; every downstream assertion is unchanged.
    """
    app = _connect(app_dsn)
    scan_key = 'm1-seed-' + uuid.uuid4().hex[:10]
    with app.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, team, "
            "started_at, finished_at, code_commit, schema_version, gate_version, "
            "operator, params) VALUES (%s, %s, 'crypto_eth', %s, %s, %s, 1, 'g1', "
            "'unattended', '{\"seed\": true}'::jsonb) RETURNING scan_id",
            (scan_key, 'f' * 64, at(0), at(6), 'c' * 40))
        scan_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO papersim.candidates (scan_id, candidate_key, side, "
            "market_kind, team, model_name, model_version, input_payload, "
            "conclusion, gate_report, evaluated_at, market_slug) VALUES "
            "(%s, 'c-seed', 'YES', 'terminal', 'crypto_eth', 'gbm', 'r19', "
            "'{}'::jsonb, 'ACCEPTED', '{}'::jsonb, %s, %s) RETURNING candidate_id",
            (scan_id, at(5), 'mkt-seed'))
        candidate_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO papersim.entries (candidate_id, stake, price, shares, "
            "fee_category, fee_rate, entry_fee, fee_source, tier, entered_at) "
            "VALUES (%s, 250, 0.56, 446.4286, 'crypto', 0.07, 10.08, 'schedule', "
            "'standard', %s) RETURNING entry_id", (candidate_id, at(6)))
        entry_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO papersim.bankroll_events (event_key, occurred_at, currency, "
            "amount, reason, entry_id, scan_id) VALUES (%s, %s, 'USD', -260.08, "
            "'SCAN_ENTRY', %s, %s)",
            (f'entry:{entry_id}:cash', at(6), entry_id, scan_id))
    app.close()
    return {'scan_id': scan_id}


@NATIVE
def test_native_full_catalog_applies_from_an_empty_face(tmp_path):
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    # FX-26: applies the frozen four-entry pre-FX26 tree; the live
    # five-entry apply is proven in tests/test_papersim_fx26_migrations.py.
    frozen_tree = write_frozen_fx26_catalog(tmp_path / 'frozen', repo_root=ROOT)
    result = pm.apply_pending_migrations(owner, frozen_tree)
    # Fresh-apply inventory count 3 -> 4 migrations (M3/V2 option 1).
    assert result == {'applied': 4, 'pending_before': 4}
    assert _ledger(owner) == [(name, digest) for name, digest, _ in
                              pm.papersim_migration_catalog(frozen_tree)]
    assert len(_papersim_tables(owner)) == 13
    with owner.cursor() as cursor:
        # Independent assertion: exactly the three designated reporting
        # views exist in the papersim schema (M3/V2 option 1).
        cursor.execute("SELECT table_name FROM information_schema.views "
                       "WHERE table_schema = 'papersim' ORDER BY table_name")
        assert [row[0] for row in cursor.fetchall()] == list(PAPERSIM_VIEWS)
        cursor.execute("SELECT column_name, is_nullable FROM information_schema.columns "
                       "WHERE table_schema = 'papersim' AND table_name = 'scan_runs' "
                       "AND column_name = 'operation_key'")
        assert cursor.fetchone() == ('operation_key', 'YES')
        cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'papersim' "
                       "AND indexname = 'scan_runs_operation_key_idx'")
        assert 'UNIQUE' in cursor.fetchone()[0]
    owner.close()


@NATIVE
def test_native_upgrade_from_the_m1_nine_table_face_preserves_m1_rows(tmp_path):
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    m1_tree = write_frozen_m1_catalog(tmp_path / 'm1', repo_root=ROOT)
    assert pm.apply_pending_migrations(owner, m1_tree) == {'applied': 2, 'pending_before': 2}
    assert len(_papersim_tables(owner)) == 9
    owner.commit()
    app_dsn = owner_dsn.replace('user=pal_owner', 'user=pal_app')
    seeded = _seed_one_m1_scan_row(app_dsn)
    entry_id = None
    app = _connect(app_dsn)
    with app.cursor() as cursor:
        cursor.execute('SELECT entry_id FROM papersim.entries')
        entry_id = cursor.fetchone()[0]
    app.close()
    owner = _connect(owner_dsn, autocommit=False)
    # FX-26: the upgrade target is the frozen four-entry pre-FX26 tree;
    # the live five-entry upgrade is proven in tests/test_papersim_fx26_
    # migrations.py.
    frozen_tree = write_frozen_fx26_catalog(tmp_path / 'frozen', repo_root=ROOT)
    result = pm.apply_pending_migrations(owner, frozen_tree)
    # From the frozen M1 pair the full catalog now adds exactly the two
    # post-M1 migrations (M2 operations + the M3/V2 reporting views).
    assert result == {'applied': 2, 'pending_before': 2}
    assert set(M2_TABLES) <= set(_papersim_tables(owner))
    owner.commit()
    owner.close()
    app = _connect(app_dsn)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.scan_runs')
        assert cursor.fetchone()[0] == 1
        cursor.execute('SELECT operation_key FROM papersim.scan_runs WHERE scan_id = %s',
                       (seeded['scan_id'],))
        assert cursor.fetchone()[0] is None  # M1 rows keep NULL operation_key
        cursor.execute('SELECT stake, entry_fee FROM papersim.entries WHERE entry_id = %s',
                       (entry_id,))
        assert cursor.fetchone() == (Decimal('250'), Decimal('10.08'))
        cursor.execute('SELECT coalesce(sum(amount), 0) FROM papersim.bankroll_events')
        assert cursor.fetchone()[0] == Decimal('-260.08')  # cash events survive the upgrade
        for table in M2_TABLES:
            cursor.execute(f'SELECT count(*) FROM papersim.{table}')
            assert cursor.fetchone()[0] == 0
    app.close()


@NATIVE
def test_native_m2_drift_stops_and_rolls_back_the_receipt(tmp_path):
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    m1_tree = write_frozen_m1_catalog(tmp_path / 'm1', repo_root=ROOT)
    pm.apply_pending_migrations(owner, m1_tree)
    owner.commit()
    drift = _connect(owner_dsn)
    with drift.cursor() as cursor:
        cursor.execute('CREATE TABLE papersim.command_run_events (x int)')
    drift.close()
    owner = _connect(owner_dsn, autocommit=False)
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_apply_failed'):
        pm.apply_pending_migrations(owner, ROOT)
    assert len(_ledger(owner)) == 2
    assert 'bankroll_checks' not in _papersim_tables(owner)
    owner.close()


@NATIVE
def test_native_new_tables_reject_mutation_and_duplicate_keys(tmp_path):
    psycopg = pytest.importorskip('psycopg')
    from polymarket_alpha_lab.papersim import store
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    pm.apply_pending_migrations(owner, ROOT)
    owner.commit()
    app_dsn = owner_dsn.replace('user=pal_owner', 'user=pal_app')
    app, result, _ = _seed_one_scan(app_dsn)
    candidate_id = result['candidate_ids'][next(iter(result['candidate_ids']))]
    app.close()
    owner = _connect(owner_dsn)
    when = at(60)
    run_key = store.command_run_event_key('op-guard', 1, 'SUCCEEDED')
    control_key = store.queue_control_key('crypto_eth', 'PAUSE', 'fx17_rule', 'v1', when)
    check_key = store.bankroll_check_key(when, 'c' * 40, 0)
    mark_key = store.candidate_mark_key(candidate_id, 'market_slug', 'guard', 'YES',
                                        'clob_book', when)
    seeds = {
        'command_run_events': (
            'INSERT INTO papersim.command_run_events (run_event_key, operation_key, attempt, '
            'phase, command, queue_id, occurred_at, code_commit) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            (run_key, 'op-guard', 1, 'SUCCEEDED', 'run-scan', 'crypto_eth', when, 'c' * 40)),
        'queue_control_events': (
            'INSERT INTO papersim.queue_control_events (control_key, queue_id, control_kind, '
            'rule, rule_version, metrics, observed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
            (control_key, 'crypto_eth', 'PAUSE', 'fx17_rule', 'v1', '{"settled": 10}', when)),
        'bankroll_checks': (
            'INSERT INTO papersim.bankroll_checks (check_key, as_of, event_watermark, '
            'cash_total, deployed_total, initial_and_adjustments, realized_net, '
            'open_entry_fees, identity_difference, tolerance, passed, code_commit) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (check_key, when, 0, 0, 0, 0, 0, 0, 0, 0.005, True, 'c' * 40)),
        'candidate_marks': (
            'INSERT INTO papersim.candidate_marks (candidate_mark_key, candidate_id, '
            'sampling_kind, observed_at, market_ref_kind, market_ref, side, mid, source_kind) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (mark_key, candidate_id, 'PERIODIC', when, 'market_slug', 'guard', 'YES', 0.55,
             'clob_book')),
    }
    with owner.cursor() as cursor:
        for statement, params in seeds.values():
            cursor.execute(statement, params)
            with pytest.raises(psycopg.errors.UniqueViolation):
                cursor.execute(statement, params)
        # One CLOSE per candidate: a second CLOSE row is rejected even with a
        # fresh key.
        close_key = store.candidate_mark_key(candidate_id, 'market_slug', 'guard', 'YES',
                                             'clob_close', when)
        cursor.execute('INSERT INTO papersim.candidate_marks (candidate_mark_key, candidate_id, '
                       'sampling_kind, observed_at, market_ref_kind, market_ref, side, last, '
                       'source_kind) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (close_key, candidate_id, 'CLOSE', when, 'market_slug', 'guard', 'YES',
                        1, 'clob_close'))
        second_close = store.candidate_mark_key(candidate_id, 'market_slug', 'guard', 'YES',
                                                'clob_close', at(61))
        with pytest.raises(psycopg.errors.UniqueViolation):
            cursor.execute('INSERT INTO papersim.candidate_marks (candidate_mark_key, '
                           'candidate_id, sampling_kind, observed_at, market_ref_kind, '
                           'market_ref, side, last, source_kind) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (second_close, candidate_id, 'CLOSE', at(61), 'market_slug',
                            'guard', 'YES', 0, 'clob_close'))
        # scan_runs.operation_key: duplicate values rejected, NULLs coexist.
        cursor.execute('INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, team, '
                       'started_at, finished_at, code_commit, schema_version, gate_version, '
                       'operator, params, operation_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'
                       '%s::jsonb,%s)',
                       ('op-scan-1', 'd' * 64, 'crypto_eth', when, when, 'c' * 40, 1, 'g1',
                        'unattended', '{}', 'op-A'))
        with pytest.raises(psycopg.errors.UniqueViolation):
            cursor.execute('INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, '
                           'team, started_at, finished_at, code_commit, schema_version, '
                           'gate_version, operator, params, operation_key) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)',
                           ('op-scan-2', 'd' * 64, 'crypto_eth', when, when, 'c' * 40, 1,
                            'g1', 'unattended', '{}', 'op-A'))
        cursor.execute('INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, team, '
                       'started_at, finished_at, code_commit, schema_version, gate_version, '
                       'operator, params, operation_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'
                       '%s::jsonb,NULL)',
                       ('op-scan-3', 'd' * 64, 'crypto_eth', when, when, 'c' * 40, 1, 'g1',
                        'unattended', '{}'))
        # Append protection on every new table (M1 guard pattern).
        mutable_column = {'command_run_events': 'error_code',
                          'candidate_marks': 'source_ref',
                          'queue_control_events': 'reason',
                          'bankroll_checks': 'queue_id'}
        for table in M2_TABLES:
            column = mutable_column[table]
            for statement in (f'UPDATE papersim.{table} SET {column} = {column}',
                              f'DELETE FROM papersim.{table}',
                              f'TRUNCATE papersim.{table}'):
                with pytest.raises(psycopg.errors.DatabaseError,
                                   match='papersim_mutation_rejected'):
                    cursor.execute(statement)
    owner.close()
