# Phase 1 Development Node Acceptance Checklist

Date: 2026-07-12
Status: active acceptance checklist
Scope: pre-commit and pre-push acceptance documentation

## Acceptance Rule

A Phase 1 development node is accepted only when the changed surface is
complete, tested, reviewed, and still inside the Phase 1 boundary. This
checklist summarizes the mandatory evidence from
`docs/quality/phase-1-development-node-quality-gates.md`.

## Required Evidence Before Commit

Each node handoff must record:

- changed paths and ownership boundary;
- implementation summary or documentation-only statement;
- `python -m compileall src tests` result, unless documentation-only;
- targeted test command and result, unless documentation-only;
- full `pytest` result, unless documentation-only;
- `git diff --check` result;
- readonly boundary scan result;
- local Supabase/Postgres persistence check result when durable storage,
  database helpers, fixtures, report history, or configuration changed;
- secret and no-live-trading-field scan result;
- independent read-only review outcome under
  [Project Agent Routing](../../AGENTS.md#project-agent-routing);
- explicit push status: blocked or eligible.

## Must-Pass Checklist

Before a commit is ready:

- [ ] Diff contains only intended files for the node.
- [ ] No unrelated user or parallel-worker changes are included.
- [ ] Compile gate passed, or the node is documented as docs-only.
- [ ] Targeted tests passed, or the node is documented as docs-only.
- [ ] Full `pytest` passed, or the node is documented as docs-only.
- [ ] `git diff --check` passed.
- [ ] Phase 1 readonly boundary scan passed.
- [ ] Local Supabase/Postgres persistence rules passed or were not applicable.
- [ ] Secret scan passed.
- [ ] No live trading, wallet, credential, account, or order-mutation fields
  were introduced.
- [ ] The current revision received an independent read-only `VERDICT: PASS`
  after any required changes were re-reviewed.

## GitHub Push Acceptance

GitHub push is allowed only after:

- all required quality gates pass;
- the current revision has an independent read-only `VERDICT: PASS`;
- the commit excludes unrelated worktree changes;
- no secrets, raw DSNs, credentials, wallet material, account data, hosted
  account details, live-account order ids, or private source payloads are
  present;
- no behavior authorizes live trading, order signing, order submission, order
  cancellation, order replacement, exchange mutation, or account mutation.

If any item is uncertain, the node is not eligible for GitHub push.
