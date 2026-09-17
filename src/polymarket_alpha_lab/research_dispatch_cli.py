"""Operate existing durable research tasks; never discover clients or credentials.

Admit one reviewed batch/allowance, inspect existing state, or explicitly run one
budgeted turn through an application's supplied client. No file queue, scheduler,
migration, provider discovery or retry.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import sys
from pathlib import Path

from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_dispatch import (
    MAX_BATCH_BYTES, ResearchBatchSnapshot, StoredResearchBatch, decode_batch,
)
from polymarket_alpha_lab.research_dispatch_rotation import StoredResearchRotationTurn, batch_ids
from polymarket_alpha_lab.research_dispatch_rotation_runner import ResearchRotationReport
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from polymarket_alpha_lab.research_model_budget import (
    MAX_POLICY_BYTES, ModelBudgetSnapshot, StoredModelBudget, decode_budget,
)
from polymarket_alpha_lab.research_resolution import digest
from polymarket_alpha_lab.research_resolution_confirmation_cli import _emit
from polymarket_alpha_lab.team_research_agent_types import identifier


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown arguments can contain a mistakenly pasted credential. Do not
        # echo them (or a type conversion's raw input) in an error or traceback.
        self.exit(2, 'research_dispatch_arguments_invalid; use --help\n')


def _identifier(value):
    try:
        identifier('identifier', value)
    except ValueError:
        raise argparse.ArgumentTypeError('invalid project identifier') from None
    return value


def _parser(default_root):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--root', type=Path, default=default_root)
    commands = parser.add_subparsers(dest='operation', required=True)
    batch = commands.add_parser('inspect-batch', allow_abbrev=False)
    batch.add_argument('--batch-id', type=_identifier, required=True)
    turn = commands.add_parser('inspect-turn', allow_abbrev=False)
    turn.add_argument('--rotation-id', type=_identifier, required=True)
    turn.add_argument('--turn-id', type=_identifier, required=True)
    budget = commands.add_parser('inspect-budget', allow_abbrev=False)
    budget.add_argument('--budget-id', type=_identifier, required=True)
    run = commands.add_parser('run-turn', allow_abbrev=False,
        description='One explicit turn; an approved application must supply model_factory. '
                    'The standalone script never loads a model or credential.')
    run.add_argument('--rotation-id', type=_identifier, required=True)
    run.add_argument('--turn-id', type=_identifier, required=True)
    run.add_argument('--batch-id', type=_identifier, action='append', required=True)
    run.add_argument('--budget-id', type=_identifier, required=True)
    run.add_argument('--max-tasks', type=int, default=10)
    run.add_argument('--max-workers', type=int, default=2)
    run.add_argument('--allow-model-calls', action='store_true')
    paper = commands.add_parser('inspect-paper', allow_abbrev=False,
        help='read one original stored simulation receipt')
    paper.add_argument('--record-id', type=_identifier, required=True)
    capture = commands.add_parser('capture-paper', allow_abbrev=False,
        help='save one reviewed canonical stdin simulation, never a real trade')
    capture.add_argument('--record-id', type=_identifier, required=True)
    capture.add_argument('--input-sha256', required=True)
    capture.add_argument('--allow-paper-write', action='store_true')
    for operation, key, permission in (
            ('enqueue-batch', 'batch-id', 'allow-queue-write'),
            ('create-budget', 'budget-id', 'allow-budget-write')):
        admission = commands.add_parser(operation, allow_abbrev=False,
            help='store one reviewed canonical stdin value; never run research')
        admission.add_argument('--' + key, type=_identifier, required=True)
        admission.add_argument('--input-sha256', required=True)
        admission.add_argument('--' + permission, action='store_true')
    return parser


def _inspect(session, args):
    if args.operation == 'inspect-batch':
        value = session.inspect_research_batch(batch_id=args.batch_id)
        expected = ResearchBatchSnapshot
    elif args.operation == 'inspect-turn':
        value = session.inspect_research_turn(rotation_id=args.rotation_id, turn_id=args.turn_id)
        expected = StoredResearchRotationTurn
    else:
        value = session.inspect_model_budget(budget_id=args.budget_id)
        expected = ModelBudgetSnapshot
    if value is None:
        return None
    if type(value) is not expected:
        raise ValueError('research_dispatch_receipt_invalid')
    value = replace(value)
    if args.operation == 'inspect-batch':
        matches = value.stored.batch.batch_id == args.batch_id
    elif args.operation == 'inspect-turn':
        matches = (value.turn.rotation_id, value.turn.turn_id) == (args.rotation_id, args.turn_id)
    else:
        matches = value.stored.policy.budget_id == args.budget_id
    if not matches:
        raise ValueError('research_dispatch_receipt_mismatch')
    return value.to_dict()


def _run(session, args, model_factory, stop):
    value = session.run_research_rotation(rotation_id=args.rotation_id, turn_id=args.turn_id,
        batch_ids_to_run=tuple(args.batch_id), model_factory=model_factory,
        allow_model_calls=True, max_tasks=args.max_tasks, max_workers=args.max_workers,
        stop=stop, model_budget_id=args.budget_id)
    if type(value) is not ResearchRotationReport:
        raise ValueError('research_dispatch_receipt_invalid')
    value = replace(value)
    if value.stored is not None:
        turn = value.stored.turn
        if (turn.rotation_id, turn.turn_id, tuple(r[0] for r in turn.roster),
                turn.max_tasks, turn.max_workers) != (
                args.rotation_id, args.turn_id, tuple(args.batch_id), args.max_tasks, args.max_workers):
            raise ValueError('research_dispatch_receipt_mismatch')
    body = value.to_dict()
    if value.stop_requested:
        return body, 130
    for attempt in value.attempts:
        execution = attempt.execution
        if (attempt.status != 'returned' or execution is None or execution.record is None
                or execution.record.run.research is None
                or execution.record.run.research.status != 'completed'):
            return body, 1
    # A replay/no-work turn is a successful *operation*, not completed research.
    return body, 0



def _publish(envelope, code, stop):
    """Use the existing checked emitter without losing cooperative interruption.

    Cleanup precedes publication. A short write may leave a prefix; neither a
    failed output nor a stopped token rolls back previously admitted work.
    """
    result = _emit(envelope, code)
    if result == 130 and stop is not None:
        stop.request_stop()
    return result



def _read_admission(args):
    """Original canonical codec, with at most one optional LF/CRLF frame.

    Bound total bytes before decoding. Short reads are not EOF. The producer
    must close stdin; this byte limit is not a wall-clock timeout for a pipe.
    No supplied approval digest, timestamp or source bytes are replaced.
    """
    digest(args.input_sha256)
    batching = args.operation == 'enqueue-batch'
    maximum = (MAX_BATCH_BYTES if batching else MAX_POLICY_BYTES) + 2
    source = sys.stdin.buffer
    data = bytearray()
    while True:
        limit = min(65536, maximum + 1 - len(data))
        part = source.read(limit)
        if type(part) is not bytes or len(part) > limit:
            raise ValueError('research_dispatch_input_invalid')
        if not part:
            break
        data.extend(part)
        if len(data) > maximum:
            raise ValueError('research_dispatch_input_limit')
    raw = bytes(data)
    if raw.endswith(b'\r\n'):
        raw = raw[:-2]
    elif raw.endswith(b'\n'):
        raw = raw[:-1]
    value = (decode_batch if batching else decode_budget)(raw.decode('utf-8'), args.input_sha256)
    expected_id = args.batch_id if batching else args.budget_id
    if (value.batch_id if batching else value.budget_id) != expected_id:
        raise ValueError('research_dispatch_input_identity_mismatch')
    return value


def _admit(session, args, value):
    """Only immutable metadata, not a fresh snapshot or proof of new creation."""
    # Preserve the reviewed value even if a faulty collaborator mutates the
    # supplied object before returning a receipt that aliases that same object.
    expected_payload = value.payload
    if args.operation == 'enqueue-batch':
        stored = session.enqueue_research_batch(batch=value, allow_queue_write=True)
        if type(stored) is not StoredResearchBatch:
            raise ValueError('research_dispatch_receipt_invalid')
        stored = replace(stored)
        if stored.batch.payload != expected_payload:
            raise ValueError('research_dispatch_receipt_mismatch')
        return dict(batch_id=stored.batch.batch_id, batch_sha256=stored.batch.content_sha256,
            request_count=len(stored.batch.requests), enqueued_at=stored.enqueued_at.isoformat())
    stored = session.create_model_budget(policy=value, allow_budget_write=True)
    if type(stored) is not StoredModelBudget:
        raise ValueError('research_dispatch_receipt_invalid')
    stored = replace(stored)
    if stored.policy.payload != expected_payload:
        raise ValueError('research_dispatch_receipt_mismatch')
    return dict(budget_id=stored.policy.budget_id, policy_sha256=stored.policy.content_sha256,
        created_at=stored.created_at.isoformat(), expires_at=stored.policy.expires_at.isoformat(),
        request_count=len(stored.policy.request_keys))

def main(argv: list[str] | None = None, *, default_root: Path,
         model_factory=None, stop: ResearchDispatchStop | None = None) -> int:
    """Emit existing metadata receipts only, after managed-session cleanup.

    The supplied factory remains an explicit trusted application boundary, not
    a sandbox or proof of a provider's charge bound. There is no dynamic import,
    environment credential lookup or implicit unbudgeted fallback. Use the typed
    session API instead when retaining pending_run for in-process capture retry.
    """
    parser = _parser(default_root)
    args = parser.parse_args(argv)
    if args.operation in ('capture-paper', 'inspect-paper'):
        from polymarket_alpha_lab.research_paper_operator import operate_paper
        return operate_paper(root=args.root, operation=args.operation, record_id=args.record_id,
            input_sha256=getattr(args, 'input_sha256', None),
            allow_paper_write=getattr(args, 'allow_paper_write', False))
    admitting = args.operation in ('enqueue-batch', 'create-budget')
    running = args.operation == 'run-turn'
    if running:
        try:
            batch_ids(tuple(args.batch_id))
            if not 1 <= args.max_tasks <= 100 or not 1 <= args.max_workers <= 8:
                raise ValueError
        except ValueError:
            parser.error('invalid run limits or roster')
    if stop is not None and type(stop) is not ResearchDispatchStop:
        parser.error('invalid stop token')
    envelope = dict(operation=args.operation, result=None,
        automatic_reclaim_permitted=False, automatic_retry_permitted=False,
        final_database_state_checked=False, provider_charge_bound_verified=False,
        paper_only=True, report_only=True, readonly=True)
    if running and (args.allow_model_calls is not True or not callable(model_factory)):
        reason = ('research_dispatch_model_opt_in_required' if not args.allow_model_calls
                  else 'research_dispatch_approved_client_required')
        envelope.update(status='blocked', reason_code=reason,
            operation_entered=False, model_calls_possible=False, business_writes_possible=False)
        return _publish(envelope, 2, stop)
    admission = None
    if admitting:
        envelope.update(operation_entered=False, model_calls_possible=False,
                        business_writes_possible=False, public_network_called=False)
        allowed = (args.allow_queue_write if args.operation == 'enqueue-batch'
                   else args.allow_budget_write)
        if allowed is not True:
            return _publish(dict(envelope, status='blocked',
                reason_code='research_dispatch_admission_opt_in_required'), 2, stop)
        try:
            admission = _read_admission(args)
        except KeyboardInterrupt:
            # Preserve the INPUT interruption even if publishing its receipt
            # also fails. Output status alone cannot carry this shared signal.
            if stop is not None:
                stop.request_stop()
            return _publish(dict(envelope, status='interrupted',
                reason_code='research_dispatch_input_interrupted'), 130, stop)
        except (Exception, SystemExit):
            return _publish(dict(envelope, status='invalid_input',
                reason_code='research_dispatch_input_invalid'), 2, stop)
    # Conservatively report possible effects once the managed operation is
    # entered, including unknown COMMIT/cleanup acknowledgement. Never infer
    # absence of writes from an exception. No success is printed before cleanup.
    envelope.update(operation_entered=True, model_calls_possible=running,
                    business_writes_possible=running or admitting)
    control = ResearchDispatchStop() if stop is None else stop
    try:
        with ProjectPostgres(args.root).session() as session:
            if admitting:
                body = _admit(session, args, admission)
                code, status = 0, 'admission_receipt_returned'
            elif running:
                body, code = _run(session, args, model_factory, control)
                status = body['status']
            else:
                body = _inspect(session, args)
                code, status = (3, 'not_found') if body is None else (0, 'inspected')
        envelope.update(status=status, result=body)
    except KeyboardInterrupt:
        control.request_stop()
        envelope.update(status='interrupted', reason_code='research_dispatch_interrupted')
        code = 130
    except (Exception, SystemExit):
        envelope.update(status='failed', reason_code='research_dispatch_operation_failed')
        code = 1
    return _publish(envelope, code, control)


__all__ = ('main',)
