"""Bounded test-only soak driver for PAL_LONGTASK_20260920_V1 (LT-01).

A test-scoped campaign loop around the project's owned-process supervision
(``polymarket_alpha_lab.research_process``). It is NOT a business scheduler,
agent framework, second ledger, or sandbox: it connects to no database,
network, credential store, or provider. The only process launcher and the
only termination path is ``run_research_process`` (Windows Job Object with
KILL_ON_JOB_CLOSE / Linux process group); this driver never enumerates or
kills processes by PID or image name, and never kills anything it did not
start itself through that layer.

Control contract (queue schema pal-local-longtask-v1):
- Fixed candidate identity plus fixed master seed; each round derives its
  sub-seed from (master_seed, monotonic round number).
- Per-round start record and final state in {passed, failed, interrupted,
  unknown}; unknown is used when the outcome cannot be proven.
- Heartbeat, checkpoint, and summary cadence plus own-resource sampling
  (own RSS, Python threads, in-flight owned child flag, campaign bytes,
  volume free bytes); anything not measurable is recorded as not measured.
- STOP file at ``<campaign>/stop`` and cooperative SIGINT/SIGBREAK/SIGTERM.
- Restart opens a NEW segment with NEW round numbers; rounds left running
  by a dead driver are finalized interrupted (or unknown when their record
  is unreadable) by recovery, never rerun under the same round number.
- Log caps pause instead of overwriting evidence. The first complete
  failure is append-only (O_EXCL) and is never rewritten or deleted.
- Every record read is capped at the I/O layer (a read never allocates
  past the cap); a record over its cap is refused as unreadable/invalid
  instead of being read in full and truncated afterwards.
- Only successful rounds' own-marked temporary directories are cleaned;
  symlinks/reparse points and anything resolving outside the campaign
  root are refused. Uncertain cleanup stops the whole driver.

Usage:
  python tests/support/soak_driver.py run --campaign DIR --config FILE
  python tests/support/soak_driver.py inspect --campaign DIR

Exit codes: 0 completed/stopped, 1 internal error, 3 evidence failure or
uncertain cleanup, 4 another live instance holds the lock, 5 invalid or
mismatched configuration, 6 paused by a resource limit.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import sysconfig
import threading
import time

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / 'src'))

from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from polymarket_alpha_lab.research_process import (
    ResearchProcessError,
    ResearchProcessSpec,
    run_research_process,
)

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_EVIDENCE = 3
EXIT_LOCK_BUSY = 4
EXIT_CONFIG = 5
EXIT_PAUSED = 6

FINAL_STATES = ('passed', 'failed', 'interrupted', 'unknown')
CLOSE_REASONS = ('complete', 'stop_file', 'signal', 'paused_logs', 'paused_disk',
                 'evidence_failure', 'cleanup_uncertain')

# Bounded-input caps (R6). Reads are limited before any allocation: each
# bounded read touches at most cap bytes, and a record larger than its cap
# is refused outright (treated as unreadable/invalid), never read in full
# and sliced afterwards.
RECORD_MAX_BYTES = 2 * 1048576        # campaign/lock/round/segment JSON records
MANIFEST_MAX_BYTES = 16 * 1048576     # scenario manifest (256 x 30,000-byte sources)
CONFIG_MAX_BYTES = 1048576            # campaign configuration document
JUNIT_MAX_BYTES = 1048576             # pytest receipt XML
STOP_NOTE_MAX_BYTES = 256             # stop-file note snippet
HEARTBEAT_LINE_MAX_BYTES = 65536      # one heartbeat JSONL line
HEARTBEAT_MAX_LINES = 65536           # heartbeat lines read per segment (inspect)

# kind='subinput' wiring (docs/contracts/soak-subinput-receipt-v1.md). The
# driver checks only the contract's light subset here; whitelist recomputation,
# index continuity, header byte budget, and deduplication stay with the
# independent audit (N2). New evidence lives in new files and new field names;
# the five-field payload bytes, ``_validate_receipt``, and the legacy
# ``_distinct_inputs`` semantics are deliberately untouched.
SUBINPUT_SCHEMA = 'pal-soak-subinput-receipt-v1'
SUBINPUT_RECEIPT_MAX_BYTES = 524288   # one per-family receipt file (512KiB)
SUBINPUT_PLANNED_ROWS_MAX = 4096      # absolute per-round row budget (contract 2.4)
SUBINPUT_FAMILIES = ('capture-codec', 'paper-decimal-fill', 'uncapped-authz-codec')
SUBINPUT_COUNT_KEYS = ('planned', 'generated', 'attempted', 'completed', 'oracle_passed')
_SUBINPUT_RECEIPT_KEYS = frozenset({
    'schema', 'family', 'entry', 'normalize_rule', 'candidate', 'manifest_sha256',
    'generator_sha256', 'contract_sha256', 'round', 'segment', 'sub_seed',
    'scenario', 'index_origin', 'oracle', 'counts', 'rows'})
_HEX64_RE = re.compile('[0-9a-f]{64}')
_SIGNAL_NAMES = {int(signal.SIGINT): 'sigint'}
for _name in ('SIGTERM', 'SIGBREAK'):
    if hasattr(signal, _name):
        _SIGNAL_NAMES[int(getattr(signal, _name))] = _name.lower()


class SoakConfigError(ValueError):
    """Fixed-token configuration failure; no paths or file contents leak."""


class EvidenceWriteFailure(RuntimeError):
    """An evidence write failed; the driver must fail closed, not continue."""


def _number(name, value, low, high):
    if type(value) not in (int, float) or isinstance(value, bool) or not low <= value <= high:
        raise SoakConfigError(f'soak_config_invalid:{name}')
    return value


def derive_sub_seed(master_seed: int, round_no: int) -> int:
    """Deterministic sub-seed from the fixed master seed and round number."""
    digest = sha256(f'soak:{master_seed}:{round_no}'.encode('ascii')).hexdigest()
    return int(digest[:16], 16)


def _sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 16), b''):
            digest.update(block)
    return digest.hexdigest()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _read_capped(path: Path, max_bytes: int) -> bytes | None:
    """Bounded file read: at most ``max_bytes`` bytes ever leave the file.

    The bound is enforced by the read sizes themselves (one read of
    ``max_bytes`` plus a one-byte over-limit probe), so a damaged or padded
    record can never make this driver allocate past the cap. Returns None
    for a missing file, an I/O error, or a record over its cap — an
    explicit refusal, never a silently truncated read.
    """
    try:
        with open(path, 'rb') as handle:
            data = handle.read(max_bytes)
            if handle.read(1):
                return None  # over the cap: refuse instead of truncating
            return data
    except OSError:
        return None


def _read_json(path: Path, max_bytes: int = RECORD_MAX_BYTES):
    raw = _read_capped(path, max_bytes)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None


def _read_stop_note(path: Path) -> str:
    """Bounded stop-file snippet (R6: never read-all then slice)."""
    try:
        with open(path, 'rb') as handle:
            return handle.read(STOP_NOTE_MAX_BYTES).decode('utf-8', 'replace')[:200].strip()
    except OSError:
        return ''


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write-through replace; a crash leaves either old or new content."""
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _append_line(path: Path, data: bytes) -> None:
    with open(path, 'ab') as handle:
        handle.write(data)
        handle.flush()


def _is_dangerous_path(path: Path, root: Path) -> bool:
    """Refuse links/reparse points and anything resolving outside root."""
    try:
        if path.is_symlink():
            return True
        if os.name == 'nt' and os.stat(path, follow_symlinks=False).st_file_attributes & 0x400:
            return True  # FILE_ATTRIBUTE_REPARSE_POINT
        return not path.resolve().is_relative_to(root.resolve())
    except OSError:
        return True


def _pid_alive(pid) -> bool | None:
    """Read-only liveness probe for one recorded instance PID.

    Never terminates anything: POSIX uses signal 0, Windows opens the
    process with SYNCHRONIZE and polls the handle. None means unknown.
    """
    if type(pid) is not int or pid <= 0:
        return None
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                return False
            try:
                status = kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0)
                return status == 258  # WAIT_TIMEOUT means still running
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


def _own_rss_bytes() -> int | None:
    """Best-effort own working set; None records 'not measured'."""
    try:
        if os.name == 'nt':
            import ctypes
            class _Counters(ctypes.Structure):
                _fields_ = [('cb', ctypes.c_uint32), ('PageFaultCount', ctypes.c_uint32),
                            ('PeakWorkingSetSize', ctypes.c_size_t),
                            ('WorkingSetSize', ctypes.c_size_t),
                            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                            ('PagefileUsage', ctypes.c_size_t),
                            ('PeakPagefileUsage', ctypes.c_size_t)]
            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            try:
                api = ctypes.WinDLL('kernel32', use_last_error=True)
                get_info = api.GetProcessMemoryInfo
            except AttributeError:
                api = ctypes.WinDLL('psapi', use_last_error=True)
                get_info = api.GetProcessMemoryInfo
            get_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
            current = ctypes.c_void_p(-1)  # GetCurrentProcess pseudo-handle
            if get_info(current, ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
            return None
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except Exception:
        return None


def _tree_bytes(path: Path) -> tuple[int, bool]:
    """Sum file sizes under one campaign-owned directory."""
    total = 0
    measured = True
    try:
        for current, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.lstat(Path(current) / name).st_size
                except OSError:
                    measured = False
    except OSError:
        measured = False
    return total, measured


def _volume_free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _pinned_purelib() -> str:
    """This interpreter's own site-packages, immune to user-site shadowing.

    Deriving the pytest child's PYTHONPATH from an ambient ``import pytest``
    resolution is unsound: ``site`` orders a user site-packages BEFORE this
    interpreter's own site-packages, and such a directory can hold pytest
    without the rest of the pinned dependencies. The ``-S`` scenario child
    strips its own site-packages, so PYTHONPATH becomes the only dependency
    source and anything the ambient directory lacks (observed in the formal
    soak: tzdata, present in the pinned runtime but not in the user site)
    silently vanishes from every pytest round. Resolve the pinned
    interpreter's purelib instead, and refuse to construct the scenario
    when pytest is absent from it rather than degrade silently.
    """
    purelib = Path(sysconfig.get_paths()['purelib']).resolve()
    if not (purelib / 'pytest' / '__init__.py').is_file():
        raise SoakConfigError('soak_pytest_site_invalid')
    return str(purelib)


@dataclass(frozen=True, slots=True)
class SoakScenario:
    name: str
    kind: str  # 'process' | 'pytest' | 'subinput'
    code: str = ''
    files: tuple[str, ...] = ()
    module: str = ''            # subinput: generator import name (-S -m <module>)
    planned_rows: int = 0       # subinput: per-round planned rows (1..4096)
    families: tuple[str, ...] = ()  # subinput: enabled contract families (sorted)


def _load_manifest(path: Path) -> tuple[tuple[SoakScenario, ...], str]:
    document = _read_json(path, MANIFEST_MAX_BYTES)
    if type(document) is not dict or type(document.get('scenarios')) is not list \
            or not 1 <= len(document['scenarios']) <= 256:
        raise SoakConfigError('soak_manifest_invalid')
    seen, entries = set(), []
    for item in document['scenarios']:
        if type(item) is not dict or type(item.get('name')) is not str:
            raise SoakConfigError('soak_manifest_invalid')
        name, kind = item['name'], item.get('kind')
        if name in seen or not 1 <= len(name) <= 120 \
                or re.fullmatch('[A-Za-z0-9_.-]+', name) is None \
                or kind not in ('process', 'pytest', 'subinput'):
            raise SoakConfigError('soak_manifest_invalid')
        if kind == 'process':
            code = item.get('code')
            if type(code) is not str or not 1 <= len(code.encode('utf-8')) <= 30000 or '\x00' in code:
                raise SoakConfigError('soak_manifest_invalid')
            entries.append(SoakScenario(name, 'process', code=code))
        elif kind == 'subinput':
            module = item.get('module')
            if type(module) is not str or not 1 <= len(module) <= 200 \
                    or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*',
                                    module) is None:
                raise SoakConfigError('soak_manifest_invalid')
            planned_rows = item.get('planned_rows')
            if type(planned_rows) is not int or isinstance(planned_rows, bool) \
                    or not 1 <= planned_rows <= SUBINPUT_PLANNED_ROWS_MAX:
                raise SoakConfigError('soak_manifest_invalid')
            families = item.get('families')
            if type(families) is not list or not 1 <= len(families) <= len(SUBINPUT_FAMILIES) \
                    or any(type(family) is not str or family not in SUBINPUT_FAMILIES
                           for family in families) \
                    or len(set(families)) != len(families):
                raise SoakConfigError('soak_manifest_invalid')
            entries.append(SoakScenario(name, 'subinput', module=module,
                                        planned_rows=planned_rows,
                                        families=tuple(sorted(families))))
        else:
            files = item.get('files')
            if type(files) is not list or not 1 <= len(files) <= 64:
                raise SoakConfigError('soak_manifest_invalid')
            resolved = []
            for entry in files:
                if type(entry) is not str or '\x00' in entry:
                    raise SoakConfigError('soak_manifest_invalid')
                candidate = Path(entry)
                if not candidate.is_absolute():
                    candidate = _REPO_ROOT / candidate
                try:
                    if (candidate.is_symlink() or not candidate.is_file()
                            or candidate.suffix != '.py'):
                        raise SoakConfigError('soak_manifest_invalid')
                except OSError:
                    raise SoakConfigError('soak_manifest_invalid') from None
                resolved.append(str(candidate))
            entries.append(SoakScenario(name, 'pytest', files=tuple(resolved)))
        seen.add(name)
    ordered = tuple(sorted(entries, key=lambda item: item.name))
    # Identity keys for process/pytest entries stay exactly {name, kind, code,
    # files}: a manifest without subinput scenarios hashes identically to the
    # pre-subinput driver. A subinput entry adds its own wiring identity.
    identity = []
    for scenario in ordered:
        entry = {'name': scenario.name, 'kind': scenario.kind,
                 'code': scenario.code, 'files': list(scenario.files)}
        if scenario.kind == 'subinput':
            entry['module'] = scenario.module
            entry['planned_rows'] = scenario.planned_rows
            entry['families'] = list(scenario.families)
        identity.append(entry)
    return ordered, _sha256_bytes(_canonical(identity))


@dataclass(frozen=True, slots=True)
class SoakConfig:
    master_seed: int
    round_period_seconds: float
    heartbeat_seconds: float
    checkpoint_seconds: float
    summary_seconds: float
    max_unobserved_gap_seconds: float
    scenario_timeout_ms: int
    cleanup_timeout_ms: int
    per_round_log_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_total_log_bytes: int
    max_repro_files: int
    volume_min_free_bytes: int
    min_rounds: int
    max_rounds: int | None
    max_wall_seconds: float | None
    workers: int
    candidate: dict
    scenario_manifest: str

    @classmethod
    def from_dict(cls, document) -> 'SoakConfig':
        if type(document) is not dict:
            raise SoakConfigError('soak_config_invalid:document')
        candidate = document.get('candidate', {})
        if type(candidate) is not dict:
            raise SoakConfigError('soak_config_invalid:candidate')
        manifest = document.get('scenario_manifest')
        if type(manifest) is not str or not manifest:
            raise SoakConfigError('soak_config_invalid:scenario_manifest')
        cfg = cls(
            master_seed=int(_number('master_seed', document.get('master_seed'), 1, 2**63 - 1)),
            round_period_seconds=_number('round_period_seconds',
                                         document.get('round_period_seconds', 300), 0.1, 86400),
            heartbeat_seconds=_number('heartbeat_seconds', document.get('heartbeat_seconds', 60), 0.1, 3600),
            checkpoint_seconds=_number('checkpoint_seconds', document.get('checkpoint_seconds', 7200), 0.2, 604800),
            summary_seconds=_number('summary_seconds', document.get('progress_summary_seconds', 43200), 0.5, 604800),
            max_unobserved_gap_seconds=_number('max_unobserved_gap_seconds',
                                               document.get('max_unobserved_gap_seconds', 900), 1, 604800),
            scenario_timeout_ms=int(_number('scenario_timeout_ms',
                                            document.get('scenario_timeout_ms', 240000), 250, 3600000)),
            cleanup_timeout_ms=int(_number('cleanup_timeout_ms', document.get('cleanup_timeout_ms', 5000), 100, 30000)),
            per_round_log_bytes=int(_number('per_round_log_bytes',
                                            document.get('per_round_log_bytes', 1048576), 256, 1048576)),
            max_stdout_bytes=int(_number('max_stdout_bytes', document.get('max_stdout_bytes', 1048576), 64, 1048576)),
            max_stderr_bytes=int(_number('max_stderr_bytes', document.get('max_stderr_bytes', 65536), 64, 1048576)),
            max_total_log_bytes=int(_number('max_total_log_bytes',
                                            document.get('max_evidence_bytes', 536870912), 1024, 2**40)),
            max_repro_files=int(_number('max_repro_files', document.get('max_repro_files', 100), 0, 100)),
            volume_min_free_bytes=int(_number('volume_min_free_bytes',
                                              document.get('minimum_volume_free_bytes', 10737418240), 0, 2**50)),
            min_rounds=int(_number('min_rounds', document.get('minimum_valid_rounds', 1), 1, 10**7)),
            max_rounds=(int(_number('max_rounds', document.get('max_rounds'), 1, 10**7))
                        if document.get('max_rounds') is not None else None),
            max_wall_seconds=(_number('max_wall_seconds', document.get('max_wall_seconds'), 1, 1209600)
                              if document.get('max_wall_seconds') is not None else None),
            workers=int(_number('workers', document.get('workers', 1), 1, 2)),
            candidate=candidate,
            scenario_manifest=str(Path(manifest).resolve()),
        )
        return cfg

    def canonical_bytes(self) -> bytes:
        return _canonical({
            'master_seed': self.master_seed,
            'round_period_seconds': self.round_period_seconds,
            'heartbeat_seconds': self.heartbeat_seconds,
            'checkpoint_seconds': self.checkpoint_seconds,
            'progress_summary_seconds': self.summary_seconds,
            'max_unobserved_gap_seconds': self.max_unobserved_gap_seconds,
            'scenario_timeout_ms': self.scenario_timeout_ms,
            'cleanup_timeout_ms': self.cleanup_timeout_ms,
            'per_round_log_bytes': self.per_round_log_bytes,
            'max_stdout_bytes': self.max_stdout_bytes,
            'max_stderr_bytes': self.max_stderr_bytes,
            'max_evidence_bytes': self.max_total_log_bytes,
            'max_repro_files': self.max_repro_files,
            'minimum_volume_free_bytes': self.volume_min_free_bytes,
            'minimum_valid_rounds': self.min_rounds,
            'max_rounds': self.max_rounds,
            'max_wall_seconds': self.max_wall_seconds,
            'workers': self.workers,
            'candidate': self.candidate,
            'scenario_manifest': self.scenario_manifest,
        })


class SoakDriver:
    """One bounded campaign invocation; one segment per invocation."""

    def __init__(self, config: SoakConfig, campaign_root: Path):
        self.config = config
        self.root = Path(campaign_root).resolve()  # owned children need absolute cwd
        self.scenarios, self.manifest_sha = _load_manifest(Path(config.scenario_manifest))
        self.driver_sha = _sha256_file(Path(__file__).resolve())
        self.python = str(Path(sys.executable).resolve())
        self.python_sha = _sha256_file(Path(self.python))
        self.stop = ResearchDispatchStop()
        self.stop_path = self.root / 'stop'
        self.lock_path = self.root / 'driver.lock'
        self._stop_source: str | None = None
        self._stop_note = ''
        self._signal_name: str | None = None
        self._evidence_failure = False
        self._cleanup_uncertain = False
        self._in_flight = False
        self._done = threading.Event()
        self._io_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._segment_no = 0
        self._segment_dir: Path | None = None
        self._next_round_no = 1
        self._totals = {'passed': 0, 'failed': 0, 'interrupted': 0, 'unknown': 0}
        self._distinct_inputs: set[str] = set()
        self._subinput_rows_completed = 0  # declared completed rows, passed subinput rounds
        self._recovery: dict = {}
        self._last_heartbeat_wall = 0.0
        self._gaps: list[dict] = []
        self._last_checkpoint_wall = 0.0
        self._last_summary_wall = 0.0
        self._rss_samples: list[int] = []
        self._disk_samples: list[int] = []
        self._started_wall = 0.0
        self._restore_handlers: dict[int, object] = {}

    # ---------- evidence helpers (write failures are EvidenceWriteFailure) ----------

    def _write_json(self, path: Path, obj) -> None:
        try:
            _atomic_write_bytes(path, _canonical(obj) + b'\n')
        except OSError:
            raise EvidenceWriteFailure(f'evidence_write_failed:{path.name}') from None

    def _append_jsonl(self, path: Path, obj) -> None:
        try:
            _append_line(path, _canonical(obj) + b'\n')
        except OSError:
            raise EvidenceWriteFailure(f'evidence_write_failed:{path.name}') from None

    def _driver_log(self, event: str, **fields) -> None:
        entry = {'wall': time.time(), 'event': event, **fields}
        path = self.root / 'driver.log'
        cap = max(self.config.per_round_log_bytes, 262144)
        try:
            if path.exists() and path.stat().st_size >= cap:
                rotated = self.root / 'driver.log.1'
                if rotated.exists():
                    rotated.unlink()  # rotated diagnostics only; evidence is never removed
                os.replace(path, rotated)
        except OSError:
            raise EvidenceWriteFailure('evidence_write_failed:driver.log') from None
        self._append_jsonl(path, entry)

    # ---------- boot / lock / recovery ----------

    def run(self) -> int:
        try:
            boot = self._boot()
        except EvidenceWriteFailure:
            print('SOAK_EVIDENCE_FAILURE boot')
            return EXIT_EVIDENCE
        if boot != EXIT_OK:
            return boot
        code, reason = EXIT_INTERNAL, 'internal'
        try:
            self.install_signal_handlers()
            for target, name in ((self._stop_watch_loop, 'soak-stop-watch'),
                                 (self._heartbeat_loop, 'soak-heartbeat')):
                thread = threading.Thread(target=target, name=name, daemon=True)
                thread.start()
                self._threads.append(thread)
            self._last_checkpoint_wall = self._last_summary_wall = time.time()
            self._heartbeat_once()
            code, reason = self._loop()
        except EvidenceWriteFailure:
            code, reason = EXIT_EVIDENCE, 'evidence_failure'
        except Exception:
            code, reason = EXIT_INTERNAL, 'internal'
        finally:
            self._close_segment(reason, code)
            self._done.set()
            for thread in self._threads:
                thread.join(timeout=max(2.0, self.config.heartbeat_seconds * 2))
            self.restore_signal_handlers()
            self._release_lock()
        return code

    def _boot(self) -> int:
        try:
            if self.root.is_symlink() or (self.root.exists() and not self.root.is_dir()):
                print('SOAK_CONFIG_INVALID campaign_root')
                return EXIT_CONFIG
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            print('SOAK_CONFIG_INVALID campaign_root')
            return EXIT_CONFIG
        identity = {
            'schema': 'pal-soak-campaign-v1',
            'config_sha256': _sha256_bytes(self.config.canonical_bytes()),
            'manifest_sha256': self.manifest_sha,
            'driver_sha256': self.driver_sha,
            'python_sha256': self.python_sha,
            'stop_path': str(self.stop_path),
            'max_unobserved_gap_seconds': self.config.max_unobserved_gap_seconds,
            'candidate': self.config.candidate,
        }
        existing = _read_json(self.root / 'campaign.json')
        if existing is None:
            try:
                with open(self.root / 'campaign.json', 'x', encoding='utf-8') as handle:
                    handle.write(_canonical(identity).decode('utf-8') + '\n')
            except OSError:
                print('SOAK_EVIDENCE_FAILURE campaign.json')
                return EXIT_EVIDENCE
        elif any(existing.get(key) != value for key, value in identity.items()):
            print('SOAK_CONFIG_MISMATCH campaign.json')
            return EXIT_CONFIG
        lock = self._acquire_lock()
        if lock != EXIT_OK:
            return lock
        try:
            self._recover()
            self._open_segment()
            self._driver_log('boot', pid=os.getpid(), segment=self._segment_no,
                             rounds_base=self._next_round_no)
        except EvidenceWriteFailure:
            print('SOAK_EVIDENCE_FAILURE boot')
            self._release_lock()
            return EXIT_EVIDENCE
        return EXIT_OK

    def _acquire_lock(self) -> int:
        record = {'pid': os.getpid(), 'wall': time.time(),
                  'argv': [str(part) for part in sys.argv]}
        try:
            with open(self.lock_path, 'x', encoding='utf-8') as handle:
                handle.write(_canonical(record).decode('utf-8') + '\n')
            return EXIT_OK
        except FileExistsError:
            previous = _read_json(self.lock_path) or {}
            alive = _pid_alive(previous.get('pid'))
            if alive is True:
                print(f"SOAK_LOCK_BUSY pid={previous.get('pid')}")
                return EXIT_LOCK_BUSY
            stale_to = self.root / f'driver.lock.stale-{int(time.time())}'
            try:
                os.replace(self.lock_path, stale_to)
                with open(self.lock_path, 'x', encoding='utf-8') as handle:
                    handle.write(_canonical(record).decode('utf-8') + '\n')
            except OSError:
                print('SOAK_EVIDENCE_FAILURE driver.lock')
                return EXIT_EVIDENCE
            try:
                self._driver_log('lock_stale_recovered', previous_pid=previous.get('pid'),
                                 previous_alive=alive)
            except EvidenceWriteFailure:
                return EXIT_EVIDENCE
            return EXIT_OK
        except OSError:
            print('SOAK_EVIDENCE_FAILURE driver.lock')
            return EXIT_EVIDENCE

    def _release_lock(self) -> None:
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    def _recover(self) -> None:
        """Scan previous segments; finalize interrupted/unknown; never rerun.

        A round directory whose round.json is missing (controller died
        between creating the round directory and writing its 'running'
        record) is finalized unknown exactly like an unreadable record:
        the audit reads the directory as a round, so its number is burned
        and never handed out again by a restart.

        Every recovery pass rebuilds its totals from the complete on-disk
        evidence, so an existing unknown round is recounted once on every
        new recovery (second, third, ...) exactly like the audit's
        directory-based count; the durable unknown sidecar is written only
        by the first recovery and its bytes are never rewritten afterwards.
        """
        segments_dir = self.root / 'segments'
        segments_dir.mkdir(exist_ok=True)
        last_closed = True
        highest_round = 0
        for segment in sorted(path for path in segments_dir.iterdir() if path.is_dir()
                              and re.fullmatch(r'segment-\d{6}', path.name)):
            self._segment_no = max(self._segment_no, int(segment.name.split('-')[1]))
            if _read_json(segment / 'segment-close.json') is None:
                last_closed = False
            unknown_dir = segment / 'rounds' / 'unknown'
            for round_dir in sorted(path for path in (segment / 'rounds').iterdir()
                                    if path.is_dir() and path.name.startswith('round-')):
                record_path = round_dir / 'round.json'
                record = _read_json(record_path)
                if record is None:
                    sidecar = unknown_dir / f'{round_dir.name}.json'
                    if not sidecar.exists():  # durable marker written once, never rewritten
                        unknown_dir.mkdir(exist_ok=True)
                        self._write_json(sidecar, {
                            'final': 'unknown', 'reason': 'record_unreadable',
                            'classified_by': 'recovery', 'wall': time.time(),
                            'source': str(record_path.relative_to(segment))})
                    self._totals['unknown'] += 1  # recounted on EVERY recovery pass
                    highest_round = max(highest_round,
                                        self._round_number_from_name(round_dir.name))
                    continue
                highest_round = max(highest_round, int(record.get('round', 0)))
                final = record.get('final')
                if final in FINAL_STATES:
                    self._totals[final] += 1
                else:
                    record['final'] = 'interrupted'
                    record['reason'] = 'driver_exit_before_final'
                    record['classified_by'] = 'recovery'
                    record['recovered_wall'] = time.time()
                    self._write_json(record_path, record)
                    self._totals['interrupted'] += 1
                if type(record.get('input_sha256')) is str:
                    self._distinct_inputs.add(record['input_sha256'])
        self._next_round_no = highest_round + 1
        self._recovery = {'previous_segment_closed': last_closed,
                          'interrupted_or_unknown_finalized':
                              self._totals['interrupted'] + self._totals['unknown'],
                          'next_round_no': self._next_round_no}

    @staticmethod
    def _round_number_from_name(name: str) -> int:
        digits = name.removeprefix('round-').split('.')[0]
        try:
            return int(digits)
        except ValueError:
            return 0

    def _open_segment(self) -> None:
        self._segment_no += 1
        self._segment_dir = self.root / 'segments' / f'segment-{self._segment_no:06d}'
        (self._segment_dir / 'rounds').mkdir(parents=True, exist_ok=False)
        self._started_wall = time.time()
        self._write_json(self._segment_dir / 'segment.json', {
            'segment': self._segment_no,
            'started_wall': self._started_wall,
            'started_monotonic': time.monotonic(),
            'pid': os.getpid(),
            'argv': [str(part) for part in sys.argv],
            'cwd': os.getcwd(),
            'python_sha256': self.python_sha,
            'driver_sha256': self.driver_sha,
            'config_sha256': _sha256_bytes(self.config.canonical_bytes()),
            'manifest_sha256': self.manifest_sha,
            'rounds_base': self._next_round_no,
            'recovery': self._recovery,
            'stop_path': str(self.stop_path),
            'workers': self.config.workers,
            'execution_model': 'sequential-owned-child',
        })

    # ---------- stop / signal plumbing ----------

    def install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            self._signal_name = _SIGNAL_NAMES.get(signum, f'signal_{signum}')
            self._stop_source = self._stop_source or 'signal'
            self._stop_note = self._signal_name
            self.stop.request_stop()
        for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            try:
                self._restore_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, handler)
            except (OSError, ValueError):
                continue

    def restore_signal_handlers(self) -> None:
        for signum, previous in self._restore_handlers.items():
            try:
                signal.signal(signum, previous)
            except (OSError, ValueError):
                pass
        self._restore_handlers.clear()

    def _stop_watch_loop(self) -> None:
        interval = min(1.0, max(0.05, self.config.heartbeat_seconds / 2))
        while not self._done.wait(interval):
            try:
                exists = self.stop_path.exists()
            except OSError:
                continue
            if exists and not self.stop.is_stopped():
                self._stop_source = self._stop_source or 'stop_file'
                self._stop_note = _read_stop_note(self.stop_path)
                self.stop.request_stop()
                return

    def _heartbeat_loop(self) -> None:
        while not self._done.wait(self.config.heartbeat_seconds):
            try:
                self._heartbeat_once()
            except EvidenceWriteFailure:
                self._evidence_failure = True
                return

    def _heartbeat_once(self) -> None:
        if self._segment_dir is None:
            return
        wall = time.time()
        rss = _own_rss_bytes()
        if rss is not None:
            self._rss_samples.append(rss)
        campaign_bytes, measured = _tree_bytes(self.root)
        if measured:
            self._disk_samples.append(campaign_bytes)
        gap = None if self._last_heartbeat_wall == 0 else wall - self._last_heartbeat_wall
        if gap is not None and gap > self.config.max_unobserved_gap_seconds:
            self._gaps.append({'wall': wall, 'gap_seconds': round(gap, 3)})
        self._last_heartbeat_wall = wall
        with self._io_lock:
            self._append_jsonl(self._segment_dir / 'heartbeats.jsonl', {
                'wall': wall, 'monotonic': time.monotonic(), 'pid': os.getpid(),
                'rounds_total': dict(self._totals),
                'next_round_no': self._next_round_no,
                'owned_child_in_flight': self._in_flight,
                'rss_bytes': rss,
                'rss_measured': rss is not None,
                'python_threads': threading.active_count(),
                'os_thread_count': None,
                'os_child_count': None,
                'campaign_bytes': campaign_bytes if measured else None,
                'campaign_bytes_measured': measured,
                'volume_free_bytes': _volume_free_bytes(self.root),
                'gap_seconds': None if gap is None else round(gap, 3),
            })
            if wall - self._last_checkpoint_wall >= self.config.checkpoint_seconds:
                self._last_checkpoint_wall = wall
                self._write_json(self._segment_dir / 'checkpoint.json', self._progress('checkpoint'))
            if wall - self._last_summary_wall >= self.config.summary_seconds:
                self._last_summary_wall = wall
                self._write_json(self._segment_dir / 'summary.json', self._progress('summary'))

    def _progress(self, kind: str) -> dict:
        return {
            'kind': kind,
            'segment': self._segment_no,
            'wall': time.time(),
            'monotonic': time.monotonic(),
            'pid': os.getpid(),
            'rounds_total': dict(self._totals),
            'distinct_inputs': len(self._distinct_inputs),
            'subinput_rows_completed': self._subinput_rows_completed,
            'next_round_no': self._next_round_no,
            'gaps_over_limit': list(self._gaps),
            'rss_samples': {'count': len(self._rss_samples),
                            'first': self._rss_samples[0] if self._rss_samples else None,
                            'last': self._rss_samples[-1] if self._rss_samples else None,
                            'max': max(self._rss_samples) if self._rss_samples else None},
            'campaign_bytes_samples': {'count': len(self._disk_samples),
                                       'first': self._disk_samples[0] if self._disk_samples else None,
                                       'last': self._disk_samples[-1] if self._disk_samples else None,
                                       'max': max(self._disk_samples) if self._disk_samples else None},
            'volume_free_bytes': _volume_free_bytes(self.root),
            'config_sha256': _sha256_bytes(self.config.canonical_bytes()),
            'manifest_sha256': self.manifest_sha,
        }

    # ---------- rounds ----------

    def _scenario_spec(self, scenario: SoakScenario, round_tmp: Path):
        if os.name == 'nt':
            environment: list[tuple[str, str]] = [('SystemRoot', os.environ['SystemRoot'])]
        else:
            environment = []
        if scenario.kind == 'process':
            argv = (self.python, '-I', '-S', '-c', scenario.code)
            cwd = str(round_tmp)
        elif scenario.kind == 'subinput':
            # Generator child: repo root first (tests package + the module
            # named by the manifest), then the pinned purelib — measured on
            # this host, ZoneInfo('America/New_York') fails under -S without
            # it, and the capture-codec contract family needs it. Same pinned
            # dependency rule as the pytest branch; -S keeps PYTHONPATH the
            # only dependency source, so no ambient site can shadow either.
            environment = [*environment,
                           ('PYTHONPATH', os.pathsep.join([str(_REPO_ROOT), _pinned_purelib()])),
                           ('PYTHONUTF8', '1'), ('PYTHONDONTWRITEBYTECODE', '1')]
            argv = (self.python, '-S', '-m', scenario.module)
            cwd = str(round_tmp)
        else:
            site_dir = _pinned_purelib()
            environment = [*environment, ('PYTHONPATH', site_dir),
                           ('PYTHONUTF8', '1'), ('PYTHONDONTWRITEBYTECODE', '1'),
                           ('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')]
            argv = (self.python, '-S', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                    '--basetemp', str(round_tmp / 'pytest'),
                    '--junitxml', str(round_tmp / 'pytest.xml'),
                    *scenario.files)
            cwd = str(_REPO_ROOT)
        return ResearchProcessSpec(
            argv=argv, cwd=cwd, environment=tuple(environment),
            executable_sha256=self.python_sha,
            timeout_ms=self.config.scenario_timeout_ms,
            max_stdin_bytes=16000000,
            max_stdout_bytes=self.config.max_stdout_bytes,
            max_stderr_bytes=self.config.max_stderr_bytes,
            cleanup_timeout_ms=self.config.cleanup_timeout_ms,
        )

    def _validate_receipt(self, scenario: SoakScenario, stdout: bytes, round_no: int,
                          sub_seed: int, round_tmp: Path):
        if scenario.kind == 'process':
            try:
                receipt = json.loads(stdout.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                return None, 'receipt_invalid'
            if (type(receipt) is not dict or receipt.get('echo_round') != round_no
                    or receipt.get('echo_seed') != sub_seed):
                return None, 'receipt_invalid'
            return receipt, None
        junit = round_tmp / 'pytest.xml'
        try:
            import xml.etree.ElementTree as etree
            raw = _read_capped(junit, JUNIT_MAX_BYTES)
            if raw is None:  # over the cap: refused, never truncated
                return None, 'receipt_invalid'
            root = etree.fromstring(raw)
            if root.tag == 'testsuites':
                root = root.find('testsuite')
            receipt = {key: int(root.get(key, 0))
                       for key in ('tests', 'failures', 'errors', 'skipped')}
        except (OSError, ValueError, SyntaxError, AttributeError, TypeError):
            return None, 'receipt_invalid'
        if receipt['failures'] or receipt['errors']:
            return receipt, 'nonzero_exit'
        return receipt, None

    # ---------- kind='subinput' receipt handling (contract light subset) ----------

    @staticmethod
    def _subinput_counts_ok(counts) -> bool:
        """Contract decision 5 invariant helper: five ints, monotonic chain."""
        if type(counts) is not dict or set(counts) != set(SUBINPUT_COUNT_KEYS):
            return False
        for value in counts.values():
            if type(value) is not int or isinstance(value, bool) or value < 0:
                return False
        return (counts['planned'] >= counts['generated'] >= counts['attempted']
                >= counts['oracle_passed'] == counts['completed'])

    @classmethod
    def _subinput_document_reason(cls, document, expected_family: str, scenario_name: str,
                                  manifest_sha: str, round_no: int, segment_no: int,
                                  sub_seed: int) -> str | None:
        """Fixed-reason structural check of one receipt document.

        Light driver subset of contract decisions 1/2.2/2.3/7: exact schema
        string, exact top-level field set, transport echo bindings, declared
        family/entry shape, lowercase-hex64 two-element rows without a
        duplicate input hash, and the five-count chain with
        ``len(rows) == counts['completed']``. Returns 'receipt_invalid',
        'counts_inconsistent', or None. NOT checked here (independent audit
        owns them): whitelist recomputation of both hashes, index continuity,
        header 2048-byte budget, candidate/generator/contract sha semantics.
        """
        if type(document) is not dict or set(document) != _SUBINPUT_RECEIPT_KEYS:
            return 'receipt_invalid'
        strings = ('family', 'entry', 'normalize_rule', 'candidate', 'manifest_sha256',
                   'generator_sha256', 'contract_sha256', 'oracle')
        for key in strings:
            if type(document[key]) is not str or not 1 <= len(document[key]) <= 65536:
                return 'receipt_invalid'
        if (document['schema'] != SUBINPUT_SCHEMA
                or document['family'] != expected_family
                or re.fullmatch(re.escape(expected_family) + r'/[a-z0-9-]{1,32}',
                                document['entry']) is None
                or _HEX64_RE.fullmatch(document['manifest_sha256']) is None
                or _HEX64_RE.fullmatch(document['generator_sha256']) is None
                or _HEX64_RE.fullmatch(document['contract_sha256']) is None
                or document['manifest_sha256'] != manifest_sha
                or type(document['round']) is not int or document['round'] != round_no
                or type(document['segment']) is not int or document['segment'] != segment_no
                or type(document['sub_seed']) is not int or document['sub_seed'] != sub_seed
                or document['scenario'] != scenario_name
                or type(document['index_origin']) is not int
                or isinstance(document['index_origin'], bool) or document['index_origin'] < 0):
            return 'receipt_invalid'
        counts, rows = document['counts'], document['rows']
        if type(rows) is not list:
            return 'receipt_invalid'
        if not cls._subinput_counts_ok(counts) or counts['completed'] != len(rows):
            return 'counts_inconsistent'
        seen_inputs: set[str] = set()
        for row in rows:
            if type(row) is not list or len(row) != 2 \
                    or any(type(cell) is not str or _HEX64_RE.fullmatch(cell) is None
                           for cell in row):
                return 'receipt_invalid'
            if row[0] in seen_inputs:  # duplicate input hash: whole file rejected
                return 'receipt_invalid'
            seen_inputs.add(row[0])
        return None

    def _validate_subinput_receipts(self, scenario: SoakScenario, stdout: bytes,
                                    round_no: int, sub_seed: int, round_tmp: Path):
        """S4' — validate the generator's stdout summary and its receipts.

        Reads every receipt file the summary declares under ``round_tmp``
        with a bounded 512KiB-plus-probe read, checks the summary shape
        (echo fields, one entry per manifest-declared family, contract file
        name), then each file's light structure and the summary-vs-file
        sha256. Returns ``(summary, metas, totals, reason)``; ``metas`` is a
        list of ``{family, entry, file, rows, sha256}`` pointers ready for
        promotion and round.json registration, ``totals`` the five-count
        aggregate. Any failure keeps the round fail-closed with one of the
        fixed words receipt_missing / receipt_invalid / receipt_over_limit /
        receipt_sha_mismatch / counts_inconsistent and no promotion.
        """
        try:
            summary = json.loads(stdout.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None, None, None, 'receipt_invalid'
        if type(summary) is not dict:
            return None, None, None, 'receipt_invalid'
        if summary.get('echo_round') != round_no or summary.get('echo_seed') != sub_seed:
            return summary, None, None, 'receipt_invalid'
        declared = summary.get('subinput_families')
        if type(declared) is not list or len(declared) != len(scenario.families):
            return summary, None, None, 'receipt_invalid'
        metas: list[dict] = []
        totals = dict.fromkeys(SUBINPUT_COUNT_KEYS, 0)
        seen_families: set[str] = set()
        for item in declared:
            if type(item) is not dict:
                return summary, None, None, 'receipt_invalid'
            family, entry = item.get('family'), item.get('entry')
            file_name, rows_declared = item.get('file'), item.get('rows')
            sha_declared, counts_declared = item.get('sha256'), item.get('counts')
            if (family not in scenario.families or family in seen_families
                    or type(entry) is not str or type(file_name) is not str
                    or file_name != f'subinputs-{family}.json'
                    or type(rows_declared) is not int or isinstance(rows_declared, bool)
                    or rows_declared < 0
                    or type(sha_declared) is not str
                    or _HEX64_RE.fullmatch(sha_declared) is None):
                return summary, None, None, 'receipt_invalid'
            seen_families.add(family)
            if not self._subinput_counts_ok(counts_declared):
                return summary, None, None, 'counts_inconsistent'
            source = round_tmp / file_name
            try:
                source.stat()
            except OSError:
                return summary, None, None, 'receipt_missing'
            raw = _read_capped(source, SUBINPUT_RECEIPT_MAX_BYTES)
            if raw is None:  # over the 512KiB budget: refused, never truncated
                return summary, None, None, 'receipt_over_limit'
            if _sha256_bytes(raw) != sha_declared:
                return summary, None, None, 'receipt_sha_mismatch'
            try:
                document = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                return summary, None, None, 'receipt_invalid'
            reason = self._subinput_document_reason(document, family, scenario.name,
                                                    self.manifest_sha, round_no,
                                                    self._segment_no, sub_seed)
            if reason is not None:
                return summary, None, None, reason
            rows = document['rows']
            if counts_declared != document['counts'] or rows_declared != len(rows):
                return summary, None, None, 'counts_inconsistent'
            metas.append({'family': family, 'entry': entry, 'file': file_name,
                          'rows': len(rows), 'sha256': sha_declared})
            for key in SUBINPUT_COUNT_KEYS:
                totals[key] += document['counts'][key]
        return summary, metas, totals, None

    def _promote_subinput_receipts(self, round_tmp: Path, round_dir: Path,
                                   metas: list[dict]) -> None:
        """S5' — move validated receipts out of tmp before any cleanup.

        Promotion happens only after validation passed and always before the
        passed-round tmp cleanup, so ``shutil.rmtree`` can never destroy the
        only copy. ``os.replace`` is the atomic rename within one round
        directory pair; any failure is an evidence failure (fail closed).
        """
        for meta in metas:
            try:
                os.replace(round_tmp / meta['file'], round_dir / meta['file'])
            except OSError:
                raise EvidenceWriteFailure(
                    f"evidence_write_failed:{meta['file']}") from None

    def _run_round(self, round_no: int) -> None:
        if self._segment_dir is None:
            raise EvidenceWriteFailure('evidence_write_failed:no_segment')
        sub_seed = derive_sub_seed(self.config.master_seed, round_no)
        scenario = self.scenarios[sub_seed % len(self.scenarios)]
        round_dir = self._segment_dir / 'rounds' / f'round-{round_no:09d}'
        round_dir.mkdir(parents=True, exist_ok=False)
        round_tmp = round_dir / 'tmp'
        round_tmp.mkdir()
        payload = _canonical({'round': round_no, 'segment': self._segment_no,
                              'sub_seed': sub_seed, 'scenario': scenario.name,
                              'tmp_dir': str(round_tmp)}) + b'\n'
        input_sha = _sha256_bytes(payload)
        self._write_json(round_dir / 'round.json', {
            'round': round_no, 'segment': self._segment_no,
            'sub_seed': sub_seed, 'scenario': scenario.name,
            'input_sha256': input_sha, 'status': 'running',
            'started_wall': time.time()})
        started = time.monotonic()
        receipt, stdout, stderr_bytes, final, reason = None, b'', 0, None, None
        receipts_meta = counts_agg = None
        self._in_flight = True
        try:
            spec = self._scenario_spec(scenario, round_tmp)
            result = run_research_process(spec=spec, stdin=payload,
                                          allow_process_start=True, stop=self.stop)
        except ResearchProcessError as error:
            text = str(error)
            if text == 'research_process_stopped':
                final, reason = 'interrupted', 'stopped'
            elif text == 'research_process_cleanup_failed':
                final, reason = 'unknown', 'cleanup_failed'
            elif text == 'research_process_failed':
                final, reason = 'unknown', 'opaque_failure'
            else:
                final = 'failed'
                reason = ('timeout' if text.endswith('timeout')
                          else 'output_limit' if text.endswith('output_limit')
                          else 'nonzero_exit' if text.endswith('nonzero_exit')
                          else 'not_started' if text.endswith('not_started')
                          else 'input_incomplete' if text.endswith('input_incomplete')
                          else 'scenario_error')
        except (OSError, ValueError):
            final, reason = 'failed', 'scenario_error'
        else:
            stdout = result.stdout
            stderr_bytes = result.stderr_bytes
            if scenario.kind == 'subinput':
                # S4' light validation (missing generator module surfaces here
                # as the child's own nonzero exit above, never an unknown).
                receipt, receipts_meta, counts_agg, invalid = \
                    self._validate_subinput_receipts(scenario, stdout, round_no,
                                                     sub_seed, round_tmp)
                if invalid is None and receipts_meta:
                    self._promote_subinput_receipts(round_tmp, round_dir, receipts_meta)
            else:
                receipt, invalid = self._validate_receipt(scenario, stdout, round_no,
                                                          sub_seed, round_tmp)
            final, reason = ('passed', None) if invalid is None else ('failed', invalid)
        self._in_flight = False
        record = {
            'round': round_no, 'segment': self._segment_no,
            'sub_seed': sub_seed, 'scenario': scenario.name,
            'input_sha256': input_sha, 'status': 'final', 'final': final,
            'reason': reason, 'finished_wall': time.time(),
            'elapsed_ms': int((time.monotonic() - started) * 1000),
            'stderr_bytes': stderr_bytes, 'receipt': receipt,
        }
        if receipts_meta is not None:  # promoted receipts: pointers + declared counts
            record['subinput_receipts'] = receipts_meta
            record['subinput_counts'] = counts_agg
        # Per-round log: capped stdout snippet; never exceeds per_round_log_bytes.
        header = _canonical({'round': round_no, 'scenario': scenario.name,
                             'final': final, 'reason': reason}).strip()
        body = stdout[:max(0, self.config.per_round_log_bytes - len(header) - 32)]
        truncated = len(stdout) > len(body)
        try:
            _append_line(round_dir / 'round.log',
                         header + b'\n' + body + (b'\n[truncated]' if truncated else b'') + b'\n')
        except OSError:
            raise EvidenceWriteFailure('evidence_write_failed:round.log') from None
        record['log_truncated'] = truncated
        self._write_json(round_dir / 'round.json', record)
        self._totals[final] += 1
        self._distinct_inputs.add(input_sha)
        if final == 'passed' and counts_agg is not None:
            # Declared progress counter only — never named distinct_qualified,
            # which is the independent audit's exclusive proof field.
            self._subinput_rows_completed += counts_agg['completed']
        self._driver_log('round_final', round=round_no, final=final, reason=reason,
                         scenario=scenario.name, elapsed_ms=record['elapsed_ms'])
        if final == 'failed':
            self._record_failure_evidence(record, scenario, payload, stdout, round_dir)
        elif final == 'passed':
            self._cleanup_passed_tmp(round_tmp, round_no)

    def _record_failure_evidence(self, record, scenario, payload, stdout, round_dir) -> None:
        # First complete failure: append-only via O_EXCL; never rewritten.
        first = {'round': record['round'], 'segment': record['segment'],
                 'scenario': scenario.name, 'reason': record['reason'],
                 'sub_seed': record['sub_seed'], 'wall': record['finished_wall'],
                 'receipt': record['receipt'], 'log': str(round_dir / 'round.log')}
        first_path = self.root / 'first-failure.json'
        try:
            with open(first_path, 'x', encoding='utf-8') as handle:
                handle.write(_canonical(first).decode('utf-8') + '\n')
            with open(self.root / 'first-failure.log', 'x', encoding='utf-8') as handle:
                handle.write('payload: ' + payload.decode('utf-8', 'replace'))
                handle.write('\nstdout: ' + stdout.decode('utf-8', 'replace'))
                handle.write('\nreason: ' + str(record['reason']) + '\n')
        except FileExistsError:
            if first_path.is_dir():  # path collision, not a preserved record
                raise EvidenceWriteFailure('evidence_write_failed:first-failure') from None
        except OSError:
            raise EvidenceWriteFailure('evidence_write_failed:first-failure') from None
        # Distinct minimal reproductions, at most max_repro_files.
        repro_dir = self.root / 'failures'
        digest = _sha256_bytes(scenario.name.encode('utf-8') + b'\x00'
                               + str(record['reason']).encode('ascii') + b'\x00' + stdout)
        target = repro_dir / f'rep-{digest[:10]}.json'
        if target.exists() or len(list(repro_dir.glob('rep-*.json'))) >= self.config.max_repro_files:
            return
        try:
            repro_dir.mkdir(exist_ok=True)
            _atomic_write_bytes(target, _canonical({
                'scenario': scenario.name, 'reason': record['reason'],
                'sub_seed': record['sub_seed'], 'round': record['round'],
                'payload': payload.decode('utf-8', 'replace'),
                'stdout': stdout.decode('utf-8', 'replace')}) + b'\n')
        except OSError:
            raise EvidenceWriteFailure('evidence_write_failed:repro') from None

    def _cleanup_passed_tmp(self, round_tmp: Path, round_no: int) -> None:
        """Remove only this campaign's own successful-round scratch dirs."""
        try:
            if not round_tmp.exists():
                return
            if _is_dangerous_path(round_tmp, self.root):
                raise OSError('refused')
            shutil.rmtree(round_tmp)
            if round_tmp.exists():
                raise OSError('uncertain')
        except OSError:
            self._driver_log('cleanup_uncertain', round=round_no)
            self._cleanup_uncertain = True

    # ---------- main loop ----------

    def _loop(self) -> tuple[int, str]:
        next_start = time.monotonic()
        while True:
            if self._evidence_failure:
                return EXIT_EVIDENCE, 'evidence_failure'
            if self._cleanup_uncertain:
                return EXIT_EVIDENCE, 'cleanup_uncertain'
            if self._stop_source is not None:
                return EXIT_OK, ('signal' if self._stop_source == 'signal' else 'stop_file')
            total_rounds = sum(self._totals.values())
            elapsed = time.time() - self._started_wall
            if (total_rounds >= self.config.min_rounds
                    and (self.config.max_wall_seconds is None
                         or elapsed >= self.config.max_wall_seconds)):
                return EXIT_OK, 'complete'
            if self.config.max_rounds is not None and total_rounds >= self.config.max_rounds:
                return EXIT_OK, 'complete'
            free = _volume_free_bytes(self.root)
            if free is not None and free < self.config.volume_min_free_bytes:
                return EXIT_PAUSED, 'paused_disk'
            used, measured = _tree_bytes(self.root)
            if measured and used >= self.config.max_total_log_bytes:
                return EXIT_PAUSED, 'paused_logs'
            self._run_round(self._next_round_no)
            self._next_round_no += 1
            # Sleep to the next period boundary; stay responsive to stop.
            next_start += self.config.round_period_seconds
            while True:
                if self._done.is_set() or self._stop_source is not None or self._evidence_failure:
                    break
                remaining = next_start - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.25, max(0.0, remaining)))

    def _close_segment(self, reason: str, code: int) -> None:
        try:
            if self._segment_dir is None:
                return
            close_reason = reason if reason in CLOSE_REASONS else 'internal'
            summary = self._progress('close-summary')
            summary.update({
                'close_reason': close_reason,
                'exit_code': code,
                'stopped_wall': time.time(),
                'stop_note': self._stop_note[:200],
                'signal': self._signal_name,
                'segment_elapsed_seconds': round(time.time() - self._started_wall, 3),
                'targets_met': {'min_rounds': sum(self._totals.values()) >= self.config.min_rounds},
                'distinct_inputs': len(self._distinct_inputs),
                'subinput_rows_completed': self._subinput_rows_completed,
            })
            self._write_json(self._segment_dir / 'summary.json', summary)
            self._write_json(self._segment_dir / 'segment-close.json', {
                'reason': close_reason, 'exit_code': code,
                'stopped_wall': summary['stopped_wall'],
                'rounds_total': dict(self._totals),
                'next_round_no': self._next_round_no,
                'gaps_over_limit': list(self._gaps),
                'stop_note': summary['stop_note'], 'signal': self._signal_name,
                'targets_met': summary['targets_met'],
                'subinput_rows_completed': self._subinput_rows_completed,
            })
            self._driver_log('segment_close', reason=close_reason, code=code)
        except EvidenceWriteFailure:
            print('SOAK_EVIDENCE_FAILURE segment-close')
        except Exception:
            print('SOAK_INTERNAL_ERROR segment-close')


def inspect_campaign(root: Path) -> dict:
    """Read-only campaign state report; takes no lock and writes nothing."""
    document = _read_json(root / 'campaign.json')
    if document is None:
        return {'error': 'campaign_missing'}
    max_gap = document.get('max_unobserved_gap_seconds')
    report = {'campaign': str(root), 'identity': document, 'segments': [], 'lock': {}}
    lock = _read_json(root / 'driver.lock')
    if lock is not None:
        report['lock'] = {'pid': lock.get('pid'), 'alive': _pid_alive(lock.get('pid'))}
    totals = {'passed': 0, 'failed': 0, 'interrupted': 0, 'unknown': 0}
    distinct: set[str] = set()
    subinput_rows = 0
    segments_dir = root / 'segments'
    if segments_dir.is_dir():
        for segment in sorted(path for path in segments_dir.iterdir()
                              if path.is_dir() and re.fullmatch(r'segment-\d{6}', path.name)):
            close = _read_json(segment / 'segment-close.json')
            counts = {'passed': 0, 'failed': 0, 'interrupted': 0, 'unknown': 0}
            heartbeats, gaps, previous_wall = 0, [], None
            heartbeat_read_stopped = None
            heartbeat_file = segment / 'heartbeats.jsonl'
            if heartbeat_file.exists():
                # R6: bounded JSONL iteration — each read is at most one
                # line cap, the line count is capped, and an over-long line
                # stops parsing (the remainder would only desync) instead
                # of being read in full.
                lines_seen = 0
                with open(heartbeat_file, 'rb') as handle:
                    while lines_seen < HEARTBEAT_MAX_LINES:
                        raw = handle.readline(HEARTBEAT_LINE_MAX_BYTES + 1)
                        if not raw:
                            break
                        lines_seen += 1
                        if len(raw) > HEARTBEAT_LINE_MAX_BYTES:
                            heartbeat_read_stopped = 'line_over_limit'
                            break
                        try:
                            entry = json.loads(raw)
                        except ValueError:
                            continue
                        heartbeats += 1
                        wall = entry.get('wall')
                        if previous_wall is not None and type(wall) in (int, float) \
                                and type(max_gap) in (int, float) and wall - previous_wall > max_gap:
                            gaps.append({'wall': wall, 'gap_seconds': round(wall - previous_wall, 3)})
                        if type(wall) in (int, float):
                            previous_wall = wall
                    if heartbeat_read_stopped is None and lines_seen >= HEARTBEAT_MAX_LINES \
                            and handle.readline(1):
                        heartbeat_read_stopped = 'line_count_over_limit'
            rounds_dir = segment / 'rounds'
            if rounds_dir.is_dir():
                for round_dir in sorted(p for p in rounds_dir.iterdir()
                                        if p.is_dir() and p.name.startswith('round-')):
                    record = _read_json(round_dir / 'round.json')
                    if record is None:
                        counts['unknown'] += 1  # missing record == unreadable here too
                        continue
                    final = record.get('final')
                    if final in counts:
                        counts[final] += 1
                    if type(record.get('input_sha256')) is str:
                        distinct.add(record['input_sha256'])
                    if final == 'passed' and type(record.get('subinput_receipts')) is list:
                        # Read-only declared progress from registered pointers;
                        # qualification is the independent audit's judgment.
                        for pointer in record['subinput_receipts']:
                            if type(pointer) is dict and type(pointer.get('rows')) is int:
                                subinput_rows += pointer['rows']
            totals = {key: totals[key] + counts[key] for key in totals}
            report['segments'].append({'path': str(segment), 'close': close, 'rounds': counts,
                                       'heartbeats': heartbeats, 'gaps_over_limit': gaps,
                                       'heartbeat_read_stopped': heartbeat_read_stopped})
    used, measured = _tree_bytes(root)
    report['rounds_total'] = totals
    report['distinct_inputs'] = len(distinct)
    report['subinput_rows_completed'] = subinput_rows
    report['campaign_bytes'] = used if measured else None
    report['campaign_bytes_measured'] = measured
    report['volume_free_bytes'] = _volume_free_bytes(root)
    report['first_failure'] = _read_json(root / 'first-failure.json')
    report['continuity'] = {'segments': len(report['segments']),
                            'broken': any(not entry['close'] or entry['gaps_over_limit']
                                          for entry in report['segments'])}
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='soak_driver')
    sub = parser.add_subparsers(dest='command', required=True)
    run_parser = sub.add_parser('run')
    run_parser.add_argument('--campaign', required=True)
    run_parser.add_argument('--config', required=True)
    inspect_parser = sub.add_parser('inspect')
    inspect_parser.add_argument('--campaign', required=True)
    args = parser.parse_args(argv)
    if args.command == 'inspect':
        print(json.dumps(inspect_campaign(Path(args.campaign).resolve()), indent=2, sort_keys=True))
        return EXIT_OK
    try:
        raw = _read_capped(Path(args.config), CONFIG_MAX_BYTES)
        if raw is None:  # over the cap: refuse the configuration outright
            raise ValueError('config_over_read_cap') from None
        document = json.loads(raw.decode('utf-8'))
        config = SoakConfig.from_dict(document)
        driver = SoakDriver(config, Path(args.campaign).resolve())
    except (OSError, ValueError):
        print('SOAK_CONFIG_INVALID config')
        return EXIT_CONFIG
    code = driver.run()
    print(f'SOAK_EXIT code={code}')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
