# Read-only project research evaluation console

## Purpose and use

Run the existing captured-history evaluator without writing Python glue. This
is a view of research already in the project-private native PostgreSQL instance,
not a research launcher, model selector, new storage layer or trading dashboard.
No public API, model, operator confirmation, outcome fetch or business write is
performed. No SQL migration or dependency change is required for this feature.

Install the existing locked project environment with `uv sync --locked --extra
postgres`, then run from an already initialized source or newly built kit:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_project_research.py
```

A new source checkout can explicitly point at an original initialized project:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_project_research.py --root "C:\path\to\actual-project"
```

Use the actual root containing `pyproject.toml`, `database/migrations.lock.json`
and `.local/postgres`, not an extraction wrapper. This command does not initialize,
import, migrate, restore or reset a database. Do not copy `.local` or overlay a new
kit on an existing installation. Existing downloaded kits are not automatically
updated. `--help` needs no database. There is no caller DSN option or cloud fallback.

The managed session can start an initialized stopped private instance, and stops
only one it started itself. A previously running managed instance remains running.
Normal database runtime logs/WAL may change; **read-only means no application
business writes**, not a byte-frozen server directory. Existing path/instance/
role/migration/environment checks remain active; the console cannot bypass them.

## Historical views and configuration

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_project_research.py --as-of "2026-09-12T12:00:00+08:00" --include-decisions
```

The timestamp is an explicitly zoned ISO value; a missing timezone or invalid
configuration fails before a manager is constructed. The existing database clock
rejects future report times. Historical visibility is based on recorded/claimed
receipt time, not a model-supplied date. A later outcome is not included in an
older snapshot. `--as-of` is a historical view, not an authorization to backfill.

The console calls **ProjectResearchSession.evaluate()**, which invokes the strict
existing complete-execution loader in one read-only repeatable-read snapshot.
There is no fallback to the legacy non-strict evaluator, skip-incomplete option,
success-only filter, group filter or recent-record truncation.

`--max-records` defaults to 10,000, range 1..10,000. The loader applies this to
attempts and outcomes separately; its existing 32 MiB payload cap also applies.
Exceeding a cap returns a block, never a score computed from truncated history.
`--buckets` accepts 2, 5, 10 or 20. `--min-sample-count` defaults to 30 and
`--min-bin-count` to 5, each 1..10,000. These are descriptive diagnostic settings,
not confidence levels, fitted calibration or forecast approval thresholds.

## Output and interpretation

On a completed read, exit code 0 and `status=evaluated` mean the query and
serialization completed. They do NOT imply good forecasts or approval. The
nested `evaluation_status` distinguishes:

| Status | Interpretation |
| --- | --- |
| `no_visible_attempts` | No captured attempts visible at this time. This does not say there are no registered markets or outcomes; orphan outcomes have a separate count. |
| `no_scored_forecasts` | Attempts exist, but none is currently scorable. Decision counts explain pending outcomes, failed/blocked research, late reports or subsequent attempts. |
| `diagnostics_available` | At least one decision was scored. Per-group sample warnings and other limitations still apply. |

Each group's original Brier score, log loss, neutral baseline, reliability bins
and sample status are preserved exactly. Empty scores remain null. An erroneous
certainty retains `log_loss_status=infinite`, a null numeric mean and its explicit
infinite-loss count; it is not clipped to a finite score or printed as JSON NaN.
Small samples remain `insufficient_sample`. No pooled score or model ranking is
computed, and `forecast_approval_performed` always remains false.

`scored_decision_count` can include the same event in different team/model/protocol
groups. `scored_condition_count` counts distinct scored event IDs, so repetitions
are not labeled additional independent events. Neither count measures statistical
independence between different events. The first recorded attempt per group/event
remains selected even if a later retry is better; failures are not removed.

Default output includes group IDs/configuration, aggregate decision counts and the
existing input digest, but no per-record decision list. `--include-decisions`
adds the existing canonical per-record IDs, reasons, probabilities and hashes.
Neither mode exports evidence text, model summaries, prompts, raw API bodies or
source URLs. Identity labels and probabilities may still be business-sensitive;
this is local diagnostic output, not an anonymization guarantee or public upload.
The console prints JSON to stdout and does not persist a new report file/journal.

## Failures and completion boundaries

Exit 1 with `status=blocked` and `evaluation=null` exposes only these exact known
reasons: `research_execution_history_incomplete`, `research_capture_history_limit`,
`research_evaluation_from_future`. An incomplete claim anywhere in the visible
execution history blocks the whole read, including claims for settled events.
It is NOT interpreted as empty history and cannot produce partial group scores.

Other exceptions use the fixed `research_evaluation_operation_failed` code with
no raw database error, DSN or filesystem path. A session-close failure suppresses
an otherwise computed success report. There is no automatic retry, partial result
fallback or automatic repair. Interrupts are not disguised as normal DB failures.
Argument errors exit 2. Do not delete claims/data or relax checks to get exit 0.

A successful history gate establishes only the existing visible claim/result
completeness check in that database snapshot. It does not authenticate external
sources, audit every historical operator decision, prove no unregistered work was
omitted or independently certify old outcomes. Older records and all scoring
formulas remain unchanged. Use the separate resolution worklist for registered
markets that do not yet have captured attempts/outcomes.

## Acceptance scope

Offline tests cover output distinctions, exact existing scores, privacy of raw
content, later-attempt exclusion, per-group counts, historical visibility, invalid
arguments, fixed errors, interruptions and cleanup failure. An extended native
resolution test invokes the actual CLI against a fresh private instance for
empty/pending/scored/future/incomplete views and checks readback/row counts. The
actual kit test checks help without a cluster and evaluates one pending record
from the extracted kit's own Python environment. These use synthetic business
inputs, not a user's database or paid model. Final revision/counts belong in the PR.


## Reviewed settlement mode (WP-05 operator integration)

The SAME command now exposes the existing one-snapshot settled-simulation API:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_project_research.py --settled-paper
.\.venv\Scripts\python.exe scripts/evaluate_project_research.py --settled-paper --include-decisions
.\.venv\Scripts\python.exe scripts/evaluate_project_research.py --settled-paper --as-of "2026-09-15T12:00:00+08:00"
```

The last timestamp is an example historical scope, not reusable authorization or
an assertion that records exist at that time. `--root`, record bound and probability
diagnostic settings retain their existing meanings. Without `--settled-paper`,
the original probability-only path and interrupt behavior are unchanged. No new
command, web view, file loader, scenario capture or business-write option is added.
This entry requires an initialized project with its complete current migration
catalog; it does not install/migrate/repair a database or update an old kit.

`evaluation_kind=settled_paper` identifies the new envelope. Its `evaluation` is
the existing `research-paper-settlement-v1` export. No money, grouping, source
approval or first-attempt selection is recomputed by the console. All original
status counts, cost-policy groups and limits remain. Only `attempts` and nested
`history.decisions` are omitted by default; `decisions_included=false` explicitly
marks this presentation choice, not missing source records. With
`--include-decisions`, both original arrays are retained without filtering.
The original input hash still binds the FULL evaluated source, not a hash of the
shortened display. Group policy labels and hashes remain business metadata.

Read the existing [settlement runbook](research-paper-settlement.md) in the source
repository for the model and provenance contract. Empty, missing, rejected,
failed and pending categories are preserved; no settled sample remains null, not
zero PnL. Different cost/risk policies are not pooled. Amounts are the saved binary
payout minus saved assumed-cost upper bounds. They are NOT actual account PnL,
verified fees, portfolio returns or strategy approval. Source assertions and
recorded-time historical views retain their original limitations. The console's
shape, phase-flag, count and request-option checks do not replace the managed
service's source/amount verification and do not authenticate a forged exporter.

The new mode calls `evaluate_settled_paper_research` exactly once. An incompatible
export, different requested historical cutoff or inconsistent diagnostic options
fails without falling back to probability-only evaluation. Known history/future
blocks and `research_paper_settlement_read_limit` exit 1 with `status=blocked` and
`evaluation=null`. Other operation, serialization or session-cleanup failures,
including `SystemExit(0)`, exit 1 with a fixed sanitized reason. Keyboard interrupt
returns 130 and no success evaluation. Argument errors still exit 2 before project
access. Success is fully serialized after cleanup, then written once; if stdout
itself fails, output may be absent/partial and exit is nonzero without a retry or
attempted second error write. Exit 0 means a successful read/presentation, not a
profitable strategy, fully settled population or approval to trade.

Unit and separate same-assistant adversarial tests cover both views, option
binding, null/group preservation, malformed exports, cleanup, interruption and
output errors. Existing native settlement proof now invokes the actual console
outside the parent lifecycle lease, checks four payouts, historical and failed
reads, unchanged table counts and incomplete-history refusal. Actual extracted-kit
proof invokes the option in BOTH isolated environments using their own locked
Python installations (one missing-paper record and one empty history). Those kit
checks do not pretend to be the four-payout settlement proof. Inputs/models are
synthetic; no user's database or real provider is involved. Final source/CI
identities and preserved first failures are recorded in the implementation PR.

WP-05 remains PARTIAL, G5 open and V1 1/6. Real approved forecasts, input/fee evidence,
remaining operator configuration, D1-D3 and the existing PowerShell 5.1 reliability
issue remain open. No user-machine step or new migration is required by this change.


## Checked read-only output (WP-03 / WP-04 / WP-05)

The existing execution lookup, inventory, resolution lookup and both evaluation
modes reuse the checked resolution emitter. Only after managed cleanup do they
serialize one complete JSON envelope, write it once, check its character count
and explicitly flush. A short write, invalid write count, serialization error or
write/flush failure returns nonzero; no second envelope or repeated query is sent.
Normal JSON, not-found exit status, history gates and calculations are unchanged.

Internal SystemExit during the managed read or cleanup is a failed operation,
not a successful process exit. Existing argument/help exits and operation-level
KeyboardInterrupt behavior are preserved. An output interruption returns130;
other output failures return1. Known database conflicts are recognized from the
original string argument without invoking a custom exception string formatter.

A failed sink may retain a partial prefix and cannot reliably receive another
error envelope. Write/flush success is not proof of consumer receipt. These are
read-only queries: failing output does not authorize model execution, task reclaim,
settlement replay, a new identifier or a claim that earlier writes were rolled back.
No additional business records, provider calls, migrations or file repair occur.

The isolated native inventory test runs the five real command modes with a
short-output sink, then checks the original records and engine state. This is
synthetic stdout fault injection, not a universal OS pipe or crash guarantee.

Primary references checked 2026-09-17:
https://docs.python.org/3.12/library/io.html
https://docs.python.org/3.12/library/exceptions.html
