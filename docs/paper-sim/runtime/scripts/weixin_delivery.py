"""Deliver one claimed reminder through the fixed, verified Weixin entry point."""

import argparse
import base64
import hashlib
import json
import math
import os
import pwd
import re
import secrets
import selectors
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import NamedTuple


HC_PATH = os.environ.get("PAL_WEIXIN_HC_PATH", "")
HC_USER = os.environ.get("PAL_WEIXIN_HC_USER", "")
HC_SOCKET = os.environ.get("PAL_WEIXIN_HC_SOCKET", "")
HC_CONTEXT_SHA256 = os.environ.get("PAL_WEIXIN_HC_CONTEXT_SHA256", "")
WINDOWS_FINGERPRINT = os.environ.get("PAL_HERDR_WINDOWS_FINGERPRINT", "")
WINDOWS_HOST = os.environ.get("PAL_WINDOWS_HOST", "")
POWERSHELL_PATH = os.environ.get("PAL_WEIXIN_POWERSHELL_PATH", "")
SCRIPT_PATH = os.environ.get("PAL_WEIXIN_SCRIPT_PATH", "")
RELEASE_COMMIT = os.environ.get("PAL_WEIXIN_RELEASE_COMMIT", "")
RELEASE_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_BLOB = "docs/paper-sim/runtime/scripts/windows/Invoke-PaperWeixin.ps1"
CHECKSUM_BLOB = SCRIPT_BLOB + ".sha256"

HEX64 = re.compile(r"[a-f0-9]{64}")
HEX32 = re.compile(r"[a-f0-9]{32}")
GIT_COMMIT = re.compile(r"[a-f0-9]{40}")
STAGE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
HC_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}")
SESSION_ID = re.compile(r"sess_[A-Za-z0-9-]{1,96}")
WINDOWS_PATH = re.compile(r"[a-zA-Z]:\\(?:[A-Za-z0-9_ ()-][A-Za-z0-9_. ()-]*\\)*[A-Za-z0-9_ ()-][A-Za-z0-9_. ()-]*")
UTC_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)")
MAX_BODY_CHARS = 1500
MAX_PAPER_WINDOW = timedelta(minutes=5)
CLOCK_SKEW = timedelta(seconds=2)
WINDOWS_GUARD = 3
TRANSIT_MARGIN = 12
CLIENT_TIMEOUT_PADDING = 5
RECEIPT_SECONDS = 12
CLEANUP_SECONDS = 9
POST_LAUNCH_RESERVE = RECEIPT_SECONDS + CLEANUP_SECONDS + 1
MIN_PRIMARY_SECONDS = 45
UPLOAD_ALLOWANCE = 20
# The Windows sender and the existing application window keep these ceilings.
NATIVE_MAX = 575
TOTAL_SECONDS = 660
HC_VERSION = "0.1.21"
HC_LOOKUP_SECONDS = 3
MAX_ENVELOPE_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 20480
MAX_RESPONSE_BYTES = 8192
MAX_RECEIPT_BYTES = 4096

PROTOCOL = "paper-weixin-fixed-v1"
FIXED_CLAIMED = "fixed_entry_claimed"
FIXED_INTERRUPTED = "fixed_entry_interrupted"
FIXED_UNCONFIRMED = "fixed_entry_unconfirmed"
UNVERIFIED_REFUSAL = "fixed_entry_refusal_unverified"
NATIVE_REFUSALS = frozenset({
    "zcode_tool_permission_denied",
    "hermes_platform_write_denied",
    "weixin_owner_message_required",
})
FIXED_REFUSALS = frozenset(f"fixed_entry_{reason}" for reason in NATIVE_REFUSALS) | {UNVERIFIED_REFUSAL}
FIXED_RECOVERABLE = frozenset({FIXED_CLAIMED, FIXED_INTERRUPTED, FIXED_UNCONFIRMED})
FIXED_REASONS = FIXED_RECOVERABLE | FIXED_REFUSALS

ROUTE_VERIFIED = {"route_verified": True, "parser_verified": True, "weixin_target_verified": True}
PREPARE_FIELDS = frozenset({
    "stage_key", "body_variant", "body_digest", "body", "starts_utc", "cutoff_utc", "deadline_utc",
})
REQUEST_FIELDS = {
    "check": frozenset(),
    "prepare": PREPARE_FIELDS,
    "send": frozenset({"send_budget_seconds"}),
    "receipt": frozenset({"receipt_generation"}),
    "cleanup": frozenset(),
}
RESPONSE_FIELDS = frozenset({"protocol", "purpose", "code", "script_sha256"})
SUCCESS_FLAGS = {
    "check": {"hostname_matches", "target_verified"},
    "prepare": {"prepared"},
    "send": {"sent"},
    "cleanup": set(),
}
RECEIPT_IDENTITY = ("stage_key", "nonce", "target_digest", "body_variant", "body_digest")
FIXED_RECEIPT_FIELDS = frozenset(RECEIPT_IDENTITY) | {
    "protocol", "script_sha256", "starts_utc", "cutoff_utc", "deadline_utc",
    "send_started_at", "confirmed_at", "state", "platform", "target_verified",
    "native_success", "chat_verified", "native_exit_code", "message_id_sha256",
}
WINDOWS_FAILURE_CODES = frozenset({21, 22, 23, 24, 25, 26, 27, 28, 29, 31})
# Exit 29 is a state conflict. A concurrent invocation may still own the files.
COMPLETED_SEND_CODES = (WINDOWS_FAILURE_CODES - {29}) | {0, 32}


class HandoffError(Exception):
    """A fixed diagnostic safe to return without route or account data."""


class HcContext(NamedTuple):
    executable: str
    home: str
    socket: Path
    socket_identity: tuple[int, int]
    uid: int


class DirectRoute(NamedTuple):
    context: HcContext
    windows_id: str
    local_id: str
    local_name: str


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


def fixed_windows_path(value):
    return matches(WINDOWS_PATH, value) and all(
        part == part.strip() and not part.endswith(".") for part in PureWindowsPath(value).parts[1:]
    )


def launcher(nonce):
    """Return the sole Windows process and argument vector."""
    if not matches(HEX32, nonce):
        raise HandoffError("invalid_nonce")
    if (
        not fixed_windows_path(POWERSHELL_PATH)
        or not POWERSHELL_PATH.lower().endswith(r"\system32\windowspowershell\v1.0\powershell.exe")
        or not fixed_windows_path(SCRIPT_PATH)
        or PureWindowsPath(SCRIPT_PATH).name.lower() != "invoke-paperweixin.ps1"
    ):
        raise HandoffError("fixed_entry_unconfigured")
    return [POWERSHELL_PATH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
            "-File", SCRIPT_PATH, nonce]


def approved_script_sha256():
    """Verify the checkout and use the manifest from its approved Git commit."""
    launcher("0" * 32)
    if not matches(GIT_COMMIT, RELEASE_COMMIT):
        raise HandoffError("fixed_entry_unconfigured")

    def git(*args):
        result = subprocess.run(
            ["/usr/bin/git", "--no-replace-objects", "-C", str(RELEASE_ROOT), *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False,
            env={"PATH": "/usr/bin:/bin", "GIT_ALLOW_PROTOCOL": "", "GIT_NO_LAZY_FETCH": "1"},
        )
        if result.returncode != 0:
            raise HandoffError("fixed_release_unverified")
        return result.stdout

    try:
        if git("rev-parse", "--verify", "HEAD") != RELEASE_COMMIT.encode("ascii") + b"\n":
            raise HandoffError("fixed_release_unverified")
        manifest = git("cat-file", "blob", f"{RELEASE_COMMIT}:{CHECKSUM_BLOB}")
        source = git("cat-file", "blob", f"{RELEASE_COMMIT}:{SCRIPT_BLOB}")
        checksum = hashlib.sha256(source).hexdigest()
        expected_manifest = f"{checksum}  Invoke-PaperWeixin.ps1\n".encode("ascii")
        if (not source or not source.isascii() or manifest != expected_manifest
                or (RELEASE_ROOT / SCRIPT_BLOB).read_bytes() != source
                or (RELEASE_ROOT / CHECKSUM_BLOB).read_bytes() != manifest):
            raise HandoffError("fixed_release_unverified")
        return checksum
    except (OSError, subprocess.TimeoutExpired):
        raise HandoffError("fixed_release_unverified") from None


def _no_duplicate_keys(items):
    value = dict(items)
    if len(items) != len(value):
        raise ValueError("duplicate JSON key")
    return value


def _invalid_json_constant(_):
    raise ValueError("non-JSON constant")


def _json_object(raw, limit):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= limit:
        return None
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                           parse_constant=_invalid_json_constant)
        return value if isinstance(value, dict) else None
    except (ValueError, RecursionError):
        return None


def _socket_identity(path, uid):
    endpoint = path.lstat()
    parent = path.parent.lstat()
    if (not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_uid != uid
            or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid or parent.st_mode & 0o077):
        raise HandoffError("route_context_unverified")
    return endpoint.st_dev, endpoint.st_ino


def _hc_context():
    if (not os.path.isabs(HC_PATH) or not os.path.isabs(HC_SOCKET) or not HC_USER
            or not matches(HEX64, HC_CONTEXT_SHA256)):
        raise HandoffError("route_unavailable")
    try:
        uid = os.geteuid()
        account = pwd.getpwuid(uid)
        path = Path(HC_SOCKET)
        if account.pw_name != HC_USER or not os.path.isabs(account.pw_dir) or path.resolve(strict=True) != path:
            raise HandoffError("route_context_unverified")
        return HcContext(HC_PATH, account.pw_dir, path, _socket_identity(path, uid), uid)
    except (AttributeError, KeyError, OSError, RuntimeError, ValueError):
        raise HandoffError("route_context_unverified") from None


def _run_hc(argv, *, env, stdin, deadline):
    """Collect at most one bounded reply while feeding the fixed request."""
    output = bytearray()
    with selectors.DefaultSelector() as selector, subprocess.Popen(
        argv, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd="/", bufsize=0,
    ) as child:
        try:
            selector.register(child.stdout, selectors.EVENT_READ)
            if stdin:
                selector.register(child.stdin, selectors.EVENT_WRITE)
                os.set_blocking(child.stdin.fileno(), False)
            pending = memoryview(stdin or b"")
            while selector.get_map():
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    return None
                for key, event in selector.select(timeout):
                    stream = key.fileobj
                    if event == selectors.EVENT_WRITE:
                        pending = pending[os.write(stream.fileno(), pending):]
                        if not pending:
                            selector.unregister(stream)
                            stream.close()
                        continue
                    chunk = os.read(stream.fileno(), min(65536, MAX_ENVELOPE_BYTES - len(output) + 1))
                    if not chunk:
                        selector.unregister(stream)
                    output.extend(chunk)
                    if len(output) > MAX_ENVELOPE_BYTES:
                        return None
            code = child.wait(timeout=max(0, deadline - time.monotonic()))
            return subprocess.CompletedProcess(argv, code, bytes(output))
        finally:
            if child.poll() is None:
                child.kill()


def _hc_call(context, args, until, *, stdin=None, limit=HC_LOOKUP_SECONDS):
    deadline = min(until, time.monotonic() + limit)
    if deadline <= time.monotonic():
        return None
    env = {"PATH": os.defpath, "HOME": context.home, "HC_SOCKET": str(context.socket)}
    try:
        if _socket_identity(context.socket, context.uid) != context.socket_identity:
            return None
        result = _run_hc([context.executable, *args], env=env, stdin=stdin, deadline=deadline)
        if (result is not None and result.returncode == 0 and time.monotonic() < deadline
                and _socket_identity(context.socket, context.uid) == context.socket_identity):
            return result.stdout
    except (HandoffError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def _decode_base64(value, limit):
    if not isinstance(value, str) or len(value) > 4 * ((limit + 2) // 3):
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) <= limit and base64.b64encode(decoded).decode("ascii") == value:
            return decoded
    except ValueError:
        pass
    return None


def _windows_selector(context, local_id, local_name, until):
    record = _json_object(_hc_call(context, ["machine", "ls", "--json"], until), MAX_ENVELOPE_BYTES)
    rows = record.get("machines") if isinstance(record, dict) else None
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not matches(HEX32, row.get("id")) or not matches(HC_NAME, row.get("name"))
        for row in rows
    ):
        raise HandoffError("route_unavailable")
    hits = [row for row in rows if digest(row["id"]) == WINDOWS_FINGERPRINT]
    if len(hits) != 1:
        raise HandoffError("route_unavailable")
    selected = hits[0]
    machine_id, host = selected["id"], selected.get("host")
    if not matches(STAGE_KEY, host) or host.casefold() != WINDOWS_HOST.casefold():
        raise HandoffError("route_identity_mismatch")
    # hc 0.1.21 resolves the local identity first, then exact machine names or IDs.
    if machine_id in (local_id, local_name) or any(
        row is not selected and row["name"] == machine_id for row in rows
    ):
        raise HandoffError("route_selector_ambiguous")
    if selected.get("state") != "connected":
        raise HandoffError("route_unavailable")
    return machine_id


def route(until):
    if not matches(HEX64, WINDOWS_FINGERPRINT) or not matches(STAGE_KEY, WINDOWS_HOST):
        raise HandoffError("route_unavailable")
    context = _hc_context()
    if _hc_call(context, ["--version"], until) != f"hc {HC_VERSION}\n".encode("ascii"):
        raise HandoffError("route_client_unverified")
    status = _json_object(
        _hc_call(context, ["server", "status", "--json"], until), MAX_ENVELOPE_BYTES,
    )
    core = status.get("core") if status and status.get("version") == HC_VERSION else None
    if (not isinstance(core, dict) or not matches(HEX32, core.get("id"))
            or not matches(HC_NAME, core.get("name")) or digest(core["id"]) != HC_CONTEXT_SHA256):
        raise HandoffError("route_context_unverified")
    windows_id = _windows_selector(context, core["id"], core["name"], until)
    return DirectRoute(context, windows_id, core["id"], core["name"])


def _read_windows_exit(raw):
    # hc 0.1.21's explicit --json branch exits 0 for every RPC result.
    # _hc_call has already required that local status.
    record = _json_object(raw, MAX_ENVELOPE_BYTES)
    if record is None:
        return None
    code = record.get("code")
    if (type(code) is not int or not 0 <= code <= 255 or record.get("signal") is not None
            or record.get("timed_out") is not False or record.get("truncated") is not False):
        return None
    stdout = _decode_base64(record.get("stdout"), MAX_ENVELOPE_BYTES)
    return None if stdout is None else (code, stdout)


def fixed_request(endpoint, nonce, purpose, script_sha, target, until, seconds, **params):
    if (purpose not in REQUEST_FIELDS or params.keys() != REQUEST_FIELDS[purpose]
            or type(seconds) is not int or not 1 <= seconds <= NATIVE_MAX):
        raise HandoffError("invalid_fixed_request")
    request = {
        "protocol": PROTOCOL, "purpose": purpose, "hostname": WINDOWS_HOST,
        "script_sha256": script_sha, "target_digest": target, **params,
    }
    wire = json.dumps(request, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(wire) > MAX_REQUEST_BYTES:
        raise HandoffError("invalid_fixed_request")
    windows_argv = launcher(nonce)
    try:
        if _windows_selector(endpoint.context, endpoint.local_id, endpoint.local_name, until) != endpoint.windows_id:
            return None
    except HandoffError:
        return None
    result = _read_windows_exit(_hc_call(
        endpoint.context, ["exec", "--json", "--on", endpoint.windows_id, "--timeout", str(seconds),
                           "-i", "--", *windows_argv],
        until, stdin=wire, limit=seconds + CLIENT_TIMEOUT_PADDING,
    ))
    if result is None:
        return None
    code, stdout = result
    if code in WINDOWS_FAILURE_CODES:
        return None if stdout else (code, None)
    if code not in (0, 32):
        return None
    payload = _json_object(stdout, MAX_RESPONSE_BYTES)
    if (payload is None or payload.get("protocol") != PROTOCOL or payload.get("purpose") != purpose
            or type(payload.get("code")) is not int or payload["code"] != code
            or payload.get("script_sha256") != script_sha):
        return (code, None) if code == 32 else None
    if code == 32:
        reason = payload.get("reason")
        if (purpose in {"check", "send", "receipt"} and payload.keys() == RESPONSE_FIELDS | {"reason"}
                and isinstance(reason, str) and reason in NATIVE_REFUSALS):
            return code, payload
        return code, None
    if purpose == "receipt":
        if (payload.keys() == RESPONSE_FIELDS | {"present"} and payload["present"] is False
                or payload.keys() == RESPONSE_FIELDS | {"contents"} and isinstance(payload["contents"], str)):
            return code, payload
        return None
    flags = SUCCESS_FLAGS[purpose]
    if payload.keys() == RESPONSE_FIELDS | flags and all(payload[field] is True for field in flags):
        return code, payload
    return None


def refusal_reason(response):
    if response is not None and response[0] == 32:
        return f"fixed_entry_{response[1]['reason']}" if response[1] is not None else UNVERIFIED_REFUSAL
    return None


def check_route(target_digest):
    """Verify the pinned route, fixed script and private destination."""
    if matches(HEX64, target_digest):
        try:
            until = time.monotonic() + 55
            sha = approved_script_sha256()
            endpoint = route(until)
            checked = fixed_request(endpoint, secrets.token_hex(16), "check", sha, target_digest, until, 24)
            if checked is not None and checked[0] == 0:
                return ROUTE_VERIFIED.copy()
        except HandoffError:
            pass
    return dict.fromkeys(ROUTE_VERIFIED, False)


def read_remote_receipt(endpoint, nonce, sha, target, until, *, generation):
    response = fixed_request(endpoint, nonce, "receipt", sha, target, until, 9, receipt_generation=generation)
    stopped = refusal_reason(response)
    if stopped:
        return stopped, None
    if response is None or response[0] != 0:
        return "receipt_lookup_unconfirmed", None
    encoded = response[1].get("contents")
    if encoded is None:
        return "receipt_missing", None
    value = _json_object(_decode_base64(encoded, MAX_RECEIPT_BYTES), MAX_RECEIPT_BYTES)
    return ("receipt_read", value) if value is not None else ("receipt_invalid", None)


def _fixed_stamp(stamp):
    if not matches(UTC_STAMP, stamp):
        raise ValueError("invalid UTC timestamp")
    return datetime.fromisoformat(stamp)


def verify_receipt(receipt, job, deadline, *, script_sha=None, submitted_at=None):
    if not isinstance(receipt, dict):
        return None
    if any(receipt.get(field) != job[field] for field in RECEIPT_IDENTITY):
        return None
    if (receipt.get("state") != "accepted" or receipt.get("platform") != "weixin"
            or any(receipt.get(field) is not True for field in ("target_verified", "native_success", "chat_verified"))
            or type(receipt.get("native_exit_code")) is not int or receipt["native_exit_code"] != 0
            or not matches(HEX64, receipt.get("message_id_sha256"))):
        return None
    try:
        if script_sha is None:
            if {"protocol", "script_sha256", "deadline_utc"} & receipt.keys():
                return None
            started, confirmed, cutoff = [as_utc(datetime.fromisoformat(receipt[field]))
                                          for field in ("send_started_at", "confirmed_at", "cutoff_utc")]
        else:
            if (receipt.keys() != FIXED_RECEIPT_FIELDS or receipt["protocol"] != PROTOCOL
                    or receipt["script_sha256"] != script_sha or not matches(HEX64, script_sha)):
                return None
            started, confirmed, cutoff, opens, stated_deadline = [
                _fixed_stamp(receipt[field]) for field in
                ("send_started_at", "confirmed_at", "cutoff_utc", "starts_utc", "deadline_utc")
            ]
            if opens != _fixed_stamp(job["starts_utc"]) or stated_deadline != deadline or opens > started:
                return None
            if submitted_at is not None and started < as_utc(submitted_at) - CLOCK_SKEW:
                return None
        if not started <= confirmed < cutoff <= deadline:
            return None
        if "cutoff_utc" in job and cutoff != datetime.fromisoformat(job["cutoff_utc"]):
            return None
        if "starts_utc" in job and started < datetime.fromisoformat(job["starts_utc"]):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    result = {"state": "accepted", "platform": "weixin", "target_verified": True,
              "attempt_key": job["nonce"], "body_variant": job["body_variant"],
              "message_id_sha256": receipt["message_id_sha256"], "confirmed_at": confirmed.isoformat()}
    if script_sha is not None:
        result["fixed_receipt"] = receipt
    return result


def recover(*, stage_key, attempt_key, body_variant, deadline, target_digest, body_digest,
            native_session=None, native_session_verified=False, starts_at=None, submitted_at=None,
            receipt_generation="legacy", cutoff_at=None):
    """Look up one previously claimed attempt in its own receipt location."""
    unknown = {"state": "unknown", "reason": "receipt_unconfirmed"}
    if receipt_generation == "legacy" and native_session is not None and (
        not matches(SESSION_ID, native_session) or native_session_verified is not True
    ):
        return {"state": "unknown", "reason": "native_session_unverified"}
    if (receipt_generation not in {"fixed", "legacy"}
            or not matches(STAGE_KEY, stage_key) or not matches(HEX32, attempt_key)
            or body_variant not in ("primary", "fallback")
            or not matches(HEX64, target_digest) or not matches(HEX64, body_digest)):
        return unknown
    try:
        deadline = as_utc(deadline)
        job = {"stage_key": stage_key, "nonce": attempt_key, "body_variant": body_variant,
               "body_digest": body_digest, "target_digest": target_digest}
        fixed = receipt_generation == "fixed"
        if fixed:
            job["starts_utc"] = as_utc(starts_at).isoformat()
            submitted_at = as_utc(submitted_at)
            if cutoff_at is not None:
                job["cutoff_utc"] = as_utc(cutoff_at).isoformat()
        until = time.monotonic() + 35
        sha = approved_script_sha256()
        endpoint = route(until)
        status, receipt = read_remote_receipt(endpoint, attempt_key, sha, target_digest, until,
                                              generation=receipt_generation)
        if fixed and status in FIXED_REFUSALS:
            return {"state": "unknown", "reason": status}
        verified = verify_receipt(receipt, job, deadline, script_sha=sha if fixed else None, submitted_at=submitted_at)
        if verified is not None and not fixed and native_session is not None:
            verified["native_session_verified"] = True
        return verified or unknown
    except (HandoffError, TypeError, ValueError, OverflowError):
        return unknown


def deliver(body, *, stage_key, starts_at, deadline, target_digest, on_submit,
            valid_until=None, fallback_body=None, native_session=None):
    """Start the fixed sender at most once after a durable caller-owned claim.

    The caller supplies the approved binding and a callback accepting
    attempt_key, body_variant and body_digest. A truthy return means that the
    claim transaction, including its fixed transport marker, committed.
    """
    if native_session is not None and not matches(SESSION_ID, native_session):
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
    wall_free = native_budget(last_cutoff, now)
    if not wall_free:
        return expired

    transport_start = time.monotonic()
    hard_limit = transport_start + TOTAL_SECONDS
    launch_until = hard_limit - POST_LAUNCH_RESERVE
    route_limit = min(transport_start + 45, transport_start + wall_free)
    try:
        sha = approved_script_sha256()
        endpoint = route(route_limit)
        checked = fixed_request(endpoint, secrets.token_hex(16), "check", sha, target_digest, route_limit, 24)
    except HandoffError as error:
        return {"state": "preflight_failed", "reason": str(error)}
    stopped = refusal_reason(checked)
    if stopped:
        return {"state": "refused", "reason": stopped}
    if not native_budget(last_cutoff, utcnow()):
        return expired
    if checked is None or checked[0] != 0:
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
               "deadline_utc": deadline.isoformat(), "body": text}
        cleanup = False
        try:
            upload_until = min(launch_until - CLIENT_TIMEOUT_PADDING - WINDOWS_GUARD,
                               time.monotonic() + UPLOAD_ALLOWANCE + CLIENT_TIMEOUT_PADDING)
            prepared = fixed_request(endpoint, nonce, "prepare", sha, target_digest, upload_until, UPLOAD_ALLOWANCE,
                                     **{field: job[field] for field in PREPARE_FIELDS})
            if prepared is None or prepared[0] != 0:
                return {"state": "preflight_failed", "reason": "preparation_unconfirmed"}

            cleanup = True
            now = utcnow()
            budget = native_budget(cutoff, now)
            if now < starts or not budget or (variant == "primary" and fallback and budget < MIN_PRIMARY_SECONDS):
                continue
            try:
                claimed = on_submit(attempt_key=nonce, body_variant=variant, body_digest=body_digest)
            except Exception:
                return {"state": "preflight_failed", "reason": "claim_unconfirmed"}
            if not claimed:
                return {"state": "claimed", "reason": "claim_rejected"}

            uncertain = {"state": "unknown", "attempt_key": nonce, "body_variant": variant,
                         "reason": FIXED_UNCONFIRMED}
            now = utcnow()
            transport_budget = math.floor(launch_until - time.monotonic()) - CLIENT_TIMEOUT_PADDING - HC_LOOKUP_SECONDS
            budget = min(native_budget(cutoff, now), transport_budget)
            if now < starts or budget < 1:
                return {**uncertain, "launch_diagnostic": "window_changed_after_claim"}

            cleanup = False
            sent = fixed_request(endpoint, nonce, "send", sha, target_digest, launch_until, budget,
                                 send_budget_seconds=budget)
            cleanup = sent is not None and sent[0] in COMPLETED_SEND_CODES
            stopped = refusal_reason(sent)
            if stopped:
                return {**uncertain, "reason": stopped}
            if sent is None or sent[0] != 0:
                return {**uncertain, "fixed_exit_code": sent[0] if sent else None}

            receipt_until = min(hard_limit - CLEANUP_SECONDS, time.monotonic() + RECEIPT_SECONDS)
            status, receipt = read_remote_receipt(endpoint, nonce, sha, target_digest, receipt_until,
                                                  generation="fixed")
            if status in FIXED_REFUSALS:
                return {**uncertain, "reason": status}
            verified = verify_receipt(receipt, job, deadline, script_sha=sha)
            return verified or {**uncertain, "receipt_diagnostic": "receipt_invalid" if receipt else status}
        finally:
            if cleanup:
                fixed_request(endpoint, nonce, "cleanup", sha, target_digest,
                              min(hard_limit, time.monotonic() + CLEANUP_SECONDS), 8)
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
