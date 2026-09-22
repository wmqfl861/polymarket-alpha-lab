"""N4 independent review tests for the RV-05 single-shot auto-link control
(PAL_RV05_CONTROL_SAFETY_20260922, node N4): tests/support/soak_linker.py.

The nine plan counterexamples (L1-L9) converted into POST-FIX pytest
assertions, plus the five legal controls and the remaining fix-wave pass
conditions (stale/old GO reports, mixed/missing-gate forged reports, nonzero
preflight returns, launch return-code evidence, strict launch argv structure,
execution-policy bypass rejection, synthetic hooks in formal bindings, bounded
record reads).

RED/GREEN semantics: these tests assert the FIXED contract from the fix-wave
plan.  On the unfixed historical source (commit b061b7d7) the counterexample
tests fail (RED baseline evidence) while the legal controls pass; after the
N1/N2/N3 fixes are integrated by N0 every test in this module must pass.

Windows interface split (plan requirement): the real process/handle tests
(``TestWin32RealInterface``) probe real spawned children through the module's
own Win32 calls, while the controlled error-injection tests
(``TestWin32ErrorInjection``) patch ``ctypes.WinDLL`` with recording fakes.
The 64-bit-handle claim is therefore NOT established by mocks alone: the real
group runs the module against real 64-bit Windows handles on every Windows CI
run, and the injection group pins the NULL / WAIT_FAILED / 0 / 258 tri-state
mapping plus full-width handle passing at the injection boundary.

Discipline (same as tests/test_soak_linker.py): fully synthetic environments
under pytest tmp_path - no real campaign, no original soak PID, no real
launch package, no STOP file for any campaign, no network, no business roots,
no credentials.  Every spawned process is a test-created synthetic child
(python -I -S -B stub scripts); at most one child subprocess at a time (the
preflight stub's cancel grandchild in the L1 real test is strictly ordered:
it completes before the stub exits, so the parent is never concurrent with
it).  Constants: official_cases_run=0, sandbox_started=false,
activation_authorized=false.
"""
from __future__ import annotations

import ctypes
import importlib.util
import itertools
import json
import os
import subprocess
import sys
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
AFTER = '2026-09-24T13:00:00Z'           # past the window latest

EXIT_OK, EXIT_NO_GO, EXIT_CLAIM_LOST, EXIT_EXPIRED, EXIT_UNKNOWN, \
    EXIT_USAGE, EXIT_BINDING = 0, 2, 3, 4, 5, 6, 7

WIN32_ONLY = pytest.mark.skipif(
    os.name != 'nt', reason='Win32 real-interface and fault-injection '
    'groups are verified on Windows only (plan: real Windows + fault '
    'injection; logic-green on other platforms is not 64-bit ABI proof)')

_MODULE_TAG = itertools.count()


def _epoch(text: str) -> float:
    return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def _parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def _load_linker_module():
    """Fresh module instance per in-process test (isolates module-attribute
    stubbing exactly like the coordinator's pinned repro_linker.py)."""
    spec = importlib.util.spec_from_file_location(
        f'soak_linker_review_mod_{next(_MODULE_TAG)}', LINKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


# ---------------------------------------------------------------------------
# synthetic stub children (python -I -S -B, stdlib only, env-knobbed)
# ---------------------------------------------------------------------------

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
# the spec digest, the argv echo and the generation time). The
# 'execution' config key lets the negative vectors forge one field.
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
# launch stand-in is a real .ps1 executed by the host's canonical System32
# WindowsPowerShell. It reads launch-behavior.json NEXT TO ITSELF
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
    $doc = @{ schema = $schema
              synthetic = ($mode -ne 'badschema') } | ConvertTo-Json
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


class ReviewSynth:
    """Independent synthetic one-shot environment (control dir + pinned
    package + synthetic original campaign + absent planned campaign root)."""

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
        self.files['errata'].write_text('# synthetic errata\n', encoding='utf-8')
        self.files['generator'].write_text(
            '# synthetic generator\n', encoding='utf-8')
        self.files['manifest'].write_text(
            json.dumps({'schema': 'pal-soak-manifest-v1'}), encoding='utf-8')
        self.files['config'].write_text(
            json.dumps({'schema': 'pal-soak-config-v1',
                        'candidate': 'synthetic-review'}), encoding='utf-8')

        self.report_path = self.control / 'preflight-report.json'
        self.launch_counter = self.control / 'launch-counter.txt'
        self.binding_path = self.control / 'linker-binding.json'
        self.binding = self._base_binding()

    def _base_binding(self) -> dict:
        control = self.control
        return {
            'schema': 'pal-rv05-linker-binding-v1',
            'task_id': 'PAL_RV05_CONTROL_SAFETY_20260922_N4_REVIEW',
            **{name: {'path': str(path), 'sha256': sha256_file(path)}
               for name, path in self.files.items()},
            'runtime': {'python_exe': PY, 'sha256': sha256_file(PY)},
            'frozen': {'commit': 'b061b7d7ad51cd21d7a64c84eba0941a1fd08085',
                       'tree': '79ec2d7945f9dd2b492e243521e0c166f8fbebd0'},
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

    def write_powershell_stub(self) -> Path:
        stub = self.pkg / 'powershell.exe'
        stub.write_text('# synthetic powershell stand-in (pinned location '
                        'holder; arm-level tests never execute it)\n',
                        encoding='utf-8')
        return stub

    def plant_stale_go_report(self, age_seconds: float = 7200) -> None:
        """A GO report left over from an EARLIER preflight invocation."""
        report = {'schema': 'pal-rv05-preflight-v1', 'overall': 'GO',
                  'unmet_count': 0, 'unmet_ids': [],
                  'checks': [{'id': i, 'status': 'pass'} for i in (
                      'capacity_reachable', 'windows_kit_present',
                      'frozen_tree_integrity', 'resources_available',
                      'original_driver_exited',
                      'original_segment_close_present',
                      'original_evidence_stable',
                      'original_children_cleaned', 'launch_window')]}
        self.report_path.write_text(
            json.dumps(report, indent=2) + '\n', encoding='utf-8')
        when = time.time() - age_seconds
        os.utime(self.report_path, (when, when))

    def original_exited_clean(self, round_statuses=('failed', 'failed'),
                              stable=True) -> None:
        seg = self.original / 'segments' / 'segment-000001'
        (seg / 'rounds').mkdir(parents=True, exist_ok=True)
        for index, status in enumerate(round_statuses, start=1):
            rd = seg / 'rounds' / f'round-{index:06d}'
            rd.mkdir(exist_ok=True)
            (rd / 'round.json').write_text(json.dumps(
                {'round': index, 'status': status,
                 'sub_seed': 2000 + index}), encoding='utf-8')
        (seg / 'segment-close.json').write_text(json.dumps(
            {'schema': 'pal-soak-segment-close-v1', 'reason': 'complete',
             'rounds': len(round_statuses)}), encoding='utf-8')
        (self.original / 'campaign.json').write_text(json.dumps(
            {'schema': 'pal-soak-campaign-v1',
             'status': 'finished-with-failures'}), encoding='utf-8')
        when = _epoch(IN1) - (2000 if stable else 100)
        for path in sorted(self.original.rglob('*')):
            if path.is_file():
                os.utime(path, (when, when))

    # -- linker driving -----------------------------------------------------

    def arm(self, now: str = BEFORE, binding_path: Path | None = None):
        return run_linker(['arm', '--binding',
                           str(binding_path or self.binding_path),
                           '--now-utc', now])

    def wait(self, now: str, extra=(), binding_path: Path | None = None,
             timeout=300):
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
    env = ReviewSynth(tmp_path)
    env.write_fake_launch_script()
    env.write_binding()
    return env


# ===========================================================================
# legal controls (must stay GREEN on the unfixed source and forever)
# ===========================================================================

class TestLegalControls:

    def test_control_normal_start_once(self, synth):
        """正常启动: original exited clean -> PRECHECK -> CLAIMED -> STARTED
        exactly once with a consistent claim/receipt/campaign triple; a
        second wait is idempotent and never launches again."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        rc, out = synth.wait(IN1)
        assert rc == EXIT_OK, out
        state = synth.state()
        assert state['state'] == 'STARTED'
        assert [t['to'] for t in state['transitions']] == [
            'ARMED_WAITING', 'PRECHECK', 'CLAIMED', 'STARTED']
        claim = json.loads(synth.claim())
        receipt = json.loads(
            (synth.control / 'linker-started.json').read_text('utf-8'))
        assert receipt['claim_id'] == claim['claim_id']
        assert receipt['launch_returncode'] == 0
        assert receipt['campaign_json_written'] is True
        assert (synth.new_campaign / 'campaign.json').exists()
        assert synth.launch_count() == 1
        rc2, out2 = synth.wait(IN2)
        assert rc2 == EXIT_OK, out2
        assert synth.launch_count() == 1

    def test_control_cancel_before_any_cycle(self, synth):
        """提前撤销: cancel while ARMED_WAITING -> CANCELLED; a later wait
        performs no work; neither campaign is ever touched."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        rc, out = run_linker(['cancel', '--binding', str(synth.binding_path),
                              '--now-utc', BEFORE, '--note', 'early stop'])
        assert rc == EXIT_OK, out
        assert synth.state()['state'] == 'CANCELLED'
        rc2, out2 = synth.wait(IN1)
        assert rc2 == EXIT_OK, out2
        assert synth.state()['state'] == 'CANCELLED'
        assert synth.claim() is None
        assert synth.launch_count() == 0
        assert not (synth.original / 'stop').exists()
        assert not (synth.new_campaign / 'stop').exists()
        assert not synth.new_root.exists()

    def test_control_before_window_keeps_waiting(self, synth):
        """未到窗口: wait before earliest stays ARMED_WAITING with no claim
        and no launch even with the original fully exited and stable."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        rc, out = synth.wait(BEFORE)
        assert rc == EXIT_OK, out
        state = synth.state()
        assert state['state'] == 'ARMED_WAITING'
        assert state['checks'][-1]['reason'] == 'window_not_reached'
        assert synth.claim() is None
        assert synth.launch_count() == 0

    def test_control_lost_ack_claim_without_receipt(self, synth):
        """lost-ack: claim consumed but no receipt -> UNKNOWN, claim kept
        byte-identical, never a second launch."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        rc, out = synth.wait(IN1, extra=['--stop-after', 'claim'])
        assert rc == EXIT_OK, out
        assert synth.state()['state'] == 'CLAIMED'
        before = sha256_file(synth.control / 'linker-claim.json')
        rc2, out2 = synth.wait(IN1)
        assert rc2 == EXIT_UNKNOWN, out2
        assert synth.state()['state'] == 'UNKNOWN'
        assert 'lost_ack' in synth.state()['terminal']['reason']
        assert sha256_file(synth.control / 'linker-claim.json') == before
        assert synth.launch_count() == 0
        assert not synth.new_root.exists()

    def test_control_pin_drift_after_arm_is_no_go(self, synth):
        """pin 漂移: a pinned file changes after arm -> NO_GO with the
        drifted field listed; nothing starts."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        synth.files['config'].write_text(
            json.dumps({'schema': 'pal-soak-config-v1',
                        'candidate': 'DRIFTED-REVIEW'}), encoding='utf-8')
        rc, out = synth.wait(IN1)
        assert rc == EXIT_NO_GO, out
        state = synth.state()
        assert state['state'] == 'NO_GO'
        assert 'binding_drift' in state['terminal']['reason']
        assert 'config:sha256' in state['terminal']['reason']
        assert synth.claim() is None
        assert synth.launch_count() == 0

    def test_control_cancel_after_start_refused(self, synth):
        """too-late cancel control: once the one-shot claim is consumed and
        STARTED recorded, cancel is REFUSED (exit 6) - manual adjudication
        only; the state stays STARTED."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        rc, out = synth.wait(IN1)
        assert rc == EXIT_OK, out
        assert synth.state()['state'] == 'STARTED'
        rc2, out2 = run_linker(['cancel', '--binding',
                                str(synth.binding_path), '--now-utc', IN2])
        assert rc2 == EXIT_USAGE, out2
        assert synth.state()['state'] == 'STARTED'
        assert synth.launch_count() == 1


# ===========================================================================
# L1 / L2 / pin-digest-during-preflight: the preflight boundary
# ===========================================================================

class TestPreflightBoundary:

    def test_l1_cancel_during_preflight_blocks_launch(self, synth):
        """L1 (real subprocess combination): a cancel confirmed by a real
        grandchild linker WHILE the parent is blocked inside the bound
        preflight child must be honoured at the last safe point before the
        irreversible launch: no claim, no launch, final state CANCELLED."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        cancel_log = synth.control / 'cancel-grandchild.log'
        synth.preflight_behavior(cancel={
            'binding': str(synth.binding_path),
            'linker': str(LINKER),
            'now': IN1,
            'log': str(cancel_log)})
        rc, out = synth.wait(IN1)
        # the injected cancel itself succeeded (test validity)
        assert 'rc=0' in cancel_log.read_text(encoding='utf-8')
        assert (synth.control / 'linker-stop').exists()
        # ... but must not have been overridden into a start
        assert rc == EXIT_OK, out
        state = synth.state()
        assert state['state'] == 'CANCELLED', state
        assert synth.claim() is None, 'cancel before the irreversible ' \
            'boundary must not consume the one-shot claim'
        assert not (synth.control / 'linker-started.json').exists()
        assert not (synth.new_campaign / 'campaign.json').exists()
        assert synth.launch_count() == 0, \
            'a received cancellation must not consume a new launch'

    def test_preflight_binding_digest_change_blocks_launch(self, synth):
        """预检期间 binding 摘要变化: a pinned file is rewritten while the
        parent is blocked inside the preflight child -> the pins must be
        re-verified before the claim; NO_GO, no launch."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        synth.preflight_behavior(
            mutate_pin=str(synth.files['config']))
        rc, out = synth.wait(IN1)
        assert rc == EXIT_NO_GO, out
        state = synth.state()
        assert state['state'] == 'NO_GO', state
        assert 'drift' in state['terminal']['reason'].lower()
        assert synth.claim() is None
        assert synth.launch_count() == 0
        assert not (synth.new_campaign / 'campaign.json').exists()


class TestInProcessBoundaryInjection:
    """Mid-cycle deterministic hooks through the module-level subprocess
    seam (same injection boundary as the coordinator's pinned repro)."""

    def _harness(self, tmp_path):
        env = ReviewSynth(tmp_path)
        env.write_fake_launch_script()
        env.write_binding()
        env.original_exited_clean()
        module = _load_linker_module()
        real_subprocess = module.subprocess
        events: list[str] = []
        binding = json.loads(env.binding_path.read_text(encoding='utf-8'))

        def stub_run(argv, **_kwargs):
            static = list(binding['preflight_argv'])
            if list(argv[:len(static)]) == static and \
                    len(argv) == len(static) + 2 and \
                    argv[len(static)] == '--execution-id':
                # the runtime preflight argv = static 9-token whitelist +
                # this cycle's fresh execution id; the stub report echoes
                # the execution provenance block the 9a/9a2 consumption
                # contract requires (execution id, spec digest, argv echo,
                # generation time, full PASS gate inventory)
                events.append('preflight')
                execution_id = argv[-1]
                spec_path = static[6]
                now_utc = datetime.now(timezone.utc).strftime(
                    '%Y-%m-%dT%H:%M:%SZ')
                env.report_path.write_text(json.dumps(
                    {'schema': 'pal-rv05-preflight-v1', 'overall': 'GO',
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
                     'generated_at_utc': now_utc}) + '\n',
                    encoding='utf-8')
                if harness.preflight_hook:
                    harness.preflight_hook()
                return SimpleNamespace(returncode=0)
            if list(argv) == list(binding['launch_argv']):
                events.append('launch_stub')
                if harness.make_campaign:
                    env.new_campaign.mkdir(parents=True, exist_ok=True)
                    (env.new_campaign / 'campaign.json').write_text(
                        json.dumps({'schema': 'pal-soak-campaign-v1',
                                    'synthetic': True}), encoding='utf-8')
                return SimpleNamespace(returncode=harness.launch_rc)
            raise AssertionError(
                'unexpected stub argv (no real command was run)')

        module.subprocess = SimpleNamespace(
            run=stub_run, SubprocessError=real_subprocess.SubprocessError)
        linker = module.Linker(env.binding_path, _parse_utc(BEFORE))
        assert linker.cmd_arm() == EXIT_OK
        linker.synthetic_clock = _parse_utc(IN1)

        class _H:
            pass
        harness = _H()
        harness.env, harness.module, harness.linker = env, module, linker
        harness.events, harness.preflight_hook = events, None
        harness.launch_rc, harness.make_campaign = 0, True
        harness.subprocess_real = real_subprocess
        return harness

    def _restore(self, harness):
        harness.module.subprocess = harness.subprocess_real

    def test_l1_cancel_during_preflight_inprocess(self, tmp_path):
        """L1 (in-process barrier, repro parity): cancel lands exactly
        between the preflight report write and the preflight return."""
        h = self._harness(tmp_path)

        def cancel_now():
            other = h.module.Linker(h.env.binding_path, _parse_utc(IN1))
            assert other.cmd_cancel('cancel during preflight') == EXIT_OK
            assert h.env.state()['state'] == 'CANCELLED'
        h.preflight_hook = cancel_now
        try:
            rc = h.linker.cmd_wait(300, 1, 'never')
        finally:
            self._restore(h)
        assert rc == EXIT_OK
        state = h.env.state()
        assert state['state'] == 'CANCELLED', state
        assert 'launch_stub' not in h.events, \
            'a cancellation received before the irreversible boundary must ' \
            'prevent the launch entirely'
        assert h.env.claim() is None
        assert h.env.launch_count() == 0

    def test_l2_window_expired_during_preflight_blocks_launch(self, tmp_path):
        """L2: the clock crosses the window latest while the parent is
        blocked inside preflight -> the window must be re-checked at the
        actual launch boundary: EXPIRED, no claim, no launch."""
        h = self._harness(tmp_path)
        h.preflight_hook = lambda: setattr(
            h.linker, 'synthetic_clock', _parse_utc(AFTER))
        try:
            rc = h.linker.cmd_wait(300, 1, 'never')
        finally:
            self._restore(h)
        assert rc == EXIT_EXPIRED
        state = h.env.state()
        assert state['state'] == 'EXPIRED', state
        assert 'window' in state['terminal']['reason'].lower()
        assert 'launch_stub' not in h.events
        assert h.env.claim() is None
        assert h.env.launch_count() == 0


# ===========================================================================
# preflight report contracts (stale GO / mixed / missing gates / rc mismatch)
# ===========================================================================

class TestPreflightReportContracts:

    def test_stale_go_report_not_consumed_after_rc0(self, synth):
        """旧 GO 文件: the preflight child exits 0 WITHOUT rewriting the
        report; a GO report left over from an earlier invocation sits at the
        report path.  A start must never be authorized from that residue."""
        synth.original_exited_clean()
        synth.plant_stale_go_report()
        assert synth.arm()[0] == EXIT_OK
        synth.preflight_behavior(skip_write=True)
        rc, out = synth.wait(IN1)
        assert rc in (EXIT_NO_GO, EXIT_UNKNOWN), out
        state = synth.state()
        assert state['state'] in ('NO_GO', 'UNKNOWN'), state
        assert synth.claim() is None
        assert synth.launch_count() == 0
        assert not (synth.new_campaign / 'campaign.json').exists()

    def test_preflight_nonzero_rc_with_stale_go_stays_failed(self, synth):
        """非零返回但旧 GO 存在: the preflight child exits NONZERO leaving a
        stale GO report in place -> must stay failed (never launch, never
        retry the next wait into a GO)."""
        synth.original_exited_clean()
        synth.plant_stale_go_report()
        assert synth.arm()[0] == EXIT_OK
        synth.preflight_behavior(rc=1, skip_write=True)
        rc, out = synth.wait(IN1)
        assert rc in (EXIT_NO_GO, EXIT_UNKNOWN), out
        assert synth.state()['state'] in ('NO_GO', 'UNKNOWN')
        assert synth.claim() is None
        assert synth.launch_count() == 0
        rc2, out2 = synth.wait(IN1)
        assert rc2 in (EXIT_NO_GO, EXIT_UNKNOWN), out2
        assert synth.state()['state'] in ('NO_GO', 'UNKNOWN')
        assert synth.launch_count() == 0

    def test_mixed_go_report_with_nonwaitable_unmet_blocks_launch(self, synth):
        """伪造混合预检报告: overall=GO while the report itself lists a
        NON-waitable unmet gate - internally contradictory, must not
        authorize a launch."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        synth.preflight_behavior(verdict='GO',
                                 unmet=['frozen_tree_integrity'])
        rc, out = synth.wait(IN1)
        assert rc in (EXIT_NO_GO, EXIT_UNKNOWN), out
        state = synth.state()
        assert state['state'] in ('NO_GO', 'UNKNOWN'), state
        assert synth.claim() is None
        assert synth.launch_count() == 0

    def test_go_report_missing_gate_inventory_blocks_launch(self, synth):
        """未知缺门禁: overall=GO with an EMPTY checks inventory - the
        report demonstrates no gate set at all and must not authorize a
        launch."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        synth.preflight_behavior(verdict='GO', checks='empty')
        rc, out = synth.wait(IN1)
        assert rc in (EXIT_NO_GO, EXIT_UNKNOWN), out
        state = synth.state()
        assert state['state'] in ('NO_GO', 'UNKNOWN'), state
        assert synth.claim() is None
        assert synth.launch_count() == 0


# ===========================================================================
# launch outcome contracts (L3 / L4)
# ===========================================================================

class TestLaunchOutcomeContracts:

    def test_l3_nonzero_launch_stays_failed_on_rewait(self, synth):
        """L3: launcher rc7 first writes NO_GO; the NEXT wait must not
        reinterpret the failure receipt as STARTED - the terminal verdict,
        the consumed claim and the preserved receipt all stay put."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        synth.launch_behavior(rc=7, mode='skip')
        rc, out = synth.wait(IN1)
        assert rc == EXIT_NO_GO, out
        assert synth.state()['state'] == 'NO_GO'
        claim_before = synth.claim()
        receipt_before = (synth.control / 'linker-started.json'
                          ).read_text(encoding='utf-8')
        rc2, out2 = synth.wait(IN1)
        assert rc2 == EXIT_NO_GO, out2
        state = synth.state()
        assert state['state'] == 'NO_GO', \
            'a failed-launch receipt must never heal into STARTED'
        assert synth.launch_count() == 1, 'no second launch attempt'
        assert synth.claim() == claim_before
        assert (synth.control / 'linker-started.json').read_text(
            encoding='utf-8') == receipt_before

    def test_l4_rc0_without_campaign_is_unknown(self, synth):
        """L4: launcher exit 0 with the bound campaign.json absent is NOT
        proof of a start -> UNKNOWN (insufficient evidence), never STARTED;
        re-observation stays UNKNOWN with the claim consumed."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        synth.launch_behavior(mode='skip')
        rc, out = synth.wait(IN1)
        assert rc == EXIT_UNKNOWN, out
        state = synth.state()
        assert state['state'] == 'UNKNOWN', state
        assert not (synth.new_campaign / 'campaign.json').exists()
        assert synth.claim() is not None, 'evidence (consumed slot) preserved'
        assert synth.launch_count() == 1
        rc2, out2 = synth.wait(IN2)
        assert rc2 == EXIT_UNKNOWN, out2
        assert synth.state()['state'] == 'UNKNOWN'
        assert synth.launch_count() == 1

    def test_l4_rc0_campaign_wrong_schema_is_unknown(self, synth):
        """L4 variant: launcher exit 0 and a campaign.json exists but with
        the WRONG schema - the file identity does not match the bound
        campaign -> UNKNOWN, never STARTED."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        synth.launch_behavior(mode='badschema')
        rc, out = synth.wait(IN1)
        assert rc == EXIT_UNKNOWN, out
        state = synth.state()
        assert state['state'] == 'UNKNOWN', state
        assert (synth.new_campaign / 'campaign.json').exists()
        assert synth.launch_count() == 1
        rc2, out2 = synth.wait(IN2)
        assert rc2 == EXIT_UNKNOWN, out2
        assert synth.state()['state'] == 'UNKNOWN'
        assert synth.launch_count() == 1


# ===========================================================================
# started-receipt hardening on re-wait (forged receipts, short writes)
# ===========================================================================

class TestStartedReceiptHardening:

    def _started(self, synth):
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        rc, out = synth.wait(IN1)
        assert rc == EXIT_OK, out
        assert synth.state()['state'] == 'STARTED'
        return json.loads(
            (synth.control / 'linker-started.json').read_text('utf-8'))

    def test_forged_receipt_wrong_task_not_accepted(self, synth):
        """伪造回执: a receipt carrying the right schema and claim_id but a
        DIFFERENT task_id must not be accepted as a valid STARTED receipt
        on re-wait -> UNKNOWN (lost-ack), no relaunch."""
        receipt = self._started(synth)
        receipt['task_id'] = 'PAL_SOME_OTHER_TASK'
        (synth.control / 'linker-started.json').write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + '\n',
            encoding='utf-8')
        rc, out = synth.wait(IN2)
        assert rc == EXIT_UNKNOWN, out
        assert synth.state()['state'] == 'UNKNOWN'
        assert synth.launch_count() == 1

    def test_receipt_duplicate_keys_rejected(self, synth):
        """重复键: a receipt JSON with a duplicated key (even a benign one
        whose last value matches) must be rejected as invalid on re-wait ->
        UNKNOWN, no relaunch."""
        receipt = self._started(synth)
        raw = json.dumps(receipt, sort_keys=True)
        duplicated = raw[:-1] + \
            ', "%s": "%s"}' % ('claim_id', receipt['claim_id'])
        assert duplicated.count('"claim_id"') == 2
        (synth.control / 'linker-started.json').write_text(
            duplicated, encoding='utf-8')
        rc, out = synth.wait(IN2)
        assert rc == EXIT_UNKNOWN, out
        assert synth.state()['state'] == 'UNKNOWN'
        assert synth.launch_count() == 1

    def test_half_written_claim_stays_unknown(self, synth):
        """短写 claim guard: a truncated (half-written) claim with no
        receipt -> UNKNOWN lost-ack with the bytes kept, never a relaunch."""
        synth.original_exited_clean()
        assert synth.arm()[0] == EXIT_OK
        rc, out = synth.wait(IN1, extra=['--stop-after', 'claim'])
        assert rc == EXIT_OK, out
        claim_path = synth.control / 'linker-claim.json'
        claim_path.write_text(
            claim_path.read_text(encoding='utf-8')[:20], encoding='utf-8')
        rc2, out2 = synth.wait(IN1)
        assert rc2 == EXIT_UNKNOWN, out2
        assert synth.state()['state'] == 'UNKNOWN'
        assert 'lost_ack' in synth.state()['terminal']['reason']
        assert synth.launch_count() == 0


# ===========================================================================
# strict launch argv structure (L5 / L6 / synthetic hooks)
# ===========================================================================

class TestLaunchArgvContract:

    def test_l5_unrelated_command_with_pinned_name_rejected(self, synth):
        """L5 (repro argv): launch_argv runs an unrelated inline command
        while the pinned launcher path appears only as an unused argument.
        String membership must not satisfy the argv contract -> arm exit 7
        before any state exists."""
        def unrelated(binding):
            binding['launch_argv'] = [
                PY, '-I', '-S', '-B', '-c', 'pass',
                '--unused-pinned-name', binding['launcher']['path']]
        rc, out = synth.arm(binding_path=synth.write_binding(unrelated))
        assert rc == EXIT_BINDING, out
        assert 'launch_argv' in out
        assert not (synth.control / 'linker-state.json').exists()

    def test_real_argv_strict_shape_control_arms(self, synth):
        """Legal control: the documented real shape - fixed powershell
        executable, -NoProfile -NonInteractive -ExecutionPolicy
        RemoteSigned, pinned launcher at the -File position, pinned
        SpecPath - arms cleanly. (N4/N0 reconciliation: the interpreter is
        the pinned powershell stub via the optional launch_interpreter
        key; arm-level validation never executes it.)"""
        stub = synth.write_powershell_stub()

        def real_shape(binding):
            binding['launch_interpreter'] = {
                'path': str(stub), 'sha256': sha256_file(stub)}
            binding['launch_argv'] = [
                str(stub), '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'RemoteSigned', '-File',
                binding['launcher']['path'], '-SpecPath',
                binding['launch_spec']['path']]
        rc, out = synth.arm(binding_path=synth.write_binding(real_shape))
        assert rc == EXIT_OK, out
        assert synth.state()['state'] == 'ARMED_WAITING'

    def test_l6_execution_policy_bypass_rejected(self, synth):
        """L6: -ExecutionPolicy Bypass in the real-shaped argv must be
        rejected at arm (exit 7)."""
        stub = synth.write_powershell_stub()

        def bypass(binding):
            binding['launch_argv'] = [
                str(stub), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-NonInteractive', '-File', binding['launcher']['path'],
                '-SpecPath', binding['launch_spec']['path']]
        rc, out = synth.arm(binding_path=synth.write_binding(bypass))
        assert rc == EXIT_BINDING, out
        assert 'ExecutionPolicy' in out or 'launch_argv' in out
        assert not (synth.control / 'linker-state.json').exists()

    def test_l6_bypass_equivalents_rejected(self, synth):
        """L6 equivalents: case variants, abbreviations, -Command and
        -EncodedCommand are all bypass vectors and must all be rejected."""
        stub = synth.write_powershell_stub()
        variants = [
            ['-ExecutionPolicy', 'ByPass'],
            ['-EXECUTIONPOLICY', 'BYPASS'],
            ['-ep', 'bypass'],
            ['-Command', 'Write-Output x'],
            ['-EncodedCommand', 'QQBwAHAAbAA='],
        ]
        for extra in variants:
            def mutate(binding, extra=extra):
                binding['launch_argv'] = [
                    str(stub), '-NoProfile', '-NonInteractive', '-File',
                    binding['launcher']['path'], '-SpecPath',
                    binding['launch_spec']['path'], *extra]
            rc, out = synth.arm(binding_path=synth.write_binding(mutate))
            assert rc == EXIT_BINDING, (extra, out)
            assert not (synth.control / 'linker-state.json').exists(), extra

    def test_argv_with_synthetic_hooks_rejected(self, synth):
        """正式绑定拒绝 synthetic hooks: neither launch_argv nor
        preflight_argv of a formal binding may carry the test-only
        --now-utc / --stop-after hooks."""
        stub = synth.write_powershell_stub()

        def hook_launch(binding):
            binding['launch_argv'] = list(binding['launch_argv']) + [
                '--now-utc', IN1]
        rc, out = synth.arm(binding_path=synth.write_binding(hook_launch))
        assert rc == EXIT_BINDING, out

        def hook_stop(binding):
            binding['launch_argv'] = list(binding['launch_argv']) + [
                '--stop-after', 'claim']
        rc2, out2 = synth.arm(binding_path=synth.write_binding(hook_stop))
        assert rc2 == EXIT_BINDING, out2

        def hook_preflight(binding):
            binding['preflight_argv'] = list(binding['preflight_argv']) + [
                '--now-utc', IN1]
        rc3, out3 = synth.arm(
            binding_path=synth.write_binding(hook_preflight))
        assert rc3 == EXIT_BINDING, out3

        def hook_real_shape(binding):
            binding['launch_argv'] = [
                str(stub), '-NoProfile', '-NonInteractive', '-File',
                binding['launcher']['path'], '-SpecPath',
                binding['launch_spec']['path'], '--now-utc', IN1]
        rc4, out4 = synth.arm(
            binding_path=synth.write_binding(hook_real_shape))
        assert rc4 == EXIT_BINDING, out4

    def test_l5_pinned_launcher_wrong_position_rejected(self, synth):
        """L5 structure: the -File target is an UNPINNED script while the
        pinned launcher path rides along as an unused trailing argument ->
        rejected (execution position, not membership).  The pinned launcher
        being entirely absent is rejected as well (both current and fixed)."""
        stub = synth.write_powershell_stub()
        drifted = synth.pkg / 'launch-drifted.ps1'
        drifted.write_text('# unpinned drifted launcher stand-in\n',
                           encoding='utf-8')

        def unused_argument(binding):
            binding['launch_argv'] = [
                str(stub), '-NoProfile', '-NonInteractive', '-File',
                str(drifted), '-SpecPath', binding['launch_spec']['path'],
                '--unused', binding['launcher']['path']]
        rc, out = synth.arm(
            binding_path=synth.write_binding(unused_argument))
        assert rc == EXIT_BINDING, out

        def absent(binding):
            binding['launch_argv'] = [
                str(stub), '-NoProfile', '-NonInteractive', '-File',
                str(drifted), '-SpecPath', binding['launch_spec']['path']]
        rc2, out2 = synth.arm(binding_path=synth.write_binding(absent))
        assert rc2 == EXIT_BINDING, out2

    def test_l5_argv0_not_fixed_program_rejected(self, synth):
        """L5 structure: argv[0] is some other program (not the fixed
        powershell executable and not the approved synthetic shape) even
        though the pinned launcher sits at the -File position -> rejected."""
        stub = synth.write_powershell_stub()
        other = synth.pkg / 'cmd-standin.exe'
        other.write_text('# synthetic not-the-fixed-program stand-in\n',
                         encoding='utf-8')

        def other_program(binding):
            binding['launch_argv'] = [
                str(other), '-NoProfile', '-NonInteractive', '-File',
                binding['launcher']['path'], '-SpecPath',
                binding['launch_spec']['path']]
        rc, out = synth.arm(
            binding_path=synth.write_binding(other_program))
        assert rc == EXIT_BINDING, out
        assert not (synth.control / 'linker-state.json').exists()


# ===========================================================================
# L7: bounded record reads
# ===========================================================================

class TestBoundedRecords:

    def test_l7_oversize_valid_prefix_rejected(self, tmp_path):
        """L7: a record whose first 64 KiB are a valid JSON document (with
        an illegal trailer, or just one byte over the cap) must NOT be
        accepted as the whole file; a record of exactly the cap stays
        readable (bounded reads keep working)."""
        module = _load_linker_module()
        cap = module.RECORD_MAX_BYTES
        raw = b'{"state": "STARTED"}'

        exact = tmp_path / 'exact.json'
        exact.write_bytes(raw + b' ' * (cap - len(raw)))
        doc, reason = module._read_json_record(exact)
        assert reason == '' and doc == {'state': 'STARTED'}, reason

        over_by_one = tmp_path / 'over-by-one.json'
        over_by_one.write_bytes(raw + b' ' * (cap + 1 - len(raw)))
        doc2, reason2 = module._read_json_record(over_by_one)
        assert doc2 is None and reason2 != '', \
            'a max+1 record must be rejected as over-limit, not truncated ' \
            'into a valid prefix'

        trailer = tmp_path / 'trailer.json'
        trailer.write_bytes(raw + b' ' * (cap - len(raw)) + b'NOT_JSON')
        doc3, reason3 = module._read_json_record(trailer)
        assert doc3 is None and reason3 != '', \
            'a truncated valid prefix must not be treated as the whole file'

    def test_l7_duplicate_keys_in_record_rejected(self, tmp_path):
        """L7/重复键: a record JSON carrying duplicate keys must be
        rejected, not last-wins parsed."""
        module = _load_linker_module()
        path = tmp_path / 'duplicate.json'
        path.write_text('{"state": "A", "state": "B"}', encoding='utf-8')
        doc, reason = module._read_json_record(path)
        assert doc is None and reason != ''


# ===========================================================================
# Windows real interface (real spawned children through the module's own
# Win32 calls - NOT mocks; skips only on non-Windows platforms)
# ===========================================================================

@WIN32_ONLY
class TestWin32RealInterface:

    def test_real_alive_process_probed_alive(self):
        """Real 64-bit ABI path: a real spawned child is probed alive (True)
        with a creation time matching reality - exercises the module's real
        OpenProcess/WaitForSingleObject/GetProcessTimes handle path."""
        module = _load_linker_module()
        child = subprocess.Popen(
            [PY, '-I', '-S', '-B', '-c', 'import time; time.sleep(30)'])
        try:
            assert module._pid_alive(child.pid) is True
            started = module._process_start_utc(child.pid)
            assert started is not None
            delta = abs((datetime.now(timezone.utc) - started)
                        .total_seconds())
            assert delta < 300, delta
        finally:
            child.kill()
            child.wait()

    def test_real_exited_process_probed_not_alive(self):
        """A real exited (reaped) child probes as exited (False), not
        unknown."""
        module = _load_linker_module()
        child = subprocess.Popen([PY, '-I', '-S', '-B', '-c', 'pass'])
        child.wait()
        assert module._pid_alive(child.pid) is False

    def test_real_nonexistent_pid_is_unknown_not_dead(self):
        """L8 real shape: OpenProcess NULL on a pid that does not exist is a
        probe FAILURE - it must classify unknown (None), never fabricate an
        exit (False)."""
        module = _load_linker_module()
        assert module._pid_alive(4000000) is None


# ===========================================================================
# Windows controlled error injection (recording fakes behind
# ctypes.WinDLL - the shape-injection contract, kept clearly separate from
# the real-interface group above)
# ===========================================================================

@WIN32_ONLY
class TestWin32ErrorInjection:

    def _probe(self, openproc, wait, close):
        module = _load_linker_module()
        # N4/N0 reconciliation: the N3 subpatch ABI-declares GetLastError
        # on the kernel32 handle, so the injected fake carries it too
        # (declaration-only at this seam; runtime classification reads
        # ctypes.get_last_error, which the fakes below set deterministically).
        api = SimpleNamespace(OpenProcess=openproc,
                              WaitForSingleObject=wait, CloseHandle=close,
                              GetProcessTimes=lambda *_a: False,
                              GetLastError=lambda *_a: ctypes.get_last_error())
        real_win = ctypes.WinDLL
        ctypes.WinDLL = lambda *_a, **_k: api
        try:
            return module._pid_alive(1234567)
        finally:
            ctypes.WinDLL = real_win

    def test_l8_openprocess_null_is_unknown(self):
        """L8: OpenProcess returning NULL (here with a last error set, both
        access-denied and invalid-parameter shapes) must classify unknown
        (None) - an API failure must not fabricate an exit."""

        def null_access(*_a):
            ctypes.set_last_error(5)      # ERROR_ACCESS_DENIED
            return 0

        def null_invalid(*_a):
            ctypes.set_last_error(87)     # ERROR_INVALID_PARAMETER
            return 0

        for openproc in (null_access, null_invalid):
            assert self._probe(openproc, lambda *_a: 258,
                               lambda *_a: True) is None

    def test_l9_wait_failed_is_unknown(self):
        """L9: WaitForSingleObject returning WAIT_FAILED must classify
        unknown (None), never dead."""

        def wait_failed(*_a):
            ctypes.set_last_error(6)      # ERROR_INVALID_HANDLE
            return 0xFFFFFFFF

        assert self._probe(lambda *_a: 17, wait_failed,
                           lambda *_a: True) is None

    def test_wait_codes_map_alive_and_exited(self):
        """0 = exited (False), 258 WAIT_TIMEOUT = alive (True)."""
        assert self._probe(lambda *_a: 17, lambda *_a: 258,
                           lambda *_a: True) is True
        assert self._probe(lambda *_a: 17, lambda *_a: 0,
                           lambda *_a: True) is False

    def test_inject_64bit_handle_value_preserved(self):
        """64-bit handle width guard at the injection boundary: a handle
        value beyond 32 bits must arrive at WaitForSingleObject/CloseHandle
        at full width (a c_int restype regression would truncate it)."""
        big = 0x100000017
        seen_wait, seen_close = [], []

        def wait(handle, _ms):
            seen_wait.append(handle.value
                             if isinstance(handle, ctypes.c_void_p) else handle)
            return 258

        def close(handle):
            seen_close.append(handle.value
                              if isinstance(handle, ctypes.c_void_p) else handle)
            return True

        assert self._probe(lambda *_a: big, wait, close) is True
        assert seen_wait == [big], hex(seen_wait[0] if seen_wait else 0)
        assert seen_close == [big], hex(seen_close[0] if seen_close else 0)

    def test_handle_released_after_each_probe(self):
        """句柄每次正确关闭 guard: every successful OpenProcess is matched
        by exactly one CloseHandle with the same handle value."""
        closed = []

        def close(handle):
            closed.append(handle.value
                          if isinstance(handle, ctypes.c_void_p) else handle)
            return True

        assert self._probe(lambda *_a: 17, lambda *_a: 258, close) is True
        assert self._probe(lambda *_a: 17, lambda *_a: 0, close) is False
        assert closed == [17, 17]
