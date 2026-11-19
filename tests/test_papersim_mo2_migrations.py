"""MO-2 full papersim catalog proofs (2026-11-19 audit-optimizer 05
node MO-2; 04 section 9).

Offline: the CURRENT manifest lists exactly six migrations -- the
frozen five-entry FX-26-era history (byte-identical on its own frozen
tree, proven with unchanged assertions in
``tests/test_papersim_fx26_migrations.py``) plus the MO-2
model-registry migration -- the directory glob matches, every entry
hash matches the file bytes, wrappers strip and forbidden tokens stay
out. Native (opt-in disposable cluster): full apply from an empty face
(applied 6), upgrade from the frozen five-entry face (applied exactly
1, the FX-26-era rows preserved), and the six-table contract: columns,
the database-side canonical-digest CHECK (convert_to 'UTF8'), the
CANDIDATE forward-plan CHECK, the jsonb array CHECKs, the UNFREEZE
actor CHECK and the append-only triggers with pal_app grants.
"""

from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from tests.papersim_mo2_catalog import MO2_FROZEN_CATALOG, write_frozen_mo2_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / 'database/papersim/migrations'
MANIFEST_PATH = ROOT / 'database/papersim/migrations.lock.json'
MO2_MIGRATION = ('20261119210907_papersim_model_registry.sql',
                 '2547b93d10ffe103e5c3fb9ec457c6e95c8336eb87674f164cdc96bbe17dc416')
FULL_CATALOG = MO2_FROZEN_CATALOG + (MO2_MIGRATION,)
MO2_TABLES = ('optimization_runs', 'model_specs', 'promotion_decisions',
              'model_role_events', 'shadow_predictions',
              'optimizer_control_events')
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim migration proof is opt-in')


# ---------------------------------------------------------------------------
# Offline current-catalog contract
# ---------------------------------------------------------------------------

def test_live_manifest_lists_the_frozen_five_history_plus_the_mo2_entry():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    assert [(entry['name'], entry['sha256']) for entry in manifest['migrations']] \
        == list(FULL_CATALOG)
    assert [name for name, _ in FULL_CATALOG] == sorted(path.name for path
                                                        in CATALOG_DIR.glob('*.sql'))
    assert all(entry['transaction_wrapper'] is True for entry in manifest['migrations'])


def test_live_catalog_hashes_match_file_bytes_and_strip_wrappers():
    catalog = pm.papersim_migration_catalog(ROOT)
    # Current full-catalog inventory count 5 -> 6 (MO-2: the sixth
    # managed migration 20261119210907_papersim_model_registry.sql).
    assert len(catalog) == 6
    for (name, digest, body), (expected_name, expected_digest) in zip(catalog, FULL_CATALOG,
                                                                      strict=True):
        assert name == expected_name
        assert digest == expected_digest
        assert digest == sha256((CATALOG_DIR / name).read_bytes()).hexdigest()
        assert 'begin;' not in body.lower() and 'commit;' not in body.lower()
        assert not pm.FORBIDDEN_BODY_TOKENS.search(body)


def test_mo2_migration_carries_no_url_or_dsn_tokens():
    text = (CATALOG_DIR / MO2_MIGRATION[0]).read_text(encoding='utf-8').lower()
    for token in ('postgresql://', 'postgres://', 'http://', 'https://', '://',
                  'database_url', 'sqlite:', '.jsonl'):
        assert token not in text, (MO2_MIGRATION[0], token)


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
    name = 'pal_papersim_mo2_' + uuid.uuid4().hex[:10]
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


def _migrated_database():
    """A fully migrated database plus its pal_app DSN."""
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    assert pm.apply_pending_migrations(owner, ROOT) == {'applied': 6, 'pending_before': 6}
    owner.commit()
    owner.close()
    return owner_dsn, owner_dsn.replace('user=pal_owner', 'user=pal_app')


@NATIVE
def test_native_full_catalog_applies_from_an_empty_face():
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    result = pm.apply_pending_migrations(owner, ROOT)
    # Current fresh-apply inventory count 5 -> 6 migrations (MO-2).
    assert result == {'applied': 6, 'pending_before': 6}
    assert _ledger(owner) == [(name, digest) for name, digest, _ in
                              pm.papersim_migration_catalog(ROOT)]
    owner.close()


@NATIVE
def test_native_upgrade_from_the_frozen_five_entry_face(tmp_path):
    owner_dsn = _fresh_managed_database()
    owner = _connect(owner_dsn, autocommit=False)
    frozen_tree = write_frozen_mo2_catalog(tmp_path / 'frozen')
    assert pm.apply_pending_migrations(owner, frozen_tree) == {'applied': 5,
                                                               'pending_before': 5}
    owner.commit()
    owner.close()
    owner = _connect(owner_dsn, autocommit=False)
    result = pm.apply_pending_migrations(owner, ROOT)
    # From the frozen five-entry face the current catalog adds exactly
    # the MO-2 migration, nothing else.
    assert result == {'applied': 1, 'pending_before': 1}
    owner.commit()
    owner.close()
    with _connect(owner_dsn).cursor() as cursor:
        cursor.execute("SELECT count(*) FROM papersim_private.migrations")
        assert cursor.fetchone()[0] == 6
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'papersim' "
                       "AND tablename = ANY(%s) ORDER BY tablename", (list(MO2_TABLES),))
        assert [row[0] for row in cursor.fetchall()] == sorted(MO2_TABLES)


@NATIVE
def test_native_model_specs_digest_check_rejects_forged_pairs():
    psycopg = pytest.importorskip('psycopg')
    owner_dsn, app_dsn = _migrated_database()
    app = _connect(app_dsn)
    forged_text = '{"family":"x"}'
    forged_digest = sha256(b'different bytes entirely').hexdigest()
    with pytest.raises(psycopg.errors.CheckViolation):
        with app.cursor() as cursor:
            cursor.execute(
                "INSERT INTO papersim.model_specs (spec_sha256, canonical_text, family, "
                "procedure_version, model_version, params, envelope, data_contract, "
                "source, code_commit) VALUES (%s, %s, 'f', 'p', 'm', '{}'::jsonb, "
                "'[]'::jsonb, 'd', 'manual', %s)",
                (forged_digest, forged_text, 'a' * 40))
    # The UTF-8 derivation matches the Python side byte for byte: the
    # same text with its true digest lands.
    true_text = '{"k":"v"}'
    with app.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.model_specs (spec_sha256, canonical_text, family, "
            "procedure_version, model_version, params, envelope, data_contract, "
            "source, code_commit) VALUES (%s, %s, 'f', 'p', 'm', '{}'::jsonb, "
            "'[]'::jsonb, 'd', 'manual', %s)",
            (sha256(true_text.encode('utf-8')).hexdigest(), true_text, 'a' * 40))
    app.close()


@NATIVE
def test_native_candidate_registration_requires_forward_plan():
    psycopg = pytest.importorskip('psycopg')
    owner_dsn, app_dsn = _migrated_database()
    app = _connect(app_dsn)
    stamp = datetime(2026, 11, 19, 21, 30, tzinfo=UTC)
    digest = sha256(b'baseline spec').hexdigest()
    commit = 'b' * 40
    with app.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.model_specs (spec_sha256, canonical_text, family, "
            "procedure_version, model_version, params, envelope, data_contract, "
            "source, code_commit) VALUES (%s, %s, 'f', 'p', 'm', '{}'::jsonb, "
            "'[]'::jsonb, 'd', 'frozen_baseline', %s)",
            (digest, 'baseline spec', commit))
        for role in ('BASELINE', 'ACTIVE'):
            cursor.execute(
                "INSERT INTO papersim.model_role_events (role_event_key, lineage, "
                "spec_sha256, role, effective_at, reason, actor, code_commit) "
                "VALUES (%s, 'f/over_under', %s, %s, %s, 'init', 'op', %s)",
                (f'init-{role}', digest, role, stamp, commit))
        # A CANDIDATE row without its forward plan is rejected by the CHECK.
        with pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(
                "INSERT INTO papersim.model_role_events (role_event_key, lineage, "
                "spec_sha256, role, effective_at, reason, actor, code_commit) "
                "VALUES (%s, 'f/over_under', %s, 'CANDIDATE', %s, 'reg', 'op', %s)",
                ('cand-noplan', sha256(b'candidate spec').hexdigest(), stamp, commit))
    app.close()


@NATIVE
def test_native_unfreeze_actor_check_and_scope_contract():
    psycopg = pytest.importorskip('psycopg')
    owner_dsn, app_dsn = _migrated_database()
    app = _connect(app_dsn)
    stamp = datetime(2026, 11, 19, 21, 40, tzinfo=UTC)
    with app.cursor() as cursor:
        # An automatic actor may freeze but NEVER unfreeze (04 section 9).
        cursor.execute(
            "INSERT INTO papersim.optimizer_control_events (optimizer_control_key, "
            "scope, control_kind, reason, actor, occurred_at) VALUES (%s, 'GLOBAL', "
            "'FREEZE', 'r', 'auto:v1', %s)", ('fz1', stamp))
        with pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(
                "INSERT INTO papersim.optimizer_control_events (optimizer_control_key, "
                "scope, control_kind, reason, actor, occurred_at) VALUES (%s, 'GLOBAL', "
                "'UNFREEZE', 'r', 'auto:v1', %s)", ('uz1', stamp))
        # Scope and lineage must agree exactly.
        with pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(
                "INSERT INTO papersim.optimizer_control_events (optimizer_control_key, "
                "scope, lineage, control_kind, reason, actor, occurred_at) "
                "VALUES (%s, 'GLOBAL', 'f/over_under', 'FREEZE', 'r', 'op', %s)",
                ('fz2', stamp))
        with pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(
                "INSERT INTO papersim.optimizer_control_events (optimizer_control_key, "
                "scope, control_kind, reason, actor, occurred_at) VALUES (%s, "
                "'LINEAGE', 'FREEZE', 'r', 'op', %s)", ('fz3', stamp))
        cursor.execute(
            "INSERT INTO papersim.optimizer_control_events (optimizer_control_key, "
            "scope, lineage, control_kind, reason, actor, occurred_at) "
            "VALUES (%s, 'LINEAGE', 'f/over_under', 'UNFREEZE', 'r', 'owner', %s)",
            ('uz2', stamp))
    app.close()


@NATIVE
def test_native_array_checks_outcome_tokens_and_truncate_guards():
    psycopg = pytest.importorskip('psycopg')
    owner_dsn, app_dsn = _migrated_database()
    app = _connect(app_dsn)  # autocommit: failed CHECKs cannot poison it
    stamp = datetime(2026, 11, 19, 21, 50, tzinfo=UTC)
    commit = 'c' * 40
    base = ("INSERT INTO papersim.optimization_runs (optimization_key, family, "
            "lineages, data_cutoff, rule_version, random_seed, trial_count, "
            "active_specs, outcome, code_commit) VALUES (%s, 'f', %s, %s, 'rv', 1, "
            "0, '{}'::jsonb, %s, %s)")
    with app.cursor() as cursor:
        # lineages/snapshot_refs must be jsonb ARRAYS (plan review P3-7).
        with pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(base, ('r1', '"not-an-array"', stamp, 'RUN_FAILED', commit))
        # outcome is the exact four-token closed set.
        with pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(base, ('r2', '["f/over_under"]', stamp, 'IMPROVED', commit))
        cursor.execute(base, ('r3', '["f/over_under"]', stamp, 'RUN_FAILED', commit))
    app.close()
    # UPDATE is rejected by the immutable trigger (owner session: pal_app
    # would be stopped by privileges before the trigger could speak).
    owner = _connect(owner_dsn)
    with pytest.raises(psycopg.errors.DatabaseError,
                       match='papersim_mutation_rejected'):
        with owner.cursor() as cursor:
            cursor.execute("UPDATE papersim.optimization_runs SET family = 'g' "
                           "WHERE optimization_key = 'r3'")
    owner.close()
    # TRUNCATE never succeeds on any MO-2 table. Two barriers may
    # speak, in server order: a table REFERENCED by a foreign key is
    # stopped by the engine itself ("cannot truncate a table referenced
    # in a foreign key constraint", model_specs -> optimization_runs
    # etc.) and the remaining tables are stopped by the no_truncate
    # statement trigger. Either way the row counts are unchanged.
    owner = _connect(owner_dsn)
    for table in MO2_TABLES:
        with owner.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM papersim.{table}')
            before = cursor.fetchone()[0]
        with pytest.raises(psycopg.errors.DatabaseError) as caught:
            with owner.cursor() as cursor:
                cursor.execute(f'TRUNCATE papersim.{table}')
        message = str(caught.value)
        assert ('papersim_mutation_rejected' in message
                or 'cannot truncate a table referenced' in message), message
        with owner.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM papersim.{table}')
            assert cursor.fetchone()[0] == before
    owner.close()
