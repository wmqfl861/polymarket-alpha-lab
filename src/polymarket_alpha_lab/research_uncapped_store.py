"""Append-only uncapped authorization, shared call ordinals and reported usage.

Every operation uses the existing project/DSN-bound transaction implementation.
No model, filesystem, auth discovery, retry, transcript or invented fee record.
"""
from dataclasses import asdict, replace
from hashlib import sha256

from polymarket_alpha_lab import research_capture_psycopg as db
from polymarket_alpha_lab import research_execution_psycopg as execution
from polymarket_alpha_lab.research_codex_protocol import CodexReply, CodexUsage, build_prompt
from polymarket_alpha_lab.research_resolution import utc
from polymarket_alpha_lab.research_uncapped_authorization import (
    MAX_AUTHORIZATION_BYTES, StoredUncappedAuthorization, UncappedModelSnapshot,
    copy_authorization, decode_authorization,
)
from polymarket_alpha_lab.team_research_agent_types import identifier, integer

_COLUMNS = 'authorization_id,payload,payload_sha256,created_at,paper_only,report_only,readonly'
_USAGE = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens')
_OUTCOME = 'record_id,call_number,status,' + ','.join(_USAGE) + ',output_sha256,completed_at,paper_only,report_only,readonly'


def _row(row):
    if type(row) is not tuple or len(row) != 7 or any(x is not True for x in row[4:]):
        raise ValueError('research_uncapped_row_invalid')
    policy = decode_authorization(row[1], row[2])
    if row[0] != policy.authorization_id:
        raise ValueError('research_uncapped_row_mismatch')
    return StoredUncappedAuthorization(policy, row[3])


def _lock(cursor, authorization_id):
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                   ('polymarket/uncapped/' + authorization_id,))


def _load(cursor, authorization_id):
    cursor.execute('SELECT octet_length(payload) FROM research_capture.uncapped_model_authorizations WHERE authorization_id=%s', (authorization_id,))
    length = cursor.fetchone()
    if length is None:
        return None
    if type(length) is not tuple or len(length) != 1 or type(length[0]) is not int or not 1 <= length[0] <= MAX_AUTHORIZATION_BYTES:
        raise ValueError('research_uncapped_payload_limit')
    cursor.execute(f'SELECT {_COLUMNS} FROM research_capture.uncapped_model_authorizations WHERE authorization_id=%s', (authorization_id,))
    stored = _row(cursor.fetchone())
    if stored.policy.authorization_id != authorization_id:
        raise ValueError('research_uncapped_lookup_mismatch')
    return stored


def create_uncapped_authorization_with_psycopg(dsn, *, policy, allow_authorization_write=False):
    if allow_authorization_write is not True:
        raise ValueError('research_uncapped_write_opt_in_required')
    policy = copy_authorization(policy)
    expected = policy.payload
    def operation(cursor):
        _lock(cursor, policy.authorization_id)
        existing = _load(cursor, policy.authorization_id)
        if existing is not None:
            if existing.policy.payload != expected:
                raise db.ResearchCaptureConflict('research_uncapped_authorization_conflict')
            return existing
        cursor.execute('INSERT INTO research_capture.uncapped_model_authorizations (authorization_id,payload,payload_sha256) '
            f'VALUES (%s,%s,%s) RETURNING {_COLUMNS}', (policy.authorization_id, expected, policy.content_sha256))
        stored = _row(cursor.fetchone())
        if stored.policy.payload != expected:
            raise ValueError('research_uncapped_insert_mismatch')
        return stored
    return db._local_transaction(dsn, operation)


def inspect_uncapped_authorization_with_psycopg(dsn, *, authorization_id):
    identifier('authorization_id', authorization_id)
    def operation(cursor):
        cursor.execute('SELECT clock_timestamp()')
        now = utc('database clock', cursor.fetchone()[0])
        stored = _load(cursor, authorization_id)
        if stored is None:
            return None
        cursor.execute("SELECT count(*),count(o.record_id) FILTER (WHERE o.status='validated'),"
            "count(o.record_id) FILTER (WHERE o.status='failed'),"
            "coalesce(sum(o.input_tokens::bigint+o.output_tokens),0) "
            'FROM research_capture.model_call_reservations r '
            'LEFT JOIN research_capture.codex_invocation_outcomes o USING (record_id,call_number) '
            'WHERE r.authorization_id=%s', (authorization_id,))
        counts = cursor.fetchone()
        if type(counts) is not tuple or len(counts) != 4 or any(type(n) is not int for n in counts[:3]):
            raise ValueError('research_uncapped_accounting_invalid')
        # PostgreSQL sum(bigint) is exact numeric. Never truncate fractional data.
        total = counts[3]
        if not 0 <= total <= 3200000000 or total != int(total):
            raise ValueError('research_uncapped_accounting_invalid')
        return UncappedModelSnapshot(stored, now, *counts[:3], int(total))
    return db._local_transaction(dsn, operation, readonly=True)


def _reserve_invocation(dsn, *, policy, request, call_number, messages_json, max_output_tokens):
    policy = copy_authorization(policy)
    request = policy.bind_request(request)
    integer('call_number', call_number, 1, request.limits.max_model_calls)
    integer('max_output_tokens', max_output_tokens, 1, request.limits.max_output_tokens)
    build_prompt(messages_json, max_output_tokens)  # Pure validation before reservation.
    raw = messages_json.encode('utf-8')
    if len(raw) > policy.max_message_bytes:
        raise ValueError('research_uncapped_message_limit')
    args = (policy.authorization_id, request.record_id, request.content_sha256, call_number,
            sha256(raw).hexdigest(), len(raw), max_output_tokens)
    columns = 'authorization_id,record_id,request_sha256,call_number,message_sha256,message_bytes,max_output_tokens'
    def operation(cursor):
        _lock(cursor, policy.authorization_id)
        saved = _load(cursor, policy.authorization_id)
        if saved is None or saved.policy.payload != policy.payload:
            raise db.ResearchCaptureConflict('research_uncapped_authorization_conflict')
        cursor.execute(f'SELECT {columns} FROM research_capture.model_call_reservations WHERE record_id=%s AND call_number=%s',
                       (request.record_id, call_number))
        prior = cursor.fetchone()
        if prior is not None:
            if prior != args:
                raise db.ResearchCaptureConflict('research_uncapped_invocation_conflict')
            return False
        claimed = execution._lookup(cursor, request.record_id)
        if claimed is None or claimed.request.payload != request.payload or claimed.record is not None:
            raise db.ResearchCaptureConflict('research_uncapped_claim_required')
        cursor.execute(f'INSERT INTO research_capture.model_call_reservations ({columns}) '
            f'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {columns},budget_id,reservation_number,reserved_micros', args)
        if cursor.fetchone() != (*args, None, None, None):
            raise ValueError('research_uncapped_reservation_mismatch')
        return True
    return db._local_transaction(dsn, operation)


def _save_outcome(dsn, *, request, policy, call_number, result):
    """Only an owned invocation may call this. None records failure, not zero cost."""
    policy = copy_authorization(policy)
    request = policy.bind_request(request)
    integer('call_number', call_number, 1, request.limits.max_model_calls)
    if result is not None:
        if type(result) is not CodexReply:
            raise ValueError('research_codex_reply_invalid')
        result = replace(result)
    values = ((None,)*6 if result is None else
              tuple(getattr(result.usage, k) for k in _USAGE) + (result.output_sha256,))
    expected = (request.record_id, call_number, 'failed' if result is None else 'validated', *values)
    def operation(cursor):
        _lock(cursor, policy.authorization_id)
        cursor.execute('SELECT authorization_id,request_sha256,reserved_at,max_output_tokens FROM research_capture.model_call_reservations '
            'WHERE record_id=%s AND call_number=%s', (request.record_id, call_number))
        permit = cursor.fetchone()
        if (type(permit) is not tuple or len(permit) != 4
                or permit[:2] != (policy.authorization_id, request.content_sha256)):
            raise db.ResearchCaptureConflict('research_uncapped_permit_required')
        reserved_at = utc('reserved_at', permit[2])
        integer('max_output_tokens', permit[3], 1, request.limits.max_output_tokens)
        if result is not None and result.usage.output_tokens > permit[3]:
            raise ValueError('research_uncapped_output_overrun')
        cursor.execute(f'SELECT {_OUTCOME} FROM research_capture.codex_invocation_outcomes WHERE record_id=%s AND call_number=%s',
                       (request.record_id, call_number))
        previous = cursor.fetchone()
        if previous is None:
            cursor.execute('INSERT INTO research_capture.codex_invocation_outcomes '
                '(record_id,call_number,status,' + ','.join(_USAGE) + ',output_sha256) '
                f'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {_OUTCOME}', expected)
            previous = cursor.fetchone()
        if (type(previous) is not tuple or len(previous) != 13 or previous[:9] != expected
                or any(x is not True for x in previous[10:])
                or utc('completed_at', previous[9]) < reserved_at):
            raise db.ResearchCaptureConflict('research_codex_outcome_conflict')
        # Never return raw client data; exact metadata was checked before COMMIT.
        return previous[9]
    return db._local_transaction(dsn, operation)
