"""Explicit bounded stdin for a manually reviewed settlement, never a file queue."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import sys
from pathlib import Path

from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_resolution import IndependentResolutionConfirmation
from polymarket_alpha_lab.research_resolution_confirmation import CryptoSettlementReview, SCHEMA, copy_review
from polymarket_alpha_lab.research_resolution_inspection_cli import resolution_review_summary
from polymarket_alpha_lab.team_research_agent_types import strict_json

MAX_INPUT_BYTES = 65536
_FIELDS = {'review_id', 'record_id', 'request_sha256', 'candidate_review_id',
    'candidate_payload_sha256', 'confirmation', 'source_venue', 'source_pair',
    'source_interval', 'source_price_field', 'source_candle_open_at'}
_PROOF_FIELDS = {'condition_id', 'market_slug', 'actual_yes', 'resolved_at', 'confirmed_at',
    'gamma_content_sha256', 'reviewer_id', 'source_reference', 'source_text', 'independently_verified'}


def decode_review(raw: bytes) -> CryptoSettlementReview:
    """Closed input shape. No timestamps, YES/NO or independent approval defaults."""
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_INPUT_BYTES:
            raise ValueError
        value = strict_json(raw.decode('utf-8'))
        if type(value) is not dict or set(value) != _FIELDS:
            raise ValueError
        proof = value['confirmation']
        if type(proof) is not dict or set(proof) != _PROOF_FIELDS:
            raise ValueError
        proof = dict(proof)
        for name in ('resolved_at', 'confirmed_at'):
            proof[name] = datetime.fromisoformat(proof[name])
        value['source_candle_open_at'] = datetime.fromisoformat(value['source_candle_open_at'])
        value['confirmation'] = IndependentResolutionConfirmation(**proof)
        return CryptoSettlementReview(**value)
    except Exception:
        raise ValueError('settlement_input_invalid') from None


def _emit(envelope, code):
    """Publish one resolution result after cleanup; never retry a broken stream.

    A completed write/flush does not prove the receiver consumed the JSON. A
    partial write can leave a prefix on stdout, so no second envelope is sent.
    """
    try:
        rendered = json.dumps(envelope, ensure_ascii=True, allow_nan=False, indent=2) + '\n'
        written = sys.stdout.write(rendered)
        if type(written) is not int or written != len(rendered):
            return 1
        sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    except (Exception, SystemExit):
        return 1
    return code


def confirm_from_stdin(*, root: Path, stream=None, allow_resolution_write: bool = False) -> int:
    """Consume one <=64KiB UTF8 input, write once, emit only after cleanup.

    The caller must keep original input for explicit uncertain-COMMIT replay.
    No raw evidence, source URL, exception text or operator ID is printed.
    """
    envelope = dict(operation='confirm_crypto_resolution', result=None,
        public_network_called=False, live_model_called=False,
        business_writes_possible=False, source_authentication_performed=False,
        automatic_retry_permitted=False, paper_only=True, report_only=True, readonly=True)
    if allow_resolution_write is not True:
        envelope.update(status='blocked', reason_code='settlement_write_opt_in_required')
        code = 2
    else:
        try:
            source_stream = sys.stdin.buffer if stream is None else stream
            instruction = decode_review(source_stream.read(MAX_INPUT_BYTES + 1))
        except KeyboardInterrupt:
            envelope.update(status='interrupted', reason_code='settlement_input_interrupted')
            code = 130
        except (Exception, SystemExit):
            envelope.update(status='invalid_input', reason_code='settlement_input_invalid')
            code = 2
        else:
            envelope['business_writes_possible'] = True
            try:
                with ProjectPostgres(root).session() as session:
                    # Keep the approved input private; an adapter owns only its
                    # detached argument, including the nested confirmation.
                    receipt = session.confirm_crypto_resolution(instruction=copy_review(instruction),
                                                                  allow_resolution_write=True)
                    result = resolution_review_summary(receipt, review_id=instruction.review_id)
                    if result['linked_outcome'] is None:
                        raise ValueError('settlement_receipt_invalid')
                    proof = receipt.submission.confirmation
                    source = strict_json(proof.source_text)
                    expected = dict(schema_version=SCHEMA, record_id=instruction.record_id,
                        request_sha256=instruction.request_sha256,
                        candidate_review_id=instruction.candidate_review_id,
                        candidate_payload_sha256=instruction.candidate_payload_sha256,
                        source_venue=instruction.source_venue, source_pair=instruction.source_pair,
                        source_interval=instruction.source_interval, source_price_field=instruction.source_price_field,
                        source_candle_open_at=instruction.source_candle_open_at.isoformat(),
                        source_text=instruction.confirmation.source_text,
                        original_source_content_sha256=instruction.confirmation.source_content_sha256)
                    if (type(source) is not dict or any(source.get(k) != v for k,v in expected.items())
                            or source.get('source_authentication_performed') is not False
                            or source.get('independently_verified_assertion') is not True
                            or replace(proof, source_text=instruction.confirmation.source_text) != instruction.confirmation):
                        raise ValueError('settlement_receipt_mismatch')
                envelope.update(status='recorded_operator_confirmation', result=result)
                code = 0
            except KeyboardInterrupt:
                envelope.update(status='interrupted', reason_code='settlement_operation_interrupted')
                code = 130
            except (Exception, SystemExit):
                envelope.update(status='failed', reason_code='settlement_operation_failed')
                code = 1
    return _emit(envelope, code)


__all__ = ('decode_review', 'confirm_from_stdin')
