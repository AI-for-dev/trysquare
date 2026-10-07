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


def usage(clone: Path, before: set[str]) -> dict[str, dict]:
    """The usage of every combo run created since `before`, keyed by its directory.

    A run whose `usage.json` is absent was cut before combo closed it - a timeout, a
    kill. Its cost is unknown rather than zero, so it is refused like a changed layout.
    """
    found = {}
    for name in sorted(run_dirs(clone) - before):
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
