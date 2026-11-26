# DSN parser hardening — 2026-09-12

## Authority and scope

For this development session the repository owner explicitly authorized the
implementing assistant to perform its own review, commit, and merge to main,
without the external-review or CodeGraph gates. This is a user-authorized
exception, not an external review PASS or a claim of CodeGraph synchronization.
Historical gate receipts remain historical; none are fabricated or rewritten.

Baseline: `c21502e598a3ec00dd146e048f217f6a3e383fea`.
Development branch: `dev/dsn-parser-hardening-20260912`.

This node changes only central DSN string validation, adds regression tests,
and introduces GitHub Actions offline verification. Phase 1 stays paper-only,
report-only, and readonly. There is no strategy-cycle behavior change, live
trading, account/auth/wallet/order path, database migration, new persistence
backend, or additional connection call. Durable project data remains local
Supabase/Postgres only. CI logs are engineering artifacts, not project data.

## Implementation and self-review

`validate_local_postgres_dsn` remains the sole existing policy entry point.
Its private keyword/value parser now follows libpq's single-quote and
backslash value syntax rather than shell tokenization. Double quotes remain
ordinary value characters. Duplicate keys remain visible and are rejected by
the existing local-only policy. The parser returns no partially accepted
result for malformed or ambiguous strings.

URI strings containing a raw `#` are rejected because `urlsplit` fragment
handling differs from libpq. Literal hashes must be encoded as `%23`. Raw NUL
and non-string runtime inputs fail closed in both public entry points.
Existing localhost, loopback IPv4/IPv6, explicit Unix sockets, redacted
messages, and hard report flags are preserved. Spacing around keyword equals
signs and legitimate libpq value escaping are covered positively.

The implementation is pure parsing: it opens no connection, reads no
credentials or environment, and introduces no dependency. PostgreSQL's
Keyword/Value Connection Strings specification is the syntax reference:
https://www.postgresql.org/docs/18/libpq-connect.html#LIBPQ-CONNSTRING

## Verification contract

The candidate's 54 original and 46 new module cases were re-run in this
session: **100 passed**. New cases are delivered separately as
`tests/test_supabase_local_dsn_parser_regressions.py`, leaving the original
54-case file unchanged. All fixtures are synthetic; these tests make no
network or database connections. Source blob `22ac63ee17ba910d21017dc6f691c8010e89d549`
is byte-identical to the locally tested candidate.

The archived CI configuration for this revision used Python 3.12 and the locked
dependencies. It ran `scripts/verify_local.py --full`, which checks the editable
install, CLI, compilation, sanitized pytest and tracked files. The 100 cases
above are the parser-specific result.

## Limits

No real Supabase/Postgres integration or psycopg end-to-end connection was
performed. This closes the reproduced string-parser discrepancies, not every
possible connection-boundary issue. Inherited PGHOSTADDR/PGSERVICE defaults,
DNS behavior, wrapper parameter merging and broader connection policy are not
claimed solved. Adjacent quoted/unquoted values and dangling escapes are
conservatively rejected rather than promising every unusual libpq form.

The earlier unmerged candidate package and 2026-09-11 handoff describe their
own historical verification state; this node's PR and Actions evidence are the
source of truth for its delivery status.
