"""FX-26 full papersim catalog proofs (2026-11-19 fix-list FX-26 / N-1).

Offline: the CURRENT manifest lists exactly five migrations -- the frozen
four-entry pre-FX26 history (byte-identical on its own frozen tree,
proven with unchanged assertions in ``tests/test_papersim_m2_migrations.py``)
plus the FX-26 candidate event-keys migration -- the directory glob
matches, every entry hash matches the file bytes, wrappers strip and
forbidden tokens stay out. Native (opt-in disposable cluster): full
apply from an empty face (applied 5), upgrade from the frozen four-entry
face with the pre-FX26 row preserved at NULL event keys, and the new
columns / index / CHECK contract on ``papersim.candidates``.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from tests.papersim_fx26_catalog import FX26_FROZEN_CATALOG, write_frozen_fx26_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / 'database' / 'papersim' / 'migrations'
MANIFEST_PATH = ROOT / 'database' / 'papersim' / 'migrations.lock.json'
FX26_MIGRATION = ('20261119173000_papersim_candidate_event_keys.sql',
                  'e29507cec4a5199d85877a33003b487a8c7bc917c42fc561607dfe5419ca45ce')
FULL_CATALOG = FX26_FROZEN_CATALOG + (FX26_MIGRATION,)
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim migration proof is opt-in')


# ---------------------------------------------------------------------------
# Offline current-catalog contract
# ---------------------------------------------------------------------------

def test_live_manifest_lists_the_frozen_four_history_plus_the_fx26_entry():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    assert [(entry['name'], entry['sha256']) for entry in manifest['migrations']] \
        == list(FULL_CATALOG)
    assert [name for name, _ in FULL_CATALOG] == sorted(path.name for path
                                                        in CATALOG_DIR.glob('*.sql'))
    assert all(entry['transaction_wrapper'] is True for entry in manifest['migrations'])


def test_live_catalog_hashes_match_file_bytes_and_strip_wrappers():
    catalog = pm.papersim_migration_catalog(ROOT)
    # Current full-catalog inventory count 4 -> 5 (FX-26: the fifth
    # managed migration 20261119173000_papersim_candidate_event_keys.sql).
    assert len(catalog) == 5
    for (name, digest, body), (expected_name, expected_digest) in zip(catalog, FULL_CATALOG,
                                                                      strict=True):
        assert name == expected_name
        assert digest == expected_digest
        assert digest == sha256((CATALOG_DIR / name).read_bytes()).hexdigest()
        assert 'begin;' not in body.lower() and 'commit;' not in body.lower()
        assert not pm.FORBIDDEN_BODY_TOKENS.search(body)


def test_fx26_migration_carries_no_url_or_dsn_tokens():
    text = (CATALOG_DIR / FX26_MIGRATION[0]).read_text(encoding='utf-8').lower()
    for token in ('postgresql://', 'postgres://', 'http://', 'https://', '://',
                  'database_url', 'sqlite:', '.jsonl'):
        assert token not in text, (FX26_MIGRATION[0], token)


# ---------------------------------------------------------------------------
# Native proofs (opt-in, disposable clusters only)
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
    name = 'pal_papersim_fx26_' + uuid.uuid4().hex[:10]
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


def _candidate_columns(owner):
    with owner.cursor() as cursor:
        cursor.execute("SELECT column_name FROM information_schema.columns "
                       "WHERE table_schema = 'papersim' AND table_name = 'candidates' "
                       "ORDER BY column_name")
        return [row[0] for row in cursor.fetchall()]


def _seed_one_pre_fx26_scan(app_dsn):
    """One minimal scan WITHOUT event keys, seeded via pre-FX26 column SQL.

    The store's candidate INSERT grew the FX-26 event-key columns, which
    the frozen four-entry face under test does not have; seeding with the
    pre-FX26 column set directly keeps the seed schema-accurate.
    """
    app = _connect(app_dsn)
    stamp = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    with app.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, team, "
            "started_at, finished_at, code_commit, schema_version, gate_version, "
            "operator, params) VALUES (%s, %s, 'crypto_eth', %s, %s, %s, 1, 'g1', "
            "'unattended', '{\"seed\": true}'::jsonb) RETURNING scan_id",
            ('fx26-seed-' + uuid.uuid4().hex[:10], 'e' * 64, stamp,
             stamp + timedelta(minutes=6), 'c' * 40))
        scan_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO papersim.candidates (scan_id, candidate_key, side, "
            "market_kind, team, model_name, model_version, input_payload, "
            "conclusion, gate_report, evaluated_at, market_slug) VALUES "
            "(%s, %s, 'YES', 'terminal', 'crypto_eth', 'gbm', 'r19', '{}'::jsonb, "
            "'REJECTED', '{}'::jsonb, %s, %s) RETURNING candidate_id",
            (scan_id, 'pre-fx26-1', stamp, 'mkt-' + uuid.uuid4().hex[:8]))
        candidate_id = cursor.fetchone()[0]
    app.close()
    return {'scan_id': scan_id, 'candidate_ids': {'pre-fx26-1': candidate_id}}


@NATIVE
def test_native_full_catalog_applies_from_an_empty_face():
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    result = pm.apply_pending_migrations(owner, ROOT)
    # Current fresh-apply inventory count 4 -> 5 migrations (FX-26).
    assert result == {'applied': 5, 'pending_before': 5}
    assert _ledger(owner) == [(name, digest) for name, digest, _ in
                              pm.papersim_migration_catalog(ROOT)]
    owner.close()


@NATIVE
def test_native_upgrade_from_the_frozen_four_entry_face_preserves_rows(tmp_path):
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    frozen_tree = write_frozen_fx26_catalog(tmp_path / 'frozen')
    assert pm.apply_pending_migrations(owner, frozen_tree) == {'applied': 4,
                                                               'pending_before': 4}
    owner.commit()
    owner.close()
    app_dsn = owner_dsn.replace('user=pal_owner', 'user=pal_app')
    written = _seed_one_pre_fx26_scan(app_dsn)
    owner = _connect(owner_dsn, autocommit=False)
    result = pm.apply_pending_migrations(owner, ROOT)
    # From the frozen four-entry face the current catalog adds exactly the
    # FX-26 migration, nothing else.
    assert result == {'applied': 1, 'pending_before': 1}
    owner.commit()
    owner.close()
    from polymarket_alpha_lab.papersim import store_psycopg
    app = store_psycopg.connect_explicit(app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    candidate_id = next(iter(written['candidate_ids'].values()))
    with app.cursor() as cursor:
        cursor.execute('SELECT event_key, event_start_at FROM papersim.candidates '
                       'WHERE candidate_id = %s', (candidate_id,))
        assert cursor.fetchone() == (None, None)  # pre-FX26 rows keep NULL keys
    app.close()


@NATIVE
def test_native_fx26_columns_and_index_match_the_contract(tmp_path):
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    frozen_tree = write_frozen_fx26_catalog(tmp_path / 'frozen')
    pm.apply_pending_migrations(owner, frozen_tree)
    owner.commit()
    before = _candidate_columns(owner)
    assert 'event_key' not in before and 'event_start_at' not in before
    pm.apply_pending_migrations(owner, ROOT)
    owner.commit()
    after = _candidate_columns(owner)
    # Exactly the two new columns appear; nothing else changes.
    assert sorted(set(after) - set(before)) == ['event_key', 'event_start_at']
    assert len(after) == len(before) + 2
    with owner.cursor() as cursor:
        cursor.execute("SELECT column_name, data_type, is_nullable "
                       "FROM information_schema.columns "
                       "WHERE table_schema = 'papersim' AND table_name = 'candidates' "
                       "AND column_name IN ('event_key', 'event_start_at') "
                       "ORDER BY column_name")
        assert cursor.fetchall() == [('event_key', 'text', 'YES'),
                                     ('event_start_at', 'timestamp with time zone', 'YES')]
        cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'papersim' "
                       "AND indexname = 'candidates_event_key_idx'")
        indexdef = cursor.fetchone()[0]
        assert 'event_key' in indexdef and 'UNIQUE' not in indexdef
    owner.close()


@NATIVE
def test_native_fx26_checks_reject_blank_and_nonfinite_values():
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn)
    pm.apply_pending_migrations(owner, ROOT)
    owner.close()
    app_dsn = owner_dsn.replace('user=pal_owner', 'user=pal_app')
    _seed_one_pre_fx26_scan(app_dsn)  # NULL keys insert fine (pre-FX26 columns)
    app = _connect(app_dsn)
    with app.cursor() as cursor:
        cursor.execute('SELECT scan_id FROM papersim.scan_runs ORDER BY scan_id LIMIT 1')
        scan_id = cursor.fetchone()[0]
    stamp = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    insert = ('INSERT INTO papersim.candidates (scan_id, candidate_key, side, market_kind, '
              'team, model_name, model_version, input_payload, conclusion, gate_report, '
              'evaluated_at, event_key, event_start_at) VALUES '
              '(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)')
    row = ('YES', 'terminal', 'crypto_eth', 'gbm', 'r19', '{}', 'REJECTED', '{}', stamp)
    psycopg = pytest.importorskip('psycopg')
    with pytest.raises(psycopg.errors.CheckViolation):
        with app.cursor() as cursor:
            cursor.execute(insert, (scan_id, 'blank-key', *row, '', None))
    with pytest.raises(psycopg.errors.CheckViolation):
        with app.cursor() as cursor:
            cursor.execute(insert, (scan_id, 'infinite-key', *row, 'gamma:1',
                                    'infinity'))
    with app.cursor() as cursor:
        cursor.execute(insert, (scan_id, 'null-key', *row, None, None))
        cursor.execute("SELECT event_key, event_start_at FROM papersim.candidates "
                       "WHERE candidate_key = 'null-key'")
        assert cursor.fetchone() == (None, None)
    app.close()
