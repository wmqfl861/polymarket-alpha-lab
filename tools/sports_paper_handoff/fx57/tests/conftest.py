"""Check normal evidence writes in a disposable database with local delivery isolated."""

from datetime import timedelta

import pytest

import football_daily as daily
import football_store as store
import sports_focus
import sports_portfolio as portfolio
from tools.sports_paper_handoff.tests.conftest import account, offline_sources

__all__ = ["account", "offline_sources"]


@pytest.fixture
def persist_quote(account, monkeypatch):
    db, key = account

    def persist(quote, now):
        db.execute("UPDATE football_notify.sports_portfolio_account SET started_at=%s WHERE restart_key=%s",
                   (quote.observed_at - timedelta(minutes=1), key))
        monkeypatch.setattr(store, "db_now", lambda _: now)
        allocated = portfolio.plan(db, [quote], restart_key=key)
        assert len(allocated) == 1
        assert portfolio.enter(db, [quote], restart_key=key, round_key="synthetic", expected=allocated) == allocated
        position, = portfolio.open_positions(db, restart_key=key)
        return position["settlement_evidence"]

    return persist


@pytest.fixture
def persist_watch(account, monkeypatch):
    db, _ = account
    db.execute(store.SCHEMA)

    def persist(watch, now):
        db.execute("""INSERT INTO football_notify.fixture
                      (id, revision, kickoff, fixture_state, info, watch, highlight, first_seen, last_seen)
                      VALUES (%s,%s,%s,'scheduled','{}',true,false,%s,%s)""",
                   (watch["fixture_key"], watch["revision"], watch["kickoff"], now, now))
        monkeypatch.setattr(store, "db_now", lambda _: now)
        monkeypatch.setattr(sports_focus, "build", lambda *_args, **_kw: {"entries": [watch], "body": "synthetic"})
        # Personal recipient binding, body checks and worker startup belong to the local delivery boundary.
        monkeypatch.setattr(store, "binding", lambda _: None)
        monkeypatch.setattr(daily, "valid_focus_body", lambda *_: True)
        monkeypatch.setattr(daily, "start_workers", lambda *_: None)
        result = sports_focus.publish(db)
        assert result["state"] == "queued"
        row = db.execute("SELECT detail->'evidence' AS evidence FROM job WHERE key=%s", (result["job_key"],)).fetchone()
        saved, = row["evidence"]
        return saved

    return persist
