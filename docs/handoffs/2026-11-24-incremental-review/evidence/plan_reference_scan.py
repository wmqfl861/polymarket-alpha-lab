#!/usr/bin/env python3
"""List references to node-level plans, plan reviews and rulings in source files added or
changed between two commits, and report which documents under docs/paper-sim/design exist.

usage: python3 -I plan_reference_scan.py <repo_root> <base_commit> <head_commit>

Reviewer's own read-only script (2026-11-21 review). It reads blobs with `git cat-file`;
it never imports or runs repository code. A line counts when it cites a node plan by
version or section ("plan v2", "plan r2 section 1", "plan section 3", "plan MO-7 section
1.2"), a review item ("review P2-1", "plan review R2") or a numbered ruling ("裁定①").
Citations of the two in-repo handoff documents ("plan 05", "design 04") and of
DELIVERY_PLAN.md are NOT counted: those documents are in the repository.
"""
from __future__ import annotations

import re
import subprocess
import sys

NODE_REF = re.compile(
    r"(?i)(\bplan v\d|\bplan r\d|\bplan section \d|\bplan MO-\d+ (?:section|rule)|\bplan review\b|"
    r"\breview(?: decision)? [PR]\d|\bcode-review P\d|\bplan D\d|裁定[①②③④⑤])"
)
IN_REPO = re.compile(r"(?i)plan 05 section|05 section|design 04")


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout.decode("utf-8", "replace")


def main() -> int:
    repo, base, head = sys.argv[1:4]
    status = {}
    fields = git(repo, "diff", "--name-status", "--no-renames", "-z", base, head).split("\0")
    for letter, name in zip(fields[0::2], fields[1::2]):
        if name.startswith("src/") and name.endswith(".py"):
            status[name] = letter
    print(f"base={base} head={head}")
    for letter, label in (("A", "files ADDED since base (new modules)"), ("M", "files MODIFIED since base")):
        names = sorted(n for n, s in status.items() if s == letter)
        total = 0
        rows = []
        for name in names:
            text = git(repo, "cat-file", "blob", f"{head}:{name}")
            hits = [i for i, line in enumerate(text.split("\n"), 1)
                    if NODE_REF.search(IN_REPO.sub("", line))]
            if hits:
                total += len(hits)
                rows.append((name, hits))
        print(f"{label}: {len(names)}; citing a node plan, review item or ruling: {len(rows)} files, {total} lines")
        for name, hits in rows:
            shown = ", ".join(str(h) for h in hits[:12]) + (" ..." if len(hits) > 12 else "")
            print(f"  {len(hits):3d}  {name}: {shown}")
    design = [n for n in git(repo, "ls-tree", "--name-only", f"{head}:docs/paper-sim/design").split("\n") if n]
    print(f"entries under docs/paper-sim/design at head: {design}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
