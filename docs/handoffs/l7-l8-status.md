# Handoff: L7/L8 engineering status since v0.1.0-linux-preview.1

Record date: 2026-10-07. Recording revision: main =
b05b9d6e181d20831a9358fcfb56772920d83c6e (interim status; see 3.8).
Companion narrative: DELIVERY_PLAN.md sections 63-66.

This handoff records the state on 2026-10-07. Its next steps and dispatch
rules are historical. Use [Project Agent Routing](../../AGENTS.md#project-agent-routing)
for current work. The handoff grants no activation or acceptance change.
Numbers come from DELIVERY_PLAN.md, history, GitHub Actions, or the
arbitration artifacts in section 7.

## 1. Objective and scope

Forward the complete engineering state since the published prerelease
v0.1.0-linux-preview.1 to the next coordinator or operator: what L7 and
L8 delivered, where each gate's evidence lives, what remains open by
ruling, and the exact recovery actions once the designated Linux host is
reachable again.

Out of scope and untouchable by the receiver: the L9 work in flight at
record time (research_linux_relay.py plus its tests, and a design
amendment), owned by concurrent workers. Nothing of L9 is merged at
b05b9d6e; see 3.7.

## 2. Pinned identities

| Item | Value |
|---|---|
| Repository | https://github.com/Lordakee/polymarket-alpha-lab (origin) |
| Branch | main |
| Current main | b05b9d6e181d20831a9358fcfb56772920d83c6e |
| Publication receipt | commit a1128a65 (section 62 amendment, E) |
| Release | ID 398244797, immutable prerelease, published and verified |
| Designated Linux host | ubuntu@166.1.232.93 (see 3.8) |
| CI workflow | "Offline verification" (GitHub Actions) |

Prerelease tag lineage, unwrapped values on their own lines:

- Tag: v0.1.0-linux-preview.1, annotated object
  d9fc6e0131da8499bed2eb4959ea3cd7d1b90b71
- Peels to release source commit (C):
  06e49d0fde20c837cda80f0b3e61fcb1f3806ca2
- The designated Linux host has been unreachable since 2026-10-07.

## 3. Baseline lineage

### 3.1 Prerelease baseline

v0.1.0-linux-preview.1 (six immutable assets verified against the
published SHA256SUMS; full pins in
docs/releases/v0.1.0-linux-preview.1/handoff.md) closed the L6
durable-publication and fixed-version engineering subconditions only.
G2-G6 remained open at publication; the six official probe scenarios
were then 0/6. Everything in 3.2-3.7 happened after that publication.

### 3.2 L7 official probe node (DELIVERY_PLAN.md section 63)

Acquisition and qualification:

- Official Claude Code artifact, version 2.1.278, image sha256
  5c4735937844e84f8a93306e841a5b0e12252909b07870f789b190468da147ab,
  234,119,480 bytes; vendor signature/key-fingerprint chain verified in
  three layers (l7-acquisition evidence). INPUT: 4,710 files, tkinter
  excluded.
- Outer-runner native qualification: 38 passed / 0 failed / 0 skipped.
- qualify-version PASSED: the exact banner "2.1.278 (Claude Code)"
  plus trailing newline on stdout (expected_banner is image version +
  " (Claude Code)" in scripts/claude_probe_environment.py), plus
  teardown and post-run integrity checks. Its earlier false abort is
  preserved as a first failure at
  ubuntu@166.1.232.93:~/pal-artifacts/l7-work/final/export/
  (run-record.json).

First official-six cohort (source under review a2e32a13): 6 executed /
0 passed / 6 failed, recorded RECORD-QUALIFICATION-FAILURE per the
l7-official-six-escalation arbitration (which contains an independent
review VERDICT: PASS). All six inner version checks passed; every
snapshot was complete with zero unsafe entries and zero sentinel files
but carried five changed entries, failing the zero-state-change gate;
each scenario showed one Messages POST plus one additional unexpected
request; success and invalid_action exited 0 but were rejected by the
strict decoder. Evidence: 166:~/pal-artifacts/l7-work/six/.

Diagnostic cohort (source b76dc7be, tree 60983a87; observation-only,
original criteria unchanged; recorded at dc09ec95), three findings:

(a) In all six message phases the transcript opened with "HEAD
    /api/hello", refused HTTP 404 as refused_unexpected_method, before
    the contract-matching "POST /v1/messages?beta=true"; no required
    authentication, telemetry, retry or external-service dependency was
    established, so the connectivity stop rule was not triggered.
(b) The success predecode capture: stdout 1,671 bytes, zero stderr,
    valid outer JSON, first failing validation stage outer_keys, a
    25-key shape containing nine previously unadmitted metadata keys,
    is_error=false, one turn, stop_reason=end_turn, synthetic usage
    counters input=3, output=5, cache creation=7, cache read=11
    (total 26), and a result string of length 106.
(c) Version-time state changes were zero, and each scenario still
    added the same five entries (config/.claude.json, config/backups/,
    one timestamped backup beneath it, config/sessions/,
    tmp/claude-1000/); all six therefore remained nonqualifying under
    the unchanged common conditions.

Evidence: 166:~/pal-artifacts/l7-work/diag/, with the retained envelope
record preserved locally at l7-final-ruling/
retained-success-predecode.json (section 7).

Owner decisions: L7-Q1 (the exact refused-HEAD-preflight plus one
Messages POST sequence) and L7-Q2 (the exact five-entry first-run
bootstrap class, baseline relocation excluded) were both APPROVED
2026-09-29, together with a standing approval delegation (the verbatim
owner wording is recorded in section 63). Prospective reentry criteria
were recorded 2026-09-30 at 7f4833ca: exact HEAD method, literal
unnormalized target /api/hello, framing, order and cardinality; exactly
one Host identifying the designated numeric-loopback server; exactly
one contract POST after the fixed 404; the five-entry bootstrap with
closed nine-field and two-field schemas, machineID/userID exactly 64
lowercase hexadecimal characters, firstStartVersion exactly 2.1.278,
migrationVersion integer (not boolean) 14, and matching
backup/config firstStartTime values.

Reentry: the predicate round d21e0adb (independent review VERDICT:
PASS) let the REENTRY cohort pass JUnit tests=6 failures=0 errors=0
skipped=0, but the outer runner rejected overall qualification on
export_unsafe_entry for pytest 9.1.1's convenience symlink
(pytest-tmp/test_supplied_claude_profile_lcurrent). The harness
correction 714efa6c, approved as handling (a) by the
l7-export-symlink-arbitration, creates six distinct exclusively
allocated scenario directories and drops that alias from the export
tree; the exporter and acceptance rule are unchanged. Local gates at
714efa6c: 234 passed / 17 skipped; adjacent decoder suite 326.

FINAL cohort at 714efa6c (tree
2d11618c9b467a571f84deb95115b43178569ea9, rebuilt and measured INPUT
manifest at 4,710 files, pinned pytest 9.1.1, the same pinned official
artifact and provenance, the approved section 63 criteria): six
executed, six passed, zero failures/errors/skips (JUnit time="28.227");
export of 52 files / 172,649 bytes at
166:~/pal-artifacts/l7-work/final7/export/ (junit.xml, run-record.json,
export-manifest.json, per-scenario evidence and diagnostic sidecars;
launcher sha256 prefix 0e9ba04e315c, 1,405 bytes; plan sha256 prefix
0208c44fdeb9515c); verified-empty cgroup teardown under the delegated
pal-l7-* subtree (memory.max 4 GiB, swap.max 0, pids.max 64, zero
events); outer run record accepted=true with no findings.

Disposition (section 63): ONLY G2's fixed-official-artifact
six-scenario synthetic-probe subcondition is closed. All earlier
cohorts remain preserved without rescoring. Real provider calls remain
false; activation_authorized=false.

First-failure preservation chain (section 63 execution-side record):
the colorama DEPS_LINUX block before its fix; three
runtime_closure_incomplete rounds (tkinter exclusion, split-dynstr ELF
parsing, usrmerge dual-guest binds); runner-qualification first round
with 10 failures (l7-runner-qualification.log); the qualify-version
false abort; the official-six first 6 failures.

### 3.3 Historical September 30 dispatch decision

The owner revised the dispatch settings in effect on 2026-09-30.
The original decision is recorded in `4f84f264d8d762c298f4623459aa24923aab4bc0`.
Follow [Project Agent Routing](../../AGENTS.md#project-agent-routing)
for current project work.

### 3.4 Section 65 post-L7 ruling (recorded at db6e2291)

Consult baseline 4f84f264; the amended L8 plan received an independent
VERDICT: PASS. L8 engineering was approved. Real activation
was withheld. The original §65 ruling recorded these prerequisites:
"accepted final engineering/native/review evidence, delivered
contained transport, official-image qualification through actual L5
wiring, a non-shared trusted runtime, reviewed explicit per-call
credential supply, and a Codex-approved pinned activation record with
scoped PG evidence writes." In October, §66 then changed the route for
arbitration. Follow [Project Agent Routing](../../AGENTS.md#project-agent-routing)
for current arbitration. The
shared production decoder stays authoritative; no second admission
decoder or relaxed envelope is introduced. The stale probe diagnostic
is deferred. The then-current cleanup return and survivor-name
assertion were named blockers; no surviving process had been
demonstrated. No G2-G6 closure or V1 completion follows.

### 3.5 L8 slices (all four review-closed and pushed)

- D = 35478f53 (design only, gated): the bounded fixed-origin
  validating relay (hash-pinned trust config, exact SNI/hostname
  checks, per-request method/literal-path/query validation, header
  allowlist, no CONNECT/SOCKS/tunnels/retries/redirects, 16,000,000
  and 32 MiB byte bounds, 10 s and 30 s time bounds as a sub-budget of
  timeout_ms); the offline-netns boundary (validating engine in the
  trusted parent, dumb inner peer on the reviewed [20000,32767] port
  window, credential still via the L5 one-use pipe); parent-only
  PostgreSQL access; the private per-call credential procedure; the
  activation-record template covering every section 65 prerequisite;
  and the precise 9-file subsequent implementation scope with disjoint
  ownership and scope-1-before-scope-3 sequencing (design text:
  docs/research-local-agent.md and docs/research-dispatch.md at this
  commit). Design gate VERDICT: PASS with one major accuracy finding
  and three minor/notes, all four applied. NOT implemented.
- A = 111420db: the contained-process model-phase result is stored as
  a candidate and returned only after cleanup completes with no
  failure (a cleanup error in finally no longer bypasses error
  handling), and the contained-profile version expectation becomes
  exactly cli_version + " (Claude Code)\n", matching the retained
  official probe fixture. Fourteen new tests. Local gates: 208
  passed / 5 skipped; adjacent decoder 326; process 58/3. Independent
  review VERDICT: PASS.
- B = 3acdf5cc (test only): the retained real official envelope (the
  1,671-byte capture from the L7 final ruling, length and sha256
  cross-checked against its own record) is driven through the actual
  profile factory and ClaudeProcessModel decode -- decoded action
  exactly the single search_evidence call, call IDs, synthetic
  26-token total -- and through the real two-phase supervisor relay
  core over real os.pipe pairs; a five-class rejection matrix rejects
  through the genuine production admission ladder with no repair or
  retry. Three Linux-only native tests (opt-in containment; compiled
  replay vendor with the corrected official banner) collect-and-skip
  off-host. Proves composition only, NOT official-image L5
  qualification. Independent review VERDICT: PASS.
- C = e88a0c13 (test only): replaces the never-matching global /proc
  command-name scan with positively observed, test-owned identities
  (/proc stat parsing with start-time PID-reuse guards, thread-unioned
  child discovery, owned pal-call-* cgroup allocations under the run's
  delegated root, structural supervisor binding, post-kill removal
  verification) and adds the useful fresh-parent recovery scenario
  (one interrupted request permanently incomplete with unknown usage;
  a later fresh-parent turn completes the other team's original
  request; full reconciliation). Windows host: 5 collected / 5
  skipped (native opt-ins). Independent review VERDICT: PASS.

Section 66 gate record (at b05b9d6e): focused surface 603 passed /
11 skipped (six files); CI "Offline verification" green on all four
slice commits (latest e88a0c13 in 8m38s). Substitute native evidence
obtained before the outage: worker C ran the file's five native tests
on the server -- 5 passed in 318 s, including both mandated proof
lines "native driver loss" and "native useful recovery", with one
first failure recorded and corrected -- on 111420db plus C's
then-uncommitted files, content-identical to e88a0c13 but not a formal
rerun on the final integrated tree.

### 3.6 Historical October 7 dispatch decision

The owner changed project assignments on 2026-10-07.
The original decision is recorded in `df137300c7b293a159588650488996da05f9d68d`.
Follow [Project Agent Routing](../../AGENTS.md#project-agent-routing)
for current project work.

### 3.7 L9 in flight (NOT in git at b05b9d6e)

An L9 plan (provider-relay implementation towards the L8-D design)
was gated FAIL by plan review with two blockers: (1) the wrapper
option surface has no --pass-fd (the production code already
classifies the "bwrap: Unknown option --pass-fd" stderr marker as
wrapper_option_unsupported in
src/polymarket_alpha_lab/research_process_linux.py), and (2) the
frozen-helper/inner-peer constraint of the L8-D design is
unsatisfiable as originally drafted. Remediation is in flight at
record time by concurrent workers: a design amendment and a
crossing-independent W1 core (research_linux_relay.py plus its
tests). None of it is merged. Do not touch their files, and do not
pre-empt their gates. L9's own gates are cited in its plan once that
plan is integrated into the repository.

### 3.8 The honest blocker: 166 host outage

ubuntu@166.1.232.93 has been fully unreachable since 2026-10-07: SSH
"Connection closed" and ICMP 100% loss -- a host-level outage, not a
daemon or project failure, confirmed over two independent paths
(local and review subagent). Consequences, recorded honestly at
b05b9d6e:

- L8's native verification on the final integrated tree and
  verify_local --full are PENDING, not failed and not passed.
- L9's native gates are likewise pending.
- L8 is NOT declared closed and must not be declared closed until the
  section 65 completion evidence exists (section 6).

## 4. Gate status: verified vs pending

Verified, with evidence locations:

- CI "Offline verification" green, per commit (run IDs from the GitHub
  Actions record): 35478f53 run 36692994306; 111420db run 36693807938;
  3acdf5cc run 37611097266; e88a0c13 run 37613529802 (8m38s per
  section 66); df137300 run 37608658675; b05b9d6e run 37618942219.
  Earlier chain, all success: dc09ec95 36515666273; 51ce41f8
  36518630690; 2ddd63ed 36591802659; 7f4833ca 36599102005; d21e0adb
  36600995766; 7157fb48 36606509295; 4f84f264 36683410972; db6e2291
  36689837613. One exception, recorded honestly: 714efa6c's run
  36605973104 is CANCELLED; section 63's acceptance of that round
  rests on the local gates (234/17 plus decoder 326) and the
  arbitration-approved prescription, and the following commit
  7157fb48 is CI-green.
- Local focused counts: 3.2 and 3.5 (714efa6c: 234 passed / 17
  skipped, decoder 326; L8-A: 208/5, decoder 326, process 58/3;
  integrated L8 tree per section 66: 603 passed / 11 skipped over six
  files).
- Native substitute evidence (pre-outage, content-equal to e88a0c13,
  not the final tree): C's 5 passed in 318 s on the designated host
  (section 66).
- Review verdicts: amended L8 plan PASS (section 65); each slice A/B/
  C/D PASS (commit records plus section 66); L7 predicate round PASS
  (d21e0adb); export-symlink handling (a) via
  l7-export-symlink-arbitration; final L7 disposition via
  l7-final-ruling with an independent PASS; first-cohort ruling via
  l7-official-six-escalation with an embedded independent PASS.
- Server evidence (unreachable until recovery; paths per section 63):
  166:~/pal-artifacts/l7-work/ with l7-acquisition/, six/, qv2/,
  final/export/, diag/ and final7/export/; plus
  l7-runner-qualification.log and the first-failure chain listed in
  3.2. Release-era roots remain at 166:~/pal-artifacts/ (verify-full-
  C-06e49d0f.log, release-v0.1.0-linux-preview.1/ and the retained
  attempt-1 timing directory).

Pending, all blocked on 166 recovery:

- The five-file focused/native pytest surface and
  scripts/verify_local.py --full on the final integrated tree
  (b05b9d6e), exactly per the env block cited in section 6.
- B's three Linux-only opt-in native tests on the final tree.
- C's five native tests as a formal final-tree rerun.
- The corrected-banner contained round trip (the L8-A expectation
  "2.1.278 (Claude Code)\n" under a contained official round trip).
- L9 native gates per the amended L9 plan, once integrated.
- Remote rereading/verification of the retained server artifacts.

## 5. What remains open per the rulings

- G2 remains PARTIAL/open. The fixed-official-artifact six-scenario
  synthetic-probe subcondition is closed (section 63); the authorized
  real-research half remains open: at least one reviewed real
  execution for each of crypto_btc and crypto_eth with the required
  authorization, identity, usage and failure-preservation evidence,
  plus every section 65 activation prerequisite listed in 3.4.
  activation_authorized=false; real provider calls remain false.
- G3, G4 and G5 remain open (L8-C advances G3 readiness only).
- G6 remains PARTIAL/open.
- V1 remains incomplete; no statistical strategy validation and no
  live-execution authorization follows.
- The L8-D relay is designed but NOT implemented; its gated 9-file
  scope with disjoint ownership and scope-1-before-scope-3 sequencing
  requires the passed design gate plus the section 65 sequencing
  before any transport implementation begins.
- Do not flip activation_authorized when CI turns green; that
  instruction is explicit in the l8-next-node-consult verdict.

## 6. Operator-facing next steps recorded on 2026-10-07

1. Check the 166 host at the provider. It is a host-level outage (SSH
   refused plus ICMP 100% loss since 2026-10-07). Do not treat it as a
   daemon or project failure; restore host reachability only, and do
   not attempt credential, data or database recovery beyond that.
2. On recovery, confirm SSH and host identity, then run exactly the
   pending native/full verification specified in the section 65 / L8
   verdict env block. The verbatim block is the "After
   implementation..." code block in
   D:/Projects/.agent-artifacts/polymarket-alpha-lab/
   l8-next-node-consult/codex-verdict.md. Its binding requirements:
   the locked Python 3.12.14 venv at /home/ubuntu/polymarket-alpha-lab;
   the measured PG18 prefix and delegated cgroup root exported as
   POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX and
   POLYMARKET_ALPHA_LAB_LINUX_CGROUP_ROOT (unset or invalid values
   must stop execution); PYTEST_DISABLE_PLUGIN_AUTOLOAD=1;
   PYTHONUTF8=1; POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1;
   POLYMARKET_ALPHA_LAB_RUN_LINUX_CONTAINMENT=1; then the five-file
   focused/native pytest selection (tests/test_research_process_
   linux.py, tests/test_research_claude_profile.py, tests/test_
   research_claude_linux.py, tests/test_research_claude_contained_
   reply.py, tests/test_project_postgres_uncapped_audit_native.py),
   then scripts/verify_local.py --full, then git diff --check. Full
   verification strips native opt-ins and cannot replace the explicit
   native run. Do not duplicate the full script here, and do not
   improvise variants; the artifact block governs.
3. Record the outcomes (exact counts, first failures, skips, the two
   mandated proof lines) in DELIVERY_PLAN.md per the section 65
   completion rule; only that evidence closes L8. Preserve first
   failures; no rerun-to-green without recording.
4. L9: wait for the design amendment and the W1 core to land and pass
   their own gates, including resolution of the two plan-gate
   blockers in 3.7. Do not pre-empt or duplicate their scope.

Allowed: read-only inspection; the recovery verification above;
recording results in DELIVERY_PLAN.md. Forbidden: declaring L8, L9 or
any gate closed without the cited evidence; activation, provider
calls or credential discovery; rescoring historical cohorts; touching
the concurrent workers' L9 files; live trading, account or private-
key handling (the Phase 1 paper-only/report-only/readonly boundary).

## 7. Pinned references

Commit chain since the prerelease source 06e49d0f (oldest first; one
line each; full messages in git):

| Commit | One-line description |
|---|---|
| a8c00d1f | section 62 record: L1-L6 evidence, prerelease pending |
| c36a76c1 | final prerelease handoff and section 62 consistency fix |
| a1128a65 | publication receipt for v0.1.0-linux-preview.1 (E) |
| 7bd2a989 | signal death vs ordinary nonzero exit; probe observation v2 |
| 41dfba9f | real Linux INPUT builder and namespace-plan v2 (L7) |
| ff48cbbc | narrowly scoped outer runner for the official probe (L7) |
| 7fab2425 | deterministic export traversal and host-interp asserts |
| e29f07b9 | Linux dependency tuple excludes the win32-marker colorama |
| e07e959b | payload runtime closure complete (tkinter, ELF, usrmerge) |
| a36563ea | outer-runner qualification fixes on the real host |
| a2e32a13 | stop the false driver_lost on an already-EOF FIFO stdin |
| af99841a | section 63 first-attempt qualification-failure record |
| b76dc7be | observation-only diagnostic instrumentation (L7) |
| dc09ec95 | diagnostic cohort record and two pending owner decisions |
| 51ce41f8 | L7 decoder calibration: second closed terminal representation |
| 2ddd63ed | L7-Q1/Q2 approved plus the standing Codex delegation |
| 7f4833ca | prospective reentry criteria recorded under the delegation |
| d21e0adb | predicates for the two approved exceptions (review PASS) |
| 714efa6c | harness correction: exclusive scenario dirs, symlink drop |
| 7157fb48 | section 63 reentry PASSED record (subcondition closed) |
| 4f84f264 | §64 original dispatch decision, 2026-09-30 |
| db6e2291 | sections 64-65 record: L8 approved, activation withheld |
| 35478f53 | L8-D relay and credential-ingress design (design-only) |
| 111420db | L8-A cleanup suppression and exact official banner |
| df137300 | §66 original dispatch decision, 2026-10-07 |
| 3acdf5cc | L8-B contained terminal-reply composition proof |
| e88a0c13 | L8-C identity cleanup evidence and useful recovery |
| b05b9d6e | L8 integration-gate status; honest 166-outage record |

Arbitration artifacts (local, outside the repository):

- D:/Projects/.agent-artifacts/polymarket-alpha-lab/l7-final-ruling/
  -- codex-verdict.md plus retained-success-predecode.json (the
  retained success-envelope record referenced by L8-B).
- .../l7-predicate-arbitration/ -- codex-ruling.md (the Q1/Q2
  boundary arbitration behind d21e0adb).
- .../l7-export-symlink-arbitration/ -- codex-ruling.md (handling
  (a), the 714efa6c prescription).
- .../l8-next-node-consult/ -- codex-verdict.md (the L8 slice
  definitions and the env block cited in section 6).
- .../l7-official-six-escalation/ -- codex-verdict.md (the first
  cohort RECORD-QUALIFICATION-FAILURE arbitration).

Style templates for future handoffs of this kind:
docs/handoffs/pr17-network-check-full.md and
docs/releases/v0.1.0-linux-preview.1/handoff.md.

## 8. Report-back format

Report back separating what was measured in the new round from cited
existing records, preserving first failures:

1. Host recovery facts: when reachability returned, from where, and
   the first successful probe output.
2. The native run: exact counts and duration, both mandated proof
   lines, and any first failure preserved separately.
3. verify_local --full result and git diff --check status on the
   final tree.
4. The corrected-banner contained round trip outcome.
5. Any deviation, skipped or unexecuted step, with its reason; L9
   gate outcomes once their owners land them.
6. The DELIVERY_PLAN.md update commit that records the evidence.
