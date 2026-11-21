#!/usr/bin/env python3
"""History-wide scan of every blob reachable from any ref for credential-like strings.

usage: python3 -I history_secret_scan.py <full_clone_or_mirror_dir> <head_commit>

Reviewer's own read-only script (2026-11-21 review). It needs a FULL clone (all blobs
present; a blobless partial clone would fetch on demand). It reads objects with
`git cat-file --batch` and never imports or runs repository code.

No matched value is ever printed. For each hit class the output gives a count, and for
classes that need a human look it gives the path, whether the blob is still in the tree
of <head_commit>, and a "shape" (length, character classes, first 10 hex of the SHA-256)
so that two occurrences of the same value can be recognised without revealing it.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections import Counter, defaultdict

PLACEHOLDER = re.compile(
    r"(?i)^(\*+|x{3,}|<[^>]*>|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|%\(?\w*\)?s|\{[^}]*\}|\.{3,}|…+|"
    r"password|passwd|secret|pass|pw|postgres|redacted|changeme|example|dummy|test|token|none|null|"
    r"your[_-]?\w*|my[_-]?\w*|fake\w*|placeholder\w*|sample\w*)$"
)
LOCAL_HOST = re.compile(r"^(localhost|127\.0\.0\.1|\[?::1\]?|0\.0\.0\.0|host\.docker\.internal|db|postgres)(:\d+)?$", re.I)

PATTERNS = {
    "private_key_block": re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "github_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
    "sk_style_key": re.compile(rb"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "slack_token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "bearer_token": re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{30,}"),
    "jwt": re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "dsn_with_password": re.compile(rb"(?i)\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?|amqp)://([^\s:/@'\"`<>]+):([^\s@'\"`<>]+)@([^\s/'\"`<>?#,)]+)"),
    "env_secret_assignment": re.compile(rb"(?m)^[ \t]*(?:export[ \t]+)?([A-Z][A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|PASSPHRASE)[A-Z0-9_]*)[ \t]*=[ \t]*['\"]?([^\s'\"#]{8,})"),
    "hex64_near_key_word": re.compile(rb"(?i)(?:private[_ -]?key|privkey|secret[_ -]?key|mnemonic|seed)\W{0,24}(?:0x)?([0-9a-f]{64})\b"),
    "pgpass_line": re.compile(rb"(?m)^[\w.*-]+:\d{2,5}:[\w*-]+:[\w*-]+:(\S{4,})$"),
    "ssh_public_key": re.compile(rb"\bssh-(?:rsa|ed25519|dss) AAAA[0-9A-Za-z+/]{40,}"),
}
NEEDS_LOOK = ("private_key_block", "github_token", "sk_style_key", "aws_access_key", "slack_token",
              "bearer_token", "dsn_with_password", "env_secret_assignment", "hex64_near_key_word", "pgpass_line")


def shape(value: bytes) -> str:
    text = value.decode("utf-8", "replace")
    classes = "".join(sorted({"a" if c.islower() else "A" if c.isupper() else "9" if c.isdigit() else "#" for c in text}))
    return f"len={len(text)} classes={classes} sha256={hashlib.sha256(value).hexdigest()[:10]}"


def main() -> int:
    repo, head = sys.argv[1], sys.argv[2]

    def git(*args: str) -> bytes:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout

    paths: dict[str, str] = {}
    for line in git("rev-list", "--all", "--objects").decode("utf-8", "replace").splitlines():
        oid, _, path = line.partition(" ")
        if path and oid not in paths:
            paths[oid] = path
    in_head = {ln.split()[2] for ln in git("ls-tree", "-r", head).decode("utf-8", "replace").splitlines()}
    listing = git("cat-file", "--batch-all-objects", "--batch-check").decode().splitlines()
    blobs = [ln.split()[0] for ln in listing if ln.split()[1] == "blob"]
    kinds = Counter(ln.split()[1] for ln in listing)
    print(f"objects: {dict(sorted(kinds.items()))}")
    print(f"commits reachable from all refs: {git('rev-list', '--all', '--count').decode().strip()}; refs: {len(git('for-each-ref').splitlines())}")
    print(f"blobs scanned: {len(blobs)}; blobs in the tree of {head[:8]}: {len(in_head)}")

    proc = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    counts: Counter = Counter()
    detail: dict[str, dict] = defaultdict(dict)
    total_bytes = 0
    for oid in blobs:
        proc.stdin.write(oid.encode() + b"\n")
        proc.stdin.flush()
        header = proc.stdout.readline().split()
        size = int(header[2])
        data = proc.stdout.read(size)
        proc.stdout.read(1)
        total_bytes += size
        for name, rx in PATTERNS.items():
            for m in rx.finditer(data):
                counts[name] += 1
                if name not in NEEDS_LOOK:
                    continue
                note = ""
                if name == "dsn_with_password":
                    user, pw, host = m.group(1), m.group(2), m.group(3)
                    host_text = host.decode("utf-8", "replace")
                    local = bool(LOCAL_HOST.match(host_text))
                    placeholder = bool(PLACEHOLDER.match(pw.decode("utf-8", "replace")))
                    if local or placeholder:
                        counts["dsn_with_password: local host or placeholder password"] += 1
                        continue
                    value = pw
                    note = "host=" + re.sub(r"\d{1,3}(?:\.\d{1,3}){3}", "<ipv4>", host_text)
                elif name == "env_secret_assignment":
                    value = m.group(2)
                    if PLACEHOLDER.match(value.decode("utf-8", "replace")) or value.startswith((b"$", b"<", b"os.", b"{")):
                        counts["env_secret_assignment: placeholder or reference"] += 1
                        continue
                    note = "name=" + m.group(1).decode("utf-8", "replace")
                elif name in ("hex64_near_key_word", "pgpass_line"):
                    value = m.group(1)
                else:
                    value = m.group(0)
                key = (name, hashlib.sha256(value).hexdigest())
                entry = detail[name].setdefault(key, {"shape": shape(value), "note": note, "blobs": set()})
                entry["blobs"].add(oid)
    proc.stdin.close()
    proc.wait()
    print(f"bytes scanned: {total_bytes}")
    print()
    print("== counts per class (occurrences across all blobs)")
    for name in list(PATTERNS) + sorted(k for k in counts if k not in PATTERNS):
        print(f"  {counts.get(name, 0):6d}  {name}")
    print()
    print("== distinct values that need a human look (value never shown)")
    shown = 0
    for name in NEEDS_LOOK:
        for (_, _digest), entry in sorted(detail[name].items(), key=lambda kv: -len(kv[1]["blobs"])):
            shown += 1
            blobs_here = sorted(entry["blobs"])
            still = sum(1 for b in blobs_here if b in in_head)
            sample = sorted({paths.get(b, "?") for b in blobs_here})[:4]
            print(f"  [{name}] {entry['shape']} {entry['note']}".rstrip())
            print(f"      blobs: {len(blobs_here)} (still in head tree: {still}); paths: {sample}")
    if not shown:
        print("  none")

    print()
    print("== commit author and committer e-mail identities across all refs (addresses never shown)")
    uses: Counter = Counter()
    for line in git("log", "--all", "--format=%ae%x09%ce").decode("utf-8", "replace").splitlines():
        for address in line.split("\t"):
            uses[address.lower()] += 1

    def identity_class(address: str) -> str:
        local, _, domain = address.partition("@")
        if domain.endswith("users.noreply.github.com") or domain in ("noreply.github.com", "github.com") or "noreply" in local:
            return "noreply or platform"
        if domain.endswith((".invalid", ".example", ".local", ".test")) or domain in ("localhost", "local", "invalid", "example.com", "example.org"):
            return "placeholder (not deliverable)"
        return "deliverable-looking"

    grouped: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for address, count in uses.items():
        grouped[identity_class(address)].append((address, count))
    for label in sorted(grouped):
        items = grouped[label]
        domains = Counter(address.partition("@")[2] for address, _ in items)
        print(f"  {label}: {len(items)} distinct, {sum(c for _, c in items)} uses; domains: {dict(sorted(domains.items()))}")
        if label == "deliverable-looking":
            for address, count in sorted(items, key=lambda item: -item[1]):
                local, _, domain = address.partition("@")
                print(f"      local part length={len(local)} domain={domain} uses={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
