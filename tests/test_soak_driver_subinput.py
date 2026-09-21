"""Wiring tests for kind='subinput' rounds in the test-only soak driver.

RV05 N0 wave 2 (W7): the ok-compute scenario's per-sub-input receipt wiring
(promotion, pointer registration, failure words, budget enforcement, cleanup
protection) is exercised end-to-end with SYNTHETIC contract receipts built
here by hand per docs/contracts/soak-subinput-receipt-v1.md. The real N1
generator module is NOT imported (it lives on another branch); the missing
generator is itself a tested error path. All child processes are synthetic
and run through the project's owned-process layer; no credentials,
databases, network, or real model/market/order access anywhere.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tests.support import soak_driver as drv

SEED = 2026092104


# ---------- synthetic contract receipt builders ----------

def receipt_doc(round_no, segment_no, sub_seed, manifest_sha, scenario='ok-compute',
                family='capture-codec', rows=None, counts=None):
    """A contract-valid v1 receipt document (decision 2.2 field set)."""
    if rows is None:  # lowercase hex WITH letters, so case/digit mutations bite
        rows = [[format(0xa1b2c3 + index, '064x'), format(0x3f4e5d + index, '064x')]
                for index in range(3)]
    if counts is None:
        counts = {key: len(rows) for key in drv.SUBINPUT_COUNT_KEYS}
    return {
        'schema': drv.SUBINPUT_SCHEMA,
        'family': family,
        'entry': f'{family}/run',
        'normalize_rule': f'norm:{family}-1',
        'candidate': json.dumps({'label': 'rv05-n0-w2'}, sort_keys=True,
                                separators=(',', ':')),
        'manifest_sha256': manifest_sha,
        'generator_sha256': 'a' * 64,
        'contract_sha256': 'b' * 64,
        'round': round_no, 'segment': segment_no, 'sub_seed': sub_seed,
        'scenario': scenario,
        'index_origin': 0,
        'oracle': f'producer:{family}-1',
        'counts': counts,
        'rows': rows,
    }


def receipt_bytes(doc) -> bytes:
    """Contract 2.1 serialization: sorted compact JSON plus one newline."""
    return json.dumps(doc, sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n'


def summary_entry(doc, raw: bytes) -> dict:
    """One family summary item exactly as the real generator writes it
    (ERRATA-001 Q3): files list + family-level row/count aggregates."""
    return {'family': doc['family'], 'entry': doc['entry'],
            'files': [{'file': f"subinputs-{doc['family']}.json",
                       'rows': len(doc['rows']),
                       'sha256': hashlib.sha256(raw).hexdigest(),
                       'bytes': len(raw)}],
            'rows': len(doc['rows']), 'counts': dict(doc['counts'])}


def summary_stdout(round_no, sub_seed, entries) -> bytes:
    payload = {'echo_round': round_no, 'echo_seed': sub_seed, 'echo_scenario': 'ok-compute',
               'ok': True, 'subinput_families': entries, 'receipt_bytes_total': 0}
    return json.dumps(payload).encode('utf-8') + b'\n'


class FakeResult:
    def __init__(self, stdout, stderr_bytes=0):
        self.stdout = stdout
        self.stderr_bytes = stderr_bytes


# ---------- manifest / config / campaign helpers ----------

def subinput_manifest(tmp_path, *, module='tests.support.soak_okcompute',
                      families=('capture-codec',), planned_rows=2048,
                      extra_scenarios=()):
    scenarios = [{'name': 'ok-compute', 'kind': 'subinput', 'module': module,
                  'planned_rows': planned_rows, 'families': list(families)}]
    scenarios.extend(extra_scenarios)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'scenarios': scenarios}), encoding='utf-8')
    return path


def subinput_config(manifest, **overrides):
    config = {
        'master_seed': SEED,
        'round_period_seconds': 0.3,
        'heartbeat_seconds': 0.15,
        'checkpoint_seconds': 0.3,
        'progress_summary_seconds': 1.0,
        'max_unobserved_gap_seconds': 900,
        'scenario_timeout_ms': 20000,
        'cleanup_timeout_ms': 4000,
        'per_round_log_bytes': 1048576,
        'max_stdout_bytes': 1048576,
        'max_stderr_bytes': 65536,
        'max_evidence_bytes': 536870912,
        'max_repro_files': 100,
        'minimum_volume_free_bytes': 0,
        'minimum_valid_rounds': 1,
        'workers': 1,
        'candidate': {'label': 'rv05-n0-w2'},
        'scenario_manifest': str(manifest),
    }
    config.update(overrides)
    return config


def make_fake_child(monkeypatch, driver, plan):
    """Replace the supervision call with a synthetic generator child.

    ``plan(payload_dict, round_tmp, manifest_sha) -> (files, stdout)`` writes
    receipt files into the round's tmp exactly like the real child (cwd) and
    returns the child's stdout summary bytes.
    """
    def fake(spec, stdin=None, allow_process_start=False, stop=None):
        payload = json.loads(stdin.decode('utf-8'))
        files, stdout = plan(payload, Path(spec.cwd), driver.manifest_sha)
        for name, data in files.items():
            (Path(spec.cwd) / name).write_bytes(data)
        return FakeResult(stdout)
    monkeypatch.setattr(drv, 'run_research_process', fake)


def valid_plan(families=('capture-codec',)):
    def plan(payload, round_tmp, manifest_sha):
        files, entries = {}, []
        for family in families:
            doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                              manifest_sha, scenario=payload['scenario'], family=family)
            raw = receipt_bytes(doc)
            files[f'subinputs-{family}.json'] = raw
            entries.append(summary_entry(doc, raw))
        return files, summary_stdout(payload['round'], payload['sub_seed'], entries)
    return plan


def run_campaign(monkeypatch, tmp_path, plan, *, families=('capture-codec',),
                 rounds=1, **overrides):
    manifest = subinput_manifest(tmp_path, families=families)
    config = drv.SoakConfig.from_dict(
        subinput_config(manifest, minimum_valid_rounds=rounds, **overrides))
    campaign = tmp_path / 'campaign'
    driver = drv.SoakDriver(config, campaign)
    if plan is not None:
        make_fake_child(monkeypatch, driver, plan)
    code = driver.run()
    return code, campaign, driver


def round_record(campaign, round_no, segment=1):
    return drv._read_json(campaign / 'segments' / f'segment-{segment:06d}' / 'rounds'
                          / f'round-{round_no:09d}' / 'round.json')


def expected_payload(round_no, scenario, campaign, segment=1):
    round_dir = (campaign / 'segments' / f'segment-{segment:06d}' / 'rounds'
                 / f'round-{round_no:09d}')
    return drv._canonical({'round': round_no, 'segment': segment,
                           'sub_seed': drv.derive_sub_seed(SEED, round_no),
                           'scenario': scenario,
                           'tmp_dir': str(round_dir / 'tmp')}) + b'\n'


def patch_bounded_open(monkeypatch):
    """Record the size of every binary read per file (R6 bounded-read proof)."""
    reads: dict[str, list[int]] = {}
    real_open = open

    def counting_open(file, mode='r', *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if 'r' in mode and 'b' in mode:
            key = str(Path(file).resolve())
            real_read = handle.read

            def counting_read(size=-1):
                data = real_read(size)
                reads.setdefault(key, []).append(len(data))
                return data
            handle.read = counting_read
        return handle

    monkeypatch.setattr(drv, 'open', counting_open, raising=False)
    return reads


# ---------- W1: manifest loading ----------

def test_subinput_manifest_entry_loads_with_wiring_identity(tmp_path):
    manifest = subinput_manifest(tmp_path, families=['uncapped-authz-codec', 'capture-codec'],
                                 planned_rows=2048)
    scenarios, identity_sha = drv._load_manifest(manifest)
    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.kind == 'subinput' and scenario.module == 'tests.support.soak_okcompute'
    assert scenario.planned_rows == 2048
    assert scenario.families == ('capture-codec', 'uncapped-authz-codec')  # stored sorted
    expected_identity = [{'name': 'ok-compute', 'kind': 'subinput', 'code': '', 'files': [],
                          'module': 'tests.support.soak_okcompute', 'planned_rows': 2048,
                          'families': ['capture-codec', 'uncapped-authz-codec']}]
    assert identity_sha == drv._sha256_bytes(drv._canonical(expected_identity))


def test_manifest_without_subinput_keeps_legacy_identity_formula(tmp_path):
    case = tmp_path / 'case.py'
    case.write_text('def test_one():\n    pass\n', encoding='utf-8')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'scenarios': [
        {'name': 'echo', 'kind': 'process', 'code': 'pass'},
        {'name': 'batch', 'kind': 'pytest', 'files': [str(case)]}]}), encoding='utf-8')
    _, identity_sha = drv._load_manifest(manifest)
    # Exactly the pre-subinput formula: name/kind/code/files only, name-sorted.
    expected = [{'name': 'batch', 'kind': 'pytest', 'code': '', 'files': [str(case)]},
                {'name': 'echo', 'kind': 'process', 'code': 'pass', 'files': []}]
    assert identity_sha == drv._sha256_bytes(drv._canonical(expected))


@pytest.mark.parametrize('entry', [
    {'kind': 'subinput'},                                   # module missing
    {'kind': 'subinput', 'module': ''},                     # empty module
    {'kind': 'subinput', 'module': 'a..b'},                 # bad dots
    {'kind': 'subinput', 'module': 'a/b'},                  # path, not import name
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': 0},
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': 4097},
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': '2048'},
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': True},
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': 2048},                # families missing
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': 2048, 'families': []},
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': 2048, 'families': ['nope']},
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': 2048,
     'families': ['capture-codec', 'capture-codec']},                           # duplicate
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': 2048, 'families': 'capture-codec'},
    {'kind': 'subinput', 'module': 'mod', 'planned_rows': 2048, 'families': [7]},
])
def test_subinput_manifest_rejects_invalid_entries(tmp_path, entry):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'scenarios': [
        {'name': 'ok-compute', **entry}]}), encoding='utf-8')
    with pytest.raises(drv.SoakConfigError):
        drv._load_manifest(manifest)


# ---------- W2: scenario spec / sanitized environment ----------

def test_subinput_scenario_spec_matches_pytest_sanitization_level(tmp_path):
    manifest = subinput_manifest(tmp_path)
    config = drv.SoakConfig.from_dict(subinput_config(manifest))
    driver = drv.SoakDriver(config, tmp_path / 'campaign')
    round_tmp = tmp_path / 'roundtmp'
    round_tmp.mkdir()
    spec = driver._scenario_spec(driver.scenarios[0], round_tmp)
    # ERRATA-001 Q3: argv identity channel — candidate canonical JSON,
    # manifest sha, repository contract-copy sha, planned rows, one --family
    # flag per enabled family in stored (sorted) order.
    assert spec.argv == (driver.python, '-S', '-m', 'tests.support.soak_okcompute',
                         '--candidate', json.dumps(config.candidate, sort_keys=True,
                                                   separators=(',', ':')),
                         '--manifest-sha256', driver.manifest_sha,
                         '--contract-sha256', driver._subinput_contract_sha256(),
                         '--rows', '2048', '--family', 'capture-codec')
    assert spec.cwd == str(round_tmp)
    expected_keys = {'PYTHONPATH', 'PYTHONUTF8', 'PYTHONDONTWRITEBYTECODE'}
    if os.name == 'nt':
        expected_keys.add('SystemRoot')
    assert set(key for key, _ in spec.environment) == expected_keys  # no ambient leak
    pythonpath = dict(spec.environment)['PYTHONPATH']
    assert pythonpath.split(os.pathsep)[:1] == [str(drv._REPO_ROOT)]  # repo root first
    assert pythonpath.split(os.pathsep)[1] == drv._pinned_purelib()   # pinned deps second
    # Same sanitization level as the pytest kind (which only adds its own flag).
    case = tmp_path / 'case.py'
    case.write_text('def test_one():\n    pass\n', encoding='utf-8')
    pytest_spec = driver._scenario_spec(drv.SoakScenario('batch', 'pytest', files=(str(case),)),
                                        tmp_path / 'other')
    pytest_keys = {key for key, _ in pytest_spec.environment}
    assert expected_keys <= pytest_keys and pytest_keys - expected_keys == {'PYTEST_DISABLE_PLUGIN_AUTOLOAD'}


def test_subinput_contract_sha_is_the_repository_copy(tmp_path):
    """The argv --contract-sha256 value is the hashed docs/contracts copy —
    the same bytes the audit cross-checks (contract_copy_verified)."""
    manifest = subinput_manifest(tmp_path)
    config = drv.SoakConfig.from_dict(subinput_config(manifest))
    driver = drv.SoakDriver(config, tmp_path / 'campaign')
    expected = hashlib.sha256(drv._SUBINPUT_CONTRACT_COPY.read_bytes()).hexdigest()
    assert driver._subinput_contract_sha256() == expected
    assert drv._HEX64_RE.fullmatch(expected)


SYNTH_GENERATOR = '''import json, os, sys
data = json.loads(sys.stdin.buffer.read())
with open('child-env.json', 'w') as handle:
    handle.write(json.dumps({'keys': sorted(os.environ), 'argv': list(sys.argv),
                             'executable': sys.executable}))
print(json.dumps({'echo_round': data['round'], 'echo_seed': data['sub_seed']}))
'''


def test_subinput_child_spawns_as_module_with_exact_env_in_real_subprocess(tmp_path, monkeypatch):
    """Real-subprocess smoke: -S -m <module> with a real PYTHONPATH import of
    a synthetic generator, whose own report proves the child environment is
    exactly the sanitized set (measured by the child, not assumed)."""
    fake_root = tmp_path / 'fake-repo'
    fake_root.mkdir()
    (fake_root / 'synthgen.py').write_text(SYNTH_GENERATOR, encoding='utf-8')
    monkeypatch.setattr(drv, '_REPO_ROOT', fake_root)
    manifest = subinput_manifest(tmp_path, module='synthgen')
    config = drv.SoakConfig.from_dict(subinput_config(manifest))
    driver = drv.SoakDriver(config, tmp_path / 'campaign')
    round_tmp = tmp_path / 'roundtmp'
    round_tmp.mkdir()
    spec = driver._scenario_spec(driver.scenarios[0], round_tmp)
    from polymarket_alpha_lab.research_process import run_research_process
    payload = drv._canonical({'round': 4, 'segment': 1, 'sub_seed': 77,
                              'scenario': 'ok-compute', 'tmp_dir': str(round_tmp)}) + b'\n'
    result = run_research_process(spec=spec, stdin=payload, allow_process_start=True,
                                  stop=drv.ResearchDispatchStop())
    receipt = json.loads((round_tmp / 'child-env.json').read_text(encoding='utf-8'))
    expected_keys = {'PYTHONPATH', 'PYTHONUTF8', 'PYTHONDONTWRITEBYTECODE'}
    if os.name == 'nt':
        expected_keys.add('SystemRoot')
    # Windows env keys are case-insensitive (the child reports SYSTEMROOT for
    # a passed SystemRoot); the exact SET is the purity claim, case aside.
    assert {key.upper() for key in receipt['keys']} == {key.upper() for key in expected_keys}
    assert Path(receipt['executable']).resolve() == Path(driver.python).resolve()
    # The child (not the parent) proves the identity argv arrived intact.
    child_argv = receipt['argv'][1:]
    assert child_argv[:2] == ['--candidate',
                              json.dumps(config.candidate, sort_keys=True,
                                         separators=(',', ':'))]
    assert child_argv[2:4] == ['--manifest-sha256', driver.manifest_sha]
    assert child_argv[4:6] == ['--contract-sha256', driver._subinput_contract_sha256()]
    assert child_argv[6:] == ['--rows', '2048', '--family', 'capture-codec']
    summary = json.loads(result.stdout.decode('utf-8'))
    assert summary == {'echo_round': 4, 'echo_seed': 77}


# ---------- W3: validation, promotion, pointers, failure words ----------

def test_valid_subinput_round_promotes_receipts_and_registers_pointers(tmp_path, monkeypatch):
    code, campaign, driver = run_campaign(monkeypatch, tmp_path, valid_plan(), rounds=2)
    assert code == drv.EXIT_OK
    total_rows = 0
    for round_no in (1, 2):
        record = round_record(campaign, round_no)
        assert record['final'] == 'passed' and record['reason'] is None
        # Legacy transport receipt (stdout summary) and untouched payload hash.
        assert record['receipt']['echo_round'] == round_no
        assert record['receipt']['echo_seed'] == drv.derive_sub_seed(SEED, round_no)
        assert record['input_sha256'] == drv._sha256_bytes(expected_payload(round_no, 'ok-compute', campaign))
        # New pointer registration + declared counts (new field names only).
        pointers = record['subinput_receipts']
        assert pointers == [{'family': 'capture-codec', 'entry': 'capture-codec/run',
                             'file': 'subinputs-capture-codec.json', 'rows': 3,
                             'sha256': pointers[0]['sha256']}]
        assert drv._HEX64_RE.fullmatch(pointers[0]['sha256'])
        assert record['subinput_counts'] == {key: 3 for key in drv.SUBINPUT_COUNT_KEYS}
        assert 'distinct_qualified' not in record  # audit-exclusive name never claimed
        # Promotion: the promoted bytes are the validated receipt, tmp cleaned.
        round_dir = (campaign / 'segments' / 'segment-000001' / 'rounds'
                     / f'round-{round_no:09d}')
        promoted = round_dir / 'subinputs-capture-codec.json'
        doc = receipt_doc(round_no, 1, drv.derive_sub_seed(SEED, round_no),
                          driver.manifest_sha)
        assert promoted.read_bytes() == receipt_bytes(doc)
        assert not (round_dir / 'tmp').exists()
        total_rows += 3
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['subinput_rows_completed'] == total_rows
    summary = drv._read_json(campaign / 'segments' / 'segment-000001' / 'summary.json')
    assert summary['subinput_rows_completed'] == total_rows
    report = drv.inspect_campaign(campaign)
    assert report['subinput_rows_completed'] == total_rows
    assert report['distinct_inputs'] == 2  # legacy semantics: one payload key per round


def test_two_family_round_promotes_each_file(tmp_path, monkeypatch):
    code, campaign, driver = run_campaign(
        monkeypatch, tmp_path, valid_plan(('capture-codec', 'paper-decimal-fill')),
        families=('capture-codec', 'paper-decimal-fill'), rounds=1)
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1)
    assert record['final'] == 'passed'
    assert [pointer['family'] for pointer in record['subinput_receipts']] \
        == ['capture-codec', 'paper-decimal-fill']
    assert record['subinput_counts']['completed'] == 6  # 3 rows per family
    round_dir = (campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001')
    for family in ('capture-codec', 'paper-decimal-fill'):
        assert (round_dir / f'subinputs-{family}.json').is_file()
    assert not (round_dir / 'tmp').exists()
    assert drv.inspect_campaign(campaign)['subinput_rows_completed'] == 6


def test_part_files_are_accepted_with_part_headers_and_two_pointers(tmp_path, monkeypatch):
    """ERRATA-001 Q4: base-name file = part 1 without part fields; numbered
    part >= 2 carries part/part_count; both files promote with pointers and
    aggregate into one family summary entry."""
    def plan(payload, round_tmp, manifest_sha):
        rows = [[format(0xa1b2c3 + index, '064x'), format(0x3f4e5d + index, '064x')]
                for index in range(4)]
        files, part_docs = {}, []
        for part_no, (name, chunk, extra) in enumerate((
                ('subinputs-capture-codec.json', rows[:3], {}),
                ('subinputs-capture-codec.p02.json', rows[3:], {'part': 2, 'part_count': 2}),
        ), start=1):
            doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                              manifest_sha, rows=chunk,
                              counts={key: len(chunk) for key in drv.SUBINPUT_COUNT_KEYS})
            doc['index_origin'] = 0 if part_no == 1 else 3
            doc.update(extra)
            raw = receipt_bytes(doc)
            files[name] = raw
            part_docs.append((name, doc, raw))
        entry = {'family': 'capture-codec', 'entry': 'capture-codec/encode',
                 'files': [{'file': name, 'rows': len(doc['rows']),
                            'sha256': hashlib.sha256(raw).hexdigest(),
                            'bytes': len(raw)} for name, doc, raw in part_docs],
                 'rows': 4, 'counts': {key: 4 for key in drv.SUBINPUT_COUNT_KEYS}}
        return files, summary_stdout(payload['round'], payload['sub_seed'], [entry])

    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1)
    assert record['final'] == 'passed'
    assert [pointer['file'] for pointer in record['subinput_receipts']] \
        == ['subinputs-capture-codec.json', 'subinputs-capture-codec.p02.json']
    assert [pointer['rows'] for pointer in record['subinput_receipts']] == [3, 1]
    assert record['subinput_counts']['completed'] == 4
    round_dir = campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001'
    assert (round_dir / 'subinputs-capture-codec.p02.json').is_file()
    assert not (round_dir / 'tmp').exists()


def test_part_field_on_base_name_or_malformed_part_is_rejected(tmp_path, monkeypatch):
    """part/part_count must arrive as a structurally valid pair (ERRATA-001
    Q4); a lone or out-of-range field is a structural rejection."""
    def make_plan(mutate):
        def plan(payload, round_tmp, manifest_sha):
            doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                              manifest_sha)
            mutate(doc)
            raw = receipt_bytes(doc)
            return ({'subinputs-capture-codec.json': raw},
                    summary_stdout(payload['round'], payload['sub_seed'],
                                   [summary_entry(doc, raw)]))
        return plan

    for position, mutate in enumerate((lambda doc: doc.update(part=1),
                                        lambda doc: doc.update(part_count=2),
                                        lambda doc: doc.update(part=3, part_count=2))):
        case_dir = tmp_path / f'case{position}'
        case_dir.mkdir()
        code, campaign, _ = run_campaign(monkeypatch, case_dir, make_plan(mutate))
        record = round_record(campaign, 1)
        assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid', position


def test_missing_generator_module_fails_clearly_not_unknown(tmp_path):
    """A module name that resolves to nothing must surface as a clear child
    failure, never an unknown: python -m exits nonzero. (The real contract
    generator tests.support.soak_okcompute exists in the integrated tree, so
    the absent-module path uses a name that is genuinely not importable.)"""
    manifest = subinput_manifest(tmp_path, module='tests.support.soak_okcompute_absent')
    config = drv.SoakConfig.from_dict(subinput_config(manifest, minimum_valid_rounds=1))
    campaign = tmp_path / 'campaign'
    driver = drv.SoakDriver(config, campaign)
    code = driver.run()  # REAL subprocess: module import fails in the child
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1)
    assert record['final'] == 'failed'
    assert record['reason'] == 'nonzero_exit'
    assert record['status'] == 'final'
    first = drv._read_json(campaign / 'first-failure.json')
    assert first['reason'] == 'nonzero_exit' and first['scenario'] == 'ok-compute'


def test_declared_receipt_file_missing_fails_receipt_missing(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        entries = [summary_entry(doc, receipt_bytes(doc))]
        return {}, summary_stdout(payload['round'], payload['sub_seed'], entries)  # no file
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_missing'
    round_dir = campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001'
    assert not (round_dir / 'subinputs-capture-codec.json').exists()  # nothing promoted
    assert (round_dir / 'tmp').exists()  # failed round keeps its scratch for evidence
    assert 'subinput_receipts' not in record


def test_non_json_stdout_fails_receipt_invalid(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        return {}, b'this is not json at all'
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid'
    assert record['receipt'] is None


@pytest.mark.parametrize('mutate', [
    lambda doc: doc.update(schema='pal-soak-subinput-receipt-v2'),
    lambda doc: doc.pop('index_origin'),
    lambda doc: doc.update(distinct_qualified=1),      # reserved audit field
    lambda doc: doc['rows'][0].__setitem__(0, doc['rows'][0][0].upper()),
    lambda doc: doc['rows'][0].__setitem__(1, 'ab'),
    lambda doc: doc['rows'][0].append(doc['rows'][0][0]),
    lambda doc: doc['rows'][1].__setitem__(0, doc['rows'][0][0]),  # duplicate input hash
    lambda doc: doc.update(round=doc['round'] + 1),
    lambda doc: doc.update(segment=doc['segment'] + 1),
    lambda doc: doc.update(sub_seed=doc['sub_seed'] + 1),
    lambda doc: doc.update(scenario='other-compute'),
    lambda doc: doc.update(manifest_sha256='d' * 64),
    lambda doc: doc.update(family='paper-decimal-fill'),  # file vs summary family
    lambda doc: doc.update(entry='not-a-valid-entry-shape'),
    lambda doc: doc.update(index_origin=-1),
    lambda doc: doc.update(oracle=123),                  # non-string header field
])
def test_receipt_structural_variants_fail_receipt_invalid(tmp_path, monkeypatch, mutate):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        mutate(doc)
        raw = receipt_bytes(doc)
        return ({'subinputs-capture-codec.json': raw},
                summary_stdout(payload['round'], payload['sub_seed'],
                               [summary_entry(doc, raw)]))
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid', record
    assert 'subinput_receipts' not in record


def test_receipt_file_bytes_not_json_fail_receipt_invalid(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        raw = b'not-json-but-sha-matches'
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        entry = summary_entry(doc, raw)
        return ({'subinputs-capture-codec.json': raw},
                summary_stdout(payload['round'], payload['sub_seed'], [entry]))
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid'


def test_summary_file_name_escape_is_rejected_before_any_read(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        raw = receipt_bytes(doc)
        entry = summary_entry(doc, raw)
        entry['files'][0]['file'] = '../subinputs-capture-codec.json'
        return ({}, summary_stdout(payload['round'], payload['sub_seed'], [entry]))
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid'
    assert not (campaign / 'segments' / 'segment-000001').parent.joinpath(
        'subinputs-capture-codec.json').exists()


@pytest.mark.parametrize('mutate', [
    lambda doc, entry: doc['counts'].update(completed=2),                     # != len(rows)
    lambda doc, entry: doc['counts'].update(planned=2),                       # < generated
    lambda doc, entry: doc['counts'].update(attempted=2),                     # < oracle chain
    lambda doc, entry: doc['counts'].update(distinct_qualified=3),            # extra count key
    lambda doc, entry: doc['counts'].pop('generated'),                        # missing count key
    lambda doc, entry: doc['counts'].update(planned='3'),                     # non-int count
    lambda doc, entry: entry['counts'].update(planned=99),                    # summary vs file
    lambda doc, entry: entry.update(rows=2),                                  # summary rows vs file
])
def test_counts_variants_fail_counts_inconsistent(tmp_path, monkeypatch, mutate):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        raw = receipt_bytes(doc)
        entry = summary_entry(doc, raw)
        mutate(doc, entry)
        raw = receipt_bytes(doc)  # doc may have changed; summary sha tracks it
        entry['files'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
        return ({'subinputs-capture-codec.json': raw},
                summary_stdout(payload['round'], payload['sub_seed'], [entry]))
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'counts_inconsistent', record


def test_summary_sha_mismatch_fails_receipt_sha_mismatch(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        raw = receipt_bytes(doc)
        entry = summary_entry(doc, raw)
        entry['files'][0]['sha256'] = 'e' * 64  # declared sha does not match the file bytes
        return ({'subinputs-capture-codec.json': raw},
                summary_stdout(payload['round'], payload['sub_seed'], [entry]))
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_sha_mismatch'


def test_receipt_over_limit_is_refused_with_a_bounded_read(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        raw = receipt_bytes(doc)
        padded = raw + b' ' * (drv.SUBINPUT_RECEIPT_MAX_BYTES - len(raw) + 64)
        entry = summary_entry(doc, padded)
        return ({'subinputs-capture-codec.json': padded},
                summary_stdout(payload['round'], payload['sub_seed'], [entry]))

    reads = patch_bounded_open(monkeypatch)
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_over_limit'
    receipt_path = (campaign / 'segments' / 'segment-000001' / 'rounds'
                    / 'round-000000001' / 'tmp' / 'subinputs-capture-codec.json')
    sizes = reads.get(str(receipt_path), [])
    assert sizes and max(sizes) <= drv.SUBINPUT_RECEIPT_MAX_BYTES  # cap + probe only
    assert sum(sizes) <= drv.SUBINPUT_RECEIPT_MAX_BYTES + 1


def test_summary_shape_variants_fail_receipt_invalid(tmp_path, monkeypatch):
    cases = {
        'missing_subinput_families': lambda summary: summary.pop('subinput_families'),
        'echo_round_mismatch': lambda summary: summary.update(
            echo_round=summary['echo_round'] + 1),
        'ok_not_true': lambda summary: summary.update(ok=False),
        'ok_missing': lambda summary: summary.pop('ok'),
        'family_not_in_manifest': lambda summary: summary['subinput_families'][0].update(
            family='paper-decimal-fill'),
        'entry_not_a_dict': lambda summary: summary.update(
            subinput_families=['capture-codec']),
        'files_not_a_list': lambda summary: summary['subinput_families'][0].update(
            files='subinputs-capture-codec.json'),
        'files_empty': lambda summary: summary['subinput_families'][0].update(files=[]),
        'sha_not_hex': lambda summary: summary['subinput_families'][0]['files'][0].update(
            sha256='zz'),
    }
    for name, mutate in cases.items():
        case_dir = tmp_path / name  # one fresh campaign per case
        case_dir.mkdir()

        def plan(payload, round_tmp, manifest_sha, mutate=mutate):
            doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                              manifest_sha)
            raw = receipt_bytes(doc)
            summary = {'echo_round': payload['round'], 'echo_seed': payload['sub_seed'],
                       'echo_scenario': 'ok-compute', 'ok': True,
                       'subinput_families': [summary_entry(doc, raw)],
                       'receipt_bytes_total': 0}
            mutate(summary)
            return ({'subinputs-capture-codec.json': raw},
                    json.dumps(summary).encode('utf-8'))

        code, campaign, _ = run_campaign(monkeypatch, case_dir, plan)
        record = round_record(campaign, 1)
        assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid', name


def test_summary_family_count_mismatch_fails_receipt_invalid(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        raw = receipt_bytes(doc)
        return ({'subinputs-capture-codec.json': raw},
                summary_stdout(payload['round'], payload['sub_seed'],
                               [summary_entry(doc, raw)]))
    # Manifest declares TWO families; the summary reports only one.
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan,
                                      families=('capture-codec', 'uncapped-authz-codec'))
    record = round_record(campaign, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid'


def test_promotion_failure_fails_closed_and_keeps_the_original(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        raw = receipt_bytes(doc)
        # Pre-create a DIRECTORY where the receipt must be promoted: the
        # atomic os.replace must fail, and the driver must fail closed.
        (round_tmp.parent / 'subinputs-capture-codec.json').mkdir(exist_ok=True)
        return ({'subinputs-capture-codec.json': raw},
                summary_stdout(payload['round'], payload['sub_seed'],
                               [summary_entry(doc, raw)]))
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan)
    assert code == drv.EXIT_EVIDENCE
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'evidence_failure'
    round_dir = campaign / 'segments' / 'segment-000001' / 'rounds' / 'round-000000001'
    assert (round_dir / 'subinputs-capture-codec.json').is_dir()      # collision untouched
    assert (round_dir / 'tmp' / 'subinputs-capture-codec.json').is_file()  # original kept


def test_failed_subinput_round_preserves_tmp_without_pointers(tmp_path, monkeypatch):
    def plan(payload, round_tmp, manifest_sha):
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha)
        doc['rows'][0][0] = doc['rows'][0][0].upper()  # invalid: uppercase hex
        raw = receipt_bytes(doc)
        return ({'subinputs-capture-codec.json': raw},
                summary_stdout(payload['round'], payload['sub_seed'],
                               [summary_entry(doc, raw)]))
    code, campaign, _ = run_campaign(monkeypatch, tmp_path, plan, rounds=2)
    assert code == drv.EXIT_OK
    for round_no in (1, 2):
        record = round_record(campaign, round_no)
        assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid'
        assert 'subinput_receipts' not in record and 'subinput_counts' not in record
        round_dir = (campaign / 'segments' / 'segment-000001' / 'rounds'
                     / f'round-{round_no:09d}')
        assert (round_dir / 'tmp' / 'subinputs-capture-codec.json').is_file()
        assert not (round_dir / 'subinputs-capture-codec.json').exists()
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['subinput_rows_completed'] == 0
    assert drv.inspect_campaign(campaign)['subinput_rows_completed'] == 0


# ---------- legacy compatibility (payload bytes + legacy counting) ----------

def test_mixed_manifest_keeps_payload_bytes_and_legacy_counts(tmp_path, monkeypatch):
    echo_code = ('import sys,json\n'
                 'p=json.loads(sys.stdin.buffer.read())\n'
                 'sys.stdout.write(json.dumps({"echo_round":p["round"],'
                 '"echo_seed":p["sub_seed"]}))\n')

    def plan(payload, round_tmp, manifest_sha):
        if payload['scenario'] == 'ok-echo':  # real stdout, no receipt files
            return {}, json.dumps({'echo_round': payload['round'],
                                   'echo_seed': payload['sub_seed']}).encode('utf-8')
        doc = receipt_doc(payload['round'], payload['segment'], payload['sub_seed'],
                          manifest_sha, scenario=payload['scenario'])
        raw = receipt_bytes(doc)
        return ({'subinputs-capture-codec.json': raw},
                summary_stdout(payload['round'], payload['sub_seed'],
                               [summary_entry(doc, raw)]))

    manifest = subinput_manifest(tmp_path, extra_scenarios=[
        {'name': 'ok-echo', 'kind': 'process', 'code': echo_code}])
    config = drv.SoakConfig.from_dict(subinput_config(manifest, minimum_valid_rounds=6))
    campaign = tmp_path / 'campaign'
    driver = drv.SoakDriver(config, campaign)
    make_fake_child(monkeypatch, driver, plan)
    assert driver.run() == drv.EXIT_OK

    rounds = 0
    subinput_rows = 0
    for round_no in range(1, 7):
        record = round_record(campaign, round_no)
        assert record['final'] == 'passed'
        # S2 untouched for BOTH kinds: the five-field canonical payload bytes.
        assert record['input_sha256'] == drv._sha256_bytes(
            expected_payload(round_no, record['scenario'], campaign))
        rounds += 1
        if record['scenario'] == 'ok-compute':
            assert record['subinput_receipts'][0]['rows'] == 3
            subinput_rows += 3
    assert subinput_rows > 0  # the mix actually exercised the subinput kind
    summary = drv._read_json(campaign / 'segments' / 'segment-000001' / 'summary.json')
    assert summary['distinct_inputs'] == rounds  # legacy: exactly one key per round
    assert summary['subinput_rows_completed'] == subinput_rows
    report = drv.inspect_campaign(campaign)
    assert report['distinct_inputs'] == rounds
    assert report['subinput_rows_completed'] == subinput_rows
