# Paper-sim core library usage (WP-P1)

`polymarket_alpha_lab.papersim` is the migrated, tested core of the
paper-sim system (soccer lineage `r5c -> r15 -> r23`, crypto lineage
`r19`). Everything in it is **paper-only / report-only / readonly**: it
models probabilities, gates paper entries, and builds reports. It never
authenticates accounts, touches credentials, or signs/submits orders.

## CLI

Run inside the project virtualenv from the repository root:

```bash
.venv/bin/python -m polymarket_alpha_lab.papersim.cli selfcheck
.venv/bin/python -m polymarket_alpha_lab.papersim.cli schema
.venv/bin/python -m polymarket_alpha_lab.papersim.cli validate-dsn
```

- **selfcheck** — the invariant suite. Imports the package surface and
  recomputes every frozen regression anchor through the real
  implementations: the three r19 crypto touch anchors (ETH-2000 /
  ETH-2250 / BTC-65k), the four r23 soccer anchors over the ten sweep
  variants (BRE-FOR / HUL-IPS / CHE-MUN / BOU-LEE), the banned
  cross-league path (HOF-LYO must yield `None`), and the #48
  gate-arithmetic record. Prints one `[PASS]`/`[FAIL]` line per check
  and exits 0 only when all ten pass. No network, no database.
  Run it after any change to the package or its fixtures.
- **schema** — prints the ledger migration DDL verbatim (byte-identical
  to `supabase/migrations/20261101000000_papersim_ledger.sql`).
  Descriptive evidence only; applying migrations is the exclusive job of
  the transactional native-Postgres migration ledger, never this
  command.
- **validate-dsn** — report-only validation of `PAPERSIM_PG_DSN`
  through the single audited `validate_local_postgres_dsn`. It never
  opens a connection, never reads the password file, and never prints
  the DSN value.

## International soccer inputs and totals research

`intl_data` refreshes the history CSV, validates the Elo directories, and
adds recent fixtures from `latest.tsv`. Use an explicit cutoff when
replaying a historical window. The default cache lifetime is one hour.

```bash
research_dir=$(mktemp -d)
source_dir="$research_dir/sources"
snapshot="$research_dir/snapshot.json"
evidence="$research_dir/refresh-evidence.json"
cutoff=$(date -u +%F)
.venv/bin/python -m polymarket_alpha_lab.papersim.intl_data \
  --cache-dir "$source_dir" --before-date "$cutoff" \
  --out "$snapshot" --evidence-json > "$evidence"
```

For offline replay, pass `--source-dir` with `results.csv`, `World.tsv`,
`latest.tsv`, `en.teams.tsv`, and `en.tournaments.tsv`. Use `--results-csv`
if the seed CSV is elsewhere. Repeat `--espn-scoreboard` with frozen
scoreboard responses to verify team scores. With those responses,
`--exclude-late-espn-kickoffs` also removes supplementary fixtures whose
known UTC kickoff is on or after the cutoff. For included fixtures with
verified kickoffs, that option uses the later of the original fixture
date and the UTC kickoff date for modelling. Other fixtures use the
source fixture date. Day-precision inputs lack a final-whistle timestamp.
The evidence records source digests, coverage, overlap conflicts, and any
independent score checks. Existing two-file callers of `build_snapshot`
keep their historical input mode. For full validation of a refreshed
snapshot, retain the JSON output from `--evidence-json` and pass it with
the same cached or frozen sources.

```bash
.venv/bin/python tools/wp_intl_validate.py validate \
  --snapshot "$snapshot" --cache-dir "$source_dir" \
  --refresh-evidence "$evidence" --out-dir "$research_dir/validation" \
  --revision "$(git rev-parse HEAD)" --skip-pm
```

If the snapshot was built with `--results-csv` or `--espn-scoreboard`, pass
the corresponding source files to the validation command. It verifies
the digests and uses the cutoff from the evidence.

Compare the causal tournament mean with the fixture-specific shadow model
using the same matches and cutoff. The report includes Brier score, log
loss, coverage, and fallback counts. The fixed Elo slope was fitted on
data that overlaps the example evaluation window.

```bash
.venv/bin/python tools/wp_intl_validate.py totals-replay \
  --snapshot "$snapshot" --date-lo 2024-01-01 --date-hi 2026-11-17
```

For a market-reference comparison, use the normalized fixture, rules, and
two-sided quote evidence described by `soccer_totals --help`. The command
walks the order book for the requested stake and reports the fee-adjusted
price difference plus any missing or incompatible evidence. A complete
comparison needs a Gamma snapshot from the preceding five minutes that
shows an active market accepting orders, and timestamped, equivalent
rules and quotes from the other source.

```bash
.venv/bin/python -m polymarket_alpha_lab.papersim.adapters.soccer_totals evidence.json
```

The [paper-sim blueprint](blueprint.md) describes the remaining statistical
and operational validation.

## How round scripts adopt the library incrementally

The library replaces the per-round bespoke scripts one function at a
time; a round script can migrate one stage per round without big-bang
rewrites:

1. **Load xG data**: `xg.load_xg_snapshot(path)` reads a pre-fetched
   Understat snapshot. Fetching runs through explicit public data
   adapters. Loaders consume the supplied snapshot without network access.
2. **Price a soccer fixture**: `soccer_model.fixture_variants(snapshot,
   home, home_league, away, away_league)` evaluates all ten sweep
   variants (or returns `None` when the fixture is refused — see the
   guard below); `soccer_model.candidate_from_variants(...)` builds a
   candidate quote; `xg.recent_form(...)` adds form context. For a
   cross-league fixture (UCL/UEL style), pass the explicit
   `bridge=` κ table — see "The cross-league bridge path" below.
3. **Price a crypto barrier**: `crypto_model.realized_vol_windows(
   closes)` builds the 7/30/60/87d vol table from complete 6h bars,
   then `crypto_model.touch_probability(...)` /
   `terminal_probability(...)` give the A/B convention pair and
   `conservative_edges(...)` the per-side conservative fair values.
4. **Gate candidates**: `gates.walk_book(levels, "ask")` walks the $250
   VWAP, `gates.evaluate_candidate(quote, book, ...)` applies the full
   frozen gate set (spread/worst/exec/liq/fill/entry-window/news) with
   the exec bar evaluated on the fee-NET edge (see "Trading-fee model"
   below), `gates.suspension_policy(kind, cross_league=...)` answers
   market-kind eligibility, `gates.rank_candidates(...)` orders the
   survivors.
5. **Size and settle**: `risk_policy.sizing_tier(...)` /
   `paper_stake(...)`, `team_cap_status(...)`,
   `held_fixture_add_allowed(...)`; `ledger.settle_natural(...)` /
   `settle_exit(...)` realize outcomes, `bankroll_identity(...)`
   checks the cash identity.

## Trading-fee model and the net-edge exec bar

The paper fee estimate is `shares * rate * p * (1 - p)` on
a fill at price `p` — `rate` is 7% on crypto markets
(`gates.FEE_RATE_CRYPTO`) and 5% on sports markets
(`gates.FEE_RATE_SPORTS`). Per share bought, that is a fee cost of
`gates.fee_cost(entry_price, fee_rate) = rate * p * (1 - p)`, maximal
at `p = 0.5` (1.25c per share at the sports rate, 1.75c at the crypto
rate).

The 2026-11-05 fee-accounting fix made the frozen exec bar a NET bar
(the review found the old bar compared the GROSS executable edge, so
some 5-6c gross "passing" candidates were really 3.75-4.75c net):

- `evaluate_candidate(..., fee_rate=...)` (default `FEE_RATE_SPORTS`;
  crypto candidates pass `FEE_RATE_CRYPTO`) subtracts the per-share
  fee at the walked entry VWAP from the executable edge BEFORE the
  +5c exec bar (and before the +8c under_risk bar). The 50-50
  cancellation haircut then acts on the NET edge, and
  `GateReport.exec_edge_haircut` (the ranking tiebreak) is net-based.
- `GateReport.exec_edge_gross` preserves the raw
  `model_median - vwap250` edge; `GateReport.exec_edge` is the net
  edge. `worst_variant_edge` stays gross — it is a model-agreement
  margin, not an executable-edge bar.
- The frozen threshold VALUES (var <= 6c, worst >= +4c, exec >= +5c,
  liq >= $5k) are unchanged — only the edge definition moved from
  gross to net. Passing `fee_rate=0.0` restores the gross comparison
  (useful for replaying frozen gross records).
- `evaluate_gates(...)` keeps its as-supplied comparison when called
  without fee parameters (callers apply the fee/haircut first, the
  same convention as the haircut); passing the optional
  `fee_rate=...` + `entry_price=...` pair makes it net its supplied
  GROSS `median_exec_edge` exactly like `evaluate_candidate` does.
- Worked example (#48 Brentford-Forest O2.5): entry VWAP 0.560,
  exec edge +7.42c gross; sports fee 0.05 * 0.56 * 0.44 = 1.23c ->
  +6.19c net, still clearing the +5c bar. A 5.5c gross edge at a
  p=0.5 entry, by contrast, nets 4.25c and now fails.

**The cross-league guard is relaxable ONLY via the explicit bridge
parameter**: `match_lambdas` / `fixture_variants` /
`candidate_from_variants` return `None` whenever the two clubs' xG
ratings come from different leagues AND no `bridge=` κ table was
passed. The HOF-LYO favorite-flip came from bespoke scripts bypassing
exactly this guard (`review/FIXES.md`, 2026-10-30). Round scripts must
treat `None` as "not modelable", never re-derive cross-league lambdas
themselves. There is no other relaxation path: no config flag, no
environment variable, no default bridge.

Note (2026-11-12): cross-league **match_winner is conditionally
eligible** — the WP-P5 two-season walk-forward passed (§75): a bridged
cross-league MW entry requires `gates.check_cross_league_mw`
(bridge present AND worst-variant edge >= +5c) and is sized via
`risk_policy.CROSS_LEAGUE_ML_CLASS` at a fixed $150. The bridge
re-enables totals/BTTS-style
channels through the guarded modeling path.

## FotMob Championship snapshots (WP-P3)

`papersim.fotmob` adapts FotMob's public unofficial JSON API (no key,
no credentials) into the same Understat snapshot wire shape
`xg.load_xg_snapshot` consumes. It exists for domestic leagues OUTSIDE
Understat's Big-5 coverage: the EFL Championship is the validated
source (league key `"Championship"`, `xg.DOMESTIC_LEAGUES` documents
the known keys); Eredivisie was validated 2026-11-07 (§71: gates A-D pass, E fail ->
totals-family enterable).

```python
from polymarket_alpha_lab.papersim import fotmob

fixtures = fotmob.fetch_fotmob_league(fotmob.FotMobLeague.CHAMPIONSHIP)  # current season
prior = fotmob.fetch_fotmob_league(48, "2025/2026")                      # explicit season
details = {mid: fotmob.fetch_match_details(mid) for mid in kept_match_ids}
block = fotmob.fotmob_to_understat(
    {"fixtures": prior, "details": details},
    fotmob.FotMobLeague.CHAMPIONSHIP, "2025",
)
snapshot = {}  # merge blocks season by season, then model via xg/soccer_model
```

- **Rate limit**: both fetch functions share one module-wide
  1 request / 1.3 s guard (`SLEEP_S`), matching the Phase 1 fetch
  policy. FotMob's API is unofficial — keep the spacing and expect no
  stability guarantees.
- **Fail-closed**: `fotmob_to_understat` refuses to emit a block on
  season drift, malformed fixture rows, a team table that does not
  match the league size, or an xG skip rate above 5%
  (`FotMobSchemaError`); nothing is returned.
- **Kept matches**: numeric league rounds only (playoffs excluded),
  finished, not cancelled, with an xG row — identical to the Phase 1
  adapter filters. Per-match gaps are skipped and counted, bounded by
  the skip-rate ceiling.
- **Pure loaders**: `fotmob.load_fotmob_snapshot(path)` validates the
  block structure (`teams` + `dates`, xG on every dates row) and never
  touches the network.
- Championship is DOMESTIC: `pair_lambdas` / `fixture_variants` treat
  `home_league == away_league == "Championship"` pairs through the
  standard 10-variant path; the cross-league guard applies unchanged
  (a Championship-vs-EPL fixture still yields `None` without a
  bridge).

### Validation status and the known 1X2 limitation

WP-P3 Phase 1 validation (2026-11-03, report
`paper-sim/round31/fotmob_validation_report.md`): gates A-D PASS
(schema/coverage, league goal fit, xG-goal correlation, walk-forward
backtest vs a league-average baseline); gate E (market comparison)
FAIL — 5 of 12 fixtures flipped the 1X2 favorite, because teams new to
the snapshot season (relegated/promoted sides) fall back to
league-average multipliers and the model compresses strong away
favorites. Until that is fixed, Championship snapshots are sanctioned
for **O/U totals, BTTS and team totals only**; 1X2 stays deprioritized
under the frozen domestic match_winner policy (and cross-league MW follows the §75 conditional path above).

On this host `www.fotmob.com` DNS is pinned: for actual backfills use
the server-side Phase 1 tool (`paper-sim/tools/fotmob_adapter.py`:
DoH + `curl --resolve`, resumable on-disk matchDetails cache). The
library fetch functions target environments with normal DNS
resolution.

## The cross-league bridge path (WP-P2a / WP-P5)

Domestic xG multipliers are measured relative to their OWN league's
average and are not cross-league comparable. Combining them raw is
what produced the HOF-LYO favorite flip. The sanctioned path threads
an explicit league-strength κ table through the modeling calls. Since
2026-11-10 the production table is frozen IN the module (WP-P5
two-season revalidation):

```python
from polymarket_alpha_lab.papersim import league_strength, soccer_model

# Path 0 — the shipped frozen production table (WP-P5, pre_2025_26):
table = league_strength.KAPPA

# Path 1 — flat JSON config file: {"bundesliga": 0.05, "ligue_1": 0.0}
table = league_strength.load_kappa_table("kappa.json")

# Path 2 — WP-P2b nested estimation report: the κ mapping lives under
# the report's "kappa" member, next to its own metadata; the loader
# extracts exactly that sub-object.
table = league_strength.load_kappa_report("kappa_report.json")

# Thread it: every soccer-modeling entry point takes bridge=.
variants = soccer_model.fixture_variants(
    snapshot, home, home_league, away, away_league, bridge=table.kappa
)
candidate = soccer_model.candidate_from_variants(
    snapshot, home, home_league, away, away_league, 2.5,
    "HOF vs LYO", "m1", 0.5, bridge=table.kappa,
)
```

What the bridge does: each side keeps its OWN league's base rates and
multipliers, and the pair is tilted by the clamped factor
`f = exp(clamp(kappa_home_league - kappa_away_league, ±ln 1.25))` —
home rate `* f`, away rate `/ f` — so the team from the stronger
(larger-κ) league is boosted on both sides of the ball. The clamp
bounds any κ error to a 1.25x displacement per side (edge ratio at
most `1.25² = 1.5625`); the κ table is now calibrated (WP-P5) and the
clamp is retained as frozen policy. Behavior details:

- Same-league fixtures are byte-identical with and without a bridge:
  the argument is never consulted when `home_league == away_league`.
- A league missing from the bridge still yields `None` (refuse the
  fixture; there is no default κ — `KAPPA` is an explicit constant
  you must pass, not an implicit default).
- κ values must be finite numbers; NaN/Inf/boolean κ raise
  `ValueError` (a NaN would otherwise leak through the clamp as a
  plausible 1.25 factor). Every table (loaded or shipped) is deeply
  immutable (read-only mapping; mutation raises `TypeError`).

**The shipped frozen table (WP-P5, 2026-11-10).**
`league_strength.KAPPA` holds the `pre_2025_26` freeze from the
canonical two-season run (values verbatim from
`tables.pre_2025_26.kappa` of
`~/paper-sim/review/wp_p5/two_season/sensitivity/canon_boot/two_season_report.json`,
sha 6ceadb6a…; decision memo `sensitivity/decision_memo.md`, owner
authorization 2026-11-10):

| league | κ |
|---|---|
| EPL | +0.021655 |
| La_liga | -0.071492 |
| Serie_A | -0.053532 |
| Bundesliga | -0.095116 |
| Ligue_1 | -0.182982 |
| nuisance | -0.300105 (fit cap) |

Provenance: two-season zero-placeholder refit on UCL/UEL FT matches
(2023/24 + 2024/25 FotMob backfill for club multipliers, 2025/26
Understat for p2b evaluation continuity); fit rule "obs kickoff day <
2025-09-16", leak-free, n_train=547; strict time-ordered T1-T5 gate
protocol with the formal G2 PASS on the canonical run; the frozen
tables are provider-invariant (byte-wise identical under the
mixed-source and all-FotMob source maps, max κ shift 0.000000);
bootstrap 95% CIs from B=300 match-resample refits (e.g. EPL
[-0.0658, +0.1737], Ligue_1 [-0.2798, -0.0411], full table in the
module comment). Re-freeze cadence: per the report's G2 rule the
table is frozen pre-season each year. Keys follow the capitalized
league-group convention of the production merged big-5 snapshot
blocks (`Bundesliga_2026`, `Ligue_1_2026`, ...); a lowercase
Understat-style league key does not resolve and the fixture is
refused.

The table it superseded (WP-P2b, in-sample n=835 fit, archived at
`~/paper-sim/round31/raw/league_strength_kappa.json`) stays importable
as `league_strength.SUPERSEDED_KAPPA_WP_P2B` for rollback provenance
only — never thread it as the production bridge.

**Away-side haircut 0.06 (WP-P5).** The away side is the known weak
joint of the bridged pair (2025-26 T2 fragile at 0.079, bootstrap p50
already above 0.08). The pre-registered corrector family `exp(-h)`,
h ∈ {0.06, 0.08, 0.10}, was selected at the mildest value: the
BRIDGED away λ is multiplied by `exp(-0.06)` (factor
`league_strength.AWAY_HAIRCUT_FACTOR` = 0.9417645335842487, helper
`league_strength.apply_away_haircut(lambda_away)`), exactly mirroring
the harness corrector (`ba * exp(lc_a - kd - haircut)`; the home side
is untouched). It applies to bridged cross-league away rates ONLY —
the same-league path never takes a haircut. Evidence: pooled T2
0.05069 → 0.01785 with bootstrap replicate pass 0.76 → 0.94; the
fragile 2025-26 season T2 0.07900 → 0.03828; cost 1.6% of the pooled
T1 edge (-0.00944 → -0.00929); no gate regression anywhere.

## DSN and password-file conventions

- Ledger persistence targets the project-private native PostgreSQL
  cluster only. `PAPERSIM_PG_DSN` (example documented default:
  `postgresql://paper_sim@127.0.0.1:55432/paper_sim`) is validated
  through the single audited `validate_local_postgres_dsn`; hosted
  hosts, sqlite and JSONL fallbacks are rejected. `ledger.papersim_dsn()`
  is the only sanctioned resolution path.
- The SCRAM password is **never** part of the DSN. It is supplied
  out-of-band via `PAPERSIM_PG_PASSWORD_FILE` — an environment variable
  holding the path of a one-line password file owned by the operator.
  There is no default path; the library never reads, logs or commits
  that file; only a connection adapter explicitly asked to connect may
  read it at connect time.

## What stays server-side

- Raw data fetching (Understat pages, Binance/Coinbase prints, CLOB
  books) stays in the external research tooling; the library consumes
  local snapshot files only, with one sanctioned exception: the FotMob
  fetch functions above (`papersim.fotmob`) are the package's only
  network surface. The Phase 1 FotMob adapter/validator tools remain
  the server-side backfill path for this host.
- The live `/home/ubuntu/paper-sim` round records remain the book of
  record for past rounds; the library is the canonical implementation
  new rounds copy from, not a retro-edit of history (r12/r13 boundary
  warnings included in the module docstrings).
- The live loopback `paper_sim` cluster (127.0.0.1:55432), its password
  file and any migration application stay with the server-side
  migration ledger; `schema` only mirrors the DDL as evidence.

Two ledger functions (`record_entry`, `mark_to_market`) remain frozen
stubs by design until the store-adapter commit lands; everything else
in the package surface is implemented and regression-anchored.

**Gate-safe candidate construction:** build gateable soccer candidates with `soccer_model.candidate_from_variants(...)` (snapshot-driven, carries the true ten-variant quotes). The pair-level `over_under_quote(...)` collapses sweep variance for O/U >= 2.0 and is NOT gate-safe — see its docstring warning.
