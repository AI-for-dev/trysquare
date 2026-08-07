# SPDX-License-Identifier: BSD-3-Clause
"""The `trysquare` command.

This is the module named by `[project.scripts]`, and it is deliberately thin: the
eight subcommands and every guard they carry live in `trysquare.cli`, which is also
what `python -m trysquare` runs, so an installed command and a clone cannot drift
into measuring different things.

What is added here is only what appears the moment the name is typed in a shell
rather than called from Python:

* an interrupt is a decision, not a crash. A matrix takes hours, so Ctrl-C during
  one is normal, and it must say what survived rather than print a traceback over
  the run report. `interrupt.handled` is what makes the decision *act* rather than
  merely be reported: it stops the matrix and takes down the agents it started. It
  is installed here rather than in `cli`, because owning a process's signals is
  something only that process's entry point may do.
* a decision taken with `kill` is the same decision. SIGTERM is how a CI job
  cancellation, a `docker stop` and a `timeout` all arrive, and under its default
  disposition it killed the harness where it stood and left every agent running,
  spending for a full timeout with nobody left to read the result.
* a closed pipe is not an error either. `trysquare render ... | head` closes stdout
  early, and the interpreter's default is to complain at shutdown about an
  exception nobody can act on.
"""

from __future__ import annotations

import os
import signal
import sys

from .. import interrupt
from ..cli import main as run_command


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code, which the console script uses."""
    try:
        with interrupt.handled(signal.SIGINT, signal.SIGTERM):
            return run_command(argv)
    except KeyboardInterrupt as stopped:
        # State is written run by run, so what completed is on disk. Naming the
        # flag that resumes it is the difference between an interruption and a
        # lost afternoon.
        print(
            "\ninterrupted: completed runs are recorded; "
            "the same command with --resume measures only what produced nothing",
            file=sys.stderr,
        )
        # The shell's own convention, so a `kill` and a Ctrl-C are told apart by the
        # code as well as by the message. Read off the exception, which is what actually
        # ended this, rather than off the module: a plain `KeyboardInterrupt` carries no
        # signal and has always meant 130.
        signum = getattr(stopped, "signum", None) or interrupt.signalled() or signal.SIGINT
        return 128 + signum
    except BrokenPipeError:
        # Redirect what is left of stdout to devnull, so the flush at shutdown has
        # somewhere to go and Python stays quiet about a pipe the reader closed.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141


if __name__ == "__main__":
    sys.exit(main())
