"""N1 unit tests: ok-compute per-subinput generator (pal-soak-subinput-receipt-v1).

Covers the contract checklist groups: (a) normal control, (b) mid-run failure
with honest prefix, (c) tampered actual results, (d) duplicate inputs and
transport-field (nonce-only) independence, (e) truncated/short-written
receipts, (f) recorder-seam execution ordering with a gated-oracle barrier,
(g) measured byte budgets (2048/3839/3840/4096/512KiB), (h) unknown-field and
schema rejection, plus the main() child interface including one real
`python -S -m` subprocess run per moment (held and sequential, never parallel).
"""
import dataclasses
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.support import soak_okcompute as gen

FAMILIES = ('capture-codec', 'paper-decimal-fill', 'uncapped-authz-codec')
FAST_FAMILY = 'paper-decimal-fill'


def _identity(**overrides):
    base = dict(candidate='{"rv05":"n1-test"}', manifest_sha256='11' * 32,
                contract_sha256='22' * 32,
                generator_sha256=gen.default_generator_sha256(),
                round=2, segment=1, sub_seed=99, scenario='ok-compute')
    base.update(overrides)
    return gen.build_identity(**base)


def _payload(tmp_dir, **overrides):
    payload = {'round': 2, 'segment': 1, 'sub_seed': 99, 'scenario': 'ok-compute',
               'tmp_dir': str(tmp_dir)}
    payload.update(overrides)
    return payload


def _replace_driver(monkeypatch, family, **changes):
    driver = gen.FAMILIES[family]
    patched = dataclasses.replace(driver, **changes)
    monkeypatch.setitem(gen.FAMILIES, family, patched)
    return driver


class _FakeStdin:
    """Minimal sys.stdin stand-in: main() only calls buffer.read(limit)."""

    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


def _hex_pair(index):
    return [hashlib.sha256(b'in-%d' % index).hexdigest(),
            hashlib.sha256(b'out-%d' % index).hexdigest()]


def _synthetic_doc(rows, *, index_origin=0, part=None, part_count=None,
                   candidate='{"rv05":"n1-test"}'):
    counts = {'planned': len(rows), 'generated': len(rows), 'attempted': len(rows),
              'oracle_passed': len(rows), 'completed': len(rows)}
    return gen.build_receipt_doc(_identity(candidate=candidate),
                                 gen.FAMILIES[FAST_FAMILY], index_origin, counts, rows,
                                 part=part, part_count=part_count)


# ---------------------------------------------------------------------------
# (a) normal control
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('family', FAMILIES)
def test_family_sample_consistency(family):
    seen = set()
    for index in range(24):
        sub = gen.derive_input(family, index)
        assert sub.family == family and sub.index == index
        assert sub.input_sha256 == hashlib.sha256(
            gen.normalized_input_bytes(family, index)).hexdigest()
        json.dumps(sub.descriptor)  # JSON-normalizable
        assert not {'round', 'segment', 'sub_seed', 'scenario', 'tmp_dir'} & set(
            sub.descriptor)  # no transport fields in identity
        actual = gen.target_call(family, index)
        assert gen.producer_oracle(family, index, actual) is True
        again = gen.target_call(family, index)
        assert again.result_sha256 == actual.result_sha256  # deterministic
        assert sub.input_sha256 not in seen
        seen.add(sub.input_sha256)
        assert gen.actual_result_sha256(family, actual) == actual.result_sha256


def test_descriptor_matches_independent_contract_rebuild():
    """Mini-N2: rebuild a few descriptors straight from the contract text."""
    from zoneinfo import ZoneInfo
    # F1 index 9: zone ZONES[9%4]=ZONES[1]; status STATUSES[(9//4)%3]=STATUSES[2]
    instant = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=9)
    descriptor = {'family': 'capture-codec', 'zone': 'Australia/Lord_Howe',
                  'instant_utc': instant.astimezone(UTC).isoformat(),
                  'status': 'intake_blocked'}
    assert hashlib.sha256(gen.canonical_json(descriptor)).hexdigest() == \
        gen.input_sha256('capture-codec', 9)
    # F2 index 6: buy (even), tok-6, size 7/8, base 106, captured +6s
    captured = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=6)
    levels = [[format(Decimal('106') + Decimal('0.25') * (j + 1), 'f'),
               format(Decimal((6 + j * 13) % 37 + 1), 'f')] for j in range(5)]
    descriptor = {'family': 'paper-decimal-fill', 'token_id': 'tok-6', 'side': 'buy',
                  'size': format(Decimal(6 % 400 + 1) / Decimal(8), 'f'),
                  'levels': levels, 'captured_at': captured.astimezone(UTC).isoformat()}
    assert hashlib.sha256(gen.canonical_json(descriptor)).hexdigest() == \
        gen.input_sha256('paper-decimal-fill', 6)
    # F3 index 4: keys rec-12..14, approved +4min, expires +1+(4%48) hours
    approved = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=4)
    expires = approved + timedelta(hours=1 + 4 % 48)
    keys = [[f'rec-{4 * 3 + k}', f'{((4 * 3 + k) ** 31) % 2 ** 256:064x}']
            for k in range(3)]
    descriptor = {'family': 'uncapped-authz-codec', 'model_id': 'synthetic',
                  'request_keys': keys, 'approved_at': approved.isoformat(),
                  'expires_at': expires.isoformat(),
                  'adapter_contract_sha256':
                      hashlib.sha256(b'rv05-authz:%d' % 4).hexdigest()}
    assert hashlib.sha256(gen.canonical_json(descriptor)).hexdigest() == \
        gen.input_sha256('uncapped-authz-codec', 4)


@pytest.mark.parametrize('family', FAMILIES)
def test_run_family_full_block_consistency(family, tmp_path):
    result = gen.run_family(family, 0, 16, identity=_identity())
    assert result.ok and result.failure is None
    counts = result.counts
    assert counts == {'planned': 16, 'generated': 16, 'attempted': 16,
                      'oracle_passed': 16, 'completed': 16}
    assert len(result.rows) == 16
    assert len({row[0] for row in result.rows}) == 16  # all distinct inputs
    file_name, doc = result.parts[0]
    assert file_name == f'subinputs-{family}.json'
    assert len(result.parts) == 1
    gen.check_receipt_doc(doc)
    assert doc['index_origin'] == 0
    data = gen.serialize_receipt(doc)
    parsed = json.loads(data)
    assert gen.serialize_receipt(parsed) == data  # byte-stable round trip
    written = gen.write_receipt_file(tmp_path / file_name, doc)
    assert written.rows == 16 and written.bytes_len == len(data)


def test_run_round_summary_and_files(tmp_path):
    summary = gen.run_round(_payload(tmp_path), families=(FAST_FAMILY,),
                            planned_rows=10, identity=_identity(), out_dir=tmp_path)
    assert summary['ok'] is True
    assert summary['echo_round'] == 2 and summary['echo_seed'] == 99
    assert summary['echo_scenario'] == 'ok-compute'
    (entry,) = summary['subinput_families']
    assert entry['family'] == FAST_FAMILY
    assert entry['counts']['completed'] == 10 == entry['rows']
    (file_info,) = entry['files']
    path = tmp_path / file_info['file']
    data = path.read_bytes()
    assert len(data) == file_info['bytes'] == summary['receipt_bytes_total']
    assert hashlib.sha256(data).hexdigest() == file_info['sha256']
    gen.verify_receipt_file(path, data)
    gen.check_receipt_doc(json.loads(data))
    assert data == gen.serialize_receipt(json.loads(data))


# ---------------------------------------------------------------------------
# (b) mid-run failure keeps an honest prefix
# ---------------------------------------------------------------------------

def test_target_error_midway_freezes_prefix(tmp_path):
    original = gen.FAMILIES[FAST_FAMILY].target
    boom = {'index': 5}

    def flaky(sub):
        if sub.index == boom['index']:
            raise RuntimeError('SYNTHETIC-TARGET-FAILURE')
        return original(sub)

    monkeypatch = pytest.MonkeyPatch()
    _replace_driver(monkeypatch, FAST_FAMILY, target=flaky)
    try:
        result = gen.run_family(FAST_FAMILY, 0, 12, identity=_identity())
    finally:
        monkeypatch.undo()
    assert result.ok is False
    assert result.failure == {'family': FAST_FAMILY, 'stage': 'target', 'index': 5,
                              'reason': 'target_error',
                              'detail': "RuntimeError('SYNTHETIC-TARGET-FAILURE')"}
    assert result.counts == {'planned': 12, 'generated': 6, 'attempted': 5,
                             'oracle_passed': 5, 'completed': 5}
    assert len(result.rows) == 5  # honest prefix, never padded to planned
    _, doc = result.parts[0]
    gen.check_receipt_doc(doc)  # partial doc is structurally valid
    assert [row[0] for row in doc['rows']] == [row[0] for row in result.rows]


def test_oracle_rejection_stops_run(tmp_path):
    original = gen.FAMILIES[FAST_FAMILY].oracle

    def rejecting(sub, actual):
        if sub.index == 3:
            return False
        return original(sub, actual)

    monkeypatch = pytest.MonkeyPatch()
    _replace_driver(monkeypatch, FAST_FAMILY, oracle=rejecting)
    try:
        result = gen.run_family(FAST_FAMILY, 0, 8, identity=_identity())
    finally:
        monkeypatch.undo()
    assert result.ok is False
    assert result.failure['stage'] == 'oracle' and result.failure['index'] == 3
    assert result.failure['reason'] == 'oracle_failed'
    assert result.counts == {'planned': 8, 'generated': 4, 'attempted': 4,
                             'oracle_passed': 3, 'completed': 3}
    assert all(row[0] != gen.input_sha256(FAST_FAMILY, 3) for row in result.rows)


def test_main_reports_failure_and_preserves_prefix(tmp_path, capsys, monkeypatch):
    original = gen.FAMILIES[FAST_FAMILY].target

    def flaky(sub):
        if sub.index == 2:
            raise RuntimeError('boom-mid-round')
        return original(sub)

    _replace_driver(monkeypatch, FAST_FAMILY, target=flaky)
    payload = json.dumps(_payload(tmp_path, round=1, sub_seed=3)).encode('utf-8')
    monkeypatch.setattr(sys, 'stdin', _FakeStdin(payload))
    code = gen.main(['--family', FAST_FAMILY, '--rows', '6',
                     '--candidate', '{"rv05":"n1-test"}',
                     '--manifest-sha256', '11' * 32,
                     '--contract-sha256', '22' * 32])
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out['ok'] is False and out['failure']['reason'] == 'target_error'
    assert out['echo_round'] == 1 and out['echo_seed'] == 3
    doc = json.loads((tmp_path / 'subinputs-paper-decimal-fill.json').read_bytes())
    assert doc['counts']['completed'] == 2 and len(doc['rows']) == 2
    gen.check_receipt_doc(doc)


# ---------------------------------------------------------------------------
# (c) tampered actual results are rejected by the producer oracle
# ---------------------------------------------------------------------------

def test_tampered_capture_payload_rejected():
    honest = gen.target_call('capture-codec', 0)
    other = gen.target_call('capture-codec', 1)
    assert gen.producer_oracle('capture-codec', 0, other) is False
    payload = honest.value
    mutated = payload[:80] + ('x' if payload[80] != 'x' else 'y') + payload[81:]
    tampered = dataclasses.replace(honest, value=mutated)
    assert gen.producer_oracle('capture-codec', 0, tampered) is False
    assert gen.producer_oracle('capture-codec', 0, honest) is True


def test_tampered_paper_fill_rejected():
    honest = gen.target_call('paper-decimal-fill', 5)
    assert gen.producer_oracle('paper-decimal-fill', 5, honest) is True
    fill = honest.value
    conservation = dataclasses.replace(fill, unfilled_size=fill.unfilled_size + 1)
    assert gen.producer_oracle('paper-decimal-fill', 5,
                               dataclasses.replace(honest, value=conservation)) is False
    unquantized = dataclasses.replace(
        fill, average_price=(fill.average_price + Decimal('0.0007')))
    assert gen.producer_oracle('paper-decimal-fill', 5,
                               dataclasses.replace(honest, value=unquantized)) is False
    if fill.worst_price is not None:
        step = Decimal('0.001')
        beyond = dataclasses.replace(
            fill, average_price=fill.worst_price + step if fill.side == 'buy'
            else fill.worst_price - step)
        assert gen.producer_oracle(
            'paper-decimal-fill', 5,
            dataclasses.replace(honest, value=beyond)) is False


def test_tampered_authz_payload_rejected():
    honest = gen.target_call('uncapped-authz-codec', 2)
    assert gen.producer_oracle('uncapped-authz-codec', 2, honest) is True
    payload = honest.value
    mutated = payload[:60] + ('x' if payload[60] != 'x' else 'y') + payload[61:]
    tampered = dataclasses.replace(honest, value=mutated)
    assert gen.producer_oracle('uncapped-authz-codec', 2, tampered) is False


def test_run_family_tampered_target_fails_round():
    honest_driver = gen.FAMILIES['capture-codec']
    wrong = gen.target_call('capture-codec', 40)

    def lying(sub):
        if sub.index == 1:
            return wrong  # claims another input's payload as this input's result
        return honest_driver.target(sub)

    monkeypatch = pytest.MonkeyPatch()
    _replace_driver(monkeypatch, 'capture-codec', target=lying)
    try:
        result = gen.run_family('capture-codec', 0, 4, identity=_identity())
    finally:
        monkeypatch.undo()
    assert result.ok is False and result.failure['reason'] == 'oracle_failed'
    assert len(result.rows) == 1  # only the honest first row survives


# ---------------------------------------------------------------------------
# (d) duplicates fold; transport-only changes never create new identity
# ---------------------------------------------------------------------------

def test_same_input_yields_same_identity():
    for family in FAMILIES:
        first = gen.derive_input(family, 13)
        second = gen.derive_input(family, 13)
        assert first.input_sha256 == second.input_sha256
        assert gen.target_call(family, 13).result_sha256 == \
            gen.target_call(family, 13).result_sha256


def test_duplicate_input_folds_without_new_row(monkeypatch):
    original = gen.FAMILIES[FAST_FAMILY].derive
    canonical = {3: original(3), 5: original(3)}  # index 5 replays input 3

    def replaying(index):
        return canonical.get(index) or original(index)

    events = []
    _replace_driver(monkeypatch, FAST_FAMILY, derive=replaying)
    result = gen.run_family(FAST_FAMILY, 0, 8, identity=_identity(),
                            recorder=events.append)
    assert result.ok is True
    assert result.counts['planned'] == 8 and result.counts['completed'] == 7
    keys = [row[0] for row in result.rows]
    assert len(keys) == len(set(keys)) == 7  # folded: no duplicate key ever written
    _, doc = result.parts[0]
    gen.check_receipt_doc(doc)
    assert any(name == 'input_duplicate' for name, _ in events)


def test_transport_only_changes_preserve_identity_and_rows(tmp_path):
    first = gen.run_family(FAST_FAMILY, 0, 10, identity=_identity())
    second = gen.run_family(FAST_FAMILY, 0, 10, identity=_identity(
        round=999, segment=42, sub_seed=123456, scenario='ok-echo',
        candidate='{"other":"candidate"}', manifest_sha256='33' * 32))
    assert first.rows == second.rows  # identical logical inputs and results
    assert gen.serialize_receipt(first.parts[0][1]) != \
        gen.serialize_receipt(second.parts[0][1])  # transport echo differs in header


# ---------------------------------------------------------------------------
# (e) truncated or padded receipts are detectable
# ---------------------------------------------------------------------------

def test_truncated_receipt_detected(tmp_path):
    doc = _synthetic_doc([_hex_pair(i) for i in range(5)])
    path = tmp_path / 'subinputs-paper-decimal-fill.json'
    data = gen.serialize_receipt(doc)
    gen.write_receipt_file(path, doc)
    truncated = data[:len(data) - 40]
    path.write_bytes(truncated)
    with pytest.raises(gen.SoakGeneratorError, match='receipt_write_mismatch'):
        gen.verify_receipt_file(path, data)
    with pytest.raises((ValueError, gen.SoakGeneratorError)):
        json.loads(path.read_bytes())  # structurally broken as well


def test_padded_receipt_detected(tmp_path):
    doc = _synthetic_doc([_hex_pair(i) for i in range(3)])
    path = tmp_path / 'r.json'
    data = gen.serialize_receipt(doc)
    gen.write_receipt_file(path, doc)
    path.write_bytes(data + b'x')
    with pytest.raises(gen.SoakGeneratorError, match='receipt_write_mismatch'):
        gen.verify_receipt_file(path, data)


def test_writer_is_atomic_and_complete(tmp_path):
    doc = _synthetic_doc([_hex_pair(0)])
    path = tmp_path / 'subinputs-paper-decimal-fill.json'
    gen.write_receipt_file(path, doc)
    assert path.read_bytes() == gen.serialize_receipt(doc)
    assert path.read_bytes().endswith(b'\n')
    assert not list(tmp_path.glob('*.tmp'))  # no residue


# ---------------------------------------------------------------------------
# (f) recorder seam: order, identity of oracle input, gated-oracle barrier
# ---------------------------------------------------------------------------

ROW_EVENTS = ('input_constructed', 'target_entered', 'target_returned',
              'oracle_checked', 'row_appended')


def test_recorder_order_per_row():
    events = []
    result = gen.run_family(FAST_FAMILY, 0, 6, identity=_identity(),
                            recorder=events.append)
    assert result.ok
    for index in range(6):
        row = [(name, payload) for name, payload in events
               if payload.get('index') == index]
        assert [name for name, _ in row] == list(ROW_EVENTS)
        assert row[3][1]['verdict'] is True
        assert row[0][1]['input_sha256'] == row[4][1]['input_sha256']
    indices = [payload['index'] for name, payload in events
               if name == 'row_appended']
    assert indices == list(range(6))  # strictly serial, in index order


def test_oracle_consumes_the_exact_target_return_value():
    captured = {}
    mismatches = []
    driver = gen.FAMILIES[FAST_FAMILY]
    original_target, original_oracle = driver.target, driver.oracle

    def spy_target(sub):
        actual = original_target(sub)
        captured[sub.index] = actual
        return actual

    def spy_oracle(sub, actual):
        if actual is not captured.get(sub.index):
            mismatches.append(sub.index)
        return original_oracle(sub, actual)

    monkeypatch = pytest.MonkeyPatch()
    _replace_driver(monkeypatch, FAST_FAMILY, target=spy_target, oracle=spy_oracle)
    try:
        result = gen.run_family(FAST_FAMILY, 0, 4, identity=_identity())
    finally:
        monkeypatch.undo()
    assert result.ok and not mismatches and len(captured) == 4


def test_oracle_false_yields_no_row_appended():
    events = []
    original = gen.FAMILIES[FAST_FAMILY].oracle

    def rejecting(sub, actual):
        return False if sub.index == 2 else original(sub, actual)

    monkeypatch = pytest.MonkeyPatch()
    _replace_driver(monkeypatch, FAST_FAMILY, oracle=rejecting)
    try:
        result = gen.run_family(FAST_FAMILY, 0, 4, identity=_identity(),
                                recorder=events.append)
    finally:
        monkeypatch.undo()
    assert not result.ok
    names = [name for name, payload in events if payload.get('index') == 2]
    assert names == ['input_constructed', 'target_entered', 'target_returned',
                     'oracle_checked', 'family_failed']
    assert 'row_appended' not in [name for name, payload in events
                                  if payload.get('index') == 2]


def test_gated_oracle_blocks_row_append_until_released():
    gate = threading.Event()
    released = threading.Event()
    original = gen.FAMILIES[FAST_FAMILY].oracle

    def gated(sub, actual):
        if sub.index == 3:
            gate.wait(timeout=30)
            released.set()
        return original(sub, actual)

    monkeypatch = pytest.MonkeyPatch()
    _replace_driver(monkeypatch, FAST_FAMILY, oracle=gated)
    events = []
    outcome = {}

    def work():
        outcome['result'] = gen.run_family(FAST_FAMILY, 0, 5, identity=_identity(),
                                           recorder=events.append)

    try:
        worker = threading.Thread(target=work)
        worker.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(name == 'target_returned' and payload.get('index') == 3
                   for name, payload in events):
                break
            time.sleep(0.01)
        else:
            worker.join(timeout=1)
            pytest.fail('worker never reached the gated oracle')
        time.sleep(0.1)
        parked = [name for name, payload in events if payload.get('index') == 3]
        assert parked == ['input_constructed', 'target_entered', 'target_returned']
        assert not any(name == 'row_appended' and payload.get('index') == 3
                       for name, payload in events)
        gate.set()
        assert released.wait(timeout=30)
        worker.join(timeout=30)
    finally:
        gate.set()
        monkeypatch.undo()
    result = outcome['result']
    assert result.ok and len(result.rows) == 5
    names = [name for name, payload in events if payload.get('index') == 3]
    assert names == list(ROW_EVENTS)  # row appended only after the verdict


def test_recorder_none_matches_recorder_present_output_byte_for_byte():
    plain = gen.run_family(FAST_FAMILY, 0, 8, identity=_identity())
    observed = gen.run_family(FAST_FAMILY, 0, 8, identity=_identity(),
                              recorder=lambda event: None)
    assert gen.serialize_receipt(plain.parts[0][1]) == \
        gen.serialize_receipt(observed.parts[0][1])
    assert plain.counts == observed.counts and plain.ok == observed.ok


# ---------------------------------------------------------------------------
# (g) byte budgets, measured (never estimated)
# ---------------------------------------------------------------------------

def test_row_encoding_is_136_bytes_measured():
    pair = json.dumps(['a' * 64, 'b' * 64], separators=(',', ':'))
    assert len(pair) + 1 == 136  # element plus list comma, real bytes
    one = gen.serialize_receipt(_synthetic_doc([_hex_pair(0)]))
    two = gen.serialize_receipt(_synthetic_doc([_hex_pair(0), _hex_pair(1)]))
    assert len(two) - len(one) == 136  # measured row cost in a real receipt


def test_real_2048_row_block_within_budget(tmp_path):
    result = gen.run_family(FAST_FAMILY, 0, 2048, identity=_identity())
    assert result.ok and result.counts['completed'] == 2048
    assert len(result.parts) == 1
    data = gen.serialize_receipt(result.parts[0][1])
    assert len(data) <= 524288, f'measured {len(data)} bytes'
    written = gen.write_receipt_file(tmp_path / result.parts[0][0],
                                     result.parts[0][1])
    assert written.bytes_len == len(data)
    header = len(gen.canonical_json({**result.parts[0][1], 'rows': []}))
    assert header <= 2048, f'measured header {header} bytes'


def test_part_split_at_3840_and_4096(tmp_path):
    for planned, part_rows, part_count in ((3839, [3839], 1), (3840, [3839, 1], 2),
                                           (4096, [3839, 257], 2)):
        result = gen.run_family(FAST_FAMILY, 0, planned, identity=_identity())
        assert result.ok, planned
        assert [len(doc['rows']) for _, doc in result.parts] == part_rows
        assert [name for name, _ in result.parts] == (
            ['subinputs-paper-decimal-fill.json'] if part_count == 1 else
            ['subinputs-paper-decimal-fill.json',
             'subinputs-paper-decimal-fill.p02.json'])
        for number, (name, doc) in enumerate(result.parts, start=1):
            gen.check_receipt_doc(doc)
            data = gen.serialize_receipt(doc)
            assert len(data) <= 524288, f'{planned}/{name}: {len(data)} bytes'
            gen.write_receipt_file(tmp_path / f'{planned}-{name}', doc)
            if number >= 2:
                assert doc['part'] == number and doc['part_count'] == part_count
                assert doc['index_origin'] == 3839  # first row of part two
            else:
                assert 'part' not in doc
        total = sum(len(doc['rows']) for _, doc in result.parts)
        assert total == planned


def test_planned_rows_above_absolute_cap_rejected():
    with pytest.raises(gen.SoakGeneratorError, match='config_invalid'):
        gen.run_family(FAST_FAMILY, 0, 4097, identity=_identity())


def test_over_limit_receipt_refused_before_write(tmp_path):
    rows = [_hex_pair(i) for i in range(4000)]
    doc = _synthetic_doc(rows)
    path = tmp_path / 'oversize.json'
    with pytest.raises(gen.SoakGeneratorError, match='receipt_over_limit') as caught:
        gen.write_receipt_file(path, doc)
    measured = len(gen.serialize_receipt(doc))
    assert measured > 524288
    assert str(measured) in caught.value.detail
    assert not path.exists()  # nothing truncated, nothing written


def test_header_over_limit_receipt_refused(tmp_path):
    rows = [_hex_pair(0)]
    doc = _synthetic_doc(rows, candidate='{"pad":"' + 'x' * 3000 + '"}')
    measured = len(gen.canonical_json({**doc, 'rows': []}))
    assert measured > 2048
    with pytest.raises(gen.SoakGeneratorError, match='receipt_header_over_limit'):
        gen.write_receipt_file(tmp_path / 'wide.json', doc)


# ---------------------------------------------------------------------------
# (h) closed field set and schema handling
# ---------------------------------------------------------------------------

def _base_doc():
    return _synthetic_doc([_hex_pair(0), _hex_pair(1)])


def test_unknown_top_level_field_rejected():
    doc = _base_doc()
    doc['smuggled'] = True
    with pytest.raises(gen.SoakGeneratorError, match='receipt_unknown_field'):
        gen.check_receipt_doc(doc)


def test_missing_top_level_field_rejected():
    doc = _base_doc()
    del doc['entry']
    with pytest.raises(gen.SoakGeneratorError, match='receipt_invalid'):
        gen.check_receipt_doc(doc)


def test_reserved_audit_field_rejected_wherever_it_appears():
    doc = _base_doc()
    doc['counts']['distinct_qualified'] = 1
    with pytest.raises(gen.SoakGeneratorError, match='producer_claim_reserved_field'):
        gen.check_receipt_doc(doc)
    doc = _base_doc()
    doc['distinct_qualified'] = 1
    with pytest.raises(gen.SoakGeneratorError, match='producer_claim_reserved_field'):
        gen.check_receipt_doc(doc)


def test_wrong_or_missing_schema_rejected():
    doc = _base_doc()
    doc['schema'] = 'pal-soak-subinput-receipt-v2'
    with pytest.raises(gen.SoakGeneratorError, match='receipt_schema_rejected'):
        gen.check_receipt_doc(doc)
    doc = _base_doc()
    del doc['schema']
    with pytest.raises(gen.SoakGeneratorError, match='receipt_schema_rejected'):
        gen.check_receipt_doc(doc)


def test_unknown_family_and_entry_rejected():
    with pytest.raises(gen.SoakGeneratorError, match='whitelist_unknown'):
        gen.run_family('not-a-family', 0, 4, identity=_identity())
    with pytest.raises(gen.SoakGeneratorError, match='whitelist_unknown'):
        gen.derive_input('capture-codec2', 0)
    doc = _base_doc()
    doc['entry'] = 'capture-codec/other-verb'
    with pytest.raises(gen.SoakGeneratorError, match='whitelist_unknown'):
        gen.check_receipt_doc(doc)


def test_counts_invariants_enforced():
    doc = _base_doc()
    doc['counts']['completed'] = 3  # != len(rows)
    with pytest.raises(gen.SoakGeneratorError, match='counts_inconsistent'):
        gen.check_receipt_doc(doc)
    doc = _base_doc()
    doc['counts']['attempted'] = 99  # attempted > generated
    with pytest.raises(gen.SoakGeneratorError, match='counts_inconsistent'):
        gen.check_receipt_doc(doc)
    doc = _base_doc()
    doc['rows'][1] = list(doc['rows'][0])  # duplicate key inside one file
    with pytest.raises(gen.SoakGeneratorError, match='receipt_invalid'):
        gen.check_receipt_doc(doc)
    doc = _base_doc()
    doc['rows'][0][0] = doc['rows'][0][0].upper()  # not lowercase hex64
    with pytest.raises(gen.SoakGeneratorError, match='receipt_invalid'):
        gen.check_receipt_doc(doc)


# ---------------------------------------------------------------------------
# main() / child interface
# ---------------------------------------------------------------------------

def test_main_requires_identity_arguments(tmp_path, capsys, monkeypatch):
    payload = json.dumps(_payload(tmp_path)).encode('utf-8')
    monkeypatch.setattr(sys, 'stdin', _FakeStdin(payload))
    code = gen.main(['--rows', '4'])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out['ok'] is False and out['error'] == 'config_invalid'
    assert 'candidate' in out['detail']


def _spawn_child(tmp_path, extra_args, payload):
    repo_root = Path(gen.__file__).resolve().parents[2]
    environment = {'PYTHONPATH': str(repo_root), 'PYTHONUTF8': '1',
                   'PYTHONDONTWRITEBYTECODE': '1'}
    if os.name == 'nt':
        environment['SystemRoot'] = os.environ.get('SystemRoot', 'C:\\Windows')
    argv = [sys.executable, '-S', '-m', 'tests.support.soak_okcompute', *extra_args]
    return subprocess.run(argv, input=json.dumps(payload).encode('utf-8'),
                          capture_output=True, cwd=str(tmp_path), env=environment,
                          timeout=300)


def test_real_subprocess_single_family(tmp_path):
    payload = _payload(tmp_path, round=1, sub_seed=7)
    done = _spawn_child(tmp_path, ['--family', FAST_FAMILY, '--rows', '24',
                                   '--candidate', '{"rv05":"child"}',
                                   '--manifest-sha256', 'aa' * 32,
                                   '--contract-sha256', 'bb' * 32], payload)
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout.decode('utf-8'))
    assert summary['echo_round'] == 1 and summary['echo_seed'] == 7
    assert summary['ok'] is True
    (entry,) = summary['subinput_families']
    assert entry['family'] == FAST_FAMILY and entry['rows'] == 24
    (file_info,) = entry['files']
    data = (tmp_path / file_info['file']).read_bytes()
    assert hashlib.sha256(data).hexdigest() == file_info['sha256']
    doc = json.loads(data)
    assert doc['index_origin'] == 0 and doc['counts']['completed'] == 24
    gen.check_receipt_doc(doc)
    # rows are re-derivable from indexes alone (independent of the child)
    for offset, row in enumerate(doc['rows']):
        sub = gen.derive_input(FAST_FAMILY, offset)
        actual = gen.target_call(FAST_FAMILY, offset)
        assert row == [sub.input_sha256, actual.result_sha256]


def test_real_subprocess_two_families(tmp_path):
    payload = _payload(tmp_path, round=2, sub_seed=8)
    done = _spawn_child(tmp_path, ['--family', FAST_FAMILY,
                                   '--family', 'uncapped-authz-codec', '--rows', '10',
                                   '--candidate', '{"rv05":"child"}',
                                   '--manifest-sha256', 'aa' * 32,
                                   '--contract-sha256', 'bb' * 32], payload)
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout.decode('utf-8'))
    assert summary['ok'] is True
    assert {entry['family']: entry['rows'] for entry in summary['subinput_families']} \
        == {FAST_FAMILY: 5, 'uncapped-authz-codec': 5}
    assert sorted(path.name for path in tmp_path.glob('subinputs-*.json')) == [
        'subinputs-paper-decimal-fill.json', 'subinputs-uncapped-authz-codec.json']
    for name in ('subinputs-paper-decimal-fill.json',
                 'subinputs-uncapped-authz-codec.json'):
        gen.check_receipt_doc(json.loads((tmp_path / name).read_bytes()))
