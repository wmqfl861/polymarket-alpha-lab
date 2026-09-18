# Resolution evidence review and atomic outcome capture

## Scope

This is a conservative, operator-confirmed settlement intake, not an autonomous
oracle or a trading action. The existing public Gamma reader supplies a raw
market snapshot. The new pure gate treats its closed/status/prices fields only
as finality **hints**. A ready result additionally requires an explicit independent
operator attestation bound to the exact snapshot. The operator remains responsible
for verifying the market's resolution rules, finality, actual result, source and
resolution time. The program does not fetch, authenticate or semantically verify
the attestation source. Another hostname alone does not prove independence.

In particular, a closed market may not be finally resolved. A proposed/disputed
result is not final, and a 50/50 payout cannot be scored as a binary NO. Neither
`closedTime` nor the scheduled event end time is used as `resolved_at`.

## Review states

- `pending`: market not closed, or the supported UMA status is not `resolved`.
- `needs_confirmation`: canonical binary finality hints are present, but there
  is no independent attestation. A candidate direction is not a stored outcome.
- `blocked`: malformed/contradictory input, unsupported payouts, scope or time
  mismatch, stale/future snapshot, or disagreement with independent confirmation.
- `ready`: the supported hints agree with the explicit operator confirmation.
  This is **operator-confirmed**, not automatically chain-verified or error-free.

Only literal Yes/No outcomes are supported, with exactly one 1 and one 0 in the
price hint. The Yes/No order is matched by label, not assumed by array position.
Near-0/near-1 quotes, void/split payouts and unsupported outcomes cannot create
an evaluation outcome. Unknown stays unknown. Missing finality metadata blocks
promotion even for an event that may already be resolved in reality.

Scope uses a canonical lowercase `0x` + 64 hexadecimal condition ID and an exact
market slug. The snapshot cannot be future-dated or more than 600 seconds older
than the explicit check time. All comparisons normalize to UTC. A confirmation
must satisfy `resolved_at <= fetched_at <= confirmed_at <= checked_at`; receipt
in the database must occur within 600 seconds of the check. Exact retries return
the original receipt even after that window; changed payloads require a new ID.

## Read-only public probe

```powershell
.\.venv\Scripts\python.exe scripts/probe_research_resolution.py --condition-id YOUR_CANONICAL_CONDITION_ID --market-slug YOUR_MARKET_SLUG --allow-public-fetch
```

This makes one unauthenticated public request through the existing fixed-origin
Gamma reader and prints a candidate assessment and snapshot hash. It never opens
a database, calls a model, or performs independent confirmation. The public fetch
must be explicitly enabled. Production calls are not part of default tests.

For a fully synthetic, network-free example:

```powershell
.\.venv\Scripts\python.exe scripts/run_resolution_review_demo.py
```

## Project-private storage and application integration

The market must already have been registered prospectively by the research
capture/runner path. This code does not create markets or modify their forecast
cutoff. Apply the new manifest-locked migration on an existing managed instance
with the usual explicit `scripts/project_database.py migrate` procedure. Fresh
initialization applies all 63 migrations. The previous 62 SQL files are unchanged.

```python
from datetime import UTC, datetime
from pathlib import Path
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_resolution import (
    IndependentResolutionConfirmation, ResolutionSubmission,
)

# snapshot: a fresh GammaMarketSnapshot from the existing approved public reader.
# confirmed_yes/resolution_time/reference/text/reviewer: independently checked
# operator input, not fields invented by the model or taken from market quotes.
confirmation = IndependentResolutionConfirmation(
    condition_id=condition_id,
    market_slug=snapshot.market_slug,
    actual_yes=confirmed_yes,
    resolved_at=resolution_time,
    confirmed_at=datetime.now(UTC),
    gamma_content_sha256=snapshot.content_sha256,
    reviewer_id=reviewer_id,
    source_reference=approved_public_https_reference,
    source_text=approved_public_evidence_text,
    independently_verified=True,
)
submission = ResolutionSubmission(
    review_id=unique_review_id,
    condition_id=condition_id,
    snapshot=snapshot,
    checked_at=datetime.now(UTC),
    confirmation=confirmation,
)
with ProjectPostgres(Path(project_root)).session() as research:
    receipt = research.record_resolution(submission=submission)
    original = research.inspect_resolution(review_id=unique_review_id)
    diagnostics = research.evaluate().to_dict()
```

Omit confirmation to record an unconfirmed check. Pending/blocked/unconfirmed
reviews are retained, not converted to outcomes. Keep a single immutable
submission for retrying an uncertain commit; do not regenerate timestamps under
the same ID. The duplicate review returns its original evidence and timestamps.
A ready record cannot replace a pre-existing outcome, even with the same answer.

The evidence row includes exact raw Gamma bytes, collection/check times, the
operator's source text/reference/reviewer ID and content hashes. Ready evidence
and its outcome commit **in the same transaction**. The outcome references the
immutable review by URN and canonical payload digest. Database triggers enforce
this link and refuse a ready review without a matching outcome at commit. A
post-insert exception rolls back both. Existing legacy outcome APIs stay compatible
but do not retroactively establish reviewed evidence provenance.

New tables/functions live inside the existing private `research_capture` schema,
with the same restricted application role and no public API exposure. UPDATE,
DELETE and TRUNCATE are forbidden. Every managed call reuses the current instance
binding, audited local DSN validator and transaction/driver implementation; no new
connector, credential discovery, file journal or hosted database is introduced.

Readback verifies canonical payload bytes and hashes and recomputes the assessment.
Its explicit review-inspection API returns source text to the caller; it is not a
public diagnostic export. Normal evaluator output does not expose those raw texts.
Hashes bind content; they are not signatures, encryption or proof of source truth.
Supply public/redacted evidence only. No hidden model reasoning is stored.

## Worklist and candidate collection

The project-private worklist and explicit one-shot batch collector are documented
in [research-resolution-queue.md](research-resolution-queue.md). They retain
unconfirmed checks using this store; they cannot supply independent attestation
or create outcomes. Recurring scheduling and automatic finality remain absent.

## Limits and acceptance

This node does not implement automated oracle/chain finality, correction or dispute
handling for an already stored immutable outcome, recurring settlement polling,
fitted calibration, forecast publication or live execution. The source
reference is not followed, including redirects. A reviewer ID and boolean are
operator assertions, not independently authenticated user identities.

The optional native test uses a fresh private Windows PostgreSQL instance and
synthetic public snapshots/attestations. It checks actual migration, eight
concurrent identical submissions, atomic links, rollback, deferred constraints,
readback/restart retention and first-attempt scoring. It never uses the user's
installed database or real model/API credentials. Final measured results belong
in the PR, not inferred from default skipped integration tests.

Official field/semantics references (retrieved 2026-09-13):
- https://docs.polymarket.com/concepts/resolution
- https://docs.polymarket.com/market-data/market-details
- https://docs.polymarket.com/api-reference/markets/get-market-by-slug

## Preserve approved confirmation across adapter boundaries (WP-04)

The manual BTC/ETH confirmation command keeps the decoded approved instruction
private and passes a separately validated copy to its managed adapter. The
existing `copy_review` also copies the nested independent confirmation; no new
input format or serializer is introduced. Its receipt is still checked against
the original review, request, source evidence, time and outcome assertions, not
against an adapter's subsequently changed argument.

The confirmation service fixes the original canonical `ResolutionSubmission`
bytes BEFORE calling the existing atomic writer. An internally consistent receipt
for different content is rejected even when a faulty writer has also changed its
argument. Conversely, returning the correct original receipt remains valid when
only the adapter-owned argument was changed after the actual write or replay.
There is one writer call; neither mismatch nor output failure retries the business
operation, creates a replacement review or claims the transaction rolled back.
Original-ID inspection and explicit SAME-input replay remain the recovery path.

This is defensive receipt binding, not a sandbox for hostile Python, source
truth authentication or a report that an actual database was corrupted. Frozen
dataclasses emulate read-only values; they do not enforce an execution security
boundary. The original source/time checks, one-outcome policy, immutable ledger,
write opt-in, bounded stdin, output behavior and private PostgreSQL rules remain.

The existing isolated native confirmation test replays both original BTC/ETH
confirmations through the actual writer, then changes only adapter-owned arguments.
It must still return the original receipts and preserve original candidates,
forecasts, outcome counts and instance identity. This uses synthetic input on
CI-owned PostgreSQL, not a user installation or an authorized real settlement.

Reference checked 2026-09-18: Python dataclasses `replace` invokes the dataclass
constructor (including `__post_init__`); nested copying here is the existing
project type's explicit behavior, not a generic promise of `replace`.
https://docs.python.org/3.12/library/dataclasses.html#dataclasses.replace
