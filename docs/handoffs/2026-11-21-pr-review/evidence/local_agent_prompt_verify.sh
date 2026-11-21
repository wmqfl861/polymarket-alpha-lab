set -euo pipefail
cd "$(mktemp -d)"
git init -q
git fetch --no-tags https://github.com/wmqfl861/polymarket-alpha-lab.git \
  refs/heads/main:refs/audit/main \
  refs/heads/handoff/optimizer-review-2026-11-21:refs/audit/package \
  refs/heads/handoff/progress-review-2026-11-21:refs/audit/findings \
  refs/pull/114/head:refs/audit/pr114 \
  refs/pull/116/head:refs/audit/pr116 \
  refs/pull/118/head:refs/audit/pr118

base=2bdc4cccacc925ea8e6569c8865b90c0b0c671e6
pkg=09cf06f8ff57382b121470ad67c18994204db02b
dir=docs/handoffs/2026-11-21-optimizer-review
git merge-base --is-ancestor "$base" refs/audit/main
git merge-base --is-ancestor "$pkg" refs/audit/package
test "$(git rev-parse refs/audit/findings)" = 5e4033c4a7820c0f7bea85c54923f46bc376fdc3
test "$(git rev-parse refs/audit/pr114)" = 26dfa2a05357121665009981cd600e23c91e093a
test "$(git rev-parse refs/audit/pr116)" = 8c7b536514eda6dfc772bf7fc6a4fc597892a294
test "$(git rev-parse refs/audit/pr118)" = f09850ba8ef010895d6a959cbeff401fbc706363

git cat-file blob "$pkg:$dir/MANIFEST.tsv" > MANIFEST.tsv
test "$(wc -c < MANIFEST.tsv)" -eq 252
test "$(wc -l < MANIFEST.tsv)" -eq 3
printf '%s  MANIFEST.tsv\n' \
  8919f34523e26ea172bb6001c353e601ce3988fd3d900e3cf668b82374b60303 | sha256sum -c -
while IFS=$'\t' read -r path bytes digest; do
  test "$(git cat-file -s "$pkg:$dir/$path")" -eq "$bytes"
  test "$(git cat-file blob "$pkg:$dir/$path" | sha256sum | cut -d ' ' -f 1)" = "$digest"
done < MANIFEST.tsv
git show "$pkg:$dir/README.md"
git show "$pkg:$dir/evidence.md"
