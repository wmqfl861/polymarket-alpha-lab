"""Opt-in native MO-8 proofs on a private cluster.

Runs only inside the disposable native cluster environment (the CI
papersim partition or an explicitly assembled local cluster); never a
business cluster. The forward drill captures the due verdict at its
write boundary and exercises apply against synthetic historical
promotion records. Its rollback starts from a fixture handover and
uses the real statistical decision and role writers. The zero-pairing
cases also run score and decide through the CLI parser.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json
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
    from polymarket_alpha_lab.local_postgres_dsn import validate_local_postgres_dsn

    validate_local_postgres_dsn(dsn, env_var_name='PAPERSIM_NATIVE_MANAGED_OWNER_DSN')
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _migrated_app():
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
    bootstrap = _connect(owner_dsn)
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


def _seed_scan(owner, *, scan_key: str, day: datetime, params=None) -> int:
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, "
            "team, started_at, finished_at, code_commit, schema_version, "
            "gate_version, operator, params) VALUES (%s,%s,'paper',%s,%s,%s,1,"
            "'g1','unattended',%s::jsonb) RETURNING scan_id",
            (scan_key, sha256(scan_key.encode()).hexdigest(), day, day,
             'c' * 40, json.dumps(params if params is not None else {})))
        return int(cursor.fetchone()[0])


def _seed_pair(owner, *, scan_id: int, index: int, start: datetime,
               observed: datetime | None, yes_won: bool, shadow_spec: str | None,
               priced_version: str, priced_probability: float,
               shadow_is_better: bool, role_at_prediction: str,
               market_type: str = 'over_under', side: str = 'YES',
               conclusion: str = 'ACCEPTED', shadow_gate: bool = False,
               shadow_fill=None, shadow_probability=None, shadow_refusal=None) -> int:
    """One market: an ACTIVE-priced candidate row, its natural outcome
    if observed, and one SHADOW row (60/40 on the winning side when the
    shadow is the better model, 40/60 when it is the worse one)."""
    evaluated_at = start + timedelta(hours=2)
    market_id = f"market-{scan_id}-{index}"
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.candidates (scan_id, candidate_key, "
            "market_id, side, market_kind, market_type, team, model_name, "
            "model_version, median_probability, input_payload, conclusion, "
            "gate_report, evaluated_at, event_key, event_start_at) "
            "VALUES (%s,%s,%s,%s,'terminal',%s,'paper',"
            "'soccer',%s,%s,'{}'::jsonb,%s,'{}'::jsonb,%s,%s,%s) "
            "RETURNING candidate_id",
            (scan_id, f"ck-{scan_id}-{index}", market_id, side, market_type,
             priced_version, priced_probability, conclusion, evaluated_at,
             f"match-{scan_id}-{index}", start))
        candidate_id = int(cursor.fetchone()[0])
        if observed is not None:
            cursor.execute(
                "INSERT INTO papersim.market_outcomes (evidence_key, market_id, "
                "outcome, observed_at, source_kind) VALUES (%s,%s,%s,%s,"
                "'data_api_resolution')",
                (f"outcome-{scan_id}-{index}", market_id,
                 'YES' if yes_won else 'NO', observed))
    if shadow_spec is not None:
        if shadow_probability is None and shadow_refusal is None:
            winner = 0.6 if shadow_is_better else 0.4
            shadow_probability = winner if yes_won else 1.0 - winner
        _seed_shadow(owner, candidate_id, shadow_spec, evaluated_at,
                     role_at_prediction, shadow_probability,
                     gate_pass=shadow_gate, fill=shadow_fill, refusal=shadow_refusal)
    return candidate_id


def _seed_shadow(owner, candidate_id, spec_sha, predicted_at, role, probability,
                 *, gate_pass, fill, refusal=None):
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.shadow_predictions (shadow_key, "
            "candidate_id, spec_sha256, role_at_prediction, probability, "
            "gate_pass, hypothetical_fill_price, refusal_reason, predicted_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING shadow_prediction_id",
            (f"sp|{candidate_id}|{spec_sha}|{predicted_at.isoformat()}", candidate_id,
             spec_sha, role, probability, gate_pass, fill, refusal, predicted_at))
        return cursor.fetchone()[0]


def _seed_close(owner, candidate_id, side, observed_at, *, last=None, mid=None, bid=None):
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.candidate_price_evidence "
            "(evidence_key, candidate_id, evidence_role, observed_at, "
            "market_ref_kind, market_ref, side, last, mid, bid, source_kind) "
            "SELECT %s, candidate_id, 'CLOSE', %s, 'market_id', market_id, "
            "%s, %s, %s, %s, 'clob_book' FROM papersim.candidates WHERE candidate_id = %s "
            "RETURNING evidence_id",
            (f"clv-close-{uuid.uuid4().hex}", observed_at, side, last, mid, bid, candidate_id))
        return cursor.fetchone()[0]


def _seed_entry(owner, candidate_id, price, entered_at):
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.entries (candidate_id, stake, price, shares, "
            "fee_category, fee_rate, entry_fee, tier, entered_at) "
            "VALUES (%s,%s,%s,1,'sports',0,0,'A',%s) RETURNING entry_id",
            (candidate_id, price, price, entered_at))
        return cursor.fetchone()[0]


def _seed_forward_decision(owner, *, lineage, candidate_sha, active_sha,
                           conclusion, look_number, decided_at, evidence, metrics):
    from polymarket_alpha_lab.papersim.optimize.policy import RULE_VERSION
    from polymarket_alpha_lab.papersim.store import promotion_decision_key

    # Pre-existing fixture history for apply/readback, independent of the
    # optimizer's decision write entry point.
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO papersim.promotion_decisions "
            "(decision_key, lineage, decision_class, candidate_spec_sha256, "
            "active_spec_sha256, rule_version, look_number, evidence_watermark, "
            "metrics, conclusion, decided_by, decided_at) "
            "VALUES (%s,%s,'FORWARD',%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s) "
            "RETURNING decision_id",
            (promotion_decision_key(lineage, 'FORWARD', candidate_sha, RULE_VERSION, look_number),
             lineage, candidate_sha, active_sha, RULE_VERSION, look_number,
             json.dumps(evidence), json.dumps(metrics), conclusion, f'auto:{RULE_VERSION}', decided_at))
        return cursor.fetchone()[0]


def _seed_role_history(owner, lineage, instant, roles, *, decision_id=None,
                       reason='synthetic role history', actor='operator', code_commit='c' * 40):
    from polymarket_alpha_lab.papersim.store import model_role_event_key

    with owner.cursor() as cursor:
        for digest, role in roles:
            cursor.execute(
                "INSERT INTO papersim.model_role_events (role_event_key, lineage, "
                "spec_sha256, role, effective_at, reason, forward_plan, decision_id, actor, code_commit) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                (model_role_event_key(lineage, digest, role, instant), lineage,
                 digest, role, instant, reason, '{}' if role == 'CANDIDATE' else None,
                 decision_id, actor, code_commit))


def _register_candidate(app, lineage, spec, *, effective_at=REGISTERED):
    from polymarket_alpha_lab.papersim import store, store_psycopg

    store_psycopg.register_model_spec(app, spec, source='optimizer', code_commit='c' * 40)
    store_psycopg.record_role_events(app, store.RoleEventBatch(events=(
        store.RoleEventSubmission(
            lineage=lineage, spec_sha256=spec.spec_sha256(), role='CANDIDATE',
            effective_at=effective_at, reason='drill registration', forward_plan={},
            actor='auto:promo-rule-v1', code_commit='c' * 40),)))


def _provider_params(app, *scan_lineages):
    from polymarket_alpha_lab.papersim import scan
    from polymarket_alpha_lab.papersim.optimize.provider import RegistryProvider

    provider = RegistryProvider.from_connection(app)
    return scan.provider_params({lineage: provider.specs_for_lineage(lineage)
                                for lineage in scan_lineages})


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
               decision_id: int, *, expected_failure=None, actor=None, confirm=False):
    args = types.SimpleNamespace(
        command='optimize', optimize_command='apply',
        decision_id=decision_id, actor=actor, confirm=confirm,
        config=str(config_root / 'scan-config.json'),
        managed=False, dsn=None, repo_root=str(clean_root))
    exit_code = cli._cmd_optimize_apply(args)
    output = capsys.readouterr()
    if expected_failure is not None:
        assert (exit_code, output.out) == (1, '')
        assert output.err == f'optimize apply: FAIL: {expected_failure}\n'
        return None
    assert (exit_code, output.err) == (0, '')
    return json.loads(output.out)


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
        spec = candidate_spec()
        _register_candidate(app, LINEAGE, spec)
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

        # -- 3. Capture the first due verdict at the decision writer ----
        captured = []

        def capture_forward(conn, **request):
            assert request['decision_class'] == 'FORWARD'
            captured.append(request)
            return {'intercepted': True}

        registered_history = store_psycopg.load_all_role_events(app)
        with monkeypatch.context() as patch:
            patch.setattr(cli, '_optimize_write_decision', capture_forward)
            report = _run_decide(cli, capsys, tmp_path, clean_root)
            assert report['written'] == [{'intercepted': True, 'conclusion': 'PROMOTE',
                                         'class': 'FORWARD'}]
            assert report['looks'] == [{
                'class': 'FORWARD', 'spec': spec.spec_sha256(), 'due': True,
                'key_counter': 1, 'evidence_ordinal': 0, 'blocked_looks_consumed': 0,
                'n_markets': 160,
            }]
            first, = captured
            assert first['candidate_sha'] == spec.spec_sha256()
            assert first['active_sha'] == baseline.spec_sha256()
            verdict = first['verdict']
            assert verdict.look_number == 1 and verdict.evidence_ordinal == 0
            assert store_psycopg.load_promotion_decisions(app, LINEAGE) == []
            assert store_psycopg.load_all_role_events(app) == registered_history

            handover = first['decided_at']
            decision_id = _seed_forward_decision(
                owner, lineage=LINEAGE, candidate_sha=spec.spec_sha256(),
                active_sha=baseline.spec_sha256(), conclusion=verdict.conclusion,
                look_number=1, decided_at=handover, evidence=verdict.evidence_watermark,
                metrics=verdict.metrics)
            owner.commit()
            replay = _run_decide(cli, capsys, tmp_path, clean_root)
            assert replay['written'] == []
            assert replay['looks'][0]['evidence_ordinal'] == 1
            assert replay['looks'][0]['key_counter'] == 2
            assert replay['looks'][0]['due'] is False
            assert len(captured) == 1

        # -- 4. Both promotions fail, including replay of legacy roles --
        pending_id = _seed_forward_decision(
            owner, lineage=LINEAGE, candidate_sha=spec.spec_sha256(),
            active_sha=baseline.spec_sha256(), conclusion='PROMOTE_PENDING_CONFIRM',
            look_number=2, decided_at=handover, evidence=verdict.evidence_watermark,
            metrics=verdict.metrics)
        owner.commit()
        decisions_before = store_psycopg.load_promotion_decisions(app, LINEAGE)

        def unexpected_promotion_role_write(*args, **kwargs):
            pytest.fail('a rejected promotion reached the role writer')

        with monkeypatch.context() as patch:
            patch.setattr(store_psycopg, 'record_role_events', unexpected_promotion_role_write)
            for blocked_id in (decision_id, pending_id):
                _run_apply(cli, capsys, tmp_path, clean_root, blocked_id,
                           expected_failure='papersim_optimize_apply_promotion_unavailable',
                           actor='operator', confirm=True)
        with _connect(app_dsn) as fresh:
            assert store_psycopg.load_all_role_events(fresh) == registered_history
            assert store_psycopg.load_promotion_decisions(fresh, LINEAGE) == decisions_before

        _seed_role_history(
            owner, LINEAGE, handover,
            ((baseline.spec_sha256(), 'GUARD'), (spec.spec_sha256(), 'ACTIVE')),
            decision_id=decision_id, reason=f'MO-8 FORWARD look 1 decision {decision_id}',
            actor='auto:promo-rule-v1')
        owner.commit()
        view = store_psycopg.load_lineage_roles(app, LINEAGE)
        assert view.active == spec.spec_sha256()
        assert view.guard == baseline.spec_sha256()
        legacy_history = store_psycopg.load_all_role_events(app)
        linked = store_psycopg.load_decision_role_events(app, LINEAGE, decision_id)
        assert [(row.spec_sha256, row.role) for row in linked] == [
            (baseline.spec_sha256(), 'GUARD'), (spec.spec_sha256(), 'ACTIVE'),
        ]
        with monkeypatch.context() as patch:
            patch.setattr(store_psycopg, 'record_role_events', unexpected_promotion_role_write)
            _run_apply(cli, capsys, tmp_path, clean_root, decision_id,
                       expected_failure='papersim_optimize_apply_promotion_unavailable')
        with _connect(app_dsn) as fresh:
            assert store_psycopg.load_all_role_events(fresh) == legacy_history
            assert store_psycopg.load_promotion_decisions(fresh, LINEAGE) == decisions_before

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

        write_decision = cli._optimize_write_decision

        def write_rollback_only(conn, **request):
            assert request['decision_class'] == 'ROLLBACK'
            return write_decision(conn, **request)

        with monkeypatch.context() as patch:
            patch.setattr(cli, '_optimize_write_decision', write_rollback_only)
            rollback_report = _run_decide(cli, capsys, tmp_path, clean_root)
        conclusions = {row['class']: row['conclusion']
                       for row in rollback_report['written']}
        assert conclusions == {'ROLLBACK': 'ROLLBACK'}
        rollback_decision = rollback_report['written'][0]['decision_id']

        # -- 6. apply the rollback: the failed active retires ----------
        applied2 = _run_apply(cli, capsys, tmp_path, clean_root,
                              rollback_decision)
        assert applied2['conclusion'] == 'ROLLBACK'
        assert applied2['actor'] == 'auto:promo-rule-v1'
        assert applied2['applied']['inserted'] is True
        with _connect(app_dsn) as fresh:
            final = store_psycopg.load_lineage_roles(fresh, LINEAGE)
            assert final.active == baseline.spec_sha256()
            assert final.guard is None
            assert spec.spec_sha256() in final.retired
            rolled_history = store_psycopg.load_all_role_events(fresh)
            rolled_decisions = store_psycopg.load_promotion_decisions(fresh, LINEAGE)
        applied_again = _run_apply(cli, capsys, tmp_path, clean_root, rollback_decision)
        assert applied_again['applied']['inserted'] is False
        assert applied_again['applied']['role_event_ids'] == applied2['applied']['role_event_ids']
        with _connect(app_dsn) as fresh:
            assert store_psycopg.load_all_role_events(fresh) == rolled_history
            assert store_psycopg.load_promotion_decisions(fresh, LINEAGE) == rolled_decisions

        # -- 7. the third decide has nothing left to judge -------------
        settled = _run_decide(cli, capsys, tmp_path, clean_root)
        assert settled['written'] == [] and settled['looks'] == []
    finally:
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'remove',
                        '--force', str(clean_root)], check=True,
                       capture_output=True)
        owner.close()
        app.close()


@NATIVE
@pytest.mark.parametrize('unpaired', (0, 2), ids=('no-shadow-rows', 'all-unpaired'))
def test_native_club_candidate_without_pairs(tmp_path, capsys, monkeypatch, unpaired):
    from polymarket_alpha_lab.papersim import cli, store_psycopg

    lineage = LINEAGE
    owner_dsn, app_dsn = _migrated_app()
    candidate = candidate_spec()
    candidate_sha = candidate.spec_sha256()
    baseline = SOCCER_FROZEN.baseline_spec()
    with _connect(app_dsn) as app:
        store_psycopg.initialize_registry(
            app, families=[SOCCER_FROZEN], actor='op', code_commit='c' * 40,
            effective_at=INIT)
        _register_candidate(app, lineage, candidate)
        role_events = store_psycopg.load_all_role_events(app)[lineage]
        assert store_psycopg.load_promotion_decisions(app, lineage) == []
        scan_params = _provider_params(app, 'club-soccer/over_under')

    if unpaired:
        with _connect(owner_dsn, autocommit=False) as owner:
            scan_id = _seed_scan(owner, scan_key='zero-pairs', day=_at(3), params=scan_params)
            for index in range(unpaired):
                _seed_pair(owner, scan_id=scan_id, index=index, start=_at(3),
                           observed=None, yes_won=True, shadow_spec=candidate_sha,
                           priced_version=baseline.model_version(),
                           priced_probability=0.5, shadow_is_better=True,
                           role_at_prediction='CANDIDATE')

    config = tmp_path / 'scan-config.json'
    config.write_text(json.dumps({
        'team': 'paper', 'operator': 'unattended',
        'queues': [{'queue': 'q1', 'lineage': lineage, 'data_source_version': 'v1'}],
    }), encoding='utf-8')
    args = ['--lineage', lineage, '--config', str(config), '--dsn', app_dsn]

    exit_code = cli.main(['optimize', 'score', *args])
    output = capsys.readouterr()
    assert (exit_code, output.err) == (0, '')
    score = json.loads(output.out)
    assert score['forward_comparison'] == {
        'status': 'available', 'reason': None, 'scan_lineage': 'club-soccer/over_under',
        'registered_active_spec_sha256': baseline.spec_sha256(),
        'registered_active_model_version': baseline.model_version(),
        'pricing_spec_sha256': baseline.spec_sha256(), 'pricing_model_version': baseline.model_version(),
        'fallback_reason': None,
    }
    pair, = score['pairs']
    assert pair['pair'] == 'candidate-vs-active'
    assert pair['spec'] == candidate_sha
    assert pair['comparator'] == baseline.spec_sha256()
    assert pair['stats'] == {
        'n_markets': 0, 'n_event_clusters': 0, 'n_complete_blocks': 0,
        'span_days': 0, 'watermark': None, 'paired_mean': None,
        'ci95': [None, None], 'ci99': [None, None], 'close_dev_markets': 0,
        'close_dev_gap': None, 'unpaired': unpaired,
        'close_dev_active_entries': 0, 'close_dev_n_candidate': 0, 'close_dev_n_active': 0,
        'close_dev_missing_candidate': 0, 'close_dev_missing_active': 0,
        'close_dev_missing_candidate_fill': 0, 'close_dev_missing_active_fill': 0,
        'close_dev_candidate_mean': None, 'close_dev_active_mean': None,
    }
    assert pair['next_look']['due'] is False
    _, _, threshold = cli._optimize_threshold(lineage)
    assert pair['next_look']['target_markets'] == threshold.min_markets
    assert pair['next_look']['markets_to_next_look'] == threshold.min_markets
    assert pair['next_look']['evidence_ordinal'] == 0
    status = _read_optimizer(capsys, app_dsn, 'status', '--lineage', lineage, '--config', str(config))
    status_lineage, = status['lineages']
    assert status_lineage['initialized'] is True
    assert status_lineage['forward_comparison'] == score['forward_comparison']
    assert status_lineage['pairs'] == score['pairs']

    def unexpected_forward_write(*args, **kwargs):
        pytest.fail('a forward look reached the writer before it was due')

    monkeypatch.setattr(cli, '_optimize_write_decision', unexpected_forward_write)
    for _ in range(2):
        exit_code = cli.main(['optimize', 'decide', *args])
        output = capsys.readouterr()
        assert (exit_code, output.err) == (0, '')
        assert json.loads(output.out) == {
            'lineage': lineage, 'written': [],
            'looks': [{'class': 'FORWARD', 'spec': candidate_sha, 'due': False,
                       'n_markets': 0, 'key_counter': 1, 'evidence_ordinal': 0,
                       'blocked_looks_consumed': 0}],
        }
        with _connect(app_dsn) as app:
            assert store_psycopg.load_promotion_decisions(app, lineage) == []
            assert store_psycopg.load_all_role_events(app)[lineage] == role_events
            roles = store_psycopg.load_lineage_roles(app, lineage)
            assert roles.active == baseline.spec_sha256()
            assert roles.candidates == (candidate_sha,)
            assert roles.guard is None


def _read_optimizer(capsys, app_dsn, *args):
    from polymarket_alpha_lab.papersim import cli

    code = cli.main(['optimize', *args, '--dsn', app_dsn])
    output = capsys.readouterr()
    assert (code, output.err) == (0, '')
    return json.loads(output.out)


def _read_business_state(app):
    from polymarket_alpha_lab.papersim import store_psycopg

    with app.cursor() as cursor:
        cursor.execute("""
            SELECT (SELECT count(*) FROM papersim.scan_runs),
                   (SELECT count(*) FROM papersim.candidates),
                   (SELECT count(*) FROM papersim.entries),
                   (SELECT count(*) FROM papersim.shadow_predictions),
                   (SELECT count(*) FROM papersim.candidate_price_evidence),
                   (SELECT count(*) FROM papersim.market_outcomes)
            """)
        business_counts = cursor.fetchone()
    roles = store_psycopg.load_all_role_events(app)
    return business_counts, roles, {
        lineage: store_psycopg.load_promotion_decisions(app, lineage) for lineage in roles
    }


@NATIVE
def test_native_status_and_score_report_scan_identity(capsys, monkeypatch):
    from polymarket_alpha_lab.papersim import cli, store_psycopg
    from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY
    from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import (
        SOCCER_RECALIB, spec_for_params,
    )
    from polymarket_alpha_lab.papersim.optimize.provider import RegistryProvider
    from polymarket_alpha_lab.papersim.store import PapersimStoreError

    owner_dsn, app_dsn = _migrated_app()
    frozen = SOCCER_FROZEN.baseline_spec()
    recalib = SOCCER_RECALIB.baseline_spec()
    candidate = candidate_spec()
    recalib_candidate = spec_for_params({'half_life_days': 60.0})
    alias = replace(candidate, params={'kappa': 0.35}, legacy_model_version=frozen.model_version())
    nba = NBA_PACE_EFFICIENCY.baseline_spec()
    nba_candidate = replace(nba, params={**nba.params, 'fixture': 'another-spec'})
    recalib_lineage = 'club-soccer-recalib/match_winner'
    nba_lineage = 'nba-pace-efficiency/totals'
    btts_lineage = 'club-soccer-frozen/btts'
    alias_lineage = 'club-soccer-frozen/team_total'
    with _connect(app_dsn) as app:
        empty_before = _read_business_state(app)

    empty = _read_optimizer(capsys, app_dsn, 'status', '--lineage', LINEAGE)
    unregistered, = empty['lineages']
    assert unregistered['initialized'] is False
    assert unregistered['roles']['active'] is None
    assert unregistered['fallback_spec_sha256'] == frozen.spec_sha256()
    assert unregistered['pairs'] == [] and unregistered['forward_sample_count'] is None
    assert unregistered['forward_comparison']['status'] == 'unavailable'
    assert unregistered['forward_comparison']['reason'] == 'registry_not_initialized'
    assert unregistered['forward_comparison']['fallback_reason'] == 'registry_empty'
    for lineage in (LINEAGE, 'unknown/target'):
        code = cli.main(['optimize', 'score', '--lineage', lineage, '--dsn', app_dsn])
        output = capsys.readouterr()
        assert (code, output.out) == (1, '')
        assert output.err == 'optimize score: FAIL: papersim_optimize_lineage_unknown\n'

    with _connect(app_dsn) as app:
        assert _read_business_state(app) == empty_before
        store_psycopg.initialize_registry(
            app, families=[SOCCER_FROZEN, SOCCER_RECALIB, NBA_PACE_EFFICIENCY],
            actor='op', code_commit='c' * 40, effective_at=INIT)
        _register_candidate(app, LINEAGE, candidate)
        _register_candidate(app, recalib_lineage, recalib_candidate)
        _register_candidate(app, nba_lineage, nba_candidate)
        _register_candidate(app, btts_lineage, candidate)
        _register_candidate(app, alias_lineage, alias)

    scan_lineage = 'club-soccer/match_winner'
    with _connect(app_dsn) as app:
        scan_params = _provider_params(app, scan_lineage)
    with _connect(owner_dsn, autocommit=False) as owner:
        for lineage, promoted in ((btts_lineage, candidate), (alias_lineage, alias)):
            _seed_role_history(
                owner, lineage, _at(3),
                ((frozen.spec_sha256(), 'GUARD'), (promoted.spec_sha256(), 'ACTIVE')))
        scan_id = _seed_scan(owner, scan_key='registered-and-priced', day=_at(4), params=scan_params)
        shadowed = _seed_pair(
            owner, scan_id=scan_id, index=0, start=_at(4), observed=_at(5), yes_won=True,
            shadow_spec=recalib_candidate.spec_sha256(), priced_version=frozen.model_version(),
            priced_probability=0.5, shadow_is_better=True, role_at_prediction='CANDIDATE',
            market_type='match_winner')

    with _connect(app_dsn) as app:
        _register_candidate(app, btts_lineage, alias, effective_at=_at(4))
        _register_candidate(app, alias_lineage, candidate, effective_at=_at(4))
        before = _read_business_state(app)
        with app.cursor() as cursor:
            cursor.execute("""
                SELECT r.params #>> ARRAY['provider','specs',%s::text,'active_spec_sha256'],
                       c.model_version
                FROM papersim.scan_runs r JOIN papersim.candidates c USING (scan_id)
                WHERE c.candidate_id = %s
                """, (scan_lineage, shadowed))
            assert cursor.fetchone() == (frozen.spec_sha256(), frozen.model_version())

    build_provider = RegistryProvider.from_connection
    provider_reads = []

    def observe_provider(cls, conn, **kwargs):
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_setting('transaction_isolation'), "
                           "current_setting('transaction_read_only')")
            provider_reads.append(cursor.fetchone())
        return build_provider(conn, **kwargs)

    monkeypatch.setattr(RegistryProvider, 'from_connection', classmethod(observe_provider))
    status = _read_optimizer(capsys, app_dsn, 'status')
    assert provider_reads == [('repeatable read', 'on')]
    assert isinstance(status['lineages'], list)
    assert status['shadow_predictions'] == 'paired_by_lineage'
    assert status['freeze'] == {'global_frozen': False, 'frozen_lineages': []}
    by_lineage = {row['lineage']: row for row in status['lineages']}
    assert by_lineage[LINEAGE]['forward_comparison']['status'] == 'available'
    assert by_lineage[LINEAGE]['pairs'][0]['stats']['n_markets'] == 0
    for lineage, registered, expected_candidate, reason, mapped in (
            (recalib_lineage, recalib, recalib_candidate, 'pricing_spec_mismatch', scan_lineage),
            (btts_lineage, candidate, alias, 'pricing_spec_mismatch', 'club-soccer/btts'),
            (alias_lineage, alias, candidate, 'pricing_spec_mismatch', 'club-soccer/team_total'),
            (nba_lineage, nba, nba_candidate, 'scan_lineage_unsupported', None)):
        provider_reads.clear()
        filtered = _read_optimizer(capsys, app_dsn, 'status', '--lineage', lineage)
        assert filtered['lineages'] == [by_lineage[lineage]]
        score = _read_optimizer(capsys, app_dsn, 'score', '--lineage', lineage)
        assert provider_reads == [('repeatable read', 'on'), ('repeatable read', 'on')]
        comparison = score['forward_comparison']
        assert comparison == by_lineage[lineage]['forward_comparison']
        assert comparison == {
            'status': 'unavailable', 'reason': reason, 'scan_lineage': mapped,
            'registered_active_spec_sha256': registered.spec_sha256(),
            'registered_active_model_version': registered.model_version(),
            'pricing_spec_sha256': frozen.spec_sha256() if mapped else None,
            'pricing_model_version': frozen.model_version() if mapped else None,
            'fallback_reason': None,
        }
        assert score['pairs'] == by_lineage[lineage]['pairs']
        assert score['pairs'][-1] == {
            'pair': 'candidate-vs-active', 'spec': expected_candidate.spec_sha256(),
            'comparator': registered.spec_sha256(), 'stats': None, 'next_look': None,
        }
        if lineage in (btts_lineage, alias_lineage):
            assert score['pairs'][0] == {
                'pair': 'active-vs-guard', 'spec': registered.spec_sha256(),
                'comparator': frozen.spec_sha256(), 'stats': None,
            }

    load_spec = store_psycopg.load_model_spec

    for error, fallback in (
            (PapersimStoreError('papersim_model_spec_roundtrip_mismatch'), 'spec_checksum_mismatch'),
            (RuntimeError('fixture registry read failure'), 'registry_read_failed')):
        def unreadable_baseline(conn, digest, failure=error):
            if digest == frozen.spec_sha256():
                raise failure
            return load_spec(conn, digest)

        monkeypatch.setattr(store_psycopg, 'load_model_spec', unreadable_baseline)
        degraded_status = _read_optimizer(capsys, app_dsn, 'status', '--lineage', LINEAGE)
        degraded = _read_optimizer(capsys, app_dsn, 'score', '--lineage', LINEAGE)
        assert degraded['forward_comparison'] == degraded_status['lineages'][0]['forward_comparison']
        assert degraded['forward_comparison'] == {
            'status': 'unavailable', 'reason': 'pricing_identity_unavailable',
            'scan_lineage': 'club-soccer/over_under',
            'registered_active_spec_sha256': frozen.spec_sha256(),
            'registered_active_model_version': None,
            'pricing_spec_sha256': frozen.spec_sha256(), 'pricing_model_version': frozen.model_version(),
            'fallback_reason': fallback,
        }
        assert degraded['active_queue_entries'] is None
        assert degraded['pairs'] == [{
            'pair': 'candidate-vs-active', 'spec': candidate.spec_sha256(),
            'comparator': frozen.spec_sha256(), 'stats': None, 'next_look': None,
        }]
    with _connect(app_dsn) as app:
        assert _read_business_state(app) == before


def _read_clv(app, since, candidate, active):
    from polymarket_alpha_lab.papersim import cli

    with app.transaction(), app.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return cli._optimize_forward_stats(
            app, LINEAGE, shadow_spec_sha256=candidate.spec_sha256(),
            priced_model_version=active.model_version(), since=since, judged='shadow',
            with_close_deviation=True, stats_spec=candidate.spec_sha256(),
            stats_comparator=active.spec_sha256())


@NATIVE
def test_native_clv_one_cent_boundary_on_numeric_fills():
    from polymarket_alpha_lab.papersim import cli, store_psycopg
    from polymarket_alpha_lab.papersim.optimize import policy

    owner_dsn, app_dsn = _migrated_app()
    active = SOCCER_FROZEN.baseline_spec()
    candidate = candidate_spec()
    with _connect(app_dsn) as app, _connect(owner_dsn, autocommit=False) as owner:
        store_psycopg.initialize_registry(
            app, families=[SOCCER_FROZEN], actor='op', code_commit='c' * 40,
            effective_at=INIT)
        _register_candidate(app, LINEAGE, candidate)
        scan_params = _provider_params(app, 'club-soccer/over_under')
        facts = cli._optimize_lineage_facts(app, LINEAGE, [])['facts']
        threshold = policy.classify_threshold('club-soccer-frozen', 'over_under')
        price = Decimal('0.53')
        for day, candidate_fill, expected_gap, blocked in (
                (3, '0.49', '0.01', False),
                (5, '0.511', '-0.011', True),
                (7, '0.51', '-0.01', True),
                (9, '0.5099999999999999999999999999999999',
                 '-0.0099999999999999999999999999999999', False)):
            start = _at(day)
            observed = start + timedelta(days=1)
            scan_id = _seed_scan(owner, scan_key=f'numeric-{day}', day=start, params=scan_params)
            for index in range(30):
                candidate_id = _seed_pair(
                    owner, scan_id=scan_id, index=index, start=start, observed=observed,
                    yes_won=True, shadow_spec=candidate.spec_sha256(),
                    priced_version=active.model_version(), priced_probability=0.4,
                    shadow_is_better=True, role_at_prediction='CANDIDATE', conclusion='REJECTED',
                    shadow_gate=True, shadow_fill=Decimal(candidate_fill), shadow_probability=0.5)
                _seed_close(owner, candidate_id, 'YES', observed, last=price)
            if day == 3:
                owner.commit()
                candidates_only = _read_clv(app, start, candidate, active)
                assert candidates_only.close_dev_markets == 30
                assert candidates_only.close_dev_candidate.n_prices == 30
                assert candidates_only.close_dev_active.n_prices == 0
                assert candidates_only.close_dev_gap is None
                assert policy._close_deviation_check(candidates_only) == (True, True, False)
            for index in (30, 31):
                candidate_id = _seed_pair(
                    owner, scan_id=scan_id, index=index, start=start, observed=None,
                    yes_won=True, shadow_spec=None, priced_version=active.model_version(),
                    priced_probability=0.4, shadow_is_better=True, role_at_prediction='CANDIDATE')
                _seed_entry(owner, candidate_id, Decimal('0.50'), start + timedelta(hours=2))
                _seed_close(owner, candidate_id, 'YES', observed, last=price)
            owner.commit()

            stats = _read_clv(app, start, candidate, active)
            assert stats.n_markets == 30 and stats.unpaired == 0
            assert stats.close_dev_markets == 30
            assert stats.close_dev_candidate.n_prices == 30 and stats.close_dev_active.n_prices == 2
            assert stats.close_dev_gap == float(expected_gap)
            report = cli._optimize_stats_dict(stats)
            assert Decimal(report['close_dev_gap']) == Decimal(expected_gap)
            assert Decimal(report['close_dev_active_mean']) == Decimal('0.03')
            assert Fraction(report['close_dev_candidate_mean']) == Fraction(price) - Fraction(candidate_fill)
            verdict = policy.decide_forward(facts, stats, look_number=1, evidence_ordinal=0,
                                            threshold=threshold, as_of=observed)
            assert verdict.conclusion == ('CONTINUE' if blocked else 'PROMOTE')
            assert verdict.metrics['close_dev_violation'] is blocked
            assert verdict.metrics['close_dev_flagged_only'] is False
            assert verdict.metrics['close_dev_gap'] == report['close_dev_gap']
            if day == 3:
                former_gap = abs(price - Decimal('0.5')) - abs(price - Decimal('0.4'))
                assert former_gap <= Decimal(str(policy.CLOSE_DEVIATION_MIN_GAP))
                assert Decimal(report['close_dev_gap']) > 0


def _clv_fixture_readback(app, pairs, *, shadow):
    """Aggregate the explicitly selected fixture prices using native NUMERIC."""
    fill_table, row_key, price = (
        ('shadow_predictions', 'shadow_prediction_id', 'hypothetical_fill_price')
        if shadow else ('entries', 'entry_id', 'price'))
    fill_ids, close_ids = map(list, zip(*pairs, strict=True))
    with app.cursor() as cursor:
        cursor.execute(f"""
            SELECT count(*), count(COALESCE(p.last, p.mid) - f.{price}),
                   count(*) FILTER (WHERE COALESCE(p.last, p.mid) IS NULL),
                   count(*) FILTER (WHERE f.{price} IS NULL),
                   sum(COALESCE(p.last, p.mid) - f.{price})
            FROM unnest(%s::bigint[], %s::bigint[]) AS chosen(fill_id, close_id)
            JOIN papersim.{fill_table} f ON f.{row_key} = chosen.fill_id
            LEFT JOIN papersim.candidate_price_evidence p ON p.evidence_id = chosen.close_id
            """, (fill_ids, close_ids))
        return cursor.fetchone()


@NATIVE
def test_native_clv_uses_own_side_and_complete_prices_per_population(capsys):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize import policy

    owner_dsn, app_dsn = _migrated_app()
    active = SOCCER_FROZEN.baseline_spec()
    candidate = candidate_spec()
    candidate_sha = candidate.spec_sha256()
    other_spec = replace(candidate, params={'kappa': 0.35})
    start, observed = _at(4), _at(5)
    evaluated_at = start + timedelta(hours=2)
    with _connect(app_dsn) as app, _connect(owner_dsn, autocommit=False) as owner:
        store_psycopg.initialize_registry(
            app, families=[SOCCER_FROZEN], actor='op', code_commit='c' * 40,
            effective_at=INIT)
        _register_candidate(app, LINEAGE, candidate)
        store_psycopg.register_model_spec(app, other_spec, source='manual', code_commit='c' * 40)
        scan_params = _provider_params(app, 'club-soccer/over_under', 'club-soccer/btts')
        scan_id = _seed_scan(owner, scan_key='clv-populations', day=start, params=scan_params)

        def seed_row(index, *, scan=scan_id, when=start, type_='over_under',
                     side='YES', yes_won=True, result=None, conclusion='ACCEPTED',
                     priced_version=active.model_version()):
            return _seed_pair(
                owner, scan_id=scan, index=index, start=when, observed=result,
                yes_won=yes_won, shadow_spec=None, priced_version=priced_version,
                priced_probability=0.5, shadow_is_better=True,
                role_at_prediction='CANDIDATE', market_type=type_, side=side,
                conclusion=conclusion)

        candidate_pairs = []
        for index in range(29):
            side = {0: 'NO', 1: 'YES-basket', 2: 'NO-basket'}.get(index, 'YES')
            candidate_id = seed_row(
                index, side=side, yes_won=side.startswith('YES'),
                result=observed if index in (1, 2) else None,
                conclusion='ACCEPTED' if index == 2 else 'REJECTED')
            fill = {0: '0.45', 1: '0.48', 2: '0.51', 3: '0.51'}.get(index)
            probability = {1: Decimal('0'), 2: Decimal('0.6')}.get(index, Decimal('0.5'))
            shadow_id = _seed_shadow(
                owner, candidate_id, candidate_sha, evaluated_at, 'CANDIDATE',
                probability, gate_pass=True, fill=Decimal(fill) if fill is not None else None)
            close_id = None
            if index == 0:
                shadow_id = _seed_shadow(
                    owner, candidate_id, candidate_sha, evaluated_at + timedelta(seconds=1),
                    'CANDIDATE', probability, gate_pass=True, fill=Decimal('0.48'))
                _seed_shadow(owner, candidate_id, other_spec.spec_sha256(), evaluated_at,
                             'GUARD', Decimal('0.5'), gate_pass=True, fill=Decimal('0.99'))
                _seed_close(owner, candidate_id, side, observed, last=Decimal('0.25'))
                close_id = _seed_close(owner, candidate_id, side, observed, last=Decimal(0),
                                       mid=Decimal('0.4'))
                _seed_close(owner, candidate_id, side, observed - timedelta(hours=1),
                            last=Decimal('0.1'))
                _seed_close(owner, candidate_id, 'YES', observed + timedelta(hours=1),
                            last=Decimal('0.9'))
            elif index == 1:
                close_id = _seed_close(owner, candidate_id, side, observed, last=Decimal(1),
                                       mid=Decimal('0.57'))
            elif index == 2:
                close_id = _seed_close(owner, candidate_id, side, observed, mid=Decimal('0.531'))
                _seed_close(owner, candidate_id, side, observed + timedelta(hours=1),
                            bid=Decimal('0.51'))
            elif index == 3:
                _seed_close(owner, candidate_id, side, observed, bid=Decimal('0.51'))
            elif index == 4:
                close_id = _seed_close(owner, candidate_id, side, observed, last=Decimal('0.53'))
            candidate_pairs.append((shadow_id, close_id))

        active_pairs = []
        for index in range(7):
            side = 'NO' if index in (5, 6) else 'YES'
            candidate_id = seed_row(index + 100, side=side)
            entry_id = _seed_entry(owner, candidate_id, Decimal('0.50'), evaluated_at)
            if index == 0:
                _seed_shadow(owner, candidate_id, candidate_sha, evaluated_at, 'CANDIDATE',
                             None, gate_pass=False, fill=None, refusal='fixture shadow refusal')
            close_id = None
            if index < 6:
                close_id = _seed_close(owner, candidate_id, side, observed,
                                       last=Decimal('0.532' if index == 5 else '0.53'))
            if side == 'NO':
                _seed_close(owner, candidate_id, 'YES', observed + timedelta(hours=1),
                            last=Decimal('0.99'))
            active_pairs.append((entry_id, close_id))

        wrong_params = json.loads(json.dumps(scan_params))
        wrong_params['provider']['specs']['club-soccer/over_under']['active_spec_sha256'] = (
            other_spec.spec_sha256())
        wrong_spec_scan = _seed_scan(owner, scan_key='clv-wrong-spec', day=start, params=wrong_params)
        other_target_scan = _seed_scan(owner, scan_key='clv-wrong-target', day=start,
                                       params=_provider_params(app, 'club-soccer/btts'))
        for excluded in (
                seed_row(200, type_='btts'),
                seed_row(201, when=REGISTERED - timedelta(days=1)),
                seed_row(202, scan=wrong_spec_scan),
                seed_row(203, scan=other_target_scan),
                seed_row(204, priced_version=other_spec.model_version())):
            _seed_entry(owner, excluded, Decimal('0.50'), evaluated_at)
            _seed_close(owner, excluded, 'YES', observed, last=Decimal('0.99'))
        owner.commit()

        annotated = _read_clv(app, REGISTERED, candidate, active)
        assert annotated.close_dev_markets == 29
        assert annotated.close_dev_gap == policy.CLOSE_DEVIATION_MIN_GAP
        assert policy._close_deviation_check(annotated) == (True, True, False)

        missing = seed_row(29, conclusion='REJECTED')
        missing_shadow_id = _seed_shadow(
            owner, missing, candidate_sha, evaluated_at, 'CANDIDATE',
            Decimal('0.5'), gate_pass=True, fill=None)
        candidate_pairs.append((missing_shadow_id, None))
        owner.commit()

        scored = _read_optimizer(capsys, app_dsn, 'score', '--lineage', LINEAGE)
        report = scored['pairs'][0]['stats']
        stats = _read_clv(app, REGISTERED, candidate, active)
        assert policy._close_deviation_check(stats) == (False, False, True)
        assert report['n_markets'] == report['n_event_clusters'] == 1
        assert report['watermark'] == observed.isoformat()
        assert report['unpaired'] == annotated.unpaired + 1 == 31
        assert report['paired_mean'] == repr(float(Decimal('0.5').ln() - Decimal('0.6').ln()))
        candidate_truth = _clv_fixture_readback(app, candidate_pairs, shadow=True)
        active_truth = _clv_fixture_readback(app, active_pairs, shadow=False)
        assert candidate_truth == (30, 3, 26, 26, Decimal('0.061'))
        assert active_truth == (7, 6, 1, 0, Decimal('0.182'))
        for side, truth, sample, count_key in (
                ('candidate', candidate_truth, stats.close_dev_candidate, 'close_dev_markets'),
                ('active', active_truth, stats.close_dev_active, 'close_dev_active_entries')):
            eligible, n_prices, missing_close, missing_fill, total = truth
            assert report[count_key] == eligible
            assert report[f'close_dev_n_{side}'] == n_prices
            assert report[f'close_dev_missing_{side}'] == missing_close
            assert report[f'close_dev_missing_{side}_fill'] == missing_fill
            reference_mean = Fraction(total) / n_prices
            assert sample.mean == reference_mean
            assert abs(Fraction(report[f'close_dev_{side}_mean']) - reference_mean) < Fraction(1, 10**60)
        assert stats.close_dev_candidate.gap_from(stats.close_dev_active) == Fraction('-0.01')
        assert report['close_dev_gap'] == '-0.01'
        expanded = _read_optimizer(capsys, app_dsn, 'score', '--lineage', LINEAGE,
                                   '--close-price-evidence')
        assert datetime.fromisoformat(expanded.pop('read_transaction_started_at')).utcoffset() is not None
        audit = expanded['pairs'][0].pop('close_price_evidence')
        assert expanded == scored
        assert audit['rule_version'] == policy.RULE_VERSION
        assert datetime.fromisoformat(audit['since']) == REGISTERED
        assert audit['missing_close_and_fill'] == {'candidate': 25, 'active': 0}
        rows = audit['rows']
        ids = [row['candidate_id'] for row in rows]
        assert ids == sorted(set(ids))
        for member, fill, expected_pairs, truth in (
                ('shadow_prediction_id', 'shadow_fill', candidate_pairs, candidate_truth),
                ('entry_id', 'entry_fill', active_pairs, active_truth)):
            population = [row for row in rows if row[member] is not None]
            measured = {(r[member], r['close']['evidence_id'] if r['close'] else None)
                        for r in population}
            assert measured == set(expected_pairs)
            priced = [r for r in population if r['close'] is not None and r[fill] is not None]
            assert (len(population), len(priced)) == truth[:2]
            assert sum(Fraction(r['close']['price']) - Fraction(r[fill]) for r in priced) == truth[4]
        selected = {r['close']['evidence_id']: r['close'] for r in rows if r['close']}
        assert selected[candidate_pairs[0][1]]['price_field'] == 'last'
        assert Decimal(selected[candidate_pairs[0][1]]['price']) == 0
        assert selected[candidate_pairs[2][1]]['price_field'] == 'mid'
        member = next(r for r in rows if r['shadow_prediction_id'] == candidate_pairs[2][0])
        shared_entry = _seed_entry(owner, member['candidate_id'], Decimal('0.49'), evaluated_at)
        owner.commit()
        shared = _read_optimizer(capsys, app_dsn, 'score', '--lineage', LINEAGE,
                                 '--close-price-evidence')['pairs'][0]
        shared_rows = shared['close_price_evidence']['rows']
        shared_row, = [r for r in shared_rows if r['candidate_id'] == member['candidate_id']]
        assert len(shared_rows) == len(rows)
        assert shared_row['shadow_prediction_id'] == member['shadow_prediction_id']
        assert shared_row['entry_id'] == shared_entry
        assert shared_row['close'] == member['close']
        assert Decimal(shared_row['entry_fill']) == Decimal('0.49')
        assert shared['stats']['close_dev_n_candidate'] == candidate_truth[1]
        assert shared['stats']['close_dev_n_active'] == active_truth[1] + 1
