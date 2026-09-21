"""Counterexample tests for the LT-01 test-only soak driver itself.

All subprocesses are controlled synthetic Python snippets run through the
project's owned-process layer; no credentials, databases, network, or real
model/market/order access anywhere in this file.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from tests.support import soak_driver as drv

DRIVER = Path(drv.__file__).resolve()


def _wait_for(predicate, timeout=20.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


ECHO = ('import sys,json\n'
        'p=json.loads(sys.stdin.buffer.read())\n'
        'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"],'
        '"value":p["sub_seed"]%97}))\n')

FAIL_AFTER_WRITE = ('import sys,json,os\n'
                    'p=json.loads(sys.stdin.buffer.read())\n'
                    'open(os.path.join(p["tmp_dir"],"junk.bin"),"wb").write(b"j"*64)\n'
                    'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"]}))\n'
                    'sys.exit(3 if p["round"]%2 else 0)\n')

PAD_RECEIPT = ('import sys,json\n'
               'p=json.loads(sys.stdin.buffer.read())\n'
               'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"],'
               '"pad":"x"*100000}))\n')

if os.name == 'nt':
    LOCK_SLEEP = ('import sys,json,time,os,msvcrt\n'
                  'p=json.loads(sys.stdin.buffer.read())\n'
                  'h=open(os.path.join(p["tmp_dir"],"child.lock"),"a+")\n'
                  'msvcrt.locking(h.fileno(),msvcrt.LK_NBLCK,1)\n'
                  'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"]}))\n'
                  'sys.stdout.flush()\n'
                  'time.sleep(120)\n')
else:
    LOCK_SLEEP = ('import sys,json,time,os,fcntl\n'
                  'p=json.loads(sys.stdin.buffer.read())\n'
                  'h=open(os.path.join(p["tmp_dir"],"child.lock"),"a+")\n'
                  'fcntl.flock(h.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)\n'
                  'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"]}))\n'
                  'sys.stdout.flush()\n'
                  'time.sleep(120)\n')


def acquire_lock(path, timeout=15.0):
    """Exclusively lock the grandchild's marker; success proves it exited."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
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


def manifest_with(tmp_path, scenarios):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'scenarios': scenarios}), encoding='utf-8')
    return path


def base_config(manifest, **overrides):
    config = {
        'master_seed': 2026092001,
        'round_period_seconds': 0.3,
        'heartbeat_seconds': 0.15,
        'checkpoint_seconds': 0.3,
        'progress_summary_seconds': 1.0,
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
        'candidate': {'label': 'lt01-selftest'},
        'scenario_manifest': str(manifest),
    }
    config.update(overrides)
    return config


def write_config(tmp_path, config, name='config.json'):
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding='utf-8')
    return path


def start_driver(campaign, config_path, **popen_kwargs):
    env = dict(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    if os.name == 'nt':
        env['SystemRoot'] = os.environ['SystemRoot']
    return subprocess.Popen(
        [sys.executable, '-B', str(DRIVER), 'run', '--campaign', str(campaign),
         '--config', str(config_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(DRIVER.parents[2]),
        **popen_kwargs)


def finish(child, timeout=25.0):
    out, err = child.communicate(timeout=timeout)
    return child.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')


def run_in_process(tmp_path, manifest, **overrides):
    config = drv.SoakConfig.from_dict(base_config(manifest, **overrides))
    campaign = tmp_path / 'campaign'
    driver = drv.SoakDriver(config, campaign)
    code = driver.run()
    return code, campaign


def round_record(campaign, segment, round_no):
    return drv._read_json(campaign / 'segments' / f'segment-{segment:06d}' / 'rounds'
                          / f'round-{round_no:09d}' / 'round.json')


def final_record(campaign, segment, round_no):
    """A round record only once it has reached a final state."""
    record = round_record(campaign, segment, round_no)
    return record if record is not None and record.get('status') == 'final' else None


# ---------- deterministic seeding and configuration ----------

def test_sub_seed_derivation_is_deterministic_and_distinct():
    assert drv.derive_sub_seed(2026092001, 7) == drv.derive_sub_seed(2026092001, 7)
    assert drv.derive_sub_seed(2026092001, 7) != drv.derive_sub_seed(2026092001, 8)
    assert drv.derive_sub_seed(2026092001, 7) != drv.derive_sub_seed(2026092002, 7)
    assert 0 <= drv.derive_sub_seed(2026092001, 7) < 2**64


@pytest.mark.parametrize('changes', [
    {'master_seed': True}, {'master_seed': 0}, {'round_period_seconds': 0.01},
    {'workers': 3}, {'minimum_valid_rounds': 0}, {'per_round_log_bytes': 1048577},
    {'max_stdout_bytes': 10}, {'scenario_timeout_ms': 3600001},
    {'scenario_manifest': ''}, {'candidate': 'x'},
])
def test_invalid_configuration_is_rejected(tmp_path, changes):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    with pytest.raises(drv.SoakConfigError):
        drv.SoakConfig.from_dict(base_config(manifest, **changes))


def test_pid_liveness_sees_a_live_child_and_a_dead_one():
    # A live instance must never read as dead (that would admit a second
    # driver). A dead PID must read as dead unless Windows reused it first;
    # each attempt probes immediately after reaping to dodge reuse races.
    for _attempt in range(3):
        child = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time;time.sleep(5)'])
        try:
            assert _wait_for(lambda: drv._pid_alive(child.pid) is True, timeout=10) is True
        finally:
            child.kill()
            child.wait(timeout=10)
        if drv._pid_alive(child.pid) is False:
            return
    pytest.fail('reaped child never observed as not alive')


# ---------- in-process control behaviors ----------

def test_boot_receipts_round_states_and_thread_cleanup(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    code, campaign = run_in_process(tmp_path, manifest, minimum_valid_rounds=2)
    assert code == drv.EXIT_OK
    identity = drv._read_json(campaign / 'campaign.json')
    assert identity['schema'] == 'pal-soak-campaign-v1'
    assert identity['stop_path'] == str(campaign / 'stop')
    assert identity['driver_sha256'] == drv._sha256_file(DRIVER)
    segment = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment.json')
    assert segment['pid'] == os.getpid() and segment['recovery']['previous_segment_closed'] is True
    for round_no in (1, 2):
        record = round_record(campaign, 1, round_no)
        assert record['final'] == 'passed' and record['receipt']['echo_round'] == round_no
        assert record['receipt']['echo_seed'] == drv.derive_sub_seed(2026092001, round_no)
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'complete' and close['rounds_total']['passed'] == 2
    assert drv._read_json(campaign / 'segments' / 'segment-000001' / 'checkpoint.json')['kind'] == 'checkpoint'
    assert drv._read_json(campaign / 'segments' / 'segment-000001' / 'summary.json')['segment'] == 1
    heartbeats = (campaign / 'segments' / 'segment-000001' / 'heartbeats.jsonl').read_text()
    assert 'rss_measured' in heartbeats and 'volume_free_bytes' in heartbeats
    assert not any(t.name.startswith('soak-') for t in threading.enumerate())
    assert not (campaign / 'driver.lock').exists()


def test_round_log_is_capped_and_marked_truncated(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'pad', 'kind': 'process', 'code': PAD_RECEIPT}])
    code, campaign = run_in_process(tmp_path, manifest, minimum_valid_rounds=1,
                                    per_round_log_bytes=1024)
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1, 1)
    assert record['final'] == 'passed' and record['log_truncated'] is True
    log = campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001' / 'round.log'
    assert log.stat().st_size <= 1024


def test_first_failure_is_preserved_and_never_overwritten(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'fail', 'kind': 'process',
                                         'code': FAIL_AFTER_WRITE}])
    code, campaign = run_in_process(tmp_path, manifest, minimum_valid_rounds=2)
    assert code == drv.EXIT_OK
    first = round_record(campaign, 1, 1)
    second = round_record(campaign, 1, 2)
    if first['final'] != 'failed':  # scenario fails on odd rounds; ensure both ran
        assert second['final'] == 'failed'
    preserved = (campaign / 'first-failure.json').read_text(encoding='utf-8')
    lines = [line for line in preserved.splitlines() if line.strip()]
    assert len(lines) == 1
    recorded = json.loads(lines[0])
    failing = [r for r in (first, second) if r['final'] == 'failed']
    assert recorded['round'] == min(r['round'] for r in failing)
    assert recorded['reason'] in ('nonzero_exit', 'scenario_error')
    assert (campaign / 'first-failure.log').exists()
    repros = list((campaign / 'failures').glob('rep-*.json'))
    assert 1 <= len(repros) <= 2  # identical failures deduplicate to one repro


def test_passed_round_tmp_cleaned_failed_round_preserved(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'mixed', 'kind': 'process',
                                         'code': FAIL_AFTER_WRITE}])
    code, campaign = run_in_process(tmp_path, manifest, minimum_valid_rounds=2)
    assert code == drv.EXIT_OK
    rounds_dir = campaign / 'segments' / 'segment-000001' / 'rounds'
    seen = {'passed': 0, 'failed': 0}
    for record_path in sorted(rounds_dir.glob('round-*/round.json')):
        record = drv._read_json(record_path)
        tmp_dir = record_path.parent / 'tmp'
        if record['final'] == 'passed':
            seen['passed'] += 1
            assert not tmp_dir.exists()
        else:
            seen['failed'] += 1
            assert (tmp_dir / 'junk.bin').exists()
    assert seen == {'passed': 1, 'failed': 1}  # even round passes, odd round fails


def test_signal_dispatch_sets_cooperative_stop_and_interrupts_round(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'sleep', 'kind': 'process', 'code': LOCK_SLEEP}])
    config = drv.SoakConfig.from_dict(base_config(manifest, minimum_valid_rounds=1))
    campaign = tmp_path / 'campaign'
    driver = drv.SoakDriver(config, campaign)
    driver.install_signal_handlers()
    handler = signal.getsignal(signal.SIGINT)
    assert callable(handler)
    handler(signal.SIGINT, None)  # exactly what the OS would deliver for Ctrl+C
    assert driver.stop.is_stopped() and driver._stop_source == 'signal'
    assert driver._signal_name == 'sigint'
    driver.restore_signal_handlers()
    assert signal.getsignal(signal.SIGINT) is not handler
    # A round admitted after the stop is interrupted, never silently dropped;
    # the reused supervision layer refuses to spawn with a pre-set stop.
    assert driver._boot() == drv.EXIT_OK  # boot already opened segment 1
    driver._run_round(1)
    record = round_record(campaign, 1, 1)
    assert record['final'] == 'interrupted' and record['reason'] == 'stopped'
    assert record['receipt'] is None and record['elapsed_ms'] < 5000
    driver._release_lock()


def test_pytest_kind_runs_a_synthetic_pytest_batch(tmp_path):
    target = tmp_path / 'synthetic_case.py'
    target.write_text('def test_one():\n    assert 1 + 1 == 2\n\n\n'
                      'def test_two():\n    assert "lt01" != "production"\n', encoding='utf-8')
    manifest = manifest_with(tmp_path, [{'name': 'batch', 'kind': 'pytest',
                                         'files': [str(target)]}])
    code, campaign = run_in_process(tmp_path, manifest, minimum_valid_rounds=1,
                                    scenario_timeout_ms=60000)
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1, 1)
    assert record['final'] == 'passed'
    assert record['receipt']['tests'] == 2 and record['receipt']['failures'] == 0


def test_pytest_child_pythonpath_is_the_pinned_purelib(tmp_path):
    """The -S child's PYTHONPATH must be this interpreter's own site-packages.

    Regression for the formal-soak failure mode: deriving it from an ambient
    ``import pytest`` lets a user site-packages (which ``site`` orders before
    the interpreter's own and which may hold pytest without the remaining
    pinned dependencies) leak into the child, whose ``-S`` strip leaves
    PYTHONPATH as the only dependency source — tzdata then vanishes and
    every zone-dependent test fails. The pinned purelib must be used even
    when the ambient pytest import resolves somewhere else.
    """
    import sysconfig
    target = tmp_path / 'synthetic_case.py'
    target.write_text('def test_one():\n    pass\n', encoding='utf-8')
    manifest = manifest_with(tmp_path, [{'name': 'batch', 'kind': 'pytest',
                                         'files': [str(target)]}])
    config = drv.SoakConfig.from_dict(base_config(manifest))
    driver = drv.SoakDriver(config, tmp_path / 'campaign')
    scenario = drv.SoakScenario('batch', 'pytest', files=(str(target),))
    spec = driver._scenario_spec(scenario, tmp_path / 'roundtmp')
    env = dict(spec.environment)
    purelib = str(Path(sysconfig.get_paths()['purelib']).resolve())
    assert env['PYTHONPATH'] == purelib
    assert (Path(purelib) / 'pytest' / '__init__.py').is_file()
    # The ambient import resolution must not be the source: when a shadowing
    # user site provides pytest, the two locations differ and the pinned one
    # must still win.
    ambient = str(Path(__import__('pytest').__file__).resolve().parents[1])
    assert env['PYTHONPATH'] != ambient or ambient == purelib


def test_pytest_site_refuses_a_purelib_without_pytest(tmp_path, monkeypatch):
    """A purelib lacking pytest fails loudly instead of degrading the child."""
    import sysconfig
    empty = tmp_path / 'not-a-site'
    empty.mkdir()
    monkeypatch.setattr(sysconfig, 'get_paths',
                        lambda *a, **k: {'purelib': str(empty)})
    with pytest.raises(drv.SoakConfigError) as raised:
        drv._pinned_purelib()
    assert 'soak_pytest_site_invalid' in str(raised.value)


# ---------- driver-as-subprocess control behaviors ----------

def test_stop_file_closes_cleanly_with_receipts(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    config = write_config(tmp_path, base_config(manifest))
    campaign = tmp_path / 'campaign'
    child = start_driver(campaign, config)
    # RED->fix: previously this waited only until round 2's record existed
    # (status 'running'), so on a loaded host the STOP file could land before
    # round 2 finished and rounds_total['passed'] stayed at 1. Waiting for
    # round 2's final state makes the >= 2 passed / clean-close / receipt
    # assertions deterministic; none of them is weakened.
    assert _wait_for(lambda: final_record(campaign, 1, 2), timeout=25) is not None
    segment = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment.json')
    assert segment['pid'] == child.pid and 'soak_driver.py' in ' '.join(segment['argv'])
    heartbeats = campaign / 'segments' / 'segment-000001' / 'heartbeats.jsonl'
    assert _wait_for(lambda: heartbeats.exists() and len(heartbeats.read_text().splitlines()) >= 3,
                     timeout=15) is not None
    (campaign / 'stop').write_text('operator stop for test', encoding='utf-8')
    code, out, err = finish(child)
    assert code == drv.EXIT_OK, (out, err)
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'stop_file' and close['stop_note'] == 'operator stop for test'
    assert close['rounds_total']['passed'] >= 2 and not (campaign / 'driver.lock').exists()
    report = json.loads(subprocess.run(
        [sys.executable, '-B', str(DRIVER), 'inspect', '--campaign', str(campaign)],
        capture_output=True, text=True, check=True,
        env={'SystemRoot': os.environ['SystemRoot']} if os.name == 'nt' else {}).stdout)
    assert report['rounds_total']['passed'] >= 2
    assert report['continuity'] == {'segments': 1, 'broken': False}


def test_stop_file_mid_round_interrupts_and_terminates_owned_child(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'sleep', 'kind': 'process', 'code': LOCK_SLEEP}])
    config = write_config(tmp_path, base_config(
        manifest, round_period_seconds=5, minimum_valid_rounds=50))
    campaign = tmp_path / 'campaign'
    child = start_driver(campaign, config)
    marker = campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001' / 'tmp' / 'child.lock'
    assert _wait_for(lambda: marker.exists(), timeout=25) is not None
    (campaign / 'stop').write_text('', encoding='utf-8')
    code, out, err = finish(child)
    assert code == drv.EXIT_OK, (out, err)
    record = round_record(campaign, 1, 1)
    assert record['final'] == 'interrupted' and record['reason'] == 'stopped'
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'stop_file'
    lock_fd = acquire_lock(str(marker), timeout=10)
    assert lock_fd is not None  # job ownership really reaped the sleeping grandchild
    os.close(lock_fd)


def test_terminal_signal_stops_the_driver_cooperatively(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    config = write_config(tmp_path, base_config(manifest))
    campaign = tmp_path / 'campaign'
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    child = start_driver(campaign, config, creationflags=creationflags)
    assert _wait_for(lambda: round_record(campaign, 1, 2), timeout=25) is not None
    try:
        if os.name == 'nt':
            child.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            child.send_signal(signal.SIGINT)
    except (OSError, ValueError):
        child.kill()
        finish(child)
        pytest.skip('no console group signal delivery in this environment')
    code, out, err = finish(child)
    assert code == drv.EXIT_OK, (out, err)
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'signal'
    assert close['signal'] in ('sigint', 'sigbreak')
    assert not (campaign / 'driver.lock').exists()


def test_second_live_instance_is_refused_without_touching_the_first(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    config = write_config(tmp_path, base_config(manifest))
    campaign = tmp_path / 'campaign'
    first = start_driver(campaign, config)
    assert _wait_for(lambda: round_record(campaign, 1, 1), timeout=25) is not None
    second = start_driver(campaign, config)
    code, out, err = finish(second)
    assert code == drv.EXIT_LOCK_BUSY and 'SOAK_LOCK_BUSY' in (out + err)
    assert f'pid={first.pid}' in (out + err)
    heartbeats = campaign / 'segments' / 'segment-000001' / 'heartbeats.jsonl'
    count_before = len(heartbeats.read_text().splitlines())
    assert _wait_for(lambda: len(heartbeats.read_text().splitlines()) > count_before + 1,
                     timeout=15) is not None  # the first instance is unaffected
    assert first.poll() is None
    (campaign / 'stop').write_text('', encoding='utf-8')
    assert finish(first)[0] == drv.EXIT_OK


@pytest.mark.skipif(os.name!='nt',
                    reason='requires Windows Job-Object KILL_ON_JOB_CLOSE reclamation: '
                           'an abruptly killed parent closes the job handle and the OS '
                           'reaps the owned child; the POSIX start_new_session orphan '
                           'survives and keeps the child lock')
def test_abnormal_exit_recovers_into_a_new_segment_with_new_round_numbers(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'sleep', 'kind': 'process', 'code': LOCK_SLEEP},
                                        {'name': 'echo', 'kind': 'process', 'code': ECHO}])
    config = write_config(tmp_path, base_config(
        manifest, round_period_seconds=3, minimum_valid_rounds=50))
    campaign = tmp_path / 'campaign'
    first = start_driver(campaign, config)
    marker = campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001' / 'tmp' / 'child.lock'
    assert _wait_for(lambda: marker.exists(), timeout=25) is not None
    first.kill()  # abnormal driver death; no stop path, no close record
    first.wait(timeout=15)
    lock_fd = acquire_lock(str(marker), timeout=10)  # parent loss closed the job handle
    assert lock_fd is not None
    os.close(lock_fd)
    assert (campaign / 'driver.lock').exists()  # stale lock left behind
    second = start_driver(campaign, config)
    assert _wait_for(lambda: drv._read_json(
        campaign / 'segments' / 'segment-000002' / 'segment.json'), timeout=25) is not None
    recovered = round_record(campaign, 1, 1)
    assert recovered['final'] == 'interrupted'
    assert recovered['classified_by'] == 'recovery'
    header = drv._read_json(campaign / 'segments' / 'segment-000002' / 'segment.json')
    assert header['recovery']['previous_segment_closed'] is False
    assert header['recovery']['next_round_no'] >= 2 and header['rounds_base'] >= 2
    (campaign / 'stop').write_text('', encoding='utf-8')
    code, out, err = finish(second)
    assert code == drv.EXIT_OK, (out, err)
    close = drv._read_json(campaign / 'segments' / 'segment-000002' / 'segment-close.json')
    assert close['reason'] == 'stop_file'
    stale = list(campaign.glob('driver.lock.stale-*'))
    assert len(stale) == 1 and stale[0].stat().st_size > 0
    used_numbers = []
    for segment in (1, 2):
        for path in (campaign / 'segments' / f'segment-{segment:06d}' / 'rounds'
                     ).glob('round-*/round.json'):
            record = drv._read_json(path)
            if record:
                used_numbers.append(record['round'])
    assert used_numbers and len(used_numbers) == len(set(used_numbers))  # no round number reused


def test_unreadable_round_record_is_classified_unknown_not_crashed(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    config = write_config(tmp_path, base_config(manifest, minimum_valid_rounds=1))
    campaign = tmp_path / 'campaign'
    first = start_driver(campaign, config)
    assert finish(first)[0] == drv.EXIT_OK
    record_path = campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001' / 'round.json'
    record_path.write_bytes(b'{not-json')  # corrupt the final record
    second = start_driver(campaign, config)
    code, out, err = finish(second)
    assert code == drv.EXIT_OK, (out, err)
    sidecar = (campaign / 'segments' / 'segment-000001' / 'rounds' / 'unknown'
               / 'round-000000001.json')
    unknown = drv._read_json(sidecar)
    assert unknown['final'] == 'unknown' and unknown['reason'] == 'record_unreadable'
    close = drv._read_json(campaign / 'segments' / 'segment-000002' / 'segment-close.json')
    assert close['rounds_total']['unknown'] == 1
    report = json.loads(subprocess.run(
        [sys.executable, '-B', str(DRIVER), 'inspect', '--campaign', str(campaign)],
        capture_output=True, text=True, check=True,
        env={'SystemRoot': os.environ['SystemRoot']} if os.name == 'nt' else {}).stdout)
    assert report['rounds_total']['unknown'] == 1


def test_config_mismatch_on_resume_is_refused(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    campaign = tmp_path / 'campaign'
    assert finish(start_driver(campaign, write_config(
        tmp_path, base_config(manifest, minimum_valid_rounds=1), 'a.json')))[0] == drv.EXIT_OK
    code, out, err = finish(start_driver(campaign, write_config(
        tmp_path, base_config(manifest, minimum_valid_rounds=2), 'b.json')))
    assert code == drv.EXIT_CONFIG and 'SOAK_CONFIG_MISMATCH' in (out + err)


# ---------- fail-closed evidence and resource limits ----------

def test_evidence_write_failure_fails_closed(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'fail', 'kind': 'process',
                                         'code': FAIL_AFTER_WRITE}])
    config = write_config(tmp_path, base_config(
        manifest, minimum_valid_rounds=50, round_period_seconds=0.3))
    campaign = tmp_path / 'campaign'
    campaign.mkdir()
    (campaign / 'first-failure.json').mkdir()  # path collision where evidence must go
    child = start_driver(campaign, config)
    code, out, err = finish(child)
    assert code == drv.EXIT_EVIDENCE, (out, err)
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'evidence_failure'
    assert round_record(campaign, 1, 1)['final'] == 'failed'  # round itself was recorded
    assert list((campaign / 'first-failure.json').iterdir()) == []  # collision untouched
    assert not (campaign / 'driver.lock').exists()


def test_total_log_cap_pauses_instead_of_deleting_evidence(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'pad', 'kind': 'process', 'code': PAD_RECEIPT}])
    config = write_config(tmp_path, base_config(
        manifest, per_round_log_bytes=4096, max_evidence_bytes=5000,
        heartbeat_seconds=5.0, minimum_valid_rounds=50))
    campaign = tmp_path / 'campaign'
    child = start_driver(campaign, config)
    code, out, err = finish(child)
    assert code == drv.EXIT_PAUSED, (out, err)
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'paused_logs'
    rounds = list((campaign / 'segments' / 'segment-000001' / 'rounds').glob('round-*'))
    assert rounds and all(path.exists() for path in rounds)  # nothing deleted
    used, measured = drv._tree_bytes(campaign)
    assert measured and used >= 5000  # over cap yet fully preserved


def test_low_volume_free_space_pauses_before_any_round(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    config = write_config(tmp_path, base_config(
        manifest, minimum_volume_free_bytes=2**50))
    campaign = tmp_path / 'campaign'
    child = start_driver(campaign, config)
    code, out, err = finish(child)
    assert code == drv.EXIT_PAUSED, (out, err)
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'paused_disk' and close['rounds_total']['passed'] == 0


def test_inspect_flags_heartbeat_gap_as_broken_continuity(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    config = write_config(tmp_path, base_config(
        manifest, minimum_valid_rounds=2, heartbeat_seconds=0.15))
    campaign = tmp_path / 'campaign'
    assert finish(start_driver(campaign, config))[0] == drv.EXIT_OK
    heartbeats = campaign / 'segments' / 'segment-000001' / 'heartbeats.jsonl'
    lines = heartbeats.read_text(encoding='utf-8').splitlines()
    last = json.loads(lines[-1])
    forged = dict(last, wall=last['wall'] + 3600)
    with open(heartbeats, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(forged, sort_keys=True, separators=(',', ':')) + '\n')
    report = json.loads(subprocess.run(
        [sys.executable, '-B', str(DRIVER), 'inspect', '--campaign', str(campaign)],
        capture_output=True, text=True, check=True,
        env={'SystemRoot': os.environ['SystemRoot']} if os.name == 'nt' else {}).stdout)
    assert report['continuity']['broken'] is True
    assert report['segments'][0]['gaps_over_limit'][0]['gap_seconds'] > 900
