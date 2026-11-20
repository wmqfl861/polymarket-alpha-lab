"""Tracked public-address hygiene guard (FX-31, 2026-11-20).

Remediates finding FX-31 / N-6 (2026-11-19 audit fix list): three tracked
files that re-quoted the server public address were redacted in place (see
docs/handoffs/m3-cleanup/REDACTIONS.md and the FX-31 section of
docs/remediation-evidence/2026-11-14/MANIFEST.md), and this guard freezes the
remaining tracked stock so that any new or unexplained public-address
occurrence anywhere in the repository fails the suite.

Detector behavior is identical to
docs/handoffs/2026-11-13-m0-review/evidence/address_occurrence_diff.py
(IPv4, including the regex-escaped form; global-unicast only; documentation
nets, private and loopback ranges excluded; IPv6 out of scope).

Hygiene rules for this module itself:
- The frozen allowlist and every other literal in this file store sha256
  digests, byte counts and line numbers only — never a raw address value.
- All address-shaped test data is assembled at runtime (string joins over
  integer octets); fault injection uses synthetic global-unicast values that
  are not any value in the tracked stock and not any real host of this
  repository, or pure in-memory count maps.
- Failure messages print path, sha256 and expected/actual counts only.

Allowlist governance (project rule for this constant):
FROZEN_ALLOWLIST changes only through an explicit node decision recorded in
the same PR that changes the stock. Known hooks: FX-25 (owner-approved
AGENTS.md alias replacement shrinks the grandfathered root entry below),
FX-33, and FX-18 archival batches — moving or renaming a grandfathered file
fails this guard loudly until that batch's own node decision regrows the
map; that fail-loud behavior is intentional.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Same shape as address_occurrence_diff.py: optional backslash before each dot
# so regex-escaped occurrences are caught too.
GREP_PATTERN = r"[0-9]{1,3}\\?\.[0-9]{1,3}\\?\.[0-9]{1,3}\\?\.[0-9]{1,3}"
FIND = re.compile(
    r"(?<![\d.])(\d{1,3})\\?\.(\d{1,3})\\?\.(\d{1,3})\\?\.(\d{1,3})(?![\d.])"
)
# Documentation ranges (first three octets plus a zero host part), assembled
# from integers so this source file itself stays free of dotted-quad text.
_DOC_NET_OCTETS = ((192, 0, 2), (198, 51, 100), (203, 0, 113))
DOC_NETS = tuple(
    ipaddress.ip_network(".".join(map(str, octets + (0,))) + "/24")
    for octets in _DOC_NET_OCTETS
)

REMEDIATED_PATHS = (
    "docs/handoffs/m3-cleanup/codex-b0-pause-decisions-20261116.log",
    "docs/remediation-evidence/2026-11-14/decision/m1-final-review-rerun-20261116.log",
    "docs/remediation-evidence/2026-11-14/MANIFEST.md",
)

# Frozen at FX-31 (2026-11-20) from the post-redaction scan of the tracked +
# untracked working-tree stock: 6 distinct values, 42 occurrences in 13 files.
# The AGENTS.md entry is the narrowly grandfathered root baseline (count 1)
# that stays until the FX-25 owner-approved alias draft lands.
FROZEN_ALLOWLIST: dict[str, dict[str, int]] = {
    "AGENTS.md": {
        "fb214e0790225f76fafe112263bfd20ea879fc63bef24ad451994ab61a301530": 1,
    },
    "DELIVERY_PLAN.md": {
        "fb214e0790225f76fafe112263bfd20ea879fc63bef24ad451994ab61a301530": 7,
        "0a4731266cecbe0bc45ea8a91a21c1e202ee88990f7c5e8f8e69c79a19377789": 2,
    },
    "docs/handoffs/g2-activation-readiness.md": {
        "0a4731266cecbe0bc45ea8a91a21c1e202ee88990f7c5e8f8e69c79a19377789": 5,
    },
    "docs/handoffs/g2-first-real-run-runbook-draft.md": {
        "0a4731266cecbe0bc45ea8a91a21c1e202ee88990f7c5e8f8e69c79a19377789": 1,
    },
    "docs/handoffs/l7-l8-status.md": {
        "fb214e0790225f76fafe112263bfd20ea879fc63bef24ad451994ab61a301530": 3,
    },
    "docs/handoffs/l8-l9-status.md": {
        "fb214e0790225f76fafe112263bfd20ea879fc63bef24ad451994ab61a301530": 3,
        "0a4731266cecbe0bc45ea8a91a21c1e202ee88990f7c5e8f8e69c79a19377789": 5,
    },
    "docs/paper-sim/server-mirror/tools/tests/test_understat_refresh.py": {
        "0d2fdc313ceb7a434774fd9de6aa70804ccdcbf19c7bee77ed2d7a6f31ac2f68": 3,
    },
    "docs/releases/linux-preview-release.md": {
        "fb214e0790225f76fafe112263bfd20ea879fc63bef24ad451994ab61a301530": 2,
    },
    "docs/releases/v0.1.0-linux-preview.1/handoff.md": {
        "fb214e0790225f76fafe112263bfd20ea879fc63bef24ad451994ab61a301530": 2,
    },
    "docs/releases/v0.1.0-linux-preview.1/release-manifest.json": {
        "fb214e0790225f76fafe112263bfd20ea879fc63bef24ad451994ab61a301530": 1,
    },
    "docs/reviews/model-review-2026-11-07.md": {
        "0a4731266cecbe0bc45ea8a91a21c1e202ee88990f7c5e8f8e69c79a19377789": 1,
    },
    "tests/test_cli_probability_selection_scorer_agreement.py": {
        "838c4c2573848f58e74332341a7ca6bc5cd86a8aec7d644137d53b4d597f10f5": 1,
    },
    "tests/test_research_linux_relay.py": {
        "5ad7ffdf62642216060820c629bc4f186813bcbfb71e5a10d63fcacbd354181f": 1,
        "838c4c2573848f58e74332341a7ca6bc5cd86a8aec7d644137d53b4d597f10f5": 3,
        "d20004891b6baada0c141972fc179712dfbfa0adcfe16288ba078164be954439": 1,
    },
}
FROZEN_TOTAL_OCCURRENCES = 42
FROZEN_PER_DIGEST_TOTALS = {
    "fb214e0790225f76fafe112263bfd20ea879fc63bef24ad451994ab61a301530": 19,
    "0a4731266cecbe0bc45ea8a91a21c1e202ee88990f7c5e8f8e69c79a19377789": 14,
    "838c4c2573848f58e74332341a7ca6bc5cd86a8aec7d644137d53b4d597f10f5": 4,
    "0d2fdc313ceb7a434774fd9de6aa70804ccdcbf19c7bee77ed2d7a6f31ac2f68": 3,
    "5ad7ffdf62642216060820c629bc4f186813bcbfb71e5a10d63fcacbd354181f": 1,
    "d20004891b6baada0c141972fc179712dfbfa0adcfe16288ba078164be954439": 1,
}


def detect_public_addresses(text: str) -> tuple[str, ...]:
    """Return the public global-unicast IPv4 values present in ``text``.

    Behavior identical to address_occurrence_diff.py: regex-escaped forms
    count as their plain canonical value, invalid octets are dropped, and
    documentation nets, private and non-global ranges are excluded.
    """
    found: list[str] = []
    for match in FIND.finditer(text):
        octets = [int(group) for group in match.groups()]
        if any(octet > 255 for octet in octets):
            continue
        address = ipaddress.ip_address(".".join(str(octet) for octet in octets))
        if not address.is_global or any(address in net for net in DOC_NETS):
            continue
        found.append(str(address))
    return tuple(found)


def count_map_from_grep_output(output: str) -> dict[str, dict[str, int]]:
    """Fold ``git grep -a -n`` working-tree output into per-file digest counts."""
    per_file: dict[str, dict[str, int]] = {}
    for line in output.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        path, _number, text = parts
        counts = per_file.setdefault(path, {})
        for address in detect_public_addresses(text):
            digest = hashlib.sha256(address.encode("ascii")).hexdigest()
            counts[digest] = counts.get(digest, 0) + 1
    return per_file


def scan_repository(repo_root: Path) -> dict[str, dict[str, int]]:
    """Scan tracked plus untracked (non-ignored) files of ``repo_root``.

    ``-a`` keeps ``-text``-marked archives scannable; ``--untracked`` makes
    the stock identical before and after new files are committed.
    """
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "grep",
            "-a",
            "-nE",
            "--untracked",
            GREP_PATTERN,
            "--",
            ".",
        ],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if result.returncode not in (0, 1):
        raise AssertionError(
            f"git grep failed with exit {result.returncode}: {result.stderr.strip()}"
        )
    return count_map_from_grep_output(result.stdout)


def compare_count_maps(
    actual: dict[str, dict[str, int]],
    frozen: dict[str, dict[str, int]],
) -> list[str]:
    """Compare a scanned stock against a frozen allowlist.

    Returns one masked problem line per (path, digest) mismatch: path,
    sha256 and expected/actual counts only. An added occurrence inside an
    already-allowlisted file fails on count, an occurrence in any other file
    fails on path, and an unexplained removal fails on count too.
    """
    problems: list[str] = []
    for path in sorted(set(actual) | set(frozen)):
        actual_counts = actual.get(path, {})
        frozen_counts = frozen.get(path, {})
        if path not in frozen:
            for digest, count in sorted(actual_counts.items()):
                problems.append(
                    f"{path}: path not in allowlist: "
                    f"sha256 {digest}: expected 0, found {count}"
                )
            continue
        for digest in sorted(set(actual_counts) | set(frozen_counts)):
            expected = frozen_counts.get(digest, 0)
            found = actual_counts.get(digest, 0)
            if expected != found:
                problems.append(
                    f"{path}: sha256 {digest}: expected {expected}, found {found}"
                )
    return problems


def _quad(*octets: int) -> str:
    return ".".join(str(octet) for octet in octets)


def _escaped(quad: str) -> str:
    return ("\\" + ".").join(quad.split("."))


def _digest_of(address: str) -> str:
    return hashlib.sha256(address.encode("ascii")).hexdigest()


def _frozen_digests() -> set[str]:
    return {
        digest for counts in FROZEN_ALLOWLIST.values() for digest in counts
    }


def test_t1_detector_flags_only_global_unicast_samples() -> None:
    flagged = (
        _quad(93, 184, 216, 34),
        _quad(1, 2, 3, 4),
        _escaped(_quad(93, 184, 216, 34)),
    )
    for sample in flagged:
        canonical = sample.replace("\\", "")
        assert detect_public_addresses(sample) == (canonical,)

    not_flagged = (
        _quad(192, 0, 2, 1),
        _quad(198, 51, 100, 7),
        _quad(203, 0, 113, 9),
        _quad(10, 1, 2, 3),
        _quad(172, 16, 5, 5),
        _quad(127, 0, 0, 1),
        _quad(300, 1, 2, 3),
        _quad(1, 2, 3, 4) + ".5",
        "." + _quad(93, 184, 216, 34),
        "9" + _quad(93, 184, 216, 34),
    )
    for sample in not_flagged:
        assert detect_public_addresses(sample) == ()


def test_t2_tracked_public_address_stock_equals_frozen_allowlist() -> None:
    totals: dict[str, int] = {}
    for counts in FROZEN_ALLOWLIST.values():
        for digest, count in counts.items():
            totals[digest] = totals.get(digest, 0) + count
    assert sum(totals.values()) == FROZEN_TOTAL_OCCURRENCES
    assert totals == FROZEN_PER_DIGEST_TOTALS

    actual = scan_repository(REPO_ROOT)
    problems = compare_count_maps(actual, FROZEN_ALLOWLIST)
    assert not problems, (
        "tracked public-address stock drifted from the frozen allowlist:\n"
        + "\n".join(problems)
    )


def test_t2_fault_injection_duplicate_count_inside_allowlisted_file() -> None:
    synthetic = _quad(93, 184, 216, 34)
    digest = _digest_of(synthetic)
    assert digest not in _frozen_digests()
    grep_output = (
        f"DELIVERY_PLAN.md:3008: resumes on ubuntu@{synthetic} per plan\n"
        f"DELIVERY_PLAN.md:3010: regex form {_escaped(synthetic)} too\n"
    )
    actual = count_map_from_grep_output(grep_output)
    assert actual == {"DELIVERY_PLAN.md": {digest: 2}}
    problems = compare_count_maps(actual, {"DELIVERY_PLAN.md": {digest: 1}})
    assert problems == [
        f"DELIVERY_PLAN.md: sha256 {digest}: expected 1, found 2"
    ]


def test_t2_fault_injection_occurrence_in_unknown_path() -> None:
    synthetic = _quad(1, 2, 3, 4)
    digest = _digest_of(synthetic)
    assert digest not in _frozen_digests()
    grep_output = f"docs/notes/new-note.md:7: egress via {synthetic}\n"
    actual = count_map_from_grep_output(grep_output)
    assert actual == {"docs/notes/new-note.md": {digest: 1}}
    problems = compare_count_maps(actual, {})
    assert problems == [
        f"docs/notes/new-note.md: path not in allowlist: "
        f"sha256 {digest}: expected 0, found 1"
    ]


def test_t2_fault_injection_unexplained_removal() -> None:
    digest = "ab" * 32
    problems = compare_count_maps({}, {"docs/handoffs/l8-l9-status.md": {digest: 2}})
    assert problems == [
        f"docs/handoffs/l8-l9-status.md: sha256 {digest}: expected 2, found 0"
    ]


def test_t3_remediated_paths_and_this_file_contain_no_address_text() -> None:
    for relative in REMEDIATED_PATHS:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8", errors="replace")
        assert detect_public_addresses(text) == (), relative

    own_source = Path(__file__).read_text(encoding="utf-8")
    assert list(FIND.finditer(own_source)) == []
