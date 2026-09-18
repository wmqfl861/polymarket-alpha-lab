"""Bind a human settlement attestation to an original BTC/ETH forecast.

Compose existing immutable lookup, rule/time checks and atomic review storage.
No fetching, pricing inference, source authentication, model or new persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re

from polymarket_alpha_lab.research_crypto_contract_scope import _fold, _YES, _OTHERWISE
from polymarket_alpha_lab.research_crypto_observation import assess_crypto_observation_time
from polymarket_alpha_lab.research_execution import CapturedResearchExecution
from polymarket_alpha_lab.research_execution_psycopg import inspect_captured_research_with_psycopg
from polymarket_alpha_lab.research_resolution import (
    IndependentResolutionConfirmation, ResolutionSubmission, assess_resolution, digest, utc,
)
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
from polymarket_alpha_lab.research_resolution_store import (
    StoredResolutionReview, load_resolution_review_with_psycopg, record_resolution_review_with_psycopg,
)
from polymarket_alpha_lab.team_research_agent_types import hard_flags, identifier, strict_json, text
from polymarket_alpha_lab.team_research_intake import _timestamp

SCHEMA = 'crypto-settlement-attestation-v1'


@dataclass(frozen=True, slots=True)
class CryptoSettlementReview:
    """Explicit operator assertions; labels and hashes do not prove source truth.

    The source descriptor must match the supported original rule's venue, pair,
    minute and Close field. The operator still independently verifies the actual
    price, outcome and complete rules. These assertions are retained, not inferred.
    """
    review_id: str
    record_id: str
    request_sha256: str
    candidate_review_id: str
    candidate_payload_sha256: str
    confirmation: IndependentResolutionConfirmation = field(repr=False)
    source_venue: str
    source_pair: str
    source_interval: str
    source_price_field: str
    source_candle_open_at: datetime
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self):
        hard_flags(self)
        for name in ('review_id', 'record_id', 'candidate_review_id'):
            identifier(name, getattr(self, name))
        if self.review_id == self.candidate_review_id:
            raise ValueError('settlement_new_review_id_required')
        digest(self.request_sha256)
        digest(self.candidate_payload_sha256)
        if type(self.confirmation) is not IndependentResolutionConfirmation:
            raise ValueError('settlement_confirmation_required')
        object.__setattr__(self, 'confirmation', replace(self.confirmation))
        # Leave room for the binding envelope inside the original 32K text cap.
        text('source_text', self.confirmation.source_text, 16000)
        for name in ('source_venue', 'source_pair', 'source_interval', 'source_price_field'):
            text(name, getattr(self, name), 32)
        object.__setattr__(self, 'source_candle_open_at', utc('source_candle_open_at', self.source_candle_open_at))


def copy_review(value):
    if type(value) is not CryptoSettlementReview:
        raise ValueError('settlement_review_invalid')
    return replace(value)


def build_crypto_resolution_confirmation(*, instruction: CryptoSettlementReview,
        execution: CapturedResearchExecution, candidate: StoredResolutionReview) -> ResolutionSubmission:
    """Pure assembly from detached receipts, not proof of a database lookup.

    Reject before promotion if original prediction, terms, source descriptor or
    timestamps disagree. Persist provenance INSIDE the existing confirmation's
    source_text; its hash covers this envelope, not only the original source text.
    """
    item = copy_review(instruction)
    if type(execution) is not CapturedResearchExecution or type(candidate) is not StoredResolutionReview:
        raise ValueError('settlement_original_records_required')
    execution = replace(execution)
    candidate = replace(candidate, outcome=None if candidate.outcome is None else replace(candidate.outcome))
    request, record = execution.request, execution.record
    if (request.record_id != item.record_id or request.content_sha256 != item.request_sha256
            or candidate.submission.review_id != item.candidate_review_id
            or sha256(encode_resolution(candidate.submission).encode()).hexdigest() != item.candidate_payload_sha256):
        raise ValueError('settlement_original_binding_mismatch')
    intake = request.intake
    if (intake.team_id not in ('crypto_btc', 'crypto_eth') or record is None
            or record.run.research is None or record.run.research.status != 'completed'):
        raise ValueError('settlement_completed_crypto_forecast_required')
    original = candidate.submission
    if (original.confirmation is not None or candidate.outcome is not None
            or assess_resolution(original).status != 'needs_confirmation'):
        raise ValueError('settlement_unconfirmed_candidate_required')
    if (intake.condition_id != original.condition_id or intake.market_slug != original.snapshot.market_slug):
        raise ValueError('settlement_market_mismatch')
    task = intake.task
    data = strict_json(original.snapshot.raw_json.decode('utf-8'))
    if (data.get('question'), data.get('description')) != (task.question, task.resolution_criteria):
        raise ValueError('settlement_original_terms_changed')
    observation = assess_crypto_observation_time(team_id=intake.team_id, question=task.question,
        resolution_criteria=task.resolution_criteria, market_slug=intake.market_slug,
        as_of=intake.as_of, forecast_cutoff_at=request.forecast_cutoff_at,
        scheduled_end_at=_timestamp(data.get('endDate')))
    if not observation.new_launch_time_eligible or not record.recorded_at < request.forecast_cutoff_at:
        raise ValueError('settlement_forecast_not_prospective')
    # Narrow positive source hint in the already-supported Yes clause. This does
    # not certify arbitrary prose or make the original scope parser an oracle.
    rules = _fold(task.resolution_criteria)
    clause = rules[_YES.search(rules).end():_OTHERWISE.search(rules).start()]
    ticker = 'btc' if intake.team_id == 'crypto_btc' else 'eth'
    if (len(re.findall(r'\bbinance\b', clause)) != 1
            or re.search(r'\bbinance\s+' + ticker + r'/?usdt\b', clause) is None
            or (item.source_venue, item.source_pair, item.source_interval, item.source_price_field)
                != ('binance', ticker.upper() + 'USDT', '1m', 'close')
            or item.source_candle_open_at != observation.candle_open_at):
        raise ValueError('settlement_source_descriptor_mismatch')
    proof = item.confirmation
    if (proof.resolved_at < observation.candle_open_at + timedelta(minutes=1)
            or proof.confirmed_at < candidate.recorded_at):
        raise ValueError('settlement_confirmation_time_mismatch')
    # The original public text remains verbatim as a nested string, with its own
    # hash. No schema migration, mutable candidate or second outcome table.
    source = dict(schema_version=SCHEMA, record_id=item.record_id,
        request_sha256=item.request_sha256, record_sha256=record.content_sha256,
        candidate_review_id=item.candidate_review_id, candidate_payload_sha256=item.candidate_payload_sha256,
        original_market_content_sha256=intake.market_content_sha256,
        source_venue=item.source_venue, source_pair=item.source_pair,
        source_interval=item.source_interval, source_price_field=item.source_price_field,
        source_candle_open_at=item.source_candle_open_at.isoformat(),
        observation=observation.to_dict(), source_text=proof.source_text,
        original_source_content_sha256=proof.source_content_sha256,
        independently_verified_assertion=True, source_authentication_performed=False)
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    submission = ResolutionSubmission(item.review_id, original.condition_id, original.snapshot,
        proof.confirmed_at, replace(proof, source_text=encoded))
    if assess_resolution(submission).status != 'ready':
        raise ValueError('settlement_confirmation_not_ready')
    return submission


def confirm_crypto_resolution_with_psycopg(dsn: str, *, instruction: CryptoSettlementReview,
        allow_resolution_write: bool = False) -> StoredResolutionReview:
    """Read immutable originals, then reuse the atomic review/outcome writer.

    Reads are separate snapshots, not a whole-project transaction. Both originals
    are immutable; the existing writer serializes the final condition/outcome
    conflict. Exact instruction replay rebuilds identical bytes/times and uses
    the original writer's idempotency, including after expiry. Never retry here.
    """
    if allow_resolution_write is not True:
        raise ValueError('settlement_write_opt_in_required')
    item = copy_review(instruction)
    execution = inspect_captured_research_with_psycopg(dsn, record_id=item.record_id)
    candidate = load_resolution_review_with_psycopg(dsn, review_id=item.candidate_review_id)
    submission = build_crypto_resolution_confirmation(instruction=item, execution=execution, candidate=candidate)
    # Bind BEFORE handing the mutable-in-practice dataclass to the adapter.
    # A changed argument is not a replacement for the approved canonical bytes.
    expected_payload = encode_resolution(submission)
    receipt = record_resolution_review_with_psycopg(dsn, submission=submission)
    if type(receipt) is not StoredResolutionReview:
        raise ValueError('settlement_receipt_invalid')
    receipt = replace(receipt, outcome=None if receipt.outcome is None else replace(receipt.outcome))
    if encode_resolution(receipt.submission) != expected_payload or receipt.outcome is None:
        raise ValueError('settlement_receipt_mismatch')
    return receipt


__all__ = ('CryptoSettlementReview', 'build_crypto_resolution_confirmation',
           'confirm_crypto_resolution_with_psycopg')
