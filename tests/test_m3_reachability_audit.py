"""M3 B0 reachability-audit locks -- hard prerequisite gate for FX-18 batches B1-B8.

This module locks the B0 baseline artifacts produced by
``docs/handoffs/m3-cleanup/inventory.py`` (plan m3-plan-formal.md sections
3.1-3.4) and re-proves the live source tree against them:

* the four baseline TSV artifacts, ``prune-manifest.json`` and the audit tool
  itself are locked by SHA256 -- they are immutable baseline evidence;
* U0 / D0 canonical hashes and counts must equal the plan locks;
* the eight batch manifests must exactly partition D0 with the plan's
  per-batch counts, line totals and canonical module-list hashes (the file
  lists stay locked even after an ``executed`` marker is added);
* the retained closure must stay leak-free (no retained/K0 consumer of any
  D0 module, no cross-family cycle, no UNKNOWN dynamic reference on retained
  surfaces);
* ``inventory.py --verify`` re-proofs the current tree: no deletion outside
  approved batch manifests, batch deletions are atomic, the reachable set is
  unchanged (R_before == R_after), no D0/K0 file drifted, and unreachable
  membership matches the pending candidate set except for ADDITIVE new
  modules that no frozen batch manifest claims (FX-18 PR-A landing
  adaptation, 2026-11-20: at the first refreshed parent 3e52516f the only
  such additive drift was 22 new ``papersim.*`` modules -- a plan-3.1
  protected subpackage; canonical-parent refresh 2026-11-20: canonical main
  advanced through the PR#85 merge 86feaf1a (MO-5d crypto family) and the
  refreshed inventory run was re-executed at that parent into
  ``refreshed-20261120-86feaf1a/``, where the additive drift is 23
  ``papersim.*`` modules -- the prior 22 plus
  ``papersim.optimize.families.crypto``; every other verify() failure class
  stays fatal, and this test never approves or performs any deletion).

After EVERY B1-B8 deletion batch this suite MUST be re-run and pass on the
batch's parent commit before the next batch starts (plan 3.4 step 4). A
failure here means the baseline drifted and must be consciously re-derived
and re-reviewed, not silenced.

paper-only / report-only / readonly: this test reads the repository as data
and never imports project code.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
E_DIR = REPO_ROOT / "docs" / "handoffs" / "m3-cleanup"

BASELINE_COMMIT = "4c8bec2fdce31fec3a8ec06a6ab93a25e4d7fc69"

# Plan locks (m3-plan-formal.md sections 1.2 / 3.2 / 4.1).
PLAN_U0_COUNT = 2438
PLAN_U0_SHA256 = "6a35d24c748cfd26880401cc6dab84eb36a327901cc2896c0975adfff51c1f78"
PLAN_D0_COUNT = 2395
PLAN_D0_SHA256 = "f32335000022ea6b8bb5c5466e8d3ab362f475fed6b000c6f75385989ad676b8"
PLAN_K0_COUNT = 43
PLAN_REACHABLE_COUNT = 429
PLAN_U0_LINES = 2315298
PLAN_REACHABLE_LINES = 167641
PLAN_EXPORTS = {"names": 369, "providers": 76, "reachable": 165,
                "retained": 33, "to_archive": 171}

BATCH_COUNTS = {"B1": 1114, "B2": 399, "B3": 332, "B4": 114,
                "B5": 65, "B6": 90, "B7": 98, "B8": 183}
BATCH_LINES = {"B1": 1271697, "B2": 402380, "B3": 257173, "B4": 106707,
               "B5": 63194, "B6": 46510, "B7": 37446, "B8": 110879}
BATCH_EXPORTS = {"B1": 0, "B2": 0, "B3": 0, "B4": 6,
                 "B5": 0, "B6": 0, "B7": 9, "B8": 156}

# Canonical SHA256 over the sorted module list of each batch's file set
# (LF, trailing newline) -- survives later ``executed`` marker edits.
BATCH_MODULE_SHA256 = {
    "B1": "6f84c6e067580403d30371ee1a866d99ae8810a795a4d9b65ad07b7a837e2ff7",
    "B2": "3452806855635e0f7db160a9a3bac21e38dfb518b2df22fdf9a8df0ffeb877cc",
    "B3": "250884d04a8204ecf2c0c99d8f3aea9273f0d28d4154f0931770bb3b018ec323",
    "B4": "bf667bd8351ca7ea0fdc8fbfe96a8cdc763e7a943aa30686cff4bc3f9f6e27df",
    "B5": "59206e04855e803dcbf74d329da3ac6b4dbc0a0c793ea7a867bdac5e90c7eff9",
    "B6": "0c8add742bffc2d1c79b07c0612e8ad132bc374bc04e65103ef5530b7496633e",
    "B7": "215b756276e856533bd2cc2a7ee513ac026fd8a26ee717410e7bcc92b49e6c71",
    "B8": "a6e222433c8838d4f589c5f6d588cdf7632dca0fad8999dc5df5c1be4fb97e52",
}

# Immutable baseline artifacts (B0 generation at BASELINE_COMMIT).
BASELINE_ARTIFACT_SHA256 = {
    "inventory.py":
        "ac4609dd7547efae6e3a07777794b881d27fb9246ca9c7fa1a8ec30198f8c656",
    "source-inventory.tsv":
        "59af7faa99153debc07ec150206bfab97cd09d9ff2f40fd6149a8a37852479f5",
    "retained-modules.tsv":
        "2a72f80fc8bf2a166525659a75d3393d8a6bc21c87a9f23b5b95cbfa49fbfe94",
    "root-exports.tsv":
        "6066a73366bcb4f8545abde62729ff8fcd95bcdb954b056c14fe8ed05c51857f",
    "test-retirement.tsv":
        "1168f4a87b6f90c55f18bb80a37cf1412a5b2d8de311a27239967975fbc497c4",
    "prune-manifest.json":
        "0c52b3012b94556e27a58751707303cbc8426aca661b62075307a6b7f069cb9c",
}

SOURCE_INVENTORY_ROWS = 2868
RETAINED_TSV_KINDS = {"seed_broker": 13, "k0_retained": 43}
RETAINED_TSV_TOTAL_ROWS = 97  # seed_broker + k0_retained + protected_reachable

# Canonical-parent refreshed run (FX-18 PR-A preparation refresh, 2026-11-20).
# The release node requires the PR-A base to be latest canonical main; the
# first refreshed run (docs/handoffs/m3-cleanup/refreshed-20261120/, parent
# 3e52516f, PR#84 merge) was superseded after canonical main advanced through
# the PR#85 merge 86feaf1a ("MO-5d crypto GBM optimize family"). The proven
# generator invocation was re-executed at the new parent with only the --out
# directory adjusted to encode the new parent SHA (additive-only; the prior
# run directory stays untouched as historical evidence). At 86feaf1a the
# additive drift since the B0 baseline is 23 papersim.* modules (the prior
# 22 plus the new MO-5d module papersim.optimize.families.crypto).
REFRESHED_PARENT_DIR = E_DIR / "refreshed-20261120-86feaf1a"
REFRESHED_PARENT_COMMIT = "86feaf1ad88be3f9c262a26cdac650cb26c1a573"
MO5D_NEW_MODULE = "polymarket_alpha_lab.papersim.optimize.families.crypto"
MO5D_NEW_TEST = "tests/test_papersim_optimize_crypto_family.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(names) -> str:
    return hashlib.sha256(("\n".join(sorted(names)) + "\n").encode("utf-8")).hexdigest()


def _prune() -> dict:
    return json.loads((E_DIR / "prune-manifest.json").read_text(encoding="utf-8"))


def _batch(batch: str) -> dict:
    return json.loads((E_DIR / f"{batch}-manifest.json").read_text(encoding="utf-8"))


def _tsv_rows(name: str) -> list[list[str]]:
    text = (E_DIR / name).read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in text[1:] if line.strip()]


def _load_inventory_module():
    path = E_DIR / "inventory.py"
    assert path.is_file(), f"missing audit tool: {path}"
    spec = importlib.util.spec_from_file_location("m3_b0_inventory", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_artifacts_are_locked() -> None:
    for name, expected in BASELINE_ARTIFACT_SHA256.items():
        path = E_DIR / name
        assert path.is_file(), f"missing baseline artifact: {path}"
        actual = _sha256(path)
        assert actual == expected, (
            f"{name} drifted: {actual} != locked {expected}; the B0 baseline "
            "artifacts are immutable evidence and must be consciously "
            "re-derived and re-reviewed, never edited in place")


def test_prune_manifest_matches_plan_locks() -> None:
    prune = _prune()
    reach = prune["reachability"]
    assert prune["plan_locks_all_match"] is True
    assert prune["lock_failures"] == {}
    assert prune["baseline"]["commit"] == BASELINE_COMMIT

    u0 = reach["u0_modules"]
    assert len(u0) == PLAN_U0_COUNT
    assert _canonical_sha256(u0) == PLAN_U0_SHA256
    assert reach["u0_lines"] == PLAN_U0_LINES

    d0_modules = [entry["module"] for entry in reach["d0"]]
    assert len(d0_modules) == PLAN_D0_COUNT
    assert len(set(d0_modules)) == PLAN_D0_COUNT
    assert _canonical_sha256(d0_modules) == PLAN_D0_SHA256

    assert len(reach["k0"]) == PLAN_K0_COUNT
    assert {entry["module"] for entry in reach["k0"]} <= set(u0)
    assert reach["reachable_count"] == PLAN_REACHABLE_COUNT
    assert len(reach["reachable_modules"]) == PLAN_REACHABLE_COUNT
    assert reach["reachable_lines"] == PLAN_REACHABLE_LINES

    hist = prune["historical_tsv"]
    assert hist["module_set_equal_to_recomputed_u0"] is True
    assert hist["sha256_crlf"] == hist["plan_sha256"], (
        "historical TSV file-level checksum must reproduce under the recorded "
        "LF->CRLF normalization, otherwise the plan baseline has drifted")

    tool = prune["tool"]
    assert tool["sha256"] == BASELINE_ARTIFACT_SHA256["inventory.py"]
    assert tool["sha256"] == _sha256(E_DIR / "inventory.py")


def test_batch_manifests_exactly_partition_d0() -> None:
    prune = _prune()
    d0 = {entry["module"]: entry for entry in prune["reachability"]["d0"]}
    seen: dict[str, str] = {}
    exports_to_retire: set[str] = set()
    for batch, expected_count in BATCH_COUNTS.items():
        manifest = _batch(batch)
        assert manifest["batch"] == batch
        assert manifest["baseline_commit"] == BASELINE_COMMIT
        assert manifest["tool_sha256"] == BASELINE_ARTIFACT_SHA256["inventory.py"]
        assert manifest["executed"] is False or "executed_commit" in manifest
        modules = [entry["module"] for entry in manifest["files"]]
        assert len(modules) == expected_count, f"{batch} file count drifted"
        assert _canonical_sha256(modules) == BATCH_MODULE_SHA256[batch], (
            f"{batch} module list drifted from the locked batch manifest")
        assert manifest["counts"]["lines"] == BATCH_LINES[batch]
        assert manifest["counts"]["root_exports"] == BATCH_EXPORTS[batch]
        assert manifest["dependency_check"]["verdict"] in ("PASS", "PAUSE")
        for entry, module in zip(manifest["files"], modules):
            assert entry["path"] == f"src/{module.replace('.', '/')}.py"
            baseline = d0[module]
            assert entry["sha256"] == baseline["sha256"]
            assert entry["lines"] == baseline["lines"]
            assert entry["covered_by_retained_closure"] is False
            assert module not in seen, f"{module} claimed by {seen[module]} and {batch}"
            seen[module] = batch
        exports_to_retire |= set(manifest["exports_to_retire"])
    assert seen.keys() == d0.keys(), "batch manifests must cover D0 exactly"
    assert len(exports_to_retire) == PLAN_EXPORTS["to_archive"]

    for batch in BATCH_COUNTS:
        manifest = _batch(batch)
        assert manifest["verification_requirements"], batch


def test_root_exports_inventory_locked() -> None:
    rows = _tsv_rows("root-exports.tsv")
    assert len(rows) == PLAN_EXPORTS["names"]
    header = (E_DIR / "root-exports.tsv").read_text(encoding="utf-8").splitlines()[0]
    for column in ("export_name", "provider_module", "original_symbol",
                   "status", "batch"):
        assert column in header
    statuses = [row[3] for row in rows]
    assert statuses.count("reachable") == PLAN_EXPORTS["reachable"]
    assert statuses.count("retained") == PLAN_EXPORTS["retained"]
    assert statuses.count("to_archive") == PLAN_EXPORTS["to_archive"]
    assert len({row[1] for row in rows}) == PLAN_EXPORTS["providers"]
    batch_exports = {b: {r[0] for r in rows if r[4] == b} for b in BATCH_COUNTS}
    for batch in BATCH_COUNTS:
        assert batch_exports[batch] == set(_batch(batch)["exports_to_retire"])
    to_archive_names = {r[0] for r in rows if r[3] == "to_archive"}
    assert to_archive_names == {r[0] for r in rows if r[4] in BATCH_COUNTS}


def test_retained_closure_is_leak_free() -> None:
    prune = _prune()
    enriched = prune["enriched_analysis"]
    assert enriched["retained_consumer_violations"] == []
    assert enriched["k0_dependency_leaks"] == []
    assert enriched["cross_family_cycles"] == []
    assert enriched["unknown_references_in_retained_surfaces"] == []
    assert enriched["entry_consumers_of_d0"] == {}
    # UNKNOWN dynamic references are permitted only inside pending D0 modules
    # and test files, and they keep every batch that they can reach PAUSED.
    surfaces = {ref["surface"].split(":")[0] for ref in enriched["unknown_references"]}
    assert surfaces <= {"src_d0", "test"}, surfaces
    for batch in BATCH_COUNTS:
        check = _batch(batch)["dependency_check"]
        if check["unknown_test_references_pending_manual_review"]:
            # Plan 3.3 item 6: no deletion permit until manual review; a batch
            # may only leave PAUSE with an explicit recorded review receipt.
            assert check["verdict"] == "PAUSE" or check.get("manual_review_completed"), batch

    kinds: dict[str, int] = {}
    retained_rows = _tsv_rows("retained-modules.tsv")
    for row in retained_rows:
        kinds[row[1]] = kinds.get(row[1], 0) + 1
    for kind, expected in RETAINED_TSV_KINDS.items():
        assert kinds.get(kind) == expected, (kind, kinds.get(kind))
    assert len(retained_rows) == RETAINED_TSV_TOTAL_ROWS, len(retained_rows)
    assert all(row[4] == "all" for row in retained_rows)


def test_source_inventory_covers_all_baseline_modules() -> None:
    rows = _tsv_rows("source-inventory.tsv")
    assert len(rows) == SOURCE_INVENTORY_ROWS
    paths = [row[0] for row in rows]
    assert paths == sorted(paths)
    assert len(set(paths)) == SOURCE_INVENTORY_ROWS
    prune = _prune()
    reach = prune["reachability"]
    expected = ({entry["path"] for entry in reach["d0"]}
                | {entry["path"] for entry in reach["k0"]}
                | {"src/polymarket_alpha_lab/__init__.py"})
    assert expected <= set(paths)


def test_current_tree_reproofs_against_baseline() -> None:
    inventory = _load_inventory_module()
    # FX-18 PR-A landing adaptation (2026-11-20): the audit test landed from
    # m3/b0 (blob da72da6927f573ae73b2ad4518136ead21d96b02) asserted a strict
    # ``verify(...) == 0``. At the first refreshed parent 3e52516f the live
    # tree legitimately contained 22 additive papersim.* modules after the
    # B0 baseline; after the canonical-parent refresh to the PR#85 merge
    # 86feaf1a (MO-5d crypto family) the additive drift is 23 papersim.*
    # modules -- a plan-3.1 protected subpackage -- so verify() reports
    # exactly the v4_unreachable_membership drift class.
    # Every other failure class stays fatal, the drifted modules are proven
    # additive-only and unclaimed by any frozen batch manifest, and nothing
    # here approves or performs any deletion.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = inventory.verify(REPO_ROOT)
    report = json.loads(buf.getvalue())
    assert report["baseline_commit"] == BASELINE_COMMIT, report
    assert report["missing_d0"] == 0, report
    assert report["executed_batches"] == [], report
    assert code == (1 if report["errors"] else 0), report
    failures = {error["check"] for error in report["errors"]}
    assert failures <= {"v4_unreachable_membership"}, report["errors"]
    d0_modules = {entry["module"] for entry in _prune()["reachability"]["d0"]}
    k0_modules = {entry["module"] for entry in _prune()["reachability"]["k0"]}
    claimed: set[str] = set()
    for batch in BATCH_COUNTS:
        claimed |= {entry["module"] for entry in _batch(batch)["files"]}
    for error in report["errors"]:
        detail = error["detail"]
        assert detail["no_longer_unreachable"] == [], error
        # verify() truncates this list to 20 entries; the complete drift set
        # is re-derived exhaustively below from the frozen inventory.
        for module in detail["unexpected_new_unreachable"]:
            assert module not in d0_modules, module
            assert module not in k0_modules, module
            assert module not in claimed, module
    # Exhaustive additive-only accounting against the frozen baseline
    # inventory: no baseline-recorded module disappeared, and every module
    # added since the baseline is claimed by no batch manifest (so no drift
    # module can ever ride into a deletion set unnoticed).
    baseline_paths = {row[0] for row in _tsv_rows("source-inventory.tsv")}
    current_paths = {path.relative_to(REPO_ROOT).as_posix()
                     for path in (REPO_ROOT / "src").rglob("*.py")}
    assert baseline_paths <= current_paths, sorted(baseline_paths - current_paths)
    for path in sorted(current_paths - baseline_paths):
        module = path[len("src/"):-len(".py")].replace("/", ".")
        assert module not in d0_modules, module
        assert module not in k0_modules, module
        assert module not in claimed, module


def test_canonical_parent_refresh_run_accounting() -> None:
    """Lock the refreshed inventory run at the canonical parent 86feaf1a.

    Additive FX-18 PR-A canonical-refresh accounting (2026-11-20): the
    release node requires the PR-A base to be latest canonical main, so the
    proven refreshed-run generator invocation was re-executed at the PR#85
    merge parent into ``refreshed-20261120-86feaf1a/`` (additive-only; the
    first refreshed run at parent 3e52516f in ``refreshed-20261120/`` stays
    untouched as historical evidence). This function proves B1 stability at
    the refreshed parent and classifies every module added since the B0
    baseline into the drift vocabulary (papersim.* protected subpackage,
    HELD_SUBTRACTED, claimed by no frozen batch manifest). It also locks the
    MO-5d module/test classification and their recorded hashes. It approves
    and performs no deletion; every refreshed batch manifest stays
    executed=false with verdict PAUSE.
    """
    manifest = json.loads(
        (REFRESHED_PARENT_DIR / "prune-manifest.json").read_text(encoding="utf-8"))
    assert manifest["baseline"]["commit"] == REFRESHED_PARENT_COMMIT, manifest["baseline"]
    assert manifest["tool"]["sha256"] == BASELINE_ARTIFACT_SHA256["inventory.py"]

    # Every refreshed batch stays not-executed and PAUSE (unknowns are never
    # force-resolved; the refreshed view pauses every batch).
    for batch in BATCH_COUNTS:
        refreshed = json.loads(
            (REFRESHED_PARENT_DIR / f"{batch}-manifest.json").read_text(encoding="utf-8"))
        assert refreshed["executed"] is False, batch
        assert refreshed["executed_commit"] is None, batch
        assert refreshed["dependency_check"]["verdict"] == "PAUSE", batch

    # B1 stability at the refreshed parent: identical membership, line
    # total, export count and retirement lists as the frozen manifest.
    frozen_b1 = _batch("B1")
    refreshed_b1 = json.loads(
        (REFRESHED_PARENT_DIR / "B1-manifest.json").read_text(encoding="utf-8"))
    refreshed_modules = [entry["module"] for entry in refreshed_b1["files"]]
    assert len(refreshed_modules) == BATCH_COUNTS["B1"] == 1114
    assert set(refreshed_modules) == {entry["module"] for entry in frozen_b1["files"]}
    assert refreshed_b1["counts"]["lines"] == BATCH_LINES["B1"]
    assert refreshed_b1["counts"]["root_exports"] == BATCH_EXPORTS["B1"]
    frozen_sha = {entry["module"]: entry["sha256"] for entry in frozen_b1["files"]}
    for entry in refreshed_b1["files"]:  # zero D0 file modified at the parent
        assert entry["sha256"] == frozen_sha[entry["module"]], entry["module"]
    for key in ("delete_full", "nodeid_candidate_files", "shared_conflicts_or_blocked"):
        assert (set(refreshed_b1["test_retirement"][key])
                == set(frozen_b1["test_retirement"][key])), key
    frozen_unknown_tests = {u["test"] for u in frozen_b1["dependency_check"][
        "unknown_test_references_pending_manual_review"]}
    refreshed_unknown_tests = {u["test"] for u in refreshed_b1["dependency_check"][
        "unknown_test_references_pending_manual_review"]}
    assert refreshed_unknown_tests == frozen_unknown_tests  # same 25-file PAUSE set

    # Refreshed batch counts: only B8 grew (183 -> 194, +10 papersim at the
    # first refreshed parent plus the MO-5d module); B1-B7 are unchanged.
    refreshed_counts = manifest["reachability"]["batch_counts"]
    for batch in BATCH_COUNTS:
        expected = BATCH_COUNTS[batch] + (11 if batch == "B8" else 0)
        assert refreshed_counts[batch] == expected, batch

    # Exhaustive drift accounting vs the frozen baseline inventory: every
    # module added since the B0 baseline is a plan-3.1 protected papersim
    # module claimed by no frozen batch manifest (so no drift module can
    # ever ride into a deletion set unnoticed).
    baseline_paths = {row[0] for row in _tsv_rows("source-inventory.tsv")}
    refreshed_rows = [line.split("\t") for line in
                      (REFRESHED_PARENT_DIR / "source-inventory.tsv")
                      .read_text(encoding="utf-8").splitlines()[1:] if line.strip()]
    refreshed_paths = {row[0] for row in refreshed_rows}
    assert baseline_paths <= refreshed_paths, sorted(baseline_paths - refreshed_paths)
    d0_modules = {entry["module"] for entry in _prune()["reachability"]["d0"]}
    k0_modules = {entry["module"] for entry in _prune()["reachability"]["k0"]}
    claimed: set[str] = set()
    for batch in BATCH_COUNTS:
        claimed |= {entry["module"] for entry in _batch(batch)["files"]}
    drift_modules = []
    for row in refreshed_rows:
        if row[0] in baseline_paths:
            continue
        module = row[1]
        assert module.startswith("polymarket_alpha_lab.papersim."), module
        assert module not in d0_modules, module
        assert module not in k0_modules, module
        assert module not in claimed, module
        drift_modules.append(module)
    assert len(drift_modules) == 23, sorted(drift_modules)
    assert MO5D_NEW_MODULE in drift_modules

    # The refreshed retained closure carries every drift module as a
    # protected papersim row (97 frozen rows + 23 drift rows).
    retained_rows = [line.split("\t") for line in
                     (REFRESHED_PARENT_DIR / "retained-modules.tsv")
                     .read_text(encoding="utf-8").splitlines()[1:] if line.strip()]
    assert len(retained_rows) == RETAINED_TSV_TOTAL_ROWS + len(drift_modules)
    retained_kinds: dict[str, int] = {}
    for row in retained_rows:
        retained_kinds[row[1]] = retained_kinds.get(row[1], 0) + 1
    for kind, expected in RETAINED_TSV_KINDS.items():
        assert retained_kinds.get(kind) == expected, (kind, retained_kinds.get(kind))
    refreshed_b8 = json.loads(
        (REFRESHED_PARENT_DIR / "B8-manifest.json").read_text(encoding="utf-8"))
    mo5d_entry = next(entry for entry in refreshed_b8["files"]
                      if entry["module"] == MO5D_NEW_MODULE)
    assert _sha256(REPO_ROOT / mo5d_entry["path"]) == mo5d_entry["sha256"]
    assert mo5d_entry["lines"] == 654  # MO-5d module size at the parent commit

    # MO-5d test-drift classification: the new crypto-family test file is a
    # refreshed-B8 blocked_mixed papersim test (no B1 association), and the
    # B1 module-scope consumer set is unchanged from the frozen analysis
    # (799 files: 796 delete_full + 3 shared conflicts; no new B1 consumer
    # test appeared at the refreshed parent).
    retirement_lines = (REFRESHED_PARENT_DIR / "test-retirement.tsv") \
        .read_text(encoding="utf-8").splitlines()
    header = retirement_lines[0].split("\t")
    path_i = header.index("test_path")
    scope_i = header.index("batches_module_scope")
    verdict_i = header.index("verdict")
    rows = [line.split("\t") for line in retirement_lines[1:] if line.strip()]
    mo5d_row = next(row for row in rows if row[path_i] == MO5D_NEW_TEST)
    assert mo5d_row[verdict_i] == "blocked_mixed", mo5d_row
    assert mo5d_row[scope_i] == "B8", mo5d_row
    b1_scoped_refreshed = {row[path_i] for row in rows
                           if "B1" in row[scope_i].split(";")}
    frozen_rows = _tsv_rows("test-retirement.tsv")
    frozen_header = (E_DIR / "test-retirement.tsv") \
        .read_text(encoding="utf-8").splitlines()[0].split("\t")
    b1_scoped_frozen = {row[frozen_header.index("test_path")] for row in frozen_rows
                        if "B1" in row[frozen_header.index("batches_module_scope")].split(";")}
    assert b1_scoped_refreshed == b1_scoped_frozen
    assert len(b1_scoped_frozen) == 799
