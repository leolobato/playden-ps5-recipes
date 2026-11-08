# SPDX-License-Identifier: CC0-1.0
# Recipes releases on GitHub, signed with your GPG key (scripts/publish_github.sh). Run from a clean, up-to-date main.
#
#   make release-dry-run              what the next release would record; changes nothing
#   make release                      commit the lists, sign and push the next r<YYYYMMDD>.<n> tag
#   make release TAG=r20261108.2      the same with a chosen tag
#   make release-watch [TAG=...]      follow the workflow that attaches recipes.zip and index.json
#   make test                         the scripts' unit tests
#
# CHECKER=path passes Playden's community_recipe_check to the build.

TAG ?=
CHECKER ?=
export TAG CHECKER

.PHONY: release release-dry-run release-watch test

release-dry-run:
	@DRY_RUN=1 scripts/publish_github.sh

release:
	@scripts/publish_github.sh

# The Recipes run for the tag (the latest r* tag without TAG), then the release it made.
release-watch:
	@tag="$${TAG:-$$(git tag --list 'r*' --sort=-creatordate | head -n1)}"; \
	id=""; for i in 1 2 3 4 5 6; do \
	  id="$$(gh run list --workflow recipes.yml --branch "$$tag" --limit 1 --json databaseId --jq '.[0].databaseId // empty')"; \
	  [ -n "$$id" ] && break; sleep 5; \
	done; \
	[ -n "$$id" ] || { echo "no Recipes run for $$tag yet" >&2; exit 1; }; \
	gh run watch "$$id" --exit-status && gh release view "$$tag" --json tagName,url,assets --jq '"\(.url)\n" + ([.assets[].name] | join(", "))'

test:
	@python3 -m unittest discover -s scripts -p 'test_*.py'
