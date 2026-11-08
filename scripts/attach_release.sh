#!/usr/bin/env bash
# SPDX-License-Identifier: CC0-1.0
# Builds recipes.zip and index.json from a release tag and attaches them to its GitHub release, with notes that list
# what's new since the previous release. CI runs it from both workflows: Recipes (a tag pushed by `make release`) and
# Release (started by hand or from the admin page, which pushes its tag with a token that starts no other workflow).
#
#   scripts/attach_release.sh r20261108.1
#
# The tag must be checked out. Needs gh with GH_TOKEN. OUT sets the build folder (default: out).
set -euo pipefail

tag="${1:?usage: scripts/attach_release.sh <tag>}"
out="${OUT:-out}"
cd "$(git rev-parse --show-toplevel)"

[ "$(git rev-parse HEAD)" = "$(git rev-list -n1 "$tag")" ] || { echo "::error::check out $tag before attaching its files"; exit 1; }

# The release time is the tagged commit's, so a rebuild gives the same index.json. revisions.lst and slugs.lst are
# committed before tagging; a change here means the tag was made without them.
python3 scripts/build_release.py --out "$out" --now "$(git log -1 --format=%ct)"
if ! git diff --quiet -- revisions.lst slugs.lst || [ -n "$(git ls-files --others -- revisions.lst slugs.lst)" ]; then
  echo "::warning::revisions.lst or slugs.lst weren't updated before tagging; run scripts/build_release.py and commit them first"
fi

# What's new: this index.json against the one attached to the previous r* tag (the nearest one before this commit).
# Without a previous release, or if it has no index.json, every recipe counts as added.
prev="$(git describe --tags --abbrev=0 --match 'r[0-9]*' "$tag^" 2>/dev/null || true)"
args=()
if [ -n "$prev" ]; then
  args+=(--since "$prev")
  if gh release download "$prev" --pattern index.json --dir "$out/prev" --clobber >/dev/null 2>&1; then
    args+=(--old "$out/prev/index.json")
  else
    echo "::warning::couldn't download index.json from $prev; listing every recipe as added"
  fi
fi
{
  echo "Community recipes. Playden downloads index.json and recipes.zip from the latest release."
  echo
  python3 scripts/release_notes.py --new "$out/index.json" ${args[@]+"${args[@]}"}
} > "$out/notes.md"
cat "$out/notes.md"

if gh release view "$tag" >/dev/null 2>&1; then
  gh release edit "$tag" --notes-file "$out/notes.md"
else
  gh release create "$tag" --verify-tag --title "Recipes $tag" --notes-file "$out/notes.md"
fi
gh release upload "$tag" "$out/recipes.zip" "$out/index.json" --clobber
