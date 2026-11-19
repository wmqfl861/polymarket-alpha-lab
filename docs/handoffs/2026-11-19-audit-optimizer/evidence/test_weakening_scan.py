#!/usr/bin/env python3
"""Compare test files between two commits and flag possible weakening.

Reviewer's own read-only script (2026-11-19 audit). It reads blobs with
`git show` and parses them with `ast`; it never imports or runs repository code.

For every tests/**/*.py file that exists at both commits and differs, it reports:
  - test functions removed or renamed away
  - test functions whose number of `assert` statements dropped
  - skip/xfail markers and pytest.skip()/importorskip calls that are new
It also lists test files deleted between the two commits.

Usage: python3 -I test_weakening_scan.py <repo_dir> <base_commit> <head_commit>
"""
from __future__ import annotations

import ast
import subprocess
import sys


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def blob(repo: str, rev: str, path: str) -> str | None:
    result = subprocess.run(["git", "-C", repo, "show", f"{rev}:{path}"], capture_output=True)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def profile(source: str) -> tuple[dict[str, int], int, int]:
    """Return ({test qualname: assert count}, skip-ish marker count, xfail marker count)."""
    tree = ast.parse(source)
    tests: dict[str, int] = {}
    skips = xfails = 0

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                count = sum(isinstance(sub, ast.Assert) for sub in ast.walk(node))
                # pytest.raises / pytest.fail blocks count as checks as well
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        text = ast.unparse(sub.func)
                        if text.endswith("pytest.raises") or text.endswith("pytest.fail") or text.endswith(".raises"):
                            count += 1
                tests[prefix + node.name] = count
            elif isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")

    visit(tree.body, "")
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Call):
            text = ast.unparse(node.func)
        elif isinstance(node, ast.Attribute):
            text = ast.unparse(node)
        if not text:
            continue
        if text.endswith("mark.skip") or text.endswith("mark.skipif") or text == "pytest.skip" or text.endswith("importorskip"):
            skips += 1
        elif text.endswith("mark.xfail") or text == "pytest.xfail":
            xfails += 1
    return tests, skips, xfails


def main() -> None:
    repo, base, head = sys.argv[1:4]
    status = [line.split("\t") for line in git(repo, "diff", "--name-status", "-M", base, head, "--", "tests").splitlines()]
    deleted = [parts[1] for parts in status if parts[0] == "D" and parts[1].endswith(".py")]
    modified = [parts[-1] for parts in status if parts[0][0] in "MR" and parts[-1].endswith(".py")]
    renamed = {parts[2]: parts[1] for parts in status if parts[0][0] == "R"}
    added = [parts[1] for parts in status if parts[0] == "A" and parts[1].endswith(".py")]
    print(f"base={base} head={head}")
    print(f"test python files: added={len(added)} modified={len(modified)} deleted={len(deleted)}")
    for path in deleted:
        print(f"DELETED FILE {path}")
    total_removed = total_dropped = total_new_skips = total_new_xfails = 0
    base_tests_total = head_tests_total = 0
    for path in sorted(modified):
        old_source = blob(repo, base, renamed.get(path, path))
        new_source = blob(repo, head, path)
        if old_source is None or new_source is None:
            continue
        try:
            old_tests, old_skips, old_xfails = profile(old_source)
            new_tests, new_skips, new_xfails = profile(new_source)
        except SyntaxError as exc:
            print(f"PARSE ERROR {path}: {exc}")
            continue
        base_tests_total += len(old_tests)
        head_tests_total += len(new_tests)
        removed = sorted(set(old_tests) - set(new_tests))
        dropped = sorted(name for name in old_tests if name in new_tests and new_tests[name] < old_tests[name])
        lines = []
        for name in removed:
            lines.append(f"    removed test: {name} (had {old_tests[name]} checks)")
        for name in dropped:
            lines.append(f"    fewer checks: {name} {old_tests[name]} -> {new_tests[name]}")
        if new_skips > old_skips:
            lines.append(f"    skip markers/calls: {old_skips} -> {new_skips}")
        if new_xfails > old_xfails:
            lines.append(f"    xfail markers/calls: {old_xfails} -> {new_xfails}")
        total_removed += len(removed)
        total_dropped += len(dropped)
        total_new_skips += max(0, new_skips - old_skips)
        total_new_xfails += max(0, new_xfails - old_xfails)
        if lines:
            print(f"{path}: tests {len(old_tests)} -> {len(new_tests)}")
            print("\n".join(lines))
    print(f"modified files: test functions {base_tests_total} -> {head_tests_total}")
    print(f"totals: removed tests={total_removed} tests with fewer checks={total_dropped} "
          f"new skip markers={total_new_skips} new xfail markers={total_new_xfails}")
    added_tests = 0
    for path in added:
        source = blob(repo, head, path)
        if source:
            try:
                added_tests += len(profile(source)[0])
            except SyntaxError:
                print(f"PARSE ERROR {path}")
    print(f"added files: {len(added)} with {added_tests} test functions")


if __name__ == "__main__":
    main()
