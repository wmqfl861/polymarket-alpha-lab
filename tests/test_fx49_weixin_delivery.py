"""Offline FX-49 coverage through the Linux transport's normal entry points."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from threading import Event
import time
from types import SimpleNamespace

import pytest


RUNTIME_SCRIPT = Path(__file__).resolve().parents[1] / "docs/paper-sim/runtime/scripts/weixin_delivery.py"
spec = importlib.util.spec_from_file_location("fx49_weixin_delivery", RUNTIME_SCRIPT)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
START = NOW - timedelta(seconds=30)
DEADLINE = NOW + timedelta(minutes=2)
BODY = 'Fixture 1 \r\n赛前消息 \U0001f3df\n"quotes", \\slashes, `backticks`, $dollars\n'
TARGET = delivery.digest("weixin:synthetic-private-chat")
SHA = delivery.digest("synthetic fixed script")
LOCAL_ID = "1" * 32
WINDOWS_ID = "2" * 32
OTHER_ID = "4" * 32
HOST = "FIXTURE-WINDOWS"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
SCRIPT = r"C:\Paper Delivery\Invoke-PaperWeixin.ps1"


def encoded(value):
    return json.dumps(value, ensure_ascii=True).encode("ascii")


def envelope(code, content, *, timed_out=False, truncated=False):
    return {"code": code, "stdout": base64.b64encode(content).decode("ascii"),
            "stderr": "", "signal": None, "ms": 1, "timed_out": timed_out, "truncated": truncated}


class FakeHc:
    """Model hc 0.1.21's JSON mode and an offline fixed receiver."""

    def __init__(self):
        self.now = NOW
        self.timeline = []
        self.notes = {}
        self.receipts = {}
        self.windows_id = WINDOWS_ID
        self.host = HOST
        self.local_id = LOCAL_ID
        self.local_name = "fixture-linux"
        self.client_version = "0.1.21"
        self.core_version = "0.1.21"
        self.connected = True
        self.machine_exit = 0
        self.other_machines = []
        self.socket_identity = (1, 2)
        self.context = delivery.HcContext("/fixture/hc", "/fixture/worker",
                                          Path("/fixture/worker/context.sock"), self.socket_identity, 1000)
        self.calls = []
        self.targets = ["dm"]
        self.target_digest = TARGET
        self.check_refusal = None
        self.send_refusal = None
        self.send_code = 0
        self.publish_receipt = True
        self.reply_overrides = {}
        self.wrapper_fault = None
        self.delivered_bytes = []
        self.native_calls = 0

    def __call__(self, argv, *, env, stdin, deadline):
        assert argv[0] == self.context.executable
        assert env == {"PATH": os.defpath, "HOME": self.context.home, "HC_SOCKET": str(self.context.socket)}
        assert deadline > time.monotonic()
        args = argv[1:]
        self.calls.append(tuple(args))
        if args == ["--version"]:
            assert stdin is None
            return subprocess.CompletedProcess(argv, 0, f"hc {self.client_version}\n".encode("ascii"))
        if args == ["server", "status", "--json"]:
            assert stdin is None
            status = {"version": self.core_version, "core": {"id": self.local_id, "name": self.local_name}}
            return subprocess.CompletedProcess(argv, 0, encoded(status))
        if args == ["machine", "ls", "--json"]:
            assert stdin is None
            target = {"id": self.windows_id, "name": "test-win", "host": self.host,
                      "state": "connected" if self.connected else "unreachable"}
            return subprocess.CompletedProcess(argv, self.machine_exit, encoded({"machines": [*self.other_machines, target]}))

        assert len(argv) == 15
        assert args[:5] == ["exec", "--json", "--on", WINDOWS_ID, "--timeout"]
        assert args[6:8] == ["-i", "--"]
        assert args[8:-1] == [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", SCRIPT]
        assert all(BODY not in argument and TARGET not in argument for argument in argv)
        nonce = args[-1]
        assert delivery.HEX32.fullmatch(nonce)
        assert len(stdin) <= 20480
        payload = json.loads(stdin)
        purpose = payload.pop("purpose")
        phase_seconds = {"check": 24, "prepare": 20, "receipt": 9, "cleanup": 8}
        assert int(args[5]) == (payload["send_budget_seconds"] if purpose == "send" else phase_seconds[purpose])
        assert payload.pop("hostname") == HOST
        assert payload.pop("target_digest") == TARGET
        assert payload.pop("script_sha256") == SHA
        assert payload.pop("protocol") == "paper-weixin-fixed-v1"
        self.timeline.append((purpose, nonce))
        code, extra = self.endpoint(purpose, nonce, payload)
        report = {"protocol": "paper-weixin-fixed-v1", "purpose": purpose, "code": code, "script_sha256": SHA}
        reply = encoded(report | extra | self.reply_overrides.get(purpose, {})) if code in (0, 32) else b""
        fault = self.wrapper_fault if purpose == "send" else None
        if fault == "nested":
            reply = encoded(envelope(code, reply))
        report = envelope(code, reply, timed_out=fault == "timeout", truncated=fault == "truncated")
        changes = {
            "inconsistent": {"code": 31},
            "boolean_code": {"code": False},
            "out_of_range": {"code": 256},
            "bad_timed_out": {"timed_out": 0},
            "bad_truncated": {"truncated": 0},
            "signal": {"signal": 9},
            "invalid_base64": {"stdout": "$"},
            "oversized_decoded": {"stdout": base64.b64encode(b"x" * 8193).decode("ascii")},
        }
        wire = encoded(report | changes.get(fault, {}))
        if fault == "duplicate_key":
            wire = wire[:-1] + b', "code": 0}'
        elif fault == "incomplete":
            wire = wire[:-1]
        elif fault == "oversized_envelope":
            wire = b" " * delivery.MAX_ENVELOPE_BYTES + wire
        process_exit = {"process_exit": 8, "propagated_code": code}.get(fault, 0)
        return subprocess.CompletedProcess(argv, process_exit, wire)

    def endpoint(self, purpose, nonce, data):
        if purpose == "check":
            assert data == {}
            if self.check_refusal:
                return 32, {"reason": self.check_refusal}
            if self.targets != ["dm"]:
                return 24, {}
            if self.target_digest != TARGET:
                return 25, {}
            return 0, {"hostname_matches": True, "target_verified": True}
        if purpose == "prepare":
            assert set(data) == {"stage_key", "body_variant", "body_digest", "body",
                                 "starts_utc", "cutoff_utc", "deadline_utc"}
            if nonce in self.notes:
                return 29, {}
            body = data.pop("body").encode("utf-8")
            assert delivery.digest(body.decode("utf-8")) == data["body_digest"]
            self.notes[nonce] = (body, data)
            return 0, {"prepared": True}
        if purpose == "send":
            assert set(data) == {"send_budget_seconds"}
            assert type(data["send_budget_seconds"]) is int and 1 <= data["send_budget_seconds"] <= 575
            body, job = self.notes[nonce]
            if self.send_code in (22, 24, 25, 26):
                return self.send_code, {}
            self.native_calls += 1
            self.delivered_bytes.append(body)
            if self.send_refusal:
                self.receipts[("fixed", nonce)] = self.send_refusal
                return 32, {"reason": self.send_refusal}
            if self.publish_receipt:
                self.receipts[("fixed", nonce)] = receipt(job, nonce)
            return self.send_code, {"sent": True}
        if purpose == "receipt":
            assert set(data) == {"receipt_generation"}
            assert data["receipt_generation"] in ("fixed", "legacy")
            stored = self.receipts.get((data["receipt_generation"], nonce))
            if isinstance(stored, str):
                return 32, {"reason": stored}
            if stored is None:
                return 0, {"present": False}
            return 0, {"contents": base64.b64encode(encoded(stored)).decode("ascii")}
        assert purpose == "cleanup" and data == {}
        self.notes.pop(nonce, None)
        return 0, {}


def receipt(job, nonce):
    return {**job, "nonce": nonce, "target_digest": TARGET, "state": "accepted", "platform": "weixin",
            "target_verified": True, "native_success": True, "native_exit_code": 0, "chat_verified": True,
            "message_id_sha256": delivery.digest("synthetic-message"), "send_started_at": NOW.isoformat(),
            "confirmed_at": (NOW + timedelta(seconds=1)).isoformat(),
            "protocol": "paper-weixin-fixed-v1", "script_sha256": SHA}


@pytest.fixture
def hc(monkeypatch):
    stub = FakeHc()
    values = {
        "HC_CONTEXT_SHA256": delivery.digest(LOCAL_ID), "_hc_context": lambda: stub.context,
        "WINDOWS_FINGERPRINT": delivery.digest(WINDOWS_ID), "WINDOWS_HOST": HOST,
        "POWERSHELL_PATH": POWERSHELL, "SCRIPT_PATH": SCRIPT,
        "approved_script_sha256": lambda: SHA, "utcnow": lambda: stub.now,
        "_socket_identity": lambda *_: stub.socket_identity, "_run_hc": stub,
    }
    for name, value in values.items():
        monkeypatch.setattr(delivery, name, value)
    return stub


def launch(on_submit=lambda **_: True, **overrides):
    args = {"stage_key": "fixture-round", "starts_at": START, "deadline": DEADLINE,
            "target_digest": TARGET, "on_submit": on_submit}
    return delivery.deliver(BODY, **(args | overrides))


def recover(attempt, generation="fixed", **overrides):
    args = {"stage_key": "fixture-round", "attempt_key": attempt, "body_variant": "primary",
            "body_digest": delivery.digest(BODY), "target_digest": TARGET, "deadline": DEADLINE,
            "receipt_generation": generation}
    if generation == "fixed":
        args.update(starts_at=START, cutoff_at=DEADLINE, submitted_at=NOW)
    return delivery.recover(**(args | overrides))


def test_success_uses_exact_argv_and_preserves_body_bytes(hc):
    def claim(*, attempt_key, body_variant, body_digest):
        assert body_variant == "primary" and body_digest == delivery.digest(BODY)
        assert hc.notes[attempt_key][0] == BODY.encode("utf-8")
        assert hc.native_calls == 0
        hc.timeline.append(("claim", attempt_key))
        return True

    result = launch(claim)
    assert result["state"] == "accepted"
    nonce = result["attempt_key"]
    assert hc.timeline[1:] == [(op, nonce) for op in ("prepare", "claim", "send", "receipt", "cleanup")]
    assert hc.delivered_bytes == [BODY.encode("utf-8")]
    assert hc.notes == {}
    assert "native_session_verified" not in result and "native_turn_finished" not in result


@pytest.mark.parametrize("field,value", [
    ("windows_id", "0" * 32), ("host", "other-fixture"), ("local_id", "0" * 32),
    ("local_name", WINDOWS_ID), ("client_version", "0.1.22"), ("core_version", "0.1.22"),
    ("connected", False), ("machine_exit", 1),
])
def test_each_route_identity_mismatch_stops_before_windows(hc, field, value):
    setattr(hc, field, value)
    assert not delivery.check_route(TARGET)["route_verified"]
    assert launch()["state"] == "preflight_failed"
    assert hc.timeline == []


@pytest.mark.parametrize("other", [
    {"id": WINDOWS_ID, "name": "duplicate-target"},
    {"id": OTHER_ID, "name": WINDOWS_ID},
])
def test_duplicate_binding_or_selector_stops_before_windows(hc, other):
    hc.other_machines = [other]
    assert not delivery.check_route(TARGET)["route_verified"]
    assert launch()["state"] == "preflight_failed"
    assert hc.timeline == [] and hc.native_calls == 0


def test_route_check_cli_uses_full_id_and_only_the_read_only_windows_check(hc, monkeypatch, capsys):
    hc.other_machines = [{"id": OTHER_ID, "name": WINDOWS_ID[:8], "host": HOST}]
    monkeypatch.setenv("HC_SOCKET", "/fixture/interactive.sock")
    monkeypatch.setenv("HC_SESSION", "interactive-fixture")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/fixture/interactive-config")
    monkeypatch.setattr(delivery.sys, "argv", [str(RUNTIME_SCRIPT), "--route-check"])
    monkeypatch.setattr(delivery.sys, "stdin", io.StringIO(f"{TARGET}\n"))
    assert delivery.main() == 0
    assert json.loads(capsys.readouterr().out) == delivery.ROUTE_VERIFIED
    assert [op for op, _ in hc.timeline] == ["check"] and hc.native_calls == 0


def test_empty_relay_field_cannot_verify_an_unconfigured_direct_route(monkeypatch):
    monkeypatch.setenv("PAL_HERDR_HUB_FINGERPRINT", "")
    monkeypatch.setattr(delivery, "WINDOWS_FINGERPRINT", delivery.digest(WINDOWS_ID))
    monkeypatch.setattr(delivery, "WINDOWS_HOST", HOST)
    monkeypatch.setattr(delivery, "HC_PATH", "/fixture/hc")
    monkeypatch.setattr(delivery, "HC_USER", "fixture-worker")
    monkeypatch.setattr(delivery, "HC_SOCKET", "")
    monkeypatch.setattr(delivery, "HC_CONTEXT_SHA256", "")
    monkeypatch.setattr(delivery, "approved_script_sha256", lambda: SHA)
    monkeypatch.setattr(delivery, "_run_hc", lambda *_a, **_kw: pytest.fail("missing direct context ran hc"))
    assert delivery.check_route(TARGET) == dict.fromkeys(delivery.ROUTE_VERIFIED, False)


def test_context_must_belong_to_the_notification_worker(monkeypatch):
    monkeypatch.setattr(delivery, "HC_PATH", "/fixture/hc")
    monkeypatch.setattr(delivery, "HC_USER", "fixture-worker")
    monkeypatch.setattr(delivery, "HC_SOCKET", "/fixture/interactive.sock")
    monkeypatch.setattr(delivery, "HC_CONTEXT_SHA256", delivery.digest(LOCAL_ID))
    monkeypatch.setattr(delivery.pwd, "getpwuid", lambda _: SimpleNamespace(
        pw_name="fixture-interactive", pw_dir="/fixture/interactive",
    ))
    with pytest.raises(delivery.HandoffError, match="route_context_unverified"):
        delivery._hc_context()


@pytest.mark.parametrize("uid,mode", [(1001, 0o700), (1000, 0o770), (1000, 0o755)])
def test_context_socket_parent_requires_the_workers_private_directory(monkeypatch, uid, mode):
    socket_path = Path("/fixture/worker/context.sock")

    def lstat(path):
        if path == socket_path:
            return SimpleNamespace(st_uid=1000, st_mode=stat.S_IFSOCK | 0o700, st_dev=10, st_ino=20)
        assert path == socket_path.parent
        return SimpleNamespace(st_uid=uid, st_mode=stat.S_IFDIR | mode)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(delivery.HandoffError, match="route_context_unverified"):
        delivery._socket_identity(socket_path, 1000)


@pytest.mark.parametrize("change", ["socket", "selector"])
def test_context_or_selector_change_after_claim_never_sends(hc, change):
    def claim(**_):
        if change == "socket":
            hc.socket_identity = (2, 3)
        else:
            hc.other_machines = [{"id": OTHER_ID, "name": WINDOWS_ID}]
        return True

    result = launch(claim)
    assert result["state"] == "unknown" and result["reason"] == delivery.FIXED_UNCONFIRMED
    assert result["attempt_key"] in hc.notes
    assert [op for op, _ in hc.timeline] == ["check", "prepare"]
    assert hc.native_calls == 0


@pytest.mark.parametrize("targets,target", [([], TARGET), (["dm", "dm"], TARGET), (["group"], TARGET),
                                              (["dm"], "9" * 64)])
def test_private_target_checks_precede_preparation(hc, targets, target):
    hc.targets, hc.target_digest = targets, target
    assert launch()["state"] == "preflight_failed"
    assert [op for op, _ in hc.timeline] == ["check"]
    assert hc.native_calls == 0


@pytest.mark.parametrize("code", [22, 24, 25, 26])
def test_send_rechecks_window_target_and_body(hc, code):
    hc.send_code = code
    assert launch()["fixed_exit_code"] == code
    assert hc.native_calls == 0
    assert not hc.receipts
    assert [op for op, _ in hc.timeline] == ["check", "prepare", "send", "cleanup"]


@pytest.mark.parametrize("reason", sorted(delivery.NATIVE_REFUSALS))
def test_preclaim_and_postclaim_refusals_are_explicit(hc, reason):
    hc.check_refusal = reason
    assert launch() == {"state": "refused", "reason": f"fixed_entry_{reason}"}
    assert [op for op, _ in hc.timeline] == ["check"]
    hc.check_refusal = None
    hc.send_refusal = reason
    result = launch()
    assert result["state"] == "unknown" and result["reason"] == f"fixed_entry_{reason}"
    assert [op for op, _ in hc.timeline] == ["check", "check", "prepare", "send", "cleanup"]
    assert hc.native_calls == 1
    assert recover(result["attempt_key"]) == {"state": "unknown", "reason": f"fixed_entry_{reason}"}
    assert hc.native_calls == 1


def test_window_changes_and_claim_failure_never_send(hc):
    def broken_claim(**_):
        raise RuntimeError("fixture claim failed")

    assert launch(broken_claim)["reason"] == "claim_unconfirmed"
    assert launch(lambda **_: False)["reason"] == "claim_rejected"

    def claim_at_cutoff(**_):
        hc.now = DEADLINE
        return True

    result = launch(claim_at_cutoff)
    assert result["state"] == "unknown" and result["launch_diagnostic"] == "window_changed_after_claim"
    assert launch()["state"] == "expired"
    assert hc.native_calls == 0 and hc.notes == {}
    assert "send" not in [op for op, _ in hc.timeline]


def test_same_nonce_prepare_loser_cannot_send_or_clean_winners_files(hc, monkeypatch):
    monkeypatch.setattr(delivery.secrets, "token_hex", lambda _: "3" * 32)
    entered = Event()
    release = Event()

    def claim(**_):
        entered.set()
        return release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(launch, claim)
        try:
            assert entered.wait(5)
            second = pool.submit(launch)
            assert second.result(timeout=5)["reason"] == "preparation_unconfirmed"
            assert hc.notes
            assert "cleanup" not in [op for op, _ in hc.timeline]
        finally:
            release.set()
        assert first.result(timeout=5)["state"] == "accepted"
    assert hc.native_calls == 1 and hc.notes == {}


@pytest.mark.parametrize("fault", [
    "timeout", "truncated", "nested", "inconsistent", "process_exit", "signal",
    "boolean_code", "out_of_range", "bad_timed_out", "bad_truncated", "invalid_base64",
    "duplicate_key", "incomplete", "oversized_envelope", "oversized_decoded",
])
def test_invalid_send_envelopes_allow_independent_read_only_recovery(hc, fault):
    hc.wrapper_fault = fault
    result = launch()
    assert result["state"] == "unknown"
    assert [op for op, _ in hc.timeline] == ["check", "prepare", "send"]
    assert recover(result["attempt_key"])["state"] == "accepted"
    assert hc.native_calls == 1


def test_hc_0_1_21_explicit_json_mode_rejects_even_the_same_local_nonzero_code(hc):
    hc.send_code = 25
    hc.wrapper_fault = "propagated_code"
    result = launch()
    assert result["state"] == "unknown" and result["fixed_exit_code"] is None
    assert [op for op, _ in hc.timeline] == ["check", "prepare", "send"]
    assert hc.native_calls == 0


def test_sender_keeps_native_limit_and_acknowledgment_phases(hc):
    result = launch(deadline=NOW + timedelta(hours=1))
    assert result["state"] == "accepted"
    phases = [int(call[5]) for call in hc.calls if call[0] == "exec"]
    assert phases == [24, 20, 575, 9, 8]


def test_send_that_returns_without_receipt_stays_unknown(hc):
    hc.publish_receipt = False
    result = launch()
    assert result["state"] == "unknown" and result["receipt_diagnostic"] == "receipt_missing"
    assert recover(result["attempt_key"])["state"] == "unknown"
    assert hc.native_calls == 1


@pytest.mark.parametrize("override", [
    {"unknown": True}, {"sent": "true"}, {"purpose": "prepare"}, {"code": False}, {"script_sha256": "0" * 64},
])
def test_malformed_current_response_cannot_accept_a_valid_receipt(hc, override):
    hc.reply_overrides["send"] = override
    result = launch()
    assert result["state"] == "unknown"
    assert [op for op, _ in hc.timeline] == ["check", "prepare", "send"]
    assert hc.native_calls == 1


def test_unrecognized_refusal_reason_is_not_accepted(hc):
    hc.send_refusal = "unverified_native_error"
    result = launch()
    assert result["state"] == "unknown" and result["reason"] == delivery.UNVERIFIED_REFUSAL
    assert [op for op, _ in hc.timeline] == ["check", "prepare", "send", "cleanup"]
    assert hc.native_calls == 1


@pytest.mark.parametrize("invalid_body", ["contradictory-code", "oversized"])
def test_trusted_refusal_code_with_invalid_windows_body_stays_terminal(hc, invalid_body):
    hc.send_refusal = "hermes_platform_write_denied"
    if invalid_body == "oversized":
        hc.wrapper_fault = "oversized_decoded"
    else:
        hc.reply_overrides["send"] = {"code": 0}
    result = launch()
    assert result["state"] == "unknown" and result["reason"] == delivery.UNVERIFIED_REFUSAL
    assert [op for op, _ in hc.timeline] == ["check", "prepare", "send", "cleanup"]


@pytest.mark.parametrize("field,value", [
    ("protocol", "paper-weixin-fixed-v0"), ("script_sha256", "0" * 64), ("body_digest", "0" * 64),
    ("target_digest", "0" * 64), ("stage_key", "other-round"), ("nonce", "0" * 32),
    ("confirmed_at", DEADLINE.isoformat()), ("send_started_at", (START - timedelta(seconds=1)).isoformat()),
    ("starts_utc", (START - timedelta(seconds=1)).isoformat()), ("native_exit_code", False),
    ("deadline_utc", (DEADLINE + timedelta(seconds=1)).isoformat()), ("chat_verified", 1),
])
def test_recovery_rejects_forged_or_stale_receipts(hc, field, value):
    result = launch()
    nonce = result["attempt_key"]
    hc.receipts[("fixed", nonce)][field] = value
    assert recover(nonce)["state"] == "unknown"
    assert hc.native_calls == 1


def test_legacy_and_fixed_receipts_never_cross(hc):
    result = launch()
    nonce = result["attempt_key"]
    raw = hc.receipts[("fixed", nonce)]
    hc.receipts[("legacy", nonce)] = raw
    assert recover(nonce, "legacy")["state"] == "unknown"
    old = {key: value for key, value in raw.items() if key not in {"protocol", "script_sha256", "deadline_utc"}}
    hc.receipts[("legacy", nonce)] = old
    legacy = recover(nonce, "legacy")
    assert legacy["state"] == "accepted" and "fixed_receipt" not in legacy
    hc.receipts[("fixed", nonce)] = old
    assert recover(nonce)["state"] == "unknown"
    assert hc.native_calls == 1


@pytest.mark.parametrize("powershell,script", [
    ("", ""), ("", SCRIPT), (POWERSHELL, ""), ("powershell.exe", SCRIPT),
    (POWERSHELL, r"C:\Paper\..\Invoke-PaperWeixin.ps1"),
])
def test_protected_paths_are_required_before_any_windows_start(monkeypatch, powershell, script):
    monkeypatch.setattr(delivery, "utcnow", lambda: NOW)
    monkeypatch.setattr(delivery, "POWERSHELL_PATH", powershell)
    monkeypatch.setattr(delivery, "SCRIPT_PATH", script)
    monkeypatch.setattr(delivery.subprocess, "run", lambda *_, **__: pytest.fail("an absent release started a process"))
    assert launch()["reason"] == "fixed_entry_unconfigured"
    assert not delivery.check_route(TARGET)["route_verified"]


def test_approved_manifest_and_blob_come_from_the_deployed_git_commit(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(["/usr/bin/git", "-C", str(tmp_path), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout

    source = tmp_path / delivery.SCRIPT_BLOB
    source.parent.mkdir(parents=True)
    source.write_bytes(b"exit 21\n")
    checksum = delivery.digest("exit 21\n")
    manifest = tmp_path / delivery.CHECKSUM_BLOB
    manifest.write_bytes(f"{checksum}  Invoke-PaperWeixin.ps1\n".encode("ascii"))
    git("init", "--quiet")
    git("add", ".")
    git("-c", "user.name=Offline Test", "-c", "user.email=offline@example.invalid", "commit", "-qm", "fixture")
    approved = git("rev-parse", "HEAD").decode("ascii").strip()
    for field, value in {"RELEASE_ROOT": tmp_path, "RELEASE_COMMIT": approved,
                         "POWERSHELL_PATH": POWERSHELL, "SCRIPT_PATH": SCRIPT}.items():
        monkeypatch.setattr(delivery, field, value)
    assert delivery.approved_script_sha256() == checksum
    source.write_bytes(b"exit 0\n")
    with pytest.raises(delivery.HandoffError, match="fixed_release_unverified"):
        delivery.approved_script_sha256()
    source.write_bytes(b"exit 21\n")
    manifest.write_bytes(b"0" * 64 + b"  Invoke-PaperWeixin.ps1\n")
    with pytest.raises(delivery.HandoffError, match="fixed_release_unverified"):
        delivery.approved_script_sha256()
    monkeypatch.setattr(delivery, "RELEASE_COMMIT", "0" * 40)
    with pytest.raises(delivery.HandoffError, match="fixed_release_unverified"):
        delivery.approved_script_sha256()


def test_local_hc_pipe_bounds_capture_and_keeps_input_bytes(monkeypatch):
    monkeypatch.setattr(delivery, "MAX_ENVELOPE_BYTES", 32)
    command = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"]
    raw = b"first line\r\nsecond line\n"
    env = {"PATH": os.defpath}
    result = delivery._run_hc(command, env=env, stdin=raw, deadline=time.monotonic() + 5)
    assert (result.returncode, result.stdout) == (0, raw)
    assert delivery._run_hc(command, env=env, stdin=b"x" * 33, deadline=time.monotonic() + 5) is None


def test_transport_source_has_only_the_fixed_hc_process_vector():
    source = RUNTIME_SCRIPT.read_text(encoding="utf-8")
    forbidden = ('"herdr-core"', '"zcode"', "hermes-wechat-send", "yolo", "-ExecutionPolicy", "-EncodedCommand")
    assert not any(token in source for token in forbidden)
