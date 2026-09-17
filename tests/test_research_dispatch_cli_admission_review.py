"""Separate self-review of canonical admission and unknown-commit boundaries."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import io
import json

import pytest

from polymarket_alpha_lab import research_dispatch_cli as cli
from polymarket_alpha_lab.research_dispatch import StoredResearchBatch
from polymarket_alpha_lab.research_model_budget import StoredModelBudget
from tests.test_research_dispatch_cli import ROOT, NOW, PRIVATE, managed, READS, read_value, RUN
from tests.test_research_dispatch_cli_admission import admission, invoke


def test_returning_mutated_input_alias_cannot_replace_reviewed_payload(admission, monkeypatch, capsys):
    calls = []
    class Session:
        def enqueue_research_batch(self, *, batch, allow_queue_write):
            calls.append(1)
            object.__setattr__(batch, 'batch_id', 'changed-after-review')
            return StoredResearchBatch(batch, NOW)
        def create_model_budget(self, *, policy, allow_budget_write):
            calls.append(1)
            object.__setattr__(policy, 'budget_id', 'changed-after-review')
            return StoredModelBudget(policy, NOW)
    class Database:
        def __init__(self, root): pass
        @contextmanager
        def session(self): yield Session()
    monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(admission.value.payload.encode())))
    code = cli.main(admission.arguments+[admission.permission], default_root=ROOT)
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out['result'] is None
    assert out['business_writes_possible'] and calls == [1]


@pytest.mark.parametrize('kind', ['invalid-hash', 'no-permission'])
def test_preflight_never_accesses_default_stream(admission, managed, monkeypatch, capsys, kind):
    class Input:
        @property
        def buffer(self): pytest.fail('premature stdin access')
    monkeypatch.setattr(cli.sys, 'stdin', Input())
    args = list(admission.arguments)
    if kind == 'invalid-hash': args[-1] = 'not-a-digest'; args += [admission.permission]
    assert cli.main(args, default_root=ROOT) == 2
    assert json.loads(capsys.readouterr().out)['operation_entered'] is False
    assert 'root' not in managed


@pytest.mark.parametrize('error,code', [(OSError(PRIVATE), 2), (SystemExit(0), 2), (KeyboardInterrupt(PRIVATE), 130)])
def test_default_stream_lookup_fails_with_fixed_safe_error(admission, managed, monkeypatch, capsys, error, code):
    class Input:
        @property
        def buffer(self): raise error
    monkeypatch.setattr(cli.sys, 'stdin', Input())
    assert cli.main(admission.arguments+[admission.permission], default_root=ROOT) == code
    output = capsys.readouterr()
    assert PRIVATE not in output.out+output.err and 'root' not in managed
    assert json.loads(output.out)['result'] is None


@pytest.mark.parametrize('returned', [None, 'text', bytearray(b'bytes'), b'x'*40])
def test_invalid_or_oversized_chunk_fails_before_decode(admission, managed, monkeypatch, capsys, returned):
    calls = []
    monkeypatch.setattr(cli, 'MAX_BATCH_BYTES', 8)
    monkeypatch.setattr(cli, 'MAX_POLICY_BYTES', 8)
    def read(size): calls.append(size); return returned
    monkeypatch.setattr(cli, 'decode_batch', lambda *a: pytest.fail('decoded unbounded input'))
    monkeypatch.setattr(cli, 'decode_budget', lambda *a: pytest.fail('decoded unbounded input'))
    code, out = invoke(admission, managed, monkeypatch, capsys, source=SimpleNamespace(read=read))
    assert code == 2 and calls == [11] and 'root' not in managed


@pytest.mark.parametrize('frame', [b'', b'\n', b'\r\n'])
def test_exact_byte_cap_succeeds_without_larger_read(admission, managed, monkeypatch, capsys, frame):
    raw = admission.value.payload.encode()
    monkeypatch.setattr(cli, 'MAX_BATCH_BYTES' if admission.kind == 'batch' else 'MAX_POLICY_BYTES', len(raw))
    source = io.BytesIO(raw+frame)
    counts = []
    def read(size): counts.append(size); return source.read(size)
    code, out = invoke(admission, managed, monkeypatch, capsys, source=SimpleNamespace(read=read))
    assert code == 0 and counts[-1] <= 3


@pytest.mark.parametrize('operation', ['batch', 'budget', 'read', 'blocked-run'])
@pytest.mark.parametrize('fault', ['short', 'flush', 'zero-exit', 'interrupt', 'bool-count'])
def test_output_failure_is_nonzero_and_never_second_write(managed, monkeypatch, operation, fault):
    from tests.test_research_dispatch_cli import request, snapshot
    from polymarket_alpha_lab.research_dispatch import ResearchBatch
    if operation == 'batch':
        value = ResearchBatch('batch', (request(),))
        managed['value'] = StoredResearchBatch(value, NOW)
        args = ['enqueue-batch','--batch-id','batch','--input-sha256',value.content_sha256,'--allow-queue-write']
    elif operation == 'budget':
        managed['value'] = snapshot().stored; value = managed['value'].policy
        args = ['create-budget','--budget-id','budget','--input-sha256',value.content_sha256,'--allow-budget-write']
    else:
        value = None
        args = ['inspect-budget','--budget-id','budget'] if operation == 'read' else RUN
        managed['value'] = read_value('inspect-budget')
    writes, flushes, closed_at_write = [], [], []
    class Output:
        def write(self, text):
            writes.append(text)
            closed_at_write.append(managed.get('closed', False))
            assert operation == 'blocked-run' or managed['closed']
            if fault == 'zero-exit': raise SystemExit(0)
            if fault == 'interrupt': raise KeyboardInterrupt(PRIVATE)
            if fault == 'bool-count': return True
            return len(text)-1 if fault == 'short' else len(text)
        def flush(self):
            flushes.append(1)
            if fault == 'flush': raise OSError(PRIVATE)
    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, 'stdout', Output())
        patch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(value.payload.encode() if value else b'')))
        code = cli.main(args, default_root=ROOT)
    assert code == (130 if fault == 'interrupt' else 1)
    assert len(writes) == 1 and len(flushes) == (1 if fault == 'flush' else 0)
    assert closed_at_write == [operation != 'blocked-run']
    assert len(managed['calls']) == (0 if operation == 'blocked-run' else 1)


@pytest.mark.parametrize('phase', ['buffer', 'read'])
@pytest.mark.parametrize('output_fault', ['none', 'short', 'write-error'])
def test_input_interrupt_preserves_shared_stop_even_when_error_output_fails(
        admission, managed, monkeypatch, phase, output_fault):
    """Set stop for the INPUT interruption before attempting its error receipt."""
    from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
    control = ResearchDispatchStop()
    reads, writes, flushes = [], [], []

    class Input:
        @property
        def buffer(self):
            if phase == 'buffer':
                raise KeyboardInterrupt(PRIVATE)
            return self

        def read(self, count):
            reads.append(count)
            raise KeyboardInterrupt(PRIVATE)

    class Output:
        def write(self, text):
            writes.append(text)
            if output_fault == 'write-error':
                raise OSError(PRIVATE)
            return len(text) - int(output_fault == 'short')

        def flush(self):
            flushes.append(1)

    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, 'stdin', Input())
        patch.setattr(cli.sys, 'stdout', Output())
        code = cli.main(admission.arguments + [admission.permission], default_root=ROOT, stop=control)
    assert code == (130 if output_fault == 'none' else 1)
    assert control.is_stopped()
    assert 'root' not in managed and not managed['calls']
    assert len(reads) == int(phase == 'read')
    assert len(writes) == 1 and len(flushes) == int(output_fault == 'none')
    assert PRIVATE not in writes[0]


@pytest.mark.parametrize('stage', ['blocked', 'invalid', 'stored'])
@pytest.mark.parametrize('fault', ['short', 'flush'])
def test_ordinary_admission_output_failure_does_not_cancel_shared_work(
        admission, managed, monkeypatch, stage, fault):
    from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
    control = ResearchDispatchStop()
    managed['value'] = admission.stored
    raw = b'invalid' if stage == 'invalid' else admission.value.payload.encode()
    writes = []

    class Output:
        def write(self, text):
            writes.append(text)
            return len(text) - int(fault == 'short')

        def flush(self):
            raise OSError(PRIVATE)

    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw)))
        patch.setattr(cli.sys, 'stdout', Output())
        code = cli.main(admission.arguments + ([] if stage == 'blocked' else [admission.permission]),
                        default_root=ROOT, stop=control)
    assert code == 1 and not control.is_stopped()
    assert len(writes) == 1 and PRIVATE not in writes[0]
    assert len(managed['calls']) == int(stage == 'stored')
    assert managed.get('closed', False) is (stage == 'stored')
