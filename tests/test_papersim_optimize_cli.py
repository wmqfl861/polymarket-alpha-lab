"""MO-8 CLI wiring proofs (offline; plan 05 node MO-8).

The parser contract of the six optimize subcommands, the production
run-scan shadow wiring (MO-7 review P3-2) and the freeze/unfreeze
actor rules; the database paths run in the native drill
(test_papersim_optimize_policy_native.py).
"""

import json
from pathlib import Path
import types

import pytest

from polymarket_alpha_lab.papersim import cli
from polymarket_alpha_lab.papersim.optimize.shadow import SnapshotShadowSource


class _StubConnection:
    def __init__(self):
        self.closed = False
        self.locked = False
        self.isolation_checked = False
        self.result = None

    def close(self):
        self.closed = True

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            try:
                yield
            finally:
                self.locked = False
                self.isolation_checked = False
        return transaction()

    def cursor(self):
        from contextlib import nullcontext
        return nullcontext(self)

    def execute(self, query, parameters=None):
        if query == "SHOW transaction_isolation":
            assert parameters is None
            self.isolation_checked = True
            self.result = ("read committed",)
            return
        assert "pg_try_advisory_xact_lock" in query
        assert self.isolation_checked
        self.locked = True
        self.result = (True,)

    def fetchone(self):
        assert self.result is not None
        return self.result


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


# ---------------------------------------------------------------------------
# Review P2 fix: the apply path's every refusal branch, offline.
# ---------------------------------------------------------------------------

class _Conn(_StubConnection):
    pass


def _decision_row(**overrides):
    from datetime import UTC, datetime

    base = {
        "decision_id": 11, "lineage": "club-soccer-frozen/over_under",
        "decision_class": "FORWARD",
        "candidate_spec_sha256": "c" * 64, "active_spec_sha256": "a" * 64,
        "conclusion": "PROMOTE", "look_number": 1,
        "rule_version": "promo-rule-v1",
        "decided_at": datetime(2026, 11, 24, tzinfo=UTC),
        "evidence_watermark": {"spec": "c" * 64, "comparator": "a" * 64},
        "metrics": {},
    }
    base.update(overrides)
    return base


def _facts_state(**fact_overrides):
    from datetime import UTC, datetime

    from polymarket_alpha_lab.papersim.optimize.policy import LineageFacts

    facts = LineageFacts(
        lineage="club-soccer-frozen/over_under",
        active_spec="a" * 64, guard_spec="b" * 64,
        candidate_specs=("c" * 64,),
        candidate_registered_at={"c" * 64: datetime(2026, 11, 2, tzinfo=UTC)},
        **fact_overrides)
    view = types.SimpleNamespace(active="a" * 64, baseline="a" * 64,
                                 candidates=("c" * 64,), guard="b" * 64)
    return {"facts": facts, "view": view,
            "entries": {"total": 0, "by_model_name": {}},
            "active_model_version": "soccer-r23"}


def _rollback_decision_row():
    return _decision_row(decision_class="ROLLBACK", conclusion="ROLLBACK",
                         candidate_spec_sha256="a" * 64,
                         evidence_watermark={"spec": "a" * 64, "comparator": "b" * 64})


def _apply_args(**overrides):
    base = {"command": "optimize", "optimize_command": "apply",
            "decision_id": 11, "actor": None, "confirm": False,
            "config": None, "managed": False, "dsn": None,
            "repo_root": "."}
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _wire_apply(monkeypatch, decision, state, *, historical=()):
    captured = {}
    monkeypatch.setattr(cli, "_open_readonly_connection",
                        lambda args, label: _Conn())
    monkeypatch.setattr(cli, "_optimize_decision_row",
                        lambda conn, decision_id: decision)
    monkeypatch.setattr(cli, "_optimize_lineage_facts",
                        lambda conn, lineage, queue_ids:
                            captured.update(queue_ids=queue_ids) or state)
    monkeypatch.setattr(cli, "require_clean_worktree_path",
                        lambda repo_root: captured.update(repo_root=repo_root) or "d" * 40)

    from polymarket_alpha_lab.papersim import store_psycopg

    def _historical_application(conn, lineage, decision_id):
        assert conn.locked
        captured["role_history_read"] = True
        return historical

    monkeypatch.setattr(store_psycopg, "load_decision_role_events", _historical_application)

    def _capture(conn, batch):
        assert conn.locked
        captured["batch"] = batch
        return {"lineage": "club-soccer-frozen/over_under",
                "role_event_ids": {}, "inserted": True}

    monkeypatch.setattr(store_psycopg, "record_role_events", _capture)
    return captured


def test_apply_refuses_decision_free_conclusions(monkeypatch, capsys):
    for conclusion in ("CONTINUE", "HOLD", "BLOCKED"):
        _wire_apply(monkeypatch, _decision_row(conclusion=conclusion),
                    _facts_state())
        assert cli._cmd_optimize_apply(_apply_args()) == 1
        assert "papersim_optimize_apply_no_events" in capsys.readouterr().err


@pytest.mark.parametrize(("actor", "confirm"), [
    (None, False), ("auto:promo-rule-v1", True), ("auto:promo-rule-v1", False),
    ("operator", True),
])
def test_apply_pending_confirm_is_unavailable_with_any_actor(monkeypatch, capsys, actor, confirm):
    captured = _wire_apply(monkeypatch, _decision_row(conclusion="PROMOTE_PENDING_CONFIRM"),
                           _facts_state())
    assert cli._cmd_optimize_apply(_apply_args(actor=actor, confirm=confirm)) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "optimize apply: FAIL: papersim_optimize_apply_promotion_unavailable\n"
    assert captured == {}


def test_apply_unknown_decision_fails(monkeypatch, capsys):
    _wire_apply(monkeypatch, None, _facts_state())
    assert cli._cmd_optimize_apply(_apply_args()) == 1
    assert "papersim_optimize_decision_unknown" in capsys.readouterr().err


def test_apply_active_changing_requires_config_fail_closed(monkeypatch,
                                                            capsys):
    captured = _wire_apply(monkeypatch, _rollback_decision_row(), _facts_state())
    assert cli._cmd_optimize_apply(_apply_args(config=None)) == 1
    assert "papersim_policy_queue_bindings_missing" in capsys.readouterr().err
    assert captured == {}


def test_apply_refuses_frozen_and_paused_lineages(monkeypatch, capsys,
                                                   tmp_path):
    config = tmp_path / "scan.json"
    config.write_text('{"team":"paper","operator":"unattended",'
                      '"queues":[{"queue":"q1","lineage":'
                      '"club-soccer-frozen/over_under",'
                      '"data_source_version":"v1"}]}', encoding="utf-8")
    frozen = _wire_apply(monkeypatch, _rollback_decision_row(), _facts_state(
        frozen=True))
    assert cli._cmd_optimize_apply(_apply_args(config=str(config))) == 1
    assert "papersim_optimize_apply_frozen" in capsys.readouterr().err
    assert "batch" not in frozen
    paused = _wire_apply(monkeypatch, _rollback_decision_row(), _facts_state(
        lineage_paused_or_stopped=True))
    # The pause re-verification is real: the bindings from --config feed
    # the facts (here asserted through the captured queue_ids).
    assert cli._cmd_optimize_apply(_apply_args(config=str(config))) == 1
    assert "papersim_optimize_apply_paused" in capsys.readouterr().err
    assert paused["queue_ids"] == ["q1"]
    assert "batch" not in paused


def test_apply_without_config_for_retire_only(monkeypatch, capsys):
    captured = _wire_apply(monkeypatch, _decision_row(conclusion="RETIRE"),
                           _facts_state())
    # A RETIRE batch changes no active: no config, no pause check.
    assert cli._cmd_optimize_apply(_apply_args()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["conclusion"] == "RETIRE"
    assert payload["actor"] == "auto:promo-rule-v1"
    event, = captured["batch"].events
    assert (event.role, event.spec_sha256) == ("RETIRED", "c" * 64)


@pytest.mark.parametrize("conclusion", ["PROMOTE", "PROMOTE_PENDING_CONFIRM"])
@pytest.mark.parametrize("previously_applied", [False, True], ids=["fresh", "replay"])
def test_apply_refuses_fresh_and_replayed_promotions(monkeypatch, capsys, tmp_path,
                                                     conclusion, previously_applied):
    state = _facts_state()
    decision = _decision_row(conclusion=conclusion)
    historical = ()
    if previously_applied:
        historical = (
            types.SimpleNamespace(spec_sha256="a" * 64, role="GUARD"),
            types.SimpleNamespace(spec_sha256="c" * 64, role="ACTIVE"),
            types.SimpleNamespace(spec_sha256="b" * 64, role="RETIRED"),
        )
    captured = _wire_apply(monkeypatch, decision, state, historical=historical)
    config = _scan_config(tmp_path)
    assert cli._cmd_optimize_apply(
        _apply_args(config=str(config), actor="operator", confirm=True)) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "optimize apply: FAIL: papersim_optimize_apply_promotion_unavailable\n"
    assert captured == {}
    with pytest.raises(ValueError, match="^papersim_optimize_apply_promotion_unavailable$"):
        cli._decision_roles(decision, state["view"], manual_replay=previously_applied)


def test_score_reports_unknown_pause_state_without_config(monkeypatch,
                                                           capsys):
    report = {"lineage": "club-soccer-frozen/over_under", "pairs": []}

    class ScoreConnection(_Conn):
        def execute(self, query, parameters=None):
            assert parameters is None
            assert query == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            self.isolation_checked = True

    def _score(conn, lineage, queue_ids, *, pause_state_known):
        assert conn.isolation_checked
        report["pause_state_known"] = pause_state_known
        report["lineage_paused_or_stopped"] = (
            False if pause_state_known else None)
        return dict(report)

    monkeypatch.setattr(cli, "_open_readonly_connection",
                        lambda args, label: ScoreConnection())
    monkeypatch.setattr(cli, "_optimize_score_lineage", _score)
    args = types.SimpleNamespace(
        command="optimize", optimize_command="score",
        lineage="club-soccer-frozen/over_under", config=None,
        managed=False, dsn=None, repo_root=".")
    assert cli._cmd_optimize_score(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pause_state_known"] is False
    assert payload["lineage_paused_or_stopped"] is None


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
