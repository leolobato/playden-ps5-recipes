# SPDX-License-Identifier: CC0-1.0
"""Tests for scripts/release_notes.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import release_notes


def index(*games):
    """games: (name, [(recipe_id, revision[, author[, contributors]]), ...])"""
    def recipe(rid, rev, author="", contributors=()):
        return {"id": rid, "revision": rev, "author": author, "contributors": list(contributors)}
    return {"format_version": 2, "games": [
        {"store": "steam", "key": str(n), "name": name, "recipes": [recipe(*member) for member in members]}
        for n, (name, members) in enumerate(games)]}


OLD = index(("Portal", [("community-steam-400-a", "r1", "alice")]),
            ("Half-Life", [("community-steam-70-b", "r1"), ("community-steam-70-c", "r1", "carol")]))
NEW = index(("Portal", [("community-steam-400-a", "r2", "alice", ["bob"])]),
            ("Half-Life", [("community-steam-70-b", "r1")]),
            ("Cyberpunk 2077", [("community-steam-1091500-d", "r1", "dave")]))


class ChangesTest(unittest.TestCase):
    def test_groups_by_id_and_revision(self):
        added, updated, removed = release_notes.changes(OLD, NEW)
        self.assertEqual(added, [("Cyberpunk 2077", "community-steam-1091500-d", "by dave")])
        self.assertEqual(updated, [("Portal", "community-steam-400-a", "by alice, with bob")])
        self.assertEqual(removed, [("Half-Life", "community-steam-70-c", "by carol")])

    def test_without_old_index_everything_is_added(self):
        added, updated, removed = release_notes.changes(None, NEW)
        self.assertEqual([i for _, i, _ in added],
                         ["community-steam-1091500-d", "community-steam-70-b", "community-steam-400-a"])
        self.assertEqual((updated, removed), ([], []))

    def test_removed_uses_the_old_game_name(self):
        renamed = index(("Half-Life 1", [("community-steam-70-b", "r1")]))
        _, _, removed = release_notes.changes(OLD, renamed)
        self.assertIn(("Half-Life", "community-steam-70-c", "by carol"), removed)


class NotesTest(unittest.TestCase):
    def test_markdown(self):
        self.assertEqual(release_notes.notes(OLD, NEW, since="r20261108.1"), "\n".join([
            "## What's new since r20261108.1",
            "",
            "### Added",
            "",
            "- Cyberpunk 2077: `community-steam-1091500-d` by dave",
            "",
            "### Updated",
            "",
            "- Portal: `community-steam-400-a` by alice, with bob",
            "",
            "### Removed",
            "",
            "- Half-Life: `community-steam-70-c` by carol",
        ]) + "\n")

    def test_credit(self):
        self.assertEqual(release_notes.credit({"author": "alice"}), "by alice")
        self.assertEqual(release_notes.credit({"author": "alice", "contributors": ["bob", "carol"]}),
                         "by alice, with bob and carol")
        self.assertEqual(release_notes.credit({"author": "alice", "contributors": ["alice"]}), "by alice")
        self.assertEqual(release_notes.credit({"contributors": ["bob"]}), "by bob")
        self.assertEqual(release_notes.credit({"author": "", "contributors": []}), "")
        self.assertEqual(release_notes.credit({}), "")

    def test_a_recipe_without_author_has_no_credit(self):
        self.assertIn("- Half-Life: `community-steam-70-b`\n", release_notes.notes(None, NEW))

    def test_empty_groups_are_left_out(self):
        text = release_notes.notes(OLD, OLD)
        self.assertEqual(text, "## What's new\n\nNo recipe changes.\n")

    def test_main_reads_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp, "old.json"), Path(tmp, "new.json")
            old.write_text(json.dumps(OLD), encoding="utf-8")
            new.write_text(json.dumps(NEW), encoding="utf-8")
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(release_notes.main(["--old", str(old), "--new", str(new)]), 0)
            self.assertIn("### Removed", out.getvalue())

    def test_main_refuses_a_missing_file(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(release_notes.main(["--new", "/nonexistent/index.json"]), 1)


if __name__ == "__main__":
    unittest.main()
