# Cleanup FD fixture correction and regression plan

## Finding

CodeGraph traced
`tests/test_research_process_linux.py::test_cleanup_failure_is_reported_not_swallowed`
to `_ContainedSession._cleanup` in
`src/polymarket_alpha_lab/research_process_linux.py`. The test currently assigns
the same synthetic descriptor numbers, 11 and 12, to seven ownership fields,
then calls real cleanup. Those numbers are process-global and can belong to a
pytest logging stream. Cleanup closes the number assigned to the first four
fields, reaches the deliberately stuck supervisor, and `Stuck.kill()` raises
`SystemExit(0)`. A prior full-file diagnostic recorded 119 passed calls and 7
setup skips, then an `OSError` while pytest closed its logging handler during
unconfigure. Retain `g19-exit-diagnostic-run.json` and its first failed attempt
unchanged. Its diagnostic-script SHA-256 is
`ed1520abcf24ea565225405e27fff9830c2ba1ace2b67fc6badc7550973188a6`.

The two relevant source blobs are identical between FX-47
`14f516ca562b218b776aa3f7fb174a39ba4ce66f` and registration
`97091184db35f4f6fe7d06d0cccb6472bcee7d23`. Their current blob IDs are
`67f8ccea58cd297becb896ffa91d17c8257c164f` for
`research_process_linux.py` and
`d19a10b69b751946e83cbaafa3484a84ae854ba1` for the test file. This isolates
the defect to test-owned descriptor setup, while retaining the observed
process-exit failure as evidence.

## Minimum correction node

1. Replace fixed integers with seven simultaneously open, distinct descriptors
   from `os.open(os.devnull, flags)`, using write mode for four `_w` fields and
   read mode for three `_r` fields. Before transferring ownership, compare the
   actual seven field values, in order, to the independently recorded allocated
   values and require seven distinct descriptors with successful `os.fstat`.
   Restoring the old 11/12 assignments generates at most two distinct values
   and fails this focused check before any foreign descriptor can be closed. Keep these
   descriptors test-local. Do not mock `os.close` or change global fixtures.
2. Use a `try/finally` owner for the entire setup and assertion path. If setup
   fails before ownership transfer, close every descriptor in the local setup
   list. After transfer, the finalizer closes only descriptor values still
   present in the session fields and clears them first. `_cleanup` nulls the
   first four fields as it relinquishes them, so the finalizer closes the
   remaining three without retrying stale descriptor numbers. Register an
   independent `ExitStack` callback per owned slot so all callbacks run on a
   partial allocation or assertion failure, and surface unexpected close errors.
3. Have `Stuck.kill()` record that it ran, then raise a pre-created
   `SystemExit(0)` sentinel. Capture the exception and assert object identity and
   `code == 0`, `kill` was reached, and `FakeAdmission.close()` ran. These
   assertions establish that the expected interruption came from the fake
   supervisor after cleanup began. Real valid descriptors prevent `os.close`
   from accidentally closing pytest-owned descriptors.
4. Keep the call to real `_ContainedSession._cleanup(5)` and all existing test
   coverage. Do not change production exception handling.

## Candidate and evidence nodes

- Base a single reviewed, test-only dependency on the verified common ancestor
  `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`. Preserve the original refs.
  In fresh candidates, incorporate this dependency into each proper base before
  replaying its own changes. Registration's base includes its intake commit
  `e65be3e7` and the test dependency. Verify registration's diff against that
  combined base contains only the original documentation, and FX-47's diff
  against its corrected base contains only its original work. Compare trees
  with each old tip: only the specified test file should differ. If a combined
  integration commit is used before the dependency lands, label its full scope.
- Recompute each corrected candidate's source manifest and test inventory.
  Bind every new receipt to that candidate SHA and test-file digest. The
  registration failure remains bound to
  `97091184db35f4f6fe7d06d0cccb6472bcee7d23` and its original inventory. Keep
  the first FX-47 and registration failures as historical attempts, never as
  corrected-candidate evidence.
- Leave both offline runners, the shared serial lock, SSL guard, process
  isolation, and proof checks unchanged. A changed test file requires a new
  candidate manifest, not a bypass, skip, relaxed exit comparison, or edited
  receipt.

## Focused and full acceptance

The old fixed-FD test is the regression baseline. Its file-level evidence is
119 passed calls, 7 setup skips, and a pytest-unconfigure logging-handler
`OSError`. The corrected test must preserve the exact `SystemExit(0)` from the
fake kill and assert kill/admission-close events. Then run the focused node and
the complete `test_research_process_linux.py` file. Require worker, pytest,
and process exits of zero, complete selected inventory, no collection errors
or deselection, and no finalization exception.

After rebinding candidate manifests, run the real all-file offline proof once
for each corrected candidate. Require the runner's exact-head pin, clean
checkout, complete test inventory, matching pytest/process exits, and a stable
successful exit. Run the two proofs serially under the existing lock. Preserve
the first failed attempts append-only.

Standard pytest on an unchanged original head can lawfully reproduce the
historical failure, but cannot establish corrected behavior. A test overlay is
a dirty, changed input and is not same-head proof. A separate ordinary pytest
run is possible on each corrected, clean, committed test-maintenance head. That
is truthful same-head execution for the new SHA, while leaving the full-suite
proof to its unchanged runner and gate. Registration then remains accurately
labelled as docs registration plus test maintenance. The relevant source blobs
are common to both candidates, so the same test-only correction is
independently applicable to each. The original test-head equivalence and
original failure receipts remain untouched.

## Prerequisites

- The shared serial runner lock is available and both offline runners are idle.
- Each candidate is clean and exactly pinned before creating its test-only
  commit.
- The patch and its new source/inventory digests are reviewed and rebound.
- Existing Python/pytest runtime, offline guard, and no-network/no-production
  constraints remain active.
- This plan changes no fixture, rule, runner, repository file, or evidence and
  runs no tests or suites.

Prospective change: one test-local FD-ownership correction, applied separately
to FX-47 and registration, with no production or harness change. The corrected
commit SHAs are unknown until implementation.
