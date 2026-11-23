#!/usr/bin/env python3
"""Count public IPv4-like strings in two commits without printing any of them.

Reviewer's own code for the 2026-11-13 M0 review. It runs `git grep` on the
two commits (read-only) and reports, for each distinct public address-like
value, how often it occurs at each commit and which files newly contain it.
Values are never printed: each one is shown only as "address #N", numbered by
how often it occurs at the base commit.

Usage: python3 -I address_occurrence_diff.py <repo_dir> <base_commit> <head_commit>
"""
from __future__ import annotations

import collections
import ipaddress
import re
import subprocess
import sys

PATTERN = r"[0-9]{1,3}\\?\.[0-9]{1,3}\\?\.[0-9]{1,3}\\?\.[0-9]{1,3}"
FIND = re.compile(r"(?<![\d.])(\d{1,3})\\?\.(\d{1,3})\\?\.(\d{1,3})\\?\.(\d{1,3})(?![\d.])")
DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]


def occurrences(repo: str, rev: str) -> dict[str, list[tuple[str, str, bool]]]:
    # -a: treat every file as text. Without it, paths marked `-diff` in .gitattributes (the server
    # mirror at the reviewed commit) are reported only as "Binary file ... matches" and would be missed.
    out = subprocess.run(["git", "-C", repo, "grep", "-a", "-nE", PATTERN, rev, "--", "."],
                         capture_output=True, text=True, check=False).stdout
    found: dict[str, list[tuple[str, str, bool]]] = collections.defaultdict(list)
    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        path, number, text = parts[1], parts[2], parts[3]
        for match in FIND.finditer(text):
            octets = [int(x) for x in match.groups()]
            if any(x > 255 for x in octets):
                continue
            address = ipaddress.ip_address(".".join(map(str, octets)))
            if not address.is_global or any(address in net for net in DOC_NETS):
                continue
            found[str(address)].append((path, number, "\\" in match.group(0)))
    return found


def main() -> None:
    repo, base_rev, head_rev = sys.argv[1:4]
    base, head = occurrences(repo, base_rev), occurrences(repo, head_rev)
    ordered = sorted(set(base) | set(head), key=lambda a: (-len(base.get(a, [])), a))
    print(f"base={base_rev} head={head_rev} distinct public IPv4-like values: {len(ordered)}")
    for index, address in enumerate(ordered, 1):
        b, h = base.get(address, []), head.get(address, [])
        if len(b) < 5 and len(b) == len(h):
            continue  # rare, unchanged values (version strings, third-party fixtures) are not listed
        base_files = {p for p, _, _ in b}
        new_files = sorted({p for p, _, _ in h} - base_files)
        print(f"address #{index}: base occurrences={len(b)} in {len(base_files)} files; "
              f"head occurrences={len(h)} in {len({p for p, _, _ in h})} files; "
              f"files newly containing it={len(new_files)}")
        for path, number, escaped in h:
            if path in new_files:
                print(f"    new: {path}:{number} ({'regex-escaped' if escaped else 'plain'} form, value masked)")


if __name__ == "__main__":
    main()
