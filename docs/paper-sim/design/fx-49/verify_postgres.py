"""Prove the reconstructed FX-49 delivery paths using the runtime scratch fixture."""

from __future__ import annotations

import ast
from contextlib import ExitStack
from datetime import timedelta
import hashlib
import importlib
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest


RUNTIME = Path("docs/paper-sim/runtime")
STORE = RUNTIME / "scripts" / "football_store.py"
SCHEMA_NAMES = {"SCHEMA", "ROUND_SCHEMA"}
DDL = ("CREATE ", "ALTER ", "DROP ", "TRUNCATE ", "REINDEX ", "GRANT ", "REVOKE ")


class PinnedRuntimePackage(MetaPathFinder):
    """Check the Git blob of every package module the runtime suite imports."""

    def __init__(self, source_root, expected_blobs):
        self.source_root = source_root
        self.expected_blobs = expected_blobs
        self.verified = set()

    def find_spec(self, name, path, target=None):
        if name != "polymarket_alpha_lab" and not name.startswith("polymarket_alpha_lab."):
            return None
        spec = PathFinder.find_spec(name, path, target)
        if spec is None or not isinstance(spec.loader, SourceFileLoader):
            raise ImportError("FX-49 runtime package source is unavailable")
        source = Path(spec.origin)
        if source.is_symlink() or not source.resolve().is_relative_to(self.source_root):
            raise ImportError("FX-49 runtime package source is outside the reviewed checkout")
        key = "src/" + source.relative_to(self.source_root).as_posix()
        raw = source.read_bytes()
        digest = hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()
        if self.expected_blobs.get(key) != digest:
            raise ImportError("FX-49 imported package source differs from runtime R")
        # Compile the bytes just verified instead of accepting pre-existing bytecode.
        spec.loader.get_code = lambda _: compile(raw, str(source), "exec", dont_inherit=True)
        self.verified.add(key)
        return spec


def static_schema(source):
    """Extract the reviewed schema and every literal containing DDL."""
    assignments, statements = {}, []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in SCHEMA_NAMES:
                    assignments[target.id] = ast.literal_eval(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(line.lstrip().upper().startswith(DDL) for line in node.value.splitlines()):
                statements.append(node.value)
    assert assignments.keys() == SCHEMA_NAMES
    return assignments, sorted(statements)


def catalog(connection):
    return connection.execute("""
        SELECT 'column' AS kind, tablename || '.' || a.attname AS name,
               concat_ws(':', format_type(a.atttypid, a.atttypmod), a.attnotnull,
                         pg_get_expr(d.adbin, d.adrelid)) AS definition
        FROM pg_tables t
        JOIN pg_namespace n ON n.nspname = t.schemaname
        JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = t.tablename
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE t.schemaname = 'football_notify'
        UNION ALL
        SELECT 'index', indexname, indexdef FROM pg_indexes WHERE schemaname = 'football_notify'
        UNION ALL
        SELECT 'constraint', c.relname || '.' || k.conname, pg_get_constraintdef(k.oid)
        FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'football_notify'
        ORDER BY 1, 2, 3
    """).fetchall()


def schema_gate(blob, root, runtime, database, store):
    command = ["/usr/bin/git", "--no-replace-objects", "-C", str(root), "cat-file", "blob", blob]
    source = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env={"PATH": os.defpath, "GIT_ALLOW_PROTOCOL": "", "GIT_NO_LAZY_FETCH": "1"}).stdout
    before, statements = static_schema(source)
    after, candidate_ddl = static_schema((runtime / STORE).read_bytes())
    assert before == after and statements == candidate_ddl
    with database.connect() as connection:
        for name in ("SCHEMA", "ROUND_SCHEMA"):
            connection.execute(before[name])
        expected = catalog(connection)
        assert expected
        connection.execute("DROP SCHEMA football_notify CASCADE")
        for name in ("SCHEMA", "ROUND_SCHEMA"):
            connection.execute(getattr(store, name))
        assert catalog(connection) == expected
        connection.execute("DROP SCHEMA football_notify CASCADE")


def connect_outside_fixture(*_args, **_kwargs):
    raise AssertionError("FX-49 tests must use the temporary PostgreSQL fixture")


def reject_external_io(event, _arguments):
    if event.startswith("socket.") or event in {"os.system", "os.posix_spawn", "subprocess.Popen"}:
        raise AssertionError("unexpected external I/O in the FX-49 database suite")


@pytest.mark.parametrize("refused", [False, True])
def test_pregame_durable_claim(refused):
    import football_daily as daily
    import football_store as store

    cases = importlib.import_module("test_fx49_football_delivery")
    fixture = cases.FootballDeliveryTests()
    with ExitStack() as stack:
        stack.callback(fixture.doCleanups)
        fixture.setUp()
        connection = fixture.connection
        now = fixture.now
        token, key = "3" * 32, "synthetic-pregame"
        deadline = now + timedelta(minutes=2)
        connection.execute(store.ROUND_SCHEMA)
        connection.execute(daily.portfolio.SCHEMA)
        connection.execute("""
            INSERT INTO sports_portfolio_account (restart_key, started_at, opening_cash_fen)
            VALUES (%s, %s, %s)
        """, (daily.portfolio.RESTART_KEY, now - timedelta(days=1), 10000))
        store.add_job(connection, "t90", now - timedelta(minutes=1), deadline,
                      fixture={"id": cases.FIXTURE, "revision": 1})
        rows = store.due_pregame_rows(connection, now)
        assert len(rows) == 1
        store.open_round(connection, key, now, rows)
        store.lease_jobs(connection, rows, token, now, round_key=key)
        selection = SimpleNamespace(
            rows=rows, body=cases.BODY, valid_until=deadline, entries=[], reasons={},
            quotes=[], allocations=[], available_cash_fen=10000, realized_pnl_fen=0,
        )
        if refused:
            fixture.deny_send = "hermes_platform_write_denied"
        before = catalog(connection)
        with (
            store.native_delivery_lock(connection) as locked,
            patch.object(daily, "pregame_decisions", return_value={}),
            patch.object(daily, "current_round_selection", return_value=selection),
        ):
            assert locked
            result = daily.deliver_pregame_round(connection, rows, token, key, cases.TARGET, cases.SESSION, now)
        assert result == {"state": "unknown" if refused else "accepted", "stage": "pregame",
                          "fixtures": 1, "considered": 1}
        attempt = fixture.attempt()
        assert attempt["reason"] == ("fixed_entry_hermes_platform_write_denied" if refused else None)
        assert not attempt["native_session_verified"]
        assert fixture.calls.count("send") == 1
        assert fixture.calls.count(("receipt", "fixed")) == (0 if refused else 1)
        fixture.now += store.SUBMITTED_LEASE_LENGTH + timedelta(seconds=1)
        assert fixture.worker()["state"] == "idle"
        assert fixture.calls.count("send") == 1
        assert catalog(connection) == before


def main():
    runtime, main_checkout, pg_bin = (Path(arg).resolve() for arg in sys.argv[1:4])
    blob = sys.argv[4]
    expected_blobs = json.loads(Path(sys.argv[5]).read_bytes())
    runtime_tests = runtime / RUNTIME / "tests"
    package_root = main_checkout / "src"
    sys.path[:0] = [str(runtime_tests), str(runtime / RUNTIME / "scripts"), str(package_root)]
    assert not any(name == "polymarket_alpha_lab" or name.startswith("polymarket_alpha_lab.")
                   for name in sys.modules)
    importer = PinnedRuntimePackage(package_root, expected_blobs)
    sys.meta_path.insert(0, importer)
    os.environ["PAL_PAPER_ACCOUNT_KEY"] = "synthetic-fx49-ci"
    import psycopg
    from offline_postgres import temporary_postgres, scratch_database

    with temporary_postgres(pg_bin), patch.object(psycopg, "connect", connect_outside_fixture):
        import football_store as store

        class RequiredFixture:
            @staticmethod
            def skipTest(_reason):
                raise AssertionError("isolated PostgreSQL fixture is missing")

        schema_gate(blob, runtime, runtime, scratch_database(RequiredFixture()), store)
        sys.addaudithook(reject_external_io)
        result = pytest.main([
            "-q", "-p", "no:cacheprovider", str(runtime_tests / "test_football_focus.py"),
            str(runtime_tests / "test_fx49_football_delivery.py"), str(Path(__file__).resolve()),
        ])
        assert importer.verified, "FX-49 runtime suite loaded no verified package source"
        return result


if __name__ == "__main__":
    raise SystemExit(main())
