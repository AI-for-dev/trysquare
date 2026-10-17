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
from collections import deque
from contextlib import contextmanager
from pathlib import Path

from .measure import Fold, plus

#: Seconds between two snapshots. One is below what a reader notices and far above
#: what the write costs: a matrix of 180 runs writes a few kilobytes a second for two
#: hours, against agent runs that each hold megabytes in flight.
INTERVAL = 1.0

#: Seconds the live rate is measured over. Ten snapshots: one would swing with every
#: pause between two stream chunks, a minute would still show a turn that ended.
WINDOW = 10.0

#: Tokens and updates a run's ratio starts with: one token per update, which is what `pi`
#: streams from a vLLM server, counted as if this many tokens had measured it. Tool call
#: arguments arrive several tokens per chunk, so a short first turn of tool calls
#: measures 1.5 to 6 on its own, and that ratio applied to the thinking that followed
#: made one run sum to 27% more than it was billed. With 200, every run measured on
#: such a server summed within 7%.
PRIOR = 200

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

    The counters are split because they answer different questions. `turns`, `input`
    and `output` come off `message_end` and are exact, but arrive once a turn - minutes
    apart on a thinking model, minutes of a dashboard sitting still. `updates` counts
    the stream updates the sieve throws away, one per chunk the provider sends, and
    moves as the agent writes.

    Two rates are derived from them. `turn_rate` is exact: the last turn's output
    tokens over the time from its first update to its end, so the queue, the prompt
    processing and the tools are left out. `rate` is the updates of the last `WINDOW`
    seconds, converted to tokens at the ratio this attempt's finished turns measured,
    starting from `PRIOR`. The ratio is measured because a provider may send several
    tokens in one chunk.

    Plain attribute assignment throughout, and no lock. The keys of the published
    entry are fixed when it is built, so a snapshot taken mid-update sees one field
    from before and another from after - which for independent counters on a screen
    refreshed every second is not a defect worth a lock on the drain of a stream that
    carries a gigabyte.
    """

    def __init__(self, entry: dict) -> None:
        self._entry = entry
        self._fold = Fold()
        self._flow = None
        self._spent: dict = {}
        self._rates()

    def _rates(self) -> None:
        # (time, updates) at each snapshot of the window; the first update of the turn
        # being written; tokens and updates of the turns already finished.
        self._readings: deque = deque()
        self._began: tuple[float, int] | None = None
        self._streamed = [PRIOR, PRIOR]
        self._entry.update(rate=None, turn_rate=None)

    def attempt(self, n: int) -> None:
        """A new attempt truncates the trace, so the fold starts over with it."""
        self._fold = Fold()
        self._flow, self._spent = None, {}
        self._entry.update(combo=None, attempt=n, started=time.time(), updates=0)
        self._rates()
        self._numbers()

    def follow(self, flow) -> None:
        """`flow()` reads what this attempt's combo runs have ended so far, or None.

        A `/run` spends everything in combo's subagents, which never write to the
        stream, so the stream alone would show an hour of flow as a run doing nothing.
        Read on each snapshot rather than on each event, since the flow emits none.
        """
        self._flow = flow

    def published(self) -> dict:
        """The entry as a snapshot shows it, with the flow it follows read again."""
        try:
            read = self._flow() if self._flow else None
        except Exception:  # noqa: BLE001 - a decorative file may not end a matrix
            read = None
        if read:
            self._spent = read["usage"]
            self._entry["combo"] = {
                k: read[k] for k in ("visits", "agent", "path", "running", "directories")
            }
            self._entry["model_id"] = read["model"] or self._entry["model_id"]
            self._numbers()
        self._entry["rate"] = self._rate()
        return dict(self._entry)

    def _rate(self) -> float | None:
        now, updates = time.time(), self._entry["updates"]
        self._readings.append((now, updates))
        while now - self._readings[0][0] > WINDOW:
            self._readings.popleft()
        then, before = self._readings[0]
        if now <= then:
            return None
        tokens, chunks = self._streamed
        return round((updates - before) / (now - then) * tokens / chunks, 1)

    def update(self) -> None:
        """One stream update seen and dropped. The hot path: 16 679 of them per run."""
        now = time.time()
        if self._began is None:
            self._began = (now, self._entry["updates"])
        self._entry["updates"] += 1
        self._entry["seen"] = now

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
        if event.get("type") == "message_end":
            self._turn(event.get("message") or {})
        self._numbers()

    def _turn(self, message: dict) -> None:
        """A message ended: if the agent streamed it and was billed for it, a turn to time."""
        began, self._began = self._began, None
        output = (message.get("usage") or {}).get("output") or 0
        if message.get("role") != "assistant" or began is None or not output:
            return
        then, before = began
        self._streamed[0] += output
        self._streamed[1] += self._entry["updates"] - before
        seconds = self._entry["seen"] - then
        self._entry["turn_rate"] = round(output / seconds, 1) if seconds > 0 else None

    def _numbers(self) -> None:
        usage = plus(self._fold.usage, {"flow": self._spent})
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
        self._runs: dict[str, Watch] = {}
        # Around insertion and removal only, so the snapshot cannot walk a dict that
        # is changing size. What happens inside one entry needs no lock - see `Watch`.
        self._lock = threading.Lock()

    @contextmanager
    def watching(self, run_id: str, cell: str, repetition: int, session: Path | None = None):
        """Publishes one run for as long as it runs, and unpublishes it after.

        A run that ends leaves this file: what it produced belongs to `measures.json`,
        and holding it here too would make two records of one run, which is how they
        come to disagree.

        `session` is the directory the agent writes its session into, named so a reader
        can render what the agent is doing. A pointer rather than a copy: the archive
        takes the session when the run ends, and the entry goes with the run.
        """
        entry = {
            "cell": cell,
            "repetition": repetition,
            "session": str(session) if session else None,
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
            "combo": None,
        }
        watch = Watch(entry)
        with self._lock:
            self._runs[run_id] = watch
        try:
            yield watch
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
            watches = dict(self._runs)
        runs = {k: watch.published() for k, watch in watches.items()}
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
def watching(board, run_id: str, cell: str, repetition: int, session: Path | None = None):
    """`board.watching`, or nothing at all when no board is publishing.

    So a caller measuring one run never has to know whether anybody is looking, and
    `None` travels no further than this line.
    """
    if board is None:
        yield None
        return
    with board.watching(run_id, cell, repetition, session) as watch:
        yield watch


@contextmanager
def published(write, board: Board, interval: float = INTERVAL):
    """Writes `board` through `write` on the way in, every `interval`, and on the way out.

    `write` is a one-argument callable rather than an `Output`, so this module can be
    exercised without a tree and cannot reach anything else in one.
    """

    def publish(payload: dict) -> None:
        try:
            write(payload)
        except Exception:  # noqa: BLE001 - a decorative file may not end a matrix
            pass

    publish(board.snapshot())
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
