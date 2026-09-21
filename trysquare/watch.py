# SPDX-License-Identifier: BSD-3-Clause
"""`trysquare watch`: a matrix directory, read and served, while it fills.

A reader, and only a reader. Nothing here writes into an output tree, nothing in the
measurement path imports this module, and the server answers three fixed routes rather
than mapping a URL onto a file - so no request can name a path, and a matrix cannot be
damaged by somebody watching it.

It binds to `127.0.0.1`. A matrix directory holds prompts, diffs and session
transcripts, which are the work of whoever ran it and not something a harness may put
on a network interface because a dashboard was convenient.

**What it will not show.** While the matrix is incomplete, a cell is a count and
nothing else: `4/7`, never a median, an interval or a verdict. `runner.resolve` already
refuses to write a synthesis for a launch restricted by `--only`, and a screen is not a
better reason to publish an unfinished matrix than a command line was. Runs are
interleaved by repetition, so at any moment every cell holds about the same handful,
and an interval drawn over four runs would swing visibly at each one that lands - on a
projector, in front of a room, where somebody photographs it.

When the matrix completes, `cmd_run` has just written `synthesis.md` and its page
beside the ledger, and this links to them. The verdict is that file's to give:
computing it a second time here is how two answers to one question come to disagree.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .measure import Run, kind, rate, valid_runs
from .outputs import LIVE, STATE, SYNTHESIS, measures_in

PAGE = Path(__file__).parent / "dashboard.html"
SYNTHESIS_PAGE = Path(SYNTHESIS).with_suffix(".html").name

#: Seconds without a heartbeat past which a launch is presumed dead rather than busy.
#: Ten times the publishing interval: a ticker that misses one write is under load, one
#: that misses ten has stopped - and a launch that stops without a word, which is what
#: `SIGKILL` and a lost machine both do, is the one nobody otherwise gets to see.
SILENT_AFTER = 10.0

#: What a reader needs about the launch. Taken from `state.json`, which is the record;
#: `live.json` deliberately carries no second copy of any of it.
LEDGER_FIELDS = (
    "scenario",
    "provider",
    "model",
    "thinking",
    "etalon",
    "repetitions",
    "concurrency",
    "timeout",
    "overrides",
)


def _read(path: Path):
    """A JSON file, or None. Absent and unreadable are one answer here.

    The files this reads are rewritten underneath it: `write_json` renames a complete
    neighbour over each one, so a read sees either version whole. What it must never do
    is fail because a file is not there yet.
    """
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def progress(state: dict | None) -> dict:
    """How far the matrix is, by run state and by cell."""
    if not state:
        return {"planned": 0, "done": 0, "states": {}, "cells": {}}
    states: dict[str, int] = {}
    cells: dict[str, dict] = {}
    for meta in state.get("runs", {}).values():
        states[meta["state"]] = states.get(meta["state"], 0) + 1
        cell = cells.setdefault(meta["cell"], {"planned": 0, "done": 0})
        cell["planned"] += 1
        cell["done"] += meta["state"] != "missing"
    return {
        "planned": len(state.get("runs", {})),
        "done": sum(n for s, n in states.items() if s != "missing"),
        "states": states,
        "cells": cells,
    }


def columns(runs: list[Run]) -> list[str]:
    """The metrics a count can be taken of, in the order the rows first name them.

    Read off the rows rather than off the scenario, because a directory has to describe
    itself and `watch` is given one and nothing else. Booleans only, for the reason
    `table.scored_metrics` gives: a number has a median rather than a count, and a list
    has neither.
    """
    seen: dict[str, None] = {}
    for run in runs:
        for name, value in run.metrics.items():
            if kind(value) == "rate":
                seen.setdefault(name)
    return list(seen)


def tallies(runs: list[Run]) -> dict:
    """Per cell, how many runs said true out of how many could say.

    Over the runs that produced a measurement, and **not** under the scenario's
    `validity` filter, which lives in a file this command is not given. So a count here
    can exceed the synthesis's: `delivered` drops runs from that table and from no
    column of this one. The page says as much, because a count that quietly means
    something else than the published one is worse than no count.
    """
    names = columns(runs)
    by_cell: dict[str, list[Run]] = {}
    for run in valid_runs(runs):
        by_cell.setdefault(run.cell, []).append(run)
    return {
        "metrics": names,
        "cells": {
            cell: {"runs": len(rows), "counts": {n: list(rate(rows, n)) for n in names}}
            for cell, rows in by_cell.items()
        },
    }


def assemble(directory: Path, now: float | None = None) -> dict:
    """Everything the page draws, from the files the directory holds.

    Assembled here rather than in the browser, so the page renders an answer this
    module is answerable for, and so the answer is testable without a socket.
    """
    directory = Path(directory)
    state = _read(directory / STATE)
    live = _read(directory / LIVE)
    complete = bool(state and state.get("complete"))
    seen = (live or {}).get("seen")
    return {
        "directory": directory.name,
        "live": live,
        "silent": bool(live and not live.get("finished") and seen)
        and (now or time.time()) - seen > SILENT_AFTER,
        "ledger": {k: (state or {}).get(k) for k in LEDGER_FIELDS},
        "progress": progress(state),
        "tallies": tallies(measures_in(directory)),
        "complete": complete,
        "synthesis": SYNTHESIS_PAGE if (directory / SYNTHESIS_PAGE).is_file() else None,
    }


def handler_for(directory: Path):
    """The request handler for one directory, as a class of its own.

    A class per server rather than an attribute set on a shared one, so two watched
    matrices in one process cannot answer for each other.
    """

    class Handler(BaseHTTPRequestHandler):
        """Three routes, named one by one. A URL never becomes a path."""

        def do_GET(self) -> None:  # noqa: N802 - the name the stdlib dispatches on
            route = self.path.split("?", 1)[0]
            if route == "/":
                return self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
            if route == "/data":
                body = json.dumps(assemble(directory), ensure_ascii=False).encode()
                return self._send(body, "application/json; charset=utf-8")
            if route == f"/{SYNTHESIS_PAGE}" and (directory / SYNTHESIS_PAGE).is_file():
                return self._send(
                    (directory / SYNTHESIS_PAGE).read_bytes(), "text/html; charset=utf-8"
                )
            self.send_error(404)
            return None

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # The page polls; a cached answer is a matrix that appears to have stopped.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            """Silent: one line per poll, twice a second, buries everything else."""

    return Handler


def server(directory: Path, port: int = 0) -> ThreadingHTTPServer:
    """Bound to the loopback interface, not yet serving.

    Returned rather than run, so the caller can read the port it was given: `0` asks
    the system for a free one, which is what keeps two watched matrices apart.
    """
    return ThreadingHTTPServer(("127.0.0.1", port), handler_for(Path(directory)))
