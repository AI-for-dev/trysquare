# SPDX-License-Identifier: BSD-3-Clause
"""Stopping, when the operator asks.

A matrix runs for hours, so interrupting one is normal rather than exceptional. Two
things made it neither quick nor clean, and both come from the same absence: nothing
owned the processes the harness starts.

**Nothing stopped.** `ThreadPoolExecutor` shuts down with `wait=True`, and CPython puts
the shutdown sentinel *behind* every pending work item, so the whole queue still ran. A
matrix of thirty-two runs stopped at the fifth spent another hour, with the bar frozen
and nothing printed. Cancellation has to be cooperative - `concurrent.futures` joins its
workers at interpreter shutdown, so not even `sys.exit` escapes - and cooperative
cancellation usually means a flag tested in a dozen places, one of which is always
forgotten.

**One door instead of a dozen checks.** Every subprocess in this package goes through
`run`, and `run` refuses to open once the operator has asked to stop. A queued run
therefore dies at its first `git clone` without anyone having written a check for it,
and a run already in flight dies when its child does. The one flag test outside this
module is at the top of `runner.one_run`, and it exists only so a worker released from a
lock does not get as far as creating a directory.

**Children are session leaders.** `start_new_session=True` is set here, not at the call
sites, because it is inseparable from the killing: `os.killpg` then reaps the whole
descendant tree, which is what it takes to stop a validator's pytest or an agent's own
tool subprocesses. It also removes an accident this used to rely on. Children shared the
terminal's process group, so a Ctrl-C reached them for free - and only a Ctrl-C did.
A `kill`, a CI cancellation, a `docker stop` reached the harness alone and orphaned every
agent to burn tokens for a full timeout with nobody watching. Now every route is the same
route.

**Asking, then taking.** The first signal asks: the flag goes up, every child gets
SIGTERM, and `Stopped` unwinds the main thread. A watchdog then guarantees an end even if
a child ignores signals - SIGKILL at `GRACE`, and the process leaves at `DEADLINE`. A
second signal takes that path at once. This is the whole reason the escalation is armed
by the handler rather than by `stop`: a `stop` called while the main thread is already
unwinding needs no deadline.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

#: How long a child gets to answer SIGTERM before it is killed outright.
GRACE = 2.0

#: How long the whole shutdown gets before the process leaves without it.
DEADLINE = 5.0


class Stopped(KeyboardInterrupt):
    """A subprocess that the operator's interrupt cancelled, raised instead of run.

    A `KeyboardInterrupt` rather than an `Exception`, and that is load-bearing rather
    than decorative. `runner.one_run` wraps a whole measurement in `except Exception`,
    so that one frozen run cannot cost the matrix, and every failure path there ends in
    a `Run` carrying a state. A cancellation caught by it would be **written down**: a
    run interrupted after the agent produced tokens keeps `valid`, and `valid` is not in
    `outputs.RESUMABLE`, so no later `--resume` could reach it again - a run with no
    metrics and no diff, recorded as measured forever. Passing through `except Exception`
    untouched is what leaves a cancelled run unrecorded, and therefore still `missing`
    for the resume that follows.
    """

    def __init__(self, signum: int | None = None, message: str = "") -> None:
        super().__init__(message or "stopped")
        self.signum = signum


_stopping = threading.Event()
_signum: int | None = None

_lock = threading.Lock()
_children: set[subprocess.Popen] = set()

_hard_exit: list = []

# Only ever set by `reset`, so a watchdog armed by one test cannot outlive it. Nothing
# in a real run sets it: the escalation is meant to run to its end, including through
# the worker join that `concurrent.futures` performs at interpreter shutdown, which is
# the last place a wedged child can hold the process.
_done = threading.Event()


def stopping() -> bool:
    """Whether the operator has asked for this to end."""
    return _stopping.is_set()


def signalled() -> int | None:
    """The signal that asked, when one did. `None` for a stop asked for in Python."""
    return _signum


def reset() -> None:
    """Back to a state where work may start, and any armed watchdog disarmed.

    For tests. A process that has been asked to stop does not resume.
    """
    global _signum
    _signum = None
    _stopping.clear()
    _done.set()
    with _lock:
        _children.clear()
    _hard_exit.clear()


def on_hard_exit(callback) -> None:
    """Registers something to do before the process leaves without unwinding.

    For what owns the terminal rather than what owns a file: `os._exit` skips every
    `finally`, so a live region abandoned mid-draw leaves the terminal with no cursor.
    Files need nothing here - `outputs.write_json` writes a neighbour and renames it,
    so a hard exit during a write leaves the previous complete file.
    """
    _hard_exit.append(callback)


def stop(signum: int | None = None) -> None:
    """Refuses every child from here on, and takes down the ones already running.

    Idempotent, which is what lets the signal handler, the pool and a caller unwinding
    all say it without coordinating.
    """
    global _signum
    if _signum is None:
        _signum = signum
    _stopping.set()
    _terminate(signal.SIGTERM)


def _terminate(sig: int) -> None:
    """Signals the whole group of every child still running.

    The group rather than the process, because a child is a session leader here and
    what has to go is its descendants too: an agent's tool subprocesses, a validator's
    test runner. `poll` filters the ones already reaped, since a process group id is
    free for reuse the moment its leader is waited on.
    """
    with _lock:
        live = [proc for proc in _children if proc.poll() is None]
    for proc in live:
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            # Gone between the poll and the signal, or never ours to signal. Either
            # way there is nothing left to stop.
            pass


def run(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: dict | None = None,
    input: str | None = None,
    timeout: float | None = None,
    capture_output: bool = False,
    text: bool = False,
    stdin=None,
    stdout=None,
) -> subprocess.CompletedProcess:
    """`subprocess.run`, for a process the harness can still stop.

    A reimplementation rather than a wrapper, because `subprocess.run` gives no way to
    reach the `Popen` it creates, and reaching it is the entire point.

    Two refusals, and the second is the one that is easy to leave out. Before the spawn,
    so a queued run dies at its first child instead of measuring anything. And **after**
    the wait, because a child we killed comes back as `returncode -15` and every caller
    would read a verdict into it: an unproductive agent worth retrying, a
    `validator exited -15`, a `could not install dependencies`. An exit status we caused
    is not evidence about anything.

    `stdout` is a file the caller opened, and it overrides the pipe `capture_output`
    would have given: `communicate` accumulates a pipe **in the parent**, and the
    agent's stream has no size anyone controls. Handing the child a file is the only
    way a caller can bound its own memory. `capture_output` then still means stderr,
    which is small and which the caller needs as a value. `CompletedProcess.stdout` is
    None: the file is the output, on a timeout too, where the exception carries nothing
    because there was no pipe to carry it from.

    The timeout branch does what `subprocess.run` does and nothing more. For a piped
    caller the exception was built by `Popen._check_timeout` before `_communicate`
    reached its text-mode translation, so its partial output is bytes even under
    `text=True`. Collecting the streams again here would quietly break that.
    """
    if stopping():
        raise Stopped(signalled(), f"not started, the run was stopped: {argv[0]}")

    streams = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE} if capture_output else {}
    if stdout is not None:
        streams["stdout"] = stdout
    if input is not None:
        stdin = subprocess.PIPE

    with subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        text=text,
        stdin=stdin,
        start_new_session=True,
        **streams,
    ) as proc:
        with _lock:
            _children.add(proc)
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        except BaseException:
            proc.kill()
            raise
        finally:
            with _lock:
                _children.discard(proc)
        code = proc.poll()

    if stopping():
        raise Stopped(signalled(), f"killed while it ran: {argv[0]}")
    return subprocess.CompletedProcess(proc.args, code, out, err)


@contextmanager
def handled(*signums: int) -> Iterator[None]:
    """Answers the operator's signals for the length of the `with`, then gives them back.

    Off the main thread this installs nothing and says so by doing nothing: `signal.signal`
    refuses there, and raising would make `cli.main` uncallable from a test that runs it
    in a thread.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    previous = {number: signal.getsignal(number) for number in signums}
    for number in signums:
        signal.signal(number, _answer)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _answer(signum: int, _frame) -> None:
    """The first signal asks. The second takes."""
    if stopping():
        hard_exit(signum)
    stop(signum)
    _arm()
    # The same thing the default SIGINT handler does, so every `except KeyboardInterrupt`
    # already written keeps working, and a SIGTERM now unwinds like a Ctrl-C instead of
    # killing the process where it stands.
    raise Stopped(signum)


def _arm() -> None:
    """Starts the escalation that guarantees an end.

    A daemon thread: it must not itself be a reason the interpreter stays up, and a
    process that got out on its own takes it along without it ever firing.
    """
    _done.clear()
    threading.Thread(target=_escalate, name="trysquare-stop", daemon=True).start()


def _escalate() -> None:
    if _done.wait(GRACE):
        return
    _terminate(signal.SIGKILL)
    if _done.wait(DEADLINE - GRACE):
        return
    hard_exit()


def hard_exit(signum: int | None = None) -> None:
    """Leaves now, having given the terminal back first.

    `os._exit` skips every `finally`, every atexit hook, and the join
    `concurrent.futures` performs on its workers - which is exactly why it is here,
    since a worker wedged on a child that ignores signals is the only way to reach this.
    What it must not skip is whatever owns the terminal.
    """
    _terminate(signal.SIGKILL)
    for callback in list(_hard_exit):
        try:
            callback()
        except Exception:  # noqa: BLE001 - leaving is not the moment to raise
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (ValueError, OSError):
            pass
    os._exit(128 + (signum or _signum or signal.SIGINT))
