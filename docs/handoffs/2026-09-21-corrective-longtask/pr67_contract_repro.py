"""Finite historical reproducer for PR67 f2aff305, not a soak or a repair.

Uses only the pinned local source and small synthetic directories. It imports
actual functions, mocks the final audit/liveness in one checkpoint test, and
launches no process, network, provider or database. Exit 3 means reproduced
contract failures; it is expected on the pinned baseline, NOT an accepted gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

BASE = 'f2aff305cd30879ff4b15c6a7bbafd82856aaf75'
PINS = {
    'tests/support/soak_closeout.py': '0a634a8cbc03c7e4d6e5dbb0670783c5610829c2',
    'tests/support/soak_audit.py': '3f92c1571141725ee10a53bdc5417cb9ce0a06b0',
    'tests/support/soak_driver.py': 'a8934cef9fe9f9c9bcef064e999c97eada0dfa94',
}


def load_source(source: Path):
    for name, expected in PINS.items():
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('source_type')
        raw = path.read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if actual != expected:
            raise ValueError('not_the_pinned_historical_source')
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(source), str(source / 'src')]
    from tests.support import soak_closeout as module
    if Path(module.__file__).resolve() != source / 'tests/support/soak_closeout.py':
        raise ValueError('wrong_import_origin')
    return module


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def exercise(c, scratch: Path):
    findings, controls = [], []
    with tempfile.TemporaryDirectory(prefix='pr67-contract-', dir=scratch) as folder:
        root = Path(folder)
        campaign = root / 'synthetic-campaign'
        seg = campaign / 'segments/segment-000001'
        write_json(seg / 'segment.json', {'started_wall': 1000})
        write_json(seg / 'segment-close.json', {'reason': 'complete', 'stopped_wall': 260200})
        snap = {'segments': [{'name': 'segment-000001'}],
                'close_reasons': {'segment-000001': 'complete'}}
        config = {'max_wall_seconds': 259200, 'minimum_valid_rounds': 864}
        report = {'rounds_total': {'passed': 864, 'failed': 0, 'unknown': 0, 'interrupted': 0},
                  'inputs': {'receipt_declared_subinputs_sum': 100000,
                             'functional_seed_distinct': 1, 'payload_level_distinct': 1,
                             'pytest_logical_identities': ['one-fixed-test']}}
        gates = c.build_gates(report, config, c.ENDED_NORMALLY, snap, campaign)
        findings.append({'id': 'R1_DECLARATION_IS_NOT_DISTINCT_PROOF',
                         'reproduced': gates['min_distinct_inputs']['status'] == 'PASS',
                         'observed': gates['min_distinct_inputs']['status'],
                         'expected': 'not PASS without actual distinct-execution proof'})
        controls.append({'id': 'C1_FULL_WALL_CONTROL',
                         'passed': gates['observation_wall_target']['status'] == 'PASS'})

        failed_report = {**report, 'rounds_total': {'passed': 0, 'failed': 864, 'unknown': 0, 'interrupted': 0}}
        g = c.build_gates(failed_report, config, c.ENDED_NORMALLY, snap, campaign)
        findings.append({'id': 'R2_FAILED_ROUNDS_ARE_NOT_VALID_ROUNDS',
                         'reproduced': g['min_rounds']['status'] == 'PASS',
                         'observed': g['min_rounds'], 'expected': 'not PASS for minimum_valid_rounds'})

        write_json(seg / 'segment-close.json', {'reason': 'complete', 'stopped_wall': 260080})
        g = c.build_gates(report, config, c.ENDED_NORMALLY, snap, campaign)
        findings.append({'id': 'R3_120_SECOND_WALL_DEFICIT_ACCEPTED',
                         'reproduced': g['observation_wall_target']['status'] == 'PASS',
                         'observed': g['observation_wall_target'], 'expected': 'not PASS for a strict 72-hour target'})
        write_json(seg / 'segment-close.json', {'reason': 'complete', 'stopped_wall': 130600})
        g = c.build_gates(report, config, c.ENDED_NORMALLY, snap, campaign)
        controls.append({'id': 'C2_LARGE_DEFICIT_REJECTED',
                         'passed': g['observation_wall_target']['status'] != 'PASS'})

        latest = campaign / 'segments/segment-000002'
        write_json(latest / 'segment.json', {'started_wall': 1000})
        write_json(latest / 'segment-close.json', {'reason': 'stop_file', 'stopped_wall': 260200})
        mixed = {'segments': [{'name': 'segment-000001'}, {'name': 'segment-000002'}],
                 'close_reasons': {'segment-000001': 'complete', 'segment-000002': 'stop_file'}}
        outcome = c.detect_outcome(stop_seen=False, receipt=c.complete_receipt(mixed),
                                   pid_alive=False, stable=True, now_wall=300000,
                                   deadline_wall=400000, target_end_wall=200000)
        g = c.build_gates(report, config, outcome, mixed, campaign)
        findings.append({'id': 'R4_OLD_RECEIPT_BORROWED_BY_NEW_SEGMENT',
                         'reproduced': outcome == c.ENDED_NORMALLY and
                                       g['segment_close_complete']['status'] == 'PASS' and
                                       g['observation_wall_target']['status'] == 'PASS',
                         'observed': {'outcome': outcome, 'latest_reason': 'stop_file',
                                      'complete_gate': g['segment_close_complete']['status']},
                         'expected': 'bind receipt and time to the same completed segment'})

        out = root / 'closeout-output'
        options = SimpleNamespace(campaign=str(campaign), out=str(out), stop_file=str(out / 'stop'),
            deadline='2000-01-01T00:00:00Z', target_end=None, driver_pid=None, label='synthetic',
            stability=900, interval=300, checkpoint_dir=str(campaign),
            audit_config=None, audit_manifest=None, audit_baseline=None)
        with patch.object(c, 'campaign_snapshot', return_value={'missing': False}), \
             patch.object(c, 'do_closeout', return_value={'outcome': c.DEADLINE_REACHED}):
            result = c.wait_loop(options)
        wrote_inside = (campaign / 'closeout-checkpoint.json').is_file()
        findings.append({'id': 'R5_CHECKPOINT_PATH_ESCAPES_CAMPAIGN_GUARD',
                         'reproduced': wrote_inside, 'observed': {'wrote_inside_campaign': wrote_inside,
                                                                'wait_exit': result},
                         'expected': 'reject every campaign write target before any write'})
        rejected = False
        try:
            c.guard_paths(campaign, campaign / 'forbidden-output', out / 'stop')
        except SystemExit:
            rejected = True
        controls.append({'id': 'C3_EXISTING_OUTPUT_GUARD_WORKS', 'passed': rejected})

        lock = campaign / 'driver.lock'
        lock.write_bytes(b'{"pid":7}' + b' ' * (4 * c.LOCK_READ_CAP))
        observed_reads = []
        original_read = Path.read_bytes
        def instrument(path):
            raw = original_read(path)
            if path == lock:
                observed_reads.append(len(raw))
            return raw
        with patch.object(Path, 'read_bytes', instrument):
            c.campaign_snapshot(campaign)
        findings.append({'id': 'R6_READ_ALL_BEFORE_SLICING',
                         'reproduced': bool(observed_reads) and max(observed_reads) > c.LOCK_READ_CAP,
                         'observed': {'bytes_read': observed_reads, 'claimed_read_cap': c.LOCK_READ_CAP},
                         'expected': 'bound I/O before allocation, not after read_bytes'})
        lock.write_bytes(b'{"pid":7}')
        controls.append({'id': 'C4_SMALL_LOCK_CONTROL',
                         'passed': c.campaign_snapshot(campaign)['lock_pid'] == 7})
        recovery_root = root / 'repeat-recovery'
        (recovery_root / 'segments/segment-000001/rounds/round-000000001').mkdir(parents=True)
        def recovering_driver():
            driver = object.__new__(c.drv.SoakDriver)
            driver.root = recovery_root
            driver._segment_no = 0
            driver._totals = dict.fromkeys(c.drv.FINAL_STATES, 0)
            driver._distinct_inputs = set()
            return driver
        first = recovering_driver()
        first._recover()
        sidecar = recovery_root / 'segments/segment-000001/rounds/unknown/round-000000001.json'
        initial_sidecar = sidecar.read_bytes()
        controls.append({'id': 'C5_FIRST_UNKNOWN_RECOVERY',
                         'passed': first._totals['unknown'] == 1 and first._next_round_no == 2})
        second = recovering_driver()
        second._recover()
        findings.append({'id': 'R7_SECOND_RECOVERY_LOSES_UNKNOWN_COUNT',
                         'reproduced': first._totals['unknown'] == 1 and
                                       second._totals['unknown'] == 0 and
                                       sidecar.read_bytes() == initial_sidecar,
                         'observed': {'first_unknown': first._totals['unknown'],
                                      'second_unknown': second._totals['unknown'],
                                      'next_round_still_burned': second._next_round_no},
                         'expected': 'recount an existing unknown record once on every new recovery'})
    return findings, controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--scratch', required=True, help='Existing new-task scratch directory; never a live campaign')
    args = parser.parse_args()
    source, scratch = Path(args.source).resolve(), Path(args.scratch).resolve()
    if not source.is_dir() or not scratch.is_dir() or scratch == source or scratch.is_relative_to(source):
        raise ValueError('source_and_scratch_must_be_separate')
    module = load_source(source)
    findings, controls = exercise(module, scratch)
    body = {'schema': 'pr67-finite-contract-repro-v1', 'historical_source': BASE,
            'scope': 'finite actual-function tests; mocked final audit in checkpoint case; no live campaign',
            'findings': findings, 'controls': controls,
            'reproduced': sum(x['reproduced'] for x in findings),
            'controls_passed': sum(x['passed'] for x in controls),
            'official_cases_run': 0, 'activation_authorized': False}
    print(json.dumps(body, indent=2, sort_keys=True))
    if not all(x['passed'] for x in controls):
        return 5
    return 3 if any(x['reproduced'] for x in findings) else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'PROBE_SETUP_OR_INTERNAL_ERROR', 'type': type(error).__name__}))
        raise SystemExit(5) from None
