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
round-trip (plan-review P3-4). Registry reads preserve the incoming
transaction state after success, malformed stored specs and SQL errors.
"""

from contextlib import closing, contextmanager, nullcontext
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
from polymarket_alpha_lab.papersim.optimize.provider import RegistryProvider
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
SCAN_LINEAGE = 'club-soccer/over_under'
MISMATCH_LINEAGE = 'club-soccer-frozen/btts'
MISMATCH_SCAN_LINEAGE = 'club-soccer/btts'
BASELINE_SPEC = SOCCER_FROZEN.baseline_spec()
BASELINE = BASELINE_SPEC.spec_sha256()
UNKNOWN_SPEC = 'e' * 64
AUTOCOMMIT_MODES = pytest.mark.parametrize('autocommit', [False, True],
                                            ids=['default', 'autocommit'])


def candidate_spec() -> ModelSpec:
    return ModelSpec(family='club-soccer-frozen', procedure_version='r23',
                     params={'kappa': 0.25}, envelope=({'name': 'confs',
                                                        'weight': 0.5},),
                     data_contract='understat-xg-snapshot-v1')


def _connect(dsn, *, autocommit=True):
    connection = _adapter().connect_explicit(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _migrated_app() -> tuple[str, str]:
    """A fully migrated private database; returns (owner_dsn, app_dsn)."""
    from polymarket_alpha_lab.project_postgres import sql

    name = 'pal_papersim_mo2reg_' + uuid.uuid4().hex[:10]
    with _connect(OWNER_DSN) as admin, admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    owner_dsn = _rebind_database(OWNER_DSN, name)
    with _connect(owner_dsn) as bootstrap, bootstrap.cursor() as cursor:
        cursor.execute(sql.bootstrap_sql(uuid.uuid4().hex, 'b' * 64, system_id))
    with _connect(owner_dsn, autocommit=False) as owner:
        assert pm.apply_pending_migrations(owner, ROOT) == {'applied': 6, 'pending_before': 6}
    return owner_dsn, owner_dsn.replace('user=pal_owner', 'user=pal_app')


def _adapter():
    """The real adapter, imported at EXECUTION time only.

    The offline ledger proof injects a fake store_psycopg into
    sys.modules and depends on the real one never being imported in an
    offline process; collection-time imports would break it (the m2/
    fx26 native files follow the same function-level discipline).
    """

    from polymarket_alpha_lab.papersim import store_psycopg

    return store_psycopg


def _app(dsn: str):
    return _connect(dsn, autocommit=False)


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


@pytest.fixture
def registry_database():
    return _migrated_app()


def _register_candidate_role(conn, lineage, spec):
    result = _adapter().record_role_events(conn, store.RoleEventBatch(events=(
        store.RoleEventSubmission(
            lineage=lineage, spec_sha256=spec.spec_sha256(), role='CANDIDATE',
            effective_at=LATER, reason='transaction test', forward_plan={'look_at': 150},
            actor='auto:rules-v1', code_commit=COMMIT),)))
    assert result['inserted'] is True


def _seed_registered_registry(dsn):
    adapter = _adapter()
    app, _ = _initialized(dsn)
    with app:
        candidate = candidate_spec()
        assert adapter.register_model_spec(app, candidate, source='optimizer',
                                           code_commit=COMMIT)['inserted'] is True
        _register_candidate_role(app, LINEAGE, candidate)
    return candidate


def _seed_mismatched_spec(owner_dsn, app_dsn):
    spec = ModelSpec(family=SOCCER_FROZEN.family_id,
                     procedure_version=BASELINE_SPEC.procedure_version,
                     params={'kappa': 0.75}, envelope=(),
                     data_contract=BASELINE_SPEC.data_contract)
    with _connect(owner_dsn) as owner:
        # The canonical text and digest satisfy the database CHECK. Only
        # the separately stored rebuild fields disagree with that identity.
        owner.execute(
            "INSERT INTO papersim.model_specs "
            "(spec_sha256, canonical_text, family, procedure_version, model_version, "
            "params, envelope, data_contract, source, code_commit) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, '[]'::jsonb, %s, 'manual', %s)",
            (spec.spec_sha256(), spec.canonical_json(), spec.family, spec.procedure_version,
             spec.model_version(), '{"kappa": 0.5}', spec.data_contract, COMMIT))
    with _app(app_dsn) as app:
        _register_candidate_role(app, MISMATCH_LINEAGE, spec)
    return spec.spec_sha256()


@contextmanager
def _expect_read_transaction(conn, status):
    autocommit = conn.autocommit
    assert conn.info.transaction_status is status
    yield
    assert conn.info.transaction_status is status
    assert conn.autocommit is autocommit


def _assert_spec(actual, expected):
    assert actual.spec_sha256() == expected.spec_sha256()
    assert actual.canonical_json() == expected.canonical_json()
    assert actual.model_version() == expected.model_version()


def _assert_fallback(provider, lineage, reason):
    spec_view = provider.specs_for_lineage(lineage)
    assert spec_view.fallback_reason == reason
    _assert_spec(spec_view.active, BASELINE_SPEC)
    _assert_spec(spec_view.baseline, BASELINE_SPEC)
    assert spec_view.candidates == () and spec_view.guard is None
    assert spec_view.registry is None


def _assert_registered(provider, candidate):
    spec_view = provider.specs_for_lineage(SCAN_LINEAGE)
    assert spec_view.fallback_reason is None
    _assert_spec(spec_view.active, BASELINE_SPEC)
    _assert_spec(spec_view.baseline, BASELINE_SPEC)
    assert len(spec_view.candidates) == 1
    _assert_spec(spec_view.candidates[0], candidate)
    assert candidate.spec_sha256() != BASELINE
    assert spec_view.registry == ((LINEAGE, BASELINE, (candidate.spec_sha256(),)),)


def _snapshot_sentinel():
    from polymarket_alpha_lab.papersim.snapshots import SnapshotRegistration

    return SnapshotRegistration(uuid.uuid4().bytes, 'test', 'registry transaction', AT)


def _assert_snapshot_visible(conn, snapshot, *, visible):
    with conn.transaction(), conn.cursor() as cursor:
        cursor.execute('SELECT content FROM papersim.snapshots WHERE content_sha256 = %s',
                       (snapshot.content_sha256,))
        rows = cursor.fetchall()
    assert rows == ([(snapshot.content,)] if visible else [])


@contextmanager
def _caller_transaction(conn, *, commit, implicit=False):
    from psycopg import Rollback

    if implicit:
        conn.execute('SELECT 1')
        yield
        if commit:
            conn.commit()
        else:
            conn.rollback()
        return
    with conn.transaction() as transaction:
        yield
        if not commit:
            raise Rollback(transaction)


def _assert_mismatched_spec_read(conn, status, digest):
    with _expect_read_transaction(conn, status), pytest.raises(
            store.PapersimStoreError, match='^papersim_model_spec_roundtrip_mismatch$'):
        _adapter().load_model_spec(conn, digest)


@NATIVE
@AUTOCOMMIT_MODES
def test_native_registry_idle_reads_release_transaction(registry_database, autocommit,
                                                        monkeypatch):
    from psycopg.pq import TransactionStatus

    from polymarket_alpha_lab.papersim.snapshots import register_snapshot

    adapter = _adapter()
    idle = TransactionStatus.IDLE
    _owner_dsn, dsn = registry_database
    sentinel = _snapshot_sentinel()
    with closing(_connect(dsn, autocommit=autocommit)) as app:
        with _expect_read_transaction(app, idle):
            empty = RegistryProvider.from_connection(app)
        _assert_fallback(empty, SCAN_LINEAGE, 'registry_empty')

        candidate = _seed_registered_registry(dsn)
        with _expect_read_transaction(app, idle):
            rebuilt = adapter.load_model_spec(app, BASELINE)
        _assert_spec(rebuilt, BASELINE_SPEC)
        with _expect_read_transaction(app, idle):
            unknown = adapter.load_model_spec(app, UNKNOWN_SPEC)
        assert unknown is None

        loaded_digests = []
        load_model_spec = adapter.load_model_spec

        def track_lookup(conn, digest):
            loaded_digests.append(digest)
            return load_model_spec(conn, digest)

        monkeypatch.setattr(adapter, 'load_model_spec', track_lookup)
        with _expect_read_transaction(app, idle):
            registered = RegistryProvider.from_connection(app)
        _assert_registered(registered, candidate)
        # Active and Baseline share a digest across the initialized lineages.
        assert sorted(loaded_digests) == sorted([BASELINE, candidate.spec_sha256()])

        assert register_snapshot(app, sentinel)['inserted'] is True
        assert app.info.transaction_status is idle
    with _app(dsn) as observer:
        _assert_snapshot_visible(observer, sentinel, visible=True)


@NATIVE
@AUTOCOMMIT_MODES
def test_native_registry_spec_content_failure_releases_idle_transaction(registry_database,
                                                                        autocommit):
    from psycopg.pq import TransactionStatus

    adapter = _adapter()
    idle = TransactionStatus.IDLE
    owner_dsn, dsn = registry_database
    candidate = _seed_registered_registry(dsn)
    mismatched = _seed_mismatched_spec(owner_dsn, dsn)
    with closing(_connect(dsn, autocommit=autocommit)) as app:
        _assert_mismatched_spec_read(app, idle, mismatched)
        with _expect_read_transaction(app, idle):
            provider = RegistryProvider.from_connection(app)
        _assert_fallback(provider, MISMATCH_SCAN_LINEAGE, 'spec_checksum_mismatch')
        _assert_registered(provider, candidate)
        with _expect_read_transaction(app, idle):
            rebuilt = adapter.load_model_spec(app, BASELINE)
        _assert_spec(rebuilt, BASELINE_SPEC)


@NATIVE
@pytest.mark.parametrize('autocommit,implicit', [(False, False), (True, False), (False, True)],
                         ids=['explicit-default', 'explicit-autocommit', 'implicit-default'])
@pytest.mark.parametrize('commit', [False, True], ids=['rollback', 'commit'])
def test_native_registry_reads_preserve_caller_transaction(registry_database, autocommit,
                                                           implicit, commit):
    from psycopg.pq import TransactionStatus

    from polymarket_alpha_lab.papersim.snapshots import register_snapshot

    adapter = _adapter()
    intrans = TransactionStatus.INTRANS
    owner_dsn, dsn = registry_database
    candidate = _seed_registered_registry(dsn)
    mismatched = _seed_mismatched_spec(owner_dsn, dsn)
    sentinel = _snapshot_sentinel()
    with closing(_connect(dsn, autocommit=autocommit)) as app, _app(dsn) as observer:
        with _caller_transaction(app, commit=commit, implicit=implicit):
            assert register_snapshot(app, sentinel)['inserted'] is True
            _assert_snapshot_visible(observer, sentinel, visible=False)

            with _expect_read_transaction(app, intrans):
                rebuilt = adapter.load_model_spec(app, BASELINE)
            _assert_spec(rebuilt, BASELINE_SPEC)
            _assert_snapshot_visible(app, sentinel, visible=True)
            _assert_snapshot_visible(observer, sentinel, visible=False)

            _assert_mismatched_spec_read(app, intrans, mismatched)
            with _expect_read_transaction(app, intrans):
                unknown = adapter.load_model_spec(app, UNKNOWN_SPEC)
            assert unknown is None
            with _expect_read_transaction(app, intrans):
                provider = RegistryProvider.from_connection(app)
            _assert_fallback(provider, MISMATCH_SCAN_LINEAGE, 'spec_checksum_mismatch')
            _assert_registered(provider, candidate)
            _assert_snapshot_visible(app, sentinel, visible=True)
            _assert_snapshot_visible(observer, sentinel, visible=False)
            with _expect_read_transaction(app, intrans):
                rebuilt = adapter.load_model_spec(app, BASELINE)
            _assert_spec(rebuilt, BASELINE_SPEC)
        assert app.info.transaction_status is TransactionStatus.IDLE
    with _app(dsn) as observer:
        _assert_snapshot_visible(observer, sentinel, visible=commit)


@NATIVE
@AUTOCOMMIT_MODES
@pytest.mark.parametrize('outcome', ['idle', 'rollback', 'commit'])
@pytest.mark.parametrize('reader', ['loader', 'provider'])
def test_native_registry_sql_failure_releases_read_transaction(registry_database, autocommit,
                                                             outcome, reader):
    from psycopg.errors import LockNotAvailable
    from psycopg.pq import TransactionStatus

    from polymarket_alpha_lab.papersim.snapshots import register_snapshot

    adapter = _adapter()
    owner_dsn, dsn = registry_database
    candidate = _seed_registered_registry(dsn)
    sentinel = _snapshot_sentinel()
    idle = outcome == 'idle'
    status = TransactionStatus.IDLE if idle else TransactionStatus.INTRANS
    with closing(_connect(dsn, autocommit=autocommit)) as app, _app(dsn) as observer:
        with app.transaction():
            app.execute("SET lock_timeout = '250ms'")
        assert app.info.transaction_status is TransactionStatus.IDLE

        caller = nullcontext() if idle else _caller_transaction(app, commit=outcome == 'commit')
        with caller:
            if not idle:
                assert register_snapshot(app, sentinel)['inserted'] is True
                _assert_snapshot_visible(observer, sentinel, visible=False)
            with _connect(owner_dsn) as locker, locker.transaction():
                locker.execute('LOCK TABLE papersim.model_specs IN ACCESS EXCLUSIVE MODE')
                with _expect_read_transaction(app, status):
                    roles = adapter.load_all_role_events(app)
                assert LINEAGE in roles
                if reader == 'loader':
                    with _expect_read_transaction(app, status), pytest.raises(
                            LockNotAvailable) as error:
                        adapter.load_model_spec(app, BASELINE)
                    assert error.value.sqlstate == '55P03'
                else:
                    with _expect_read_transaction(app, status):
                        provider = RegistryProvider.from_connection(app)
                    _assert_fallback(provider, SCAN_LINEAGE, 'registry_read_failed')
            if not idle:
                _assert_snapshot_visible(app, sentinel, visible=True)
                _assert_snapshot_visible(observer, sentinel, visible=False)
            with _expect_read_transaction(app, status):
                provider = RegistryProvider.from_connection(app)
            _assert_registered(provider, candidate)
        assert app.info.transaction_status is TransactionStatus.IDLE
    with _app(dsn) as observer:
        _assert_snapshot_visible(observer, sentinel, visible=outcome == 'commit')
