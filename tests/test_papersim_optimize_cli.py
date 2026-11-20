"""MO-8 CLI wiring proofs (offline; plan 05 node MO-8).

The parser contract of the six optimize subcommands, the production
run-scan shadow wiring (MO-7 review P3-2) and the freeze/unfreeze
actor rules; the database paths run in the native drill
(test_papersim_optimize_policy_native.py).
"""

import json
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim import cli
from polymarket_alpha_lab.papersim.optimize.shadow import SnapshotShadowSource


class _StubConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_optimize_registers_all_six_subcommands(capsys):
    for command in ("score", "decide", "apply", "rollback", "freeze",
                    "unfreeze"):
        with pytest.raises(SystemExit) as caught:
            cli.main(["optimize", command, "--help"])
        assert caught.value.code == 0
        capsys.readouterr()
    with pytest.raises(SystemExit) as caught:
        cli.main(["optimize", "score", "--help"])
    assert caught.value.code == 0
    assert "--lineage" in capsys.readouterr().out


def test_optimize_requires_a_known_leaf_command(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["optimize"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit) as caught:
        cli.main(["optimize", "nope"])
    assert caught.value.code == 2


def test_decide_requires_config_and_lineage(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["optimize", "decide"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit) as caught:
        cli.main(["optimize", "decide", "--help"])
    assert caught.value.code == 0
    assert "--config" in capsys.readouterr().out


def _scan_config(tmp_path: Path) -> Path:
    (tmp_path / "pack.json").write_text("[]", encoding="utf-8")
    config = tmp_path / "scan.json"
    config.write_text(json.dumps({
        "team": "paper", "operator": "unattended",
        "queues": [{"queue": "q1", "lineage": "crypto/touch",
                    "data_source_version": "v1", "pack": "pack.json"}],
    }), encoding="utf-8")
    return config


def test_run_scan_wires_the_shadow_source_and_budget(tmp_path, monkeypatch,
                                                     capsys):
    captured = {}

    def _fake_run_scan(config, records, **kwargs):
        captured.update(kwargs)
        return {"scan_id": 1}

    from polymarket_alpha_lab.papersim import scan
    from polymarket_alpha_lab.papersim.optimize import provider

    monkeypatch.setattr(scan, "run_scan", _fake_run_scan)
    monkeypatch.setattr(cli, "_open_readonly_connection",
                        lambda args, label: _StubConnection())
    monkeypatch.setattr(cli, "_evaluation_candidates_from_store",
                        lambda conn: ())
    monkeypatch.setattr(
        provider.RegistryProvider, "from_connection",
        classmethod(lambda cls, conn: object()))
    assert cli.main(["run-scan", "--dsn", "postgresql://x",
                     "--config", str(_scan_config(tmp_path)),
                     "--repo-root", str(tmp_path)]) == 0
    assert isinstance(captured["shadow_source"], SnapshotShadowSource)
    assert captured["shadow_budget_seconds"] == cli.RUN_SCAN_SHADOW_BUDGET_SECONDS
    assert cli.RUN_SCAN_SHADOW_BUDGET_SECONDS == 30.0
    # The budget is an explicit override away from the default.
    assert cli.main(["run-scan", "--dsn", "postgresql://x",
                     "--config", str(_scan_config(tmp_path)),
                     "--repo-root", str(tmp_path),
                     "--shadow-budget-seconds", "1.5"]) == 0
    assert captured["shadow_budget_seconds"] == 1.5


def test_unfreeze_rejects_an_automatic_actor_before_any_write(monkeypatch,
                                                              capsys):
    connection = _StubConnection()
    monkeypatch.setattr(cli, "_open_readonly_connection",
                        lambda args, label: connection)
    written = []

    from polymarket_alpha_lab.papersim import store_psycopg

    def _fail_write(conn, submission):
        written.append(submission)
        raise AssertionError("must not be reached")

    monkeypatch.setattr(store_psycopg, "record_optimizer_control_event",
                        _fail_write)
    assert cli.main(["optimize", "unfreeze", "--reason", "resume",
                     "--actor", "auto:promo-rule-v1"]) == 1
    assert "papersim_unfreeze_actor_invalid" in capsys.readouterr().err
    assert not written
    assert connection.closed


def test_freeze_accepts_auto_actor_and_echoes_key_and_id(monkeypatch, capsys):
    connection = _StubConnection()
    monkeypatch.setattr(cli, "_open_readonly_connection",
                        lambda args, label: connection)

    from polymarket_alpha_lab.papersim import store_psycopg

    captured = {}

    def _fake_write(conn, submission):
        submission.validate()
        captured["submission"] = submission
        return {"optimizer_control_id": 7,
                "optimizer_control_key": submission.key(),
                "inserted": True}

    monkeypatch.setattr(store_psycopg, "record_optimizer_control_event",
                        _fake_write)
    assert cli.main(["optimize", "freeze", "--reason", "safety stop",
                     "--actor", "auto:promo-rule-v1",
                     "--occurred-at", "2026-11-20T12:00:00+00:00"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"optimizer_control_id": 7,
                       "optimizer_control_key":
                           captured["submission"].key(),
                       "inserted": True}
    assert payload["optimizer_control_key"].startswith("oce|GLOBAL|FREEZE|")
    assert connection.closed
