"""Separate same-assistant adversarial checks of test-evidence boundaries."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests import packaged_research_flow as flow


@pytest.mark.parametrize('origin', ['other/src/mod.py', 'kit-sibling/src/mod.py'])
def test_foreign_or_similarly_named_root_cannot_count_as_kit_source(monkeypatch, tmp_path, origin):
    module = SimpleNamespace(__file__=str(tmp_path/origin))
    monkeypatch.setattr(flow, 'sys', SimpleNamespace(modules={'polymarket_alpha_lab.fake': module}))
    with pytest.raises(AssertionError, match='checkout code used'):
        flow.assert_origins(tmp_path/'kit')


def test_no_project_modules_is_not_a_successful_origin_check(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, 'sys', SimpleNamespace(modules={}))
    with pytest.raises(AssertionError, match='no project modules'):
        flow.assert_origins(tmp_path)


def test_recipe_timeout_is_preserved_without_retry(monkeypatch, tmp_path):
    original = subprocess.TimeoutExpired('synthetic', 300)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1); raise original
    monkeypatch.setattr(flow.subprocess, 'run', fail)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        flow.run_packaged_recipe(tmp_path, sys.executable, tmp_path)
    assert caught.value is original and calls == [1]


def test_missing_kit_cannot_use_installed_checkout_or_start_database(tmp_path):
    root = tmp_path/'missing-kit'; root.mkdir()
    before = list(root.iterdir())
    result = flow.run_packaged_recipe(root, sys.executable, tmp_path)
    assert result.returncode != 0 and 'kit source missing' in result.stderr
    assert 'packaged_flow_verified' not in result.stdout
    assert list(root.iterdir()) == before


def test_read_failure_does_not_launch_an_empty_recipe(monkeypatch, tmp_path):
    def fail(*args, **kwargs): raise OSError('synthetic fixture unavailable')
    monkeypatch.setattr(Path, 'read_text', fail)
    monkeypatch.setattr(flow.subprocess, 'run', lambda *a, **k: pytest.fail('empty recipe launched'))
    with pytest.raises(OSError, match='synthetic fixture unavailable'):
        flow.run_packaged_recipe(tmp_path, sys.executable, tmp_path)


def test_short_confirmation_sink_is_byte_exact_under_windows_text_translation(monkeypatch, tmp_path):
    """Model Windows text newlines without pretending to run Windows here."""
    import io
    import json
    import runpy

    root = tmp_path / 'kit'
    package = root / 'src/polymarket_alpha_lab'
    package.mkdir(parents=True)
    (package / '__init__.py').write_bytes(b'')
    class WindowsOutput:
        def __init__(self): self.buffer = io.BytesIO()
        def write(self, text):
            self.buffer.write(text.replace('\n', '\r\n').encode('utf-8'))
            return len(text)
        def flush(self): self.buffer.flush()
    output = WindowsOutput()
    body = json.dumps(dict(operation='confirm_crypto_resolution',
                           status='recorded_operator_confirmation'), indent=2) + '\n'
    def execute(script, *, run_name):
        assert Path(script) == root / 'scripts/review_resolution_queue.py'
        assert run_name == '__main__'
        assert sys.stdout.write(body) == 10
        raise SystemExit(1)
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', output)
        patch.setattr(sys, 'argv', ['fixture', str(root)])
        patch.setattr(sys, 'path', list(sys.path))
        patch.setattr(runpy, 'run_path', execute)
        with pytest.raises(SystemExit) as exit:
            exec(flow._CONFIRM_SHORT_OUTPUT, {})
        assert exit.value.code == 1
    assert output.buffer.getvalue() == b'{\n  "opera'


@pytest.mark.parametrize('result', [
    subprocess.CompletedProcess([], 0, b'{\n  "opera', b''),
    subprocess.CompletedProcess([], 1, b'', b''),
    subprocess.CompletedProcess([], 1, b'{\n  "opera', b'error'),
    subprocess.CompletedProcess([], 1, b'{\n  "statu', b''),
], ids=['zero-exit', 'no-success-witness', 'stderr', 'wrong-prefix'])
def test_short_confirmation_refuses_false_process_witness(monkeypatch, tmp_path, result):
    calls = []
    def run(*args, **kwargs): calls.append(1); return result
    monkeypatch.setattr(flow.subprocess, 'run', run)
    with pytest.raises(AssertionError):
        flow.run_confirmation_output_failure(tmp_path, b'synthetic')
    assert calls == [1]


def test_confirmation_failure_probe_uses_one_kit_process_and_original_bytes(monkeypatch, tmp_path):
    calls = []
    result = subprocess.CompletedProcess([], 1, b'{\n  "opera', b'')
    payload = b'{"synthetic":"unchanged"}'
    def run(*args, **kwargs): calls.append((args, kwargs)); return result
    monkeypatch.setattr(flow.subprocess, 'run', run)
    assert flow.run_confirmation_output_failure(tmp_path, payload) is result
    args, kwargs = calls[0]
    assert args[0] == [sys.executable, '-I', '-c', flow._CONFIRM_SHORT_OUTPUT, str(tmp_path)]
    assert kwargs['input'] is payload and kwargs['timeout'] == 60
    assert kwargs['cwd'] == tmp_path.parent and kwargs['shell'] is False
    assert kwargs['check'] is False and kwargs['capture_output'] is True
    assert len(calls) == 1


def test_confirmation_failure_probe_does_not_retry_timeout(monkeypatch, tmp_path):
    original = subprocess.TimeoutExpired('synthetic', 60)
    calls = []
    def run(*args, **kwargs): calls.append(1); raise original
    monkeypatch.setattr(flow.subprocess, 'run', run)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        flow.run_confirmation_output_failure(tmp_path, b'synthetic')
    assert caught.value is original and calls == [1]


def test_injected_recipe_fits_windows_process_command_line(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(flow.subprocess, 'run', lambda args, **kw: commands.append(args))
    root = tmp_path / ('k'*160)
    flow.run_packaged_recipe(root, root / '.venv/Scripts/python.exe', tmp_path)
    # CreateProcessW includes the terminating NUL in its 32767-character limit.
    # This is a bounded representative path probe, not an arbitrary-path guarantee.
    units = len(subprocess.list2cmdline(commands[0]).encode('utf-16-le')) // 2 + 1
    assert units < 32767
