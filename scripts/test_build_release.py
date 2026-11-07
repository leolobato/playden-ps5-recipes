# SPDX-License-Identifier: CC0-1.0
"""Tests for scripts/build_release.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import build_release
from recipe_testing import FAKE_CHECKER, TempRepo

NOW = 1792684800  # 2026-10-22T16:00:00Z
INDEX_ENTRY_KEYS = {"id", "title", "status", "author", "contributors", "tested_kit", "min_kit", "tested_firmware",
                    "tested_date", "revision"}


class BuildTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.out = tempfile.TemporaryDirectory(prefix="recipes-out-")
        self.repo.place("good.json")
        self.repo.place("good.json", key="1592280", slug="mouse-preset")
        self.repo.place("good.json", key="1592280", slug="gamepad")

    def tearDown(self):
        self.repo.close()
        self.out.cleanup()

    def build(self, out=None, **kwargs):
        kwargs.setdefault("now", NOW)
        return build_release.build(self.repo.root, out or self.out.name, **kwargs)

    def output(self, name, out=None):
        return (Path(out or self.out.name) / name).read_bytes()


class ZipTest(BuildTestCase):
    def test_two_builds_are_byte_identical(self):
        self.build(update_lists=False)
        with tempfile.TemporaryDirectory() as other:
            self.build(out=other, now=NOW + 3600, update_lists=False)
            self.assertEqual(self.output("recipes.zip"), self.output("recipes.zip", other))

    def test_zip_layout(self):
        self.build()
        with zipfile.ZipFile(str(Path(self.out.name) / "recipes.zip")) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            self.assertEqual(names, sorted(names))
            self.assertEqual(names, ["recipes/steam/1592280/gamepad.json", "recipes/steam/1592280/mouse-preset.json",
                                     "recipes/steam/274520/120hz-left-stick-pointer.json"])
            for info in infos:
                self.assertFalse(info.is_dir())
                self.assertEqual(info.date_time, build_release.ZIP_TIME)
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                # The zip holds the repository's bytes, so the revision in the index matches.
                self.assertEqual(archive.read(info), (self.repo.root / info.filename).read_bytes())


class IndexTest(BuildTestCase):
    def test_index_schema(self):
        self.build()
        index = json.loads(self.output("index.json"))
        self.assertEqual(set(index), {"format_version", "generated_at", "zip_sha256", "games"})
        self.assertEqual(index["format_version"], 2)
        self.assertEqual(index["generated_at"], "2026-10-22T16:00:00Z")
        self.assertEqual(index["zip_sha256"], hashlib.sha256(self.output("recipes.zip")).hexdigest())
        self.assertEqual([(g["store"], g["key"]) for g in index["games"]], [("steam", "274520"), ("steam", "1592280")])
        for game in index["games"]:
            self.assertEqual(set(game), {"store", "key", "name", "recipes"})
            self.assertEqual(game["name"], "Darkwood")
        self.assertEqual([e["id"] for e in index["games"][1]["recipes"]],
                         ["community-steam-1592280-gamepad", "community-steam-1592280-mouse-preset"])
        entry = index["games"][0]["recipes"][0]
        self.assertEqual(set(entry), INDEX_ENTRY_KEYS)
        self.assertEqual(entry, {
            "id": "community-steam-274520-120hz-left-stick-pointer",
            "title": "120 Hz, pointer on left stick",
            "status": "works",
            "author": "TodorPenchev",
            "contributors": [],
            "tested_kit": "0.12",
            "min_kit": "",
            "tested_firmware": "13.60",
            "tested_date": "2026-10-22",
            "revision": hashlib.sha256(
                (self.repo.root / "recipes/steam/274520/120hz-left-stick-pointer.json").read_bytes()).hexdigest(),
        })

    def test_games_are_ordered_by_store_then_key(self):
        self.repo.place("imported.json", store="imported", key="b-game")
        self.repo.place("imported.json", store="imported", key="a-game")
        self.repo.place("gog.json", store="gog", key="20")
        self.repo.place("gog.json", store="gog", key="3")
        self.repo.place("epic.json", store="epic", key="f" * 32)
        self.repo.place("epic.json", store="epic", key="a" + "0" * 31)
        self.repo.place("epic.json", store="epic", key="9" * 32)
        self.repo.place("epic.json", store="epic", key="0" * 32)
        self.repo.place("good.json", key="99")
        self.build()
        index = json.loads(self.output("index.json"))
        self.assertEqual([(g["store"], g["key"]) for g in index["games"]], [
            ("steam", "99"), ("steam", "274520"), ("steam", "1592280"),
            ("epic", "0" * 32), ("epic", "9" * 32), ("epic", "a" + "0" * 31), ("epic", "f" * 32),
            ("gog", "3"), ("gog", "20"),
            ("imported", "a-game"), ("imported", "b-game"),
        ])
        imported = index["games"][-1]
        self.assertEqual(imported["name"], "TMNT: Mutant Melee")
        self.assertEqual([e["id"] for e in imported["recipes"]],
                         ["community-imported-b-game-120hz-left-stick-pointer"])
        with zipfile.ZipFile(str(Path(self.out.name) / "recipes.zip")) as archive:
            self.assertIn("recipes/imported/b-game/120hz-left-stick-pointer.json", archive.namelist())
            self.assertIn("recipes/epic/%s/120hz-left-stick-pointer.json" % ("0" * 32), archive.namelist())

    def test_made_on_only_when_set(self):
        self.repo.place("made-on-imported.json", key="1592280", slug="from-folder")
        self.build()
        index = json.loads(self.output("index.json"))
        by_id = {e["id"]: e for g in index["games"] for e in g["recipes"]}
        self.assertEqual(by_id["community-steam-1592280-from-folder"]["made_on"], "imported")
        self.assertEqual(set(by_id["community-steam-1592280-from-folder"]), INDEX_ENTRY_KEYS | {"made_on"})
        self.assertNotIn("made_on", by_id["community-steam-1592280-gamepad"])

    def rename(self, path, name):
        data = json.loads(self.repo.read(path))
        data["game"]["name"] = name
        self.repo.write(path, json.dumps(data, indent=2) + "\n")

    def game_names(self):
        index = json.loads(self.output("index.json"))
        return {g["key"]: g["name"] for g in index["games"]}

    def test_name_is_the_newest_published_recipes(self):
        self.build()
        # A new revision of one recipe is published later than the others: its name wins.
        self.rename("recipes/steam/1592280/gamepad.json", "Darkwood (2017)")
        self.build(now=NOW + 60)
        self.assertEqual(self.game_names()["1592280"], "Darkwood (2017)")
        # An older recipe of the game keeps its earlier time, even when built again later.
        self.build(now=NOW + 120)
        self.assertEqual(self.game_names()["1592280"], "Darkwood (2017)")

    def test_name_from_a_new_recipe_counts_as_now(self):
        self.build()
        path = self.repo.place("good.json", key="1592280", slug="zz-first-of-many")
        self.rename(path, "Darkwood Remastered")
        self.build(now=NOW + 60, update_lists=False)
        self.assertEqual(self.game_names()["1592280"], "Darkwood Remastered")

    def test_name_ties_go_to_the_larger_id(self):
        self.rename("recipes/steam/1592280/gamepad.json", "Gamepad name")
        self.rename("recipes/steam/1592280/mouse-preset.json", "Mouse name")
        self.build()
        self.assertEqual(self.game_names()["1592280"], "Mouse name")

    def test_refused_recipe_stops_the_build(self):
        self.repo.place("locked.json", slug="locked")
        with self.assertRaises(build_release.ReleaseError) as caught:
            self.build()
        self.assertIn("locked must be empty", str(caught.exception))
        self.assertFalse((Path(self.out.name) / "recipes.zip").exists())

    def test_checker_refusal_stops_the_build(self):
        self.repo.place("good.json", slug="hostile")
        with self.assertRaises(build_release.ReleaseError):
            self.build(checker=FAKE_CHECKER)

    def test_checker_passes(self):
        self.assertEqual(self.build(checker=FAKE_CHECKER)["recipes"], 3)

    def test_limits(self):
        original = build_release.MAX_ENTRIES
        build_release.MAX_ENTRIES = 2
        try:
            with self.assertRaisesRegex(build_release.ReleaseError, "more than 2"):
                self.build()
        finally:
            build_release.MAX_ENTRIES = original
        original = build_release.MAX_INDEX_BYTES
        build_release.MAX_INDEX_BYTES = 100
        try:
            with self.assertRaisesRegex(build_release.ReleaseError, "index.json"):
                self.build()
        finally:
            build_release.MAX_INDEX_BYTES = original
        original = build_release.MAX_ZIP_BYTES
        build_release.MAX_ZIP_BYTES = 100
        try:
            with self.assertRaisesRegex(build_release.ReleaseError, "recipes.zip"):
                self.build()
        finally:
            build_release.MAX_ZIP_BYTES = original


class ListsTest(BuildTestCase):
    def revisions(self):
        return [line.split() for line in self.repo.read("revisions.lst").splitlines()]

    def test_first_release_writes_lists(self):
        info = self.build()
        self.assertEqual(sorted(info["changed_lists"]), ["revisions.lst", "slugs.lst"])
        self.assertEqual(self.repo.read("slugs.lst"),
                         "steam/274520 120hz-left-stick-pointer\nsteam/1592280 gamepad\nsteam/1592280 mouse-preset\n")
        rows = self.revisions()
        self.assertEqual(len(rows), 3)
        for recipe_id, revision, game, published in rows:
            store, key = game.split("/")
            self.assertEqual(store, "steam")
            self.assertTrue(recipe_id.startswith("community-%s-%s-" % (store, key)))
            self.assertEqual(len(revision), 64)
            self.assertEqual(published, str(NOW))
        self.assertEqual([row[0] for row in rows], sorted(row[0] for row in rows))

    def test_same_release_again_changes_nothing(self):
        self.build()
        before = (self.repo.read("revisions.lst"), self.repo.read("slugs.lst"))
        info = self.build(now=NOW + 86400)
        self.assertEqual(info["changed_lists"], [])
        self.assertEqual((self.repo.read("revisions.lst"), self.repo.read("slugs.lst")), before)

    def test_revisions_are_append_only(self):
        self.build()
        first = self.repo.read("revisions.lst").splitlines()
        # A new revision of one recipe and a removed recipe.
        path = "recipes/steam/274520/120hz-left-stick-pointer.json"
        data = json.loads(self.repo.read(path))
        data["community"]["notes"] = "Revision 2."
        self.repo.write(path, json.dumps(data, indent=2) + "\n")
        self.repo.remove("recipes/steam/1592280/gamepad.json")
        self.build(now=NOW + 60)
        second = self.repo.read("revisions.lst").splitlines()
        for line in first:
            self.assertIn(line, second)
        added = [line for line in second if line not in first]
        self.assertEqual(len(added), 1)
        self.assertTrue(added[0].startswith("community-steam-274520-120hz-left-stick-pointer "))
        self.assertIn(" steam/274520 ", added[0])
        self.assertTrue(added[0].endswith(" %d" % (NOW + 60)))
        # The removed recipe's slug stays.
        self.assertIn("steam/1592280 gamepad", self.repo.read("slugs.lst").splitlines())

    def test_lists_for_every_store(self):
        self.repo.place("imported.json", store="imported", key="tmnt-mutant-melee")
        self.repo.place("gog.json", store="gog", key="1207658695")
        self.repo.place("epic.json", store="epic", key="9d2d0eb64d5c44529cece33fe2a46482")
        self.build()
        self.assertEqual(self.repo.read("slugs.lst").splitlines(), [
            "steam/274520 120hz-left-stick-pointer",
            "steam/1592280 gamepad",
            "steam/1592280 mouse-preset",
            "epic/9d2d0eb64d5c44529cece33fe2a46482 120hz-left-stick-pointer",
            "gog/1207658695 120hz-left-stick-pointer",
            "imported/tmnt-mutant-melee 120hz-left-stick-pointer",
        ])
        games = {row[0]: row[2] for row in self.revisions()}
        self.assertEqual(games["community-imported-tmnt-mutant-melee-120hz-left-stick-pointer"],
                         "imported/tmnt-mutant-melee")
        self.assertEqual(games["community-gog-1207658695-120hz-left-stick-pointer"], "gog/1207658695")

    def test_no_update_lists(self):
        self.build(update_lists=False)
        self.assertFalse((self.repo.root / "revisions.lst").exists())

    def test_malformed_list_is_refused(self):
        self.repo.write("revisions.lst", "only two\n")
        with self.assertRaises(build_release.ReleaseError):
            self.build()

    def test_old_list_lines_are_refused(self):
        # The Steam-only shapes: a bare app ID where the store and key go.
        self.repo.write("revisions.lst", "community-274520-x %s 274520 %d\n" % ("0" * 64, NOW))
        with self.assertRaisesRegex(build_release.ReleaseError, "revisions.lst: malformed"):
            self.build()
        self.repo.write("revisions.lst", "")
        self.repo.write("slugs.lst", "274520 120hz-left-stick-pointer\n")
        with self.assertRaisesRegex(build_release.ReleaseError, "slugs.lst: malformed"):
            self.build()


if __name__ == "__main__":
    unittest.main()
