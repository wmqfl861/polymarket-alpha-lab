Native ZCode agent sess_subagent_agent_e33568ab-0f0d-4df0-847b-f8a18a7a6857

All evidence is in hand. The three library pins are materially different in model/gate sources, so per-round coherence is the correct designation rule. Writing the final document.

---

# D1 G-prep implementation plan — **v3.0 (final)**: code-ready concretization of accepted v2 §C1/C2/C4

Read-only session throughout: no files created/modified, no tests, no network beyond one `git ls-remote`, no DB, no git mutations, no timers, no managed writes, no old-ledger access, no credentials, no address literals reproduced (sources cited by file only). CodeGraph checked first; index empty, so direct reads were used. Nothing below has been implemented — every proposed file is a *proposal for the node's PR*, not an authorized artifact; v2's mention of generic tooling created no authorization, and none is claimed here.

## 1. Reconciliation to canonical — verified, not assumed

- Canonical `wmq` = `github.com/wmqfl861/polymarket-alpha-lab`; `git ls-remote wmq refs/heads/main` → **`fa01be0b6b7cc0d01886f5aee40456c4d3b726bd`** (matches the coordinator's statement; MO-3/PR #86 in history). This checkout: detached at `3e52516f71fdadeb86ef0d521521e6384ad9bb68`, clean.
- **`fa01be0b` is locally present** (reachable from local branch `eval/fx35-pure-evidence-20261120`); `3e52516f` IS its ancestor. Read-only diff `3e52516f..fa01be0b`: only MO-5d + MO-3 content — `evaluation.py` (public cluster bootstrap), new `optimize/{metrics,walkforward,scorecard,families/crypto}.py`, `optimize/provider.py`, tests, `DELIVERY_PLAN.md`. **Unchanged at canonical: `papersim/scan.py`, `cli.py`, `store_psycopg.py`, `adapters/`, `replay.py`, `pipeline.py`, all of `server-mirror/`** — therefore every interface citation in v2 and below carries to canonical verbatim. Lane status per coordinator, confirmed by the diff: B1 merged; **B2/B3/B4 NOT merged; D1 has zero accrued days; a confirmed G7 shared managed-write product defect is held for the owner** ⇒ this prep performs **no managed writes, no timers, no paper operations, no network captures, no DB activity of any kind**.

## 2. Answers to the v2 review's minor notes (all non-blocking; disposition recorded)

1. Mirror redaction counts: stated imprecisely in v2. Correct statement: `REDACTIONS.md` records the redacted files with pre/post sha256; the `MANIFEST-NOT-MIRRORED.md` appendix lists the in-mirror occurrences. No numeric claim is relied on below; integrity is enforced per-file against `MANIFEST.sha256` at stage time.
2. v2's optional T0 sanity (reproducing an archived round's candidates from its raw) was vacuous — round raw/candidates data is not mirrored. **Dropped** in v3; replaced by the smoke-vs-day-1 rule (§7).
3. Window-constant modularity: **proven for the designated chains** — day windows are module-level constants (`round33/raw/soccer_scan33.py:75-78` `LO/HI/DISC_LO/DISC_HI`; `scan_crypto_33.py:25` `OUT` + inline window in docstring; `round43/raw/scan43.py:73-78` path/window constants). The C2 wrapper's override surface is therefore exactly path+window globals.
4. Canonical SHA: now re-verified with network (`ls-remote`) *and* locally (ancestry + content diff) — no longer planner-only.
5. Missing round17/25 dirs: immaterial — C4 designates exactly one soccer chain and one crypto script (§3).

## 3. C4 — frozen-chain designation (grounded, named, verifiable)

**Designation (record in the node's DELIVERY_PLAN section at PR time):**

| Path L leg | Frozen scripts (mirror paths, sha256 per `server-mirror/MANIFEST.sha256`) | Library pin (each exists in local git) | Why this pin |
| --- | --- | --- | --- |
| Soccer chain | `round43/raw/enum43e.py`→`build_board43c.py`→`scan43.py` (staged: enum fetch → boards → scan; candidates via `write_candidates43a.py`/`write_candidates43e.py`) | **`1a2760bc`** | `scan43.py:36-40` header records "Frozen gates (library `1a2760bc`, selfcheck re-run this session pre-scan 2026-11-08T13:40Z, PASS 10/10)" — a historically coherent, selfcheck-verified (scripts, library) pair |
| Crypto chain | `round33/raw/scan_crypto_33.py` (the dispatch template's named reference implementation, `tools/scan_dispatch_template.md:34`) | **`19501719`** | r33 docstring `soccer_scan33.py:39-40` records the session pin + selfcheck 10/10 for the round-33 script set |

- **Pins are materially different** (read-only diffs: `19501719..1a2760bc` touches `soccer_model/gates/xg/risk_policy` (458+/56−); `1a2760bc..9e761317` touches `league_strength/xg` (149+/9−)). Therefore: **per-leg pins, never mixed**; the final server binding `9e761317` (`VERSION-CHECKIN.md:15-16`) is recorded as the loop's end-state inventory, not silently substituted into a round that never ran with it. Any deviation from these pins is a plan change requiring re-review.
- **Rerun-unchanged feasibility, per leg:** `scan43.py` already consumes persisted raw files (`scan43.py:73-78`: pointer sidecar, `events_window_43.json`, `espn_fixtures_43.json`, Eredivisie snapshot) and imports the library via `sys.path.insert` (`:57-58`); its remaining live reads are CLOB books in-stage. `scan_crypto_33.py` is a top-level monolith (library import `:16-21`, `OUT` constant `:25`, terminal `json.dump` `:431-432`). Both are executable unchanged given (a) the sandbox path layout, (b) a recording HTTP layer (§4 C2), (c) the pinned library on the inserted path.
- **Known non-comparable surfaces, pre-registered (never fake equality):** (i) the manual news-concordance layer (agent research, e.g. `round33/raw/write_candidates33.py:1-18`, r43 header "news concordance (one focused search per serious candidate)") — inputs **unknown/unloadable**; carried as provenance annotations from round docstrings, marked UNKNOWN in the diff, classified per §6; (ii) **basketball decisions do not exist in the frozen loop** (`round46/boards46.py:2-7`: "Basketball = NO MODEL"; NBA was shadow-only, `round47/shadow47.py:1-4`) — basketball markets are *captured with terms preserved* but have no Path L decision; they appear in the diff only as captured-not-compared rows; (iii) legacy ledger writes — excluded entirely (chain stops at scan/candidates JSON).

## 4. C1 + C2 — smallest concrete implementation (proposed node scope; independent lane)

**Existing entry points are insufficient, documented:** the papersim CLI (`selfcheck/schema/validate-dsn/evaluate/replay/run-scan`, `cli.py:886-1012`) is the managed pipeline only — nothing in-repo stages mirrored scripts, pins a historical library, redirects their I/O, or records their HTTP reads; `tools/*.py` are direct-exec research scripts without staging/pinning. Hence the minimal new files below. **No new configuration system, framework, dependency, DB module, or persistence layer**: day parameters are explicit JSON arguments to the operator tool (same pattern as `run-scan --config`, `cli.py:984-988`); persistence at run time uses only already-reviewed paths (`snapshots.register_snapshot`, `snapshots.py:196`; decisions via `run-scan` itself; `bankroll_daily_check`, `store_psycopg.py:579` — the latter once B4 wiring exposes it).

**Proposed files (this lane's exclusive write scope; nothing else is touched):**

| File | Responsibility (function-level contract) |
| --- | --- |
| `src/polymarket_alpha_lab/papersim/d1compare/__init__.py` | lazy, empty surface; imported only by the operator tool — CLI startup module count untouched (FX-20 budget, `tests/test_init_lazy_imports.py`) |
| `d1compare/journal.py` | capture-journal record types + reader/verifier: entries `(url, utc_fetched_at, request-meta-without-credentials, response-bytes, sha256)`; pure functions `verify_journal`, `journal_digest`; preserves **origin (URL), as-of (timestamp), executable odds (raw CLOB book levels, not midpoint-only — Engineering Defaults), terms verbatim (raw Gamma question/outcomes; basketball ML/spreads/totals OT-inclusion as stated by the market, carried never re-derived), league/model keys, sides, time (gameStartTime/expiry)** |
| `d1compare/packbuild.py` | pure `journal → pack` builder: constructs `EntryRequest` (`pipeline.py:304-350` field set) from journal raw, encodes via the existing codec `replay.replay_bundle_payload` (same pattern as `tests/test_papersim_scan.py:48,112`), emits pack records per `scan.py:245-283,341-388` contract incl. FX-26 `events` materials (`scan.py:220-241`), `book_snapshot_sha256`, `source_ref` = journal url+sha256; one provenance map per pack |
| `d1compare/frozenrun.py` | staging + execution env: copy designated mirror scripts, verify each against `MANIFEST.sha256`; materialize pinned libraries via `git archive <pin>` (no working-tree mutation, no network); present the hard-coded layout (`scan43.py`/`scan_crypto_33.py` constants) via rootless mount namespace (preferred, DELIVERY_PLAN §61) or recorded path/window-global shim (fallback; override surface = paths + `LO/HI/DISC_*` only); install the **recording urllib layer** (records every network read into the day's journal — this is what makes ONE canonical contemporaneous capture feed both paths); fixed env (TZ UTC, `PYTHONHASHSEED`), library-archive sha256 recorded; zero DB, zero credentials, egress allow-list = public API hosts used by the scripts |
| `d1compare/diff.py` | candidate-by-candidate diff: join keys = market identity + side + kind; field deltas (decision, side, stake/size, gate outcomes, quotes, snapshot digests, as-of); emits the three fixed classes (§6) + pre-registered items; missing/unknown → explicit UNKNOWN rows — **never fabricate comparator equality** |
| `tools/d1_compare.py` | operator entry (precedent: `tools/wp_*.py`): subcommands `stage` (verify+stage frozen bytes+libraries), `run-frozen` (execute Path L, produce journal+artifacts), `build-packs`, `run-new` (invoke existing `run-scan` on built packs), `diff`; writes nothing to any DB (PG registration happens through `run-scan`/`register_snapshot` inside `run-new`/`run-frozen` post-pass steps, only when runs are authorized) |
| `tests/test_papersim_d1compare_journal.py`, `..._packbuild.py`, `..._diff.py` | offline tests over synthetic journals/fixtures — **synthetic tests are code verification only; no synthetic or historical cycle ever counts as real operations** |

**Lane non-overlap (verified against canonical `fa01be0b`):** FX-35 owns `evaluation.py`; B2/B3/B4 own scan-orchestration/store surfaces; MO lanes own `optimize/*`; DELIVERY_PLAN registrations are appended one per node-PR, sequenced after in-flight registrations. This lane touches none of those files; its PR registers its own section.

## 5. Run gates (revised per the operating order — no redundant owner checks)

The owner's standing order **already authorizes** the D1 same-input paper comparison and D2 after seven true days plus scheme conditions. v2's G0 owner-confirmation of the comparator shape is therefore **removed** as redundant. The interval may start when, and only when:

1. B2 (incl. FX-34), B3 (incl. FX-29), B4 (incl. bankroll-check exposure) merged at the execution base;
2. the **G7 managed-write defect is fixed with authorized evidence**;
3. this lane's C1/C2/C4 code is merged after independent review (Iron Rule 4, VERDICT line) — reviews are agent duties under the standing grants (2026-10-07 ZCode-subagents override; 2026-09-12 self-review authorization), not owner checkpoints;
4. S1 (scheduler installation on the execution host) — the one genuinely out-of-scope operational action at interval start; S3′ (validated-snapshot copy) only if Understat regeneration fails; production historical import, real-account changes, and unscoped server jobs remain under their separate limits and are **not** preconditions.

**Smoke vs day 1:** an initial technical smoke (first live wiring cycle) is distinct from interval start. A real complete prospective day counts as **day 1** iff every registered condition held from the beginning of that day (slots, receipts, identity, capture registration, diff artifact); a day with any unmet condition is the smoke (or a failure), recorded as such, and the interval starts with the next fully-conforming day. No backdating, ever.

## 6. Difference classes (candidate-by-candidate), cash identities, receipts, 7-day recording

- **Classes (fixed three, `docs/handoffs/2026-11-12-audit/04-development-plan.md:138-142`):** (1) library defect — fix new code, day count restarts (code_commit changes); (2) frozen-script defect / legacy ad-hoc drift — new code authoritative, historical impact recorded; (3) intentional rule difference — owner decides (pre-registered: FX-26 event keys, provider-registration scan params `scan.py:17-28`, manual news layer absent in new code, basketball-not-modeled). Never relax gates to align. UNKNOWN-input rows (news layer) are annotated with provenance and never scored as agreement.
- **Cash identities:** Path N — daily four-way `bankroll_daily_check` (tolerance `0.005`, `store.py:61-62`; day+commit+watermark key, `store.py:815-821`), receipt-only. Path L — structurally cashless (zero DB writes; chain stops at JSON); daily check = zero-write confirmation + arithmetic self-consistency of its artifacts. No invented legacy cash invariant; frozen legacy balances untouched.
- **Expected schedule slots per UTC day:** 00:05 capture (Path L frozen run with recorder) → pack build → `run-scan` (Path N) → marks/settle per B2/B3 cadence (close marks pre-kickoff, FX-34) → 23:50 report + `bankroll_daily_check` (`as_of` 23:59:59Z). **Real receipt checklist per day:** every slot has `command_run_events` STARTED/SUCCEEDED (stable `--operation-key`, attempts recorded, `scan.py:793-811`), one passing `bankroll_checks` row, journal sha256 + pack sha256 + provenance map registered, diff artifact emitted, env allow-list clean (no legacy DSN, no credential files), egress log limited to public API hosts. Incomplete days extend the window beyond seven.
- **7-day start recording:** an interval-start entry registered *before day 1 closes*: execution-base commit, per-leg frozen pins + `MANIFEST.sha256` digests + library-archive sha256s, day-1 slot schedule, smoke/disqualification record if any. Seven consecutive complete forward UTC days ⇒ D1 exit review (Iron Rule 4) ⇒ D2 per v2 §4 (unchanged: FX-35 wired, 30-day standard-F clock from cutover, MO-7/8/9 future evidence only).

## 7. Honest gaps under the signed scope

1. **G7 hold blocks everything operational** — this prep delivers code + tests only; even capture-byte registration waits for the fix's authorized evidence. 2. **B2/B3/B4 unmerged** — marks/settle/report/bankroll slots cannot exist yet; no run. 3. **C3 data gap stands** — rated-xG snapshot data is not in-repo (`MANIFEST-NOT-MIRRORED.md:8,24`); regeneration via the mirrored bounded tool, S3′ fallback otherwise. 4. **No historical raw exists in-repo to self-verify the harness** — first live cycle doubles as wiring proof under §5's smoke rule. 5. **Basketball has no frozen decision logic** — captured-not-compared, recorded, never faked. 6. All proposed files are **proposals** — authorization accrues only through this node's plan review → implementation → independent review → PR, per the standing process.

**Not doing (unchanged from v2 §6, plus this prep's specifics):** no managed writes, timers, paper operations, network captures, or DB activity; no old-ledger/credential touches; no edits to frozen scripts, mirror, handoff directories, migrations, V1 source, or other lanes' files; no new deps/frameworks/config systems/persistence; no synthetic or historical cycle counted as operations; no redundant owner re-confirmations; no backdating.

**Plan version: v3.0 — final.** Ready for this node's independent plan review; implementation only after that PASS, and interval start only per §5.
