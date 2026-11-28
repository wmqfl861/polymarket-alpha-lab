#!/usr/bin/env python3
"""For every branch that refs_delta.txt lists as removed, say whether its commits are still
reachable from the refs that remain (branches, pull-request heads, tags).

Usage: python3 -I removed_branch_reachability.py <repo_dir> <refs_delta.txt> <main_commit>

<repo_dir> must still hold the removed commits (a clone fetched before the removal does) and must
have fetched the remaining refs as origin/* remote-tracking refs, pull-request heads as origin/pr/*.
Read-only: runs git rev-list, rev-parse and log only.
"""
import re
import subprocess
import sys


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def main() -> None:
    repo, listing, main_commit = sys.argv[1:4]
    removed, inside = [], False
    for line in open(listing, encoding="utf-8"):
        if line.startswith("removed:"):
            inside = True
            continue
        if inside:
            found = re.fullmatch(r"\s+(\S+): was ([0-9a-f]{7,40})\s*", line)
            if not found:
                inside = False
                continue
            removed.append((found[1], found[2]))
    main_trees = set(git(repo, "log", "--first-parent", "--format=%T", main_commit).split())
    fully, leftovers = 0, []
    for name, tip in removed:
        only_here = int(git(repo, "rev-list", "--count", tip, "--not", "--remotes=origin", "--tags").strip())
        if only_here == 0:
            fully += 1
            continue
        parents = len(git(repo, "rev-list", "--parents", "-n1", tip).split()) - 1
        tree = git(repo, "rev-parse", tip + "^{tree}").strip()
        leftovers.append((name, only_here, parents, tree in main_trees))
    print(f"removed branches listed: {len(removed)}")
    print(f"every commit still reachable from a remaining branch, pull-request head or tag: {fully}")
    print(f"with commits reachable from no remaining ref: {len(leftovers)}")
    for name, count, parents, same_tree in leftovers:
        print(f"  {name}: {count} such commit(s); tip has {parents} parents; "
              f"tip tree equals a first-parent commit of main: {'yes' if same_tree else 'no'}")


if __name__ == "__main__":
    main()
