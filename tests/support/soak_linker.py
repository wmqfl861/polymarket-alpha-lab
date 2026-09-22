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
      launch_spec, --json-out, preflight_report_path];
  launch_argv -> [powershell, -NoProfile, -ExecutionPolicy, Bypass,
      -File, launcher, -SpecPath, launch_spec].

  argv/pin cross-checks (fix wave, cross-review M1): preflight_argv[0]
  must equal runtime.python_exe, preflight_argv must contain the pinned
  preflight path, and launch_argv must contain the pinned launcher path
  — an argv that does not invoke the pinned files is rejected at load,
  so a mis-assembled binding can never call an unpinned script.

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
import os
import re
import subprocess
import sys
import time
import uuid
from hashlib import sha256
from pathlib import Path

SCHEMA_BINDING = 'pal-rv05-linker-binding-v1'
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
    """Bounded binary read -> bytes | None(absent) | 'UNREADABLE'."""
    try:
        with open(path, 'rb') as handle:
            return handle.read(max_bytes)
    except FileNotFoundError:
        return None
    except OSError:
        return 'UNREADABLE'


def _read_json_record(path: Path):
    """-> (doc | None, reason) with reason in {'', 'absent', 'unreadable',
    'invalid'}; never raises. Distinguishes absent from unreadable so a
    permission problem stays UNKNOWN instead of looking like a clean exit."""
    raw = _read_capped(path)
    if raw is None:
        return None, 'absent'
    if raw == 'UNREADABLE':
        return None, 'unreadable'
    try:
        return json.loads(raw.decode('utf-8')), ''
    except (ValueError, UnicodeDecodeError):
        return None, 'invalid'


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


def _pid_alive(pid) -> bool | None:
    """Read-only liveness probe for ONE recorded PID (same contract as the
    frozen driver's helper; None = unknown)."""
    if type(pid) is not int or pid <= 0:
        return None
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            handle = kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            try:
                status = kernel32.WaitForSingleObject(
                    ctypes.c_void_p(handle), 0)
                return status == 258
            finally:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        except OSError:
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
        try:
            import ctypes

            class _FT(ctypes.Structure):
                _fields_ = [('lo', ctypes.c_uint32), ('hi', ctypes.c_uint32)]

            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                creation, exit_t, kernel_t, user_t = (_FT(), _FT(), _FT(),
                                                      _FT())
                if not kernel32.GetProcessTimes(
                        ctypes.c_void_p(handle),
                        ctypes.byref(creation), ctypes.byref(exit_t),
                        ctypes.byref(kernel_t), ctypes.byref(user_t)):
                    return None
                total = (creation.hi << 32) | creation.lo
                if total == 0:
                    return None
                unix = total / 1e7 - 11644473600.0
                return _dt.datetime.fromtimestamp(unix, _dt.timezone.utc)
            finally:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        except OSError:
            return None
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


def load_binding(binding_path: Path) -> dict:
    text = _read_capped(binding_path, 1024 * 1024)
    if text is None or text == 'UNREADABLE':
        raise BindingError(f'binding manifest unreadable: {binding_path}')
    try:
        binding = json.loads(text.decode('utf-8'),
                             object_pairs_hook=_no_duplicate_keys)
    except (ValueError, UnicodeDecodeError) as error:
        raise BindingError(f'binding manifest invalid JSON: {error}') from error
    if not isinstance(binding, dict):
        raise BindingError('binding manifest must be a JSON object')
    if binding.get('schema') != SCHEMA_BINDING:
        raise BindingError(
            f'schema: unknown binding schema {binding.get("schema")!r}; '
            f'this linker accepts exactly {SCHEMA_BINDING!r}')
    missing = [k for k in REQUIRED_KEYS if k not in binding]
    unknown = [k for k in binding if k not in REQUIRED_KEYS]
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
    if binding['preflight']['path'] not in binding['preflight_argv']:
        raise BindingError('preflight_argv must contain the pinned '
                           'preflight path')
    if binding['launcher']['path'] not in binding['launch_argv']:
        raise BindingError('launch_argv must contain the pinned '
                           'launcher path')

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
    elif lock_reason in ('unreadable', 'invalid'):
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
            if reason in ('unreadable', 'invalid'):
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
                if rreason in ('unreadable', 'invalid'):
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
        claim_id = uuid.uuid4().hex
        doc = {'schema': SCHEMA_CLAIM, 'task_id': self.binding['task_id'],
               'claim_id': claim_id,
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
        fd = os.open(self.claim_path,
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return claim_id

    def valid_started_receipt(self):
        """-> (doc | None, reason). A receipt is valid only if it parses,
        carries the right schema and matches the on-disk claim id."""
        claim, creason = _read_json_record(self.claim_path)
        doc, reason = _read_json_record(self.receipt_path)
        if creason == 'absent' and reason == 'absent':
            return None, 'no_claim_no_receipt'
        if reason == 'absent':
            return None, 'receipt_absent_after_claim'
        if reason in ('unreadable', 'invalid'):
            return None, f'receipt_{reason}'
        if not isinstance(doc, dict) \
                or doc.get('schema') != SCHEMA_STARTED_RECEIPT:
            return None, 'receipt_unknown_schema'
        if not isinstance(claim, dict) \
                or claim.get('claim_id') != doc.get('claim_id'):
            return None, 'receipt_claim_mismatch'
        return doc, ''

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

        # 1. lost-ack/started guard BEFORE anything else: a claim without a
        #    valid receipt = UNKNOWN forever (restart included; no
        #    re-launch); a claim WITH a valid receipt = STARTED (heals a
        #    state file that lags behind the receipt).
        if self.claim_exists():
            doc, reason = self.valid_started_receipt()
            if doc is not None:
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
            before = _sha256_file(self.claim_path)
            self._record_check(state, 'UNKNOWN', f'lost_ack:{reason}', {
                'claim': str(self.claim_path),
                'claim_sha256_before': before,
                'receipt_reason': reason})
            state['_terminal_reason'] = f'lost_ack: {reason}'
            self._transition(state, 'UNKNOWN', self.linker_id,
                             self.now_iso())
            self.write_state(state)
            after = _sha256_file(self.claim_path)
            print('UNKNOWN (lost-ack): claim exists without a valid started '
                  f'receipt ({reason}).')
            print(f'  claim kept byte-identical: {before == after} '
                  f'({self.claim_path})')
            print('  NO second launch, NO claim recovery, NO receipt '
                  'rewrite. Manual adjudication required.')
            return EXIT_UNKNOWN, False

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
        argv = [str(part) for part in self.binding['preflight_argv']]
        print(f'PRECHECK: running bound preflight: {" ".join(argv[:8])}...')
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace',
                                  timeout=PREFLIGHT_TIMEOUT_S)
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
                                               'absent') \
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

        # 10. CLAIM: unique one-shot slot, atomically, before any effect
        try:
            claim_id = self.write_claim()
        except FileExistsError:
            # lost the race to a concurrent instance: do NOT launch, do
            # NOT touch the shared state
            print('CLAIM COMPETITION LOST: another instance holds the '
                  f'unique claim ({self.claim_path}); this instance exits '
                  'without launching and without modifying state')
            return EXIT_CLAIM_LOST, False
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

        # 11. START: invoke the bound launch flow (fixed argv) once
        argv = [str(part) for part in self.binding['launch_argv']]
        print(f'STARTING: {" ".join(argv[:8])}...')
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace',
                                  timeout=LAUNCH_TIMEOUT_S)
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
        receipt = {
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
            'binding_sha256': _sha256_file(self.binding_path),
            'frozen': dict(self.binding['frozen']),
            'preflight_verdict': report.get('overall'),
            'notes': [
                'STARTED receipt: the unique one-shot slot was consumed by '
                'THIS claim.',
                'If this receipt is later missing/half-written while the '
                'claim exists, the state is UNKNOWN (lost-ack): no '
                're-launch, no rewrite.',
                'The new campaign STOP path belongs to the campaign; '
                'cancelling the linker never touches it.']}
        _atomic_write_json(self.receipt_path, receipt)
        state['started'] = receipt
        if proc.returncode != 0:
            self._record_check(state, 'NO_GO',
                               'launch_command_nonzero_exit',
                               {'returncode': proc.returncode,
                                'claim_id': claim_id})
            state['_terminal_reason'] = (
                f'launch_command_nonzero_exit: rc={proc.returncode}')
            self._transition(state, 'NO_GO', self.linker_id, self.now_iso())
            self.write_state(state)
            print(f'NO_GO: bound launch flow exited rc={proc.returncode} '
                  '(receipt kept; typed terminal state)')
            return EXIT_NO_GO, False
        self._record_check(state, 'STARTED', 'started', {
            'claim_id': claim_id,
            'new_campaign_dir': self.binding['new_campaign_dir'],
            'campaign_json_written': campaign_written})
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
