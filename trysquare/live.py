# SPDX-License-Identifier: BSD-3-Clause
"""`live.json`: what a matrix is doing right now, while it is doing it.

A matrix runs for hours and the archive says nothing until a run ends. Between two
ends, the only witness a run leaves is its `trace.jsonl`, which lives in the work
directory - outside the output tree, keyed by experiment name rather than by output
directory, and purged by the machine. Reading it back is guessing; this file is the
harness saying.

Three rules hold it together.

**It is written, never read.** Nothing in the measurement path loads `live.json` back,
so no reader can change what is measured. A campaign that runs with nobody watching
runs identically.

**Its writer may fail.** The ticker is a daemon thread and swallows everything: a full
disk, a read-only directory or a serialization bug costs a stale dashboard, never a
matrix. The one thing a decorative file may not do is end a two-hour run.

**It says when it stopped being live.** The last write, on the way out of `published`
and whatever ended the launch, stamps `finished` and marks the runs still in flight as
stopped. Without it a file left by a campaign killed yesterday reads as a campaign
running today, which is the one lie a live view can tell that an archive cannot.

What it is **not** is a second ledger. `state.json` and `measures.json` remain the
record; this holds what has no other home, and a run that ends leaves it.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager

from .measure import Fold

#: Seconds between two snapshots. One is below what a reader notices and far above
#: what the write costs: a matrix of 180 runs writes a few kilobytes a second for two
#: hours, against agent runs that each hold megabytes in flight.
INTERVAL = 1.0

RUNNING = "running"
STOPPED = "stopped"


def _model_of(event: dict) -> str | None:
    """The model an event says answered, from either shape the agent writes.

    The two are not interchangeable, and `runner.recorded_model` only knows one of
    them. An archived **session** announces the model once, in a `model_change`; the
    **stream** never emits one and carries the name on every assistant message
    instead. A live view reads the stream, so reading only `model_change` leaves the
    column empty for every run that never switches model, which is all of them.
    """
    message = event.get("message") or {}
    if model := message.get("model"):
        return model
    return event.get("model") if event.get("type") == "model_change" else None


class Watch:
    """One run's live entry, and the only thing the agent module ever touches.

    The counters are split because they answer different questions and only one of
    them is honest about tokens. `turns`, `input` and `output` come off `message_end`
    and are exact, but arrive once a turn - a minute apart on a real run, which is a
    minute of a dashboard sitting still. `updates` counts the stream updates the sieve
    throws away, each carrying a delta of about two characters, and says only that the
    agent is writing. It is a pulse, not a rate, and nothing may present it as tokens.

    Plain attribute assignment throughout, and no lock. The keys of the published
    entry are fixed when it is built, so a snapshot taken mid-update sees one field
    from before and another from after - which for independent counters on a screen
    refreshed every second is not a defect worth a lock on the drain of a stream that
    carries a gigabyte.
    """

    def __init__(self, entry: dict) -> None:
        self._entry = entry
        self._fold = Fold()

    def attempt(self, n: int) -> None:
        """A new attempt truncates the trace, so the fold starts over with it."""
        self._fold = Fold()
        self._entry.update(attempt=n, started=time.time(), updates=0)
        self._numbers()

    def update(self) -> None:
        """One stream update seen and dropped. The hot path: 16 679 of them per run."""
        self._entry["updates"] += 1
        self._entry["seen"] = time.time()

    def kept(self, line: bytes) -> None:
        """One event the sieve kept, folded as `measure.read` would fold it."""
        try:
            event = json.loads(line)
        except ValueError:
            return
        self._fold.feed(event)
        if model := _model_of(event):
            self._entry["model_id"] = model
        self._entry["seen"] = time.time()
        self._numbers()

    def _numbers(self) -> None:
        usage = self._fold.usage
        self._entry.update(
            input=usage["input"],
            output=usage["output"],
            turns=usage["turns"],
            retries=usage["retries"],
            cost=usage["cost"],
        )


class Board:
    """The header a launch freezes, and the runs currently in flight.

    The header is taken once, at launch, and never recomputed. A reader of the
    dashboard is reading what this launch is doing, so it must not be told what the
    scenario says now: a scenario edited mid-campaign would silently relabel runs that
    were measured under the previous declaration.
    """

    def __init__(self, header: dict) -> None:
        self.header = dict(header)
        self._runs: dict[str, dict] = {}
        # Around insertion and removal only, so the snapshot cannot walk a dict that
        # is changing size. What happens inside one entry needs no lock - see `Watch`.
        self._lock = threading.Lock()

    @contextmanager
    def watching(self, run_id: str, cell: str, repetition: int):
        """Publishes one run for as long as it runs, and unpublishes it after.

        A run that ends leaves this file: what it produced belongs to `measures.json`,
        and holding it here too would make two records of one run, which is how they
        come to disagree.
        """
        entry = {
            "cell": cell,
            "repetition": repetition,
            "attempt": 0,
            "state": RUNNING,
            "started": time.time(),
            "seen": time.time(),
            "model_id": None,
            "input": 0,
            "output": 0,
            "turns": 0,
            "retries": 0,
            "cost": 0.0,
            "updates": 0,
        }
        with self._lock:
            self._runs[run_id] = entry
        try:
            yield Watch(entry)
        finally:
            with self._lock:
                self._runs.pop(run_id, None)

    def snapshot(self) -> dict:
        """What is in flight, stamped with the moment the launch last said so.

        `seen` is the only defence against the one death that writes nothing: a
        `SIGKILL`, an unplugged machine, an OOM kill. The launch cannot stamp
        `finished` on its way out because it never gets one, so the file keeps its last
        snapshot and goes on claiming that fifty runs are working. A heartbeat turns
        that into something a reader can tell: `finished` empty and `seen` minutes old
        is a launch that died without a word.
        """
        with self._lock:
            runs = {k: dict(v) for k, v in self._runs.items()}
        return {**self.header, "seen": time.time(), "runs": runs}

    def stopped(self) -> dict:
        """The last snapshot, saying that nothing here is running any more.

        Entries are marked rather than dropped: `execute` joins its workers before this
        is written, so ordinarily there are none left, and what this covers is the
        launch that leaves with runs still registered.
        """
        payload = self.snapshot()
        payload["finished"] = time.time()
        for entry in payload["runs"].values():
            entry["state"] = STOPPED
        return payload


@contextmanager
def watching(board, run_id: str, cell: str, repetition: int):
    """`board.watching`, or nothing at all when no board is publishing.

    So a caller measuring one run never has to know whether anybody is looking, and
    `None` travels no further than this line.
    """
    if board is None:
        yield None
        return
    with board.watching(run_id, cell, repetition) as watch:
        yield watch


@contextmanager
def published(write, board: Board, interval: float = INTERVAL):
    """Writes `board` through `write` every `interval`, and once more on the way out.

    `write` is a one-argument callable rather than an `Output`, so this module can be
    exercised without a tree and cannot reach anything else in one.
    """

    def publish(payload: dict) -> None:
        try:
            write(payload)
        except Exception:  # noqa: BLE001 - a decorative file may not end a matrix
            pass

    stop = threading.Event()

    def tick() -> None:
        while not stop.wait(interval):
            publish(board.snapshot())

    thread = threading.Thread(target=tick, daemon=True, name="trysquare-live")
    thread.start()
    try:
        yield board
    finally:
        stop.set()
        publish(board.stopped())
