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

## Supplied historical paper scenarios (WP-05)

The SAME `scripts/evaluate_project_research.py` accepts `--paper-stdin` to join
explicit historical execution assumptions to original captured forecasts and the
existing strict evaluator. This is the WP-05 offline assembly slice, not a new
scoring system, paper-order journal, forward-test certification or live executor.
**G5 remains open.** There is no new database write, migration, model, network
request, file-backed business history, fee discovery or automatic settlement.

The existing managed session reads the complete visible execution history first.
Any incomplete claim anywhere still blocks evaluation, even if the requested
scenarios mention only completed records. It never falls back to the older
weaker loader or filters down to successful forecasts. Per-scenario original
request lookups are subsequent immutable-row reads, not a combined whole-project
transaction. Record hashes must agree with the original strict evaluation read.
A missing or foreign original stops the operation instead of being ignored.

### Explicit in-memory input, no alternate persistence

Use the fixed version's installed environment and actual reviewed project root:

```powershell
# ASCII JSON / Unicode escapes avoid PowerShell 5.1 pipeline recoding of evidence.
$scenarioJson = Read-Host 'Paste reviewed scenario JSON (ASCII with Unicode escapes)'
$scenarioJson | .\.venv\Scripts\python.exe scripts/evaluate_project_research.py --paper-stdin
$code = $LASTEXITCODE
```

This example consumes in-memory input, not a file journal. The command does not
initialize or migrate the database, discover another database, or overlay an old
immutable kit. Existing `--as-of`, `--max-records`, `--buckets` and sample-threshold
options retain their original meaning. In paper mode all original per-record
reasons are included regardless of `--include-decisions`; omitting that option
cannot hide failed, later, pending or uncovered records. Without `--paper-stdin`,
the ordinary diagnostic path is unchanged and stdin is not consumed.

The closed input is `{"scenarios":[...]}` with 1..100 distinct `record_id` values.
Every scenario must contain these exact fields (none is defaulted):

| Field | Meaning |
| --- | --- |
| `record_id`, `request_sha256` | Original execution identity and full canonical request hash, from the existing execution inspection. |
| `decision_at`, `book_captured_at` | Explicit aware ISO timestamps for the modeled decision and book collection. |
| `market` | Exactly `market_slug`, `fetched_at`, `raw_json`; the last is the verbatim public Gamma JSON as a JSON string, not an object reserialized by this parser. |
| `book_json` | Verbatim public CLOB book body as a JSON string. Raw and normalized-book hashes are distinguished. |
| `side`, `requested_shares` | Exact `yes` or `no`; positive decimal-string nominal quantity. Both sides BUY their respective outcome token against asks. |
| `costs` | Exactly seven decimal-string per-filled-share assumptions: `fee`, `extra_slippage`, `funding`, `finalization`, `time`, `risk`, `capital`. Explicit zero is allowed, missing is not zero. |
| `cost_reference_sha256`, `settings_version` | Hash of the operator's reviewed cost-assumption reference and its named settings version. These are assertions, not fee authentication. |
| `min_net_edge`, `min_confidence`, `max_spread` | Explicit decimal-string policy limits in [0,1], not investment advice. |
| `max_entry_cost` | Positive cap on the modeled filled entry notional plus all seven modeled costs. No automatic resizing to pass this cap. |
| `max_snapshot_age_seconds` | Explicit integer 1..600 for collection-time age at the historical decision. |

Numbers are exact finite decimal strings with at most six fractional places and
bounded magnitude (nonnegative, at most 1,000,000 for quantity, cost and notional
limits). Probability inputs with unsupported precision fail rather than silently
rounding. Unknown/duplicate keys, implicit dates, numeric JSON floats, booleans
used as amounts, invalid UTF-8 and missing fields are rejected. The stdin limit
is 4 MiB; aggregate raw scenario input is also bounded at 4 MiB. Each book body
is at most 64 KiB and has at most 100 levels on each side. Exact DTO inputs to the
managed Python API are revalidated and copied as well:

```python
with ProjectPostgres(Path(actual_project_root)).session() as research:
    # scenarios: a tuple of explicitly prepared ResearchPaperScenario objects.
    result = research.evaluate_paper(scenarios=scenarios).to_dict()
```

### Source and time boundaries

The original record must be the first eligible attempt under the unchanged
team/model/protocol/condition selection rule. Later attempts do not replace an
earlier failure. Only saved completed BTC/ETH forecasts with `scored` or
`outcome_pending` original decisions reach the simulation path. All other original
records remain visible with their reason or `scenario_missing`.

The scenario's market must have the same condition, slug, exact original question
and rules, explicit active/open/accepting-orders flags, exactly `Yes` and `No`
labels and two distinct explicit token IDs. Reversed label order is mapped by
label, never by a guessed position. The CLOB `market` and `asset_id` must match
that mapping. Book price/size strings are checked before calling the existing
normalizer: nonnegative finite bounded values, price at most one, positive tick
and minimum order size, tick-aligned unique prices, and explicit `neg_risk=false`.
Zero-size/zero-price levels cannot supply executable depth. A two-sided,
non-crossed book, minimum size, confidence and spread limits are required.

Collection times must fall after the saved forecast and no later than the modeled
decision, within its explicit age bound. The decision must be before the original
request cutoff and not after report as-of time. The existing crypto contract/time
gate is reused; no new rule parser or expanded contract support is introduced.
All supplied clock fields are normalized to UTC before comparison and hashing.
A scored outcome must bind the original cutoff and cannot claim resolution before
the declared observation minute has closed. An unrecorded outcome stays unknown;
it is never treated as NO, zero payout or zero profit.

**These checks verify consistency, not authenticity or collection provenance.**
Collection timestamps, raw public bodies and cost references are supplied by the
caller. The CLOB server's raw `timestamp` is retained in the body but not
interpreted or certified by this offline adapter; it does not establish live
server freshness. No signature, source identity, fee tariff, actual invoice or
real order acceptance is authenticated. A fresh timestamp cannot turn this into
a certified forward test. The code deliberately reports
`source_authentication_performed=false`, `book_server_timestamp_verified=false`
and `forward_test_provenance_established=false`.

### Existing fill/edge arithmetic and the meaning of amounts

The adapter reuses `simulate_order_book_fill` and its existing `_walk_levels`
implementation, then passes the result to `PaperProbabilitySideEdgeInput` /
`build_paper_probability_side_edge_report`. Original research probability scores,
groups and sample thresholds are returned by the original evaluator unchanged;
no second Brier/calibration implementation, pooled score or pooled profit is added.
The private walker dependency is intentional: the public fill's three-decimal
average is a display value, not an exact cash basis. The adapter reuses the same
walk's unrounded notional instead of multiplying that rounded average by shares.

Amounts are in **unit-payout accounting**: one correct modeled share pays one
unit. They are not a statement about a current exchange collateral denomination,
redemption, FX rate or realized account balance. Quantity is a nominal scenario
parameter, not a suggested position or capital allocation.

For filled quantity q, exact walked entry N and sum of the seven explicit
per-filled-share costs c, modeled expected net is q*P(side)-N-q*c. After a visible
consistent outcome, modeled settled net is payout-N-q*c. Time, risk and capital
terms are economic drags, not necessarily billed cash expenses. The output calls
these amounts modeled, not realized trading profit.

Both sides use ask depth. The ask price already pays the crossing spread, so
there is NO extra spread surcharge. Walked depth impact is already in N, so it is
NOT charged again in monetary totals. For the existing six-place edge reducer,
the per-share depth impact above best ask is rounded UP, then added to explicit
extra slippage; this can only make the edge more conservative. Extra slippage
must exclude the walk already represented in N. Fees are absolute supplied
per-filled-share costs, not an assumed current fee rate or a nonlinear fee formula.

Example: three shares consume two asks at0.40 and one at0.50. Entry is1.30,
not3*0.433=1.299. Fee0.01 plus extra slippage0.02 costs0.09. With P(side)=0.80,
modeled expected net is1.01. If that side wins, modeled settled net is1.61; if it
loses, -1.39. These are synthetic accounting examples only. Insufficient depth
keeps original requested quantity, filled quantity and unfilled remainder; it
does not silently redefine the request to fit available liquidity.

Rows that fail cost/risk checks may retain the hypothetical fill and accounting
for explanation. `status=blocked` means it was NOT accepted even when those fields
are populated. `hypothetical_fill_only=true` always applies. No portfolio, orders,
positions or economic-sample count is mutated. An exit0 means the read/computation
succeeded, not that every row was accepted or that a strategy is profitable.

The reused edge reducer's context-fresh flags mean that this supplied scenario
passed the original rule/time and quote-collection checks. They do not assert an
observed outcome exists: pending outcomes retain null settled payout and return.

### Persistence, failure and remaining G5 work

The adapter makes zero business writes. Raw inputs live in memory during the
call; hashes are not a substitute for retaining source bytes in approved storage.
There is no durable scenario/quote/cost admission, forward-provenance ledger,
new paper-order journal or retry loop. Reusing a supplied scenario after changing
its side/costs is a different retrospective what-if, not a new independent sample.
The output retains all original attempted records, missing-scenario counts,
policy-blocked counts and original group keys. It deliberately avoids aggregating
selected favorable scenarios into a performance claim.

Managed session cleanup must complete before success is printed. Invalid stdin
exits2 before project access; incomplete-history and operation/cleanup failures
exit1 without a successful result; keyboard interruption exits130. Errors are
sanitized, including SystemExit, in the opt-in paper mode. Without `--paper-stdin`,
the pre-existing KeyboardInterrupt/SystemExit propagation contract is preserved;
no paper stdin is consumed and no success is printed on those exits.
Read operations may start/stop the project's private engine but do not mutate its business records. Four old opt-in database
checks, when not run, remain explicitly unexecuted rather than treated as passes.

Offline tests are `tests/test_research_paper.py` and separately designed
`tests/test_research_paper_review.py`. The review first reproduced two defects:
accepting an outcome before the actual observation close and inconsistent hashes
for timezone-equivalent market timestamps. Both were fixed without weakening the
original assertions. Independent Fraction arithmetic checks fill notional,
partial depth, costs, YES/NO payout and conservative edge rounding.
The existing real-clock native confirmation scenario is extended rather than
creating another DB lifecycle: it checks pending/settled paper results, the actual
console, restart, unchanged originals and a real incomplete-claim denial.
Final-revision test results and first failures are recorded in the implementation
PR, not assumed from these test descriptions.

**WP-05 stays PARTIAL / G5 open.** Durable approved scenario/source/cost capture,
actual matching BTC/ETH engineering samples, independent source/settlement review
and fixed-version end-to-end acceptance remain. This offline read assembly does
not fulfill those by itself. D1-D3, real-model authorization, all existing strategy
validation gates and the known WP-06 PowerShell first-run reliability limitation
are unchanged.

Primary format/arithmetic references reviewed 2026-09-15:
https://docs.polymarket.com/api-reference/market-data/get-order-book
https://docs.python.org/3/library/decimal.html
