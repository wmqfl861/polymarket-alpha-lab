"""Real same-root OLD->CURRENT source switch, rollback and refusals (class 3).

Section 61 (2026-09-27) pins this proof to the Linux tuple: the OLD and
CANDIDATE sources must share ONE exact PostgreSQL 18 runtime inventory — the
qualified portable prefix assembled from the pinned package (the server's
~/pg-runtime-src pattern, DELIVERY_PLAN §50/§61). The earlier Windows/PG17
execution of the same seven-stage shape stays frozen historical evidence
(§55), and the §51 Windows asset comparison is equally historical. Nothing
here relabels that evidence as Linux: Linux acceptance requires this proof to
actually run on Linux against the pinned PG18 prefix.

Exact server run (ubuntu@166.1.232.93, repository at ~/polymarket-alpha-lab,
prefix assembled once and reused verbatim by every stage):

    cd ~/polymarket-alpha-lab
    for variable in $(compgen -e PG); do unset "$variable"; done
    env -u PYTEST_ADDOPTS -u PYTEST_PLUGINS -u PYTHONPATH -u PYTHONHOME \\
      PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONUTF8=1 \\
      POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX="$HOME/pg-runtime-src" \\
      POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1 \\
      .venv/bin/python -u -m pytest -q -s -o junit_family=legacy \\
      tests/test_project_postgres_version_switch_native.py

Everything runs on ONE disposable synthetic 'Original Project' root inside a
fresh private proof directory. The OLD source and the executed candidate
source are verified binary `git archive` extractions of pinned commits; the
current locked Python environment supplies dependencies for both. No released
kit is re-accepted, no existing installation, user database, user credential
or public network is touched, and the test never fetches history or software.

Business equality means stored records/timestamps/claims/ledger rows, never
cluster-file byte equality: managed admission may legitimately change logs,
control state and WAL. Outcomes stay empty here, so equality only proves the
switch created no outcome rows, not settlement preservation.
"""
from hashlib import sha1, sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import threading
from threading import Event
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SOURCE = ROOT / 'src'


def _select_adjacent_worktree_source() -> None:
    """This file's own tree is CURRENT; never run an unrelated editable copy."""
    try:
        import polymarket_alpha_lab as package
    except ImportError:
        package = None
    if package is None or not Path(package.__file__).resolve().is_relative_to(_SOURCE.resolve()):
        for name in [name for name in sys.modules
                     if name == 'polymarket_alpha_lab' or name.startswith('polymarket_alpha_lab.')]:
            del sys.modules[name]
    sys.path.insert(0, str(_SOURCE))
    import polymarket_alpha_lab as selected
    assert Path(selected.__file__).resolve().is_relative_to(_SOURCE.resolve())


_select_adjacent_worktree_source()

from polymarket_alpha_lab.project_postgres import backup_format as fmt
from polymarket_alpha_lab.project_postgres import distribution, files, sql
from polymarket_alpha_lab.project_postgres.runtime import runtime_version, verify_runtime
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from tests.native_port_selection import pick_port

ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
# The pinned source pair. The OLD side is the tagged v0.1.0-native-preview.1
# source whose offline pair comparison is §51 history; section 61 re-pins the
# SAME source pair as a NEW Linux execution expectation — the CANDIDATE side
# is the integrated HEAD at run time and both sides must install from ONE
# exact PG18 runtime inventory (see LINUX_RUNTIME_MAJOR below).
OLD_COMMIT = '2b20002c83ee33acb95fb2ca3de841f3e883ebbf'
OLD_TREE = 'd6f6750b1068a3c75ab6249d5ca4f1ef7e010ee4'
LINUX_RUNTIME_MAJOR = '18'
OLD_CATALOG_ENTRIES = 63
OLD_CATALOG_FINGERPRINT = '3e8c813dcb7ce2389f11aa7aebba03819392f014e1208910c75d70bf709ccee5'
CANDIDATE_CATALOG_ENTRIES = 70
INSIDE_OLD_MIGRATION = '20260701000000_team_forecast_tables.sql'
NEW_MIGRATIONS = ('20260914000000_research_dispatch_batches.sql',
                  '20260915000000_research_dispatch_turns.sql',
                  '20260915010000_research_model_budgets.sql',
                  '20260915020000_research_paper_simulations.sql',
                  '20260919000000_research_uncapped_audit.sql',
                  '20261101000000_papersim_ledger.sql',
                  '20261107000000_papersim_perf_view_fix.sql')
FEATURE_TABLES = ('research_capture.dispatch_batches', 'research_capture.dispatch_turns',
                  'research_capture.model_budgets', 'research_capture.paper_simulations',
                  'research_capture.uncapped_authorizations',
                  'public.decisions', 'public.resolutions', 'public.bankroll')
RECORD_IDS = ('vs-record-1', 'vs-record-2', 'vs-record-3')
CONDITION_IDS = ('vs-condition-1', 'vs-condition-2', 'vs-condition-3')
WORKFLOW = ROOT / '.github/workflows/native-postgres.yml'


def _git(*args: str) -> bytes:
    """Sanitized read-only Git, following the distribution helper convention."""
    executable = shutil.which('git')
    if executable is None:
        raise AssertionError('git executable required for the pinned archives')
    env = {k: v for k, v in files.clean_environment().items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT='0')
    result = subprocess.run([executable, '--literal-pathspecs', '-C', str(ROOT),
                             '-c', 'core.fsmonitor=false', *args], env=env,
                            stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=180, check=False, shell=False)
    if result.returncode:
        raise AssertionError('git operation failed: ' + args[0])
    return result.stdout


def _extract_verified_archive(destination: Path, commit: str, expected_tree: str) -> dict[str, str]:
    """Verified `git archive --format=tar` extraction into a fresh directory.

    Commit-to-tree identity, the full member inventory against Git blobs, and
    traversal/absolute/link/duplicate/case-collision/type rejects happen BEFORE
    any byte is written. Only binary subprocess output is used, never shell or
    PowerShell text redirection.
    """
    tree = _git('rev-parse', '--verify', commit + '^{tree}').decode().strip()
    if not re.fullmatch('[0-9a-f]{40}', tree) or tree != expected_tree:
        raise AssertionError('pinned commit does not resolve to the required tree')
    inventory: dict[str, str] = {}
    for entry in _git('ls-tree', '-rlz', '--full-tree', commit).split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        name = raw_name.decode('utf-8')
        mode, kind, blob, size = metadata.split()
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise AssertionError('nonregular Git source member: ' + name)
        inventory[name] = blob.decode()
    if not inventory or len(inventory) > 200000:
        raise AssertionError('implausible source inventory size')
    archive = _git('archive', '--format=tar', commit)
    if len(archive) > 536870912:
        raise AssertionError('archive size limit exceeded')
    destination.mkdir(parents=False, exist_ok=False)
    seen: set[str] = set()
    folded: set[tuple[str, str]] = set()
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        for item in tar:
            name = item.name
            parts = PurePosixPath(name).parts
            if (not name or PurePosixPath(name).is_absolute() or '\\' in name
                    or '..' in parts or re.match(r'^[A-Za-z]:', name)
                    or any(part in ('', '.') for part in parts)):
                raise AssertionError('unsafe archive member: ' + repr(name))
            if item.issym() or item.islnk():
                raise AssertionError('link member rejected: ' + name)
            if not (item.isfile() or item.isdir()):
                raise AssertionError('unsupported member type: ' + name)
            if name in seen:
                raise AssertionError('duplicate member name: ' + name)
            parent = str(PurePosixPath(*parts[:-1]))
            if (parent, parts[-1].casefold()) in folded:
                raise AssertionError('case-colliding member name: ' + name)
            seen.add(name)
            folded.add((parent, parts[-1].casefold()))
            target = destination.joinpath(*parts)
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if name not in inventory:
                raise AssertionError('member missing from the Git inventory: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise AssertionError('extraction target already exists: ' + name)
            raw = tar.extractfile(item).read()
            blob = sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if blob != inventory[name]:
                raise AssertionError('extracted bytes differ from the Git blob: ' + name)
            with target.open('xb') as stream:
                stream.write(raw)
    extracted = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    if extracted != set(inventory):
        raise AssertionError('extracted file inventory differs from the Git tree')
    return inventory


_OLD_ENTRY = r'''
from pathlib import Path
import runpy
import sys

source = Path(sys.argv[1]).resolve()
script = source / "scripts" / sys.argv[2]
arguments = sys.argv[3:]

assert not any(
    name == "polymarket_alpha_lab"
    or name.startswith("polymarket_alpha_lab.")
    for name in sys.modules
)

sys.path.insert(0, str(source / "src"))
sys.argv = [str(script), *arguments]

try:
    runpy.run_path(str(script), run_name="__main__")
except SystemExit as error:
    exit_code = error.code
else:
    exit_code = 0

loaded = [
    module for name, module in sys.modules.items()
    if name == "polymarket_alpha_lab"
    or name.startswith("polymarket_alpha_lab.")
]
assert loaded
assert all(
    Path(module.__file__).resolve().is_relative_to(source / "src")
    for module in loaded
)
raise SystemExit(exit_code)
'''

_OLD_RUNNER = r'''
from pathlib import Path
import runpy
import sys

source, payload = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()

assert not any(
    name == "polymarket_alpha_lab"
    or name.startswith("polymarket_alpha_lab.")
    for name in sys.modules
)

sys.path.insert(0, str(source / "src"))
sys.argv = [str(payload), *sys.argv[3:]]

try:
    runpy.run_path(str(payload), run_name="__main__")
except SystemExit as error:
    exit_code = error.code
except Exception as error:
    if payload.name == "seed.py":
        try:
            context = error.__context__
            value = context.errno if isinstance(context, OSError) else None
            errno = str(value) if type(value) is int else "ERRNO_UNAVAILABLE"
            print("OLD_SEED_FIRST_FAILURE_ERRNO=" + errno, file=sys.stderr)
        except BaseException:
            pass  # Even a broken diagnostic must preserve the original error.
    raise
else:
    exit_code = 0

loaded = [
    module for name, module in sys.modules.items()
    if name == "polymarket_alpha_lab"
    or name.startswith("polymarket_alpha_lab.")
]
assert loaded
assert all(
    module.__file__ is not None
    and Path(module.__file__).resolve().is_relative_to(source / "src")
    for module in loaded
)
print("PAYLOAD_SOURCE_OK", file=sys.stderr)
raise SystemExit(exit_code)
'''

# CURRENT script probes: an unrelated package decoy is inserted BEFORE the real
# script runs, CURRENT's package is never preloaded, and provenance is proven.
_CURRENT_ENTRY = r'''
from pathlib import Path
import runpy
import sys

root, foreign = map(Path, sys.argv[1:3])
script, arguments = sys.argv[3], sys.argv[4:]

sys.path.insert(0, str(foreign))
sys.argv = [str(root / "scripts" / script), *arguments]

try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as error:
    code = error.code
else:
    code = 0

loaded = [
    module for name, module in sys.modules.items()
    if name == "polymarket_alpha_lab"
    or name.startswith("polymarket_alpha_lab.")
]
assert loaded, "no project modules loaded"
assert all(
    module.__file__ is not None
    and Path(module.__file__).resolve().is_relative_to((root / "src").resolve())
    for module in loaded
), "foreign project code selected"
print("PROJECT_ENTRY_SOURCE_OK", file=sys.stderr)
raise SystemExit(code)
'''

_CURRENT_RUNNER = r'''
from pathlib import Path
import runpy
import sys

source, foreign, payload = map(Path, sys.argv[1:4])

assert not any(
    name == "polymarket_alpha_lab"
    or name.startswith("polymarket_alpha_lab.")
    for name in sys.modules
)

sys.path.insert(0, str(foreign))
sys.path.insert(0, str(source / "src"))
sys.argv = [str(payload), *sys.argv[4:]]

try:
    runpy.run_path(str(payload), run_name="__main__")
except SystemExit as error:
    exit_code = error.code
else:
    exit_code = 0

loaded = [
    module for name, module in sys.modules.items()
    if name == "polymarket_alpha_lab"
    or name.startswith("polymarket_alpha_lab.")
]
assert loaded
assert all(
    module.__file__ is not None
    and Path(module.__file__).resolve().is_relative_to(source.resolve() / "src")
    for module in loaded
), "foreign project code selected"
print("PAYLOAD_SOURCE_OK", file=sys.stderr)
raise SystemExit(exit_code)
'''

_SNAPSHOT_SQL = {
    'markets': 'SELECT condition_id,market_slug,forecast_cutoff_at,registered_at,'
               'paper_only,report_only,readonly FROM research_capture.markets ORDER BY condition_id',
    'attempts': 'SELECT record_id,condition_id,market_slug,team_id,model_id,protocol_version,task_id,'
                'data_as_of,recorded_at,octet_length(payload),payload_sha256,paper_only,report_only,readonly '
                'FROM research_capture.attempts ORDER BY record_id',
    'claims': 'SELECT record_id,condition_id,market_slug,team_id,model_id,protocol_version,task_id,'
              'data_as_of,claimed_at,octet_length(request_payload),request_sha256,paper_only,report_only,readonly '
              'FROM research_capture.execution_claims ORDER BY record_id',
    'outcomes': 'SELECT condition_id,market_slug,resolved_at,recorded_at,actual_yes,source_reference,'
                'source_content_sha256,paper_only,report_only,readonly '
                'FROM research_capture.outcomes ORDER BY condition_id',
}

# The migration ledger is owner-readable only; read it through the validated
# owner path, never by granting the application role new privileges.
_LEDGER_SQL = ('SELECT coalesce(json_agg(json_build_object(\'name\',name,\'sha256\',sha256,'
               '\'applied_at\',applied_at) ORDER BY name),\'[]\'::json) '
               'FROM project_private.migrations;')

_SNAPSHOT_PAYLOAD = r'''
import json, sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from polymarket_alpha_lab import research_capture_psycopg as capture
from polymarket_alpha_lab.project_postgres import backup_format as fmt
from polymarket_alpha_lab.project_postgres.runtime import verify_runtime
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres

queries = json.loads(sys.argv[2])

def plain(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value

def read(dsn):
    def operation(cursor):
        out = {}
        for name, statement in queries.items():
            cursor.execute(statement)
            out[name] = [[plain(value) for value in row] for row in cursor.fetchall()]
        return out
    return capture._local_transaction(dsn, operation, readonly=True)

db = ProjectPostgres(Path(sys.argv[1]))
runtime = verify_runtime(db.layout)
info = db._state()
with db.session() as session:
    business = session._call(read)
    # Owner-path ledger read while the borrowed engine is actually running.
    ledger = json.loads(db._psql(info, sys.argv[3]))
out = dict(instance=dict(info), runtime_version=runtime["version"],
           runtime_files=len(runtime["files"]), runtime_sha256=fmt.fingerprint(runtime["files"]))
out["snapshot"] = dict(business, ledger=ledger)
out["status_after"] = db.status()["status"]
print(json.dumps(out, ensure_ascii=True, allow_nan=False))
'''

# R03-diag2: failure-moment diagnostics executed inside the OLD seed child.
# Pure stdlib, prepended to the seed payload source; it activates only on
# the payload's failure path.  One stderr line with whitelisted fields
# only: no addresses (raw or encoded), inodes, PIDs, foreign ports,
# credentials, DSNs or locals; incomplete /proc input is UNAVAILABLE and
# never zero; the emit itself can never mask or replace the business
# exception.
_SEED_FAILURE_DIAGNOSTICS = r'''
from collections import deque as _deque
import json as _json
from pathlib import Path as _Path
import re as _re
import sys as _sys

_SEED_STATES = {'01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
                '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
                '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK',
                '0A': 'LISTEN', '0B': 'CLOSING', '0C': 'NEW_SYN_RECV',
                '0D': 'BOUND_INACTIVE'}


def _seed_diag_root():
    """sys.path[0] is the OLD src directory injected by the runner."""
    try:
        return _Path(_sys.path[0]).resolve()
    except Exception:
        return None


def _seed_diag_file(filename, root):
    try:
        if filename.startswith('<') and filename.endswith('>'):
            return filename
        path = _Path(filename)
        if not path.is_absolute():
            return filename
        if root is not None:
            try:
                return path.relative_to(root).as_posix()
            except ValueError:
                return '<outside>'
        return '<outside>'
    except Exception:
        return '<outside>'


def _seed_diag_frames(tb, root):
    """Innermost first and bounded: walk the FULL chain and keep the
    innermost (tail) 8 frames (codex DECISION 3 / R03-fix REV3).  tb
    runs outermost-first, so a bounded head slice would drop the
    decisive bind frame on long chains."""
    frames = _deque(maxlen=8)
    while tb is not None:
        code = tb.tb_frame.f_code
        frames.append([_seed_diag_file(code.co_filename, root),
                       code.co_name, tb.tb_lineno])
        tb = tb.tb_next
    return list(reversed(frames))


def _seed_diag_chain(exc):
    """Bounded breadth-first walk of __cause__ and __context__ (<= 4)."""
    chain = []
    seen = set()
    queue = [(exc, 'raised')]
    while queue and len(chain) < 4:
        current, via = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        errno = getattr(current, 'errno', None)
        chain.append({'via': via, 'type': type(current).__name__,
                      'errno': errno if type(errno) is int else None,
                      'suppress_context': bool(current.__suppress_context__),
                      'frames': _seed_diag_frames(current.__traceback__,
                                                  _seed_diag_root())})
        if current.__cause__ is not None:
            queue.append((current.__cause__, 'cause'))
        if current.__context__ is not None:
            queue.append((current.__context__, 'context'))
    return chain


def _seed_diag_tcp(name, port):
    """Bounded /proc/net/tcp{,6} counts for the task port only.

    Mirrors _old_seed_tcp_counts semantics: read at most 65537 bytes; a
    longer (truncated) read, a missing trailing newline or any malformed
    row yields 'UNAVAILABLE', never a zero count.
    """
    try:
        with open('/proc/net/' + name, 'rb') as stream:
            raw = stream.read(65537)
    except Exception:
        return 'UNAVAILABLE'
    if len(raw) > 65536 or not raw.endswith(b'\n'):
        return 'UNAVAILABLE'
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeDecodeError:
        return 'UNAVAILABLE'
    if not lines or lines[0].split()[:4] not in (
            ['sl', 'local_address', 'rem_address', 'st'],
            ['sl', 'local_address', 'remote_address', 'st']):
        return 'UNAVAILABLE'
    width = 8 if name == 'tcp' else 32
    pattern = '[0-9A-Fa-f]{' + str(width) + '}:[0-9A-Fa-f]{4}'
    counts = {'local': {}, 'peer': {}}
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 4 or fields[3] not in _SEED_STATES:
            return 'UNAVAILABLE'
        ports = []
        for endpoint in fields[1:3]:
            if not _re.fullmatch(pattern, endpoint):
                return 'UNAVAILABLE'
            ports.append(int(endpoint.rsplit(':', 1)[1], 16))
        for side, actual in zip(('local', 'peer'), ports):
            if actual == port:
                state = _SEED_STATES[fields[3]]
                counts[side][state] = counts[side].get(state, 0) + 1
    return counts


def _seed_diag_field(value):
    if isinstance(value, (dict, list)):
        return _json.dumps(value, sort_keys=True, separators=(',', ':'))
    return str(value)


def _seed_failure_emit(error, port, stream=None):
    """One stderr line at the failure moment; can never raise."""
    try:
        if stream is None:
            stream = _sys.stderr
        code = str(error)
        if not _re.fullmatch('[a-z0-9_]+', code):
            code = '<noncode>'
        errno = getattr(error, 'errno', None)
        chain = _seed_diag_chain(error)
        if type(port) is int and 1 <= port <= 65535 and port != 55432:
            tcp = _seed_diag_tcp('tcp', port)
            tcp6 = _seed_diag_tcp('tcp6', port)
        else:
            tcp = tcp6 = 'PORT_UNAVAILABLE'
        print('OLD_SEED_FAILURE_MOMENT code=' + code
              + ' errno=' + (str(errno) if type(errno) is int else 'null')
              + ' chain=' + _seed_diag_field(chain)
              + ' tcp=' + _seed_diag_field(tcp)
              + ' tcp6=' + _seed_diag_field(tcp6), file=stream)
    except BaseException:
        pass
'''


_SEED_PAYLOAD = _SEED_FAILURE_DIAGNOSTICS + r'''
import json, sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from polymarket_alpha_lab import research_execution_psycopg as execution
from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_execution import CapturedResearchRequest
from polymarket_alpha_lab.team_research_agent_types import (
    ResearchEvidence, ResearchModelReply, ResearchToolCall,
)
from polymarket_alpha_lab.team_research_intake import (
    GammaMarketSnapshot, prepare_team_research_from_gamma,
)


class Model:
    def __init__(self, source):
        self.source = source
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            name, args = "read_evidence", {"source_id": self.source}
        else:
            name, args = "finish_research", dict(
                probability_yes="0.6", confidence="0.2",
                summary="Synthetic version switch fixture.", source_ids=[self.source])
        return ResearchModelReply(
            (ResearchToolCall("call-" + str(self.calls), name, json.dumps(args)),), 1)


def request(index, team):
    now = datetime.now(UTC)
    slug = "vs-market-" + str(index)
    condition = "vs-condition-" + str(index)
    snapshot = GammaMarketSnapshot(slug, now, json.dumps(dict(
        slug=slug, conditionId=condition, question="Synthetic?",
        description="Synthetic resolution criterion", outcomes=["Yes", "No"],
        active=True, closed=False,
        endDate=(now + timedelta(days=1)).isoformat())).encode())
    evidence = ResearchEvidence("vs-source-" + str(index), team, condition,
                                "Synthetic source", "Synthetic text", "synthetic:version-switch", now)
    intake = prepare_team_research_from_gamma(
        snapshot, task_id="vs-task-" + str(index), team_id=team, condition_id=condition,
        as_of=now, evidence=(evidence,))
    return CapturedResearchRequest(
        "vs-record-" + str(index), "synthetic-model", "version-switch-proof-v1",
        now + timedelta(hours=1), intake, required_source_ids=("vs-source-" + str(index),))


root = Path(sys.argv[1])
try:
    _port = int(sys.argv[2]) if len(sys.argv) > 2 else 0
except ValueError:
    _port = 0
if not 1 <= _port <= 65535 or _port == 55432:
    _port = None
out = {}
try:
    db = ProjectPostgres(root)
    with db.session() as session:
        btc = request(1, "crypto_btc")
        completed = session.run_research(
            request=btc, model_factory=lambda _: Model("vs-source-1"))
        out["completed"] = completed.to_dict()
        eth = request(2, "crypto_eth")

        def broken(_):
            raise RuntimeError("synthetic model factory failure")

        failed = session.run_research(request=eth, model_factory=broken)
        out["failed"] = failed.to_dict()
        outstanding = request(3, "crypto_btc")
        owned, state = session._call(execution._claim, request=outstanding)
        out["outstanding_owned"] = owned
        out["outstanding"] = state.to_dict()
        out["inspections"] = {record_id: session.inspect(record_id=record_id).to_dict()
                              for record_id in ("vs-record-1", "vs-record-2", "vs-record-3")}
        try:
            session.evaluate()
            out["evaluation_refusal"] = "none"
        except Exception as error:
            out["evaluation_refusal"] = type(error).__name__ + ":" + str(error)
    out["status_after"] = db.status()["status"]
except (OSError, ProjectDatabaseError) as error:
    _seed_failure_emit(error, _port)
    raise
print(json.dumps(out, ensure_ascii=True, allow_nan=False))
'''

_READBACK_PAYLOAD = r'''
import json, sys
from pathlib import Path
from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres

out = dict(admitted=False, code=None, records=None, evaluation=None, status_after=None)
db = ProjectPostgres(Path(sys.argv[1]))
try:
    with db.session() as session:
        out["admitted"] = True
        out["records"] = {record_id: session.inspect(record_id=record_id).to_dict()
                          for record_id in sys.argv[2:]}
        try:
            session.evaluate()
            out["evaluation"] = "none"
        except Exception as error:
            out["evaluation"] = type(error).__name__ + ":" + str(error)
except ProjectDatabaseError as error:
    out["code"] = str(error)
try:
    out["status_after"] = db.status()["status"]
except ProjectDatabaseError as error:
    out["status_after"] = "error:" + str(error)
print(json.dumps(out, ensure_ascii=True, allow_nan=False))
'''

_BUNDLE_PROBE_PAYLOAD = r'''
import json, sys
from pathlib import Path
from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres

out = dict(code="admitted", status_after=None)
try:
    with ProjectPostgres(Path(sys.argv[1])).session():
        pass
except ProjectDatabaseError as error:
    out["code"] = str(error)
try:
    out["status_after"] = ProjectPostgres(Path(sys.argv[1])).status()["status"]
except ProjectDatabaseError as error:
    out["status_after"] = "error:" + str(error)
print(json.dumps(out, ensure_ascii=True, allow_nan=False))
'''


def _child_env() -> dict[str, str]:
    env = files.clean_environment()
    for key in tuple(env):
        if key.upper().startswith('PYTEST') or key.upper().startswith('GIT_'):
            del env[key]
    env['PYTHONUTF8'] = '1'
    return env


def _run(args, cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), env=_child_env(), stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=timeout, check=False, shell=False)


def _json_stdout(result: subprocess.CompletedProcess) -> dict:
    return json.loads(result.stdout.decode('utf-8'))


def _blocked_reason(result: subprocess.CompletedProcess) -> str:
    # Child stderr may also carry the wrapper's provenance marker; only one
    # line is the CLI's blocked-reason JSON object.
    for line in reversed(result.stderr.decode('utf-8', 'replace').strip().splitlines()):
        try:
            decoded = json.loads(line)
        except ValueError:
            continue
        if type(decoded) is dict and 'reason_code' in decoded:
            return decoded['reason_code']
    raise AssertionError('no blocked-reason JSON in child stderr: '
                         + result.stderr.decode('utf-8', 'replace')[-400:])


def _stderr_text(result: subprocess.CompletedProcess) -> str:
    return result.stderr.decode('utf-8', 'replace')


def test_version_switch_native_ci_is_required():
    """The dedicated Linux storage-partition workflow step and its gate are wired."""
    text = WORKFLOW.read_text(encoding='utf-8').replace('\r\n', '\n')
    worker, gate = text.split('  linux-native:\n')
    linux_worker = worker.split('  linux-native-part:\n', 1)[1]
    checkout = linux_worker.split('      - uses: actions/checkout@', 1)[1].split('      - uses:', 1)[0]
    assert 'fetch-depth: 0\n' in checkout
    assert 'persist-credentials: false\n' in checkout
    assert '      fail-fast: false\n' in linux_worker and '    timeout-minutes: 40\n' in linux_worker
    assert 'continue-on-error' not in text
    partition = linux_worker.split('      - name: Prove isolated native partition\n', 1)[1]
    switch = partition.split('      - name: Verify tracked source unchanged\n', 1)[0]
    assert '      - name: Prove native version switch\n' in switch
    assert "if: matrix.partition == 'storage'" in switch
    assert 'tests/test_project_postgres_version_switch_native.py' in switch
    assert 'POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX="$RUNNER_TEMP/pg18-portable-prefix"' in switch
    assert 'export POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1' in switch
    assert 'export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1' in switch
    assert 'export PYTHONUTF8=1' in switch
    assert 'tee "$RUNNER_TEMP/version-switch-proof.log"' in switch
    assert '--junitxml="$RUNNER_TEMP/version-switch-proof.xml"' in switch
    assert 'test ${PIPESTATUS[0]} -eq 0' in switch
    upload = linux_worker.split('      - uses: actions/upload-artifact@', 1)[1]
    assert '${{ runner.temp }}/version-switch-proof.log' in upload
    assert '${{ runner.temp }}/version-switch-proof.xml' in upload
    assert 'if-no-files-found: error' in upload and 'if: always()' in upload
    assert 'needs: [linux-native-part]' in gate
    assert 'test "$NATIVE_RESULT" = success' in gate
    # The frozen Windows/PG17 step (section 55 history) stays present verbatim
    # until the coordinator's controlled required-context replacement; this
    # file never rewires GitHub settings.
    windows_worker = text.split('  windows-native-part:\n', 1)[1].split('  windows-native:\n', 1)[0]
    assert "POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX: 'C:\\Program Files\\PostgreSQL\\17'" in windows_worker
    assert '      - name: Prove native version switch\n' in windows_worker
    assert 'tests/test_project_postgres_version_switch_native.py' in windows_worker


def test_bundle_admission_interface_pins_linux_pg18():
    """L2's public admission constants must stay on the section-61 tuple.

    The synthetic-bundle marker set below is assembled exclusively through
    distribution.py's public constants/functions; this pins that interface to
    the selected Linux x86_64 / PostgreSQL 18 admission contract.
    """
    assert distribution.TARGET == 'linux-x86_64'
    assert distribution.POSTGRES_MAJOR == LINUX_RUNTIME_MAJOR
    assert re.fullmatch(LINUX_RUNTIME_MAJOR + r'\.\d+',
                        distribution.POSTGRES_MAJOR + '.0')


def _plain(value):
    from datetime import date, datetime, time
    from decimal import Decimal
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _snapshot_rows(cursor, queries):
    out = {}
    for name, statement in queries.items():
        cursor.execute(statement)
        out[name] = [[_plain(value) for value in row] for row in cursor.fetchall()]
    return out


def _snapshot_current(original: Path) -> dict:
    from polymarket_alpha_lab import research_capture_psycopg as capture
    db = ProjectPostgres(original)
    runtime = verify_runtime(db.layout)
    info = db._state()
    with db.session() as session:
        snapshot = session._call(
            lambda dsn: capture._local_transaction(
                dsn, lambda cursor: _snapshot_rows(cursor, _SNAPSHOT_SQL), readonly=True))
        # Owner-path ledger read while the session's engine is running.
        ledger = json.loads(db._psql(info, _LEDGER_SQL))
    return dict(instance=dict(info), runtime_version=runtime['version'],
                runtime_files=len(runtime['files']),
                runtime_sha256=fmt.fingerprint(runtime['files']),
                snapshot={**snapshot, 'ledger': ledger})


def _effective_catalog(root: Path) -> tuple[int, str, tuple[tuple[str, str], ...]]:
    rows = sql.migration_catalog(files.Layout(root))
    pairs = tuple((name, digest) for name, digest, _ in rows)
    return len(rows), fmt.fingerprint(pairs), pairs


def _physical_counts(db: ProjectPostgres, info: dict) -> dict:
    return {
        'ledger': db._psql(info, 'SELECT count(*) FROM project_private.migrations;'),
        'tables': {name: db._psql(info, f"SELECT to_regclass('{name}') IS NOT NULL;")
                   for name in FEATURE_TABLES},
        'attempts': db._psql(info, 'SELECT count(*) FROM research_capture.attempts;'),
        'claims': db._psql(info, 'SELECT count(*) FROM research_capture.execution_claims;'),
        'outcomes': db._psql(info, 'SELECT count(*) FROM research_capture.outcomes;'),
    }


def _require_linux_pg18_admission(admitted: dict) -> None:
    """Pin the qualified package admission to the section-61 Linux/PG18 tuple.

    Both sides are checked: the shipped verifier's public constants (L2's
    interface, imported — never a divergent copy) AND the literal selected
    tuple, so neither this proof nor distribution.py can silently drift off
    the sole delivered platform.
    """
    assert distribution.TARGET == 'linux-x86_64' == admitted['target']
    assert distribution.POSTGRES_MAJOR == LINUX_RUNTIME_MAJOR
    assert admitted['postgres_version'].split('.')[0] == LINUX_RUNTIME_MAJOR


def _write_synthetic_bundle(root: Path) -> dict:
    """A complete, well-shaped synthetic kit marker set that the verifier passes.

    The engine member is inert bytes; only its manifest hash is ever consulted.
    It is never executed, imported or described as a released kit. Admission
    fields come exclusively from distribution.py's public constants (L2's
    Linux/PG18 contract); this file never edits that module.
    """
    actual = set(distribution.FIXED)
    for name in distribution.PUBLIC_ENTRYPOINTS:
        if (root / name).is_file():
            actual.add(name)
    for base in distribution.SOURCE_ROOTS:
        for target in (root / base).rglob('*'):
            relative = target.relative_to(root).as_posix()
            if target.is_file() and distribution.selected_source(relative):
                actual.add(relative)
    manifest_files = {name: files.digest_file(root / name) for name in sorted(actual)}
    seed = root / distribution.ENGINE
    seed.write_bytes(b'synthetic inert engine seed; never executed or imported\n')
    manifest_files[distribution.ENGINE] = files.digest_file(seed)
    manifest = dict(format=distribution.FORMAT, source_commit='c' * 40, source_tree='d' * 40,
                    target=distribution.TARGET,
                    postgres_version=distribution.POSTGRES_MAJOR + '.0',
                    python_requires='>=3.11', files=manifest_files)
    (root / distribution.MANIFEST).write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    admitted = distribution.verify_distribution(root)
    _require_linux_pg18_admission(admitted)
    return admitted


def _remove_bundle_markers(root: Path) -> None:
    for marker in (root / distribution.ENGINE, root / distribution.MANIFEST):
        if marker.exists():
            marker.unlink()


class _Rig(dict):
    __getattr__ = dict.__getitem__


def prepare_verified_archives_and_private_root(tmp_path, monkeypatch) -> _Rig:
    prefix = Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    assert prefix.is_absolute() and all((prefix / part).is_dir() for part in ('bin', 'lib', 'share')), \
        'native PostgreSQL prefix binaries are required once the opt-in is enabled'
    if os.name != 'nt':
        # Section 61 Linux tuple pin: every stage below installs its runtime
        # from this one prefix, so OLD and CANDIDATE share a single exact PG18
        # runtime inventory. A Windows/PG17 run is frozen history (section 55),
        # never counted as the Linux version-pair evidence.
        assert runtime_version(prefix).split('.')[0] == LINUX_RUNTIME_MAJOR, \
            'the Linux version-switch proof requires the pinned PG18 portable prefix'
    for key in tuple(os.environ):
        if key.upper().startswith('PG'):
            monkeypatch.delenv(key)
    parent = tmp_path
    if os.name == 'nt':
        runner = Path(os.environ['RUNNER_TEMP'])
        assert runner.is_dir(), 'RUNNER_TEMP must exist once the opt-in is enabled'
        parent = runner / ('pal-version-switch-' + uuid.uuid4().hex)
        files.private_directory(parent, create=True)
    neutral = parent / 'neutral-cwd'
    neutral.mkdir()
    foreign = parent / 'unrelated-editable' / 'polymarket_alpha_lab'
    foreign.mkdir(parents=True)
    (foreign / '__init__.py').write_text("raise RuntimeError('FOREIGN_EDITABLE_SELECTED')\n",
                                         encoding='ascii')
    payloads = parent / 'payloads'
    payloads.mkdir()
    old_source = parent / 'old-source'
    candidate = parent / 'candidate-source'
    old_inventory = _extract_verified_archive(old_source, OLD_COMMIT, OLD_TREE)
    candidate_commit = _git('rev-parse', '--verify', 'HEAD').decode().strip()
    candidate_tree = _git('rev-parse', '--verify', 'HEAD^{tree}').decode().strip()
    assert re.fullmatch('[0-9a-f]{40}', candidate_commit)
    assert re.fullmatch('[0-9a-f]{40}', candidate_tree)
    _extract_verified_archive(candidate, candidate_commit, candidate_tree)
    original = parent / 'Original Project'
    original.mkdir()
    selected = [name for name in old_inventory if distribution.selected_source(name)]
    assert set(distribution.FIXED) <= set(selected)
    for name in sorted(selected):
        target = original.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old_source.joinpath(*PurePosixPath(name).parts), target)
    original_pairs = _effective_catalog(original)
    assert original_pairs[0] == OLD_CATALOG_ENTRIES
    assert original_pairs[1] == OLD_CATALOG_FINGERPRINT
    candidate_entries, candidate_fingerprint, candidate_pairs = _effective_catalog(candidate)
    assert candidate_entries == CANDIDATE_CATALOG_ENTRIES
    assert candidate_pairs[:OLD_CATALOG_ENTRIES] == original_pairs[2]
    assert {name for name, _ in candidate_pairs[OLD_CATALOG_ENTRIES:]} == set(NEW_MIGRATIONS)
    assert (original / 'supabase/migrations' / INSIDE_OLD_MIGRATION).is_file()
    print('version-switch fixture: candidate', candidate_commit, candidate_tree,
          'old-catalog-63', original_pairs[1][:16], 'candidate-catalog-70',
          candidate_fingerprint[:16])
    port = pick_port()
    return _Rig(base=parent, neutral=neutral, foreign=foreign.parent, payloads=payloads,
                old_source=old_source, candidate=candidate, original=original,
                candidate_commit=candidate_commit, candidate_tree=candidate_tree,
                candidate_fingerprint=candidate_fingerprint, port=port, prefix=prefix,
                # Keep the venv interpreter path verbatim: on POSIX, resolving
                # the .venv symlink selects the bare base interpreter, whose
                # isolated mode has no psycopg for the OLD/CURRENT payloads.
                python=sys.executable, records={}, snapshot=None,
                identity=None, runtime=None, backup=None, phases=[])


def _phase(rig, name):
    print(f'version-switch phase {name}: start')
    return time.monotonic()


def _phase_done(rig, name, start):
    elapsed = time.monotonic() - start
    rig['phases'].append((name, elapsed))
    print(f'version-switch phase {name}: done in {elapsed:.1f}s')


def _payload_file(rig, name: str, text: str) -> Path:
    path = rig['payloads'] / name
    path.write_text(text, encoding='utf-8')
    return path


def _old_cli(rig, *arguments, timeout: int = 900) -> subprocess.CompletedProcess:
    return _run([rig['python'], '-I', '-B', '-c', _OLD_ENTRY, str(rig['old_source']),
                 'project_database.py', *arguments], rig['neutral'], timeout=timeout)


def _old_seed_tcp_counts(path: Path, port: int) -> str:
    """Bounded post-exit snapshot; never infer absence from incomplete input."""
    states = {
        '01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
        '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
        '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK',
        '0A': 'LISTEN', '0B': 'CLOSING', '0C': 'NEW_SYN_RECV',
        '0D': 'BOUND_INACTIVE',
    }
    try:
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536 or not raw.endswith(b'\n'):
            return 'UNAVAILABLE'
        lines = raw.decode('ascii').splitlines()
        if not lines or lines[0].split()[:4] not in (
                ['sl', 'local_address', 'rem_address', 'st'],
                ['sl', 'local_address', 'remote_address', 'st']):
            return 'UNAVAILABLE'
        counts = {'local': {}, 'peer': {}}
        width = 8 if path.name == 'tcp' else 32
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 4 or fields[3] not in states:
                return 'UNAVAILABLE'
            ports = []
            for endpoint in fields[1:3]:
                if not re.fullmatch(r'[0-9A-Fa-f]{' + str(width) + r'}:[0-9A-Fa-f]{4}', endpoint):
                    return 'UNAVAILABLE'
                ports.append(int(endpoint.rsplit(':', 1)[1], 16))
            for side, actual in zip(('local', 'peer'), ports):
                if actual == port:
                    state = states[fields[3]]
                    counts[side][state] = counts[side].get(state, 0) + 1
        return json.dumps(counts, sort_keys=True, separators=(',', ':'))
    except Exception:  # diagnostics never expose a raw row or exception text
        return 'UNAVAILABLE'


def _observe_old_seed_exit(rig) -> None:
    """No ownership claim: a later proc snapshot cannot prove the first cause."""
    tcp = tcp6 = 'UNAVAILABLE'
    try:
        port = rig.port
        base = rig.base.resolve(strict=True)
        original = rig.original.resolve(strict=True)
        if (type(port) is int and 0 < port < 65536 and port != 55432
                and original == base / 'Original Project'):
            tcp = _old_seed_tcp_counts(Path('/proc/net/tcp'), port)
            tcp6 = _old_seed_tcp_counts(Path('/proc/net/tcp6'), port)
    except Exception:
        pass
    print('OLD_SEED_AFTER_CHILD_EXIT OWNER_UNKNOWN', 'tcp=' + tcp, 'tcp6=' + tcp6)


def _seed_wait_local_time_wait(value):
    """Local TIME_WAIT count from one _old_seed_tcp_counts result."""
    if value == 'UNAVAILABLE':
        return None
    return json.loads(value).get('local', {}).get('TIME_WAIT', 0)


def _wait_for_seed_port_ready(rig, *, budget_s=90.0, interval_s=1.0,
                              _clock=None, _sleep=None):
    """Bounded readiness wait (codex DECISION 4): proceed with the one
    original seed run only once the task port has no local TIME_WAIT.

    UNKNOWN ('UNAVAILABLE') is never treated as zero; timeout and
    indeterminate /proc input fail honestly via pytest.fail before any
    seed child is started.  Only the task port is queried; the two
    snapshot lines are whitelisted evidence (counts, port, elapsed)."""
    clock = _clock or time.monotonic
    sleep = _sleep or time.sleep
    port = rig['port']

    def snapshot():
        return (_old_seed_tcp_counts(Path('/proc/net/tcp'), port),
                _old_seed_tcp_counts(Path('/proc/net/tcp6'), port))

    started = clock()
    tcp, tcp6 = snapshot()
    print(f'OLD_SEED_WAIT_BEFORE port={port} tcp={tcp} tcp6={tcp6} '
          f'monotonic_ns={time.monotonic_ns()}')
    deadline = started + budget_s
    while True:
        left = _seed_wait_local_time_wait(tcp)
        left6 = _seed_wait_local_time_wait(tcp6)
        if left is None or left6 is None:
            pytest.fail('OLD_SEED_WAIT_UNAVAILABLE port=' + str(port)
                        + ' waited_s=' + format(clock() - started, '.3f')
                        + ' tcp=' + str(tcp) + ' tcp6=' + str(tcp6)
                        + ' (UNKNOWN is never treated as zero; seed not '
                          'started)')
        if left == 0 and left6 == 0:
            break
        if clock() >= deadline:
            pytest.fail('OLD_SEED_WAIT_TIMEOUT port=' + str(port)
                        + ' budget_s=' + str(budget_s)
                        + ' waited_s=' + format(clock() - started, '.3f')
                        + ' tcp=' + str(tcp) + ' tcp6=' + str(tcp6)
                        + ' (local TIME_WAIT did not clear; seed not '
                          'started)')
        sleep(interval_s)
        tcp, tcp6 = snapshot()
    print(f'OLD_SEED_WAIT_AFTER port={port} tcp={tcp} tcp6={tcp6} '
          f'elapsed_s={format(clock() - started, ".3f")}')


def _echo_seed_child_stderr(result):
    """Verbatim child stderr echo before the failing assertion (codex C).

    Called from _old_payload's seed failure branch BEFORE the provenance
    assert, so the missing-marker mode (which raises inside _old_payload)
    still shows the full child stderr despite pytest assertion
    truncation.  Same source text as _stderr_text(result); success paths
    never print."""
    print('OLD_SEED_CHILD_STDERR_BEGIN')
    print(_stderr_text(result))
    print('OLD_SEED_CHILD_STDERR_END')


def _old_payload(rig, name: str, text: str, *arguments, timeout: int = 900):
    payload = _payload_file(rig, name, text)
    result = _run([rig['python'], '-I', '-B', '-c', _OLD_RUNNER, str(rig['old_source']),
                   str(payload), *arguments], rig['neutral'], timeout=timeout)
    if name == 'seed.py':
        try:
            if result.returncode != 0 or 'PAYLOAD_SOURCE_OK' not in _stderr_text(result):
                _echo_seed_child_stderr(result)
                _observe_old_seed_exit(rig)
        except BaseException:
            pass  # Observation cannot replace the existing provenance assertion.
    assert 'PAYLOAD_SOURCE_OK' in _stderr_text(result)
    return result


def _current_cli(rig, script: str, *arguments, source=None,
                 timeout: int = 900) -> subprocess.CompletedProcess:
    result = _run([rig['python'], '-I', '-B', '-c', _CURRENT_ENTRY, str(source or rig['candidate']),
                   str(rig['foreign']), script, *arguments], rig['neutral'], timeout=timeout)
    stderr = _stderr_text(result)
    assert 'PROJECT_ENTRY_SOURCE_OK' in stderr
    assert 'FOREIGN_EDITABLE_SELECTED' not in stderr
    return result


def _current_payload(rig, name: str, text: str, *arguments, source=None, timeout: int = 900):
    payload = _payload_file(rig, name, text)
    result = _run([rig['python'], '-I', '-B', '-c', _CURRENT_RUNNER,
                   str(source or rig['candidate']), str(rig['foreign']), str(payload),
                   *arguments], rig['neutral'], timeout=timeout)
    assert 'PAYLOAD_SOURCE_OK' in _stderr_text(result)
    return result


def initialize_and_seed_with_old_source(rig) -> None:
    original, port = rig['original'], str(rig['port'])
    installed = _old_cli(rig, '--root', str(original), 'install-runtime',
                         '--from-directory', str(rig['prefix']))
    assert installed.returncode == 0, _stderr_text(installed)
    assert _json_stdout(installed)['status'] == 'runtime_installed'
    initialized = _old_cli(rig, '--root', str(original), 'init', '--port', port)
    assert initialized.returncode == 0, _stderr_text(initialized)
    assert _json_stdout(initialized)['migrations_applied'] == OLD_CATALOG_ENTRIES
    refused = _old_cli(rig, '--root', str(original), 'init', '--port', port)
    assert refused.returncode == 1
    assert _blocked_reason(refused) == 'project_postgres_existing_data_not_reinitialized'
    status = _old_cli(rig, '--root', str(original), 'status')
    assert _json_stdout(status)['status'] == 'stopped'
    _wait_for_seed_port_ready(rig)
    seeded = _old_payload(rig, 'seed.py', _SEED_PAYLOAD, str(original), str(rig['port']))
    assert seeded.returncode == 0, _stderr_text(seeded)
    records = _json_stdout(seeded)
    completed, failed, outstanding = (records['completed'], records['failed'],
                                      records['outstanding'])
    assert completed['status'] == 'captured' and completed['research_status'] == 'completed'
    assert completed['recorded_at'] is not None and completed['paper_only'] is True
    assert completed['report_only'] is True and completed['readonly'] is True
    assert failed['status'] == 'captured' and failed['research_status'] == 'failed'
    assert failed['recorded_at'] is not None
    assert records['outstanding_owned'] is True
    assert outstanding['status'] == 'incomplete' and outstanding['recorded_at'] is None
    for record_id, execution, expected in zip(
            RECORD_IDS, (completed, failed, outstanding),
            ('already_captured', 'already_captured', 'incomplete')):
        inspection = records['inspections'][record_id]
        assert inspection == dict(execution, status=expected), record_id
    assert 'research_execution_history_incomplete' in records['evaluation_refusal']
    assert records['status_after'] == 'stopped'
    # Later phases compare the INSPECTION view keyed by record id; it is the
    # stable cross-source shape for both OLD and CURRENT readers.
    rig['records'] = records['inspections']
    snapshot = _old_payload(rig, 'snapshot-old.py', _SNAPSHOT_PAYLOAD, str(original),
                            json.dumps(_SNAPSHOT_SQL), _LEDGER_SQL)
    assert snapshot.returncode == 0, _stderr_text(snapshot)
    old_view = _json_stdout(snapshot)
    assert old_view['status_after'] == 'stopped'
    data = old_view['snapshot']
    assert len(data['markets']) == 3 and len(data['attempts']) == 2
    assert len(data['claims']) == 3 and data['outcomes'] == []
    assert len(data['ledger']) == OLD_CATALOG_ENTRIES
    assert sorted(row[0] for row in data['markets']) == sorted(CONDITION_IDS)
    assert all(row[4:] == [True, True, True] for row in data['markets'])
    assert all(row[11:] == [True, True, True] for row in data['claims'])
    assert all(row[11:] == [True, True, True] for row in data['attempts'])
    rig['snapshot'] = data
    rig['identity'] = old_view['instance']
    rig['runtime'] = {key: old_view[key] for key in
                      ('runtime_version', 'runtime_files', 'runtime_sha256')}


def prove_lifecycle_and_prepare_cold_backup(rig) -> None:
    from polymarket_alpha_lab import research_execution_psycopg as execution_store
    original = rig['original']
    entered, release = Event(), Event()
    outcomes, failures = [], []

    def held(dsn):
        result = execution_store.inspect_captured_research_with_psycopg(
            dsn, record_id=RECORD_IDS[0])
        entered.set()
        assert release.wait(60), 'test did not release the admitted read'
        return result

    def work():
        try:
            with ProjectPostgres(original).session() as session:
                rig['drain_session'] = session
                outcomes.append(session._call(held))
        except BaseException as error:  # noqa: BLE001  (the worker must retain its failure)
            failures.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    assert entered.wait(120), 'admitted read did not start'
    session = rig['drain_session']
    closer = threading.Thread(target=session.close)
    closer.start()
    deadline = time.monotonic() + 60
    while session._active and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not session._active, 'close did not seal new admissions'
    with pytest.raises(files.ProjectDatabaseError, match='session_closed'):
        session._call(lambda dsn: pytest.fail('work admitted on the closing session'))
    busy = _old_cli(rig, '--root', str(original), 'down')
    assert busy.returncode == 1 and _blocked_reason(busy) == 'project_postgres_busy'
    release.set()
    closer.join(120)
    worker.join(120)
    assert failures == [] and len(outcomes) == 1
    assert outcomes[0].to_dict() == rig['records'][RECORD_IDS[0]]
    del rig['drain_session']
    assert ProjectPostgres(original).status()['status'] == 'stopped'
    started = _old_cli(rig, '--root', str(original), 'up')
    assert _json_stdout(started)['status'] == 'running'
    with ProjectPostgres(original).session() as borrowed:
        assert borrowed.inspect(record_id=RECORD_IDS[0]).to_dict() == \
            rig['records'][RECORD_IDS[0]]
    assert ProjectPostgres(original).status()['status'] == 'running'
    stopped = _old_cli(rig, '--root', str(original), 'down')
    assert _json_stdout(stopped)['status'] == 'stopped'
    destination = rig['base'] / 'Private Backup'
    backed_up = _old_cli(rig, '--root', str(original), 'backup', '--destination', str(destination))
    assert backed_up.returncode == 0, _stderr_text(backed_up)
    receipt = _json_stdout(backed_up)
    assert receipt['status'] == 'backup_created' and receipt['contains_credentials'] is True
    archive = destination / 'snapshot.palpg.zip'
    verified = _old_cli(rig, '--root', str(original), 'verify-backup', '--archive', str(archive),
                        '--sha256', receipt['sha256'], '--trusted-backup')
    assert verified.returncode == 0, _stderr_text(verified)
    assert _json_stdout(verified)['status'] == 'backup_verified'
    rig['backup'] = dict(destination=destination, archive=archive, sha256=receipt['sha256'])


def prove_stopped_selection_and_current_common_reads(rig) -> None:
    original = rig['original']
    old_status = _json_stdout(_old_cli(rig, '--root', str(original), 'status'))
    current = _current_cli(rig, 'project_database.py', '--root', str(original), 'status')
    assert current.returncode == 0, current.stdout.decode('utf-8', 'replace')
    current_status = _json_stdout(current)
    assert old_status == current_status
    assert old_status['status'] == 'stopped'
    assert old_status['instance_id'] == rig['identity']['instance_id']
    assert old_status['port'] == rig['identity']['port']
    assert old_status['version'] == rig['identity']['version']
    for source in (rig['candidate'], rig['old_source']):
        assert not (source / '.local').exists() and not (source / 'runtime').exists()
    worklist = _current_cli(rig, 'review_resolution_queue.py', '--root', str(original))
    assert worklist.returncode == 0, worklist.stdout.decode('utf-8', 'replace')
    listing = _json_stdout(worklist)
    assert listing['incomplete_execution_count'] == 1
    assert listing['evaluation_blocked_by_incomplete'] is True
    assert listing['registered_market_count'] == 3
    text = json.dumps(listing)
    for identity in (*CONDITION_IDS, 'vs-market-1', 'vs-market-2', 'vs-market-3'):
        assert identity in text
    missing = _current_cli(rig, 'manage_research_tasks.py', '--root', str(original),
                           'inspect-batch', '--batch-id', 'vs-missing-batch')
    assert missing.returncode == 1
    refused = _json_stdout(missing)
    assert refused['status'] == 'failed'
    assert refused['reason_code'] == 'research_dispatch_operation_failed'
    readback = _current_payload(rig, 'readback.py', _READBACK_PAYLOAD, str(original), *RECORD_IDS)
    assert readback.returncode == 0, readback.stdout.decode('utf-8', 'replace')
    current_view = _json_stdout(readback)
    assert current_view['admitted'] is True and current_view['code'] is None
    assert current_view['records'] == rig['records']
    assert 'research_execution_history_incomplete' in current_view['evaluation']
    assert current_view['status_after'] == 'stopped'
    snapshot = _snapshot_current(original)
    assert snapshot['snapshot'] == rig['snapshot']
    assert snapshot['instance'] == rig['identity']
    assert {key: snapshot[key] for key in rig['runtime']} == rig['runtime']
    assert ProjectPostgres(original).status()['status'] == 'stopped'


def prove_compatible_old_source_rollback(rig) -> None:
    original = rig['original']
    rolled = _old_payload(rig, 'rollback.py', _READBACK_PAYLOAD, str(original), *RECORD_IDS)
    assert rolled.returncode == 0, rolled.stdout.decode('utf-8', 'replace')
    view = _json_stdout(rolled)
    assert view['admitted'] is True and view['code'] is None
    assert view['records'] == rig['records']
    assert 'research_execution_history_incomplete' in view['evaluation']
    assert view['status_after'] == 'stopped'
    snapshot = _old_payload(rig, 'snapshot-rollback.py', _SNAPSHOT_PAYLOAD, str(original),
                            json.dumps(_SNAPSHOT_SQL), _LEDGER_SQL)
    old_view = _json_stdout(snapshot)
    assert old_view['snapshot'] == rig['snapshot']
    assert old_view['instance'] == rig['identity']
    assert {key: old_view[key] for key in rig['runtime']} == rig['runtime']
    assert old_view['status_after'] == 'stopped'
    print('version-switch rollback: shared APIs admitted at 63/63; original rows intact')


def prove_bundle_integrity_refusals(rig) -> None:
    original = rig['original']
    candidate_damaged = rig['base'] / 'candidate-source-damaged'
    shutil.copytree(rig['candidate'], candidate_damaged)
    try:
        admitted = _write_synthetic_bundle(candidate_damaged)
        print('version-switch kit admission contract:', admitted['target'],
              admitted['postgres_version'])
        victim = candidate_damaged / 'src/polymarket_alpha_lab/analytics.py'
        victim_bytes = victim.read_bytes()
        probe = _current_payload(rig, 'bundle-source.py', _BUNDLE_PROBE_PAYLOAD, str(original),
                                 source=candidate_damaged)
        assert _json_stdout(probe)['code'] == 'admitted'
        with victim.open('ab') as stream:
            stream.write(b'\n# changed fixture\n')
        probe = _current_payload(rig, 'bundle-source-damaged.py', _BUNDLE_PROBE_PAYLOAD,
                                 str(original), source=candidate_damaged)
        assert _json_stdout(probe) == {'code': 'project_bundle_invalid_or_changed',
                                       'status_after': 'stopped'}
        entrypoint = _current_cli(rig, 'review_resolution_queue.py', '--root', str(original),
                                  source=candidate_damaged)
        assert entrypoint.returncode == 1
        assert _json_stdout(entrypoint)['reason_code'] == 'resolution_queue_operation_failed'
        victim.write_bytes(victim_bytes)
        _write_synthetic_bundle(original)
        migration = original / 'supabase/migrations' / INSIDE_OLD_MIGRATION
        migration_bytes = migration.read_bytes()
        probe = _current_payload(rig, 'bundle-data.py', _BUNDLE_PROBE_PAYLOAD, str(original))
        assert _json_stdout(probe)['code'] == 'admitted'
        with migration.open('ab') as stream:
            stream.write(b'\n-- changed fixture\n')
        probe = _current_payload(rig, 'bundle-data-damaged.py', _BUNDLE_PROBE_PAYLOAD,
                                 str(original))
        assert _json_stdout(probe) == {'code': 'project_bundle_invalid_or_changed',
                                       'status_after': 'stopped'}
        migration.write_bytes(migration_bytes)
    finally:
        _remove_bundle_markers(original)
        _remove_bundle_markers(candidate_damaged)
        shutil.rmtree(candidate_damaged, ignore_errors=True)
    snapshot = _snapshot_current(original)
    assert snapshot['snapshot'] == rig['snapshot'] and snapshot['instance'] == rig['identity']
    assert ProjectPostgres(original).status()['status'] == 'stopped'


def _stop_engine(rig) -> None:
    try:
        ProjectPostgres(rig['original']).down()
    except Exception as error:  # noqa: BLE001  (recorded, never force-killed)
        print('version-switch engine stop during fixture edits failed:', type(error).__name__)


def _running_physical_state(original: Path) -> tuple[dict, dict, dict]:
    """Owner-path ledger/table counts with the engine explicitly up, then down.

    This is also the real `up()` pending-count observation; `down()` always
    runs in finally so the fixture is stopped before the next edit or backup.
    """
    db = ProjectPostgres(original)
    state = db.up()
    try:
        info = db._state()
        counts = _physical_counts(db, info)
    finally:
        db.down()
    assert db.status()['status'] == 'stopped'
    return state, info, counts


def prove_pending_catalog_and_backup_prefix_paths(rig) -> None:
    original, backup = rig['original'], rig['backup']
    lock_file = original / 'database/migrations.lock.json'
    try:
        shutil.copyfile(rig['candidate'] / 'database/migrations.lock.json', lock_file)
        for name in NEW_MIGRATIONS:
            shutil.copyfile(rig['candidate'] / 'supabase/migrations' / name,
                            original / 'supabase/migrations' / name)
        pending = _current_payload(rig, 'pending.py', _BUNDLE_PROBE_PAYLOAD, str(original))
        assert _json_stdout(pending) == {'code': 'project_postgres_migrations_pending',
                                         'status_after': 'stopped'}
        state, info, counts = _running_physical_state(original)
        assert info == rig['identity']
        assert state['status'] == 'migrations_pending' and state['pending_migrations'] == 7
        assert state['started_here'] is True
        assert counts['ledger'] == '63' and counts['attempts'] == '2'
        assert counts['claims'] == '3' and counts['outcomes'] == '0'
        assert set(counts['tables'].values()) == {'f'}
        mismatch = _old_cli(rig, '--root', str(original), 'verify-backup',
                            '--archive', str(backup['archive']), '--sha256', backup['sha256'],
                            '--trusted-backup')
        assert mismatch.returncode == 1
        assert _blocked_reason(mismatch) == 'project_postgres_backup_migrations_mismatch'
        extended = _current_cli(rig, 'project_database.py', '--root', str(original),
                                'verify-backup', '--archive', str(backup['archive']),
                                '--sha256', backup['sha256'], '--trusted-backup',
                                '--allow-catalog-extension')
        assert extended.returncode == 0, extended.stdout.decode('utf-8', 'replace')
        receipt = _json_stdout(extended)
        assert receipt['status'] == 'backup_verified' and receipt['database_started'] is False
        assert receipt['migration_catalog'] == dict(
            match='append_only_extension', backup_entries=63, current_entries=70,
            current_sha256=rig['candidate_fingerprint'], additional_entries=7,
            database_ledger_checked=False, migrations_applied=0)
        restore = _current_cli(rig, 'project_database.py', '--root', str(original), 'restore',
                               '--archive', str(backup['archive']), '--sha256', backup['sha256'],
                               '--trusted-backup', '--allow-catalog-extension')
        assert restore.returncode == 1
        assert _blocked_reason(restore) == 'project_postgres_restore_existing_data_refused'
        assert (original / '.local/postgres').is_dir()
        assert not (original / '.local/postgres.restoring').exists()
        inside_path = original / 'supabase/migrations' / INSIDE_OLD_MIGRATION
        raw_inside, raw_lock = inside_path.read_bytes(), lock_file.read_bytes()
        try:
            altered = raw_inside + b'\n-- test-only non-prefix alteration\n'
            inside_path.write_bytes(altered)
            catalog = json.loads(lock_file.read_text(encoding='utf-8'))
            entry = next(item for item in catalog['migrations']
                         if item['name'] == INSIDE_OLD_MIGRATION)
            entry['sha256'] = sha256(altered).hexdigest()
            lock_file.write_text(json.dumps(catalog), encoding='utf-8')
            refused = _current_cli(rig, 'project_database.py', '--root', str(original),
                                   'verify-backup', '--archive', str(backup['archive']),
                                   '--sha256', backup['sha256'], '--trusted-backup',
                                   '--allow-catalog-extension')
            assert refused.returncode == 1
            assert _blocked_reason(refused) == 'project_postgres_backup_migrations_mismatch'
            conflict = _current_payload(rig, 'conflict.py', _BUNDLE_PROBE_PAYLOAD, str(original))
            assert _json_stdout(conflict) == {
                'code': 'project_postgres_migration_history_conflict', 'status_after': 'stopped'}
        finally:
            inside_path.write_bytes(raw_inside)
            lock_file.write_bytes(raw_lock)
        try:
            with inside_path.open('ab') as stream:
                stream.write(b'\n-- unmanifested change\n')
            changed = _current_cli(rig, 'project_database.py', '--root', str(original), 'up')
            assert changed.returncode == 1
            assert _blocked_reason(changed) == 'project_postgres_migration_changed'
        finally:
            inside_path.write_bytes(raw_inside)
            _stop_engine(rig)
        try:
            (original / 'supabase/migrations' / '20990101000000_extra.sql').write_bytes(
                b'SELECT 1;\n')
            inventory = _current_cli(rig, 'project_database.py', '--root', str(original), 'up')
            assert inventory.returncode == 1
            assert _blocked_reason(inventory) == 'project_postgres_migration_inventory_mismatch'
        finally:
            (original / 'supabase/migrations' / '20990101000000_extra.sql').unlink()
            _stop_engine(rig)
        state, info, counts = _running_physical_state(original)
        assert info == rig['identity']
        assert state['status'] == 'migrations_pending' and state['pending_migrations'] == 7
        assert counts['ledger'] == '63' and counts['attempts'] == '2'
        assert counts['claims'] == '3' and counts['outcomes'] == '0'
        assert set(counts['tables'].values()) == {'f'}
        assert ProjectPostgres(original).status()['status'] == 'stopped'
    finally:
        # The staged valid 70-entry catalog stays for the explicit migration
        # phase; only ensure the engine is stopped for the next fixture edit.
        _stop_engine(rig)
    assert lock_file.read_bytes() == (rig['candidate'] / 'database/migrations.lock.json').read_bytes()
    print('version-switch pending/refusals: 70/63 refused; prefix extension exact')


def prove_explicit_migration_and_old_catalog_refusal(rig) -> None:
    original = rig['original']
    migrated = _current_cli(rig, 'project_database.py', '--root', str(original), 'migrate')
    assert migrated.returncode == 0, migrated.stdout.decode('utf-8', 'replace')
    assert _json_stdout(migrated)['migrations_applied'] == 7
    again = _current_cli(rig, 'project_database.py', '--root', str(original), 'migrate')
    assert _json_stdout(again)['migrations_applied'] == 0
    readback = _current_payload(rig, 'readback-70.py', _READBACK_PAYLOAD, str(original), *RECORD_IDS)
    view = _json_stdout(readback)
    assert view['admitted'] is True and view['records'] == rig['records']
    assert 'research_execution_history_incomplete' in view['evaluation']
    assert view['status_after'] == 'stopped'
    missing = _current_cli(rig, 'manage_research_tasks.py', '--root', str(original),
                           'inspect-batch', '--batch-id', 'vs-missing-batch')
    assert missing.returncode == 3
    envelope = _json_stdout(missing)
    assert envelope['operation'] == 'inspect-batch'
    assert envelope['status'] == 'not_found' and envelope['result'] is None
    state, info, counts = _running_physical_state(original)
    assert info == rig['identity']
    assert state['status'] == 'running' and state['pending_migrations'] == 0
    assert counts['ledger'] == '70' and set(counts['tables'].values()) == {'t'}
    assert counts['attempts'] == '2' and counts['claims'] == '3' and counts['outcomes'] == '0'
    snapshot = _snapshot_current(original)
    for section in ('markets', 'attempts', 'claims', 'outcomes'):
        assert snapshot['snapshot'][section] == rig['snapshot'][section]
    assert snapshot['snapshot']['ledger'][:63] == rig['snapshot']['ledger']
    assert len(snapshot['snapshot']['ledger']) == 70
    assert snapshot['instance'] == rig['identity']
    lock_file = original / 'database/migrations.lock.json'
    staged_lock = lock_file.read_bytes()
    try:
        lock_file.write_bytes((rig['old_source'] / 'database/migrations.lock.json').read_bytes())
        for name in NEW_MIGRATIONS:
            (original / 'supabase/migrations' / name).unlink()
        conflict = _current_payload(rig, 'conflict-63-70.py', _BUNDLE_PROBE_PAYLOAD, str(original))
        assert _json_stdout(conflict) == {
            'code': 'project_postgres_migration_history_conflict', 'status_after': 'stopped'}
    finally:
        lock_file.write_bytes(staged_lock)
        for name in NEW_MIGRATIONS:
            shutil.copyfile(rig['candidate'] / 'supabase/migrations' / name,
                            original / 'supabase/migrations' / name)
    assert ProjectPostgres(original).status()['status'] == 'stopped'
    print('version-switch migration: seven appended; 70/70 admitted; 63 receipts preserved')


def stop_owned_disposable_instance_preserving_failures(rig) -> None:
    home = rig['original'] / '.local' / 'postgres'
    if not home.exists():
        return
    try:
        result = ProjectPostgres(rig['original']).down()
        print('version-switch cleanup:', result['status'])
    except Exception as error:  # noqa: BLE001  (a cleanup failure must not mask the first one)
        rig['cleanup_failure'] = f'{type(error).__name__}:{error}'
        print('version-switch CLEANUP FAILURE (first failure above stays authoritative):',
              type(error).__name__)


@pytest.mark.skipif(not ENABLED, reason='explicit disposable native version-switch proof is opt-in')
def test_native_same_root_source_selection_rollback_and_refusals(tmp_path, monkeypatch):
    rig = prepare_verified_archives_and_private_root(tmp_path, monkeypatch)
    try:
        start = _phase(rig, 'old-initialize')
        initialize_and_seed_with_old_source(rig)
        _phase_done(rig, 'old-initialize', start)
        start = _phase(rig, 'lifecycle-backup')
        prove_lifecycle_and_prepare_cold_backup(rig)
        _phase_done(rig, 'lifecycle-backup', start)
        start = _phase(rig, 'current-selection')
        prove_stopped_selection_and_current_common_reads(rig)
        _phase_done(rig, 'current-selection', start)
        start = _phase(rig, 'old-rollback')
        prove_compatible_old_source_rollback(rig)
        _phase_done(rig, 'old-rollback', start)
        start = _phase(rig, 'bundle-refusals')
        prove_bundle_integrity_refusals(rig)
        _phase_done(rig, 'bundle-refusals', start)
        start = _phase(rig, 'pending-backup-refusals')
        prove_pending_catalog_and_backup_prefix_paths(rig)
        _phase_done(rig, 'pending-backup-refusals', start)
        start = _phase(rig, 'explicit-migration')
        prove_explicit_migration_and_old_catalog_refusal(rig)
        _phase_done(rig, 'explicit-migration', start)
        print('version-switch native: PASS;', ';'.join(f'{name}:{elapsed:.1f}s'
                                                       for name, elapsed in rig['phases']))
    finally:
        stop_owned_disposable_instance_preserving_failures(rig)
    if rig.get('cleanup_failure'):
        raise AssertionError('cleanup could not stop the owned disposable instance: '
                             + rig['cleanup_failure'])


# These offline probes exercise the runner/observer, not the native bind or SQL
# proof above. All process and proc boundaries here use synthetic inputs.
def _synthetic_old_seed_runner(monkeypatch, payload, *, name='seed.py', emit=None):
    import builtins
    from io import StringIO
    from types import SimpleNamespace

    source = Path('/synthetic-old-source')
    script = source / name
    stderr, stdout = StringIO(), StringIO()
    process = SimpleNamespace(argv=['runner', str(source), str(script)],
                              path=[], modules={}, stderr=stderr, stdout=stdout)

    def run_path(path, *, run_name):
        assert path == str(script) and run_name == '__main__'
        process.modules['polymarket_alpha_lab'] = SimpleNamespace(
            __file__=str(source / 'src/polymarket_alpha_lab/__init__.py'))
        payload()

    imports = {'pathlib': SimpleNamespace(Path=Path), 'sys': process,
               'runpy': SimpleNamespace(run_path=run_path)}
    namespace = dict(vars(builtins))
    namespace['__import__'] = lambda name, *args, **kwargs: imports[name]
    if emit is not None:
        namespace['print'] = emit
    error = None
    # No subprocess or filesystem resolution: preserve the real runner's
    # exception object, and model only CPython's process-exit boundary.
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'resolve', lambda self: self)
        try:
            exec(compile(_OLD_RUNNER, '<old-seed-runner>', 'exec'),
                 {'__builtins__': namespace})
        except BaseException as caught:
            error = caught
    code = error.code if isinstance(error, SystemExit) else 1
    return (subprocess.CompletedProcess([], code, stdout.getvalue().encode(),
                                        stderr.getvalue().encode()), error)


def test_old_seed_observer_preserves_first_error_context_and_traceback(monkeypatch):
    failure = RuntimeError('synthetic seed failure')
    context = OSError(98, 'PRIVATE_SENTINEL')

    def payload():
        try:
            raise context
        except OSError:
            raise failure from None

    result, caught = _synthetic_old_seed_runner(monkeypatch, payload)
    assert caught is failure and type(caught) is RuntimeError
    assert caught.__context__ is context and caught.__cause__ is None
    assert caught.__suppress_context__ is True and result.returncode == 1
    assert result.stderr == b'OLD_SEED_FIRST_FAILURE_ERRNO=98\n'
    assert result.stdout == b'' and b'PAYLOAD_SOURCE_OK' not in result.stderr
    traceback = caught.__traceback__
    frames = []
    while traceback is not None:
        frames.append(traceback.tb_frame.f_code.co_filename)
        traceback = traceback.tb_next
    assert frames.count('<old-seed-runner>') == 1  # a bare raise, not raise error


@pytest.mark.parametrize('value', [None, '98', True, 98.0])
def test_old_seed_observer_rejects_non_integer_errno(monkeypatch, value):
    context = OSError()
    context.errno = value
    failure = RuntimeError('synthetic seed failure')

    def payload():
        try:
            raise context
        except OSError:
            raise failure from None

    result, caught = _synthetic_old_seed_runner(monkeypatch, payload)
    assert caught is failure and caught.__context__ is context
    assert result.returncode == 1
    assert result.stderr == b'OLD_SEED_FIRST_FAILURE_ERRNO=ERRNO_UNAVAILABLE\n'


def test_old_seed_observer_without_oserror_context(monkeypatch):
    failure = RuntimeError('synthetic seed failure')

    def payload():
        raise failure

    result, caught = _synthetic_old_seed_runner(monkeypatch, payload)
    assert caught is failure and caught.__context__ is None
    assert result.stderr == b'OLD_SEED_FIRST_FAILURE_ERRNO=ERRNO_UNAVAILABLE\n'


@pytest.mark.parametrize('observer_failure', [RuntimeError, SystemExit, KeyboardInterrupt])
def test_old_seed_observer_print_failure_cannot_replace_seed_error(monkeypatch, observer_failure):
    failure = ValueError('synthetic seed failure')
    writes = []

    def payload():
        raise failure

    def broken_print(*args, **kwargs):
        writes.append(args)
        raise observer_failure('PRIVATE_SENTINEL')

    result, caught = _synthetic_old_seed_runner(monkeypatch, payload, emit=broken_print)
    assert caught is failure and result.returncode == 1
    assert len(writes) == 1 and result.stderr == b''


def test_old_seed_observer_errno_read_failure_cannot_replace_seed_error(monkeypatch):
    class UnreadableError(OSError):
        @property
        def errno(self):
            raise RuntimeError('PRIVATE_SENTINEL')

    context = UnreadableError()
    failure = ValueError('synthetic seed failure')

    def payload():
        try:
            raise context
        except OSError:
            raise failure from None

    result, caught = _synthetic_old_seed_runner(monkeypatch, payload)
    assert caught is failure and caught.__context__ is context
    assert result.returncode == 1 and result.stderr == b''


def test_old_seed_observer_nonseed_exception_has_no_diagnostic(monkeypatch):
    failure = RuntimeError('synthetic readback failure')

    def payload():
        raise failure

    result, caught = _synthetic_old_seed_runner(monkeypatch, payload, name='readback.py')
    assert caught is failure and result.returncode == 1 and result.stderr == b''


@pytest.mark.parametrize('exit_code', [None, 0, 7])
def test_old_seed_observer_success_and_system_exit_keep_source_marker(monkeypatch, exit_code):
    def payload():
        if exit_code is not None:
            raise SystemExit(exit_code)

    result, caught = _synthetic_old_seed_runner(monkeypatch, payload)
    assert type(caught) is SystemExit and result.returncode == (exit_code or 0)
    assert result.stderr == b'PAYLOAD_SOURCE_OK\n'


@pytest.mark.parametrize(('name', 'code', 'marker', 'observations'), [
    ('seed.py', 0, True, 0), ('seed.py', 1, False, 1),
    ('seed.py', 0, False, 1), ('seed.py', 1, True, 1),
    ('readback.py', 1, False, 0), ('readback.py', 1, True, 0),
])
def test_old_seed_parent_observes_only_completed_seed_failures(
        monkeypatch, tmp_path, name, code, marker, observations):
    rig = _Rig(python='synthetic-python', old_source=tmp_path / 'old', neutral=tmp_path)
    result = subprocess.CompletedProcess([], code, b'', b'PAYLOAD_SOURCE_OK\n' if marker else b'')
    calls = []
    monkeypatch.setitem(globals(), '_payload_file', lambda *args: tmp_path / name)

    def run(*args, **kwargs):
        calls.append('child-completed')
        return result

    def observe(actual):
        assert actual is rig
        calls.append('observer')

    monkeypatch.setitem(globals(), '_run', run)
    monkeypatch.setitem(globals(), '_observe_old_seed_exit', observe)
    if marker:
        assert _old_payload(rig, name, 'synthetic') is result
    else:
        with pytest.raises(AssertionError):
            _old_payload(rig, name, 'synthetic')
    assert calls == ['child-completed'] + ['observer'] * observations


def test_old_seed_parent_run_exception_is_unchanged_without_observer(monkeypatch, tmp_path):
    failure = subprocess.TimeoutExpired('synthetic', 1)
    rig = _Rig(python='synthetic-python', old_source=tmp_path / 'old', neutral=tmp_path)
    monkeypatch.setitem(globals(), '_payload_file', lambda *args: tmp_path / 'seed.py')
    observations = []

    def run(*args, **kwargs):
        raise failure

    monkeypatch.setitem(globals(), '_run', run)
    monkeypatch.setitem(globals(), '_observe_old_seed_exit', lambda rig: observations.append(rig))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _old_payload(rig, 'seed.py', 'synthetic')
    assert caught.value is failure and observations == []


@pytest.mark.parametrize('observer_failure', [RuntimeError, SystemExit, KeyboardInterrupt])
def test_old_seed_parent_observer_failure_preserves_source_assertion(
        monkeypatch, tmp_path, observer_failure):
    rig = _Rig(python='synthetic-python', old_source=tmp_path / 'old', neutral=tmp_path)
    monkeypatch.setitem(globals(), '_payload_file', lambda *args: tmp_path / 'seed.py')
    monkeypatch.setitem(globals(), '_run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 1, b'', b''))
    observations = []

    def observe(rig):
        observations.append(rig)
        raise observer_failure('PRIVATE_SENTINEL')

    monkeypatch.setitem(globals(), '_observe_old_seed_exit', observe)
    with pytest.raises(AssertionError):
        _old_payload(rig, 'seed.py', 'synthetic')
    assert observations == [rig]


def _synthetic_seed_proc(monkeypatch, tmp_path, tcp, tcp6, *, port=40000):
    reads = []

    class Stream(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 65537
            reads.append(size)
            return super().read(size)

    def opened(path, mode):
        assert mode == 'rb'
        assert path.as_posix() in ('/proc/net/tcp', '/proc/net/tcp6')
        value = tcp if path.name == 'tcp' else tcp6
        if isinstance(value, BaseException):
            raise value
        return Stream(value)

    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(Path, 'resolve', lambda self, **kwargs: self)
    return _Rig(base=tmp_path, original=tmp_path / 'Original Project', port=port), reads


def test_old_seed_proc_observer_counts_only_local_and_peer_task_port(monkeypatch, tmp_path, capsys):
    tcp = (b'sl local_address rem_address st\n'
           b'0: 0100007F:9C40 00000000:0000 0A PRIVATE_SENTINEL\n'
           b'1: 0100007F:9C40 0100007F:9C41 06 PRIVATE_SENTINEL\n'
           b'2: 0100007F:9C42 0100007F:9C40 01 PRIVATE_SENTINEL\n'
           b'3: 0100007F:9C40 0100007F:9C40 06 PRIVATE_SENTINEL\n'
           b'4: 0100007F:9C41 0100007F:9C42 06 PRIVATE_SENTINEL\n')
    tcp6 = (b'sl local_address remote_address st\n'
            + b'0: ' + b'0' * 32 + b':9C41 ' + b'0' * 32 + b':9C40 06 PRIVATE_SENTINEL\n')
    with monkeypatch.context() as patch:
        rig, reads = _synthetic_seed_proc(patch, tmp_path, tcp, tcp6)
        _observe_old_seed_exit(rig)
    out, err = capsys.readouterr()
    assert err == '' and reads == [65537, 65537]
    assert out == ('OLD_SEED_AFTER_CHILD_EXIT OWNER_UNKNOWN '
                   'tcp={"local":{"LISTEN":1,"TIME_WAIT":2},'
                   '"peer":{"ESTABLISHED":1,"TIME_WAIT":1}} '
                   'tcp6={"local":{},"peer":{"TIME_WAIT":1}}\n')
    assert all(value not in out for value in ('PRIVATE_SENTINEL', '0100007F', '9C40', '9C41', '9C42'))


@pytest.mark.parametrize('unavailable', [
    PermissionError('PRIVATE_SENTINEL'), FileNotFoundError('PRIVATE_SENTINEL'),
    b'x' * 65537, b'sl local_address rem_address st\n0: 0100007F:9C40',
    b'', b'wrong header\n', b'sl local_address rem_address st\nmalformed\n',
    b'sl local_address rem_address st\n0: 0100007F:9C40 0100007F:9C41 FF\n',
    b'sl local_address rem_address st\n0: PRIVATE_SENTINEL 0100007F:9C40 06\n',
], ids=['permission', 'missing', 'over-limit', 'partial-row', 'empty',
        'header', 'malformed-row', 'unknown-state', 'malformed-endpoint'])
def test_old_seed_proc_observer_unavailable_is_not_zero(monkeypatch, tmp_path, capsys, unavailable):
    with monkeypatch.context() as patch:
        rig, _ = _synthetic_seed_proc(patch, tmp_path, unavailable,
                                     b'sl local_address remote_address st\n')
        _observe_old_seed_exit(rig)
    out = capsys.readouterr().out
    assert out == ('OLD_SEED_AFTER_CHILD_EXIT OWNER_UNKNOWN tcp=UNAVAILABLE '
                   'tcp6={"local":{},"peer":{}}\n')


@pytest.mark.parametrize('port', [55432, 0, 65536, None, True, '40000'])
def test_old_seed_proc_observer_refuses_non_task_port_without_reads(monkeypatch, tmp_path, capsys, port):
    with monkeypatch.context() as patch:
        rig, reads = _synthetic_seed_proc(patch, tmp_path, AssertionError('unexpected read'),
                                         AssertionError('unexpected read'), port=port)
        _observe_old_seed_exit(rig)
    assert reads == []
    assert capsys.readouterr().out == (
        'OLD_SEED_AFTER_CHILD_EXIT OWNER_UNKNOWN tcp=UNAVAILABLE tcp6=UNAVAILABLE\n')


def test_old_seed_proc_observer_refuses_root_outside_fixture(monkeypatch, tmp_path, capsys):
    with monkeypatch.context() as patch:
        rig, reads = _synthetic_seed_proc(patch, tmp_path, AssertionError('unexpected read'),
                                         AssertionError('unexpected read'))
        rig['original'] = tmp_path.parent / 'another-root'
        _observe_old_seed_exit(rig)
    assert reads == [] and 'tcp=UNAVAILABLE tcp6=UNAVAILABLE' in capsys.readouterr().out


# ----------------------------------------------------------------------------
# R03-diag2: failure-moment diagnostics for the OLD seed payload (offline).
#
# The probes below exercise only the diagnostics constant and the payload's
# failure wrapper with synthetic inputs: no native bind, no SQL, no PG.
# They mirror the observer suites above (fake modules, fake /proc rows and
# PRIVATE_SENTINEL leakage checks) and never weaken the native proof.
# ----------------------------------------------------------------------------


def _seed_diag_exec():
    namespace = {}
    exec(compile(_SEED_FAILURE_DIAGNOSTICS, '<seed-diagnostics>', 'exec'),
         namespace)
    return namespace


def _seed_diag_raiser(filename, source, error_name):
    namespace = {}
    exec(compile(source, filename, 'exec'), namespace)
    try:
        namespace['_seed_raise_target']()
    except BaseException as error:
        assert type(error).__name__ == error_name
        return error
    raise AssertionError('synthetic source did not raise')


def _seed_diag_oserror(filename):
    return _seed_diag_raiser(filename,
                             'def _seed_raise_target():\n'
                             '    raise OSError(98, "PRIVATE_SENTINEL")\n',
                             'OSError')


def _seed_diag_blocked(filename, code):
    """_FakeProjectDatabaseError(code) raised from None over a caught
    OSError whose decisive frames are attributed to `filename`."""
    context = _seed_diag_oserror(filename)
    try:
        raise context
    except OSError:
        try:
            raise _FakeProjectDatabaseError(code) from None
        except _FakeProjectDatabaseError as error:
            return error


def _seed_diag_marker(stderr):
    lines = [line for line in stderr.splitlines()
             if line.startswith('OLD_SEED_FAILURE_MOMENT')]
    assert len(lines) == 1, stderr
    return lines[0]


def test_seed_diag_emits_chain_with_from_none_semantics(monkeypatch, tmp_path):
    import io
    monkeypatch.syspath_prepend(str(tmp_path))
    server = str(tmp_path / 'src/polymarket_alpha_lab'
                 '/project_postgres/server.py')
    failure = _seed_diag_blocked(server, 'project_postgres_port_in_use')
    assert failure.__suppress_context__ is True
    namespace = _seed_diag_exec()
    err = io.StringIO()
    namespace['_seed_failure_emit'](failure, 40000, stream=err)
    line = _seed_diag_marker(err.getvalue())
    assert line.startswith('OLD_SEED_FAILURE_MOMENT '
                           'code=project_postgres_port_in_use errno=null')
    chain = json.loads(line.split(' chain=', 1)[1].split(' tcp=', 1)[0])
    assert chain[0]['via'] == 'raised'
    assert chain[0]['suppress_context'] is True
    assert chain[0]['errno'] is None
    assert chain[0]['frames'][0][0] == '<outside>'
    assert chain[1]['via'] == 'context'
    assert chain[1]['errno'] == 98
    assert ['src/polymarket_alpha_lab/project_postgres/server.py',
            '_seed_raise_target'] in [frame[:2]
                                      for frame in chain[1]['frames']]
    assert 'PRIVATE_SENTINEL' not in line and '0100007F' not in line


@pytest.mark.parametrize('value', [None, '98', True, 98.0])
def test_seed_diag_rejects_non_integer_errno(monkeypatch, tmp_path, value):
    import io
    monkeypatch.syspath_prepend(str(tmp_path))
    error = OSError()
    error.errno = value
    namespace = _seed_diag_exec()
    err = io.StringIO()
    namespace['_seed_failure_emit'](error, 40000, stream=err)
    line = _seed_diag_marker(err.getvalue())
    assert ' errno=null' in line and 'errno=98' not in line
    assert 'PRIVATE_SENTINEL' not in line


def _seed_diag_proc_open(monkeypatch, tcp, tcp6):
    import builtins
    import io
    real_open = builtins.open

    def opened(file, mode='r', *args, **kwargs):
        if file == '/proc/net/tcp':
            return io.BytesIO(tcp)
        if file == '/proc/net/tcp6':
            return io.BytesIO(tcp6)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', opened)


@pytest.mark.parametrize('content', [
    b'x' * 65537,
    b'sl local_address rem_address st\n0: 0100007F:9C40',
    b'',
    b'wrong header\n',
    b'sl local_address rem_address st\n'
    b'0: 0100007F:9C40 0100007F:9C41 FF PRIVATE_SENTINEL\n',
    b'sl local_address rem_address st\n'
    b'0: PRIVATE_SENTINEL 0100007F:9C40 06\n',
], ids=['over-limit', 'partial-row', 'empty', 'header', 'unknown-state',
        'malformed-endpoint'])
def test_seed_diag_tcp_unavailable_is_never_zero(monkeypatch, content):
    namespace = _seed_diag_exec()
    _seed_diag_proc_open(monkeypatch, content,
                         b'sl local_address remote_address st\n')
    assert namespace['_seed_diag_tcp']('tcp', 40000) == 'UNAVAILABLE'
    assert namespace['_seed_diag_tcp']('tcp6', 40000) == {'local': {},
                                                          'peer': {}}


def test_seed_diag_tcp_open_error_is_unavailable(monkeypatch):
    import builtins
    namespace = _seed_diag_exec()
    real_open = builtins.open

    def opened(file, *args, **kwargs):
        if str(file).startswith('/proc/net/'):
            raise PermissionError('PRIVATE_SENTINEL')
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', opened)
    assert namespace['_seed_diag_tcp']('tcp', 40000) == 'UNAVAILABLE'


def test_seed_diag_tcp_counts_only_task_port(monkeypatch):
    namespace = _seed_diag_exec()
    tcp = (b'sl local_address rem_address st\n'
           b'0: 0100007F:9C40 00000000:0000 0A PRIVATE_SENTINEL\n'
           b'1: 0100007F:9C40 0100007F:9C41 06 PRIVATE_SENTINEL\n'
           b'2: 0100007F:9C42 0100007F:9C40 01 PRIVATE_SENTINEL\n')
    _seed_diag_proc_open(monkeypatch, tcp,
                         b'sl local_address remote_address st\n')
    assert namespace['_seed_diag_tcp']('tcp', 40000) == {
        'local': {'LISTEN': 1, 'TIME_WAIT': 1},
        'peer': {'ESTABLISHED': 1}}
    assert namespace['_seed_diag_tcp']('tcp', 40003) == {'local': {},
                                                         'peer': {}}


def test_seed_diag_chain_terminates_on_cycles():
    namespace = _seed_diag_exec()
    first = OSError(98, 'PRIVATE_SENTINEL')
    second = OSError(99, 'PRIVATE_SENTINEL')
    first.__context__ = second
    second.__context__ = first
    chain = namespace['_seed_diag_chain'](first)
    assert len(chain) == 2
    assert chain[0]['via'] == 'raised' and chain[1]['via'] == 'context'


def test_seed_diag_emit_failure_cannot_replace_seed_error(
        monkeypatch, tmp_path):
    import io
    monkeypatch.syspath_prepend(str(tmp_path))
    namespace = _seed_diag_exec()

    def broken(*args, **kwargs):
        raise RuntimeError('PRIVATE_SENTINEL')

    namespace['_seed_diag_chain'] = broken
    error = OSError(98, 'PRIVATE_SENTINEL')
    err = io.StringIO()
    assert namespace['_seed_failure_emit'](error, 40000, stream=err) is None
    assert err.getvalue() == ''


class _SeedValue:
    def __init__(self, value):
        self._value = value

    def to_dict(self):
        return self._value


class _FakeProjectDatabaseError(RuntimeError):
    pass


class _FakeSeedSession:
    def __init__(self, enter_error):
        self._enter_error = enter_error

    def __enter__(self):
        if self._enter_error is not None:
            raise self._enter_error
        return self

    def __exit__(self, *exc_info):
        return False

    def run_research(self, *, request, model_factory):
        return _SeedValue({'status': 'captured',
                           'research_status': 'completed'})

    def _call(self, operation, **kwargs):
        return operation(**kwargs)

    def inspect(self, *, record_id):
        return _SeedValue({'status': 'inspection'})

    def evaluate(self):
        return None


class _FakeProjectPostgres:
    enter_error = None

    def __init__(self, root):
        self._root = root

    def session(self):
        return _FakeSeedSession(_FakeProjectPostgres.enter_error)

    def status(self):
        return {'status': 'stopped'}


def _seed_claim(*, request):
    return True, _SeedValue({'status': 'incomplete'})


def _seed_fake_module(name):
    import types
    return types.ModuleType(name)


def _synthetic_seed_payload(monkeypatch, tmp_path, *, enter_error=None,
                            tcp=b'sl local_address rem_address st\n',
                            tcp6=b'sl local_address remote_address st\n',
                            port='40000'):
    """Exec the REAL _SEED_PAYLOAD against fake package modules and fake
    /proc rows; returns (stdout, stderr, propagated failure or None)."""
    import contextlib
    import io
    root = tmp_path / 'Original Project'
    package = _seed_fake_module('polymarket_alpha_lab')
    execution = _seed_fake_module(
        'polymarket_alpha_lab.research_execution_psycopg')
    execution._claim = _seed_claim
    project_postgres = _seed_fake_module(
        'polymarket_alpha_lab.project_postgres')
    server = _seed_fake_module(
        'polymarket_alpha_lab.project_postgres.server')
    files = _seed_fake_module('polymarket_alpha_lab.project_postgres.files')
    files.ProjectDatabaseError = _FakeProjectDatabaseError
    server.ProjectPostgres = _FakeProjectPostgres
    execution_root = _seed_fake_module(
        'polymarket_alpha_lab.research_execution')
    execution_root.CapturedResearchRequest = type(
        'CapturedResearchRequest', (),
        {'__init__': lambda self, *args, **kwargs: None})
    agent_types = _seed_fake_module(
        'polymarket_alpha_lab.team_research_agent_types')
    for name in ('ResearchEvidence', 'ResearchModelReply',
                 'ResearchToolCall'):
        setattr(agent_types, name, type(name, (),
            {'__init__': lambda self, *args, **kwargs: None}))
    intake = _seed_fake_module('polymarket_alpha_lab.team_research_intake')
    intake.GammaMarketSnapshot = type('GammaMarketSnapshot', (),
        {'__init__': lambda self, *args, **kwargs: None})
    intake.prepare_team_research_from_gamma = lambda *args, **kwargs: object()
    package.research_execution_psycopg = execution
    package.research_execution = execution_root
    package.project_postgres = project_postgres
    package.team_research_agent_types = agent_types
    package.team_research_intake = intake
    for name, module in (
        ('polymarket_alpha_lab', package),
        ('polymarket_alpha_lab.research_execution_psycopg', execution),
        ('polymarket_alpha_lab.research_execution', execution_root),
        ('polymarket_alpha_lab.project_postgres', project_postgres),
        ('polymarket_alpha_lab.project_postgres.server', server),
        ('polymarket_alpha_lab.project_postgres.files', files),
        ('polymarket_alpha_lab.team_research_agent_types', agent_types),
        ('polymarket_alpha_lab.team_research_intake', intake),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    _FakeProjectPostgres.enter_error = enter_error
    _seed_diag_proc_open(monkeypatch, tcp, tcp6)
    out, err = io.StringIO(), io.StringIO()
    failure = None
    previous = sys.argv
    sys.argv = ['seed.py', str(root), port]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(_SEED_PAYLOAD, '<seed-payload>', 'exec'), {})
    except BaseException as error:  # noqa: BLE001 (preserve as raised)
        failure = error
    finally:
        sys.argv = previous
        _FakeProjectPostgres.enter_error = None
    return out.getvalue(), err.getvalue(), failure


def test_seed_payload_failure_moment_line_and_reraise(monkeypatch, tmp_path):
    source = tmp_path / 'old-source'
    monkeypatch.syspath_prepend(str(source))
    server = str(source / 'src/polymarket_alpha_lab'
                 '/project_postgres/server.py')
    enter_error = _seed_diag_blocked(server, 'project_postgres_port_in_use')
    tcp = (b'sl local_address rem_address st\n'
           b'0: 0100007F:9C40 0100007F:9C41 06 PRIVATE_SENTINEL\n')
    out, err, failure = _synthetic_seed_payload(
        monkeypatch, tmp_path, enter_error=enter_error, tcp=tcp,
        port='40000')
    assert isinstance(failure, _FakeProjectDatabaseError)
    assert str(failure) == 'project_postgres_port_in_use'
    assert out == ''
    line = _seed_diag_marker(err)
    assert 'code=project_postgres_port_in_use' in line
    assert 'tcp={"local":{"TIME_WAIT":1},"peer":{}}' in line
    assert 'tcp6={"local":{},"peer":{}}' in line
    chain = json.loads(line.split(' chain=', 1)[1].split(' tcp=', 1)[0])
    assert chain[1]['via'] == 'context' and chain[1]['errno'] == 98
    assert 'src/polymarket_alpha_lab/project_postgres/server.py' in [
        frame[0] for frame in chain[1]['frames']]
    assert '<seed-payload>' in [frame[0] for frame in chain[0]['frames']]
    frames = []
    traceback = failure.__traceback__
    while traceback is not None:
        frames.append(traceback.tb_frame.f_code.co_filename)
        traceback = traceback.tb_next
    assert frames.count('<seed-payload>') == 1  # bare raise kept it intact
    assert 'PRIVATE_SENTINEL' not in line and '0100007F' not in line


def test_seed_payload_success_has_no_failure_output(monkeypatch, tmp_path):
    out, err, failure = _synthetic_seed_payload(monkeypatch, tmp_path)
    assert failure is None
    assert 'OLD_SEED_FAILURE_MOMENT' not in err
    payload = json.loads(out)
    assert payload['status_after'] == 'stopped'
    assert payload['outstanding_owned'] is True
    assert payload['evaluation_refusal'] == 'none'
    assert set(payload['inspections']) == {'vs-record-1', 'vs-record-2',
                                           'vs-record-3'}


@pytest.mark.parametrize('port', ['55432', 'abc', '70000', '0', ''])
def test_seed_payload_invalid_port_marks_port_unavailable(
        monkeypatch, tmp_path, port):
    source = tmp_path / 'old-source'
    monkeypatch.syspath_prepend(str(source))
    server = str(source / 'src/polymarket_alpha_lab'
                 '/project_postgres/server.py')
    enter_error = _seed_diag_blocked(server, 'project_postgres_port_in_use')
    _, err, failure = _synthetic_seed_payload(
        monkeypatch, tmp_path, enter_error=enter_error, port=port)
    assert isinstance(failure, _FakeProjectDatabaseError)
    line = _seed_diag_marker(err)
    assert 'tcp=PORT_UNAVAILABLE' in line and 'tcp6=PORT_UNAVAILABLE' in line


# ----------------------------------------------------------------------------
# R03-fix: bounded pre-seed TIME_WAIT readiness wait, verbatim failure echo
# (offline probes; no native bind, no SQL, no PG).
# ----------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


def _seed_wait_clean():
    return b'sl local_address rem_address st\n'


def _seed_wait_clean6():
    return b'sl local_address remote_address st\n'


def _seed_wait_busy(count):
    rows = ['sl local_address rem_address st']
    for index in range(count):
        rows.append('0: 0100007F:9C40 0100007F:9C4' + str(index % 10)
                    + ' 06 PRIVATE_SENTINEL')
    return ('\n'.join(rows) + '\n').encode('ascii')


def _seed_wait_proc(monkeypatch, script):
    """Serve scripted /proc/net/tcp{,6} contents to Path.open in order."""
    import io
    served = iter(script)
    real_open = Path.open

    def opened(path, mode='rb', *args, **kwargs):
        text = str(path)
        if text.startswith('/proc/net/tcp'):
            suffix, content = next(served)
            assert text == '/proc/net/' + suffix, text
            return io.BytesIO(content)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', opened)


def _seed_wait_rig():
    return _Rig(port=40000)


def test_seed_wait_zero_proceeds_immediately(monkeypatch, capsys):
    _seed_wait_proc(monkeypatch, [('tcp', _seed_wait_clean()),
                                  ('tcp6', _seed_wait_clean6())])
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    _wait_for_seed_port_ready(_seed_wait_rig(), _clock=_FakeClock([0.0, 0.0]),
                              _sleep=fake_sleep)
    assert sleeps == []
    out = capsys.readouterr().out
    assert 'OLD_SEED_WAIT_BEFORE port=40000' in out
    assert 'OLD_SEED_WAIT_AFTER port=40000' in out
    assert 'elapsed_s=0.000' in out
    assert 'tcp={"local":{},"peer":{}}' in out


def test_seed_wait_clears_after_polling(monkeypatch, capsys):
    _seed_wait_proc(monkeypatch, [
        ('tcp', _seed_wait_busy(1)), ('tcp6', _seed_wait_clean6()),
        ('tcp', _seed_wait_busy(1)), ('tcp6', _seed_wait_clean6()),
        ('tcp', _seed_wait_clean()), ('tcp6', _seed_wait_clean6()),
    ])
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    _wait_for_seed_port_ready(
        _seed_wait_rig(), _clock=_FakeClock([0.0, 1.0, 2.0, 3.0]),
        _sleep=fake_sleep)
    assert sleeps == [1.0, 1.0]
    out = capsys.readouterr().out
    assert out.count('OLD_SEED_WAIT_BEFORE') == 1
    assert 'elapsed_s=3.000' in out
    assert 'tcp={"local":{"TIME_WAIT":1},"peer":{"TIME_WAIT":1}}' in out
    assert 'PRIVATE_SENTINEL' not in out and '0100007F' not in out


def test_seed_wait_timeout_fails_with_diagnostics(monkeypatch, capsys):
    _seed_wait_proc(monkeypatch, [
        ('tcp', _seed_wait_busy(3)), ('tcp6', _seed_wait_clean6()),
    ])
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    with pytest.raises(pytest.fail.Exception, match='OLD_SEED_WAIT_TIMEOUT'):
        _wait_for_seed_port_ready(
            _seed_wait_rig(), _clock=_FakeClock([0.0, 100.0, 100.0]),
            _sleep=fake_sleep)
    assert sleeps == []
    out = capsys.readouterr().out
    assert 'OLD_SEED_WAIT_BEFORE' in out and 'OLD_SEED_WAIT_AFTER' not in out


def test_seed_wait_unavailable_initial_fails_never_zero(monkeypatch, capsys):
    _seed_wait_proc(monkeypatch, [('tcp', b'garbage\n'),
                                  ('tcp6', _seed_wait_clean6())])
    with pytest.raises(pytest.fail.Exception,
                       match='OLD_SEED_WAIT_UNAVAILABLE'):
        _wait_for_seed_port_ready(
            _seed_wait_rig(), _clock=_FakeClock([0.0, 0.0]),
            _sleep=lambda seconds: None)
    out = capsys.readouterr().out
    assert 'tcp=UNAVAILABLE' in out


def test_seed_wait_unavailable_mid_poll_fails(monkeypatch, capsys):
    _seed_wait_proc(monkeypatch, [
        ('tcp', _seed_wait_busy(1)), ('tcp6', _seed_wait_clean6()),
        ('tcp', b'partial'), ('tcp6', _seed_wait_clean6()),
    ])
    with pytest.raises(pytest.fail.Exception,
                       match='OLD_SEED_WAIT_UNAVAILABLE'):
        _wait_for_seed_port_ready(
            _seed_wait_rig(), _clock=_FakeClock([0.0, 1.0, 1.0]),
            _sleep=lambda seconds: None)


def test_echo_seed_child_stderr_verbatim_on_failure(capsys):
    import subprocess
    result = subprocess.CompletedProcess(
        [], 1, b'', b'OLD_SEED_FIRST_FAILURE_ERRNO=98\nsynthetic traceback')
    _echo_seed_child_stderr(result)
    out = capsys.readouterr().out
    assert 'OLD_SEED_CHILD_STDERR_BEGIN\n' in out
    assert 'OLD_SEED_CHILD_STDERR_END\n' in out
    segment = out.split('OLD_SEED_CHILD_STDERR_BEGIN\n', 1)[1]
    segment = segment.split('\nOLD_SEED_CHILD_STDERR_END', 1)[0]
    assert segment == 'OLD_SEED_FIRST_FAILURE_ERRNO=98\nsynthetic traceback'


def test_old_payload_echoes_before_provenance_assert(monkeypatch, tmp_path,
                                                     capsys):
    import subprocess
    calls = []

    def fake_payload_file(rig, name, text):
        return tmp_path / name

    def fake_run(args, cwd, timeout=900):
        calls.append('run')
        return subprocess.CompletedProcess(
            args, 1, b'',
            b'OLD_SEED_FIRST_FAILURE_ERRNO=98\nrunner traceback detail')

    def fake_observe(rig):
        calls.append('observe')

    monkeypatch.setattr(sys.modules[__name__], '_payload_file',
                        fake_payload_file)
    monkeypatch.setattr(sys.modules[__name__], '_run', fake_run)
    monkeypatch.setattr(sys.modules[__name__], '_observe_old_seed_exit',
                        fake_observe)
    rig = _Rig(python='python3', old_source=tmp_path, payloads=tmp_path,
               neutral=tmp_path, base=tmp_path, original=tmp_path,
               port=40000)
    with pytest.raises(AssertionError):
        _old_payload(rig, 'seed.py', _SEED_PAYLOAD, str(tmp_path), '40000')
    out = capsys.readouterr().out
    assert out.index('OLD_SEED_CHILD_STDERR_BEGIN') < out.index(
        'OLD_SEED_CHILD_STDERR_END')
    segment = out.split('OLD_SEED_CHILD_STDERR_BEGIN\n', 1)[1]
    segment = segment.split('\nOLD_SEED_CHILD_STDERR_END', 1)[0]
    assert segment == 'OLD_SEED_FIRST_FAILURE_ERRNO=98\nrunner traceback detail'
    assert calls == ['run', 'observe']


def test_old_payload_success_has_no_echo(monkeypatch, tmp_path, capsys):
    import subprocess

    def fake_payload_file(rig, name, text):
        return tmp_path / name

    def fake_run(args, cwd, timeout=900):
        return subprocess.CompletedProcess(
            args, 0, b'{"status": "ok"}', b'PAYLOAD_SOURCE_OK\n')

    monkeypatch.setattr(sys.modules[__name__], '_payload_file',
                        fake_payload_file)
    monkeypatch.setattr(sys.modules[__name__], '_run', fake_run)
    rig = _Rig(python='python3', old_source=tmp_path, payloads=tmp_path,
               neutral=tmp_path, base=tmp_path, original=tmp_path,
               port=40000)
    result = _old_payload(rig, 'seed.py', _SEED_PAYLOAD, str(tmp_path),
                          '40000')
    assert result.returncode == 0
    out = capsys.readouterr().out
    assert 'OLD_SEED_CHILD_STDERR_BEGIN' not in out


def test_seed_diag_frames_keeps_innermost_eight(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(tmp_path))
    server = str(tmp_path / 'src/polymarket_alpha_lab'
                 '/project_postgres/server.py')
    source = ('def _seed_raise_target(depth):\n'
              '    if depth <= 0:\n'
              '        raise OSError(98, "PRIVATE_SENTINEL")\n'
              '    return _seed_raise_target(depth - 1)\n')
    namespace = {}
    exec(compile(source, server, 'exec'), namespace)
    try:
        namespace['_seed_raise_target'](11)
    except OSError as error:
        failure = error
    else:
        raise AssertionError('deep synthetic chain did not raise')
    depth = 0
    traceback = failure.__traceback__
    while traceback is not None:
        depth += 1
        traceback = traceback.tb_next
    assert depth == 13  # 12 invocation frames plus this test frame
    diag = _seed_diag_exec()
    frames = diag['_seed_diag_frames'](failure.__traceback__, tmp_path)
    assert len(frames) == 8
    assert frames[0][1] == '_seed_raise_target' and frames[0][2] == 3
    assert frames[1][2] == 4 and frames[7][2] == 4
    assert all(frame[0] == 'src/polymarket_alpha_lab/project_postgres'
               '/server.py' for frame in frames)
