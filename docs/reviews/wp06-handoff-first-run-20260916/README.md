# PR #42: final WP-06 handoff-test evidence review

Evidence-only retention. This is NOT a fix or closure of the original intermittent
PowerShell 5.1 startup/pipe timeout, a new application feature, or a V1 release.

## Fixed delivered identity

- Base main (PR #40): `f331d71d76d116aabee3661e73a9621d52e3a1d6`.
- Initial candidate: `f3537b27c8e1e9515ba4656c44825f05bf4f3a52`.
- Final reviewed head: `15c920a394e8462b19abe8bf143a149dfb81b24a`.
- Final tested merge candidate: `10e782ab9be5d37087fe2902e53aae1b67d53cc1`.
- Actual merged main: `0c438098bab4d7c0a4f626664af43a31d96d8b08`.
- Actual merged/tested tree: `bc475318487da14d25a289130bca97f7f0e1743a`.
- Separate same-assistant reviews: 5212999934, 5213357121 and 5213564071.

The actual merge tree was read back and equals the reviewed/tested tree. No
separately completed post-merge full run is claimed. Local reconstructed Git
commits are bookkeeping, not upstream commit identities.

## Scope and preserved behavior

Fixed stderr stages and timings now surround the EXISTING synthetic handoff test:
script entry, setup/import, mocked manifest/payload handling, helper return and
serialization. Hash tests also verify their original exact bytes and released
handle. The production downloader is byte-identical. No actual handoff HTTP or
model request occurs in these fixtures.

The summary has bounded input, a fixed stage vocabulary and no arbitrary stderr,
stdout, command arguments, environment values or raw payloads. It accepts bytes
or text on TimeoutExpired, preserves primary process exceptions and never changes
a nonzero exit/timeout into success. Raw pytest output is not thereby certified
safe for arbitrary private-data use; this helper is for synthetic tests only.
Missing markers and final markers do not establish causality or overrule failure.

All original 21 assertion nodes and 18 Windows test identities remain. The original
30-second process contract, native 20-minute job limit, pinned actions, permissions,
selection and security settings remain. Required missing shells fail rather than
silently skip. Three independent runner trials are declared BEFORE execution;
each begins with the original success-powershell.exe test, then exercises the new
contracts and a deliberate 30-second timeout. That injected wait is NOT reproduction
or retry of PR #30's intermittent failure. Runner provisioning may already have
used PowerShell; only first test-controlled invocation is claimed.

Production runtime code, all 67 SQL migrations, their manifest and dependencies
are unchanged.
All 2902 protected payload hashes matched baseline; the plan is append-only. All
nine changed paths are outside the existing packaged-source selector. No new kit
was built or accepted for this revision, and no user installation was overwritten.

## Separate self-review and first-failure preservation

The five-case adversarial review first produced 2 failures / 3 passes: errors from
the JUnit or stdout diagnostic sink replaced the original TimeoutExpired. The fix
retains the same primary exception and adds only fixed unavailable-diagnostic
notes. The failing assertions and original RED log are retained unchanged.

Initial candidate CI fully passed, but its native report had 18 record_property /
xunit2 compatibility warnings (1756 passed / 2 skipped in 1132.65s). Those properties
were readable; strict-xunit2 conformity was not established. The final commit adds
ONLY `-o junit_family=legacy` to the native command and nine runbook/reference lines.
It selects a compatible report family, not a warning filter. All test bytes and
runtime files are identical between candidates. Final native case IDs are identical,
all 18 traces remain, and the compatibility warnings are absent.

Final detached worktree: 25 passed / 19 Windows skips in 1.82s, clean. Exact final
local full run: 37829 passed / 41 skipped in 380.56s. Local Python 3.13.5/preinstalled
tooling is distinct from hosted locked/Windows results. Earlier preparation and
initial-candidate full logs remain separate; the pre-final run with 40 skips is
NOT final acceptance. Initial default-xunit2 local warnings are also retained.
A disposable editable-environment build-tool lookup failed and was repaired only
in that environment; project dependencies were not modified. A later container
wait-and-hash-recheck command timed out as a tooling event; its recheck was not
claimed complete. A separate immediate hash check passed without source changes
or product/CI reruns.

Direct clone was unavailable. Read-only helper run34994547707/artifact10407082874
provided fixed f331 source; 6434 blobs matched the upstream tree. Public blank
example files were never enabled; no real .env or user credentials were read.
All final nine source hashes and independently reconstructed full tree matched.

## Actual final-head acceptance

| Gate | Actual result | Run / job |
| --- | --- | --- |
| Locked full offline | 37829 passed, 41 skipped, 336.70s | 34999498604 / 104484077469 |
| Native Windows regression | 1756 passed, 2 skipped in 986.78s (0:16:26) | 34999498487 / 104484077115 |
| Existing paper/native regression | 339 passed, zero skips/errors, 408.02s | 34999498495 / 104484077072 |
| First-invocation trial 1 | 26 passed, zero skips/errors | 34999498597 / 104484077186 |
| First-invocation trial 2 | 26 passed, zero skips/errors | 34999498597 / 104484077748 |
| First-invocation trial 3 | 26 passed, zero skips/errors | 34999498597 / 104484077604 |

All four workflows and all six jobs completed successfully, including tracked-source
cleanliness. Original logs/JUnit, ZIP CRC and payload hashes were inspected, not
only badges. The final paper suite has the same 339 test IDs as the first candidate.
All 25 new cases ran on Windows (19 unit, 5 adversarial, 1 deliberate negative
process timeout). They repeat across trials and MUST NOT be summed as distinct
coverage. All 18 existing Windows handoff cases ran in native CI; its first PS5
process took 4.141s.

| Trial | Initial candidate first process | Final candidate first process | Final intentional timeout |
| --- | --- | --- | --- |
| 1 | 6.063s | 20.047s | 30.031s |
| 2 | 3.797s | 10.438s | 30.016s |
| 3 | 19.391s | 3.531s | 30.015s |

All success traces contain the complete 11 expected stages. Controlled timeout
traces preserve script_entered while TimeoutExpired is still raised. Final trial1
serialized at child elapsed3.874s versus parent elapsed20.047s; the difference
cannot distinguish startup from exit/pipe draining or establish a root cause.
All initial and final trials remain retained, not selected by speed or success.
There was no manual CI retry, prewarm, timeout extension or relaxed assertion.
Instrumentation itself may perturb timing. The original intermittent fault was
NOT reproduced; three or six passes cannot close it.

The 41 offline skips comprise 18 old Windows shell cases, one new Windows negative
timeout case, 18 native opt-ins and four older optional database cases. Of native
opt-ins, 13 ran in native CI and four in paper CI; one extracted-kit opt-in did
NOT run because package inputs did not change. Thus five optional checks remain
unexecuted (kit plus four older database tests). The two native Windows skips need
privileged symlinks. These overlapping test sets must not be added together.

## Retention and remaining gates

This directory has eight files: this report, original RED, final detached log,
final source manifest, both three-trial summaries, report-format review and raw
checksums. Complete first/final local and CI logs/JUnit and audit metadata are
supplementary in conversation `pr42-review-evidence.zip`, with a checked payload
manifest and ZIP CRC. Its exact size/hash is recorded in the delivery comment.
Do not claim the repository directory contains every raw CI byte. Actions
artifacts have seven-day retention and are not a permanent Release. No runtime
binaries, fonts, database, credentials or implementation patch is in the ZIP.

No unresolved blocking finding was identified in THIS test-evidence change's
separate self-review and actual tests. Same assistant, not fresh-agent/third-party
audit or an absolute defect-free guarantee. WP-06 remains PARTIAL, G6 open, V1 1/6.
PR #30's underlying reliability issue, D1-D3 and real input/fee/business acceptance
remain open. No user-machine task, key request or user database change is needed.
