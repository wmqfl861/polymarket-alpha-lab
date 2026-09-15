# PR #37 final review and delivery

Evidence-only retention; no runtime change or user-machine task.

## Fixed identity

- Base: `0147b5777be00296f9e981b956275b7b11b8e7f9`.
- Initial candidate: `6553a724ae57417f6c1f8073cb6c5af7f57ef1be`.
- Final reviewed head: `335e80ce3afeeff3a6539c19b30140f637a609ae`.
- Tested merge candidate: `e6b62835dd1198009063847abafdcc2d8f0ea532`.
- Actual merged main: `a257a7f921e412e348d2a17efd2b53c4f7530918`.
- Actual merge readback equals reviewed/tested tree `3407dee7e5f25c46ab15e9e65eeee4d2e1002a83`.
- Separate same-assistant reviews: `5208829359`, final gate addendum `5208949805`.

## Delivered WP-05 slice

The managed capture_paper_research and inspect_paper_research APIs retain one
immutable canonical input/result per original research record in the same private
PostgreSQL. Exact raw market/books, explicit costs and ready/rejected/failed/later
simulation decisions are saved atomically. Exact replay returns the original row;
changed bytes, times, costs or quantities conflict. Original history selection,
transaction binding and simulation are reused. No new forecast score or file journal.

New work uses the original complete-history snapshot followed by separately locked
admission. Database-stamped time and a deferred trigger check the original cutoff.
This does NOT certify remote-source timestamps, current whole-history completeness,
or WAL/COMMIT acknowledgement before cutoff. Readback recomputes only the saved
single decision; later outcomes cannot rewrite it. Output keeps realized_pnl=null,
paper_trades_created=0, source/tariff authentication false and
commit_before_cutoff_verified=false. This is durable simulation evidence, not a
filled order or portfolio ledger.

The production PR changes18paths,1085additions/17deletions. Migration67 is new;
all66older SQL bytes/entries are unchanged. Historical65->66 budget proof keeps
its original target, while current-catalog tests explicitly expect67. No original
transaction/strategy/fill/evaluator algorithm, dependency, timeout or elevated
role changed. No user database, real model/source request, credentials, old-kit
overlay or live trade was used. Actual user installations were not migrated.

## Separate review and first failures

39implementation cases passed. Separate16adversarial cases first3failed/13passed
exposed incomplete comparison of INSERT-returned history hash, first-record ID and
attempt-payload hash. The fix preserves all original assertions and RED output.
Static review then added3native-test lines requiring a successful pre-cutoff
INSERT witness; an early insertion failure cannot masquerade as deferred rollback.
The final native test passed with this stronger proof. This design finding is not
reported as an invented failed native run.

Final detached focused tests55passed/1native skip3.25s; exact final local full
verification37609passed/38skipped298.26s. Local Python3.13.5/preinstalled dependencies
are distinct from hosted locked/native acceptance. Direct clone failed DNS; a
read-only helper archived fixed source and6410Gitblob hashes were verified. Missing
original blank example fixtures were restored from pinned baseline bytes, never
activated. Initial13local failures caused by missing .env.example, a pre-pytest
interruption and editable-root refusal remain in supplementary raw logs. No actual
.env or credential was read. Local Git history is reconstruction bookkeeping;
all18final payloads and the reconstructed upstream tree were independently bound.
One historical prose glyph change is nonsemantic. Additional48binary roundtrips
and19missing-field rejections are finite codec probes, not extra native tests.

## Actual final-head gates

| Gate | Result | Run | Job |
| --- | --- | --- | --- |
| Locked full offline |37609passed,38skipped,392.46s|34957920545|104344395020|
| Existing Windows native PostgreSQL |1735passed,2skipped,0failures/errors,1046.62s|34957920375|104344341003|
| Windows paper/native capture |141passed,0skips/failures/errors,118.74s|34957920899|104344313696|
| Actual Windows kit |96passed,0skips/failures/errors,180.64s|34957920437|104344333496|

All four workflows and source-cleanliness steps passed. Actual downloaded logs,
JUnit and artifact hashes were inspected. All56new cases ran on Windows:39unit,
16adversarial and one65.979s native capture test. It proves66->67 preservation,
concurrent replay, ready/rejected/failed/later inputs, actual COMMIT followed by
injected lost acknowledgement, expired-age replay, restart, immutable/hash guards,
witnessed INSERT then deferred-cutoff rollback and original incomplete-history
refusal. Inputs/models are synthetic. No post-merge full rerun is claimed.

38offline skips comprise18Windows-shell cases executed in old-native CI,16native
opt-ins executed across old-native13/paper2/kit1, and4older optional DB cases NOT
executed. A static case in a native-named module is not another native opt-in.
The2Windows skips require privileged symlinks. Test selections overlap; do not
sum totals. Two120s faulthandler diagnostic dumps and expected negative-SQL stderr
remain in native logs, not failed CI timeouts. Initial-head paper141/0passed;
its other3runs were superseded/cancelled, not passes. No manual CI rerun, prewarm,
timeout expansion or assertion reduction.

Final kit artifact10392103804: inner66763784bytes, SHA256
`04ed51e18b64377ed6f8e5107b807390fc2820c9bf71007b5273cf1773b26aed`.
All2926manifest hashes/2925source payloads match reviewed bytes. PG17.11seed1753
entries were checked for excluded fonts/cluster/passfile paths. No engine ran in
container inspection. Capture executes in separate native proof, not inside the
existing extracted-kit lifecycle test.

## Retention and conclusion

This directory retains this report, original RED log, final detached pass log,
final source manifest and raw-file checksums. Complete first/final local and CI
logs/JUnit are supplementary in conversation pr37-review-evidence.zip; its exact
size/hash is recorded in the delivery comment. The ZIP has a verified payload
manifest/CRC and contains no runtime binaries, fonts, credentials or user database.
Do not claim all raw CI bytes are in this repository directory. Actions artifacts
10392367277/10392468268/10391953909/10392565339 have seven-day retention, not a Release.
Any included implementation patch is a historical archive of ALREADY MERGED work,
not instructions to apply it or modify a local installation.

No unresolved blocking finding in this scoped separate self-review/test pass.
This is same-assistant review, not fresh-agent/third-party audit or a guarantee of
zero defects. WP05 remains PARTIAL, G5 open, V1 1/6. Real input/fee validation,
outcome/PnL linkage, D1-D3 and knownPR30PS5.1 first-run reliability remain open.
No user-machine task or credentials are required for this delivery.
