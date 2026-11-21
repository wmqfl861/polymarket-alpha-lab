# Durable bounded research batches (WP-03)

## Delivered slice, not an unattended scheduler

An explicit application can now persist a bounded BTC/ETH batch, drain a limited
number of pending requests, stop admitting new work, and inspect/resume the same
batch after restart. This reuses the existing request codec, project-private
PostgreSQL, instance binding, execution claim and capture-only recovery paths.
There is no file-backed queue, broker, alternate driver or implicit model choice.

**This is WP-03 partial integration, not G3/V1 completion.** There is no calendar
service, automatic fresh-market collection, global provider budget, administrative
cancellation or repair of lost model outputs. The explicit multi-batch rotation
below adds a durable cursor; it is not a recurring or fleet-wide scheduler.
The WP-02 direction decisions are accepted (named local agents, all needed
research data, no first-round business scale/monetary cap); real provider use
still requires the exact executable/version/model configuration and the concrete
execution authorization. A run
starts only with an explicit factory and `allow_model_calls=True`; no model or
credential is discovered. This delivery is tested with synthetic models only.

## Persist first, then explicitly execute

The application supplies existing `CapturedResearchRequest` objects. For the V1
crypto workflow, build them from freshly validated `CryptoResearchPreview.request`
with independently reviewed terms. Do not treat selection JSON, queue membership,
a model label, or a boolean as authenticated approval or source verification.
This is not another contract parser: direct generic requests retain the existing
caller's responsibility for source suitability and authorization.

```python
from pathlib import Path
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_dispatch import ResearchBatch
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop

# reviewed_requests: a tuple of existing typed requests with approved public input.
# approved_model_factory: supplied by the authorized application, never a key scan.
batch = ResearchBatch(batch_id='operator-chosen-unique-id', requests=reviewed_requests)
with ProjectPostgres(Path(actual_project_root)).session() as research:
    receipt = research.enqueue_research_batch(batch=batch, allow_queue_write=True)
    before = research.inspect_research_batch(batch_id=batch.batch_id)
    stop = ResearchDispatchStop()
    result = research.run_research_batch(
        batch_id=batch.batch_id, model_factory=approved_model_factory,
        allow_model_calls=True, max_tasks=10, max_workers=2, stop=stop,
    )
    after = research.inspect_research_batch(batch_id=batch.batch_id)
    metadata = after.to_dict()
```

The queue stores all canonical inputs in one immutable row BEFORE a dispatch run.
Admission is all-or-nothing. An exact same-ID/same-payload retry returns its
original database timestamp, even after the requests expire. Changed content or
order under the same batch ID is rejected. Admission does not create markets,
claim tasks or call a model. New requests must already be prospective and fresh
against database time; the original claim checks DB time again before execution.
It never extends a cutoff or refreshes evidence to make an expired request run.

One batch has 1..100 requests, unique record and logical task identities, only
BTC/ETH teams, and at most 8 MiB encoded bytes. Every request retains the existing
2 MiB limit. Readback caps the associated claim/result bodies at 32 MiB before
loading them. These are complete reads of one specified batch, not paginated
whole-project lists. Exceeding a cap fails rather than silently truncating data.

## States and restart semantics

`inspect_research_batch` uses one repeatable-read snapshot and DB clock:

| State | Meaning |
| --- | --- |
| pending | No execution claim is visible and the original request is still startable. |
| expired | No execution claim is visible, but the cutoff/start-delay window has elapsed. Inputs remain stored. |
| incomplete | A claim exists but no result was captured. Worker liveness is unknown; never automatically reclaim. |
| captured | The original result is stored, including completed, failed or blocked research. Never execute it again. |

All request/claim/result bindings and canonical hashes are revalidated. Existing
claims may predate batch admission; an exact original request remains the same
request rather than authorizing a duplicate. A mismatched existing record blocks
readback, not adoption. Current queue state is derived, not written back into a
second execution ledger. The original evaluator is unchanged: incomplete claims
block strict history scoring, but unstarted queued inputs are not predictions and
are not counted as executed research. A complete batch is not a statistical sample
or evidence of profitability.

Each run reloads stored inputs; a caller can continue by the SAME batch ID without
reconstructing requests or preserving a process-local list. Captured/incomplete
claims are skipped. A process dying before a claim leaves a pending request; dying
after the original claim commits leaves incomplete. No age-based worker-death
inference, TTL reclaim or automatic paid-model retry is added.

## Limits, concurrency and stopping

Each explicit drain submits at most `max_tasks` pending items (1..100, default10)
in original order, with at most `max_workers` futures (1..8, default2). There is
no hidden executor backlog of the entire batch. Returned attempts retain input
order; concurrently admitted operations need not START or finish in that order.
Each worker gets its own copied context so managed database identity checks apply
inside its actual transactions. No transaction stays open over model execution.

These limits are per invocation, not process-wide/fleet/financial caps. Two
simultaneous dispatchers can select overlapping work; the existing immutable
per-request claim still controls loop start. Simultaneous requests for the same
team/model/protocol/condition may be rejected by the existing prior-incomplete
guard. This code never disables that protection to keep workers busy.

Another application thread may call `stop.request_stop()`. Once that returns,
no later admission passes this token. An operation admitted earlier may still
reach its claim/model later. Already admitted operations are joined before return;
there is no thread kill or universal elapsed-time deadline. A hung injected client
can delay shutdown, so the authorized model client must bound its own I/O. A stop
is process-local, one-way and not a durable cancellation; a new explicit run uses
a new token and reloads pending jobs from PostgreSQL.

A pre-claim database/identity failure returns `operation_failed`, not a stored
research failure and not proof that no transaction committed. Snapshot again before
another run. A persistent failure at the head can consume later run limits; this
slice does not implement durable error rotation, backoff or cross-batch fairness.
The explicit rotation below prevents this repeated head-of-line monopolization
within its fixed roster; the single-batch API deliberately retains its old FIFO
semantics. The controlled operator entry below assembles these methods; approved
provider integration remains a G3 gap. Actual model/factory failures use the
original captured result and are skipped subsequently, not retried.

`capture_failed` retains the exact `execution.pending_run` only in memory for the
existing explicit `retry_capture(request=..., run=...)` API. The queue stores inputs,
not a lost output. Never reconstruct a forecast to replace a missing result. The
run report describes its selection/attempts, not a final database rescan; use
`inspect_research_batch` after a drain. A failure or interrupt cannot be reported
as a complete final snapshot. Interrupts propagate after admitted workers drain.

## Migration and operating boundaries

Original batch tail: `20260914000000_research_dispatch_batches.sql` brought the
catalog to 64 migrations. The current catalog also includes the rotation tail
described below. The original 63 SQL files are unchanged.
The new append-only table holds public/redacted request evidence; no passwords,
model clients or credentials. It rejects UPDATE/DELETE/TRUNCATE and uses the
existing restricted app role/grant procedure. SQL independently checks batch
size/hash, flags, team/identity and enqueue-time constraints; application decoding
also enforces the complete canonical request schema.

A SOURCE-based initialized project whose checked-in catalog includes this tail
requires the existing explicit `project_database.py migrate` operation before
using the batch API. There is no automatic migration in admission/inspection/run.
The native proof upgrades a disposable 63-migration source instance and verifies
its old record unchanged. This is not authorization to change a user's database.

An immutable old kit's catalog remains at its original version. Do NOT overlay a
new kit, copy `.local`, or edit its integrity manifest. Calling new code with an
old kit root does not install the missing table; it must fail rather than select
another database. WP-06 must define the safe user upgrade route before deployment.
The module/runbook are included in future newly-built kits. No new long-term
Release or existing local installation is changed by this code commit.

Queue payloads contain approved business evidence even though successful API
summaries omit source text. Metadata IDs/hashes are not anonymized. Do not upload
raw batches or business records to GitHub. No real project input or provider key
is needed for the regression/native tests.

## Acceptance evidence

Focused offline tests cover canonical copying, invalid input, exact admission
replay, consistent read bounds, selection states, stop, concurrency/context,
unknown commit failures and capture-only recovery. An opt-in native test uses
actual private PostgreSQL and production managed APIs to check explicit 63->64
migration, identical concurrent admission, multiple bounded rounds with BTC/ETH,
stop/restart, captured factory failure, expiry, SQL mutation/identity guards and a
separate Python process exiting after a real claim. The next run must preserve
that incomplete claim and execute only the remaining request. Old records and
strict evaluator behavior remain unchanged.

Actual final counts/revisions and unexecuted steps belong in the implementation
PR and DELIVERY_PLAN.md; synthetic results are not approved live provider tests.
Primary transaction/context references reviewed 2026-09-14:
https://www.postgresql.org/docs/current/transaction-iso.html
https://docs.python.org/3/library/contextvars.html


## Fair multi-batch turns with a durable cursor (WP-03)

The application can group 1..10 existing batches into a fixed rotation, with at
most100 requests and8MiB of combined canonical batch input. Batch membership and
order are immutable under `rotation_id`. Unequal lengths are interleaved by
position: A0,B0,C0,A1,B1,... . This is finite slot fairness inside this explicit
roster, not weighted team fairness or automatic discovery of all queued batches.

```python
with ProjectPostgres(Path(actual_project_root)).session() as research:
    report = research.run_research_rotation(
        rotation_id='approved-roster-1', turn_id='operator-turn-1',
        batch_ids_to_run=('approved-btc-batch', 'approved-eth-batch'),
        model_factory=approved_model_factory, allow_model_calls=True,
        max_tasks=2, max_workers=2,
    )
    original_turn = research.inspect_research_turn(
        rotation_id='approved-roster-1', turn_id='operator-turn-1',
    )
```

These names are placeholders for already admitted, independently approved inputs
and a separately authorized client, not a runnable provider configuration. No
client/credential is loaded by name, and no model call is authorized by this doc.
The operator entry below also refuses to import an arbitrary factory by name.

Each NEW turn reads bounded per-batch snapshots, then under a per-rotation DB lock
records its selection and next cursor in one append-only transaction. Only after
COMMIT and cleanup succeed may it enter the existing bounded executor. The cursor
moves past every considered slot, including a pending request whose later claim
operation fails. A later explicit turn continues after that slot instead of
repeatedly spending its entire limit on the same failing head. It scans at most
one circuit, never immediately retries inside a turn, and never refreshes inputs.
Captured/incomplete/expired slots are skipped according to their selection hints;
the original per-request claim transaction rechecks actual state and DB time.

The snapshots are SEPARATE consistent per-batch reads, not one atomic whole-roster
snapshot or a promise that pending remains pending. Claims created in the meantime
still prevent a duplicate loop. Aggregate input/result limits are checked as each
batch arrives; malformed/missing/oversized batches abort the entire turn, without
reserving a partial roster. Read failures that prevent validating the roster do
not advance the cursor and require operator investigation; they are not skipped.

### Replay, failure and stop are different operations

- The same `rotation_id` + `turn_id` returns its ORIGINAL reservation and starts
  nothing, even after crash, expiry, unknown COMMIT acknowledgement or stop.
  Changing its batch IDs or task/worker limits is a conflict. Replay is not a
  rescan of the prior turn's execution outcomes.
- A NEW turn ID explicitly requests the next bounded slice. A fixed roster may
  change per-turn limits but not its batch identities/content/request bindings.
  Creating another rotation starts a different cursor, not a global fairness
  guarantee or a way to bypass the original immutable execution claim.
- A process can die after reserving but before claiming. Those inputs remain
  unclaimed in the original batch; they can be considered when a future explicit
  circuit reaches them. If it dies AFTER claiming, the request remains incomplete
  and is NEVER reclaimed. No lost result is reconstructed or relabeled failed.
- Stop requested before reservation returns `stopped_before_reservation` with no
  cursor row. A concurrent stop may arrive after the last check: that turn can
  commit a cursor yet start no worker. Its selected but unclaimed jobs remain
  stored; later turns eventually revisit them. The prior cooperative admission,
  drain and bounded-client-I/O rules apply unchanged.
- `operation_failed` still does not prove absence of a committed claim. Only the
  original claim/capture tables determine execution. The turn stores selection
  metadata, NOT worker heartbeats, actual model-call counts or final outcomes.
  Inspect original batch/task records when investigating an interrupted turn.

The report retains cyclic selection order (e.g.3,0,1), rather than sorting it into
an incorrect numeric order. Returned execution IDs/hashes must match the selected
original request keys. Successful reports omit source bodies and provider error
text. IDs/hashes remain business metadata and are not automatically public data.
A committed reservation is not a forecast, operator authorization or paid-call
receipt. Monetary enforcement remains WP-02; all task/worker caps are per call.

### Migration and evidence

`20260915000000_research_dispatch_turns.sql` is the new65th migration; all original
64SQL files are unchanged. The table independently enforces ordered predecessor
and cursor transitions, binds roster hashes and interleaved request IDs/hashes to
the existing immutable batches, and refuses mutation/truncation. The application
also uses a closed canonical codec. Selection states are historical hints, not an
independent authentication of the source or proof the result remains current.

Only the normal explicit migration operation may install it. The real integration
proof upgrades a disposable64-schema source instance, preserves an existing batch
and captured record, exercises failed-head rotation, same-turn concurrent replay,
restart and post-claim process loss. The earlier63-to64 test retains its original
catalog target, so its old upgrade assertions are not silently replaced. User
instances and old immutable kits are not upgraded or overlaid by this change.
Final fixed-revision evidence is recorded in the implementation PR; G3 remains
PARTIAL until the unified operating path and approved provider budget integration
are complete. No synthetic test can close the real-model gates.


## Optional shared model-call reservations

The batch and rotation methods accept `model_budget_id` to apply an explicitly
created shared allowance to each original task's lazy model client. See
[model-call budgets](research-model-budget.md) for exact enrollment, no-refund
semantics and the distinction between reserved bounds and actual billing.
Omitting the keyword preserves this API's prior unbudgeted behavior. No policies
are auto-created; real use is gated not by D1-D3 (already accepted) but by the
exact executable/version/model configuration and concrete execution
authorization. This is not verified provider
pricing or a global monetary guarantee. Original task claims, turn replay and
cooperative stop rules are unchanged. The operator entry below requires this
keyword rather than silently using the older unbudgeted default.


## One controlled operator entry (WP-03)

`scripts/manage_research_tasks.py` and `research_dispatch_cli.main` assemble the
existing managed inspection and bounded rotation APIs. They add no queue, file
journal, background loop, migration, model discovery or provider configuration.
This closes the operator-entry subtask, **not G3, real-provider acceptance or V1**.
The 66-migration catalog is unchanged. Use only an explicitly prepared project
with this schema; no command installs missing tables or adopts another database.

From the fixed source/kit root, using that version's Python environment:

```bash
.venv/bin/python -I scripts/manage_research_tasks.py inspect-batch --batch-id approved-btc-batch
.venv/bin/python -I scripts/manage_research_tasks.py inspect-turn --rotation-id approved-roster-1 --turn-id turn-1
.venv/bin/python -I scripts/manage_research_tasks.py inspect-budget --budget-id approved-budget-1
```

Use `--root <actual-project-root>` BEFORE the subcommand only when the reviewed
source/version-switching procedure explicitly authorizes that physical root.
The default is the script's own source/kit root, not the current working directory.
These reads use the managed project session (which may start/stop its private
engine), do not mutate research records, and never construct a model client.
Do not overlay old kits or edit bundle manifests to add this command.

### Preparation and explicit run

The application first uses the existing, separately authorized
`enqueue_research_batch(..., allow_queue_write=True)` and
`create_model_budget(..., allow_budget_write=True)` APIs with reviewed typed
inputs and the exact enrolled request hashes. The commands do not read a JSON
business queue, create policies, approve terms or infer a fee bound. See the
preparation example above and the existing model-budget runbook.

An approved application can invoke the SAME parser/entry with an already-reviewed
factory. The following is assembly pseudocode; the names must be supplied by the
authorized application, not loaded from files or environment by this command:

```python
from pathlib import Path
from polymarket_alpha_lab.research_dispatch_cli import main
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop

stop = ResearchDispatchStop()
code = main([
    'run-turn', '--rotation-id', 'approved-roster-1', '--turn-id', 'turn-1',
    '--batch-id', 'approved-btc-batch', '--batch-id', 'approved-eth-batch',
    '--budget-id', 'approved-budget-1', '--max-tasks', '2', '--max-workers', '2',
    '--allow-model-calls',
], default_root=Path(actual_project_root),
   model_factory=approved_model_factory, stop=stop)
```

Both explicit model opt-in AND the callable factory are required before opening
a managed session. `--budget-id` is mandatory; no unbudgeted fallback is exposed.
**The standalone script has NO configured model factory.** Its `run-turn` returns
exit2 before database access, even with `--allow-model-calls`; it must not be
advertised as a ready-to-use real-model client. D1-D3 are accepted; an actually
verified adapter/fee bound, the exact executable/version/model configuration and
the concrete execution authorization remain open. The accepted uncapped
first-round application route is the delivered typed assembly
`run_claude_research_rotation` in
`src/polymarket_alpha_lab/research_claude_operator.py`, described in the
[local-agent integration](research-local-agent.md) guide; this console keeps the
capped `--budget-id` contract. An injected factory is a trusted-code boundary,
not a sandbox or proof that one invocation produces at most one billed request.
No real or synthetic default client is installed in this production entry.

### Stop, inspect, then choose the next turn

Another authorized application thread can call `stop.request_stop()`. Ctrl+C
uses the existing executor's cooperative stop/drain behavior: no new admission
after the token stops, but previously admitted work may finish. It is not an
administrative cancellation, instant socket kill or persistent stop flag.
Bounded client I/O is still required; a hung client can delay return.

After interruption or an uncertain write, use `inspect-batch`, `inspect-turn` and
`inspect-budget` with the original identifiers. The same turn ID only replays its
selection receipt and starts nothing. A NEW explicit turn ID continues the
persistent cursor. A selected-but-unclaimed task skipped by a stop is revisited
on a later circuit; an already captured or incomplete task is never re-executed.
Do not invent new task IDs or switch budgets to retry a lost provider operation.

The JSON result is the existing metadata receipt, not a second execution ledger
or final whole-project snapshot. It is emitted only AFTER managed-session cleanup.
Failures (including `SystemExit` from a client or cleanup) use a fixed reason code,
not the raw exception string. A failed cleanup suppresses an otherwise successful
receipt: effects may already have committed, so inspect before further action.
The run envelope conservatively sets `model_calls_possible` and
`business_writes_possible`; these are not measured call/write counts.
`final_database_state_checked=false` and `provider_charge_bound_verified=false`
remain explicit. `readonly=true` means no trading mutation, NOT no local research
write. Read results include business IDs/hashes, so they are not anonymized.

| Exit | Meaning and required interpretation |
| --- | --- |
| 0 | The selected operation returned successfully, including no work or same-turn replay. NOT a claim that all research completed. |
| 1 | Operation/cleanup failed, a returned attempt failed/blocked, or an execution has no saved completed result. Inspect original state; no automatic retry. |
| 2 | Invalid arguments, missing model opt-in or no supplied client. No managed session is opened. |
| 3 | A specific inspection identifier was not found. Not a claim that the whole database is empty. |
| 130 | Cooperative stop or keyboard interruption. Previously admitted operations may have completed; inspect before the next explicit turn. |

A `dispatched` result can exit1 because a task failed; always check BOTH the exit
code and per-attempt metadata. Incomplete and captured failed/blocked records are
not hidden or relabeled successful. The original strict history evaluator still
rejects incomplete history. The CLI intentionally never emits raw evidence,
model output or `pending_run`. On process exit that in-memory pending result is
lost; use the existing typed session API for authorized capture-only recovery
while the original result is still held. Never reconstruct a forecast from a
receipt, and never rerun its model to replace a lost result.

### Operator acceptance and self-review

Offline unit tests are `tests/test_research_dispatch_cli.py`; the separate
same-assistant adversarial review is `tests/test_research_dispatch_cli_review.py`.
The review first reproduced six SystemExit escape cases (including real worker
threads), then verified the sanitized failure fix without weakening assertions.
These are self-review tests, not an independent third-party audit or a defect-free
guarantee. The native opt-in proof is
`tests/test_project_postgres_dispatch_cli_native.py`: two teams, multiple turns,
stop/restart, captured factory failure, a separate process exiting after claim
and allowance commit, replay without client work, retained incomplete history
and unchanged old records. Native inputs/clients/charges are synthetic.
Final-revision commands, exact counts, first failures, CI identifiers and skipped
or unexecuted checks belong to the implementation PR and DELIVERY_PLAN.md. No
local user's database, installed kit, credentials or real provider is exercised.

The combined synthetic native scenario also runs a turn with `--max-workers 2`,
proves overlapping worker admission, and commits one task before another worker
terminates the CLI child process. Restart preserves the incomplete claim and all
call reservations; same-turn replay executes nothing, while a new turn completes
only pending tasks. Capture-only recovery requires the retained original run and
cannot repair a lost result. G3 remains open pending the approved real-client
evidence.


## Task-command output completion and shared stop

The batch/turn/budget inspections and the budgeted `run-turn` entry now reuse the
existing checked resolution-result emitter. Successful JSON is unchanged: the
complete envelope and one newline are serialized first, written exactly once,
and explicitly flushed only after a complete character-count result. Serialization,
short-write, write or flush failure returns a nonzero handler result; no second
error envelope is written into a possibly damaged stream and no task is repeated.
The paper subcommands keep their existing separate operator behavior.

A `KeyboardInterrupt` during task-envelope publication returns 130 and requests
any supplied `ResearchDispatchStop`, just as interruption during the managed
operation does. This remains cooperative: it blocks subsequent admissions, not
already-admitted work. An ordinary output error does not cancel other invocations
sharing that stop token. An output prefix can already contain the earlier result;
check the process exit and inspect the ORIGINAL turn and budget before any explicit
same-input replay. Neither failure nor shared stop rolls back committed selections,
claims, results or call reservations. Successful write/flush is not proof that a
receiver consumed the envelope. Python may replace a requested exit with another
nonzero status if interpreter shutdown itself cannot flush a stream.

The enhanced existing native test uses a new empty turn after all its six synthetic
tasks finish: it first proves absence, commits the real selection, injects only a
short output sink, then verifies real lookup and exact same-turn replay. It asserts
no new client call and no additional budget reservation. This is not a new paid
provider run or a claim to reproduce PR49's separate PowerShell timing failure.
User databases, original migrations and model authorization are unchanged; D1-D3
are accepted, and the exact executable/version/model configuration and concrete
execution authorization under them remain pending.

Python stream/exit contracts consulted 2026-09-17:
https://docs.python.org/3/library/io.html#io.TextIOBase.write
https://docs.python.org/3/library/sys.html#sys.exit

## Interrupted managed-session close (WP-03 / WP-06)

The enclosing `ProjectPostgres(root).session()` owns the private lifecycle lease
until admitted work finishes. If `ProjectResearchSession.close()` raises
`KeyboardInterrupt` or `SystemExit` while sealing admissions or waiting for that
work, the owner retains the first such exception and resumes the SAME idempotent
close. It does not rerun any research, model call, claim, reservation, SQL
operation or engine command. New operations remain refused after admissions are
sealed. Once close completes, an engine started by this session is stopped once;
a previously running borrowed engine is left running. The retained interruption
then propagates to the caller. A stop failure remains the primary error, with the
retained interruption chained as its cause rather than silently discarded.

This is cooperative draining, not cancellation of an in-flight model or database
request. A client that does not return can still delay closing; its original I/O
bounds remain required. No forced thread/process kill, signal handler, OS-policy
change, new timeout or refund is introduced. Unexpected non-interruption errors
from close are not retried or declared safely drained. Hard termination and
arbitrary signal delivery outside the guarded close call are not covered by this
contract. Inspect original IDs after an uncertain stop; never restart a task or
release a reservation merely because the caller was interrupted.

Tests inject Python exceptions at the close boundary while real admitted threads
are held at a deterministic checkpoint. The package proof also checks the real
OS lifecycle lock and an existing PostgreSQL record under owned/borrowed engines.
It does not send Ctrl+C to Windows or certify all OS-console signal behavior.
Reference: Python's `threading.Condition` and `signal` documentation describe the
wait/reacquire contract and arbitrary exception delivery; neither supplies an
uninterruptible application cleanup guarantee.


### Combined close and receipt behavior

The task command's checked output path is reached only after its managed session
finishes closing. With the lifecycle-drain fix, a close interruption cannot cause
a failure envelope to be published while the original admitted work still needs
the lease. After draining, the existing CLI maps KeyboardInterrupt to130 and
requests the original shared stop token; an internal SystemExit maps to failure.
A later short output remains failure and does not undo an already requested stop.
An output interruption still requests that same token. None of these outcomes
re-executes the original operation, refunds its reservation or proves rollback.

The combined regression uses real threads and the actual session/CLI/emitter,
with synthetic engine calls and inspection data. The existing packaged drain
recipe provides separate real-engine and OS-lease coverage, not OS-signal coverage.
Python Condition wait/reacquire contract consulted for the integration review:
https://docs.python.org/3.12/library/threading.html#condition-objects

## Admit reviewed batches and budgets through the existing task command

`manage_research_tasks.py` now exposes the original immutable creation APIs as
`enqueue-batch` and `create-budget`. This fills the command-line admission step;
it does NOT select a provider, approve research inputs, start research, reserve
model calls, verify a tariff or load a model factory. D1-D3 are accepted; the
reviewed application/client boundary, the exact executable/version/model
configuration and the concrete execution authorization still apply. Preparing a
valid canonical request is
not a substitute for reviewing its actual market rules and evidence.

Each operation requires its existing identifier, `--input-sha256`, its OWN explicit
write flag, and one original canonical payload on binary stdin:

| Operation | ID argument | Required write flag | Original payload / bound |
| --- | --- | --- | --- |
| `enqueue-batch` | `--batch-id` | `--allow-queue-write` | `ResearchBatch.payload`, at most 8 MiB |
| `create-budget` | `--budget-id` | `--allow-budget-write` | `ModelCallBudget.payload`, at most 32 KiB |

The digest must be the SHA256 of the reviewed canonical UTF-8 payload, without
transport framing. One optional trailing LF or CRLF is accepted, not arbitrary
whitespace, another JSON object or a changed serialization. Original codecs check
closed schemas, exact request hashes, immutable flags and bounds. Invalid input
or a missing write flag stops before any managed database access; missing permission
also prevents reading stdin. No times, IDs, costs or approval hashes are generated
by the console. There is no `--file` loader or new business journal.

An already-authorized application may send its reviewed IN-MEMORY values directly.
The following variables are supplied reviewed values, not filenames, credentials
or default authorizations:

```python
from pathlib import Path
import subprocess

project = Path(actual_project_root)
python = project / '.venv/bin/python'
script = project / 'scripts/manage_research_tasks.py'

# This example performs ONLY the explicitly approved batch write.
# reviewed_batch is the original ResearchBatch, not discovery-console JSON.
result = subprocess.run([
    str(python), '-I', str(script), '--root', str(project),
    'enqueue-batch', '--batch-id', reviewed_batch.batch_id,
    '--input-sha256', reviewed_batch.content_sha256, '--allow-queue-write',
], input=reviewed_batch.payload.encode('utf-8'), capture_output=True,
   shell=False, check=False)
# Check returncode AND the returned status; do not automatically repeat it.
```

Budget creation follows the SAME byte transport with `create-budget`,
`--budget-id reviewed_policy.budget_id`, the reviewed policy's digest and
`--allow-budget-write`. Do not pipeline through shell text processing (`cat`,
`echo`, command substitution or PowerShell `Get-Content`), reformat
JSON, discover a credential, or interpret an example amount as an approved budget.
The producer must close stdin. The input byte bound does not promise a wall-clock
deadline against a producer that never finishes; the caller owns transport/I/O
cancellation. Cancellation does not imply rollback of a possibly completed write.

A successful result has `status=admission_receipt_returned` and ONLY immutable
metadata: ID, complete payload hash, original admission/creation time and request
count (budget also includes its expiry). No original evidence, transcript, current
queue state or remaining allowance is exported in this receipt. It may be the
first creation OR an exact replay, so it deliberately does not say "newly created"
or claim zero reservations on a reused budget. Use existing `inspect-batch` /
`inspect-budget` for the current database view.

Same-ID, identical-input replay preserves the original receipt even after later
use; changing any payload byte conflicts under the original store. Creation never
tops up/refunds an existing policy, resets tasks, refreshes a cutoff or admits
another model call. Input permission is not permission to call a provider.

Batch and budget creation are TWO independent explicit operations, NOT one atomic
pair. A successful batch write can remain when the budget write is blocked or
fails. An error, cleanup failure or lost/partial output can follow a COMMIT. Inspect
the SAME identifier first; only explicitly replay the SAME reviewed payload.
Never create a replacement ID, repeat model calls or assume that an error undid a
write. The task console now checks its single output write and flush on admission,
inspection and turn results; no second result is appended to a damaged stream.
A completed flush is not proof that a consumer received the message.

The existing `run-turn` standalone script still has **no model factory** and refuses
real execution without the approved application. These commands do not complete
G2/G3 or validate real input/fee evidence. The composed Windows kit test admits
both batches and the policy through actual child commands, proves no claims/call
reservations exist at that point, and then runs the existing synthetic flow.

Stream references checked 2026-09-16:
https://docs.python.org/3.13/library/io.html
https://docs.python.org/3.13/library/sys.html#sys.stdin


### Admission with the existing shared-stop and session-drain contracts

Admission now reuses the already-merged checked emitter and shared-stop wrapper;
there is no second implementation of JSON output. An interruption while reading
reviewed stdin requests the original supplied stop token BEFORE attempting the
error receipt. If that receipt itself short-writes or fails, the handler returns
failure but the stop request remains. Output interruptions on permission-denied,
invalid-input or stored-admission paths also reach that same token. Ordinary
output errors alone do not stop other associated task invocations. None of these
paths replays a store, invokes a model or assumes a COMMIT was rolled back.

Managed admission keeps the current session draining and engine-ownership rules.
Canonical input codecs, per-command write permissions and original payload/hash
comparisons remain unchanged. The exact provider/model configuration and the
concrete execution authorization for real charge limits remain pending; D1-D3
are accepted, and queue/budget admission is not model approval.
Integration references: Python3.12 text-stream write/flush and Condition contracts
were checked on2026-09-17; they do not guarantee consumer receipt or an
uninterruptible filesystem/database operation.


## Explicit uncapped integration (2026-09-19)

Managed batch/rotation APIs accept `uncapped_authorization` and
`allow_uncapped_costs=True` with the original explicit model-call opt-in. This is
mutually exclusive with `model_budget_id` and preserves original claims/results.
See [local-agent integration](research-local-agent.md) for scope, expiry, stop
and replay. The accepted uncapped first-round application route is the delivered
typed assembly `run_claude_research_rotation` in
`src/polymarket_alpha_lab/research_claude_operator.py`: it composes the existing
stored authorization, two reviewed BTC/ETH batches, the lazy Claude factory, the
durable call audit and one bounded rotation, and is itself side-effecting when
invoked. The standalone script still has no real client configuration; no
default subprocess transport or durable authorization ledger is supplied here.


## L8-D activation record template and subsequent transport scope (design only)

Baseline: repository main `db6e2291` (2026-09-30). The post-L7 ruling
(DELIVERY_PLAN.md §65) withheld effective activation pending a reviewed,
approved record binding engineering, isolation, the credential procedure,
and scoped authorization. The section records the **activation record
template** and **precise subsequent source/test scope** for L8-D. Follow
[Project Agent Routing](../AGENTS.md#project-agent-routing) for review and delegated arbitration.
The provider transport and credential-ingress design itself is specified in
[the L8-D section of the local-agent guide](research-local-agent.md#provider-transport-and-credential-ingress-design-l8-d-design-only);
the two documents are one reviewed package. Everything here is design only:

- Filling or approving a record does NOT flip any activation flag.
  `activation_authorized` changes only through an explicit activation
  decision after required owner approvals, never automatically when CI turns green.
- The template is a review schema, not a runnable command, a new persistence
  table or an API. It is recorded as evidence through the existing
  documentation/PR path; no migration stores it.
- Until the design gate passes and every engineering prerequisite has its
  evidence reference, every field below stays a placeholder.

### Activation record template

```text
activation_record_id : <operator-chosen unique ID>
created_utc          : <UTC>
status               : blocked | approved-not-yet-effective | effective
deciding_authority   : <independent read-only arbitration verdict under AGENTS.md + owner confirmations>
valid_window_utc     : <from>/<to> (must cover the whole intended run)

baseline_pins
  repository         : <URL>
  commit_sha         : <full commit>
  tree_sha           : <full tree; worktree clean at recording>
  branch             : <retained branch>

runtime_isolation
  host_id            : <dedicated host identity>
  host_trust_doc     : <reference to documented host trust>
  principal          : <dedicated non-shared UID/name; NEVER the shared
                        ubuntu parent/supplier>
  attestation        : <who verified, when, how; result of the
                        shared-observer/inspection/sudo check>

engineering_prerequisites   (each field cites its evidence; all mandatory)
  cleanup_corrections       : <L8-A commit + review verdicts>
  relay_design_gate         : <this design + independent VERDICT: PASS>
  relay_implementation      : <relay PR commit + final review verdict>
  contained_qualification   : <official supplied-image run through the
                              actual contained factory/model path with a
                              fake service and dummy credentials>
  reply_composition         : <L8-B evidence reference>
  recovery_evidence         : <L8-C evidence reference>

pins
  vendor_image       : <path, byte size, SHA256, version>
  wrapper            : <SHA256, size>
  helper             : <SHA256, protocol version>
  interpreter        : <SHA256, size>
  runtime_closure    : <per-file guest path, SHA256, size>
  relay              : engine peer <SHA256, protocol version
                       research-linux-relay-v1>, inner peer <SHA256>
  trust_configuration: origin hostname, port, CA bundle <SHA256, size>,
                       permitted-address rule, TLS floor
  profile            : schema_version, contract_sha256

provider_and_model
  provider           : Anthropic Messages via the pinned Claude Code CLI
  model_id / effort / cli_version : <exact pinned values>

reviewed_requests
  rotation/batch/turn IDs : <exact IDs>
  request_roster     : 1..100 exact (record_id, content_sha256) pairs
  authorization      : authorization_id, adapter_contract_sha256,
                       approved_utc, expires_utc
  input_review       : <reference to the reviewed BTC/ETH inputs>

finite_limits
  dispatch           : max_tasks, max_workers, turn count
  supplier_slots     : per-team slot counts (consumed permanently)
  process            : timeout_ms, cleanup_timeout_ms, stdin/stdout/
                       stderr caps, memory/pids/scratch caps
  relay              : request/response byte caps, connect/handshake/
                       idle/total deadlines
  stop               : stop token binding <identity requirement>

scoped_pg_writes
  project_root       : <root; DSN via validate_local_postgres_dsn only>
  permitted_classes  : uncapped authorization row, call-start rows,
                       outcome rows, batch/turn reservation rows <exact>
  prohibition        : no other writes; no new migrations unless
                       separately reviewed

preflight_set        : <approved entries: method, literal target, query,
                       header constraints; approval decision references;
                       empty means no preflight is forwarded>

credential_supply
  channel            : <private local channel description on the
                        dedicated principal>
  slots_supplied     : <counts per team>
  attestation        : no chat/Git/env/argv/file/log/stored-secret use;
                        no secure-erasure claim

identity_and_usage
  expected_identity  : <what the provider must report/serve>
  check_method       : <how actual identity/usage is verified at
                        acceptance; mismatch stops further admission>
  unknown_billing    : remains unknown; never recorded as zero

evidence
  commands_results   : <exact commands, outputs, first failures,
                        retries, skipped and unexecuted steps>

sign_off             : <operator, independent arbitration, and required owner approval references>
```

### Precise subsequent source and test scope

A future transport implementation PR may touch exactly the files below.
Anything outside this list, any relaxation of an offline guard, any online
constructor added before the design gate, and any scope growth beyond these
boundaries requires a newly reviewed design amendment first.

| File | Change | Boundary |
| --- | --- | --- |
| `src/polymarket_alpha_lab/research_linux_relay.py` | NEW | Trusted-parent validating relay engine, inner-peer source, reviewed trust-configuration record, fixed outcome codes. Standard library only; no database import; no background thread in the Python parent. |
| `src/polymarket_alpha_lab/research_process_linux.py` | MODIFIED (bounded) | Closed `relay-fixed-origin` policy value, inherited relay channel staging (model phase only), digest input, teardown integration. Offline default, offline validation and offline goldens byte-unchanged; no `qualified=` escape. Strictly after L8-A's corrections land. |
| `src/polymarket_alpha_lab/research_claude_profile.py` | MODIFIED (bounded) | New `research-claude-profile-linux-v3` digest branch binding the relay trust configuration and a v3-scoped numeric-loopback `http` endpoint form (v1/v2 https validation unchanged). v1/v2 digest bytes unchanged. |
| `src/polymarket_alpha_lab/research_claude_operator.py` | MODIFIED (opt-in wiring) | Accept a relay-bearing profile/trust configuration in the typed assembly. No discovery, no default client. |
| `tests/test_research_linux_relay.py` | NEW | Validation matrix: origin/method/target/query/header allowlist, authority-override refusal, redirect refusal, cardinality, DNS/permitted-address constraints, byte/time bounds, teardown on every outcome, no-retry, absence of CONNECT/SOCKS. |
| `tests/test_research_process_linux.py` | MODIFIED (additive) | Relay channel staging/cleanup; all offline regressions retained unchanged. |
| `tests/test_research_claude_profile.py` | MODIFIED (additive) | v3 digest golden plus v1/v2 immutability counterexamples. |
| `tests/test_research_claude_linux.py` | MODIFIED (additive) | Contained composition through the actual factory/client path against a synthetic loopback upstream with a dummy key. |
| `tests/test_research_claude_relay_native.py` | NEW (opt-in) | Official supplied-image qualification through the actual contained factory/model path with a fake service and dummy credentials, gated by explicit environment opt-ins exactly like the existing supplied-image probe. |

Ownership boundaries for the implementation phase: the relay module plus its
new test file form one write scope; the `research_process_linux.py` wiring plus
its test additions form a second, strictly sequenced after L8-A; the profile
v3/operator wiring plus its tests form a third; the opt-in native
qualification is a fourth. No two workers hold the same file; the coordinator
alone updates `DELIVERY_PLAN.md`. Every scope keeps its focused tests, full
verification, labeled self-review and a final independent read-only review
with an explicit verdict. Audit-semantics changes inside
`tests/test_project_postgres_uncapped_audit_native.py` remain L8-C's owned
surface and enter only through a separately reviewed amendment.

### Scope sequencing note

Scope 1 (`research_linux_relay.py` + its tests) must land before scope 3
(the v3 digest branch binds the relay trust-configuration record type that
scope 1 defines), in addition to the stated L8-A dependency of scope 2.
Scopes 1 and 4 remain parallel-safe.

### Gate ordering

1. This design package (both documents) receives the mandatory independent
   design gate verdict before any file above changes.
2. Implementation proceeds only inside the enumerated scope, one owned scope
   per worker, offline guards untouched.
3. The relay qualification runs use fake services and dummy credentials first;
   the official-image composition check is the ruling's new prerequisite, not
   a repeat of L7 acceptance.
4. Submit the completed record for independent read-only arbitration under
   the root routing rule and obtain the required owner approvals. Only the
   recorded effective-activation decision can authorize real research or credential use.
