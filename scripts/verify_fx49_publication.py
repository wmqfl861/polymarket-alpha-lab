"""Verify the FX-49 main checkout, rebuild its reviewed runtime tree, and run its gates."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import NamedTuple


BASELINE = "bcce1b17f7263ab89859febfcb56188c295ab371"
BASELINE_PARENT = "ab6edff2d64ac8aa5c2831d748b8730b63f8312d"
BASELINE_TREE = "6a18d926e7721cd1be0e4361e2ca909a21f20241"
BASELINE_SHA256 = "cda09f641514d8dbdcf0b6f4034b5e980823ca8453d66354b13396c060acf902"
PUBLIC_REPOSITORY = "https://github.com/wmqfl861/polymarket-alpha-lab.git"
ARCHIVE = "docs/handoffs/2026-11-23-sports-runtime/"
RELEASE = "97e9cbcbf510de0f10606ec06380376d627bff50"
RELEASE_TREE = "1bbf62faec2b0415599da5a4b4243b8da2bf4928"
RELEASE_PAYLOAD_BYTES = 314
RELEASE_PAYLOAD_SHA256 = "1ed25e4cc19a270494f9da3ce826ccee881e5c78b33ae3d8a781d65d67e672d4"
RELEASE_OBJECT_SHA256 = "f01f66a48f30893fcb7e4cdbd7457960d56e9e7333cc4d6942550f2f3ad259a0"
RELEASE_MESSAGE = b"FX-49: deliver paper notifications over a pinned direct hc link\n"
ARCHIVED_SOURCE = "a9176cc255054ebd74046bdac8fef0d98e715f6e"
ARCHIVED_EVIDENCE = "6f177ca8d0fa786fa2950a59c4ab1e6d68c71d67"
AUTHOR_DISPLAY_SOURCE = "2087121612725ad813dda1977db575896e430cff"
AUTHOR_DISPLAY_SHA256 = "1d4922a204e77b166fa63b9496e528b4e54d799bc14a89c40c1132d63e63ee6e"
AUTHOR_TIMESTAMP = 1795496371
AUTHOR_TIMEZONE = "+0000"
OLD_RELEASE_REF = "refs/heads/releases/fx49"
MANIFEST_SHA256 = "aa8d9f9f63a171c164fe50e4d51016c56306b34ab61e761856878bd8a89b82b1"
DELTA_SHA256 = "6b95b29db3d3df06b7d378d33fa1cc54f3b6e9035cb65d584abef930997598ee"
PLAN_SHA256 = "343ed404ca3079ebd7dac8c5503df733b1b7ab38dbcb75b9036c45997da5f323"
PUBLIC_PLAN = (19568, "a877a3cdb535d3139f0dc9ea54feef28ec1b97bb08f6330f57c7e56d05cfb48e")
PUBLIC_PLAN_REVIEW = (969, "57480396b288880d76b42c922957694dcde1762db9caaf4989d4eaf0ef20c856")
ARCHIVE_EVIDENCE_SHA256 = "597e44ac9d1406cba0971fd02a69e5450106a0f6981e4ce77a1687b8554cc07f"
RUNTIME = "docs/paper-sim/runtime/"
DESIGN = "docs/paper-sim/design/fx-49/"
MANIFEST = DESIGN + "runtime-release.json"
DELTA = DESIGN + "runtime-delta.json"
OBJECT_RECIPE = DESIGN + "runtime-object-v2.json"
PLAN = DESIGN + "publication-addendum-v2.md"
PLAN_REVIEW = DESIGN + "publication-review-v2.json"
PUBLICATION_INDEX = DESIGN + "publication-index-v2.json"
GATE = "scripts/verify_fx49_publication.py"
TESTS = "tests/test_fx49_weixin_delivery.py"
POSTGRES_GATE = DESIGN + "verify_postgres.py"
WINDOWS = RUNTIME + "scripts/windows/"
SCRIPT = WINDOWS + "Invoke-PaperWeixin.ps1"
CHECKSUM = SCRIPT + ".sha256"
SCRIPT_SHA256 = "e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6"
DRILL = WINDOWS + "offline-acceptance/Test-PaperWeixinOffline.ps1"
ATTRIBUTES = ".gitattributes"
ATTRIBUTE_APPEND = (
    b"\n# FX-49 pins the exact reviewed PowerShell source bytes, including LF.\n"
    + SCRIPT.encode("ascii") + b" -text\n"
)
DIRECT = frozenset({
    RUNTIME + "scripts/weixin_delivery.py",
    SCRIPT, CHECKSUM, WINDOWS + "weixin-binding.example.json", DRILL,
    WINDOWS + "offline-acceptance/HermesSubstitute.cs", WINDOWS + "offline-acceptance/README.md", TESTS,
})
RUNTIME_DELTA = frozenset(RUNTIME + path for path in (
    "README.md",
    "scripts/football_daily.py", "scripts/football_store.py",
    "tests/test_football_focus.py", "tests/test_fx49_fixed_script.py", "tests/test_fx49_football_delivery.py",
))
RELEASE_PATHS = DIRECT | RUNTIME_DELTA | {ATTRIBUTES}
WORKFLOWS = frozenset(".github/workflows/" + path for path in (
    "offline-verification.yml", "native-postgres.yml", "research-paper.yml",
))
REQUIRED_MAIN = DIRECT | WORKFLOWS | {
    ATTRIBUTES, GATE, MANIFEST, DELTA, POSTGRES_GATE, OBJECT_RECIPE, PLAN, PLAN_REVIEW, PUBLICATION_INDEX,
}
EVIDENCE = frozenset(DESIGN + path for path in (
    "plan-original.md", "plan-review-initial.json", "plan-v2.md", "plan-review-v2.json",
    "publication-addendum.md", "publication-review.json",
    "direct-hc-addendum.md", "direct-hc-plan-review.json",
    "linux-first-run.json", "linux-recheck.json", "linux-framework.json",
    "release-review-initial.json", "release-review-framework.json",
    "release-review.json", "main-review.json", "release-index.json",
))
OPTIONAL_MAIN = EVIDENCE | {"DELIVERY_PLAN.md"}
PUBLICATION_INPUTS = REQUIRED_MAIN - {PUBLICATION_INDEX} | EVIDENCE
MAINTAINABLE_INPUTS = WORKFLOWS | {ATTRIBUTES, GATE, POSTGRES_GATE}
FROZEN_PUBLICATION = (PUBLICATION_INPUTS - MAINTAINABLE_INPUTS) | {PUBLICATION_INDEX}
FX49_WORKFLOW_STEP = re.compile(
    rb"(?m)^      - name: (?:Verify FX-49 |Check out the FX-49 )[^\n]+\n(?:^(?!      - |    [^ ])[^\n]*\n)*",
)
RUNTIME_FIXTURES = frozenset(RUNTIME + path for path in (
    "scripts/basketball_bbl.py", "scripts/basketball_college.py", "scripts/basketball_college_forward.py",
    "scripts/basketball_feed.py", "scripts/basketball_focus.py", "scripts/basketball_forward.py",
    "scripts/basketball_models.py", "scripts/basketball_nba_ratings.py", "scripts/football_admission.py",
    "scripts/football_domestic_study.py", "scripts/football_feed.py", "scripts/football_focus.py",
    "scripts/football_forward.py", "scripts/football_learning.py", "scripts/football_legacy.py",
    "scripts/football_lineup_history.py", "scripts/football_model.py", "scripts/football_research.py",
    "scripts/football_shortlist.py", "scripts/football_totals_calibration.py", "scripts/sports_focus.py",
    "scripts/sports_portfolio.py", "scripts/sports_runtime.py", "tests/conftest.py", "tests/offline_postgres.py",
))
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
IPv4 = re.compile(r"(?<![\d.])\d{1,3}(?:\\?\.\d{1,3}){3}(?![\d.])")
IPv6 = re.compile(r"(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:])")
RULES = {
    "private-path": re.compile(r"(?i)(?<![\w])(?:/(?:home|root)/[\w.-]+|[A-Z]:\\{1,2}Users\\{1,2}[\w.-]+)"),
    "credential": re.compile(
        r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b"
        r"|\b(?:api[_-]?key|access[_-]?token|password|passwd|secret)[\x22']?\s*[:=]\s*"
        r"[\x22'][A-Za-z0-9._+/=-]{8,}[\x22']|\bBearer\s+[A-Za-z0-9._+/=-]{20,}"
        r"|\b(?:https?|postgres(?:ql)?|ssh)://[^\s/'\x22]+:[^\s/'\x22]+@"
    ),
    "binding": re.compile(
        r"(?i)\bwxid_[A-Za-z0-9_-]+|[A-Za-z0-9_-]+@(?:chatroom|im\.weixin)\b|\bSHA256:[A-Za-z0-9+/]{30,}"
        r"|\b(?:fingerprint|(?:hub|windows)[_-]?(?:fingerprint|host|id)|(?:private[_-])?binding[_-]digest)"
        r"[\x22']?\s*[:=]\s*[\x22'][0-9a-f:-]{16,}[\x22']"
        r"|\b(?:herdr|win(?:dows)?|(?:owner|target|dm)[_-](?:id|digest))"
        r"[\x22']?\s*[:=]\s*[\x22']"
        r"(?!REPLACE_WITH_EXISTING_PRIVATE_BINDING_SHA256[\x22'])[A-Za-z0-9_]{30,}[\x22']"
    ),
    "endpoint": re.compile(
        r"(?i)(?<![\w-])(?:[A-Za-z0-9][A-Za-z0-9-]{0,62}\.)+(?:internal|corp|onion|lan|local)(?![\w.-])"
        r"|\b(?:postgres(?:ql)?|redis|ssh|herdr)://[^\s'\x22]+"
    ),
    "port": re.compile(
        r"(?i)\blocalhost:\d{2,5}\b"
        r"|\b(?:PAL_[A-Z_]*PORT|(?:production|remote|ssh|herdr|windows)[_-]?port)"
        r"[\x22']?\s*[:=]\s*[\x22']?\d{2,5}\b"
    ),
}


class GateError(Exception):
    """A fixed diagnostic. Contents and command output stay off the console."""


class Entry(NamedTuple):
    mode: str
    blob: str

    def record(self):
        return self._asdict()


def require(condition, reason, path=None):
    if not condition:
        raise GateError(f"{path}: {reason}" if path is not None else reason)


def clean_env():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_", "PG", "PAL_", "PAPERSIM_", "SUPABASE_", "PYTEST_", "PYTHON"))
            and key not in {"DATABASE_URL", "DATABASE_DSN"}}


def run(args, root, *, data=None, timeout=60, env=None):
    return subprocess.run(args, cwd=root, input=data, capture_output=True, timeout=timeout,
                          env=env if env is not None else clean_env(), check=False)


def git(root, *args, data=None, detail="Git command failed", anonymous_fetch=False):
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TMP", "TEMP", "TMPDIR"}}
    env.update({
        "GIT_ALLOW_PROTOCOL": "https" if anonymous_fetch else "",
        "GIT_NO_LAZY_FETCH": "1", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
    })
    result = run([
        "git", "--no-replace-objects", "-c", "core.autocrlf=false", "-c", "core.hooksPath=" + os.devnull,
        "-c", "submodule.recurse=false", "-c", "credential.helper=", "-c", "http.extraHeader=",
        "-c", "http.followRedirects=false", "-C", str(root), *args,
    ], root, data=data, env=env, timeout=120)
    require(result.returncode == 0, detail)
    return result.stdout


def git_text(root, *args, detail="Git command failed"):
    return git(root, *args, detail=detail).decode("ascii").strip()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_object(items):
    result = dict(items)
    require(len(items) == len(result), "duplicate JSON key")
    return result


def decode_json(raw, path):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise GateError(f"{path}: invalid JSON") from None


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def address_literals(line):
    for expression in (IPv4, IPv6):
        for match in expression.finditer(line):
            if match.group() == "::":
                continue
            try:
                yield ipaddress.ip_address(match.group().replace("\\", ""))
            except ValueError:
                continue


def scan(raw):
    """Find deployment data without carrying original lines into diagnostics."""
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return ((1, "non-UTF-8 data"),)
    findings = []
    for number, line in enumerate(text.split("\n"), 1):
        if any(ord(char) < 32 and char not in "\t\r" for char in line):
            findings.append((number, "control character"))
        if any(address_literals(line)):
            findings.append((number, "address"))
        for label, mask in RULES.items():
            if mask.search(line):
                findings.append((number, label))
    return findings


def prescan(path, raw):
    findings = scan(raw)
    if findings:
        line, category = findings[0]
        raise GateError(f"{path}:{line}: private-data scan ({category})")


def tree(root, ref):
    raw = git(root, "ls-tree", "-r", "-z", ref)
    result = {}
    for row in raw.split(b"\0")[:-1]:
        metadata, path = row.split(b"\t", 1)
        mode, _, blob = metadata.decode("ascii").split()
        result[os.fsdecode(path)] = Entry(mode, blob)
    return result


def index(root):
    entries = {}
    for row in git(root, "ls-files", "--stage", "-z").split(b"\0")[:-1]:
        metadata, path = row.split(b"\t", 1)
        mode, blob, stage = metadata.decode("ascii").split()
        require(stage == "0", "unmerged index entries")
        entries[os.fsdecode(path)] = Entry(mode, blob)
    return entries


def changed(before, after):
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def content(root, entry, path):
    if entry is None:
        return b""
    require(entry.mode in {"100644", "100755"} and HEX40.fullmatch(entry.blob), "unexpected Git mode or blob", path)
    return git(root, "cat-file", "blob", entry.blob, detail=f"{path}: missing blob")


def checkout_bytes(root, entry, path):
    require(entry is not None, "missing checkout entry", path)
    file = root / path
    require(not file.is_symlink() and file.is_file() and file.resolve() == file,
            "missing checkout file or unsafe filesystem path", path)
    raw = file.read_bytes()
    require(raw == content(root, entry, path), "checkout bytes differ from Git", path)
    if os.name != "nt":
        executable = bool(file.stat().st_mode & stat.S_IXUSR)
        require(executable == (entry.mode == "100755"), "checkout mode differs from Git", path)
    return raw


def commit(root, ref, detail):
    result = git_text(root, "rev-parse", "--verify", ref + "^{commit}", detail=detail)
    require(HEX40.fullmatch(result), detail)
    return result


def check_parents(root, release, parents, path):
    actual = git_text(root, "rev-list", "--parents", "--max-count=1", release)
    require(actual.split() == [release, *parents], "unexpected commit parent", path)


def edits(old, new):
    old_lines, new_lines = text_lines(old), text_lines(new)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    return [{"line": i + 1, "remove": j - i,
             "insert": [line.decode("utf-8") for line in new_lines[k:l]]}
            for tag, i, j, k, l in matcher.get_opcodes() if tag != "equal"]


def text_lines(raw):
    require(not raw or (raw.endswith(b"\n") and b"\r" not in raw), "delta source needs LF text")
    return raw[:-1].split(b"\n") if raw else []


def apply_delta(path, old, changes):
    source = text_lines(old)
    require(isinstance(changes, list) and changes, "missing replacement ranges", path)
    cursor = 0
    previous = -1
    output = []
    for change in changes:
        require(isinstance(change, dict) and set(change) == {"line", "remove", "insert"},
                "invalid replacement range", path)
        line, remove, insert = change["line"], change["remove"], change["insert"]
        require(type(line) is int and type(remove) is int and isinstance(insert, list),
                "invalid replacement types", path)
        start = line - 1
        require(cursor <= start <= len(source) and previous < start and 0 <= remove <= len(source) - start
                and (remove or insert), "invalid replacement bounds", path)
        require(all(isinstance(value, str) and "\n" not in value and "\r" not in value for value in insert),
                "invalid replacement line", path)
        output.extend(source[cursor:start])
        for offset, value in enumerate(insert):
            encoded = value.encode("utf-8")
            findings = scan(encoded)
            if findings:
                raise GateError(f"{path}:{line + offset}: private-data scan ({findings[0][1]})")
            output.append(encoded)
        cursor, previous = start + remove, start
    output.extend(source[cursor:])
    return b"\n".join(output) + (b"\n" if output else b"")


def release_info(root, release):
    require(commit(root, BASELINE, "approved runtime baseline unavailable") == BASELINE, "wrong baseline")
    check_parents(root, release, [BASELINE], "runtime release")
    baseline, after = tree(root, BASELINE), tree(root, release)
    require(changed(baseline, after) == RELEASE_PATHS, "unexpected runtime changed paths")
    after_tree = git_text(root, "rev-parse", "--verify", release + "^{tree}")
    return baseline, after, after_tree


def release_inventory(before, after):
    inventory = {}
    for path in sorted(RELEASE_PATHS):
        item = {"before": before[path].record() if path in before else None, "after": after[path].record()}
        if path in DIRECT or path == ATTRIBUTES:
            item["main_path"] = path
        if path in RUNTIME_DELTA:
            item["delta"] = True
        inventory[path] = item
    return inventory


def verify_script(script, checksum, expected):
    require(len(script) == 35487 and script.isascii() and script.endswith(b"\n") and b"\r" not in script
            and sha256(script) == expected == SCRIPT_SHA256, "fixed script checksum or line endings differ", SCRIPT)
    require(checksum == f"{expected}  Invoke-PaperWeixin.ps1\n".encode("ascii"),
            "checksum file differs", CHECKSUM)


def read_object_recipe(read):
    expected = {
        "schema_version": 2,
        "parent_of_baseline": BASELINE_PARENT,
        "baseline": BASELINE,
        "baseline_tree": BASELINE_TREE,
        "baseline_payload_bytes": 450,
        "baseline_payload_sha256": BASELINE_SHA256,
        "release": RELEASE,
        "release_tree": RELEASE_TREE,
        "release_payload_bytes": RELEASE_PAYLOAD_BYTES,
        "release_payload_sha256": RELEASE_PAYLOAD_SHA256,
        "release_object_sha256": RELEASE_OBJECT_SHA256,
        "source_commit": ARCHIVED_SOURCE,
        "archive_commit": ARCHIVED_EVIDENCE,
        "script_sha256": SCRIPT_SHA256,
        "delta_sha256": DELTA_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "author_display_source": AUTHOR_DISPLAY_SOURCE,
        "author_display_source_payload_sha256": AUTHOR_DISPLAY_SHA256,
        "author_timestamp": AUTHOR_TIMESTAMP,
        "author_timezone": AUTHOR_TIMEZONE,
    }
    recipe = decode_json(read(OBJECT_RECIPE), OBJECT_RECIPE)
    require(isinstance(recipe, dict) and recipe.keys() == expected.keys()
            and all(type(recipe[key]) is type(value) and recipe[key] == value for key, value in expected.items()),
            "runtime object recipe differs from independent approval", OBJECT_RECIPE)


def approved_inventory(read):
    raw = read(MANIFEST)
    require(len(raw) == 4875 and sha256(raw) == MANIFEST_SHA256, "reviewed manifest SHA-256 differs", MANIFEST)
    manifest = decode_json(raw, MANIFEST)
    require(manifest["baseline"] == BASELINE and manifest["release"] == RELEASE
            and manifest["release_tree"] == RELEASE_TREE and manifest["release_ref"] == OLD_RELEASE_REF
            and manifest["delta_sha256"] == DELTA_SHA256 and manifest["script_sha256"] == SCRIPT_SHA256
            and manifest["version"] == 1 and manifest["changes"].keys() == RELEASE_PATHS,
            "invalid frozen inventory", MANIFEST)
    return manifest


def check_approved_documents(read):
    for path, approved in ((PLAN, PUBLIC_PLAN), (PLAN_REVIEW, PUBLIC_PLAN_REVIEW)):
        raw = read(path)
        require((len(raw), sha256(raw)) == approved, "approved plan or review projection differs", path)
    entries = {}
    for path in sorted(EVIDENCE):
        raw = read(path)
        entries[path] = {"bytes": len(raw), "sha256": sha256(raw)}
    require(sha256(json_bytes(entries)) == ARCHIVE_EVIDENCE_SHA256,
            "historical FX-49 archive bytes differ from independent approval")


def verify_publication_index(root, read, checkout, candidate, *, opening=True):
    value = decode_json(read(PUBLICATION_INDEX), PUBLICATION_INDEX)
    require(isinstance(value, dict) and set(value) == {"schema_version", "source", "archive", "plan_sha256", "inputs"}
            and type(value["schema_version"]) is int and value["schema_version"] == 2
            and value["archive"] == ARCHIVED_EVIDENCE and value["plan_sha256"] == PLAN_SHA256,
            "invalid publication index", PUBLICATION_INDEX)
    source = value["source"]
    require(isinstance(source, str) and HEX40.fullmatch(source), "missing source commit", PUBLICATION_INDEX)
    require(is_ancestor(root, source, checkout), "indexed source is outside checkout history", PUBLICATION_INDEX)
    entries = tree(root, source)
    require(isinstance(value["inputs"], dict) and value["inputs"].keys() == PUBLICATION_INPUTS,
            "incorrect publication input paths", PUBLICATION_INDEX)
    for path in sorted(PUBLICATION_INPUTS):
        declared = value["inputs"][path]
        entry = entries.get(path)
        raw = content(root, entry, path)
        require(entry is not None and declared == {
            "blob": entry.blob, "mode": entry.mode, "bytes": len(raw), "sha256": sha256(raw),
        }, "publication index differs from its recorded source", path)
        if opening or path not in MAINTAINABLE_INPUTS:
            require(candidate.get(path) == entry and read(path) == raw,
                    "publication index differs from tested checkout", path)
        elif path in WORKFLOWS:
            fixed_steps = FX49_WORKFLOW_STEP.findall(raw)
            require(fixed_steps and FX49_WORKFLOW_STEP.findall(read(path)) == fixed_steps,
                    "mandatory FX-49 workflow steps differ", path)
    check_approved_documents(read)
    return source


def check_reviewed_history(root, base, head, *, opening=True):
    forbidden = [oid for oid in (BASELINE, RELEASE) if present_as_commit(root, oid)]
    require(not any(is_ancestor(root, oid, head) for oid in forbidden), "runtime history entered the main PR")
    commits = git_text(root, "rev-list", "--parents", head, "^" + base)
    for line in commits.splitlines():
        item, *parents = line.split()
        require(parents, "publication commit has no parent")
        if len(parents) > 1:
            main_parents = [parent for parent in parents if is_ancestor(root, parent, base)]
            if opening:
                require(len(parents) == 2 and len(main_parents) == 1, "unexpected merge in FX-49 publication history")
            elif not main_parents:
                # The merged side commits are inspected separately. A merge
                # with no main parent must not invent a frozen input version.
                merged = tree(root, item)
                ancestors = [tree(root, parent) for parent in parents]
                require(all(any(merged.get(path) == ancestor.get(path) for ancestor in ancestors)
                            for path in FROZEN_PUBLICATION), "merge introduced unapproved FX-49 material")
                continue
            parents = main_parents
        before, after = tree(root, parents[0]), tree(root, item)
        changes = changed(before, after)
        if opening:
            check_main_paths(changes, opening=False)
            scan_main_changes(root, before, after, byte_reader(root, after, staged=True), changes)
        else:
            require(not changes & FROZEN_PUBLICATION, "intermediate commit changed frozen FX-49 material")


def is_ancestor(root, ancestor, descendant):
    base = git_text(root, "merge-base", ancestor, descendant, detail="cannot compare Git ancestry")
    return base == ancestor


def present_as_commit(root, oid):
    return git(root, "cat-file", "--batch-check=%(objecttype)", data=oid.encode("ascii") + b"\n") == b"commit\n"

def check_main_paths(changes, opening):
    unexpected = changes - REQUIRED_MAIN - OPTIONAL_MAIN
    require(not unexpected, f"unexpected main PR path ({len(unexpected)} outside the publication inventory)")
    if opening:
        require(REQUIRED_MAIN <= changes, "main PR omits a required publication file")


def main_scope(root, base, checkout, head=None):
    require(HEX40.fullmatch(base) and HEX40.fullmatch(checkout), "full checkout and base SHAs required")
    require(commit(root, "HEAD", "cannot read actual checkout") == checkout, "wrong main checkout")
    if head is not None:
        require(HEX40.fullmatch(head), "full PR head SHA required")
        check_parents(root, checkout, [base, head], "main PR checkout")
    elif base != checkout:
        require(git_text(root, "merge-base", base, checkout) == base, "base is not an ancestor of checkout")
    return tree(root, base), tree(root, checkout)


def choose(changes, *, existing):
    # Workflow-only and attribute-only changes matter once this gate is installed.
    return bool(changes & ({GATE, TESTS} | (WORKFLOWS | {ATTRIBUTES} if existing else set()))) or any(
        path.startswith((DESIGN, RUNTIME)) for path in changes
    )


def byte_reader(root, candidate, staged):
    def read(path):
        entry = candidate.get(path)
        require(entry is not None and entry.mode == "100644", "unexpected Git mode or blob", path)
        if staged:
            return content(root, entry, path)
        return checkout_bytes(root, entry, path)
    return read


def scan_main_changes(root, before, candidate, read, changes):
    for path in sorted(changes):
        require(path in candidate, "publication file deleted", path)
        old, new = content(root, before.get(path), path), read(path)
        scan(old)
        if path in WORKFLOWS or path == "DELIVERY_PLAN.md":
            for change in edits(old, new):
                for offset, line in enumerate(change["insert"]):
                    findings = scan(line.encode("utf-8"))
                    if findings:
                        raise GateError(f"{path}:{change['line'] + offset}: private-data scan ({findings[0][1]})")
        else:
            prescan(path, new)


def check_main(root, before, candidate, read):
    opening = MANIFEST not in before
    changes = changed(before, candidate)
    if opening:
        check_main_paths(changes, opening=True)
        scan_main_changes(root, before, candidate, read, changes)
        previous = content(root, before.get(ATTRIBUTES), ATTRIBUTES)
        require(read(ATTRIBUTES) == previous + ATTRIBUTE_APPEND, "main attributes differ", ATTRIBUTES)
    else:
        require(not changes & FROZEN_PUBLICATION & before.keys(), "previously archived FX-49 evidence was modified")
        scan_main_changes(root, before, candidate, read, changes & MAINTAINABLE_INPUTS)
    require(read(ATTRIBUTES).count(ATTRIBUTE_APPEND) == 1, "reviewed FX-49 attribute rule differs", ATTRIBUTES)
    text = git_text(root, "check-attr", "--cached", "text", "filter", "working-tree-encoding", "ident", "--", SCRIPT)
    expected = [f"{SCRIPT}: {name}: {value}" for name, value in (
        ("text", "unset"), ("filter", "unspecified"), ("working-tree-encoding", "unspecified"), ("ident", "unspecified"),
    )]
    require(text.splitlines() == expected, "effective FX-49 checkout attributes differ", ATTRIBUTES)

def verified_overlay(scratch, read, manifest, baseline):
    delta = read(DELTA)
    require(len(delta) == 56597 and sha256(delta) == DELTA_SHA256, "delta SHA-256 differs", DELTA)
    data = decode_json(delta, DELTA)
    require(isinstance(data, dict) and set(data) == {"version", "files"}
            and type(data["version"]) is int and data["version"] == 1
            and isinstance(data["files"], dict) and set(data["files"]) == RUNTIME_DELTA,
            "invalid delta files", DELTA)
    overlay = {}
    for path in sorted(RELEASE_PATHS):
        listed = manifest["changes"][path]
        before = baseline.get(path)
        require(listed["before"] == (before.record() if before else None), "baseline mode or blob differs", path)
        old = content(scratch, before, path)
        scan(old)
        if path in DIRECT:
            raw = read(path)
        elif path == ATTRIBUTES:
            raw = old + ATTRIBUTE_APPEND
        else:
            raw = apply_delta(path, old, data["files"][path])
        prescan(path, raw)
        item = listed["after"]
        after = Entry(item["mode"], git_blob(raw))
        require(after.mode == "100644" and after.record() == item, "publication bytes differ from R", path)
        overlay[path] = (after, raw)
    verify_script(overlay[SCRIPT][1], overlay[CHECKSUM][1], SCRIPT_SHA256)
    return overlay


def git_blob(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def new_checkout(scratch):
    git(scratch, "init", "--quiet", "--template=", "--object-format=sha1")


def import_objects(source, scratch, tip):
    pack = git(source, "pack-objects", "--revs", "--stdout", data=tip.encode("ascii") + b"\n",
               detail="could not export complete pinned Git history")
    git(scratch, "index-pack", "--stdin", data=pack, detail="could not import complete pinned Git history")


def owned_objects(scratch, tip):
    require(not any((scratch / ".git" / name).exists() for name in (
        "objects/info/alternates", "objects/info/http-alternates", "info/grafts", "shallow",
    )) and not any((scratch / ".git" / "objects" / "pack").glob("*.promisor"))
            and not git_text(scratch, "for-each-ref", "--format=%(objectname)", "refs/replace/"),
            "isolated Git repository has shared, replaced or partial objects")
    git(scratch, "fsck", "--connectivity-only", "--no-reflogs", "--no-dangling", tip,
        detail="pinned Git objects are not fully connected")
    owned = set(git_text(scratch, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)").splitlines())
    reachable = set(git_text(scratch, "rev-list", "--objects", "--no-object-names", tip).splitlines())
    require(owned == reachable, "isolated Git repository has objects outside the pinned closure")
    return reachable


def archive_inventory(scratch, baseline):
    """Check the original handoff size table and checksum list as data."""
    names = {name for name in baseline if name.startswith((ARCHIVE, RUNTIME))}
    hashes_path, sizes_path = ARCHIVE + "MANIFEST.sha256", ARCHIVE + "SIZES.tsv"
    hashes = content(scratch, baseline[hashes_path], hashes_path)
    sizes = content(scratch, baseline[sizes_path], sizes_path)
    require(sha256(hashes) == "29752d2e4167444ea553e2690daf9b101301b04a0389ee330c146444cec05f57"
            and sha256(sizes) == "07739f96cb7dcaa8b57bc8ffd2bfe923569d9a63eeb942cea1d1f5ff7b0a80cd",
            "archived baseline manifest or sizes differ")
    expected = dict(line.split("  ", maxsplit=1)[::-1] for line in hashes.decode("utf-8").splitlines())
    header, *rows = sizes.decode("utf-8").splitlines()
    lengths = dict(line.split("\t", maxsplit=1) for line in rows)
    require(header == "path\tbytes" and len(expected) == len(rows) == 69 and names == expected.keys()
            | {hashes_path, sizes_path} and lengths.keys() == expected.keys(), "baseline handoff inventory differs")
    for path, digest in expected.items():
        raw = content(scratch, baseline[path], path)
        require(sha256(raw) == digest and len(raw) == int(lengths[path]), "baseline archived file differs", path)


def verified_baseline(scratch):
    require(not present_as_commit(scratch, RELEASE), "reviewed release already exists in runtime object store")
    require(commit(scratch, BASELINE, "approved runtime baseline unavailable") == BASELINE, "wrong runtime baseline")
    check_parents(scratch, BASELINE, [BASELINE_PARENT], "runtime baseline")
    payload = git(scratch, "cat-file", "commit", BASELINE)
    require(len(payload) == 450 and sha256(payload) == BASELINE_SHA256
            and git_text(scratch, "rev-parse", BASELINE + "^{tree}") == BASELINE_TREE,
            "approved runtime baseline metadata differs")
    reachable = owned_objects(scratch, BASELINE)
    baseline = tree(scratch, BASELINE)
    archive_inventory(scratch, baseline)
    return baseline, reachable, payload


def fetch_baseline(root, scratch, *, hosted):
    new_checkout(scratch)
    if not hosted and present_as_commit(root, BASELINE):
        import_objects(root, scratch, BASELINE)
        return "local-baseline-closure"
    git(scratch, "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
        PUBLIC_REPOSITORY, BASELINE + ":refs/fx49/archived-baseline", anonymous_fetch=True,
        detail="public archive could not provide the approved baseline")
    return "anonymous-public-archive"


def author_fields(payload):
    headers = payload.split(b"\n\n", maxsplit=1)[0].splitlines()
    lines = [line[7:] for line in headers if line.startswith(b"author ")]
    require(len(lines) == 1, "approved identity source does not have one author field")
    match = re.fullmatch(rb"([^<>\r\n]+) <([^ <>\r\n]+)> [0-9]+ [+-][0-9]{4}", lines[0])
    require(match is not None, "approved author field has unexpected shape")
    return match.groups()


def reconstruct_commit(scratch, root_tree, baseline_payload):
    require(is_ancestor(scratch, AUTHOR_DISPLAY_SOURCE, BASELINE), "identity source is not a baseline ancestor")
    source = git(scratch, "cat-file", "commit", AUTHOR_DISPLAY_SOURCE, detail="identity source is unavailable")
    require(sha256(source) == AUTHOR_DISPLAY_SHA256, "identity source commit bytes differ")
    name, _ = author_fields(source)
    _, email = author_fields(baseline_payload)
    identity = name + b" <" + email + f"> {AUTHOR_TIMESTAMP} {AUTHOR_TIMEZONE}\n".encode("ascii")
    payload = (f"tree {root_tree}\nparent {BASELINE}\nauthor ".encode("ascii") + identity
               + b"committer " + identity + b"\n" + RELEASE_MESSAGE)
    framed = f"commit {len(payload)}\0".encode("ascii") + payload
    require(len(payload) == RELEASE_PAYLOAD_BYTES and sha256(payload) == RELEASE_PAYLOAD_SHA256
            and sha256(framed) == RELEASE_OBJECT_SHA256, "reconstructed runtime commit bytes differ")
    oid = git_text_input(scratch, "hash-object", "-t", "commit", "-w", "--stdin", data=payload)
    require(oid == RELEASE and git(scratch, "cat-file", "commit", oid) == payload, "reconstructed Git object differs")
    check_parents(scratch, oid, [BASELINE], "reconstructed runtime release")
    require(git_text(scratch, "rev-list", RELEASE, "^" + BASELINE) == RELEASE, "extra runtime release commits")


def populate(scratch, ref, entries, paths):
    for path in sorted(paths):
        require(path in entries and entries[path].mode == "100644", "missing or unsafe test dependency", path)
        prescan(path, content(scratch, entries[path], path))
    git(scratch, "update-ref", "--no-deref", "HEAD", ref)
    git(scratch, "read-tree", ref)
    git(scratch, "checkout-index", "--force", "-z", "--stdin",
        data=b"".join(os.fsencode(p) + b"\0" for p in sorted(paths)), detail="isolated checkout materialization failed")
    require(commit(scratch, "HEAD", "isolated checkout has no HEAD") == ref
            and git_text(scratch, "write-tree") == git_text(scratch, "rev-parse", ref + "^{tree}"),
            "isolated checkout HEAD or index differs")
    for path in sorted(paths):
        checkout_bytes(scratch, entries.get(path), path)


def rebuild(scratch, manifest, baseline, reachable, baseline_payload, overlay, paths):
    git(scratch, "read-tree", BASELINE)
    updates = []
    for path, (entry, raw) in sorted(overlay.items()):
        digest = git_text_input(scratch, "hash-object", "-w", "--stdin", data=raw)
        require(digest == entry.blob, "recreated blob differs", path)
        updates.append(f"{entry.mode} {digest}\t{path}\0".encode("ascii"))
    git(scratch, "update-index", "-z", "--index-info", data=b"".join(updates))
    recreated = git_text(scratch, "write-tree")
    require(recreated == RELEASE_TREE, "recreated runtime tree differs")
    require(changed(baseline, index(scratch)) == RELEASE_PATHS, "recreated runtime path inventory differs")
    reconstruct_commit(scratch, recreated, baseline_payload)
    added = owned_objects(scratch, RELEASE) - reachable
    kinds = git(scratch, "cat-file", "--batch-check=%(objecttype)", data=("\n".join(sorted(added)) + "\n").encode())
    require(sorted(kinds.splitlines()) == [b"blob"] * 15 + [b"commit"] + [b"tree"] * 9,
            "runtime Git object delta differs")
    _, after, after_tree = release_info(scratch, RELEASE)
    require(after_tree == RELEASE_TREE and release_inventory(baseline, after) == manifest["changes"],
            "reviewed release tree or inventory differs")
    populate(scratch, RELEASE, after, paths)
    return after

def git_text_input(root, *args, data):
    return git(root, *args, data=data).decode("ascii").strip()



def must_reject(check, reason):
    try:
        check()
    except GateError as error:
        require(reason in str(error), "negative check failed for the wrong reason")
    else:
        raise GateError("negative check unexpectedly accepted the change")


def verify_negative_cases(root, scratch, workspace, candidate, checkout, manifest, baseline, baseline_payload):
    trial = workspace / "negative"
    trial.mkdir()
    new_checkout(trial)
    import_objects(root, trial, checkout)
    populate(trial, checkout, candidate, {SCRIPT})

    script = trial / SCRIPT
    raw = script.read_bytes()
    tampered = bytes([raw[0] ^ 1]) + raw[1:]
    prescan(SCRIPT, tampered)
    script.write_bytes(tampered)

    def changed_script(path):
        source = trial if path == SCRIPT else root
        return checkout_bytes(source, candidate.get(path), path)

    must_reject(lambda: verified_overlay(scratch, changed_script, manifest, baseline), "checkout bytes differ from Git")
    script.unlink()
    script.symlink_to(root / SCRIPT)
    must_reject(lambda: verified_overlay(scratch, changed_script, manifest, baseline), "unsafe filesystem path")
    script.unlink()
    blob = git_text_input(trial, "hash-object", "-w", "--stdin", data=tampered)
    git(trial, "update-index", "--cacheinfo", "100644", blob, SCRIPT)
    tampered_index = index(trial)
    staged_script = byte_reader(trial, tampered_index, staged=True)
    must_reject(lambda: verified_overlay(scratch, staged_script, manifest, baseline), "publication bytes differ from R")

    receipt = DESIGN + "linux-framework.json"
    require(receipt in candidate, "reviewed Linux evidence is missing", receipt)
    before_receipt = dict(candidate)
    before_receipt.pop(receipt)
    check_main(trial, before_receipt, candidate, byte_reader(trial, candidate, staged=True))
    for forbidden in (RUNTIME + "scripts/football_daily.py", DESIGN + "unlisted-receipt.json"):
        must_reject(lambda: check_main_paths({forbidden}, opening=False), "unexpected main PR path")
    modified_receipt = dict(candidate) | {receipt: Entry("100644", blob)}
    must_reject(lambda: check_main(trial, candidate, modified_receipt, staged_script), "archived FX-49 evidence was modified")
    must_reject(lambda: prescan(DELTA, b"PAL_PG_PORT=" + bytes([53, 52, 51, 50])), "private-data scan (port)")
    read = byte_reader(root, candidate, staged=False)
    must_reject(lambda: check_approved_documents(lambda p: read(p) + b"\n" if p == PLAN else read(p)),
                "approved plan or review projection differs")
    must_reject(lambda: check_approved_documents(lambda p: read(p) + b"\n" if p == receipt else read(p)),
                "archive bytes differ")
    must_reject(lambda: approved_inventory(lambda _: read(MANIFEST).replace(RELEASE.encode(), b"a" * 40)),
                "manifest SHA-256 differs")
    must_reject(lambda: verified_overlay(scratch, lambda p: b"{}" if p == DELTA else read(p), manifest, baseline),
                "delta SHA-256 differs")
    recipe = decode_json(read(OBJECT_RECIPE), OBJECT_RECIPE)
    for key, altered in (
        ("baseline", BASELINE_PARENT), ("release", BASELINE), ("release_tree", BASELINE_TREE),
        ("author_display_source", BASELINE_PARENT), ("author_timestamp", AUTHOR_TIMESTAMP + 1),
        ("author_timezone", "-0100"), ("release_payload_bytes", RELEASE_PAYLOAD_BYTES + 1),
        ("release_object_sha256", "f" * 64),
    ):
        altered_recipe = json_bytes(recipe | {key: altered})
        must_reject(lambda: read_object_recipe(lambda _: altered_recipe), "object recipe differs")
    must_reject(lambda: read_object_recipe(lambda _: b'{"schema_version":2,"schema_version":2}'),
                "duplicate JSON key")
    must_reject(lambda: read_object_recipe(lambda _: b"{}"), "object recipe differs")
    for wrong in ("100755", "120000"):
        wrong_mode = dict(candidate) | {SCRIPT: Entry(wrong, candidate[SCRIPT].blob)}
        wrong_read = byte_reader(trial, wrong_mode, staged=True)
        must_reject(lambda: verified_overlay(scratch, wrong_read, manifest, baseline), "unexpected Git mode or blob")
    must_reject(lambda: verified_overlay(scratch, lambda p: raw.replace(b"\n", b"\r\n") if p == SCRIPT else read(p),
                                              manifest, baseline), "publication bytes differ from R")
    must_reject(lambda: main_scope(root, checkout, BASELINE_PARENT, BASELINE), "wrong main checkout")
    must_reject(lambda: main_scope(root, checkout, checkout, BASELINE), "unexpected commit parent")
    must_reject(lambda: reconstruct_commit(scratch, BASELINE_TREE, baseline_payload), "runtime commit bytes differ")
    must_reject(lambda: check_parents(scratch, RELEASE, [BASELINE_PARENT], "runtime release"), "unexpected commit parent")
    must_reject(lambda: verified_baseline(scratch), "reviewed release already exists")
    fresh = workspace / "missing"
    fresh.mkdir()
    new_checkout(fresh)
    must_reject(lambda: verified_baseline(fresh), "approved runtime baseline unavailable")
    import_objects(scratch, fresh, BASELINE)
    restored, reachable, payload = verified_baseline(fresh)
    attributes = {ATTRIBUTES}
    after = rebuild(fresh, manifest, restored, reachable, payload,
                    verified_overlay(fresh, read, manifest, restored), attributes)
    require(after == tree(scratch, RELEASE), "independent reconstructed tree differs")
    populate(fresh, BASELINE, restored, attributes)
    populate(fresh, RELEASE, after, attributes)
    populate(fresh, BASELINE, restored, attributes)
    require(owned_objects(fresh, RELEASE) - reachable, "isolated recovery proof is missing generated objects")
    incomplete = workspace / "incomplete"
    incomplete.mkdir()
    new_checkout(incomplete)
    missing = baseline[ATTRIBUTES].blob
    objects = ("\n".join(sorted(reachable - {missing})) + "\n").encode("ascii")
    pack = git(scratch, "pack-objects", "--stdout", "--no-reuse-delta", data=objects)
    git(incomplete, "index-pack", "--stdin", data=pack)
    must_reject(lambda: verified_baseline(incomplete), "not fully connected")
    shallow = fresh / ".git" / "shallow"
    shallow.write_text(BASELINE + "\n", encoding="ascii")
    must_reject(lambda: owned_objects(fresh, RELEASE), "shared, replaced or partial objects")
    shallow.unlink()
    alternate = fresh / ".git" / "objects" / "info" / "alternates"
    alternate.touch()
    must_reject(lambda: owned_objects(fresh, RELEASE), "shared, replaced or partial objects")
    alternate.unlink()
    git(fresh, "update-ref", "refs/replace/" + BASELINE, BASELINE_PARENT)
    must_reject(lambda: owned_objects(fresh, RELEASE), "shared, replaced or partial objects")
    git(fresh, "update-ref", "-d", "refs/replace/" + BASELINE)
    changed_blob = after[SCRIPT].blob
    (fresh / ".git" / "objects" / changed_blob[:2] / changed_blob[2:]).unlink()
    must_reject(lambda: owned_objects(fresh, RELEASE), "not fully connected")
    print("FX-49 negative proofs and independent offline B to R to B recovery passed")

def verify_deployed_source(scratch, manifest):
    module = RUNTIME + "scripts/weixin_delivery.py"
    check = (
        "import runpy, sys\n"
        "module = runpy.run_path(sys.argv[1])\n"
        "raise SystemExit(module['approved_script_sha256']() != sys.argv[2])\n"
    )
    env = clean_env() | {
        "PAL_WEIXIN_RELEASE_COMMIT": manifest["release"],
        "PAL_WEIXIN_POWERSHELL_PATH": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "PAL_WEIXIN_SCRIPT_PATH": r"C:\FX49Fixture\Invoke-PaperWeixin.ps1",
    }
    result = run([sys.executable, "-I", "-S", "-B", "-c", check, str(scratch / module), manifest["script_sha256"]],
                 scratch, env=env)
    require(result.returncode == 0 and result.stdout == result.stderr == b"",
            "service did not approve the manifest and file bytes in the real R checkout", module)


COUNTS = re.compile(rb"\b(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)\b")


def linux_test_env(workspace):
    # Keep managed service configuration and credentials outside the fixtures.
    return {
        "PATH": os.environ.get("PATH", os.defpath), "HOME": str(workspace), "TMPDIR": str(workspace),
        "XDG_CONFIG_HOME": str(workspace), "LC_ALL": "C.UTF-8", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONUTF8": "1", "NO_COLOR": "1",
    }


def prove_tests(label, root, workspace, command, expected, *, timeout=360):
    result = run(command, root, timeout=timeout, env=linux_test_env(workspace))
    lines = result.stdout.splitlines()
    summary = next((line for line in reversed(lines) if COUNTS.search(line)), b"")
    counts = {key: int(number) for number, key in COUNTS.findall(summary)}
    require(result.returncode == 0 and counts == {b"passed": expected},
            f"{label}: missing or unsuccessful tests")
    print(f"{label}: {expected} passed")


def verify_linux(root, scratch, workspace, *, require_portable):
    pytest = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    prove_tests("FX-49 main fake transport", root, workspace, [*pytest, TESTS], 79)
    prove_tests("FX-49 runtime fake transport", scratch, workspace, [*pytest, TESTS], 79)
    script_tests = RUNTIME + "tests/test_fx49_fixed_script.py"
    if shutil.which("pwsh"):
        prove_tests("FX-49 runtime portable script", scratch, workspace, [*pytest, script_tests], 20)
        return
    require(not require_portable, "Linux pwsh is unavailable")
    print("FX-49 optional Linux pwsh unavailable")
    names = (
        "test_manifest_pins_ascii_lf_checkout_bytes",
        "test_windows_native_parser_loads_full_framework_assembly_identity",
        "test_window_guard_is_inclusive_at_start_and_exclusive_at_cutoff",
    )
    prove_tests("FX-49 runtime static script", scratch, workspace,
                [*pytest, *(f"{script_tests}::{name}" for name in names)], len(names))


def verify_postgres(root, scratch, workspace, pg_bin, baseline, after):
    require(pg_bin is not None and all(os.access(pg_bin / name, os.X_OK) for name in ("initdb", "postgres")),
            "temporary PostgreSQL binaries unavailable")
    package = {path: entry.blob for path, entry in after.items()
               if path.startswith("src/polymarket_alpha_lab/") and path.endswith(".py") and entry.mode == "100644"}
    policy = workspace / "runtime-package-blobs.json"
    policy.write_bytes(json_bytes(package))
    store = RUNTIME + "scripts/football_store.py"
    scan(content(scratch, baseline[store], store))
    prove_tests("FX-49 runtime isolated PostgreSQL", scratch, workspace, [
        sys.executable, "-I", "-B", str(root / POSTGRES_GATE), str(scratch),
        str(root), str(pg_bin), baseline[store].blob, str(policy),
    ], 19)


WINDOWS_CASES = (
    ("success", "success", 0, 1, "accepted", None),
    ("target-not-unique", "zero", 24, 0, "absent", None),
    ("target-not-unique", "multiple", 24, 0, "absent", None),
    ("target-not-unique", "group", 24, 0, "absent", None),
    ("target-digest", "wrong-digest", 25, 0, "absent", None),
    ("body-digest", "body-changed", 26, 0, "absent", None),
    ("window-closed", "expired", 22, 0, "absent", None),
    ("native-refusal", "platform-refusal", 32, 1, "refused", "hermes_platform_write_denied"),
    ("native-refusal", "tool-refusal", 32, 1, "refused", "zcode_tool_permission_denied"),
    ("native-refusal", "owner-refusal", 32, 1, "refused", "weixin_owner_message_required"),
)
UTC_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?(?:Z|\+00:00)")


def verify_windows_report(report, script_sha):
    require(isinstance(report, dict) and set(report) == {
        "protocol", "engine", "script_sha256", "status", "phase", "completed_at_utc", "compiler_exit_code",
        "check_exit_code", "cutoff_equality_exit_code", "cases",
    }, "invalid Windows drill report", DRILL)
    require(report["protocol"] == "fx49-offline-acceptance-v1" and report["status"] == "passed"
            and report["phase"] == "cases" and report["script_sha256"] == script_sha
            and isinstance(report["engine"], str) and re.fullmatch(r"5\.1\.\d+\.\d+", report["engine"])
            and isinstance(report["completed_at_utc"], str) and UTC_STAMP.fullmatch(report["completed_at_utc"])
            and all(type(report[key]) is int and report[key] == expected for key, expected in (
                ("compiler_exit_code", 0), ("check_exit_code", 0), ("cutoff_equality_exit_code", 22),
            )), "Windows drill did not verify engine, script, preflight and exact cutoff", DRILL)
    cases = report["cases"]
    require(isinstance(cases, list) and len(cases) == len(WINDOWS_CASES), "missing Windows drill cases", DRILL)
    for case, (name, variant, code, calls, receipt, refusal) in zip(cases, WINDOWS_CASES, strict=True):
        expected = {
            "case": name, "variant": variant, "expected_exit_code": code, "observed_exit_code": code,
            "expected_send_calls": calls, "observed_send_calls": calls, "expected_receipt": receipt,
            "receipt": receipt, "prepare_exit_code": 0, "replay_exit_code": 29 if code in {0, 32} else None,
            "refusal_reason": refusal, "passed": True,
        }
        require(isinstance(case, dict) and set(case) == expected.keys() | {"completed_at_utc"}
                and isinstance(case["completed_at_utc"], str) and UTC_STAMP.fullmatch(case["completed_at_utc"])
                and all(type(case[key]) is type(value) and case[key] == value for key, value in expected.items()),
                f"Windows drill assertion failed ({name}/{variant})", DRILL)


def verify_windows(scratch, workspace, manifest):
    system_root = os.environ.get("SystemRoot")
    require(sys.platform == "win32" and system_root, "system Windows PowerShell 5.1 is required")
    engine = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    require(engine.is_file(), "system Windows PowerShell 5.1 is missing")
    result = run([
        str(engine), "-NoProfile", "-NonInteractive", "-File", str(scratch / DRILL),
        "-Workspace", str(workspace), "-ApprovedScriptSha256", manifest["script_sha256"],
    ], scratch, timeout=240)
    require(result.returncode == 0 and not result.stderr, "Windows offline drill failed", DRILL)
    report = decode_json(result.stdout, DRILL)
    verify_windows_report(report, manifest["script_sha256"])
    output = json_bytes(report)
    prescan(DRILL, output)
    print(output.decode("utf-8"), end="")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["verify", "linux", "postgres", "windows"])
    parser.add_argument("--base", required=True, help="actual PR base or local staged baseline")
    parser.add_argument("--checkout", help="full commit of the main Git checkout")
    parser.add_argument("--pr-head", help="actual PR head")
    parser.add_argument("--staged", action="store_true", help="check the actual staged main bytes before opening a PR")
    parser.add_argument("--postgres-bin", type=Path)
    parser.add_argument("--require-portable-powershell", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        require(not args.staged or (args.mode == "verify" and not args.pr_head), "staged verification needs a local baseline")
        require(not args.pr_head or args.checkout, "PR commit tuple required")
        checkout = args.checkout or commit(root, "HEAD", "cannot read actual checkout")
        base = args.base
        before, candidate = main_scope(root, base, checkout, args.pr_head)
        if args.staged:
            candidate = index(root)
        if args.pr_head:
            ancestor = git_text(root, "merge-base", base, args.pr_head)
            head_tree = tree(root, args.pr_head)
            head_changes = changed(tree(root, ancestor), head_tree)
            checkout_changes = changed(before, candidate)
            if not choose(head_changes | checkout_changes, existing=MANIFEST in before):
                check_reviewed_history(root, base, args.pr_head, opening=False)
                print(f"FX-49 paths unchanged in main PR checkout {checkout}")
                return 0
            if MANIFEST not in before:
                check_main_paths(head_changes, opening=True)
            check_reviewed_history(root, base, args.pr_head, opening=MANIFEST not in before)
            require(all(candidate.get(path) == head_tree.get(path) for path in DIRECT),
                    "gate checkout direct file differs from actual PR head")
        with TemporaryDirectory(prefix="fx49-") as temp:
            workspace = Path(temp).resolve()
            scratch = workspace / "checkout"
            scratch.mkdir()
            read = byte_reader(root, candidate, args.staged)
            check_main(root, before, candidate, read)
            source = verify_publication_index(root, read, checkout, candidate, opening=MANIFEST not in before)
            read_object_recipe(read)
            manifest = approved_inventory(read)
            location = fetch_baseline(root, scratch, hosted=bool(args.pr_head))
            baseline, reachable, payload = verified_baseline(scratch)
            overlay = verified_overlay(scratch, read, manifest, baseline)
            paths = set(DIRECT) | {ATTRIBUTES}
            if args.mode == "postgres":
                paths.update(RUNTIME_DELTA | RUNTIME_FIXTURES)
            if args.mode == "linux":
                paths.add(RUNTIME + "tests/test_fx49_fixed_script.py")
            after = rebuild(scratch, manifest, baseline, reachable, payload, overlay, paths)
            print(json.dumps({
                "gate": "FX-49", "mode": args.mode, "base": base, "head": args.pr_head,
                "checkout": checkout, "source": source, "baseline": BASELINE, "baseline_source": location,
                "release": RELEASE, "runtime_tree": RELEASE_TREE,
                "runtime_object_delta": {"commit": 1, "tree": 9, "blob": 15},
            }, sort_keys=True), flush=True)
            if args.mode in {"linux", "postgres"}:
                verify_deployed_source(scratch, manifest)
            if args.mode == "linux":
                verify_negative_cases(root, scratch, workspace, candidate, checkout, manifest, baseline, payload)
                verify_linux(root, scratch, workspace, require_portable=args.require_portable_powershell)
            elif args.mode == "postgres":
                verify_postgres(root, scratch, workspace, args.postgres_bin, baseline, after)
            elif args.mode == "windows":
                verify_windows(scratch, workspace, manifest)
        return 0
    except GateError as issue:
        print(f"FX-49 failed: {issue}", file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        print("FX-49 failed to read, reconstruct or verify the reviewed release", file=sys.stderr)
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
