"""Read-only schedule arithmetic, NOT execution or distinct-input certification.

Matches derive_sub_seed and name sorting at PR67 head 2584f6f2. Does not
import/execute manifest code, start children, access network or write files.
Use only a reviewed, stable regular-file COPY of the proposed manifest.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import stat
import sys

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
REQUIRED_INPUTS = 100_000


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('invalid_integer')
    return value


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('duplicate_json_key')
        value[key] = item
    return value


def _constant(_value):
    raise ValueError('nonfinite_json')


def read_manifest(path: Path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & 0x400
            or not 1 <= info.st_size <= MAX_MANIFEST_BYTES):
        raise ValueError('manifest_not_regular_or_bounded')
    # A bounded regular-file copy, not a defense against a malicious host's
    # concurrent path/ancestor replacement or a universal I/O deadline.
    with path.open('rb') as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if not 1 <= len(raw) <= MAX_MANIFEST_BYTES:
        raise ValueError('manifest_size_changed')
    document = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=_constant)
    if type(document) is not dict or type(document.get('scenarios')) is not list:
        raise ValueError('manifest_schema')
    items = document['scenarios']
    if not 1 <= len(items) <= 256:
        raise ValueError('scenario_count')
    names, kinds = [], {}
    for item in items:
        if type(item) is not dict:
            raise ValueError('scenario_schema')
        name, kind = item.get('name'), item.get('kind')
        if (type(name) is not str or re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', name) is None
                or name in kinds or kind not in ('process', 'pytest')):
            raise ValueError('scenario_identity')
        names.append(name)
        kinds[name] = kind
    return names, kinds, sha256(raw).hexdigest()


def distribution(names, seed, rounds):
    _integer(seed, 1, 2**63 - 1)
    _integer(rounds, 1, 100_000)
    if type(names) is not list or not 1 <= len(names) <= 256:
        raise ValueError('scenario_count')
    if any(type(n) is not str or re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', n) is None for n in names):
        raise ValueError('scenario_identity')
    if len(set(names)) != len(names):
        raise ValueError('duplicate_scenario')
    ordered = sorted(names)
    counts = Counter({name: 0 for name in ordered})
    for number in range(1, rounds + 1):
        digest = sha256(f'soak:{seed}:{number}'.encode('ascii')).hexdigest()
        counts[ordered[int(digest[:16], 16) % len(ordered)]] += 1
    return dict(counts)


def plan(names, kinds, digest, *, seed, rounds, compute_name, subinputs):
    _integer(subinputs, 1, 4096)
    counts = distribution(names, seed, rounds)
    if compute_name not in counts or kinds.get(compute_name) != 'process':
        raise ValueError('compute_scenario_missing_or_not_process')
    count = counts[compute_name]
    gross = count * subinputs
    return {
        'schema': 'pal-schedule-arithmetic-v1',
        'status': 'ARITHMETIC_ONLY_NOT_EXECUTION_PROOF',
        'manifest_sha256': digest,
        'seed': seed,
        'planned_rounds': rounds,
        'selection_rule': 'sorted-names/sha256-soak-seed-round-first16-mod-n',
        'scenario_rounds': counts,
        'compute_scenario': compute_name,
        'compute_rounds': count,
        'subinputs_per_compute_round': subinputs,
        'planned_generated_slots': gross,
        'required_completed_distinct_inputs': REQUIRED_INPUTS,
        'required_per_compute_round_if_all_distinct': ((REQUIRED_INPUTS + count - 1) // count) if count else None,
        'arithmetic_reachable_if_all_complete_and_distinct': gross >= REQUIRED_INPUTS,
        'declared_nominal_manifest_has_11_scenarios': len(names) == 11,
        'actual_completed_distinct_inputs': None,
        'observation_seconds_verified': None,
        'activation_authorized': False,
        'warning': 'No input generation, target execution, deduplication, fault coverage, duration or launch validation performed.',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=2026092103)
    parser.add_argument('--rounds', type=int, default=864)
    parser.add_argument('--compute-name', default='ok-compute')
    parser.add_argument('--subinputs', type=int, default=2048)
    args = parser.parse_args(argv)
    try:
        names, kinds, digest = read_manifest(args.manifest)
        report = plan(names, kinds, digest, seed=args.seed, rounds=args.rounds,
                      compute_name=args.compute_name, subinputs=args.subinputs)
    except (OSError, ValueError, TypeError, RecursionError):
        print('{"status":"INVALID_PLAN_INPUT","activation_authorized":false}')
        return 5
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if (report['arithmetic_reachable_if_all_complete_and_distinct']
                 and report['declared_nominal_manifest_has_11_scenarios']) else 3


if __name__ == '__main__':
    raise SystemExit(main())
