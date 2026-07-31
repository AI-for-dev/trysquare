"""Fixtures shared across the suite, and the rule that no test leaves a tree behind.

Most tests now ask for `tmp_path` and say so in their signature, which is the readable
form and the one to prefer. What lives here is the backstop for the places where a
directory is made somewhere a fixture cannot reach - a helper method called twice in one
test, a module-level factory - and the guarantee that a call added later is swept up too.
"""

import tempfile

import pytest


@pytest.fixture(autouse=True)
def temporary_directories_are_swept(tmp_path, monkeypatch):
    """Every `tempfile.mkdtemp()` a test makes lands under that test's `tmp_path`.

    The suite made 54 of them and removed none: no `rmtree`, no `addCleanup`, no
    `TemporaryDirectory` anywhere. Each run left 54 trees in `$TMPDIR`, some holding a
    git repository, and nobody noticed because a leak is silent by construction.

    Redirecting the factory rather than rewriting every caller is deliberate. It holds
    for the calls that are left, and for the next one somebody writes without thinking
    about it - a rule enforced in one place cannot be forgotten in another. `dir=` is
    still honoured, so a caller that already chose where to put a tree keeps it.

    Only the tests are affected: `trysquare/` itself never calls `mkdtemp`, and the one
    `TemporaryDirectory` in `assay.py` cleans up after itself already.
    """
    real = tempfile.mkdtemp

    def under_tmp_path(suffix=None, prefix=None, dir=None):
        return real(suffix=suffix, prefix=prefix, dir=dir if dir is not None else tmp_path)

    monkeypatch.setattr(tempfile, "mkdtemp", under_tmp_path)
