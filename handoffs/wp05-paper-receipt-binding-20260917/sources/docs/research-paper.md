# Research-to-paper scenario assembly (WP-05)

## Delivered boundary

`ProjectResearchSession.evaluate_paper_research(scenarios=..., **evaluation_options)`
connects the ORIGINAL complete-history evaluator and first-attempt selection to
the existing cost-aware event strategy and order-book fill simulator. It reads
only the same project-private native PostgreSQL instance. No network, model,
portfolio, allocation, new scoring system, migration or durable paper-trade
journal is created. The pure `ResearchPaperEvaluation` accepts detached typed
receipts; it does not authenticate their origin or establish database completeness.
Only the managed path invokes the existing complete-visible-execution-history gate.

**These are retrospective hypothetical scenarios, NOT forward paper-trade evidence,
a fee quote, realized P&L or strategy validation.** Supplied historical timestamps
are checked for consistency, not authenticated. Later market/outcome knowledge
could influence an operator's scenario choices. G5 remains open until prospective
inputs, costs, rejected attempts and simulated executions are retained in the
project database and linked to actual reviewed outcomes. No legacy JSONL or file
archive writer is invoked by this assembly.

Every original research decision remains in the output. Missing inputs, research
failures, later attempts and not-yet-visible records are not silently dropped.
The original evaluator decides which first attempt is eligible, and keeps its
probability diagnostics unchanged. Scenarios attach only to eligible `scored` or
`outcome_pending` records. No new pooled probability score is computed. A changed
outcome cannot change the scenario's selected side or cost decision.

## Explicit inputs and existing methods

An authorized application supplies a tuple of `ResearchPaperScenario` objects.
This is developer assembly, not a new stdin/file loader or a configured model.
Example names below are already reviewed in-memory values, not discovered files
or permission to read the user's database:

```python
from pathlib import Path
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_paper_inputs import ResearchPaperBook, ResearchPaperScenario

# market: existing GammaMarketSnapshot with the historical open market payload.
# costs: existing PaperCostAwareEventCostAssumptions with ALL six explicit values.
# gates: existing PaperCostAwareEventStrategyConfig with explicit thresholds.
scenario = ResearchPaperScenario(
    record_id=original_record.record_id,
    record_sha256=original_record.content_sha256,
    decision_at=reviewed_decision_time,
    market=reviewed_market_snapshot,
    yes_book=ResearchPaperBook(yes_capture_time, original_yes_book_bytes),
    no_book=ResearchPaperBook(no_capture_time, original_no_book_bytes),
    requested_size=explicit_hypothetical_share_count,
    costs=explicit_cost_assumptions,
    gates=explicit_risk_thresholds,
    resolution_risk=explicit_resolution_risk,
    assumptions_id=reviewed_assumption_identifier,
    max_age_seconds=explicit_freshness_limit,
)
with ProjectPostgres(Path(actual_project_root)).session() as research:
    result = research.evaluate_paper_research(scenarios=(scenario,)).to_dict()
```

Evaluation options are the original evaluator's historical cutoff, record bound
and diagnostic settings; the new entry cannot disable execution completeness.
An incomplete visible claim blocks the whole managed evaluation before book
simulation. No fallback loader, age-based reclaim or model retry is permitted.
Original claim/result lookups are separate snapshots over immutable rows. Exact
record equality with the evaluated snapshot is required; missing or foreign
claims abort rather than being adopted. A scenario for an unknown/changed record,
duplicate scenario or extra/mismatched execution is invalid, not silently ignored.

The method returns a recomputable in-memory composition. It does not keep raw
scenario inputs after the caller discards them, and its metadata hashes are not
substitutes for durable source bytes. Do not redirect output to a new business
file journal. Future persistence must use the same project PostgreSQL with a
reviewed versioned provenance design; no such persistence is claimed here.

## Market, time, token and depth checks

Each scenario must reference its exact saved research record hash. Its decision
time is no earlier than the saved forecast and no later than the evaluation
cutoff; it must precede the original request cutoff and supported observation.
The ORIGINAL terminal contract and observation-time gates are reused. Historical
Gamma question/rules must exactly match the original task; changed rules and
path-dependent/unknown contracts are not silently approved.

Market `active`, `acceptingOrders` and `enableOrderBook` must be exact true and
`closed` exact false. Missing/ambiguous status is rejected. Explicit positive
`orderMinSize` and supported `orderPriceMinTickSize` are required. Supported tick
values here are 0.1, 0.01, 0.001 and 0.0001; other values stay unsupported. Requested
size must meet the market minimum. Exactly named Yes/No tokens and distinct
numeric string token IDs are required; reversed label order is supported but
alias/index fallback is not. Token labels remain supplied metadata, not on-chain
authentication.

Both raw books must identify the same condition and the correct outcome token.
They require a 13-digit millisecond timestamp and complete bid/ask arrays. The
normalizer orders valid levels, but invalid/zero/nonfinite/duplicate-price or
off-tick levels are rejected BEFORE normalization; they cannot be silently
converted to zero and discarded. Empty, crossed or locked books are rejected.
Market/books must be captured after the forecast and within the explicit 1..300
second age window at the decision time. Exchange timestamps must not be later
than capture or outside that age window. Clock consistency does not prove source
truth or practical fillability.

The existing fill simulator walks BOTH ask books for the EXACT requested share
count. A NO purchase is still a BUY of the NO token, never a short sale. Partial
walks retain filled/unfilled/requested quantities, but are not eligible in this
complete-size-only slice. There is no automatic size reduction or partial trade
credit. Observed depth is a snapshot assumption, not a queue-position, latency,
IOC or guaranteed-fill model.

## Cost/risk composition and conservative amounts

All six original cost assumptions are mandatory: quadratic taker fee coefficient,
additional slippage, funding, finalization, time and risk cost per share. None are
inferred from a historical default. Zero is a declared scenario assumption, not
proof that a cost is absent. Unknown cost inputs cannot be passed as zero by this
code. Each cost is bounded to [0,1] in this supported scenario slice. Requested
shares are positive and at most 1,000,000; bounded finite decimal inputs have at
most 18 significant digits and nine fractional places. Raw books are at most
128 KiB each and 200 levels per side; at most 100 scenarios and 8 MiB total raw
scenario input are accepted.

The existing strategy receives the original probability/confidence and actual
quoted spreads. Its executable ask input is the WORST consumed ask for that
requested size, not midpoint or displayed average. This is a conservative
full-size entry-price bound. Its original confidence, spread, resolution-risk,
depth and net-edge decisions remain visible.

The legacy fill's average price is display-rounded to 0.001. It is NEVER multiplied
by size to calculate monetary totals here. For the chosen candidate, the adapter
uses conservative arithmetic under a private Decimal context:

- Entry upper bound: requested shares times worst consumed ask.
- Fee upper bound: the maximum of the explicit quadratic `r*p*(1-p)` scenario
  over the consumed price range, rounded UP to six decimals per share, times
  requested shares. The maximum can lie near 0.5 INSIDE the range; fee at the
  worst ask alone is not necessarily an upper bound.
- Non-fee upper bound: the sum of the five explicit extra per-share costs, rounded
  UP to six decimals, times shares. Extra slippage is additional to book-walk
  depth, not a claim about observed latency.
- Expected net-edge lower bound: original side probability minus worst ask,
  fee upper bound and non-fee upper bound, rounded DOWN per share, times shares.

A legacy rounded cost decision can slightly overstate a threshold-edge case. If
this conservative lower bound misses the ORIGINAL minimum net-edge threshold,
the candidate is rejected as `conservative_cost_bound_rejected`; the original
strategy result is retained and no alternative side is reranked into acceptance.
Costs/thresholds and both decisions remain visible. These bounds are mathematical
bounds only WITHIN the stated scenario model, not bounds on real exchange bills.

Official fee documentation says fee parameters are market-specific. This adapter
never fetches a tariff or claims the assumed rate matches one. References checked
2026-09-15: https://docs.polymarket.com/trading/fees and
https://docs.python.org/3/library/decimal.html . Existing generic cost defaults
are left compatible but are not used in this entry.

## Output and acceptance

`history` is the original evaluator's output. `paper_attempts` has one row for
every original decision, original group/condition IDs, reason and source binding.
Eligible inputs expose the existing strategy gates, both hypothetical book walks
and conservative assumed totals. Known invalid market inputs remain explicit
rejections; structural binding/history corruption aborts. Output includes no raw
source bodies, prompts or model summaries; business IDs/hashes still are not
anonymous data.

The hard output boundaries are `retrospective_scenarios_only=true`,
`paper_trades_created=0`, `durable_paper_evidence_created=false`,
`realized_pnl=null`, `actual_billed_fees=null`, `tariff_verified=false` and
`strategy_validation_performed=false`. `paper_scenario_ready` means ONLY that
this explicit hypothetical input clears these existing component checks.

Unit tests and separately designed same-assistant adversarial tests are
`tests/test_research_paper.py` and `tests/test_research_paper_review.py`.
`tests/test_project_postgres_paper_native.py` uses an isolated fresh PostgreSQL,
synthetic BTC/ETH forecasts, missing/failed attempts, exact read replay/restart,
unchanged records and the original incomplete-history block. No user data,
credentials or real provider are used. The dedicated native CI runs on a separate
runner so it does not extend the existing long native job or relax its timeout.
Final revision/first failures/actual CI evidence belong to the implementation PR.

WP-05 remains PARTIAL; G5 requires durable prospective simulation inputs/execution,
reviewed real outcomes and cost-aware realized evaluation. Original statistical
promotion thresholds, D1-D3 and the existing WP-06 reliability issue are unchanged.

## Prospective input/result capture in the same database (WP-05)

The earlier `evaluate_paper_research` method stays read-only and retrospective.
`capture_paper_research(scenario=..., allow_paper_write=True)` now saves ONE
immutable simulation receipt for an original research record. It reuses that
same evaluator and simulator rather than making a second strategy or order path.
`inspect_paper_research(record_id=...)` reads the original receipt and raw inputs
through the same managed project instance. No input file, model, public fetch or
real exchange operation is added.

```python
with ProjectPostgres(Path(actual_project_root)).session() as research:
    # scenario is the already reviewed ResearchPaperScenario described above.
    saved = research.capture_paper_research(scenario=scenario, allow_paper_write=True)
    metadata = saved.to_dict()
    original = research.inspect_paper_research(record_id=scenario.record_id)
```

The write flag must be exact `True`; no database access occurs without it. One
original record ID is the database primary key. Reusing the identical canonical
input returns its original receipt, even after expiry or restart. Changed raw
bytes, capture times, quantity, costs or assumptions under that record ID conflict;
changing an assumptions label is not permission to replace a rejection. No
automatic paid call, capture retry, new task ID, refresh or outcome correction is
performed. The original forecast, candidate, outcome and evaluator are unchanged.

The `research-paper-input-v1` codec retains exact raw Gamma/YES/NO bytes as base64,
including malformed book bytes whose simulation is rejected. Input envelopes are
limited to 4 MiB; result envelopes to 1 MiB. Decoding verifies hashes, all required
fields and exact canonical re-encoding. Reads check sizes before fetching bodies.
The output shows metadata, original input/result hashes, selected first-record ID,
complete-history snapshot time/hash and the stored per-record decision. Raw input
is available only via the typed receipt's scenario; it is not in `to_dict()`.
These IDs/hashes still are business metadata, not anonymized public data.

### Admission, atomicity and historical limits

New captures first run the ORIGINAL complete-visible-history gate in a consistent
read snapshot, then recheck the original record and first-attempt selection under
the existing event lock in a separate short write transaction. The receipt records
that earlier history time/hash; it is NOT a final atomic whole-project snapshot.
New incomplete claims elsewhere after that snapshot can exist, so use the original
strict evaluator before making any later complete-history claim. Existing receipt
inspection/replay does not bypass that evaluator or certify today's completeness.

The database stamps insertion time, checks the actual clock against the original
forecast cutoff, and requires a recent decision (the explicit scenario age bound,
1..300 seconds). Callers cannot backdate the database stamp. A deferred constraint
also checks cutoff near transaction completion. **This is prospective database
admission, not a guarantee that WAL flush or COMMIT acknowledgement happened before
the cutoff.** The receipt explicitly sets `commit_before_cutoff_verified=false`.
Supplied raw timestamps still do not authenticate when a remote source was known.

Validly bound ready, rejected and failed/later-attempt simulation decisions retain
both original inputs and result in ONE append-only row. Missing original results,
invalid binding, expired admission or a complete-history failure cannot enter the
capture transaction. Such pre-admission failures remain errors, not a fabricated
saved rejection. They do not erase the original research history. There is no
new durable error journal for failures that could not commit.

UPDATE, DELETE and TRUNCATE are refused; a primary key prevents competing inputs
for one original. All public writes use the existing validated local transaction
helper and instance binding. An error may occur after a successful COMMIT; do not
infer absence of writes from a thrown exception. Inspect the SAME record, and only
explicitly replay the SAME input. There is no automatic retry. Concurrent exact
calls serialize on the original event lock; conflicting input cannot overwrite.

Readback reconstructs this single decision from its immutable original/first
record references and the retained inputs with the existing selector/simulator,
then compares exact canonical result bytes. It is not a present-day full-history
re-evaluation and never uses a later outcome to change the selected side. A future
engine/rule change that produces different bytes fails closed for review; this
version does not reinterpret or migrate historical results automatically.

The output marks `durable_simulation_evidence=true` and
`prospective_database_admission=true`. It still has `paper_trades_created=0`,
`realized_pnl=null`, `tariff_verified=false` and `source_authentication_performed=false`.
A stored book-walk result is simulation evidence, not an actual filled order or a
portfolio ledger. Cost-aware realized settlement aggregation is still outstanding.

### Migration and proof

`20260915020000_research_paper_simulations.sql` is the 67th catalog entry. All 66
older migration bytes are unchanged. It creates only the append-only simulation
table and validation triggers in `research_capture`; the existing restricted app
role grant procedure applies. New code requires the existing explicit migration
operation on an authorized SOURCE instance. Nothing automatically upgrades a user
database. Do not overlay or edit an old immutable kit or its integrity manifest.

The new isolated native test upgrades a disposable66-schema instance to67, checks
an existing forecast, saves ready/rejected/failed inputs, runs concurrent exact
replay, injects acknowledgement loss AFTER a real COMMIT, checks age-expired replay
and restart, exercises denied mutation/hash writes and real deferred-cutoff rollback,
and keeps the original incomplete-history block. The historical65->66 budget test
keeps its old target; current-catalog regression assertions now expect67.

Local adversarial review first reproduced three INSERT-receipt provenance binding
omissions and then fixed them without weakening assertions. Same-assistant separate
self-review is not a third-party audit or a zero-defect guarantee. Final fixed-head
CI counts and original failures are recorded in the implementation PR. Tests use
synthetic models and fresh private databases, never user business data or paid calls.
WP-05 remains PARTIAL: real source/fee acceptance, outcome/P&L linkage, D1-D3 and the
known WP-06 reliability issue are not closed by this engineering evidence.

Transaction semantics references checked 2026-09-15:
https://www.postgresql.org/docs/17/explicit-locking.html
https://www.postgresql.org/docs/17/sql-createtrigger.html
https://www.psycopg.org/psycopg3/docs/basic/transactions.html

## Capture and inspect with the existing task command

`manage_research_tasks.py capture-paper` and `inspect-paper` invoke the same
managed storage APIs above. They add no queue, strategy, file input loader,
provider, timestamp generation or automatic approval. Existing batch/turn/budget
commands and `evaluate_project_research.py --settled-paper` remain unchanged.

The input must be the exact canonical `research-paper-input-v1` string produced by
`encode_paper_scenario(reviewed_scenario)`, together with its independently retained
SHA256. This is not a JSON report reconstructed from metadata. An approved producer
can prepare the already-reviewed in-memory input using the existing codec:

```python
from polymarket_alpha_lab.research_paper_capture_codec import encode_paper_scenario, checksum
canonical_input = encode_paper_scenario(reviewed_scenario)
reviewed_sha256 = checksum(canonical_input)
```

The codec emits ASCII JSON with base64 raw evidence and escaped non-ASCII text.
Do not decode/re-encode its evidence for a shell pipeline. Binary stdin is read
through EOF in bounded chunks, including short reads. At most4MiB of canonical
payload is accepted, plus zero or ONE trailing LF/CRLF transport terminator.
BOMs, pretty printing, extra whitespace/objects/lines are rejected rather than
normalized. The reader consumes no more than the cap+CRLF+one overflow byte.
The supplied hash and original record ID are checked before opening the project.

For modest-sized already-reviewed canonical input, no input file is needed:

```powershell
$recordId = Read-Host 'Original research record ID'
$expectedHash = Read-Host 'SHA256 supplied with the reviewed canonical input'
$payload = Read-Host 'Paste the complete canonical ASCII JSON'
$payload | .\.venv\Scripts\python.exe scripts/manage_research_tasks.py capture-paper --record-id $recordId --input-sha256 $expectedHash --allow-paper-write
$code = $LASTEXITCODE
if ($code -ne 0) { throw 'Capture did not return a complete receipt; inspect the same record before explicit replay.' }
```

Larger inputs can be sent by the approved producer over the same stdin protocol.
Input preparation, source fetching and human approval are NOT supplied by this
command. A digest proves matching bytes, not a signature or source/reviewer
identity. Never compute a replacement hash from corrupted received text just to
make it pass. Windows PowerShell encoding can differ from Python's; the canonical
ASCII envelope avoids sending raw non-ASCII evidence through that conversion.

Query without stdin or write permission:

```powershell
.\.venv\Scripts\python.exe scripts/manage_research_tasks.py inspect-paper --record-id $recordId
$code = $LASTEXITCODE
```

`--root` remains a global option placed BEFORE the subcommand. It must identify
an already-authorized physical project. The managed session can start an existing
initialized server and stops only one it started. Neither mode initializes,
migrates, adopts a database or repairs an old kit. Missing write approval never
consumes stdin; malformed arguments/input never open the project. Inspection
does not consume stdin or write business evidence.

Exit0 with `paper_receipt_returned` means a stored receipt was returned, including
exact replay or a SAVED REJECTION. Read the nested simulation status:
`paper_scenario_ready`, `paper_scenario_rejected` or `not_simulated`. None authorizes
trading. Original `paper_trades_created=0`, `realized_pnl=null`, source/fee and
prospective-admission limits remain unchanged. No output is serialized before
managed cleanup succeeds; returned receipt ID/full capture input must match.

Exit1 means operation/validation/cleanup/output failure; a capture may already have
committed. Exit2 means invalid input/arguments or missing write permission. Exit3
means one inspected receipt was not found, NOT an empty project. Exit130 means
interruption. Once capture enters a managed session, `business_writes_possible`
remains true, even for replay/failure. A short/broken output write or failed flush
returns nonzero without a second attempted envelope. Check exit status even when
stdout contains a partial or apparently complete JSON value.

After uncertainty, inspect the SAME record and explicitly replay only the SAME
canonical input/hash when appropriate. No automatic retry, changed task ID or
file-backed recovery record is created. Output is the original metadata receipt,
not raw input/evidence or exception text. IDs/hashes/assumptions are still business
metadata; do not redirect them to a new business file journal.

Tests: `test_research_paper_operator.py`, `test_research_paper_operator_review.py`,
and `test_project_postgres_paper_operator_native.py`. The native proof invokes
actual command children OUTSIDE the parent lifecycle lease, covering synthetic
BTC/ETH ready/rejected saves, replay/conflict, restart/query and unchanged originals.
No user database or real model/source is used. G5/G6, D1-D3 and the older PS5.1
reliability issue remain open; exact final-revision results are retained in the PR.

Stream/encoding references checked2026-09-15:
https://docs.python.org/3.12/library/sys.html#sys.stdin
https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding


### Bind capture receipts to the originally approved input

`capture-paper` validates the returned scenario against the caller's original
`--input-sha256`, not against the scenario object after passing it to the managed
capture adapter. A faulty adapter must not turn its own changed quantity, costs
or assumptions into the reference that authorizes its receipt. A mismatching
receipt returns the existing `paper_operator_operation_failed` response, with no
success result and no second capture. Possible committed writes remain explicit.

Conversely, when the original approved receipt is intact and only a collaborator's
argument object changes after storage, the correct receipt is still returned.
`inspect-paper` keeps its original ID-based lookup contract; it does not invent
an approval hash. Original canonical input decoding, framing, permissions, output
format, simulator, storage and cleanup behavior are unchanged.

This is a receipt-consistency check for faulty collaborators, not a sandbox for
arbitrary Python code, proof of what an untrusted store committed, or permission
to repair or overwrite a saved scenario. Frozen dataclasses are not an absolute
immutability boundary. After uncertainty, inspect the original record; never
infer rollback or automatically create another record or retry a capture.
Reference: https://docs.python.org/3.12/library/dataclasses.html#frozen-instances
