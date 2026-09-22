"""N2 independent whitelist recomputation for ``pal-soak-subinput-receipt-v1``.

Audit side of RECEIPT_CONTRACT decision 3. Every family below is implemented
from the contract text only, in this module's own code:

- the derivation of each logical input is a pure function of the global
  index (never of round / segment / scenario / sub_seed / tmp path / nonce /
  machine / candidate);
- the expected result of each audited row is derived through a DIFFERENT
  verification path than the producer oracle:

  * ``capture-codec`` (F1) — rebuild the input, call the audited entry,
    then verify through the DECODE path (canonical UTC times, status,
    record_id, payload_sha256 binding). N1's fixed-offset equivalence
    oracle is deliberately NOT used here.
  * ``paper-decimal-fill`` (F2) — re-walk the ladder with independent
    Decimal arithmetic and hash a fully independently derived canonical
    dump. The audited entry ``simulate_order_book_fill`` is never called
    by this module.
  * ``uncapped-authz-codec`` (F3) — byte-stability reserialization
    (``json.loads`` then canonical ``json.dumps`` must reproduce the
    payload byte for byte), exact field-set / schema_version match and
    sha256 equality. The dataclass decode round trip is deliberately
    NOT used here.

This module never imports the N1 generator and never evaluates receipt or
manifest content as code. Production entry points are imported lazily so an
audit without receipts for a family never loads that family's dependencies.

Contract notes (interface-face clarifications resolved by ERRATA-001; the
N0 integration wave aligned this module to the adjudicated unique rules):

- entry ids are ``<family>/<verb>`` pinned to ``encode`` / ``simulate-fill``
  / ``payload`` (ERRATA-001 Q2);
- F3's ``authorization_id`` is not part of the contract's input descriptor
  but IS part of the serialized payload; the pinned rule is
  ``authz-<index:012d>`` (ERRATA-001 Q5);
- F2's book always carries a symmetric 5+5 ladder around ``p0`` (asks
  ascending above, bids descending below, same per-j quantities); the
  descriptor lists the walked side's levels only (ERRATA-001 Q1);
- decimal-to-text in descriptors is ``format(d, 'f')`` — never scientific
  notation (ERRATA-001 B-5);
- F1 zone conversion needs the IANA tz database; when ``tzdata`` is not
  importable the verifier fails CLOSED (``tzdata_unavailable``) instead of
  silently substituting fixed offsets (ERRATA-001 B-4).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RECEIPT_SCHEMA = 'pal-soak-subinput-receipt-v1'

_BASE = datetime(2026, 1, 1, tzinfo=UTC)
_ZONES = ('America/New_York', 'Australia/Lord_Howe', 'Europe/Berlin', 'Asia/Tokyo')
_STATUSES = ('completed', 'failed', 'intake_blocked')
_QUANTUM = Decimal('0.001')
_PRICE_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _ok(detail=None):
    return {'ok': True, 'reason': None, 'detail': detail}


def _reject(reason, detail=None):
    return {'ok': False, 'reason': reason, 'detail': detail}


# --------------------------------------------------------------------------
# F1 capture-codec — norm:capture-codec-v1
# --------------------------------------------------------------------------

def _zone(key: str):
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # Fail closed: a missing IANA database must never degrade into a
        # silently different offset (the payload embeds zone wall-clock).
        return None


def capture_codec_input(index: int) -> tuple[dict, str]:
    """Independently derive the F1 logical-input descriptor and its hash."""
    instant = _BASE + timedelta(minutes=index)
    zone_key = _ZONES[index % 4]
    status = _STATUSES[(index // 4) % 3]
    descriptor = {
        'family': 'capture-codec',
        'zone': zone_key,
        'instant_utc': instant.astimezone(UTC).isoformat(),
        'status': status,
    }
    return descriptor, _hash(canonical_json(descriptor))


def verify_capture_codec(index: int, input_sha256: str,
                         result_sha256: str) -> dict:
    descriptor, want_input = capture_codec_input(index)
    if want_input != input_sha256:
        return _reject('input_mismatch', {'expected': want_input})
    tz = _zone(descriptor['zone'])
    if tz is None:
        return _reject('tzdata_unavailable', {'zone': descriptor['zone']})
    from tests.test_research_capture_codec import make_run
    from polymarket_alpha_lab.research_capture_codec import (
        decode_research_capture, encode_research_capture, payload_sha256,
    )
    instant = _BASE + timedelta(minutes=index)
    run = make_run(now=instant.astimezone(tz), status=descriptor['status'])
    record_id = f'si-{index:012d}'
    payload = encode_research_capture(record_id=record_id, model_id='synthetic',
                                      protocol_version='codec-v1', run=run)
    if _hash(payload.encode('utf-8')) != result_sha256:
        return _reject('result_mismatch')
    # Independent verification path: decode, then compare the decoded
    # record's canonical fields against the derived descriptor. This is
    # NOT N1's fixed-offset equivalence oracle.
    record = decode_research_capture(payload, recorded_at=instant,
                                     expected_sha256=payload_sha256(payload))
    if record.record_id != record_id or record.model_id != 'synthetic' \
            or record.protocol_version != 'codec-v1':
        return _reject('decode_field_mismatch', {'field': 'envelope'})
    if record.recorded_at.astimezone(UTC) != instant:
        return _reject('decode_field_mismatch', {'field': 'recorded_at'})
    decoded = record.run
    if decoded.intake.as_of.astimezone(UTC) != instant \
            or decoded.intake.fetched_at.astimezone(UTC) != instant:
        return _reject('decode_field_mismatch', {'field': 'intake_time'})
    status = descriptor['status']
    if status == 'intake_blocked':
        if decoded.intake.status != 'blocked' or decoded.research is not None:
            return _reject('decode_field_mismatch', {'field': 'status'})
    else:
        if decoded.research is None or decoded.research.status != status:
            return _reject('decode_field_mismatch', {'field': 'status'})
    return _ok({'verified_via': 'decode_path'})


# --------------------------------------------------------------------------
# F2 paper-decimal-fill — norm:paper-decimal-fill-v1
# --------------------------------------------------------------------------

def _bounded(value: Decimal, *, positive: bool = False) -> Decimal:
    from polymarket_alpha_lab.research_paper_inputs import bounded_decimal
    return bounded_decimal(value, positive=positive)


def paper_decimal_book(index: int) -> dict:
    """Independently derive the F2 book, order and descriptor hash."""
    side = 'buy' if index % 2 == 0 else 'sell'
    token_id = f'tok-{index % 7}'
    size = _bounded(Decimal(index % 400 + 1) / Decimal(8), positive=True)
    p0 = Decimal('100') + Decimal(index % 50)
    asks, bids = [], []
    for j in range(5):
        step = Decimal('0.25') * (j + 1)
        quantity = _bounded(Decimal((index + j * 13) % 37 + 1), positive=True)
        asks.append((p0 + step, quantity))
        bids.append((p0 - step, quantity))
    captured_at = _BASE + timedelta(seconds=index)
    walked = asks if side == 'buy' else bids  # buy: asks ascending / sell: bids descending
    descriptor = {
        'family': 'paper-decimal-fill',
        'token_id': token_id,
        'side': side,
        'size': format(size, 'f'),
        'levels': [[format(price, 'f'), format(quantity, 'f')]
                   for price, quantity in walked],
        'captured_at': captured_at.isoformat(),
    }
    return {'side': side, 'token_id': token_id, 'size': size, 'asks': asks,
            'bids': bids, 'captured_at': captured_at, 'walked': walked,
            'descriptor': descriptor,
            'input_sha256': _hash(canonical_json(descriptor))}


def _decimal_hash_form(value: Decimal) -> str:
    if not value.is_finite():
        return str(value)
    if value.is_zero():
        return '0'
    with localcontext(Context(prec=60, rounding=ROUND_HALF_EVEN)):
        normalized = value.normalize()
    return format(normalized, 'f')


def _snapshot_sha256(book: dict) -> str:
    """Canonical snapshot digest, reimplemented from the documented form."""
    payload = {
        'token_id': book['token_id'],
        'captured_at': book['captured_at'].isoformat(),
        'bids': [{'price': _decimal_hash_form(price), 'size': _decimal_hash_form(qty)}
                 for price, qty in book['bids']],
        'asks': [{'price': _decimal_hash_form(price), 'size': _decimal_hash_form(qty)}
                 for price, qty in book['asks']],
    }
    return _hash(json.dumps(payload, sort_keys=True,
                            separators=(',', ':')).encode('utf-8'))


def expected_paper_fill(book: dict) -> dict:
    """Re-walk the ladder best-first with independent Decimal arithmetic.

    ``simulate_order_book_fill`` is never called; every numeric field of the
    expected canonical dump is derived here from the raw levels.
    """
    side, size, walked = book['side'], book['size'], book['walked']
    filled = Decimal(0)
    notional = Decimal(0)
    worst = None
    for price, quantity in walked:
        remaining = size - filled
        if remaining <= 0:
            break
        take = remaining if remaining <= quantity else quantity
        filled += take
        notional += take * price
        worst = price
    unfilled = size - filled
    best_bid, best_ask = book['bids'][0][0], book['asks'][0][0]
    with localcontext(_PRICE_CONTEXT):
        spread = (best_ask - best_bid).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
        midpoint = ((best_bid + best_ask) / Decimal(2)).quantize(
            _QUANTUM, rounding=ROUND_HALF_EVEN)
        if filled == 0:
            average = None
            slippage = None
        else:
            exact_average = notional / filled
            average = exact_average.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
            if side == 'buy':
                slippage = max(Decimal(0), exact_average - best_ask).quantize(
                    _QUANTUM, rounding=ROUND_HALF_EVEN)
            else:
                slippage = max(Decimal(0), best_bid - exact_average).quantize(
                    _QUANTUM, rounding=ROUND_HALF_EVEN)

    def dec(value):
        return None if value is None else format(value, 'f')

    dump = {
        'token_id': book['token_id'],
        'side': side,
        'requested_size': dec(size),
        'order_book_captured_at': book['captured_at'].astimezone(UTC).isoformat(),
        'order_book_snapshot_sha256': _snapshot_sha256(book),
        'filled_size': dec(filled),
        'unfilled_size': dec(unfilled),
        'average_price': dec(average),
        'worst_price': dec(worst),
        'best_bid': dec(best_bid),
        'best_ask': dec(best_ask),
        'midpoint': dec(midpoint),
        'spread': dec(spread),
        'slippage_estimate': dec(slippage),
    }
    return {'dump': dump, 'filled': filled, 'unfilled': unfilled,
            'result_sha256': _hash(canonical_json(dump))}


def verify_paper_decimal(index: int, input_sha256: str,
                         result_sha256: str) -> dict:
    book = paper_decimal_book(index)
    if book['input_sha256'] != input_sha256:
        return _reject('input_mismatch', {'expected': book['input_sha256']})
    expected = expected_paper_fill(book)
    if expected['result_sha256'] != result_sha256:
        return _reject('result_mismatch', {'expected': expected['result_sha256']})
    if expected['filled'] + expected['unfilled'] != book['size']:
        return _reject('fill_accounting')
    return _ok({'verified_via': 'independent_decimal_walk',
                'canonical_form_fields': ['order_book_snapshot_sha256']})


# --------------------------------------------------------------------------
# F3 uncapped-authz-codec — norm:uncapped-authz-v1
# --------------------------------------------------------------------------

def uncapped_authz_input(index: int) -> tuple[dict, str]:
    """Independently derive the F3 descriptor and its hash."""
    model_id = 'synthetic'
    request_keys = [(f'rec-{index * 3 + k}', f'{((index * 3 + k) ** 31) % 2 ** 256:064x}')
                    for k in range(3)]
    approved_at = _BASE + timedelta(minutes=index)
    expires_at = approved_at + timedelta(hours=1 + (index % 48))
    adapter_contract_sha256 = sha256(b'rv05-authz:%d' % index).hexdigest()
    descriptor = {
        'family': 'uncapped-authz-codec',
        'model_id': model_id,
        'request_keys': [list(key) for key in request_keys],
        'approved_at': approved_at.isoformat(),
        'expires_at': expires_at.isoformat(),
        'adapter_contract_sha256': adapter_contract_sha256,
    }
    return descriptor, _hash(canonical_json(descriptor))


def verify_uncapped_authz(index: int, input_sha256: str,
                          result_sha256: str) -> dict:
    descriptor, want_input = uncapped_authz_input(index)
    if want_input != input_sha256:
        return _reject('input_mismatch', {'expected': want_input})
    from polymarket_alpha_lab.research_uncapped import (
        UncappedResearchAuthorization, copy_authorization,
    )
    authorization = copy_authorization(UncappedResearchAuthorization(
        authorization_id=f'authz-{index:012d}',
        model_id=descriptor['model_id'],
        adapter_contract_sha256=descriptor['adapter_contract_sha256'],
        approved_at=datetime.fromisoformat(descriptor['approved_at']),
        expires_at=datetime.fromisoformat(descriptor['expires_at']),
        request_keys=tuple(tuple(key) for key in descriptor['request_keys']),
        no_monetary_cap_approved=True,
        research_data_send_approved=True))
    payload = authorization.payload
    # Independent rule: no dataclass decode. Byte stability, exact field
    # set, schema_version and checksum equality, checked from the payload
    # text itself.
    value = json.loads(payload)
    if json.dumps(value, sort_keys=True, separators=(',', ':'),
                  ensure_ascii=True) != payload:
        return _reject('noncanonical_payload')
    expected_fields = set(UncappedResearchAuthorization.__dataclass_fields__) \
        | {'schema_version'}
    if type(value) is not dict or set(value) != expected_fields:
        return _reject('authz_field_set_mismatch')
    if value['schema_version'] != 'research-no-monetary-cap-v1':
        return _reject('authz_schema_version_mismatch')
    digest = sha256(payload.encode('utf-8')).hexdigest()
    if digest != authorization.content_sha256:
        return _reject('content_sha_mismatch')
    if digest != result_sha256:
        return _reject('result_mismatch', {'expected': digest})
    return _ok({'verified_via': 'byte_stable_reserialization'})


# --------------------------------------------------------------------------
# Registry (decision 3): (family, entry) -> normalize rule + N2 verifier
# --------------------------------------------------------------------------

REGISTRY = {
    ('capture-codec', 'capture-codec/encode'): {
        'family': 'capture-codec',
        'normalize_rule': 'norm:capture-codec-v1',
        'verify': verify_capture_codec,
    },
    ('paper-decimal-fill', 'paper-decimal-fill/simulate-fill'): {
        'family': 'paper-decimal-fill',
        'normalize_rule': 'norm:paper-decimal-fill-v1',
        'verify': verify_paper_decimal,
    },
    ('uncapped-authz-codec', 'uncapped-authz-codec/payload'): {
        'family': 'uncapped-authz-codec',
        'normalize_rule': 'norm:uncapped-authz-v1',
        'verify': verify_uncapped_authz,
    },
}


def verify_row(family_entry: tuple, index: int, input_sha256: str,
               result_sha256: str) -> dict:
    """Dispatch one row to its family verifier; never raises for bad input."""
    rule = REGISTRY.get(family_entry)
    if rule is None:
        return _reject('whitelist_unknown', {'family_entry': list(family_entry)})
    try:
        return rule['verify'](index, input_sha256, result_sha256)
    except Exception as error:  # fail closed per row; row-level isolation
        return _reject(f'recompute_error:{error.__class__.__name__}')


__all__ = ('RECEIPT_SCHEMA', 'REGISTRY', 'canonical_json', 'verify_row',
           'capture_codec_input', 'paper_decimal_book', 'expected_paper_fill',
           'uncapped_authz_input')
