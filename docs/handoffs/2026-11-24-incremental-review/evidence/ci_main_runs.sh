#!/usr/bin/env bash
# Read-only. Lists the workflow runs on main and the whole-run durations of the
# offline workflow since a given date, through the GitHub REST API.
# Usage: bash ci_main_runs.sh [owner/repo] [since-date]
# Needs: gh (authenticated for reading the repository), python3.
set -euo pipefail
repo=${1:-wmqfl861/polymarket-alpha-lab}
since=${2:-2026-11-21}

echo "# workflow runs on branch main created on or after $since (id | event | conclusion | started | head)"
for workflow in offline-verification.yml native-postgres.yml research-paper.yml; do
  echo "## $workflow"
  gh api "repos/$repo/actions/workflows/$workflow/runs?per_page=100&branch=main&created=>=$since" \
    --jq '.workflow_runs[] | [.id, .event, .conclusion, .run_started_at, .head_sha[0:8]] | @tsv' \
    | sort -t "$(printf '\t')" -k4 \
    | awk -F'\t' '{print "  "$1" | "$2" | "$3" | "$4" | "$5}'
done

echo
echo "# offline-verification.yml, every run created on or after $since: whole-run minutes (queue time included)"
gh api "repos/$repo/actions/workflows/offline-verification.yml/runs?per_page=100&created=>=$since" \
  --jq '.workflow_runs[] | [.id, .event, .conclusion, .run_started_at, .updated_at, .head_sha[0:8]] | @tsv' \
  | python3 -I -c '
import datetime
import sys

rows = []
for line in sys.stdin:
    run, event, conclusion, started, updated, head = line.rstrip("\n").split("\t")
    begin = datetime.datetime.fromisoformat(started.replace("Z", "+00:00"))
    end = datetime.datetime.fromisoformat(updated.replace("Z", "+00:00"))
    rows.append(((end - begin).total_seconds() / 60, run, event, conclusion, started, head))
for minutes, run, event, conclusion, started, head in sorted(rows, key=lambda row: row[4]):
    print(f"  {minutes:5.1f} | {run} | {event} | {conclusion} | {started} | {head}")
finished = [row[0] for row in rows if row[3] == "success"]
if finished:
    print(f"  successful runs: {len(finished)}; shortest {min(finished):.1f} min; longest {max(finished):.1f} min")
'
