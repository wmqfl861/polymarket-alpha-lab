#!/usr/bin/env bash
# Read-only. Recomputes, from git objects only, the local (non-API) facts this review relies on.
# Nothing is checked out, nothing in the repository is executed, nothing is written outside <out_dir>.
#
#   bash collect_local.sh <repo_dir> <out_dir>
#
# <repo_dir> must have fetched: main, handoff/sports-runtime-20261123, handoff/sports-fx40-20261123,
# handoff/sports-fx44-20261123, handoff/sports-fx41-nba-v2-20261123 (as origin/* remote-tracking refs).
set -euo pipefail
repo="$1"; out="$2"; mkdir -p "$out"
g() { git -C "$repo" "$@"; }
gg() { git -C "$repo" grep "$@" || true; }   # a search with no match is a result, not an error
BASE=2bdc4cccacc925ea8e6569c8865b90c0b0c671e6
MAIN=ab6edff2d64ac8aa5c2831d748b8730b63f8312d
SPORTS=bcce1b17f7263ab89859febfcb56188c295ab371
FX40=7f92e0356888785e7b1e1bc88c24ffd1edbe8ac7
FX44=3bc5a234023ee0f322e693e380ed115eb6491547
FX41=256f8491c324b04e5dd9789c012ae84f654853f2
P=src/polymarket_alpha_lab/papersim

# 1. what landed on main
{
  echo "# first-parent merges $BASE..$MAIN (commit, committer date, subject) and their size"
  for m in $(g rev-list --first-parent --merges --reverse "$BASE..$MAIN"); do
    echo "$(g log -1 --format='%H %cI %s' "$m")"
    echo "    $(g diff --shortstat "$m^1" "$m")"
  done
  echo "# total: $(g rev-list --count "$BASE..$MAIN") commits; $(g diff --shortstat "$BASE" "$MAIN")"
  echo "# source and tool files changed:"
  g diff --numstat "$BASE" "$MAIN" -- src tools scripts | awk '{printf "    +%s -%s %s\n",$1,$2,$3}'
} > "$out/main_delta.txt"

# 2. does each real merge tree equal the PR head tree (i.e. the head already contained main)?
{
  echo "# merge commit | head (2nd parent) | head contains first parent | merge tree == head tree"
  for m in $(g rev-list --first-parent --merges --reverse "$BASE..$MAIN"); do
    p1=$(g rev-parse "$m^1"); h=$(g rev-parse "$m^2")
    contains=no; g merge-base --is-ancestor "$p1" "$h" && contains=yes
    same=no; [ "$(g rev-parse "$m^{tree}")" = "$(g rev-parse "$h^{tree}")" ] && same=yes
    echo "${m:0:8} | ${h:0:8} | $contains | $same | $(g log -1 --format=%s "$m" | cut -c1-60)"
  done
} > "$out/merge_tree_equivalence.txt"

# 3. frozen content untouched on main
{
  echo "# paths that must not change between $BASE and $MAIN; lines below this header = changed paths"
  g diff --name-status "$BASE" "$MAIN" -- tests/fixtures/papersim database supabase/migrations \
    "$P/gates.py" "$P/evaluation.py" "$P/pipeline.py" "$P/soccer_model.py" "$P/nba_model.py" \
    "$P/crypto_model.py" "$P/intl_model.py" "$P/fees.py" "$P/queue_policy.py"
  echo "# (end of list)"
} > "$out/frozen_content_check.txt"

# 4. where the P1 fixes live on main
{
  echo "# FX-38 / FX-39 step 1 / O-5 on main $MAIN (path:line: text)"
  gg -n -E '_PROMOTION_UNAVAILABLE|_PROMOTION_CONCLUSIONS|def _optimize_close_deviation|def _optimize_close_sample|def _optimize_close_prices|def _optimize_forward_comparison|pricing_spec_mismatch' "$MAIN" -- "$P/cli.py" | sed "s/^$MAIN://" | cut -c1-170
  gg -n -E 'class CloseDeviationSample|def _close_deviation_gap|def close_deviation_metrics|^CLOSE_DEVIATION_MIN' "$MAIN" -- "$P/optimize/policy.py" | sed "s/^$MAIN://" | cut -c1-170
  gg -n -E '^def test_native_clv|def test_apply_refuses_fresh_and_replayed_promotions|def test_apply_pending_confirm_is_unavailable_with_any_actor|def test_native_managed_cli_seals_and_replays_calibration_nonconvergence' "$MAIN" -- tests | sed "s/^$MAIN://" | cut -c1-170
  echo "# decide path still pairs by the registered active's model version:"
  gg -n 'priced_model_version=state\["active_model_version"\]' "$MAIN" -- "$P/cli.py" | sed "s/^$MAIN://" | cut -c1-170
} > "$out/p1_fix_locations.txt"

# 5. FX-47: list-form variant_quotes reaches a jsonb column unconverted
{
  echo "# main $MAIN"
  gg -n -E 'variant_quotes: str \| list|_require_json_list\(self.variant_quotes' "$MAIN" -- "$P/store.py" "$P/scan.py" | sed "s/^$MAIN://" | cut -c1-170
  gg -n -E 'variant_quotes=raw.get|variant_quotes=record.variant_quotes' "$MAIN" -- "$P/scan.py" | sed "s/^$MAIN://" | cut -c1-170
  gg -n -E 'candidate.median_probability, candidate.variant_quotes' "$MAIN" -- "$P/store_psycopg.py" | sed "s/^$MAIN://" | cut -c1-170
  gg -n -E 'variant_quotes jsonb' "$MAIN" -- database/papersim/migrations | sed "s/^$MAIN://" | cut -c1-170
  echo "# FX-40 draft $FX40 writes the list form into the market pack:"
  gg -n -E '"variant_quotes": (\[\]|list\()' "$FX40" -- "$P/prepare.py" | sed "s/^$FX40://" | cut -c1-170
  echo "# the local agent's own record of the failure:"
  gg -n '42846' "$MAIN" -- docs/paper-sim/design/fx-39/reviews/p1-4-baseline-reproduction.md | sed "s/^$MAIN://" | cut -c1-120
} > "$out/variant_quotes_trace.txt"

# 6. retrospective-review follow-ups still open on main
{
  echo "# MO-2 retrospective R-1: readers that still use a bare cursor (no conn.transaction()) on main"
  for fn in load_optimization_runs load_promotion_decisions _registry_controls; do
    n=$(gg -n "^def $fn" "$MAIN" -- "$P/store_psycopg.py" | sed "s/^$MAIN://" | cut -d: -f2)
    body=$(g show "$MAIN:$P/store_psycopg.py" | sed -n "${n},$((n+14))p")
    tx=no; printf '%s' "$body" | grep -q 'conn.transaction()' && tx=yes
    echo "$fn at line $n: wraps conn.transaction() within its first 15 lines: $tx"
  done
  echo "# lines in DELIVERY_PLAN.md on main that name these functions: $(gg -n -E 'load_optimization_runs|load_promotion_decisions|registry_freeze_state' "$MAIN" -- DELIVERY_PLAN.md | wc -l)"
  echo "# the managed connection is opened in psycopg's default mode (not autocommit):"
  gg -n -E 'connection = psycopg\.connect\(dsn\)' "$MAIN" -- "$P/store_psycopg.py" | sed "s/^$MAIN://" | sed 's/^/    /'
  echo "# call sites of the three readers on main (whether each sits inside a transaction block was read by hand, see the report):"
  gg -n -E '(load_optimization_runs|load_promotion_decisions|registry_freeze_state|_registry_controls)\(' "$MAIN" -- src | sed "s/^$MAIN://" | grep -v ':def ' | cut -c1-150 | sed 's/^/    /'
} > "$out/retrospective_followups.txt"

# 7. Python version declaration against 3.12-only syntax
{
  gg -n 'requires-python' "$MAIN" -- pyproject.toml | sed "s/^$MAIN://"
  f=src/polymarket_alpha_lab/research_strategy_resolution_probability_scenario_ladder_report.py
  echo "# multi-line replacement fields inside f-strings (need Python 3.12) in $f on main: $(g show "$MAIN:$f" | grep -cE 'f"[a-z_]+_\{[A-Za-z_]+\($' || true)"
  echo "# commit abcf67dc rewrote them for 3.11; commit 2fbcd0d8 restored the original bytes:"
  g log --full-history --no-merges --format='%h %cI %s' -3 "$MAIN" -- "$f"
} > "$out/python_version_check.txt"

# 8. the sports runtime branch: inventory and mechanical scans
R=docs/paper-sim/runtime
ls_py() { g ls-tree -r --name-only "$SPORTS" -- "$1" | grep '\.py$'; }
lines() { local n=0 f; for f in $(ls_py "$1"); do n=$((n + $(g show "$SPORTS:$f" | wc -l))); done; echo "$n"; }
{
  echo "# branch handoff/sports-runtime-20261123 at $SPORTS (parent $(g rev-parse "$SPORTS^"))"
  echo "files added against main: $(g diff --name-only "$MAIN" "$SPORTS" | wc -l); $(g diff --shortstat "$MAIN" "$SPORTS")"
  echo "scripts: $(ls_py $R/scripts | wc -l) Python files, $(lines $R/scripts) lines"
  echo "legacy:  $(ls_py $R/legacy | wc -l) Python files, $(lines $R/legacy) lines"
  echo "tests:   $(ls_py $R/tests | wc -l) Python files, $(lines $R/tests) lines"
  echo "systemd: $(g ls-tree -r --name-only "$SPORTS" -- $R/systemd | grep -c '\.service$') services, $(g ls-tree -r --name-only "$SPORTS" -- $R/systemd | grep -c '\.timer$') timers"
  echo "workflow files changed by the branch: $(g diff --name-only "$MAIN" "$SPORTS" -- .github | wc -l)"
  echo "DELIVERY_PLAN.md / AGENTS.md changed by the branch: $(g diff --name-only "$MAIN" "$SPORTS" -- DELIVERY_PLAN.md AGENTS.md | wc -l)"
  echo
  echo "# mentions of the runtime on main (football_notify|weixin|pal-football|pal-sports|sports_portfolio|herdr|hermes), files: $(gg -l -i -E 'football_notify|weixin|pal-football|pal-sports|sports_portfolio|herdr|hermes' "$MAIN" -- . | wc -l)"
  echo
  echo "# database access"
  echo "uses of the project's DSN validator (validate_local_postgres_dsn|local_postgres_dsn|project_postgres): $(gg -c -E 'validate_local_postgres_dsn|local_postgres_dsn|project_postgres' "$SPORTS" -- $R | wc -l) files"
  echo "direct psycopg.connect calls:"; gg -n 'psycopg.connect(' "$SPORTS" -- $R | sed "s/^$SPORTS://" | cut -c1-120
  echo "read-only use of the managed paper-sim database:"; gg -n 'connect_managed' "$SPORTS" -- $R | sed "s/^$SPORTS://" | cut -c1-140
  echo "tables created by code (CREATE TABLE ... in Python sources): $(gg -h -o -E 'CREATE TABLE( IF NOT EXISTS)? [A-Za-z_.]+' "$SPORTS" -- $R/scripts $R/legacy | awk '{print $NF}' | sort -u | wc -l)"
  gg -h -o -E 'CREATE TABLE( IF NOT EXISTS)? [A-Za-z_.]+' "$SPORTS" -- $R/scripts $R/legacy | awk '{print $NF}' | sort -u | sed 's/^/    /'
  echo "files containing DDL:"; gg -c -E 'CREATE (TABLE|SCHEMA|INDEX|UNIQUE INDEX)|ALTER TABLE' "$SPORTS" -- $R/scripts $R/legacy | sed "s/^$SPORTS://" | sed 's/^/    /'
  echo
  echo "# execution-path keywords (order placement, signing, wallets): matches outside comments/tests"
  gg -n -i -E 'private[_ ]?key|mnemonic|create_order|post_order|place_order|cancel_order|py_clob_client|ClobClient|api_secret|passphrase|eth_account|web3' "$SPORTS" -- $R/scripts $R/legacy | wc -l
  echo "# HTTP requests with a method other than GET:"
  gg -n -E 'method="(POST|PUT|PATCH|DELETE)"|\.post\(' "$SPORTS" -- $R/scripts $R/legacy | sed "s/^$SPORTS://" | cut -c1-140
  gg -n 'CLOB_BASE_URL + "/books"' "$SPORTS" -- $R/scripts | sed "s/^$SPORTS://" | cut -c1-160
  echo "# third-party hosts named in the scripts:"
  gg -h -o -E 'https://[A-Za-z0-9.-]+' "$SPORTS" -- $R/scripts $R/legacy | sort | uniq -c | sort -rn | sed 's/^/    /'
  echo
  echo "# process launches"
  gg -n -E 'subprocess\.(run|Popen)|os\.system' "$SPORTS" -- $R/scripts $R/legacy | sed "s/^$SPORTS://" | cut -c1-150
  echo "# the launcher line in the delivery module:"
  gg -n -E 'zcode\.cmd .*--mode yolo|powershell\.exe' "$SPORTS" -- $R/scripts/weixin_delivery.py | sed "s/^$SPORTS://" | cut -c1-200
  echo "# dynamic code loading:"
  gg -n -E '\bexec\(compile\(' "$SPORTS" -- $R/scripts $R/legacy | sed "s/^$SPORTS://" | cut -c1-150
  echo "# files written outside PostgreSQL:"
  gg -n -E 'os\.replace\(|tempfile\.mkstemp|\.write_text\(|\.write_bytes\(' "$SPORTS" -- $R/scripts $R/legacy | sed "s/^$SPORTS://" | cut -c1-150
  echo
  echo "# owner binding scope string required by the code:"
  gg -n 'owner.get("scope")' "$SPORTS" -- $R/scripts/football_store.py | sed "s/^$SPORTS://" | cut -c1-170
  echo "# admission policy version and its numbers:"
  gg -n -E '^(POLICY_VERSION|GATE_E_FIXTURES|GATE_E_MAX_MAD|FULL_MATCHWEEKS) =' "$SPORTS" -- $R/scripts/football_admission.py | sed "s/^$SPORTS://" | cut -c1-150
  gg -n -E '^(OPENING_CASH_FEN|BASIS_POINTS) =' "$SPORTS" -- $R/scripts/sports_portfolio.py | sed "s/^$SPORTS://" | cut -c1-150
  echo "# timers:"
  for t in $(g ls-tree -r --name-only "$SPORTS" -- $R/systemd | grep '\.timer$'); do
    echo "    $(basename "$t"): $(g show "$SPORTS:$t" | grep -E '^OnCalendar=' | paste -sd' ' -)"
  done
  echo "# runtime tests (static count, nothing executed):"
  for f in $(ls_py $R/tests); do
    g show "$SPORTS:$f" | python3 -I -c '
import ast, sys
tree = ast.parse(sys.stdin.read())
functions = cases = 0
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
        functions += 1
        count = 1
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and ast.unparse(decorator.func).endswith("parametrize"):
                values = decorator.args[1]
                count *= len(values.elts) if isinstance(values, (ast.List, ast.Tuple)) else 1
        cases += count
if functions:
    print(f"    {sys.argv[1]}: {functions} test functions, {cases} cases")
' "$(basename "$f")"
  done
  echo "# runtime scripts whose module name appears in no test file:"
  for f in $(ls_py $R/scripts); do
    m=$(basename "$f" .py); n=0
    for t in $(ls_py $R/tests); do
      if g show "$SPORTS:$t" | grep -q -w "$m"; then n=$((n + 1)); fi
    done
    if [ "$n" -eq 0 ]; then echo "    $m"; fi
  done
  echo "# a plan section the handoff cites that main does not have:"
  gg -n '§87.3' "$SPORTS" -- docs/handoffs/2026-11-23-sports-runtime | sed "s/^$SPORTS://" | cut -c1-80
  echo "    headings of section 87 on main: $(g show "$MAIN:DELIVERY_PLAN.md" | grep -E '^### 87\.' | cut -c1-12 | sed 's/ *$//' | paste -sd' ' -)"
} > "$out/sports_runtime_inventory.txt"

# 9. the handoff's own pins, and whether the research branches still merge
{
  H=docs/handoffs/2026-11-23-sports-runtime
  echo "# manifest entries: $(g show "$SPORTS:$H/MANIFEST.sha256" | wc -l); mismatches against git blobs:"
  bad=0
  while read -r sum path; do
    path=${path#\*}
    [ "$(g show "$SPORTS:$path" | sha256sum | cut -d' ' -f1)" = "$sum" ] || { echo "    MISMATCH $path"; bad=$((bad+1)); }
  done < <(g show "$SPORTS:$H/MANIFEST.sha256")
  echo "    $bad"
  echo "# research sources.tsv against the retained branches (tip, tree, base is ancestor of main, patch sha256)"
  g show "$SPORTS:$H/research/sources.tsv" | tail -n +2 | while IFS=$'\t' read -r patch ref commit base tree sha; do
    t=no; [ "$(g rev-parse "$commit^{tree}")" = "$tree" ] && t=yes
    a=no; g merge-base --is-ancestor "$base" "$MAIN" && a=yes
    s=no; [ "$(g show "$SPORTS:$H/research/$patch" | sha256sum | cut -d' ' -f1)" = "$sha" ] && s=yes
    echo "    $patch: commit ${commit:0:8} tree==$t base_in_main==$a patch_sha256==$s"
  done
  echo "# merge simulation (git merge-tree --write-tree): clean or conflicting paths"
  sim() { if g merge-tree --write-tree --no-messages "$1" "$2" >/dev/null 2>&1; then echo "    $3: clean"; else echo "    $3: CONFLICT $(g merge-tree --write-tree --name-only --no-messages "$1" "$2" | tail -n +2 | tr '\n' ' ')"; fi; }
  sim "$MAIN" "$SPORTS" "main + sports-runtime"
  sim "$MAIN" "$FX40" "main + fx40"; sim "$MAIN" "$FX44" "main + fx44"; sim "$MAIN" "$FX41" "main + fx41-nba-v2"
  sim "$FX40" "$FX44" "fx40 + fx44"; sim "$FX40" "$FX41" "fx40 + fx41-nba-v2"; sim "$FX44" "$FX41" "fx44 + fx41-nba-v2"
  echo "# numbers the NBA v2 draft adds next to the registered trial cap"
  echo "    main: $(gg -n -E '^MAX_TRIALS = ' "$MAIN" -- "$P/optimize/runner.py" | sed "s/^$MAIN://")"
  echo "    fx41: $(gg -n -E '^NBA_ODR_MAX_TRIALS = ' "$FX41" -- "$P/optimize/runner.py" | sed "s/^$FX41://")"
} > "$out/sports_handoff_verification.txt"

echo "wrote: $(ls "$out" | tr '\n' ' ')"
