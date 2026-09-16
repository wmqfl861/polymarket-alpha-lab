"""Test-only recipe for a fresh CI kit. No signal delivery or business writes.

Runs with the kit's interpreter/source. A controlled Python exception interrupts
its real session close; the real OS lease and PostgreSQL read must remain usable.
"""
from pathlib import Path
import subprocess

from polymarket_alpha_lab.project_postgres.files import clean_environment

RECIPE = r'''
from pathlib import Path
from threading import Event, Thread
import json, sys
from polymarket_alpha_lab.project_postgres import files, server
from polymarket_alpha_lab.research_execution_psycopg import inspect_captured_research_with_psycopg
root = Path(sys.argv[1]).resolve()
assert Path(server.__file__).resolve().is_relative_to(root / 'src')
db = server.ProjectPostgres(root)
assert db.status()['status'] == 'stopped'
proof = []
for borrowed in (False, True):
    if borrowed:
        assert db.up()['status'] == 'running'
    with db.session() as session:
        baseline = session.inspect(record_id='kit-record').record
    entered, checkpoint, release = Event(), Event(), Event()
    state = dict(errors=[], reads=0, waits=0)
    cancellation = KeyboardInterrupt('synthetic interruption of close only')
    def work():
        try:
            def inspect(dsn):
                state['reads'] += 1
                entered.set()
                assert release.wait(30), 'recipe coordinator failed to release work'
                return inspect_captured_research_with_psycopg(dsn, record_id='kit-record').record
            state['result'] = state['session']._call(inspect)
        except BaseException as error:
            state['errors'].append(error)
    def owner():
        try:
            with db.session() as session:
                state['session'] = session
                original = session._condition.wait
                def wait(timeout=None):
                    state['waits'] += 1
                    if state['waits'] == 1:
                        raise cancellation
                    checkpoint.set()
                    return original(timeout)
                session._condition.wait = wait
                worker = Thread(target=work)
                state['worker'] = worker
                worker.start()
                assert entered.wait(30), 'recipe worker failed to enter'
        except BaseException as error:
            state['error'] = error
        finally:
            checkpoint.set()
    controller = Thread(target=owner)
    controller.start()
    try:
        assert checkpoint.wait(30), 'close did not reach a checkpoint'
        assert not state['session']._active and state['session']._in_flight == 1
        try:
            with files.Layout(root).lock():
                pass
        except files.ProjectDatabaseError as error:
            assert str(error) == 'project_postgres_busy'
        else:
            raise AssertionError('lifecycle lease was released with admitted work')
        try:
            state['session']._call(lambda _: None)
        except files.ProjectDatabaseError as error:
            assert str(error) == 'project_postgres_session_closed'
        else:
            raise AssertionError('close admitted a new operation')
    finally:
        release.set()
        controller.join(60)
        if 'worker' in state:
            state['worker'].join(30)
    assert not controller.is_alive() and not state['worker'].is_alive()
    assert state['errors'] == [] and state['reads'] == 1
    assert state['error'] is cancellation and state['waits'] >= 2
    assert state['result'] == baseline
    assert db.status()['status'] == ('running' if borrowed else 'stopped')
    if borrowed:
        db.down()
    assert db.status()['status'] == 'stopped'
    with db.session() as session:
        assert session.inspect(record_id='kit-record').record == baseline
    assert db.status()['status'] == 'stopped'
    proof.append(dict(borrowed=borrowed, admitted_reads=1, original_record_preserved=True))
print(json.dumps(dict(status='packaged_session_drain_verified', cases=proof,
    database_mocked=False, os_signal_sent=False, model_called=False)))
'''


def run_packaged_session_drain(root, python, cwd):
    return subprocess.run([str(python), '-I', '-c', RECIPE, str(root)], cwd=cwd,
        env=clean_environment(), stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding='utf-8', timeout=180, check=False, shell=False)
