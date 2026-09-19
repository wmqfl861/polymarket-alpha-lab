"""Explicit no-money-cap authorization. It never impersonates a priced budget.

A bounded immutable roster is input validation, not a total business quota.
Single-task resource/cutoff limits remain; no global call or monetary cap exists.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from hashlib import sha256
import json

from polymarket_alpha_lab.research_execution import copy_request
from polymarket_alpha_lab.research_model_budget import digest
from polymarket_alpha_lab.research_resolution import utc
from polymarket_alpha_lab.team_research_agent_types import hard_flags, identifier, integer, strict_json, text

VERSION = 'research-uncapped-authorization-v1'
MAX_AUTHORIZATION_BYTES = 32768


@dataclass(frozen=True, slots=True)
class UncappedModelAuthorization:
    authorization_id: str
    model_id: str
    profile_sha256: str
    expires_at: datetime
    request_keys: tuple[tuple[str, str], ...]
    max_message_bytes: int = 2000000
    uncapped_cost_approved: bool = False
    provider_id: str = 'codex-cli'
    output_limit_mode: str = 'observed_after_response'
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self):
        identifier('authorization_id', self.authorization_id)
        text('model_id', self.model_id, 128)
        digest(self.profile_sha256)
        object.__setattr__(self, 'expires_at', utc('expires_at', self.expires_at))
        integer('max_message_bytes', self.max_message_bytes, 1, 2000000)
        if (self.uncapped_cost_approved is not True or type(self.provider_id) is not str
                or type(self.output_limit_mode) is not str or self.provider_id != 'codex-cli'
                or self.output_limit_mode != 'observed_after_response'):
            raise ValueError('research_uncapped_approval_required')
        if (type(self.request_keys) is not tuple or not 1 <= len(self.request_keys) <= 100
                or any(type(k) is not tuple or len(k) != 2 for k in self.request_keys)):
            raise ValueError('research_uncapped_roster_invalid')
        for record_id, checksum in self.request_keys:
            identifier('record_id', record_id)
            digest(checksum)
        if len({k[0] for k in self.request_keys}) != len(self.request_keys):
            raise ValueError('research_uncapped_roster_invalid')
        hard_flags(self)

    @property
    def payload(self):
        body = asdict(self)
        body['expires_at'] = self.expires_at.isoformat()
        return json.dumps(dict(schema_version=VERSION, **body), sort_keys=True,
                          separators=(',', ':'), ensure_ascii=True, allow_nan=False)

    @property
    def content_sha256(self):
        return sha256(self.payload.encode()).hexdigest()

    def bind_request(self, request):
        policy, request = copy_authorization(self), copy_request(request)
        if (request.intake.team_id not in ('crypto_btc', 'crypto_eth')
                or request.model_id != policy.model_id
                or request.protocol_version != 'codex-cli-0.155.1-pal-v1'
                or (request.record_id, request.content_sha256) not in policy.request_keys):
            raise ValueError('research_uncapped_request_mismatch')
        return request


def copy_authorization(value):
    if type(value) is not UncappedModelAuthorization:
        raise ValueError('research_uncapped_authorization_invalid')
    result = replace(value)
    if len(result.payload.encode()) > MAX_AUTHORIZATION_BYTES:
        raise ValueError('research_uncapped_authorization_limit')
    return result


def decode_authorization(payload, checksum):
    try:
        if type(payload) is not str or not 1 <= len(payload.encode()) <= MAX_AUTHORIZATION_BYTES:
            raise ValueError
        digest(checksum)
        if sha256(payload.encode()).hexdigest() != checksum:
            raise ValueError
        data = strict_json(payload)
        if type(data) is not dict or data.pop('schema_version', None) != VERSION:
            raise ValueError
        if type(data['request_keys']) is not list or any(type(k) is not list for k in data['request_keys']):
            raise ValueError
        data['request_keys'] = tuple(tuple(k) for k in data['request_keys'])
        data['expires_at'] = datetime.fromisoformat(data['expires_at'])
        result = copy_authorization(UncappedModelAuthorization(**data))
        if result.payload != payload:
            raise ValueError
        return result
    except Exception:
        raise ValueError('research_uncapped_payload_invalid') from None


@dataclass(frozen=True, slots=True)
class StoredUncappedAuthorization:
    policy: UncappedModelAuthorization
    created_at: datetime

    def __post_init__(self):
        object.__setattr__(self, 'policy', copy_authorization(self.policy))
        object.__setattr__(self, 'created_at', utc('created_at', self.created_at))
        if self.created_at >= self.policy.expires_at:
            raise ValueError('research_uncapped_creation_expired')

    def to_dict(self):
        self.__post_init__()
        return dict(authorization_id=self.policy.authorization_id, policy_sha256=self.policy.content_sha256,
            profile_sha256=self.policy.profile_sha256, model_id=self.policy.model_id,
            created_at=self.created_at.isoformat(), expires_at=self.policy.expires_at.isoformat(),
            monetary_cap=None, global_call_cap=None, actual_billed_micros=None,
            provider_submission_count=None, hard_output_cap_enforced=False,
            output_limit_mode=self.policy.output_limit_mode,
            paper_only=True, report_only=True, readonly=True)


@dataclass(frozen=True, slots=True)
class UncappedModelSnapshot:
    stored: StoredUncappedAuthorization
    observed_at: datetime
    reserved_invocations: int
    validated_turns: int
    failed_invocations: int
    reported_total_tokens: int

    def __post_init__(self):
        if type(self.stored) is not StoredUncappedAuthorization:
            raise ValueError('research_uncapped_snapshot_invalid')
        object.__setattr__(self, 'stored', replace(self.stored))
        object.__setattr__(self, 'observed_at', utc('observed_at', self.observed_at))
        for name in ('reserved_invocations', 'validated_turns', 'failed_invocations'):
            integer(name, getattr(self, name), 0, 3200)
        integer('reported_total_tokens', self.reported_total_tokens, 0, 3200000000)
        if (self.observed_at < self.stored.created_at
                or self.validated_turns + self.failed_invocations > self.reserved_invocations
                or not self.validated_turns <= self.reported_total_tokens <= self.validated_turns*1000000):
            raise ValueError('research_uncapped_accounting_invalid')

    def to_dict(self):
        row = replace(self)
        return dict(row.stored.to_dict(), observed_at=row.observed_at.isoformat(),
            expired=row.observed_at >= row.stored.policy.expires_at,
            reserved_invocations=row.reserved_invocations, validated_turns=row.validated_turns,
            failed_invocations=row.failed_invocations,
            unknown_invocations=row.reserved_invocations-row.validated_turns-row.failed_invocations,
            reported_total_tokens=row.reported_total_tokens,
            usage_scope='validated_turns_only', automatic_retries=False)
