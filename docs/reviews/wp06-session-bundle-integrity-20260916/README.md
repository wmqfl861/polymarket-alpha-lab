# PR49 review: candidate implemented, acceptance BLOCKED

**NOT MERGED. Keep PR49 draft. One required native test failed.**

Base main: `3a987bc6c1a5df608ff8f0668b866aaf87842c04`.
Candidate: `061a55703197cc526f3d707d9da58b971c582e0b`.
Reviewed/tested tree: `6c2dfac8bfd63a12191eaf026f33392bfeb238e6`.
Tested merge candidate: `f9b68a2b4125356d69bad16f1b7f9a6cfe21e0cb`.
Separate same-assistant reviews:5224689922 and blocked-gate addendum5224882513.
No merge, release, user installation or post-test source change is claimed.

## Implemented and separately reviewed

Direct managed research previously did not reuse startup's declared-kit check.
The existing server now verifies source and data roots before private lifecycle
access, using the existing verifier. Equal roots are checked once. Either manifest
or seed requires a complete valid kit; success is not cached. Missing companions,
changed payloads, extra selected code/SQL and invalid markers refuse entry.
Source-only installations and original status/down/session cleanup remain.

Production change is20added lines. All original server methods are AST-identical
except the entry call;67SQL/dependencies and2921other protected payloads unchanged.
Plan/quickstart append-only;84old kit assertions retained,95now. Native workflow
unchanged; distribution only appends two test paths, preserving all old limits.

This is entry-time byte integrity, not publisher authentication, a filesystem
snapshot, continuous monitoring or a hostile/preloaded-Python sandbox. Some code
is already imported. Both markers absent remains SOURCE mode, not detection of
malicious removal of both. Cross-root checks do not certify schema compatibility.

Original tests:22failed/7passed. First fix:144related passed. Separate review first
2failed/6passed exposed raw PermissionError from unreadable marker lstat. Mapping
OSError to the existing bundle error fixed it without changing failing assertions.
Final37new cases passed. Exact detached224passed33.53s; origin-checked new-only
37passed2.05s. Full local38023passed/42skipped560.41s, final PASS. Local Linux
Python3.13.5/preinstalled packages are not hosted locked/Windows acceptance.

## Actual final CI and unresolved failure

| Gate | Result | Run / job |
| --- | --- | --- |
| Locked offline |38023passed,42skipped,436.95s|35114517483 /104856403609|
| Native storage |290passed,2skipped,375.89s|35114517410 /104856403684|
| Native research |1058passed,1FAILED,344.56s|35114517410 /104856403479|
| Native dispatch |516passed,409.85s|35114517410 /104856403247|
| Native aggregate |FAILURE|35114517410 /104859297315|
| Paper/native |339passed,0skips/errors,426.37s|35114517340 /104856402624|
| Actual kit |182passed,0skips/errors,538.76s|35114517472 /104856402814|

All workflows completed. Research source-cleanliness was skipped after failure;
other test jobs passed it. Raw logs/JUnit, ZIP CRC/hashes and IDs were inspected.
Native union retains1867old IDs exactly:1864pass/2skip/1FAIL. Kit retains145old IDs
plus all37new IDs, matching detached tests. Paper339IDs unchanged. Do not add
overlapping workflow counts. Five optional checks remain unexecuted: dedicated
PR42Windows timeout and4olderDB. Native2skips require privileged symlinks.

The actual469.306s existing kit case includes old lifecycle/composition and new
real direct-command, source/data-root rejection, stopped-engine log preservation,
running borrowed-engine preservation and original-record checks. No DB/runtime
mocks in those native probes. All37new cases passed on Windows. This does not
waive the required failed native gate or make the kit a release.

Failure is the unchanged success-powershell.exe handoff case at original30s.
JUnit case30.465s; parent trace30.422s. Child stages:script_entered523ms,
encoding_ready18397ms,helper_loaded/invoke_entered18435ms,manifest_entered26871ms,
manifest_returned26874ms. Trace is untruncated and well formed. The script entered;
payload/serialization completion was not observed. This is not a never-started
process or solely a post-serialization pipe-drain diagnosis. The large encoding
interval includes test-only setup, but does not establish module/CLR/network/
antivirus cause or causation by this change. The following DIFFERENT manifest_hash
case took20.156s, including a15s observed serialization interval. Later fast cases
cannot erase the first failure. Root cause and relation to PR30 remain unproven.

No retry, prewarming, increased timeout, security relaxation or speculative runtime
repair was performed. Keep this candidate draft. Source review found no additional
blocking defect in the new helper, but this actual required failure remains open.
This is separate same-assistant review, not external audit or zero-defect assurance.

## Exact retained evidence and provenance

The complete first-failed research log and JUnit are retained in THIS directory,
not only an expiring artifact: native-research-first.log and native-research-first.xml.
They are exact bytes from artifact10454376988, ZIP25986bytes/SHA256
`625166f4411e018f16d38af6825af32eeff49fdf7ac43df4a6bcd05f23d54d0f`.
Raw log44527bytes/SHA256
`f3b6db366d28ce9a4cb54158c19af683f32467d35babd999da22588f398e5d68`;
XML247780bytes/SHA256
`3edbadb998244f94db0791517513101f0a06117ea4bf369a17e3a5f9bda80ba1`.
A fixed archival job copied only those two checked files to a NEW review ref;
no project code ran, main/feature were not changed, and this was not a CI rerun.

Kit10455331533 inner66791954bytes/SHA256
`d528fabfdcfe3c2ef72b27b81b81ce251e2c7e983be6b901781034e6aa1b355a`.
All2929manifest hashes/2928source payloads match the candidate. PG17.11seed1753
entries checked for excluded fonts/cluster/passfiles; no engine ran in container.
No runtime/font binaries or user database are in this evidence directory/ZIP.

Pinned archive35112003172/artifact10452906096 supplied6453verified Gitblobs and
exact base identity after Git DNS failure. CodeGraph unavailable, not run. Original
local REDs, interrupted noneditable invocation, temporary tooling lookup issues,
unavailable streaming, incomplete static check and rejected mismatched-index patch
preflight remain separately documented; none is counted as a pass. Correct
editable/detached verification and clean BASE patch application completed.

Publication used the fixed21509byte patch623102e7293f4ca1ffa1d9f08020557416db8d395ab8cdb003a1b8d18708f5b6
and exact-tree transport35114284786 plus the Git-data workflow blob. Helper workflows
are outside the candidate; no platform refusal or force-push was bypassed.

Supplementary pr49-review-evidence.zip retains all local/CI logs and audit metadata
with a payload manifest/CRC. Other Actions artifacts still expire after7days.
The ZIP is evidence, not an installation, accepted release or local execution task.
WP06PARTIAL/G6open/V1 1of6; D1-D3, real inputs/fees/business acceptance and safe
user-version changes remain open. No user-machine task, credential discovery,
user-data restore/migration, real provider/market call, old-kit overlay or order.
