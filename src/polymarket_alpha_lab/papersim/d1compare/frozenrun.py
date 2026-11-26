"""D1 frozen artifacts and execution environment for the paper comparison.

The parent verifies the designated mirror scripts and archives the original
Git tar bytes and their binding manifest in managed project PostgreSQL.
Frozen children receive the extracted files and their fixed environment.
The chain driver :func:`execute_designated_chain` requires the section 5
run gates and an executor supplied by the approved run wiring.

Frozen-chain designation (plan section 3, C4):

==========================  =====================================  ==============
Leg                         Mirror scripts (server-mirror/...)     Library pin
==========================  =====================================  ==============
Soccer chain                round43/raw/enum43e.py                 1a2760bc
                            round43/raw/build_board43c.py          1a2760bc
                            round43/raw/scan43.py                  1a2760bc
                            round43/raw/write_candidates43a.py     1a2760bc
                            round43/raw/write_candidates43e.py     1a2760bc
Crypto leg                  round33/raw/scan_crypto_33.py          19501719
==========================  =====================================  ==============

The pins are materially different rounds of the same library and are
NEVER mixed per leg; the final server binding ``9e761317``
(VERSION-CHECKIN.md) is recorded end-state inventory, not a
substitution.  Any deviation from these designations is a plan change
requiring re-review.

Per-script override allow-lists (binding review note 5) -- the override
surface of the recorded shim fallback is EXACTLY these module-level
constants; :func:`apply_overrides` rejects every other name
mechanically.  League-scope tuples are FIXED read-only inventory, not
window data, and are deliberately NOT overridable (decision recorded in
this module; enforced by rejection):

===========================  =========================================  =========================================
Script                       Overridable (paths + window globals)      Read-only inventory (never overridable)
===========================  =========================================  =========================================
enum43e.py                   HOME, OUTDIR, LO, HI                       BASE, ESPN (frozen API origins)
build_board43c.py            HOME, RAW, ESPN, GAP, OUT, LO, HI          -- (no further module constants)
scan43.py                    HOME, UNDERSTAT_LATEST,                   BASE, CLOB (frozen API origins),
                             UNDERSTAT_POINTER, FOTMOB_CHAMP,          BIG5, TOTALS_ONLY, BOARD_ONLY
                             ERED, KAPPA_PATH, OUTDIR, OUT,             (league scope: FIXED), LEAGUE_TAGS,
                             EVENTS, ESPN, LO, HI                       HELD_IDS, HELD_PAIRS, STALENESS,
                                                                       STALE_CLUBS
write_candidates43a.py       SCAN, VERIF, OUT                           --
write_candidates43e.py       SCAN, VERIF, ESPN43E, OUT                 --
scan_crypto_33.py            OUT, _LIB_SRC                             HELD, SIG, INVERSE, MONTHS, ECON_SLUGS
                                                                       (no window constant exists in code;
                                                                       the scan window is docstring text, so
                                                                       any window override is rejected)
===========================  =========================================  =========================================

``soccer_scan33.py`` (round33) is NOT designated for staging under the
accepted plan; its would-be allow-list is recorded below with
``staged=False`` so the discipline is pinned, and staging it is
refused until a plan change re-reviews it.

Egress allow-lists are derived by READING the designated scripts (URL
constants and call sites), not invented:

- Soccer chain: ``gamma-api.polymarket.com`` (BASE), ``clob.polymarket.com``
  (CLOB), ``site.api.espn.com`` (enum43e ESPN scoreboard cross-check).
  The candidates writers and the board builder perform no network reads
  (their news-source URLs are recorded output strings, not fetches).
- Crypto leg: ``gamma-api.polymarket.com``, ``clob.polymarket.com``,
  ``api.binance.com`` (spot/klines), ``stooq.com`` (WTI daily CSV).

Sandbox layout presentation: preferred is a rootless mount namespace
that bind-mounts the staged tree onto the scripts' hard-coded paths
(:func:`mount_namespace_plan` produces the exact ``unshare``/``mount``
argv, never executed here); the acceptable fallback is the recorded
shim: :func:`apply_overrides` rewrites ONLY allow-listed constant
assignment lines in a staged copy and records every changed line.

The managed manifest contains content addresses and logical bindings.
The extracted files and actual mounts exist for the duration of a lease.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import io
import json
import os
import posixpath
import re
import select
import shlex
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polymarket_alpha_lab.papersim import snapshots, store_psycopg
from polymarket_alpha_lab.papersim.d1compare import journal as d1_journal
from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError, no_links

__all__ = [
    "CRYPTO_LIBRARY_PIN",
    "DayRunAuthorization",
    "FrozenRunError",
    "LIBRARY_SUBPATH",
    "RecordingUrllib",
    "ScriptDesignation",
    "SOCCER_LIBRARY_PIN",
    "STAGED_CHAINS",
    "apply_overrides",
    "check_run_gates",
    "child_environment",
    "designated_leg",
    "egress_hosts",
    "execute_designated_chain",
    "import_original_archives",
    "manifest_rows",
    "mount_namespace_plan",
    "plan_chain",
    "preflight_designated_chain",
    "read_staged_archives",
    "restore_archives",
    "sandbox_bindings",
    "stage_archives",
    "stage_scripts",
    "validate_library_archive",
    "REQUIRED_RUN_GATES",
]

SOCCER_LIBRARY_PIN = "1a2760bc"
CRYPTO_LIBRARY_PIN = "19501719"

#: The library import surface the frozen scripts insert on ``sys.path``:
#: both legs hard-code ``/home/ubuntu/polymarket-alpha-lab/src``
#: (scan43.py:57, scan_crypto_33.py:19-21).
LIBRARY_SUBPATH = "src"

#: Hard-coded sandbox roots presented by the layout layer.
SERVER_HOME = "/home/ubuntu/paper-sim"
LIBRARY_HOME = "/home/ubuntu/polymarket-alpha-lab"


class FrozenRunError(ValueError):
    """One frozen-run refusal with a fixed code (never a silent skip)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Designation table (C4) with the binding per-script allow-lists
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptDesignation:
    """One designated frozen script: mirror path, pin and allow-lists."""

    leg: str
    mirror_path: str
    library_pin: str
    overridable: frozenset[str]
    read_only_inventory: frozenset[str]
    staged: bool = True
    note: str = ""


#: The designated chains, exactly as the accepted plan section 3 names
#: them.  Nothing outside this table is stageable.
STAGED_CHAINS: tuple[ScriptDesignation, ...] = (
    ScriptDesignation(
        leg="soccer", mirror_path="round43/raw/enum43e.py",
        library_pin=SOCCER_LIBRARY_PIN,
        overridable=frozenset({"HOME", "OUTDIR", "LO", "HI"}),
        read_only_inventory=frozenset({"BASE", "ESPN"}),
        note="enumeration sweep; ESPN scoreboard cross-check",
    ),
    ScriptDesignation(
        leg="soccer", mirror_path="round43/raw/build_board43c.py",
        library_pin=SOCCER_LIBRARY_PIN,
        overridable=frozenset({"HOME", "RAW", "ESPN", "GAP", "OUT", "LO", "HI"}),
        read_only_inventory=frozenset(),
        note="board summary builder; local files only",
    ),
    ScriptDesignation(
        leg="soccer", mirror_path="round43/raw/scan43.py",
        library_pin=SOCCER_LIBRARY_PIN,
        overridable=frozenset({
            "HOME", "UNDERSTAT_LATEST", "UNDERSTAT_POINTER", "FOTMOB_CHAMP",
            "ERED", "KAPPA_PATH", "OUTDIR", "OUT", "EVENTS", "ESPN", "LO", "HI"}),
        read_only_inventory=frozenset({
            "BASE", "CLOB", "BIG5", "TOTALS_ONLY", "BOARD_ONLY", "LEAGUE_TAGS",
            "HELD_IDS", "HELD_PAIRS", "STALENESS", "STALE_CLUBS"}),
        note=("league-scope tuples (BIG5/TOTALS_ONLY/BOARD_ONLY) are FIXED "
              "read-only inventory: static league scope is not window data; "
              "overriding them would silently change the frozen decision "
              "population"),
    ),
    ScriptDesignation(
        leg="soccer", mirror_path="round43/raw/write_candidates43a.py",
        library_pin=SOCCER_LIBRARY_PIN,
        overridable=frozenset({"SCAN", "VERIF", "OUT"}),
        read_only_inventory=frozenset(),
        note="candidates writer (43A); local files only",
    ),
    ScriptDesignation(
        leg="soccer", mirror_path="round43/raw/write_candidates43e.py",
        library_pin=SOCCER_LIBRARY_PIN,
        overridable=frozenset({"SCAN", "VERIF", "ESPN43E", "OUT"}),
        read_only_inventory=frozenset(),
        note=("candidates writer (43E); the news URLs are recorded output "
              "strings, not fetches"),
    ),
    ScriptDesignation(
        leg="crypto", mirror_path="round33/raw/scan_crypto_33.py",
        library_pin=CRYPTO_LIBRARY_PIN,
        overridable=frozenset({"OUT", "_LIB_SRC"}),
        read_only_inventory=frozenset({"HELD", "SIG", "INVERSE", "MONTHS",
                                       "ECON_SLUGS"}),
        note=("OUT path + library source overridable; NO window constant "
              "exists in code (the window is docstring text), so any window "
              "override name is rejected"),
    ),
    # NOT designated for staging under the accepted plan; the would-be
    # allow-list is recorded so the discipline is explicit if a future
    # plan change stages it (staging is refused today).
    ScriptDesignation(
        leg="soccer", mirror_path="round33/raw/soccer_scan33.py",
        library_pin=CRYPTO_LIBRARY_PIN,
        overridable=frozenset({
            "HOME", "UNDERSTAT_LATEST", "UNDERSTAT_POINTER", "FOTMOB",
            "KAPPA_PATH", "R30_SCAN", "OUTDIR", "OUT",
            "LO", "HI", "DISC_LO", "DISC_HI"}),
        read_only_inventory=frozenset({"BASE", "CLOB", "BIG5", "TEAM_ALIASES"}),
        staged=False,
        note="round-33 soccer scan; outside the designated chains",
    ),
)

DESIGNATION_BY_PATH: dict[str, ScriptDesignation] = {
    designation.mirror_path: designation for designation in STAGED_CHAINS
}


def designated_leg(leg: str) -> tuple[ScriptDesignation, ...]:
    """The STAGED designations of one leg (unstaged inventory excluded)."""
    staged = tuple(designation for designation in STAGED_CHAINS
                   if designation.leg == leg and designation.staged)
    if not staged:
        raise FrozenRunError("frozen_leg_unknown", leg)
    return staged


# ---------------------------------------------------------------------------
# Egress allow-lists (derived by reading the designated scripts)
# ---------------------------------------------------------------------------

_EGRESS_HOSTS: dict[str, frozenset[str]] = {
    "soccer": frozenset({
        "gamma-api.polymarket.com",   # scan43.py BASE; enum43e.py BASE
        "clob.polymarket.com",        # scan43.py CLOB (fetch_book)
        "site.api.espn.com",          # enum43e.py ESPN scoreboards
    }),
    "crypto": frozenset({
        "gamma-api.polymarket.com",   # scan_crypto_33.py gamma market/events
        "clob.polymarket.com",        # scan_crypto_33.py CLOB books
        "api.binance.com",            # scan_crypto_33.py spot + klines
        "stooq.com",                  # scan_crypto_33.py WTI daily CSV
    }),
}


def egress_hosts(leg: str) -> frozenset[str]:
    """The public API hosts the designated scripts of ``leg`` actually read.

    Derived from the scripts' URL constants and call sites; an empty leg
    is a configuration error, not an open egress policy.
    """
    hosts = _EGRESS_HOSTS.get(leg)
    if hosts is None:
        raise FrozenRunError("frozen_egress_leg_unknown", leg)
    return hosts


def _enforce_egress(url: str, allowed_hosts: frozenset[str]) -> None:
    host = urllib.parse.urlsplit(url).hostname
    if not host or host.lower() not in allowed_hosts:
        raise FrozenRunError("frozen_run_egress_host_refused",
                             f"{host or '<no host>'} not in allow-list")


# ---------------------------------------------------------------------------
# Manifest parsing and script staging
# ---------------------------------------------------------------------------


def manifest_rows(manifest_path: Path) -> dict[str, tuple[str, int]]:
    """Parse ``server-mirror/MANIFEST.sha256`` per-file rows.

    Returns ``{relative_path: (sha256, size_bytes)}``.  Header comment
    lines (starting ``#``) and blank lines are skipped; malformed rows
    are explicit refusals.
    """
    rows: dict[str, tuple[str, int]] = {}
    text = Path(manifest_path).read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) != 4:
            raise FrozenRunError("frozen_manifest_row_invalid",
                                 f"line {number}: {stripped[:80]}")
        digest, size_text, _mtime, path = parts
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise FrozenRunError("frozen_manifest_digest_invalid",
                                 f"line {number}: {digest}")
        try:
            size = int(size_text)
        except ValueError as error:
            raise FrozenRunError("frozen_manifest_size_invalid",
                                 f"line {number}: {size_text}") from error
        rows[path] = (digest, size)
    return rows


def _verify_against_manifest(content: bytes, path: str,
                             rows: Mapping[str, tuple[str, int]]) -> None:
    expected = rows.get(path)
    if expected is None:
        raise FrozenRunError("frozen_manifest_row_missing", path)
    digest, size = expected
    actual = hashlib.sha256(content).hexdigest()
    if actual != digest or len(content) != size:
        raise FrozenRunError("frozen_stage_manifest_mismatch",
                             f"{path}: sha256 {actual} size {len(content)} "
                             f"!= manifest ({digest}, {size})")


@dataclass(frozen=True)
class StagedScript:
    """One staged frozen script: verified bytes plus its designation."""

    designation: ScriptDesignation
    staged_path: str          # relative to the staging root
    sha256: str
    size_bytes: int
    library_pin: str


def stage_scripts(mirror_root: Path, staging_root: _PrivateWork, *,
                  designations: Sequence[ScriptDesignation] | None = None,
                  manifest_name: str = "MANIFEST.sha256") -> tuple[StagedScript, ...]:
    """Copy the verified mirror scripts into a live private work lease.

    Every file is read from the mirror, verified per-file against
    ``MANIFEST.sha256`` (sha256 AND byte size) BEFORE use and refused on
    mismatch; the mirror itself is never modified.  Only designated,
    staged scripts are copyable -- unstaged inventory and unknown paths
    are explicit refusals.
    """
    if not isinstance(staging_root, _PrivateWork):
        raise FrozenRunError("frozen_work_lease_required")
    with staging_root.lease():
        selected = tuple(designations) if designations is not None else \
            tuple(d for d in STAGED_CHAINS if d.staged)
        mirror_root = Path(mirror_root)
        scripts = _read_scripts(mirror_root, selected, manifest_rows(mirror_root / manifest_name))
        return _copy_scripts(staging_root.path, scripts)


def _read_scripts(mirror_root: Path, designations: Sequence[ScriptDesignation],
                  rows: Mapping[str, tuple[str, int]],
                  ) -> tuple[tuple[StagedScript, bytes], ...]:
    scripts = []
    for designation in designations:
        found = DESIGNATION_BY_PATH.get(designation.mirror_path)
        if found is None or found != designation:
            raise FrozenRunError("frozen_designation_unknown",
                                 designation.mirror_path)
        if not designation.staged:
            raise FrozenRunError("frozen_script_not_designated",
                                 f"{designation.mirror_path}: {designation.note}")
        source = mirror_root / designation.mirror_path
        content = source.read_bytes()
        _verify_against_manifest(content, designation.mirror_path, rows)
        script = StagedScript(
            designation=designation,
            staged_path=f"sandbox/paper-sim/{designation.mirror_path}",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            library_pin=designation.library_pin,
        )
        scripts.append((script, content))
    return tuple(scripts)


def _copy_scripts(staging_root: Path, scripts: Sequence[tuple[StagedScript, bytes]]
                  ) -> tuple[StagedScript, ...]:
    for script, content in scripts:
        target = staging_root / script.staged_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return tuple(script for script, _ in scripts)


# ---------------------------------------------------------------------------
# Private work directories and their process leases
# ---------------------------------------------------------------------------


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_WORK_NAME = re.compile(r"call-[a-z0-9_]{8}")


def _private_directory(fd: int) -> bool:
    metadata = os.fstat(fd)
    return metadata.st_uid == os.geteuid() and stat.S_IMODE(metadata.st_mode) == 0o700


def _reap_work_directory(parent_fd: int, name: str) -> None:
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except (FileNotFoundError, NotADirectoryError):
        return
    try:
        if not _private_directory(fd):
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        shutil.rmtree(name, dir_fd=parent_fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class _PrivateWork:
    path: Path
    _owner_fd: int

    @contextmanager
    def lease(self) -> Iterator[int]:
        if _ACTIVE_WORK.get(self._owner_fd) is not self:
            raise FrozenRunError("frozen_work_lease_expired")
        # A separate open description keeps its lease even if the owner
        # is killed. An ordinary close in either process cannot unlock it.
        fd = os.open(self.path, _DIR_FLAGS)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            if (_ACTIVE_WORK.get(self._owner_fd) is not self
                    or not os.path.samestat(os.fstat(fd), os.fstat(self._owner_fd))):
                raise FrozenRunError("frozen_work_lease_expired")
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def process(self, argv: Sequence[str], **kwargs: Any) -> Iterator[subprocess.Popen]:
        with self.lease() as fd:
            with subprocess.Popen(
                argv, cwd=self.path, pass_fds=(fd,), start_new_session=True,
                **kwargs,
            ) as process:
                try:
                    yield process
                finally:
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()


_ACTIVE_WORK: dict[int, _PrivateWork] = {}


@contextmanager
def _private_work() -> Iterator[_PrivateWork]:
    root = Path(tempfile.gettempdir()) / f"polymarket-d1-frozen-{os.geteuid()}"
    root.mkdir(mode=0o700, exist_ok=True)
    parent_fd = os.open(root, _DIR_FLAGS)
    leaf = None
    owner_fd = None
    try:
        if not _private_directory(parent_fd):
            raise FrozenRunError("frozen_work_directory_not_private")
        # Creating a leaf and taking its lease are atomic with respect to
        # reapers. The parent lock lives on a directory inode.
        fcntl.flock(parent_fd, fcntl.LOCK_EX)
        for name in os.listdir(parent_fd):
            if _WORK_NAME.fullmatch(name):
                _reap_work_directory(parent_fd, name)
        leaf = Path(tempfile.mkdtemp(prefix="call-", dir=root))
        owner_fd = os.open(leaf.name, _DIR_FLAGS, dir_fd=parent_fd)
        fcntl.flock(owner_fd, fcntl.LOCK_SH)
        work = _PrivateWork(leaf, owner_fd)
        _ACTIVE_WORK[owner_fd] = work
        fcntl.flock(parent_fd, fcntl.LOCK_UN)
        yield work
    finally:
        try:
            if leaf is not None:
                if owner_fd is not None:
                    _ACTIVE_WORK.pop(owner_fd, None)
                fcntl.flock(parent_fd, fcntl.LOCK_EX)
                if owner_fd is not None:
                    os.close(owner_fd)
                _reap_work_directory(parent_fd, leaf.name)
        finally:
            os.close(parent_fd)


# ---------------------------------------------------------------------------
# Pinned library export and verification against local Git objects
# ---------------------------------------------------------------------------


_ARCHIVE_EXPORT_VERSION = 2
_ARCHIVE_TIMEOUT = 120
_TAR_UMASK = 0o002
_IO_CHUNK = 64 * 1024
_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
    "TZ": "UTC",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_ALLOW_PROTOCOL": "",
    "GIT_TERMINAL_PROMPT": "0",
}


@dataclass(frozen=True)
class _LibrarySource:
    leg: str
    pin: str
    commit: str
    tree: str
    epoch: int
    archive_byte_length: int
    archive_sha256: str

    @property
    def tree_ref(self) -> str:
        return f"{self.commit}:{LIBRARY_SUBPATH}"

    def archive_identity(self) -> dict[str, str]:
        return {
            "pin": self.pin,
            "commit": self.commit,
            "tree": self.tree,
            "subpath": LIBRARY_SUBPATH,
            "format": "tar",
        }

    def snapshot_source(self, export_version: int) -> str:
        return (f"d1:frozen-library:leg={self.leg}:commit={self.commit}:tree={self.tree}:"
                f"subpath={LIBRARY_SUBPATH}:export=v{export_version}")

    def manifest_binding(self) -> dict[str, Any]:
        return {
            **self.archive_identity(),
            "export_version": _ARCHIVE_EXPORT_VERSION,
            "commit_epoch": self.epoch,
            "tar_umask": f"{_TAR_UMASK:04o}",
            "archive_sha256": self.archive_sha256,
            "archive_byte_length": self.archive_byte_length,
        }


_LIBRARY_SOURCES = {
    "soccer": _LibrarySource(
        "soccer", SOCCER_LIBRARY_PIN,
        "1a2760bc411f1157647be6ec87a57cd368b2eb9a",
        "7ee14a247dced867cd2d902de3631065f714f65c",
        1794058265, 95262720,
        "f4e8dde6e5ec42c5dbb6bbffe632b680e921f5c2651393cdba2e7ddd79e03931",
    ),
    "crypto": _LibrarySource(
        "crypto", CRYPTO_LIBRARY_PIN,
        "195017196f90287128f27af9ae832ea42864e5dc",
        "d6ae3a86263373f80a03f68fd7e37c2bbf6bf34b",
        1793783030, 95211520,
        "cf14de7c221f10558dafc61aa8e6274dd9d6331294a354fe8c77b97b11f95d67",
    ),
}


def _library_source(leg: str, designations: Sequence[ScriptDesignation]
                    ) -> _LibrarySource:
    pins = {designation.library_pin for designation in designations}
    if len(pins) != 1:
        raise FrozenRunError("frozen_pin_mixed_within_leg", leg)
    source = _LIBRARY_SOURCES[leg]
    if pins != {source.pin}:
        raise FrozenRunError("frozen_library_pin_invalid", leg)
    return source


@dataclass(frozen=True)
class LibraryTree:
    """One extracted pinned tree within the active work directory."""

    pin: str
    archive_sha256: str
    archive_size_bytes: int
    tree_path: str
    subpath: str = LIBRARY_SUBPATH


class _GitOutput:
    """A deadline also applies while Git is blocked producing binary output."""

    def __init__(self, process: subprocess.Popen):
        self.fd = process.stdout.fileno()
        self.poll = select.poll()
        self.poll.register(self.fd, select.POLLIN)
        self.deadline = time.monotonic() + _ARCHIVE_TIMEOUT
        self.buffer = b""

    def remaining(self) -> float:
        return max(0, self.deadline - time.monotonic())

    def chunk(self, size: int) -> bytes:
        remaining = self.remaining()
        if not remaining or not self.poll.poll(max(1, round(remaining * 1000))):
            raise subprocess.TimeoutExpired("git", _ARCHIVE_TIMEOUT)
        return os.read(self.fd, size)

    def line(self) -> bytes:
        while b"\n" not in self.buffer and len(self.buffer) < 128:
            data = self.chunk(128 - len(self.buffer))
            if not data:
                break
            self.buffer += data
        line, newline, self.buffer = self.buffer.partition(b"\n")
        return line + newline

    def read(self, size: int) -> bytes:
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        if len(result) == size:
            return result
        data = bytearray(result)
        while len(data) < size:
            chunk = self.chunk(size - len(data))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)


@dataclass(frozen=True)
class _GitObjects:
    work: _PrivateWork
    leg: str
    argv: tuple[str, ...]
    env: Mapping[str, str]

    @contextmanager
    def process(self, command: Sequence[str], *, batch: bool = False
                ) -> Iterator[tuple[subprocess.Popen, _GitOutput]]:
        try:
            with self.work.process(
                [*self.argv, *command], env=self.env, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0,
                stdin=subprocess.PIPE if batch else subprocess.DEVNULL,
            ) as process:
                output = _GitOutput(process)
                yield process, output
                status = process.wait(timeout=output.remaining())
                if status:
                    raise FrozenRunError("frozen_library_archive_failed",
                                         f"{self.leg}: {command[0]} exited {status}")
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FrozenRunError("frozen_library_archive_failed",
                                 f"{self.leg}: {command[0]}: {type(error).__name__}") from None

    def read(self, command: Sequence[str], *, limit: int = 4096) -> bytes:
        with self.process(command) as (_, output):
            content = io.BytesIO()
            while data := output.chunk(min(_IO_CHUNK, limit + 1 - content.tell())):
                content.write(data)
                if content.tell() > limit:
                    code = ("frozen_library_archive_size_mismatch"
                            if command[0] == "archive" else "frozen_library_archive_failed")
                    raise FrozenRunError(code, f"{self.leg}: {command[0]} output exceeds limit")
        return content.getvalue()


def _git_alternate(path: bytes) -> str:
    # Git treats a leading quote as a C-quoted entry in its colon-separated list.
    return '"' + "".join(
        chr(byte) if 32 <= byte <= 126 and byte not in (34, 92)
        else f"\\{byte:03o}"
        for byte in path
    ) + '"'


def _isolated_git(repo_root: Path, source: _LibrarySource,
                  work: _PrivateWork, git_bin: str) -> _GitObjects:
    git = _GitObjects(work, source.leg, (git_bin,), _GIT_ENV)
    location = git.read(("-C", str(Path(repo_root).absolute()), "rev-parse",
                         "--path-format=absolute", "--git-path", "objects"))
    if not location.startswith(b"/") or not location.endswith(b"\n"):
        raise FrozenRunError("frozen_library_object_directory_invalid", source.leg)
    bare = work.path / f"git-{source.leg}"
    git.read(("init", "--quiet", "--bare", "--template=", "--object-format=sha1", str(bare)))
    return _GitObjects(
        work, source.leg,
        (git_bin, f"--git-dir={bare}", "-c", "core.attributesFile=/dev/null",
         "-c", "core.autocrlf=false", "-c", f"tar.umask={_TAR_UMASK:04o}"),
        {**_GIT_ENV, "GIT_ALTERNATE_OBJECT_DIRECTORIES": _git_alternate(location[:-1])},
    )


def _git_tree(source: _LibrarySource, git: _GitObjects) -> dict[bytes, tuple[bytes, bytes]]:
    commit = source.commit
    if (git.read(("cat-file", "-t", commit)) != b"commit\n"
            or git.read(("cat-file", "-t", source.tree)) != b"tree\n"
            or git.read(("rev-parse", "--verify", source.tree_ref)) != source.tree.encode() + b"\n"
            or git.read(("show", "-s", "--no-notes", "--format=%ct", commit))
            != f"{source.epoch}\n".encode()):
        raise FrozenRunError("frozen_library_source_mismatch", source.leg)
    listing = git.read(("ls-tree", "-r", "-t", "-z", "--full-tree", source.tree_ref),
                       limit=4 * 1024 * 1024)
    if not listing or not listing.endswith(b"\0"):
        raise FrozenRunError("frozen_library_tree_invalid", source.leg)
    tree = {}
    for entry in listing[:-1].split(b"\0"):
        match = re.fullmatch(rb"(040000 tree|100644 blob) ([0-9a-f]{40})\t(.+)",
                             entry, flags=re.DOTALL)
        if match is None:
            raise FrozenRunError("frozen_library_tree_invalid", source.leg)
        mode_type, oid, path = match.groups()
        components = path.split(b"/")
        if (path in tree or b".gitattributes" in components
                or any(part in (b"", b".", b"..") for part in components)):
            raise FrozenRunError("frozen_library_tree_invalid", source.leg)
        tree[path] = (mode_type, oid)
    return tree


def _archive_member_path(member: tarfile.TarInfo, content: bytes, leg: str) -> bytes:
    # TarInfo normalizes directory names and old V7 type flags. Check the
    # original type and the effective POSIX path before that normalization.
    header = content[member.offset_data - tarfile.BLOCKSIZE:member.offset_data]
    if header[156:157] != member.type or member.sparse is not None:
        raise FrozenRunError("frozen_library_archive_type_mismatch", f"{leg}: {member.name}")
    if "path" in member.pax_headers:
        path = member.pax_headers["path"].encode("utf-8", "surrogateescape")
    else:
        name = header[:100].split(b"\0", 1)[0]
        prefix = header[345:500].split(b"\0", 1)[0]
        path = prefix + b"/" + name if prefix else name
    if member.type == tarfile.DIRTYPE:
        path = path.removesuffix(b"/")
    if (path != member.name.encode("utf-8", "surrogateescape") or b"\0" in path
            or any(part in (b"", b".", b"..") for part in path.split(b"/"))):
        raise FrozenRunError("frozen_library_archive_unsafe", leg)
    return path


def _compare_git_blob(archive: tarfile.TarFile, member: tarfile.TarInfo, oid: bytes,
                      process: subprocess.Popen, output: _GitOutput, leg: str) -> None:
    process.stdin.write(oid + b"\n")
    if output.line() != oid + b" blob " + str(member.size).encode() + b"\n":
        raise FrozenRunError("frozen_library_archive_content_mismatch", f"{leg}: {member.name}")
    with archive.extractfile(member) as file:
        remaining = member.size
        while remaining:
            size = min(remaining, _IO_CHUNK)
            data = output.read(size)
            if len(data) != size or file.read(size) != data:
                raise FrozenRunError("frozen_library_archive_content_mismatch",
                                     f"{leg}: {member.name}")
            remaining -= size
    if output.read(1) != b"\n":
        raise FrozenRunError("frozen_library_archive_content_mismatch", f"{leg}: {member.name}")


@contextmanager
def _open_archive(content: bytes, leg: str) -> Iterator[tarfile.TarFile]:
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:",
                          encoding="utf-8", errors="surrogateescape") as archive:
            yield archive
    except tarfile.TarError as error:
        raise FrozenRunError("frozen_library_archive_invalid", leg) from error


def _infer_tar_umask(archive: tarfile.TarFile, leg: str) -> int:
    directory = next((member for member in archive.getmembers() if member.isdir()), None)
    if directory is None:
        raise FrozenRunError("frozen_library_archive_members_missing", leg)
    if directory.mode & ~0o777:
        raise FrozenRunError("frozen_library_archive_mode_mismatch", f"{leg}: {directory.name}")
    return 0o777 ^ directory.mode


def _check_tar_mode(member: tarfile.TarInfo, tar_umask: int, leg: str) -> None:
    expected = (0o777 if member.isdir() else 0o666) & ~tar_umask
    if member.mode != expected:
        raise FrozenRunError("frozen_library_archive_mode_mismatch", f"{leg}: {member.name}")


def _validate_tar(content: bytes, tree: dict[bytes, tuple[bytes, bytes]],
                  git: _GitObjects, *, tar_umask: int | None = _TAR_UMASK) -> int:
    remaining = dict(tree)
    with (_open_archive(content, git.leg) as archive,
          git.process(("cat-file", "--batch"), batch=True) as (process, output)):
        if tar_umask is None:
            tar_umask = _infer_tar_umask(archive, git.leg)
        for member in archive:
            path = _archive_member_path(member, content, git.leg)
            expected = remaining.pop(path, None)
            if expected is None:
                code = "duplicate" if path in tree else "unexpected"
                raise FrozenRunError(f"frozen_library_archive_member_{code}",
                                     f"{git.leg}: {member.name}")
            mode_type, oid = expected
            is_dir = mode_type == b"040000 tree"
            if (member.type != (tarfile.DIRTYPE if is_dir else tarfile.REGTYPE)
                    or (is_dir and member.size != 0) or member.size < 0):
                raise FrozenRunError("frozen_library_archive_type_mismatch",
                                     f"{git.leg}: {member.name}")
            _check_tar_mode(member, tar_umask, git.leg)
            if not is_dir:
                _compare_git_blob(archive, member, oid, process, output, git.leg)
        if remaining:
            raise FrozenRunError("frozen_library_archive_members_missing", git.leg)
        tail = memoryview(content)[archive.offset:]
        if len(tail) < 2 * tarfile.BLOCKSIZE or len(content) % tarfile.BLOCKSIZE or any(tail):
            raise FrozenRunError("frozen_library_archive_trailer_invalid", git.leg)
        process.stdin.close()
        if output.read(1):
            raise FrozenRunError("frozen_library_archive_failed", f"{git.leg}: cat-file trailing output")
    return tar_umask


def _generate_library_archive(source: _LibrarySource, git: _GitObjects) -> bytes:
    tree = _git_tree(source, git)
    content = git.read(("archive", "--format=tar", f"--mtime=@{source.epoch}", source.tree_ref),
                       limit=source.archive_byte_length)
    _validate_tar(content, tree, git)
    if len(content) != source.archive_byte_length:
        raise FrozenRunError("frozen_library_archive_size_mismatch", source.leg)
    if hashlib.sha256(content).hexdigest() != source.archive_sha256:
        raise FrozenRunError("frozen_library_archive_digest_mismatch", source.leg)
    return content


def validate_library_archive(leg: str, archive_bytes: bytes, *, repo_root: Path,
                             staging_root: Path, git_bin: str = "git") -> None:
    """Compare a candidate tar to the pinned Git tree independently of its hash."""
    source = _library_source(leg, designated_leg(leg))
    with _private_work() as work:
        git = _isolated_git(repo_root, source, work, git_bin)
        _validate_tar(archive_bytes, _git_tree(source, git), git)


def _library_tree(binding: Mapping[str, Any]) -> LibraryTree:
    pin = binding["pin"]
    return LibraryTree(
        pin, binding["archive_sha256"], binding["archive_byte_length"],
        f"libraries/{pin}/{LIBRARY_SUBPATH}",
    )


def _extract_library(work: _PrivateWork, library: LibraryTree, content: bytes) -> None:
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            archive.extractall(work.path / library.tree_path, filter="data")
    except (OSError, tarfile.TarError) as error:
        raise FrozenRunError("frozen_library_extraction_failed",
                             f"{library.pin}: {type(error).__name__}") from None


# ---------------------------------------------------------------------------
# Sandbox layout presentation (mount namespace preferred; shim fallback)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutBinding:
    """One hard-coded path bound to staged material."""

    hard_coded_path: str
    staged_path: str
    kind: str  # "file" | "dir"


def sandbox_bindings(staged: Sequence[StagedScript],
                     library: LibraryTree, *,
                     staging_root: str) -> tuple[LayoutBinding, ...]:
    """The hard-coded-layout binding table for one staged leg setup.

    Staged scripts land at their mirror-relative layout under the
    sandbox ``/home/ubuntu/paper-sim`` root (the layout the constants
    encode: staging already copies them to
    ``<staging_root>/sandbox/paper-sim/<mirror_path>``), and the pinned
    library tree is presented at
    ``/home/ubuntu/polymarket-alpha-lab/src`` (the path both legs
    insert on ``sys.path``).
    """
    bindings = [
        LayoutBinding(
            hard_coded_path=f"{LIBRARY_HOME}/{library.subpath}",
            staged_path=posixpath.join(staging_root, library.tree_path),
            kind="dir",
        ),
        LayoutBinding(
            hard_coded_path=SERVER_HOME,
            staged_path=posixpath.join(staging_root, "sandbox/paper-sim"),
            kind="dir",
        ),
    ]
    for script in staged:
        bindings.append(LayoutBinding(
            hard_coded_path=f"{SERVER_HOME}/{script.designation.mirror_path}",
            staged_path=posixpath.join(staging_root, script.staged_path),
            kind="file",
        ))
    return tuple(bindings)


def mount_namespace_plan(bindings: Sequence[LayoutBinding]) -> list[str]:
    """The exact rootless mount-namespace presentation argv (preferred).

    Produces the ``unshare --user --map-root-user --mount`` invocation
    whose inner shell bind-mounts every staged path onto its hard-coded
    counterpart before handing control to the child.  The argv is a
    RECORDED PLAN: this pass never runs it.  Precondition recorded for
    the execution host: the hard-coded roots exist there (they are the
    server layout the frozen scripts were written against); per-file
    bind targets sit under a dir-bound root, which is why the directory
    bindings come first.
    """
    lines: list[str] = []
    for binding in bindings:
        lines.append(f"mount --bind {shlex.quote(binding.staged_path)} "
                     f"{shlex.quote(binding.hard_coded_path)}")
    lines.append('exec "$@"')
    return ["unshare", "--user", "--map-root-user", "--mount", "--propagation",
            "private", "sh", "-c", " && ".join(lines), "d1-frozen"]


# ---------------------------------------------------------------------------
# Managed publication and readback
# ---------------------------------------------------------------------------


_STAGE_VERSION = 2
_LEGACY_STAGE_VERSION = 1
_IMPORT_RECEIPT_VERSION = 3
_STAGE_MANIFEST_KIND = "d1_frozen_stage_manifest"
_STAGE_MANIFEST_SOURCE = "d1:frozen-stage-manifest:v2"
_IMPORT_RECEIPT_SOURCE = "d1:frozen-stage-import:v3"
_ORIGINAL_MANIFEST_KIND = "d1_frozen_stage_original_manifest"
_LIBRARY_ARCHIVE_KIND = "d1_frozen_library_archive"
_LEGACY_EVIDENCE_MISSING = "frozen_archive_legacy_evidence_missing"
_MANIFEST_MAX_BYTES = 64 * 1024


def _canonical_json(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document = dict(pairs)
    if len(document) != len(pairs):
        raise ValueError("duplicate manifest field")
    return document


def _invalid_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _manifest_document(content: bytes, label: str) -> dict[str, Any]:
    try:
        if len(content) > _MANIFEST_MAX_BYTES:
            raise ValueError
        document = json.loads(content, object_pairs_hook=_unique_json_object,
                              parse_constant=_invalid_json_constant)
        if not isinstance(document, dict) or type(document.get("stage_version")) is not int:
            raise ValueError
        return document
    except (ValueError, RecursionError):
        raise FrozenRunError("frozen_archive_manifest_schema_invalid", label) from None


def _manifest_legs(document: Mapping[str, Any]) -> dict[str, Any]:
    legs = document.get("legs")
    if not isinstance(legs, dict) or not legs or not legs.keys() <= _LIBRARY_SOURCES.keys():
        raise FrozenRunError("frozen_archive_manifest_binding_invalid", "legs")
    return legs


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _leg_manifest(library: dict[str, Any], scripts: Sequence[StagedScript]) -> dict[str, Any]:
    bindings = sandbox_bindings(scripts, _library_tree(library), staging_root="")
    return {
        "library": library,
        "scripts": [
            {"mirror_path": script.designation.mirror_path,
             "sha256": script.sha256, "size_bytes": script.size_bytes}
            for script in scripts
        ],
        "bindings": [
            {"hard_coded_path": binding.hard_coded_path,
             "source_path": binding.staged_path, "kind": binding.kind}
            for binding in bindings
        ],
    }


def _manifest_scripts(leg: str, rows: object, designations: Sequence[ScriptDesignation], *,
                      legacy: bool = False) -> list[StagedScript]:
    code = "frozen_archive_manifest_binding_invalid"
    if not isinstance(rows, list) or len(rows) != len(designations):
        raise FrozenRunError(code, leg)
    scripts = []
    for row, designation in zip(rows, designations, strict=True):
        if not isinstance(row, dict):
            raise FrozenRunError(code, leg)
        digest, size = row.get("sha256"), row.get("size_bytes")
        if not _valid_sha256(digest) or type(size) is not int or size <= 0:
            raise FrozenRunError(code, designation.mirror_path)
        staged_path = f"sandbox/paper-sim/{designation.mirror_path}"
        if (row.get("mirror_path") != designation.mirror_path
                or (legacy and row.get("library_pin") != designation.library_pin)):
            raise FrozenRunError(code, designation.mirror_path)
        scripts.append(StagedScript(designation, staged_path, digest, size, designation.library_pin))
    return scripts


def _read_leg_manifest(leg: str, entry: object) -> None:
    code = "frozen_archive_manifest_binding_invalid"
    if not isinstance(entry, dict):
        raise FrozenRunError(code, leg)
    designations = designated_leg(leg)
    library = _library_source(leg, designations).manifest_binding()
    scripts = _manifest_scripts(leg, entry.get("scripts"), designations)
    if _canonical_json(entry) != _canonical_json(_leg_manifest(library, scripts)):
        raise FrozenRunError(code, leg)


def _original_leg(leg: str, entry: object) -> dict[str, Any]:
    """Resolve the original stage-v1 evidence to logical bindings."""
    code = "frozen_archive_manifest_binding_invalid"
    if not isinstance(entry, dict) or not isinstance(entry.get("library"), dict):
        raise FrozenRunError(_LEGACY_EVIDENCE_MISSING, leg)
    recorded = entry["library"]
    digest = recorded.get("archive_sha256")
    size = recorded.get("archive_size_bytes")
    if digest is None or size is None or "pin" not in recorded:
        raise FrozenRunError(_LEGACY_EVIDENCE_MISSING, leg)
    designations = designated_leg(leg)
    source = _library_source(leg, designations)
    identity = {**source.archive_identity(), "export_version": _LEGACY_STAGE_VERSION}
    if (entry.get("leg") != leg or recorded["pin"] != source.pin
            or not _valid_sha256(digest) or type(size) is not int or size <= 0):
        raise FrozenRunError(code, leg)
    for field, value in {**identity, "leg": leg, "archive_byte_length": size}.items():
        if field in recorded and (type(recorded[field]) is not type(value) or recorded[field] != value):
            raise FrozenRunError(code, leg)

    library = {**identity, "archive_sha256": digest, "archive_byte_length": size}
    if "tar_umask" in recorded:
        mask = recorded["tar_umask"]
        if not isinstance(mask, str) or re.fullmatch(r"0[0-7]{3}", mask) is None:
            raise FrozenRunError(code, leg)
        library["tar_umask"] = mask

    scripts = _manifest_scripts(leg, entry.get("staged"), designations, legacy=True)
    return _leg_manifest(library, scripts)


def _original_legs(content: bytes) -> dict[str, Any]:
    document = _manifest_document(content, "original manifest")
    if document["stage_version"] != _LEGACY_STAGE_VERSION or set(document) != {"stage_version", "legs"}:
        raise FrozenRunError("frozen_archive_original_manifest_schema_invalid")
    legs = _manifest_legs(document)
    return {leg: _original_leg(leg, legs[leg]) for leg in _LIBRARY_SOURCES if leg in legs}


@contextmanager
def _managed_archive(repo_root: Path, operation: str, *,
                     _instance_info: dict | None = None) -> Iterator[Any]:
    try:
        options = {} if _instance_info is None else {"info": _instance_info}
        with closing(store_psycopg.connect_managed(Path(repo_root), **options)) as connection:
            yield connection
    except FrozenRunError:
        raise
    except Exception as error:
        # Driver diagnostics can include connection configuration.
        raise FrozenRunError(f"frozen_managed_archive_{operation}_failed",
                             type(error).__name__) from None


def _snapshot_record(connection: Any, digest: str, label: str, *,
                     missing_code: str = "frozen_archive_snapshot_missing") -> dict:
    record = snapshots.read_snapshot(connection, digest)
    if record is None:
        raise FrozenRunError(missing_code, f"{label}: {digest}")
    content = record["content"]
    if len(content) != record["byte_length"]:
        raise FrozenRunError("frozen_archive_snapshot_length_mismatch", label)
    if record["content_sha256"] != digest or hashlib.sha256(content).hexdigest() != digest:
        raise FrozenRunError("frozen_archive_snapshot_digest_mismatch", label)
    return record


def _registered_manifest(connection: Any, digest: str) -> dict[str, Any]:
    record = _snapshot_record(connection, digest, "manifest")
    content = record["content"]
    document = _manifest_document(content, digest)
    version = document["stage_version"]
    if version == _LEGACY_STAGE_VERSION:
        raise FrozenRunError(_LEGACY_EVIDENCE_MISSING, "stage v1 requires an import-original receipt")
    fields = {"stage_version", "legs"}
    source = _STAGE_MANIFEST_SOURCE
    if version == _IMPORT_RECEIPT_VERSION:
        fields.add("original_manifest")
        source = _IMPORT_RECEIPT_SOURCE
    provenance = record["kind"], record["source"], record["schema_version"]
    try:
        canonical = _canonical_json(document)
    except (ValueError, RecursionError):
        raise FrozenRunError("frozen_archive_manifest_schema_invalid", digest) from None
    if (version not in (_STAGE_VERSION, _IMPORT_RECEIPT_VERSION) or set(document) != fields
            or provenance != (_STAGE_MANIFEST_KIND, source, version)
            or canonical != content):
        raise FrozenRunError("frozen_archive_manifest_schema_invalid", digest)
    if version == _STAGE_VERSION:
        for leg, entry in _manifest_legs(document).items():
            _read_leg_manifest(leg, entry)
    return document


def _registered_archive(connection: Any, leg: str, library: Mapping[str, Any]) -> bytes:
    record = _snapshot_record(connection, library["archive_sha256"], leg)
    if record["byte_length"] != library["archive_byte_length"]:
        raise FrozenRunError("frozen_archive_snapshot_length_mismatch", leg)
    # A verified receipt independently binds the same content to the D1 leg.
    return record["content"]


def _register_content(connection: Any, content: bytes, kind: str, source: str,
                      digest: str, *, version: int = _STAGE_VERSION,
                      receipt_conflict: str | None = None) -> None:
    saved = snapshots.register_snapshot(
        connection, snapshots.SnapshotRegistration(
            content=content, kind=kind, source=source,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
            schema_version=version,
        ),
    )
    if saved["content_sha256"] != digest or saved["byte_length"] != len(content):
        raise FrozenRunError("frozen_archive_registration_mismatch", kind)
    if receipt_conflict and (saved["kind"], saved["source"], saved["schema_version"]) != (kind, source, version):
        raise FrozenRunError(receipt_conflict, kind)


def stage_archives(legs: Sequence[str], *, mirror_root: Path, staging_root: Path,
                   repo_root: Path, git_bin: str = "git") -> dict[str, Any]:
    """Verify and publish all selected legs and their manifest atomically.

    The operator's staging-root argument remains accepted. Each call uses a
    private leaf under the system temporary directory for Git's scratch space.
    The returned manifest digest is the operator's durable receipt.
    """
    selected = tuple(legs)
    if (not selected or len(set(selected)) != len(selected)
            or not set(selected) <= _LIBRARY_SOURCES.keys()):
        raise FrozenRunError("frozen_archive_legs_invalid")
    leg_entries = {}
    originals = {}
    with _private_work() as work:
        mirror = Path(mirror_root)
        rows = manifest_rows(mirror / "MANIFEST.sha256")
        for leg in _LIBRARY_SOURCES:
            if leg not in selected:
                continue
            designations = designated_leg(leg)
            source = _library_source(leg, designations)
            scripts = tuple(script for script, _ in _read_scripts(mirror, designations, rows))
            git = _isolated_git(repo_root, source, work, git_bin)
            originals[leg] = _generate_library_archive(source, git)
            leg_entries[leg] = _leg_manifest(source.manifest_binding(), scripts)

    manifest = {"stage_version": _STAGE_VERSION, "legs": leg_entries}
    manifest_bytes = _canonical_json(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    with _managed_archive(repo_root, "write") as connection, connection.transaction():
        for leg, content in originals.items():
            source = _LIBRARY_SOURCES[leg]
            origin = source.snapshot_source(_ARCHIVE_EXPORT_VERSION)
            _register_content(connection, content, _LIBRARY_ARCHIVE_KIND,
                              origin, source.archive_sha256)
        _register_content(connection, manifest_bytes, _STAGE_MANIFEST_KIND,
                          _STAGE_MANIFEST_SOURCE, manifest_digest,
                          receipt_conflict="frozen_archive_registration_mismatch")

    with _managed_archive(repo_root, "read") as connection:
        recorded = _registered_manifest(connection, manifest_digest)
        for leg, entry in recorded["legs"].items():
            if _registered_archive(connection, leg, entry["library"]) != originals[leg]:
                raise FrozenRunError("frozen_archive_readback_mismatch", leg)
    return {**manifest, "manifest_sha256": manifest_digest}


def _original_file(path: Path, label: str, *, byte_length: int | None = None) -> bytes:
    path = path.absolute()
    try:
        no_links(path)
        parent_fd = os.open(path.anchor, _DIR_FLAGS)
        try:
            for directory in path.parts[1:-1]:
                next_fd = os.open(directory, _DIR_FLAGS, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        with os.fdopen(fd, "rb") as original:
            status = os.fstat(original.fileno())
            if not stat.S_ISREG(status.st_mode):
                raise FrozenRunError("frozen_archive_original_file_invalid", label)
            limit = _MANIFEST_MAX_BYTES if byte_length is None else byte_length
            if status.st_size > limit or (byte_length is not None and status.st_size != byte_length):
                raise FrozenRunError("frozen_archive_original_length_mismatch", label)
            content = original.read(limit + 1)
            if len(content) > limit or (byte_length is not None and len(content) != byte_length):
                raise FrozenRunError("frozen_archive_original_length_mismatch", label)
            return content
    except (OSError, ProjectDatabaseError, OverflowError) as error:
        raise FrozenRunError("frozen_archive_original_file_invalid", label) from error


def _original_inputs(legs: Mapping[str, Any], archive_files: Sequence[str],
                     archive_sha256s: Sequence[str]) -> tuple[dict[str, Path], set[str]]:
    files = {}
    references = set()
    for arguments, stored in ((archive_files, False), (archive_sha256s, True)):
        for argument in arguments:
            leg, separator, value = argument.partition("=")
            if not separator or not value or leg not in legs or leg in files or leg in references:
                raise FrozenRunError("frozen_archive_original_input_invalid")
            if stored:
                if value != legs[leg]["library"]["archive_sha256"]:
                    raise FrozenRunError("frozen_archive_original_digest_mismatch", leg)
                references.add(leg)
            else:
                files[leg] = Path(value)
    if (files.keys() | references) != legs.keys():
        raise FrozenRunError(_LEGACY_EVIDENCE_MISSING, "archive input for every leg required")
    return files, references


def _bind_original_umask(library: dict[str, Any], tar_umask: int, leg: str) -> None:
    mask = f"{tar_umask:04o}"
    if library.get("tar_umask", mask) != mask:
        raise FrozenRunError("frozen_library_archive_mode_mismatch", leg)
    library["tar_umask"] = mask


def _import_receipt(digest: str, size: int, legs: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage_version": _IMPORT_RECEIPT_VERSION,
        "original_manifest": {"stage_version": _LEGACY_STAGE_VERSION, "sha256": digest, "byte_length": size},
        "legs": legs,
    }


def _read_registered_staging(connection: Any, digest: str
                             ) -> tuple[dict[str, Any], dict[str, bytes], bytes | None]:
    receipt = _registered_manifest(connection, digest)
    if receipt["stage_version"] == _STAGE_VERSION:
        archives = {
            leg: _registered_archive(connection, leg, entry["library"])
            for leg, entry in receipt["legs"].items()
        }
        return receipt, archives, None

    reference = receipt["original_manifest"]
    if (not isinstance(reference, dict) or set(reference) != {"stage_version", "sha256", "byte_length"}
            or type(reference["stage_version"]) is not int or reference["stage_version"] != _LEGACY_STAGE_VERSION
            or not _valid_sha256(reference["sha256"])
            or type(reference["byte_length"]) is not int or reference["byte_length"] <= 0):
        raise FrozenRunError("frozen_archive_manifest_binding_invalid", "original manifest")
    original_digest = reference["sha256"]
    original = _snapshot_record(connection, original_digest, "original manifest")
    if original["byte_length"] != reference["byte_length"]:
        raise FrozenRunError("frozen_archive_snapshot_length_mismatch", "original manifest")
    original_bytes = original["content"]
    legs = _original_legs(original_bytes)
    archives = {}
    for leg, entry in legs.items():
        library = entry["library"]
        content = _registered_archive(connection, leg, library)
        # The receipt fixes the verified tar bytes. Re-derive the permissions
        # from their headers so readback needs only managed evidence.
        with _open_archive(content, leg) as archive:
            tar_umask = _infer_tar_umask(archive, leg)
            for member in archive:
                _archive_member_path(member, content, leg)
                if member.type not in (tarfile.DIRTYPE, tarfile.REGTYPE):
                    raise FrozenRunError("frozen_library_archive_type_mismatch", leg)
                _check_tar_mode(member, tar_umask, leg)
        _bind_original_umask(library, tar_umask, leg)
        archives[leg] = content
    if _canonical_json(receipt) != _canonical_json(_import_receipt(original_digest, len(original_bytes), legs)):
        raise FrozenRunError("frozen_archive_manifest_binding_invalid", digest)
    return receipt, archives, original_bytes


def import_original_archives(trusted_manifest_sha256: str, *, repo_root: Path, mirror_root: Path,
                             manifest_file: Path | None = None, archive_files: Sequence[str] = (),
                             archive_sha256s: Sequence[str] = ()) -> dict[str, Any]:
    """Verify originals against an operator's independently retained v1 receipt digest."""
    if not _valid_sha256(trusted_manifest_sha256):
        raise FrozenRunError(_LEGACY_EVIDENCE_MISSING, "trusted original manifest SHA-256 required")
    if manifest_file is None:
        with _managed_archive(repo_root, "read") as connection:
            original_manifest = _snapshot_record(
                connection, trusted_manifest_sha256, "original manifest", missing_code=_LEGACY_EVIDENCE_MISSING,
            )["content"]
    else:
        original_manifest = _original_file(manifest_file, "original manifest")
        if hashlib.sha256(original_manifest).hexdigest() != trusted_manifest_sha256:
            raise FrozenRunError("frozen_archive_original_digest_mismatch", "original manifest")
    legs = _original_legs(original_manifest)
    files, references = _original_inputs(legs, archive_files, archive_sha256s)
    originals = {}
    for leg, path in files.items():
        library = legs[leg]["library"]
        content = _original_file(path, leg, byte_length=library["archive_byte_length"])
        if hashlib.sha256(content).hexdigest() != library["archive_sha256"]:
            raise FrozenRunError("frozen_archive_original_digest_mismatch", leg)
        originals[leg] = content
    if references:
        with _managed_archive(repo_root, "read") as connection:
            for leg in legs:
                if leg in references:
                    originals[leg] = _registered_archive(connection, leg, legs[leg]["library"])

    with _private_work() as work:
        mirror = Path(mirror_root)
        rows = manifest_rows(mirror / "MANIFEST.sha256")
        for leg, entry in legs.items():
            library = entry["library"]
            designations = designated_leg(leg)
            scripts = [script for script, _ in _read_scripts(mirror, designations, rows)]
            if _leg_manifest(library, scripts) != entry:
                raise FrozenRunError("frozen_archive_mirror_binding_mismatch", leg)
            source = _library_source(leg, designations)
            git = _isolated_git(repo_root, source, work, "git")
            tar_umask = _validate_tar(originals[leg], _git_tree(source, git), git, tar_umask=None)
            _bind_original_umask(library, tar_umask, leg)

    receipt = _import_receipt(trusted_manifest_sha256, len(original_manifest), legs)
    receipt_bytes = _canonical_json(receipt)
    digest = hashlib.sha256(receipt_bytes).hexdigest()
    with _managed_archive(repo_root, "write") as connection, connection.transaction():
        _register_content(connection, original_manifest, _ORIGINAL_MANIFEST_KIND,
                          "d1:frozen-stage-import:original-json", trusted_manifest_sha256,
                          version=_LEGACY_STAGE_VERSION)
        for leg, entry in legs.items():
            _register_content(connection, originals[leg], _LIBRARY_ARCHIVE_KIND,
                              f"d1:frozen-stage-import:original-tar:leg={leg}",
                              entry["library"]["archive_sha256"], version=_LEGACY_STAGE_VERSION)
        _register_content(connection, receipt_bytes, _STAGE_MANIFEST_KIND, _IMPORT_RECEIPT_SOURCE,
                          digest, version=_IMPORT_RECEIPT_VERSION,
                          receipt_conflict="frozen_archive_receipt_conflict")

    with _managed_archive(repo_root, "read") as connection:
        recorded, archives, manifest = _read_registered_staging(connection, digest)
        if recorded != receipt or archives != originals or manifest != original_manifest:
            raise FrozenRunError("frozen_archive_readback_mismatch", digest)
    return {**receipt, "manifest_sha256": digest}


def read_staged_archives(manifest_sha256: str, *, repo_root: Path, _connection: Any | None = None
                         ) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read managed originals and their verified logical bindings by receipt digest."""
    if not _valid_sha256(manifest_sha256):
        raise FrozenRunError("frozen_archive_manifest_digest_invalid")
    if _connection is None:
        with _managed_archive(repo_root, "read") as connection:
            manifest, archives, _ = _read_registered_staging(connection, manifest_sha256)
        return manifest, archives

    from polymarket_alpha_lab.papersim.d1compare import acquire

    try:
        acquire._verify_read_connection(_connection, Path(repo_root))
    except acquire.AcquisitionError as error:
        raise FrozenRunError("frozen_archive_read_connection_unverified", error.code) from None
    with _connection.transaction():
        manifest, archives, _ = _read_registered_staging(_connection, manifest_sha256)
    return manifest, archives


def restore_archives(manifest_sha256: str, *, repo_root: Path,
                     staging_root: Path) -> dict[str, Any]:
    """Verify managed readback and exercise safe extraction in a private leaf."""
    with _private_work() as work:
        manifest, archives = read_staged_archives(manifest_sha256, repo_root=repo_root)
        for leg, content in archives.items():
            _extract_library(work, _library_tree(manifest["legs"][leg]["library"]), content)
    return {**manifest, "manifest_sha256": manifest_sha256}


_OVERRIDE_LINE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=")


def apply_overrides(source_text: str, overrides: Mapping[str, str],
                    designation: ScriptDesignation) -> tuple[str, tuple[str, ...]]:
    """The recorded-shim fallback: rewrite ONLY allow-listed constants.

    Each override must name a constant on the designation's
    ``overridable`` allow-list; the assignment LINE is located by an
    exact ``^NAME =`` match and replaced by ``NAME = <value>`` with the
    value embedded as a Python literal.  Everything else stays
    byte-identical, every changed line is recorded, names not on the
    allow-list (including read-only inventory and any window constant
    that has no code existence) are mechanically rejected, and an
    assignment that appears zero times or more than once is refused.
    """
    if not overrides:
        return source_text, ()
    rejected = sorted(set(overrides) - designation.overridable)
    if rejected:
        raise FrozenRunError("frozen_override_not_allowed",
                             f"{designation.mirror_path}: {','.join(rejected)}")
    lines = source_text.splitlines(keepends=True)
    changed: list[str] = []
    for name in sorted(overrides):
        pattern = re.compile(rf"^{name}\s*=")
        hits = [index for index, line in enumerate(lines) if pattern.match(line)]
        if len(hits) != 1:
            raise FrozenRunError(
                "frozen_override_assignment_ambiguous" if hits else
                "frozen_override_assignment_missing",
                f"{designation.mirror_path}:{name}: {len(hits)} assignments")
        index = hits[0]
        literal = json.dumps(overrides[name])  # a JSON string is a Python str literal
        changed.append(f"L{index + 1}: {lines[index].rstrip()} -> {name} = {literal}")
        lines[index] = f"{name} = {literal}\n"
    return "".join(lines), tuple(changed)


# ---------------------------------------------------------------------------
# Fixed child environment (TZ UTC, pinned hash seed, zero DB, zero creds)
# ---------------------------------------------------------------------------

#: The ONLY variables a frozen child may see.  Everything else in the
#: parent environment (including any DSN, token or credential) is
#: dropped, never inherited.
CHILD_ENV_ALLOWLIST = ("PATH", "LANG", "TZ", "HOME", "PYTHONHASHSEED",
                       "PYTHONUNBUFFERED")

#: Credential/DB-shaped variable fragments refused even inside the
#: allow-list output (defense in depth: the allow-list never copies
#: them, and the check fails loudly if one ever appears).
_FORBIDDEN_ENV_FRAGMENTS = ("DSN", "POSTGRES", "PG", "PASSWORD", "TOKEN",
                            "SECRET", "KEY", "CREDENTIAL")


def child_environment(base_env: Mapping[str, str] | None = None, *,
                      sandbox_home: str = SERVER_HOME) -> dict[str, str]:
    """The fixed environment every frozen child runs under.

    ``TZ=UTC`` and ``PYTHONHASHSEED=0`` are pinned; ``HOME`` points at
    the sandbox root; ``PATH``/``LANG`` pass through.  No database
    variable, credential or project configuration survives: the child
    is structurally DB-less and credential-less.
    """
    base = dict(base_env or {})
    env = {
        "PATH": base.get("PATH", "/usr/bin:/bin"),
        "LANG": base.get("LANG", "C.UTF-8"),
        "TZ": "UTC",
        "HOME": sandbox_home,
        "PYTHONHASHSEED": "0",
        "PYTHONUNBUFFERED": "1",
    }
    for name in env:
        if any(fragment in name.upper() for fragment in _FORBIDDEN_ENV_FRAGMENTS):
            raise FrozenRunError("frozen_env_forbidden_variable", name)  # pragma: no cover
    return env


# ---------------------------------------------------------------------------
# Recording urllib layer (the one-capture feed for both paths)
# ---------------------------------------------------------------------------


def _request_meta(url: str, request: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"method": "GET"}
    split = urllib.parse.urlsplit(url)
    meta["host"] = split.hostname
    meta["path"] = split.path
    query = dict(urllib.parse.parse_qsl(split.query, keep_blank_values=True))
    meta["query"] = d1_journal.sanitize_query(query)
    headers = getattr(request, "headers", None)
    if isinstance(headers, Mapping):
        meta["headers"] = dict(headers)
    elif headers is not None:
        meta["headers"] = {str(key): str(value) for key, value in headers.items()}
    return meta


class RecordingUrllib:
    """Intercepts the staged scripts' network reads into the day journal.

    The frozen scripts call ``urllib.request.urlopen`` directly; at
    authorized run time the child wrapper installs this recorder so
    every read is captured ``(url, utc_fetched_at, request meta without
    credentials, response bytes, sha256)`` into the shared journal --
    Path L consumes the bytes in-stage and Path N builds packs from the
    same journal.  Egress is limited to the leg's allow-list; any other
    host is refused before a byte moves.

    ``fetch`` and ``clock`` are injected so offline tests never touch
    the network; the DEFAULT fetch is real ``urllib.request.urlopen``
    and exists only for authorized run steps -- this pass never
    constructs a recorder without an injected fetch.
    """

    def __init__(self, allowed_hosts: frozenset[str], *,
                 fetch: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime.datetime] | None = None) -> None:
        self.allowed_hosts = frozenset(allowed_hosts)
        self.entries: list[d1_journal.JournalEntry] = []
        self._fetch = fetch
        self._clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))

    def _real_fetch(self, request: Any, timeout: Any) -> Any:  # pragma: no cover
        import urllib.request  # authorized run time only; never exercised here
        return urllib.request.urlopen(request, timeout=timeout)

    def request_meta(self, url: str, request: Any) -> dict[str, Any]:
        return _request_meta(url, request)

    def record(self, url: str, request: Any, content: bytes,
               utc_fetched_at: datetime.datetime | None = None) -> d1_journal.JournalEntry:
        """Record one completed read (sanitized meta, digest-bound bytes)."""
        entry = d1_journal.capture(
            url=url,
            utc_fetched_at=utc_fetched_at or self._clock(),
            request_meta=self.request_meta(url, request),
            content=content,
        )
        self.entries.append(entry)
        return entry

    def urlopen(self, request: Any, timeout: Any = None) -> io.BytesIO:
        """The ``urllib.request.urlopen`` stand-in the scripts call.

        Enforces the egress allow-list, fetches through the injected
        transport, records the read and returns the response as a
        byte-stream context manager (``read()`` works unchanged).
        """
        url = getattr(request, "full_url", None) or str(request)
        _enforce_egress(url, self.allowed_hosts)
        fetch = self._fetch or self._real_fetch
        response = fetch(request, timeout)
        content = response.read()
        self.record(url, request, content)
        stream = io.BytesIO(content)
        setattr(stream, "url", url)
        return stream

    def install(self, monkeypatch_target: Any) -> None:
        """Patch ``monkeypatch_target.urlopen`` (test/child wiring only)."""
        monkeypatch_target.urlopen = self.urlopen


# ---------------------------------------------------------------------------
# Plan section 5 run gates (the chain driver refuses without them)
# ---------------------------------------------------------------------------

#: The gates that must ALL hold before any run step executes (plan
#: section 5): B2/B3/B4 merged at the execution base, the G7
#: managed-write defect fixed with authorized evidence, this lane's
#: code merged after an Iron Rule 4 PASS review, and the S1 scheduler
#: installation on the execution host.
REQUIRED_RUN_GATES = (
    "b2_b3_b4_merged_commit",
    "g7_fix_evidence",
    "d1_lane_reviewed_merge_commit",
    "s1_scheduler_install_record",
    "execution_base_commit",
)

_GATE_LABELS = {
    "b2_b3_b4_merged_commit": "B2 (incl. FX-34), B3 (incl. FX-29), B4 (incl. bankroll exposure) merged at the execution base",
    "g7_fix_evidence": "G7 managed-write defect fixed with authorized evidence",
    "d1_lane_reviewed_merge_commit": "this lane's C1/C2/C4 code merged after an independent Iron Rule 4 PASS review",
    "s1_scheduler_install_record": "S1 scheduler installed on the execution host",
    "execution_base_commit": "the pinned execution-base commit",
}


@dataclass(frozen=True)
class DayRunAuthorization:
    """The recorded facts of the plan section 5 gates for one day run."""

    b2_b3_b4_merged_commit: str = ""
    g7_fix_evidence: str = ""
    d1_lane_reviewed_merge_commit: str = ""
    s1_scheduler_install_record: str = ""
    execution_base_commit: str = ""

    def __post_init__(self) -> None:
        for name, value in self.as_mapping().items():
            if not isinstance(value, str):
                raise FrozenRunError("frozen_authorization_field_invalid", name)

    def as_mapping(self) -> dict[str, str]:
        return {
            "b2_b3_b4_merged_commit": self.b2_b3_b4_merged_commit,
            "g7_fix_evidence": self.g7_fix_evidence,
            "d1_lane_reviewed_merge_commit": self.d1_lane_reviewed_merge_commit,
            "s1_scheduler_install_record": self.s1_scheduler_install_record,
            "execution_base_commit": self.execution_base_commit,
        }

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "DayRunAuthorization":
        if not isinstance(document, Mapping):
            raise FrozenRunError("frozen_authorization_invalid", "object required")
        unknown = sorted(set(document) - set(REQUIRED_RUN_GATES))
        if unknown:
            raise FrozenRunError("frozen_authorization_field_unknown",
                                 ",".join(unknown))
        return cls(**document)


def check_run_gates(authorization: DayRunAuthorization | None) -> tuple[str, ...]:
    """The unmet gates (empty tuple = every gate recorded as satisfied).

    A recorded authorization is necessary, never sufficient: the
    evidence strings are registered through the interval-start record
    and the independent review chain -- this check only refuses runs
    whose gate facts are absent.
    """
    if authorization is None:
        return REQUIRED_RUN_GATES
    facts = authorization.as_mapping()
    return tuple(name for name in REQUIRED_RUN_GATES if not facts[name].strip())


# ---------------------------------------------------------------------------
# The chain driver (authorized run time only; NEVER invoked in this pass)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainPlan:
    """One execution's inputs, available while its staging lease is held."""

    leg: str
    staged: tuple[StagedScript, ...]
    library: LibraryTree
    bindings: tuple[LayoutBinding, ...]
    mount_argv: tuple[str, ...]
    egress_hosts: frozenset[str]
    child_env: dict[str, str]
    manifest_sha256: str
    _work: _PrivateWork
    _leg_manifest: dict[str, Any]

    @property
    def staging_root(self) -> Path:
        return self._work.path

    def as_mapping(self) -> dict[str, Any]:
        """Serializable provenance and logical bindings for the day record."""
        return {
            "leg": self.leg, "manifest_sha256": self.manifest_sha256,
            **json.loads(_canonical_json(self._leg_manifest)),
        }

    def run_child(self, argv: Sequence[str], *, timeout: float = 120
                  ) -> subprocess.CompletedProcess[bytes]:
        """Run a foreground child with the fixed environment and a staging lease."""
        with self._work.process(
            argv, env=child_environment(self.child_env), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            stdout, stderr = process.communicate(timeout=timeout)
            result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        result.check_returncode()
        return result


@contextmanager
def plan_chain(leg: str, *, mirror_root: Path, staging_root: Path,
               repo_root: Path, manifest_sha256: str,
               base_env: Mapping[str, str] | None = None,
               _connection: Any | None = None) -> Iterator[ChainPlan]:
    """Read the sealed selection and present one leg in an execution scope."""
    designations = designated_leg(leg)
    _library_source(leg, designations)
    with _private_work() as work:
        options = {} if _connection is None else {"_connection": _connection}
        manifest, archives = read_staged_archives(manifest_sha256, repo_root=repo_root, **options)
        entry = manifest["legs"].get(leg)
        if entry is None:
            raise FrozenRunError("frozen_archive_manifest_leg_missing", leg)
        mirror = Path(mirror_root)
        scripts = _read_scripts(mirror, designations, manifest_rows(mirror / "MANIFEST.sha256"))
        if _leg_manifest(entry["library"], [script for script, _ in scripts]) != entry:
            raise FrozenRunError("frozen_archive_mirror_binding_mismatch", leg)
        staged = _copy_scripts(work.path, scripts)
        library = _library_tree(entry["library"])
        _extract_library(work, library, archives[leg])
        del archives
        bindings = sandbox_bindings(staged, library, staging_root=str(work.path))
        yield ChainPlan(
            leg=leg,
            staged=staged,
            library=library,
            bindings=bindings,
            mount_argv=tuple(mount_namespace_plan(bindings)),
            egress_hosts=egress_hosts(leg),
            child_env=child_environment(base_env),
            manifest_sha256=manifest_sha256,
            _work=work,
            _leg_manifest=entry,
        )


def preflight_designated_chain(leg: str, *, mirror_root: Path, staging_root: Path,
                               repo_root: Path, manifest_sha256: str,
                               batch_sha256: str) -> dict[str, Any]:
    """Verify and record a managed capture without starting frozen execution."""
    from polymarket_alpha_lab.papersim.d1compare import frozenexec

    return frozenexec.preflight(
        leg, mirror_root=mirror_root, staging_root=staging_root, repo_root=repo_root,
        manifest_sha256=manifest_sha256, batch_sha256=batch_sha256,
    )


def execute_designated_chain(leg: str, *, mirror_root: Path, staging_root: Path,
                             repo_root: Path,
                             authorization: DayRunAuthorization | None,
                             manifest_sha256: str | None = None,
                             base_env: Mapping[str, str] | None = None,
                             _executor=None) -> dict[str, Any]:
    """Authorized-run-time driver of one designated chain (never run here).

    Refuses unless every plan section 5 gate is recorded.  With the
    gates and an executor supplied, the callback receives a plan for the
    verified archive. It runs foreground children through the plan and
    returns while the staging lease is still held.
    """
    unmet = check_run_gates(authorization)
    if unmet:
        raise FrozenRunError(
            "frozen_run_gates_unmet",
            "; ".join(f"{name}: {_GATE_LABELS[name]}" for name in unmet))
    if _executor is None:
        raise FrozenRunError("frozen_run_executor_not_wired",
                             "the authorized wiring executor is not part of "
                             "this preparation pass")
    if manifest_sha256 is None:
        raise FrozenRunError("frozen_archive_manifest_digest_required")
    with plan_chain(leg, mirror_root=mirror_root, staging_root=staging_root,
                    repo_root=repo_root, manifest_sha256=manifest_sha256,
                    base_env=base_env) as plan:
        return _executor(plan)
