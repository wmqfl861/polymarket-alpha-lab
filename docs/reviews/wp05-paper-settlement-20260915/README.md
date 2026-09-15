# PR #38 final self-review and delivery

Evidence-only retention. No runtime change, new release or user-machine task.

## Fixed delivery identity

- Base: `a257a7f921e412e348d2a17efd2b53c4f7530918`.
- Reviewed head: `9349b5acf2037ed699909237dc7b4df0f1e9d6c9`.
- Reviewed/tested tree: `231b865836c651f39c942318bd95b39ed989ba6c`.
- Tested PR merge candidate: `0137a4c846d1923c611cd0fe2697c84af3eeb879`.
- Actual merged main: `f0ce4fa2be7d32874d88713a4c2248162b412d9d`.
- Actual merge tree was read back and equals the reviewed/tested tree.
- Separate same-assistant review: 5209817044; final gate addendum: 5209998492.

## Delivered WP-05 engineering slice

The managed evaluate_settled_paper_research API connects retained simulation
receipts to existing crypto-reviewed outcomes in ONE read-only repeatable-read
snapshot. It reuses original complete-history/first-attempt selection, immutable
paper validation and the existing crypto confirmation builder. The original
history query body was extracted into a cursor helper without changing its AST
or the old public preflight. All nine uploaded source hashes and the independently
reconstructed complete upstream tree matched. Production PR: nine paths,
949 additions / 26 deletions. All 67 migrations and dependencies are unchanged.

Original direction, quantity and assumed costs are not reselected after the
outcome. Binary payout minus the saved total cost upper bound gives a settled
simulation PnL lower bound WITHIN that model. It is not actual account PnL or a
verified tariff/fill. All attempts stay visible; missing, failed, rejected,
later, pending and insufficiently confirmed rows do not receive invented zero
PnL. Incompatible cost/risk policies are not pooled. Empty settled subsets have
null sums. No new forecast score, portfolio return, file journal or order path.

Linked crypto proof is rebuilt with the existing builder and compared canonically.
Exact outcome, condition, cutoff and full rules must agree. Ordinary legacy
outcomes remain visible but unpriced. Corrupt claimed provenance aborts instead
of yielding partial totals. Operator source assertions are not authenticated data.

Original history bounds and an additional conservative 32-MiB payload budget are
checked before body reads. Future cutoffs, incomplete visible claims and oversized
or inconsistent sources abort. One-call isolation does not reconstruct historical
COMMIT visibility: later historical reruns can see late commits with older recorded
timestamps. It also does not certify COMMIT acknowledgement before forecast cutoff.

No migration, elevated role, timeout change, real model/public-market request,
credentials, user database operation, kit overlay or live trade. Developer API
only; no new CLI. New Python module ships via existing kit selection; the new
runbook is source-repository documentation, not a newly packaged kit document.

## Separate self-review and preserved failures

The initial 29-case implementation run had one fixture keyword typo; fixing only
that fixture on unchanged product code produced 29 passes. The separate 21-case
adversarial run first had 2 failures / 19 passes: one real valid-but-opposite-review
substitution and one invalid terms-test construction. Correcting only that fixture
on the still-broken implementation yielded 1 failure / 20 passes. The monetary
association now explicitly checks review.outcome equality; the original genuine
failing assertion was not weakened. Both initial and corrected RED logs remain
in the supplementary evidence ZIP; the corrected RED is also retained here.

Final focused tests: 50 passed. Related regression: 310 passed / 1 native opt-in
skipped. Detached worktree: 50 passed / 1 native skipped in 7.08s, clean. Actual
local full verification: 37659 passed / 39 skipped in 558.34s, including installation,
entrypoints and compilation. Local Python 3.13.5/preinstalled dependencies and
fixed-source reconstruction are not hosted locked/native evidence. Direct Git DNS
failure and exact public blank-example restoration were recorded; examples were
never enabled and no real .env was read. Unsupported streaming prelaunch, one
archive-inspection host ServerError and one sleep-only tool timeout are tooling
events, not product failures, native test retries or successful tests.

Supplementary finite probes: 80 exact Fraction settlement-bound comparisons and
24 mixed-Decimal-context calls over four threads passed. Authored-file finite
credential-pattern scan found no matches. These are not additional pytest/native
counts, authenticated data or statistical strategy validation.

## Actual final-head gates

| Gate | Actual result | Run | Job |
| --- | --- | --- | --- |
| Locked full offline | 37659 passed, 39 skipped, 422.54s | 34967701724 | 104376026347 |
| Existing Windows native PostgreSQL | 1735 passed, 2 skipped, zero failures/errors, 1057.26s | 34967701728 | 104376026574 |
| Windows paper/settlement native | 192 passed, zero skips/failures/errors, 312.12s | 34967701771 | 104376026534 |
| Actual Windows kit | 96 passed, zero skips/failures/errors, 179.68s | 34967701731 | 104376025985 |

All four complete workflows and tracked-source cleanliness steps passed. Actual
logs/JUnit/hash checks were performed, not only badge inspection. All 51 new cases
ran on Windows: 29 implementation, 21 adversarial and one native integration.
Their exact test-ID multisets match the detached local suite.

The new 181.395s native case proves nine-attempt denominators, all four YES/NO
payout combinations, actual UTC minute close and original crypto confirmation,
real delayed-COMMIT snapshot isolation, unchanged source rows, historical/restart
views and strict incomplete-history refusal. A paper INSERT really remained
uncommitted through the history read, then committed before the paper query;
its earlier recorded timestamp still could not make it visible in that snapshot.
Evaluation table counts were unchanged. Business inputs/models are synthetic.

The 39 offline skips comprise 18 Windows-shell cases run in native CI, 17 native
opt-ins run across old-native (13), paper (3) and kit (1), and four older optional
DB cases NOT executed. Two Windows skips require privileged symlinks. Static tests
in native/shell-named modules are not extra opt-ins. Selections overlap; do not
sum totals. Raw logs retain two 120-second faulthandler dumps in old-native and
one in paper while waiting for actual minute close. They are diagnostics, not
failed CI timeouts. No manual CI retry, prewarm, relaxed assertion or extended
timeout. No separately completed post-merge full run is claimed.

Kit artifact 10396081185: inner ZIP 66768641 bytes, SHA256
`afffc4265098e67b26c4300f75006cd9660932a8832b49631904b4745489453e`.
All 2927 manifest hashes / 2926 source payloads equal reviewed bytes. PG17.11 seed
1753 entries checked for excluded fonts/cluster/passfile paths. No engine ran
during container archive inspection. New API executes in dedicated native proof,
not falsely attributed to the extracted-kit lifecycle test.

## Retention and remaining gates

This directory contains this report, corrected original RED, final detached log,
source manifest, final source checks and raw-file checksums. Complete initial/final
local and CI logs/JUnit, probes and audit metadata are supplementary in conversation
pr38-review-evidence.zip, with verified CRC and a per-payload size/SHA256 manifest.
Its exact ZIP size/hash is recorded in the final delivery comment. No runtime
binaries, fonts, credentials, user DB or historical implementation patches are
included. Do not claim full raw CI bytes are in this six-file GitHub directory.
Actions artifacts 10395659594, 10396701391, 10395594710 and 10395806783 retain seven-day
expiry; they are not a permanent Release.

No unresolved blocking finding was identified in this scoped separate self-review
and actual tests. Same assistant, not fresh-agent/third-party audit, source/fee
certification or a guarantee of zero defects. WP05 remains PARTIAL, G5 open, V1 1/6.
Real approved forecasts, real input/fee evidence and complete operator acceptance,
D1-D3 and known PR30 PowerShell 5.1 first-run reliability remain open. No local user
task or credentials are required for this delivery.
