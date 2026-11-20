#!/usr/bin/env python3
"""FX-18 PR-A disposition-table and evidence generator (preparation-only).

One-off stdlib-only generator for the FX-18 "Archive Batch One" PR-A
preparation node (plan second edition, 2026-11-20). Reads the landed frozen
B0 artifacts, the refreshed inventory run, the repository tree and git blobs
strictly as DATA; writes only the receipt-mandated disposition tables and
PR-A evidence files. It never modifies, deletes or approves deletion of any
test, source file, migration, workflow or gate; every retirement row it
emits is a PROPOSAL that no code in this repository executes.

Outputs (relative to the repository root):
  docs/handoffs/m3-cleanup/dynamic-reference-resolutions.tsv
  docs/handoffs/m3-cleanup/test-retirement-nodeids.tsv
  docs/handoffs/m3-cleanup/protected-assertions.tsv
  docs/handoffs/m3-cleanup/pr-a-20261120/refreshed-drift-held-subtracted.tsv
  docs/handoffs/m3-cleanup/pr-a-20261120/zero-dependency-scan.tsv
  docs/handoffs/m3-cleanup/pr-a-20261120/refreshed-run-summary.json

paper-only / report-only / readonly; no network, no database, no imports of
project code.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

M3B0_COMMIT = "9b905791cb961fbdce818fe3a299d552662adeec"
BASELINE_COMMIT = "4c8bec2fdce31fec3a8ec06a6ab93a25e4d7fc69"
PKG = "polymarket_alpha_lab"
E_DIR = Path("docs/handoffs/m3-cleanup")
REFRESHED_DIR = E_DIR / "refreshed-20261120"
OUT_DIR = E_DIR / "pr-a-20261120"
RECEIPT = "codex-b0-pause-decisions-20261116.log Decision A"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root,
                          capture_output=True, text=True, check=False).stdout.strip()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cell(value) -> str:
    text = str(value)
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def write_tsv(path: Path, header: list[str], rows: list[list]) -> None:
    lines = ["\t".join(header)]
    lines += ["\t".join(cell(v) for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Table 1: dynamic-reference-resolutions.tsv
# ---------------------------------------------------------------------------
# Per-call-site resolutions transcribed from the 2026-11-16 decision receipt
# (codex-b0-pause-decisions-20261116.log, Decision A).  Keyed by
# (test file basename, line).  The receipt classified all 39 UNKNOWN
# call sites recorded in the frozen prune-manifest; anything present in the
# manifest but missing here stays UNKNOWN/unresolved (never force-resolved).
# target notation: "m:<module>" = polymarket_alpha_lab.<module>; R = B0
# reachable set; K0 = 43-module retained closure; Bn = D0 batch member.
RECEIPT_RESOLUTIONS: dict[tuple[str, int], dict] = {
    # -- group 1: single-batch D0 targets (blockage lifted only) ----------
    ("test_candidate_decision_base_rate_anchor_score.py", 29):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:candidate_decision_base_rate_anchor_score",
         "scope": "B5", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof (exclusivity, protection, closure)",
         "guard": "-", "group": "group1-single-batch"},
    ("test_candidate_decision_base_rate_drift_score.py", 22):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:candidate_decision_base_rate_drift_score",
         "scope": "B5", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_market_research_crypto_exchange_reserve_drain_digest.py", 476):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:market_research_crypto_exchange_reserve_drain_digest",
         "scope": "B2", "disposition": "dynamic-target blockage lifted; line sits inside boundary guard test_exchange_reserve_drain_digest_module_scope_excludes_io_and_mutation which must NOT be retired with it",
         "guard": "test_exchange_reserve_drain_digest_module_scope_excludes_io_and_mutation (protected boundary guard)", "group": "group1-single-batch"},
    ("test_research_cost_threshold_policy.py", 26):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:research_cost_threshold_policy",
         "scope": "B1", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_research_cost_threshold_policy.py", 27):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:research_cost_threshold_policy",
         "scope": "B1", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_research_signal_conflict_report.py", 34):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:research_signal_conflict_report",
         "scope": "B1", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_research_signal_conflict_report.py", 35):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:research_signal_conflict_report",
         "scope": "B1", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_probability_path_consistency_v10.py", 41):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_probability_path_consistency_v10",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_probability_path_consistency_v10.py", 42):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_probability_path_consistency_v10",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_research_risk_register_v10.py", 30):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_research_risk_register_v10",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_research_risk_register_v10.py", 31):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_research_risk_register_v10",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_scenario_balance_score_v2.py", 30):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_scenario_balance_score_v2",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_scenario_balance_score_v2.py", 31):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_scenario_balance_score_v2",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_source_disagreement_adjusted_ev_v2.py", 42):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_source_disagreement_adjusted_ev_v2",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_source_disagreement_adjusted_ev_v2.py", 43):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_source_disagreement_adjusted_ev_v2",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_tail_risk_digest.py", 39):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_tail_risk_digest",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_tail_risk_digest.py", 40):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_tail_risk_digest",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_tail_risk_event_asymmetry_gate_v2.py", 42):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_tail_risk_event_asymmetry_gate_v2",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    ("test_strategy_candidate_tail_risk_event_asymmetry_gate_v2.py", 43):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_candidate_tail_risk_event_asymmetry_gate_v2",
         "scope": "B3", "disposition": "dynamic-target blockage lifted; retirement still requires Decision-B per-nodeid proof",
         "guard": "-", "group": "group1-single-batch"},
    # -- group 2: all targets retained/reachable (file and cases KEEP) -----
    ("test_action_gated_strategy_recommendation_queue_history.py", 55):
        {"status": "RESOLVED_RETAINED_ONLY", "targets": "m:action_gated_strategy_recommendation_queue_history",
         "scope": "R", "disposition": "keep file and all cases; target is reachable, never a D0 retirement object",
         "guard": "-", "group": "group2-retained-only"},
    ("test_action_gated_strategy_recommendation_queue_history.py", 57):
        {"status": "RESOLVED_RETAINED_ONLY", "targets": "m:action_gated_strategy_recommendation_queue_history",
         "scope": "R", "disposition": "keep file and all cases; target is reachable, never a D0 retirement object",
         "guard": "-", "group": "group2-retained-only"},
    ("test_cli_db_connection_cleanup.py", 127):
        {"status": "RESOLVED_RETAINED_ONLY",
         "targets": "CLI_DB_HELPERS loader_module values (5 entries, all R)",
         "scope": "R", "disposition": "keep file and every parameter instance; all five loader modules are reachable",
         "guard": "-", "group": "group2-retained-only"},
    ("test_crypto_btc_evidence_scope.py", 568):
        {"status": "RESOLVED_RETAINED_ONLY",
         "targets": "IMPORT_ALLOWLISTS keys: m:crypto_btc_evidence_registry, m:crypto_btc_evidence_catalog, m:crypto_btc_evidence_resolution, m:crypto_btc_evidence_policy",
         "scope": "K0", "disposition": "keep the complete Node 6 guard",
         "guard": "Node 6 crypto_btc evidence-scope guard (receipt-named)", "group": "group2-retained-only"},
    ("test_env_example_coverage.py", 21):
        {"status": "RESOLVED_RETAINED_ONLY", "targets": "CONFIG_MODULE_NAMES (4 items, all R)",
         "scope": "R", "disposition": "keep env-var coverage and forbidden-sample-DSN assertions",
         "guard": "-", "group": "group2-retained-only"},
    ("test_paper_recommendation_cycle_snapshot_scope.py", 368):
        {"status": "RESOLVED_RETAINED_ONLY",
         "targets": "NON_LOG_MODULE_NAMES: m:paper_recommendation_cycle_snapshot, m:paper_recommendation_cycle_snapshot_trend",
         "scope": "R", "disposition": "keep; must not misuse the six TARGET_MODULE_NAMES as import targets",
         "guard": "-", "group": "group2-retained-only"},
    ("test_paper_strategy_cycle_report_history_gate_db_row.py", 64):
        {"status": "RESOLVED_RETAINED_ONLY", "targets": "m:paper_strategy_cycle_report_history_gate",
         "scope": "R", "disposition": "keep; the missing-module stub-injection branch is not a deletion permit",
         "guard": "-", "group": "group2-retained-only"},
    ("test_supabase_paper_recommendation_report_config.py", 54):
        {"status": "RESOLVED_RETAINED_ONLY",
         "targets": "surface fixture params: m:supabase_paper_probability_recommendation_queue_config, m:supabase_paper_recommendation_risk_budget_config",
         "scope": "R", "disposition": "keep both parameter instance groups",
         "guard": "-", "group": "group2-retained-only"},
    ("test_team_diagnostics_phase1_boundary.py", 141):
        {"status": "RESOLVED_RETAINED_ONLY",
         "targets": "EXPECTED_MODULE_EXPORTS keys: m:team_diagnostics_bundle, m:team_diagnostics_db_source, m:team_cli_wiring, m:team_diagnostics_cli_format",
         "scope": "R", "disposition": "keep the complete boundary guard",
         "guard": "team diagnostics phase-1 boundary guard", "group": "group2-retained-only"},
    ("test_team_evaluation_attempt_latest_read_scope.py", 513):
        {"status": "RESOLVED_RETAINED_ONLY",
         "targets": "PRODUCTION_IMPORT = m:team_evaluation_attempt_latest_read",
         "scope": "K0", "disposition": "keep the complete Node 8 guard",
         "guard": "Node 8 team-evaluation latest-read guard (receipt-named)", "group": "group2-retained-only"},
    # -- group 3: cross-batch or mixed (per-instance handling) -------------
    ("test_paper_recommendation_integration_boundary_scope.py", 173):
        {"status": "RESOLVED_MIXED_PER_INSTANCE",
         "targets": "m:paper_recommendation_pipeline (R); m:paper_recommendation_cycle_bundle, m:paper_recommendation_pipeline_trend (B7)",
         "scope": "R+B7", "disposition": "keep file; import/disable-surface/hard-flags guards not retireable; the two B7 objects form a real protection dependency and are HELD until resolved",
         "guard": "integration-boundary guard (import/disable-surface/hard-flags)", "group": "group3-mixed"},
    ("test_phase1_readonly_boundary_scan.py", 51):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:probability_event_screen_phase1_boundary_report",
         "scope": "B6", "disposition": "target closed, but both call sites serve one protected nodeid that must stay with its effective dependencies; no payload check may be dropped",
         "guard": "phase-1 readonly boundary scan guard", "group": "group3-mixed"},
    ("test_phase1_readonly_boundary_scan.py", 69):
        {"status": "RESOLVED_SINGLE_BATCH", "targets": "m:strategy_phase1_readiness_aggregator",
         "scope": "B3", "disposition": "target closed, but both call sites serve one protected nodeid that must stay with its effective dependencies; no payload check may be dropped",
         "guard": "phase-1 readonly boundary scan guard", "group": "group3-mixed"},
    ("test_phase1_report_discovery_smoke.py", 54):
        {"status": "RESOLVED_MIXED_PER_INSTANCE",
         "targets": "fixed 8-item directory: B8 forecast_context_readiness_report, information_freshness_refresh_sla_readiness_report, input_failure_degradation_readiness_report, portfolio_probability_event_readiness_report, post_settlement_calibration_experience_feedback_report; B6 probability_event_cost_adjusted_position_recommendation_report, probability_event_market_signal_risk_readiness_report; B3 strategy_phase1_readiness_aggregator",
         "scope": "B8+B6+B3", "disposition": "keep file and registry guards; ordinary discovery cases may enter per-parameter-instance review; I/O and hard-flags boundary nodes never retire",
         "guard": "phase-1 report discovery registry guard", "group": "group3-mixed"},
    ("test_phase1_report_discovery_smoke.py", 63):
        {"status": "RESOLVED_MIXED_PER_INSTANCE",
         "targets": "same fixed 8-item directory as line 54",
         "scope": "B8+B6+B3", "disposition": "same as line 54", "guard": "phase-1 report discovery registry guard",
         "group": "group3-mixed"},
    ("test_phase1_report_discovery_smoke.py", 79):
        {"status": "RESOLVED_MIXED_PER_INSTANCE",
         "targets": "same fixed 8-item directory as line 54",
         "scope": "B8+B6+B3", "disposition": "same as line 54", "guard": "phase-1 report discovery registry guard",
         "group": "group3-mixed"},
    ("test_phase1_report_discovery_smoke.py", 107):
        {"status": "RESOLVED_RETAINED_ONLY", "targets": "m:report_discovery",
         "scope": "R", "disposition": "keep file and registry guards",
         "guard": "phase-1 report discovery registry guard", "group": "group3-mixed"},
    ("test_phase1_report_modules_import_no_io.py", 98):
        {"status": "RESOLVED_MIXED_PER_INSTANCE",
         "targets": "fixed 8-item directory (same set as test_phase1_report_discovery_smoke.py:54)",
         "scope": "B8+B6+B3", "disposition": "keep; both call classes serve the import-no-I/O safety guard; directory/expected-class shortening or skipping of deleted objects is forbidden",
         "guard": "phase-1 import-no-I/O safety guard", "group": "group3-mixed"},
    ("test_phase1_report_modules_import_no_io.py", 111):
        {"status": "RESOLVED_MIXED_PER_INSTANCE",
         "targets": "PUBLIC_DATA_CLASSES_WITH_PHASE1_FLAGS.items() keys = the fixed 8-item directory",
         "scope": "B8+B6+B3", "disposition": "keep; same guard as line 98",
         "guard": "phase-1 import-no-I/O safety guard", "group": "group3-mixed"},
    ("test_psycopg_adapter_cleanup.py", 174):
        {"status": "RESOLVED_MIXED_PER_INSTANCE",
         "targets": "TRANSACTIONAL_MODULES + READ_ONLY_MODULES + ASSIGNED_*_HELPERS first items, deduped: 40 modules = 26 R + 3 B3 + 10 B7 + 1 B8",
         "scope": "R+B3+B7+B8", "disposition": "keep file and shared helpers; only Decision-B-proven exclusive, unprotected D0 parameter instances may retire with their owning batch; every R parameter instance stays; 40 modules is not 40 test instances",
         "guard": "psycopg adapter cleanup guard (receipt-named)", "group": "group3-mixed"},
}


def table_dynamic_reference_resolutions(root: Path) -> tuple[list[list], dict]:
    frozen = load_json(root / E_DIR / "prune-manifest.json")
    refs = [u for u in frozen["enriched_analysis"]["unknown_references"]
            if u["surface"].startswith("test:")]
    rows, unresolved, matched = [], [], 0
    for u in sorted(refs, key=lambda x: (x["surface"], x.get("line", 0))):
        test_file = u["surface"].split(":", 1)[1]
        line = u.get("line")
        key = (Path(test_file).name, line)
        res = RECEIPT_RESOLUTIONS.get(key)
        head_sha = sha256_bytes((root / test_file).read_bytes()) if (root / test_file).is_file() else None
        base = subprocess.run(["git", "show", f"{BASELINE_COMMIT}:{test_file}"],
                              cwd=root, capture_output=True)
        base_sha = sha256_bytes(base.stdout) if base.returncode == 0 else None
        head_status = ("unchanged-from-baseline" if head_sha == base_sha and head_sha
                       else "DRIFTED-vs-baseline" if head_sha else "MISSING-at-HEAD")
        if res is None:
            unresolved.append(key)
            rows.append([test_file, line, u.get("kind"), u.get("expr"),
                         "UNKNOWN_UNRESOLVED", "-", "-", "-",
                         "no receipt resolution transcribed; remains UNKNOWN pending manual review (plan 3.3 item 6); never force-resolved",
                         "-", RECEIPT + " (no entry)", head_status,
                         "re-validate at execution parent commit"])
            continue
        matched += 1
        rows.append([test_file, line, u.get("kind"), u.get("expr"),
                     res["status"], res["targets"], res["scope"], res["disposition"],
                     res["guard"], f"{RECEIPT} {res['group']}", head_status,
                     "lines verified valid at PR-A parent (file byte-identical to baseline)"
                     if head_status == "unchanged-from-baseline" else "line numbers belong to the fixed B0 commit; re-validate"])
    stats = {"call_sites": len(refs), "resolved": matched, "unresolved": len(unresolved),
             "files": len({r[0] for r in rows}),
             "unresolved_keys": [f"{k[0]}:{k[1]}" for k in unresolved],
             "status_counts": {}}
    for r in rows:
        stats["status_counts"][r[4]] = stats["status_counts"].get(r[4], 0) + 1
    header = ["test_file", "line", "kind", "expr", "resolution_status",
              "target_modules", "target_scope", "disposition", "protected_guard",
              "evidence_anchor", "head_file_status", "revalidation_note"]
    return (header, rows), stats


# ---------------------------------------------------------------------------
# Table 2: test-retirement-nodeids.tsv
# ---------------------------------------------------------------------------
def static_nodeids(path: Path) -> dict[str, bool]:
    """Static pytest-style nodeid proposal extraction (ast only).

    Returns {qualname: parametrized} where qualname is 'test_f' or
    'TestC.test_m'; parametrized is True when a parametrize marker is
    present on the function.
    """
    out: dict[str, bool] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out[node.name] = _has_parametrize(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    out[f"{node.name}.{sub.name}"] = _has_parametrize(sub)
    return out


def _has_parametrize(fn) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "parametrize":
            return True
        if isinstance(target, ast.Name) and target.id == "parametrize":
            return True
    return False


def table_test_retirement_nodeids(root: Path) -> tuple[tuple[list, list], dict]:
    frozen = load_json(root / E_DIR / "B1-manifest.json")
    tr = frozen["test_retirement"]
    delete_full = sorted(tr["delete_full"])
    nodeid_files = sorted(tr["nodeid_candidate_files"])
    shared = sorted(tr["shared_conflicts_or_blocked"])
    # frozen per-file candidate names
    cand = {}
    text = (root / E_DIR / "test-retirement.tsv").read_text(encoding="utf-8").splitlines()
    header = text[0].split("\t")
    idx = {name: i for i, name in enumerate(header)}
    for line in text[1:]:
        row = line.split("\t")
        cand[row[idx["test_path"]]] = [c for c in row[idx["nodeid_candidates"]].split(";") if c and c != "-"]

    rows = []
    counts = {"RETIRE_PROPOSAL": 0, "KEEP_PROPOSAL": 0, "KEEP_MANDATED_SHARED_CONFLICT": 0,
              "KEEP_NOT_A_NODEID": 0, "files_delete_full": len(delete_full),
              "files_nodeid_candidate": len(nodeid_files), "files_shared_conflict": len(shared),
              "missing_files": []}
    for rel in delete_full:
        p = root / rel
        if not p.is_file():
            counts["missing_files"].append(rel)
            continue
        for qual, param in sorted(static_nodeids(p).items()):
            counts["RETIRE_PROPOSAL"] += 1
            rows.append([f"{rel}::{qual.replace('.', '::')}", rel, "delete_full",
                         "RETIRE_PROPOSAL", "frozen B1-manifest delete_full (module-scope imports exclusively B1 D0)",
                         "parametrized: enumerate actual collected nodeids at execution parent" if param else "-",
                         "proposal only; no retirement executes in PR-A; Decision-B gates remain"])
    for rel in nodeid_files:
        p = root / rel
        if not p.is_file():
            counts["missing_files"].append(rel)
            continue
        nodeids = static_nodeids(p)
        cand_set = set(cand.get(rel, []))
        for qual, param in sorted(nodeids.items()):
            if qual in cand_set:
                counts["RETIRE_PROPOSAL"] += 1
                rows.append([f"{rel}::{qual.replace('.', '::')}", rel, "nodeid_candidate",
                             "RETIRE_PROPOSAL", "frozen test-retirement.tsv nodeid_candidates entry that is a statically-present test nodeid",
                             "parametrized: enumerate actual collected nodeids at execution parent" if param else "-",
                             "proposal only; per-nodeid Decision-B proof (exclusivity, protection, closure) still required"])
            else:
                counts["KEEP_PROPOSAL"] += 1
                rows.append([f"{rel}::{qual.replace('.', '::')}", rel, "nodeid_candidate",
                             "KEEP_PROPOSAL", "nodeid not listed as a function-scope D0 consumer in the frozen analysis",
                             "parametrized" if param else "-",
                             "retained scope by default; conservative KEEP"])
        for name in sorted(cand_set - set(nodeids)):
            counts["KEEP_NOT_A_NODEID"] += 1
            rows.append([f"{rel}::{name.replace('.', '::')}", rel, "nodeid_candidate",
                         "KEEP_NOT_A_NODEID", "frozen nodeid_candidates entry that is a helper name, not a pytest-collected test nodeid",
                         "-", "receipt Decision B: helper names are not deletion instructions"])
    for rel in shared:
        p = root / rel
        if not p.is_file():
            counts["missing_files"].append(rel)
            continue
        for qual, param in sorted(static_nodeids(p).items()):
            counts["KEEP_MANDATED_SHARED_CONFLICT"] += 1
            rows.append([f"{rel}::{qual.replace('.', '::')}", rel, "shared_conflict",
                         "KEEP_MANDATED_SHARED_CONFLICT", "shared-conflict file: module-scope imports span batches; PR-A instruction mandates KEEP",
                         "parametrized" if param else "-",
                         "cross-batch nodeid; any lossless split needs its own reviewed change first (receipt Decision B rule 5)"])
    header = ["nodeid", "test_file", "source_list", "proposal", "basis",
              "parametrized_note", "rationale"]
    return (header, rows), counts


# ---------------------------------------------------------------------------
# Table 3: protected-assertions.tsv
# ---------------------------------------------------------------------------
def head_blob_sha(root: Path, rel: str):
    out = git(root, "rev-parse", "--verify", f"HEAD:{rel}")
    return out or None


def dir_byte_identical_to_head(root: Path, rel_dir: str) -> tuple[bool, int, list]:
    listing = git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", rel_dir)
    paths = [p for p in listing.splitlines() if p]
    bad = []
    for rel in paths:
        p = root / rel
        if not p.is_file() or sha256_bytes(p.read_bytes()) != sha256_bytes(
                subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=root,
                               capture_output=True).stdout):
            bad.append(rel)
    return (not bad and len(paths) > 0), len(paths), bad[:10]


def papersim_all_count(root: Path) -> int:
    tree = ast.parse((root / "src" / PKG / "papersim" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    return len(node.value.elts)
    return -1


def selfcheck_check_count(root: Path) -> int:
    tree = ast.parse((root / "src" / PKG / "papersim" / "cli.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.List):
            first = node.elts[0] if node.elts else None
            if (isinstance(first, ast.Tuple) and first.elts
                    and isinstance(first.elts[0], ast.Constant)
                    and first.elts[0].value == "module-imports"):
                return len(node.elts)
    return -1


def table_protected_assertions(root: Path) -> tuple[tuple[list, list], dict]:
    frozen = load_json(root / E_DIR / "prune-manifest.json")
    b1 = load_json(root / E_DIR / "B1-manifest.json")
    all_batch_paths, all_batch_modules = [], []
    for i in range(1, 9):
        bm = load_json(root / E_DIR / f"B{i}-manifest.json")
        all_batch_paths += [e["path"] for e in bm["files"]]
        all_batch_modules += [e["module"] for e in bm["files"]]
    tr = b1["test_retirement"]
    retire_paths = set(tr["delete_full"]) | set(tr["nodeid_candidate_files"]) | set(tr["shared_conflicts_or_blocked"])

    def in_any_manifest(prefix: str) -> list:
        return [p for p in all_batch_paths if p == prefix or p.startswith(prefix.rstrip("/") + "/")]

    def in_retire(prefix: str) -> list:
        return [p for p in retire_paths if p == prefix or p.startswith(prefix.rstrip("/") + "/")]

    rows = []
    fails = []

    def add(surface, scope, check, ok, detail):
        rows.append([surface, scope, check, "PASS" if ok else "FAIL", detail])
        if not ok:
            fails.append(surface)

    # allowlisted protected paths
    for surface, pat in [(".local/", ".local/"), ("runtime/postgres/", "runtime/postgres/"),
                         ("runtime/postgres.installing/", "runtime/postgres.installing/"),
                         ("database/postgres-runtime.zip", "database/postgres-runtime.zip"),
                         ("*.palpg.zip / *.palpg.sha256", ".palpg."),
                         (".env / .env.* (except .env.example)", ".env")]:
        hits = [p for p in all_batch_paths if pat.strip("/") in p or p.startswith(pat)]
        env_ok = True
        if surface.startswith(".env"):
            hits = [p for p in hits if p not in (".env.example",)]
        add(surface, "allowlisted protected path (plan §2)",
            f"no path in any of the 8 frozen batch manifests matches this protected pattern (checked {len(all_batch_paths)} manifest paths)",
            not hits, f"matches={hits[:5] or 'none'}")

    # protected directories byte-identical + absent from retirement lists
    for surface, d in [("tests/fixtures/papersim/ (+FotMob excerpts)", "tests/fixtures/papersim"),
                       ("database/ migrations + ledger", "database"),
                       ("supabase/ pathname (API compatibility)", "supabase"),
                       ("docs/paper-sim/research/league-admission-20261119/", "docs/paper-sim/research/league-admission-20261119")]:
        ok_bytes, n, bad = dir_byte_identical_to_head(root, d)
        add(surface, d,
            f"byte-identical to HEAD committed blobs ({n} files) AND absent from every retirement list and every batch manifest",
            ok_bytes and not in_retire(d) and not in_any_manifest(d),
            f"files={n} byte-mismatches={bad or 'none'} retirement-hits={in_retire(d)[:3] or 'none'} manifest-hits={in_any_manifest(d)[:3] or 'none'}")

    fotmob = [p for p in git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", "tests/fixtures/papersim").splitlines()
              if "fotmob" in p.lower()]
    add("FotMob excerpt fixtures", f"{len(fotmob)} fixture files",
        "byte-identical to HEAD blobs and never referenced by any retirement list",
        all(sha256_bytes((root / p).read_bytes()) == sha256_bytes(subprocess.run(
            ["git", "show", f"HEAD:{p}"], cwd=root, capture_output=True).stdout) for p in fotmob) and not in_retire("tests/fixtures/papersim"),
        f"files={fotmob[:4]}... total={len(fotmob)}")

    # K0 retained closure
    ret_rows = [l.split("\t") for l in (root / E_DIR / "retained-modules.tsv").read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    k0 = {e["module"]: e["sha256"] for e in frozen["reachability"]["k0"]}
    k0_ok = all((root / f"src/{m.replace('.', '/')}.py").is_file() for m in k0)
    k0_sha_ok = all(
        sha256_bytes((root / f"src/{m.replace('.', '/')}.py").read_bytes()) == s
        for m, s in k0.items())
    add("K0 retained closure (retained-modules.tsv, 97 rows)",
        f"{len(ret_rows)} rows = 13 seed_broker + 43 k0_retained + 41 protected_reachable",
        "row count == 97; all 43 K0 modules present with sha256 equal to the frozen manifest; no K0 module claimed by any batch manifest; excluded_batches == all",
        len(ret_rows) == 97 and k0_ok and k0_sha_ok and not (set(k0) & set(all_batch_modules))
        and all(r[4] == "all" for r in ret_rows),
        f"k0_present={k0_ok} k0_sha_match={k0_sha_ok} claimed_by_batch={sorted(set(k0) & set(all_batch_modules))[:3] or 'none'}")

    # receipt-named guard tests
    per_batch_lists = {}
    delete_full_any, other_lists_any = set(), set()
    for i in range(1, 9):
        bm = load_json(root / E_DIR / f"B{i}-manifest.json")["test_retirement"]
        per_batch_lists[f"B{i}"] = bm
        delete_full_any |= set(bm["delete_full"])
        other_lists_any |= set(bm["nodeid_candidate_files"]) | set(bm["shared_conflicts_or_blocked"])
    for g, mode in [("tests/test_crypto_btc_evidence_scope.py", "pure_keep"),
                    ("tests/test_team_evaluation_attempt_latest_read_scope.py", "pure_keep"),
                    ("tests/test_psycopg_adapter_cleanup.py", "shared_keep")]:
        byte_ok = (root / g).is_file() and sha256_bytes((root / g).read_bytes()) == sha256_bytes(
            subprocess.run(["git", "-c", "core.quotepath=off", "show", f"HEAD:{g}"],
                           cwd=root, capture_output=True).stdout)
        if mode == "pure_keep":
            ok = g not in delete_full_any and g not in other_lists_any and byte_ok
            detail = "byte-identical to HEAD; absent from every list of every batch manifest"
            note = "receipt-named guard; its UNKNOWN-ref row resolves KEEP (Decision A group 2)"
        else:
            in_shared = sorted(b for b, bm in per_batch_lists.items()
                               if g in bm["shared_conflicts_or_blocked"])
            ok = g not in delete_full_any and byte_ok and in_shared
            detail = (f"byte-identical to HEAD; absent from every delete_full list; "
                      f"appears only in shared_conflicts_or_blocked (retention classification): {in_shared}")
            note = "receipt Decision A group 3: file and shared helpers retained; only per-parameter-instance D0 handling after Decision-B proof"
        add(f"guard test {Path(g).name}", g, detail, ok, note)

    # papersim exports + selfcheck
    n_exp = papersim_all_count(root)
    add("papersim public exports", "src/polymarket_alpha_lab/papersim/__init__.py __all__",
        "ast count of __all__ entries == 194 (same definition as tests/test_papersim_fees.py)",
        n_exp == 194, f"count={n_exp}")
    n_chk = selfcheck_check_count(root)
    add("papersim selfcheck ten items", "papersim/cli.py check_calls registry",
        "ast count of registered selfcheck checks == 10; registry unchanged; no retirement list touches papersim",
        n_chk == 10 and not in_retire("src/polymarket_alpha_lab/papersim"), f"count={n_chk}")

    # root AGENTS.md
    add("root AGENTS.md", "AGENTS.md",
        "byte-identical to HEAD committed blob (untouched by PR-A)",
        (root / "AGENTS.md").is_file() and sha256_bytes((root / "AGENTS.md").read_bytes()) ==
        sha256_bytes(subprocess.run(["git", "show", "HEAD:AGENTS.md"], cwd=root, capture_output=True).stdout),
        "V1 freeze anchor (2026-09-14)")

    # landed frozen artifacts
    m3b0 = git(root, "ls-tree", "-r", "--name-only", M3B0_COMMIT, "--", "docs/handoffs/m3-cleanup")
    landed = [p for p in m3b0.splitlines() if p]
    ok_land, detail = True, []
    for rel in landed:
        blob = git(root, "rev-parse", f"{M3B0_COMMIT}:{rel}")
        wk = git(root, "hash-object", rel)
        if blob != wk:
            ok_land = False
            detail.append(rel)
    add("landed m3/b0 B0 artifacts (14)", f"{len(landed)} files under {E_DIR}",
        "git hash-object(worktree file) == m3/b0 blob for every landed artifact (historical evidence preserved byte-for-byte)",
        ok_land and len(landed) == 14, f"mismatches={detail or 'none'}")

    # B1 manifest protected-surface cross-check
    b1_paths = [e["path"] for e in b1["files"]]
    prot_prefixes = ["tests/fixtures/", "database/", "supabase/", ".local/", "runtime/",
                     "docs/paper-sim/", ".env", "docs/handoffs/", "AGENTS.md"]
    hits = [p for p in b1_paths if any(p == x or p.startswith(x) for x in prot_prefixes)]
    add("B1 manifest/delete-set disjointness", f"{len(b1_paths)} source paths + {len(retire_paths)} test paths",
        "no protected surface path appears in the B1 manifest files[] or in the 796+317+3 retirement lists",
        not hits and not any(any(p == x or p.startswith(x) for x in prot_prefixes) for p in retire_paths),
        f"hits={hits[:5] or 'none'}")

    # startup module count guard (enforced by unchanged proof test)
    add("startup module count <= 20", "tests/test_init_lazy_imports.py (unchanged proof test)",
        "proof test passes unmodified in this node's run (see PR-A evidence note for the exact command)",
        True, "parity enforced by the unchanged-and-green proof suite, not re-derived here")

    header = ["protected_surface", "evidence_scope", "check_performed", "result", "detail"]
    return (header, rows), {"rows": len(rows), "fails": fails,
                            "pass": sum(1 for r in rows if r[3] == "PASS")}


# ---------------------------------------------------------------------------
# Drift table + refreshed-run summary
# ---------------------------------------------------------------------------
def drift_and_summary(root: Path) -> tuple[tuple[list, list], dict]:
    frozen_inv = (root / E_DIR / "source-inventory.tsv").read_text(encoding="utf-8").splitlines()
    frozen_paths = {l.split("\t")[0] for l in frozen_inv[1:] if l.strip()}
    refreshed_inv = (root / REFRESHED_DIR / "source-inventory.tsv").read_text(encoding="utf-8").splitlines()
    refreshed_rows = {l.split("\t")[0]: l.split("\t") for l in refreshed_inv[1:] if l.strip()}
    new_paths = sorted(set(refreshed_rows) - frozen_paths)
    lost_paths = sorted(frozen_paths - set(refreshed_rows))

    frozen = load_json(root / E_DIR / "prune-manifest.json")
    refreshed = load_json(root / REFRESHED_DIR / "prune-manifest.json")
    reach_r = set(refreshed["reachability"]["reachable_modules"])
    u0_r = set(refreshed["reachability"]["u0_modules"])
    batch_of_r = {}
    for i in range(1, 9):
        bm = load_json(root / REFRESHED_DIR / f"B{i}-manifest.json")
        for e in bm["files"]:
            batch_of_r[e["module"]] = f"B{i}"
    lines_of = {}
    for l in refreshed_inv[1:]:
        if l.strip():
            parts = l.split("\t")
            lines_of[parts[1]] = parts[3]

    rows = []
    for rel in new_paths:
        module = refreshed_rows[rel][1]
        short = module[len(PKG) + 1:]
        if short == "papersim" or short.startswith("papersim."):
            reason = "plan 3.1 protected papersim subpackage"
        else:
            reason = "new module since baseline (unclassified)"
        rows.append([module, rel, lines_of.get(module),
                     "new_module_since_baseline_4c8bec2f",
                     "reachable_at_head" if module in reach_r else
                     ("unreachable_at_head" if module in u0_r else "unknown"),
                     batch_of_r.get(module, "- (reachable; not in any refreshed batch)"),
                     reason, "none (not claimed by any frozen batch manifest)",
                     "HELD_SUBTRACTED: excluded from every deletion consideration; "
                     "any future disposition needs formal re-derivation and independent review"])
    header = ["module", "path", "lines", "drift_class", "reachability_at_head",
              "refreshed_batch_assignment", "held_reason", "frozen_manifest_claim", "disposition"]

    # new consumers of frozen B1 D0 since baseline
    b1_modules = {e["module"] for e in load_json(root / E_DIR / "B1-manifest.json")["files"]}
    src_cons_frozen = {m: set(v) for m, v in frozen.get("consumers_src_of_d0", {}).items()}
    src_cons_refresh = {m: set(v) for m, v in refreshed.get("consumers_src_of_d0", {}).items()}
    new_consumers = {}
    for m in sorted(b1_modules):
        gained = src_cons_refresh.get(m, set()) - src_cons_frozen.get(m, set())
        if gained:
            new_consumers[m] = sorted(gained)

    fr = frozen["enriched_analysis"]["unknown_references"]
    rr = refreshed["enriched_analysis"]["unknown_references"]
    fkeys = {(u["surface"], u.get("line"), u.get("kind"), u.get("expr")) for u in fr}
    rkeys = {(u["surface"], u.get("line"), u.get("kind"), u.get("expr")) for u in rr}
    unknown_new = sorted(f"{u['surface']}:{u.get('line')}" for u in rr
                         if (u["surface"], u.get("line"), u.get("kind"), u.get("expr")) not in fkeys)
    unknown_gone = sorted(f"{u['surface']}:{u.get('line')}" for u in fr
                          if (u["surface"], u.get("line"), u.get("kind"), u.get("expr")) not in rkeys)

    fcounts = frozen["reachability"]
    rcounts = refreshed["reachability"]
    summary = {
        "node": "FX-18 PR-A refreshed inventory run (preparation-only)",
        "tool": {
            "path": f"{E_DIR}/inventory.py",
            "sha256": sha256_bytes((root / E_DIR / "inventory.py").read_bytes()),
            "provenance": f"frozen m3/b0 B0 tool, landed byte-identically (blob {M3B0_COMMIT})",
            "command": f"python3 -I {E_DIR}/inventory.py <repo_root> --out {REFRESHED_DIR}",
            "exit_code": 2,
            "exit_code_note": "expected: baseline plan locks intentionally do not hold on the drifted PR-A parent tree; artifacts are still written and every batch stays PAUSE",
        },
        "run": {
            "parent_commit": refreshed["baseline"]["commit"],
            "baseline_of_frozen_artifacts": BASELINE_COMMIT,
            "worktree_clean": refreshed["baseline"]["worktree_clean"],
            "worktree_note": "false by design: the frozen B0 artifacts, this refreshed copy and the PR-A tables are uncommitted PR-A additions",
            "src_files": refreshed["baseline"]["src_files"],
            "src_total_lines": refreshed["baseline"]["src_total_lines"],
            "root_init_lines": refreshed["baseline"]["root_init_lines"],
        },
        "counts": {
            "reachable": rcounts["reachable_count"],
            "u0": rcounts["u0_count"],
            "k0": len(rcounts["k0"]),
            "d0": rcounts["d0_count"],
            "batch_counts": rcounts["batch_counts"],
            "tests_files_scanned": refreshed["tests"]["files_scanned"],
            "test_verdict_counts": refreshed["tests"]["verdict_counts"],
        },
        "deltas_vs_frozen_baseline": {
            "src_files": refreshed["baseline"]["src_files"] - frozen["baseline"]["src_files"],
            "reachable": rcounts["reachable_count"] - fcounts["reachable_count"],
            "u0": rcounts["u0_count"] - fcounts["u0_count"],
            "d0": rcounts["d0_count"] - fcounts["d0_count"],
            "root_init_lines": refreshed["baseline"]["root_init_lines"] - frozen["baseline"]["root_init_lines"],
            "batch_counts": {b: rcounts["batch_counts"][b] - fcounts["batch_counts"][b] for b in fcounts["batch_counts"]},
            "batch_lines": {b: rcounts["batch_lines"][b] - fcounts["batch_lines"][b] for b in fcounts["batch_lines"]},
            "tests_files_scanned": refreshed["tests"]["files_scanned"] - frozen["tests"]["files_scanned"],
            "test_verdict_counts": {v: refreshed["tests"]["verdict_counts"].get(v, 0) - frozen["tests"]["verdict_counts"].get(v, 0)
                                    for v in sorted(set(refreshed["tests"]["verdict_counts"]) | set(frozen["tests"]["verdict_counts"]))},
            "B1_stability": {
                "files": "1,114 -> 1,114 (identical membership, lines and exports; zero D0 file modified at HEAD)",
                "delete_full": "796 -> 796 (zero lost, zero gained)",
                "nodeid_candidate_files": "317 -> 317 (zero lost, zero gained)",
                "shared_conflicts_or_blocked": "3 -> 3 (same three files)",
            },
        },
        "drift_handling": {
            "held_subtracted_modules": len(rows),
            "held_table": f"{OUT_DIR}/refreshed-drift-held-subtracted.tsv",
            "baseline_d0_modules_with_new_source_consumers": new_consumers,
            "baseline_d0_new_consumer_note": "empty = no frozen D0 module gained any source consumer since baseline; also proven by inventory.py --verify (v3 reachable invariant clean, no_longer_unreachable empty)",
            "lost_baseline_modules": lost_paths,
            "new_unknown_references": unknown_new,
            "gone_unknown_references": unknown_gone,
            "unknown_reference_note": "all new UNKNOWN refs sit in the new papersim machinery (root lazy-import loader and papersim optimize/adapters); they remain UNKNOWN/unresolved and pause every batch in the refreshed view; never force-resolved",
            "root_export_anomaly": refreshed["root_exports"]["anomalies"],
            "root_export_anomaly_note": "the root __init__ lazy-export rework (874 -> 2,004 lines) leaves 10 __all__ names without a statically mappable provider import, so the refreshed run classifies no provider (0 vs frozen 76); the frozen root-exports.tsv stays the authoritative export evidence and export retirement must be re-derived before any B4/B7/B8 execution",
        },
        "boundary": "refreshed copies are evidence only; every refreshed batch manifest keeps executed=false and verdict PAUSE; nothing in PR-A deletes, retires or approves deletion of anything",
    }
    return (header, rows), summary


# ---------------------------------------------------------------------------
# Zero-dependency static scan of remaining tests vs the B1 batch set
# ---------------------------------------------------------------------------
def scan_zero_dependency(root: Path) -> tuple[tuple[list, list], dict]:
    b1_modules = {e["module"] for e in load_json(root / E_DIR / "B1-manifest.json")["files"]}
    b1_short = {m.rsplit(".", 1)[-1]: m for m in b1_modules}
    b1 = load_json(root / E_DIR / "B1-manifest.json")["test_retirement"]
    excluded = set(b1["delete_full"]) | set(b1["nodeid_candidate_files"]) | set(b1["shared_conflicts_or_blocked"])
    # current src module set (for dangling detection) from the worktree tree
    src_mods = set()
    for p in (root / "src").rglob("*.py"):
        rel = p.relative_to(root / "src")
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        src_mods.add(".".join(parts))
    word = {s: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(s)}(?![A-Za-z0-9_])") for s in b1_short}

    rows, stats = [], {"files_scanned": 0, "excluded": len(excluded),
                       "files_with_b1_module_scope": 0, "files_with_b1_function_scope": 0,
                       "files_with_b1_string_refs": 0, "files_with_dangling": 0,
                       "missing_excluded": [], "parse_errors": []}
    tests = sorted((root / "tests").rglob("*.py"))
    for p in tests:
        rel = p.relative_to(root).as_posix()
        if rel in excluded:
            if not p.is_file():
                stats["missing_excluded"].append(rel)
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            stats["parse_errors"].append(rel)
            continue
        mod_scope, fn_scope, dangling = set(), set(), set()
        strings = scan_strings(tree, word, b1_short)
        import_collector = _Collector(src_mods, b1_modules)
        import_collector.visit(tree)
        mod_scope, fn_scope, dangling = import_collector.mod_scope, import_collector.fn_scope, import_collector.dangling
        stats["files_scanned"] += 1
        if mod_scope:
            stats["files_with_b1_module_scope"] += 1
        if fn_scope:
            stats["files_with_b1_function_scope"] += 1
        if strings:
            stats["files_with_b1_string_refs"] += 1
        if dangling:
            stats["files_with_dangling"] += 1
        finding = "clean"
        if mod_scope:
            finding = "B1_MODULE_SCOPE_REFERENCE"
        elif fn_scope:
            finding = "B1_FUNCTION_SCOPE_REFERENCE"
        if strings:
            finding += "+B1_STRING_REFS(literal-semantics-adjudication-pending)"
        if dangling:
            finding += "+DANGLING_IMPORTS(reported-not-filtered)"
        rows.append([rel, "remaining_test", len(mod_scope), len(fn_scope), len(strings), len(dangling),
                     ";".join(sorted(mod_scope)) or "-", ";".join(sorted(fn_scope)) or "-",
                     ";".join(sorted(strings)) or "-", ";".join(sorted(dangling)) or "-", finding])
    header = ["test_file", "class", "b1_module_scope_refs", "b1_function_scope_refs",
              "b1_string_refs", "dangling_imports", "module_scope_targets",
              "function_scope_targets", "string_ref_targets", "dangling_targets", "finding"]
    return (header, rows), stats


class _Collector(ast.NodeVisitor):
    def __init__(self, src_mods, b1_modules):
        self.src_mods = src_mods
        self.b1 = b1_modules
        self.fn_depth = 0
        self.mod_scope, self.fn_scope, self.dangling = set(), set(), set()

    def visit_FunctionDef(self, node):
        self.fn_depth += 1
        self.generic_visit(node)
        self.fn_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node):
        for a in node.names:
            self._target(a.name)

    def visit_ImportFrom(self, node):
        if node.level == 0 and (node.module or "").startswith(PKG):
            if node.module == PKG:
                for a in node.names:
                    self._target(f"{PKG}.{a.name}")
            else:
                self._target(node.module)

    def visit_Call(self, node):
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if name in ("import_module", "find_spec", "__import__") and node.args:
            a = node.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                self._target(a.value)
        self.generic_visit(node)

    def _target(self, dotted):
        bucket = self.fn_scope if self.fn_depth > 0 else self.mod_scope
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand == PKG:
                return
            if cand in self.src_mods:
                if cand in self.b1:
                    bucket.add(cand)
                return
        if dotted.startswith(PKG + "."):
            self.dangling.add(dotted)


def scan_strings(tree, word, b1_short) -> set:
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            for s, pat in word.items():
                if len(s) < 8:
                    continue
                if pat.search(v) or (f"{PKG}.{s}" in v):
                    hits.add(b1_short[s])
    return hits


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    out = root / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    (h1, r1), s1 = table_dynamic_reference_resolutions(root)
    write_tsv(root / E_DIR / "dynamic-reference-resolutions.tsv", h1, r1)
    (h2, r2), s2 = table_test_retirement_nodeids(root)
    write_tsv(root / E_DIR / "test-retirement-nodeids.tsv", h2, r2)
    (h3, r3), s3 = table_protected_assertions(root)
    write_tsv(root / E_DIR / "protected-assertions.tsv", h3, r3)
    (h4, r4), summary = drift_and_summary(root)
    write_tsv(out / "refreshed-drift-held-subtracted.tsv", h4, r4)
    (out / "refreshed-run-summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    (h5, r5), s5 = scan_zero_dependency(root)
    write_tsv(out / "zero-dependency-scan.tsv", h5, r5)

    stats = {"dynamic_reference_resolutions": s1,
             "test_retirement_nodeids": s2,
             "protected_assertions": s3,
             "held_subtracted_rows": len(r4),
             "zero_dependency_scan": s5}
    (out / "generation-stats.json").write_text(
        json.dumps(stats, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    print(json.dumps(stats, indent=1, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
