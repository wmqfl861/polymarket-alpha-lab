"""Operator composition tests with real typed receipts and no DB/network."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab import research_paper_operator as cli
from polymarket_alpha_lab import research_dispatch_cli as dispatch
from polymarket_alpha_lab.research_paper_capture_codec import checksum, encode_paper_scenario
from tests.test_research_paper_capture import sample

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = 'synthetic-private-failure-do-not-output'


@pytest.fixture
def managed(monkeypatch):
    _, scenario, _, receipt = sample()
    state = dict(scenario=scenario, receipt=receipt, calls=[], closed=False)
    class Session:
        def capture_paper_research(self, **kw):
            state['calls'].append(('capture', kw))
            if state.get('error'):
                raise state['error']
            return state['receipt']
        def inspect_paper_research(self, **kw):
            state['calls'].append(('inspect', kw))
            if state.get('error'):
                raise state['error']
            return state['receipt']
    class Database:
        def __init__(self, root):
            state['root'] = root
        @contextmanager
        def session(self):
            try:
                if state.get('enter_error'):
                    raise state['enter_error']
                yield Session()
            finally:
                state['closed'] = True
                if state.get('exit_error'):
                    raise state['exit_error']
    monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    return state


def invoke(state, capsys, operation='capture-paper', **kw):
    payload = encode_paper_scenario(state['scenario'])
    options = dict(root=ROOT, operation=operation, record_id=state['scenario'].record_id)
    if operation == 'capture-paper':
        options.update(input_sha256=checksum(payload), allow_paper_write=True,
                       stream=io.BytesIO(payload.encode()))
    options.update(kw)
    code = cli.operate_paper(**options)
    text = capsys.readouterr()
    assert PRIVATE not in text.out + text.err
    return code, json.loads(text.out)


@pytest.mark.parametrize('operation', ['capture-paper', 'inspect-paper'])
def test_original_receipt_and_effect_flags(managed, capsys, operation):
    code, out = invoke(managed, capsys, operation)
    assert code == 0 and out['status'] == 'paper_receipt_returned'
    assert out['result'] == managed['receipt'].to_dict()
    assert out['business_writes_possible'] is (operation == 'capture-paper')
    assert out['model_calls_possible'] is False and out['automatic_retry_permitted'] is False
    assert managed['closed'] and len(managed['calls']) == 1


@pytest.mark.parametrize('status', ['failed', 'blocked'])
def test_saved_negative_is_successful_storage_not_successful_trade(managed, capsys, status):
    _, s, _, r = sample(status=status)
    managed.update(scenario=s, receipt=r)
    code, out = invoke(managed, capsys)
    assert code == 0 and out['result']['result']['status'] == 'not_simulated'
    assert out['result']['paper_trades_created'] == 0


def test_not_found_is_distinct_from_failed_capture(managed, capsys):
    managed['receipt'] = None
    code, out = invoke(managed, capsys, 'inspect-paper')
    assert code == 3 and out['result'] is None and out['business_writes_possible'] is False
    code, out = invoke(managed, capsys)
    assert code == 1 and out['result'] is None and out['business_writes_possible'] is True


@pytest.mark.parametrize('ending', [b'', b'\n', b'\r\n'], ids=['bare', 'LF', 'CRLF'])
def test_reviewed_hash_ignores_only_one_transport_terminator(managed, capsys, ending):
    payload = encode_paper_scenario(managed['scenario'])
    code, _ = invoke(managed, capsys, stream=io.BytesIO(payload.encode() + ending))
    assert code == 0


@pytest.mark.parametrize('ending', [b'\n\n', b'\r', b' ', b'\r\n\r\n', b'junk'],
                         ids=['two-LF', 'CR', 'space', 'two-CRLF', 'junk'])
def test_extra_transport_content_rejected_before_db(managed, capsys, ending):
    payload = encode_paper_scenario(managed['scenario'])
    code, out = invoke(managed, capsys, stream=io.BytesIO(payload.encode() + ending))
    assert code == 2 and out['operation_entered'] is False and 'root' not in managed


@pytest.mark.parametrize('raw', [b'', b'null', b'{}', b'\xff', b'\xef\xbb\xbf{}'],
                         ids=['empty', 'null', 'object', 'invalid-UTF8', 'BOM'])
def test_invalid_input_sanitized(managed, capsys, raw):
    code, out = invoke(managed, capsys, stream=io.BytesIO(raw), input_sha256=checksum('{}'))
    assert code == 2 and out['business_writes_possible'] is False and not managed['calls']


@pytest.mark.parametrize('hash_value', ['a'*64, 'A'*64, PRIVATE, '', None],
                         ids=['wrong', 'uppercase', 'bad', 'empty', 'missing'])
def test_digest_required_without_substitution(managed, capsys, hash_value):
    code, out = invoke(managed, capsys, input_sha256=hash_value)
    assert code == 2 and not out['operation_entered'] and 'root' not in managed


@pytest.mark.parametrize('flag', [False, 1, None, 'true'])
def test_no_exact_approval_never_consumes_stdin(managed, capsys, flag):
    class Forbidden:
        def read(self, *args):
            pytest.fail('no stdin read without explicit approval')
    code, out = invoke(managed, capsys, allow_paper_write=flag, stream=Forbidden())
    assert code == 2 and out['status'] == 'blocked' and 'root' not in managed


def test_record_id_mismatch_before_database(managed, capsys):
    code, _ = invoke(managed, capsys, record_id='some-other-record')
    assert code == 2 and 'root' not in managed


@pytest.mark.parametrize('where', ['error', 'exit_error', 'enter_error'])
@pytest.mark.parametrize('fault', [RuntimeError(PRIVATE), SystemExit(0), SystemExit(PRIVATE), KeyboardInterrupt(PRIVATE)],
                         ids=['exception', 'exit-zero', 'exit-text', 'interrupt'])
def test_uncertain_effects_and_cleanup_not_false_success(managed, capsys, where, fault):
    managed[where] = fault
    code, out = invoke(managed, capsys)
    assert code == (130 if isinstance(fault, KeyboardInterrupt) else 1)
    assert out['result'] is None and out['business_writes_possible'] is True
    assert len(managed['calls']) <= 1 and managed['closed']


@pytest.mark.parametrize('fault', [RuntimeError(PRIVATE), SystemExit(0), KeyboardInterrupt(PRIVATE)],
                         ids=['exception', 'exit-zero', 'interrupt'])
def test_input_failure_stays_before_database(managed, capsys, fault):
    class Broken:
        def read(self, limit):
            raise fault
    code, out = invoke(managed, capsys, stream=Broken())
    assert code == (130 if isinstance(fault, KeyboardInterrupt) else 2)
    assert out['business_writes_possible'] is False and 'root' not in managed


def test_wrong_typed_receipt_cannot_claim_success(managed, capsys):
    managed['receipt'] = object()
    code, out = invoke(managed, capsys)
    assert code == 1 and out['result'] is None


def test_changed_input_receipt_is_rejected(managed, capsys):
    # The original request is still for5shares, not the returned6share receipt.
    r = managed['receipt']
    s = replace(r.scenario, requested_size=Decimal('6'))
    body = json.loads(r.result_payload)
    from polymarket_alpha_lab.research_paper_capture_codec import dump
    from polymarket_alpha_lab.research_paper_inputs import json_value
    body['scenario_binding'] = json_value(s.binding())
    managed['receipt'] = replace(r, scenario=s, result_payload=dump(body))
    code, out = invoke(managed, capsys)
    assert code == 1 and out['result'] is None


def test_serialization_and_write_after_cleanup(managed, capsys, monkeypatch):
    original = cli.json.dumps
    def render(obj, **kw):
        if type(obj) is dict and obj.get('operation') == 'capture-paper':
            assert managed['closed']
        return original(obj, **kw)
    monkeypatch.setattr(cli.json, 'dumps', render)
    assert invoke(managed, capsys)[0] == 0


@pytest.mark.parametrize('args', [
    ['capture-paper'], ['capture-paper', '--record-id', 'ok', '--input-sha256', 'a'*64, '--allow-model-calls'],
    ['inspect-paper', '--record-id', 'ok', '--allow-paper-write'],
    ['inspect-paper', '--record-id', 'ok', '--rec', 'bad'],
    ['inspect-paper', '--record-id', PRIVATE + ' secret'],
], ids=['missing', 'model-flag', 'write-on-inspect', 'abbreviation', 'invalid-id'])
def test_new_parser_modes_fail_without_echo_or_db(managed, capsys, args):
    with pytest.raises(SystemExit) as error:
        dispatch.main(args, default_root=ROOT)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert PRIVATE not in output.err and not managed['calls']


def test_existing_command_routes_canonical_capture_and_query(managed, capsys, monkeypatch):
    payload = encode_paper_scenario(managed['scenario'])
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(payload.encode())))
    assert dispatch.main(['capture-paper', '--record-id', managed['scenario'].record_id,
        '--input-sha256', checksum(payload), '--allow-paper-write'], default_root=ROOT) == 0
    capsys.readouterr()
    assert dispatch.main(['inspect-paper', '--record-id', managed['scenario'].record_id], default_root=ROOT) == 0
    capsys.readouterr()
    assert [x[0] for x in managed['calls']] == ['capture', 'inspect']


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('change', ['size', 'fees', 'assumptions'])
def test_capture_receipt_is_bound_to_approved_hash_not_mutated_argument(
        monkeypatch, capsys, team, change):
    """A faulty adapter must not make a changed scenario its own approval.

    Use a recomputed, internally valid receipt, not merely broken result JSON.
    This is collaborator-fault injection, not a hostile-Python sandbox claim.
    """
    from polymarket_alpha_lab import research_paper_capture as store
    from polymarket_alpha_lab.research_paper_capture_codec import dump
    execution, scenario, history, original = sample(team=team)
    payload = encode_paper_scenario(scenario)
    approved = checksum(payload)
    state = dict(calls=0, closed=False)

    class Session:
        def capture_paper_research(self, *, scenario, allow_paper_write):
            assert allow_paper_write is True
            state['calls'] += 1
            if change == 'size':
                object.__setattr__(scenario, 'requested_size', Decimal('6'))
            elif change == 'fees':
                object.__setattr__(scenario.costs, 'taker_fee_rate', Decimal('.03'))
            else:
                object.__setattr__(scenario, 'assumptions_id', 'changed-assumptions')
            receipt = replace(original, scenario=scenario,
                result_payload=dump(store._result_for(history, scenario, execution)))
            assert checksum(encode_paper_scenario(receipt.scenario)) != approved
            # The ordinary receipt validator and result reconstruction succeed.
            assert replace(receipt) == receipt
            return receipt

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
    assert code == 1 and body['reason_code'] == 'paper_operator_operation_failed'
    assert body['result'] is None and body['business_writes_possible'] is True
    assert body['automatic_retry_permitted'] is False
    assert state == dict(calls=1, closed=True)
    assert output.err == '' and PRIVATE not in output.out
    assert encode_paper_scenario(scenario) == payload
