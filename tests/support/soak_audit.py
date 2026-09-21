"""Offline read-only evidence audit for soak_driver campaigns (PX-01).

The driver's ``inspect`` summarises campaign state; this module adjudicates
evidence invariants the summary does not decide, without starting any process,
touching any network/database, or writing anywhere (the CLI prints to stdout
and only writes a file when ``--json-out`` explicitly names one):

- identity: campaign.json schema/SHA fields, cross-consistency with every
  segment header, optional expected-value overrides;
- rounds: directory/record round-number agreement, global uniqueness, holes in
  a segment's numbering, start/final pairing, recovery-sidecar conflicts;
- seeds: sub-seed == derive_sub_seed(master, round), scenario == sorted
  scenarios[sub_seed % n], payload SHA recomputation, child receipt echo
  cross-checks (echo_seed / stdin_sha256 when present);
- logical inputs: strict accounting that EXCLUDES round number, segment and
  tmp path from input identity, plus duplicate-functional-input detection;
- clocks and observation: heartbeat gaps, wall-vs-monotonic drift (rollback /
  forward jump), closed-segment span vs max_wall_seconds (a "complete" close
  observed for less than the wall gate is FAIL), stopped-wall vs heartbeat
  consistency, summary/segment-close agreement with the round records;
- inventory: driver.log round_final closure, junit XML presence on failed
  pytest rounds, receipt/XML test counts against an optional collection
  baseline;
- resources: heartbeats must not record unmeasured values as zero.

Every check reports PASS / FAIL / UNKNOWN. While the campaign is live (lock
holder alive, a round mid-flight, or last segment unclosed), torn or in-flight
reads are reported as SNAPSHOT_INCOMPLETE and are never judged as corruption
or as passing; the original files are never modified.

Usage:
  python tests/support/soak_audit.py audit --campaign DIR [--config FILE]
      [--manifest FILE] [--master-seed N] [--baseline FILE]
      [--expect key=value ...] [--json-out FILE]

Exit codes: 0 no FAIL, 3 FAIL found, 5 invalid invocation, 1 internal error.
"""
from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
import re
import sys
import time

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.support import soak_driver as drv  # noqa: E402

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_FAIL = 3
EXIT_CONFIG = 5

PASS, FAIL, UNKNOWN = 'PASS', 'FAIL', 'UNKNOWN'
INCOMPLETE = 'SNAPSHOT_INCOMPLETE'
_HEX64 = re.compile(r'[0-9a-f]{64}')


def check(kind, status, detail=None):
    return {'check': kind, 'status': status, 'detail': detail}


def parse_jsonl(path: Path):
    """Return (entries, torn_tail). A trailing fragment is torn, not judged."""
    entries, torn = [], False
    if not path.is_file():
        return entries, torn
    raw = path.read_bytes()
    lines = raw.split(b'\n')
    complete = lines[:-1] if raw.endswith(b'\n') else lines[:-1]
    tail = None if raw.endswith(b'\n') else lines[-1]
    if tail is not None:
        torn = True
    for line in complete:
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            torn = True
    if tail is not None and tail.strip():
        try:
            entries.append(json.loads(tail))
        except ValueError:
            pass  # half-written snapshot line; leave unjudged
    return entries, torn


def recompute_payload_hash(record: dict, round_dir: Path) -> str | None:
    """Location-derived payload hash (in-place audit semantics): canonical
    payload of {round, segment, sub_seed, scenario, tmp_dir} with tmp_dir
    under the audited campaign root."""
    try:
        payload = drv._canonical({
            'round': record['round'], 'segment': record['segment'],
            'sub_seed': record['sub_seed'], 'scenario': record['scenario'],
            'tmp_dir': str(round_dir / 'tmp')}) + b'\n'
    except (KeyError, TypeError, ValueError):
        return None
    return sha256(payload).hexdigest()


def payload_hash_matches(record: dict, round_dir: Path,
                         original_root: Path | None = None) -> bool:
    if not isinstance(record.get('input_sha256'), str):
        return False
    def _hash(tmp_dir: str):
        return sha256(drv._canonical({
            'round': record['round'], 'segment': record['segment'],
            'sub_seed': record['sub_seed'], 'scenario': record['scenario'],
            'tmp_dir': tmp_dir}) + b'\n').hexdigest()
    try:
        candidates = [str(round_dir / 'tmp')]
        if original_root is not None:
            candidates.append(str(original_root / 'segments'
                                  / round_dir.parents[1].name / 'rounds'
                                  / round_dir.name / 'tmp'))
        return record['input_sha256'] in {_hash(c) for c in candidates}
    except (KeyError, TypeError, ValueError):
        return False


def audit_campaign(root: Path, *, config=None, scenarios=None, scenario_objects=None,
                   expected: dict | None = None, baseline: dict | None = None,
                   clock_tolerance: float = 5.0, wall_tolerance: float | None = None):
    """Read-only adjudication of one campaign directory. Returns a report.

    ``config``: parsed SoakConfig config dict (master_seed, max_wall_seconds,
    minimum_valid_rounds, max_unobserved_gap_seconds, heartbeat_seconds...).
    ``scenarios``: ordered scenario names as the driver sorts them (sorted by
    name); when absent, loaded from the manifest the config names, if readable.
    ``expected``: {field: value} overrides checked against campaign.json.
    ``baseline``: {scenario_name: collected_test_count} for inventory closure.
    """
    root = Path(root)
    checks: list[dict] = []
    incomplete: list[dict] = []
    report = {'tool': 'soak_audit', 'campaign': str(root),
              'audit_wall': time.time(), 'checks': checks,
              'snapshot_incomplete': incomplete}

    cfg = None
    if config is not None:
        try:
            cfg = drv.SoakConfig.from_dict(config)
        except drv.SoakConfigError as error:
            checks.append(check('config_valid', FAIL, str(error)))
        else:
            checks.append(check('config_valid', PASS))
    scenario_objects = scenario_objects or None
    if scenarios is None and cfg is not None:
        manifest_path = Path(cfg.scenario_manifest)
        try:
            scenario_objects, _sha = drv._load_manifest(manifest_path)
            scenarios = [s.name for s in scenario_objects]
        except (OSError, drv.SoakConfigError):
            scenario_objects, scenarios = None, None
    scenario_names = sorted(scenarios) if scenarios else None
    scenario_kinds = ({s.name: s.kind for s in scenario_objects}
                      if scenario_objects else None)

    def is_pytest_scenario(name) -> bool:
        if scenario_kinds is not None:
            return scenario_kinds.get(name) == 'pytest'
        return str(name).startswith('pytest-')  # best effort without a manifest
    # ---------- identity ----------
    identity = drv._read_json(root / 'campaign.json')
    if identity is None:
        checks.append(check('identity_schema', FAIL, 'campaign.json unreadable'))
        report['overall'] = FAIL
        return report
    if identity.get('schema') != 'pal-soak-campaign-v1':
        checks.append(check('identity_schema', FAIL, identity.get('schema')))
    else:
        checks.append(check('identity_schema', PASS))
    sha_fields = ('driver_sha256', 'python_sha256', 'config_sha256', 'manifest_sha256')
    bad = [f for f in sha_fields if not isinstance(identity.get(f), str)
           or _HEX64.fullmatch(identity[f]) is None]
    checks.append(check('identity_sha_format', FAIL if bad else PASS, bad or None))
    if cfg is not None:
        want = drv._sha256_bytes(cfg.canonical_bytes())
        checks.append(check('config_sha_match',
                            PASS if identity.get('config_sha256') == want else FAIL,
                            {'recorded': identity.get('config_sha256'), 'recomputed': want}))
    if expected:
        diff = {k: {'expected': v, 'found': identity.get(k)}
                for k, v in expected.items() if identity.get(k) != v}
        checks.append(check('identity_expected', FAIL if diff else PASS, diff or None))

    # ---------- collect segments / rounds ----------
    segments_dir = root / 'segments'
    segment_dirs = sorted(p for p in segments_dir.iterdir()
                          if p.is_dir() and re.fullmatch(r'segment-\d{6}', p.name)) \
        if segments_dir.is_dir() else []
    if not segment_dirs:
        checks.append(check('segments_present', FAIL, 'no segment directories'))

    mixed = []
    for segment in segment_dirs:
        header = drv._read_json(segment / 'segment.json')
        if header is None:
            continue
        for field in sha_fields:
            if field in header and header[field] != identity.get(field):
                mixed.append({'segment': segment.name, 'field': field})
    checks.append(check('identity_segment_consistency',
                        FAIL if mixed else PASS, mixed or None))

    lock = drv._read_json(root / 'driver.lock')
    lock_alive = bool(lock) and drv._pid_alive(lock.get('pid')) is True

    records: dict[int, list[tuple[str, dict, Path]]] = {}
    sidecar_conflicts = []
    totals = {'passed': 0, 'failed': 0, 'interrupted': 0, 'unknown': 0}
    payload_hashes = set()
    functional_keys: dict[tuple, int] = {}
    duplicate_functional = []
    receipt_subinputs = 0
    pytest_invocations = 0
    running_rounds = []
    bad_finals = []
    dir_mismatches = []
    seed_bad, scenario_bad, payload_bad, receipt_bad = [], [], [], []
    failed_pytest_xml = {}
    unclosed_segments = []

    for segment in segment_dirs:
        seg_name = segment.name
        rounds_root = segment / 'rounds'
        round_dirs = sorted(p for p in rounds_root.iterdir()
                            if p.is_dir() and re.fullmatch(r'round-\d{9}', p.name)) \
            if rounds_root.is_dir() else []
        numbers = [int(p.name.split('-')[1]) for p in round_dirs]
        holes = [n for n in range(min(numbers), max(numbers) + 1)
                 if n not in set(numbers)] if numbers else []
        checks.append(check('rounds_sequence', FAIL if holes else PASS,
                            {'segment': seg_name, 'missing': holes} if holes else
                            {'segment': seg_name, 'count': len(numbers)}))
        for round_dir in round_dirs:
            record = drv._read_json(round_dir / 'round.json')
            if record is None:
                totals['unknown'] += 1
                checks.append(check('round_record_readable', FAIL,
                                    {'round_dir': round_dir.name, 'segment': seg_name}))
                continue
            rno = record.get('round')
            records.setdefault(rno, []).append((seg_name, record, round_dir))
            if round_dir.name != f'round-{rno:09d}':
                dir_mismatches.append({'segment': seg_name, 'dir': round_dir.name,
                                       'record_round': rno})
            status, final = record.get('status'), record.get('final')
            if status == 'running':
                running_rounds.append({'segment': seg_name, 'round': rno})
            elif status == 'final' and final in drv.FINAL_STATES:
                totals[final] += 1
            else:
                bad_finals.append({'segment': seg_name, 'round': rno,
                                   'status': status, 'final': final})
            if isinstance(record.get('input_sha256'), str):
                payload_hashes.add(record['input_sha256'])
            key = (record.get('scenario'), record.get('sub_seed'))
            if key in functional_keys:
                duplicate_functional.append({'round': rno, 'key': list(key),
                                             'first_round': functional_keys[key]})
            else:
                functional_keys[key] = rno
            receipt = record.get('receipt')
            if isinstance(receipt, dict):
                if isinstance(receipt.get('inputs'), int):
                    receipt_subinputs += receipt['inputs']
                if isinstance(receipt.get('tests'), int):
                    pytest_invocations += receipt['tests']
                if isinstance(receipt.get('echo_seed'), int) \
                        and receipt['echo_seed'] != record.get('sub_seed'):
                    receipt_bad.append({'round': rno, 'field': 'echo_seed'})
                if isinstance(receipt.get('stdin_sha256'), str) \
                        and receipt['stdin_sha256'] != record.get('input_sha256'):
                    receipt_bad.append({'round': rno, 'field': 'stdin_sha256'})
            scen_name = str(record.get('scenario'))
            if is_pytest_scenario(scen_name) and final == 'failed':
                xml = round_dir / 'tmp' / 'pytest.xml'
                entry = {'segment': seg_name, 'round': rno}
                if xml.is_file():
                    try:
                        import xml.etree.ElementTree as etree
                        node = etree.parse(xml).getroot()
                        if node.tag == 'testsuites':
                            node = node.find('testsuite')
                        entry.update({k: int(node.get(k, 0)) for k in
                                      ('tests', 'failures', 'errors', 'skipped')})
                    except (OSError, ValueError, AttributeError, TypeError):
                        entry['parse_error'] = True
                else:
                    entry['missing'] = True
                failed_pytest_xml[rno] = entry
        unknown_dir = rounds_root / 'unknown'
        if unknown_dir.is_dir():
            for side in unknown_dir.glob('*.json'):
                rno = int(re.sub(r'\D', '', side.name) or 0)
                if rno in records:
                    sidecar_conflicts.append({'segment': seg_name, 'round': rno})
        if drv._read_json(segment / 'segment-close.json') is None:
            unclosed_segments.append(seg_name)

    duplicates = {rno: [s for s, _rec, _p in entries]
                  for rno, entries in records.items() if len(entries) > 1}
    checks.append(check('rounds_uniqueness', FAIL if duplicates else PASS,
                        duplicates or {'rounds': len(records)}))
    checks.append(check('rounds_dir_record_match', FAIL if dir_mismatches else PASS,
                        dir_mismatches or None))
    checks.append(check('rounds_final_pairing', FAIL if bad_finals else PASS,
                        bad_finals or None))
    checks.append(check('rounds_sidecar_conflict', FAIL if sidecar_conflicts else PASS,
                        sidecar_conflicts or None))

    # ---------- seeds / scenario mapping / payload ----------
    master = cfg.master_seed if cfg is not None else None
    if master is None or scenario_names is None:
        checks.append(check('seeds_derivation', UNKNOWN,
                            'master seed or scenario list unavailable'))
        checks.append(check('seeds_scenario_mapping', UNKNOWN,
                            'scenario list unavailable'))
    else:
        for rno, entries in sorted(records.items()):
            for seg_name, record, _p in entries:
                want = drv.derive_sub_seed(master, rno)
                if record.get('sub_seed') != want:
                    seed_bad.append({'round': rno, 'segment': seg_name,
                                     'found': record.get('sub_seed'), 'expected': want})
                expected_scenario = scenario_names[want % len(scenario_names)]
                if record.get('scenario') != expected_scenario:
                    scenario_bad.append({'round': rno, 'segment': seg_name,
                                         'found': record.get('scenario'),
                                         'expected': expected_scenario})
        checks.append(check('seeds_derivation', FAIL if seed_bad else PASS,
                            seed_bad or {'rounds_checked': len(records)}))
        checks.append(check('seeds_scenario_mapping', FAIL if scenario_bad else PASS,
                            scenario_bad or None))
    payload_bad = []
    stop_path = identity.get('stop_path')
    original_root = Path(stop_path).parent if isinstance(stop_path, str) else None
    for rno, entries in sorted(records.items()):
        for seg_name, record, round_dir in entries:
            if not payload_hash_matches(record, round_dir, original_root):
                payload_bad.append({'round': rno, 'segment': seg_name,
                                    'found': record.get('input_sha256')})
    checks.append(check('payload_hash', FAIL if payload_bad else PASS,
                        payload_bad or None))
    checks.append(check('receipt_echo', FAIL if receipt_bad else PASS,
                        receipt_bad or None))

    # ---------- logical inputs (strict) ----------
    checks.append(check('inputs_duplicate_functional', FAIL if duplicate_functional else PASS,
                        duplicate_functional or None))
    pytest_identities = sorted({str(k[0]) for k in functional_keys
                                if is_pytest_scenario(k[0])})
    report['inputs'] = {
        'note': 'input identity excludes round number, segment and tmp path; '
                'round-dependent payload hashes alone never count as distinct '
                'logical inputs',
        'payload_level_distinct': len(payload_hashes),
        'functional_seed_distinct': len(functional_keys),
        'receipt_declared_subinputs_sum': receipt_subinputs,
        'pytest_test_invocations_sum': pytest_invocations,
        'pytest_logical_identities': pytest_identities,
    }

    # ---------- heartbeats / clocks / observation ----------
    max_gap = cfg.max_unobserved_gap_seconds if cfg is not None \
        else identity.get('max_unobserved_gap_seconds')
    live = lock_alive or bool(running_rounds) or bool(unclosed_segments)
    report['live'] = live
    for entry in running_rounds:
        incomplete.append({'kind': 'round_in_flight', **entry})
    all_gaps, all_hbs = [], 0
    for segment in segment_dirs:
        heartbeats, torn = parse_jsonl(segment / 'heartbeats.jsonl')
        all_hbs += len(heartbeats)
        if torn:
            if live:
                incomplete.append({'kind': 'torn_heartbeat_tail', 'segment': segment.name})
            else:
                checks.append(check('torn_heartbeat_tail', FAIL,
                                    {'segment': segment.name,
                                     'note': 'campaign closed but a heartbeat line is '
                                             'torn; evidence incomplete'}))
        walls = [h['wall'] for h in heartbeats if isinstance(h.get('wall'), (int, float))]
        monos = [h['monotonic'] for h in heartbeats
                 if isinstance(h.get('monotonic'), (int, float))]
        gaps = [{'segment': segment.name, 'wall': a, 'gap_seconds': round(b - a, 3)}
                for a, b in zip(walls, walls[1:])
                if isinstance(max_gap, (int, float)) and b - a > max_gap]
        all_gaps.extend(gaps)
        header = drv._read_json(segment / 'segment.json') or {}
        if len(walls) >= 2 and len(monos) >= 2:
            drift = (walls[-1] - walls[0]) - (monos[-1] - monos[0])
            if drift < -clock_tolerance:
                checks.append(check('clock_rollback', FAIL,
                                    {'segment': segment.name, 'drift_seconds': round(drift, 3)}))
            elif drift > clock_tolerance:
                checks.append(check('clock_forward_jump', FAIL,
                                    {'segment': segment.name, 'drift_seconds': round(drift, 3)}))
            else:
                checks.append(check('clock_consistency', PASS,
                                    {'segment': segment.name, 'drift_seconds': round(drift, 3)}))
        bad_rss = [h for h in heartbeats if h.get('rss_measured') is True and (
            not isinstance(h.get('rss_bytes'), int) or h['rss_bytes'] <= 0)]
        bad_bytes = [h for h in heartbeats if h.get('campaign_bytes_measured') is True
                     and h.get('campaign_bytes') == 0 and records]
        if bad_rss or bad_bytes:
            checks.append(check('resources_plausibility', FAIL,
                                {'segment': segment.name, 'rss_zero': len(bad_rss),
                                 'campaign_bytes_zero': len(bad_bytes)}))
        if heartbeats and all(h.get('rss_measured') is not True for h in heartbeats):
            checks.append(check('resources_plausibility', UNKNOWN,
                                {'segment': segment.name, 'note': 'rss never measured'}))
    checks.append(check('observation_gap', FAIL if all_gaps else PASS,
                        all_gaps or {'heartbeats': all_hbs}))

    # ---------- observation span / completion truth ----------
    max_wall = cfg.max_wall_seconds if cfg is not None else None
    min_rounds = cfg.min_rounds if cfg is not None else None
    total_rounds = sum(totals.values())
    for segment in segment_dirs:
        close = drv._read_json(segment / 'segment-close.json')
        header = drv._read_json(segment / 'segment.json') or {}
        started = header.get('started_wall')
        if close is None:
            if not live:
                checks.append(check('segment_close_present', UNKNOWN,
                                    {'segment': segment.name,
                                     'note': 'no close record and no live holder'}))
            continue
        stopped = close.get('stopped_wall')
        span = round(stopped - started, 3) if isinstance(stopped, (int, float)) \
            and isinstance(started, (int, float)) else None
        heartbeats, _torn = parse_jsonl(segment / 'heartbeats.jsonl')
        walls = [h['wall'] for h in heartbeats if isinstance(h.get('wall'), (int, float))]
        hb_span = round(walls[-1] - started, 3) if walls and isinstance(started, (int, float)) \
            else None
        hb_period = cfg.heartbeat_seconds if cfg is not None else 60.0
        slack = 2 * hb_period + 10.0
        if span is not None and hb_span is not None and span - hb_span > slack:
            checks.append(check('inconsistent_span', FAIL,
                                {'segment': segment.name, 'stopped_span': span,
                                 'heartbeat_span': hb_span}))
        if close.get('reason') == 'complete':
            if isinstance(min_rounds, int):
                claimed = (close.get('rounds_total') or {}).get('passed', 0) \
                    + (close.get('rounds_total') or {}).get('failed', 0) \
                    + (close.get('rounds_total') or {}).get('interrupted', 0) \
                    + (close.get('rounds_total') or {}).get('unknown', 0)
                if claimed < min_rounds:
                    checks.append(check('false_completion', FAIL,
                                        {'segment': segment.name, 'claimed': claimed,
                                         'min_rounds': min_rounds}))
            if isinstance(max_wall, (int, float)) and span is not None:
                tolerance = wall_tolerance if wall_tolerance is not None \
                    else max(0.5, 0.001 * max_wall)
                if span < max_wall - tolerance:
                    checks.append(check('short_observation', FAIL,
                                        {'segment': segment.name, 'observed_span': span,
                                         'max_wall_seconds': max_wall,
                                         'tolerance': round(tolerance, 3)}))
                else:
                    checks.append(check('observation_span', PASS,
                                        {'segment': segment.name, 'observed_span': span}))
            elif isinstance(max_wall, (int, float)):
                checks.append(check('observation_span', UNKNOWN,
                                    {'segment': segment.name, 'note': 'span not computable'}))
        # per-segment summary consistency (recount from records)
        seg_counts = {'passed': 0, 'failed': 0, 'interrupted': 0, 'unknown': 0}
        for rno, entries in records.items():
            for seg_name, record, _p in entries:
                if seg_name == segment.name and record.get('final') in seg_counts:
                    seg_counts[record['final']] += 1
        claimed_totals = close.get('rounds_total')
        if isinstance(claimed_totals, dict) and claimed_totals != seg_counts:
            checks.append(check('summary_mismatch', FAIL,
                                {'segment': segment.name, 'claimed': claimed_totals,
                                 'recounted': seg_counts}))
        else:
            checks.append(check('summary_consistency', PASS, {'segment': segment.name,
                                                              'recounted': seg_counts}))
    if live:
        first = segment_dirs[0] / 'segment.json'
        header = drv._read_json(first) or {}
        started = header.get('started_wall')
        if isinstance(started, (int, float)):
            report['observed_so_far_seconds'] = round(report['audit_wall'] - started, 1)
        checks.append(check('completion_gates', UNKNOWN,
                            {'note': 'campaign live; final gates not yet decidable',
                             'rounds_total': totals,
                             'min_rounds': min_rounds, 'max_wall_seconds': max_wall}))

    # ---------- driver.log closure ----------
    log_events = []
    torn_log = False
    for name in ('driver.log.1', 'driver.log'):
        events, torn = parse_jsonl(root / name)
        log_events.extend(events)
        if torn:
            if live:
                incomplete.append({'kind': 'torn_driver_log_tail', 'file': name})
            else:
                checks.append(check('torn_driver_log_tail', FAIL,
                                    {'file': name}))
    finals_logged = [e.get('round') for e in log_events if e.get('event') == 'round_final']
    finalized = sum(totals.values())
    log_problems = {}
    if len(finals_logged) != finalized:
        log_problems['count_mismatch'] = {'logged': len(finals_logged),
                                          'records': finalized}
    if len(finals_logged) != len(set(finals_logged)):
        log_problems['duplicate_round_events'] = True
    logged_failed = {e.get('round') for e in log_events
                     if e.get('event') == 'round_final' and e.get('final') == 'failed'}
    recorded_failed = {rno for rno, entries in records.items()
                       for _s, rec, _p in entries if rec.get('final') == 'failed'}
    if logged_failed != recorded_failed:
        log_problems['failed_set_mismatch'] = {
            'only_in_log': sorted(logged_failed - recorded_failed),
            'only_in_records': sorted(recorded_failed - logged_failed)}
    checks.append(check('driver_log_closure', FAIL if log_problems else PASS,
                        log_problems or {'round_final_events': len(finals_logged)}))

    # ---------- first failure ----------
    failed_rounds = sorted(recorded_failed)
    first = drv._read_json(root / 'first-failure.json')
    if failed_rounds:
        if first is None:
            checks.append(check('first_failure', FAIL,
                                {'note': 'failed rounds exist but first-failure.json '
                                         'is missing', 'failed_rounds': failed_rounds}))
        else:
            fr = first.get('round')
            if fr not in failed_rounds:
                checks.append(check('first_failure', FAIL,
                                    {'note': 'first-failure round is not a failed round',
                                     'claimed': fr, 'failed_rounds': failed_rounds}))
            else:
                checks.append(check('first_failure', PASS, {'round': fr,
                                                            'failed_rounds': failed_rounds}))
    else:
        checks.append(check('first_failure', PASS,
                            {'note': 'no failed rounds', 'first_failure_present': first is not None}))

    # ---------- inventory ----------
    missing_xml = [v for v in failed_pytest_xml.values() if v.get('missing')
                   or v.get('parse_error')]
    if failed_pytest_xml:
        if missing_xml:
            checks.append(check('inventory_xml', UNKNOWN,
                                {'note': 'failed pytest round without readable junit XML',
                                 'rounds': [v.get('round') for v in missing_xml]}))
        else:
            checks.append(check('inventory_xml', PASS,
                                {'failed_pytest_rounds': len(failed_pytest_xml)}))
    if baseline:
        under, over = [], []
        for rno, entries in sorted(records.items()):
            for seg_name, record, round_dir in entries:
                scen = record.get('scenario')
                if scen not in baseline or not is_pytest_scenario(scen):
                    continue
                want = baseline[scen]
                found = None
                receipt = record.get('receipt')
                if isinstance(receipt, dict) and isinstance(receipt.get('tests'), int):
                    found = receipt['tests']
                else:
                    xml_entry = failed_pytest_xml.get(rno)
                    if xml_entry and isinstance(xml_entry.get('tests'), int):
                        found = xml_entry['tests']
                if found is None:
                    continue
                if found < want:
                    under.append({'round': rno, 'scenario': scen, 'found': found,
                                  'baseline': want})
                elif found > want:
                    over.append({'round': rno, 'scenario': scen, 'found': found,
                                 'baseline': want})
        problems = {}
        if under:
            problems['undercount'] = under
        if over:
            problems['overcount'] = over
        checks.append(check('inventory_baseline', FAIL if problems else PASS,
                            problems or {'scenarios': sorted(baseline)}))
    else:
        checks.append(check('inventory_baseline', UNKNOWN,
                            {'note': 'no collection baseline provided'}))

    report['rounds_total'] = totals
    report['failed_pytest_xml'] = failed_pytest_xml
    failures = [c for c in checks if c['status'] == FAIL]
    report['failures'] = failures
    if failures:
        report['overall'] = FAIL
    elif live:
        report['overall'] = INCOMPLETE  # snapshot of a still-running campaign
    else:
        report['overall'] = PASS
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='soak_audit')
    sub = parser.add_subparsers(dest='command', required=True)
    audit = sub.add_parser('audit')
    audit.add_argument('--campaign', required=True)
    audit.add_argument('--config')
    audit.add_argument('--manifest')
    audit.add_argument('--master-seed', type=int)
    audit.add_argument('--baseline')
    audit.add_argument('--expect', action='append', default=[])
    audit.add_argument('--json-out')
    audit.add_argument('--clock-tolerance', type=float, default=5.0)
    audit.add_argument('--wall-tolerance', type=float)
    args = parser.parse_args(argv)
    try:
        config = None
        scenarios = None
        scenario_objects = None
        if args.config:
            config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        if args.manifest:
            loaded, _sha = drv._load_manifest(Path(args.manifest))
            scenarios = [s.name for s in loaded]
            scenario_objects = loaded
        expected = {}
        for item in args.expect:
            key, _sep, value = item.partition('=')
            expected[key] = value
        baseline = None
        if args.baseline:
            baseline = json.loads(Path(args.baseline).read_text(encoding='utf-8'))
        report = audit_campaign(Path(args.campaign).resolve(), config=config,
                                scenarios=scenarios, scenario_objects=scenario_objects,
                                expected=expected or None,
                                baseline=baseline, clock_tolerance=args.clock_tolerance,
                                wall_tolerance=args.wall_tolerance)
    except (OSError, ValueError, drv.SoakConfigError) as error:
        print(f'SOAK_AUDIT_CONFIG_INVALID {error.__class__.__name__}')
        return EXIT_CONFIG
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.json_out:
        Path(args.json_out).write_text(text + '\n', encoding='utf-8')
    else:
        print(text)
    return EXIT_FAIL if report['overall'] == FAIL else EXIT_OK


if __name__ == '__main__':
    raise SystemExit(main())
