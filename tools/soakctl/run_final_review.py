"""Strict-caliber final reviewer for the ORIGINAL LT-04 soak campaign terminal
state (PAL_CORRECTIVE_LONGTASK_20260921_V3, final-review prep stage).

N5 parameterized copy (PAL_RV05_CAPACITY_20260921, node N5): the strict
caliber's expected identity (commit/tree/support-module hashes, optional
python/config/manifest digests, campaign+receipt schema expectations) is
injectable via --identity-json / --expected-* / --expect-*-schema flags so
the reviewer can later be bound to the NEW candidate. Defaults keep the
frozen V3 FREEZE_RECORD values: a no-flag invocation behaves exactly like
the frozen reviewer. New rejection semantics: (1) schema-mixing - a campaign
declaring a receipt schema this binding does not expect (or any receipt
schema under a legacy binding) can never be adjudicated PASS: verdict
UNKNOWN with an explicit schema-binding reason unless a concrete FAIL is
proven; (2) explicitly bound hashes are pinned inside the audit
(identity_expected) and against the driver file - drift is FAIL;
(3) an --identity-json without the complete core identity is an invalid
invocation (exit 5), never a silent legacy fallback.

The old closeout (the historical loose-semantics copy; PID deliberately
not recorded in this distributable file) will write a
FINAL_REVIEW_READY marker and a final-summary built on the OLD loose gates
(R1-R4/R6 unfixed). Under the V3 rules that material is only "pending-review
material". This tool re-adjudicates the SAME campaign terminal state with the
FROZEN TREE's corrected gates (commit 2584f6f2, tree 702d25b5):

- full read-only ``soak_audit.audit_campaign`` adjudication with the campaign's
  own config (min_rounds from ``minimum_valid_rounds``, strict wall floor from
  ``max_wall_seconds``) and the R1 ``--min-distinct-inputs`` verified-inputs
  gate (a receipt's declared sub-input sum is never execution proof);
- campaign identity verification beyond the audit: config_sha256 (canonical
  recompute), manifest_sha256 (canonical identity recompute from the manifest
  file), driver_sha256 / python_sha256 (file rehash) — mismatch is FAIL;
- liveness refusal: while the campaign's lock/driver PID is still alive the
  final review REFUSES (verdict NOT_RUN, reason still_running) and records one
  bounded read-only progress snapshot; it never audits a live campaign as if
  it were final;
- half-written terminal states: a settled campaign whose audit still looks
  live (round mid-flight, segment unclosed, live lock holder) is reported as
  UNKNOWN (SNAPSHOT_INCOMPLETE), never as corruption and never as passing;
- strict wrapper gates (PASS/FAIL/UNKNOWN each, NOT_RUN when refused):
  planned_end_complete_receipt (complete receipt bound to the latest closed
  segment, segment-\\d{6} pattern binding), observation_wall_target (strict
  floor, deficit recorded), min_rounds_passed_only (only actually passed
  rounds count), min_distinct_inputs_verified (only audit-verified distinct
  logical inputs count).

Outputs (never written inside the audited campaign; R5-style write-target
guard, mirroring the frozen soak_closeout):

- ``final-review.json`` — itemized final-review report with an embedded full
  audit report and a verdict in {PASS, FAIL, UNKNOWN, NOT_RUN};
- ``FINAL_REVIEW_STRICT`` — marker written ONLY when a full strict audit
  adjudication actually executed (verdict PASS/FAIL/UNKNOWN; never for NOT_RUN
  refusals). Distinct from the old closeout's FINAL_REVIEW_READY.

It CANNOT and DOES NOT: restart anything, create or touch the campaign's stop
file, write inside the campaign, call models or official CLIs, use credentials
or business databases, or push. Constants: official_cases_run=0,
sandbox_started=false, activation_authorized=false.

Exit codes: 0 PASS, 3 FAIL, 4 UNKNOWN, 2 NOT_RUN (refused/missing),
5 invalid invocation, 1 internal error.

Usage (after the audited soak has definitively ended; every <...> is a
placeholder for a path/value on the invoking host - this distributable
copy carries no host paths of its own):

  python -I -B tools/soakctl/run_final_review.py review ^
    --campaign <campaign dir> ^
    --config <soak config.json> ^
    --manifest <soak manifest.json> ^
    [--baseline <collection-baseline.json>] ^
    --driver-file <repo tests\\support\\soak_driver.py> ^
    --python-file <the python.exe the soak ran under> ^
    [--driver-pid <live pid, for the still-running refusal only>] ^
    [--target-end <planned end UTC>] ^
    --min-rounds 864 --min-distinct-inputs 100000 ^
    --out <evidence out dir> ^
    --label <label> [--checkpoint-dir <checkpoint dir>] ^
    [--frozen-tree <repo root> (default: this file's repo root)] ^
    [--identity-json <binding json>]

Distributable-copy deltas vs the PAL_RV05_CAPACITY_20260921 N5 working
copy (PAL_RV05_CLOSURE_20260922, node N2; see tools/soakctl/README.md):

1. ``--frozen-tree`` default resolves to THIS repository root (the tree
   containing tests/support/{soak_driver,soak_audit,soak_closeout}.py next
   to tools/soakctl/), not a private <V3 root>/src layout.
2. The private-layout ``<V3 root>/runtime/python.exe`` auto-candidate for
   ``--python-file`` is REMOVED: the interpreter file is only verified
   when --python-file is passed explicitly. Without it the python_sha256
   identity dimension stays UNKNOWN (capping the verdict at UNKNOWN) -
   rejection-safe, never an optimistic PASS.
3. The docstring usage block carries placeholders instead of the original
   host paths/PID. No behavior change.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path

# Distributable default (N2): this file lives at <repo>/tools/soakctl/, so
# the frozen tree it audits with is the repository root itself (two levels
# up). --frozen-tree still overrides it explicitly.
DEFAULT_FROZEN_TREE = Path(__file__).resolve().parents[2]

# N5 parameterization (PAL_RV05_CAPACITY_20260921): the strict caliber's
# expected identity is INJECTABLE so this reviewer can be bound to a new
# candidate (new commit/tree/module hashes, config+manifest digests, schema
# expectations) via --identity-json / --expected-* flags. Defaults remain the
# frozen V3 FREEZE_RECORD values below, so a no-flag invocation behaves
# exactly like the frozen reviewer (legacy regression preserved).
LEGACY_BINDING = {
    'commit': '2584f6f2d86ce19e7e2dab6bea6a27a013587753',
    'tree': '702d25b5aaabbd790bd48a0ebed8d1a79f12c24d',
    'modules': {
        'soak_driver.py': '18f53a241901622a68c6c8643333557432e74e1425b4a5ca64104cf2f423e675',
        'soak_audit.py': '13e03359b2b472e43a408e0b690f43389937c04f6c0d9d3b00c9f5cd7208db3d',
        'soak_closeout.py': '0bbcfd93a3b7d502b28c934c1b4d63183513b5a79d3f7003d444b1d491f80114',
    },
    # optional pins; None = not bound (legacy behavior for that dimension):
    'python_sha256': None,
    'config_sha256': None,
    'manifest_sha256': None,
    'campaign_schema': None,
    'receipt_schema': None,
}
FROZEN_EXPECTED_COMMIT = LEGACY_BINDING['commit']    # legacy alias
FROZEN_EXPECTED_TREE = LEGACY_BINDING['tree']        # legacy alias
FROZEN_EXPECTED_MODULES = LEGACY_BINDING['modules']  # legacy alias
REQUIRED_BINDING_MODULES = ('soak_driver.py', 'soak_audit.py',
                            'soak_closeout.py')
_HEX64 = re.compile('[0-9a-f]{64}')
_HEX40 = re.compile('[0-9a-f]{40}')

TOOL = 'run_final_review'
PASS, FAIL, UNKNOWN, NOT_RUN = 'PASS', 'FAIL', 'UNKNOWN', 'NOT_RUN'
SNAPSHOT_INCOMPLETE = 'SNAPSHOT_INCOMPLETE'
CONSTANTS = {'official_cases_run': 0, 'sandbox_started': False,
             'activation_authorized': False}
LOCK_READ_CAP = 8192
LOG_TAIL_CAP = 262144
LOG_TAIL_EVENTS = 12

EXIT_PASS, EXIT_INTERNAL, EXIT_FAIL, EXIT_NOT_RUN, EXIT_CONFIG = 0, 1, 3, 2, 5


def utc_iso(wall: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(wall))


def sha256_file(path: Path) -> str | None:
    digest = sha256()
    try:
        with open(path, 'rb') as handle:
            for block in iter(lambda: handle.read(1 << 16), b''):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def load_frozen(frozen_tree: Path):
    """Import the frozen tree's support modules (read-only) by path."""
    root = Path(frozen_tree).resolve()
    if not (root / 'tests' / 'support' / 'soak_audit.py').is_file():
        raise SystemExit(f'{TOOL}: frozen tree not found at {root}')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tests.support import soak_audit as aud  # noqa: E402
    from tests.support import soak_closeout as clo  # noqa: E402
    from tests.support import soak_driver as drv  # noqa: E402
    return aud, clo, drv


def build_binding(opts) -> tuple[dict, set, list[str]]:
    """Compose the effective expected identity.

    Precedence: individual CLI flags > --identity-json > legacy frozen
    defaults. Returns (binding, explicit_keys, errors). ``explicit_keys``
    names the dimensions that were explicitly bound (identity-json or flag);
    only those activate the extra campaign-schema / receipt-schema caps and
    the audit ``expected`` identity pinning, so a legacy no-flag invocation
    keeps byte-identical behavior. An --identity-json that does not carry the
    COMPLETE core identity (commit + tree + all three module hashes) is an
    invalid invocation (rejection, exit 5), never a silent legacy fallback.
    """
    binding = {key: (dict(value) if isinstance(value, dict) else value)
               for key, value in LEGACY_BINDING.items()}
    explicit: set = set()
    errors: list[str] = []
    if getattr(opts, 'identity_json', None):
        try:
            doc = json.loads(
                Path(opts.identity_json).read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            return binding, explicit, [
                f'--identity-json unreadable: {error.__class__.__name__}']
        if not isinstance(doc, dict):
            return binding, explicit, ['--identity-json must be a JSON object']
        modules = doc.get('modules')
        missing_modules = [name for name in REQUIRED_BINDING_MODULES
                           if not isinstance(modules, dict)
                           or not isinstance(modules.get(name), str)
                           or _HEX64.fullmatch(modules[name]) is None]
        if not isinstance(doc.get('commit'), str) \
                or _HEX40.fullmatch(doc['commit']) is None:
            errors.append('--identity-json: commit (40-hex) is required')
        if not isinstance(doc.get('tree'), str) \
                or _HEX40.fullmatch(doc['tree']) is None:
            errors.append('--identity-json: tree (40-hex) is required')
        if missing_modules:
            errors.append('--identity-json: modules must pin all of '
                          + ', '.join(REQUIRED_BINDING_MODULES)
                          + f' (invalid/missing: {missing_modules})')
        if errors:
            return binding, explicit, errors
        binding['commit'] = doc['commit']
        binding['tree'] = doc['tree']
        binding['modules'] = {name: modules[name]
                              for name in REQUIRED_BINDING_MODULES}
        explicit |= {'commit', 'tree', 'modules'}
        for key in ('python_sha256', 'config_sha256', 'manifest_sha256'):
            value = doc.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
                errors.append(f'--identity-json: {key} must be 64-hex')
            else:
                binding[key] = value
                explicit.add(key)
        for key in ('campaign_schema', 'receipt_schema'):
            value = doc.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                errors.append(f'--identity-json: {key} must be a non-empty '
                              'string')
            else:
                binding[key] = value
                explicit.add(key)
        if errors:
            return binding, explicit, errors
    # individual flags override identity-json values
    for attr, key, pattern in (
            ('expected_commit', 'commit', _HEX40),
            ('expected_tree', 'tree', _HEX40),
            ('expected_python_sha256', 'python_sha256', _HEX64),
            ('expected_config_sha256', 'config_sha256', _HEX64),
            ('expected_manifest_sha256', 'manifest_sha256', _HEX64)):
        value = getattr(opts, attr, None)
        if value:
            if pattern.fullmatch(value) is None:
                errors.append(f'--{attr.replace("_", "-")}: expected '
                              f'{pattern.pattern}-shaped value')
            else:
                binding[key] = value
                explicit.add(key)
    for value in getattr(opts, 'expected_module_sha256', None) or ():
        if '=' not in value:
            errors.append(f'--expected-module-sha256: NAME=HEX required, '
                          f'got {value!r}')
            continue
        name, _, hexpart = value.partition('=')
        if name not in REQUIRED_BINDING_MODULES \
                or _HEX64.fullmatch(hexpart) is None:
            errors.append(f'--expected-module-sha256: name must be one of '
                          f'{REQUIRED_BINDING_MODULES} and hash 64-hex, got '
                          f'{value!r}')
            continue
        binding['modules'][name] = hexpart
        explicit.add('modules')
    for attr, key in (('expect_campaign_schema', 'campaign_schema'),
                      ('expect_receipt_schema', 'receipt_schema')):
        value = getattr(opts, attr, None)
        if value:
            binding[key] = value
            explicit.add(key)
    return binding, explicit, errors


def toolchain_identity(frozen_tree: Path, aud, drv,
                       binding: dict | None = None) -> dict:
    """Hash the imported frozen modules; record drift vs the bound identity."""
    binding = binding or {'commit': FROZEN_EXPECTED_COMMIT,
                          'tree': FROZEN_EXPECTED_TREE,
                          'modules': dict(FROZEN_EXPECTED_MODULES)}
    expected_modules = binding['modules']
    support = Path(drv.__file__).resolve().parent
    modules = {}
    for name in REQUIRED_BINDING_MODULES:
        expected = expected_modules.get(name)
        path = support / name
        found = sha256_file(path)
        modules[name] = {'path': str(path), 'sha256': found,
                         'expected_frozen_sha256': expected,
                         'match': found is not None and found == expected}
    head = tree = None
    try:
        head = subprocess.run(
            ['git', '-C', str(frozen_tree), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=30).stdout.strip() or None
        if head:
            tree = subprocess.run(
                ['git', '-C', str(frozen_tree), 'rev-parse', head + '^{tree}'],
                capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        head = tree = None
    drift = []
    if head is not None and head != binding['commit']:
        drift.append({'kind': 'commit', 'found': head,
                      'expected': binding['commit']})
    if head is None:
        drift.append({'kind': 'commit_unverifiable'})
    if head is not None and tree is not None and binding.get('tree') \
            and tree != binding['tree']:
        drift.append({'kind': 'tree', 'found': tree,
                      'expected': binding['tree']})
    for name, entry in modules.items():
        if not entry['match']:
            drift.append({'kind': 'module', 'module': name,
                          'found': entry['sha256'],
                          'expected': entry['expected_frozen_sha256']})
    status = PASS if not drift else UNKNOWN
    return {'frozen_tree': str(Path(frozen_tree).resolve()),
            'expected_commit': binding['commit'],
            'expected_tree': binding['tree'],
            'git_head': head, 'git_tree': tree, 'modules': modules,
            'drift': drift, 'status': status,
            'note': 'strict caliber is bound to the expected identity; drift '
                    'caps the verdict at UNKNOWN (never PASS)'}


def lock_pid(campaign: Path, drv) -> int | None:
    raw = drv._read_capped(campaign / 'driver.lock', LOCK_READ_CAP)
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode('utf-8', 'replace'))
    except ValueError:
        return None
    pid = data.get('pid') if isinstance(data, dict) else None
    return pid if isinstance(pid, int) else None


def progress_snapshot(campaign: Path, clo, drv) -> dict:
    """Bounded read-only progress data for a refusal record."""
    snap = clo.campaign_snapshot(campaign)
    progress = {'snapshot': snap}
    latest = clo.latest_segment_name(snap)
    if latest is not None:
        seg_dir = campaign / 'segments' / latest
        rounds = seg_dir / 'rounds'
        names = []
        try:
            for entry in rounds.iterdir():
                if entry.name != 'unknown':
                    names.append(entry.name)
        except OSError:
            names = []
        names.sort()
        progress['latest_segment'] = latest
        progress['finalized_round_dirs_in_latest_segment'] = len(names)
        progress['latest_round_dir'] = names[-1] if names else None
        header = drv._read_json(seg_dir / 'segment.json') or {}
        started = header.get('started_wall')
        if isinstance(started, (int, float)):
            progress['segment_started_wall_utc'] = utc_iso(started)
            progress['observed_so_far_seconds'] = round(time.time() - started, 1)
    tail_events = []
    for name in ('driver.log.1', 'driver.log'):
        raw = drv._read_capped(campaign / name, LOG_TAIL_CAP)
        if raw is None:
            continue
        lines = [ln for ln in raw.decode('utf-8', 'replace').splitlines()
                 if ln.strip()]
        for line in lines[-LOG_TAIL_EVENTS:]:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                tail_events.append({k: event.get(k) for k in
                                    ('event', 'round', 'final', 'reason',
                                     'wall') if k in event})
    progress['driver_log_tail_events'] = tail_events[-LOG_TAIL_EVENTS:]
    return progress


def resolve_driver_file(campaign: Path, explicit: str | None, clo, drv) \
        -> tuple[Path | None, str | None]:
    """--driver-file wins; else latest segment.json argv[0]; else lock argv."""
    if explicit:
        return Path(explicit), 'explicit'
    snap = clo.campaign_snapshot(campaign)
    latest = clo.latest_segment_name(snap)
    if latest is not None:
        header = drv._read_json(campaign / 'segments' / latest
                                / 'segment.json') or {}
        argv = header.get('argv')
        if isinstance(argv, list) and argv and isinstance(argv[0], str):
            return Path(argv[0]), 'latest_segment_argv0'
    raw = drv._read_capped(campaign / 'driver.lock', LOCK_READ_CAP)
    if raw is not None:
        try:
            data = json.loads(raw.decode('utf-8', 'replace'))
            argv = data.get('argv') if isinstance(data, dict) else None
            if isinstance(argv, list) and argv and isinstance(argv[0], str):
                return Path(argv[0]), 'lock_argv0'
        except ValueError:
            pass
    return None, None


def campaign_identity(campaign: Path, identity: dict, config_path: Path,
                      manifest_path: Path | None, driver_file: Path | None,
                      driver_source: str | None, python_file: Path | None,
                      python_source: str | None, audit_checks: list, drv) \
        -> dict:
    """Campaign identity verification; any mismatch is FAIL."""
    section: dict = {}
    # config: mirrored from the audit's config_sha_match (canonical recompute)
    config_match = None
    for entry in audit_checks:
        if entry.get('check') == 'config_sha_match':
            config_match = entry.get('status')
    if config_match is None:
        recorded = identity.get('config_sha256')
        recomputed = None
        try:
            cfg_doc = json.loads(config_path.read_text(encoding='utf-8'))
            recomputed = drv._sha256_bytes(
                drv.SoakConfig.from_dict(cfg_doc).canonical_bytes())
        except (OSError, ValueError, drv.SoakConfigError):
            recomputed = None
        config_match = PASS if recomputed is not None \
            and recomputed == recorded else FAIL
        section['config_sha256'] = {'status': config_match,
                                    'recorded': recorded,
                                    'recomputed': recomputed,
                                    'source': 'local recompute'}
    else:
        section['config_sha256'] = {'status': config_match,
                                    'source': 'audit config_sha_match'}
    # manifest: canonical identity recompute from the manifest file
    recorded_manifest = identity.get('manifest_sha256')
    if manifest_path is not None:
        try:
            _loaded, recomputed_manifest = drv._load_manifest(Path(manifest_path))
            section['manifest_sha256'] = {
                'status': PASS if recomputed_manifest == recorded_manifest
                else FAIL,
                'recorded': recorded_manifest,
                'recomputed': recomputed_manifest,
                'manifest_path': str(manifest_path)}
        except (OSError, drv.SoakConfigError) as error:
            section['manifest_sha256'] = {
                'status': UNKNOWN, 'recorded': recorded_manifest,
                'manifest_path': str(manifest_path),
                'error': f'{error.__class__.__name__}'}
    else:
        section['manifest_sha256'] = {
            'status': UNKNOWN, 'recorded': recorded_manifest,
            'note': 'no manifest provided'}
    # driver file rehash
    recorded_driver = identity.get('driver_sha256')
    if driver_file is not None:
        found = sha256_file(driver_file)
        section['driver_sha256'] = {
            'status': PASS if found is not None and found == recorded_driver
            else FAIL,
            'recorded': recorded_driver, 'recomputed': found,
            'driver_file': str(driver_file), 'resolution': driver_source}
    else:
        section['driver_sha256'] = {
            'status': UNKNOWN, 'recorded': recorded_driver,
            'note': 'driver file unresolvable (no --driver-file, no segment '
                    'argv, no lock argv)'}
    # python interpreter rehash
    recorded_python = identity.get('python_sha256')
    if python_file is not None:
        found = sha256_file(python_file)
        section['python_sha256'] = {
            'status': PASS if found is not None and found == recorded_python
            else FAIL,
            'recorded': recorded_python, 'recomputed': found,
            'python_file': str(python_file), 'resolution': python_source}
    else:
        section['python_sha256'] = {
            'status': UNKNOWN, 'recorded': recorded_python,
            'note': 'no --python-file provided'}
    statuses = [v.get('status') for v in section.values()]
    section['all_pass'] = all(s == PASS for s in statuses)
    section['any_fail'] = any(s == FAIL for s in statuses)
    section['any_unknown'] = any(s == UNKNOWN for s in statuses)
    return section


def strict_gates(*, report: dict | None, identity: dict | None, clo, drv,
                 campaign: Path, snapshot: dict, gates_used: dict,
                 target_end_wall: float | None, now_wall: float) -> dict:
    """Campaign-level strict end gates. NOT_RUN when no audit ran."""
    gates: dict = {}
    if report is None:
        for name in ('planned_end_complete_receipt', 'observation_wall_target',
                     'min_rounds_passed_only', 'min_distinct_inputs_verified'):
            gates[name] = {'status': NOT_RUN,
                           'note': 'final review refused; no audit executed'}
        return gates

    latest = clo.latest_segment_name(snapshot)
    receipt = clo.complete_receipt(snapshot)
    gates['planned_end_complete_receipt'] = {
        'status': PASS if receipt else FAIL,
        'detail': {'complete_receipt_on_latest_segment': receipt,
                   'latest_segment': latest,
                   'close_reasons': snapshot.get('close_reasons'),
                   'note': 'R4 binding: a complete receipt counts only on '
                           'the latest closed segment (segment-\\d{6})'}}
    span = None
    if latest is not None:
        seg_dir = campaign / 'segments' / latest
        header = drv._read_json(seg_dir / 'segment.json') or {}
        close = drv._read_json(seg_dir / 'segment-close.json') or {}
        started, stopped = header.get('started_wall'), close.get('stopped_wall')
        if isinstance(started, (int, float)) and isinstance(stopped, (int, float)):
            span = round(stopped - started, 3)
    max_wall = gates_used.get('max_wall_seconds')
    if isinstance(max_wall, (int, float)) and span is not None:
        detail = {'span_seconds': span, 'max_wall_seconds': max_wall,
                  'segment': latest}
        if span < max_wall:
            detail['deficit_seconds'] = round(max_wall - span, 3)
        gates['observation_wall_target'] = {
            'status': PASS if span >= max_wall else FAIL, 'detail': detail,
            'note': 'R3 strict floor: no tolerance is deducted from '
                    'max_wall_seconds'}
    else:
        gates['observation_wall_target'] = {
            'status': UNKNOWN,
            'detail': {'span_seconds': span, 'max_wall_seconds': max_wall},
            'note': 'span or wall target unavailable'}
    rounds_total = (report.get('rounds_total') or {})
    passed = rounds_total.get('passed')
    required_rounds = gates_used.get('min_rounds')
    if isinstance(passed, int) and isinstance(required_rounds, int):
        gates['min_rounds_passed_only'] = {
            'status': PASS if passed >= required_rounds else FAIL,
            'detail': {'achieved_passed': passed, 'required': required_rounds,
                       'rounds_total': dict(rounds_total)},
            'note': 'R2: only actually passed rounds count; failed/'
                    'interrupted/unknown never satisfy the minimum'}
    else:
        gates['min_rounds_passed_only'] = {
            'status': UNKNOWN,
            'detail': {'achieved_passed': passed, 'required': required_rounds}}
    inputs = (report.get('inputs') or {})
    verified = inputs.get('verified_distinct_inputs')
    required_inputs = gates_used.get('min_distinct_inputs')
    if isinstance(verified, int) and isinstance(required_inputs, int):
        gates['min_distinct_inputs_verified'] = {
            'status': PASS if verified >= required_inputs else FAIL,
            'detail': {'verified_distinct_inputs': verified,
                       'required': required_inputs,
                       'receipt_declared_subinputs_sum':
                           inputs.get('receipt_declared_subinputs_sum'),
                       'functional_seed_distinct':
                           inputs.get('functional_seed_distinct'),
                       'payload_level_distinct':
                           inputs.get('payload_level_distinct')},
            'note': 'R1: a declared sub-input sum is not execution proof; '
                    'only audit-verified distinct logical inputs count'}
    else:
        gates['min_distinct_inputs_verified'] = {
            'status': UNKNOWN,
            'detail': {'verified_distinct_inputs': verified,
                       'required': required_inputs}}
    if target_end_wall is not None:
        gates['target_end_context'] = {
            'status': UNKNOWN,
            'detail': {'target_end_wall_utc': utc_iso(target_end_wall),
                       'review_wall_utc': utc_iso(now_wall),
                       'note': 'context only; the strict gates above decide'}}
    return gates


def compose_verdict(*, refused: bool, audit_overall: str | None,
                    identity: dict | None, gates: dict,
                    toolchain: dict, schema_caps: list | None = None) \
        -> tuple[str, list[str]]:
    reasons: list[str] = []
    if refused:
        return NOT_RUN, ['campaign still running (or missing): review refused']
    # a half-written terminal state is not adjudicable at all: neither FAIL
    # (individual gates may look failed merely because files are torn) nor
    # PASS — SNAPSHOT_INCOMPLETE dominates every other composition
    if audit_overall == SNAPSHOT_INCOMPLETE:
        return UNKNOWN, ['audit snapshot incomplete: settled files still '
                         'look live (half-written terminal state); not '
                         'adjudicable as FAIL or PASS']
    if audit_overall == FAIL:
        reasons.append('audit overall FAIL')
    if identity is not None and identity.get('any_fail'):
        reasons.append('campaign identity hash mismatch')
    gate_fails = [name for name, g in gates.items()
                  if isinstance(g, dict) and g.get('status') == FAIL]
    if gate_fails:
        reasons.append(f'strict gate FAIL: {", ".join(gate_fails)}')
    if reasons:
        if schema_caps:
            reasons.append('campaign schema/receipt caliber unbound '
                           '(recorded alongside the concrete failures above)')
        return FAIL, reasons
    # N5 schema-mixing rejection: a campaign carrying a receipt/schemas the
    # reviewer is not bound to can never be adjudicated PASS. With no
    # concrete failure proven, the honest verdict is UNKNOWN (the evidence
    # caliber is simply not provable by this binding), never PASS.
    if schema_caps:
        return UNKNOWN, [
            f'schema binding mismatch [{cap["kind"]}]: found={cap["found"]!r} '
            f'expected={cap["expected"]!r} - {cap["semantics"]}'
            for cap in schema_caps] + [
            'schema-mixing rejection: this reviewer build is not bound to '
            "the campaign's evidence schema; verdict stays UNKNOWN, never "
            'PASS']
    if audit_overall != PASS:
        return UNKNOWN, [f'audit overall {audit_overall}']
    if identity is not None and identity.get('any_unknown'):
        reasons.append('campaign identity incompletely verified (UNKNOWN)')
    if toolchain.get('status') != PASS:
        reasons.append('frozen-tree toolchain drift/unverifiable')
    gate_unknown = [name for name, g in gates.items()
                    if isinstance(g, dict) and g.get('status') == UNKNOWN
                    and name != 'target_end_context']
    if gate_unknown:
        reasons.append(f'strict gate UNKNOWN: {", ".join(gate_unknown)}')
    if reasons:
        return UNKNOWN, reasons
    return PASS, ['strict-caliber final review passed every gate']


def do_review(opts, aud, clo, drv) -> int:
    campaign = Path(opts.campaign)
    out_dir = Path(opts.out)
    ckpt_dir = Path(opts.checkpoint_dir) if opts.checkpoint_dir else None

    # N5 binding composition first: an invalid/incomplete new-candidate
    # binding is a rejection (exit 5), never a silent legacy fallback.
    binding, explicit, binding_errors = build_binding(opts)
    if binding_errors:
        for message in binding_errors:
            print(f'{TOOL}: {message}', file=sys.stderr)
        return EXIT_CONFIG

    # R5-style write-target guard: never write inside the campaign
    if campaign.is_dir():
        for label, target in (('out', out_dir), ('checkpoint', ckpt_dir)):
            if target is not None:
                reason = clo.write_target_rejected(campaign, target)
                if reason:
                    print(f'{TOOL}: refusing {label} target: {reason}',
                          file=sys.stderr)
                    return EXIT_CONFIG

    base = {
        'tool': TOOL, 'label': opts.label,
        'generated_wall_utc': utc_iso(time.time()),
        'campaign': str(campaign), 'out_dir': str(out_dir),
        'constants': dict(CONSTANTS),
        'frozen_expected_commit': binding['commit'],
        'expected_binding': {
            'commit': binding['commit'], 'tree': binding['tree'],
            'modules': dict(binding['modules']),
            'python_sha256': binding.get('python_sha256'),
            'config_sha256': binding.get('config_sha256'),
            'manifest_sha256': binding.get('manifest_sha256'),
            'campaign_schema': binding.get('campaign_schema'),
            'receipt_schema': binding.get('receipt_schema'),
            'explicit_dimensions': sorted(explicit),
            'source': ('identity-json/flags' if explicit
                       else 'legacy-frozen-defaults'),
        },
        'note': 'strict-caliber final review (corrected gates, frozen tree); '
                'distinct from the old closeout FINAL_REVIEW_READY loose '
                'material; grants no execution authorization',
    }

    def write_report(report: dict, marker: bool) -> int:
        refuse_campaign_target(clo, campaign, out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(out_dir / 'final-review.json',
                     json.dumps(report, indent=2, sort_keys=True,
                                default=str) + '\n')
        if marker:
            marker_payload = {
                'tool': TOOL, 'label': opts.label,
                'verdict': report.get('verdict'),
                'final_review_path': str(out_dir / 'final-review.json'),
                'final_review_sha256': sha256_file(out_dir / 'final-review.json'),
                'audit_overall': report.get('audit_overall'),
                'breakdown': report.get('breakdown'),
                'written_wall_utc': utc_iso(time.time()),
                'review_pid': os.getpid(),
                'frozen_expected_commit': binding['commit'],
                'distinct_from': 'FINAL_REVIEW_READY (old closeout, loose '
                                 'gates; pending-review material only)',
                'note': 'corrected-caliber strict final review executed and '
                        'adjudicated; this marker grants no execution '
                        'authorization',
            }
            atomic_write(out_dir / 'FINAL_REVIEW_STRICT',
                         json.dumps(marker_payload, indent=2,
                                    sort_keys=True) + '\n')
        if ckpt_dir is not None and campaign.is_dir() \
                and not clo.write_target_rejected(campaign, ckpt_dir):
            try:
                ckpt_dir.mkdir(parents=True, exist_ok=True)
                atomic_write(ckpt_dir / 'final-review-checkpoint.json',
                             json.dumps({'tool': TOOL, 'label': opts.label,
                                         'wall_utc': utc_iso(time.time()),
                                         'verdict': report.get('verdict')},
                                        indent=2, sort_keys=True) + '\n')
            except OSError:
                pass
        verdict = report.get('verdict')
        return {PASS: EXIT_PASS, FAIL: EXIT_FAIL, UNKNOWN: 4,
                NOT_RUN: EXIT_NOT_RUN}.get(verdict, EXIT_INTERNAL)

    # ---------- missing campaign ----------
    if not campaign.is_dir():
        report = dict(base)
        report.update({'verdict': NOT_RUN,
                       'refuse_reason': {'kind': 'campaign_missing',
                                         'campaign': str(campaign)},
                       'audit_executed': False,
                       'strict_gates': strict_gates(
                           report=None, identity=None, clo=clo, drv=drv,
                           campaign=campaign, snapshot={'segments': []},
                           gates_used={}, target_end_wall=None,
                           now_wall=time.time())})
        return write_report(report, marker=False)

    # ---------- liveness refusal ----------
    lock = lock_pid(campaign, drv)
    expected_pid = opts.driver_pid
    lock_alive = drv._pid_alive(lock) if lock is not None else None
    expected_alive = drv._pid_alive(expected_pid) \
        if isinstance(expected_pid, int) else None
    still_running = lock_alive is True or expected_alive is True
    if still_running:
        report = dict(base)
        report.update({
            'verdict': NOT_RUN,
            'refuse_reason': {
                'kind': 'still_running',
                'lock_pid': lock, 'lock_pid_alive': lock_alive,
                'driver_pid_arg': expected_pid,
                'driver_pid_alive': expected_alive,
                'checked_wall_utc': utc_iso(time.time()),
                'explanation': 'the campaign driver is still alive; a final '
                               'review of the terminal state is refused until '
                               'the campaign has definitively ended'},
            'audit_executed': False,
            'progress_readonly': progress_snapshot(campaign, clo, drv),
            'strict_gates': strict_gates(
                report=None, identity=None, clo=clo, drv=drv,
                campaign=campaign,
                snapshot=clo.campaign_snapshot(campaign), gates_used={},
                target_end_wall=None, now_wall=time.time()),
        })
        return write_report(report, marker=False)

    # ---------- inputs ----------
    try:
        config_doc = json.loads(Path(opts.config).read_text(encoding='utf-8'))
        cfg = drv.SoakConfig.from_dict(config_doc)
    except (OSError, ValueError, drv.SoakConfigError) as error:
        print(f'{TOOL}: config invalid: {error.__class__.__name__}',
              file=sys.stderr)
        return EXIT_CONFIG

    gates_used = {'min_rounds': cfg.min_rounds,
                  'max_wall_seconds': cfg.max_wall_seconds,
                  'min_distinct_inputs': opts.min_distinct_inputs,
                  'source': 'config file + --min-distinct-inputs'}
    policy_notes = []
    if opts.min_rounds is not None:
        if opts.min_rounds < cfg.min_rounds and not opts.allow_loose:
            print(f'{TOOL}: --min-rounds {opts.min_rounds} loosens the '
                  f'config minimum {cfg.min_rounds}; refusing without '
                  f'--allow-loose', file=sys.stderr)
            return EXIT_CONFIG
        gates_used['min_rounds'] = max(opts.min_rounds, cfg.min_rounds) \
            if not opts.allow_loose else opts.min_rounds
        policy_notes.append(f'min_rounds override -> {gates_used["min_rounds"]}')
    if opts.max_wall_seconds is not None:
        if opts.max_wall_seconds > (cfg.max_wall_seconds or 0) \
                and not opts.allow_loose:
            print(f'{TOOL}: --max-wall-seconds loosens the config wall floor; '
                  f'refusing without --allow-loose', file=sys.stderr)
            return EXIT_CONFIG
        gates_used['max_wall_seconds'] = opts.max_wall_seconds
        policy_notes.append(
            f'max_wall_seconds override -> {gates_used["max_wall_seconds"]}')
    if opts.min_distinct_inputs is None or opts.min_distinct_inputs < 1:
        print(f'{TOOL}: --min-distinct-inputs (>=1) is required for a strict '
              f'final review', file=sys.stderr)
        return EXIT_CONFIG

    manifest_path = Path(opts.manifest) if opts.manifest \
        else Path(cfg.scenario_manifest)
    scenarios = scenario_objects = None
    manifest_sha_recomputed = None
    try:
        scenario_objects, manifest_sha_recomputed = \
            drv._load_manifest(manifest_path)
        scenarios = [s.name for s in scenario_objects]
    except (OSError, drv.SoakConfigError):
        scenario_objects = scenarios = None

    baseline = None
    if opts.baseline:
        try:
            baseline = json.loads(Path(opts.baseline).read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            print(f'{TOOL}: baseline unreadable: {error.__class__.__name__}',
                  file=sys.stderr)
            return EXIT_CONFIG

    # ---------- full strict audit ----------
    identity_doc = drv._read_json(campaign / 'campaign.json') or {}
    # N5: explicitly-bound identity dimensions are also pinned inside the
    # audit itself (identity_expected check) - any drift between the bound
    # hashes and the campaign.json recorded hashes is a FAIL there.
    audit_expected: dict = {}
    if 'modules' in explicit:
        audit_expected['driver_sha256'] = binding['modules']['soak_driver.py']
    if binding.get('python_sha256') and 'python_sha256' in explicit:
        audit_expected['python_sha256'] = binding['python_sha256']
    if binding.get('config_sha256') and 'config_sha256' in explicit:
        audit_expected['config_sha256'] = binding['config_sha256']
    if binding.get('manifest_sha256') and 'manifest_sha256' in explicit:
        audit_expected['manifest_sha256'] = binding['manifest_sha256']
    review_error = None
    try:
        report = aud.audit_campaign(
            campaign.resolve(), config=config_doc, scenarios=scenarios,
            scenario_objects=scenario_objects, baseline=baseline,
            expected=audit_expected or None,
            min_distinct_inputs=opts.min_distinct_inputs)
    except Exception as error:  # noqa: BLE001 - honest record, fail closed
        review_error = f'{error.__class__.__name__}: {error}'
        report = None

    snapshot = clo.campaign_snapshot(campaign)
    driver_file, driver_source = resolve_driver_file(
        campaign, opts.driver_file, clo, drv)
    python_file = Path(opts.python_file) if opts.python_file else None
    python_source = 'explicit' if opts.python_file else None
    # Distributable-copy delta (N2): no private-layout runtime/python.exe
    # auto-candidate. The interpreter is verified only when --python-file is
    # passed; otherwise python_sha256 stays UNKNOWN (never optimistic PASS).
    identity_section = campaign_identity(
        campaign, identity_doc, Path(opts.config),
        manifest_path if manifest_path.is_file() else None,
        driver_file, driver_source, python_file, python_source,
        (report or {}).get('checks', []), drv) if report is not None else None
    if identity_section is not None \
            and manifest_sha_recomputed is not None \
            and identity_section['manifest_sha256'].get('status') == UNKNOWN:
        identity_section['manifest_sha256'].update(
            {'status': PASS if manifest_sha_recomputed
             == identity_doc.get('manifest_sha256') else FAIL,
             'recomputed': manifest_sha_recomputed,
             'manifest_path': str(manifest_path)})
    # N5: an explicitly bound driver hash is also verified against the driver
    # FILE that actually ran (resolved above), independent of campaign.json.
    if identity_section is not None and 'modules' in explicit:
        found_driver_hash = sha256_file(driver_file) if driver_file else None
        expected_driver_hash = binding['modules']['soak_driver.py']
        identity_section['driver_sha256_binding'] = {
            'status': (PASS if found_driver_hash == expected_driver_hash
                       else UNKNOWN if found_driver_hash is None else FAIL),
            'recorded_expectation': expected_driver_hash,
            'driver_file': str(driver_file) if driver_file else None,
            'recomputed': found_driver_hash,
            'note': 'explicit binding pins the driver file hash'}
        identity_section['all_pass'] = all(
            v.get('status') == PASS for v in identity_section.values()
            if isinstance(v, dict))
        identity_section['any_fail'] = any(
            v.get('status') == FAIL for v in identity_section.values()
            if isinstance(v, dict))
        identity_section['any_unknown'] = any(
            v.get('status') == UNKNOWN for v in identity_section.values()
            if isinstance(v, dict))

    # N5 schema-mixing caps: computed from the campaign's own declarations
    # vs the (explicitly or legacy) bound schema expectations.
    schema_caps: list = []
    campaign_schema_declared = identity_doc.get('schema')
    receipt_schema_declared = identity_doc.get('receipt_schema')
    if 'campaign_schema' in explicit \
            and campaign_schema_declared != binding['campaign_schema']:
        schema_caps.append({
            'kind': 'campaign_schema', 'found': campaign_schema_declared,
            'expected': binding['campaign_schema'],
            'semantics': 'binding expects this campaign.json schema; the '
                         'campaign does not carry it'})
    if 'receipt_schema' in explicit:
        if receipt_schema_declared != binding['receipt_schema']:
            schema_caps.append({
                'kind': 'receipt_schema', 'found': receipt_schema_declared,
                'expected': binding['receipt_schema'],
                'semantics': 'binding expects this receipt schema; the '
                             'campaign does not carry it'})
    elif receipt_schema_declared is not None:
        schema_caps.append({
            'kind': 'receipt_schema', 'found': receipt_schema_declared,
            'expected': None,
            'semantics': 'legacy binding: the campaign carries a receipt '
                         'schema this reviewer was not bound to; its '
                         'new-schema receipt evidence cannot be adjudicated '
                         'by this caliber'})

    toolchain = toolchain_identity(Path(opts.frozen_tree), aud, drv, binding)
    target_end_wall = clo.parse_utc(opts.target_end) if opts.target_end else None
    now_wall = time.time()
    gates = strict_gates(report=report, identity=identity_section, clo=clo,
                         drv=drv, campaign=campaign, snapshot=snapshot,
                         gates_used=gates_used, target_end_wall=target_end_wall,
                         now_wall=now_wall)

    if report is None:
        verdict, reasons = UNKNOWN, [f'audit execution error: {review_error}']
    else:
        verdict, reasons = compose_verdict(
            refused=False, audit_overall=report.get('overall'),
            identity=identity_section, gates=gates, toolchain=toolchain,
            schema_caps=schema_caps)

    breakdown: dict = {}
    if report is not None:
        counts = {'PASS': 0, 'FAIL': 0, 'UNKNOWN': 0}
        for entry in report.get('checks', []):
            if entry.get('status') in counts:
                counts[entry['status']] += 1
        breakdown['audit_checks'] = counts
    breakdown['strict_gates'] = {name: g.get('status')
                                 for name, g in gates.items()
                                 if isinstance(g, dict)}
    if identity_section is not None:
        breakdown['identity'] = {name: v.get('status')
                                 for name, v in identity_section.items()
                                 if isinstance(v, dict)}
    breakdown['toolchain'] = toolchain.get('status')

    # end classification (post-mortem, read-only)
    latest = clo.latest_segment_name(snapshot)
    stop_present = False
    stop_path = identity_doc.get('stop_path')
    if isinstance(stop_path, str):
        stop_present = Path(stop_path).is_file()
    close_reason = snapshot.get('close_reasons', {}).get(latest) \
        if latest else None
    end_kind = ('complete_receipt' if close_reason == 'complete'
                else 'stop_file_close' if close_reason == 'stop_file'
                else 'closed_no_receipt' if close_reason is not None
                else 'segment_unclosed')
    if report is not None and report.get('live'):
        end_kind = f'{end_kind}+snapshot_incomplete'

    final = dict(base)
    final.update({
        'verdict': verdict, 'verdict_reasons': reasons,
        'audit_executed': report is not None,
        'review_error': review_error,
        'audit': report, 'audit_overall': (report or {}).get('overall'),
        'audit_live': (report or {}).get('live'),
        'identity': identity_section,
        'toolchain': toolchain,
        'gates_used': gates_used, 'gate_policy_notes': policy_notes,
        'strict_gates': gates, 'breakdown': breakdown,
        'schema_binding': {
            'campaign_schema_declared': campaign_schema_declared,
            'receipt_schema_declared': receipt_schema_declared,
            'bound_campaign_schema': binding.get('campaign_schema'),
            'bound_receipt_schema': binding.get('receipt_schema'),
            'caps': schema_caps,
            'note': 'schema-mixing rejection: a campaign carrying schemas '
                    'this caliber is not bound to can never be adjudicated '
                    'PASS (UNKNOWN unless a concrete FAIL is proven)',
        },
        'end_classification': {
            'end_kind': end_kind, 'latest_segment': latest,
            'latest_close_reason': close_reason,
            'complete_receipt_on_latest': clo.complete_receipt(snapshot),
            'campaign_stop_file_present': stop_present,
            'lock_pid': lock, 'lock_pid_alive': lock_alive,
            'driver_pid_arg': expected_pid,
            'driver_pid_alive': expected_alive,
            'observed_so_far_seconds':
                (report or {}).get('observed_so_far_seconds')},
        'inputs_provenance': {
            'config_path': str(Path(opts.config).resolve()),
            'config_sha256': sha256_file(Path(opts.config)),
            'manifest_path': str(manifest_path),
            'manifest_sha256': sha256_file(manifest_path)
            if manifest_path.is_file() else None,
            'baseline_path': str(Path(opts.baseline).resolve())
            if opts.baseline else None,
            'driver_file': str(driver_file) if driver_file else None,
            'driver_file_sha256': sha256_file(driver_file)
            if driver_file else None,
            'python_file': str(python_file) if python_file else None,
            'python_file_sha256': sha256_file(python_file)
            if python_file else None},
        'usage_note': 'run once after the original soak definitively ends and '
                      'the old closeout has written its FINAL_REVIEW_READY '
                      'material; this tool then produces the corrected-caliber '
                      'final adjudication of the same terminal state',
    })
    return write_report(final, marker=report is not None)


def refuse_campaign_target(clo, campaign: Path, out_dir: Path) -> None:
    if campaign.is_dir():
        reason = clo.write_target_rejected(campaign, out_dir)
        if reason:
            raise SystemExit(f'{TOOL}: refusing out inside campaign: {reason}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=TOOL)
    sub = parser.add_subparsers(dest='command', required=True)
    review = sub.add_parser('review')
    review.add_argument('--campaign', required=True)
    review.add_argument('--config', required=True)
    review.add_argument('--manifest')
    review.add_argument('--baseline')
    review.add_argument('--driver-file')
    review.add_argument('--python-file')
    review.add_argument('--driver-pid', type=int)
    review.add_argument('--target-end')
    review.add_argument('--min-rounds', type=int)
    review.add_argument('--max-wall-seconds', type=float)
    review.add_argument('--min-distinct-inputs', type=int)
    review.add_argument('--allow-loose', action='store_true',
                        help='explicitly permit gate thresholds looser than '
                             'the config (synthetic validation only)')
    review.add_argument('--frozen-tree', default=str(DEFAULT_FROZEN_TREE))
    review.add_argument('--identity-json',
                        help='new-candidate binding JSON: {commit (40-hex), '
                             'tree (40-hex), modules: {soak_driver.py, '
                             'soak_audit.py, soak_closeout.py} (64-hex '
                             'each)} + optional python_sha256/config_sha256/'
                             'manifest_sha256 (64-hex) and campaign_schema/'
                             'receipt_schema (string). Incomplete core '
                             'identity is an invalid invocation (exit 5).')
    review.add_argument('--expected-commit',
                        help='override the expected frozen-tree commit')
    review.add_argument('--expected-tree',
                        help='override the expected frozen-tree tree hash')
    review.add_argument('--expected-module-sha256', action='append',
                        metavar='NAME=HEX',
                        help='override one expected support-module hash '
                             '(repeatable; NAME in {soak_driver.py, '
                             'soak_audit.py, soak_closeout.py})')
    review.add_argument('--expected-python-sha256',
                        help='pin the interpreter file hash (64-hex)')
    review.add_argument('--expected-config-sha256',
                        help='pin the canonical config digest (64-hex)')
    review.add_argument('--expected-manifest-sha256',
                        help='pin the manifest identity digest (64-hex)')
    review.add_argument('--expect-campaign-schema',
                        help='campaign.json schema this caliber is bound to '
                             '(default: legacy - no extra cap beyond the '
                             'frozen audit identity_schema check)')
    review.add_argument('--expect-receipt-schema',
                        help='receipt schema this caliber is bound to; a '
                             'campaign declaring any other receipt schema '
                             '(or none) can never be adjudicated PASS')
    review.add_argument('--out', required=True)
    review.add_argument('--checkpoint-dir')
    review.add_argument('--label', default='final-review-strict')
    args = parser.parse_args(argv)
    if args.command != 'review':
        parser.error(f'unknown command {args.command}')
    aud, clo, drv = load_frozen(args.frozen_tree)
    try:
        return do_review(args, aud, clo, drv)
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - honest internal failure
        print(f'{TOOL}: internal error: {error.__class__.__name__}: {error}',
              file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == '__main__':
    sys.exit(main())
