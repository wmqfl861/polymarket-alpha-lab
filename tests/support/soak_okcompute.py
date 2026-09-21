"""RV05 ok-compute per-subinput generator producing `pal-soak-subinput-receipt-v1`.

Contract: WorkRoot contract RECEIPT_CONTRACT.md == docs/contracts/
soak-subinput-receipt-v1.md (FROZEN_DRAFT_1). Baseline 2584f6f2 (tree 702d25b5).

The module runs as a scenario child (`python -S -m tests.support.soak_okcompute`,
PYTHONPATH=<repo_root>) and also exposes the pure-function surface used by the
N1 unit tests: derive_input / normalized_input_bytes / target_call /
producer_oracle / serialize_receipt / run_family.

Three semantic families, each a pure function of the global input index only
(no round/segment/seed/machine/candidate influence on input identity):

  capture-codec       research_capture_codec.encode/decode + payload_sha256
  paper-decimal-fill  paper.simulate_order_book_fill (Decimal ladder)
  uncapped-authz-codec research_uncapped payload/content_sha256/decode round trip

Per-row execution order is fixed: input constructed -> target entered ->
target returned(actual) -> oracle checked(verdict) -> row appended. A
dependency-injected ``recorder`` seam observes the order; recorder=None is the
production path and behaves identically (byte-identical output).

No execution authorization is granted or used by this module:
official_cases_run = 0; sandbox_started = False; activation_authorized = False.
Paper-only, report-only, readonly values are preserved on every produced object.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = 'pal-soak-subinput-receipt-v1'
RECEIPT_MAX_BYTES = 524288        # contract decision 2.4 (512KiB, measured)
HEADER_MAX_BYTES = 2048           # contract decision 2.2
DEFAULT_PLANNED_ROWS = 2048       # contract decision 2.4 default (single file)
ABSOLUTE_MAX_ROWS = 4096          # contract decision 2.4 absolute per-round cap
PART_MAX_ROWS = 3839              # floor((524288-2048-1)/136), contract 2.4
SUMMARY_MAX_STDIN_BYTES = 1048576

official_cases_run = 0
sandbox_started = False
activation_authorized = False

_HEX64 = re.compile(r'\A[0-9a-f]{64}\Z')
_FAMILY_ID = re.compile(r'\A[a-z0-9-]{1,32}\Z')

ZONES = ('America/New_York', 'Australia/Lord_Howe', 'Europe/Berlin', 'Asia/Tokyo')
STATUSES = ('completed', 'failed', 'intake_blocked')

COUNT_FIELDS = ('planned', 'generated', 'attempted', 'oracle_passed', 'completed')
HEADER_FIELDS = frozenset({
    'schema', 'family', 'entry', 'normalize_rule', 'candidate', 'manifest_sha256',
    'generator_sha256', 'contract_sha256', 'round', 'segment', 'sub_seed', 'scenario',
    'index_origin', 'oracle', 'counts', 'rows',
})
PART_FIELDS = frozenset({'part', 'part_count'})
RESERVED_FIELD = 'distinct_qualified'


class SoakGeneratorError(Exception):
    """Typed generator failure; ``reason`` matches the wiring failure vocabulary."""

    def __init__(self, reason: str, detail: str = ''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# canonical serialization (contract decision 2.1, mirrors driver _canonical)
# ---------------------------------------------------------------------------

def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _hex64(value: str) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


# ---------------------------------------------------------------------------
# lazy project imports (the -S child has only repo_root on PYTHONPATH)
# ---------------------------------------------------------------------------

_PROJECT = None


def _bootstrap_sys_path() -> None:
    repo = Path(__file__).resolve().parents[2]
    for candidate in (repo / 'src', repo):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.append(str(candidate))
    try:
        ZoneInfo('America/New_York')
    except Exception:
        # -S strips site-packages; the pinned runtime ships tzdata (and pytest,
        # needed by the reused make_run fixture) in its own purelib.
        import sysconfig
        purelib = Path(sysconfig.get_paths()['purelib'])
        if (purelib / 'tzdata' / '__init__.py').is_file() and str(purelib) not in sys.path:
            sys.path.append(str(purelib))


def _ensure_project():
    global _PROJECT
    if _PROJECT is not None:
        return _PROJECT
    try:
        from polymarket_alpha_lab.domain import OrderBookLevel, OrderBookSnapshot
        from polymarket_alpha_lab.paper import (PRICE_QUANTUM, PaperFill, PaperOrder,
                                                simulate_order_book_fill)
        from polymarket_alpha_lab.research_capture_codec import (decode_research_capture,
                                                                 encode_research_capture)
        from polymarket_alpha_lab.research_capture_codec import payload_sha256 as capture_sha
        from polymarket_alpha_lab.research_paper_inputs import bounded_decimal
        from polymarket_alpha_lab.research_uncapped import (UncappedResearchAuthorization,
                                                            copy_authorization,
                                                            decode_authorization)
        from tests.test_research_capture_codec import make_run
    except ModuleNotFoundError:
        _bootstrap_sys_path()
        from polymarket_alpha_lab.domain import OrderBookLevel, OrderBookSnapshot
        from polymarket_alpha_lab.paper import (PRICE_QUANTUM, PaperFill, PaperOrder,
                                                simulate_order_book_fill)
        from polymarket_alpha_lab.research_capture_codec import (decode_research_capture,
                                                                 encode_research_capture)
        from polymarket_alpha_lab.research_capture_codec import payload_sha256 as capture_sha
        from polymarket_alpha_lab.research_paper_inputs import bounded_decimal
        from polymarket_alpha_lab.research_uncapped import (UncappedResearchAuthorization,
                                                            copy_authorization,
                                                            decode_authorization)
        from tests.test_research_capture_codec import make_run

    @dataclass
    class Project:
        pass

    project = Project()
    project.OrderBookLevel = OrderBookLevel
    project.OrderBookSnapshot = OrderBookSnapshot
    project.PRICE_QUANTUM = PRICE_QUANTUM
    project.PaperFill = PaperFill
    project.PaperOrder = PaperOrder
    project.simulate_order_book_fill = simulate_order_book_fill
    project.decode_research_capture = decode_research_capture
    project.encode_research_capture = encode_research_capture
    project.capture_payload_sha256 = capture_sha
    project.bounded_decimal = bounded_decimal
    project.UncappedResearchAuthorization = UncappedResearchAuthorization
    project.copy_authorization = copy_authorization
    project.decode_authorization = decode_authorization
    project.make_run = make_run
    _PROJECT = project
    return _PROJECT


# ---------------------------------------------------------------------------
# derived sub-inputs (contract decision 3; index is the only domain parameter)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubInput:
    family: str
    index: int
    input_sha256: str
    descriptor: dict
    bundle: object = None


@dataclass(frozen=True)
class CaptureCase:
    record_id: str
    run: object
    instant: datetime
    zone_key: str
    status: str


@dataclass(frozen=True)
class PaperCase:
    order: object
    book: object


@dataclass(frozen=True)
class AuthzCase:
    authz: object


@dataclass(frozen=True)
class Actual:
    family: str
    index: int
    result_sha256: str
    value: object
    aux: object = None


def _capture_derive(index: int) -> SubInput:
    project = _ensure_project()
    instant = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)
    zone_key = ZONES[index % 4]
    status = STATUSES[(index // 4) % 3]
    run = project.make_run(now=instant.astimezone(ZoneInfo(zone_key)), status=status)
    descriptor = {
        'family': 'capture-codec',
        'zone': zone_key,
        'instant_utc': instant.astimezone(UTC).isoformat(),
        'status': status,
    }
    bundle = CaptureCase(record_id=f'si-{index:012d}', run=run, instant=instant,
                         zone_key=zone_key, status=status)
    return SubInput('capture-codec', index, sha256(canonical_json(descriptor)).hexdigest(),
                    descriptor, bundle)


def _paper_derive(index: int) -> SubInput:
    project = _ensure_project()
    side = 'buy' if index % 2 == 0 else 'sell'
    token_id = f'tok-{index % 7}'
    size = project.bounded_decimal(Decimal(index % 400 + 1) / Decimal(8), positive=True)
    base = Decimal('100') + Decimal(index % 50)
    step = Decimal('0.25')
    levels = []
    for j in range(5):
        price = base + step * (j + 1) if side == 'buy' else base - step * (j + 1)
        qty = project.bounded_decimal(Decimal((index + j * 13) % 37 + 1), positive=True)
        levels.append((price, qty))
    # Opposite-side mirror ladder (same sizes, sign flipped) keeps midpoint,
    # spread and slippage exercisable; registered with N0/N2 as the completed
    # derivation rule for the contract's unspecified opposite side.
    mirror = [(base - step * (j + 1) if side == 'buy' else base + step * (j + 1), qty)
              for j, (_, qty) in enumerate(levels)]
    captured_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index)
    if side == 'buy':
        asks = tuple(project.OrderBookLevel(price=p, size=q) for p, q in levels)
        bids = tuple(project.OrderBookLevel(price=p, size=q) for p, q in mirror)
    else:
        bids = tuple(project.OrderBookLevel(price=p, size=q) for p, q in levels)
        asks = tuple(project.OrderBookLevel(price=p, size=q) for p, q in mirror)
    book = project.OrderBookSnapshot(token_id=token_id, bids=bids, asks=asks,
                                     captured_at=captured_at)
    order = project.PaperOrder(token_id=token_id, side=side, size=size)
    descriptor = {
        'family': 'paper-decimal-fill',
        'token_id': token_id,
        'side': side,
        'size': format(size, 'f'),
        'levels': [[format(p, 'f'), format(q, 'f')] for p, q in levels],
        'captured_at': captured_at.astimezone(UTC).isoformat(),
    }
    return SubInput('paper-decimal-fill', index,
                    sha256(canonical_json(descriptor)).hexdigest(), descriptor,
                    PaperCase(order=order, book=book))


def _authz_derive(index: int) -> SubInput:
    project = _ensure_project()
    model_id = 'synthetic'
    request_keys = tuple((f'rec-{index * 3 + k}', f'{((index * 3 + k) ** 31) % 2 ** 256:064x}')
                         for k in range(3))
    approved_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)
    expires_at = approved_at + timedelta(hours=1 + (index % 48))
    adapter_contract_sha256 = sha256(b'rv05-authz:%d' % index).hexdigest()
    authz = project.copy_authorization(project.UncappedResearchAuthorization(
        authorization_id=f'authz-{index:012d}',
        model_id=model_id,
        adapter_contract_sha256=adapter_contract_sha256,
        approved_at=approved_at,
        expires_at=expires_at,
        request_keys=request_keys,
        no_monetary_cap_approved=True,
        research_data_send_approved=True,
    ))
    descriptor = {
        'family': 'uncapped-authz-codec',
        'model_id': model_id,
        'request_keys': [[key, value] for key, value in request_keys],
        'approved_at': approved_at.astimezone(UTC).isoformat(),
        'expires_at': expires_at.astimezone(UTC).isoformat(),
        'adapter_contract_sha256': adapter_contract_sha256,
    }
    return SubInput('uncapped-authz-codec', index,
                    sha256(canonical_json(descriptor)).hexdigest(), descriptor,
                    AuthzCase(authz=authz))


@dataclass(frozen=True)
class FamilyDriver:
    family: str
    entry: str
    normalize_rule: str
    oracle_id: str
    derive: object
    target: object
    oracle: object


# ---------------------------------------------------------------------------
# targets and producer oracles (contract decision 3)
# ---------------------------------------------------------------------------

def _capture_target(sub: SubInput) -> Actual:
    project = _ensure_project()
    case = sub.bundle
    payload = project.encode_research_capture(
        record_id=case.record_id, model_id='synthetic', protocol_version='codec-v1',
        run=case.run)
    return Actual('capture-codec', sub.index,
                  sha256(payload.encode('utf-8')).hexdigest(), payload)


def _capture_oracle(sub: SubInput, actual: Actual) -> bool:
    project = _ensure_project()
    case = sub.bundle
    if type(actual.value) is not str:
        return False
    # (a) timezone invariance: a fixed-offset run at the same instant and the
    # same wall clock must encode to identical bytes.
    zoned = case.instant.astimezone(ZoneInfo(case.zone_key))
    fixed = timezone(zoned.utcoffset())
    fixed_run = project.make_run(now=zoned.astimezone(fixed), status=case.status)
    fixed_payload = project.encode_research_capture(
        record_id=case.record_id, model_id='synthetic', protocol_version='codec-v1',
        run=fixed_run)
    if fixed_payload != actual.value:
        return False
    # (b) decode path binds payload_sha256 and re-encodes to the same bytes.
    try:
        record = project.decode_research_capture(
            actual.value, recorded_at=case.instant,
            expected_sha256=project.capture_payload_sha256(actual.value))
        re_encoded = project.encode_research_capture(
            record_id=case.record_id, model_id='synthetic', protocol_version='codec-v1',
            run=record.run)
    except Exception:
        return False
    return re_encoded == actual.value


def _decimal_text(value):
    return None if value is None else format(value, 'f')


def _fill_dump(fill) -> dict:
    return {
        'token_id': fill.token_id,
        'side': fill.side,
        'requested_size': format(fill.requested_size, 'f'),
        'order_book_captured_at': fill.order_book_captured_at.astimezone(UTC).isoformat(),
        'order_book_snapshot_sha256': fill.order_book_snapshot_sha256,
        'filled_size': format(fill.filled_size, 'f'),
        'unfilled_size': format(fill.unfilled_size, 'f'),
        'average_price': _decimal_text(fill.average_price),
        'worst_price': _decimal_text(fill.worst_price),
        'best_bid': _decimal_text(fill.best_bid),
        'best_ask': _decimal_text(fill.best_ask),
        'midpoint': _decimal_text(fill.midpoint),
        'spread': _decimal_text(fill.spread),
        'slippage_estimate': _decimal_text(fill.slippage_estimate),
    }


def _paper_target(sub: SubInput) -> Actual:
    project = _ensure_project()
    case = sub.bundle
    fill = project.simulate_order_book_fill(case.order, case.book)
    return Actual('paper-decimal-fill', sub.index,
                  sha256(canonical_json(_fill_dump(fill))).hexdigest(), fill)


def _quantized(value: Decimal, quantum: Decimal) -> bool:
    return value == value.quantize(quantum)


def _paper_oracle(sub: SubInput, actual: Actual) -> bool:
    project = _ensure_project()
    fill = actual.value
    if type(fill) is not project.PaperFill:
        return False
    size = fill.requested_size
    if fill.filled_size + fill.unfilled_size != size:
        return False
    if fill.is_complete != (fill.unfilled_size == 0):
        return False
    quantum = project.PRICE_QUANTUM
    for value in (fill.average_price, fill.worst_price, fill.midpoint, fill.spread,
                  fill.slippage_estimate):
        if value is not None and not _quantized(value, quantum):
            return False
    if fill.best_bid is not None and fill.best_ask is not None:
        if fill.spread != (fill.best_ask - fill.best_bid).quantize(quantum):
            return False
        if fill.midpoint != ((fill.best_bid + fill.best_ask) / Decimal('2')).quantize(quantum):
            return False
    if fill.filled_size == 0:
        return fill.average_price is None and fill.worst_price is None \
            and fill.slippage_estimate is None
    if fill.side == 'buy':
        if not fill.best_ask <= fill.worst_price:
            return False
        if not fill.best_ask <= fill.average_price <= fill.worst_price:
            return False
    else:
        if not fill.worst_price <= fill.best_bid:
            return False
        if not fill.worst_price <= fill.average_price <= fill.best_bid:
            return False
    return True


def _authz_target(sub: SubInput) -> Actual:
    project = _ensure_project()
    case = sub.bundle
    payload = case.authz.payload
    checksum = case.authz.content_sha256
    decoded = project.decode_authorization(payload, checksum)
    return Actual('uncapped-authz-codec', sub.index,
                  sha256(payload.encode('utf-8')).hexdigest(), payload,
                  aux=(checksum, decoded))


def _authz_oracle(sub: SubInput, actual: Actual) -> bool:
    project = _ensure_project()
    case = sub.bundle
    if type(actual.value) is not str:
        return False
    if sha256(actual.value.encode('utf-8')).hexdigest() != case.authz.content_sha256:
        return False
    try:
        decoded = project.decode_authorization(actual.value, case.authz.content_sha256)
    except Exception:
        return False
    return decoded.payload == actual.value


FAMILIES = {
    'capture-codec': FamilyDriver(
        'capture-codec', 'capture-codec/encode', 'norm:capture-codec-v1',
        'producer:capture-codec-v1', _capture_derive, _capture_target, _capture_oracle),
    'paper-decimal-fill': FamilyDriver(
        'paper-decimal-fill', 'paper-decimal-fill/simulate-fill',
        'norm:paper-decimal-fill-v1', 'producer:paper-decimal-fill-v1',
        _paper_derive, _paper_target, _paper_oracle),
    'uncapped-authz-codec': FamilyDriver(
        'uncapped-authz-codec', 'uncapped-authz-codec/payload',
        'norm:uncapped-authz-v1', 'producer:uncapped-authz-v1',
        _authz_derive, _authz_target, _authz_oracle),
}

DEFAULT_FAMILIES = ('capture-codec',)


def _driver(family: str) -> FamilyDriver:
    driver = FAMILIES.get(family) if type(family) is str else None
    if driver is None or _FAMILY_ID.fullmatch(driver.family) is None:
        raise SoakGeneratorError('whitelist_unknown', str(family))
    return driver


# ---------------------------------------------------------------------------
# public pure-function surface (wiring plan section 4)
# ---------------------------------------------------------------------------

def normalized_input_bytes(family: str, index: int) -> bytes:
    return canonical_json(_driver(family).derive(index).descriptor)


def derive_input(family: str, index: int) -> SubInput:
    return _driver(family).derive(index)


def input_sha256(family: str, index: int) -> str:
    return _driver(family).derive(index).input_sha256


def target_call(family: str, index: int) -> Actual:
    driver = _driver(family)
    return driver.target(driver.derive(index))


def producer_oracle(family: str, index: int, actual: Actual) -> bool:
    driver = _driver(family)
    return driver.oracle(driver.derive(index), actual)


def actual_result_sha256(family: str, actual: Actual) -> str:
    if type(actual) is not Actual or actual.family != family:
        raise SoakGeneratorError('actual_invalid', family)
    return actual.result_sha256


# ---------------------------------------------------------------------------
# receipt document construction, validation, serialization (decision 2/5/7)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReceiptIdentity:
    candidate: str
    manifest_sha256: str
    contract_sha256: str
    generator_sha256: str
    round: int
    segment: int
    sub_seed: int
    scenario: str


def build_identity(*, candidate: str, manifest_sha256: str, contract_sha256: str,
                   generator_sha256: str, round: int, segment: int, sub_seed: int,
                   scenario: str) -> ReceiptIdentity:  # noqa: A002 (echoes payload)
    if type(candidate) is not str or not candidate:
        raise SoakGeneratorError('config_invalid', 'candidate')
    try:
        parsed_candidate = json.loads(candidate)
    except ValueError:
        raise SoakGeneratorError('config_invalid', 'candidate not JSON') from None
    if type(parsed_candidate) is not dict \
            or json.dumps(parsed_candidate, sort_keys=True, separators=(',', ':')) != candidate:
        raise SoakGeneratorError('config_invalid', 'candidate not canonical JSON object')
    if not _hex64(manifest_sha256) or not _hex64(contract_sha256) \
            or not _hex64(generator_sha256):
        raise SoakGeneratorError('config_invalid', 'identity sha256 fields')
    if type(round) is not int or round < 1 or type(segment) is not int or segment < 0 \
            or type(sub_seed) is not int or sub_seed < 0 or type(scenario) is not str \
            or not scenario:
        raise SoakGeneratorError('config_invalid', 'transport fields')
    return ReceiptIdentity(candidate, manifest_sha256, contract_sha256, generator_sha256,
                           round, segment, sub_seed, scenario)


def _count_dict(planned: int, generated: int, attempted: int, oracle_passed: int,
                completed: int) -> dict:
    return {'planned': planned, 'generated': generated, 'attempted': attempted,
            'oracle_passed': oracle_passed, 'completed': completed}


def build_receipt_doc(identity: ReceiptIdentity, driver: FamilyDriver, index_origin: int,
                      counts: dict, rows: list, *, part: int | None = None,
                      part_count: int | None = None) -> dict:
    doc = {
        'schema': SCHEMA,
        'family': driver.family,
        'entry': driver.entry,
        'normalize_rule': driver.normalize_rule,
        'candidate': identity.candidate,
        'manifest_sha256': identity.manifest_sha256,
        'generator_sha256': identity.generator_sha256,
        'contract_sha256': identity.contract_sha256,
        'round': identity.round,
        'segment': identity.segment,
        'sub_seed': identity.sub_seed,
        'scenario': identity.scenario,
        'index_origin': index_origin,
        'oracle': driver.oracle_id,
        'counts': dict(counts),
        'rows': [list(row) for row in rows],
    }
    if part is not None:
        doc['part'] = part
        doc['part_count'] = part_count
    return doc


def serialize_receipt(doc: dict) -> bytes:
    """Contract 2.1: sort_keys compact JSON as UTF-8 plus one trailing newline."""
    return canonical_json(doc) + b'\n'


def _header_bytes(doc: dict) -> int:
    skeleton = dict(doc)
    skeleton['rows'] = []
    return len(canonical_json(skeleton))


def _scan_reserved(value) -> bool:
    if type(value) is dict:
        return any(key == RESERVED_FIELD or _scan_reserved(item) for key, item in value.items())
    if type(value) is list:
        return any(_scan_reserved(item) for item in value)
    return False


def check_receipt_doc(doc: dict) -> None:
    """Producer-side structural self-check before any write (decision 2.2/2.3/5/7)."""
    if type(doc) is not dict or doc.get('schema') != SCHEMA:
        raise SoakGeneratorError('receipt_schema_rejected', 'schema')
    if _scan_reserved(doc):
        # The audit-exclusive claim name is refused wherever it appears, even
        # before the closed field-set check classifies it as unknown.
        raise SoakGeneratorError('producer_claim_reserved_field', RESERVED_FIELD)
    expected = HEADER_FIELDS | (PART_FIELDS if 'part' in doc else frozenset())
    if set(doc) != expected:
        raise SoakGeneratorError('receipt_unknown_field' if set(doc) - expected
                                 else 'receipt_invalid', 'top-level field set')
    for name in ('family', 'entry', 'normalize_rule', 'candidate', 'manifest_sha256',
                 'generator_sha256', 'contract_sha256', 'scenario', 'oracle'):
        if type(doc[name]) is not str or not doc[name]:
            raise SoakGeneratorError('receipt_invalid', name)
    if not _hex64(doc['manifest_sha256']) or not _hex64(doc['generator_sha256']) \
            or not _hex64(doc['contract_sha256']):
        raise SoakGeneratorError('receipt_invalid', 'identity sha256')
    if doc['family'] not in FAMILIES or FAMILIES[doc['family']].entry != doc['entry']:
        raise SoakGeneratorError('whitelist_unknown', doc['family'])
    if FAMILIES[doc['family']].normalize_rule != doc['normalize_rule']:
        raise SoakGeneratorError('receipt_invalid', 'normalize_rule')
    for name in ('round', 'segment', 'sub_seed', 'index_origin'):
        if type(doc[name]) is not int or doc[name] < 0 or (name == 'round' and doc[name] < 1):
            raise SoakGeneratorError('receipt_invalid', name)
    if 'part' in doc:
        part, part_count = doc['part'], doc['part_count']
        if type(part) is not int or part < 2 or type(part_count) is not int \
                or part_count < 2 or part > part_count:
            raise SoakGeneratorError('receipt_invalid', 'part fields')
    counts = doc['counts']
    if type(counts) is not dict or set(counts) != set(COUNT_FIELDS):
        raise SoakGeneratorError('receipt_invalid', 'counts field set')
    if any(type(counts[name]) is not int or counts[name] < 0 for name in COUNT_FIELDS):
        raise SoakGeneratorError('receipt_invalid', 'counts types')
    if not (counts['planned'] >= counts['generated'] >= counts['attempted']
            >= counts['oracle_passed'] == counts['completed']):
        raise SoakGeneratorError('counts_inconsistent', json.dumps(counts, sort_keys=True))
    rows = doc['rows']
    if type(rows) is not list or len(rows) != counts['completed']:
        raise SoakGeneratorError('counts_inconsistent', 'len(rows) != completed')
    seen = set()
    for row in rows:
        if type(row) is not list or len(row) != 2:
            raise SoakGeneratorError('receipt_invalid', 'row shape')
        if not _hex64(row[0]) or not _hex64(row[1]):
            raise SoakGeneratorError('receipt_invalid', 'row hex64')
        if row[0] in seen:
            raise SoakGeneratorError('receipt_invalid', 'duplicate input_sha256 in file')
        seen.add(row[0])


@dataclass(frozen=True)
class WrittenReceipt:
    file: str
    bytes_len: int
    sha256: str
    rows: int


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_receipt_file(path: Path, doc: dict) -> WrittenReceipt:
    """Validate, measure real bytes, then atomically write (no truncation ever)."""
    check_receipt_doc(doc)
    data = serialize_receipt(doc)
    if _header_bytes(doc) > HEADER_MAX_BYTES:
        raise SoakGeneratorError('receipt_header_over_limit',
                                 f'header={_header_bytes(doc)}')
    if len(data) > RECEIPT_MAX_BYTES:
        raise SoakGeneratorError('receipt_over_limit', f'bytes={len(data)}')
    _atomic_write(Path(path), data)
    with open(Path(path), 'rb') as handle:
        if handle.read() != data:
            raise SoakGeneratorError('receipt_write_mismatch', str(path))
    return WrittenReceipt(Path(path).name, len(data), sha256(data).hexdigest(),
                          len(doc['rows']))


def verify_receipt_file(path: Path, expected: bytes) -> None:
    """Read-back verification: a truncated or padded file must be detectable."""
    try:
        with open(Path(path), 'rb') as handle:
            observed = handle.read()
    except OSError as error:
        raise SoakGeneratorError('receipt_missing', str(error)) from None
    if observed != expected:
        raise SoakGeneratorError('receipt_write_mismatch',
                                 f'expected={len(expected)} observed={len(observed)}')


# ---------------------------------------------------------------------------
# run_family: per-row execution with recorder seam (decision 6)
# ---------------------------------------------------------------------------

@dataclass
class _RowLog:
    input_sha256: str
    result_sha256: str | None = None
    generated: bool = False
    attempted: bool = False
    oracle_passed: bool = False
    completed: bool = False


@dataclass(frozen=True)
class FamilyResult:
    family: str
    entry: str
    ok: bool
    failure: dict | None
    counts: dict
    rows: list
    parts: tuple  # ((file_name, doc), ...) in part order


def _emit(recorder, name: str, payload: dict) -> None:
    if recorder is not None:
        recorder((name, payload))


def _part_bounds(planned: int) -> list:
    bounds = list(range(0, planned, PART_MAX_ROWS))
    bounds.append(planned)
    return bounds


def part_file_name(family: str, part: int) -> str:
    if part <= 1:
        return f'subinputs-{family}.json'
    return f'subinputs-{family}.p{part:02d}.json'


def run_family(family: str, index_origin: int, planned_rows: int, *,
               identity: ReceiptIdentity, recorder=None) -> FamilyResult:
    """Execute one family block: derive -> target -> oracle -> row, per index."""
    driver = _driver(family)
    if type(index_origin) is not int or index_origin < 0:
        raise SoakGeneratorError('config_invalid', 'index_origin')
    if type(planned_rows) is not int or not 1 <= planned_rows <= ABSOLUTE_MAX_ROWS:
        raise SoakGeneratorError('config_invalid', 'planned_rows')
    logs: list[_RowLog | None] = [None] * planned_rows
    rows: list[list] = []
    seen: set[str] = set()
    failure: dict | None = None
    for k in range(planned_rows):
        index = index_origin + k
        log = None
        stage = 'derive'
        try:
            sub = driver.derive(index)
            log = _RowLog(sub.input_sha256)
            _emit(recorder, 'input_constructed',
                  {'family': family, 'index': index, 'input_sha256': sub.input_sha256})
            if sub.input_sha256 in seen:
                # Same logical input: fold, never rewrite or duplicate a row.
                _emit(recorder, 'input_duplicate',
                      {'family': family, 'index': index, 'input_sha256': sub.input_sha256})
                continue
            seen.add(sub.input_sha256)
            stage = 'target'
            _emit(recorder, 'target_entered',
                  {'family': family, 'index': index, 'input_sha256': sub.input_sha256})
            log.generated = True
            actual = driver.target(sub)
            log.result_sha256 = actual.result_sha256
            _emit(recorder, 'target_returned',
                  {'family': family, 'index': index, 'input_sha256': sub.input_sha256,
                   'result_sha256': actual.result_sha256})
            log.attempted = True
            stage = 'oracle'
            verdict = driver.oracle(sub, actual)
            _emit(recorder, 'oracle_checked',
                  {'family': family, 'index': index, 'input_sha256': sub.input_sha256,
                   'verdict': verdict})
            if verdict is not True:
                failure = {'family': family, 'stage': 'oracle', 'index': index,
                           'reason': 'oracle_failed',
                           'detail': 'producer oracle rejected actual result'}
                _emit(recorder, 'family_failed', failure)
                break
            log.oracle_passed = True
            rows.append([sub.input_sha256, actual.result_sha256])
            log.completed = True
            _emit(recorder, 'row_appended',
                  {'family': family, 'index': index, 'input_sha256': sub.input_sha256,
                   'result_sha256': actual.result_sha256})
        except SoakGeneratorError:
            raise
        except Exception as error:
            failure = {'family': family, 'stage': stage, 'index': index,
                       'reason': 'target_error' if stage != 'derive' else 'derive_error',
                       'detail': repr(error)[:200]}
            _emit(recorder, 'family_failed', failure)
            break
        finally:
            logs[k] = log
    bounds = _part_bounds(planned_rows)
    part_count = len(bounds) - 1
    parts = []
    row_cursor = 0
    for part_no in range(1, part_count + 1):
        lo, hi = bounds[part_no - 1], bounds[part_no]
        part_logs = logs[lo:hi]
        part_rows = rows[row_cursor:row_cursor + sum(1 for item in part_logs
                                                     if item is not None and item.completed)]
        row_cursor += len(part_rows)
        counts = _count_dict(
            hi - lo,
            sum(1 for item in part_logs if item is not None and item.generated),
            sum(1 for item in part_logs if item is not None and item.attempted),
            sum(1 for item in part_logs if item is not None and item.oracle_passed),
            len(part_rows))
        # Part 1 is the unnumbered base file (contract 2.4 naming); only parts
        # numbered >= 2 carry the part/part_count header fields.
        is_numbered_part = part_count > 1 and part_no >= 2
        doc = build_receipt_doc(identity, driver, index_origin + lo, counts, part_rows,
                                part=part_no if is_numbered_part else None,
                                part_count=part_count if is_numbered_part else None)
        parts.append((part_file_name(family, part_no), doc))
    overall = _count_dict(
        planned_rows,
        sum(1 for item in logs if item is not None and item.generated),
        sum(1 for item in logs if item is not None and item.attempted),
        sum(1 for item in logs if item is not None and item.oracle_passed),
        len(rows))
    return FamilyResult(family, driver.entry, failure is None, failure, overall, rows,
                        tuple(parts))


# ---------------------------------------------------------------------------
# round execution and scenario main() (wiring plan section 2/4)
# ---------------------------------------------------------------------------

PAYLOAD_FIELDS = frozenset({'round', 'segment', 'scenario', 'sub_seed', 'tmp_dir'})


def _split_rows(families: tuple, planned_rows: int) -> dict:
    if not families:
        raise SoakGeneratorError('config_invalid', 'families')
    base = planned_rows // len(families)
    split = {}
    for position, family in enumerate(families):
        split[family] = base + (planned_rows % len(families) if position == 0 else 0)
    if any(rows < 1 for rows in split.values()):
        raise SoakGeneratorError('config_invalid', 'planned_rows smaller than families')
    return split


def run_round(payload: dict, *, families: tuple, planned_rows: int,
              identity: ReceiptIdentity, out_dir: Path, recorder=None) -> dict:
    """Run every family for one round; write receipts; return the stdout summary."""
    if type(payload) is not dict or set(payload) != set(PAYLOAD_FIELDS):
        raise SoakGeneratorError('config_invalid', 'payload field set')
    split = _split_rows(families, planned_rows)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    family_summaries = []
    failures = []
    bytes_total = 0
    for family in families:
        driver = _driver(family)
        rows_for_family = split[family]
        index_origin = (identity.round - 1) * rows_for_family
        result = run_family(family, index_origin, rows_for_family, identity=identity,
                            recorder=recorder)
        files = []
        for file_name, doc in result.parts:
            written = write_receipt_file(out_dir / file_name, doc)
            bytes_total += written.bytes_len
            files.append({'file': written.file, 'rows': written.rows,
                          'sha256': written.sha256, 'bytes': written.bytes_len})
        family_summaries.append({
            'family': family,
            'entry': driver.entry,
            'files': files,
            'rows': result.counts['completed'],
            'counts': {name: result.counts[name] for name in COUNT_FIELDS},
        })
        if not result.ok:
            failures.append(result.failure)
    summary = {
        'echo_round': identity.round,
        'echo_seed': identity.sub_seed,
        'echo_scenario': identity.scenario,
        'ok': not failures,
        'subinput_families': family_summaries,
        'receipt_bytes_total': bytes_total,
    }
    if failures:
        summary['failure'] = failures[0]
    return summary


def default_generator_sha256() -> str:
    return sha256(Path(__file__).read_bytes()).hexdigest()


def _contract_sha256_from_repo() -> str | None:
    path = Path(__file__).resolve().parents[2] / 'docs' / 'contracts' \
        / 'soak-subinput-receipt-v1.md'
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return sha256(data).hexdigest()


def _parse_args(argv: list) -> dict:
    options = {'families': [], 'rows': DEFAULT_PLANNED_ROWS, 'candidate': None,
               'manifest_sha256': None, 'contract_sha256': None, 'contract_path': None}
    i = 0
    while i < len(argv):
        arg = argv[i]

        def value(name):
            nonlocal i
            if i + 1 >= len(argv):
                raise SoakGeneratorError('config_invalid', f'missing value for {name}')
            i += 1
            return argv[i]

        if arg == '--family':
            options['families'].append(value(arg))
        elif arg == '--rows':
            try:
                options['rows'] = int(value(arg))
            except ValueError:
                raise SoakGeneratorError('config_invalid', 'rows not an int') from None
        elif arg == '--candidate':
            options['candidate'] = value(arg)
        elif arg == '--manifest-sha256':
            options['manifest_sha256'] = value(arg)
        elif arg == '--contract-sha256':
            options['contract_sha256'] = value(arg)
        elif arg == '--contract-path':
            options['contract_path'] = value(arg)
        else:
            raise SoakGeneratorError('config_invalid', f'unknown argument {arg}')
        i += 1
    if not options['families']:
        options['families'] = list(DEFAULT_FAMILIES)
    if options['contract_sha256'] is None and options['contract_path'] is not None:
        options['contract_sha256'] = sha256(
            Path(options['contract_path']).read_bytes()).hexdigest()
    if options['contract_sha256'] is None:
        options['contract_sha256'] = _contract_sha256_from_repo()
    return options


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        options = _parse_args(argv)
        raw = sys.stdin.buffer.read(SUMMARY_MAX_STDIN_BYTES + 1)
        if len(raw) > SUMMARY_MAX_STDIN_BYTES:
            raise SoakGeneratorError('config_invalid', 'payload too large')
        payload = json.loads(raw.decode('utf-8'))
        if type(payload) is not dict or set(payload) != set(PAYLOAD_FIELDS):
            raise SoakGeneratorError('config_invalid', 'payload field set')
        if options['candidate'] is None:
            raise SoakGeneratorError('config_invalid', '--candidate required')
        if options['manifest_sha256'] is None:
            raise SoakGeneratorError('config_invalid', '--manifest-sha256 required')
        if options['contract_sha256'] is None:
            raise SoakGeneratorError('config_invalid', '--contract-sha256 required')
        if not 1 <= options['rows'] <= ABSOLUTE_MAX_ROWS:
            raise SoakGeneratorError('config_invalid', 'rows out of 1..4096')
        for family in options['families']:
            _driver(family)
        contract_sha256 = options['contract_sha256']
        identity = build_identity(
            candidate=options['candidate'],
            manifest_sha256=options['manifest_sha256'],
            contract_sha256=contract_sha256,
            generator_sha256=default_generator_sha256(),
            round=payload['round'],
            segment=payload['segment'],
            sub_seed=payload['sub_seed'],
            scenario=payload['scenario'],
        )
        out_dir = Path(payload['tmp_dir'])
        if not out_dir.is_dir():
            out_dir = Path.cwd()
        summary = run_round(payload, families=tuple(options['families']),
                            planned_rows=options['rows'], identity=identity,
                            out_dir=out_dir)
        sys.stdout.write(json.dumps(summary, sort_keys=True,
                                    separators=(',', ':')) + '\n')
        return 0 if summary['ok'] else 1
    except SoakGeneratorError as error:
        report = {'ok': False, 'error': error.reason, 'detail': error.detail}
        sys.stdout.write(json.dumps(report, sort_keys=True,
                                    separators=(',', ':')) + '\n')
        sys.stderr.write(f'{error.reason}: {error.detail}\n')
        return 2
    except Exception as error:  # first failure preserved, never retried
        report = {'ok': False, 'error': 'generator_unexpected', 'detail': repr(error)[:200]}
        sys.stdout.write(json.dumps(report, sort_keys=True,
                                    separators=(',', ':')) + '\n')
        sys.stderr.write(repr(error) + '\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
