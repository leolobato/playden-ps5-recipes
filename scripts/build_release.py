#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Build a recipes release: recipes.zip and index.json.

Usage:
  scripts/build_release.py --out DIR [--checker PATH] [--now UNIX_SECONDS] [--no-update-lists]

Checks every recipe (scripts/check.py), then writes DIR/recipes.zip with every recipe at
recipes/<store>/<key>/<slug>.json (sorted, fixed timestamps, no directory entries, deflate: the same files give the
same bytes) and DIR/index.json (format_version 2), which lists each game (store, key, name) with its recipes and
their revisions (the sha256 of the file) and holds the sha256 of the zip.

Games are listed by store (steam, epic, gog, imported), then by key (numerically for Steam and GOG). A game's name is
the game.name of its newest published recipe: the one whose current revision was published last in revisions.lst
(a revision first released now counts as published now), ties going to the larger recipe_id.

It also adds the released revisions to revisions.lst ("<recipe_id> <revision> <store>/<key> <published unix
seconds>") and the slugs to slugs.lst ("<store>/<key> <slug>"). Both files only grow: a line, once there, stays as
it is.

--now (or SOURCE_DATE_EPOCH) sets the release time; the default is the current time.
Python 3 standard library only.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check  # noqa: E402

FORMAT_VERSION = 2
MAX_INDEX_BYTES = 256 * 1024
MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_FILE_BYTES = check.MAX_FILE_BYTES
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class ReleaseError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def build_zip(results):
    """The zip bytes for the checked recipes: sorted, fixed times and attributes, file entries only."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for result in sorted(results, key=lambda r: r.path):
            info = zipfile.ZipInfo(result.path, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3                      # Unix, whatever the host
            info.external_attr = 0o100644 << 16         # regular file, rw-r--r--
            info.flag_bits = 0
            archive.writestr(info, result.raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return buffer.getvalue()


def store_key_order(store, key):
    """Sort key for a game: the stores in check.STORES order, then Steam and GOG keys by value, others as text."""
    rank = check.STORES.index(store) if store in check.STORES else len(check.STORES)
    return (rank, int(key) if store in ("steam", "gog") and key.isdigit() else 0, key)


def game_order(game):
    """Sort key for a "<store>/<key>" text."""
    store, _, key = game.partition("/")
    return store_key_order(store, key)


def read_revisions(text):
    """revisions.lst lines and {(recipe_id, revision): published}; refuses malformed lines."""
    lines = [line for line in text.splitlines() if line.strip()]
    published = {}
    for line in lines:
        fields = line.split()
        if len(fields) != 4 or line.startswith("#") or "/" not in fields[2] or not fields[3].isdigit():
            raise ReleaseError("revisions.lst: malformed line %r" % line)
        key = (fields[0], fields[1])
        published[key] = min(published.get(key, int(fields[3])), int(fields[3]))
    return lines, published


def index_entry(result):
    """One recipe in index.json. Absent optional texts are "", absent contributors []."""
    community = result.data["community"]

    def text(key):
        value = community.get(key)
        return value if isinstance(value, str) else ""

    entry = {
        "id": result.data["recipe_id"],
        "title": text("title"),
        "status": text("status"),
        "author": text("author"),
        "contributors": list(community.get("contributors") or []),
        "tested_kit": text("tested_kit"),
        "min_kit": text("min_kit"),
        "tested_firmware": text("tested_firmware"),
        "tested_date": text("tested_date"),
        "revision": sha256(result.raw),
    }
    if "made_on" in result.data:
        entry["made_on"] = result.data["made_on"]
    return entry


def build_index(results, zip_bytes, now, published=None):
    """index.json bytes. published maps (recipe_id, revision) to its time in revisions.lst; a revision not in it
    counts as published now."""
    published = published or {}
    games = {}
    for result in results:
        games.setdefault((result.store, result.key), []).append(result)
    listed = []
    for (store, key), members in sorted(games.items(), key=lambda item: store_key_order(*item[0])):
        newest = max(members, key=lambda r: (published.get((r.data["recipe_id"], sha256(r.raw)), now),
                                             r.data["recipe_id"]))
        entries = sorted((index_entry(r) for r in members), key=lambda e: e["id"])
        listed.append({"store": store, "key": key, "name": newest.data["game"]["name"], "recipes": entries})
    index = {
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zip_sha256": sha256(zip_bytes),
        "games": listed,
    }
    text = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8")


def updated_revisions(text, results, now):
    """revisions.lst with the released revisions added; existing lines are kept as they are."""
    lines, published = read_revisions(text)
    known = set(published)
    for result in results:
        key = (result.data["recipe_id"], sha256(result.raw))
        if key not in known:
            known.add(key)
            lines.append("%s %s %s %d" % (key[0], key[1], result.game, now))
    return "".join(line + "\n" for line in sorted(set(lines)))


def updated_slugs(text, results):
    """slugs.lst with every released slug added; existing lines are kept."""
    pairs = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 2 or line.startswith("#") or "/" not in fields[0]:
            raise ReleaseError("slugs.lst: malformed line %r" % line)
        pairs.add((fields[0], fields[1]))
    for result in results:
        pairs.add((result.game, result.slug))
    return "".join("%s %s\n" % pair for pair in sorted(pairs, key=lambda p: (game_order(p[0]), p[1])))


def build(repo, out_dir, checker=None, now=None, update_lists=True):
    """Checks every recipe and writes the release into out_dir. Returns a dict describing it."""
    repo = Path(repo)
    if now is None:
        epoch = os.environ.get("SOURCE_DATE_EPOCH")
        now = int(epoch) if epoch else int(time.time())
    results = check.check_files(check.all_recipe_files(repo), repo=repo, checker=checker)
    refused = [r for r in results if not r.ok]
    if refused:
        lines = ["%s: %s" % (r.path, refusal) for r in refused for refusal in r.refusals]
        raise ReleaseError("refused recipes:\n  " + "\n  ".join(lines))
    if len(results) > MAX_ENTRIES:
        raise ReleaseError("%d recipes, more than %d" % (len(results), MAX_ENTRIES))
    for result in results:
        if len(result.raw) > MAX_FILE_BYTES:
            raise ReleaseError("%s is larger than %d bytes" % (result.path, MAX_FILE_BYTES))
    ids = {}
    for result in results:
        other = ids.setdefault(result.data["recipe_id"], result.path)
        if other != result.path:
            raise ReleaseError("%s and %s have the same recipe_id" % (other, result.path))

    zip_bytes = build_zip(results)
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise ReleaseError("recipes.zip is %d bytes, more than %d" % (len(zip_bytes), MAX_ZIP_BYTES))
    revisions_path = repo / "revisions.lst"
    _, published = read_revisions(revisions_path.read_text(encoding="utf-8") if revisions_path.exists() else "")
    index_bytes = build_index(results, zip_bytes, now, published)
    if len(index_bytes) > MAX_INDEX_BYTES:
        raise ReleaseError("index.json is %d bytes, more than %d" % (len(index_bytes), MAX_INDEX_BYTES))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recipes.zip").write_bytes(zip_bytes)
    (out_dir / "index.json").write_bytes(index_bytes)

    changed = []
    if update_lists:
        for name, make in (("revisions.lst", lambda text: updated_revisions(text, results, now)),
                           ("slugs.lst", lambda text: updated_slugs(text, results))):
            path = repo / name
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            after = make(before)
            if after != before:
                path.write_text(after, encoding="utf-8")
                changed.append(name)
    return {"recipes": len(results), "zip_sha256": sha256(zip_bytes), "zip_bytes": len(zip_bytes),
            "index_bytes": len(index_bytes), "changed_lists": changed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="folder for recipes.zip and index.json")
    parser.add_argument("--checker", help="path to Playden's community_recipe_check")
    parser.add_argument("--now", type=int, help="release time in unix seconds (default: SOURCE_DATE_EPOCH or now)")
    parser.add_argument("--no-update-lists", action="store_true", help="leave revisions.lst and slugs.lst alone")
    parser.add_argument("--repo", default=str(check.REPO), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        info = build(args.repo, args.out, checker=args.checker, now=args.now, update_lists=not args.no_update_lists)
    except ReleaseError as error:
        print("build_release: %s" % error, file=sys.stderr)
        return 1
    print("%d recipe(s); recipes.zip %d bytes, sha256 %s; index.json %d bytes"
          % (info["recipes"], info["zip_bytes"], info["zip_sha256"], info["index_bytes"]))
    for name in info["changed_lists"]:
        print("updated %s" % name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
