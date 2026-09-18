"""Synthetic original-forecast to human-settlement assembly; no actual I/O."""
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path

import pytest

from polymarket_alpha_lab import research_resolution_confirmation as core
from polymarket_alpha_lab import research_resolution_confirmation_cli as cli
from polymarket_alpha_lab.research_execution import CapturedResearchRequest, CapturedResearchExecution
from polymarket_alpha_lab.research_resolution import IndependentResolutionConfirmation, ResolutionSubmission, assess_resolution
from polymarket_alpha_lab.research_resolution_codec import encode_resolution, decode_resolution
from polymarket_alpha_lab.research_resolution_store import StoredResolutionReview
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, TeamResearchResult
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, prepare_team_research_from_gamma
from polymarket_alpha_lab.team_research_market_pipeline import MarketTeamResearchRun
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationRecord, ResearchEvaluationOutcome

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 15, 11, tzinfo=UTC)
OPEN = NOW + timedelta(hours=1)
CID = '0x'+'a'*64
PRIVATE = 'synthetic-private-source-text-do-not-print'


def request(team='crypto_btc', *, at=NOW, opening=OPEN, cid=CID, record_id='original'):
    asset, ticker = ('Bitcoin', 'BTC') if team == 'crypto_btc' else ('Ethereum', 'ETH')
    month = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
             'September', 'October', 'November', 'December')[opening.month-1]
    title = f'Will the price of {asset} be above $2,000 on {month} {opening.day}, {opening.year}?'
    clock = f'{opening.hour % 12 or 12}:{opening.minute:02d} '+('AM' if opening.hour < 12 else 'PM')
    rules = (f'This market will resolve to "Yes" if the Close price of the Binance {ticker}/USDT '
        f'1-minute candle at {clock} UTC on the date in the title is above $2,000. '
        'Otherwise it will resolve to "No". Synthetic fixture only.')
    raw = dict(conditionId=cid, slug=record_id+'-market', question=title, description=rules,
        active=True, closed=False, outcomes=['Yes', 'No'], endDate=(opening+timedelta(hours=1)).isoformat())
    evidence = ResearchEvidence('source', team, cid, 'Approved synthetic input', PRIVATE, 'fixture:source', at)
    snap = GammaMarketSnapshot(raw['slug'], at, json.dumps(raw).encode())
    intake = prepare_team_research_from_gamma(snap, task_id=record_id, team_id=team,
        condition_id=cid, as_of=at, evidence=(evidence,))
    req = CapturedResearchRequest(record_id, 'synthetic-model', 'synthetic-protocol',
        opening-timedelta(seconds=1), intake, required_source_ids=('source',))
    return req, raw


def captured(req, *, status='completed', at=None):
    i = req.intake
    fields = dict(task_id=i.task_id, team_id=i.team_id, condition_id=i.condition_id,
        market_slug=i.market_slug, as_of=i.as_of, status=status,
        reason_code='research_completed' if status == 'completed' else 'model_failed')
    if status == 'completed':
        fields.update(probability_yes=Decimal('.5'), confidence=Decimal('.5'), source_ids=('source',), summary=PRIVATE)
    run = MarketTeamResearchRun(i, TeamResearchResult(**fields))
    row = ResearchEvaluationRecord(req.record_id, req.model_id, req.protocol_version, at or i.as_of, run)
    return CapturedResearchExecution(req, i.as_of, 'captured', row)


def fixture(team='crypto_btc', yes=True, *, at=NOW, opening=OPEN, cid=CID, record_id='original'):
    req, raw = request(team, at=at, opening=opening, cid=cid, record_id=record_id)
    raw.update(closed=True, acceptingOrders=False, umaResolutionStatus='resolved',
               outcomePrices=['1', '0'] if yes else ['0', '1'])
    fetched = opening+timedelta(minutes=1)
    sub = ResolutionSubmission('candidate-'+record_id, cid,
        GammaMarketSnapshot(raw['slug'], fetched, json.dumps(raw).encode()), fetched)
    candidate = StoredResolutionReview(sub, fetched)
    proof = IndependentResolutionConfirmation(cid, raw['slug'], yes, fetched,
        fetched+timedelta(seconds=1), sub.snapshot.content_sha256, 'human-reviewer',
        'https://data.binance.vision/synthetic-review', PRIVATE, independently_verified=True)
    instruction = core.CryptoSettlementReview('confirmed-'+record_id, record_id,
        req.content_sha256, sub.review_id, sha256(encode_resolution(sub).encode()).hexdigest(),
        proof, 'binance', 'BTCUSDT' if team == 'crypto_btc' else 'ETHUSDT', '1m', 'close', opening)
    return instruction, captured(req), candidate


def build(instruction, execution, candidate):
    return core.build_crypto_resolution_confirmation(instruction=instruction, execution=execution, candidate=candidate)


def receipt(sub, req):
    payload = encode_resolution(sub)
    outcome = ResearchEvaluationOutcome(sub.condition_id, sub.snapshot.market_slug, req.forecast_cutoff_at,
        sub.confirmation.resolved_at, sub.checked_at, sub.confirmation.actual_yes,
        'urn:polymarket-alpha-lab:resolution-review:'+sub.review_id, sha256(payload.encode()).hexdigest())
    return StoredResolutionReview(sub, sub.checked_at, outcome)


def input_bytes(instruction):
    value = asdict(instruction)
    for key in ('paper_only', 'report_only', 'readonly'):
        value.pop(key); value['confirmation'].pop(key)
    for key in ('resolved_at', 'confirmed_at'):
        value['confirmation'][key] = value['confirmation'][key].isoformat()
    value['source_candle_open_at'] = value['source_candle_open_at'].isoformat()
    return json.dumps(value).encode()


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('yes', [True, False])
def test_original_forecast_candidate_and_source_are_bound_verbatim(team, yes):
    i, e, c = fixture(team, yes)
    before = (e.request.payload, e.record.content_sha256, encode_resolution(c.submission))
    sub = build(i, e, c)
    assert assess_resolution(sub).status == 'ready' and sub.confirmation.actual_yes is yes
    source = json.loads(sub.confirmation.source_text)
    assert source['record_id'] == e.request.record_id
    assert source['request_sha256'] == e.request.content_sha256
    assert source['record_sha256'] == e.record.content_sha256
    assert source['candidate_payload_sha256'] == i.candidate_payload_sha256
    assert source['source_text'] == PRIVATE
    assert source['original_source_content_sha256'] == sha256(PRIVATE.encode()).hexdigest()
    assert sub.confirmation.source_content_sha256 != source['original_source_content_sha256']
    assert source['source_authentication_performed'] is False
    assert decode_resolution(encode_resolution(sub), expected_sha256=sha256(encode_resolution(sub).encode()).hexdigest()) == sub
    assert (e.request.payload, e.record.content_sha256, encode_resolution(c.submission)) == before
    assert c.outcome is None and c.submission.confirmation is None
    assert PRIVATE not in repr(i)+repr(sub)


@pytest.mark.parametrize('changes', [dict(review_id='candidate-original'), dict(review_id='bad id'),
    dict(record_id=''), dict(candidate_review_id='bad id'), dict(request_sha256='A'*64),
    dict(candidate_payload_sha256='bad'), dict(confirmation=None), dict(source_venue=''),
    dict(source_pair=3), dict(source_interval=True), dict(source_price_field='x'*33),
    dict(source_candle_open_at=OPEN.replace(tzinfo=None)), dict(readonly=False), dict(paper_only=1),
    dict(report_only=False)])
def test_invalid_instructions(changes):
    i, _, _ = fixture()
    with pytest.raises(ValueError): replace(i, **changes)


@pytest.mark.parametrize('field,value', [('record_id','foreign'), ('request_sha256','b'*64),
    ('candidate_review_id','foreign'), ('candidate_payload_sha256','b'*64),
    ('source_venue','coinbase'), ('source_pair','BTCUSD'), ('source_pair','ETHUSDT'),
    ('source_interval','1h'), ('source_price_field','high'), ('source_candle_open_at',OPEN+timedelta(seconds=1))])
def test_scope_hash_or_descriptor_mismatch(field,value):
    i,e,c=fixture()
    with pytest.raises(ValueError):build(replace(i,**{field:value}),e,c)


@pytest.mark.parametrize('changes', [dict(condition_id='0x'+'b'*64), dict(market_slug='foreign'),
    dict(gamma_content_sha256='b'*64), dict(actual_yes=False),
    dict(resolved_at=OPEN+timedelta(seconds=59)), dict(confirmed_at=OPEN+timedelta(minutes=12))])
def test_proof_mismatch_or_stale_does_not_promote(changes):
    i,e,c=fixture()
    with pytest.raises(ValueError):build(replace(i,confirmation=replace(i.confirmation,**changes)),e,c)


def test_confirm_before_candidate_recording_rejected():
    i,e,c=fixture()
    c=replace(c,recorded_at=i.confirmation.confirmed_at+timedelta(seconds=1))
    with pytest.raises(ValueError,match='time_mismatch'):build(i,e,c)


@pytest.mark.parametrize('changes', [dict(question='Changed?'), dict(description='Changed rules'),
    dict(description=None),dict(endDate='invalid')])
def test_changed_or_missing_original_terms_rejected(changes):
    i,e,c=fixture();raw=json.loads(c.submission.snapshot.raw_json);raw.update(changes)
    sub=replace(c.submission,snapshot=replace(c.submission.snapshot,raw_json=json.dumps(raw).encode()))
    c=replace(c,submission=sub)
    i=replace(i,candidate_payload_sha256=sha256(encode_resolution(sub).encode()).hexdigest(),
        confirmation=replace(i.confirmation,gamma_content_sha256=sub.snapshot.content_sha256))
    with pytest.raises(ValueError):build(i,e,c)


@pytest.mark.parametrize('status',['failed','blocked','incomplete','capture_failed'])
def test_original_failed_or_missing_forecast_never_promoted(status):
    i,e,c=fixture()
    if status in ('failed','blocked'):e=captured(e.request,status=status)
    else:e=replace(e,status=status,record=None,pending_run=e.record.run if status=='capture_failed' else None)
    with pytest.raises(ValueError,match='completed_crypto'):build(i,e,c)


def test_late_saved_prediction_rejected():
    i,e,c=fixture();e=replace(e,record=replace(e.record,recorded_at=e.request.forecast_cutoff_at))
    with pytest.raises(ValueError,match='not_prospective'):build(i,e,c)


@pytest.mark.parametrize('changes',[dict(closed=False),dict(umaResolutionStatus='disputed'),dict(outcomePrices=['0.5','0.5'])])
def test_pending_disputed_nonbinary_candidate_not_promoted(changes):
    i,e,c=fixture();raw=json.loads(c.submission.snapshot.raw_json);raw.update(changes)
    sub=replace(c.submission,snapshot=replace(c.submission.snapshot,raw_json=json.dumps(raw).encode()))
    c=replace(c,submission=sub)
    i=replace(i,candidate_payload_sha256=sha256(encode_resolution(sub).encode()).hexdigest())
    with pytest.raises(ValueError,match='unconfirmed_candidate'):build(i,e,c)


def test_equivalent_utc_instants_produce_same_confirmation():
    i,e,c=fixture();zone=timezone(timedelta(hours=8))
    other=replace(i,source_candle_open_at=i.source_candle_open_at.astimezone(zone),
        confirmation=replace(i.confirmation,resolved_at=i.confirmation.resolved_at.astimezone(zone),
                             confirmed_at=i.confirmation.confirmed_at.astimezone(zone)))
    assert encode_resolution(build(i,e,c))==encode_resolution(build(other,e,c))


@pytest.fixture
def storage(monkeypatch):
    i,e,c=fixture();state=dict(instruction=i,execution=e,candidate=c,calls=[])
    def inspect(dsn,**kw):state['calls'].append(('inspect',kw));return state['execution']
    def load(dsn,**kw):state['calls'].append(('candidate',kw));return state['candidate']
    def write(dsn,**kw):
        state['calls'].append(('write',kw))
        if state.get('error'):raise state['error']
        return state.get('receipt',receipt(kw['submission'],e.request))
    monkeypatch.setattr(core,'inspect_captured_research_with_psycopg',inspect)
    monkeypatch.setattr(core,'load_resolution_review_with_psycopg',load)
    monkeypatch.setattr(core,'record_resolution_review_with_psycopg',write)
    return state


def test_existing_writer_is_used_once_and_exact_replay_has_no_new_time(storage):
    i=storage['instruction']
    first=core.confirm_crypto_resolution_with_psycopg('test-seam',instruction=i,allow_resolution_write=True)
    second=core.confirm_crypto_resolution_with_psycopg('test-seam',instruction=i,allow_resolution_write=True)
    assert first==second
    assert [x[0] for x in storage['calls']]==['inspect','candidate','write']*2


@pytest.mark.parametrize('flag',[False,None,0,1,'true'])
def test_no_write_opt_in_means_no_db_access(storage,flag):
    with pytest.raises(ValueError):core.confirm_crypto_resolution_with_psycopg('test',instruction=storage['instruction'],allow_resolution_write=flag)
    assert not storage['calls']


@pytest.mark.parametrize('field',['execution','candidate'])
def test_missing_original_stops_before_writer(storage,field):
    storage[field]=None
    with pytest.raises(ValueError):core.confirm_crypto_resolution_with_psycopg('test',instruction=storage['instruction'],allow_resolution_write=True)
    assert all(name!='write' for name,_ in storage['calls'])


def test_invalid_input_precedes_db_access(storage):
    i=storage['instruction'];object.__setattr__(i,'readonly',False)
    with pytest.raises(ValueError):core.confirm_crypto_resolution_with_psycopg('test',instruction=i,allow_resolution_write=True)
    assert not storage['calls']


@pytest.mark.parametrize('error',[RuntimeError(PRIVATE),KeyboardInterrupt(),SystemExit(0)])
def test_storage_failure_propagates_once_without_retry(storage,error):
    storage['error']=error
    with pytest.raises(type(error)):core.confirm_crypto_resolution_with_psycopg('test',instruction=storage['instruction'],allow_resolution_write=True)
    assert [name for name,_ in storage['calls']].count('write')==1


def test_wrong_return_receipt_rejected(storage):
    storage['receipt']=storage['candidate']
    with pytest.raises(ValueError,match='receipt_mismatch'):
        core.confirm_crypto_resolution_with_psycopg('test',instruction=storage['instruction'],allow_resolution_write=True)


def test_closed_stdin_decodes_exact_instruction():
    i,_,_=fixture();assert cli.decode_review(input_bytes(i))==i


@pytest.mark.parametrize('raw',[b'',b'null',b'[]',b'{}',b'\xff',b'{"a":1,"a":2}',
    b'{"x":NaN}',b'0'*(cli.MAX_INPUT_BYTES+1),'not-bytes'],
    ids=['empty','null','array','object','invalid-utf8','duplicate-keys','nonfinite','over-64k','not-bytes'])
def test_invalid_stdin_rejected(raw):
    with pytest.raises(ValueError,match='settlement_input_invalid'):cli.decode_review(raw)


@pytest.mark.parametrize('field',sorted(cli._FIELDS))
def test_missing_stdin_field_has_no_implicit_default(field):
    i,_,_=fixture();data=json.loads(input_bytes(i));del data[field]
    with pytest.raises(ValueError):cli.decode_review(json.dumps(data).encode())


@pytest.mark.parametrize('field',sorted(cli._PROOF_FIELDS))
def test_missing_proof_field_has_no_implicit_default(field):
    i,_,_=fixture();data=json.loads(input_bytes(i));del data['confirmation'][field]
    with pytest.raises(ValueError):cli.decode_review(json.dumps(data).encode())


@pytest.fixture
def managed(monkeypatch):
    i,e,c=fixture();state=dict(instruction=i,calls=[],receipt=receipt(build(i,e,c),e.request))
    class Session:
        def confirm_crypto_resolution(self,**kw):
            state['calls'].append(kw)
            if state.get('error'):raise state['error']
            return state['receipt']
    class Database:
        def __init__(self,root):state['root']=root
        @contextmanager
        def session(self):
            try:yield Session()
            finally:
                state['closed']=True
                if state.get('cleanup_error'):raise state['cleanup_error']
    monkeypatch.setattr(cli,'ProjectPostgres',Database)
    return state


def invoke(state,capsys,raw=None,flag=True):
    code=cli.confirm_from_stdin(root=ROOT,stream=io.BytesIO(input_bytes(state['instruction']) if raw is None else raw),allow_resolution_write=flag)
    captured=capsys.readouterr();assert PRIVATE not in captured.out+captured.err
    return code,json.loads(captured.out)


def test_cli_clean_receipt_after_cleanup(managed,capsys,monkeypatch):
    original=cli.json.dumps
    def serialize(value,**kw):
        if type(value)is dict and value.get('operation')=='confirm_crypto_resolution':assert managed['closed']
        return original(value,**kw)
    monkeypatch.setattr(cli.json,'dumps',serialize)
    code,out=invoke(managed,capsys)
    assert code==0 and out['result']['linked_outcome']['actual_yes'] is True
    assert out['source_authentication_performed'] is False and managed['closed']
    assert len(managed['calls'])==1


@pytest.mark.parametrize('where',['error','cleanup_error'])
@pytest.mark.parametrize('error',[RuntimeError(PRIVATE),KeyboardInterrupt(PRIVATE),SystemExit(PRIVATE),SystemExit(0)])
def test_cli_uncertain_failure_not_success_or_no_write(managed,capsys,where,error):
    managed[where]=error;code,out=invoke(managed,capsys)
    assert code==(130 if isinstance(error,KeyboardInterrupt) else 1)
    assert out['result'] is None and out['business_writes_possible'] is True
    assert managed['closed'] and len(managed['calls'])==1


def test_invalid_input_never_opens_project(managed,capsys):
    code,out=invoke(managed,capsys,b'{}')
    assert code==2 and out['business_writes_possible'] is False and 'root' not in managed


def test_missing_opt_in_does_not_consume_stdin(managed,capsys):
    class Forbidden:
        def read(self,*a):pytest.fail('stdin consumed without explicit permission')
    code=cli.confirm_from_stdin(root=ROOT,stream=Forbidden())
    assert code==2 and 'root' not in managed


def test_script_confirmation_flags_are_mutually_exclusive(monkeypatch):
    spec=importlib.util.spec_from_file_location('resolution_queue_test',ROOT/'scripts/review_resolution_queue.py')
    script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    monkeypatch.setattr(script,'ProjectPostgres',lambda *a:pytest.fail('unexpected DB access'))
    for args in (['--confirm'],['--allow-resolution-write'],['--confirm','--allow-resolution-write','--collect','--allow-public-fetch']):
        with pytest.raises(SystemExit) as error:script.main(args)
        assert error.value.code==2


# Preserve the approved caller input when an adapter changes its own arguments.
def binding_alternative(instruction, execution, candidate, change):
    if change == 'outcome':
        return fixture(execution.request.intake.team_id, yes=False)
    if change == 'record':
        other, original, public = fixture(execution.request.intake.team_id, record_id='unapproved-original')
        return replace(other, review_id=instruction.review_id), original, public
    if change == 'review-id':
        return replace(instruction, review_id='unapproved-review'), execution, candidate
    field, value = {
        'reviewer': ('reviewer_id', 'unapproved-reviewer'),
        'source-text': ('source_text', 'different synthetic unapproved source text'),
        'source-reference': ('source_reference', 'https://data.binance.vision/synthetic-unapproved-source'),
        'time': ('confirmed_at', instruction.confirmation.confirmed_at + timedelta(seconds=2)),
    }[change]
    return replace(instruction, confirmation=replace(instruction.confirmation, **{field: value})), execution, candidate


def mutate_instruction_argument(target, replacement):
    # Deliberately model a faulty adapter, not a hostile-code security boundary.
    # Include in-place NESTED changes rather than replacing only the outer value.
    from dataclasses import fields
    for item in fields(target.confirmation):
        object.__setattr__(target.confirmation, item.name, getattr(replacement.confirmation, item.name))
    for item in fields(target):
        if item.name != 'confirmation':
            object.__setattr__(target, item.name, getattr(replacement, item.name))


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('change', ['review-id', 'reviewer', 'source-text', 'source-reference', 'time', 'outcome', 'record'])
def test_cli_receipt_cannot_replace_original_approval_by_mutating_argument(team, change, monkeypatch, capsys):
    original, execution, candidate = fixture(team)
    replacement, other_execution, other_candidate = binding_alternative(original, execution, candidate, change)
    wrong = receipt(build(replacement, other_execution, other_candidate), other_execution.request)
    calls, closed = [], []
    class Session:
        def confirm_crypto_resolution(self, *, instruction, allow_resolution_write):
            assert allow_resolution_write is True
            calls.append(instruction)
            mutate_instruction_argument(instruction, replacement)
            return wrong
    class Database:
        def __init__(self, root):
            assert root == ROOT
        @contextmanager
        def session(self):
            try:
                yield Session()
            finally:
                closed.append(True)
    monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    code = cli.confirm_from_stdin(root=ROOT, stream=io.BytesIO(input_bytes(original)), allow_resolution_write=True)
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code == 1 and value['status'] == 'failed'
    assert value['reason_code'] == 'settlement_operation_failed' and value['result'] is None
    assert value['business_writes_possible'] is True
    assert len(calls) == len(closed) == 1 and output.err == ''
    assert PRIVATE not in output.out and 'unapproved' not in output.out


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
@pytest.mark.parametrize('change', ['review-id', 'reviewer', 'source-text', 'source-reference', 'time'])
def test_confirmation_service_binds_canonical_submission_before_writer(team, change, monkeypatch):
    instruction, execution, candidate = fixture(team)
    replacement, _, _ = binding_alternative(instruction, execution, candidate, change)
    wrong_submission = build(replacement, execution, candidate)
    calls = []
    def write(dsn, *, submission):
        from dataclasses import fields
        calls.append(encode_resolution(submission))
        for item in fields(submission):
            object.__setattr__(submission, item.name, getattr(wrong_submission, item.name))
        return receipt(submission, execution.request)
    monkeypatch.setattr(core, 'inspect_captured_research_with_psycopg', lambda *a, **k: execution)
    monkeypatch.setattr(core, 'load_resolution_review_with_psycopg', lambda *a, **k: candidate)
    monkeypatch.setattr(core, 'record_resolution_review_with_psycopg', write)
    approved = encode_resolution(build(instruction, execution, candidate))
    with pytest.raises(ValueError, match='^settlement_receipt_mismatch$'):
        core.confirm_crypto_resolution_with_psycopg('synthetic', instruction=instruction, allow_resolution_write=True)
    assert calls == [approved]
    assert encode_resolution(build(instruction, execution, candidate)) == approved
