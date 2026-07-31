# SPDX-License-Identifier: BSD-3-Clause
"""What the licence claims about this repository, checked.

The README states that every source file of the package carries an SPDX identifier and
that the package declares the same expression. A claim a reader can verify by hand is a
claim the suite should verify too, or it becomes true only until the next file is added.

The shebang test is here rather than beside the example because it is the licence work
that broke it: an SPDX header was inserted above `#!/usr/bin/env python3`, and a shebang
only works as line 1. The kernel then handed the file to the shell, which executed its
docstring as commands. Every existing test passed, because they all invoke the example
with an explicit interpreter.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPDX = "SPDX-License-Identifier: BSD-3-Clause"
PACKAGE = ROOT / "trysquare"


def sources() -> list[Path]:
    """Every file the wheel ships that can carry a comment."""
    return sorted(p for p in PACKAGE.rglob("*") if p.suffix in (".py", ".ts") and p.is_file())


def scripts() -> list[Path]:
    """Every file in the repository that means to be executable by shebang.

    A shebang is looked for in the opening lines, not only the first, because one that
    has been pushed down is exactly what this must notice. Recognising a script only by
    its first line would classify a broken script as no script at all, and the sweep
    below would then pass over an empty list - which is how the defect got in.
    """
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in (".venv", "_build", "dist", "node_modules") for part in path.parts):
            continue
        if any(line.startswith("#!/") for line in path.read_text(errors="replace").split("\n")[:5]):
            found.append(path)
    return found


class TestTheIdentifierTravelsWithTheCode:
    def test_every_shipped_source_carries_it(self):
        """So a file that leaves this repository still says what it is."""
        bare = [p.relative_to(ROOT) for p in sources() if SPDX not in p.read_text()[:200]]
        assert bare == [], f"missing the SPDX identifier: {bare}"

    def test_there_is_something_to_check(self):
        """A sweep over an empty list passes for the wrong reason, which this repository
        has already been bitten by once - a preflight test that swept a directory which
        had stopped shipping went on passing over no files at all."""
        assert len(sources()) > 15

    def test_the_declared_expression_matches_the_licence_text(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        assert pyproject["project"]["license"] == "BSD-3-Clause"
        licence = (ROOT / "LICENSE").read_text()
        assert licence.startswith("BSD 3-Clause License")
        assert "The trysquare Authors" in licence

    def test_the_collective_holder_is_enumerated(self):
        """LICENSE names its holders collectively, so the notice is complete only with the
        file that lists them - which is why both ship in the wheel."""
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        assert set(pyproject["project"]["license-files"]) == {"LICENSE", "AUTHORS"}
        assert "@" in (ROOT / "AUTHORS").read_text()


class TestAShebangOnlyWorksFirst:
    def test_no_header_sits_above_a_shebang(self):
        """The defect this file exists for. With a comment above it, `./validator.py` is
        run by the shell, which reports `trysquare.assay: command not found` - an error
        about the *validator* that says nothing about what actually went wrong."""
        displaced = [
            p.relative_to(ROOT)
            for p in scripts()
            if not p.read_text(errors="replace").split("\n")[0].startswith("#!")
        ]
        assert displaced == [], f"a shebang must be line 1: {displaced}"

    def test_the_shipped_example_is_one_of_them(self):
        """Named explicitly: the sweep above is only as good as what it sweeps, and this
        is the file the documentation tells a reader to run."""
        assert (ROOT / "examples" / "validator.py") in scripts()


class TestTheVersionIsNamedOnce:
    """It was written in three places, with nothing deriving it.

    `pyproject.toml`, `trysquare/__init__.py` and `docs/conf.py` each carried the literal
    `0.1.0`, so a release could ship a wheel and a documentation page disagreeing about
    what it was - and a bump had to remember all three.
    """

    def declared(self) -> str:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        return pyproject["project"]["version"]

    def test_the_package_reports_what_the_project_declares(self):
        from trysquare import __version__

        assert __version__ == self.declared()

    def test_nothing_else_writes_a_version_literal(self):
        """The check that keeps this true: a second literal is how the drift started."""
        written = [
            path.relative_to(ROOT)
            for path in (ROOT / "trysquare" / "__init__.py", ROOT / "docs" / "conf.py")
            if f'"{self.declared()}"' in path.read_text()
        ]
        assert written == [], f"these repeat the version instead of deriving it: {written}"

    def test_the_copyright_a_page_shows_is_the_one_the_licence_names(self):
        """A documentation footer is a copyright notice, so it cannot name someone the
        licence does not."""
        conf = (ROOT / "docs" / "conf.py").read_text()
        assert 'copyright = "2026, The trysquare Authors"' in conf
