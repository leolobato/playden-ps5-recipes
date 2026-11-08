# Playden community recipes

Game settings that players found for running games in [Playden](https://github.com/leolobato/playden-ps5) on the
PS5. Each file in `recipes/` is one recipe for one game: the program to start, its arguments, library
overrides and the Game settings rows that differ from Playden's defaults, plus who tested it, on which firmware and
kit, and how well it ran. A game can have several recipes, for example "120 Hz, pointer on left stick" and "Mouse
preset".

Playden offers these recipes when you install a game or from Game settings. It never picks one for you.

## Layout

```
recipes/<store>/<key>/<slug>.json
```

- `<store>` is the store the game comes from: `steam`, `epic`, `gog` or `imported` (a game copied to the console
  from elsewhere).
- `<key>` names the game in that store:

  | Store | Key |
  |---|---|
  | `steam` | The Steam app ID: digits, no leading zero, at most 4294967295. |
  | `gog` | The GOG product ID: 1–20 digits, no leading zero. |
  | `epic` | The Epic catalog item ID: 32 lowercase hex digits. |
  | `imported` | A name key made from the game's name (see below). |

- `<slug>` is a short name for the recipe: lowercase letters, digits and single hyphens, 1–32 characters, no hyphen
  at either end. Once a slug has been released it belongs to that recipe for good: it is listed in `slugs.lst` and
  can't be used for another recipe of the same game, even after the recipe is removed.
- The file's `recipe_id` is `community-<store>-<key>-<slug>`, at most 128 bytes, for example
  [`community-steam-274520-120hz-left-stick-pointer`](recipes/steam/274520/120hz-left-stick-pointer.json).

A recipe belongs to one store's game. The same game bought in another store is a different game here, with its own
recipes.

### The game

Every recipe names its game in a `game` object, which must agree with the folder:

```json
"game": {"store": "steam", "key": "274520", "name": "Darkwood"}
```

`name` is the game's name as the store or the game's folder shows it, 1–120 characters. A release lists each game
under the name in its newest recipe.

`made_on` is optional. It records the store of the copy the recipe was made with, when that is not the game's store.
For example, an imported copy of a Steam game that has its Steam app ID shares its recipe under `steam/<app id>`
with `"made_on": "imported"`. Leave it out otherwise.

The older fields `app_id` and `steam_appid` are no longer accepted: the `game` object replaced them.

### Name keys for imported games

An imported game has no store ID, so its key is made from its name: the name is decomposed (Unicode NFKD), accents
are dropped, letters are lowercased, every run of other characters becomes one hyphen, hyphens are trimmed at both
ends, and the result is cut to 48 characters (trimmed again). A name with nothing left becomes `game`. Playden makes
the same key on every console.

| Name | Key |
|---|---|
| TMNT: Mutant Melee | `tmnt-mutant-melee` |
| Crème Brûlée & Clockwork — déjà vu | `creme-brulee-clockwork-deja-vu` |
| Half-Life 2 | `half-life-2` |
| ＦＵＬＬ　ＷＩＤＴＨ | `full-width` |
| `  ---  ` | `game` |

The maintainer may rename an imported game's folder in review, for example to tell two games with the same name
apart, until the game has a released recipe. After that the key is fixed, as it is part of the recipe IDs.

### The community block

The `community` block says who made the recipe and how it was tested:

| Field | Meaning |
|---|---|
| `format_version` | `1`. |
| `title` | Optional. The name Playden shows in the list of recipes, 1–60 characters. Without one, Playden shows "Recipe from <author>". |
| `author` | Optional. Who first shared the recipe. Without one, the recipe is shown as "from the community". |
| `contributors` | People whose changes were merged into it later. |
| `tested_kit` | The runtime kit version it was tested with, like `0.12` or `0.12-dev.20261022.1`. |
| `min_kit` | Only when the recipe really needs a newer kit. Usually left out. |
| `tested_firmware`, `tested_date` | PS5 system software and day of the test (`YYYY-MM-DD`). |
| `status` | `works`, `playable`, `broken` or `untested`. |
| `notes` | Anything a player must do outside Playden, like a PS5 video setting or the game's own options. |

`locked` stays empty in a community recipe. The full format is in
Playden's [docs/community-recipes.md](https://github.com/leolobato/playden-ps5/blob/main/docs/community-recipes.md).

## Share your settings

Sharing straight from Playden ("Share recipe") is coming. Until then:

1. Get the game running the way you like from **Game settings** in Playden.
2. Set the game's rating and note under **Compatibility and note**.
3. In **Game settings > Advanced**, choose **Export as recipe**. Playden saves the recipe in `/data/playden/logs`
   on the console. Copy it off over FTP.
4. If you like, give it a `community.title` and put your GitHub name in `community.author`; both are optional.
   Add anything a player must change outside Playden to `community.notes`.
5. Check that the file has the `game` object described above, pick a slug, set `recipe_id` to
   `community-<store>-<key>-<slug>`, save the file as `recipes/<store>/<key>/<slug>.json` and open a pull request.

Don't edit the program, arguments or overrides by hand: export them from a run that works. To improve an existing
recipe, change its file and add your name to `contributors`.

You can also attach the file to a [test report](https://github.com/leolobato/playden-ps5/issues/new/choose) and the
maintainer will add it.

## Checks

Every pull request runs `scripts/check.py` on the recipes it changes. It refuses a file whose store, key, slug,
`recipe_id` and `game` disagree, an unknown store, a key that doesn't follow its store's rule, a bad or reused slug,
a `made_on` that isn't another store, the old `app_id` and `steam_appid` fields, a kit version that isn't like
`0.12`, a non-empty `locked` list or a file over 16 KB. A pull request that changes the program, arguments, library
overrides, environment or registry values gets the label "changes how the game starts", and the maintainer reviews
those lines closely.

Later, CI will also run Playden's own `community_recipe_check`, built from the same parser the console uses. It
isn't built in CI yet; the workflow skips it until a runner has it (the `PLAYDEN_CHECKER` variable).

Run the same checks locally with Python 3 (no packages needed):

```sh
python3 scripts/check.py                                   # every recipe
python3 scripts/check.py --base-ref origin/main            # only what your branch changes, with the slug history
python3 scripts/check.py --checker path/to/community_recipe_check recipes/steam/274520/120hz-left-stick-pointer.json
python3 -m unittest discover -s scripts -p 'test_*.py'     # the scripts' own tests
```

## Releases

Playden doesn't read this repository's files directly: it downloads the latest release, which holds two files:

- `recipes.zip` with every recipe at `recipes/<store>/<key>/<slug>.json`.
- `index.json` (`format_version` 2) with a `games` list: each game's `store`, `key` and `name`, and its recipes (id,
  title, status, author, contributors, kits, firmware, date, `made_on` when set) with each recipe's `revision`, the
  sha256 of its file. It also holds the sha256 of the zip. Playden drops any file whose hash doesn't match. Games are
  listed by store (Steam, Epic, GOG, imported), then by key.

So nothing reaches players until the maintainer merges it and tags a release. `scripts/build_release.py --out DIR`
builds both files; the same recipes always give the same zip. It also records every released revision in
`revisions.lst` (`<recipe_id> <revision> <store>/<key> <published>`) and every slug in `slugs.lst`
(`<store>/<key> <slug>`). Both files only grow, so votes for older revisions still count and slugs are never reused.

Releases are tagged `r<YYYYMMDD>.<n>`. Before tagging, the maintainer runs `scripts/build_release.py` and commits the
updated `revisions.lst` and `slugs.lst`. CI builds and attaches the files when the tag is pushed. `make release` does
all of it from a clean `main` that matches GitHub's, with the commit and tag signed by the maintainer's GPG key;
`make release-dry-run` shows what it would record, and `make release-watch` follows CI until the release has its files.
The release notes list what's new since the previous release (recipes added, updated and removed, by game and recipe
ID), from `scripts/release_notes.py` comparing the two releases' `index.json`; the dry run previews them.

## License

The recipes are dedicated to the public domain under [CC0 1.0](LICENSE). By sending a pull request, you agree to
release your contribution under CC0.
