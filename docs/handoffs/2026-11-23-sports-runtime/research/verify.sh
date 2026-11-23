#!/usr/bin/env bash
set -euo pipefail

research_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_url=https://github.com/wmqfl861/polymarket-alpha-lab.git
published_main=ab6edff2d64ac8aa5c2831d748b8730b63f8312d

verification_dir=$(mktemp -d "${TMPDIR:-/tmp}/papersim-research.XXXXXXXX")
trap 'rm -rf -- "$verification_dir"' EXIT

public_git() {
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
        git -c credential.helper= "$@"
}

repo="$verification_dir/repo"
checkout="$verification_dir/checkout"
public_git clone --quiet --no-checkout --no-tags --single-branch --branch main -- "$repo_url" "$repo"
public_git -C "$repo" merge-base --is-ancestor "$published_main" refs/remotes/origin/main

{
    read -r header
    verified=0
    while IFS=$'\t' read -r patch_name source_ref source_commit public_base target_tree expected_checksum
    do
        patch_file="$research_dir/$patch_name"
        checksum=$(sha256sum < "$patch_file")
        if [[ ${checksum%% *} != "$expected_checksum" ]]; then
            printf 'SHA-256 mismatch for %s\n' "$patch_name" >&2
            exit 1
        fi
        public_git -C "$repo" merge-base --is-ancestor "$public_base" "$published_main"
        public_git -C "$repo" worktree add --quiet --detach "$checkout" "$public_base"
        public_git -C "$checkout" apply --check --index "$patch_file"
        public_git -C "$checkout" apply --index "$patch_file"
        restored_tree=$(public_git -C "$checkout" write-tree)
        if [[ "$restored_tree" != "$target_tree" ]]; then
            printf 'Tree mismatch for %s\nExpected %s\nRestored %s\n' "$patch_name" "$target_tree" "$restored_tree" >&2
            exit 1
        fi
        printf '%s %s %s\n' "$patch_name" "$public_base" "$restored_tree"
        public_git -C "$repo" worktree remove --force "$checkout"
        verified=$((verified + 1))
    done
} < "$research_dir/sources.tsv"

if [[ "$verified" -ne 3 ]]; then
    printf 'Expected 3 patches, verified %s\n' "$verified" >&2
    exit 1
fi
