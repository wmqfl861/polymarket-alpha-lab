#!/usr/bin/env python3
"""List the module-level constants and dataclass defaults that act as model
parameters in the paper-simulation model modules.

Reviewer's own read-only script (2026-11-19 audit). It parses source text with
`ast` (read through `git show`); it never imports or runs repository code.

Usage: python3 -I model_parameter_inventory.py <repo_dir> <commit>
"""
from __future__ import annotations

import ast
import subprocess
import sys

BASE = "src/polymarket_alpha_lab/papersim/"
MODULES = [
    "crypto_model.py", "soccer_model.py", "xg.py", "league_strength.py", "recalib.py",
    "intl_model.py", "intl_totals.py", "nba_model.py", "gates.py", "risk_policy.py", "pipeline.py", "evaluation.py",
]
SKIP_PREFIX = ("REASON_", "STEP_", "STATUS_", "VERDICT_", "POLICY_", "NEWS_", "SIDE_", "TIER_", "DIRECTION_",
               "KIND_", "FILL_", "PNL_", "RATING_SOURCE", "TOTAL_SOURCE", "TOURNAMENT_", "_")
CONFIG_CLASSES = {"Config", "VariantSpec", "IntlVariantSpec", "NbaVariantSpec"}


def show(repo: str, commit: str, path: str) -> str:
    return subprocess.run(["git", "-C", repo, "show", f"{commit}:{path}"], capture_output=True, text=True, check=True).stdout


def literal(node: ast.AST, width: int = 120) -> str:
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= width else text[: width - 3] + "..."


def is_parameter_value(node: ast.AST) -> bool:
    """Numbers, tuples/dicts of numbers, MappingProxyType(...) wrappers, small constructor tuples."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool) or isinstance(node.value, str)
    if isinstance(node, ast.UnaryOp):
        return is_parameter_value(node.operand)
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(is_parameter_value(e) or isinstance(e, ast.Call) for e in node.elts)
    if isinstance(node, ast.Dict):
        return True
    if isinstance(node, ast.Call):
        return True
    if isinstance(node, ast.BinOp):
        return True
    return False


def main() -> None:
    repo, commit = sys.argv[1:3]
    print(f"commit={commit}")
    for name in MODULES:
        path = BASE + name
        tree = ast.parse(show(repo, commit, path))
        rows = []
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.isupper() and not target.id.startswith(SKIP_PREFIX) \
                            and value is not None and is_parameter_value(value) and target.id != "__all__":
                        if isinstance(value, ast.Constant) and isinstance(value.value, str) and "VERSION" not in target.id \
                                and "WINDOW" not in target.id:
                            continue
                        rows.append(f"  {path}:{node.lineno}: {target.id} = {literal(value)}")
            elif isinstance(node, ast.ClassDef) and node.name in CONFIG_CLASSES:
                for sub in node.body:
                    if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name) and sub.value is not None:
                        rows.append(f"  {path}:{sub.lineno}: {node.name}.{sub.target.id} = {literal(sub.value)}")
        print(f"{name}: {len(rows)} entries")
        print("\n".join(rows))


if __name__ == "__main__":
    main()
