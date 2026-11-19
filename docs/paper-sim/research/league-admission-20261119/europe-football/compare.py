"""Freeze a UTC pregame CLOB comparison for the project's two archived soccer models.

python compare.py fetch    # network, uses the project's WP-P4 archive and scratch cache
python compare.py score    # offline, only needs comparison-inputs.json.gz in this folder
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
import threading
import time
import unicodedata

import requests

OUT = Path(__file__).resolve().parent
CACHE = Path('/home/ubuntu/test-sandbox/tmp/league-admission-20261119/domestic')
ARCHIVE = Path('/home/ubuntu/paper-sim/review/wp-p4-evidence/wp-p4-walkforward-2026-11-07.json')
LEAGUES = {'EPL':'epl', 'La_liga':'lal', 'Bundesliga':'bun', 'Serie_A':'sea', 'Championship':'elc', 'Eredivisie':'ere', 'Ligue_1':'fl1'}
ALIASES = {
    'epl': {'Coventry':'cov', 'Hull':'hul', 'Tottenham':'tot'},
    'lal': {'Atletico Madrid':'mad', 'Celta Vigo':'cel', 'Deportivo La Coruna':'dep', 'Real Betis':'bet', 'Racing Santander':'rrc'},
    'sea': {'Frosinone':'fro', 'Inter':'int'},
    'bun': {'Paderborn':'scp', 'Borussia Dortmund':'dor', 'Union Berlin':'uni', 'FC Cologne':'koe', 'Mainz 05':'mai', 'Bayern Munich':'bay', 'Elversberg':'elv', 'Bayer Leverkusen':'b04', 'RasenBallsport Leipzig':'lei', 'Borussia M.Gladbach':'moe'},
    'ere': {'NEC Nijmegen':'nec', 'PSV Eindhoven':'psv', 'AZ Alkmaar':'az'},
    'fl1': {'Troyes':'tro'},
}
THREAD = threading.local()
MODELS = ('production10', 'recalibrated')
SIDES = ('H','D','A','O2.5')
# Gamma retained the original slug when the game was postponed. The event and
# contract kickoffs, team names, rules and resolved outcome are still verified.
RESCHEDULED_SLUGS = {
    'ere-nec-exc-2026-09-08':'ere-nec-exc-2026-08-22',
    'elc-bri-lin-2026-09-15':'elc-bri-lin-2026-09-08',
    'elc-mid-mil-2026-09-15':'elc-mid-mil-2026-09-08',
    'elc-wol-por-2026-10-20':'elc-wol-por-2026-09-09',
    'fl1-tou-tro-2026-10-25':'fl1-tou-tro-2026-10-24',
    'fl1-psg-lyo-2026-10-26':'fl1-psg-lyo-2026-10-25',
    'fl1-ogc-ren-2026-10-30':'fl1-ogc-ren-2026-10-31',
    'fl1-lyo-ang-2026-11-01':'fl1-lyo-ang-2026-10-31',
    'fl1-lor-sbr-2026-11-02':'fl1-lor-sbr-2026-10-31',
}


def norm(text):
    unaccent = ''.join(ch for ch in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(ch))
    return re.sub('[^a-z0-9]', '', re.sub(r'\b(fc|afc|cf|sc|sv|sbv|ud)\b', '', unaccent))


def ts(stamp):
    return int(datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp())


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def get(url, params):
    if not hasattr(THREAD, 'session'):
        THREAD.session = requests.Session()
    key = requests.Request('GET',url,params=params).prepare().url
    file = CACHE / (hashlib.sha256(key.encode()).hexdigest() + '.json.gz')
    if file.is_file():
        with gzip.open(file, 'rt') as f:
            return json.load(f)
    for attempt in range(4):
        try:
            response = THREAD.session.get(url, params=params, timeout=18)
            response.raise_for_status()
            payload = {'url':key, 'retrieved_utc':utc_now(), 'sha256':hashlib.sha256(response.content).hexdigest(), 'data':response.json()}
            with gzip.open(file.with_suffix('.tmp'),'wt') as f:
                json.dump(payload,f,ensure_ascii=False)
            file.with_suffix('.tmp').replace(file)
            return payload
        except (requests.RequestException, ValueError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def resolve_team(name, league, teams):
    if name in ALIASES.get(league, {}):
        selected = [t for t in teams if t['abbreviation']==ALIASES[league][name]]
    else:
        selected = [t for t in teams if norm(name) in {norm(x) for x in (t.get('alias'),t.get('name')) if x}]
    if len(selected)!=1:
        raise ValueError(f'unresolved team: {league} {name}')
    return selected[0]


def market_batch(slugs):
    res = get('https://gamma-api.polymarket.com/markets', {'slug':slugs, 'closed':'true', 'limit':len(slugs)})
    markets = res['data']
    if not isinstance(markets,list) or any(m['slug'] not in slugs for m in markets):
        raise ValueError('Gamma did not filter requested market slugs')
    if len({m['slug'] for m in markets})!=len(markets):
        raise ValueError('duplicate market slug')
    return {m['slug']:(m,res['sha256']) for m in markets}


def one_market(args, markets):
    row, side, slug, team_names, base_slug = args
    if slug not in markets:
        return {'status':'missing_market', 'slug':slug}
    m, source_hash = markets[slug]
    result = {'slug':slug,'gamma_response_sha256':source_hash,'condition_id':m['conditionId'],'market_id':m['id']}
    kickoff = ts(row['kickoff'])
    target = kickoff - 3600
    market_kick = m.get('gameStartTime')
    if not market_kick or ts(market_kick)!=kickoff:
        return dict(result,status='kickoff_mismatch')
    total = side=='O2.5'
    wanted_event = base_slug + ('-more-markets' if total else '')
    e = next((v for v in m.get('events',[]) if v['slug']==wanted_event),None)
    if e is None and total:
        e = next((v for v in m.get('events',[]) if v['slug']==base_slug),None)
    names_str = ' vs. '.join(team_names)
    if not e or not e.get('startTime') or ts(e['startTime'])!=kickoff or e['title'].removesuffix(' - More Markets')!=names_str:
        return dict(result,status='event_identity_mismatch')
    rules = m.get('description','').casefold()
    if '90 minutes' not in rules or 'stoppage time' not in rules:
        return dict(result,status='regular_time_rule_unverified')
    labels = json.loads(m['outcomes'])
    team_title = m.get('groupItemTitle','')
    if total:
        valid = m.get('sportsMarketType')=='totals' and m.get('line')==2.5 and labels==['Over','Under'] and '3 or more goals' in rules and team_title=='O/U 2.5'
    else:
        expected = {'H':team_names[0], 'A':team_names[1], 'D':f'Draw ({names_str})'}[side]
        valid = m.get('sportsMarketType')=='moneyline' and labels==['Yes','No'] and team_title==expected
    if not valid:
        return dict(result,status='outcome_identity_mismatch')
    home,away=row['score']; integer_winner={'H':home>away,'D':home==away,'A':home<away,'O2.5':home+away>=3}[side]
    if list(map(float,json.loads(m['outcomePrices']))) != [float(integer_winner),float(not integer_winner)]:
        return dict(result,status='resolution_conflict_or_void')
    tokens = json.loads(m['clobTokenIds'])
    if len(tokens)!=2 or len(set(tokens))!=2:
        return dict(result,status='token_identity_missing')
    since = max(target - 600, ts(row['cutoff']))
    resp = get('https://clob.polymarket.com/prices-history', {'market':tokens[0], 'startTs':target-660,'endTs':target,'fidelity':1})
    result.update(token=tokens[0], settlement='90min+stoppage', label=labels[0], price_url=resp['url'], history_response_sha256=resp['sha256'])
    pts = [p for p in resp['data']['history'] if since<=p['t']<=target]
    if not pts:
        return dict(result,status='no_fresh_pregame_history')
    last_time = max(p['t'] for p in pts)
    values = {p['p'] for p in pts if p['t']==last_time}
    if len(values)!=1 or not (0 < next(iter(values)) < 1):
        return dict(result,status='invalid_or_conflicting_price')
    return dict(result,status='ok',t=last_time,p=values.pop())


def fetch():
    CACHE.mkdir(parents=True,exist_ok=True)
    archive_bytes = ARCHIVE.read_bytes()
    archive = json.loads(archive_bytes)
    france_file = OUT/'france-walkforward.json.gz'
    with gzip.open(france_file,'rt') as f:
        france = json.load(f)
    assert not any(r['league']=='Ligue_1' for r in archive['fixture_predictions'])
    assert all(r['league']=='Ligue_1' for r in france['fixture_predictions'])
    archive['fixture_predictions'].extend(france['fixture_predictions'])
    teams = {lg:get('https://gamma-api.polymarket.com/teams', {'league':lg,'limit':500})['data'] for lg in LEAGUES.values()}
    rows, todo = [], []
    for fx in archive['fixture_predictions']:
        if not all(m in fx['forecasts'] for m in MODELS):
            continue
        row = {k:fx[k] for k in ('match_id','league','block_id','cutoff','kickoff','home_team','away_team')}
        row['score'] = [fx['home_goals'],fx['away_goals']]
        row['models'] = {m:{'raw':fx['forecasts'][m]['raw_1x2'], 'prob':fx['forecasts'][m]['scoring_1x2'], 'over':fx['forecasts'][m]['totals_probabilities']['2.5']} for m in MODELS}
        row['markets']={}; rows.append(row)
        if ts(row['cutoff'])>ts(row['kickoff'])-3600:
            row['timing']='model_cutoff_after_T-60';continue
        row['timing']='eligible'
        lg=LEAGUES[fx['league']]
        home,away=[resolve_team(fx[k],lg,teams[lg]) for k in ('home_team','away_team')]
        code_h,code_a=home['abbreviation'],away['abbreviation']
        base=f"{lg}-{code_h}-{code_a}-{fx['kickoff'][:10]}"
        base=RESCHEDULED_SLUGS.get(base,base)
        for side,ending in zip(SIDES,(code_h,'draw',code_a,'total-2pt5')):
            todo.append((row,side,base+'-'+ending,[home['name'],away['name']],base))
    slugs = [t[2] for t in todo]
    if len(set(slugs))!=len(slugs): raise ValueError('nonunique fixture/side')
    chunks = [slugs[start:start+40] for start in range(0,len(slugs),40)]
    with ThreadPoolExecutor(6) as pool:
        markets = {k:v for batch in pool.map(market_batch,chunks) for k,v in batch.items()}
    print(f'{len(rows)} paired rows; {len(todo)} target legs; {len(markets)} Gamma matches',flush=True)
    with ThreadPoolExecutor(10) as pool:
        for (row,side,*_),res in zip(todo,pool.map(partial(one_market,markets=markets),todo)):
            row['markets'][side]=res
    with gzip.open(OUT/'comparison-inputs.json.gz','wt') as f:
        json.dump({'retrieved_utc':utc_now(),'wp_p4_archive_sha256':hashlib.sha256(archive_bytes).hexdigest(),
                   'supplemental_france_forecasts_sha256':hashlib.sha256(france_file.read_bytes()).hexdigest(),'fixtures':rows},f,ensure_ascii=False)


def block_interval(data):
    groups=defaultdict(list)
    for block,value in data: groups[block].append(value)
    totals=[(len(v),sum(v)) for v in groups.values()]
    if len(totals)<2:return None
    rng=random.Random(20261119); reps=[]
    for _ in range(5000):
        sample=rng.choices(totals,k=len(totals));reps.append(sum(x[1] for x in sample)/sum(x[0] for x in sample))
    reps.sort();return [reps[124],reps[4874]]


def evaluate(inputs,league,family):
    rows=[r for r in inputs if r['league']==league]
    reason=Counter(); accepted=[]
    for row in rows:
        if row['timing']!='eligible': reason[row['timing']]+=1;continue
        wanted=SIDES[:3] if family=='1X2' else SIDES[3:]
        legs=[row['markets'][s] for s in wanted]
        problems=sorted({x['status'] for x in legs if x['status']!='ok'})
        if problems: reason[' / '.join(problems)]+=1;continue
        if max(x['t'] for x in legs)-min(x['t'] for x in legs)>300:
            reason['unsynchronized_legs']+=1;continue
        h,a=row['score']; market=[x['p'] for x in legs]; total=sum(market); model=row['models']
        if family=='1X2':
            outcome=0 if h>a else (1 if h==a else 2)
            probs={'market':[x/total for x in market],**{m:model[m]['prob'] for m in MODELS}}
            distances={m:statistics.mean(abs(x-y) for x,y in zip(model[m]['raw'],market)) for m in MODELS}
        else:
            outcome=int(h+a<3)
            overs={'market':market[0],**{m:model[m]['over'] for m in MODELS}}
            probs={m:[p,1-p] for m,p in overs.items()}
            distances={m:abs(model[m]['over']-market[0]) for m in MODELS}
        loss={m:-math.log(v[outcome]) for m,v in probs.items()}
        accepted.append({'match_id':row['match_id'],'block':row['block_id'],'loss':loss,'mad':distances})
    result={'archive_matches':len(rows),'n':len(accepted),'blocks':len({r['block'] for r in accepted}),'excluded':dict(reason)}
    if not accepted:return result
    result['logloss']={m:statistics.mean(r['loss'][m] for r in accepted) for m in ('market',*MODELS)}
    result['raw_mad_pct_points']={m:100*statistics.mean(r['mad'][m] for r in accepted) for m in MODELS}
    result['paired']={}
    for m,ref in [('production10','market'),('recalibrated','market'),('recalibrated','production10')]:
        diff=[(r['block'],r['loss'][m]-r['loss'][ref]) for r in accepted]
        result['paired'][m+'_minus_'+ref]={'delta':statistics.mean(v for _,v in diff),'ci95':block_interval(diff)}
    return result


def score():
    with gzip.open(OUT/'comparison-inputs.json.gz','rt') as f:inputs=json.load(f)
    rows=inputs.pop('fixtures')
    summaries={lg:{family:evaluate(rows,lg,family) for family in ('1X2','O2.5')} for lg in LEAGUES}
    output={**inputs,'timing':'T-60 min, tolerance 10 min, 1X2 legs within 5 min','leagues':summaries}
    (OUT/'summary.json').write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n')
    for lg,summary in summaries.items():
        for family,r in summary.items():print(lg,family,r['n'],r['blocks'],r.get('logloss',{}),r['excluded'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['fetch','score'])
    if parser.parse_args().action=='fetch': fetch()
    else: score()
