# G2 activation-readiness pack (draft; requests decisions; authorizes nothing)

Worker N1 of the October 2026 post-L9 cycle, 2026-10-09.
Updated by worker N5 (D-2: activation-record pinning with T8 evidence) on
2026-10-10: every T8-dependent field below moved from PENDING-DECISION /
MEASURED-AT-RUN to the NOW-MEASURED values recorded in DELIVERY_PLAN.md §68
at e76361d7 (decision-chain execution: D-1 attested, T8 EXECUTED AND
PASSED, D-tuple adopted, D-3 provisionally set, D-4 + final go/no-go held).
Nothing in this update authorizes anything; `activation_authorized=false`
everywhere, unchanged (§68:3137-3139).
Companion operator document: [g2-first-real-run-runbook-draft.md](g2-first-real-run-runbook-draft.md).
Plan charter: `.agent-artifacts/polymarket-alpha-lab/post-l9-planning/README.md`
(node N1, with Revision 1 findings F1/F3/F5 binding).

**This document is a DRAFT decision-request package. It authorizes nothing.**
`activation_authorized=false`; real provider calls remain false; no credential
material appears here or is requested here. Filling or approving any part of
the draft record below does not flip any activation flag (the L8-D template's
own rule, [research-dispatch.md](../research-dispatch.md) lines 646-648).

Baseline pins (verified `git rev-parse` on the recording host):

| Item | Value |
|---|---|
| Repository | https://github.com/Lordakee/polymarket-alpha-lab, branch `main` |
| Recording commit (N1, 2026-10-09) | `67cad027969f34bc21dfad64bb4fcb1b7e72b2d9e` (67cad027) |
| Recording tree (N1) | `3cdb1e846c26b706b778970e0c5dbb1f8640a95f` (worktree was clean at 67cad027 before the N1 draft's two new files) |
| N1 commit subject | "docs: record the L9 node final-review PASS and close the node pending T8/D-1 (section 68)" |
| N5 update commit (2026-10-10) | `e76361d79a3e066ec20aa05b6245bbe406157007` (e76361d7; tree `f06056469613e61f5e6f0e866c03440a964bc9f3`; worktree clean at e76361d7 before this update's two file edits) |
| N5 update subject | "docs: record the decision-chain execution and the FULL L9 closure - D-1 attested, T8 PASSED 11/11, D-tuple hybrid re-pin adopted (section 68)" |
| T8 execution tree/commit | `2e5cd30a70f81374c4302ba08592e1495337873b` (2e5cd30a; tree `ae49783d22c7d59194c15cbe51e8d32a274ae217`; §68:3116 records the run as 树 2e5cd30a; 2e5cd30a→e76361d7 touches DELIVERY_PLAN.md only — verified `git diff --stat`: 1 file, +30 doc lines, zero code) |

Evidence discipline (plan Revision 1 F3): every evidence claim below derives
exclusively from LANDED DELIVERY_PLAN.md sections and committed repository
artifacts — at 67cad027 for the N1 authoring, extended at e76361d7 for the
N5 update (§68:3111-3139: the decision-chain execution and FULL L9 closure,
including the T8 evidence recorded against the execution tree 2e5cd30a).
W12/W15 environment facts (e.g. the on-154 PG18 prefix, the transferred
L8-B fixture) remain execution conveniences only: they may be referenced as
re-verify instructions but NO evidence pin in this pack rests on them. One
fact changed status at T8: the official image's on-host provenance is no
longer a W15 relay convenience — §68:3116-3118 records the npm-official-
channel re-acquisition measured byte-identical to the L7 pin; any local
copy is still re-verified before use. Where a value must still be
re-measured at decision time, it is marked PENDING with the owning gate
named.

Terminology guard: "D1–D3" (§7 owner decisions: local agent, research data,
no first-round cap) are settled and distinct from the NEW decision items
introduced below, which this pack writes as "D-1", "D-2", … per the plan
charter's Revision 1 F1 numbering.

---

## 1. The §65 activation-prerequisite mapping

The original §65 ruling quoted below records the approval wording at that
time. Use [Project Agent Routing](../../AGENTS.md#project-agent-routing) for current arbitration.

> "Effective activation requires accepted final engineering/native/review
> evidence, delivered contained transport, official-image qualification
> through actual L5 wiring, a non-shared trusted runtime, reviewed explicit
> per-call credential supply, and a Codex-approved pinned activation record
> with scoped PG evidence writes."

The table walks each prerequisite against what LANDED today. The N1
authoring recorded "L9 node CLOSED pending T8/D-1" (the §68 final review at
67cad027, DELIVERY_PLAN.md:3030-3042: VERDICT PASS; "节点关闭：pending 仅
T8/D-1"). That closure condition is now DISCHARGED: §68:3111-3139 at
e76361d7 records the decision-chain execution — D-1 attested (owner
instruction "按你推荐的做"), T8 EXECUTED AND PASSED (11 过/0 败), D-tuple
adopted, D-3 provisionally set, D-4 + final activation held — and the L9
node is FULLY CLOSED ("L9 节点自此彻底关闭（无剩余门）", §68:3125).

| # | §65 prerequisite (verbatim fragment) | State | Landed evidence / owning gate |
|---|---|---|---|
| 1 | "accepted final engineering/native/review evidence" | **LANDED for the whole L8/L9 engineering arc, T8 included (row 3); the L9 node is FULLY CLOSED with no remaining gate (§68:3125)** | L9 node final review VERDICT: PASS (§68:3030-3042: CI chain re-checked, W2 digests recomputed, frozen pins re-run 9/3, per-commit review chain verified, §67 binding contracts sampled at HEAD, honesty confirmed — "§65 证据点名其树 d3eec34f 满足"). L9.1 landing d3eec34f with the 154 verification chain (§68:3017-3023): binding T1 re-run 12/12 at the L9.1 digests; §65 five-file native block 322 passed/1 skipped (3:37) — L8 native closure (B 3 opt-ins + C 5 + corrected-banner round trip) CLOSED; `verify_local --full` PASS (log `~/pal-artifacts/l91-landing.log` on 154); CI green (run 37955091414); W3 native 121/1 and refusal-stability 40/0. Offline-half commit/review chain: W1 e337ccea (R-W1 PASS), W5 db44df7c (PASS), W6/W6b 91168a31 (R-W6 FAIL→R-W6b PASS), W2 ad07d3be (R-W2 PASS zero findings), W3 11e53cc2 (R-W3 PASS), native fix dd668cd1 (first failure preserved), L9.1 d3eec34f (R-W14 FAIL→supplemental PASS) — §68:2881-2913, 3002-3016. T8 subsequently EXECUTED AND PASSED 11 过/0 败 at tree 2e5cd30a on 154 (§68:3116-3125; detail in row 3). Post-L9 cycle nodes also closed: N4 adversarial audit (R-N4 PASS, §68:3052-3084) and N2 §50-matrix re-establishment on 154 (R-N2 PASS, §68:3086-3109); "post-L9 周期（N1–N4）至此全部闭环" (§68:3108-3109). |
| 2 | "delivered contained transport" | **LANDED** | The relay surface is implemented, reviewed and natively exercised: relay core + ENGINE_SOURCE (e337ccea), profile schema v3 + typed RelayTrustConfig admission (db44df7c), RELAY_HELPER_SOURCE + two-value closed egress (ad07d3be), parent-side wiring with the S2 success contract and no-second-channel rule (11e53cc2, arbitration §68:2950-2973 conditions C1-C7), L9.1 reap/drain fixes (d3eec34f). Native proof on 154: binding T1 fd-crossing 12/12 twice (69e35331 pre-L9.1 at §68:2986-2991; re-run at L9.1 digests §68:3017-3018, zero C4 divergence both times), W3 parent-path native tests green (§68:3022-3023). §68 final review verified the §67 binding contracts at HEAD (§68:3033-3035). |
| 3 | "official-image qualification through actual L5 wiring" (= T8) | **SATISFIED — T8 EXECUTED AND PASSED (§68:3116-3125, recorded at e76361d7)** | Executed at tree/commit 2e5cd30a (`2e5cd30a70f81374c4302ba08592e1495337873b`) on 154 under the full opt-in matrix including ISOLATED_HOST=1 (the D-1 attestation), with the image re-acquired through the npm official channel — sha256 5c47…47ab / 234,119,480 B, measured byte-identical to the L7 pin (§68:3116-3118). Result **11 passed / 0 failed** ("11 过/0 败", §68:3118); stdout carried the CLAUDE_RELAY_QUALIFICATION JSON (§68:3118-3125): activation_authorized:false; relay_evidence.code=relay_refused_method with **derived=false**; dns_resolutions=0; upstream_connections=0; requests_refused=1; cgroup_survivors=[]; launch digests = the L9.1 values (15afa87d…/22a396e3…); port_window=[20000,32767]; credentials=dummy-synthetic; fake service matches=true with zero connections — the relay refused the non-approved-method request at the protocol layer, one of the two designed closed outcomes (gate rule, tests/test_research_claude_relay_native.py:704-716, JSON line at :750-764). Engineering subset only: success can NEVER authorize real activation (gate header :1-21). |
| 4 | "a non-shared trusted runtime" | **SATISFIED — D-1 RESOLVED by owner attestation (§68:3113-3115)** | The owner's explicit instruction "按你推荐的做" (2026-10-10) adopted the coordinator recommendation: SERVER_HOST_REDACTED constitutes a dedicated/non-shared host in the §65 sense (option 1 of the D-1 shapes below), on the complete fact base of §68:3025-3027 plus the §68:3044-3050 coordinator-measured four items (`ls /home/` shows only `ubuntu`; `/etc/passwd` has no other UID≥1000 human user; all 15 tmux terminal sessions belong to ubuntu, owner-operated, earliest traceable 2026-08-29; `sudo -n true` passes, NOPASSWD). The attestation satisfies R-W16 F1's owner-attestation requirement and the W13 runbook's ISOLATED_HOST premise; T8 subsequently ran with POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_ISOLATED_HOST=1 under it (§68:3116-3117). The earlier shared-machine record (§68:2975-2981) is superseded for §65 purposes by this later owner ruling. The record's runtime_isolation fields are now filled from this attestation (§3 below). |
| 5 | "reviewed explicit per-call credential supply" | **PENDING-DECISION D-4 — designed, not approved, not executed (HELD, §68:3137-3139)** | The operator procedure is designed and landed as design text (research-local-agent.md:1513-1551: dedicated principal, private local channel, `FiniteInMemoryApiKeySupplier` one-slot-per-team, delayed model-phase delivery, no-erasure honesty, no credential in evidence). The supplier type and its stop-token identity binding are implemented and natively proven in the §54 composition (tests/test_project_postgres_uncapped_audit_native.py:347). Its D-1 precondition is now discharged (row 4), but the approval to execute remains owner-gated (§53:1681-1685) and is HELD at the credential boundary: the final go/no-go and the credential-supply window require the owner's explicit statement before the fully pinned record (§68:3137-3139). |
| 6 | "a … pinned activation record with scoped PG evidence writes" | **RECORD PINNED WITH ALL AVAILABLE EVIDENCE (this N5 update); D-2 APPROVAL REMAINS THE GATE** | The record template was delivered at 35478f53 (§66:2819-2820, design gate PASS). The §3 fill is now pinned with all available evidence: the T8-dependent fields carry the §68 NOW-MEASURED values (`contained_qualification` cites the executed T8 run), and the runtime_isolation fields carry the D-1 attestation. The remaining PENDING fields are exactly D-4 (credential procedure + window) and the final owner go/no-go (§68:3137-3139), plus the approval-time measurements re-taken on the exact run host/tree at pinning. Flipping `status` to `approved-not-yet-effective`/`effective` remains the D-2 decision (native-subagent arbitration + owner confirmation); approval flips no flag by itself. |
| 7 | "Codex-approved" (historical wording) | **D-2 approval authority** | Obtain fresh, independent read-only arbitration with an explicit verdict under Project Iron Rule 4 and [Project Agent Routing](../../AGENTS.md#project-agent-routing). Owner confirmation remains required for the owner-gated decisions, including D-4 and the final go/no-go. |

Downstream (not §65 prerequisites, recorded for honesty): the G2 real-research
half itself — at least one reviewed real execution per `crypto_btc`/
`crypto_eth` with authorization/identity/usage reconciliation and original
failures preserved (§61:2299-2301; §63:2743-2751) — plus G3 final combination
(§61:2303-2305), G4 real settlements (§4:74) and G5 real samples (§4:75)
remain open and are NOT advanced by this pack. G2's fixed-official-artifact
six-scenario synthetic-probe subcondition alone is closed (§63:2743-2751).
V1 completion stays 1/6 (§4:78).

---

## 2. Decision requests (owner/arbiter-gated; this pack decides none of them)

These are laid out with facts only. Per the plan charter §2, activation
authorization, the pinned activation record, non-shared runtime provisioning,
per-call credential supply approval and scoped real-run execution are all
owner/arbiter-gated; nothing below may be read as deciding or preconditioning
them (plan-review question 2).

### D-1 — Is the designated Linux host a "dedicated/non-shared host" in the §65 sense? (RESOLVED 2026-10-10 — see Status; originally blocked T8 and rows 4-5 above)

- **What §65 requires:** "a non-shared trusted runtime". The activation-record
  template requires a dedicated host identity, a documented host-trust
  reference, and a dedicated non-shared principal — explicitly "NEVER the
  shared ubuntu parent/supplier" (research-dispatch.md:670-676).
- **What the record shows:** 154 is recorded as a shared machine by owner
  instruction; only `~/polymarket-alpha-lab` is touched; the one-time password
  was used solely to install the coordinator public key and never stored
  (§68:2975-2979). The L8-D credential procedure states a separate directory
  does not protect parent memory from the same UID or privileged observers
  (research-local-agent.md:1518-1524).
- **Facts the §68 record already carries for the decision** (§68:3025-3027,
  plan Revision 1 F1): `/home` contains only the `ubuntu` principal; all tmux
  sessions on the machine belong to it and are owner-operated; no other human
  users exist; sudo is passwordless.
- **Original request:** the owner's explicit attestation, or the ruling of
  the arbiter under the rule then in effect, on whether the designated Linux
  host met §65. The October 2026 options were:
  1. Attest 154 qualifies (e.g. single-trustee machine in the §65 sense) →
     T8 may run on 154 with `POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_ISOLATED_HOST=1`
     as the recorded attestation, and the runtime_isolation fields of the
     record are filled from the attestation.
  2. Rule 154 NOT dedicated → T8 and any activation-time credential supply
     wait for a qualified non-shared host (provisioning is a separate owner
     decision), or for an owner-defined alternative trust posture recorded as
     an acceptance change (§6:155-156).
- **Status: RESOLVED (2026-10-10).** The owner's explicit instruction
  "按你推荐的做" adopted the coordinator recommendation; §68:3113-3115
  records 154 as qualifying as dedicated/non-shared in the §65 sense
  (option 1 above), on the complete fact base of §68:3025-3027 plus the
  §68:3044-3050 coordinator-measured four items (source: deployment-time
  server inspection output). The attestation satisfies R-W16 F1's
  owner-attestation requirement and the W13 runbook's ISOLATED_HOST
  premise; T8 subsequently ran on 154 with
  POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_ISOLATED_HOST=1 under this attestation
  (§68:3116-3117). The §3 record fields previously PENDING on D-1 are
  filled below. The earlier shared-machine record (§68:2975-2981) is
  superseded for §65 purposes by this later owner ruling; the historical
  text above is retained unchanged as the decision's fact basis.

### D-2 — Approval of the pinned activation record (gates effective activation)

- The T8 precondition is SATISFIED: the record's `contained_qualification`
  field now cites the executed T8 run (§68:3116-3125; filled in §3 below).
  The record draft is now PINNED with all available evidence — every field
  with landed evidence carries it, and the only placeholders left are the
  ones named below.
- The remaining PENDING fields are exactly: D-4 (credential-supply
  procedure approval + execution window) and the final owner go/no-go
  (§68:3137-3139 — both require the owner's explicit statement before the
  record may be fully pinned and approved), plus the approval-time
  measurements re-taken on the exact run host/tree at pinning (the
  baseline re-pin rule stands: the tree has moved past 67cad027 through
  documentation-only commits to e76361d7, and will move again when this
  update lands).
- D-2 requires independent read-only arbitration under the
  [Project Agent Routing](../../AGENTS.md#project-agent-routing) and owner confirmation (row 7).
- Approval flips no flag by itself: `activation_authorized` changes only
  through the explicit activation decision recorded with the record
  (research-dispatch.md:646-648, 797-799). The record's `status` stays
  `blocked` until that decision.

### D-3 — Real-endpoint preflight set (provisionally set by T8 evidence)

- The pinned Claude Code 2.1.278 was observed issuing exactly one bodyless
  unauthenticated `HEAD /api/hello` preflight before the Messages POST in the
  L7 SYNTHETIC loopback environment; that approval "authorizes nothing at a
  real endpoint" (research-local-agent.md:1439-1465). The relay's request set
  today contains exactly one Messages POST and NO preflight entries
  (src/polymarket_alpha_lab/research_linux_relay.py:1913-1914 defaults;
  qualification constants tests/test_research_claude_relay_native.py:83-89).
- If the CLI repeats the preflight at the real origin, the call fails closed
  by design (a deliberate blocker, not a gap to patch). The L8-D order is:
  demonstrate the exact preflight through the contained path with a fake
  service and dummy credentials first, then a reviewed decision approves
  exactly that set into `preflight_set` (research-local-agent.md:1447-1465).
- **Status: PROVISIONALLY SET (§68:3134-3136).** The T8 fake-service
  qualification supplied the L8-D order's demonstration step: the CLI's
  request was refused at the relay protocol layer (relay_evidence.code=
  relay_refused_method, requests_refused=1, fake service zero connections,
  §68:3119-3125). Per the owner-adopted recommendation chain, the first
  real run's preflight set maintains the L7-approved form — the single
  HEAD /api/hello — with NO new endpoint authorization (§68:3134-3136,
  citing §63). The `preflight_set` field of the §3 record now carries this
  provisional set; enforcement into the launch request set happens at
  assembly under the approved record (the landed relay default remains
  exactly one Messages POST with no preflight entries).

### D-4 — Credential-supply procedure approval and execution window

- The procedure is designed (research-local-agent.md:1513-1551) and its
  supplier machinery is proven (§54 native composition); approval to EXECUTE
  it (typed once per key through a private local channel on the dedicated
  principal) is owner-gated and preconditioned on D-1. Requested together
  with D-2 at activation time, not now.
- **Status: HELD (§68:3137-3139).** The authorization chain's advance stops
  at the real credential boundary: the final go/no-go and the credential
  supply window require the owner's explicit statement before the fully
  pinned record. The N1 runbook draft §F (Step F) is the operational
  surface; `activation_authorized=false` unchanged.

### Cross-reference (owned elsewhere, not re-requested here)

- **Release-tuple / host gap — RESOLVED as the D-tuple hybrid re-pin
  (§68:3126-3133, N3 option c)**: G6's code-checked surface keeps
  linux/x86_64 + PG major 18 + per-release exact PG build recorded;
  OS/Python become recorded environmental facts with floors (Python ≥3.11;
  kernel/namespace closure per the T1 empirical convention); 154 is the
  first re-measured instance (Ubuntu 24.04.1 / kernel 6.8 / CPython
  3.12.3 / PG 18.6 PGDG 18.6-1.pgdg24.04+2, provenance honestly named);
  the prerequisite evidence is satisfied by the N2-S6 distributed run
  (1 passed, kit provenance 67cad027) and CI linux-kit green (§62:2447).
  No G2-G5 criterion changed; this is NOT activation authorization; §61's
  original wording is retained as history. The record's `interpreter`
  field reflects this (below).
- The N2 native-evidence re-establishment and N4 relay-surface audit have
  both landed and closed: N4 adversarial audit + R-N4 independent
  re-verification PASS (§68:3052-3084: zero BLOCKER/zero MAJOR/zero fixes;
  13+ constructed attacks all fail-closed; five MINOR and seven NOTE
  findings deferred with reopening conditions), and the N2 §50-matrix
  re-establishment on 154 + R-N2 verification (§68:3086-3109: 66 passed/
  2 skipped/0 failed; the distribution kit first passing on 154,
  source_commit=67cad027; CodeGraph unblocked on 154). "post-L9 周期
  （N1–N4）至此全部闭环" (§68:3108-3109).

---

## 3. Activation record (L8-D template fill — pinned with all available evidence; a DRAFT until the D-2 approval decision)

Template source: [research-dispatch.md](../research-dispatch.md) lines
655-749. The template's own rule governs (lines 646-653): filling does NOT
flip any activation flag; the template is a review schema recorded through
the documentation/PR path; fields without evidence references stay
placeholders. The draft's authority fields follow the
[Project Agent Routing](../../AGENTS.md#project-agent-routing)
and the owner gates listed above. T8 HAS run (§68:3116-3125) and is
recorded without credential material in this fill. It is
pinned below as ENGINEERING evidence only; nothing here claims activation
occurred, that any real provider call was made, that D-4 was approved, or
that the final owner go/no-go was given.

```text
activation_record_id : DRAFT-PAL-AR-2026-10-09-A   (draft suffix until the
                      D-2 approval decision; the fill below is PINNED with
                      all available evidence at e76361d7 by worker N5,
                      2026-10-10)
created_utc          : 2026-10-09 (N1 draft authoring); 2026-10-10 (N5
                      evidence pinning at e76361d7); re-stamped at the D-2
                      approval decision
status               : blocked
                      (D-1 resolved + T8 passed, 68:3111-3125; now blocked
                      on D-4 -> final owner go/no-go -> the D-2 approval
                      decision; nothing below is effective)
deciding_authority   : independent, freshly dispatched read-only
                      arbitration per AGENTS.md Native Codex Subagent
                      Routing and Project Iron Rule 4, with explicit
                      VERDICT line and owner confirmation for
                      owner-gated items (Article 0; DELIVERY_PLAN.md
                      §6). D-4 and the final owner go/no-go remain
                      owner decisions before approval of the pinned
                      record.
valid_window_utc     : PENDING (D-4 window statement + final owner
                      go/no-go, 68:3137-3139; set at the D-2 approval;
                      must cover the whole intended run)

baseline_pins
  repository         : https://github.com/Lordakee/polymarket-alpha-lab
  commit_sha         : e76361d79a3e066ec20aa05b6245bbe406157007  (draft pin:
                      the decision-chain execution record commit carrying
                      the T8 evidence, 68:3111-3139; RE-PINNED to the exact
                      tree the approved run executes on — D-2. 2e5cd30a
                      (T8 execution) -> e76361d7 touches DELIVERY_PLAN.md
                      only, verified git diff --stat)
  tree_sha           : f06056469613e61f5e6f0e866c03440a964bc9f3  (e76361d7
                      tree; worktree clean before this update's two file
                      edits; same re-pin rule)
  branch             : main

runtime_isolation
  host_id            : ubuntu@SERVER_HOST_REDACTED (Ubuntu 24.04.1, kernel 6.8,
                      16 cores, 31 GiB) — D-1-ATTESTED dedicated/non-shared
                      in the 65 sense by the owner's explicit instruction
                      "按你推荐的做" (2026-10-10, 68:3113-3115); the earlier
                      shared-machine record (68:2975-2981) is superseded for
                      65 purposes by that later owner ruling
  host_trust_doc     : the D-1 owner attestation IS this field's content:
                      68:3113-3115 (owner-made attestation satisfying
                      R-W16 F1) resting on the complete fact base of
                      68:3025-3027 + the 68:3044-3050 four coordinator-
                      measured items (ls /home -> only ubuntu; /etc/passwd
                      no other UID>=1000 human user; all 15 tmux sessions
                      belong to ubuntu, owner-operated, earliest traceable
                      2026-08-29; sudo -n true passes, NOPASSWD)
  principal          : ubuntu — the SOLE principal on the D-1-attested
                      host (/home holds only ubuntu; no other human user;
                      68:3044-3050). The owner's D-1 attestation rules
                      this single-trustee shape qualifies in the 65 sense;
                      the template's separate-account wording presumed a
                      multi-trustee shared host. Credential supply under
                      this principal remains blocked by D-4 (68:3137-3139).
  attestation        : owner attestation 2026-10-10 via explicit
                      instruction "按你推荐的做" (68:3111-3115); verifier =
                      the owner; method = explicit owner adoption of the
                      coordinator recommendation resting on the 68:3044-
                      3050 deployment-time server inspection output; date
                      = 2026-10-10. T8 ran under it with
                      POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_ISOLATED_HOST=1
                      (68:3116-3117), which is the recorded ISOLATED_HOST
                      attestation the W13 runbook requires.

engineering_prerequisites   (each field cites its evidence; all mandatory)
  cleanup_corrections       : LANDED — L8-A commit 111420db (cleanup-success
                              suppression + exact contained banner), review
                              VERDICT: PASS (DELIVERY_PLAN.md 66:2818-2821);
                              semantics extended by the L9.1 reap/drain fixes
                              d3eec34f (R-W14 FAIL -> supplemental PASS,
                              68:3002-3016) and exercised natively in the
                              65-block (322 passed/1 skipped, 68:3018-3020).
  relay_design_gate         : LANDED — L8-D design package commit 35478f53,
                              design gate VERDICT: PASS (DELIVERY_PLAN.md
                              66:2819-2820); design text: research-dispatch
                              .md 634-799 + research-local-agent.md 1256-1564.
  relay_implementation      : LANDED — W1 e337ccea (R-W1 PASS), W5 db44df7c
                              (PASS), W6/W6b 91168a31 (R-W6 FAIL -> R-W6b
                              PASS), W2 ad07d3be (R-W2 PASS, zero findings),
                              W3 11e53cc2 (R-W3 PASS; arbitration C1-C7
                              68:2950-2973), L9.1 d3eec34f; node final
                              review PASS 67cad027 (68:3030-3042).
  contained_qualification   : EXECUTED AND PASSED — T8 at tree/commit
                              2e5cd30a (2e5cd30a70f81374c4302ba08592e1
                              4953337873b) on 154, full opt-in matrix incl.
                              ISOLATED_HOST=1 under the owner-attested D-1;
                              image sha256 5c47…47ab / 234,119,480 B
                              re-acquired through the npm official channel,
                              byte-identical to the L7 pin (68:3116-3118).
                              Counts: 11 passed / 0 failed ("11 过/0 败",
                              68:3118). Stdout CLAUDE_RELAY_QUALIFICATION
                              JSON (68:3118-3125):
                              activation_authorized:false;
                              relay_evidence.code=relay_refused_method,
                              derived=false; dns_resolutions=0;
                              upstream_connections=0; requests_refused=1;
                              cgroup_survivors=[]; launch digests = L9.1
                              values (15afa87d…/22a396e3…);
                              port_window=[20000,32767];
                              credentials=dummy-synthetic; fake service
                              matches=true with zero connections — the relay
                              refused the non-approved-method request at
                              the protocol layer: one of the two designed
                              closed outcomes (gate rule tests/
                              test_research_claude_relay_native.py:704-716,
                              JSON line :750-764). Engineering subset only:
                              success never authorizes real activation.
  reply_composition         : LANDED — L8-B commit 3acdf5cc (review PASS,
                              DELIVERY_PLAN.md 66:2818-2821); native closure
                              on the final tree inside the 65-block (68:
                              3018-3020), fixture re-verify instruction in
                              the runbook (W15 relayed copy is non-evidence).
  recovery_evidence         : LANDED — L8-C commit e88a0c13 (review PASS,
                              DELIVERY_PLAN.md 66:2818-2821); native closure
                              with both mandated proof lines ("native driver
                              loss: PASS", "native useful recovery: PASS")
                              in the 65-block on the final tree (68:3018-
                              3020; proof lines at tests/test_project_
                              postgres_uncapped_audit_native.py:1385,1784).

pins
  vendor_image       : LANDED IDENTITY — Claude Code 2.1.278, sha256
                       5c4735937844e84f8a93306e841a5b0e12252909b07870f789b19
                       0468da147ab, 234,119,480 bytes (DELIVERY_PLAN.md
                       63:2719-2721; signature chain verified 63:2502-2506).
                       On-host provenance NOW-MEASURED at T8 (68:3116-3118):
                       re-acquired through the npm official channel on 154
                       and measured byte-identical to the L7 pin. On-host
                       path: re-verify the digest on the run host before
                       use (any local copy remains re-verify-only, plan F3).
  wrapper            : bubblewrap 0.11.1 built from the official tarball
                       sha256 c1b7455a1283b1295879a46d5f001dfd088c0bb0f238ab
                       b5e128b3583a246f71 (DELIVERY_PLAN.md 68:2984-2985,
                       non-setuid asserted). Wrapper-binary sha256/size:
                       MEASURED AT RUN on the exact host (T1 discipline;
                       not individually recorded in the 68 T8 block).
  helper             : OFFLINE_HELPER_SHA256 22a396e384be6f597e33009f7bde23f4
                       9838de010ac74d6a73f3e95b58302b286 (L9.1 re-pin,
                       DELIVERY_PLAN.md 68:3010-3011); protocol
                       research-linux-helper-v1 (RELAY_HELPER_SOURCE header,
                       src/polymarket_alpha_lab/research_linux_relay.py
                       :1041). NOW-MEASURED at T8: the qualification JSON's
                       launch digest summary equals the L9.1 values
                       (15afa87d…/22a396e3…, 68:3122; the 22a396e3… member
                       is the OFFLINE_HELPER_SHA256 above; the 15afa87d…
                       member's full value lives in the pinned T8 JSON
                       output). The relay-helper digest is recomputed by the
                       gate file at admission (tests/test_research_linux_
                       relay_helper_gates.py) — re-verified at real-run
                       assembly.
  interpreter        : the venv interpreter actually used on the runtime
                       host — sha256 + size MEASURED AT PINNING on the run
                       host. Platform fact NOW RECORDED via the adopted
                       D-tuple hybrid re-pin (68:3126-3133, N3 option c):
                       OS/Python are recorded environmental facts with
                       floors (Python >=3.11; kernel/namespace closure per
                       the T1 empirical convention) — no longer a
                       "deviation"; 154 is the first re-measured instance
                       (Ubuntu 24.04.1 / kernel 6.8 / CPython 3.12.3 /
                       PG 18.6 PGDG 18.6-1.pgdg24.04+2, provenance honestly
                       named). Closure provenance of system interpreters
                       established by W10 (fe13da54).
  runtime_closure    : per-file guest path + sha256 + size — MEASURED AT RUN
                       by the existing discover_runtime_closure launch path
                       (recipe: tests/test_research_process_linux.py
                       :2395-2408); no static value exists or is invented.
                       T8 qualification-path measurement on record: the
                       launch digest summary matched the L9.1 values
                       (15afa87d…/22a396e3…, 68:3122); the real run's full
                       closure is still measured at assembly.
  relay              : engine peer = ENGINE_SOURCE (src/polymarket_alpha_lab/
                       research_linux_relay.py:102; digest recomputed and
                       sealed at admission per W3), protocol
                       research-linux-relay-v1 (research_linux_relay.py:40);
                       inner peer = RELAY_HELPER_SOURCE (research_linux_
                       relay.py:1041; digest recomputed by the gates).
  trust_configuration: PENDING-DECISION D-2 (approval-time artifact) —
                       defaults LANDED and typed: origin hostname
                       api.anthropic.com (DEFAULT_ORIGIN_HOSTNAME,
                       research_linux_relay.py:41), scheme https, port 443,
                       TLS floor tls12 (research_linux_relay.py:42-44),
                       permitted-address rule public-global-unicast-only
                       (research_linux_relay.py:1901). The CA bundle is a
                       REAL root set to be selected, pinned (sha256 + size,
                       RelayCaBundlePin, research_linux_relay.py:1875-1887)
                       and REVIEWED at approval; no ambient system roots.
  profile            : schema v3 (_relay_v3_digest; W5 db44df7c), profile
                       digest computed over the serialized launch policy
                       including the trust configuration — MEASURED AT
                       PINNING for the real trust config (the landed v3
                       goldens c29b8353…/8f06ef6d… at DELIVERY_PLAN.md
                       68:3010-3011 bind the synthetic trust, not a real
                       one; T8's launch digest summary matched the L9.1
                       values on the qualification path — 15afa87d…/
                       22a396e3…, 68:3122 — again synthetic trust).
                       contract_sha256: from the constructed profile.

provider_and_model
  provider           : Anthropic Messages via the pinned Claude Code CLI
                       (2.1.278; version banner constant CLAUDE_VERSION,
                       src/polymarket_alpha_lab/research_claude_exec.py:23)
  model_id / effort / cli_version : model_id claude-opus-5 (MODEL_ID,
                       src/polymarket_alpha_lab/research_claude_profile.py
                       :27) / effort NOT CARRIED by the profile surface
                       (PENDING: state the exact CLI invocation values at
                       pinning) / cli_version 2.1.278.

reviewed_requests
  rotation/batch/turn IDs : PENDING (D-2 window; assembled per runbook
                       Steps E-F; no real reviewed BTC/ETH requests exist
                       today — real predictions exist only after activation,
                       plan 2/WP-04 row)
  request_roster     : 1..2 exact (record_id, content_sha256) pairs — the
                       reviewed two single-request batches (one per team);
                       template's 1..100 roster applies to the general
                       schema, the reviewed first-run shape is one request
                       per team (src/polymarket_alpha_lab/research_claude_
                       operator.py:131-133)
  authorization      : authorization_id + adapter_contract_sha256 (= profile
                       contract_sha256) + approved_utc + expires_utc —
                       created by the existing typed authorization path at
                       run time (recipe: tests/test_project_postgres_
                       uncapped_native.py:34-41; binding enforced by the
                       operator's canonical-input check, research_claude_
                       operator.py:143-149)
  input_review       : PENDING — reference to the WP-01-reviewed BTC/ETH
                       inputs assembled at run time (runbook Step E)

finite_limits
  dispatch           : max_tasks=2, max_workers=2 (the reviewed first-run
                       configuration; 1..2 are the only admitted values,
                       src/polymarket_alpha_lab/research_claude_operator.py
                       :156-159); one rotation, one turn (turn-one; later
                       turns only via the explicit new-turn path)
  supplier_slots     : per-team 1 (one key per team; FiniteInMemoryApiKey
                       Supplier, src/polymarket_alpha_lab/research_claude_
                       profile.py:248; construction requires the stop token
                       by identity; slots consumed permanently)
  process            : timeout_ms / cleanup_timeout_ms / stdin-stdout-
                       stderr caps / memory-pids-scratch caps — set on the
                       launch at assembly (recipe values in tests/test_
                       research_process_linux.py:2400-2429; the real values
                       are recorded at pinning)
  relay              : LANDED DEFAULTS — max request 16,000,000 B; response
                       32 MiB in 4,096-B reads; connect 10,000 ms; handshake
                       10,000 ms; idle 30,000 ms; total = remaining spec
                       budget (research-linux-relay constants,
                       src/polymarket_alpha_lab/research_linux_relay.py
                       :54-60; design table research-local-agent.md
                       :1369-1377)
  stop               : stop token identity binding — the SAME ResearchDis
                       patchStop object supplied to the supplier
                       construction and the rotation (admission guard,
                       src/polymarket_alpha_lab/research_claude_operator.py
                       :112-115; 54-native identity assertions,
                       tests/test_project_postgres_uncapped_audit_native.py
                       :467-476)

scoped_pg_writes
  project_root       : the project-private root on the runtime host (host
                       D-1-attested: ubuntu@SERVER_HOST_REDACTED; D-2 approval
                       pending); every DSN through the single audited
                       validate_local_postgres_dsn gate only (Project Iron
                       Rule 1)
  permitted_classes  : uncapped authorization row + receipt, call-start
                       rows, outcome rows, batch/turn reservation rows (the
                       existing durable uncapped/dispatch paths; 53:1622-
                       1629; relay adds zero database surface,
                       research-local-agent.md:1498-1511)
  prohibition        : no other writes; no new migrations unless separately
                       reviewed; disposable per-test clusters for any gate
                       runs (W13 boundary); no business-DB mutation

preflight_set        : PROVISIONALLY SET (D-3 per 68:3134-3136) — the
                       single L7-approved bodyless unauthenticated
                       HEAD /api/hello entry (63:2658-2688). T8 supplied
                       the L8-D order's demonstration step: the CLI's
                       request was refused at the relay protocol layer
                       (relay_evidence.code=relay_refused_method,
                       requests_refused=1, fake service zero connections,
                       68:3119-3125), and the owner-adopted recommendation
                       sets the first real run's preflight to the L7 form
                       with NO new endpoint authorization (68:3134-3136).
                       Enforcement into the launch request set happens at
                       assembly under the approved record; the landed relay
                       default remains exactly one Messages POST with no
                       preflight entries (research_linux_relay.py:1913-
                       1914). Nothing here authorizes a real endpoint.

credential_supply
  channel            : PENDING-DECISION D-4 (D-1 resolved; HELD at the
                       credential boundary, 68:3137-3139) — private local
                       channel on the dedicated principal (interactive
                       local terminal prompt or an explicitly reviewed
                       local entry helper; never chat/Git/repo files/
                       ambient env/argv/stored-secret discovery/logs),
                       research-local-agent.md:1525-1534. This fill
                       contains no channel mechanics beyond that boundary
                       (plan N1 acceptance).
  slots_supplied     : 1 per team (crypto_btc, crypto_eth)
  attestation        : no chat/Git/env/argv/file/log/stored-secret use; no
                       secure-erasure claim (research-local-agent.md
                       :1536-1546)

identity_and_usage
  expected_identity  : the pinned CLI's version banner "2.1.278 (Claude
                       Code)" through the contained version phase
                       (CLAUDE_VERSION, research_claude_exec.py:23; L8-B
                       corrected-banner round trip, 68:3018-3020) and the
                       provider's served identity/usage as reported in the
                       admitted response envelope
  check_method       : the existing durable uncapped audit (call-start/
                       outcome rows + reply fingerprints, 54-native per-
                       record reconciliation) plus decoder admission;
                       identity mismatch stops further admission
  unknown_billing    : remains unknown; never recorded as zero (65:2796-/
                       61:2301 cap-honesty rule)

evidence
  commands_results   : to be the exact commands, outputs, first failures,
                       retries, skipped and unexecuted steps of the approved
                       run (runbook 2/G). Already-pinned baseline evidence:
                       68:3017-3042 (T1 re-run, 65-block, verify_local
                       --full log path ~/pal-artifacts/l91-landing.log, CI
                       37955091414, node final review) and 68:3111-3139
                       (the decision-chain execution: D-1 attestation
                       facts; T8 execution at tree 2e5cd30a with the
                       CLAUDE_RELAY_QUALIFICATION JSON and 11 过/0 败;
                       D-tuple adoption; D-3 provisional set; D-4/final
                       held).

sign_off             : PENDING — operator reference, arbitration
                       verdict reference under AGENTS.md, and
                       owner confirmation references.
                       No sign-off exists at update time; the D-2 approval
                       decision is still outstanding.
```

Draft-status honesty notes on the fill (updated by N5, 2026-10-10):

1. Every remaining `MEASURED AT RUN/PINNING` field is a placeholder in the
   template's sense (research-dispatch.md:652-653): it names the mechanism
   that will produce the value, and invents no value. Fields whose values
   T8 measured now carry the §68-recorded NOW-MEASURED values.
2. Every remaining `PENDING-DECISION` field names its owning gate — after
   the N5 update these are exactly D-4 (credential procedure + window),
   the final owner go/no-go (§68:3137-3139), and the D-2 approval decision
   itself (record status/sign-off/valid_window).
3. T8 execution IS now asserted, as pinned engineering evidence only
   (§68:3116-3125); nothing in the fill asserts activation, real provider
   calls, credential existence, D-4 approval, or the final go/no-go.
4. The W15-relayed on-154 facts (PG18 prefix, L8-B fixture) appear only as
   re-verify instructions, never as pins (plan F3). The T8 image's
   npm-channel re-acquisition is the one on-host fact promoted to evidence,
   because §68:3116-3118 records it as measured at the T8 run itself.

---

## 4. What this pack does NOT claim (honesty section)

- T8 HAS run and PASSED (11 过/0 败, §68:3116-3125) and the L9 node is
  FULLY CLOSED (§68:3125) — but T8 is engineering-subset evidence only:
  "success can NEVER authorize real activation" (the T8 gate file's own
  header and rule, tests/test_research_claude_relay_native.py:1-21,
  704-716). No engineering pass authorizes activation.
- No activation has occurred; `activation_authorized=false` everywhere;
  real provider calls false (§65:2791-2792, §68:3027-3028, §68:3137-3139).
- The host-trust reference now exists as the D-1 owner attestation
  (§68:3113-3115) and the principal question is settled by it; still NOT
  existing for a real run today: the real CA bundle, any reviewed real
  input, and any credential (D-4 held at the credential boundary,
  §68:3137-3139).
- G2 remains PARTIAL (real-research half open); G3/G4/G5 open; G6 PARTIAL;
  V1 1/6 (§4:78, l8-l9-status.md §6). The D-tuple adoption changes no
  G2-G5 criterion (§68:3126-3133).
- This pack adds no new gate, framework, layer or acceptance criterion; it
  converts landed evidence into a decision package (plan §3 N1) and, in the
  N5 update, pins that package's T8-dependent fields from §68 (plan §3 N5).

## 5. Labeled self-review (N1 implementer)

Performed against the plan N1 charter and dispatch constraints:

- Scope: only this file and the companion runbook draft created; no src/,
  tests/, workflow, migration or ledger changes; not committed (coordinator
  gates/commits).
- Prerequisite fidelity: all seven §65 fragments walked; landed citations
  checked against DELIVERY_PLAN.md sections 61-68 and repository files at
  67cad027; T8 and every owner-gated item marked PENDING-DECISION with the
  owning gate named; D-1 not resolved or preconditioned (facts only, from
  §68's own record).
- Template fidelity in the October 2026 N1 draft: field order, names,
  and filling/flipping rules were preserved. The draft then reflected
  the arbitration route recorded in §66 (plan F5). The live route for
  reuse is in the project root rule linked above.
- Credential discipline: zero credential material; the credential_supply
  fields carry only the designed boundary text.
- Non-evidence discipline: W12/W15 facts used only as re-verify pointers;
  all pins anchored in ledger sections or repo files (F3).
- Known limits: (a) the draft's `commit_sha` pin will drift if the tree
  moves before D-2 — re-pin at submission (stated in the fill); (b) the
  `effort` value is not carried by any landed profile surface and is left
  PENDING rather than guessed; (c) the request_roster field restates the
  template's 1..100 general schema against the reviewed two-request
  first-run shape — flagged inline, not silently narrowed.

## 6. Labeled self-review (N5 implementer, 2026-10-10 — SELF-REVIEW, not the independent review gate)

Scope: only the two files named in the N5 dispatch (this file and the
companion runbook draft) were modified; no src/, tests/, workflow,
migration or ledger changes; NOT committed (coordinator gates + a fresh
read-only review subagent per Project Iron Rule 4 remain ahead).

Field-by-field PENDING→MEASURED/NOW-SET moves made in this update (every
value from DELIVERY_PLAN.md §68:3111-3139 at e76361d7 unless noted):

| Field / row | Was (N1, at 67cad027) | Now (N5, at e76361d7) | Source |
|---|---|---|---|
| §65 row 3 (T8) | PENDING-DECISION D-1 — NOT EXECUTED | SATISFIED — EXECUTED AND PASSED, 11 passed/0 failed | 68:3116-3125 |
| §65 row 4 (runtime) | PENDING-DECISION D-1 — UNSATISFIED | SATISFIED — D-1 owner attestation (option 1) | 68:3113-3115, 3044-3050 |
| §65 row 1 | T8 the "single named exception" | whole arc LANDED; L9 FULLY CLOSED | 68:3125 |
| §65 row 5 (D-4) | PENDING-DECISION D-4 | unchanged PENDING-DECISION D-4, now HELD per ledger | 68:3137-3139 |
| §65 row 6 (record) | DRAFT; pinning pending D-2 | PINNED with all available evidence; D-2 approval the remaining gate | 68:3111-3139 + this update |
| contained_qualification | PENDING-DECISION D-1 (T8 NOT EXECUTED) | EXECUTED AND PASSED: tree/commit 2e5cd30a, full opt-in matrix incl. ISOLATED_HOST=1, image 5c47…47ab/234,119,480 B npm-channel re-acquisition; 11/0; the CLAUDE_RELAY_QUALIFICATION JSON fields verbatim | 68:3116-3125 |
| runtime_isolation.host_id | PENDING-DECISION D-1 (candidate) | ubuntu@SERVER_HOST_REDACTED, D-1-attested | 68:3113-3115 |
| runtime_isolation.host_trust_doc | PENDING-DECISION D-1 | the D-1 owner attestation + complete fact base | 68:3113-3115, 3025-3027, 3044-3050 |
| runtime_isolation.principal | PENDING-DECISION D-1 | ubuntu, sole principal on the attested host; credential supply still D-4-blocked | 68:3044-3050, 3137-3139 |
| runtime_isolation.attestation | PENDING-DECISION D-1 (facts only) | owner attestation 2026-10-10, "按你推荐的做"; T8 ran with ISOLATED_HOST=1 under it | 68:3111-3117 |
| pins.vendor_image (on-host) | MEASURED AT RUN | NOW-MEASURED at T8: npm official channel, byte-identical to L7 pin | 68:3116-3118 |
| pins.helper (launch digests) | MEASURED VALUE RECORDED AT PINNING | NOW-MEASURED at T8: launch digests = L9.1 values (15afa87d…/22a396e3…) | 68:3122, 3010-3011 |
| pins.interpreter (platform) | "recorded deviation" from 3.12.14 tuple | recorded environmental fact with floors via adopted D-tuple; 154 first re-measured instance | 68:3126-3133 |
| pins.runtime_closure (qual. path) | MEASURED AT RUN only | T8 launch-digest match added; real-run closure still measured at assembly | 68:3122 |
| preflight_set | EMPTY (deliberate blocker) | PROVISIONALLY SET (D-3): single L7-approved HEAD /api/hello; no new endpoint; enforcement at assembly | 68:3134-3136 |
| credential_supply.channel | PENDING-DECISION D-1 + D-4 | PENDING-DECISION D-4 only (D-1 resolved) | 68:3137-3139 |
| valid_window_utc | PENDING (D-2) | PENDING (D-4 window + final go/no-go; set at D-2 approval) | 68:3137-3139 |
| baseline_pins | 67cad027 draft pin | e76361d7 draft pin; re-pin rule unchanged | git rev-parse at authoring |
| status | blocked on D-1 -> T8 -> D-2 | blocked on D-4 -> final go/no-go -> D-2 approval | 68:3111-3139 |

Fields deliberately NOT moved (no T8 evidence exists for them in §68):
wrapper-binary sha256/size (still MEASURED AT RUN), interpreter sha256+size
(still MEASURED AT PINNING), trust_configuration real CA bundle
(approval-time), profile real-trust digest (MEASURED AT PINNING),
reviewed_requests (post-activation only), finite_limits process values
(set at assembly), sign_off (D-2 approval outstanding), D-4 row language.

Checks performed: every inserted value traced to §68:3111-3139, §68:3044-3050,
§68:3052-3084, §68:3086-3109 or git metadata verified on this host
(`git rev-parse`/`git diff --stat`); `activation_authorized` appears only as
false; zero credential material added; the D-4 boundary language unchanged
in substance; no claim of D-2 approval, D-4 approval, final go/no-go,
activation, or real provider calls. Known limits: (a) the 15afa87d… digest
is quoted only in its §68-abbreviated form — its full value lives in the
pinned T8 JSON output and is not reproduced anywhere in this repo; (b) the
record's baseline re-pin rule still applies when this update's commit lands.
