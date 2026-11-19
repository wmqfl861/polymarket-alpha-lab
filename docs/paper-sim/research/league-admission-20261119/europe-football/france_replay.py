"""Rebuild Ligue 1 separately after resolving the first-round fixture identity."""
import gzip
import hashlib
import json
from pathlib import Path
import sys

from compare import get, OUT, CACHE

REPO = Path('/home/ubuntu/test-sandbox/projects/pal-optimization-20261118')
sys.path[:0] = [str(REPO/'tools'), str(REPO/'src')]
from wp_p4_history import parse_schedule, parse_matches, build_folds, HistoryAudit, utc
from wp_p4_walkforward import select_config, evaluate_fold

DATA = Path('/home/ubuntu/paper-sim')
SOURCES = [DATA/'big5_20261108/xg_fotmob_big5_merged.json', DATA/'tools/fotmob_cache/fixtures/leagues_53_2026.json']


def replay():
    CACHE.mkdir(parents=True,exist_ok=True)
    fm = get('https://www.fotmob.com/api/data/matchDetails', {'matchId':5803105})
    espn = get('https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/summary', {'event':401876487})
    full, schedule = [json.loads(p.read_bytes()) for p in SOURCES]
    provenance = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES}
    provenance.update({x['url']:x['sha256'] for x in (fm,espn)})
    if provenance[str(SOURCES[0])] != '464e366ebfb50e58390c54ac6d8e6d9ff6237ae51331b1546e133d8daf6da052':
        raise ValueError('source xG snapshot changed')
    g=fm['data']['general']; header=fm['data']['header']; ep=espn['data']['header']['competitions'][0]
    kickoff=utc(g['matchTimeUTCDate'])
    assert g['matchId']=='5803105' and g['leagueId']==53 and g['matchRound']=='1' and g['finished']
    assert [(t['id'],t['name'],t['score']) for t in header['teams']]==[(9851,'Rennes',2),(9847,'Paris Saint-Germain',2)]
    assert header['status']['reason']['short']=='FT' and ep['status']['type']['completed'] and utc(ep['date'])==kickoff
    assert sorted((t['homeAway'],t['team']['displayName'],t['score']) for t in ep['competitors'])==[('away','Paris Saint-Germain','2'),('home','Stade Rennais','2')]
    groups=fm['data']['content']['stats']['Periods']['All']['stats']
    values={tuple(x['stats']) for grp in groups for x in grp['stats'] if x['key']=='expected_goals' and all(v is not None for v in x['stats'])}
    if len(values)!=1:raise ValueError('conflicting/missing match xG')
    xg=values.pop(); assert list(map(float,xg))==[0.87,2.16]
    current=full['Ligue_1_2026']; matches=[r for r in current['dates'] if str(r['id'])=='31948']; assert len(matches)==1
    bad=matches[0]; assert (bad['h']['title'],bad['a']['title'])==('Paris Saint Germain','Rennes') and bad['goals']=={'h':'2','a':'2'}
    current['dates'].remove(bad)
    current['dates'].append({'id':'5803105','isResult':True,'h':bad['a'],'a':bad['h'],'goals':{'h':'2','a':'2'},'xG':{'h':xg[0],'a':xg[1]},'datetime':kickoff.isoformat()})
    rosters=[]; matches=[]; official=()
    for year in ('2025','2026'):
        block=full['Ligue_1_'+year]; roster={t['title'] for t in block['teams'].values()}
        assert len(roster)==18
        rosters.extend(('Ligue_1',year,t['title'],str(t['id'])) for t in block['teams'].values())
        if year=='2026':official=parse_schedule(schedule,'Ligue_1',year,roster)
        season,excluded=parse_matches(block,'Ligue_1',year,official,str(SOURCES[0]));assert not excluded,excluded
        matches.extend(season)
    audit=HistoryAudit(tuple(matches),official,(),(),tuple(rosters))
    folds,skipped=build_folds(audit,'Ligue_1','2026'); configs=[]; predictions=[]; cache={}
    for fold in folds:
        cfg,selection=select_config(audit,fold,cache=cache,folds=folds)
        predictions.extend(evaluate_fold(audit,fold,cfg));configs.append({'block':fold.block_id,**selection})
    assert predictions and len({r['match_id'] for r in predictions})==len(predictions)
    with gzip.open(OUT/'france-walkforward.json.gz','wt') as out:
        json.dump({'sources':provenance,'excluded_original_row':bad,'verified_home_score_away':'Rennes 2–2 Paris Saint-Germain','verified_xg':xg,'fold_configs':configs,'skipped_folds':skipped,'fixture_predictions':predictions},out,ensure_ascii=False)
    print('replayed France:',len(folds),'full rounds,',len(predictions),'predictions')


if __name__=='__main__':replay()
