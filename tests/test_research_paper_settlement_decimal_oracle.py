"""Independent Decimal oracle for synthetic BTC/ETH paper and settlement math.

LT-03 (PAL_LONGTASK_20260920_V1). Everything here is synthetic fixture data:
no network, database, credential, real market, real settlement, real forecast
or forward-looking performance claim is used or implied. No sample of real
profit, manual settlement or future prediction is fabricated.

Expected values are recomputed IN THIS FILE from the raw synthetic inputs with
decimal.Decimal. The oracle never calls the functions under test
(cost_aware_event_strategy arithmetic, simulate_order_book_fill,
ResearchPaperEvaluation composition or _amounts) to derive an expectation; it
re-derives the documented model from first principles:

- Buy book walk: consume raw ask levels best-first until the requested size is
  filled; worst price is the last consumed level price; a side is executable
  only when the full size is filled (partial walks are never trades).
- Scenario fee bound (quadratic per-share model, conservative upper bound):
  fee is evaluated at the price inside the consumed range [best_ask, worst]
  that MAXIMIZES p*(1-p), i.e. fee_peak = min(worst, max(best_ask, 1/2)),
  then rounded UP to 1e-6.
- Non-fee per-share costs are summed then rounded UP to 1e-6; the per-share net
  edge lower bound is rounded DOWN to 1e-6; totals are size * per-share bounds.
- Strategy layer (legacy report): fee/non-fee/net rounded to nearest 1e-6 and
  evaluated at the executable price itself, gates on confidence, spread (max of
  the two books' best bid/ask spreads), resolution risk, depth (filled size)
  and per-side net edge; the larger clearing net edge wins (ties keep yes).
- Settlement: binary payout = size iff the selected side matches the confirmed
  outcome, else 0; settled PnL lower bound = payout - total cost upper bound.
  Missing/unknown never becomes a number: unsettled rows carry amounts=None.

Property preconditions are stated per test. Monotonicity is asserted only while
every compared run keeps the SAME selected side and identical market, books,
fair probability, gates and size; no ordering is asserted across different
trade choices (yes vs no, or selected vs rejected configurations).
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal as D, localcontext
from hashlib import sha256
from zoneinfo import ZoneInfo
import json

import pytest

from polymarket_alpha_lab import research_paper as paper_core
from polymarket_alpha_lab import research_paper_capture as paper_store
from polymarket_alpha_lab import research_paper_settlement as settlement_core
from polymarket_alpha_lab.cost_aware_event_strategy import (
    PaperCostAwareEventCostAssumptions as Costs,
    PaperCostAwareEventStrategyConfig as Gates,
)
from polymarket_alpha_lab.research_capture_codec import encode_research_capture
from polymarket_alpha_lab.research_execution import CapturedResearchExecution, CapturedResearchRequest
from polymarket_alpha_lab.research_paper_capture_codec import checksum, dump
from polymarket_alpha_lab.research_paper_inputs import ResearchPaperBook as Book
from polymarket_alpha_lab.research_paper_inputs import ResearchPaperScenario as Scenario
from polymarket_alpha_lab.research_resolution import (
    IndependentResolutionConfirmation, ResolutionSubmission,
)
from polymarket_alpha_lab.research_resolution_confirmation import (
    CryptoSettlementReview, build_crypto_resolution_confirmation,
)
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
from polymarket_alpha_lab.research_resolution_store import StoredResolutionReview
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, TeamResearchResult
from polymarket_alpha_lab.team_research_evaluation import (
    ResearchEvaluationOutcome, ResearchEvaluationRecord, ResearchEvaluationReport,
)
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, prepare_team_research_from_gamma
from polymarket_alpha_lab.team_research_market_pipeline import MarketTeamResearchRun

PRIVATE = 'synthetic-private-oracle-text-never-echoed'
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December')
ZERO, ONE = D(0), D(1)
SIX = D('0.000001')
DEFAULT_YES = (('0.4', '10'),)
DEFAULT_YES_BIDS = (('0.39', '10'),)
DEFAULT_NO = (('0.6', '10'),)
DEFAULT_NO_BIDS = (('0.59', '10'),)


# --------------------------------------------------------------------------
# Independent Decimal arithmetic (the oracle itself).
# --------------------------------------------------------------------------

def _quantize(value, quantum, rounding):
    with localcontext() as ctx:
        ctx.prec = 50
        return value.quantize(quantum, rounding=rounding)


def ceil6(value):
    return _quantize(value, SIX, ROUND_CEILING)


def floor6(value):
    return _quantize(value, SIX, ROUND_FLOOR)


def even6(value):
    return _quantize(value, SIX, ROUND_HALF_EVEN)


def oracle_walk(asks, size):
    """Best-first buy walk over raw [(price, size), ...] ask levels."""
    filled, worst = ZERO, None
    for price, available in sorted(asks, key=lambda level: level[0]):
        if filled >= size:
            break
        filled += min(size - filled, available)
        worst = price
    return filled, worst


def oracle_side(fair, asks, bids, size, costs):
    """One side's walk result plus the scenario and strategy cost bounds."""
    best_ask, best_bid = min(p for p, _ in asks), max(p for p, _ in bids)
    filled, worst = oracle_walk(asks, size)
    nonfee_raw = (costs.slippage_cost_per_share + costs.funding_cost_per_share
                  + costs.finalization_cost_per_share + costs.time_cost_per_share
                  + costs.risk_cost_per_share)
    result = dict(best_ask=best_ask, best_bid=best_bid, filled=filled, worst=worst,
                  executable=None if filled != size else worst)
    if result['executable'] is None:
        return result
    price = result['executable']
    fee_peak = min(price, max(best_ask, D('.5')))
    fee = ceil6(costs.taker_fee_rate * fee_peak * (ONE - fee_peak))
    nonfee = ceil6(nonfee_raw)
    strategy_fee = even6(costs.taker_fee_rate * price * (ONE - price))
    strategy_nonfee = even6(nonfee_raw)
    strategy_total = even6(strategy_fee + strategy_nonfee)
    result.update(fee=fee, nonfee=nonfee, lower=floor6(fair - price - fee - nonfee),
                  strategy_fee=strategy_fee, strategy_nonfee=strategy_nonfee,
                  strategy_total=strategy_total,
                  strategy_net=even6((fair - price) - strategy_total))
    return result


def oracle_selection(probability_yes, confidence, yes, no, size, costs, gates, resolution_risk):
    """Gate evaluation and side choice, re-derived from the documented rules."""
    sides = {'yes': oracle_side(probability_yes, yes['asks'], yes['bids'], size, costs),
             'no': oracle_side(ONE - probability_yes, no['asks'], no['bids'], size, costs)}
    spread = max(sides['yes']['best_ask'] - sides['yes']['best_bid'],
                 sides['no']['best_ask'] - sides['no']['best_bid'])
    shared_gates_pass = (confidence >= gates.min_confidence and spread <= gates.max_spread
                         and resolution_risk <= gates.max_resolution_risk)
    choices = [name for name in ('yes', 'no')
               if shared_gates_pass and sides[name]['executable'] is not None
               and sides[name]['filled'] > ZERO and sides[name]['filled'] >= gates.min_ask_size
               and sides[name]['strategy_net'] >= gates.min_net_edge]
    selected = 'none'
    if choices:
        best = max(sides[name]['strategy_net'] for name in choices)
        selected = next(name for name in ('yes', 'no')
                        if name in choices and sides[name]['strategy_net'] == best)
        if sides[selected]['lower'] < gates.min_net_edge:
            selected = 'none'  # conservative_cost_bound_rejected
    return selected, sides, spread


def oracle_totals(side_result, size):
    price, fee, nonfee = side_result['executable'], side_result['fee'], side_result['nonfee']
    return dict(entry_notional_upper_bound=size * price,
                assumed_fee_upper_bound=size * fee,
                assumed_non_fee_cost_upper_bound=size * nonfee,
                assumed_total_cost_upper_bound=size * (price + fee + nonfee),
                expected_net_edge_lower_bound_per_share=side_result['lower'],
                expected_net_edge_lower_bound=size * side_result['lower'])


def oracle_payout(selected_side, actual_yes, size):
    return size if (selected_side == 'yes') == actual_yes else ZERO


# --------------------------------------------------------------------------
# Synthetic fixtures (same shape as the existing paper-group fixtures, with
# knobs for team, question calendar date, clock zone, books, costs and gates).
# --------------------------------------------------------------------------

def _wall_hour(hour, ampm):
    return hour % 12 + (12 if ampm == 'pm' else 0)


def _opening(date, clock):
    month, day, year = date
    hour, minute, ampm, zone_key = clock
    wall = datetime(year, month, day, _wall_hour(hour, ampm), minute)
    if zone_key == 'utc':
        return wall.replace(tzinfo=UTC)
    return wall.replace(tzinfo=ZoneInfo('America/New_York')).astimezone(UTC)


def _millis(at):
    delta = at - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def synthetic_case(n=1, team='crypto_btc', *, p='0.7', date=(9, 15, 2026),
                   clock=(10, 30, 'am', 'utc'), yes_asks=DEFAULT_YES,
                   yes_bids=DEFAULT_YES_BIDS, no_asks=DEFAULT_NO,
                   no_bids=DEFAULT_NO_BIDS, size='5', costs=None, gates=None,
                   resolution_risk='0.1', at=None):
    asset, ticker = ('Bitcoin', 'BTC') if team == 'crypto_btc' else ('Ethereum', 'ETH')
    month, day, year = date
    title = f'Will the price of {asset} be above $2,000 on {MONTHS[month - 1]} {day}, {year}?'
    clock_text = f'{clock[0] % 12 or 12}:{clock[1]:02d} {clock[2].upper()} {clock[3].upper()}'
    rules = (f'This market will resolve to "Yes" if the Close price of the Binance {ticker}/USDT '
             f'1-minute candle at {clock_text} on the date in the title is above $2,000. '
             'Otherwise it will resolve to "No".')
    try:
        opening = _opening(date, clock)
    except ValueError:
        opening = None  # Invalid calendar date; the row stays loadable and blocked later.
    anchor = opening if opening is not None else datetime(year, month, 1, tzinfo=UTC)
    start = at if at is not None else anchor - timedelta(hours=1)
    cid = '0x' + format(n, '064x')
    raw = dict(conditionId=cid, slug=f'synthetic-oracle-{n}', question=title, description=rules,
               active=True, closed=False, acceptingOrders=True, enableOrderBook=True,
               orderMinSize='1', orderPriceMinTickSize='0.001',
               endDate=(anchor + timedelta(hours=1)).isoformat(),
               outcomes=['Yes', 'No'], clobTokenIds=['101', '102'])
    evidence = (ResearchEvidence('source', team, cid, 'Fixture', PRIVATE, 'fixture:source', start),)
    intake = prepare_team_research_from_gamma(
        GammaMarketSnapshot(raw['slug'], start, json.dumps(raw).encode()),
        task_id=f'task-{n}', team_id=team, condition_id=cid, as_of=start, evidence=evidence)
    request = CapturedResearchRequest(f'record-{n}', 'synthetic-model', 'synthetic-protocol',
                                      anchor - timedelta(seconds=1), intake,
                                      required_source_ids=('source',))
    run = MarketTeamResearchRun(intake, TeamResearchResult(
        task_id=intake.task_id, team_id=team, condition_id=cid, market_slug=intake.market_slug,
        as_of=start, status='completed', reason_code='research_completed',
        probability_yes=D(p), confidence=D('.9'), source_ids=('source',), summary=PRIVATE))
    record = ResearchEvaluationRecord(request.record_id, request.model_id, request.protocol_version,
                                      start, run)
    execution = CapturedResearchExecution(request, start, 'captured', record)

    def book(token, levels_bids, levels_asks):
        return Book(start, json.dumps(dict(
            asset_id=token, market=cid, timestamp=str(_millis(start)),
            bids=[dict(price=price, size=size_) for price, size_ in levels_bids],
            asks=[dict(price=price, size=size_) for price, size_ in levels_asks])).encode())

    decision_at = start + timedelta(seconds=1)
    scenario = Scenario(request.record_id, record.content_sha256, decision_at,
                        GammaMarketSnapshot(raw['slug'], decision_at, json.dumps(raw).encode()),
                        book('101', yes_bids, yes_asks), book('102', no_bids, no_asks),
                        D(size), costs or Costs(D('.02'), D('.001'), D(0), D(0), D(0), D(0)),
                        gates or Gates('oracle-test', min_confidence=D('.7'), max_spread=D('.05'),
                                       max_resolution_risk=D('.2'), min_ask_size=D('1'),
                                       min_net_edge=D('.01')),
                        D(resolution_risk), 'oracle-synthetic-costs', 60)
    return request, execution, scenario, raw, opening


def run_scenario(request, execution, scenario):
    history = ResearchEvaluationReport((execution.record,), (), scenario.decision_at)
    report = paper_core.ResearchPaperEvaluation(history, (scenario,), (execution,)).to_dict()
    return next(row for row in report['paper_attempts'] if row['record_id'] == request.record_id)


def _opening_from_request(request):
    title = request.intake.task.question
    parts = title.split(' on ', 1)[1].rstrip('?').replace(',', '').split(' ')
    month, day, year = MONTHS.index(parts[0]) + 1, int(parts[1]), int(parts[2])
    clock = request.intake.task.resolution_criteria.split(' candle at ', 1)[1].split(' on ')[0]
    hour_minute, ampm, zone_key = clock.split(' ')
    hour, minute = (int(value) for value in hour_minute.split(':'))
    return _opening((month, day, year), (hour, minute, ampm.lower(), zone_key.lower()))


def confirmation(request, execution, raw, actual_yes, *, when=None):
    candle = _opening_from_request(request)
    when = when or request.forecast_cutoff_at + timedelta(seconds=61)
    body = dict(raw, closed=True, acceptingOrders=False, umaResolutionStatus='resolved',
                outcomePrices=['1', '0'] if actual_yes else ['0', '1'])
    candidate = StoredResolutionReview(ResolutionSubmission(
        'candidate-' + request.record_id, request.intake.condition_id,
        GammaMarketSnapshot(request.intake.market_slug, when, json.dumps(body).encode()), when), when)
    proof = IndependentResolutionConfirmation(
        request.intake.condition_id, request.intake.market_slug, actual_yes, when,
        when + timedelta(seconds=1), candidate.submission.snapshot.content_sha256,
        'synthetic-reviewer', 'https://data.binance.vision/synthetic-settlement', PRIVATE,
        independently_verified=True)
    ticker = 'BTC' if request.intake.team_id == 'crypto_btc' else 'ETH'
    instruction = CryptoSettlementReview(
        'confirmed-' + request.record_id, request.record_id, request.content_sha256,
        candidate.submission.review_id,
        sha256(encode_resolution(candidate.submission).encode()).hexdigest(),
        proof, 'binance', ticker + 'USDT', '1m', 'close', candle)
    submission = build_crypto_resolution_confirmation(instruction=instruction, execution=execution,
                                                      candidate=candidate)
    outcome = ResearchEvaluationOutcome(
        request.intake.condition_id, request.intake.market_slug, request.forecast_cutoff_at,
        when, when + timedelta(seconds=1), actual_yes,
        settlement_core.resolution._REFERENCE + submission.review_id,
        checksum(encode_resolution(submission)))
    return StoredResolutionReview(submission, when + timedelta(seconds=1), outcome), candidate


def stored_receipt(request, execution, scenario):
    first_history = ResearchEvaluationReport((execution.record,), (), scenario.decision_at)
    attempt_hash = checksum(encode_research_capture(
        record_id=request.record_id, model_id=request.model_id,
        protocol_version=request.protocol_version, run=execution.record.run))
    return paper_store.StoredResearchPaper(
        scenario, request.content_sha256, attempt_hash, request.record_id,
        first_history.generated_at, first_history.input_sha256, request.forecast_cutoff_at,
        scenario.decision_at + timedelta(seconds=1),
        dump(paper_store._result_for(first_history, scenario, execution)))


def settlement_case(n=1, team='crypto_btc', *, actual_yes=True, **knobs):
    """Scenario plus stored receipt, confirmation, outcome and settlement history."""
    request, execution, scenario, raw, _ = synthetic_case(n=n, team=team, **knobs)
    receipt = stored_receipt(request, execution, scenario)
    review, candidate = confirmation(request, execution, raw, actual_yes)
    history = ResearchEvaluationReport((execution.record,), (review.outcome,),
                                       request.forecast_cutoff_at + timedelta(minutes=2))
    return history, receipt, review, execution, candidate


def assemble(history, papers, reviews):
    with localcontext(settlement_core._CONTEXT):
        return settlement_core._assemble(history, papers, reviews)


def decimal_levels(levels):
    return tuple((D(price), D(size)) for price, size in levels)


def oracle_books(knobs):
    yes = dict(asks=decimal_levels(knobs.get('yes_asks', DEFAULT_YES)),
               bids=decimal_levels(knobs.get('yes_bids', DEFAULT_YES_BIDS)))
    no = dict(asks=decimal_levels(knobs.get('no_asks', DEFAULT_NO)),
              bids=decimal_levels(knobs.get('no_bids', DEFAULT_NO_BIDS)))
    return yes, no


# --------------------------------------------------------------------------
# Oracle agreement on paper simulation arithmetic.
# --------------------------------------------------------------------------

CONFIGS = {
    # Single-level walk, yes chosen, OTM price (fee peak clamps at 1/2).
    'shallow-yes': dict(p='0.7'),
    # No side chosen instead.
    'shallow-no': dict(p='0.1'),
    # Multi-level walk: worst 0.5, best ask 0.4, fee peak exactly 1/2 inside the range.
    'deep-walk': dict(p='0.72', yes_asks=(('0.4', '2'), ('0.5', '3'))),
    # Deep ITM: worst 0.9, fee peak clamps DOWN at best ask 0.75.
    'itm-clamp': dict(p='0.95', yes_asks=(('0.75', '2'), ('0.9', '3')),
                      yes_bids=(('0.74', '10'),)),
    # Yes book too shallow: yes fill incomplete, executable only on the no side.
    'partial-book': dict(p='0.7', yes_asks=(('0.4', '2'),)),
    # Zero-fee, slippage-only; per-share net edge lower bound is exactly zero.
    'zero-edge': dict(p='0.5', yes_asks=(('0.49', '10'),), yes_bids=(('0.48', '10'),),
                      costs=Costs(D(0), D('.01'), D(0), D(0), D(0), D(0)),
                      gates=Gates('oracle-zero', min_confidence=D('.7'), max_spread=D('.05'),
                                  max_resolution_risk=D('.2'), min_ask_size=D('1'),
                                  min_net_edge=D(0))),
    # Synthetic certainty: per-share bound is exactly zero and the settled win
    # PnL lower bound is exactly zero (payout == cost bound).
    'certain-win': dict(p='1', yes_asks=(('0.99', '10'),), yes_bids=(('0.98', '10'),),
                        costs=Costs(D(0), D('.01'), D(0), D(0), D(0), D(0)),
                        gates=Gates('oracle-zero', min_confidence=D('.7'), max_spread=D('.05'),
                                    max_resolution_risk=D('.2'), min_ask_size=D('1'),
                                    min_net_edge=D(0))),
    # Neither side clears the edge threshold.
    'none-selected': dict(p='0.4'),
}
BOUND_FIELDS = ('entry_notional_upper_bound', 'assumed_fee_upper_bound',
                'assumed_non_fee_cost_upper_bound', 'assumed_total_cost_upper_bound')


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('name', sorted(CONFIGS))
def test_oracle_reproduces_walk_costs_edges_and_selection(team, name):
    knobs = dict(CONFIGS[name])
    expected_p = D(knobs.pop('p'))
    request, execution, scenario, raw, _ = synthetic_case(
        n=sorted(CONFIGS).index(name) * 2 + (1 if team == 'crypto_btc' else 2),
        team=team, p=str(expected_p), **knobs)
    yes, no = oracle_books(knobs)
    selected, sides, _ = oracle_selection(
        expected_p, D('.9'), yes, no, scenario.requested_size, scenario.costs,
        scenario.gates, scenario.resolution_risk)
    row = run_scenario(request, execution, scenario)
    assert row['selected_side'] == selected
    for side_name in ('yes', 'no'):
        walk, side, result = row['book_walks'][side_name], sides[side_name], \
            row['strategy'][side_name + '_result']
        assert D(walk['filled_size']) == side['filled']
        assert D(walk['best_ask']) == side['best_ask']
        assert D(walk['best_bid']) == side['best_bid']
        assert D(walk['worst_price']) == side['worst']
        if side['executable'] is None:
            assert result['executable_price'] is None and result['net_edge_per_share'] is None
        else:
            assert D(result['executable_price']) == side['executable']
            assert D(result['fee_cost_per_share']) == side['strategy_fee']
            assert D(result['non_fee_cost_per_share']) == side['strategy_nonfee']
            assert D(result['total_cost_per_share']) == side['strategy_total']
            assert D(result['net_edge_per_share']) == side['strategy_net']
    if selected == 'none':
        assert row['status'] == 'paper_scenario_rejected'
    else:
        assert row['status'] == 'paper_scenario_ready'
        totals = oracle_totals(sides[selected], scenario.requested_size)
        for field, expected in totals.items():
            assert D(row['assumed_totals'][field]) == expected, field


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('p,actual,side', [('0.7', True, 'yes'), ('0.7', False, 'yes'),
                                           ('0.1', False, 'no'), ('0.1', True, 'no')])
def test_oracle_settlement_amounts_and_pnl_signs(team, p, actual, side):
    history, receipt, review, execution, _ = settlement_case(team=team, p=p, actual_yes=actual)
    outcome, scenario = review.outcome, receipt.scenario
    yes, no = oracle_books({})
    selected, sides, _ = oracle_selection(D(p), D('.9'), yes, no, scenario.requested_size,
                                          scenario.costs, scenario.gates, scenario.resolution_risk)
    assert selected == side
    totals = oracle_totals(sides[selected], scenario.requested_size)
    payout = oracle_payout(selected, outcome.actual_yes, scenario.requested_size)
    out = assemble(history, {scenario.record_id: receipt}, {outcome.condition_id: review})
    row = out['attempts'][0]
    assert row['status'] == 'settled_simulation'
    amounts = row['amounts']
    assert amounts['selected_side'] == selected and amounts['actual_yes'] is outcome.actual_yes
    assert D(amounts['binary_payout']) == payout
    for field in BOUND_FIELDS:
        assert D(amounts[field]) == totals[field], field
    pnl = D(amounts['settled_pnl_lower_bound'])
    assert pnl == payout - totals['assumed_total_cost_upper_bound']
    # Winning side: strictly positive bound; losing side: strictly negative,
    # because this synthetic cost bound is strictly positive. Zero is exercised
    # separately below; unknown/missing is never a number.
    if (selected == 'yes') == outcome.actual_yes:
        assert pnl > 0
    else:
        assert pnl < 0


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('actual', [True, False])
def test_oracle_exact_zero_pnl_is_a_real_computed_zero(team, actual):
    history, receipt, review, execution, _ = settlement_case(team=team, actual_yes=actual,
                                                             **CONFIGS['certain-win'])
    scenario = receipt.scenario
    out = assemble(history, {scenario.record_id: receipt}, {review.outcome.condition_id: review})
    row = out['attempts'][0]
    assert row['status'] == 'settled_simulation'
    amounts = row['amounts']
    payout = oracle_payout(amounts['selected_side'], actual, scenario.requested_size)
    # Independent expectation: price 0.99, zero fee rate, slippage 0.01 per share.
    cost = scenario.requested_size * (D('0.99') + D(0) + D('.01'))
    assert D(amounts['binary_payout']) == payout
    assert D(amounts['assumed_total_cost_upper_bound']) == cost == scenario.requested_size
    assert D(amounts['settled_pnl_lower_bound']) == payout - cost
    if (amounts['selected_side'] == 'yes') == actual:
        assert D(amounts['settled_pnl_lower_bound']) == 0
    else:
        assert D(amounts['settled_pnl_lower_bound']) == -scenario.requested_size


def test_group_totals_conserve_oracle_row_amounts_and_missing_stays_none():
    settled_a = settlement_case(n=1, p='0.7', actual_yes=True)
    settled_b = settlement_case(n=2, p='0.7', actual_yes=False)
    pending = settlement_case(n=3, p='0.7')
    history = ResearchEvaluationReport(
        (settled_a[3].record, settled_b[3].record, pending[3].record),
        (settled_a[2].outcome, settled_b[2].outcome), settled_a[0].generated_at)
    papers = {case_[1].scenario.record_id: case_[1] for case_ in (settled_a, settled_b, pending)}
    out = assemble(history, papers, {case_[2].outcome.condition_id: case_[2]
                                     for case_ in (settled_a, settled_b)})

    def row_for(case_):
        return next(row for row in out['attempts']
                    if row['record_id'] == case_[3].request.record_id)

    oracle_pnl, oracle_payout, oracle_cost = ZERO, ZERO, ZERO
    for case_ in (settled_a, settled_b):
        amounts = row_for(case_)['amounts']
        assert amounts is not None
        oracle_pnl += D(amounts['settled_pnl_lower_bound'])
        oracle_payout += D(amounts['binary_payout'])
        oracle_cost += D(amounts['assumed_total_cost_upper_bound'])
    group = out['groups'][0]
    assert group['attempt_count'] == 3 and group['settled_count'] == 2
    assert D(group['settled_pnl_lower_bound_sum']) == oracle_pnl
    assert D(group['binary_payout_sum']) == oracle_payout
    assert D(group['assumed_total_cost_upper_bound_sum']) == oracle_cost
    assert oracle_pnl == oracle_payout - oracle_cost  # conservation across the group
    # The outcome_pending row stays unsettled: amounts None, never a zero PnL.
    pending_row = row_for(pending)
    assert pending_row['status'] == 'outcome_pending' and pending_row['amounts'] is None
    assert out['status_counts'] == dict(paper_evidence_missing=0, research_not_selected=0,
                                        paper_not_selected=0, outcome_pending=1,
                                        crypto_confirmation_required=0, settled_simulation=2)
    # A group with no settled row reports None sums, not invented zeros.
    pending_only = ResearchEvaluationReport((pending[3].record,), (), pending[0].generated_at)
    empty = assemble(pending_only, {pending[1].scenario.record_id: pending[1]}, {})
    assert empty['groups'][0]['settled_pnl_lower_bound_sum'] is None


# --------------------------------------------------------------------------
# Monotonicity properties (preconditions stated per test).
# --------------------------------------------------------------------------

def test_fee_rate_increase_never_improves_net_edge_bound():
    """Preconditions: identical market, books, fair probability 0.9, gates and
    size; only the taker fee rate varies; the same side stays selected in every
    run (asserted as a precondition). This compares cost bounds of ONE fixed
    trade choice; it asserts nothing about yes-vs-no or rejected scenarios."""
    previous = None
    for rate in ('0', '0.01', '0.02', '0.05', '0.1', '0.25', '0.5', '1'):
        costs = Costs(D(rate), D('.001'), D(0), D(0), D(0), D(0))
        request, execution, scenario, _, _ = synthetic_case(p='0.9', costs=costs)
        row = run_scenario(request, execution, scenario)
        assert row['selected_side'] == 'yes'  # precondition, not an outcome claim
        totals = row['assumed_totals']
        yes, no = oracle_books({})
        side = oracle_side(D('0.9'), yes['asks'], yes['bids'], scenario.requested_size, costs)
        assert D(totals['assumed_fee_upper_bound']) == scenario.requested_size * side['fee']
        assert D(totals['expected_net_edge_lower_bound_per_share']) == side['lower']
        if previous is not None:
            assert D(totals['assumed_fee_upper_bound']) >= previous['fee']
            assert D(totals['entry_notional_upper_bound']) == previous['entry']
            assert D(totals['expected_net_edge_lower_bound_per_share']) <= previous['lower']
        previous = dict(fee=D(totals['assumed_fee_upper_bound']),
                        lower=D(totals['expected_net_edge_lower_bound_per_share']),
                        entry=D(totals['entry_notional_upper_bound']))


def test_slippage_increase_never_improves_net_edge_bound():
    """Preconditions: identical market, books, fair probability 0.9, gates and
    size; only slippage varies; the same side stays selected in every run."""
    previous = None
    for slip in ('0', '0.0005', '0.001', '0.005', '0.01', '0.05', '0.1'):
        costs = Costs(D('.02'), D(slip), D(0), D(0), D(0), D(0))
        request, execution, scenario, _, _ = synthetic_case(p='0.9', costs=costs)
        row = run_scenario(request, execution, scenario)
        assert row['selected_side'] == 'yes'  # precondition
        totals = row['assumed_totals']
        if previous is not None:
            assert D(totals['assumed_non_fee_cost_upper_bound']) >= previous['nonfee']
            assert D(totals['expected_net_edge_lower_bound_per_share']) <= previous['lower']
        previous = dict(nonfee=D(totals['assumed_non_fee_cost_upper_bound']),
                        lower=D(totals['expected_net_edge_lower_bound_per_share']))


# --------------------------------------------------------------------------
# Time and timezone boundaries through the paper/settlement path.
# --------------------------------------------------------------------------

VALID_BOUNDARIES = [
    ((2, 29, 2028), (10, 30, 'am', 'utc'), 'leap-day'),
    ((2, 28, 2027), (11, 59, 'pm', 'utc'), 'non-leap-february-end'),
    ((12, 31, 2026), (11, 59, 'pm', 'utc'), 'year-end'),
    ((1, 1, 2027), (12, 0, 'am', 'utc'), 'year-start-midnight'),
    ((3, 9, 2026), (9, 30, 'am', 'et'), 'et-after-spring-forward'),
    ((11, 2, 2026), (9, 30, 'am', 'et'), 'et-after-fall-back'),
    ((6, 15, 2026), (12, 0, 'pm', 'et'), 'et-noon'),
]


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('date,clock,label', VALID_BOUNDARIES)
def test_valid_calendar_and_zone_clocks_produce_settleable_scenarios(team, date, clock, label):
    index = VALID_BOUNDARIES.index((date, clock, label))
    request, execution, scenario, _, opening = synthetic_case(n=index * 3 + 11, team=team,
                                                              p='0.7', date=date, clock=clock)
    assert opening is not None and scenario.decision_at < request.forecast_cutoff_at
    row = run_scenario(request, execution, scenario)
    assert row['status'] == 'paper_scenario_ready', row['reason_code']
    yes, no = oracle_books({})
    selected, sides, _ = oracle_selection(D('0.7'), D('.9'), yes, no, scenario.requested_size,
                                          scenario.costs, scenario.gates, scenario.resolution_risk)
    assert selected == row['selected_side'] == 'yes'
    totals = oracle_totals(sides['yes'], scenario.requested_size)
    for field in BOUND_FIELDS:
        assert D(row['assumed_totals'][field]) == totals[field], field


BLOCKED_BOUNDARIES = [
    ((2, 29, 2027), (10, 30, 'am', 'utc'), 'nonexistent-leap-day'),
    ((4, 31, 2026), (10, 30, 'am', 'utc'), 'nonexistent-month-end'),
    ((11, 1, 2026), (1, 30, 'am', 'et'), 'dst-fold-ambiguous'),
    ((3, 8, 2026), (2, 30, 'am', 'et'), 'dst-gap-nonexistent'),
]


@pytest.mark.parametrize('date,clock,label', BLOCKED_BOUNDARIES)
def test_boundary_time_questions_never_become_settleable(date, clock, label):
    request, execution, scenario, _, _ = synthetic_case(n=61, p='0.7', date=date, clock=clock)
    row = run_scenario(request, execution, scenario)
    assert row['status'] == 'paper_scenario_rejected'
    assert row['reason_code'] == 'original_contract_or_observation_blocked'
    assert row.get('assumed_totals') is None and row.get('selected_side') is None


def test_resolved_at_exactly_at_cutoff_settles_and_one_microsecond_before_cannot_exist():
    """Boundary semantics of the settlement terms: recorded < cutoff <=
    resolved. resolved_at == forecast_cutoff_at (month-end instant
    2026-09-30T23:59:59Z) is accepted at the composition seam; an outcome with
    resolved_at one microsecond earlier is not even constructible (type-level
    chronology), and the full confirmation chain is stricter still: it requires
    the candle to have closed before any settlement time, so a settlement at
    the cutoff can never build a confirmation at all."""
    request, execution, scenario, raw, opening = synthetic_case(
        n=77, p='0.7', date=(10, 1, 2026), clock=(12, 0, 'am', 'utc'))
    assert request.forecast_cutoff_at == datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)
    # Direct-constructed, fully consistent review whose resolved_at EQUALS the
    # cutoff (assess_resolution ready and every type constraint holds). Review
    # ordering constraints: checked_at <= review.recorded_at <= outcome.recorded_at.
    body = dict(raw, closed=True, acceptingOrders=False, umaResolutionStatus='resolved',
                outcomePrices=['1', '0'])
    resolved = request.forecast_cutoff_at
    snap = GammaMarketSnapshot(request.intake.market_slug, resolved, json.dumps(body).encode())
    proof = IndependentResolutionConfirmation(
        request.intake.condition_id, request.intake.market_slug, True, resolved,
        resolved + timedelta(seconds=1), snap.content_sha256, 'synthetic-reviewer',
        'https://data.binance.vision/synthetic-settlement', PRIVATE, independently_verified=True)
    submission = ResolutionSubmission('confirmed-' + request.record_id,
                                      request.intake.condition_id, snap,
                                      resolved + timedelta(seconds=1), proof)
    payload = encode_resolution(submission)
    reference = settlement_core.resolution._REFERENCE + submission.review_id
    payload_hash = sha256(payload.encode()).hexdigest()
    outcome = ResearchEvaluationOutcome(
        request.intake.condition_id, request.intake.market_slug, request.forecast_cutoff_at,
        resolved, resolved + timedelta(seconds=2), True, reference, payload_hash)
    edge_review = StoredResolutionReview(submission, resolved + timedelta(seconds=2), outcome)
    receipt = stored_receipt(request, execution, scenario)
    settled_history = ResearchEvaluationReport((execution.record,), (outcome,),
                                               request.forecast_cutoff_at + timedelta(minutes=2))
    out = assemble(settled_history, {scenario.record_id: receipt},
                   {outcome.condition_id: edge_review})
    row = out['attempts'][0]
    assert row['status'] == 'settled_simulation'
    yes, no = oracle_books({})
    selected, sides, _ = oracle_selection(D('0.7'), D('.9'), yes, no, scenario.requested_size,
                                          scenario.costs, scenario.gates, scenario.resolution_risk)
    totals = oracle_totals(sides[selected], scenario.requested_size)
    payout = oracle_payout(selected, True, scenario.requested_size)
    assert D(row['amounts']['binary_payout']) == payout
    assert D(row['amounts']['settled_pnl_lower_bound']) \
        == payout - totals['assumed_total_cost_upper_bound']
    # One microsecond earlier is rejected by the outcome type itself.
    with pytest.raises(ValueError, match='chronological'):
        ResearchEvaluationOutcome(request.intake.condition_id, request.intake.market_slug,
                                  request.forecast_cutoff_at,
                                  resolved - timedelta(microseconds=1), resolved, True,
                                  reference, payload_hash)
    # The full chain is stricter: settlement at the cutoff precedes the candle
    # close, so no confirmation can be built for it.
    with pytest.raises(ValueError, match='settlement_confirmation_time_mismatch'):
        confirmation(request, execution, raw, True, when=request.forecast_cutoff_at)


# --------------------------------------------------------------------------
# Wrong source, tampered hashes and foreign bindings at the settlement read.
# --------------------------------------------------------------------------

class Cursor:
    def __init__(self, answers):
        self.answers = list(answers)

    def execute(self, sql, args=None):
        self.answer = self.answers.pop(0)

    def fetchone(self):
        return self.answer

    def fetchall(self):
        return self.answer


def _retagged_review(review, envelope_changes):
    """Re-encode a saved confirmation with tampered envelope fields, keeping
    every type-level binding self-consistent (hashes updated) so only the
    settlement read's own rebuild check can catch the divergence."""
    envelope = json.loads(review.submission.confirmation.source_text)
    envelope.update(envelope_changes)
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    submission = replace(review.submission,
                         confirmation=replace(review.submission.confirmation,
                                              source_text=encoded))
    payload = encode_resolution(submission)
    outcome = replace(review.outcome,
                      source_content_sha256=sha256(payload.encode()).hexdigest())
    return replace(review, submission=submission, outcome=outcome), outcome


@pytest.mark.parametrize('change,error', [
    (dict(source_candle_open_at='2026-09-15T10:31:00+00:00'),
     'settlement_source_descriptor_mismatch'),
    (dict(original_market_content_sha256='b' * 64),
     'research_paper_settlement_crypto_proof_changed'),
], ids=['shifted-candle', 'tampered-market-hash'])
def test_tampered_saved_confirmation_aborts_the_settlement_read(change, error, monkeypatch):
    history, receipt, review, execution, candidate = settlement_case()
    tampered, outcome = _retagged_review(review, change)
    seen = []

    def load_review(cursor, review_id, at, budget):
        seen.append(review_id)
        return tampered if review_id == review.submission.review_id else candidate

    monkeypatch.setattr(settlement_core, '_review', load_review)
    monkeypatch.setattr(settlement_core.paper, '_original', lambda cursor, rid: execution)
    with pytest.raises(ValueError, match=error):
        settlement_core._crypto_review(Cursor([(100,)]), outcome, history, [1000])
    assert seen == [review.submission.review_id, candidate.submission.review_id]


def test_receipt_bound_to_a_foreign_record_hash_aborts_settlement(monkeypatch):
    request, execution, scenario, raw, _ = synthetic_case(n=51)
    receipt = stored_receipt(request, execution, scenario)
    # A row decode that yields a genuinely different record (different run
    # bytes, hence a different computed content hash) for the same record_id:
    # only the settlement reader's own binding check can stop the mismatch.
    divergent_run = replace(execution.record.run,
                            research=replace(execution.record.run.research,
                                             probability_yes=D('0.71')))
    divergent = ResearchEvaluationRecord(request.record_id, request.model_id,
                                         request.protocol_version,
                                         execution.record.recorded_at, divergent_run)
    assert divergent.content_sha256 != receipt.scenario.record_sha256
    history = ResearchEvaluationReport((divergent,), (),
                                       request.forecast_cutoff_at + timedelta(minutes=2))
    cursor = Cursor([(1, 123), [(receipt.scenario.record_id,)]])
    monkeypatch.setattr(settlement_core.paper, '_load', lambda cursor_, rid: receipt)
    with pytest.raises(ValueError, match='research_paper_settlement_receipt_mismatch'):
        settlement_core._paper_rows(cursor, history, 10000, [1000])
