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
  tmp path from input identity, plus duplicate-functional-input detection,
  and an optional min-distinct-inputs gate that counts only distinct logical
  inputs verified against executed round records — a receipt's declared
  sub-input sum alone is a declaration, never execution proof;
- clocks and observation: heartbeat gaps, wall-vs-monotonic drift (rollback /
  forward jump), closed-segment span vs max_wall_seconds (a "complete" close
  observed for less than the wall gate is FAIL — the floor is strict and no
  tolerance is deducted from it), stopped-wall vs heartbeat consistency,
  summary/segment-close agreement with the round records, and completion
  binding: a "complete" close receipt must belong to the LATEST closed
  segment, so an older segment's complete receipt cannot be spliced with a
  newer segment's times, identity or rounds to pose as a normal end;
- inventory: driver.log round_final closure, junit XML presence on failed
  pytest rounds, receipt/XML test counts against an optional collection
  baseline;
- resources: heartbeats must not record unmeasured values as zero;
- subinput receipts (``pal-soak-subinput-receipt-v1``): per-round
  ``subinputs-<family>.json`` files are read under a strict 512KiB
  limit-first probe, structurally validated against the receipt contract
  (exact schema/field set, types, hex64 rows, counts invariants, header
  byte budget, in-file duplicate keys), whitelist-recomputed row by row
  through the audit's OWN independent family verifiers (never the
  generator), deduplicated globally by ``entry + NUL + input_sha256`` over
  passed rounds of the R4-qualified segment, and reported in a dedicated
  ``subinputs`` block with the new ``min_distinct_qualified_subinputs``
  gate. Legacy input metrics and the old ``min_distinct_inputs`` gate are
  frozen side by side and never convert into the new counters; unknown
  fields in old records are ignored but listed in the report.

Every check reports PASS / FAIL / UNKNOWN. While the campaign is live (lock
holder alive, a round mid-flight, or last segment unclosed), torn or in-flight
reads are reported as SNAPSHOT_INCOMPLETE and are never judged as corruption
or as passing; the original files are never modified.

Usage:
  python tests/support/soak_audit.py audit --campaign DIR [--config FILE]
      [--manifest FILE] [--master-seed N] [--baseline FILE]
      [--expect key=value ...] [--min-distinct-inputs N]
      [--min-distinct-qualified-subinputs N] [--json-out FILE]

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
from tests.support import soak_audit_subinputs as subinputs  # noqa: E402

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_FAIL = 3
EXIT_CONFIG = 5

PASS, FAIL, UNKNOWN = 'PASS', 'FAIL', 'UNKNOWN'
INCOMPLETE = 'SNAPSHOT_INCOMPLETE'
_HEX64 = re.compile(r'[0-9a-f]{64}')

# --- pal-soak-subinput-receipt-v1 audit constants (contract decisions 2/7/8)
RECEIPT_MAX_BYTES = 524288            # 512KiB per receipt file, limit-first
RECEIPT_HEADER_MAX_BYTES = 2048       # serialized header skeleton budget
RECEIPT_BASE_FIELDS = frozenset({
    'schema', 'family', 'entry', 'normalize_rule', 'candidate',
    'manifest_sha256', 'generator_sha256', 'contract_sha256', 'round',
    'segment', 'sub_seed', 'scenario', 'index_origin', 'oracle', 'counts',
    'rows'})
RECEIPT_PART_FIELDS = frozenset({'part', 'part_count'})
RECEIPT_COUNT_FIELDS = ('planned', 'generated', 'attempted', 'oracle_passed',
                        'completed')
_FAMILY_RE = re.compile(r'[a-z0-9-]{1,32}')
# Round-record fields the baseline driver writes (plus the wired subinput
# pointer fields); anything else is reported, never interpreted (decision 7).
KNOWN_ROUND_FIELDS = frozenset({
    'round', 'segment', 'sub_seed', 'scenario', 'input_sha256', 'status',
    'final', 'reason', 'finished_wall', 'elapsed_ms', 'stderr_bytes',
    'receipt', 'log_truncated', 'subinput_receipts', 'subinput_counts'})


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


class _DuplicateKeyError(ValueError):
    """A receipt document repeated a JSON object key."""


def _pairs_without_duplicates(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _read_receipt_capped(path: Path):
    """Limit-first receipt read (contract decision 2.4 / N2 interface).

    At most ``RECEIPT_MAX_BYTES`` bytes ever leave the file: one bounded
    read plus a single-byte over-limit probe executed BEFORE any parsing.
    Returns ``(data, None)`` on success and ``(None, reason)`` on refusal.
    """
    try:
        with open(path, 'rb') as handle:
            data = handle.read(RECEIPT_MAX_BYTES)
            if handle.read(1):
                return None, 'receipt_over_limit'
            return data, None
    except OSError:
        return None, 'receipt_unreadable'


def _parse_receipt_doc(raw: bytes):
    """Strict receipt parse: duplicate JSON keys are a rejection, not a
    last-writer-wins merge."""
    return json.loads(raw.decode('utf-8'),
                      object_pairs_hook=_pairs_without_duplicates)


def _is_plain_int(value) -> bool:
    return type(value) is int  # bool is an int subclass; reject it explicitly


def _validate_receipt_doc(doc):
    """Structural validation (contract decisions 1/2/7).

    Returns ``(reason, detail)`` when the file must be rejected, else
    ``(None, None)``.
    """
    if type(doc) is not dict:
        return 'receipt_not_object', {'type': type(doc).__name__}
    schema = doc.get('schema')
    if schema != subinputs.RECEIPT_SCHEMA:
        return 'receipt_schema_rejected', {
            'found': schema if type(schema) is str else type(schema).__name__}
    if 'distinct_qualified' in doc:
        # Decision 5: the qualified-distinct count is audit-exclusive.
        return 'producer_claim_reserved_field', None
    keys = set(doc)
    extra = sorted(keys - RECEIPT_BASE_FIELDS - RECEIPT_PART_FIELDS)
    if extra:
        return 'receipt_unknown_field', {'fields': extra}
    missing = sorted(RECEIPT_BASE_FIELDS - keys)
    if missing:
        return 'receipt_missing_field', {'fields': missing}
    part, part_count = doc.get('part'), doc.get('part_count')
    if (part is None) != (part_count is None):
        return 'receipt_field_type', {'field': 'part/part_count',
                                      'note': 'both or neither'}
    if part is not None and not (_is_plain_int(part) and _is_plain_int(part_count)
                                 and 1 <= part <= part_count):
        return 'receipt_field_type', {'field': 'part'}
    for field in ('family', 'entry', 'normalize_rule', 'candidate', 'oracle',
                  'scenario'):
        if type(doc.get(field)) is not str:
            return 'receipt_field_type', {'field': field}
    if _FAMILY_RE.fullmatch(doc['family']) is None:
        return 'receipt_field_type', {'field': 'family',
                                      'value': doc['family'][:64]}
    for field in ('manifest_sha256', 'generator_sha256', 'contract_sha256'):
        if type(doc.get(field)) is not str \
                or _HEX64.fullmatch(doc[field]) is None:
            return 'receipt_field_type', {'field': field}
    for field in ('round', 'segment', 'sub_seed', 'index_origin'):
        if not _is_plain_int(doc.get(field)):
            return 'receipt_field_type', {'field': field}
    if doc['index_origin'] < 0:
        return 'receipt_field_type', {'field': 'index_origin', 'note': 'negative'}
    counts = doc.get('counts')
    if type(counts) is not dict or set(counts) != set(RECEIPT_COUNT_FIELDS) \
            or any(not _is_plain_int(counts[name]) or counts[name] < 0
                   for name in RECEIPT_COUNT_FIELDS):
        return 'receipt_field_type', {'field': 'counts'}
    rows = doc.get('rows')
    if type(rows) is not list:
        return 'receipt_field_type', {'field': 'rows'}
    for position, row in enumerate(rows):
        if type(row) is not list or len(row) != 2 \
                or any(type(item) is not str or _HEX64.fullmatch(item) is None
                       for item in row):
            return 'receipt_row_shape', {'row': position}
    skeleton = dict(doc)
    skeleton['rows'] = []
    header_bytes = len(json.dumps(skeleton, sort_keys=True,
                                  separators=(',', ':')).encode('utf-8')) + 1
    if header_bytes > RECEIPT_HEADER_MAX_BYTES:
        return 'receipt_header_over_limit', {'bytes': header_bytes}
    planned, generated = counts['planned'], counts['generated']
    attempted, oracle_passed, completed = (counts['attempted'],
                                           counts['oracle_passed'],
                                           counts['completed'])
    if not (planned >= generated >= attempted >= oracle_passed == completed) \
            or completed != len(rows):
        return 'counts_inconsistent', dict(counts, rows=len(rows))
    row_inputs = [row[0] for row in rows]
    if len(set(row_inputs)) != len(row_inputs):
        seen, duplicated = set(), set()
        for value in row_inputs:
            (duplicated if value in seen else seen).add(value)
        return 'receipt_duplicate_key', {'input_sha256': sorted(duplicated)[:5]}
    return None, None


def _sub_accumulator():
    return {'receipt_files': 0, 'rows_read': 0, 'rows_whitelist_verified': 0,
            'rows_rejected': 0, 'planned_sum': 0, 'generated_sum': 0,
            'attempted_sum': 0, 'completed_sum': 0, 'oracle_passed_sum': 0,
            'declared_vs_rows_consistent': 0, 'domain_overlaps': 0,
            'dedup_collapsed': 0, 'distinct_qualified': 0}


def _audit_subinputs(records, identity, cfg, qualified_segment, min_qualified,
                     unknown_fields_seen, legacy_verified_round_inputs):
    """Walk every round's ``subinputs-*.json`` receipts (contract N2 side).

    Returns ``(report_block, checks_list)``. Row failures are isolated per
    row; file-level contract violations reject the whole file; the legacy
    input metrics are reported next to — never merged into — the new
    ``distinct_qualified`` counter.
    """
    total = _sub_accumulator()
    families: dict[str, dict] = {}
    rejected_files: list[dict] = []
    row_rejections: list[dict] = []
    pointer_problems: list[dict] = []
    overlap_events: list[dict] = []
    provenance = {'generator_sha256': set(), 'contract_sha256': set()}
    contract_copy_path = _REPO_ROOT / 'docs' / 'contracts' \
        / 'soak-subinput-receipt-v1.md'
    contract_copy_sha = None
    if contract_copy_path.is_file():
        try:
            contract_copy_sha = sha256(contract_copy_path.read_bytes()).hexdigest()
        except OSError:
            contract_copy_sha = None
    want_candidate = None
    if cfg is not None and isinstance(getattr(cfg, 'candidate', None), dict):
        want_candidate = json.dumps(cfg.candidate, sort_keys=True,
                                    separators=(',', ':'))
    qualified_keys: dict[str, dict] = {}

    def process(seg_name, rno, record, round_dir, name, pointer):
        path = round_dir / name
        label = {'segment': seg_name, 'round': rno, 'file': name}

        def reject(reason, detail=None):
            rejected_files.append({**label, 'reason': reason, 'detail': detail})

        raw, read_reason = _read_receipt_capped(path)
        if raw is None:
            reject(read_reason, {'limit_bytes': RECEIPT_MAX_BYTES})
            return
        try:
            doc = _parse_receipt_doc(raw)
        except _DuplicateKeyError as error:
            reject('receipt_duplicate_json_key', {'key': str(error)})
            return
        except (ValueError, UnicodeDecodeError):
            reject('receipt_unparseable')
            return
        if json.dumps(doc, sort_keys=True, separators=(',', ':')).encode(
                'utf-8') + b'\n' != raw:
            reject('receipt_noncanonical')
            return
        reason, detail = _validate_receipt_doc(doc)
        if reason is not None:
            reject(reason, detail)
            return
        family, entry = doc['family'], doc['entry']
        rule = subinputs.REGISTRY.get((family, entry))
        if rule is None:
            reject('whitelist_unknown', {'family': family, 'entry': entry})
            return
        if doc['normalize_rule'] != rule['normalize_rule']:
            reject('normalize_rule_mismatch',
                   {'found': doc['normalize_rule'],
                    'expected': rule['normalize_rule']})
            return
        if re.fullmatch(rf'subinputs-{re.escape(family)}(?:\.p\d+)?.json',
                        name) is None:
            reject('receipt_filename_mismatch', {'family': family})
            return
        if (doc['round'] != record.get('round')
                or doc['segment'] != record.get('segment')
                or doc['sub_seed'] != record.get('sub_seed')
                or doc['scenario'] != record.get('scenario')):
            reject('receipt_echo_transport')
            return
        if doc['manifest_sha256'] != identity.get('manifest_sha256'):
            reject('receipt_manifest_mismatch')
            return
        if want_candidate is not None and doc['candidate'] != want_candidate:
            reject('receipt_candidate_mismatch')
            return
        if pointer is not None and (
                pointer.get('sha256') != sha256(raw).hexdigest()
                or pointer.get('rows') != len(doc['rows'])
                or pointer.get('family') != family
                or pointer.get('entry') != entry):
            reject('receipt_pointer_mismatch')
            return
        provenance['generator_sha256'].add(doc['generator_sha256'])
        provenance['contract_sha256'].add(doc['contract_sha256'])
        family_acc = families.setdefault(family, _sub_accumulator())
        for acc in (family_acc, total):
            acc['receipt_files'] += 1
            for counter in RECEIPT_COUNT_FIELDS:
                acc[f'{counter}_sum'] += doc['counts'][counter]
            acc['rows_read'] += len(doc['rows'])
        if doc['counts']['completed'] == len(doc['rows']):
            family_acc['declared_vs_rows_consistent'] += 1
            total['declared_vs_rows_consistent'] += 1
        source = f'{seg_name}/{rno}/{name}'
        in_qualified_segment = (qualified_segment is not None
                                and seg_name == qualified_segment
                                and record.get('final') == 'passed')
        for offset, row in enumerate(doc['rows']):
            index = doc['index_origin'] + offset
            verdict = subinputs.verify_row((family, entry), index,
                                           row[0], row[1])
            if verdict['ok']:
                for acc in (family_acc, total):
                    acc['rows_whitelist_verified'] += 1
                if in_qualified_segment:
                    key = entry + '\x00' + row[0]
                    first = qualified_keys.get(key)
                    if first is None:
                        qualified_keys[key] = {'family': family, 'source': source}
                    else:
                        for acc in (family_acc, total):
                            acc['dedup_collapsed'] += 1
                        if first['source'] != source:
                            for acc in (family_acc, total):
                                acc['domain_overlaps'] += 1
                            overlap_events.append(
                                {'first': first['source'], 'repeat': source,
                                 'entry': entry})
            else:
                for acc in (family_acc, total):
                    acc['rows_rejected'] += 1
                row_rejections.append({**label, 'row': offset, 'index': index,
                                       'reason': verdict['reason'],
                                       'detail': verdict.get('detail')})

    for rno in sorted(records):
        for seg_name, record, round_dir in sorted(records[rno],
                                                  key=lambda item: item[0]):
            pointer_map = {}
            raw_pointers = record.get('subinput_receipts')
            if raw_pointers is not None:
                if type(raw_pointers) is list:
                    for pointer in raw_pointers:
                        if type(pointer) is dict \
                                and type(pointer.get('file')) is str:
                            pointer_map[pointer['file']] = pointer
                        else:
                            pointer_problems.append(
                                {'segment': seg_name, 'round': rno,
                                 'problem': 'malformed_pointer'})
                else:
                    pointer_problems.append({'segment': seg_name, 'round': rno,
                                             'problem': 'subinput_receipts_not_a_list'})
            try:
                disk_files = sorted(p.name for p in round_dir.glob('subinputs-*.json'))
            except OSError:
                disk_files = []
            if record.get('final') == 'passed' \
                    and set(pointer_map) != set(disk_files):
                pointer_problems.append(
                    {'segment': seg_name, 'round': rno,
                     'missing_files': sorted(set(pointer_map) - set(disk_files)),
                     'unpointed_files': sorted(set(disk_files) - set(pointer_map))})
            for name in disk_files:
                process(seg_name, rno, record, round_dir, name, pointer_map.get(name))

    for info in qualified_keys.values():
        families[info['family']]['distinct_qualified'] += 1
        total['distinct_qualified'] += 1

    provenance_problems = []
    for field in ('generator_sha256', 'contract_sha256'):
        if len(provenance[field]) > 1:
            provenance_problems.append({'field': field,
                                        'values': sorted(provenance[field])})
    contract_copy_verified = None
    if contract_copy_sha is not None and provenance['contract_sha256']:
        if provenance['contract_sha256'] == {contract_copy_sha}:
            contract_copy_verified = True
        else:
            contract_copy_verified = False
            provenance_problems.append(
                {'field': 'contract_sha256',
                 'note': 'does not match the repository contract copy',
                 'expected': contract_copy_sha,
                 'found': sorted(provenance['contract_sha256'])})

    checks_out = []
    if total['receipt_files'] or rejected_files or pointer_problems:
        if rejected_files:
            checks_out.append(check('subinput_receipts', FAIL,
                                    {'count_rejected': len(rejected_files),
                                     'rejected_files': rejected_files[:20],
                                     'receipt_files': total['receipt_files'],
                                     'rows_read': total['rows_read']}))
        else:
            checks_out.append(check('subinput_receipts', PASS,
                                    {'receipt_files': total['receipt_files'],
                                     'rows_read': total['rows_read']}))
        if total['rows_read']:
            if row_rejections:
                checks_out.append(check('subinput_row_recompute', FAIL,
                                        {'rows_rejected': total['rows_rejected'],
                                         'sample': row_rejections[:20]}))
            else:
                checks_out.append(check('subinput_row_recompute', PASS,
                                        {'rows_whitelist_verified':
                                         total['rows_whitelist_verified']}))
        if pointer_problems:
            checks_out.append(check('subinput_pointer_consistency', FAIL,
                                    {'problems': pointer_problems[:20]}))
        if provenance_problems:
            checks_out.append(check('subinput_provenance_consistency', FAIL,
                                    {'problems': provenance_problems}))
        elif total['receipt_files']:
            checks_out.append(check('subinput_provenance_consistency', PASS,
                                    {'generator_sha256':
                                     sorted(provenance['generator_sha256']),
                                     'contract_copy_verified':
                                     contract_copy_verified}))
    else:
        checks_out.append(check(
            'subinput_receipts', PASS,
            {'receipt_files': 0, 'rows_read': 0,
             'note': 'no subinput receipt files present; '
                     'pal-soak-subinput-receipt-v1 not exercised '
                     '(legacy rules only)'}))
    if isinstance(min_qualified, int):
        checks_out.append(check(
            'min_distinct_qualified_subinputs',
            PASS if total['distinct_qualified'] >= min_qualified else FAIL,
            {'required': min_qualified,
             'distinct_qualified': total['distinct_qualified'],
             'dedup_collapsed': total['dedup_collapsed'],
             'domain_overlaps': total['domain_overlaps'],
             'families': {family: acc['distinct_qualified']
                          for family, acc in sorted(families.items())},
             'qualified_segment': qualified_segment,
             'note': 'counts only rows individually re-verified by the '
                     'audit-side whitelist in passed rounds of the '
                     'R4-qualified segment; legacy '
                     'verified_distinct_inputs never contributes'}))
    else:
        checks_out.append(check('min_distinct_qualified_subinputs', UNKNOWN,
                                {'note': 'no qualified-subinput minimum '
                                         'provided'}))

    block = {
        'contract': subinputs.RECEIPT_SCHEMA,
        'qualified_segment': qualified_segment,
        'families': {family: dict(acc) for family, acc in sorted(families.items())},
        'total': total,
        'rejected_files': rejected_files[:50],
        'row_rejections': row_rejections[:50],
        'domain_overlap_events': overlap_events[:50],
        'pointer_problems': pointer_problems[:50],
        'unknown_fields_seen': sorted(unknown_fields_seen),
        'contract_copy_verified': contract_copy_verified,
        'legacy_verified_round_inputs': legacy_verified_round_inputs,
        'legacy_note': 'legacy metrics are frozen: verified_distinct_inputs '
                       'and receipt_declared_subinputs_sum never convert '
                       'into distinct_qualified and are never summed with it',
    }
    return block, checks_out


def audit_campaign(root: Path, *, config=None, scenarios=None, scenario_objects=None,
                   expected: dict | None = None, baseline: dict | None = None,
                   clock_tolerance: float = 5.0,
                   min_distinct_inputs: int | None = None,
                   min_distinct_qualified_subinputs: int | None = None):
    """Read-only adjudication of one campaign directory. Returns a report.

    ``config``: parsed SoakConfig config dict (master_seed, max_wall_seconds,
    minimum_valid_rounds, max_unobserved_gap_seconds, heartbeat_seconds...).
    ``scenarios``: ordered scenario names as the driver sorts them (sorted by
    name); when absent, loaded from the manifest the config names, if readable.
    ``expected``: {field: value} overrides checked against campaign.json.
    ``baseline``: {scenario_name: collected_test_count} for inventory closure.
    ``min_distinct_inputs``: required count of distinct logical inputs. The
    wall gate is a strict floor (no tolerance is deducted); only actually
    passed rounds count toward minimum_valid_rounds.
    ``min_distinct_qualified_subinputs``: required count of distinct
    qualified sub-inputs per the ``pal-soak-subinput-receipt-v1`` audit
    (decision 8); independent of the legacy gate above.
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
    unknown_fields_seen: set[str] = set()

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
            unknown_fields_seen.update(set(record) - KNOWN_ROUND_FIELDS)
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
    verified_keys: set[tuple] = set()
    for rno, entries in sorted(records.items()):
        for seg_name, record, round_dir in entries:
            if payload_hash_matches(record, round_dir, original_root):
                # executed-and-verified logical input identity
                verified_keys.add((record.get('scenario'), record.get('sub_seed')))
            else:
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
        'verified_distinct_inputs': len(verified_keys),
        'receipt_declared_subinputs_sum': receipt_subinputs,
        'pytest_test_invocations_sum': pytest_invocations,
        'pytest_logical_identities': pytest_identities,
    }
    # R1: a declared sub-input sum is not execution proof. Only distinct
    # logical inputs whose round records actually verify against the campaign
    # count toward the gate; with no required minimum the gate stays UNKNOWN
    # instead of an optimistic PASS.
    if isinstance(min_distinct_inputs, int):
        checks.append(check(
            'min_distinct_inputs',
            PASS if len(verified_keys) >= min_distinct_inputs else FAIL,
            {'required': min_distinct_inputs,
             'verified_distinct_inputs': len(verified_keys),
             'receipt_declared_subinputs_sum': receipt_subinputs,
             'functional_seed_distinct': len(functional_keys),
             'payload_level_distinct': len(payload_hashes),
             'note': 'declaration alone is not distinct-input proof; only '
                     'logical inputs verified against executed round records '
                     'count'}))
    else:
        checks.append(check('min_distinct_inputs', UNKNOWN,
                            {'note': 'no distinct-input minimum provided'}))

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
        # per-segment recount from records (drives the completion gates)
        seg_counts = {'passed': 0, 'failed': 0, 'interrupted': 0, 'unknown': 0}
        for rno, entries in records.items():
            for seg_name, record, _p in entries:
                if seg_name == segment.name and record.get('final') in seg_counts:
                    seg_counts[record['final']] += 1
        if close.get('reason') == 'complete':
            # R2: only actually passed rounds are valid rounds. Failed,
            # interrupted and unknown rounds never satisfy
            # minimum_valid_rounds, regardless of what the close summary sums.
            if isinstance(min_rounds, int):
                close_totals = close.get('rounds_total')
                claimed_passed = close_totals.get('passed', 0) \
                    if isinstance(close_totals, dict) else 0
                if not isinstance(claimed_passed, int):
                    claimed_passed = 0
                if seg_counts['passed'] < min_rounds:
                    checks.append(check('false_completion', FAIL,
                                        {'segment': segment.name,
                                         'passed_recounted': seg_counts['passed'],
                                         'passed_claimed': claimed_passed,
                                         'required_valid_rounds': min_rounds,
                                         'note': 'only actually passed rounds count '
                                                 'toward minimum_valid_rounds'}))
            # R3: the wall gate is a strict floor — no tolerance is deducted
            # from max_wall_seconds, so a 120-second deficit on a 72-hour gate
            # is a FAIL, not a met target.
            if isinstance(max_wall, (int, float)) and span is not None:
                if span < max_wall:
                    checks.append(check('short_observation', FAIL,
                                        {'segment': segment.name,
                                         'observed_span': span,
                                         'max_wall_seconds': max_wall,
                                         'deficit_seconds': round(max_wall - span, 3),
                                         'note': 'strict wall floor; no tolerance '
                                                 'deducted'}))
                else:
                    checks.append(check('observation_span', PASS,
                                        {'segment': segment.name, 'observed_span': span}))
            elif isinstance(max_wall, (int, float)):
                checks.append(check('observation_span', UNKNOWN,
                                    {'segment': segment.name, 'note': 'span not computable'}))
        claimed_totals = close.get('rounds_total')
        if isinstance(claimed_totals, dict) and claimed_totals != seg_counts:
            checks.append(check('summary_mismatch', FAIL,
                                {'segment': segment.name, 'claimed': claimed_totals,
                                 'recounted': seg_counts}))
        else:
            checks.append(check('summary_consistency', PASS, {'segment': segment.name,
                                                              'recounted': seg_counts}))

    # R4: a "complete" close receipt must belong to the LATEST closed
    # segment. An older segment's complete receipt combined with a newer
    # segment's start/stop times, identity or rounds is a cross-segment
    # splice and can never support a normal-end verdict.
    latest_segment = segment_dirs[-1] if segment_dirs else None
    complete_segment_names = [segment.name for segment in segment_dirs
                              if (drv._read_json(segment / 'segment-close.json')
                                  or {}).get('reason') == 'complete']
    if complete_segment_names:
        spliced = [name for name in complete_segment_names
                   if latest_segment is None or name != latest_segment.name]
        if spliced:
            checks.append(check('completion_segment_binding', FAIL,
                                {'complete_segments': complete_segment_names,
                                 'latest_segment': latest_segment.name
                                 if latest_segment else None,
                                 'spliced_receipts': spliced,
                                 'note': 'complete receipt not bound to the latest '
                                         'closed segment; receipt, start/stop times, '
                                         'identity and rounds must come from one '
                                         'continuous closed segment'}))
        else:
            checks.append(check('completion_segment_binding', PASS,
                                {'segment': latest_segment.name,
                                 'note': 'receipt, span, identity and rounds bound '
                                         'to the same latest closed segment'}))
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

    # ---------- subinput receipts (pal-soak-subinput-receipt-v1) ----------
    # Decision 8 qualification scope: only the R4-qualified segment (the
    # latest closed segment whose complete receipt is not spliced) feeds
    # distinct_qualified; every other file is still read, verified and
    # reported, it just never qualifies.
    qualified_segment = None
    if latest_segment is not None and complete_segment_names == [latest_segment.name]:
        qualified_segment = latest_segment.name
    report['subinputs'], subinput_checks = _audit_subinputs(
        records, identity, cfg, qualified_segment,
        min_distinct_qualified_subinputs, unknown_fields_seen,
        len(verified_keys))
    checks.extend(subinput_checks)

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
    audit.add_argument('--min-distinct-inputs', type=int)
    audit.add_argument('--min-distinct-qualified-subinputs', type=int)
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
                                min_distinct_inputs=args.min_distinct_inputs,
                                min_distinct_qualified_subinputs=
                                args.min_distinct_qualified_subinputs)
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
