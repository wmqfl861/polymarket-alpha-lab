"""Explicit uncapped task operations reuse the existing command and receipts."""
from dataclasses import replace
from types import SimpleNamespace
import io
import json

import pytest

from polymarket_alpha_lab import research_dispatch_cli as cli
from polymarket_alpha_lab import research_codex_local as local
from polymarket_alpha_lab.research_dispatch_rotation_runner import ResearchRotationReport
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from tests.test_research_dispatch_cli import ROOT, managed, stored_turn
from tests.test_research_codex import profile, SENTINEL
from tests.test_research_uncapped import policy, snap


@pytest.fixture
def authorization(tmp_path):
    return policy(profile(tmp_path))


def admit_args(a, allowed=True):
    return ['create-uncapped', '--authorization-id', a.authorization_id,
            '--input-sha256', a.content_sha256] + (['--allow-authorization-write'] if allowed else [])


def run_args(a, path='explicit-profile.json', allowed=True):
    return ['run-turn', '--rotation-id', 'rotation', '--turn-id', 'turn', '--batch-id', 'batch',
            '--authorization-id', a.authorization_id, '--codex-profile', path,
            '--max-tasks', '1', '--max-workers', '1'] + (['--allow-model-calls'] if allowed else [])


def invoke(capsys, args, **kw):
    result = cli.main(args, default_root=ROOT, **kw)
    text = capsys.readouterr()
    assert SENTINEL not in text.out + text.err
    return result, json.loads(text.out)


@pytest.mark.parametrize('frame', [b'', b'\n', b'\r\n'])
def test_explicit_admission_metadata_only(authorization, managed, monkeypatch, capsys, frame):
    a = authorization
    managed['value'] = snap(a).stored
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(a.payload.encode()+frame)))
    code, out = invoke(capsys, admit_args(a))
    assert code == 0 and managed['closed']
    assert managed['calls'][0][0] == 'create_uncapped_authorization'
    assert managed['calls'][0][1]['allow_authorization_write'] is True
    assert out['result'] == managed['value'].to_dict()
    assert out['result']['monetary_cap'] is None
    assert out['model_calls_possible'] is False and out['business_writes_possible'] is True
    assert 'request_keys' not in out['result'] and 'profile' not in out['result']


@pytest.mark.parametrize('operation', ['admit', 'run'])
def test_no_optin_no_input_profile_or_database(authorization, managed, monkeypatch, capsys, operation):
    class Unreadable:
        @property
        def buffer(self): pytest.fail('unauthorized stdin')
    monkeypatch.setattr(cli.sys, 'stdin', Unreadable())
    monkeypatch.setattr(local, 'read_profile', lambda *a: pytest.fail('unauthorized profile'))
    args = admit_args(authorization, False) if operation == 'admit' else run_args(authorization, allowed=False)
    code, out = invoke(capsys, args)
    assert code == 2 and not out['operation_entered'] and 'root' not in managed


@pytest.mark.parametrize('bad', [b'', b'null', b'[]', b'{}', b'\xff', b'x'*32769])
def test_bad_admission_before_database(authorization, managed, monkeypatch, capsys, bad):
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(bad)))
    code, out = invoke(capsys, admit_args(authorization))
    assert code == 2 and out['status'] == 'invalid_input' and 'root' not in managed


@pytest.mark.parametrize('defect', ['space', 'extra-frame', 'hash', 'identity', 'policy-mutation'])
def test_admission_binding_rejects_changes(authorization, managed, monkeypatch, capsys, defect):
    a = authorization
    raw = a.payload.encode(); args = admit_args(a)
    managed['value'] = snap(a).stored
    if defect == 'space': raw += b' '
    if defect == 'extra-frame': raw += b'\n\n'
    if defect == 'hash': args[4] = 'a'*64
    if defect == 'identity': args[2] = 'foreign'
    if defect == 'policy-mutation':
        managed['value'] = replace(managed['value'], policy=replace(a, max_message_bytes=1))
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw)))
    code, out = invoke(capsys, args)
    assert code == (1 if defect == 'policy-mutation' else 2)
    assert out['result'] is None
    assert len(managed['calls']) == int(defect == 'policy-mutation')


@pytest.mark.parametrize('defect', ['foreign', 'mutated', 'wrong-type', 'query', 'cleanup'])
def test_inspect_does_not_publish_invalid_receipt(authorization, managed, capsys, defect):
    value = snap(authorization)
    if defect == 'foreign': value = replace(value, stored=replace(value.stored,
        policy=replace(value.stored.policy, authorization_id='foreign')))
    if defect == 'mutated': object.__setattr__(value, 'reserved_invocations', -1)
    if defect == 'wrong-type': value = {'raw': SENTINEL}
    if defect in ('query', 'cleanup'):
        managed['error' if defect == 'query' else 'cleanup_error'] = RuntimeError(SENTINEL)
    managed['value'] = value
    code, out = invoke(capsys, ['inspect-uncapped', '--authorization-id', authorization.authorization_id])
    assert code == 1 and out['result'] is None and managed['closed']


@pytest.mark.parametrize('missing', [False, True])
def test_inspect_metadata_and_not_found(authorization, managed, capsys, missing):
    managed['value'] = None if missing else snap(authorization)
    code, out = invoke(capsys, ['inspect-uncapped', '--authorization-id', authorization.authorization_id])
    assert code == (3 if missing else 0) and managed['closed']
    assert not out['model_calls_possible'] and not out['business_writes_possible']
    if not missing:
        assert out['result']['provider_submission_count'] is None
        assert out['result']['reported_total_tokens'] == 0


@pytest.mark.parametrize('fault', ['missing', 'factory', 'profile-error', 'profile-exit', 'profile-interrupt', 'capped-profile'])
def test_invalid_configuration_before_database(authorization, managed, monkeypatch, capsys, fault):
    control = ResearchDispatchStop(); calls = []
    def load(path):
        calls.append(path)
        raise {'profile-exit': SystemExit, 'profile-interrupt': KeyboardInterrupt}.get(fault, ValueError)(SENTINEL)
    monkeypatch.setattr(local, 'read_profile', load)
    args = run_args(authorization); factory = None
    if fault == 'missing':
        index = args.index('--codex-profile'); del args[index:index+2]
    if fault == 'factory': factory = lambda _: pytest.fail('factory entered')
    if fault == 'capped-profile':
        args[args.index('--authorization-id')] = '--budget-id'; factory = lambda _: None
    code, out = invoke(capsys, args, model_factory=factory, stop=control)
    assert code == (130 if fault == 'profile-interrupt' else 2)
    assert not out['operation_entered'] and 'root' not in managed
    assert control.is_stopped() == (fault == 'profile-interrupt')
    assert len(calls) == int(fault.startswith('profile-'))


def test_run_one_explicit_uncapped_operation_without_factory(authorization, managed, monkeypatch, capsys, tmp_path):
    target = tmp_path/'runtime'; target.mkdir()
    p = profile(target)
    managed['value'] = ResearchRotationReport('turn_already_reserved', stored_turn(), (), False)
    monkeypatch.setattr(local, 'read_profile', lambda path: p)
    code, out = invoke(capsys, run_args(authorization))
    assert code == 0 and managed['closed'] and len(managed['calls']) == 1
    method, kw = managed['calls'][0]
    assert method == 'run_research_rotation'
    assert kw['model_factory'] is kw['model_budget_id'] is None
    assert kw['codex_profile'] == p and kw['uncapped_authorization_id'] == authorization.authorization_id
    assert out['execution_mode'] == 'uncapped_codex' and out['monetary_cap'] is None
    assert out['provider_submission_count'] is None


@pytest.mark.parametrize('phase', ['query', 'cleanup'])
@pytest.mark.parametrize('kind', [RuntimeError, SystemExit, KeyboardInterrupt])
def test_operation_failures_no_retry_and_conservative_effects(authorization, managed, monkeypatch, capsys, tmp_path, phase, kind):
    target = tmp_path/'runtime'; target.mkdir(); p = profile(target)
    monkeypatch.setattr(local, 'read_profile', lambda path: p)
    managed['value'] = ResearchRotationReport('turn_already_reserved', stored_turn(), (), False)
    managed['error' if phase == 'query' else 'cleanup_error'] = kind(SENTINEL)
    control = ResearchDispatchStop()
    code, out = invoke(capsys, run_args(authorization), stop=control)
    assert code == (130 if kind is KeyboardInterrupt else 1)
    assert out['model_calls_possible'] and out['business_writes_possible'] and out['result'] is None
    assert len(managed['calls']) == 1 and managed['closed']
    assert control.is_stopped() == (kind is KeyboardInterrupt)


@pytest.mark.parametrize('kind', ['short', 'error', 'interrupt'])
def test_output_failure_not_success_or_repeated_operation(authorization, managed, monkeypatch, kind):
    managed['value'] = snap(authorization); writes=[]
    class Output:
        def write(self, data):
            assert managed['closed']; writes.append(data)
            if kind == 'error': raise BrokenPipeError(SENTINEL)
            if kind == 'interrupt': raise KeyboardInterrupt()
            return len(data)-1
        def flush(self): pytest.fail('short/failed write must not flush')
    control = ResearchDispatchStop()
    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, 'stdout', Output())
        code = cli.main(['inspect-uncapped', '--authorization-id', authorization.authorization_id], default_root=ROOT, stop=control)
    assert code == (130 if kind == 'interrupt' else 1)
    assert len(writes) == len(managed['calls']) == 1
    assert control.is_stopped() == (kind == 'interrupt')
