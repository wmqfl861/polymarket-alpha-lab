"""Separate same-assistant adversarial review of the paper replay integration."""
from dataclasses import replace
from decimal import Decimal as D, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
import io
import json
from types import SimpleNamespace

import pytest

from tests.test_research_paper_evaluation import ROOT, PRIVATE, fixture, compose, managed, raw_input
from polymarket_alpha_lab import research_evaluation_cli as cli
from polymarket_alpha_lab import research_paper_evaluation as core


@pytest.mark.parametrize('fault',['raw-leak','wrong-scenario'])
def test_console_does_not_trust_arbitrary_returned_paper_dictionary(managed,capsys,fault):
    s,e,r=fixture()
    if fault=='raw-leak':managed['result']={'raw_secret':PRIVATE}
    else:managed['result']=compose(replace(s,shares=D('2')),e,r)
    code=cli.main(['--paper-scenarios'],default_root=ROOT)
    text=capsys.readouterr()
    assert code==1 and PRIVATE not in text.out+text.err
    assert json.loads(text.out)['evaluation'] is None


def test_explicitly_disabled_order_book_is_not_simulated():
    s,e,r=fixture();raw=json.loads(s.market.raw_json);raw['enableOrderBook']=False
    s=replace(s,market=replace(s.market,raw_json=json.dumps(raw).encode()))
    row=compose(s,e,r)['scenarios'][0]
    assert row['status']=='rejected_scenario' and row['reason_code']=='market_not_open'


def test_incomplete_even_with_zero_scenarios_is_not_a_weaker_history_query(monkeypatch):
    calls=[]
    def denied(*a,**kw):calls.append(1);raise ValueError('incomplete')
    monkeypatch.setattr(core,'load_captured_research_evaluation_with_psycopg',denied)
    with pytest.raises(ValueError):core.evaluate_research_paper_with_psycopg('test',scenarios=())
    assert calls==[1]


def test_finite_fraction_oracle_for_depth_and_cash_fees():
    s,e,r=fixture()
    def rounded_fee(price,rate):
        x=Fraction(price)*Fraction(rate)*(1-Fraction(price))*1000000
        n,rem=divmod(x.numerator,x.denominator)
        n+=int(2*rem>x.denominator or (2*rem==x.denominator and n%2==1))
        return Fraction(n,1000000)
    for rate in ('0','.000001','.07','.3','1'):
        for prices in (('.410001','.419999'),('.42','.44'),('.401','.48')):
            raw=json.loads(s.yes_book.raw_json)
            raw['asks']=[dict(price=prices[0],size='1'),dict(price=prices[1],size='2')]
            new=replace(s,yes_book=replace(s.yes_book,raw_json=json.dumps(raw).encode()),
                costs=replace(s.costs,taker_fee_rate=D(rate)))
            result=compose(new,e,r)['scenarios'][0]['scenario']
            assert result['selected_side']=='yes'
            expected_notional=Fraction(prices[0])+2*Fraction(prices[1])
            expected_fee=rounded_fee(prices[0],rate)+2*rounded_fee(prices[1],rate)
            expected_cost=expected_notional+expected_fee+Fraction(3,1000)
            assert Fraction(result['accounting']['gross_notional'])==expected_notional
            assert Fraction(result['accounting']['fee_estimate'])==expected_fee
            assert Fraction(result['net_after_outcome'])==3-expected_cost


def test_stdin_read_is_bounded_and_never_opens_project_on_overflow(managed,capsys,monkeypatch):
    from polymarket_alpha_lab.research_paper_inputs import MAX_INPUT_BYTES
    class Stream:
        def read(self,n):assert n==MAX_INPUT_BYTES+1;return b' '*n
    monkeypatch.setattr(cli.sys,'stdin',SimpleNamespace(buffer=Stream()))
    assert cli.main(['--paper-scenarios'],default_root=ROOT)==2 and 'root' not in managed


def test_valid_typed_but_different_scenario_receipt_is_rejected(managed,capsys):
    s,e,r=fixture();other=replace(s,shares=D('2'))
    managed['result']=core.ResearchPaperReplay(r,(other,),(e,))
    assert cli.main(['--paper-scenarios'],default_root=ROOT)==1
    assert json.loads(capsys.readouterr().out)['evaluation'] is None


def test_timezone_equivalence_and_reversed_outcome_order():
    from datetime import timezone, timedelta
    s,e,r=fixture();raw=json.loads(s.market.raw_json)
    raw.update(outcomes=['No','Yes'],clobTokenIds=['202','101'])
    same=replace(s,market=replace(s.market,raw_json=json.dumps(raw).encode()),
        simulated_at=s.simulated_at.astimezone(timezone(timedelta(hours=8))))
    a=compose(s,e,r)['scenarios'][0]['scenario'];b=compose(same,e,r)['scenarios'][0]['scenario']
    assert a['accounting']==b['accounting'] and a['selected_side']==b['selected_side']


def test_source_order_does_not_change_book_fill():
    s,e,r=fixture();raw=json.loads(s.yes_book.raw_json);raw['asks'].reverse()
    other=replace(s,yes_book=replace(s.yes_book,raw_json=json.dumps(raw).encode()))
    a=compose(s,e,r)['scenarios'][0]['scenario'];b=compose(other,e,r)['scenarios'][0]['scenario']
    assert a['fills']==b['fills'] and a['accounting']==b['accounting']
    assert a['yes_book_raw_sha256']!=b['yes_book_raw_sha256']
