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
  the run report.
* a closed pipe is not an error either. `trysquare render ... | head` closes stdout
  early, and the interpreter's default is to complain at shutdown about an
  exception nobody can act on.
"""

from __future__ import annotations

import os
import sys

from ..cli import main as run_command


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code, which the console script uses."""
    try:
        return run_command(argv)
    except KeyboardInterrupt:
        # State is written run by run, so what completed is on disk. Naming the
        # flag that resumes it is the difference between an interruption and a
        # lost afternoon.
        print(
            "\ninterrupted: completed runs are recorded; "
            "the same command with --resume measures only what produced nothing",
            file=sys.stderr,
        )
        return 130
    except BrokenPipeError:
        # Redirect what is left of stdout to devnull, so the flush at shutdown has
        # somewhere to go and Python stays quiet about a pipe the reader closed.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141


if __name__ == "__main__":
    sys.exit(main())
