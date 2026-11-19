#!/usr/bin/env python3
"""Pair frozen European basketball predictions with exact market lines and public prices."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, timedelta
import difflib
import io
import json
import math
from pathlib import Path
import re
from zipfile import ZipFile

import numpy as np

from backtest import bi_outcomes, kaggle_games, week_ci, words, write_csv
from fetch_sources import CLOB, LEAGUES, instant, write_json


MARKETS = {'moneyline': 'moneyline', 'totals': 'total', 'spreads': 'spread'}
NAME_EQUIVALENTS = {
    'amici pallacanestro udinese': 'udine', 'pallacanestro udinese': 'udine',
    'olympiacos b c': 'olympiacos', 'fc universitatea cluj': 'cluj napoca',
    'bahcesehir koleji': 'bahcesehir', 'bahcesehir college istanbul': 'bahcesehir',
    'cb 1939 canarias': 'tenerife', 'la laguna tenerife': 'tenerife',
    'basketball loewen braunschweig': 'braunschweig', 'loewen braunschweig': 'braunschweig',
    'bayern munchen': 'bayern munich', 'munchen': 'munich', 'koeln': 'koln',
    'asvel lyon villeurbanne': 'asvel', 'lyon villeurbanne': 'asvel', 'ldlc asvel': 'asvel',
    'joventut badalona': 'joventut', 'gravelines dunkerque': 'gravelines', 'gravelines dunkerque basketball': 'gravelines',
    'pallacanestro reggiana': 'reggiana', 'reggio emilia': 'reggiana', 'pallacanestro varese': 'varese',
    'pallacanestro trieste': 'trieste', 'pallacanestro brescia': 'brescia', 'scaligera verona': 'verona',
    'unione sportiva victoria libertas pallacanestro': 'pesaro', 'pallacanestro cantu': 'cantu',
    'pistoia basket 2000': 'pistoia', 'scafati basket': 'scafati', 'treviso basket': 'treviso',
    'scaligera basket verona': 'verona', 'derthona basket': 'tortona', 'bcc derthona tortona': 'tortona',
    'a b c napoli': 'napoli', 'basket napoli': 'napoli', 'napoli basket': 'napoli', 'napoli basketball': 'napoli',
    'zenit saint petersburg': 'zenit', 'zenit st petersburg': 'zenit', 'mba mai': 'mba', 'mba moscow': 'mba',
    'avtodor saratov': 'avtodor', 'enisei krasnoyarsk': 'enisey', 'enisey krasnoyarsk': 'enisey',
    'pari nn': 'nizhny novgorod', 'paris nizhny novgorod': 'nizhny novgorod', 'pari nizhny novgorod': 'nizhny novgorod',
    'parma parimatch perm': 'parma', 'basketball club parma': 'parma', 'uralmash ekaterinburg': 'uralmash',
    'uralmash yekaterinburg': 'uralmash', 'czech basketball nymburk': 'nymburk', 'era nymburk': 'nymburk',
    'united basketball club': 'united', 'rytas vilnius': 'rytas', 'legia warszawa': 'legia', 'legia warsaw': 'legia',
    'falco vulcano szombathely': 'szombathely', 'falco kc szombathely': 'szombathely',
    'sig strasbourg': 'strasbourg', 'strasbourg ig': 'strasbourg', 'karsiyaka basket': 'karsiyaka',
    'fenerbahce beko': 'fenerbahce', 'olimpia milano': 'olimpia milan', 'milan': 'milano',
    'anadolu efes': 'efes', 'turk telekom': 'turk telekom', 'fenerbahce ulker': 'fenerbahce',
    'valencia basket': 'valencia', 'real madrid baloncesto': 'real madrid', 'dinamo sassari': 'sassari',
    'aylik u bt cluj napoca': 'cluj napoca', 'u bt cluj napoca': 'cluj napoca', 'cluj napoca': 'cluj napoca',
    'c s m u bt cluj napoca': 'cluj napoca', 'recoletas salud san pablo burgos': 'burgos',
    'san pablo burgos': 'burgos', 'dolomiti energia trento': 'trento', 'aquila basket trento': 'trento',
    'pallacanestro brescia leonessa': 'brescia', 'chemnitz 99': 'chemnitz', 'niners chemnitz': 'chemnitz',
    'nurnberg falcons': 'nurnberg', 'telekom baskets bonn': 'bonn', 'fraport skyliners': 'frankfurt',
    'skyliners frankfurt': 'frankfurt', 'frankfurt skyliners': 'frankfurt', 'ewe baskets oldenburg': 'oldenburg',
    'neo phoenix': 'san en', 'uni baskets munster': 'munster', 'rostocker seawolves': 'rostock',
    'rostocker seewolfe': 'rostock', 'rasta vechta': 'vechta', 'bamberg baskets': 'bamberg',
    'bmw park': 'bayern', 'fitness first wurzburg baskets': 'wurzburg', 'wurze­burg': 'wurzburg',
    'cosea jl bourg en bresse': 'bourg', 'jl bourg en bresse': 'bourg', 'jl bourg': 'bourg',
    'tofas bursa': 'tofas', 'galatasaray mct technic': 'galatasaray', 'surne bilbao basket': 'bilbao',
    'filou oostende': 'oostende', 'kalev cramo': 'kalev', 'b c wolves': 'wolves',
    'turk telekom ankara': 'turk telekom', 'b c buducnost podgorica': 'buducnost',
    'buducnost voli podgorica': 'buducnost', 'buducnost voli': 'buducnost',
    'lech le mans': 'le mans', 'lemans': 'le mans', 'chalon saone': 'chalon',
    'elan chalon': 'chalon', 'pallacanestro reyer venezia': 'venezia', 'umana reyer venice': 'venezia',
    'umana reyer venezia': 'venezia', 'hapoel b c': 'hapoel', 'bnei penlink herzliya': 'herzliya',
    'bnei herzliya': 'herzliya', 'yoast united': 'yoast', 'vilpas vikings': 'vilpas',
    'semcesi': 'sc derby', 'studentski centar': 'sc derby', 'bbc bayreuth': 'bayreuth',
    's oliver wurzburg': 'wurzburg', 'lauwen braunschweig': 'braunschweig', 'medi bayreuth': 'bayreuth',
}


def normalized(value):
    import unicodedata
    value = unicodedata.normalize('NFKD', value.lower()).encode('ascii', 'ignore').decode('ascii')
    value = ' '.join(re.findall(r'[a-z0-9]+', value))
    return NAME_EQUIVALENTS.get(value, value)


def similarity(first, second):
    a, b = normalized(first), normalized(second)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    left, right = words(a), words(b)
    shared = left & right
    distinct = shared - {'hapoel', 'maccabi', 'tel', 'aviv', 'baskets', 'basketbol', 'tel', 'club', 'bc'}
    if shared and distinct and (left <= right or right <= left):
        return .96
    overlap = len(shared) / max(1, min(len(left), len(right)))
    fuzzy = difflib.SequenceMatcher(None, a, b).ratio()
    if distinct and len(distinct) >= 2:
        return max(fuzzy, .76 + .17 * overlap)
    return fuzzy


def choose_fixture(candidates, first, second):
    ranks = []
    for row in candidates:
        home, away = row['home'], row['away']
        reverse_score = min(similarity(first, away), similarity(second, home))
        direct_score = min(similarity(first, home), similarity(second, away))
        score = max(direct_score, reverse_score)
        if score < .78 or abs(direct_score - reverse_score) < .09:
            continue
        ranks.append((score, direct_score > reverse_score, row))
    ranks.sort(key=lambda match: match[0], reverse=True)
    if not ranks or (len(ranks) > 1 and ranks[0][0] - ranks[1][0] < .08):
        return None, None, None
    return ranks[0][2], ranks[0][1], ranks[0][0]


def event_names(event):
    for market in event.get('markets', []):
        if market.get('sportsMarketType') == 'moneyline':
            names = json.loads(market.get('outcomes') or '[]')
            if len(names) == 2 and set(names) not in ({'Yes', 'No'}, {'Over', 'Under'}):
                return names
    pieces = re.split(r'\s+vs\.?\s+', event.get('title', ''), maxsplit=1, flags=re.I)
    return pieces if len(pieces) == 2 else ('', '')


def normal_probability(delta, sigma):
    return .5 * (1 + math.erf(delta / (sigma * math.sqrt(2))))


def total_probs(row, line):
    return (normal_probability(float(row['baseline_total']) - line, float(row['baseline_total_sigma'])),
            normal_probability(float(row['candidate_total']) - line, float(row['candidate_total_sigma'])))


def market_model(game, kind, market, outcomes):
    if kind == 'total':
        line = float(market['line'])
        assert 'o/u' in market['question'].lower()
        if line % 1 != .5 or set(outcomes) != {'Over', 'Under'}:
            raise ValueError('unsupported total line or outcome labels')
        y = int(float(game['final_total']) > line)
        return line, outcomes.index('Over'), y, *total_probs(game, line)
    left, right = outcomes
    first_scores = similarity(left, game['home']), similarity(left, game['away'])
    second_scores = similarity(right, game['home']), similarity(right, game['away'])
    direct = min(first_scores[0], second_scores[1])
    reverse = min(first_scores[1], second_scores[0])
    if max(direct, reverse) < .78 or abs(direct - reverse) < .09:
        raise ValueError('unverified team outcome orientation')
    index_home = int(reverse > direct)
    if kind == 'moneyline':
        return None, index_home, int(game['winner_home']), float(game['baseline_p_home']), float(game['candidate_p_home'])
    question = re.fullmatch(r'Spread: (.+) \(([+−-]?\d+(?:\.\d+)?)\)', market['question'])
    if not question or similarity(question[1], left) < .9:
        raise ValueError('unverified spread orientation')
    signed = float(question[2].replace('−', '-'))
    if signed != float(market['line']) or signed % 1 != .5:
        raise ValueError('unsupported spread line')
    line = signed if index_home == 0 else -signed
    p = normal_probability(float(game['baseline_margin']) + line, float(game['baseline_margin_sigma']))
    o = normal_probability(float(game['candidate_margin']) + line, float(game['candidate_margin_sigma']))
    return line, index_home, int(float(game['final_margin']) + line > 0), p, o


def compare_market(game, event, market, prices, similarity_score):
    kind = MARKETS[market['sportsMarketType']]
    tip = instant(market.get('gameStartTime')) or instant(event['startTime'])
    event_tip = instant(event['startTime'])
    archive_tip = instant(game['tip_utc']) if game else None
    earliest_tip = min(point for point in (tip, event_tip, archive_tip) if point)
    anchor = earliest_tip - timedelta(hours=1)
    row = {'league': game['league'] if game else '', 'market': kind, 'line': market.get('line'),
           'market_id': market['id'], 'condition_id': market.get('conditionId'), 'market_slug': market['slug'],
           'event_slug': event['slug'], 'event_title': event['title'], 'game_key': '', 'source_home': '',
           'source_away': '', 'source_tip_utc': '', 'date': str(tip.date()),
           'split': 'holdout' if tip.date() < date(2026, 7, 1) else 'current', 'tip_utc': tip.isoformat(),
           'anchor_utc': anchor.isoformat(), 'event_tip_utc': event_tip.isoformat(),
           'anchor_basis': 'earliest independent/Gamma tip' if archive_tip else 'Gamma tip only',
           'independent_tip_source': game.get('independent_tip_source', game['score_source']) if archive_tip else '',
           'event_closed_utc': (instant(event['closedTime']).isoformat() if event.get('closedTime') else ''),
           'scoreboard_tip_delta_minutes': (tip - archive_tip).total_seconds() / 60 if archive_tip else '',
           'age_a_seconds': '', 'age_b_seconds': '',
           'source': f"https://polymarket.com/event/{event['slug']}", 'selected_token': '', 'other_token': '',
           'selected_price_utc': '', 'other_price_utc': '', 'score_source': game['score_source'] if game else '',
           'rule_explicitly_includes_overtime': int(bool(re.search(r'(?:include|includes|including) (?:any )?overtime',
                                                       market.get('description', ''), re.I))),
           'scoreboard_overtime': '', 'settlement_agrees_with_scoreboard': '',
           'status': 'scoreboard fixture missing or ambiguous', 'match_similarity': similarity_score,
           'label': '', 'actual': '', 'baseline_p': '', 'candidate_p': '', 'selected_price': '', 'other_price': '',
           'market_p': ''}
    if not game:
        return row
    row.update(game_key=game['game_key'], source_home=game['home'], source_away=game['away'],
               source_tip_utc=game['tip_utc'], split=game['split'], scoreboard_overtime=game['known_overtime'])
    times = [value for value in (tip, event_tip, archive_tip) if value]
    if max(times) - min(times) > timedelta(minutes=15):
        row['status'] = 'source tip times differ by more than 15 min'
        return row
    outcomes = json.loads(market.get('outcomes') or '[]')
    tokens = json.loads(market.get('clobTokenIds') or '[]')
    if len(outcomes) != 2 or len(tokens) != 2:
        row['status'] = 'unsupported binary metadata'
        return row
    try:
        line, focus, actual, baseline, candidate = market_model(game, kind, market, outcomes)
    except (KeyError, TypeError, AssertionError, ValueError) as err:
        row['status'] = f'line or outcome mapping: {err}'
        return row
    selected, other = tokens[focus], tokens[1 - focus]
    row.update(line=line, label=outcomes[focus], actual=actual, baseline_p=baseline, candidate_p=candidate,
               selected_token=selected, other_token=other)
    settlement = [float(p) for p in json.loads(market.get('outcomePrices') or '[]')]
    if settlement not in ([0.0, 1.0], [1.0, 0.0]):
        row['status'] = 'void, disputed or non-binary resolution'
        return row
    row['settlement_agrees_with_scoreboard'] = int(settlement[focus] == actual)
    if settlement[focus] != actual:
        row['status'] = 'official scoreboard and Polymarket settlement disagree'
        return row
    feature_cutoff = instant(game['prediction_time_utc'])
    if feature_cutoff > anchor:
        row['status'] = 'market anchor precedes model prediction'
        return row
    listed = instant(market.get('startDate'))
    closed = instant(event.get('closedTime'))
    if closed and closed <= anchor:
        row['status'] = 'event was already closed by target anchor'
        return row
    if not listed or listed > anchor:
        row['status'] = 'line not proven listed by target anchor'
        return row
    anchor_seconds = int(anchor.timestamp())
    both = []
    for token in (selected, other):
        points = [point for point in prices.get(token, [])
                  if anchor_seconds - 900 <= point['t'] <= anchor_seconds and point['t'] >= listed.timestamp()]
        if not points:
            row['status'] = 'no fresh two-sided CLOB price history'
            return row
        both.append(max(points, key=lambda point: point['t']))
    a, b = both
    row.update(selected_price=a['p'], other_price=b['p'],
               selected_price_utc=(anchor - timedelta(seconds=anchor_seconds - a['t'])).isoformat(),
               other_price_utc=(anchor - timedelta(seconds=anchor_seconds - b['t'])).isoformat(),
               age_a_seconds=anchor_seconds - a['t'], age_b_seconds=anchor_seconds - b['t'])
    if abs(a['t'] - b['t']) > 300 or not (.90 <= a['p'] + b['p'] <= 1.10):
        row['status'] = 'two-sided timestamps or complement check failed'
        return row
    if not row['rule_explicitly_includes_overtime']:
        status = 'diagnostic: overtime wording unverified'
    elif not archive_tip:
        status = 'diagnostic: game start unverified outside Polymarket'
    else:
        status = 'matched price history'
    row.update(status=status, market_p=a['p'] / (a['p'] + b['p']))
    return row


def polymarket_replay(cache, index):
    detail = []
    n_events = {}
    for league, (sport, _) in LEAGUES.items():
        events = json.loads((cache / f'gamma-{sport}.json').read_text())
        prices = json.loads((cache / f'prices-{sport}-extended.json').read_text())
        n_events[league] = len(events)
        for event in events:
            tip = instant(event['startTime'])
            event_day = event.get('eventDate') or str(tip.date())
            names = event_names(event)
            game, _, score = choose_fixture(index[league, event_day], *names)
            other_day_game = None
            if game is None and league == 'EuroLeague':
                day = date.fromisoformat(event_day)
                adjacent = [row for offset in (-1, 1)
                            for row in index[league, str(day + timedelta(days=offset))]]
                other_day_game, _, _ = choose_fixture(adjacent, *names)
            for market in event.get('markets', []):
                if market.get('sportsMarketType') not in MARKETS:
                    continue
                row = compare_market(game, event, market, prices, score)
                row['league'] = league
                if other_day_game:
                    row.update(status='scoreboard fixture played on a different UTC date',
                               game_key=other_day_game['game_key'], source_home=other_day_game['home'],
                               source_away=other_day_game['away'], source_tip_utc=other_day_game['tip_utc'])
                detail.append(row)
    return detail, n_events


def archive_moneyline(cache, predictions, index):
    for row in predictions:
        if row['source_avg_home_odds'] and row['source_avg_away_odds']:
            yield row, float(row['source_avg_home_odds']), float(row['source_avg_away_odds']), row['game_key']
    for game in kaggle_games(cache):
        if game.league not in ('EuroLeague', 'EuroCup') or game.season < 2025 or not game.odds_home or not game.odds_away:
            continue
        row, direct, _ = choose_fixture(index[game.league, str(game.day)], game.home_name, game.away_name)
        if not row:
            continue
        score = int(row['final_home']), int(row['final_away'])
        if score != ((game.home_score, game.away_score) if direct else (game.away_score, game.home_score)):
            continue
        home, away = (game.odds_home, game.odds_away) if direct else (game.odds_away, game.odds_home)
        yield row, home, away, game.key


def france_pro_b_totals(cache, predictions):
    games = {row['game_key'].removeprefix('kaggle-'): row for row in predictions
             if row['league'] == 'France Pro B' and row['tip_utc']}
    selected = {}
    with ZipFile(cache / 'european-odds.zip') as archive, archive.open('closing_odds.csv') as source:
        for quote in csv.DictReader(io.TextIOWrapper(source, encoding='utf8')):
            key = quote['game_id']
            if key not in games or quote['market'] != 'total' or not (quote['line'] and quote['over_odds'] and quote['under_odds']):
                continue
            row = games[key]
            line = float(quote['line'])
            if line % 1 != .5:
                continue
            cutoff = instant(row['tip_utc']) - timedelta(hours=1)
            timestamp = instant(quote['last_update_utc'])
            if not timestamp or not cutoff - timedelta(minutes=15) <= timestamp <= cutoff:
                continue
            over, under = float(quote['over_odds']), float(quote['under_odds'])
            if min(over, under) <= 1 or 1 / over + 1 / under > 1.2:
                continue
            implied = (1 / over) / (1 / over + 1 / under)
            rank = (-timestamp.timestamp(), abs(implied - .5), quote['bookmaker'], line)
            if key not in selected or rank < selected[key][0]:
                selected[key] = rank, quote, row, cutoff, timestamp
    for _, quote, row, cutoff, timestamp in selected.values():
        line = float(quote['line'])
        over, under = float(quote['over_odds']), float(quote['under_odds'])
        baseline, candidate = total_probs(row, line)
        yield {'league': 'France Pro B', 'split': row['split'], 'date': row['date'], 'game_key': row['game_key'],
               'market': 'total', 'line': line, 'bookmaker': quote['bookmaker'], 'anchor_utc': cutoff.isoformat(),
               'scoreboard_overtime': row['known_overtime'], 'semantics': 'bookmaker overtime wording unavailable',
               'quote_utc': timestamp.isoformat(), 'odds_over': over, 'odds_under': under,
               'baseline_p': baseline, 'candidate_p': candidate,
               'market_p': (1 / over) / (1 / over + 1 / under), 'actual': int(float(row['final_total']) > line)}


def performance(rows):
    columns = ('baseline_p', 'candidate_p', 'market_p')
    y = np.array([int(row['actual']) for row in rows])
    points = {name: np.array([float(row[name]) for row in rows]) for name in columns}
    result = {name: bi_outcomes(values, y) for name, values in points.items()}
    days = [date.fromisoformat(row['date']) for row in rows]
    for model in ('baseline_p', 'market_p'):
        loss = (points['candidate_p'] - y) ** 2 - (points[model] - y) ** 2
        result[f'candidate_minus_{model}_brier_95_ci'] = week_ci(loss, days)
    return {'rows': len(rows), 'unique_games': len({row['game_key'] for row in rows}), **result}


def summarize(clob, sportsbooks, archive_totals, events):
    output = {}
    for league in LEAGUES:
        league_clob = [row for row in clob if row['league'] == league]
        league_external = [row for row in sportsbooks if row['league'] == league]
        output[league] = {'gamma_events': events[league], 'clob': {}, 'sportsbook': {}, 'diagnostics': {}}
        for kind in MARKETS.values():
            relevant = [row for row in league_clob if row['market'] == kind]
            status = Counter(row['status'] for row in relevant)
            matched = [row for row in relevant if row['status'] == 'matched price history']
            if relevant:
                output[league]['clob'][kind] = {
                    'listings': len(relevant), 'listed_unique_events': len({r['event_slug'] for r in relevant}),
                    'statuses': dict(status),
                    'accepted_anchor_basis': dict(Counter(r['anchor_basis'] for r in matched)),
                    'accepted_rule_wording': dict(Counter(r['rule_explicitly_includes_overtime'] for r in matched)),
                    'accepted_overtime_status': dict(Counter(r['scoreboard_overtime'] or 'unknown' for r in matched)),
                    'settlement_check': dict(Counter(r['settlement_agrees_with_scoreboard'] for r in relevant)),
                }
                for split in ('all', 'holdout', 'current'):
                    sample = [row for row in matched if split == 'all' or row['split'] == split]
                    if sample:
                        output[league]['clob'][kind][split] = performance(sample)
            for reason, key in (('diagnostic: overtime wording unverified', 'unknown_overtime_wording'),
                                ('diagnostic: game start unverified outside Polymarket', 'gamma_only_start')):
                diagnostic = [row for row in relevant if row['status'] == reason]
                if diagnostic:
                    output[league]['diagnostics'][f'clob_{kind}_{key}'] = {
                        **performance(diagnostic),
                        'overtime_observed': dict(Counter(row['scoreboard_overtime'] or 'unknown' for row in diagnostic)),
                    }
            external = [row for row in league_external if row['market'] == kind]
            if external:
                output[league]['sportsbook'][kind] = {split: performance(sample)
                    for split in ('holdout', 'current') if (sample := [row for row in external if row['split'] == split])}
        if league == 'France Pro B' and archive_totals:
            output[league]['diagnostics']['sportsbook_totals_unknown_overtime_wording'] = {
                split: {**performance(sample), 'overtime_observed':
                         dict(Counter(row['scoreboard_overtime'] or 'unknown' for row in sample))}
                for split in ('holdout', 'current')
                if (sample := [row for row in archive_totals if row['split'] == split])
            }
    return output


def attach_verified_clocks(predictions, paths):
    targets = {(row['league'], row['game_key']): row for row in predictions}
    for path in paths:
        with path.open(encoding='utf8') as source:
            for record in csv.DictReader(source):
                key = record['league'], record['game_key']
                row = targets.get(key)
                if not row:
                    raise ValueError(f'Independent clock points to an unknown game: {key}')
                exact_scores = (int(row['final_home']), int(row['final_away'])) == (
                    int(record['home_points']), int(record['away_points']))
                named_teams = min(similarity(row['home'], record['home']),
                                  similarity(row['away'], record['away'])) >= .86
                if not exact_scores or not named_teams or not record['source_url']:
                    raise ValueError(f'Independent clock failed identity verification: {key}')
                clock = instant(record['tip_utc'])
                existing = instant(row['tip_utc'])
                if not clock or (existing and clock != existing):
                    raise ValueError(f'Conflicting independent times for {key}')
                row['tip_utc'] = clock.isoformat()
                row['independent_tip_source'] = record['source_url']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--verified-clocks', action='append', type=Path, default=[])
    args = parser.parse_args()
    with (args.output / 'game_predictions.csv').open(encoding='utf8') as source:
        predictions = list(csv.DictReader(source))
    attach_verified_clocks(predictions, args.verified_clocks)
    index = defaultdict(list)
    for row in predictions:
        index[row['league'], row['date']].append(row)
    clob, events = polymarket_replay(args.cache, index)
    write_csv(args.output / 'polymarket_market_replay.csv', clob)
    external_ml = []
    for row, home, away, key in archive_moneyline(args.cache, predictions, index):
        if min(home, away) <= 1.0:
            continue
        external_ml.append({'league': row['league'], 'split': row['split'], 'date': row['date'],
                            'game_key': row['game_key'], 'sportsbook_source_key': key, 'market': 'moneyline',
                            'odds_home': home, 'odds_away': away, 'baseline_p': row['baseline_p_home'],
                            'candidate_p': row['candidate_p_home'], 'market_p': (1 / home) / (1 / home + 1 / away),
                            'actual': row['winner_home'], 'source': row['score_source'] if key == row['game_key'] else
                            'https://www.kaggle.com/datasets/archangel0x01/european-club-basketball-results-and-odds'})
    write_csv(args.output / 'sportsbook_closing_moneyline_comparison.csv', external_ml)
    archive_totals = list(france_pro_b_totals(args.cache, predictions))
    if archive_totals:
        write_csv(args.output / 'sportsbook_france_pro_b_timed_totals.csv', archive_totals)
    source = {'polymarket_api': f'{CLOB}/batch-prices-history',
              'polymarket_reference': 'both token prices within 15 minutes before earliest available tip-minus-60 minutes',
              'sportsbook_ml_reference': 'historical results-page average closes with no individual observation clock',
              'sportsbook_pro_b_totals_diagnostic': 'archived closing line last updated within 15 min before tip-minus-60, overtime wording unverified, all available regulation and OT games included',
              'leagues': summarize(clob, external_ml, archive_totals, events)}
    write_json(args.output / 'market_metrics.json', source)
    for league, result in source['leagues'].items():
        print(league, result['gamma_events'], {kind: value.get('all', {}).get('rows', 0)
              for kind, value in result['clob'].items()}, flush=True)


if __name__ == '__main__':
    main()
