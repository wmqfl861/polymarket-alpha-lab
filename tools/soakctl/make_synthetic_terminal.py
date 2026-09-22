"""Build small SYNTHETIC soak terminal states for validating
tools/soakctl/run_final_review.py (distributable parameterization,
PAL_RV05_CLOSURE_20260922 node N2; recipe unchanged from the
PAL_CORRECTIVE_LONGTASK_20260921_V3 builder, paths made explicit args).

Reads a finished rehearsal/soak campaign (any campaign the frozen driver
produced; stop_file or complete closure both work) and constructs, under
the --out directory, two clearly-synthetic copies:

- ``synth-b-fullpass/campaign`` - a "full pass" terminal state: the source
  config is tightened to small gates the achieved evidence exceeds
  (--min-rounds, --max-wall-seconds; defaults 5 / 600 like the V3 builder,
  which assumed a multi-hour source campaign - pass the values that fit
  YOUR source campaign) so a strict final review invoked with matching
  small --min-rounds/--min-distinct-inputs must PASS; the copy's
  campaign.json and segment.json config_sha256 are recomputed consistently
  through the frozen SoakConfig canonical form, the single segment's close
  receipt is rewritten to a ``complete`` reason (rounds_total left matching
  the true recount, span and stopped_wall left real), targets_met corrected
  to min_rounds=true, and the consumed stop file removed - the shape a
  normal completed run leaves.
- ``synth-b2-incomplete/campaign`` - the same copy with segment-close.json
  removed: a half-written terminal state that must adjudicate as
  SNAPSHOT_INCOMPLETE -> verdict UNKNOWN, never FAIL-as-corruption.

Everything is written under --out; the source campaign is never modified.
The frozen tree is imported read-only for canonical hashing.

Usage (placeholders - no host paths are baked into this tool):

  python -I -B tools/soakctl/make_synthetic_terminal.py ^
    --campaign <finished campaign dir> ^
    --config <the campaign's config.json> ^
    --out <evidence out dir> ^
    --frozen-tree <repo root (default: this file's repo root)> ^
    [--min-rounds 5] [--max-wall-seconds 600]

Exit 0 when the full-pass copy passes its integrity spot checks, 1 else.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

DEFAULT_FROZEN_TREE = Path(__file__).resolve().parents[2]


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':')) + '\n'


def write_json(path: Path, doc) -> None:
    path.write_text(canon(doc), encoding='utf-8')


def load_frozen(frozen_tree: Path):
    root = Path(frozen_tree).resolve()
    if not (root / 'tests' / 'support' / 'soak_audit.py').is_file():
        raise SystemExit(f'make_synthetic_terminal: frozen tree not found at {root}')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tests.support import soak_closeout as clo  # noqa: E402
    from tests.support import soak_driver as drv  # noqa: E402
    return clo, drv


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='make_synthetic_terminal')
    parser.add_argument('--campaign', required=True,
                        help='finished source campaign directory (read-only)')
    parser.add_argument('--config', required=True,
                        help='config.json the source campaign ran under')
    parser.add_argument('--out', required=True,
                        help='output directory (created; never inside the campaign)')
    parser.add_argument('--frozen-tree', default=str(DEFAULT_FROZEN_TREE))
    parser.add_argument('--min-rounds', type=int, default=5,
                        help='tightened minimum_valid_rounds (default 5)')
    parser.add_argument('--max-wall-seconds', type=float, default=600,
                        help='tightened max_wall_seconds (default 600; must not '
                             'exceed the source campaign observation span)')
    args = parser.parse_args(argv)

    src_campaign = Path(args.campaign)
    src_config = Path(args.config)
    out_root = Path(args.out)
    clo, drv = load_frozen(Path(args.frozen_tree))
    if src_campaign.is_dir() and clo.write_target_rejected(src_campaign, out_root):
        raise SystemExit('make_synthetic_terminal: refusing out inside campaign')

    out_b = out_root / 'synth-b-fullpass'
    out_b2 = out_root / 'synth-b2-incomplete'
    for stale in (out_b, out_b2):
        if stale.exists():
            shutil.rmtree(stale)
    out_b.mkdir(parents=True)
    out_b2.mkdir(parents=True)

    # ---- synthetic tightened config (small gates for a small campaign) ----
    config = json.loads(src_config.read_text(encoding='utf-8'))
    config['minimum_valid_rounds'] = args.min_rounds
    config['max_wall_seconds'] = args.max_wall_seconds
    config['candidate'] = dict(config['candidate'])
    config['candidate']['purpose'] = 'final-review-prep synthetic full-pass state'
    synth_config_path = out_b / 'config-synth.json'
    synth_config_path.write_text(canon(config), encoding='utf-8')
    new_config_sha = drv._sha256_bytes(
        drv.SoakConfig.from_dict(config).canonical_bytes())

    changes: dict = {'source_campaign': str(src_campaign),
                     'source_config': str(src_config),
                     'synth_config': str(synth_config_path),
                     'new_config_sha256': new_config_sha,
                     'config_changes': {'minimum_valid_rounds': args.min_rounds,
                                        'max_wall_seconds': args.max_wall_seconds}}

    # ---- build the full-pass copy ----
    camp_b = out_b / 'campaign'
    shutil.copytree(src_campaign, camp_b)
    identity = json.loads((camp_b / 'campaign.json').read_text(encoding='utf-8'))
    identity['config_sha256'] = new_config_sha
    write_json(camp_b / 'campaign.json', identity)
    seg = camp_b / 'segments' / 'segment-000001'
    header = json.loads((seg / 'segment.json').read_text(encoding='utf-8'))
    header['config_sha256'] = new_config_sha
    write_json(seg / 'segment.json', header)
    close = json.loads((seg / 'segment-close.json').read_text(encoding='utf-8'))
    changes['close_before'] = {'reason': close.get('reason'),
                               'rounds_total': close.get('rounds_total'),
                               'stopped_wall': close.get('stopped_wall'),
                               'targets_met': close.get('targets_met')}
    close['reason'] = 'complete'
    close['targets_met'] = {'min_rounds': True}
    write_json(seg / 'segment-close.json', close)
    stop = camp_b / 'stop'
    if stop.is_file():
        stop.unlink()
        changes['stop_file_removed'] = True
    write_json(out_b / 'BUILD_RECORD.json', changes)
    print('built', camp_b)

    # ---- build the snapshot-incomplete variant from the full-pass copy ----
    camp_b2 = out_b2 / 'campaign'
    shutil.copytree(camp_b, camp_b2)
    (camp_b2 / 'segments' / 'segment-000001' / 'segment-close.json').unlink()
    shutil.copy2(synth_config_path, out_b2 / 'config-synth.json')
    write_json(out_b2 / 'BUILD_RECORD.json', {
        'derived_from': str(camp_b),
        'change': 'segment-close.json removed (half-written terminal state '
                  'shape: segment left unclosed, no live pid)'})
    print('built', camp_b2)

    # ---- integrity spot checks on the full-pass copy ----
    snap = clo.campaign_snapshot(camp_b)
    checks = {
        'complete_receipt_on_latest': clo.complete_receipt(snap),
        'latest_segment': clo.latest_segment_name(snap),
        'close_reason': snap.get('close_reasons', {}).get('segment-000001'),
        'stop_file_gone': not (camp_b / 'stop').exists(),
        'identity_config_sha': json.loads(
            (camp_b / 'campaign.json').read_text(encoding='utf-8')
        ).get('config_sha256') == new_config_sha,
        'segment_config_sha': json.loads(
            (seg / 'segment.json').read_text(encoding='utf-8')
        ).get('config_sha256') == new_config_sha,
    }
    write_json(out_b / 'BUILD_CHECKS.json', checks)
    print(json.dumps(checks, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
