"""Deliver one claimed reminder through the verified native Weixin route."""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta


CORE = os.environ.get("PAL_HERDR_CORE", "")
HUB_FINGERPRINT = os.environ.get("PAL_HERDR_HUB_FINGERPRINT", "")
WINDOWS_FINGERPRINT = os.environ.get("PAL_HERDR_WINDOWS_FINGERPRINT", "")
WINDOWS_HOST = os.environ.get("PAL_WINDOWS_HOST", "")
HEX64 = re.compile(r"[a-f0-9]{64}")
HEX32 = re.compile(r"[a-f0-9]{32}")
STAGE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
MACHINE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
SESSION_ID = re.compile(r"sess_[A-Za-z0-9-]{1,96}")
MAX_BODY_CHARS = 1500
MAX_PAPER_WINDOW = timedelta(minutes=5)
WINDOWS_GUARD = 3
TRANSIT_MARGIN = 12
HUB_TIMEOUT_PADDING = 25
CLIENT_TIMEOUT_PADDING = 5
RECEIPT_SECONDS = 12
CLEANUP_SECONDS = 9
POST_LAUNCH_RESERVE = RECEIPT_SECONDS + CLEANUP_SECONDS + 1
MIN_PRIMARY_SECONDS = 45
UPLOAD_ALLOWANCE = 20
# Allow the original native session to finish within Herdr's 600-second hub limit.
NATIVE_MAX = 575
TOTAL_SECONDS = 660

ROUTE_VERIFIED = {"route_verified": True, "parser_verified": True, "weixin_target_verified": True}
LAUNCH_REPORTS = {
    "RECEIPT_WRITTEN": "zcode_reported_receipt_written",
    "SEND_REPORTED": "native_send_reported",
    "TOOL_DENIED": "zcode_tool_permission_denied",
    "PLATFORM_DENIED": "hermes_platform_write_denied",
    "OWNER_REPLY_REQUIRED": "weixin_owner_message_required",
    "SKILL_UNAVAILABLE": "zcode_skill_unavailable",
    "TARGET_INVALID": "native_target_unverified",
    "WINDOW_CLOSED": "native_window_closed",
    "NATIVE_FAILED": "native_command_failed",
    "UNSPECIFIED": "zcode_unspecified",
}
REFUSALS = {"zcode_tool_permission_denied", "hermes_platform_write_denied"}

# The Windows process receives only JSON metadata and an opaque UTF-8 note.
UPLOAD_DATA = r"""
$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
try {
    if ($env:COMPUTERNAME -ine '@PAL_WINDOWS_HOST@') { exit 21 }
    $job = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($job.nonce -cnotmatch '^[a-f0-9]{32}$' -or $job.body_digest -cnotmatch '^[a-f0-9]{64}$' `
        -or $job.body -isnot [string] -or !$job.body -or $job.metadata -isnot [string]) { exit 21 }
    $root = [IO.Path]::GetTempPath()
    $dir = Join-Path $root ('paper-football-wx-' + $job.nonce)
    if (Test-Path -LiteralPath $dir) { exit 21 }
    [IO.Directory]::CreateDirectory($dir) >$null
    $note = Join-Path $dir 'note.txt'
    [IO.File]::WriteAllText($note, $job.body, $utf8)
    if ((Get-FileHash -LiteralPath $note -Algorithm SHA256).Hash.ToLowerInvariant() -cne $job.body_digest) { exit 21 }
    [IO.File]::WriteAllText((Join-Path $dir 'job.json'), $job.metadata, $utf8)
    try {
        $stale = [DateTime]::UtcNow.AddHours(-24)
        $oldJobs = Get-ChildItem -LiteralPath $root -Directory -Filter 'paper-football-wx-*' -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -cmatch '^paper-football-wx-[a-f0-9]{32}$' -and $_.CreationTimeUtc -lt $stale `
                -and !($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Select-Object -First 24
        foreach ($oldJob in $oldJobs) {
            foreach ($name in @('note.txt', 'job.json', 'receipt.json')) {
                [IO.File]::Delete((Join-Path $oldJob.FullName $name))
            }
            [IO.Directory]::Delete($oldJob.FullName, $false)
        }
    } catch {}
    [Console]::Write('{"prepared":true}')
} catch { exit 28 }
"""

ROUTE_CHECK = r"""
$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
try {
    if ($env:COMPUTERNAME -ine '@PAL_WINDOWS_HOST@') { exit 21 }
    $bundle = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseInput($bundle.launcher, [ref]$tokens, [ref]$parseErrors) >$null
    if ($parseErrors.Count -ne 0) { exit 28 }
    Get-Command zcode.cmd -CommandType Application >$null
    Get-Command hermes.exe -CommandType Application >$null
    $listingText = & hermes.exe send --list weixin --json 2>$null
    if ($LASTEXITCODE -ne 0) { exit 23 }
    $listing = ($listingText -join [Environment]::NewLine) | ConvertFrom-Json
    $targets = @($listing.platforms.weixin)
    if ($targets.Count -ne 1 -or $targets[0].type -cne 'dm') { exit 24 }
    $id = $targets[0].id
    if ($id -isnot [string] -or !$id -or $id -match '[:\s\p{Cc}]') { exit 24 }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes('weixin:' + $id))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($hash -cne $bundle.target_digest) { exit 25 }
    [Console]::Write('{"route_verified":true,"parser_verified":true,"weixin_target_verified":true}')
} catch { exit 28 }
"""


class HandoffError(Exception):
    """A fixed diagnostic safe to return without route or account data."""


def utcnow():
    return datetime.now(UTC)


def as_utc(stamp):
    if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return stamp.astimezone(UTC)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def valid_body(body):
    if not isinstance(body, str) or not 1 <= len(body) <= MAX_BODY_CHARS or "\x00" in body or not body.strip():
        return False
    try:
        body.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def native_budget(cutoff, now):
    remaining = math.floor((cutoff - now).total_seconds()) - WINDOWS_GUARD - TRANSIT_MARGIN
    return max(0, min(NATIVE_MAX, remaining))


def envelope_timeout(record):
    # Herdr wraps the hub command and then that command's Windows child.
    for _ in range(2):
        if not isinstance(record, dict) or "stdout" not in record:
            return False
        if record.get("timed_out") is True:
            return True
        try:
            record = json.loads(base64.b64decode(record["stdout"], validate=True))
        except (TypeError, ValueError):
            return False
    return False


def core_json(args, until, *, stdin=None, limit=55, timeout_report=None):
    seconds = min(limit, until - time.monotonic())
    if seconds <= 0:
        return None
    try:
        result = subprocess.run(
            [CORE, *args], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=seconds, check=False,
        )
        if len(result.stdout) <= 1024 * 1024 and (result.returncode == 0 or timeout_report is not None):
            value = json.loads(result.stdout)
            if timeout_report is not None and envelope_timeout(value):
                timeout_report.append(True)
            return value if result.returncode == 0 and isinstance(value, dict) else None
    except subprocess.TimeoutExpired:
        if timeout_report is not None:
            timeout_report.append(True)
    except (OSError, ValueError):
        return None
    return None


def exec_result(record):
    if not isinstance(record, dict):
        return None
    if "stdout" not in record:
        return record
    if record.get("code") != 0 or record.get("timed_out") or record.get("truncated"):
        return None
    try:
        value = json.loads(base64.b64decode(record["stdout"], validate=True))
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        return None


def on_hub(hub, args, until, *, stdin=None, seconds=50, timeout_report=None):
    flags = ["-i"] if stdin is not None else []
    return exec_result(core_json(
        ["exec", "--json", "--on", hub, "--timeout", str(seconds), *flags, "--", "herdr-core", *args],
        until, stdin=stdin, limit=seconds + CLIENT_TIMEOUT_PADDING, timeout_report=timeout_report,
    ))


def on_windows(hub, windows, source, until, *, stdin=None, seconds=12, timeout_report=None):
    if not matches(MACHINE_NAME, WINDOWS_HOST):
        return None
    source = source.replace("@PAL_WINDOWS_HOST@", WINDOWS_HOST)
    flags = ["-i"] if stdin is not None else []
    return exec_result(on_hub(
        hub, ["exec", "--json", "--on", windows, "--timeout", str(seconds), *flags, "--",
              "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", source],
        until, stdin=stdin, seconds=seconds + HUB_TIMEOUT_PADDING, timeout_report=timeout_report,
    ))


def machine_name(record, fingerprint, host=None):
    rows = record.get("machines") if isinstance(record, dict) else None
    if not isinstance(rows, list):
        raise HandoffError("route_unavailable")
    hits = [row for row in rows if isinstance(row, dict) and matches(HEX32, row.get("id"))
            and digest(row["id"]) == fingerprint]
    if len(hits) != 1:
        raise HandoffError("route_unavailable")
    name = hits[0].get("name")
    if not matches(MACHINE_NAME, name) or (host and str(hits[0].get("host", "")).casefold() != host.casefold()):
        raise HandoffError("route_identity_mismatch")
    return name


def route(until):
    if (
        not os.path.isabs(CORE)
        or not matches(HEX64, HUB_FINGERPRINT)
        or not matches(HEX64, WINDOWS_FINGERPRINT)
        or not matches(MACHINE_NAME, WINDOWS_HOST)
    ):
        raise HandoffError("route_unavailable")
    hub = machine_name(core_json(["machine", "ls", "--json"], until), HUB_FINGERPRINT)
    windows = machine_name(on_hub(hub, ["machine", "ls", "--json"], until), WINDOWS_FINGERPRINT, WINDOWS_HOST)
    return hub, windows


def launcher(job, seconds, *, resume=False):
    prompt = (
        "Use the same stock hermes-wechat-send procedure that succeeded before for one owner-approved Weixin DM. "
        f"Read job.json in paper-football-wx-{job['nonce']} under Windows GetTempPath. Use hermes.exe send --list "
        "weixin --json to confirm exactly one DM. Verify its unprefixed string ID has no colon, whitespace or control "
        "characters, SHA-256 of UTF-8 weixin:<id> matches target_digest, and the note.txt hash matches body_digest. "
        "Capture send_started_at in Windows UTC immediately before sending and require starts_utc <= send_started_at < cutoff_utc. "
        "Run hermes.exe send exactly once with --to weixin:<id>, --file <full path to note.txt>, and --json. "
        "The --file argument is the complete message source and must be used in place of a positional message. "
        "Do not use --subject, do not send only the first line or heading, and do not reconstruct or shorten the note. "
        "The CLI itself reads the complete UTF-8 note, including all newline-separated match details, through --file. "
        "Keep the note text, IDs and raw native output private. Stop on denial or error. "
        "Report SEND_REPORTED on exit 0, top-level success true and a returned message_id if no receipt is available. "
        "Optionally after that success, if native platform is weixin and chat_id matches the raw or once-prefixed "
        "verified DM, write UTF-8 receipt.json in the job folder. Copy stage_key, nonce, target_digest, body_variant, "
        "body_digest, cutoff_utc from job.json. Include state accepted, platform weixin, target_verified true, "
        "native_exit_code 0, native_success true, chat_verified true, lowercase UTF-8 SHA-256 of message_id as "
        "message_id_sha256, and UTC ISO 8601 send_started_at and confirmed_at, both before cutoff_utc. "
        "Report RECEIPT_WRITTEN only if it was written. Never retry or switch tools or recipients. Reply exactly one "
        "FOOTBALL_NOTIFY:<token> line. Other tokens are TOOL_DENIED for explicit ZCode tool denial, PLATFORM_DENIED for "
        "explicit Hermes write denial, OWNER_REPLY_REQUIRED if Weixin explicitly says the user must send the bot "
        "a message first, SKILL_UNAVAILABLE, TARGET_INVALID, WINDOW_CLOSED, NATIVE_FAILED and UNSPECIFIED."
    ).replace("'", "''")
    session_input = r"""
$trustedSession = [Console]::In.ReadToEnd()
if ($trustedSession -cnotmatch '\Asess_[A-Za-z0-9-]{1,96}\z') { exit 21 }
""" if resume else ""
    resume_argument = "--resume $trustedSession " if resume else ""
    return f"""
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ine '{WINDOWS_HOST}') {{ exit 21 }}
{session_input}
$starts = [DateTimeOffset]::Parse('{job['starts_utc']}')
$cutoff = [DateTimeOffset]::Parse('{job['cutoff_utc']}')
$now = [DateTimeOffset]::UtcNow
if ($now -lt $starts -or $now.AddSeconds({seconds + WINDOWS_GUARD}) -ge $cutoff) {{ exit 22 }}
$paperFootballTask = '{prompt}'
& zcode.cmd {resume_argument}--mode yolo --workflow-mode disabled --toolsearch-mode off --no-browser --no-color --json --prompt $paperFootballTask
exit $LASTEXITCODE
"""


def verify_route(hub, windows, target_digest, until, *, resume=False):
    now = utcnow()
    sample = {"nonce": "0" * 32, "starts_utc": now.isoformat(),
              "cutoff_utc": (now + timedelta(minutes=2)).isoformat()}
    bundle = json.dumps({"target_digest": target_digest, "launcher": launcher(sample, 1, resume=resume)}).encode("ascii")
    return on_windows(hub, windows, ROUTE_CHECK, until, stdin=bundle, seconds=24) == ROUTE_VERIFIED


def check_route(target_digest):
    """Verify the pinned route and Windows launch syntax using a read-only listing."""
    if matches(HEX64, target_digest):
        try:
            until = time.monotonic() + 55
            hub, windows = route(until)
            if verify_route(hub, windows, target_digest, until):
                return ROUTE_VERIFIED.copy()
        except HandoffError:
            pass
    return dict.fromkeys(ROUTE_VERIFIED, False)


def upload_job(hub, windows, job, until):
    metadata = {field: value for field, value in job.items() if field != "body"}
    bundle = json.dumps({"nonce": job["nonce"], "body_digest": job["body_digest"],
                         "body": job["body"], "metadata": json.dumps(metadata)}).encode("ascii")
    return on_windows(hub, windows, UPLOAD_DATA, until, stdin=bundle, seconds=20) == {"prepared": True}


def launch_status(result):
    if not isinstance(result, dict) or not matches(SESSION_ID, result.get("sessionId")):
        return "launch_unconfirmed"
    text = result.get("response")
    if not isinstance(text, str) or len(text) > 80:
        return "zcode_unspecified"
    prefix, _, status = text.strip().partition(":")
    return LAUNCH_REPORTS.get(status, "zcode_unspecified") if prefix == "FOOTBALL_NOTIFY" else "zcode_unspecified"


def run_zcode(hub, windows, job, seconds, until, *, native_session):
    timeouts = []
    resume = native_session is not None
    stdin = native_session.encode("ascii") if resume else None
    result = on_windows(hub, windows, launcher(job, seconds, resume=resume), until, stdin=stdin,
                        seconds=seconds, timeout_report=timeouts)
    if timeouts:
        return "launch_timeout", False, False
    finished = isinstance(result, dict) and matches(SESSION_ID, result.get("sessionId"))
    if resume and not finished:
        return "native_session_unverified", False, False
    if resume and result["sessionId"] != native_session:
        return "native_session_mismatch", False, True
    return launch_status(result), resume, finished


def read_remote_receipt(hub, windows, nonce, until):
    source = f"""
$ErrorActionPreference = 'Stop'
try {{
    if ($env:COMPUTERNAME -ine '{WINDOWS_HOST}') {{ exit 21 }}
    $dir = Join-Path ([IO.Path]::GetTempPath()) 'paper-football-wx-{nonce}'
    $path = Join-Path $dir 'receipt.json'
    if (!(Test-Path -LiteralPath $path)) {{ [Console]::Write('{{"present":false}}'); exit 0 }}
    $file = Get-Item -LiteralPath $path
    if ($file.PSIsContainer -or $file.Length -gt 4096 -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) {{
        [Console]::Write('{{"invalid":true}}'); exit 0
    }}
    $bytes = [IO.File]::ReadAllBytes($path)
    [Console]::Write('{{"contents":"' + [Convert]::ToBase64String($bytes) + '"}}')
}} catch {{ exit 27 }}
"""
    result = on_windows(hub, windows, source, until, seconds=9)
    if not isinstance(result, dict):
        return "receipt_lookup_unconfirmed", None
    if result.keys() == {"present"} and result["present"] is False:
        return "receipt_missing", None
    encoded = result.get("contents")
    if result.keys() == {"contents"} and isinstance(encoded, str) and len(encoded) <= 5464:
        try:
            raw = base64.b64decode(encoded, validate=True)
            receipt = json.loads(raw) if len(raw) <= 4096 else None
            if isinstance(receipt, dict):
                return "receipt_read", receipt
        except (ValueError, RecursionError):
            pass
    return "receipt_invalid", None


def cleanup_job(hub, windows, nonce, until, *, keep_receipt):
    keep = "$true" if keep_receipt else "$false"
    source = f"""
$ErrorActionPreference = 'Stop'
try {{
    if ($env:COMPUTERNAME -ine '{WINDOWS_HOST}') {{ exit 21 }}
    $dir = Join-Path ([IO.Path]::GetTempPath()) 'paper-football-wx-{nonce}'
    if ([IO.Directory]::Exists($dir) -and !((Get-Item -LiteralPath $dir).Attributes -band [IO.FileAttributes]::ReparsePoint)) {{
        [IO.File]::Delete((Join-Path $dir 'note.txt'))
        [IO.File]::Delete((Join-Path $dir 'job.json'))
        if (!{keep}) {{
            [IO.File]::Delete((Join-Path $dir 'receipt.json'))
            [IO.Directory]::Delete($dir, $false)
        }}
    }}
    [Console]::Write('{{}}')
}} catch {{ exit 27 }}
"""
    on_windows(hub, windows, source, until, seconds=8)


def verify_receipt(receipt, job, deadline):
    if not isinstance(receipt, dict):
        return None
    for field in ("stage_key", "nonce", "target_digest", "body_variant", "body_digest"):
        if receipt.get(field) != job[field]:
            return None
    if (receipt.get("state") != "accepted" or receipt.get("platform") != "weixin"
            or any(receipt.get(field) is not True for field in ("target_verified", "native_success", "chat_verified"))
            or type(receipt.get("native_exit_code")) is not int or receipt["native_exit_code"] != 0
            or not matches(HEX64, receipt.get("message_id_sha256"))):
        return None
    try:
        started, confirmed, cutoff = [as_utc(datetime.fromisoformat(receipt[field]))
                                      for field in ("send_started_at", "confirmed_at", "cutoff_utc")]
        if not started <= confirmed < cutoff <= deadline:
            return None
        if "cutoff_utc" in job and cutoff != datetime.fromisoformat(job["cutoff_utc"]):
            return None
        if "starts_utc" in job and started < datetime.fromisoformat(job["starts_utc"]):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return {"state": "accepted", "platform": "weixin", "target_verified": True,
            "attempt_key": job["nonce"], "body_variant": job["body_variant"],
            "message_id_sha256": receipt["message_id_sha256"], "confirmed_at": confirmed.isoformat()}


def unconfirmed_reason(launch, receipt):
    if receipt == "receipt_invalid":
        return "receipt_invalid"
    if launch == "native_send_reported":
        return "send_reported_without_receipt"
    if launch == "zcode_reported_receipt_written":
        return receipt
    return launch


def recover(*, stage_key, attempt_key, body_variant, deadline, target_digest, body_digest,
            native_session=None, native_session_verified=False):
    """Look up the receipt for a previously claimed attempt without sending."""
    unknown = {"state": "unknown", "reason": "receipt_unconfirmed"}
    if native_session is not None and (not matches(SESSION_ID, native_session) or native_session_verified is not True):
        return {"state": "unknown", "reason": "native_session_unverified"}
    if (not matches(STAGE_KEY, stage_key) or not matches(HEX32, attempt_key)
            or body_variant not in ("primary", "fallback")
            or not matches(HEX64, target_digest) or not matches(HEX64, body_digest)):
        return unknown
    try:
        deadline = as_utc(deadline)
        until = time.monotonic() + 35
        hub, windows = route(until)
        job = {"stage_key": stage_key, "nonce": attempt_key, "body_variant": body_variant,
               "body_digest": body_digest, "target_digest": target_digest}
        _, receipt = read_remote_receipt(hub, windows, attempt_key, until)
        verified = verify_receipt(receipt, job, deadline)
        if verified is not None and native_session is not None:
            verified["native_session_verified"] = True
        return verified or unknown
    except (HandoffError, TypeError, ValueError, OverflowError):
        return unknown


def deliver(body, *, stage_key, starts_at, deadline, target_digest, on_submit,
            valid_until=None, fallback_body=None, native_session=None):
    """Perform at most one native launch after taking a durable caller-owned claim.

    The caller supplies the approved binding and an on_submit callback accepting
    attempt_key, body_variant, and body_digest as keywords. A truthy return must
    mean the claim is durable. Any submitted attempt without a verified receipt
    remains unknown and is eligible only for receipt lookup. A supplied native
    session must be the caller's exclusive, privately pinned conversation.
    """
    resume = native_session is not None
    if resume and not matches(SESSION_ID, native_session):
        return {"state": "preflight_failed", "reason": "native_session_unverified"}
    now = utcnow()
    try:
        starts, deadline = as_utc(starts_at), as_utc(deadline)
        paper_cutoff = min(as_utc(valid_until), now + MAX_PAPER_WINDOW, deadline) if valid_until is not None else deadline
    except (TypeError, ValueError, OverflowError):
        return {"state": "preflight_failed", "reason": "invalid_window"}
    if (starts >= deadline or not matches(STAGE_KEY, stage_key) or not matches(HEX64, target_digest)
            or not callable(on_submit) or not valid_body(body)
            or (fallback_body is not None and not valid_body(fallback_body))):
        return {"state": "preflight_failed", "reason": "invalid_payload"}
    if now < starts:
        return {"state": "preflight_failed", "reason": "window_not_open"}
    fallback = valid_until is not None and fallback_body is not None
    last_cutoff = deadline if fallback else paper_cutoff
    expired = {"state": "expired", "reason": "window_closed"}
    if not native_budget(last_cutoff, now):
        return expired

    wall_free, transport_start = native_budget(last_cutoff, now), time.monotonic()
    hard_limit = transport_start + TOTAL_SECONDS
    launch_until = hard_limit - POST_LAUNCH_RESERVE
    route_limit = min(transport_start + 45, transport_start + wall_free)
    try:
        hub, windows = route(route_limit)
        route_ok = verify_route(hub, windows, target_digest, route_limit, resume=resume)
    except HandoffError:
        route_ok = False
    if not native_budget(last_cutoff, utcnow()):
        return expired
    if not route_ok:
        return {"state": "preflight_failed", "reason": "route_unverified"}

    variants = [("primary", body, paper_cutoff)]
    if fallback:
        if native_budget(paper_cutoff, utcnow()) < MIN_PRIMARY_SECONDS + UPLOAD_ALLOWANCE:
            variants.clear()
        variants.append(("fallback", fallback_body, deadline))
    for variant, text, cutoff in variants:
        now = utcnow()
        if now < starts:
            return {"state": "preflight_failed", "reason": "window_not_open"}
        if not native_budget(cutoff, now):
            continue
        nonce, body_digest = secrets.token_hex(16), digest(text)
        job = {"stage_key": stage_key, "nonce": nonce, "body_variant": variant, "body_digest": body_digest,
               "target_digest": target_digest, "starts_utc": starts.isoformat(), "cutoff_utc": cutoff.isoformat(),
               "body": text}
        submitted = False
        try:
            upload_until = min(launch_until - HUB_TIMEOUT_PADDING - CLIENT_TIMEOUT_PADDING - 3,
                               time.monotonic() + 25)
            if not upload_job(hub, windows, job, upload_until):
                return {"state": "preflight_failed", "reason": "preparation_unconfirmed"}
            budget = native_budget(cutoff, utcnow())
            if not budget or (variant == "primary" and fallback and budget < MIN_PRIMARY_SECONDS):
                continue
            try:
                claimed = on_submit(attempt_key=nonce, body_variant=variant, body_digest=body_digest)
            except Exception:
                return {"state": "preflight_failed", "reason": "claim_unconfirmed"}
            if not claimed:
                return {"state": "claimed", "reason": "claim_rejected"}

            now = utcnow()
            transport_budget = math.floor(launch_until - time.monotonic()) - HUB_TIMEOUT_PADDING - CLIENT_TIMEOUT_PADDING
            budget = min(native_budget(cutoff, now), transport_budget)
            if now < starts or budget < 1:
                if not native_budget(last_cutoff, now):
                    return expired
                return {"state": "preflight_failed", "reason": "window_changed_after_claim"}
            submitted = True
            launch_diagnostic, session_verified, turn_finished = run_zcode(
                hub, windows, job, budget, launch_until, native_session=native_session,
            )
            provenance = {"native_session_verified": session_verified, "native_turn_finished": turn_finished} if resume else {}
            uncertain = {"state": "unknown", "attempt_key": nonce, "body_variant": variant,
                         "launch_diagnostic": launch_diagnostic, **provenance}
            if (resume and not session_verified) or launch_diagnostic in REFUSALS:
                return {**uncertain, "reason": launch_diagnostic, "receipt_diagnostic": "receipt_not_checked"}
            receipt_until = min(hard_limit - CLEANUP_SECONDS, time.monotonic() + RECEIPT_SECONDS)
            receipt_status, receipt = read_remote_receipt(hub, windows, nonce, receipt_until)
            verified = verify_receipt(receipt, job, deadline)
            if verified:
                return {**verified, **provenance}
            if receipt_status == "receipt_read":
                receipt_status = "receipt_invalid"
            return {**uncertain, "reason": unconfirmed_reason(launch_diagnostic, receipt_status),
                    "receipt_diagnostic": receipt_status}
        finally:
            cleanup_job(hub, windows, nonce, min(hard_limit, time.monotonic() + CLEANUP_SECONDS), keep_receipt=submitted)
    return expired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-check", action="store_true", required=True,
                        help="read the binding digest from stdin for the read-only route check")
    parser.parse_args()
    checked = check_route(sys.stdin.readline(66).strip())
    print(json.dumps(checked))
    return 0 if checked["route_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
