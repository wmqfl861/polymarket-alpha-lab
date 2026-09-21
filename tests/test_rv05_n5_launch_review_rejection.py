"""N5 rejection-first tests for the parameterized RV-05 launch package and
final reviewer (PAL_RV05_CAPACITY_20260921, node N5).

Rejection tests BEFORE any launch: these tests NEVER start a soak, never
create a STOP file, never touch the original campaign / original launch
package / original final reviewer / PIDs 62420/20372/98500, and run purely
against SYNTHETIC materials plus the read-only frozen tree.

Covered contracts:

(a) schema mixing - a campaign carrying a NEW receipt schema cannot be
    adjudicated PASS by a reviewer not bound to it (UNKNOWN cap); a
    campaign.json schema other than pal-soak-campaign-v1 is FAILED by the
    frozen audit's own identity_schema check (rejection);
(b) identity hash drift - candidate commit / module / config / manifest
    / contract / generator pin drift keeps preflight NO_GO; recorded-vs-
    bound or recorded-vs-recomputed drift makes the final review FAIL;
    frozen toolchain drift caps the final review at UNKNOWN;
(c) duplicate launch - a valid v1 or v2 receipt with dry_run=false refuses
    a second real launch (exit 3) BEFORE the preflight runs and with zero
    side effects;
(d) lost-ack - a claim without a completed receipt, or a half-written /
    unreadable receipt, refuses with exit 6, keeps the claim byte-identical
    and never auto-relaunches (launch state stays UNKNOWN);
(e) preflight not-ready - an incomplete candidate binding, a missing
    receipt contract or a contract hash mismatch keeps the preflight NO_GO.

Legacy regression: a spec WITHOUT a binding section produces exactly the
legacy preflight check set; the final reviewer without binding flags
reproduces the V3 four-path validation (FAIL/PASS/UNKNOWN/NOT_RUN) on the
same synthetic materials V3 used (RV-04 rehearsal campaign copy).

Environment contract (skip cleanly when absent, so this file is inert in
contexts without the WorkRoot layout):

  PAL_RV05_N5_LAUNCH_DIR        dir with preflight-rv05.py + launch-rv05.ps1
                               (parameterized N5 copies)
  PAL_RV05_N5_REVIEW_DIR        dir with run_final_review.py (N5 copy)
  PAL_RV05_N5_FROZEN_TREE       read-only frozen repo root containing
                               tests/support/{soak_driver,soak_audit,
                               soak_closeout}.py at 2584f6f2
  PAL_RV05_N5_PYTHON            sanitized python.exe used to run the tools
  PAL_RV05_N5_REHEARSAL_DIR     dir with campaign-rv04a/, config-rv04.json,
                               manifest-rv04.json (fixture copies)
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path

import pytest

_HEX64 = re.compile('[0-9a-f]{64}')


def _env_dir(name):
    value = os.environ.get(name)
    return Path(value).resolve() if value else None


LAUNCH_DIR = _env_dir('PAL_RV05_N5_LAUNCH_DIR')
REVIEW_DIR = _env_dir('PAL_RV05_N5_REVIEW_DIR')
FROZEN_TREE = _env_dir('PAL_RV05_N5_FROZEN_TREE')
PY = Path(os.environ['PAL_RV05_N5_PYTHON']).resolve() \
    if os.environ.get('PAL_RV05_N5_PYTHON') else None
REHEARSAL = _env_dir('PAL_RV05_N5_REHEARSAL_DIR')

_MISSING = [name for name, value in (
    ('PAL_RV05_N5_LAUNCH_DIR', LAUNCH_DIR),
    ('PAL_RV05_N5_REVIEW_DIR', REVIEW_DIR),
    ('PAL_RV05_N5_FROZEN_TREE', FROZEN_TREE),
    ('PAL_RV05_N5_PYTHON', PY),
    ('PAL_RV05_N5_REHEARSAL_DIR', REHEARSAL)) if value is None]
if _MISSING:
    pytest.skip(
        'N5 rejection tests need the WorkRoot layout via env vars '
        f'(missing: {", ".join(_MISSING)}); skipping', allow_module_level=True)

PREFLIGHT_PY = LAUNCH_DIR / 'preflight-rv05.py'
LAUNCH_PS1 = LAUNCH_DIR / 'launch-rv05.ps1'
NOMINAL_MANIFEST = LAUNCH_DIR / 'manifest-rv05.json'
REVIEW_PY = REVIEW_DIR / 'run_final_review.py'
DRIVER_FILE = FROZEN_TREE / 'tests' / 'support' / 'soak_driver.py'
AUDIT_FILE = FROZEN_TREE / 'tests' / 'support' / 'soak_audit.py'
CLOSEOUT_FILE = FROZEN_TREE / 'tests' / 'support' / 'soak_closeout.py'
for _required in (PREFLIGHT_PY, LAUNCH_PS1, NOMINAL_MANIFEST, REVIEW_PY,
                  DRIVER_FILE, AUDIT_FILE, CLOSEOUT_FILE, PY,
                  REHEARSAL / 'campaign-rv04a',
                  REHEARSAL / 'config-rv04.json',
                  REHEARSAL / 'manifest-rv04.json'):
    if not _required.exists():
        pytest.skip(f'N5 layout incomplete: {_required} missing; skipping',
                    allow_module_level=True)

LEGACY_PREFLIGHT_IDS = [
    'spec_load', 'driver_module_import', 'config_parses', 'manifest_parses',
    'frozen_src_head', 'frozen_src_clean', 'frozen_driver_sha',
    'frozen_audit_sha', 'frozen_closeout_sha', 'runtime_python_ok',
    'original_driver_exited', 'original_segment_close_present',
    'original_evidence_stable', 'original_children_cleaned', 'volume_free',
    'target_campaign_absent', 'no_leftover_own_processes', 'launch_window',
    'windows_kit_adjudicated', 'capacity_inputs_reachable',
    'capacity_rounds_margin',
]
LAUNCHER = 'powershell'
IS_WINDOWS = os.name == 'nt'
HAVE_LAUNCHER = IS_WINDOWS and shutil.which(LAUNCHER) is not None


def sha256_file(path) -> str:
    digest = sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 16), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, doc) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + '\n',
        encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


_FROZEN_DRV = None


def frozen_driver():
    """Import the frozen soak_driver BY FILE PATH (read-only; canonical
    hashing only).

    Loading by path — never via a sys.path package import — is load-bearing:
    ``_load_manifest`` resolves pytest scenario files against the DRIVER's
    own repo root, so the same manifest bytes yield a different identity
    digest depending on which tree's driver loads it. The preflight and
    final reviewer under test import the driver from FROZEN_TREE explicitly;
    this helper must do exactly the same or the pinned digests diverge.
    """
    global _FROZEN_DRV
    if _FROZEN_DRV is None:
        spec = importlib.util.spec_from_file_location(
            'n5_frozen_soak_driver', DRIVER_FILE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _FROZEN_DRV = module
    return _FROZEN_DRV


def canonical_config_sha(doc) -> str:
    drv = frozen_driver()
    return drv._sha256_bytes(drv.SoakConfig.from_dict(doc).canonical_bytes())


def run_tool(args, timeout=240):
    return subprocess.run(
        [str(PY), '-I', '-S', '-B', *[str(a) for a in args]],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=timeout)


# --------------------------------------------------------------------------
# synthetic preflight environment
# --------------------------------------------------------------------------

@pytest.fixture(scope='module')
def synth(tmp_path_factory):
    """A synthetic environment on which the parameterized preflight is GO
    when the spec matches it exactly (all-GO baseline for drift tests)."""
    root = tmp_path_factory.mktemp('n5synth')

    # synthetic candidate repo (git HEAD == whatever spec pins)
    repo = root / 'cand-repo'
    repo.mkdir()
    (repo / 'README.md').write_text('synthetic candidate repo\n',
                                    encoding='utf-8')
    for args in (['init', '-q', str(repo)],
                 ['-C', str(repo), '-c', 'user.name=n5-test',
                  '-c', 'user.email=n5@test.invalid', 'add', '-A'],
                 ['-C', str(repo), '-c', 'user.name=n5-test',
                  '-c', 'user.email=n5@test.invalid',
                  'commit', '-q', '-m', 'synthetic candidate']):
        subprocess.run(['git', *args], check=True, capture_output=True,
                       timeout=60)
    head = subprocess.run(
        ['git', '-C', str(repo), 'rev-parse', 'HEAD'],
        capture_output=True, text=True, check=True, timeout=60).stdout.strip()

    # synthetic ORIGINAL campaign: closed segment, stable evidence, no lock
    orig = root / 'orig-campaign'
    seg = orig / 'segments' / 'segment-000001'
    seg.mkdir(parents=True)
    close = seg / 'segment-close.json'
    write_json(close, {'reason': 'complete',
                       'rounds_total': {'passed': 2, 'failed': 0,
                                        'interrupted': 0, 'unknown': 0},
                       'stopped_wall': time.time() - 2000,
                       'targets_met': {'min_rounds': True}})
    past = time.time() - 2000
    os.utime(close, (past, past))

    v3_state = root / 'V3_STATE.md'
    v3_state.write_text(
        '# synthetic state\nwindows-kit failure adjudication: 已裁决 passed '
        '(synthetic marker)\n', encoding='utf-8')

    manifest = root / 'manifest.json'
    shutil.copyfile(NOMINAL_MANIFEST, manifest)

    config = {
        'master_seed': 2026092199,
        'round_period_seconds': 1,
        'heartbeat_seconds': 10,
        'checkpoint_seconds': 60,
        'progress_summary_seconds': 120,
        'max_unobserved_gap_seconds': 60,
        'scenario_timeout_ms': 5000,
        'cleanup_timeout_ms': 5000,
        'per_round_log_bytes': 65536,
        'max_stdout_bytes': 262144,
        'max_stderr_bytes': 65536,
        'max_evidence_bytes': 268435456,
        'max_repro_files': 20,
        'minimum_volume_free_bytes': 1,
        'minimum_valid_rounds': 5,
        'max_rounds': 120,
        'max_wall_seconds': 600,
        'workers': 1,
        'candidate': {'name': 'n5-synthetic',
                      'commit': head,
                      'purpose': 'n5 rejection-test synthetic'},
        'scenario_manifest': str(manifest),
        'min_distinct_inputs': 10,
    }
    config_path = root / 'config-synth.json'
    write_json(config_path, config)

    # receipt contract + "generator" for binding tests
    contract = root / 'receipt-contract.json'
    write_json(contract, {'schema': 'pal-soak-receipt-v2-synthetic',
                          'rows': '[input_sha256,result_sha256]'})
    generator = root / 'gen.py'
    generator.write_text('# synthetic generator\n', encoding='utf-8')

    return {
        'root': root, 'repo': repo, 'head': head,
        'orig': orig, 'v3_state': v3_state,
        'config': config_path, 'config_doc': config,
        'manifest': manifest,
        # the manifest pin is the IDENTITY digest (frozen _load_manifest),
        # not the raw file hash — same value space as campaign.json's
        # manifest_sha256 and preflight's manifest_parses detail
        'manifest_sha': frozen_driver()._load_manifest(manifest)[1],
        'config_canonical_sha': canonical_config_sha(config),
        'contract': contract, 'contract_sha': sha256_file(contract),
        'generator': generator, 'generator_sha': sha256_file(generator),
        'driver_sha': sha256_file(DRIVER_FILE),
        'audit_sha': sha256_file(AUDIT_FILE),
        'closeout_sha': sha256_file(CLOSEOUT_FILE),
        'python_sha': sha256_file(PY),
        'planned': root / 'planned',
    }


def make_spec(env, tmp_path, *, binding=None, frozen_overrides=None,
              window=('2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z'),
              name='spec.json'):
    spec = {
        'schema': 'pal-rv05-launch-spec-v1',
        'task_id': 'PAL_RV05_CAPACITY_20260921_N5_TEST',
        'run_label': 'n5-rejection-test',
        'frozen': {
            'commit': env['head'],
            'tree': '0' * 40,
            'base_commit': '2584f6f2d86ce19e7e2dab6bea6a27a013587753',
            'driver_sha256': env['driver_sha'],
            'audit_sha256': env['audit_sha'],
            'closeout_sha256': env['closeout_sha'],
            'python_sha256': env['python_sha'],
            **(frozen_overrides or {}),
        },
        'paths': {
            'v3_root': str(env['root']),
            'src_root': str(env['repo']),
            'python_exe': str(PY),
            'driver_py': str(DRIVER_FILE),
            'audit_py': str(AUDIT_FILE),
            'closeout_py': str(CLOSEOUT_FILE),
            'config': str(env['config']),
            'manifest': str(env['manifest']),
            'preflight': str(PREFLIGHT_PY),
            'original_campaign': str(env['orig']),
            'original_driver_pid_reported': None,
            'original_closeout_pid_reported': None,
            'v3_state': str(env['v3_state']),
            'rehearsal_campaign': str(env['root'] / 'rehearsal-absent'),
            'smoke_rv05_root': str(env['root'] / 'smoke-absent'),
            'planned_campaign_root': str(env['planned']),
            'planned_campaign_dir': str(env['planned'] / 'campaign'),
            'receipt': str(tmp_path / 'launch-receipt.json'),
            'claim': str(tmp_path / 'launch-claim.json'),
            'preflight_last': str(tmp_path / 'preflight-last.json'),
            'work_temp': str(tmp_path / 'work-temp'),
        },
        'gates': {
            'min_rounds': 5,
            'min_distinct_inputs': 10,
            'max_wall_seconds': 600,
            'max_unobserved_gap_seconds': 900,
            'evidence_stability_seconds': 900,
            'volume_min_free_bytes': 1,
        },
        'launch_window_utc': {'earliest': window[0], 'latest': window[1]},
        'once_only': True,
    }
    if binding is not None:
        spec['binding'] = binding
    spec_path = tmp_path / name
    write_json(spec_path, spec)
    return spec_path, spec


def run_preflight(spec_path, tmp_path):
    out = tmp_path / 'preflight-report.json'
    proc = run_tool([PREFLIGHT_PY, '--spec', spec_path, '--json-out', out])
    report = read_json(out) if out.exists() else None
    return proc, report


def good_binding(env):
    return {
        'label': 'n5-synthetic-binding',
        'config_sha256': env['config_canonical_sha'],
        'manifest_sha256': env['manifest_sha'],
        'receipt_schema': 'pal-soak-receipt-v2-synthetic',
        'contract_path': str(env['contract']),
        'contract_sha256': env['contract_sha'],
        'generator_py': str(env['generator']),
        'generator_sha256': env['generator_sha'],
    }


# --------------------------------------------------------------------------
# preflight: GO baseline, legacy regression, drift and not-ready rejections
# --------------------------------------------------------------------------

def test_preflight_synthetic_go_without_binding(synth, tmp_path):
    """All-GO baseline: with a fully matching spec the copy reaches GO
    (proves the parameterized copy's happy path works; no launch occurs —
    the preflight is read-only)."""
    spec_path, _spec = make_spec(synth, tmp_path)
    proc, report = run_preflight(spec_path, tmp_path)
    assert report is not None
    assert report['overall'] == 'GO', report['unmet_ids']
    assert proc.returncode == 0
    assert report['binding_checks_active'] is False
    assert [c['id'] for c in report['checks']] == LEGACY_PREFLIGHT_IDS


def test_preflight_binding_complete_still_go(synth, tmp_path):
    """A complete, matching binding keeps GO and adds exactly the five
    binding checks."""
    spec_path, _spec = make_spec(synth, tmp_path,
                                 binding=good_binding(synth))
    proc, report = run_preflight(spec_path, tmp_path)
    assert report['overall'] == 'GO', report['unmet_ids']
    assert proc.returncode == 0
    ids = [c['id'] for c in report['checks']]
    # legacy checks all present (their internal order is asserted by the
    # no-binding test) plus exactly the five binding checks
    assert set(LEGACY_PREFLIGHT_IDS) <= set(ids)
    assert set(ids) - set(LEGACY_PREFLIGHT_IDS) == {
        'binding_complete', 'binding_config_digest', 'binding_manifest_digest',
        'binding_receipt_contract', 'binding_generator_sha'}
    assert report['binding_checks_active'] is True
    assert report['spec_binding']['receipt_schema'] == \
        'pal-soak-receipt-v2-synthetic'


@pytest.mark.parametrize('field,check_id', [
    ('commit', 'frozen_src_head'),
    ('driver_sha256', 'frozen_driver_sha'),
    ('audit_sha256', 'frozen_audit_sha'),
    ('closeout_sha256', 'frozen_closeout_sha'),
    ('python_sha256', 'runtime_python_ok'),
])
def test_preflight_frozen_identity_drift_rejects(synth, tmp_path, field,
                                                 check_id):
    """(b) candidate/toolchain identity drift -> exactly that check FAILs,
    overall NO_GO, exit 1."""
    wrong = ('f' * 40) if field == 'commit' else ('f' * 64)
    spec_path, _spec = make_spec(synth, tmp_path,
                                 frozen_overrides={field: wrong})
    proc, report = run_preflight(spec_path, tmp_path)
    assert report['overall'] == 'NO_GO'
    assert proc.returncode == 1
    assert report['unmet_ids'] == [check_id]


@pytest.mark.parametrize('binding_mutator,check_id', [
    (lambda b, env: b.__setitem__('config_sha256', 'e' * 64),
     'binding_config_digest'),
    (lambda b, env: b.__setitem__('manifest_sha256', 'e' * 64),
     'binding_manifest_digest'),
    (lambda b, env: b.__setitem__('contract_sha256', 'e' * 64),
     'binding_receipt_contract'),
    (lambda b, env: b.__setitem__('generator_sha256', 'e' * 64),
     'binding_generator_sha'),
])
def test_preflight_binding_digest_drift_rejects(synth, tmp_path,
                                                binding_mutator, check_id):
    """(b) binding digest drift -> exactly that binding check FAILs."""
    binding = good_binding(synth)
    binding_mutator(binding, synth)
    spec_path, _spec = make_spec(synth, tmp_path, binding=binding)
    proc, report = run_preflight(spec_path, tmp_path)
    assert report['overall'] == 'NO_GO'
    assert proc.returncode == 1
    assert report['unmet_ids'] == [check_id]


def test_preflight_binding_missing_pin_rejects(synth, tmp_path):
    """(e) not-ready candidate: binding present but config_sha256 missing ->
    binding_complete is the sole unmet check; NO_GO."""
    binding = good_binding(synth)
    del binding['config_sha256']
    spec_path, _spec = make_spec(synth, tmp_path, binding=binding)
    proc, report = run_preflight(spec_path, tmp_path)
    assert report['overall'] == 'NO_GO'
    assert proc.returncode == 1
    assert report['unmet_ids'] == ['binding_complete']
    detail = next(c for c in report['checks']
                  if c['id'] == 'binding_complete')['detail']
    assert detail['missing_pins'] == ['config_sha256']


def test_preflight_contract_file_missing_rejects(synth, tmp_path):
    """(e) receipt contract declared but file absent -> NO_GO."""
    binding = good_binding(synth)
    binding['contract_path'] = str(tmp_path / 'absent-contract.json')
    spec_path, _spec = make_spec(synth, tmp_path, binding=binding)
    proc, report = run_preflight(spec_path, tmp_path)
    assert report['overall'] == 'NO_GO'
    assert proc.returncode == 1
    assert report['unmet_ids'] == ['binding_receipt_contract']


def test_preflight_nogo_regression_legacy_window(synth, tmp_path):
    """Legacy NO_GO path regression: a closed launch window keeps the
    legacy check ids and NO_GO (V3 behavior preserved)."""
    spec_path, _spec = make_spec(
        synth, tmp_path,
        window=('2020-01-01T00:00:00Z', '2020-01-02T00:00:00Z'))
    proc, report = run_preflight(spec_path, tmp_path)
    assert report['overall'] == 'NO_GO'
    assert proc.returncode == 1
    assert [c['id'] for c in report['checks']] == LEGACY_PREFLIGHT_IDS
    assert report['unmet_ids'] == ['launch_window']
    assert report['binding_checks_active'] is False


# --------------------------------------------------------------------------
# launcher: duplicate-launch and lost-ack rejections (no launch ever occurs)
# --------------------------------------------------------------------------

pytestmark_launcher = pytest.mark.skipif(
    not HAVE_LAUNCHER, reason='powershell launcher unavailable')


def run_launcher(spec_path, *, dry_run=False, timeout=180):
    args = [LAUNCHER, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(LAUNCH_PS1), '-SpecPath', str(spec_path)]
    if dry_run:
        args.append('-DryRun')
    return subprocess.run(args, capture_output=True, timeout=timeout)


def launcher_spec(synth, tmp_path):
    """NO_GO spec (closed window) with receipt/claim/report paths in tmp —
    even a guard-order bug could not reach an actual launch."""
    spec_path, spec = make_spec(
        synth, tmp_path,
        window=('2020-01-01T00:00:00Z', '2020-01-02T00:00:00Z'),
        name='launcher-spec.json')
    return spec_path, spec


@pytest.mark.parametrize('receipt_schema', [
    'pal-rv05-launch-receipt-v1', 'pal-rv05-launch-receipt-v2'])
def test_duplicate_launch_receipt_refuses(synth, tmp_path, receipt_schema):
    """(c) a valid completed receipt (v1 OR v2) refuses a second real launch
    before the preflight runs and with zero side effects."""
    if not HAVE_LAUNCHER:
        pytest.skip('powershell launcher unavailable')
    spec_path, spec = launcher_spec(synth, tmp_path)
    receipt = {
        'schema': receipt_schema, 'task_id': spec['task_id'],
        'dry_run': False, 'pid': 4242, 'claim_id': 'deadbeef',
        'launched_at_utc': '2026-09-21T00:00:00Z',
        'campaign_dir': spec['paths']['planned_campaign_dir']}
    write_json(spec['paths']['receipt'], receipt)
    proc = run_launcher(spec_path)
    assert proc.returncode == 3, proc.stdout
    assert b'REFUSED' in proc.stdout
    # guard precedes preflight and launch: no report, no claim, no campaign
    assert not Path(spec['paths']['preflight_last']).exists()
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['planned_campaign_root']).exists()


def test_duplicate_launch_receipt_wins_over_claim(synth, tmp_path):
    """(c) claim + completed receipt -> duplicate refusal (receipt wins)."""
    if not HAVE_LAUNCHER:
        pytest.skip('powershell launcher unavailable')
    spec_path, spec = launcher_spec(synth, tmp_path)
    write_json(spec['paths']['receipt'], {
        'schema': 'pal-rv05-launch-receipt-v2', 'dry_run': False})
    write_json(spec['paths']['claim'], {
        'schema': 'pal-rv05-launch-claim-v1', 'dry_run': False,
        'state': 'claimed'})
    proc = run_launcher(spec_path)
    assert proc.returncode == 3
    assert b'REFUSED' in proc.stdout


def test_lost_ack_claim_without_receipt_refuses(synth, tmp_path):
    """(d) claim without a receipt -> LOST_ACK refusal, state UNKNOWN, no
    automatic second launch, no preflight, no campaign creation."""
    if not HAVE_LAUNCHER:
        pytest.skip('powershell launcher unavailable')
    spec_path, spec = launcher_spec(synth, tmp_path)
    claim_bytes = json.dumps(
        {'schema': 'pal-rv05-launch-claim-v1',
         'claim_id': 'cafebabe1234', 'dry_run': False, 'state': 'claimed',
         'campaign_dir': spec['paths']['planned_campaign_dir']},
        indent=2, sort_keys=True).encode('utf-8') + b'\n'
    Path(spec['paths']['claim']).write_bytes(claim_bytes)
    proc = run_launcher(spec_path)
    assert proc.returncode == 6, proc.stdout
    assert b'LOST_ACK' in proc.stdout
    assert b'UNKNOWN' in proc.stdout
    assert b'no automatic second launch' in proc.stdout
    # lost-ack keeps the claim byte-identical and writes nothing else
    assert Path(spec['paths']['claim']).read_bytes() == claim_bytes
    assert not Path(spec['paths']['receipt']).exists()
    assert not Path(spec['paths']['preflight_last']).exists()
    assert not Path(spec['paths']['planned_campaign_root']).exists()


def test_lost_ack_is_stable_no_auto_recovery(synth, tmp_path):
    """(d) re-invoking after a lost-ack refusal repeats the refusal with the
    claim unchanged — no recovery, no second send, ever."""
    if not HAVE_LAUNCHER:
        pytest.skip('powershell launcher unavailable')
    spec_path, spec = launcher_spec(synth, tmp_path)
    claim_bytes = b'{"schema": "pal-rv05-launch-claim-v1", "dry_run": false,'
    Path(spec['paths']['claim']).write_bytes(claim_bytes)
    first = run_launcher(spec_path)
    assert first.returncode == 6
    second = run_launcher(spec_path)
    assert second.returncode == 6
    assert Path(spec['paths']['claim']).read_bytes() == claim_bytes
    assert not Path(spec['paths']['receipt']).exists()


def test_lost_ack_half_written_receipt_refuses(synth, tmp_path):
    """(d) a torn/half-written receipt (unparseable JSON) is lost-ack."""
    if not HAVE_LAUNCHER:
        pytest.skip('powershell launcher unavailable')
    spec_path, spec = launcher_spec(synth, tmp_path)
    Path(spec['paths']['receipt']).write_bytes(
        b'{"schema": "pal-rv05-launch-receipt-v2", "task_i')
    proc = run_launcher(spec_path)
    assert proc.returncode == 6, proc.stdout
    assert b'LOST_ACK' in proc.stdout
    assert b'unreadable/half-written' in proc.stdout


def test_dry_run_writes_no_claim_or_receipt(synth, tmp_path):
    """Dry-run performs the read-only preflight only: no claim, no receipt,
    nothing created under the planned root."""
    if not HAVE_LAUNCHER:
        pytest.skip('powershell launcher unavailable')
    spec_path, spec = launcher_spec(synth, tmp_path)
    proc = run_launcher(spec_path, dry_run=True)
    assert proc.returncode == 2  # NO_GO spec: dry run reports it
    assert b'DRY RUN' in proc.stdout
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['receipt']).exists()
    assert not Path(spec['paths']['planned_campaign_root']).exists()
    assert Path(spec['paths']['preflight_last']).exists()  # allowed artifact


def test_dry_run_after_receipt_is_informative_only(synth, tmp_path):
    """Dry-run after a real receipt stays informational (no refusal)."""
    if not HAVE_LAUNCHER:
        pytest.skip('powershell launcher unavailable')
    spec_path, spec = launcher_spec(synth, tmp_path)
    write_json(spec['paths']['receipt'], {
        'schema': 'pal-rv05-launch-receipt-v2', 'dry_run': False})
    proc = run_launcher(spec_path, dry_run=True)
    assert proc.returncode == 2  # NO_GO spec governs the dry-run exit
    assert b'NOTE (dry-run)' in proc.stdout


def test_go_spec_with_duplicate_receipt_never_launches(synth, tmp_path):
    """(c)+(d) strongest guard proof: even with an otherwise all-GO spec,
    the duplicate receipt refuses BEFORE any launch effect."""
    if not HAVE_LAUNCHER:
        pytest.skip('powershell launcher unavailable')
    spec_path, spec = make_spec(synth, tmp_path, name='go-spec.json')
    write_json(spec['paths']['receipt'], {
        'schema': 'pal-rv05-launch-receipt-v2', 'dry_run': False})
    proc = run_launcher(spec_path)
    assert proc.returncode == 3
    assert not Path(spec['paths']['planned_campaign_root']).exists()
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['preflight_last']).exists()


# --------------------------------------------------------------------------
# synthetic terminal campaigns for the final reviewer
# --------------------------------------------------------------------------

def build_synth_campaign(dest, *, incomplete=False, patch_campaign=None):
    """Replicate the V3 make_synthetic_terminal recipe on the fixture copy:
    tightened config, consistent config_sha256 in campaign/segment headers,
    complete close receipt, stop removed. `patch_campaign` overlays extra
    campaign.json fields (schema tampering, hash drift, receipt_schema)."""
    dest = Path(dest)
    campaign = dest / 'campaign'
    shutil.copytree(REHEARSAL / 'campaign-rv04a', campaign)
    config = read_json(REHEARSAL / 'config-rv04.json')
    config['minimum_valid_rounds'] = 5
    config['max_wall_seconds'] = 600
    config['candidate'] = dict(config['candidate'])
    config['candidate']['purpose'] = 'n5 rejection-test synthetic state'
    config['scenario_manifest'] = str(REHEARSAL / 'manifest-rv04.json')
    config_path = dest / 'config-synth.json'
    write_json(config_path, config)
    new_sha = canonical_config_sha(config)

    identity = read_json(campaign / 'campaign.json')
    identity['config_sha256'] = new_sha
    if patch_campaign:
        identity.update(patch_campaign)
    write_json(campaign / 'campaign.json', identity)
    seg = campaign / 'segments' / 'segment-000001'
    header = read_json(seg / 'segment.json')
    header['config_sha256'] = new_sha
    write_json(seg / 'segment.json', header)
    if incomplete:
        (seg / 'segment-close.json').unlink()
    else:
        close = read_json(seg / 'segment-close.json')
        close['reason'] = 'complete'
        close['targets_met'] = {'min_rounds': True}
        write_json(seg / 'segment-close.json', close)
    stop = campaign / 'stop'
    if stop.is_file():
        stop.unlink()
    return campaign, config_path


def run_review(campaign, config, out, *, manifest=None, min_rounds=5,
               min_inputs=10, extra=(), timeout=300):
    args = [REVIEW_PY, 'review',
            '--campaign', campaign,
            '--config', config,
            '--manifest', manifest or (REHEARSAL / 'manifest-rv04.json'),
            '--driver-file', DRIVER_FILE,
            '--python-file', PY,
            '--min-rounds', min_rounds,
            '--min-distinct-inputs', min_inputs,
            '--out', out,
            '--label', 'n5-rejection-test',
            *extra]
    proc = run_tool(args, timeout=timeout)
    report_path = Path(out) / 'final-review.json'
    report = read_json(report_path) if report_path.exists() else None
    return proc.returncode, report, proc


EXIT_BY_VERDICT = {'PASS': 0, 'FAIL': 3, 'UNKNOWN': 4, 'NOT_RUN': 2}


def assert_verdict(report, proc_code, expected):
    assert report is not None
    assert report['verdict'] == expected
    assert proc_code == EXIT_BY_VERDICT[expected]


# ---- four-path legacy regression (V3 val-a/b/b2/c parity) ----------------

def test_review_fail_path_real_caliber(synth, tmp_path):
    """val-a parity: unmodified rehearsal terminal state at 864/100000 ->
    honest FAIL, marker written."""
    campaign = tmp_path / 'val-a' / 'campaign'
    shutil.copytree(REHEARSAL / 'campaign-rv04a', campaign)
    code, report, _proc = run_review(
        campaign, REHEARSAL / 'config-rv04.json', tmp_path / 'val-a-out',
        min_rounds=864, min_inputs=100000)
    assert_verdict(report, code, 'FAIL')
    assert (Path(tmp_path) / 'val-a-out' / 'FINAL_REVIEW_STRICT').is_file()


def test_review_pass_path_small_gates(synth, tmp_path):
    """val-b parity: synthetic full-pass state with small gates -> PASS,
    marker written, no schema caps."""
    campaign, config = build_synth_campaign(tmp_path / 'val-b')
    code, report, _proc = run_review(campaign, config, tmp_path / 'val-b-out')
    assert_verdict(report, code, 'PASS')
    out = tmp_path / 'val-b-out'
    assert (out / 'FINAL_REVIEW_STRICT').is_file()
    assert report['schema_binding']['caps'] == []
    assert report['expected_binding']['source'] == 'legacy-frozen-defaults'


def test_review_unknown_path_snapshot_incomplete(synth, tmp_path):
    """val-b2 parity: segment-close removed -> SNAPSHOT_INCOMPLETE ->
    UNKNOWN, never FAIL-as-corruption."""
    campaign, config = build_synth_campaign(tmp_path / 'val-b2',
                                             incomplete=True)
    code, report, _proc = run_review(campaign, config, tmp_path / 'val-b2-out')
    assert_verdict(report, code, 'UNKNOWN')
    assert report['audit_overall'] == 'SNAPSHOT_INCOMPLETE'


def test_review_not_run_path_live_pid(synth, tmp_path):
    """val-c parity (synthetic): a live driver PID (this very test process)
    refuses the review: NOT_RUN, audit never executes, no marker."""
    campaign, config = build_synth_campaign(tmp_path / 'val-c')
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'val-c-out',
        extra=['--driver-pid', os.getpid()])
    assert_verdict(report, code, 'NOT_RUN')
    assert report['refuse_reason']['kind'] == 'still_running'
    assert report['audit_executed'] is False
    assert not (tmp_path / 'val-c-out' / 'FINAL_REVIEW_STRICT').exists()


# ---- (a) schema mixing -----------------------------------------------------

def test_review_legacy_reviewer_rejects_new_receipt_schema(synth, tmp_path):
    """(a) CORE: a campaign declaring a NEW receipt schema cannot be
    adjudicated PASS by the legacy-bound reviewer — the audit itself stays
    PASS (it ignores the unknown field), the schema cap forces UNKNOWN."""
    campaign, config = build_synth_campaign(
        tmp_path / 'mix-legacy',
        patch_campaign={'receipt_schema': 'pal-soak-receipt-v2-synthetic'})
    code, report, _proc = run_review(campaign, config, tmp_path / 'mix-out')
    assert report['audit_overall'] == 'PASS'
    assert_verdict(report, code, 'UNKNOWN')
    reasons = ' | '.join(report['verdict_reasons'])
    assert 'receipt_schema' in reasons
    assert 'pal-soak-receipt-v2-synthetic' in reasons
    caps = report['schema_binding']['caps']
    assert caps and caps[0]['kind'] == 'receipt_schema'
    assert report['verdict'] != 'PASS'


def test_review_bound_schema_missing_on_campaign(synth, tmp_path):
    """(a) a reviewer bound to the new receipt schema, run on a campaign
    that does not carry it, is capped at UNKNOWN."""
    campaign, config = build_synth_campaign(tmp_path / 'mix-bound')
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'mix-bound-out',
        extra=['--expect-receipt-schema', 'pal-soak-receipt-v2-synthetic'])
    assert_verdict(report, code, 'UNKNOWN')
    assert report['verdict'] != 'PASS'
    assert any('receipt_schema' in r for r in report['verdict_reasons'])


def test_review_campaign_schema_v2_is_audit_rejected(synth, tmp_path):
    """(a) a campaign.json schema other than pal-soak-campaign-v1 is FAILED
    by the frozen audit's identity_schema check (rejection branch)."""
    campaign, config = build_synth_campaign(
        tmp_path / 'mix-cschema',
        patch_campaign={'schema': 'pal-soak-campaign-v2'})
    code, report, _proc = run_review(campaign, config, tmp_path / 'mix-c-out')
    assert_verdict(report, code, 'FAIL')
    audit_ids = {c['check'] for c in report['audit']['checks']}
    failed = {c['check'] for c in report['audit']['checks']
              if c['status'] == 'FAIL'}
    assert 'identity_schema' in audit_ids and 'identity_schema' in failed


# ---- (b) identity hash drift ----------------------------------------------

def test_review_recorded_manifest_drift_fails(synth, tmp_path):
    """(b) campaign.json manifest_sha256 tampered -> identity FAIL."""
    campaign, config = build_synth_campaign(
        tmp_path / 'drift-manifest',
        patch_campaign={'manifest_sha256': 'd' * 64})
    code, report, _proc = run_review(campaign, config,
                                     tmp_path / 'drift-manifest-out')
    assert_verdict(report, code, 'FAIL')
    assert report['identity']['manifest_sha256']['status'] == 'FAIL'


def test_review_recorded_driver_drift_fails(synth, tmp_path):
    """(b) campaign.json driver_sha256 tampered -> file rehash mismatch."""
    campaign, config = build_synth_campaign(
        tmp_path / 'drift-driver',
        patch_campaign={'driver_sha256': 'd' * 64})
    code, report, _proc = run_review(campaign, config,
                                     tmp_path / 'drift-driver-out')
    assert_verdict(report, code, 'FAIL')
    assert report['identity']['driver_sha256']['status'] == 'FAIL'


def test_review_toolchain_commit_drift_caps_unknown(synth, tmp_path):
    """(b) expected commit drift vs the frozen tree -> toolchain UNKNOWN
    caps the verdict (everything else passes)."""
    campaign, config = build_synth_campaign(tmp_path / 'drift-toolchain')
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'drift-toolchain-out',
        extra=['--expected-commit', 'e' * 40])
    assert_verdict(report, code, 'UNKNOWN')
    assert report['toolchain']['status'] == 'UNKNOWN'
    assert any(d['kind'] == 'commit' for d in report['toolchain']['drift'])


def test_review_bound_manifest_pin_drift_fails(synth, tmp_path):
    """(b) explicitly bound manifest digest (audit identity_expected) vs the
    campaign's recorded value -> FAIL."""
    campaign, config = build_synth_campaign(tmp_path / 'drift-bound')
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'drift-bound-out',
        extra=['--expected-manifest-sha256', 'e' * 64])
    assert_verdict(report, code, 'FAIL')
    checks = {c['check']: c['status'] for c in report['audit']['checks']}
    assert checks.get('identity_expected') == 'FAIL'


# ---- binding injection ------------------------------------------------------

def test_identity_json_complete_binding_passes(synth, tmp_path):
    """Positive control: a complete identity-json pinning the true frozen
    identity keeps the full-pass campaign PASS."""
    campaign, config = build_synth_campaign(tmp_path / 'bind-ok')
    recorded = read_json(campaign / 'campaign.json')
    identity = {
        'commit': '2584f6f2d86ce19e7e2dab6bea6a27a013587753',
        'tree': '702d25b5aaabbd790bd48a0ebed8d1a79f12c24d',
        'modules': {
            'soak_driver.py': sha256_file(DRIVER_FILE),
            'soak_audit.py': sha256_file(AUDIT_FILE),
            'soak_closeout.py': sha256_file(CLOSEOUT_FILE),
        },
        'python_sha256': recorded['python_sha256'],
        'manifest_sha256': recorded['manifest_sha256'],
    }
    identity_path = tmp_path / 'bind-ok' / 'identity.json'
    write_json(identity_path, identity)
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'bind-ok-out',
        extra=['--identity-json', identity_path])
    assert_verdict(report, code, 'PASS')
    assert report['expected_binding']['source'] == 'identity-json/flags'
    assert report['identity']['driver_sha256_binding']['status'] == 'PASS'


@pytest.mark.parametrize('identity_doc', [
    {'commit': 'a' * 40},  # missing tree + modules
    {'commit': 'a' * 40, 'tree': 'b' * 40,
     'modules': {'soak_driver.py': 'c' * 64}},  # modules incomplete
    {'commit': 'NOT_HEX', 'tree': 'b' * 40,
     'modules': {'soak_driver.py': 'c' * 64, 'soak_audit.py': 'c' * 64,
                 'soak_closeout.py': 'c' * 64}},  # bad commit shape
])
def test_identity_json_incomplete_is_invalid_invocation(synth, tmp_path,
                                                        identity_doc):
    """An incomplete/malformed new-candidate binding is rejected (exit 5),
    never a silent legacy fallback."""
    campaign, config = build_synth_campaign(tmp_path / 'bind-bad')
    identity_path = tmp_path / 'bind-bad' / 'identity.json'
    write_json(identity_path, identity_doc)
    code, report, proc = run_review(
        campaign, config, tmp_path / 'bind-bad-out',
        extra=['--identity-json', identity_path])
    assert code == 5
    assert report is None
    assert 'identity-json' in (proc.stderr or '')
    assert not (tmp_path / 'bind-bad-out' / 'FINAL_REVIEW_STRICT').exists()


def test_identity_flag_bad_shape_is_invalid_invocation(synth, tmp_path):
    """A malformed --expected-commit value is rejected (exit 5)."""
    campaign, config = build_synth_campaign(tmp_path / 'flag-bad')
    code, _report, _proc = run_review(
        campaign, config, tmp_path / 'flag-bad-out',
        extra=['--expected-commit', 'zzzz'])
    assert code == 5
