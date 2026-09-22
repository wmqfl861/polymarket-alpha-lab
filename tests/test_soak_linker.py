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
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
out = None
for i, a in enumerate(args):
    if a == '--json-out' and i + 1 < len(args):
        out = args[i + 1]
sleep = float(os.environ.get('FAKE_PREFLIGHT_SLEEP', '0') or 0)
if sleep:
    time.sleep(sleep)
verdict = os.environ.get('FAKE_PREFLIGHT_VERDICT', 'GO')
unmet = [s for s in os.environ.get('FAKE_PREFLIGHT_UNMET', '').split(',')
         if s]
report = {'schema': 'pal-rv05-preflight-v1', 'overall': verdict,
          'unmet_count': len(unmet), 'unmet_ids': unmet, 'checks': []}
text = json.dumps(report, indent=2)
if out:
    Path(out).write_text(text + '\\n', encoding='utf-8')
print(text)
sys.exit(0 if verdict == 'GO' else 1)
'''

FAKE_LAUNCH = '''\
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
campaign = None
for i, a in enumerate(args):
    if a == '--campaign' and i + 1 < len(args):
        campaign = args[i + 1]
counter = os.environ.get('FAKE_LAUNCH_COUNTER', '')
if counter:
    with open(counter, 'a', encoding='utf-8') as handle:
        handle.write('launch %s pid=%d\\n' % (time.time(), os.getpid()))
if campaign:
    target = Path(campaign)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'campaign.json').write_text(
        json.dumps({'schema': 'pal-soak-campaign-v1', 'synthetic': True}),
        encoding='utf-8')
sys.exit(0)
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
            '# synthetic launcher stand-in (pinned, never executed '
            'directly by the linker tests)\n', encoding='utf-8')
        self.files['preflight'].write_text(FAKE_PREFLIGHT, encoding='utf-8')
        self.files['launch_spec'].write_text(
            json.dumps({'schema': 'pal-rv05-launch-spec-v1',
                        'synthetic': True}), encoding='utf-8')
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
            'launch_argv': [PY, '-I', '-S', '-B',
                            str(self.pkg / 'launch-fake.py'),
                            '--campaign', str(self.new_campaign)],
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
        (self.pkg / 'launch-fake.py').write_text(
            FAKE_LAUNCH, encoding='utf-8')

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
             env_extra=None, timeout=180):
        env = dict(os.environ)
        env.setdefault('FAKE_LAUNCH_COUNTER', str(self.launch_counter))
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            [PY, '-I', '-S', '-B', str(LINKER), 'wait', '--binding',
             str(binding_path or self.binding_path), '--now-utc', now,
             *extra],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout, env=env)
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
        results[tag] = synth.wait(
            IN1, env_extra={'FAKE_PREFLIGHT_SLEEP': '2.5'}, timeout=300)

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
