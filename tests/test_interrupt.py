"""Stopping, checked against real processes.

Most of this suite runs no subprocess at all, on purpose. This file is the exception,
and it has to be: what is under test is whether a child dies, and a mock cannot die.
The children are `sys.executable` sleeping, so nothing here needs the agent, the
network, or a token.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from trysquare import agent, interrupt

ROOT = Path(__file__).resolve().parent.parent

#: Long enough that nothing here finishes on its own. Every one of these children is
#: expected to be killed, so this is also the timeout a broken test would hit.
FOREVER = 30


def sleeper(marker: Path, extra: str = "") -> list[str]:
    """A child that says it has started, then waits to be stopped."""
    return [
        sys.executable,
        "-c",
        f"import pathlib, time\n{extra}\n"
        f"pathlib.Path({str(marker)!r}).write_text('up')\n"
        f"time.sleep({FOREVER})\n",
    ]


def until(condition, limit: float = 5.0) -> bool:
    """Polls rather than sleeps, so a slow machine costs nothing and a fast one waits."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def calling(argv, **kwargs) -> tuple[threading.Thread, dict]:
    """Runs a child on another thread, so the test itself can be the one that stops it."""
    outcome: dict = {}

    def call():
        try:
            outcome["returned"] = interrupt.run(argv, **kwargs)
        except BaseException as e:  # noqa: BLE001 - the exception is the measurement
            outcome["raised"] = e

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    return thread, outcome


class TestRefusingToStart:
    def test_a_child_is_not_spawned_once_stopping(self, monkeypatch):
        """The mechanism the whole design rests on: a queued run dies at its first
        child, so no cancellation check has to be written anywhere else."""
        spawned = []
        monkeypatch.setattr(
            subprocess, "Popen", lambda *a, **k: spawned.append(a) or pytest.fail("spawned")
        )
        interrupt.stop()
        with pytest.raises(interrupt.Stopped):
            interrupt.run([sys.executable, "-c", "pass"])
        assert spawned == []

    def test_a_run_that_was_never_stopped_is_ordinary(self):
        done = interrupt.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
        assert done.returncode == 0
        assert done.stdout.strip() == "hi"


class TestKillingWhatIsRunning:
    def test_a_running_child_is_killed_and_the_call_says_so(self, tmp_path):
        """The refusal has to come *after* the wait as well as before it.

        A child we killed comes back as `returncode -15`, and every caller reads a
        verdict into that: an agent worth retrying, a validator that failed, a
        dependency that would not install. An exit status we caused is not evidence.
        """
        marker = tmp_path / "up"
        thread, outcome = calling(sleeper(marker), capture_output=True, text=True)
        assert until(marker.is_file), "the child never started"

        interrupt.stop()
        thread.join(timeout=5)

        assert not thread.is_alive(), "the call did not come back after the stop"
        assert isinstance(outcome.get("raised"), interrupt.Stopped)
        assert "returned" not in outcome, "a killed child was reported as a result"

    def test_a_grandchild_does_not_survive_its_parent(self, tmp_path):
        """What `start_new_session` is for, and the only test that can prove it.

        A validator spawns a test runner, an agent spawns its tools. Signalling the
        child alone leaves those running, and they are most of what is being paid for.
        """
        marker, pid_file = tmp_path / "up", tmp_path / "grandchild"
        spawn = (
            "import subprocess\n"
            f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; "
            f"time.sleep({FOREVER})'])\n"
            f"pathlib = __import__('pathlib')\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        )
        thread, _ = calling(sleeper(marker, extra=spawn), capture_output=True, text=True)
        assert until(marker.is_file), "the child never started"
        grandchild = int(pid_file.read_text())

        interrupt.stop()
        thread.join(timeout=5)

        assert until(lambda: not alive(grandchild), limit=interrupt.GRACE + 3), (
            f"grandchild {grandchild} outlived the stop"
        )

    def test_a_child_that_ignores_the_ask_is_taken_anyway(self, tmp_path):
        """SIGTERM is a request. `GRACE` is how long the request is left standing."""
        marker = tmp_path / "up"
        deaf = "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        thread, outcome = calling(sleeper(marker, extra=deaf), capture_output=True, text=True)
        assert until(marker.is_file), "the child never started"

        interrupt.stop()
        interrupt._terminate(signal.SIGKILL)  # what the watchdog does at GRACE
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert isinstance(outcome.get("raised"), interrupt.Stopped)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class TestTimeout:
    """A timeout is not a stop, and the difference is the partial output."""

    def test_a_timeout_still_carries_what_the_child_had_written(self):
        script = "import sys, time\nsys.stdout.write('partial')\nsys.stdout.flush()\ntime.sleep(30)"
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            interrupt.run(
                [sys.executable, "-c", script], capture_output=True, text=True, timeout=0.5
            )
        assert caught.value.stdout, "the partial output was dropped on the way out"

    def test_a_timed_out_agent_keeps_what_it_streamed(self, monkeypatch, tmp_path):
        """A timed-out run is read from its trace, not from the exception.

        There is no pipe left for the exception to carry a partial output on, so what
        the child had flushed is on disk and is measured like any other stream. What
        the old contract could not say is the last assertion: a timeout is folded, not
        skipped, so a run that was billed before it froze still reports its turn.
        """
        monkeypatch.setattr(agent, "PI", sys.executable)
        event = json.dumps({"type": "message_end", "message": {"usage": {"input": 7, "output": 2}}})
        script = f"import sys, time\nsys.stdout.write({event!r} + chr(10))\nsys.stdout.flush()\ntime.sleep(30)"
        trace = tmp_path / "trace.jsonl"
        outcome = agent.run(tmp_path, ["-c", script], timeout=1, trace=trace)
        assert outcome.timed_out
        assert "message_end" in trace.read_text()
        assert outcome.usage["turns"] == 1


class TestRetrying:
    """A run that produced nothing is retried. A run something else ended is not."""

    def outcome(self, code: int, usage: dict | None = None) -> agent.Outcome:
        return agent.Outcome(
            trace=Path("trace.jsonl"),
            response="",
            error="",
            stderr="",
            code=code,
            duration=0,
            timed_out=False,
            usage=usage or {},
        )

    def attempts(self, monkeypatch, code: int) -> int:
        calls = []
        monkeypatch.setattr(
            agent,
            "run",
            lambda *a: calls.append(a) or self.outcome(code),  # noqa: ARG005
        )
        agent.run_until_productive(Path.cwd(), [], 10, 3, Path("trace.jsonl"))
        return len(calls)

    def test_a_signalled_agent_is_not_retried(self, monkeypatch):
        """One Ctrl-C used to buy three more agent runs, and so did the OOM killer."""
        assert self.attempts(monkeypatch, code=-signal.SIGTERM) == 1

    def test_an_agent_that_merely_said_nothing_still_is(self, monkeypatch):
        assert self.attempts(monkeypatch, code=0) == 3


class TestSignals:
    def test_an_interrupt_stops_and_unwinds(self):
        with pytest.raises(interrupt.Stopped) as caught:
            with interrupt.handled(signal.SIGINT):
                signal.raise_signal(signal.SIGINT)
        assert caught.value.signum == signal.SIGINT
        assert interrupt.stopping()
        assert interrupt.signalled() == signal.SIGINT

    def test_a_sigterm_is_answered_the_same_way(self):
        """The signal a CI cancellation, a `docker stop` and a `timeout` all send."""
        with pytest.raises(interrupt.Stopped) as caught:
            with interrupt.handled(signal.SIGTERM):
                signal.raise_signal(signal.SIGTERM)
        assert caught.value.signum == signal.SIGTERM

    def test_a_stop_is_still_a_keyboard_interrupt(self):
        """What lets every handler already written keep working, and one not to."""
        assert issubclass(interrupt.Stopped, KeyboardInterrupt)
        assert not isinstance(interrupt.Stopped(), Exception)

    def test_the_previous_handlers_are_given_back(self):
        before = signal.getsignal(signal.SIGINT)
        with interrupt.handled(signal.SIGINT, signal.SIGTERM):
            assert signal.getsignal(signal.SIGINT) is not before
        assert signal.getsignal(signal.SIGINT) is before

    def test_off_the_main_thread_nothing_is_installed(self):
        """`signal.signal` refuses there, and `cli.main` has to stay callable in one."""
        failed = []

        def install():
            try:
                with interrupt.handled(signal.SIGINT):
                    pass
            except Exception as e:  # noqa: BLE001
                failed.append(e)

        thread = threading.Thread(target=install)
        thread.start()
        thread.join()
        assert failed == []


FORCED = f"""
import os, signal, sys, time
sys.path.insert(0, {str(ROOT)!r})
from trysquare import interrupt

with interrupt.handled(signal.SIGINT):
    try:
        signal.raise_signal(signal.SIGINT)
    except interrupt.Stopped:
        pass
    # Nothing here answers the first ask, which is the situation the second is for.
    signal.raise_signal(signal.SIGINT)
    time.sleep({FOREVER})
"""


class TestTheSecondAsk:
    def test_pressing_again_leaves_at_once(self):
        """The guarantee of last resort. A worker wedged on a child that will not answer
        a signal is joined at interpreter shutdown, so nothing short of leaving without
        unwinding can end the process - and the operator has asked twice."""
        start = time.monotonic()
        done = subprocess.run(
            [sys.executable, "-c", FORCED], capture_output=True, text=True, timeout=FOREVER
        )
        assert done.returncode == 128 + signal.SIGINT
        assert time.monotonic() - start < interrupt.DEADLINE
