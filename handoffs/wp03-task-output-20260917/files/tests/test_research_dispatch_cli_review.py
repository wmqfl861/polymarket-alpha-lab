"""Separate adversarial self-review of the operator boundary, not external audit."""
from dataclasses import replace
import json

import pytest

from polymarket_alpha_lab import research_dispatch_cli as cli
from polymarket_alpha_lab import research_dispatch_rotation_runner as rotation
from polymarket_alpha_lab import research_model_budget_runner as budgeted
from polymarket_alpha_lab.research_dispatch_rotation import ResearchRotationTurn, StoredResearchRotationTurn
from polymarket_alpha_lab.research_dispatch_rotation_runner import ResearchRotationReport
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from tests.test_research_dispatch_cli import (
    ROOT, NOW, PRIVATE, RUN, READS, managed, invoke, read_value, stored_turn,
)


@pytest.mark.parametrize('value', [0, PRIVATE])
@pytest.mark.parametrize('where', ['error', 'cleanup_error'])
def test_system_exit_is_not_success_or_an_unsanitized_terminal_exit(managed, capsys, value, where):
    managed['value'] = ResearchRotationReport('turn_already_reserved', stored_turn())
    managed[where] = SystemExit(value)
    code, out = invoke(capsys, RUN, model_factory=lambda _: None)
    assert code == 1 and out['status'] == 'failed' and out['result'] is None
    assert out['business_writes_possible'] is True and managed['closed']


@pytest.mark.parametrize('operation,arguments', READS)
def test_output_is_not_emitted_while_session_is_open(managed, capsys, monkeypatch, operation, arguments):
    managed['value'] = read_value(operation)
    original = cli.json.dumps
    def serialize(*a, **kw):
        if type(a[0]) is dict and 'operation' in a[0]: assert managed['closed']
        return original(*a, **kw)
    monkeypatch.setattr(cli.json, 'dumps', serialize)
    code, _ = invoke(capsys, [operation, *arguments])
    assert code == 0


@pytest.mark.parametrize('error', [KeyboardInterrupt(PRIVATE), SystemExit(0), SystemExit(PRIVATE)])
def test_real_executor_drains_and_stops_on_worker_base_exception(managed, capsys, monkeypatch, error):
    batch = read_value('inspect-batch')
    control = ResearchDispatchStop()
    calls = []
    monkeypatch.setattr(rotation, 'inspect_research_turn_with_psycopg', lambda *a, **k: None)
    monkeypatch.setattr(rotation, 'load_research_batch_with_psycopg', lambda *a, **k: batch)
    def reserve(dsn, **kw):
        turn = ResearchRotationTurn(turn_number=1, start_slot=0, **kw)
        return True, StoredResearchRotationTurn(turn, NOW)
    monkeypatch.setattr(rotation, '_reserve', reserve)
    def execute(*a, **kw):
        calls.append(kw)
        raise error
    monkeypatch.setattr(budgeted, 'run_budgeted_research_with_psycopg', execute)
    original = cli._run
    class RealSession:
        def run_research_rotation(self, **kw):
            return rotation.run_research_rotation_with_psycopg('unused-test-seam', **kw)
    def run(session, args, model_factory, stop):
        return original(RealSession(), args, model_factory, stop)
    monkeypatch.setattr(cli, '_run', run)
    code, out = invoke(capsys, RUN, model_factory=lambda _: None, stop=control)
    assert code == (130 if isinstance(error, KeyboardInterrupt) else 1)
    assert control.is_stopped() and managed['closed']
    assert len(calls) == 1 and calls[0]['budget_id'] == 'budget'
    assert out['result'] is None


def test_mutated_run_and_non_receipt_rejected(managed, capsys):
    for value in (object(), {'status': 'success'}, ResearchRotationReport('turn_already_reserved', stored_turn())):
        if type(value) is ResearchRotationReport: object.__setattr__(value, 'status', 'success')
        managed['value'] = value
        code, out = invoke(capsys, RUN, model_factory=lambda _: None)
        assert code == 1 and out['result'] is None


def test_stopped_before_reservation_never_reaches_store_or_client(managed, capsys, monkeypatch):
    control = ResearchDispatchStop(); control.request_stop()
    def forbidden(*a, **kw): pytest.fail('pre-stopped run reached the store')
    monkeypatch.setattr(rotation, 'inspect_research_turn_with_psycopg', forbidden)
    original = cli._run
    class RealSession:
        def run_research_rotation(self, **kw):
            return rotation.run_research_rotation_with_psycopg('unused-test-seam', **kw)
    monkeypatch.setattr(cli, '_run', lambda session, args, factory, stop: original(RealSession(), args, factory, stop))
    code, out = invoke(capsys, RUN, model_factory=forbidden, stop=control)
    assert code == 130 and out['result']['status'] == 'stopped_before_reservation'
    assert out['result']['cursor_written_here'] is False


@pytest.mark.parametrize('mode', ['read', 'run', 'blocked'])
@pytest.mark.parametrize('phase', ['write', 'flush'])
def test_output_interrupt_requests_shared_stop_after_cleanup(managed, monkeypatch, mode, phase):
    """Returning exit130 must retain the shared cooperative-stop semantics."""
    import sys
    control = ResearchDispatchStop()
    managed['value'] = (read_value('inspect-budget') if mode == 'read' else
                        ResearchRotationReport('turn_already_reserved', stored_turn()))
    writes = []

    class Output:
        def write(self, text):
            assert ('entered' not in managed) if mode == 'blocked' else managed['closed']
            writes.append(text)
            if phase == 'write':
                raise KeyboardInterrupt(PRIVATE)
            return len(text)

        def flush(self):
            raise KeyboardInterrupt(PRIVATE)

    argv = ['inspect-budget', '--budget-id', 'budget'] if mode == 'read' else RUN
    options = {'model_factory': lambda _: None} if mode == 'run' else {}
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', Output())
        code = cli.main(argv, default_root=ROOT, stop=control, **options)
    assert code == 130
    assert control.is_stopped(), 'output interruption lost the caller shared stop request'
    assert len(writes) == 1
    assert len(managed['calls']) == (0 if mode == 'blocked' else 1)


@pytest.mark.parametrize('fault', ['short', 'exit-zero'])
def test_noninterrupt_output_failure_does_not_cancel_unrelated_work(managed, monkeypatch, fault):
    import sys
    control = ResearchDispatchStop()
    managed['value'] = ResearchRotationReport('turn_already_reserved', stored_turn())
    writes = []

    class Output:
        def write(self, text):
            writes.append(text)
            if fault == 'exit-zero':
                raise SystemExit(0)
            return 0

        def flush(self):
            pytest.fail('failed write was flushed')

    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', Output())
        code = cli.main(RUN, default_root=ROOT, model_factory=lambda _: None, stop=control)
    assert code == 1 and not control.is_stopped()
    assert managed['closed'] and len(managed['calls']) == len(writes) == 1


@pytest.mark.parametrize('kind', ['none', 'bool', 'negative', 'float'])
def test_invalid_task_output_count_never_claims_a_complete_receipt(managed, monkeypatch, kind):
    import sys
    writes = []
    managed['value'] = read_value('inspect-budget')

    class Output:
        def write(self, text):
            writes.append(text)
            return {'none': None, 'bool': True, 'negative': -1, 'float': float(len(text))}[kind]

        def flush(self):
            pytest.fail('invalid write count was accepted')

    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', Output())
        code = cli.main(['inspect-budget', '--budget-id', 'budget'], default_root=ROOT)
    assert code == 1 and len(writes) == len(managed['calls']) == 1
    assert managed['closed']


@pytest.mark.parametrize('kind', ['value-error', 'exit-zero', 'interrupt'])
def test_task_envelope_serialization_failure_has_no_partial_output(managed, monkeypatch, kind):
    import sys
    control = ResearchDispatchStop()
    managed['value'] = read_value('inspect-budget')
    original = cli.json.dumps
    writes = []
    error = {'value-error': ValueError(PRIVATE), 'exit-zero': SystemExit(0),
             'interrupt': KeyboardInterrupt(PRIVATE)}[kind]

    def serialize(value, *args, **kwargs):
        if type(value) is dict and 'operation' in value:
            assert managed['closed']
            raise error
        return original(value, *args, **kwargs)

    class Output:
        def write(self, text):
            writes.append(text)
            return len(text)

        def flush(self):
            pytest.fail('no serialized output exists')

    with monkeypatch.context() as patch:
        patch.setattr(cli.json, 'dumps', serialize)
        patch.setattr(sys, 'stdout', Output())
        code = cli.main(['inspect-budget', '--budget-id', 'budget'], default_root=ROOT, stop=control)
    assert code == (130 if kind == 'interrupt' else 1)
    assert control.is_stopped() is (kind == 'interrupt')
    assert writes == [] and len(managed['calls']) == 1


_TASK_OUTPUT_CHILD = r'''
from contextlib import contextmanager
from pathlib import Path
import json, runpy, sys
root, mode, fault = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, str(root / 'src'))
from polymarket_alpha_lab import research_dispatch_cli as cli
class Session:
    def inspect_model_budget(self, **kwargs):
        return None
class Database:
    def __init__(self, root): pass
    @contextmanager
    def session(self):
        yield Session()
cli.ProjectPostgres = Database
original = sys.stdout
class Output:
    def write(self, text):
        # A real complete envelope must reach this point; the session is synthetic.
        assert json.loads(text)['status'] == ('blocked' if mode == 'blocked' else 'not_found')
        if fault == 'exit-zero': raise SystemExit(0)
        return original.buffer.write(text[:10].encode('ascii'))
    def flush(self): original.flush()
sys.stdout = Output()
script = root / 'scripts/manage_research_tasks.py'
args = ['inspect-budget', '--budget-id', 'synthetic-budget'] if mode == 'read' else [
    'run-turn', '--rotation-id', 'synthetic-roster', '--turn-id', 'synthetic-turn',
    '--batch-id', 'synthetic-batch', '--budget-id', 'synthetic-budget', '--allow-model-calls']
sys.argv = [str(script), *args]
runpy.run_path(str(script), run_name='__main__')
'''


@pytest.mark.parametrize('mode', ['read', 'blocked'])
@pytest.mark.parametrize('fault', ['short', 'exit-zero'])
def test_actual_task_interpreter_does_not_exit_zero_after_output_failure(tmp_path, mode, fault):
    import subprocess
    import sys
    from polymarket_alpha_lab.project_postgres.files import clean_environment

    result = subprocess.run([sys.executable, '-I', '-c', _TASK_OUTPUT_CHILD, str(ROOT), mode, fault],
        cwd=tmp_path, env=clean_environment(), stdin=subprocess.DEVNULL,
        capture_output=True, timeout=30, check=False, shell=False)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr == b''
    assert result.stdout == (b'{\n  "opera' if fault == 'short' else b'')
    assert not list(tmp_path.iterdir())
