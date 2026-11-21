# Model-Layer Review — 2026-11-07 (codex gpt-6-astra/max)

**Provenance**: server codex-cli 0.166.0, model `gpt-6-astra`, reasoning effort
`max`, `--sandbox read-only`, run on ubuntu@SERVER_HOST_REDACTED against repo commit
`5bdae2e3`. Owner authorization (verbatim): "调用服务器codex审核项目，特别是
模型，是否可以优化。指定模型为gpt-6-astra/max。审核好以后，你按照审核意见
修改，然后服务器本地和仓库同步更新到最新。"

**Artifacts**: brief `~/paper-sim/review/model_review_brief_20261107.md`; raw
output `~/paper-sim/review/model_review_20261107_out.md` (copied verbatim below);
stderr log `model_review_20261107_err.log`. 302,191 tokens consumed. Reviewer
environment note: shell commands failed under its sandbox
(`bwrap: loopback: Failed RTM_NEWADDR`), so SHA verification / test runs / PG
inspection were not independently performed by the reviewer; ledger evidence was
treated as supplied. The coordinator verified SHA and ran tests separately.

**Verdict line**: `REVIEW COMPLETE: 9 findings (0 P0, 4 P1, 5 P2, 0 P3)`.

---

# Model-Layer Review

**Executive summary**

- The Eredivisie and Championship validation results supplied in the brief show a repeated failure pattern: gates A–D pass, gate E fails at 6.40c versus the 6c bar.
- The promoted-team fallback is a real source of artificial variant spread. The proposed `k=8` shrink is plausible to test, but not established as the correct value.
- Keep the 6c spread bar and all other frozen thresholds for now. Do not tune them to clear the failing cohorts.
- The 10 variants are a parameter-sensitivity envelope, not ten independent model estimates; their current spread is useful as a conservative screen but not a calibrated uncertainty interval.
- Main implementation risks are inconsistent fee/haircut order across gate APIs, weakly enforced cross-league match-winner requirements, and discarded prior-season ratings when current-season history is empty.
- The two crypto drift conventions bound drift assumptions, but do not capture volatility-forecast or jump risk; terminal-market direction also needs to be explicit at the API boundary.
- No files were changed. CodeGraph source was reviewed read-only; shell commands failed with `bwrap: loopback: Failed RTM_NEWADDR`, so I could not verify the checkout SHA, run tests, or independently inspect the private validation, performance, or PostgreSQL ledger evidence. Those figures below are treated as supplied by the owner.

**Findings**

1. **[P1] Promoted-team fallback creates misleading variant disagreement.**
   **Evidence:** [`xg.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/xg.py:371) uses raw current rates in `cur` mode and league-average rates for missing last-season data in `last` mode. The blend also substitutes league average for missing prior rates at lines 374–382. The gate measures the full min-to-max range at [`gates.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:581). The supplied round-39 analysis reports 256/560 Eredivisie rows failing spread alone, including established teams; the analogous promoted-team diagnosis is therefore not a complete explanation of the league-wide failures.
   **Recommendation:** Do not ship a special spread-statistic exclusion for no-prior fixtures: it would hide disagreement between a current-form estimate and a league-prior estimate. Do not declare `k=8` correct from the reported analysis. Implement a coherent current-rate shrink toward the league prior, then select `k` by nested, time-ordered validation on historical promotions and sparse-history teams. Avoid injecting the same current observations into both the current and fallback-last components. Do not block a promoted team merely for lacking same-league prior data if usable current xG exists; block only when neither a defensible current estimate nor a prior is available. Revalidate gates A–E before changing accepted fixtures.

2. **[P2] The 10-variant set is a sensitivity envelope, not calibrated model uncertainty.**
   **Evidence:** [`soccer_model.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/soccer_model.py:127) defines six blend configurations, current-only, last-only, and two shrink exponents. The `0.8` and `1.1` values are shrink exponents, not blend weights. [`candidate_from_variants`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/soccer_model.py:482) reports the median and full min/max; [`gates.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:604) uses that range for the 6c gate. The variants are correlated parameter perturbations, with unequal representation of model dimensions.
   **Recommendation:** Keep the median and full range for now; replacing the median with a trimmed mean or excluding last-only to pass the spread bar would be an unvalidated change. In WP-P4, define plausible parameter families and assess whether their spread predicts out-of-time error. Any change to variant membership, aggregation, or the 6c statistic requires A–E revalidation.

3. **[P1] Gate-E failure needs model recalibration work, not a lower market-distance bar.**
   **Evidence:** The supplied round-39 Eredivisie validation reports gate E at 6.40c, and the brief says Championship failed E with the same A–D-pass pattern. [`soccer_model.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/soccer_model.py:94) fixes Dixon–Coles rho at `-0.1`; only one sweep variant uses it at lines 127–138. [`xg.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/xg.py:335) uses the prior season’s league goal fit, while team rates are simple season-history averages at lines 356–383.
   **Recommendation:** Keep 6c as the promotion bar until the model changes. For WP-P4, fit recency-weighted league home/away scoring levels and team attack/defense rates with hierarchical shrinkage; fit low-score dependence from score outcomes rather than O/U outcomes. Tune decay and shrinkage only inside expanding-window historical validation. Refit after completed matchweeks, with minimum-data rules and league/global fallback priors. Shadow prospective predictions for at least two matchweeks and a predeclared out-of-time sample; require E to pass without regressions in A–D before any acceptance or sizing policy changes.

4. **[P1] Cross-league match-winner requirements are descriptive, not an invariant of these APIs.**
   **Evidence:** [`gates.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:117) says bridged cross-league MW needs an explicit bridge, worst-variant edge of at least 5c, and $150 sizing. But [`suspension_policy`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:437) returns `eligible=True` for the market kind without checking bridge or edge, and [`evaluate_candidate`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:490) has no cross-league or market-kind input. [`sizing_tier`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/risk_policy.py:241) has a domestic-MW tier but no cross-league MW class. The brief says the current paper cohort is already bridged and sized at $150; enforcement may exist in an external caller I could not inspect.
   **Recommendation:** Make the bridge-present, worst-edge-at-least-5c, and $150 conditions explicit in the candidate policy/sizing contract, or add a caller-level regression test proving all three are enforced before entry. Preserve the current bridge formula and T4 accumulation pending more settled observations. This needs policy and bridge replay; the code change alone does not establish new model calibration.

5. **[P1] Fee and coin-flip haircut order differs between gate APIs.**
   **Evidence:** [`evaluate_candidate`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:596) subtracts the fee and then applies the haircut at line 600. [`evaluate_gates`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:671) documents the caller applying the haircut first, then subtracts fees at lines 715–717. Tests exercise the fee-plus-haircut behavior through `evaluate_candidate` at [`test_papersim_gates.py`](/home/ubuntu/polymarket-alpha-lab/tests/test_papersim_gates.py:403), but the two entry points do not share one arithmetic contract. At a 50c entry and 5% fee, reversing fee and a 0.5 haircut changes the result by up to 0.625c, enough to flip near-bar decisions.
   **Recommendation:** Centralize fee, haircut, and rounding order in one helper and make both APIs call it. Add parity tests at the haircut boundaries and near the 5c / 8c bars. Re-run gate arithmetic and affected acceptance evidence; if any pass/fail cohort changes, refresh the relevant WP-P3 gate results.

6. **[P2] Returning teams with empty current history lose their prior-season rating.**
   **Evidence:** [`ratings_for`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/xg.py:356) skips teams with empty current-season histories, so it never emits their `last` or blended rating. `pair_lambdas` then defaults missing entries to `(1.0, 1.0)` at lines 627–628. Yet the sweep includes a last-only variant at [`soccer_model.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/soccer_model.py:134). This makes pre-season and early-season returning teams effectively league average despite available prior data.
   **Recommendation:** Build ratings for roster teams with no current rows: use prior-season rates for last-only, and the prior plus current evidence available for blend. Keep current-only behavior explicit as a baseline or shrink it deliberately. Add an early-season fixture test and revalidate A–E because forecasts and variant ranges will change.

7. **[P2] Crypto conservatism covers drift convention, not volatility or tail uncertainty; terminal side mapping is ambiguous.**
   **Evidence:** [`crypto_model.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/crypto_model.py:45) fixes `SIGNATURE_WINDOW` at 30d. [`realized_vol_daily`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/crypto_model.py:140) uses uncentered RMS returns, while `conservative_edges` only takes the low/high values across drift conventions at lines 274–300. `TerminalProbability` is explicitly `P(S_T <= barrier)` at lines 109–115, but `conservative_edges` treats its inputs as YES probabilities; an above-barrier YES market must be complemented by the caller. The supplied ETH example has a 0.589 model probability versus 0.445 market probability, a 14.4c gap.
   **Recommendation:** Keep the current A/B conservative orientation when the probability is already for the market’s YES event. Add an explicit terminal direction input or a named transform, with above- and below-market tests. Shadow 7d/30d/60d/87d and a volatility forecast on timestamped outcomes, stratified by horizon and barrier distance; assess jump/regime errors before changing the 30d signature. This requires crypto-specific walk-forward validation, not soccer A–E.

8. **[P2] FotMob xG values are not checked for finite, nonnegative numeric content.**
   **Evidence:** [`fotmob.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/fotmob.py:336) accepts any non-null xG strings; `_build_block` converts them with `float()` at lines 405–444 without finite/range checks. Values such as `"NaN"` or `"inf"` can enter the snapshot. Downstream finite checking exists in [`gates.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:553), but that is later than model construction.
   **Recommendation:** Reject non-finite and negative xG at the adapter boundary, with a clear schema error; add focused tests. This is safe without A–E revalidation if it only rejects malformed data and leaves valid snapshots byte-identical. The supplied Eredivisie result should also be reconciled with FotMob’s stale adapter-provenance comment at [`fotmob.py`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/fotmob.py:117), which says Eredivisie had not yet been run through the adapter.

9. **[P2] The settled record does not support changing global thresholds, and the largest edge needs a predeclared cohort check.**
   **Evidence:** The brief reports +31.6% aggregate ROI over 53+ settled paper bets, but only 6 settled MW bets (2W–4L), 5 BTTS (2W–3L), and 7 team-total bets. It also reports a 18.7c net Le Mans–Monaco MW NO edge. [`rank_candidates`](/home/ubuntu/polymarket-alpha-lab/src/polymarket_alpha_lab/papersim/gates.py:638) orders survivors by worst-variant margin, then haircut-adjusted execution edge; it does not make the overall ROI comparable across market-kind and league cohorts.
   **Recommendation:** Keep net exec ≥5c, worst ≥4c, spread ≤6c, and liquidity ≥$5k frozen. Keep MW deprioritized and its reduced sizing/observation policy; do not infer BTTS failure or success from five bets. Use the existing ledger to predeclare and review edge buckets by market kind, league, side, bridge status, and model version; inspect calibration and closing-line movement alongside returns. For the 18.7c signal, verify the probability mapping and variant range, then retain it as a paper observation rather than relaxing gates.

**Ranked implementation list**

Ranked by expected value per engineering hour; estimates are rough. “A–E: no” means the change should still get focused regression tests, but should not alter valid forecast outputs or the soccer validation gates.

| Rank | Change | Estimated effort | A–E revalidation |
|---|---|---:|---|
| 1 | Unify fee/haircut arithmetic across gate APIs and add boundary parity tests. | 2–4 h | Yes: gate arithmetic and affected acceptance evidence. |
| 2 | Add finite/nonnegative xG validation to the FotMob adapter. | 1–2 h | No, if valid snapshots remain unchanged. |
| 3 | Analyze the existing ledger by predeclared market/league/bridge/edge cohorts; stress-test the 18.7c case. | 2–4 h | No; read-only analysis. |
| 4 | Make cross-league MW bridge, +5c worst-edge, and $150 sizing requirements enforceable or caller-tested. | 2–4 h | No full A–E; rerun bridge and policy acceptance checks. |
| 5 | Preserve prior-season ratings for roster teams with empty current-season history. | 2–3 h | Yes. |
| 6 | Evaluate promoted-team shrinkage, including `k=8`, using historical promotion cohorts; retain the full spread screen. | 4–8 h | Yes. |
| 7 | Rework the variant envelope or spread statistic only after validating predictive coverage. | 8–16 h | Yes. |
| 8 | Develop and shadow WP-P4 recency, home-advantage, and low-score recalibration. | 12–20 h | Yes, including A–D regression checks and E promotion evidence. |
| 9 | Shadow multi-window/forecast volatility and explicit terminal-market direction handling in crypto. | 6–12 h | No soccer A–E; require crypto-specific walk-forward validation. |

REVIEW COMPLETE: 9 findings (0 P0, 4 P1, 5 P2, 0 P3)
