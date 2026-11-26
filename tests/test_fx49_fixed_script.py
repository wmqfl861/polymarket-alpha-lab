"""Exercise the unchanged fixed script with an isolated, local Hermes substitute."""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "docs/paper-sim/runtime/scripts/windows/Invoke-PaperWeixin.ps1"
MANIFEST = SOURCE.with_suffix(".ps1.sha256")
PROTOCOL = "paper-weixin-fixed-v1"
NONCE = "a" * 32
DM = 'fixture-"dm"'
MESSAGE = "fixture-message-id"
BODY = "Synthetic header\r\n第二行 🧪\nQuotes: \" ' $ % \x60 \\\n"
REFUSALS = {
    "PLATFORM_DENIED": "hermes_platform_write_denied",
    "TOOL_DENIED": "zcode_tool_permission_denied",
    "OWNER_REPLY_REQUIRED": "weixin_owner_message_required",
}

HERMES_SUBSTITUTE = r"""
from pathlib import Path
import json
import sys

root = Path(__file__).parent
fixture = json.loads((root / "fixture.json").read_bytes())
argv = sys.argv[1:]
if argv == ["send", "--list", "weixin", "--json"]:
    if "list_refusal" in fixture:
        print(json.dumps({"success": False, "error_code": fixture["list_refusal"]}))
        sys.exit(7)
    print(json.dumps({"platforms": {"weixin": fixture["targets"]}}))
    sys.exit(0)

with (root / "send-count").open("ab") as calls:
    calls.write(b"1\n")
if argv != ["send", "--to", "weixin:" + fixture["dm"], "--file", fixture["note"], "--json"]:
    print("unexpected fixture argv", file=sys.stderr)
    sys.exit(8)
if Path(argv[4]).read_bytes() != fixture["body"].encode("utf-8"):
    print("incomplete fixture note", file=sys.stderr)
    sys.exit(9)

print("native stderr privacy sentinel", file=sys.stderr)
if "send_error" in fixture:
    print(json.dumps({"success": False, "error_code": fixture["send_error"], "detail": fixture["body"]}))
    sys.exit(7)
accepted = {
    "success": True,
    "platform": "weixin",
    "chat_id": fixture.get("chat", "weixin:" + fixture["dm"]),
    "message_id": fixture["message"],
    "detail": fixture["body"],
}
if fixture.get("duplicate_success"):
    print("{\"success\":false," + json.dumps(accepted)[1:])
else:
    print(json.dumps(accepted))
"""


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def encode_json(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def test_manifest_pins_ascii_lf_checkout_bytes():
    data = SOURCE.read_bytes()
    assert data.isascii()
    assert b"\r" not in data
    assert data.endswith(b"\n")
    assert MANIFEST.read_bytes() == f"{sha256(data)}  {SOURCE.name}\n".encode("ascii")
    repo = SOURCE.parents[5]
    attributes = (repo / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert f"{SOURCE.relative_to(repo)} -text" in attributes
    binding_template = json.loads(SOURCE.with_name("weixin-binding.example.json").read_bytes())
    assert set(binding_template) == {"hermes_path", "target_digest"}
    assert binding_template["target_digest"].startswith("REPLACE_")


def test_windows_native_parser_loads_full_framework_assembly_identity():
    source = SOURCE.read_text(encoding="ascii")
    native_reader = source.split("function Read-NativeObject", 1)[1].split("function Get-NativeMember", 1)[0]
    windows_loader = native_reader.split("if ($OnWindows) {", 1)[1].split("}", 1)[0]
    commands = [line.strip() for line in windows_loader.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert commands == [
        "$framework = [object].Assembly.GetName()",
        "$serialization = [Reflection.AssemblyName]::new('System.Runtime.Serialization')",
        "$serialization.Version = $framework.Version",
        "$serialization.CultureInfo = [Globalization.CultureInfo]::InvariantCulture",
        "$serialization.SetPublicKeyToken($framework.GetPublicKeyToken())",
        "$null = [Reflection.Assembly]::Load($serialization)",
    ]


def test_window_guard_is_inclusive_at_start_and_exclusive_at_cutoff():
    source = SOURCE.read_text(encoding="ascii")
    assert "$Stamp -lt $Window.starts -or $Stamp -ge $Window.cutoff -or" in source
    assert "$confirmed -lt $sent.started -or $confirmed -ge $window.cutoff" in source


@dataclass
class Entry:
    shell: str
    script: Path
    env: dict
    job: Path
    legacy: Path
    fixture: Path
    calls: Path
    fields: dict

    def invoke(self, purpose, code=0, *, overrides=None, raw=None, argv_tail=()):
        request = {**self.fields, "purpose": purpose}
        if purpose == "send":
            request["send_budget_seconds"] = 12
        elif purpose == "receipt":
            request["receipt_generation"] = "fixed"
        request.update(overrides or {})
        completed = subprocess.run(
            [self.shell, "-NoProfile", "-NonInteractive", "-File", str(self.script), NONCE, *argv_tail],
            input=encode_json(request) if raw is None else raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=35,
            env=self.env,
            cwd=self.script.parent,
            check=False,
        )
        assert completed.returncode == code
        assert completed.stderr == b""
        for private in (DM, MESSAGE, "native stderr privacy sentinel", BODY):
            assert private.encode("utf-8") not in completed.stdout
        if code not in (0, 32):
            assert completed.stdout == b""
            return {}
        reply = json.loads(completed.stdout)
        assert reply.items() >= {
            "protocol": PROTOCOL, "purpose": purpose, "code": code, "script_sha256": self.fields["script_sha256"],
        }.items()
        return reply

    def prepare(self, body=BODY):
        now = datetime.now(UTC)
        fields = {
            "stage_key": "fixture.stage",
            "body_variant": "primary",
            "body_digest": sha256(body.encode("utf-8")),
            "body": body,
            "starts_utc": (now - timedelta(seconds=20)).isoformat(),
            "cutoff_utc": (now + timedelta(minutes=2)).isoformat(),
            "deadline_utc": (now + timedelta(minutes=3)).isoformat(),
        }
        assert self.invoke("prepare", overrides=fields)["prepared"] is True
        assert (self.job / "note.txt").read_bytes() == body.encode("utf-8")
        return fields

    def alter_substitute(self, **changes):
        data = json.loads(self.fixture.read_bytes())
        self.fixture.write_bytes(encode_json({**data, **changes}))

    @property
    def send_count(self):
        return len(self.calls.read_bytes().splitlines()) if self.calls.exists() else 0


@pytest.fixture
def linux_powershell():
    shell = shutil.which("pwsh") if sys.platform.startswith("linux") else None
    if shell is None:
        pytest.skip("optional Linux script check requires a pre-existing pwsh")
    return shell


@pytest.fixture
def entry(tmp_path, linux_powershell):
    shell = linux_powershell
    installed = tmp_path / "protected install"
    profile = tmp_path / "local profile"
    temp = tmp_path / "legacy temp"
    installed.mkdir(mode=0o700)
    profile.mkdir(mode=0o700)
    temp.mkdir(mode=0o700)

    script = installed / SOURCE.name
    script.write_bytes(SOURCE.read_bytes())
    hermes = installed / "hermes.exe"
    hermes.write_text(f"#!{sys.executable}\n{HERMES_SUBSTITUTE}", encoding="ascii")
    hermes.chmod(0o700)
    job = profile / "PolymarketAlphaLab" / "WeixinDelivery" / "jobs" / NONCE
    target_digest = sha256(f"weixin:{DM}".encode("utf-8"))
    (installed / "weixin-binding.json").write_bytes(
        encode_json({"hermes_path": str(hermes), "target_digest": target_digest}),
    )
    fixture_path = installed / "fixture.json"
    fixture_path.write_bytes(encode_json({
        "targets": [{"id": DM, "type": "dm"}], "dm": DM, "note": str(job / "note.txt"),
        "body": BODY, "message": MESSAGE,
    }))
    # A fresh profile and explicit local paths keep both generations and the
    # substitute entirely inside this pytest directory.
    env = {
        "LOCALAPPDATA": str(profile),
        "TMPDIR": str(temp),
        "HOME": str(profile),
        "XDG_DATA_HOME": str(profile),
        "PATH": os.defpath,
        "POWERSHELL_TELEMETRY_OPTOUT": "1",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    }
    common = {
        "protocol": PROTOCOL,
        "hostname": socket.gethostname(),
        "script_sha256": sha256(script.read_bytes()),
        "target_digest": target_digest,
    }
    return Entry(shell, script, env, job, temp / f"paper-football-wx-{NONCE}",
                 fixture_path, installed / "send-count", common)


def test_success_writes_one_bound_receipt_and_keeps_it_after_cleanup(entry):
    route_check = entry.invoke("check")
    assert route_check["hostname_matches"] is True
    assert route_check["target_verified"] is True
    expected = entry.prepare()
    assert entry.invoke("send")["sent"] is True
    assert entry.send_count == 1

    result = entry.invoke("receipt")
    raw = base64.b64decode(result["contents"], validate=True)
    assert raw == (entry.job / "receipt.json").read_bytes()
    receipt = json.loads(raw)
    assert set(receipt) == {
        "protocol", "script_sha256", "state", "platform", "target_verified", "native_success", "chat_verified",
        "native_exit_code", "message_id_sha256", "send_started_at", "confirmed_at", "stage_key", "nonce",
        "target_digest", "body_variant", "body_digest", "starts_utc", "cutoff_utc", "deadline_utc",
    }
    assert receipt.items() >= {
        "protocol": PROTOCOL, "nonce": NONCE, "script_sha256": entry.fields["script_sha256"],
        "target_digest": entry.fields["target_digest"], "state": "accepted", "platform": "weixin",
        "target_verified": True, "native_success": True, "chat_verified": True, "native_exit_code": 0,
        "message_id_sha256": sha256(MESSAGE.encode("utf-8")),
    }.items()
    assert all(receipt[key] == value for key, value in expected.items() if key != "body")
    starts, started, confirmed, cutoff, deadline = (
        datetime.fromisoformat(receipt[key])
        for key in ("starts_utc", "send_started_at", "confirmed_at", "cutoff_utc", "deadline_utc")
    )
    assert starts <= started <= confirmed < cutoff <= deadline

    entry.invoke("cleanup")
    assert not (entry.job / "note.txt").exists()
    assert not (entry.job / "job.json").exists()
    assert (entry.job / "send.claimed").is_file()
    assert entry.invoke("receipt") == result
    entry.invoke("send", 29)
    assert entry.send_count == 1


@pytest.mark.parametrize("targets", [
    [],
    [{"id": DM, "type": "dm"}, {"id": "other-fixture", "type": "dm"}],
    [{"id": DM, "type": "group"}],
], ids=["zero", "multiple", "group"])
def test_nonunique_or_group_cannot_send(entry, targets):
    entry.prepare()
    entry.alter_substitute(targets=targets)
    entry.invoke("check", 24)
    entry.invoke("send", 24)
    assert entry.send_count == 0
    assert entry.invoke("receipt")["present"] is False


def test_valid_dm_with_wrong_digest_cannot_change_binding(entry):
    entry.prepare()
    entry.alter_substitute(targets=[{"id": "different-fixture-dm", "type": "dm"}])
    entry.invoke("check", 25)
    entry.invoke("send", 25)
    assert entry.send_count == 0
    assert entry.invoke("receipt")["present"] is False


def test_changed_note_is_rejected_using_actual_file_bytes(entry):
    entry.prepare()
    (entry.job / "note.txt").write_bytes(b"changed synthetic note\n")
    entry.invoke("send", 26)
    assert entry.send_count == 0
    assert entry.invoke("receipt")["present"] is False


def test_closed_window_cannot_send(entry):
    entry.prepare()
    metadata = entry.job / "job.json"
    prepared = json.loads(metadata.read_bytes())
    # The launch happens at or after this mocked job's cutoff. The strict
    # equality boundary is also pinned by the separate static guard check.
    prepared["cutoff_utc"] = datetime.now(UTC).isoformat()
    metadata.write_bytes(encode_json(prepared))
    entry.invoke("send", 22)
    assert entry.send_count == 0
    assert entry.invoke("receipt")["present"] is False


@pytest.mark.parametrize("native_code", tuple(REFUSALS))
def test_structured_refusal_is_durable_and_never_retried(entry, native_code):
    entry.prepare()
    entry.alter_substitute(send_error=native_code)
    assert entry.invoke("send", 32)["reason"] == REFUSALS[native_code]
    assert entry.send_count == 1
    assert not (entry.job / "receipt.json").exists()
    entry.invoke("cleanup")
    assert entry.invoke("receipt", 32)["reason"] == REFUSALS[native_code]
    entry.invoke("send", 29)
    assert entry.send_count == 1


@pytest.mark.parametrize("changes", [
    {"send_error": "SOME_NEW_REFUSAL"},
    {"chat": f"weixin:weixin:{DM}"},
    {"chat": "weixin:synthetic-other-chat"},
    {"duplicate_success": True},
], ids=["unknown-code", "double-prefix", "wrong-chat", "duplicate-success"])
def test_ambiguous_native_output_remains_unconfirmed(entry, changes):
    entry.prepare()
    entry.alter_substitute(**changes)
    entry.invoke("send", 31)
    assert entry.send_count == 1
    assert entry.invoke("receipt")["present"] is False
    entry.invoke("send", 29)
    assert entry.send_count == 1


def test_unsolicited_fields_duplicate_fields_and_amount_of_argv_are_rejected(entry):
    entry.invoke("check", 21, overrides={"command": "fixture"})
    entry.invoke("check", 21, argv_tail=("extra",))
    duplicate = encode_json({**entry.fields, "purpose": "check"})[:-1] + b',"purpose":"check"}'
    entry.invoke("check", 21, raw=duplicate)
    entry.invoke("check", 21, overrides={"protocol": True})
    assert entry.send_count == 0


def test_prepare_is_exclusive_and_rejects_reparsed_body(entry):
    fields = entry.prepare()
    entry.invoke("prepare", 29, overrides=fields)
    note = entry.job / "note.txt"
    substitute = note.with_name("synthetic-alternate.txt")
    substitute.write_bytes(note.read_bytes())
    note.unlink()
    note.symlink_to(substitute)
    entry.invoke("send", 27)
    assert entry.send_count == 0


def test_fixed_receipt_requires_success_publication_and_legacy_paths_do_not_mix(entry):
    entry.prepare()
    (entry.job / "receipt.json").write_bytes(b"{}")
    entry.invoke("send", 29)
    assert entry.send_count == 0
    assert entry.invoke("receipt")["present"] is False

    legacy = {
        "stage_key": "fixture.old",
        "nonce": NONCE,
        "target_digest": entry.fields["target_digest"],
        "body_variant": "primary",
        "body_digest": sha256(b"fixture historical body"),
        "cutoff_utc": "2026-01-01T00:20:00+00:00",
        "state": "accepted",
        "platform": "weixin",
        "target_verified": True,
        "native_success": True,
        "chat_verified": True,
        "native_exit_code": 0,
        "message_id_sha256": sha256(MESSAGE.encode("utf-8")),
        "send_started_at": "2026-01-01T00:10:00.0000000Z",
        "confirmed_at": "2026-01-01T00:11:00.0000000Z",
    }
    old_read = {"receipt_generation": "legacy"}
    assert entry.invoke("receipt", overrides=old_read)["present"] is False
    entry.legacy.mkdir(mode=0o700)
    (entry.legacy / "receipt.json").write_bytes(encode_json({**legacy, "untrusted": {"opaque": BODY}}))
    old_reply = entry.invoke("receipt", overrides=old_read)
    assert json.loads(base64.b64decode(old_reply["contents"], validate=True)) == legacy
    assert entry.invoke("receipt")["present"] is False


def test_offline_rehearsal_verifies_the_refusal_that_survives_cleanup(entry, tmp_path):
    runner = SOURCE.parent / "offline-acceptance" / "Test-PaperWeixinOffline.ps1"
    check = Path(__file__).with_name("fx49_offline_refusal_check.ps1")
    result = subprocess.run(
        [entry.shell, "-NoProfile", "-NonInteractive", "-File", str(check), str(runner), str(tmp_path)],
        cwd=tmp_path, env=entry.env, text=True, capture_output=True, check=True, timeout=30,
    )
    assert not result.stderr
    cases = json.loads(result.stdout)
    assert {name: (case["passed"], case["reads"], case["calls"]) for name, case in cases.items()} == {
        "intact": (True, 2, 1),
        "wrong-first-read": (False, 1, 1),
        "wrong-retained-read": (False, 2, 1),
        "retained-array": (False, 2, 1),
        "send-array": (False, 1, 1),
        "code-as-string": (False, 2, 1),
        "protocol-array": (False, 2, 1),
        "purpose-array": (False, 2, 1),
        "digest-array": (False, 2, 1),
        "extra-field": (False, 2, 1),
        "wrong-retry-code": (False, 1, 1),
        "retry-with-output": (False, 1, 1),
        "sent-twice": (False, 2, 2),
    }


def test_offline_rehearsal_preserves_accepted_receipt_wire_types(linux_powershell, tmp_path):
    runner = SOURCE.parent / "offline-acceptance" / "Test-PaperWeixinOffline.ps1"
    check = Path(__file__).with_name("fx49_offline_acceptance_check.ps1")
    result = subprocess.run(
        [linux_powershell, "-NoProfile", "-NonInteractive", "-File", str(check), str(runner), str(tmp_path)],
        cwd=tmp_path, capture_output=True, check=True, timeout=20,
        env={"HOME": str(tmp_path), "PATH": os.defpath, "POWERSHELL_TELEMETRY_OPTOUT": "1"},
    )
    states = json.loads(result.stdout)
    assert states.pop("intact") == "accepted"
    invalid = {
        "number-string", "bool-number", "state-array", "stage-array", "duplicate-state",
        "fractional-exit", "timestamp-shape", "cutoff-equality", "body-mismatch",
        "contents-array", "file-mismatch", "missing-flag", "integer-flag", "false-flag",
    }
    assert states == {case: "invalid" for case in invalid}
