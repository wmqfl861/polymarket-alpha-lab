"""PX-03 deterministic failure and recovery experiments for the soak driver.

PAL_PARALLEL_REVIEW_20260921_V2 lane PX-03 (plan section 7): deterministic
fault timing at the driver's state-save / restore / segment / lock / stop
boundaries, exercised only on this file's own disposable campaign copies.

Expected recovery states are computed by the INDEPENDENT small model at the
top of this file (``RecoveryOracle``). The oracle is derived from the
driver's documented recovery contract only (module docstring of
tests/support/soak_driver.py plus the frozen soak_audit semantics):
every round directory a dead controller leaves behind must end in one of
the four final states; a record that is unreadable or was never written is
'unknown' (counted exactly once per campaign via the sidecar); a record
without a final state is rewritten 'interrupted'; no round number is ever
handed out twice. The oracle shares no code with the driver and is never
fed values produced by the driver's recovery implementation.

Determinism policy: every fault fires at a fixed call boundary selected by
file name and call ordinal (monkeypatched write seams), at a rendezvous
barrier (stop vs checkpoint), or behind an observed file-existence
precondition (subprocess controller kill). No random sleep is used to claim
that a race has been covered.

Process ownership: every process spawned here is a short-lived synthetic
Python child created and reaped by this file. Nothing scans, signals, or
terminates processes by PID or image name, and no process owned by anyone
else is touched.

Properties that would need a real database, a real power-loss, or foreign
processes to prove are NOT_RUN here (see the PX-03 lane report); no database
or provider is contacted by this file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from tests.support import soak_driver as drv

DRIVER = Path(drv.__file__).resolve()

# ---------------------------------------------------------------------------
# owned synthetic scenario snippets
# ---------------------------------------------------------------------------

ECHO = ('import sys,json\n'
        'p=json.loads(sys.stdin.buffer.read())\n'
        'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"]}))\n')

ECHO_MARKER = ('import sys,json,os\n'
               'p=json.loads(sys.stdin.buffer.read())\n'
               'open(os.path.join(p["tmp_dir"],"marker.txt"),"w").write("kept")\n'
               'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"]}))\n')

if os.name == 'nt':
    SLEEP_LOCK = ('import sys,json,time,os,msvcrt\n'
                  'p=json.loads(sys.stdin.buffer.read())\n'
                  'h=open(os.path.join(p["tmp_dir"],"child.lock"),"a+")\n'
                  'msvcrt.locking(h.fileno(),msvcrt.LK_NBLCK,1)\n'
                  'sys.stdout.write(json.dumps({"echo_round":p["round"],'
                  '"echo_seed":p["sub_seed"]}))\n'
                  'sys.stdout.flush()\n'
                  'time.sleep(120)\n')
else:
    SLEEP_LOCK = ('import sys,json,time,os,fcntl\n'
                  'p=json.loads(sys.stdin.buffer.read())\n'
                  'h=open(os.path.join(p["tmp_dir"],"child.lock"),"a+")\n'
                  'fcntl.flock(h.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)\n'
                  'sys.stdout.write(json.dumps({"echo_round":p["round"],'
                  '"echo_seed":p["sub_seed"]}))\n'
                  'sys.stdout.flush()\n'
                  'time.sleep(120)\n')

# ---------------------------------------------------------------------------
# local fixtures / builders
# ---------------------------------------------------------------------------


def manifest_with(tmp_path, scenarios):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'scenarios': scenarios}), encoding='utf-8')
    return path


def base_config(manifest, **overrides):
    config = {
        'master_seed': 2026092103,
        'round_period_seconds': 0.1,
        'heartbeat_seconds': 0.1,
        'checkpoint_seconds': 43200.0,
        'progress_summary_seconds': 43200.0,
        'max_unobserved_gap_seconds': 900,
        'scenario_timeout_ms': 20000,
        'cleanup_timeout_ms': 4000,
        'per_round_log_bytes': 1048576,
        'max_stdout_bytes': 1048576,
        'max_stderr_bytes': 65536,
        'max_evidence_bytes': 536870912,
        'max_repro_files': 100,
        'minimum_volume_free_bytes': 0,
        'minimum_valid_rounds': 50,
        'workers': 1,
        'candidate': {'label': 'px03-recovery'},
        'scenario_manifest': str(manifest),
    }
    config.update(overrides)
    return config


def wait_until(predicate, timeout=20.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def try_lock_file(path, timeout=10.0):
    """Exclusively lock an owned child's marker; success proves it exited."""
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    deadline = time.monotonic() + timeout
    while True:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.1)


def start_driver_process(campaign, config_path):
    env = dict(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    if os.name == 'nt':
        env['SystemRoot'] = os.environ['SystemRoot']
    return subprocess.Popen(
        [sys.executable, '-B', str(DRIVER), 'run', '--campaign', str(campaign),
         '--config', str(config_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        cwd=str(DRIVER.parents[2]))


def replace_lock_with_dead_controller_pid(campaign):
    """Restage the crashed controller's lock PID as one of OUR reaped children.

    The in-process crash simulation below leaves ``driver.lock`` naming this
    pytest process, which is alive and would legitimately block a restart.
    A real crash leaves the PID of a process that is now dead; the closest
    faithful restage uses one of this file's own terminated children. Only
    processes created here are involved.
    """
    for _attempt in range(3):
        victim = subprocess.Popen(
            [sys.executable, '-I', '-S', '-c', 'import time; time.sleep(60)'])
        try:
            assert wait_until(lambda: drv._pid_alive(victim.pid) is True,
                              timeout=10) is True
        finally:
            victim.kill()
            victim.wait(timeout=10)
        if drv._pid_alive(victim.pid) is False:
            (campaign / 'driver.lock').write_text(
                json.dumps({'pid': victim.pid, 'wall': 0.0, 'argv': []}),
                encoding='utf-8')
            return victim.pid
    pytest.fail('never observed a reaped own child as not alive')


# ---------------------------------------------------------------------------
# INDEPENDENT small recovery oracle (PX-03)
# ---------------------------------------------------------------------------
# Derived only from the documented contract; shares no code with the driver
# and is never fed driver-produced expectations.

ORACLE_FINAL_STATES = ('passed', 'failed', 'interrupted', 'unknown')


def _parse_json_object(path: Path):
    """Independent JSON reader (deliberately not drv._read_json)."""
    try:
        with open(path, 'rb') as handle:
            document = json.loads(handle.read().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return document if type(document) is dict else None


def _dir_round_number(name: str) -> int:
    digits = name.removeprefix('round-').split('.')[0]
    try:
        return int(digits)
    except ValueError:
        return 0


class RecoveryOracle:
    """Pure model of the documented post-crash recovery state.

    observe() walks the campaign disk exactly once, BEFORE any restart, and
    computes: per-state totals, the next round number that may be handed out
    (every round number a dead controller touched is reserved), the next
    segment number, whether all previous segments closed, the final state
    each round record must carry afterwards, and which unknown sidecars a
    recovery pass must create (exactly once per campaign).
    """

    def __init__(self):
        self.totals = {state: 0 for state in ORACLE_FINAL_STATES}
        self.next_round_no = 1
        self.next_segment_no = 1
        self.all_segments_closed = True
        self.expected_disk = {}       # (segment_name, round_dir_name) -> final
        self.sidecars_to_create = []  # (segment_name, round_dir_name)

    def observe(self, campaign: Path) -> 'RecoveryOracle':
        segments_dir = Path(campaign) / 'segments'
        if segments_dir.is_dir():
            for segment in sorted(segments_dir.iterdir()):
                if not (segment.is_dir() and segment.name.startswith('segment-')
                        and segment.name[len('segment-'):].isdigit()):
                    continue
                self.next_segment_no = max(self.next_segment_no,
                                           int(segment.name[len('segment-'):]) + 1)
                if _parse_json_object(segment / 'segment-close.json') is None:
                    self.all_segments_closed = False
                rounds_root = segment / 'rounds'
                if not rounds_root.is_dir():
                    continue
                for round_dir in sorted(rounds_root.iterdir()):
                    if not (round_dir.is_dir() and round_dir.name.startswith('round-')):
                        continue
                    key = (segment.name, round_dir.name)
                    record = _parse_json_object(round_dir / 'round.json')
                    if record is None:
                        # Record unreadable or never written: unknown, and the
                        # number it stood for is still burned (never rerun).
                        number = _dir_round_number(round_dir.name)
                        self.next_round_no = max(self.next_round_no, number + 1)
                        if not (rounds_root / 'unknown'
                                / f'{round_dir.name}.json').exists():
                            self.sidecars_to_create.append(key)
                            self.totals['unknown'] += 1
                        self.expected_disk[key] = 'unknown'
                        continue
                    number = record.get('round') \
                        if type(record.get('round')) is int \
                        else _dir_round_number(round_dir.name)
                    self.next_round_no = max(self.next_round_no, number + 1)
                    final = record.get('final')
                    if final in ORACLE_FINAL_STATES:
                        self.expected_disk[key] = final
                        self.totals[final] += 1
                    else:
                        # Started but never finalized by the dead controller.
                        self.expected_disk[key] = 'interrupted'
                        self.totals['interrupted'] += 1
        return self


def assert_recovery_matches(instance, oracle, campaign):
    """Driver-observed recovery state must equal the independent model."""
    assert instance._totals == oracle.totals
    assert instance._next_round_no == oracle.next_round_no
    assert instance._segment_no == oracle.next_segment_no
    assert instance._recovery['previous_segment_closed'] == oracle.all_segments_closed
    for (seg_name, dir_name), expected in oracle.expected_disk.items():
        base = campaign / 'segments' / seg_name / 'rounds' / dir_name
        if expected == 'unknown':
            sidecar = campaign / 'segments' / seg_name / 'rounds' / 'unknown' \
                / f'{dir_name}.json'
            document = _parse_json_object(sidecar)
            assert document is not None and document['final'] == 'unknown'
        else:
            record = _parse_json_object(base / 'round.json')
            assert record is not None and record['final'] == expected
    for key in oracle.sidecars_to_create:
        seg_name, dir_name = key
        assert (campaign / 'segments' / seg_name / 'rounds' / 'unknown'
                / f'{dir_name}.json').is_file()


def boot_recovery(config, campaign):
    """A restart's boot phase (identity check, lock, recovery, new segment)."""
    fresh = drv.SoakDriver(config, campaign)
    return fresh, fresh._boot()


# ---------------------------------------------------------------------------
# fault-injection seams (fixed call boundaries, no timing guesses)
# ---------------------------------------------------------------------------


class AtomicGate:
    """Raise at the Nth ``_atomic_write_bytes`` call for one file name."""

    def __init__(self, name, ordinal, exception):
        self.name, self.ordinal, self.exception = name, ordinal, exception
        self.seen, self.fired = 0, False

    def __call__(self, real_atomic):
        def gated(path, data):
            if path.name == self.name:
                self.seen += 1
                if self.seen == self.ordinal:
                    self.fired = True
                    raise self.exception
            return real_atomic(path, data)
        return gated


def gate_write_json(monkeypatch, name, ordinal, exception):
    """Raise at the Nth ``SoakDriver._write_json`` call for one file name."""
    real = drv.SoakDriver._write_json
    state = {'seen': 0, 'fired': False}

    def gated(driver, path, obj):
        if path.name == name:
            state['seen'] += 1
            if state['seen'] == ordinal:
                state['fired'] = True
                raise exception
        return real(driver, path, obj)

    monkeypatch.setattr(drv.SoakDriver, '_write_json', gated)
    return state


def gate_driver_log_event(monkeypatch, event_name, exception):
    """Raise when the driver is about to log one specific event."""
    real = drv.SoakDriver._driver_log
    fired = threading.Event()

    def gated(driver, event, **fields):
        if event == event_name:
            fired.set()
            raise exception
        return real(driver, event, **fields)

    monkeypatch.setattr(drv.SoakDriver, '_driver_log', gated)
    return fired


def read_log_events(campaign):
    events = []
    path = campaign / 'driver.log'
    if path.exists():
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.strip():
                entry = _parse_json_object_from_text(line)
                if entry is not None:
                    events.append(entry['event'])
    return events


def _parse_json_object_from_text(text):
    try:
        document = json.loads(text)
    except ValueError:
        return None
    return document if type(document) is dict else None


# ===========================================================================
# experiments
# ===========================================================================


def test_oracle_runs_standalone_on_an_empty_campaign(tmp_path):
    """The oracle computes without the driver; empty campaign -> cold start."""
    oracle = RecoveryOracle().observe(tmp_path / 'campaign')
    assert oracle.totals == {state: 0 for state in ORACLE_FINAL_STATES}
    assert oracle.next_round_no == 1 and oracle.next_segment_no == 1
    assert oracle.all_segments_closed is True


def test_crash_before_running_record_is_unknown_and_number_reserved(tmp_path, monkeypatch):
    """Interruption BEFORE the state save: round dir exists, record never written.

    The contract ("rounds left running ... finalized ... never rerun under the
    same round number") burns the round number the moment the round directory
    exists, because the frozen audit reads that directory as a round. The
    oracle therefore expects unknown=1 and next_round_no=2.
    """
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    crashed = drv.SoakDriver(config, campaign)
    assert crashed._boot() == drv.EXIT_OK
    gate_write_json(monkeypatch, 'round.json', 1,
                    SystemExit('px03-killed-before-running-record'))
    with pytest.raises(SystemExit):
        crashed._run_round(1)
    round_dir = campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001'
    assert round_dir.is_dir() and not (round_dir / 'round.json').exists()

    replace_lock_with_dead_controller_pid(campaign)
    oracle = RecoveryOracle().observe(campaign)
    assert oracle.totals['unknown'] == 1 and oracle.next_round_no == 2

    fresh, code = boot_recovery(config, campaign)
    try:
        assert code == drv.EXIT_OK
        assert_recovery_matches(fresh, oracle, campaign)
        header = _parse_json_object(
            campaign / 'segments' / 'segment-000002' / 'segment.json')
        assert header['rounds_base'] == oracle.next_round_no
        assert round_dir.is_dir()  # evidence of the crash is preserved
    finally:
        fresh._release_lock()

    # A second recovery pass must not recount the sidecar (counted once).
    second, code2 = boot_recovery(config, campaign)
    try:
        assert code2 == drv.EXIT_OK
        assert second._totals == {state: 0 for state in ORACLE_FINAL_STATES}
        assert second._next_round_no == 2
    finally:
        second._release_lock()


def test_crash_after_running_record_is_recovered_interrupted(tmp_path, monkeypatch):
    """Interruption AFTER the 'running' state save, before the final save."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO_MARKER}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    crashed = drv.SoakDriver(config, campaign)
    assert crashed._boot() == drv.EXIT_OK
    gate_write_json(monkeypatch, 'round.json', 2,
                    SystemExit('px03-killed-before-final-record'))
    with pytest.raises(SystemExit):
        crashed._run_round(1)
    record = _parse_json_object(
        campaign / 'segments' / 'segment-000001' / 'rounds'
        / 'round-000000001' / 'round.json')
    assert record['status'] == 'running' and 'final' not in record
    marker = (campaign / 'segments' / 'segment-000001' / 'rounds'
              / 'round-000000001' / 'tmp' / 'marker.txt')
    assert marker.exists()  # interrupted rounds are never cleaned

    replace_lock_with_dead_controller_pid(campaign)
    oracle = RecoveryOracle().observe(campaign)
    assert oracle.totals == {'passed': 0, 'failed': 0, 'interrupted': 1, 'unknown': 0}

    fresh, code = boot_recovery(config, campaign)
    try:
        assert code == drv.EXIT_OK
        assert_recovery_matches(fresh, oracle, campaign)
        recovered = _parse_json_object(
            campaign / 'segments' / 'segment-000001' / 'rounds'
            / 'round-000000001' / 'round.json')
        assert recovered['classified_by'] == 'recovery'
        assert marker.exists()  # recovery never cleans either
    finally:
        fresh._release_lock()

    second, code2 = boot_recovery(config, campaign)
    try:
        assert code2 == drv.EXIT_OK
        assert second._totals == oracle.totals  # rewritten record recounted once
    finally:
        second._release_lock()


def test_crash_after_final_record_before_confirmation_counts_passed(tmp_path, monkeypatch):
    """Update complete, confirmation not returned.

    The final record is on disk (passed) but the controller died before the
    round_final confirmation log and before passed-round cleanup. Recovery
    must count the round from the disk record and must not need the log.
    """
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO_MARKER}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    crashed = drv.SoakDriver(config, campaign)
    assert crashed._boot() == drv.EXIT_OK
    gate_driver_log_event(monkeypatch, 'round_final',
                          SystemExit('px03-killed-before-confirmation'))
    with pytest.raises(SystemExit):
        crashed._run_round(1)
    record = _parse_json_object(
        campaign / 'segments' / 'segment-000001' / 'rounds'
        / 'round-000000001' / 'round.json')
    assert record['status'] == 'final' and record['final'] == 'passed'
    assert 'round_final' not in read_log_events(campaign)  # confirmation lost
    marker = (campaign / 'segments' / 'segment-000001' / 'rounds'
              / 'round-000000001' / 'tmp' / 'marker.txt')
    assert marker.exists()  # cleanup never ran

    replace_lock_with_dead_controller_pid(campaign)
    oracle = RecoveryOracle().observe(campaign)
    assert oracle.totals == {'passed': 1, 'failed': 0, 'interrupted': 0, 'unknown': 0}

    fresh, code = boot_recovery(config, campaign)
    try:
        assert code == drv.EXIT_OK
        assert_recovery_matches(fresh, oracle, campaign)
        assert 'round_final' not in read_log_events(campaign)  # never forged
    finally:
        fresh._release_lock()


def test_final_record_disk_error_fails_closed_then_recovers_interrupted(tmp_path, monkeypatch):
    """Disk error at the final-record write: fail closed, later recovered."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    driver = drv.SoakDriver(config, campaign)
    assert driver._boot() == drv.EXIT_OK
    gate = AtomicGate('round.json', 2, OSError(28, 'simulated disk full'))
    monkeypatch.setattr(drv, '_atomic_write_bytes', gate(drv._atomic_write_bytes))
    with pytest.raises(drv.EvidenceWriteFailure):
        driver._run_round(1)
    assert gate.fired
    record = _parse_json_object(
        campaign / 'segments' / 'segment-000001' / 'rounds'
        / 'round-000000001' / 'round.json')
    assert record['status'] == 'running'

    replace_lock_with_dead_controller_pid(campaign)
    oracle = RecoveryOracle().observe(campaign)
    fresh, code = boot_recovery(config, campaign)
    try:
        assert code == drv.EXIT_OK
        assert_recovery_matches(fresh, oracle, campaign)
        assert oracle.totals['interrupted'] == 1
    finally:
        fresh._release_lock()


def test_checkpoint_permission_error_fails_closed(tmp_path, monkeypatch):
    """Permission error on the checkpoint write stops the loop as evidence
    failure instead of continuing rounds without checkpoints."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(
        manifest, checkpoint_seconds=0.2, heartbeat_seconds=0.1))
    campaign = tmp_path / 'campaign'
    gate = AtomicGate('checkpoint.json', 1, PermissionError(13, 'simulated denial'))
    monkeypatch.setattr(drv, '_atomic_write_bytes', gate(drv._atomic_write_bytes))
    code = drv.SoakDriver(config, campaign).run()
    assert code == drv.EXIT_EVIDENCE
    close = _parse_json_object(
        campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'evidence_failure'
    assert not (campaign / 'driver.lock').exists()


def test_torn_heartbeat_append_fails_closed_and_inspect_survives(tmp_path, monkeypatch):
    """ENOSPC mid-heartbeat-append leaves a torn line; the driver fails closed
    and the read-only inspect does not choke on the torn tail."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(
        manifest, heartbeat_seconds=0.1, checkpoint_seconds=43200.0))
    campaign = tmp_path / 'campaign'
    real_append = drv._append_line

    def torn_append(path, data):
        if path.name != 'heartbeats.jsonl':
            return real_append(path, data)
        with open(path, 'ab') as handle:  # a real half line on disk
            handle.write(data[:max(1, len(data) // 2)])
            handle.flush()
        raise OSError(28, 'simulated ENOSPC')

    monkeypatch.setattr(drv, '_append_line', torn_append)
    code = drv.SoakDriver(config, campaign).run()
    assert code == drv.EXIT_EVIDENCE
    heartbeats = campaign / 'segments' / 'segment-000001' / 'heartbeats.jsonl'
    lines = [line for line in heartbeats.read_bytes().split(b'\n') if line]
    assert lines and _parse_json_object_from_text(
        lines[-1].decode('utf-8', 'replace')) is None  # torn tail preserved
    report = drv.inspect_campaign(campaign)  # must not raise on the torn line
    assert report['segments'] and report['rounds_total']['passed'] >= 0


def test_campaign_json_collision_fails_boot_as_evidence(tmp_path):
    """A directory squatting where campaign.json must be created is an
    evidence failure at boot, not a silent bypass."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    campaign.mkdir()
    (campaign / 'campaign.json').mkdir()
    assert drv.SoakDriver(config, campaign)._boot() == drv.EXIT_EVIDENCE


def test_torn_checkpoint_and_stale_tmp_are_ignored_by_recovery(tmp_path):
    """Half a checkpoint (torn replace never happened) plus a torn old
    checkpoint.json: recovery must neither read nor delete them, and the
    torn scaffolding must not affect the recovered totals."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    crashed = drv.SoakDriver(config, campaign)
    assert crashed._boot() == drv.EXIT_OK
    crashed._run_round(1)  # completes fully; then the controller "dies"
    segment = campaign / 'segments' / 'segment-000001'
    (segment / 'checkpoint.json').write_bytes(b'{"kind":"checkpoint","seg')
    (segment / 'checkpoint.json.tmp').write_bytes(b'{"kind":"checkp')

    replace_lock_with_dead_controller_pid(campaign)
    oracle = RecoveryOracle().observe(campaign)
    assert oracle.totals['passed'] == 1 and oracle.next_round_no == 2
    assert oracle.all_segments_closed is False

    fresh, code = boot_recovery(config, campaign)
    try:
        assert code == drv.EXIT_OK
        assert_recovery_matches(fresh, oracle, campaign)
        assert (segment / 'checkpoint.json').read_bytes() == b'{"kind":"checkpoint","seg'
        assert (segment / 'checkpoint.json.tmp').read_bytes() == b'{"kind":"checkp'
    finally:
        fresh._release_lock()


def test_stop_file_and_checkpoint_rendezvous_both_survive(tmp_path, monkeypatch):
    """STOP and a checkpoint write arrive at one fixed rendezvous.

    The checkpoint's tmp content is fully written and fsynced; the barrier
    releases the replace and the stop-file write into the same instant. The
    checkpoint must land atomically (valid JSON), the stop must be honored,
    and both evidence artifacts must survive.
    """
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(
        manifest, heartbeat_seconds=0.1, checkpoint_seconds=0.2))
    campaign = tmp_path / 'campaign'
    real_atomic = drv._atomic_write_bytes
    barrier = threading.Barrier(2)
    fired = threading.Event()

    def gated_atomic(path, data):
        if path.name != 'checkpoint.json' or fired.is_set():
            return real_atomic(path, data)
        fired.set()
        tmp = path.with_name(path.name + '.tmp')
        with open(tmp, 'wb') as handle:  # content fully on disk first
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        barrier.wait(timeout=30)        # checkpoint pending, replace not yet done
        os.replace(tmp, path)
        return None

    monkeypatch.setattr(drv, '_atomic_write_bytes', gated_atomic)

    stopper_done = threading.Event()

    def stopper():
        barrier.wait(timeout=30)        # stop arrives while the replace pends
        (campaign / 'stop').write_text('px03-simul-stop', encoding='utf-8')
        stopper_done.set()

    thread = threading.Thread(target=stopper, daemon=True)
    thread.start()
    code = drv.SoakDriver(config, campaign).run()
    assert code == drv.EXIT_OK
    assert stopper_done.wait(timeout=10)
    close = _parse_json_object(
        campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'stop_file'
    assert close['stop_note'] == 'px03-simul-stop'
    checkpoint = _parse_json_object(
        campaign / 'segments' / 'segment-000001' / 'checkpoint.json')
    assert checkpoint is not None and checkpoint['kind'] == 'checkpoint'
    assert not (campaign / 'driver.lock').exists()
    assert not (campaign / 'segments' / 'segment-000001' / 'checkpoint.json.tmp').exists()


def test_reboot_of_a_live_instance_is_refused(tmp_path):
    """Duplicate start claim on the same live instance: the second boot must
    be refused by the lock while the first segment stays intact."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    driver = drv.SoakDriver(config, campaign)
    assert driver._boot() == drv.EXIT_OK
    assert driver._boot() == drv.EXIT_LOCK_BUSY  # own PID is alive
    segments = sorted(path.name for path in (campaign / 'segments').iterdir())
    assert segments == ['segment-000001']  # no double segment from re-boot
    lock = _parse_json_object(campaign / 'driver.lock')
    assert lock['pid'] == os.getpid()
    driver._release_lock()


def test_lock_with_live_foreign_pid_refused_dead_pid_stale_recovered(tmp_path):
    """PID reuse, conservative direction, using only owned synthetic children.

    A lock naming ANY live PID must be refused (the driver cannot prove the
    PID is not a driver), so a reused PID can never admit a second driver.
    Only a provably dead PID releases the lock via the stale path.
    """
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    campaign.mkdir()

    squatter = subprocess.Popen(
        [sys.executable, '-I', '-S', '-c', 'import time; time.sleep(60)'])
    try:
        assert wait_until(lambda: drv._pid_alive(squatter.pid) is True,
                          timeout=10) is True
        (campaign / 'driver.lock').write_text(
            json.dumps({'pid': squatter.pid, 'wall': 0.0, 'argv': []}),
            encoding='utf-8')
        claimer = drv.SoakDriver(config, campaign)
        assert claimer._acquire_lock() == drv.EXIT_LOCK_BUSY
        # the refused claim must not have touched the existing lock
        lock = _parse_json_object(campaign / 'driver.lock')
        assert lock['pid'] == squatter.pid
    finally:
        squatter.kill()
        squatter.wait(timeout=10)

    dead_pid = replace_lock_with_dead_controller_pid(campaign)
    # that helper rewrote the lock with a provably dead owned PID
    assert _parse_json_object(campaign / 'driver.lock')['pid'] == dead_pid
    successor = drv.SoakDriver(config, campaign)
    try:
        assert successor._acquire_lock() == drv.EXIT_OK
        stale = sorted(campaign.glob('driver.lock.stale-*'))
        assert len(stale) == 1 and stale[0].stat().st_size > 0
        new_lock = _parse_json_object(campaign / 'driver.lock')
        assert new_lock['pid'] == os.getpid() and new_lock['pid'] != dead_pid
        events = [entry for entry in (
            _parse_json_object_from_text(line)
            for line in (campaign / 'driver.log').read_text(
                encoding='utf-8', errors='replace').splitlines() if line.strip())
            if entry and entry.get('event') == 'lock_stale_recovered']
        assert events and events[-1]['previous_pid'] == dead_pid
    finally:
        successor._release_lock()


def test_recovery_sidecar_write_failure_fails_boot(tmp_path, monkeypatch):
    """A write failure while recovery itself is finalizing an unreadable
    round must fail the boot (fail closed), leaving no new segment."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    campaign = tmp_path / 'campaign'
    crashed = drv.SoakDriver(config, campaign)
    assert crashed._boot() == drv.EXIT_OK
    crashed._run_round(1)
    record_path = (campaign / 'segments' / 'segment-000001' / 'rounds'
                   / 'round-000000001' / 'round.json')
    record_path.write_bytes(b'{torn')  # unreadable record from the "crash"

    replace_lock_with_dead_controller_pid(campaign)
    real_atomic = drv._atomic_write_bytes

    def refuse_sidecar(path, data):
        if path.parent.name == 'unknown':
            raise PermissionError(13, 'simulated denial')
        return real_atomic(path, data)

    monkeypatch.setattr(drv, '_atomic_write_bytes', refuse_sidecar)
    fresh = drv.SoakDriver(config, campaign)
    assert fresh._boot() == drv.EXIT_EVIDENCE
    assert sorted(path.name for path in
                  (campaign / 'segments').iterdir()) == ['segment-000001']
    assert not (campaign / 'driver.lock').exists()  # boot released it


@pytest.mark.skipif(os.name!='nt',
                    reason='requires Windows Job-Object KILL_ON_JOB_CLOSE reclamation: '
                           'an abruptly killed controller closes the job handle and the '
                           'OS reaps the owned child; the POSIX start_new_session orphan '
                           'survives and keeps the child lock')
def test_controller_killed_mid_child_recovers_per_oracle(tmp_path):
    """Controller dies first while its owned child is still running (real
    subprocess kill, marker-gated): the job layer reaps the child, recovery
    finalizes the round interrupted, and the number is never reused."""
    manifest = manifest_with(tmp_path, [{'name': 'sleep', 'kind': 'process',
                                         'code': SLEEP_LOCK}])
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(base_config(
        manifest, round_period_seconds=3, minimum_valid_rounds=50,
        heartbeat_seconds=0.2, checkpoint_seconds=43200.0)), encoding='utf-8')
    campaign = tmp_path / 'campaign'
    controller = start_driver_process(campaign, config_path)
    marker = (campaign / 'segments' / 'segment-000001' / 'rounds'
              / 'round-000000001' / 'tmp' / 'child.lock')
    assert wait_until(lambda: marker.exists(), timeout=30) is not None
    controller.kill()  # abrupt controller death; no stop, no close, no finally
    controller.wait(timeout=15)
    lock_fd = try_lock_file(marker, timeout=15)
    assert lock_fd is not None  # job ownership really reaped the owned child
    os.close(lock_fd)
    assert (campaign / 'driver.lock').exists()  # stale lock left behind

    oracle = RecoveryOracle().observe(campaign)
    assert oracle.totals['interrupted'] == 1 and oracle.next_round_no == 2
    assert oracle.all_segments_closed is False

    config = drv.SoakConfig.from_dict(json.loads(config_path.read_text('utf-8')))
    fresh, code = boot_recovery(config, campaign)
    try:
        assert code == drv.EXIT_OK
        assert_recovery_matches(fresh, oracle, campaign)
        header = _parse_json_object(
            campaign / 'segments' / 'segment-000002' / 'segment.json')
        assert header['rounds_base'] == 2
        assert header['recovery']['previous_segment_closed'] is False
    finally:
        fresh._release_lock()


def test_unclosed_segment_after_close_write_failure_is_flagged_by_recovery(
        tmp_path, monkeypatch):
    """OBSERVATION (pinned): if the segment-close write itself fails at
    shutdown, the close failure is swallowed into a printed line and the
    loop's original exit code is returned. The next recovery must at least
    record the segment as not closed instead of pretending continuity."""
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process',
                                         'code': ECHO}])
    config = drv.SoakConfig.from_dict(base_config(manifest, max_rounds=1,
                                                  minimum_valid_rounds=1))
    campaign = tmp_path / 'campaign'
    gate = AtomicGate('segment-close.json', 1, PermissionError(13, 'simulated denial'))
    monkeypatch.setattr(drv, '_atomic_write_bytes', gate(drv._atomic_write_bytes))
    code = drv.SoakDriver(config, campaign).run()
    assert code == drv.EXIT_OK  # pinned current behavior (see lane report)
    assert gate.fired
    segment = campaign / 'segments' / 'segment-000001'
    assert _parse_json_object(segment / 'segment-close.json') is None
    assert _parse_json_object(segment / 'summary.json') is not None

    oracle = RecoveryOracle().observe(campaign)
    assert oracle.all_segments_closed is False
    fresh, boot_code = boot_recovery(config, campaign)
    try:
        assert boot_code == drv.EXIT_OK
        assert_recovery_matches(fresh, oracle, campaign)
        assert fresh._recovery['previous_segment_closed'] is False
    finally:
        fresh._release_lock()
