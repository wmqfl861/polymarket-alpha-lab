# Handoff: L8-pending/L9 engineering status since the L7/L8 handoff

Record date: 2026-10-09. Recording revision: main =
dd668cd1b7977052193539a0be1d40db55fdb94a (pushed to origin), PLUS the
uncommitted in-flight L9.1 working-tree batch documented in section 6 as
pending. Companion narrative: DELIVERY_PLAN.md sections 65-68. Predecessor
volume: docs/handoffs/l7-l8-status.md. Scope: forward the full L9 arc
state to the next coordinator or operator. This is a status handoff; it
authorizes nothing: no activation, no real provider calls, no
acceptance-criteria change, no claim that T8, L8-native closure, the L9
node or any gate is closed. Every number comes from DELIVERY_PLAN.md, git
history, GitHub Actions records (read back 2026-10-09 via gh), the
artifacts named in section 8, or the working-tree diff. Facts relayed by
the coordinator without a local artifact are labeled as such.

## 1. Executive status

Closed with evidence since the l7-l8-status.md record:

- The L9 offline half is landed, independently reviewed and on origin:
  amendment gate (2c610449), T8 gate file (27d78ca7, W4), relay core
  (e337ccea, W1), profile v3 + typed trust (db44df7c, W5), helper
  gates (91168a31, W6/W6b), relay helper + two-value egress (ad07d3be,
  W2), parent-side wiring (11e53cc2, W3), native first-run fix
  (dd668cd1). Verdict details in sections 3 and 8.
- The relay fd-crossing property is demonstrated twice: non-binding CI
  preview 12/12 (run 37719238674) and the BINDING server T1 12/12 on
  ubuntu@SERVER_HOST_REDACTED (69e35331, zero C4 divergence). The section-67
  no-fallback worst case is retired for the pre-L9.1 digests.
- The SERVER_HOST_REDACTED_RETIRED outage ended by owner-ordered server substitution
  (166 retired; 154 provisioned with the preview-proven recipe); the
  W3 push hold (arbitration C2) lifted.

Pending, and why:

- The binding T1 pass predates the in-flight L9.1 batch, which changed
  the frozen helper bytes; every digest embedding them moved. UPDATE
  (post-authoring): the re-run at the L9.1 digests has since PASSED on
  154 (12/12 incl. the three native tests, exercising the harness CA
  adaptation in section 5.4b); it is pending only its DELIVERY_PLAN
  record at the landed tip.
- CI "Offline verification" is RED at dd668cd1 (run 37936621543, one
  failure: the Linux-only relay channel-builder test tripped the
  suite's no-network tripwire). The in-flight batch contains the fix.
- W3's two native tests have since run GREEN on 154 at the L9.1 batch
  (121 passed / 1 skipped for the full native process file; the two
  wrapper/refusal flake nets 40/40). T8, L8 native closure and
  verify_local --full remain unexecuted on the landed tree. This
  document executes nothing.
- One ruling gap for T8: section 65 requires a non-shared trusted
  runtime, 154 is shared by owner instruction, and the W13 runbook
  says STOP on a shared host for the T8 ISOLATED_HOST step (sections
  6-7). Owner decision D-1 pending.

## 2. Pinned identities

| Item | Value |
|---|---|
| Repository / branch | https://github.com/Lordakee/polymarket-alpha-lab, main |
| Current main (origin = local) | dd668cd1b7977052193539a0be1d40db55fdb94a |
| Working tree at record time | 5 modified files, uncommitted (+397/-27): the L9.1 batch (section 6) |
| CI workflow (binding, per push) | "Offline verification" |
| CI workflow (manual, non-binding) | "T1 relay-crossing fd-survival PREVIEW" (7834a203) |
| Designated Linux host | ubuntu@SERVER_HOST_REDACTED (Ubuntu 24.04.1, kernel 6.8, 16 cores, 31 GiB; SHARED machine, touch only ~/polymarket-alpha-lab) |
| Retired host | SERVER_HOST_REDACTED_RETIRED (owner order 2026-10-09 after a 30+ h outage; 166-only artifacts lost with it, recorded at 69e35331) |
| bubblewrap pin | 0.11.1, official tarball sha256 c1b7455a1283b1295879a46d5f001dfd088c0bb0f238abb5e128b3583a246f71 |
| Official CLI image (T8) | 2.1.278, sha256 5c4735937844e84f8a93306e841a5b0e12252909b07870f789b190468da147ab, 234,119,480 bytes |

## 3. The L9 design arc

### 3.1 Design amendment 1 (2c610449; DELIVERY_PLAN.md 67:2836-2874)

The section-66 native-subagent amendment resolved both L9 plan-gate
blockers. BLOCKER 1 (crossing): --pass-fd does not exist in bubblewrap
0.11.1 (the gate review independently re-fetched v0.11.1 bubblewrap.c:
fd closing runs only in the monitor_child/pid-1-reaper branches; the
payload exec branch closes only proc_fd and, unless --as-pid-1,
opt_sync_fd, so inherited descriptors reach the payload). The amendment
makes an INHERITED AF_UNIX socketpair descriptor the mechanism: parent
creates it, the existing _stage keep-set/set_inheritable/execv path
carries it into bwrap, no bwrap option names it. That survival property
is undocumented, so the T1 native probe (positive byte round trip +
CLOEXEC negative control + teardown verification) became W2's hard
merge precondition; all four fallbacks were evaluated and rejected (no
viable fallback; probe failure would be a node-level blocker). BLOCKER
2 (second helper): the checked-in RELAY_HELPER_SOURCE, strictly
additive over the frozen HELPER_SOURCE (CONF relay_channel_fd/
relay_port model-phase-only; stage keep-set; exactly two PAL_RELAY_*
setenv entries; guest dummy pump: single-draw extraction port, exit 92
without redraw, exactly one connection, then the frozen guest duties
and vendor execve); two-value closed egress branch (offline vs
relay-fixed-origin, cross-pins refuse); offline CONF/argv/policy_dict/
v1/v2 digest bytes unchanged. Gate VERDICT: PASS (0 blocker/major, 3
MINOR with binding W2/W3 prescriptions, 5 NOTEs; artifact
l9-amendment/README.md). The 9-file scope and activation boundaries
are unchanged.

### 3.2 The offline half lands (native subagents per section 66)

- W4 = 27d78ca7: opt-in T8 gate file
  tests/test_research_claude_relay_native.py (764 lines): the
  CLAUDE_RELAY_QUALIFICATION=1 opt-in plus probe, containment,
  PG-prefix and cgroup prerequisites (skip without the opt-in; every
  gap after it FAILS naming the variable); in-test self-signed CA
  with a zero-ambient-trust negative; fake Messages TLS round trip;
  exactly two closed outcomes (success with derived relay evidence,
  or the closed preflight blocker); dummy key, fake loopback service,
  NOT activation. Review VERDICT: PASS (3 findings applied).
- W1 = e337ccea: relay core (trust records + validating ENGINE_SOURCE;
  69 test functions; 151 passed / 1 skipped), carrying the W5 review
  prescription (sorted forwarded-header default proven cross-seed by a
  PYTHONHASHSEED subprocess byte-stability test). R-W1 VERDICT: PASS;
  its 3 MINORs + NOTE prescriptions (six items, incl. chunked-release
  cap and real-OSError teardown-suppression test) went to W2.
- W5 = db44df7c: profile schema v3 + typed-trust admission (symbolic
  endpoint http://127.0.0.1:0, port 0 = per-call draw, never a digest
  input; _relay_v3_digest over the serialized launch policy; closed
  two-value _ADMITTED_EGRESS, typed RelayTrustConfig required;
  offline v1/v2 bytes and goldens unchanged; four items left W2-gated
  as 3 skips + 1 xfail). Review VERDICT: PASS.
- W6/W6b = 91168a31: the gate file (five frozen offline byte pins;
  strict-additivity subsequence with closed scaffolding vocabulary;
  cross-pin refusals; exactly-two-setenv binding; three-story CLOEXEC
  control; fail-on-divergent-landing; stdlib AST gate;
  allocation-scoped survivor scan). R-W6 FAIL (contradictory setenv
  index arithmetic, over-binding marker vocabulary, timing-dependent
  CLOEXEC control; six findings); W6b repaired all six; R-W6b VERDICT:
  PASS with execution verification (hostile inputs, real-source
  surgical variants, 13-outcome cases).
- W2 = ad07d3be: RELAY_HELPER_SOURCE landed (exactly 74 inserted
  lines, zero delete/replace; only new import socket; pump validates
  the channel fd pre-fork, forks before the credential read, one
  bind/listen/accept, exit-92 no redraw), helper digest
  262031105a1809d087ae2d437583ab41647404002e78afc13fb8b022941dede9;
  all four cross-pins and neighbor refusals; offline bytes frozen;
  W5's four gated items green with CLAUDE_V3_GOLDEN re-pinned (then
  linux a093f08f... / win32 563a5fde...). Gates: six-file 441/12;
  full sanitized suite 40747/80/0 in 49m51s. R-W2 VERDICT: PASS,
  zero blocker/major/minor.
- Deviation (c675308a): section 68 first said W2 was local-only
  pending T1; minutes later the stacked docs commit 50e658bb carried
  it to origin with the 91168a31..50e658bb push (coordinator ordering
  slip, not a content problem). No force rewrite of shared main;
  substantive gates unchanged; T1-failure rollback is git revert.

### 3.3 T1 preview campaign (W9-W11 + diagnosis commits; 68:2923-2945)

While 166 was down, a manual workflow_dispatch preview on ubuntu-24.04
(7834a203; R-W9 FAIL on one blocker - -Ddocs=false is not a 0.11.1
meson option - fixed per its prescription to -Dman=disabled) ran the T1
gate file against a source-built bubblewrap 0.11.1 from the pinned
tarball. Nine dispatches, runs 37713430462 (2026-10-08T01:32Z) through
37719238674 (02:43Z), eight failures then the final success, burned
down four environment defects that would all have hit the server
native run; each fix landed with its own independent review:

| # | Defect | Root cause | Fix (commit, review) |
|---|---|---|---|
| 1 | Interpreter provenance | uv's standalone CPython private RPATH ($ORIGIN/../lib) breaks after closure rebinding to /pal/runtime; guest died loading libz.so.1 | Venv from system CPython /usr/bin/python3.12 (e0cd874f) |
| 2 | Closure binds maps-versioned paths | discover_runtime_closure bound system libs at /proc/self/maps version-suffixed paths (libz.so.1.3) while the loader searches default dirs by DT_NEEDED soname | W10 fe13da54: LD_TRACE_LOADED_OBJECTS on the interpreter; bind each traced dep additionally at its soname path (R-W10 PASS, zero findings) |
| 3 | Closure probe import set | The probe's fixed import list lacked socket (RELAY_HELPER_SOURCE imports it; pure-Python stdlib is invisible to /proc/self/maps until imported) | W11 ef7e85db: probe imports = union of both helper sources' stdlib imports + AST coupling test failing on future uncovered imports (R-W11 PASS) |
| 4 | cgroup v2 common-ancestor rule | A cgroup.procs writer needs write access to the common ancestor's cgroup.procs; runner step shells sit in systemd slices whose common ancestor with the delegated subtree is the root-owned global root, so every migration write failed EACCES | 054d8163: each step shell migrates once into a delegated-subtree child via a root-assisted write (diagnosis ladder b039f8ef run-5, 8bebd480 run-6, af131cd0 run-7) |

Anchors: run 2 = 37713859290; run 3 = 37714242116; run 4 = 37716295041;
run 5 = 37717937412 (10 passed / 2 failed - the CLOEXEC negative control
PASSED for the first time while the supervisor-driven tests died at
report EOF); runs 6-8 = 37718285870 / 37718575994 / 37718906985. Final
37719238674: 12/12, conclusion success - fd survival across the bwrap
0.11.1 payload exec, the CLOEXEC control and teardown verification,
through the same hash-pinned wrapper mechanism as production.
Production insight for the server runs: the trusted parent must already
live inside the delegated cgroup subtree.

### 3.4 W3 early-start arbitration (a9b47362; 68:2947-2970)

With the server still down, a native read-only arbitration subagent
ruled RULING: authorize-early-start-with-conditions (the crossing
assumption was now doubly evidenced; W3 is additive parent-side wiring
mostly independent of the crossing; Iron Rule 6 opposes idling
capacity; safety boundaries untouched). Conditions (violation voids):
C1 offline implementation only, per the R-W7-approved W3 plan (artifact
l9-w3-plan/README.md; binding section-13 addendum at line 621, whose
two resolutions - the S2 fallback success contract and the
no-second-channel rule - are summarized in 3.6); write scope exactly
research_process_linux.py + tests/test_research_process_linux.py.
C2 the binding T1 stays a SERVER run, moved from W3's start gate to
its merge/push gate; node closure, T8, L8-native, verify_local --full
and activation gates unchanged. C3 server-first restores the original
order and voids this authorization. C4 any server-T1 failure or
divergence from the preview assumptions (wrapper digest/version/
source, kernel, interpreter provenance) freezes the W3 path for fresh
re-review + re-arbitration; rollback by git revert. C5 W3 must state
it started under this arbitration with binding T1 pending; never
claim T1/T8/node completion; preserve first failures. C6 safety
boundaries restated; C7 the record rides with the W3 dispatch. The W3
plan's own gate: R-W7 VERDICT: PASS (conditional on transmitting
section 13).

### 3.5 Server substitution and binding T1 (69e35331; 68:2972-2991)

The owner retired SERVER_HOST_REDACTED_RETIRED (verbatim instruction recorded in
section 68) after the 30+ hour outage and designated
ubuntu@SERVER_HOST_REDACTED (shared machine; one-time password used only to
install the coordinator public key, never stored). 166-only losses are
recorded honestly (notably the L7 final7 reentry export tree, 52
files, under ~/pal-artifacts); ledger records, commit history and
local log captures survive. 154 was provisioned with the
preview-proven recipe - exact measured facts in the section-7 table:
repo hard-reset to origin/main a9b47362 (excluding then-unpushed W3),
venv from system CPython 3.12.3 (recorded deviation from the release
tuple's 3.12.14; system interpreters are closure-proven by the
preview, W10), smoke 9 passed / 3 skipped, verified-tarball bwrap
0.11.1 in ~/pal-runtime, recorded userns sysctl lift, delegated
/sys/fs/cgroup/pal subtree, shell migration for the v2
common-ancestor rule. BINDING T1 ON 154: 12/12 passed including all
three native tests, C4 divergence check against the preview
assumptions (tarball digest, version string, mechanism, kernel
generation, interpreter provenance) = zero divergence. C2 was
satisfied; the W3 push hold lifted (11e53cc2 and the stacking docs
commit pushed together). Remaining, all on 154 per 68:2989-2991: W3
two native tests + T8, L8 native closure + verify_local --full, node
final review. (The trailing open-items block at 68:2993-2997 still
carries pre-substitution 166-outage wording naming W2-push and
W3-dispatch as pending; superseded by the substitution paragraph;
reconcile in the next docs commit.)

### 3.6 W3 lands; native first-run finding (11e53cc2, dd668cd1)

W3 (11e53cc2) implemented the parent-side wiring per C1. The two
binding section-13 resolutions it implements: (1) the S2 fallback
success contract - success = clean model_done + last call_done ==
relay_ok + clean teardown, a parent-initiated post-success engine exit
being the expected clean exit; (2) the no-second-channel rule - only
the symbolic endpoint is substituted, the drawn port and the fd>=100
channel are kept, no second channel/draw/credential key. Delivered:
fail-closed symbolic-endpoint substitution, one retained port draw,
both channel ends >= 100, the sealed engine peer started before MODEL
and before any credential release (CONF proven against the real W1
main() validator), the bounded-conclusion success contract,
engine-first teardown (STOP -> liveness EOF -> bounded kill; L8-A
suppression via teardown_failure), closed outcome mapping, the
LINUX_RELAY_EVIDENCE single-slot accessor, one additive run-path
dispatch; offline byte identity green (W6 five pins + v1/v2/v3
goldens + offline CONF/argv pins). Gates: six-file 476/16;
relay-native 10/1; clean sequential full suite 40782/84/0 in 58m02s
(a contended run's single out-of-scope failure was verified as load
contention by an isolated rerun and is preserved). R-W3 VERDICT: PASS
- zero blocker/major, one conservative MINOR (bounded ~3x cleanup
budget), 4 fail-closed NOTEs, all seven seams CONFORM. The binding T1
pass then unlocked W3's two native tests for their first real
execution: the driver-loss runner failed before RUNNING with a
TypeError (its subprocess passed sys.argv[1] as str into
build_native_relay_probe's directory / 'vendor'; in-process offline
callers pass pytest's Path, so every offline suite was green).
Reproduced and diagnosed on the server, fixed by a one-line coercion
(dd668cd1); the native rerun was still pending there - and the
rerun/loop is what surfaced the two scheduling races now being fixed
in-flight (section 5).

## 4. Native-gate status board

| Gate | State | Evidence | What remains |
|---|---|---|---|
| T1 fd-crossing, binding | PASSED on 154 at pre-L9.1 digests; RE-RUN PASSED at the L9.1 digests (post-authoring update) | 69e35331 + 68:2981-2989 (12/12, zero divergence); non-binding preview 37719238674 12/12; L9.1 re-run on 154 at the patched dd668cd1 tree: 12/12 incl. the three native tests, exercising the 5.4b CA adaptation | DELIVERY_PLAN record at the landed tip |
| W3 native two tests | PASSED on 154 at the L9.1 batch (post-authoring update): full native process file 121 passed / 1 skipped; wrapper-refusal flake net 40/40 | dd668cd1 first failure (str/Path TypeError) preserved and fixed (dd668cd1 itself); L9.1 server runs | DELIVERY_PLAN record at the landed tip |
| T8 qualification | NOT EXECUTED; environment ready (image + PG prefix on 154, W15-relayed) | Gate file 27d78ca7; image digest cross-checks DELIVERY_PLAN:2719-2720 | Execute W13 Step 3.2 after W3 native - the ISOLATED_HOST shared-host ruling (section 6) first; the W4 gate accepts the closed preflight blocker as an honest outcome |
| L8-B 3 opt-ins | NOT EXECUTED on the final tree; fixture on 154 byte-equal (W15-relayed) | Predecessor 3.5; local fixture 3,506 bytes, sha256 433ff7b2184e87c1aed4eceddd0e2e30d2e4a45f00e318674e224ba9b3db6a02 | Verify the 154 fixture digest, then the section-65 five-file block |
| L8-C 5 native + corrected-banner round trip | NOT EXECUTED on the final tree | Predecessor 3.5 / section-66 substitute evidence (5 passed in 318 s pre-integration) only | The section-65 five-file block on the final tree; both mandated proof lines |
| verify_local --full | NOT EXECUTED on the final tree on the server tuple | Prior: 40015/80/0 in 1901.52 s at C (section-61 era); W3-era suite 40782/84/0 locally | W13 Step 4; budget 35-60 min; it strips all POLYMARKET_ALPHA_LAB_* opt-ins and never substitutes for native runs |
| CI "Offline verification" per push | GREEN through a9b47362; 69e35331 cancelled (superseded); RED at dd668cd1 | gh 2026-10-09: run 37936621543 - 1 failed (test_relay_channel_builder_moves_both_ends_above_100, "Failed: network entered"), 40753 passed, 112 skipped, 519.74 s | The in-flight tripwire lift (5.3) + L9.1 landing must restore green |

Verified CI run ids (gh, 2026-10-09): 27d78ca7 = 37652669577,
db44df7c = 37662783312, 91168a31 = 37672438553, c675308a = 37678930875,
caa55cad = 37714240406, fe13da54 = 37716293083, 73d02893 = 37719538718,
a9b47362 = 37751820829 - all success. Cancelled in push bursts
(superseded by the next push; each burst's terminal commit is green):
50e658bb = 37678829352, 7834a203 = 37713415259, e0cd874f = 37713857365,
ef7e85db = 37717935131, b039f8ef = 37718283463, 8bebd480 = 37718573814,
af131cd0 = 37718905233, 054d8163 = 37719235034, 69e35331 = 37936136894.
dd668cd1 = 37936621543 FAILURE as above. e337ccea (W1) has no
separately read-back run id; section 68 records CI green through
91168a31, which includes it.

## 5. In-flight: the L9.1 reap/drain/tripwire batch (NOT landed)

At record time the working tree carries one uncommitted batch (grew to
+410/-28 over dd668cd1 with the 5.4b CA adaptation, which postdates
this section's original authoring) across five files:
research_linux_relay.py and research_process_linux.py (src),
test_research_claude_linux.py, test_research_linux_relay_helper_gates.py
and test_research_process_linux.py (tests). No review verdict yet; not
committed; nothing here claims it is done. All parts verified by
reading the diff.

### 5.1 Helper-side reap race (the W14 frozen-helper fix)

Root cause: _try_reap (research_process_linux.py:394-399) returns None
when os.waitpid raises ChildProcessError (ECHILD - the pid was already
reaped). Both frozen helper sources' phase loops (_run_version and
_run_model, mirrored in HELPER_SOURCE and RELAY_HELPER_SOURCE)
reassigned `status = _try_reap(staged['pid'])` unconditionally on every
select iteration. Race: the child exits non-zero (e.g. a
wrapper-option refusal, exit 3) and iteration N reaps it into a real
wait status; an inherited dup2'd stderr write end held past the child's
death keeps the pipe open, so the loop iterates again; iteration N+1
re-waitpids the reaped pid, gets ECHILD -> None, and overwrites the
finalized status; the `status is not None` guard then fails, exit_code
stays None, and the _classify_stderr mapping (wrapper_option_
unsupported) is silently skipped. It surfaced as a ~30% classification
flake in the coordinator's native loop runs on the faster substituted
host (per the in-flight tests' honest scope notes) and was diagnosed
there; the window itself is L5-era. Fix: every per-iteration reap is
guarded by `if status is _RUNNING:` (four sites: both phases, both
sources). Tests: a POSIX behavioral test execs each REAL frozen source
against a stub _stage that engineers the window deterministically (a
grandchild holds the stderr write end 0.4 s past the child's exit 3),
10 repetitions x both sources asserting version_done /
wrapper_option_unsupported / exit_code 3 / exact stderr count; plus an
every-host static AST test proving every _try_reap call site sits
under an `status is _RUNNING` guard.

### 5.2 Parent-side drain-first race

Root cause: _ContainedSession's await/version/ready loops
(research_process_linux.py, pre-fix ~1774-1835) raised the generic
research_process_failed the instant supervisor.poll() showed exit,
discarding the supervisor's final classified report line still
buffered in the report pipe (it always writes its final line before
exiting). On the faster substituted host the window opened for real:
a wrapper-option refusal was misreported as the generic failure.
Latent since L5. Fix: drain-first guard - an exited supervisor raises
the generic failure only when a zero-timeout select finds the report
channel empty (the _drain_model idiom). Test: a Linux-only regression
with a dead Popen and a pre-buffered classified line in a real pipe
asserts the buffered event is delivered and mapped
(research_process_wrapper_unsupported); a truly empty channel still
fails generically.

### 5.3 CI AF_UNIX tripwire lift

CI run 37936621543 (dd668cd1) failed exactly one test: the Linux-only
test_relay_channel_builder_moves_both_ends_above_100, "Failed: network
entered". The relay channel builder constructs a real AF_UNIX
socketpair (_create_relay_channel, research_process_linux.py:1570),
and the suite's autouse no_external_io tripwire for synthetic unit
tests (tests/test_research_process_linux.py:568-576) replaces
socket.socket with pytest.fail. The test had never run under CI
before: it skips on the Windows dev host, and W3 reached origin only
after the binding T1 pass. Fix: narrowly restore the real socket
factory for that one test via monkeypatch (the pre-existing
engine-executing-test idiom, :1592-1595).

### 5.4 Re-baseline (L9.1) implications for the frozen pins

Because both frozen helper sources' bytes moved, every digest embedding
them is re-pinned in the batch (old -> new, read from the diff):

| Pin | L9 value | L9.1 value |
|---|---|---|
| OFFLINE_HELPER_SHA256 | 2d7e6515fdecfef86182e9cc7f28bddd21f2c0cc466a4cb59cce5da59ea27cda | 22a396e384be6f597e33009f7bde23f4938de010ac74d6a73f3e95b58302b286 |
| OFFLINE_POLICY_SHA256 | 5ed0074c947ac471a560f148d1074536cf4195ecc4b372bd69b9a13377d60358 | 37e8c9afa9bc76769c4d0223fe0f1dc252d6174a48041a10fc9df6ff421ad56b |
| CLAUDE_V3_GOLDEN linux | a093f08f... | c29b835394bba558a6a2fd33596e2c67e84ff373e8e3e11c60171f949c2813ee |
| CLAUDE_V3_GOLDEN win32 | 563a5fde... | 8f06ef6d37467f5774957319e2a25dc62532cd03b416a63a71f56fc2776002c3 |

Re-measured UNCHANGED by L9.1 (neither embeds the helper digest; full
values at the L9 baseline, tests/test_research_linux_relay_helper_
gates.py:107-116): OFFLINE_CONF_SHA256 f6864007..., OFFLINE_ARGV_
VERSION_SHA256 d34891a5..., OFFLINE_ARGV_MODEL_SHA256 1b9f33c2... The
RELAY_HELPER_SOURCE digest necessarily moved too (its bytes carry the
guarded reap); no standalone relay-digest constant exists to quote -
the gates recompute it (tests/test_research_linux_relay_helper_gates.py
:234-236). Consequences: the batch needs its own independent review
(Iron Rule 4) and landing; CI must return green; and the BINDING T1
must be RE-RUN on 154 at the new digests before W3-native / T8 /
L8-closure evidence is declared - the 69e35331 pass binds the
pre-L9.1 bytes. Per arbitration C5, nothing in or after this batch may
claim T1/T8/node completion beyond its own measured evidence.

### 5.4b T1-harness CA adaptation (added post-authoring)

The landed W3 admission seals the relay CA bundle by READING the pinned
path, so the T1 native harness can no longer carry the surface trust's
declaration-only CA pin (fixed path, placeholder digest - nothing real
can exist there). The batch's fourth part (tests/test_research_
linux_relay_helper_gates.py, _measured_relay_launch) now writes the
real synthetic CA (tests/test_research_linux_relay.SYNTHETIC_CA bytes)
beside the compiled payload and pins it exactly, mirroring W3's own
build_native_relay_probe idiom; the offline shape assertions are
unchanged. First exercised by the L9.1-digest T1 re-run (12/12 on 154,
see section 4); the pre-W3 binding runs and all nine CI preview runs
predate W3's CA sealing and never touched this path.

## 6. Open owner-gated items (unchanged; NOT authorized here)

- Effective activation still requires, verbatim from section 65
  (DELIVERY_PLAN.md:2793-2796): "accepted final engineering/native/
  review evidence, delivered contained transport, official-image
  qualification through actual L5 wiring, a non-shared trusted runtime,
  reviewed explicit per-call credential supply, and a Codex-approved
  pinned activation record with scoped PG evidence writes."
  activation_authorized=false; real provider calls remain false.
- Shared-host tension: 154 is shared by owner instruction, section 65
  demands a non-shared trusted runtime for activation, and the W13
  runbook's T8 step says set POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_
  ISOLATED_HOST=1 only on a truly dedicated host, else STOP. T8 on 154
  needs an explicit ruling (run without the flag and record the W4
  gate's closed preflight-blocker outcome, or an owner/arbitration
  decision). This document does not resolve it.
- G2 remains PARTIAL (real-research half open); G3, G4, G5 open; G6
  PARTIAL; V1 incomplete. No rescoring of historical cohorts.
- L8 closure still waits for final-integrated-tree native evidence +
  verify_local --full (section-65 completion rule); the 68:2993-2997
  stale open-items wording should be reconciled in the next docs
  commit.

## 7. Operator-facing next steps

The operative execution discipline is the W13 runbook
(D:/Projects/.agent-artifacts/polymarket-alpha-lab/l8-l9-recovery-
runbook/README.md, 2026-10-09): Step 1 (binding T1), Step 3.1 (W3
native), Step 3.2 (T8 matrix), Step 4 (the verbatim section-65
five-file block + verify_local --full + git diff --check) and Step 5
(records checklist) govern. Two substitutions since it was authored
for the 166 recovery: the host is now SERVER_HOST_REDACTED, and its Step 0
host expectations are superseded by the section-68 substitution record
(venv = system CPython 3.12.3, an accepted recorded deviation - the
runbook's 3.12.14 STOP expectation no longer applies as written;
re-measure Step 0 values on 154). Order of work:

1. Land the L9.1 batch with its own independent read-only review
   (VERDICT line), commit, push, confirm CI green (closing the
   dd668cd1 red). Nothing else builds on a red CI.
2. Re-run the binding T1 at the new helper digests on 154 (W13 Step 1;
   expect 12 passed incl. the three native tests; preserve first
   failures).
3. W3's two native tests (W13 Step 3.1; expect 2 passed; the dd668cd1
   str/Path fix is already on origin).
4. T8 qualification (W13 Step 3.2) AFTER the shared-host ruling in
   section 6; expect 1 passed + the CLAUDE_RELAY_QUALIFICATION JSON
   line with activation_authorized: false (fake TLS + dummy key).
5. L8 native closure + verify_local --full on the final tree (W13 Step
   4; both mandated proof lines; 35-60 min budget).
6. Records per W13 Step 5 and the section-65 completion rule,
   including the C5 honesty statement.

Facts table for 154 (W15-relayed rows are coordinator-relayed facts
from worker W15's report, 2026-10-09 - no artifact file existed under
.agent-artifacts at authoring time; digest/size cells are
independently anchored, so re-verify at use):

| Fact | Value | Source |
|---|---|---|
| Host / access | ubuntu@SERVER_HOST_REDACTED, pubkey-installed (one-time password not stored) | 68:2972-2976 |
| Repo / venv | /home/ubuntu/polymarket-alpha-lab on main; .venv from system CPython 3.12.3 (recorded deviation from the 3.12.14 release tuple) | 68:2977-2981 |
| bubblewrap | 0.11.1 built into ~/pal-runtime from tarball sha256 c1b7455a1283b1295879a46d5f001dfd088c0bb0f238abb5e128b3583a246f71; non-setuid asserted | 68:2981-2984; W13 :22 |
| userns / cgroup | AppArmor userns sysctl lifted (recorded system-level change); delegated /sys/fs/cgroup/pal (memory+pids, ubuntu-owned); shells migrate in for the v2 common-ancestor rule | 68:2984-2986 |
| T1 binding state | 12/12 incl. three native tests, zero C4 divergence - at PRE-L9.1 digests; re-run pending | 68:2986-2989 |
| T8 image on 154 | Re-acquired from the npm pinned channel, matching sha256 5c4735937844e84f8a93306e841a5b0e12252909b07870f789b190468da147ab / 234,119,480 bytes exactly (W15-relayed; digest/size anchor: DELIVERY_PLAN:2719-2720) | W15 + section 63 |
| PG18 prefix on 154 | ~/pg18-portable-prefix, proven (W15-relayed; satisfies the runtime gate: bin/{postgres,initdb,pg_ctl,psql,pg_controldata} + share/postgres.bki, majors 16/17/18 accepted, PG18 for fidelity) | W15 + W12 report |
| L8-B fixture on 154 | retained-success-predecode.json transferred byte-equal (W15-relayed); local reference 3,506 bytes, sha256 433ff7b2184e87c1aed4eceddd0e2e30d2e4a45f00e318674e224ba9b3db6a02 - verify the server copy before the L8-B run | W15 + local measurement |
| 166 losses (recorded) | ~/pal-artifacts incl. the L7 final7 52-file export tree; ledger/commit history/local log captures survive | 68:2976-2978 |

Allowed: read-only inspection; the gate executions above on 154;
recording results in DELIVERY_PLAN.md. Forbidden: declaring T8, L8, L9
or any gate closed without the cited evidence; activation, provider
calls or credential discovery; rescoring historical cohorts; touching
the in-flight workers' uncommitted files; live trading, account or
private-key handling (the Phase 1 paper-only/report-only/readonly
boundary).

## 8. Pinned references

Commit chain 27d78ca7..HEAD (oldest first; full messages in git). Two
docs commits sit between the predecessor record (b05b9d6e) and W4:
74bcdda7 (the l7-l8-status.md handoff itself) and 2c610449 (the
section-67 record).

| Commit | Slice | Review verdict |
|---|---|---|
| 27d78ca7 | W4: T8 qualification gate file | PASS (3 findings applied) |
| e337ccea | W1: relay core + ENGINE_SOURCE (151/1) | R-W1 PASS (3 MINOR + NOTE prescriptions carried to W2) |
| db44df7c | W5: profile v3 + typed-trust admission | PASS |
| 91168a31 | W6/W6b: relay helper gates (T1+T3) | R-W6 FAIL (6 findings) -> W6b -> R-W6b PASS (execution-verified) |
| ad07d3be | W2: RELAY_HELPER_SOURCE + two-value egress (441/12; full 40747/80/0) | R-W2 PASS, zero findings |
| 50e658bb | section-68 record (L9 offline half) | docs |
| c675308a | section-68 deviation record (W2 reached origin via stacked push) | docs |
| 7834a203 | W9: manual T1 preview workflow (non-binding) | R-W9 FAIL (1 blocker) -> fixed per prescription |
| e0cd874f | preview fix 1: venv from system CPython (run 37713430462) | preview iteration |
| caa55cad | preview diagnostics (run 37713859290) | preview iteration |
| fe13da54 | W10: soname-reachable closure binds (run 37714242116) | R-W10 PASS, zero findings |
| ef7e85db | W11: closure probe covers both helpers' stdlib imports (run 37716295041) | R-W11 PASS |
| b039f8ef | preview: drain supervisor stderr (run 37717937412) | preview iteration |
| 8bebd480 | preview: replicate the cgroup admit sequence (run-6) | preview iteration |
| af131cd0 | preview: mirror production cgroup order (run-7) | preview iteration |
| 054d8163 | preview fix 4: migrate step shells into the delegated subtree (run-8) | preview fix |
| 73d02893 | section-68 T1 preview outcome record (run 37719238674, 12/12 non-binding) | docs |
| a9b47362 | section-68 W3 early-start arbitration (C1-C7) | arbitration record |
| 11e53cc2 | W3: parent-side relay wiring (476/16; full 40782/84/0) | R-W3 PASS (1 MINOR, 7 seams CONFORM); pushed under C2 |
| 69e35331 | section-68 server substitution + binding T1 12/12 on 154 | docs |
| dd668cd1 | W3 native first-run fix (str->Path in the probe directory) | first failure preserved; native rerun pending |

Local artifacts (outside the repository), all under
D:/Projects/.agent-artifacts/polymarket-alpha-lab/: l9-amendment/
README.md (amendment gate record); l9-w3-plan/README.md (the
R-W7-approved plan, binding section-13 addendum at line 621);
l8-native-ci-feasibility/README.md (W12, 2026-10-09, during the
outage: L8-C CI preview GO, corrected-banner round trip GO, L8-B NO-GO
on fixture publication - moot on 154 per W15 - and verify_local --full
NO-GO for preview purposes; optional de-risking only, binding evidence
stays on the server); l8-l9-recovery-runbook/README.md (W13, 2026-10-09,
authored for the 166 recovery; command blocks remain operative with
the section-7 substitutions); l7-final-ruling/retained-success-
predecode.json (the retained L7 success envelope the L8-B opt-ins
require). Style template lineage: docs/handoffs/l7-l8-status.md (the
predecessor), docs/handoffs/pr17-network-check-full.md and
docs/releases/v0.1.0-linux-preview.1/handoff.md.

## 9. Report-back format

Separate newly measured rounds from cited records; preserve first
failures: (1) L9.1 review verdict, commit, CI run/result; (2) T1
re-run counts, duration, log path, any divergence; (3) W3 native
counts and first failures; (4) the shared-host ruling verbatim, then
T8 counts + the CLAUDE_RELAY_QUALIFICATION line; (5) section-65 block
counts, both proof lines, verify_local --full markers, git diff
--check; (6) deviations and the DELIVERY_PLAN.md record commit,
including the 68:2993-2997 reconciliation.
