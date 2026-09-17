# Read-only resolution review inspection

## Purpose and commands

Inspect the recorded reason, hashes and optional outcome link for one retained
resolution check, without exporting source text or changing that check. This is
the counterpart to the resolution worklist, not an automatic confirmation tool.
Use an already initialized project-private native PostgreSQL instance and the
existing locked postgres dependencies:

```powershell
.\.venv\Scripts\python.exe scripts/inspect_project_resolution.py --review-id YOUR_REVIEW_ID
.\.venv\Scripts\python.exe scripts/inspect_project_resolution.py --root "C:\path\to\actual-project" --review-id YOUR_REVIEW_ID
```

The worklist (`scripts/review_resolution_queue.py`) supplies the latest review ID
for unresolved registered markets. The new command can also read a known earlier
or operator-confirmed review ID. It does not list all reviews or discover an ID
for every previously settled market. Existing task inspection and evaluation
commands remain separate; task record IDs and review IDs are not interchangeable.

Use the physical project root, not an extraction wrapper. There is no caller DSN,
initialization, migration, repair, restore, retry, public fetch, model or outcome
write argument. The command only calls `ProjectPostgres.session().inspect_resolution()`.
The existing read-only transaction, canonical decoder, optional outcome-link check
and instance/role/environment restrictions remain authoritative. No new SQL,
connection layer or schema is added. Help and invalid arguments need no database.

A stopped private instance may be started for this read and then stopped; a
previously running managed instance stays running. Read-only means no business
record writes, not byte-frozen server logs/WAL. Do not overlay new code onto an
old immutable kit or copy `.local` to move a database. A new source environment
may refer to the original actual root explicitly. No new migration is required.

## Historical assessment, not a fresh approval

Exit 0 / `status=inspected` means lookup, validation and session exit completed.
The nested `inspection_status` describes the STORED review:

| Status | Meaning |
| --- | --- |
| `recorded_pending` | Its stored check saw a market not closed or without the supported finality hint. |
| `recorded_needs_confirmation` | Its stored check found a candidate binary direction, but lacked independent operator attestation. |
| `recorded_blocked` | Its stored data or attestation failed the original gate; no linked outcome is assigned to this review. |
| `recorded_operator_confirmed` | That stored review passed the operator-confirmation gate and has its bound outcome. |

`assessment_basis=stored_checked_at` and `current_freshness_checked=false` are
intentional. Inspection recomputes the original assessment against the original
snapshot/check times, not today's time. A candidate may now be stale or superseded;
this command does not authorize submitting it as fresh evidence. A blocked review
with malformed raw bytes can still be inspected, without printing those bytes.

`submitted_confirmation` is the metadata of an operator ASSERTION, when supplied.
It can exist on a pending/blocked review and is not proof an outcome was accepted.
Its `asserted_yes` and `independently_verified_assertion` remain distinct from the
optional `linked_outcome.actual_yes`. False remains false; unknown remains null.
No operator identity/source is authenticated or independently checked by this read.

`linked_outcome=null` means this particular review has no recorded link. Another
review or a legacy outcome for the same market may exist. For example, inspecting
the original unconfirmed candidate after a later confirmation STILL returns that
original candidate, with no outcome, not a rewritten operator-confirmed record.
`other_reviews_checked=false` and `market_settlement_status_checked=false` make
this limited scope explicit. The command does not certify current market state,
settlement finality, source truth, forecast quality or complete execution history.

## Metadata and integrity

Output includes exact review/condition/slug identifiers, original check/receipt
and source times, canonical payload hash/UTF8 byte size, raw snapshot hash/byte
size, assessment reason/candidate, and selected confirmation/outcome times/hashes.
The confirmation source hash covers original UTF8 source text. A linked outcome's
source hash binds the canonical review payload, not just the Gamma snapshot.

No raw API bodies/base64, source texts/URLs, reviewer identifiers, question/rules,
model summary, probability, transcript, tool trace, DSN or filesystem path is
exported in successful diagnostics. Labels/hashes/times and an actual result can
still be business-sensitive; this is not anonymization or authorization to upload.
Output is JSON stdout, not a new persistent file journal. The existing explicit
Python inspection API still returns original evidence privately, unchanged.

The formatter revalidates exact types, the requested review ID, nested flags,
receipt time bounds and linked outcome invariants. It detaches the outcome before
its original validator runs, preserving the caller's stored object and hashes.
A pure supplied receipt is not proof it came from a DB; only the successful managed
lookup establishes that the record was read from the selected project instance.

## Failure behavior

Exit 3 / `review_not_found` means no review with that ID was found, NOT that there
is no research, no outcome, no registered market or no other review. Missing is
not blocked, pending or a reason to create a replacement record.

Read, validation, JSON serialization or session cleanup errors return exit 1,
`status=failed`, fixed reason `research_resolution_inspection_failed`, inspection
null, and no raw exception detail. A close failure suppresses success and not-found
reports alike. No partial output, fallback decoder, retry, claim repair or outcome
promotion is attempted. Argument errors exit 2; interrupts are not relabeled as
normal database failures. Do not alter recorded data to force a successful read.

Inspection can remain available while the strict evaluator refuses an incomplete
execution history; it never bypasses that guard or computes substitute scores.

## Acceptance scope

Offline tests exercise original check-time semantics, YES/NO/unknown states,
rejected confirmations, binary/raw invalid data, detached validation, tampered
links/flags/times, metadata privacy, wrong IDs, errors, interruptions and cleanup.
The extended native resolution proof uses real subprocess reads of missing,
unconfirmed and confirmed records before/after restart, preserves original hashes
and row counts, and verifies inspection does not unblock incomplete-history scoring.
The actual extracted kit checks help without initialization and a missing review
with its own installed environment. Native business data/models are synthetic,
not the user's instance or provider credentials. Measured results belong in the PR.


## Recovery output failures (WP-04 / WP-06)

This command follows the same [checked single-record output contract](research-execution-inspection.md#checked-single-record-recovery-output-wp-03--wp-04--wp-06)
as execution inspection. Existing inspected/not-found/error JSON and completed
lookup statuses are unchanged. Internal zero exits, short output and failed flush
are not successful lookup delivery; no second envelope, confirmation, fetch or
retry is performed. Serialization failure returns nonzero without fallback JSON.
A failure is not evidence of absence or rollback. Original operation interruption
continues to propagate without a result. The existing human assertion and linked
outcome are neither promoted nor refreshed by this output reliability repair.
