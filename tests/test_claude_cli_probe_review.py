"""Separate self-review counterexamples for the TEST harness, not CLI approval."""
from dataclasses import replace
import http.client
import json
import os
from pathlib import Path

import pytest

from polymarket_alpha_lab import research_process as core
from tests import claude_cli_probe as probe
from tests.test_claude_cli_probe import image, simulated_runner
from tests.test_research_claude_exec import envelope, action


def test_valid_but_changed_reply_does_not_pass_exact_mock_roundtrip(tmp_path):
    original = simulated_runner([])
    def run(**kwargs):
        result = original(**kwargs)
        if kwargs['spec'].argv[1:] != ('--version',):
            other = envelope([action('read_evidence', source_id='invented')])
            return replace(result, stdout=json.dumps(other).encode())
        return result
    result = probe.run_probe(*image(), root=tmp_path/'probe', mode='success', allow_probe=True, process_runner=run)
    assert probe.observation_passed(result) is False


def test_expected_image_size_is_exact_not_only_an_upper_bound(tmp_path):
    binary, digest, size = image()
    with pytest.raises(ValueError, match='probe_image_unavailable'):
        probe.run_probe(binary, digest, size+1, root=tmp_path/'probe', mode='success', allow_probe=True,
                        process_runner=lambda **_: pytest.fail('wrong-size image launched'))


def test_snapshot_root_link_is_not_read(tmp_path):
    outside = tmp_path/'outside'; outside.mkdir(); (outside/'foreign').write_text(probe.TEST_KEY)
    link = tmp_path/'link'
    try: link.symlink_to(outside, target_is_directory=True)
    except OSError: pytest.skip('symlink privilege unavailable')
    result = probe.state_snapshot(link)
    assert result['complete'] is False and result['unsafe_entries'] == 1
    assert result['sentinel_files'] == 0 and result['files'] == {}


def test_queued_directory_link_is_not_followed(monkeypatch, tmp_path):
    root = tmp_path/'root'; root.mkdir(); child = root/'child'; child.mkdir()
    outside = tmp_path/'outside'; outside.mkdir(); (outside/'foreign').write_text(probe.TEST_KEY)
    original = probe.os.stat
    replaced = []
    def changed_stat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if Path(path) == child and not replaced:
            replaced.append(True)
            child.rmdir()
            try: child.symlink_to(outside, target_is_directory=True)
            except OSError: pytest.skip('symlink privilege unavailable')
        return info
    with monkeypatch.context() as patch:
        patch.setattr(probe.os, 'stat', changed_stat)
        result = probe.state_snapshot(root)
    assert replaced == [True]
    assert result['complete'] is False and result['sentinel_files'] == 0


@pytest.mark.parametrize('kind', ['duplicate_json', 'unknown_method'])
def test_malformed_request_cannot_be_hidden_by_valid_final_reply(tmp_path, kind):
    def run(**kwargs):
        if kwargs['spec'].argv[1:] == ('--version',):
            return core.ResearchProcessResult(probe.VERSION_OUTPUT.encode(), 0, 1)
        env = dict(kwargs['spec'].environment)
        address = env['ANTHROPIC_BASE_URL'].removeprefix('http://')
        conn = http.client.HTTPConnection(address, timeout=2)
        body = json.dumps({'model':probe.MODEL_ID,'max_tokens':1024,'stream':True,'tools':[],
                          'messages':[{'role':'user','content':kwargs['stdin'].decode()}]})
        if kind == 'unknown_method':
            conn.request('BREW','/v1/messages'); conn.getresponse().read()
        else:
            body = '{"model":"different",' + body[1:]
        conn.request('POST','/v1/messages',body,{'x-api-key':probe.TEST_KEY,'Content-Type':'application/json'})
        try:
            conn.getresponse().read()
        except http.client.RemoteDisconnected:
            pass  # Deliberate malformed-request rejection; still fabricate a valid final reply below.
        finally:
            conn.close()
        value=envelope();value['result']=probe.response_text('success')
        return core.ResearchProcessResult(json.dumps(value).encode(),0,1)
    result=probe.run_probe(*image(),root=tmp_path/'probe',mode='success',allow_probe=True,process_runner=run)
    assert probe.observation_passed(result) is False


def test_probe_optins_are_stripped_by_existing_full_verifier(monkeypatch):
    from scripts.verify_local import build_environment
    for name in probe._ENV: monkeypatch.setenv(name, 'synthetic')
    assert set(probe._ENV).isdisjoint(build_environment())


def test_configured_image_does_not_read_or_start_the_selected_image(monkeypatch, tmp_path):
    values = dict(zip(probe._ENV, ('1', str(tmp_path/'image'), 'a'*64, '200', '1'), strict=True))
    monkeypatch.setattr(probe.os, 'open', lambda *a: pytest.fail('file opened'))
    assert probe.configured_image(values) == (str(tmp_path/'image'), 'a'*64, 200)
    values[probe._ENV[-1]] = '0'
    with pytest.raises(ValueError, match='probe_configuration_invalid'):
        probe.configured_image(values)


def test_snapshot_entry_limit_is_not_a_clean_snapshot(tmp_path):
    for n in range(probe.MAX_ENTRIES+1): (tmp_path/str(n)).write_bytes(b'')
    result = probe.state_snapshot(tmp_path)
    assert result['complete'] is False and result['limit_reached'] is True
    assert len(result['files']) == probe.MAX_ENTRIES


def test_snapshot_never_reads_multiply_linked_file(tmp_path):
    root = tmp_path/'root'; root.mkdir()
    other = tmp_path/'other'; other.write_text(probe.TEST_KEY)
    try: os.link(other, root/'linked')
    except OSError: pytest.skip('hardlink support unavailable')
    result = probe.state_snapshot(root)
    assert result['complete'] is False and result['sentinel_files'] == 0
    assert result['unsafe_entries'] == 1


def test_replaced_root_cannot_look_unchanged(tmp_path):
    root = tmp_path/'root'; root.mkdir()
    before = probe.state_snapshot(root)
    root.rename(tmp_path/'original')
    root.mkdir()
    after = probe.state_snapshot(root, expected_root=before['root_identity'])
    assert after['complete'] is False


@pytest.mark.parametrize('kind', [KeyboardInterrupt, SystemExit])
def test_snapshot_close_failure_never_masks_original_interruption(monkeypatch, tmp_path, kind):
    file = tmp_path/'file'; file.write_text('x'); metadata=file.stat()
    original=kind();closed=[]
    def interrupted(*args): raise original
    def close(fd): closed.append(fd);raise OSError('synthetic second error')
    # Restore process-wide os functions BEFORE pytest reports an unexpected
    # inner assertion failure; the diagnostic watcher owns unrelated descriptors.
    with monkeypatch.context() as patch:
        patch.setattr(probe.os,'open',lambda *a: 987)
        patch.setattr(probe.os,'fstat',lambda *a: metadata)
        patch.setattr(probe.os,'read',interrupted)
        patch.setattr(probe.os,'close',close)
        with pytest.raises(kind) as caught: probe.state_snapshot(tmp_path)
    assert caught.value is original and closed == [987]



def test_server_close_failure_does_not_mask_original_interrupt(monkeypatch):
    original=KeyboardInterrupt();calls=[];close=probe._Server.server_close
    def failed_close(server):
        calls.append(1);close(server);raise OSError('synthetic close error')
    monkeypatch.setattr(probe._Server,'server_close',failed_close)
    with pytest.raises(KeyboardInterrupt) as caught:
        with probe.loopback_server('success'):
            raise original
    assert caught.value is original and calls == [1]


def test_server_start_failure_closes_socket_without_waiting_for_unstarted_worker(monkeypatch):
    original=OSError('synthetic start error');calls=[];close=probe._Server.server_close
    def no_start(worker): raise original
    def record_close(server):calls.append(1);close(server)
    monkeypatch.setattr(probe.Thread,'start',no_start)
    monkeypatch.setattr(probe._Server,'server_close',record_close)
    with pytest.raises(OSError) as caught:
        with probe.loopback_server('success'):pytest.fail('entered')
    assert caught.value is original and calls == [1]


@pytest.mark.parametrize('nested', [False, True])
def test_snapshot_uses_current_file_identity_not_windows_direntry_zeroes(monkeypatch, tmp_path, nested):
    """Windows DirEntry.stat omits device/inode/link count; lstat does not."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from hashlib import sha256
    root = tmp_path/'root'; root.mkdir()
    target = root/'sub' if nested else root
    target.mkdir(exist_ok=True)
    (target/'file').write_bytes(b'known bytes')
    original = probe.os.scandir
    class Entry:
        def __init__(self, entry):
            self._entry = entry
            self.path, self.name = entry.path, entry.name
        def stat(self, **kwargs):
            actual = self._entry.stat(**kwargs)
            return SimpleNamespace(st_mode=actual.st_mode, st_size=actual.st_size,
                st_mtime_ns=actual.st_mtime_ns, st_ino=0, st_dev=0, st_nlink=0,
                st_file_attributes=getattr(actual, 'st_file_attributes', 0))
    @contextmanager
    def windows_entries(path):
        with original(path) as stream:
            yield (Entry(entry) for entry in stream)
    with monkeypatch.context() as patch:
        patch.setattr(probe.os, 'scandir', windows_entries)
        result = probe.state_snapshot(root)
    assert result['complete'] is True and result['unsafe_entries'] == 0
    assert len(result['files']) == (2 if nested else 1)
    key = str(Path('sub/file')) if nested else 'file'
    assert result['files'][key] == ('file', sha256(b'known bytes').hexdigest())


@pytest.mark.parametrize('kind', [KeyboardInterrupt, SystemExit])
def test_failed_injection_assertion_restores_os_before_pytest_reporting(monkeypatch, tmp_path, kind):
    """Deliberately make the inner assertion fail, without poisoning its reporter."""
    original = (probe.os.open, probe.os.read, probe.os.close, probe.os.fstat)
    inner = pytest.MonkeyPatch()
    with monkeypatch.context() as patch:
        patch.setattr(probe, 'state_snapshot', lambda _: {'complete': False})
        try:
            with pytest.raises(pytest.fail.Exception):
                test_snapshot_close_failure_never_masks_original_interruption(inner, tmp_path, kind)
            restored = (probe.os.open, probe.os.read, probe.os.close, probe.os.fstat) == original
        finally:
            inner.undo()  # Keep the RED test itself from breaking pytest's reporter.
    assert restored is True
