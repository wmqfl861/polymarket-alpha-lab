"""Negative/closure tests for the packaged session drain failure witness.

The witness is a pure observer attached to the test helper only (PAL_RV05 N4).
These tests verify, without any real kit, native engine, service or provider:
(a) the emitted record is closed-vocabulary only and cannot leak argv, DSN,
    environment, password-file content, raw stderr/stdout, user paths or dumps;
(b) a failing witness never masks or replaces the original result/exception;
(c) the observer leaves execution count, command line, options, timeout and
    control flow exactly as before, and stays silent on success;
(d) the canonical Windows access-violation form is 0xC0000005 (eight hex
    digits), never the mistyped seven-digit 0xC000005.
"""
import builtins
import json
import subprocess

import pytest

from tests import packaged_session_drain as probe

WITNESS_CLOSED_KEYS_EXIT = frozenset({'schema', 'kit', 'phase', 'native_command',
    'failure_kind', 'monotonic_elapsed_seconds', 'exit_code_32hex'})
WITNESS_CLOSED_KEYS_EXCEPTION = frozenset({'schema', 'kit', 'phase', 'native_command',
    'failure_kind', 'monotonic_elapsed_seconds', 'exception_label'})

# Sensitive-shaped decoys planted in every channel the witness must never copy.
DECOY_DSN = 'postgres://pal-owner:s3cret-pw@127.0.0.1:5432/pal_private'
DECOY_STDERR = ('Traceback (most recent call last):\n'
    'DSN ' + DECOY_DSN + '\nPGPASSWORD=s3cret-pw PGDATA=/u/private\n'
    'argv --quote "x" -c import os; os.environ\n'
    'password file app.pgpass opened\n')
FORBIDDEN_MARKERS = (DECOY_DSN, 's3cret-pw', 'PGPASSWORD', 'PGDATA', 'app.pgpass',
    'postgres://', 'argv', 'stderr', 'Traceback', 'os.environ')


def _witness_lines(captured):
    return [line for line in captured.out.splitlines()
            if line.startswith(probe.DRAIN_WITNESS_PREFIX)]


def _single_witness_record(captured):
    lines = _witness_lines(captured)
    assert len(lines) == 1, lines
    record = json.loads(lines[0][len(probe.DRAIN_WITNESS_PREFIX):].strip())
    # The witness line must be short enough to survive truncated CI logging.
    assert len(lines[0]) <= 400
    for marker in FORBIDDEN_MARKERS:
        assert marker not in lines[0]
    return record


def _assert_closed_common(record):
    assert record['schema'] == probe.DRAIN_WITNESS_SCHEMA
    assert record['kit'] in probe.DRAIN_WITNESS_KITS
    assert record['phase'] in probe.DRAIN_WITNESS_PHASES
    assert record['native_command'] in probe.DRAIN_WITNESS_NATIVE_COMMANDS
    assert record['failure_kind'] in probe.DRAIN_WITNESS_KINDS
    elapsed = record['monotonic_elapsed_seconds']
    assert isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
    assert 0 <= elapsed < 60


def test_witness_nonzero_exit_is_closed_and_leak_free(monkeypatch, tmp_path, capsys):
    secret_python = tmp_path / ('venv-' + DECOY_DSN.replace('/', '_').replace(':', '-'))
    result = subprocess.CompletedProcess([], 1, 'stdout ' + DECOY_DSN, DECOY_STDERR)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(probe.subprocess, 'run', run)
    root = tmp_path / 'kit root with spaces'
    returned = probe.run_packaged_session_drain(root, secret_python, tmp_path)
    emitted = capsys.readouterr()
    lines = _witness_lines(emitted)
    assert len(lines) == 1
    record = json.loads(lines[0][len(probe.DRAIN_WITNESS_PREFIX):].strip())
    assert returned is result and len(calls) == 1
    assert set(record) == WITNESS_CLOSED_KEYS_EXIT
    _assert_closed_common(record)
    assert record['failure_kind'] == 'nonzero_exit'
    assert record['exit_code_32hex'] == '0x00000001'
    # User paths of this run (tmp_path embeds the real OS user name) and the
    # decoy interpreter path must not appear anywhere in the emitted line.
    for user_path in (str(root), str(secret_python), str(tmp_path)):
        assert user_path not in lines[0]
    for marker in FORBIDDEN_MARKERS:
        assert marker not in lines[0]


def test_witness_exception_kind_is_closed_and_message_free(monkeypatch, tmp_path, capsys):
    leak = RuntimeError('secret ' + DECOY_DSN + ' PGPASSWORD=s3cret-pw C:\\Users\\private')
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        raise leak

    monkeypatch.setattr(probe.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as caught:
        probe.run_packaged_session_drain(tmp_path, 'synthetic-python', tmp_path)
    assert caught.value is leak and calls == [1]
    record = _single_witness_record(capsys.readouterr())
    assert set(record) == WITNESS_CLOSED_KEYS_EXCEPTION
    _assert_closed_common(record)
    assert record['failure_kind'] == 'exception'
    assert record['exception_label'] == 'unclassified_exception'
    assert record['exception_label'] in probe.DRAIN_WITNESS_EXCEPTION_LABELS


def test_witness_classifies_timeout_and_spawn_errors_in_closed_enum(monkeypatch, tmp_path, capsys):
    original = subprocess.TimeoutExpired('synthetic', 180)
    spawn = FileNotFoundError('synthetic-python does not exist at C:\\Users\\private')
    outcomes = [original, spawn]
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(outcomes.pop(0)))
    with pytest.raises(subprocess.TimeoutExpired):
        probe.run_packaged_session_drain(tmp_path, 'synthetic-python', tmp_path)
    first = json.loads(_witness_lines(capsys.readouterr())[0][len(probe.DRAIN_WITNESS_PREFIX):].strip())
    with pytest.raises(FileNotFoundError):
        probe.run_packaged_session_drain(tmp_path, 'synthetic-python', tmp_path)
    second = json.loads(_witness_lines(capsys.readouterr())[0][len(probe.DRAIN_WITNESS_PREFIX):].strip())
    assert first['exception_label'] == 'subprocess_timeout'
    assert second['exception_label'] == 'os_spawn_error'
    assert set(first) == set(second) == WITNESS_CLOSED_KEYS_EXCEPTION


def test_witness_record_failure_degrades_to_unavailable_without_masking(monkeypatch, tmp_path, capsys):
    original = subprocess.TimeoutExpired('synthetic', 180)

    def broken_dumps(*args, **kwargs):
        raise ValueError('synthetic witness serialization failure')

    monkeypatch.setattr(probe.json, 'dumps', broken_dumps)
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(original))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        probe.run_packaged_session_drain(tmp_path, 'synthetic-python', tmp_path)
    assert caught.value is original
    lines = _witness_lines(capsys.readouterr())
    assert len(lines) == 1
    assert lines[0] == probe.DRAIN_WITNESS_PREFIX + ' {"diagnostic":"unavailable","schema":"%s"}' % probe.DRAIN_WITNESS_SCHEMA


def test_witness_total_emission_failure_still_propagates_original(monkeypatch, tmp_path):
    # Even when printing itself fails, the original exception must surface
    # unchanged and nothing from the witness may replace or interrupt it.
    original = subprocess.TimeoutExpired('synthetic', 180)

    def dead_print(*args, **kwargs):
        raise OSError('synthetic closed stdout')

    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        raise original

    monkeypatch.setattr(probe.subprocess, 'run', run)
    monkeypatch.setattr(builtins, 'print', dead_print)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        probe.run_packaged_session_drain(tmp_path, 'synthetic-python', tmp_path)
    assert caught.value is original and calls == [1]


def test_witness_emission_failure_on_exit_path_keeps_result_contract(monkeypatch, tmp_path):
    result = subprocess.CompletedProcess([], 1, '', DECOY_STDERR)

    def dead_print(*args, **kwargs):
        raise OSError('synthetic closed stdout')

    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: result)
    monkeypatch.setattr(builtins, 'print', dead_print)
    returned = probe.run_packaged_session_drain(tmp_path, 'synthetic-python', tmp_path)
    assert returned is result and returned.returncode == 1 and returned.stderr == DECOY_STDERR


def test_observer_leaves_success_path_silent_and_untouched(monkeypatch, tmp_path, capsys):
    from polymarket_alpha_lab.project_postgres.files import clean_environment
    result = subprocess.CompletedProcess([], 0, '{"status":"packaged_session_drain_verified"}', '')
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(probe.subprocess, 'run', run)
    python = tmp_path / '.venv/Scripts/python.exe'
    returned = probe.run_packaged_session_drain(tmp_path, python, tmp_path.parent)
    assert returned is result
    assert _witness_lines(capsys.readouterr()) == []
    assert len(calls) == 1
    args, options = calls[0]
    assert args == [str(python), '-I', '-c', probe.RECIPE, str(tmp_path)]
    assert options['timeout'] == 180 and options['check'] is options['shell'] is False
    assert options['stdin'] is subprocess.DEVNULL and options['cwd'] == tmp_path.parent
    assert options['capture_output'] is True and options['text'] is True
    assert options['encoding'] == 'utf-8'
    assert options['env'] == clean_environment()


def test_observer_keeps_failure_command_line_options_and_single_execution(monkeypatch, tmp_path, capsys):
    result = subprocess.CompletedProcess([], 1, '', DECOY_STDERR)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(probe.subprocess, 'run', run)
    python = tmp_path / '.venv/Scripts/python.exe'
    returned = probe.run_packaged_session_drain(tmp_path, python, tmp_path.parent)
    assert returned is result and len(calls) == 1
    assert _single_witness_record(capsys.readouterr())['failure_kind'] == 'nonzero_exit'
    args, options = calls[0]
    assert args == [str(python), '-I', '-c', probe.RECIPE, str(tmp_path)]
    assert options['timeout'] == 180 and options['check'] is options['shell'] is False
    assert options['stdin'] is subprocess.DEVNULL and options['cwd'] == tmp_path.parent


@pytest.mark.parametrize('code,expected', [
    (-1073741819, '0xC0000005'),  # canonical Windows access violation
    (1, '0x00000001'),
    (0, '0x00000000'),
    (-1, '0xFFFFFFFF'),
    (2147483647, '0x7FFFFFFF'),
    (-2147483648, '0x80000000'),
    ('not-a-code', 'unavailable'),
    (None, 'unavailable'),
    (True, 'unavailable'),
])
def test_exit_code_uses_canonical_32_bit_hex_forms(code, expected):
    formatted = probe._witness_exit_code_32hex(code)
    assert formatted == expected
    if formatted != 'unavailable':
        assert len(formatted) == 10 and formatted.startswith('0x')
        assert len(formatted[2:]) == 8  # never the seven-digit 0xC000005 form


def test_access_violation_witness_never_emits_mistyped_code(monkeypatch, tmp_path, capsys):
    result = subprocess.CompletedProcess([], -1073741819, '', DECOY_STDERR)
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: result)
    probe.run_packaged_session_drain(tmp_path, 'synthetic-python', tmp_path)
    record = _single_witness_record(capsys.readouterr())
    assert record['exit_code_32hex'] == '0xC0000005'
    assert '0xC000005' not in probe._witness_exit_code_32hex(-1073741819)
