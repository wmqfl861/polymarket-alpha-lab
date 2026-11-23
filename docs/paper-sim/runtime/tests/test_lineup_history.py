"""Pregame domestic roster reference integration, freshness and hard-failure tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import time
from unittest.mock import patch

import pytest

import football_daily as daily
import football_feed as feed
import football_lineup_history as hist
import football_model as model

NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)


def fixture_and_seasons():
    kickoff = feed.utc_iso(NOW + timedelta(minutes=26))
    row = {'id': 'gamma:fl1:123', 'fixture_id': '100', 'fotmob_league_id': '53', 'league':'fl1',
           'fotmob_home_id':'4','fotmob_away_id':'5','kickoff':kickoff, 'model':{'sha256':'a'*64},'state':'scheduled'}
    calendar = {'100': {'state':'scheduled','home_id':'4','away_id':'5','kickoff':kickoff,
                        'home':'X','home_short':'X','away':'Y','away_short':'Y'}}
    for i in range(6):
        for h, a, base in [('4', str(20+i), 200), (str(30+i), '5', 300)]:
            calendar[str(base+i)]={'state':'finished','home_id':h,'away_id':a,
                                   'kickoff':feed.utc_iso(NOW-timedelta(days=(6-i)*6))}
    for mid, state, at in [('700','postponed',NOW-timedelta(days=2)),('701','cancelled',NOW-timedelta(days=1)),
                          ('702','started',NOW-timedelta(hours=1)),('703','finished',NOW-timedelta(hours=1))]:
        calendar[mid]={'state':state,'home_id':'4','away_id':'5','kickoff':feed.utc_iso(at)}
    return row, {'version':1,'seasons':{'fl1:2026':{'calendar':calendar,'calendar_at':feed.utc_iso(NOW-timedelta(minutes=4)),
                                                    'calendar_sha256':'e'*64}}}


def archived_lineup(spec):
    records = []
    for tid in spec['team_ids']:
        ids = [str(100*int(tid)+i) for i in range(1,12)]
        records.append({'starters':sorted(ids),'goalkeeper':ids[0]})
    return records


def cached_source(spec, clock, deadline):
    return hist._signed(spec | {'observed_at':feed.utc_iso(clock()),'lineups':archived_lineup(spec),
                          'raw_sha256':'f'*64,'source':hist.details_url(spec['fixture_id']),'provider':'optaSdapi'})


def with_history():
    row, seasons = fixture_and_seasons()
    with patch.object(hist, '_fetch', side_effect=cached_source) as call:
        hist.refresh([row],seasons,{},lambda:NOW-timedelta(minutes=1),time.monotonic()+20)
        assert call.call_count==10
    history=row['lineup_history']
    assert history['chronological_ids']==[['201','202','203','204','205'],['301','302','303','304','305']]
    assert '100' not in history['matches']
    return row, seasons


def context(f, *, when, confirmed):
    current = archived_lineup({'team_ids':[f['fotmob_home_id'],f['fotmob_away_id']]})
    for d in current:
        d.update(formation='4-4-2',absences={},absences_complete=True)
        if not confirmed:
            d.update(formation='',starters=[],goalkeeper=None)
    return hist._signed({'fixture_id':f['fixture_id'], 'team_ids':[f['fotmob_home_id'],f['fotmob_away_id']],
                        'source':hist.details_url(f['fixture_id']),'observed_at':feed.utc_iso(when),'kickoff':f['kickoff'],
                        'lineup_type':'standard' if confirmed else None,'lineup_provider':'optaSdapi' if confirmed else '',
                        'lineups':current})


def sign_changes(doc):
    doc['sha256']=feed.document_digest({k:v for k,v in doc.items() if k!='sha256'})


def test_context_preserves_prepublication_injuries_and_an_already_announced_side():
    fixture, _ = fixture_and_seasons()
    previous = context(fixture, when=NOW - timedelta(hours=1), confirmed=False)
    current = context(fixture, when=NOW, confirmed=True)
    assert feed.should_save_pregame_context(fixture, previous)
    fixture["review_context"] = previous
    assert not feed.should_save_pregame_context(fixture, current)
    predicted = current | {"lineup_type": "predicted"}
    sign_changes(predicted)
    assert feed.should_save_pregame_context(fixture, predicted)
    incomplete = deepcopy(predicted)
    incomplete["lineups"][0]["absences_complete"] = False
    sign_changes(incomplete)
    assert not feed.should_save_pregame_context(fixture, incomplete)
    previous["lineup_type"] = "standard"
    previous["lineups"][0] = dict(current["lineups"][0])
    sign_changes(previous)
    assert not feed.should_save_pregame_context(fixture, predicted)


def test_regular_review_keeps_held_prices_and_checks_the_earlier_lineup_expiry():
    fixture, _ = with_history()
    fixture["review_context"] = context(fixture, when=NOW - timedelta(hours=1), confirmed=False)
    fixture["model"].update(
        fixture_id=fixture["fixture_id"], kickoff=fixture["kickoff"], team_ids=["4", "5"],
        observed_at=feed.utc_iso(NOW - timedelta(minutes=2)), over_2_5_hits=8,
        variant_names=list(feed.soccer_model.SWEEP_NAMES), lambdas=[[1.65, 1.55]] * 10,
    )
    official_at = NOW - timedelta(minutes=3)
    official = context(fixture, when=official_at, confirmed=True)
    pending = context(fixture, when=official_at, confirmed=False)
    observed = NOW - timedelta(seconds=10)
    over = feed.Selection("全场大 2.5", "Over", "551", "over", .51)
    under = feed.Selection("全场小 2.5", "Under", "552", "under", .50)
    terms = {"description": feed._REGULATION, "slug": "test-o-u", "liquidityNum": 50000, "feesEnabled": True,
             "feeSchedule": {"rate": .05, "takerOnly": True, "exponent": 1}}
    market = feed.Market("test-match", terms, "456", "0x" + "a" * 64, "total", 2.5, None, (over, under))
    books = [(market, option, ([(option.reference, 2000)], observed)) for option in market.options]
    event = {"active": True, "closed": False, "startTime": fixture["kickoff"]}
    with (
        patch.object(feed, "_clock", return_value=lambda: NOW),
        patch.object(feed, "_gamma_event_refs", return_value=[("test-match", "23")]),
        patch.object(feed, "_refresh_event", return_value=event),
        patch.object(feed, "_event_markets", return_value=[market]),
        patch.object(feed, "_quoted_markets", return_value=(books, False)),
        patch.object(feed, "_fotmob_evidence", return_value=("verified", pending, None)) as latest,
        patch.object(feed, "_verify_live_contract", return_value=terms) as direct,
    ):
        watch = feed.review(fixture, NOW, "t30")
        assert watch["status"] == "watch" and "signals" not in watch
        snapshot, = watch["price_candidates"]
        assert snapshot["direction"] == over.label and snapshot["filled_usd"] == 250
        assert snapshot["model_version"] == feed.MODEL_VERSION_SOCCER and len(snapshot["variants"]) == 10
        entry, = daily.round_review_entries([{"key": "stage-1"}], {"stage-1": watch}, {}, kept=set())
        assert entry["review"]["price_candidates"] == [snapshot]
        direct.assert_not_called()
        latest.return_value = ("verified", official, None)
        accepted = feed.review(fixture, NOW, "t30")
        assert accepted["status"] == "paper"
        assert accepted["lineup_review"]["basis"] == "verified_team_history"
        assert accepted["signal"]["valid_until"] == feed.utc_iso(official_at + feed.BOOK_AGE)
        direct.assert_called_once()


def test_cold_capture_is_bound_to_target_and_cache_is_reused_in_exact_time_window():
    f, cache=with_history()
    previous=cache.pop('lineup_history')
    original=deepcopy(f['lineup_history']['matches'])
    with patch.object(hist,'_fetch',side_effect=AssertionError('unexpected duplicate fetch')):
        hist.refresh([f],cache,previous,lambda:NOW,time.monotonic()+10)
    assert original == f['lineup_history']['matches']
    assert len(cache['lineup_history']['matches'])==10
    soon=context(f,when=NOW,confirmed=True)
    early=context(f,when=NOW-timedelta(hours=1),confirmed=False)
    ok,why,proof=hist.evaluate(f,soon,early,NOW)
    assert ok and why=='verified_lineup_history'
    assert proof['teams']['home']['fixture_ids']==['201','202','203','204','205']
    assert proof['teams']['away']['current_turnover']==0
    assert proof['reference_compiled_at']==feed.utc_iso(NOW)
    assert proof['teams']['home']['observed_at']==[feed.utc_iso(NOW-timedelta(minutes=1))]*5


def test_missing_budget_and_failed_public_sources_leave_other_model_and_good_cache_usable():
    f, source=fixture_and_seasons()
    model_before=deepcopy(f['model'])
    with patch.object(hist,'_fetch') as fetched:
        hist.refresh([f],source,{},lambda:NOW,time.monotonic()-1)
        assert not fetched.called and 'lineup_history' not in f
    with patch.object(hist,'_fetch',return_value=None) as fetched:
        hist.refresh([f],source,{},lambda:NOW,time.monotonic()+20)
        assert fetched.call_count==10 and 'lineup_history' not in f
        pending=source.pop('lineup_history')
        hist.refresh([f],source,pending,lambda:NOW+timedelta(minutes=2),time.monotonic()+20)
        assert fetched.call_count==10
    assert f['model']==model_before


def test_cache_size_bound_keeps_all_existing_model_input_if_added_reference_has_no_space():
    f, source=fixture_and_seasons()
    # Set the store-sized limit to the current base cache plus too little room for a source match.
    limit=hist._size(source)+190
    with patch.object(hist,'SOURCE_CACHE_LIMIT',limit),patch.object(hist,'_fetch',side_effect=cached_source):
        hist.refresh([f],source,{},lambda:NOW,time.monotonic()+10)
    assert hist._size(source)<=limit
    assert 'lineup_history' in f and f['model']


@pytest.mark.parametrize('case,reason',[
    ('stale_current','history_lineup_identity_or_clock_invalid'),
    ('wrong_fixture','history_lineup_identity_or_clock_invalid'),
    ('same_clock','history_lineup_identity_or_clock_invalid'),
    ('missing_before','history_lineup_identity_or_clock_invalid'),
    ('predicted','history_current_xi_unconfirmed'),
    ('unknown_provider','history_current_xi_unconfirmed'),
    ('no_keeper','history_current_xi_unconfirmed'),
    ('duplicate_XI','history_current_xi_unconfirmed'),
    ('cross_team_player','history_current_xi_unconfirmed'),
    ('unknown_absences','history_availability_unknown'),
    ('injured_starter','history_starter_unavailable'),
    ('confirmed_changed','history_confirmed_lineup_changed'),
    ('one_previously_confirmed_side_changed','history_confirmed_lineup_changed'),
    ('malformed_previous_confirmed_side','history_previous_confirmed_xi_incomplete'),
    ('new_keeper','history_unfamiliar_starting_goalkeeper'),
    ('new_regular_absence','history_newly_unavailable_regular'),
    ('excess_turnover','history_abnormal_starting_xi_turnover'),
    ('target_rescheduled','history_lineup_identity_or_clock_invalid'),
    ('future_history','history_reference_unavailable'),
    ('duplicate_prior','history_reference_unavailable'),
    ('future_detail','history_prior_fixture_or_clock_invalid'),
    ('unordered_prior','history_prior_order_ambiguous'),
    ('bad_reference_signature','history_reference_unavailable'),
])
def test_evidence_failures_stay_closed(case,reason):
    f,_=with_history(); cur=context(f,when=NOW,confirmed=True); prev=context(f,when=NOW-timedelta(hours=1),confirmed=False)
    home=cur['lineups'][0]
    if case=='stale_current': cur['observed_at']=feed.utc_iso(NOW-timedelta(minutes=6))
    if case=='wrong_fixture': prev['source']=hist.details_url('999')
    if case=='same_clock': prev['observed_at']=cur['observed_at']
    if case=='missing_before': prev={}
    if case=='predicted': cur['lineup_type']='predicted'
    if case=='unknown_provider': cur['lineup_provider']=''
    if case=='no_keeper': home['goalkeeper']=None
    if case=='duplicate_XI': home['starters'][-1]=home['starters'][-2]
    if case=='cross_team_player': home['starters'][-1]=cur['lineups'][1]['starters'][1]
    if case=='unknown_absences': prev['lineups'][1]['absences_complete']=False
    if case=='injured_starter': home['absences'][home['starters'][3]]='injury'
    if case=='confirmed_changed':
        prev=context(f,when=NOW-timedelta(minutes=10),confirmed=True)
        prev['lineups'][1]['formation']='4-3-3'
    if case=='one_previously_confirmed_side_changed':
        prev=context(f,when=NOW-timedelta(minutes=10),confirmed=True)
        prev['lineups'][0].update(starters=[],starters_unpublished=True)
        prev['lineups'][1]['starters'][-1]='998'
    if case=='malformed_previous_confirmed_side':
        prev=context(f,when=NOW-timedelta(minutes=10),confirmed=True)
        prev['lineups'][0].update(starters=[],starters_unpublished=False)
    if case=='new_keeper': home['starters'][0]=home['goalkeeper']='999'
    if case=='new_regular_absence':
        old=home['starters'][-1];home['starters'][-1]='998';home['absences'][old]='injury'
        # One turnover is ordinary in the reference. Only the newly unavailable regular should block.
        match=f['lineup_history']['matches']['201'];match['lineups'][0]['starters'][-1]='997';sign_changes(match)
    if case=='excess_turnover': home['starters'][-1]='998'
    if case=='target_rescheduled': f['kickoff']=feed.utc_iso(NOW+timedelta(hours=1))
    if case=='future_history': f['lineup_history']['compiled_at']=feed.utc_iso(NOW+timedelta(minutes=1))
    if case=='duplicate_prior': f['lineup_history']['chronological_ids'][0][-1]='201'
    if case=='future_detail':
        match=f['lineup_history']['matches']['201'];match['observed_at']=feed.utc_iso(NOW+timedelta(seconds=1));sign_changes(match)
    if case=='unordered_prior': f['lineup_history']['chronological_ids'][0].reverse()
    if case=='bad_reference_signature': f['lineup_history']['policy']='unexpected'
    if case!='bad_reference_signature': sign_changes(f['lineup_history'])
    for doc in (cur,prev):
        if doc: sign_changes(doc)
    ok,why,_=hist.evaluate(f,cur,prev,NOW)
    assert not ok and why==reason


def test_fetch_rejects_wrong_identity_unfinished_and_invalid_xi_from_public_feed():
    f, seasons=fixture_and_seasons(); r=seasons['seasons']['fl1:2026']['calendar']['201'];spec=hist._match('201',r,'53')
    def raw():
        h,a=spec['team_ids']
        return {'general':{'matchId':spec['fixture_id'],'parentLeagueId':'53','homeTeam':{'id':h},'awayTeam':{'id':a},
                           'matchTimeUTCDate':spec['kickoff']},'header':{'status':{'utcTime':spec['kickoff'],'finished':True}},
                'content':{'lineup':{'matchId':'201','lineupType':'standard','source':'optaSdapi',
                    **{s+'Team':{'id':tid,'starters':[{'id':str(int(tid)*100+i),'positionId':11 if i==1 else i+60}
                           for i in range(1,12)]} for tid,s in zip((h,a),('home','away'))}}}}
    def fetched(_:str,**opts):
        opts['response_sink']('public',json.dumps(payload).encode())
        return payload
    payload=raw()
    with patch.object(hist.fotmob,'fetch_match_details',side_effect=fetched):
        actual=hist._fetch(spec,lambda:NOW,time.monotonic()+20)
        assert hist._valid_match(actual,spec,NOW) and actual['observed_at']==feed.utc_iso(NOW)
        payload=raw();payload['general']['awayTeam']['id']='999';assert hist._fetch(spec,lambda:NOW,time.monotonic()+20) is None
        payload=raw();payload['header']['status']['finished']=False;assert hist._fetch(spec,lambda:NOW,time.monotonic()+20) is None
        payload=raw();payload['content']['lineup']['homeTeam']['starters'][0]['positionId']=53
        assert hist._fetch(spec,lambda:NOW,time.monotonic()+20) is None


def test_model_refresh_invokes_reference_only_after_current_fixture_identity_and_xg_model_are_ready():
    f, sources=fixture_and_seasons()
    now=lambda:NOW-timedelta(minutes=1)
    target=sources['seasons']['fl1:2026']['calendar']['100']
    f.update(home=target['home'],away=target['away'],home_alias='',away_alias='')
    def prepare(comp,season,deadline,clock):
        if str(comp.fotmob_id)=='53' and season==2026:
            return {'calendar':{'100':target},'calendar_at':feed.utc_iso(now()),'calendar_sha256':'f'*64,'source':'fixture_test'}
        return {'calendar':{},'calendar_at':feed.utc_iso(now())}
    def enrichment(rows, cache, previous, clock, deadline):
        assert rows[0]['fixture_id']=='100' and rows[0]['model']=={'sample':True}
        assert 'fl1:2026' in cache['seasons'] and previous is None
        rows[0]['lineup_history']={'sample':'bound'}
    with patch.object(model,'_calendar',side_effect=prepare), patch.object(model,'_seed_rows',return_value={}), \
         patch.object(model,'_fixture_model',return_value={'sample':True}), \
         patch('football_totals_calibration.league_tail_inputs',return_value=None), \
         patch.object(hist,'refresh',side_effect=enrichment) as hook:
        _, cache=model.refresh_models([f],now,None)
    assert hook.called and f['lineup_history']=={'sample':'bound'} and cache['version']==1
