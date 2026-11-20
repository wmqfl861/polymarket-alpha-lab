"""Opt-in native MO-8 proofs: one full round on a private cluster.

Runs only inside the disposable native cluster environment (the CI
papersim partition or an explicitly assembled local cluster); never a
business cluster.  The drill of plan 05 node MO-8's acceptance: seed a
registry, register a candidate, seed shadow evidence, decide (PROMOTE),
apply the handover, decide the ROLLBACK look, apply the rollback --
then assert the fold, the idempotent decision replay and the look
bookkeeping end to end through the REAL CLI handlers.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import types
import uuid

import pytest

from polymarket_alpha_lab.papersim.optimize.families.soccer_frozen import (
    SOCCER_FROZEN,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim proof is opt-in')

INIT = datetime(2026, 11, 1, tzinfo=UTC)
REGISTERED = datetime(2026, 11, 2, tzinfo=UTC)
LINEAGE = 'club-soccer-frozen/over_under'


def _at(day: int, hour: int = 18) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def _dec(day: int, hour: int = 18) -> datetime:
    return datetime(2026, 12, day, hour, tzinfo=UTC)


def candidate_spec() -> ModelSpec:
    return ModelSpec(family='club-soccer-frozen', procedure_version='r23',
                     params={'kappa': 0.3}, envelope=({'name': 'confs',
                                                       'weight': 0.5},),
                     data_contract='understat-xg-snapshot-v1')


def _connect(dsn, *, autocommit=True):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _migrated_app():
    psycopg = pytest.importorskip('psycopg')
    from polymarket_alpha_lab.papersim import migrations as pm
    from polymarket_alpha_lab.project_postgres import sql
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_mo8drill_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind(OWNER_DSN, name)
    bootstrap = psycopg.connect(owner_dsn)
    bootstrap.autocommit = True
    with bootstrap.cursor() as cursor:
        cursor.execute(sql.bootstrap_sql(uuid.uuid4().hex, 'b' * 64, system_id))
    bootstrap.close()
    owner = _connect(owner_dsn, autocommit=False)
    applied = pm.apply_pending_migrations(owner, ROOT)
    assert applied['applied'] >= 6
    owner.commit()
    owner.close()
    return owner_dsn, owner_dsn.replace('user=pal_owner', 'user=pal_app')


def _clean_worktree(tmp_path: Path) -> Path:
    """A clean checkout of HEAD for require_clean_worktree (role events
    carry a code commit; the live tree may be mid-change)."""
    work = tmp_path / 'clean'
    subprocess.run(['git', '-C', str(ROOT), 'worktree', 'add',
                    '--detach', str(work), 'HEAD'], check=True,
                   capture_output=True)
    return work


def _seed_scan(owner, *, scan_key: str, day: datetime) -> int:
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, "
            "team, started_at, finished_at, code_commit, schema_version, "
            "gate_version, operator, params) VALUES (%s,%s,'paper',%s,%s,%s,1,"
            "'g1','unattended','{}'::jsonb) RETURNING scan_id",
            (scan_key, sha256(scan_key.encode()).hexdigest(), day, day,
             'c' * 40))
        return int(cursor.fetchone()[0])


def _seed_pair(owner, *, scan_id: int, index: int, start: datetime,
               observed: datetime, yes_won: bool, shadow_spec: str,
               priced_version: str, priced_probability: float,
               shadow_is_better: bool, role_at_prediction: str) -> None:
    """One market: an ACTIVE-priced candidate row, its natural outcome
    and one SHADOW row (60/40 on the winning side when the shadow is
    the better model, 40/60 when it is the worse one)."""
    evaluated_at = start + timedelta(hours=2)
    market_id = f"market-{scan_id}-{index}"
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.candidates (scan_id, candidate_key, "
            "market_id, side, market_kind, market_type, team, model_name, "
            "model_version, median_probability, input_payload, conclusion, "
            "gate_report, evaluated_at, event_key, event_start_at) "
            "VALUES (%s,%s,%s,'YES','terminal','over_under','paper',"
            "'soccer',%s,%s,'{}'::jsonb,'ACCEPTED','{}'::jsonb,%s,%s,%s) "
            "RETURNING candidate_id",
            (scan_id, f"ck-{scan_id}-{index}", market_id, priced_version,
             priced_probability, evaluated_at, f"match-{scan_id}-{index}",
             start))
        candidate_id = int(cursor.fetchone()[0])
        cursor.execute(
            "INSERT INTO papersim.market_outcomes (evidence_key, market_id, "
            "outcome, observed_at, source_kind) VALUES (%s,%s,%s,%s,"
            "'data_api_resolution')",
            (f"outcome-{scan_id}-{index}", market_id,
             'YES' if yes_won else 'NO', observed))
        winner = 0.6 if shadow_is_better else 0.4
        loser = 1.0 - winner
        cursor.execute(
            "INSERT INTO papersim.shadow_predictions (shadow_key, "
            "candidate_id, spec_sha256, role_at_prediction, probability, "
            "gate_pass, predicted_at) VALUES (%s,%s,%s,%s,%s,false,%s)",
            (f"sp|{candidate_id}|{shadow_spec}|"
             f"{evaluated_at.strftime('%Y%m%d%H%M%S')}", candidate_id,
             shadow_spec, role_at_prediction,
             winner if yes_won else loser, evaluated_at))


def _run_decide(cli, capsys, config_root: Path, clean_root: Path) -> dict:
    """The scan config lives OUTSIDE the clean worktree (writing it
    inside would dirty the tree the apply path proves clean)."""
    import json as _json

    config_path = config_root / 'scan-config.json'
    config_path.write_text(
        '{"team":"paper","operator":"unattended","queues":[{"queue":"q1",'
        '"lineage":"' + LINEAGE + '","data_source_version":"v1"}]}',
        encoding='utf-8')
    args = types.SimpleNamespace(
        command='optimize', optimize_command='decide', lineage=LINEAGE,
        config=str(config_path), managed=False, dsn=None,
        repo_root=str(clean_root))
    assert cli._cmd_optimize_decide(args) == 0
    return _json.loads(capsys.readouterr().out)


def _run_apply(cli, capsys, config_root: Path, clean_root: Path,
                decision_id: int) -> dict:
    import json as _json

    args = types.SimpleNamespace(
        command='optimize', optimize_command='apply',
        decision_id=decision_id, actor=None, confirm=False,
        config=str(config_root / 'scan-config.json'),
        managed=False, dsn=None, repo_root=str(clean_root))
    assert cli._cmd_optimize_apply(args) == 0
    return _json.loads(capsys.readouterr().out)


@NATIVE
def test_native_full_round_register_shadow_decide_handover_rollback(
        tmp_path, monkeypatch, capsys):
    owner_dsn, app_dsn = _migrated_app()
    owner = _connect(owner_dsn, autocommit=False)
    app = _connect(app_dsn)
    store_psycopg = pytest.importorskip(
        'polymarket_alpha_lab.papersim.store_psycopg')
    cli = pytest.importorskip('polymarket_alpha_lab.papersim.cli')

    clean_root = _clean_worktree(tmp_path)
    # Each handler call opens (and in its finally CLOSES) its own
    # connection -- a fresh app connection per call, like production.
    monkeypatch.setattr(cli, '_open_readonly_connection',
                        lambda args, label: _connect(app_dsn))
    try:
        # -- 1. initialize the registry and register a candidate ------
        store_psycopg.initialize_registry(
            app, families=[SOCCER_FROZEN], actor='op', code_commit='c' * 40,
            effective_at=INIT)
        from polymarket_alpha_lab.papersim import store
        spec = candidate_spec()
        store_psycopg.register_model_spec(app, spec, source='optimizer',
                                          code_commit='c' * 40)
        store_psycopg.record_role_events(app, store.RoleEventBatch(events=(
            store.RoleEventSubmission(
                lineage=LINEAGE, spec_sha256=spec.spec_sha256(),
                role='CANDIDATE', effective_at=REGISTERED,
                reason='drill registration', forward_plan={},
                actor='auto:promo-rule-v1', code_commit='c' * 40),)))
        baseline = SOCCER_FROZEN.baseline_spec()
        active_version = baseline.model_version()

        # -- 2. phase-1 evidence: 160 markets over ISO weeks 45/46/47;
        #       the candidate's shadow is uniformly better than 0.5 ----
        scan1 = _seed_scan(owner, scan_key='drill-1', day=_at(3))
        starts = ([_at(day) for day in (3, 4, 5, 10, 11, 12, 17, 18, 19)]
                  * 18)[:160]
        for index, start in enumerate(starts):
            _seed_pair(owner, scan_id=scan1, index=index, start=start,
                       observed=start + timedelta(days=1),
                       yes_won=(index % 2 == 0),
                       shadow_spec=spec.spec_sha256(),
                       priced_version=active_version,
                       priced_probability=0.5, shadow_is_better=True,
                       role_at_prediction='CANDIDATE')
        owner.commit()

        # -- 3. decide: the first evidence look promotes ---------------
        report = _run_decide(cli, capsys, tmp_path, clean_root)
        assert [row['conclusion'] for row in report['written']] == ['PROMOTE']
        decision_id = report['written'][0]['decision_id']
        # A second decide at the same watermark writes NOTHING new (the
        # evidence ordinal moved to 1 and 2x150 markets are not due).
        replay = _run_decide(cli, capsys, tmp_path, clean_root)
        assert replay['written'] == []
        assert replay['looks'][0]['evidence_ordinal'] == 1

        # -- 4. apply: the handover batch lands as one transaction -----
        applied = _run_apply(cli, capsys, tmp_path, clean_root, decision_id)
        assert applied['conclusion'] == 'PROMOTE'
        view = store_psycopg.load_lineage_roles(app, LINEAGE)
        assert view.active == spec.spec_sha256()
        assert view.guard == baseline.spec_sha256()

        # -- 5. phase-2: 155 markets over ISO weeks 48/49 where the NEW
        #       active is uniformly worse than the guard ---------------
        scan2 = _seed_scan(owner, scan_key='drill-2', day=_dec(1))
        phase2 = ([datetime(2026, 11, day, 18, tzinfo=UTC)
                   for day in (24, 25)] * 40)[:40] + \
                 ([datetime(2026, 12, day, 18, tzinfo=UTC)
                   for day in (1, 2)] * 60)[:115]
        for index, start in enumerate(phase2):
            _seed_pair(owner, scan_id=scan2, index=index, start=start,
                       observed=start + timedelta(days=5),
                       yes_won=(index % 2 == 0),
                       shadow_spec=baseline.spec_sha256(),
                       priced_version=spec.model_version(),
                       priced_probability=0.5, shadow_is_better=True,
                       role_at_prediction='GUARD')
        owner.commit()

        rollback_report = _run_decide(cli, capsys, tmp_path, clean_root)
        conclusions = {row['class']: row['conclusion']
                       for row in rollback_report['written']}
        assert conclusions == {'ROLLBACK': 'ROLLBACK'}
        rollback_decision = rollback_report['written'][0]['decision_id']

        # -- 6. apply the rollback: the failed active retires ----------
        applied2 = _run_apply(cli, capsys, tmp_path, clean_root,
                              rollback_decision)
        assert applied2['conclusion'] == 'ROLLBACK'
        final = store_psycopg.load_lineage_roles(app, LINEAGE)
        assert final.active == baseline.spec_sha256()
        assert final.guard is None
        assert spec.spec_sha256() in final.retired

        # -- 7. the third decide has nothing left to judge -------------
        settled = _run_decide(cli, capsys, tmp_path, clean_root)
        assert settled['written'] == [] and settled['looks'] == []
    finally:
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'remove',
                        '--force', str(clean_root)], check=True,
                       capture_output=True)
        owner.close()
        app.close()
