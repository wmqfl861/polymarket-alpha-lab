#!/usr/bin/env bash
# Read-only. Recomputes, from git objects only, the local (non-API) facts this review relies on.
# Nothing is checked out, nothing in the repository is executed, nothing is written outside <out_dir>.
#
#   bash collect_local.sh <repo_dir> <out_dir>
set -euo pipefail
repo="$1"; out="$2"; mkdir -p "$out"
g() { git -C "$repo" "$@"; }
gg() { git -C "$repo" grep "$@" || true; }   # a search with no match is a result, not an error

BASE=eaf6e8149f83a708d09158529ec774f4fbed8b9d      # main reviewed earlier on 2026-11-28
MAIN=2fc69b0560b073e05718ea335c05388872756e66      # main reviewed in this round
P=src/polymarket_alpha_lab/papersim
D=docs/paper-sim/design
R=docs/paper-sim/research/sports-historical-20261128
js() {  # js <path in MAIN> <python expression over d>
  g show "$MAIN:$1" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$2"
}

{
echo "== A. range $BASE..$MAIN"
echo "commits: $(g rev-list --count $BASE..$MAIN); on the first-parent line: $(g rev-list --count --first-parent $BASE..$MAIN); merges on it: $(g rev-list --count --first-parent --merges $BASE..$MAIN)"
g diff --no-renames --shortstat $BASE $MAIN
for area in docs/handoffs/2026-11-28-requirements-review $R tools/sports_paper_handoff $D src tests DELIVERY_PLAN.md AGENTS.md deploy; do
  printf '  %-56s %s\n' "$area" "$(g diff --no-renames --shortstat $BASE $MAIN -- "$area" | sed 's/^ *//')"
done
echo "source files changed: $(g diff --name-only $BASE $MAIN -- src | tr '\n' ' ')"
echo "previous delivery cf61d445 is an ancestor of main: $(g merge-base --is-ancestor cf61d445fa62dd8f802c96c75ddd8c5244f35255 $MAIN && echo yes || echo no)"
echo "AGENTS.md blob: $(g rev-parse $MAIN:AGENTS.md) (unchanged since the baseline: $([ "$(g rev-parse $BASE:AGENTS.md)" = "$(g rev-parse $MAIN:AGENTS.md)" ] && echo yes || echo no))"
echo "lines of $P/gates.py changed in the range: $(g diff --numstat $BASE $MAIN -- $P/gates.py | wc -l)"

echo
echo "== B. the owner's recorded reply and the delivery item"
g show $MAIN:DELIVERY_PLAN.md | python3 -I -c '
import sys
lines = sys.stdin.read().split("\n")
sec = next(i for i, l in enumerate(lines, 1) if l.startswith("## 90."))
quote = next(i for i, l in enumerate(lines, 1) if i > sec and l.startswith("> "))
print("section 90 starts at line %d; first quoted line after it (line %d):" % (sec, quote))
print("  " + lines[quote - 1])
row = next(i for i, l in enumerate(lines, 1) if l.startswith("| OD-AC |"))
print("the OD-AC row (line %d):" % row)
print("  " + lines[row - 1])
board = next(i for i, l in enumerate(lines, 1) if l.startswith("## 状态板"))
rows = [l.split("|")[1].strip() for l in lines[board:board + 30] if l.startswith("| ") and not l.startswith("| ---")]
print("status board heading: %s; first column of its rows: %s" % (lines[board - 1], " / ".join(rows)))
'
for phrase in 个人通讯 本机范围; do
  echo "files outside the audit packages that contain \"$phrase\": $(gg -l -F "$phrase" $MAIN -- . ':!docs/handoffs/2026-11-26-completion-review' ':!docs/handoffs/2026-11-28-requirements-review' | sed "s/^$MAIN://" | tr '\n' ' ')"
done
echo "lines in the repository that put those words in a quotation (start with '> '): $(gg -h -F -e 个人通讯 -e 本机范围 $MAIN -- . | grep -c '^> ' || true)"
echo "fx-49 records added or changed in the range: $(g diff --name-only $BASE $MAIN -- $D/fx-49 | wc -l)"
echo "scope sentence of the field-change table:"
g show $MAIN:$D/fx-56/2026-11-28-project-field-changes.md | sed -n '3p' | sed 's/^/  /'

echo
echo "== C. small-stake pricing (FX-57)"
echo "shared module: $(g show $MAIN:$P/sports_paper_pricing.py | wc -l) lines, sha256 $(g show $MAIN:$P/sports_paper_pricing.py | sha256sum | cut -c1-64)"
echo "candidate patch touches $(g show $MAIN:tools/sports_paper_handoff/fx57/daily-best-ask.patch | grep -c '^diff --git') runtime files; README says: $(g show $MAIN:tools/sports_paper_handoff/fx57/README.md | grep -o '本候选供离线复验，尚待部署' | head -1)"
H=$D/fx-57/2026-11-28-historical-best-ask.json
echo "ledger readback: as_of=$(js $H 'd["as_of_utc"]') positions_after_deployment=$(js $H 'd["readback"]["positions_after_deployment"]') source_cache_rows=$(js $H 'd["readback"]["source_cache"]["rows"]') with_asks=$(js $H 'd["readback"]["source_cache"]["with_asks"]') with_best_ask_size=$(js $H 'd["readback"]["source_cache"]["with_best_ask_size"]')"
echo "per position (league, recorded best ask, stake in yuan, realised P&L, capacity assessment):"
js $H '"\n".join("  %s, %s, %s, %s, %s" % (p["league"], p["recorded_best_ask"], p["paper_stake_cny"], p["realized_pnl_cny"], p["assessment"]) for p in d["positions"])'

echo
echo "== D. historical validation evidence"
echo "files in $R: $(g ls-tree -r --name-only $MAIN -- $R | wc -l)"
echo "files in main that mention the replay rows or its kind: $(gg -l -E 'reconstructed_rows|event_time_retro_reconstruction' $MAIN -- . | sed "s/^$MAIN://" | tr '\n' ' ')"
N=$R/new-five.json
echo "five-league replay: model=$(js $N 'd["model_version"]') code=$(js $N 'd["repository_commit"][:8]') inputs=$(js $N 'd["registered_input_count"]') new_admissions=$(js $N 'd["new_admission_eligible_count"]') contemporaneous_news_rows=$(js $N 'd["definitely_contemporaneous_historical_news_rows"]')"
echo "per league (normal regular targets / forecasts / three-way paired n and Brier model, market / over-2.5 paired n and Brier model, market):"
js $N '"\n".join("  %s: %s / %s / %s %.5f %.5f / %s" % (k, v["normal_regular_targets"], v["forecasts"], v["metrics"]["match_winner"]["paired_n"], v["metrics"]["match_winner"]["paired"]["model_brier"], v["metrics"]["match_winner"]["paired"]["market_brier"], ("%s %.5f %.5f" % (v["metrics"]["total_2_5"]["paired_n"], v["metrics"]["total_2_5"]["paired"]["model_brier"], v["metrics"]["total_2_5"]["paired"]["market_brier"])) if v["metrics"]["total_2_5"]["paired_n"] else "0") for k, v in sorted(d["leagues"].items()))'
B=$R/basketball.json
echo "NBA prospective strict pairs: baseline n=$(js $B 'd["nba"]["prospective_strict_T30_home_side"]["baseline"]["model"]["n"]') model=$(js $B '"%.5f" % d["nba"]["prospective_strict_T30_home_side"]["baseline"]["model"]["brier"]') market=$(js $B '"%.5f" % d["nba"]["prospective_strict_T30_home_side"]["baseline"]["market"]["brier"]')"
echo "NBA archived 2024-25 diagnostic rows: $(js $B '"; ".join("n=%s market %.5f baseline %.5f optimized %.5f" % (r["market"]["n"], r["market"]["brier"], r["baseline"]["brier"], r["archived_optimized"]["brier"]) for r in d["nba"]["archived_exploratory_spec_same_heldout_rows"] if r.get("market_kind") == "ml" and "market" in r)')"
echo "NBA total contracts whose wording does not state overtime: $(js $B 'sum(v["total_descriptions"] - v["descriptions_mention_overtime_or_OT"] for v in d["nba"]["archived_total_wording"].values())')"
echo "sealed optimizer run: $(js $B 'd["nba"]["sealed_optimizer_not_rerun"]["outcome"]') after $(js $B 'd["nba"]["sealed_optimizer_not_rerun"]["trials"]') trials"
echo "market reference in the registration: $(g show $MAIN:docs/paper-sim/research/euro-soccer-20261128/registration.md | grep -o '同场、同盘口的去水平均收盘赔率' | head -1)"

echo
echo "== E. tests and local full runs"
echo "marker added by FX-59: $(g diff $BASE $MAIN -- tests/test_papersim_capture_marks_native.py | grep -E '^\+[^+]' | tr -d '+')"
T=$D/fx-59/full-tests-151f4e4ab913.json
g show $MAIN:$T | python3 -I -c '
import json, sys
d = json.load(sys.stdin)
def walk(o, path=""):
    if isinstance(o, dict):
        if {"passed", "skipped", "failed"} <= set(o):
            print("  %s: passed=%s skipped=%s failed=%s errors=%s" % (path, o["passed"], o["skipped"], o["failed"], o.get("errors")))
        for k, v in o.items():
            walk(v, path + "/" + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, path + "/" + str(i))
print("local full runs recorded in %s:" % sys.argv[1])
walk(d)
' "$T"

echo
echo "== F. branch rules readback and commit identities"
echo "local readback: $(js $D/fx-36/2026-11-28-branch-rules.json 'json.dumps(d)[:300]' 2>/dev/null || g show $MAIN:$D/fx-36/2026-11-28-branch-rules.json | tr -d '\n' | cut -c1-300)"
g log --format='%ae%n%ce' $BASE..$MAIN | sed 's/.*@//' | sort | uniq -c | sed 's/^ */  /'
} | sed 's/[[:space:]]*$//' > "$out/local_facts.txt"
echo "wrote: local_facts.txt ($(wc -l < "$out/local_facts.txt") lines)"
