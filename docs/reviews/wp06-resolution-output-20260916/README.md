# PR #45: resolution console failure handling

## Delivered identity and scope

Base: `1ac3ec6b1ec0de18faa1e0227076b14075525ec7` (PR43).
Reviewed head: `f04f0b8cd9131e6cff0bf2ff2213020dc25f1b79`.
Tested merge candidate: `81b3e2053466f01812ce9483f77218bda9c6e46c`.
Actual merge: `fff21287c0e3bfab2d679d7ac47bd8044f808ea9`.
Reviewed/tested/merged tree: `d6b1de4eda456393294549e5580db6c731d0d201`.
Separate same-assistant reviews: 5217694588 and 5217764538.

Six changed paths,383additions/13deletions. The merge tree was read back and
matches the reviewed tree. No post-test source change or separate post-merge
full test run is claimed. The separately open PR44 is not part of this delivery.

The existing resolution list/collection command no longer lets internal
SystemExit(0) escape an operation or cleanup as process success. KeyboardInterrupt
returns130. Failure metadata conservatively retains possible collection network
calls/evidence writes; an error does not prove rollback or authorize retry.

List/collect and confirmation share a small emitter INSIDE the existing
confirmation module: full serialization, one checked text write and explicit
flush. Serialization, short-write, write and flush errors return nonzero; no second
envelope or business retry is attempted. A partial prefix can remain, and flush
success does not prove receipt by the consumer. Interpreter shutdown may impose a
different nonzero status outside this handler. Original successful JSON and
failed_count exit semantics remain.

Default sys.stdin.buffer lookup now occurs inside the existing confirmation
input guard, after explicit permission. Explicit streams and original bounded
input codec remain. Parser/help and source-root behavior are unchanged; arbitrary
argument errors and a hostile interpreter are not claimed covered.

Both original managed operation blocks and decode_review are AST-identical.
All67SQL, dependency/manifest files and2899other protected source/SQL/config
payloads are unchanged. Native CI only appends two test paths; previous selections,
actions, permissions and time limits remain. The plan appends20lines without
editing prior text. No new store, provider, model, business journal or order path.

## Separate self-review and first failures

Original-code first run exposed10SystemExit failures before an intentional
uncaught KeyboardInterrupt stopped pytest. A harness-only adjustment recorded
escaped exceptions as failed assertions; the SAME original code then gave
35failed/3passed. Both logs remain; these counts are scenarios, not35distinct bugs.
Initial fix plus related tests passed214cases.

The separate review phase first gave3failed/11passed, exposing default binary
stdin access outside the guard. Fixed without weakening those assertions; the
review suite also checks after-operation sink failures and default opt-in behavior.
Six real fresh-interpreter cases exercise actual scripts with SYNTHETIC sessions;
these are not new real-PostgreSQL fault-injection tests.

Final new suite60passed. Related236passed14.96s. Exact detached-worktree60passed
4.64s, clean. Exact local full37916passed/41skipped313.05s with install/module/
console/compile checks. Local Python3.13.5/preinstalled packages are distinct from
hosted locked/Windows acceptance. Initial disposable-venv setuptools lookup failed;
only that venv's tooling search path was corrected, not project dependencies.

Pinned source helper35044875410/artifact10426940305 supplied6445verified tracked
blobs and the exact base tree after direct-clone DNS failure. Local Git ancestry
is reconstruction bookkeeping, not upstream identity. All6uploaded hashes and
full final tree match reviewed bytes. No real .env or user credentials were read.
The finite authored credential-pattern scan found no hits; not an absolute audit.

## Actual final-head gates

| Gate | Actual result | Run / job |
| --- | --- | --- |
| Locked offline |37916passed,41skipped,389.88s|35046004646 /104635839665|
| Native Windows |1816 passed, 2 skipped in 788.62s (0:13:08)|35046004634 /104635839454|
| Paper/native |339passed,0skips/errors,338.04s|35046004636 /104635839977|
| Actual Windows kit |123passed,0skips/errors,224.31s|35046004706 /104635839933|

All four workflows and tracked-source cleanliness steps passed. Actual downloaded
logs/JUnit, ZIP CRCs, payload hashes and case IDs were checked. All60new cases ran
on Windows. Native retains all1758prior IDs plus60new IDs; paper339 and kit123 IDs
match PR43. New console fault cases use synthetic sessions; existing native
confirmation/restart regression supplies separate real-engine coverage. No new
fault-injection proof inside an extracted kit or PR44's integrated recipe is claimed.

The41offline skips comprise18oldWindows shell, one dedicated Windows timeout,
18native opt-ins and four older optionalDB cases. Native13/paper4/kit1 opt-ins and
18old shell cases executed. Five optional checks did not execute this revision:
PR42's dedicated intentional-timeout test and four older DB opt-ins. Native's two
skips require privileged symlinks. Counts overlap; do not sum. Expected negative
SQL/argument output and diagnostic thread dumps remain in raw logs, not failed CI
reruns. No manual CI retry, prewarm, expanded timeout or weakened assertion.

New kit artifact10426384297 inner66787673bytes SHA256
`66545cebed03bfaf4bd32698a55b7fc83ea9017a0967cbe7ac72a53ebaba32e8`.
All2929manifest hashes/2928source payloads equal reviewed bytes. PG17.11seed1753
entries checked for excluded fonts/cluster/passfiles; no engine ran during
container archive inspection. Actual kit execution was on isolated Windows CI.

## Retention and remaining gates

The evidence-only repository directory retains this report, findings, source
manifest, original3failure review log, detached result, raw checksums and kit
verification. Complete first/final local and four-CI logs/JUnit are supplementary
in conversation pr45-review-evidence.zip with a checked CRC/size/SHA256 manifest.
Its exact outer size/hash is in the delivery comment. Do not claim every raw CI
byte is in the repository directory. Actions artifacts have7day retention, not
a permanent Release. No runtime binaries, fonts, credentials, user database or
implementation patch are in the supplementary ZIP.

No unresolved blocking finding identified in this scoped self-review/testing.
Same assistant in a separate phase/worktree, not external/fresh-agent audit or an
absolute no-defect guarantee. WP06PARTIAL/G6open/V1 1of6. D1-D3, real forecast/input/
fee acceptance, safe release-specific version changes and the original intermittent
PS5.1 fault remain open. No user-machine task or user DB migration, real provider/
market call, secret handling, live order or old-kit overlay was performed.
