"""Explicit budgeted use of the existing capture loop; no provider selection.

Only a newly committed permit enters the caller's factory/client. Factories must
be inert constructors and one complete must represent at most ONE charge-bounded
provider operation. These are reviewed adapter contracts, not sandbox enforcement.
"""
from dataclasses import replace
from threading import Lock

from polymarket_alpha_lab import research_execution_psycopg as execution
from polymarket_alpha_lab import team_research_agent as agent
from polymarket_alpha_lab.research_execution import copy_request
from polymarket_alpha_lab.research_model_budget import ModelBudgetSnapshot, copy_budget
from polymarket_alpha_lab.research_model_budget_store import _reserve_call, load_model_budget_with_psycopg
from polymarket_alpha_lab.team_research_agent_types import ResearchModelReply, identifier


class _BudgetedModel:
    __slots__ = ('_dsn', '_policy', '_request', '_factory', '_client', '_number', '_failed', '_lock')

    def __init__(self, dsn, policy, request, factory):
        self._policy = copy_budget(policy)
        self._request = self._policy.bind_request(request)
        self._dsn, self._factory = dsn, factory
        self._client = None
        self._number = 0
        self._failed = False
        self._lock = Lock()

    def __repr__(self):
        return 'BudgetedResearchModel(client=<private>)'

    def complete(self, *, messages_json, max_output_tokens):
        # No two calls can share/reorder one per-task sequence. Different tasks
        # can execute concurrently; the database arbitrates their shared cap.
        with self._lock:
            if self._failed:
                raise ValueError('research_budget_client_stopped')
            self._number += 1
            try:
                owned = _reserve_call(self._dsn, policy=self._policy, request=self._request,
                    call_number=self._number, messages_json=messages_json, max_output_tokens=max_output_tokens)
                if owned is not True:
                    raise ValueError('research_budget_call_already_reserved')
                if self._client is None:
                    self._client = self._factory(self._request.intake.team_id)
                if not callable(getattr(self._client, 'complete', None)):
                    raise ValueError('research_budget_client_invalid')
                reply = self._client.complete(messages_json=messages_json, max_output_tokens=max_output_tokens)
                if type(reply) is not ResearchModelReply:
                    raise ValueError('research_budget_reply_invalid')
                reply.__post_init__()
                return reply
            except BaseException as error:
                # No refund or automatic resend, including after interruption.
                self._failed = True
                # KeyboardInterrupt/SystemExit retain their original semantics.
                if not isinstance(error, Exception):
                    raise
                raise ValueError('research_budget_call_blocked_or_failed') from None


def _initial_message_incompatible(request, policy):
    """Reject only a first call the existing agent would actually attempt.

    Keep no-eligible-evidence/context-limit captures on their original path.
    The allowance counts UTF-8 bytes, unlike the agent's character context cap.
    Later tool transcripts still use the original per-call reservation guard.
    """
    if request.intake.status != 'prepared':
        return False
    catalog, messages = agent._initial_context(request.intake.task, request.limits,
                                               request.required_source_ids)
    if not catalog:
        return False
    transcript = agent._dump(messages)
    return (len(transcript) <= request.limits.max_context_chars
            and len(transcript.encode('utf-8')) > policy.max_message_bytes)


def run_budgeted_research_with_psycopg(dsn, *, request, budget_id, model_factory, allow_model_calls=False):
    """Bind explicit policy to a request, then reuse original immutable claims.

    Budget exhaustion/client errors use the unchanged model_failed capture reason;
    original model_calls counts complete attempts, not actual billable requests.
    Read the reservation ledger separately; it is a cap, not provider billing.
    Existing completed/incomplete request replays never enter this lazy client.
    """
    identifier('budget_id', budget_id)
    if allow_model_calls is not True or not callable(model_factory):
        raise ValueError('research_budget_model_opt_in_required')
    request = copy_request(request)
    snapshot = load_model_budget_with_psycopg(dsn, budget_id=budget_id)
    if snapshot is None:
        raise ValueError('research_budget_not_found')
    if type(snapshot) is not ModelBudgetSnapshot:
        raise ValueError('research_budget_snapshot_invalid')
    snapshot = replace(snapshot)
    if snapshot.stored.policy.budget_id != budget_id:
        raise ValueError('research_budget_lookup_mismatch')
    policy = snapshot.stored.policy
    request = policy.bind_request(request)
    blocked_reason = None
    if snapshot.to_dict()['available_call_reservations'] == 0:
        blocked_reason = 'research_budget_not_available'
    elif (request.intake.status == 'prepared'
            and request.limits.max_output_tokens > policy.max_output_tokens):
        # The existing loop requests this fixed ceiling on its first call.
        # Do not consume an immutable claim for a known-incompatible allowance,
        # or silently clamp the reviewed request to make that call admissible.
        blocked_reason = 'research_budget_output_limit_incompatible'
    elif _initial_message_incompatible(request, policy):
        blocked_reason = 'research_budget_initial_message_incompatible'
    if blocked_reason is not None:
        # Existing claims/results remain readable even if this allowance cannot
        # admit NEW work. No new claim, call permit or client is created here.
        prior = execution.inspect_captured_research_with_psycopg(dsn, record_id=request.record_id)
        if prior is not None:
            prior = replace(prior)
            if prior.request.payload != request.payload:
                raise ValueError('research_budget_request_mismatch')
            return prior
        raise ValueError(blocked_reason)

    def factory(team_id):
        if team_id != request.intake.team_id:
            raise ValueError('research_budget_team_mismatch')
        return _BudgetedModel(dsn, policy, request, model_factory)
    return execution.run_captured_research_with_psycopg(dsn, request=request, model_factory=factory)
