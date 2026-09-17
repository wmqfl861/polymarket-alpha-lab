# Shared model-call allowances (WP-02 / WP-03)

## What is delivered, and what is NOT a monetary guarantee

Explicit approved research requests can share one immutable PostgreSQL allowance
across individual executions, batches and rotation turns. Every wrapped client
call first commits a nonrefundable reservation. Limits cover total reservations,
a conservative **fixed upper charge per call**, message UTF-8 bytes and requested
output tokens. There is no provider selection, credential lookup, automatic
pricing download, implicit authorization, new broker or alternative persistence.

**This is enforcement of internal reserved upper charges, NOT independent
verification of the provider bill.** `per_call_micros` must conservatively cover
ALL billing for one operation under the actual approved adapter: prompt tokens,
tool schemas, completion/reasoning tokens, other fees and any provider overhead.
A wrapper cannot certify that an arbitrary callback honors an output limit or
makes exactly one request. The factory must be inert; its `complete` method must
make at most one operation, with no hidden retries, fallbacks or extra fees beyond
the attested bound. `bound_reference_sha256` binds reviewed pricing/adapter terms,
not a trusted live price oracle. The labels do not authenticate a provider/model.

No project provider/rate or fee bound has been selected/verified in this delivery.
D1 provider/model, D2 data permission and D3 affordable enforceable budgets are
still owner decisions. An unverified or unbounded adapter must NOT be enabled for
real research; configuration alone does not close G2. Tests use synthetic clients
and synthetic integer charges, not real prices or invoices. Existing unwrapped
APIs remain explicit compatibility paths; this is not a sandbox against the
owning user or code deliberately bypassing the wrapper.

## One explicitly approved policy

`ModelCallBudget` specifies `budget_id`, provider/model labels, an uppercase
three-letter currency label (no FX conversion), `total_micros`, `per_call_micros`,
`max_calls`, `max_message_bytes`, `max_output_tokens`, a UTC-normalized expiry,
1..100 exact `(record_id, request_sha256)` pairs, a reviewed bound-reference digest,
and explicit `cost_bound_attested=True`. Money uses INTEGER millionths of that
currency, never floating point. The maximum policy is 32 KiB and 3200 calls.
No amounts or prices are inferred from a model name. Even free models require an
explicit conservative bound and finite call count, not an unlimited default.

```python
from pathlib import Path
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_model_budget import ModelCallBudget

# These values must come from actual operator-reviewed inputs/adapter/rate bounds,
# not from a model answer, example price, .env scan or hidden default.
policy = ModelCallBudget(
    budget_id=approved_budget_id, provider_id=approved_provider_id,
    model_id=approved_model_id, currency=approved_currency,
    total_micros=approved_total_micros, per_call_micros=verified_call_upper_bound,
    max_calls=approved_call_limit, max_message_bytes=approved_message_byte_limit,
    max_output_tokens=approved_output_limit, expires_at=approved_expiry,
    request_keys=tuple((r.record_id, r.content_sha256) for r in reviewed_requests),
    bound_reference_sha256=reviewed_bound_reference_sha256, cost_bound_attested=True,
)
with ProjectPostgres(Path(actual_project_root)).session() as research:
    receipt = research.create_model_budget(policy=policy, allow_budget_write=True)
    result = research.run_budgeted_research(
        request=reviewed_requests[0], budget_id=policy.budget_id,
        model_factory=approved_inert_factory, allow_model_calls=True,
    )
    remaining = research.inspect_model_budget(budget_id=policy.budget_id).to_dict()
```

Creating a policy transmits no research and creates no task/market. It is immutable:
exact same-ID/same-content creation returns the original receipt even after expiry;
changing amount, price bound, request roster or any other field conflicts. No topup,
reset, refund, rollover or automatic creation of a replacement budget is supplied.
The existing canonical request hash binds the complete evidence/configuration; a
changed task/model/request cannot silently use an old enrollment.

## Connect the same allowance to existing task operation

Already admitted batches/rotations accept explicit `model_budget_id`:

```python
with ProjectPostgres(Path(actual_project_root)).session() as research:
    report = research.run_research_rotation(
        rotation_id=approved_rotation_id, turn_id=new_explicit_turn_id,
        batch_ids_to_run=approved_batch_ids, model_factory=approved_inert_factory,
        allow_model_calls=True, max_tasks=approved_task_limit,
        max_workers=approved_worker_limit, model_budget_id=approved_budget_id,
    )
    remaining = research.inspect_model_budget(budget_id=approved_budget_id).to_dict()
```

`run_research_batch` accepts the same optional keyword. It does NOT create policies
implicitly. Omitting it retains the earlier explicit unbudgeted API behavior,
not proof of a monetary cap. A rotation replay remains inert regardless of this
keyword; a new turn cannot refund or duplicate prior call reservations. This
policy is shared across calls using this budget ID, not across different policies,
projects, currencies or external provider accounts. Worker caps remain per drain;
this does not establish global concurrent-I/O limits.

A known empty/expired policy rejects NEW work before a task claim, leaving the
original input pending until its existing research cutoff. Original completed or
incomplete tasks still replay without calling a model. Concurrent consumption can
exhaust an allowance AFTER another task has started; that task saves an ordinary
failed result with the original `model_failed` reason. Budget-denial and provider
failures are not newly distinguished in the existing result codec. `model_calls`
continues to count attempted `complete` calls, including denied wrapper attempts;
it is NOT an invoice or exact count of transmitted provider requests.

## Reservation before any client entry

The existing execution claim commits first. A lazy wrapper is then constructed
without constructing the provider. On each `complete`, it checks message/token
bounds, exact enrollment and an existing matching uncompleted claim, then reserves
one full upper charge in a short original managed transaction. SQL independently
checks database time, policy/request expiry, claim/model/hash binding, per-request
call order and aggregate amount/count limits. The provider factory and method are
entered ONLY after a newly inserted permit's COMMIT and connection cleanup return.
No transaction or budget lock is held over model I/O.

The append-only `(record_id, call_number)` key prevents another budget/client from
resending the same original call ordinal. The per-budget unique sequence and
transaction lock prevent parallel reservations from exceeding the configured cap.
Replaying a permit is not permission to transmit again. Sequence reuse/conflicts,
uncertain COMMIT acknowledgement and errors fail closed, not resend. Every error
permanently closes that wrapper instance to further calls. Interrupts propagate;
ordinary errors are fixed-code/redacted before reaching the original model loop.

A permit is NEVER refunded, even when a factory fails, a reply is malformed, a
request times out, or the process exits after reservation but before transmission.
This deliberately wastes capacity rather than guessing that a provider did not
receive/charge a request. Recreating a client starts at ordinal 1 and cannot resume
a lost output. Existing incomplete claims remain incomplete and block strict
history evaluation. No ledger of actual provider responses/invoices is created.

Expiry is checked at reservation time, not an enforceable socket deadline after
commit. Already admitted I/O may finish later; authorized adapters still need their
own bounded I/O and stop behavior. The messages bound covers UTF-8 message bytes,
not provider protocol overhead; the fixed charge must separately cover that
additional overhead. Requested output caps are forwarded unchanged, never silently
truncated to make an otherwise unsupported configuration run.

`inspect_model_budget` returns one consistent bounded accounting snapshot with
reserved count/amount and remaining capacity. Actual provider call count and billed
amount stay `null`; provider-charge verification remains `false`. It exports no
transcript, source body, client, key or price-reference content. Identifiers and
hashes are still business metadata, not authorization to publish them. Preflight
failures without a permit are not a complete durable denial audit.

## Migration and evidence boundaries

New tail **20260915010000_research_model_budgets.sql** brings the catalog to 66.
The prior 65 SQL files/receipts remain unchanged. Budget policies and reservations
are append-only in the SAME project-private PostgreSQL. Existing restricted roles,
DSN/instance guards, grants and transaction helper are reused. No SECURITY DEFINER,
new credential, file journal, automatic migration or startup override.

The native test upgrades only a disposable source instance 65→66, preserves an old
record, and checks shared parallel capacity, identity/expiry, exact replay, no
refund after lost acknowledgement, raw SQL/mutation rejection, and a separate
process dying AFTER its first call reservation. Following restart, the interrupted
claim and reservation remain; only another pending task can spend remaining quota.
The prior 63→64 and 64→65 proofs retain their own historical schema targets.

Do NOT overlay an old immutable kit, edit its manifest or copy `.local` to acquire
this schema. No user instance is upgraded here. Safe installation/version switching
remains WP-06. No new long-term Release is implied. Exact commands/results/revisions
belong in the implementation PR and `DELIVERY_PLAN.md`; synthetic allowance tests
are not price validation, real model authorization or G2/G3 completion.

Primary implementation references checked 2026-09-15:
https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS
https://www.postgresql.org/docs/17/transaction-iso.html
https://docs.python.org/3.13/library/threading.html


## Prepared-request output compatibility before claiming (WP-02 / WP-03)

A prepared request whose fixed `limits.max_output_tokens` exceeds the selected
budget's `max_output_tokens` is refused before a **new** execution claim. The
existing agent requests that ceiling on its first model call. A mismatch raises
`research_budget_output_limit_incompatible`; it creates neither a claim nor a
call reservation and does not construct a client. Previously the task could be
claimed and captured as `model_failed` before this known mismatch was rejected.

The original request and budget are not clamped, edited or automatically replaced.
An explicitly reviewed compatible budget may later be supplied for the SAME still
unclaimed request, subject to all original freshness and execution checks. Batch
execution records its existing `operation_failed` metadata and keeps the request
unclaimed; it does not automatically retry. A compatible peer can still run.

Existing completion and incomplete receipts are looked up, revalidated and
returned unchanged even under an incompatible output allowance. This is not
permission to restart them. Already-blocked intake with no model work retains its
original capture behavior. Empty/expired budget errors keep their priority.

This static check is not a general request/provider compatibility certificate or
a claim that a whole research loop fits the available money. The original
per-call transaction still validates message bytes, output cap, expiry and shared
reservations before entering the supplied client. Later exhaustion, provider
errors, uncertain commits and no-refund behavior are unchanged. No real provider,
fee verification, credential access or automatic user-database operation is added.


## First-message byte compatibility before claiming (WP-02 / WP-03)

A prepared request whose actual first `messages_json` exceeds the selected
budget's `max_message_bytes` is now rejected with
`research_budget_initial_message_incompatible` before a new immutable claim.
The preflight reuses the agent's original eligible-catalog and initial-message
construction; it does not approximate message size from the stored request,
truncate rules or evidence, or change canonical inputs. The allowance measures
UTF-8 bytes, while the agent's existing context gate measures characters.
Equality at the byte ceiling remains permitted.

No-model paths (blocked intake, no eligible evidence or an initial context-limit
rejection) retain their original captured results. Empty/expired allowance and
the existing output-token incompatibility keep their earlier priority. An already
visible completed or incomplete execution is revalidated and returned unchanged;
an incomplete claim is not reclaimed. A history-read failure does not fall through
to creating a new claim. No automatic budget adjustment or retry is introduced.

An explicitly reviewed compatible budget may later be supplied for the SAME
unclaimed input. A compatible first message does not mean that later tool messages
fit: the original per-call transaction still checks each actual message, output
cap, shared balance and expiry. Later denial/failure capture and no-refund rules
are unchanged. This internal message-size check is not a provider tokenizer,
HTTP-envelope size calculation, tariff verification or proof that an entire loop
fits the monetary budget. D1-D3 and real-provider validation remain required.

Python JSON serialization returns text, not encoded bytes; encoding is explicit.
Reference checked 2026-09-17: https://docs.python.org/3.12/library/json.html
