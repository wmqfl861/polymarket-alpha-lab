"""Offline assembly of original forecasts, book fills and existing cost arithmetic.

Supplied historical scenarios are NOT durable forward-test evidence or orders.
No model, network, file journal, fee discovery, settlement inference or new scorer.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta
from decimal import Context, Decimal, ROUND_CEILING, localcontext
from hashlib import sha256
import json
import re

from polymarket_alpha_lab.normalize import normalize_order_book
from polymarket_alpha_lab.paper import PaperOrder, simulate_order_book_fill, _walk_levels
from polymarket_alpha_lab.paper_probability_side_edge import (
    PaperProbabilitySideEdgeConfig, PaperProbabilitySideEdgeInput,
    build_paper_probability_side_edge_report,
)
from polymarket_alpha_lab.research_crypto_observation import assess_crypto_observation_time
from polymarket_alpha_lab.research_execution import CapturedResearchExecution
from polymarket_alpha_lab.research_resolution import digest, utc
from polymarket_alpha_lab.team_research_agent_types import hard_flags, identifier, integer, strict_json
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationReport
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, _timestamp

MAX_SCENARIOS = 100
MAX_BOOK_BYTES = 65536
MAX_INPUT_BYTES = 4194304
CONTEXT = Context(prec=64)
Q = Decimal('0.000001')
ZERO = Decimal(0)
ONE = Decimal(1)


def number(value, *, maximum=Decimal('1000000'), positive=False):
    """Closed finite six-place arithmetic domain; never silently round inputs."""
    if (type(value) is not Decimal or not value.is_finite()
            or len(value.as_tuple().digits) > 13 or not -6 <= value.as_tuple().exponent <= 6
            or value < 0 or value > maximum or (positive and value == 0)):
        raise ValueError('research_paper_number_invalid')
    with localcontext(CONTEXT):
        if value.quantize(Q) != value:
            raise ValueError('research_paper_number_invalid')
    return value


def json_value(value):
    if type(value) is Decimal:
        return format(value, 'f')
    if type(value) is datetime:
        return value.isoformat()
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) in (tuple, list):
        return [json_value(v) for v in value]
    if type(value) is dict:
        return {k: json_value(v) for k, v in value.items()}
    return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}


def _hash(value):
    return sha256(json.dumps(json_value(value), sort_keys=True, separators=(',', ':'),
                            ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchPaperCosts:
    """Explicit costs per filled share in payout units; NOT an exchange fee rate.

    Extra slippage excludes the order-book walk already paid in entry notional.
    Risk/time/capital are modeled economic drags, not asserted cash invoices.
    """
    fee: Decimal
    extra_slippage: Decimal
    funding: Decimal
    finalization: Decimal
    time: Decimal
    risk: Decimal
    capital: Decimal

    def __post_init__(self):
        for f in fields(self):
            number(getattr(self, f.name))


@dataclass(frozen=True, slots=True)
class ResearchPaperScenario:
    record_id: str
    request_sha256: str
    decision_at: datetime
    market: GammaMarketSnapshot = field(repr=False)
    book_json: bytes = field(repr=False)
    book_captured_at: datetime
    side: str
    requested_shares: Decimal
    costs: ResearchPaperCosts
    cost_reference_sha256: str
    settings_version: str
    min_net_edge: Decimal
    min_confidence: Decimal
    max_spread: Decimal
    max_entry_cost: Decimal
    max_snapshot_age_seconds: int
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self):
        hard_flags(self)
        identifier('record_id', self.record_id)
        identifier('settings_version', self.settings_version)
        digest(self.request_sha256)
        digest(self.cost_reference_sha256)
        if type(self.side) is not str or self.side not in ('yes', 'no'):
            raise ValueError('research_paper_side_invalid')
        for name in ('decision_at', 'book_captured_at'):
            object.__setattr__(self, name, utc(name, getattr(self, name)))
        if type(self.market) is not GammaMarketSnapshot or type(self.costs) is not ResearchPaperCosts:
            raise ValueError('research_paper_inputs_invalid')
        object.__setattr__(self, 'market', replace(self.market, fetched_at=utc('fetched_at', self.market.fetched_at)))
        object.__setattr__(self, 'costs', replace(self.costs))
        number(self.requested_shares, positive=True)
        number(self.max_entry_cost, positive=True)
        for name in ('min_net_edge', 'min_confidence', 'max_spread'):
            number(getattr(self, name), maximum=ONE)
        integer('max_snapshot_age_seconds', self.max_snapshot_age_seconds, 1, 600)
        _book(self)

    @property
    def content_sha256(self):
        values = {f.name: getattr(self, f.name) for f in fields(self)
                  if f.name not in ('market', 'book_json')}
        values.update(market_sha256=self.market.content_sha256,
                      market_slug=self.market.market_slug, market_fetched_at=self.market.fetched_at,
                      book_raw_sha256=sha256(self.book_json).hexdigest())
        return _hash(values)


def scenarios_copy(scenarios):
    if type(scenarios) is not tuple or not 1 <= len(scenarios) <= MAX_SCENARIOS:
        raise ValueError('research_paper_scenarios_invalid')
    if any(type(s) is not ResearchPaperScenario for s in scenarios):
        raise ValueError('research_paper_scenarios_invalid')
    copied = tuple(replace(s) for s in scenarios)
    if len({s.record_id for s in copied}) != len(copied):
        raise ValueError('research_paper_duplicate_scenario')
    if sum(len(s.book_json) + len(s.market.raw_json) for s in copied) > MAX_INPUT_BYTES:
        raise ValueError('research_paper_input_limit')
    return copied


def _decimal_string(value, **kwargs):
    if type(value) is not str or re.fullmatch(r'(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?', value) is None:
        raise ValueError('research_paper_decimal_string_required')
    return number(Decimal(value), **kwargs)


def _book(item):
    if type(item.book_json) is not bytes or not 1 <= len(item.book_json) <= MAX_BOOK_BYTES:
        raise ValueError('research_paper_book_limit')
    raw = strict_json(item.book_json.decode('utf-8'))
    if type(raw) is not dict:
        raise ValueError('research_paper_book_invalid')
    for key in ('market', 'asset_id'):
        if type(raw.get(key)) is not str or not 1 <= len(raw[key]) <= 128:
            raise ValueError('research_paper_book_identity_required')
    tick = _decimal_string(raw.get('tick_size'), maximum=ONE, positive=True)
    _decimal_string(raw.get('min_order_size'), positive=True)
    if raw.get('neg_risk') is not False:
        raise ValueError('research_paper_binary_book_required')
    for side in ('bids', 'asks'):
        levels = raw.get(side)
        if type(levels) is not list or len(levels) > 100:
            raise ValueError('research_paper_depth_limit')
        prices = set()
        for row in levels:
            if type(row) is not dict or not {'price', 'size'}.issubset(row):
                raise ValueError('research_paper_level_invalid')
            price = _decimal_string(row['price'], maximum=ONE)
            _decimal_string(row['size'])
            with localcontext(CONTEXT):
                if price in prices or price % tick:
                    raise ValueError('research_paper_level_invalid')
            prices.add(price)
    return raw, normalize_order_book(raw, captured_at=item.book_captured_at)


def _list(value):
    return strict_json(value) if type(value) is str else value


def _simulate(item, execution, decision, outcome, generated_at):
    request, record = execution.request, execution.record
    if (request.record_id != item.record_id or request.content_sha256 != item.request_sha256
            or record is None or record.content_sha256 != decision.record_sha256):
        raise ValueError('research_paper_original_binding_mismatch')
    result = dict(scenario_sha256=item.content_sha256, side=item.side, status='blocked',
        reason_code=None, fill=None, cost_edge=None, accounting=None,
        book_raw_sha256=sha256(item.book_json).hexdigest(), market_sha256=item.market.content_sha256,
        decision_at=item.decision_at, cost_reference_sha256=item.cost_reference_sha256,
        settings_version=item.settings_version, requested_shares=item.requested_shares,
        costs_per_filled_share=item.costs, hypothetical_fill_only=True)
    def blocked(reason):
        result['reason_code'] = reason
        return result
    if decision.reason_code not in ('scored', 'outcome_pending'):
        return blocked('original_' + decision.reason_code)
    intake, research = record.run.intake, record.run.research
    if intake.team_id not in ('crypto_btc', 'crypto_eth'):
        return blocked('unsupported_team')
    if not record.recorded_at <= item.decision_at < request.forecast_cutoff_at:
        return blocked('decision_not_prospective')
    if item.decision_at > generated_at:
        return blocked('scenario_from_future')
    for at in (item.market.fetched_at, item.book_captured_at):
        if not record.recorded_at <= at <= item.decision_at:
            return blocked('snapshot_time_mismatch')
        if item.decision_at - at > timedelta(seconds=item.max_snapshot_age_seconds):
            return blocked('snapshot_stale')
    market = strict_json(item.market.raw_json.decode('utf-8'))
    if (type(market) is not dict or market.get('conditionId') != intake.condition_id
            or market.get('slug') != intake.market_slug or item.market.market_slug != intake.market_slug
            or market.get('question') != intake.task.question
            or market.get('description') != intake.task.resolution_criteria):
        return blocked('market_or_terms_mismatch')
    if (market.get('active') is not True or market.get('closed') is not False
            or market.get('acceptingOrders') is not True):
        return blocked('market_not_open')
    observation = assess_crypto_observation_time(team_id=intake.team_id, question=intake.task.question,
        resolution_criteria=intake.task.resolution_criteria, market_slug=intake.market_slug,
        as_of=item.decision_at, forecast_cutoff_at=request.forecast_cutoff_at,
        scheduled_end_at=_timestamp(market.get('endDate')))
    if not observation.new_launch_time_eligible:
        return blocked('unsupported_observation')
    names, tokens = _list(market.get('outcomes')), _list(market.get('clobTokenIds'))
    if (type(names) is not list or sorted(names) != ['No', 'Yes'] or type(tokens) is not list
            or len(tokens) != 2 or any(type(t) is not str or not t or len(t) > 128 for t in tokens)
            or tokens[0] == tokens[1]):
        return blocked('explicit_token_mapping_required')
    raw, book = _book(item)
    if (raw['market'] != intake.condition_id
            or book.token_id != dict(zip(names, tokens))[item.side.title()]):
        return blocked('book_token_mismatch')
    bid, ask = book.best_bid, book.best_ask
    if bid is None or ask is None:
        return blocked('two_sided_book_required')
    if ask <= bid or ask - bid > item.max_spread:
        return blocked('spread_limit')
    if item.requested_shares < Decimal(raw['min_order_size']):
        return blocked('below_minimum_size')
    number(research.probability_yes, maximum=ONE)
    if research.confidence < item.min_confidence:
        return blocked('confidence_limit')
    # Both YES and NO scenarios BUY their explicit outcome token's ASK side.
    fill = simulate_order_book_fill(PaperOrder(book.token_id, 'buy', item.requested_shares), book)
    filled, entry, _ = _walk_levels(item.requested_shares, book.asks)
    if filled != fill.filled_size or filled == 0:
        return blocked('no_fill')
    # The existing fill exposes a rounded display average (0.001). Cash math
    # instead reuses the SAME walk's unrounded notional, never display averages.
    impact = (entry / filled - ask).quantize(Q, rounding=ROUND_CEILING)
    c = item.costs
    edge_input = PaperProbabilitySideEdgeInput(intake.market_slug, intake.task.question, item.side,
        research.probability_yes, ask, c.fee, ZERO, impact+c.extra_slippage,
        c.funding, c.finalization, c.time, c.risk, c.capital,
        item.requested_shares, sum((v.size for v in book.asks if v.price > 0), ZERO), True, True, ('supplied_historical_scenario',))
    edge = build_paper_probability_side_edge_report((edge_input,),
        config=PaperProbabilitySideEdgeConfig(item.settings_version, item.min_net_edge),
        generated_at=item.decision_at)
    costs = sum((getattr(c, f.name) for f in fields(c)), ZERO) * filled
    side_probability = research.probability_yes if item.side == 'yes' else ONE-research.probability_yes
    accounting = dict(entry_notional=entry, modeled_costs=costs, modeled_total_cost=entry+costs,
        modeled_expected_net=filled*side_probability-entry-costs, settled_payout=None,
        modeled_settled_net=None, depth_impact_in_entry_notional=True,
        separate_spread_charge=ZERO, depth_impact_for_edge_per_share=impact)
    if decision.reason_code == 'scored':
        if (outcome is None or outcome.content_sha256 != decision.outcome_sha256
                or outcome.forecast_cutoff_at != request.forecast_cutoff_at):
            raise ValueError('research_paper_outcome_binding_mismatch')
        if outcome.resolved_at < observation.candle_open_at + timedelta(minutes=1):
            return blocked('outcome_before_observation_close')
        payout = filled if outcome.actual_yes == (item.side == 'yes') else ZERO
        accounting.update(settled_payout=payout, modeled_settled_net=payout-entry-costs)
    result.update(fill=fill, cost_edge=edge, accounting=accounting)
    if entry+costs > item.max_entry_cost:
        return blocked('entry_cost_limit')
    if edge.rows[0].action != 'recommend':
        return blocked('existing_cost_edge_' + edge.rows[0].action)
    result.update(status='simulated', reason_code='complete_fill' if fill.is_complete else 'partial_fill')
    return result


def compose_research_paper_evaluation(*, evaluation: ResearchEvaluationReport,
        scenarios: tuple[ResearchPaperScenario, ...], executions: tuple[CapturedResearchExecution, ...]) -> dict:
    """Keep every original decision; never select a later successful attempt.

    Pure supplied-input composition does NOT establish database completeness.
    Use the managed loader for the original strict execution-history check.
    """
    if type(evaluation) is not ResearchEvaluationReport:
        raise ValueError('research_paper_evaluation_required')
    evaluation = replace(evaluation)
    scenarios = scenarios_copy(scenarios)
    if (type(executions) is not tuple or len(executions) != len(scenarios)
            or any(type(e) is not CapturedResearchExecution for e in executions)):
        raise ValueError('research_paper_executions_required')
    executions = tuple(replace(e) for e in executions)
    by_id = {e.request.record_id: e for e in executions}
    supplied = {s.record_id: s for s in scenarios}
    original_ids = {r.record_id for r in evaluation.records}
    if set(by_id) != set(supplied) or not set(supplied).issubset(original_ids):
        raise ValueError('research_paper_unknown_or_duplicate_execution')
    outcomes = {o.condition_id: o for o in evaluation.outcomes}
    rows = []
    with localcontext(CONTEXT):
        for d in evaluation.decisions:
            row = dict(record_id=d.record_id, team_id=d.team_id, model_id=d.model_id,
                protocol_version=d.protocol_version, condition_id=d.condition_id,
                original_reason_code=d.reason_code, record_sha256=d.record_sha256,
                status='scenario_missing', reason_code='scenario_not_supplied')
            if d.record_id in supplied:
                row.update(_simulate(supplied[d.record_id], by_id[d.record_id], d,
                                     outcomes.get(d.condition_id), evaluation.generated_at))
            rows.append(row)
    return json_value(dict(schema_version='research-paper-evaluation-v1',
        evaluation=evaluation.to_dict(), scenario_input_sha256=_hash(sorted(s.content_sha256 for s in scenarios)),
        supplied_scenario_count=len(scenarios), original_attempt_count=len(rows), rows=rows,
        simulated_count=sum(r['status']=='simulated' for r in rows),
        blocked_count=sum(r['status']=='blocked' for r in rows),
        missing_scenario_count=sum(r['status']=='scenario_missing' for r in rows),
        accounting_unit='unit_payout', scenario_persistence_performed=False, forward_test_provenance_established=False,
        source_authentication_performed=False, book_server_timestamp_verified=False, pooled_pnl_computed=False,
        paper_only=True, report_only=True, readonly=True))


@dataclass(frozen=True, slots=True)
class ResearchPaperEvaluation:
    """Detached inputs, not cached derived scores or authenticated source proof."""
    evaluation: ResearchEvaluationReport = field(repr=False)
    scenarios: tuple[ResearchPaperScenario, ...] = field(repr=False)
    executions: tuple[CapturedResearchExecution, ...] = field(repr=False)
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True

    def __post_init__(self):
        hard_flags(self)
        if type(self.evaluation) is not ResearchEvaluationReport:
            raise ValueError('research_paper_evaluation_required')
        object.__setattr__(self, 'evaluation', replace(self.evaluation))
        object.__setattr__(self, 'scenarios', scenarios_copy(self.scenarios))
        if type(self.executions) is not tuple or any(type(e) is not CapturedResearchExecution for e in self.executions):
            raise ValueError('research_paper_executions_required')
        object.__setattr__(self, 'executions', tuple(replace(e) for e in self.executions))
        self.to_dict()

    def to_dict(self):
        hard_flags(self)
        return compose_research_paper_evaluation(evaluation=self.evaluation,
            scenarios=self.scenarios, executions=self.executions)
