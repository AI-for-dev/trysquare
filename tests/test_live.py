# SPDX-License-Identifier: BSD-3-Clause
"""What a matrix publishes about itself while it runs.

Two properties carry everything here. The numbers a launch shows while it runs must be
the numbers it records when it ends, because a dashboard nobody can trust is worse than
no dashboard. And the writer must be unable to cost a matrix: it writes a decorative
file, and a decorative file has no standing to end two hours of work.

Nothing here spends a token.
"""

import json
import threading
import time

import pytest

from trysquare import agent, live, measure
from tests.test_stream import fake_agent, message, update, writing  # noqa: F401 - a fixture


def board() -> live.Board:
    return live.Board({"scenario": "t", "started": 0.0, "finished": None})


class TestWhatIsPublished:
    def test_a_run_appears_while_it_runs_and_leaves_when_it_ends(self):
        b = board()
        with b.watching("abc", "none / off", 3):
            entry = b.snapshot()["runs"]["abc"]
            assert entry["cell"] == "none / off"
            assert entry["repetition"] == 3
            assert entry["state"] == live.RUNNING
        assert b.snapshot()["runs"] == {}, "a finished run is measures.json's to describe"

    def test_the_numbers_are_the_ones_the_ledger_will_record(self):
        """One fold fed two ways. Two implementations would drift, and the dashboard is
        the one of the pair nobody checks against the archive."""
        stream = [message("first"), message("second")]
        b = board()
        with b.watching("abc", "none / off", 0) as watch:
            for line in stream:
                watch.kept(line.encode())
            entry = b.snapshot()["runs"]["abc"]

        recorded = measure.read(measure.events("\n".join(stream))).usage
        assert entry["turns"] == recorded["turns"] == 2
        assert entry["input"] == recorded["input"]
        assert entry["output"] == recorded["output"]

    def test_the_pulse_counts_updates_and_never_claims_to_count_tokens(self):
        b = board()
        with b.watching("abc", "none / off", 0) as watch:
            for _ in range(5):
                watch.update()
            entry = b.snapshot()["runs"]["abc"]
        assert entry["updates"] == 5
        assert entry["output"] == 0, "an update carries no usage, so it is not a token"

    def test_the_model_that_answers_is_read_from_the_stream_not_the_session(self):
        """A stream emits no `model_change`: it names the model on every assistant
        message. Reading only the session's shape leaves the column empty for every
        run that never switches model, which is all of them."""
        b = board()
        event = json.loads(message("hello"))
        event["message"]["model"] = "gemma-4-31b"
        with b.watching("abc", "none / off", 0) as watch:
            watch.kept(json.dumps(event).encode())
            assert b.snapshot()["runs"]["abc"]["model_id"] == "gemma-4-31b"

    def test_a_session_style_model_change_is_read_too(self):
        b = board()
        with b.watching("abc", "none / off", 0) as watch:
            watch.kept(json.dumps({"type": "model_change", "model": "gemma-4-90b"}).encode())
            assert b.snapshot()["runs"]["abc"]["model_id"] == "gemma-4-90b"

    def test_a_new_attempt_starts_its_numbers_over(self):
        """The trace is truncated per attempt, so a fold carried across two of them
        would report the abandoned attempt's tokens as part of the kept one's."""
        b = board()
        with b.watching("abc", "none / off", 0) as watch:
            watch.attempt(1)
            watch.kept(message("first").encode())
            watch.attempt(2)
            entry = b.snapshot()["runs"]["abc"]
        assert entry["attempt"] == 2
        assert entry["turns"] == 0

    def test_a_line_that_is_not_an_event_is_ignored(self):
        b = board()
        with b.watching("abc", "none / off", 0) as watch:
            watch.kept(b"not json at all")
            assert b.snapshot()["runs"]["abc"]["turns"] == 0


class TestTheFileSaysWhenItStopped:
    def test_the_last_write_stamps_the_end(self):
        written = []
        with live.published(written.append, board(), interval=0.01):
            time.sleep(0.05)
        assert len(written) > 1, "the ticker wrote while the launch ran"
        assert written[-1]["finished"] is not None

    def test_a_run_still_in_flight_at_the_end_is_not_left_looking_alive(self):
        """Otherwise a file left by a campaign killed yesterday reads as one running
        today, which is the one lie a live view can tell that an archive cannot."""
        written = []
        b = board()
        with b.watching("abc", "none / off", 0):
            with live.published(written.append, b, interval=10):
                pass
        assert written[-1]["runs"]["abc"]["state"] == live.STOPPED

    def test_a_heartbeat_dates_every_snapshot(self):
        """The only thing that tells a killed launch from a working one: a `SIGKILL`
        writes no ending, so the file goes on claiming its runs are alive."""
        written = []
        with live.published(written.append, board(), interval=0.01):
            time.sleep(0.05)
        assert written[0]["finished"] is None
        assert written[0]["seen"] > 0
        assert written[-1]["seen"] >= written[0]["seen"]

    def test_an_interrupted_launch_still_stamps_the_end(self):
        written = []
        with pytest.raises(KeyboardInterrupt):
            with live.published(written.append, board(), interval=10):
                raise KeyboardInterrupt
        assert written[-1]["finished"] is not None


class TestTheWriterCannotCostAMatrix:
    def test_a_write_that_raises_is_swallowed(self):
        """A full disk costs a stale dashboard. It does not cost the matrix."""

        def refuse(_payload):
            raise OSError("no space left on device")

        with live.published(refuse, board(), interval=0.01):
            time.sleep(0.05)

    def test_the_ticker_never_holds_the_process_open(self):
        with live.published(lambda _payload: None, board(), interval=3600):
            pass
        alive = [t for t in threading.enumerate() if t.name == "trysquare-live"]
        assert all(t.daemon for t in alive)


class TestTheSieveFeedsTheBoard:
    """Through the real sieve and a real child: the drain is where the counting has to
    happen, and a unit test of `Watch` alone would not exercise it."""

    def test_what_the_stream_writes_reaches_the_entry(self, fake_agent, tmp_path):  # noqa: F811
        b = board()
        trace = tmp_path / "trace.jsonl"
        with b.watching("abc", "none / off", 0) as watch:
            agent.run(tmp_path, writing(message("done"), 4), timeout=60, trace=trace, watch=watch)
            assert b.snapshot()["runs"]["abc"]["turns"] == 4

    def test_the_discarded_updates_are_counted_rather_than_lost(self, fake_agent, tmp_path):  # noqa: F811
        b = board()
        trace = tmp_path / "trace.jsonl"
        with b.watching("abc", "none / off", 0) as watch:
            agent.run(tmp_path, writing(update("xy"), 6), timeout=60, trace=trace, watch=watch)
            assert b.snapshot()["runs"]["abc"]["updates"] == 6
        assert trace.stat().st_size == 0, "counted, and still not kept"


class TestWithNobodyLooking:
    def test_a_run_measures_the_same(self, fake_agent, tmp_path):  # noqa: F811
        """The property that lets `watch` stay optional everywhere it is threaded."""
        trace = tmp_path / "trace.jsonl"
        outcome = agent.run(tmp_path, writing(message("done"), 3), timeout=60, trace=trace)
        assert outcome.usage["turns"] == 3

    def test_watching_nothing_yields_nothing(self):
        with live.watching(None, "abc", "none / off", 0) as watch:
            assert watch is None
