#!/usr/bin/env python3
"""Download keyless basketball archives and Polymarket public market data."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlencode
import requests


CUTOFF = datetime(2026, 11, 19, tzinfo=timezone.utc)
HOLDOUT_FROM = datetime(2025, 7, 1, tzinfo=timezone.utc)
EARLIEST_YEAR = 2018
LEAGUES = {
    'EuroLeague': ('euroleague', 102669),
    'EuroCup': ('bkeurocup', 105266),
    'Basketball Champions League': ('bkcl', 103093),
    'Spain ACB': ('bkligend', 103094),
    'Germany BBL': ('bkbbl', 104345),
    'Italy Serie A': ('bkseriea', 103095),
    'France Pro A': ('bkfr1', 103098),
    'France Pro B': ('bkfr2', 105829),
    'VTB United League': ('bkvtb', 104344),
}
EURO_API = 'https://api-live.euroleague.net/v2/competitions'
KAGGLE_API = ('https://www.kaggle.com/api/v1/datasets/download/'
              'archangel0x01/european-club-basketball-results-and-odds?datasetVersionNumber=3')
VTB_API = 'https://api.vtb-league.com/v2/leagues/vtb/seasons'
VTB_INFO = 'https://org.infobasket.su/Comp'
VTB_COMPETITIONS = {2019: 30051, 2020: 32984, 2021: 34829, 2022: 37492,
                    2023: 39799, 2024: 42729, 2025: 46399}
FIBA_BASE = 'https://www.fiba.basketball/en/history/112-fiba-mens-european-club-competitions-tier-1'
FIBA_EDITIONS = {2019: 208046, 2020: 208136, 2021: 208247, 2022: 208407,
                 2023: 208494, 2024: 208737, 2025: 208850, 2026: 208962}
GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'


def read(url, data=None):
    for attempt in range(3):
        try:
            response = requests.request('POST' if data is not None else 'GET', url,
                                        json=data, timeout=35)
            response.raise_for_status()
            return response.content
        except requests.HTTPError as exc:
            if exc.response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def public_sources(directory):
    paths = {
        'european-odds.zip': KAGGLE_API,
        'BCL-BE-2026.md': 'https://r.jina.ai/https://www.betexplorer.com/basketball/europe/champions-league/results/',
        'BCL-teams-2027.md': 'https://r.jina.ai/https://www.championsleague.basketball/en/teams',
        'BCL-teams-2026.md': f'https://r.jina.ai/{FIBA_BASE}/208962/teams',
        'VTB-infoseasons.json': f'{VTB_INFO}/GetSeasonsForTag?format=json&tag=vtb',
        'VTB-api-2026.json': f'{VTB_API}/2026/matches?limit=1000',
        'VTB-api-2027.json': f'{VTB_API}/2027/matches?limit=1000',
        'polymarket-sports.json': f'{GAMMA}/sports',
    }
    for year, competition in VTB_COMPETITIONS.items():
        paths[f'VTB-info-{year}.json'] = f'{VTB_INFO}/GetCalendar/?format=json&comps={competition}'
    for year, edition in FIBA_EDITIONS.items():
        paths[f'FIBA-{year}.md'] = f'https://r.jina.ai/{FIBA_BASE}/{edition}/games'
    for code in 'EU':
        for year in range(EARLIEST_YEAR, CUTOFF.year + 1):
            season = f'{code}{year}'
            paths[f'{season}.json'] = f'{EURO_API}/{code}/seasons/{season}/games'

    def get(pair):
        name, url = pair
        path = directory / name
        if not path.exists():
            content = read(url)
            if name.endswith('.zip') and not content.startswith(b'PK'):
                raise ValueError(f'Expected zip from {url}')
            path.write_bytes(content)
        print(name, path.stat().st_size, flush=True)

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(get, (pair for pair in paths.items() if not pair[0].startswith('VTB-info-'))))
    for pair in paths.items():
        if pair[0].startswith('VTB-info-'):
            if not (directory / pair[0]).exists():
                time.sleep(1)
            get(pair)
    write_json(directory / 'source-urls.json', paths)


def instant(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def load_gamma(directory):
    def get(item):
        league, (sport, tag) = item
        path = directory / f'gamma-{sport}.json'
        if path.exists():
            return sport, json.loads(path.read_text())
        page_size = 100
        events = {}
        queries = []
        for offset in range(0, 5000, page_size):
            params = {'tag_id': tag, 'closed': 'true', 'order': 'startTime',
                      'ascending': 'false', 'limit': page_size, 'offset': offset}
            url = f'{GAMMA}/events?{urlencode(params)}'
            rows = json.loads(read(url))
            queries.append({'url': url, 'rows': len(rows)})
            before_period = 0
            for event in rows:
                tip = instant(event.get('startTime'))
                settled = instant(event.get('closedTime'))
                if tip and tip < HOLDOUT_FROM:
                    before_period += 1
                if tip and HOLDOUT_FROM <= tip < CUTOFF and settled and settled < CUTOFF:
                    events[event['id']] = event
            if len(rows) < page_size or before_period == len(rows):
                break
        else:
            raise RuntimeError(f'{league}: pagination cap reached')
        values = list(events.values())
        write_json(path, values)
        write_json(directory / f'gamma-{sport}-queries.json', queries)
        print(sport, len(values), flush=True)
        return sport, values

    with ThreadPoolExecutor(6) as pool:
        return dict(pool.map(get, LEAGUES.items()))


def market_jobs(event_rows):
    for event in event_rows:
        event_tip = instant(event['startTime'])
        for market in event.get('markets', []):
            if market.get('sportsMarketType') not in ('moneyline', 'totals', 'spreads'):
                continue
            tip = instant(market.get('gameStartTime')) or event_tip
            if abs(tip - event_tip) > timedelta(minutes=15):
                continue
            tokens = json.loads(market.get('clobTokenIds') or '[]')
            listed = instant(market.get('startDate'))
            anchor = tip - timedelta(hours=1)
            if len(tokens) != 2 or (listed and listed > anchor):
                continue
            yield int(anchor.timestamp()), tokens


def price_batches(jobs):
    token_cutoffs = {}
    for cutoff, tokens in sorted(jobs, key=lambda job: job[0]):
        if token_cutoffs and (len(token_cutoffs) >= 20 or cutoff - min(token_cutoffs.values()) > 21600):
            yield token_cutoffs
            token_cutoffs = {}
        for token in tokens:
            if token in token_cutoffs:
                raise ValueError(f'Duplicated token {token}')
            token_cutoffs[token] = cutoff
    if token_cutoffs:
        yield token_cutoffs


def load_prices(directory, listings):
    for sport, events in listings.items():
        path = directory / f'prices-{sport}-extended.json'
        if path.exists():
            continue
        batches = list(price_batches(market_jobs(events)))

        def get(cutoffs):
            payload = {'markets': list(cutoffs), 'fidelity': 5,
                       'start_ts': min(cutoffs.values()) - 1800,
                       'end_ts': max(cutoffs.values())}
            result = json.loads(read(f'{CLOB}/batch-prices-history', payload))['history']
            return {token: [point for point in result.get(token, [])
                            if cutoff - 1800 <= point['t'] <= cutoff]
                    for token, cutoff in cutoffs.items()}

        histories = {}
        with ThreadPoolExecutor(9) as pool:
            for prices in pool.map(get, batches):
                if set(histories).intersection(prices):
                    raise ValueError(f'Duplicate token history in {sport}')
                histories.update(prices)
        write_json(path, histories)
        print(sport, len(histories), sum(bool(values) for values in histories.values()), 'tokens', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('--only', choices=('sources', 'gamma', 'prices', 'all'), default='all')
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    if args.only in ('sources', 'all'):
        public_sources(args.cache)
    if args.only in ('gamma', 'prices', 'all'):
        listings = load_gamma(args.cache)
        if args.only in ('prices', 'all'):
            load_prices(args.cache, listings)


if __name__ == '__main__':
    main()
