#!/usr/bin/env python3
"""Summarise a pytest junit XML: failures per test file and per first message line.

Usage: python3 -I junit_summary.py <junit.xml> [--messages N]
"""
import collections
import sys
import xml.etree.ElementTree as ET


def main() -> int:
    path = sys.argv[1]
    limit = 40
    if "--messages" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--messages") + 1])
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    per_file = collections.Counter()
    per_message = collections.Counter()
    example = {}
    totals = collections.Counter()
    for suite in suites:
        for case in suite.iter("testcase"):
            totals["cases"] += 1
            classname = case.get("classname", "")
            parts = classname.split(".")
            # tests.test_x[.Class] -> tests/test_x.py
            module = "/".join(parts[:2]) + ".py" if len(parts) >= 2 else classname
            for kind in ("failure", "error"):
                for node in case.findall(kind):
                    totals[kind] += 1
                    per_file[module] += 1
                    message = (node.get("message") or "").strip().splitlines()
                    first = message[0] if message else "(no message)"
                    first = first[:160]
                    per_message[first] += 1
                    example.setdefault(first, f"{module}::{case.get('name')}")
            if case.find("skipped") is not None:
                totals["skipped"] += 1
    print("totals:", dict(totals))
    print("files with failures:", len(per_file))
    for module, count in sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{count:5d}  {module}")
    print()
    print(f"first message lines (top {limit}):")
    for message, count in per_message.most_common(limit):
        print(f"{count:5d}  {message}")
        print(f"         e.g. {example[message][:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
