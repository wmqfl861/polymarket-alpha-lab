"""Synthetic integration of original research, paper depth and explicit costs."""
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal as D, getcontext, localcontext
from hashlib import sha256
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab import research_paper_inputs as inputs
from polymarket_alpha_lab import research_paper_evaluation as core
from polymarket_alpha_lab import research_evaluation_cli as cli
from polymarket_alpha_lab.cost_aware_event_strategy import PaperCostAwareEventCostAssumptions as Costs, PaperCostAwareEventStrategyConfig as Config
from polymarket_alpha_lab.research_execution import CapturedResearchRequest, CapturedResearchExecution
from polymarket_alpha_lab.research_capture_psycopg import ResearchCaptureConflict
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, TeamResearchResult
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, prepare_team_research_from_gamma
from polymarket_alpha_lab.team_research_market_pipeline import MarketTeamResearchRun
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationRecord, ResearchEvaluationOutcome, ResearchEvaluationReport

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 15, 10, tzinfo=UTC)
OPEN = NOW + timedelta(hours=1)
PRIVATE = 'synthetic-source-not-for-output'


def request(n=0, team='crypto_btc', *, at=NOW, opening=OPEN):
    cid = '0x' + format(3000 + n, '064x')
    asset, ticker = ('Bitcoin', 'BTC') if team == 'crypto_btc' else ('Ethereum', 'ETH')
    months = ('January','February','March','April','May','June','July','August','September','October','November','December')
    clock = f'{opening.hour % 12 or 12}:{opening.minute:02d} '+('AM' if opening.hour < 12 else 'PM')
    question = f'Will the price of {asset} be above $2,000 on {months[opening.month-1]} {opening.day}, {opening.year}?'
    rules = (f'This market will resolve to "Yes" if the Close price of the Binance {ticker}/USDT '
        f'1-minute candle at {clock} UTC on the date in the title is above $2,000. '
        'Otherwise it will resolve to "No". Synthetic fixture only.')
    raw = dict(conditionId=cid, slug='paper-market-'+str(n), question=question, description=rules,
        active=True, closed=False, acceptingOrders=True, outcomes=['Yes','No'], clobTokenIds=['101','202'],
        endDate=(opening+timedelta(hours=1)).isoformat())
    evidence = ResearchEvidence('source', team, cid, 'Synthetic input', PRIVATE, 'fixture:source', at)
    market = GammaMarketSnapshot(raw['slug'], at, json.dumps(raw).encode())
    intake = prepare_team_research_from_gamma(market, task_id='paper-'+str(n), team_id=team,
        condition_id=cid, as_of=at, evidence=(evidence,))
    return CapturedResearchRequest('paper-'+str(n), 'synthetic-model', 'paper-protocol',
        opening-timedelta(seconds=1), intake, required_source_ids=('source',)), raw


def execution(req, probability='.8', status='completed'):
    i = req.intake
    fields = dict(task_id=i.task_id, team_id=i.team_id, condition_id=i.condition_id,
        market_slug=i.market_slug, as_of=i.as_of, status=status,
        reason_code='research_completed' if status == 'completed' else 'model_failed')
    if status == 'completed':
        fields.update(probability_yes=D(probability), confidence=D('.9'), source_ids=('source',), summary=PRIVATE)
    record = ResearchEvaluationRecord(req.record_id, req.model_id, req.protocol_version, i.as_of,
        MarketTeamResearchRun(i, TeamResearchResult(**fields)))
    return CapturedResearchExecution(req, i.as_of, 'captured', record)


def scenario(e, raw, *, at=None):
    at = at or e.record.recorded_at + timedelta(seconds=1)
    def book(token, bid, asks):
        body = dict(asset_id=token, market=e.request.intake.condition_id,
            bids=[dict(price=bid, size='100')], asks=[dict(price=p, size=s) for p,s in asks])
        return inputs.ResearchPaperBook(at, json.dumps(body).encode())
    return inputs.ResearchPaperScenario(e.request.record_id, e.request.content_sha256, e.record.content_sha256,
        at, D('3'), GammaMarketSnapshot(e.request.intake.market_slug, at, json.dumps(raw).encode()),
        book('101','.4', [('.42','1'),('.44','2')]), book('202','.5',[('.6','10')]),
        Costs(D('.07'),D('.001'),D('0'),D('0'),D('0'),D('0')),
        Config('paper-example',D('.7'),D('.15'),D('.2'),D('1'),D('.01')),D('.1'),'synthetic-assumption',60)


def fixture(n=0, team='crypto_btc', *, probability='.8', actual=True, status='completed'):
    req, raw = request(n, team)
    e = execution(req, probability, status)
    s = scenario(e, raw)
    outcomes = () if actual is None else (ResearchEvaluationOutcome(req.intake.condition_id, req.intake.market_slug,
        req.forecast_cutoff_at, OPEN+timedelta(minutes=1), OPEN+timedelta(minutes=2), actual,
        'fixture:human-confirmed', 'c'*64),)
    report = ResearchEvaluationReport((e.record,), outcomes, OPEN+timedelta(minutes=3))
    return s,e,report


def compose(s,e,r):
    return core.compose_research_paper_evaluation(r,(s,),(e,))


def raw_input(s):
    b = asdict(s)
    for name in ('paper_only','report_only','readonly'):
        b.pop(name)
        b['market'].pop(name)
    return json.dumps([b], default=lambda x: x.decode() if type(x) is bytes else
        x.isoformat() if type(x) is datetime else str(x)).encode()


@pytest.mark.parametrize('team',['crypto_btc','crypto_eth'])
@pytest.mark.parametrize('actual',[True,False,None])
def test_depth_cash_cost_payout_and_pending_are_exact(team,actual):
    s,e,r = fixture(team=team,actual=actual)
    out = compose(s,e,r); row = out['scenarios'][0]['scenario']
    a = row['accounting']
    assert row['selected_side']=='yes' and row['fills']['yes']['average_price']=='0.433'
    assert D(a['gross_notional'])==D('1.30')  # NOT 0.433 * 3.
    assert D(a['fee_estimate'])==D('.051548')  # 1*.017052 + 2*.017248.
    assert D(a['additional_cost_estimate'])==D('.003')
    assert D(a['total_cost_estimate'])==D('1.354548')
    assert D(a['depth_slippage_not_added_twice'])==D('.04')
    assert D(a['expected_net'])==D('1.045452')
    assert row['cost_gate']['yes_result']['executable_price']=='0.44'
    if actual is None:
        assert row['payout'] is row['net_after_outcome'] is None
    else:
        assert D(row['payout'])==D('3' if actual else '0')
        assert D(row['net_after_outcome'])==(D('3' if actual else '0')-D('1.354548'))
    assert out['forward_paper_evidence'] is out['pooled_profit_computed'] is False
    assert PRIVATE not in json.dumps(out)


def test_no_side_uses_complement_and_outcome_does_not_choose_side():
    yes_case = fixture(probability='.1',actual=True)
    no_case = fixture(probability='.1',actual=False)
    a=compose(*yes_case)['scenarios'][0]['scenario'];b=compose(*no_case)['scenarios'][0]['scenario']
    assert a['selected_side']==b['selected_side']=='no'
    assert a['accounting']==b['accounting'] and a['cost_gate']==b['cost_gate']
    assert a['payout']=='0' and b['payout']=='3'


@pytest.mark.parametrize('name,value', [('min_confidence',D('1')),('max_spread',D('.01')),
    ('max_resolution_risk',D('.01')),('min_ask_size',D('4')),('min_net_edge',D('.9'))])
def test_existing_risk_and_cost_gates_not_bypassed(name,value):
    s,e,r=fixture();s=replace(s,config=replace(s.config,**{name:value}))
    row=compose(s,e,r)['scenarios'][0]
    assert row['status']=='not_selected' and row['scenario']['accounting'] is None
    assert row['scenario']['cost_gate']['selected_side']=='none'


def test_partial_depth_is_visible_but_cannot_be_selected():
    s,e,r=fixture();s=replace(s,shares=D('4'))
    row=compose(s,e,r)['scenarios'][0]['scenario']
    assert row['selected_side']=='none'
    assert row['fills']['yes']['filled_size']=='3' and row['fills']['yes']['unfilled_size']=='1'
    assert row['cost_gate']['yes_result']['ask_size']=='0'


@pytest.mark.parametrize('changes,reason', [({'conditionId':'0x'+'f'*64},'market_or_terms_mismatch'),
    ({'question':'changed'},'market_or_terms_mismatch'),({'description':PRIVATE},'market_or_terms_mismatch'),
    ({'closed':True},'market_not_open'),({'acceptingOrders':None},'market_not_open'),
    ({'outcomes':['Up','Down']},'binary_tokens_invalid'),({'clobTokenIds':['202','101']},'book_identity_mismatch')])
def test_mismatched_market_is_rejected_without_dropping_attempt(changes,reason):
    s,e,r=fixture();raw=json.loads(s.market.raw_json);raw.update(changes)
    s=replace(s,market=replace(s.market,raw_json=json.dumps(raw).encode()))
    out=compose(s,e,r);assert out['record_count']==1
    assert out['scenarios'][0]['reason_code']==reason


@pytest.mark.parametrize('which',['market','yes_book','no_book'])
@pytest.mark.parametrize('delta',[-1,2])
def test_lookahead_and_pre_forecast_snapshots_rejected(which,delta):
    s,e,r=fixture();obj=getattr(s,which);field='fetched_at' if which=='market' else 'captured_at'
    s=replace(s,**{which:replace(obj,**{field:NOW+timedelta(seconds=delta)})})
    assert compose(s,e,r)['scenarios'][0]['reason_code']=='snapshot_time_invalid'


def test_stale_future_and_after_cutoff_scenarios_rejected():
    s,e,r=fixture()
    assert compose(replace(s,simulated_at=NOW+timedelta(seconds=62)),e,r)['scenarios'][0]['reason_code']=='snapshot_stale'
    assert compose(replace(s,simulated_at=r.generated_at+timedelta(seconds=1)),e,r)['scenarios'][0]['reason_code']=='simulation_time_invalid'
    _,raw=request();s=scenario(e,raw,at=e.request.forecast_cutoff_at)
    assert compose(s,e,r)['scenarios'][0]['reason_code']=='observation_time_ineligible'


def test_missing_experiment_failed_and_later_attempt_retained():
    s,e,r=fixture()
    req,raw=request(1);failed=execution(req,status='failed')
    # A later original task for the SAME event is not a new independent sample.
    req2=replace(e.request,record_id='later',intake=replace(e.request.intake,task_id='later',
        task=replace(e.request.intake.task,task_id='later')))
    later=execution(req2);later=replace(later,record=replace(later.record,recorded_at=NOW+timedelta(seconds=2)))
    combined=ResearchEvaluationReport((e.record,failed.record,later.record),r.outcomes,r.generated_at)
    out=core.compose_research_paper_evaluation(combined,(),())
    assert out['record_count']==3 and sum(out['counts'].values())==3
    assert {x['reason_code'] for x in out['scenarios']}=={'no_supplied_books_or_costs','research_failed','later_attempt'}


@pytest.mark.parametrize('field,value',[('request_sha256','d'*64),('record_sha256','d'*64),('record_id','foreign')])
def test_tampered_identity_fails_whole_join(field,value):
    s,e,r=fixture()
    with pytest.raises(ValueError):compose(replace(s,**{field:value}),e,r)


@pytest.mark.parametrize('value',[D('NaN'),D('Infinity'),D('-1'),D('0'),D('1000001'),D('0.0000001'),1,True],
    ids=['nan','infinity','negative','zero','large','precision','int','bool'])
def test_shares_invalid(value):
    s,_,_=fixture()
    with pytest.raises(ValueError):replace(s,shares=value)


@pytest.mark.parametrize('mutation',[{'bids':None},{'asks':[{'price':'NaN','size':'1'}]},
    {'asks':[{'price':'1','size':'1'}]},{'asks':[{'price':'.5','size':'0'}]},
    {'asks':[{'price':.5,'size':'1'}]}, {'asset_id':101}, {'asks':[{'price':'.4','size':'1'}]},
    {'asks':[{'price':'.5','size':'1'},{'price':'.50','size':'1'}]}])
def test_bad_books_never_silently_drop_depth(mutation):
    s,_,_=fixture();raw=json.loads(s.yes_book.raw_json);raw.update(mutation)
    with pytest.raises((ValueError,ArithmeticError)):
        replace(s.yes_book,raw_json=json.dumps(raw).encode())


def test_decode_requires_all_costs_flags_and_no_unknown_fields():
    s,_,_=fixture();raw=raw_input(s)
    assert inputs.decode_scenarios(raw)==(s,)
    for change in ('cost','unknown','duplicate','null','number'):
        data=json.loads(raw)
        if change=='cost':del data[0]['costs']['taker_fee_rate']
        elif change=='unknown':data[0]['auto_trade']=True
        elif change=='null':data[0]['costs']['time_cost_per_share']=None
        elif change=='number':data[0]['shares']=3
        else:
            with pytest.raises(ValueError):inputs.decode_scenarios(raw.replace(b'"shares":',b'"shares":"0","shares":',1))
            continue
        with pytest.raises(ValueError):inputs.decode_scenarios(json.dumps(data).encode())


@pytest.mark.parametrize('raw',[b'',b'{}',b'null',b'\xff',b'0'*(inputs.MAX_INPUT_BYTES+1)],
    ids=['empty','object','null','utf8','oversized'])
def test_bad_stdin(raw):
    with pytest.raises(ValueError):inputs.decode_scenarios(raw)


def test_limits_duplicates_and_hard_flags():
    s,e,r=fixture()
    with pytest.raises(ValueError):core.compose_research_paper_evaluation(r,(s,s),(e,e))
    with pytest.raises(ValueError):inputs.copy_scenarios((s,)*101)
    with pytest.raises(ValueError):replace(s,readonly=False)
    with pytest.raises(ValueError):replace(s,max_snapshot_age_seconds=True)
    with pytest.raises(ValueError):replace(s,costs=replace(s.costs,taker_fee_rate=D('1.000001')))
    with pytest.raises(ValueError):replace(s,simulated_at=s.simulated_at.replace(tzinfo=None))


def test_caller_decimal_context_does_not_change_results_or_input():
    s,e,r=fixture();before=(s.content_sha256,e.request.payload,r.to_dict())
    expected=compose(s,e,r)
    with localcontext() as c:
        c.prec=6
        assert compose(s,e,r)==expected
    assert (s.content_sha256,e.request.payload,r.to_dict())==before


def test_outcome_before_declared_minute_does_not_create_profit():
    s,e,r=fixture();r=replace(r,outcomes=(replace(r.outcomes[0],resolved_at=OPEN),))
    row=compose(s,e,r)['scenarios'][0]['scenario']
    assert row['status']=='simulated_outcome_rejected' and row['payout'] is None
    assert row['selected_side']=='yes' and row['accounting'] is not None


def test_managed_loader_never_falls_back_when_history_incomplete(monkeypatch):
    calls=[]
    def blocked(*a,**kw):calls.append('history');raise ResearchCaptureConflict('research_execution_history_incomplete')
    monkeypatch.setattr(core,'load_captured_research_evaluation_with_psycopg',blocked)
    monkeypatch.setattr(core,'_local_transaction',lambda *a,**k:pytest.fail('original lookup after history rejection'))
    s,_,_=fixture()
    with pytest.raises(ResearchCaptureConflict):core.evaluate_research_paper_with_psycopg('test',scenarios=(s,))
    assert calls==['history']


def test_managed_wrapper_uses_original_read_only_lookup(monkeypatch):
    s,e,r=fixture();calls=[]
    monkeypatch.setattr(core,'load_captured_research_evaluation_with_psycopg',lambda *a,**k:r)
    monkeypatch.setattr(core,'_lookup',lambda c,id:e if id==s.record_id else None)
    def read(dsn,op,**kw):assert kw=={'readonly':True};calls.append(1);return op(object())
    monkeypatch.setattr(core,'_local_transaction',read)
    assert core.evaluate_research_paper_with_psycopg('test',scenarios=(s,)).to_dict()==compose(s,e,r)
    assert len(calls)==1


@pytest.fixture
def managed(monkeypatch):
    s,e,r=fixture();state=dict(calls=[],result=core.ResearchPaperReplay(r,(s,),(e,)))
    class Session:
        def evaluate_paper(self,**kw):
            state['calls'].append(kw)
            if 'error' in state:raise state['error']
            return state['result']
    class DB:
        def __init__(self,root):state['root']=root
        @contextmanager
        def session(self):
            try:yield Session()
            finally:
                state['closed']=True
                if 'cleanup' in state:raise state['cleanup']
    monkeypatch.setattr(cli,'ProjectPostgres',DB)
    monkeypatch.setattr(cli.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(raw_input(s))))
    return state


def test_existing_evaluation_cli_reads_once_and_cleans_up(managed,capsys):
    assert cli.main(['--paper-scenarios'],default_root=ROOT)==0
    out=json.loads(capsys.readouterr().out)
    assert out['evaluation']==managed['result'].to_dict() and managed['closed']
    assert len(managed['calls'])==1 and out['business_writes_performed'] is False


@pytest.mark.parametrize('where',['error','cleanup'])
@pytest.mark.parametrize('error',[RuntimeError(PRIVATE),KeyboardInterrupt(PRIVATE),SystemExit(PRIVATE)])
def test_cli_errors_do_not_print_success_or_raw_text(managed,capsys,where,error):
    managed[where]=error
    assert cli.main(['--paper-scenarios'],default_root=ROOT)==(130 if isinstance(error,KeyboardInterrupt) else 1)
    captured=capsys.readouterr();assert PRIVATE not in captured.out+captured.err
    out=json.loads(captured.out);assert out['evaluation'] is None and managed['closed']


def test_cli_invalid_stdin_precedes_database(managed,capsys,monkeypatch):
    monkeypatch.setattr(cli.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'{}')))
    assert cli.main(['--paper-scenarios'],default_root=ROOT)==2
    assert 'root' not in managed
