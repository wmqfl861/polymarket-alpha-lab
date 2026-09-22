"""RV-05 single-shot auto-link control (PAL_RV05_CLOSURE_20260922, node N3).

THIN wait/unique-start layer ABOVE the existing one-shot launch package
(``launch-rv05.ps1`` + ``preflight-rv05.py``). It does not replace, modify
or re-implement the launcher, the preflight, the driver, the audit or the
generator: START means "invoke the bound launch flow (fixed argv) exactly
once, after every START condition is confirmed". This module is control
code only - it never starts the soak itself (the bound launch_argv does,
under the launcher's own claim/receipt one-shot protocol).

State machine (persisted in ``state_path``; plan section 6):

    UNARMED -> ARMED_WAITING -> PRECHECK -> CLAIMED -> STARTED
                                     -> FINAL_REVIEW_READY
    parallel terminals: CANCELLED / EXPIRED / NO_GO / UNKNOWN

Waiting-vs-terminal semantics (the load-bearing rule):

  * the ONLY waitable situations are "the original run has not finished
    yet" (lock-record identity alive, evidence not yet stable) and "the
    launch window has not opened yet". These keep the state
    ARMED_WAITING and are retried on the next bounded poll cycle.
  * identity drift, cleanup that cannot be confirmed, corrupted evidence,
    PID reuse, lost acknowledgement -> NO_GO / UNKNOWN TERMINAL states.
    They are never retried into a pass: a later ``wait`` invocation on a
    terminal state re-reports the same terminal verdict and does no work.
  * planned end time is NOT proof the original driver exited: exit is
    confirmed from campaign evidence only (released lock + closing
    receipts + stable evidence + no leftover running children), never
    from a historical bare PID number. An unconfirmable process
    identity stays UNKNOWN.

One-shot uniqueness (mirrors the launcher's claim-first protocol one
level up):

  * the unique claim (``claim_path``) is created atomically
    (O_CREAT|O_EXCL) immediately BEFORE the launch flow is invoked;
  * a restart of this control program NEVER clears or rewrites the
    claim;
  * if the claim exists without a valid STARTED receipt (crash between
    claim and receipt, or a half-written/unreadable receipt) the state
    is LOST-ACK / UNKNOWN: no second launch attempt, no claim recovery,
    no receipt rewrite - manual adjudication only;
  * two concurrent instances that both pass all gates race for the
    claim: exactly one wins (O_EXCL); the loser exits with code 3
    WITHOUT launching and without touching the shared state;
  * CANCEL is the linker's OWN stop marker (``linker_stop_path``). It is
    a path completely independent from the original campaign's stop file
    and from the new campaign's stop file: cancelling the linker never
    stops (and never writes anything to) either campaign.

Irreversible boundaries, cancel linearization, receipt semantics
(fix wave N1, PAL_RV05_CONTROL_SAFETY_20260922, L1-L4). Within one
wait cycle the boundaries are linear and DOCUMENTED - "one more if" is
not the fix; the fix is WHERE the re-checks sit:

  B0  preflight-phase re-checks (marker/terminal/pins/clock/window)
      run before any irreversible step (unchanged B0 checks);
  B1  the bound preflight subprocess is read-only and reversible. The
      moment it returns, the POST-PREFLIGHT GATE re-validates everything
      its duration could have invalidated, BEFORE the claim consumes
      the one-shot slot: the operator cancel marker (a cancel written
      while the preflight ran - L1), the window/deadline against a
      FRESH clock read (crossing latest during preflight - L2), binding
      pin drift, and report freshness (the GO report must have been
      produced by THIS invocation - mtime >= preflight spawn; a stale
      leftover GO from an earlier cycle is UNKNOWN, never a launch
      basis). A foreign terminal state WITHOUT a cancel marker is
      deliberately NOT a gate abort: a concurrent instance that already
      won is adjudicated by the O_EXCL claim race (exit 3), preserving
      the two-instance competition contract.
  B2  the one-shot CLAIM write (O_CREAT|O_EXCL, full-write loop over
      partial writes, fsync, byte-identical readback + claim_id
      verification) is the SLOT-CONSUMPTION boundary. From here the
      slot is consumed and no later step may un-consume it; a claim
      write that cannot be confirmed (short/torn write) is UNKNOWN and
      is never re-attempted.
  B3  immediately before the launch invocation the LAST-SAFE-POINT GATE
      re-checks the cancel marker and the window/deadline one final
      time. THIS IS THE LAST MOMENT AT WHICH A CANCEL CAN PREVENT THE
      LAUNCH: a marker present there (cancel confirmed before the
      boundary) aborts to CANCELLED with ``launch_invoked=false`` while
      the claim stays consumed (recorded no-launch prevention,
      re-reported as CANCELLED on every later wait).
  B4  the launch invocation itself is THE IRREVOCABLE BOUNDARY. Once
      the bound launch_argv child has been spawned, a cancel can no
      longer prevent anything: only the launch's own recorded outcome
      stands (STARTED / LAUNCH_FAILED / LAUNCH_UNKNOWN, or lost-ack
      UNKNOWN), and no later wait may upgrade or re-issue it.

Receipts record their PRODUCING outcome (``outcome``: ``STARTED`` |
``LAUNCH_FAILED`` | ``LAUNCH_UNKNOWN``) together with rc and full
identity (task_id / binding_sha256 / frozen / claim_id). Every later
wait re-reports STRICTLY the outcome recorded inside the receipt (L3):
a failed receipt stays NO_GO, an unknown receipt stays UNKNOWN, a
legacy/foreign receipt (no outcome, wrong task/binding/claim/frozen,
or outcome inconsistent with rc) is rejected to UNKNOWN, and only
``outcome=STARTED`` with ``launch_returncode == 0`` and intact
identity proves STARTED. A launch rc==0 additionally requires start
EVIDENCE (L4): the receipt written and read back intact, the on-disk
claim still matching, and the bound ``campaign.json`` present at
exactly the pinned ``new_campaign_dir`` as a valid record; any gap
means outcome LAUNCH_UNKNOWN and state UNKNOWN - rc0 alone is never
start evidence. Missing/incomplete receipts stay UNKNOWN and are never
re-issued; human re-adjudication is separate from automatic re-issue.

N2/N3 integration points RESERVED this wave (linker-side patches from
those nodes are integrated by N1 in the next wave): the launcher layer
(launch-rv05.ps1) must mirror B1/B3 inside itself before ITS inner
claim - the ``boundary_gate`` map in the started receipt records the
linker-side gate verdicts for that mirror, and the receipt outcome
vocabulary above is the cross-layer contract; N3's bounded-read /
short-write / Win32 tri-state patches attach to ``_read_capped`` and
the claim write loop without moving these boundaries.

Binding manifest (schema ``pal-rv05-linker-binding-v1``): the complete,
hash-pinned identity the linker is allowed to start. Validated at arm
time AND re-verified on every poll cycle: placeholders, unknown schema,
unknown/missing/duplicate fields, wrong types, non-absolute paths,
inconsistent argv and any deviation from the approved time limits are
REJECTED (exit 7 at arm; NO_GO drift if the world changed after arm).
Time limits are checked item-by-item against the plan-approved values
compiled in APPROVED_LIMITS; an unexplained relaxation is LISTED and NOT
accepted. START conditions covered by the bound preflight run: capacity
reachability, control-test/gate completeness, frozen-tree integrity and
resources (the preflight is the single comprehensive GO gate; the
binding pins are the control-package identity layer above it).

Real-arm integration map (filled by N0/N2 after the distributable
control package is fixed; NOT armed by this wave - tests are synthetic):

  linker/launcher/preflight/launch_spec  -> the N2-delivered control
      package files (launcher code is owned by this linker contract;
      hashes pinned per file);
  contract/errata/generator/manifest/config/runtime -> the frozen
      candidate evidence chain (same pins as
      ``launch-final\\launch-spec-rv05-candidate.json`` binding +
      ERRATA-001.md);
  preflight_argv -> [runtime, -I, -S, -B, preflight, --spec,
      launch_spec, --json-out, preflight_report_path]
      (+ ['--execution-id', <fresh per-cycle uuid>] appended at runtime)
  launch_argv -> [powershell, -NoProfile, -NonInteractive,
      -ExecutionPolicy, RemoteSigned, -File, launcher, -SpecPath,
      launch_spec]

  argv/pin cross-checks (fix wave N2 spec, replacing the older membership
  cross-checks): the argv lists are validated as STRUCTURED POSITION-EXACT
  whitelists - the interpreter (the optional pinned ``launch_interpreter``
  or the canonical System32 WindowsPowerShell default) must be argv[0],
  the switch set must match an approved form element-for-element with
  RemoteSigned the only permitted -ExecutionPolicy value, -File must be
  followed EXACTLY by the pinned launcher and -SpecPath by the pinned
  launch spec, with nothing after it; the preflight argv is the fixed
  9-token shape above. -ExecutionPolicy Bypass/Unrestricted, -Command,
  -EncodedCommand, stdin scripts and synthetic hooks (--now-utc,
  --stop-after) are rejected outright (L5/L6). Formal child runs get a
  closed environment and a pinned cwd at both subprocess call sites.

Usage (stdlib only; python -I -S -B):

  soak_linker.py arm    --binding B.json [--now-utc T]
  soak_linker.py wait   --binding B.json [--once | --interval S
                        --max-cycles N] [--now-utc T]
                        [--stop-after {never,claim}]
  soak_linker.py cancel --binding B.json [--note TEXT] [--now-utc T]
  soak_linker.py status --binding B.json
  soak_linker.py mark-final-review-ready --binding B.json [--now-utc T]

--now-utc is the SYNTHETIC clock hook for tests (ISO-8601 Z); when set,
the poll loop never sleeps and interval bounds are not enforced. Real
arm runs never pass it. Default poll interval is >= 300 s (from the
binding; CLI override below 300 s requires --now-utc).

Exit codes:
  0  cycle completed in a waitable/OK state (ARMED_WAITING continue,
     CANCELLED, STARTED idempotent, FINAL_REVIEW_READY)
  2  NO_GO (terminal, typed reason)
  3  claim competition lost (another instance holds the claim)
  4  EXPIRED (window closed / total deadline passed without a start)
  5  UNKNOWN (terminal; lost-ack, PID reuse, unreadable or corrupted
     evidence, backward clock jump)
  6  invalid usage or refused transition (not armed, wrong state,
     cancel after claim, ...)
  7  binding rejected (placeholder/unknown schema/wrong type/unknown or
     missing field/time-limit deviation)

Resource discipline: at most ONE child subprocess at any time (the
preflight run, later the launch run - never concurrent), bounded record
reads (64 KiB), bounded campaign scans (200 segments / 5000 rounds like
the preflight), bounded state check history (last 100 cycles), bounded
poll loop (max-cycles, hard-stops at the deadline). This process never
terminates, kills or enumerates processes system-wide; the only process
probes are read-only liveness/creation-time queries of the single PID
recorded in the original campaign's lock record.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from hashlib import sha256
from pathlib import Path

SCHEMA_BINDING = 'pal-rv05-linker-binding-v1'
SCHEMA_PREFLIGHT_REPORT = 'pal-rv05-preflight-v1'
SCHEMA_STATE = 'pal-rv05-linker-state-v1'
SCHEMA_CLAIM = 'pal-rv05-linker-claim-v1'
SCHEMA_STARTED_RECEIPT = 'pal-rv05-linker-started-receipt-v1'
SCHEMA_ARMED_RECEIPT = 'pal-rv05-linker-armed-receipt-v1'

STATES = ('UNARMED', 'ARMED_WAITING', 'PRECHECK', 'CLAIMED', 'STARTED',
          'FINAL_REVIEW_READY', 'CANCELLED', 'EXPIRED', 'NO_GO', 'UNKNOWN')
TERMINAL = {'CANCELLED', 'EXPIRED', 'NO_GO', 'UNKNOWN', 'FINAL_REVIEW_READY'}
STATE_EXIT = {'ARMED_WAITING': 0, 'STARTED': 0, 'CANCELLED': 0,
              'FINAL_REVIEW_READY': 0, 'EXPIRED': 4, 'NO_GO': 2,
              'UNKNOWN': 5}

EXIT_OK, EXIT_NO_GO, EXIT_CLAIM_LOST, EXIT_EXPIRED, EXIT_UNKNOWN, \
    EXIT_USAGE, EXIT_BINDING = 0, 2, 3, 4, 5, 6, 7

# Receipt outcome vocabulary (L3/L4 fix wave): a receipt records the
# state its PRODUCING launch attempt ended in; later waits re-report
# strictly this recorded outcome and never upgrade it.
RECEIPT_OUTCOME_STARTED = 'STARTED'
RECEIPT_OUTCOME_LAUNCH_FAILED = 'LAUNCH_FAILED'
RECEIPT_OUTCOME_LAUNCH_UNKNOWN = 'LAUNCH_UNKNOWN'
# Reason strings for a recorded no-launch prevention (abort after the
# claim, before the launch invocation) re-reported on later waits.
NO_LAUNCH_REASONS = {
    'CANCELLED': 'operator_cancel_after_claim_no_launch',
    'EXPIRED': 'window_closed_after_claim_no_launch',
    'NO_GO': 'binding_drift_after_claim_no_launch',
}

# Plan-approved limits (PAL_RV05_CLOSURE_20260922 plan section 6). The
# binding must carry EXACTLY these values; any deviation (in particular a
# relaxation: later latest/deadline, shorter stability, faster polling) is
# listed and rejected - "unexplained relaxations are not auto-accepted".
APPROVED_LIMITS = {
    'launch_window_utc.earliest': '2026-09-24T02:14:50Z',
    'launch_window_utc.latest': '2026-09-24T12:36:41Z',
    'deadline_utc': '2026-09-28T00:36:41Z',
    'evidence_stability_seconds': 900,
    'poll_interval_seconds_min': 300,
}

DEFAULT_POLL_INTERVAL = 300
PREFLIGHT_TIMEOUT_S = 600
LAUNCH_TIMEOUT_S = 900
RECORD_MAX_BYTES = 64 * 1024
# A GO report older than the preflight spawn moment (minus this mtime
# tolerance) is a stale leftover from an earlier invocation, not bound
# to THIS preflight call (L2 pass condition: "a previous GO never
# substitutes the live launch boundary").
REPORT_FRESHNESS_TOLERANCE_S = 2.0
MAX_CHECK_HISTORY = 100
MAX_TRANSITIONS = 100
MAX_SEGMENTS_SCAN = 200
MAX_ROUNDS_SCAN = 5000
CLOCK_BACKWARD_TOLERANCE_S = 1.0
PID_IDENTITY_TOLERANCE_S = 5.0

_HEX64 = re.compile(r'\A[0-9a-f]{64}\Z')
_HEX40 = re.compile(r'\A[0-9a-f]{40}\Z')
_PLACEHOLDER_WORDS = {'', 'todo', 'tbd', 'placeholder', 'fill', 'fill-me',
                      'n/a', 'na', 'null', 'none', 'pending', 'unknown',
                      'xxx', 'xxxx', 'xxxxx', 'tbc', '?', '-'}
_PLACEHOLDER_PATTERNS = (
    re.compile(r'<[^>]*>'), re.compile(r'\{\{[^}]*\}\}'),
    re.compile(r'\b0{16,}\b'), re.compile(r'\b([0-9a-f])\1{15,}\b'))

PINNED_FILE_FIELDS = (
    'linker', 'launcher', 'preflight', 'launch_spec', 'contract', 'errata',
    'generator', 'manifest', 'config')
CONTROL_DIR_FIELDS = ('claim_path', 'state_path', 'started_receipt_path',
                      'linker_stop_path', 'preflight_report_path')
REQUIRED_KEYS = ('schema', 'task_id', *PINNED_FILE_FIELDS, 'runtime',
                 'frozen', 'original_campaign', 'new_campaign_root',
                 'new_campaign_dir', *CONTROL_DIR_FIELDS, 'launch_argv',
                 'preflight_argv', 'launch_window_utc', 'deadline_utc',
                 'poll_interval_seconds', 'evidence_stability_seconds')


class BindingError(Exception):
    """Binding manifest rejected (placeholder/type/unknown/limits)."""


class Refused(Exception):
    """Invalid usage or refused transition."""


class ClaimWriteError(Exception):
    """The one-shot claim could not be confirmed fully written (short or
    torn write, failed fsync, readback mismatch). The slot must be
    treated as consumed -> UNKNOWN, never re-attempted, never deleted."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_utc(text: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(str(text).replace('Z', '+00:00'))


def _sha256_file(path: Path) -> str | None:
    digest = sha256()
    try:
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _read_capped(path: Path, max_bytes: int = RECORD_MAX_BYTES):
    """Bounded binary read -> bytes | None(absent) | 'UNREADABLE' |
    'OVER_LIMIT'.

    Reads max_bytes PLUS ONE probe byte: a file larger than the cap is
    REJECTED as 'OVER_LIMIT', never truncated-and-parsed. Without the
    probe, a legal JSON prefix of exactly max_bytes followed by an
    illegal tail was silently truncated to the prefix and accepted as
    the complete record (L7)."""
    try:
        with open(path, 'rb') as handle:
            raw = handle.read(max_bytes + 1)
    except FileNotFoundError:
        return None
    except OSError:
        return 'UNREADABLE'
    if len(raw) > max_bytes:
        return 'OVER_LIMIT'
    return raw


def _reject_record_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f'duplicate JSON key: {key}')
        seen[key] = value
    return seen


def _reject_nonstandard_constant(name):
    # json calls this only for NaN / Infinity / -Infinity literals.
    raise ValueError(f'non-standard numeric constant rejected: {name}')


def _reject_nonfinite_floats(doc):
    # Strict JSON has no non-finite numbers; a legal-looking exponent
    # such as 1e999 parses to float inf and is rejected after the fact.
    stack = [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, float):
            if not math.isfinite(node):
                raise ValueError('non-finite float value rejected')
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return doc


def _read_json_record(path: Path):
    """-> (doc | None, reason) with reason in {'', 'absent', 'unreadable',
    'over_limit', 'invalid'}; never raises. Distinguishes absent from
    unreadable so a permission problem stays UNKNOWN instead of looking
    like a clean exit. Strict parse (L7): duplicate keys at any depth,
    NaN/Infinity/-Infinity constants, non-finite floats and non-object
    top levels are 'invalid' (a non-object doc would otherwise crash
    read_state with AttributeError). 'over_limit' is its own reason so
    an over-cap file can never be laundered into absent/invalid
    handling."""
    raw = _read_capped(path)
    if raw is None:
        return None, 'absent'
    if raw == 'UNREADABLE':
        return None, 'unreadable'
    if raw == 'OVER_LIMIT':
        return None, 'over_limit'
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return None, 'invalid'
    try:
        doc = json.loads(
            text,
            object_pairs_hook=_reject_record_duplicate_keys,
            parse_constant=_reject_nonstandard_constant)
        _reject_nonfinite_floats(doc)
    except (ValueError, RecursionError):
        # RecursionError: pathological nesting depth in the C scanner.
        return None, 'invalid'
    if not isinstance(doc, dict):
        return None, 'invalid'
    return doc, ''


def _atomic_write_json(path: Path, doc) -> None:
    tmp = path.with_name(path.name + '.tmp')
    data = (json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)
            + '\n').encode('utf-8')
    with open(tmp, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _looks_like_placeholder(value) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text.lower() in _PLACEHOLDER_WORDS:
        return True
    return any(p.search(text) for p in _PLACEHOLDER_PATTERNS)


# Win32 constants for the read-only process probes (full ABI below).
_WIN_SYNCHRONIZE = 0x00100000
_WIN_QUERY_LIMITED = 0x1000
_WIN_WAIT_OBJECT_0 = 0
_WIN_WAIT_TIMEOUT = 258
_WIN_WAIT_FAILED = 0xFFFFFFFF
_WIN_ERROR_ACCESS_DENIED = 5
_WIN_ERROR_INVALID_PARAMETER = 87

_WIN_ABI = None  # lazy (kernel32, FILETIME-class) with the ABI applied


def _win_kernel32():
    """-> (kernel32, FILETIME class); kernel32 loaded with
    use_last_error=True and the COMPLETE ctypes ABI declared for every
    Win32 function used (OpenProcess / WaitForSingleObject / CloseHandle
    / GetProcessTimes / GetLastError).

    Undeclared restype defaults to C int: a 64-bit HANDLE return would
    be truncated and WAIT_FAILED (0xFFFFFFFF) would arrive as -1.
    use_last_error=True + ctypes.get_last_error() is the race-free
    GetLastError snapshot (a later direct GetLastError call can observe
    a clobbered slot); GetLastError is still ABI-declared for
    completeness."""
    global _WIN_ABI
    if _WIN_ABI is None:
        import ctypes

        class _FT(ctypes.Structure):
            _fields_ = [('lo', ctypes.c_uint32), ('hi', ctypes.c_uint32)]

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32,  # access
                                         ctypes.c_int,     # inherit
                                         ctypes.c_uint32]  # pid
        kernel32.OpenProcess.restype = ctypes.c_void_p      # HANDLE, 64-bit
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p,
                                                 ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32  # unsigned
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_FT), ctypes.POINTER(_FT),
            ctypes.POINTER(_FT), ctypes.POINTER(_FT)]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = ctypes.c_uint32
        _WIN_ABI = (kernel32, _FT)
    return _WIN_ABI


def _win_classify_openprocess_error(code: int) -> str:
    """Reason for an OpenProcess NULL. Every failure shape -> unknown."""
    if code == _WIN_ERROR_ACCESS_DENIED:
        return (f'openprocess_access_denied({_WIN_ERROR_ACCESS_DENIED}): '
                'permission unknown; no elevation attempted')
    if code == _WIN_ERROR_INVALID_PARAMETER:
        return (f'openprocess_invalid_parameter'
                f'({_WIN_ERROR_INVALID_PARAMETER}): possibly nonexistent '
                'pid; NOT trusted exit evidence')
    return f'openprocess_null:gle={code}'


def _win_liveness(pid, dll=None, get_error=None):
    """Windows tri-state liveness for ONE pid -> (state, reason) with
    state in {'alive', 'dead', 'unknown'}.

    ONLY WaitForSingleObject == WAIT_OBJECT_0 (the process object is
    signaled) proves exit; WAIT_TIMEOUT (258) means alive. OpenProcess
    NULL (classified by GetLastError; access denied stays a permission
    unknown and is NEVER escalated - this probe does not raise
    privileges), WAIT_FAILED, unexpected codes and call errors are all
    unknown: a failed query can never fabricate an exit (L8/L9).

    Handle ownership: the handle OpenProcess returned is closed exactly
    once (finally); a NULL is never closed; a failed wait does NOT free
    the handle, so the close still runs; a CloseHandle failure is
    recorded without a second attempt. dll/get_error are the injection
    seam for tests (fakes record calls)."""
    if type(pid) is not int or pid <= 0:
        return 'unknown', 'invalid_pid'
    real_dll = dll is None
    if dll is None:
        dll, _ft = _win_kernel32()
    if get_error is None:
        import ctypes
        get_error = (ctypes.get_last_error if real_dll
                     else (lambda: getattr(dll, 'last_error', 0)))
    try:
        handle = dll.OpenProcess(_WIN_SYNCHRONIZE, 0, pid)
    except OSError as error:
        return 'unknown', f'openprocess_error:{error}'
    if not handle:
        try:
            code = int(get_error())
        except (TypeError, ValueError):
            code = -1
        return 'unknown', _win_classify_openprocess_error(code)
    try:
        status = dll.WaitForSingleObject(handle, 0)
        if status == _WIN_WAIT_TIMEOUT:
            return 'alive', 'wait_timeout(258)'
        if status == _WIN_WAIT_OBJECT_0:
            return 'dead', 'wait_object_0(0): process object signaled'
        if status == _WIN_WAIT_FAILED:
            try:
                code = int(get_error())
            except (TypeError, ValueError):
                code = -1
            return 'unknown', f'wait_failed(0xFFFFFFFF):gle={code}'
        return 'unknown', f'wait_unexpected:{status!r}'
    except OSError as error:
        return 'unknown', f'wait_error:{error}'
    finally:
        try:
            dll.CloseHandle(handle)
        except OSError:
            pass


def _win_start_utc(pid, dll=None, get_error=None):
    """Windows creation time for ONE pid -> aware UTC datetime | None
    (unknown). Full ABI; OpenProcess NULL or a failed GetProcessTimes
    is unknown (never a fabricated timestamp); same no-elevation and
    single-close rules as _win_liveness."""
    if type(pid) is not int or pid <= 0:
        return None
    import ctypes
    real_dll = dll is None
    if dll is None:
        dll, ft_class = _win_kernel32()
    else:
        class _FT(ctypes.Structure):  # injection-seam FILETIME
            _fields_ = [('lo', ctypes.c_uint32), ('hi', ctypes.c_uint32)]
        ft_class = _FT
    if get_error is None:
        get_error = (ctypes.get_last_error if real_dll
                     else (lambda: getattr(dll, 'last_error', 0)))
    try:
        handle = dll.OpenProcess(_WIN_QUERY_LIMITED, 0, pid)
    except OSError:
        return None
    if not handle:
        return None  # any error code stays unknown; no elevation
    try:
        creation, exit_t, kernel_t, user_t = (ft_class(), ft_class(),
                                              ft_class(), ft_class())
        ok = dll.GetProcessTimes(handle, creation, exit_t,
                                 kernel_t, user_t)
        if not ok:
            return None
        total = (creation.hi << 32) | creation.lo
        if total == 0:
            return None
        unix = total / 1e7 - 11644473600.0
        return _dt.datetime.fromtimestamp(unix, _dt.timezone.utc)
    except (OSError, AttributeError, TypeError):
        return None
    finally:
        try:
            dll.CloseHandle(handle)
        except OSError:
            pass


def _pid_alive(pid) -> bool | None:
    """Read-only liveness probe for ONE recorded PID (same contract as the
    frozen driver's helper; None = unknown)."""
    if type(pid) is not int or pid <= 0:
        return None
    if os.name == 'nt':
        state, _reason = _win_liveness(pid)
        if state == 'alive':
            return True
        if state == 'dead':
            return False
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _process_start_utc(pid) -> _dt.datetime | None:
    """Creation time of ONE recorded PID (read-only; None = unknown).

    Identity companion to the liveness probe: the original driver's lock
    records (pid, wall). A live PID whose creation time does NOT match the
    recorded wall is a REUSED pid -> ownership cannot be confirmed ->
    UNKNOWN. None keeps UNKNOWN as well (never a bare-PID conclusion)."""
    if type(pid) is not int or pid <= 0:
        return None
    if os.name == 'nt':
        return _win_start_utc(pid)
    try:
        fields = Path(f'/proc/{pid}/stat').read_text(
            encoding='utf-8').rsplit(')', 1)[1].split()
        start_ticks = int(fields[19])
        boot = None
        for line in Path('/proc/stat').read_text(encoding='utf-8').splitlines():
            if line.startswith('btime '):
                boot = int(line.split()[1])
        if boot is None:
            return None
        hz = os.sysconf('SC_CLK_TCK')
        return _dt.datetime.fromtimestamp(
            boot + start_ticks / hz, _dt.timezone.utc)
    except (OSError, ValueError, IndexError):
        return None


# --------------------------------------------------------------------------
# binding manifest
# --------------------------------------------------------------------------

def _no_duplicate_keys(pairs):
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise BindingError(f'duplicate JSON key: {key}')
        seen.add(key)
    return dict(pairs)


def _require_str(container, key, where, absolute=False):
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BindingError(f'{where}.{key}: must be a non-empty string')
    if _looks_like_placeholder(value):
        raise BindingError(
            f'{where}.{key}: placeholder value rejected: {value!r}')
    if absolute and not os.path.isabs(value):
        raise BindingError(f'{where}.{key}: must be an absolute path: {value}')
    return value


def _require_pin(binding, field, must_exist):
    pin = binding.get(field)
    if not isinstance(pin, dict) or set(pin) != {'path', 'sha256'}:
        raise BindingError(
            f'{field}: must be exactly {{path, sha256}} (unknown or missing '
            f'sub-fields rejected); got keys '
            f'{sorted(pin) if isinstance(pin, dict) else type(pin).__name__}')
    path = _require_str(pin, 'path', field, absolute=True)
    digest = pin.get('sha256')
    if not isinstance(digest, str) or not _HEX64.match(digest):
        raise BindingError(f'{field}.sha256: must be a 64-char lowercase hex '
                           f'string; got {digest!r}')
    if _looks_like_placeholder(digest):
        raise BindingError(f'{field}.sha256: placeholder value rejected')
    if must_exist and not Path(path).exists():
        raise BindingError(f'{field}.path: pinned file does not exist: {path}')
    return path, digest


# ---------------------------------------------------------------------------
# structured launch/preflight argv whitelist (fix wave N2 spec, L5/L6).
# The old string-membership cross-checks ("the pinned path appears somewhere
# in argv") are REPLACED by position-exact structure validation: WHICH
# executable runs, WHICH switch set, WHERE -File points, and what may never
# appear anywhere. A launch_argv that executes an unrelated command while
# merely MENTIONING the pinned launcher is rejected (L5); -ExecutionPolicy
# Bypass/Unrestricted and every alternative execution form (-Command,
# -EncodedCommand, stdin scripts) are rejected outright (L6).
# ---------------------------------------------------------------------------

# argv[1:1+k] must equal ONE of these, element-for-element, case-sensitive,
# IN ORDER. RemoteSigned is the ONLY allowed -ExecutionPolicy value.
APPROVED_LAUNCH_SWITCH_SETS = (
    ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'RemoteSigned',
     '-File'],
    ['-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File'],
)
# Exact case-insensitive TOKEN equality (never substring: a path that merely
# contains e.g. "bypass" in a filename must not false-positive). Scanned over
# EVERY token of both argv lists, independent of the positional whitelist.
FORBIDDEN_TOKENS_CI = frozenset({
    '-command', '-encodedcommand', '-encodedjavascript', '-stdin',
    '--now-utc', '--stop-after', 'bypass', 'unrestricted',
})
# Formal child runs get a CLOSED environment: the OS/PATH basics a pinned
# script's own children need, plus TEMP/TMP redirected into the control
# directory's run-temp. PYTHON* / PAL_RV05_* / test knobs never propagate.
KEEP_ENV = ('SystemRoot', 'SYSTEMDRIVE', 'ComSpec', 'PATHEXT', 'PATH',
            'OS', 'WINDIR', 'NUMBER_OF_PROCESSORS',
            'PROCESSOR_ARCHITECTURE', 'PROCESSOR_IDENTIFIER',
            'PROCESSOR_LEVEL', 'PROCESSOR_REVISION')
# Optional binding key: {"path", "sha256"} pin of the PowerShell interpreter
# a real arm SHOULD carry. Absent -> the canonical System32 WindowsPowerShell
# host default (recorded choice, existence-checked).
OPTIONAL_BINDING_KEYS = ('launch_interpreter',)
# The bound preflight's static argv shape: exactly 9 tokens. The linker
# appends the per-cycle execution id AT RUNTIME only (never a binding value).
PREFLIGHT_ARGV_LEN = 9


def _norm_ci(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def default_launch_interpreter_path() -> str:
    """Canonical host Windows PowerShell (System32), or the C:\\Windows
    fallback when SystemRoot is unset OR EMPTY (an empty value would
    otherwise join into a broken relative path; non-Windows test hosts
    should pin launch_interpreter explicitly)."""
    return os.path.join(os.environ.get('SystemRoot') or r'C:\Windows',
                        r'System32\WindowsPowerShell\v1.0\powershell.exe')


def _launch_interpreter_path(binding: dict) -> str:
    """Resolve the interpreter argv[0] must equal: the OPTIONAL
    ``launch_interpreter`` pin (validated with _require_pin semantics plus
    live sha256 match) or, when absent, the canonical System32 default
    (existence-checked). The choice is deterministic per binding load."""
    interp = binding.get('launch_interpreter')
    if interp is not None:
        path, digest = _require_pin(binding, 'launch_interpreter',
                                    must_exist=True)
        live = _sha256_file(Path(path))
        if live is None or live != digest:
            raise BindingError(
                f'launch_interpreter: sha256 drift (pinned {digest}, '
                f'live {live}); the pinned interpreter changed on disk')
        return path
    path = default_launch_interpreter_path()
    if not Path(path).is_file():
        raise BindingError(
            f'launch interpreter missing: {path} (pin launch_interpreter '
            'explicitly on hosts without the System32 WindowsPowerShell '
            'default)')
    return path


def validate_launch_argv(binding: dict) -> None:
    """L5/L6: position-exact launch_argv whitelist. Raises BindingError with
    the offending contract in the message; returns None when conformant.
    Re-run cheaply at the START boundary to catch binding drift."""
    argv = binding['launch_argv']
    interp_path = _launch_interpreter_path(binding)
    if _norm_ci(argv[0]) != _norm_ci(interp_path):
        raise BindingError(
            f'launch_argv[0] must be the pinned PowerShell interpreter '
            f'(absolute path: {interp_path}), nothing else (L5: the '
            'interpreter is the EXECUTED program, not a mentionable '
            f'string); got {argv[0]!r}')
    for switches in APPROVED_LAUNCH_SWITCH_SETS:
        k = len(switches)
        if argv[1:1 + k] == switches:
            break
    else:
        raise BindingError(
            'launch_argv switches must be EXACTLY one of '
            f'{list(APPROVED_LAUNCH_SWITCH_SETS)} (element-for-element, in '
            'order) - no Bypass/Unrestricted, no -Command/-EncodedCommand, '
            'no extra or missing switches (L5/L6)')
    tail = argv[1 + k:]
    if len(tail) != 3 or tail[1] != '-SpecPath':
        raise BindingError(
            'launch_argv: after -File exactly [launcher, -SpecPath, '
            'launch_spec] is allowed (nothing after the spec path; no '
            '-DryRun, no second spec, no extra parameters); got tail '
            f'{tail!r}')
    if _norm_ci(tail[0]) != _norm_ci(binding['launcher']['path']):
        raise BindingError(
            'launch_argv: -File must be followed EXACTLY by the pinned '
            'launcher path (L5: the launcher must be the EXECUTED script, '
            'not an unused argument); got '
            f'{tail[0]!r} != {binding["launcher"]["path"]!r}')
    if _norm_ci(tail[2]) != _norm_ci(binding['launch_spec']['path']):
        raise BindingError(
            'launch_argv: -SpecPath must be followed EXACTLY by the pinned '
            'launch spec; got '
            f'{tail[2]!r} != {binding["launch_spec"]["path"]!r}')
    for key in ('launch_argv', 'preflight_argv'):
        for tok in binding[key]:
            if tok.strip().lower() in FORBIDDEN_TOKENS_CI:
                raise BindingError(
                    f'{key}: forbidden token {tok!r} (synthetic hook / '
                    'policy bypass / alternative execution form)')


def validate_preflight_argv(binding: dict) -> None:
    """L5: the bound preflight argv is STATIC and must be exactly
    [runtime, -I, -S, -B, preflight, --spec, launch_spec, --json-out,
    preflight_report_path] (length 9; flag positions exact-string; path
    positions normcase+normpath equal to the pinned values). The old
    containment checks are subsumed."""
    argv = binding['preflight_argv']
    flags = ['-I', '-S', '-B']
    if len(argv) != PREFLIGHT_ARGV_LEN:
        raise BindingError(
            f'preflight_argv: must be exactly {PREFLIGHT_ARGV_LEN} tokens '
            f'[runtime, -I, -S, -B, preflight, --spec, launch_spec, '
            '--json-out, report] (the per-cycle --execution-id is appended '
            f'by the linker at runtime, never a binding value); got {argv!r}')
    if _norm_ci(argv[0]) != _norm_ci(binding['runtime']['python_exe']):
        raise BindingError(
            'preflight_argv[0] must equal runtime.python_exe '
            f'(got {argv[0]!r})')
    if argv[1:4] != flags:
        raise BindingError(
            f'preflight_argv[1:4] must be exactly {flags} (sanitized '
            f'interpreter flags); got {argv[1:4]!r}')
    if _norm_ci(argv[4]) != _norm_ci(binding['preflight']['path']):
        raise BindingError(
            'preflight_argv[4] must be EXACTLY the pinned preflight script '
            f'(got {argv[4]!r} != {binding["preflight"]["path"]!r})')
    if argv[5] != '--spec' or _norm_ci(argv[6]) != _norm_ci(
            binding['launch_spec']['path']):
        raise BindingError(
            'preflight_argv[5:7] must be exactly [--spec, pinned '
            f'launch_spec]; got {argv[5:7]!r}')
    if argv[7] != '--json-out' or _norm_ci(argv[8]) != _norm_ci(
            binding['preflight_report_path']):
        raise BindingError(
            'preflight_argv[7:9] must be exactly [--json-out, pinned '
            f'preflight_report_path]; got {argv[7:9]!r}')


def runtime_preflight_argv(binding: dict, execution_id: str) -> list:
    """The argv the PRECHECK actually spawns: the validated static 9-token
    whitelist PLUS this cycle's fresh execution id (32 hex). The id is
    generated per cycle and is NEVER a binding value; the preflight echoes
    it inside its report's execution block, binding the report to THIS
    invocation (fresh-report contract)."""
    if not isinstance(execution_id, str) or not execution_id:
        raise Refused('execution_id must be a non-empty string')
    validate_preflight_argv(binding)
    return list(binding['preflight_argv']) + ['--execution-id', execution_id]


def _sanitized_run_env(temp_dir: Path) -> dict:
    """Closed child environment (fix wave N2): KEEP_ENV basics + TEMP/TMP
    pinned into the control run-temp. Nothing else from the parent process
    - in particular no PYTHON* / PAL_RV05_* / test knobs - propagates into
    a formal preflight or launch run."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in KEEP_ENV if key in os.environ}
    env['TEMP'] = env['TMP'] = str(temp_dir)
    return env


def load_binding(binding_path: Path) -> dict:
    text = _read_capped(binding_path, 1024 * 1024)
    if text is None or text == 'UNREADABLE':
        raise BindingError(f'binding manifest unreadable: {binding_path}')
    if text == 'OVER_LIMIT':
        # over-limit probe: a 1 MiB+ manifest is rejected, never
        # truncated to a legal prefix and parsed (L7).
        raise BindingError(f'binding manifest exceeds the 1 MiB '
                           f'bounded-read limit: {binding_path}')
    try:
        binding = json.loads(text.decode('utf-8'),
                             object_pairs_hook=_no_duplicate_keys,
                             parse_constant=_reject_nonstandard_constant)
        _reject_nonfinite_floats(binding)
    except (ValueError, UnicodeDecodeError) as error:
        raise BindingError(f'binding manifest invalid JSON: {error}') from error
    if not isinstance(binding, dict):
        raise BindingError('binding manifest must be a JSON object')
    if binding.get('schema') != SCHEMA_BINDING:
        raise BindingError(
            f'schema: unknown binding schema {binding.get("schema")!r}; '
            f'this linker accepts exactly {SCHEMA_BINDING!r}')
    missing = [k for k in REQUIRED_KEYS if k not in binding]
    unknown = [k for k in binding
               if k not in REQUIRED_KEYS + OPTIONAL_BINDING_KEYS]
    if missing or unknown:
        raise BindingError(
            f'binding field set mismatch (missing={missing}, '
            f'unknown={unknown}); the binder must pin the complete '
            f'identity - unknown versions or partial bindings are rejected')
    _require_str(binding, 'task_id', 'binding')
    for field in PINNED_FILE_FIELDS:
        _require_pin(binding, field, must_exist=True)

    runtime = binding['runtime']
    if not isinstance(runtime, dict) or set(runtime) != {'python_exe',
                                                         'sha256'}:
        raise BindingError('runtime: must be exactly {python_exe, sha256}')
    python_exe = _require_str(runtime, 'python_exe', 'runtime', absolute=True)
    py_digest = runtime.get('sha256')
    if not isinstance(py_digest, str) or not _HEX64.match(py_digest):
        raise BindingError('runtime.sha256: must be 64-char lowercase hex')
    if not Path(python_exe).exists():
        raise BindingError(f'runtime.python_exe does not exist: {python_exe}')

    frozen = binding['frozen']
    if not isinstance(frozen, dict) or set(frozen) != {'commit', 'tree'}:
        raise BindingError('frozen: must be exactly {commit, tree}')
    for key in ('commit', 'tree'):
        value = frozen.get(key)
        if not isinstance(value, str) or not _HEX40.match(value):
            raise BindingError(
                f'frozen.{key}: must be a 40-char lowercase hex SHA-1; '
                f'got {value!r}')

    for key in ('original_campaign', 'new_campaign_root', 'new_campaign_dir',
                *CONTROL_DIR_FIELDS):
        _require_str(binding, key, 'binding', absolute=True)
    if not Path(binding['original_campaign']).is_dir():
        raise BindingError('original_campaign: directory does not exist: '
                           f'{binding["original_campaign"]}')

    # one control directory for every writable control artifact; it must be
    # disjoint from both campaign roots (write-scope check).
    parents = {Path(binding[key]).parent.resolve()
               for key in CONTROL_DIR_FIELDS}
    if len(parents) != 1:
        raise BindingError(
            f'control paths must share ONE control directory; got {sorted(map(str, parents))}')
    control_dir = parents.pop()
    for label in ('original_campaign', 'new_campaign_root',
                  'new_campaign_dir'):
        target = Path(binding[label]).resolve()
        if control_dir == target or control_dir in target.parents \
                or target in control_dir.parents:
            raise BindingError(
                f'control directory overlaps {label}: {control_dir} vs '
                f'{target}; the linker write scope must be disjoint')

    for key in ('launch_argv', 'preflight_argv'):
        argv = binding[key]
        if not isinstance(argv, list) or not argv \
                or not all(isinstance(part, str) and part.strip()
                           for part in argv):
            raise BindingError(
                f'{key}: must be a non-empty list of non-empty strings '
                f'(fixed argv; no shell string)')
        if any(_looks_like_placeholder(part) for part in argv):
            raise BindingError(f'{key}: placeholder component rejected')
    if binding['preflight_argv'][0] != python_exe:
        raise BindingError('preflight_argv[0] must equal runtime.python_exe')
    # L5/L6 (fix wave N2 spec): the position-exact structured whitelists
    # REPLACE the string-membership cross-checks. validate_launch_argv also
    # resolves the optional launch_interpreter pin (or the System32 default)
    # and validates its live sha256.
    validate_preflight_argv(binding)
    validate_launch_argv(binding)

    window = binding['launch_window_utc']
    if not isinstance(window, dict) or set(window) != {'earliest', 'latest'}:
        raise BindingError('launch_window_utc: must be exactly '
                           '{earliest, latest}')
    deviations = []
    for key in ('earliest', 'latest'):
        value = _require_str(window, key, 'launch_window_utc')
        try:
            parse_utc(value)
        except ValueError as error:
            raise BindingError(
                f'launch_window_utc.{key}: invalid ISO-8601: {error}') from error
        approved = parse_utc(APPROVED_LIMITS[f'launch_window_utc.{key}'])
        if parse_utc(value) != approved:
            deviations.append(f'launch_window_utc.{key}: {value} != approved '
                              f'{APPROVED_LIMITS[f"launch_window_utc.{key}"]}')
    deadline = _require_str(binding, 'deadline_utc', 'binding')
    try:
        parse_utc(deadline)
    except ValueError as error:
        raise BindingError(f'deadline_utc: invalid ISO-8601: {error}') from error
    if parse_utc(deadline) != parse_utc(APPROVED_LIMITS['deadline_utc']):
        deviations.append(f'deadline_utc: {deadline} != approved '
                          f'{APPROVED_LIMITS["deadline_utc"]}')

    stability = binding['evidence_stability_seconds']
    if type(stability) is not int or isinstance(stability, bool):
        raise BindingError('evidence_stability_seconds: must be an integer')
    if stability != APPROVED_LIMITS['evidence_stability_seconds']:
        deviations.append(
            f'evidence_stability_seconds: {stability} != approved '
            f'{APPROVED_LIMITS["evidence_stability_seconds"]}')
    interval = binding['poll_interval_seconds']
    if type(interval) is not int or isinstance(interval, bool) \
            or interval < APPROVED_LIMITS['poll_interval_seconds_min']:
        raise BindingError(
            'poll_interval_seconds: must be an integer >= '
            f'{APPROVED_LIMITS["poll_interval_seconds_min"]} (bounded '
            f'polling; shorter intervals are a synthetic-test-only CLI '
            f'convenience, never a binding value)')
    if deviations:
        raise BindingError(
            'time-limit deviation from the plan-approved parameters '
            '(listed, NOT auto-accepted; re-arm needs a newly approved '
            f'binding): {"; ".join(deviations)}')
    return binding


def verify_binding_pins(binding: dict) -> list[str]:
    """Recompute every pinned digest; returns the list of drifted fields
    (empty = no drift). Read-only."""
    drifted = []
    for field in PINNED_FILE_FIELDS:
        path = Path(binding[field]['path'])
        got = _sha256_file(path)
        if got is None or got != binding[field]['sha256']:
            drifted.append(f'{field}:sha256({got})!={binding[field]["sha256"]}')
    got_py = _sha256_file(Path(binding['runtime']['python_exe']))
    if got_py is None or got_py != binding['runtime']['sha256']:
        drifted.append(f'runtime:sha256({got_py})'
                       f'!={binding["runtime"]["sha256"]}')
    return drifted


# --------------------------------------------------------------------------
# original-campaign evidence classification (read-only, evidence-only)
# --------------------------------------------------------------------------

def classify_original(campaign: Path, stability_seconds: int, now: _dt.datetime):
    """Classify the ORIGINAL campaign from its on-disk evidence only.

    Returns (kind, reason, detail):
      ('WAIT', 'original_running', ...)        lock identity alive -> wait
      ('WAIT', 'evidence_not_stable', ...)     exited, not yet stable -> wait
      ('OK', 'exited_clean', ...)              exited + cleaned + stable
      ('NO_GO'|'UNKNOWN', reason, ...)         terminal, typed
    A bare historical PID is NEVER used to conclude death: a live pid with
    a creation time that does not match the lock record, or any probe that
    cannot be completed, stays UNKNOWN.
    """
    lock_path = campaign / 'driver.lock'
    lock, lock_reason = _read_json_record(lock_path)
    if lock_reason == 'absent':
        pass  # released on clean exit (driver _release_lock unlinks) -> fall through
    elif lock_reason in ('unreadable', 'invalid', 'over_limit'):
        return 'UNKNOWN', f'lock_{lock_reason}', {
            'driver_lock': f'present but {lock_reason}',
            'note': 'cannot confirm the original run state; UNKNOWN, no '
                    'retry-to-pass'}
    elif not isinstance(lock, dict) or type(lock.get('pid')) is not int:
        return 'UNKNOWN', 'lock_record_malformed', {
            'driver_lock': 'present, readable, but not a pid record'}
    else:
        pid = lock['pid']
        alive = _pid_alive(pid)
        if alive is None:
            return 'UNKNOWN', 'liveness_probe_inconclusive', {
                'lock_pid': pid,
                'note': 'cannot confirm original process state; UNKNOWN'}
        if alive:
            started = _process_start_utc(pid)
            wall = lock.get('wall')
            if not isinstance(wall, (int, float)) or started is None:
                return 'UNKNOWN', 'pid_identity_unconfirmable', {
                    'lock_pid': pid, 'lock_wall': wall,
                    'process_start_utc': started.isoformat()
                    if started else None,
                    'note': 'live PID but creation identity cannot be '
                            'confirmed; bare-PID conclusions are forbidden'}
            lock_wall = _dt.datetime.fromtimestamp(float(wall),
                                                   _dt.timezone.utc)
            delta = abs((started - lock_wall).total_seconds())
            if delta > PID_IDENTITY_TOLERANCE_S:
                return 'UNKNOWN', 'pid_reuse', {
                    'lock_pid': pid,
                    'lock_wall_utc': lock_wall.isoformat(),
                    'process_start_utc': started.isoformat(),
                    'delta_seconds': round(delta, 3),
                    'note': 'PID is alive but is a DIFFERENT process (pid '
                            'reuse); ownership cannot be confirmed'}
            return 'WAIT', 'original_running', {
                'lock_pid': pid,
                'identity': 'lock record (pid, wall) matches the live '
                            'process creation time',
                'note': 'original run still alive; normal waiting'}
        return 'NO_GO', 'lock_held_pid_not_alive', {
            'lock_pid': pid,
            'note': 'original died WITHOUT releasing its lock (abnormal '
                    'exit; cleanup path never ran) - definite block, not a '
                    'waitable state'}

    # lock released -> exited; verify children cleanup + closing evidence
    segments_dir = campaign / 'segments'
    closes: list[tuple[Path, float]] = []
    running_rounds: list[dict] = []
    unclosed: list[str] = []
    corrupted: list[str] = []
    counts = {'passed': 0, 'failed': 0, 'running': 0, 'other': 0}
    scanned_segments = 0
    scanned_rounds = 0
    if segments_dir.is_dir():
        try:
            segment_dirs = sorted(segments_dir.iterdir())
        except OSError:
            return 'UNKNOWN', 'segments_unreadable', {
                'note': 'campaign segments directory cannot be read'}
        for seg in segment_dirs[:MAX_SEGMENTS_SCAN]:
            if not (seg.is_dir() and re.fullmatch(r'segment-\d{6}', seg.name)):
                continue
            scanned_segments += 1
            close = seg / 'segment-close.json'
            doc, reason = _read_json_record(close)
            if reason in ('unreadable', 'invalid', 'over_limit'):
                corrupted.append(f'{seg.name}/segment-close.json:{reason}')
            elif reason == 'absent':
                unclosed.append(seg.name)
            else:
                try:
                    closes.append((close, close.stat().st_mtime))
                except OSError:
                    corrupted.append(f'{seg.name}/segment-close.json:mtime')
            rounds_root = seg / 'rounds'
            if not rounds_root.is_dir():
                continue
            try:
                round_dirs = sorted(rounds_root.iterdir())
            except OSError:
                corrupted.append(f'{seg.name}/rounds:unreadable')
                continue
            for rd in round_dirs[:MAX_ROUNDS_SCAN]:
                if not rd.is_dir():
                    continue
                scanned_rounds += 1
                record, rreason = _read_json_record(rd / 'round.json')
                if rreason in ('unreadable', 'invalid', 'over_limit'):
                    corrupted.append(f'{rd}/round.json:{rreason}')
                    continue
                if rreason == 'absent':
                    corrupted.append(f'{rd}/round.json:absent')
                    continue
                status = record.get('status') if isinstance(record, dict) else None
                if status == 'running':
                    counts['running'] += 1
                    running_rounds.append(
                        {'segment': seg.name, 'round_dir': rd.name})
                elif status in ('passed', 'failed'):
                    counts[status] += 1
                else:
                    counts['other'] += 1
    detail = {'segments_scanned': scanned_segments,
              'rounds_scanned': scanned_rounds,
              'round_status_counts': counts,
              'note': 'failure evidence is PRESERVED and counted, never '
                      'required to be all-passed at this layer'}
    if corrupted:
        detail['corrupted'] = corrupted[:20]
        return 'UNKNOWN', 'evidence_corrupted', {
            **detail, 'note': 'half-written/unreadable campaign records; '
                              'evidence integrity cannot be confirmed'}
    if not closes:
        return 'UNKNOWN', 'no_closing_evidence', {
            **detail, 'note': 'lock released but no parseable closing '
                              'receipt exists; exit cannot be confirmed'}
    if unclosed:
        detail['unclosed_segments'] = unclosed[:20]
        return 'NO_GO', 'leftover_children', {
            **detail, 'note': 'original exited but segments remain unclosed '
                              '(own children not cleaned) - definite block'}
    if running_rounds:
        detail['running_round_records'] = running_rounds[:10]
        return 'NO_GO', 'leftover_children', {
            **detail, 'note': 'original exited but round records still say '
                              '"running" (leftover children) - definite '
                              'block; not waitable, not retried'}
    mtime_candidates = [m for _p, m in closes]
    for name in ('campaign.json', 'driver.log'):
        probe = campaign / name
        try:
            if probe.exists():
                mtime_candidates.append(probe.stat().st_mtime)
        except OSError:
            pass
    newest = max(mtime_candidates)
    age = (now - _dt.datetime.fromtimestamp(newest, _dt.timezone.utc)
           ).total_seconds()
    detail['newest_mtime_age_seconds'] = round(age, 1)
    detail['required_stable_seconds'] = stability_seconds
    if age < stability_seconds:
        return 'WAIT', 'evidence_not_stable', {
            **detail, 'note': 'original exited and is clean, but evidence '
                              'not yet stable for the required window '
                              '(normal waiting)'}
    detail['closed_segments'] = [Path(p).parent.name for p, _m in closes]
    return 'OK', 'exited_clean', detail


# --------------------------------------------------------------------------
# linker state
# --------------------------------------------------------------------------

class Linker:
    def __init__(self, binding_path: Path, now: _dt.datetime | None):
        self.binding_path = binding_path
        self.binding = load_binding(binding_path)
        self.synthetic_clock = now
        self.control_dir = Path(self.binding['state_path']).parent
        self.state_path = Path(self.binding['state_path'])
        self.claim_path = Path(self.binding['claim_path'])
        self.receipt_path = Path(self.binding['started_receipt_path'])
        self.stop_path = Path(self.binding['linker_stop_path'])
        self.linker_id = uuid.uuid4().hex

    # -- clock ------------------------------------------------------------

    def now(self) -> _dt.datetime:
        return self.synthetic_clock or _dt.datetime.now(_dt.timezone.utc)

    def now_iso(self) -> str:
        return self.now().strftime('%Y-%m-%dT%H:%M:%SZ')

    # -- state io ---------------------------------------------------------

    def load_state(self):
        doc, reason = _read_json_record(self.state_path)
        if reason == 'unreadable':
            raise Refused('state file exists but is unreadable - manual '
                          'adjudication required')
        if reason == 'over_limit':
            # an over-cap state file must NOT fall through to "not
            # armed" (which would permit a re-arm over it); it stays a
            # manual-adjudication refusal.
            raise Refused('state file exceeds the 64 KiB bounded-record '
                          'limit - manual adjudication required')
        if reason == 'invalid':
            raise Refused('state file is not valid JSON - manual '
                          'adjudication required')
        return doc

    def read_state(self) -> dict:
        doc = self.load_state()
        if doc is None:
            raise Refused('not armed (no state file); run arm first')
        if doc.get('schema') != SCHEMA_STATE:
            raise Refused(f'unknown state schema: {doc.get("schema")!r}')
        if doc.get('state') not in STATES:
            raise Refused(f'unknown state value: {doc.get("state")!r}')
        if doc.get('binding_sha256') != _sha256_file(self.binding_path):
            raise Refused('state was armed against a DIFFERENT binding '
                          'manifest; refusing (re-arm requires a fresh '
                          'approved binding and no live claim)')
        return doc

    def write_state(self, state: dict) -> None:
        state['schema'] = SCHEMA_STATE
        state['task_id'] = self.binding['task_id']
        state['binding_sha256'] = _sha256_file(self.binding_path)
        state['state'] = state.get('state', 'UNARMED')
        state['checks'] = state.get('checks', [])[-MAX_CHECK_HISTORY:]
        state['transitions'] = state.get('transitions', [])[-MAX_TRANSITIONS:]
        _atomic_write_json(self.state_path, state)

    @staticmethod
    def _transition(state: dict, to: str, linker_id: str, at: str) -> None:
        history = state.setdefault('transitions', [])
        history.append({'from': state.get('state', 'UNARMED'), 'to': to,
                        'at_utc': at, 'linker_id': linker_id})
        state['state'] = to
        if to in TERMINAL:
            state['terminal'] = {'state': to,
                                 'reason': state.get('_terminal_reason', ''),
                                 'at_utc': at, 'linker_id': linker_id}

    def _record_check(self, state: dict, decision: str, reason: str,
                      detail: dict | None = None) -> None:
        checks = state.setdefault('checks', [])
        checks.append({'seq': len(checks) + 1, 'at_utc': self.now_iso(),
                       'decision': decision, 'reason': reason,
                       'detail': detail or {}})

    # -- claim / receipt ----------------------------------------------------

    def claim_exists(self) -> bool:
        return self.claim_path.exists()

    def write_claim(self) -> str:
        """Create the one-shot claim atomically and CONFIRM the complete
        write (slot-consumption boundary B2): O_CREAT|O_EXCL open, a
        full-write loop over short/partial writes, fsync, then a
        byte-identical readback whose parsed claim_id matches. Any
        failure raises ClaimWriteError - the slot is then treated as
        consumed (UNKNOWN on the cycle path) and the partial file is
        PRESERVED as evidence; it is never deleted, retried or patched.

        FileExistsError propagates unchanged (claim competition lost)."""
        claim_id = uuid.uuid4().hex
        doc = {'schema': SCHEMA_CLAIM, 'task_id': self.binding['task_id'],
               'claim_id': claim_id,
               'linker_id': self.linker_id,
               'binding_sha256': _sha256_file(self.binding_path),
               'claimed_at_utc': self.now_iso(),
               'once_only': True,
               'notes': [
                   'Unique one-shot claim, created atomically (O_EXCL) '
                   'BEFORE the launch flow is invoked.',
                   'A control-program restart NEVER clears or rewrites this '
                   'claim.',
                   'If no valid started-receipt follows, the state is '
                   'LOST-ACK/UNKNOWN: no second launch, no claim recovery, '
                   'no receipt rewrite; manual adjudication only.']}
        data = (json.dumps(doc, indent=2, ensure_ascii=False,
                           sort_keys=True) + '\n').encode('utf-8')
        try:
            # O_BINARY: on Windows os.open defaults to TEXT mode, which
            # would translate \n to \r\n and break the byte-identical
            # readback contract (the claim must be byte-exact on disk)
            fd = os.open(self.claim_path,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY
                         | getattr(os, 'O_BINARY', 0))
        except FileExistsError:
            raise
        except OSError as error:
            raise ClaimWriteError(
                f'claim open failed (slot treated as consumed): {error}'
            ) from error
        try:
            view = memoryview(data)
            while view:
                try:
                    written = os.write(fd, view)
                except OSError as error:
                    raise ClaimWriteError(
                        'claim write failed mid-document (short/torn write; '
                        'the partial file persists and the slot is treated '
                        f'as consumed): {error}') from error
                if written <= 0:
                    raise ClaimWriteError(
                        'claim write made no progress (persistent short '
                        'write); the partial file persists and the slot is '
                        'treated as consumed')
                view = view[written:]
            try:
                os.fsync(fd)
            except OSError as error:
                raise ClaimWriteError(
                    f'claim fsync failed (durability unconfirmed; slot '
                    f'treated as consumed): {error}') from error
        finally:
            os.close(fd)
        readback = _read_capped(self.claim_path, max_bytes=len(data) + 64)
        if readback != data:
            raise ClaimWriteError(
                'claim readback mismatch: the on-disk claim does not '
                'byte-match the intended document (short/torn write or '
                'concurrent tampering); the file persists, the slot is '
                'treated as consumed, and no launch may proceed')
        try:
            confirmed = json.loads(readback.decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as error:
            raise ClaimWriteError(
                f'claim readback unparseable: {error}') from error
        if not isinstance(confirmed, dict) \
                or confirmed.get('claim_id') != claim_id:
            raise ClaimWriteError(
                'claim readback identity mismatch: parsed claim_id does '
                'not match the intended claim')
        return claim_id

    def _receipt_identity_defect(self, doc, claim) -> str:
        """-> '' when the started-receipt doc carries the right schema and
        the COMPLETE bound identity (task / binding bytes / frozen pins /
        claim). Any defect returns a typed reason; a defective receipt is
        never accepted as proof of anything (rejected, not healed)."""
        if not isinstance(doc, dict) \
                or doc.get('schema') != SCHEMA_STARTED_RECEIPT:
            return 'receipt_unknown_schema'
        if doc.get('task_id') != self.binding['task_id']:
            return 'receipt_task_mismatch'
        if doc.get('binding_sha256') != _sha256_file(self.binding_path):
            return 'receipt_binding_mismatch'
        if doc.get('frozen') != self.binding['frozen']:
            return 'receipt_frozen_mismatch'
        if not isinstance(claim, dict) \
                or not isinstance(claim.get('claim_id'), str) \
                or claim.get('claim_id') != doc.get('claim_id'):
            return 'receipt_claim_mismatch'
        return ''

    def valid_started_receipt(self):
        """-> (doc | None, reason). PROOF of a successful start ONLY: right
        schema, complete bound identity, matching on-disk claim, recorded
        ``outcome == STARTED`` AND ``launch_returncode == 0``. A recorded
        failure (LAUNCH_FAILED) or unknown (LAUNCH_UNKNOWN) outcome, a
        legacy receipt without an outcome, or an outcome inconsistent
        with its rc NEVER validates as started (L3: receipts record their
        producing state; re-observation cannot upgrade them)."""
        claim, _creason = _read_json_record(self.claim_path)
        doc, reason = _read_json_record(self.receipt_path)
        if reason == 'absent' and _creason == 'absent':
            return None, 'no_claim_no_receipt'
        if reason == 'absent':
            return None, 'receipt_absent_after_claim'
        if reason in ('unreadable', 'invalid', 'over_limit'):
            return None, f'receipt_{reason}'
        defect = self._receipt_identity_defect(doc, claim)
        if defect:
            return None, defect
        if doc.get('outcome') != RECEIPT_OUTCOME_STARTED:
            return None, 'receipt_outcome_not_started'
        if doc.get('launch_returncode') != 0:
            return None, 'receipt_outcome_started_rc_conflict'
        return doc, ''

    def _recorded_launch_prevention(self, state_doc, claim) -> str | None:
        """-> 'CANCELLED' | 'EXPIRED' | 'NO_GO' | None. Recognizes a
        terminal state that records a NO-LAUNCH prevention after the
        claim but before the launch invocation (boundary B3): the
        persisted state must name the same claim_id, carry
        ``launch_invoked == false``, and be one of the gate verdicts;
        a CANCELLED record additionally requires the cancel marker to
        still exist (the cancel evidence itself). Anything less certain
        stays None -> the caller treats the claim as lost-ack UNKNOWN."""
        if not isinstance(state_doc, dict) or not isinstance(claim, dict):
            return None
        recorded_claim = state_doc.get('claim')
        if not isinstance(recorded_claim, dict) \
                or recorded_claim.get('claim_id') != claim.get('claim_id'):
            return None
        if state_doc.get('launch_invoked') is not False:
            return None
        verdict = state_doc.get('state')
        if verdict not in NO_LAUNCH_REASONS:
            return None
        if verdict == 'CANCELLED' and not self.stop_path.exists():
            return None
        return verdict

    def adjudicate_claim(self, state_doc):
        """Re-derive the verdict for a consumed claim STRICTLY from the
        recorded evidence (claim + receipt). Returns
        (verdict, exit_code, reason, receipt_or_None, detail).

        The receipt is the state machine binding (L3): its recorded
        ``outcome`` decides, never the re-observation. LAUNCH_FAILED
        re-reports NO_GO, LAUNCH_UNKNOWN re-reports UNKNOWN, STARTED
        (rc 0, identity intact) re-reports STARTED; absent / unreadable /
        invalid / identity-defective / outcome-less receipts are UNKNOWN
        (lost-ack) - never re-issued, never upgraded. A recorded
        no-launch prevention after the claim re-reports its gate verdict.
        FINAL_REVIEW_READY (a post-start operator terminal) is handled by
        the caller before adjudication."""
        claim, creason = _read_json_record(self.claim_path)
        if creason in ('unreadable', 'invalid') \
                or not isinstance(claim, dict) \
                or not isinstance(claim.get('claim_id'), str):
            return ('UNKNOWN', EXIT_UNKNOWN, 'lost_ack:claim_corrupt',
                    None, {'claim_reason': creason,
                           'note': 'the unique slot may be consumed but its '
                                   'record cannot be read; no re-launch'})
        receipt, rreason = _read_json_record(self.receipt_path)
        if rreason == 'absent':
            prevented = self._recorded_launch_prevention(state_doc, claim)
            if prevented is not None:
                return (prevented, STATE_EXIT[prevented],
                        NO_LAUNCH_REASONS[prevented], None,
                        {'claim_id': claim['claim_id'],
                         'launch_invoked': False,
                         'note': 'recorded no-launch prevention after the '
                                 'claim, before the launch invocation '
                                 '(boundary B3); re-reported verbatim'})
            return ('UNKNOWN', EXIT_UNKNOWN, 'lost_ack:receipt_absent',
                    None, {'claim_id': claim['claim_id']})
        if rreason in ('unreadable', 'invalid'):
            return ('UNKNOWN', EXIT_UNKNOWN, f'lost_ack:receipt_{rreason}',
                    None, {'claim_id': claim['claim_id']})
        defect = self._receipt_identity_defect(receipt, claim)
        if defect:
            return ('UNKNOWN', EXIT_UNKNOWN, f'lost_ack:{defect}', receipt,
                    {'claim_id': claim['claim_id']})
        outcome = receipt.get('outcome')
        rc = receipt.get('launch_returncode')
        if outcome == RECEIPT_OUTCOME_STARTED:
            if rc != 0:
                return ('UNKNOWN', EXIT_UNKNOWN,
                        'lost_ack:receipt_outcome_rc_conflict', receipt,
                        {'outcome': outcome, 'launch_returncode': rc,
                         'note': 'recorded outcome inconsistent with its '
                                 'own rc; rejected, not healed'})
            return ('STARTED', EXIT_OK, 'receipt_outcome_started', receipt,
                    {'claim_id': claim['claim_id']})
        if outcome == RECEIPT_OUTCOME_LAUNCH_FAILED:
            return ('NO_GO', EXIT_NO_GO,
                    f'launch_command_nonzero_exit: rc={rc} '
                    '(receipt-recorded; re-observed, never upgraded)',
                    receipt, {'launch_returncode': rc,
                              'claim_id': claim['claim_id']})
        if outcome == RECEIPT_OUTCOME_LAUNCH_UNKNOWN:
            return ('UNKNOWN', EXIT_UNKNOWN,
                    f"launch_unknown: "
                    f"{receipt.get('failure_reason', 'unrecorded')}",
                    receipt, {'claim_id': claim['claim_id']})
        return ('UNKNOWN', EXIT_UNKNOWN, 'lost_ack:receipt_outcome_missing',
                receipt, {'outcome': outcome,
                          'note': 'legacy or foreign receipt without a '
                                  'recorded producing outcome; rejected, '
                                  'never upgraded to STARTED'})

    # -- last-safe-point gates (L1/L2) ------------------------------------

    def _launch_boundary_gate(self, phase, preflight_started=None,
                              report_path=None):
        """Re-validate every condition the elapsed step could have
        invalidated, at the boundary BEFORE the next irreversible step
        (fix wave N1). ``phase`` is ``'post_preflight'`` (before the
        claim) or ``'post_claim'`` (immediately before the launch
        invocation - the LAST moment a confirmed cancel can prevent the
        launch).

        Returns None when every gate passes, else a typed verdict dict
        {verdict, reason, exit, detail}. Checks, in order:
          * operator cancel marker - the linearization point of cancel;
            present here means the cancel is confirmed BEFORE the
            irreversible boundary and MUST win (L1);
          * window / deadline against a FRESH ``self.now()`` read - never
            a clock value cached before the preflight ran (L2);
          * binding pin drift over every pinned file;
          * report freshness (post_preflight only): the GO report must
            have been written by THIS preflight invocation (mtime >=
            spawn time - tolerance); a leftover GO from an earlier cycle
            is UNKNOWN, not a launch basis.

        Deliberately NOT checked: a foreign terminal state WITHOUT a
        cancel marker - a concurrent instance that already won is
        adjudicated by the O_EXCL claim race (exit 3), preserving the
        two-instance competition contract."""
        if self.stop_path.exists():
            note = ''
            raw = _read_capped(self.stop_path, 4096)
            if isinstance(raw, bytes):
                note = raw.decode('utf-8', 'replace')[:200].strip()
            reason = ('operator_cancel_confirmed_during_preflight'
                      if phase == 'post_preflight' else
                      'operator_cancel_confirmed_after_claim')
            return {'verdict': 'CANCELLED', 'exit': EXIT_OK,
                    'reason': reason,
                    'detail': {'stop_marker': str(self.stop_path),
                               'note': note, 'phase': phase,
                               'claim_consumed': phase == 'post_claim'}}
        now = self.now()
        latest = parse_utc(self.binding['launch_window_utc']['latest'])
        deadline = parse_utc(self.binding['deadline_utc'])
        if now > deadline:
            return {'verdict': 'EXPIRED', 'exit': EXIT_EXPIRED,
                    'reason': ('past_total_deadline_during_preflight'
                               if phase == 'post_preflight' else
                               'past_total_deadline_after_claim'),
                    'detail': {'now_utc': self.now_iso(), 'phase': phase,
                               'deadline_utc': self.binding['deadline_utc'],
                               'claim_consumed': phase == 'post_claim'}}
        if now > latest:
            return {'verdict': 'EXPIRED', 'exit': EXIT_EXPIRED,
                    'reason': ('window_crossed_during_preflight'
                               if phase == 'post_preflight' else
                               'window_crossed_after_claim'),
                    'detail': {'now_utc': self.now_iso(), 'phase': phase,
                               'window_latest':
                                   self.binding['launch_window_utc']['latest'],
                               'claim_consumed': phase == 'post_claim',
                               'note': 'preflight duration cannot make '
                                       'window validation stale'}}
        drifted = verify_binding_pins(self.binding)
        if drifted:
            return {'verdict': 'NO_GO', 'exit': EXIT_NO_GO,
                    'reason': f'binding_drift_at_{phase}',
                    'detail': {'drifted': drifted, 'phase': phase,
                               'claim_consumed': phase == 'post_claim'}}
        if phase == 'post_preflight' and preflight_started is not None:
            try:
                mtime = Path(report_path).stat().st_mtime
            except OSError:
                return {'verdict': 'UNKNOWN', 'exit': EXIT_UNKNOWN,
                        'reason': 'preflight_report_stat_failed',
                        'detail': {'report': str(report_path),
                                   'phase': phase,
                                   'note': 'cannot confirm the GO report '
                                           'was produced by this '
                                           'invocation'}}
            if mtime + REPORT_FRESHNESS_TOLERANCE_S < preflight_started:
                return {'verdict': 'UNKNOWN', 'exit': EXIT_UNKNOWN,
                        'reason': 'preflight_report_stale',
                        'detail': {
                            'report': str(report_path),
                            'report_age_at_preflight_spawn_s': round(
                                preflight_started - mtime, 3),
                            'phase': phase,
                            'note': 'GO report predates THIS preflight '
                                    'invocation (stale leftover); a '
                                    'previous GO never substitutes the '
                                    'live launch boundary'}}
        return None

    def _settle_gate(self, state: dict, gate: dict) -> tuple[int, bool]:
        """Apply a failed last-safe-point gate: record the typed verdict,
        transition to its terminal state and STOP before the irreversible
        step. A gate failure after the claim keeps the consumed slot and
        records ``launch_invoked=false`` (boundary B3 prevention)."""
        verdict, reason = gate['verdict'], gate['reason']
        if state.get('claim') is not None:
            state['launch_invoked'] = False
        self._record_check(state, verdict, reason, gate['detail'])
        state['_terminal_reason'] = reason
        self._transition(state, verdict, self.linker_id, self.now_iso())
        self.write_state(state)
        print(f'{verdict}: last-safe-point gate ({gate["detail"].get("phase")}) '
              f'reason={reason}')
        if verdict == 'CANCELLED':
            if gate['detail'].get('claim_consumed'):
                print('  cancel confirmed AFTER the claim but BEFORE the '
                      'launch invocation - the last moment a cancel can '
                      'prevent the launch; the claim stays consumed and '
                      'NO launch was invoked')
            else:
                print('  cancel confirmed during preflight (before the '
                      'claim); the one-shot slot is NOT consumed')
        elif verdict == 'EXPIRED':
            print('  the fresh post-step clock read crossed the approved '
                  'bound; elapsed preflight time cannot keep a stale '
                  'window validation alive')
        return gate['exit'], False

    # -- start-evidence verification (L4) ---------------------------------

    def _verify_start_evidence(self, claim_id: str, returncode: int):
        """Exit 0 alone is NOT proof of an actual start (L4). Required
        evidence, all of it: the on-disk claim still exists with the same
        claim_id, and the bound ``campaign.json`` exists at exactly the
        pinned ``new_campaign_dir`` as a readable, valid record (identity
        echo: if the campaign record carries a task_id it must match the
        bound task). Returns (ok, reason, detail)."""
        detail = {}
        campaign_dir = Path(self.binding['new_campaign_dir'])
        campaign_json = campaign_dir / 'campaign.json'
        claim_doc, creason = _read_json_record(self.claim_path)
        if creason != '' or not isinstance(claim_doc, dict) \
                or claim_doc.get('claim_id') != claim_id:
            return False, 'claim_missing_or_identity_changed', {
                'claim_reason': creason, 'expected_claim_id': claim_id,
                'launch_returncode': returncode}
        try:
            present = campaign_json.exists()
        except OSError:
            present = False
        detail['campaign_json_exists'] = present
        if not present:
            return False, 'campaign_json_absent_at_bound_target', detail
        doc, rreason = _read_json_record(campaign_json)
        detail['campaign_json_reason'] = rreason
        if rreason != '' or not isinstance(doc, dict):
            return False, 'campaign_json_not_valid_record', detail
        try:
            resolved = campaign_json.parent.resolve()
            bound = campaign_dir.resolve()
        except OSError:
            return False, 'campaign_target_identity_unresolvable', detail
        if resolved != bound:
            return False, 'campaign_json_outside_bound_dir', detail
        detail['campaign_json_schema'] = doc.get('schema')
        if 'task_id' in doc and doc.get('task_id') != self.binding['task_id']:
            return False, 'campaign_task_mismatch', detail
        detail['note'] = ('rc0 corroborated by the intact claim plus the '
                          'campaign.json record at the pinned target')
        return True, '', detail

    # ======================================================================
    # subcommands
    # ======================================================================

    def cmd_arm(self) -> int:
        existing = self.load_state()
        if existing is not None:
            if existing.get('schema') != SCHEMA_STATE:
                raise Refused('existing state file with unknown schema')
            same_binding = (existing.get('binding_sha256')
                            == _sha256_file(self.binding_path))
            if existing.get('state') != 'ARMED_WAITING' or not same_binding:
                raise Refused(
                    f'arm refused: state file already exists in state '
                    f'{existing.get("state")!r} '
                    f'(binding {"same" if same_binding else "DIFFERENT"}); '
                    f'this linker is single-shot - no re-arm over live '
                    f'state')
            print('ALREADY_ARMED (idempotent): state is ARMED_WAITING with '
                  'the same binding manifest.')
            return EXIT_OK
        if self.claim_exists() or self.receipt_path.exists():
            raise Refused('arm refused: a claim or started-receipt already '
                          'exists under the bound control paths; the '
                          'one-shot slot may already be consumed (manual '
                          'adjudication)')
        for key in ('new_campaign_root', 'new_campaign_dir'):
            if Path(self.binding[key]).exists():
                raise Refused(f'arm refused: {key} already exists: '
                              f'{self.binding[key]}')
        drifted = verify_binding_pins(self.binding)
        if drifted:
            state = {'state': 'NO_GO', '_terminal_reason':
                     f'binding_drift_at_arm: {drifted}'}
            self._transition(state, 'NO_GO', self.linker_id, self.now_iso())
            state['armed'] = None
            self.write_state(state)
            print(f'REFUSED: binding pin drift at arm time: {drifted}')
            return EXIT_NO_GO
        pid = os.getpid()
        started = _process_start_utc(pid)
        armed_receipt = {
            'schema': SCHEMA_ARMED_RECEIPT, 'state': 'ARMED_WAITING',
            'at_utc': self.now_iso(), 'pid': pid,
            'pid_start_utc': started.isoformat() if started else None,
            'binding_sha256': _sha256_file(self.binding_path),
            'task_id': self.binding['task_id']}
        state = {
            'state': 'UNARMED', 'linker_id': self.linker_id,
            'armed': {
                'at_utc': self.now_iso(), 'pid': pid,
                'pid_start_utc': started.isoformat()
                if started else None,
                'argv': [str(part) for part in sys.argv],
                'binding_digest_summary': {
                    field: self.binding[field]['sha256']
                    for field in PINNED_FILE_FIELDS},
                'runtime_sha256': self.binding['runtime']['sha256'],
                'frozen': dict(self.binding['frozen']),
                'launch_window_utc': dict(
                    self.binding['launch_window_utc']),
                'deadline_utc': self.binding['deadline_utc'],
                'cancel_method': (
                    'soak_linker.py cancel --binding <binding path> - '
                    'writes the LINKER-OWN stop marker at '
                    f'{self.binding["linker_stop_path"]} (independent from '
                    'the original campaign stop file and from the new '
                    'campaign stop file; cancelling the linker never stops '
                    'either campaign)'),
                'first_receipt': armed_receipt},
            'last_now_utc': self.now_iso(),
            'checks': [], 'transitions': []}
        self._transition(state, 'ARMED_WAITING', self.linker_id,
                         self.now_iso())
        state['armed']['first_receipt']['after_state'] = 'ARMED_WAITING'
        self.write_state(state)
        print(f'ARMED_WAITING: pid={pid} state={self.state_path}')
        print(f'FIRST RECEIPT (ARMED_WAITING): {json.dumps(armed_receipt)}')
        print(f'CANCEL: {state["armed"]["cancel_method"]}')
        return EXIT_OK

    # -- one bounded wait cycle -------------------------------------------

    def _cycle(self, stop_after: str) -> tuple[int, bool]:
        """Run ONE bounded check cycle. Returns (exit_code, continue_loop).

        Evidence-over-state ordering: the on-disk claim/receipt pair is
        adjudicated FIRST (a control-program restart, including one that
        crashed mid-cycle leaving a transient CLAIMED/PRECHECK state,
        re-derives the truth from the claim evidence), then terminal
        states are re-reported, then the live checks run."""
        state = self.read_state()
        current = state['state']

        # 1. consumed-slot adjudication BEFORE anything else: the verdict
        #    is derived STRICTLY from the recorded claim/receipt evidence
        #    (receipt-outcome binding, L3). A claim WITH a valid
        #    outcome=STARTED receipt is STARTED (heals a lagging state
        #    file); a recorded LAUNCH_FAILED receipt re-reports NO_GO;
        #    anything else (absent/unreadable/identity-defective receipt,
        #    recorded unknown, recorded no-launch prevention) re-reports
        #    its recorded verdict - NEVER an upgrade, NEVER a re-issue.
        if self.claim_exists():
            if current == 'FINAL_REVIEW_READY':
                # post-start operator terminal: keep it, never roll back
                # to STARTED by re-adjudication
                print('STATE: FINAL_REVIEW_READY (terminal) - no work '
                      'performed')
                return EXIT_OK, False
            verdict, code, reason, doc, detail = \
                self.adjudicate_claim(state)
            if verdict == 'STARTED':
                if current != 'STARTED':
                    self._record_check(state, 'STARTED',
                                       'receipt_present_state_lagging',
                                       {'claim_id': doc.get('claim_id')})
                    self._transition(state, 'STARTED', self.linker_id,
                                     self.now_iso())
                    state['started'] = doc
                    self.write_state(state)
                    print(f'STARTED (healed from receipt): claim_id='
                          f'{doc.get("claim_id")}')
                else:
                    print(f'STATE: STARTED (claim_id={doc.get("claim_id")}) '
                          '- idempotent; the one shot is consumed')
                return EXIT_OK, False
            if verdict == current:
                self._record_check(
                    state, verdict, f'{reason}:re_reported_from_record',
                    detail)
                self.write_state(state)
                print(f'{verdict} (re-reported from the recorded evidence; '
                      f'reason={reason}) - receipt-bound verdict, never '
                      'upgraded and never re-issued by re-observation')
                return code, False
            before = _sha256_file(self.claim_path)
            self._record_check(state, verdict, reason, {
                **(detail or {}),
                'claim': str(self.claim_path),
                'claim_sha256_before': before})
            state['_terminal_reason'] = reason
            self._transition(state, verdict, self.linker_id, self.now_iso())
            self.write_state(state)
            after = _sha256_file(self.claim_path)
            if verdict == 'UNKNOWN':
                print(f'UNKNOWN (lost-ack): {reason}.')
                print(f'  claim kept byte-identical: {before == after} '
                      f'({self.claim_path})')
                print('  NO second launch, NO claim recovery, NO receipt '
                      'rewrite. Manual adjudication required.')
            else:
                print(f'{verdict}: {reason} (claim/receipt evidence kept '
                      'byte-identical; no work performed)')
            return code, False

        # 2. terminal states are re-reported verbatim; never retried into
        #    pass. A STARTED state whose claim vanished is broken evidence.
        if current in TERMINAL:
            reason = (state.get('terminal') or {}).get('reason', '')
            print(f'STATE: {current} (terminal; reason={reason or "n/a"}) - '
                  'no work performed')
            return STATE_EXIT.get(current, EXIT_OK), False
        if current == 'STARTED':
            self._record_check(state, 'UNKNOWN', 'claim_missing_after_started',
                               {'claim': str(self.claim_path)})
            state['_terminal_reason'] = 'claim_missing_after_started'
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print('UNKNOWN: state says STARTED but the unique claim is '
                  'missing; broken evidence, no re-launch')
            return EXIT_UNKNOWN, False

        # 3. binding drift (identity re-verified every cycle)
        drifted = verify_binding_pins(self.binding)
        if drifted:
            self._record_check(state, 'NO_GO', 'binding_drift',
                               {'drifted': drifted})
            state['_terminal_reason'] = f'binding_drift: {drifted}'
            self._transition(state, 'NO_GO', self.linker_id, self.now_iso())
            self.write_state(state)
            print(f'NO_GO: control-package/candidate identity drift: '
                  f'{drifted}')
            return EXIT_NO_GO, False

        # 4. operator cancel (linker-OWN marker; campaigns untouched)
        if self.stop_path.exists():
            note = ''
            # existence is the operative (conservative) fact; the note
            # is cosmetic. isinstance(bytes) excludes the sentinels,
            # so an over-limit or unreadable marker still cancels.
            raw = _read_capped(self.stop_path, 4096)
            if isinstance(raw, bytes):
                note = raw.decode('utf-8', 'replace')[:200].strip()
            self._record_check(state, 'CANCELLED', 'operator_cancel',
                               {'stop_marker': str(self.stop_path),
                                'note': note})
            state['_terminal_reason'] = 'operator_cancel'
            self._transition(state, 'CANCELLED', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'CANCELLED by operator marker: {self.stop_path} '
                  '(linker stop ONLY; neither campaign was touched)')
            return EXIT_OK, False

        # 5. clock monotonicity (backward wall-clock jump)
        now = self.now()
        last_now = state.get('last_now_utc')
        if last_now:
            try:
                delta = (now - parse_utc(last_now)).total_seconds()
            except ValueError:
                delta = 0.0
            if delta < -CLOCK_BACKWARD_TOLERANCE_S:
                self._record_check(state, 'UNKNOWN', 'clock_jump_backward',
                                   {'last_now_utc': last_now,
                                    'now_utc': self.now_iso(),
                                    'delta_seconds': round(delta, 1)})
                state['_terminal_reason'] = 'clock_jump_backward'
                self._transition(state, 'UNKNOWN', self.linker_id,
                                 self.now_iso())
                self.write_state(state)
                print(f'UNKNOWN: backward clock jump detected '
                      f'({delta:.1f}s); time evidence unreliable, no launch')
                return EXIT_UNKNOWN, False
        state['last_now_utc'] = self.now_iso()

        # 6. window / deadline
        window = self.binding['launch_window_utc']
        earliest, latest = parse_utc(window['earliest']), \
            parse_utc(window['latest'])
        deadline = parse_utc(self.binding['deadline_utc'])
        if now > deadline:
            self._record_check(state, 'EXPIRED', 'past_total_deadline',
                               {'now_utc': self.now_iso(),
                                'deadline_utc': self.binding['deadline_utc']})
            state['_terminal_reason'] = 'past_total_deadline'
            self._transition(state, 'EXPIRED', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'EXPIRED: total deadline passed without a start '
                  f'(now={self.now_iso()} > {self.binding["deadline_utc"]})')
            return EXIT_EXPIRED, False
        if now > latest:
            self._record_check(state, 'EXPIRED', 'window_closed', {
                'now_utc': self.now_iso(),
                'window_latest': window['latest']})
            state['_terminal_reason'] = 'window_closed'
            self._transition(state, 'EXPIRED', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'EXPIRED: launch window closed without a start '
                  f'(now={self.now_iso()} > {window["latest"]})')
            return EXIT_EXPIRED, False
        if now < earliest:
            self._record_check(state, 'WAIT', 'window_not_reached', {
                'now_utc': self.now_iso(),
                'seconds_to_earliest': round(
                    (earliest - now).total_seconds(), 1)})
            self.write_state(state)
            print(f'ARMED_WAITING: launch window not open yet '
                  f'(earliest={window["earliest"]}); bounded wait '
                  'continues')
            return EXIT_OK, True

        # 7. planned-target absence (a pre-existing target is a block)
        for key in ('new_campaign_root', 'new_campaign_dir'):
            if Path(self.binding[key]).exists():
                self._record_check(state, 'NO_GO', 'target_already_exists',
                                   {'path': self.binding[key]})
                state['_terminal_reason'] = f'target_already_exists: {key}'
                self._transition(state, 'NO_GO', self.linker_id,
                                 self.now_iso())
                self.write_state(state)
                print(f'NO_GO: {key} already exists: {self.binding[key]}')
                return EXIT_NO_GO, False

        # 8. original-campaign evidence classification (every bounded
        #    check cycle records its verdict, including the passing one)
        kind, reason, detail = classify_original(
            Path(self.binding['original_campaign']),
            self.binding['evidence_stability_seconds'], now)
        self._record_check(state, kind, reason, detail)
        if kind == 'WAIT':
            self.write_state(state)
            print(f'ARMED_WAITING: {reason} (normal waiting); detail: '
                  f'{json.dumps(detail, default=str)[:400]}')
            return EXIT_OK, True
        if kind != 'OK':
            code = EXIT_UNKNOWN if kind == 'UNKNOWN' else EXIT_NO_GO
            state['_terminal_reason'] = reason
            self._transition(state, kind, self.linker_id, self.now_iso())
            self.write_state(state)
            print(f'{kind}: {reason}; detail: '
                  f'{json.dumps(detail, default=str)[:400]}')
            print('  terminal typed state - not retried into a pass')
            return code, False

        # 9. PRECHECK: the bound comprehensive preflight (single child
        #    process, read-only; every gate incl. capacity/windows-kit/
        #    frozen-tree/resources must be GO)
        self._transition(state, 'PRECHECK', self.linker_id, self.now_iso())
        self.write_state(state)
        report_path = Path(self.binding['preflight_report_path'])
        # fix wave N2 (fresh-report contract): this cycle's execution id is
        # generated HERE (uuid4, never a binding value) and appended to the
        # validated static preflight argv; the report must echo it back
        # inside its execution block, binding the consumed verdict to THIS
        # invocation (a leftover/forged GO from another cycle or run cannot
        # present a matching id).
        execution_id = uuid.uuid4().hex
        argv = runtime_preflight_argv(self.binding, execution_id)
        print(f'PRECHECK: running bound preflight: {" ".join(argv[:8])}...')
        # real wall-clock spawn time: the GO report must be FRESHER than
        # this moment, proving it was produced by THIS invocation
        preflight_spawned_at = time.time()
        preflight_cycle_started_utc = _dt.datetime.now(_dt.timezone.utc)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=PREFLIGHT_TIMEOUT_S,
                cwd=str(Path(self.binding['preflight']['path'])
                        .resolve().parent.parent),
                env=_sanitized_run_env(
                    Path(self.binding['state_path']).parent / 'run-temp'))
        except (OSError, subprocess.SubprocessError) as error:
            self._record_check(state, 'UNKNOWN', 'preflight_not_runnable',
                               {'error': f'{type(error).__name__}: {error}'})
            state['_terminal_reason'] = 'preflight_not_runnable'
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'UNKNOWN: bound preflight could not run: {error}')
            return EXIT_UNKNOWN, False
        report, rreason = _read_json_record(report_path)
        if proc.returncode != 0 or rreason in ('unreadable', 'invalid',
                                               'over_limit', 'absent') \
                or not isinstance(report, dict):
            self._record_check(state, 'UNKNOWN',
                               f'preflight_report_invalid:{rreason}',
                               {'returncode': proc.returncode})
            state['_terminal_reason'] = f'preflight_report_invalid: {rreason}'
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'UNKNOWN: preflight report unreadable/invalid '
                  f'({rreason}); no launch')
            return EXIT_UNKNOWN, False
        # 9a. execution-bound report verification (fix wave N2 spec
        #     section 5): the report must prove it was produced by THIS
        #     cycle's invocation - matching execution id, the pinned spec
        #     path AND a spec sha256 the linker re-hashes NOW (binding
        #     unchanged since arm), an argv echo equal to the flags after
        #     the script, and a generation time at/after this cycle's
        #     start. Any gap is UNKNOWN terminal
        #     preflight_report_not_execution_bound - never waitable, never
        #     auto-retried into a GO.
        exec_block = report.get('execution')
        bound_fields = []
        if report.get('schema') != SCHEMA_PREFLIGHT_REPORT:
            bound_fields.append('schema')
        if not isinstance(exec_block, dict):
            bound_fields.append('execution_block')
        else:
            if exec_block.get('execution_id') != execution_id:
                bound_fields.append('execution_id')
            if _norm_ci(str(exec_block.get('spec', ''))) != _norm_ci(
                    self.binding['launch_spec']['path']):
                bound_fields.append('spec')
            spec_sha_now = _sha256_file(
                Path(self.binding['launch_spec']['path']))
            if exec_block.get('spec_sha256') != spec_sha_now:
                bound_fields.append('spec_sha256')
            if list(exec_block.get('argv') or []) != argv[5:]:
                bound_fields.append('argv_echo')
        generated = report.get('generated_at_utc')
        try:
            generated_utc = parse_utc(str(generated)) \
                if generated is not None else None
        except ValueError:
            generated_utc = None
        if generated_utc is None:
            bound_fields.append('generated_at_utc')
        elif generated_utc + _dt.timedelta(
                seconds=REPORT_FRESHNESS_TOLERANCE_S) \
                < preflight_cycle_started_utc:
            bound_fields.append('generated_at_utc')
        if bound_fields:
            self._record_check(
                state, 'UNKNOWN', 'preflight_report_not_execution_bound',
                {'failed_fields': bound_fields,
                 'execution_id': execution_id,
                 'returncode': proc.returncode})
            state['_terminal_reason'] = \
                f'preflight_report_not_execution_bound: {bound_fields}'
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'UNKNOWN: preflight report is not bound to this '
                  f'execution (failed: {bound_fields}); no launch, no '
                  'retry into a GO')
            return EXIT_UNKNOWN, False
        # 9a2. complete-gate-inventory consistency (fix wave N2/plan
        #      "报告必须绑定本次调用/完整门禁/配置"): a verdict must rest on a
        #      demonstrated gate set - non-empty checks, every unmet id
        #      present in the inventory, and a GO with zero non-PASS
        #      entries. The linker deliberately does NOT hardcode the
        #      preflight's gate NAMES (they are the preflight owner's
        #      vocabulary and evolve); it enforces the inventory shape.
        checks = report.get('checks')
        unmet_ids = list(report.get('unmet_ids') or [])
        inventory_defects = []
        if not isinstance(checks, list) or not checks:
            inventory_defects.append('empty_or_not_a_list')
        else:
            check_ids = {c.get('id') for c in checks
                         if isinstance(c, dict)}
            if len(check_ids) != len(
                    [c for c in checks if isinstance(c, dict)]):
                inventory_defects.append('duplicate_or_missing_ids')
            missing_unmet = [i for i in unmet_ids if i not in check_ids]
            if missing_unmet:
                inventory_defects.append(f'unmet_not_in_inventory:'
                                         f'{missing_unmet}')
            if report.get('overall') == 'GO':
                non_pass = [c.get('id') for c in checks
                            if isinstance(c, dict)
                            and c.get('status') != 'PASS']
                if non_pass:
                    inventory_defects.append(f'go_with_non_pass:{non_pass}')
        if inventory_defects:
            self._record_check(
                state, 'NO_GO', 'preflight_report_gate_inventory_invalid',
                {'defects': inventory_defects})
            state['_terminal_reason'] = (
                f'preflight_report_gate_inventory_invalid: '
                f'{inventory_defects}')
            self._transition(state, 'NO_GO', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'NO_GO: preflight report gate inventory incomplete/'
                  f'inconsistent ({inventory_defects}); a verdict without a '
                  'demonstrated complete gate set authorizes nothing')
            return EXIT_NO_GO, False
        if report.get('overall') != 'GO':
            unmet = list(report.get('unmet_ids') or [])
            waitable = {'original_driver_exited',
                        'original_segment_close_present',
                        'original_evidence_stable',
                        'original_children_cleaned', 'launch_window'}
            if unmet and set(unmet) <= waitable:
                self._record_check(state, 'WAIT', 'preflight_waitable_unmet',
                                   {'unmet_ids': unmet})
                self._transition(state, 'ARMED_WAITING', self.linker_id,
                                 self.now_iso())
                self.write_state(state)
                print(f'ARMED_WAITING: preflight unmet on waitable '
                      f"time-gate checks only ({unmet}); bounded wait "
                      'continues')
                return EXIT_OK, True
            self._record_check(state, 'NO_GO', 'preflight_not_go',
                               {'unmet_ids': unmet})
            state['_terminal_reason'] = f'preflight_not_go: {unmet}'
            self._transition(state, 'NO_GO', self.linker_id, self.now_iso())
            self.write_state(state)
            print(f'NO_GO: bound preflight verdict='
                  f'{report.get("overall")} unmet={unmet}')
            return EXIT_NO_GO, False

        # 9b. POST-PREFLIGHT GATE (boundary B1, L1/L2 fix wave N1): the
        #     preflight ran for real elapsed time - everything its
        #     duration could have invalidated is re-validated HERE,
        #     BEFORE the claim consumes the one-shot slot: a cancel
        #     written while the preflight ran, the window/deadline against
        #     a FRESH clock read, binding pin drift, and the freshness of
        #     this very GO report (a leftover GO from an earlier cycle is
        #     rejected).
        gate = self._launch_boundary_gate(
            'post_preflight', preflight_started=preflight_spawned_at,
            report_path=report_path)
        if gate is not None:
            return self._settle_gate(state, gate)
        gate_log = {'post_preflight': 'pass'}

        # 10. CLAIM (slot-consumption boundary B2): unique one-shot slot,
        #     atomically with full-write confirmation, before any effect
        try:
            claim_id = self.write_claim()
        except FileExistsError:
            # lost the race to a concurrent instance: do NOT launch, do
            # NOT touch the shared state
            print('CLAIM COMPETITION LOST: another instance holds the '
                  f'unique claim ({self.claim_path}); this instance exits '
                  'without launching and without modifying state')
            return EXIT_CLAIM_LOST, False
        except ClaimWriteError as error:
            # unconfirmable claim write: the slot may be half-consumed;
            # treat as UNKNOWN, keep whatever is on disk, never re-attempt
            self._record_check(state, 'UNKNOWN', 'claim_write_unconfirmed',
                               {'error': str(error),
                                'claim': str(self.claim_path)})
            state['_terminal_reason'] = f'claim_write_unconfirmed: {error}'
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'UNKNOWN: one-shot claim could not be confirmed fully '
                  f'written ({error}); the slot is treated as consumed, '
                  'NO launch, the partial claim is preserved; manual '
                  'adjudication required')
            return EXIT_UNKNOWN, False
        state['claim'] = {'path': str(self.claim_path),
                          'claim_id': claim_id,
                          'claimed_at_utc': self.now_iso()}
        self._transition(state, 'CLAIMED', self.linker_id, self.now_iso())
        self.write_state(state)
        print(f'CLAIMED (one-shot): {self.claim_path} (claim_id={claim_id})')

        if stop_after == 'claim':
            print('SYNTHETIC INTERRUPTION: --stop-after claim reached '
                  '(simulates a control-program crash between claim and '
                  'receipt; synthetic tests only, never in a real arm)')
            return EXIT_OK, False

        # 10b. LAST-SAFE-POINT GATE (boundary B3, L1/L2 fix wave N1):
        #     immediately before the launch invocation - the LAST moment
        #     at which a confirmed cancel can still prevent the launch.
        #     A marker present here aborts to CANCELLED with the claim
        #     consumed and launch_invoked=false (recorded prevention);
        #     after the invocation (B4) only the launch's own recorded
        #     outcome may stand.
        gate = self._launch_boundary_gate('post_claim')
        if gate is not None:
            return self._settle_gate(state, gate)
        gate_log['post_claim'] = 'pass'

        # 11. START: invoke the bound launch flow (fixed argv) once.
        #     This invocation is THE IRREVOCABLE BOUNDARY (B4).
        #     fix wave N2: the launch argv structure is RE-validated here
        #     (cheap; catches binding-file drift between arm and start on
        #     any axis the pin re-hash cannot express, e.g. argv edits).
        try:
            validate_launch_argv(self.binding)
        except BindingError as error:
            state['launch_invoked'] = False
            self._record_check(state, 'NO_GO',
                               'binding_drift_after_claim_no_launch',
                               {'error': str(error),
                                'claim_id': claim_id})
            state['_terminal_reason'] = \
                f'binding_drift_after_claim_no_launch: {error}'
            self._transition(state, 'NO_GO', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'NO_GO: launch argv re-validation failed after the '
                  f'claim ({error}); the claim stays consumed, NO launch '
                  'was invoked, manual adjudication required')
            return EXIT_NO_GO, False
        argv = [str(part) for part in self.binding['launch_argv']]
        state['launch_invoked'] = True
        self.write_state(state)
        print(f'STARTING: {" ".join(argv[:8])}...')
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=LAUNCH_TIMEOUT_S,
                cwd=str(Path(self.binding['launcher']['path'])
                        .resolve().parent.parent),
                env=_sanitized_run_env(
                    Path(self.binding['state_path']).parent / 'run-temp'))
        except (OSError, subprocess.SubprocessError) as error:
            self._record_check(state, 'UNKNOWN', 'launch_not_runnable', {
                'error': f'{type(error).__name__}: {error}',
                'claim_id': claim_id})
            state['_terminal_reason'] = (
                f'launch_not_runnable (claim consumed; outcome unknown)')
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'UNKNOWN: bound launch flow could not run: {error}; '
                  'claim retained, manual adjudication required')
            return EXIT_UNKNOWN, False

        campaign_dir = Path(self.binding['new_campaign_dir'])
        campaign_written = (campaign_dir / 'campaign.json').exists()
        evidence_ok, evidence_reason, evidence_detail = \
            self._verify_start_evidence(claim_id, proc.returncode)
        receipt_common = {
            'schema': SCHEMA_STARTED_RECEIPT,
            'task_id': self.binding['task_id'],
            'claim_id': claim_id,
            'once_only': True,
            'started_at_utc': self.now_iso(),
            'launch_argv': argv,
            'launch_returncode': proc.returncode,
            'new_campaign_root': self.binding['new_campaign_root'],
            'new_campaign_dir': self.binding['new_campaign_dir'],
            'campaign_json_written': campaign_written,
            'campaign_evidence': evidence_detail,
            'boundary_gate': dict(gate_log),
            'binding_sha256': _sha256_file(self.binding_path),
            'frozen': dict(self.binding['frozen']),
            'preflight_verdict': report.get('overall'),
            'notes': [
                'STARTED receipt: the unique one-shot slot was consumed by '
                'THIS claim.',
                'outcome records the PRODUCING result of this launch '
                'attempt (STARTED / LAUNCH_FAILED / LAUNCH_UNKNOWN); a '
                'later wait re-reports strictly this recorded outcome and '
                'can never upgrade a failure or unknown to STARTED.',
                'If this receipt is later missing/half-written while the '
                'claim exists, the state is UNKNOWN (lost-ack): no '
                're-launch, no rewrite.',
                'The new campaign STOP path belongs to the campaign; '
                'cancelling the linker never touches it.']}
        if proc.returncode != 0:
            receipt = {**receipt_common,
                       'outcome': RECEIPT_OUTCOME_LAUNCH_FAILED,
                       'failure_reason': (
                           f'launch_command_nonzero_exit: '
                           f'rc={proc.returncode}')}
            _atomic_write_json(self.receipt_path, receipt)
            state['started'] = receipt
            self._record_check(state, 'NO_GO',
                               'launch_command_nonzero_exit',
                               {'returncode': proc.returncode,
                                'claim_id': claim_id})
            state['_terminal_reason'] = (
                f'launch_command_nonzero_exit: rc={proc.returncode}')
            self._transition(state, 'NO_GO', self.linker_id, self.now_iso())
            self.write_state(state)
            print(f'NO_GO: bound launch flow exited rc={proc.returncode} '
                  '(receipt outcome=LAUNCH_FAILED, kept; a later wait '
                  're-reports THIS recorded failure and can never upgrade '
                  'it to STARTED)')
            return EXIT_NO_GO, False
        if not evidence_ok:
            receipt = {**receipt_common,
                       'outcome': RECEIPT_OUTCOME_LAUNCH_UNKNOWN,
                       'failure_reason': evidence_reason}
            _atomic_write_json(self.receipt_path, receipt)
            state['started'] = receipt
            self._record_check(
                state, 'UNKNOWN', 'launch_start_evidence_insufficient',
                {'reason': evidence_reason, 'claim_id': claim_id,
                 **evidence_detail})
            state['_terminal_reason'] = (
                f'launch_start_evidence_insufficient: {evidence_reason}')
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'UNKNOWN: launch exited rc=0 but the start evidence is '
                  f'insufficient ({evidence_reason}); rc0 alone is never '
                  'start evidence (receipt outcome=LAUNCH_UNKNOWN, kept)')
            return EXIT_UNKNOWN, False
        receipt = {**receipt_common,
                   'outcome': RECEIPT_OUTCOME_STARTED}
        _atomic_write_json(self.receipt_path, receipt)
        # receipt readback (L4): the proof itself must read back intact
        # with its full identity before STARTED may be claimed
        rdoc, rreason = _read_json_record(self.receipt_path)
        rclaim, _rcreason = _read_json_record(self.claim_path)
        readback_defect = (f'receipt_{rreason}' if rreason else
                           self._receipt_identity_defect(rdoc, rclaim))
        if readback_defect:
            self._record_check(state, 'UNKNOWN',
                               'receipt_write_unconfirmed',
                               {'reason': readback_defect,
                                'claim_id': claim_id,
                                'note': 'the started receipt could not be '
                                        'read back intact; a later wait '
                                        'adjudicates strictly from what '
                                        'actually reads back'})
            state['_terminal_reason'] = (
                f'receipt_write_unconfirmed: {readback_defect}')
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            print(f'UNKNOWN: started receipt write could not be confirmed '
                  f'({readback_defect}); claim retained, manual '
                  'adjudication required')
            return EXIT_UNKNOWN, False
        state['started'] = rdoc
        self._record_check(state, 'STARTED', 'started', {
            'claim_id': claim_id,
            'outcome': RECEIPT_OUTCOME_STARTED,
            'new_campaign_dir': self.binding['new_campaign_dir'],
            'campaign_json_written': campaign_written,
            'campaign_evidence': evidence_detail})
        self._transition(state, 'STARTED', self.linker_id, self.now_iso())
        self.write_state(state)
        print(f'STARTED: claim_id={claim_id} campaign='
              f'{self.binding["new_campaign_dir"]}')
        print(f'STARTED RECEIPT: {self.receipt_path}')
        return EXIT_OK, False

    def cmd_wait(self, interval: int, max_cycles: int,
                 stop_after: str) -> int:
        synthetic = self.synthetic_clock is not None
        if synthetic:
            print('NOTE: synthetic clock mode (tests only); poll sleeps '
                  'skipped')
        code, continue_loop = self._cycle(stop_after)
        cycles = 1
        while continue_loop and cycles < max_cycles:
            if not synthetic:
                time.sleep(max(0, interval))
            code, continue_loop = self._cycle(stop_after)
            cycles += 1
        if continue_loop:
            print(f'ARMED_WAITING: bounded wait paused after {cycles} '
                  f'cycle(s); re-invoke wait to continue (bounded polling, '
                  f'interval >= {self.binding["poll_interval_seconds"]}s)')
        return code

    def cmd_cancel(self, note: str) -> int:
        state = self.read_state()
        current = state['state']
        if current in ('CLAIMED', 'STARTED', 'FINAL_REVIEW_READY'):
            raise Refused(
                f'cancel refused from state {current!r}: the one-shot '
                f'claim may already be consumed; manual adjudication only')
        if current in ('CANCELLED',):
            print('CANCELLED (idempotent): already cancelled.')
            return EXIT_OK
        marker = self.stop_path
        tmp = marker.with_name(marker.name + '.tmp')
        payload = (f'linker cancel at {self.now_iso()}'
                   + (f': {note}' if note else '') + '\n')
        with open(tmp, 'wb') as handle:
            handle.write(payload.encode('utf-8'))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, marker)
        self._record_check(state, 'CANCELLED', 'operator_cancel',
                           {'stop_marker': str(marker), 'note': note})
        state['_terminal_reason'] = 'operator_cancel'
        self._transition(state, 'CANCELLED', self.linker_id, self.now_iso())
        self.write_state(state)
        print(f'CANCELLED: linker-own stop marker written: {marker}')
        print('This marker stops ONLY this linker. The original campaign '
              'stop file and the new campaign stop file are NOT touched '
              'by this command.')
        return EXIT_OK

    def cmd_status(self) -> int:
        state = self.load_state()
        if state is None:
            print(f'STATE: UNARMED (no state file at {self.state_path})')
            return EXIT_OK
        print(json.dumps({k: state.get(k) for k in (
            'state', 'task_id', 'binding_sha256', 'armed', 'claim',
            'started', 'terminal', 'last_now_utc')},
            indent=2, ensure_ascii=False, default=str))
        checks = state.get('checks') or []
        print(f'checks recorded: {len(checks)} (last: '
              f'{json.dumps(checks[-1], default=str) if checks else "none"})')
        return STATE_EXIT.get(state.get('state'), EXIT_OK)

    def cmd_mark_final_review_ready(self) -> int:
        state = self.read_state()
        if state['state'] != 'STARTED':
            raise Refused(
                f'mark-final-review-ready refused from state '
                f'{state["state"]!r} (requires STARTED)')
        doc, reason = self.valid_started_receipt()
        if doc is None:
            raise Refused(f'started receipt invalid ({reason})')
        self._record_check(state, 'FINAL_REVIEW_READY',
                           'operator_marked', {})
        state['_terminal_reason'] = 'final_review_ready'
        self._transition(state, 'FINAL_REVIEW_READY', self.linker_id,
                         self.now_iso())
        self.write_state(state)
        print('FINAL_REVIEW_READY: started campaign marked ready for the '
              'final review stage.')
        return EXIT_OK


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='soak_linker',
        description='RV-05 single-shot auto-link control (wait/claim/'
                    'unique-start state machine above the one-shot '
                    'launcher).')
    sub = parser.add_subparsers(dest='command', required=True)

    def add_common(sp):
        sp.add_argument('--binding', required=True,
                        help='path to the pal-rv05-linker-binding-v1 '
                             'manifest')
        sp.add_argument('--now-utc',
                        help='SYNTHETIC clock override for tests '
                             '(ISO-8601 Z); never used in a real arm')

    p_arm = sub.add_parser('arm', help='UNARMED -> ARMED_WAITING (validates '
                                       'the binding first)')
    add_common(p_arm)
    p_wait = sub.add_parser('wait', help='one or more bounded check cycles')
    add_common(p_wait)
    p_wait.add_argument('--once', action='store_true',
                        help='single check cycle (default)')
    p_wait.add_argument('--interval', type=int, default=None,
                        help='poll interval seconds (default: binding '
                             'value >= 300; below 300 requires --now-utc)')
    p_wait.add_argument('--max-cycles', type=int, default=1,
                        help='bounded number of check cycles per '
                             'invocation (default 1)')
    p_wait.add_argument('--stop-after', choices=['never', 'claim'],
                        default='never',
                        help='synthetic interruption simulation for tests '
                             '(never used in a real arm)')
    p_cancel = sub.add_parser('cancel', help='operator cancel via the '
                                             'linker-OWN stop marker')
    add_common(p_cancel)
    p_cancel.add_argument('--note', default='')
    p_status = sub.add_parser('status', help='print the persisted state')
    add_common(p_status)
    p_fr = sub.add_parser('mark-final-review-ready',
                          help='STARTED -> FINAL_REVIEW_READY')
    add_common(p_fr)

    args = parser.parse_args(argv)
    now = None
    if args.now_utc:
        try:
            now = parse_utc(args.now_utc)
        except ValueError as error:
            print(f'ARG ERROR: --now-utc: {error}')
            return EXIT_USAGE
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
    try:
        linker = Linker(Path(args.binding), now)
    except BindingError as error:
        print(f'BINDING REJECTED: {error}')
        return EXIT_BINDING

    interval = linker.binding['poll_interval_seconds']
    if args.command == 'wait' and args.interval is not None:
        interval = args.interval
    if args.command == 'wait' and interval < APPROVED_LIMITS[
            'poll_interval_seconds_min'] and now is None:
        print(f'ARG ERROR: --interval {interval}s < '
              f'{APPROVED_LIMITS["poll_interval_seconds_min"]}s minimum is '
              f'a synthetic-test-only convenience and requires --now-utc')
        return EXIT_USAGE
    max_cycles = args.max_cycles if args.command == 'wait' else 1
    if args.command == 'wait' and args.once:
        max_cycles = 1

    try:
        if args.command == 'arm':
            return linker.cmd_arm()
        if args.command == 'wait':
            return linker.cmd_wait(interval, max_cycles, args.stop_after)
        if args.command == 'cancel':
            return linker.cmd_cancel(args.note)
        if args.command == 'status':
            return linker.cmd_status()
        if args.command == 'mark-final-review-ready':
            return linker.cmd_mark_final_review_ready()
    except Refused as error:
        print(f'REFUSED: {error}')
        return EXIT_USAGE
    except BindingError as error:
        print(f'BINDING REJECTED: {error}')
        return EXIT_BINDING
    print(f'ARG ERROR: unknown command {args.command!r}')
    return EXIT_USAGE


if __name__ == '__main__':
    sys.exit(main())
