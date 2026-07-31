"""A bar for the loops that take hours, and an honest estimate of what is left.

A matrix is dozens of agent runs of several minutes each. Until now the only thing
an operator saw was one line per finished run, which says what *was* measured and
nothing about what is left: no count, no elapsed time, no arrival estimate. On a
matrix that runs overnight, "still working" and "hung" looked the same from the
terminal.

Three rules hold this together.

**The record scrolls, the bar is pinned.** Every line a command printed before
still prints, unchanged, above the bar. `Bar.line` exists so a caller never has to
know whether a live region currently owns the terminal.

**The estimate is the average rate since launch, not a recent one.** See
`eta_seconds`.

**Off means off.** When output is not a terminal - a pipe, a redirect, a test
capturing stdout - there is no bar and no escape sequence, and `Bar.line` is a
plain `print`. That is what keeps a piped run byte-identical to what it printed
before this module existed.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TextColumn,
)

#: Turns the bar off without a command line. For wrappers and CI, which cannot
#: always reach the arguments of the command they run.
OFF = "TRYSQUARE_NO_PROGRESS"


def eta_seconds(completed: int, total: int, elapsed: float) -> float | None:
    """Seconds left, from the throughput since launch. `None` until it means one.

    Rich's own `TimeRemainingColumn` divides by `Task.speed`, which averages
    completions inside a thirty-second window. A matrix of ten-minute runs completes
    nothing at all inside most thirty-second windows, so that column would read
    `-:--:--` for the whole matrix except the instants a batch lands.

    Throughput since launch is what an operator computes by hand, and on a saturated
    pool it is exactly right rather than approximately: five runs of `T` done out of
    thirty-two gives `27 x T/5`, which is what the remaining twenty-seven will take
    five at a time. It counts the ramp before the first completion instead of
    discarding it, and it steps down in batches rather than swinging by minutes
    every few seconds.

    It over-estimates only at the tail, where fewer runs are left than there are
    workers. An estimate that arrives early is the one to prefer.
    """
    if total <= 0 or completed <= 0 or elapsed <= 0:
        return None
    return max(0.0, (total - completed) * elapsed / completed)


def clock(seconds: float) -> str:
    """A duration at the scale it is read at.

    A matrix runs for hours, so `1h 12m` is the useful reading and `72m` is not.
    Both the elapsed and the remaining column use this, so the two read alike.
    """
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def wanted(stream=None, no_progress: bool = False) -> bool:
    """Whether a bar should be drawn at all.

    `NO_COLOR` is deliberately not read: it asks for no colour, not for no motion,
    and rich already honours it for styling.
    """
    stream = sys.stdout if stream is None else stream
    if no_progress or os.environ.get(OFF) or os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):  # a StringIO, or a stream already closed
        return False


class _ClockColumn(ProgressColumn):
    """One duration of the task, rendered by `clock`."""

    def __init__(self, of):
        self.of = of
        super().__init__()

    def render(self, task: Task) -> str:
        value = self.of(task)
        return "--" if value is None else clock(value)


class Bar:
    """A counter being advanced, and a `print` that survives it.

    Constructed with `progress=None` when there is no terminal to draw in, so a
    caller has one code path either way.
    """

    def __init__(self, progress: Progress | None = None, task_id=None):
        self._progress = progress
        self._task_id = task_id

    @property
    def enabled(self) -> bool:
        return self._progress is not None

    def tick(self, step: int = 1) -> None:
        """Advances the bar. Past the total is allowed: a resumed matrix can fire
        its callback more often than it planned to, and that must not raise."""
        if self._progress is not None:
            self._progress.advance(self._task_id, step)

    def line(self, text: str = "") -> None:
        """Writes one line above the bar, or straight out when there is no bar."""
        if self._progress is None:
            print(text, flush=True)
            return
        # `markup=False` keeps a run's `detail` - or a traceback - from being read
        # as rich's own square-bracket syntax and vanishing from the record.
        # `highlight=False` because the numbers here are measurements, not syntax.
        # `soft_wrap=True` wraps the way `print` does, never cropping to the width
        # of the terminal.
        self._progress.console.print(text, markup=False, highlight=False, soft_wrap=True)

    def warn(self, text: str) -> None:
        """The same, for what belongs on stderr.

        It only reaches stderr when the bar is off. A live region owns the terminal
        it draws in, and a second stream writing into that region tears it; the bar
        is only ever on when stdout *is* a terminal, and then both streams are that
        same terminal anyway. Redirect or pipe either one and the bar is off, which
        is the case where the distinction can still be observed.
        """
        if self._progress is None:
            print(text, file=sys.stderr, flush=True)
            return
        self.line(text)


@contextmanager
def bar(total: int, label: str, enabled: bool = True) -> Iterator[Bar]:
    """A bar pinned to the bottom for the length of the `with`, or nothing at all.

    Nothing at all is a `Bar` too. And it is a context manager because an interrupt
    during a matrix is normal: `Progress` restores the cursor on the way out, before
    the entry point catches the interrupt and prints what survived.
    """
    if not enabled or total <= 0:
        yield Bar()
        return

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("elapsed"),
        _ClockColumn(lambda task: task.elapsed),
        TextColumn("left"),
        _ClockColumn(
            lambda task: eta_seconds(int(task.completed), int(task.total or 0), task.elapsed or 0.0)
        ),
        refresh_per_second=4,
    )
    with progress:
        yield Bar(progress, progress.add_task(label, total=total))
