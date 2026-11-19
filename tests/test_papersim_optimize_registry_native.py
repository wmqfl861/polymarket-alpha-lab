"""Opt-in native MO-2 model-registry proofs (2026-11-19 audit-optimizer
05 node MO-2).

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local cluster).
Never touches a business cluster: every test builds its own private
managed-shaped database on the throwaway cluster. Proves: the
append-only triggers reject UPDATE/DELETE on all six tables; the
writer functions are idempotent (same key + same content returns the
original row, changed content is a conflict) across specs, runs,
decisions, role events and control events; registration and role
writes round-trip through the pure registry (initialize twice skips,
an initialization identity drift is a hard conflict, a handover batch
lands as its pair or not at all); the database-side canonical-digest
CHECK and the UNFREEZE actor CHECK back the application layers; a
fresh registry reads empty (all-baseline fallback premise) and the
stored spec rebuilds to its own primary key through the jsonb
round-trip (plan-review P3-4).
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
from pathlib import Path
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import store
from polymarket_alpha_lab.papersim.optimize.families.soccer_frozen import (
    SOCCER_FROZEN,
)
from polymarket_alpha_lab.papersim.optimize.registry import (
    RegistryInvariantError,
    derive_lineage_roles,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim proof is opt-in')

AT = datetime(2026, 11, 19, 21, 0, tzinfo=UTC)
LATER = AT + timedelta(hours=2)
COMMIT = 'd' * 40
LINEAGE = 'club-soccer-frozen/over_under'
BASELINE_SPEC = SOCCER_FROZEN.baseline_spec()
BASELINE = BASELINE_SPEC.spec_sha256()


def candidate_spec() -> ModelSpec:
    return ModelSpec(family='club-soccer-frozen', procedure_version='r23',
                     params={'kappa': 0.25}, envelope=({'name': 'confs',
                                                        'weight': 0.5},),
                     data_contract='understat-xg-snapshot-v1')


def _connect(dsn, *, autocommit=True):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _migrated_app() -> tuple[str, str]:
    """A fully migrated private database; returns (owner_dsn, app_dsn)."""
    psycopg = pytest.importorskip('psycopg')
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_mo2reg_' + uuid.uuid4().hex[:10]
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
    owner = _connect(owner_dsn, autocommit=False)
    assert pm.apply_pending_migrations(owner, ROOT) == {'applied': 6, 'pending_before': 6}
    owner.commit()
    owner.close()
    return owner_dsn, owner_dsn.replace('user=pal_owner', 'user=pal_app')


def _adapter():
    """The real adapter, imported at EXECUTION time only.

    The offline ledger proof injects a fake store_psycopg into
    sys.modules and depends on the real one never being imported in an
    offline process; collection-time imports would break it (the m2/
    fx26 native files follow the same function-level discipline).
    """

    return pytest.importorskip('polymarket_alpha_lab.papersim.store_psycopg')


def _app(dsn: str):
    return _adapter().connect_explicit(dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')


def _initialized(dsn: str):
    store_psycopg = _adapter()
    app = _app(dsn)
    report = store_psycopg.initialize_registry(
        app, families=[SOCCER_FROZEN], actor='op', code_commit=COMMIT, effective_at=AT)
    return app, report


MO2_TABLES = ('optimization_runs', 'model_specs', 'promotion_decisions',
              'model_role_events', 'shadow_predictions', 'optimizer_control_events')


@NATIVE
def test_native_append_only_triggers_reject_update_and_delete():
    store_psycopg = _adapter()
    owner_dsn, dsn = _migrated_app()
    app, _ = _initialized(dsn)
    psycopg = pytest.importorskip('psycopg')
    # Seed one row in every table that the registry init did not fill:
    # a run, a decision, a control event, and a shadow prediction
    # hanging on one freshly seeded candidate row.
    store_psycopg.record_optimization_run(app, store.OptimizationRunSubmission(
        family='club-soccer-frozen', data_cutoff=AT, lineages=[LINEAGE],
        rule_version='rules-v1', random_seed=1, trial_count=0,
        outcome='RUN_FAILED', code_commit=COMMIT))
    store_psycopg.record_promotion_decision(app, store.PromotionDecisionSubmission(
        lineage=LINEAGE, active_spec_sha256=BASELINE, rule_version='rules-v1',
        decided_by='op', decided_at=LATER, conclusion='HOLD'))
    store_psycopg.record_optimizer_control_event(
        app, store.OptimizerControlEventSubmission(
            scope='GLOBAL', control_kind='FREEZE', reason='seed', actor='op',
            occurred_at=AT))
    with app.transaction(), app.cursor() as cursor:
        cursor.execute("INSERT INTO papersim.scan_runs (scan_key, "
                       "scan_payload_sha256, team, started_at, finished_at, "
                       "code_commit, schema_version, gate_version, operator, params) "
                       "VALUES (%s, %s, 't', %s, %s, %s, 1, 'g', 'unattended', "
                       "'{}'::jsonb) RETURNING scan_id",
                       ('mo2-trig-' + uuid.uuid4().hex[:8], 'a' * 64, AT, AT, COMMIT))
        scan_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO papersim.candidates (scan_id, candidate_key, "
                       "side, market_kind, team, model_name, model_version, "
                       "input_payload, conclusion, gate_report, evaluated_at) "
                       "VALUES (%s, 'k', 'YES', 'terminal', 't', 'm', 'v', "
                       "'{}'::jsonb, 'REJECTED', '{}'::jsonb, %s) "
                       "RETURNING candidate_id", (scan_id, AT))
        candidate_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO papersim.shadow_predictions (shadow_key, "
                       "candidate_id, spec_sha256, role_at_prediction, gate_pass, "
                       "predicted_at) VALUES (%s, %s, %s, 'BASELINE', false, %s)",
                       ('mo2-trig-shadow', candidate_id, BASELINE, AT))
    app.close()
    # pal_app is stopped by PRIVILEGES before any trigger could fire
    # (select+insert only) -- prove the triggers themselves on an owner
    # session, where they are the only barrier (the m2 precedent).
    owner = _connect(owner_dsn)
    for table in MO2_TABLES:
        with owner.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM papersim.{table}')
            assert cursor.fetchone()[0] > 0, table
            with pytest.raises(psycopg.errors.DatabaseError,
                               match='papersim_mutation_rejected'):
                cursor.execute(f'UPDATE papersim.{table} SET paper_only = true')
            with pytest.raises(psycopg.errors.DatabaseError,
                               match='papersim_mutation_rejected'):
                cursor.execute(f'DELETE FROM papersim.{table}')
    owner.close()


@NATIVE
def test_native_register_model_spec_idempotent_and_roundtrips():
    store_psycopg = _adapter()
    _owner_dsn, dsn = _migrated_app()
    app = _app(dsn)
    first = store_psycopg.register_model_spec(app, BASELINE_SPEC,
                                              source='frozen_baseline',
                                              code_commit=COMMIT)
    assert first['inserted'] is True
    second = store_psycopg.register_model_spec(app, BASELINE_SPEC,
                                               source='frozen_baseline',
                                               code_commit=COMMIT)
    assert second['inserted'] is False
    assert second['spec_sha256'] == first['spec_sha256'] == BASELINE
    # Same digest, different content (a re-registered family alias) is
    # a conflict, never an update.
    with pytest.raises(store.PapersimStoreError):
        store_psycopg.register_model_spec(app, BASELINE_SPEC, source='manual',
                                          code_commit=COMMIT)
    # Library round-trip (plan-review P3-4): the row rebuilds into a
    # ModelSpec whose identity equals the primary key, byte for byte.
    rebuilt = store_psycopg.load_model_spec(app, BASELINE)
    assert rebuilt.spec_sha256() == BASELINE
    assert rebuilt.canonical_json() == BASELINE_SPEC.canonical_json()
    assert rebuilt.model_version() == BASELINE_SPEC.model_version()
    assert store_psycopg.load_model_spec(app, 'e' * 64) is None
    app.close()


@NATIVE
def test_native_optimization_run_and_decision_idempotency():
    store_psycopg = _adapter()
    _owner_dsn, dsn = _migrated_app()
    app, _ = _initialized(dsn)
    run = store.OptimizationRunSubmission(
        family='club-soccer-frozen', data_cutoff=AT, lineages=[LINEAGE],
        search_space={'grid': [1, 2]}, rule_version='rules-v1', random_seed=7,
        trial_count=3, active_specs={LINEAGE: BASELINE},
        outcome='CANDIDATE_REGISTERED', code_commit=COMMIT)
    first = store_psycopg.record_optimization_run(app, run)
    assert first['inserted'] is True
    again = store_psycopg.record_optimization_run(app, run)
    assert again['inserted'] is False
    assert again['optimization_id'] == first['optimization_id']
    changed = store.OptimizationRunSubmission(
        family='club-soccer-frozen', data_cutoff=AT, lineages=[LINEAGE],
        search_space={'grid': [1, 2]}, rule_version='rules-v1', random_seed=7,
        trial_count=9, active_specs={LINEAGE: BASELINE},
        outcome='CANDIDATE_REGISTERED', code_commit=COMMIT)
    with pytest.raises(store.PapersimStoreError):
        store_psycopg.record_optimization_run(app, changed)
    # active_specs digests are proven inside the transaction.
    with pytest.raises(store.PapersimStoreError):
        store_psycopg.record_optimization_run(app, store.OptimizationRunSubmission(
            family='club-soccer-frozen', data_cutoff=AT, lineages=[LINEAGE],
            rule_version='rules-v1', random_seed=8, trial_count=0,
            active_specs={LINEAGE: 'f' * 64}, outcome='RUN_FAILED',
            code_commit=COMMIT))
    decision = store.PromotionDecisionSubmission(
        lineage=LINEAGE, active_spec_sha256=BASELINE, rule_version='rules-v1',
        decided_by='auto:rules-v1', decided_at=LATER, conclusion='HOLD')
    assert store_psycopg.record_promotion_decision(app, decision)['inserted'] is True
    assert store_psycopg.record_promotion_decision(
        app, decision)['inserted'] is False
    app.close()


@NATIVE
def test_native_initialize_registry_is_idempotent_and_drift_is_conflict():
    store_psycopg = _adapter()
    _owner_dsn, dsn = _migrated_app()
    app, report = _initialized(dsn)
    assert set(report) == {'club-soccer-frozen/' + target
                           for target in SOCCER_FROZEN.targets()}
    assert all(row['state'] == 'initialized' for row in report.values())
    # Second run: every lineage already carries its BASELINE row with
    # the same digest -- all skipped, no new rows.
    report2 = store_psycopg.initialize_registry(
        app, families=[SOCCER_FROZEN], actor='op', code_commit=COMMIT, effective_at=AT)
    assert all(row['state'] == 'skipped' for row in report2.values())
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.model_specs')
        specs = cursor.fetchone()[0]
        cursor.execute('SELECT count(*) FROM papersim.model_role_events')
        events = cursor.fetchone()[0]
    # Identity drift (the family re-identified its baseline) is a hard
    # conflict: never silently rewritten.
    drifted = ModelSpec(family='club-soccer-frozen', procedure_version='r99',
                        params={}, envelope=BASELINE_SPEC.envelope,
                        data_contract=BASELINE_SPEC.data_contract)

    class DriftedFamily:
        family_id = SOCCER_FROZEN.family_id
        procedure_version = 'r99'

        def baseline_spec(self):
            return drifted

        def targets(self):
            return SOCCER_FROZEN.targets()

    with pytest.raises(store.PapersimStoreError):
        store_psycopg.initialize_registry(
            app, families=[DriftedFamily()], actor='op', code_commit=COMMIT,
            effective_at=AT)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.model_specs')
        assert cursor.fetchone()[0] == specs
        cursor.execute('SELECT count(*) FROM papersim.model_role_events')
        assert cursor.fetchone()[0] == events
    app.close()


@NATIVE
def test_native_initialized_registry_folds_to_baseline_active():
    store_psycopg = _adapter()
    _owner_dsn, dsn = _migrated_app()
    app, _ = _initialized(dsn)
    grouped = store_psycopg.load_all_role_events(app)
    assert set(grouped) == {'club-soccer-frozen/' + target
                            for target in SOCCER_FROZEN.targets()}
    for lineage, batches in grouped.items():
        view = derive_lineage_roles(batches)
        assert view.active == view.baseline == BASELINE
        assert view.candidates == () and view.guard is None
    per_lineage = store_psycopg.load_lineage_roles(app, LINEAGE)
    assert per_lineage.active == BASELINE
    app.close()


@NATIVE
def test_native_fresh_registry_reads_empty_for_baseline_fallback():
    store_psycopg = _adapter()
    _owner_dsn, dsn = _migrated_app()
    app = _app(dsn)
    # Empty registry: no events at all -- the load returns the
    # all-baseline fallback premise (04 section 11) and the family
    # baseline is immediately usable with today's identity.
    assert store_psycopg.load_all_role_events(app) == {}
    assert store_psycopg.load_lineage_roles(app, LINEAGE) is None
    fallback = SOCCER_FROZEN.baseline_spec()
    assert fallback.model_version() == 'soccer-r23'
    app.close()


@NATIVE
def test_native_handover_batch_is_atomic_pair_or_nothing():
    store_psycopg = _adapter()
    _owner_dsn, dsn = _migrated_app()
    app, _ = _initialized(dsn)
    candidate = candidate_spec()
    store_psycopg.register_model_spec(app, candidate, source='optimizer',
                                      code_commit=COMMIT)
    registration = store.RoleEventBatch(events=(
        store.RoleEventSubmission(
            lineage=LINEAGE, spec_sha256=candidate.spec_sha256(),
            role='CANDIDATE', effective_at=LATER, reason='promising',
            forward_plan={'look_at': 150}, actor='auto:rules-v1',
            code_commit=COMMIT),))
    assert store_psycopg.record_role_events(app, registration)['inserted'] is True
    # Replay: all keys present with identical content -> original ids.
    replay = store_psycopg.record_role_events(app, registration)
    assert replay['inserted'] is False
    # A handover missing its GUARD partner is rejected BEFORE any
    # insert: the library keeps no half pair.
    with pytest.raises(RegistryInvariantError):
        store_psycopg.record_role_events(app, store.RoleEventBatch(events=(
            store.RoleEventSubmission(
                lineage=LINEAGE, spec_sha256=candidate.spec_sha256(),
                role='ACTIVE', effective_at=LATER + timedelta(hours=1),
                reason='promote', actor='auto:rules-v1',
                code_commit=COMMIT),)))
    with app.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM papersim.model_role_events "
                       "WHERE role = 'ACTIVE'")
        assert cursor.fetchone()[0] == 3  # one per initialized lineage
    # The legal pair lands as two rows sharing effective_at, and the
    # folded view hands over exactly.
    at = LATER + timedelta(hours=2)
    handover = store.RoleEventBatch(events=(
        store.RoleEventSubmission(
            lineage=LINEAGE, spec_sha256=BASELINE, role='GUARD',
            effective_at=at, reason='demoted by promotion',
            actor='auto:rules-v1', code_commit=COMMIT),
        store.RoleEventSubmission(
            lineage=LINEAGE, spec_sha256=candidate.spec_sha256(),
            role='ACTIVE', effective_at=at, reason='promoted',
            actor='auto:rules-v1', code_commit=COMMIT)))
    result = store_psycopg.record_role_events(app, handover)
    assert result['inserted'] is True and set(result['role_event_ids']) == {'GUARD',
                                                                           'ACTIVE'}
    replay = store_psycopg.record_role_events(app, handover)
    assert replay['inserted'] is False
    view = store_psycopg.load_lineage_roles(app, LINEAGE)
    assert view.active == candidate.spec_sha256()
    assert view.guard == BASELINE
    app.close()


@NATIVE
def test_native_control_events_unfreeze_never_automatic():
    store_psycopg = _adapter()
    _owner_dsn, dsn = _migrated_app()
    app = _app(dsn)
    freeze = store.OptimizerControlEventSubmission(
        scope='GLOBAL', control_kind='FREEZE', reason='ops pause',
        actor='auto:rules-v1', occurred_at=AT)
    assert store_psycopg.record_optimizer_control_event(app, freeze)['inserted'] is True
    assert store_psycopg.record_optimizer_control_event(
        app, freeze)['inserted'] is False
    # The application layer refuses an automatic unfreeze ...
    with pytest.raises(store.PapersimStoreError):
        store_psycopg.record_optimizer_control_event(
            app, store.OptimizerControlEventSubmission(
                scope='GLOBAL', control_kind='UNFREEZE', reason='resume',
                actor='auto:rules-v1', occurred_at=LATER))
    # ... and so does the database CHECK for anything bypassing it
    # (raw negative SQL runs on a plain AUTOCOMMIT session so the
    # aborted statement cannot poison the managed adapter).
    psycopg = pytest.importorskip('psycopg')
    raw = _connect(dsn)
    with pytest.raises(psycopg.errors.CheckViolation):
        with raw.cursor() as cursor:
            cursor.execute(
                "INSERT INTO papersim.optimizer_control_events "
                "(optimizer_control_key, scope, control_kind, reason, actor, "
                "occurred_at) VALUES (%s, 'GLOBAL', 'UNFREEZE', 'r', 'auto:v9', %s)",
                ('direct-uz', LATER))
    raw.close()
    manual = store.OptimizerControlEventSubmission(
        scope='GLOBAL', control_kind='UNFREEZE', reason='owner resumes',
        actor='owner', occurred_at=LATER)
    assert store_psycopg.record_optimizer_control_event(
        app, manual)['inserted'] is True
    assert store_psycopg.registry_freeze_state(app) == {'global_frozen': False,
                                                        'frozen_lineages': []}
    scoped = store.OptimizerControlEventSubmission(
        scope='LINEAGE', lineage=LINEAGE, control_kind='FREEZE',
        reason='data integrity', actor='op', occurred_at=LATER)
    store_psycopg.record_optimizer_control_event(app, scoped)
    assert store_psycopg.registry_freeze_state(app) == {
        'global_frozen': False, 'frozen_lineages': [LINEAGE]}
    app.close()


@NATIVE
def test_native_foreign_keys_hold_shadow_to_candidate_and_specs_to_runs():
    store_psycopg = _adapter()
    _owner_dsn, dsn = _migrated_app()
    app, _ = _initialized(dsn)
    candidate = candidate_spec()
    store_psycopg.register_model_spec(app, candidate, source='optimizer',
                                      code_commit=COMMIT)
    psycopg = pytest.importorskip('psycopg')
    raw = _connect(dsn)
    # A shadow prediction hangs on an ACTIVE candidate row that does
    # not exist here (the writer is MO-7): the FK rejects.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with raw.cursor() as cursor:
            cursor.execute(
                "INSERT INTO papersim.shadow_predictions (shadow_key, candidate_id, "
                "spec_sha256, role_at_prediction, gate_pass, predicted_at) "
                "VALUES (%s, 999999, %s, 'CANDIDATE', true, %s)",
                ('sk-1', candidate.spec_sha256(), AT))
    raw.close()
    # A decision cannot point at a spec that was never registered.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store_psycopg.record_promotion_decision(app, store.PromotionDecisionSubmission(
            lineage=LINEAGE, active_spec_sha256='f' * 64, rule_version='rules-v1',
            decided_by='op', decided_at=LATER, conclusion='HOLD'))
    app.close()
