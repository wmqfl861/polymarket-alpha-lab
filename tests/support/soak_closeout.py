"""PX-05a standalone terminal closeout for the LT-04 formal soak campaign.

Owner-approved bypass closeout (2026-09-21, PAL_PARALLEL_REVIEW_20260921_V2
section 10). Scope is exactly:

- Wait read-only (poll >= 300s, bounded stat-only snapshots) for the ORIGINAL
  soak campaign to definitively end, then run ONE full read-only soak_audit
  adjudication and write ``final-summary.json`` plus a ``FINAL_REVIEW_READY``
  marker into a side-root output directory.
- Handle: normal end (segment-close receipt), early exit (driver gone before
  the target end, no receipt), half-written snapshots (settled files whose
  audit stays SNAPSHOT_INCOMPLETE), unknown/unverifiable states and internal
  failures (recorded honestly), and the absolute deadline passing while the
  driver still runs.

It CANNOT and DOES NOT: restart the original task, call any model, modify
source code, push anything, create services or scheduled tasks, or wake any
language model. It only reads bounded campaign state and writes files under
its own side-root output/checkpoint directories. Its stop path is a
side-root stop file; it never creates or touches the campaign's own stop
file and never writes inside the campaign.

Write-target guard (R5): every write root — the output directory, the
checkpoint directory, and each derived target (final-summary.json,
FINAL_REVIEW_READY, closeout-failed/-stopped/-status/-started markers, the
poll log and its ``.tmp`` rotation siblings) — is resolved to its absolute
form (``..`` ancestor escapes and reparse-point aliases included) and
refused, before any file is opened, when it is the campaign directory or
anywhere inside it. Required roots (out, stop-file) are refused at entry;
the optional checkpoint root is refused per write and recorded honestly in
the started receipt instead of silently writing elsewhere.

Subcommands:
  wait    real polling loop until a definite end condition, then close out
  settle  one-shot closeout assuming the end already happened (recovery or
          synthetic terminal-state testing; refuses while the driver lives
          unless the deadline has passed)
  status  print one bounded state snapshot as JSON (no writes)
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.support import soak_audit as aud  # noqa: E402
from tests.support import soak_driver as drv  # noqa: E402

TOOL = 'soak_closeout'

# outcomes
STOP_REQUESTED = 'STOP_REQUESTED'
ENDED_NORMALLY = 'ENDED_NORMALLY'
EARLY_EXIT = 'EARLY_EXIT'
LATE_EXIT_NO_RECEIPT = 'LATE_EXIT_NO_RECEIPT'
DEADLINE_REACHED = 'DEADLINE_REACHED'
UNKNOWN_END_STATE = 'UNKNOWN_END_STATE'
NOT_ENDED = 'NOT_ENDED'
CLOSEOUT_FAILED = 'CLOSEOUT_FAILED'

REVIEW_READY_OUTCOMES = (ENDED_NORMALLY, EARLY_EXIT, LATE_EXIT_NO_RECEIPT,
                         DEADLINE_REACHED)

# exit codes
EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_NOT_ENDED = 2
EXIT_STOPPED = 4
EXIT_CONFIG = 5

MIN_POLL_INTERVAL = 300
POLL_LOG_ROTATE_BYTES = 1024 * 1024
ROOT_FILE_NAMES = ('campaign.json', 'driver.lock', 'driver.log', 'driver.log.1',
                   'first-failure.json')
SEGMENT_FILE_NAMES = ('segment.json', 'heartbeats.jsonl', 'segment-close.json')
LOCK_READ_CAP = 8192
CLOSE_READ_CAP = 65536
MAX_SEGMENTS = 64
MAX_ROUND_ENTRIES = 4096
MIN_DISTINCT_INPUTS_GATE = 100000


def utc_iso(wall: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(wall))


def parse_utc(text: str) -> float:
    return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


# ---------------------------------------------------------------- snapshot

def _stat_entry(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def campaign_snapshot(root: Path) -> dict:
    """Bounded, stat-only activity fingerprint of a campaign directory.

    Content is read only from ``driver.lock`` (pid) and
    ``segment-close.json`` (reason), both size-capped. Never writes.
    """
    root = Path(root)
    snap: dict = {'missing': False, 'root_files': {}, 'segments': [],
                  'lock_pid': None, 'close_reasons': {}, 'truncated': False}
    if not root.is_dir():
        snap['missing'] = True
        return snap
    for name in ROOT_FILE_NAMES:
        entry = _stat_entry(root / name)
        if entry is not None:
            snap['root_files'][name] = entry
    try:
        raw = (root / 'driver.lock').read_bytes()[:LOCK_READ_CAP]
        data = json.loads(raw.decode('utf-8', 'replace'))
        pid = data.get('pid') if isinstance(data, dict) else None
        snap['lock_pid'] = pid if isinstance(pid, int) else None
    except (OSError, ValueError):
        snap['lock_pid'] = None
    segments_dir = root / 'segments'
    try:
        names = sorted(p.name for p in segments_dir.iterdir() if p.is_dir())
    except OSError:
        names = []
    if len(names) > MAX_SEGMENTS:
        snap['truncated'] = True
        names = names[-MAX_SEGMENTS:]
    for name in names:
        base = segments_dir / name
        seg: dict = {'name': name, 'files': {}, 'rounds_mtime_ns': None,
                     'rounds_count': None, 'rounds_latest': None,
                     'truncated_rounds': False}
        for fname in SEGMENT_FILE_NAMES:
            entry = _stat_entry(base / fname)
            if entry is not None:
                seg['files'][fname] = entry
        close = base / 'segment-close.json'
        if close.is_file():
            try:
                if close.stat().st_size <= CLOSE_READ_CAP:
                    data = drv._read_json(close)
                    snap['close_reasons'][name] = \
                        (data or {}).get('reason') if isinstance(data, dict) \
                        else 'unreadable'
                else:
                    snap['close_reasons'][name] = 'unreadable_oversize'
            except OSError:
                snap['close_reasons'][name] = 'unreadable'
        rounds = base / 'rounds'
        try:
            seg['rounds_mtime_ns'] = rounds.stat().st_mtime_ns
            count = 0
            latest = None
            for entry in rounds.iterdir():
                count += 1
                if latest is None or entry.name > latest:
                    latest = entry.name
                if count > MAX_ROUND_ENTRIES:
                    seg['truncated_rounds'] = True
                    break
            seg['rounds_count'] = count
            seg['rounds_latest'] = latest
        except OSError:
            pass
        snap['segments'].append(seg)
    return snap


def complete_receipt(snap: dict) -> bool:
    return any(reason == 'complete'
               for reason in snap.get('close_reasons', {}).values())


def detect_outcome(*, stop_seen: bool, receipt: bool, pid_alive,
                   stable: bool, now_wall: float, deadline_wall: float,
                   target_end_wall: float | None,
                   settle_mode: bool = False):
    """Decide the terminal outcome from bounded evidence.

    Returns an outcome string, or ``None`` when waiting must continue
    (``settle_mode`` never returns ``None`` for a live driver: it returns
    NOT_ENDED / UNKNOWN_END_STATE / DEADLINE_REACHED instead). Pure.
    """
    if stop_seen:
        return STOP_REQUESTED
    if pid_alive is False and (stable or settle_mode):
        if receipt:
            return ENDED_NORMALLY
        if target_end_wall is not None and now_wall < target_end_wall:
            return EARLY_EXIT
        return LATE_EXIT_NO_RECEIPT
    if now_wall >= deadline_wall:
        return DEADLINE_REACHED  # running or unverifiable at the deadline
    if settle_mode:
        if pid_alive is None:
            return UNKNOWN_END_STATE
        return NOT_ENDED
    return None


# ------------------------------------------------------------------- audit

def run_audit(campaign: Path, config_path: str | None,
              manifest_path: str | None, baseline_path: str | None) -> dict:
    """One full read-only soak_audit adjudication. Raises on bad inputs."""
    config = None
    if config_path:
        config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    baseline = None
    if baseline_path:
        baseline = json.loads(Path(baseline_path).read_text(encoding='utf-8'))
    scenarios = scenario_objects = None
    if manifest_path:
        loaded, _sha = drv._load_manifest(Path(manifest_path))
        scenarios = [s.name for s in loaded]
        scenario_objects = loaded
    return aud.audit_campaign(Path(campaign).resolve(), config=config,
                              scenarios=scenarios,
                              scenario_objects=scenario_objects,
                              baseline=baseline)


def build_gates(report: dict, config: dict | None, outcome: str,
                snapshot: dict, campaign: Path) -> dict:
    """Campaign-level end gates with PASS / NOT_YET / UNKNOWN classification.

    The raw per-check adjudication stays inside the embedded audit report;
    this section only states whether the planned end was reached. NOT_YET
    means the campaign ended (or was stopped) before the gate could ever be
    satisfied at closeout time.
    """
    gates: dict = {}
    cfg = config if isinstance(config, dict) else {}
    max_wall = cfg.get('max_wall_seconds')
    min_rounds = cfg.get('minimum_valid_rounds')
    ended_planned = outcome == ENDED_NORMALLY

    receipt = complete_receipt(snapshot)
    gates['segment_close_complete'] = {
        'status': 'PASS' if receipt else 'NOT_YET',
        'detail': {'receipt': receipt, 'outcome': outcome}}
    if receipt:
        # span of the closed segment, recomputed from its own files
        span = None
        for seg in reversed(snapshot.get('segments', [])):
            close_path = Path(campaign) / 'segments' / seg['name'] \
                / 'segment-close.json'
            header = drv._read_json(Path(campaign) / 'segments' / seg['name']
                                    / 'segment.json') or {}
            close = drv._read_json(close_path) or {}
            started = header.get('started_wall')
            stopped = close.get('stopped_wall')
            if isinstance(started, (int, float)) \
                    and isinstance(stopped, (int, float)):
                span = round(stopped - started, 3)
            break
        if isinstance(max_wall, (int, float)) and span is not None:
            tolerance = max(0.5, 0.001 * max_wall)
            gates['observation_wall_target'] = {
                'status': 'PASS' if span >= max_wall - tolerance else 'NOT_YET',
                'detail': {'span_seconds': span, 'max_wall_seconds': max_wall,
                           'tolerance_seconds': round(tolerance, 3)}}
        else:
            gates['observation_wall_target'] = {
                'status': 'UNKNOWN',
                'detail': {'span_seconds': span, 'max_wall_seconds': max_wall,
                           'note': 'span or config unavailable'}}
    else:
        gates['observation_wall_target'] = {
            'status': 'NOT_YET',
            'detail': {'note': 'no complete close receipt; planned end not '
                               'reached', 'outcome': outcome}}

    rounds_total = report.get('rounds_total') or {}
    achieved_rounds = sum(v for v in rounds_total.values()
                          if isinstance(v, (int, float)))
    if isinstance(min_rounds, int):
        gates['min_rounds'] = {
            'status': 'PASS' if achieved_rounds >= min_rounds else 'NOT_YET',
            'detail': {'achieved': achieved_rounds, 'required': min_rounds}}
    else:
        gates['min_rounds'] = {'status': 'UNKNOWN',
                               'detail': {'achieved': achieved_rounds}}

    inputs = report.get('inputs') or {}
    subinputs = inputs.get('receipt_declared_subinputs_sum')
    gates['min_distinct_inputs'] = {
        'status': ('PASS' if isinstance(subinputs, int)
                   and subinputs >= MIN_DISTINCT_INPUTS_GATE else 'NOT_YET'),
        'threshold': MIN_DISTINCT_INPUTS_GATE,
        'detail': {
            'receipt_declared_subinputs_sum': subinputs,
            'functional_seed_distinct': inputs.get('functional_seed_distinct'),
            'payload_level_distinct': inputs.get('payload_level_distinct'),
            'pytest_logical_identities':
                inputs.get('pytest_logical_identities'),
            'note': 'raw accounting per soak_audit inputs section; the '
                    'caliber (kouchi) judgment for this gate belongs to the '
                    'PX-05 main-lane final adjudication, not this bypass '
                    'closeout'}}
    if not ended_planned and outcome != DEADLINE_REACHED:
        for name in ('segment_close_complete', 'observation_wall_target',
                     'min_rounds', 'min_distinct_inputs'):
            if gates[name]['status'] == 'UNKNOWN':
                gates[name]['status'] = 'NOT_YET'
    return gates


def build_breakdown(report: dict, gates: dict) -> dict:
    counts = {'PASS': 0, 'FAIL': 0, 'UNKNOWN': 0}
    for entry in report.get('checks', []):
        status = entry.get('status')
        if status in counts:
            counts[status] += 1
    counts['NOT_YET'] = sum(1 for g in gates.values()
                            if g.get('status') == 'NOT_YET')
    return counts


# ------------------------------------------------------------------- write

def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_poll_log(out_dir: Path, entry: dict) -> None:
    log = out_dir / 'closeout-poll-log.jsonl'
    try:
        if log.is_file() and log.stat().st_size > POLL_LOG_ROTATE_BYTES:
            rotated = out_dir / 'closeout-poll-log.jsonl.1'
            if rotated.is_file():
                rotated.unlink()
            os.replace(log, rotated)
    except OSError:
        pass
    try:
        with log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, sort_keys=True, default=str)
                         + '\n')
    except OSError:
        pass


def write_target_rejected(campaign: Path, target: Path) -> str | None:
    """Reason a planned write target must be refused, or None when allowed.

    The target is resolved to its absolute form (``..`` ancestor escapes
    and reparse-point aliases included) and refused when it is the campaign
    directory itself or anywhere inside it. Pure; never writes.
    """
    campaign_resolved = Path(campaign).resolve()
    resolved = Path(target).resolve()
    if resolved == campaign_resolved:
        return f'target is the campaign directory: {resolved}'
    try:
        resolved.relative_to(campaign_resolved)
    except ValueError:
        return None
    return f'target inside campaign directory: {resolved}'


def refuse_campaign_target(campaign: Path, target: Path, label: str) -> None:
    """Refuse a required write target that would land in the campaign.

    Raised before any file is opened, so a refusal never leaves a
    half-written artifact behind.
    """
    reason = write_target_rejected(campaign, target)
    if reason:
        raise SystemExit(f'{TOOL} refusing {label} inside campaign: {reason}')


def guarded_checkpoint_dir(campaign: Path,
                           value) -> tuple[Path | None, str | None]:
    """Guard a configured checkpoint dir against campaign writes.

    Returns ``(dir, rejection)``: a configured dir is either the guarded
    directory (rejection None) or None with the rejection reason — the
    checkpoint is then disabled and nothing is ever written, following the
    existing tolerated-checkpoint-failure path instead of crashing or
    writing elsewhere. No value configured -> ``(None, None)``.
    """
    if not value:
        return None, None
    target = Path(value)
    reason = write_target_rejected(campaign, target)
    if reason:
        return None, reason
    return target, None


def write_checkpoint(checkpoint_dir: Path | None, payload: dict,
                     campaign: Path | None = None) -> None:
    if checkpoint_dir is None:
        return
    if campaign is not None \
            and write_target_rejected(campaign, checkpoint_dir):
        return  # guarded before mkdir/open: never write into the campaign
    try:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(checkpoint_dir / 'closeout-checkpoint.json',
                      json.dumps(payload, indent=2, sort_keys=True,
                                 default=str) + '\n')
    except OSError:
        pass


def guard_paths(campaign: Path, out_dir: Path, stop_file: Path) -> None:
    """Refuse output locations that would write inside the campaign."""
    for label, path in (('out', out_dir), ('stop-file', stop_file)):
        refuse_campaign_target(campaign, path, label)


# ------------------------------------------------------------- closeout

def do_closeout(*, outcome: str, snapshot: dict, campaign: Path,
                out_dir: Path, label: str, started_wall: float,
                first_poll_wall: float, polls: int, interval: int,
                stability: int, deadline_wall: float,
                target_end_wall: float | None, stop_file: Path,
                driver_pid: int | None, stability_assumed: bool,
                config_path: str | None, manifest_path: str | None,
                baseline_path: str | None, extra: dict | None) -> dict:
    """Run the audit and write final-summary.json (+markers). Never writes
    inside the campaign. Returns the summary dict."""
    # every target written below (final-summary.json, closeout-failed.txt,
    # FINAL_REVIEW_READY, and their .tmp siblings) is a fixed name derived
    # from out_dir, so guarding out_dir guards them all
    refuse_campaign_target(campaign, out_dir, 'out')
    out_dir.mkdir(parents=True, exist_ok=True)
    closeout_wall = time.time()
    summary: dict = {
        'tool': TOOL, 'label': label, 'outcome': outcome,
        'campaign': str(campaign), 'out_dir': str(out_dir),
        'stop_path': str(stop_file),
        'driver_pid': driver_pid,
        'lock_pid_at_end': snapshot.get('lock_pid'),
        'close_reasons_at_end': snapshot.get('close_reasons'),
        'campaign_missing_at_end': snapshot.get('missing', False),
        'closeout_started_wall_utc': utc_iso(started_wall),
        'first_poll_wall_utc': utc_iso(first_poll_wall),
        'closeout_wall_utc': utc_iso(closeout_wall),
        'ended_observed_wall_utc': utc_iso((extra or {}).get(
            'ended_observed_wall') or closeout_wall),
        'poll_interval_seconds': interval, 'polls': polls,
        'stability_window_seconds': stability,
        'stability_assumed': stability_assumed,
        'deadline_wall_utc': utc_iso(deadline_wall),
        'target_end_wall_utc': utc_iso(target_end_wall)
        if target_end_wall else None,
        'capabilities_note': 'bypass closeout: reads bounded original state, '
                             'writes side-root summary and readiness marker '
                             'only; cannot restart the task, call models, '
                             'modify source, push, create services, or wake '
                             'a language model',
    }
    if extra:
        summary.update(extra)
    if outcome not in REVIEW_READY_OUTCOMES:
        summary['review_ready'] = False
        _atomic_write(out_dir / 'final-summary.json',
                      json.dumps(summary, indent=2, sort_keys=True,
                                 default=str) + '\n')
        return summary
    try:
        report = run_audit(campaign, config_path, manifest_path,
                           baseline_path)
        config = json.loads(Path(config_path).read_text(encoding='utf-8')) \
            if config_path else None
        gates = build_gates(report, config, outcome, snapshot, campaign)
    except Exception as error:  # noqa: BLE001 - record honestly, fail closed
        failed = dict(summary)
        failed['outcome'] = CLOSEOUT_FAILED
        failed['review_ready'] = False
        failed['first_failure'] = {
            'error': f'{error.__class__.__name__}: {error}',
            'stage': 'audit'}
        _atomic_write(out_dir / 'final-summary.json',
                      json.dumps(failed, indent=2, sort_keys=True,
                                 default=str) + '\n')
        (out_dir / 'closeout-failed.txt').write_text(
            f'{error.__class__.__name__}: {error}\n', encoding='utf-8')
        return failed
    summary['audit'] = report
    summary['audit_overall'] = report.get('overall')
    # half-write signal: the settled files still LOOK live (round mid-flight,
    # segment unclosed, live lock holder) even though the driver ended
    summary['audit_snapshot_incomplete_after_end'] = \
        report.get('live') is True
    summary['gates'] = gates
    summary['breakdown'] = build_breakdown(report, gates)
    summary['review_ready'] = True
    _atomic_write(out_dir / 'final-summary.json',
                  json.dumps(summary, indent=2, sort_keys=True,
                             default=str) + '\n')
    marker = {
        'tool': TOOL, 'label': label, 'outcome': outcome,
        'final_summary_path': str(out_dir / 'final-summary.json'),
        'final_summary_sha256': _sha256_file(out_dir / 'final-summary.json'),
        'audit_overall': summary['audit_overall'],
        'breakdown': summary['breakdown'],
        'written_wall_utc': utc_iso(time.time()),
        'closeout_pid': os.getpid(),
        'stop_path': str(stop_file),
        'note': 'PX-05a bypass closeout marker: evidence files are final and '
                'stable; this file does not wake any model and grants no '
                'execution authorization',
    }
    _atomic_write(out_dir / 'FINAL_REVIEW_READY',
                  json.dumps(marker, indent=2, sort_keys=True) + '\n')
    return summary


def write_stopped(out_dir: Path, label: str, snapshot: dict,
                  stop_file: Path, polls: int,
                  campaign: Path | None = None) -> None:
    if campaign is not None:
        refuse_campaign_target(campaign, out_dir, 'out')
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(out_dir / 'closeout-stopped.json', json.dumps({
        'tool': TOOL, 'label': label, 'outcome': STOP_REQUESTED,
        'stop_path': str(stop_file), 'stopped_wall_utc': utc_iso(time.time()),
        'closeout_pid': os.getpid(), 'polls': polls,
        'lock_pid_at_stop': snapshot.get('lock_pid'),
        'close_reasons_at_stop': snapshot.get('close_reasons'),
        'note': 'owner stop file observed; no final summary or readiness '
                'marker written; original campaign untouched',
    }, indent=2, sort_keys=True) + '\n')


# ------------------------------------------------------------------- wait

def wait_loop(opts) -> int:
    campaign = Path(opts.campaign)
    out_dir = Path(opts.out)
    stop_file = Path(opts.stop_file)
    guard_paths(campaign, out_dir, stop_file)
    ckpt_dir, ckpt_reject = guarded_checkpoint_dir(campaign,
                                                   opts.checkpoint_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started_wall = time.time()
    deadline_wall = parse_utc(opts.deadline)
    target_end_wall = parse_utc(opts.target_end) if opts.target_end else None
    pid_expected: int | None = opts.driver_pid

    started_receipt = {
        'tool': TOOL, 'label': opts.label, 'pid': os.getpid(),
        'argv': sys.argv, 'started_wall_utc': utc_iso(started_wall),
        'campaign': str(campaign), 'out_dir': str(out_dir),
        'stop_path': str(stop_file),
        'checkpoint_dir_requested': str(opts.checkpoint_dir)
        if opts.checkpoint_dir else None,
        'checkpoint_dir_effective': str(ckpt_dir) if ckpt_dir else None,
        'checkpoint_guard_rejection': ckpt_reject,
        'driver_pid': pid_expected, 'deadline_wall_utc': opts.deadline,
        'target_end_wall_utc': opts.target_end,
        'poll_interval_seconds': opts.interval,
        'stability_window_seconds': opts.stability,
        'audit_config': opts.audit_config,
        'audit_manifest': opts.audit_manifest,
        'audit_baseline': opts.audit_baseline,
        'note': 'waiting for the original soak to end; first receipt of the '
                'bypass closeout',
    }
    _atomic_write(out_dir / 'closeout-started.json',
                  json.dumps(started_receipt, indent=2, sort_keys=True,
                             default=str) + '\n')

    prev_snap = None
    stable_since = None
    polls = 0
    outcome = None
    ended_observed = None
    last_snap: dict = {}
    consecutive_errors = 0
    first_error = None
    while True:
        now = time.time()
        polls += 1
        try:
            snap = campaign_snapshot(campaign)
            consecutive_errors = 0
        except OSError as error:
            consecutive_errors += 1
            if first_error is None:
                first_error = f'snapshot:{error.__class__.__name__}:{error}'
            if consecutive_errors >= 3:
                outcome = CLOSEOUT_FAILED
                last_snap = {'missing': True}
                ended_observed = now
                break
            snap = prev_snap or {'missing': True}
        last_snap = snap
        pid_alive = drv._pid_alive(pid_expected) if pid_expected else None
        lock_pid = snap.get('lock_pid')
        lock_anomaly = (lock_pid is not None and pid_expected is not None
                        and lock_pid != pid_expected)
        if prev_snap is not None and snap == prev_snap:
            if stable_since is None:
                stable_since = now
        else:
            stable_since = None
        stable = stable_since is not None \
            and (now - stable_since) >= opts.stability
        stop_seen = stop_file.is_file()
        receipt = complete_receipt(snap)
        outcome = detect_outcome(
            stop_seen=stop_seen, receipt=receipt, pid_alive=pid_alive,
            stable=stable, now_wall=now, deadline_wall=deadline_wall,
            target_end_wall=target_end_wall)
        poll_entry = {
            'poll': polls, 'wall_utc': utc_iso(now), 'pid_alive': pid_alive,
            'lock_pid': lock_pid, 'lock_anomaly': lock_anomaly,
            'receipt': receipt, 'stable': stable,
            'stable_since_wall_utc': utc_iso(stable_since)
            if stable_since else None,
            'stop_seen': stop_seen, 'outcome': outcome,
            'rounds_latest': [s.get('rounds_latest')
                              for s in snap.get('segments', [])][-1:]
            if snap.get('segments') else [],
        }
        write_poll_log(out_dir, poll_entry)
        write_checkpoint(ckpt_dir, {
            'tool': TOOL, 'label': opts.label, 'pid': os.getpid(),
            'wall_utc': utc_iso(now), 'polls': polls,
            'outcome_so_far': outcome, 'pid_alive': pid_alive,
            'receipt': receipt, 'stable': stable, 'stop_seen': stop_seen,
            'first_error': first_error}, campaign=campaign)
        if outcome is not None:
            ended_observed = now
            break
        prev_snap = snap
        # responsive stop: check the stop file at most every 30s while
        # sleeping one poll interval
        remaining = opts.interval
        while remaining > 0:
            chunk = min(30, remaining)
            time.sleep(chunk)
            remaining -= chunk
            if stop_file.is_file():
                outcome = STOP_REQUESTED
                ended_observed = time.time()
                break
        if outcome is not None:
            last_snap = campaign_snapshot(campaign)
            write_poll_log(out_dir, {
                'poll': polls + 1, 'wall_utc': utc_iso(time.time()),
                'stop_seen': True, 'outcome': STOP_REQUESTED,
                'note': 'stop file observed during sleep'})
            break

    if outcome == STOP_REQUESTED:
        write_stopped(out_dir, opts.label, last_snap, stop_file, polls,
                      campaign=campaign)
        write_checkpoint(ckpt_dir, {
            'tool': TOOL, 'label': opts.label, 'pid': os.getpid(),
            'wall_utc': utc_iso(time.time()), 'outcome': STOP_REQUESTED,
            'polls': polls}, campaign=campaign)
        return EXIT_STOPPED
    if outcome == CLOSEOUT_FAILED:
        summary = {
            'tool': TOOL, 'label': opts.label, 'outcome': CLOSEOUT_FAILED,
            'review_ready': False,
            'campaign': str(campaign),
            'closeout_wall_utc': utc_iso(time.time()),
            'first_failure': first_error,
            'note': 'repeated bounded snapshot failures; campaign state '
                    'could not be read honestly',
        }
        refuse_campaign_target(campaign, out_dir, 'out')
        out_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(out_dir / 'final-summary.json',
                      json.dumps(summary, indent=2, sort_keys=True,
                                 default=str) + '\n')
        (out_dir / 'closeout-failed.txt').write_text(
            f'{first_error}\n', encoding='utf-8')
        return EXIT_INTERNAL
    summary = do_closeout(
        outcome=outcome, snapshot=last_snap, campaign=campaign,
        out_dir=out_dir, label=opts.label, started_wall=started_wall,
        first_poll_wall=started_wall, polls=polls, interval=opts.interval,
        stability=opts.stability, deadline_wall=deadline_wall,
        target_end_wall=target_end_wall, stop_file=stop_file,
        driver_pid=pid_expected, stability_assumed=False,
        config_path=opts.audit_config, manifest_path=opts.audit_manifest,
        baseline_path=opts.audit_baseline,
        extra={'ended_observed_wall': ended_observed,
               'first_poll_error': first_error})
    return EXIT_OK if summary.get('outcome') != CLOSEOUT_FAILED \
        else EXIT_INTERNAL


# ----------------------------------------------------------------- settle

def settle_once(opts) -> int:
    campaign = Path(opts.campaign)
    out_dir = Path(opts.out)
    stop_file = Path(opts.stop_file)
    guard_paths(campaign, out_dir, stop_file)
    out_dir.mkdir(parents=True, exist_ok=True)
    started_wall = time.time()
    deadline_wall = parse_utc(opts.deadline)
    target_end_wall = parse_utc(opts.target_end) if opts.target_end else None
    snap = campaign_snapshot(campaign)
    pid_alive = drv._pid_alive(opts.driver_pid) if opts.driver_pid else None
    outcome = detect_outcome(
        stop_seen=stop_file.is_file(), receipt=complete_receipt(snap),
        pid_alive=pid_alive, stable=True, now_wall=time.time(),
        deadline_wall=deadline_wall, target_end_wall=target_end_wall,
        settle_mode=True)
    if outcome == STOP_REQUESTED:
        write_stopped(out_dir, opts.label, snap, stop_file, 0,
                      campaign=campaign)
        return EXIT_STOPPED
    if outcome in (NOT_ENDED, UNKNOWN_END_STATE):
        refuse_campaign_target(campaign, out_dir, 'out')
        _atomic_write(out_dir / 'closeout-status.json', json.dumps({
            'tool': TOOL, 'label': opts.label, 'outcome': outcome,
            'wall_utc': utc_iso(time.time()), 'pid_alive': pid_alive,
            'lock_pid': snap.get('lock_pid'),
            'receipt': complete_receipt(snap),
            'note': 'settle: end not definite; no summary written',
        }, indent=2, sort_keys=True) + '\n')
        return EXIT_NOT_ENDED
    summary = do_closeout(
        outcome=outcome, snapshot=snap, campaign=campaign, out_dir=out_dir,
        label=opts.label, started_wall=started_wall,
        first_poll_wall=started_wall, polls=1, interval=0,
        stability=0, deadline_wall=deadline_wall,
        target_end_wall=target_end_wall, stop_file=stop_file,
        driver_pid=opts.driver_pid, stability_assumed=True,
        config_path=opts.audit_config, manifest_path=opts.audit_manifest,
        baseline_path=opts.audit_baseline, extra=None)
    return EXIT_OK if summary.get('outcome') != CLOSEOUT_FAILED \
        else EXIT_INTERNAL


# -------------------------------------------------------------------- cli

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=TOOL)
    sub = parser.add_subparsers(dest='command', required=True)

    def common(sp, need_interval):
        sp.add_argument('--campaign', required=True)
        sp.add_argument('--out', required=True)
        sp.add_argument('--stop-file', required=True)
        sp.add_argument('--driver-pid', type=int)
        sp.add_argument('--deadline', required=True)
        sp.add_argument('--target-end')
        sp.add_argument('--audit-config')
        sp.add_argument('--audit-manifest')
        sp.add_argument('--audit-baseline')
        sp.add_argument('--label', default='soak-closeout')
        sp.add_argument('--checkpoint-dir')
        if need_interval:
            sp.add_argument('--interval', type=int, default=MIN_POLL_INTERVAL)
            sp.add_argument('--stability', type=int, default=900)

    waitp = sub.add_parser('wait')
    common(waitp, True)
    settlep = sub.add_parser('settle')
    common(settlep, False)
    statusp = sub.add_parser('status')
    statusp.add_argument('--campaign', required=True)
    args = parser.parse_args(argv)

    if args.command == 'status':
        print(json.dumps(campaign_snapshot(Path(args.campaign)), indent=2,
                         sort_keys=True, default=str))
        return EXIT_OK
    if args.command == 'wait':
        if args.interval < MIN_POLL_INTERVAL:
            parser.error(f'--interval must be >= {MIN_POLL_INTERVAL}s; '
                         'refusing high-frequency polling')
        return wait_loop(args)
    return settle_once(args)


if __name__ == '__main__':
    sys.exit(main())
