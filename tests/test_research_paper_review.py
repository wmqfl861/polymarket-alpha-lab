"""Separate same-assistant adversarial review and independent arithmetic oracle."""
from dataclasses import replace
from datetime import timedelta, timezone
from decimal import Decimal as D, localcontext, ROUND_UP
from fractions import Fraction
import json

import pytest

from polymarket_alpha_lab import research_paper as core
from polymarket_alpha_lab import research_paper_input as inputs
from tests.test_research_paper import fixture, composed, book_change, input_bytes, NOW


def test_settlement_cannot_precede_actual_observation_close():
    s,ex,r=fixture()
    impossible=replace(r.outcomes[0],resolved_at=ex.request.forecast_cutoff_at)
    row=composed(s,ex,replace(r,outcomes=(impossible,)))['rows'][0]
    assert row['status']=='blocked' and row['reason_code']=='outcome_before_observation_close'
    assert row['accounting'] is None or row['accounting']['modeled_settled_net'] is None


def test_equivalent_market_collection_instant_has_same_scenario_hash():
    s,ex,r=fixture();zone=timezone(timedelta(hours=8))
    other=replace(s,market=replace(s.market,fetched_at=s.market.fetched_at.astimezone(zone)),
                  decision_at=s.decision_at.astimezone(zone),book_captured_at=s.book_captured_at.astimezone(zone))
    assert other.content_sha256==s.content_sha256
    assert composed(other,ex,r)==composed(s,ex,r)


@pytest.mark.parametrize('quantity',['1','2','3','4','5','3.000001'])
@pytest.mark.parametrize('side',['yes','no'])
def test_independent_fraction_oracle_for_fill_and_cost(quantity,side):
    s,ex,r=fixture(side=side,actual=False)
    s=replace(s,requested_shares=D(quantity))
    result=composed(s,ex,r)['rows'][0]
    # Independent arithmetic, no production walker used in the expected values.
    q=Fraction(quantity);a=min(q,Fraction(2));b=min(max(q-a,0),Fraction(2))
    filled=a+b;notional=a*Fraction(2,5)+b*Fraction(1,2);cost=filled*Fraction(3,100)
    assert Fraction(result['fill']['filled_size'])==filled
    assert Fraction(result['fill']['unfilled_size'])==q-filled
    assert Fraction(result['accounting']['entry_notional'])==notional
    assert Fraction(result['accounting']['modeled_costs'])==cost
    payout=filled if side=='no' else Fraction(0)
    assert Fraction(result['accounting']['modeled_settled_net'])==payout-notional-cost
    # Existing six-place edge is conservative; rounded fill display is not money.
    assert Fraction(result['cost_edge']['rows'][0]['net_probability_edge']) <= Fraction(4,5)-notional/filled-cost/filled


def test_hostile_decimal_context_cannot_change_cash_or_hashes():
    s,ex,r=fixture();expected=composed(s,ex,r)
    with localcontext() as ctx:
        ctx.prec=3;ctx.rounding=ROUND_UP
        assert composed(s,ex,r)==expected


def test_as_of_before_scenario_does_not_leak_future_settlement():
    s,ex,r=fixture();r=replace(r,generated_at=ex.record.recorded_at)
    row=composed(s,ex,r)['rows'][0]
    assert row['reason_code']=='scenario_from_future' and row['accounting'] is None


def test_outcome_binding_cannot_change_registered_cutoff():
    s,ex,r=fixture();other=replace(r.outcomes[0],forecast_cutoff_at=r.outcomes[0].forecast_cutoff_at+timedelta(seconds=1))
    with pytest.raises(ValueError,match='outcome_binding'):composed(s,ex,replace(r,outcomes=(other,)))


def test_input_bool_numbers_and_unknown_fields_never_gain_defaults():
    s,_,_=fixture()
    for key,value in [('requested_shares',True),('min_net_edge',0.01),('new_field','ignored')]:
        raw=json.loads(input_bytes((s,)));raw['scenarios'][0][key]=value
        with pytest.raises(ValueError):inputs.decode_paper_scenarios(json.dumps(raw).encode())


def test_zero_size_levels_are_not_phantom_liquidity():
    s,ex,r=fixture();s=book_change(s,asks=[dict(price='0.20',size='0'),dict(price='0.50',size='1')])
    out=composed(s,ex,r)['rows'][0]
    assert D(out['fill']['filled_size'])==1 and D(out['accounting']['entry_notional'])==D('.50')


def test_duplicate_scenario_does_not_double_economic_sample():
    s,ex,r=fixture()
    with pytest.raises(ValueError):core.compose_research_paper_evaluation(evaluation=r,scenarios=(s,s),executions=(ex,ex))


def test_mutated_source_limits_fail_without_mutating_original_record():
    s,ex,r=fixture();before=ex.record.content_sha256
    object.__setattr__(s.costs,'fee',D('-1'))
    with pytest.raises(ValueError):composed(s,ex,r)
    assert ex.record.content_sha256==before
