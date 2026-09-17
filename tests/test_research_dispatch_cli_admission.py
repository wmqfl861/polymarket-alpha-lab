"""Reviewed task/budget admission via existing codecs; synthetic sessions only."""
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
import io
import json

import pytest

from polymarket_alpha_lab import research_dispatch_cli as cli
from polymarket_alpha_lab.research_dispatch import ResearchBatch, StoredResearchBatch
from tests.test_research_dispatch_cli import ROOT, NOW, PRIVATE, managed, request as sample_request, snapshot


@pytest.fixture(params=['batch', 'budget'])
def admission(request):
    kind = request.param
    if kind == 'batch':
        value = ResearchBatch('batch', (sample_request(),))
        stored = StoredResearchBatch(value, NOW)
    else:
        stored = snapshot().stored
        value = stored.policy
    operation, key, permission = (('enqueue-batch', '--batch-id', '--allow-queue-write')
                                  if kind == 'batch' else
                                  ('create-budget', '--budget-id', '--allow-budget-write'))
    return SimpleNamespace(kind=kind, value=value, stored=stored, permission=permission,
        arguments=[operation, key, kind, '--input-sha256', value.content_sha256],
        method='enqueue_research_batch' if kind == 'batch' else 'create_model_budget')


def invoke(admission, managed, monkeypatch, capsys, *, raw=None, allowed=True, source=None):
    managed['value'] = admission.stored
    if source is None:
        source = io.BytesIO(admission.value.payload.encode() if raw is None else raw)
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=source))
    code = cli.main(admission.arguments + ([admission.permission] if allowed else []), default_root=ROOT,
                    model_factory=lambda _: pytest.fail('admission called a model factory'))
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err
    assert output.err == ''
    return code, json.loads(output.out)


@pytest.mark.parametrize('frame', [b'', b'\n', b'\r\n'])
def test_admit_exact_original_value_once_and_return_only_metadata(admission, managed, monkeypatch, capsys, frame):
    code, out = invoke(admission, managed, monkeypatch, capsys, raw=admission.value.payload.encode()+frame)
    assert code == 0 and out['status'] == 'admission_receipt_returned'
    assert out['business_writes_possible'] and out['operation_entered'] and managed['closed']
    assert out['model_calls_possible'] is out['public_network_called'] is False
    assert out['provider_charge_bound_verified'] is out['automatic_retry_permitted'] is False
    method, configuration = managed['calls'][0]
    assert len(managed['calls']) == 1 and method == admission.method
    key = 'batch' if admission.kind == 'batch' else 'policy'
    assert configuration[key].payload == admission.value.payload
    assert configuration['allow_queue_write' if key == 'batch' else 'allow_budget_write'] is True
    body = out['result']
    assert body['request_count'] == 1
    assert body['batch_sha256' if key == 'batch' else 'policy_sha256'] == admission.value.content_sha256
    assert body['enqueued_at' if key == 'batch' else 'created_at'] == NOW.isoformat()
    assert 'requests' not in body and 'remaining_calls' not in body and 'reserved_calls' not in body


def test_absent_permission_never_resolves_stdin_or_database(admission, managed, monkeypatch, capsys):
    class Input:
        @property
        def buffer(self): pytest.fail('unapproved input access')
    monkeypatch.setattr(cli.sys, 'stdin', Input())
    assert cli.main(admission.arguments, default_root=ROOT) == 2
    body = json.loads(capsys.readouterr().out)
    assert body['status'] == 'blocked' and body['business_writes_possible'] is False
    assert not managed['calls'] and 'root' not in managed


@pytest.mark.parametrize('suffix', [b' ', b'\n\n', b'\r', b'\x00', b'{}'])
def test_extra_or_noncanonical_transport_rejected_before_database(admission, managed, monkeypatch, capsys, suffix):
    code, out = invoke(admission, managed, monkeypatch, capsys, raw=admission.value.payload.encode()+suffix)
    assert code == 2 and out['result'] is None and out['operation_entered'] is False
    assert not managed['calls'] and 'root' not in managed


@pytest.mark.parametrize('bad', [b'', b'\xff', b'{bad', b'[]', b'null'])
def test_invalid_payload_never_opens_database(admission, managed, monkeypatch, capsys, bad):
    code, out = invoke(admission, managed, monkeypatch, capsys, raw=bad)
    assert code == 2 and out['status'] == 'invalid_input'
    assert 'root' not in managed


@pytest.mark.parametrize('defect', ['hash', 'id', 'extra-field', 'unsafe-flag'])
def test_digest_identity_and_original_closed_schema_preserved(admission, managed, monkeypatch, capsys, defect):
    raw = admission.value.payload.encode()
    if defect == 'hash':
        admission.arguments[-1] = 'a'*64
    elif defect == 'id':
        admission.arguments[2] = 'other'
    else:
        body = json.loads(raw)
        body['unrecognized' if defect == 'extra-field' else 'readonly'] = False
        raw = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
        admission.arguments[-1] = sha256(raw).hexdigest()
    code, out = invoke(admission, managed, monkeypatch, capsys, raw=raw)
    assert code == 2 and out['operation_entered'] is False and 'root' not in managed


def test_small_reads_continue_until_eof(admission, managed, monkeypatch, capsys):
    raw = io.BytesIO(admission.value.payload.encode()+b'\r\n')
    class Chunked:
        def read(self, size): return raw.read(min(size, 3))
    code, out = invoke(admission, managed, monkeypatch, capsys, source=Chunked())
    assert code == 0 and out['result']['request_count'] == 1


@pytest.mark.parametrize('phase', ['error', 'cleanup_error'])
@pytest.mark.parametrize('kind', ['error', 'exit-zero', 'interrupt'])
def test_uncertain_operation_or_cleanup_is_not_retried(admission, managed, monkeypatch, capsys, phase, kind):
    managed[phase] = {'error': RuntimeError(PRIVATE), 'exit-zero': SystemExit(0),
                      'interrupt': KeyboardInterrupt(PRIVATE)}[kind]
    code, out = invoke(admission, managed, monkeypatch, capsys)
    assert code == (130 if kind == 'interrupt' else 1)
    assert out['result'] is None and out['business_writes_possible'] is True
    assert out['model_calls_possible'] is False and len(managed['calls']) == 1


@pytest.mark.parametrize('fault', ['none', 'wrong-kind', 'changed-payload'])
def test_receipt_requires_exact_type_and_entire_input(admission, managed, monkeypatch, capsys, fault):
    if fault == 'none': admission.stored = None
    elif fault == 'wrong-kind': admission.stored = object()
    elif admission.kind == 'batch':
        changed = replace(admission.value, requests=(replace(admission.value.requests[0], model_id='other'),))
        admission.stored = StoredResearchBatch(changed, NOW)
    else:
        admission.stored = replace(admission.stored, policy=replace(admission.value, total_micros=200))
    code, out = invoke(admission, managed, monkeypatch, capsys)
    assert code == 1 and out['result'] is None and managed['closed']


@pytest.mark.parametrize('stage', ['blocked', 'invalid', 'stored'])
@pytest.mark.parametrize('phase', ['write', 'flush'])
def test_admission_output_interrupt_preserves_original_shared_stop(
        admission, managed, monkeypatch, stage, phase):
    """The added admission exits must retain the merged PR51 stop contract."""
    from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
    control = ResearchDispatchStop()
    managed['value'] = admission.stored
    writes, flushes = [], []
    raw = b'invalid' if stage == 'invalid' else admission.value.payload.encode()

    class Output:
        def write(self, text):
            if stage == 'stored':
                assert managed['closed']
            else:
                assert 'root' not in managed
            writes.append(text)
            if phase == 'write':
                raise KeyboardInterrupt(PRIVATE)
            return len(text)

        def flush(self):
            flushes.append(1)
            raise KeyboardInterrupt(PRIVATE)

    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw)))
        patch.setattr(cli.sys, 'stdout', Output())
        code = cli.main(admission.arguments + ([] if stage == 'blocked' else [admission.permission]),
                        default_root=ROOT, stop=control)
    assert code == 130 and control.is_stopped()
    assert len(writes) == 1 and len(flushes) == int(phase == 'flush')
    assert len(managed['calls']) == int(stage == 'stored')
    assert all(PRIVATE not in text for text in writes)
