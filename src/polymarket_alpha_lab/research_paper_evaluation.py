"""Join saved research to the existing cost gate and depth-walking paper fill.

Hypothetical supplied-book replay, not durable forward trading evidence. Never
pool profits, choose using outcomes, manufacture forecasts or weaken history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, localcontext
from datetime import timedelta
import json

from polymarket_alpha_lab.cost_aware_event_strategy import (
    PaperCostAwareEventMarketSnapshot, build_paper_cost_aware_event_strategy_report,
)
from polymarket_alpha_lab.paper import PaperOrder, _walk_levels, simulate_order_book_fill
from polymarket_alpha_lab.research_crypto_observation import assess_crypto_observation_time
from polymarket_alpha_lab.research_execution import CapturedResearchExecution
from polymarket_alpha_lab.research_execution_psycopg import (
    _lookup, load_captured_research_evaluation_with_psycopg,
)
from polymarket_alpha_lab.research_capture_psycopg import _local_transaction
from polymarket_alpha_lab.research_paper_inputs import MATH_CONTEXT, _json_value, copy_scenarios
from polymarket_alpha_lab.team_research_agent_types import hard_flags, strict_json
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationReport
from polymarket_alpha_lab.team_research_intake import _timestamp

ZERO = Decimal('0')
ONE = Decimal('1')


class _Rejected(ValueError):
    pass


def _original(scenario, execution, record):
    if type(execution) is not CapturedResearchExecution:
        raise ValueError('paper_original_execution_missing')
    execution = replace(execution)
    if (execution.request.record_id != scenario.record_id
            or execution.request.content_sha256 != scenario.request_sha256
            or execution.record is None or execution.record != record
            or record.content_sha256 != scenario.record_sha256):
        raise ValueError('paper_original_binding_mismatch')
    return execution


def _books(s, e, generated_at):
    record, intake = e.record, e.request.intake
    if intake.team_id not in ('crypto_btc', 'crypto_eth'):
        raise _Rejected('unsupported_team')
    if not record.recorded_at <= s.simulated_at <= generated_at:
        raise _Rejected('simulation_time_invalid')
    for at in (s.market.fetched_at, s.yes_book.captured_at, s.no_book.captured_at):
        if not record.recorded_at <= at <= s.simulated_at:
            raise _Rejected('snapshot_time_invalid')
        if (s.simulated_at - at).total_seconds() > s.max_snapshot_age_seconds:
            raise _Rejected('snapshot_stale')
    raw = strict_json(s.market.raw_json.decode('utf-8'))
    if type(raw) is not dict:
        raise _Rejected('market_invalid')
    task = intake.task
    if (s.market.market_slug, raw.get('slug'), raw.get('conditionId'), raw.get('question'), raw.get('description')) != (
            intake.market_slug, intake.market_slug, intake.condition_id, task.question, task.resolution_criteria):
        raise _Rejected('market_or_terms_mismatch')
    if (raw.get('active') is not True or raw.get('closed') is not False
            or raw.get('acceptingOrders') is not True or raw.get('enableOrderBook') is False):
        raise _Rejected('market_not_open')
    observation = assess_crypto_observation_time(team_id=intake.team_id, question=task.question,
        resolution_criteria=task.resolution_criteria, market_slug=intake.market_slug,
        as_of=s.simulated_at, forecast_cutoff_at=e.request.forecast_cutoff_at,
        scheduled_end_at=_timestamp(raw.get('endDate')))
    if not observation.new_launch_time_eligible:
        raise _Rejected('observation_time_ineligible')
    outcomes, tokens = raw.get('outcomes'), raw.get('clobTokenIds')
    outcomes = strict_json(outcomes) if type(outcomes) is str else outcomes
    tokens = strict_json(tokens) if type(tokens) is str else tokens
    if (type(outcomes) is not list or sorted(outcomes) != ['No', 'Yes'] or type(tokens) is not list
            or len(tokens) != 2 or any(type(t) is not str for t in tokens) or len(set(tokens)) != 2):
        raise _Rejected('binary_tokens_invalid')
    mapping = dict(zip(outcomes, tokens))
    books = (s.yes_book.normalized(), s.no_book.normalized())
    for label, evidence, book in zip(('Yes', 'No'), (s.yes_book, s.no_book), books):
        if book.token_id != mapping[label] or strict_json(evidence.raw_json.decode()).get('market') != intake.condition_id:
            raise _Rejected('book_identity_mismatch')
    return books, observation


def _gate(s, record, books, fills):
    # Screen at the WORST consumed ask, not a midpoint or rounded VWAP. Partial
    # fills remain visible but cannot be selected as a full-size experiment.
    bids = [b.best_bid for b in books]
    asks = [b.best_ask for b in books]
    if any(x is None for x in (*bids, *asks)):
        raise _Rejected('missing_executable_quote')
    snapshot = PaperCostAwareEventMarketSnapshot(record.run.intake.market_slug,
        record.run.intake.task.question, record.run.research.probability_yes, record.run.research.confidence,
        bids[0], fills[0].worst_price, fills[0].filled_size if fills[0].is_complete else ZERO,
        bids[1], fills[1].worst_price, fills[1].filled_size if fills[1].is_complete else ZERO,
        max(asks[0] - bids[0], asks[1] - bids[1]), s.resolution_risk)
    return build_paper_cost_aware_event_strategy_report(snapshot,
        cost_assumptions=s.costs, config=s.config, generated_at=s.simulated_at)


def _costs(s, book, fill, probability):
    filled, notional, _ = _walk_levels(s.shares, book.asks)
    if filled != fill.filled_size:
        raise ValueError('paper_fill_accounting_mismatch')
    # Reuse the SAME legacy fee reducer for each consumed level. All additional
    # cost assumptions are cash-equivalent scenario costs, not provider invoices.
    remaining, fee = filled, ZERO
    for level in book.asks:
        if remaining == 0:
            break
        size = min(remaining, level.size)
        one_level = PaperCostAwareEventMarketSnapshot('fee-scenario', 'Scenario only', probability, ONE,
            None, level.price, size, None, None, None, ZERO, ZERO)
        priced = build_paper_cost_aware_event_strategy_report(one_level,
            cost_assumptions=s.costs, config=s.config, generated_at=s.simulated_at)
        fee += size * priced.yes_result.fee_cost_per_share
        remaining -= size
    non_fee = filled * s.costs.non_fee_cost_per_share
    total = notional + fee + non_fee
    return dict(gross_notional=notional, fee_estimate=fee, additional_cost_estimate=non_fee,
        total_cost_estimate=total, expected_payout=filled * probability,
        expected_net=filled * probability - total,
        depth_slippage_not_added_twice=notional - filled * book.best_ask,
        maximum_loss_estimate=total, fill_average_is_display_only=True)


def _one(s, e, generated_at, outcome):
    result = dict(scenario_sha256=s.content_sha256, simulated_at=s.simulated_at, requested_shares=s.shares,
        market_raw_sha256=s.market.content_sha256, yes_book_raw_sha256=s.yes_book.content_sha256,
        no_book_raw_sha256=s.no_book.content_sha256, cost_reference=s.cost_reference,
        costs=asdict(s.costs), config=asdict(s.config), resolution_risk=s.resolution_risk,
        max_snapshot_age_seconds=s.max_snapshot_age_seconds,
        selected_side='none', cost_gate=None, fills=None, accounting=None,
        payout=None, net_after_outcome=None)
    try:
        books, observation = _books(s, e, generated_at)
        fills = tuple(simulate_order_book_fill(PaperOrder(b.token_id, 'buy', s.shares), b) for b in books)
        result['fills'] = dict(zip(('yes', 'no'), (asdict(f) for f in fills)))
        gate = _gate(s, e.record, books, fills)
        gate_body = asdict(gate)
        gate_body.pop('question')
        result['cost_gate'] = gate_body
        if gate.status != 'paper_review_ready':
            return dict(result, status='not_selected', reason_code=gate.status)
        side = gate.selected_side
        index = ('yes', 'no').index(side)
        probability = e.record.run.research.probability_yes
        probability = probability if side == 'yes' else ONE - probability
        accounting = _costs(s, books[index], fills[index], probability)
        result.update(selected_side=side, accounting=accounting)
        # Selection/size/cost NEVER consumes the outcome. Only this final join
        # uses the original evaluator's visible, scored outcome.
        if outcome is None:
            return dict(result, status='simulated_pending_outcome', reason_code='outcome_pending')
        if (outcome.forecast_cutoff_at != e.request.forecast_cutoff_at
                or outcome.resolved_at < observation.candle_open_at + timedelta(minutes=1)):
            return dict(result, status='simulated_outcome_rejected', reason_code='outcome_time_mismatch')
        payout = fills[index].filled_size if outcome.actual_yes == (side == 'yes') else ZERO
        return dict(result, status='simulated_with_outcome', reason_code='hypothetical_only',
            payout=payout, net_after_outcome=payout - accounting['total_cost_estimate'])
    except _Rejected as error:
        return dict(result, status='rejected_scenario', reason_code=str(error))
    except (ValueError, TypeError, UnicodeError, OverflowError):
        # Malformed untrusted source text is an explicit rejected row, never an
        # invented missing quote or a reason to drop an original research attempt.
        return dict(result, status='rejected_scenario', reason_code='scenario_source_invalid')


def compose_research_paper_evaluation(report, scenarios, executions):
    """Pure in-memory join; a supplied report cannot authenticate DB history."""
    if type(report) is not ResearchEvaluationReport:
        raise ValueError('paper_history_invalid')
    report = replace(report)
    scenarios = copy_scenarios(scenarios)
    if type(executions) is not tuple or len(executions) != len(scenarios):
        raise ValueError('paper_execution_binding_invalid')
    records = {r.record_id: r for r in report.records}
    joined = {}
    for s, e in zip(scenarios, executions):
        if s.record_id not in records:
            raise ValueError('paper_scenario_unknown_record')
        joined[s.record_id] = (s, _original(s, e, records[s.record_id]))
    outcomes = {o.condition_id: o for o in report.outcomes if o.recorded_at <= report.generated_at}
    rows = []
    with localcontext(MATH_CONTEXT):
        for decision in report.decisions:
            row = dict(record_id=decision.record_id, team_id=decision.team_id, model_id=decision.model_id,
                protocol_version=decision.protocol_version, condition_id=decision.condition_id,
                record_sha256=decision.record_sha256, research_reason=decision.reason_code,
                outcome_sha256=decision.outcome_sha256, scenario=None,
                scenario_sha256=joined[decision.record_id][0].content_sha256 if decision.record_id in joined else None)
            if decision.reason_code not in ('scored', 'outcome_pending'):
                row.update(status='excluded_research', reason_code=decision.reason_code)
            elif decision.record_id not in joined:
                row.update(status='missing_scenario', reason_code='no_supplied_books_or_costs')
            else:
                s, e = joined[decision.record_id]
                scenario = _one(s, e, report.generated_at,
                    outcomes.get(decision.condition_id) if decision.reason_code == 'scored' else None)
                row.update(status=scenario['status'], reason_code=scenario['reason_code'], scenario=scenario)
            rows.append(row)
    counts = {status: sum(row['status'] == status for row in rows) for status in sorted({r['status'] for r in rows})}
    body = dict(schema_version='research-paper-replay-v1', research=report.to_dict(), scenarios=rows,
        scenario_count=len(scenarios), record_count=len(report.records), counts=counts,
        accounting_mode='hypothetical_cash_cost_hold_to_resolution', screening_price='worst_consumed_ask',
        fee_mode='existing_six_decimal_per_share_curve', market_fee_verified=False,
        forward_paper_evidence=False, input_authentication_performed=False, scenario_selection_bias_possible=True,
        snapshot_times_are_supplied_assertions=True, venue_execution_constraints_verified=False,
        pooled_profit_computed=False, database_written=False, paper_only=True, report_only=True, readonly=True)
    return json.loads(json.dumps(body, default=_json_value, allow_nan=False))


def evaluate_research_paper_with_psycopg(dsn, *, scenarios, **configuration):
    """Read the original strict complete-history evaluator before any replay.

    A second bounded read retrieves immutable original execution requests. No
    writes/fetches, reduced-history fallback or bypass of incomplete claims.
    """
    scenarios = copy_scenarios(scenarios)
    report = load_captured_research_evaluation_with_psycopg(dsn, **configuration)
    executions = _local_transaction(dsn,
        lambda cursor: tuple(_lookup(cursor, s.record_id) for s in scenarios), readonly=True) if scenarios else ()
    return ResearchPaperReplay(report, scenarios, executions)


@dataclass(frozen=True, slots=True)
class ResearchPaperReplay:
    """Detached inputs, not a caller-provided precomputed profit dictionary."""
    history: ResearchEvaluationReport = field(repr=False)
    scenarios: tuple = field(repr=False)
    executions: tuple = field(repr=False)
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self):
        hard_flags(self)
        if type(self.history) is not ResearchEvaluationReport:
            raise ValueError('paper_history_invalid')
        object.__setattr__(self, 'history', replace(self.history))
        object.__setattr__(self, 'scenarios', copy_scenarios(self.scenarios))
        if type(self.executions) is not tuple or len(self.executions) != len(self.scenarios):
            raise ValueError('paper_execution_binding_invalid')
        records = {r.record_id: r for r in self.history.records}
        copies = []
        for scenario, execution in zip(self.scenarios, self.executions):
            if scenario.record_id not in records:
                raise ValueError('paper_scenario_unknown_record')
            copies.append(_original(scenario, execution, records[scenario.record_id]))
        object.__setattr__(self, 'executions', tuple(copies))

    def to_dict(self):
        checked = replace(self)
        return compose_research_paper_evaluation(checked.history, checked.scenarios, checked.executions)


__all__ = ('ResearchPaperReplay', 'compose_research_paper_evaluation', 'evaluate_research_paper_with_psycopg')
