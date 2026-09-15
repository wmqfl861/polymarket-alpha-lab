# PR #39 final self-review and delivery

Evidence-only retention. No runtime change, release or local-agent task.

## Fixed delivered identity

- Base: `f0ce4fa2be7d32874d88713a4c2248162b412d9d`.
- Initial candidate: `2900b081f5a725e5262a9fee21ba1e54d79f3f07`.
- Final reviewed head: `cbd75aec47c7224d4f6ec72e7c75eb2d2971d0b2`.
- Reviewed/tested tree: `da3c1d589eb2662db0b3a5f108abe49221e8ef93`.
- Tested PR merge candidate: `2e8a2a8c71f26dbfbd86625f2fd63d10ab106964`.
- Actual merged main: `747faeaa8a5d60776261d80941e061b80af0f9b4`.
- Actual merge tree was read back and equals the reviewed/tested tree.
- Separate same-assistant reviews: 5210663087, 5210905292 and final 5211128481.

## WP-05 operator integration

The existing evaluation command now accepts --settled-paper, with optional
--include-decisions. It calls the existing managed settlement API once, without
recomputing amounts, changing cost-policy groups or filtering original attempts.
Default probability evaluation is unchanged. Counts, decimal strings, nulls and
full-input hashes remain; the shorter display omits only the two per-record
arrays and explicitly marks that choice. Request scope/options and incompatible
exports are checked, but this presenter is not a source authenticator: the original
managed service still owns complete-history, provenance and monetary validation.

Success is serialized after managed cleanup and written once. New-mode known
blocks and unknown failures have no partial success or fallback. SystemExit(0)
cannot become success; interrupts return130. A failed output stream returns
nonzero without a second write, but cannot guarantee an intact error envelope.
Actual native CLI children compare with the original API for four payouts,
summary/details, historical and blocked views. BOTH independently extracted kits
run the option using their own Python environments.

The production PR changes14paths,949additions/4deletions. All67SQL migrations,
manifest, dependencies, roles and original history/settlement algorithms remain
unchanged. No scenario capture, business-write option, new command/store/score,
real provider/public-market call, credentials, user database operation, old-kit
overlay or long-term Release. Settled amounts remain assumed-cost simulation
bounds, not actual account PnL, verified fees or strategy validation.

## First native failure and isolated diagnosis

Initial old-native run34974025205/job104397074599 FAILED with a Windows
CPython3.12.10 access violation during a C-watchdog timeout traceback. It produced
no complete JUnit or test totals; source-cleanliness was skipped. No totals are
inferred from dots. Other three successful workflows did not waive the failure,
and the PR was not merged. Original raw log and first-run artifacts are retained
separately from final green results.

Fixed stdlib-only paired diagnosis on the SAME interpreter reproduced4of4C
watchdog access violations and4of4successful Python-thread synchronous dumps,
without project, native-extension or database imports, network or deliberate
memory corruption. Source: review/pr39-native-crash-20260915, immutable commit
`667129448ff71a11337bd041016f6c5c930834ab`, path
`docs/reviews/pr39-native-crash-20260915/faulthandler_probe.py`.
Run34975611566/artifact10399510615 retains all eight fixed samples. The controller's
successful completion is diagnostic evidence, not release acceptance. The result
strongly supports the asynchronous C diagnostic mechanism; it is not proof that
all interpreter problems or the older PowerShell5.1 issue are eliminated.

The correction replaces pytest's test-only timeout diagnostic plugin rather than
silencing diagnostics. Fatal handlers remain enabled. The original120/180second
diagnostic trigger values,20/10minute outer job deadlines, all old test paths,
assertions, action pins and permissions remain. Worker-owned duplicated descriptors,
cancellation and error propagation are tested. No C timeout watchdog is used.
Explicit hard-exit requests cannot silently downgrade into a diagnostic-only mode.

Important tradeoff: a Python diagnostic thread needs the GIL. If native code holds
it indefinitely, a dump may not occur. The unchanged outer CI deadline still fails
the job. This is not identical diagnostic coverage or automatic deadlock recovery.
No manual failed-job rerun, interpreter/dependency upgrade, prewarm, deadline
extension or assertion reduction was used. Test infrastructure is not shipped as
application runtime code. See the source runbook docs/ci-thread-diagnostics.md.

## Separate review and local evidence

Operator34implementation+22review tests: original review6failed/16passed exposed
historical-window, record-bound, three diagnostic-option and nested-readonly
mismatches. The six failing assertions remained unchanged after fixes.
Diagnostic15implementation+6review tests: original review2failed/4passed exposed a
constructor descriptor leak and a close failure reduced to a warning. Both were
fixed without weakening assertions. Both raw RED logs are retained here.

Exact final detached focused testing:129passed/2native opt-in skipped21.51s, with
the replacement active. Detached full verification:37736passed/39skipped228.55s
used identical final runtime/tests/workflows plus an11line documentation-only
plan note. That note was removed before the exact final-payload focused retest;
the local full run is not mislabeled as identical-full-tree hosted acceptance.
Local Python3.13.5/preinstalled dependencies and reconstructed Git history are
separate from hosted locked/native evidence. A standalone review-venv installation
failed before pytest; existing editable dependencies were explicitly pointed to
the detached worktree for review and restored afterward. Original logs are kept.

Both worktrees are clean and all14final payload hashes were rechecked. Complete
upstream-tree reconstruction matches GitHub. All6416other materialized base blobs,
including67SQL, are unchanged; the one unmaterialized .codegraph/.gitignore is
retained through the full upstream base tree. Original CLI helpers/default path
pass AST equivalence after removing only the new option and branch. Public example
fixtures were never enabled; no real .env was read. A48record/24comparison mixed-
context projection probe is finite supplementary evidence, not additional pytest
or statistical/source-authentication proof.

## Actual final-head acceptance

| Gate | Actual result | Run | Job |
| --- | --- | --- | --- |
| Locked full offline |37736passed,39skipped,400.31s|34977684475|104409508928|
| Windows native PostgreSQL |1756passed,2skipped,0failures/errors,1034.53s|34977684371|104409508701|
| Windows paper/native |269passed,0skips/failures/errors,313.45s|34977684644|104409509502|
| Actual Windows kit |96passed,0skips/failures/errors,151.58s|34977684401|104409509781|

All four whole workflows and tracked-source cleanliness checks passed. Actual
downloaded logs/JUnit/hashes were inspected. All77new cases ran on Windows:
56operator+21diagnostics. All1737old-native case IDs are preserved exactly, with
21diagnostic cases added. Two actual Python-triggered diagnostic dumps completed
normally in old-native, one in paper; no fatal access violation occurred in these
final runs. The200.093s extended native settlement scenario exercised actual CLI
children, original API equality, history/limits/incomplete blocks and no evaluation
writes. All business inputs and models were synthetic.

The39offline skips comprise18Windows-shell and17native opt-ins exercised in their
corresponding final Windows flows (old-native13,paper3,kit1), and4older optionalDB
cases NOT executed. Two Windows skips require privileged symlinks. Selections
overlap; do not sum counts. Passing this finite run does not prove indefinite
stability. No separately completed post-merge full test run is claimed.

Final kit10400455813 inner66772053bytes SHA256
`70b607bef98c019ba46eea3838a924b97cc85f0d7ce1c3780c7f47ab62edc463`.
All2927manifest hashes/2926source payloads equal reviewed bytes. PG17.11seed1753
entries were checked for excluded fonts/cluster/passfile paths. No engine ran
during container archive inspection. Actual extracted-kit tests ran new mode on
missing-paper/empty-history examples, not the separate four-payout native scenario.

## Retention and remaining gates

This directory retains this report, two original RED logs, exact final focused
log, source manifest, paired-diagnosis summary and raw-file checksums. Complete
first/final CI logs/JUnit, local failures and paired stdout/stderr are supplementary
in conversation pr39-review-evidence.zip with CRC and per-payload size/SHA256
verification. Exact ZIP size/hash is in the final delivery comment. Do not claim
all raw CI bytes reside in this seven-file directory. Original Actions artifacts
have seven-day retention and are not a Release. The supplementary ZIP contains
no runtime binaries, fonts, credentials, user databases or implementation patches.

No unresolved blocking finding identified in this scoped separate self-review and
actual tests. Same assistant, not fresh-agent/third-party audit, source/fee
certification or a zero-defect guarantee. WP05 remains PARTIAL, G5 open, V1 1/6.
Real approved forecasts, input/fee evidence, full operator acceptance, D1-D3 and
known PR30PowerShell5.1 first-run reliability remain open. No user-machine task
or credentials are required for this delivery.
