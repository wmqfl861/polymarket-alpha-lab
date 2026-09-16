# WP-03/WP-06 task-output candidate: unpublished, Windows acceptance pending

Repository: wmqfl861/polymarket-alpha-lab
Base: 3a987bc6c1a5df608ff8f0668b866aaf87842c04
Base tree: 81a82964c103cf0f7cfa21f0bb3cad182a90e186
Expected result tree: b5734a05cc60ff37bcbf226bedb8586eefc9112a
Local-only bookkeeping commit: b1290f41825eeb1d73d60d2b0e31fcc27fe1eb44

This is a distinct task-command output fix. It does NOT include PR49's session
bundle change or any proposed CAPI2 experiment. PR49 remains blocked/draft.
There is no new upstream branch/PR for this candidate yet. GitHub-first delivery
is NOT complete: the current connector has no write actions, and container git
could not resolve github.com. This limitation is not a request to circumvent the
previous diagnostic safety refusal. Do not publish or execute those old objects.

## Behavior

Budgeted task execution and batch/turn/budget queries reuse the EXISTING checked
resolution emitter. Preserve successful JSON and the existing operation codes.
Render one envelope before one checked character write and explicit flush. Fail
nonzero without a second envelope or business replay on output failure. Preserve
shared cooperative stop on an output KeyboardInterrupt, but do not cancel other
shared invocations merely because an ordinary output stream fails. Previously
admitted work and committed reservations are not rolled back. A full write/flush
is not proof of consumer receipt, and parser/help/paper-subcommand behavior is not
changed. No new provider, persistence mechanism, workflow, migration or dependency.

## Local evidence, not Windows acceptance

Original-code output contracts:19 failed; intermediate implementation:95 passed.
Separate review:6 failures for lost shared stop, then fixed with original assertions.
Final task modules:114 passed, including38new cases. Related256passed/1native skip.
Detached243passed/0skips with source origins checked. Exact full local verifier:
38024passed/42skipped/334.23s, exit0 and final PASS.
Environment: Linux Python3.13.5/pytest9.0.2/tzdata2026.2, preinstalled tooling,
not locked hosted/Windows acceptance. New native assertions are written/compiled
but NOT executed. Four new subprocess tests use synthetic sessions, not real DBs.

## Safe publication and use

First verify the outer ZIP and PAYLOAD-MANIFEST.json against the independent
hashes in the user-facing handoff. Then read REVIEW.md and all six-file diff.
Upload ONLY this packet into a fresh retained handoff branch from the exact base:
`handoff/wp03-task-output-20260917`, under
`handoffs/wp03-task-output-20260917/`. Keep ordinary hooks/security checks enabled.
Record the full delivery commit; read every payload back from GitHub and recheck
hashes. Do not claim delivery before those steps. No project code is applied in
that handoff branch and no user database or installation is modified.

Only after verified GitHub publication, use a SECOND clean SOURCE checkout at
that exact base, create `feature/wp03-task-output-20260917`, and download the patch
from the immutable delivery commit. Do not start this branch from the handoff tip.
`git apply --check --index implementation.patch`, then `git apply --index`,
`git diff --cached --check`, and `git write-tree` must produce the result tree above.
The exact six changed paths and byte/blob hashes are in source-manifest.json.
Do not force/partially apply or reimplement the patch. A changed upstream base,
pre-existing branch, new mismatch, or access denial is a stop-and-report condition.

Create a NEW draft PR; do not update PR49 or merge main. Required final-head CI:
Offline verification; Native project PostgreSQL (all3partitions plus aggregate);
Research paper bridge; Native project distribution. Inspect actual logs/JUnit,
source-cleanliness steps and artifact hashes. The new38test IDs are pinned here;
the enhanced native case is
`tests/test_project_postgres_dispatch_cli_native.py::test_operator_rounds_stop_restart_failures_and_process_loss`.
Require its complete unskipped JUnit pass and the new `native task output: PASS`
witness, not the witness alone. It performs real empty-turn commit/readback/replay
in the CI disposable database and checks unchanged call reservations. Never use
a user database for that proof. Do not rerun failures until green or expand limits.
Report the fixed delivery commit, PR/head/tree, all results/first failures, artifact
hashes and missing checks. Keep the PR draft for coordinator evidence review.
