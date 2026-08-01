# SPDX-License-Identifier: BSD-3-Clause
"""One question, asked at the one moment it is worth asking.

A launch that finds results already on disk has three sensible answers - measure
everything again, measure only the difference, or stop - and until now it took the first
without asking. `--resume` and `--extend` were the answers, and both had to be known and
typed before the command was run.

Two rules keep the question from becoming a liability.

**A flag is never second-guessed.** `--resume`, `--extend` and `--overwrite` say what to
do, so nothing is asked. The question exists for the launch that said nothing.

**Off a terminal there is no question.** A prompt written to a log file with nobody to
answer it would hang a pipeline forever, so the detection below requires *both* streams to
be terminals, and `TRYSQUARE_NO_PROMPT=1` refuses on one that is. What a scripted launch
does is exactly what it did before this module existed - the announced overwrite - which is
what keeps a matrix in CI reproducible.

There is no default answer, and Enter is not one: the cheapest keystroke must not be the
one that spends a matrix of tokens.
"""

from __future__ import annotations

import os
import sys

#: Refuses the question without a command line. For wrappers and CI, which cannot always
#: reach the arguments of the command they run - the same reason `progress.OFF` exists.
OFF = "TRYSQUARE_NO_PROMPT"

DIFFERENCE = "difference"
EVERYTHING = "everything"
ABORT = "abort"


def wanted(out=None, incoming=None) -> bool:
    """Whether a question may be asked at all.

    Both streams, because a question needs a terminal to appear in *and* one to be answered
    from. Piped output with a terminal on stdin would block on a prompt nobody can see.
    """
    out = sys.stdout if out is None else out
    incoming = sys.stdin if incoming is None else incoming
    if os.environ.get(OFF) or os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(out.isatty()) and bool(incoming.isatty())
    except (AttributeError, ValueError):  # a StringIO, or a stream already closed
        return False


def ask(question: list[str], answers: list[tuple[str, str, str]], reader=input, out=print) -> str:
    """Asks until one of `answers` is given. End of input is `ABORT`.

    `answers` is a list of `(key, value, gloss)`, so the menu is rendered from the same
    material the reply is matched against and the two cannot drift apart. A key or the whole
    value is accepted, because a prompt offering `[d]` should not refuse `difference`.

    `reader` and `out` are arguments so the loop is testable without a terminal. What
    decides whether it is reached at all is `wanted` above.
    """
    while True:
        for line in question:
            out(line)
        for key, _, gloss in answers:
            out(f"    [{key}] {gloss}")
        try:
            given = reader("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            out("")
            return ABORT
        for key, value, _ in answers:
            if given in (key, value):
                return value
        out("  not one of the answers offered")
