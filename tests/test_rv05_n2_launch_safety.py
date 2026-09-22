"""N2 launcher/preflight control-safety tests (PAL_RV05_CONTROL_SAFETY_20260922).

Node N2 owns tools/soakctl (launcher / preflight / templates / README).
This module pins the L5/L6 launcher-side and preflight-side contracts:

  - L6 (policy bypass): the launcher REFUSES policy-bypass invocations —
    powershell.exe -ExecutionPolicy Bypass/Unrestricted sets
    PSExecutionPolicyPreference, the launcher detects it and exits 2 with
    BLOCKED before anything else runs. The compliant form is
    -ExecutionPolicy RemoteSigned (or no switch under a host policy).
  - old-GO rejection (fresh-result binding): every launcher invocation
    generates a unique execution id, passes it to the preflight
    (--execution-id) and refuses any report not bound to THIS execution
    (absent/wrong execution id). A stale GO residue — including a
    "preflight" that replays a canned GO report — can never authorize a
    launch. A pre-existing report file is deleted before the preflight
    runs, so a stub that writes nothing cannot smuggle a residue through.
  - execution binding (positive control): the compliant dry-run path on a
    synthetic all-GO environment prints EXECUTION BINDING: PASS with the
    fresh execution id and reaches the dry-run end without claim, receipt
    or campaign creation.
  - compliant control: the real read-only dry-run path (RemoteSigned
    invocation, NO_GO spec) keeps its documented exit-2 semantics.
  - spec hygiene: a formal spec carrying a synthetic hook (--now-utc) or
    a policy-bypass token is refused.
  - preflight argv strictness: unknown flags and abbreviated flags are
    hard usage errors (allow_abbrev=False); the report carries execution
    provenance (execution id echo, spec file digest, argv echo).

Launcher cases are Windows/powershell-only and skip individually
elsewhere (the launcher is a Windows PowerShell script). No case ever
performs a real launch: the strongest reachable state is a compliant
dry run or a refusal. Constants: official_cases_run=0,
sandbox_started=false, activation_authorized=false.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOAKCTL = REPO_ROOT / 'tools' / 'soakctl'
PREFLIGHT_PY = SOAKCTL / 'preflight-rv05.py'
LAUNCH_PS1 = SOAKCTL / 'launch-rv05.ps1'
DRIVER_FILE = REPO_ROOT / 'tests' / 'support' / 'soak_driver.py'
AUDIT_FILE = REPO_ROOT / 'tests' / 'support' / 'soak_audit.py'
CLOSEOUT_FILE = REPO_ROOT / 'tests' / 'support' / 'soak_closeout.py'
PY = Path(sys.executable).resolve()
LAUNCHER = 'powershell'
IS_WINDOWS = os.name == 'nt'
HAVE_LAUNCHER = IS_WINDOWS and shutil.which(LAUNCHER) is not None

for _required in (PREFLIGHT_PY, LAUNCH_PS1, DRIVER_FILE, AUDIT_FILE,
                  CLOSEOUT_FILE, PY):
    if not _required.exists():
        raise FileNotFoundError(
            f'N2 launch-safety tests: required delivery file missing from '
            f'this checkout: {_required} (broken delivery; not skippable)')

ECHO = ('import sys,json,hashlib\n'
        'raw=sys.stdin.buffer.read()\n'
        'p=json.loads(raw.decode("utf-8"))\n'
        'print(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"],'
        '"stdin_sha256":hashlib.sha256(raw).hexdigest(),"stdin_bytes":len(raw)}))\n')

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
    hashing only), exactly like the preflight under test does."""
    global _FROZEN_DRV
    if _FROZEN_DRV is None:
        spec = importlib.util.spec_from_file_location(
            'n2_safety_soak_driver', DRIVER_FILE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _FROZEN_DRV = module
    return _FROZEN_DRV


# --------------------------------------------------------------------------
# synthetic all-GO environment (self-contained; same recipe as the N5
# rejection suite's synth fixture, minimal for the N2 surface)
# --------------------------------------------------------------------------

@pytest.fixture(scope='module')
def synth(tmp_path_factory):
    root = tmp_path_factory.mktemp('n2safety')

    repo = root / 'cand-repo'
    repo.mkdir()
    (repo / 'README.md').write_text('synthetic candidate repo\n',
                                    encoding='utf-8')
    for args in (['init', '-q', str(repo)],
                 ['-C', str(repo), '-c', 'user.name=n2-test',
                  '-c', 'user.email=n2@test.invalid', 'add', '-A'],
                 ['-C', str(repo), '-c', 'user.name=n2-test',
                  '-c', 'user.email=n2@test.invalid',
                  'commit', '-q', '-m', 'synthetic candidate']):
        subprocess.run(['git', *args], check=True, capture_output=True,
                       timeout=60)
    head = subprocess.run(
        ['git', '-C', str(repo), 'rev-parse', 'HEAD'],
        capture_output=True, text=True, check=True, timeout=60).stdout.strip()

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
    write_json(manifest, {'scenarios': [
        {'name': 'n2-echo', 'kind': 'process', 'code': ECHO}]})

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
        'candidate': {'name': 'n2-synthetic',
                      'commit': head,
                      'purpose': 'n2 launch-safety synthetic'},
        'scenario_manifest': str(manifest),
        'min_distinct_inputs': 10,
    }
    config_path = root / 'config-synth.json'
    write_json(config_path, config)
    return {
        'root': root, 'repo': repo, 'head': head,
        'orig': orig, 'v3_state': v3_state,
        'config': config_path,
        'driver_sha': sha256_file(DRIVER_FILE),
        'audit_sha': sha256_file(AUDIT_FILE),
        'closeout_sha': sha256_file(CLOSEOUT_FILE),
        'python_sha': sha256_file(PY),
        'planned': root / 'planned',
    }


def make_spec(env, tmp_path, *, preflight=None, window=None, extra=None,
              name='spec.json'):
    if window is None:
        window = ('2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z')
    spec = {
        'schema': 'pal-rv05-launch-spec-v1',
        'task_id': 'PAL_RV05_CONTROL_SAFETY_20260922_N2_TEST',
        'run_label': 'n2-launch-safety',
        'frozen': {
            'commit': env['head'],
            'tree': '0' * 40,
            'base_commit': '2584f6f2d86ce19e7e2dab6bea6a27a013587753',
            'driver_sha256': env['driver_sha'],
            'audit_sha256': env['audit_sha'],
            'closeout_sha256': env['closeout_sha'],
            'python_sha256': env['python_sha'],
        },
        'paths': {
            'v3_root': str(env['root']),
            'src_root': str(env['repo']),
            'python_exe': str(PY),
            'driver_py': str(DRIVER_FILE),
            'audit_py': str(AUDIT_FILE),
            'closeout_py': str(CLOSEOUT_FILE),
            'config': str(env['config']),
            'manifest': str(env['config'].parent / 'manifest.json'),
            'preflight': str(preflight or PREFLIGHT_PY),
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
    if extra:
        spec.update(extra)
    spec_path = tmp_path / name
    write_json(spec_path, spec)
    return spec_path, spec


# --------------------------------------------------------------------------
# preflight-side: argv strictness + execution provenance
# --------------------------------------------------------------------------

def run_preflight(args, timeout=240):
    return subprocess.run(
        [str(PY), '-I', '-S', '-B', *[str(a) for a in args]],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=timeout)


def test_preflight_report_execution_provenance(synth, tmp_path):
    """The report carries THIS run's execution binding: the --execution-id
    echo, the spec FILE digest, and the verbatim argv."""
    spec_path, _ = make_spec(synth, tmp_path)
    out = tmp_path / 'report.json'
    exec_id = 'n2execid00000001'
    proc = run_preflight([PREFLIGHT_PY, '--spec', spec_path,
                          '--json-out', out, '--execution-id', exec_id])
    assert proc.returncode == 0, proc.stdout
    report = read_json(out)
    assert report['overall'] == 'GO', report['unmet_ids']
    execution = report['execution']
    assert execution['execution_id'] == exec_id
    assert execution['spec'] == str(spec_path.resolve())
    assert execution['spec_sha256'] == sha256_file(spec_path)
    assert execution['json_out'] == str(out)
    assert execution['argv'] == ['--spec', str(spec_path), '--json-out',
                                 str(out), '--execution-id', exec_id]
    # additive provenance only: the legacy check ids are unchanged
    assert [c['id'] for c in report['checks']] == LEGACY_PREFLIGHT_IDS


def test_preflight_execution_id_optional(synth, tmp_path):
    """Legacy callers without --execution-id still work (id echoed null);
    a malformed id is a hard usage error."""
    spec_path, _ = make_spec(synth, tmp_path)
    out = tmp_path / 'report.json'
    proc = run_preflight([PREFLIGHT_PY, '--spec', spec_path,
                          '--json-out', out])
    assert proc.returncode == 0
    assert read_json(out)['execution']['execution_id'] is None
    bad = run_preflight([PREFLIGHT_PY, '--spec', spec_path,
                         '--json-out', str(tmp_path / 'r2.json'),
                         '--execution-id', 'x'])
    assert bad.returncode != 0
    assert 'execution-id' in bad.stderr


def test_preflight_rejects_unknown_argument(synth, tmp_path):
    """A synthetic clock hook smuggled at the preflight is a hard usage
    error — the argument surface is exactly the three known flags."""
    spec_path, _ = make_spec(synth, tmp_path)
    proc = run_preflight([PREFLIGHT_PY, '--spec', spec_path,
                          '--now-utc', '2026-09-22T00:00:00Z'])
    assert proc.returncode != 0
    assert 'unrecognized arguments' in proc.stderr


def test_preflight_rejects_abbreviated_argument(synth, tmp_path):
    """Abbreviated flags (--exec) must not resolve to --execution-id."""
    spec_path, _ = make_spec(synth, tmp_path)
    proc = run_preflight([PREFLIGHT_PY, '--spec', spec_path,
                          '--exec', 'abcdefghijklmnop'])
    assert proc.returncode != 0


# --------------------------------------------------------------------------
# launcher-side: policy bypass (L6), spec hygiene
# --------------------------------------------------------------------------

def require_launcher():
    if not IS_WINDOWS or not HAVE_LAUNCHER:
        pytest.skip(f'launcher cases are Windows/powershell-only '
                    f'(os.name={os.name}, powershell='
                    f'{shutil.which(LAUNCHER) is not None})')
    if not LAUNCH_PS1.is_file():
        pytest.skip(f'launcher missing from this checkout: {LAUNCH_PS1}')


def run_launcher(spec_path, *, dry_run=False, policy='RemoteSigned',
                 timeout=240):
    """Invoke the launcher with an EXPLICIT execution policy. The default is
    the compliant RemoteSigned form; 'none' omits the switch entirely; the
    L6 rejection cases pass the forbidden Bypass/Unrestricted forms."""
    args = [LAUNCHER, '-NoProfile']
    if policy != 'none':
        args += ['-ExecutionPolicy', policy]
    args += ['-File', str(LAUNCH_PS1), '-SpecPath', str(spec_path)]
    if dry_run:
        args.append('-DryRun')
    return subprocess.run(args, capture_output=True, timeout=timeout)


@pytest.mark.parametrize('policy', ['Bypass', 'Unrestricted'])
def test_launcher_rejects_policy_bypass_invocation(synth, tmp_path, policy):
    """L6: -ExecutionPolicy Bypass (and the Unrestricted equivalent) is
    BLOCKED before anything runs — no guard read, no preflight, no report,
    no claim, no receipt, no campaign."""
    require_launcher()
    spec_path, spec = make_spec(synth, tmp_path)
    proc = run_launcher(spec_path, dry_run=True, policy=policy)
    assert proc.returncode == 2, proc.stdout
    assert b'BLOCKED: policy-bypass invocation rejected' in proc.stdout
    # blocked before any other work: nothing was read or written
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['receipt']).exists()
    assert not Path(spec['paths']['preflight_last']).exists()
    assert not Path(spec['paths']['planned_campaign_root']).exists()


def test_launcher_policy_preference_unset_is_compliant(synth, tmp_path):
    """Compliant control: invoking WITHOUT -ExecutionPolicy (host-configured
    policy governs) reaches the normal fail-closed preflight path on a NO_GO
    spec — it is not treated as a bypass."""
    require_launcher()
    spec_path, spec = make_spec(synth, tmp_path,
                                window=('2020-01-01T00:00:00Z',
                                        '2020-01-02T00:00:00Z'),
                                name='nogo-spec.json')
    proc = run_launcher(spec_path, dry_run=True, policy='none')
    assert proc.returncode == 2  # NO_GO spec: dry run reports it
    assert b'BLOCKED' not in proc.stdout
    assert b'DRY RUN' in proc.stdout
    assert not Path(spec['paths']['claim']).exists()
    assert Path(spec['paths']['preflight_last']).exists()


def test_launcher_remotesigned_compliant_dry_run_nogo(synth, tmp_path):
    """Compliant control: -ExecutionPolicy RemoteSigned dry run on a NO_GO
    spec keeps the documented dry-run semantics (exit 2, plan printed,
    read-only)."""
    require_launcher()
    spec_path, spec = make_spec(synth, tmp_path,
                                window=('2020-01-01T00:00:00Z',
                                        '2020-01-02T00:00:00Z'),
                                name='nogo-spec.json')
    proc = run_launcher(spec_path, dry_run=True, policy='RemoteSigned')
    assert proc.returncode == 2
    assert b'BLOCKED' not in proc.stdout
    assert b'DRY RUN' in proc.stdout
    assert b'LAUNCH CMD' in proc.stdout
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['receipt']).exists()
    assert not Path(spec['paths']['planned_campaign_root']).exists()
    assert Path(spec['paths']['preflight_last']).exists()


@pytest.mark.parametrize('token', ['--now-utc', '--stop-after'])
def test_launcher_refuses_spec_with_synthetic_hook(synth, tmp_path, token):
    """A FORMAL spec carrying a synthetic test hook is refused before any
    preflight run (formal configurations never carry test hooks)."""
    require_launcher()
    spec_path, spec = make_spec(synth, tmp_path,
                                extra={'notes': [f'linker hook {token} X']},
                                name=f'hook-spec.json')
    proc = run_launcher(spec_path, dry_run=True)
    assert proc.returncode == 2, proc.stdout
    assert b'REFUSED: spec carries a forbidden synthetic-hook/policy-bypass token' \
        in proc.stdout
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['preflight_last']).exists()


def test_launcher_refuses_spec_with_bypass_token(synth, tmp_path):
    """A spec embedding the old Bypass invocation example is refused too."""
    require_launcher()
    spec_path, spec = make_spec(
        synth, tmp_path,
        extra={'notes': ['run via powershell -ExecutionPolicy Bypass -File']},
        name='bypass-spec.json')
    proc = run_launcher(spec_path, dry_run=True)
    assert proc.returncode == 2, proc.stdout
    assert b'REFUSED: spec carries a forbidden synthetic-hook/policy-bypass token' \
        in proc.stdout


# --------------------------------------------------------------------------
# launcher-side: old-GO residue rejection (fresh-result binding)
# --------------------------------------------------------------------------

STUB_SILENT = ('import sys\n'
               'sys.exit(0)\n')


def test_launcher_deletes_stale_report_and_refuses_without_fresh_one(
        synth, tmp_path):
    """A planted OLD GO report is deleted before the preflight runs; a
    "preflight" that produces nothing cannot smuggle the residue through —
    the launcher refuses with NO REPORT."""
    require_launcher()
    stub = tmp_path / 'stub-silent.py'
    stub.write_text(STUB_SILENT, encoding='utf-8')
    spec_path, spec = make_spec(synth, tmp_path, preflight=stub,
                                name='stub-silent-spec.json')
    planted = tmp_path / 'planted-go.json'
    write_json(planted, {'schema': 'pal-rv05-preflight-v1',
                         'overall': 'GO', 'unmet_ids': [], 'checks': []})
    shutil.copyfile(planted, spec['paths']['preflight_last'])
    proc = run_launcher(spec_path, dry_run=True)
    assert proc.returncode == 2, proc.stdout
    assert b'PREFLIGHT_FAILED: no preflight report produced' in proc.stdout
    # the planted residue is GONE (deleted before the stub ran)
    assert not Path(spec['paths']['preflight_last']).exists()
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['receipt']).exists()


def test_launcher_rejects_replayed_go_report_not_bound_to_this_run(
        synth, tmp_path):
    """The strongest old-GO attack: a preflight stub REPLAYS a canned GO
    report with a fresh timestamp, the correct spec digest and its own
    well-formed execution id — everything matches except THIS invocation's
    execution id. The launcher must refuse: an unbound GO never authorizes
    anything."""
    require_launcher()
    stub = tmp_path / 'stub-replay.py'
    stub.write_text('pass\n', encoding='utf-8')
    spec_path, spec = make_spec(synth, tmp_path, preflight=stub,
                                name='replay-spec.json')
    canned = tmp_path / 'canned-go.json'
    write_json(canned, {
        'schema': 'pal-rv05-preflight-v1', 'overall': 'GO',
        'unmet_ids': [], 'checks': [],
        'execution': {
            # a WELL-FORMED id that simply is not this run's id
            'execution_id': 'ffffffffffffffffffffffffffffffff',
            # the CORRECT digest of the spec the launcher will read
            'spec_sha256': sha256_file(spec_path),
            'argv': []}})
    stub.write_text(
        'import json, sys, time\n'
        f'canned = r"{canned}"\n'
        'argv = sys.argv[1:]\n'
        'out = argv[argv.index("--json-out") + 1]\n'
        'doc = json.loads(open(canned, "r", encoding="utf-8").read())\n'
        'doc["generated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",\n'
        '                                        time.gmtime())\n'
        'open(out, "w", encoding="utf-8").write(json.dumps(doc, indent=2))\n'
        'sys.exit(0)\n', encoding='utf-8')
    proc = run_launcher(spec_path, dry_run=True)
    assert proc.returncode == 2, proc.stdout
    assert b'REFUSED: preflight report is not bound to this execution' \
        in proc.stdout
    assert b'expected execution_id=' in proc.stdout
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['receipt']).exists()
    assert not Path(spec['paths']['planned_campaign_root']).exists()
    # the replayed report itself stays on disk as evidence of the refusal;
    # it WAS a GO-shaped report with a foreign execution id
    replayed = read_json(spec['paths']['preflight_last'])
    assert replayed['overall'] == 'GO'
    assert replayed['execution']['execution_id'] == \
        'ffffffffffffffffffffffffffffffff'


def test_launcher_rejects_report_without_execution_block(synth, tmp_path):
    """An old-format report (no execution block at all, e.g. written by a
    preflight predating the binding contract) is refused even when fresh."""
    require_launcher()
    stub = tmp_path / 'stub-legacy.py'
    spec_path, spec = make_spec(synth, tmp_path, name='legacy-spec.json')
    stub.write_text(
        'import json, sys, time\n'
        'argv = sys.argv[1:]\n'
        'out = argv[argv.index("--json-out") + 1]\n'
        'doc = {"schema": "pal-rv05-preflight-v1", "overall": "GO",\n'
        '       "unmet_ids": [], "checks": [],\n'
        '       "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",\n'
        '                                      time.gmtime())}\n'
        'open(out, "w", encoding="utf-8").write(json.dumps(doc, indent=2))\n'
        'sys.exit(0)\n', encoding='utf-8')
    spec2_path, spec2 = make_spec(synth, tmp_path, preflight=stub,
                                  name='legacy-spec-stub.json')
    proc = run_launcher(spec2_path, dry_run=True)
    assert proc.returncode == 2, proc.stdout
    assert b'REFUSED: preflight report is not bound to this execution' \
        in proc.stdout
    assert b'<absent>' in proc.stdout
    assert not Path(spec2['paths']['claim']).exists()


# --------------------------------------------------------------------------
# compliant positive control end to end (read-only; dry run only)
# --------------------------------------------------------------------------

def test_launcher_compliant_dry_run_go_passes_execution_binding(
        synth, tmp_path):
    """Compliant control on an all-GO synthetic environment: the dry run
    verifies THIS execution's binding (fresh id, spec digest), reports GO,
    previews the launch-boundary rechecks green and creates nothing."""
    require_launcher()
    spec_path, spec = make_spec(synth, tmp_path, name='go-spec.json')
    proc = run_launcher(spec_path, dry_run=True, policy='RemoteSigned')
    assert proc.returncode == 0, proc.stdout
    assert b'BLOCKED' not in proc.stdout
    assert b'EXECUTION BINDING: PASS' in proc.stdout
    assert b'PREFLIGHT_VERDICT: GO' in proc.stdout
    assert b'spec_drift=PASS pins=PASS window=PASS' in proc.stdout
    assert b'=== DRY RUN END ===' in proc.stdout
    # read-only: no claim, no receipt, no campaign
    assert not Path(spec['paths']['claim']).exists()
    assert not Path(spec['paths']['receipt']).exists()
    assert not Path(spec['paths']['planned_campaign_root']).exists()
    # the report consumed IS bound to a fresh per-run execution id
    report = read_json(spec['paths']['preflight_last'])
    assert report['execution']['execution_id']
    assert len(report['execution']['execution_id']) == 32
    assert report['execution']['spec_sha256'] == sha256_file(spec_path)


def test_launcher_usage_without_spec_path():
    """Invalid invocation (missing -SpecPath) prints USAGE and exits 2 under
    the compliant form."""
    require_launcher()
    proc = subprocess.run(
        [LAUNCHER, '-NoProfile', '-ExecutionPolicy', 'RemoteSigned',
         '-File', str(LAUNCH_PS1)], capture_output=True, timeout=120)
    assert proc.returncode == 2
    assert b'USAGE' in proc.stdout
