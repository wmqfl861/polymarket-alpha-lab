"""RV-05 corrected-72h launch preflight (READ-ONLY).

N5 parameterized copy (PAL_RV05_CAPACITY_20260921, node N5). Behavior is
byte-for-byte identical to the frozen V3 preflight for specs without a
"binding" section; when the spec carries one, additional candidate-binding
checks run (see the binding section contract below).

Second-segment capacity rewrite (2026-09-21, PAL_RV05_CAPACITY_20260921
second segment): check capacity_inputs_reachable now implements the W6
sub-input gate semantics. When the config carries
min_distinct_qualified_subinputs (the superseding gate; the legacy
min_distinct_inputs per-round gate is structurally unreachable and was
dropped from the bound config by the W6 ruling), the check performs
sub-input SCHEDULE ARITHMETIC: it replicates the frozen driver's
deterministic round->scenario selection (sorted scenario names +
derive_sub_seed) over minimum_valid_rounds rounds and verifies
sum(scheduled rounds x planned_rows) over kind=subinput scenarios >= the
gate. Same selection rule as assets/capacity_plan.py; no manifest scenario
code is executed (the frozen driver module is reused read-only exactly as
the other checks reuse its loaders). A config carrying the legacy
min_distinct_inputs keeps the legacy per-round arithmetic unchanged.

Pure read-only precondition checker for the one-shot corrected 72h soak
launch package (PAL_CORRECTIVE_LONGTASK_20260921_V3, RV-05). It NEVER
starts, stops, kills, writes or modifies anything outside its optional
--json-out evidence file. Original campaign / original root / historical
PIDs are only read; process identity comes from campaign lock/receipt
evidence, never from bare PID numbers, and no system-wide process scan
is performed.

Usage:
  python -I -S -B preflight-rv05.py [--spec launch-spec-rv05.json]
                                    [--json-out report.json]

Optional spec "binding" section (N5 candidate injection surface; absent =
legacy 22-check behavior unchanged, same check ids and field semantics):

  "binding": {
    "label": "<candidate label>",
    "config_sha256": "<canonical SoakConfig digest of paths.config>",
    "manifest_sha256": "<manifest identity digest of paths.manifest>",
    "receipt_schema": "<required receipt schema id>",
    "contract_path": "<receipt contract file>",     # required together
    "contract_sha256": "<sha256 of that file>",     # with receipt_schema
    "generator_py": "<generator file>",             # optional (N1 wiring)
    "generator_sha256": "<sha256 of the generator file>"
  }

When present it adds checks: binding_complete (all required pins present),
binding_config_digest, binding_manifest_digest, binding_receipt_contract
(only when receipt_schema is declared) and binding_generator_sha (only when
generator_py is declared). Any missing pin, unreadable file or hash
mismatch is FAIL, so a not-yet-ready new candidate can never reach GO.
All original checks are unchanged; the report gains additive provenance
fields only (preflight_variant, binding_checks_active, spec_binding).

Output: one JSON document on stdout:
  {schema, generated_at_utc, overall: GO|NO_GO, checks: [{id, status,
   status_text, detail}], summary: {...}}
Every check status is one of PASS / FAIL / UNKNOWN. overall is GO only
when every check is PASS.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
from hashlib import sha256
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

SCHEMA = 'pal-rv05-preflight-v1'
PASS, FAIL, UNKNOWN = 'PASS', 'FAIL', 'UNKNOWN'
VARIANT = 'rv05-n5-parameterized-v2'  # v2 = second-segment capacity rewrite


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_utc(text: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(text.replace('Z', '+00:00'))


def _sha256_file(path: Path) -> str | None:
    try:
        digest = sha256()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _load_driver_module(driver_py: Path):
    # Register in sys.modules BEFORE exec: dataclasses resolves
    # cls.__module__ through sys.modules while the module body is still
    # executing, so an unregistered name breaks @dataclass processing.
    spec = importlib.util.spec_from_file_location('rv05_soak_driver', driver_py)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(prog='preflight-rv05')
    parser.add_argument('--spec', default=str(here / 'launch-spec-rv05.json'))
    parser.add_argument('--json-out')
    args = parser.parse_args(argv)

    checks: list[dict] = []

    def add(cid: str, status: str, detail) -> None:
        checks.append({'id': cid, 'status': status,
                       'status_text': {PASS: 'met', FAIL: 'NOT met',
                                       UNKNOWN: 'unknown'}[status],
                       'detail': detail})

    def add_error(cid: str, error: BaseException) -> None:
        add(cid, UNKNOWN, {'error': f'{error.__class__.__name__}: {error}'})

    spec_path = Path(args.spec).resolve()
    drv = None
    spec = None
    try:
        spec = json.loads(spec_path.read_text(encoding='utf-8'))
        add('spec_load', PASS, {'spec': str(spec_path), 'schema': spec.get('schema')})
    except (OSError, ValueError) as error:
        add('spec_load', FAIL, {'error': str(error.__class__.__name__)})
    paths = (spec or {}).get('paths', {})
    frozen = (spec or {}).get('frozen', {})
    gates = (spec or {}).get('gates', {})
    window = (spec or {}).get('launch_window_utc', {})
    binding = (spec or {}).get('binding')
    binding_active = isinstance(binding, dict) and bool(binding)
    config_canonical_sha = None
    manifest_sha_found = None
    manifest_scenarios = None

    src_root = Path(paths.get('src_root', ''))
    driver_py = Path(paths.get('driver_py', ''))
    audit_py = Path(paths.get('audit_py', ''))
    closeout_py = Path(paths.get('closeout_py', ''))
    python_exe = Path(paths.get('python_exe', ''))
    config_path = Path(paths.get('config', ''))
    manifest_path = Path(paths.get('manifest', ''))
    original_campaign = Path(paths.get('original_campaign', ''))
    planned_root = Path(paths.get('planned_campaign_root', ''))
    planned_campaign = Path(paths.get('planned_campaign_dir', ''))
    v3_state = Path(paths.get('v3_state', ''))
    rehearsal_campaign = Path(paths.get('rehearsal_campaign', ''))
    smoke_root = Path(paths.get('smoke_rv05_root', ''))

    # --- import the frozen driver module (read-only reuse of its loaders) ---
    driver_import_error = None
    try:
        drv = _load_driver_module(driver_py)
    except Exception as error:  # noqa: BLE001 - report, never crash the preflight
        drv = None
        driver_import_error = f'{error.__class__.__name__}: {error}'
    if drv is None:
        add('driver_module_import', FAIL,
            {'error': driver_import_error or 'import failed',
             'note': 'frozen soak_driver.py could not be imported; '
                     'config/manifest checks report UNKNOWN below'})
    else:
        add('driver_module_import', PASS, {'driver_py': str(driver_py)})

    # --- 1. config parses through the frozen driver loader ---
    config_doc = None
    if drv is None:
        try:
            config_doc = json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            config_doc = None
        add('config_parses', UNKNOWN,
            {'note': 'driver module unavailable; JSON '
                     + ('readable' if config_doc is not None else 'unreadable')
                     + ' but SoakConfig validation not performed'})
    else:
        try:
            config_doc = json.loads(config_path.read_text(encoding='utf-8'))
            cfg = drv.SoakConfig.from_dict(config_doc)
            config_canonical_sha = sha256(cfg.canonical_bytes()).hexdigest()
            add('config_parses', PASS, {
                'config': str(config_path),
                'canonical_sha256': config_canonical_sha,
                'master_seed': cfg.master_seed,
                'round_period_seconds': cfg.round_period_seconds,
                'heartbeat_seconds': cfg.heartbeat_seconds,
                'checkpoint_seconds': cfg.checkpoint_seconds,
                'summary_seconds': cfg.summary_seconds,
                'max_unobserved_gap_seconds': cfg.max_unobserved_gap_seconds,
                'min_rounds': cfg.min_rounds,
                'max_wall_seconds': cfg.max_wall_seconds,
                'workers': cfg.workers,
                'volume_min_free_bytes': cfg.volume_min_free_bytes,
                'audit_gate_min_distinct_inputs': config_doc.get('min_distinct_inputs'),
                'note': 'min_distinct_inputs is not a driver key; it is the '
                        'audit --min-distinct-inputs / closeout gate value'})
        except (OSError, ValueError) as error:
            add('config_parses', FAIL,
                {'error': f'{error.__class__.__name__}: {error}',
                 'note': 'SoakConfigError text is a fixed token; see driver source'})

    # --- 2. scenario manifest parses through the frozen loader ---
    if drv is None:
        add('manifest_parses', UNKNOWN,
            {'note': 'driver module unavailable; manifest validation not performed'})
    else:
        try:
            scenarios, manifest_sha = drv._load_manifest(manifest_path)
            manifest_sha_found = manifest_sha
            manifest_scenarios = scenarios
            add('manifest_parses', PASS, {
                'manifest': str(manifest_path), 'manifest_sha256': manifest_sha,
                'scenario_count': len(scenarios),
                'scenarios': [{'name': s.name, 'kind': s.kind} for s in scenarios]})
        except (OSError, ValueError) as error:
            add('manifest_parses', FAIL, {'error': f'{error.__class__.__name__}'})

    # --- 3/4. frozen candidate tree: HEAD + clean worktree ---
    try:
        head = subprocess.run(
            ['git', '--no-optional-locks', '-C', str(src_root),
             'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=60).stdout.strip()
        add('frozen_src_head',
            PASS if head == frozen.get('commit') else FAIL,
            {'head': head, 'expected': frozen.get('commit')})
    except (OSError, subprocess.SubprocessError) as error:
        add_error('frozen_src_head', error)
    try:
        proc = subprocess.run(
            ['git', '--no-optional-locks', '-C', str(src_root),
             'status', '--porcelain'],
            capture_output=True, text=True, timeout=60)
        dirty = [line for line in proc.stdout.splitlines() if line.strip()]
        add('frozen_src_clean', PASS if not dirty else FAIL,
            {'dirty_entries': dirty[:20], 'dirty_count': len(dirty)})
    except (OSError, subprocess.SubprocessError) as error:
        add_error('frozen_src_clean', error)

    # --- 5. frozen tool-chain hashes (driver/audit/closeout/python) ---
    for cid, path, expected in (
            ('frozen_driver_sha', driver_py, frozen.get('driver_sha256')),
            ('frozen_audit_sha', audit_py, frozen.get('audit_sha256')),
            ('frozen_closeout_sha', closeout_py, frozen.get('closeout_sha256')),
            ('runtime_python_ok', python_exe, frozen.get('python_sha256'))):
        try:
            got = _sha256_file(path)
            add(cid, PASS if got == expected else FAIL,
                {'path': str(path), 'sha256': got, 'expected': expected})
        except OSError as error:
            add_error(cid, error)

    # --- 5b. candidate binding pins (N5 parameterization; active only when
    #          the spec carries a "binding" object — a not-yet-ready new
    #          candidate stays NO_GO through these checks) ---
    if binding_active:
        required = ['config_sha256', 'manifest_sha256']
        if isinstance(binding.get('receipt_schema'), str) \
                and binding.get('receipt_schema'):
            required += ['contract_path', 'contract_sha256']
        missing = [key for key in required
                   if not isinstance(binding.get(key), str) or not binding[key]]
        add('binding_complete', FAIL if missing else PASS,
            {'missing_pins': missing, 'required_pins': required,
             'declared_keys': sorted(binding.keys()),
             'note': 'spec carries a candidate binding section; every '
                     'required pin must be present before GO'})
        if isinstance(binding.get('config_sha256'), str) and binding['config_sha256']:
            if config_canonical_sha is None:
                add('binding_config_digest', UNKNOWN,
                    {'note': 'canonical config digest unavailable (driver '
                             'module not imported or config unparseable); '
                             'cannot verify the pinned config digest'})
            else:
                add('binding_config_digest',
                    PASS if config_canonical_sha == binding['config_sha256'] else FAIL,
                    {'recomputed': config_canonical_sha,
                     'pinned': binding['config_sha256'],
                     'config': str(config_path)})
        if isinstance(binding.get('manifest_sha256'), str) and binding['manifest_sha256']:
            if manifest_sha_found is None:
                add('binding_manifest_digest', UNKNOWN,
                    {'note': 'manifest identity digest unavailable (driver '
                             'module not imported or manifest unparseable); '
                             'cannot verify the pinned manifest digest'})
            else:
                add('binding_manifest_digest',
                    PASS if manifest_sha_found == binding['manifest_sha256'] else FAIL,
                    {'recomputed': manifest_sha_found,
                     'pinned': binding['manifest_sha256'],
                     'manifest': str(manifest_path)})
        if isinstance(binding.get('receipt_schema'), str) and binding['receipt_schema']:
            contract_path = Path(binding.get('contract_path', ''))
            expected_contract = binding.get('contract_sha256')
            got_contract = _sha256_file(contract_path)
            add('binding_receipt_contract',
                PASS if got_contract is not None and expected_contract
                and got_contract == expected_contract else FAIL,
                {'contract_path': str(contract_path),
                 'sha256': got_contract, 'pinned': expected_contract,
                 'receipt_schema': binding['receipt_schema'],
                 'note': 'the receipt contract for the declared schema must '
                         'exist on disk and hash-match the pin'})
        if isinstance(binding.get('generator_py'), str) and binding['generator_py']:
            gen_path = Path(binding['generator_py'])
            got_gen = _sha256_file(gen_path)
            add('binding_generator_sha',
                PASS if got_gen is not None
                and got_gen == binding.get('generator_sha256') else FAIL,
                {'generator_py': str(gen_path), 'sha256': got_gen,
                 'pinned': binding.get('generator_sha256')})

    # --- 6. original driver exited + lock released (lock evidence only) ---
    lock_path = original_campaign / 'driver.lock'
    lock = drv._read_json(lock_path) if drv is not None else None
    if lock is None and not lock_path.exists():
        add('original_driver_exited', PASS,
            {'driver_lock': 'absent (released on clean exit)',
             'note': 'lock file deleted by _release_lock on normal exit'})
    elif isinstance(lock, dict):
        pid = lock.get('pid')
        alive = drv._pid_alive(pid) if drv is not None else None
        add('original_driver_exited',
            PASS if alive is False else FAIL,
            {'driver_lock': 'present', 'lock_pid': pid, 'pid_alive': alive,
             'note': 'identity from campaign lock record, not the historical '
                     'PID number; read-only liveness probe only'})
    else:
        add('original_driver_exited', UNKNOWN,
            {'driver_lock': 'present but unreadable/invalid'})

    # --- 7. original campaign has a closing receipt ---
    segments_dir = original_campaign / 'segments'
    closes: list[tuple[Path, float]] = []
    if segments_dir.is_dir():
        for seg in sorted(segments_dir.iterdir()):
            close = seg / 'segment-close.json'
            try:
                if close.is_file():
                    closes.append((close, close.stat().st_mtime))
            except OSError:
                continue
    if closes:
        add('original_segment_close_present', PASS,
            {'closed_segments': [c[0].parent.name for c in closes]})
    else:
        add('original_segment_close_present', FAIL,
            {'note': 'no segments/segment-*/segment-close.json yet; the '
                     'original run has not closed a segment (still running '
                     'or abnormal state)'})

    # --- 8. original evidence stable >= 900s (mtime-based, read-only) ---
    stability = gates.get('evidence_stability_seconds', 900)
    mtime_candidates: list[float] = [m for _p, m in closes]
    for name in ('campaign.json', 'driver.log', 'driver.lock'):
        probe = original_campaign / name
        try:
            if probe.exists():
                mtime_candidates.append(probe.stat().st_mtime)
        except OSError:
            pass
    if closes and mtime_candidates:
        newest = max(mtime_candidates)
        age = time.time() - newest
        add('original_evidence_stable',
            PASS if age >= stability else FAIL,
            {'newest_mtime_age_seconds': round(age, 1),
             'required_stable_seconds': stability,
             'note': 'newest mtime across segment-close receipts and '
                     'campaign/driver bookkeeping files'})
    else:
        add('original_evidence_stable', FAIL,
            {'note': 'no closing receipt to stabilize on yet'})

    # --- 9. original own children cleaned (campaign receipts only) ---
    running: list[dict] = []
    unclosed: list[str] = []
    scanned_rounds = 0
    if segments_dir.is_dir():
        for seg in sorted(segments_dir.iterdir())[:200]:
            if not (seg.is_dir() and re.fullmatch(r'segment-\d{6}', seg.name)):
                continue
            if drv is not None and drv._read_json(seg / 'segment-close.json') is None:
                unclosed.append(seg.name)
            rounds_root = seg / 'rounds'
            if not rounds_root.is_dir():
                continue
            for rd in sorted(rounds_root.iterdir())[:5000]:
                if not rd.is_dir():
                    continue
                record = drv._read_json(rd / 'round.json') if drv is not None else None
                scanned_rounds += 1
                if isinstance(record, dict) and record.get('status') == 'running':
                    running.append({'segment': seg.name, 'round_dir': rd.name})
    add('original_children_cleaned',
        PASS if not running and not unclosed and closes else FAIL,
        {'running_round_records': running[:10],
         'running_count': len(running),
         'unclosed_segments': unclosed,
         'round_dirs_scanned': scanned_rounds,
         'note': 'per campaign round records and segment receipts only; no '
                 'system process scan'})

    # --- 10. target volume free >= gate ---
    min_free = gates.get('volume_min_free_bytes', 10737418240)
    probe_dir = planned_root
    while not probe_dir.exists() and probe_dir.parent != probe_dir:
        probe_dir = probe_dir.parent
    try:
        free = shutil.disk_usage(probe_dir).free
        add('volume_free', PASS if free >= min_free else FAIL,
            {'probe_dir': str(probe_dir), 'free_bytes': free,
             'required_bytes': min_free,
             'free_gib': round(free / 1024 ** 3, 2)})
    except OSError as error:
        add_error('volume_free', error)

    # --- 11. planned campaign dir must not exist yet ---
    try:
        exists = planned_campaign.exists() or planned_root.exists()
        add('target_campaign_absent', PASS if not exists else FAIL,
            {'planned_root': str(planned_root),
             'planned_campaign_dir': str(planned_campaign),
             'exists': exists})
    except OSError as error:
        add_error('target_campaign_absent', error)

    # --- 12. no leftover processes from this batch (known markers only) ---
    leftovers = []
    for label, camp in (('rehearsal-v3', rehearsal_campaign),
                        ('smoke-rv05', smoke_root / 'campaign-01'),
                        ('rv05-planned', planned_campaign)):
        lock_file = Path(camp) / 'driver.lock'
        try:
            if not lock_file.exists():
                continue
            rec = drv._read_json(lock_file) if drv is not None else None
            if isinstance(rec, dict):
                alive = drv._pid_alive(rec.get('pid')) if drv is not None else None
                if alive is True:
                    leftovers.append({'campaign': str(camp),
                                      'lock_pid': rec.get('pid')})
        except OSError:
            leftovers.append({'campaign': str(camp), 'error': 'lock unreadable'})
    add('no_leftover_own_processes', PASS if not leftovers else FAIL,
        {'live_locks': leftovers,
         'note': 'only own-batch campaign lock files are probed (known '
                 'launch markers); no system-wide process enumeration'})

    # --- 13. launch window ---
    try:
        earliest = _parse_utc(window.get('earliest', ''))
        latest = _parse_utc(window.get('latest', ''))
        now = _dt.datetime.now(_dt.timezone.utc)
        in_window = earliest <= now <= latest
        add('launch_window', PASS if in_window else FAIL,
            {'now_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
             'earliest': window.get('earliest'), 'latest': window.get('latest'),
             'in_window': in_window,
             'seconds_to_earliest': round((earliest - now).total_seconds(), 1)
             if now < earliest else None,
             'seconds_to_latest': round((latest - now).total_seconds(), 1)
             if now < latest else None})
    except (ValueError, TypeError) as error:
        add_error('launch_window', error)

    # --- 14. windows-kit adjudication status (V3_STATE pending marker) ---
    try:
        state_text = v3_state.read_text(encoding='utf-8')
        rv05_block = state_text.split('RV-05', 1)
        block = rv05_block[1] if len(rv05_block) > 1 else state_text
        pending_block = block.split('待满足', 1)
        pending_text = pending_block[1][:1200] if len(pending_block) > 1 else ''
        still_pending = 'windows-kit' in pending_text
        adjudicated = re.search(r'windows-kit[^\n]{0,120}(已裁决|裁决[:：=]\s*通过)',
                                state_text) is not None
        if adjudicated:
            add('windows_kit_adjudicated', PASS,
                {'marker': 'adjudication-passed marker found in V3_STATE.md'})
        elif still_pending:
            add('windows_kit_adjudicated', FAIL,
                {'marker': 'V3_STATE.md RV-05 待满足 block still lists the '
                           'windows-kit failure adjudication as pending '
                           '(coordinator decision)',
                 'note': 'preflight reads the recorded state only; it does '
                         'not decide the adjudication'})
        else:
            add('windows_kit_adjudicated', UNKNOWN,
                {'marker': 'no windows-kit pending marker and no '
                           'adjudication-passed marker found; update '
                           'V3_STATE.md to record either state explicitly'})
    except OSError as error:
        add_error('windows_kit_adjudicated', error)

    # --- 15. capacity: can the gates be reached at all (task order §6:
    #          启动前先验证容量可达) ---
    # Second-segment rewrite (2026-09-21): the W6 bound config carries ONLY
    # min_distinct_qualified_subinputs (the legacy per-round
    # min_distinct_inputs gate is structurally unreachable under per-round
    # logical-input semantics and was superseded by the sub-input gate —
    # RV05_STATE §12 W6). New semantics = sub-input schedule arithmetic:
    # replicate the frozen driver's deterministic round->scenario selection
    # over minimum_valid_rounds rounds and verify
    # sum(scheduled rounds x planned_rows) >= the gate. ARITHMETIC ONLY —
    # no manifest scenario code is executed; the frozen driver module is
    # reused read-only for derive_sub_seed exactly as the other checks
    # reuse its loaders. Same selection rule as assets/capacity_plan.py
    # (sorted-names/sha256-soak-seed-round-first16-mod-n); reproduces the
    # W4a record (ok-compute 74/864 rounds -> 74x2048 = 151552 >= 100000).
    # A config still carrying the legacy min_distinct_inputs keeps the
    # legacy per-round arithmetic unchanged.
    try:
        period = float(config_doc['round_period_seconds'])
        max_wall = float(config_doc['max_wall_seconds'])
        required_rounds = int(config_doc['minimum_valid_rounds'])
        max_rounds_theoretical = int(max_wall // period) + 1
        subinput_gate = config_doc.get('min_distinct_qualified_subinputs')
        gate_is_int = (isinstance(subinput_gate, int)
                       and not isinstance(subinput_gate, bool))
        if subinput_gate is not None:
            if not gate_is_int or subinput_gate <= 0:
                add('capacity_inputs_reachable', FAIL,
                    {'gate_field': 'min_distinct_qualified_subinputs',
                     'declared_value': subinput_gate,
                     'note': 'sub-input gate declared but not a positive '
                             'integer; the gate cannot be evaluated'})
            elif drv is None or manifest_scenarios is None:
                add('capacity_inputs_reachable', UNKNOWN,
                    {'note': 'driver module or manifest parse unavailable; '
                             'sub-input schedule arithmetic cannot run'})
            else:
                seed = int(config_doc['master_seed'])
                names = [s.name for s in manifest_scenarios]
                rounds_per = {name: 0 for name in names}
                for number in range(1, required_rounds + 1):
                    picked = names[drv.derive_sub_seed(seed, number)
                                   % len(names)]
                    rounds_per[picked] += 1
                rows = []
                planned_slots = 0
                for scenario in manifest_scenarios:
                    if scenario.kind != 'subinput':
                        continue
                    slots = (rounds_per.get(scenario.name, 0)
                             * scenario.planned_rows)
                    planned_slots += slots
                    rows.append({
                        'scenario': scenario.name,
                        'kind': scenario.kind,
                        'scheduled_rounds': rounds_per.get(scenario.name, 0),
                        'planned_rows_per_round': scenario.planned_rows,
                        'planned_slots': slots})
                add('capacity_inputs_reachable',
                    PASS if rows and planned_slots >= subinput_gate else FAIL,
                    {'gate_semantics':
                        'min_distinct_qualified_subinputs (sub-input '
                        'schedule arithmetic, ARITHMETIC_ONLY_NOT_'
                        'EXECUTION_PROOF)',
                     'master_seed': seed,
                     'scheduled_rounds': required_rounds,
                     'selection_rule':
                        'sorted-names/sha256-soak-seed-round-first16-mod-n '
                        '(frozen driver derive_sub_seed; same rule as '
                        'assets/capacity_plan.py)',
                     'subinput_scenarios': rows,
                     'planned_generated_slots': planned_slots,
                     'required_min_distinct_qualified_subinputs': subinput_gate,
                     'margin_slots': planned_slots - subinput_gate,
                     'corroborating_evidence':
                        'W4a limited capacity trial: 6144/6144 distinct '
                        'qualified sub-inputs with zero dedup folding and '
                        'zero domain overlaps (evidence\\w4a\\capacity-'
                        'trial); W4b rehearsal audit PASS 6144/4096',
                     'note': 'planned slots over the minimum_valid_rounds '
                             'schedule; a gate declared with no kind='
                             'subinput scenario is structurally unreachable '
                             '(FAIL); actual distinctness is certified '
                             'post-run by the frozen N2 audit with '
                             '--min-distinct-qualified-subinputs on a '
                             'complete-closed campaign'})
        else:
            required_inputs = int(config_doc['min_distinct_inputs'])
            # Frozen corrected audit: verified_distinct_inputs counts one
            # (scenario, sub_seed) key per PASSED round (soak_audit.py R1), and
            # the driver runs exactly one round per period boundary.
            max_verified_inputs = max_rounds_theoretical
            add('capacity_inputs_reachable',
                PASS if max_verified_inputs >= required_inputs else FAIL,
                {'max_rounds_theoretical': max_rounds_theoretical,
                 'arithmetic': f'floor({max_wall}/{period})+1',
                 'max_verified_distinct_inputs': max_verified_inputs,
                 'required_min_distinct_inputs': required_inputs,
                 'note': 'LEGACY per-round semantics (config carries only '
                         'min_distinct_inputs): the frozen audit counts one '
                         'verified logical input (scenario, sub_seed) per '
                         'PASSED round; one round per period; therefore '
                         'verified_distinct_inputs <= rounds <= floor('
                         'max_wall/period)+1. If this check fails the '
                         'declared input gate is structurally unreachable '
                         'under the frozen code and the task-book timings'})
        add('capacity_rounds_margin',
            PASS if max_rounds_theoretical >= required_rounds else FAIL,
            {'max_rounds_theoretical': max_rounds_theoretical,
             'required_passed_rounds': required_rounds,
             'margin_rounds': max_rounds_theoretical - required_rounds,
             'note': 'R2 semantics: only PASSED rounds count; the margin is '
                     'the number of non-passing rounds (failure/interruption/'
                     'restart slot loss) the run can absorb and still meet '
                     'minimum_valid_rounds. Margin <= 0 means structurally '
                     'unreachable; margin 1 means zero tolerance'})
    except (KeyError, TypeError, ValueError) as error:
        add_error('capacity_inputs_reachable', error)

    # --- verdict ---
    unmet = [c for c in checks if c['status'] != PASS]
    overall = 'GO' if not unmet else 'NO_GO'
    report = {
        'schema': SCHEMA,
        'preflight_variant': VARIANT,
        'binding_checks_active': binding_active,
        'generated_at_utc': _utcnow_iso(),
        'overall': overall,
        'unmet_count': len(unmet),
        'unmet_ids': [c['id'] for c in unmet],
        'checks': checks,
        'protections': {
            'read_only': True,
            'original_campaign_touched': False,
            'processes_killed': 0,
            'launch_performed': False,
        },
    }
    if binding_active:
        report['spec_binding'] = {
            'label': binding.get('label'),
            'receipt_schema': binding.get('receipt_schema'),
            'declared_pins': sorted(
                key for key in binding
                if key.endswith('_sha256') or key.endswith('_path')
                or key.endswith('_py')),
            'note': 'candidate binding section present; every pinned digest '
                    'must match for GO; absent pins or mismatches keep the '
                    'overall verdict NO_GO',
        }
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.json_out:
        Path(args.json_out).write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0 if overall == 'GO' else 1


if __name__ == '__main__':
    sys.exit(main())
