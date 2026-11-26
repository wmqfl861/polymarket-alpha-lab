"""Separate negative review of config encoding and pre-launch input boundaries."""
from dataclasses import replace
import os
import stat
import tomllib
import traceback
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab import research_codex_profile as core
from polymarket_alpha_lab import research_codex_process as host
from polymarket_alpha_lab import research_process as process
from tests.test_research_codex_profile import profile, request, MODEL
from tests.test_research_uncapped import approval


@pytest.mark.parametrize('path', ['中文', '😀', '𐀀'])
def test_gateway_unicode_round_trips_through_actual_toml_parser(tmp_path, path):
    p = replace(profile(tmp_path), gateway_url='https://example.invalid/' + path)
    args = p(request()).argv
    configs = args[args.index('-c'):-1][1::2]
    assert tomllib.loads('\n'.join(configs)) == p.settings


@pytest.mark.parametrize('original', [KeyboardInterrupt(), SystemExit(0)])
def test_schema_close_failure_never_masks_original_interruption(monkeypatch, tmp_path, original):
    p = profile(tmp_path); closed = []
    monkeypatch.setattr(core.os, 'open', lambda *a, **k: 998)
    monkeypatch.setattr(core.os, 'fstat', lambda _: SimpleNamespace(st_mode=stat.S_IFREG,
        st_size=len(request().output_schema_json.encode())))
    def read(*args): raise original
    def close(fd): closed.append(fd); raise OSError('PRIVATE-SCHEMA-ERROR')
    monkeypatch.setattr(core.os, 'read', read)
    monkeypatch.setattr(core.os, 'close', close)
    with pytest.raises(type(original)) as caught: p(request())
    assert caught.value is original and closed == [998]


def test_schema_partial_reads_preserve_exact_bytes(monkeypatch, tmp_path):
    p = profile(tmp_path); original = core.os.read
    monkeypatch.setattr(core.os, 'read', lambda fd, count: original(fd, min(count, 3)))
    assert p(request()).argv[-1] == '-'


def test_schema_close_interruption_propagates_without_leaking_path(monkeypatch, tmp_path):
    p = profile(tmp_path); original = core.os.close; closed = []; error = KeyboardInterrupt()
    def close(fd):
        closed.append(fd); original(fd); raise error
    monkeypatch.setattr(core.os, 'close', close)
    with pytest.raises(KeyboardInterrupt) as caught: p(request())
    assert caught.value is error and len(closed) == 1


def test_profile_factory_copies_nested_configuration_before_client_entry(monkeypatch, tmp_path):
    p = profile(tmp_path)
    permission = approval(model_id=MODEL, adapter_contract_sha256=p.contract_sha256)
    factory = core.codex_profile_factory(profile=p, authorization=permission, allow_process_start=True)
    object.__setattr__(p.process, 'argv', ('/CHANGED-BY-CALLER',))
    object.__setattr__(p, 'gateway_url', 'http://bad.invalid')
    seen = []
    def failed(**kw): seen.append(kw['spec']); raise ValueError('PRIVATE-PROCESS')
    monkeypatch.setattr(host, 'run_research_process', failed)
    client = factory('crypto_btc')
    with pytest.raises(ValueError):
        client.complete(messages_json=request().messages_json, max_output_tokens=123)
    assert len(seen) == 1 and seen[0].argv[0].endswith('native-codex')
    assert 'http://bad.invalid' not in repr(seen[0].argv)
    with pytest.raises(ValueError, match='stopped'):
        client.complete(messages_json=request().messages_json, max_output_tokens=123)
    assert len(seen) == 1


def test_schema_growth_after_fstat_is_rejected_before_process(monkeypatch, tmp_path):
    p = profile(tmp_path); original = core.os.fstat
    def fstat(fd):
        result = original(fd)
        with open(p.schema_path, 'ab') as writer: writer.write(b'X')
        return result
    monkeypatch.setattr(core.os, 'fstat', fstat)
    with pytest.raises(ValueError, match='unavailable'): p(request())


def test_native_image_growth_still_hits_explicit_limit(monkeypatch, tmp_path):
    p = profile(tmp_path).process
    p = replace(p, max_executable_bytes=4)
    closed = []; reads = iter((b'\x7fELF', b'extra', b''))
    monkeypatch.setattr(process.os, 'open', lambda *a, **k: 998)
    monkeypatch.setattr(process.os, 'fstat', lambda _: SimpleNamespace(st_mode=stat.S_IFREG,st_size=4))
    monkeypatch.setattr(process.os, 'read', lambda *a: next(reads))
    monkeypatch.setattr(process.os, 'close', lambda fd: closed.append(fd))
    with pytest.raises(ValueError, match='image_invalid'): process._verify_executable(p)
    assert closed == [998]
