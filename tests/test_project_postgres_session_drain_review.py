"""Separate self-review of drain, exception precedence and unchanged admission.

No OS signal is sent and no service/provider is called. The controlled interrupt
is raised at close while real admitted threads are held by test-owned Events.
"""
import pytest

from polymarket_alpha_lab.project_postgres import files, research
from tests.test_project_postgres_session_drain import exercise, managed


@pytest.mark.parametrize('started', [False, True])
def test_repeated_close_interrupts_preserve_first_without_restarting_work(managed, started):
    first = KeyboardInterrupt('first test-owned interruption')
    managed.update(started=started, close_errors=[first, SystemExit(4), KeyboardInterrupt('later')])
    exercise(managed)
    assert managed['checkpoint_lease'] is True and managed['in_flight_at_unlock'] == [0]
    assert managed['error'] is first
    assert managed['events'].count('wait') >= 4
    assert managed['events'].count('stop') == int(started)
    assert managed['operation_calls'] == 1


@pytest.mark.parametrize('kind', ['regular', 'interrupt', 'exit'])
def test_stop_error_stays_primary_with_delayed_interrupt_as_cause(managed, kind):
    interruption = KeyboardInterrupt('close test')
    failure = {'regular': files.ProjectDatabaseError('project_postgres_stop_failed'),
               'interrupt': KeyboardInterrupt('stop test'), 'exit': SystemExit(9)}[kind]
    managed.update(close_errors=[interruption], stop_error=failure)
    exercise(managed)
    assert managed['in_flight_at_unlock'] == [0]
    assert managed['error'] is failure and failure.__cause__ is interruption
    assert managed['events'].count('stop') == 1


def test_borrowed_engine_never_stopped_even_with_deferred_interrupt(managed):
    first = SystemExit(0)
    managed.update(started=False, close_errors=[first], stop_error=AssertionError('must not stop'))
    exercise(managed)
    assert managed['error'] is first and 'stop' not in managed['events']
    assert managed['in_flight_at_unlock'] == [0]


def test_body_failure_remains_in_exception_chain_after_close_interrupt(managed):
    body = ValueError('original body failed')
    interruption = KeyboardInterrupt('during drain')
    managed.update(body_error=body, close_errors=[interruption])
    exercise(managed)
    assert managed['error'] is interruption and interruption.__context__ is body
    assert managed['in_flight_at_unlock'] == [0]


def test_interruption_before_close_enters_is_still_deferred(managed, monkeypatch):
    original = research.ProjectResearchSession.close
    first = KeyboardInterrupt('before condition acquisition')
    calls = []
    def close(session):
        calls.append(session)
        if len(calls) == 1:
            raise first
        return original(session)
    monkeypatch.setattr(research.ProjectResearchSession, 'close', close)
    exercise(managed)
    assert managed['error'] is first and len(calls) == 2 and calls[0] is calls[1]
    assert managed['checkpoint_lease'] is True and managed['in_flight_at_unlock'] == [0]


def test_non_interrupt_close_failure_is_not_retried_or_mislabeled_drained(managed, monkeypatch):
    failure = RuntimeError('synthetic unexpected close failure')
    calls = []
    def close(session):
        calls.append(session)
        # This scenario has no admitted operations. General close faults are
        # outside the controlled-interruption retry, and must not spin forever.
        raise failure
    monkeypatch.setattr(research.ProjectResearchSession, 'close', close)
    with pytest.raises(RuntimeError) as caught:
        with managed['db'].session() as session:
            managed['session'] = session
    assert caught.value is failure and calls == [session]
    assert 'stop' not in managed['events']


@pytest.mark.parametrize('started', [False, True])
def test_no_inflight_work_and_normal_close_retains_original_return(managed, started):
    managed['started'] = started
    with managed['db'].session() as session:
        managed['session'] = session
        assert session._call(lambda _: 42) == 42
    assert managed['in_flight_at_unlock'] == [0]
    assert managed['events'].count('stop') == int(started)
    with pytest.raises(files.ProjectDatabaseError, match='session_closed'):
        session._call(lambda _: pytest.fail('closed session readmitted work'))


def test_packaged_probe_uses_exact_kit_interpreter_once(monkeypatch, tmp_path):
    import subprocess
    from tests import packaged_session_drain as probe
    calls = []
    result = subprocess.CompletedProcess([], 1, 'synthetic', '')
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return result
    monkeypatch.setattr(probe.subprocess, 'run', run)
    python = tmp_path / '.venv/Scripts/python.exe'
    assert probe.run_packaged_session_drain(tmp_path, python, tmp_path.parent) is result
    assert len(calls) == 1
    args, options = calls[0]
    assert args == [str(python), '-I', '-c', probe.RECIPE, str(tmp_path)]
    assert options['timeout'] == 180 and options['check'] is options['shell'] is False
    assert options['stdin'] is subprocess.DEVNULL and options['cwd'] == tmp_path.parent


def test_packaged_probe_preserves_timeout_without_retry(monkeypatch, tmp_path):
    import subprocess
    from tests import packaged_session_drain as probe
    original = subprocess.TimeoutExpired('synthetic', 180)
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        raise original
    monkeypatch.setattr(probe.subprocess, 'run', run)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        probe.run_packaged_session_drain(tmp_path, 'synthetic-python', tmp_path)
    assert caught.value is original and calls == [1]


def test_packaged_probe_command_line_and_syntax_are_bounded():
    import subprocess
    from tests.packaged_session_drain import RECIPE
    compile(RECIPE, '<packaged session drain>', 'exec')
    command = ['C:/'+('p'*180)+'/python.exe', '-I', '-c', RECIPE, 'C:/'+('k'*180)]
    assert len(subprocess.list2cmdline(command).encode('utf-16-le')) // 2 + 1 < 32767


@pytest.mark.parametrize('started', [False, True])
@pytest.mark.parametrize('interruption_kind', ['interrupt', 'exit-zero'])
@pytest.mark.parametrize('output_fault', ['none', 'short', 'interrupt'])
def test_integrated_task_cli_drains_before_output_and_preserves_shared_stop(
        managed, monkeypatch, started, interruption_kind, output_fault):
    """PR50 close and PR51 output controls in one real threaded session.

    Native engine calls and the inspection payload are fixtures; admitted work,
    Condition.wait, the session context, CLI catch path and emitter are real.
    """
    from contextlib import redirect_stdout
    from threading import Thread
    import json
    from polymarket_alpha_lab import research_dispatch_cli as cli
    from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop

    state = managed
    state['started'] = started
    control = ResearchDispatchStop()
    interruption = KeyboardInterrupt('close-only fixture') if interruption_kind == 'interrupt' else SystemExit(0)
    output, flushes, emitted_codes = [], [], []
    original_emit = cli._emit
    monkeypatch.setattr(cli, 'ProjectPostgres', lambda root: state['db'])

    def work():
        try:
            def admitted(dsn):
                state['operation_calls'] += 1
                state['operation_entered'].set()
                assert state['operation_release'].wait(5), 'admitted operation was not released'
                return 'original result'
            state['result'] = state['session']._call(admitted)
        except BaseException as error:
            state['failures'].append(error)

    def inspect(session, args):
        state['session'] = session
        original_wait = session._condition.wait
        waits = []
        def wait(timeout=None):
            waits.append(1)
            if len(waits) == 1:
                raise interruption
            state['release_checkpoint'].set()
            return original_wait(timeout)
        session._condition.wait = wait
        state['worker'] = Thread(target=work)
        state['worker'].start()
        assert state['operation_entered'].wait(5), 'worker not admitted'
        return None
    monkeypatch.setattr(cli, '_inspect', inspect)

    class Output:
        def write(self, text):
            assert state['lease'] is False and state['in_flight_at_unlock'] == [0]
            output.append(text)
            if output_fault == 'interrupt':
                raise KeyboardInterrupt('output-only fixture')
            return len(text) - int(output_fault == 'short')
        def flush(self):
            flushes.append(1)

    def emit(envelope, code):
        emitted_codes.append(code)
        with redirect_stdout(Output()):
            return original_emit(envelope, code)
    monkeypatch.setattr(cli, '_emit', emit)

    def owner():
        try:
            state['code'] = cli.main(['inspect-budget', '--budget-id', 'synthetic'],
                default_root=state['db'].layout.root, stop=control)
        except BaseException as error:
            state['owner_error'] = error
    thread = Thread(target=owner)
    thread.start()
    try:
        assert state['release_checkpoint'].wait(5), 'close neither waited nor unlocked'
        assert state['lease'] is True and state['session']._in_flight == 1
        assert output == emitted_codes == []
        with pytest.raises(files.ProjectDatabaseError, match='session_closed'):
            state['session']._call(lambda _: pytest.fail('closed session admitted work'))
    finally:
        state['operation_release'].set()
        thread.join(5)
        if 'worker' in state:
            state['worker'].join(5)
    assert not thread.is_alive() and not state['worker'].is_alive()
    assert 'owner_error' not in state and state['failures'] == []
    assert state['operation_calls'] == 1 and state['result'] == 'original result'
    assert state['in_flight_at_unlock'] == [0]
    assert state['events'].count('stop') == int(started)
    assert emitted_codes == [130 if interruption_kind == 'interrupt' else 1]
    expected_code = 130 if output_fault == 'interrupt' else 1 if output_fault == 'short' else emitted_codes[0]
    assert state['code'] == expected_code
    assert control.is_stopped() is (interruption_kind == 'interrupt' or output_fault == 'interrupt')
    assert len(output) == 1 and len(flushes) == int(output_fault == 'none')
    body = json.loads(output[0])
    assert body['status'] == ('interrupted' if interruption_kind == 'interrupt' else 'failed')
    assert body['result'] is None and body['automatic_retry_permitted'] is False
    assert 'close-only fixture' not in output[0] and 'output-only fixture' not in output[0]
