"""Separate same-assistant boundary review, not external source authentication."""
from dataclasses import replace
import io
import json

import pytest

from polymarket_alpha_lab import research_paper_operator as cli
from polymarket_alpha_lab.research_paper_capture_codec import checksum, dump, encode_paper_scenario
from polymarket_alpha_lab.research_paper_inputs import json_value
from tests.test_research_paper_operator import ROOT, PRIVATE, managed, invoke


def test_invalid_direct_operation_does_not_echo_untrusted_argument(managed, capsys):
    code = cli.operate_paper(root=ROOT, operation=PRIVATE, record_id='record-1')
    output = capsys.readouterr()
    assert code == 2 and PRIVATE not in output.out + output.err
    assert 'root' not in managed


@pytest.mark.parametrize('tail', [b'', b'\n', b'\r\n', b'junk'], ids=['bare', 'LF', 'CRLF', 'residue'])
def test_short_binary_reads_require_eof_not_first_complete_json(managed, capsys, tail):
    data = encode_paper_scenario(managed['scenario']).encode() + tail
    class ShortReads:
        def __init__(self):
            self.source = io.BytesIO(data)
            self.count = 0
        def read(self, size):
            assert 1 <= size <= 65536
            self.count += 1
            return self.source.read(min(size, 3))
    stream = ShortReads()
    code, out = invoke(managed, capsys, stream=stream)
    assert code == (2 if tail == b'junk' else 0)
    assert stream.count > 2 and stream.source.read() == b''


def test_over_limit_reader_stops_at_one_overflow_byte(managed, capsys, monkeypatch):
    monkeypatch.setattr(cli, 'MAX_PAYLOAD_BYTES', 64)
    class Bounded:
        count = 0
        def read(self, size):
            self.count += 1
            assert self.count == 1 and size == 67
            return b'x' * size
    code, out = invoke(managed, capsys, stream=Bounded())
    assert code == 2 and out['operation_entered'] is False


@pytest.mark.parametrize('bad', [None, 'not bytes', bytearray(b'{}')], ids=['none', 'text', 'bytearray'])
def test_nonbinary_stream_result_rejected(managed, capsys, bad):
    class Bad:
        def read(self, size):
            return bad
    assert invoke(managed, capsys, stream=Bad())[0] == 2
    assert 'root' not in managed


def test_inspect_does_not_consume_stream(managed, capsys):
    class Forbidden:
        def read(self, *args):
            pytest.fail('inspect consumed input')
    assert invoke(managed, capsys, 'inspect-paper', stream=Forbidden())[0] == 0


def test_same_record_hash_does_not_allow_foreign_record_id(managed, capsys):
    r = managed['receipt']
    s = replace(r.scenario, record_id='foreign')
    body = json.loads(r.result_payload)
    body.update(record_id='foreign', scenario_binding=json_value(s.binding()))
    managed['receipt'] = replace(r, scenario=s, result_payload=dump(body))
    code, out = invoke(managed, capsys, 'inspect-paper')
    assert code == 1 and out['result'] is None


def test_mutated_receipt_flags_are_not_output_as_success(managed, capsys):
    object.__setattr__(managed['receipt'], 'readonly', False)
    code, out = invoke(managed, capsys)
    assert code == 1 and out['result'] is None


@pytest.mark.parametrize('fault', ['short-write', 'write', 'flush', 'interrupt'])
def test_output_failure_after_commit_is_nonzero_without_second_write(managed, monkeypatch, fault):
    class Broken:
        calls = 0
        def write(self, text):
            self.calls += 1
            assert managed['closed'] and self.calls == 1 and PRIVATE not in text
            if fault == 'short-write':
                return len(text) - 1
            if fault == 'write':
                raise OSError(PRIVATE)
            if fault == 'interrupt':
                raise KeyboardInterrupt(PRIVATE)
            return len(text)
        def flush(self):
            raise SystemExit(PRIVATE)
    target = Broken()
    payload = encode_paper_scenario(managed['scenario'])
    with monkeypatch.context() as mp:
        mp.setattr(cli.sys, 'stdout', target)
        code = cli.operate_paper(root=ROOT, operation='capture-paper', record_id='record-1',
            input_sha256=checksum(payload), allow_paper_write=True, stream=io.BytesIO(payload.encode()))
    assert code == (130 if fault == 'interrupt' else 1)
    assert target.calls == 1 and len(managed['calls']) == 1


def test_canonical_input_hash_does_not_authorize_pretty_printed_json(managed, capsys):
    raw = json.dumps(json.loads(encode_paper_scenario(managed['scenario'])), indent=2)
    code, out = invoke(managed, capsys, stream=io.BytesIO(raw.encode()), input_sha256=checksum(raw))
    assert code == 2 and not out['operation_entered']


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('change', ['size', 'fees', 'assumptions'])
def test_correct_original_receipt_survives_post_save_argument_mutation(
        monkeypatch, capsys, team, change):
    """A correct receipt remains authoritative when only the adapter argument drifts."""
    from contextlib import contextmanager
    from decimal import Decimal
    from tests.test_research_paper_capture import sample
    _, scenario, _, original = sample(team=team)
    payload = encode_paper_scenario(scenario)
    approved = checksum(payload)
    expected = original.to_dict()
    state = dict(calls=0, closed=False)

    class Session:
        def capture_paper_research(self, *, scenario, allow_paper_write):
            state['calls'] += 1
            assert allow_paper_write is True
            assert encode_paper_scenario(scenario) == payload
            if change == 'size':
                object.__setattr__(scenario, 'requested_size', Decimal('6'))
            elif change == 'fees':
                object.__setattr__(scenario.costs, 'taker_fee_rate', Decimal('.03'))
            else:
                object.__setattr__(scenario, 'assumptions_id', 'changed-assumptions')
            assert encode_paper_scenario(scenario) != payload
            return original

    class Database:
        def __init__(self, root):
            assert root == ROOT

        @contextmanager
        def session(self):
            try:
                yield Session()
            finally:
                state['closed'] = True

    monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    code = cli.operate_paper(root=ROOT, operation='capture-paper', record_id=scenario.record_id,
        input_sha256=approved, allow_paper_write=True, stream=io.BytesIO(payload.encode()))
    output = capsys.readouterr()
    body = json.loads(output.out)
    assert code == 0 and body['result'] == expected
    assert body['result']['input_sha256'] == approved and state == dict(calls=1, closed=True)
    assert output.err == '' and encode_paper_scenario(scenario) == payload


@pytest.mark.parametrize('operation', ['capture-paper', 'inspect-paper'])
def test_cleanup_cannot_rewrite_the_validated_receipt_projection(
        managed, monkeypatch, capsys, operation):
    """Do not publish a retained mutable receipt object after session cleanup."""
    from contextlib import contextmanager
    from decimal import Decimal
    original = managed['receipt']
    expected = original.to_dict()

    class Session:
        def capture_paper_research(self, **kw):
            managed['calls'].append(('capture', kw))
            return original

        def inspect_paper_research(self, **kw):
            managed['calls'].append(('inspect', kw))
            return original

    class Database:
        def __init__(self, root):
            pass

        @contextmanager
        def session(self):
            try:
                yield Session()
            finally:
                object.__setattr__(original.scenario, 'requested_size', Decimal('6'))
                managed['closed'] = True

    monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    code, out = invoke(managed, capsys, operation)
    assert code == 0 and out['result'] == expected
    assert managed['closed'] and len(managed['calls']) == 1
