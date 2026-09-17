# WP-05: bind simulation receipts to the original approval

## Status and identity

This is a local, unmerged candidate. GitHub-first publication and final hosted
Windows acceptance are NOT complete. There is no PR number or remote candidate
commit to cite. The required source is the unchanged upstream main commit
`a71ae4d716e8a73bd3614f080f42e5a6968d76f8`, tree
`4d15e1d555f108e19edf19e160593eea2841b28c`. The exact candidate result tree is
`ec17d9401fabdb6a3b7507b5324a4811070c7327`.

Local Git commit IDs in candidate-identity.json are reconstruction bookkeeping,
not fetchable upstream identities. Apply the patch only to the pinned UPSTREAM
base after a verified GitHub handoff has been published and read back.

## Finding and fix

The original paper operator decoded a canonical scenario against the caller's
reviewed input_sha256, but compared its returned receipt against the scenario
object AFTER passing that object to a capture collaborator. A faulty collaborator
that changed both the argument and returned scenario could therefore return a
valid-looking receipt for different quantities, costs or assumptions, with exit0.

Six original-code counterexamples (BTC/ETH crossed with quantity, nested fee or
assumptions changes) returned success when failure was required. The test uses the
existing simulator to recompute a coherent typed receipt; it does not merely send
an obviously invalid result. This is one consistency defect demonstrated by six
scenarios, not six unrelated production defects. The database/capture collaborator
is synthetic in these tests, so this is NOT evidence that the current native
storage normally corrupts records or that hostile Python has been sandboxed.

The existing _receipt helper now compares checksum(encode_paper_scenario(...)) to
the ORIGINAL input_sha256. That string has already been validated by the existing
canonical input reader. It is not recomputed from the possibly altered argument.
The helper still checks the exact receipt type, hard flags and original record ID.
Inspection, which accepts no caller approval hash, retains its existing ID-based
lookup contract. No hash is invented for inspection and no public signature changes.

## Separate same-assistant review

The initial fix passed the old69 + new6 operator cases (75 total). A separate review
added eight cases: six prove that the correct original receipt remains successful
when only the adapter's post-save argument changes; two prove that subsequent
cleanup cannot rewrite the validated projection for capture or inspection. All8
passed without another blocking finding. The first six failing assertions were
retained unchanged. Final two operator modules contain83passing cases,14new.

The separate detached Git worktree executed524related cases in12.74s with its actual
module import origin checked. The development related run passed524cases and
skipped1explicit native case in11.39s. These counts overlap, not additive coverage.
A separate24-case finite compatibility comparison across BTC/ETH, success/negative
receipts, capture/inspection and canonical framing found identical original/new
exit codes, stdout text bytes and empty stderr on normal inputs. This is bounded
compatibility evidence, not a universal guarantee.

AST checks show read_scenario and the output emitter unchanged. The public command
body changes only the keyword passed into its existing receipt validation call.
The old unit modules are exact prefixes. All19previous outer native assertion
nodes remain,21now, plus assertions inside the new child-program string. No SQL,
67-migration ledger, dependency, workflow, simulator, pricing, permission, storage
or stop/drain behavior was changed. No new business data format or file queue.

Full local verification: 38314 passed / 42 skipped in 340.17s; exit0 and full verification PASS (384.785s wrapper including entrypoint/compile checks).
Environment: Linux Python3.13.5 with preinstalled container tooling and a separate
editable venv. This is NOT locked hosted/Windows or real-database acceptance.

## Native proof still pending

The existing test_paper_operator_capture_replay_inspect_and_rejection contains two
new real-process invocations. Each calls the original capture method against its
already-saved BTC/ETH scenario, then deliberately changes ONLY the adapter argument
AFTER the real storage call. The original returned receipt must remain identical.
The original subsequent database checks still verify two simulations, two attempts,
67migrations and original records. This is an explicit same-input replay, not an
automatic retry. The no-model negative receipt is also covered.

The child code is ASCII and compiled. Two offline synthetic invocations checked
its wrapper; no native database or real child execution is inferred from that.
The actual native test remains unexecuted in this continuation. Require the full
unskipped JUnit testcase and complete paper workflow, not its PASS marker alone:

`native paper approved-input binding: PASS; original BTC/ETH receipts survive adapter argument mutation, single replay`

All four final-head workflows remain required: full offline, native three
partitions plus aggregate, paper, and actual distribution. The14new unit cases must
run on Windows in the paper workflow. Existing CI inventories/deadlines are unchanged.
A pass on the original PR53 tree is not acceptance of this candidate.

## Limits and provenance

A mismatch returns the existing paper_operator_operation_failed response, result
null and conservative possible-write metadata. A failed receipt is not a rollback
proof. There is no second capture, output envelope, replacement identity, automatic
repair, file recovery journal or model request. A matching receipt is not independent
authentication of an untrusted store's real writes. Frozen dataclasses emulate
read-only instances, not absolute protection against arbitrary Python mutation:
https://docs.python.org/3.12/library/dataclasses.html#frozen-instances

Source/provenance and minor audit-runner setup issues are recorded separately in
tooling-notes.txt. GitHub write capability was unavailable rather than a candidate
safety refusal; no blocked CAPI diagnostic or prior failure was routed around.
No user credential, .env, user database, old kit or real provider was accessed.

No unresolved blocking finding was found in the executed LOCAL scope. This is a
separate phase/worktree by the same assistant, not a fresh-agent/third-party audit
or absolute no-defect guarantee. Hosted/native acceptance and GitHub publication
remain incomplete. WP05/WP06 PARTIAL, G5/G6/V1 and D1-D3 remain open; PR49 remains
separate and no old failure is waived.
