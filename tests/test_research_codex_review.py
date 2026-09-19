"""Separate same-assistant adversarial review, not an external audit."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from polymarket_alpha_lab import research_codex_local as local
from polymarket_alpha_lab import research_uncapped_runner as runner
from tests.test_research_codex import profile, SENTINEL
from tests.test_research_uncapped import policy, req


@pytest.mark.parametrize('name', ['AGENTS.md', 'AGENTS.override.md', 'managed_config.toml'])
def test_auth_inherited_configuration_presence_is_fail_closed(tmp_path, monkeypatch, name):
    p = profile(tmp_path); path = p.codex_home/name; path.write_text(SENTINEL)
    monkeypatch.setattr(local, '_system_configuration_directory', lambda: tmp_path/'absent')
    monkeypatch.setattr(Path, 'read_text', lambda *a, **kw: pytest.fail('must not read policy contents'))
    with pytest.raises(ValueError, match='inherited_configuration'):
        local._require_unmanaged_profile(p)
    assert path.exists()


@pytest.mark.parametrize('name', ['config.toml', 'requirements.toml', 'managed_config.toml'])
def test_system_configuration_cannot_be_cleared_by_empty_table(tmp_path, monkeypatch, name):
    p = profile(tmp_path); system = tmp_path/'system'; system.mkdir(); path = system/name
    path.write_text(SENTINEL)
    monkeypatch.setattr(local, '_system_configuration_directory', lambda: system)
    with pytest.raises(ValueError, match='inherited_configuration'):
        local._require_unmanaged_profile(p)
    assert path.read_text() == SENTINEL


def test_configuration_permission_failure_blocks(tmp_path, monkeypatch):
    p = profile(tmp_path)
    monkeypatch.setattr(local, '_system_configuration_directory', lambda: tmp_path/'system')
    monkeypatch.setattr(Path, 'lstat', lambda *a, **kw: (_ for _ in ()).throw(PermissionError(SENTINEL)))
    with pytest.raises(PermissionError): local._require_unmanaged_profile(p)


def test_private_wrapper_rejects_different_profile_before_reservation(tmp_path, monkeypatch):
    p = profile(tmp_path); a = policy(p)
    other = replace(p, model_id='different-model')
    monkeypatch.setattr(runner, '_reserve_invocation', lambda *a, **kw: pytest.fail('mismatched profile reserved'))
    with pytest.raises(ValueError, match='profile_mismatch'):
        runner._UncappedCodex('fake', req(), a, other)


def test_profile_rechecked_before_each_reservation(tmp_path, monkeypatch):
    p = profile(tmp_path); a = policy(p)
    client = runner._UncappedCodex('fake', req(), a, p)
    object.__setattr__(client._profile, 'timeout_seconds', 1)
    monkeypatch.setattr(runner, '_reserve_invocation', lambda *a, **kw: pytest.fail('mismatched profile reserved'))
    with pytest.raises(ValueError, match='research_uncapped_call_failed'):
        client.complete(messages_json='[{"role":"user","content":"test"}]', max_output_tokens=100)
    assert client._failed
