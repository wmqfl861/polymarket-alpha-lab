#!/usr/bin/env bash
# Read-only. Recomputes, from git objects only, the local (non-API) facts this review relies on.
# Nothing is checked out, nothing in the repository is executed, nothing is written outside <out_dir>.
#
#   bash collect_local.sh <repo_dir> <out_dir>
#
# <repo_dir> must have fetched main, the tag evidence/fx48-legacy-integrity-20261126 and the
# branches handoff/sports-runtime-20261123 and work/fx44-close-window-and-m2-guide
# (as origin/* remote-tracking refs).
set -euo pipefail
repo="$1"; out="$2"; mkdir -p "$out"
g() { git -C "$repo" "$@"; }
gg() { git -C "$repo" grep "$@" || true; }   # a search with no match is a result, not an error
# Truncate each line to N characters and drop trailing blanks. GNU cut counts bytes and can split a multi-byte character.
clip() {
  python3 -I -c '
import sys
limit = int(sys.argv[1])
for raw in sys.stdin.buffer:
    line = raw.decode("utf-8", errors="replace").rstrip("\n")
    sys.stdout.buffer.write((line[:limit].rstrip() + "\n").encode("utf-8"))
' "$1"
}
BASE=ab6edff2d64ac8aa5c2831d748b8730b63f8312d      # main reviewed on 2026-11-24
MAIN=9a64415ae8a191f4c0abc5276c661240e1920e8c      # main reviewed in this round
LASTCI=eae037aa21bc827ebf0d33ec2f61d1d3d10f17f4    # last main commit with workflow runs (merge of #135)
WFDEL=128a3680                                     # commit that deleted the workflow files
RUNTIME=bcce1b17f7263ab89859febfcb56188c295ab371   # handoff/sports-runtime-20261123 (unchanged since 2026-11-24)
P=src/polymarket_alpha_lab/papersim
R=docs/paper-sim/runtime

area_of() {
  python3 -I -c '
import collections, sys
files, added, deleted = collections.Counter(), collections.Counter(), collections.Counter()
for line in sys.stdin:
    a, d, name = line.rstrip("\n").split("\t", 2)
    a = 0 if a == "-" else int(a); d = 0 if d == "-" else int(d)
    if name.startswith("src/"): key = "src"
    elif name.startswith(("tools/", "scripts/")): key = "tools+scripts"
    elif name.startswith("tests/"): key = "tests"
    elif name.startswith("docs/paper-sim/runtime/"): key = "docs/paper-sim/runtime"
    elif name.startswith(".github/"): key = ".github"
    elif name in ("AGENTS.md", "DELIVERY_PLAN.md"): key = name
    elif name.startswith("docs/"): key = "docs (other)"
    else: key = "other"
    files[key] += 1; added[key] += a; deleted[key] += d
for key in sorted(files):
    print(f"    {key}: {files[key]} files, +{added[key]} -{deleted[key]}")
print(f"    total: {sum(files.values())} files, +{sum(added.values())} -{sum(deleted.values())}")
'
}

# 1. what landed on main
{
  echo "# range $BASE..$MAIN"
  echo "commits: $(g rev-list --count "$BASE..$MAIN"); first-parent: $(g rev-list --count --first-parent "$BASE..$MAIN") (merges $(g rev-list --count --first-parent --merges "$BASE..$MAIN"), non-merge $(g rev-list --count --first-parent --no-merges "$BASE..$MAIN"))"
  echo "by area:"
  g diff --numstat --no-renames "$BASE" "$MAIN" | area_of
  echo
  echo "# first-parent commits, oldest first: commit | committer date (UTC) | kind | paths touched (src / tools+scripts / runtime / tests / other) | subject"
  for c in $(g rev-list --first-parent --reverse "$BASE..$MAIN"); do
    kind=direct; [ "$(g rev-list --parents -n 1 "$c" | wc -w)" -gt 2 ] && kind=merge
    names=$(g diff --name-only --no-renames "$c^1" "$c")
    n_src=$(printf '%s\n' "$names" | grep -c '^src/' || true)
    n_tool=$(printf '%s\n' "$names" | grep -c -E '^(tools|scripts)/' || true)
    n_rt=$(printf '%s\n' "$names" | grep -c '^docs/paper-sim/runtime/' || true)
    n_test=$(printf '%s\n' "$names" | grep -c '^tests/' || true)
    n_all=$(printf '%s\n' "$names" | grep -c . || true)
    n_other=$((n_all - n_src - n_tool - n_rt - n_test))
    after=""; g merge-base --is-ancestor "$LASTCI" "$c^1" && after=" after-last-CI"
    echo "${c:0:8} | $(TZ=UTC g log -1 --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd "$c") | $kind$after | $n_src / $n_tool / $n_rt / $n_test / $n_other | $(g log -1 --format=%s "$c" | clip 90)"
  done
  echo
  echo "# after the last commit that had workflow runs ($LASTCI)"
  echo "commits: $(g rev-list --count "$LASTCI..$MAIN"); first-parent: $(g rev-list --count --first-parent "$LASTCI..$MAIN") (merges $(g rev-list --count --first-parent --merges "$LASTCI..$MAIN"), non-merge $(g rev-list --count --first-parent --no-merges "$LASTCI..$MAIN"))"
  code=0
  for c in $(g rev-list --first-parent "$LASTCI..$MAIN"); do
    if g diff --name-only --no-renames "$c^1" "$c" | grep -q -E '^(src|tools|scripts|docs/paper-sim/runtime)/'; then code=$((code + 1)); fi
  done
  echo "first-parent commits that touch src, tools, scripts or the runtime scripts: $code"
  echo "direct (non-merge) first-parent commits before that point in this range: $(g rev-list --count --first-parent --no-merges "$BASE..$LASTCI")"
  echo "by area:"
  g diff --numstat --no-renames "$LASTCI" "$MAIN" | area_of
  echo "source and tool files changed after that point:"
  g diff --numstat --no-renames "$LASTCI" "$MAIN" -- src tools scripts "$R" | awk '{printf "    +%s -%s %s\n",$1,$2,$3}'
} > "$out/main_delta.txt"

# 2. each pull-request merge made while the workflows existed: is the merge tree equal to the head tree?
{
  echo "# merge commit | head (2nd parent) | head contains first parent | merge tree == head tree | subject"
  for m in $(g rev-list --first-parent --merges --reverse "$BASE..$MAIN"); do
    p1=$(g rev-parse "$m^1"); h=$(g rev-parse "$m^2")
    contains=no; g merge-base --is-ancestor "$p1" "$h" && contains=yes
    same=no; [ "$(g rev-parse "$m^{tree}")" = "$(g rev-parse "$h^{tree}")" ] && same=yes
    echo "${m:0:8} | ${h:0:8} | $contains | $same | $(g log -1 --format=%s "$m" | clip 70)"
  done
} > "$out/merge_tree_equivalence.txt"

# 3. frozen content untouched on main
{
  echo "# paths that must not change between $BASE and $MAIN; lines between the markers = changed paths"
  echo "# (begin)"
  g diff --name-status --no-renames "$BASE" "$MAIN" -- tests/fixtures/papersim database/papersim supabase/migrations \
    "$P/gates.py" "$P/evaluation.py" "$P/pipeline.py" "$P/soccer_model.py" "$P/nba_model.py" \
    "$P/crypto_model.py" "$P/intl_model.py" "$P/fees.py" "$P/queue_policy.py" "$P/risk_policy.py" \
    "$P/selfcheck.py" "$P/optimize/policy.py"
  echo "# (end)"
  echo "# everything under database/ that changed:"
  g diff --numstat --no-renames "$BASE" "$MAIN" -- database | awk '{printf "    +%s -%s %s\n",$1,$2,$3}'
  echo "# registered trial caps on main:"
  gg -n -E '^(MAX_TRIALS|NBA_ODR_MAX_TRIALS) = ' "$MAIN" -- "$P/optimize/runner.py" | sed "s/^$MAIN://" | sed 's/^/    /'
  echo "# where the larger cap is taken: only after a registered claim, and the schedule refuses to run without one"
  gg -n -E 'if nba_odr_claim is not None:|_take_nba_odr_claim\(nba_odr_claim\)|elif schedule_id == NBA_ODR_SCHEDULE_ID:|raise ValueError\(_NBA_ODR_REGISTRATION_REQUIRED\)|max_trials = NBA_ODR_MAX_TRIALS|not 1 <= max_trials <= MAX_TRIALS' "$MAIN" -- "$P/optimize/runner.py" | sed "s/^$MAIN://" | clip 150 | sed 's/^/    /'
} > "$out/frozen_content_check.txt"

# 4. the root rules file and the workflow files
{
  for c in "$BASE" "$MAIN"; do
    echo "AGENTS.md at ${c:0:8}: blob $(g rev-parse "$c:AGENTS.md"), $(g cat-file -s "$c:AGENTS.md") bytes, $(g show "$c:AGENTS.md" | wc -l) lines, sha256 $(g show "$c:AGENTS.md" | sha256sum | cut -d' ' -f1)"
  done
  echo "sha256 recorded at the head of DELIVERY_PLAN.md on main: $(g show "$MAIN:DELIVERY_PLAN.md" | sed -n '1,12p' | grep -o -E '[0-9a-f]{64}' | sort -u | paste -sd' ' -)"
  echo "headings of AGENTS.md on main:"
  g show "$MAIN:AGENTS.md" | grep -E '^#' | sed 's/^/    /'
  echo "headings of AGENTS.md at the previous review:"
  g show "$BASE:AGENTS.md" | grep -E '^#' | clip 100 | sed 's/^/    /'
  echo
  echo "files under .github at the previous review: $(g ls-tree -r --name-only "$BASE" -- .github | wc -l)"
  echo "files under .github on main: $(g ls-tree -r --name-only "$MAIN" -- .github | wc -l)"
  echo "commit that removed them: $(TZ=UTC g log -1 --date=format-local:%Y-%m-%dT%H:%M:%SZ --format='%H %cd %s' "$WFDEL")"
  g show --stat=120 --format= "$WFDEL" -- .github | clip 130 | sed 's/^/    /'
  echo "merge that replaced AGENTS.md: $(TZ=UTC g log -1 --date=format-local:%Y-%m-%dT%H:%M:%SZ --format='%H %cd %s' "$(g log --first-parent --format=%H -1 "$MAIN" -- AGENTS.md)")"
  echo
  echo "DELIVERY_PLAN.md on main: $(g show "$MAIN:DELIVERY_PLAN.md" | wc -l) lines; change since the previous review: $(g diff --shortstat "$BASE" "$MAIN" -- DELIVERY_PLAN.md | sed 's/^ *//')"
} > "$out/rules_and_workflows.txt"

# 5. the delivery code on main and what the runtime branch still contains
{
  echo "# delivery module and fixed script on main $MAIN"
  for f in $(g ls-tree -r --name-only "$MAIN" -- "$R"); do
    echo "    $f: $(g show "$MAIN:$f" | wc -l) lines, sha256 $(g show "$MAIN:$f" | sha256sum | cut -d' ' -f1)"
  done
  echo "recorded hash file content: $(g show "$MAIN:$R/scripts/windows/Invoke-PaperWeixin.ps1.sha256" | clip 120)"
  echo "non-ASCII bytes in the fixed script: $(g show "$MAIN:$R/scripts/windows/Invoke-PaperWeixin.ps1" | LC_ALL=C grep -c -P '[^\x00-\x7F]' || true)"
  echo "first commit of these files on main: $(TZ=UTC g log --diff-filter=A --date=format-local:%Y-%m-%dT%H:%M:%SZ --format='%h %cd %s' "$MAIN" -- "$R/scripts/weixin_delivery.py" | tail -1)"
  echo "# the launcher on main (the one process the server asks the Windows host to start):"
  gg -n -E '^def launcher|return \[POWERSHELL_PATH' "$MAIN" -- "$R/scripts/weixin_delivery.py" | sed "s/^$MAIN://" | clip 170 | sed 's/^/    /'
  cnt() { gg -c -i -E "$1" "$2" -- "$R/scripts/weixin_delivery.py" | sed 's/.*://' | paste -sd+ - | sed 's/^$/0/'; }
  echo "# approval-free mode (\"yolo\") in the delivery module: main $(cnt 'yolo' "$MAIN"), runtime branch $(cnt 'yolo' "$RUNTIME")"
  echo "# agent client launch (\"zcode.cmd\") in the delivery module: main $(cnt 'zcode\.cmd' "$MAIN"), runtime branch $(cnt 'zcode\.cmd' "$RUNTIME")"
  echo "# the one remaining mention of the client's name on main is a failure code kept for reading older receipts:"
  gg -n -i 'zcode' "$MAIN" -- "$R/scripts/weixin_delivery.py" | sed "s/^$MAIN://" | clip 150 | sed 's/^/    /'
  echo "# execution-policy parameter in the launcher or the fixed script on main (matches): $(gg -c -i 'ExecutionPolicy' "$MAIN" -- "$R/scripts/weixin_delivery.py" "$R/scripts/windows/Invoke-PaperWeixin.ps1" | sed 's/.*://' | paste -sd+ - | sed 's/^$/0/')"
  echo "# the fixed script locates itself by its own file path and compares its own hash with the request:"
  gg -n -E 'Get-AbsolutePath \$PSCommandPath|\$ScriptHash = Get-Sha256|Test-String \$Request\[.script_sha256.\]' "$MAIN" -- "$R/scripts/windows/Invoke-PaperWeixin.ps1" | sed "s/^$MAIN://" | clip 170 | sed 's/^/    /'
  echo
  echo "# runtime files: on main $(g ls-tree -r --name-only "$MAIN" -- "$R" | wc -l); on the runtime branch $(g ls-tree -r --name-only "$RUNTIME" -- "$R" | wc -l)"
  echo "# runtime branch tip is unchanged since the previous review: $(g rev-parse origin/handoff/sports-runtime-20261123)"
  echo "# candidate kept only as a tag: $(g rev-parse 'refs/tags/evidence/fx48-legacy-integrity-20261126^{commit}') (parent $(g rev-parse 'refs/tags/evidence/fx48-legacy-integrity-20261126^{commit}^'))"
  g show --stat=120 --format= 'refs/tags/evidence/fx48-legacy-integrity-20261126^{commit}' | clip 130 | sed 's/^/    /'
  echo "# timers defined on the runtime branch: $(g ls-tree -r --name-only "$RUNTIME" -- "$R/systemd" | grep -c '\.timer$')"
} > "$out/delivery_code_facts.txt"

# 5b. source modules added in this range, and where each is imported from (reachability)
{
  echo "# source modules added between $BASE and $MAIN, and the non-test files with an import line naming them"
  for f in $(g diff --name-only --no-renames --diff-filter=A "$BASE" "$MAIN" -- src tools scripts | grep '\.py$'); do
    m=$(basename "$f" .py)
    users=$(gg -l -E "^[[:space:]]*(from|import) .*\b$m\b" "$MAIN" -- src tools scripts | sed "s/^$MAIN://" | grep -v "^$f$" | sed 's|^src/polymarket_alpha_lab/||' | paste -sd' ' -)
    echo "    $f ($(g show "$MAIN:$f" | wc -l) lines): ${users:-NONE}"
  done
  echo "# command-line entry points that reach them:"
  gg -n -E 'prepared_scan\.run_prepared_scan\(|capture_preparation_inputs\(|register_managed_nba_odr_experiment\(' "$MAIN" -- src/polymarket_alpha_lab/papersim/cli.py tools/d1_compare.py | sed "s/^$MAIN://" | clip 150 | sed 's/^/    /'
} > "$out/new_module_reachability.txt"

# 6. news evidence binding in the managed preparation path (finding R7-5)
{
  f="$P/prepare_inputs.py"
  echo "# $f on main: the football news record is taken with its own verdict; the crypto one is derived and its sources are registered snapshots"
  gg -n -E 'def _soccer_news|def _crypto_news|return gates.NewsCheck\(fixture, document\["delta"\], document\["concordant"\]|check = gates.check_news\(|snapshots.verify_snapshot\(connection, snapshots.snapshot_digest_argument\(digest\)\)|_require\(_utc\(source\["fetched_at"\]\) <= observed' "$MAIN" -- "$f" | sed "s/^$MAIN://" | clip 170 | sed 's/^/    /'
  echo "# how the gate reads the record, and how check_news derives the verdict:"
  gg -n -E '"news_concordant": news is not None and news.concordant|concordant = news_concordance\(classification\) and not block' "$MAIN" -- "$P/gates.py" | sed "s/^$MAIN://" | clip 170 | sed 's/^/    /'
  echo "# producers of these two record kinds outside tests (matches in src and tools): $(gg -l -E 'SOCCER_NEWS_KIND|CRYPTO_NEWS_KIND|papersim_prepare_soccer_news|papersim_prepare_crypto_news' "$MAIN" -- src tools | sed "s/^$MAIN://" | paste -sd' ' -)"
} > "$out/news_binding_trace.txt"

# 7. crypto terminal contracts: the rule text the new matcher accepts
{
  f="$P/prepare_inputs.py"
  echo "# $f on main"
  gg -n -E '_BINANCE_CLOSING_RULE = |closing at 12:00 in the et timezone|clock_is_close|opening = clock - timedelta\(minutes=1\)|market.end_at != opening \+ timedelta\(minutes=1\)' "$MAIN" -- "$f" | sed "s/^$MAIN://" | clip 170 | sed 's/^/    /'
  echo "# commit that added it: $(TZ=UTC g log -1 --date=format-local:%Y-%m-%dT%H:%M:%SZ --format='%h %cd %s' -S'_BINANCE_CLOSING_RULE' "$MAIN" -- "$f")"
} > "$out/crypto_terminal_rule.txt"

# 8. identities on the new commits (e-mail domains only)
{
  echo "# author-domain committer-domain count, for the $(g rev-list --count "$BASE..$MAIN") commits in $BASE..$MAIN"
  g log --format='%ae %ce' "$BASE..$MAIN" | awk '{split($1,a,"@"); split($2,c,"@"); print a[2], c[2]}' | sort | uniq -c | awk '{printf "    %s %s %s\n",$2,$3,$1}'
} > "$out/commit_identity_domains.txt"

# 9. open items named by the repository's own follow-up record
{
  f=docs/paper-sim/design/optimizer-phase1/2026-11-26/audit-followup.md
  echo "# $f on main: $(g show "$MAIN:$f" | wc -l) lines, sha256 $(g show "$MAIN:$f" | sha256sum | cut -d' ' -f1)"
  echo "# phrases counted in that file:"
  for phrase in '两条尚未关闭的 P1' '0/6' '0/7' '0/4' '部署提交' 'NO_OBSERVATION'; do
    echo "    \"$phrase\": $(g show "$MAIN:$f" | grep -c -F -- "$phrase" || true)"
  done
  s=docs/paper-sim/design/optimizer-phase1/2026-11-26/field-status.json
  echo "# $s on main: $(g cat-file -s "$MAIN:$s") bytes, sha256 $(g show "$MAIN:$s" | sha256sum | cut -d' ' -f1)"
  echo "# FX-44 candidate branch: $(g rev-parse origin/work/fx44-close-window-and-m2-guide) (merged into main: $(g merge-base --is-ancestor origin/work/fx44-close-window-and-m2-guide "$MAIN" && echo yes || echo no))"
} > "$out/open_items_in_repo_records.txt"

echo "wrote: $(ls "$out" | paste -sd' ' -)"
