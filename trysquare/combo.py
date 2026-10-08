# SPDX-License-Identifier: BSD-3-Clause
"""What a combo flow run leaves in the clone, read as part of the run's usage.

`/run <flow> <input>` is an extension command, and pi runs it before any model turn.
The main session then makes no assistant turn at all: every token is spent in combo's
own in-process subagent sessions, which never reach the stream `measure` reads. Read
from the stream alone, a flow that did the whole task is a run that consumed nothing.

combo is named here rather than behind a declared contract, because there is no
contract to declare: the files below are combo's own record of a run, written for its
users, and trysquare reads them as they are - the way `assay.OPAQUE` names its
`subagent` tool. The layout is pinned to combo v0.3.0. A directory that looks like one
of its runs and does not hold what v0.3.0 writes is refused loudly, rather than read
as a run that cost nothing.

What v0.3.0 writes, for each `/run`: `runs/<YYYY-MM-DD_HH-MM-SS>[-n]/` at the root of
the clone, with `journal.jsonl` opened as the run starts and `usage.json` closed as it
ends, print mode included. `total` there is the whole run, delegated subagents and all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

LAYOUT = "combo v0.3.0"

RUNS = "runs"

#: A run directory's name. combo suffixes `-2`, `-3` when two runs start in one second.
RUN_DIR = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?")

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


def ended(directory: Path) -> list[dict]:
    """The `visit_end` entries of a run's journal so far. A line combo is still writing is skipped."""
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
        if isinstance(entry, dict) and entry.get("type") == "visit_end":
            entries.append(entry)
    return entries


def progress(clone: Path, before: set[str]) -> dict | None:
    """What the combo runs created since `before` have ended so far, for a live view.

    combo journals a visit when it ends and not before, so the subagent named here is
    the last one to finish, and the tokens of the one working now arrive with its end.
    Each visit is counted once, at the outermost visit holding it, as combo's own
    `costOf` does: a loop's usage already includes every iteration's. Unlike `usage`,
    this never raises, since combo is still writing what it reads.
    """
    spent, agents = [], []
    for name in sorted(run_dirs(clone) - before):
        ends = ended(clone / name)
        paths = {end.get("path") for end in ends}
        spent += [end for end in ends if paths.isdisjoint(holders(str(end.get("path"))))]
        agents += [end for end in ends if end.get("kind") == "agent"]
    if not spent:
        return None
    last = agents[-1] if agents else {}
    return {
        "usage": {key: sum((end.get("usage") or {}).get(key, 0) for end in spent) for key in TOTAL},
        "visits": len(agents),
        "agent": last.get("agent"),
        "path": last.get("path"),
        "model": last.get("model"),
    }
