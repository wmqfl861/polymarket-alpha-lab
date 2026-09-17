# Read-only execution inspection

Use `scripts/inspect_project_research.py --record-id YOUR_RECORD_ID` to inspect
one existing captured-runner claim without launching, repairing or scoring it.
The command reuses `ProjectPostgres.session().inspect()` and its original
read-only transaction, canonical request/result decoders and instance binding.
No new database, SQL migration, source transport or model client is introduced.

## Source and kit use

Install the locked project dependencies with the postgres extra. From an already
initialized project's source directory or a newly built kit:

```powershell
uv sync --locked --extra postgres
.\.venv\Scripts\python.exe scripts/inspect_project_research.py --record-id YOUR_RECORD_ID
```

A fresh source checkout may explicitly reference the original database root:

```powershell
.\.venv\Scripts\python.exe scripts/inspect_project_research.py --root "C:\path\to\actual-project" --record-id YOUR_RECORD_ID
```

That root must be the actual initialized project, not an extraction wrapper.
This does not initialize, migrate, copy, repair, restore or reset an instance.
Do not overlay a new kit on an old installation or copy `.local` to update it.
Old immutable kits stay valid but do not automatically acquire this command.
`--help` and invalid argument handling do not construct the database manager.
There is no DSN, public-fetch, model, repair, historical-time or retry option.

The existing manager may start a stopped private server for this read and stops
only one it started itself. A previously running instance remains running. All
original path/role/environment/migration checks apply. Read-only refers to
business records, not byte-unchanged PostgreSQL logs or WAL while the server runs.

## Result interpretation

| Exit / status | Meaning |
| --- | --- |
| 0 / `inspected` | One valid claim was read. Inspect the nested result state; exit 0 does not mean successful research. |
| 3 / `claim_not_found` | This ID has no visible execution claim in the lookup's database snapshot. |
| 1 / `failed` | The query, record validation or session cleanup failed. No partial inspection is returned. |
| 2 / argument error | Invalid or missing arguments; no manager was constructed. |

`claim_not_found` is NOT proof that the ID has no legacy standalone attempt,
that a market does not exist, or that the entire database is empty. This command
only covers the captured runner's execution claims and matching attempts. It
does not fall back to legacy records, fetch all claims, or register a missing ID.

Nested `inspection_status` is one of:
- `result_not_captured`: a claim exists with no matching captured result.
- `captured_intake_blocked`: the captured input gate rejected the task.
- `captured_completed`, `captured_failed`, `captured_blocked`: the actual stored
  research result has that status; its original fixed reason and counters remain.

A claim without a result may be running, interrupted, or missing a result after
an uncertain save. The database read alone cannot distinguish these cases:
`worker_liveness` stays `unknown`, and missing result counters/times stay null,
not zero or invented failure. `capture_failed` with its in-memory recovery object
is NOT a durable database state, and is not reconstructed by inspection.

No expiry/reclaim, process termination or automatic model retry is authorized.
Existing explicit capture-only recovery requires retaining the ORIGINAL result;
never manufacture a new report or change task IDs to bypass an incomplete claim.
Inspection remains available for an incomplete task, while the separate strict
history evaluator still refuses incomplete history. Inspecting one task is not
an entire-history check, eligibility certificate, scoring step or forecast approval.

## Output boundaries

Output includes task/team/model/protocol/market IDs, original capture/request
hashes and times, input status/reason, source COUNTS, configured limits and stored
model/tool/token counters. Model labels are caller assertions, not authenticated
provider identities; limits are not hard spending guarantees. Stored counters
refer to the prior research, not operations performed by this inspection command.

No evidence text, raw API response, question/rules, summary, probability,
confidence, citation IDs/URLs, model transcript or tool trace is exported. Labels,
counts and hashes can still be sensitive business metadata. This is local stdout,
not an anonymized public report or a new durable file journal. The original raw
records are not rewritten and existing canonical hashes/formats are unchanged.

All returned records are revalidated, and a mismatched ID or unexpected in-memory
execution state is rejected. Output is published only after successful session
exit, including the not-found case. Exceptions return one fixed diagnostic code,
not raw filesystem paths or DB/HTTP details. Interrupts are not relabeled as normal
failures. No retries or fallback scores are attempted. Current-snapshot reads do
not establish that another process cannot add an outcome or result afterward.

## Acceptance

Offline cases exercise all ten teams and stored result states, absent/incomplete
separation, copied metadata, redaction, nested mutation, identity checks, invalid
arguments, cleanup failure and interrupts. Real isolated Windows PostgreSQL tests
invoke the actual CLI for missing/completed/failed/incomplete claims and recheck
row counts, original receipts, stopped state and the strict evaluation block.
Each actual extracted kit tests help before initialization; one later inspects a
synthetic recorded task using its own installed Python. None of this is user data,
a real provider call, process-liveness detection or a general security guarantee.

## Finding a record ID

The read-only [execution inventory](research-execution-inventory.md) lists all
visible claims under a strict count/payload cap. It includes incomplete tasks
without requiring a record ID and does not retry or score them.


## Checked single-record recovery output (WP-03 / WP-04 / WP-06)

The execution and resolution-review lookup commands now reuse the existing
checked JSON emitter after the managed session has closed. A complete successful
lookup keeps its original JSON and exit code: 0 for an inspected record, 3 for a
completed lookup that found no matching record, and 1 for a managed lookup error.
Internal SystemExit, including SystemExit(0), during construction, lookup,
metadata validation or cleanup is a failed lookup, not process success.

Serialization, short-write, write and flush failure return nonzero. A broken
stream is never given a second envelope and no lookup, confirmation or model
operation is automatically repeated. Serialization failure can leave no output;
write/flush failure can leave a prefix or even complete-looking JSON. Callers must
check the process status AND complete JSON, not infer success from a prefix. A
failure does not mean the requested record is absent, nor that an earlier write
was rolled back. Query only the original identity; no substitute record or retry
of business work is authorized.

Original KeyboardInterrupt during lookup/cleanup still propagates without a
fabricated result. Output-stage interruption follows the shared emitter's 130
return; interpreter shutdown can subsequently impose its own nonzero status.
Parser/help behavior, read-only metadata, record validation and unknown-liveness
semantics remain unchanged. This is not a receiver-acknowledgment protocol,
filesystem snapshot or protection against arbitrary preloaded Python code.

The emitter is imported when the command is invoked, not while modules initialize;
this preserves both import orders with the existing confirmation module, which
already imports the resolution summary. No new output subsystem is introduced.
Python reference checked 2026-09-17:
https://docs.python.org/3.12/library/io.html
https://docs.python.org/3.12/library/exceptions.html#SystemExit
