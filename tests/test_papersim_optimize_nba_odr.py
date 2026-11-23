"""Synthetic inputs for the fixed FX-41 NBA v2 archive reader."""

import gzip
import json
from collections import Counter
from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

import pytest

from polymarket_alpha_lab.papersim.espn_nba import NBA_TEAMS
from polymarket_alpha_lab.papersim.optimize import nba_odr

_RETRIEVED = datetime(2026, 11, 19, 1, 30, tzinfo=UTC)
_SOURCES = {
    "schedule": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
                "/teams/{ESPN team id}/schedule?season={year}&seasontype=2",
    "box_inputs": "https://www.basketball-reference.com/teams/"
                  "{Basketball Reference abbreviation}/{year}/gamelog/",
}
_FIRST = date(2024, 11, 2)
_SECOND = date(2024, 11, 3)
_THIRD = date(2024, 11, 4)
_FOURTH = date(2024, 11, 5)


def _game(event_id, day, *, started=None, home="GS", away="BOS", neutral=False):
    # Synthetic full-game points (116–112 after a 108–108 regulation tie).
    return {
        "event_id": event_id, "date": day.isoformat(),
        "started": started or f"{day.isoformat()}T23:30:00Z",
        "home": home, "away": away, "neutral": neutral, "home_pts": 116, "away_pts": 112,
        "home_poss": 104.0, "away_poss": 102.8,
        "home_poss_inputs": {"fga": 88, "oreb": 10, "tov": 15, "fta": 25},
        "away_poss_inputs": {"fga": 92, "oreb": 13, "tov": 15, "fta": 20},
    }


def _season_bytes(season, rows, **header):
    data = {
        "season": season, "sources": _SOURCES, "retrieved_at": _RETRIEVED.isoformat(), "games": rows,
        **header,
    }
    decoded = json.dumps(data, sort_keys=True).encode()
    raw = gzip.compress(decoded, mtime=0)
    dates = Counter(date.fromisoformat(row["date"]) for row in rows)
    pin = nba_odr.NBAArchivePin(
        season=season, gzip_byte_length=len(raw), gzip_sha256=sha256(raw).hexdigest(),
        decoded_byte_length=len(decoded), decoded_sha256=sha256(decoded).hexdigest(),
        retrieved_at=_RETRIEVED, first_day=min(dates), last_day=max(dates), day_count=len(dates),
        training_only=tuple(sorted(dates.items())[:3]),
    )
    return raw, pin


@pytest.fixture
def season():
    rows = [
        _game("104", _FOURTH, away="NY"),
        _game("100", _FIRST, started="2024-11-03T03:30:00Z", neutral=True),
        _game("101", _FIRST, home="LAL", away="NY"),
        _game(102, _SECOND, started="2024-11-04T04:30:00Z", home="BOS", away="GS"),
        _game("103", _THIRD, home="LAL", away="GS"),
    ]
    return nba_odr._read_season_archive(*_season_bytes("2024-25", rows))


def test_archive_emits_reciprocal_team_rows_and_full_game_contracts(season):
    game = season.games_by_day[_FIRST][0]
    encoded = f'["2024-25","100","{season.pin.gzip_sha256}"]'.encode()
    assert game.event_key == sha256(encoded).hexdigest()
    assert game.source_identity == ("2024-25", "100", season.pin.gzip_sha256)
    assert season.games_by_key[game.event_key] is game
    assert game.started == datetime(2024, 11, 3, 3, 30, tzinfo=UTC)
    assert game.source_day == game.label_day == _FIRST
    assert game.available_at is None
    assert game.neutral is True
    assert game.prediction_fixture().home == "GS"
    assert game.prediction_fixture().away == "BOS"
    assert (game.total, game.margin) == (228, 4)
    assert dict(nba_odr.NBA_ODR_LINES) == {
        "moneyline": (None, ("away", "home")),
        "totals": (225.5, ("under", "over")),
    }
    assert game.settled_side("moneyline") == "home"
    assert game.settled_side("totals") == "over"
    assert replace(game, home_points=106).settled_side("moneyline") == "away"
    assert replace(game, home_points=106).settled_side("totals") == "under"

    history = season.history_at(_SECOND, [item.event_key for item in season.games_by_day[_FIRST]])
    assert history["season"] == "2024-25"
    assert history["injuries"] == {}
    assert history["teams"]["GS"]["display"] == "Golden State Warriors"
    home, = history["teams"]["GS"]["games"]
    away, = history["teams"]["BOS"]["games"]
    assert home["event_key"] == away["event_key"] == game.event_key
    assert (home["opponent"], home["home"], home["pts"], home["opp_pts"]) == ("BOS", True, 116, 112)
    assert (away["opponent"], away["home"], away["pts"], away["opp_pts"]) == ("GS", False, 112, 116)
    assert (home["poss"], away["poss"]) == (104.0, 102.8)
    assert (home["ortg"], home["drtg"]) == (100 * 116 / 104.0, 100 * 112 / 104.0)
    assert (away["ortg"], away["drtg"]) == (100 * 112 / 102.8, 100 * 116 / 102.8)


def test_prediction_rest_uses_earlier_same_season_source_dates(season):
    first, second, third, fourth = (season.games_by_day[day][0] for day in (_FIRST, _SECOND, _THIRD, _FOURTH))
    assert (first.rest_home, first.rest_away) == (None, None)
    assert second.event_id == "102"
    assert (second.prediction_fixture().b2b_home, second.prediction_fixture().b2b_away) == (True, True)
    assert (third.rest_home, third.rest_away) == (2, 1)
    assert (fourth.rest_home, fourth.rest_away) == (1, 3)
    assert fourth.prediction_fixture().before_date == _FOURTH.isoformat()

    opener = date(2025, 10, 21)
    other = nba_odr._read_season_archive(*_season_bytes("2025-26", [_game("100", opener)]))
    next_season = other.games[0]
    assert next_season.event_id == first.event_id
    assert next_season.event_key != first.event_key
    assert (next_season.rest_home, next_season.rest_away) == (None, None)
    with pytest.raises(ValueError, match="strictly earlier available games from its own season"):
        other.history_at(opener, [first.event_key])


def test_source_day_start_and_tipoff_date_follow_new_york_dst():
    assert nba_odr.source_day_start_utc(date(2024, 11, 3)) == datetime(2024, 11, 3, 4, tzinfo=UTC)
    assert nba_odr.source_day_start_utc(date(2024, 11, 4)) == datetime(2024, 11, 4, 5, tzinfo=UTC)
    assert nba_odr.source_day_start_utc(date(2025, 3, 9)) == datetime(2025, 3, 9, 5, tzinfo=UTC)
    assert nba_odr.source_day_start_utc(date(2025, 3, 10)) == datetime(2025, 3, 10, 4, tzinfo=UTC)
    row = _game("105", date(2025, 3, 8), started="2025-03-09T04:30:00Z")
    archive = nba_odr._read_season_archive(*_season_bytes("2024-25", [row]))
    assert archive.games[0].source_day == date(2025, 3, 8)
    row["date"] = "2025-03-09"
    with pytest.raises(ValueError, match="New York source date"):
        nba_odr._read_season_archive(*_season_bytes("2024-25", [row]))


def test_training_barrier_respects_whole_days_and_late_label_evidence(season):
    first = season.games_by_day[_FIRST]
    keys = [game.event_key for game in first]
    with pytest.raises(ValueError, match="whole training source days"):
        season.history_at(_SECOND, keys[:1])
    with pytest.raises(ValueError, match="duplicates a training event"):
        season.history_at(_SECOND, keys + keys[:1])
    with pytest.raises(ValueError, match="strictly earlier available games"):
        season.history_at(_FIRST, keys)

    observed_at = nba_odr.source_day_start_utc(_THIRD)
    delayed = replace(first[0], available_at=observed_at)
    with pytest.raises(ValueError, match="availability contradicts the source clocks"):
        replace(first[0], available_at=first[0].started - timedelta(seconds=1))
    with_late_game = replace(season, games=(delayed, *season.games[1:]))
    assert delayed.effective_label_day == _THIRD
    assert with_late_game.games_by_day[_SECOND][0].prediction_fixture().rest_home == 1
    with pytest.raises(ValueError, match="strictly earlier available games"):
        with_late_game.history_at(_THIRD, keys)
    history = with_late_game.history_at(_FOURTH, keys)
    assert len(history["teams"]) == 4


def test_future_scores_do_not_enter_earlier_fixture_or_training_history(season):
    keys = [game.event_key for game in season.games_by_day[_FIRST]]
    earlier_prediction = season.games_by_day[_SECOND][0].prediction_fixture()
    earlier_history = json.dumps(season.history_at(_SECOND, keys), sort_keys=True).encode()
    changed_result = replace(season.games[-1], home_points=160, away_points=112)
    revised = replace(season, games=(*season.games[:-1], changed_result))
    assert changed_result.total != season.games[-1].total
    assert revised.games_by_day[_SECOND][0].prediction_fixture() == earlier_prediction
    assert json.dumps(revised.history_at(_SECOND, keys), sort_keys=True).encode() == earlier_history


@pytest.mark.parametrize(("changes", "error"), [
    ({"neutral": 1}, "neutral-site marker"),
    ({"home": "GSW"}, "distinct official home and away"),
    ({"away": "GS"}, "distinct official home and away"),
    ({"home_pts": 112}, "require whole nonnegative points and a winner"),
    ({"home_pts": True}, "require whole nonnegative points and a winner"),
    ({"away_poss": 104.0}, "own-team possession estimate"),
    ({"away_poss_inputs": {"fga": 92, "oreb": False, "tov": 15, "fta": 20}}, "integer counts"),
    ({"event_id": True}, "ASCII ESPN identity"),
    ({"started": "2024-11-02T23:30:00"}, "aware timestamp"),
    ({"available_at": "2024-11-02T23:30:00Z"}, "unexpected fields"),
])
def test_archive_reader_rejects_invalid_fields(changes, error):
    row = _game("100", _FIRST)
    row.update(changes)
    with pytest.raises(ValueError, match=error):
        nba_odr._read_season_archive(*_season_bytes("2024-25", [row]))


def test_reader_rejects_duplicate_event_and_ambiguous_team_games():
    repeated = [_game(100, _FIRST), _game("100", _SECOND)]
    with pytest.raises(ValueError, match="duplicate event identity"):
        nba_odr._read_season_archive(*_season_bytes("2024-25", repeated))

    ambiguous = [_game("100", _FIRST), _game("101", _FIRST, home="LAL", away="GS")]
    with pytest.raises(ValueError, match="more than one game per team"):
        nba_odr._read_season_archive(*_season_bytes("2024-25", ambiguous))


def test_gzip_and_decoded_pins_are_checked_independently():
    row = _game("100", _FIRST)
    content, pin = _season_bytes("2024-25", [row])
    modified = content[:-1] + bytes([content[-1] ^ 1])
    with pytest.raises(ValueError, match="gzip length or SHA-256 mismatch"):
        nba_odr._read_season_archive(modified, pin)
    with pytest.raises(ValueError, match="decoded length or SHA-256 mismatch"):
        nba_odr._read_season_archive(content, replace(pin, decoded_sha256="0" * 64))
    with pytest.raises(ValueError, match="exactly the two approved seasonal archives"):
        nba_odr.load_nba_odr_archives({"2024-25": content})
    with pytest.raises(ValueError, match="gzip length or SHA-256 mismatch"):
        nba_odr.load_nba_odr_archives({"2024-25": content, "2025-26": content})


def test_reader_checks_archive_season_provenance_and_retrieval_clock():
    row = _game("100", _FIRST)
    altered, pin = _season_bytes("2024-25", [row], sources={**_SOURCES, "schedule": "other source"})
    with pytest.raises(ValueError, match="different season or source URLs"):
        nba_odr._read_season_archive(altered, pin)
    content, pin = _season_bytes("2024-25", [row])
    with pytest.raises(ValueError, match="different season or source URLs"):
        nba_odr._read_season_archive(content, replace(pin, season="2025-26"))
    with pytest.raises(ValueError, match="retrieval clock conflicts"):
        nba_odr._read_season_archive(content, replace(pin, retrieved_at=_RETRIEVED - timedelta(seconds=1)))


def test_value_free_metadata_keeps_exact_holdout_membership_without_using_labels():
    prior_day, first_held, last_held = (date(2026, 3, 4), date(2026, 3, 5), date(2026, 4, 12))
    prior, first, last = (_game(event_id, day) for event_id, day in (
        ("150", prior_day), ("151", first_held), ("152", last_held),
    ))
    first["home_pts"] = first["away_pts"]
    last["home_poss"] = 0
    content, pin = _season_bytes("2025-26", [first, prior, last])
    metadata = nba_odr._read_season_metadata(content, pin)
    assert metadata.holdout_days == (first_held, last_held)
    assert tuple(metadata.games_by_key[key].event_id for key in metadata.holdout_event_keys) == ("151", "152")
    event = metadata.games_by_day[first_held][0]
    assert type(event) is nba_odr.NBAArchiveEvent
    assert {item.name for item in fields(event)} == {
        "season", "event_id", "event_key", "season_source_sha256", "source_day", "label_day",
        "started", "available_at", "home_team", "away_team", "neutral", "rest_home", "rest_away",
    }
    assert (event.rest_home, event.rest_away) == (1, 1)
    assert event.available_at is None
    with pytest.raises(ValueError, match="full-game scores"):
        nba_odr._read_season_archive(content, pin)


def test_metadata_checks_nested_box_input_field_presence():
    row = _game("100", _FIRST)
    del row["home_poss_inputs"]["oreb"]
    with pytest.raises(ValueError, match="possession inputs has unexpected fields"):
        nba_odr._read_season_metadata(*_season_bytes("2024-25", [row]))


def test_two_season_loader_keeps_independent_espn_evidence_unknown(monkeypatch):
    teams = tuple(NBA_TEAMS)
    seasons = {"2024-25": date(2024, 10, 22), "2025-26": date(2025, 10, 21)}
    pins = {}
    archives = {}
    for season_index, (season, opener) in enumerate(seasons.items()):
        rows = [
            _game(
                str(10_000 * (season_index + 1) + 15 * day_index + team_index // 2),
                opener + timedelta(days=day_index),
                home=teams[team_index], away=teams[team_index + 1],
            )
            for day_index in range(82)
            for team_index in range(0, 30, 2)
        ]
        archives[season], pins[season] = _season_bytes(season, rows)

    monkeypatch.setattr(nba_odr, "NBA_ODR_ARCHIVE_PINS", pins)
    loaded = nba_odr.load_nba_odr_archives(archives)
    metadata = nba_odr.load_nba_odr_metadata(archives)
    assert tuple(loaded) == tuple(seasons)
    for season, archive in loaded.items():
        originals = archive.independent_espn
        assert metadata[season].independent_espn == originals
        assert tuple(metadata[season].games_by_key) == tuple(archive.games_by_key)
        assert archive.pin.gzip_sha256 == sha256(archives[season]).hexdigest()
        assert len(archive.games) == 1_230
        assert archive.retrieved_at == _RETRIEVED
        assert originals.inventory_observed_at > archive.retrieved_at
        assert originals.cache_roots == ("espn_default", "espn_prior_backfill")
        assert (originals.available_team_schedules, originals.required_team_schedules) == (0, 30)
        assert (originals.available_game_summaries, originals.required_game_summaries) == (0, 1_230)
        assert originals.teams_equal is None
        assert originals.injuries_equal is None
        assert originals.reason == "registered_raw_schedules_and_summaries_unavailable"
        assert all(game.available_at is None for game in archive.games)
        assert archive.games[0].prediction_fixture().rest_home is None
