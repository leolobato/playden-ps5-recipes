# SPDX-License-Identifier: CC0-1.0
"""Tests for scripts/check.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""

import io
import json
import os
import contextlib
import unittest

import check
from recipe_testing import FAKE_CHECKER, TempRepo


class CheckTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()

    def tearDown(self):
        self.repo.close()

    def check(self, *paths, base_ref=None, checker=None):
        return check.check_files(list(paths), repo=self.repo.root, base_ref=base_ref, checker=checker)

    def refusals(self, path, **kwargs):
        (result,) = self.check(path, **kwargs)
        return result.refusals

    def assertRefused(self, path, fragment, **kwargs):
        refusals = self.refusals(path, **kwargs)
        self.assertTrue(any(fragment in text for text in refusals), "%r not in %r" % (fragment, refusals))

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["--repo", str(self.repo.root)] + list(argv))
        return code, out.getvalue()


class ContentTest(CheckTestCase):
    def test_good_recipe_passes(self):
        path = self.repo.place("good.json")
        self.assertEqual(self.refusals(path), [])

    def test_the_repository_recipes_pass(self):
        results = check.check_files(check.all_recipe_files(check.REPO), repo=check.REPO)
        self.assertTrue(results)
        for result in results:
            self.assertEqual(result.refusals, [], result.path)

    def test_id_must_match_file_name(self):
        path = self.repo.place("mismatched-id.json", fix_id=False)
        self.assertRefused(path, "recipe_id is 'community-steam-274520-another-name'")

    def test_game_key_must_match_folder(self):
        path = self.repo.place("good.json", fix_id=False, key="274521")
        refusals = self.refusals(path)
        self.assertTrue(any("game.key '274520' doesn't match folder 274521" in r for r in refusals), refusals)
        self.assertTrue(any("the file name needs 'community-steam-274521-" in r for r in refusals), refusals)

    def test_old_layouts_are_refused(self):
        good = self.repo.read(self.repo.place("good.json"))
        for path in ("recipes/274520-darkwood.json", "recipes/274520/darkwood.json",
                     "recipes/steam/274520/extra/darkwood.json"):
            with self.subTest(path=path):
                self.repo.write(path, good)
                self.assertRefused(path, "recipes/<store>/<key>/<slug>.json")

    def test_bad_slugs(self):
        for slug in ("Darkwood", "a" * 33, "double--hyphen", "-leading", "trailing-", "under_score"):
            with self.subTest(slug=slug):
                path = self.repo.place("good.json", slug=slug)
                self.assertRefused(path, "slug %r" % slug)

    def test_slug_limits_pass(self):
        for slug in ("a", "a" * 32, "0-9", "120hz-left-stick-pointer"):
            with self.subTest(slug=slug):
                self.assertEqual(self.refusals(self.repo.place("good.json", slug=slug)), [])

    def test_longest_valid_id_fits(self):
        # The longest key (an imported name key, 48 bytes) and slug give a 100-byte id.
        path = self.repo.place("imported.json", store="imported", key="k" * 48, slug="s" * 32)
        (result,) = self.check(path)
        self.assertEqual(result.refusals, [])
        self.assertEqual(len(result.data["recipe_id"]), 100)

    def test_id_longer_than_128_bytes(self):
        # No valid key is that long; an over-long folder is refused for the key and for the id.
        path = self.repo.place("good.json", store="imported", key="k" * 90, slug="s" * 32)
        refusals = self.refusals(path)
        self.assertTrue(any("longer than 128 bytes" in r for r in refusals), refusals)
        self.assertTrue(any("is not a name key" in r for r in refusals), refusals)

    def test_missing_title_is_fine(self):
        self.assertEqual(self.refusals(self.repo.place("missing-title.json")), [])

    def test_long_title(self):
        path = self.repo.place("good.json")
        data = json.loads(self.repo.read(path))
        data["community"]["title"] = "x" * 61
        self.repo.write(path, json.dumps(data))
        self.assertRefused(path, "community.title must be")

    def test_missing_author_is_fine(self):
        self.assertEqual(self.refusals(self.repo.place("missing-author.json")), [])

    def test_bad_kit_versions(self):
        refusals = self.refusals(self.repo.place("bad-kit.json"))
        self.assertTrue(any("tested_kit 'hl2-fw1360-dev1'" in r for r in refusals), refusals)
        self.assertTrue(any("min_kit '0.12-dev.2026102.1'" in r for r in refusals), refusals)

    def test_kit_versions(self):
        for good in ("0.12", "1.0", "0.12-dev.20261022.1", "10.200-dev.20261022.15"):
            self.assertTrue(check.kit_version_ok(good), good)
        for bad in ("0.12.1", "v0.12", "0.12-dev", "kit-h", "", "0.12-rc.20261022.1"):
            self.assertFalse(check.kit_version_ok(bad), bad)
        self.assertLess(check.kit_version_key("0.9"), check.kit_version_key("0.12"))
        self.assertEqual(check.kit_version_key("0.12-dev.20261022.1"), check.kit_version_key("0.12"))

    def test_settings_only_recipe_passes(self):
        # Format 2: no program of its own; its program rows are defaults.
        path = self.repo.place("settings-only.json", slug="mouse-preset")
        self.assertEqual(self.refusals(path), [])

    def test_format_versions(self):
        for version in (0, 3, "2", True, None):
            with self.subTest(version=version):
                path = self.repo.place("good.json")
                data = json.loads(self.repo.read(path))
                data["community"]["format_version"] = version
                self.repo.write(path, json.dumps(data))
                self.assertRefused(path, "community.format_version must be 1 (a recipe with its program) or 2")

    def edited(self, fixture, change, slug="120hz-left-stick-pointer"):
        path = self.repo.place(fixture, slug=slug)
        data = json.loads(self.repo.read(path))
        change(data)
        self.repo.write(path, json.dumps(data, indent=2))
        return path

    def test_format_1_needs_its_program(self):
        for name in ("launch_entries", "executable"):
            with self.subTest(name=name):
                path = self.edited("good.json", lambda d: d.pop(name))
                self.assertRefused(path, "%s is missing: a format_version 1 recipe names its program" % name)
        path = self.edited("good.json", lambda d: d["defaults"].update(program="Other.exe"))
        self.assertRefused(path, "defaults sets program: put the program")

    def test_settings_only_refuses_what_playden_fills_in(self):
        good = json.loads((check.REPO / "tests" / "fixtures" / "good.json").read_text(encoding="utf-8"))
        for name in check.FILLED_FIELDS:
            with self.subTest(name=name):
                path = self.edited("settings-only.json", lambda d: d.update({name: good[name]}), slug="mouse-preset")
                self.assertRefused(path, "can't carry %s: Playden takes it" % name)
        for name, value in (("registry", {}), ("ini", {}), ("game_files", []), ("steam_api_copies", [])):
            with self.subTest(name=name):
                path = self.edited("settings-only.json", lambda d: d.update({name: value}), slug="mouse-preset")
                self.assertRefused(path, "can't carry %s: only a recipe with its own program" % name)
        for name in check.SETTINGS_ONLY_REFUSED_DEFAULTS:
            with self.subTest(default=name):
                path = self.edited("settings-only.json", lambda d: d["defaults"].update({name: "x"}), slug="mouse-preset")
                self.assertRefused(path, "defaults sets %s in a settings-only recipe" % name)

    def test_new_settings_only_file_is_labelled_by_its_program_rows(self):
        # Without Playden's checker: a new settings-only file changes how the game starts only through program rows.
        base = self.repo.commit("base")
        plain = self.edited("settings-only.json", lambda d: [d["defaults"].pop(k) for k in ("program", "program_arguments")],
                            slug="mouse-preset")
        for kwargs in ({"base_ref": base}, {}):
            with self.subTest(**kwargs):
                (result,) = self.check(plain, **kwargs)
                self.assertEqual(result.labels, set())
        named = self.repo.place("settings-only.json", slug="named-program")
        (result,) = self.check(named, base_ref=base)
        self.assertEqual(result.labels, {check.LABEL_STARTS_GAME})

    def test_settings_only_program_rows_are_labelled(self):
        path = self.repo.place("settings-only.json", slug="mouse-preset")
        base = self.repo.commit("base")
        (result,) = self.check(path, base_ref=base)
        self.assertEqual(result.labels, set())
        self.edited("settings-only.json", lambda d: d["defaults"].update(program="Bin/Other.exe"), slug="mouse-preset")
        (result,) = self.check(path, base_ref=base)
        self.assertEqual(result.labels, {check.LABEL_STARTS_GAME})

    def test_locked_must_be_empty(self):
        self.assertRefused(self.repo.place("locked.json"), "locked must be empty")

    def test_oversized_file(self):
        self.assertRefused(self.repo.place("oversized.json"), "more than 16384")

    def test_bad_status_and_date(self):
        path = self.repo.place("good.json")
        data = json.loads(self.repo.read(path))
        data["community"]["status"] = "great"
        data["community"]["tested_date"] = "2026-13-01"
        self.repo.write(path, json.dumps(data))
        refusals = self.refusals(path)
        self.assertTrue(any("status 'great'" in r for r in refusals), refusals)
        self.assertTrue(any("tested_date '2026-13-01'" in r for r in refusals), refusals)

    def test_not_json(self):
        self.repo.write("recipes/steam/274520/broken.json", "{")
        self.assertRefused("recipes/steam/274520/broken.json", "not valid UTF-8 JSON")


class NameKeyTest(unittest.TestCase):
    # The shared table: Playden, the API and this checker must give the same keys.
    EXAMPLES = (
        ("TMNT: Mutant Melee", "tmnt-mutant-melee"),
        ("Crème Brûlée & Clockwork — déjà vu", "creme-brulee-clockwork-deja-vu"),
        ("  ---  ", "game"),
        ("Half-Life 2", "half-life-2"),
        ("ＦＵＬＬ　ＷＩＤＴＨ", "full-width"),
    )

    def test_examples(self):
        for name, key in self.EXAMPLES:
            with self.subTest(name=name):
                self.assertEqual(check.name_key(name), key)

    def test_cut_to_48_bytes_without_a_trailing_hyphen(self):
        self.assertEqual(check.name_key("a" * 60), "a" * 48)
        self.assertEqual(check.name_key("a" * 47 + " b"), "a" * 47)
        self.assertEqual(check.name_key(""), "game")
        self.assertEqual(check.name_key("日本語"), "game")
        # Every mark goes, also those without a combining class (U+20DD, U+034F, U+FE0F).
        for name in ("A\u20ddB", "A\u034fB", "A\ufe0fB"):
            with self.subTest(name=name):
                self.assertEqual(check.name_key(name), "ab")
        self.assertEqual(check.name_key("é" * 30), "e" * 30)  # NFKD before the cut: one byte each

    def test_keys_are_valid_imported_keys(self):
        for name in [n for n, _ in self.EXAMPLES] + ["a" * 60, "a" * 47 + " b", "Ω"]:
            with self.subTest(name=name):
                self.assertTrue(check.store_key_ok("imported", check.name_key(name)))


class StoreTest(CheckTestCase):
    def test_every_store_passes(self):
        for fixture, store, key in (("good.json", "steam", "274520"), ("gog.json", "gog", "1207658695"),
                                    ("epic.json", "epic", "9d2d0eb64d5c44529cece33fe2a46482"),
                                    ("imported.json", "imported", "tmnt-mutant-melee")):
            with self.subTest(store=store):
                path = self.repo.place(fixture, store=store, key=key)
                self.assertEqual(path, "recipes/%s/%s/120hz-left-stick-pointer.json" % (store, key))
                (result,) = self.check(path)
                self.assertEqual(result.refusals, [])
                self.assertEqual(result.data["recipe_id"], "community-%s-%s-120hz-left-stick-pointer" % (store, key))
                self.assertEqual(result.game, "%s/%s" % (store, key))

    def test_key_limits_pass(self):
        for store, key in (("steam", "1"), ("steam", "4294967295"), ("gog", "1"), ("gog", "9" * 20),
                           ("epic", "0" * 32), ("imported", "a"), ("imported", "a" * 48), ("imported", "0-9")):
            with self.subTest(store=store, key=key):
                self.assertEqual(self.refusals(self.repo.place("good.json", store=store, key=key)), [])

    def test_bad_keys_per_store(self):
        bad = {
            "steam": ("0", "0274520", "4294967296", "99999999999", "darkwood", "-1"),
            "gog": ("0", "01", "9" * 21, "abc"),
            "epic": ("9D2D0EB64D5C44529CECE33FE2A46482", "9d2d0eb64d5c44529cece33fe2a4648", "g" * 32,
                     "9d2d0eb6-4d5c-4452-9cec-e33fe2a46482"),
            "imported": ("TMNT", "tmnt--melee", "-tmnt", "tmnt-", "tmnt_melee", "a" * 49),
        }
        for store, keys in bad.items():
            for key in keys:
                with self.subTest(store=store, key=key):
                    self.assertRefused(self.repo.place("good.json", store=store, key=key), "folder %r is not" % key)

    def test_unknown_store_is_refused(self):
        path = self.repo.place("good.json", store="itch", key="274520")
        refusals = self.refusals(path)
        self.assertTrue(any("folder 'itch' is not a store" in r for r in refusals), refusals)
        self.assertTrue(any("game.store 'itch' must be one of" in r for r in refusals), refusals)

    def test_store_keys_are_checked_directly(self):
        self.assertTrue(check.store_key_ok("steam", "274520"))
        self.assertFalse(check.store_key_ok("steam", 274520))
        self.assertFalse(check.store_key_ok("itch", "274520"))

    def test_imported_key_need_not_equal_the_name_key(self):
        # A maintainer may rename an imported game's folder in review.
        path = self.repo.place("imported.json", store="imported", key="tmnt")
        self.assertEqual(self.refusals(path), [])


class GameFieldTest(CheckTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.repo.place("good.json")

    def edit(self, change):
        data = json.loads(self.repo.read(self.path))
        change(data)
        self.repo.write(self.path, json.dumps(data, ensure_ascii=False))

    def test_game_store_must_match_folder(self):
        self.edit(lambda d: d["game"].update(store="gog"))
        self.assertRefused(self.path, "game.store 'gog' doesn't match folder steam")

    def test_game_key_must_be_a_string(self):
        self.edit(lambda d: d["game"].update(key=274520))
        self.assertRefused(self.path, "game.key must be a string")

    def test_missing_game_is_refused(self):
        self.edit(lambda d: d.pop("game"))
        self.assertRefused(self.path, "the game object is missing")
        self.edit(lambda d: d.update(game="Darkwood"))
        self.assertRefused(self.path, "the game object is missing")

    def test_unknown_game_field_is_refused(self):
        self.edit(lambda d: d["game"].update(app_id=274520))
        self.assertRefused(self.path, "game.app_id is not a game field")

    def test_game_names(self):
        for name in ("D", "x" * 120, "Crème Brûlée — déjà vu", "ＦＵＬＬ　ＷＩＤＴＨ", "Dark\u00a0wood"):
            with self.subTest(name=name):
                self.edit(lambda d: d["game"].update(name=name))
                self.assertEqual(self.refusals(self.path), [])
        for name in ("", "   ", "\u3000", "x" * 121, "Dark\nwood", "Dark\twood", "Dark\u200bwood", "Dark\u2028wood",
                     "Dark\ue000wood", "\u034f", " \u0301 ", None, 7):
            with self.subTest(name=name):
                self.edit(lambda d: d["game"].update(name=name))
                self.assertRefused(self.path, "game.name must be 1-120 printable characters")
        self.edit(lambda d: d["game"].pop("name"))
        self.assertRefused(self.path, "game.name must be 1-120 printable characters")

    def test_newline_in_path_is_refused(self):
        # Python's $ matches before a final newline: a path or slug ending in one must still be refused.
        for path in ("recipes/steam/274520/pad.json\n", "recipes/steam/274520/pad\n.json"):
            with self.subTest(path=path):
                self.assertFalse(check.RECIPE_PATH.match(path) and check.SLUG.match(check.RECIPE_PATH.match(path).group(3)))
        self.assertIsNone(check.RECIPE_ID.match("community-steam-274520-pad\n"))
        self.assertIsNone(check.STORE_KEYS["steam"].match("274520\n"))

    def test_obsolete_fields_are_refused(self):
        for name in ("app_id", "steam_appid"):
            with self.subTest(name=name):
                self.edit(lambda d: d.update({name: 274520}))
                self.assertRefused(self.path, "%s is obsolete" % name)
                self.edit(lambda d: d.pop(name))
                self.assertEqual(self.refusals(self.path), [])

    def test_made_on(self):
        path = self.repo.place("made-on-imported.json")
        self.assertEqual(self.refusals(path), [])
        for store in ("epic", "gog"):
            self.edit(lambda d: d.update(made_on=store))
            self.assertEqual(self.refusals(self.path), [])
        self.edit(lambda d: d.update(made_on="steam"))
        self.assertRefused(self.path, "made_on is only for a copy from another store")
        for bad in ("itch", "", None, {"store": "imported", "key": "darkwood"}):
            with self.subTest(made_on=bad):
                self.edit(lambda d: d.update(made_on=bad))
                self.assertRefused(self.path, "made_on %r must be one of" % (bad,))


class SlugHistoryTest(CheckTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.repo.place("good.json")
        self.repo.write("slugs.lst", "steam/274520 120hz-left-stick-pointer\n")
        self.base = self.repo.commit("first release")

    def test_changing_a_listed_recipe_passes(self):
        data = json.loads(self.repo.read(self.path))
        data["community"]["notes"] = "Changed."
        self.repo.write(self.path, json.dumps(data, indent=2))
        self.assertEqual(self.refusals(self.path, base_ref=self.base), [])

    def test_removed_slug_cannot_come_back(self):
        self.repo.remove(self.path)
        removed = self.repo.commit("remove the recipe")
        self.repo.place("good.json")
        self.assertRefused(self.path, "never reused", base_ref=removed)

    def test_same_slug_for_another_game_passes(self):
        path = self.repo.place("good.json", key="1592280")
        self.assertEqual(self.refusals(path, base_ref=self.base), [])

    def test_same_key_in_another_store_passes(self):
        path = self.repo.place("gog.json", store="gog", key="274520")
        self.assertEqual(self.refusals(path, base_ref=self.base), [])

    def test_removed_slug_of_an_imported_game_cannot_come_back(self):
        path = self.repo.place("imported.json", store="imported", key="tmnt-mutant-melee", slug="pad")
        self.repo.write("slugs.lst", "steam/274520 120hz-left-stick-pointer\nimported/tmnt-mutant-melee pad\n")
        self.repo.commit("second release")
        self.repo.remove(path)
        removed = self.repo.commit("remove the recipe")
        self.repo.place("imported.json", store="imported", key="tmnt-mutant-melee", slug="pad")
        self.assertRefused(path, "used by a recipe for imported/tmnt-mutant-melee", base_ref=removed)

    def test_new_slug_passes(self):
        path = self.repo.place("good.json", slug="mouse-preset")
        self.assertEqual(self.refusals(path, base_ref=self.base), [])

    def test_lists_only_grow(self):
        self.repo.write("slugs.lst", "")
        self.assertEqual(len(check.check_lists_append_only(self.repo.root, self.base)), 1)
        self.repo.write("slugs.lst", "steam/274520 120hz-left-stick-pointer\nsteam/274520 mouse-preset\n")
        self.assertEqual(check.check_lists_append_only(self.repo.root, self.base), [])

    def test_changed_files_only(self):
        self.repo.place("good.json", slug="mouse-preset")
        self.assertEqual(check.changed_recipe_files(self.repo.root, self.base), ["recipes/steam/274520/mouse-preset.json"])


class LabelTest(CheckTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.repo.place("good.json")
        self.base = self.repo.commit("first release")

    def labels(self, **kwargs):
        (result,) = self.check(self.path, **kwargs)
        return result.labels

    def edit(self, change):
        data = json.loads(self.repo.read(self.path))
        change(data)
        self.repo.write(self.path, json.dumps(data, indent=2))

    def test_notes_change_has_no_label(self):
        self.edit(lambda d: d["community"].update(notes="Other notes."))
        self.assertEqual(self.labels(base_ref=self.base), set())

    def test_display_row_change_has_no_label(self):
        self.edit(lambda d: d["defaults"].update(refresh="60"))
        self.assertEqual(self.labels(base_ref=self.base), set())

    def test_launch_changes_are_labelled(self):
        changes = {
            "arguments": lambda d: d.update(arguments="-popupwindow"),
            "dll_overrides": lambda d: d.update(dll_overrides="d3d9=n"),
            "program": lambda d: d["launch_entries"][0].update(executable="Other.exe"),
            "environment": lambda d: d["defaults"].update(environment="DXVK_HUD=fps"),
            "registry": lambda d: d.update(registry={"Software\\Wine\\AppDefaults\\Darkwood.exe\\X": {"A": "1"}}),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self.repo.git("checkout", "-q", "--", ".")
                self.edit(change)
                self.assertEqual(self.labels(base_ref=self.base), {check.LABEL_STARTS_GAME})

    def test_new_file_is_labelled(self):
        path = self.repo.place("good.json", slug="mouse-preset")
        (result,) = self.check(path, base_ref=self.base)
        self.assertEqual(result.labels, {check.LABEL_STARTS_GAME})

    def test_labels_out(self):
        self.edit(lambda d: d.update(arguments="-popupwindow"))
        out = self.repo.root / "labels.txt"
        code, text = self.run_main("--base-ref", self.base, "--labels-out", str(out))
        self.assertEqual(code, 0, text)
        self.assertEqual(out.read_text(), check.LABEL_STARTS_GAME + "\n")


class CheckerTest(CheckTestCase):
    def test_checker_flags_and_refusals(self):
        good = self.repo.place("good.json")
        hostile = self.repo.place("good.json", slug="hostile")
        by_path = {r.path: r for r in self.check(good, hostile, checker=FAKE_CHECKER)}
        self.assertEqual(by_path[good].refusals, [])
        self.assertEqual(by_path[good].flags, ["program: Darkwood.exe"])
        self.assertEqual(by_path[good].labels, {check.LABEL_STARTS_GAME})
        self.assertEqual(by_path[good].command, "wine Darkwood.exe")
        self.assertEqual(by_path[hostile].refusals, ["checker: launch_entries: program is outside the game folder"])

    def test_checker_garbage_refuses(self):
        good = self.repo.place("good.json")
        os.environ["FAKE_CHECKER_GARBAGE"] = "1"
        try:
            (result,) = self.check(good, checker=FAKE_CHECKER)
        finally:
            del os.environ["FAKE_CHECKER_GARBAGE"]
        self.assertTrue(result.refusals and "without readable JSON" in result.refusals[0], result.refusals)

    def test_missing_checker_refuses(self):
        good = self.repo.place("good.json")
        (result,) = self.check(good, checker=str(self.repo.root / "no-such-checker"))
        self.assertTrue(result.refusals and result.refusals[0].startswith("checker:"), result.refusals)

    def test_parse_variants(self):
        root = self.repo.root
        parsed = check.parse_checker_output(json.dumps([
            {"path": str(root / "recipes/steam/1/a.json"), "ok": False},
            {"path": "recipes/steam/1/b.json", "errors": ["bad"], "warnings": [{"kind": "registry"}]},
        ]), root)
        self.assertEqual(parsed["recipes/steam/1/a.json"]["refusals"], ["refused by community_recipe_check"])
        self.assertEqual(parsed["recipes/steam/1/b.json"]["refusals"], ["bad"])
        self.assertEqual(parsed["recipes/steam/1/b.json"]["flags"], ["registry"])


class MainTest(CheckTestCase):
    def test_exit_codes(self):
        self.repo.place("good.json")
        code, text = self.run_main()
        self.assertEqual(code, 0, text)
        self.repo.place("locked.json", slug="locked")
        code, text = self.run_main()
        self.assertEqual(code, 1, text)
        self.assertIn("refused  recipes/steam/274520/locked.json: locked must be empty", text)


if __name__ == "__main__":
    unittest.main()
