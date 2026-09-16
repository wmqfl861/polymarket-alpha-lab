# WP-06: explicit cold-backup catalog-prefix compatibility

Status: **CANDIDATE, NOT PUBLISHED, NOT MERGED. Windows/native/kit CI not executed for this candidate.**

Base upstream commit: `4df08873f4b9b8f1378913c0d00ca6bb183e51e2`.
Base source tree: `401f5a691e6898c670e446d142340d396862dae9`.
Candidate source tree: `87486a77918a1eef57e19b7960032211a8578ab9`.
Local commit IDs are reconstruction/checkpoint bookkeeping, NOT upstream commits.

## Delivered candidate behavior

The existing verify-backup and restore commands gain explicit
`--allow-catalog-extension`. Default exact-catalog behavior and normal receipts
remain unchanged. The optional path requires a real boolean at the Python API.
It validates the entire current source catalog, then matches the old archive's
existing effective-name/hash fingerprint against a nonempty exact prefix.
No backup-format change, new business store, automatic migration or engine change.

All original trust, independently retained archive checksum, platform, exact
physical-root/runtime and absent-target guards remain. Changed/removed/reordered
historical entries or an invalid added tail fail. Opt-in receipts report catalog
counts and current fingerprint, explicitly not an applied database ledger.
Restoring leaves the engine stopped, and managed sessions still reject pending
migrations until a separate explicit migrate. This is not an immutable-kit upgrade,
in-place rollback, cross-path restore, or authorization to operate on user data.

## Separate same-assistant self-review

Initial feature requirements on original production code: 21 failed, 1 passed,
largely because the new opt-in API/CLI did not yet exist. This is a baseline for
new functionality, not 21 newly discovered independent product defects.

After implementation, separate adversarial review: 1 failed, 12 passed. A changed
appended migration with an unchanged catalog length could pass the final restore
recheck when it compared counts alone. The fix binds the complete current catalog
fingerprint. The original failing assertion remains unchanged. Existing success
contract tests now also require that additional provenance field; no rejection
assertions were relaxed. Failed rechecks retain staging and never publish it.

Final focused run: 244 passed, 2 native opt-in skips in 13.14s.
Separate detached worktree: 130 passed, zero skips in 2.25s, comprising the 35 new
unit/review cases plus unchanged backup/ZIP regressions. Imported backup source
origin was explicitly verified under `/mnt/data/pal-backup-review/src`.
New native test is written/compiled, NOT locally executed. It is designed to use
an isolated real 66-migration snapshot, the unchanged real 67th catalog migration,
CLI default/opt-in checks, same-root absent-target restore, actual pending-ledger
refusal, explicit migration, original success/failed-record and identity retention.
No user backup or generated credential is in this package.

Additional finite checks: 99 valid prefix matches, 14 nonprefix refusals and a
1000-entry worst-prefix probe. These are finite in-memory probes, not additional
pytest/native cases or a proof for arbitrary environments.

Static review retained all 18 original native-backup assertion nodes (52 now),
all 67 SQL migrations and 2920 other protected source/script/SQL/dependency
payloads. Backup creation, original archive approval/streaming validation,
shutdown checks, fingerprint encoding and staging layout remain AST-identical.
Native workflow only appends two test-module selections; old tests, job/process
limits, actions and permissions are unchanged. Python compile and finite authored
credential-pattern scan passed. Same assistant, separate review phase/worktree;
not third-party/fresh-agent audit, exhaustive security audit or zero-defect guarantee.

## Source and publication limitations

A public Git clone in the container failed DNS. Existing fixed source archives and
the prior integration patch were used to reconstruct the exact current main tree;
every archive blob and the final tree were verified. Temporary Python environment
reused only preinstalled tooling and installed the project editable without network
or dependency/lock changes. Local Python3.13.5 is NOT hosted locked Windows evidence.
CodeGraph executable was not available and was not run.

This turn's GitHub connector exposes 48 read operations and no commit/branch/PR
write operation. Installed plugin capability discovery found no available alternate
write path; the remote connector also exposes no command execution/write tools.
`gh` is not installed in the container. This is an observed interface limitation,
not a claim that the owner's GitHub repository permissions were revoked. No write
operation was reported denied, no policy was bypassed, and no source was uploaded.

The patch has been checked/applied against an independent index of the exact base
source tree and yields the candidate tree above. It still needs GitHub-first
publication, readback verification, final-source Windows/native/kit CI and final
merge review. The included files preserve work; they are NOT a claim that the
required repository-first handoff has been completed. A local publication bootstrap
must publish them to an immutable repository ref BEFORE implementation/CI handoff.

No user-machine operation, business database read/write, real market/provider call,
credential discovery, old-kit overlay or live order was performed. WP06 remains
PARTIAL; G6 and V1 completion, D1-D3, actual input/fee/provider acceptance and the
original PS5.1 intermittent issue remain open.

## Completed exact-tree full local verification

37973 passed / 42 skipped in 319.70s; `PASS: full local verification`.
The process completed and both source/review worktrees stayed clean. The 42
skips comprise 18 old Windows-shell cases, the dedicated Windows timeout case,
19 native opt-ins (including this new one), and four older optional database
cases. None of those skipped tests is claimed executed in this turn.
This is local Linux/Python3.13.5 evidence, not Windows or locked hosted CI.
