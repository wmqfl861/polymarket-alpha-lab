#!/usr/bin/env python3
"""Read-only scan of the lines ADDED between two commits for sensitive-looking strings.

Usage: python3 -I added_line_sensitive_scan.py <repo> <base> <head> [--show]

It never prints a matched value. For every category it prints the number of matches and,
with --show, the file path, the line number in <head>, and the match with everything except
its shape masked (letters -> a, digits -> 9). Categories:

  public_ipv4      dotted quads that are not private, loopback, link-local, documentation,
                   multicast, or version-like numbers inside words
  email            things shaped like an e-mail address (noreply and example domains are counted
                   separately as email_benign)
  token            well-known credential shapes: GitHub tokens, AWS keys, private key blocks,
                   Slack tokens, "sk-" keys, JWTs, bearer headers with a literal value
  url_secret       URLs that carry key=/token=/secret=/access_token= with a literal value
  dsn_password     postgres/mysql/redis/mongodb URLs with user:password@
  assigned_secret  NAME = "literal" where NAME contains password/passwd/secret/token/api_key/apikey
                   and the literal is at least 8 characters and is not a placeholder
  wechat_id        wxid_…, gh_… (public account ids), openid-like o…-28-char ids, chatroom ids
  windows_user_path  C:\\Users\\<name>\\… style paths (may disclose an account name)
  home_path        /home/<name>/… or /Users/<name>/… absolute paths (may disclose an account name)
"""
import ipaddress
import re
import subprocess
import sys


def run(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          errors="replace", check=True).stdout


def mask(s):
    return re.sub(r"[0-9]", "9", re.sub(r"[A-Za-z]", "a", s))


PLACEHOLDER = re.compile(r"^(x+|\*+|<.*>|\$\{.*\}|@.*@|changeme|example|placeholder|redacted|dummy|test|none|null|"
                         r"your[_-].*|.*_here|fake.*|sample.*)$", re.I)

PATTERNS = {
    "token": re.compile(
        r"(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|gho_[A-Za-z0-9]{30,}|ghs_[A-Za-z0-9]{30,}|"
        r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abprs]-[A-Za-z0-9-]{10,}|"
        r"\bsk-[A-Za-z0-9_-]{20,}|\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
        r"[Bb]earer\s+[A-Za-z0-9_\-\.=]{20,})"),
    "url_secret": re.compile(
        r"https?://[^\s\"'<>]*[?&](key|token|secret|access_token|sig|signature|apikey|api_key)=([A-Za-z0-9_\-]{8,})"),
    "dsn_password": re.compile(r"\b(postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://[^\s:/@\"']+:([^\s@\"'{}$<>]{3,})@"),
    "assigned_secret": re.compile(
        r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|token|api_key|apikey)[A-Za-z0-9_]*)\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"),
    "wechat_id": re.compile(r"\b(wxid_[A-Za-z0-9]{8,}|gh_[0-9a-f]{12}\b|[0-9]{8,}@chatroom|\bo[A-Za-z0-9_-]{27}\b)"),
    "windows_user_path": re.compile(r"[A-Za-z]:\\+Users\\+([A-Za-z0-9._-]+)"),
    "home_path": re.compile(r"(?<![A-Za-z0-9_~.])/(?:home|Users)/([A-Za-z0-9._-]+)/"),
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
IPV4 = re.compile(r"(?<![0-9A-Za-z.])((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?![0-9A-Za-z]|\.[0-9])")
BENIGN_EMAIL = re.compile(r"(?i)(noreply|example\.(com|org|net|invalid)|\.invalid$|users\.noreply\.github\.com|"
                          r"@pytest\.|@mark\.|@dataclass|localhost)")
GENERIC_USER = {"claude", "runner", "user", "username", "name", "you", "me", "ubuntu", "root", "work", "example",
                "operator", "service", "svc", "app", "test", "tester", "dev", "postgres", "pal"}


def public_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if any(len(p) > 1 and p.startswith("0") for p in text.split(".")):
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or ip in ipaddress.ip_network("192.0.2.0/24")
                or ip in ipaddress.ip_network("198.51.100.0/24") or ip in ipaddress.ip_network("203.0.113.0/24"))


def main():
    repo, base, head = sys.argv[1:4]
    show = "--show" in sys.argv
    diff = run(repo, "diff", "-U0", "--no-color", "--no-renames", base, head)
    counts, rows = {}, []
    path, lineno = None, 0
    for line in diff.split("\n"):
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            continue
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            lineno = int(m.group(1))
            continue
        if not line.startswith("+") or line.startswith("+++") or path is None:
            continue
        text = line[1:]
        here = lineno
        lineno += 1
        for m in IPV4.finditer(text):
            if public_ip(m.group(1)):
                counts["public_ipv4"] = counts.get("public_ipv4", 0) + 1
                rows.append(("public_ipv4", path, here, mask(m.group(1))))
        for m in EMAIL.finditer(text):
            cat = "email_benign" if BENIGN_EMAIL.search(m.group(0)) else "email"
            counts[cat] = counts.get(cat, 0) + 1
            if cat == "email":
                rows.append((cat, path, here, mask(m.group(0))))
        for cat, pat in PATTERNS.items():
            for m in pat.finditer(text):
                val = m.group(m.lastindex) if m.lastindex else m.group(0)
                if cat in ("assigned_secret", "url_secret", "dsn_password") and PLACEHOLDER.match(val):
                    continue
                if cat == "assigned_secret" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\]]*", val) and not re.search(r"[0-9]", val):
                    # looks like an identifier or attribute reference, not a literal credential
                    counts["assigned_identifier"] = counts.get("assigned_identifier", 0) + 1
                    continue
                if cat in ("windows_user_path", "home_path") and val.lower() in GENERIC_USER:
                    counts[cat + "_generic"] = counts.get(cat + "_generic", 0) + 1
                    continue
                counts[cat] = counts.get(cat, 0) + 1
                rows.append((cat, path, here, mask(m.group(0))[:90]))
    print(f"base={base[:12]} head={head[:12]}")
    for cat in sorted(counts):
        print(f"  {cat}: {counts[cat]}")
    if not counts:
        print("  (no matches)")
    if show:
        for cat, p, n, shape in rows:
            print(f"    {cat}\t{p}:{n}\t{shape}")


if __name__ == "__main__":
    main()
