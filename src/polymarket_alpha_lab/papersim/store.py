"""Pure transformation and validation for the papersim v2 store.

This module is deliberately I/O free: it builds idempotency keys, validates
row shapes against the managed schema's CHECK contracts, applies the
scoring-association semantics (09D design v2 sections 4.4, 4.5 and
4.2-7) and prepares the data the transactional adapter in
``store_psycopg`` writes. Durable persistence is project-private native
PostgreSQL only (Project Iron Rule 1); nothing here opens connections,
reads clocks or falls back to file journals.

Key semantics pinned here (09D v2 section 4):

- ``UNKNOWN`` means "observed but not final" or "no observation at all"
  for a market outcome: it never carries a confirmation time and never
  shadows an existing non-UNKNOWN observation.
- ``INSUFFICIENT`` means "no usable decision-time price" for a candidate:
  missing evidence is never replaced by 0 or any filled value.
- The effective market outcome is the latest non-UNKNOWN observation by
  ``observed_at`` with tie-break ``outcome_id DESC`` (P1-1 (b): there is
  no superseded_by column; corrections append later observations).
- The decision-time observation of a candidate is its DECISION evidence
  row with the smallest ``evidence_id`` (P3-8: no pointer column).
- Evidence time order: ``CLOSE.observed_at >= DECISION.observed_at`` and
  ``DECISION.observed_at <= scan_runs.finished_at``; violations reject the
  whole scan transaction with ``papersim_evidence_time_order_invalid``.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
import math

OUTCOME_TOKENS = ('YES', 'NO', 'YES-basket', 'VOIDED', 'UNKNOWN')
NATURAL_OUTCOME_TOKENS = ('YES', 'NO', 'YES-basket')
OUTCOME_SOURCE_KINDS = ('gamma_close', 'clob_terminal_price', 'data_api_resolution',
                        'operator_attestation', 'legacy_import')
EVIDENCE_ROLES = ('DECISION', 'CLOSE')
MARKET_REF_KINDS = ('condition_id', 'market_id', 'token_id', 'market_slug')
SIDE_TOKENS = ('YES', 'NO', 'YES-basket', 'NO-basket')
MARKET_KINDS = ('terminal', 'touch', 'other')
CONCLUSIONS = ('ACCEPTED', 'REJECTED', 'CONSTRUCTION_FAILED')
SETTLEMENT_CLASSIFICATIONS = ('NATURAL', 'EXIT', 'VOIDED_REFUND')
WIN_LOSS_TOKENS = ('win', 'loss')
FEE_SOURCES = ('schedule', 'estimated')
BANKROLL_REASONS = ('INITIAL', 'SCAN_ENTRY', 'SETTLE_NATURAL', 'SETTLE_EXIT',
                    'VOIDED_REFUND', 'FEE', 'ADJUSTMENT', 'REVERSAL', 'LEGACY_IMPORT')
STAKE_SHARE_TOLERANCE = Decimal('0.01')

# M2 closed-loop operations (S0, M2 plan sections 3.1, 3.2 and 5.3).
COMMAND_PHASES = ('STARTED', 'PREPARED', 'SUCCEEDED', 'FAILED', 'RECOVERY_READ')
MARK_SAMPLING_KINDS = ('PERIODIC', 'CLOSE')
QUEUE_CONTROL_KINDS = ('PAUSE', 'RESUME')
BANKROLL_BASE_REASONS = ('INITIAL', 'ADJUSTMENT', 'LEGACY_IMPORT')
BANKROLL_TOLERANCE = Decimal('0.005')
SETTLEMENT_CASH_REASONS = {'NATURAL': 'SETTLE_NATURAL', 'EXIT': 'SETTLE_EXIT',
                           'VOIDED_REFUND': 'VOIDED_REFUND'}


class PapersimStoreError(ValueError):
    """Validation failure carrying one fixed public code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str):
    raise PapersimStoreError(code) from None


def _require_text(value, name: str) -> str:
    if type(value) is not str or not value:
        _fail(f'papersim_{name}_invalid')
    return value


def _optional_text(value, name: str):
    if value is None:
        return None
    return _require_text(value, name)


def _require_timestamp(value, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(f'papersim_{name}_invalid')
    return value


def _optional_timestamp(value, name: str):
    if value is None:
        return None
    return _require_timestamp(value, name)


def _require_number(value, name: str):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        _fail(f'papersim_{name}_invalid')
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f'papersim_{name}_invalid')
    if isinstance(value, Decimal) and not value.is_finite():
        _fail(f'papersim_{name}_invalid')
    return value


def _as_decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _require_probability_closed(value, name: str):
    number = _require_number(value, name)
    if not Decimal(0) <= _as_decimal(number) <= Decimal(1):
        _fail(f'papersim_{name}_invalid')
    return number


def _optional_probability_closed(value, name: str):
    if value is None:
        return None
    return _require_probability_closed(value, name)


def _optional_interval_open(value, name: str):
    """Quote columns use the open interval (0, 1): 0/1 quotes are not fillable."""
    if value is None:
        return None
    number = _require_number(value, name)
    if not Decimal(0) < _as_decimal(number) < Decimal(1):
        _fail(f'papersim_{name}_invalid')
    return number


def _optional_interval_closed(value, name: str):
    """Mark/exit columns use the closed interval [0, 1] (terminal extremes legal)."""
    if value is None:
        return None
    number = _require_number(value, name)
    if not Decimal(0) <= _as_decimal(number) <= Decimal(1):
        _fail(f'papersim_{name}_invalid')
    return number


def _optional_hash64(value, name: str):
    if value is None:
        return None
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        _fail(f'papersim_{name}_invalid')
    return value


def _require_json_payload(value, name: str) -> str:
    """Accept a JSON string or a JSON-encodable structure; return canonical text."""
    if type(value) is str:
        try:
            json.loads(value)
        except ValueError:
            _fail(f'papersim_{name}_invalid')
        return value
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        _fail(f'papersim_{name}_invalid')


def _require_json_list(value, name: str) -> str:
    text = _require_json_payload(value, name)
    if not isinstance(json.loads(text), list):
        _fail(f'papersim_{name}_invalid')
    return text


def utc_stamp(value: datetime) -> str:
    """Second-resolution UTC stamp used by the idempotency keys (09D v2 section 4.4)."""
    return _require_timestamp(value, 'observed_at').astimezone(UTC).strftime('%Y%m%d%H%M%S')


def market_outcome_evidence_key(condition_id, market_id, source_kind, source_ref, observed_at) -> str:
    """Writer-side idempotency key for ``papersim.market_outcomes``."""
    return '|'.join(('mo', condition_id or '', market_id or '', _require_text(source_kind, 'source_kind'),
                     source_ref or '', utc_stamp(observed_at)))


def price_evidence_key(candidate_id, evidence_role, market_ref_kind, market_ref, side,
                       source_kind, observed_at) -> str:
    """Writer-side idempotency key for ``papersim.candidate_price_evidence``."""
    if type(candidate_id) is not int:
        _fail('papersim_candidate_id_invalid')
    return '|'.join(('cpe', str(candidate_id), _require_text(evidence_role, 'evidence_role'),
                     _require_text(market_ref_kind, 'market_ref_kind'),
                     _require_text(market_ref, 'market_ref'), _require_text(side, 'side'),
                     _require_text(source_kind, 'source_kind'), utc_stamp(observed_at)))


def scan_key(team, started_at, code_commit, gate_version, schema_version, params_text) -> str:
    """sha256(team|started_at_utc|code_commit|gate_version|schema_version|params_hash)."""
    _require_text(team, 'team')
    _require_timestamp(started_at, 'started_at')
    _require_text(gate_version, 'gate_version')
    if type(schema_version) is not int:
        _fail('papersim_schema_version_invalid')
    params = _require_json_payload(params_text, 'params')
    material = '|'.join((team, utc_stamp(started_at), code_commit or '', gate_version,
                         str(schema_version), sha256(params.encode('utf-8')).hexdigest()))
    return sha256(material.encode('utf-8')).hexdigest()


#: Market classes recognized by :func:`candidate_event_key` (FX-26).
EVENT_KEY_CLASSES = ('sports', 'crypto', 'other')


def _clean_text(value):
    """Strip a string; blank/whitespace-only or non-str counts as missing."""
    if type(value) is not str:
        return None
    stripped = value.strip()
    return stripped or None


def candidate_event_key(*, market_class: str,
                        gamma_event_id: str | None = None,
                        league: str | None = None, home: str | None = None,
                        away: str | None = None, kickoff_day: str | None = None,
                        asset: str | None = None, expiry_at: datetime | None = None,
                        market_ref: str | None = None) -> str | None:
    """The pure match key of one candidate's underlying event (FX-26, N-1).

    Three shapes (fix-list FX-26): sports events key on the Gamma event
    id, falling back to the canonical ``league|home|away|day`` string
    when no id is available; crypto events key on the asset plus the UTC
    expiry date; every other market keys on the market's own identity.
    Insufficient inputs yield ``None`` -- no key is fabricated, and the
    evaluation loader then keeps the frozen market-key clustering, so a
    missing match key never silently widens or narrows an interval. The
    kickoff day is a caller-normalized string and the expiry a tz-aware
    datetime; this function never parses free text, reads clocks or
    fetches anything.
    """
    normalized = _clean_text(market_class)
    if normalized not in EVENT_KEY_CLASSES:
        _fail('papersim_event_key_market_class_invalid')
    if normalized == 'sports':
        event_id = _clean_text(gamma_event_id)
        if event_id is not None:
            return f'gamma:{event_id}'
        parts = [_clean_text(value) for value in (league, home, away, kickoff_day)]
        if all(part is not None for part in parts):
            return 'match:' + '|'.join(parts)
        return None
    if normalized == 'crypto':
        asset_name = _clean_text(asset)
        if asset_name is None or not isinstance(expiry_at, datetime) \
                or expiry_at.tzinfo is None:
            return None
        return f'crypto:{asset_name}|{expiry_at.astimezone(UTC).date().isoformat()}'
    identity = _clean_text(market_ref)
    if identity is None:
        return None
    return f'market:{identity}'


def validate_legacy_source(is_legacy, legacy_source_id) -> None:
    """legacy_source_id may be non-null only on legacy rows (P3-7)."""
    if type(is_legacy) is not bool:
        _fail('papersim_is_legacy_invalid')
    if (legacy_source_id is not None) and not is_legacy:
        _fail('papersim_legacy_source_invalid')


def validate_settlement_consistency(classification, win_loss) -> None:
    """NATURAL carries win/loss; EXIT and VOIDED_REFUND never do (P3-7)."""
    if classification not in SETTLEMENT_CLASSIFICATIONS:
        _fail('papersim_classification_invalid')
    if classification == 'NATURAL':
        if win_loss not in WIN_LOSS_TOKENS:
            _fail('papersim_settlement_win_loss_invalid')
    elif win_loss is not None:
        _fail('papersim_settlement_win_loss_invalid')


def validate_reversal(reason, reverses_event_id) -> None:
    """A REVERSAL cash event must reference the event it reverses."""
    if reason not in BANKROLL_REASONS:
        _fail('papersim_bankroll_reason_invalid')
    if reason == 'REVERSAL' and reverses_event_id is None:
        _fail('papersim_reversal_reference_missing')


def natural_alignment(side, outcome) -> bool:
    """Mirror the fixed calibration hit rule (09D v2 section 4.3-4).

    YES/YES-basket sides align with an equal natural outcome; NO/NO-basket
    sides align with the natural NO (basket legs resolve on the natural NO).
    """
    if side in ('YES', 'YES-basket'):
        return outcome == side
    if side in ('NO', 'NO-basket'):
        return outcome == 'NO'
    _fail('papersim_side_token_invalid')


def binding_token_status(candidate_yes_token, candidate_no_token,
                         outcome_yes_token, outcome_no_token):
    """Token-family consistency between a candidate and a market outcome.

    Returns True (families overlap), False (market matched but the token
    families are disjoint: ``market_binding_mismatch``) or None (either
    side carries no token identity, so no verdict is possible).
    """
    candidate_tokens = {token for token in (candidate_yes_token, candidate_no_token) if token}
    outcome_tokens = {token for token in (outcome_yes_token, outcome_no_token) if token}
    if not candidate_tokens or not outcome_tokens:
        return None
    return not candidate_tokens.isdisjoint(outcome_tokens)


def validate_evidence_time_order(decision_observed_at, close_observed_at=None,
                                 *, scan_finished_at=None) -> None:
    """Cross-table time-order guards that CHECK constraints cannot express (P3-9)."""
    if close_observed_at is not None and decision_observed_at is not None:
        if _require_timestamp(close_observed_at, 'close_observed_at') < \
                _require_timestamp(decision_observed_at, 'decision_observed_at'):
            _fail('papersim_evidence_time_order_invalid')
    if scan_finished_at is not None:
        if _require_timestamp(decision_observed_at, 'decision_observed_at') > \
                _require_timestamp(scan_finished_at, 'scan_finished_at'):
            _fail('papersim_evidence_time_order_invalid')


@dataclass(frozen=True)
class EffectiveMarketOutcome:
    """The effective outcome of one market, or an explicit UNKNOWN gap."""

    outcome_id: int | None
    outcome: str
    observed_at: datetime | None = None
    source_kind: str | None = None

    @property
    def is_effective(self) -> bool:
        return self.outcome != 'UNKNOWN'


def select_effective_outcome(observations) -> EffectiveMarketOutcome:
    """Latest non-UNKNOWN observation wins; tie-break outcome_id DESC (P1-1 (b)).

    ``observations`` are mappings with ``outcome_id``, ``outcome`` and
    ``observed_at``. When only UNKNOWN observations exist the latest one is
    returned with its UNKNOWN label (coverage gap, not a filled value); no
    observations at all yields an UNKNOWN result with no row attached.
    """
    rows = [(int(item['outcome_id']), item['outcome'], item['observed_at'], item.get('source_kind'))
            for item in observations]
    for _, outcome, _, _ in rows:
        if outcome not in OUTCOME_TOKENS:
            _fail('papersim_outcome_token_invalid')
    pool = [row for row in rows if row[1] != 'UNKNOWN'] or [row for row in rows if row[1] == 'UNKNOWN']
    if not pool:
        return EffectiveMarketOutcome(None, 'UNKNOWN')
    best = max(pool, key=lambda row: (row[2], row[0]))
    return EffectiveMarketOutcome(best[0], best[1], best[2], best[3])


def decision_time_observation(evidence_rows):
    """The DECISION row with the smallest evidence_id (derived rule, P3-8)."""
    decisions = sorted((row for row in evidence_rows if row.get('evidence_role') == 'DECISION'),
                       key=lambda row: row['evidence_id'])
    return decisions[0] if decisions else None


def decision_price_status(decision_row) -> str:
    """'AVAILABLE' or 'INSUFFICIENT' for the decision-time price (09D v2 section 4.5)."""
    if decision_row is None:
        return 'INSUFFICIENT'
    if all(decision_row.get(column) is None for column in ('last', 'mid', 'bid', 'ask')):
        return 'INSUFFICIENT'
    return 'AVAILABLE'


@dataclass(frozen=True)
class MarketOutcomeObservation:
    """One sourced observation of a market-level outcome (append-only)."""

    condition_id: str | None = None
    market_id: str | None = None
    market_slug: str | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None
    outcome: str = 'UNKNOWN'
    outcome_detail: str | None = None
    confirmed_at: datetime | None = None
    observed_at: datetime | None = None
    source_kind: str = 'operator_attestation'
    source_ref: str | None = None
    source_snapshot_sha256: str | None = None

    def validate(self) -> 'MarketOutcomeObservation':
        _optional_text(self.condition_id, 'condition_id')
        _optional_text(self.market_id, 'market_id')
        _optional_text(self.market_slug, 'market_slug')
        if self.condition_id is None and self.market_id is None and self.market_slug is None:
            _fail('papersim_market_identity_missing')
        if self.outcome not in OUTCOME_TOKENS:
            _fail('papersim_outcome_token_invalid')
        if self.source_kind not in OUTCOME_SOURCE_KINDS:
            _fail('papersim_outcome_source_kind_invalid')
        _require_timestamp(self.observed_at, 'observed_at')
        _optional_timestamp(self.confirmed_at, 'confirmed_at')
        if self.outcome == 'UNKNOWN' and self.confirmed_at is not None:
            _fail('papersim_outcome_unknown_confirmed')
        _optional_hash64(self.source_snapshot_sha256, 'source_snapshot_sha256')
        return self

    def evidence_key(self) -> str:
        return market_outcome_evidence_key(self.condition_id, self.market_id,
                                           self.source_kind, self.source_ref, self.observed_at)

    def comparison(self) -> dict:
        return {'condition_id': self.condition_id, 'market_id': self.market_id,
                'market_slug': self.market_slug, 'yes_token_id': self.yes_token_id,
                'no_token_id': self.no_token_id, 'outcome': self.outcome,
                'outcome_detail': self.outcome_detail, 'confirmed_at': self.confirmed_at,
                'observed_at': self.observed_at, 'source_kind': self.source_kind,
                'source_ref': self.source_ref,
                'source_snapshot_sha256': self.source_snapshot_sha256}


@dataclass(frozen=True)
class PriceEvidenceObservation:
    """One (candidate, time, market reference, side) paired price observation."""

    observed_at: datetime | None = None
    evidence_role: str = 'DECISION'
    model_probability: Decimal | float | int | None = None
    market_ref_kind: str = 'market_slug'
    market_ref: str = ''
    token_ref: str | None = None
    side: str = 'YES'
    bid: Decimal | float | int | None = None
    ask: Decimal | float | int | None = None
    mid: Decimal | float | int | None = None
    last: Decimal | float | int | None = None
    book_sha256: str | None = None
    snapshot_sha256: str | None = None
    source_kind: str = 'operator_attestation'
    source_ref: str | None = None
    candidate_id: int | None = None

    def validate(self) -> 'PriceEvidenceObservation':
        if self.evidence_role not in EVIDENCE_ROLES:
            _fail('papersim_evidence_role_invalid')
        if self.market_ref_kind not in MARKET_REF_KINDS:
            _fail('papersim_market_ref_kind_invalid')
        _require_text(self.market_ref, 'market_ref')
        if self.side not in SIDE_TOKENS:
            _fail('papersim_side_token_invalid')
        _require_timestamp(self.observed_at, 'observed_at')
        _optional_probability_closed(self.model_probability, 'model_probability')
        _optional_interval_open(self.bid, 'bid')
        _optional_interval_open(self.ask, 'ask')
        _optional_interval_open(self.mid, 'mid')
        if self.last is not None:
            if self.evidence_role == 'CLOSE':
                _optional_interval_closed(self.last, 'last')
            else:
                _optional_interval_open(self.last, 'last')
        if self.bid is not None and self.ask is not None and _as_decimal(self.bid) > _as_decimal(self.ask):
            _fail('papersim_bid_ask_crossed')
        if (self.mid is not None and self.bid is not None and self.ask is not None
                and not _as_decimal(self.bid) <= _as_decimal(self.mid) <= _as_decimal(self.ask)):
            _fail('papersim_mid_outside_quote')
        if self.evidence_role == 'CLOSE' and all(
                value is None for value in (self.last, self.mid, self.bid, self.ask)):
            _fail('papersim_close_price_missing')
        _optional_hash64(self.book_sha256, 'book_sha256')
        _optional_hash64(self.snapshot_sha256, 'snapshot_sha256')
        _require_text(self.source_kind, 'source_kind')
        if self.source_ref is None and not (self.source_kind == 'operator_attestation'
                                            and self.snapshot_sha256 is not None):
            _fail('papersim_evidence_source_missing')
        if self.candidate_id is not None and type(self.candidate_id) is not int:
            _fail('papersim_candidate_id_invalid')
        return self

    def evidence_key(self) -> str:
        return price_evidence_key(self.candidate_id, self.evidence_role, self.market_ref_kind,
                                  self.market_ref, self.side, self.source_kind, self.observed_at)

    def evidence_key_with(self, candidate_id: int) -> str:
        """Idempotency key bound to the candidate id assigned at insert time."""
        return price_evidence_key(candidate_id, self.evidence_role, self.market_ref_kind,
                                  self.market_ref, self.side, self.source_kind, self.observed_at)


@dataclass(frozen=True)
class EntrySubmission:
    """The single paper entry of one candidate (validated before any write)."""

    stake: Decimal | float | int = 0
    price: Decimal | float | int = 0
    shares: Decimal | float | int = 0
    fee_category: str = ''
    fee_rate: Decimal | float | int = 0
    entry_fee: Decimal | float | int = 0
    fee_source: str = 'schedule'
    tier: str = ''
    entered_at: datetime | None = None
    legacy_source_id: str | None = None
    is_legacy: bool = False

    def validate(self) -> 'EntrySubmission':
        validate_legacy_source(self.is_legacy, self.legacy_source_id)
        stake = _require_number(self.stake, 'stake')
        price = _require_number(self.price, 'price')
        shares = _require_number(self.shares, 'shares')
        if _as_decimal(stake) <= 0:
            _fail('papersim_stake_invalid')
        if not Decimal(0) < _as_decimal(price) < Decimal(1):
            _fail('papersim_price_invalid')
        if _as_decimal(shares) <= 0:
            _fail('papersim_shares_invalid')
        if _as_decimal(_require_number(self.fee_rate, 'fee_rate')) < 0:
            _fail('papersim_fee_rate_invalid')
        if _as_decimal(_require_number(self.entry_fee, 'entry_fee')) < 0:
            _fail('papersim_entry_fee_invalid')
        if self.fee_source not in FEE_SOURCES:
            _fail('papersim_fee_source_invalid')
        if self.fee_source == 'estimated' and not self.is_legacy:
            # estimated fees exist only for the legacy import; v2 entries
            # always walk the reviewed fee schedule (M1 plan FX-08).
            _fail('papersim_fee_source_estimated_requires_legacy')
        _require_text(self.fee_category, 'fee_category')
        _require_text(self.tier, 'tier')
        _require_timestamp(self.entered_at, 'entered_at')
        if abs(_as_decimal(shares) * _as_decimal(price) - _as_decimal(stake)) >= STAKE_SHARE_TOLERANCE:
            _fail('papersim_entry_stake_mismatch')
        return self


@dataclass(frozen=True)
class CandidateSubmission:
    """One scanned candidate: accepted, rejected or construction-failed."""

    candidate_key: str = ''
    conclusion: str = 'REJECTED'
    side: str = 'YES'
    market_kind: str = 'terminal'
    team: str = ''
    model_name: str = ''
    model_version: str = ''
    input_payload: str = '{}'
    gate_report: str = '{}'
    evaluated_at: datetime | None = None
    condition_id: str | None = None
    market_id: str | None = None
    market_slug: str | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None
    market_type: str | None = None
    event_key: str | None = None
    event_start_at: datetime | None = None
    binding_version: int = 1
    median_probability: Decimal | float | int | None = None
    variant_quotes: str | list = '[]'
    snapshot_refs: str | list = '[]'
    rejection_reasons: str | list = '[]'
    order_book: str | None = None
    book_fetched_at: datetime | None = None
    legacy_source_id: str | None = None
    is_legacy: bool = False
    price_evidence: tuple[PriceEvidenceObservation, ...] = ()
    entry: EntrySubmission | None = None

    def validate(self) -> 'CandidateSubmission':
        _require_text(self.candidate_key, 'candidate_key')
        if self.conclusion not in CONCLUSIONS:
            _fail('papersim_conclusion_invalid')
        if self.side not in SIDE_TOKENS:
            _fail('papersim_side_token_invalid')
        if self.market_kind not in MARKET_KINDS:
            _fail('papersim_market_kind_invalid')
        _require_text(self.team, 'team')
        _require_text(self.model_name, 'model_name')
        _require_text(self.model_version, 'model_version')
        if type(self.binding_version) is not int or self.binding_version < 1:
            _fail('papersim_binding_version_invalid')
        _optional_probability_closed(self.median_probability, 'median_probability')
        _require_json_payload(self.input_payload, 'input_payload')
        _require_json_payload(self.gate_report, 'gate_report')
        _require_json_list(self.variant_quotes, 'variant_quotes')
        _require_json_list(self.snapshot_refs, 'snapshot_refs')
        for reference in json.loads(_require_json_list(self.snapshot_refs, 'snapshot_refs')):
            _optional_hash64(reference, 'snapshot_ref')
        _require_json_list(self.rejection_reasons, 'rejection_reasons')
        _require_timestamp(self.evaluated_at, 'evaluated_at')
        if (self.order_book is None) != (self.book_fetched_at is None):
            _fail('papersim_order_book_pairing_invalid')
        if self.order_book is not None:
            _require_json_payload(self.order_book, 'order_book')
            _optional_timestamp(self.book_fetched_at, 'book_fetched_at')
        _optional_text(self.event_key, 'event_key')
        _optional_timestamp(self.event_start_at, 'event_start_at')
        validate_legacy_source(self.is_legacy, self.legacy_source_id)
        for evidence in self.price_evidence:
            evidence.validate()
        if self.entry is not None:
            self.entry.validate()
        return self


@dataclass(frozen=True)
class ScanRunRecord:
    """The scan identity row of one complete scan (accepted+rejected+failed)."""

    team: str = ''
    started_at: datetime | None = None
    finished_at: datetime | None = None
    code_commit: str | None = None
    schema_version: int = 1
    gate_version: str = ''
    operator: str = 'unattended'
    params: str = '{}'
    is_legacy: bool = False
    # B1 (05 section 2): the stable scheduling identity of an unattended
    # scan (papersim.scan_runs.operation_key, unique in the M2 S0
    # migration). Deliberately NOT part of ``key()`` above and NOT in
    # the hand-written ``payload_sha256`` field lists -- the scan
    # identity stays team|started_at|code_commit|gate_version|
    # schema_version|params_hash; this column only prevents one
    # scheduled operation from writing two scans.
    operation_key: str | None = None

    def validate(self) -> 'ScanRunRecord':
        _require_text(self.team, 'team')
        _require_text(self.gate_version, 'gate_version')
        _require_text(self.operator, 'operator')
        if type(self.schema_version) is not int:
            _fail('papersim_schema_version_invalid')
        _require_timestamp(self.started_at, 'started_at')
        _require_timestamp(self.finished_at, 'finished_at')
        if self.started_at > self.finished_at:
            _fail('papersim_scan_time_order_invalid')
        if self.code_commit is not None and not (type(self.code_commit) is str
                                                 and len(self.code_commit) == 40
                                                 and all(c in '0123456789abcdef' for c in self.code_commit)):
            _fail('papersim_code_commit_invalid')
        if not self.is_legacy and self.code_commit is None:
            _fail('papersim_code_commit_required')
        _require_json_payload(self.params, 'params')
        _optional_text(self.operation_key, 'operation_key')
        return self

    def key(self) -> str:
        return scan_key(self.team, self.started_at, self.code_commit, self.gate_version,
                        self.schema_version, self.params)


@dataclass(frozen=True)
class ScanSubmission:
    """One complete scan write: run row, candidates, price evidence and entries."""

    scan: ScanRunRecord
    candidates: tuple[CandidateSubmission, ...] = field(default=())

    def validate(self) -> 'ScanSubmission':
        self.scan.validate()
        keys = set()
        for candidate in self.candidates:
            candidate.validate()
            if candidate.candidate_key in keys:
                _fail('papersim_candidate_key_duplicate')
            keys.add(candidate.candidate_key)
            decisions = [evidence.observed_at for evidence in candidate.price_evidence
                         if evidence.evidence_role == 'DECISION']
            closes = [evidence.observed_at for evidence in candidate.price_evidence
                      if evidence.evidence_role == 'CLOSE']
            if decisions:
                first_decision = min(decisions)
                for close_at in closes:
                    validate_evidence_time_order(first_decision, close_at)
                for decision_at in decisions:
                    validate_evidence_time_order(decision_at, scan_finished_at=self.scan.finished_at)
            if candidate.entry is not None and candidate.conclusion != 'ACCEPTED':
                _fail('papersim_entry_requires_accepted_candidate')
        return self

    def payload_sha256(self) -> str:
        """Canonical digest of the full submission for scan idempotency."""
        document = {'scan': {'team': self.scan.team,
                             'started_at': self.scan.started_at.isoformat(),
                             'finished_at': self.scan.finished_at.isoformat(),
                             'code_commit': self.scan.code_commit,
                             'schema_version': self.scan.schema_version,
                             'gate_version': self.scan.gate_version,
                             'operator': self.scan.operator,
                             'params': self.scan.params,
                             'is_legacy': self.scan.is_legacy},
                    'candidates': [self._candidate_payload(candidate) for candidate in self.candidates]}
        return sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()

    @staticmethod
    def _candidate_payload(candidate: CandidateSubmission) -> dict:
        payload = {'candidate_key': candidate.candidate_key, 'conclusion': candidate.conclusion,
                   'side': candidate.side, 'market_kind': candidate.market_kind, 'team': candidate.team,
                   'model_name': candidate.model_name, 'model_version': candidate.model_version,
                   'input_payload': candidate.input_payload, 'gate_report': candidate.gate_report,
                   'evaluated_at': candidate.evaluated_at.isoformat(),
                   'condition_id': candidate.condition_id, 'market_id': candidate.market_id,
                   'market_slug': candidate.market_slug, 'yes_token_id': candidate.yes_token_id,
                   'no_token_id': candidate.no_token_id, 'market_type': candidate.market_type,
                   'event_key': candidate.event_key,
                   'event_start_at': None if candidate.event_start_at is None
                                     else candidate.event_start_at.isoformat(),
                   'binding_version': candidate.binding_version,
                   'median_probability': None if candidate.median_probability is None
                                         else str(candidate.median_probability),
                   'variant_quotes': candidate.variant_quotes, 'snapshot_refs': candidate.snapshot_refs,
                   'rejection_reasons': candidate.rejection_reasons, 'order_book': candidate.order_book,
                   'book_fetched_at': None if candidate.book_fetched_at is None
                                      else candidate.book_fetched_at.isoformat(),
                   'legacy_source_id': candidate.legacy_source_id, 'is_legacy': candidate.is_legacy,
                   'price_evidence': [{
                       'observed_at': evidence.observed_at.isoformat(),
                       'evidence_role': evidence.evidence_role,
                       'model_probability': None if evidence.model_probability is None
                                            else str(evidence.model_probability),
                       'market_ref_kind': evidence.market_ref_kind, 'market_ref': evidence.market_ref,
                       'token_ref': evidence.token_ref, 'side': evidence.side,
                       'bid': None if evidence.bid is None else str(evidence.bid),
                       'ask': None if evidence.ask is None else str(evidence.ask),
                       'mid': None if evidence.mid is None else str(evidence.mid),
                       'last': None if evidence.last is None else str(evidence.last),
                       'book_sha256': evidence.book_sha256,
                       'snapshot_sha256': evidence.snapshot_sha256,
                       'source_kind': evidence.source_kind, 'source_ref': evidence.source_ref,
                       'candidate_id': evidence.candidate_id}
                       for evidence in candidate.price_evidence],
                   'entry': None if candidate.entry is None else {
                       'stake': str(candidate.entry.stake), 'price': str(candidate.entry.price),
                       'shares': str(candidate.entry.shares), 'fee_category': candidate.entry.fee_category,
                       'fee_rate': str(candidate.entry.fee_rate), 'entry_fee': str(candidate.entry.entry_fee),
                       'fee_source': candidate.entry.fee_source, 'tier': candidate.entry.tier,
                       'entered_at': candidate.entry.entered_at.isoformat(),
                       'legacy_source_id': candidate.entry.legacy_source_id,
                       'is_legacy': candidate.entry.is_legacy}}
        return payload


# ---------------------------------------------------------------------------
# M2 closed-loop operations (S0; M2 plan sections 3.1, 3.2, 3.5, 3.6, 5.3).
# Idempotency is two-layered (M2 plan section 3.2): a stable scheduling
# identity (operation key / mark identity / check identity) and exact
# content comparison. Same key + same content returns the original row;
# same key + changed content is a conflict; nothing is ever updated.
# ---------------------------------------------------------------------------

def _require_commit40(value, name: str) -> str:
    if type(value) is not str or len(value) != 40 \
            or any(c not in '0123456789abcdef' for c in value):
        _fail(f'papersim_{name}_invalid')
    return value


def _require_positive_int(value, name: str) -> int:
    if type(value) is not int or value < 1:
        _fail(f'papersim_{name}_invalid')
    return value


def command_run_event_key(operation_key, attempt, phase) -> str:
    """Writer-side idempotency key for ``papersim.command_run_events``.

    The scheduling identity (operation key + attempt + phase) is stable:
    a retry may never mint a fresh identity from the current clock.
    """
    if phase not in COMMAND_PHASES:
        _fail('papersim_phase_invalid')
    return '|'.join(('cre', _require_text(operation_key, 'operation_key'),
                     str(_require_positive_int(attempt, 'attempt')), phase))


def candidate_mark_key(candidate_id, market_ref_kind, market_ref, side,
                       source_kind, observed_at) -> str:
    """Writer-side idempotency key for ``papersim.candidate_marks`` (4.4 shape)."""
    if type(candidate_id) is not int:
        _fail('papersim_candidate_id_invalid')
    return '|'.join(('cm', str(candidate_id), _require_text(market_ref_kind, 'market_ref_kind'),
                     _require_text(market_ref, 'market_ref'), _require_text(side, 'side'),
                     _require_text(source_kind, 'source_kind'), utc_stamp(observed_at)))


def entry_mark_key(entry_id, source_kind, marked_at) -> str:
    """Writer-side idempotency key for the ``papersim.marks`` row of a capture."""
    if type(entry_id) is not int:
        _fail('papersim_entry_id_invalid')
    return '|'.join(('mk', str(entry_id), _require_text(source_kind, 'source_kind'),
                     utc_stamp(marked_at)))


def queue_control_key(queue_id, control_kind, rule, rule_version, observed_at) -> str:
    """Writer-side idempotency key for ``papersim.queue_control_events``."""
    if control_kind not in QUEUE_CONTROL_KINDS:
        _fail('papersim_control_kind_invalid')
    return '|'.join(('qc', _require_text(queue_id, 'queue_id'), control_kind,
                     _require_text(rule, 'rule'), _require_text(rule_version, 'rule_version'),
                     utc_stamp(observed_at)))


def bankroll_check_key(as_of, code_commit, event_watermark) -> str:
    """Writer-side idempotency key for ``papersim.bankroll_checks``.

    Day (UTC) + code commit + event watermark: a re-run against an
    unchanged ledger returns the original receipt; a later watermark is a
    new appended check, never an overwrite.
    """
    if type(event_watermark) is not int or event_watermark < 0:
        _fail('papersim_event_watermark_invalid')
    day = _require_timestamp(as_of, 'as_of').astimezone(UTC).strftime('%Y%m%d')
    return '|'.join(('bc', day, _require_commit40(code_commit, 'code_commit'),
                     str(event_watermark)))


#: Control-rule names the B1 admission wiring writes (FX-17 lane rules;
#: the latch reader folds by these prefixes, see ``store_psycopg.latched_pauses``).
QUEUE_CONTROL_RULE_FX17 = 'fx17_cross_league'
QUEUE_CONTROL_RULE_BLUEPRINT = 'blueprint_universal'


@dataclass(frozen=True)
class QueueControlSubmission:
    """One ``papersim.queue_control_events`` row (the FX-17 pause latch).

    Rows are append-only evidence (immutable triggers on the table): a
    restart never clears them and only a reviewed RESUME row may follow
    a pause.  B1 writes PAUSE rows only -- a RESUME is a human action
    and therefore always carries ``reviewed_by`` (the table enforces
    it).  Idempotency: ``queue_control_key`` includes ``observed_at``,
    so two scans tripping the same rule append two distinct evidence
    rows by design; the latch reader folds any number of rows per queue.
    """

    queue_id: str = ''
    control_kind: str = ''
    rule: str = ''
    rule_version: str = ''
    metrics: str | dict = '{}'
    evidence_watermark: str | dict = '{}'
    reason: str | None = None
    reviewed_by: str | None = None
    observed_at: datetime | None = None
    scan_id: int | None = None

    def validate(self) -> 'QueueControlSubmission':
        if self.control_kind not in QUEUE_CONTROL_KINDS:
            _fail('papersim_control_kind_invalid')
        _require_text(self.queue_id, 'queue_id')
        _require_text(self.rule, 'rule')
        _require_text(self.rule_version, 'rule_version')
        _require_timestamp(self.observed_at, 'observed_at')
        _require_json_payload(self.metrics, 'metrics')
        _require_json_payload(self.evidence_watermark, 'evidence_watermark')
        if self.control_kind == 'RESUME' and not _clean_control_text(self.reviewed_by):
            _fail('papersim_resume_requires_reviewer')
        if type(self.scan_id) is not int and self.scan_id is not None:
            _fail('papersim_scan_id_invalid')
        return self

    def key(self) -> str:
        return queue_control_key(self.queue_id, self.control_kind, self.rule,
                                 self.rule_version, self.observed_at)


def _clean_control_text(value):
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        _fail('papersim_control_reviewer_invalid')
    return value.strip()


@dataclass(frozen=True)
class CommandRunEventSubmission:
    """One ``papersim.command_run_events`` receipt row (B1 run-scan, FX-03).

    The scheduling identity (operation key + attempt + phase) is stable
    across retries: a retry may never mint a fresh identity from the
    current clock.  ``code_commit`` carries the verified worktree HEAD
    of the running code (the scan refuses to start on a dirty worktree,
    so every receipt row records a real commit).
    """

    operation_key: str = ''
    attempt: int = 1
    phase: str = ''
    command: str = ''
    queue_id: str | None = None
    scheduled_for: datetime | None = None
    occurred_at: datetime | None = None
    code_commit: str | None = None
    config_digest: str | None = None
    input_evidence: str | list = '[]'
    output_evidence: str | list = '[]'
    error_code: str | None = None
    scan_id: int | None = None

    def validate(self) -> 'CommandRunEventSubmission':
        if self.phase not in COMMAND_PHASES:
            _fail('papersim_phase_invalid')
        _require_text(self.operation_key, 'operation_key')
        _require_text(self.command, 'command')
        if type(self.attempt) is not int or self.attempt < 1:
            _fail('papersim_attempt_invalid')
        _require_timestamp(self.occurred_at, 'occurred_at')
        _optional_timestamp(self.scheduled_for, 'scheduled_for')
        _optional_text(self.queue_id, 'queue_id')
        _optional_text(self.error_code, 'error_code')
        if self.code_commit is not None and not (type(self.code_commit) is str
                                                 and len(self.code_commit) == 40
                                                 and all(c in '0123456789abcdef'
                                                         for c in self.code_commit)):
            _fail('papersim_code_commit_invalid')
        if self.config_digest is not None and not (type(self.config_digest) is str
                                                   and len(self.config_digest) == 64
                                                   and all(c in '0123456789abcdef'
                                                           for c in self.config_digest)):
            _fail('papersim_config_digest_invalid')
        _require_json_list(self.input_evidence, 'input_evidence')
        _require_json_list(self.output_evidence, 'output_evidence')
        if type(self.scan_id) is not int and self.scan_id is not None:
            _fail('papersim_scan_id_invalid')
        return self

    def key(self) -> str:
        return command_run_event_key(self.operation_key, self.attempt, self.phase)


def bankroll_identity_difference(cash_total, deployed_total, initial_and_adjustments,
                                 realized_net, open_entry_fees) -> Decimal:
    """C + D - B0 - P_closed_net + F_open_entry (M2 plan section 5.3).

    D is the OPEN PRINCIPAL (stake only); open entry fees enter through
    their own term so settled and open positions reconcile to the same
    base funds within the half-cent tolerance.
    """
    names = ('cash_total', 'deployed_total', 'initial_and_adjustments',
             'realized_net', 'open_entry_fees')
    values = [_as_decimal(_require_number(value, name))
              for value, name in zip((cash_total, deployed_total, initial_and_adjustments,
                                      realized_net, open_entry_fees), names, strict=True)]
    cash, deployed, base, realized, fees = values
    return cash + deployed - base - realized + fees


@dataclass(frozen=True)
class CandidateMarkCapture:
    """One periodic or close mark observation of ANY candidate (M2 B2).

    Marks do not depend on entries: rejected and construction-failed
    candidates keep their periodic marks so scoring stays free of entry
    selection bias. Value columns use the closed interval [0,1] (mark
    semantics, mirroring M1 marks): terminal 0/1 prices are legal states
    while executable quotes stay open-interval in candidate_price_evidence.
    """

    candidate_id: int = 0
    sampling_kind: str = 'PERIODIC'
    observed_at: datetime | None = None
    market_ref_kind: str = 'market_slug'
    market_ref: str = ''
    token_ref: str | None = None
    side: str = 'YES'
    bid: Decimal | float | int | None = None
    ask: Decimal | float | int | None = None
    mid: Decimal | float | int | None = None
    last: Decimal | float | int | None = None
    source_kind: str = 'operator_attestation'
    source_ref: str | None = None
    snapshot_sha256: str | None = None

    def validate(self) -> 'CandidateMarkCapture':
        if type(self.candidate_id) is not int:
            _fail('papersim_candidate_id_invalid')
        if self.sampling_kind not in MARK_SAMPLING_KINDS:
            _fail('papersim_sampling_kind_invalid')
        if self.market_ref_kind not in MARKET_REF_KINDS:
            _fail('papersim_market_ref_kind_invalid')
        _require_text(self.market_ref, 'market_ref')
        _optional_text(self.token_ref, 'token_ref')
        if self.side not in SIDE_TOKENS:
            _fail('papersim_side_token_invalid')
        _require_timestamp(self.observed_at, 'observed_at')
        _optional_interval_closed(self.bid, 'bid')
        _optional_interval_closed(self.ask, 'ask')
        _optional_interval_closed(self.mid, 'mid')
        _optional_interval_closed(self.last, 'last')
        if self.bid is not None and self.ask is not None \
                and _as_decimal(self.bid) > _as_decimal(self.ask):
            _fail('papersim_bid_ask_crossed')
        if all(value is None for value in (self.bid, self.ask, self.mid, self.last)):
            _fail('papersim_mark_price_missing')
        _require_text(self.source_kind, 'source_kind')
        _optional_text(self.source_ref, 'source_ref')
        _optional_hash64(self.snapshot_sha256, 'snapshot_sha256')
        return self

    def mark_key(self) -> str:
        return candidate_mark_key(self.candidate_id, self.market_ref_kind, self.market_ref,
                                  self.side, self.source_kind, self.observed_at)

    def comparison(self) -> dict:
        return {'candidate_id': self.candidate_id, 'sampling_kind': self.sampling_kind,
                'observed_at': self.observed_at, 'market_ref_kind': self.market_ref_kind,
                'market_ref': self.market_ref, 'token_ref': self.token_ref,
                'side': self.side, 'bid': self.bid, 'ask': self.ask, 'mid': self.mid,
                'last': self.last, 'source_kind': self.source_kind,
                'source_ref': self.source_ref, 'snapshot_sha256': self.snapshot_sha256}


@dataclass(frozen=True)
class SettlementRecord:
    """One settlement plus its single cash leg, committed atomically (M2 B3).

    Idempotency is the entry_id uniqueness plus exact content comparison:
    a duplicate settlement of the same entry with different content is a
    conflict and never an update. ``exit_fee`` is the sell-leg fee used
    only for the cash leg (proceeds minus exit fee); ``fee_total`` keeps
    the full settlement fee of the pnl identity (M1 CHECK:
    ``pnl_net = pnl_gross - fee_total``). The entry fee was already paid
    in SCAN_ENTRY and is never charged again here.
    """

    entry_id: int = 0
    classification: str = ''
    outcome_label: str | None = None
    win_loss: str | None = None
    exit_price: Decimal | float | int | None = None
    exit_fee: Decimal | float | int = 0
    proceeds: Decimal | float | int = 0
    pnl_gross: Decimal | float | int = 0
    fee_total: Decimal | float | int = 0
    fee_source: str = 'schedule'
    pnl_net: Decimal | float | int = 0
    source_outcome_id: int | None = None
    exit_mark_id: int | None = None
    evidence: str | None = None
    confirmed_by: str = ''
    confirmed_at: datetime | None = None
    is_legacy: bool = False

    def validate(self) -> 'SettlementRecord':
        if type(self.entry_id) is not int:
            _fail('papersim_entry_id_invalid')
        validate_settlement_consistency(self.classification, self.win_loss)
        _optional_interval_closed(self.exit_price, 'exit_price')
        if self.classification == 'EXIT' and self.exit_price is None:
            _fail('papersim_settlement_exit_price_missing')
        exit_fee = _as_decimal(_require_number(self.exit_fee, 'exit_fee'))
        if exit_fee < 0:
            _fail('papersim_settlement_exit_fee_invalid')
        if self.classification != 'EXIT' and exit_fee != 0:
            _fail('papersim_settlement_exit_fee_invalid')
        proceeds = _as_decimal(_require_number(self.proceeds, 'proceeds'))
        if proceeds < 0:
            _fail('papersim_proceeds_invalid')
        fee_total = _as_decimal(_require_number(self.fee_total, 'fee_total'))
        if fee_total < 0:
            _fail('papersim_fee_total_invalid')
        pnl_gross = _as_decimal(_require_number(self.pnl_gross, 'pnl_gross'))
        pnl_net = _as_decimal(_require_number(self.pnl_net, 'pnl_net'))
        if pnl_net != pnl_gross - fee_total:
            _fail('papersim_settlement_pnl_mismatch')
        if self.fee_source not in FEE_SOURCES:
            _fail('papersim_fee_source_invalid')
        for name, value in (('source_outcome_id', self.source_outcome_id),
                            ('exit_mark_id', self.exit_mark_id)):
            if value is not None and type(value) is not int:
                _fail(f'papersim_{name}_invalid')
        _require_text(self.confirmed_by, 'confirmed_by')
        _require_timestamp(self.confirmed_at, 'confirmed_at')
        return self

    def cash_reason(self) -> str:
        return SETTLEMENT_CASH_REASONS[self.classification]

    def cash_amount(self) -> Decimal:
        amount = _as_decimal(self.proceeds)
        if self.classification == 'EXIT':
            amount -= _as_decimal(self.exit_fee)
        if amount < 0:
            _fail('papersim_settlement_cash_invalid')
        return amount

    def event_key(self) -> str:
        return f'settle:{self.entry_id}'

    def comparison(self) -> dict:
        return {'classification': self.classification, 'outcome_label': self.outcome_label,
                'win_loss': self.win_loss, 'exit_price': self.exit_price,
                'proceeds': self.proceeds, 'pnl_gross': self.pnl_gross,
                'fee_total': self.fee_total, 'fee_source': self.fee_source,
                'pnl_net': self.pnl_net, 'source_outcome_id': self.source_outcome_id,
                'exit_mark_id': self.exit_mark_id, 'evidence': self.evidence,
                'confirmed_by': self.confirmed_by, 'confirmed_at': self.confirmed_at,
                'is_legacy': self.is_legacy}


@dataclass(frozen=True)
class BankrollCheckRequest:
    """Parameters of one daily cash identity check (M2 plan section 5.3).

    The totals themselves are recomputed by the transactional adapter
    inside one read transaction; this record carries only the check
    identity, the half-cent tolerance and the evidence digests.
    """

    as_of: datetime | None = None
    code_commit: str = ''
    tolerance: Decimal | float | int = BANKROLL_TOLERANCE
    queue_id: str | None = None
    report_digest: str | None = None

    def validate(self) -> 'BankrollCheckRequest':
        _require_timestamp(self.as_of, 'as_of')
        _require_commit40(self.code_commit, 'code_commit')
        if _as_decimal(_require_number(self.tolerance, 'tolerance')) < 0:
            _fail('papersim_tolerance_invalid')
        _optional_text(self.queue_id, 'queue_id')
        _optional_hash64(self.report_digest, 'report_digest')
        return self

    def key(self, event_watermark: int) -> str:
        return bankroll_check_key(self.as_of, self.code_commit, event_watermark)


@dataclass(frozen=True)
class BankrollCheckResult:
    """The persisted outcome of one daily cash identity check."""

    check_id: int
    check_key: str
    inserted: bool
    as_of: datetime
    event_watermark: int
    cash_total: Decimal
    deployed_total: Decimal
    initial_and_adjustments: Decimal
    realized_net: Decimal
    open_entry_fees: Decimal
    identity_difference: Decimal
    tolerance: Decimal
    passed: bool


# ---------------------------------------------------------------------------
# MO-2 model registry (docs/handoffs/2026-11-19-audit-optimizer/04
# section 9; 05 node MO-2). Same two-layer idempotency as the M2
# operations block above: a stable writer-side scheduling identity and
# exact content comparison. Same key + same content returns the
# original row; same key + changed content is a conflict; nothing is
# ever updated. Every idempotency-key material below -- including the
# effective_at/decided_at/occurred_at time fields -- comes from the
# submitted decision/event record, NEVER from the writing clock: a
# retry may not mint a fresh identity from now() (the
# command_run_event_key discipline above and the bankroll_check_key
# day-of-as_of precedent).
# ---------------------------------------------------------------------------

#: Optimization-run outcomes (04 section 3: exactly four).
OPTIMIZATION_OUTCOMES = ('CANDIDATE_REGISTERED', 'NO_IMPROVEMENT',
                         'INSUFFICIENT_DATA', 'RUN_FAILED')
#: Promotion-decision classes and conclusions (04 section 9).
DECISION_CLASSES = ('FORWARD', 'ROLLBACK', 'MANUAL')
DECISION_CONCLUSIONS = ('PROMOTE', 'PROMOTE_PENDING_CONFIRM', 'CONTINUE',
                        'RETIRE', 'ROLLBACK', 'HOLD', 'BLOCKED')
#: Role tokens (04 section 3) and model-spec sources (04 section 9).
ROLE_TOKENS = ('ACTIVE', 'BASELINE', 'CANDIDATE', 'GUARD', 'RETIRED')
SPEC_SOURCES = ('frozen_baseline', 'optimizer', 'manual')
#: Roles a shadow prediction may be made under (never the active spec).
SHADOW_ROLES = ('BASELINE', 'CANDIDATE', 'GUARD')
#: Optimizer control kinds and scopes (04 sections 9 and 11).
OPTIMIZER_CONTROL_KINDS = ('FREEZE', 'UNFREEZE')
OPTIMIZER_CONTROL_SCOPES = ('GLOBAL', 'LINEAGE')


def _utc_stamp_field(value, name: str) -> str:
    """utc_stamp with the caller's field name in the error code."""
    return _require_timestamp(value, name).astimezone(UTC).strftime('%Y%m%d%H%M%S')


def normalize_json_number(value):
    """jsonb round-trip normalization (plan review P3-4).

    psycopg may hand back ``Decimal`` for jsonb numbers when a custom
    loads hook is configured; ``ModelSpec`` identity needs int/float.
    The spelling decides, exactly like ``json.loads``: a decimal with a
    fraction point or exponent becomes float (``8.0`` stays a FLOAT,
    never flips to int 8), a plain integer spelling becomes int.
    Containers recurse. Without this, a decimal spelling flip could
    change a rebuilt spec's canonical text (``i:8`` vs ``f:8.0``).
    """

    if isinstance(value, Decimal):
        text = str(value)
        if '.' in text or 'e' in text or 'E' in text:
            return float(value)
        return int(value)
    if isinstance(value, dict):
        return {key: normalize_json_number(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_json_number(item) for item in value]
    return value


def optimization_run_key(family, data_cutoff, rule_version, random_seed,
                         search_space_text) -> str:
    """Writer-side idempotency key for ``papersim.optimization_runs``.

    One run is the identity family + data cutoff + rule version + seed +
    search-space digest. ``data_cutoff`` is the run's FIXED knowledge
    cutoff taken from the submitted run record, never the writing
    clock: a retry may not mint a fresh identity from now().
    """
    if type(random_seed) is not int:
        _fail('papersim_random_seed_invalid')
    space = _require_json_payload(search_space_text, 'search_space')
    return '|'.join(('or', _require_text(family, 'family'),
                     _utc_stamp_field(data_cutoff, 'data_cutoff'),
                     _require_text(rule_version, 'rule_version'),
                     str(random_seed), sha256(space.encode('utf-8')).hexdigest()))


def promotion_decision_key(lineage, decision_class, candidate_spec_sha256,
                           rule_version, look_number) -> str:
    """Writer-side idempotency key for ``papersim.promotion_decisions``.

    The decision identity is lineage + class + candidate + rule version
    + look number -- all fields of the submitted decision record; the
    decision time never enters the key, so a re-submitted identical
    judgement returns the original row instead of a fresh one.
    """
    if decision_class not in DECISION_CLASSES:
        _fail('papersim_decision_class_invalid')
    if type(look_number) is not int or look_number < 1:
        _fail('papersim_look_number_invalid')
    candidate = candidate_spec_sha256 or ''
    if candidate:
        _optional_hash64(candidate, 'candidate_spec_sha256')
    return '|'.join(('pd', _require_text(lineage, 'lineage'), decision_class,
                     candidate, _require_text(rule_version, 'rule_version'),
                     str(look_number)))


def model_role_event_key(lineage, spec_sha256, role, effective_at) -> str:
    """Writer-side idempotency key for ``papersim.model_role_events``.

    One role change is lineage + spec + role + effective_at. The
    effective time is the event's OWN recorded effect time from the
    submitted batch, never the writing clock; the two handover events
    of one promotion share effective_at but differ in spec/role, so a
    replayed handover finds both original rows. Actor and reason are
    content, not identity: same key with changed content is a conflict.
    """
    if role not in ROLE_TOKENS:
        _fail('papersim_role_invalid')
    return '|'.join(('mre', _require_text(lineage, 'lineage'),
                     _optional_hash64(spec_sha256, 'spec_sha256'), role,
                     _utc_stamp_field(effective_at, 'effective_at')))


def shadow_prediction_key(candidate_id, spec_sha256, predicted_at) -> str:
    """Writer-side idempotency key for ``papersim.shadow_predictions``.

    Candidate row + spec + the scan-recorded prediction time (from the
    submitted shadow record, never the writing clock). The writer is
    MO-7; MO-2 pins the identity contract only.
    """
    if type(candidate_id) is not int:
        _fail('papersim_candidate_id_invalid')
    return '|'.join(('sp', str(candidate_id),
                     _optional_hash64(spec_sha256, 'spec_sha256'),
                     _utc_stamp_field(predicted_at, 'predicted_at')))


def optimizer_control_key(scope_target, control_kind, actor, occurred_at) -> str:
    """Writer-side idempotency key for ``papersim.optimizer_control_events``.

    ``scope_target`` is the lineage for a LINEAGE-scoped event or
    ``GLOBAL``. ``occurred_at`` is the event's recorded effect time
    taken from the submitted control record, never the writing clock.
    """
    if control_kind not in OPTIMIZER_CONTROL_KINDS:
        _fail('papersim_optimizer_control_kind_invalid')
    return '|'.join(('oce', _require_text(scope_target, 'scope_target'),
                     control_kind, _require_text(actor, 'actor'),
                     _utc_stamp_field(occurred_at, 'occurred_at')))


@dataclass(frozen=True)
class ModelSpecSubmission:
    """One model-spec row for ``papersim.model_specs`` (MO-2, 04 section 9).

    Pure scalar shape -- deliberately NOT holding a ``ModelSpec`` so
    ``papersim.store`` never imports the optimize subpackage (the
    import-purity contract of ``tests/test_papersim_optimize_spec.py``
    pins what ``import papersim`` loads). The writer-side expansion of
    a ``ModelSpec`` into this shape lives in ``store_psycopg``.
    Identity contract: ``spec_sha256`` MUST equal the SHA-256 of the
    UTF-8 bytes of ``canonical_text`` (the MO-1 canonical text; the
    database re-derives the same digest with
    ``convert_to(canonical_text, 'UTF8')``), ``model_version`` MUST be
    the legacy alias when ``legacy_model_version`` is set and otherwise
    ``procedure_version + '+' + spec_sha256[:12]``.
    """

    spec_sha256: str = ''
    canonical_text: str = ''
    family: str = ''
    procedure_version: str = ''
    model_version: str = ''
    legacy_model_version: str | None = None
    params: str = '{}'
    envelope: str = '[]'
    data_contract: str = ''
    source: str = 'frozen_baseline'
    parent_spec_sha256: str | None = None
    optimization_id: int | None = None
    code_commit: str = ''

    def validate(self) -> 'ModelSpecSubmission':
        _optional_hash64(self.spec_sha256, 'spec_sha256')
        _require_text(self.canonical_text, 'canonical_text')
        if sha256(self.canonical_text.encode('utf-8')).hexdigest() != self.spec_sha256:
            _fail('papersim_spec_canonical_mismatch')
        _require_text(self.family, 'family')
        _require_text(self.procedure_version, 'procedure_version')
        _require_text(self.model_version, 'model_version')
        _optional_text(self.legacy_model_version, 'legacy_model_version')
        _require_json_payload(self.params, 'params')
        _require_json_list(self.envelope, 'envelope')
        _require_text(self.data_contract, 'data_contract')
        if self.source not in SPEC_SOURCES:
            _fail('papersim_spec_source_invalid')
        _optional_hash64(self.parent_spec_sha256, 'parent_spec_sha256')
        if self.optimization_id is not None \
                and (type(self.optimization_id) is not int or self.optimization_id < 1):
            _fail('papersim_optimization_id_invalid')
        _require_commit40(self.code_commit, 'code_commit')
        expected = self.legacy_model_version if self.legacy_model_version is not None \
            else f'{self.procedure_version}+{self.spec_sha256[:12]}'
        if self.model_version != expected:
            _fail('papersim_model_version_inconsistent')
        return self

    def comparison(self) -> dict:
        return {'canonical_text': self.canonical_text, 'family': self.family,
                'procedure_version': self.procedure_version,
                'model_version': self.model_version,
                'legacy_model_version': self.legacy_model_version,
                'params': self.params, 'envelope': self.envelope,
                'data_contract': self.data_contract, 'source': self.source,
                'parent_spec_sha256': self.parent_spec_sha256,
                'optimization_id': self.optimization_id,
                'code_commit': self.code_commit}


@dataclass(frozen=True)
class OptimizationRunSubmission:
    """One optimization run row (MO-2, 04 sections 3 and 9).

    ``active_specs`` maps lineage -> spec sha256 of the ACTIVE spec at
    run time; it stays plain JSON (no FK, the
    candidate_marks.snapshot_sha256 pattern) and the writer proves each
    referenced spec exists inside the same transaction, exactly like
    the record_scan snapshot_refs proof.
    """

    family: str = ''
    data_cutoff: datetime | None = None
    lineages: str | list = '[]'
    dataset_summary: str | dict = '{}'
    snapshot_refs: str | list = '[]'
    search_space: str | dict = '{}'
    rule_version: str = ''
    random_seed: int = 0
    trial_count: int = 0
    budget: str | dict = '{}'
    active_specs: str | dict = '{}'
    outcome: str = 'RUN_FAILED'
    scorecard: str | dict = '{}'
    artifacts_sha256: str | None = None
    code_commit: str = ''

    def validate(self) -> 'OptimizationRunSubmission':
        _require_text(self.family, 'family')
        _require_timestamp(self.data_cutoff, 'data_cutoff')
        lineages = json.loads(_require_json_list(self.lineages, 'lineages'))
        if not lineages or not all(type(name) is str and name for name in lineages):
            _fail('papersim_lineages_invalid')
        _require_json_payload(self.dataset_summary, 'dataset_summary')
        references = json.loads(_require_json_list(self.snapshot_refs, 'snapshot_refs'))
        for reference in references:
            _optional_hash64(reference, 'snapshot_ref')
        _require_json_payload(self.search_space, 'search_space')
        _require_text(self.rule_version, 'rule_version')
        if type(self.random_seed) is not int:
            _fail('papersim_random_seed_invalid')
        if type(self.trial_count) is not int or self.trial_count < 0:
            _fail('papersim_trial_count_invalid')
        _require_json_payload(self.budget, 'budget')
        active = json.loads(_require_json_payload(self.active_specs, 'active_specs'))
        if not isinstance(active, dict) \
                or not all(type(name) is str for name in active) \
                or not all(name in set(lineages) for name in active):
            _fail('papersim_active_specs_invalid')
        for digest in active.values():
            _optional_hash64(digest, 'active_spec_sha256')
        if self.outcome not in OPTIMIZATION_OUTCOMES:
            _fail('papersim_outcome_invalid')
        _require_json_payload(self.scorecard, 'scorecard')
        _optional_hash64(self.artifacts_sha256, 'artifacts_sha256')
        _require_commit40(self.code_commit, 'code_commit')
        return self

    def key(self) -> str:
        return optimization_run_key(self.family, self.data_cutoff, self.rule_version,
                                    self.random_seed, self.search_space)

    def comparison(self) -> dict:
        return {'family': self.family, 'lineages': self.lineages,
                'data_cutoff': self.data_cutoff,
                'dataset_summary': self.dataset_summary,
                'snapshot_refs': self.snapshot_refs,
                'search_space': self.search_space,
                'rule_version': self.rule_version, 'random_seed': self.random_seed,
                'trial_count': self.trial_count, 'budget': self.budget,
                'active_specs': self.active_specs, 'outcome': self.outcome,
                'scorecard': self.scorecard,
                'artifacts_sha256': self.artifacts_sha256,
                'code_commit': self.code_commit}


@dataclass(frozen=True)
class PromotionDecisionSubmission:
    """One promotion-decision row (MO-2, 04 section 9).

    A BLOCKED conclusion must carry its block reason and no other
    conclusion may (mirrored by the database CHECK); ``decided_at`` is
    part of the content, never of the idempotency key.
    """

    lineage: str = ''
    decision_class: str = 'FORWARD'
    candidate_spec_sha256: str | None = None
    active_spec_sha256: str = ''
    rule_version: str = ''
    look_number: int = 1
    evidence_watermark: str | dict = '{}'
    metrics: str | dict = '{}'
    conclusion: str = 'HOLD'
    block_reason: str | None = None
    decided_by: str = ''
    decided_at: datetime | None = None

    def validate(self) -> 'PromotionDecisionSubmission':
        _require_text(self.lineage, 'lineage')
        if self.decision_class not in DECISION_CLASSES:
            _fail('papersim_decision_class_invalid')
        _optional_hash64(self.candidate_spec_sha256, 'candidate_spec_sha256')
        _optional_hash64(self.active_spec_sha256, 'active_spec_sha256')
        _require_text(self.rule_version, 'rule_version')
        if type(self.look_number) is not int or self.look_number < 1:
            _fail('papersim_look_number_invalid')
        _require_json_payload(self.evidence_watermark, 'evidence_watermark')
        _require_json_payload(self.metrics, 'metrics')
        if self.conclusion not in DECISION_CONCLUSIONS:
            _fail('papersim_conclusion_invalid')
        _optional_text(self.block_reason, 'block_reason')
        if (self.conclusion == 'BLOCKED') != (self.block_reason is not None):
            _fail('papersim_block_reason_invalid')
        _require_text(self.decided_by, 'decided_by')
        _require_timestamp(self.decided_at, 'decided_at')
        return self

    def key(self) -> str:
        return promotion_decision_key(self.lineage, self.decision_class,
                                      self.candidate_spec_sha256, self.rule_version,
                                      self.look_number)

    def comparison(self) -> dict:
        return {'lineage': self.lineage, 'decision_class': self.decision_class,
                'candidate_spec_sha256': self.candidate_spec_sha256,
                'active_spec_sha256': self.active_spec_sha256,
                'rule_version': self.rule_version, 'look_number': self.look_number,
                'evidence_watermark': self.evidence_watermark,
                'metrics': self.metrics, 'conclusion': self.conclusion,
                'block_reason': self.block_reason, 'decided_by': self.decided_by,
                'decided_at': self.decided_at}


@dataclass(frozen=True)
class RoleEventSubmission:
    """One role-change event row (MO-2, 04 section 9).

    ``effective_at`` is the event's own recorded effect time (key
    material, never the writing clock). A CANDIDATE registration must
    carry its forward plan (pre-registration, design section 5
    measure 7; mirrored by the database CHECK).
    """

    lineage: str = ''
    spec_sha256: str = ''
    role: str = 'ACTIVE'
    effective_at: datetime | None = None
    reason: str = ''
    forward_plan: str | dict | None = None
    decision_id: int | None = None
    actor: str = ''
    code_commit: str = ''

    def validate(self) -> 'RoleEventSubmission':
        _require_text(self.lineage, 'lineage')
        _optional_hash64(self.spec_sha256, 'spec_sha256')
        if self.role not in ROLE_TOKENS:
            _fail('papersim_role_invalid')
        _require_timestamp(self.effective_at, 'effective_at')
        _require_text(self.reason, 'reason')
        if self.forward_plan is not None:
            _require_json_payload(self.forward_plan, 'forward_plan')
        if self.role == 'CANDIDATE' and self.forward_plan is None:
            _fail('papersim_forward_plan_required')
        if self.decision_id is not None \
                and (type(self.decision_id) is not int or self.decision_id < 1):
            _fail('papersim_decision_id_invalid')
        _require_text(self.actor, 'actor')
        _require_commit40(self.code_commit, 'code_commit')
        return self

    def key(self) -> str:
        return model_role_event_key(self.lineage, self.spec_sha256, self.role,
                                    self.effective_at)

    def comparison(self) -> dict:
        return {'lineage': self.lineage, 'spec_sha256': self.spec_sha256,
                'role': self.role, 'effective_at': self.effective_at,
                'reason': self.reason,
                'forward_plan': None if self.forward_plan is None
                                else _require_json_payload(self.forward_plan, 'forward_plan'),
                'decision_id': self.decision_id, 'actor': self.actor,
                'code_commit': self.code_commit}


@dataclass(frozen=True)
class RoleEventBatch:
    """One transaction's role events of ONE lineage (MO-2, 05 node MO-2).

    The batch IS the transaction unit the pure registry validates: a
    promotion handover is exactly [old active -> GUARD, candidate ->
    ACTIVE] in one batch (plus RETIRED of a previous guard first when
    present). The writer applies the whole batch in one database
    transaction after the prior-state check.
    """

    events: tuple[RoleEventSubmission, ...] = ()

    def validate(self) -> 'RoleEventBatch':
        if not self.events:
            _fail('papersim_role_event_batch_empty')
        lineages = {event.lineage for event in self.events}
        if len(lineages) != 1:
            _fail('papersim_role_event_batch_lineage_invalid')
        stamps = {event.effective_at for event in self.events}
        if len(stamps) != 1:
            _fail('papersim_role_event_batch_time_invalid')
        for event in self.events:
            event.validate()
        return self


@dataclass(frozen=True)
class ShadowPredictionSubmission:
    """One shadow-prediction row (MO-2 identity contract; writer is MO-7).

    Hangs on the ACTIVE candidate row of the same market and scan;
    ``probability`` is closed-interval [0,1] like a mark, the
    hypothetical fill price stays open-interval like a quote.
    """

    candidate_id: int = 0
    spec_sha256: str = ''
    role_at_prediction: str = 'BASELINE'
    probability: Decimal | float | int | None = None
    variant_quotes: str | list = '[]'
    refusal_reason: str | None = None
    gate_pass: bool = False
    gate_reason: str | None = None
    hypothetical_fill_price: Decimal | float | int | None = None
    predicted_at: datetime | None = None

    def validate(self) -> 'ShadowPredictionSubmission':
        if type(self.candidate_id) is not int:
            _fail('papersim_candidate_id_invalid')
        _optional_hash64(self.spec_sha256, 'spec_sha256')
        if self.role_at_prediction not in SHADOW_ROLES:
            _fail('papersim_shadow_role_invalid')
        _optional_probability_closed(self.probability, 'probability')
        _require_json_list(self.variant_quotes, 'variant_quotes')
        _optional_text(self.refusal_reason, 'refusal_reason')
        if type(self.gate_pass) is not bool:
            _fail('papersim_gate_pass_invalid')
        _optional_text(self.gate_reason, 'gate_reason')
        _optional_interval_open(self.hypothetical_fill_price,
                                'hypothetical_fill_price')
        if self.refusal_reason is not None and self.probability is not None:
            _fail('papersim_shadow_refusal_conflict')
        _require_timestamp(self.predicted_at, 'predicted_at')
        return self

    def key(self) -> str:
        return shadow_prediction_key(self.candidate_id, self.spec_sha256,
                                     self.predicted_at)

    def comparison(self) -> dict:
        return {'candidate_id': self.candidate_id, 'spec_sha256': self.spec_sha256,
                'role_at_prediction': self.role_at_prediction,
                'probability': self.probability, 'variant_quotes': self.variant_quotes,
                'refusal_reason': self.refusal_reason, 'gate_pass': self.gate_pass,
                'gate_reason': self.gate_reason,
                'hypothetical_fill_price': self.hypothetical_fill_price,
                'predicted_at': self.predicted_at}


@dataclass(frozen=True)
class OptimizerControlEventSubmission:
    """One optimizer freeze/unfreeze row (MO-2, 04 sections 9 and 11).

    Scope is GLOBAL or one lineage. An UNFREEZE can never be decided by
    an automatic rule -- checked here (application layer), checked in
    the pure registry and enforced again by the database CHECK; three
    layers, one rule. ``occurred_at`` is key material from the record,
    never the writing clock.
    """

    scope: str = 'GLOBAL'
    lineage: str | None = None
    control_kind: str = 'FREEZE'
    reason: str = ''
    actor: str = ''
    occurred_at: datetime | None = None

    def validate(self) -> 'OptimizerControlEventSubmission':
        if self.scope not in OPTIMIZER_CONTROL_SCOPES:
            _fail('papersim_control_scope_invalid')
        if (self.scope == 'LINEAGE') != (self.lineage is not None):
            _fail('papersim_control_scope_lineage_invalid')
        if self.lineage is not None:
            _require_text(self.lineage, 'lineage')
        if self.control_kind not in OPTIMIZER_CONTROL_KINDS:
            _fail('papersim_optimizer_control_kind_invalid')
        _require_text(self.reason, 'reason')
        _require_text(self.actor, 'actor')
        if self.control_kind == 'UNFREEZE' and self.actor.startswith('auto:'):
            _fail('papersim_unfreeze_actor_invalid')
        _require_timestamp(self.occurred_at, 'occurred_at')
        return self

    def key(self) -> str:
        return optimizer_control_key(self.lineage if self.scope == 'LINEAGE'
                                     else 'GLOBAL', self.control_kind, self.actor,
                                     self.occurred_at)

    def comparison(self) -> dict:
        return {'scope': self.scope, 'lineage': self.lineage,
                'control_kind': self.control_kind, 'reason': self.reason,
                'actor': self.actor, 'occurred_at': self.occurred_at}
