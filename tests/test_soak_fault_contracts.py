"""N3 fault-contract tests for the two designed-failure soak scenarios.

PAL_RV05_CAPACITY_20260921, node N3 (scenario mapping + fault contracts).

13 -> 11 + 2 mapping: the approved nominal corrected-72h manifest keeps the
11 round-passing scenarios (verbatim names/codes/files of the original
formal 13-scenario manifest). The two designed-failure process scenarios
(``fail-nonzero`` and ``fail-output-limit``) are REMOVED from the nominal
rotation but their designed-failure CONTRACTS are asserted here so the
fault coverage does not disappear:

- fail-nonzero  -- designed atomic process failure: the child consumes its
  round payload, marks its designed form, and exits with a fixed non-zero
  code; supervision reports ``research_process_nonzero_exit`` and the round
  is recorded final='failed' reason='nonzero_exit' (historical form in the
  original formal campaign: 10/10 rounds failed exactly this way, stderr 0).
- fail-output-limit -- designed resource-limit failure: the child floods
  stdout past the configured cap; supervision refuses with
  ``research_process_output_limit`` (never read-in-full, never truncated
  into a success) and the round is recorded final='failed'
  reason='output_limit' (historical form: 8/8 rounds failed this way).

Every contract test requires all four elements to PASS:
  (a) the expected underlying error occurs with its FIXED designed form;
  (b) the terminal state is exact: final='failed' + the designed reason,
      receipt absent, never re-labeled 'passed'/'expected' anywhere;
  (c) resource limits are respected: bounded reads, the output cap trips
      before the timeout boundary, and under-cap output is NOT refused
      (exact boundary control at the supervision layer);
  (d) the owned child is cleaned up: it has exited before the call
      returns, proven by exclusively locking the child's own lock file
      (held until process death), which is impossible while the child is
      alive.

These are NEW runs through the frozen supervision layer
(``run_research_process``) and the frozen driver round path
(``SoakDriver._run_round``). The original formal-campaign records are not
read, moved, or rewritten here: old designed failures stay recorded as
failed; nothing is renamed 'expected'. All subprocesses are owned
synthetic children; no credentials, databases, network, or real
model/market/order access anywhere in this file.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time

import pytest

from tests.support import soak_driver as drv
from polymarket_alpha_lab.research_process import (
    ResearchProcessError,
    ResearchProcessSpec,
    run_research_process,
)

if os.name == 'nt':
    CHILD_LOCK = ('import json,os,sys,msvcrt\n'
                  'p=json.loads(sys.stdin.buffer.read())\n'
                  'h=open(os.path.join(p["tmp_dir"],"child.lock"),"a+")\n'
                  'msvcrt.locking(h.fileno(),msvcrt.LK_NBLCK,1)\n'
                  'm=open(os.path.join(p["tmp_dir"],"child.marker"),"w")\n')
else:
    CHILD_LOCK = ('import json,os,sys,fcntl\n'
                  'p=json.loads(sys.stdin.buffer.read())\n'
                  'h=open(os.path.join(p["tmp_dir"],"child.lock"),"a+")\n'
                  'fcntl.flock(h.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)\n'
                  'm=open(os.path.join(p["tmp_dir"],"child.marker"),"w")\n')

# fail-nonzero (canonical N3 form): consume the round payload, take the
# exclusive lock (held until process death), stamp the designed form into
# a separate marker file, then exit with the FIXED designed non-zero code
# 3 -- no stdout receipt, no stderr (matching every original-campaign
# fault round: stderr_bytes=0, receipt=None).
FAIL_NONZERO = CHILD_LOCK + (
    'm.write("fail-nonzero:designed-exit-code=3")\n'
    'm.flush()\n'
    'sys.exit(3)\n')

# fail-output-limit (canonical N3 form): consume the round payload, hold
# the lock, then flood stdout well past the cap and never exit on its own
# before that (safety valve only trips long after the cap must have
# refused). Supervision stops reading at cap+1 and kills the child.
FAIL_OUTPUT_LIMIT = CHILD_LOCK + (
    'm.write("fail-output-limit:designed-stdout-flood")\n'
    'm.flush()\n'
    'chunk=b"x"*65536\n'
    'total=0\n'
    'while True:\n'
    '    sys.stdout.buffer.write(chunk)\n'
    '    sys.stdout.buffer.flush()\n'
    '    total+=len(chunk)\n'
    '    if total>8388608:\n'
    '        break\n'
    'sys.exit(3)\n')

# Nominal control (ok-echo form): a small valid receipt under every cap.
ECHO = ('import sys,json\n'
        'p=json.loads(sys.stdin.buffer.read())\n'
        'sys.stdout.write(json.dumps({"echo_round":p["round"],'
        '"echo_seed":p["sub_seed"],"value":p["sub_seed"]%97}))\n')

PRODUCTION_STDOUT_CAP = 1048576  # frozen driver default; never relaxed here

N3_SEED = 2026092105  # N3 test seed; distinct from formal 2026092103


def acquire_lock(path, timeout=15.0):
    """Exclusively lock the child's marker; success proves it exited."""
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


def n3_config(manifest, **overrides):
    config = {
        'master_seed': N3_SEED,
        'round_period_seconds': 0.1,
        'heartbeat_seconds': 0.1,
        'checkpoint_seconds': 1.0,
        'progress_summary_seconds': 5.0,
        'max_unobserved_gap_seconds': 900,
        'scenario_timeout_ms': 15000,
        'cleanup_timeout_ms': 5000,
        'per_round_log_bytes': 1048576,
        'max_stdout_bytes': PRODUCTION_STDOUT_CAP,
        'max_stderr_bytes': 65536,
        'max_evidence_bytes': 536870912,
        'max_repro_files': 100,
        'minimum_volume_free_bytes': 0,
        'minimum_valid_rounds': 1,
        'workers': 1,
        'candidate': {'label': 'n3-fault-contract'},
        'scenario_manifest': str(manifest),
    }
    config.update(overrides)
    return drv.SoakConfig.from_dict(config)


def boot_driver(tmp_path, manifest, **overrides):
    config = n3_config(manifest, **overrides)
    driver = drv.SoakDriver(config, tmp_path / 'campaign')
    assert driver._boot() == drv.EXIT_OK
    return driver


def round_record(campaign, segment, round_no):
    return drv._read_json(campaign / 'segments' / f'segment-{segment:06d}' / 'rounds'
                          / f'round-{round_no:09d}' / 'round.json')


def direct_spec(tmp_path, code, stdout_cap=PRODUCTION_STDOUT_CAP, timeout_ms=15000):
    """Mirror the driver's process-scenario spec for a direct supervised run."""
    environment = (('SystemRoot', os.environ['SystemRoot']),) if os.name == 'nt' else ()
    python = str(Path(sys.executable).resolve())
    return ResearchProcessSpec(
        argv=(python, '-I', '-S', '-c', code),
        cwd=str(tmp_path),
        environment=environment,
        executable_sha256=sha256(Path(python).read_bytes()).hexdigest(),
        timeout_ms=timeout_ms,
        max_stdin_bytes=16000000,
        max_stdout_bytes=stdout_cap,
        max_stderr_bytes=65536,
        cleanup_timeout_ms=5000,
    )


def direct_payload(tmp_path, round_no=1, sub_seed=12345):
    return json.dumps({'round': round_no, 'sub_seed': sub_seed,
                       'tmp_dir': str(tmp_path)}).encode('utf-8') + b'\n'


# ------------------------------------------------------------------
# fail-nonzero: four-element fault contract (driver-level round)
# ------------------------------------------------------------------

def test_fail_nonzero_fault_contract_four_elements(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'fail-nonzero', 'kind': 'process',
                                         'code': FAIL_NONZERO}])
    driver = boot_driver(tmp_path, manifest)

    # Element (a): the designed underlying failure occurs with its fixed
    # form, asserted directly at the supervision layer first (real owned
    # child, sanitized -I -S environment, exact fixed error code text).
    with pytest.raises(ResearchProcessError) as info:
        run_research_process(spec=direct_spec(tmp_path, FAIL_NONZERO),
                             stdin=direct_payload(tmp_path),
                             allow_process_start=True)
    assert str(info.value) == 'research_process_nonzero_exit'  # fixed form, no diagnostics
    lock = acquire_lock(str(tmp_path / 'child.lock'))
    try:
        assert lock is not None, 'supervision left the designed child alive'
        assert (tmp_path / 'child.marker').read_text(encoding='utf-8') \
            == 'fail-nonzero:designed-exit-code=3'
    finally:
        os.close(lock)

    # Now the same designed failure through the frozen driver round path.
    driver._run_round(1)
    driver._release_lock()
    record = round_record(tmp_path / 'campaign', 1, 1)

    # (a) expected underlying error: designed exit reached, quiet child.
    assert record['scenario'] == 'fail-nonzero'
    assert record['reason'] == 'nonzero_exit'
    assert record['stderr_bytes'] == 0  # designed form: no stderr noise
    assert record['receipt'] is None    # no receipt is ever produced

    # (b) terminal state exact: failed with the designed reason, and it is
    # never re-labeled passed/expected anywhere in the record.
    assert record['status'] == 'final' and record['final'] == 'failed'
    assert 'expected' not in record
    assert driver._totals.get('passed', 0) == 0
    assert driver._totals.get('failed', 0) == 1

    # (c) resource limits respected: far inside the timeout boundary, and
    # the failed round's own evidence stays capped (log header only).
    assert record['elapsed_ms'] < driver.config.scenario_timeout_ms
    log = (tmp_path / 'campaign' / 'segments' / 'segment-000001' / 'rounds'
           / 'round-000000001' / 'round.log')
    assert log.stat().st_size <= driver.config.per_round_log_bytes

    # (d) owned process cleaned up: the round child exited (its lock file in
    # the preserved failed-round tmp is lockable) and no round is in flight.
    round_tmp = (tmp_path / 'campaign' / 'segments' / 'segment-000001'
                 / 'rounds' / 'round-000000001' / 'tmp')
    lock = acquire_lock(str(round_tmp / 'child.lock'))
    try:
        assert lock is not None, 'round child still alive after finalization'
        assert (round_tmp / 'child.marker').read_text(encoding='utf-8') \
            == 'fail-nonzero:designed-exit-code=3'
    finally:
        os.close(lock)
    assert driver._in_flight is False


# ------------------------------------------------------------------
# fail-output-limit: four-element fault contract (driver-level round)
# ------------------------------------------------------------------

def test_fail_output_limit_fault_contract_four_elements(tmp_path):
    manifest = manifest_with(tmp_path, [{'name': 'fail-output-limit', 'kind': 'process',
                                         'code': FAIL_OUTPUT_LIMIT}])
    driver = boot_driver(tmp_path, manifest)

    # Element (a): output over the cap is REFUSED at the supervision layer
    # -- raised as the fixed code, never returned as truncated success.
    with pytest.raises(ResearchProcessError) as info:
        run_research_process(spec=direct_spec(tmp_path, FAIL_OUTPUT_LIMIT),
                             stdin=direct_payload(tmp_path),
                             allow_process_start=True)
    assert str(info.value) == 'research_process_output_limit'  # fixed form
    lock = acquire_lock(str(tmp_path / 'child.lock'))
    try:
        assert lock is not None, 'flooder still alive after refusal'
        assert (tmp_path / 'child.marker').read_text(encoding='utf-8') \
            == 'fail-output-limit:designed-stdout-flood'
    finally:
        os.close(lock)

    # The same designed failure through the frozen driver round path.
    driver._run_round(1)
    driver._release_lock()
    record = round_record(tmp_path / 'campaign', 1, 1)

    # (a) expected underlying error: over-cap flood refused.
    assert record['scenario'] == 'fail-output-limit'
    assert record['reason'] == 'output_limit'
    assert record['receipt'] is None

    # (b) terminal state exact; never re-labeled.
    assert record['status'] == 'final' and record['final'] == 'failed'
    assert 'expected' not in record
    assert driver._totals.get('passed', 0) == 0
    assert driver._totals.get('failed', 0) == 1

    # (c) resource limits respected: the OUTPUT CAP fired, not the timer
    # (the flooder never exits by itself before the cap must refuse), and
    # the round completed well inside the timeout boundary with bounded
    # evidence (round.log holds only the capped header, no flood copy).
    assert record['reason'] != 'timeout'
    assert record['elapsed_ms'] < driver.config.scenario_timeout_ms
    log = (tmp_path / 'campaign' / 'segments' / 'segment-000001' / 'rounds'
           / 'round-000000001' / 'round.log')
    assert log.stat().st_size <= driver.config.per_round_log_bytes

    # (d) owned process cleaned up: the killed flooder's lock file is
    # free; the child is gone before the round was finalized.
    round_tmp = (tmp_path / 'campaign' / 'segments' / 'segment-000001'
                 / 'rounds' / 'round-000000001' / 'tmp')
    lock = acquire_lock(str(round_tmp / 'child.lock'))
    try:
        assert lock is not None, 'killed flooder still holds its lock file'
        assert (round_tmp / 'child.marker').read_text(encoding='utf-8') \
            == 'fail-output-limit:designed-stdout-flood'
    finally:
        os.close(lock)
    assert driver._in_flight is False


# ------------------------------------------------------------------
# Exact cap boundary at the supervision layer: under/at cap is NOT
# refused; one byte over IS refused. Proves the refusal is the designed
# limit, not any output-size sensitivity. (c)-element boundary control.
# ------------------------------------------------------------------

def test_output_cap_boundary_is_exact_at_supervision_layer(tmp_path):
    cap = 4096
    quiet = 'import sys\nsys.stdout.buffer.write(b"x"*%d)\n' % cap
    over = 'import sys\nsys.stdout.buffer.write(b"x"*%d)\n' % (cap + 1)

    result = run_research_process(spec=direct_spec(tmp_path, quiet, stdout_cap=cap),
                                  stdin=b'{}\n', allow_process_start=True)
    assert len(result.stdout) == cap            # exactly at cap: accepted in full
    assert result.elapsed_ms < 15000

    with pytest.raises(ResearchProcessError) as info:
        run_research_process(spec=direct_spec(tmp_path, over, stdout_cap=cap),
                             stdin=b'{}\n', allow_process_start=True)
    assert str(info.value) == 'research_process_output_limit'  # one byte over: refused


# ------------------------------------------------------------------
# 11 + 2 partition semantics in one rotation: nominal rounds pass,
# both designed-fault rounds fail with their own designed reason, and
# no designed failure is ever counted as a passed valid round.
# ------------------------------------------------------------------

def test_nominal_passes_while_both_faults_fail_in_shared_rotation(tmp_path):
    manifest = manifest_with(tmp_path, [
        {'name': 'ok-echo', 'kind': 'process', 'code': ECHO},
        {'name': 'fail-nonzero', 'kind': 'process', 'code': FAIL_NONZERO},
        {'name': 'fail-output-limit', 'kind': 'process', 'code': FAIL_OUTPUT_LIMIT},
    ])
    driver = boot_driver(tmp_path, manifest)
    campaign = tmp_path / 'campaign'

    expected = {'ok-echo': ('passed', None),
                'fail-nonzero': ('failed', 'nonzero_exit'),
                'fail-output-limit': ('failed', 'output_limit')}
    seen = set()
    round_no = 0
    while seen != set(expected) and round_no < 24:
        round_no += 1
        driver._run_round(round_no)
        record = round_record(campaign, 1, round_no)
        want_final, want_reason = expected[record['scenario']]
        assert record['status'] == 'final'
        assert record['final'] == want_final, record
        assert record['reason'] == want_reason, record
        assert 'expected' not in record
        seen.add(record['scenario'])
    driver._release_lock()

    assert seen == set(expected), f'rotation never exercised all three: {seen}'
    assert driver._totals.get('passed', 0) >= 1
    assert driver._totals.get('failed', 0) >= 2
    # The nominal rounds' tmp is cleaned; both designed-fault rounds keep
    # their tmp as failure evidence (matches the original campaign shape).
    rounds_dir = campaign / 'segments' / 'segment-000001' / 'rounds'
    for record_path in sorted(rounds_dir.glob('round-*/round.json')):
        record = drv._read_json(record_path)
        tmp_dir = record_path.parent / 'tmp'
        if record['final'] == 'passed':
            assert not tmp_dir.exists()
        else:
            assert (tmp_dir / 'child.marker').exists()
