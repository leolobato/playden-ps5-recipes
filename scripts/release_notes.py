#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Write the "What's new" section of a release's notes.

Usage:
  scripts/release_notes.py --new NEW_INDEX [--old OLD_INDEX] [--since TAG]

Compares two index.json files (the previous release's and this one's) by recipe id:
  Added    recipe ids that are only in the new index
  Updated  recipe ids in both whose revision changed
  Removed  recipe ids that are only in the old index
Each line names the game, the recipe id and who made it (the author, then any contributors). Empty groups are left
out. Without --old (no previous release, or it has no index.json), every recipe counts as added.
Prints Markdown to stdout. Python 3 standard library only.
"""

import argparse
import json
import sys


def credit(recipe):
    """"by author, with a and b" from an index.json recipe; "" when it names nobody."""
    author = (recipe.get("author") or "").strip()
    others = [c.strip() for c in recipe.get("contributors") or [] if isinstance(c, str)]
    others = [c for c in others if c and c != author]
    if not author and not others:
        return ""
    if not author:
        return "by " + join(others)
    return "by " + author + (", with " + join(others) if others else "")


def join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def recipes(index):
    """{recipe_id: (game name, revision, credit)} from a parsed index.json."""
    found = {}
    for game in index.get("games", []):
        for recipe in game.get("recipes", []):
            found[recipe["id"]] = (game.get("name", ""), recipe.get("revision", ""), credit(recipe))
    return found


def changes(old, new):
    """(added, updated, removed): lists of (game name, recipe id, credit), sorted by game name then id. Removed recipes
    take the game name and credit from the old index."""
    before, after = recipes(old or {}), recipes(new)
    added = [(after[i][0], i, after[i][2]) for i in after if i not in before]
    updated = [(after[i][0], i, after[i][2]) for i in after if i in before and before[i][1] != after[i][1]]
    removed = [(before[i][0], i, before[i][2]) for i in before if i not in after]
    order = lambda item: (item[0].casefold(), item[1])
    return sorted(added, key=order), sorted(updated, key=order), sorted(removed, key=order)


def notes(old, new, since=None):
    """The Markdown section."""
    added, updated, removed = changes(old, new)
    lines = ["## What's new" + (" since %s" % since if since else "")]
    if not (added or updated or removed):
        lines += ["", "No recipe changes."]
    for heading, items in (("Added", added), ("Updated", updated), ("Removed", removed)):
        if items:
            lines += ["", "### %s" % heading, ""]
            lines += ["- %s: `%s`%s" % (name or "(unnamed game)", recipe_id, " " + who if who else "")
                      for name, recipe_id, who in items]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--new", required=True, help="this release's index.json")
    parser.add_argument("--old", help="the previous release's index.json")
    parser.add_argument("--since", help="the previous release's tag, named in the heading")
    args = parser.parse_args(argv)
    try:
        with open(args.new, encoding="utf-8") as f:
            new = json.load(f)
        old = None
        if args.old:
            with open(args.old, encoding="utf-8") as f:
                old = json.load(f)
    except (OSError, ValueError) as error:
        print("release_notes: %s" % error, file=sys.stderr)
        return 1
    sys.stdout.write(notes(old, new, args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
