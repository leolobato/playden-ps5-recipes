#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Check community recipes before they are merged or released.

Usage:
  scripts/check.py [--checker PATH] [--base-ref REF] [--labels-out FILE] [files...]

With no files, it checks every file under recipes/, or with --base-ref only the files that differ from that ref.
Each recipe must sit at recipes/<store>/<key>/<slug>.json, with recipe_id community-<store>-<key>-<slug> and a
`game` object naming the same store and key, real kit versions, no locked rows, and a slug that was never used by
another recipe of the same game (slugs.lst). community.format_version 1 names its program (launch_entries,
executable); 2 is a settings-only recipe, which may not carry what Playden fills in from its own recipe.

Stores and keys: steam (the app ID), gog (the product ID), epic (the catalog item ID, 32 lowercase hex digits) and
imported (a name key made from the game's name by name_key()).

A removed recipe's slug can only be caught coming back with --base-ref: the file is then new compared with the
base and its slug is already in the base's slugs.lst. Without --base-ref, a listed slug must match its folder.

--checker runs Playden's community_recipe_check --json on the files and adds its refusals and flags.
--labels-out writes the pull request labels to apply, one per line.

Python 3 standard library only.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MAX_FILE_BYTES = 16 * 1024
MAX_ID_BYTES = 128
MAX_SLUG = 32
MAX_TITLE = 60
MAX_GAME_NAME = 120
MAX_NAME_KEY = 48
MAX_STEAM_APP_ID = 4294967295
# In the order a release lists them.
STORES = ("steam", "epic", "gog", "imported")
STATUSES = ("works", "playable", "broken", "untested")
KIT_VERSION = re.compile(r"^[0-9]+\.[0-9]+(-dev\.[0-9]{8}\.[0-9]+)?\Z")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\Z")
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
RECIPE_PATH = re.compile(r"^recipes/([^/]+)/([^/]+)/([^/]+)\.json\Z")
RECIPE_ID = re.compile(r"^community-(steam|epic|gog|imported)-[a-z0-9]+(-[a-z0-9]+)*\Z")
STORE_KEYS = {
    "steam": re.compile(r"^[1-9][0-9]{0,9}\Z"),
    "gog": re.compile(r"^[1-9][0-9]{0,19}\Z"),
    "epic": re.compile(r"^[0-9a-f]{32}\Z"),
    "imported": SLUG,
}
KEY_RULES = {
    "steam": "a Steam app ID (digits, no leading zero, at most %d)" % MAX_STEAM_APP_ID,
    "gog": "a GOG product ID (1-20 digits, no leading zero)",
    "epic": "an Epic catalog item ID (32 lowercase hex digits)",
    "imported": "a name key (1-%d lowercase letters, digits and single hyphens, no hyphen at either end)"
                % MAX_NAME_KEY,
}
# Fields of the old Steam-only format; the game object replaced them.
OBSOLETE_FIELDS = ("app_id", "steam_appid")

LABEL_STARTS_GAME = "changes how the game starts"

# Fields that decide which program starts and how: a change to any of them gets LABEL_STARTS_GAME.
LAUNCH_FIELDS = ("launch_entries", "executable", "arguments", "resolution_arguments", "dll_overrides", "registry")
# Game settings rows in `defaults` that do the same.
LAUNCH_DEFAULTS = ("environment", "launch_option", "program", "program_folder", "program_arguments",
                   "extra_arguments", "library_overrides", "registry_dll_overrides")
# community.format_version: 1, a recipe with its program (launch_entries and executable); 2, a settings-only recipe,
# whose program Playden fills in from its own recipe for the player's copy.
FORMAT_VERSIONS = (1, 2)
# What a format 1 recipe must carry: its program.
PROGRAM_FIELDS = ("launch_entries", "executable")
# What a settings-only recipe can't carry: what Playden fills in, and what belongs to a recipe with its own program.
FILLED_FIELDS = ("launch_entries", "executable", "architecture", "runtime", "prefix", "detected", "dll_overrides",
                 "arguments", "resolution_arguments")
PROGRAM_ONLY_FIELDS = ("registry", "ini", "game_files", "steam_api_copies")
# Game settings rows a settings-only recipe can't set: Playden's recipe has them, or (graphics) the recipe's own field.
SETTINGS_ONLY_REFUSED_DEFAULTS = ("arguments", "extra_arguments", "dll_overrides", "architecture", "graphics")
# Game settings rows a format 1 recipe keeps in its own fields instead.
PROGRAM_DEFAULTS = ("program", "program_folder", "program_arguments", "arguments", "extra_arguments", "dll_overrides")
# Words in a checker flag that mean the same thing.
LAUNCH_FLAG_WORDS = ("program", "argument", "override", "dll", "environment", "registry", "launch", "executable")


class Result:
    """What the checks found in one file."""

    def __init__(self, path):
        self.path = path            # relative to the repository, with forward slashes
        self.refusals = []
        self.flags = []
        self.labels = set()
        self.data = None            # the parsed JSON object, when it parsed
        self.raw = None             # the file bytes
        self.store = None
        self.key = None
        self.slug = None
        self.command = None

    @property
    def ok(self):
        return not self.refusals

    @property
    def game(self):
        """The game's "<store>/<key>", as revisions.lst and slugs.lst name it."""
        return "%s/%s" % (self.store, self.key)


def name_key(name):
    """The key of an imported game, made from its name. Playden and the API make it the same way.

    Unicode NFKD, every mark (category M) dropped, lowercase ASCII; every run of other characters becomes one hyphen,
    hyphens trimmed at both ends, cut to 48 bytes and trimmed again; an empty result is "game".
    """
    decomposed = unicodedata.normalize("NFKD", name)
    # Every mark (category M), not only those with a combining class: U+20DD or U+FE0F would otherwise split words.
    text = "".join(c for c in decomposed if not unicodedata.category(c).startswith("M")).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text[:MAX_NAME_KEY].rstrip("-")
    return text or "game"


def game_name_printable(name):
    """True when name has a visible character and no control, format, private-use or unassigned characters and no
    line breaks. Spaces of any width are fine (a store may show a name with a full-width or no-break space)."""
    # A visible character: not a space, a mark or a control/format character (U+034F alone would show nothing).
    if not any(unicodedata.category(c)[0] not in ("Z", "M", "C") for c in name):
        return False
    return not any(unicodedata.category(c)[0] == "C" or unicodedata.category(c) in ("Zl", "Zp") for c in name)


def store_key_ok(store, key):
    """True when key follows the store's rule."""
    pattern = STORE_KEYS.get(store)
    if pattern is None or not isinstance(key, str) or not pattern.match(key):
        return False
    if store == "steam":
        return int(key) <= MAX_STEAM_APP_ID
    if store == "imported":
        return len(key) <= MAX_NAME_KEY
    return True


def kit_version_ok(text):
    return isinstance(text, str) and bool(KIT_VERSION.match(text))


def kit_version_key(text):
    """Orders kit versions: 0.9 < 0.12, and a dev version counts as the release it leads to."""
    match = KIT_VERSION.match(text)
    if not match:
        raise ValueError("not a kit version: %r" % text)
    generation, revision = text.split("-", 1)[0].split(".")
    return (int(generation), int(revision))


def slug_ok(slug):
    return isinstance(slug, str) and 1 <= len(slug) <= MAX_SLUG and bool(SLUG.match(slug))


def read_list(text):
    """Lines of a .lst file split on spaces, without blanks and # comments."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.split())
    return out


def read_slugs(text):
    """The ("<store>/<key>", slug) pairs in slugs.lst."""
    return {(fields[0], fields[1]) for fields in read_list(text) if len(fields) >= 2}


def git(repo, *args, check=True):
    done = subprocess.run(["git", "-C", str(repo)] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and done.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), done.stderr.decode("utf-8", "replace").strip()))
    return done


def show_at_ref(repo, ref, path):
    """The bytes of path at ref, or None when it doesn't exist there."""
    done = git(repo, "show", "%s:%s" % (ref, path), check=False)
    return done.stdout if done.returncode == 0 else None


def all_recipe_files(repo):
    root = Path(repo) / "recipes"
    if not root.is_dir():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames.sort()
        for name in sorted(filenames):
            out.append(Path(dirpath, name).relative_to(repo).as_posix())
    return sorted(out)


def changed_recipe_files(repo, base_ref):
    """Files under recipes/ that were added, changed or renamed compared with base_ref (working tree included)."""
    done = git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=ACMRT", base_ref, "--", "recipes")
    files = [line for line in done.stdout.decode("utf-8").splitlines() if line]
    # Files git doesn't track yet count as changed too.
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "--", "recipes")
    files += [line for line in untracked.stdout.decode("utf-8").splitlines() if line]
    return sorted(set(files))


def relative(repo, path):
    absolute = Path(path) if os.path.isabs(path) else Path(repo) / path
    try:
        return absolute.resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def text_field(block, key, result, where, required=False):
    """A string field; refuses other types. Returns '' when absent."""
    value = block.get(key)
    if value is None:
        if required:
            result.refusals.append("%s%s is missing" % (where, key))
        return ""
    if not isinstance(value, str):
        result.refusals.append("%s%s must be a string" % (where, key))
        return ""
    if required and not value.strip():
        result.refusals.append("%s%s is empty" % (where, key))
    return value


def check_content(result, raw):
    """The rules that need only the file: layout, id, community block."""
    result.raw = raw
    match = RECIPE_PATH.match(result.path)
    if not match:
        result.refusals.append("a recipe must be at recipes/<store>/<key>/<slug>.json")
        return
    store, key, slug = match.group(1), match.group(2), match.group(3)
    if store not in STORES:
        result.refusals.append("folder %r is not a store; use one of %s" % (store, ", ".join(STORES)))
    elif not store_key_ok(store, key):
        result.refusals.append("folder %r is not %s" % (key, KEY_RULES[store]))
    result.store, result.key, result.slug = store, key, slug
    if not slug_ok(slug):
        result.refusals.append("slug %r must be 1-%d lowercase letters, digits and single hyphens, without a hyphen "
                               "at either end" % (slug, MAX_SLUG))
    if len(raw) > MAX_FILE_BYTES:
        result.refusals.append("file is %d bytes, more than %d" % (len(raw), MAX_FILE_BYTES))
        return
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        result.refusals.append("not valid UTF-8 JSON: %s" % error)
        return
    if not isinstance(data, dict):
        result.refusals.append("the file must hold a JSON object")
        return
    result.data = data

    expected_id = "community-%s-%s-%s" % (store, key, slug)
    recipe_id = data.get("recipe_id")
    if recipe_id != expected_id:
        result.refusals.append("recipe_id is %r, the file name needs %r" % (recipe_id, expected_id))
    if len(expected_id.encode("utf-8")) > MAX_ID_BYTES:
        result.refusals.append("recipe_id %r is longer than %d bytes" % (expected_id, MAX_ID_BYTES))
    elif not RECIPE_ID.match(expected_id):
        result.refusals.append("recipe_id %r is not community-<store>-<key>-<slug> in lowercase letters, digits and "
                               "single hyphens" % expected_id)
    for name in OBSOLETE_FIELDS:
        if name in data:
            result.refusals.append("%s is obsolete: the game object names the game now" % name)
    check_game(result, data, store, key)
    locked = data.get("locked", [])
    if locked != []:
        result.refusals.append("locked must be empty in a community recipe")

    community = data.get("community")
    if not isinstance(community, dict):
        result.refusals.append("the community block is missing")
        return
    where = "community."
    format_version = community.get("format_version")
    if type(format_version) is not int or format_version not in FORMAT_VERSIONS:
        result.refusals.append("community.format_version must be 1 (a recipe with its program) or 2 (a settings-only "
                               "recipe)")
    else:
        check_format(result, data, format_version)
    # Title and author are optional: Playden lists an untitled recipe as "Recipe from <author>", and one without
    # an author as "from the community".
    title = text_field(community, "title", result, where)
    if title and (len(title) > MAX_TITLE or not title.isprintable()):
        result.refusals.append("community.title must be 1-%d printable characters" % MAX_TITLE)
    text_field(community, "author", result, where)
    contributors = community.get("contributors", [])
    if not isinstance(contributors, list) or not all(isinstance(n, str) and n.strip() for n in contributors):
        result.refusals.append("community.contributors must be a list of names")
    tested_kit = text_field(community, "tested_kit", result, where, required=True)
    if tested_kit.strip() and not kit_version_ok(tested_kit):
        result.refusals.append("community.tested_kit %r is not a kit version like 0.12 or 0.12-dev.20261022.1"
                               % tested_kit)
    min_kit = text_field(community, "min_kit", result, where)
    if min_kit and not kit_version_ok(min_kit):
        result.refusals.append("community.min_kit %r is not a kit version like 0.12 or 0.12-dev.20261022.1" % min_kit)
    text_field(community, "tested_firmware", result, where)
    tested_date = text_field(community, "tested_date", result, where)
    if tested_date:
        try:
            if not DATE.match(tested_date):
                raise ValueError(tested_date)
            datetime.date.fromisoformat(tested_date)
        except ValueError:
            result.refusals.append("community.tested_date %r is not a YYYY-MM-DD date" % tested_date)
    status = community.get("status")
    if status not in STATUSES:
        result.refusals.append("community.status %r must be one of %s" % (status, ", ".join(STATUSES)))
    text_field(community, "notes", result, where)


def check_format(result, data, format_version):
    """The fields each format version needs or refuses (Playden's ParseCommunityRecipe and community rules)."""
    defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    if format_version == 1:
        for name in PROGRAM_FIELDS:
            if name not in data:
                result.refusals.append("%s is missing: a format_version 1 recipe names its program (a recipe without "
                                       "one is format_version 2, settings-only)" % name)
        for name in PROGRAM_DEFAULTS:
            if name in defaults:
                result.refusals.append("defaults sets %s: put the program, its folder, arguments and library overrides "
                                       "in launch_entries, arguments and dll_overrides" % name)
        return
    for name in FILLED_FIELDS:
        if name in data:
            result.refusals.append("a settings-only recipe (format_version 2) can't carry %s: Playden takes it from its "
                                   "own recipe for the copy" % name)
    for name in PROGRAM_ONLY_FIELDS:
        if name in data:
            result.refusals.append("a settings-only recipe (format_version 2) can't carry %s: only a recipe with its own "
                                   "program (format_version 1) can" % name)
    for name in SETTINGS_ONLY_REFUSED_DEFAULTS:
        if name in defaults:
            hint = ('set the recipe\'s "graphics" field instead' if name == "graphics" else
                    "Playden takes it from its own recipe for the copy; name a program with defaults program and its "
                    "arguments with program_arguments")
            result.refusals.append("defaults sets %s in a settings-only recipe: %s" % (name, hint))


def check_game(result, data, store, key):
    """The game object, which must name the folder's store and key, and the optional made_on."""
    game = data.get("game")
    if not isinstance(game, dict):
        result.refusals.append('the game object is missing: "game": {"store", "key", "name"}')
    else:
        for name in sorted(set(game) - {"store", "key", "name"}):
            result.refusals.append("game.%s is not a game field (store, key, name)" % name)
        game_store, game_key = game.get("store"), game.get("key")
        if game_store not in STORES:
            result.refusals.append("game.store %r must be one of %s" % (game_store, ", ".join(STORES)))
        elif game_store != store:
            result.refusals.append("game.store %r doesn't match folder %s" % (game_store, store))
        if not isinstance(game_key, str):
            result.refusals.append("game.key must be a string")
        elif game_key != key:
            result.refusals.append("game.key %r doesn't match folder %s" % (game_key, key))
        name = game.get("name")
        if not isinstance(name, str) or not 1 <= len(name) <= MAX_GAME_NAME or not game_name_printable(name):
            result.refusals.append("game.name must be 1-%d printable characters" % MAX_GAME_NAME)
    if "made_on" in data:
        made_on = data["made_on"]
        if made_on not in STORES:
            result.refusals.append("made_on %r must be one of %s" % (made_on, ", ".join(STORES)))
        elif isinstance(game, dict) and made_on == game.get("store"):
            result.refusals.append("made_on is only for a copy from another store; leave it out when it is %r"
                                   % made_on)


def launch_view(data):
    """The parts of a recipe that decide how the game starts, for comparison."""
    if not isinstance(data, dict):
        return None
    defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    return ({key: data.get(key) for key in LAUNCH_FIELDS},
            {key: defaults.get(key) for key in LAUNCH_DEFAULTS})


def new_file_starts_game(data):
    """Whether a new recipe changes how the game starts: one with its program does; a settings-only recipe (format 2)
    only through the program rows it sets in defaults, as Playden's checker flags it."""
    if not isinstance(data, dict):
        return True
    community = data.get("community") if isinstance(data.get("community"), dict) else {}
    if community.get("format_version") != 2:
        return True
    return any(value is not None for group in launch_view(data) for value in group.values())


def parse_checker_output(text, repo):
    """Reads community_recipe_check --json output into {relative path: {"refusals", "flags", "command", "ok"}}.

    Expected: {"files": [{"path", "ok", "refusals": [...], "flags": [...], "command", ...}]}. A bare list of file
    objects is accepted too, and refusals or flags may be strings or objects (their "message" or the whole object).
    """
    document = json.loads(text)
    files = document.get("files", []) if isinstance(document, dict) else document
    if not isinstance(files, list):
        raise ValueError("no file list in the checker output")

    def messages(items):
        if items is None:
            return []
        if not isinstance(items, list):
            items = [items]
        out = []
        for item in items:
            if isinstance(item, dict):
                text = item.get("message") or item.get("reason") or item.get("text")
                field = item.get("field") or item.get("id") or item.get("kind")
                if text and field:
                    out.append("%s: %s" % (field, text))
                else:
                    out.append(text or field or json.dumps(item, sort_keys=True))
            else:
                out.append(str(item))
        return out

    out = {}
    for entry in files:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        refusals = messages(entry.get("refusals", entry.get("errors")))
        ok = entry.get("ok")
        if ok is False and not refusals:
            refusals = ["refused by community_recipe_check"]
        out[relative(repo, entry["path"])] = {
            "refusals": refusals,
            "flags": messages(entry.get("flags", entry.get("warnings"))),
            "command": entry.get("command"),
            "ok": ok if isinstance(ok, bool) else not refusals,
        }
    return out


def run_checker(checker, repo, paths):
    """Runs Playden's checker on the files; returns parse_checker_output's map, or raises with what went wrong."""
    absolute = [str(Path(repo) / path) for path in paths]
    done = subprocess.run([checker, "--json"] + absolute, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return parse_checker_output(done.stdout.decode("utf-8"), repo)
    except ValueError as error:
        raise RuntimeError("community_recipe_check exited %d without readable JSON (%s): %s"
                           % (done.returncode, error, done.stderr.decode("utf-8", "replace").strip()[-500:]))


def flag_changes_start(flag):
    lowered = flag.lower()
    return any(word in lowered for word in LAUNCH_FLAG_WORDS)


def check_files(paths, repo=REPO, base_ref=None, checker=None):
    """Checks the files (paths relative to repo) and returns a Result for each."""
    repo = Path(repo)
    slugs_text = None
    if base_ref:
        base_slugs = show_at_ref(repo, base_ref, "slugs.lst")
        slugs_text = base_slugs.decode("utf-8") if base_slugs is not None else ""
    elif (repo / "slugs.lst").exists():
        slugs_text = (repo / "slugs.lst").read_text(encoding="utf-8")
    known_slugs = read_slugs(slugs_text or "")

    results = []
    for path in paths:
        result = Result(relative(repo, path))
        absolute = repo / result.path
        if not absolute.is_file():
            result.refusals.append("file not found")
            results.append(result)
            continue
        check_content(result, absolute.read_bytes())

        if result.store and result.key and result.slug:
            listed = (result.game, result.slug) in known_slugs
            base = show_at_ref(repo, base_ref, result.path) if base_ref else None
            if base_ref and base is None and listed:
                result.refusals.append("slug %r was used by a recipe for %s that was removed; slugs are never reused"
                                       % (result.slug, result.game))
            if base_ref and checker is None:
                if base is None:
                    if new_file_starts_game(result.data):
                        result.labels.add(LABEL_STARTS_GAME)
                else:
                    try:
                        before = json.loads(base.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        before = None
                    if launch_view(before) != launch_view(result.data):
                        result.labels.add(LABEL_STARTS_GAME)
            elif checker is None:
                # Nothing to compare with: treat the file as new.
                if new_file_starts_game(result.data):
                    result.labels.add(LABEL_STARTS_GAME)
        results.append(result)

    if checker and results:
        try:
            found = run_checker(checker, repo, [r.path for r in results])
        except (OSError, RuntimeError) as error:
            for result in results:
                result.refusals.append("checker: %s" % error)
            return results
        for result in results:
            entry = found.get(result.path)
            if entry is None:
                result.refusals.append("checker: no result for this file")
                continue
            result.refusals += ["checker: %s" % text for text in entry["refusals"]]
            result.flags += entry["flags"]
            result.command = entry["command"]
            if any(flag_changes_start(flag) for flag in entry["flags"]):
                result.labels.add(LABEL_STARTS_GAME)
    return results


def check_lists_append_only(repo, base_ref):
    """Refusals for revisions.lst and slugs.lst lines that were removed or changed since base_ref."""
    refusals = []
    for name in ("revisions.lst", "slugs.lst"):
        before = show_at_ref(repo, base_ref, name)
        if before is None:
            continue
        path = Path(repo) / name
        now = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
        lost = [line for line in before.decode("utf-8").splitlines() if line.strip() and line not in now]
        if lost:
            refusals.append("%s: %d line(s) removed or changed; it only grows" % (name, len(lost)))
    return refusals


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="recipe files (default: all, or the ones changed since --base-ref)")
    parser.add_argument("--checker", help="path to Playden's community_recipe_check")
    parser.add_argument("--base-ref", help="git ref to compare with (the pull request's base)")
    parser.add_argument("--labels-out", help="write the labels to apply here, one per line")
    parser.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    repo = Path(args.repo)

    if args.files:
        paths = args.files
    elif args.base_ref:
        paths = changed_recipe_files(repo, args.base_ref)
    else:
        paths = all_recipe_files(repo)

    results = check_files(paths, repo=repo, base_ref=args.base_ref, checker=args.checker)
    list_refusals = check_lists_append_only(repo, args.base_ref) if args.base_ref else []

    labels = set()
    for result in results:
        labels |= result.labels
        if result.ok:
            print("ok       %s" % result.path)
        for refusal in result.refusals:
            print("refused  %s: %s" % (result.path, refusal))
        for flag in result.flags:
            print("flag     %s: %s" % (result.path, flag))
        if result.command:
            print("command  %s: %s" % (result.path, result.command))
    for refusal in list_refusals:
        print("refused  %s" % refusal)
    for label in sorted(labels):
        print("label    %s" % label)
    if not results:
        print("no recipe files to check")

    if args.labels_out:
        with open(args.labels_out, "w", encoding="utf-8") as out:
            out.write("".join(label + "\n" for label in sorted(labels)))

    refused = sum(1 for r in results if not r.ok) + len(list_refusals)
    print("%d file(s) checked, %d refused" % (len(results), sum(1 for r in results if not r.ok)))
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
