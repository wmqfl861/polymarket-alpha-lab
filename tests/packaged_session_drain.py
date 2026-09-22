"""Test-only recipe for a fresh CI kit. No signal delivery or business writes.

Runs with the kit's interpreter/source. A controlled Python exception interrupts
its real session close; the real OS lease and PostgreSQL read must remain usable.

Failure witness (PAL_RV05_CAPACITY_20260921 / N4): when this drain substep
fails, one compact closed-field line is printed so a truncated CI traceback
still leaves the phase, native-command category, 32-bit-hex exit code or fixed
exception label, monotonic elapsed time and kit category visible. The witness
is a pure observer: it changes no execution count, timeout, public error text
or control flow, and its own failure only records a fixed "unavailable"
marker. Closed whitelist only - never argv, DSN, environment, password-file
content, raw stderr/stdout, user paths or dumps.
"""
from pathlib import Path
import json
import subprocess
import time

from polymarket_alpha_lab.project_postgres.files import clean_environment

# Closed witness vocabulary. Emission only ever selects constants from these
# tuples (membership re-verified by the negative tests); nothing caller- or
# child-derived is ever printed.
DRAIN_WITNESS_SCHEMA = 'pal-drain-failure-witness-v1'
DRAIN_WITNESS_PREFIX = 'drain_failure_witness'
DRAIN_WITNESS_PHASES = ('drain_child_execution',)
DRAIN_WITNESS_NATIVE_COMMANDS = ('kit_python_interpreter',)
DRAIN_WITNESS_KITS = ('project_postgres_distribution_native_kit',)
DRAIN_WITNESS_KINDS = ('nonzero_exit', 'exception')
DRAIN_WITNESS_EXCEPTION_LABELS = ('subprocess_timeout', 'os_spawn_error',
    'unclassified_exception')
DRAIN_WITNESS_UNAVAILABLE = 'unavailable'

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


def _witness_exit_code_32hex(code) -> str:
    """Closed 32-bit two's-complement hex form; never a raw or partial code.

    The canonical Windows access-violation form is 0xC0000005 (eight hex
    digits); the mistyped seven-digit 0xC000005 form must never be produced.
    """
    if isinstance(code, bool) or not isinstance(code, int):
        return DRAIN_WITNESS_UNAVAILABLE
    return '0x{:08X}'.format(code & 0xFFFFFFFF)


def _witness_exception_label(error) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return 'subprocess_timeout'
    if isinstance(error, OSError):
        return 'os_spawn_error'
    return 'unclassified_exception'


def _witness_drain_failure(kind, code, error, started) -> None:
    """Print one closed-field failure-witness line; never raise or mask.

    Observer only: any failure inside the witness itself degrades to a fixed
    "unavailable" marker (or to silence if even that cannot be printed) and the
    original result/exception propagation is left exactly as it was.
    """
    try:
        record = {
            'schema': DRAIN_WITNESS_SCHEMA,
            'kit': DRAIN_WITNESS_KITS[0],
            'phase': DRAIN_WITNESS_PHASES[0],
            'native_command': DRAIN_WITNESS_NATIVE_COMMANDS[0],
            'failure_kind': kind,
            'monotonic_elapsed_seconds': round(time.monotonic() - started, 3),
        }
        if kind == 'nonzero_exit':
            record['exit_code_32hex'] = _witness_exit_code_32hex(code)
        else:
            record['exception_label'] = _witness_exception_label(error)
        line = json.dumps(record, sort_keys=True, separators=(',', ':'))
    except BaseException:
        line = '{"diagnostic":"%s","schema":"%s"}' % (
            DRAIN_WITNESS_UNAVAILABLE, DRAIN_WITNESS_SCHEMA)
    try:
        print(DRAIN_WITNESS_PREFIX + ' ' + line, flush=True)
    except BaseException:
        pass


def run_packaged_session_drain(root, python, cwd):
    started = time.monotonic()
    try:
        result = subprocess.run([str(python), '-I', '-c', RECIPE, str(root)], cwd=cwd,
            env=clean_environment(), stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding='utf-8', timeout=180, check=False, shell=False)
    except BaseException as error:
        _witness_drain_failure('exception', None, error, started)
        raise
    if result.returncode != 0:
        _witness_drain_failure('nonzero_exit', result.returncode, None, started)
    return result
