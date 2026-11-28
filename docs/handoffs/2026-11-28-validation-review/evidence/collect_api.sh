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
  echo "server refs: heads=$(printf '%s\n' "$refs" | grep -c 'refs/heads/') tags=$(printf '%s\n' "$refs" | grep -c -E 'refs/tags/[^^]*$') pull_request_heads=$(printf '%s\n' "$refs" | grep -c -E 'refs/pull/[0-9]+/head$') handoff_branches=$(printf '%s\n' "$refs" | grep -c 'refs/heads/handoff/')"
  echo "open pull requests: $(gh api "repos/$repo/pulls?state=open&per_page=100" --jq '[.[] | "#\(.number)"] | join(" ")')"
  echo "workflow files on main: $(gh api "repos/$repo/git/trees/main?recursive=1" --jq '[.tree[] | select(.path | startswith(".github/"))] | length'); workflow runs created on or after 2026-11-26T12:00:00Z: $(gh api "repos/$repo/actions/runs?per_page=1&created=>=2026-11-26T12:00:00Z" --jq '.total_count')"
}

if [ ! -s "$out/opening_checks.txt" ]; then
  { echo "# checked at $(now) through the GitHub API and git ls-remote"; opening; } > "$out/opening_checks.txt"
else
  { echo "# checked again at $(now)"; opening; } >> "$out/opening_checks.txt"
fi

{
  printf 'pr\tstate\tclosed_at\thead_sha\thead_ref\n'
  for n in 34 35 41 131; do
    gh api "repos/$repo/pulls/$n" --jq '["#\(.number)", (if .merged then "merged" else .state end), (.closed_at // "-"), .head.sha, .head.ref] | @tsv'
  done
} > "$out/pr_states.tsv"

echo "wrote: opening_checks.txt pr_states.tsv"
