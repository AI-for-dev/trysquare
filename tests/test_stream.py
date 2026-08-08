# SPDX-License-Identifier: BSD-3-Clause
"""The agent's stream never becomes an object.

A matrix was killed twice by the machine, not by a bug anyone could read: the harness
held every byte the agent wrote, and one runaway run reached 136 GB. The stream is the
one input here whose size nobody controls, so the property is not "it is usually small"
but "no size can make the harness hold it".

Nothing here spends a token.
"""

import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

from trysquare import agent, measure

MB = 1024 * 1024


def writing(payload: str, times: int) -> list[str]:
    """A fake agent that writes one line `times` over, then leaves."""
    script = f"import sys\nline = {payload!r} + chr(10)\nsys.stdout.write(line * {times})\n"
    return ["-c", script]


def message(text: str, padding: int = 0) -> str:
    return json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": text + "." * padding,
                "usage": {"input": 10, "output": 1},
            },
        }
    )


@pytest.fixture
def fake_agent(monkeypatch):
    monkeypatch.setattr(agent, "PI", sys.executable)


class TestTheStreamIsNeverHeld:
    def test_a_stream_far_larger_than_the_harness_never_becomes_an_object(
        self, fake_agent, tmp_path
    ):
        """The measurement that says the 136 GB cannot happen again.

        `tracemalloc` rather than a resident-size reading: it counts Python
        allocations exactly, where the allocator's high-water mark is a fact about
        the allocator and differs between the machine this was written on and CI.
        """
        trace = tmp_path / "trace.jsonl"
        lines = 2048
        outcome = agent.run(
            tmp_path, writing(message("done", padding=64 * 1024), lines), timeout=120, trace=trace
        )

        tracemalloc.start()
        measure.read_file(trace)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert trace.stat().st_size > 128 * MB
        assert peak < 8 * MB, "the stream was held after all"
        assert outcome.usage["turns"] == lines
        assert outcome.response.startswith("done")

    def test_a_stream_that_never_breaks_a_line_is_still_bounded(self, fake_agent, tmp_path):
        """The bound that `for line in file` would not give.

        Nothing in the format promises a newline. A reader that holds a whole line is
        only bounded if the writer emits them, and the writer is the thing under
        measurement.
        """
        trace = tmp_path / "trace.jsonl"
        script = f"import sys\nsys.stdout.write('x' * {64 * MB})\n"

        tracemalloc.start()
        outcome = agent.run(tmp_path, ["-c", script], timeout=120, trace=trace)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert peak < 4 * measure.LINE_LIMIT
        assert outcome.usage["turns"] == 0
        # Not one byte of it was an event, so the sieve kept none of it.
        assert trace.stat().st_size == 0

    def test_an_over_long_line_costs_only_itself(self, tmp_path):
        """What follows a line too long to be an event is still read."""
        trace = tmp_path / "trace.jsonl"
        with trace.open("wb") as sink:
            sink.write(b"{" + b"x" * (measure.LINE_LIMIT + 1024) + b"}\n")
            sink.write(message("kept").encode() + b"\n")

        found = measure.read_file(trace)
        assert found.usage["turns"] == 1
        assert found.response == "kept"


class TestTheSieve:
    """What no reader wants never reaches the disk.

    `pi` sends the whole accumulated message twice on every update to deliver a delta
    of two characters, so the stream costs the square of the answer. Measured on the
    campaign that found this: 1.001 GiB of `message_update` against 84 KB of
    everything else, and every measurement comes off that 84 KB.
    """

    def update(self, text: str) -> str:
        """One cumulative snapshot, shaped as `pi` writes it."""
        body = {"role": "assistant", "content": text, "usage": {"input": 1, "output": 1}}
        return json.dumps(
            {
                "type": "message_update",
                "assistantMessageEvent": {"delta": text[-2:], "partial": body},
                "message": body,
            },
            separators=(",", ":"),
        )

    def chattering(self, upto: int, answer: str) -> list[str]:
        """A child that snapshots its way to `upto` characters, then speaks once.

        Built inside the child rather than passed in: the whole point is a stream too
        big to be an argument, and the quadratic growth is what makes it so.
        """
        script = (
            "import json, sys\n"
            "for n in range(1, %d):\n"
            "    text = 'z' * n\n"
            "    body = {'role': 'assistant', 'content': text,"
            " 'usage': {'input': 1, 'output': 1}}\n"
            "    sys.stdout.write(json.dumps({'type': 'message_update',"
            " 'assistantMessageEvent': {'delta': text[-2:], 'partial': body},"
            " 'message': body}, separators=(',', ':')) + chr(10))\n"
            "sys.stdout.write(%r + chr(10))\n" % (upto, message(answer))
        )
        return ["-c", script]

    def test_the_updates_are_dropped_and_the_measurement_is_unchanged(self, fake_agent, tmp_path):
        trace = tmp_path / "trace.jsonl"
        outcome = agent.run(tmp_path, self.chattering(400, "the answer"), timeout=60, trace=trace)

        kept = trace.read_text()
        assert "message_update" not in kept
        assert outcome.response == "the answer"
        assert outcome.usage["turns"] == 1
        assert len(kept) < 1024, "only the answer should have survived"

    def test_a_message_end_quoting_the_dropped_name_is_kept(self, tmp_path):
        """The prefix match is what makes this safe; a search would lose the answer."""
        trace = tmp_path / "trace.jsonl"
        spoken = message("I looked at the message_update events")
        assert agent.DISCARDED not in spoken.encode()[: len(agent.DISCARDED)]

        with trace.open("wb") as keep:
            sieve = agent._Sieve(keep)
            with sieve.plumbed() as sink:
                sink.write(self.update("zz").encode() + b"\n")
                sink.write(spoken.encode() + b"\n")

        found = measure.read_file(trace)
        assert found.response == "I looked at the message_update events"
        assert found.usage["turns"] == 1


class TestTheCeiling:
    """A run that will not stop writing is stopped, and written down as an incident.

    Once the stream lives on disk, time is all that still bounds how many bytes a
    runaway produces, and the rate is the agent's. The ceiling is what turns "it fills
    the disk and takes the four runs beside it" into one cell somebody can read.
    """

    def forever(self) -> list[str]:
        """A child that writes without end, slowly enough not to flood a test run."""
        script = (
            "import sys, time\n"
            "block = 'x' * (256 * 1024) + chr(10)\n"
            "while True:\n"
            "    sys.stdout.write(block)\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.02)\n"
        )
        return ["-c", script]

    def test_a_runaway_is_stopped_and_refused(self, fake_agent, tmp_path):
        trace = tmp_path / "trace.jsonl"
        outcome = agent.run(tmp_path, self.forever(), timeout=60, trace=trace, ceiling=4 * MB)

        assert outcome.overflowed
        assert not outcome.produced_something
        assert "4 MB" in outcome.stderr
        assert trace.stat().st_size >= 4 * MB

    def test_a_run_that_derailed_after_working_is_still_refused(self, tmp_path):
        """A usage in the wreckage does not make the wreckage a measurement."""
        billed = {"input": 100, "output": 10, "turns": 1, "cacheRead": 0, "cost": 0.0, "retries": 0}
        outcome = agent.Outcome(
            trace=tmp_path / "trace.jsonl",
            response="",
            error="",
            stderr="",
            code=None,
            duration=0,
            timed_out=False,
            usage=billed,
            overflowed=True,
        )
        assert not outcome.produced_something

    def test_a_runaway_is_not_retried(self, monkeypatch, tmp_path):
        """Three runaways is the incident this ends, not the incident it triples."""
        calls = []

        def overrunning(cwd, args, timeout, trace, ceiling=None):  # noqa: ARG001
            calls.append(trace)
            return agent.Outcome(
                trace=trace,
                response="",
                error="",
                stderr="wrote more than 1024 MB and was stopped",
                code=None,
                duration=0,
                timed_out=False,
                usage={},
                overflowed=True,
            )

        monkeypatch.setattr(agent, "run", overrunning)
        _, tries = agent.run_until_productive(tmp_path, [], 10, 3, tmp_path / "t.jsonl", 1)
        assert (len(calls), tries) == (1, 1)

    def test_a_long_answer_is_no_longer_what_trips_it(self, fake_agent, tmp_path):
        """The ceiling weighs what is kept, and that is what makes it fair.

        Weighing the raw stream meant weighing the square of the answer, so the cells
        whose agent works at length hit it first - an exclusion that follows the
        treatment, which is the one thing a matrix must never do.
        """
        trace = tmp_path / "trace.jsonl"
        raw = tmp_path / "raw"
        # 3000 snapshots of a message growing to 3000 characters: about 9 MB of stream
        # for one answer, nine times a ceiling the run must not reach.
        outcome = agent.run(
            tmp_path,
            TestTheSieve().chattering(3000, "a long answer"),
            timeout=60,
            trace=trace,
            ceiling=1 * MB,
        )

        assert not outcome.overflowed
        assert outcome.produced_something
        assert outcome.response == "a long answer"
        assert not raw.exists()

    def test_a_stream_under_the_ceiling_is_untouched(self, fake_agent, tmp_path):
        trace = tmp_path / "trace.jsonl"
        outcome = agent.run(
            tmp_path, writing(message("done"), 4), timeout=60, trace=trace, ceiling=4 * MB
        )

        assert not outcome.overflowed
        assert outcome.produced_something
        assert outcome.usage["turns"] == 4

    def test_the_whole_group_stops_writing_not_only_the_child(self, fake_agent, tmp_path):
        """A runaway's own tool subprocesses hold the same file.

        Killing the leader alone leaves a grandchild writing into a trace the harness
        has already measured, so the file a validator reads is not the file that was
        scored.
        """
        marker = tmp_path / "grandchild.pid"
        inner = (
            "import sys, time\n"
            "block = 'x' * (256 * 1024) + chr(10)\n"
            "while True:\n"
            "    sys.stdout.write(block)\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.02)\n"
        )
        script = (
            "import pathlib, subprocess, sys\n"
            f"kid = subprocess.Popen([sys.executable, '-c', {inner!r}])\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(kid.pid))\n"
            "kid.wait()\n"
        )
        agent.run(tmp_path, ["-c", script], timeout=60, trace=tmp_path / "t.jsonl", ceiling=4 * MB)

        pid = int(marker.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        pytest.fail("the grandchild outlived the ceiling and kept writing")


class TestWhatTheTraceIsWorth:
    def test_a_run_that_said_nothing_still_leaves_its_trace(self, fake_agent, tmp_path):
        """The empty run is the one somebody wants to read.

        The trace used to be written after the emptiness test, so the run with nothing
        to show for itself was also the run with no evidence. It is the same argument
        the sessions are archived on.
        """
        trace = tmp_path / "trace.jsonl"
        error = json.dumps({"type": "error", "errorMessage": "upstream\nis  unavailable"})
        outcome = agent.run(tmp_path, writing(error, 1), timeout=30, trace=trace)

        assert not outcome.produced_something
        assert trace.is_file()
        assert outcome.error == "upstream is unavailable"

    def test_a_retry_reads_its_own_attempt_and_not_the_one_it_replaced(self, tmp_path):
        """Truncation is what keeps the numbers the last attempt's.

        Appending would sum a failed attempt's usage into the attempt that replaced
        it, and a run that never worked would be published as one that did.
        """
        trace = tmp_path / "trace.jsonl"
        trace.write_text(message("stale") + "\n")
        assert measure.read_file(trace).usage["turns"] == 1

        with trace.open("wb") as sink:
            sink.write(message("fresh").encode() + b"\n")
        found = measure.read_file(trace)
        assert found.usage["turns"] == 1
        assert found.response == "fresh"


class TestTheJudgeStreamStaysOutOfTheArchive:
    def test_the_judge_writes_its_trace_where_it_is_told(self, monkeypatch, tmp_path):
        """A stream in the published tree is 16 MB per run that `--extend` copies.

        The judge runs with its dossier as its working directory, and that dossier is
        inside `runs/<id>/validation/`. The trace is given rather than derived from it.
        """
        from trysquare import validation
        from trysquare.scenario import Validator

        seen = {}

        def fake_run(cwd, args, timeout, trace, ceiling=None):  # noqa: ARG001
            seen["trace"] = trace
            return agent.Outcome(
                trace=trace,
                response="",
                error="",
                stderr="",
                code=0,
                duration=0,
                timed_out=False,
                usage={},
            )

        monkeypatch.setattr(agent, "run", fake_run)
        dossier = tmp_path / "archive" / "validation" / "judge"
        dossier.mkdir(parents=True)
        workdir = tmp_path / "work"

        validation.run_judge(
            Validator(mode="judge", metrics=("ok",), config={}),
            dossier,
            "prompt",
            Path("brick.ts"),
            30,
            workdir / "judge.jsonl",
        )

        assert seen["trace"] == workdir / "judge.jsonl"
        assert dossier not in seen["trace"].parents
