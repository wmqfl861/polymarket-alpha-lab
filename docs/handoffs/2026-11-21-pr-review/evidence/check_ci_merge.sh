#!/usr/bin/env bash
# Read-only. For each pull request: which base commit did its latest pull_request CI run actually merge the head into,
# and is that the current main?  GitHub Actions checks out refs/pull/N/merge, and that merge commit is not rebuilt when
# main moves, so a green run can be a green run on an old base. The job log says which one it was.
#
#   bash check_ci_merge.sh 114 116 118
#
# Needs the gh CLI with read access to the repository. Prints one line per pull request:
#   CURRENT  the run merged the head into the main that is current now
#   STALE    the run merged into an older main (it names both commits); run the three workflows again on a refreshed branch
set -euo pipefail
REPO=wmqfl861/polymarket-alpha-lab
MAIN=$(gh api "repos/$REPO/git/ref/heads/main" --jq .object.sha)
echo "main now: $MAIN"
for pr in "$@"; do
  head=$(gh api "repos/$REPO/pulls/$pr" --jq .head.sha)
  run=$(gh api "repos/$REPO/actions/runs?head_sha=$head&event=pull_request&per_page=50" \
    --jq '[.workflow_runs[] | select(.name=="Offline verification")] | sort_by(.created_at) | last | .id')
  if [ -z "$run" ] || [ "$run" = null ]; then
    echo "#$pr head ${head:0:8}: no pull_request run of Offline verification"; continue
  fi
  job=$(gh api "repos/$REPO/actions/runs/$run/jobs" --jq '.jobs[0].id')
  line=$(gh api "repos/$REPO/actions/jobs/$job/logs" | grep -m1 -oE 'HEAD is now at [0-9a-f]{7,40} Merge [0-9a-f]{40} into [0-9a-f]{7,40}' || true)
  base=${line##* into }
  if [ -z "$base" ]; then echo "#$pr head ${head:0:8} run $run: no merge checkout line in the log"; continue; fi
  case "$MAIN" in
    "$base"*) echo "#$pr head ${head:0:8} run $run: CURRENT (merged into ${base:0:8})" ;;
    *)        echo "#$pr head ${head:0:8} run $run: STALE (merged into ${base:0:8}, main is ${MAIN:0:8})" ;;
  esac
done
