# Patch handoff rule

Owner instruction, 2026-09-13: patches for local-agent handoffs must be committed
as real files in the relevant GitHub repository before the handoff is delivered.
A chat attachment or expiring Actions artifact is not the sole delivery channel.
This rule is intended as the owner's cross-project handoff preference; storing
this file does not change account-level ChatGPT custom instructions or other
repositories automatically.

Every handoff must identify the repository, reachable delivery branch and full
immutable delivery commit, exact patch path, working download URL/command,
SHA256, implementation base, expected resulting tree (when supplied), inspection,
application and verification commands. Read the uploaded file back and verify
its bytes before reporting delivery. Use a separate handoff branch when patch
publication must not modify the implementation baseline. Do not delete that
branch while its files are in use; immutable links must remain reachable.

Before applying, re-read the implementation branch and merged PR status. Already
merged patches are archives, not pending work. Do not reapply them, recreate a PR,
or overwrite a release. Historical replay uses a fresh checkout of the exact base
and git apply --check --index before git apply --index. Stop on any hash, base,
path, tree or application mismatch; do not force, partially apply, or bypass checks.

Finish repository-side work before assigning only genuinely local tasks. Preserve
first failures, rerun results and unexecuted checks separately. Never include
credentials, database directories, backups or unrelated private logs in delivery.
If publishing is denied or fails, report the actual blocker and do not invent
URLs or evade safeguards. This rule changes delivery, not safety or authorization.
