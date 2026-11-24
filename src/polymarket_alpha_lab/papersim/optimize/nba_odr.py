"""Pinned normalized NBA regular-season inputs for the FX-41 v2 schedule."""

from __future__ import annotations

import gzip
import json
import math
import re
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from hashlib import sha256
from types import MappingProxyType
from typing import Generic, TypeVar
from zoneinfo import ZoneInfo

from polymarket_alpha_lab.papersim import espn_nba
from polymarket_alpha_lab.papersim.optimize.families.nba import NbaFamilyFixture

NBA_ODR_AS_OF = datetime(2026, 11, 19, 2, tzinfo=UTC)
NBA_ODR_SOURCE_CALENDAR = "America/New_York"
NBA_ODR_SOURCE_TIMEZONE = ZoneInfo(NBA_ODR_SOURCE_CALENDAR)
NBA_ODR_ARCHIVE_ROOT = "docs/paper-sim/research/league-admission-20261119/nba"
NBA_ODR_SOURCE_KEY = "espn-nba-basketball-reference"
NBA_ODR_HOLDOUT_START = date(2026, 3, 5)
NBA_ODR_HOLDOUT_END = date(2026, 4, 12)
NBA_ODR_TOTALS_LINE = 225.5
NBA_ODR_LINES = MappingProxyType({
    "moneyline": (None, ("away", "home")),
    "totals": (NBA_ODR_TOTALS_LINE, ("under", "over")),
})

_GAMES_PER_TEAM = 82
_GAMES_PER_SEASON = _GAMES_PER_TEAM * espn_nba.TEAM_COUNT // 2
_EVENT_ID = re.compile(r"[0-9]+", re.ASCII)
_ARCHIVE_FIELDS = {"season", "retrieved_at", "sources", "games"}
_EVENT_FIELDS = {"date", "event_id", "started", "home", "away", "neutral"}
_GAME_FIELDS = _EVENT_FIELDS | {
    "home_pts", "away_pts", "home_poss", "away_poss", "home_poss_inputs", "away_poss_inputs",
}
_POSSESSION_FIELDS = {"fga", "oreb", "tov", "fta"}
_SOURCE_URLS = MappingProxyType({
    "schedule": (
        f"https://{espn_nba.HOST}{espn_nba.API_PATH}"
        "/teams/{ESPN team id}/schedule?season={year}&seasontype=2"
    ),
    "box_inputs": (
        "https://www.basketball-reference.com/teams/"
        "{Basketball Reference abbreviation}/{year}/gamelog/"
    ),
})


@dataclass(frozen=True, slots=True)
class NBAArchivePin:
    season: str
    gzip_byte_length: int
    gzip_sha256: str
    decoded_byte_length: int
    decoded_sha256: str
    retrieved_at: datetime
    first_day: date
    last_day: date
    day_count: int
    training_only: tuple[tuple[date, int], ...]

    @property
    def relpath(self) -> str:
        return f"{NBA_ODR_ARCHIVE_ROOT}/games-{self.season}.json.gz"


NBA_ODR_ARCHIVE_PINS = MappingProxyType({
    "2024-25": NBAArchivePin(
        season="2024-25",
        gzip_byte_length=39_916,
        gzip_sha256="a71489ae2ee8a3afddd4c8520ce4e93b3005c399590cae83791e493f215a902d",
        decoded_byte_length=424_531,
        decoded_sha256="8e076559f766cfb1cf03376e143c96c504eda84703e6a1d44f2b3bfcb9090d0d",
        retrieved_at=datetime(2026, 11, 19, 1, 54, 32, 830906, tzinfo=UTC),
        first_day=date(2024, 10, 22),
        last_day=date(2025, 4, 13),
        day_count=163,
        training_only=((date(2024, 10, 22), 2), (date(2024, 10, 23), 10), (date(2024, 10, 24), 4)),
    ),
    "2025-26": NBAArchivePin(
        season="2025-26",
        gzip_byte_length=39_985,
        gzip_sha256="893f998ea4cd6ef45b20c575f96675d325ca4b9e1a185f3299d52a125a8eb136",
        decoded_byte_length=424_994,
        decoded_sha256="f4930fd430a3c2ca2b45874b7828e15dce992702c8ea5c7c112bcc5fdc2166f8",
        retrieved_at=datetime(2026, 11, 19, 1, 50, 48, 289155, tzinfo=UTC),
        first_day=date(2025, 10, 21),
        last_day=date(2026, 4, 12),
        day_count=164,
        training_only=((date(2025, 10, 21), 2), (date(2025, 10, 22), 12), (date(2025, 10, 23), 2)),
    ),
})


@dataclass(frozen=True, slots=True)
class NBAIndependentESPN:
    """Coverage from the owner-approved filename inventory for one season."""

    inventory_sha256: str = "f3bf0c46a3596de85353327b8fead7ceed274f7c00ee8aa88648eb9586c5dd48"
    inventory_byte_length: int = 1_465
    inventory_observed_at: datetime = datetime(2026, 11, 21, 17, 9, 48, 289725, tzinfo=UTC)
    cache_roots: tuple[str, ...] = ("espn_default", "espn_prior_backfill")
    available_team_schedules: int = 0
    required_team_schedules: int = espn_nba.TEAM_COUNT
    available_game_summaries: int = 0
    required_game_summaries: int = _GAMES_PER_SEASON
    reason: str = "registered_raw_schedules_and_summaries_unavailable"
    teams_equal: None = None
    injuries_equal: None = None


NBA_ODR_INDEPENDENT_ESPN = NBAIndependentESPN()


def source_day_start_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, NBA_ODR_SOURCE_TIMEZONE).astimezone(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class NBAArchiveEvent:
    """Verified event identity, source clocks, teams, and schedule context."""

    season: str
    event_id: str
    season_source_sha256: str
    source_day: date
    label_day: date
    started: datetime
    home_team: str
    away_team: str
    neutral: bool
    rest_home: int | None
    rest_away: int | None
    available_at: datetime | None = None
    event_key: str = field(init=False)

    def __post_init__(self) -> None:
        if self.label_day < self.source_day:
            raise ValueError("NBA label day precedes the source day")
        if self.available_at is not None and (
            self.available_at.utcoffset() is None
            or self.available_at < self.started
            or self.effective_label_day < self.label_day
        ):
            raise ValueError("NBA observed label availability contradicts the source clocks")
        encoded = json.dumps(self.source_identity, separators=(",", ":")).encode("utf-8")
        object.__setattr__(self, "event_key", sha256(encoded).hexdigest())

    @property
    def source_identity(self) -> tuple[str, str, str]:
        return self.season, self.event_id, self.season_source_sha256

    @property
    def effective_label_day(self) -> date:
        if self.available_at is None:
            return self.label_day
        return self.available_at.astimezone(NBA_ODR_SOURCE_TIMEZONE).date()

    def prediction_fixture(self) -> NbaFamilyFixture:
        return NbaFamilyFixture(
            home=self.home_team, away=self.away_team, rest_home=self.rest_home, rest_away=self.rest_away,
            b2b_home=self.rest_home == 1, b2b_away=self.rest_away == 1,
            before_date=self.source_day.isoformat(),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class NBAArchiveGame(NBAArchiveEvent):
    home_points: int
    away_points: int
    home_possessions: float
    away_possessions: float

    @property
    def margin(self) -> int:
        return self.home_points - self.away_points

    @property
    def total(self) -> int:
        return self.home_points + self.away_points

    def settled_side(self, target: str) -> str:
        if target == "moneyline":
            return "home" if self.margin > 0 else "away"
        if target == "totals":
            return "over" if self.total > NBA_ODR_TOTALS_LINE else "under"
        raise ValueError("unknown NBA archive contract target")

    def _history_row(self, *, home: bool) -> dict[str, object]:
        if home:
            opponent, points, conceded = self.away_team, self.home_points, self.away_points
            possessions, rest = self.home_possessions, self.rest_home
        else:
            opponent, points, conceded = self.home_team, self.away_points, self.home_points
            possessions, rest = self.away_possessions, self.rest_away
        return {
            "event_key": self.event_key, "date": self.source_day.isoformat(), "home": home,
            "opponent": opponent, "pts": points, "opp_pts": conceded, "poss": possessions,
            "ortg": 100.0 * points / possessions, "drtg": 100.0 * conceded / possessions,
            "rest_days": rest, "b2b": rest == 1,
        }


_Event = TypeVar("_Event", bound=NBAArchiveEvent)


@dataclass(frozen=True, slots=True)
class _NBASeasonEvents(Generic[_Event]):
    pin: NBAArchivePin
    retrieved_at: datetime
    games: tuple[_Event, ...]
    independent_espn: NBAIndependentESPN | None = None
    games_by_day: Mapping[date, tuple[_Event, ...]] = field(init=False, repr=False)
    games_by_key: Mapping[str, _Event] = field(init=False, repr=False)

    @property
    def season(self) -> str:
        return self.pin.season

    def __post_init__(self) -> None:
        by_day = defaultdict(list)
        by_key = {}
        for game in self.games:
            if (game.season, game.season_source_sha256) != (self.season, self.pin.gzip_sha256):
                raise ValueError("NBA game identifies a different seasonal archive")
            if game.event_key in by_key:
                raise ValueError(f"NBA {self.season} has a duplicate event identity")
            by_key[game.event_key] = game
            by_day[game.source_day].append(game)
        object.__setattr__(self, "games_by_key", MappingProxyType(by_key))
        object.__setattr__(self, "games_by_day", MappingProxyType({
            day: tuple(games) for day, games in sorted(by_day.items())
        }))

    @property
    def holdout_days(self) -> tuple[date, ...]:
        return tuple(day for day in self.games_by_day if NBA_ODR_HOLDOUT_START <= day <= NBA_ODR_HOLDOUT_END)

    @property
    def holdout_event_keys(self) -> tuple[str, ...]:
        return tuple(game.event_key for day in self.holdout_days for game in self.games_by_day[day])


@dataclass(frozen=True, slots=True)
class NBASeasonMetadata(_NBASeasonEvents[NBAArchiveEvent]):
    """Value-free archive projection for registration and preview."""


@dataclass(frozen=True, slots=True)
class NBASeasonArchive(_NBASeasonEvents[NBAArchiveGame]):
    """Scoring archive with validated full-game results and own-team box inputs."""

    def history_at(self, day: date, train_keys: Iterable[str]) -> dict[str, object]:
        """Adapt sealed earlier whole days of this season to the family history shape."""
        if day not in self.games_by_day:
            raise ValueError("NBA replay requires a source date from its own season")
        selected = {}
        counts: Counter[date] = Counter()
        for key in train_keys:
            game = self.games_by_key.get(key)
            if game is None or game.source_day >= day or game.effective_label_day >= day:
                raise ValueError("NBA replay requires strictly earlier available games from its own season")
            if key in selected:
                raise ValueError("NBA replay duplicates a training event")
            selected[key] = game
            counts[game.source_day] += 1
        if any(count != len(self.games_by_day[source_day]) for source_day, count in counts.items()):
            raise ValueError("NBA replay requires whole training source days")

        rows = defaultdict(list)
        for game in sorted(selected.values(), key=lambda item: (item.source_day, item.event_id)):
            rows[game.home_team].append(game._history_row(home=True))
            rows[game.away_team].append(game._history_row(home=False))
        return {
            "generated_at": self.retrieved_at.isoformat(), "source": NBA_ODR_SOURCE_KEY,
            "season": self.season, "injuries": {},
            "teams": {
                team: {"display": espn_nba.NBA_TEAMS[team][1], "games": tuple(rows[team])}
                for team in sorted(rows)
            },
        }


def load_nba_odr_metadata(content_by_season: Mapping[str, bytes]) -> Mapping[str, NBASeasonMetadata]:
    """Verify the fixed source identities and collect score-free season metadata."""
    return _load_two_seasons(content_by_season, _read_season_metadata)


def load_nba_odr_archives(content_by_season: Mapping[str, bytes]) -> Mapping[str, NBASeasonArchive]:
    """Load the two registered archive payloads using only the fixed production pins."""
    return _load_two_seasons(content_by_season, _read_season_archive)


_Season = TypeVar("_Season", bound=_NBASeasonEvents)


def _load_two_seasons(
    content_by_season: Mapping[str, bytes],
    read_season: Callable[[bytes, NBAArchivePin], _Season],
) -> Mapping[str, _Season]:
    if content_by_season.keys() != NBA_ODR_ARCHIVE_PINS.keys():
        raise ValueError("NBA v2 requires exactly the two approved seasonal archives")
    seasons = {}
    event_ids: set[str] = set()
    source_days: set[date] = set()
    for season, pin in NBA_ODR_ARCHIVE_PINS.items():
        archive = read_season(content_by_season[season], pin)
        _verify_regular_season(archive)
        ids = {game.event_id for game in archive.games}
        if event_ids & ids or source_days & archive.games_by_day.keys():
            raise ValueError("NBA season archives have overlapping source dates or event IDs")
        event_ids.update(ids)
        source_days.update(archive.games_by_day)
        seasons[season] = replace(archive, independent_espn=NBA_ODR_INDEPENDENT_ESPN)
    return MappingProxyType(seasons)


def _verify_regular_season(archive: _NBASeasonEvents) -> None:
    pin = archive.pin
    counts = Counter(team for game in archive.games for team in (game.home_team, game.away_team))
    expected_counts = dict.fromkeys(espn_nba.NBA_TEAMS, _GAMES_PER_TEAM)
    if len(archive.games) != _GAMES_PER_SEASON or dict(counts) != expected_counts:
        raise ValueError(f"NBA {pin.season} archive requires a full 30-team regular season")
    days = tuple(archive.games_by_day)
    if (len(days), days[0], days[-1]) != (pin.day_count, pin.first_day, pin.last_day):
        raise ValueError(f"NBA {pin.season} archive does not match its pinned source calendar")
    opening = tuple((day, len(archive.games_by_day[day])) for day in days[:len(pin.training_only)])
    if opening != pin.training_only:
        raise ValueError(f"NBA {pin.season} archive does not match its training-only opening days")
    teams = {
        team for day, _ in opening for game in archive.games_by_day[day]
        for team in (game.home_team, game.away_team)
    }
    if teams != espn_nba.NBA_TEAMS.keys():
        raise ValueError(f"NBA {pin.season} opening days do not cover all official teams")


def _strict_object(value: object, fields: set[str], what: str) -> dict:
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError(f"NBA archive {what} has unexpected fields")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("NBA archive JSON contains a duplicate object key")
        result[key] = value
    return result


def _metadata_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = _unique_object(pairs)
    if "event_id" not in result:
        return result
    _strict_object(result, _GAME_FIELDS, "game")
    _strict_object(result["home_poss_inputs"], _POSSESSION_FIELDS, "possession inputs")
    _strict_object(result["away_poss_inputs"], _POSSESSION_FIELDS, "possession inputs")
    return {key: result[key] for key in _EVENT_FIELDS}


def _utc_timestamp(value: object, what: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"NBA archive {what} requires an aware timestamp")
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is not None:
            return stamp.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"NBA archive {what} requires an aware timestamp") from exc
    raise ValueError(f"NBA archive {what} requires an aware timestamp")


def _source_date(value: object) -> date:
    if isinstance(value, str):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            if day.isoformat() == value:
                return day
    raise ValueError("NBA archive game requires an ISO source date")


def _read_season_rows(
    content: bytes, pin: NBAArchivePin, *, metadata: bool,
) -> tuple[datetime, list[tuple[dict, dict]]]:
    if not isinstance(content, bytes) or (len(content), sha256(content).hexdigest()) != (
        pin.gzip_byte_length, pin.gzip_sha256,
    ):
        raise ValueError(f"NBA {pin.season} archive gzip length or SHA-256 mismatch")
    try:
        decoded = gzip.decompress(content)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError(f"NBA {pin.season} archive gzip decoding failed") from exc
    if (len(decoded), sha256(decoded).hexdigest()) != (pin.decoded_byte_length, pin.decoded_sha256):
        raise ValueError(f"NBA {pin.season} archive decoded length or SHA-256 mismatch")
    try:
        data = json.loads(
            decoded.decode("utf-8"), object_pairs_hook=_metadata_object if metadata else _unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"NBA {pin.season} archive JSON decoding failed") from exc
    data = _strict_object(data, _ARCHIVE_FIELDS, "root")
    if data["season"] != pin.season or data["sources"] != _SOURCE_URLS:
        raise ValueError(f"NBA {pin.season} archive has a different season or source URLs")
    retrieved = _utc_timestamp(data["retrieved_at"], "retrieved_at")
    if retrieved != pin.retrieved_at or retrieved >= NBA_ODR_AS_OF:
        raise ValueError(f"NBA {pin.season} archive retrieval clock conflicts with its pin or cutoff")
    if not isinstance(data["games"], list) or not data["games"]:
        raise ValueError(f"NBA {pin.season} archive requires a nonempty game list")

    fields = _EVENT_FIELDS if metadata else _GAME_FIELDS
    rows = [_strict_object(row, fields, "game") for row in data["games"]]
    dated = sorted(((_source_date(row["date"]), row) for row in rows), key=lambda item: item[0])
    previous: dict[str, date] = {}
    events = [(_event_fields(row, day, pin, previous), row) for day, row in dated]
    events.sort(key=lambda item: (item[0]["source_day"], item[0]["event_id"]))
    return retrieved, events


def _read_season_metadata(content: bytes, pin: NBAArchivePin) -> NBASeasonMetadata:
    retrieved, events = _read_season_rows(content, pin, metadata=True)
    return NBASeasonMetadata(pin, retrieved, tuple(NBAArchiveEvent(**fields) for fields, _ in events))


def _read_season_archive(content: bytes, pin: NBAArchivePin) -> NBASeasonArchive:
    retrieved, events = _read_season_rows(content, pin, metadata=False)
    return NBASeasonArchive(pin, retrieved, tuple(_decode_game(fields, row) for fields, row in events))


def _own_possessions(counts: object, archived: object) -> float:
    inputs = _strict_object(counts, _POSSESSION_FIELDS, "possession inputs")
    if any(type(number) is not int or number < 0 for number in inputs.values()):
        raise ValueError("NBA archive possession inputs require nonnegative integer counts")
    try:
        possessions = espn_nba._possessions(inputs)
    except OverflowError as exc:
        raise ValueError("NBA archive possession inputs exceed numeric range") from exc
    if (
        type(archived) not in (int, float)
        or not math.isfinite(possessions)
        or possessions <= 0
        or archived != possessions
    ):
        raise ValueError("NBA archive own-team possession estimate does not match its box inputs")
    return possessions


def _event_fields(row: dict, day: date, pin: NBAArchivePin, previous: dict[str, date]) -> dict:
    raw_id = row["event_id"]
    event_id = str(raw_id) if type(raw_id) in (str, int) else ""
    if _EVENT_ID.fullmatch(event_id) is None:
        raise ValueError("NBA archive event ID requires an ASCII ESPN identity")
    started = _utc_timestamp(row["started"], "started")
    if started >= pin.retrieved_at or started.astimezone(NBA_ODR_SOURCE_TIMEZONE).date() != day:
        raise ValueError("NBA archive tipoff contradicts its New York source date or retrieval clock")

    home, away = row["home"], row["away"]
    if home == away or any(
        not isinstance(team, str) or team not in espn_nba.NBA_TEAMS for team in (home, away)
    ):
        raise ValueError("NBA archive game requires distinct official home and away teams")
    if type(row["neutral"]) is not bool:
        raise ValueError("NBA archive game requires a Boolean neutral-site marker")
    home_previous, away_previous = previous.get(home), previous.get(away)
    if home_previous == day or away_previous == day:
        raise ValueError("NBA archive has more than one game per team on a source day")
    previous[home] = previous[away] = day

    return dict(
        season=pin.season, event_id=event_id, season_source_sha256=pin.gzip_sha256,
        source_day=day, label_day=day, started=started, neutral=row["neutral"],
        home_team=home, away_team=away,
        rest_home=None if home_previous is None else (day - home_previous).days,
        rest_away=None if away_previous is None else (day - away_previous).days,
    )


def _decode_game(fields: dict, row: dict) -> NBAArchiveGame:
    home_points, away_points = row["home_pts"], row["away_pts"]
    if home_points == away_points or any(
        type(points) is not int or points < 0 for points in (home_points, away_points)
    ):
        raise ValueError("NBA archive full-game scores require whole nonnegative points and a winner")
    return NBAArchiveGame(
        **fields, home_points=home_points, away_points=away_points,
        home_possessions=_own_possessions(row["home_poss_inputs"], row["home_poss"]),
        away_possessions=_own_possessions(row["away_poss_inputs"], row["away_poss"]),
    )
