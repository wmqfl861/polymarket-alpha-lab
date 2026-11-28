"""Whole-window replay with an empty market and a corrected fixture calendar."""

from dataclasses import replace
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab.papersim import euro_soccer_study as study
from polymarket_alpha_lab.papersim import cli
from polymarket_alpha_lab.papersim.euro_soccer_sources import Fixture


TIP = datetime(2026, 11, 28, 18, tzinfo=UTC)
SEEN = TIP - timedelta(days=1)
GAME = Fixture("por", "2026/2027", "regular", "50", "10", "20", "Home", "Away", TIP,
               "scheduled", None, SEEN, "b" * 64)


class Store:
    def __init__(self):
        self.docs = {}

    def fetch(self, url, body=None, *, max_age=None):
        raise OSError("source gap")

    def keys(self, prefix):
        return {k for k in self.docs if k.startswith(prefix)}

    def save(self, key, doc):
        return self.docs.setdefault(key, doc)

    def latest(self, key):
        return self.docs.get(key)


def _history():
    games = []
    for n in range(4):
        past = TIP - timedelta(days=n + 4)
        games.append(replace(GAME, fixture_id=str(100 + n), kickoff=past, away_id="30",
                             status="finished", score=(2, 1)))
        games.append(replace(GAME, fixture_id=str(200 + n), kickoff=past, home_id="40",
                             status="finished", score=(1, 1)))
    return tuple(games)


def test_scan_persists_each_natural_window_then_scores_result_once(monkeypatch):
    store = Store()
    calendar = [GAME]
    monkeypatch.setattr(study, "_calendar",
                        lambda store, league, season, ttl: tuple(calendar) + _history() if season == GAME.season else ())
    for slot, minutes in (("preview", 65), ("forward", 25), ("closing", 2)):
        at = TIP - timedelta(minutes=minutes)
        report = study.scan_league(store, "por", clock=lambda: at)
        assert report["captured"] == {slot: 1}
        observation = store.latest(study._key(GAME) + slot)
        assert observation["forecast"]["baseline"]["home_win"] > 0
        assert observation["forecast"]["context"] is None
        assert observation["markets"]["status"] == "unavailable"
        assert observation["research_only"] and observation["amount_cny"] == 0
        assert study.scan_league(store, "por", clock=lambda: at)["captured"] == {}
    after = TIP + timedelta(hours=3)
    final = replace(GAME, status="finished", score=(1, 1), observed_at=after, source_sha256="c" * 64)
    # A changed kickoff must never settle the earlier market identity.
    calendar[:] = [replace(final, kickoff=TIP + timedelta(minutes=10))]
    assert study.scan_league(store, "por", clock=lambda: after)["results_added"] == 0
    calendar[:] = [final]
    assert study.scan_league(store, "por", clock=lambda: after)["results_added"] == 1
    result = store.latest(study._key(GAME) + "result")
    assert result["score"] == [1, 1]
    for slot in ("preview", "forward", "closing"):
        scores = result[slot]["scores"]["match_winner"]
        assert scores["baseline"]["brier"] >= 0 and scores["market"] is None and scores["context"] is None
    assert study.scan_league(store, "por", clock=lambda: after)["results_added"] == 0


def test_natural_windows_take_priority_and_only_one_preview_per_league(monkeypatch):
    store = Store()
    now = TIP - timedelta(hours=2)
    items = [replace(GAME, fixture_id=str(i), kickoff=now + timedelta(minutes=minutes))
             for i, minutes in enumerate([30, 2, 100, 120, 3, 20], 1)]
    monkeypatch.setattr(study, "_calendar", lambda s, league, season, ttl: tuple(items) if season == GAME.season else ())
    sequence = []
    def observe(store, game, calendar, listings, gaps, slot, clock):
        sequence.append((slot, game.fixture_id))
        return {"kind": "observation"}
    monkeypatch.setattr(study, "_observe", observe)
    report = study.scan_league(store, "por", clock=lambda: now)
    assert report["captured"] == {"closing": 2, "forward": 1, "preview": 1}
    assert sequence == [("closing", "2"), ("closing", "5"), ("forward", "1"), ("preview", "3")]


def test_elapsed_window_cannot_be_backfilled(monkeypatch):
    store = Store()
    moments = iter([TIP - timedelta(minutes=21), TIP - timedelta(minutes=20)])
    assert study._observe(store, GAME, (GAME,), [], [], "forward", lambda: next(moments)) is None


def test_fresh_report_that_play_has_started_precludes_a_pregame_record(monkeypatch):
    at = TIP - timedelta(minutes=2)
    store = Store()
    monkeypatch.setattr(store, "fetch", lambda *args, **kwargs: SimpleNamespace(raw=b"{}", received_at=at, sha256="b"*64))
    monkeypatch.setattr(study, "parse_prematch_news", lambda *args, **kwargs:
                        SimpleNamespace(blockers=("news_not_prematch",)))
    with pytest.raises(study.FixtureChanged):
        study._observe(store, GAME, (GAME,), [], [], "closing", lambda: at)
    assert not store.docs


def test_command_masks_connection_details(monkeypatch, capsys):
    def unavailable(action, root):
        raise RuntimeError("private connection details")
    monkeypatch.setattr(study, "_command", unavailable)
    assert cli.main(["euro-soccer", "status"]) == 1
    assert capsys.readouterr().err == "euro-soccer failed (RuntimeError)\n"


@pytest.mark.parametrize("minutes,slot", [(2881, None), (2880, "preview"), (31, "preview"), (30, "forward"),
                                         (21, "forward"), (20, None), (4, None), (3, "closing"),
                                         (1, "closing"), (0, None), (-1, None)])
def test_slot_boundaries(minutes, slot):
    assert study.capture_slot(TIP, TIP - timedelta(minutes=minutes)) == slot
