# PR #36 final review and delivery

Evidence-only retention branch. No runtime change or local-agent task.

## Fixed identity

- Base: `4a6e787e1b0a6fb93c0d36455987c66f0b043e7c`.
- Reviewed head: `710313dcd567e9eb06d4e0a1f55f56375d44c89c`.
- Reviewed/tested tree: `27af9bd6cd4511688a6ad9c65dc30d3909c1c2e7`.
- Tested merge candidate: `e496ac8e67b2b8f56418c7fb05b8d621d0ea2574`.
- Actual merged main: `0147b5777be00296f9e981b956275b7b11b8e7f9`.
- Actual merge tree was read back and equals the tested tree.
- Separate same-assistant PR review: `5207966344`; final gate conclusion recorded here.

## Delivered scope

WP-05 read-only research-to-cost/order-book scenario composition reuses original
complete-history/first-attempt selection, cost/risk strategy and book-walk fills.
Original record/claim equality, rules/observation, market minimum/tick/status,
YES/NO token mapping, fresh valid books and full requested quantity are checked.
All original attempts remain visible; incomplete history blocks. Costs are explicit
hypothetical inputs, with conservative consumed-range fee and entry bounds.

This is retrospective in-memory computation: paper_trades_created=0,
durable_paper_evidence_created=false, realized_pnl=null, tariff_verified=false.
It is not a prospective trade ledger, verified fee quote or strategy validation.
No migration, dependency, original strategy/scorer/store algorithm, role,
provider call, public-market fetch, user database or installed kit was changed.
The production PR changes11paths,1099additions/2deletions. The old native workflow
and timeout are unchanged; new native cases use a separate runner.

## Separate self-review and preserved first failures

47 implementation cases passed. The initial eight adversarial cases returned
5failed/3passed, but one rounding fixture violated original capture precision.
Correcting ONLY that fixture on the same broken implementation reproduced five
genuine failures: threshold rounding, consumed-range fee bound, missing original
observation/contract gate, ignored minimum quantity and malformed JSON handling.
Both RED logs are retained in the supplementary evidence ZIP, not replaced by green
results. Assertions were not weakened; adversarial coverage expanded to37cases.

Final detached local worktree:84passed/1native opt-in skipped1.39s, clean. This
uses Python3.13.5/preinstalled packages and a verified shipped-source SUBSET, not
a full clone, locked full suite or local native test result. Direct clone failed
DNS. Ten prepared payloads matched uploaded Git hashes; root plan was separately
verified through exact upstream patch (one WP05row+27appended lines). All66SQL
and2917unchanged prior shipped source hashes were preserved. Two transient
transcription mismatches were hash-rejected before commit and left unreferenced.

Finite extra probes:240seeded exact Fraction cases(223ready bounds/17rejected),
100scenarios with200ask levels, and100mixed-Decimal-context recomputations over
8threads. These are not extra pytest/native counts or statistical proof.

## Actual final-head gates

| Gate | Actual result | Run | Job |
| --- | --- | --- | --- |
| Locked full offline | 37554passed,37skipped,339.30s | 34950904273 | 104321371538 |
| Existing Windows native PostgreSQL | 1735passed,2skipped,0failures/errors,826.05s | 34950904535 | 104321372192 |
| New Windows paper/native proof | 85passed,0skips/failures/errors,33.40s | 34950904771 | 104321373372 |
| Actual Windows kit | 96passed,0skips/failures/errors,180.81s | 34950904312 | 104321371958 |

All four whole workflows and source-cleanliness steps passed. Actual downloaded
logs/JUnit/hashes checked. All85new cases ran on Windows:47unit+37adversarial+1native.
The30.571s new native case proves BTC/ETH read-only composition, failed-record
denominator, exact read replay/restart, unchanged rows and strict incomplete-history
refusal. Its interrupt is injected SystemExit in the SAME process, not a child
crash. All business inputs/models are synthetic.

37offline skips=18Windows shell(executed old-native)+15native opt-ins(13old-native,
1paper,1kit)+4older optional DB cases NOT executed. Two Windows skips require
privileged symlinks. Counts overlap and must not be added. Old-native log retains
one120second faulthandler diagnostic dump and expected negative SQL-test output;
final JUnit has no failures/errors. No manual CI rerun, prewarm, timeout extension
or reduced assertions.

Kit10389257580 inner66753352bytes SHA256
`6e898d133c95a3204cf0aeff903cd4382bf5c92a6f5bf591667cf55d2e3c61f6`.
All2923manifest hashes/2922source payloads match reviewed bytes. New verifier accepts
untouched oldPR33kit2920hashes. PG17.11seed1753entries checked for excluded
font/cluster/passfile paths; no engine ran during container inspection. The new
paper API executes in its separate native proof, not inside the existing extracted
kit lifecycle test.

## Publication and evidence limits

The final PR body update failed with one ReadTimeout and two502 service errors;
readbacks after the first two failures showed the old body. The final PR review
addendum also returned502. Those writes are NOT claimed successful. Merge itself
succeeded normally with an expected-head guard; its actual tree was verified.
The merge message and this retained report record the now-completed gates, even
if the older PR description still says pending. These were metadata-service
failures, not failed product tests or hidden model/database retries.

This directory retains this report, findings summary, exact-rational probe/result,
local final pass log and CI raw-file checksums. Complete initial RED/local/final CI
logs and JUnit are in supplementary conversation `pr36-review-evidence.zip`, with
embedded manifest and verified CRC/size/SHA256. Do not claim all raw logs are stored
in this directory. Actions artifacts10388963791(offline),10388894945(old-native),
10388803388(paper),10389511391(kitproof) have seven-day retention, not a Release.
No runtime binaries, fonts, credentials or user database are in the evidence ZIP.

## Conclusion and remaining gates

No unresolved blocking finding identified in this scoped separate self-review and
actual tests. Same assistant, not external/fresh-agent audit, source/tariff
certification or a zero-defect guarantee. WP05 remains PARTIAL, G5 open, V1 1/6.
Durable prospective paper inputs/rejections/executions and real outcome/P&L linkage
remain required. D1-D3 and known PR30PowerShell5.1 first-run reliability remain open.
No user-machine task or credentials are required for this delivery.
