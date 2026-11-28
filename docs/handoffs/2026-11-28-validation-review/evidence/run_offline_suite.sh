#!/usr/bin/env bash
# Runs the repository's offline pytest suite in a detached worktree with a clean
# environment (the same variables scripts/verify_local.py sets), writing a log
# and a JUnit file outside the worktree. Nothing in the repository is modified.
# Usage: bash run_offline_suite.sh <worktree> <label> <python> <out_dir>
set -uo pipefail
worktree=$1; label=$2; python=$3; out=$4
cd "$worktree"
start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
env -i HOME=/home/claude/r7/run/home PATH=/usr/bin:/bin LANG=C.UTF-8 \
  PYTHONUTF8=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 POLYMARKET_ALPHA_LAB_RUN_SUPABASE_SMOKE=0 \
  "$python" -m pytest -q -ra -p no:cacheprovider --junitxml="$out/junit_$label.xml" \
  > "$out/pytest_$label.log" 2>&1
code=$?
end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
{
  echo "label=$label commit=$(git rev-parse HEAD) python=$("$python" -c 'import sys; print(sys.version.split()[0])') pytest=$("$python" -c 'import pytest; print(pytest.__version__)')"
  echo "start=$start end=$end exit=$code"
  echo "tracked files changed by the run: $(git status --porcelain | wc -l)"
} > "$out/pytest_$label.meta"
