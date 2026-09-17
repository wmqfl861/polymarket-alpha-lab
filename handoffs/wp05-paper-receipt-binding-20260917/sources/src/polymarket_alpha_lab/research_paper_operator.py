"""Existing task-command access to immutable simulation receipts.

Canonical reviewed stdin plus its explicit digest; no file queue, new simulator,
provider, timestamp generation, source authentication or implicit write approval.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_paper_capture import StoredResearchPaper
from polymarket_alpha_lab.research_paper_capture_codec import (
    MAX_PAYLOAD_BYTES, checksum, decode_paper_scenario, encode_paper_scenario,
)
from polymarket_alpha_lab.research_resolution import digest
from polymarket_alpha_lab.team_research_agent_types import identifier


def read_scenario(stream, *, record_id: str, input_sha256: str):
    """Read at most payload cap + CRLF + one overflow byte, through EOF.

    Accept zero or ONE trailing LF/CRLF as transport framing. Never strip other
    whitespace, change a timestamp or compute a substitute approval digest.
    """
    identifier('record_id', record_id)
    digest(input_sha256)
    maximum = MAX_PAYLOAD_BYTES + 2
    buffer, size = bytearray(), 0
    while True:
        limit = min(65536, maximum + 1 - size)
        part = stream.read(limit)
        if type(part) is not bytes or len(part) > limit:
            raise ValueError('paper_operator_input_invalid')
        if not part:
            break
        buffer.extend(part)
        size += len(part)
        if size > maximum:
            raise ValueError('paper_operator_input_limit')
    raw = bytes(buffer)
    if raw.endswith(b'\r\n'):
        raw = raw[:-2]
    elif raw.endswith(b'\n'):
        raw = raw[:-1]
    scenario = decode_paper_scenario(raw.decode('utf-8'), input_sha256)
    if scenario.record_id != record_id:
        raise ValueError('paper_operator_record_mismatch')
    return scenario


def _receipt(value, *, record_id, input_sha256=None):
    if type(value) is not StoredResearchPaper:
        raise ValueError('paper_operator_receipt_invalid')
    value = replace(value)
    if (value.scenario.record_id != record_id
            or (input_sha256 is not None
                and checksum(encode_paper_scenario(value.scenario)) != input_sha256)):
        raise ValueError('paper_operator_receipt_mismatch')
    # Bind to the reviewed digest, not the mutable object passed to a collaborator.
    # Storage has already checked original provenance and recomputed its result.
    # This presenter checks request identity, not a second proof of database truth.
    return value.to_dict()


def _emit(envelope, code):
    try:
        rendered = json.dumps(envelope, ensure_ascii=True, allow_nan=False, indent=2) + '\n'
        if sys.stdout.write(rendered) != len(rendered):
            return 1
        sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    except (Exception, SystemExit):
        # A broken stream cannot reliably receive a second error envelope.
        return 1
    return code


def operate_paper(*, root: Path, operation: str, record_id: str,
                  input_sha256: str | None = None, allow_paper_write: bool = False,
                  stream=None) -> int:
    """One explicit capture or original lookup; output follows managed cleanup.

    Exit0 is a returned stored receipt, including a saved rejection or exact
    replay, NOT an accepted trade. Operation/cleanup/output failures may follow
    a committed write. The operator must inspect and explicitly replay unchanged
    input; this console never retries or retains a file-backed recovery object.
    """
    known_operation = type(operation) is str and operation in ('capture-paper', 'inspect-paper')
    envelope = dict(operation=operation if known_operation else None, result=None, operation_entered=False,
        business_writes_possible=False, model_calls_possible=False, public_network_called=False,
        automatic_retry_permitted=False, final_database_state_checked=False,
        paper_only=True, report_only=True, readonly=True)
    try:
        identifier('record_id', record_id)
        if not known_operation:
            raise ValueError
        if operation == 'inspect-paper' and (input_sha256 is not None or allow_paper_write is not False):
            raise ValueError
    except (Exception, SystemExit):
        return _emit(dict(envelope, status='invalid_input', reason_code='paper_operator_arguments_invalid'), 2)
    capturing = operation == 'capture-paper'
    if capturing and allow_paper_write is not True:
        return _emit(dict(envelope, status='blocked', reason_code='paper_operator_write_opt_in_required'), 2)
    scenario = None
    if capturing:
        try:
            source = sys.stdin.buffer if stream is None else stream
            scenario = read_scenario(source, record_id=record_id, input_sha256=input_sha256)
        except KeyboardInterrupt:
            return _emit(dict(envelope, status='interrupted', reason_code='paper_operator_input_interrupted'), 130)
        except (Exception, SystemExit):
            return _emit(dict(envelope, status='invalid_input', reason_code='paper_operator_input_invalid'), 2)
    envelope.update(operation_entered=True, business_writes_possible=capturing)
    try:
        with ProjectPostgres(root).session() as session:
            value = (session.capture_paper_research(scenario=scenario, allow_paper_write=True) if capturing
                     else session.inspect_paper_research(record_id=record_id))
            if value is None and not capturing:
                result, code, status = None, 3, 'not_found'
            else:
                result = _receipt(value, record_id=record_id, input_sha256=input_sha256)
                code, status = 0, 'paper_receipt_returned'
        envelope.update(status=status, result=result)
    except KeyboardInterrupt:
        envelope.update(status='interrupted', reason_code='paper_operator_interrupted')
        code = 130
    except (Exception, SystemExit):
        envelope.update(status='failed', reason_code='paper_operator_operation_failed')
        code = 1
    return _emit(envelope, code)


__all__ = ('operate_paper',)
