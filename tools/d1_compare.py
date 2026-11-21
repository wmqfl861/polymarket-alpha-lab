#!/usr/bin/env python3
"""D1 comparison operator entry (precedent: tools/wp_*.py).

The stage command verifies the frozen mirror scripts and registers the
original per-leg Git archives and their manifest in managed PostgreSQL.
Import-original verifies retained stage-v1 evidence against an independent
manifest digest and registers the exact original bytes and a receipt.
The restore command verifies those bytes by manifest digest and tests
extraction in a temporary scope. Capture-inputs records public market
responses for a UTC window in the managed snapshot store. Build-packs and
diff process local inputs. Run-frozen and run-new require the plan section 5 authorization
and the approved execution wiring.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import signal
import sys
from pathlib import Path

_REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from polymarket_alpha_lab.papersim.d1compare import (  # noqa: E402
    diff as d1_diff,
    frozenrun as d1_frozenrun,
    journal as d1_journal,
    packbuild as d1_packbuild,
)

DEFAULT_MIRROR_ROOT = Path(__file__).resolve().parents[1] / \
    "docs/paper-sim/server-mirror"


def _load_json(path: str) -> object:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise SystemExit(f"d1_compare: cannot read {path}: {error}")
    except json.JSONDecodeError as error:
        raise SystemExit(f"d1_compare: invalid JSON in {path}: {error}")
    return document


# ---------------------------------------------------------------------------
# stage
# ---------------------------------------------------------------------------


def command_stage(args: argparse.Namespace) -> int:
    legs = ["soccer", "crypto"] if args.leg == "all" else [args.leg]
    receipt = d1_frozenrun.stage_archives(
        legs, mirror_root=Path(args.mirror_root),
        staging_root=Path(args.staging_root), repo_root=Path(args.repo_root))
    print(json.dumps(receipt, sort_keys=True))
    return 0


def command_import_original(args: argparse.Namespace) -> int:
    receipt = d1_frozenrun.import_original_archives(
        args.trusted_manifest_sha256, repo_root=Path(args.repo_root), mirror_root=Path(args.mirror_root),
        manifest_file=Path(args.manifest_file) if args.manifest_file is not None else None,
        archive_files=args.archive_file, archive_sha256s=args.archive_sha256,
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0


def command_restore(args: argparse.Namespace) -> int:
    receipt = d1_frozenrun.restore_archives(
        args.manifest_sha256,
        staging_root=Path(args.staging_root), repo_root=Path(args.repo_root))
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _capture_time(value: str) -> datetime.datetime:
    try:
        stamp = datetime.datetime.fromisoformat(value)
        if stamp.utcoffset() is not None:
            return stamp.astimezone(datetime.timezone.utc)
    except (ValueError, OverflowError):
        pass
    raise argparse.ArgumentTypeError("expected an ISO 8601 timestamp with a UTC offset")


def command_capture_inputs(args: argparse.Namespace) -> int:
    from polymarket_alpha_lab.papersim.d1compare import acquire

    try:
        receipt = acquire.capture_inputs(
            repo_root=Path(args.repo_root),
            window_start=args.window_start,
            window_end=args.window_end,
            tag_slugs=tuple(args.tag_slug),
            allow_public_fetch=args.allow_public_fetch,
        )
    except acquire.AcquisitionError as error:
        print(f"d1_compare: capture-inputs failed: {error.code}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, allow_nan=False))
    return 0


# ---------------------------------------------------------------------------
# run-frozen / run-new (gate-refusing run steps)
# ---------------------------------------------------------------------------


def _authorization(args: argparse.Namespace) -> d1_frozenrun.DayRunAuthorization | None:
    if not args.authorization:
        return None
    document = _load_json(args.authorization)
    if not isinstance(document, dict):
        raise SystemExit("d1_compare: authorization document must be an object")
    return d1_frozenrun.DayRunAuthorization.from_mapping(document)


def _refuse_gates(authorization: d1_frozenrun.DayRunAuthorization | None) -> None:
    unmet = d1_frozenrun.check_run_gates(authorization)
    if unmet:
        print("d1_compare: run refused -- plan section 5 gates unmet:",
              file=sys.stderr)
        for name in unmet:
            print(f"  - {name}", file=sys.stderr)
        raise SystemExit(2)


def command_run_frozen(args: argparse.Namespace) -> int:
    if args.preflight:
        if args.authorization is not None:
            raise d1_frozenrun.FrozenRunError("preflight does not accept run authorization")
        if not args.manifest_sha256 or not args.batch_sha256:
            raise d1_frozenrun.FrozenRunError("preflight requires a manifest and a captured batch")
        receipt = d1_frozenrun.preflight_designated_chain(
            args.leg, mirror_root=Path(args.mirror_root),
            staging_root=Path(args.staging_root), repo_root=Path(args.repo_root),
            manifest_sha256=args.manifest_sha256, batch_sha256=args.batch_sha256,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt["status"] == "ready" else 2
    if args.batch_sha256 is not None:
        raise d1_frozenrun.FrozenRunError("--batch-sha256 requires --preflight")
    authorization = _authorization(args)
    _refuse_gates(authorization)
    d1_frozenrun.execute_designated_chain(
        args.leg, mirror_root=Path(args.mirror_root),
        staging_root=Path(args.staging_root),
        repo_root=Path(args.repo_root), authorization=authorization,
        manifest_sha256=args.manifest_sha256)
    return 0  # unreachable in this pass (the executor gate refuses first)


def command_run_new(args: argparse.Namespace) -> int:
    authorization = _authorization(args)
    _refuse_gates(authorization)
    scan_config = _load_json(args.scan_config)
    if not isinstance(scan_config, dict):
        raise SystemExit("d1_compare: scan config must be an object")
    # The authorized run step invokes ONLY the existing reviewed command:
    #   python -m polymarket_alpha_lab.papersim.cli run-scan
    #       --config <scan_config> --managed --repo-root <repo>
    # Journal/pack digests register through snapshots.register_snapshot
    # inside the same reviewed path.  This pass never executes it.
    argv = [sys.executable, "-m", "polymarket_alpha_lab.papersim.cli",
            "run-scan", "--config", str(Path(args.scan_config).resolve()),
            "--managed", "--repo-root", str(Path(args.repo_root).resolve())]
    plan = {"run_new_version": 1, "argv": argv,
            "gates": authorization.as_mapping() if authorization else {}}
    print(json.dumps(plan, indent=1, sort_keys=True))
    print("d1_compare: run-new wiring executor is not part of this "
          "preparation pass; nothing was executed", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# build-packs
# ---------------------------------------------------------------------------


def command_build_packs(args: argparse.Namespace) -> int:
    journal_document = _load_json(args.journal)
    if not isinstance(journal_document, dict):
        raise SystemExit("d1_compare: journal document must be an object")
    entries = d1_journal.entries_from_document(journal_document)
    params = _load_json(args.params)
    if not isinstance(params, dict):
        raise SystemExit("d1_compare: pack params must be an object")
    league_keys = params.get("league_keys") or {}
    market_plans = []
    plan_fields = {field.name for field in
                   dataclasses.fields(d1_packbuild.MarketPlan)}
    for raw in params.get("markets") or []:
        if not isinstance(raw, dict):
            raise SystemExit("d1_compare: each market entry must be an object")
        unknown = sorted(set(raw) - plan_fields)
        if unknown:
            raise SystemExit(f"d1_compare: unknown MarketPlan fields: "
                             f"{','.join(unknown)}")
        market_plans.append(d1_packbuild.MarketPlan(**raw))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for pack_raw in params.get("packs") or []:
        allowed = {"queue", "team", "lineage", "model_name", "market_type",
                   "source_kind"}
        spec = d1_packbuild.PackSpec(
            **{key: value for key, value in pack_raw.items() if key in allowed})
        pack_market_ids = [str(mid) for mid in pack_raw.get("markets") or []]
        plans = [market for market in market_plans
                 if market.market_id in set(pack_market_ids)]
        observations = d1_packbuild.observations_from_journal(
            entries, [market.market_id for market in plans],
            league_keys=league_keys)
        records, provenance = d1_packbuild.build_pack(
            observations, plans, spec, journal_entries=entries)
        document = d1_packbuild.pack_document(records, provenance)
        d1_packbuild.verify_pack_document(document)
        pack_path = out_dir / f"{pack_raw.get('name', spec.queue)}.pack.json"
        pack_path.write_text(json.dumps(document["records"], indent=1),
                             encoding="utf-8")
        provenance_path = out_dir / f"{pack_raw.get('name', spec.queue)}.provenance.json"
        provenance_path.write_text(
            json.dumps(document["provenance"], indent=1, sort_keys=True),
            encoding="utf-8")
        print(f"built {pack_path} records={len(records)} "
              f"pack_sha256={provenance.pack_sha256}")
    return 0


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def command_diff(args: argparse.Namespace) -> int:
    left_raw = _load_json(args.path_l)
    right_raw = _load_json(args.path_n)
    if not isinstance(left_raw, list) or not isinstance(right_raw, list):
        raise SystemExit("d1_compare: candidate documents must be JSON arrays")
    left = [d1_diff.row_from_mapping(raw, provenance="path_l:frozen-chain")
            for raw in left_raw]
    right = [d1_diff.row_from_mapping(raw, provenance="path_n:run-scan")
             for raw in right_raw]
    report = d1_diff.diff_day(left, right)
    document = json.dumps(report.as_document(), indent=1, sort_keys=True)
    if args.out == "-":
        print(document)
    else:
        Path(args.out).write_text(document, encoding="utf-8")
        print(f"wrote {args.out}")
    summary = report.summary()
    print("rows={rows} agreement_fields={agreement_fields}/{compared_fields} "
          "classes={class_counts} captured_not_compared={captured_not_compared}"
          .format(**summary), file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="d1_compare.py",
        description="D1 shared-capture paper comparison and managed frozen archive")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser(
        "stage", help="verify and register the designated frozen libraries")
    stage.add_argument("--mirror-root", default=str(DEFAULT_MIRROR_ROOT),
                       help="server-mirror root (default: docs/paper-sim/server-mirror)")
    stage.add_argument("--staging-root", required=True,
                       help="operator staging directory (scratch space is private and temporary)")
    stage.add_argument("--repo-root", default=".",
                       help="project root with local Git objects and managed PostgreSQL (default: .)")
    stage.add_argument("--leg", choices=["soccer", "crypto", "all"],
                       default="all", help="which designated chain to stage")
    stage.set_defaults(handler=command_stage)

    original = subparsers.add_parser(
        "import-original", help="verify and register retained stage-v1 archives")
    original.add_argument("--repo-root", required=True)
    original.add_argument("--mirror-root", default=str(DEFAULT_MIRROR_ROOT))
    original.add_argument("--trusted-manifest-sha256", required=True,
                          help="independently retained digest of the original stage-v1 JSON bytes")
    original.add_argument("--manifest-file", help="original JSON file (default: managed snapshot by digest)")
    original.add_argument("--archive-file", action="append", default=[], metavar="LEG=PATH")
    original.add_argument("--archive-sha256", action="append", default=[], metavar="LEG=SHA256")
    original.set_defaults(handler=command_import_original)

    restore = subparsers.add_parser(
        "restore", help="verify and temporarily extract a registered frozen archive")
    restore.add_argument("--manifest-sha256", required=True)
    restore.add_argument("--staging-root", required=True)
    restore.add_argument("--repo-root", default=".")
    restore.set_defaults(handler=command_restore)

    capture = subparsers.add_parser(
        "capture-inputs", help="record public Gamma and CLOB responses for a UTC window")
    capture.add_argument("--repo-root", default=".", help="project root with managed PostgreSQL")
    capture.add_argument("--window-start", required=True, type=_capture_time,
                         help="inclusive start of the capture window")
    capture.add_argument("--window-end", required=True, type=_capture_time,
                         help="exclusive end of the capture window")
    capture.add_argument("--tag-slug", required=True, action="append",
                         help="Gamma tag to discover (repeat for additional tags)")
    capture.add_argument("--allow-public-fetch", action="store_true",
                         help="enable the public market data adapters")
    capture.set_defaults(handler=command_capture_inputs)

    run_frozen = subparsers.add_parser(
        "run-frozen", help="execute Path L (refused unless every plan "
                           "section 5 run gate is recorded)")
    run_frozen.add_argument("--leg", choices=["soccer", "crypto"], required=True)
    run_frozen.add_argument("--mirror-root", default=str(DEFAULT_MIRROR_ROOT))
    run_frozen.add_argument("--staging-root", required=True)
    run_frozen.add_argument("--repo-root", default=".")
    run_frozen.add_argument("--manifest-sha256")
    run_frozen.add_argument("--preflight", action="store_true",
                            help="verify and record captured inputs without starting a frozen script")
    run_frozen.add_argument("--batch-sha256", help="managed capture batch to verify in preflight")
    run_frozen.add_argument("--authorization", default=None,
                            help="JSON document recording the plan section 5 "
                                 "gates (b2_b3_b4_merged_commit, g7_fix_evidence, "
                                 "d1_lane_reviewed_merge_commit, "
                                 "s1_scheduler_install_record, "
                                 "execution_base_commit)")
    run_frozen.set_defaults(handler=command_run_frozen)

    build_packs = subparsers.add_parser(
        "build-packs", help="pure journal-to-pack building (offline)")
    build_packs.add_argument("--journal", required=True,
                             help="journal staging document (journal_version 1)")
    build_packs.add_argument("--params", required=True,
                             help="day params JSON: packs (queue/team/lineage/"
                                  "model_name/markets/name) + markets (MarketPlan "
                                  "fields) + league_keys")
    build_packs.add_argument("--out-dir", required=True,
                             help="output directory for pack and provenance files")
    build_packs.set_defaults(handler=command_build_packs)

    run_new = subparsers.add_parser(
        "run-new", help="invoke the existing run-scan on built packs (same "
                        "gates; never executed by this pass)")
    run_new.add_argument("--scan-config", required=True,
                         help="run-scan --config JSON document (existing CLI)")
    run_new.add_argument("--repo-root", default=".")
    run_new.add_argument("--authorization", default=None,
                         help="JSON document recording the plan section 5 gates")
    run_new.set_defaults(handler=command_run_new)

    differ = subparsers.add_parser(
        "diff", help="candidate-by-candidate Path L vs Path N comparison (offline)")
    differ.add_argument("--path-l", required=True,
                        help="Path L candidates JSON array")
    differ.add_argument("--path-n", required=True,
                        help="Path N candidates JSON array")
    differ.add_argument("--out", default="-",
                        help="output path for the diff document (- = stdout)")
    differ.set_defaults(handler=command_diff)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except d1_frozenrun.FrozenRunError as error:
        print(f"d1_compare: {args.command} refused: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        if args.command not in ("stage", "import-original", "restore", "run-frozen", "capture-inputs"):
            raise
        print(f"d1_compare: {args.command} failed: {type(error).__name__}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _terminate(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    raise SystemExit(main())
