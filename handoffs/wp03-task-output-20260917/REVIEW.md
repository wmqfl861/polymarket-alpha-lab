# Separate same-assistant review

The review is independent in phase and worktree, not another agent or third party.

The existing command prints its final envelope unchecked. Nineteen original-code
failures cover short writes, flush omission, escaping SystemExit(0)/interrupt/write
errors and single-envelope publication. This is not nineteen distinct bugs.
Reusing the current checked resolution emitter initially passed95related cases.
A separately designed six-case review found that returning130 from that shared
emitter did not request the supplied cooperative stop token. The final wrapper
requests that token on130, including blocked/read/run paths, after any admitted
work has completed cleanup. Ordinary output failures do not stop other invocations.
The original six assertions were retained, not weakened.

Additional review covers invalid write-count types, serialization before output,
no repeated business call, complete JSON preservation, and four actual fresh
interpreter failures using synthetic sessions. The shared emitter itself is
byte-identical to the baseline. Parser, inspection and run helpers are AST-identical.
All83original assertion nodes across the three edited test modules are retained;
there are128now. All67migration bytes, dependencies, workflow files and2921other
protected source/script/config payloads match baseline. Documentation is append-only.
The candidate was applied on a separate pristine BASE worktree with --check --index;
its written tree exactly matched the frozen candidate.

The existing native case is strengthened, not removed: a fresh empty turn has no
prior receipt, commits through real CLI/session/DB code, then sees ONLY an injected
short output. A completed short-write witness prevents a pre-write error from
masquerading as proof. Durable readback, exact original-ID replay, immutable receipt
and unchanged policy/reservation counts are required; no extra synthetic client
is allowed. This new native path has NOT run locally or in hosted CI this turn.

No remaining blocking source issue was identified in this scoped local review,
but publication, Windows/hosted gates and the new real-database scenario remain
unverified. Do not infer full stage acceptance or guarantee no defects. PR49's
unresolved first-invocation fault and the blocked CAPI2 experiment are separate;
this candidate does not change those tests, settings, logs, limits or PR status.
D1-D3, G3/G6, real source/fee/business acceptance remain open.

Tooling failures are retained separately: initial disposable venv could not locate
preinstalled setuptools, corrected only by its local tooling path; no project
requirements changed. GitHub exposes no writes in this tool set; direct container
git failed DNS. No token/credential discovery, actual provider, user DB, system
security change, or denied-publication workaround was performed. Source came from
the existing pinned archive, all6453blob hashes and exact upstream commit/tree
verified. Both final source and detached review worktrees were clean.
