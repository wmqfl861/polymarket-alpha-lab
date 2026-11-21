"""MO-6 CLI tests: the optimize families/init/run/status wiring (T5).

The papersim CLI startup budget is pinned separately by
tests/test_init_lazy_imports.py (limit 20, measured baseline 19): the
optimize wiring adds ZERO startup imports — every heavy import lives
inside the command handlers.
"""

from __future__ import annotations

import json

import pytest

from polymarket_alpha_lab.papersim.cli import main


def test_optimize_families_lists_every_registered_family(capsys) -> None:
    assert main(["optimize", "families"]) == 0
    payload = json.loads(capsys.readouterr().out)
    families = payload["families"]
    ids = [family["family_id"] for family in families]
    assert ids == sorted(ids)
    # Seven registered families at MO-6 (league-bridge merged as MO-5e
    # with NO lineages: targets == ()).
    assert set(ids) == {
        "club-soccer-frozen", "club-soccer-recalib", "crypto-gbm",
        "intl-elo-goal-residuals", "intl-elo-poisson", "league-bridge",
        "nba-pace-efficiency",
    }
    bridge = next(f for f in families if f["family_id"] == "league-bridge")
    assert bridge["targets"] == []
    assert bridge["searchable"] is False
    recalib = next(f for f in families if f["family_id"] == "club-soccer-recalib")
    assert recalib["searchable"] is True
    assert len(recalib["param_space"]) == 2  # the v1 3x3 grid
    nba = next(f for f in families if f["family_id"] == "nba-pace-efficiency")
    assert nba["baseline_model_version"].startswith("wp-nba2+")
    frozen = next(f for f in families if f["family_id"] == "club-soccer-frozen")
    assert frozen["searchable"] is False
    assert frozen["baseline_model_version"] == "soccer-r23"


def test_optimize_families_leaves_startup_modules_untouched() -> None:
    # Focused duplicate of the pinned budget: importing the CLI (which
    # builds the optimize parsers) must not load the optimize runner.
    import subprocess
    import sys

    code = (
        "import polymarket_alpha_lab.papersim.cli, sys\n"
        "names = [n for n in sys.modules if n == 'polymarket_alpha_lab' "
        "or n.startswith('polymarket_alpha_lab.')]\n"
        "print(len(names)); "
        "print('polymarket_alpha_lab.papersim.optimize.runner' in sys.modules)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", code],
        capture_output=True, text=True, check=True,
    )
    lines = proc.stdout.strip().splitlines()
    assert int(lines[0]) == 19
    assert lines[1] == "False"


def test_optimize_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["optimize"])
    assert excinfo.value.code == 2


def test_optimize_run_requires_a_connection_source(capsys) -> None:
    # In the merged optimize group (MO-8 skeleton) the connection args
    # come from _optimize_source without a required mutex group: a
    # missing source fails in the handler with the FAIL line (exit 1),
    # the same semantics as MO-8's own subcommands.
    assert (
        main([
            "optimize", "run",
            "--family", "nba-pace-efficiency",
            "--as-of", "2026-11-10T07:10:08Z",
            "--input", "whatever.json",
            "--repo-root", ".",
        ]) == 1
    )
    assert "FAIL" in capsys.readouterr().err


def test_optimize_run_missing_input_fails_fast_without_connection(
    capsys, tmp_path
) -> None:
    # A malformed input document fails BEFORE any connection is opened
    # (the handler validates the input first).
    missing = tmp_path / "missing-input.json"
    assert (
        main([
            "optimize", "run",
            "--family", "nba-pace-efficiency",
            "--as-of", "2026-11-10T07:10:08Z",
            "--input", str(missing),
            "--managed",
        ]) == 1
    )
    assert "FAIL" in capsys.readouterr().err


def test_optimize_run_rejects_unknown_connection_free_input_shapes(
    capsys, tmp_path
) -> None:
    document = tmp_path / "input.json"
    document.write_text("{}", encoding="utf-8")
    assert (
        main([
            "optimize", "run",
            "--family", "nba-pace-efficiency",
            "--as-of", "2026-11-10T07:10:08Z",
            "--input", str(document),
            "--managed",
        ]) == 1
    )
    assert "papersim_optimize_inputs_empty" in capsys.readouterr().err


def test_optimize_help_names_the_four_subcommands(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["optimize", "--help"])
    assert excinfo.value.code == 0
    text = capsys.readouterr().out
    for token in ("families", "init", "run", "status"):
        assert token in text


def _run(path):
    return ["optimize", "run", "--family", "nba-pace-efficiency",
            "--as-of", "2026-11-10T05:00:00Z", "--seed", "20261120",
            "--input-manifest", str(path)]


def test_registered_run_requires_a_connection_source(capsys) -> None:
    for options in ([], ["--managed", "--dsn", "unused"]):
        with pytest.raises(SystemExit) as excinfo:
            main([*_run("unused.json"), *options])
        assert excinfo.value.code == 2
        assert "error" in capsys.readouterr().err


def test_registered_run_missing_input_fails_fast_without_connection(
    capsys, tmp_path, monkeypatch
) -> None:
    from polymarket_alpha_lab.papersim import cli

    def unexpected(*args, **kwargs):
        pytest.fail("unregistered input attempted to open the database")
    monkeypatch.setattr(cli, "_open_readonly_connection", unexpected)
    assert main([*_run(tmp_path / "missing-input.json"), "--managed"]) == 1
    assert "FAIL" in capsys.readouterr().err


def test_registered_run_rejects_unknown_connection_free_input_shapes(
    capsys, tmp_path, monkeypatch
) -> None:
    from polymarket_alpha_lab.papersim import cli

    document = tmp_path / "input.json"
    document.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "_open_readonly_connection",
                        lambda *args, **kwargs: pytest.fail("unexpected DB access"))
    assert main([*_run(document), "--managed"]) == 1
    assert "papersim_optimize_manifest_pointer_invalid" in capsys.readouterr().err


def test_manifest_requires_registered_bytes_and_exact_protocol(monkeypatch):
    from hashlib import sha256
    from polymarket_alpha_lab.papersim import cli, snapshots
    from polymarket_alpha_lab.papersim.optimize import runner

    protocol = runner.historical_protocol("nba-pace-efficiency")
    source = {"role": "nba_snapshot", "key": "snapshot", "kind": "registered_research_input",
              "schema_version": 1, "sha256": "a" * 64, "byte_length": 100}

    def read(sources, expected_protocol=protocol, *, stored_kind="optimizer_experiment_manifest"):
        manifest = {"protocol": protocol, "sources": sources, "schema_version": 1}
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        digest = sha256(raw).hexdigest()
        monkeypatch.setattr(snapshots, "read_snapshot", lambda conn, key: {
            "kind": stored_kind, "schema_version": 1, "byte_length": len(raw), "content": raw})
        return cli._registered_optimization_manifest(None, digest, len(raw), expected_protocol)

    _, refs = read([source])
    assert refs == {"a" * 64: 100}
    absent = {**source, "key": "unrecovered", "required": False, "sha256": None,
              "byte_length": None, "absent_reason": "archive_unavailable"}
    _, refs = read([source, absent])
    assert refs == {"a" * 64: 100}
    invalid = [
        [source, source],
        [{**source, "key": "../outside"}],
        [{**source, "key": "https://example.invalid/data"}],
        [{**source, "executable": "hidden option"}],
        [source, {**source, "key": "other", "byte_length": 101}],
        [source, {**absent, "required": True}],
        [{**source, "sha256": source["sha256"].upper()}],
    ]
    for sources in invalid:
        with pytest.raises(ValueError):
            read(sources)
    with pytest.raises(ValueError, match="manifest_invalid"):
        read([source], {**protocol, "seed": True})
    with pytest.raises(ValueError, match="snapshot_invalid"):
        read([source], stored_kind="registered_research_input")

    for field in ("captured_at", "provider_published_at"):
        stamped = {**source, "provenance": {field: "2026-11-10T05:00:00Z"}}
        assert read([stamped])[1] == refs
        for invalid_stamp in (None, "2026-11-10", 42):
            with pytest.raises(ValueError, match="manifest_invalid"):
                read([{**source, "provenance": {field: invalid_stamp}}])


def test_run_excludes_search_and_publication_overrides(capsys):
    for flag in ("--max-trials", "--budget-seconds", "--threshold", "--promote"):
        with pytest.raises(SystemExit) as error:
            main([*_run("registered.json"), "--managed", flag, "1"])
        assert error.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize(("option", "value"), [
    ("--seed", "27"),
    ("--as-of", "2026-11-11T05:00:00Z"),
    ("--as-of", "2026-11-10T05:00:00"),
    ("--attempt", "0"),
])
def test_run_rejects_unregistered_protocol_before_opening_inputs(
    option, value, monkeypatch, capsys
):
    from polymarket_alpha_lab.papersim import cli

    def unexpected(*args, **kwargs):
        pytest.fail("protocol rejection must precede input and database access")

    monkeypatch.setattr(cli, "_optimization_pointer", unexpected)
    monkeypatch.setattr(cli, "_open_readonly_connection", unexpected)
    args = _run("unused.json")
    if option in args:
        args[args.index(option) + 1] = value
    else:
        args.extend([option, value])
    assert main([*args, "--managed"]) == 1
    err = capsys.readouterr().err
    expected = "time_invalid" if option == "--as-of" and not value.endswith("Z") else "protocol_mismatch"
    assert "papersim_optimize_" + expected in err


def test_registered_pointer_rejects_duplicate_json_keys(tmp_path):
    from polymarket_alpha_lab.papersim import cli

    pointer = tmp_path / "request.json"
    pointer.write_text('{"schema_version":1,"schema_version":1,"manifest_sha256":"' +
                       "a" * 64 + '","manifest_byte_length":44}')
    with pytest.raises(ValueError, match="manifest_pointer_invalid"):
        cli._optimization_pointer(pointer)


@pytest.mark.parametrize(("source", "outcome", "expected_exit"), [
    (["--managed"], None, 0),
    (["--dsn", "isolated-test-connection"], "RUN_FAILED", 1),
])
def test_registered_dry_run_uses_only_the_read_path(source, outcome, expected_exit,
                                                   tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from polymarket_alpha_lab.papersim import cli, store_psycopg
    from polymarket_alpha_lab.papersim.optimize import runner

    digest = "a" * 64
    pointer = tmp_path / "registered.json"
    pointer.write_text(json.dumps({"schema_version": 1, "manifest_sha256": digest,
                                   "manifest_byte_length": 81}))
    events = []
    connection = SimpleNamespace(close=lambda: events.append("closed"))
    monkeypatch.setattr(cli, "require_clean_worktree_path", lambda path: "b" * 40)
    monkeypatch.setattr(cli, "_open_readonly_connection", lambda *a, **kw: connection)
    @contextmanager
    def read_transaction(conn, clock, *, stage, closeout):
        assert conn is connection and stage == "manifest_read" and closeout
        events.append("begin-read")
        yield
        events.append("end-read")

    monkeypatch.setattr(store_psycopg, "optimization_read_transaction", read_transaction,
                        raising=False)
    manifest = {"sources": []}

    def read_manifest(conn, key, size, protocol):
        assert conn is connection and (key, size) == (digest, 81)
        assert protocol["family"] == "nba-pace-efficiency"
        events.append("manifest")
        return manifest, {}

    def preview(conn, **request):
        assert conn is connection and request["manifest"] is manifest
        assert request["random_seed"] == 20261120 and request["attempt"] == 3
        assert request["operation_key"] == "stored-key"
        assert "compute" not in request and "reconnect" not in request
        events.append("preview")
        return {"dry_run": True, "outcome": outcome, "artifact_bytes": b"private result"}

    def unexpected(*args, **kwargs):
        pytest.fail("dry-run reached a writer or model callback")

    monkeypatch.setattr(cli, "_registered_optimization_manifest", read_manifest)
    monkeypatch.setattr(store_psycopg, "preview_managed_optimization", preview, raising=False)
    monkeypatch.setattr(store_psycopg, "run_managed_optimization", unexpected)
    monkeypatch.setattr(runner, "historical_run_arguments", unexpected)
    monkeypatch.setattr(runner, "execute_optimization_run", unexpected)
    args = ["optimize", "run", "--family", "nba-pace-efficiency",
            "--as-of", "2026-11-10T05:00:00Z", "--input-manifest", str(pointer),
            "--dry-run", "--operation-key", "stored-key", "--attempt", "3", *source]
    assert main(args) == expected_exit
    assert json.loads(capsys.readouterr().out) == {"dry_run": True, "outcome": outcome}
    assert events == ["begin-read", "manifest", "end-read", "preview", "closed"]


@pytest.mark.parametrize("source", ["managed", "dsn"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(("document", "classification"), [
    ("{}", "inputs_empty"),
    ('{"inputs":[]}', "inputs_empty"),
    (" \r\n" * 2000 + '{"inputs":[]}', "inputs_empty"),
    ('{"historical":true}', "inputs_empty"),
    ('{"historical":true,"inputs":[]}', "inputs_empty"),
    ('{"inputs":{}}', "inputs_empty"),
    ('{"inputs":""}', "inputs_empty"),
    ('{"inputs":[{}],"inputs":[]}', "inputs_empty"),
    ('{"inputs":[{"path":"unopened.json","kind":"espn_nba_snapshot"}]}',
     "input_manifest_required"),
    ('{"archive_root":"unopened-archive","inputs":[]}', "input_manifest_required"),
    ('{"archive_root":"unopened-archive","inputs":[{"path":"x","kind":"nba"}]}', "input_invalid"),
    ('{"archive_root":null}', "input_invalid"),
    ('{"inputs":null}', "input_invalid"),
    ('{"inputs":[{}]}', "input_invalid"),
    ('{"inputs":[]', "input_invalid"),
    ("[]", "input_invalid"),
])
def test_legacy_run_classifies_input_before_connecting(tmp_path, monkeypatch, capsys,
                                                       source, dry_run, document, classification):
    from polymarket_alpha_lab.papersim import cli
    from polymarket_alpha_lab.papersim.optimize import runner

    def unexpected(*args, **kwargs):
        pytest.fail("legacy input reached the old loader, a business path or the database")

    legacy = tmp_path / "legacy.json"
    legacy.write_text(document, encoding="utf-8")
    open_document = type(legacy).open

    def only_legacy_input(path, *args, **kwargs):
        assert path == legacy
        return open_document(path, *args, **kwargs)

    monkeypatch.setattr(cli, "_open_readonly_connection", unexpected)
    monkeypatch.setattr(runner, "resolve_run_inputs", unexpected)
    monkeypatch.setattr(type(legacy), "open", only_legacy_input)
    connect = (["--managed"] if source == "managed"
               else ["--dsn", "host=127.0.0.1 dbname=pal_fixture user=pal_app"])
    argv = ["optimize", "run", "--family", "nba-pace-efficiency", "--as-of",
            "2026-11-10T05:00:00Z", "--input", str(legacy), *connect]
    assert main(argv + (["--dry-run"] if dry_run else [])) == 1
    output = capsys.readouterr()
    assert not output.out
    assert f"FAIL: papersim_optimize_{classification} " in output.err
    assert ("--input-manifest" in output.err) == (classification == "input_manifest_required")


def test_status_reports_registry_fallback_and_zero_runs(monkeypatch):
    from contextlib import contextmanager
    from polymarket_alpha_lab.papersim import cli, store_psycopg

    statements = []

    class Connection:
        @contextmanager
        def transaction(self):
            statements.append("begin")
            yield
            statements.append("end")

        def cursor(self):
            return self

        def execute(self, sql):
            statements.append(sql)

    lineage = "nba-pace-efficiency/totals"
    def role_history(conn):
        assert statements == ["begin", "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"]
        return {}
    monkeypatch.setattr(store_psycopg, "load_all_role_events", role_history)
    monkeypatch.setattr(store_psycopg, "registry_freeze_state",
                        lambda conn: {"global_frozen": False, "frozen_lineages": set()})
    monkeypatch.setattr(store_psycopg, "latest_optimization_for_lineage", lambda conn, key: None)
    report = cli._optimization_status(Connection(), lineage, None)["lineages"][lineage]
    assert report["initialized"] is False
    assert report["last_run"] is None and report["roles"]["active"] is None
    assert report["fallback_spec_sha256"] == report["roles"]["baseline"]
    assert report["pause_state_known"] is False and report["lineage_paused_or_stopped"] is None
    assert statements[-1] == "end"
