#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Stands in for Playden's community_recipe_check --json in the tests.

A file whose name holds "hostile" is refused; every file gets a flag naming its program. Set FAKE_CHECKER_GARBAGE=1
to print something that isn't JSON.
"""
import json
import os
import sys

paths = [arg for arg in sys.argv[1:] if arg != "--json"]
if os.environ.get("FAKE_CHECKER_GARBAGE"):
    print("Segmentation fault")
    sys.exit(139)
files = []
for path in paths:
    with open(path, encoding="utf-8") as handle:
        recipe = json.load(handle)
    hostile = "hostile" in os.path.basename(path)
    files.append({
        "path": path,
        "recipe_id": recipe.get("recipe_id"),
        "ok": not hostile,
        "refusals": [{"field": "launch_entries", "message": "program is outside the game folder"}] if hostile else [],
        "flags": ["program: " + str(recipe.get("executable"))],
        "command": "wine " + str(recipe.get("executable")),
    })
print(json.dumps({"files": files}))
sys.exit(1 if any(not f["ok"] for f in files) else 0)
