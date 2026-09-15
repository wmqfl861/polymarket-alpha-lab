# PR #32 final operator-entry review record

This is an evidence-only retention branch. It does not modify main or any runtime source.

## Fixed delivered identity

- Base: `038be0c0df91999e6038e8fefe2830022b2780fb`.
- Reviewed head: `91287cbc75e6617dddd619ad96fc6bc90ed64518`.
- Reviewed/tested tree: `1c0a3412df054985c9c0efe99025807832ca9615`.
- Tested PR merge candidate: `efb8d0772171d5d2a9b91a912b7d7b292d14b284`.
- Actual merged main: `62a6337d0484d3dd2cc457a6b2d468282a33204e`.
- Actual merge tree was read back and equals the tested tree.
- Separate same-assistant reviews: `5205061312`, `5205112961` on PR #32.

## Delivered scope

One controlled entry composes existing project PostgreSQL batch, turn and allowance APIs. It supports original-record inspection and one explicit budgeted rotation through an application-supplied reviewed factory. Standalone run-turn intentionally refuses before database access because no provider client is configured. Stop is cooperative; replay executes nothing; incomplete or captured failures are never automatically rerun. Output follows managed cleanup and does not publish raw evidence, model text or pending_run.

Ten production-PR paths changed, 875 additions / 7 deletions. No changes to the 66 SQL migrations, dependencies, existing claim/capture/rotation/budget algorithms, role permissions, client I/O limits or user installations.

## Separate self-review

64 implementation cases plus 12 separately designed adversarial cases passed in a separate worktree (76 passed in 2.46s, clean afterward). The local Python 3.13.5 workspace was a verified source subset, not a full repository clone or locked/native test environment. Exact changed-file hashes bind it to the final upstream tree; full and native results below come from actual CI.

The first adversarial run was 6 failed / 6 passed. SystemExit(0) and exception text could escape from operation, cleanup or real executor-worker boundaries. The operator now returns sanitized failure. The original failing assertions and RED output are retained; no assertion was weakened. Final review also fixed missing distribution-workflow triggers for operator-script-only and review-test-only changes, without changing runtime code, timeouts, pinned actions or permissions.

The final scope review found no unresolved blocking defect. This is not a fresh-agent/third-party audit or an assurance of zero defects.

## Actual final-head gates

| Gate | Actual result | Run | Job |
| --- | --- | --- | --- |
| Locked full offline verification | 37351 passed, 35 skipped, 414.27s; installation, entrypoints, compilation and cleanliness passed | 34922876592 | 104234671059 |
| Windows native PostgreSQL | 1615 passed, 2 skipped, 902.88s; cleanliness passed | 34922876644 | 104234649179 |
| Windows actual kit build/extract/start | 96 passed, 0 skipped, 173.59s; cleanliness passed | 34922876699 | 104234623529 |

All 77 new cases actually executed on Windows: 64 unit, 12 adversarial and one real private-PostgreSQL operator scenario. None failed, errored or skipped. The native scenario covers two-team rounds, stop/restart, saved factory failure, child exit86 after claim and allowance commit, replay without client work, retained incomplete history, no refund/reclaim and preservation of prior records. Provider clients and business inputs are synthetic.

The 35 offline skips comprise 18 Windows-shell cases (executed in native CI), 13 native opt-ins (12 executed in native CI and one in kit CI), and four older optional database cases not executed. Two Windows skips require privileged symlinks. Test selections overlap; totals must not be added.

A supplementary local aggregation assertion was corrected to distinguish 19 total handoff-module cases from 18 shell cases plus one static check. This did not change product tests or CI output. Original JUnit remains in the full evidence package.

The superseded first candidate's offline and native runs were cancelled by normal new-revision concurrency; its successful kit is not final acceptance evidence. No manual retry, prewarming or timeout increase was used.

## Evidence locations and integrity

This branch retains the summary and raw-file checksums, not the raw ZIP bytes. The complete 20-member evidence ZIP was delivered in the development conversation as `pr32-review-evidence-deflate.zip`: 48489 bytes, SHA256 `d82c2e1be4481acc618671b1e071bc426c9e3e10d370f4a05b22b2211af3a9fe`. Its CRC and all 19 payload hashes against the embedded manifest were checked. It contains original RED/final logs, JUnit, source bindings and CI/package audit metadata. No engine binaries, fonts, credentials or user database are included.

Original CI artifacts: offline `10378873301`; native `10378854220`; distribution proof `10378643494`; Windows kit `10379071049`. These CI artifacts have seven-day retention, not a permanent Release. The PR retains their exact counts and hashes. An attempted supplementary binary-blob transcription did not match its expected Git hash and was left unreferenced; no corrupt archive was committed or linked.

Actual kit: 66731360 inner-ZIP bytes, SHA256 `ef27af9d76bb8d9aeed7b99aa2e9b45a4f3277406606c3235bad7a2583c55ff1`. All 2918 manifest hashes and 2917 source payloads were checked against the reviewed source. The untouched old kit's 2916 entries also verify. Source-only container smoke tests are not represented as Windows engine execution.

## Remaining product gates

WP-03 remains PARTIAL, WP-02 AWAITING_OWNER, V1 1/6. D1 provider/model, D2 input-send permission and D3 executable budget remain open. No verified real-provider charge bound or real-model acceptance is claimed. The PR #30 PowerShell 5.1 first-run reliability root cause remains open despite this run passing. No user-machine task, credential request, user database change, installed-kit overlay, live order or long-term Release is required or performed here.
