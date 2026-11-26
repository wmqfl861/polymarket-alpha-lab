#!/usr/bin/env bash
# Read-only. Facts that come from the GitHub REST API and from the remote's ref list.
# Usage: bash collect_api.sh <repo_dir> <out_dir> [owner/repo]
# Needs: gh (authenticated for reading the repository), git, python3.
# The fork owner's name is never written; only whether the fork is public and when it was last pushed.
set -euo pipefail
dir="$1"; out="$2"; repo=${3:-wmqfl861/polymarket-alpha-lab}
mkdir -p "$out"
now() { date -u +%Y-%m-%dT%H:%MZ; }

opening() {
  gh api "repos/$repo" --jq '"visibility=\(.visibility) private=\(.private) forks=\(.forks_count) default_branch=\(.default_branch) pushed_at=\(.pushed_at)"'
  gh api "repos/$repo/branches/main" --jq '"main protected=\(.protected) sha=\(.commit.sha)"'
  echo "repository rulesets: $(gh api "repos/$repo/rulesets" --jq 'length'); rules in effect on main: $(gh api "repos/$repo/rules/branches/main" --jq 'length')"
  gh api "repos/$repo/forks?per_page=100" --jq '.[] | "fork: \(if .private then "private" else "public" end) (owner name omitted here), last pushed \(.pushed_at)"'
  refs=$(git -C "$dir" ls-remote origin)
  echo "server refs: heads=$(printf '%s\n' "$refs" | grep -c 'refs/heads/') tags=$(printf '%s\n' "$refs" | grep -c -E 'refs/tags/[^^]*$') pull_request_heads=$(printf '%s\n' "$refs" | grep -c -E 'refs/pull/[0-9]+/head$')"
  echo "open pull requests: $(gh api "repos/$repo/pulls?state=open&per_page=100" --jq '[.[] | "#\(.number)"] | join(" ")')"
  echo "workflow files on main: $(gh api "repos/$repo/git/trees/main?recursive=1" --jq '[.tree[] | select(.path | startswith(".github/"))] | length') (GitHub still lists $(gh api "repos/$repo/actions/workflows?per_page=100" --jq '.total_count') definitions from other branches and from history)"
}

if [ ! -s "$out/opening_checks.txt" ]; then
  { echo "# checked at $(now) through the GitHub API and git ls-remote"; opening; } > "$out/opening_checks.txt"
else
  { echo "# checked again at $(now)"; opening; } >> "$out/opening_checks.txt"
fi

{
  printf 'pr\tstate\tmerged_at\tmerge_commit\tbase_sha\thead_sha\tchanged_files\tadditions\tdeletions\thead_ref\ttitle\n'
  for n in $(seq 124 138); do
    gh api "repos/$repo/pulls/$n" --jq '["#\(.number)", (if .merged then "merged" else .state end), (.merged_at // "-"), (if .merged then .merge_commit_sha else "-" end), .base.sha, .head.sha, .changed_files, .additions, .deletions, .head.ref, .title] | @tsv'
  done
} > "$out/pr_metadata.tsv"

{
  echo "# workflow runs created on or after 2026-11-26T06:00:00Z (any branch, any event): $(gh api "repos/$repo/actions/runs?per_page=100&created=>=2026-11-26T06:00:00Z" --jq '.total_count')"
  echo "# the last runs on branch main (id | workflow | event | conclusion | started | head)"
  gh api "repos/$repo/actions/runs?per_page=12&branch=main" \
    --jq '.workflow_runs[] | [.id, .name, .event, .conclusion, .run_started_at, .head_sha[0:8]] | @tsv' \
    | awk -F'\t' '{print "  "$1" | "$2" | "$3" | "$4" | "$5" | "$6}'
  echo "# pytest summary line of the offline job on the last main commit that had runs"
  run=$(gh api "repos/$repo/actions/runs?per_page=50&branch=main&event=push" --jq '[.workflow_runs[] | select(.path == ".github/workflows/offline-verification.yml")][0].id')
  job=$(gh api "repos/$repo/actions/runs/$run/jobs" --jq '.jobs[0].id')
  log=$(mktemp)
  gh api "repos/$repo/actions/jobs/$job/logs" > "$log"
  echo "  run $run job $job head $(gh api "repos/$repo/actions/runs/$run" --jq '.head_sha')"
  grep -a -o -E '(Python 3\.[0-9]+\.[0-9]+|\+ pytest==[0-9.]+|\+ tzdata==[0-9.]+)' "$log" | sort -u | sed 's/^/  /'
  grep -a -o -E '[0-9]+ passed, [0-9]+ skipped[^(]*\([0-9:]+\)' "$log" | tail -1 | sed 's/^/  /'
  rm -f "$log"
} > "$out/ci_after_removal.txt"

echo "wrote: opening_checks.txt pr_metadata.tsv ci_after_removal.txt"
