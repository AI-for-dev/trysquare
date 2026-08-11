"""A real git repository in a temporary directory, for tests that need one.

Real rather than faked, because the primitives it serves run `git ls-tree`, `git show`
and `git diff`, and a directory of files exercises none of them. The technique is the
one `test_pinned_source_offline.py:50-66` already proved fast and hermetic: `git init`,
one commit, one tag, and never a network.

Extracted here once a second test file needed it. Before that it lived inline, which was
correct: one caller is a hypothetical seam.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# A fixed identity, so the fixture does not depend on whose machine it runs on - an
# unset `user.email` makes `git commit` fail, and it is unset on CI.
#
# The PATH is derived rather than fixed. It was `/usr/bin:/bin:/usr/local/bin`, which
# names the same three directories everywhere and therefore looked machine-independent;
# it is not, because what sits at `/usr/bin/git` is a machine's business. On macOS
# without the command line tools accepted it is a stub that exits 69 on every call, and
# on a Nix profile the real git is somewhere else entirely - either way the whole suite
# fails on `git init` for a reason that has nothing to do with what it measures. Taking
# the directory of the git that will actually run keeps the environment minimal and
# makes it the *same* git the code under test uses.
GIT = shutil.which("git") or "/usr/bin/git"
ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": f"{Path(GIT).parent}:/usr/bin:/bin",
}


def git(directory: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=directory, env=ENV, capture_output=True, text=True, check=True
    )
    return done.stdout


def a_repo(files: dict[str, str], tag: str = "etalon-v1") -> Path:
    """A repository holding `files`, committed once and tagged."""
    directory = Path(tempfile.mkdtemp(prefix="assay-repo-"))
    write(directory, files)
    git(directory, "init", "-q")
    git(directory, "add", "-A")
    git(directory, "commit", "-qm", "etalon")
    git(directory, "tag", tag)
    return directory


def write(directory: Path, files: dict[str, str]) -> None:
    """Writes files into a tree, creating the directories they need."""
    for name, text in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text)
