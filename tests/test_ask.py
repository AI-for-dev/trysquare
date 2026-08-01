# SPDX-License-Identifier: BSD-3-Clause
"""The one question a launch asks, and the two rules that keep it from costing anything.

Off a terminal there is no question at all, which is what leaves every scripted launch and
every other assertion in this suite byte-identical to what it was. And no keystroke is a
default: the cheapest one must not be the one that spends a matrix of tokens.
"""

import io
import os
from unittest.mock import patch

from trysquare.ask import ABORT, DIFFERENCE, EVERYTHING, OFF, ask, wanted


class Terminal(io.StringIO):
    """A stream that claims to be one."""

    def isatty(self) -> bool:
        return True


ANSWERS = [
    ("d", DIFFERENCE, "measure only what is missing"),
    ("e", EVERYTHING, "measure all of it again"),
    ("a", ABORT, "nothing is spent"),
]


def reading(*given):
    """A reader that answers the given lines, in order."""
    answers = iter(given)
    return lambda prompt: next(answers)


class TestWhenAQuestionMayBeAsked:
    def interactive(self):
        return patch.dict(os.environ, {"TERM": "xterm"}, clear=True)

    def test_two_terminals_may_be_asked(self):
        with self.interactive():
            assert wanted(out=Terminal(), incoming=Terminal())

    def test_a_pipe_is_never_asked(self):
        with self.interactive():
            assert not wanted(out=io.StringIO(), incoming=Terminal())

    def test_nor_a_terminal_reading_from_a_pipe(self):
        """A prompt nobody can answer would hang the pipeline on an invisible question."""
        with self.interactive():
            assert not wanted(out=Terminal(), incoming=io.StringIO())

    def test_the_environment_can_refuse_it(self):
        """For a wrapper that cannot reach the arguments of the command it runs."""
        with patch.dict(os.environ, {OFF: "1"}, clear=True):
            assert not wanted(out=Terminal(), incoming=Terminal())
        with patch.dict(os.environ, {"TERM": "dumb"}, clear=True):
            assert not wanted(out=Terminal(), incoming=Terminal())

    def test_a_stream_that_cannot_answer_is_not_a_terminal(self):
        """What a closed stream does when asked, and what a stream with no `isatty` does:
        neither may become a question nobody is there to answer."""

        class Closed:
            def isatty(self):
                raise ValueError("I/O operation on closed file")

        with self.interactive():
            assert not wanted(out=Closed(), incoming=Terminal())
            assert not wanted(out=object(), incoming=Terminal())


class TestTheAnswer:
    def answered(self, *given) -> str:
        return ask(
            ["a matrix already exists"], ANSWERS, reader=reading(*given), out=lambda *_: None
        )

    def test_a_key_is_an_answer(self):
        assert DIFFERENCE == self.answered("d")

    def test_and_so_is_the_whole_word(self):
        """A prompt offering `[d]` has no business refusing `difference`."""
        assert DIFFERENCE == self.answered("difference")

    def test_case_and_surrounding_space_are_forgiven(self):
        assert EVERYTHING == self.answered("  E ")

    def test_an_unreadable_answer_is_asked_again(self):
        assert ABORT == self.answered("yes please", "a")

    def test_enter_is_not_an_answer(self):
        """No default: the cheapest keystroke must not be the one that spends a matrix."""
        assert EVERYTHING == self.answered("", "e")

    def test_end_of_input_aborts(self):
        def closed(prompt):
            raise EOFError

        assert ABORT == ask(["q"], ANSWERS, reader=closed, out=lambda *_: None)

    def test_an_interruption_aborts(self):
        def interrupted(prompt):
            raise KeyboardInterrupt

        assert ABORT == ask(["q"], ANSWERS, reader=interrupted, out=lambda *_: None)

    def test_the_menu_is_rendered_from_the_answers_offered(self):
        """One list for the menu and for the matching, so a gloss cannot come to describe
        an answer the reply no longer maps to."""
        said = []
        ask(["a matrix already exists"], ANSWERS, reader=reading("a"), out=said.append)
        assert "a matrix already exists" in said
        assert "    [d] measure only what is missing" in said
