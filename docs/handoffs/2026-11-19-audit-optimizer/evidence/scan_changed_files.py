#!/usr/bin/env python3
"""Scan every file added or modified between two commits for address-like,
credential-like and e-mail-like strings. Values are never printed.

Reviewer's own read-only script (2026-11-19 audit). It reads blobs with
`git cat-file`; it never imports or runs repository code. Compressed members
(.gz, .tar.gz) are decompressed in memory and scanned as text where possible.

Usage: python3 -I scan_changed_files.py <repo_dir> <base_commit> <head_commit>
"""
from __future__ import annotations

import gzip
import io
import ipaddress
import re
import subprocess
import sys
import tarfile
from collections import Counter

IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\\?\.(\d{1,3})\\?\.(\d{1,3})\\?\.(\d{1,3})(?![\d.])")
DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
SECRETS = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "pgpassword": re.compile(r"PGPASSWORD\s*=\s*['\"]?[^\s'\"$]{4,}"),
    "dsn_password": re.compile(r"(?i)postgres(?:ql)?://[^\s:@/]+:[^\s@/]+@"),
    "password_kv": re.compile(r"(?i)\bpassword\s*[=:]\s*['\"][^'\"\s]{4,}['\"]"),
    "api_key_kv": re.compile(r"(?i)\b(api[_-]?key|apikey|secret|token|bearer)\b\s*[=:]\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]"),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    "sk_key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "webhook": re.compile(r"hooks\.slack\.com/services/|discord(?:app)?\.com/api/webhooks/"),
    "telegram_bot": re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b"),
    "eth_private_key_like": re.compile(r"(?i)\b(private[_-]?key|secret[_-]?key|mnemonic|seed[_-]?phrase)\b\s*[=:]\s*['\"][^'\"]{16,}['\"]"),
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(?i)@(example\.(com|org|invalid)|users\.noreply\.github\.com|anthropic\.com|noreply\.|localhost)|noreply@|@v\d|@[0-9a-f]{7,40}\b")
SKIP_SELF = ("docs/handoffs/2026-11-13-m0-review/evidence/", "docs/handoffs/2026-11-12-audit/evidence/")


def git_bytes(repo: str, *args: str) -> bytes:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout


def texts(path: str, data: bytes):
    """Yield (member_name, text) pairs for one blob."""
    if path.endswith((".tar.gz", ".tgz")):
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                for member in archive:
                    if member.isfile() and member.size <= 64 * 1024 * 1024:
                        handle = archive.extractfile(member)
                        if handle is not None:
                            raw = handle.read()
                            if b"\0" not in raw[:8192]:
                                yield f"{path}!{member.name}", raw.decode("utf-8", errors="replace")
        except (tarfile.TarError, OSError, EOFError):
            yield path, ""
        return
    if path.endswith(".gz"):
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError):
            return
    if b"\0" in data[:8192]:
        return
    yield path, data.decode("utf-8", errors="replace")


def main() -> None:
    repo, base, head = sys.argv[1:4]
    names = [n for n in git_bytes(repo, "diff", "--name-only", "-z", "--diff-filter=AM", base, head).decode().split("\0") if n]
    counts: Counter[str] = Counter()
    hits: list[str] = []
    binary = scanned = members = 0
    largest: list[tuple[int, str]] = []
    for path in names:
        data = git_bytes(repo, "cat-file", "blob", f"{head}:{path}")
        largest.append((len(data), path))
        found_text = False
        for member, text in texts(path, data):
            found_text = True
            members += 1
            own_scanner = member.startswith(SKIP_SELF)
            for number, line in enumerate(text.splitlines(), 1):
                for match in IPV4.finditer(line):
                    octets = [int(x) for x in match.groups()]
                    if any(x > 255 for x in octets):
                        continue
                    address = ipaddress.ip_address(".".join(map(str, octets)))
                    if not address.is_global or any(address in net for net in DOC_NETS):
                        continue
                    # dotted version strings inside user-agent values or package pins are counted separately
                    context = line[max(0, match.start() - 24):match.start()].lower()
                    kind = "ipv4_public"
                    if re.search(r"(chrome|firefox|safari|version|v|edg|gecko|applewebkit|mozilla|python|psql|postgresql|openssl|curl)[/ =:]*$", context):
                        kind = "ipv4_like_version_string"
                    counts[kind] += 1
                    if kind == "ipv4_public" and len(hits) < 400:
                        hits.append(f"ipv4_public {member}:{number} (value masked)")
                if not own_scanner:
                    for name, pattern in SECRETS.items():
                        if pattern.search(line):
                            counts[f"secret:{name}"] += 1
                            if len(hits) < 400:
                                hits.append(f"secret:{name} {member}:{number} (value masked)")
                for match in EMAIL.finditer(line):
                    if EMAIL_OK.search(match.group(0)) or EMAIL_OK.search(line[max(0, match.start() - 2):match.end() + 2]):
                        counts["email_placeholder_or_noreply"] += 1
                        continue
                    counts["email_other"] += 1
                    if len(hits) < 400:
                        hits.append(f"email_other {member}:{number} (value masked)")
        if found_text:
            scanned += 1
        else:
            binary += 1
    print(f"base={base} head={head}")
    print(f"files added or modified={len(names)} scanned as text={scanned} (text members incl. archives={members}) binary or unreadable={binary}")
    print("hit counts:", dict(sorted(counts.items())))
    grouped: Counter[str] = Counter()
    for hit in hits:
        kind, where, *_ = hit.split(" ")
        grouped[f"{kind} {where.rsplit(':', 1)[0]}"] += 1
    for key, value in sorted(grouped.items()):
        print(f"  {value:5d} x {key}")
    print("largest files:")
    for size, path in sorted(largest, reverse=True)[:12]:
        print(f"  {size:10d} {path}")


if __name__ == "__main__":
    main()
