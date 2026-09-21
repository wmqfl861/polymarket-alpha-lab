"""PX-04 cross-module composition tests; fully synthetic data and clients.

PAL_PARALLEL_REVIEW_20260921_V2 lane PX-04. These tests join interfaces that
existing single-module suites already cover separately; they deliberately do
NOT repeat those single-module properties. The compositions exercised here:

    strict protocol decode (Codex subset-cache stream / Claude additive stream)
      -> real agent loop -> uncapped claim runner (mock claim/capture seam,
         optionally with the mock durable-audit harness)
      -> evaluation record/report -> paper scenario walk/cost report
      -> stored paper receipt -> crypto confirmation -> settlement assembly
      -> per-group denominators and conserved Decimal sums.

Reused lane assets (read-only reuse, no re-testing of their single-module
nature): LT-02 decoder/stop counterexamples (tests/test_research_exec_boundaries.py,
tests/test_research_transport_stop_order.py), LT-03 Decimal oracle fixtures and
independent expectations (tests/test_research_paper_settlement_decimal_oracle.py),
the uncapped claim harness (tests/test_research_uncapped.Harness) and the audit
harness (tests/test_research_uncapped_audit.AuditHarness).

Honesty boundaries: mock claim/audit transactions are NOT native PostgreSQL
transaction proof; synthetic settled PnL is NOT strategy-validity proof; the
two synthetic protocol clients are NOT real providers and no third real client
is introduced; no market record is real (all condition IDs, books, candles and
confirmations are fabricated fixtures); official_cases_run stays 0.

Expected numbers are independent expressions: token totals are plain integer
arithmetic over the synthetic usage numbers placed in the streams (never the
decoder's own accounting), and settlement amounts are re-derived from the raw
book strings with the LT-03 test-side oracle (never the production cost code).
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from decimal import localcontext
from types import SimpleNamespace
import json
import re

import pytest

from polymarket_alpha_lab import research_claude_exec as claude
from polymarket_alpha_lab import research_codex_exec as codex
from polymarket_alpha_lab import research_paper as paper_core
from polymarket_alpha_lab import research_paper_capture as paper_store
from polymarket_alpha_lab import research_paper_settlement as settlement_core
from polymarket_alpha_lab import research_uncapped_runner as runner
from polymarket_alpha_lab.cost_aware_event_strategy import (
    PaperCostAwareEventCostAssumptions as Costs,
    PaperCostAwareEventStrategyConfig as Gates,
)
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from polymarket_alpha_lab.research_paper import _CONTEXT
from polymarket_alpha_lab.research_paper_inputs import (
    ResearchPaperBook as Book,
    ResearchPaperScenario as Scenario,
)
from polymarket_alpha_lab.research_paper_capture_codec import dump
from polymarket_alpha_lab.research_process import ResearchProcessResult
from polymarket_alpha_lab.team_research_evaluation import (
    ResearchEvaluationOutcome, ResearchEvaluationRecord, ResearchEvaluationReport,
)
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot

from tests.test_research_claude_exec import action as claude_action
from tests.test_research_claude_exec import envelope, MODEL
from tests.test_research_codex_exec import action as codex_action
from tests.test_research_codex_exec import events
from tests.test_research_paper_settlement_decimal_oracle import (
    _millis, confirmation, oracle_payout, oracle_selection, oracle_totals,
    settlement_case, stored_receipt, synthetic_case,
)
from tests.test_research_uncapped import Harness, approval
from tests.test_research_uncapped import NOW
from tests.test_research_uncapped_audit import AuditHarness

# Synthetic per-call usage numbers (the independent arithmetic source of truth).
# Codex 'input_tokens' INCLUDES cached tokens (subset convention); Claude
# reports uncached input plus cache read/write separately (additive convention).
CODEX_USAGE = (
    dict(input_tokens=100, cached_input_tokens=10, cache_write_input_tokens=0,
         output_tokens=20, reasoning_output_tokens=0),
    dict(input_tokens=90, cached_input_tokens=30, cache_write_input_tokens=0,
         output_tokens=20, reasoning_output_tokens=0),
)
CLAUDE_USAGE = (
    dict(input_tokens=30, output_tokens=6, cache_creation_input_tokens=7,
         cache_read_input_tokens=11),
    dict(input_tokens=25, output_tokens=6, cache_creation_input_tokens=7,
         cache_read_input_tokens=15),
)
# Independent expressions, one per client convention.
EXPECTED_PER_CALL = {
    'codex': ((100 - 10) + 10 + 20, (90 - 30) + 30 + 20),
    'claude': (30 + 7 + 11 + 6, 25 + 7 + 15 + 6),
}
EXPECTED_RESEARCH_TOTALS = {kind: sum(calls) for kind, calls in EXPECTED_PER_CALL.items()}

YES_ASKS, YES_BIDS = (('0.4', '10'),), (('0.39', '10'),)
NO_ASKS, NO_BIDS = (('0.6', '10'),), (('0.59', '10'),)
COSTS = Costs(D('.02'), D('.001'), D(0), D(0), D(0), D(0))
GATES = Gates('px04-composition', min_confidence=D('.7'), max_spread=D('.05'),
              max_resolution_risk=D('.2'), min_ask_size=D('1'), min_net_edge=D('.01'))


class ProtocolScriptedClient:
    """One synthetic protocol stream per agent-loop call, through the real
    strict decoder of the selected client kind. No process, network or DB."""

    def __init__(self, kind, *, fail_at=None, stop_after=None, stop=None,
                 probability_yes='0.7', confidence='0.9'):
        self.kind, self.seen = kind, []
        self.fail_at, self.stop_after, self.stop = fail_at, stop_after, stop
        self.probability_yes, self.confidence = probability_yes, confidence

    def _script_action(self, index):
        if index == 0:
            return 'read_evidence', {'source_id': 'source'}
        return 'finish_research', dict(probability_yes=self.probability_yes,
                                       confidence=self.confidence,
                                       summary='Synthetic composition finish.',
                                       source_ids=['source'])

    def _decode(self, index, name, args, max_output_tokens):
        if self.kind == 'codex':
            rows = events(actions=[codex_action(name, args)], **CODEX_USAGE[index])
            stream = ('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows)
                      + '\n').encode('utf-8')
            return codex.decode_codex_exec_output(
                codex.CodexExecOutput(0, stream), call_number=index + 1,
                max_output_tokens=max_output_tokens)
        counts = CLAUDE_USAGE[index]
        value = envelope(calls=[claude_action(name, **args)], usage=dict(counts),
                         modelUsage={MODEL: dict(zip(
                             ('inputTokens', 'outputTokens',
                              'cacheCreationInputTokens', 'cacheReadInputTokens'),
                             counts.values()))})
        wire_bytes = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        return claude.decode_claude_result(
            ResearchProcessResult(wire_bytes, 0, 20),
            request=claude.ClaudeExecInput(MODEL, '[{}]', max_output_tokens),
            call_number=index + 1)

    def complete(self, *, messages_json, max_output_tokens):
        index = len(self.seen)
        if self.fail_at is not None and index >= self.fail_at:
            # Undecodable transport bytes: the strict decoder must fail closed.
            self.seen.append('failed')
            raw = (codex.CodexExecOutput(0, b'\xff') if self.kind == 'codex'
                   else ResearchProcessResult(b'{}', 0, 20))
            if self.kind == 'codex':
                codex.decode_codex_exec_output(raw, call_number=index + 1)
            else:
                claude.decode_claude_result(
                    raw, request=claude.ClaudeExecInput(MODEL, '[{}]', 100),
                    call_number=index + 1)
            raise AssertionError('decoder accepted undecodable bytes')
        name, args = self._script_action(index)
        reply = self._decode(index, name, args, max_output_tokens)
        self.seen.append(name)
        if self.stop_after is not None and index >= self.stop_after and self.stop is not None:
            self.stop.request_stop()
        return reply


def chain_case(monkeypatch, n, team, kind='codex', *, audited=False, client=None,
               stop=None, stop_in_factory=None, probability_yes='0.7', confidence='0.9'):
    """Full chain once: protocol client -> claim runner -> captured record."""
    request, _, _, raw, _ = synthetic_case(n=n, team=team, at=NOW - timedelta(seconds=30))
    harness = AuditHarness(monkeypatch) if audited else Harness(monkeypatch)
    client = client or ProtocolScriptedClient(
        kind, probability_yes=probability_yes, confidence=confidence)

    def factory(team_id):
        harness.events.append('factory')
        if stop_in_factory is not None:
            stop_in_factory.request_stop()
        return client

    execution = runner.run_uncapped_research_with_psycopg(
        'synthetic', request=request, authorization=approval((request,)),
        model_factory=factory, allow_model_calls=True, allow_uncapped_costs=True,
        stop=stop, **({'require_durable_audit': True} if audited else {}))
    return SimpleNamespace(request=request, execution=execution,
                           record=execution.record, raw=raw,
                           harness=harness, client=client)


def paper_scenario(request, record, raw, *, costs=None, gates=None):
    """Scenario whose book/market capture times are legal for a RUNNER-produced
    record (recorded_at = claim + 2s), unlike the LT-03 direct fixtures."""
    captured_at = record.recorded_at + timedelta(seconds=5)
    decision_at = record.recorded_at + timedelta(seconds=8)
    cid = request.intake.condition_id

    def book(token, bids, asks):
        return Book(captured_at, json.dumps(dict(
            asset_id=token, market=cid, timestamp=str(_millis(captured_at)),
            bids=[dict(price=p, size=s) for p, s in bids],
            asks=[dict(price=p, size=s) for p, s in asks])).encode())

    return Scenario(request.record_id, record.content_sha256, decision_at,
                    GammaMarketSnapshot(request.intake.market_slug, decision_at,
                                        json.dumps(raw).encode()),
                    book('101', YES_BIDS, YES_ASKS), book('102', NO_BIDS, NO_ASKS),
                    D('5'), costs or COSTS, gates or GATES, D('0.1'),
                    'px04-synthetic', 60)


def settle(records, papers, outcomes, reviews, generated_at):
    history = ResearchEvaluationReport(tuple(records), tuple(outcomes), generated_at)
    with localcontext(_CONTEXT):
        return settlement_core._assemble(history, papers, reviews)


def direct_outcome(request, *, offset_seconds=180, actual_yes=True, salt='a'):
    cutoff = request.forecast_cutoff_at
    return ResearchEvaluationOutcome(
        request.intake.condition_id, request.intake.market_slug, cutoff,
        cutoff + timedelta(seconds=offset_seconds),
        cutoff + timedelta(seconds=offset_seconds + 1), actual_yes,
        'synthetic-direct', salt * 64)


def audit_snapshot(record_id, harness):
    from polymarket_alpha_lab import research_uncapped_audit as values
    return values.UncappedAuditSnapshot(record_id, tuple(harness.starts),
                                        tuple(harness.outcomes)).to_dict()


def oracle_expectation():
    levels = lambda rows: tuple((D(p), D(s)) for p, s in rows)
    yes = dict(asks=levels(YES_ASKS), bids=levels(YES_BIDS))
    no = dict(asks=levels(NO_ASKS), bids=levels(NO_BIDS))
    selected, sides, _ = oracle_selection(D('0.7'), D('.9'), yes, no, D('5'),
                                          COSTS, GATES, D('0.1'))
    assert selected == 'yes'  # precondition for the fixed synthetic books
    return selected, oracle_totals(sides[selected], D('5'))


# ---------------------------------------------------------------------------
# 1. Two clients' protocol returns, BTC/ETH: success through the whole chain.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('kind', ['codex', 'claude'])
@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
def test_both_protocol_backends_reach_settlement_with_independent_arithmetic(
        monkeypatch, kind, team):
    case = chain_case(monkeypatch, n=110 if team == 'crypto_btc' else 111,
                      team=team, kind=kind)
    research = case.record.run.research
    assert case.harness.events[:2] == ['claim', 'factory']
    assert research.status == 'completed' and research.reason_code == 'research_completed'
    assert research.source_ids == ('source',)
    assert research.probability_yes == D('0.7') and research.confidence == D('0.9')
    # Cache/token accounting: independent integer arithmetic over the synthetic
    # usage numbers (subset convention for codex, additive for claude).
    assert research.total_tokens == EXPECTED_RESEARCH_TOTALS[kind]
    assert EXPECTED_PER_CALL[kind] == tuple(
        (u['input_tokens'] - u['cached_input_tokens']) + u['cached_input_tokens']
        + u['output_tokens'] for u in CODEX_USAGE) if kind == 'codex' else \
        EXPECTED_PER_CALL[kind] == tuple(
            u['input_tokens'] + u['cache_creation_input_tokens']
            + u['cache_read_input_tokens'] + u['output_tokens'] for u in CLAUDE_USAGE)

    scenario = paper_scenario(case.request, case.record, case.raw)
    history = ResearchEvaluationReport((case.record,), (), scenario.decision_at)
    report = paper_core.ResearchPaperEvaluation(history, (scenario,),
                                                (case.execution,)).to_dict()
    row = report['paper_attempts'][0]
    selected, totals = oracle_expectation()
    assert row['status'] == 'paper_scenario_ready' and row['selected_side'] == selected
    for field, expected in totals.items():
        assert D(row['assumed_totals'][field]) == expected, field

    receipt = stored_receipt(case.request, case.execution, scenario)
    review, _ = confirmation(case.request, case.execution, case.raw, True)
    settled = settle((case.record,), {scenario.record_id: receipt},
                     (review.outcome,), {review.outcome.condition_id: review},
                     case.request.forecast_cutoff_at + timedelta(minutes=2))
    srow = settled['attempts'][0]
    assert srow['status'] == 'settled_simulation'
    payout = oracle_payout(selected, True, scenario.requested_size)
    assert D(srow['amounts']['binary_payout']) == payout == D('5')
    assert D(srow['amounts']['settled_pnl_lower_bound']) \
        == payout - totals['assumed_total_cost_upper_bound']
    # The settled cost bound is exactly the paper capture's stored bound.
    assert srow['amounts']['assumed_total_cost_upper_bound'] \
        == row['assumed_totals']['assumed_total_cost_upper_bound']


# ---------------------------------------------------------------------------
# 2. Failure: a protocol failure can never become settled gains.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('kind', ['codex', 'claude'])
@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
def test_failed_protocol_never_completes_research_or_settles(monkeypatch, kind, team):
    case = chain_case(monkeypatch, n=120 if team == 'crypto_btc' else 121,
                      team=team, kind=kind,
                      client=ProtocolScriptedClient(kind, fail_at=0))
    research = case.record.run.research
    assert research.status == 'failed' and research.reason_code == 'model_failed'
    assert research.total_tokens == 0  # no structured reply was ever accounted

    scenario = paper_scenario(case.request, case.record, case.raw)
    history = ResearchEvaluationReport((case.record,), (), scenario.decision_at)
    report = paper_core.ResearchPaperEvaluation(history, (scenario,), ()).to_dict()
    row = report['paper_attempts'][0]
    # The failed attempt stays in the report; it is never deleted.
    assert row['status'] == 'not_simulated' and row['reason_code'] == 'research_failed'
    assert report['attempt_count'] == 1 and report['ready_scenario_count'] == 0

    receipt = stored_receipt(case.request, case.execution, scenario)
    # A crypto confirmation cannot even be built over failed research.
    with pytest.raises(ValueError, match='settlement_completed_crypto_forecast_required'):
        confirmation(case.request, case.execution, case.raw, True)
    # With a direct outcome present but no review, the failed row still cannot
    # settle: it is classified research_not_selected with amounts None.
    outcome = direct_outcome(case.request)
    settled = settle((case.record,), {scenario.record_id: receipt}, (outcome,), {},
                     case.request.forecast_cutoff_at + timedelta(minutes=5))
    srow = settled['attempts'][0]
    assert srow['status'] == 'research_not_selected' and srow['amounts'] is None
    assert settled['status_counts'] == dict(
        paper_evidence_missing=0, research_not_selected=1, paper_not_selected=0,
        outcome_pending=0, crypto_confirmation_required=0, settled_simulation=0)


@pytest.mark.parametrize('team,actual', [('crypto_btc', True), ('crypto_btc', False),
                                         ('crypto_eth', True), ('crypto_eth', False)])
def test_unknown_confirmation_is_required_not_zero_payout(team, actual):
    """Composition invariant at the settlement read: an outcome without a
    durable confirmation is 'crypto_confirmation_required' with amounts None —
    unknown never decays into a NO side, a zero fee or a zero payout."""
    history, receipt, review, execution, _ = settlement_case(
        n=131 if team == 'crypto_btc' else 132, team=team, actual_yes=actual)
    scenario = receipt.scenario
    settled = settle((execution.record,), {scenario.record_id: receipt},
                     (review.outcome,), {}, history.generated_at)
    row = settled['attempts'][0]
    assert row['status'] == 'crypto_confirmation_required'
    assert row['amounts'] is None and row['review_id'] is None
    group = settled['groups'][0]
    assert group['settled_count'] == 0
    assert group['settled_pnl_lower_bound_sum'] is None
    assert group['binary_payout_sum'] is None
    assert settled['status_counts']['crypto_confirmation_required'] == 1


# ---------------------------------------------------------------------------
# 3. Stop arrival order composed with the audited runner and settlement.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('kind', ['codex', 'claude'])
@pytest.mark.parametrize('where', ['after-first-reply', 'inside-audit-window'])
def test_stop_arrival_order_composes_with_audit_and_exclusion(monkeypatch, kind, where):
    stop = ResearchDispatchStop()
    if where == 'after-first-reply':
        # Stop raised by the client AFTER its first decoded reply: the second
        # call is refused by the OUTER admission check, before any audit start.
        case = chain_case(monkeypatch, n=140, team='crypto_btc', kind=kind,
                          audited=True, stop=stop,
                          client=ProtocolScriptedClient(kind, stop_after=0, stop=stop))
        assert case.harness.events == ['authorization', 'claim', 'start_commit',
                                       'factory', 'outcome_returned', 'capture']
        assert [s.call_number for s in case.harness.starts] == [1]
        assert [o.status for o in case.harness.outcomes] == ['returned']
        snapshot = audit_snapshot(case.request.record_id, case.harness)
        assert snapshot['reported_tokens_known_subset'] == EXPECTED_PER_CALL[kind][0]
        assert snapshot['unknown_usage_call_count'] == 0
    else:
        # Stop lands INSIDE the audited window (between the committed start and
        # the provider call): a terminal 'failed' audit row is written for the
        # start, usage stays unknown, and the provider is never entered.
        case = chain_case(monkeypatch, n=141, team='crypto_btc', kind=kind,
                          audited=True, stop=stop, stop_in_factory=stop)
        assert case.harness.events == ['authorization', 'claim', 'start_commit',
                                       'factory', 'outcome_failed', 'capture']
        assert case.client.seen == []  # provider never entered
        assert [s.call_number for s in case.harness.starts] == [1]
        assert [o.status for o in case.harness.outcomes] == ['failed']
        assert case.harness.outcomes[0].reported_total_tokens is None
        snapshot = audit_snapshot(case.request.record_id, case.harness)
        assert snapshot['validated_reply_count'] == 0
        assert snapshot['reported_tokens_known_subset'] is None  # unknown, not zero
        assert snapshot['unknown_usage_call_count'] == 1
    assert snapshot['actual_billed_micros'] is None
    assert case.record.run.research.reason_code == 'model_failed'
    # Replay after the stop is inert: with the stop active the runner takes the
    # unavailable path (inspect the committed result, no model, no new claim).
    case.harness.events.clear()
    replay = runner.run_uncapped_research_with_psycopg(
        'synthetic', request=case.request, authorization=approval((case.request,)),
        model_factory=lambda _: pytest.fail('no model after stop'),
        allow_model_calls=True, allow_uncapped_costs=True, stop=stop,
        require_durable_audit=True)
    assert replay == case.execution
    assert case.harness.events == ['authorization', 'inspect']
    # The stopped research is excluded from settlement gains.
    scenario = paper_scenario(case.request, case.record, case.raw)
    receipt = stored_receipt(case.request, case.execution, scenario)
    outcome = direct_outcome(case.request, salt='d')
    settled = settle((case.record,), {scenario.record_id: receipt}, (outcome,), {},
                     case.request.forecast_cutoff_at + timedelta(minutes=5))
    assert settled['attempts'][0]['status'] == 'research_not_selected'
    assert settled['attempts'][0]['amounts'] is None


# ---------------------------------------------------------------------------
# 4. Audit composition: decoded totals and unknown usage across both clients.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('kind', ['codex', 'claude'])
def test_audit_rows_carry_decoded_totals_with_independent_cache_arithmetic(
        monkeypatch, kind):
    case = chain_case(monkeypatch, n=150, team='crypto_eth', kind=kind, audited=True)
    starts, outcomes = case.harness.starts, case.harness.outcomes
    assert [s.call_number for s in starts] == [1, 2]
    assert [o.status for o in outcomes] == ['returned', 'returned']
    # Independent expressions per client convention (subset vs additive).
    assert [o.reported_total_tokens for o in outcomes] == list(EXPECTED_PER_CALL[kind])
    snapshot = audit_snapshot(case.request.record_id, case.harness)
    # The audit-known token subset equals the loop-accounted research total.
    assert snapshot['reported_tokens_known_subset'] == sum(EXPECTED_PER_CALL[kind])
    assert snapshot['reported_tokens_known_subset'] == case.record.run.research.total_tokens
    assert snapshot['all_calls_have_terminal_record'] is True
    assert snapshot['provider_submission_count'] is None
    assert snapshot['actual_billed_micros'] is None


@pytest.mark.parametrize('kind', ['codex', 'claude'])
def test_first_call_failure_keeps_unknown_audit_usage_and_zero_research_tokens(
        monkeypatch, kind):
    case = chain_case(monkeypatch, n=151, team='crypto_btc', kind=kind, audited=True,
                      client=ProtocolScriptedClient(kind, fail_at=0))
    assert [o.status for o in case.harness.outcomes] == ['failed']
    assert case.harness.outcomes[0].reported_total_tokens is None
    assert case.harness.outcomes[0].reply_sha256 is None
    snapshot = audit_snapshot(case.request.record_id, case.harness)
    assert snapshot['validated_reply_count'] == 0
    assert snapshot['reported_tokens_known_subset'] is None  # unknown, not zero
    assert case.record.run.research.total_tokens == 0


# ---------------------------------------------------------------------------
# 5. Mixed cohort: the full denominator ladder and conserved group sums.
# ---------------------------------------------------------------------------

def test_mixed_cohort_keeps_every_denominator_and_failed_samples(monkeypatch):
    cases = {}

    def build(key, n, team, kind, *, fail=False):
        client = ProtocolScriptedClient(kind, fail_at=0) if fail else None
        cases[key] = chain_case(monkeypatch, n=n, team=team, kind=kind, audited=True,
                                client=client)

    build('btc-codex-win', 160, 'crypto_btc', 'codex')
    build('btc-claude-loss', 161, 'crypto_btc', 'claude')
    build('btc-codex-fail', 162, 'crypto_btc', 'codex', fail=True)
    build('eth-codex-pending', 163, 'crypto_eth', 'codex')
    build('eth-claude-review-missing', 164, 'crypto_eth', 'claude')
    build('eth-claude-fail', 165, 'crypto_eth', 'claude', fail=True)

    win, loss = cases['btc-codex-win'], cases['btc-claude-loss']
    pending = cases['eth-codex-pending']
    review_missing = cases['eth-claude-review-missing']

    scenarios = {key: paper_scenario(c.request, c.record, c.raw)
                 for key, c in cases.items()}
    receipts = {key: stored_receipt(c.request, c.execution, scenarios[key])
                for key, c in cases.items()}

    # Denominator ladder, rung by rung, from independent observations.
    attempts = len(cases)  # 6 research attempts claimed
    assert sum(c.harness.events.count('claim') for c in cases.values()) == attempts
    starts = [s for c in cases.values() for s in c.harness.starts]
    returned = [o for c in cases.values() for o in c.harness.outcomes
                if o.status == 'returned']
    failed_calls = [o for c in cases.values() for o in c.harness.outcomes
                    if o.status == 'failed']
    structured_replies = len(returned)  # legal protocol replies, both clients
    valid_research = [c for c in cases.values()
                      if c.record.run.research.status == 'completed']
    assert structured_replies == 8  # 4 successes x 2 calls, independent count
    assert len(failed_calls) == 2
    assert len(starts) == attempts + structured_replies // 2 == 10
    assert len(valid_research) == 4

    papers_history = ResearchEvaluationReport(
        tuple(c.record for c in cases.values()), (),
        max(s.decision_at for s in scenarios.values()))
    eligible = {c.request.record_id for c in valid_research}
    paper_report = paper_core.ResearchPaperEvaluation(
        papers_history, tuple(scenarios.values()),
        tuple(c.execution for c in cases.values()
              if c.request.record_id in eligible)).to_dict()
    assert paper_report['attempt_count'] == 6 == attempts
    assert paper_report['ready_scenario_count'] == 4 == len(valid_research)
    by_record = {row['record_id']: row for row in paper_report['paper_attempts']}
    for key in ('btc-codex-fail', 'eth-claude-fail'):
        row = by_record[cases[key].request.record_id]
        assert row['status'] == 'not_simulated' and row['reason_code'] == 'research_failed'

    reviews, outcomes = {}, []
    for case_, actual in ((win, True), (loss, False)):
        review, _ = confirmation(case_.request, case_.execution, case_.raw, actual)
        reviews[review.outcome.condition_id] = review
        outcomes.append(review.outcome)
    # Outcome exists for the fifth case; its confirmation review is withheld.
    outcomes.append(direct_outcome(review_missing.request, salt='b'))

    settled = settle([c.record for c in cases.values()],
                     {s.record_id: receipts[key] for key, s in scenarios.items()},
                     outcomes, reviews, max(o.recorded_at for o in outcomes))
    rows = {row['record_id']: row for row in settled['attempts']}
    assert settled['attempt_count'] == 6 and settled['paper_evidence_count'] == 6
    assert settled['status_counts'] == dict(
        paper_evidence_missing=0, research_not_selected=2, paper_not_selected=0,
        outcome_pending=1, crypto_confirmation_required=1, settled_simulation=2)

    # Rung 'simulatable': the four completed researches all produced ready
    # paper scenarios (asserted above via ready_scenario_count). Rung
    # 'verifiable settlement': exactly the two with the full confirmation chain
    # settle; independent oracle expectations for payout and cost.
    _, totals = oracle_expectation()
    win_row, loss_row = rows[win.request.record_id], rows[loss.request.record_id]
    assert win_row['status'] == loss_row['status'] == 'settled_simulation'
    assert D(win_row['amounts']['binary_payout']) == D('5')
    assert D(loss_row['amounts']['binary_payout']) == D('0')
    for row in (win_row, loss_row):
        assert D(row['amounts']['assumed_total_cost_upper_bound']) \
            == totals['assumed_total_cost_upper_bound']
        assert D(row['amounts']['settled_pnl_lower_bound']) \
            == D(row['amounts']['binary_payout']) - totals['assumed_total_cost_upper_bound']
    assert rows[pending.request.record_id]['status'] == 'outcome_pending'
    assert rows[pending.request.record_id]['amounts'] is None
    assert rows[review_missing.request.record_id]['status'] == 'crypto_confirmation_required'
    assert rows[review_missing.request.record_id]['amounts'] is None
    for key in ('btc-codex-fail', 'eth-claude-fail'):
        assert rows[cases[key].request.record_id]['status'] == 'research_not_selected'
        assert rows[cases[key].request.record_id]['amounts'] is None

    # Group denominators per team and Decimal conservation of the sums.
    groups = {g['team_id']: g for g in settled['groups']}
    assert groups['crypto_btc']['attempt_count'] == 3
    assert groups['crypto_btc']['settled_count'] == 2
    assert groups['crypto_eth']['attempt_count'] == 3
    assert groups['crypto_eth']['settled_count'] == 0
    # Independent expression: payout_sum - cost_sum == pnl_sum (prec-64 context,
    # ROUND_HALF_EVEN; every input already quantized to 1e-6 by the fee model).
    btc = groups['crypto_btc']
    payout_sum, cost_sum = D('5') + D('0'), 2 * totals['assumed_total_cost_upper_bound']
    assert D(btc['binary_payout_sum']) == payout_sum
    assert D(btc['assumed_total_cost_upper_bound_sum']) == cost_sum
    assert D(btc['settled_pnl_lower_bound_sum']) == payout_sum - cost_sum
    for name in ('binary_payout_sum', 'assumed_total_cost_upper_bound_sum',
                 'settled_pnl_lower_bound_sum'):
        assert groups['crypto_eth'][name] is None  # no settled row: no invented zero


# ---------------------------------------------------------------------------
# 6. Source / time / raw-hash mismatch rejections at the settlement read.
# ---------------------------------------------------------------------------

def amounts_tuple():
    history, receipt, review, execution, _ = settlement_case(n=201, team='crypto_btc',
                                                             actual_yes=True)
    decision = json.loads(receipt.result_payload)
    return receipt, decision, review.outcome, review, execution.record


def call_amounts(receipt, decision, outcome, review, record):
    with localcontext(_CONTEXT):
        return settlement_core._amounts(receipt, decision, outcome, review, record)


def test_settlement_amounts_accept_the_legal_control():
    receipt, decision, outcome, review, record = amounts_tuple()
    amounts = call_amounts(receipt, decision, outcome, review, record)
    assert amounts['selected_side'] == 'yes'
    assert D(amounts['binary_payout']) == D('5')
    assert D(amounts['settled_pnl_lower_bound']) == \
        D(amounts['binary_payout']) - D(amounts['assumed_total_cost_upper_bound'])


def retasked_record(record, *, question=None, criteria=None):
    """A type-valid record whose task TERMS diverge from the confirmed snapshot
    (the settlement read must catch the divergence itself)."""
    changes = {}
    if question is not None:
        changes['question'] = question
    if criteria is not None:
        changes['resolution_criteria'] = criteria
    intake = replace(record.run.intake, task=replace(record.run.intake.task, **changes))
    run = replace(record.run, intake=intake)
    return ResearchEvaluationRecord(record.record_id, record.model_id,
                                    record.protocol_version, record.recorded_at, run)


@pytest.mark.parametrize('mutation', [
    'terms-question', 'terms-description', 'outcome-condition', 'outcome-review-divergence',
    'cutoff-mismatch', 'receipt-after-cutoff', 'non-pending-capture', 'foreign-first-record',
    'side-none', 'cost-decomposition',
])
def test_settlement_amounts_reject_source_time_hash_and_cost_mismatches(mutation):
    receipt, decision, outcome, review, record = amounts_tuple()
    kwargs = dict(receipt=receipt, decision=decision, outcome=outcome, review=review,
                  record=record)
    if mutation == 'terms-question':
        kwargs['record'] = retasked_record(record, question='Divergent synthetic question?')
    elif mutation == 'terms-description':
        kwargs['record'] = retasked_record(
            record, criteria='Divergent synthetic resolution rules.')
    elif mutation == 'outcome-condition':
        # Compose the confirmation of market A with the record of market B: a
        # type-valid cross-market mix only the read's own check can reject.
        _, _, _, other_execution, _ = settlement_case(n=202, team='crypto_btc',
                                                      actual_yes=True)
        kwargs['record'] = other_execution.record
    elif mutation == 'outcome-review-divergence':
        # The outcome passed to the read diverges from the review's saved one
        # (raw binding mismatch between the two records being composed).
        kwargs['outcome'] = ResearchEvaluationOutcome(
            outcome.condition_id, outcome.market_slug, outcome.forecast_cutoff_at,
            outcome.resolved_at, outcome.recorded_at, not outcome.actual_yes,
            outcome.source_reference, outcome.source_content_sha256)
    elif mutation == 'cutoff-mismatch':
        # forecast_cutoff_at is not part of the review<->outcome type bindings,
        # so a shifted cutoff is type-legal and only the read rejects it.
        shifted = ResearchEvaluationOutcome(
            outcome.condition_id, outcome.market_slug,
            outcome.forecast_cutoff_at + timedelta(seconds=1), outcome.resolved_at,
            outcome.recorded_at, outcome.actual_yes, outcome.source_reference,
            outcome.source_content_sha256)
        kwargs['outcome'] = shifted
        kwargs['review'] = replace(review, outcome=shifted)
    elif mutation == 'receipt-after-cutoff':
        # Cutoff pulled back to the receipt's own recorded time: the paper
        # capture was then NOT strictly pre-cutoff (receipt.recorded_at <
        # outcome.forecast_cutoff_at fails). Every review<->outcome binding
        # (actual_yes, resolved_at, reference, hash) stays intact.
        shifted = ResearchEvaluationOutcome(
            outcome.condition_id, outcome.market_slug, receipt.recorded_at,
            outcome.resolved_at, outcome.recorded_at, outcome.actual_yes,
            outcome.source_reference, outcome.source_content_sha256)
        kwargs['outcome'] = shifted
        kwargs['review'] = replace(review, outcome=shifted)
    elif mutation == 'non-pending-capture':
        kwargs['decision'] = dict(decision, original_reason_code='scored')
    elif mutation == 'foreign-first-record':
        kwargs['receipt'] = replace(receipt, first_record_id='foreign-record')
    elif mutation == 'side-none':
        kwargs['decision'] = dict(decision, selected_side='none')
    elif mutation == 'cost-decomposition':
        totals = dict(decision['assumed_totals'])
        totals['assumed_total_cost_upper_bound'] = str(
            D(totals['assumed_total_cost_upper_bound']) + D('0.000001'))
        kwargs['decision'] = dict(decision, assumed_totals=totals)
    with pytest.raises(ValueError, match='research_paper_settlement'):
        call_amounts(**kwargs)


def test_scenario_bound_to_changed_record_hash_is_rejected_by_paper_evaluation():
    """Changed input at the paper binding seam: a scenario is bound to the
    ORIGINAL record content hash; a changed record with the same record_id
    cannot reuse it."""
    request, execution, scenario, _, _ = synthetic_case(n=210, team='crypto_btc',
                                                        at=NOW - timedelta(seconds=30))
    research = execution.record.run.research
    divergent = ResearchEvaluationRecord(
        execution.record.record_id, execution.record.model_id,
        execution.record.protocol_version, execution.record.recorded_at,
        replace(execution.record.run,
                research=replace(research, probability_yes=D('0.71'))))
    assert divergent.content_sha256 != scenario.record_sha256
    history = ResearchEvaluationReport((divergent,), (), scenario.decision_at)
    with pytest.raises(ValueError, match='research_paper_record_binding_mismatch'):
        paper_core.ResearchPaperEvaluation(history, (scenario,), (execution,))


# ---------------------------------------------------------------------------
# 7. First request / verbatim replay / changed input at the claim seam.
# ---------------------------------------------------------------------------

def test_verbatim_replay_reproduces_byte_identical_paper_and_settlement(monkeypatch):
    case = chain_case(monkeypatch, n=220, team='crypto_btc', kind='codex')
    scenario = paper_scenario(case.request, case.record, case.raw)
    receipt = stored_receipt(case.request, case.execution, scenario)
    review, _ = confirmation(case.request, case.execution, case.raw, True)
    first_row = settle((case.record,), {scenario.record_id: receipt},
                       (review.outcome,), {review.outcome.condition_id: review},
                       case.request.forecast_cutoff_at + timedelta(minutes=2))['attempts'][0]

    case.harness.events.clear()
    replayed = runner.run_uncapped_research_with_psycopg(
        'synthetic', request=case.request, authorization=approval((case.request,)),
        model_factory=lambda _: pytest.fail('replay must not start a model'),
        allow_model_calls=True, allow_uncapped_costs=True)
    assert replayed == case.execution and case.harness.events == ['replay']

    history = ResearchEvaluationReport((replayed.record,), (), scenario.decision_at)
    recomputed = dump(paper_store._result_for(history, scenario, replayed))
    assert recomputed == receipt.result_payload  # byte-identical paper result
    second_row = settle((replayed.record,), {scenario.record_id: receipt},
                        (review.outcome,), {review.outcome.condition_id: review},
                        case.request.forecast_cutoff_at + timedelta(minutes=2))['attempts'][0]
    assert second_row == first_row


@pytest.mark.parametrize('change_id', ['changed-limits', 'changed-protocol',
                                       'changed-cutoff'])
def test_changed_input_same_record_id_is_rejected_before_any_claim(monkeypatch, change_id):
    request, _, _, _, _ = synthetic_case(n=221, team='crypto_eth',
                                         at=NOW - timedelta(seconds=30))
    harness = Harness(monkeypatch)
    changes = {
        'changed-limits': dict(max_start_delay_seconds=299),
        'changed-protocol': dict(protocol_version='v2'),
        'changed-cutoff': dict(forecast_cutoff_at=request.forecast_cutoff_at
                               + timedelta(seconds=1)),
    }
    changed = replace(request, **changes[change_id])
    assert changed.payload != request.payload  # the edit really changes the input
    with pytest.raises(ValueError, match='research_uncapped_request_mismatch'):
        runner.run_uncapped_research_with_psycopg(
            'synthetic', request=changed, authorization=approval((request,)),
            model_factory=lambda _: pytest.fail('factory must not start'),
            allow_model_calls=True, allow_uncapped_costs=True)
    assert not harness.events  # rejected before the claim seam


# ---------------------------------------------------------------------------
# 8. Forward-time composition: research after its own cutoff cannot settle.
# ---------------------------------------------------------------------------

def test_request_past_cutoff_fails_admission_and_never_settles(monkeypatch):
    request, _, _, raw, _ = synthetic_case(n=230, team='crypto_btc',
                                           at=NOW - timedelta(seconds=30))
    # Cutoff one second BEFORE the runner's frozen admission clock (NOW+3s).
    request = replace(request, forecast_cutoff_at=NOW + timedelta(seconds=2))
    harness = AuditHarness(monkeypatch)
    client = ProtocolScriptedClient('codex')
    execution = runner.run_uncapped_research_with_psycopg(
        'synthetic', request=request, authorization=approval((request,)),
        model_factory=lambda _: client, allow_model_calls=True,
        allow_uncapped_costs=True, require_durable_audit=True)
    assert execution.record.run.research.reason_code == 'model_failed'
    # Admission failed BEFORE the audit window: no start row, no invented
    # outcome, no structured reply.
    assert harness.starts == [] and harness.outcomes == []
    assert audit_snapshot(request.record_id, harness)['audit_coverage'] == 'no_audited_calls'
    # No paper receipt can exist for post-cutoff work, so settlement sees the
    # attempt as missing paper evidence, never as settled.
    settled = settle((execution.record,), {}, (direct_outcome(request, salt='c'),), {},
                     request.forecast_cutoff_at + timedelta(seconds=120))
    row = settled['attempts'][0]
    assert row['status'] == 'paper_evidence_missing' and row['amounts'] is None


# ---------------------------------------------------------------------------
# 9. Decimal composition properties with stated preconditions.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
def test_win_loss_pair_under_identical_books_conserves_pnl(team):
    """Preconditions: identical synthetic market, books, size, costs, gates and
    the SAME selected side (asserted); only the confirmed outcome differs. The
    two settled bounds then satisfy pnl_win + pnl_loss == size - 2 * cost bound
    — an independent conservation identity for the composition (payouts sum to
    the size), evaluated in the settlement context (prec 64, ROUND_HALF_EVEN,
    inputs quantized to 1e-6)."""
    rows = []
    for actual in (True, False):
        history, receipt, review, execution, _ = settlement_case(
            n=240 if team == 'crypto_btc' else 241, team=team, actual_yes=actual)
        scenario = receipt.scenario
        settled = settle((execution.record,), {scenario.record_id: receipt},
                         (review.outcome,), {review.outcome.condition_id: review},
                         history.generated_at)
        row = settled['attempts'][0]
        assert row['status'] == 'settled_simulation'
        rows.append(row)
    win, loss = rows
    assert win['amounts']['selected_side'] == loss['amounts']['selected_side']
    cost = D(win['amounts']['assumed_total_cost_upper_bound'])
    assert D(loss['amounts']['assumed_total_cost_upper_bound']) == cost
    pnl_win = D(win['amounts']['settled_pnl_lower_bound'])
    pnl_loss = D(loss['amounts']['settled_pnl_lower_bound'])
    size = D(win['amounts']['requested_size'])
    assert pnl_win == size - cost and pnl_loss == D('0') - cost
    assert pnl_win + pnl_loss == size - 2 * cost  # conservation identity
    # Rounding property (conditions above): every emitted numeric bound has at
    # most six fractional digits — the scenario fee model's 1e-6 quantum
    # survives the settlement composition unchanged.
    for row in rows:
        for value in row['amounts'].values():
            if isinstance(value, str) and re.fullmatch(r'-?\d+\.\d+', value):
                assert len(value.split('.')[1]) <= 6, value


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
def test_higher_slippage_never_improves_settled_pnl_bound(team):
    """Preconditions: identical market, books, fair probability, gates, size,
    selected side (asserted present) and confirmed outcome; only the slippage
    cost assumption varies. The settled cost bound is non-decreasing and the
    settled PnL lower bound is non-increasing (the conservative direction is
    preserved through capture -> settlement). Applies to this one fixed trade
    choice only; nothing is asserted across different trade choices."""
    previous = None
    for slip in ('0', '0.001', '0.01', '0.05'):
        history, receipt, review, execution, _ = settlement_case(
            n=250 if team == 'crypto_btc' else 251, team=team, actual_yes=True,
            costs=Costs(D('.02'), D(slip), D(0), D(0), D(0), D(0)))
        scenario = receipt.scenario
        settled = settle((execution.record,), {scenario.record_id: receipt},
                         (review.outcome,), {review.outcome.condition_id: review},
                         history.generated_at)
        row = settled['attempts'][0]
        assert row['status'] == 'settled_simulation'
        assert row['amounts']['selected_side'] in ('yes', 'no')
        cost = D(row['amounts']['assumed_total_cost_upper_bound'])
        pnl = D(row['amounts']['settled_pnl_lower_bound'])
        assert D(row['amounts']['binary_payout']) == scenario.requested_size
        if previous is not None:
            assert cost >= previous[0]
            assert pnl <= previous[1]
        previous = (cost, pnl)
