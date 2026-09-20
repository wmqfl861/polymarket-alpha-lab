# Local-agent protocol and explicit uncapped research (WP-02 / WP-03)

## Delivered boundary, not a ready-to-run Codex launcher

The strict **Codex 0.155.1 event-protocol adapter** and explicit
**no-business-monetary-cap execution path** cover one task, a batch and a
rotation. They reuse the original managed PostgreSQL session, immutable requests,
claim-before-model ordering and result capture. No second queue, backend,
monetary reservation or reporting subsystem is introduced.

**A real subprocess transport and pinned restrictive command builder are shipped;
no approved real-provider activation is supplied.** `CodexProcessTransport` starts
one explicitly configured native command and connects it to `CodexExecModel`.
It provides bounded pipe I/O and owned-process cleanup, not filesystem/network,
CLI configuration/persistence or provider-output isolation. The selected-version
profile below has synthetic upstream evidence, not complete isolation approval. The standalone task script has no configured real
client. G2/G3 remain open. See the concrete process section below.

## Permission without a fictitious budget

`UncappedResearchAuthorization` is a copied, canonical application value: ID,
model label, reviewed adapter-contract digest, UTC approval/expiry, 1..100 exact
original `(record_id, request_sha256)` pairs, and explicit true approvals for
research transmission and no monetary ceiling. The finite roster is a resource
bound per intake, not a cumulative business event/cost limit. The original
per-task message/output/call limits remain.

`monetary_cap` and `actual_billed_micros` are **null**, not zero. There is no price,
currency, all-inclusive fee attestation, fake huge allowance or refund. Existing
capped `ModelCallBudget` behavior is unchanged. Selecting both modes, missing an
explicit opt-in, or mismatching a task/model/hash fails closed.

The value alone is **not signed authorization or a durable approval ledger**.
The explicit `create_uncapped_authorization` / `require_durable_audit=True` path
below now stores permission and per-call metadata in the original project database.
Legacy calls without that opt-in remain unaudited. A digest is a reviewed reference,
not executable/provider identity attestation; caller-declared approval and reported
usage do not independently authenticate a human, model or invoice. Raw Codex
streams are not persisted by this audit path.

The explicitly configured owning application supplies the reviewed values below,
not a model response, guessed credential or implicit environment scan:

```python
from pathlib import Path
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_uncapped import UncappedResearchAuthorization
from polymarket_alpha_lab.research_codex_exec import CodexExecModel

permission = UncappedResearchAuthorization(
    authorization_id=reviewed_authorization_id,
    model_id=reviewed_model_id,
    adapter_contract_sha256=reviewed_adapter_contract_sha256,
    approved_at=reviewed_approval_time,
    expires_at=reviewed_expiry_time,
    request_keys=tuple((r.record_id, r.content_sha256) for r in reviewed_requests),
    no_monetary_cap_approved=True,
    research_data_send_approved=True,
)

def inert_factory(team_id):
    return CodexExecModel(model_id=reviewed_model_id,
                          transport=approved_transport_factory(team_id))

with ProjectPostgres(Path(actual_project_root)).session() as research:
    receipt = research.run_uncapped_research(
        request=reviewed_requests[0], authorization=permission,
        model_factory=inert_factory,
        allow_model_calls=True, allow_uncapped_costs=True,
    )
```

This is an integration example requiring reviewed values and a separately proven
transport, **not an executable real-model setup command**. Managed sessions retain
DSN validation, instance binding and lifecycle draining. No user instance is
opened, migrated or upgraded by this source change.

Existing batch/rotation methods accept `uncapped_authorization=permission` and
`allow_uncapped_costs=True` with `allow_model_calls=True`, the inert factory and
original limits/stop token. Omit `model_budget_id`. Batch scope validates the
selected pending prefix; rotation scope validates every pending roster member
before reserving a new turn. Permission already expired at that preflight
rejects a new reservation. It may expire during/after a committed reservation;
then task admission fails without a model call and the original turn remains.
Same-turn replay remains inert. The standalone CLI is unchanged.

## Execution, expiry and uncertain outcomes

A new original execution claim commits before real factory/client entry. No
monetary permit is appropriate here. The wrapper checks permission time, cutoff,
stop, messages, output request and call count; it rechecks time/stop after factory
construction. A zero-model evidence rejection never constructs a real client.
Callbacks remain trusted in-process code, not malicious-code sandboxes.

Permission expiry uses the application UTC clock. The original DB claim also
checks its task window with the DB clock. This is not a new DB approval clock,
hard process deadline, universal cancellation or actual provider-request counter.
In-flight responses may arrive later; the transport must separately enforce I/O,
cleanup and absence of hidden retries/tools/fallback.

Every client/decoder error closes that wrapper. Ordinary errors become fixed
codes; interrupts propagate. No automatic resend, repair, resume, refund or task-ID
substitution. Original failures are captured normally. Process loss after claim
leaves **incomplete**, never age-based reclaim. Exact original histories replay
under expired permission without model entry; lookup errors do not create work.

## Codex protocol contract

`CodexExecInput` preserves exact message text, explicit model label, unchanged
output request and closed action schema. Its prompt encloses the transcript plus
existing research tool definitions. Those are action **data**, not executable
CLI tools. The adapter does not read files, launch a process or perform I/O.

The decoder requires one successful thread/turn, supported complete items, one
final action object and the pinned five-field usage schema. Unknown/tool/error
events, nonzero exits, missing/duplicate terminal events, unfinished items,
invalid usage/UTF-8/JSON/arguments and excess bytes/events fail closed. LF/CRLF
separate records; Unicode within JSON does not. Generated tool IDs do not collide
across calls. The original agent still verifies citations against read evidence.

Returned `total_tokens` is reported input plus output; cached-input and reasoning
subsets are not double counted. It is not independent token or invoice verification.
Failed streams have no validated usage receipt: zero original counters must not
be interpreted as proof of no outside usage/charge. Reported output overrun is
rejected **after** the operation, not a pre-request provider cap. Raw stderr and
reasoning are not copied to the research result or a new durable store.

## Actual upstream investigation and remaining activation blockers

Checked 2026-09-19 against official **0.155.1**, source
`be2951ea34f0d295ed0becf97079f92fa5f6950e`. A separately exported official Linux
binary matched its release size/digest. Probes used new private home/work
folders, synthetic prompts and a loopback in-memory Responses service. No real
provider, API token, user login/database or market input was used. These are
experiments, not a production launcher or Windows CLI acceptance.

- `--ephemeral` still created local SQLite state files in the observed process.
  Pinned source initializes state storage separately from rollout persistence;
  `sqlite` is a removed feature. No test-only in-memory flag or unsafe bypass is
  adopted as a product persistence solution.
- Disabling shell left other tools in the initial request. Additional settings
  yielded an empty tool list in one sample, not proof of ancestor-config,
  hooks/plugins/MCP or filesystem isolation.
- A corrected probe exited zero and completed but also emitted a Code Mode error
  item. The decoder rejects it: exit zero alone is not sufficient. That probe
  does not certify an admissible host.
- Pinned request construction did not establish a provider-enforced output limit
  matching this project's interface. No undocumented flag is invented. Default
  documented HTTP/SSE retries cannot be counted as one request per CLI start.

Host persistence/context isolation, no-hidden-operation evidence, resource and
cleanup enforcement, actual model identity and authorization/usage audit remain
required before a launcher can be enabled. No user-local task is delegated to
bypass these unresolved contracts.

Primary references (checked 2026-09-19):
- https://developers.openai.com/codex/noninteractive/
- https://developers.openai.com/codex/config-reference/
- https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/exec/src/exec_events.rs
- https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/core/src/client.rs
- https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/rollout/src/state_db.rs

## Evidence and separate review

Initial focused **2 failed / 175 passed**: an authorization model lone surrogate
was incorrectly accepted (fixed); one stale-evidence fixture was incorrectly
constructed (corrected, same zero-call expectation). Related tests then passed570.

Separate same-assistant review first reproduced **8 failed / 9 passed**:
Unicode/CR-only framing, reported output overrun, empty messages entering a
factory, expired permission consuming a rotation. Original counterexamples remain.
An intermediate **1 failed / 586 passed** used an unpatched static-time fixture;
after providing its clock, the unchanged expectations passed **587** tests.
This is not external/fresh-agent review or a guarantee of no defects.

The native proof `tests/test_project_postgres_uncapped_native.py` uses current67
migrations, BTC/ETH synthetic Codex events, stop/two turns/restart, duplicate claims,
expired replay, zero fake permits, real child-process loss and older/incomplete
history preservation. Windows dispatch appends this and three new unit modules;
all original selectors/deadlines/permissions remain. Exact final tree, full/hosted
results, first failures and unexecuted checks belong in the implementation PR.

A subsequent packaging review reproduced one omitted integration-guide test, then
added the guide to the existing optional distribution list. Older kits remain
verifiable; the counterexample is retained in the review test module. A third
synthetic CLI probe still emitted the missing Code Mode companion error despite
an explicit feature setting; the decoder rejected it without stripping the error.
This is an incomplete probe runtime, not proof every Codex installation fails.


### Additional separate self-review: nested Unicode (2026-09-19)

A fresh review of PR #60 head `61b260dd41285e59b5587381bacd025f1bbf6385`
reproduced **12 failing / 3 passing** counterexamples: UTF-8-valid JSON can contain
an escaped unpaired surrogate that becomes invalid text only after a nested
parse. Validating only the message/stream envelope admitted four invalid input
forms and eight invalid evidence-action argument forms. The protocol now checks
all decoded input strings/keys and the original validator's decoded action
arguments before a host call or valid reply. No character replacement, transcript
re-encoding, precision conversion or relaxation of the action schema is used.
Valid Chinese text, emoji, surrogate pairs and Decimal-bearing original input
remain byte-for-byte unchanged. These cases stay in the existing review module
already selected by Windows native CI. No host, database codec or migration changes.

This is another separate same-assistant source/negative-test review, not external
certification. The earlier local exploratory full run overlapped review edits and
is NOT final-tree acceptance evidence. Final frozen-tree local and hosted results,
source hashes and any remaining failures are recorded in the PR before merge.
Four additional BTC/ETH capture/replay checks retain the original failed result
without invalid summary text or a second host entry. Nineteen added regressions
cover this correction in total. All real-host/persistence/usage acceptance limits
above remain open.


### Final-head kit failure and test scheduling correction

The first additional-review head `fa19a5e53c4d34078e25384f3e5ed7ef9bea241d`
passed full offline verification but its actual Windows kit run `35430546829`
failed: 426 passed / 1 failed. The original first `capture-paper` returned an
operation error; the post-command clock was 0.384006 seconds past the original
cutoff, with no paper receipt returned. This suggests an exhausted prospective
window but does not identify the underlying database exception or prove the
historical PS5.1 cause. The raw failed log/XML and hashes remain in PR #60;
no later green run retroactively changes that failure.

The test recipe had put six negative admission/absence commands inside the
four original requests' finite prospective window. The corrected recipe first
runs those SAME command kinds, exit/permission/absence assertions and payload
validation against separately named, never-admitted control requests. Only after
all six checks succeed does it create the four original `packaged-*` requests,
then positively admit their exact payloads once. Controls share the batch/budget
lookup IDs so absence is checked before the actual admission, but their task IDs
and hashes are distinct. They never become research inputs or durable records.
This is a test scheduling correction, not a runtime deadline or data rewrite.

The original UTC minute-floor +2-minute opening, cutoff one second before it,
60-second command and 300-second recipe timeouts, four original tasks, two turns,
stop/restart, seven permits, all capture/settlement/replay/cold-recovery assertions
and real native clocks remain. There is no retry, input refresh, artificial wait
for a favorable clock phase, disabled guard, or startup prewarm. Nine offline
ordering/early-failure counterexamples exercise three clock phases and every
negative-check failure position before any positive operation. The final fixed
revision must pass full local and hosted verification before leaving draft.
This removes avoidable preflight work from the sample window, not all host timing
variability; real-host, usage and V1 acceptance limitations above still apply.


## Durable uncapped audit (WP-02 / WP-03; 2026-09-19)

The earlier application-only path is retained as explicit compatibility. New
real-host integration must use **`require_durable_audit=True`**. This mode adds
same-project PostgreSQL authorization, call-start and outcome records; it does
not supply a CLI launcher, change a provider contract or authenticate a human's
approval. The old `UncappedResearchAuthorization` payload and original request /
claim / result codecs remain unchanged. No raw transcript, response, exception,
reasoning or credential is stored in the new audit tables.

### Explicit setup and use

After preparing the reviewed `permission` and exact `reviewed_requests` shown
above, and with a separately verified inert factory supplied by the application:

```python
with ProjectPostgres(Path(actual_project_root)).session() as research:
    saved = research.create_uncapped_authorization(
        authorization=permission, allow_authorization_write=True,
    )
    original = research.inspect_uncapped_authorization(
        authorization_id=permission.authorization_id,
    )
    assert original == saved
    receipt = research.run_uncapped_research(
        request=reviewed_requests[0], authorization=original.authorization,
        model_factory=inert_factory, allow_model_calls=True,
        allow_uncapped_costs=True, require_durable_audit=True,
    )
    audit = research.inspect_uncapped_calls(
        record_id=reviewed_requests[0].record_id,
    ).to_dict()
```

These are application integration calls requiring reviewed inputs, not instructions
to run on an existing user installation. Current-source migration 68 must first
be installed on an explicitly authorized instance. There is no credential search,
automatic migration, default model or inferred authorization. The same
`require_durable_audit=True` keyword propagates through existing batch and rotation
APIs with `uncapped_authorization=permission` and the original two opt-ins. A new
rotation verifies the stored authorization before committing its selection; an
existing turn still replays without work. The standalone CLI remains unchanged.

Creating the same authorization ID with exact content returns its original
DB-stamped receipt, even after expiry. Different content under that ID conflicts;
it is never updated or silently reapproved. The stored receipt copies the original
value. DB time rejects newly inserted future/expired approval windows. The audit
flag must be an exact Boolean and cannot be attached to the legacy/capped mode.
A required but absent/mismatched audit authorization fails before a task claim.

### Ordered, separately immutable evidence

Every audited `complete` first commits a start row bound to the original claimed
request, authorization hash, call ordinal, exact message hash/UTF-8 byte length
and requested output ceiling. Only then may the real factory/client be entered.
The DB checks current authorization/cutoff, request/model/roster binding and
strict per-task order. A duplicate ordinal is not permission to send again; a
failed or unresolved preceding call cannot admit the next one. There are no
monetary amounts or fictitious budget reservations.

A returned **structurally validated** `ResearchModelReply` gets a separate outcome
with its deterministic hash and reported aggregate token count. Outcome COMMIT
and cleanup must succeed before the reply is released to the original research
loop. `returned` does not mean the loop accepted the actions/citations, that a
forecast completed, or that final research capture succeeded. Those decisions
remain in the original loop/result. Failures and interrupts record fixed statuses
with usage/hash null. No automatic retry or second contradictory outcome is used.

The start and returned outcome receipts are independently revalidated against the
current request/reply at the wrapper boundary. A corrupt receipt suppresses client
entry or the returned reply. Outcome-logging failure never converts a failed
operation to success; an original interrupt remains primary, while a new interrupt
during ordinary failure logging propagates instead of being swallowed.

Process loss after a committed start leaves a row without an outcome: **unknown**,
not zero external usage and not proof that no provider operation occurred. A lost
start acknowledgement never enters the factory. A lost outcome acknowledgement
can leave `returned` in the audit but a failed research result; both are truthful
observations at different boundaries. Inspect the original IDs; do not resume the
model. Existing completed/incomplete histories replay unchanged, with no audit
backfill, no replacement task and no new client. Expiry does not cancel already
admitted I/O; start stamping is not a universal socket/commit deadline.

### Interpretation and limitations

`inspect_uncapped_calls` uses one bounded read-only snapshot (at most 32 starts).
It validates identities, order, outcome bindings and time sequence before output.
No-audit history is `no_audited_calls`, not proof of no calls. Missing, failed and
interrupted observations retain unknown usage; only structurally validated replies
contribute to `reported_tokens_known_subset`, which is null if none exist.
`provider_submission_count` and `actual_billed_micros` always remain null here.
This is reported application usage, not independently verified provider billing,
retry accounting, cryptographic approval, model identity or a record of every
pre-admission refusal. Cache/reasoning breakdowns are not newly inferred.

The explicit compatibility default remains unaudited. This feature is not a
sandbox against an owner or code deliberately choosing a legacy API, changing
DB privileges, forging reports or running a CLI separately. Actual host isolation,
no-hidden-operation evidence and real usage/provider verification remain mandatory
before activation. No real host is certified by adding these records.

Migration `20260919000000_research_uncapped_audit.sql` is tail 68. The original 67
SQL files are byte-identical; old 66-to-67 backup/paper proofs retain their original
catalog targets. New native tests use an isolated 67-to-68 upgrade with existing
history, concurrent originals, no fake money permits, actual COMMIT-acknowledgement
loss and child-process death. Existing SELECT/INSERT-only application grants and
mutation-rejection triggers are reused; no new database backend or user-instance
operation. Fixed-version installation/upgrade acceptance remains WP-06.

Separate same-assistant review reproduced nine issues before their corrections:
new interrupts during failure logging, returned receipt binding, and next-call
start preceding the previous recorded outcome. The original failing tests remain.
The first corrected run also exposed two synthetic-clock fixtures inconsistent
with the new ordering rule; fixtures were corrected, not the rule. Final frozen
full/hosted results, native case inventories, first failures and exact source tree
are recorded in the implementation PR. This is self-review, not external review
or a zero-defect guarantee. G2/G3 and the full V1 milestone remain open.

PostgreSQL references checked 2026-09-19:
https://www.postgresql.org/docs/17/ddl-constraints.html
https://www.postgresql.org/docs/17/transaction-iso.html

The first complete candidate run reported 1 failure / 38,964 passes / 44 skips:
the explicit native-workflow inventory expected the prior file list. All three
new audit modules were already selected by the workflow. Its inventory assertion
now explicitly adds those modules while preserving every original selector and
partition requirement. This was not a skipped test or removed assertion; the
final tree must pass another complete verification.

### PR61 native failures and follow-up evidence

Head `5bbcb4b5aee692de2cee1684122fba44204b147a` passed local/offline verification
but failed two hosted gates. The new native audit test reached its last assertion:
following child loss the engine remained running, and the parent correctly borrowed
it. The fixture incorrectly expected a borrower to stop it. The corrected fixture
explicitly stops the CI-owned engine after the crash, then enters a new owning
session to prove actual restart and the SAME unknown/incomplete/history readback.
Production ownership behavior and every original audit assertion remain unchanged.

The separate whole-kit run raised TimeoutExpired after the original 300 seconds
waiting for captured output. The parent stack does not identify the child's stage
or prove whether it was alive or a descendant retained a pipe. The original failed
run and logs are retained in PR61. This is not the earlier first-paper cutoff failure
or a demonstrated PS5.1 root cause, and is not retrospectively counted as passed.

The TEST recipe now emits at most 256 fixed-name elapsed-time stage records into
an 8-KiB-bounded readback of a fresh private test file, independently of stdout/stderr
EOF. Summaries contain no paths, commands, environment, business IDs or raw output.
Invalid/missing/torn/oversize records are explicit. Observation occurs AFTER the
original subprocess.run has returned/raised and performed its cleanup, not exactly
at the timer boundary; an absent final marker is not proof of a specific cause.
Reporting or diagnostic cleanup errors cannot replace the original result/exception.
The parent launcher is omitted from the inline child command (it is never called
there), retaining every child function/statement while staying under the existing
Windows command-line size assertion. No application/module is imported from the
checkout inside the kit. Test stage file I/O can perturb timings and is not a
hard-deadline, descendant-supervision or reliability certificate.

The original 300-second recipe/60-second commands, original business cutoff,
all test operations and assertions remain. No CI retry, startup prewarm, favorable
clock-phase wait, sample refresh or deadline extension is introduced. Stage evidence
is diagnostic, not itself a runtime repair or permission to close the historical
failure. Final revised-source gate results and remaining risk stay in PR61.

Python subprocess.run timeout/cleanup and monotonic clock contracts checked
2026-09-19: https://docs.python.org/3.12/library/subprocess.html
https://docs.python.org/3.12/library/time.html#time.monotonic_ns

### PR #61 follow-up: explicit whole-flow test timing profile

The instrumented `c4fe3a07` kit run `35435161357` returned a failed second paper
capture after its original cutoff (one earlier receipt existed). Its stage trace
shows about 67 seconds from actual sample binding to that return. The old fixture
used `floor(now to minute) + 2 minutes`, leaving **more than 59 but at most 119
seconds** before cutoff. A multi-command two-turn/restart acceptance sequence was
therefore competing with its clock phase; this was not a promised production
one-minute service-level objective. The older 300-second timeout remains a
separate failure whose underlying cause has not been established.

This revision deliberately changes the **test timing profile**, not the runtime
admission rules: choose the earliest whole-minute opening whose cutoff is at
least 120 seconds after the actual binding time. The resulting initial sample
window is `[120, 180)` seconds. Construct each actual input once, after negative
preflight, with no favorable-phase wait, backdating, extension, replacement or
retry. All real minute-close waits, original business operations and 68 original
recipe assertions remain; production late-input/DB-cutoff checks are unchanged.

The enclosing test recipe's supervision limit is explicitly **420 seconds,
previously 300**, to include the increased initial horizon and real settlement
wait. The individual 60-second operator commands, PowerShell checks, 180-second
diagnostic watchdog and 20-minute CI job limits are unchanged. This is not a
claim that a test completed within the former 300-second limit, a repair of the
historical PS5.1 issue, or an unlimited-runtime guarantee. Future overruns still
fail. Sixty original-clock-phase counterexamples failed before this change and
pass after it; fractional-second, date-rollover and exact-minute controls also
check the earliest eligible candle. The final source tests, Windows result and
any remaining failure are retained in PR #61 before any merge.


## Concrete process execution (WP-02 / WP-03, 2026-09-19)

`research_process.run_research_process` executes one **explicit native image**;
`research_codex_process.CodexProcessTransport` supplies its returned bytes to the
existing strict event decoder. This replaces the missing process-operation layer,
not the remaining approved-vendor-profile or real-use acceptance work.

### Exact application inputs, no discovery

An immutable `ResearchProcessSpec` supplies absolute executable/working-directory
paths, a tuple of arguments, the complete environment as key/value tuples, expected
SHA256 of the executable image, positive operation/cleanup time limits and bounded
stdin/stdout/stderr byte limits. Construction is inert. `allow_process_start=True`
is required at the transport or direct execution boundary. There is no shell,
PATH resolution, inherited parent environment, `.env`/credential lookup, automatic
installation, health request, repair prompt, fallback or process retry.

Image size, native signature and expected hash are checked before launch. These
checks bind the chosen file, **not its DLLs/libraries, imported code, configuration,
model identity or external billing**. The subsequent path-based launch is not an
atomic defense against a concurrent file replacement. The application must use a
protected, reviewed installation and separately verify every relevant dependency
and configuration. Repr hides command/environment/stdout; it does not secure memory
or redact arbitrary provider-echoed content returned as intended stdout.

The application supplies an inert `prepare_command(request)` function to
`CodexProcessTransport`. It receives a copied `CodexExecInput` with explicit model,
unchanged output-token request and output schema. It must choose the reviewed
version's exact arguments/configuration, not accept command text from a model.
The original prompt is snapshotted before calling that function, then sent unchanged
to stdin. No command is silently modified to evade operator or organization policy.

```python
from polymarket_alpha_lab.research_codex_process import CodexProcessTransport
from polymarket_alpha_lab.research_codex_exec import CodexExecModel
from polymarket_alpha_lab.research_process import ResearchProcessSpec

# prepare_reviewed_command must return a ResearchProcessSpec derived solely from
# the owning application's reviewed native installation/configuration contract.
# It must honor request.model_id, request.output_schema_json and the original
# output request; the process layer does not invent or certify Codex flags.
def inert_factory(team_id):
    return CodexExecModel(
        model_id=reviewed_model_id,
        transport=CodexProcessTransport(
            prepare_command=prepare_reviewed_command,
            allow_process_start=True,
            stop=shared_stop_token,
        ),
    )

# Use the existing managed run_uncapped_research API with stored authorization,
# both model/cost opt-ins and require_durable_audit=True. This keeps the original
# DB claim and audited call start committed before prepare_command/process entry.
```

This is an application assembly contract, **not a ready-to-run Codex login or
real-research command**. No default profile is supplied while the documented
vendor persistence, context/tool configuration, retry and output-bound questions
remain unresolved. A `.cmd`/`.bat`/shell launcher is not accepted as a native
image. Explicit environment values are private application inputs, not an
invitation to put secrets into chat, source files, arguments or retained evidence.

### Supervision and ownership

Stdin writes and both output reads are nonblocking binary operations in the
calling thread; no background pipe-reader thread, output spool or business file
journal is created. Reads are bounded and interleaved so a flooded pipe cannot
starve stop/deadline checks. Stderr is counted and discarded, never decoded or
included in error text. Exceeding either output cap fails the operation. An exact
cap is allowed. Temporary write backpressure is not mistaken for truncated input.

The shared stop token is checked before and during execution. Ordinary failures
are fixed-code errors; KeyboardInterrupt/SystemExit propagate after cleanup.
Termination of the owned domain is attempted on exit, including success, so
successful leader exit does not intentionally leave owned helpers running.
An exit-zero leader whose descendants retain output pipes still hits the existing
operation deadline rather than waiting forever for EOF. Cleanup errors suppress
success and do not launch replacement work. A transport error permanently closes
that transport; the existing model wrapper also closes on protocol errors.

| Platform | Ownership and explicit limitations |
| --- | --- |
| Windows x64, Python >=3.12 | Create native child **suspended**, give it only the three allowed pipe handles, assign it to an anonymous parent-owned **kill-on-close Job Object**, then resume. Assignment/resume failures do not execute the target or fall back. Cleanup terminates the job and waits for its active count and leader exit. After assignment, parent loss closes the noninherited job handle. Nested-job denial stays a failure; no breakaway or security-policy change. |
| Linux | A new cooperative process group owns the launched command and nonescaping descendants. Keep the leader unreaped until terminating its group to avoid targeting a reused group ID. Reject a pre-existing SIGCHLD handler rather than replacing it. This is **not** containment of malicious `setsid`/group escape or a guarantee of cleanup after the Python parent is killed. |

Timeout accounting begins before image verification, but OS file access, process
creation, kernel teardown and a stalled Python caller can exceed configured time.
Operation time plus a separate cleanup wait is **not a universal wall-clock,
CPU/memory, process-count or provider-charge bound**. A failed cleanup means the
final state is uncertain, not proof every process has gone. Process groups/Job
Objects are not filesystem/network or credential sandboxes. Other in-process
launchers must not use broad handle inheritance during the Windows inheritance
window. No malicious in-process isolation is claimed.

Cancellation or timeout after transmission may still have incurred provider
usage; the original audit records failure/unknown and never refunds or resends.
Success here establishes bounded local execution and a valid process exit, not
one billable provider submission, authentic usage, valid citations or a complete
research forecast. The original decoder and research agent apply those separate
checks unchanged.

### Verification and remaining acceptance

Real synthetic Python processes exercise exact stdin/output, clean explicit
environment/cwd/argument handling, large input and backpressure, stdout/stderr
floods, silence, nonzero exit, closed/descendant-held pipes, stop, interrupts,
cleanup failure and descendants after leader success. Windows native selection
also executes structure layout, suspended assignment/resume rejection and parent
loss; Linux-only group/reaping checks are explicitly skipped there. The existing
native audit proof adds BTC/ETH **real database plus real subprocess** calls:
start metadata is visible through another DB transaction before each launch,
reported replies are recorded, stop/restart preserves history and original-ID
replay does not construct or launch another client. All inputs/providers are
synthetic; no user database or genuine model service is involved.

A separate same-assistant review reproduced four initial boundary failures:
zero-write backpressure, arbitrary error text, interrupt precedence and Windows
handle-close failure. A later two-case review reproduced interrupted partial
launch being masked by a secondary cleanup error. Original counterexamples stay.
One test fixture initially timed out waiting for a nested interpreter's site
startup; the stdlib-only synthetic commands now use `-I -S`, with unchanged
production and test operation deadlines. A tool-execution timeout and local venv
setuptools-path setup failure are separate from completed pytest results.
Exact frozen/full/hosted results and any further failures are recorded in the
implementation PR; Linux results do not substitute for actual Windows execution.

References checked 2026-09-19:
- https://docs.python.org/3.12/library/subprocess.html
- https://docs.python.org/3.12/library/os.html#os.set_blocking
- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw
- https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject

### Continuation review: cancellation and handle ownership

A separate continuation of PR #62 at original head
`f5eebf9a56d3b9020e9305188fffb89a66383231` reproduced nineteen negative cases:
four first-interruption/cleanup-error cases, six descriptor-reuse cases, six
Windows handle/thread-close cases, two repeat-cleanup cases and a real Linux
FIFO-image check. The original direct process tests passed 72 with four Windows
skips; these additional counterexamples still failed and were corrected.

Cleanup now retains the first KeyboardInterrupt/SystemExit even when a later
wait fails or its deadline expires, and still attempts each owned close. Both
pipe descriptors and Windows handles are relinquished before close: the OS can
release and reuse a number before Python receives cancellation. Neither a
subsequent cleanup nor constructor-finally closes that stale number again.
An error from close is not retried by number and is not proof of successful
reclamation. This is a specific ownership boundary, not a promise covering every
possible asynchronous Python/OS interruption point.

On Linux, image validation opens nonblocking before fstat so a FIFO without a
writer is immediately rejected as nonregular instead of blocking validation.
Regular native images retain their original digest, size and magic checks.
Kernel/file operations can still exceed the supervision deadline; this does not
create a universal wall-clock guarantee.

The Windows native integration fixture explicitly expects `captured` on first
execution and `already_captured` on replay, comparing every other receipt field
unchanged. Short named test IDs preserve the entire 200KB binary fixture while
avoiding a Windows environment-value overflow before the test could run.
No payload, production semantics, test deadline or workflow selector is relaxed.
The original failed native run remains evidence, not a passed earlier version.

The existing parent-loss proof observes the target **after Job assignment**.
The suspended-create/assign sequence is not atomic with respect to hard parent
termination before assignment; a suspended process may remain in that window.
No target instructions run before assignment/resume, but this is not complete
parent-loss containment at every startup instruction. A selected real CLI profile
must account for this and the existing filesystem/network/persistence limits.

The corrected ten-file local regression passed 380 with five platform/opt-in
skips. Final frozen full results and actual Windows case inventories belong to
the exact implementation PR revision; no old-head success replaces them.
This review is by the same assistant in a separate pass, not an external reviewer.
Primary reference for close/reuse behavior: https://peps.python.org/pep-0475/


## Pinned restrictive command profile (WP-02; real activation still blocked)

`research_codex_profile.CodexExecProfile` constructs one closed command for the
explicit model label **gpt-5.6-sol**, Codex **0.155.1**. No fallback model/version,
extra flags, shell, config loader, downloader, credential lookup or business store
is added. This is an executable command builder, not a provider-authentication
proxy or an assertion that the declared model label is the real serving model.

The caller supplies an original `ResearchProcessSpec` containing only its ONE
native image, explicit working directory, whole environment, image SHA256 and I/O
limits, plus an exact schema-file path and explicit gateway URL. Only path-valued
HOME/CODEX_HOME, USERPROFILE, SYSTEMROOT/WINDIR and TMPDIR/TMP/TEMP environment
entries are admitted; HOME and CODEX_HOME are required. Parent variables, PATH,
proxies, loader overrides, credentials and token-variable discovery are not
inherited. The URL permits HTTPS or literal `http://127.0.0.1:<port>` only, with no
userinfo/query/fragment. This syntax restriction does not authenticate a gateway
or redact secrets a caller incorrectly places in a path.

The generated `exec` command uses strict config, ignores the selected user config,
uses ephemeral mode, JSON events, no colors, the supplied output schema and stdin.
Closed TOML overrides request max reasoning/default service tier, read-only tool
sandbox, no interactive approvals, no project documents/environment injection,
no history/memories/skills instructions/hooks/plugins/MCP/app/shell/search tools,
no telemetry/update checks, and zero HTTP/SSE retries on the one explicit Responses
gateway. These are configured switches, **not a blanket guarantee about inherited
managed configuration or all model-dependent tool behavior**. Required organization
policies are not bypassed: no ignore-rules, bypass-hook-trust or dangerous sandbox
flag is used. An empty config table is not an effective-config certificate.

The existing action schema must match byte-for-byte at command preparation. Its
read is size-bounded and regular-file checked; it is not recreated or repaired.
The approved prompt remains exact stdin bytes, never an argument or journal.
There is no invented provider-side `max_output_tokens` flag: the original decoder
still rejects REPORTED output overrun only after the operation. Neither image nor
schema verification closes concurrent path replacement, ancestor symlinks,
dependency loading or malicious in-process mutation. A separately reviewed
execution boundary is still required for real inputs.

`profile.contract_sha256` covers the complete declared process/environment,
version/model/URL/schema path, exact action schema and closed settings. It returns
only a hash, not private paths or transcript. `codex_profile_factory` requires the
same model and profile digest in an explicit copied uncapped authorization before
any schema read or process launch, then supplies a fresh client per BTC/ETH task.
Use it with the existing STORED authorization and `require_durable_audit=True`
execution path. It does not create approval, waive expiry/request-roster checks,
or make the legacy unaudited API an approved shortcut. The caller must supply
that SAME reviewed authorization to both factory construction and execution.

### Explicit executable size compatibility

The official full Linux package's main image is **269,273,536 bytes**, larger
than the original 256 MiB image ceiling. `ResearchProcessSpec.max_executable_bytes`
now permits an explicitly declared ceiling up to **512 MiB**, retaining **256 MiB
as the unchanged default**. No bound is inferred from a file or automatically
raised after rejection. Both initial file size and actual read bytes enforce the
selected limit; regular/native-image and SHA256 checks remain. This pins the
selected image, not all companions/dependencies or filesystem state. Old callers
retain their original ceiling; explicit limits also enter the profile digest.

### Actual pinned upstream experiment and limits

Source `be2951ea34f0d295ed0becf97079f92fa5f6950e` was obtained as a complete
blob-verified public archive; the full public Linux release package was downloaded
without execution, size/SHA256 checked, then tested only with synthetic data,
new temporary HOME/CODEX_HOME/work directories and an in-memory loopback service.
No user files/authentication or real model/market requests were used. Unlike the
earlier standalone-image probe, this full package includes its Code Mode companion.

Package: `codex-package-x86_64-unknown-linux-musl.tar.gz`, 138,838,055 bytes,
SHA256 `a65b895c6ac1a73629bbe4b864640c86133e94a43b4d67b3103044e1a306d5a2`.
Main image SHA256 `0753dfe1d8b87a52436deb13eb1c549661ef4c84fee2c5aa688385eebeccb761`;
companion SHA256 `210ab8ebaebf4bc1421d9e30339c858354ca35fa91e2f87f4c6204e5382f8a63`.
These are the observed Linux package, not Windows artifact hashes or a reproducible
build attestation. Binaries are not added to this repository/distribution.

Five opt-in tests use the ACTUAL process transport plus strict decoder: success,
429, 500, malformed action and unapproved tool action. Each observed exactly one
loopback POST; request body contained the intended model label and exact schema,
no offered tool list or Authorization header, the approved prompt sentinel and
neither the synthetic parent instruction nor hostile user-config sentinel. Valid
reply reports 120 tokens; every invalid scenario closes the original client and
never resends. This is not proof of arbitrary managed-config isolation, every
possible network operation, actual provider billing or Windows CLI acceptance.

**Ephemeral mode still produced SQLite state files in these successful probes.**
This is explicit negative evidence, not an allowed project persistence backend.
No real research profile is activated; no flags are invented or errors suppressed
to pretend zero-persistence. Next resolve CLI-owned state/context isolation within
the owner's persistence rules (or verify an allowed alternative CLI) before real
activation. An empty working directory is not proof of no other filesystem writes.

The native test `tests/test_research_codex_profile_native.py` is skipped unless
`POLYMARKET_ALPHA_LAB_TEST_CODEX_PROFILE_NATIVE=1` and an explicitly supplied
`POLYMARKET_ALPHA_LAB_TEST_CODEX_PACKAGE_ROOT` points to that hash-checked Linux
package. It downloads nothing; the sanitized full verifier removes the opt-in.
Its optional tests are distinct from default Windows/native database checks.
The two new unit/review modules run in the existing Windows dispatch partition.

### Separate self-review and retained first failures

The original image check was directly observed rejecting the full official image
before launch. Six interface acceptance tests were RED before adding the explicit
field; those missing-field failures are not six separate production defects.
The first profile test run had 2 failed/48 passed because its fixture used the
old synthetic model and imported the base instead of audit harness. Both fixture
mistakes were corrected; the original expected audited behavior remained.

The first full-package diagnostic stream contained deprecated-feature/unknown-model
errors despite exit zero. The decoder correctly refused it. The final profile
uses the current `hooks` switch and the explicitly reviewed model metadata; no
error item is stripped. A distinct self-review then reproduced 4 failures/6valid
controls: JSON surrogate-pair escaping was invalid TOML for non-BMP URL text, and
schema-close errors could mask original interruptions. UTF-8 scalar serialization
and original-cancellation precedence fix them without replacing approved text,
retrying a descriptor close or weakening their assertions.

Final local/hosted exact-tree tests, independent frozen-copy review, original
failure logs and unexecuted items are recorded in the implementation PR. This is
same-assistant separate review, not an external/fresh-agent audit or guarantee
of zero defects. All 68 SQL files, original audit/request codecs and dependencies
remain unchanged; no user installation/database/credential or live-order work.


The first candidate's hosted selection exposed an existing coverage gap: changes
to the shipped Codex/process modules alone did not trigger the actual-kit job.
Eight path-selection counterexamples failed before the correction. The existing
kit workflow now includes these source/test families and this guide, retaining
all prior filters, test commands, deadlines and permissions. No manual rerun or
old-head success substitutes for the corrected final tree's full verification.


## Claude Code alternative: explicit API-mode source adapter (2026-09-20)

**Implemented here, but not an activated or certified official CLI:**
`research_claude_exec.ClaudeProcessModel` and
`research_claude_profile.ClaudeExecProfile` reuse the existing native process
supervisor, original action validation, uncapped authorization and PostgreSQL
call audit. The selected contract label is Claude Code **2.1.278**, model
**claude-opus-5**, effort **max**. This is an explicit alternative already allowed
by D1, never automatic fallback from Codex. The Codex SQLite finding remains open.

### Operation and authentication

The fixed command requests `--print --bare --restricted --no-session-persistence`,
text stdin / JSON stdout, one turn, no built-in or MCP tools, no slash commands,
no user/project settings and no permission-prompt host. Environment switches
request zero API retries, no non-streaming fallback, compaction, title generation,
attachments, background tasks, updates, telemetry, automatic memory or fast mode.
Managed policy is not bypassed. No arbitrary extra flags or inherited environment
are accepted. The declared command is not proof that all combinations of CLI and
managed configuration enforce it.

Unlike a logged-in interactive Claude session, **bare mode is an API-mode path**:
it does not use the user's OAuth/keychain/subscription login. An explicitly
approved local application supplies an in-memory API key callback; no key is
requested in chat or searched in files/environment/keychains by this code. The
factory requires both `allow_process_start=True` and `allow_api_key_use=True`.
The callback is not invoked until the original audited call enters the client.
Known incompatible stdin size, stop or changed profile contract blocks before
key retrieval. Callbacks are trusted, bounded application code, not a sandbox.
A slow callback is not covered by the process supervisor's later deadline.

Public profile inputs include an explicit native executable and its SHA256,
complete path-only environment including HOME/CLAUDE_CONFIG_DIR, dedicated
working directory, HTTPS endpoint and original process limits. Endpoint userinfo,
query strings, fragments and plaintext HTTP are rejected. No config file, schema
file, gateway, installer or credential loader is created. The profile digest
covers the public process/command/environment plus prompt/action protocol;
credentials are excluded. The supplied key enters only the child environment,
not argv, prompt, profile digest, result or a new audit field. It is NOT erased
from memory or protected against privileged environment readers or a compromised
CLI. This is not a general redactor for secrets echoed by external code.

The application creates the existing UncappedResearchAuthorization with the exact
`profile.contract_sha256`, model and original request hashes, explicitly stores
that authorization in the SAME project PostgreSQL, obtains
`claude_profile_factory(profile=..., authorization=..., api_key_supplier=...,
allow_process_start=True, allow_api_key_use=True)`, and supplies it on the original
`run_uncapped_research(..., require_durable_audit=True, allow_model_calls=True,
allow_uncapped_costs=True)` path with the SAME authorization. This is a typed
integration contract, not a ready-to-run real-provider command. The standalone
operator script still does not activate a model client.

### Strict result and usage semantics

Only one JSON object of type `result`, successful subtype, exact false error,
one turn and end-turn reason is accepted. No stderr bytes are accepted, since an
unclassified warning may indicate incompatible flags/config. Missing or unknown
keys, duplicate JSON keys, invalid Unicode at either nested boundary, mismatching
model usage, permission denials, errors, deferred/structured tool work, trailing
output, nonzero process exit and reported output overruns fail closed. The final
`result` text must contain only the original closed `calls` action-data contract;
the original research agent still checks evidence read/citation relationships.
No output is repaired and no request/process is retried, resumed or substituted.

This path deliberately omits CLI `--json-schema`: that feature has a separate
structured-output retry mechanism. The host validates the returned action text
once rather than permitting CLI-hosted repair turns. The original requested
output limit is forwarded through CLAUDE_CODE_MAX_OUTPUT_TOKENS and checked again
in reported usage. The official variable applies to **most** requests; neither
this flag, the one-turn limit nor a CLI result proves exactly one external API
submission or an all-inclusive pre-request token/monetary bound.

Reported total tokens add uncached input, cache creation, cache read and output.
Claude's cache counters are not Codex's input subsets. The single model breakdown
must agree with those four totals; optional cache-creation splits must also agree.
Optional cost estimates are validated but discarded, never persisted as verified
charges. Invalid results retain unknown external usage in the original audit;
zero research counters do not prove zero outside cost. Successful structural
conversion does not guarantee a completed forecast.

### Evidence, self-review and outstanding actual-CLI checks

The official CLI reference, environment reference and SDK result parser informed
this contract. An attempt to retrieve the official Linux binary was denied by the
available download path and stopped: no mirror, alternate package or helper was
used to work around it. **The official Claude Code binary was not executed here.**
The release version/digest is metadata, not an attestation of an installed image.
There is no demonstrated elimination of SQLite or other CLI-owned files yet.
The no-session flag documents sessions, not every possible local state/log.

Tests use synthetic keys, declared result envelopes and actual isolated Python
subprocesses. The native PostgreSQL proof appends BTC/ETH success/failure cases,
separate-transaction start visibility, reported-cache accounting, restart and
original-ID replay with no extra process. It does not substitute a fake binary
for an official-CLI persistence/network acceptance claim. No original native
scenario, migration, dependency, deadline or old workflow selector is removed.
Claude source/test paths are added to both native and actual-kit triggers.

Separate same-assistant review reproduced six cases before correction: three
unclassified-stderr acceptances, two known input-limit failures that entered the
key callback unnecessarily, and a digest missing the static prompt/action
protocol. Tests now retain those counterexamples. Earlier stop-method and audit
projection mistakes were fixture errors, fixed using the original APIs, not
product defects. Exact final full/frozen/Windows evidence belongs in the PR.
No independent reviewer or zero-defect guarantee is claimed.

**Still required before real use:** obtain an approved official binary, verify
its flags/result shape and effective managed settings, inspect writes and network
activity under fresh dedicated state directories, verify no hidden repair/retry/
tool calls, validate actual model identity and provider usage, and resolve any
persistence incompatibility under the existing PostgreSQL-only rule. No change to
that rule or use of existing subscription credentials is implied. No user-local
installation/database operation or real provider call occurred. G2–G6 stay open.

Primary references checked 2026-09-20 (no new runtime dependency):
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/env-vars
- https://code.claude.com/docs/en/headless
- https://github.com/anthropics/claude-agent-sdk-python/blob/3cb0f73e6234408b5883b1e7e2875a46a3049cb9/src/claude_agent_sdk/_internal/message_parser.py
- https://github.com/anthropics/claude-code/releases/tag/v2.1.278


## Supplied Claude binary acceptance probe (2026-09-20; WP-02)

The source-only test `tests/test_research_claude_profile_native.py` now provides
six fixed scenarios for a separately verified, explicitly supplied **2.1.278**
native image: success, HTTP429, HTTP500, invalid action text, an actual tool-use
response, and a truncated event stream. This is not a seventh adapter, a product
reporting subsystem, a downloader, or a default live-model configuration.

**The official binary was unavailable in this development environment and has
NOT been executed for this delivery.** Earlier restricted downloads are not
retried via mirrors/packages/CI helpers. The local and normal CI proofs use
explicitly labeled standard-library Python stand-ins; their success is not
vendor compatibility, state, network or identity acceptance. Default verification
skips the six supplied-image cases, and the full verifier strips their opt-ins.

### Prerequisites and invocation

Use a fresh SOURCE checkout and its locked Python3.12 test environment, not a
business installation/kit. The operator must already have a publisher-verified
native image and a disposable, secret-free host with external egress denied and
numeric loopback allowed. The harness does not install or weaken security policy,
create that isolation, inspect real credentials or adopt a subscription login.
Do not copy user HOME, `.claude`, `.env`, `.local`, a database or backups into it.
Absent prerequisites are a blocker, not permission to improvise another route.

Five exact environment values enable the test. All have the original prefix that
`scripts/verify_local.py` removes, so ordinary full verification cannot silently
activate an image inherited from the parent environment:

```text
POLYMARKET_ALPHA_LAB_CLAUDE_PROBE=1
POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_IMAGE=<absolute path of reviewed native image>
POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_SHA256=<independently recorded lowercase SHA256>
POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_BYTES=<exact positive byte count>
POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_ISOLATED_HOST=1
```

The last value is the operator's assertion of the required test environment,
**not isolation attestation by the program**. No values means six explicit skips;
partial/invalid values mean failures, not skips. Do not derive publisher trust
from hashing arbitrary bytes. Native-image type, exact size/hash and reported
version are checked; no version substitution, automatic install or upgrade.

Run only the named file, with a unique previously nonexistent pytest base temp:

```text
python -m pytest -q -s --tb=short -o junit_family=legacy tests/test_research_claude_profile_native.py --junitxml=<outside-source proof path> --basetemp=<new disposable path>
```

Use the retained handoff's concrete source commit, tree, checksums and ordered
Windows commands. Do not run this template against user data or broad test
selection. Six cases must actually execute; skips are not acceptance. Stop after
this one fixed batch, preserve first failures and return the metadata. Do not
retry until green, update the image, relax an assertion, refresh an input or
whitelist a newly observed state file without a separately reviewed correction.

### What the probe measures

Each scenario creates its own new private root and explicit HOME/config/tmp/work
paths. Fixed unrelated-context canaries are seeded only there; the real user
configuration is neither read nor copied. The helper never reuses an existing
root. It performs two explicitly counted operations: `--version`, then one prompt
with the original restrictive arguments/environment. A wrong version or traffic
during the version operation prevents the prompt operation.

The production profile remains HTTPS-only. This test has **one declared transport
override**: `ANTHROPIC_BASE_URL` is changed from a numeric-loopback HTTPS declaration
to HTTP at its newly bound127.0.0.1port. Only the public synthetic noncredential is
supplied. No forwarding, TLS disabling, real provider endpoint or real key is
involved. This tests binary/protocol behavior, **not production TLS or an unchanged
end-to-end profile digest**; the observation explicitly records the override.

The mock records only bounded counts and Boolean checks, not headers or bodies.
It requires one Messages request, the declared model/output request, unchanged
approved prompt, no offered tools/unrelated canary/key in the body, and the exact
synthetic key header. Duplicate JSON, malformed/unknown methods, extra requests
and server errors cannot hide behind a valid final CLI result. Stream fixtures
follow the documented Messages event order. Success requires the exact supplied
research action and cache-inclusive26synthetic tokens; negative scenarios must
actually reach the proper mock request/response, not pass because an image failed
to start. No invoice/model-identity or all-provider-request claim is inferred.

Before/after snapshots cover only that fresh root, at most256entries,64KiBper
file and4MiBtotal. They hash surviving regular files, detect changes/new/deleted
entries and synthetic prompt/response/key canaries in UTF-8/UTF-16. Observed
links, reparse points, multiply linked files, replaced roots, oversized or
inconsistent reads are incomplete/failing evidence; no observed link is followed.
This is not an atomic filesystem sandbox: races, outside-root activity and
transient/deleted writes still require independent OS-level observation. The
probe cannot approve a path merely because its final snapshot is empty.

`CLAUDE_CLI_PROBE` output and the JUnit property contain fixed metadata only: image
hash/size, expected-version match, process/decoder result, request counts,
conformance flags and surviving-state counts. They contain no raw output,
credential, path, prompt, response or state-file contents. The private test root
is retained locally for inspection; do not upload its directory to GitHub.
`activation_authorized`, `vendor_provenance_verified`, `external_egress_verified`,
`outside_root_verified` and `transient_writes_verified` remain **false**, even on
an otherwise successful observation. No Boolean proves an unobserved guarantee.

### Implementation evidence and limits

The helper has tests for all six scenarios using both in-process controlled
responses and real synthetic Python subprocesses, malformed requests, hidden
retries, output substitution, state writes and interruption/cleanup. Separate
review reproduced six initial helper gaps (reply equality, exact image size,
root/queued directory links, duplicate JSON and an uncounted unknown method),
then fixed them without changing production code or old business test limits.
The first queued-directory fixture did not actually replace a directory on the
old code; it was corrected and reproduced again before the fix. Initial absence
of the new helper was a collection error, not an existing product defect. An
intermediate negative test needed to handle deliberate HTTP connection refusal;
its final assertion was retained. Exact final results and failures are in the PR.

A passed supplied-image batch is only a compatibility/surviving-state subset,
not G2, full sandboxing or approval for real research. The original Codex SQLite
conflict remains. If real Claude writes state, record that negative evidence and
leave activation blocked; do not delete it before taking the snapshot or silently
reinterpret the PostgreSQL-only persistence rule. No new migrations, dependency,
account credential loader, production endpoint exception or real-model call.

Primary references checked2026-09-20:
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/env-vars
- https://platform.claude.com/docs/en/build-with-claude/streaming

### First Windows apparatus failure and correction

The first PR65 head `a64e7033047efea81538409ebf95ba747db2a272` did NOT
pass native dispatch: its log has1475passed/30failed/3skipped and the JUnit
contains an extra internal-error node with three final expected cases absent.
The new snapshot relied on `os.DirEntry.stat` identity fields; Python3.12 sets
inode/device/link-count to zero there on Windows. The helper now uses current
`os.stat(..., follow_symlinks=False)` metadata while retaining all link, reparse,
hardlink, identity and size checks. Two simulated Windows-metadata counterexamples
fail on the original helper and pass with this correction. The queued-directory
swap test moves its injection to the actual metadata call, preserving rejection
and adding proof that the replacement really occurred.

The original new interrupted-read fixture also kept global OS replacements live
until pytest fixture teardown. When its expected exception did not occur, those
replacements affected the existing diagnostic hook during failure reporting.
The injection is now scoped only to the tested operation and restored before
pytest reports an assertion failure. Two additional RED-to-GREEN tests check
this. A separate deliberately failing three-case interpreter experiment now
retains both intended failures AND the following passing case, without an
internal reporting error. Its nonzero status is intentional, not a passing
product-test result. The original Windows failure remains failed.

No runtime, existing diagnostic implementation, timeout, test selection or state
refusal rule is weakened. The old provisional handoff pins the failed source
and refuses use because the accepted PR head must match; replace its delivery
references with the final reviewed revision before any local task. All final
frozen-source/Windows evidence and first failures are retained in PR65. An actual
official binary is still not available or run here.

Python reference checked2026-09-20:
https://docs.python.org/3.12/library/os.html#os.DirEntry.stat
