"""Synthetic full-history paper assembly; no real provider, HTTP or database."""
from contextlib import contextmanager
from dataclasses import replace, fields
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal as D, localcontext
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab import research_paper as core, research_paper_input as inputs
from polymarket_alpha_lab import research_paper_service as service, research_evaluation_cli as cli
from polymarket_alpha_lab.research_capture_psycopg import ResearchCaptureConflict
from polymarket_alpha_lab.research_execution import CapturedResearchExecution, CapturedResearchRequest
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationRecord, ResearchEvaluationOutcome, ResearchEvaluationReport
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, TeamResearchResult
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, prepare_team_research_from_gamma
from polymarket_alpha_lab.team_research_market_pipeline import MarketTeamResearchRun

NOW = datetime(2026, 9, 15, 10, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]
PRIVATE = 'synthetic-evidence-not-for-console'


def prepared(n=1, team='crypto_btc', *, at=NOW, opening=None):
    opening = opening or at.replace(hour=16, minute=0, second=0, microsecond=0)
    asset, ticker = ('Bitcoin', 'BTC') if team == 'crypto_btc' else ('Ethereum', 'ETH')
    months = ('January','February','March','April','May','June','July','August','September','October','November','December')
    day = f'{months[opening.month-1]} {opening.day}, {opening.year}'
    clock = f'{opening.hour%12 or 12}:{opening.minute:02d} '+('AM' if opening.hour<12 else 'PM')
    cid = '0x'+format(n,'064x'); slug = 'paper-'+str(n)
    raw = dict(conditionId=cid, slug=slug, question=f'Will the price of {asset} be above $2,000 on {day}?',
        description=f'This market will resolve to "Yes" if the Close price of the Binance {ticker}/USDT '
        f'1-minute candle at {clock} UTC on the date in the title is above $2,000. Otherwise it will resolve to "No".',
        active=True, closed=False, acceptingOrders=True, outcomes=['Yes','No'],
        clobTokenIds=['token-yes-'+str(n),'token-no-'+str(n)], endDate=(opening+timedelta(hours=1)).isoformat())
    snap = GammaMarketSnapshot(slug, at, json.dumps(raw).encode())
    evidence = ResearchEvidence('source', team, cid, 'Synthetic input', PRIVATE, 'fixture:source', at)
    intake = prepare_team_research_from_gamma(snap, task_id='task-'+str(n), team_id=team, condition_id=cid,
                                             as_of=at, evidence=(evidence,))
    return CapturedResearchRequest('record-'+str(n), 'synthetic', 'protocol-v1', opening-timedelta(seconds=1),
        intake, required_source_ids=('source',)), raw


def execution(req, probability=D('.8'), status='completed'):
    i = req.intake
    kw = dict(task_id=i.task_id, team_id=i.team_id, condition_id=i.condition_id, market_slug=i.market_slug,
              as_of=i.as_of, status=status, reason_code='research_completed' if status=='completed' else 'model_failed')
    if status=='completed':
        kw.update(probability_yes=probability, confidence=D('.9'), source_ids=('source',), summary=PRIVATE)
    row = ResearchEvaluationRecord(req.record_id, req.model_id, req.protocol_version,
        i.as_of+timedelta(seconds=1), MarketTeamResearchRun(i, TeamResearchResult(**kw)))
    return CapturedResearchExecution(req, i.as_of, 'captured', row)


def scenario(req, raw, *, at=None, side='yes'):
    at = at or req.intake.as_of+timedelta(seconds=2)
    book = dict(market=req.intake.condition_id, asset_id=raw['clobTokenIds'][0 if side=='yes' else 1],
        bids=[dict(price='0.39',size='10')], asks=[dict(price='0.50',size='2'),dict(price='0.40',size='2')],
        min_order_size='1', tick_size='0.01', neg_risk=False)
    return core.ResearchPaperScenario(req.record_id, req.content_sha256, at,
        GammaMarketSnapshot(req.intake.market_slug, at, json.dumps(raw).encode()), json.dumps(book).encode(),
        at, side, D('3'), core.ResearchPaperCosts(D('.01'),D('.02'),D(0),D(0),D(0),D(0),D(0)),
        'a'*64, 'scenario-v1', D('.01'), D('.7'), D('.2'), D('10'), 60)


def fixture(*, team='crypto_btc', side='yes', actual=True, settled=True, status='completed'):
    req, raw = prepared(team=team)
    ex = execution(req, D('.8') if side=='yes' else D('.2'), status)
    s = scenario(req, raw, side=side)
    outcomes = ()
    if settled:
        outcomes = (ResearchEvaluationOutcome(req.intake.condition_id, req.intake.market_slug, req.forecast_cutoff_at,
            NOW.replace(hour=16,minute=1), NOW.replace(hour=16,minute=2), actual, 'urn:synthetic', 'b'*64),)
    report = ResearchEvaluationReport((ex.record,), outcomes, NOW.replace(hour=17))
    return s, ex, report


def composed(s, ex, report):
    return core.ResearchPaperEvaluation(report,(s,),(ex,)).to_dict()


def input_bytes(scenarios):
    rows=[]
    for s in scenarios:
        row={f.name:core.json_value(getattr(s,f.name)) for f in fields(s)
             if f.name not in ('market','book_json','paper_only','report_only','readonly')}
        row['market']=dict(market_slug=s.market.market_slug, fetched_at=s.market.fetched_at.isoformat(),
                           raw_json=s.market.raw_json.decode())
        row['book_json']=s.book_json.decode()
        rows.append(row)
    return json.dumps(dict(scenarios=rows)).encode()


def book_change(s, **changes):
    raw=json.loads(s.book_json);raw.update(changes)
    return replace(s,book_json=json.dumps(raw).encode())


def market_change(s, **changes):
    raw=json.loads(s.market.raw_json);raw.update(changes)
    return replace(s,market=replace(s.market,raw_json=json.dumps(raw).encode()))


@pytest.mark.parametrize('team',['crypto_btc','crypto_eth'])
@pytest.mark.parametrize('side',['yes','no'])
@pytest.mark.parametrize('actual',[True,False])
def test_explicit_side_depth_cost_and_outcome(team,side,actual):
    s,ex,r=fixture(team=team,side=side,actual=actual)
    out=composed(s,ex,r);row=out['rows'][0];cash=row['accounting']
    assert row['status']=='simulated' and row['fill']['side']=='buy'
    assert D(cash['entry_notional'])==D('1.30')
    assert D(cash['modeled_costs'])==D('.09') and D(cash['modeled_total_cost'])==D('1.39')
    assert D(cash['modeled_expected_net'])==D('1.01')
    assert D(cash['settled_payout'])==(D(3) if actual==(side=='yes') else D(0))
    assert D(cash['modeled_settled_net'])==(D('1.61') if actual==(side=='yes') else D('-1.39'))
    assert D(row['fill']['average_price'])==D('.433')  # display value, not cash basis
    assert D(row['cost_edge']['rows'][0]['side_probability'])==D('.8')
    assert out['evaluation']==r.to_dict() and out['pooled_pnl_computed'] is False
    assert PRIVATE not in json.dumps(out)


def test_partial_fill_retains_requested_and_unfilled_shares():
    s,ex,r=fixture();s=replace(s,requested_shares=D(10))
    row=composed(s,ex,r)['rows'][0]
    assert row['reason_code']=='partial_fill' and D(row['fill']['requested_size'])==10
    assert D(row['fill']['filled_size'])==4 and D(row['fill']['unfilled_size'])==6
    assert D(row['accounting']['entry_notional'])==D('1.8')


def test_no_settlement_stays_unknown_not_no_or_zero_pnl():
    s,ex,r=fixture(settled=False);row=composed(s,ex,r)['rows'][0]
    assert row['status']=='simulated' and row['original_reason_code']=='outcome_pending'
    assert row['accounting']['settled_payout'] is row['accounting']['modeled_settled_net'] is None


@pytest.mark.parametrize('status',['failed','blocked'])
def test_original_unsuccessful_attempt_stays_in_denominator(status):
    s,ex,r=fixture(status=status);out=composed(s,ex,r)
    assert out['original_attempt_count']==1 and out['blocked_count']==1
    assert out['rows'][0]['reason_code']=='original_research_'+status


def test_missing_scenario_and_other_teams_are_not_dropped():
    s,ex,r=fixture();req,_=prepared(2,'crypto_eth');other=execution(req)
    r=replace(r,records=(ex.record,other.record))
    out=composed(s,ex,r)
    assert len(out['rows'])==2 and out['missing_scenario_count']==1
    assert out['rows'][1]['status']=='scenario_missing'
    assert len(out['evaluation']['decisions'])==2


def test_later_attempt_cannot_replace_original_selection():
    s,ex,r=fixture();first=replace(ex.record,record_id='first',recorded_at=NOW,
        run=replace(ex.record.run,intake=replace(ex.record.run.intake,task_id='different',
                    task=replace(ex.record.run.intake.task,task_id='different')),
                    research=replace(ex.record.run.research,task_id='different')))
    # Even an earlier successful record forces original first-attempt selection.
    out=composed(s,ex,replace(r,records=(first,ex.record)))
    assert next(x for x in out['rows'] if x['record_id']==s.record_id)['reason_code']=='original_later_attempt'


@pytest.mark.parametrize('name,value',[
    ('requested_shares',D(0)),('requested_shares',D('NaN')),('requested_shares',D('1E1000000')),
    ('requested_shares',D('.0000001')),('min_confidence',D('1.1')),('max_entry_cost',True),
    ('max_spread',D('-1')),('max_snapshot_age_seconds',0),('max_snapshot_age_seconds',True),
    ('max_snapshot_age_seconds',601),('side','sell'),('readonly',False),('paper_only',1),
    ('request_sha256','A'*64),('cost_reference_sha256','x'),('decision_at',NOW.replace(tzinfo=None)),
])
def test_invalid_scenario_inputs(name,value):
    s,_,_=fixture()
    with pytest.raises(ValueError):replace(s,**{name:value})


@pytest.mark.parametrize('cost',[f.name for f in fields(core.ResearchPaperCosts)])
def test_every_cost_must_be_valid_and_is_counted(cost):
    s,ex,r=fixture()
    with pytest.raises(ValueError):replace(s.costs,**{cost:D('-1')})
    zero=core.ResearchPaperCosts(*(D(0) for _ in range(7)))
    row=composed(replace(s,costs=replace(zero,**{cost:D('.1')})),ex,r)['rows'][0]
    assert D(row['accounting']['modeled_costs'])==D('.3')


@pytest.mark.parametrize('change,reason',[
    ({'bids':[]},'two_sided_book_required'),({'asks':[]},'two_sided_book_required'),
    ({'bids':[dict(price='0.6',size='1')]},'spread_limit'),
    ({'asset_id':'wrong'},'book_token_mismatch'),({'market':'foreign'},'book_token_mismatch'),
    ({'min_order_size':'4'},'below_minimum_size'),
])
def test_book_scenario_blocks_with_reason(change,reason):
    s,ex,r=fixture();row=composed(book_change(s,**change),ex,r)['rows'][0]
    assert row['status']=='blocked' and row['reason_code']==reason


@pytest.mark.parametrize('change',[
    {'neg_risk':True},{'tick_size':'0'},{'min_order_size':None},{'bids':{}},
    {'asks':[dict(price='NaN',size='1')]},{'asks':[dict(price='1.01',size='1')]},
    {'asks':[dict(price='0.4',size='1'),dict(price='0.40',size='2')]},
    {'asks':[dict(price='0.405',size='1')]},{'asks':[dict(price='0.4',size='-1')]},
])
def test_invalid_book_never_silently_filters_bad_levels(change):
    s,_,_=fixture()
    with pytest.raises(ValueError):book_change(s,**change)


@pytest.mark.parametrize('change,reason',[
    ({'active':None},'market_not_open'),({'closed':True},'market_not_open'),
    ({'acceptingOrders':1},'market_not_open'),({'conditionId':'foreign'},'market_or_terms_mismatch'),
    ({'description':'changed'},'market_or_terms_mismatch'),({'question':'changed'},'market_or_terms_mismatch'),
    ({'clobTokenIds':['a','a']},'explicit_token_mapping_required'),
    ({'outcomes':['Up','Down']},'explicit_token_mapping_required'),
])
def test_market_scope_stays_closed(change,reason):
    s,ex,r=fixture();row=composed(market_change(s,**change),ex,r)['rows'][0]
    assert row['reason_code']==reason


def test_reversed_explicit_yes_no_mapping_is_not_index_fallback():
    s,ex,r=fixture();m=json.loads(s.market.raw_json)
    s=market_change(s,outcomes=m['outcomes'][::-1],clobTokenIds=m['clobTokenIds'][::-1])
    assert composed(s,ex,r)['rows'][0]['status']=='simulated'


@pytest.mark.parametrize('change,reason',[
    ({'decision_at':NOW},'decision_not_prospective'),
    ({'decision_at':NOW.replace(hour=16)},'decision_not_prospective'),
    ({'book_captured_at':NOW},'snapshot_time_mismatch'),
    ({'book_captured_at':NOW+timedelta(seconds=3)},'snapshot_time_mismatch'),
    ({'decision_at':NOW+timedelta(seconds=63)},'snapshot_stale'),
    ({'min_confidence':D('.95')},'confidence_limit'),
    ({'max_entry_cost':D('1')},'entry_cost_limit'),
    ({'min_net_edge':D('.9')},'existing_cost_edge_watch'),
])
def test_clock_risk_and_existing_cost_limits(change,reason):
    s,ex,r=fixture();row=composed(replace(s,**change),ex,r)['rows'][0]
    assert row['status']=='blocked' and row['reason_code']==reason


def test_large_cost_rejects_but_keeps_counterfactual_calculations():
    s,ex,r=fixture();s=replace(s,costs=replace(s.costs,fee=D('.9')))
    row=composed(s,ex,r)['rows'][0]
    assert row['reason_code']=='existing_cost_edge_reject' and row['accounting'] is not None


def test_wrong_request_hash_or_foreign_receipt_is_fatal():
    s,ex,r=fixture()
    with pytest.raises(ValueError):composed(replace(s,request_sha256='c'*64),ex,r)
    with pytest.raises(ValueError):composed(s,replace(ex,record=replace(ex.record,model_id='foreign')),r)


def test_bounded_scenarios_and_no_duplicate_record():
    s,ex,r=fixture()
    for value in ([],(),(s,s),(s,)*101):
        with pytest.raises(ValueError):core.scenarios_copy(value)


def test_closed_input_roundtrip_and_no_raw_source_in_repr():
    s,ex,r=fixture();assert inputs.decode_paper_scenarios(input_bytes((s,)))==(s,)
    assert PRIVATE not in repr(core.ResearchPaperEvaluation(r,(s,),(ex,)))


@pytest.mark.parametrize('field',sorted(inputs._FIELDS))
def test_input_fields_are_not_optional(field):
    s,_,_=fixture();raw=json.loads(input_bytes((s,)));del raw['scenarios'][0][field]
    with pytest.raises(ValueError):inputs.decode_paper_scenarios(json.dumps(raw).encode())


@pytest.mark.parametrize('raw',[b'',b'null',b'[]',b'{}',b'{"scenarios":[],"scenarios":[]}',b'\xff',b'0'*(core.MAX_INPUT_BYTES+1)],
                         ids=['empty','null','list','missing','duplicate','utf8','oversized'])
def test_bad_stdin_is_rejected(raw):
    with pytest.raises(ValueError):inputs.decode_paper_scenarios(raw)


@pytest.fixture
def storage(monkeypatch):
    s,ex,r=fixture();state=dict(s=s,ex=ex,r=r,calls=[])
    def evaluate(dsn,**kw):
        state['calls'].append(('evaluate',kw))
        if 'error' in state:raise state['error']
        return state['r']
    def inspect(dsn,**kw):state['calls'].append(('inspect',kw));return state['ex']
    monkeypatch.setattr(service,'load_captured_research_evaluation_with_psycopg',evaluate)
    monkeypatch.setattr(service,'inspect_captured_research_with_psycopg',inspect)
    return state


def test_strict_history_loader_precedes_original_request_lookup(storage):
    result=service.evaluate_research_paper_with_psycopg('test-seam',scenarios=(storage['s'],))
    assert result.to_dict()['simulated_count']==1
    assert [x[0] for x in storage['calls']]==['evaluate','inspect']


def test_incomplete_global_history_never_falls_back_or_filters(storage):
    storage['error']=ResearchCaptureConflict('research_execution_history_incomplete')
    with pytest.raises(ResearchCaptureConflict):service.evaluate_research_paper_with_psycopg('test',scenarios=(storage['s'],))
    assert [x[0] for x in storage['calls']]==['evaluate']


def test_invalid_or_mutated_input_precedes_database_access(storage):
    object.__setattr__(storage['s'],'readonly',False)
    with pytest.raises(ValueError):service.evaluate_research_paper_with_psycopg('test',scenarios=(storage['s'],))
    assert not storage['calls']


@pytest.fixture
def managed(monkeypatch):
    s,ex,r=fixture();state=dict(s=s,calls=[],report=core.ResearchPaperEvaluation(r,(s,),(ex,)))
    class Session:
        def evaluate_paper(self,**kw):
            state['calls'].append(kw)
            if state.get('error') is not None:raise state['error']
            return state['report']
    class DB:
        def __init__(self,root):state['opened']=True
        @contextmanager
        def session(self):
            try:yield Session()
            finally:
                state['closed']=True
                if state.get('cleanup') is not None:raise state['cleanup']
    monkeypatch.setattr(cli,'ProjectPostgres',DB)
    monkeypatch.setattr(cli.sys,'stdin',SimpleNamespace(buffer=BytesIO(input_bytes((s,)))))
    return state


def test_cli_reuses_existing_entry_and_prints_after_cleanup(managed,capsys,monkeypatch):
    original=cli.json.dumps
    def serialize(value,**kw):
        if type(value)is dict and value.get('status')=='evaluated':assert managed['closed']
        return original(value,**kw)
    monkeypatch.setattr(cli.json,'dumps',serialize)
    assert cli.main(['--paper-stdin'],default_root=ROOT)==0
    output=capsys.readouterr();assert PRIVATE not in output.out+output.err
    out=json.loads(output.out)
    assert out['business_writes_performed'] is False
    assert out['evaluation']['simulated_count']==1 and len(managed['calls'])==1


@pytest.mark.parametrize('where',['error','cleanup'])
@pytest.mark.parametrize('error',[RuntimeError(PRIVATE),KeyboardInterrupt(PRIVATE),SystemExit(0)])
def test_cli_failure_suppresses_success_and_private_text(managed,capsys,where,error):
    managed[where]=error
    assert cli.main(['--paper-stdin'],default_root=ROOT)==(130 if isinstance(error,KeyboardInterrupt) else 1)
    text=capsys.readouterr();assert PRIVATE not in text.out+text.err
    assert json.loads(text.out)['evaluation'] is None and managed['closed']


def test_cli_invalid_input_does_not_open_project(managed,capsys,monkeypatch):
    monkeypatch.setattr(cli.sys,'stdin',SimpleNamespace(buffer=BytesIO(b'{}')))
    assert cli.main(['--paper-stdin'],default_root=ROOT)==2
    assert 'opened' not in managed
