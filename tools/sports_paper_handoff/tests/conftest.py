"""Use synthetic feeds and the restored runtime's disposable Unix-socket PostgreSQL."""

import importlib.util
from pathlib import Path
import socket
import subprocess

import pytest

import football_feed
import football_store
import sports_portfolio


@pytest.fixture(autouse=True)
def offline_sources(monkeypatch):
    def no_external_source(*_args, **_kwargs):
        pytest.fail("offline price regressions must use synthetic feeds and their scratch database")

    monkeypatch.setattr(socket, "getaddrinfo", no_external_source)
    monkeypatch.setattr(socket.socket, "connect", no_external_source)
    monkeypatch.setattr(football_store, "connect", no_external_source)


@pytest.fixture
def account():
    helper_path = Path(football_feed.__file__).resolve().parents[1] / "tests" / "offline_postgres.py"
    spec = importlib.util.spec_from_file_location("sports_price_offline_postgres", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    pg_bin = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
    key = "synthetic-depth-freeze-account"
    with helper.temporary_postgres(pg_bin):
        # The retained helper publishes its instance for the lifetime of this context.
        with helper.scratch_database(None).connect() as db:
            sports_portfolio.restart(db, restart_key=key)
            yield db, key
