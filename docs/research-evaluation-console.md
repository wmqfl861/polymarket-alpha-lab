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

## WP-05: supplied-book hypothetical paper replay

The SAME console accepts `--paper-scenarios`. It reads one UTF-8 JSON array from
stdin (maximum 2 MiB, 0..100 scenarios), first loads the original strict execution
history, then joins supplied books/cost assumptions to that history. It does not
write paper trades, queue work, fetch prices, run a model, confirm outcomes or
create another probability scoring system. Incomplete claims anywhere still
block the whole evaluation. Existing output without this flag is unchanged.

This is **hypothetical reconstruction**, not proof of forward paper trading.
Supplied timestamps, source IDs and rates are assertions; source authentication
and prior policy selection are not established. The operator may have chosen
snapshots or scenarios after seeing outcomes. `forward_paper_evidence=false`,
`scenario_selection_bias_possible=true` and `market_fee_verified=false` are always
present. G5 and statistical strategy validation remain open. No file-backed
business history is introduced; retain genuine source evidence only in the
approved PostgreSQL workflow. This command itself keeps supplied raw inputs only
in memory, so it is not a durable evidence-capture path.

### Explicit inputs; no inherited fee default

Each array item has exactly these fields, with no inferred monetary values:

- `record_id`, `request_sha256`, `record_sha256`: the exact saved original request
  and completed-result identity. A mismatch or unknown ID fails the whole join.
- `simulated_at`: aware ISO time; `shares`: positive Decimal string, at most
  1,000,000; `resolution_risk`: Decimal string in [0,1]; `cost_reference`: an
  operator-chosen project identifier, not an authenticated tariff reference;
  `max_snapshot_age_seconds`: exact integer 1..300.
- `market`: `market_slug`, `fetched_at`, `raw_json` (a string containing the exact
  supplied Gamma JSON, not a nested object). Each raw body is at most 128 KiB.
- `yes_book`, `no_book`: each has `captured_at` and `raw_json` string. Book JSON
  must have `market` matching the condition and a numeric-string `asset_id`
  matching Gamma's explicit Yes/No `clobTokenIds`. `bids`/`asks` each contain at
  most 200 distinct price levels with only string `price` and `size`. Prices are
  strictly between 0 and 1; sizes are positive. Non-finite, excessive-precision,
  duplicate or crossed levels are rejected, not silently skipped. The original
  normalizer sorts valid levels best-first; original raw hashes remain available.
- `costs`: all six explicit Decimal strings: `taker_fee_rate`,
  `slippage_cost_per_share`, `funding_cost_per_share`,
  `finalization_cost_per_share`, `time_cost_per_share`, `risk_cost_per_share`.
- `config`: `config_version` plus all five Decimal-string thresholds:
  `min_confidence`, `max_spread`, `max_resolution_risk`, `min_ask_size`,
  `min_net_edge`. These are existing cost/risk gate settings, not live approval.

Decimal inputs have at most six fractional digits, bounded exponents and finite
values. The taker fee parameter is 0..1; other nonnegative cost inputs are at most
1,000,000 cash-equivalent units per share. Unknown or duplicate JSON keys, missing
costs, numeric JSON in place of Decimal strings and oversized input fail before
project access. Empty `[]` is valid: it still checks the entire execution history
and exposes all missing scenarios instead of pretending the project is empty.

An authorized application can construct the typed inputs with
`ResearchPaperBook` / `ResearchPaperScenario` and call:

```python
with ProjectPostgres(Path(actual_project_root)).session() as research:
    replay = research.evaluate_paper(scenarios=reviewed_scenarios)
    output = replay.to_dict()
```

Or pass the equivalent reviewed JSON to the existing console, using the fixed
source/kit's installed Python environment:

```powershell
# One-line ASCII JSON; use \uXXXX escapes for Unicode under PowerShell 5.1.
$scenarios = Read-Host 'Paste the reviewed paper-scenario array'
$scenarios | .\.venv\Scripts\python.exe scripts/evaluate_project_research.py --paper-scenarios
$code = $LASTEXITCODE
```

There is no filename argument or JSONL queue. `--root` retains its existing
managed-root meaning. Do not overlay an old kit or copy its `.local` directory.
Both the supplied array and output can contain business-sensitive IDs, prices
and probabilities; they are not suitable for an unreviewed public upload.

### Original history, then executable depth, then outcome

All original evaluator decisions remain in the output, including failed/blocked,
later, late and not-yet-visible records. First recorded attempt per
team/model/protocol/event is unchanged. Eligible attempts without supplied inputs
are `missing_scenario`; invalid source/chronology becomes `rejected_scenario`.
No attempt disappears merely because it has no trade or no favorable outcome.
Counts are whole-history counts, not a selectively profitable denominator.

A scenario requires the original saved BTC/ETH result, matching condition/slug
and byte-exact original question/rules. The market must explicitly be active,
not closed and accepting orders; an explicit disabled order book is rejected.
Snapshots must be no earlier than the saved forecast and no later than the
simulated decision, within the supplied freshness bound. The original supported
terminal-contract/observation gate requires simulation before the original
cutoff and cutoff before candle open. Venue timestamps within raw source bodies
are not authenticated by these caller-supplied collection timestamps.

Both books are walked by the original `simulate_order_book_fill`. Partial and
empty fills remain visible, but only a full requested size can be selected.
Missing bid/ask rejects the scenario. The existing cost-aware strategy and risk
gates screen at the **worst consumed ask** on each side, not midpoint or rounded
average. This is a deliberately conservative selection rule, not the highest
possible VWAP-derived edge. Original confidence, spread, depth, resolution-risk
and net-edge gates remain active. No actual venue tick/minimum-order/funding or
balance constraints are certified; `venue_execution_constraints_verified=false`.

After selection, notional is recomputed by the SAME exact depth walker. The old
fill's three-decimal `average_price` is display-only and is never multiplied to
recover monetary cost. Each consumed level is charged using the EXISTING
six-decimal per-share fee reducer; explicit additional per-share costs are then
added. Depth slippage is already inside notional and is exposed separately, NOT
added a second time. `slippage_cost_per_share` is only an operator-specified
additional residual assumption beyond that walked depth.

The reused fee curve is `rate * price * (1-price)`. This path never calls the
legacy default-cost factory. Official fee documentation describes market-specific
rates and five-decimal fee rounding; the historical reducer's six-decimal
per-share aggregation is therefore an explicit **scenario approximation**, not
an exchange invoice or a verified executable tariff. Cash-equivalent costs and
unit binary payouts do not model token fee deductions, rebates, depegs or actual
redemption mechanisms. Review all assumptions before any real use.

For example, a three-share synthetic buy consumes one at 0.42 and two at 0.44.
Notional is 1.30, although the old display average is 0.433. With supplied rate
0.07 and residual cost 0.001 per share, the reused model estimates fee 0.051548,
additional cost 0.003, and total 1.354548. A unit payout for all three shares gives
hypothetical net 1.645452; a zero payout gives -1.354548. These are deterministic
example inputs, not real trade results or recommended position sizes.

Only AFTER size, side and costs are determined does the join inspect the original
evaluator's visible outcome. Pending outcomes keep payout/net null; a generic
historical outcome whose timing conflicts with the original cutoff/minute close
is explicitly rejected for payout calculation. Later outcomes never change the
simulated side/cost. No pooled return, annualized return, portfolio allocation or
claim of statistical independence is produced. The unchanged original Brier/log
loss groups and insufficient-sample warnings remain alongside the paper replay.

### Errors and evidence boundaries

Exit 0 means the evaluation operation completed, including rejected/missing or
unselected scenarios, NOT that all scenarios traded or made money. Always inspect
row statuses and counts. Invalid stdin exits 2 before DB access; operational or
history failures exit 1; keyboard interruption exits 130. Successful output is
emitted only after managed cleanup. Typed replay inputs are revalidated and
recomputed; arbitrary returned dictionaries and foreign scenario receipts are
not printed as success. Raw evidence/model text is omitted, but IDs/hashes and
probabilities remain visible in this explicitly selected mode.

The strict history is one DB snapshot; the bounded original request lookup is a
second read over immutable claim/result rows. New activity can occur between
snapshots; this is not a claim of a permanently complete project. Neither read
writes application data or auto-retries. No live client or alternate persistence
path is introduced.

Tests: `test_research_paper_evaluation.py`, separate same-assistant adversarial
`test_research_paper_review.py`, and opt-in
`test_project_postgres_paper_native.py`. The native proof uses fresh isolated
PostgreSQL, prospective synthetic BTC/ETH forecasts, the existing manual
confirmation path and original evaluator, read-only row counts, restart and
incomplete-history refusal. Final counts, failures and source identities are in
the implementing PR, not implied by this runbook. This is not G5 completion.

Primary references checked 2026-09-15 (not runtime destinations):
https://docs.polymarket.com/trading/fees
https://docs.python.org/3/library/decimal.html
