# SPDX-License-Identifier: BSD-3-Clause
"""The cheat sheet is pinned to the parser, because a wrong flag is worse than no page.

A cheat sheet exists to be trusted at the keyboard without being read closely, which is
exactly the document that fails worst when it goes stale: a reader who has to check it
against `--help` has no use for it. The parser is the only authority on what the flags
are, so the page is compared against it here rather than proofread by hand - the same
reason `examples/validator.py` is run by the suite instead of quoted in a document.

Only the *surface* is checked. Whether a gloss describes its flag well is a judgement no
test can make; whether the flag exists is not.
"""

import re
from pathlib import Path

from trysquare.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
# The page is a stub that includes the sheet; the flags live in the raw HTML body, so
# both are read and neither can hold a flag the other contradicts.
PAGE = ROOT / "docs" / "reference" / "cheatsheet.md"
BODY = ROOT / "docs" / "reference" / "cheatsheet-body.html"


def parser_flags() -> set[str]:
    """Every long option of every subcommand, `--help` aside."""
    found = set()
    for action in build_parser()._subparsers._group_actions:  # the one registry there is
        for sub in action.choices.values():
            found |= {opt for opt in sub._option_string_actions if opt.startswith("--")}
    return found - {"--help"}


def sheet() -> str:
    return PAGE.read_text() + BODY.read_text()


def page_flags() -> set[str]:
    """`--help` aside on this side too: the sheet points a reader at it, and the parser
    gives every subcommand one, so comparing them says nothing."""
    return set(re.findall(r"--[a-z][a-z-]+", sheet())) - {"--help"}


class TestEveryFlagIsRealAndPresent:
    def test_the_page_invents_nothing(self):
        """A flag on the page that the parser does not define sends a reader to an
        error, which is the one thing a cheat sheet may never do."""
        assert page_flags() - parser_flags() == set()

    def test_the_page_leaves_nothing_out(self):
        """A flag added to the parser and not to the page turns the page into a subset
        of the truth, silently - so adding one here fails until the page catches up."""
        assert parser_flags() - page_flags() == set()

    def test_there_is_something_to_compare(self):
        """Both assertions above hold trivially over two empty sets, which is how a
        check like this passes for the wrong reason once a path or a private attribute
        moves underneath it."""
        assert len(parser_flags()) > 10


class TestEverySubcommandHasACard:
    def test_all_eight(self):
        """The sheet's own claim - eight commands - checked against the parser, since a
        ninth would otherwise be documented everywhere but here.

        A card title is what is looked for, not the bare word: `run` and `render` appear
        in one another's prose, so a substring match would pass for a command the sheet
        only mentions.
        """
        commands = {
            name for action in build_parser()._subparsers._group_actions for name in action.choices
        }
        text = sheet()
        assert len(commands) == 8
        assert [c for c in sorted(commands) if f'"ts-name">{c} ' not in text] == []
