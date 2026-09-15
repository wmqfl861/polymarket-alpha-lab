# PR #31 fresh-session takeover review (2026-09-15)

Scope: independent review of the already-merged WP-02 allowance / WP-03 dispatch
integration, not a new runtime implementation or a V1 completion declaration.
This reviewer did not participate in the previous implementation context. The
new probe itself received a separate same-reviewer check; no external Claude,
Codex subagent, CodeGraph or formal verification is claimed.

## Pinned implementation

- Repository: `wmqfl861/polymarket-alpha-lab`
- Main reviewed: `038be0c0df91999e6038e8fefe2830022b2780fb`
- PR #31 head: `4b5ed848ccd82d524a2d8f87d24024da5124fa53`
- Implementation tree: `d03437bdae3ba3f4a00652704f8c45d525f8a684`
- CI merge candidate: `2edefc245f4e7259be9937c28fad864646352c6f`
- Audit files only are retained on `review/pr31-takeover-20260915`; the pinned
  delivery commit is recorded in the PR #31 review comment. This branch is NOT
  a new application release and is not an instruction to update a user kit.

The ordinary container could not resolve github.com for git clone. The GitHub
connector supplied artifact 10374120722 instead. Its outer ZIP SHA256 is
`8e9cbe4f3ec98eb699cd63d71a4faad58c5c4fc2128cb007bd5254db079a8b14`;
inner ZIP SHA256 is
`508a042fcb95ef167fb114dcadb01e47194e18c9c74b416d907747973fe1494f`.
All 2916 manifest file hashes were recomputed and matched. The bundle metadata
has the same implementation tree as main. The reviewer did not reconstruct the
entire source Git tree from an original full checkout. Production files in the
extracted snapshot were not changed, and native binaries were not executed.

## New independently authored offline checks

The final exact probe passed **49 tests in 3.74 seconds**, Python 3.13.5 and pytest
9.0.2, using the current container dependencies, NOT the locked Windows setup.
Its integer-accounting test covers **598 combinations**, within the 49 tests;
do not add these counts together. It also checks exact integer types, a 100-task
BTC/ETH policy roundtrip, changed-request rejection, non-new/non-boolean permit
rejection, regular errors and interrupts at reservation/factory/client entry,
malformed replies, exhausted-policy rejection before claims, and inert expired
incomplete replay. Four synthetic rounds each dispatch 100 tasks with eight
workers and limits 1/7/31/100; recreated clients do not gain another permit.

The reservation boundary in those concurrency checks is an in-memory synthetic
atomic stub. These checks are NOT independent SQL concurrency, native process
loss, actual provider pricing, or real model-call tests. They add no business
persistence backend; fixtures never open a database or make a network request.
The fixture DSN is validated by the existing canonical validator, without a key.

First run: **1 failed / 48 passed**, because the reviewer wrote 890 instead of
598 as the expected case counter. All behavioral assertions passed. A separate
finite count confirmed 598; only that bookkeeping assertion was corrected.
A subsequent same-reviewer cleanup replaced the unused dummy DSN string with a
canonical validated local fixture, then the exact published probe was rerun.
`first-run.txt` preserves the first failure; `final-pinned-run.txt` is the exact
published probe's run. No production defect or RED/GREEN source fix is implied.
Python compile verification also passed without writing into the source kit.

Example offline replay from an existing isolated full source checkout at the
implementation commit, with this reviewed test file downloaded separately:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/absolute/source/src python -m pytest -q -p no:cacheprovider /absolute/review/test_pr31_independent_review.py
```

Do not use a user database, start a model provider, apply migrations, copy .local,
change permissions, or patch an installed immutable kit to replay these probes.

## Existing CI evidence independently downloaded and read, NOT rerun

| Gate | Existing run | Actual result |
| --- | --- | --- |
| Locked offline | 34909306155 | 37275 passed / 34 skipped |
| Windows native | 34909306270 | 1538 passed / 2 skipped |
| Windows distribution | 34909306240 | 96 passed / 0 skipped |

Proof archives 10373584923, 10373778240 and 10373173467 were downloaded, hashed
and their actual logs/JUnit inspected. Native budget upgrade/shared-cap/process
loss and rotation recovery cases have no skip/error/failure element. This is
review of prior CI, not new Windows execution in this session. Existing default
skips and the PR #30 PS5.1 first-run reliability limitation remain visible.

Log SHA256:
- Offline: `e4e6632d26b807723c5744ce4e6b301b109e0321023cb970cd1bc21ed26d4cc6`
- Native: `eaff82f30c25746abdb21e39fac19de025126d7dc0a6ebc0c67facf40c80c0f3`
- Distribution: `1928f9c42e970dfd4a7cef21542204f0bf06c61bb74c26e4cd842dd345fea547`

## Conclusion and next work

No new release-blocking defect was identified within this limited code-read and
offline-review scope. This is not assurance of absence of defects or permission
to enable a provider. Reserved fixed charges are not verified invoices; an actual
adapter must still have reviewed fee bounds, inert construction, at most one
provider operation per complete call, no hidden retries, and bounded I/O.

DELIVERY_PLAN.md remains authoritative: WP-01 DONE/G1; WP-02 AWAITING_OWNER;
WP-03 through WP-06 PARTIAL; V1 remains **1/6**. No new gate is closed by this audit.
Continue WP-03's minimal operator entry/stop/restart integration and WP-02's
credential-free adapter design, reusing existing components. D1 provider/model,
D2 input sending permission and D3 enforceable first-run budget remain unknown.
Do not send a local agent to rediscover keys, repeat old acceptance, replay
merged candidates, or route around any prior safety refusal. No local-owner task
is required just to complete this takeover audit.
