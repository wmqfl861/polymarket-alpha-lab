"""Separately designed adversarial self-review; no claim of external audit."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import io
import json

import pytest

from polymarket_alpha_lab import research_resolution_confirmation as core
from polymarket_alpha_lab import research_resolution_confirmation_cli as cli
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
from tests.test_research_resolution_confirmation import (
    ROOT, OPEN, PRIVATE, fixture, build, receipt, input_bytes, managed, invoke,
)


@pytest.mark.parametrize('fault',['foreign-record','opposite-outcome','different-source'])
def test_cli_must_bind_result_to_instruction_not_only_review_id(managed,capsys,fault):
    i,e,c=fixture()
    if fault=='foreign-record':
        other,ex,ca=fixture(record_id='foreign')
        other=replace(other,review_id=i.review_id)
        wrong=receipt(build(other,ex,ca),ex.request)
    elif fault=='opposite-outcome':
        other,ex,ca=fixture(yes=False)
        wrong=receipt(build(other,ex,ca),ex.request)
    else:
        other=replace(i,confirmation=replace(i.confirmation,source_text='other independently reviewed source'))
        wrong=receipt(build(other,e,c),e.request)
    managed['receipt']=wrong
    code,out=invoke(managed,capsys)
    assert code==1 and out['result'] is None
    assert out['business_writes_possible'] is True and managed['closed']


@pytest.mark.parametrize('position',['top','proof'])
def test_stdin_rejects_extra_fields_and_duplicate_approvals(position):
    i,_,_=fixture();raw=input_bytes(i);body=json.loads(raw)
    target=body if position=='top' else body['confirmation']
    target['unexpected']='do not ignore me'
    with pytest.raises(ValueError):cli.decode_review(json.dumps(body).encode())
    key='record_id' if position=='top' else 'actual_yes'
    raw=raw.replace(('"'+key+'":').encode(),('"'+key+'": null,"'+key+'":').encode(),1)
    with pytest.raises(ValueError):cli.decode_review(raw)


@pytest.mark.parametrize('delta_seconds',[-1,0,1,599,600,601])
def test_snapshot_age_boundary_matches_original_gate(delta_seconds):
    i,e,c=fixture();confirmed=c.submission.snapshot.fetched_at+timedelta(seconds=delta_seconds)
    if delta_seconds<0:
        with pytest.raises(ValueError):replace(i.confirmation,confirmed_at=confirmed)
        return
    i=replace(i,confirmation=replace(i.confirmation,confirmed_at=confirmed))
    if delta_seconds>600:
        with pytest.raises(ValueError):build(i,e,c)
    else:
        assert build(i,e,c).checked_at==confirmed


def test_read_call_never_requests_more_than_limit_plus_one(managed,capsys):
    class Bounded:
        def read(self,size):
            assert size==cli.MAX_INPUT_BYTES+1
            return b' '*size
    code=cli.confirm_from_stdin(root=ROOT,stream=Bounded(),allow_resolution_write=True)
    assert code==2 and 'root' not in managed


def test_input_exceptions_are_sanitized_without_db_access(managed,capsys):
    class Broken:
        def read(self,size):raise SystemExit(PRIVATE)
    code=cli.confirm_from_stdin(root=ROOT,stream=Broken(),allow_resolution_write=True)
    output=capsys.readouterr()
    assert code==2 and PRIVATE not in output.out+output.err and 'root' not in managed


def test_terms_and_hash_tampering_is_not_repaired_in_place():
    i,e,c=fixture();before=(e.request.payload,encode_resolution(c.submission))
    object.__setattr__(i,'source_pair','BTCUSD')
    with pytest.raises(ValueError):build(i,e,c)
    assert (e.request.payload,encode_resolution(c.submission))==before


# A distinct review pass checks correct receipts and failure-before-write behavior.
@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('change', ['review-id', 'reviewer', 'source-text', 'source-reference', 'time', 'outcome', 'record'])
def test_review_original_cli_receipt_survives_adapter_argument_changes(team, change, monkeypatch, capsys):
    from contextlib import contextmanager
    from tests.test_research_resolution_confirmation import binding_alternative, mutate_instruction_argument
    instruction, execution, candidate = fixture(team)
    approved_input = input_bytes(instruction)
    approved = receipt(build(instruction, execution, candidate), execution.request)
    expected = cli.resolution_review_summary(approved, review_id=instruction.review_id)
    replacement, _, _ = binding_alternative(instruction, execution, candidate, change)
    calls, closed = [], []
    class Session:
        def confirm_crypto_resolution(self, *, instruction, allow_resolution_write):
            calls.append(input_bytes(instruction))
            mutate_instruction_argument(instruction, replacement)
            return approved
    class Database:
        def __init__(self, root):
            assert root == ROOT
        @contextmanager
        def session(self):
            try:
                yield Session()
            finally:
                closed.append(True)
    monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    assert cli.confirm_from_stdin(root=ROOT, stream=io.BytesIO(approved_input), allow_resolution_write=True) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['result'] == expected and output.err == ''
    assert calls == [approved_input] and closed == [True]
    assert input_bytes(instruction) == approved_input and PRIVATE not in output.out


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('change', ['review-id', 'reviewer', 'source-text', 'source-reference', 'time'])
def test_review_original_service_receipt_survives_writer_argument_changes(team, change, monkeypatch):
    from dataclasses import fields
    from tests.test_research_resolution_confirmation import binding_alternative
    instruction, execution, candidate = fixture(team)
    approved_input = input_bytes(instruction)
    original = build(instruction, execution, candidate)
    approved_payload = encode_resolution(original)
    approved_receipt = receipt(original, execution.request)
    replacement, _, _ = binding_alternative(instruction, execution, candidate, change)
    changed = build(replacement, execution, candidate)
    writes = []
    def write(dsn, *, submission):
        writes.append(encode_resolution(submission))
        for item in fields(submission):
            object.__setattr__(submission, item.name, getattr(changed, item.name))
        return approved_receipt
    monkeypatch.setattr(core, 'inspect_captured_research_with_psycopg', lambda *a, **k: execution)
    monkeypatch.setattr(core, 'load_resolution_review_with_psycopg', lambda *a, **k: candidate)
    monkeypatch.setattr(core, 'record_resolution_review_with_psycopg', write)
    result = core.confirm_crypto_resolution_with_psycopg('synthetic', instruction=instruction, allow_resolution_write=True)
    assert result == approved_receipt and writes == [approved_payload]
    assert result is not approved_receipt and result.outcome is not approved_receipt.outcome
    assert input_bytes(instruction) == approved_input


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
def test_review_receipt_projection_is_fixed_before_managed_cleanup(team, monkeypatch, capsys):
    from contextlib import contextmanager
    instruction, execution, candidate = fixture(team)
    approved = receipt(build(instruction, execution, candidate), execution.request)
    expected = cli.resolution_review_summary(approved, review_id=instruction.review_id)
    calls, closed = [], []
    class Session:
        def confirm_crypto_resolution(self, **kwargs):
            calls.append(1)
            return approved
    class Database:
        def __init__(self, root):
            assert root == ROOT
        @contextmanager
        def session(self):
            try:
                yield Session()
            finally:
                object.__setattr__(approved.submission, 'review_id', 'late-unapproved-review')
                object.__setattr__(approved.submission.confirmation, 'source_text', 'late-unapproved-text')
                object.__setattr__(approved.outcome, 'actual_yes', False)
                closed.append(True)
    monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    assert cli.confirm_from_stdin(root=ROOT, stream=io.BytesIO(input_bytes(instruction)), allow_resolution_write=True) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['result'] == expected and output.err == ''
    assert calls == [1] and closed == [True] and 'late-unapproved' not in output.out


@pytest.mark.parametrize('error', [OSError('synthetic encoding failure'), KeyboardInterrupt(), SystemExit(0)])
def test_review_failed_binding_does_not_enter_writer(monkeypatch, error):
    instruction, execution, candidate = fixture()
    submission = build(instruction, execution, candidate)
    monkeypatch.setattr(core, 'inspect_captured_research_with_psycopg', lambda *a, **k: execution)
    monkeypatch.setattr(core, 'load_resolution_review_with_psycopg', lambda *a, **k: candidate)
    # Only the post-build canonical binding is faulty; no real storage is used.
    monkeypatch.setattr(core, 'build_crypto_resolution_confirmation', lambda **kw: submission)
    def encode(value):
        raise error
    monkeypatch.setattr(core, 'encode_resolution', encode)
    monkeypatch.setattr(core, 'record_resolution_review_with_psycopg', lambda *a, **k: pytest.fail('writer reached'))
    with pytest.raises(type(error)) as caught:
        core.confirm_crypto_resolution_with_psycopg('synthetic', instruction=instruction, allow_resolution_write=True)
    assert caught.value is error
