#!/usr/bin/env python3
"""Check that the registered optimizer scope, rule v1 numbers and blueprint v4 are verbatim copies.

usage: python3 -I check_registration.py <repo_root> <commit>

Reviewer's own read-only script (2026-11-21 review). It reads blobs with `git cat-file`
and compares line sets; it never imports or runs repository code.
"""
from __future__ import annotations

import subprocess
import sys

PKG = "docs/handoffs/2026-11-19-audit-optimizer"


def blob(repo: str, rev: str, path: str) -> str:
    return subprocess.run(["git", "-C", repo, "cat-file", "blob", f"{rev}:{path}"],
                          capture_output=True, check=True).stdout.decode("utf-8")


def main() -> int:
    repo, rev = sys.argv[1], sys.argv[2]
    design = blob(repo, rev, f"{PKG}/04-optimizer-design.md").split("\n")
    plan = blob(repo, rev, "DELIVERY_PLAN.md")
    plan_lines = set(plan.split("\n"))
    print(f"commit: {rev}")
    for label, lo, hi in (("04 section 2, the scope table and its two paragraphs (lines 20-29)", 20, 29),
                          ("04 section 6, both tables and the envelope paragraph (lines 75-95)", 75, 95),
                          ("04 section 6, the closing two bullets (lines 97-100)", 97, 100)):
        lines = [line for line in design[lo - 1:hi] if line.strip()]
        missing = [line for line in lines if line not in plan_lines]
        print(f"{label}: {len(lines)} non-empty lines; verbatim in DELIVERY_PLAN.md: {len(lines) - len(missing)}")
    owner = "完整审核项目，然后我需要增加模型自动优化的功能，请写好开发计划给我，连同审核文档，更新的蓝图。一起推送仓库。注意修改和开发计划需要详细。然后给我提示词给到本地agent"
    print(f"owner's 2026-11-19 sentence present verbatim in DELIVERY_PLAN.md: {owner in plan}")
    blueprint = blob(repo, rev, "docs/paper-sim/blueprint.md").split("\n")
    v4 = blob(repo, rev, f"{PKG}/02-blueprint-v4.md").split("\n")
    extra = [line for line in blueprint if line.strip() and line not in set(v4)]
    lost = [line for line in v4 if line.strip() and line not in set(blueprint)]
    print(f"docs/paper-sim/blueprint.md: {len(blueprint)} lines; 02-blueprint-v4.md: {len(v4)} lines")
    print(f"  lines of the 02 file missing from blueprint.md: {len(lost)}")
    print(f"  lines of blueprint.md not in the 02 file: {len(extra)}")
    for line in extra:
        print(f"    + {line[:110]}")
    total = len(plan.split("\n"))
    last_heading = [line for line in plan.split("\n") if line.startswith("## ") or line.startswith("### ")][-1]
    print(f"DELIVERY_PLAN.md: {total} lines; last heading: {last_heading[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
