"""One explicitly authorized Codex loop through the original immutable claim path.

A local CLI turn is not a priced/single-provider-operation allowance. Actual fees
and provider-request counts are unknown; only validated CLI usage is recorded.
"""
from dataclasses import replace
from threading import Lock

from polymarket_alpha_lab import research_execution_psycopg as execution
from polymarket_alpha_lab.research_codex_local import copy_profile, invoke_codex
from polymarket_alpha_lab.research_codex_protocol import CodexReply
from polymarket_alpha_lab.research_execution import copy_request
from polymarket_alpha_lab.research_model_budget_runner import _initial_message_incompatible
from polymarket_alpha_lab.research_uncapped_authorization import UncappedModelSnapshot, copy_authorization
from polymarket_alpha_lab.research_uncapped_store import (
    _reserve_invocation, _save_outcome, inspect_uncapped_authorization_with_psycopg,
)
from polymarket_alpha_lab.team_research_agent_types import identifier


class _UncappedCodex:
    __slots__ = ('_dsn','_request','_policy','_profile','_number','_failed','_lock')

    def __init__(self, dsn, request, policy, profile):
        self._policy = copy_authorization(policy)
        self._request = self._policy.bind_request(request)
        self._profile = self._bound_profile(profile)
        self._dsn, self._number, self._failed, self._lock = dsn, 0, False, Lock()

    def _bound_profile(self, value):
        profile = copy_profile(value)
        if (profile.content_sha256 != self._policy.profile_sha256
                or profile.model_id != self._policy.model_id):
            raise ValueError('research_uncapped_profile_mismatch')
        return profile

    def __repr__(self):
        return 'UncappedCodex(client=<private>)'

    def complete(self, *, messages_json, max_output_tokens):
        with self._lock:
            if self._failed:
                raise ValueError('research_uncapped_client_stopped')
            self._number += 1
            try:
                profile = self._bound_profile(self._profile)
                owned = _reserve_invocation(self._dsn, policy=self._policy, request=self._request,
                    call_number=self._number, messages_json=messages_json, max_output_tokens=max_output_tokens)
                if owned is not True:
                    raise ValueError('research_uncapped_invocation_already_reserved')
                try:
                    result = invoke_codex(profile, messages_json=messages_json,
                                         max_output_tokens=max_output_tokens, call_number=self._number)
                    if type(result) is not CodexReply:
                        raise ValueError('research_codex_reply_invalid')
                    result = replace(result)
                except Exception:
                    # One failure write only; no inferred usage, refund or resend.
                    _save_outcome(self._dsn, request=self._request, policy=self._policy,
                                  call_number=self._number, result=None)
                    raise ValueError('research_codex_invocation_failed') from None
                # Persist validated usage before allowing the next native turn.
                # Lost COMMIT acknowledgement leaves this wrapper stopped.
                _save_outcome(self._dsn, request=self._request, policy=self._policy,
                              call_number=self._number, result=result)
                return result.reply
            except BaseException as error:
                self._failed = True
                if not isinstance(error, Exception):
                    raise
                raise ValueError('research_uncapped_call_failed') from None


def run_uncapped_research_with_psycopg(dsn, *, request, authorization_id, profile, allow_model_calls=False):
    if allow_model_calls is not True:
        raise ValueError('research_uncapped_model_opt_in_required')
    identifier('authorization_id', authorization_id)
    request, profile = copy_request(request), copy_profile(profile)
    snapshot = inspect_uncapped_authorization_with_psycopg(dsn, authorization_id=authorization_id)
    if type(snapshot) is not UncappedModelSnapshot:
        raise ValueError('research_uncapped_authorization_required')
    snapshot = replace(snapshot)
    policy = snapshot.stored.policy
    if (policy.authorization_id != authorization_id or policy.profile_sha256 != profile.content_sha256
            or policy.model_id != profile.model_id):
        raise ValueError('research_uncapped_profile_mismatch')
    request = policy.bind_request(request)
    blocked = ('research_uncapped_authorization_expired' if snapshot.observed_at >= policy.expires_at
               else 'research_uncapped_initial_message_incompatible' if _initial_message_incompatible(request, policy)
               else None)
    if blocked is not None:
        prior = execution.inspect_captured_research_with_psycopg(dsn, record_id=request.record_id)
        if prior is not None:
            prior = replace(prior)
            if prior.request.payload != request.payload:
                raise ValueError('research_uncapped_replay_mismatch')
            return prior
        raise ValueError(blocked)
    def factory(team):
        if team != request.intake.team_id:
            raise ValueError('research_uncapped_team_mismatch')
        return _UncappedCodex(dsn, request, policy, profile)
    return execution.run_captured_research_with_psycopg(dsn, request=request, model_factory=factory)
