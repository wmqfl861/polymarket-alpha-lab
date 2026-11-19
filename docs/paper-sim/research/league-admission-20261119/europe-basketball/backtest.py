#!/usr/bin/env python3
"""Walk-forward European basketball baselines and validation-selected models."""

import argparse
from collections import Counter, defaultdict, deque
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import io
import json
from itertools import groupby, product
import math
from pathlib import Path
import re
import unicodedata
from zipfile import ZipFile

import numpy as np

from fetch_sources import (CUTOFF, EARLIEST_YEAR, EURO_API, FIBA_EDITIONS, instant,
                           LEAGUES, VTB_API, VTB_COMPETITIONS, VTB_INFO, write_json)


TUNE_START = 2022
HOLDOUT_START = 2025
CURRENT_START = 2026
UTC = timezone.utc
DAY = timedelta(days=1)
RESULT_DELAY = timedelta(hours=6)
BCL = 'Basketball Champions League'
BCL_SCORES = 'https://www.betexplorer.com/basketball/europe/champions-league/results/'


@dataclass(frozen=True)
class Game:
    league: str
    key: str
    season: int
    day: date
    tip: datetime | None
    home: str
    away: str
    home_name: str
    away_name: str
    phase: str
    neutral: bool
    home_score: int
    away_score: int
    overtime: bool | None
    odds_home: float | None
    odds_away: float | None
    source: str

    @property
    def margin(self):
        return self.home_score - self.away_score

    @property
    def total(self):
        return self.home_score + self.away_score

    @property
    def home_win(self):
        return int(self.margin > 0)

    @property
    def cutoff(self):
        # A day-open snapshot precedes all of that day's tip-offs and T−60 anchors.
        return datetime.combine(self.day, time.min, tzinfo=UTC)

    @property
    def available_at(self):
        if self.tip:
            return self.tip + RESULT_DELAY
        # Day-only archives carry no finish clock. Add a full day after the listed day.
        return self.cutoff + 2 * DAY


def euro_games(directory):
    for code, league in (('E', 'EuroLeague'), ('U', 'EuroCup')):
        for year in range(EARLIEST_YEAR, CUTOFF.year + 1):
            season = f'{code}{year}'
            path = directory / f'{season}.json'
            payload = json.loads(path.read_text())
            assert len(payload['data']) == payload['total'], season
            for row in payload['data']:
                if not row['played'] or row['gameStatus'] == 'Cancelled':
                    continue
                home, away = row['local'], row['road']
                tip = instant(row.get('utcDate'))
                if not tip or tip >= CUTOFF:
                    continue
                yield Game(league, row['identifier'], year, tip.date(), tip,
                           home['club']['code'], away['club']['code'], home['club']['name'],
                           away['club']['name'], row['phaseType']['code'], bool(row['isNeutralVenue']),
                           home['score'], away['score'], bool(home['partials']['extraPeriods']),
                           None, None, f'{EURO_API}/{code}/seasons/{season}/games')


def kaggle_games(directory):
    with ZipFile(directory / 'european-odds.zip') as archive, archive.open('games.csv') as file:
        for row in csv.DictReader(io.TextIOWrapper(file, encoding='utf8')):
            league = row['competition']
            if league not in LEAGUES:
                continue
            year = int(row['season'][:4])
            if year < EARLIEST_YEAR:
                continue
            tip = instant(row['start_time_utc'])
            day = tip.date() if tip else date.fromisoformat(row['date'])
            if day >= CUTOFF.date():
                continue
            overtime = {'True': True, 'False': False}.get(row['overtime'])
            yield Game(league, f"kaggle-{row['game_id']}", year, day, tip, row['home_team'],
                       row['away_team'], row['home_team'], row['away_team'], row['round'], False,
                       int(row['home_score']), int(row['away_score']), overtime,
                       float(row['avg_home_odds']) if row['avg_home_odds'] else None,
                       float(row['avg_away_odds']) if row['avg_away_odds'] else None,
                       'https://www.kaggle.com/datasets/archangel0x01/european-club-basketball-results-and-odds')


BE_ROW = re.compile(r'^\| \[(.+?)\]\((https://[^)]+)\) \| \[(\d{1,3}):(\d{1,3})([^]]*)\]'
                    r'\([^)]+\) \| (.*?) \| (.*?) \| (.*?) \|\s*$')
FIBA_ROW = re.compile(r'\]\((https://[^)]+/games/(\d+)-[^)]+)\)$')
FIBA_SCORE = re.compile(r'\)\s+([A-Z0-9]+)(?:\s+\1)?\s+(\d{1,3})(?=\s+!\[|\]\()')
FIBA_TEAM = re.compile(r'organisation_(\d+)[^)]*\) ## ([^\]]+?) ([A-Z0-9]+)\]\(')
FIBA_ORG = re.compile(r'organisation_(\d+)[^)]*\) ([A-Z0-9]+)\s')


def words(value):
    value = unicodedata.normalize('NFKD', value.casefold()).encode('ascii', 'ignore').decode('ascii')
    return set(re.findall(r'[a-z0-9]+', value)) - {'basket', 'basketball', 'basquet', 'bc', 'kk', 'sk', 'fc'}


def bcl_games(directory):
    archived = []
    global_codes = defaultdict(set)
    year_codes = {}
    latest_names = {}
    for file_year in (*FIBA_EDITIONS, 2027):
        team_path = directory / f'BCL-teams-{file_year}.md'
        if team_path.exists():
            for org, name, code in FIBA_TEAM.findall(team_path.read_text()):
                global_codes[code].add(org)
                year_codes[file_year, code] = org
                if file_year == 2027:
                    latest_names[org] = name
        if file_year == 2027:
            continue
        path = directory / f'FIBA-{file_year}.md'
        source = next(line.removeprefix('URL Source: ') for line in path.read_text().splitlines()
                      if line.startswith('URL Source: '))
        day = None
        count = 0
        for line in path.read_text().splitlines():
            if re.fullmatch(r'\d{1,2} [A-Z][a-z]+ \d{4}', line):
                day = datetime.strptime(line, '%d %B %Y').date()
                continue
            if not line.startswith('[Regular Season') or 'Final' not in line or not day:
                continue
            ids = FIBA_ROW.search(line)
            points = FIBA_SCORE.findall(line)
            if not ids or len(points) != 2:
                continue
            for org, code in FIBA_ORG.findall(line):
                global_codes[code].add(org)
                year_codes[file_year, code] = org
            (_, game_id), ((home, h), (away, a)) = ids.groups(), points
            archived.append((file_year, day, game_id, home, away, int(h), int(a), source))
            count += 1
        if count < 85:
            raise ValueError(f'Only {count} regular-season matches parsed from {path.name}')
    for year, day, key, home, away, h, a, source in archived:
        def organization(code):
            recorded = year_codes.get((year, code))
            alternatives = global_codes[code]
            return recorded or (next(iter(alternatives)) if len(alternatives) == 1 else code)
        yield Game(BCL, f'fiba-{key}', year - 1, day, None, organization(home), organization(away),
                   latest_names.get(organization(home), home), latest_names.get(organization(away), away),
                   'Regular Season', False, h, a, None, None, None, source)

    def current_org(name):
        manual = {'Slavia Prague ERA NBK': 'slavia', 'Spartak Subotica': 'spartak',
                  'Joventut Badalona': 'joventut', 'AEK Athens': 'aek', 'Bilbao': 'bilbao',
                  'Strasbourg': 'strasbourg', 'Bonn': 'bonn', 'Juventus': 'juventus'}
        tokens = words(manual.get(name, name))
        matches = [org for org, official in latest_names.items() if tokens <= words(official)]
        return matches[0] if len(matches) == 1 else name

    # BetExplorer's current Main tab supplies the rest of the current regular-season scores.
    path = directory / 'BCL-BE-2026.md'
    for line in path.read_text().splitlines():
        row = BE_ROW.fullmatch(line)
        if not row:
            continue
        name, url, home_score, away_score, overtime_label, odds_home, odds_away, label = row.groups()
        if ' - ' not in name:
            continue
        home, away = name.replace('*', '').split(' - ', 1)
        if label == 'Yesterday':
            day = CUTOFF.date() - DAY
        elif label == 'Today':
            day = CUTOFF.date()
        elif re.fullmatch(r'\d{2}\.\d{2}\.(?:\d{4})?', label):
            d, m, *calendar_year = [int(value) for value in label.split('.') if value]
            day = date(calendar_year[0] if calendar_year else CURRENT_START + int(m < 7), m, d)
        else:
            continue
        if not date(2026, 10, 6) <= day < CUTOFF.date():
            continue
        yield Game(BCL, f'betexplorer-{url.strip("/").split("/")[-1]}', CURRENT_START, day, None,
                   current_org(home), current_org(away), home, away, 'Regular Season', False,
                   int(home_score), int(away_score), 'AET' in overtime_label.upper(),
                   float(odds_home) if re.fullmatch(r'\d+\.\d+', odds_home) else None,
                   float(odds_away) if re.fullmatch(r'\d+\.\d+', odds_away) else None, BCL_SCORES)


def vtb_games(directory):
    league = 'VTB United League'
    for year, comp in VTB_COMPETITIONS.items():
        path = directory / f'VTB-info-{year}.json'
        games = json.loads(path.read_text())
        for row in games:
            if row['GameStatus'] != 1 or row['LeagueNameEn'] != league:
                continue
            day = datetime.strptime(row['GameDate'], '%d.%m.%Y').date()
            tip = None
            if row['HasTime'] and row.get('GameTimeMsk'):
                tip = instant(f'{day}T{row["GameTimeMsk"]}:00+03:00')
            if tip:
                day = tip.date()
            yield Game(league, f'vtb-{row["GameID"]}', year - 1, day, tip,
                       str(row['TeamAid']), str(row['TeamBid']), row['ShortTeamNameAen'], row['ShortTeamNameBen'],
                       row['CompNameEn'], False, int(row['ScoreA']), int(row['ScoreB']),
                       None if row['Periods'] < 4 else row['Periods'] > 4, None, None,
                       f'{VTB_INFO}/GetCalendar/?format=json&comps={comp}')
    for year in (2026, 2027):
        path = directory / f'VTB-api-{year}.json'
        payload = json.loads(path.read_text())
        assert len(payload['data']) == payload['meta']['count'] < 1000
        for row in payload['data']:
            if row['competition'] != 'vtb' or row['matchStatus'] != 'COMPLETE':
                continue
            tip = instant(row['matchTimeUTC'])
            if not tip or tip >= CUTOFF:
                continue
            home, away = sorted(row['competitors'], key=lambda item: not item['isHomeCompetitor'])
            yield Game(league, f'vtb-{row["matchId"]}', year - 1, tip.date(), tip, str(home['teamId']),
                       str(away['teamId']), home['teamName']['en'], away['teamName']['en'],
                       row['phaseName']['en'], False, int(home['scoreString']), int(away['scoreString']),
                       row['extraPeriodsUsed'], None, None, f'{VTB_API}/{year}/matches?limit=1000')


def source_games(directory):
    games = defaultdict(list)
    rejected = Counter()
    seen = set()
    for group in (euro_games(directory), kaggle_games(directory), bcl_games(directory), vtb_games(directory)):
        for game in group:
            if game.league in ('EuroLeague', 'EuroCup') and game.key.startswith('kaggle-'):
                continue
            if game.key in seen:
                rejected[game.league, 'duplicate source id'] += 1
                continue
            seen.add(game.key)
            if not (30 <= min(game.home_score, game.away_score) and
                    max(game.home_score, game.away_score) <= 200 and game.margin and game.home != game.away):
                rejected[game.league, 'invalid or forfeited final score'] += 1
                continue
            games[game.league].append(game)
    return {name: sorted(rows, key=lambda g: (g.day, g.tip or g.cutoff, g.key))
            for name, rows in games.items()}, rejected


def logistic(value):
    return 1 / (1 + np.exp(-np.clip(value, -36, 36)))


def probability(values):
    return np.clip(np.array(values, dtype=float), 1e-6, 1 - 1e-6)


@dataclass(frozen=True)
class Elo:
    step: int
    home_adv: int
    carry: float
    margin_bonus: bool


BASE_ELO = Elo(20, 65, 0.65, False)
ELO_GRID = [Elo(*args) for args in product((12, 24, 36), (35, 65, 95), (0.50, 0.80), (False, True))]
ELO_CONVERSION = math.log(10) / 400
TOTAL_WINDOWS = (5, 10, 20)
RIDGE_GRID = (1.0, 10.0, 80.0, 400.0)


def append_result(ratings, teams, league_totals, game, steps, home_advs, margin_bonus):
    home = ratings[game.home]
    away = ratings[game.away]
    prior = logistic((home - away + home_advs * (not game.neutral)) * ELO_CONVERSION)
    multiplier = math.log(abs(game.margin) + 1) / 2.5
    delta = steps * (game.home_win - prior) * np.where(margin_bonus, multiplier, 1.0)
    ratings[game.home] = home + delta
    ratings[game.away] = away - delta
    teams[game.home].append((game.home_score, game.away_score, game.day))
    teams[game.away].append((game.away_score, game.home_score, game.day))
    league_totals.append(game.total)


def features(games):
    configs = [BASE_ELO, *ELO_GRID]
    steps = np.array([item.step for item in configs])
    home_advs = np.array([item.home_adv for item in configs])
    carries = np.array([item.carry for item in configs])
    margin_bonus = np.array([item.margin_bonus for item in configs])
    ratings = defaultdict(lambda: np.zeros(len(configs), dtype=float))
    teams = defaultdict(lambda: deque(maxlen=max(TOTAL_WINDOWS)))
    league_totals = deque(maxlen=250)
    pending = []
    latest_available = None
    elo_predictions = []
    total_bases = []
    total_features = defaultdict(list)
    margin_features = defaultdict(list)
    input_cutoffs = []
    team_counts = []
    last_season = None
    for day, batch in groupby(games, key=lambda r: r.day):
        rows = list(batch)
        season = rows[0].season
        assert all(g.season == season for g in rows), day
        forecast = rows[0].cutoff
        ready = sorted((game for game in pending if game.available_at <= forecast),
                       key=lambda game: (game.available_at, game.tip or game.cutoff, game.key))
        for game in ready:
            append_result(ratings, teams, league_totals, game, steps, home_advs, margin_bonus)
            latest_available = game.available_at
        pending = [game for game in pending if game.available_at > forecast]
        if season != last_season:
            for team in ratings:
                ratings[team] = ratings[team] * carries
            # Power ratings carry over. Box-score form starts afresh each season.
            teams.clear()
            last_season = season
        prior_totals = list(league_totals)
        base_total = float(np.mean(prior_totals)) if prior_totals else 160.0
        short_mean = float(np.mean(prior_totals[-50:])) if prior_totals else base_total
        for game in rows:
            assert latest_available is None or latest_available <= game.cutoff
            home_recent = list(teams[game.home])
            away_recent = list(teams[game.away])
            elo = ratings[game.home] - ratings[game.away] + home_advs * (not game.neutral)
            elo_predictions.append(elo)
            total_bases.append(base_total)
            input_cutoffs.append(latest_available)
            team_counts.append((len(home_recent), len(away_recent)))
            for window in TOTAL_WINDOWS:
                home = home_recent[-window:]
                away = away_recent[-window:]
                home_att, home_def = [sum(h[i] - base_total / 2 for h in home) / (len(home) + 4)
                                      for i in (0, 1)]
                away_att, away_def = [sum(a[i] - base_total / 2 for a in away) / (len(away) + 4)
                                      for i in (0, 1)]
                rest_home = min(14, (day - home[-1][2]).days) if home else 7
                rest_away = min(14, (day - away[-1][2]).days) if away else 7
                total_features[window].append([short_mean - base_total, home_att + away_att,
                                               home_def + away_def, min(rest_home, rest_away),
                                               abs(home_att - away_att), abs(home_def - away_def)])
                margin_features[window].append([home_att - away_att, away_def - home_def,
                                                rest_home - rest_away, int(not game.neutral)])
        pending.extend(rows)
    return (np.array(elo_predictions), np.array(total_bases),
            {w: np.array(values) for w, values in total_features.items()},
            {w: np.array(values) for w, values in margin_features.items()}, input_cutoffs, team_counts)


def ridge(x, y, penalty):
    mean = np.mean(x, axis=0)
    scale = np.maximum(np.std(x, axis=0), 1e-4)
    standardized = (x - mean) / scale
    y_mean = np.mean(y)
    coefficients = np.linalg.solve(standardized.T @ standardized + penalty * np.eye(x.shape[1]),
                                   standardized.T @ (y - y_mean)) / scale
    return y_mean - mean @ coefficients, coefficients


def prediction(params, x):
    intercept, coefficients = params
    return intercept + x @ coefficients


def supervised(base, features_by_window, target, early, tune):
    trials = []
    for window, penalty in product(TOTAL_WINDOWS, RIDGE_GRID):
        x = features_by_window[window]
        params = ridge(x[early], (target - base)[early], penalty)
        estimated = base[tune] + prediction(params, x[tune])
        validation_rmse = float(np.sqrt(np.mean((estimated - target[tune]) ** 2)))
        trials.append((validation_rmse, window, penalty))
    tuning_rmse, window, penalty = min(trials)
    fitting = early | tune
    x = features_by_window[window]
    params = ridge(x[fitting], (target - base)[fitting], penalty)
    fitted = base + prediction(params, x)
    # Use residuals on tuning games evaluated by a model that only saw the early split.
    early_fit = ridge(x[early], (target - base)[early], penalty)
    sigma = np.std((target - base)[tune] - prediction(early_fit, x[tune]), ddof=1)
    return fitted, float(sigma), {'window': window, 'penalty': penalty, 'validation_rmse': tuning_rmse,
                                'intercept': float(params[0]), 'coefficients': params[1].tolist()}


def bi_outcomes(prob, actual):
    p = probability(prob)
    actual = np.array(actual)
    bins = np.minimum((p * 5).astype(int), 4)
    ece = sum(np.mean(in_bin) * abs(np.mean(actual[in_bin] - p[in_bin]))
              for i in range(5) if np.any(in_bin := bins == i))
    return {'brier': float(np.mean((p - actual) ** 2)),
            'log_loss': float(np.mean(-actual * np.log(p) - (1 - actual) * np.log1p(-p))),
            'accuracy': float(np.mean((p >= .5) == actual)), 'ece_5': float(ece)}


def points_outcomes(expected, actual):
    error = np.array(expected) - np.array(actual)
    return {'mae': float(np.mean(abs(error))), 'rmse': float(np.sqrt(np.mean(error ** 2))),
            'bias': float(np.mean(error))}


def week_ci(loss_changes, days):
    grouped = defaultdict(list)
    for change, day in zip(loss_changes, days, strict=True):
        grouped[day.isocalendar()[:2]].append(float(change))
    if len(grouped) < 4:
        return None
    sums = np.array([sum(group) for group in grouped.values()])
    counts = np.array([len(group) for group in grouped.values()])
    samples = np.random.default_rng(1119).integers(0, len(grouped), size=(4000, len(grouped)))
    means = sums[samples].sum(axis=1) / counts[samples].sum(axis=1)
    return np.quantile(means, [.025, .975]).tolist()


def league_backtest(games):
    seasons = np.array([g.season for g in games])
    wins = np.array([g.home_win for g in games])
    scores = np.array([g.total for g in games])
    margins = np.array([g.margin for g in games])
    warmup_season = min(seasons)
    early = (seasons > warmup_season) & (seasons < TUNE_START)
    tune = (seasons >= TUNE_START) & (seasons < HOLDOUT_START)
    hold = seasons == HOLDOUT_START
    current = seasons == CURRENT_START
    if early.sum() < 100 or tune.sum() < 60 or hold.sum() < 60:
        raise ValueError(f'{games[0].league}: insufficient history (early {sum(early)}, tune {sum(tune)}, hold {sum(hold)})')
    elo, total_base, totals, margin_features, latest, recent = features(games)
    baseline_win = logistic(elo[:, 0] * ELO_CONVERSION)
    scaling_grid = (.75, 1., 1.25)
    trials = []
    for i, scale in product(range(1, elo.shape[1]), scaling_grid):
        p = logistic(elo[tune, i] * ELO_CONVERSION * scale)
        trials.append((float(np.mean((p - wins[tune]) ** 2)), i, scale))
    validation_brier, elo_index, scale = min(trials)
    candidate_win = logistic(elo[:, elo_index] * ELO_CONVERSION * scale)
    margin_base = elo[:, 0] * 0.045
    margin_inputs = {window: np.c_[matrix, elo[:, elo_index] / 100]
                     for window, matrix in margin_features.items()}
    margin_model, marg_model_sigma, margin_config = supervised(np.zeros(len(games)), margin_inputs,
                                                               margins, early, tune)
    total_model, total_model_sigma, total_config = supervised(total_base, totals, scores, early, tune)
    total_base_sigma = float(np.std((scores - total_base)[tune], ddof=1))
    marg_base_sigma = float(np.std((margins - margin_base)[tune], ddof=1))
    result = {'source_games': len(games), 'first_date': str(games[0].day), 'last_date': str(games[-1].day),
              'warmup_season': int(warmup_season), 'early_train_games': int(sum(early)),
              'tuning_games': int(sum(tune)), 'winner_candidate': {**ELO_GRID[elo_index - 1].__dict__,
              'scale': scale, 'validation_brier': validation_brier}, 'total_candidate': total_config,
              'margin_candidate': margin_config, 'prediction_time': '00:00 UTC on archived game date'}
    for stage, mask in (('holdout', hold), ('current', current)):
        if not np.any(mask):
            continue
        rows = [g for g, include in zip(games, mask, strict=True) if include]
        day_column = [g.day for g in rows]
        y = wins[mask]
        baseline = baseline_win[mask]
        candidate = candidate_win[mask]
        base_total_abs = abs(total_base[mask] - scores[mask])
        model_total_abs = abs(total_model[mask] - scores[mask])
        base_margin_abs = abs(margin_base[mask] - margins[mask])
        model_margin_abs = abs(margin_model[mask] - margins[mask])
        result[stage] = {'games': len(rows), 'first_date': str(rows[0].day), 'last_date': str(rows[-1].day),
                         'overtime_known_or_detected': sum(g.overtime is not None for g in rows),
                         'overtime_games': sum(bool(g.overtime) for g in rows),
                         'winner_baseline': bi_outcomes(baseline, y), 'winner_candidate': bi_outcomes(candidate, y),
                         'winner_delta_brier_95_ci': week_ci((candidate - y) ** 2 - (baseline - y) ** 2, day_column),
                         'totals_baseline': points_outcomes(total_base[mask], scores[mask]),
                         'totals_candidate': points_outcomes(total_model[mask], scores[mask]),
                         'totals_delta_mae_95_ci': week_ci(model_total_abs - base_total_abs, day_column),
                         'margin_baseline': points_outcomes(margin_base[mask], margins[mask]),
                         'margin_candidate': points_outcomes(margin_model[mask], margins[mask]),
                         'margin_delta_mae_95_ci': week_ci(model_margin_abs - base_margin_abs, day_column)}
    predictions = []
    for i, game in enumerate(games):
        if not (hold[i] or current[i]):
            continue
        row = {'league': game.league, 'game_key': game.key, 'season': game.season,
               'split': 'holdout' if hold[i] else 'current', 'date': str(game.day),
               'tip_utc': game.tip.isoformat() if game.tip else '', 'prediction_time_utc': game.cutoff.isoformat(),
               'last_result_available_utc': latest[i].isoformat() if latest[i] else '',
               'home': game.home_name, 'away': game.away_name, 'home_key': game.home, 'away_key': game.away,
               'phase': game.phase, 'neutral_known': int(game.neutral), 'prior_home_games_tracked': recent[i][0],
               'prior_away_games_tracked': recent[i][1], 'final_home': game.home_score, 'final_away': game.away_score,
               'known_overtime': '' if game.overtime is None else int(game.overtime), 'winner_home': game.home_win,
               'final_total': game.total, 'final_margin': game.margin, 'baseline_p_home': baseline_win[i],
               'candidate_p_home': candidate_win[i], 'baseline_total': total_base[i], 'candidate_total': total_model[i],
               'baseline_total_sigma': total_base_sigma, 'candidate_total_sigma': total_model_sigma,
               'baseline_margin': margin_base[i], 'candidate_margin': margin_model[i],
               'baseline_margin_sigma': marg_base_sigma, 'candidate_margin_sigma': marg_model_sigma,
               'source_avg_home_odds': game.odds_home, 'source_avg_away_odds': game.odds_away,
               'score_source': game.source}
        predictions.append(row)
    return predictions, result


def write_csv(path, rows):
    if not rows:
        raise ValueError(f'No rows to write to {path}')
    with path.open('w', encoding='utf8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    games, rejected = source_games(args.cache)
    by_league = {}
    predictions = []
    for league, records in sorted(games.items()):
        rows, by_league[league] = league_backtest(records)
        predictions.extend(rows)
        print(league, len(records), len(rows), flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / 'game_predictions.csv', predictions)
    write_json(args.output / 'prediction_metrics.json', {'leagues': by_league, 'rejected':
        [{'league': league, 'reason': reason, 'games': count} for (league, reason), count in sorted(rejected.items())]})


if __name__ == '__main__':
    main()
