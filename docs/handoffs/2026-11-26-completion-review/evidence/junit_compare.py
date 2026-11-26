#!/usr/bin/env python3
"""Compare two pytest JUnit files: totals, and tests whose outcome is new or changed.

Usage: python3 -I junit_compare.py <base.xml> <head.xml>
Prints totals per file, per-test-file differences in counts, the skip reasons of tests that
are skipped at head and were not skipped (or did not exist) at base, and all skips at head
grouped by what their reason asks for.
"""
import collections
import sys
import xml.etree.ElementTree as ET


def load(path):
    outcomes = {}
    reasons = {}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    for suite in suites:
        for case in suite.iter("testcase"):
            parts = case.get("classname", "").split(".")
            module = "/".join(parts[:2]) + ".py" if len(parts) >= 2 else case.get("classname", "")
            key = (module, ".".join(parts[2:]), case.get("name"))
            outcome = "passed"
            for kind in ("failure", "error", "skipped"):
                node = case.find(kind)
                if node is not None:
                    outcome = "failed" if kind in ("failure", "error") else "skipped"
                    if kind == "skipped":
                        reasons[key] = (node.get("message") or "").strip().splitlines()[0][:110] if node.get("message") else ""
                    break
            outcomes[key] = outcome
    return outcomes, reasons


def main():
    base, base_reasons = load(sys.argv[1])
    head, head_reasons = load(sys.argv[2])
    for label, data in (("base", base), ("head", head)):
        counts = collections.Counter(data.values())
        print(f"{label}: {len(data)} cases; passed {counts['passed']}, skipped {counts['skipped']}, failed {counts['failed']}")
    added = [k for k in head if k not in base]
    removed = [k for k in base if k not in head]
    changed = [k for k in head if k in base and head[k] != base[k]]
    print(f"cases only at head: {len(added)} ({collections.Counter(head[k] for k in added).most_common()})")
    print(f"cases only at base: {len(removed)} ({collections.Counter(base[k] for k in removed).most_common()})")
    print(f"cases in both whose outcome changed: {len(changed)}")
    for key in changed:
        print(f"    {key[0]}::{key[2][:80]}: {base[key]} -> {head[key]}")
    per_file = collections.defaultdict(collections.Counter)
    for key in added:
        per_file[key[0]]["+" + head[key]] += 1
    for key in removed:
        per_file[key[0]]["-" + base[key]] += 1
    print("per test file (cases only at head as +outcome, only at base as -outcome):")
    for module in sorted(per_file):
        print("    " + module + ": " + ", ".join(f"{k} {v}" for k, v in sorted(per_file[module].items())))
    new_skips = collections.Counter()
    for key in head:
        if head[key] == "skipped" and base.get(key) != "skipped":
            new_skips[(key[0], head_reasons.get(key, ""))] += 1
    print("skipped at head and not skipped at base (file | reason | cases):")
    for (module, reason), count in sorted(new_skips.items()):
        print(f"    {module} | {reason} | {count}")
    groups = collections.Counter()
    for key, outcome in head.items():
        if outcome != "skipped":
            continue
        text = head_reasons.get(key, "").lower()
        if any(word in text for word in ("pwsh", "powershell", "windows", "win32")):
            groups["needs Windows or PowerShell"] += 1
        elif any(word in text for word in ("native", "postgres", "disposable", "supabase", "dsn", "_pg=")):
            groups["needs a native PostgreSQL or another opt-in native proof"] += 1
        else:
            groups["other (server-only fixtures, opt-in host proofs)"] += 1
    print("all skips at head, grouped by what the reason asks for:")
    for name, count in groups.most_common():
        print(f"    {count:4d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
