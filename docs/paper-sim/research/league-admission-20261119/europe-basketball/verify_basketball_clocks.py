#!/usr/bin/env python3
"""Verify Germany BBL and BCL tips against published match pages and archived scores."""

import argparse
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from backtest import write_csv
from fetch_sources import CUTOFF, instant, read
from replay_markets import choose_fixture, similarity


SITE = 'https://www.betexplorer.com'
LISTINGS = (
    ('Germany BBL', 2026, 'bbl-be-now.html', '/basketball/germany/bbl/results/'),
    ('Germany BBL', 2025, 'bbl-be-2526.html', '/basketball/germany/bbl-2025-2026/results/'),
    ('Germany BBL', 2025, 'bbl-be-2526-main.html', '/basketball/germany/bbl-2025-2026/results/?stage=rXnrx7Ca'),
    ('Germany BBL', 2025, 'bbl-be-2526-playin.html', '/basketball/germany/bbl-2025-2026/results/?stage=4tA9hSdt'),
    ('Basketball Champions League', 2026, 'BCL-BE-2026.html', '/basketball/europe/champions-league/results/'),
)


def cached(path, url):
    if not path.exists():
        path.write_bytes(read(url))
    return BeautifulSoup(path.read_bytes(), 'html.parser')


def listed_date(raw, year):
    if raw == 'Yesterday' and year == 2026:
        return CUTOFF.date() - timedelta(days=1)
    if raw == 'Today' and year == 2026:
        return CUTOFF.date()
    match = re.fullmatch(r'(\d{2})\.(\d{2})\.(\d{4})?', raw)
    if not match:
        return None
    d, m, y = match.groups()
    return date(int(y) if y else year + int(int(m) < 7), int(m), int(d))


def listed_games(cache, candidates):
    submissions = {}
    for league, year, filename, endpoint in LISTINGS:
        source = urljoin(SITE, endpoint)
        soup = cached(cache / filename, source)
        table = soup.select_one('table.table-main')
        if not table:
            raise ValueError(f'No match table at {source}')
        for entry in table.select('tr'):
            link = entry.select_one('a.in-match')
            cells = entry.find_all('td', recursive=False)
            if not link or len(cells) < 5:
                continue
            names = [team.get_text(' ', strip=True) for team in link.find_all('span', recursive=False)]
            scores = re.match(r'^(\d{1,3}):(\d{1,3})\b', cells[1].get_text(' ', strip=True))
            day = listed_date(cells[-1].get_text(' ', strip=True), year)
            if not day or day >= CUTOFF.date() or not scores or len(names) != 2:
                continue
            home, away = names
            row, direct, confidence = choose_fixture(candidates[league, str(day)], home, away)
            if not row or not direct or confidence < .87:
                continue
            official_score = (int(row['final_home']), int(row['final_away']))
            if tuple(map(int, scores.groups())) != official_score:
                continue
            url = urljoin(SITE, link['href'])
            previous = submissions.get(url)
            if previous and previous[0]['game_key'] != row['game_key']:
                raise ValueError(f'Game URL maps to multiple scores: {url}')
            submissions[url] = (row, home, away, day, official_score)
    return submissions


def verified_tip(pair, event_soup):
    url, (row, listed_home, listed_away, day, scores) = pair
    item = None
    for tag in event_soup.find_all('script', {'type': 'application/ld+json'}):
        if not tag.string:
            continue
        candidate = json.loads(tag.string)
        if candidate.get('@type') == 'SportsEvent':
            item = candidate
            break
    if not item or not item.get('startDate') or item.get('eventStatus') != 'https://schema.org/EventEnded':
        return None
    encoded = item.get('name', '').split(' - ', 1)
    if len(encoded) != 2:
        return None
    home, away = encoded
    if similarity(home, listed_home) < .90 or similarity(away, listed_away) < .90:
        return None
    tip = instant(item['startDate'])
    if not tip or tip.date() != day:
        return None
    return {'league': row['league'], 'game_key': row['game_key'], 'tip_utc': tip.isoformat(),
            'source_url': item.get('url') or url, 'home': row['home'], 'away': row['away'],
            'home_points': scores[0], 'away_points': scores[1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with (args.output / 'game_predictions.csv').open(encoding='utf8') as file:
        predictions = list(csv.DictReader(file))
    with (args.output / 'polymarket_market_replay.csv').open(encoding='utf8') as file:
        ids = {row['game_key'] for row in csv.DictReader(file)
               if row['league'] in {'Germany BBL', 'Basketball Champions League'} and row['game_key']}
    candidates = defaultdict(list)
    for row in predictions:
        if row['game_key'] in ids:
            candidates[row['league'], row['date']].append(row)
    event_links = listed_games(args.cache, candidates)
    print('Independently matched score records', len(event_links), flush=True)

    def fetch_link(pair):
        url, _ = pair
        try:
            slug = url.strip('/').split('/')[-1]
            prefix = 'BBL' if '/germany/' in url else 'BCL'
            soup = cached(args.cache / f'{prefix}-event-{slug}.html', url)
            return verified_tip(pair, soup)
        except Exception as error:
            print(f'{url}: {error}', flush=True)
            return None

    with ThreadPoolExecutor(8) as pool:
        values = [row for row in pool.map(fetch_link, event_links.items()) if row]
    by_game = {}
    for row in values:
        key = row['league'], row['game_key']
        if key in by_game and by_game[key]['tip_utc'] != row['tip_utc']:
            raise ValueError(f'Conflicting archived start times for {key}')
        by_game[key] = row
    rows = sorted(by_game.values(), key=lambda row: (row['league'], row['tip_utc'], row['game_key']))
    write_csv(args.output / 'germany_bbl_bcl_verified_clocks.csv', rows)
    print(Counter(row['league'] for row in rows), flush=True)


if __name__ == '__main__':
    main()
