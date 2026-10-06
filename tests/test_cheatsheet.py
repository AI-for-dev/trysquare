# SPDX-License-Identifier: BSD-3-Clause
"""The cheat sheet is pinned to the parser, because a wrong flag is worse than no page.

A cheat sheet exists to be trusted at the keyboard without being read closely, which is
exactly the document that fails worst when it goes stale: a reader who has to check it
against `--help` has no use for it. The parser is the only authority on what the flags
are, so the page is compared against it here rather than proofread by hand - the same
reason `examples/validator.py` is run by the suite instead of quoted in a document.

Only the *surface* is checked. Whether a gloss describes its flag well is a judgement no
test can make; whether the flag exists, and sits on the card of its command, is not.
"""

import re
from pathlib import Path

from trysquare.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
# The page is a stub that includes the sheet; the flags live in the raw HTML body, so
# both are read and neither can hold a flag the other contradicts.
PAGE = ROOT / "docs" / "reference" / "cheatsheet.md"
BODY = ROOT / "docs" / "reference" / "cheatsheet-body.html"


def flags(text: str) -> set[str]:
    """`--help` aside: the sheet points a reader at it, and the parser gives every
    command one, so comparing them says nothing."""
    return set(re.findall(r"--[a-z][a-z-]+", text)) - {"--help"}


def parsers() -> dict:
    """`trysquare` itself under `""`, then every subcommand under its name."""
    top = build_parser()
    found = {"": top}
    for action in top._subparsers._group_actions:  # the one registry there is
        found |= action.choices
    return found


def options(parser) -> set[str]:
    return flags(" ".join(parser._option_string_actions))


def cards() -> dict[str, str]:
    """Each command's card, and the invocation line of the masthead under `""` for the
    flags of `trysquare` itself, which precede any command."""
    body = BODY.read_text()
    found = {"": re.search(r'<div class="ts-invocations">.*?</div>', body, re.S)[0]}
    for card in re.findall(r'<article class="ts-cmd.*?</article>', body, re.S):
        found[re.search(r'"ts-name">([a-z]+) ', card)[1]] = card
    return found


class TestEveryFlagIsRealAndPresent:
    def test_the_page_invents_nothing(self):
        """A flag on the page that the parser does not define sends a reader to an
        error, which is the one thing a cheat sheet may never do."""
        defined = set().union(*map(options, parsers().values()))
        assert flags(PAGE.read_text() + BODY.read_text()) - defined == set()

    def test_each_card_holds_its_own_flags(self):
        """A flag listed only on another command's card reads, to someone scanning for
        the command at hand, as a flag it does not take. Compared both ways: a flag
        added to the parser fails here until its card catches up."""
        page = cards()
        assert {
            name: flags(page[name]) ^ options(parser)
            for name, parser in parsers().items()
            if flags(page[name]) != options(parser)
        } == {}

    def test_there_is_something_to_compare(self):
        """The assertions above hold trivially over empty sets, which is how a check
        like this passes for the wrong reason once a path or a private attribute moves
        underneath it."""
        assert len(set().union(*map(options, parsers().values()))) > 10


class TestEverySubcommandHasACard:
    def test_all_ten(self):
        """The sheet's own claim - ten commands - checked against the parser, since an
        eleventh would otherwise be documented everywhere but here.

        A card title is what is looked for, not the bare word: `run` and `render` appear
        in one another's prose, so a substring match would pass for a command the sheet
        only mentions.
        """
        assert len(parsers()) == 11
        assert cards().keys() == parsers().keys()
