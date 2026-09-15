# PR #40 final operator-entry review and delivery

Evidence-only retention. This is not a runtime patch or a user-machine task.

## Scope and fixed candidate

Base: `747faeaa8a5d60776261d80941e061b80af0f9b4` (PR #39).
Reviewed head: `5f232e5d2b7beac220a11dc951dcc186917356a6`.
Reviewed tree: `7da52033f4c6efd91efc3363a3d7f4a7169fef94`.
Tested PR merge candidate: `2e8a1434cf917591759e72cc60557e0fe940831c`.
Actual merged main: `f331d71d76d116aabee3661e73a9621d52e3a1d6`.
Actual merge tree readback equals the reviewed/tested tree above.
Separate same-assistant reviews: 5211675284 and final gate addendum5211876448.

The existing task command now exposes capture-paper and inspect-paper through
existing immutable native PostgreSQL APIs. Capture requires original record ID,
explicit canonical-input SHA256 and write opt-in. Read bounded binary stdin through
EOF, permit only one optional LF/CRLF terminator, and reject hash/identity/encoding
mismatch before project access. Inspection consumes no stdin or business write.

Stored receipts must match original identity and, for capture, the complete input.
Successful storage includes saved rejection and exact replay; it is never trade
approval. Error/cleanup/output failure may follow a committed write. Output is
metadata only after managed cleanup; short writes/failed flushes return nonzero
without trying a second envelope. No automatic retry or regeneration of dates,
input hashes, model approvals or task identities.

Eight paths, 690 additions/2 deletions. All 67 migrations and manifest, dependencies,
roles, storage/transaction/simulation/settlement algorithms are unchanged. Existing
run/inspect helpers and old main execution path retain identical AST. The paper
workflow only appends three test modules, preserving all former selections,
diagnostic plugin, deadlines and permissions. PR39's settled-paper command is
reused, not reimplemented. Root plan still marks WP05/WP06 PARTIAL, G5/G6 open,
V1 1/6. There was no real provider/public-source request, credential discovery,
user database operation, kit overlay, live order or Release.

## Separate self-review and retained first failure

52 implementation cases passed. Separately designed 17 adversarial cases first
returned 1 failed/16 passed. An unknown operation supplied directly to the helper
was echoed in its error envelope. Argparse already blocked unknown CLI commands;
this is a helper-boundary disclosure, not observed database corruption. Unknown
operation now emits null, with the original failing assertion unchanged. Original
RED output is retained. A bytearray also replaces a per-chunk list to avoid
metadata overhead for pathological short reads; no invented failure is claimed.

Final detached worktree: 69 passed/1 native opt-in skipped in 2.31s, clean.
Related regression: 200 passed/1 native skipped in 4.51s. Full local verification:
37805 passed/40 skipped in 309.47s; install, entrypoints and compilation passed.
Local Python3.13.5/preinstalled dependencies are NOT locked hosted/native evidence.
A fixed-source archive verified6430 baseline blobs after direct clone DNS failure.
Local Git commits are reconstruction bookkeeping, not upstream identities. All
8 uploaded blobs and the independently reconstructed full upstream tree match.
6426 unchanged baseline files, including67SQL, were checked; only the unrelated
.codegraph/.gitignore was absent from the local archive. Both worktrees remained
clean. Initial editable-install backend-path failure and unsupported interactive
prelaunch are tooling events, not failed product tests. Only the pinned blank
public .env.example exists; no actual .env or credentials were read or enabled.

Supplementary finite stream probe:72 canonical binary roundtrips under randomized
short reads,72 framing rejections, and actual4MiB overflow stopping after exactly
4194307bytes (cap+CRLF+one overflow byte). These are not additional pytest/native
counts. Finite authored-file credential-pattern scan found no matches; it is not
a guarantee of detecting every possible secret.

## All four final-head gates completed successfully

Offline34984096819/job104431632134:37805 passed/40 skipped275.09s; cleanliness PASS.
Paper34984096893/job104431631941:339 passed/0skips/errors432.53s; cleanliness PASS.
Kit34984096840/job104431631156:96 passed/0skips/errors214.12s; cleanliness PASS.
Native34984096962/job104431643677:1756 passed/2skipped/0errors1077.99s; cleanliness PASS.
Actual logs/JUnit, whole-workflow conclusions and cleanliness steps were checked.
40offline skips=18Windows shell(executed native)+18native opt-ins(13old-native,
4paper,1kit)+4older optionalDB cases NOT executed. Two Windows skips require
privileged symlinks. Static cases in platform-named modules are not extra opt-ins.
Selections overlap; do not sum totals. Raw logs retain2native/1paper/1kit diagnostic
stack dumps and expected negative-test stderr, not failed timeouts. No manual CI
rerun, prewarm, increased deadline or weakened assertion. No post-merge full rerun
is claimed; the actual merge has the exact already-tested source tree.

All70new cases executed in paper/native CI (52unit+17review+1native), with exact
JUnit test-ID multiset matching the local suite. The54.199s new native case invokes
actual child commands outside the parent lifecycle lease and proves BTC/ETH
ready/rejected saves, missing approval, missing record, replay, changed-quantity
conflict, restart/readback and unchanged originals. Inputs/models are synthetic.
This is binary child stdin includingCRLF, not interactive PowerShell paste testing.

Kit artifact10402887533 inner66776667bytes SHA256
94a4cc8819285ea7c947f6bbd0607c7d44cff847a2d700ae1be45bcc8cf59ea8.
All2928manifest hashes/2927source payloads match the reviewed source. PG17.11 seed
1753entries checked for excluded fonts/cluster/passfile paths. Container archive
inspection executes no engine. Existing extracted-kit lifecycle proof does not
itself execute the new command; the separate paper-native case does.

## Limits and operator notes

Canonical input preparation and human approval remain external prerequisites.
Hashes bind bytes, not source or reviewer identity. The manual Read-Host example
is host-length-limited, not a universal4MiB paste facility; larger payloads use the
approved producer's binary stdin. Truncated text fails the original hash before
DB access. Never generate a replacement hash merely to accept changed input.
Byte bounds also do not impose a universal deadline on a stalled input producer.

Exit0 means a receipt returned, not trading success. Exit1 may follow a committed
write, exit2 is invalid input/approval, exit3 is one missing receipt, and130 is
interruption. Existing prospective-admission, source/fee and historical-data limits
remain unchanged. D1-D3 and the older PS5.1 first-run reliability issue remain open.

No unresolved blocking finding was identified in this scoped separate self-review
and actual tests. This is SAME-ASSISTANT review, not an external/fresh-agent audit,
source authentication or zero-defect guarantee. WP05/WP06 remain PARTIAL, G5/G6 open,
V1 1/6. Real input/fee/operator acceptance and D1-D3 remain required. No local user
task or credentials are needed for this delivery.

## Retained evidence

This directory retains the final report, original RED, final detached log, exact
source manifest, final gate metadata and raw-file checksums. Complete first/final
local and CI logs/JUnit and supplemental probes are in conversation
`pr40-review-evidence.zip`; its size/hash and validated payload count are recorded
in the final PR delivery comment. Full raw CI bytes are not falsely claimed as
files in this repository directory. Actions artifacts10402816822(offline),
10402604329(native),10403141639(paper) and10403166818(kitproof) have seven-day
retention, not a permanent Release. Evidence ZIP excludes runtime binaries, fonts,
credentials, user databases and implementation patches. This is not a local-agent
execution handoff. An optional raw-job metadata URL read was endpoint-rejected;
no alternate raw URL was attempted and dedicated status tools supplied acceptance.
