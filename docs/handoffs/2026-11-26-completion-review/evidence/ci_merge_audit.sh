#!/usr/bin/env bash
# Read-only. For every pull request merged into main after BASE, report:
#   - the PR head and the merge commit's first parent (the main it was merged onto)
#   - every workflow run on the PR head (name, event, conclusion)
#   - for each pull_request run: the merge commit its first job checked out ("Merge <head> into <base>")
#     and whether <base> equals the first parent of the real merge (CURRENT) or an older main (STALE)
#   - the push runs on the merge commit itself
#
#   bash ci_merge_audit.sh <repo_dir> <base_commit> <tip_commit>
#
# Needs the gh CLI with read access. Output is tab separated.
set -euo pipefail
dir="$1"; base="$2"; tip="$3"
REPO=wmqfl861/polymarket-alpha-lab
printf 'pr\tmerge_commit\tfirst_parent\thead\tworkflow\tevent\trun_id\tattempt\tconclusion\tchecked_out\tbase_of_checkout\tverdict\n'
for m in $(git -C "$dir" rev-list --first-parent --merges --reverse "$base..$tip"); do
  subj=$(git -C "$dir" log -1 --format=%s "$m")
  pr=$(printf '%s' "$subj" | sed -nE 's/^Merge pull request #([0-9]+) .*/\1/p')
  [ -n "$pr" ] || { printf -- '-\t%s\t-\t-\t(not a PR merge: %s)\n' "${m:0:8}" "$subj"; continue; }
  p1=$(git -C "$dir" rev-parse "$m^1"); head=$(git -C "$dir" rev-parse "$m^2")
  gh api "repos/$REPO/actions/runs?head_sha=$head&per_page=100" \
    --jq '.workflow_runs[] | [.name, .event, .id, .run_attempt, (.conclusion // .status)] | @tsv' | sort |
  while IFS=$'\t' read -r name event run attempt concl; do
    checked='-'; cbase='-'; verdict='-'
    if [ "$event" = pull_request ]; then
      job=$(gh api "repos/$REPO/actions/runs/$run/jobs" --jq '.jobs[0].id')
      line=$(gh api "repos/$REPO/actions/jobs/$job/logs" 2>/dev/null | grep -m1 -oE 'HEAD is now at [0-9a-f]{7,40} Merge [0-9a-f]{40} into [0-9a-f]{7,40}' || true)
      if [ -n "$line" ]; then
        checked=$(printf '%s' "$line" | awk '{print $5}'); cbase=${line##* into }
        case "$p1" in "$cbase"*) verdict=CURRENT ;; *) verdict=STALE ;; esac
      else
        verdict=NO_MERGE_LINE
      fi
    fi
    printf '#%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$pr" "${m:0:8}" "${p1:0:8}" "${head:0:8}" "$name" "$event" "$run" "$attempt" "$concl" "$checked" "${cbase:0:8}" "$verdict"
  done
  gh api "repos/$REPO/actions/runs?head_sha=$m&per_page=100" \
    --jq '.workflow_runs[] | [.name, .event, .id, .run_attempt, (.conclusion // .status)] | @tsv' | sort |
  while IFS=$'\t' read -r name event run attempt concl; do
    printf '#%s\t%s\t%s\t(merge commit)\t%s\t%s\t%s\t%s\t%s\t-\t-\t-\n' "$pr" "${m:0:8}" "${p1:0:8}" "$name" "$event" "$run" "$attempt" "$concl"
  done
done
