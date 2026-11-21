#!/usr/bin/env python3
"""Read-only check of the registered facts in the local agent's 2026-11-21 progress package.

usage: python3 -I check_package_claims.py <repo_root> [<package_commit>]

It reads git objects only (no working-tree files, no network, no writes) and prints:
  1. the package files at <package_commit> against the package's own MANIFEST.tsv;
  2. evidence.md section 1 (44 first-parent merges) against `git log --first-parent`;
  3. file-level lists of the three open pull requests named in the package
     (their head commits must already be fetched into the clone);
  4. SHA-256 of the two pinned Europe research files (evidence.md section 6);
  5. a scan of the package text for public-looking IPv4 addresses, absolute
     server paths, connection strings and token-like strings.
Addresses are never printed: only a count and a SHA-256 prefix.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import subprocess
import sys

B = "c4367b6b3ce4d957ce79ce645a7ab3425be5a133"
M = "2bdc4cccacc925ea8e6569c8865b90c0b0c671e6"
DEFAULT_R = "09cf06f8ff57382b121470ad67c18994204db02b"
PKG = "docs/handoffs/2026-11-21-optimizer-review"
PR114_HEAD = "26dfa2a05357121665009981cd600e23c91e093a"
PR114_OLD_BASE = "11fb457c6e714844ec2ee1c0eb3c90dbd145eaa7"
PR116_HEAD = "8c7b536514eda6dfc772bf7fc6a4fc597892a294"
PR116_BASE = "11fb457c6e714844ec2ee1c0eb3c90dbd145eaa7"
PR118_HEAD = "f09850ba8ef010895d6a959cbeff401fbc706363"
EUROPE = (
    ("docs/paper-sim/research/league-admission-20261119/europe-basketball/verification.json",
     "d0ffe021b75ffdea95014bc77c271e0625609f98b50ade26180d86500876f635"),
    ("docs/paper-sim/research/league-admission-20261119/europe-football/summary.json",
     "cbed42762971f55bcdbd4a44a4c022808b83540006632c59a726247423573388"),
)


def git(repo: str, *args: str, text: bool = True):
    out = subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout
    return out.decode("utf-8") if text else out


def blob(repo: str, rev: str, path: str) -> bytes:
    return git(repo, "cat-file", "blob", f"{rev}:{path}", text=False)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def section(title: str) -> None:
    print()
    print(f"== {title}")


def main() -> int:
    repo = sys.argv[1]
    r = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_R
    problems = 0

    section("1. package files at the package commit")
    print(f"package commit: {r}")
    parents = git(repo, "show", "-s", "--format=%P", r).split()
    print(f"parents: {' '.join(parents)}  (first parent is M: {parents[:1] == [M]})")
    names = sorted(git(repo, "ls-tree", "-r", "--name-only", f"{r}:{PKG}").split("\n")[:-1])
    print(f"files in {PKG}: {names}")
    outside = [p for p in git(repo, "diff", "--name-only", M, r).split("\n") if p and not p.startswith(PKG + "/")]
    print(f"files changed outside the package directory (vs M): {len(outside)}")
    manifest = blob(repo, r, f"{PKG}/MANIFEST.tsv")
    print(f"MANIFEST.tsv: {len(manifest)} bytes, {manifest.count(b'\n')} lines, sha256 {sha(manifest)}")
    for line in manifest.decode("utf-8").splitlines():
        path, size, digest = line.split("\t")
        data = blob(repo, r, f"{PKG}/{path}")
        ok = len(data) == int(size) and sha(data) == digest
        problems += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'DIFF'} {path}: {len(data)} bytes, sha256 {sha(data)}")

    section("2. evidence.md section 1 against the first-parent chain B..M")
    evidence = blob(repo, r, f"{PKG}/evidence.md").decode("utf-8")
    rows = re.findall(r"^\| \[#(\d+)\]\([^)]*\) \| ([^|]+) \| `([0-9a-f]{40})` \| `([0-9a-f]{40})` \|$", evidence, flags=re.M)
    log = [ln.split(" ", 3) for ln in git(repo, "log", "--first-parent", "--reverse", "--format=%H %P %s", f"{B}..{M}").splitlines()]
    print(f"table rows: {len(rows)}; first-parent commits: {len(log)}")
    mismatches = 0
    for i, entry in enumerate(log):
        merge, p1, p2, subject = entry if len(entry) == 4 else (entry[0], entry[1], "", " ".join(entry[2:]))
        m = re.match(r"Merge pull request #(\d+) ", subject)
        actual = (m.group(1) if m else "?", merge, p2)
        claimed = (rows[i][0], rows[i][2], rows[i][3]) if i < len(rows) else None
        if claimed != actual:
            mismatches += 1
            print(f"  MISMATCH at row {i + 1}: claimed {claimed}, actual {actual}")
    non_merge = sum(1 for e in log if len(e) != 4 or not re.fullmatch(r"[0-9a-f]{40}", e[2]))
    print(f"rows whose PR number, merge commit and second parent all match: {len(log) - mismatches} of {len(log)}")
    print(f"first-parent commits that are not two-parent merges: {non_merge}")
    print(f"last first-parent commit equals M: {log[-1][0] == M}")
    problems += mismatches
    total = int(git(repo, "rev-list", "--count", f"{B}..{M}").strip())
    print(f"all commits in B..M: {total}")

    section("3. open pull requests: file-level lists")
    for label, base, head in (
        ("#114 vs M (re-opened base)", M, PR114_HEAD),
        ("#114 vs 11fb457c (historical base)", PR114_OLD_BASE, PR114_HEAD),
        ("#116 vs 11fb457c", PR116_BASE, PR116_HEAD),
        ("#118 vs M", M, PR118_HEAD),
    ):
        mb = git(repo, "merge-base", base, head).strip()
        lines = [ln for ln in git(repo, "diff", "--name-status", "--no-renames", base, head).splitlines() if ln]
        three = [ln for ln in git(repo, "diff", "--name-status", "--no-renames", mb, head).splitlines() if ln]
        print(f"-- {label}: merge-base {mb[:8]} (base is ancestor of head: {mb == base})")
        print(f"   two-dot diff {len(lines)} file(s); diff from merge-base {len(three)} file(s)")
        for ln in three:
            print(f"   {ln}")
    claimed_118 = re.search(r"变更路径（登记全列 14 项）：(.*?)。\n", evidence, flags=re.S)
    claimed_paths = sorted(re.findall(r"`([^`]+)`", claimed_118.group(1))) if claimed_118 else []
    actual_paths = sorted(ln.split("\t", 1)[1] for ln in git(repo, "diff", "--name-status", "--no-renames", M, PR118_HEAD).splitlines() if ln)
    print(f"-- #118 registered paths: {len(claimed_paths)}; actual: {len(actual_paths)}; identical: {claimed_paths == actual_paths}")
    problems += 0 if claimed_paths == actual_paths else 1

    section("4. pinned Europe research files at B")
    for path, want in EUROPE:
        got = sha(blob(repo, B, path))
        problems += 0 if got == want else 1
        print(f"  {'ok  ' if got == want else 'DIFF'} {path}: sha256 {got}")

    section("5. sensitive-content scan of the package text")
    ip_re = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
    patterns = {
        "absolute server path": re.compile(r"(?<![\w.])/(?:home|root|srv|opt|var|etc|mnt|data)/[\w./-]+"),
        "connection string": re.compile(r"\b(?:postgres(?:ql)?|mysql|redis|mongodb)://\S+", re.I),
        "token-like string": re.compile(r"\b(?:ghp_|gho_|github_pat_|sk-|xox[bap]-|AKIA)[A-Za-z0-9_-]{8,}"),
        "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "password assignment": re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key)\s*[=:]\s*\S+"),
    }
    for path in ("README.md", "evidence.md", "reviewer-prompt.md", "MANIFEST.tsv"):
        text = blob(repo, r, f"{PKG}/{path}").decode("utf-8")
        public = []
        for cand in ip_re.findall(text):
            try:
                ip = ipaddress.ip_address(cand)
            except ValueError:
                continue
            if ip.is_global:
                public.append(cand)
        print(f"  {path}: public IPv4-looking strings: {len(public)}" + "".join(f" [sha256 {sha(p.encode())[:12]}]" for p in sorted(set(public))))
        problems += len(public)
        for label, rx in patterns.items():
            hits = rx.findall(text)
            print(f"  {path}: {label}: {len(hits)}")
            problems += len(hits)

    section("summary")
    print(f"problems found: {problems}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
