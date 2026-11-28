#!/usr/bin/env bash
# Read-only. Recomputes, from git objects only, the local (non-API) facts this review relies on.
# Nothing is checked out, nothing in the repository is executed, nothing is written outside <out_dir>.
#
#   bash collect_local.sh <repo_dir> <out_dir>
#
# <repo_dir> must have fetched main and the branch handoff/sports-runtime-20261123
# (as origin/* remote-tracking refs).
set -euo pipefail
repo="$1"; out="$2"; mkdir -p "$out"
g() { git -C "$repo" "$@"; }
gg() { git -C "$repo" grep "$@" || true; }   # a search with no match is a result, not an error

BASE=9a64415ae8a191f4c0abc5276c661240e1920e8c      # main reviewed on 2026-11-26
MAIN=eaf6e8149f83a708d09158529ec774f4fbed8b9d      # main reviewed in this round
RUNTIME=bcce1b17f7263ab89859febfcb56188c295ab371   # handoff/sports-runtime-20261123 (unchanged)
P=src/polymarket_alpha_lab/papersim
D=docs/paper-sim/design
js() {  # js <path in MAIN> <python expression over d>
  g show "$MAIN:$1" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$2"
}

{
echo "== A. range $BASE..$MAIN"
echo "commits: $(g rev-list --count $BASE..$MAIN); on the first-parent line: $(g rev-list --count --first-parent $BASE..$MAIN); merges on it: $(g rev-list --count --first-parent --merges $BASE..$MAIN)"
g diff --no-renames --shortstat $BASE $MAIN
for area in docs/handoffs/2026-11-26-completion-review tests docs/paper-sim/runtime src $D/fx-49 $D/fx-44 tools/sports_paper_handoff $D/fx-23 docs/paper-sim/research/euro-soccer-20261128 $D/fx-36 DELIVERY_PLAN.md AGENTS.md deploy; do
  printf '  %-52s %s\n' "$area" "$(g diff --no-renames --shortstat $BASE $MAIN -- "$area" | sed 's/^ *//')"
done
echo "files changed under src/ outside $P: $(g diff --name-only $BASE $MAIN -- src | grep -v "^$P/" | wc -l)"
echo "workflow files on main: $(g ls-tree -r --name-only $MAIN -- .github | wc -l)"

echo
echo "== B. AGENTS.md"
echo "lines: $(g show $MAIN:AGENTS.md | wc -l); blob: $(g rev-parse $MAIN:AGENTS.md); sha256: $(g show $MAIN:AGENTS.md | sha256sum | cut -c1-64)"
echo "lines added since $BASE: $(g diff --numstat $BASE $MAIN -- AGENTS.md | cut -f1); removed: $(g diff --numstat $BASE $MAIN -- AGENTS.md | cut -f2)"
g diff $BASE $MAIN -- AGENTS.md | grep -E '^\+[^+]' | sed 's/^/  /'

echo
echo "== C. registered gate rules"
echo "lines of $P/gates.py changed in the range: $(g diff --numstat $BASE $MAIN -- $P/gates.py | wc -l) (0 = file untouched)"
g show $MAIN:$P/gates.py | sed -n '/^GATES/,/^)/p' | sed 's/^/  /'
echo "checks in gates.evaluate_candidate (registered):"
g show $MAIN:$P/gates.py | sed -n '/^_CANDIDATE_CHECK_ORDER/,/^)/p' | grep -o '"[a-z_0-9]*"),' | tr -d '"),' | tr '\n' ' '; echo

echo
echo "== D. small-stake paper pricing (2026-11-27)"
echo "shared module sha256: $(g show $MAIN:$P/sports_paper_pricing.py | sha256sum | cut -c1-64); lines: $(g show $MAIN:$P/sports_paper_pricing.py | wc -l)"
echo "checks in sports_paper_pricing.evaluate_candidate (daily paper book):"
g show $MAIN:$P/sports_paper_pricing.py | sed -n '/checks = {/,/}/p' | grep -o '"[a-z_]*":' | tr -d '":' | tr '\n' ' '; echo
echo "the size condition in best_ask():"
g show $MAIN:$P/sports_paper_pricing.py | grep -n 'size > 0' | sed 's/^/  /'
echo "callers of the module inside main (outside tests and the handoff tool): $(gg -l 'sports_paper_pricing' $MAIN -- src docs/paper-sim/runtime | grep -v "$P/sports_paper_pricing.py" | wc -l)"
echo "runtime files touched by tools/sports_paper_handoff/daily-best-ask.patch: $(g show $MAIN:tools/sports_paper_handoff/daily-best-ask.patch | grep -c '^diff --git')"
g show $MAIN:tools/sports_paper_handoff/daily-best-ask.patch | grep '^diff --git' | sed 's#.* b/docs/paper-sim/runtime/scripts/#  #'
patch_count() { g show $MAIN:tools/sports_paper_handoff/daily-best-ask.patch | grep -E "^$1" | grep -c -- "$2" || true; }
for key in near_two_cent_depth filled_usd depth_filled_usd gamma_liquidity price_basis; do
  echo "  patch lines mentioning $key: removed=$(patch_count '-' "$key") added=$(patch_count '\+' "$key")"
done
echo "football_forward.py after the patch still calls the registered evaluation: $(g show $MAIN:tools/sports_paper_handoff/daily-best-ask.patch | sed -n '/b\/docs\/paper-sim\/runtime\/scripts\/football_forward.py/,/^diff --git a\/docs\/paper-sim\/runtime\/scripts\/football_shortlist.py/p' | grep -c 'feed.gates.evaluate_candidate')"
echo "simulated account on the runtime branch ($RUNTIME):"
g show $RUNTIME:docs/paper-sim/runtime/scripts/sports_portfolio.py | grep -n -E '^(OPENING_CASH_FEN|BASIS_POINTS) = ' | sed 's/^/  /'
g show $RUNTIME:docs/paper-sim/runtime/scripts/sports_portfolio.py | grep -n 'stake_fen = min(cash' | sed 's/^ */  /'
echo "currency conversion anywhere in the runtime scripts or the pricing module: $(gg -i -l -E 'usd_?cny|cny_?usd|exchange_rate' $RUNTIME -- docs/paper-sim/runtime/scripts | wc -l) file(s) on the runtime branch, $(gg -i -l -E 'usd_?cny|cny_?usd|exchange_rate' $MAIN -- $P/sports_paper_pricing.py tools/sports_paper_handoff | wc -l) in main"
echo "runtime's own full suite as recorded in docs/handoffs/2026-11-27-sports-paper-pricing.md:"
g show $MAIN:docs/handoffs/2026-11-27-sports-paper-pricing.md | grep -E '完整运行层套件' | sed 's/^/  /'

echo
echo "== E. European football study (2026-11-28)"
g show $MAIN:$P/euro_soccer_model.py | grep -n '^MODEL_VERSION' | sed 's/^/  /'
for f in markets model outcomes report sources store study team_news; do printf '  euro_soccer_%s.py %s lines\n' "$f" "$(g show $MAIN:$P/euro_soccer_$f.py | wc -l)"; done
echo "timer: $(g show $MAIN:deploy/systemd/user/pal-euro-soccer-study.timer | grep OnCalendar)"
echo "service: $(g show $MAIN:deploy/systemd/user/pal-euro-soccer-study.service | grep -E '^(ExecStart|TimeoutStartSec)' | sed 's#%h/[^ ]*/##' | tr '\n' ' ')"
echo "database unit: $(g show $MAIN:deploy/systemd/user/pal-euro-soccer-db.service | grep -E '^ExecStart' | sed 's#%h/[^ ]*/##')"
echo "request guard in euro_soccer_store.py (fixed endpoints only):"
g show $MAIN:$P/euro_soccer_store.py | grep -n -E 'split.scheme != "https"|/api/data/leagues|/api/data/matchDetails|GAMMA_MARKETS_BY_CONDITION_URL|CLOB_BASE_URL \+ "/books"|ProxyHandler|_NoRedirect' | sed 's/^ */  /' | cut -c1-150
echo "amount and scope markers in euro_soccer_study.py: research_only=$(g show $MAIN:$P/euro_soccer_study.py | grep -c '"research_only": True') amount_cny_zero=$(g show $MAIN:$P/euro_soccer_study.py | grep -c '"amount_cny": 0')"
F=docs/paper-sim/research/euro-soccer-20261128/field-check.json
echo "field check: as_of=$(js $F 'd["as_of_utc"]') timer=$(js $F 'd["timer"]["enabled"]+"/"+d["timer"]["active"]') code=$(js $F 'd["runtime_code_readback"]') amount_cny=$(js $F 'd["amount_cny"]')"
echo "field check per league (fixtures previewed / with news scenario / forward windows / closing windows / scored):"
js $F '"\n".join("  %s: %s / %s / %s / %s / %s" % (k, v["fixtures"], v["team_news_scenario_fixtures"], v["windows"]["forward"], v["windows"]["closing"], v["scored_fixtures"]) for k, v in sorted(d["leagues"].items()))'
echo "files changed between the deployed study commit and main, outside docs/: $(g diff --name-only "$(js $F 'd["runtime_code_readback"]')" $MAIN | grep -v '^docs/' | tr '\n' ' ')"

echo
echo "== F. WeChat delivery (FX-49)"
W=$D/fx-49/2026-11-26-windows-protected-candidate.json
echo "newest fx-49 record on main: $(g log -1 --format='%cI %h' $MAIN -- $D/fx-49)"
echo "windows candidate record: production_switched=$(js $W 'd.get("production_switched")') verified_natural_deliveries=$(js $W 'd.get("verified_natural_deliveries")') windows_six_completed=$(js $W 'd.get("windows_six_completed")') server_fixed_checkout=$(js $W 'd.get("server_fixed_checkout")')"
echo "status board row in DELIVERY_PLAN.md:"
g show $MAIN:DELIVERY_PLAN.md | grep -n '^| 微信投递与运行层' | python3 -I -c 'import sys; [print("  " + l.rstrip().split("|")[-2].strip()) for l in sys.stdin]'
echo "DELIVERY_PLAN.md lines carrying the blanket sentence: $(g show $MAIN:DELIVERY_PLAN.md | grep -n -F '一概同意' | cut -d: -f1 | tr '\n' ' ')"
g show $MAIN:DELIVERY_PLAN.md | python3 -I -c '
import sys
lines = sys.stdin.read().split("\n")
first = next(i for i, l in enumerate(lines, 1) if "best_ask" in l)
sec89 = next(i for i, l in enumerate(lines, 1) if l.startswith("## 89."))
quotes = [i for i, l in enumerate(lines, 1) if i >= first and any(k in l for k in ("机主原话", "机主答复", "机主要求", "机主提出"))]
print("DELIVERY_PLAN.md: pricing paragraph at line %d, section 89 at line %d, %d lines in all; lines from there on that quote or cite the owner: %d" % (first, sec89, len(lines), len(quotes)))
'
echo "repository copy of the delivery caller dispatches: $(g show $MAIN:docs/paper-sim/runtime/scripts/weixin_delivery.py | grep -n 'return \[FIXED_RELAY, nonce\]' | sed 's/ \+/ /g')"

echo
echo "== G. access restrictions (FX-36)"
X=$D/fx-36
echo "branch rules readback: observed=$(js $X/2026-11-26-effective-branch-protection.json 'd["observed_utc"]') main_rules=$(js $X/2026-11-26-effective-branch-protection.json 'd["branches"]["main"]["effective_rules_count"]') review_branch_rules=$(js $X/2026-11-26-effective-branch-protection.json 'd["branches"]["review_branch"]["effective_rules_count"]')"
echo "login policy: applied=$(js $X/2026-11-26-login-hardening.json 'd["login-policy-change"]["applied_utc"]') password_disabled=$(js $X/2026-11-26-login-hardening.json 'd["login-policy-change"]["verification"]["all_password_disabled"]') keyboard_interactive_disabled=$(js $X/2026-11-26-login-hardening.json 'd["login-policy-change"]["verification"]["all_keyboard_interactive_disabled"]')"
echo "second restriction: applied=$(js $X/2026-11-26-second-restriction-hardening.json 'd["change"]["applied_utc"]') start_condition_mandatory=$(js $X/2026-11-26-second-restriction-hardening.json 'd["change"]["checks"]["start_condition_mandatory"]') external=$(js $X/2026-11-26-second-restriction-hardening.json 'd["external_recheck"]["network_observation"]["network_outcome"]') cold_boot_measured=$(js $X/2026-11-26-second-restriction-hardening.json 'd["change"]["cold_boot_duration_measured"]')"

echo
echo "== H. branch cleanup (FX-23) and local full tests"
C=$D/fx-23/2026-11-26-cleanup-result.json
echo "cleanup record: before=$(js $C 'd["before"]') after=$(js $C 'd["after"]') removed=$(js $C 'd["removed"]') pr131=$(js $C 'd["pr131"]["state"]') retained=$(js $C 'd["retained_groups"]')"
T=$D/fx-49/full-tests-1a74b5429f84.json
js $T '"\n".join("local full run on %s, %s: passed=%s skipped=%s failed=%s errors=%s finished=%s" % (d["tested_main_commit"][:8], r["profile"], r["counts"]["passed"], r["counts"]["skipped"], r["counts"]["failed"], r["counts"]["errors"], r["pytest_finished_utc"]) for r in d["runs"])'

echo
echo "== I. commit identities in the range (e-mail domains only)"
g log --format='%ae%n%ce' $BASE..$MAIN | sed 's/.*@//' | sort | uniq -c | sed 's/^ */  /'
} | sed 's/[[:space:]]*$//' > "$out/local_facts.txt"
echo "wrote: local_facts.txt ($(wc -l < "$out/local_facts.txt") lines)"
