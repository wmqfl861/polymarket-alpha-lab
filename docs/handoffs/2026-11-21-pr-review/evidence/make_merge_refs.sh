#!/usr/bin/env bash
# Read-only. Builds, in a fresh scratch repository, the merge results the scans in this directory use,
# and reports which merges conflict. Nothing is written to GitHub or to any existing repository.
#
#   bash make_merge_refs.sh
#
# m_main+N      = the pull request's head merged into the pinned main (what a merge commit would contain today)
# m_A+B         = two pull requests merged together (shows whether they can land in either order without work)
# Conflicts are reported with the conflicting paths; no merge commit is built for them.
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
MAIN=2bdc4cccacc925ea8e6569c8865b90c0b0c671e6
H114=26dfa2a05357121665009981cd600e23c91e093a
H116=8c7b536514eda6dfc772bf7fc6a4fc597892a294
H118=f09850ba8ef010895d6a959cbeff401fbc706363

cd "$(mktemp -d)"
git init -q
git config user.email audit@example.invalid
git config user.name audit
git fetch -q --no-tags "$REPO" \
  refs/heads/main:refs/audit/main \
  refs/pull/114/head:refs/audit/pr114 refs/pull/116/head:refs/audit/pr116 refs/pull/118/head:refs/audit/pr118
test "$(git rev-parse refs/audit/pr114)" = "$H114"
test "$(git rev-parse refs/audit/pr116)" = "$H116"
test "$(git rev-parse refs/audit/pr118)" = "$H118"
git merge-base --is-ancestor "$MAIN" refs/audit/main
git update-ref refs/audit/base "$MAIN"

mk() { # name parent1 parent2
  local tree
  if tree=$(git merge-tree --write-tree --no-messages "$2" "$3"); then
    git update-ref "refs/audit/$1" "$(git commit-tree "$tree" -p "$2" -p "$3" -m "merge $1")"
    echo "CLEAN     $1  (tree $(git rev-parse --short "refs/audit/$1^{tree}"))"
  else
    echo "CONFLICT  $1  paths: $(git merge-tree --write-tree --name-only --no-messages "$2" "$3" | tail -n +2 | tr '\n' ' ')"
  fi
}
mk m_main+114 refs/audit/base refs/audit/pr114
mk m_main+116 refs/audit/base refs/audit/pr116
mk m_main+118 refs/audit/base refs/audit/pr118
mk m_114+116 refs/audit/pr114 refs/audit/pr116
mk m_116+118 refs/audit/pr116 refs/audit/pr118
mk m_114+118 refs/audit/pr114 refs/audit/pr118
echo "merge-base(main, pr114)=$(git merge-base refs/audit/base refs/audit/pr114 | cut -c1-8)  (pr116)=$(git merge-base refs/audit/base refs/audit/pr116 | cut -c1-8)  (pr118)=$(git merge-base refs/audit/base refs/audit/pr118 | cut -c1-8)"
echo "scratch repository: $(pwd)"
