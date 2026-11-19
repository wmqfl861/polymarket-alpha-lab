"""FX-12 candidate binding and forensic replay of paper decisions.

Two faces of one contract (M1 plan section 3 FX-12, items 2 and 3):

RECORD SIDE -- binding. :func:`bind_candidate` turns one
:class:`~polymarket_alpha_lab.papersim.pipeline.EntryRequest` plus its
:class:`~polymarket_alpha_lab.papersim.pipeline.EntryDecision` into a
:class:`~polymarket_alpha_lab.papersim.store.CandidateSubmission` whose
``input_payload`` is a canonical, version-pinned replay bundle carrying
the decision-time evidence -- the raw model quote (xG/soccer or the
original crypto probability record), the RAW order-book ladder, the
league-strength kappa ``bridge`` table, the news record, the portfolio
state (bankroll/cash/reserved/held sides/team exposure), the decision
clock (``book_time``/``now``/``max_book_age_seconds``), and the fee and
configuration evidence. ``snapshot_refs`` cite raw evidence bytes
registered through :mod:`polymarket_alpha_lab.papersim.snapshots`, the
gate report JSON carries the full ordered reason record, and the code
commit rides on the scan run row. Rejected AND construction-failed
candidates bind the same way (:func:`bind_construction_failure`
re-runs the pure constructor so the recorded failure is the observed
one).

REPLAY SIDE -- forensics. :func:`replay` is READ-ONLY取证 over a
supplied managed connection: it re-reads the candidate, its scan run,
its decision-time price evidence and every cited snapshot; verifies
each snapshot's hash, byte length and (optionally) schema version
against the stored bytes; refuses to run unless the recorded code
commit equals the operator-supplied TRUSTED commit (the current HEAD
never silently substitutes for the recorded one) and the recomputed
policy fingerprint matches the recorded one; then recomputes
:func:`~polymarket_alpha_lab.papersim.pipeline.decide_entry` in the
reviewed fixed environment and compares EVERY field -- conclusion,
ordered reasons, per-step statuses, sizing, fees, walks, bridge
fingerprint and version pins -- under one rounding policy. Nothing is
fetched from the network, no code from the record is executed (only
the reviewed library's own pure functions run), missing evidence or a
missing snapshot is an explicit failure, and the module issues SELECT
statements only: replay never writes a row, never adds an entry and
never opens a connection itself.

Paper boundary: binding and replay are paper-evidence operations only
(``paper_only``/``report_only``/``readonly``); they grant no execution
authorization and never touch an order.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from collections.abc import Mapping

from polymarket_alpha_lab.papersim import snapshots, soccer_model, store
from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability, TouchProbability
from polymarket_alpha_lab.papersim.gates import NewsCheck
from polymarket_alpha_lab.papersim.pipeline import (
    GATE_VERSION,
    EntryDecision,
    EntryRequest,
    bridge_fingerprint,
    decide_entry,
    policy_code_sha256,
)
from polymarket_alpha_lab.papersim.soccer_model import SoccerCandidate
from polymarket_alpha_lab.papersim.store import PapersimStoreError

__all__ = [
    "CONSTRUCTION_BUNDLE_TOKEN",
    "CONSTRUCTION_RETURNED_NONE",
    "CONSTRUCTION_VALUE_ERROR",
    "CONSTRUCTOR_SOCCER",
    "ENTRY_BUNDLE_TOKEN",
    "PapersimReplayError",
    "REPLAY_BUNDLE_VERSION",
    "REPLAY_NUMERIC_DP",
    "bind_candidate",
    "bind_construction_failure",
    "bind_entry",
    "bind_scan_run",
    "bound_book_bytes",
    "bound_median_probability",
    "decision_report",
    "decision_report_json",
    "replay",
    "replay_bundle_payload",
    "request_from_replay_bundle",
]

#: Version of the replay bundle codec; stored inside every payload and
#: checked on replay, so an old bundle never replays under a new reader.
REPLAY_BUNDLE_VERSION = 1

#: ONE rounding policy for every numeric field comparison: floats are
#: compared after rounding to this many decimals (1e-9 money scale --
#: the pipeline's own cent-scale epsilon discipline; no wide epsilon
#: ever swallows a tampered fee, price or walk value).
REPLAY_NUMERIC_DP = 9

ENTRY_BUNDLE_TOKEN = 'papersim.replay.entry_request'
CONSTRUCTION_BUNDLE_TOKEN = 'papersim.replay.construction_failure'

CONSTRUCTOR_SOCCER = 'soccer_model.candidate_for_market'
CONSTRUCTION_RETURNED_NONE = 'construction_returned_none'
CONSTRUCTION_VALUE_ERROR = 'construction_value_error'

#: The only dataclasses a bundle may reconstruct: the frozen evidence
#: records of the reviewed library. Anything else in a ``$record`` tag
#: fails replay -- a bundle never names arbitrary code to execute.
_RECORD_TYPES = {f'{record_type.__module__}.{record_type.__qualname__}': record_type
                 for record_type in (NewsCheck, TouchProbability, TerminalProbability,
                                     SoccerCandidate)}

#: The only constructors a construction-failure bundle may re-run.
_CONSTRUCTORS = (CONSTRUCTOR_SOCCER,)

#: Sentinel for a construction that no longer reproduces its recorded
#: failure (re-run produced a candidate instead of refusing).
_CONSTRUCTION_SUCCEEDED = 'construction_succeeded'

_CANDIDATE_SQL = (
    'SELECT c.candidate_id, c.scan_id, c.candidate_key, c.conclusion, c.gate_report, '
    'c.rejection_reasons, c.input_payload, c.snapshot_refs, c.order_book, c.book_fetched_at, '
    'c.evaluated_at, c.condition_id, c.market_id, c.market_slug, c.side, '
    's.code_commit, s.schema_version, s.gate_version, s.finished_at '
    'FROM papersim.candidates c JOIN papersim.scan_runs s ON s.scan_id = c.scan_id '
    'WHERE c.candidate_id = %s'
)

_EVIDENCE_SQL = (
    'SELECT evidence_id, evidence_role, observed_at, model_probability, market_ref_kind, '
    'market_ref, token_ref, side, bid, ask, mid, last, book_sha256, snapshot_sha256, '
    'source_kind, source_ref FROM papersim.candidate_price_evidence '
    'WHERE candidate_id = %s AND evidence_role = \'DECISION\' ORDER BY evidence_id LIMIT 1'
)


class PapersimReplayError(ValueError):
    """One explicit replay failure: a stable code plus a detail string."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(code if not detail else f'{code}: {detail}')
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Canonical bundle codec (record side encodes, replay side decodes)
# ---------------------------------------------------------------------------

def _encode(value):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PapersimReplayError('papersim_replay_payload_non_finite')
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PapersimReplayError('papersim_replay_payload_non_finite')
        return {'$decimal': str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise PapersimReplayError('papersim_replay_payload_datetime_naive')
        return {'$datetime': value.isoformat()}
    if isinstance(value, Mapping):
        items = []
        for key in sorted(value):
            if not isinstance(key, str):
                raise PapersimReplayError('papersim_replay_payload_map_key_invalid')
            items.append([key, _encode(value[key])])
        return {'$map': items}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        record_type = type(value)
        return {'$record': {'$record_type': f'{record_type.__module__}.{record_type.__qualname__}',
                           'fields': {field.name: _encode(getattr(value, field.name))
                                      for field in dataclasses.fields(record_type)}}}
    raise PapersimReplayError('papersim_replay_payload_unsupported',
                              type(value).__qualname__)


def _decode(value):
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PapersimReplayError('papersim_replay_payload_non_finite')
        return value
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, Mapping) and len(value) == 1:
        (tag, inner), = value.items()
        if tag == '$decimal':
            if not isinstance(inner, str):
                raise PapersimReplayError('papersim_replay_payload_invalid', '$decimal')
            return Decimal(inner)
        if tag == '$datetime':
            if not isinstance(inner, str):
                raise PapersimReplayError('papersim_replay_payload_invalid', '$datetime')
            moment = datetime.fromisoformat(inner)
            if moment.tzinfo is None:
                raise PapersimReplayError('papersim_replay_payload_datetime_naive')
            return moment
        if tag == '$map':
            if not isinstance(inner, list):
                raise PapersimReplayError('papersim_replay_payload_invalid', '$map')
            return {key: _decode(item) for key, item in inner}
        if tag == '$record':
            if not isinstance(inner, Mapping) or set(inner) != {'$record_type', 'fields'}:
                raise PapersimReplayError('papersim_replay_payload_invalid', '$record')
            record_type = _RECORD_TYPES.get(inner['$record_type'])
            if record_type is None:
                raise PapersimReplayError('papersim_replay_payload_record_unknown',
                                          str(inner['$record_type']))
            fields = inner['fields']
            if not isinstance(fields, Mapping):
                raise PapersimReplayError('papersim_replay_payload_invalid', '$record fields')
            expected = {field.name for field in dataclasses.fields(record_type)}
            if set(fields) != expected:
                raise PapersimReplayError('papersim_replay_payload_record_fields',
                                          str(inner['$record_type']))
            return record_type(**{name: _decode(fields[name]) for name in fields})
    raise PapersimReplayError('papersim_replay_payload_invalid',
                              type(value).__qualname__ if not isinstance(value, Mapping)
                              else 'plain object in bundle')


def replay_bundle_payload(request: EntryRequest) -> str:
    """Canonical JSON text of one request's full decision-time evidence."""
    document = {'bundle': ENTRY_BUNDLE_TOKEN, 'version': REPLAY_BUNDLE_VERSION,
                'fields': {field.name: _encode(getattr(request, field.name))
                           for field in dataclasses.fields(EntryRequest)}}
    return json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False)


def request_from_replay_bundle(payload) -> EntryRequest:
    """Reconstruct the request from a bundle; strict, never inventive.

    ``payload`` is the stored ``input_payload`` (parsed object or JSON
    text). The bundle token, the codec version, every field name and
    every record type are verified before construction; unknown
    material fails with an explicit code instead of being guessed.
    """
    document = _as_json(payload)
    if not isinstance(document, Mapping) or set(document) != {'bundle', 'version', 'fields'} \
            or document['bundle'] != ENTRY_BUNDLE_TOKEN:
        raise PapersimReplayError('papersim_replay_bundle_invalid')
    if document['version'] != REPLAY_BUNDLE_VERSION:
        raise PapersimReplayError('papersim_replay_schema_version_mismatch',
                                  f'{document["version"]} != {REPLAY_BUNDLE_VERSION}')
    fields = document['fields']
    if not isinstance(fields, Mapping):
        raise PapersimReplayError('papersim_replay_bundle_invalid')
    known = {field.name for field in dataclasses.fields(EntryRequest)}
    unknown = set(fields) - known
    if unknown:
        raise PapersimReplayError('papersim_replay_bundle_field_unknown',
                                  ','.join(sorted(unknown)))
    try:
        return EntryRequest(**{name: _decode(fields[name]) for name in fields})
    except PapersimReplayError:
        raise
    except (TypeError, ValueError) as exc:
        raise PapersimReplayError('papersim_replay_bundle_invalid', str(exc)) from exc


def construction_payload(inputs: Mapping, *, constructor: str, failure: str,
                         failure_detail: str) -> str:
    """Canonical JSON text of one construction-failure bundle."""
    document = {'bundle': CONSTRUCTION_BUNDLE_TOKEN, 'version': REPLAY_BUNDLE_VERSION,
                'constructor': constructor, 'failure': failure,
                'failure_detail': failure_detail,
                'inputs': {key: _encode(inputs[key]) for key in sorted(inputs)}}
    return json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False)


# ---------------------------------------------------------------------------
# Decision report (the gate_report JSON on record AND the comparison target)
# ---------------------------------------------------------------------------

def _walk_dict(walk) -> dict | None:
    if walk is None:
        return None
    return {'side': walk.side, 'notional': walk.notional, 'best': walk.best,
            'vwap': walk.vwap, 'filled': walk.filled,
            'filled_notional': walk.filled_notional, 'shares': walk.shares,
            'depth_near2c': walk.depth_near2c}


def _step_dict(step) -> dict:
    return {'step': step.step, 'status': step.status, 'reasons': list(step.reasons),
            'detail': step.detail}


def decision_report(decision: EntryDecision) -> dict:
    """The full-field decision record: every comparable field, plain JSON.

    Step ``result`` objects are replaced by the numbers and booleans
    they contributed (walks, tier, gate checks, crypto evidence); the
    ordered step statuses/reasons and the top-level scalars are kept
    verbatim. Used verbatim as the stored ``gate_report`` and as the
    replay comparison target, so nothing is compared that was not
    recorded and nothing recorded escapes comparison.
    """
    crypto = None
    if decision.crypto_evidence is not None:
        crypto = {}
        for field in dataclasses.fields(decision.crypto_evidence):
            value = getattr(decision.crypto_evidence, field.name)
            crypto[field.name] = list(value) if isinstance(value, tuple) else value
    return {
        'accepted': decision.accepted,
        'reasons': list(decision.reasons),
        'steps': [_step_dict(step) for step in decision.steps],
        'market_id': decision.market_id,
        'fixture': decision.fixture,
        'market_kind': decision.market_kind,
        'tier': None if decision.tier is None else {
            'tier': decision.tier.tier, 'min_notional': decision.tier.min_notional,
            'max_notional': decision.tier.max_notional, 'reason': decision.tier.reason},
        'notional': decision.notional,
        'market_class': decision.market_class,
        'fee_category': decision.fee_category,
        'fee_rate': decision.fee_rate,
        'market_rate': decision.market_rate,
        'entry_fee': decision.entry_fee,
        'acceptance_walk': _walk_dict(decision.acceptance_walk),
        'actual_walk': _walk_dict(decision.actual_walk),
        'gate_checks': None if decision.gate_report is None else {
            'checks': dict(decision.gate_report.checks),
            'failed': list(decision.gate_report.decision.failed)},
        'cross_league_mw': None if decision.cross_league_mw_check is None else {
            'passed': decision.cross_league_mw_check.passed,
            'failed': list(decision.cross_league_mw_check.failed)},
        'crypto_evidence': crypto,
        'under_risk_flagged': decision.under_risk_flagged,
        'bridge_sha256': decision.bridge_sha256,
        'pipeline_version': decision.pipeline_version,
        'gate_version': decision.gate_version,
        'model_version': decision.model_version,
        'code_sha256': decision.code_sha256,
    }


def decision_report_json(decision: EntryDecision) -> str:
    return json.dumps(decision_report(decision), sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


# ---------------------------------------------------------------------------
# Record-side binding helpers
# ---------------------------------------------------------------------------

def bound_book_bytes(request: EntryRequest) -> bytes:
    """The canonical RAW ladder bytes registered as the book snapshot.

    The ladder keeps its level order (no key sorting): a book is an
    ordered sequence, and replay compares it structurally.
    """
    return json.dumps(_encode(list(request.book)), separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _quote_field(quote, names: tuple[str, ...]):
    """First present non-None field off a quote object or mapping."""
    if quote is None:
        return None
    for name in names:
        if isinstance(quote, Mapping):
            if quote.get(name) is not None:
                return quote[name]
        else:
            value = getattr(quote, name, None)
            if value is not None:
                return value
    return None


def bound_median_probability(request: EntryRequest, decision: EntryDecision):
    """The decision-time model median off the recorded evidence shapes."""
    if decision.crypto_evidence is not None:
        return decision.crypto_evidence.model_median
    return _quote_field(request.model_quote, ('model_median',))


def bind_scan_run(*, team: str, started_at: datetime, finished_at: datetime,
                  code_commit: str, params: str = '{}', operator: str = 'unattended',
                  schema_version: int = REPLAY_BUNDLE_VERSION) -> store.ScanRunRecord:
    """The scan identity row: decision clock bounds plus the code commit."""
    return store.ScanRunRecord(team=team, started_at=started_at, finished_at=finished_at,
                               code_commit=code_commit, schema_version=schema_version,
                               gate_version=GATE_VERSION, operator=operator, params=params)


def bind_entry(decision: EntryDecision, *, entered_at: datetime) -> store.EntrySubmission:
    """The single paper entry of an accepted candidate, from its decision."""
    if not decision.accepted or decision.notional is None or decision.tier is None \
            or decision.actual_walk is None or decision.actual_walk.vwap is None \
            or decision.fee_category is None or decision.fee_rate is None \
            or decision.entry_fee is None:
        raise PapersimReplayError('papersim_replay_entry_fields_missing')
    stake = Decimal(str(decision.notional))
    price = Decimal(str(decision.actual_walk.vwap))
    shares = (stake / price).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    return store.EntrySubmission(stake=stake, price=price, shares=shares,
                                 fee_category=decision.fee_category,
                                 fee_rate=Decimal(str(decision.fee_rate)),
                                 entry_fee=Decimal(str(decision.entry_fee)),
                                 fee_source='schedule', tier=decision.tier.tier,
                                 entered_at=entered_at)


def bind_candidate(request: EntryRequest, decision: EntryDecision, *,
                   team: str, candidate_key: str, side: str, market_kind: str,
                   evaluated_at: datetime,
                   market_ref_kind: str = 'market_id',
                   market_id: str | None = None, market_slug: str | None = None,
                   condition_id: str | None = None, yes_token_id: str | None = None,
                   no_token_id: str | None = None, market_type: str | None = None,
                   event_key: str | None = None, event_start_at: datetime | None = None,
                   model_name: str = 'soccer', model_version: str | None = None,
                   median_probability=None, variant_quotes: str | list = '[]',
                   snapshot_refs: tuple[str, ...] | list = (),
                   book_snapshot_sha256: str | None = None,
                   decision_snapshot_sha256: str | None = None,
                   source_kind: str = 'clob_book', source_ref: str | None = None,
                   entry: store.EntrySubmission | None = None) -> store.CandidateSubmission:
    """Bind one decided request into its replayable candidate row data.

    The bundle carries the full decision-time evidence; the gate report
    carries the full ordered reason record; the price evidence row is
    the decision-time observation paired with the same market reference
    and side; ``snapshot_refs`` cite registered raw bytes. Rejected
    candidates bind identically (their reasons are the record).
    ``event_key``/``event_start_at`` (FX-26) carry the pure
    :func:`~polymarket_alpha_lab.papersim.store.candidate_event_key`
    product and the adapter-parsed game start (or crypto expiry); both
    default to ``None`` so a caller without the inputs leaves the
    columns NULL and evaluation keeps the market-key fallback.
    """
    market_ref = (market_id if market_ref_kind == 'market_id' else
                  condition_id if market_ref_kind == 'condition_id' else
                  market_slug if market_ref_kind == 'market_slug' else None)
    if market_ref is None:
        raise PapersimReplayError('papersim_replay_binding_market_ref_missing')
    if source_ref is None and source_kind != 'operator_attestation':
        source_ref = f'{market_ref_kind}:{market_ref}'
    ask = None if decision.acceptance_walk is None else decision.acceptance_walk.best
    evidence = store.PriceEvidenceObservation(
        observed_at=evaluated_at, evidence_role='DECISION',
        model_probability=None if median_probability is None else Decimal(str(median_probability)),
        market_ref_kind=market_ref_kind, market_ref=market_ref, token_ref=yes_token_id,
        side=side, bid=None, ask=None if ask is None else Decimal(str(ask)), mid=None,
        book_sha256=book_snapshot_sha256, snapshot_sha256=decision_snapshot_sha256,
        source_kind=source_kind, source_ref=source_ref)
    return store.CandidateSubmission(
        candidate_key=candidate_key,
        conclusion='ACCEPTED' if decision.accepted else 'REJECTED',
        side=side, market_kind=market_kind, team=team,
        model_name=model_name,
        model_version=model_version or decision.model_version,
        input_payload=replay_bundle_payload(request),
        gate_report=decision_report_json(decision),
        evaluated_at=evaluated_at,
        condition_id=condition_id, market_id=market_id or request.market_id,
        market_slug=market_slug, yes_token_id=yes_token_id, no_token_id=no_token_id,
        market_type=market_type, event_key=event_key,
        event_start_at=event_start_at, binding_version=1,
        median_probability=None if median_probability is None
        else Decimal(str(median_probability)),
        variant_quotes=variant_quotes,
        snapshot_refs=json.dumps(list(snapshot_refs), separators=(',', ':')),
        rejection_reasons=json.dumps(list(decision.reasons), separators=(',', ':')),
        order_book=json.dumps(_encode(list(request.book)), separators=(',', ':'),
                              allow_nan=False),
        book_fetched_at=request.book_time,
        price_evidence=(evidence,), entry=entry)


def bind_construction_failure(*, team: str, candidate_key: str, evaluated_at: datetime,
                              inputs: Mapping, side: str = 'YES', market_kind: str = 'other',
                              market_id: str | None = None, market_slug: str | None = None,
                              condition_id: str | None = None, market_type: str | None = None,
                              model_name: str = 'soccer',
                              model_version: str = 'soccer-r23-construction',
                              snapshot_refs: tuple[str, ...] | list = (),
                              constructor: str = CONSTRUCTOR_SOCCER) -> store.CandidateSubmission:
    """Record a construction failure by RE-RUNNING the pure constructor.

    The failure recorded is the failure observed: ``inputs`` are proven
    codec-clean, the constructor runs once here, and its refusal (None
    or ValueError) becomes the recorded ``rejection_reasons`` entry and
    bundle payload. A construction that unexpectedly SUCCEEDS refuses
    to be recorded as failed.
    """
    if constructor not in _CONSTRUCTORS:
        raise PapersimReplayError('papersim_replay_constructor_unknown', constructor)
    clean = {key: _decode(_encode(inputs[key])) for key in inputs}
    try:
        outcome = soccer_model.candidate_for_market(**clean)
    except ValueError as exc:
        failure, failure_detail = CONSTRUCTION_VALUE_ERROR, str(exc)
    else:
        if outcome is None:
            failure, failure_detail = CONSTRUCTION_RETURNED_NONE, ''
        else:
            raise PapersimReplayError('papersim_replay_construction_succeeded')
    return store.CandidateSubmission(
        candidate_key=candidate_key, conclusion='CONSTRUCTION_FAILED', side=side,
        market_kind=market_kind, team=team, model_name=model_name, model_version=model_version,
        input_payload=construction_payload(clean, constructor=constructor, failure=failure,
                                           failure_detail=failure_detail),
        gate_report='{}', evaluated_at=evaluated_at, condition_id=condition_id,
        market_id=market_id, market_slug=market_slug, market_type=market_type,
        snapshot_refs=json.dumps(list(snapshot_refs), separators=(',', ':')),
        rejection_reasons=json.dumps([failure], separators=(',', ':')))


# ---------------------------------------------------------------------------
# Replay side
# ---------------------------------------------------------------------------

def _as_json(value):
    """Normalize a stored jsonb value (object already parsed or text)."""
    if value is None or isinstance(value, (dict, list, bool, int, float)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, Decimal):
        return value
    raise PapersimReplayError('papersim_replay_row_invalid', type(value).__qualname__)


def _same_number(left, right) -> bool:
    try:
        return round(float(left), REPLAY_NUMERIC_DP) == round(float(right), REPLAY_NUMERIC_DP)
    except (TypeError, ValueError, ArithmeticError):
        return False


def _compare(stored, recomputed, path: str) -> None:
    """Structural comparison under the ONE rounding policy."""
    if stored is None or recomputed is None:
        if stored is not recomputed:
            raise PapersimReplayError('papersim_replay_decision_mismatch', path)
        return
    if isinstance(stored, bool) or isinstance(recomputed, bool):
        if isinstance(stored, bool) and isinstance(recomputed, bool) and stored == recomputed:
            return
        raise PapersimReplayError('papersim_replay_decision_mismatch', path)
    if isinstance(stored, (int, float, Decimal)) and isinstance(recomputed, (int, float, Decimal)):
        if _same_number(stored, recomputed):
            return
        raise PapersimReplayError('papersim_replay_decision_mismatch', path)
    if isinstance(stored, str) or isinstance(recomputed, str):
        if stored != recomputed:
            raise PapersimReplayError('papersim_replay_decision_mismatch', path)
        return
    if isinstance(stored, list) and isinstance(recomputed, list):
        if len(stored) != len(recomputed):
            raise PapersimReplayError('papersim_replay_decision_mismatch', path)
        for index, (one, two) in enumerate(zip(stored, recomputed, strict=True)):
            _compare(one, two, f'{path}[{index}]')
        return
    if isinstance(stored, Mapping) and isinstance(recomputed, Mapping):
        if set(stored) != set(recomputed):
            raise PapersimReplayError('papersim_replay_decision_mismatch', path)
        for key in stored:
            _compare(stored[key], recomputed[key], f'{path}.{key}')
        return
    if stored != recomputed:
        raise PapersimReplayError('papersim_replay_decision_mismatch', path)


def _fetch_one(conn, sql: str, params) -> dict | None:
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(sql, params)
        columns = [description.name for description in cursor.description]
        row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row, strict=True))


def _require_trusted_commit(trusted_code_commit) -> str:
    if not isinstance(trusted_code_commit, str) or len(trusted_code_commit) != 40 \
            or any(character not in '0123456789abcdef'
                   for character in trusted_code_commit):
        raise PapersimReplayError('papersim_replay_trusted_commit_invalid')
    return trusted_code_commit


def _run_constructor(payload: Mapping) -> tuple[str, str]:
    """Re-run the recorded pure constructor on the recorded inputs."""
    constructor = payload.get('constructor')
    if constructor not in _CONSTRUCTORS:
        raise PapersimReplayError('papersim_replay_constructor_unknown', str(constructor))
    try:
        inputs = {key: _decode(value) for key, value in payload['inputs'].items()}
    except PapersimReplayError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PapersimReplayError('papersim_replay_bundle_invalid', str(exc)) from exc
    try:
        outcome = soccer_model.candidate_for_market(**inputs)
    except ValueError as exc:
        # The constructor's OWN refusal is a construction failure, not a
        # bundle problem -- it must replay as the recorded ValueError.
        return CONSTRUCTION_VALUE_ERROR, str(exc)
    if outcome is None:
        return CONSTRUCTION_RETURNED_NONE, ''
    return _CONSTRUCTION_SUCCEEDED, ''


def replay(conn, candidate_id: int, *, trusted_code_commit: str,
           expected_scan_schema_version: int = REPLAY_BUNDLE_VERSION,
           expected_snapshot_schema_version: int | None = None) -> dict:
    """Read-only forensic replay of one recorded candidate.

    Verifies the recorded code commit against the operator-supplied
    TRUSTED commit (never the ambient HEAD), the scan schema version,
    every cited snapshot (hash, byte length, optional schema version),
    the decision-time evidence coherence, and then recomputes
    ``decide_entry`` in this reviewed environment and compares every
    recorded field under the single rounding policy. Returns the check
    summary on match; raises :class:`PapersimReplayError` on the first
    explicit failure. Issues SELECT statements only.
    """
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int):
        raise PapersimReplayError('papersim_replay_candidate_id_invalid')
    trusted = _require_trusted_commit(trusted_code_commit)
    row = _fetch_one(conn, _CANDIDATE_SQL, (candidate_id,))
    if row is None:
        raise PapersimReplayError('papersim_replay_candidate_missing', str(candidate_id))
    checks: list[str] = []
    if row['code_commit'] is None:
        raise PapersimReplayError('papersim_replay_code_commit_missing')
    if row['code_commit'] != trusted:
        raise PapersimReplayError('papersim_replay_code_commit_mismatch',
                                  f'{row["code_commit"]} != {trusted}')
    checks.append('code_commit')
    if int(row['schema_version']) != expected_scan_schema_version:
        raise PapersimReplayError('papersim_replay_schema_version_mismatch',
                                  f'{row["schema_version"]} != {expected_scan_schema_version}')
    checks.append('schema_version')
    for reference in _as_json(row['snapshot_refs']) or []:
        snapshots.verify_snapshot(conn, reference,
                                  expected_schema_version=expected_snapshot_schema_version)
    checks.append('snapshots')
    if row['conclusion'] == 'CONSTRUCTION_FAILED':
        return _replay_construction(row, checks)
    return _replay_decision(conn, row, checks)


def _replay_construction(row: dict, checks: list) -> dict:
    payload = _as_json(row['input_payload'])
    if not isinstance(payload, Mapping) or payload.get('bundle') != CONSTRUCTION_BUNDLE_TOKEN:
        raise PapersimReplayError('papersim_replay_bundle_invalid')
    if payload.get('version') != REPLAY_BUNDLE_VERSION:
        raise PapersimReplayError('papersim_replay_schema_version_mismatch')
    failure, failure_detail = _run_constructor(payload)
    recorded_failure = payload.get('failure')
    if failure != recorded_failure or failure_detail != payload.get('failure_detail', ''):
        raise PapersimReplayError('papersim_replay_construction_mismatch',
                                  f'{recorded_failure} vs {failure}')
    reasons = _as_json(row['rejection_reasons']) or []
    if reasons != [recorded_failure]:
        raise PapersimReplayError('papersim_replay_reasons_mismatch', str(reasons))
    checks.extend(['constructor', 'rejection_reasons'])
    return {'candidate_id': int(row['candidate_id']), 'scan_id': int(row['scan_id']),
            'candidate_key': row['candidate_key'], 'mode': 'construction_failure',
            'matched': True, 'code_commit': row['code_commit'], 'checks': tuple(checks)}


def _replay_decision(conn, row: dict, checks: list) -> dict:
    request = request_from_replay_bundle(row['input_payload'])
    checks.append('bundle')
    stored_book = _as_json(row['order_book'])
    _compare(stored_book, _encode(list(request.book)), 'order_book')
    if row['book_fetched_at'] != request.book_time:
        raise PapersimReplayError('papersim_replay_book_time_mismatch')
    checks.append('book')
    evidence = _fetch_one(conn, _EVIDENCE_SQL, (int(row['candidate_id']),))
    if evidence is None:
        raise PapersimReplayError('papersim_replay_evidence_missing')
    if evidence['observed_at'] != row['evaluated_at']:
        raise PapersimReplayError('papersim_replay_evidence_time_mismatch')
    ref_column = {'condition_id': 'condition_id', 'market_id': 'market_id',
                  'market_slug': 'market_slug'}.get(evidence['market_ref_kind'])
    if ref_column is not None and row[ref_column] is not None \
            and evidence['market_ref'] != row[ref_column]:
        raise PapersimReplayError('papersim_replay_evidence_binding_mismatch')
    if evidence['side'] != row['side']:
        raise PapersimReplayError('papersim_replay_evidence_binding_mismatch')
    checks.append('evidence')
    if evidence['book_sha256'] is not None:
        stored = snapshots.read_snapshot(conn, evidence['book_sha256'])
        if stored is None:
            raise PapersimStoreError('papersim_snapshot_missing')
        try:
            book_snapshot = _as_json(stored['content'].decode('utf-8'))
        except (UnicodeDecodeError, PapersimReplayError) as exc:
            raise PapersimReplayError('papersim_replay_book_snapshot_mismatch',
                                      str(exc)) from exc
        _compare(book_snapshot, stored_book, 'book_snapshot')
    if evidence['snapshot_sha256'] is not None:
        snapshots.verify_snapshot(conn, evidence['snapshot_sha256'])
    decision = decide_entry(request)
    checks.append('recompute')
    if decision.code_sha256 != policy_code_sha256():
        raise PapersimReplayError('papersim_replay_policy_fingerprint_mismatch')
    payload = _as_json(row['gate_report'])
    if not isinstance(payload, Mapping):
        raise PapersimReplayError('papersim_replay_bundle_invalid', 'gate_report')
    if payload.get('code_sha256') != decision.code_sha256:
        raise PapersimReplayError('papersim_replay_policy_fingerprint_mismatch')
    if row['gate_version'] != decision.gate_version:
        raise PapersimReplayError('papersim_replay_gate_version_mismatch')
    if request.bridge is not None and payload.get('bridge_sha256') != \
            bridge_fingerprint(request.bridge):
        raise PapersimReplayError('papersim_replay_bridge_mismatch')
    checks.append('policy_pins')
    walk_best = None if decision.acceptance_walk is None else decision.acceptance_walk.best
    if walk_best is None:
        if evidence['ask'] is not None:
            raise PapersimReplayError('papersim_replay_evidence_ask_mismatch')
    elif evidence['ask'] is None or not _same_number(evidence['ask'], walk_best):
        raise PapersimReplayError('papersim_replay_evidence_ask_mismatch')
    _compare(payload, decision_report(decision), 'gate_report')
    checks.append('decision')
    if row['conclusion'] != ('ACCEPTED' if decision.accepted else 'REJECTED'):
        raise PapersimReplayError('papersim_replay_conclusion_mismatch')
    reasons = _as_json(row['rejection_reasons']) or []
    if reasons != list(decision.reasons):
        raise PapersimReplayError('papersim_replay_reasons_mismatch')
    checks.extend(['conclusion', 'rejection_reasons'])
    return {'candidate_id': int(row['candidate_id']), 'scan_id': int(row['scan_id']),
            'candidate_key': row['candidate_key'], 'mode': 'entry_request',
            'matched': True, 'code_commit': row['code_commit'], 'checks': tuple(checks)}
