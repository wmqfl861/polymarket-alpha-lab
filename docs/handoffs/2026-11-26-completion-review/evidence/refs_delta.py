#!/usr/bin/env python3
"""Compare two listings of remote-tracking refs ("<refname> <commit>" per line).

Usage: python3 -I refs_delta.py <repo> <before.txt> <after.txt> <main_commit>

The listings come from `git for-each-ref --format='%(refname) %(objectname)' refs/remotes/origin`
taken before and after a full fetch (branches and pull-request heads). Pull-request refs are
normalised to their number. Read-only: the script only asks git whether a tip is an ancestor of
<main_commit>.
"""
import re
import subprocess
import sys


def load(path):
    branches, pulls = {}, {}
    for line in open(path, encoding="utf-8"):
        parts = line.split()
        if len(parts) < 2:
            continue
        name, commit = parts[0], parts[1]
        name = name.removeprefix("refs/remotes/origin/")
        if name == "HEAD":
            continue
        match = re.fullmatch(r"pr/([0-9]+)(?:/(head|merge))?", name)
        if match:
            if match.group(2) != "merge":
                pulls[int(match.group(1))] = commit
        else:
            branches[name] = commit
    return branches, pulls


def merged(repo, tip, main):
    return subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", tip, main]).returncode == 0


def main():
    repo, before_path, after_path, main_commit = sys.argv[1:5]
    before, pulls_before = load(before_path)
    after, pulls_after = load(after_path)
    new = sorted(set(after) - set(before))
    gone = sorted(set(before) - set(after))
    moved = sorted(name for name in after if name in before and after[name] != before[name])
    print(f"branches: {len(before)} -> {len(after)}; pull-request heads: {len(pulls_before)} -> {len(pulls_after)}")
    print(f"new branches: {len(new)}; removed branches: {len(gone)}; branches whose tip moved: {len(moved)}")
    print("moved:")
    for name in moved:
        print(f"    {name}: {before[name][:10]} -> {after[name][:10]}")
    print("removed:")
    for name in gone:
        print(f"    {name}: was {before[name][:10]}")
    print("new (tip, merged into the reviewed main?):")
    for name in new:
        print(f"    {name}: {after[name][:10]} {'merged' if merged(repo, after[name], main_commit) else 'NOT merged'}")
    print("new pull-request heads:")
    for number in sorted(set(pulls_after) - set(pulls_before)):
        print(f"    #{number}: {pulls_after[number][:10]} {'merged' if merged(repo, pulls_after[number], main_commit) else 'NOT merged'}")
    print("pull-request heads that changed: " + (", ".join(f"#{n}" for n in sorted(pulls_after) if n in pulls_before and pulls_after[n] != pulls_before[n]) or "none"))
    print("handoff/* branches (unchanged unless marked):")
    for name in sorted(after):
        if name.startswith("handoff/"):
            state = "new" if name not in before else ("MOVED" if before[name] != after[name] else "unchanged")
            print(f"    {name}: {after[name][:10]} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
