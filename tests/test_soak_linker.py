"""N3 synthetic tests for the RV-05 single-shot auto-link control
(PAL_RV05_CLOSURE_20260922, node N3): tests/support/soak_linker.py.

13 required scenarios (plan section 6), each against a fully SYNTHETIC
environment built in pytest tmp_path - no real campaign, no real PID from
the original soak (PIDs 62420/20372/98500 are never referenced), no real
launch package, no STOP file for any campaign, no network, no business
roots. The "original alive" scenarios simulate a live original with a
synthetic campaign + lock record whose pid/wall identity matches the
TEST PROCESS ITSELF (read-only liveness/creation-time probes only).

Additional legal controls: binding-manifest validation (placeholder /
unknown schema / unknown-missing-duplicate fields / wrong types / time-
limit relaxation rejected; the exact legal binding arms and records the
first ARMED_WAITING receipt with PID, creation identity, command,
config digest and cancel method).

The synthetic clock hook (--now-utc) drives window/clock scenarios; the
implementation default poll interval stays >= 300 s (binding-enforced).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS_DIR = Path(__file__).resolve().parent
LINKER = TESTS_DIR / 'support' / 'soak_linker.py'
PY = sys.executable

BEFORE = '2026-09-23T00:00:00Z'          # before the approved window
IN1 = '2026-09-24T03:00:00Z'             # inside the approved window
IN2 = '2026-09-24T05:00:00Z'             # later inside the window
JUMP_BACK = '2026-09-24T02:30:00Z'       # earlier than IN1 (backward jump)
AFTER = '2026-09-24T13:00:00Z'           # past the window latest
PAST_DEADLINE = '2026-09-28T01:00:00Z'   # past the total deadline


def _epoch(text: str) -> float:
    return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def _load_linker_module():
    spec = importlib.util.spec_from_file_location(
        'soak_linker_under_test', LINKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_LINKER_MOD = _load_linker_module()


def sha256_file(path) -> str:
    digest = sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 16), b''):
            digest.update(block)
    return digest.hexdigest()


def run_linker(args, timeout=180):
    proc = subprocess.run(
        [PY, '-I', '-S', '-B', str(LINKER), *args],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


FAKE_PREFLIGHT = '''\
import datetime as dt
import hashlib, json, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
out = None
spec = None
execution_id = None
for i, a in enumerate(args):
    if a == '--json-out' and i + 1 < len(args):
        out = args[i + 1]
    elif a == '--spec' and i + 1 < len(args):
        spec = args[i + 1]
    elif a == '--execution-id' and i + 1 < len(args):
        execution_id = args[i + 1]
# N4/N0 reconciliation (fix wave N2 closed child env): behavior comes from
# a JSON config NEXT TO THIS SCRIPT, written by the test before wait -
# never from environment variables. Formal preflight/launch children run
# with a closed env and must not be steerable by ambient test knobs.
cfg = {}
cfg_path = Path(__file__).resolve().parent / 'preflight-behavior.json'
if cfg_path.is_file():
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
sleep = float(cfg.get('sleep', 0) or 0)
if sleep:
    time.sleep(sleep)
# deterministic mid-preflight injections: each completes strictly before
# this stub exits, so the blocked parent never races them.
cancel = cfg.get('cancel') or {}
if cancel:
    r = subprocess.run([sys.executable, '-I', '-S', '-B',
                        cancel['linker'], 'cancel', '--binding',
                        cancel['binding'], '--now-utc', cancel['now']],
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    Path(cancel['log']).write_text(
        'rc=%d\\n%s\\n%s\\n' % (r.returncode, r.stdout, r.stderr),
        encoding='utf-8')
mutate = cfg.get('mutate_pin') or ''
if mutate:
    Path(mutate).write_text('MUTATED DURING PREFLIGHT\\n', encoding='utf-8')
verdict = cfg.get('verdict', 'GO')
unmet = list(cfg.get('unmet') or [])
if cfg.get('checks', 'full') == 'empty':
    checks = []
else:
    checks = [{'id': i, 'status': 'PASS'} for i in (
        'capacity_reachable', 'windows_kit_present', 'frozen_tree_integrity',
        'resources_available', 'original_driver_exited',
        'original_segment_close_present', 'original_evidence_stable',
        'original_children_cleaned', 'launch_window')]


def _sha(path):
    if path and Path(path).is_file():
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return None


now_utc = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
report = {'schema': 'pal-rv05-preflight-v1', 'overall': verdict,
          'unmet_count': len(unmet), 'unmet_ids': unmet, 'checks': checks}
# execution provenance block (N2 fresh-report contract): bound to THIS
# invocation (the linker passes --execution-id and re-verifies the echo,
# the spec digest, the argv echo and the generation time). The 'execution'
# config key lets the negative vectors forge one specific field.
xo = cfg.get('execution') or {}
report['execution'] = {
    'execution_id': xo.get('execution_id', execution_id),
    'spec': xo.get('spec', spec),
    'spec_sha256': xo.get('spec_sha256', _sha(spec)),
    'json_out': out,
    'argv': list(args),
    'invoked_at_utc': now_utc}
report['generated_at_utc'] = xo.get('generated_at_utc', now_utc)
if xo.get('omit_block'):
    del report['execution']
text = cfg.get('raw_report')
if text is None:
    text = json.dumps(report, indent=2)
rc = int(cfg.get('rc', 0) or 0)
skip_write = bool(cfg.get('skip_write'))
if out and not skip_write and not rc:
    Path(out).write_text(text + '\\n', encoding='utf-8')
print(text)
sys.exit(rc if rc else (0 if verdict == 'GO' else 1))
'''

# N4/N0 reconciliation (fix wave N2 L5/L6 structured argv whitelist): the
# bound launch_argv must use the PowerShell grammar, so the synthetic
# launch stand-in is a real .ps1 executed by the host's canonical
# System32 WindowsPowerShell. It reads launch-behavior.json NEXT TO ITSELF
# (counter / rc / mode / campaign), writes campaign.json BOM-LESS via
# .NET WriteAllText (the linker's strict record reader rejects a UTF-8
# BOM), and never needs an environment knob.
SYSTEM32_POWERSHELL = os.path.join(
    os.environ.get('SystemRoot') or r'C:\Windows',
    r'System32\WindowsPowerShell\v1.0\powershell.exe')

SYNTHETIC_LAUNCHER_PS1 = '''\
param([string]$SpecPath)
$ErrorActionPreference = 'Stop'
# synthetic launch stand-in (pinned by sha256 in the test binding; the
# behavior file is test-owned and NOT part of the pin)
$cfgPath = Join-Path $PSScriptRoot 'launch-behavior.json'
$cfg = @{}
if (Test-Path -LiteralPath $cfgPath -PathType Leaf) {
    $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
}
$campaign = [string]$cfg.campaign
if (-not $campaign -and $SpecPath -and
    (Test-Path -LiteralPath $SpecPath -PathType Leaf)) {
    $spec = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if ($spec.PSObject.Properties['paths'] -and
        $spec.paths.PSObject.Properties['planned_campaign_dir']) {
        $campaign = [string]$spec.paths.planned_campaign_dir
    }
}
$mode = [string]$cfg.mode
if ('' -ne $campaign -and $mode -ne 'skip') {
    New-Item -ItemType Directory -Force -Path $campaign | Out-Null
    $schema = if ($mode -eq 'badschema') {
        'not-the-bound-campaign-schema'
    } else {
        'pal-soak-campaign-v1'
    }
    $doc = @{ schema = $schema; synthetic = $true } | ConvertTo-Json
    [System.IO.File]::WriteAllText(
        (Join-Path $campaign 'campaign.json'), $doc)
}
$counter = [string]$cfg.counter
if ('' -ne $counter) {
    $line = 'launch {0} pid={1}' -f `
        ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()), $PID
    [System.IO.File]::AppendAllText($counter, $line +
        [System.Environment]::NewLine)
}
exit ([int]$cfg.rc)
'''


class Synth:
    """Synthetic one-shot environment: control dir + pinned package +
    synthetic original campaign + absent planned campaign root."""

    def __init__(self, root: Path):
        self.root = root
        self.control = root / 'linker-control'
        self.pkg = root / 'pkg'
        self.original = root / 'original' / 'campaign'
        self.new_root = root / 'planned-root'
        self.new_campaign = self.new_root / 'campaign'
        self.control.mkdir(parents=True)
        self.pkg.mkdir(parents=True)
        self.original.mkdir(parents=True)

        self.files = {
            'linker': LINKER,
            'launcher': self.pkg / 'launch-rv05.ps1',
            'preflight': self.pkg / 'preflight-fake.py',
            'launch_spec': self.pkg / 'launch-spec.json',
            'contract': self.pkg / 'soak-subinput-receipt-v1.md',
            'errata': self.pkg / 'ERRATA-001.md',
            'generator': self.pkg / 'soak_okcompute.py',
            'manifest': self.pkg / 'manifest-rv05-final.json',
            'config': self.pkg / 'config-bound.json',
        }
        self.files['launcher'].write_text(
            SYNTHETIC_LAUNCHER_PS1, encoding='utf-8')
        self.files['preflight'].write_text(FAKE_PREFLIGHT, encoding='utf-8')
        self.files['launch_spec'].write_text(
            json.dumps({'schema': 'pal-rv05-launch-spec-v1',
                        'synthetic': True,
                        'paths': {
                            'planned_campaign_root': str(self.new_root),
                            'planned_campaign_dir': str(self.new_campaign),
                        }}), encoding='utf-8')
        self.files['contract'].write_text(
            '# synthetic receipt contract\n', encoding='utf-8')
        self.files['errata'].write_text(
            '# synthetic errata\n', encoding='utf-8')
        self.files['generator'].write_text(
            '# synthetic generator\n', encoding='utf-8')
        self.files['manifest'].write_text(
            json.dumps({'schema': 'pal-soak-manifest-v1'}), encoding='utf-8')
        self.files['config'].write_text(
            json.dumps({'schema': 'pal-soak-config-v1',
                        'candidate': 'synthetic-15f24d24'}), encoding='utf-8')

        self.report_path = self.control / 'preflight-report.json'
        self.launch_counter = self.control / 'launch-counter.txt'
        self.binding_path = self.control / 'linker-binding.json'
        self.binding = self._base_binding()

    def _base_binding(self) -> dict:
        control = self.control
        return {
            'schema': 'pal-rv05-linker-binding-v1',
            'task_id': 'PAL_RV05_CLOSURE_20260922_N3_SYNTH',
            **{name: {'path': str(path), 'sha256': sha256_file(path)}
               for name, path in self.files.items()},
            'runtime': {'python_exe': PY, 'sha256': sha256_file(PY)},
            'frozen': {'commit': '15f24d246493cdc114a127c366da537659d8c5d8',
                       'tree': 'd388d113c0a75bb297bf1d60b13a9237fb31a07a'},
            'original_campaign': str(self.original),
            'new_campaign_root': str(self.new_root),
            'new_campaign_dir': str(self.new_campaign),
            'claim_path': str(control / 'linker-claim.json'),
            'state_path': str(control / 'linker-state.json'),
            'started_receipt_path': str(control / 'linker-started.json'),
            'linker_stop_path': str(control / 'linker-stop'),
            'preflight_report_path': str(self.report_path),
            'launch_argv': [SYSTEM32_POWERSHELL, '-NoProfile',
                            '-NonInteractive', '-ExecutionPolicy',
                            'RemoteSigned', '-File',
                            str(self.files['launcher']),
                            '-SpecPath', str(self.files['launch_spec'])],
            'preflight_argv': [PY, '-I', '-S', '-B',
                               str(self.files['preflight']),
                               '--spec', str(self.files['launch_spec']),
                               '--json-out', str(self.report_path)],
            'launch_window_utc': {'earliest': '2026-09-24T02:14:50Z',
                                  'latest': '2026-09-24T12:36:41Z'},
            'deadline_utc': '2026-09-28T00:36:41Z',
            'poll_interval_seconds': 300,
            'evidence_stability_seconds': 900,
        }

    # -- environment shaping ------------------------------------------------

    def write_binding(self, mutate=None) -> Path:
        binding = json.loads(json.dumps(self.binding))
        if mutate:
            mutate(binding)
        self.binding_path.write_text(
            json.dumps(binding, indent=2) + '\n', encoding='utf-8')
        return self.binding_path

    def write_fake_launch_script(self) -> None:
        """Install the synthetic launch stand-in's DEFAULT behavior config
        (counter on, campaign at the bound dir, rc 0). The .ps1 itself is
        pinned in __init__; per-test variants go through launch_behavior().
        """
        self.launch_behavior(counter=str(self.launch_counter),
                             campaign=str(self.new_campaign),
                             mode='ok', rc=0)

    def preflight_behavior(self, **cfg) -> None:
        """Merge keys into preflight-behavior.json (the fake preflight's
        behavior config next to the pinned script). Replaces the old
        FAKE_PREFLIGHT_* environment knobs: formal child runs get a closed
        environment, so test steering happens through this file."""
        path = self.pkg / 'preflight-behavior.json'
        merged = {}
        if path.exists():
            merged = json.loads(path.read_text(encoding='utf-8'))
        merged.update(cfg)
        path.write_text(json.dumps(merged, indent=2), encoding='utf-8')

    def launch_behavior(self, **cfg) -> None:
        """Merge keys into launch-behavior.json (the synthetic launcher's
        behavior config next to the pinned .ps1)."""
        path = self.pkg / 'launch-behavior.json'
        merged = {}
        if path.exists():
            merged = json.loads(path.read_text(encoding='utf-8'))
        merged.update(cfg)
        path.write_text(json.dumps(merged, indent=2), encoding='utf-8')

    def set_lock(self, mode: str) -> None:
        lock = self.original / 'driver.lock'
        if mode == 'absent':
            return
        if mode == 'garbage':
            lock.write_bytes(b'not-json{{{')
            return
        if mode == 'dir':
            lock.mkdir()
            return
        if mode == 'alive':
            pid = os.getpid()
            started = _LINKER_MOD._process_start_utc(pid)
            wall = started.timestamp()
        elif mode == 'reuse':
            pid = os.getpid()
            started = _LINKER_MOD._process_start_utc(pid)
            wall = started.timestamp() - 3600.0
        elif mode == 'dead':
            child = subprocess.Popen(
                [PY, '-I', '-S', '-B', '-c', 'pass'])
            child.wait()
            pid = child.pid
            wall = time.time() - 60.0
        else:
            raise ValueError(mode)
        lock.write_text(json.dumps(
            {'pid': pid, 'wall': wall,
             'argv': ['synthetic-original-driver']}), encoding='utf-8')

    def build_segment(self, rounds: list[str], close: str = 'ok') -> Path:
        seg = self.original / 'segments' / 'segment-000001'
        (seg / 'rounds').mkdir(parents=True, exist_ok=True)
        for index, status in enumerate(rounds, start=1):
            rd = seg / 'rounds' / f'round-{index:06d}'
            rd.mkdir(exist_ok=True)
            (rd / 'round.json').write_text(json.dumps(
                {'round': index, 'status': status,
                 'sub_seed': 1000 + index}), encoding='utf-8')
        if close == 'ok':
            (seg / 'segment-close.json').write_text(json.dumps(
                {'schema': 'pal-soak-segment-close-v1', 'reason': 'complete',
                 'rounds': len(rounds)}), encoding='utf-8')
        elif close == 'halfwrite':
            (seg / 'segment-close.json').write_text(
                '{"schema": "pal-soak-segment-close-v1", "reas', encoding='utf-8')
        elif close == 'absent':
            pass
        return seg

    def set_evidence_age(self, seconds: float, now: str = IN1) -> None:
        when = _epoch(now) - seconds
        for path in sorted(self.original.rglob('*')):
            if path.is_file():
                os.utime(path, (when, when))

    def original_exited_clean(self, round_statuses=('failed', 'failed'),
                              stable=True) -> None:
        """The 'original failed its gates but exited NORMALLY' shape: lock
        released (absent), closing receipts present, no running children,
        failure records preserved, evidence stable."""
        self.set_lock('absent')
        self.build_segment(list(round_statuses), close='ok')
        (self.original / 'campaign.json').write_text(json.dumps(
            {'schema': 'pal-soak-campaign-v1',
             'status': 'finished-with-failures'}), encoding='utf-8')
        self.set_evidence_age(2000 if stable else 100, now=IN1)

    # -- linker driving -----------------------------------------------------

    def arm(self, now: str = BEFORE, binding_path: Path | None = None):
        return run_linker(['arm', '--binding',
                           str(binding_path or self.binding_path),
                           '--now-utc', now])

    def wait(self, now: str, extra=(), binding_path: Path | None = None,
             timeout=180):
        proc = subprocess.run(
            [PY, '-I', '-S', '-B', str(LINKER), 'wait', '--binding',
             str(binding_path or self.binding_path), '--now-utc', now,
             *extra],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout)
        return proc.returncode, proc.stdout + proc.stderr

    def state(self) -> dict:
        return json.loads(
            (self.control / 'linker-state.json').read_text(encoding='utf-8'))

    def claim(self):
        path = self.control / 'linker-claim.json'
        return (path.read_text(encoding='utf-8') if path.exists() else None)

    def launch_count(self) -> int:
        counter = self.launch_counter
        if not counter.exists():
            return 0
        return len([line for line in
                    counter.read_text(encoding='utf-8').splitlines()
                    if line.strip()])


@pytest.fixture()
def synth(tmp_path):
    env = Synth(tmp_path)
    env.write_fake_launch_script()
    env.write_binding()
    return env


# ==========================================================================
# the 13 required synthetic scenarios
# ==========================================================================

def test_t01_window_not_reached_keeps_waiting(synth):
    """1. 未到时刻: even with the original fully exited+stable, a wait
    before the window earliest stays ARMED_WAITING and never claims or
    launches."""
    synth.original_exited_clean()
    assert synth.arm()[0] == 0
    rc, out = synth.wait(BEFORE)
    assert rc == 0, out
    state = synth.state()
    assert state['state'] == 'ARMED_WAITING'
    assert state['checks'][-1]['reason'] == 'window_not_reached'
    assert synth.claim() is None
    assert not synth.new_root.exists()


def test_t02_original_alive_keeps_waiting(synth):
    """2. 原存活: in-window but the original lock identity is alive ->
    ARMED_WAITING (planned end time is NOT proof of exit)."""
    synth.set_lock('alive')
    synth.build_segment(['running'])
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN1)
    assert rc == 0, out
    state = synth.state()
    assert state['state'] == 'ARMED_WAITING'
    assert state['checks'][-1]['reason'] == 'original_running'
    assert synth.claim() is None
    assert not synth.new_root.exists()


def test_t03_original_failed_but_clean_exit_starts_once(synth):
    """3. 原失败但正常退出: released lock + closing receipts + stable
    evidence + failures PRESERVED -> proceeds PRECHECK -> CLAIMED ->
    STARTED exactly once with the bound launch flow."""
    synth.original_exited_clean(round_statuses=('failed', 'failed'))
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN1)
    assert rc == 0, out
    state = synth.state()
    assert state['state'] == 'STARTED'
    hops = [t['to'] for t in state['transitions']]
    assert hops == ['ARMED_WAITING', 'PRECHECK', 'CLAIMED', 'STARTED']
    claim = json.loads(synth.claim())
    receipt = json.loads(
        (synth.control / 'linker-started.json').read_text(encoding='utf-8'))
    assert receipt['claim_id'] == claim['claim_id']
    assert receipt['new_campaign_dir'] == str(synth.new_campaign)
    assert receipt['launch_returncode'] == 0
    assert receipt['campaign_json_written'] is True
    assert (synth.new_campaign / 'campaign.json').exists()
    assert synth.launch_count() == 1
    # failure evidence preserved untouched
    round_doc = json.loads((synth.original / 'segments' / 'segment-000001'
                            / 'rounds' / 'round-000001' / 'round.json'
                            ).read_text(encoding='utf-8'))
    assert round_doc['status'] == 'failed'
    classify = next(c for c in state['checks'] if c['reason'] == 'exited_clean')
    assert classify['detail']['round_status_counts']['failed'] == 2


def test_t04_leftover_children_is_terminal_no_go(synth):
    """4. 残留孩子: original exited but round records still say running ->
    NO_GO (definite, typed); a second cycle does not retry it into a
    pass."""
    synth.set_lock('absent')
    synth.build_segment(['failed', 'running'], close='ok')
    synth.set_evidence_age(2000)
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN1)
    assert rc == 2, out
    state = synth.state()
    assert state['state'] == 'NO_GO'
    assert state['terminal']['reason'] == 'leftover_children'
    assert synth.claim() is None
    rc2, out2 = synth.wait(IN1)
    assert rc2 == 2, out2
    assert synth.state()['state'] == 'NO_GO'
    assert synth.launch_count() == 0


def test_t05_unreadable_lock_is_unknown(synth, tmp_path):
    """5. 读权限未知: a lock that exists but cannot be read/parsed ->
    UNKNOWN (never a bare-PID or optimistic conclusion); terminal."""
    synth.set_lock('garbage')
    synth.build_segment(['failed'])
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN1)
    assert rc == 5, out
    assert synth.state()['state'] == 'UNKNOWN'
    assert 'lock_invalid' in synth.state()['terminal']['reason']
    assert synth.launch_count() == 0
    assert synth.claim() is None
    # second variant on a FRESH environment: the lock path itself cannot
    # be read as a file (directory in the way)
    fresh = Synth(tmp_path / 'v2')
    fresh.write_fake_launch_script()
    fresh.write_binding()
    fresh.set_lock('dir')
    fresh.build_segment(['failed'])
    assert fresh.arm()[0] == 0
    rc2, out2 = fresh.wait(IN1)
    assert rc2 == 5, out2
    assert fresh.state()['state'] == 'UNKNOWN'
    assert 'lock_unreadable' in fresh.state()['terminal']['reason']
    assert fresh.launch_count() == 0
    assert fresh.claim() is None


def test_t06_pid_reuse_is_unknown(synth):
    """6. PID 复用: the lock PID is alive but its creation time does not
    match the recorded wall -> a DIFFERENT process owns the pid ->
    UNKNOWN; the original is never declared dead from a bare pid."""
    synth.set_lock('reuse')
    synth.build_segment(['failed'])
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN1)
    assert rc == 5, out
    state = synth.state()
    assert state['state'] == 'UNKNOWN'
    assert state['terminal']['reason'] == 'pid_reuse'
    assert state['checks'][-1]['detail']['lock_pid'] == os.getpid()
    assert synth.claim() is None
    assert synth.launch_count() == 0


def test_t07_half_written_close_receipt_is_unknown(synth):
    """7. 原快照半写: truncated segment-close.json -> corrupted evidence
    -> UNKNOWN; retrying the cycle stays UNKNOWN (no retry-to-pass)."""
    synth.set_lock('absent')
    synth.build_segment(['failed'], close='halfwrite')
    synth.set_evidence_age(2000)
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN1)
    assert rc == 5, out
    state = synth.state()
    assert state['state'] == 'UNKNOWN'
    assert state['terminal']['reason'] == 'evidence_corrupted'
    rc2, out2 = synth.wait(IN1)
    assert rc2 == 5, out2
    assert synth.state()['state'] == 'UNKNOWN'
    assert synth.launch_count() == 0
    assert synth.claim() is None


def test_t08_candidate_dependency_drift_is_no_go(synth):
    """8. 候选或依赖漂移: a pinned control-package/candidate file changes
    after arm -> NO_GO with the drifted fields listed; nothing starts."""
    synth.original_exited_clean()
    assert synth.arm()[0] == 0
    synth.files['config'].write_text(
        json.dumps({'schema': 'pal-soak-config-v1',
                    'candidate': 'DRIFTED'}), encoding='utf-8')
    rc, out = synth.wait(IN1)
    assert rc == 2, out
    state = synth.state()
    assert state['state'] == 'NO_GO'
    assert 'binding_drift' in state['terminal']['reason']
    assert 'config:sha256' in state['terminal']['reason']
    assert synth.claim() is None
    assert not synth.new_root.exists()
    assert synth.launch_count() == 0


def test_t09_claim_competition_single_start(synth):
    """9. 两实例竞争 claim: two concurrent instances race the unique
    claim; exactly one starts, the loser exits without launching and
    without modifying the shared state."""
    synth.original_exited_clean()
    assert synth.arm()[0] == 0
    results = {}

    def run_one(tag):
        synth.preflight_behavior(sleep=2.5)
        results[tag] = synth.wait(IN1, timeout=300)

    first = threading.Thread(target=run_one, args=('a',))
    first.start()
    time.sleep(0.4)
    second = threading.Thread(target=run_one, args=('b',))
    second.start()
    first.join()
    second.join()
    codes = sorted(code for code, _out in results.values())
    assert codes == [0, 3], results
    assert synth.launch_count() == 1
    claim = json.loads(synth.claim())
    assert claim['schema'] == 'pal-rv05-linker-claim-v1'
    state = synth.state()
    assert state['state'] == 'STARTED'
    assert state['claim']['claim_id'] == claim['claim_id']


def test_t10_claim_then_interrupt_no_relaunch(synth):
    """10. claim 写入后中断: a control-program crash between claim and
    receipt (simulated with the explicit --stop-after claim hook) ->
    restart adjudicates UNKNOWN, keeps the claim byte-identical and never
    re-launches."""
    synth.original_exited_clean()
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN1, extra=['--stop-after', 'claim'])
    assert rc == 0, out
    assert synth.state()['state'] == 'CLAIMED'
    claim_before = sha256_file(synth.control / 'linker-claim.json')
    rc2, out2 = synth.wait(IN1)
    assert rc2 == 5, out2
    state = synth.state()
    assert state['state'] == 'UNKNOWN'
    assert 'lost_ack' in state['terminal']['reason']
    assert sha256_file(synth.control / 'linker-claim.json') == claim_before
    assert synth.launch_count() == 0
    assert not synth.new_root.exists()


def test_t11_started_receipt_lost_is_unknown(synth):
    """11. 启动后确认丢失: after STARTED, a half-written/missing receipt
    with the claim present -> UNKNOWN; claim intact, no second launch."""
    synth.original_exited_clean()
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN1)
    assert rc == 0, out
    assert synth.state()['state'] == 'STARTED'
    claim_before = sha256_file(synth.control / 'linker-claim.json')
    receipt = synth.control / 'linker-started.json'
    receipt.write_text(receipt.read_text(encoding='utf-8')[:30],
                       encoding='utf-8')
    rc2, out2 = synth.wait(IN2)
    assert rc2 == 5, out2
    assert synth.state()['state'] == 'UNKNOWN'
    assert 'lost_ack' in synth.state()['terminal']['reason']
    assert sha256_file(synth.control / 'linker-claim.json') == claim_before
    assert synth.launch_count() == 1
    # deletion variant
    receipt.unlink()
    rc3, out3 = synth.wait(IN2)
    assert rc3 == 5, out3
    assert synth.state()['state'] == 'UNKNOWN'
    assert synth.launch_count() == 1


def test_t12_operator_cancel_is_linker_own(synth):
    """12. 操作者撤销: cancel writes ONLY the linker's own stop marker;
    the original campaign stop file and the new campaign stop file are
    never created; a later wait performs no work."""
    synth.set_lock('alive')
    synth.build_segment(['running'])
    assert synth.arm()[0] == 0
    rc, out = run_linker(
        ['cancel', '--binding', str(synth.binding_path),
         '--now-utc', BEFORE, '--note', 'operator says stop'])
    assert rc == 0, out
    state = synth.state()
    assert state['state'] == 'CANCELLED'
    assert (synth.control / 'linker-stop').exists()
    assert not (synth.original / 'stop').exists()
    assert not (synth.new_campaign / 'stop').exists()
    assert not synth.new_root.exists()
    rc2, out2 = synth.wait(IN1)
    assert rc2 == 0, out2
    assert synth.state()['state'] == 'CANCELLED'
    assert synth.launch_count() == 0
    assert synth.claim() is None
    # cancel is idempotent and never reaches either campaign
    rc3, out3 = run_linker(
        ['cancel', '--binding', str(synth.binding_path), '--now-utc', IN1])
    assert rc3 == 0, out3
    assert not (synth.original / 'stop').exists()


def test_t13_clock_jump_and_window_oversleep(synth, tmp_path):
    """13. 时钟跳变与睡眠越窗: (a) a backward wall-clock jump -> UNKNOWN;
    (b) sleeping past the window latest -> EXPIRED (window_closed);
    (c) past the total deadline -> EXPIRED (past_total_deadline)."""
    # (a) backward clock jump
    synth.set_lock('alive')
    synth.build_segment(['running'])
    assert synth.arm()[0] == 0
    rc, out = synth.wait(IN2)
    assert rc == 0, out
    assert synth.state()['state'] == 'ARMED_WAITING'
    rc2, out2 = synth.wait(JUMP_BACK)
    assert rc2 == 5, out2
    assert synth.state()['state'] == 'UNKNOWN'
    assert synth.state()['terminal']['reason'] == 'clock_jump_backward'
    assert synth.claim() is None

    # (b) oversleep past the window latest (fresh environment)
    fresh = Synth(tmp_path / 'fresh-b')
    fresh.write_fake_launch_script()
    fresh.write_binding()
    fresh.set_lock('alive')
    fresh.build_segment(['running'])
    assert fresh.arm()[0] == 0
    rc3, out3 = fresh.wait(IN1)
    assert rc3 == 0, out3
    assert fresh.state()['state'] == 'ARMED_WAITING'
    rc4, out4 = fresh.wait(AFTER)
    assert rc4 == 4, out4
    assert fresh.state()['state'] == 'EXPIRED'
    assert fresh.state()['terminal']['reason'] == 'window_closed'
    assert fresh.claim() is None
    assert fresh.launch_count() == 0

    # (c) past the total deadline (fresh environment)
    late = Synth(tmp_path / 'fresh-c')
    late.write_fake_launch_script()
    late.write_binding()
    late.set_lock('alive')
    late.build_segment(['running'])
    assert late.arm(now=BEFORE)[0] == 0
    rc5, out5 = late.wait(PAST_DEADLINE)
    assert rc5 == 4, out5
    assert late.state()['state'] == 'EXPIRED'
    assert late.state()['terminal']['reason'] == 'past_total_deadline'
    assert late.claim() is None


# ==========================================================================
# binding validation (legal controls + rejections)
# ==========================================================================

def test_t14_binding_rejects_placeholders(synth):
    """BIND 占位符拒绝: placeholder pins are rejected at arm (exit 7);
    legal control: the exact same environment with real pins arms."""
    def mutate(binding):
        binding['config']['sha256'] = 'PLACEHOLDER'
    synth.write_binding(mutate)
    rc, out = synth.arm(binding_path=synth.write_binding(mutate))
    assert rc == 7, out
    assert 'placeholder' in out.lower()
    assert not (synth.control / 'linker-state.json').exists()
    # legal control: unmutated binding arms fine
    synth.write_binding()
    rc2, out2 = synth.arm()
    assert rc2 == 0, out2
    assert synth.state()['state'] == 'ARMED_WAITING'


def test_t15_binding_rejects_unknown_schema_and_fields(synth):
    def bad_schema(binding):
        binding['schema'] = 'pal-rv05-linker-binding-v9'
    rc, out = synth.arm(binding_path=synth.write_binding(bad_schema))
    assert rc == 7, out
    assert 'unknown binding schema' in out

    def extra_field(binding):
        binding['surprise_key'] = 'x'
    rc2, out2 = synth.arm(binding_path=synth.write_binding(extra_field))
    assert rc2 == 7, out2
    assert 'unknown' in out2.lower()

    def missing_field(binding):
        del binding['errata']
    rc3, out3 = synth.arm(binding_path=synth.write_binding(missing_field))
    assert rc3 == 7, out3
    assert 'missing' in out3.lower()


def test_t16_binding_rejects_wrong_types(synth):
    def bad_sha_type(binding):
        binding['config']['sha256'] = 12345
    rc, out = synth.arm(binding_path=synth.write_binding(bad_sha_type))
    assert rc == 7, out

    def bad_argv_type(binding):
        binding['launch_argv'] = 'powershell whole string'
    rc2, out2 = synth.arm(binding_path=synth.write_binding(bad_argv_type))
    assert rc2 == 7, out2

    def bad_window_shape(binding):
        binding['launch_window_utc'] = {'earliest': '2026-09-24T02:14:50Z'}
    rc3, out3 = synth.arm(binding_path=synth.write_binding(bad_window_shape))
    assert rc3 == 7, out3

    def bad_pin_keys(binding):
        binding['launcher'] = {'path': binding['launcher']['path']}
    rc4, out4 = synth.arm(binding_path=synth.write_binding(bad_pin_keys))
    assert rc4 == 7, out4

    def fast_polling(binding):
        binding['poll_interval_seconds'] = 60
    rc5, out5 = synth.arm(binding_path=synth.write_binding(fast_polling))
    assert rc5 == 7, out5
    assert 'poll_interval' in out5


def test_t17_binding_lists_and_rejects_time_limit_relaxation(synth):
    def relax_latest(binding):
        binding['launch_window_utc']['latest'] = '2026-09-24T13:36:41Z'
    rc, out = synth.arm(binding_path=synth.write_binding(relax_latest))
    assert rc == 7, out
    assert 'launch_window_utc.latest' in out
    assert 'NOT auto-accepted' in out

    def relax_deadline(binding):
        binding['deadline_utc'] = '2026-09-30T00:00:00Z'
    rc2, out2 = synth.arm(binding_path=synth.write_binding(relax_deadline))
    assert rc2 == 7, out2
    assert 'deadline_utc' in out2

    def relax_stability(binding):
        binding['evidence_stability_seconds'] = 10
    rc3, out3 = synth.arm(binding_path=synth.write_binding(relax_stability))
    assert rc3 == 7, out3
    assert 'evidence_stability_seconds' in out3

    def move_earlier(binding):
        binding['launch_window_utc']['earliest'] = '2026-09-23T00:00:00Z'
    rc4, out4 = synth.arm(binding_path=synth.write_binding(move_earlier))
    assert rc4 == 7, out4
    assert 'launch_window_utc.earliest' in out4


def test_t18_arm_records_first_receipt_and_cancel_method(synth):
    """arm 记录义务: PID + creation identity, command, config digest
    summary, first ARMED_WAITING receipt and the cancel method."""
    synth.set_lock('alive')
    assert synth.arm()[0] == 0
    state = synth.state()
    assert state['state'] == 'ARMED_WAITING'
    armed = state['armed']
    assert armed['pid'] > 0
    assert armed['pid_start_utc']
    assert any('arm' in part for part in armed['argv'])
    assert armed['frozen']['commit'] == \
        '15f24d246493cdc114a127c366da537659d8c5d8'
    assert len(armed['binding_digest_summary']) == 9
    receipt = armed['first_receipt']
    assert receipt['schema'] == 'pal-rv05-linker-armed-receipt-v1'
    assert receipt['state'] == 'ARMED_WAITING'
    assert receipt['pid'] == armed['pid']
    assert str(synth.control / 'linker-stop') in armed['cancel_method']
    rc, out = run_linker(['status', '--binding', str(synth.binding_path)])
    assert rc == 0, out
    assert 'ARMED_WAITING' in out


def test_t19_wait_requires_arm_and_single_subprocess_shape(synth):
    """Legal control on usage: wait without arm is refused; the started
    receipt records the exact fixed launch argv (single fixed command,
    no shell string); mark-final-review-ready works from STARTED."""
    rc, out = synth.wait(IN1)
    assert rc == 6, out
    assert 'not armed' in out
    synth.original_exited_clean()
    assert synth.arm()[0] == 0
    rc2, out2 = synth.wait(IN1)
    assert rc2 == 0, out2
    receipt = json.loads(
        (synth.control / 'linker-started.json').read_text(encoding='utf-8'))
    assert receipt['launch_argv'] == list(synth.binding['launch_argv'])
    rc3, out3 = run_linker(
        ['mark-final-review-ready', '--binding', str(synth.binding_path),
         '--now-utc', IN2])
    assert rc3 == 0, out3
    assert synth.state()['state'] == 'FINAL_REVIEW_READY'
    # one shot: further wait is idempotent, never a second launch
    rc4, out4 = synth.wait(IN2)
    assert rc4 == 0, out4
    assert synth.launch_count() == 1


def test_t20_binding_rejects_launch_argv_not_carrying_pinned_launcher(synth):
    """launch_argv/pin cross-check (fix wave, cross-review M1): an argv
    that invokes an UNPINNED launcher file is rejected at arm (exit 7)
    before any state exists — a mis-assembled binding can never call an
    unpinned script. The unmutated binding (which carries the pinned
    launcher path inside launch_argv) is the legal control every other
    arming test exercises."""
    drifted = synth.pkg / 'launch-drifted.ps1'
    drifted.write_text('# drifted launcher stand-in (exists; unpinned)\n',
                       encoding='utf-8')

    def drift_argv(binding):
        # N4/N0 reconciliation: the drift vector now uses the COMPLIANT
        # PowerShell grammar with the UNPINNED drifted script at the -File
        # position - the position-exact L5 form of the same defect (the
        # old python-fake membership vector is subsumed by the argv[0]
        # whitelist rejection).
        binding['launch_argv'] = [
            SYSTEM32_POWERSHELL, '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'RemoteSigned', '-File',
            str(drifted),
            '-SpecPath', binding['launch_spec']['path']]
    rc, out = synth.arm(binding_path=synth.write_binding(drift_argv))
    assert rc == 7, out
    assert 'launch_argv' in out
    assert 'launcher' in out
    assert not (synth.control / 'linker-state.json').exists()

    # legal control: the same environment with the pinned launcher path
    # back inside launch_argv arms fine and the started receipt carries it
    synth.write_binding()
    synth.original_exited_clean()
    assert synth.arm()[0] == 0
    rc2, out2 = synth.wait(IN1)
    assert rc2 == 0, out2
    receipt = json.loads(
        (synth.control / 'linker-started.json').read_text(encoding='utf-8'))
    assert str(synth.files['launcher']) in receipt['launch_argv']
    assert synth.launch_count() == 1


# ==========================================================================
# fix wave N1 (PAL_RV05_CONTROL_SAFETY_20260922): deterministic injection
# tests for L1-L4 plus legal controls.
#
# These run the linker IN-PROCESS against a lazy fake runner injected at
# the module's own dependency boundary (``subprocess.run`` replaced by a
# hook-carrying stand-in, the same injection style the coordinator's
# repro used): NO real child process is started by this section, and
# every race counterexample is reproduced with deterministic
# barrier/hook placement (cancel during preflight, clock crossing during
# preflight, cancel between claim and launch, cancel during launch,
# stale GO report, pin drift mid-run, short claim write, old/foreign
# receipts, rc0 without campaign) - never with random sleeps.
# ==========================================================================

INPROC_BEFORE = datetime(2026, 9, 23, tzinfo=timezone.utc)
INPROC_IN = datetime(2026, 9, 24, 3, tzinfo=timezone.utc)
INPROC_LATE = datetime(2026, 9, 24, 12, 36, 42, tzinfo=timezone.utc)
INPROC_PAST_DEADLINE = datetime(2026, 9, 28, 1, tzinfo=timezone.utc)


def _load_linker_module_inproc():
    spec = importlib.util.spec_from_file_location(
        'soak_linker_inproc_under_test', LINKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class InProcSynth:
    """Single-process synthetic environment with deterministic hooks.

    ``preflight_hook`` runs while the (fake) bound preflight "executes";
    ``launch_hook`` runs while the (fake) bound launch "executes";
    ``launch_rc`` / ``make_campaign`` / ``write_report`` shape the launch
    and report behavior. ``events`` records every stub call."""

    def __init__(self, root: Path):
        root.mkdir(parents=True)
        self.root = root
        self.m = _load_linker_module_inproc()
        self.control = root / 'control'
        self.original = root / 'original'
        pkg = root / 'pkg'
        self.control.mkdir()
        self.original.mkdir()
        pkg.mkdir()
        pins = {}
        for name in self.m.PINNED_FILE_FIELDS:
            path = LINKER if name == 'linker' else pkg / f'{name}.txt'
            if name != 'linker':
                path.write_text(f'SYNTHETIC {name}\n', encoding='utf-8')
            pins[name] = {'path': str(path), 'sha256': sha256_file(path)}
        self.report_path = self.control / 'preflight-report.json'
        self.new_root = root / 'planned-root'
        self.new_campaign = self.new_root / 'campaign'
        self.binding_path = self.control / 'linker-binding.json'
        # N4/N0 reconciliation (L5 whitelist): the in-proc lane never
        # executes children (lazy fake runner), so argv[0] is a STUB
        # interpreter file PINNED via the optional launch_interpreter key
        # (validates _require_pin + live sha256 at load) instead of the
        # host's real System32 powershell - hermetic and platform-neutral.
        stub_interp = pkg / 'powershell.exe'
        stub_interp.write_text(
            '# synthetic interpreter stand-in (pinned; never executed - '
            'the lazy fake runner intercepts every spawn)\n',
            encoding='utf-8')
        self.launch_argv = [str(stub_interp), '-NoProfile',
                            '-NonInteractive', '-ExecutionPolicy',
                            'RemoteSigned', '-File',
                            pins['launcher']['path'],
                            '-SpecPath', pins['launch_spec']['path']]
        self.preflight_argv = [
            PY, '-I', '-S', '-B', pins['preflight']['path'],
            '--spec', pins['launch_spec']['path'],
            '--json-out', str(self.report_path)]
        self.binding = {
            'schema': self.m.SCHEMA_BINDING,
            'task_id': 'PAL_RV05_CONTROL_SAFETY_N1_SYNTH',
            **pins,
            'runtime': {'python_exe': PY, 'sha256': sha256_file(PY)},
            'frozen': {
                'commit': 'b061b7d7ad51cd21d7a64c84eba0941a1fd08085',
                'tree': '79ec2d7945f9dd2b492e243521e0c166f8fbebd0'},
            'original_campaign': str(self.original),
            'new_campaign_root': str(self.new_root),
            'new_campaign_dir': str(self.new_campaign),
            'claim_path': str(self.control / 'linker-claim.json'),
            'state_path': str(self.control / 'linker-state.json'),
            'started_receipt_path': str(self.control / 'linker-started.json'),
            'linker_stop_path': str(self.control / 'linker-stop'),
            'preflight_report_path': str(self.report_path),
            'launch_argv': list(self.launch_argv),
            'launch_interpreter': {'path': str(stub_interp),
                                   'sha256': sha256_file(stub_interp)},
            'preflight_argv': list(self.preflight_argv),
            'launch_window_utc': {'earliest': '2026-09-24T02:14:50Z',
                                  'latest': '2026-09-24T12:36:41Z'},
            'deadline_utc': '2026-09-28T00:36:41Z',
            'poll_interval_seconds': 300,
            'evidence_stability_seconds': 900}
        self.binding_path.write_text(
            json.dumps(self.binding, indent=2) + '\n', encoding='utf-8')
        self.events = []
        self.preflight_hook = None
        self.launch_hook = None
        self.launch_rc = 0
        self.make_campaign = True
        self.write_report = True
        # injection boundaries: no real liveness probe, no real classify,
        # no real child process (lazy fake runner with hooks)
        self.m._process_start_utc = lambda pid: INPROC_BEFORE
        self.m.classify_original = \
            lambda *a, **k: ('OK', 'synthetic_exited_clean', {})
        self.m.subprocess = SimpleNamespace(
            run=self._fake_run, SubprocessError=subprocess.SubprocessError)
        self.linker = self.m.Linker(self.binding_path, INPROC_BEFORE)
        assert self.linker.cmd_arm() == 0
        self.linker.synthetic_clock = INPROC_IN

    def _fake_run(self, argv, **kwargs):
        static = list(self.preflight_argv)
        if list(argv[:len(static)]) == static and \
                len(argv) == len(static) + 2 and \
                argv[len(static)] == '--execution-id':
            # the runtime preflight argv = static 9-token whitelist + this
            # cycle's fresh execution id (never a binding value)
            self.events.append('preflight')
            if self.write_report:
                execution_id = argv[-1]
                spec_path = static[6]
                now_utc = datetime.now(timezone.utc).strftime(
                    '%Y-%m-%dT%H:%M:%SZ')
                report = {
                    'schema': 'pal-rv05-preflight-v1', 'overall': 'GO',
                    'unmet_count': 0, 'unmet_ids': [],
                    'checks': [{'id': i, 'status': 'PASS'} for i in (
                        'capacity_reachable', 'windows_kit_present',
                        'frozen_tree_integrity', 'resources_available',
                        'original_driver_exited',
                        'original_segment_close_present',
                        'original_evidence_stable',
                        'original_children_cleaned', 'launch_window')],
                    'execution': {
                        'execution_id': execution_id,
                        'spec': spec_path,
                        'spec_sha256': sha256_file(spec_path),
                        'json_out': static[8],
                        'argv': list(argv)[5:],
                        'invoked_at_utc': now_utc},
                    'generated_at_utc': now_utc}
                self.report_path.write_text(
                    json.dumps(report, indent=2) + '\n', encoding='utf-8')
            if self.preflight_hook:
                self.preflight_hook()
            return SimpleNamespace(returncode=0)
        if list(argv) == self.launch_argv:
            self.events.append('launch_stub')
            if self.launch_hook:
                self.launch_hook()
            if self.make_campaign:
                self.new_campaign.mkdir(parents=True, exist_ok=True)
                (self.new_campaign / 'campaign.json').write_text(
                    json.dumps({'schema': 'pal-soak-campaign-v1',
                                'synthetic': True}) + '\n',
                    encoding='utf-8')
            return SimpleNamespace(returncode=self.launch_rc)
        raise AssertionError(
            f'unexpected subprocess argv {argv!r} (no real command runs)')

    # -- accessors ----------------------------------------------------------

    def cycle(self):
        return self.linker._cycle('never')

    def state(self) -> dict:
        return json.loads(
            (self.control / 'linker-state.json').read_text(encoding='utf-8'))

    def state_value(self) -> str:
        return self.state()['state']

    def terminal_reason(self) -> str:
        return (self.state().get('terminal') or {}).get('reason', '')

    def claim_path(self) -> Path:
        return self.control / 'linker-claim.json'

    def claim_exists(self) -> bool:
        return self.claim_path().exists()

    def claim_text(self):
        path = self.claim_path()
        return path.read_text(encoding='utf-8') if path.exists() else None

    def receipt_path(self) -> Path:
        return self.control / 'linker-started.json'

    def receipt_text(self):
        path = self.receipt_path()
        return path.read_text(encoding='utf-8') if path.exists() else None

    def stop_path(self) -> Path:
        return self.control / 'linker-stop'

    def launches(self) -> int:
        return self.events.count('launch_stub')

    def preflights(self) -> int:
        return self.events.count('preflight')


# -- L1: cancel vs launch race ---------------------------------------------

def test_n1_l1_cancel_during_preflight_prevents_launch(tmp_path):
    """L1 injection: while the bound preflight 'executes', another cancel
    is fully confirmed (marker + CANCELLED state). The post-preflight
    gate must see it and stop BEFORE the claim: CANCELLED, no launch,
    slot NOT consumed."""
    f = InProcSynth(tmp_path / 'l1-preflight')

    def cancel_during_preflight():
        other = f.m.Linker(f.binding_path, INPROC_IN)
        assert other.cmd_cancel('synthetic owner cancellation') == 0
        assert f.state_value() == 'CANCELLED'

    f.preflight_hook = cancel_during_preflight
    code, cont = f.cycle()
    assert (code, cont) == (0, False)
    state = f.state()
    assert state['state'] == 'CANCELLED'
    assert state['terminal']['reason'] == \
        'operator_cancel_confirmed_during_preflight'
    assert f.launches() == 0
    assert not f.claim_exists()          # slot NOT consumed
    assert f.stop_path().exists()
    # re-wait: terminal re-report, still no launch
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (0, False)
    assert f.state_value() == 'CANCELLED'
    assert f.launches() == 0


def test_n1_l1_cancel_between_claim_and_launch_prevents_launch(tmp_path):
    """L1 boundary B3: a cancel confirmed AFTER the claim write but
    BEFORE the launch invocation is still before the irreversible
    boundary - the launch is prevented; the claim stays consumed with a
    recorded no-launch prevention (CANCELLED, launch_invoked=false), and
    re-waits re-report CANCELLED (they do NOT degrade to lost-ack)."""
    f = InProcSynth(tmp_path / 'l1-postclaim')
    original_write_claim = f.m.Linker.write_claim

    def write_claim_then_cancel(self_l):
        claim_id = original_write_claim(self_l)
        # the cancel reads PRECHECK state (CLAIMED not yet persisted) so
        # it confirms: marker + CANCELLED state - deterministically ahead
        # of this instance's CLAIMED write
        other = f.m.Linker(f.binding_path, INPROC_IN)
        assert other.cmd_cancel('cancel raced ahead of the CLAIMED write') == 0
        return claim_id

    f.m.Linker.write_claim = write_claim_then_cancel
    code, cont = f.cycle()
    assert (code, cont) == (0, False)
    state = f.state()
    assert state['state'] == 'CANCELLED'
    assert state['terminal']['reason'] == \
        'operator_cancel_confirmed_after_claim'
    assert state['launch_invoked'] is False
    assert f.launches() == 0
    claim = json.loads(f.claim_text())
    assert state['claim']['claim_id'] == claim['claim_id']
    assert f.claim_exists()              # slot consumed, bound to record
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (0, False)
    assert f.state_value() == 'CANCELLED'
    assert f.launches() == 0


def test_n1_l1_cancel_during_launch_cannot_revoke_started(tmp_path):
    """L1 boundary B4 ('cancel too late' control): a cancel confirmed
    while the launch child is ALREADY running cannot revoke anything -
    the launch outcome stands (STARTED for rc0 + evidence) and re-waits
    stay STARTED with exactly one launch."""
    f = InProcSynth(tmp_path / 'l1-toolate')

    def late_cancel_marker():
        f.stop_path().write_text('late cancel during launch\n',
                                 encoding='utf-8')

    f.launch_hook = late_cancel_marker
    code, cont = f.cycle()
    assert (code, cont) == (0, False)
    assert f.state_value() == 'STARTED'
    assert f.launches() == 1
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (0, False)
    assert f.state_value() == 'STARTED'
    assert f.launches() == 1


# -- L2: window/deadline crossed during the elapsed step -------------------

def test_n1_l2_window_crossed_during_preflight(tmp_path):
    """L2 injection: the clock crosses latest while the preflight runs.
    The post-preflight gate re-reads the clock FRESH and stops: EXPIRED
    (window_closed), no claim, no launch."""
    f = InProcSynth(tmp_path / 'l2-preflight')
    f.preflight_hook = lambda: setattr(
        f.linker, 'synthetic_clock', INPROC_LATE)
    code, cont = f.cycle()
    assert (code, cont) == (4, False)
    state = f.state()
    assert state['state'] == 'EXPIRED'
    assert state['terminal']['reason'] == 'window_crossed_during_preflight'
    assert f.launches() == 0
    assert not f.claim_exists()
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (4, False)
    assert f.state_value() == 'EXPIRED'


def test_n1_l2_window_crossed_after_claim_no_launch(tmp_path):
    """L2 at boundary B3: latest crossed between the claim and the launch
    invocation -> EXPIRED with the launch prevented (launch_invoked
    false, claim consumed) and re-reported on re-wait."""
    f = InProcSynth(tmp_path / 'l2-postclaim')
    original_write_claim = f.m.Linker.write_claim

    def write_claim_then_cross(self_l):
        claim_id = original_write_claim(self_l)
        f.linker.synthetic_clock = INPROC_LATE
        return claim_id

    f.m.Linker.write_claim = write_claim_then_cross
    code, cont = f.cycle()
    assert (code, cont) == (4, False)
    state = f.state()
    assert state['state'] == 'EXPIRED'
    assert state['terminal']['reason'] == 'window_crossed_after_claim'
    assert state['launch_invoked'] is False
    assert f.launches() == 0
    assert f.claim_exists()
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (4, False)
    assert f.state_value() == 'EXPIRED'
    assert f.launches() == 0


def test_n1_l2_deadline_crossed_during_preflight(tmp_path):
    """L2 variant: the TOTAL deadline (not just the window) crossed
    during the preflight -> EXPIRED at the post-preflight gate."""
    f = InProcSynth(tmp_path / 'l2-deadline')
    f.preflight_hook = lambda: setattr(
        f.linker, 'synthetic_clock', INPROC_PAST_DEADLINE)
    code, cont = f.cycle()
    assert (code, cont) == (4, False)
    state = f.state()
    assert state['state'] == 'EXPIRED'
    assert state['terminal']['reason'] == \
        'past_total_deadline_during_preflight'
    assert f.launches() == 0
    assert not f.claim_exists()


def test_n1_l2_stale_go_report_rejected(tmp_path):
    """L2 adjacency ('a previous GO never substitutes the live launch
    boundary'): this invocation's preflight exits 0 WITHOUT writing the
    report; a GO report left over from an earlier cycle (old mtime) is
    rejected as UNKNOWN preflight_report_stale - no claim, no launch."""
    f = InProcSynth(tmp_path / 'l2-stale-go')
    f.report_path.write_text(json.dumps(
        {'schema': 'pal-rv05-preflight-v1', 'overall': 'GO',
         'unmet_ids': [], 'checks': []}) + '\n', encoding='utf-8')
    old = time.time() - 3600.0
    os.utime(f.report_path, (old, old))
    f.write_report = False
    code, cont = f.cycle()
    assert (code, cont) == (5, False)
    state = f.state()
    assert state['state'] == 'UNKNOWN'
    assert state['terminal']['reason'] == 'preflight_report_stale'
    assert f.launches() == 0
    assert not f.claim_exists()


def test_n1_l2_pin_drift_during_preflight_rejected(tmp_path):
    """Window-and-pin pass condition: a pinned file changes while the
    preflight runs -> the post-preflight gate re-verifies pins and stops
    NO_GO before the claim."""
    f = InProcSynth(tmp_path / 'l2-pindrift')
    f.preflight_hook = lambda: Path(
        f.binding['generator']['path']).write_text(
        'CHANGED DURING PREFLIGHT\n', encoding='utf-8')
    code, cont = f.cycle()
    assert (code, cont) == (2, False)
    state = f.state()
    assert state['state'] == 'NO_GO'
    assert state['terminal']['reason'] == 'binding_drift_at_post_preflight'
    drifted = state['checks'][-1]['detail']['drifted']
    assert any(entry.startswith('generator:sha256(') for entry in drifted)
    assert f.launches() == 0
    assert not f.claim_exists()


# -- L3: receipts record their producing outcome; never upgraded ----------

def test_n1_l3_failed_receipt_never_upgrades_on_rewait(tmp_path):
    """L3 injection: the launch stub exits rc7 (writes NO_GO + a
    LAUNCH_FAILED receipt). The next wait must re-report the RECORDED
    failure - never STARTED, never re-issued, claim/receipt untouched."""
    f = InProcSynth(tmp_path / 'l3-rc7')
    f.launch_rc = 7
    f.make_campaign = False
    code1, cont1 = f.cycle()
    assert (code1, cont1) == (2, False)
    state1 = f.state()
    assert state1['state'] == 'NO_GO'
    assert state1['terminal']['reason'] == 'launch_command_nonzero_exit: rc=7'
    receipt = json.loads(f.receipt_text())
    assert receipt['outcome'] == 'LAUNCH_FAILED'
    assert receipt['launch_returncode'] == 7
    claim_before = sha256_file(f.claim_path())
    receipt_before = sha256_file(f.receipt_path())
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (2, False)
    state2 = f.state()
    assert state2['state'] == 'NO_GO'            # NOT healed to STARTED
    assert 'receipt-recorded' in json.dumps(state2['checks'][-1])
    assert f.launches() == 1                     # never re-issued
    assert sha256_file(f.claim_path()) == claim_before
    assert sha256_file(f.receipt_path()) == receipt_before


def test_n1_l3_legacy_and_foreign_receipts_rejected(tmp_path):
    """L3 variants: receipts that are legacy (no outcome), foreign
    (wrong claim/task/schema) or self-inconsistent (outcome STARTED with
    rc7) are all rejected to UNKNOWN/NO_GO - none is ever accepted as
    STARTED, and no launch is attempted for any of them."""
    f = InProcSynth(tmp_path / 'l3-foreign')
    claim_id = f.linker.write_claim()
    base = {'schema': f.m.SCHEMA_STARTED_RECEIPT,
            'task_id': f.binding['task_id'],
            'claim_id': claim_id,
            'binding_sha256': sha256_file(f.binding_path),
            'frozen': dict(f.binding['frozen']),
            'launch_returncode': 0}
    variants = {
        'outcome_missing': (dict(base), 5),
        'outcome_failed': ({**base, 'outcome': 'LAUNCH_FAILED'}, 2),
        'outcome_started_rc7': ({**base, 'outcome': 'STARTED',
                                 'launch_returncode': 7}, 5),
        'claim_mismatch': ({**base, 'outcome': 'STARTED',
                            'claim_id': '0' * 32}, 5),
        'wrong_task': ({**base, 'outcome': 'STARTED',
                        'task_id': 'SOME_OTHER_TASK'}, 5),
        'wrong_schema': ({**base, 'outcome': 'STARTED',
                          'schema': 'pal-rv05-linker-started-receipt-v0'}, 5)}
    for name, (doc, expected_code) in variants.items():
        f.receipt_path().write_text(json.dumps(doc) + '\n', encoding='utf-8')
        code, cont = f.cycle()
        assert f.state_value() != 'STARTED', name
        assert code == expected_code, name
        assert f.launches() == 0, name


# -- L4: rc0 is not start evidence -----------------------------------------

def test_n1_l4_rc0_without_campaign_is_unknown(tmp_path):
    """L4 injection: the launch stub exits 0 but the bound campaign was
    never created -> outcome LAUNCH_UNKNOWN, state UNKNOWN (never
    STARTED), and re-waits keep UNKNOWN without a second launch."""
    f = InProcSynth(tmp_path / 'l4-no-campaign')
    f.launch_rc = 0
    f.make_campaign = False
    code, cont = f.cycle()
    assert (code, cont) == (5, False)
    state = f.state()
    assert state['state'] == 'UNKNOWN'
    assert 'campaign_json_absent_at_bound_target' in \
        state['terminal']['reason']
    receipt = json.loads(f.receipt_text())
    assert receipt['outcome'] == 'LAUNCH_UNKNOWN'
    assert receipt['launch_returncode'] == 0
    assert f.launches() == 1
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (5, False)
    assert f.state_value() == 'UNKNOWN'
    assert f.launches() == 1


def test_n1_l4_rc0_invalid_or_foreign_campaign_is_unknown(tmp_path):
    """L4 variants: rc0 with (a) a corrupt campaign.json and (b) a
    campaign.json carrying a FOREIGN task_id are both insufficient start
    evidence -> UNKNOWN, never STARTED."""
    # (a) corrupt campaign.json
    f = InProcSynth(tmp_path / 'l4-corrupt-json')
    f.make_campaign = False

    def write_corrupt_campaign():
        f.new_campaign.mkdir(parents=True, exist_ok=True)
        (f.new_campaign / 'campaign.json').write_text(
            '{"schema": "pal-soak-campaign-v1", "reas', encoding='utf-8')

    f.launch_hook = write_corrupt_campaign
    code, cont = f.cycle()
    assert (code, cont) == (5, False)
    state = f.state()
    assert state['state'] == 'UNKNOWN'
    assert 'campaign_json_not_valid_record' in state['terminal']['reason']
    assert f.launches() == 1

    # (b) campaign.json with a foreign task_id
    g = InProcSynth(tmp_path / 'l4-foreign-task')
    g.make_campaign = False

    def write_foreign_task_campaign():
        g.new_campaign.mkdir(parents=True, exist_ok=True)
        (g.new_campaign / 'campaign.json').write_text(json.dumps(
            {'schema': 'pal-soak-campaign-v1', 'task_id': 'FOREIGN_TASK'})
            + '\n', encoding='utf-8')

    g.launch_hook = write_foreign_task_campaign
    code2, cont2 = g.cycle()
    assert (code2, cont2) == (5, False)
    assert g.state_value() == 'UNKNOWN'
    assert 'campaign_task_mismatch' in g.terminal_reason()
    assert g.launches() == 1


# -- claim full-write confirmation ------------------------------------------

def test_n1_claim_short_write_is_unknown_no_launch(tmp_path):
    """Claim short-write rejection (B2): a claim write that cannot be
    confirmed complete (synthetic partial write + failure) leaves a
    partial file that is PRESERVED, moves the state to UNKNOWN
    (claim_write_unconfirmed), never launches, and is never re-attempted
    (later waits adjudicate the corrupted claim to UNKNOWN)."""
    f = InProcSynth(tmp_path / 'claim-shortwrite')
    original_write_claim = f.m.Linker.write_claim

    def claim_with_short_write(self_l):
        real_write = os.write

        def short_write(fd, data):
            buf = bytes(data)
            real_write(fd, buf[:max(1, len(buf) // 2)])
            raise OSError('synthetic short-write failure')

        os.write = short_write
        try:
            return original_write_claim(self_l)
        finally:
            os.write = real_write

    f.m.Linker.write_claim = claim_with_short_write
    code, cont = f.cycle()
    assert (code, cont) == (5, False)
    state = f.state()
    assert state['state'] == 'UNKNOWN'
    assert 'claim_write_unconfirmed' in state['terminal']['reason']
    assert f.launches() == 0
    partial = f.claim_path().read_bytes()
    assert 0 < len(partial)           # partial evidence preserved, not deleted
    claim_before = sha256_file(f.claim_path())
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (5, False)
    assert f.state_value() == 'UNKNOWN'
    assert f.launches() == 0
    assert sha256_file(f.claim_path()) == claim_before  # never patched


# -- legal controls (deterministic, in-process) -----------------------------

def test_n1_control_normal_start_records_boundary_gates(tmp_path):
    """Legal control: with no cancel, no window crossing and no drift,
    both boundary gates pass and the normal PRECHECK -> CLAIMED -> STARTED
    path runs ONCE; the receipt records outcome STARTED, rc0, the
    boundary-gate log and the campaign evidence; re-wait is idempotent."""
    f = InProcSynth(tmp_path / 'ctl-normal')
    code, cont = f.cycle()
    assert (code, cont) == (0, False)
    state = f.state()
    assert state['state'] == 'STARTED'
    hops = [t['to'] for t in state['transitions']]
    assert hops == ['ARMED_WAITING', 'PRECHECK', 'CLAIMED', 'STARTED']
    receipt = json.loads(f.receipt_text())
    assert receipt['outcome'] == 'STARTED'
    assert receipt['launch_returncode'] == 0
    assert receipt['boundary_gate'] == {'post_preflight': 'pass',
                                        'post_claim': 'pass'}
    assert receipt['campaign_evidence']['campaign_json_exists'] is True
    claim = json.loads(f.claim_text())
    assert receipt['claim_id'] == claim['claim_id']
    assert f.launches() == 1
    code2, cont2 = f.cycle()
    assert (code2, cont2) == (0, False)
    assert f.state_value() == 'STARTED'
    assert f.launches() == 1


def test_n1_control_before_window_inproc(tmp_path):
    """Legal control: before the window earliest nothing runs at all -
    not even the preflight."""
    f = InProcSynth(tmp_path / 'ctl-before-window')
    f.linker.synthetic_clock = INPROC_BEFORE
    code, cont = f.cycle()
    assert (code, cont) == (0, True)
    assert f.state_value() == 'ARMED_WAITING'
    assert f.events == []
    assert not f.claim_exists()


def test_n1_control_cancel_before_cycle_inproc(tmp_path):
    """Legal control: a cancel confirmed before any preflight runs stops
    the cycle at the ordinary pre-preflight check with zero stub calls."""
    f = InProcSynth(tmp_path / 'ctl-cancel-first')
    assert f.linker.cmd_cancel('stop before anything') == 0
    code, cont = f.cycle()
    assert (code, cont) == (0, False)
    assert f.state_value() == 'CANCELLED'
    assert f.events == []
    assert not f.claim_exists()
