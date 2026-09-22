"""N5 rejection-first tests for the parameterized RV-05 launch package and
final reviewer (PAL_RV05_CAPACITY_20260921, node N5; self-contained rewrite
PAL_RV05_CLOSURE_20260922, node N2).

Rejection tests BEFORE any launch: these tests NEVER start a soak beyond a
tiny synthetic driver campaign inside pytest tmp_path (see ``control``
below), never create a STOP file against any real campaign, never touch
the original campaign / original launch package / original final reviewer,
and run purely against SYNTHETIC materials plus this read-only checkout.

Self-contained layout (N2 rewrite): the module NO LONGER requires the five
PAL_RV05_N5_* environment variables or any WorkRoot session directory, and
there is NO module-level skip. Everything is resolved from THIS repository
checkout, and every fixture is generated inside pytest tmp_path:

  tool under test   tests/<this repo>/tools/soakctl/preflight-rv05.py
                    tests/<this repo>/tools/soakctl/run_final_review.py
  frozen tree       this repository root (tests/support/{soak_driver,
                    soak_audit,soak_closeout}.py), imported read-only
  python            sys.executable (the interpreter running pytest)
  rehearsal         a REAL driver-produced 10+-round all-passing campaign
  fixture           generated in tmp_path by the frozen driver itself
                    (same recipe as tests/test_soak_audit.py's ``control``),
                    then tightened via the make_synthetic_terminal recipe

The five environment variables remain as OPTIONAL overrides for binding
the tools under test to other copies (values used verbatim when set):

  PAL_RV05_N5_LAUNCH_DIR        dir with preflight-rv05.py (+ the launcher
                               slot: launch-rv05.ps1)
  PAL_RV05_N5_REVIEW_DIR        dir with run_final_review.py
  PAL_RV05_N5_FROZEN_TREE       repo root with tests/support/soak_*.py
  PAL_RV05_N5_PYTHON            python.exe used to run the tools
  (PAL_RV05_N5_REHEARSAL_DIR is no longer consulted - the fixture is
   always generated; the name is accepted and ignored for compatibility.)

Launcher integration slot (node N3): the launcher script launch-rv05.ps1
is NOT part of the N2 delivery. The launcher cases below look for it at
tools/soakctl/launch-rv05.ps1 (or $PAL_RV05_N5_LAUNCH_DIR/launch-rv05.ps1)
and skip INDIVIDUALLY - never at module level - with an explicit reason
while the slot is unfilled, so the other 30 cases always really execute.
On non-Windows (no powershell) the same cases skip with a platform reason.

Covered contracts (unchanged from the N5 original):

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
legacy preflight check set; the final reviewer reproduces the V3 four-path
validation (FAIL/PASS/UNKNOWN/NOT_RUN). One deliberate semantic delta vs
the WorkRoot original: the strict reviewer's LEGACY default binding pins
the historical frozen commit 2584f6f2, which can never match an arbitrary
checkout HEAD, so the PASS-path cases bind the ACTUAL checkout identity
via --identity-json (the parameterization surface this suite exists to
prove) instead of relying on a private frozen-tree coincidence.
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

REPO_ROOT = Path(__file__).resolve().parents[1]
SOAKCTL = REPO_ROOT / 'tools' / 'soakctl'


def _env_dir(name):
    value = os.environ.get(name)
    return Path(value).resolve() if value else None


LAUNCH_DIR = _env_dir('PAL_RV05_N5_LAUNCH_DIR')
REVIEW_DIR = _env_dir('PAL_RV05_N5_REVIEW_DIR')
FROZEN_TREE = _env_dir('PAL_RV05_N5_FROZEN_TREE') or REPO_ROOT
PY = Path(os.environ['PAL_RV05_N5_PYTHON']).resolve() \
    if os.environ.get('PAL_RV05_N5_PYTHON') else Path(sys.executable).resolve()

# The delivered tools are part of this repository: a missing file is a
# BROKEN DELIVERY and fails loudly (this module never skips wholesale).
PREFLIGHT_PY = (LAUNCH_DIR or SOAKCTL) / 'preflight-rv05.py'
REVIEW_PY = (REVIEW_DIR or SOAKCTL) / 'run_final_review.py'
DRIVER_FILE = FROZEN_TREE / 'tests' / 'support' / 'soak_driver.py'
AUDIT_FILE = FROZEN_TREE / 'tests' / 'support' / 'soak_audit.py'
CLOSEOUT_FILE = FROZEN_TREE / 'tests' / 'support' / 'soak_closeout.py'
for _required in (PREFLIGHT_PY, REVIEW_PY, DRIVER_FILE, AUDIT_FILE,
                  CLOSEOUT_FILE, PY):
    if not _required.exists():
        raise FileNotFoundError(
            f'self-contained N5 rejection tests: required delivery file '
            f'missing from this checkout: {_required} (broken delivery; '
            f'not skippable - check tools/soakctl/ and tests/support/)')

# N3 integration slot: absent until the launcher lands; the launcher cases
# skip individually (see require_launcher) instead of skipping the module.
LAUNCH_PS1 = (LAUNCH_DIR or SOAKCTL) / 'launch-rv05.ps1'

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

# synthetic process scenarios (same shape as tests/test_soak_audit.py)
ECHO = ('import sys,json,hashlib\n'
        'raw=sys.stdin.buffer.read()\n'
        'p=json.loads(raw.decode("utf-8"))\n'
        'print(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"],'
        '"stdin_sha256":hashlib.sha256(raw).hexdigest(),"stdin_bytes":len(raw)}))\n')
WRITER = ('import sys,json,os\n'
          'p=json.loads(sys.stdin.buffer.read())\n'
          'open(os.path.join(p["tmp_dir"],"junk.bin"),"wb").write(b"j"*64)\n'
          'sys.stdout.write(json.dumps({"echo_round":p["round"],'
          '"echo_seed":p["sub_seed"]}))\n')


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
    """Import the checkout's soak_driver BY FILE PATH (read-only; canonical
    hashing and the tiny control-campaign fixture only).

    Loading by path - never via a sys.path package import - is load-bearing:
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


def git_query(repo: Path, *args: str) -> str:
    proc = subprocess.run(['git', '--no-optional-locks', '-C', str(repo),
                           *args], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


_ACTUAL_IDENTITY = None


def actual_identity():
    """The CHECKOUT's own frozen identity (commit/tree/module hashes).

    The strict reviewer's legacy defaults pin the historical 2584f6f2
    freeze; from an arbitrary checkout that is always drift, so the
    PASS-path cases bind the actual identity explicitly - exactly the
    --identity-json surface the reviewer is parameterized with.
    """
    global _ACTUAL_IDENTITY
    if _ACTUAL_IDENTITY is None:
        head = git_query(FROZEN_TREE, 'rev-parse', 'HEAD')
        tree = git_query(FROZEN_TREE, 'rev-parse', head + '^{tree}')
        _ACTUAL_IDENTITY = {
            'commit': head, 'tree': tree,
            'modules': {
                'soak_driver.py': sha256_file(DRIVER_FILE),
                'soak_audit.py': sha256_file(AUDIT_FILE),
                'soak_closeout.py': sha256_file(CLOSEOUT_FILE)}}
    return _ACTUAL_IDENTITY


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

    # synthetic scenario manifest (GENERATED, not copied: self-contained)
    manifest = root / 'manifest.json'
    write_json(manifest, {'scenarios': [
        {'name': 'n5-echo', 'kind': 'process', 'code': ECHO},
        {'name': 'n5-writer', 'kind': 'process', 'code': WRITER},
        {'name': 'n5-unicode', 'kind': 'process',
         'code': 'import sys,json\nd=json.loads(sys.stdin.buffer.read())\n'
                 'print(json.dumps({"round":d["round"],"ok":True}))\n'}]})

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


@pytest.fixture(scope='session')
def control(tmp_path_factory):
    """A REAL tiny soak campaign produced by the frozen driver inside
    tmp_path (same recipe as tests/test_soak_audit.py's control): an
    all-passing 10+-round single-segment campaign closed complete. This is
    the self-contained replacement for the old RV-04 rehearsal fixture
    copy; the driver runs only synthetic scenarios and cleans up after
    itself. It is a test fixture build, not a soak launch."""
    drv = frozen_driver()
    base = tmp_path_factory.mktemp('n5ctl')
    target = base / 'synthetic_case.py'
    target.write_text('def test_one():\n    assert 1 + 1 == 2\n\n\n'
                      'def test_two():\n    assert "n5" != "production"\n\n\n'
                      'def test_three():\n    assert len("abc") == 3\n',
                      encoding='utf-8')
    manifest = base / 'manifest.json'
    write_json(manifest, {'scenarios': [
        {'name': 'n5-batch', 'kind': 'pytest', 'files': [str(target)]},
        {'name': 'n5-echo', 'kind': 'process', 'code': ECHO},
        {'name': 'n5-writer', 'kind': 'process', 'code': WRITER}]})
    config = {
        'master_seed': 2026092198, 'round_period_seconds': 0.25,
        'heartbeat_seconds': 0.1, 'checkpoint_seconds': 0.5,
        'progress_summary_seconds': 1.0, 'max_unobserved_gap_seconds': 900,
        'scenario_timeout_ms': 60000, 'cleanup_timeout_ms': 4000,
        'per_round_log_bytes': 1048576, 'max_stdout_bytes': 1048576,
        'max_stderr_bytes': 65536, 'max_evidence_bytes': 536870912,
        'max_repro_files': 100, 'minimum_volume_free_bytes': 0,
        'minimum_valid_rounds': 10, 'max_wall_seconds': 2.5, 'workers': 1,
        'candidate': {'label': 'n5-rejection-selftest'},
        'scenario_manifest': str(manifest),
    }
    config_path = base / 'control-config.json'
    write_json(config_path, config)
    campaign = base / 'campaign'
    driver = drv.SoakDriver(drv.SoakConfig.from_dict(config), campaign)
    assert driver.run() == drv.EXIT_OK
    close = drv._read_json(campaign / 'segments' / 'segment-000001'
                           / 'segment-close.json')
    assert close['reason'] == 'complete'
    assert close['rounds_total']['passed'] >= 10  # min_distinct_inputs=10
    return {'base': base, 'campaign': campaign, 'config': config_path,
            'manifest': manifest, 'config_doc': config}


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
# N3 INTEGRATION SLOT: these nine cases need launch-rv05.ps1, which is NOT
# part of the N2 delivery (the launcher belongs to node N3). They look for
# it at tools/soakctl/launch-rv05.ps1 (or $PAL_RV05_N5_LAUNCH_DIR) and skip
# INDIVIDUALLY with an explicit reason until N3 lands it - the module never
# skips wholesale, so the 30 self-contained cases above always really run.
# On non-Windows platforms (no powershell) the same cases skip with a
# platform reason: the launcher is a Windows powershell script.


def require_launcher():
    if not IS_WINDOWS or not HAVE_LAUNCHER:
        pytest.skip(f'launcher cases are Windows/powershell-only '
                    f'(os.name={os.name}, powershell='
                    f'{shutil.which(LAUNCHER) is not None})')
    if not LAUNCH_PS1.is_file():
        pytest.skip(f'launcher not integrated in this checkout yet (N3 '
                    f'slot): {LAUNCH_PS1} absent; point '
                    f'PAL_RV05_N5_LAUNCH_DIR at a directory containing '
                    f'launch-rv05.ps1 to exercise these cases')


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
    require_launcher()
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
    require_launcher()
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
    require_launcher()
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
    require_launcher()
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
    require_launcher()
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
    require_launcher()
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
    require_launcher()
    spec_path, spec = launcher_spec(synth, tmp_path)
    write_json(spec['paths']['receipt'], {
        'schema': 'pal-rv05-launch-receipt-v2', 'dry_run': False})
    proc = run_launcher(spec_path, dry_run=True)
    assert proc.returncode == 2  # NO_GO spec governs the dry-run exit
    assert b'NOTE (dry-run)' in proc.stdout


def test_go_spec_with_duplicate_receipt_never_launches(synth, tmp_path):
    """(c)+(d) strongest guard proof: even with an otherwise all-GO spec,
    the duplicate receipt refuses BEFORE any launch effect."""
    require_launcher()
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

def build_synth_campaign(dest, control, *, incomplete=False,
                         patch_campaign=None):
    """Replicate the make_synthetic_terminal recipe on the driver-produced
    control campaign: tightened config, consistent config_sha256 in
    campaign/segment headers, complete close receipt, stop removed.
    `patch_campaign` overlays extra campaign.json fields (schema tampering,
    hash drift, receipt_schema)."""
    dest = Path(dest)
    campaign = dest / 'campaign'
    shutil.copytree(control['campaign'], campaign)
    config = read_json(control['config'])
    config['minimum_valid_rounds'] = 5
    config['max_wall_seconds'] = control['config_doc']['max_wall_seconds']
    config['candidate'] = dict(config['candidate'])
    config['candidate']['purpose'] = 'n5 rejection-test synthetic state'
    config['scenario_manifest'] = str(control['manifest'])
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


def run_review(campaign, config, out, control, *, manifest=None,
               min_rounds=5, min_inputs=10, extra=(), timeout=300):
    args = [REVIEW_PY, 'review',
            '--campaign', campaign,
            '--config', config,
            '--manifest', manifest or control['manifest'],
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


def actual_identity_path(dest, control) -> Path:
    """--identity-json binding the CHECKOUT's actual identity (commit/tree/
    modules) plus the campaign's recorded python/manifest pins."""
    recorded = read_json(control['campaign'] / 'campaign.json')
    identity = dict(actual_identity())
    identity['python_sha256'] = recorded['python_sha256']
    identity['manifest_sha256'] = recorded['manifest_sha256']
    identity_path = Path(dest) / 'identity.json'
    write_json(identity_path, identity)
    return identity_path


# ---- four-path legacy regression (V3 val-a/b/b2/c parity) ----------------

def test_review_fail_path_real_caliber(control, tmp_path):
    """val-a parity: the honest control terminal state at 864/100000 ->
    honest FAIL, marker written."""
    campaign = tmp_path / 'val-a' / 'campaign'
    shutil.copytree(control['campaign'], campaign)
    code, report, _proc = run_review(
        campaign, control['config'], tmp_path / 'val-a-out', control,
        min_rounds=864, min_inputs=100000)
    assert_verdict(report, code, 'FAIL')
    assert (Path(tmp_path) / 'val-a-out' / 'FINAL_REVIEW_STRICT').is_file()


def test_review_pass_path_small_gates(control, tmp_path):
    """val-b parity: synthetic full-pass state with small gates -> PASS,
    marker written, no schema caps. Bound via --identity-json to the
    CHECKOUT identity (the distributable default pins the historical
    2584f6f2 freeze and can never match an arbitrary HEAD)."""
    campaign, config = build_synth_campaign(tmp_path / 'val-b', control)
    identity_path = actual_identity_path(tmp_path / 'val-b', control)
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'val-b-out', control,
        extra=['--identity-json', identity_path])
    assert_verdict(report, code, 'PASS')
    out = tmp_path / 'val-b-out'
    assert (out / 'FINAL_REVIEW_STRICT').is_file()
    assert report['schema_binding']['caps'] == []
    assert report['expected_binding']['source'] == 'identity-json/flags'


def test_review_unknown_path_snapshot_incomplete(control, tmp_path):
    """val-b2 parity: segment-close removed -> SNAPSHOT_INCOMPLETE ->
    UNKNOWN, never FAIL-as-corruption."""
    campaign, config = build_synth_campaign(tmp_path / 'val-b2', control,
                                             incomplete=True)
    identity_path = actual_identity_path(tmp_path / 'val-b2', control)
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'val-b2-out', control,
        extra=['--identity-json', identity_path])
    assert_verdict(report, code, 'UNKNOWN')
    assert report['audit_overall'] == 'SNAPSHOT_INCOMPLETE'


def test_review_not_run_path_live_pid(control, tmp_path):
    """val-c parity (synthetic): a live driver PID (this very test process)
    refuses the review: NOT_RUN, audit never executes, no marker."""
    campaign, config = build_synth_campaign(tmp_path / 'val-c', control)
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'val-c-out', control,
        extra=['--driver-pid', os.getpid()])
    assert_verdict(report, code, 'NOT_RUN')
    assert report['refuse_reason']['kind'] == 'still_running'
    assert report['audit_executed'] is False
    assert not (tmp_path / 'val-c-out' / 'FINAL_REVIEW_STRICT').exists()


# ---- (a) schema mixing -----------------------------------------------------

def test_review_legacy_reviewer_rejects_new_receipt_schema(control, tmp_path):
    """(a) CORE: a campaign declaring a NEW receipt schema cannot be
    adjudicated PASS by the legacy-bound reviewer — the audit itself stays
    PASS (it ignores the unknown field), the schema cap forces UNKNOWN."""
    campaign, config = build_synth_campaign(
        tmp_path / 'mix-legacy', control,
        patch_campaign={'receipt_schema': 'pal-soak-receipt-v2-synthetic'})
    code, report, _proc = run_review(campaign, config, tmp_path / 'mix-out',
                                     control)
    assert report['audit_overall'] == 'PASS'
    assert_verdict(report, code, 'UNKNOWN')
    reasons = ' | '.join(report['verdict_reasons'])
    assert 'receipt_schema' in reasons
    assert 'pal-soak-receipt-v2-synthetic' in reasons
    caps = report['schema_binding']['caps']
    assert caps and caps[0]['kind'] == 'receipt_schema'
    assert report['verdict'] != 'PASS'


def test_review_bound_schema_missing_on_campaign(control, tmp_path):
    """(a) a reviewer bound to the new receipt schema, run on a campaign
    that does not carry it, is capped at UNKNOWN."""
    campaign, config = build_synth_campaign(tmp_path / 'mix-bound', control)
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'mix-bound-out', control,
        extra=['--expect-receipt-schema', 'pal-soak-receipt-v2-synthetic'])
    assert_verdict(report, code, 'UNKNOWN')
    assert report['verdict'] != 'PASS'
    assert any('receipt_schema' in r for r in report['verdict_reasons'])


def test_review_campaign_schema_v2_is_audit_rejected(control, tmp_path):
    """(a) a campaign.json schema other than pal-soak-campaign-v1 is FAILED
    by the frozen audit's identity_schema check (rejection branch)."""
    campaign, config = build_synth_campaign(
        tmp_path / 'mix-cschema', control,
        patch_campaign={'schema': 'pal-soak-campaign-v2'})
    code, report, _proc = run_review(campaign, config, tmp_path / 'mix-c-out',
                                     control)
    assert_verdict(report, code, 'FAIL')
    audit_ids = {c['check'] for c in report['audit']['checks']}
    failed = {c['check'] for c in report['audit']['checks']
              if c['status'] == 'FAIL'}
    assert 'identity_schema' in audit_ids and 'identity_schema' in failed


# ---- (b) identity hash drift ----------------------------------------------

def test_review_recorded_manifest_drift_fails(control, tmp_path):
    """(b) campaign.json manifest_sha256 tampered -> identity FAIL."""
    campaign, config = build_synth_campaign(
        tmp_path / 'drift-manifest', control,
        patch_campaign={'manifest_sha256': 'd' * 64})
    code, report, _proc = run_review(campaign, config,
                                     tmp_path / 'drift-manifest-out', control)
    assert_verdict(report, code, 'FAIL')
    assert report['identity']['manifest_sha256']['status'] == 'FAIL'


def test_review_recorded_driver_drift_fails(control, tmp_path):
    """(b) campaign.json driver_sha256 tampered -> file rehash mismatch."""
    campaign, config = build_synth_campaign(
        tmp_path / 'drift-driver', control,
        patch_campaign={'driver_sha256': 'd' * 64})
    code, report, _proc = run_review(campaign, config,
                                     tmp_path / 'drift-driver-out', control)
    assert_verdict(report, code, 'FAIL')
    assert report['identity']['driver_sha256']['status'] == 'FAIL'


def test_review_toolchain_commit_drift_caps_unknown(control, tmp_path):
    """(b) expected commit drift vs the frozen tree -> toolchain UNKNOWN
    caps the verdict (everything else passes)."""
    campaign, config = build_synth_campaign(tmp_path / 'drift-toolchain',
                                            control)
    identity_path = actual_identity_path(tmp_path / 'drift-toolchain',
                                         control)
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'drift-toolchain-out', control,
        extra=['--identity-json', identity_path,
               '--expected-commit', 'e' * 40])
    assert_verdict(report, code, 'UNKNOWN')
    assert report['toolchain']['status'] == 'UNKNOWN'
    assert any(d['kind'] == 'commit' for d in report['toolchain']['drift'])


def test_review_bound_manifest_pin_drift_fails(control, tmp_path):
    """(b) explicitly bound manifest digest (audit identity_expected) vs the
    campaign's recorded value -> FAIL."""
    campaign, config = build_synth_campaign(tmp_path / 'drift-bound',
                                            control)
    code, report, _proc = run_review(
        campaign, config, tmp_path / 'drift-bound-out', control,
        extra=['--expected-manifest-sha256', 'e' * 64])
    assert_verdict(report, code, 'FAIL')
    checks = {c['check']: c['status'] for c in report['audit']['checks']}
    assert checks.get('identity_expected') == 'FAIL'


# ---- binding injection ------------------------------------------------------

def test_identity_json_complete_binding_passes(control, tmp_path):
    """Positive control: a complete identity-json pinning the checkout's
    true identity keeps the full-pass campaign PASS."""
    campaign, config = build_synth_campaign(tmp_path / 'bind-ok', control)
    recorded = read_json(campaign / 'campaign.json')
    identity = {
        'commit': actual_identity()['commit'],
        'tree': actual_identity()['tree'],
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
        campaign, config, tmp_path / 'bind-ok-out', control,
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
def test_identity_json_incomplete_is_invalid_invocation(control, tmp_path,
                                                        identity_doc):
    """An incomplete/malformed new-candidate binding is rejected (exit 5),
    never a silent legacy fallback."""
    campaign, config = build_synth_campaign(tmp_path / 'bind-bad', control)
    identity_path = tmp_path / 'bind-bad' / 'identity.json'
    write_json(identity_path, identity_doc)
    code, report, proc = run_review(
        campaign, config, tmp_path / 'bind-bad-out', control,
        extra=['--identity-json', identity_path])
    assert code == 5
    assert report is None
    assert 'identity-json' in (proc.stderr or '')
    assert not (tmp_path / 'bind-bad-out' / 'FINAL_REVIEW_STRICT').exists()


def test_identity_flag_bad_shape_is_invalid_invocation(control, tmp_path):
    """A malformed --expected-commit value is rejected (exit 5)."""
    campaign, config = build_synth_campaign(tmp_path / 'flag-bad', control)
    code, _report, _proc = run_review(
        campaign, config, tmp_path / 'flag-bad-out', control,
        extra=['--expected-commit', 'zzzz'])
    assert code == 5
