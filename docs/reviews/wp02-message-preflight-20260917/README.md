# PR53: first-message byte compatibility before immutable research claims

The approved scope is WP02/WP03. D1-D3, real-provider/source/fee/business acceptance,
G2/G3/G6 and V1 are not closed by this change. PR49's failed revision and the
previously blocked CAPI experiment are separate and untouched.

## Frozen source and scoped change

Base: a1284c41db411281b666211c6a9ce4b6930ca8ec (PR52).
Candidate: c16a8208d27db144bd001196255e636aa525c0a3.
Tree: 4d15e1d555f108e19edf19e160593eea2841b28c.
Tested merge candidate: 54c58434d81ccb70ba67dcee92d34116d7a7bd6c.
Separate self-reviews: 5231678230 and final5231804146.
Actual merge: a71ae4d716e8a73bd3614f080f42e5a6968d76f8.
The actual merge was read back with the same tree and expected parents; no
post-test source change or separate post-merge full test run is claimed.

Six files,347additions/16deletions. The budget runner reuses the original agent
eligible-catalog/first-message construction, extracted as a private helper. A
known oversized FIRST messages_json is rejected before a new immutable execution
claim. UTF-8 bytes are counted exactly; equality is allowed. No request truncation
or automatic budget replacement. Original no-model captures and the earlier
empty/expired/output-limit priorities remain. Existing completed/incomplete
receipts are read/revalidated, not reclaimed; a read failure never admits work.

Later tool transcripts still need the original per-call transactional reservation.
This is not a provider tokenizer, entire HTTP-envelope size check, fee attestation
or promise that the full loop fits a monetary budget. No provider/default client,
credential path, business journal, store, migration or workflow was added.

## Actual local development and separate review

Original-code run:12failed/6passed. Eight cases establish the known oversized-input
claim/capture->model_failed path without a factory/permit. Four others assert the
new early-readonly replay shortcut with a simplified synthetic claim stub; they
do NOT establish duplicate execution in the old real database. Its claim-layer
deduplication was already present. Original RED retained.

The separate adversarial phase passed14cases with no new blocking finding. Together
with18implementation cases,32new tests cover exact byte boundaries/non-ASCII,
no-model captures, error priority, original receipt validation, history failures,
explicit compatible budgets and unchanged input. Transactions/claim/capture are
synthetic in unit tests; original permit input validation remains real Python code.

Independent old-agent/new-agent comparison passed180finite cases with identical
raw model messages/output ceilings AND final results. It includes existing teams,
text, eligibility/context, required sources and timezone variants; no V1 team
scope expansion or universal proof is claimed. Static AST checks preserve original
context expressions, model loop, other functions and _BudgetedModel. All46old
native assertions remain,58now; old unit bytes remain an exact prefix. All67SQL,
dependency and workflow files are unchanged.

Detached Git worktree544passed/0skips6.38s, actual import origin checked. Related
544passed/1native skip. Complete local scripts/verify_local.py --full finished
38300passed/42skipped319.09s,exit0/fullPASS including install/entry/compile checks.
LinuxPython3.13.5/preinstalled tooling is distinct from hosted locked/Windows
execution. No local PostgreSQL engine or actual provider was run.

## Provenance and tooling limits

Pinned source35185471542/artifact10481901673 checked6461blobs plus full base tree
and commit. Direct clone DNS failed; CodeGraph unavailable and not run. Temporary
venv points to installed build/test tooling, without dependency changes. Initial
audit-script import and trailing-whitespace-fixture mistakes were corrected only
in the audit runner. Streaming full-test execution was unavailable before start;
one actual full run completed normally. Collection postprocessing had its own
path correction without rerunning tests. These are not passed product checks.

An evidence-only uploaded patch had one transcription typo (replayplayed), found
by readback before application or any CI execution. It was corrected to the
ORIGINAL locally reviewed26359bytes,blob665d83f0903b852b45225416dee7fba06cd7b0d6,
SHA2565c2f55b7645ed240d9d09b8f91c7834a033fc3b597d3128a331e996c6001952d.
The tested source never changed to fit the typo. Transport35186955443 then verified
patch hash/size,base/result tree and six-file scope, publishing a NEW review ref
without running project code or changing main/the feature/workflow files. Helpers
are outside the candidate. No denied operation or CAPI experiment was routed around.


## Actual final-revision hosted evidence

| Gate | Actual result | Run / job |
| --- | --- | --- |
| Locked offline | 38300passed/42skipped444.43s | 35187062409 /105091284250 |
| Native storage | 290passed/2skipped281.30s | 35187062398 /105091284574 |
| Native research | 1059passed/0skips347.14s | 35187062398 /105091284533 |
| Native dispatch | 745passed/0skips524.98s | 35187062398 /105091284406 |
| Native aggregate | SUCCESS | 35187062398 /105093149041 |
| Paper/native | 339passed/0skips405.51s | 35187062595 /105091284835 |
| Actual kit | 368passed/0skips566.21s | 35187062404 /105091284295 |

All four workflows/seven jobs succeeded, with six test-job source-cleanliness
checks. Actual downloaded log/JUnit/ZIP CRC/SHA256 and exact testcase IDs checked.
Native2096IDs=2064baseline+32new; no missing/duplicate IDs. All32new cases passed
on Windows dispatch. Paper339 and kit368ID sets equal baseline, not additional
new tests. Cross-workflow counts overlap and must not be added as distinct coverage.

Enhanced native budget test has complete unskipped JUnit success61.705s. Its exact
BTC/ETH marker is before the later original process-loss assertions; full JUnit
success proves those finished too. Actual CI-private PostgreSQL verified zero
new claim/permit on mismatch, explicit matching budgets for SAME unmodified input,
immutable completed replay and original incomplete process-loss replay without
new reservations. Model clients/inputs are synthetic, not paid providers or users.
Existing full kit lifecycle/admission/research/simulation/settlement/drain/cold
recovery also passed separately on this tree. No new DB harness or user DB was used.

Five optional checks remain unexecuted: dedicatedPR42Windows intentional timeout
and four older DB opt-ins. Native2skips need privileged symlinks. All18old handoff
properties remain, but this success does not waive PR49 or establish its PS5cause.
No manual CI rerun, prewarm, expanded deadline or weakened assertion. One pure
wait-only container helper reached its call limit without any project/test action;
it is not a failed or retried CI/test run.

## Package and retention

Actual kit10481774798 was downloaded and independently checked. Outer66800006bytes,
SHA256daf24d91f1adae32dc5340e81055b3314b3340ebb684916303fd23a43eb7b2bf.
Inner66799834bytes,
SHA2566ae90ecc0f372acdbe25970d78e5c205c921255173045f7c59e25bc5356ea073.
Inner size/hash agree with the build receipt. All2929manifest hashes/2928source
payloads match the reviewed tree. PG17.11seed1753entries checked for excluded
fonts/cluster/passfiles; no engine executed or installed in the container.

This evidence-only directory retains eight report/metadata/selected-log files.
Full local RED/review/final and six final CI log/JUnit payloads are supplementary
in pr53-message-preflight-review-evidence.zip with a complete checked manifest and
CRC. The exact outer size/hash is recorded in the PR delivery comment. Full raw
logs are not all stored in this repository directory. Actions artifacts retain
seven days; neither branch nor supplemental ZIP is an application Release.
No executable patch, runtime/font binary, credential or user DB is in that ZIP.

No unresolved blocking finding identified in this scoped same-assistant separate
review and actual tests. Not a third-party/fresh-agent audit or absolute defect-free
guarantee. WP02 AWAITING_OWNER, WP03/WP06 PARTIAL, G2/G3/G6/V1 and D1-D3 remain open.
Real input/fee/business acceptance and PR49's underlying issue remain unclosed.
No user-machine task, installation overwrite, user-data/credential/provider/market/
wallet/order operation is required or claimed.
