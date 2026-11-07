# SPDX-License-Identifier: CC0-1.0
"""Helpers for the unit tests: a throwaway recipes repository built from tests/fixtures."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
FAKE_CHECKER = str(FIXTURES / "fake_checker.py")
GOOD_ID = "community-steam-274520-120hz-left-stick-pointer"


class TempRepo:
    """A git repository in a temporary folder. Commits are never signed."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory(prefix="recipes-test-")
        self.root = Path(self._dir.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")

    def close(self):
        self._dir.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root)] + list(args), check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout.decode("utf-8")

    def commit(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def place(self, fixture, key="274520", slug="120hz-left-stick-pointer", store="steam", fix_id=True):
        """Copies a fixture to recipes/<store>/<key>/<slug>.json; fix_id rewrites its recipe_id and its game's store
        and key to match. The text is edited in place, so the file keeps its size and layout."""
        text = (FIXTURES / fixture).read_text(encoding="utf-8")
        if fix_id:
            text = re.sub(r'"recipe_id": "[^"]*"', '"recipe_id": "community-%s-%s-%s"' % (store, key, slug), text, count=1)
            text = re.sub(r'("game": \{"store": )"[^"]*"(, "key": )"[^"]*"', r'\1"%s"\2"%s"' % (store, key), text, count=1)
        path = self.root / "recipes" / store / key / ("%s.json" % slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path.relative_to(self.root).as_posix()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def remove(self, relative):
        path = self.root / relative
        if path.is_dir():
            shutil.rmtree(str(path))
        else:
            path.unlink()
