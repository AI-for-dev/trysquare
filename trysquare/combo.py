# SPDX-License-Identifier: BSD-3-Clause
"""What a combo flow run leaves in the clone, read as part of the run's usage.

`/run <flow> <input>` is an extension command, and pi runs it before any model turn.
The main session then makes no assistant turn at all: every token is spent in combo's
own in-process subagent sessions, which never reach the stream `measure` reads. Read
from the stream alone, a flow that did the whole task is a run that consumed nothing.

combo is named here rather than behind a declared contract, because there is no
contract to declare: the files below are combo's own record of a run, written for its
users, and trysquare reads them as they are - the way `assay.OPAQUE` names its
`subagent` tool. The layout is pinned to combo v0.4.0. A directory that looks like one
of its runs and does not hold what v0.4.0 writes is refused loudly, rather than read
as a run that cost nothing.

What v0.4.0 writes, for each `/run`: `runs/<YYYY-MM-DD_HH-MM-SS>[-n]/` at the root of
the clone, with `journal.jsonl` opened as the run starts and `usage.json` closed as it
ends, print mode included. `total` there is the whole run, delegated subagents and all.
The journal gets a `visit_start` and a `visit_end` line for each visit, as it happens.
Each subagent's pi session grows in `.sessions/<start>_<id>.jsonl`, and is copied to
its transcript, `<home>/<agent>.jsonl`, once the subagent closes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

LAYOUT = "combo v0.4.0"

RUNS = "runs"

#: A run directory's name. combo suffixes `-2`, `-3` when two runs start in one second.
RUN_DIR = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?")

#: Where a run keeps its subagents' pi sessions while they work.
SESSIONS = ".sessions"

#: The figures of `usage.json`'s `total` a run's usage is made of. Same names as trysquare's.
TOTAL = ("input", "output", "cacheRead", "cost", "turns")

#: The custom message `/run` leaves its answer in, ending `ok · runs/<ts>` or `failed at ...`.
ANSWER = "pipeline-result"

#: Why a `/run` that consumed nothing is empty: combo refuses one without a word in print mode.
REFUSED = "no combo run directory was created, so combo refused the /run (silently, in print mode)"


class LayoutError(RuntimeError):
    """A combo run directory that does not hold what the pinned layout writes."""


def run_dirs(clone: Path) -> set[str]:
    """The combo run directories in `clone`, by their path from its root."""
    runs = clone / RUNS
    if not runs.is_dir():
        return set()
    return {f"{RUNS}/{d.name}" for d in runs.iterdir() if d.is_dir() and RUN_DIR.fullmatch(d.name)}


def created(clone: Path, before: set[str]) -> list[str]:
    """The combo run directories in `clone` that are not in `before`, oldest first."""
    return sorted(run_dirs(clone) - before)


def usage(clone: Path, before: set[str]) -> dict[str, dict]:
    """The usage of every combo run created since `before`, keyed by its directory.

    A run whose `usage.json` is absent was cut before combo closed it - a timeout, a
    kill. Its cost is unknown rather than zero, so it is refused like a changed layout.
    """
    found = {}
    for name in created(clone, before):
        directory = clone / name
        if not (directory / "journal.jsonl").is_file():
            raise LayoutError(f"{name} has no journal.jsonl, which {LAYOUT} writes first")
        report = directory / "usage.json"
        if not report.is_file():
            raise LayoutError(
                f"{name} has no usage.json: the flow ended before combo measured it, "
                f"so its cost is unknown"
            )
        try:
            total = json.loads(report.read_text()).get("total") or {}
        except (json.JSONDecodeError, AttributeError) as e:
            raise LayoutError(f"{name}/usage.json is not the object {LAYOUT} writes: {e}") from e
        missing = [key for key in TOTAL if not isinstance(total.get(key), (int, float))]
        if missing:
            raise LayoutError(
                f"{name}/usage.json has no total {', '.join(missing)}, as {LAYOUT} writes"
            )
        found[name] = {key: total[key] for key in TOTAL}
    return found


#: What a visit path ends with when it is one iteration of a loop or one item of a `map`.
ITERATION = re.compile(r"(#\d+|\[\d+\])$")


def holders(path: str) -> list[str]:
    """The visits that can hold the visit `path`: `fix#2/code` is held by the loop `fix`."""
    segments = path.split("/")
    return [ITERATION.sub("", "/".join(segments[: i + 1])) for i in range(len(segments) - 1)]


def journal(directory: Path) -> list[dict]:
    """A run's journal so far. A line combo is still writing is skipped."""
    try:
        lines = (directory / "journal.jsonl").read_text().splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def working(entries: list[dict]) -> list[dict]:
    """The agent visits of a journal that started and have not ended, in the order they started.

    Only the last life counts: what a killed life left open is not running any more.
    """
    started: dict[str, dict] = {}
    for entry in entries:
        kind_ = entry.get("type")
        if kind_ == "life_start":
            started.clear()
        elif kind_ == "visit_start" and entry.get("kind") == "agent":
            started[entry.get("path")] = entry
        elif kind_ == "visit_end":
            started.pop(entry.get("path"), None)
    return [{"agent": start.get("agent"), "path": start.get("path")} for start in started.values()]


def progress(clone: Path, before: set[str]) -> dict | None:
    """Where the combo runs created since `before` are, for a live view.

    `running` names the subagents working now, and `directories` is where those runs
    are. A journal older than v0.4.0 holds no `visit_start`, so only the last subagent
    to finish is named. The tokens of a visit
    arrive with its end, counted once at the outermost visit holding it, as combo's
    own `costOf` does: a loop's usage already includes every iteration's. Unlike
    `usage`, this never raises, since combo is still writing what it reads.
    """
    spent, agents, running = [], [], []
    names = created(clone, before)
    for name in names:
        entries = journal(clone / name)
        ends = [entry for entry in entries if entry.get("type") == "visit_end"]
        paths = {end.get("path") for end in ends}
        spent += [end for end in ends if paths.isdisjoint(holders(str(end.get("path"))))]
        agents += [end for end in ends if end.get("kind") == "agent"]
        running += working(entries)
    if not (spent or running):
        return None
    last = agents[-1] if agents else {}
    return {
        "usage": {key: sum((end.get("usage") or {}).get(key, 0) for end in spent) for key in TOTAL},
        "visits": len(agents),
        "agent": last.get("agent"),
        "path": last.get("path"),
        "model": last.get("model"),
        "running": running,
        "directories": [str(clone / name) for name in names],
    }


def subagents(directory: Path) -> list[dict]:
    """The subagents of one run so far, in the order they started, each with its session.

    A session names no agent, and the transcript that does is only written once its
    subagent closes. So a session without one is named after the visits still running,
    taken in the order both started; one that matches none, a delegated child still
    working, goes by its id alone.
    """
    try:
        sessions = sorted((directory / SESSIONS).glob("*.jsonl"))
        transcripts = [p for p in directory.glob("*/**/*.jsonl") if p.parent.name != SESSIONS]
    except OSError:
        return []
    closed = {_session_id(p): p.relative_to(directory).with_suffix("") for p in transcripts}
    running = iter(working(journal(directory)))
    found = []
    for session in sessions:
        sid = session.stem.rpartition("_")[2]
        if home := closed.get(sid):
            name = {"agent": home.name, "path": home.parent.as_posix(), "running": False}
        else:
            visit = next(running, {"agent": None, "path": None})
            name = {**visit, "running": True}
        found.append({"id": sid, "session": session, **name})
    return found


def _session_id(path: Path) -> str | None:
    """The id in a pi session's header line."""
    try:
        with path.open() as f:
            return json.loads(f.readline()).get("id")
    except (OSError, ValueError, AttributeError):
        return None
