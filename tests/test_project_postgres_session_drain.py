"""Close interruptions must not release the lease before admitted work drains.

Real session/Condition/threads; only the native lifecycle and condition-wait
interrupt are synthetic. No signal handlers, OS policies, engine or provider.
"""
from contextlib import contextmanager
from threading import Event, Thread

import pytest

from polymarket_alpha_lab.project_postgres import binding, files, server


@pytest.fixture
def managed(tmp_path, monkeypatch):
    root = tmp_path / 'Session Drain With Spaces'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    (root / 'database').mkdir()
    (root / 'database/migrations.lock.json').write_text('{}')
    db = server.ProjectPostgres(root)
    state = dict(db=db, events=[], lease=False, started=True, body_error=None,
                 stop_error=None, close_errors=[], release_checkpoint=Event(),
                 operation_entered=Event(), operation_release=Event(), failures=[],
                 result=None, operation_calls=0, in_flight_at_unlock=[])
    identity = dict(version='17.11', instance_id='i', root_sha256='r', system_identifier='s')

    @contextmanager
    def lease(_):
        state['lease'] = True
        state['events'].append('lock')
        try:
            yield
        finally:
            state['in_flight_at_unlock'].append(state['session']._in_flight)
            state['lease'] = False
            state['events'].append('unlock')
            state['release_checkpoint'].set()

    monkeypatch.setattr(files.Layout, 'lock', lease)
    monkeypatch.setattr(server, 'verify_runtime', lambda _: {'version': '17.11'})
    monkeypatch.setattr(db, '_state', lambda: identity)
    monkeypatch.setattr(db, '_dsn', lambda _: 'synthetic:never-connected')
    monkeypatch.setattr(db, '_start', lambda _: state['started'])
    monkeypatch.setattr(db, '_pending', lambda _: ())

    def stop(_):
        assert state['lease'] and state['session']._in_flight == 0
        state['events'].append('stop')
        if state['stop_error'] is not None:
            raise state['stop_error']
    monkeypatch.setattr(db, '_stop', stop)
    return state


def exercise(state):
    """Stop at a deterministic checkpoint: a resumed wait OR premature unlock.

    A held worker is released in finally even when an assertion fails. No sleeps
    or timing races determine correctness; bounded waits detect a broken harness.
    """
    worker = None

    def operation(dsn):
        state['operation_calls'] += 1
        assert dsn == 'synthetic:never-connected'
        assert binding._EXPECTED.get() == ('i', 'r', 's')
        state['operation_entered'].set()
        assert state['operation_release'].wait(5), 'test did not release admitted work'
        return 'original result'

    def work():
        try:
            state['result'] = state['session']._call(operation)
        except BaseException as error:
            state['failures'].append(error)

    def owner():
        nonlocal worker
        try:
            with state['db'].session() as session:
                state['session'] = session
                original_wait = session._condition.wait
                pending = list(state['close_errors'])

                def interrupted_wait(timeout=None):
                    assert not session._active and session._in_flight == 1
                    state['events'].append('wait')
                    if pending:
                        raise pending.pop(0)
                    state['release_checkpoint'].set()
                    return original_wait(timeout)
                session._condition.wait = interrupted_wait
                worker = Thread(target=work)
                worker.start()
                assert state['operation_entered'].wait(5), 'worker did not enter'
                if state['body_error'] is not None:
                    raise state['body_error']
        except BaseException as error:
            state['error'] = error
        else:
            state['error'] = None

    controller = Thread(target=owner)
    controller.start()
    try:
        assert state['release_checkpoint'].wait(5), 'close neither waited nor exited'
        state['checkpoint_lease'] = state['lease']
        state['checkpoint_in_flight'] = state['session']._in_flight
        with pytest.raises(files.ProjectDatabaseError, match='session_closed'):
            state['session']._call(lambda _: pytest.fail('admitted after closing'))
    finally:
        state['operation_release'].set()
        controller.join(5)
        if worker is not None:
            worker.join(5)
    assert not controller.is_alive() and worker is not None and not worker.is_alive()
    assert state['failures'] == []
    assert state['result'] == 'original result' and state['operation_calls'] == 1


@pytest.mark.parametrize('started', [False, True])
@pytest.mark.parametrize('kind', ['interrupt', 'exit-zero', 'exit-nonzero'])
def test_close_interruption_preserves_lease_and_original_exception(managed, started, kind):
    interruption = {'interrupt': KeyboardInterrupt('synthetic cancellation'),
                    'exit-zero': SystemExit(0), 'exit-nonzero': SystemExit(7)}[kind]
    managed.update(started=started, close_errors=[interruption])
    exercise(managed)
    assert managed['checkpoint_lease'] is True
    assert managed['checkpoint_in_flight'] == 1
    assert managed['in_flight_at_unlock'] == [0]
    assert managed['error'] is interruption
    assert managed['events'].count('stop') == int(started)
    assert managed['events'][-1] == 'unlock'


@pytest.mark.parametrize('started', [False, True])
@pytest.mark.parametrize('body_failed', [False, True])
def test_normal_close_still_drains_once_and_preserves_body(managed, started, body_failed):
    failure = ValueError('synthetic body failure') if body_failed else None
    managed.update(started=started, body_error=failure)
    exercise(managed)
    assert managed['checkpoint_lease'] is True
    assert managed['in_flight_at_unlock'] == [0]
    assert managed['error'] is failure
    assert managed['events'].count('stop') == int(started)
