"""Actual managed CLI turns, cooperative stop/restart and lost child process.

Fresh isolated native PostgreSQL, existing synthetic crypto fixtures only.
"""
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

import pytest

from polymarket_alpha_lab import research_dispatch_cli as cli
from polymarket_alpha_lab.project_postgres import files
from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_dispatch import ResearchBatch
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from polymarket_alpha_lab.research_model_budget import ModelCallBudget
from polymarket_alpha_lab.research_capture_psycopg import ResearchCaptureConflict
from tests.test_project_postgres_dispatch_native import prepared
from tests.test_team_research_cross_source import Model

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'


def allowance(name, requests, calls):
    return ModelCallBudget(name, 'synthetic-provider', requests[0].model_id, 'USD', calls*100,
        100, calls, 100000, 1024, datetime.now(UTC)+timedelta(hours=1),
        tuple((r.record_id, r.content_sha256) for r in requests), 'a'*64, cost_bound_attested=True)


def run_args(turn, *, rotation='operator', batches=('operator-btc', 'operator-eth'),
             budget='operator-budget', tasks=2):
    return ['run-turn', '--rotation-id', rotation, '--turn-id', turn,
        *sum((['--batch-id', name] for name in batches), []), '--budget-id', budget,
        '--max-tasks', str(tasks), '--max-workers', '1', '--allow-model-calls']


def invoke(root, args, **kwargs):
    output = io.StringIO()
    with redirect_stdout(output): code = cli.main(args, default_root=root, **kwargs)
    result = json.loads(output.getvalue())
    assert 'raw_json' not in output.getvalue() and 'probability_yes' not in output.getvalue()
    return code, result


_CRASH = r'''
import os,sys
from pathlib import Path
from polymarket_alpha_lab.research_dispatch_cli import main

def factory(_):os._exit(86)
raise SystemExit(main(['run-turn','--rotation-id','crash-operator','--turn-id','crash-1',
    '--batch-id','operator-crash','--budget-id','operator-crash-budget',
    '--max-tasks','1','--max-workers','1','--allow-model-calls'],
    default_root=Path(sys.argv[1]),model_factory=factory))
'''


@pytest.mark.skipif(not ENABLED, reason='explicit native operator CLI proof is opt-in')
def test_operator_rounds_stop_restart_failures_and_process_loss(tmp_path, monkeypatch):
    prefix = Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'): monkeypatch.delenv(key)
    parent = tmp_path
    if os.name == 'nt':
        parent = Path(os.environ['RUNNER_TEMP'])/('pal-operator-'+uuid.uuid4().hex)
        files.private_directory(parent, create=True)
    root = parent/'Operator With Spaces'; root.mkdir()
    (root/'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT/'database', root/'database')
    shutil.copytree(ROOT/'supabase/migrations', root/'supabase/migrations')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    import_runtime_directory(root, prefix)
    db = ProjectPostgres(root)
    try:
        assert db.initialize(port=port)['migrations_applied'] == 67
        requests = tuple(prepared(1100+i, 'crypto_btc' if i%2 == 0 else 'crypto_eth') for i in range(6))
        with db.session() as session:
            identity = db._state()
            session.enqueue_research_batch(batch=ResearchBatch('operator-btc', requests[::2]), allow_queue_write=True)
            session.enqueue_research_batch(batch=ResearchBatch('operator-eth', requests[1::2]), allow_queue_write=True)
            session.create_model_budget(policy=allowance('operator-budget', requests, 20), allow_budget_write=True)
        # The actual standalone script reads through its own source from an
        # unrelated working directory; it never supplies a production client.
        child = subprocess.run([sys.executable, '-I', str(ROOT/'scripts/manage_research_tasks.py'),
            '--root', str(root), 'inspect-budget', '--budget-id', 'operator-budget'],
            cwd=parent, capture_output=True, text=True, encoding='utf-8',
            env=files.clean_environment(), timeout=120)
        assert child.returncode == 0, (child.stdout, child.stderr)
        assert json.loads(child.stdout)['result']['reserved_calls'] == 0
        child = subprocess.run([sys.executable, '-I', str(ROOT/'scripts/manage_research_tasks.py'),
            '--root', str(root), *run_args('standalone-blocked')],
            cwd=parent, capture_output=True, text=True, encoding='utf-8',
            env=files.clean_environment(), timeout=120)
        assert child.returncode == 2, (child.stdout, child.stderr)
        assert json.loads(child.stdout)['operation_entered'] is False
        with db.session() as session:
            assert session.inspect_research_turn(rotation_id='operator', turn_id='standalone-blocked') is None
        made = []
        def factory(team):
            made.append(team)
            return Model()
        def forbidden(_): raise AssertionError('unexpected synthetic client construction')
        code, first = invoke(root, run_args('round-1'), model_factory=factory)
        assert code == 0 and first['result']['execution_invocations'] == 2
        assert made == ['crypto_btc', 'crypto_eth']
        code, budget = invoke(root, ['inspect-budget', '--budget-id', 'operator-budget'])
        assert code == 0 and budget['result']['reserved_calls'] == 6
        code, original = invoke(root, ['inspect-turn', '--rotation-id', 'operator', '--turn-id', 'round-1'])
        assert code == 0 and original['result'] == first['result']['turn']
        code, replay = invoke(root, run_args('round-1'), model_factory=forbidden)
        assert code == 0 and replay['result']['status'] == 'turn_already_reserved'
        assert replay['result']['execution_invocations'] == 0 and len(made) == 2
        control = ResearchDispatchStop()
        def stop_after_admission(team):
            control.request_stop()
            return factory(team)
        code, stopped = invoke(root, run_args('round-2'), model_factory=stop_after_admission, stop=control)
        assert code == 130 and stopped['result']['stop_requested'] is True
        assert stopped['result']['execution_invocations'] == 1 and len(made) == 3
        # The managed sessions have stopped/released their private instance.
        db.down()
        db = ProjectPostgres(root)
        assert db.status()['instance_id'] == identity['instance_id']
        # Cursor has passed the not-admitted B1 slot; A2 fails, B2 completes.
        failures = []
        def mixed(team):
            if team == 'crypto_btc':
                failures.append(team)
                raise RuntimeError('synthetic factory failure')
            return factory(team)
        code, mixed_run = invoke(root, run_args('round-3'), model_factory=mixed)
        assert code == 1 and len(failures) == 1
        assert [a['execution']['research_status'] for a in mixed_run['result']['attempts']] == ['failed', 'completed']
        code, resumed = invoke(root, run_args('round-4'), model_factory=factory)
        assert code == 0 and resumed['result']['execution_invocations'] == 1
        assert len(made) == 5
        for name in ('operator-btc', 'operator-eth'):
            code, found = invoke(root, ['inspect-batch', '--batch-id', name])
            assert code == 0 and found['result']['state_counts'] == dict(pending=0, expired=0, incomplete=0, captured=3)
        code, budget = invoke(root, ['inspect-budget', '--budget-id', 'operator-budget'])
        assert code == 0 and budget['result']['reserved_calls'] == 16  # 5*3 + one failed factory.
        # A new empty turn still writes an immutable selection receipt. Fail
        # ONLY the output after that real commit; never call a synthetic model
        # again or change the original turn ID to manufacture a successful run.
        output_turn = 'output-failure-proof'
        code, absent = invoke(root, ['inspect-turn', '--rotation-id', 'operator', '--turn-id', output_turn])
        assert code == 3 and absent['result'] is None
        witnessed, short_writes = [], []
        class ShortOutput:
            def write(self, text):
                witnessed.append(json.loads(text))
                assert db.status()['status'] == 'stopped'
                short_writes.append(len(text) - 1)
                return short_writes[-1]
            def flush(self):
                pytest.fail('incomplete output must not be flushed as a full receipt')
        with redirect_stdout(ShortOutput()):
            output_code = cli.main(run_args(output_turn), default_root=root, model_factory=forbidden)
        assert output_code == 1 and len(witnessed) == len(short_writes) == 1
        assert witnessed[0]['status'] == 'dispatched'
        assert witnessed[0]['result']['execution_invocations'] == 0
        assert witnessed[0]['business_writes_possible'] is True
        code, saved_turn = invoke(root, ['inspect-turn', '--rotation-id', 'operator', '--turn-id', output_turn])
        assert code == 0 and saved_turn['result'] == witnessed[0]['result']['turn']
        code, same_turn = invoke(root, run_args(output_turn), model_factory=forbidden)
        assert code == 0 and same_turn['result']['status'] == 'turn_already_reserved'
        assert same_turn['result']['turn'] == saved_turn['result']
        assert same_turn['result']['execution_invocations'] == 0
        code, retained_budget = invoke(root, ['inspect-budget', '--budget-id', 'operator-budget'])
        assert code == 0 and retained_budget['result']['reserved_calls'] == 16
        assert retained_budget['result']['reserved_micros'] == budget['result']['reserved_micros']
        assert retained_budget['result']['policy_sha256'] == budget['result']['policy_sha256']
        assert len(made) == 5 and len(failures) == 1
        print('native task output: PASS; new empty turn committed, short output failed, exact replay, no new calls')
        with db.session() as session:
            old_record = session.inspect(record_id=requests[0].record_id).record
            crashed = (prepared(1200), prepared(1201, 'crypto_btc'))
            session.enqueue_research_batch(batch=ResearchBatch('operator-crash', crashed), allow_queue_write=True)
            session.create_model_budget(policy=allowance('operator-crash-budget', crashed, 4), allow_budget_write=True)
        child = subprocess.run([sys.executable, '-I', '-c', _CRASH, str(root)],
            capture_output=True, text=True, encoding='utf-8', env=files.clean_environment(), timeout=120)
        assert child.returncode == 86, (child.stdout, child.stderr)
        db.down()
        code, interrupted = invoke(root, ['inspect-batch', '--batch-id', 'operator-crash'])
        assert code == 0 and interrupted['result']['state_counts']['incomplete'] == 1
        assert interrupted['result']['items'][0]['worker_liveness'] == 'unknown'
        code, replay = invoke(root, run_args('crash-1', rotation='crash-operator',
            batches=('operator-crash',), budget='operator-crash-budget', tasks=1), model_factory=forbidden)
        assert code == 0 and replay['result']['execution_invocations'] == 0
        code, resumed = invoke(root, run_args('crash-2', rotation='crash-operator',
            batches=('operator-crash',), budget='operator-crash-budget', tasks=1), model_factory=factory)
        assert code == 0 and resumed['result']['execution_invocations'] == 1
        code, budget = invoke(root, ['inspect-budget', '--budget-id', 'operator-crash-budget'])
        assert code == 0 and budget['result']['reserved_calls'] == 4
        with db.session() as session:
            assert session.inspect(record_id=crashed[0].record_id).status == 'incomplete'
            assert session.inspect(record_id=requests[0].record_id).record == old_record
            with pytest.raises(ResearchCaptureConflict, match='history_incomplete'): session.evaluate()
            assert db._psql(identity, 'SELECT count(*) FROM project_private.migrations;') == '67'
        assert db.status()['instance_id'] == identity['instance_id']
        print('native operator CLI: PASS; two-team rounds, stop/restart, failed result, replay, crash remains incomplete')
    finally:
        if db.status()['status'] != 'stopped': db.down()
