"""Independently verify dates and tip times for Italian, Spanish and French basketball."""
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.parse import urljoin

from lxml import html
import requests

BASE = 'https://www.betexplorer.com'
LEAGUES = {'Italy Serie A':('italy','lega-a'), 'Spain ACB':('spain','acb'), 'France Pro A':('france','lnb')}
HERE = Path(__file__).resolve().parent
SIBLING = HERE.parent/'europe-basketball'
CACHE = Path('/home/ubuntu/test-sandbox/tmp/league-admission-20261119/club-basketball-clocks')
SESS = threading.local()


def page(url):
    f=CACHE/(hashlib.sha256(url.encode()).hexdigest()+'.html.gz')
    if f.is_file():return gzip.decompress(f.read_bytes())
    if not hasattr(SESS,'session'): SESS.session=requests.Session()
    for attempt in range(3):
        try:
            r=SESS.session.get(url,timeout=14);r.raise_for_status()
            if not r.url.rstrip('/').startswith(url.split('?')[0].rstrip('/')):raise ValueError('redirected page')
            f.write_bytes(gzip.compress(r.content,mtime=0));return r.content
        except requests.RequestException:
            if attempt==2:raise
            time.sleep(attempt+1)


def norm(name):
    return ''.join(c for c in unicodedata.normalize('NFKD',name.lower()) if c.isalnum())


def index(league):
    country, division=LEAGUES[league]; matches={}
    for year in ['-2025-2026','']:
        url=f'{BASE}/basketball/{country}/{division}{year}/results/'
        root=html.fromstring(page(url)); stages=[urljoin(url,a) for a in root.xpath('//a[starts-with(@href,"?stage=")]/@href')]
        for stage in (stages or [url]):
            dom=html.fromstring(page(stage))
            for a in dom.xpath('//a[contains(@class,"in-match")]'):
                tr=a.xpath('./ancestor::tr')[0];td=tr.xpath('./td'); names=[s.text_content().strip() for s in a.xpath('./span')];
                if len(names)!=2 or len(td)<3:continue
                score=re.fullmatch(r'(\d{1,3})\s*:\s*(\d{1,3})(?:\s*(?:AET|OT|AOT|\dOT))?',td[1].text_content().strip(),re.I)
                if not score:continue
                matchurl=urljoin(url,a.get('href'));matches[matchurl]=(names[0],names[1],int(score[1]),int(score[2]))
    return matches


def verify(task):
    row,url,display=task
    raw=page(url); dom=html.fromstring(raw)
    schema=[json.loads(s) for s in dom.xpath('//script[@type="application/ld+json"]/text()')];events=[e for e in schema if isinstance(e,dict) and e.get('@type')=='SportsEvent'];
    if len(events)!=1:return None,'missing event metadata'
    event=events[0];stamp=datetime.fromisoformat(event['startDate']);
    if stamp.utcoffset() is None or event.get('sport')!='Basketball' or event.get('eventStatus')!='https://schema.org/EventEnded':return None,'unsupported scope'
    # The archive's JSON-LD splits hyphenated club names. The two ordered
    # team profile links in the match header retain the complete names.
    header_names=[a.text_content().strip() for a in dom.xpath('//a[contains(@href,"/basketball/team/")]') if a.text_content().strip()]
    event_names=event.get('name','').split(' - ')
    schema_names=[event['homeTeam']['name'],event['awayTeam']['name']]
    exact_names=next((names for names in [event_names, schema_names, header_names]
                      if [norm(v) for v in names]==[norm(v) for v in display]),None)
    if exact_names is None:return None,'match page team conflict'
    if event.get('url','').rstrip('/')!=url.rstrip('/'):return None,'match page URL mismatch'
    wall=dom.xpath('string(//*[@id="match-date"]/@data-dt)')
    if not wall or datetime.strptime(wall,'%d,%m,%Y,%H,%M').replace(tzinfo=timezone(timedelta(hours=1)))!=stamp:return None,'two page clock fields conflict'
    utc=stamp.astimezone(timezone.utc)
    if str(utc.date())!=row['date']:return None,'date mismatch'
    return dict(league=row['league'],game_key=row['game_key'],tip_utc=utc.isoformat(),source_url=url,home=row['home'],away=row['away'],home_points=row['final_home'],away_points=row['final_away'],source_home=exact_names[0],source_away=exact_names[1],archived_start=event['startDate'],source_sha256=hashlib.sha256(raw).hexdigest()),None


def collect():
    CACHE.mkdir(parents=True,exist_ok=True)
    with (SIBLING/'game_predictions.csv').open() as f: games=[r for r in csv.DictReader(f) if r['league'] in LEAGUES]
    with (SIBLING/'polymarket_market_replay.csv').open() as f: target_keys={(r['league'],r['game_key']) for r in csv.DictReader(f) if r['league'] in LEAGUES and r['game_key']}
    # The league's published final regular-season round had a common start
    # time for all seven games, including the originally unlinked Napoli game.
    target_keys.update((r['league'],r['game_key']) for r in games if r['league']=='Italy Serie A' and r['date']=='2026-05-10')
    targets=[r for r in games if (r['league'],r['game_key']) in target_keys]
    index_by_league={};tasks=[]; missing=Counter();by_score=defaultdict(list)
    with ThreadPoolExecutor(3) as ex:
        for league,entries in zip(LEAGUES,ex.map(index,LEAGUES)):
            index_by_league[league]=entries
            for url,(h,a,hg,ag) in entries.items():by_score[(league,norm(h),norm(a),hg,ag)].append((url,(h,a)))
    for r in targets:
        key=(r['league'],norm(r['home']),norm(r['away']),int(r['final_home']),int(r['final_away']));hits=by_score.get(key,[])
        if not hits:missing[(r['league'],'teams and final score not found')]+=1
        for url,display in hits:tasks.append((r,url,display))
    print('indices',[(l,len(e)) for l,e in index_by_league.items()],'identified event keys',len(targets),'fetching verified pages',len(tasks),flush=True)
    hits=defaultdict(list)
    with ThreadPoolExecutor(9) as executor:
        for (r,url,display),(result,err) in zip(tasks,executor.map(verify,tasks)):
            if result: hits[(r['league'],r['game_key'])].append(result)
            elif err:missing[(r['league'],err)]+=1
    output=[v[0] for v in hits.values() if len(v)==1];assert len(output)==len(hits)
    output.sort(key=lambda r:(r['league'],r['tip_utc'],r['game_key']));target=HERE/'verified.csv';temp=target.with_suffix('.tmp')
    with temp.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(output[0]));w.writeheader();w.writerows(output)
    temp.replace(target);print('verified',Counter(r['league'] for r in output),'missing',missing)


if __name__=='__main__':collect()
