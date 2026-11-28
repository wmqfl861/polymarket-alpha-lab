#!/usr/bin/env bash
# The compile step of the repository's scripts/verify_local.py (python -m compileall -q src tests scripts),
# run with the byte-code cache redirected outside the worktree so that nothing is written into it.
# Usage: bash run_compile_step.sh <worktree> <python>
set -uo pipefail
worktree=$1; python=$2
cd "$worktree"
cache=$(mktemp -d)
env -i HOME=/home/claude/r7/run/home PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHONUTF8=1 PYTHONPYCACHEPREFIX="$cache" \
  "$python" -m compileall -q src tests scripts
code=$?
rm -rf "$cache"
echo "compileall commit=$(git rev-parse HEAD) exit=$code tracked files changed: $(git status --porcelain | wc -l)"
