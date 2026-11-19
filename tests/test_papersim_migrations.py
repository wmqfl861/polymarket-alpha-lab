"""Papersim managed-migration catalog and applier proofs.

The pure part (offline) validates the catalog contract: lock manifest
shape, name/hash rules, wrapper stripping, forbidden tokens, inventory
equality, the hash-consistent-prefix rule and the dedicated advisory lock
key. The native part (opt-in, disposable clusters only) proves the apply
state machine on real PostgreSQL: fresh apply, bootstrap drift stop,
failure rollback, idempotent rerun, prefix conflict, lock serialization
and correction-by-new-migration. No test ever touches a business cluster:
databases are created inside the throwaway cluster exposed by the test
environment.

M1 migration history runs against the frozen TWO-entry M1 catalog
(``tests/papersim_m1_catalog.py``, M2 plan section 6.3) so the original
"two migrations, nine tables" assertions stay exact; the current full
manifest (every post-M1 migration, currently the M2 operations and the
M3/V2 reporting-views entries) is proven in
``tests/test_papersim_m2_migrations.py``.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from tests.papersim_m1_catalog import write_frozen_m1_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / 'database/papersim/migrations'
MANIFEST_PATH = ROOT / 'database/papersim/migrations.lock.json'
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim migration proof is opt-in')


# ---------------------------------------------------------------------------
# Pure catalog contract (offline)
# ---------------------------------------------------------------------------

def test_lock_key_and_manifest_identity_are_pinned():
    assert pm.PAPERSIM_LOCK_KEY == 73198643
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    assert manifest['format'] == 'papersim-migrations-v1'
    assert [entry['name'] for entry in manifest['migrations']] == sorted(
        path.name for path in CATALOG_DIR.glob('*.sql'))


def test_catalog_hashes_match_file_bytes_and_strip_wrappers(tmp_path):
    # M1 migration history keeps its frozen TWO-entry catalog (M2 plan
    # section 6.3); the current full manifest is proven independently in
    # tests/test_papersim_m2_migrations.py. The assertions are unchanged.
    tree = write_frozen_m1_catalog(tmp_path / 'repo')
    directory = tree / 'database/papersim/migrations'
    manifest = json.loads(
        (tree / 'database/papersim/migrations.lock.json').read_text(encoding='utf-8'))
    catalog = pm.papersim_migration_catalog(tree)
    assert len(catalog) == len(manifest['migrations']) == 2
    for (name, digest, body), entry in zip(catalog, manifest['migrations'], strict=True):
        assert digest == entry['sha256'] == sha256((directory / name).read_bytes()).hexdigest()
        assert entry['transaction_wrapper'] is True
        assert not pm.FORBIDDEN_BODY_TOKENS.search(body)
        assert 'begin;' not in body.lower() and 'commit;' not in body.lower()


def test_papersim_sql_files_carry_no_url_or_dsn_tokens():
    for path in sorted(CATALOG_DIR.glob('*.sql')):
        text = path.read_text(encoding='utf-8').lower()
        for token in ('postgresql://', 'postgres://', 'http://', 'https://', '://',
                      'database_url', 'sqlite:', '.jsonl'):
            assert token not in text, (path.name, token)


def _catalog_tree(tmp_path: Path, *, mutate=None):
    tree = tmp_path / 'repo'
    (tree / 'database/papersim/migrations').mkdir(parents=True)
    for path in sorted(CATALOG_DIR.glob('*.sql')):
        shutil.copyfile(path, tree / 'database/papersim/migrations' / path.name)
    if mutate is not None:
        mutate(tree)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    for entry in manifest['migrations']:
        entry['sha256'] = sha256((tree / 'database/papersim/migrations'
                                  / entry['name']).read_bytes()).hexdigest()
    (tree / 'database/papersim/migrations.lock.json').write_text(
        json.dumps(manifest, indent=2), encoding='utf-8')
    return tree


def test_changed_migration_bytes_stop_the_catalog(tmp_path):
    tree = _catalog_tree(tmp_path)

    def tamper(_tree):
        target = tree / 'database/papersim/migrations/20261115000100_papersim_v2_core.sql'
        target.write_text(target.read_text(encoding='utf-8') + '\n-- drift\n', encoding='utf-8')

    tamper(tree)  # mutate after manifest re-hash: bytes no longer match
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_changed'):
        pm.papersim_migration_catalog(tree)


def test_inventory_mismatch_stops_the_catalog(tmp_path):
    tree = _catalog_tree(tmp_path)
    (tree / 'database/papersim/migrations/20990101000000_extra.sql').write_text(
        'begin;\nselect 1;\ncommit;\n', encoding='utf-8')
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_inventory_mismatch'):
        pm.papersim_migration_catalog(tree)


def test_manifest_shape_rules_are_enforced(tmp_path):
    tree = tmp_path / 'repo'
    (tree / 'database/papersim/migrations').mkdir(parents=True)
    (tree / 'database/papersim/migrations/20261115000000_papersim_bootstrap.sql').write_text(
        'begin;\nselect 1;\ncommit;\n', encoding='utf-8')
    digest = sha256(b'begin;\nselect 1;\ncommit;\n').hexdigest()
    directory = tree / 'database/papersim/migrations'
    bad_manifests = (
        {'format': 'native-postgres-migrations-v1',
         'migrations': [{'name': '20261115000000_papersim_bootstrap.sql', 'sha256': digest,
                         'transaction_wrapper': True}]},
        {'format': pm.MANIFEST_FORMAT,
         'migrations': [{'name': 'bad_name.sql', 'sha256': digest, 'transaction_wrapper': True}]},
        {'format': pm.MANIFEST_FORMAT,
         'migrations': [{'name': '20261115000000_papersim_bootstrap.sql', 'sha256': 'xy' * 32,
                         'transaction_wrapper': True}]},
        {'format': pm.MANIFEST_FORMAT,
         'migrations': [{'name': '20261115000000_papersim_bootstrap.sql', 'sha256': digest,
                         'transaction_wrapper': 'yes'}]},
        {'format': pm.MANIFEST_FORMAT, 'migrations': []},
    )
    for manifest in bad_manifests:
        (tree / 'database/papersim/migrations.lock.json').write_text(
            json.dumps(manifest), encoding='utf-8')
        with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_manifest_invalid'):
            pm.papersim_migration_catalog(tree)


def test_wrapper_and_forbidden_body_rules(tmp_path):
    tree = tmp_path / 'repo'
    (tree / 'database/papersim/migrations').mkdir(parents=True)
    directory = tree / 'database/papersim/migrations'

    def write_manifest(body_bytes):
        name = '20261115000000_papersim_bootstrap.sql'
        directory.joinpath(name).write_bytes(body_bytes)
        (tree / 'database/papersim/migrations.lock.json').write_text(json.dumps(
            {'format': pm.MANIFEST_FORMAT,
             'migrations': [{'name': name, 'sha256': sha256(body_bytes).hexdigest(),
                             'transaction_wrapper': True}]}), encoding='utf-8')

    write_manifest(b'select 1;\n')  # no wrapper to strip
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_manifest_invalid'):
        pm.papersim_migration_catalog(tree)
    write_manifest(b'begin;\ncreate table t (x int);\ncommit;\nselect 2;\n')  # trailing junk
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_manifest_invalid'):
        pm.papersim_migration_catalog(tree)
    write_manifest(b'begin;\nrollback;\ncommit;\n')
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_manifest_invalid'):
        pm.papersim_migration_catalog(tree)
    write_manifest(b'begin;\n\\copy t from stdin\ncommit;\n')
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_manifest_invalid'):
        pm.papersim_migration_catalog(tree)


def test_pending_migrations_enforce_the_hash_consistent_prefix():
    catalog = pm.papersim_migration_catalog(ROOT)
    assert pm.papersim_pending_migrations(catalog, []) == catalog
    applied = [{'name': catalog[0][0], 'sha256': catalog[0][1]}]
    assert pm.papersim_pending_migrations(catalog, applied) == catalog[1:]
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_history_conflict'):
        pm.papersim_pending_migrations(catalog, [{'name': catalog[1][0], 'sha256': catalog[1][1]}])
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_history_conflict'):
        pm.papersim_pending_migrations(catalog, [{'name': catalog[0][0], 'sha256': 'f' * 64}])
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_history_conflict'):
        pm.papersim_pending_migrations(catalog, applied + [{'name': '20990101000000_x.sql',
                                                            'sha256': 'a' * 64}])


# ---------------------------------------------------------------------------
# Native applier proofs (disposable cluster only)
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
    name = 'pal_papersim_mig_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind_database(OWNER_DSN, name)
    app_dsn = owner_dsn.replace('user=pal_owner', 'user=pal_app')
    bootstrap = psycopg.connect(owner_dsn)
    bootstrap.autocommit = True
    with bootstrap.cursor() as cursor:
        from polymarket_alpha_lab.project_postgres import sql
        cursor.execute(sql.bootstrap_sql(uuid.uuid4().hex, 'b' * 64, system_id))
    bootstrap.close()
    return owner_dsn, app_dsn


def _ledger(owner):
    with owner.cursor() as cursor:
        cursor.execute("SELECT to_regclass('papersim_private.migrations') IS NULL")
        if cursor.fetchone()[0]:
            return []  # pre-bootstrap face: no ledger relation exists yet
        cursor.execute("SELECT name, sha256 FROM papersim_private.migrations ORDER BY name")
        return cursor.fetchall()


@NATIVE
def test_native_fresh_apply_writes_matching_receipts(tmp_path):
    # M1 history proof: the frozen two-entry catalog from empty (M2 plan
    # section 6.3); the current three-entry catalog has its own fresh-apply
    # proof in tests/test_papersim_m2_migrations.py.
    tree = write_frozen_m1_catalog(tmp_path / 'm1')
    owner_dsn, app_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    result = pm.apply_pending_migrations(owner, tree)
    assert result == {'applied': 2, 'pending_before': 2}
    catalog = pm.papersim_migration_catalog(tree)
    assert _ledger(owner) == [(name, digest) for name, digest, _ in catalog]
    status = pm.migration_status(owner, tree)
    assert status['applied'] == [name for name, _, _ in catalog] and status['pending'] == []
    with owner.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM information_schema.tables "
                       "WHERE table_schema = 'papersim'")
        assert cursor.fetchone()[0] == 9
    owner.close()
    app = _connect(app_dsn)
    with app.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM papersim.scan_runs")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT has_schema_privilege('pal_app', 'papersim_private', 'USAGE')")
        assert cursor.fetchone()[0] is False
    app.close()


@NATIVE
def test_native_bootstrap_fails_on_existing_objects(tmp_path):
    owner_dsn, _ = _fresh_managed_database()
    drift = _connect(owner_dsn)
    with drift.cursor() as cursor:
        cursor.execute('CREATE SCHEMA papersim')
    drift.close()
    owner = _connect(owner_dsn, autocommit=False)
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_apply_failed'):
        pm.apply_pending_migrations(owner, ROOT)
    assert _ledger(owner) == []  # no receipt survived the drift stop
    owner.close()


@NATIVE
def test_native_failed_migration_rolls_back_ddl_and_receipt(tmp_path):
    # M1 history proof on the frozen two-entry catalog (M2 plan section
    # 6.3): the failing migration sorts after the M1 pair, so the ledger
    # after the failure is exactly the frozen M1 prefix. Building from the
    # frozen tree (instead of copying the live directory) keeps the
    # committed-prefix assertion independent of later catalog growth.
    tree = write_frozen_m1_catalog(tmp_path / 'repo')
    failing = tree / 'database/papersim/migrations/20261115000200_papersim_fail.sql'
    failing.write_text('begin;\ncreate table papersim.boom (x int);\nselect 1 / 0;\ncommit;\n',
                       encoding='utf-8')
    entries = []
    for path in sorted((tree / 'database/papersim/migrations').glob('*.sql')):
        entries.append({'name': path.name, 'sha256': sha256(path.read_bytes()).hexdigest(),
                        'transaction_wrapper': True})
    (tree / 'database/papersim/migrations.lock.json').write_text(
        json.dumps({'format': pm.MANIFEST_FORMAT, 'migrations': entries}), encoding='utf-8')
    owner_dsn, _ = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_apply_failed'):
        pm.apply_pending_migrations(owner, tree)
    catalog = pm.papersim_migration_catalog(tree)
    catalog = tuple(row for row in catalog if row[0] != failing.name)
    assert _ledger(owner) == [(name, digest) for name, digest, _ in catalog]
    with owner.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM information_schema.tables "
                       "WHERE table_schema = 'papersim' AND table_name = 'boom'")
        assert cursor.fetchone()[0] == 0
    owner.close()


@NATIVE
def test_native_idempotent_rerun_and_prefix_conflict(tmp_path):
    tree = write_frozen_m1_catalog(tmp_path / 'm1')
    owner_dsn, _ = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    assert pm.apply_pending_migrations(owner, tree)['applied'] == 2
    assert pm.apply_pending_migrations(owner, tree) == {'applied': 0, 'pending_before': 0}
    with owner.cursor() as cursor:
        cursor.execute("INSERT INTO papersim_private.migrations(name, sha256) "
                       "VALUES ('20990101000000_fake.sql', %s)", ('a' * 64,))
    owner.commit()
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_history_conflict'):
        pm.apply_pending_migrations(owner, tree)
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_history_conflict'):
        pm.migration_status(owner, tree)
    owner.close()


@NATIVE
def test_native_concurrent_appliers_serialize_under_the_advisory_lock(tmp_path):
    psycopg = pytest.importorskip('psycopg')
    tree = write_frozen_m1_catalog(tmp_path / 'm1')
    owner_dsn, _ = _fresh_managed_database()
    blocker = psycopg.connect(owner_dsn)
    blocker.autocommit = True
    with blocker.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_lock(%s)', (pm.PAPERSIM_LOCK_KEY,))
    results = []

    def applier():
        owner = _connect(owner_dsn, autocommit=False)
        try:
            results.append(pm.apply_pending_migrations(owner, tree))
        except Exception as error:  # pragma: no cover - surfaced by assertions
            results.append(error)
        finally:
            owner.close()

    thread = threading.Thread(target=applier)
    thread.start()
    time.sleep(2.0)
    assert thread.is_alive(), 'the applier must block while the papersim lock is held'
    with blocker.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_unlock(%s)', (pm.PAPERSIM_LOCK_KEY,))
    thread.join(timeout=30)
    assert not thread.is_alive()
    blocker.close()
    assert results == [{'applied': 2, 'pending_before': 2}]
    owner = _connect(owner_dsn, autocommit=False)
    catalog = pm.papersim_migration_catalog(tree)
    assert _ledger(owner) == [(name, digest) for name, digest, _ in catalog]
    owner.close()


@NATIVE
def test_native_correction_arrives_as_a_new_migration_only(tmp_path):
    tree = write_frozen_m1_catalog(tmp_path / 'm1')
    owner_dsn, _ = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    assert pm.apply_pending_migrations(owner, tree)['applied'] == 2
    corrected = tree / 'database/papersim/migrations/20261115000300_papersim_correction.sql'
    corrected.write_text('begin;\ncreate table papersim.correction_marker (k int);\ncommit;\n',
                         encoding='utf-8')
    entries = [{'name': path.name, 'sha256': sha256(path.read_bytes()).hexdigest(),
                'transaction_wrapper': True}
               for path in sorted((tree / 'database/papersim/migrations').glob('*.sql'))]
    (tree / 'database/papersim/migrations.lock.json').write_text(
        json.dumps({'format': pm.MANIFEST_FORMAT, 'migrations': entries}), encoding='utf-8')
    assert pm.apply_pending_migrations(owner, tree) == {'applied': 1, 'pending_before': 1}
    with owner.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM papersim.correction_marker")
        assert cursor.fetchone()[0] == 0
    assert len(_ledger(owner)) == 3
    owner.close()


@NATIVE
def test_native_cli_applies_through_a_validated_socket_dsn(tmp_path):
    import subprocess
    import sys
    tree = write_frozen_m1_catalog(tmp_path / 'm1')
    owner_dsn, _ = _fresh_managed_database()
    completed = subprocess.run(
        [sys.executable, '-m', 'polymarket_alpha_lab.papersim.migrations', 'apply',
         '--repo-root', str(tree), '--dsn', owner_dsn],
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {'applied': 2, 'pending_before': 2}
    owner = _connect(owner_dsn, autocommit=False)
    assert len(_ledger(owner)) == 2
    owner.close()
