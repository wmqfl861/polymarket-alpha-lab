"""Chronological research and point-in-time forecasts for registered score models."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from functools import cached_property, lru_cache
from hashlib import sha256
from itertools import groupby, product
from pathlib import Path
from statistics import fmean
import csv
import gzip
import io
import json
import math
import random
import re
import time
from urllib.parse import urlencode

from polymarket_alpha_lab.papersim import fotmob, soccer_model

from football_feed import (
    BOOK_AGE, _clock, document_digest, numeric_id, public_budget, record, rows, stamp, team_key, utc_iso,
)


ARCHIVE = (Path(__file__).resolve().parents[4] / "docs" / "paper-sim"
           / "research" / "league-admission-20261119" / "new-soccer")
ARCHIVE_ZONE = timezone(timedelta(hours=1))
HOLDOUT = date(2026, 1, 1)
ARCHIVE_END = date(2026, 11, 19)
FIRST_RESEARCH_SPLIT = date(2026, 7, 1)
LINES = (1.5, 2.5, 3.5, 4.5, 5.5)
MARKETS = ("1x2", *(f"ou{line:.1f}" for line in LINES))
LABELS = {"1x2": ("home", "draw", "away")} | {market: ("over", "under") for market in MARKETS[1:]}
BASELINE_VERSION = "football-20261119-score-baseline-v1"
MODEL_REVISION = "football-score-grid-v1"
SOURCE_VERSION = 1
CALENDAR_AGE = timedelta(minutes=10)
HISTORICAL_CALENDAR_AGE = timedelta(days=7)
FORECAST_AGE = timedelta(minutes=25)
RETROSPECTIVE_INTERVAL = timedelta(days=1)
DAY = timedelta(days=1)
HALF_LIVES = (120, 300, 600)


@dataclass(frozen=True)
class Competition:
    archive_code: str
    fotmob_id: int
    country: str
    tuning_start: date
    group_only: bool = False


COMPETITIONS = {
    "bra": Competition("bra", 268, "BRA", date(2024, 1, 1)),
    "uru1": Competition("uru", 161, "URU", date(2025, 1, 1)),
    "uzb1": Competition("uzb", 540, "UZB", date(2025, 1, 1)),
    "col2": Competition("col", 9125, "COL", date(2025, 1, 1)),
    "aseanc": Competition("asean", 11012, "INT", date(2025, 7, 1), group_only=True),
}
ARCHIVE_CODES = {competition.archive_code: league for league, competition in COMPETITIONS.items()}
PRIORITY_SCOPES = (("bra", "total", 3.5, None), ("col2", "total", 2.5, None),
                   ("uzb1", "match_winner", None, None))
# Equivalent display names verified against the ordered competition registries.
_ALIASES = {
    "bra": (
        ("Athletico-PR", "Athletico Paranaense", "Clube Athletico Paranaense", "Athletico PR"),
        ("Atletico-MG", "Atlético Mineiro", "Clube Atlético Mineiro"),
        ("Flamengo RJ", "Flamengo", "CR Flamengo"),
        ("Botafogo RJ", "Botafogo", "Botafogo FR"),
        ("Bragantino", "RB Bragantino", "Red Bull Bragantino"),
        ("Chapecoense-SC", "Chapecoense"),
        ("Vasco", "Vasco da Gama", "CR Vasco da Gama"),
        ("Vitoria", "EC Vitória"),
    ),
    "uru1": (
        ("CA Cerro", "Cerro"), ("Central Esp.", "Central Español"),
        ("Defensor Sp.", "Defensor Sporting", "Defensor Sporting Club"),
        ("Juventud", "Juventud de las Piedras"), ("Liverpool M.", "Liverpool FC", "Liverpool Montevideo"),
        ("Maldonado", "Deportivo Maldonado"), ("Montevideo City", "Montevideo City Torque", "Torque"),
        ("Penarol", "Club Atletico Peñarol", "Atletico Peñarol"), ("Progreso", "Club Atlético Progreso"),
        ("Racing Montevideo", "Racing", "Racing Club de Montevideo"), ("Wanderers", "Montevideo Wanderers"),
    ),
    "uzb1": (
        ("Andijan", "Andijon"), ("Bunyodkor", "Bunyodkor Tashkent"), ("Din. Samarkand", "Dinamo Samarkand"),
        ("Lok. Tashkent", "Lokomotiv Tashkent"), ("OKMK", "AGMK", "OKMK Olmaliq"),
        ("Pakhtakor", "Pakhtakor Tashkent"), ("Qizilqum", "Qizilqum Zarafshon"),
        ("Sogdiana", "Sogdiana Jizzakh"), ("Termez Surkhon", "Surkhon-2011", "Surkhon Termez"),
        ("Xorazm Urganch", "Xorazm"),
    ),
    "col2": (
        ("Atletico F.C.", "Atlético FC"), ("Boca Juniors", "Boca Juniors de Cali"), ("Cartagena", "Real Cartagena"),
        ("Ind. Valle del Cauca", "Independiente Valle del Cauca"),
        ("Inter Palmira", "Internacional FC de Palmira"), ("Quindio", "Deportes Quindío"),
        ("U. Magdalena", "Unión Magdalena"), ("Huila", "Atlético Huila"),
        ("Cucuta", "Cúcuta Deportivo"),
    ),
    "aseanc": (
        ("Bangkok Utd", "Bangkok United"), ("Borneo", "Borneo Samarinda"), ("Buriram", "Buriram United"),
        ("Ho Chi Minh", "Cong An Ho Chí Minh City"), ("Johor DT", "Johor Darul Ta'zim"),
        ("Lion City", "Lion City Sailors FC"), ("Pathum United", "BG Pathum United"),
        ("Shan Utd", "Shan United"), ("Tampines", "Tampines Rovers FC"),
    ),
}
TEAM_ALIASES = {
    league: {team_key(label): team_key(names[0]) for names in groups for label in names}
    for league, groups in _ALIASES.items()
}
_ARCHIVE_HASHES = {
    "frozen_parameters.json": "b50a4985e509016a4a966df40e52003872992aec09f6d180ab19904a1ccc2326",
    "tuning.csv": "6f728d0fe4cf256ed70f518671deb67068e76fbe1aa56c2f4f4950a9e9bbc181",
    "metrics.json": "2497bd95386514f07f9b3590c1ffc5a2d27f842a8d6dbe146b0e5ce14ab1d175",
    "frozen-inputs/fixtures.jsonl.gz": "977ef86ed89203aaa34efd85a17965627ccd20d3487682a91fa98c629229dceb",
    "frozen-inputs/prices.jsonl.gz": "360443773c681c7d791c79a8dbd4cc878aec85ef224e63819a82eb9073aca4a0",
}


@dataclass(frozen=True)
class Parameters:
    half_life: int
    team_prior: int
    team_power: float
    rho: float
    pace: float

    @classmethod
    def from_row(cls, row):
        return cls(int(row["half_life"]), int(row["team_prior"]), float(row["team_power"]),
                   float(row["rho"]), float(row["pace"]))


BASELINE = Parameters(300, 14, 0.75, -0.08, 1.0)
LEAGUE_ONLY = Parameters(300, 14, 0.0, 0.0, 1.0)


@dataclass(frozen=True)
class Match:
    id: str
    day: date
    home: str
    away: str
    hg: int
    ag: int


@dataclass(frozen=True)
class Model:
    league: str
    holdout_start: date
    inputs_sha256: str
    parameters: dict[str, tuple[Parameters, ...]]

    @cached_property
    def spec(self):
        return {"revision": MODEL_REVISION, "league": self.league, "scope": list(MARKETS),
                "stage": "group" if COMPETITIONS[self.league].group_only else "league",
                "holdout_start": self.holdout_start.isoformat(), "training_sha256": self.inputs_sha256,
                "variant_parameters": {key: [asdict(item) for item in values]
                                       for key, values in self.parameters.items()},
                "baseline_version": BASELINE_VERSION, "baseline_parameters": asdict(BASELINE),
                "team_aliases": TEAM_ALIASES[self.league],
                "forecast_protocol": {"archive_utc_offset": "+01:00", "results_delay_days": 2,
                                      "home_prior": 1.4, "away_prior": 1.1, "league_prior_games": 12}}

    @cached_property
    def version(self):
        return f"{MODEL_REVISION}-{self.league}-{document_digest(self.spec)}"


def _team(league, label):
    name = team_key(label)
    return TEAM_ALIASES[league].get(name, name)


def _market_key(kind, line):
    if kind == "match_winner" and line is None:
        return "1x2"
    if kind == "total" and line in LINES:
        return f"ou{float(line):.1f}"
    return None


def registered_scopes():
    """Yield the original competition and exact regulation market scopes."""
    for league in COMPETITIONS:
        yield league, "match_winner", None, None
        for line in LINES:
            yield league, "total", line, None


def _verified_file(path):
    raw = (ARCHIVE / path).read_bytes()
    if sha256(raw).hexdigest() != _ARCHIVE_HASHES[path]:
        raise ValueError(f"research archive changed: {path}")
    return raw


def _json_lines(path):
    with gzip.GzipFile(fileobj=io.BytesIO(_verified_file(path))) as archive:
        return [json.loads(line) for line in archive]


def _unique_variants(parameters, market):
    variants = {}
    for spec in parameters:
        key = (spec.half_life, spec.team_prior if spec.team_power else 0, spec.team_power,
               spec.rho if market in {"1x2", "ou1.5"} else 0.0, spec.pace)
        variants.setdefault(key, spec)
        if len(variants) == 10:
            return tuple(variants.values())
    raise ValueError("fewer than ten distinct research variants")


@dataclass(frozen=True)
class Archive:
    matches: dict
    models: dict
    prices: dict
    metrics: dict


@lru_cache(maxsize=1)
def _archive():
    frozen = json.loads(_verified_file("frozen_parameters.json"))
    rows_by_league = defaultdict(list)
    for row in _json_lines("frozen-inputs/fixtures.jsonl.gz"):
        league = ARCHIVE_CODES[row["league"]]
        game = Match(row["id"], date.fromisoformat(row["day"]), _team(league, row["home"]),
                     _team(league, row["away"]), row["hg"], row["ag"])
        rows_by_league[league].append(game)
    matches = {league: tuple(sorted(items, key=lambda game: (game.day, game.id)))
               for league, items in rows_by_league.items()}
    if any(len(games) != len({game.id for game in games}) for games in matches.values()):
        raise ValueError("duplicate archived match")
    rankings = defaultdict(list)
    for row in csv.DictReader(io.StringIO(_verified_file("tuning.csv").decode())):
        rankings[ARCHIVE_CODES[row["league"]], row["market"]].append(Parameters.from_row(row))
    models = {}
    for league, competition in COMPETITIONS.items():
        settings = frozen[competition.archive_code]
        official = {market: Parameters.from_row(info["parameters"])
                    for market, info in settings["models"].items()}
        variants = {}
        for market in MARKETS:
            tuned = "1x2" if market == "1x2" else "ou2.5"
            ranking = (official[tuned], *rankings[league, tuned])
            variants[market] = _unique_variants(ranking, market)
        identity = document_digest({"archive": _ARCHIVE_HASHES, "history": settings["history_hash"]})
        models[league] = Model(league, HOLDOUT, identity, variants)
    prices = {(ARCHIVE_CODES[row["league"]], row["id"]): row
              for row in _json_lines("frozen-inputs/prices.jsonl.gz")}
    metrics = {(ARCHIVE_CODES[row["league"]], row["market"], row["cohort"]): row
               for row in json.loads(_verified_file("metrics.json"))}
    return Archive(matches, models, prices, metrics)


def model_version(league):
    return _archive().models[league].version if league in COMPETITIONS else None


def historical_evidence(league, kind, line):
    """Read the archived finalist's original matched bookmaker score."""
    market = _market_key(kind, line)
    if league not in COMPETITIONS or market is None:
        return None
    archive = _archive()
    result = archive.metrics.get((league, market, "matched"))
    if result is None:
        return None
    return {"league": league, "market": market, "original_finalist": dict(result),
            "archive_metrics_sha256": _ARCHIVE_HASHES["metrics.json"],
            "market_basis": "BetExplorer timestamped regulation bookmaker consensus, 0–24 h before kickoff",
            "forecast_basis": "archived single finalist, before registering the ten-variant version"}


class Ratings:
    """The archived score-only team rates. Each call advances to an archive day."""

    def __init__(self, history, half_life, origin):
        self.history = iter(history)
        self.next = next(self.history, None)
        self.origin = origin
        self.half_life = half_life
        self.teams = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        self.home_goals = self.away_goals = self.games = 0.0

    def advance(self, day):
        cutoff = day - DAY
        while self.next is not None and self.next.day < cutoff:
            game = self.next
            weight = math.exp2((game.day - self.origin).days / self.half_life)
            home, away = self.teams[game.home], self.teams[game.away]
            home[0] += weight * game.hg
            home[1] += weight * game.ag
            home[2] += weight
            away[0] += weight * game.ag
            away[1] += weight * game.hg
            away[3] += weight
            self.home_goals += weight * game.hg
            self.away_goals += weight * game.ag
            self.games += weight
            self.next = next(self.history, None)
        self.decay = math.exp2(-(day - self.origin).days / self.half_life)
        weight = self.games * self.decay
        self.base_home = (self.home_goals * self.decay + 12 * 1.4) / (weight + 12)
        self.base_away = (self.away_goals * self.decay + 12 * 1.1) / (weight + 12)

    def state(self, home, away):
        return (self.base_home, self.base_away,
                tuple(value * self.decay for value in self.teams[home]),
                tuple(value * self.decay for value in self.teams[away]))


def _rates(state, spec):
    home, away, home_stats, away_stats = state
    prior = spec.team_prior * (home + away) / 2

    def strength(stats):
        goals, conceded, home_games, away_games = stats
        attack = (goals + prior) / (home_games * home + away_games * away + prior)
        defense = (conceded + prior) / (home_games * away + away_games * home + prior)
        return attack, defense

    home_attack, home_defense = strength(home_stats)
    away_attack, away_defense = strength(away_stats)
    rates = (home * (home_attack * away_defense) ** spec.team_power * spec.pace,
             away * (away_attack * home_defense) ** spec.team_power * spec.pace)
    return tuple(min(4.5, max(0.15, rate)) for rate in rates)


def _poisson(rate, limit):
    values = [math.exp(-rate)]
    for n in range(1, limit + 1):
        values.append(values[-1] * rate / n)
    return values


def _probabilities(home, away, rho, market):
    # Same analytic Dixon–Coles probabilities as the archived scorer.
    delta = rho * home * away * math.exp(-home - away)
    if market == "1x2":
        win = draw = below = 0.0
        for p_home, p_away in zip(_poisson(home, 24), _poisson(away, 24), strict=True):
            win += p_home * below
            draw += p_home * p_away
            below += p_away
        win += delta
        draw -= 2 * delta
        return win, draw, 1.0 - win - draw
    under = sum(_poisson(home + away, int(float(market[2:]))))
    if market == "ou1.5":
        under += delta
    return 1.0 - under, under


def _actual(game, market):
    if market == "1x2":
        return 0 if game.hg > game.ag else 1 if game.hg == game.ag else 2
    return int(game.hg + game.ag < float(market[2:]))


def _candidate_grid(market):
    yield LEAGUE_ONLY
    yield BASELINE
    rhos, paces = ((0.0, -0.08, -0.15), (1.0,)) if market == "1x2" else ((0.0,), (0.9, 1.0, 1.1))
    for values in product(HALF_LIVES, (6, 14, 30), (0.45, 0.75, 1.0), rhos, paces):
        spec = Parameters(*values)
        if spec != BASELINE:
            yield spec


def _states(games, start):
    models = {half: Ratings(games, half, games[0].day) for half in HALF_LIVES}
    for day, grouped in groupby(games, key=lambda item: item.day):
        if day < start:
            continue
        for rating in models.values():
            rating.advance(day)
        for game in grouped:
            yield game, {half: rating.state(game.home, game.away) for half, rating in models.items()}


def _loss(probabilities, target, market):
    components = tuple((p - (i == target)) ** 2 for i, p in enumerate(probabilities))
    return (sum(components) if market == "1x2" else components[0],
            -math.log(max(probabilities[target], 1e-15)))


def _fit(league, games, split):
    cutoff = split - DAY
    training = tuple(game for game in games if game.day < cutoff)
    period = tuple(_states(training, COMPETITIONS[league].tuning_start)) if training else ()
    if not period:
        return None
    ranking = {}
    for market in ("1x2", "ou2.5"):
        scores = []
        for spec in _candidate_grid(market):
            losses = [_loss(_probabilities(*_rates(states[spec.half_life], spec), spec.rho, market),
                            _actual(game, market), market) for game, states in period]
            brier, logloss = (fmean(component) for component in zip(*losses, strict=True))
            scores.append((brier, logloss, spec))
        scores.sort(key=lambda item: item[:2])
        ranking[market] = tuple(spec for _, _, spec in scores)
    parameters = {market: _unique_variants(ranking["1x2" if market == "1x2" else "ou2.5"], market)
                  for market in MARKETS}
    identity = document_digest(_training_records(training))
    return Model(league, split, identity, parameters)


def _training_records(matches):
    return [[game.id, game.day.isoformat(), game.home, game.away, game.hg, game.ag] for game in matches]


def _season(league, day):
    if COMPETITIONS[league].group_only:
        year = day.year if day.month >= 7 else day.year - 1
        return f"{year}/{year + 1}"
    return str(day.year)


def _in_scope(league, match_round):
    if league == "aseanc":
        return isinstance(match_round, str) and re.fullmatch(r"Group Stage - Round [1-9][0-9]*", match_round) is not None
    if league == "col2":
        return numeric_id(match_round) is not None or (
            isinstance(match_round, str)
            and re.fullmatch(r"(Apertura|Clausura)( - Quadrangular)? - Round [1-9][0-9]*", match_round) is not None
        )
    return numeric_id(match_round) is not None


def _calendar(league, season, clock, deadline):
    spec = COMPETITIONS[league]
    raw_digest = None

    def captured(_url, raw):
        nonlocal raw_digest
        raw_digest = sha256(raw).hexdigest()

    payload = fotmob.fetch_fotmob_league(spec.fotmob_id, season, timeout=7, allow_public_fetch=True,
                                        budget=public_budget(deadline, requests=2), response_sink=captured)
    details = record(payload.get("details"))
    fixtures = record(payload.get("fixtures")).get("allMatches")
    if (numeric_id(details.get("id")) != str(spec.fotmob_id) or details.get("selectedSeason") != season
            or details.get("country") != spec.country or raw_digest is None
            or not isinstance(fixtures, list) or not 0 < len(fixtures) <= 700):
        raise ValueError("FotMob competition or season changed")
    seen, matches, results, excluded = set(), [], [], Counter()
    as_of = clock()
    for match in rows(fixtures):
        match_id = numeric_id(match.get("id"))
        if not _in_scope(league, match.get("round")):
            excluded["outside_registered_stage"] += 1
            continue
        status = record(match.get("status"))
        home, away = record(match.get("home")), record(match.get("away"))
        home_id, away_id = numeric_id(home.get("id")), numeric_id(away.get("id"))
        home_team, away_team = _team(league, home.get("name")), _team(league, away.get("name"))
        kick = stamp(status.get("utcTime"))
        if not all((match_id, home_id, away_id, home_team, away_team, kick)) or home_id == away_id or home_team == away_team:
            excluded["invalid_identity_or_time"] += 1
            continue
        if match_id in seen:
            raise ValueError("duplicate FotMob match ID")
        seen.add(match_id)
        if fotmob._is_cancelled(match) or status.get("postponed") is True or status.get("awarded") is True:
            excluded["cancelled_postponed_awarded"] += 1
            continue
        if status.get("started") is status.get("finished") is status.get("cancelled") is False and as_of < kick:
            matches.append([match_id, utc_iso(kick), home_id, away_id, home_team, away_team])
        if kick.astimezone(ARCHIVE_ZONE).date() < ARCHIVE_END:
            continue
        score = re.fullmatch(r"(\d{1,2})\s*[-:–]\s*(\d{1,2})", str(status.get("scoreStr")))
        if (kick < as_of and status.get("finished") is True and status.get("started") is True
                and status.get("cancelled") is False and status.get("awarded") is False
                and record(status.get("reason")).get("short") == "FT" and score):
            results.append([match_id, utc_iso(kick), home_team, away_team, *map(int, score.groups())])
        elif kick < as_of and status.get("finished") is True:
            excluded["no_regulation_result"] += 1
    return {"calendar_at": utc_iso(as_of), "sha256": raw_digest, "matches": matches, "results": results,
            "excluded": dict(excluded), "source": f"https://{fotmob.HOST}/api/data/leagues?" +
            urlencode({"id": spec.fotmob_id, "season": season})}


def _cache(source_cache):
    current = record(record(source_cache).get("research"))
    seasons = record(current.get("seasons")) if current.get("version") == SOURCE_VERSION else {}
    # The persisted cache is shared with established model providers.
    return dict(record(source_cache)), dict(current, version=SOURCE_VERSION, seasons=dict(seasons))


def _read_calendars(keys, research, clock, *, history=False, deadline_seconds=36):
    now = clock()
    today = now.astimezone(ARCHIVE_ZONE).date()
    deadline = time.monotonic() + deadline_seconds
    for league, season in sorted(keys, key=lambda key: (key[1], key[0]), reverse=True):
        key = league + ":" + season
        cached = record(research["seasons"].get(key))
        observed = stamp(cached.get("calendar_at"))
        older = history and season != _season(league, today)
        max_age = HISTORICAL_CALENDAR_AGE if older else CALENDAR_AGE
        if observed is not None and timedelta(0) <= now - observed <= max_age:
            continue
        try:
            research["seasons"][key] = _calendar(league, season, clock, deadline)
        except (ValueError, OSError, fotmob.FotMobError, RuntimeError):
            continue


def _provider_records(league, research, as_of):
    dates, conflicted = {}, set()
    used = []
    prefix = league + ":"
    for key, season in sorted(record(research.get("seasons")).items()):
        source_at = stamp(season.get("calendar_at"))
        if not key.startswith(prefix) or source_at is None or source_at > as_of:
            continue
        used.append((season.get("source"), season.get("sha256")))
        for match_id, timestamp, home, away, hg, ag in season.get("results", ()):
            kick = stamp(timestamp)
            if kick is not None and kick < source_at:
                game = Match("fotmob:" + match_id, kick.astimezone(ARCHIVE_ZONE).date(), home, away, hg, ag)
                identity = (kick, game)
                if match_id in dates and dates[match_id] != identity:
                    conflicted.add(match_id)
                dates[match_id] = identity
    return tuple(game for match_id, (_, game) in dates.items() if match_id not in conflicted), tuple(used)


def _history(league, research, as_of):
    archive = _archive().matches[league]
    provider, used = _provider_records(league, research, as_of)
    cutoff = as_of.astimezone(ARCHIVE_ZONE).date() - DAY
    games = tuple(sorted((game for game in archive + provider if game.day < cutoff),
                         key=lambda game: (game.day, game.id)))
    return games, used


@dataclass(frozen=True)
class ResearchForecast:
    fixture: dict
    model: Model
    model_observed_at: datetime
    evidence: dict
    baseline_version: str = BASELINE_VERSION

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    @property
    def model_version(self):
        return self.model.version

    @property
    def model_evidence(self):
        return self.evidence

    def _quote(self, market, selection, baseline):
        name = _market_key(market.kind, market.line)
        if name is None or getattr(market, "team_side", None) is not None:
            return None
        side = selection.side
        direction = side.removesuffix("_no")
        labels = LABELS[name]
        if direction not in labels or (name != "1x2" and side != direction):
            return None
        index = labels.index(direction)
        if baseline:
            values = (_probabilities(*self.evidence["baseline_rates"], BASELINE.rho, name)[index],)
        else:
            specs = self.model.parameters[name]
            values = tuple(_probabilities(*pair, spec.rho, name)[index]
                           for pair, spec in zip(self.evidence["variant_rates"][name], specs, strict=True))
        if side != direction:
            values = tuple(1.0 - value for value in values)
        return {"model_median": soccer_model.median(values), "qs_variants": values}

    def market_quote(self, market, selection):
        return self._quote(market, selection, baseline=False)

    def baseline_quote(self, market, selection):
        return self._quote(market, selection, baseline=True)

    @cached_property
    def projection(self):
        rates = self.evidence["variant_rates"]["ou2.5"]
        specs = self.model.parameters["ou2.5"]
        outputs = tuple(soccer_model.variant_outputs(*pair, rho=spec.rho, max_goals=24)
                        for pair, spec in zip(rates, specs, strict=True))
        means = tuple(soccer_model.median([pair[side] for pair in rates]) for side in (0, 1))
        meta = {"over_2_5_hits": self.evidence["over_2_5_hits"], "sha256": self.evidence["sha256"],
                "observed_at": utc_iso(self.model_observed_at), "model_version": self.model_version}
        return meta, outputs, means


def _verify_forecast(fixture, now, saved, model, registered_at=None):
    league = fixture.get("league")
    observed = stamp(saved.get("observed_at"))
    kickoff = stamp(fixture.get("kickoff"))
    identity = [fixture.get("fixture_id"), fixture.get("fotmob_home_id"), fixture.get("fotmob_away_id")]
    names = [_team(league, fixture.get(side)) for side in ("home", "away")]
    content = {key: value for key, value in saved.items() if key not in {"sha256", "challengers"}}
    if (observed is None or kickoff is None or not observed <= now < kickoff or now - observed > FORECAST_AGE
            or (registered_at is not None and registered_at > observed)
            or saved.get("version") != model.version or saved.get("identity") != identity
            or saved.get("gamma_teams") != names or fixture.get("state") != "scheduled"
            or saved.get("kickoff") != fixture.get("kickoff") or saved.get("gamma_id") != fixture.get("id")
            or not all(numeric_id(item) for item in identity)
            or fixture.get("fotmob_league_id") != str(COMPETITIONS[league].fotmob_id)
            or saved.get("sha256") != document_digest(content)):
        return None
    rates = record(saved.get("variant_rates"))
    if any(not isinstance(rates.get(market), list) or len(rates[market]) != 10 for market in MARKETS):
        return None
    pairs = [pair for market in MARKETS for pair in rates[market]]
    hits = saved.get("over_2_5_hits")
    if ((hits is not None and (type(hits) is not int or not 0 <= hits <= 10))
            or not all(isinstance(pair, list) and len(pair) == 2 and
                       all(type(rate) in {int, float} and 0.15 <= rate <= 4.5 for rate in pair)
                       for pair in [*pairs, saved.get("baseline_rates")])):
        return None
    return ResearchForecast(fixture, model, observed, content | {"sha256": saved["sha256"]})


def _registered_model(league, version, data, as_of):
    registration = stamp(data.get("registered_at"))
    spec = record(data.get("spec"))
    try:
        split = date.fromisoformat(spec["holdout_start"])
    except (ValueError, TypeError, KeyError):
        return None
    if (registration is None or not split <= registration.astimezone(ARCHIVE_ZONE).date()
            or registration > as_of):
        return None
    model = _restore_model(league, spec, split)
    return (model, registration) if model and model.version == version else None


def from_fixture(fixture, now, *, version=None):
    """Load an observed forecast for the requested immutable version."""
    league = fixture.get("league")
    saved = record(fixture.get("research"))
    if league not in COMPETITIONS or not saved or now.utcoffset() is None:
        return None
    reference = _archive().models[league]
    if version is None or version == reference.version:
        return _verify_forecast(fixture, now, saved, reference)
    challenger = record(record(saved.get("challengers")).get(version))
    registered = _registered_model(league, version, challenger, now)
    if registered is None:
        return None
    model, registration = registered
    return _verify_forecast(fixture, now, challenger, model, registration)


def _registered_league_models(league, research, as_of):
    registry = record(research.get("model_registry"))
    valid = []
    for version, definition in registry.items():
        data = record(definition)
        if record(data.get("spec")).get("league") != league:
            continue
        registered = _registered_model(league, version, data, as_of)
        if registered:
            valid.append(registered)
    return sorted(valid, key=lambda registered: (registered[1], registered[0].version), reverse=True)


def _forecasts(fixture, as_of, research):
    league = fixture.get("league")
    kickoff = stamp(fixture.get("kickoff"))
    if (league not in COMPETITIONS or kickoff is None or as_of >= kickoff
            or fixture.get("state") != "scheduled" or as_of.astimezone(ARCHIVE_ZONE).date() < ARCHIVE_END):
        return ()
    day = kickoff.astimezone(ARCHIVE_ZONE).date()
    calendar = record(research.get("seasons", {}).get(league + ":" + _season(league, day)))
    observed = stamp(calendar.get("calendar_at"))
    if observed is None or not timedelta(0) <= as_of - observed <= CALENDAR_AGE:
        return ()
    names = [{_team(league, fixture.get(side)), _team(league, fixture.get(side + "_alias"))} - {""}
             for side in ("home", "away")]
    eligible = [row for row in calendar["matches"] if len(row) == 6 and stamp(row[1]) == kickoff
                and row[4] in names[0] and row[5] in names[1]]
    if len(eligible) != 1:
        return ()
    match_id, _, home_id, away_id, home, away = eligible[0]
    games, sources = _history(league, research, as_of)
    if not games or not {home, away} <= {team for game in games for team in (game.home, game.away)}:
        return ()
    states = {}
    for half in HALF_LIVES:
        rating = Ratings(games, half, games[0].day)
        rating.advance(day)
        states[half] = rating.state(home, away)
    season = _season(league, day)
    current_season = [game for game in games if _season(league, game.day) == season]
    form = [[game for game in current_season if team in (game.home, game.away)][-5:] for team in (home, away)]
    over_hits = None
    if all(len(team_form) == 5 for team_form in form):
        over_hits = sum(game.hg + game.ag > 2.5 for team_form in form for game in team_form)
    common = {"baseline_version": BASELINE_VERSION, "observed_at": utc_iso(as_of), "gamma_id": fixture["id"],
             "gamma_teams": [_team(league, fixture.get(side)) for side in ("home", "away")],
             "identity": [match_id, home_id, away_id], "kickoff": fixture["kickoff"],
             "calendar_observed_at": utc_iso(observed), "calendar_source": calendar["source"],
             "calendar_sha256": calendar["sha256"], "history_sources": [list(source) for source in sources],
             "archive_history_sha256": _ARCHIVE_HASHES["frozen-inputs/fixtures.jsonl.gz"],
             "history_sha256": document_digest(_training_records(games)), "history_count": len(games),
             "last_result_day": games[-1].day.isoformat(),
             "baseline_rates": list(_rates(states[BASELINE.half_life], BASELINE)),
             "over_2_5_hits": over_hits}

    def evidence(model, registration=None):
        rates = {market: [list(_rates(states[spec.half_life], spec)) for spec in variants]
                 for market, variants in model.parameters.items()}
        saved = common | {"version": model.version, "fit_window_end": model.holdout_start.isoformat(),
                          "variant_rates": rates}
        if registration is not None:
            saved |= {"spec": model.spec, "registered_at": utc_iso(registration)}
        return saved | {"sha256": document_digest(saved)}

    reference = _archive().models[league]
    registered = _registered_league_models(league, research, as_of)
    forecasts = [(reference, evidence(reference))]
    forecasts.extend((model, evidence(model, registration)) for model, registration in registered
                     if model.version != reference.version)
    saved = forecasts[0][1] | {"challengers": {model.version: item for model, item in forecasts[1:]}}
    enriched = fixture | {"fixture_id": match_id, "fotmob_league_id": str(COMPETITIONS[league].fotmob_id),
                          "fotmob_home_id": home_id, "fotmob_away_id": away_id, "research": saved, "watch": True,
                          "reason": "有可核对的赛果与赛前预测"}
    return tuple(ResearchForecast(enriched, model, as_of, item) for model, item in forecasts)


def available_forecasts(fixture, now, source_cache=None):
    """Return all versions with verified schedules and an observed registration."""
    if now.utcoffset() is None or fixture.get("league") not in COMPETITIONS:
        return ()
    reference = from_fixture(fixture, now)
    forecasts = [reference] if reference is not None else []
    for version in record(record(fixture.get("research")).get("challengers")):
        if (result := from_fixture(fixture, now, version=version)) is not None:
            forecasts.append(result)
    if source_cache:
        _, research = _cache(source_cache)
        registered = {model.version for model, _ in _registered_league_models(fixture["league"], research, now)}
        if not forecasts or registered - {result.model_version for result in forecasts}:
            return _forecasts(fixture, now, research) or tuple(forecasts)
    return tuple(forecasts)


def forecast(fixture, as_of, source_cache=None):
    """Return the pre-match model from a persisted fit or a fresh calendar."""
    forecasts = available_forecasts(fixture, as_of, source_cache)
    return forecasts[0] if forecasts else None


def refresh(fixtures, now, source_cache):
    """Refresh each required league calendar once and enrich matching fixtures."""
    clock = now if callable(now) else _clock(now.astimezone(UTC))
    cache, research = _cache(source_cache)
    keys = {(fixture["league"], _season(fixture["league"], kickoff.astimezone(ARCHIVE_ZONE).date()))
            for fixture in fixtures if fixture.get("league") in COMPETITIONS
            and fixture.get("state") == "scheduled" and (kickoff := stamp(fixture.get("kickoff"))) is not None}
    if not keys:
        return fixtures, cache
    _read_calendars(keys, research, clock)
    observed = clock()
    updated = []
    for fixture in fixtures:
        found = _forecasts(fixture, observed, research)
        updated.append(found[0].fixture if found else fixture)
    return updated, cache | {"research": research}


def _bookmaker_consensus(recorded, market, day):
    """Match complete, timestamped bookmaker legs for this fixture and line."""
    kickoff = stamp(recorded.get("kickoff"))
    if kickoff is None or kickoff.astimezone(ARCHIVE_ZONE).date() != day:
        return None
    offered = record(recorded.get("prices")).get(market)
    quotes = []
    for legs in record(offered).values():
        if not isinstance(legs, list) or len(legs) != len(LABELS[market]):
            continue
        reciprocal = []
        for leg in legs:
            if not isinstance(leg, list) or len(leg) != 2:
                break
            odds, time_text = leg
            observed = stamp(time_text)
            if (observed is None or not timedelta(0) < kickoff - observed <= DAY
                    or type(odds) not in (int, float) or not 1 < odds < math.inf):
                break
            reciprocal.append(1 / odds)
        if len(reciprocal) == len(legs):
            overround = sum(reciprocal)
            quotes.append(tuple(leg / overround for leg in reciprocal))
    if not quotes:
        return None
    return tuple(fmean(probabilities) for probabilities in zip(*quotes, strict=True))


def _forward_market_rows(connection, as_of, *, leagues=COMPETITIONS):
    if connection is None:
        return ()
    exists = connection.execute(
        """SELECT to_regclass('football_notify.football_forward_result') IS NOT NULL AS ready"""
    ).fetchone()
    if not exists["ready"]:
        return ()
    return connection.execute(
        """SELECT c.fixture_key, c.fixture_revision, c.fixture_id, c.league, c.market_id,
                  c.market_kind, c.line, c.side, c.kickoff, o.observed_at, o.book_observed_at,
                  o.market_price, r.home_goals, r.away_goals, r.won
           FROM football_notify.football_forward_contract c
           JOIN football_notify.football_forward_observation o
               USING (fixture_key, fixture_revision, market_id, outcome)
           JOIN football_notify.football_forward_result r
               USING (fixture_key, fixture_revision, market_id, outcome, model_version)
           WHERE c.league = ANY(%s) AND c.team_side IS NULL
             AND c.rule = 'regulation_90_plus_stoppage'
             AND c.kickoff = o.kickoff AND o.kickoff = r.kickoff
             AND o.observed_at >= c.kickoff - INTERVAL '30 minutes'
             AND o.observed_at < c.kickoff - INTERVAL '20 minutes'
             AND r.observed_at <= %s AND c.kickoff >= %s AND r.state = 'resolved'
             AND o.market_price_basis = 'clob_midpoint' AND o.market_price IS NOT NULL
             AND NOT o.reason_codes && ARRAY['direct_contract_unverified', 'independent_fixture_unverified']""",
        (list(leagues), as_of, datetime.combine(ARCHIVE_END, datetime.min.time(), ARCHIVE_ZONE)),
    ).fetchall()


def _forward_consensus(forward_rows):
    """Join actual simultaneous contract midpoints and regulation scores."""
    groups = defaultdict(list)
    for row in forward_rows:
        market = _market_key(row["market_kind"], row["line"])
        at = row["book_observed_at"]
        if (market is None or row["side"] not in LABELS[market] or not isinstance(at, datetime)
                or not timedelta(0) <= row["observed_at"] - at <= BOOK_AGE):
            continue
        match_scope = (row["league"], "fotmob:" + row["fixture_id"], market,
                       row["fixture_key"], row["fixture_revision"], row["kickoff"], row["observed_at"])
        groups[match_scope].append(row)
    found = defaultdict(list)
    for scope, offers in groups.items():
        market = scope[2]
        labels = LABELS[market]
        first = offers[0]
        goals = (first["home_goals"], first["away_goals"])
        game = Match(scope[1], scope[5].astimezone(ARCHIVE_ZONE).date(), "", "", *goals)
        exact = {}
        for offer in offers:
            index = labels.index(offer["side"])
            if ((offer["home_goals"], offer["away_goals"]) != goals
                    or offer["won"] != (_actual(game, market) == index)):
                break
            values = (offer["market_id"], offer["market_price"], offer["book_observed_at"])
            if offer["side"] in exact and exact[offer["side"]] != values:
                break
            exact[offer["side"]] = values
        else:
            if set(exact) != set(labels):
                continue
            ids, prices, times = zip(*(exact[label] for label in labels), strict=True)
            if (max(times) - min(times) > BOOK_AGE
                    or len(set(ids)) != (3 if market == "1x2" else 1)):
                continue
            overround = sum(prices)
            comparison = (game.day, goals, tuple(price / overround for price in prices))
            found[scope[:3]].append((scope[3:6], scope[6], comparison))
    return {scope: min(snapshots, key=lambda offer: offer[1])[2]
            for scope, snapshots in found.items() if len({offer[0] for offer in snapshots}) == 1}


def _ensemble(states, specs, market):
    matrix = [_probabilities(*_rates(states[spec.half_life], spec), spec.rho, market) for spec in specs]
    return tuple(soccer_model.median(component) for component in zip(*matrix, strict=True))


def _binary_scores(probability, occurs):
    likelihood = probability if occurs else 1 - probability
    return (probability - occurs) ** 2, -math.log(max(likelihood, 1e-15))


@dataclass(frozen=True)
class ScoredMatch:
    day: date
    actual: int
    forecasts: dict


def _point_scores(sample, market, label_index):
    if label_index is None:
        # A 1X2 vector of per-contract medians needs normalizing for a joint log score.
        # Binary direction scores below use the same unadjusted medians as the live gate.
        return {name: _loss([p / sum(probs) for p in probs], sample.actual, market)
                for name, probs in sample.forecasts.items()}
    return {name: _binary_scores(probs[label_index], sample.actual == label_index)
            for name, probs in sample.forecasts.items()}


def _paired_delta(scored, left, right):
    by_week = defaultdict(list)
    for day, row in scored:
        gains = tuple(b - a for a, b in zip(row[left], row[right], strict=True))
        by_week[day.isocalendar()[:2]].append(gains)
    weeks = [(sum(brier for brier, _ in values), sum(logloss for _, logloss in values), len(values))
             for values in by_week.values()]
    delta = [fmean(row[right][i] - row[left][i] for _, row in scored) for i in range(2)]
    if len(weeks) < 2:
        return {"brier": delta[0], "log_loss": delta[1], "brier_ci95": None, "log_loss_ci95": None}
    random_source = random.Random(20261119)
    expected = [[], []]
    for _ in range(2000):
        sample = random_source.choices(weeks, k=len(weeks))
        brier, logloss, count = map(sum, zip(*sample, strict=True))
        expected[0].append(brier / count)
        expected[1].append(logloss / count)
    intervals = []
    for values in expected:
        values.sort()
        intervals.append([values[50], values[1949]])
    return {"brier": delta[0], "log_loss": delta[1], "brier_ci95": intervals[0], "log_loss_ci95": intervals[1]}


def _summary(samples, market, label_index=None, *, interval=False):
    if not samples:
        return {"matches": 0}
    scores = [(sample.day, _point_scores(sample, market, label_index)) for sample in samples]
    names = scores[0][1].keys()
    result = {"matches": len(samples), "first_date": samples[0].day.isoformat(), "last_date": samples[-1].day.isoformat(),
              "models": {name: {"brier": fmean(losses[name][0] for _, losses in scores),
                                "log_loss": fmean(losses[name][1] for _, losses in scores)}
                         for name in names}}
    if interval:
        paired = [("candidate", "baseline")]
        if "reference" in names:
            paired.append(("candidate", "reference"))
        if "market" in names:
            paired.append(("candidate", "market"))
        result["gain"] = {f"{candidate}_vs_{against}": _paired_delta(scores, candidate, against)
                          for candidate, against in paired}
    if label_index is not None:
        observed = sum(sample.actual == label_index for sample in samples)
        result |= {"outcome_count": observed, "observed_frequency": observed / len(samples),
                   "mean_probability": {name: fmean(sample.forecasts[name][label_index] for sample in samples)
                                        for name in names}}
    return result


def _score_comparison(league, games, candidate, reference, archive, forward_market):
    scored, prospective = ({market: [] for market in MARKETS} for _ in range(2))
    for game, states in _states(games, candidate.holdout_start):
        baseline = _rates(states[BASELINE.half_life], BASELINE)
        prices = archive.prices.get((league, game.id), {})
        for market in MARKETS:
            guesses = {"baseline": _probabilities(*baseline, BASELINE.rho, market),
                       "candidate": _ensemble(states, candidate.parameters[market], market)}
            if reference:
                guesses["reference"] = _ensemble(states, reference.parameters[market], market)
            market_probs = _bookmaker_consensus(prices, market, game.day)
            guesses["market"] = market_probs
            actual = _actual(game, market)
            scored[market].append(ScoredMatch(game.day, actual, guesses))
            live = forward_market.get((league, game.id, market))
            if live and live[:2] == (game.day, (game.hg, game.ag)):
                prospective[market].append(ScoredMatch(game.day, actual, guesses | {"market": live[2]}))
    results = {}
    for market, samples in scored.items():
        entire = [ScoredMatch(game.day, game.actual, {k: v for k, v in game.forecasts.items() if k != "market"})
                  for game in samples]
        matched = [game for game in samples if game.forecasts["market"] is not None]
        results[market] = {"all_results": _summary(entire, market),
                           "prematch_market": _summary(matched, market, interval=True),
                           "polymarket_prematch": _summary(prospective[market], market, interval=True),
                           "outcomes": {label: {"all_results": _summary(entire, market, index),
                                                "prematch_market": _summary(matched, market, index),
                                                "polymarket_prematch": _summary(prospective[market], market, index)}
                                        for index, label in enumerate(LABELS[market])},
                           "unmatched_market": len(samples) - len(matched)}
    return results


def _restore_model(league, saved, split):
    spec = record(saved)
    if (spec.get("league") != league or spec.get("holdout_start") != split.isoformat()
            or spec.get("revision") != MODEL_REVISION):
        return None
    settings = record(spec.get("variant_parameters"))
    try:
        variants = {market: _unique_variants(map(Parameters.from_row, settings[market]), market)
                    for market in MARKETS}
        model = Model(league, split, spec["training_sha256"], variants)
    except (ValueError, TypeError, KeyError):
        return None
    return model if model.spec == spec else None


def _research_split(day):
    return max(FIRST_RESEARCH_SPLIT, date(day.year, 7 if day.month >= 7 else 1, 1))


def _history_keys(day):
    keys = set()
    for league in COMPETITIONS:
        start = int(_season(league, ARCHIVE_END).split("/")[0])
        end = int(_season(league, day).split("/")[0])
        keys.update((league, _season(league, date(year, 7, 1))) for year in range(start, end + 1))
    return keys


def optimize(now, source_cache=None, *, use_network=True, force=False, connection=None):
    """Fit one challenger per time split and re-score the growing chronological holdout."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("retrospective requires an aware timestamp")
    cache, research = _cache(source_cache)
    previous = record(research.get("retrospective"))
    last = stamp(previous.get("generated_at"))
    if (not force and last and isinstance(research.get("model_registry"), dict)
            and timedelta(0) <= now - last < RETROSPECTIVE_INTERVAL):
        return previous, cache
    archive = _archive()
    clock = _clock(now.astimezone(UTC))
    day = now.astimezone(ARCHIVE_ZONE).date()
    if use_network:
        _read_calendars(_history_keys(day), research, clock, history=True)
    observed = clock()
    forward_market = _forward_consensus(_forward_market_rows(connection, observed))
    split = _research_split(day)
    previous_leagues = record(previous.get("leagues"))
    registry = dict(record(research.get("model_registry")))
    leagues = {}
    for league, spec in COMPETITIONS.items():
        games, source = _history(league, research, observed)
        if not games:
            continue
        production = archive.models[league]
        before = record(previous_leagues.get(league))
        challenger = _restore_model(league, before.get("challenger_spec"), split) or _fit(league, games, split)
        if challenger:
            registry.setdefault(challenger.version, {"registered_at": utc_iso(clock()), "spec": challenger.spec})
        leagues[league] = {
            "active_version": production.version, "active_spec": production.spec,
            "challenger_version": challenger.version if challenger else None,
            "challenger_spec": challenger.spec if challenger else None,
            "prior_tuning_start": spec.tuning_start.isoformat(), "latest_result_date": games[-1].day.isoformat(),
            "result_sources": [list(item) for item in source], "history_sha256": document_digest(_training_records(games)),
            "active": _score_comparison(league, games, production, None, archive, forward_market),
            "challenger": (_score_comparison(league, games, challenger, production, archive, forward_market)
                           if challenger else {}),
        }
    report = {"generated_at": utc_iso(clock()), "archive_end_exclusive": ARCHIVE_END.isoformat(),
              "archive_sha256": _ARCHIVE_HASHES, "baseline_version": BASELINE_VERSION,
              "priority_scopes": [list(scope) for scope in PRIORITY_SCOPES],
              "market_basis": "BetExplorer complete same-match regulation quotes strictly 0–24 h before kickoff",
              "polymarket_basis": "Matched settled contracts with complete synchronized pre-match CLOB midpoint coverage",
              "score_basis": "Chronological score replay with two archive days of result delay and frozen parameters per split",
              "comparison_limits": [
                  "Initial challenger reuses part of a previously disclosed research period.",
                  "New FotMob results may have no matched archival bookmaker prices.",
                  "A joint 1X2 score uses normalized medians, individual binary scopes use the gate probabilities.",
                  "Bookmaker prices and retrospective replay provide no executable Polymarket or prospective entry evidence.",
                  "Week-block intervals are pointwise and do not adjust for multiple leagues or markets.",
              ],
              "leagues": leagues}
    report["sha256"] = document_digest(report)
    research |= {"retrospective": report, "model_registry": registry}
    return report, cache | {"research": research}
