"""N2 counterexample tests for the ``pal-soak-subinput-receipt-v1`` audit.

Every family derivation below is written AGAIN, directly from the receipt
contract text, as this file's own implementation — the audit module's
verifiers are never used to build the legal controls, so a divergence
between the contract text and the audit's derivation fails these tests
instead of cancelling itself out.

The legal control reuses the real driver campaign from ``test_soak_audit``
(same session-scoped fixture): forged copies get valid receipts injected
into genuinely passed rounds and must PASS, and each counterexample mutates
exactly one dimension and must be caught. No credentials, databases,
network, real model/market/order access; the only entries called are the
project's own deterministic pure functions.
"""
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.support import soak_audit as aud
from tests.support import soak_audit_subinputs as sub
from tests.support import soak_driver as drv
from tests.test_soak_audit import (audit, control, failed_names, forge,
                                   round_path, status_of, write_json)

AUDIT_CLI = Path(aud.__file__).resolve()

ENTRY = {'capture-codec': 'capture-codec/encode',
         'paper-decimal-fill': 'paper-decimal-fill/simulate-fill',
         'uncapped-authz-codec': 'uncapped-authz-codec/payload'}
RULE = {'capture-codec': 'norm:capture-codec-v1',
        'paper-decimal-fill': 'norm:paper-decimal-fill-v1',
        'uncapped-authz-codec': 'norm:uncapped-authz-v1'}
BASE = datetime(2026, 1, 1, tzinfo=UTC)
ZONES = ['America/New_York', 'Australia/Lord_Howe', 'Europe/Berlin', 'Asia/Tokyo']
STATUSES = ['completed', 'failed', 'intake_blocked']
QUANTUM = Decimal('0.001')


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _h(data: bytes) -> str:
    return sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Independent per-family derivations (this file's own contract reading)
# --------------------------------------------------------------------------

def _capture_row(index):
    instant = BASE + timedelta(minutes=index)
    zone_key = ZONES[index % 4]
    status = STATUSES[(index // 4) % 3]
    descriptor = {'family': 'capture-codec', 'zone': zone_key,
                  'instant_utc': instant.isoformat(), 'status': status}
    input_sha = _h(_canon(descriptor))
    from tests.test_research_capture_codec import make_run
    from polymarket_alpha_lab.research_capture_codec import \
        encode_research_capture
    run = make_run(now=instant.astimezone(ZoneInfo(zone_key)), status=status)
    payload = encode_research_capture(record_id=f'si-{index:012d}',
                                      model_id='synthetic',
                                      protocol_version='codec-v1', run=run)
    return [input_sha, _h(payload.encode('utf-8'))]


def _paper_book(index):
    side = 'buy' if index % 2 == 0 else 'sell'
    token_id = f'tok-{index % 7}'
    size = Decimal(index % 400 + 1) / Decimal(8)
    p0 = Decimal('100') + Decimal(index % 50)
    asks = [(p0 + Decimal('0.25') * (j + 1), Decimal((index + j * 13) % 37 + 1))
            for j in range(5)]
    bids = [(p0 - Decimal('0.25') * (j + 1), Decimal((index + j * 13) % 37 + 1))
            for j in range(5)]
    captured_at = BASE + timedelta(seconds=index)
    walked = asks if side == 'buy' else bids
    descriptor = {'family': 'paper-decimal-fill', 'token_id': token_id,
                  'side': side, 'size': str(size),
                  'levels': [[str(price), str(qty)] for price, qty in walked],
                  'captured_at': captured_at.isoformat()}
    return {'side': side, 'token_id': token_id, 'size': size, 'asks': asks,
            'bids': bids, 'captured_at': captured_at, 'walked': walked,
            'input_sha256': _h(_canon(descriptor))}


def _paper_row(index):
    book = _paper_book(index)
    side, size, walked = book['side'], book['size'], book['walked']
    filled = Decimal(0)
    notional = Decimal(0)
    worst = None
    for price, qty in walked:
        remaining = size - filled
        if remaining <= 0:
            break
        take = remaining if remaining <= qty else qty
        filled += take
        notional += take * price
        worst = price
    best_bid, best_ask = book['bids'][0][0], book['asks'][0][0]
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        spread = (best_ask - best_bid).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
        midpoint = ((best_bid + best_ask) / Decimal(2)).quantize(
            QUANTUM, rounding=ROUND_HALF_EVEN)
        if filled == 0:
            average = slippage = None
        else:
            exact_average = notional / filled
            average = exact_average.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
            if side == 'buy':
                slippage = max(Decimal(0), exact_average - best_ask).quantize(
                    QUANTUM, rounding=ROUND_HALF_EVEN)
            else:
                slippage = max(Decimal(0), best_bid - exact_average).quantize(
                    QUANTUM, rounding=ROUND_HALF_EVEN)

    def dec(value):
        return None if value is None else format(value, 'f')

    def hash_form(value):
        if value.is_zero():
            return '0'
        with localcontext(Context(prec=60, rounding=ROUND_HALF_EVEN)):
            return format(value.normalize(), 'f')

    snapshot_payload = {
        'token_id': book['token_id'], 'captured_at': book['captured_at'].isoformat(),
        'bids': [{'price': hash_form(p), 'size': hash_form(q)} for p, q in book['bids']],
        'asks': [{'price': hash_form(p), 'size': hash_form(q)} for p, q in book['asks']]}
    dump = {'token_id': book['token_id'], 'side': side,
            'requested_size': dec(size),
            'order_book_captured_at': book['captured_at'].isoformat(),
            'order_book_snapshot_sha256': _h(_canon(snapshot_payload)),
            'filled_size': dec(filled), 'unfilled_size': dec(size - filled),
            'average_price': dec(average), 'worst_price': dec(worst),
            'best_bid': dec(best_bid), 'best_ask': dec(best_ask),
            'midpoint': dec(midpoint), 'spread': dec(spread),
            'slippage_estimate': dec(slippage)}
    return [book['input_sha256'], _h(_canon(dump))]


def _authz_row(index):
    request_keys = [(f'rec-{index * 3 + k}',
                     f'{((index * 3 + k) ** 31) % 2 ** 256:064x}') for k in range(3)]
    approved_at = BASE + timedelta(minutes=index)
    expires_at = approved_at + timedelta(hours=1 + (index % 48))
    adapter = sha256(b'rv05-authz:%d' % index).hexdigest()
    descriptor = {'family': 'uncapped-authz-codec', 'model_id': 'synthetic',
                  'request_keys': [list(key) for key in request_keys],
                  'approved_at': approved_at.isoformat(),
                  'expires_at': expires_at.isoformat(),
                  'adapter_contract_sha256': adapter}
    from polymarket_alpha_lab.research_uncapped import (
        UncappedResearchAuthorization, copy_authorization,
    )
    authorization = copy_authorization(UncappedResearchAuthorization(
        authorization_id=f'authz-{index:012d}', model_id='synthetic',
        adapter_contract_sha256=adapter, approved_at=approved_at,
        expires_at=expires_at, request_keys=tuple(request_keys),
        no_monetary_cap_approved=True, research_data_send_approved=True))
    return [_h(_canon(descriptor)), _h(authorization.payload.encode('utf-8'))]


_ROW_CACHE: dict[tuple, list] = {}


def row_for(family, index):
    key = (family, index)
    if key not in _ROW_CACHE:
        builder = {'capture-codec': _capture_row,
                   'paper-decimal-fill': _paper_row,
                   'uncapped-authz-codec': _authz_row}[family]
        _ROW_CACHE[key] = builder(index)
    return list(_ROW_CACHE[key])


# --------------------------------------------------------------------------
# Receipt construction helpers
# --------------------------------------------------------------------------

def manifest_sha_of(control) -> str:
    return drv._read_json(control['campaign'] / 'campaign.json')['manifest_sha256']


# Integration tree (N0) carries the repository contract copy; when present,
# the audit enforces receipt contract_sha256 provenance against it, so the
# synthetic legal control must carry the real value. N2's standalone branch
# has no copy and the check is skipped there ('b' * 64 inert).
_CONTRACT_COPY = Path(drv.__file__).resolve().parents[2] / 'docs' / 'contracts' \
    / 'soak-subinput-receipt-v1.md'
_CONTRACT_SHA = (sha256(_CONTRACT_COPY.read_bytes()).hexdigest()
                 if _CONTRACT_COPY.is_file() else 'b' * 64)


def receipt_doc(control, record, family, indices, *, index_origin=None,
                entry=None, normalize_rule=None, counts=None, rows=None,
                updates=None):
    rows = [list(row_for(family, i)) for i in indices] if rows is None else rows
    doc = {
        'schema': 'pal-soak-subinput-receipt-v1',
        'family': family,
        'entry': entry or ENTRY[family],
        'normalize_rule': normalize_rule or RULE[family],
        'candidate': json.dumps(control['config']['candidate'], sort_keys=True,
                                separators=(',', ':')),
        'manifest_sha256': manifest_sha_of(control),
        'generator_sha256': 'a' * 64,
        'contract_sha256': _CONTRACT_SHA,
        'round': record['round'], 'segment': record['segment'],
        'sub_seed': record['sub_seed'], 'scenario': record['scenario'],
        'index_origin': index_origin if index_origin is not None
                        else (indices[0] if indices else 0),
        'oracle': f'producer:{family}-v1',
        'counts': counts or {'planned': len(rows), 'generated': len(rows),
                             'attempted': len(rows),
                             'oracle_passed': len(rows), 'completed': len(rows)},
        'rows': rows,
    }
    if updates:
        doc.update(updates)
    return doc


def inject_receipt(control, campaign, round_no, family, indices, *, doc=None,
                   pointer=True, filename=None, write=True):
    round_dir = round_path(campaign, round_no)
    record = drv._read_json(round_dir / 'round.json')
    doc = doc or receipt_doc(control, record, family, indices)
    name = filename or f'subinputs-{family}.json'
    target = round_dir / name
    if write:
        target.write_bytes(_canon(doc) + b'\n')
    if pointer:
        pointers = record.get('subinput_receipts') or []
        pointers.append({'family': doc['family'], 'entry': doc['entry'],
                         'file': name, 'rows': len(doc['rows']),
                         'sha256': _h(target.read_bytes())})
        record['subinput_receipts'] = pointers
        write_json(round_dir / 'round.json', record)
    return target, doc


def reasons_of(report):
    return [entry.get('reason') for entry in report['subinputs']['rejected_files']]


def rewrite_receipt(campaign, round_no, target, doc):
    """Rewrite a receipt file and refresh its round.json pointer hash so the
    pointer layer stays consistent with the mutation under test."""
    target.write_bytes(_canon(doc) + b'\n')
    record_path = round_path(campaign, round_no) / 'round.json'
    record = drv._read_json(record_path)
    for pointer in record.get('subinput_receipts', []):
        if pointer.get('file') == target.name:
            pointer['sha256'] = _h(target.read_bytes())
            pointer['rows'] = len(doc['rows'])
    write_json(record_path, record)


# --------------------------------------------------------------------------
# independent-walk agreement with the real entry (derivation sanity)
# --------------------------------------------------------------------------

def test_independent_paper_walk_agrees_with_the_real_entry():
    from polymarket_alpha_lab.domain import OrderBookLevel, OrderBookSnapshot
    from polymarket_alpha_lab.paper import PaperOrder, simulate_order_book_fill

    def dec(value):
        return None if value is None else format(value, 'f')

    for index in (0, 1, 2, 9, 40, 61, 123, 500, 999, 2401):
        book = _paper_book(index)
        row = _paper_row(index)
        snapshot = OrderBookSnapshot(
            token_id=book['token_id'],
            bids=tuple(OrderBookLevel(p, q) for p, q in book['bids']),
            asks=tuple(OrderBookLevel(p, q) for p, q in book['asks']),
            captured_at=book['captured_at'])
        fill = simulate_order_book_fill(
            PaperOrder(book['token_id'], book['side'], book['size']), snapshot)
        actual = {'token_id': fill.token_id, 'side': fill.side,
                  'requested_size': dec(fill.requested_size),
                  'order_book_captured_at':
                      fill.order_book_captured_at.astimezone(UTC).isoformat(),
                  'order_book_snapshot_sha256': fill.order_book_snapshot_sha256,
                  'filled_size': dec(fill.filled_size),
                  'unfilled_size': dec(fill.unfilled_size),
                  'average_price': dec(fill.average_price),
                  'worst_price': dec(fill.worst_price),
                  'best_bid': dec(fill.best_bid), 'best_ask': dec(fill.best_ask),
                  'midpoint': dec(fill.midpoint), 'spread': dec(fill.spread),
                  'slippage_estimate': dec(fill.slippage_estimate)}
        assert _h(_canon(actual)) == row[1], index


def test_audit_verifiers_accept_honest_rows_and_reject_tampered_ones():
    for family, index in (('capture-codec', 7), ('paper-decimal-fill', 12),
                          ('uncapped-authz-codec', 3)):
        row = row_for(family, index)
        assert sub.verify_row((family, ENTRY[family]), index,
                              row[0], row[1])['ok'] is True
        flipped = list(row)
        flipped[1] = '0' * 64
        verdict = sub.verify_row((family, ENTRY[family]), index,
                                 flipped[0], flipped[1])
        assert verdict['ok'] is False and verdict['reason'] == 'result_mismatch'
        swapped = [row[1], row[0]]
        verdict = sub.verify_row((family, ENTRY[family]), index,
                                 swapped[0], swapped[1])
        assert verdict['ok'] is False and verdict['reason'] == 'input_mismatch'


def test_paper_verifier_does_not_call_the_audited_entry(monkeypatch):
    import polymarket_alpha_lab.paper as paper_module

    def forbidden(*args, **kwargs):
        raise AssertionError('audit-side F2 must not call simulate_order_book_fill')

    monkeypatch.setattr(paper_module, 'simulate_order_book_fill', forbidden)
    row = row_for('paper-decimal-fill', 21)
    verdict = sub.verify_row(('paper-decimal-fill',
                              ENTRY['paper-decimal-fill']), 21, row[0], row[1])
    assert verdict['ok'] is True
    assert verdict['detail']['verified_via'] == 'independent_decimal_walk'


def test_registry_covers_exactly_the_three_contract_families():
    assert set(sub.REGISTRY) == {
        ('capture-codec', 'capture-codec/encode'),
        ('paper-decimal-fill', 'paper-decimal-fill/simulate-fill'),
        ('uncapped-authz-codec', 'uncapped-authz-codec/payload')}
    for rule in sub.REGISTRY.values():
        assert rule['normalize_rule'].startswith('norm:')


def test_derivation_is_a_pure_function_of_the_index():
    for family in ('capture-codec', 'paper-decimal-fill', 'uncapped-authz-codec'):
        assert row_for(family, 33) == row_for(family, 33)
        assert row_for(family, 33)[0] != row_for(family, 34)[0]


# --------------------------------------------------------------------------
# legal control with receipts
# --------------------------------------------------------------------------

def test_legal_control_with_receipts_passes_all_subinput_gates(control, tmp_path):
    copy = forge(control, tmp_path)
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5))
    inject_receipt(control, copy, 2, 'capture-codec', range(0, 3))
    inject_receipt(control, copy, 3, 'uncapped-authz-codec', range(0, 3))
    report = audit(copy, config=control['config'],
                   min_distinct_qualified_subinputs=11)
    assert report['overall'] == aud.PASS, report['failures']
    assert failed_names(report) == []
    block = report['subinputs']
    assert block['qualified_segment'] == 'segment-000001'
    total = block['total']
    assert total['receipt_files'] == 3
    assert total['rows_read'] == 11
    assert total['rows_whitelist_verified'] == 11
    assert total['rows_rejected'] == 0
    # six-count cross-check values (first five are recomputed echoes)
    assert total['planned_sum'] == total['generated_sum'] \
        == total['attempted_sum'] == total['oracle_passed_sum'] \
        == total['completed_sum'] == 11
    assert total['declared_vs_rows_consistent'] == 3
    assert total['distinct_qualified'] == 11
    assert total['dedup_collapsed'] == 0 and total['domain_overlaps'] == 0
    assert {name: acc['distinct_qualified']
            for name, acc in block['families'].items()} == {
        'capture-codec': 3, 'paper-decimal-fill': 5, 'uncapped-authz-codec': 3}
    assert status_of(report, 'min_distinct_qualified_subinputs') == aud.PASS
    assert status_of(report, 'subinput_receipts') == aud.PASS
    assert status_of(report, 'subinput_row_recompute') == aud.PASS
    # legacy metrics untouched next to the new counters
    assert report['inputs']['verified_distinct_inputs'] == 6
    assert report['inputs']['receipt_declared_subinputs_sum'] == 0
    assert block['legacy_verified_round_inputs'] == 6
    # one row short of the gate is a FAIL
    strict = audit(copy, config=control['config'],
                   min_distinct_qualified_subinputs=12)
    assert status_of(strict, 'min_distinct_qualified_subinputs') == aud.FAIL


def test_old_campaign_without_receipts_keeps_legacy_semantics(control):
    report = audit(control['campaign'], config=control['config'],
                   min_distinct_inputs=6, min_distinct_qualified_subinputs=100000)
    total = report['subinputs']['total']
    assert total['receipt_files'] == 0 and total['rows_read'] == 0
    assert total['distinct_qualified'] == 0
    # the two gates judge independent quantities and never upgrade each other
    assert status_of(report, 'min_distinct_inputs') == aud.PASS
    gate = status_of(report, 'min_distinct_qualified_subinputs')
    assert gate == aud.FAIL
    assert report['inputs']['verified_distinct_inputs'] == 6
    assert report['subinputs']['legacy_verified_round_inputs'] == 6
    plain = audit(control['campaign'], config=control['config'])
    assert status_of(plain, 'min_distinct_qualified_subinputs') == aud.UNKNOWN


def test_legacy_inputs_block_is_unchanged_by_receipt_injection(control, tmp_path):
    before = audit(control['campaign'], config=control['config'])
    copy = forge(control, tmp_path)
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5))
    after = audit(copy, config=control['config'])
    assert before['inputs'] == after['inputs']
    assert after['subinputs']['total']['distinct_qualified'] == 5


# --------------------------------------------------------------------------
# tampering with actual results / input identity
# --------------------------------------------------------------------------

def _single_file_campaign(control, tmp_path, family='paper-decimal-fill',
                           count=5):
    copy = forge(control, tmp_path)
    target, doc = inject_receipt(control, copy, 1, family, range(0, count))
    return copy, target, doc


def test_tampered_actual_result_is_rejected_row_by_row(control, tmp_path):
    copy, target, doc = _single_file_campaign(control, tmp_path)
    doc['rows'][2][1] = 'e' * 64  # a real output hash, wrong value
    rewrite_receipt(copy, 1, target, doc)
    report = audit(copy, config=control['config'],
                   min_distinct_qualified_subinputs=5)
    names = failed_names(report)
    assert 'subinput_row_recompute' in names and 'subinput_receipts' not in names
    total = report['subinputs']['total']
    assert total['rows_read'] == 5
    assert total['rows_whitelist_verified'] == 4  # row-level isolation
    assert total['rows_rejected'] == 1
    assert total['distinct_qualified'] == 4
    assert report['subinputs']['row_rejections'][0]['reason'] == 'result_mismatch'


def test_tampered_input_identity_is_rejected(control, tmp_path):
    copy, target, doc = _single_file_campaign(control, tmp_path)
    doc['rows'][1][0] = 'f' * 64
    rewrite_receipt(copy, 1, target, doc)
    report = audit(copy, config=control['config'])
    assert 'subinput_row_recompute' in failed_names(report)
    assert report['subinputs']['total']['rows_rejected'] == 1
    assert report['subinputs']['row_rejections'][0]['reason'] == 'input_mismatch'


def test_swapped_rows_are_both_rejected_not_the_whole_file(control, tmp_path):
    copy, target, doc = _single_file_campaign(control, tmp_path)
    doc['rows'][0], doc['rows'][4] = doc['rows'][4], doc['rows'][0]
    rewrite_receipt(copy, 1, target, doc)
    report = audit(copy, config=control['config'])
    assert 'subinput_receipts' not in failed_names(report)
    assert report['subinputs']['total']['rows_rejected'] == 2
    assert report['subinputs']['total']['rows_whitelist_verified'] == 3


def test_capture_and_authz_tampering_detected(control, tmp_path):
    for family, index_range in (('capture-codec', range(0, 3)),
                                ('uncapped-authz-codec', range(0, 3))):
        copy = forge(control, tmp_path, name=f'tamper-{family}')
        target, doc = inject_receipt(control, copy, 1, family, index_range)
        doc['rows'][0][1] = 'd' * 64
        rewrite_receipt(copy, 1, target, doc)
        report = audit(copy, config=control['config'])
        assert 'subinput_row_recompute' in failed_names(report), family
        rejection = report['subinputs']['row_rejections'][0]
        assert rejection['reason'] == 'result_mismatch', family


# --------------------------------------------------------------------------
# duplicates: in-file vs cross-file
# --------------------------------------------------------------------------

def test_in_file_duplicate_key_rejects_the_whole_file(control, tmp_path):
    copy, target, doc = _single_file_campaign(control, tmp_path)
    doc['rows'][3][0] = doc['rows'][0][0]  # same input identity twice
    rewrite_receipt(copy, 1, target, doc)
    report = audit(copy, config=control['config'])
    assert 'subinput_receipts' in failed_names(report)
    assert 'receipt_duplicate_key' in reasons_of(report)
    total = report['subinputs']['total']
    assert total['rows_read'] == 0 and total['distinct_qualified'] == 0


def test_cross_file_replay_collapses_with_domain_overlap(control, tmp_path):
    copy = forge(control, tmp_path)
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5))
    # round 2 replays the same index block: only round/segment/sub_seed and
    # the file differ — transport-only variation must NOT create new identity
    inject_receipt(control, copy, 2, 'paper-decimal-fill', range(0, 5))
    report = audit(copy, config=control['config'],
                   min_distinct_qualified_subinputs=5)
    assert report['overall'] == aud.PASS, report['failures']
    total = report['subinputs']['total']
    assert total['receipt_files'] == 2 and total['rows_read'] == 10
    assert total['rows_whitelist_verified'] == 10
    assert total['distinct_qualified'] == 5
    assert total['dedup_collapsed'] == 5
    assert total['domain_overlaps'] == 5
    assert report['subinputs']['domain_overlap_events'][0]['entry'] == \
        ENTRY['paper-decimal-fill']


# --------------------------------------------------------------------------
# omission / truncation / write-failure shapes
# --------------------------------------------------------------------------

def test_truncated_receipt_is_unparseable(control, tmp_path):
    copy, target, doc = _single_file_campaign(control, tmp_path)
    raw = target.read_bytes()
    target.write_bytes(raw[:len(raw) - 120])  # torn tail
    report = audit(copy, config=control['config'])
    assert 'subinput_receipts' in failed_names(report)
    assert 'receipt_unparseable' in reasons_of(report)


def test_pointer_to_promoted_but_missing_file_fails(control, tmp_path):
    copy, target, _doc = _single_file_campaign(control, tmp_path)
    target.unlink()  # promotion failed after the pointer was written
    report = audit(copy, config=control['config'])
    names = failed_names(report)
    # the missing file itself is undiscoverable; the dangling pointer is the
    # visible failure surface
    assert 'subinput_pointer_consistency' in names
    gate = [entry for entry in report['checks']
            if entry['check'] == 'subinput_pointer_consistency'][0]
    assert gate['detail']['problems'][0]['missing_files'] == [target.name]
    assert report['subinputs']['total']['distinct_qualified'] == 0


def test_unpointed_receipt_file_in_passed_round_fails(control, tmp_path):
    copy = forge(control, tmp_path)
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5),
                   pointer=False)
    report = audit(copy, config=control['config'])
    gate = [entry for entry in report['checks']
            if entry['check'] == 'subinput_pointer_consistency']
    assert gate and gate[0]['status'] == aud.FAIL
    assert gate[0]['detail']['problems'][0]['unpointed_files']


def test_interrupted_round_rows_never_qualify(control, tmp_path):
    copy, target, _doc = _single_file_campaign(control, tmp_path)
    record_path = round_path(copy, 1) / 'round.json'
    record = drv._read_json(record_path)
    record['final'] = 'interrupted'
    record['reason'] = 'driver_stopped'
    write_json(record_path, record)
    report = audit(copy, config=control['config'],
                   min_distinct_qualified_subinputs=5)
    total = report['subinputs']['total']
    assert total['rows_read'] == 5
    assert total['rows_whitelist_verified'] == 5  # still evidence-verified...
    assert total['distinct_qualified'] == 0        # ...but never qualified
    assert status_of(report, 'min_distinct_qualified_subinputs') == aud.FAIL


# --------------------------------------------------------------------------
# wrong transport / provenance / whitelist echo
# --------------------------------------------------------------------------

def test_wrong_sub_seed_echo_is_rejected(control, tmp_path):
    copy = forge(control, tmp_path)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5))
    doc['sub_seed'] = doc['sub_seed'] + 1  # wrong seed binding
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    report = audit(copy, config=control['config'])
    assert 'subinput_receipts' in failed_names(report)
    assert 'receipt_echo_transport' in reasons_of(report)


def test_wrong_candidate_echo_is_rejected(control, tmp_path):
    copy = forge(control, tmp_path)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5))
    doc['candidate'] = '{"label":"somebody-else"}'
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    report = audit(copy, config=control['config'])
    assert 'receipt_candidate_mismatch' in reasons_of(report)


def test_wrong_manifest_sha_is_rejected(control, tmp_path):
    copy = forge(control, tmp_path)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5))
    doc['manifest_sha256'] = 'c' * 64
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    report = audit(copy, config=control['config'])
    assert 'receipt_manifest_mismatch' in reasons_of(report)


def test_unknown_family_and_entry_are_rejected(control, tmp_path):
    copy = forge(control, tmp_path)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5),
                      entry='paper-decimal-fill/unknown-verb')
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    report = audit(copy, config=control['config'])
    assert 'whitelist_unknown' in reasons_of(report)

    other = forge(control, tmp_path, name='family')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5),
                      updates={'family': 'mystery-family',
                               'entry': 'mystery-family/run',
                               'normalize_rule': 'norm:mystery-v1'})
    inject_receipt(control, other, 1, 'paper-decimal-fill', range(0, 5), doc=doc,
                   filename='subinputs-mystery-family.json')
    report = audit(other, config=control['config'])
    assert 'whitelist_unknown' in reasons_of(report)


def test_wrong_normalize_rule_is_rejected(control, tmp_path):
    copy = forge(control, tmp_path)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5),
                      normalize_rule='norm:paper-decimal-fill-v0')
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    report = audit(copy, config=control['config'])
    assert 'normalize_rule_mismatch' in reasons_of(report)


def test_pointer_sha_mismatch_is_rejected(control, tmp_path):
    copy, target, doc = _single_file_campaign(control, tmp_path)
    record_path = round_path(copy, 1) / 'round.json'
    record = drv._read_json(record_path)
    record['subinput_receipts'][0]['sha256'] = '9' * 64
    write_json(record_path, record)
    report = audit(copy, config=control['config'])
    assert 'receipt_pointer_mismatch' in reasons_of(report)


# --------------------------------------------------------------------------
# structural rejections: schema, fields, types, keys, header
# --------------------------------------------------------------------------

def _inject_with_updates(control, tmp_path, name='struct', **updates):
    copy = forge(control, tmp_path, name=name)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5),
                      updates=updates or None)
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    return audit(copy, config=control['config'])


def test_unsupported_schema_is_rejected(control, tmp_path):
    report = _inject_with_updates(control, tmp_path, name='v2',
                                  schema='pal-soak-subinput-receipt-v2')
    assert 'receipt_schema_rejected' in reasons_of(report)
    assert report['subinputs']['rejected_files'][0]['detail']['found'] == \
        'pal-soak-subinput-receipt-v2'


def test_missing_schema_is_rejected_like_a_bad_schema(control, tmp_path):
    copy = forge(control, tmp_path)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5))
    del doc['schema']
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    report = audit(copy, config=control['config'])
    assert 'receipt_schema_rejected' in reasons_of(report)


def test_producer_claiming_reserved_field_is_rejected(control, tmp_path):
    report = _inject_with_updates(control, tmp_path, name='reserved',
                                  distinct_qualified=5)
    assert 'producer_claim_reserved_field' in reasons_of(report)


def test_unknown_extra_field_is_rejected(control, tmp_path):
    report = _inject_with_updates(control, tmp_path, name='extra',
                                  smuggled_semantics=True)
    assert 'receipt_unknown_field' in reasons_of(report)
    assert report['subinputs']['rejected_files'][0]['detail']['fields'] == \
        ['smuggled_semantics']


def test_missing_header_field_is_rejected(control, tmp_path):
    copy = forge(control, tmp_path)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5))
    del doc['oracle']
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    report = audit(copy, config=control['config'])
    assert 'receipt_missing_field' in reasons_of(report)


def test_wrong_header_type_is_rejected(control, tmp_path):
    report = _inject_with_updates(control, tmp_path, name='type', round='1')
    assert 'receipt_field_type' in reasons_of(report)
    boolified = _inject_with_updates(control, tmp_path, name='booltype',
                                     sub_seed=True)
    assert 'receipt_field_type' in reasons_of(boolified)


def test_counts_invariant_violation_is_rejected(control, tmp_path):
    report = _inject_with_updates(
        control, tmp_path, name='counts',
        counts={'planned': 4, 'generated': 5, 'attempted': 5,
                'oracle_passed': 5, 'completed': 5})
    assert 'counts_inconsistent' in reasons_of(report)


def test_completed_rows_length_mismatch_is_rejected(control, tmp_path):
    copy = forge(control, tmp_path, name='rowslen')
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc = receipt_doc(control, record, 'paper-decimal-fill', range(0, 5),
                      counts={'planned': 6, 'generated': 6, 'attempted': 6,
                              'oracle_passed': 6, 'completed': 6})
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5), doc=doc)
    report = audit(copy, config=control['config'])
    assert 'counts_inconsistent' in reasons_of(report)


def test_header_over_byte_budget_is_rejected(control, tmp_path):
    report = _inject_with_updates(control, tmp_path, name='bigheader',
                                  candidate='x' * 3000)
    assert 'receipt_header_over_limit' in reasons_of(report)


def test_duplicate_json_keys_are_rejected(control, tmp_path):
    copy, target, doc = _single_file_campaign(control, tmp_path)
    raw = target.read_bytes()
    needle = b'"family":"paper-decimal-fill",'
    assert raw.count(needle) == 1
    forged = raw.replace(needle, needle + b'"family":"paper-decimal-fill",', 1)
    target.write_bytes(forged)
    report = audit(copy, config=control['config'])
    assert 'receipt_duplicate_json_key' in reasons_of(report)


def test_noncanonical_serialization_is_rejected(control, tmp_path):
    copy, target, doc = _single_file_campaign(control, tmp_path)
    target.write_bytes(json.dumps(doc, sort_keys=True, indent=1).encode())
    report = audit(copy, config=control['config'])
    assert 'receipt_noncanonical' in reasons_of(report)


# --------------------------------------------------------------------------
# byte limits: limit-first read
# --------------------------------------------------------------------------

def test_read_receipt_capped_enforces_the_boundary_before_parsing(tmp_path):
    exact = tmp_path / 'exact.json'
    exact.write_bytes(b'x' * aud.RECEIPT_MAX_BYTES)
    data, reason = aud._read_receipt_capped(exact)
    assert reason is None and len(data) == aud.RECEIPT_MAX_BYTES
    over = tmp_path / 'over.json'
    over.write_bytes(b'x' * (aud.RECEIPT_MAX_BYTES + 1))
    data, reason = aud._read_receipt_capped(over)
    assert data is None and reason == 'receipt_over_limit'


def test_over_limit_receipt_file_is_refused(control, tmp_path):
    copy = forge(control, tmp_path)
    record = drv._read_json(round_path(copy, 1) / 'round.json')
    indices = range(0, 4096)  # 4096 rows measure past the 512KiB budget
    doc = receipt_doc(control, record, 'paper-decimal-fill', indices)
    target = round_path(copy, 1) / 'subinputs-paper-decimal-fill.json'
    target.write_bytes(_canon(doc) + b'\n')
    assert target.stat().st_size > aud.RECEIPT_MAX_BYTES
    pointers = [{'family': doc['family'], 'entry': doc['entry'],
                 'file': target.name, 'rows': len(doc['rows']),
                 'sha256': _h(target.read_bytes())}]
    record['subinput_receipts'] = pointers
    write_json(round_path(copy, 1) / 'round.json', record)
    report = audit(copy, config=control['config'])
    assert 'receipt_over_limit' in reasons_of(report)
    total = report['subinputs']['total']
    assert total['rows_read'] == 0 and total['distinct_qualified'] == 0


def test_part_files_within_budget_are_accepted(control, tmp_path):
    copy = forge(control, tmp_path)
    first_record = drv._read_json(round_path(copy, 1) / 'round.json')
    doc1 = receipt_doc(control, first_record, 'paper-decimal-fill',
                       range(0, 5), index_origin=0,
                       updates={'part': 1, 'part_count': 2})
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5),
                   doc=doc1, filename='subinputs-paper-decimal-fill.p01.json')
    doc2 = receipt_doc(control, first_record, 'paper-decimal-fill',
                       range(5, 10), index_origin=5,
                       updates={'part': 2, 'part_count': 2})
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(5, 10),
                   doc=doc2, filename='subinputs-paper-decimal-fill.p02.json')
    report = audit(copy, config=control['config'],
                   min_distinct_qualified_subinputs=10)
    assert report['overall'] == aud.PASS, report['failures']
    total = report['subinputs']['total']
    assert total['receipt_files'] == 2 and total['rows_read'] == 10
    assert total['distinct_qualified'] == 10


# --------------------------------------------------------------------------
# cross-segment mixing
# --------------------------------------------------------------------------

def test_cross_segment_receipts_are_verified_but_never_qualify(control,
                                                                tmp_path):
    copy = forge(control, tmp_path)
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5))
    # a newer segment closes the campaign: the older segment's evidence is a
    # splice per R4 and can never feed distinct_qualified
    latest = copy / 'segments' / 'segment-000002'
    (latest / 'rounds').mkdir(parents=True)
    write_json(latest / 'segment.json', {'started_wall': 2000, 'segment': 2})
    write_json(latest / 'segment-close.json',
               {'reason': 'complete', 'stopped_wall': 261200,
                'rounds_total': {'passed': 0, 'failed': 0, 'interrupted': 0,
                                 'unknown': 0}})
    report = audit(copy, config=control['config'])
    assert 'completion_segment_binding' in failed_names(report)
    block = report['subinputs']
    assert block['qualified_segment'] is None
    assert block['total']['rows_read'] == 5
    assert block['total']['rows_whitelist_verified'] == 5
    assert block['total']['distinct_qualified'] == 0


# --------------------------------------------------------------------------
# unknown fields in old records are ignored, not interpreted
# --------------------------------------------------------------------------

def test_unknown_round_record_fields_are_reported_without_judgment(control,
                                                                   tmp_path):
    copy = forge(control, tmp_path)
    record_path = round_path(copy, 2) / 'round.json'
    record = drv._read_json(record_path)
    record['future_field'] = {'anything': True}
    write_json(record_path, record)
    report = audit(copy, config=control['config'])
    assert report['overall'] == aud.PASS, report['failures']
    assert report['subinputs']['unknown_fields_seen'] == ['future_field']


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------

def test_cli_min_distinct_qualified_gate_exit_codes(control, tmp_path):
    env = {'SystemRoot': os.environ['SystemRoot']} if os.name == 'nt' else {}
    copy = forge(control, tmp_path)
    inject_receipt(control, copy, 1, 'paper-decimal-fill', range(0, 5))
    ok = subprocess.run(
        [sys.executable, '-B', str(AUDIT_CLI), 'audit',
         '--campaign', str(copy), '--config',
         str(_config_path(control)), '--min-distinct-qualified-subinputs', '5'],
        capture_output=True, text=True, env=env)
    assert ok.returncode == aud.EXIT_OK, ok.stdout[-2000:]
    report = json.loads(ok.stdout)
    assert report['subinputs']['total']['distinct_qualified'] == 5
    bad = subprocess.run(
        [sys.executable, '-B', str(AUDIT_CLI), 'audit',
         '--campaign', str(copy), '--config',
         str(_config_path(control)), '--min-distinct-qualified-subinputs', '6'],
        capture_output=True, text=True, env=env)
    assert bad.returncode == aud.EXIT_FAIL
    failed = {entry['check'] for entry in json.loads(bad.stdout)['failures']}
    assert failed == {'min_distinct_qualified_subinputs'}


_CONFIG_PATH = None


def _config_path(control):
    global _CONFIG_PATH
    if _CONFIG_PATH is None:
        _CONFIG_PATH = control['manifest'].parent / 'subinput-audit-config.json'
        _CONFIG_PATH.write_text(json.dumps(control['config']), encoding='utf-8')
    return _CONFIG_PATH
