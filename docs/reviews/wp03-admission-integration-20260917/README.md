# PR47: reviewed admission integrated with shared stop and managed draining

## Delivered identity

- Previous main: `2a0e881a2057ce6cba33c1a6885dcd099d4a5b52` (PR48/50/51).
- Original PR47: `f1d9f68d08fdb8a0453319e885c3827296d8d694`.
- Reviewed head: `11f7540bfe556c6243b03e64d7246c3c1312576b`.
- Tested merge candidate: `75800a0811fadbfd02be95cf9fbd52298e214678`.
- Actual merge: `41cc131d154a8a134d46608f5c5da1ca1e4ddaa6`.
- Reviewed/tested/merged tree: `f6d283f47ed2174bbf0d5567361c7c0e6e55d7e6`.
- Separate same-assistant reviews: 5229885260 and 5229945907.

The actual merge tree and parents were read back. Both original histories were
preserved; no force-push, post-test source change or separate post-merge full run
is claimed. Eleven feature paths, 698 additions and 23 deletions.

## Operator path and boundaries

The existing task console now admits one reviewed batch or budget through
`enqueue-batch` / `create-budget`. Each requires the original identifier, reviewed
canonical-input SHA256, its own explicit write flag and bounded binary stdin.
Original codecs/stores, 8MiB/32KiB bounds and optional one-line framing remain.
No permission means no input read; invalid input/hash/ID means no managed DB access.

Admission stores immutable reviewed input; it does not claim tasks, reserve calls,
run models or replenish quotas. Same-input replay preserves the first receipt;
changed input conflicts. Batch and budget are TWO independent writes, not an
atomic pair. Error or output failure is not proof of rollback. Inspect original
IDs and explicitly replay only the same input; no automatic business retry.

Integration preserves main's checked emitter/shared stop and session draining.
Output interruption reaches the original stop token. Input interruption requests
stop BEFORE publishing its error so another output failure cannot erase the stop.
Ordinary output error alone does not cancel related work. Producer must close
stdin; a byte cap is not a universal pipe deadline or receiver acknowledgment.

Only the original task CLI changes in production. Existing main inspection/run/
stop-wrapper functions and original PR47 input/admission functions match by AST.
All 67 migrations, dependencies and 2921 other protected payloads are unchanged.
All 89 main and 85 old-PR47 outer kit assertions remain; 90 now. Original admission
tests remain exact prefixes. Final old-PR47 recipe is byte-identical: no changed
input, cutoff, clock, order or deadline in this integration. Native three-part
inventory and required aggregate remain, with only two admission modules explicitly
added to dispatch and kit and an exact closed-inventory update. No old test,
permission, pinned action or timeout was weakened.

## Separate review and preserved first failures

Correct assembly baseline: 229 passed. New output-stop cases first gave 8 failed /
4 passed; early returns were changed to reuse main's stop-aware output path.
Separate input/output combination review first gave 8 failed / 16 passed; requesting
stop before the input-error receipt fixed it. Original failing assertions and
both RED logs are retained. There are 36 new integration cases, supplementing the
original 102 admission cases.

Final related: 323 passed in 9.11s. Separate detached Git worktree: 323 passed in
10.23s, with actual import origin checked. Exact full local verification: 38198
passed / 42 skipped in 429.17s, exit0 and full PASS. Local Linux Python3.13.5 with
preinstalled tooling is distinct from hosted locked/Windows acceptance.

An earlier assembly script wrongly expected the whole current plan to equal the
common prefix, stopped early, and a subsequent command mistakenly tested the
INCOMPLETE assembly: 102 failed / 127 passed. This is a retained assembly/tooling
event, not 102 product defects. Corrected preflight assembles all files before
execution. These results are separate from the two genuine integration REDs.

Original PR47's first kit failure (246 passed / 1 failed) and later old-head
247-case green report were downloaded and retained. The old generic nested failure
cause remains unproven. The old final test revision moved verification-only
processes after prospective captures, not original clocks/deadlines. Those old
results do not replace acceptance of this combined tree.

## Actual final-head acceptance

| Gate | Actual result | Run / job |
| --- | --- | --- |
| Locked offline | 38198 passed, 42 skipped, 424.95s | 35168861072 / 105035943744 |
| Native storage | 290 passed, 2 skipped, 367.94s | 35168861115 / 105035944109 |
| Native research | 1059 passed, zero skips, 362.68s | 35168861115 / 105035943780 |
| Native dispatch | 692 passed, zero skips, 440.94s | 35168861115 / 105035944102 |
| Native aggregate | SUCCESS | 35168861115 / 105037545935 |
| Paper/native | 339 passed, zero skips, 389.09s | 35168861080 / 105035943833 |
| Actual kit | 319 passed, zero skips, 490.11s | 35168861081 / 105035943758 |

All four workflows/seven jobs succeeded. Six test jobs completed source-cleanliness
checks. Actual downloaded raw logs/JUnit, ZIP CRC/hashes and case-ID multisets were
checked. Native2043 IDs = 1905 actual-main IDs + 138 admission IDs, without omissions
or duplicates. Kit319 IDs = 181 actual-main + 138 admission; all247 old-PR47 IDs
remain. Paper339 IDs unchanged. All138 admission cases passed on Windows in both
dispatch and kit; repeated/overlapping tests are not extra distinct coverage.

The actual435.783s existing kit case includes prior lifecycle, admission recipe
and drain proof. It returned three admission receipts, four attempts/simulations,
two settlements, seven original reservations, one incomplete claim/unrefunded
reservation,186kit-origin modules and exact committed-confirmation replay after
failed output. Prior absence, no claims/reservations before execution, spent-policy
replay/conflict and owned/borrowed engine drain assertions passed. Real PostgreSQL
lifecycle, but synthetic inputs/models/reviewer assertions/costs. Drain interruption
is test injection, not a real OS signal. This is not account P&L or human acceptance.

Five optional checks remain unexecuted: dedicated PR42 Windows intentional timeout
and four older DB opt-ins. Native two skips need privileged symlinks. All18 original
handoff traces returned (firstPS5 success3.688s), but this neither waives PR49's
failed tree nor establishes a fix of its cause. No manual CI rerun or prewarm.

## Package and retention

Actual kit artifact10475843478 was independently downloaded. Inner ZIP66796806
bytes, SHA256 `b69d2da54d23d226b79151de1f77cc51eab9b9edb05ff4dabdfa25973ef8536f`.
All2929 manifest hashes/2928 source payloads equal reviewed bytes. PostgreSQL17.11
seed1753 entries checked for excluded font/cluster/passfile paths. No engine was
executed or installed in the container. This is not a deployed user kit or Release.

Pinned source archives35167229633/artifact10474979485 and35167490019/artifact10475044700
verified6456main/6452old47/6450common blobs and exact upstream commit objects.
Git DNS failed; CodeGraph was unavailable and not run. A fixed1671byte text-only
reconstruction script and18796byte integration patch were read back and applied
under start/end tree guards by transport35168596212. Only nine non-workflow paths
were published to a NEW review ref, without application execution or direct
main/feature/workflow-file mutation. Git-data added two reviewed workflow blobs.
Helper material is outside the feature; no blocked CAPI experiment or platform
refusal was routed around. Public examples were never enabled; no user secrets read.

This evidence-only directory contains eight report/metadata/selected-log files.
Complete local RED/final and final CI logs/JUnit plus old-PR47 first-failure evidence
are supplementary in `pr47-integration-review-evidence.zip`, with checked payload
manifest and CRC. Its exact outer size/hash is recorded in the delivery comment.
Do not claim every raw log is in the repository directory. Actions artifacts expire
after seven days. No runtime/font binaries, credentials, user database or executable
handoff patch is included in the supplementary ZIP. No local-agent task is implied.

No unresolved blocking finding was identified in this scoped separate self-review
and actual tests. Same assistant, not third-party/fresh-agent audit or absolute
no-defect assurance. WP02 remains AWAITING_OWNER; WP03/WP06 PARTIAL; G2/G3/G6 and V1
remain open (V1 1/6). D1-D3, real input/fee/business acceptance, safe user-version
changes and PR49's first-invocation failure remain open. No user-machine/database,
credential, actual provider/market/order or old-installation operation occurred.
