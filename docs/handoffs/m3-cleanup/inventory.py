#!/usr/bin/env python3
"""M3 B0 reachability inventory and prune-manifest generator.

One-off engineering tool for FX-18 prune batches B1-B8 (plan m3-plan-formal.md
sections 3.1-3.4). Reads the repository strictly as DATA (ast.parse / bytes):
it never imports or executes repo code, never touches the database, never
modifies or deletes source files, and writes only into the engineering
evidence directory ``docs/handoffs/m3-cleanup/``. paper-only / report-only /
readonly.

Usage:
  python3 -I inventory.py <repo_root>            # generate baseline artifacts
  python3 -I inventory.py <repo_root> --verify   # re-proof the current tree
                                                 # against committed artifacts

The first layer reuses the audit semantics of
docs/handoffs/2026-11-12-audit/evidence/audit_static.py (entry set: console
scripts, __main__, __name__ == "__main__" guards, import targets of scripts/
and tools/; reach BFS without root-__init__ eager fan-out) so U0 reproduces
the historical unreachable_modules.tsv module set. On top of that, this tool
adds the B0 evidence required by plan 3.3: root-export symbol resolution
(FX-20 name mapping), dynamic import resolution (importlib / __import__ /
subprocess -m / constant strings) with UNKNOWN detection, retained-closure
and K0-leak checks, cross-family strongly-connected-component analysis,
per-path test retirement analysis (module-scope vs function-scope, exact
import chains -- no prefix guessing), doc registry entry location, and
per-batch B1-B8 manifests with dependency-check verdicts.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

PKG = "polymarket_alpha_lab"
E_DIR = Path("docs/handoffs/m3-cleanup")
HIST_TSV = Path("docs/handoffs/2026-11-12-audit/evidence/unreachable_modules.tsv")

# ---- plan locks (m3-plan-formal.md sections 1.2 / 3.2 / 4.1) ----------------
PLAN_LOCKS = {
    "u0_count": 2438,
    "u0_canonical_sha256": "6a35d24c748cfd26880401cc6dab84eb36a327901cc2896c0975adfff51c1f78",
    "d0_count": 2395,
    "d0_canonical_sha256": "f32335000022ea6b8bb5c5466e8d3ab362f475fed6b000c6f75385989ad676b8",
    "k0_count": 43,
    "reachable_count": 429,
    "src_files": 2868,
    "root_export_names": 369,
    "root_export_providers": 76,
    "root_exports_reachable": 165,
    "root_exports_retained": 33,
    "root_exports_to_archive": 171,
    "hist_tsv_plan_bytes": 226904,
    "hist_tsv_plan_sha256": "227aa2dc98ecd8e67ed5ec806c44a09c3b5d62301c31b3bdcc540a27fb82ceeb",
    "batch_counts": {"B1": 1114, "B2": 399, "B3": 332, "B4": 114,
                     "B5": 65, "B6": 90, "B7": 98, "B8": 183},
    "batch_lines": {"B1": 1271697, "B2": 402380, "B3": 257173, "B4": 106707,
                    "B5": 63194, "B6": 46510, "B7": 37446, "B8": 110879},
}

# 13 paper-broker / order-lifecycle retention seeds (plan 3.1).
BROKER_SEEDS = [
    "paper_broker", "paper_broker_db_row", "paper_broker_history",
    "paper_broker_load", "paper_broker_psycopg", "paper_broker_store",
    "paper_order_lifecycle", "paper_order_lifecycle_db_history",
    "paper_order_lifecycle_db_history_load", "paper_order_lifecycle_db_row",
    "paper_order_lifecycle_psycopg", "paper_order_lifecycle_store",
    "supabase_paper_broker_config",
]

# K0: 43 unreachable retained modules (plan 3.1).
K0_SHORT = """commodities_gold_team commodities_oil_team crypto_btc_evidence_catalog
crypto_btc_evidence_policy crypto_btc_evidence_registry crypto_btc_evidence_resolution
crypto_btc_forecast_service crypto_btc_team crypto_eth_team equity_indices_team
macro_rates_team paper_broker_history paper_broker_load paper_order_lifecycle_db_history
paper_order_lifecycle_db_history_load politics_team research_claude_exec
research_claude_operator research_claude_profile research_codex_exec research_codex_process
research_codex_profile research_linux_relay research_process research_process_linux
research_process_windows sports_basketball_team sports_other_team sports_soccer_team
supabase_team_evidence_aggregation_config team_evaluation_attempt_latest_read
team_evidence_aggregation team_evidence_aggregation_allocation
team_evidence_aggregation_attempt_psycopg team_evidence_aggregation_attempt_store
team_evidence_aggregation_codec team_evidence_aggregation_db_row
team_evidence_aggregation_temporal team_evidence_aggregation_types
team_evidence_aggregation_witness team_forecast_batch team_forecast_build_envelope
team_research_glm""".split()

BATCH_PREFIXES = [("B1", "research_"), ("B2", "market_"), ("B3", "strategy_"),
                  ("B4", "team_"), ("B5", "candidate_"), ("B6", "probability_"),
                  ("B7", "paper_")]
BATCH_ORDER = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"]
DOC_REGISTRIES = [
    "docs/index/phase1-module-index.md",
    "docs/testing/phase1-node-test-manifest.md",
    "docs/reports/phase1-report-registry.md",
]


def batch_of(module: str) -> str:
    short = module[len(PKG) + 1:] if module.startswith(PKG + ".") else module
    for name, prefix in BATCH_PREFIXES:
        if short.startswith(prefix):
            return name
    return "B8"


def canonical_sha(sorted_names) -> str:
    return hashlib.sha256(("\n".join(sorted_names) + "\n").encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def modname(path: Path, src_root: Path) -> str:
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def module_path(module: str) -> str:
    if module == PKG:
        return f"src/{PKG}/__init__.py"
    return f"src/{module.replace('.', '/')}.py"


def _unparse_safe(node) -> str:
    try:
        return ast.unparse(node)[:160]
    except Exception:
        return "<unparseable>"


# ----------------------------------------------------------------------------
# single-file parsing
# ----------------------------------------------------------------------------
class Facts:
    __slots__ = ("path", "module", "sha256", "size", "lines", "has_main",
                 "audit_static", "audit_lazy", "dyn_resolved", "dyn_unknown",
                 "dyn_detail", "root_symbol", "dangling", "mod_scope",
                 "fn_scope", "string_refs")

    def __init__(self, path, module):
        self.path = path
        self.module = module
        self.sha256 = ""
        self.size = 0
        self.lines = 0
        self.has_main = False
        self.audit_static = set()
        self.audit_lazy = set()
        self.dyn_resolved = set()
        self.dyn_unknown = []
        self.dyn_detail = []
        self.root_symbol = set()
        self.dangling = []
        self.mod_scope = set()
        self.fn_scope = {}
        self.string_refs = set()


FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def resolve_rel(mod: str, is_pkg: bool, level: int, target) -> str:
    base = mod.split(".")
    if not is_pkg:
        base = base[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    out = ".".join(base)
    if target:
        out = f"{out}.{target}" if out else target
    return out


def _module_level_const_env(tree) -> dict:
    env = {}

    def evaln(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = evaln(node.left), evaln(node.right)
            return a + b if isinstance(a, str) and isinstance(b, str) else None
        if isinstance(node, ast.JoinedStr):
            parts = [evaln(v) for v in node.values]
            return "".join(parts) if all(isinstance(p, str) for p in parts) else None
        return None

    def visit(nodes):
        for n in nodes:
            if isinstance(n, FUNC_NODES):
                continue
            if isinstance(n, ast.ClassDef):
                visit(n.body)
                continue
            pairs = []
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        pairs.append((t, n.value))
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) \
                    and n.value is not None:
                pairs.append((n.target, n.value))
            for t, v in pairs:
                val = evaln(v)
                if isinstance(val, str):
                    env[t.id] = val
            for fld in ("body", "orelse", "finalbody"):
                v = getattr(n, fld, None)
                if isinstance(v, list):
                    visit(v)

    visit(tree.body)
    return env


def _has_main_guard(tree) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            t = node.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "__name__"):
                return True
    return False


def parse_source(path: Path, module: str, known: set, exports: dict,
                 collect_strings: bool = False, short_names: dict | None = None):
    """Parse one file as data.

    audit_static/audit_lazy mirror audit_static.py's imports_of exactly (full
    AST walk: function-local, conditional, platform-branch imports included;
    string constants equal to known module names count as lazy imports).
    Everything beyond that is the enriched B0 layer.
    """
    f = Facts(path, module)
    data = path.read_bytes()
    f.sha256 = hashlib.sha256(data).hexdigest()
    f.size = len(data)
    text = data.decode("utf-8")
    f.lines = len(text.splitlines())
    tree = ast.parse(text)
    is_pkg = path.name == "__init__.py"
    env = _module_level_const_env(tree)

    from_importlib = set()
    pkg_aliases = set()
    audit_static, audit_lazy = set(), set()
    mod_scope, fn_scope = set(), defaultdict(set)
    scope_stack = []
    scope_module_level = [True]

    def record_scope(dep):
        if dep is None or dep == module:
            return
        if all(scope_module_level):
            mod_scope.add(dep)
        else:
            fn_scope[".".join(scope_stack) or "<anon>"].add(dep)

    def eval_target(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            v = env.get(node.id)
            return v if isinstance(v, str) else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = eval_target(node.left), eval_target(node.right)
            return a + b if isinstance(a, str) and isinstance(b, str) else None
        if isinstance(node, ast.JoinedStr):
            parts = [eval_target(v) for v in node.values]
            return "".join(parts) if all(isinstance(p, str) for p in parts) else None
        return None

    def resolve_dyn(value, arg_node, kind):
        if value is None:
            f.dyn_unknown.append({"file": str(path), "line": getattr(arg_node, "lineno", 0),
                                  "kind": kind, "expr": _unparse_safe(arg_node)})
            return
        if value in known:
            f.dyn_resolved.add(value)
            f.dyn_detail.append({"target": value, "line": getattr(arg_node, "lineno", 0),
                                 "kind": kind, "resolved": "direct"})
            record_scope(value)
            return
        prefixed = f"{PKG}.{value}"
        if prefixed in known:
            f.dyn_resolved.add(prefixed)
            f.dyn_detail.append({"target": prefixed, "line": getattr(arg_node, "lineno", 0),
                                 "kind": kind, "resolved": "pkg_prefixed"})
            record_scope(prefixed)
            return
        f.dyn_detail.append({"target": value, "line": getattr(arg_node, "lineno", 0),
                             "kind": kind, "resolved": "external_or_dangling"})

    def visit(node):
        for child in ast.iter_child_nodes(node):
            pushed = False
            if isinstance(child, FUNC_NODES):
                scope_stack.append(getattr(child, "name", "<lambda>"))
                scope_module_level.append(False)
                pushed = True
            elif isinstance(child, ast.ClassDef):
                scope_stack.append(child.name)
                scope_module_level.append(scope_module_level[-1])
                pushed = True

            if isinstance(child, ast.Import):
                for a in child.names:
                    audit_static.add(a.name)
                    if a.name in known:
                        record_scope(a.name)
                    if a.name == PKG:
                        pkg_aliases.add(a.asname or PKG)
            elif isinstance(child, ast.ImportFrom):
                base = (resolve_rel(module, is_pkg, child.level, child.module)
                        if child.level else (child.module or ""))
                audit_static.add(base)
                if base in known:
                    record_scope(base)
                if child.level == 0 and base == "importlib":
                    for a in child.names:
                        if a.name in ("import_module", "find_spec"):
                            from_importlib.add(a.asname or a.name)
                if child.level == 0 and base == PKG:
                    for a in child.names:
                        cand = f"{PKG}.{a.name}"
                        if cand in known:
                            audit_static.add(cand)
                            record_scope(cand)
                        elif a.name in exports:
                            prov = exports[a.name]["provider"]
                            f.root_symbol.add(prov)
                            record_scope(prov)
                        else:
                            f.dangling.append({"file": str(path), "line": child.lineno,
                                               "name": a.name})
                else:
                    for a in child.names:
                        cand = f"{base}.{a.name}"
                        if cand in known:
                            audit_static.add(cand)
                            record_scope(cand)
            elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                if child.value in known:
                    audit_lazy.add(child.value)
                    record_scope(child.value)
                if collect_strings and short_names is not None:
                    s = child.value
                    hit = None
                    if s.endswith(".py") and s[:-3] in short_names:
                        hit = s[:-3]
                    if hit is not None:
                        f.string_refs.add(short_names[hit])
                        record_scope(short_names[hit])
            elif isinstance(child, ast.Attribute):
                base_expr = child.value
                if isinstance(base_expr, ast.Name) and base_expr.id in pkg_aliases:
                    attr = child.attr
                    if attr in exports:
                        prov = exports[attr]["provider"]
                        f.root_symbol.add(prov)
                        record_scope(prov)
                    cand = f"{PKG}.{attr}"
                    if cand in known:
                        f.root_symbol.add(cand)
                        record_scope(cand)
            elif isinstance(child, ast.Call):
                fn = child.func
                dyn_kind = None
                arg_node = None
                if isinstance(fn, ast.Name) and (fn.id == "__import__"
                                                 or fn.id in from_importlib):
                    dyn_kind = f"name:{fn.id}"
                    arg_node = child.args[0] if child.args else None
                elif isinstance(fn, ast.Attribute) and fn.attr in (
                        "import_module", "__import__", "find_spec"):
                    dyn_kind = f"attr:{fn.attr}"
                    arg_node = child.args[0] if child.args else None
                if dyn_kind and arg_node is not None:
                    resolve_dyn(eval_target(arg_node), arg_node, dyn_kind)
                if isinstance(fn, ast.Attribute) and fn.attr in (
                        "run", "Popen", "call", "check_call", "check_output"):
                    for a in child.args:
                        if isinstance(a, (ast.List, ast.Tuple)):
                            for i, el in enumerate(a.elts):
                                if (isinstance(el, ast.Constant) and el.value == "-m"
                                        and i + 1 < len(a.elts)):
                                    resolve_dyn(eval_target(a.elts[i + 1]),
                                                a.elts[i + 1], "subprocess_-m")

            visit(child)
            if pushed:
                scope_stack.pop()
                scope_module_level.pop()

    visit(tree)
    f.audit_static = {s for s in audit_static if s in known}
    f.audit_lazy = audit_lazy - f.audit_static - {module}
    f.mod_scope = {d for d in mod_scope if d in known}
    f.fn_scope = {k: {d for d in v if d in known} for k, v in fn_scope.items() if v}
    f.has_main = _has_main_guard(tree)
    return f


def load_root_exports(init_path: Path, known: set):
    """Map the 369 root __init__ exports to provider modules (FX-20 mapping)."""
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    exports = {}
    all_names = []
    anomalies = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                and node.module.startswith(PKG + ".") and node.module in known:
            for a in node.names:
                public = a.asname or a.name
                if public in exports:
                    anomalies.append(f"duplicate export name {public}")
                exports[public] = {"provider": node.module, "symbol": a.name,
                                   "line": node.lineno}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == PKG:
            anomalies.append(f"unexpected root from-import at line {node.lineno}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(PKG):
                    anomalies.append(f"plain import {a.name} at line {node.lineno}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__" \
                        and isinstance(node.value, ast.List):
                    for el in node.value.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            all_names.append(el.value)
    missing = [n for n in all_names if n not in exports]
    extra = [n for n in exports if n not in set(all_names)]
    if missing:
        anomalies.append(f"__all__ names without provider import: {missing[:10]}")
    if extra:
        anomalies.append(f"imported names missing from __all__: {extra[:10]}")
    return exports, all_names, anomalies


# ----------------------------------------------------------------------------
# graph helpers
# ----------------------------------------------------------------------------
def reach(edges: dict, starts, extra_lazy: dict | None = None):
    """audit_static.reach with use_init=False semantics."""
    seen, q = set(), deque(starts)
    lazy = extra_lazy or {}
    while q:
        m = q.popleft()
        if m in seen or m not in edges:
            continue
        seen.add(m)
        if m == PKG:
            continue
        q.extend((edges[m] | lazy.get(m, set())) - seen)
    return seen


def tarjan_scc(nodes, edges):
    """Iterative Tarjan; returns list of components (lists)."""
    index, low = {}, {}
    onstack, stack = set(), []
    counter = [0]
    sccs = []
    for root in sorted(nodes):
        if root in index:
            continue
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        onstack.add(root)
        work = [(root, iter(sorted(edges.get(root, ()))))]
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in nodes:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    onstack.add(nxt)
                    work.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in onstack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    onstack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                sccs.append(sorted(comp))
    return sccs


# ----------------------------------------------------------------------------
# collection
# ----------------------------------------------------------------------------
def collect(root: Path, include_tests: bool = True):
    src_root = root / "src"
    files = sorted(src_root.rglob("*.py"))
    mods = {modname(f, src_root): f for f in files}
    known = set(mods)

    exports, all_names, export_anomalies = load_root_exports(
        src_root / PKG / "__init__.py", known)
    short_names = {m[len(PKG) + 1:]: m for m in known if m != PKG}

    facts = {}
    for m, p in sorted(mods.items()):
        facts[m] = parse_source(p, m, known, exports,
                                collect_strings=(m == PKG), short_names=short_names)

    def parse_outside(paths):
        out = {}
        for p in paths:
            key = str(p.relative_to(root))
            out[key] = parse_source(p, p.stem, known, exports,
                                    collect_strings=True, short_names=short_names)
        return out

    script_facts = parse_outside(sorted((root / "scripts").glob("*.py")))
    tool_facts = parse_outside(sorted((root / "tools").rglob("*.py")))
    test_facts = parse_outside(sorted((root / "tests").rglob("*.py"))) \
        if include_tests else {}

    wf_hits = []
    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        pat = re.compile(rf"\b{PKG}(?:\.[a-z_][a-z0-9_]*)*\b")
        for p in sorted(wf_dir.glob("*.yml")):
            for i, line in enumerate(p.read_text(encoding="utf-8",
                                                 errors="replace").splitlines(), 1):
                for hit in pat.findall(line):
                    if hit in known:
                        wf_hits.append({"workflow": str(p.relative_to(root)),
                                        "line": i, "module": hit})
    return {"src_root": src_root, "mods": mods, "known": known, "facts": facts,
            "exports": exports, "all_names": all_names,
            "export_anomalies": export_anomalies,
            "script_facts": script_facts, "tool_facts": tool_facts,
            "test_facts": test_facts, "workflow_hits": wf_hits}


def collect_light(root: Path, parse_modules: set):
    """Verify-mode collection: AST-parses only the retained modules (baseline
    reachable set + K0 + root package) plus scripts/ and tools/; the remaining
    src files are registered by filename only (their sha256 is still checked
    by the caller). Sound for the R-invariant because pending D0 modules are
    never entries and no retained module may depend on them (both proven at
    B0); any baseline drift is flagged for re-baselining instead."""
    src_root = root / "src"
    files = sorted(src_root.rglob("*.py"))
    mods = {modname(f, src_root): f for f in files}
    known = set(mods)
    exports, _, _ = load_root_exports(src_root / PKG / "__init__.py", known)

    facts = {}
    for m in sorted(parse_modules & set(mods)):
        facts[m] = parse_source(mods[m], m, known, exports)

    def parse_outside(paths):
        out = {}
        for p in paths:
            key = str(p.relative_to(root))
            out[key] = parse_source(p, p.stem, known, exports)
        return out

    return {"src_root": src_root, "mods": mods, "known": known, "facts": facts,
            "exports": exports,
            "script_facts": parse_outside(sorted((root / "scripts").glob("*.py"))),
            "tool_facts": parse_outside(sorted((root / "tools").rglob("*.py"))),
            "test_facts": {}, "workflow_hits": []}


def build_graphs(data):
    facts = data["facts"]
    audit_edges, audit_lazy, enr_edges = {}, {}, {}
    for m, f in facts.items():
        audit_edges[m] = set(f.audit_static) - {m}
        audit_lazy[m] = set(f.audit_lazy) - {m}
        enr_edges[m] = (set(f.audit_static) | set(f.audit_lazy)
                        | set(f.dyn_resolved) | set(f.root_symbol)) - {m}

    console = [f"{PKG}.cli", f"{PKG}.__main__"]
    main_guard = sorted(m for m, f in facts.items() if f.has_main)

    def targets(fd, audit_only):
        out = set()
        for f in fd.values():
            if audit_only:
                out |= f.audit_static | f.audit_lazy
            else:
                out |= f.audit_static | f.audit_lazy | f.dyn_resolved | f.root_symbol
        return out

    script_targets_audit = targets(data["script_facts"], True)
    tool_targets_audit = targets(data["tool_facts"], True)
    script_targets_enr = targets(data["script_facts"], False)
    tool_targets_enr = targets(data["tool_facts"], False)
    wf_modules = sorted({h["module"] for h in data["workflow_hits"]})

    entries_audit = set(console) | set(main_guard) | script_targets_audit | tool_targets_audit
    entries_enr = entries_audit | script_targets_enr | tool_targets_enr | set(wf_modules)
    reachable = reach(audit_edges, entries_audit, audit_lazy) - {PKG}
    reachable_enr = reach(enr_edges, entries_enr) - {PKG}
    return {"audit_edges": audit_edges, "audit_lazy": audit_lazy,
            "enr_edges": enr_edges, "console": console, "main_guard": main_guard,
            "script_targets": sorted(script_targets_audit),
            "tool_targets": sorted(tool_targets_audit),
            "workflow_modules": wf_modules, "entries": entries_audit,
            "reachable": reachable, "reachable_enr": reachable_enr}


# ----------------------------------------------------------------------------
# generation
# ----------------------------------------------------------------------------
def _git(root: Path, *args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, check=False).stdout.strip()
    except OSError:
        return ""


def generate(root: Path, out_dir: Path) -> int:
    t0 = time.time()
    data = collect(root, include_tests=True)
    graphs = build_graphs(data)
    facts, known = data["facts"], data["known"]

    # -- U0 reproduction ----------------------------------------------------
    tsv_path = root / HIST_TSV
    tsv_bytes = tsv_path.read_bytes()
    tsv_sha = hashlib.sha256(tsv_bytes).hexdigest()
    tsv_lines = tsv_bytes.decode("utf-8").splitlines()
    hist_u0 = sorted({ln.split("\t")[0] for ln in tsv_lines[1:] if ln.strip()})

    all_mods = set(known) - {PKG}
    reachable = graphs["reachable"]
    u0 = sorted(all_mods - reachable)
    u0_matches_hist = set(u0) == set(hist_u0)

    k0 = sorted(f"{PKG}.{n}" for n in K0_SHORT)
    k0_bad = [m for m in k0 if m not in known]
    k0_not_u0 = [m for m in k0 if m not in set(u0)]
    d0 = sorted(set(u0) - set(k0))

    locks = {
        "u0_count": (PLAN_LOCKS["u0_count"], len(u0)),
        "u0_canonical_sha256": (PLAN_LOCKS["u0_canonical_sha256"], canonical_sha(u0)),
        "d0_count": (PLAN_LOCKS["d0_count"], len(d0)),
        "d0_canonical_sha256": (PLAN_LOCKS["d0_canonical_sha256"], canonical_sha(d0)),
        "k0_count": (PLAN_LOCKS["k0_count"], len([m for m in k0 if m in set(u0)])),
        "reachable_count": (PLAN_LOCKS["reachable_count"], len(reachable)),
        "src_files": (PLAN_LOCKS["src_files"], len(known)),
    }
    batch_counts = {b: sum(1 for m in d0 if batch_of(m) == b) for b in BATCH_ORDER}
    batch_lines = {b: sum(facts[m].lines for m in d0 if batch_of(m) == b)
                   for b in BATCH_ORDER}
    for b in BATCH_ORDER:
        locks[f"batch_{b}_count"] = (PLAN_LOCKS["batch_counts"][b], batch_counts[b])
        locks[f"batch_{b}_lines"] = (PLAN_LOCKS["batch_lines"][b], batch_lines[b])
    lock_fail = {k: {"plan": p, "actual": a} for k, (p, a) in locks.items() if p != a}

    # The historical TSV file-level checksums are recorded in the plan against a
    # CRLF copy. The committed file is LF-normalized; when the CRLF-conversion
    # hash reproduces the plan value exactly, the difference is a verified,
    # explained file-encoding artifact, not a set difference (the U0 canonical
    # hash above is the binding set-level lock).
    crlf_sha = hashlib.sha256(tsv_bytes.replace(b"\n", b"\r\n")).hexdigest()
    tsv_note = None
    tsv_lock_ok = (tsv_sha == PLAN_LOCKS["hist_tsv_plan_sha256"]
                   or crlf_sha == PLAN_LOCKS["hist_tsv_plan_sha256"])
    if (tsv_sha != PLAN_LOCKS["hist_tsv_plan_sha256"]
            and crlf_sha == PLAN_LOCKS["hist_tsv_plan_sha256"]):
        crlf_len = len(tsv_bytes.replace(b"\n", b"\r\n"))
        tsv_note = ("Committed TSV is LF-normalized; the plan's file-level bytes/SHA256 "
                    f"reproduce exactly under LF->CRLF conversion ({len(tsv_bytes)} -> "
                    f"{crlf_len} bytes, delta = 1 byte x {crlf_len - len(tsv_bytes)} lines). "
                    "The module set is identical and the canonical U0 hash matches the "
                    "plan lock; no source drift. Recorded as a verified, explained "
                    "file-encoding difference, not a set difference.")
    locks["hist_tsv_file_sha256"] = (PLAN_LOCKS["hist_tsv_plan_sha256"],
                                     tsv_sha if tsv_sha == PLAN_LOCKS["hist_tsv_plan_sha256"]
                                     else crlf_sha)
    lock_fail = {k: {"plan": p, "actual": a} for k, (p, a) in locks.items() if p != a}
    if not tsv_lock_ok:
        lock_fail["hist_tsv_file_sha256"] = {
            "plan": PLAN_LOCKS["hist_tsv_plan_sha256"],
            "actual_lf": tsv_sha, "actual_crlf": crlf_sha}

    # -- enriched consumer analysis ------------------------------------------
    enr = graphs["enr_edges"]
    reachable_enr = graphs["reachable_enr"]
    k0set, d0set = set(k0), set(d0)
    retained = reachable | k0set

    enr_violations, k0_leaks = [], []
    consumers_src = defaultdict(list)
    for m in sorted(all_mods):
        for tgt in sorted(enr.get(m, ())):
            if tgt in d0set:
                consumers_src[tgt].append(m)
                if m in k0set:
                    k0_leaks.append({"k0_module": m, "d0_dependency": tgt})
                    enr_violations.append({
                        "d0_module": tgt, "consumer": m, "consumer_class": "K0"})
                elif m in reachable:
                    enr_violations.append({
                        "d0_module": tgt, "consumer": m, "consumer_class": "reachable"})

    entry_consumers = defaultdict(list)
    for surface, fd in (("script", data["script_facts"]), ("tool", data["tool_facts"])):
        for key, f in sorted(fd.items()):
            for tgt in sorted(f.audit_static | f.audit_lazy | f.dyn_resolved | f.root_symbol):
                if tgt in d0set:
                    entry_consumers[tgt].append(f"{surface}:{key}")
    for h in data["workflow_hits"]:
        if h["module"] in d0set:
            entry_consumers[h["module"]].append(f"workflow:{h['workflow']}:{h['line']}")

    unknown_refs = []
    for m, f in sorted(facts.items()):
        for u in f.dyn_unknown:
            u = dict(u)
            u["surface"] = (f"src_d0:{batch_of(m)}" if m in d0set else
                            "src_k0" if m in k0set else
                            "src_reachable" if m in reachable else "src_pkg")
            unknown_refs.append(u)
    for surface, fd in (("script", data["script_facts"]), ("tool", data["tool_facts"]),
                        ("test", data["test_facts"])):
        for key, f in sorted(fd.items()):
            for u in f.dyn_unknown:
                u = dict(u)
                u["surface"] = f"{surface}:{key}"
                unknown_refs.append(u)
    unknown_retained = [u for u in unknown_refs
                        if not u["surface"].startswith(("src_d0:", "test:"))]

    # -- cross-family SCC over D0 --------------------------------------------
    d0_graph = {m: {t for t in enr.get(m, ()) if t in d0set} for m in d0set}
    sccs = [c for c in tarjan_scc(set(d0set), d0_graph) if len(c) > 1]
    cross_cycles = [{"members": c, "batches": sorted({batch_of(m) for m in c})}
                    for c in sccs if len({batch_of(m) for m in c}) > 1]

    # -- test retirement analysis (exact per-path import chains) --------------
    test_rows = []
    test_analysis = {}
    for key, f in sorted(data["test_facts"].items()):
        proj_deps = ((f.audit_static | f.audit_lazy | f.dyn_resolved
                      | f.root_symbol | f.string_refs) & known) - {PKG}
        d0_deps = {d for d in proj_deps if d in d0set}
        if not d0_deps and not f.dangling and not f.dyn_unknown:
            continue
        mod_scope = {d for d in f.mod_scope if d != PKG}
        mod_d0 = {d for d in mod_scope if d in d0set}
        fn_d0 = {}
        for qual, deps in f.fn_scope.items():
            hit = sorted(d for d in deps if d in d0set)
            if hit:
                fn_d0[qual] = hit
        batches_mod = sorted({batch_of(d) for d in mod_d0})
        batches_fn = sorted({batch_of(d) for ds in fn_d0.values() for d in ds})
        retained_deps = sorted(d for d in proj_deps if d in retained)
        other_mod = sorted(d for d in mod_scope if d not in d0set)
        if not mod_d0 and not fn_d0:
            verdict = "keep_with_flags"
        elif not mod_d0 and fn_d0:
            verdict = "nodeid_partial"
        else:
            full_ok = (not other_mod) and len(batches_mod) == 1 \
                and set(batches_fn) <= set(batches_mod)
            if full_ok:
                verdict = "retire_full"
            elif len(batches_mod) > 1:
                verdict = "conflict"
            else:
                verdict = "blocked_mixed"
        row = {
            "test_path": key, "verdict": verdict,
            "batches_module_scope": ";".join(batches_mod),
            "batches_function_scope": ";".join(batches_fn),
            "module_scope_d0_deps": ";".join(sorted(mod_d0)),
            "module_scope_retained_deps": ";".join(
                d for d in retained_deps if d in mod_scope),
            "nodeid_candidates": ";".join(sorted(fn_d0)),
            "unknown_dynamic": len(f.dyn_unknown),
            "dangling_root_symbols": ";".join(d["name"] for d in f.dangling),
        }
        test_rows.append(row)
        test_analysis[key] = row

    blockers = {b: set() for b in BATCH_ORDER}
    retire_full_by_batch = {b: [] for b in BATCH_ORDER}
    nodeid_by_batch = {b: set() for b in BATCH_ORDER}
    for key, row in test_analysis.items():
        v = row["verdict"]
        if v == "retire_full":
            retire_full_by_batch[row["batches_module_scope"]].append(key)
        elif v in ("conflict", "blocked_mixed"):
            for b in row["batches_module_scope"].split(";"):
                if b:
                    blockers[b].add(key)
        elif v == "nodeid_partial":
            for b in row["batches_function_scope"].split(";"):
                if b:
                    nodeid_by_batch[b].add(key)

    # Unknown dynamic references inside test files: these cannot be statically
    # resolved (f-string/variable/obfuscated join targets) and, per plan 3.3
    # item 6, no deletion permit may be issued until they are manually
    # reviewed. They are listed per batch for every test file that is still
    # present when that batch executes under the canonical order.
    test_unknown_by_file = {}
    for key, f in sorted(data["test_facts"].items()):
        if f.dyn_unknown:
            test_unknown_by_file[key] = f.dyn_unknown

    def unknown_tests_at(batch: str):
        out = []
        for key, refs in sorted(test_unknown_by_file.items()):
            row = test_analysis.get(key)
            if row is not None and row["verdict"] == "retire_full":
                if BATCH_ORDER.index(row["batches_module_scope"]) < BATCH_ORDER.index(batch):
                    continue  # file retired with an earlier batch
            out.append({"test": key, "count": len(refs), "refs": refs[:3]})
        return out

    # -- doc registry entries -------------------------------------------------
    doc_lines = {}
    for doc in DOC_REGISTRIES:
        p = root / doc
        doc_lines[doc] = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    doc_entries = {}
    for m in d0set:
        short = m[len(PKG) + 1:]
        pats = [re.compile(rf"polymarket_alpha_lab\.{re.escape(short)}\b"),
                re.compile(rf"src/polymarket_alpha_lab/{re.escape(short)}\.py\b"),
                re.compile(rf"`{re.escape(short)}`"),
                re.compile(rf"\b{re.escape(short)}\.py\b")]
        hits = {}
        for doc, lines in doc_lines.items():
            ln = [i for i, l in enumerate(lines, 1) if any(p.search(l) for p in pats)]
            if ln:
                hits[doc] = ln
        if hits:
            doc_entries[m] = hits

    # -- root exports classification ------------------------------------------
    export_rows = []
    for name in sorted(data["exports"]):
        info = data["exports"][name]
        prov = info["provider"]
        if prov in k0set:
            status, batch = "retained", "-"
        elif prov in d0set:
            status, batch = "to_archive", batch_of(prov)
        else:
            status, batch = "reachable", "-"
        export_rows.append((name, prov, info["symbol"], status, batch, info["line"]))
    n_reach = sum(1 for r in export_rows if r[3] == "reachable")
    n_ret = sum(1 for r in export_rows if r[3] == "retained")
    n_arch = sum(1 for r in export_rows if r[3] == "to_archive")
    locks["root_export_names"] = (PLAN_LOCKS["root_export_names"], len(data["all_names"]))
    locks["root_export_providers"] = (PLAN_LOCKS["root_export_providers"],
                                      len({r[1] for r in export_rows}))
    locks["root_exports_reachable"] = (PLAN_LOCKS["root_exports_reachable"], n_reach)
    locks["root_exports_retained"] = (PLAN_LOCKS["root_exports_retained"], n_ret)
    locks["root_exports_to_archive"] = (PLAN_LOCKS["root_exports_to_archive"], n_arch)
    lock_fail = {k: {"plan": p, "actual": a} for k, (p, a) in locks.items() if p != a}

    # -- write artifacts -------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    tool_sha = sha256_file(Path(__file__).resolve())
    baseline_commit = _git(root, "rev-parse", "HEAD")
    dirty = bool(_git(root, "status", "--porcelain"))

    inv_lines = ["path\tmodule\tsha256\tlines"]
    for m in sorted(known):
        f = facts[m]
        rel = f.path.relative_to(root).as_posix()
        inv_lines.append(f"{rel}\t{m}\t{f.sha256}\t{f.lines}")
    (out_dir / "source-inventory.tsv").write_text("\n".join(inv_lines) + "\n",
                                                  encoding="utf-8", newline="\n")

    ret_lines = ["module\tkind\treason\tunreachable\texcluded_batches"]
    u0set = set(u0)
    for n in BROKER_SEEDS:
        m = f"{PKG}.{n}"
        if m in known:
            ret_lines.append(f"{m}\tseed_broker\tplan 3.1 paper-broker/order-lifecycle "
                             f"seed\t{'yes' if m in u0set else 'no'}\tall")
    for m in k0:
        ret_lines.append(f"{m}\tk0_retained\tplan 3.1 K0 closure member\tyes\tall")
    protected = set()
    for m in sorted(known):
        if m == PKG:
            continue
        short = m[len(PKG) + 1:]
        if m == f"{PKG}.papersim" or short.startswith("papersim."):
            protected.add((m, "plan 3.1 protected papersim subpackage"))
        elif m == f"{PKG}.project_postgres" or short.startswith("project_postgres."):
            protected.add((m, "plan 3.1 protected project_postgres subpackage"))
        elif short in ("public_http", "research_probability_scores",
                       "supabase_local_dsn", "local_postgres_dsn"):
            protected.add((m, "plan 3.1 named exclusion"))
    for m, why in sorted(protected):
        ret_lines.append(f"{m}\tprotected_reachable\t{why}\t"
                         f"{'yes' if m in u0set else 'no'}\tall")
    (out_dir / "retained-modules.tsv").write_text("\n".join(ret_lines) + "\n",
                                                  encoding="utf-8", newline="\n")

    exp_lines = ["export_name\tprovider_module\toriginal_symbol\tstatus\tbatch\timport_line"]
    for name, prov, sym, status, batch, line in export_rows:
        exp_lines.append(f"{name}\t{prov}\t{sym}\t{status}\t{batch}\t{line}")
    (out_dir / "root-exports.tsv").write_text("\n".join(exp_lines) + "\n",
                                              encoding="utf-8", newline="\n")

    tr_header = ("test_path\tverdict\tbatches_module_scope\tbatches_function_scope\t"
                 "module_scope_d0_deps\tmodule_scope_retained_deps\tnodeid_candidates\t"
                 "unknown_dynamic\tdangling_root_symbols")
    tr_lines = [tr_header] + ["\t".join(str(r[k]) or "-" for k in (
        "test_path", "verdict", "batches_module_scope", "batches_function_scope",
        "module_scope_d0_deps", "module_scope_retained_deps", "nodeid_candidates",
        "unknown_dynamic", "dangling_root_symbols")) for r in test_rows]
    (out_dir / "test-retirement.tsv").write_text("\n".join(tr_lines) + "\n",
                                                 encoding="utf-8", newline="\n")

    baseline_artifacts = {
        "source-inventory.tsv": sha256_file(out_dir / "source-inventory.tsv"),
        "retained-modules.tsv": sha256_file(out_dir / "retained-modules.tsv"),
        "root-exports.tsv": sha256_file(out_dir / "root-exports.tsv"),
        "test-retirement.tsv": sha256_file(out_dir / "test-retirement.tsv"),
    }

    prune = {
        "schema": "m3-b0-prune-manifest/1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": {"path": f"{E_DIR}/inventory.py", "sha256": tool_sha,
                 "mode": "generate", "argv": [str(root)]},
        "baseline": {"commit": baseline_commit, "worktree_clean": not dirty,
                     "plan": "m3-plan-formal.md sections 3.1-3.4",
                     "src_files": len(known),
                     "src_total_lines": sum(f.lines for f in facts.values()),
                     "root_init_lines": facts[PKG].lines},
        "baseline_artifacts_sha256": baseline_artifacts,
        "plan_locks": {k: {"plan": v[0], "actual": v[1], "match": v[0] == v[1]}
                       for k, v in locks.items()},
        "plan_locks_all_match": not lock_fail,
        "lock_failures": lock_fail,
        "historical_tsv": {
            "path": str(HIST_TSV), "bytes": len(tsv_bytes), "sha256_lf": tsv_sha,
            "sha256_crlf": crlf_sha, "plan_bytes": PLAN_LOCKS["hist_tsv_plan_bytes"],
            "plan_sha256": PLAN_LOCKS["hist_tsv_plan_sha256"],
            "module_set_equal_to_recomputed_u0": u0_matches_hist,
            "discrepancy_note": tsv_note},
        "entry_points": {
            "definition": ("console scripts (pyproject.toml) + __main__ + "
                           "__name__=='__main__' guard modules + import targets of "
                           "scripts/*.py and tools/**/*.py (audit_static semantics); "
                           "workflow module refs and resolved dynamic imports are "
                           "additional entry evidence for the enriched pass only"),
            "console_scripts": [{"name": "polymarket-alpha-lab",
                                 "entry": "polymarket_alpha_lab.cli:main"}],
            "console": graphs["console"],
            "main_guard_modules": graphs["main_guard"],
            "script_files": sorted(data["script_facts"]),
            "script_targets": graphs["script_targets"],
            "tool_files": sorted(data["tool_facts"]),
            "tool_targets": graphs["tool_targets"],
            "workflow_module_refs": data["workflow_hits"],
            "server_mirror_table": {
                "status": ("no machine-readable mirror replacement table exists in the "
                           "repository; the DELIVERY_PLAN.md 2026-11-14 mirror/manifest/"
                           "replacement-table item is recorded CLOSED for evidence "
                           "collection; no additional entry derived from it"),
                "entries": []},
        },
        "reachability": {
            "definition": ("audit_static reach: BFS over static+lazy AST imports from "
                           "the entry set, root-__init__ eager fan-out excluded"),
            "reachable_count": len(reachable),
            "reachable_lines": sum(facts[m].lines for m in reachable),
            "reachable_modules": sorted(reachable),
            "u0_count": len(u0),
            "u0_lines": sum(facts[m].lines for m in u0),
            "u0_modules": u0,
            "u0_canonical_sha256": canonical_sha(u0),
            "k0": [{"module": m, "sha256": facts[m].sha256, "lines": facts[m].lines,
                    "path": module_path(m)} for m in k0],
            "k0_canonical_sha256": canonical_sha(k0),
            "d0_count": len(d0),
            "d0_canonical_sha256": canonical_sha(d0),
            "batch_counts": batch_counts,
            "batch_lines": batch_lines,
            "d0": [{"module": m, "path": module_path(m), "sha256": facts[m].sha256,
                    "lines": facts[m].lines, "batch": batch_of(m)} for m in d0],
        },
        "enriched_analysis": {
            "definition": ("adds root-export symbol edges (FX-20 mapping), resolved "
                           "dynamic imports (importlib/__import__/subprocess -m/constant "
                           "strings incl. pkg-prefixed), workflow refs"),
            "reachable_enriched_count": len(reachable_enr),
            "d0_modules_reachable_enriched": sorted(reachable_enr & d0set),
            "retained_consumer_violations": enr_violations,
            "k0_dependency_leaks": k0_leaks,
            "entry_consumers_of_d0": dict(sorted(entry_consumers.items())),
            "unknown_references": unknown_refs,
            "unknown_reference_count": len(unknown_refs),
            "unknown_references_in_retained_surfaces": unknown_retained,
            "unknown_test_references_by_file": {k: len(v) for k, v in
                                                sorted(test_unknown_by_file.items())},
            "cross_family_cycles": cross_cycles,
            "scc_component_count": len(sccs),
        },
        "root_exports": {
            "names": len(data["all_names"]),
            "providers": len({r[1] for r in export_rows}),
            "reachable": n_reach, "retained": n_ret, "to_archive": n_arch,
            "anomalies": data["export_anomalies"],
            "to_archive_by_batch": {b: sum(1 for r in export_rows if r[4] == b)
                                    for b in BATCH_ORDER},
        },
        "tests": {
            "files_scanned": len(data["test_facts"]),
            "rows": len(test_rows),
            "verdict_counts": {v: sum(1 for r in test_rows if r["verdict"] == v)
                               for v in sorted({r["verdict"] for r in test_rows})},
            "retire_full_by_batch": {b: sorted(v) for b, v in
                                     retire_full_by_batch.items()},
            "conflict_or_blocked_by_batch": {b: sorted(v) for b, v in blockers.items()},
            "nodeid_candidate_files_by_batch": {b: sorted(v) for b, v in
                                                nodeid_by_batch.items()},
        },
        "m2_reservations": {"modules": [],
                            "note": "no M2 dependency reservations registered at B0 "
                                    "time; each batch must re-check before execution"},
        "doc_registry_entries": {m: doc_entries[m] for m in sorted(doc_entries)},
        "consumers_src_of_d0": {m: consumers_src[m] for m in sorted(consumers_src)},
        "dangling_root_symbols": {m: f.dangling for m, f in sorted(facts.items())
                                  if f.dangling},
    }
    (out_dir / "prune-manifest.json").write_text(
        json.dumps(prune, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")

    # -- per-batch manifests ----------------------------------------------------
    cycle_by_batch = defaultdict(list)
    for cyc in cross_cycles:
        for b in cyc["batches"]:
            cycle_by_batch[b].append(cyc)
    members_by_batch = {b: [m for m in d0 if batch_of(m) == b] for b in BATCH_ORDER}
    unknown_by_file = defaultdict(list)
    for u in unknown_refs:
        unknown_by_file[u["file"].replace("\\", "/")].append(u)

    for b in BATCH_ORDER:
        members = members_by_batch[b]
        mset = set(members)
        files = []
        for m in members:
            f = facts[m]
            p = module_path(m)
            tc = []
            for key, row in test_analysis.items():
                if m in row["module_scope_d0_deps"].split(";"):
                    tc.append({"test": key, "scope": "module"})
                else:
                    tf = data["test_facts"][key]
                    for qual, deps in tf.fn_scope.items():
                        if m in deps:
                            tc.append({"test": key, "scope": "function", "nodeid": qual})
            files.append({
                "module": m, "path": p, "sha256": f.sha256, "lines": f.lines,
                "root_exports": sorted(n for n, i in data["exports"].items()
                                       if i["provider"] == m),
                "doc_registry_entries": doc_entries.get(m, {}),
                "src_consumers": consumers_src.get(m, []),
                "test_consumers": tc,
                "unknown_consumers": unknown_by_file.get(str(f.path), []),
                "covered_by_retained_closure": m in k0set,
                "covered_by_m2_reservation": m in prune["m2_reservations"]["modules"],
            })
        violations = [v for v in enr_violations if v["d0_module"] in mset]
        leaks = [v for v in k0_leaks if v["d0_dependency"] in mset]
        econs = {k: v for k, v in sorted(entry_consumers.items()) if k in mset}
        unknown_tests = unknown_tests_at(b)
        reasons = []
        if violations:
            reasons.append("retained-src consumers exist "
                           f"({len({v['d0_module'] for v in violations})} modules)")
        if leaks:
            reasons.append(f"K0 dependency leak ({len(leaks)})")
        if econs:
            reasons.append("entry/script/tool/workflow consumers exist")
        if cycle_by_batch[b]:
            reasons.append(f"cross-family cycle members ({len(cycle_by_batch[b])})")
        if blockers[b]:
            reasons.append("shared test files with module-scope imports spanning "
                           f"batches ({len(blockers[b])})")
        if unknown_retained:
            reasons.append(f"UNKNOWN dynamic references on retained surfaces "
                           f"({len(unknown_retained)})")
        if unknown_tests:
            reasons.append(f"UNKNOWN dynamic references in test files remaining at "
                           f"this batch ({len(unknown_tests)} files) -- manual review "
                           f"required (plan 3.3 item 6)")
        manifest = {
            "batch": b,
            "family": next((p for n, p in BATCH_PREFIXES if n == b), "* (remainder)"),
            "baseline_commit": baseline_commit,
            "tool_sha256": tool_sha,
            "entry_set_ref": "prune-manifest.json#entry_points",
            "counts": {"files": len(members),
                       "lines": sum(facts[m].lines for m in members),
                       "root_exports": sum(len(x["root_exports"]) for x in files)},
            "files": files,
            "dependency_check": {
                "retained_src_dependents": sorted({v["d0_module"] for v in violations}),
                "k0_dependency_leaks": leaks,
                "entry_script_tool_workflow_consumers": econs,
                "unknown_consumers_in_retained_surfaces": unknown_retained,
                "unknown_test_references_pending_manual_review": unknown_tests,
                "cross_family_cycles": cycle_by_batch[b],
                "canonical_order": BATCH_ORDER,
                "verdict": "PAUSE" if reasons else "PASS",
                "reasons": reasons,
            },
            "test_retirement": {
                "delete_full": sorted(retire_full_by_batch[b]),
                "shared_conflicts_or_blocked": sorted(blockers[b]),
                "nodeid_candidate_files": sorted(nodeid_by_batch[b]),
                "retained_tests_zero_dependency_obligation": (
                    "after retiring delete_full files and this batch's nodeid "
                    "candidates, remaining tests and shared fixtures must show zero "
                    "references (module-scope, function-scope, strings) to this "
                    "batch's modules and exports; prove on undeleted source per "
                    "plan 3.4 step 2 before physical deletion"),
            },
            "exports_to_retire": sorted(r[0] for r in export_rows if r[4] == b),
            "doc_registry_removals": {m: doc_entries[m] for m in sorted(doc_entries)
                                      if batch_of(m) == b},
            "m2_reservations_check": prune["m2_reservations"]["modules"],
            "executed": False,
            "executed_commit": None,
            "verification_requirements": [
                "step 1: mark deprecated-pending-prune in the engineering inventory "
                "and current module index; no runtime warnings, no shell modules, "
                "no selfcheck output change",
                "step 2: run the full verifier on undeleted source, then prove "
                "remaining-test zero dependency for this batch",
                "step 3: physical deletion of exactly these manifest paths in one "
                "atomic commit (source + exclusive tests + root exports + doc "
                "registry entries); protection tag pre-prune-2026-11 pushed and read "
                "back first (coordinator action)",
                "step 4: re-run inventory.py --verify on the batch parent commit; "
                "record R_before==R_after, unreachable totals, file/line/export "
                "counts, retired nodeids, remaining test results",
            ],
        }
        (out_dir / f"{b}-manifest.json").write_text(
            json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")

    # -- summary ---------------------------------------------------------------
    print(f"src files: {len(known)}  reachable: {len(reachable)}  "
          f"U0: {len(u0)}  K0: {len(k0)}  D0: {len(d0)}")
    print(f"batches: {batch_counts}")
    print(f"batch lines: {batch_lines}")
    print(f"locks all match: {not lock_fail}"
          + ("" if not lock_fail else f"  FAILURES: {json.dumps(lock_fail)}"))
    ea = prune["enriched_analysis"]
    print(f"enriched: reachable_enr={ea['reachable_enriched_count']} "
          f"violations={len(ea['retained_consumer_violations'])} "
          f"k0_leaks={len(ea['k0_dependency_leaks'])} "
          f"unknown={ea['unknown_reference_count']} "
          f"(retained-surface {len(ea['unknown_references_in_retained_surfaces'])}) "
          f"cross_cycles={len(ea['cross_family_cycles'])} sccs={ea['scc_component_count']}")
    print(f"exports: {prune['root_exports']['names']} names / "
          f"{prune['root_exports']['providers']} providers "
          f"(reachable {n_reach} / retained {n_ret} / to_archive {n_arch})  "
          f"anomalies: {len(prune['root_exports']['anomalies'])}")
    print(f"tests scanned: {prune['tests']['files_scanned']}  "
          f"rows: {prune['tests']['rows']}  verdicts: {prune['tests']['verdict_counts']}")
    for b in BATCH_ORDER:
        bm = json.loads((out_dir / f"{b}-manifest.json").read_text(encoding="utf-8"))
        print(f"{b}: files={bm['counts']['files']} lines={bm['counts']['lines']} "
              f"exports={bm['counts']['root_exports']} "
              f"verdict={bm['dependency_check']['verdict']} "
              f"reasons={bm['dependency_check']['reasons']}")
    print(f"doc registry modules: {len(prune['doc_registry_entries'])}")
    print(f"artifacts written to {out_dir} in {time.time() - t0:.1f}s")

    if lock_fail or not u0_matches_hist or k0_bad or k0_not_u0:
        if k0_bad:
            print("K0 modules missing from src:", k0_bad)
        if k0_not_u0:
            print("K0 modules not in U0:", k0_not_u0)
        if not u0_matches_hist:
            only_rec = sorted(set(u0) - set(hist_u0))[:10]
            only_hist = sorted(set(hist_u0) - set(u0))[:10]
            print("U0 mismatch vs historical TSV:",
                  json.dumps({"recomputed_only": only_rec, "tsv_only": only_hist}))
        return 2
    return 0


# ----------------------------------------------------------------------------
# verify mode (re-proof after each batch)
# ----------------------------------------------------------------------------
def verify(root: Path) -> int:
    out_dir = root / E_DIR
    prune = json.loads((out_dir / "prune-manifest.json").read_text(encoding="utf-8"))
    r = prune["reachability"]
    baseline_all = set(r["reachable_modules"]) | set(r["u0_modules"]) | {PKG}
    d0 = {e["module"]: e for e in r["d0"]}
    k0_sha = {e["module"]: e["sha256"] for e in r["k0"]}

    parse_set = set(r["reachable_modules"]) | set(k0_sha) | {PKG}
    data = collect_light(root, parse_set)
    graphs = build_graphs(data)
    facts, known = data["facts"], data["known"]
    current = set(known)
    errors = []

    # sha256 of every present baseline src file, parsed or filename-only
    def sha_of(module):
        p = root / module_path(module)
        return sha256_file(p) if p.is_file() else None

    missing = sorted(baseline_all - current)
    illegal = [m for m in missing if m not in d0]
    if illegal:
        errors.append({"check": "v1_no_illegal_deletion",
                       "detail": f"non-D0 modules missing: {illegal[:20]}"})

    claimed = defaultdict(list)
    for b in BATCH_ORDER:
        bm = json.loads((out_dir / f"{b}-manifest.json").read_text(encoding="utf-8"))
        for entry in bm["files"]:
            claimed[entry["module"]].append(b)
    unclaimed = [m for m in missing if m not in claimed]
    if unclaimed:
        errors.append({"check": "v2_missing_claimed_by_batch", "detail": unclaimed[:20]})
    double = {m: bs for m, bs in claimed.items() if len(bs) > 1}
    if double:
        errors.append({"check": "v2_batch_double_claim", "detail": double})

    executed, partial = set(), {}
    for b in BATCH_ORDER:
        bm = json.loads((out_dir / f"{b}-manifest.json").read_text(encoding="utf-8"))
        members = {e["module"] for e in bm["files"]}
        gone = members - current
        if gone:
            executed.add(b)
            if gone != members:
                partial[b] = sorted(members - gone)
    if partial:
        errors.append({"check": "v2_batch_atomicity",
                       "detail": f"partially deleted batches: {partial}"})

    reachable_now = graphs["reachable"]
    if reachable_now != set(r["reachable_modules"]):
        errors.append({"check": "v3_reachable_invariant",
                       "detail": {
                           "baseline_only": sorted(set(r["reachable_modules"]) - reachable_now)[:20],
                           "current_only": sorted(reachable_now - set(r["reachable_modules"]))[:20]}})

    expected_unreachable = (set(d0) | set(k0_sha)) & current
    actual_unreachable = (current - {PKG}) - reachable_now
    if actual_unreachable != expected_unreachable:
        errors.append({"check": "v4_unreachable_membership",
                       "detail": {"unexpected_new_unreachable":
                                  sorted(actual_unreachable - expected_unreachable)[:20],
                                  "no_longer_unreachable":
                                  sorted(expected_unreachable - actual_unreachable)[:20]}})

    modified = [m for m, e in d0.items() if m in current and sha_of(m) != e["sha256"]]
    if modified:
        errors.append({"check": "v5_d0_files_unmodified", "detail": modified[:20]})
    k0_modified = [m for m, s in k0_sha.items()
                   if m in current and sha_of(m) != s]
    if k0_modified:
        errors.append({"check": "v5b_k0_files_unmodified", "detail": k0_modified[:20]})

    for name, sha in prune["baseline_artifacts_sha256"].items():
        p = out_dir / name
        if not p.is_file() or sha256_file(p) != sha:
            errors.append({"check": "v6_baseline_artifact_integrity", "detail": name})

    result = {"mode": "verify", "baseline_commit": prune["baseline"]["commit"],
              "current_commit": _git(root, "rev-parse", "HEAD"),
              "executed_batches": sorted(executed),
              "present_src_files": len(current), "missing_d0": len(missing),
              "checks_passed": not errors, "errors": errors}
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if not errors else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root")
    parser.add_argument("--out", default=None)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    if args.verify:
        return verify(root)
    out = Path(args.out) if args.out else root / E_DIR
    return generate(root, out)


if __name__ == "__main__":
    raise SystemExit(main())
