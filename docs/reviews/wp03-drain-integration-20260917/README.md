# PR50: lifecycle drain and checked task receipts integrated

## Delivered version

- Previous main / PR51: `839d4039406d1113676e0b474680e6d4061f289b`.
- Original PR50: `668c8eea2c6f740561afdfdd5368849ee58b4624`.
- Reviewed head: `772bef3d2507a061fa2834323c13d7519dc42cd3`.
- Tested merge candidate: `b31a7d20fe83a98ee328e926d38ffa9f4c145b68`.
- Actual merge: `2a0e881a2057ce6cba33c1a6885dcd099d4a5b52`.
- Reviewed/tested/merged tree: `ee504d7967f56c5ae6411333ab8d46247e613357`.
- Separate same-assistant reviews: 5229601685 and 5229665525.

Actual merge parents/tree were read back and match the reviewed integration.
Both histories are preserved, without force-push. No separately completed
post-merge full test run is claimed. Eight paths,607additions/4deletions.

## Scope and behavior

The original PR50 fix retains the lifecycle lease while resuming the SAME
idempotent close after KeyboardInterrupt/SystemExit. Already-admitted work drains;
new admissions remain sealed. Only an engine started by that session is stopped,
once. A borrowed engine stays running. The first interruption then propagates;
a stop failure remains primary with the interruption chained as its cause.
No model, SQL, claim, reservation or engine operation is retried.

PR51's checked output and shared-stop code remains byte-identical. Receipts are
published only after this managed cleanup. Close interruption and subsequent
short/interrupted output retain their distinct nonzero/stop behavior without
rerunning the original task, refunding a reservation or inferring rollback.

The production fixes and original PR50 native recipe were not rewritten during
integration. Only both append-only document histories and twelve new combination
cases were added. All67SQL/dependencies/2921other protected payloads remain;
all84prior-main kit assertion nodes are retained,89final. Native three-part
inventory and existing limits/actions/permissions are unchanged.

This is cooperative draining, not hard cancellation or arbitrary OS-signal safety.
Hung clients still require bounded I/O. Unexpected ordinary close errors are not
retried or certified recovered. No security policy, provider or user installation
is changed. PR49 and the denied CAPI experiment remain separate and untouched.

## Separate self-review and local tests

The twelve new owned/borrowed x closeKBI/zeroExit x normal/short/interrupted-output
cases first failed on PR51 main without the drain fix:12failed/14deselected2.29s.
The same assertions pass integrated. Actual threads/Condition/session/CLI/emitter
are used; native calls and inspection payload are fixtures. No additional failing
production finding was identified in the separate integration review.

Focused150passed16.19s; related423passed20.28s. Separate detached423passed20.06s
with complete JUnit and imports verified before/after. That compound shell wrapper
returned1 without a test failure/diagnostic; its shell exit0 is NOT claimed.
Separate Git cleanliness checks returned0. A direct-interpreter detached36case run
recorded pytest_return0/process0 in1.40s, with origins checked.
Exact full local38060passed/42skipped539.40s, verificationPASS/exit0; total runner
elapsed557.94s. These are LinuxPython3.13.5/preinstalled-tool results, distinct
from locked hosted Windows acceptance. No source change after the frozen tree.

## Actual final-head hosted acceptance

| Gate | Result | Run / job |
| --- | --- | --- |
| Locked offline |38060passed,42skipped,436.54s|35164891024 /105023664533|
| Native storage |290passed,2skipped,392.61s|35164890798 /105023663672|
| Native research |1059passed,0skips,341.97s|35164890798 /105023663467|
| Native dispatch |554passed,0skips,440.50s|35164890798 /105023663659|
| Native aggregate |SUCCESS|35164890798 /105025443595|
| Paper/native |339passed,0skips,387.85s|35164890982 /105023664116|
| Actual kit |181passed,0skips,478.90s|35164890794 /105023663609|

All four workflows/seven jobs succeeded; all six test-job source-cleanliness
checks passed. Actual raw logs/JUnit/CRC/hashes and case-ID sets were inspected.
Native1905IDs and paper339IDs match PR51 exactly. Kit181 retains145main IDs plus
36drain/integration IDs, all36executed on Windows. All169oldPR50 IDs remain.
The12added combinations and original24drain cases are not additional separate
native engine runs. Overlapping workflow counts must not be summed.

The419.472s existing kit case includes prior lifecycle/composition AND the real
owned/borrowed drain recipe. A controlled Python exception only in Condition.wait
leaves the real OS lease busy, admissions closed, one original PostgreSQL read
usable and the original record/error preserved. No DB/lifecycle mock or OS signal
in this recipe. Owned/borrowed final states pass. The prior packaged research
composition and PR51's91.956s complete native task-output testcase also pass.
The latter's marker alone is not acceptance. An unchanged180s watchdog stack
is retained, not a failed process timeout or hidden retry.

Five optional checks did not execute: dedicatedPR42Windows intentional-timeout
and four olderDB opt-ins. All19native opt-ins execute across native14/paper4/kit1.
Two native skips require privileged symlinks. The firstPS5.1 fixture returned in
1.719s here, but PR49's failed different tree and underlying cause are not closed.
No CI rerun, prewarm, larger existing deadline or weaker assertion was used.

## Kit, source provenance and retention

Actual kit10475120840 was downloaded and hashed: outer66793464bytes/SHA256
`4277e8fe38eb79c6d20d80745fefc473cb9a74e32a24d0acc51dc47f6263cd78`;
inner66793292bytes/SHA256
`c3c6187866a0fdb73067d0593c242ed1f76d66fec8c786bfbbdbf35b2878430d`.
All2929manifest hashes/2928source payloads match reviewed bytes. PG17.11seed1753
entries checked for excluded fonts/cluster/passfiles; no engine ran in container.
This is engineering acceptance, not a permanent application Release.

Mounted6453blob baseline plus originalPR51patch reconstructed main. Read-only
snapshot35163719342/artifact10474270173 verified8oldPR50payloads/all6456mappings.
No new clone/credential lookup; local commits are reconstruction bookkeeping.
CodeGraph absent/not run. Static tooling deadlines, unavailable streaming and
wrapper-status details are preserved in tooling-notes.txt, not product failures.
Fixed8318byte patch db03f5bdbd0ecd26e11ad09ca9ad5ba4df27006885193d8d3082b901df6019ea
was read back and applied by exact-tree publication35164588619 to a NEW review
ref only, without app execution/main/feature mutation. Normal two-parent Git-data
publication then advanced PR50. Helper workflow/patch are outside the feature.

This eight-file directory retains the report, source bindings, review findings,
final gate summary, raw checksums, kit verification, direct detached result and
tooling notes. Full new12case RED, local/final CI logs/JUnit and oldPR50kit proof
are supplementary in pr50-integrated-review-evidence.zip, with verified CRC and
per-file size/SHA256. The delivery comment records its exact outer hash/size.
Do not claim this directory holds every raw log. No implementation patch, engine,
font binary, credential or user database is in that supplementary ZIP.
Actions artifacts expire after7days; this directory is evidence, not an updater.

No unresolved blocking finding identified in this scoped same-assistant separate
self-review and actual final tests. Not external/fresh-agent audit or absolute
no-defect assurance. WP03/WP06PARTIAL, G3/G6open, V1 1of6; D1-D3, real input/fee/
business acceptance and safe user-version changes remain open. No user-machine
task, userDB/backup/migration, real model/market request or live order occurred.
