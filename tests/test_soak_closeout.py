"""PX-05a synthetic terminal-state tests for tests/support/soak_closeout.py.

Five forged terminal branches (normal end / early exit / half-write /
unknown / deadline) plus stop-file, failure and guard-path coverage. The
closeout program is exercised through its CLI (``settle``/``wait``) and, for
fast loop paths that must not respect the 300s CLI floor, through direct
library calls with interval/stability 0 — the floor itself is asserted as a
CLI guard. Forged campaigns are hand-built minimal directories; no real
driver, no subprocess scenario, no network, no credentials. Every test also
asserts the closeout never wrote inside the (forged) campaign directory and
never created a campaign ``stop`` file.

R5 write-target guard coverage: the historical probe showed ``wait_loop``
accepting a ``--checkpoint-dir`` pointing into the campaign and writing the
original directory. Its counterexample is migrated here (mocked terminal
closeout, exactly the probe's shape) alongside an unmocked variant and the
side-root control: a campaign-inside checkpoint target is refused with zero
campaign writes, while a side-root target still receives its checkpoint.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from tests.support import soak_closeout as clo

FUTURE = '2030-01-01T00:00:00Z'
PAST = '2000-01-01T00:00:00Z'


def utc_iso(wall: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(wall))


def exited_pid() -> int:
    """Spawn a real short-lived child and return its (now dead) PID."""
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait()
    assert proc.returncode == 0
    return proc.pid


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, sort_keys=True) + '\n', encoding='utf-8')


def forge_campaign(root: Path, *, rounds: int = 2, receipt: bool = False,
                   lock_pid: int | None = None, running_round: bool = False,
                   torn_log: bool = False, started_wall: float | None = None,
                   stopped_wall: float | None = None) -> Path:
    """Minimal hand-forged campaign directory (never produced by a driver)."""
    started = time.time() - 100 if started_wall is None else started_wall
    stopped = time.time() - 2 if stopped_wall is None else stopped_wall
    segment = root / 'segments' / 'segment-000001'
    (segment / 'rounds').mkdir(parents=True)
    write_json(root / 'campaign.json', {
        'schema': 'pal-soak-campaign-v1-forged',
        'forged': True, 'note': 'PX-05a synthetic terminal-state fixture'})
    if lock_pid is not None:
        write_json(root / 'driver.lock', {'pid': lock_pid, 'wall': started})
    write_json(segment / 'segment.json', {'segment': 1, 'started_wall': started})
    (segment / 'heartbeats.jsonl').write_text(
        ''.join(json.dumps({'wall': started + n, 'monotonic': float(n)})
                + '\n' for n in (1, 2)), encoding='utf-8')
    finalized = 0
    for n in range(1, rounds + 1):
        round_dir = segment / 'rounds' / f'round-{n:09d}'
        round_dir.mkdir()
        (round_dir / 'tmp').mkdir()
        if running_round and n == rounds:
            write_json(round_dir / 'round.json', {
                'round': n, 'segment': 1, 'status': 'running',
                'sub_seed': 1000 + n, 'scenario': 'forged'})
        else:
            finalized += 1
            write_json(round_dir / 'round.json', {
                'round': n, 'segment': 1, 'status': 'final', 'final': 'passed',
                'sub_seed': 1000 + n, 'scenario': 'forged'})
    log_lines = [json.dumps({'event': 'round_final', 'round': n,
                             'final': 'passed', 'reason': None})
                 for n in range(1, finalized + 1)]
    body = '\n'.join(log_lines)
    if torn_log:
        body += '\n{"event":"round_fi'
    (root / 'driver.log').write_text(body + '\n', encoding='utf-8')
    if receipt:
        write_json(segment / 'segment-close.json', {
            'reason': 'complete', 'stopped_wall': stopped,
            'rounds_total': {'passed': finalized, 'failed': 0,
                             'interrupted': 0, 'unknown': 0}})
    return root


def write_min_config(out: Path) -> tuple[str, str]:
    """Tiny but schema-valid SoakConfig + manifest pair."""
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / 'manifest.json'
    write_json(manifest, {'scenarios': [
        {'name': 'forged', 'kind': 'process', 'code': 'print("x")'}]})
    config = out / 'config.json'
    write_json(config, {'master_seed': 2026092001,
                        'scenario_manifest': str(manifest),
                        'candidate': {'label': 'forged'},
                        'max_wall_seconds': 10,
                        'minimum_valid_rounds': 1})
    return str(config), str(manifest)


def tree_fingerprint(root: Path):
    rows = []
    for path in sorted(root.rglob('*')):
        st = path.stat()
        rows.append((str(path.relative_to(root)), st.st_size, st.st_mtime_ns))
    return rows


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def run_cli(argv) -> int:
    return clo.main(argv)


def settle_args(campaign: Path, out: Path, **over) -> list:
    args = ['settle', '--campaign', str(campaign), '--out', str(out),
            '--stop-file', str(out / 'STOP'), '--deadline', FUTURE,
            '--label', 'px05a-test']
    args += ['--target-end', over.pop('target_end', FUTURE)]
    for key, value in over.items():
        args += [f'--{key.replace("_", "-")}', str(value)]
    return args


# ------------------------------------------------------------ CLI guards

def test_cli_rejects_fast_polling(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', receipt=True)
    args = ['wait', '--campaign', str(campaign), '--out',
            str(tmp_path / 'out'), '--stop-file', str(tmp_path / 'stop'),
            '--deadline', FUTURE, '--interval', '60']
    with pytest.raises(SystemExit):
        run_cli(args)


def test_guard_refuses_out_inside_campaign(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign')
    args = settle_args(campaign, campaign / 'nested-out')
    with pytest.raises(SystemExit, match='refusing'):
        run_cli(args)


def test_guard_refuses_out_ancestor_escape(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign')
    escape = tmp_path / 'side-root' / 'nested' / '..' / '..' \
        / campaign.name / 'inside-out'
    with pytest.raises(SystemExit, match='refusing'):
        clo.guard_paths(campaign, escape, tmp_path / 'stop')


# ------------------------------------------------- R5 write-target guard

def test_write_target_rejected_boundaries(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign')
    side = tmp_path / 'side-root'
    assert clo.write_target_rejected(campaign, side) is None
    assert clo.write_target_rejected(campaign, side / 'ckpt') is None
    assert clo.write_target_rejected(campaign, campaign) is not None
    assert clo.write_target_rejected(
        campaign, campaign / 'closeout-checkpoint.json') is not None
    escape = tmp_path / 'side-root' / 'nested' / '..' / '..' \
        / campaign.name / 'ckpt'
    assert clo.write_target_rejected(campaign, escape) is not None


def test_write_target_rejected_resolves_reparse_alias(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign')
    alias = tmp_path / 'ckpt-alias'
    try:
        alias.symlink_to(campaign, target_is_directory=True)
    except OSError:
        pytest.skip('directory symlink unavailable on this host')
    assert clo.write_target_rejected(campaign, alias) is not None


def test_guarded_checkpoint_dir_variants(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign')
    none_dir, none_reason = clo.guarded_checkpoint_dir(campaign, None)
    assert none_dir is None and none_reason is None
    side = tmp_path / 'side-ckpt'
    ok_dir, ok_reason = clo.guarded_checkpoint_dir(campaign, str(side))
    assert ok_dir == side and ok_reason is None
    bad_dir, bad_reason = clo.guarded_checkpoint_dir(campaign, str(campaign))
    assert bad_dir is None and bad_reason


def test_wait_loop_checkpoint_inside_campaign_is_refused(tmp_path):
    """Migrated R5 counterexample (probe shape): wait_loop must refuse a
    checkpoint dir pointing into the campaign and leave it byte-identical."""
    campaign = forge_campaign(tmp_path / 'campaign')
    out = tmp_path / 'out'
    before = tree_fingerprint(campaign)
    opts = argparse.Namespace(
        campaign=str(campaign), out=str(out), stop_file=str(out / 'stop'),
        deadline=PAST, target_end=None, driver_pid=None, label='px05a-r5',
        stability=900, interval=300, checkpoint_dir=str(campaign),
        audit_config=None, audit_manifest=None, audit_baseline=None)
    with patch.object(clo, 'campaign_snapshot',
                      return_value={'missing': False}), \
         patch.object(clo, 'do_closeout',
                      return_value={'outcome': clo.DEADLINE_REACHED}):
        assert clo.wait_loop(opts) == clo.EXIT_OK
    assert not (campaign / 'closeout-checkpoint.json').exists()
    assert tree_fingerprint(campaign) == before  # zero campaign writes
    receipt = load_json(out / 'closeout-started.json')
    assert receipt['checkpoint_dir_requested'] == str(campaign)
    assert receipt['checkpoint_dir_effective'] is None
    assert receipt['checkpoint_guard_rejection']
    assert (out / 'closeout-poll-log.jsonl').is_file()  # side root unaffected


def test_wait_loop_checkpoint_inside_campaign_real_closeout(tmp_path):
    """Unmocked variant: the whole deadline closeout still runs, the guard
    alone disables the checkpoint target, and nothing lands in campaign."""
    campaign = forge_campaign(tmp_path / 'campaign', lock_pid=os.getpid())
    opts = wait_opts(tmp_path, campaign, deadline=utc_iso(time.time() - 10),
                     driver_pid=os.getpid())
    opts.checkpoint_dir = str(campaign)
    before = tree_fingerprint(campaign)
    assert clo.wait_loop(opts) == 0
    assert not (campaign / 'closeout-checkpoint.json').exists()
    summary = load_json(tmp_path / 'out' / 'final-summary.json')
    assert summary['outcome'] == clo.DEADLINE_REACHED
    receipt = load_json(tmp_path / 'out' / 'closeout-started.json')
    assert receipt['checkpoint_guard_rejection']
    assert tree_fingerprint(campaign) == before


def test_wait_loop_checkpoint_side_root_control(tmp_path):
    """Control: a side-root checkpoint dir is still written normally."""
    campaign = forge_campaign(tmp_path / 'campaign', lock_pid=os.getpid())
    opts = wait_opts(tmp_path, campaign, deadline=utc_iso(time.time() - 10),
                     driver_pid=os.getpid())
    before = tree_fingerprint(campaign)
    assert clo.wait_loop(opts) == 0
    ckpt = tmp_path / 'ckpt' / 'closeout-checkpoint.json'
    assert ckpt.is_file()
    assert load_json(ckpt)['label'] == 'px05a-test'
    receipt = load_json(tmp_path / 'out' / 'closeout-started.json')
    assert receipt['checkpoint_dir_effective'] == str(tmp_path / 'ckpt')
    assert receipt['checkpoint_guard_rejection'] is None
    assert tree_fingerprint(campaign) == before


# ------------------------------------------------------- normal end (1/5)

def test_settle_normal_end(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', receipt=True,
                              lock_pid=None)
    out = tmp_path / 'out'
    config, manifest = write_min_config(out)
    dead = exited_pid()
    before = tree_fingerprint(campaign)
    assert clo.main(settle_args(
        campaign, out, driver_pid=dead, audit_config=config,
        audit_manifest=manifest)) == 0
    summary = load_json(out / 'final-summary.json')
    assert summary['outcome'] == clo.ENDED_NORMALLY
    assert summary['review_ready'] is True
    assert summary['stability_assumed'] is True
    assert 'audit' in summary and 'gates' in summary
    gates = summary['gates']
    assert gates['segment_close_complete']['status'] == 'PASS'
    assert gates['observation_wall_target']['status'] == 'PASS'  # span 98s >= 10s
    assert gates['min_rounds']['status'] == 'PASS'
    assert gates['min_distinct_inputs']['status'] == 'NOT_YET'
    assert set(summary['breakdown']) == {'PASS', 'FAIL', 'UNKNOWN', 'NOT_YET'}
    marker = load_json(out / 'FINAL_REVIEW_READY')
    assert marker['outcome'] == clo.ENDED_NORMALLY
    digest = clo._sha256_file(out / 'final-summary.json')
    assert marker['final_summary_sha256'] == digest
    assert tree_fingerprint(campaign) == before  # campaign untouched
    assert not (campaign / 'stop').exists()


# ------------------------------------------------------- early exit (2/5)

def test_settle_early_exit(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', receipt=False)
    out = tmp_path / 'out'
    dead = exited_pid()
    assert clo.main(settle_args(campaign, out, driver_pid=dead,
                                target_end=FUTURE)) == 0
    summary = load_json(out / 'final-summary.json')
    assert summary['outcome'] == clo.EARLY_EXIT
    assert summary['review_ready'] is True
    # no close receipt -> the settled campaign still looks live/unclosed
    assert summary['audit_snapshot_incomplete_after_end'] is True
    assert summary['audit_overall'] in ('SNAPSHOT_INCOMPLETE', 'FAIL')
    gates = summary['gates']
    assert gates['segment_close_complete']['status'] == 'NOT_YET'
    assert gates['observation_wall_target']['status'] == 'NOT_YET'
    assert summary['breakdown']['NOT_YET'] >= 3
    assert (out / 'FINAL_REVIEW_READY').is_file()
    assert not (campaign / 'stop').exists()


# ------------------------------------------------------- half-write (3/5)

def test_settle_half_write(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', receipt=False,
                              running_round=True, torn_log=True)
    out = tmp_path / 'out'
    dead = exited_pid()
    assert clo.main(settle_args(campaign, out, driver_pid=dead,
                                target_end=FUTURE)) == 0
    summary = load_json(out / 'final-summary.json')
    assert summary['outcome'] == clo.EARLY_EXIT
    assert summary['review_ready'] is True
    report = summary['audit']
    # half-written state must be visible: a round mid-flight keeps the
    # settled snapshot looking live, and the torn driver-log tail is parked
    # in snapshot_incomplete (not judged) while the campaign looks live
    assert summary['audit_snapshot_incomplete_after_end'] is True
    assert report['snapshot_incomplete'], 'torn tail or live round not recorded'
    kinds = [item.get('kind') for item in report['snapshot_incomplete']]
    assert 'torn_driver_log_tail' in kinds
    assert summary['breakdown']['UNKNOWN'] >= 1
    assert (out / 'FINAL_REVIEW_READY').is_file()
    assert not (campaign / 'stop').exists()


# --------------------------------------------------------- unknown (4/5)

def test_settle_unknown_end_state(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign')
    out = tmp_path / 'out'
    # driver pid 0: liveness unverifiable before the deadline
    assert clo.main(settle_args(campaign, out, driver_pid=0)) == \
        clo.EXIT_NOT_ENDED
    status = load_json(out / 'closeout-status.json')
    assert status['outcome'] == clo.UNKNOWN_END_STATE
    assert not (out / 'final-summary.json').exists()
    assert not (out / 'FINAL_REVIEW_READY').exists()


def test_settle_not_ended_live_driver(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', lock_pid=os.getpid())
    out = tmp_path / 'out'
    assert clo.main(settle_args(campaign, out, driver_pid=os.getpid())) == \
        clo.EXIT_NOT_ENDED
    status = load_json(out / 'closeout-status.json')
    assert status['outcome'] == clo.NOT_ENDED
    assert not (out / 'FINAL_REVIEW_READY').exists()


# -------------------------------------------------------- deadline (5/5)

def test_settle_deadline_reached_while_running(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', lock_pid=os.getpid())
    out = tmp_path / 'out'
    past = utc_iso(time.time() - 10)
    assert clo.main(['settle', '--campaign', str(campaign), '--out', str(out),
                     '--stop-file', str(out / 'STOP'), '--deadline', past,
                     '--target-end', FUTURE, '--driver-pid',
                     str(os.getpid()), '--label', 'px05a-test']) == 0
    summary = load_json(out / 'final-summary.json')
    assert summary['outcome'] == clo.DEADLINE_REACHED
    assert summary['review_ready'] is True
    # lock holder (this test process) alive -> the audited snapshot honestly
    # still looks live at the deadline
    assert summary['audit_snapshot_incomplete_after_end'] is True
    marker = load_json(out / 'FINAL_REVIEW_READY')
    assert marker['outcome'] == clo.DEADLINE_REACHED
    assert not (campaign / 'stop').exists()  # never stops the campaign


# ------------------------------------------------------- failure + stop

def test_settle_closeout_failed_on_bad_audit_config(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', receipt=True)
    out = tmp_path / 'out'
    bad_config = out / 'bad-config.json'
    bad_config.parent.mkdir(parents=True, exist_ok=True)
    bad_config.write_text('{not json', encoding='utf-8')
    dead = exited_pid()
    assert clo.main(settle_args(campaign, out, driver_pid=dead,
                                audit_config=str(bad_config))) == \
        clo.EXIT_INTERNAL
    summary = load_json(out / 'final-summary.json')
    assert summary['outcome'] == clo.CLOSEOUT_FAILED
    assert summary['review_ready'] is False
    assert 'first_failure' in summary
    assert (out / 'closeout-failed.txt').is_file()
    assert not (out / 'FINAL_REVIEW_READY').exists()


def test_wait_stop_file_stops_without_summary(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', lock_pid=os.getpid())
    out = tmp_path / 'out'
    stop = out / 'STOP-closeout'
    out.mkdir(parents=True)
    stop.write_text('owner stop\n', encoding='utf-8')
    before = tree_fingerprint(campaign)
    assert clo.main(['wait', '--campaign', str(campaign), '--out', str(out),
                     '--stop-file', str(stop), '--deadline', FUTURE,
                     '--driver-pid', str(os.getpid()),
                     '--interval', '300']) == clo.EXIT_STOPPED
    stopped = load_json(out / 'closeout-stopped.json')
    assert stopped['outcome'] == clo.STOP_REQUESTED
    assert stopped['stop_path'] == str(stop)
    assert not (out / 'final-summary.json').exists()
    assert not (out / 'FINAL_REVIEW_READY').exists()
    assert tree_fingerprint(campaign) == before


# ----------------------------------------------- fast library-level waits

def wait_opts(tmp_path, campaign, *, interval=0, stability=0, deadline=FUTURE,
              target_end=FUTURE, driver_pid=None):
    return argparse.Namespace(
        campaign=str(campaign), out=str(tmp_path / 'out'),
        stop_file=str(tmp_path / 'out' / 'STOP'), driver_pid=driver_pid,
        deadline=deadline, target_end=target_end, interval=interval,
        stability=stability, audit_config=None, audit_manifest=None,
        audit_baseline=None, label='px05a-test',
        checkpoint_dir=str(tmp_path / 'ckpt'))


def test_wait_loop_deadline_fast(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', lock_pid=os.getpid())
    opts = wait_opts(tmp_path, campaign, deadline=utc_iso(time.time() - 10),
                     driver_pid=os.getpid())
    assert clo.wait_loop(opts) == 0
    out = tmp_path / 'out'
    summary = load_json(out / 'final-summary.json')
    assert summary['outcome'] == clo.DEADLINE_REACHED
    assert (out / 'FINAL_REVIEW_READY').is_file()
    polls = [json.loads(line) for line
             in (out / 'closeout-poll-log.jsonl').read_text(
                 encoding='utf-8').splitlines() if line.strip()]
    assert polls and polls[-1]['outcome'] == clo.DEADLINE_REACHED
    checkpoint = load_json(tmp_path / 'ckpt' / 'closeout-checkpoint.json')
    assert checkpoint['label'] == 'px05a-test'


def test_wait_loop_normal_end_requires_stability(tmp_path):
    campaign = forge_campaign(tmp_path / 'campaign', receipt=True)
    dead = exited_pid()
    opts = wait_opts(tmp_path, campaign, driver_pid=dead)
    assert clo.wait_loop(opts) == 0
    out = tmp_path / 'out'
    summary = load_json(out / 'final-summary.json')
    assert summary['outcome'] == clo.ENDED_NORMALLY
    assert summary['stability_assumed'] is False
    polls = [json.loads(line) for line
             in (out / 'closeout-poll-log.jsonl').read_text(
                 encoding='utf-8').splitlines() if line.strip()]
    assert len(polls) >= 2  # needed a second probe to prove stability
    assert (out / 'FINAL_REVIEW_READY').is_file()
    assert (out / 'closeout-started.json').is_file()


# ----------------------------------------------------------- primitives

def test_campaign_snapshot_missing_and_bounded(tmp_path):
    missing = clo.campaign_snapshot(tmp_path / 'nope')
    assert missing['missing'] is True
    campaign = forge_campaign(tmp_path / 'campaign', receipt=True,
                              lock_pid=424242)
    snap = clo.campaign_snapshot(campaign)
    assert snap['missing'] is False
    assert snap['lock_pid'] == 424242
    assert snap['close_reasons']['segment-000001'] == 'complete'
    assert clo.complete_receipt(snap) is True
    assert snap['segments'][0]['rounds_count'] == 2


def test_detect_outcome_priority(tmp_path):
    kw = dict(stop_seen=False, receipt=False, pid_alive=False, stable=True,
              now_wall=1_000_000.0, deadline_wall=2_000_000.0,
              target_end_wall=1_500_000.0)
    assert clo.detect_outcome(**kw) == clo.EARLY_EXIT
    assert clo.detect_outcome(**{**kw, 'receipt': True}) == clo.ENDED_NORMALLY
    assert clo.detect_outcome(**{**kw, 'now_wall': 1_600_000.0}) == \
        clo.LATE_EXIT_NO_RECEIPT
    assert clo.detect_outcome(**{**kw, 'stop_seen': True}) == \
        clo.STOP_REQUESTED
    assert clo.detect_outcome(**{**kw, 'pid_alive': True,
                                 'now_wall': 2_100_000.0}) == \
        clo.DEADLINE_REACHED
    assert clo.detect_outcome(**{**kw, 'pid_alive': None}) is None
    assert clo.detect_outcome(**{**kw, 'pid_alive': True}) is None
