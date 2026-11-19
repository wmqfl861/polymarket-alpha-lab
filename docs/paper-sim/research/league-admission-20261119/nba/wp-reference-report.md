# WP-NBA-3 NBA model validation — walk-forward + market comparison

- generated_at (UTC): 2026-11-10T07:40:42.025266+00:00
- repo revision (both hosts): 1a2760bc (repo, both hosts); harness /tmp/wp_nba3 (uncommitted worktree copies of espn_nba.py/nba_model.py)
- snapshot: `/home/ubuntu/paper-sim/nba/snapshot_20261110.json` (sha256 eca00867165e9d64…)
- season 2026-27, 154 games, 2026-10-20..2026-11-09
- eligibility: >= 8 strictly-earlier games for BOTH teams; predictions from snapshots truncated at the game date (fully causal: ratings, sigma walk, league means, HCA, B2B fit)
- paper-only / report-only: no entries, no DB writes, no production changes; promotion NOT decided here

## Gate A — data integrity

- reload through `load_nba_snapshot`: pass (load_nba_snapshot accepted)
- teams 30, games 154, per-team games 9..12
- team-game possessions: min 89.2, mean 102.75, max 116.2; share in the 94-102 band: 0.360
- rebuild from retained raw cache byte-equal (teams+injuries): True
- **PASS**

## Walk-forward evaluation (gate D mirror)

- eligible & scored: **n = 30** (model refusals inside the eligible set: 0)
- home win rate: 0.500

| moneyline | Brier | log-loss | beats? |
|---|---|---|---|
| model (6-variant median) | 0.1638 | 0.4987 | — |
| coin flip (p=0.5) | 0.2500 | 0.6931 | Brier YES, LL YES |
| home always (p=1, LL clipped at 1e-12) | 0.5000 | 13.8155 | Brier YES, LL YES |

- totals log-loss at the evaluated line: **0.8375** (coin 0.5 at the same lines: 0.6931); n = 30, pushes excluded 0, over rate 0.433
- line sources: {'dk_closing': 30, 'model_median': 0} (ESPN-captured DK closing total)

## Gate B — goal/points fit (xG analog)

- Pearson r(exp_total, actual total) = 0.1158 (p = 5.45e-01), Spearman rho = 0.1133
- Pearson r(exp_margin, actual margin) = 0.4785 (p = 6.79e-03)
- mean |total error| 16.12 pts, mean |margin error| 13.70 pts
- bar: Pearson r(exp_total, actual) >= 0.25 with p < 0.05 and n >= 20
- **FAIL**

## Gate C — rating sanity vs league table

- Spearman rho(EWMA net rating, win%) = 0.9143 (p = 7.01e-16)
- Spearman rho(EWMA ORtg, win%) = 0.7977; rho(EWMA ORtg, actual net rtg) = 0.8634
- bar: Spearman rho(EWMA net rating, win%) >= 0.5 over 30 teams
- **PASS**

### ML reliability bins (walk-forward set)

| p_home bin | n | predicted mean | actual rate |
|---|---|---|---|
| [0.0,0.2) | 7 | 0.1191 | 0.1429 |
| [0.2,0.4) | 3 | 0.3086 | 0.3333 |
| [0.4,0.6) | 6 | 0.4996 | 0.1667 |
| [0.6,0.8) | 10 | 0.7185 | 0.8000 |
| [0.8,1.0) | 4 | 0.8677 | 1.0000 |

## Gate E — market comparison (DK closing devig)

- ESPN pickcenter DraftKings quotes captured post-final in the WP-NBA-1 backfill cache; treated as the closing line
- walk-forward set (n = 30): **MAD 0.1153** (11.5c), median 0.1182, p90 0.1655, max 0.4045; mean book vig 0.0412
- favorite flips: 5/30 (exact-0.5 ties 0)
- wider model-eligible set (>=1 prior, n = 138): MAD 0.2015 (20.1c), flips 39/138
- bar: MAD <= 0.06 (task wording; round-46 feasibility target 4-5c)
- **FAIL** (DK number above; PM closing below)

### Polymarket CLOB closing comparison (mapped ML markets)

- mapped: 10 moneyline markets; **PM-closing MAD vs model: 0.0881** (8.8c), median 0.0899, max 0.1487; flips 2/10
- PM closing vs DK devig MAD: 0.0114 (cross-book sanity)

| date | game | PM close home | DK devig home | model p_home | |model-PM| | volume $ |
|---|---|---|---|---|---|---|
| 2026-11-08 | MIA@SAC | 0.175 | 0.182 | 0.324 | 0.149 | 900,309 |
| 2026-11-08 | UTAH@MIN | 0.865 | 0.841 | 0.951 | 0.086 | 1,034,244 |
| 2026-11-09 | MEM@TOR | 0.475 | 0.478 | 0.506 | 0.031 | 2,526,217 |
| 2026-11-09 | SA@PHX | 0.235 | 0.233 | 0.145 | 0.090 | 1,276,722 |
| 2026-11-09 | DEN@HOU | 0.645 | 0.631 | 0.701 | 0.056 | 1,738,643 |
| 2026-11-09 | NO@GS | 0.825 | 0.811 | 0.755 | 0.070 | 1,134,587 |
| 2026-11-09 | WSH@DET | 0.785 | 0.773 | 0.817 | 0.032 | 1,052,959 |
| 2026-11-09 | IND@DAL | 0.625 | 0.631 | 0.767 | 0.142 | 1,559,097 |
| 2026-11-09 | CLE@CHA | 0.255 | 0.266 | 0.129 | 0.126 | 1,092,605 |
| 2026-11-09 | LAL@ATL | 0.545 | 0.566 | 0.445 | 0.100 | 2,733,311 |

## Variant-spread audit (6-variant envelope, soccer bar 6c)

- p_home spread: mean 0.0298 (3.0c), median 0.0272, p90 0.0469, max 0.0540; **fraction > 6c: 0.000**
- totals-quote spread: mean 0.0291 (2.9c), max 0.0575; fraction > 6c: 0.000
- exp_total spread (pts): mean 0.89, max 2.60
- spread driver: {'half_life': 8, 'sigma': 13, 'mixed': 9}

| min-prior bucket | n | p_home mean spread | p_home >6c | totals mean spread | totals >6c |
|---|---|---|---|---|---|
| 8-9 | 26 | 0.0286 | 0.000 | 0.0282 | 0.000 |
| 10-11 | 4 | 0.0373 | 0.000 | 0.0347 | 0.000 |
| 12-+ | 0 | — | — | — | — |

| time window | dates | n | p_home mean | >6c | totals mean | >6c |
|---|---|---|---|---|---|---|
| early_third | 2026-11-04..2026-11-05 | 2 | 0.0303 | 0.000 | 0.0288 | 0.000 |
| mid_third | 2026-11-06..2026-11-07 | 13 | 0.0297 | 0.000 | 0.0270 | 0.000 |
| late_third | 2026-11-08..2026-11-09 | 15 | 0.0298 | 0.000 | 0.0309 | 0.000 |

## Gate table

| gate | check | verdict | key numbers |
|---|---|---|---|
| A | data integrity (adapter-enforced; reload + cache rebuild) | **PASS** | 30 teams, 154 games, rebuild-equal True, poss 94-102 share 0.360 |
| B | goal/points fit: exp_total vs actual totals correlation | **FAIL** | r=0.1158 (p=5.5e-01), rho=0.1133, n=30 |
| C | rating sanity: EWMA ORtg/net vs league-table order | **PASS** | rho(net,win%)=0.9143, rho(ORtg,win%)=0.7977, 30 teams |
| D | walk-forward beats coin-flip AND home-always on BOTH Brier and log-loss (n>=20) | **PASS** | n=30: model Brier 0.1638 vs coin 0.2500/home 0.5000; LL 0.4987 vs 0.6931/13.8155; totals LL 0.8375 vs coin 0.6931 |
| E | market MAD <= 0.06 (DK closing devig; PM closing reported) | **FAIL** | DK MAD 0.1153 (11.5c) n=30, flips 5/30; PM closing MAD 0.0881 over 10 markets |

## Verdict notes (report only — promotion not decided here)

- Walk-forward n = 30 of 154 snapshot games meet the >= 8-prior bar; every other gate number inherits this small-sample caveat.
- Sigma walk: model used data-fit sigma on games whose truncated snapshot passed MIN_RESIDUAL_GAMES=10 residuals; residual counts per prediction are in results.json.
- Variant spread fraction > 6c: p_home 0.000, totals 0.000; early-season expectation is wide — recheck as games accumulate.
- Promotion is NOT decided by this harness: these are the coordinator's gate inputs.
- More data needed for a confident promotion decision: walk-forward n grows with each backfill; ~4-6 more team-games per team (2 weeks) roughly triples the eligible set, and PM mapped-market MAD should be re-pulled on the larger set.
