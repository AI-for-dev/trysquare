# SPDX-License-Identifier: BSD-3-Clause
"""The agent's stream never becomes an object.

A matrix was killed twice by the machine, not by a bug anyone could read: the harness
held every byte the agent wrote, and one runaway run reached 136 GB. The stream is the
one input here whose size nobody controls, so the property is not "it is usually small"
but "no size can make the harness hold it".

Nothing here spends a token.
"""

import json
import sys
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

        assert trace.stat().st_size == 64 * MB
        assert peak < 4 * measure.LINE_LIMIT
        assert outcome.usage["turns"] == 0

    def test_an_over_long_line_costs_only_itself(self, tmp_path):
        """What follows a line too long to be an event is still read."""
        trace = tmp_path / "trace.jsonl"
        with trace.open("wb") as sink:
            sink.write(b"{" + b"x" * (measure.LINE_LIMIT + 1024) + b"}\n")
            sink.write(message("kept").encode() + b"\n")

        found = measure.read_file(trace)
        assert found.usage["turns"] == 1
        assert found.response == "kept"


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

        def fake_run(cwd, args, timeout, trace):  # noqa: ARG001
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
