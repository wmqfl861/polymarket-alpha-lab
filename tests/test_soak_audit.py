"""PX-01 rejection-counterexample tests for tests/support/soak_audit.py.

Every audit dimension is exercised twice: a legal control (a real campaign
produced by the real soak driver through the project's owned-process layer,
with synthetic scenarios only) must PASS, and a forged copy of that campaign
must be detected (FAIL, or UNKNOWN / SNAPSHOT_INCOMPLETE where judging is
not legitimate). All subprocesses are controlled synthetic Python snippets;
no credentials, databases, network, or real model/market/order access.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.support import soak_audit as aud
from tests.support import soak_driver as drv

AUDIT_CLI = Path(aud.__file__).resolve()

ECHO = ('import sys,json,hashlib\n'
        'raw=sys.stdin.buffer.read()\n'
        'p=json.loads(raw.decode("utf-8"))\n'
        'print(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"],'
        '"stdin_sha256":hashlib.sha256(raw).hexdigest(),"stdin_bytes":len(raw)}))\n')

# A passing process scenario that still writes inside its tmp dir, so the
# legal control exercises the passed-round tmp-cleanup path. The historical
# always-fail variant made the control itself fail the corrected R2 gate
# (failed rounds are not valid rounds), so the legal control now uses a
# passing writer and failure shapes are forged per-test instead.
WRITER = ('import sys,json,os\n'
          'p=json.loads(sys.stdin.buffer.read())\n'
          'open(os.path.join(p["tmp_dir"],"junk.bin"),"wb").write(b"j"*64)\n'
          'sys.stdout.write(json.dumps({"echo_round":p["round"],'
          '"echo_seed":p["sub_seed"]}))\n')


def failed_names(report):
    return [c['check'] for c in report['checks'] if c['status'] == aud.FAIL]


def status_of(report, name):
    for entry in report['checks']:
        if entry['check'] == name:
            return entry['status']
    return None


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, sort_keys=True, separators=(',', ':')) + '\n',
                    encoding='utf-8')


def heartbeat_lines(segment: Path):
    raw = (segment / 'heartbeats.jsonl').read_bytes()
    return [json.loads(line) for line in raw.split(b'\n') if line.strip()]


def rewrite_heartbeats(segment: Path, entries) -> None:
    body = b''.join(json.dumps(e, sort_keys=True, separators=(',', ':')).encode('utf-8')
                    + b'\n' for e in entries)
    (segment / 'heartbeats.jsonl').write_bytes(body)


def round_path(campaign: Path, round_no: int, segment: int = 1) -> Path:
    return campaign / 'segments' / f'segment-{segment:06d}' / 'rounds' \
        / f'round-{round_no:09d}'


def round_records(campaign: Path):
    found = {}
    rounds_root = campaign / 'segments' / 'segment-000001' / 'rounds'
    for path in sorted(rounds_root.glob('round-*/round.json')):
        record = drv._read_json(path)
        if record:
            found[record['round']] = (path, record)
    return found


@pytest.fixture(scope='session')
def control(tmp_path_factory):
    """One real driver campaign: six rounds, all passing (echo / writer /
    pytest batch), closed complete. minimum_valid_rounds=6 is met by six
    actually passed rounds, which is what the corrected R2 gate requires."""
    base = tmp_path_factory.mktemp('px01-control')
    target = base / 'synthetic_case.py'
    target.write_text('def test_one():\n    assert 1 + 1 == 2\n\n\n'
                      'def test_two():\n    assert "px01" != "production"\n\n\n'
                      'def test_three():\n    assert len("abc") == 3\n', encoding='utf-8')
    manifest = base / 'manifest.json'
    manifest.write_text(json.dumps({'scenarios': [
        {'name': 'audit-batch', 'kind': 'pytest', 'files': [str(target)]},
        {'name': 'audit-echo', 'kind': 'process', 'code': ECHO},
        {'name': 'audit-writer', 'kind': 'process', 'code': WRITER}]}),
        encoding='utf-8')
    config = {
        'master_seed': 2026092001, 'round_period_seconds': 0.25,
        'heartbeat_seconds': 0.1, 'checkpoint_seconds': 0.5,
        'progress_summary_seconds': 1.0, 'max_unobserved_gap_seconds': 900,
        'scenario_timeout_ms': 60000, 'cleanup_timeout_ms': 4000,
        'per_round_log_bytes': 1048576, 'max_stdout_bytes': 1048576,
        'max_stderr_bytes': 65536, 'max_evidence_bytes': 536870912,
        'max_repro_files': 100, 'minimum_volume_free_bytes': 0,
        'minimum_valid_rounds': 6, 'max_wall_seconds': 1.0, 'workers': 1,
        'candidate': {'label': 'px01-audit-selftest'},
        'scenario_manifest': str(manifest),
    }
    campaign = base / 'campaign'
    driver = drv.SoakDriver(drv.SoakConfig.from_dict(config), campaign)
    assert driver.run() == drv.EXIT_OK
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'complete'
    return {'campaign': campaign, 'config': config, 'manifest': manifest,
            'close': close}


def audit(campaign: Path, config=None, **kwargs):
    return aud.audit_campaign(campaign, config=config, **kwargs)


def forge(control, tmp_path, name='forged'):
    copy = tmp_path / name
    shutil.copytree(control['campaign'], copy)
    return copy


# ---------- legal control ----------

def test_control_campaign_passes_every_dimension(control):
    report = audit(control['campaign'], config=control['config'])
    assert report['overall'] == aud.PASS, report['failures']
    assert failed_names(report) == []
    assert report['rounds_total'] == {'passed': 6, 'failed': 0, 'interrupted': 0,
                                      'unknown': 0}
    assert report['inputs']['payload_level_distinct'] == 6
    assert report['inputs']['functional_seed_distinct'] == 6
    assert report['inputs']['verified_distinct_inputs'] == 6
    assert report['inputs']['pytest_test_invocations_sum'] == 3
    records = round_records(control['campaign'])
    assert records[5][1]['receipt']['tests'] == 3  # pytest batch closed at 3 cases
    first = drv._read_json(control['campaign'] / 'first-failure.json')
    assert first is None                            # no failed rounds in the control
    # corrected R2/R3/R4 gates all hold for the legal control
    assert status_of(report, 'false_completion') is None
    assert status_of(report, 'observation_span') == aud.PASS
    assert status_of(report, 'completion_segment_binding') == aud.PASS


# ---------- 1. missing round ----------

def test_missing_round_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    shutil.rmtree(round_path(copy, 3))
    report = audit(copy, config=control['config'])
    assert 'rounds_sequence' in failed_names(report)
    assert report['overall'] == aud.FAIL


# ---------- 2. duplicate round ----------

def test_duplicate_round_number_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    shutil.copytree(round_path(copy, 1), round_path(copy, 99))
    report = audit(copy, config=control['config'])
    names = failed_names(report)
    assert 'rounds_uniqueness' in names and 'rounds_dir_record_match' in names


# ---------- 3. multiple terminal states for one round ----------

def test_terminal_sidecar_conflict_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    unknown = copy / 'segments' / 'segment-000001' / 'rounds' / 'unknown'
    unknown.mkdir()
    write_json(unknown / 'round-000000002.json',
               {'final': 'unknown', 'reason': 'record_unreadable'})
    report = audit(copy, config=control['config'])
    assert 'rounds_sidecar_conflict' in failed_names(report)


# ---------- 4. lost tail line ----------

def test_torn_tail_fails_closed_campaign_but_not_live_snapshot(control, tmp_path):
    copy = forge(control, tmp_path)
    hb = copy / 'segments' / 'segment-000001' / 'heartbeats.jsonl'
    with open(hb, 'ab') as handle:
        handle.write(b'{"wall": 1789956888.0, "mono')
    report = audit(copy, config=control['config'])
    assert 'torn_heartbeat_tail' in failed_names(report)
    # The same torn tail in a LIVE campaign is an incomplete snapshot, not a verdict.
    live = forge(control, tmp_path, name='live')
    hb = live / 'segments' / 'segment-000001' / 'heartbeats.jsonl'
    with open(hb, 'ab') as handle:
        handle.write(b'{"wall": 1789956888.0, "mono')
    running = round_path(live, 7)
    running.mkdir()
    seed = drv.derive_sub_seed(2026092001, 7)
    names = ['audit-batch', 'audit-echo', 'audit-writer']
    record = {'round': 7, 'segment': 1, 'status': 'running', 'final': None,
              'sub_seed': seed, 'scenario': names[seed % 3],
              'input_sha256': ''}
    record['input_sha256'] = aud.recompute_payload_hash(record, running)
    write_json(running / 'round.json', record)
    report = audit(live, config=control['config'])
    assert report['live'] is True
    assert report['overall'] == aud.INCOMPLETE
    assert 'torn_heartbeat_tail' not in failed_names(report)
    kinds = {item['kind'] for item in report['snapshot_incomplete']}
    assert 'torn_heartbeat_tail' in kinds and 'round_in_flight' in kinds


# ---------- 5. mixed driver identity ----------

def test_mixed_segment_identity_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    header_path = copy / 'segments' / 'segment-000001' / 'segment.json'
    header = drv._read_json(header_path)
    header['driver_sha256'] = 'f' * 64
    write_json(header_path, header)
    report = audit(copy, config=control['config'])
    assert 'identity_segment_consistency' in failed_names(report)


# ---------- 6. wrong summary ----------

def test_summary_mismatch_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    close_path = copy / 'segments' / 'segment-000001' / 'segment-close.json'
    close = drv._read_json(close_path)
    # The legal control really passed 6 rounds; claim 5 to contradict the
    # records. (Updated for the all-passing control: the old forged value 6
    # matched the new control by accident and forged nothing.)
    close['rounds_total']['passed'] = 5
    write_json(close_path, close)
    report = audit(copy, config=control['config'])
    assert 'summary_mismatch' in failed_names(report)


# ---------- 7. clock rollback ----------

def test_clock_rollback_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    segment = copy / 'segments' / 'segment-000001'
    entries = heartbeat_lines(segment)
    for entry in entries[3:]:
        entry['wall'] -= 3600.0
    rewrite_heartbeats(segment, entries)
    report = audit(copy, config=control['config'])
    assert 'clock_rollback' in failed_names(report)


# ---------- 8. observation gap ----------

def test_observation_gap_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    segment = copy / 'segments' / 'segment-000001'
    entries = heartbeat_lines(segment)
    entries[-1]['wall'] += 3600.0
    rewrite_heartbeats(segment, entries)
    report = audit(copy, config=control['config'])
    assert 'observation_gap' in failed_names(report)


# ---------- 9. short observation masquerading as the wall gate ----------

def test_short_observation_claiming_complete_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    close_path = copy / 'segments' / 'segment-000001' / 'segment-close.json'
    close = drv._read_json(close_path)
    header = drv._read_json(copy / 'segments' / 'segment-000001' / 'segment.json')
    close['stopped_wall'] = header['started_wall'] + 0.1  # claims 0.1s, gate is 1.0s
    write_json(close_path, close)
    report = audit(copy, config=control['config'])
    assert 'short_observation' in failed_names(report)


def test_seventy_one_fifty_five_cannot_pose_as_seventy_two_hours(control, tmp_path):
    # Same forgery shape as 71h55m posing as 72h: claim a 259200s wall gate was
    # met by evidence observed for ~1.2s. The config claim itself is also
    # rejected because campaign.json pins the config hash of the real run.
    copy = forge(control, tmp_path)
    report = audit(copy, config={**control['config'], 'max_wall_seconds': 259200})
    names = failed_names(report)
    assert 'short_observation' in names and 'config_sha_match' in names


# ---------- 10. fewer XML test cases than the inventory ----------

def test_undercounted_test_cases_are_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    path, record = round_records(copy)[5]
    record['receipt']['tests'] = 2  # three tests really ran
    write_json(path, record)
    report = audit(copy, config=control['config'],
                   baseline={'audit-batch': 3, 'audit-echo': 0, 'audit-writer': 0})
    assert 'inventory_baseline' in failed_names(report)
    clean = audit(control['campaign'], config=control['config'],
                  baseline={'audit-batch': 3, 'audit-echo': 0, 'audit-writer': 0})
    assert 'inventory_baseline' not in failed_names(clean)


# ---------- 11. label-only duplicate inputs ----------

def test_label_only_duplicate_input_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    records = round_records(copy)
    path, round6 = records[6]
    _, round1 = records[1]
    round6['sub_seed'] = round1['sub_seed']      # identical functional seed...
    round6['scenario'] = round1['scenario']      # ...and identical scenario
    write_json(path, round6)                      # payload hash now stale too
    report = audit(copy, config=control['config'])
    names = failed_names(report)
    assert 'inputs_duplicate_functional' in names
    assert 'payload_hash' in names               # the stale hash is caught as well
    clean = audit(control['campaign'], config=control['config'])
    assert report['inputs']['functional_seed_distinct'] == 5  # 6 rounds, 5 distinct
    assert clean['inputs']['functional_seed_distinct'] == 6


# ---------- 12. swallowed failure ----------

def test_swallowed_failure_is_detected_two_ways(control, tmp_path):
    # A passing round is forged into a "failed" record: the driver log still
    # says passed, the close summary no longer matches the recount, and the
    # failed round has no first-failure record to back it.
    flipped = forge(control, tmp_path, name='flipped')
    path, record = round_records(flipped)[3]
    record['final'] = 'failed'                   # round 4 really passed
    record['reason'] = 'nonzero_exit'
    write_json(path, record)
    report = audit(flipped, config=control['config'])
    names = failed_names(report)
    assert 'driver_log_closure' in names         # the log still says passed
    assert 'summary_mismatch' in names           # the close summary no longer matches
    assert 'first_failure' in names              # failed round without a first-failure record


# ---------- 13. unmeasured resources written as zero ----------

def test_unmeasured_resource_written_as_zero_is_detected(control, tmp_path):
    copy = forge(control, tmp_path)
    segment = copy / 'segments' / 'segment-000001'
    entries = heartbeat_lines(segment)
    forged = dict(entries[-1])
    forged['wall'] += 0.1
    forged['monotonic'] += 0.1
    forged['rss_measured'] = True
    forged['rss_bytes'] = 0                      # a live process never has RSS 0
    rewrite_heartbeats(segment, entries + [forged])
    report = audit(copy, config=control['config'])
    assert 'resources_plausibility' in failed_names(report)
    clean = audit(control['campaign'], config=control['config'])
    assert 'resources_plausibility' not in failed_names(clean)


# ---------- 14. R1: declared sub-inputs are not distinct-input proof ----------

def test_r1_declared_subinputs_alone_cannot_pass_distinct_inputs_gate(control, tmp_path):
    # Historical counterexample shape: receipts declare a sub-input sum that
    # meets the gate (100000) while the campaign actually executed only a
    # handful of distinct logical inputs. Declaration is not execution proof.
    copy = forge(control, tmp_path)
    for round_no in (1, 2):
        path, record = round_records(copy)[round_no]
        record['receipt']['inputs'] = 50000
        write_json(path, record)
    report = audit(copy, config=control['config'], min_distinct_inputs=100000)
    names = failed_names(report)
    assert 'min_distinct_inputs' in names
    assert report['inputs']['receipt_declared_subinputs_sum'] == 100000
    assert report['inputs']['verified_distinct_inputs'] < 100000
    assert report['overall'] == aud.FAIL


def test_r1_verified_distinct_inputs_pass_the_gate(control):
    report = audit(control['campaign'], config=control['config'],
                   min_distinct_inputs=6)
    assert 'min_distinct_inputs' not in failed_names(report)
    assert status_of(report, 'min_distinct_inputs') == aud.PASS
    assert report['inputs']['verified_distinct_inputs'] == 6
    # Without a required minimum the gate stays UNKNOWN, never an optimistic PASS.
    plain = audit(control['campaign'], config=control['config'])
    assert status_of(plain, 'min_distinct_inputs') == aud.UNKNOWN


# ---------- 15. R2: failed rounds are not valid rounds ----------

def test_r2_all_failed_rounds_do_not_satisfy_minimum_valid_rounds(control, tmp_path):
    # Historical counterexample shape: passed=0 / failed=864 against
    # minimum_valid_rounds=864. Only actually passed rounds count; failed,
    # interrupted and unknown rounds never do.
    copy = forge(control, tmp_path)
    for path in sorted((copy / 'segments' / 'segment-000001' / 'rounds')
                       .glob('round-*/round.json')):
        record = drv._read_json(path)
        record['final'] = 'failed'
        record['reason'] = 'nonzero_exit'
        write_json(path, record)
    report = audit(copy, config=control['config'])
    names = failed_names(report)
    assert 'false_completion' in names
    gate = [c for c in report['checks'] if c['check'] == 'false_completion'][0]
    assert gate['detail']['passed_recounted'] == 0
    assert gate['detail']['required_valid_rounds'] == \
        control['config']['minimum_valid_rounds']
    assert report['overall'] == aud.FAIL


def test_r2_six_actually_passed_rounds_satisfy_the_minimum(control):
    report = audit(control['campaign'], config=control['config'])
    assert 'false_completion' not in failed_names(report)


# ---------- 16. R3: no tolerance is deducted from the wall floor ----------

def test_r3_small_wall_deficit_is_not_accepted(control, tmp_path):
    # 0.9s observed against a 1.0s gate: the old audit accepted any deficit
    # inside max(0.5, 0.1%) tolerance; the corrected gate is a strict floor.
    copy = forge(control, tmp_path)
    close_path = copy / 'segments' / 'segment-000001' / 'segment-close.json'
    close = drv._read_json(close_path)
    header = drv._read_json(copy / 'segments' / 'segment-000001' / 'segment.json')
    close['stopped_wall'] = header['started_wall'] + 0.9
    write_json(close_path, close)
    report = audit(copy, config=control['config'])
    names = failed_names(report)
    assert 'short_observation' in names
    gate = [c for c in report['checks'] if c['check'] == 'short_observation'][0]
    assert gate['detail']['deficit_seconds'] == 0.1

    # The historical 72h shape: 259080s observed against a 259200s gate is a
    # 120-second deficit, previously hidden by the 259.2s proportional
    # tolerance. (The config override also trips config_sha_match, which is
    # expected: the campaign pins the real config hash.)
    scaled = forge(control, tmp_path, name='scaled')
    close_path = scaled / 'segments' / 'segment-000001' / 'segment-close.json'
    close = drv._read_json(close_path)
    header = drv._read_json(scaled / 'segments' / 'segment-000001' / 'segment.json')
    close['stopped_wall'] = header['started_wall'] + 259080
    write_json(close_path, close)
    report = audit(scaled, config={**control['config'], 'max_wall_seconds': 259200})
    gate = [c for c in report['checks'] if c['check'] == 'short_observation'][0]
    assert gate['status'] == aud.FAIL
    assert gate['detail']['deficit_seconds'] == 120.0


def test_r3_span_exactly_at_the_gate_is_accepted(control, tmp_path):
    copy = forge(control, tmp_path)
    close_path = copy / 'segments' / 'segment-000001' / 'segment-close.json'
    close = drv._read_json(close_path)
    header = drv._read_json(copy / 'segments' / 'segment-000001' / 'segment.json')
    close['stopped_wall'] = header['started_wall'] + 1.0  # exactly the gate
    write_json(close_path, close)
    report = audit(copy, config=control['config'])
    assert 'short_observation' not in failed_names(report)
    assert status_of(report, 'observation_span') == aud.PASS


# ---------- 17. R4: completion receipt must bind to one closed segment ----------

def test_r4_old_complete_receipt_spliced_with_new_segment_is_rejected(control, tmp_path):
    # Historical counterexample shape: segment-000001 closed "complete" (the
    # receipt and the full wall span) while a newer segment-000002 actually
    # ended the campaign via stop_file. Borrowing the old segment's receipt
    # must never pose as a normal end.
    copy = forge(control, tmp_path)
    latest = copy / 'segments' / 'segment-000002'
    (latest / 'rounds').mkdir(parents=True)
    write_json(latest / 'segment.json', {'started_wall': 2000, 'segment': 2})
    write_json(latest / 'segment-close.json',
               {'reason': 'stop_file', 'stopped_wall': 261200,
                'rounds_total': {'passed': 0, 'failed': 0, 'interrupted': 0,
                                 'unknown': 0}})
    report = audit(copy, config=control['config'])
    names = failed_names(report)
    assert 'completion_segment_binding' in names
    gate = [c for c in report['checks'] if c['check'] == 'completion_segment_binding'][0]
    assert gate['detail']['spliced_receipts'] == ['segment-000001']
    assert gate['detail']['latest_segment'] == 'segment-000002'
    assert report['overall'] == aud.FAIL


def test_r4_completion_bound_to_the_same_closed_segment_is_accepted(control):
    report = audit(control['campaign'], config=control['config'])
    gate = [c for c in report['checks'] if c['check'] == 'completion_segment_binding'][0]
    assert gate['status'] == aud.PASS
    assert gate['detail']['segment'] == 'segment-000001'


# ---------- pure helpers and CLI ----------

def test_parse_jsonl_reports_torn_tail_without_judging(tmp_path):
    path = tmp_path / 'lines.jsonl'
    path.write_bytes(b'{"a":1}\n{"b":2}\n{"partial')
    entries, torn = aud.parse_jsonl(path)
    assert [e['a'] for e in entries if 'a' in e] == [1]
    assert torn is True
    path.write_bytes(b'{"a":1}\n{"b":2}\n')
    entries, torn = aud.parse_jsonl(path)
    assert len(entries) == 2 and torn is False


def test_recompute_payload_hash_matches_driver(control):
    path, record = round_records(control['campaign'])[2]
    assert aud.recompute_payload_hash(record, path.parent) == record['input_sha256']


def test_cli_exit_codes(control, tmp_path):
    env = {'SystemRoot': os.environ['SystemRoot']} if os.name == 'nt' else {}
    config_path = control['manifest'].parent / 'audit-config.json'
    config_path.write_text(json.dumps(control['config']), encoding='utf-8')
    ok = subprocess.run([sys.executable, '-B', str(AUDIT_CLI), 'audit',
                         '--campaign', str(control['campaign']),
                         '--config', str(config_path)],
                        capture_output=True, text=True, env=env)
    assert ok.returncode == aud.EXIT_OK, ok.stdout[-2000:]
    report = json.loads(ok.stdout)
    assert report['overall'] == aud.PASS
    copy = forge(control, tmp_path)
    close_path = copy / 'segments' / 'segment-000001' / 'segment-close.json'
    close = drv._read_json(close_path)
    header = drv._read_json(copy / 'segments' / 'segment-000001' / 'segment.json')
    close['stopped_wall'] = header['started_wall'] + 0.1
    write_json(close_path, close)
    bad = subprocess.run([sys.executable, '-B', str(AUDIT_CLI), 'audit',
                          '--campaign', str(copy), '--config', str(config_path)],
                         capture_output=True, text=True, env=env)
    assert bad.returncode == aud.EXIT_FAIL
    assert 'short_observation' in [c['check'] for c in json.loads(bad.stdout)['failures']]
