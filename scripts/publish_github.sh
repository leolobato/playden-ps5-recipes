#!/usr/bin/env bash
# SPDX-License-Identifier: CC0-1.0
# Publishes a recipes release on GitHub, signed with your own GPG key. Run it through the Makefile:
#
#   make release-dry-run    builds and shows what the release would record; changes nothing
#   make release            records the lists, signs and pushes the next r<YYYYMMDD>.<n> tag
#
# From a clean `main` that matches GitHub's, it builds the release (scripts/build_release.py, which checks every
# recipe), commits revisions.lst and slugs.lst (signed, `git commit -S`), makes a signed tag (`git tag -s`) and
# pushes the branch and the tag together. The Recipes workflow then builds recipes.zip and index.json from the tag
# and attaches them to a new GitHub release; Playden and the API read the latest release.
#
# Environment: TAG (default: the next free r<UTC date>.<n>), CHECKER (path to Playden's community_recipe_check),
# DRY_RUN=1, REMOTE (default origin; it must be the GitHub repository).
set -euo pipefail

REMOTE="${REMOTE:-origin}"
BRANCH=main
REPOSITORY=leolobato/playden-ps5-recipes
LISTS=(revisions.lst slugs.lst)

fail() { echo "release: $*" >&2; exit 1; }

cd "$(git rev-parse --show-toplevel)"

url="$(git remote get-url "$REMOTE" 2>/dev/null)" || fail "no remote named $REMOTE"
case "$url" in
  *github.com[:/]"$REPOSITORY" | *github.com[:/]"$REPOSITORY".git) ;;
  *) fail "remote $REMOTE is $url, not github.com/$REPOSITORY" ;;
esac

[ "$(git symbolic-ref --short -q HEAD || true)" = "$BRANCH" ] || fail "check out $BRANCH first (releases are tagged on $BRANCH)"
[ -z "$(git status --porcelain --untracked-files=normal)" ] || fail "the working tree has uncommitted changes"

git fetch --quiet --tags "$REMOTE" "$BRANCH"
local_head="$(git rev-parse HEAD)"
remote_head="$(git rev-parse "$REMOTE/$BRANCH")"
if [ "$local_head" != "$remote_head" ]; then
  if git merge-base --is-ancestor HEAD "$REMOTE/$BRANCH"; then fail "$BRANCH is behind $REMOTE/$BRANCH: git pull --ff-only first"; fi
  fail "$BRANCH has commits that aren't on $REMOTE/$BRANCH: push them (or reset) first"
fi

last="$(git tag --list 'r*' --sort=-creatordate | grep -E '^r[0-9]{8}\.[0-9]+$' | head -n1 || true)"
if [ -n "$last" ] && [ "$(git rev-list -n1 "$last")" = "$local_head" ]; then
  fail "$BRANCH is already released as $last; nothing new to publish"
fi

if [ -z "${TAG:-}" ]; then
  day="r$(date -u +%Y%m%d)"
  n=1
  while git rev-parse -q --verify "refs/tags/$day.$n" >/dev/null; do n=$((n + 1)); done
  TAG="$day.$n"
fi
[[ "$TAG" =~ ^r[0-9]{8}\.[0-9]+$ ]] || fail "tag $TAG must look like r<YYYYMMDD>.<n>, e.g. r20261108.1"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && fail "tag $TAG already exists"
[ -z "$(git ls-remote --tags "$REMOTE" "refs/tags/$TAG")" ] || fail "tag $TAG already exists on $REMOTE"

if [ -n "$last" ]; then
  echo "Since $last:"
  git log --oneline --no-decorate "$last..HEAD" | sed 's/^/  /'
fi

out="$(mktemp -d "${TMPDIR:-/tmp}/recipes-release.XXXXXX")"
trap 'rm -rf "$out"' EXIT
python3 scripts/build_release.py --out "$out" ${CHECKER:+--checker "$CHECKER"}

if [ "${DRY_RUN:-0}" = 1 ]; then
  # A preview of the notes CI writes: this build's index.json against the last release's.
  notes_args=()
  if [ -n "$last" ]; then
    notes_args+=(--since "$last")
    if command -v gh >/dev/null && gh release download "$last" --repo "$REPOSITORY" --pattern index.json \
        --dir "$out/prev" >/dev/null 2>&1; then
      notes_args+=(--old "$out/prev/index.json")
    else
      echo "(couldn't download index.json from $last; the notes list every recipe as added)"
    fi
  fi
  echo
  python3 scripts/release_notes.py --new "$out/index.json" ${notes_args[@]+"${notes_args[@]}"}
  echo
  git --no-pager diff --stat -- "${LISTS[@]}"
  git --no-pager diff -U0 -- "${LISTS[@]}" | grep -E '^\+[^+]' || echo "(the lists don't change)"
  git checkout --quiet -- "${LISTS[@]}"
  echo "dry run: would commit the lists, tag $TAG (both signed) and push $BRANCH and $TAG to $REMOTE"
  exit 0
fi

if [ -n "$(git status --porcelain -- "${LISTS[@]}")" ]; then
  git add -- "${LISTS[@]}"
  git commit --quiet -S -m "Record the recipes released in $TAG"
  echo "committed the lists ($(git rev-parse --short HEAD), signed)"
fi
git tag -s -m "Recipes $TAG" "$TAG"
git tag -v "$TAG" >/dev/null 2>&1 || fail "tag $TAG has no valid signature; delete it with: git tag -d $TAG"
git push --atomic "$REMOTE" "refs/heads/$BRANCH:refs/heads/$BRANCH" "refs/tags/$TAG:refs/tags/$TAG"

echo "pushed $BRANCH and $TAG. The Recipes workflow attaches the files; follow it with: make release-watch TAG=$TAG"
echo "https://github.com/$REPOSITORY/releases/tag/$TAG"
