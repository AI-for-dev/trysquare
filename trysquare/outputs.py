# SPDX-License-Identifier: BSD-3-Clause
"""The output tree, and the state that makes a matrix resumable.

One directory per experiment, and relaunching the same experiment overwrites it.
The archive of previous versions is git. A timestamped directory per launch would
accumulate variants of one experiment to choose between, which is optional
stopping through the back door.

The directory name carries the experiment's identity, which is also its guard:
anything that changes what is measured changes the name, so a quick run at three
repetitions writes to its own directory and *cannot* corrupt a published matrix at
ten.

The layout, as a literal block so the underscores are not read as markup::

    <output>/<scenario>_<etalon>_<provider>_<model>_n<N>/
      state.json        cells, runs, valid / empty / failed, attempt counters
      measures.json     one line per run
      synthesis.md      scores, costs, gaps and verdicts, written when complete
      runs/<cell>/<id>/           grouped, or runs/<id>/ when the tree is blind
        context.json  configuration.json  diff.patch
        session/*.jsonl          the agent's per-message record, one file per attempt
        validation/<mode>.json   validation/<mode>.stderr

`runs/` takes one of two layouts, and which one is a property of the tree rather than
of the measurement: a run id is an opaque hash, so a cell directory is the difference
between reading a diff and looking a hash up first. Blind is the layout that costs
something - a human filling a scoring form who knows which configuration they are
grading grades it better - so a scenario declaring a `form` validator is blind by
default and every other one is grouped. `Output` settles it once and is the only
place a run's path is built.

The layout is recorded in the state and deliberately *not* in the directory name: it
changes where bytes land, not what is measured, so it is not part of the experiment's
identity.

The session files are the agent's own trace, copied here so it outlives the work
directory - which is disposable by design and which the OS may purge. The raw event
stream is *not* copied: it is almost entirely streaming deltas and says nothing the
per-message record does not, at five hundred times the size.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from .measure import EMPTY, VALIDATOR_FAILED, Run

STATE = "state.json"
MEASURES = "measures.json"
SYNTHESIS = "synthesis.md"
SESSION = "session"
RUNS = "runs"
DIFF = "diff.patch"
CONFIGURATION = "configuration.json"
VALIDATION = "validation"
# What tells a run's directory from a cell's, whatever the run managed to produce.
RUN_MARKERS = (DIFF, CONFIGURATION, SESSION, VALIDATION)

# The two layouts `runs/` can take, as they are written in the state.
BY_CELL = "by-cell"
BLIND = "blind"

MISSING = "missing"

# Only these two states may be relaunched by a resume, because only these two
# produced no result at all. A validator failure is not among them: it needs
# re-scoring, which costs no tokens, and re-measuring it would let a resume
# change a run that had already produced something.
RESUMABLE = (MISSING, EMPTY)


def write_json(path: Path, payload) -> None:
    """One serialization for everything this tree holds, and one write.

    `indent=2` and real UTF-8, with a final newline, declared once: a rewrite of
    the same data must be byte-identical to the original wherever it is written
    from, or `replay --rescore` could not promise to leave untouched what it did
    not change.

    Written to a neighbour and renamed over the target, because `state.json` and
    `measures.json` are now rewritten after every run of a matrix that runs for
    hours. `os.replace` is atomic within a filesystem, so a kill during a write
    leaves the previous complete file rather than a truncated one - a ledger cut
    in half is worse than a ledger one run out of date, since nothing downstream
    can tell it is not the whole story.
    """
    temporary = path.with_name(path.name + ".writing")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def slug(value) -> str:
    """A value reduced to one path component.

    Tags legitimately contain a slash (`release/1.0`), which would otherwise turn one
    directory name into two and put the result somewhere nobody named.
    """
    return str(value).replace("/", "-").replace(" ", "-")


def experiment_name(scenario, repetitions: int | None = None) -> str:
    """The directory name, which is the experiment's identity."""
    n = repetitions if repetitions is not None else scenario.protocol["repetitions"]
    parts = [
        scenario.name,
        scenario.task["etalon"],
        scenario.agent["provider"],
        scenario.agent["model"],
        f"n{n}",
    ]
    return "_".join(slug(p) for p in parts)


def cell_dir(cell: str) -> str:
    """One path component per cell.

    A grid cell is named by joining its axis values with ` / `, which `slug` alone
    would turn into `nothing---off`. The separator becomes one underscore, as in the
    experiment's own directory name.
    """
    return slug(str(cell).replace(" / ", "_"))


def run_location(runs_dir: Path, run_id_: str, cell: str | None) -> Path:
    """Where one run's directory is. The only rule, so both layouts have one author.

    `cell` is `None` for a blind tree, and also for an id the scenario does not plan -
    a tree written under another scenario, whose runs have no cell here to be filed
    under and so stay at the root.
    """
    return runs_dir / cell_dir(cell) / run_id_ if cell else runs_dir / run_id_


def is_run_dir(directory: Path) -> bool:
    """Whether a directory is a run's rather than a cell's.

    By what a run leaves behind, because a cell directory holds nothing but run
    directories. A run that produced nothing still archives its session, and one that
    failed before its diff still holds its validation, so no single marker covers them
    all - and a run with none of these is a directory with nothing in it to read.
    """
    return any((directory / marker).exists() for marker in RUN_MARKERS)


def sniff_layout(runs_dir: Path) -> str | None:
    """The layout a tree already has, read from its shape. `None` when it has none.

    Read rather than assumed because a tree whose ledger was lost is still a tree
    somebody wants to render, and guessing wrong there does not fail loudly - it
    reports missing sessions for runs that are sitting on disk.
    """
    if not runs_dir.is_dir():
        return None
    for child in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if is_run_dir(child):
            return BLIND
        if any(is_run_dir(grandchild) for grandchild in child.iterdir() if grandchild.is_dir()):
            return BY_CELL
    return None


def ledger_run_dirs(experiment: Path, state: dict) -> dict[str, Path]:
    """Where each run of a ledger sits, in the layout that ledger records.

    For a reader that has the state in hand and wants the runs it names, rather than
    whatever directories happen to be there.
    """
    runs_dir = experiment / RUNS
    grouped = (state.get("layout") or sniff_layout(runs_dir)) == BY_CELL
    return {
        rid: run_location(runs_dir, rid, meta.get("cell") if grouped else None)
        for rid, meta in state.get("runs", {}).items()
    }


def archived_run_dirs(experiment: Path) -> list[Path]:
    """Every run directory under an experiment, in either layout.

    The leaf name is the run id in both, so a caller can still identify a run by the
    directory it reads - which is how a re-scoring finds the row to rewrite.
    """
    runs_dir = experiment / RUNS
    if not runs_dir.is_dir():
        return []
    found = []
    for child in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if is_run_dir(child):
            found.append(child)
        else:
            found.extend(sorted(p for p in child.iterdir() if p.is_dir() and is_run_dir(p)))
    return found


def run_id(scenario_name: str, cell: str, repetition: int) -> str:
    """A short opaque id, stable for a given (scenario, cell, repetition).

    Opaque so a form can be filled without knowing which cell is being scored, and
    stable so a resume can tell an absent run from one already done. The mapping
    back to the cell lives in `state.json`, deliberately not in the form.
    """
    key = f"{scenario_name}/{cell}/{repetition}".encode()
    return hashlib.blake2s(key, digest_size=4).hexdigest()


def cell_fingerprint(scenario, cell) -> str:
    """What one cell declares, as one short digest.

    The directory name is the experiment's identity. This is a cell's, one level down.
    Without it the ledger records that a run belongs to `rule / high` and nothing about
    what `rule / high` *was*, so editing a delta and resuming keeps the runs measured
    under the old declaration and completes the matrix with the new one: two
    configurations published under one name.

    Taken over `scenario.declared(cell)`, which is also what a run is built from, so
    what the ledger promises and what the agent received cannot drift apart.

    Over the **declaration**, not over the bytes it points at: a context brick is
    fingerprinted by its path, and editing that file in place is not caught here. That
    is the choice `etalon` already makes - a tag up front, the commit it resolved to
    recorded per run in `configuration.json` - and it is what keeps `resolve` off the
    disk, which is what keeps a dry run free.

    `sort_keys`, because reordering two keys of a TOML block must not cost a matrix.
    """
    payload = json.dumps(scenario.declared(cell), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.blake2s(payload.encode(), digest_size=8).hexdigest()


def per_cell(runs: dict) -> dict[str, int]:
    """How many runs a mapping of run ids holds for each cell, in first-seen order."""
    counts: dict[str, int] = {}
    for meta in runs.values():
        counts[meta["cell"]] = counts.get(meta["cell"], 0) + 1
    return counts


class Output:
    """The directory for one experiment, and everything written into it."""

    def __init__(
        self, root: Path, scenario, repetitions: int | None = None, grouped: bool | None = None
    ):
        self.scenario = scenario
        self.repetitions = repetitions or scenario.protocol["repetitions"]
        self.directory = Path(root) / experiment_name(scenario, self.repetitions)
        self.runs_dir = self.directory / RUNS
        self.cell_of = {i: meta["cell"] for i, meta in self.plan().items()}
        self.grouped = self._settle(grouped)
        if self.grouped:
            self._refuse_colliding_cells()

    # --- layout ---------------------------------------------------------

    def _settle(self, asked: bool | None) -> bool:
        """Which layout this tree has, from the three things that may decide it.

        An explicit answer wins, then the tree already on disk, then the rule: grouped
        unless a human scores something. Asking for a layout a non-empty tree
        contradicts is refused rather than applied, because the two layouts do not
        merge - every run would end up archived twice, once under each, and no reader
        could tell which half was this launch.
        """
        found = self.read_state().get("layout") or sniff_layout(self.runs_dir)
        if asked is None:
            return found == BY_CELL if found else not self.scenario.manual_metrics
        if found and found != (BY_CELL if asked else BLIND):
            raise RuntimeError(
                f"{self.directory} already holds runs laid out {found}, and this asks "
                f"for {BY_CELL if asked else BLIND}: the two layouts do not merge, so "
                f"every run would be archived twice. Write elsewhere, or delete it - "
                f"the archive of previous versions is git"
            )
        return asked

    def _refuse_colliding_cells(self) -> None:
        """Two cells reduced to one directory name would share it, and read as one."""
        taken: dict[str, str] = {}
        for cell in self.cell_of.values():
            first = taken.setdefault(cell_dir(cell), cell)
            if first != cell:
                raise RuntimeError(
                    f"cells {first!r} and {cell!r} both name the directory "
                    f"{cell_dir(cell)!r}, so a tree grouped by cell would file them "
                    f"together. Rename one, or measure with --no-group-by-cell"
                )

    def prepare(self) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        return self.directory

    @property
    def layout(self) -> str:
        """The layout as the state records it."""
        return BY_CELL if self.grouped else BLIND

    def location(self, run_id_: str) -> Path:
        """A run's directory, whether or not anything has been written to it."""
        cell = self.cell_of.get(run_id_) if self.grouped else None
        return run_location(self.runs_dir, run_id_, cell)

    def relative_run(self, run_id_: str) -> str:
        """The same path, relative to the experiment directory, for a link or a form."""
        return self.location(run_id_).relative_to(self.directory).as_posix()

    def run_dir(self, run_id_: str) -> Path:
        d = self.location(run_id_)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- state ----------------------------------------------------------

    def plan(self) -> dict:
        """Every run this experiment expects, keyed by its opaque id."""
        return {
            run_id(self.scenario.name, cell.name, i): {"cell": cell.name, "repetition": i}
            for cell in self.scenario.cells
            for i in range(self.repetitions)
        }

    def cell_drift(self, previous: dict) -> tuple[dict[str, int], dict[str, int]]:
        """Where the scenario and an existing ledger disagree about the cells.

        Two mappings of cell name to run count: the cells the scenario declares and
        the ledger does not know, and the cells the ledger holds and the scenario no
        longer declares. The directory name carries the scenario, the etalon, the
        agent and the repetition count - not the cells - so a scenario that grew a
        variant reuses the directory of the matrix already published, and nothing but
        this comparison can say so.

        Read from the ledger as found. `load_or_create_state` fills in the ids it does
        not know, which is the behaviour described here and also what erases the
        evidence for it.
        """
        wanted, known = per_cell(self.plan()), per_cell(previous.get("runs", {}))
        added = {name: n for name, n in wanted.items() if name not in known}
        stale = {name: n for name, n in known.items() if name not in wanted}
        return added, stale

    def fingerprints(self) -> dict[str, str]:
        """What every cell of this scenario declares, one digest each."""
        return {cell.name: cell_fingerprint(self.scenario, cell) for cell in self.scenario.cells}

    def measured_cells(self, state: dict) -> set[str]:
        """Cells holding a run a resume can no longer relaunch."""
        return {m["cell"] for m in state["runs"].values() if m["state"] not in RESUMABLE}

    def changed_cells(self, state: dict) -> list[str]:
        """Cells declaring something other than what their results were measured under.

        Only cells that already produced one: while every run of a cell is still
        missing or empty, nothing was measured under the old declaration and the new
        one simply replaces it. The same line `to_do` draws, for the same reason.

        A cell with no recorded fingerprint is not a changed cell. Ledgers written
        before fingerprints existed carry none, and inventing one now would record
        today's declaration as the one those runs were measured under.
        """
        measured = self.measured_cells(state)
        recorded = state.get("cells", {})
        return [
            name
            for name, fingerprint in self.fingerprints().items()
            if name in measured and recorded.get(name, fingerprint) != fingerprint
        ]

    def read_state(self) -> dict:
        path = self.directory / STATE
        if not path.is_file():
            return {}
        return json.loads(path.read_text())

    def write_state(self, state: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        write_json(self.directory / STATE, state)

    def initial_state(self, overrides: dict | None = None) -> dict:
        """A fresh ledger, recording the load that produced it.

        Concurrency and timeout are written down whatever their origin. They
        condition the retry count and therefore every cost column, so a matrix
        that does not record its own load cannot have its costs read.

        The repository is recorded by its **logical** name only. Where it actually came
        from - a directory, or a URL and the commit its tag pointed at - is written per
        run by `runner.archive`, because this method is called from `resolve()` during a
        `--dry-run`, and a field derived from the disk or the network would stop a dry
        run from being free.

        `cells` records what each cell declares, so a later launch can tell a cell that
        was renamed from a cell that was rewritten. Pure computation over the parsed
        scenario, like everything else here, so a dry run stays free.
        """
        return {
            "scenario": self.scenario.name,
            "repo": self.scenario.task["repo"],
            "etalon": self.scenario.task["etalon"],
            "provider": self.scenario.agent["provider"],
            "model": self.scenario.agent["model"],
            "thinking": self.scenario.agent["thinking"],
            "repetitions": self.repetitions,
            "concurrency": self.scenario.protocol.get("concurrency"),
            "timeout": self.scenario.protocol.get("timeout"),
            "layout": self.layout,
            "overrides": overrides or {},
            "complete": False,
            "cells": self.fingerprints(),
            "runs": {
                run_id_: {**meta, "state": MISSING, "attempts": 0}
                for run_id_, meta in self.plan().items()
            },
        }

    def load_or_create_state(self, overrides: dict | None = None) -> dict:
        """Reuses an existing ledger, or refuses when it is a different experiment.

        A different repetition count is a different experiment and belongs in its
        own directory. Since the count is part of the directory name this should be
        unreachable, but a ledger that disagrees with its own directory is worth
        catching rather than trusting.
        """
        state = self.read_state()
        if not state:
            return self.initial_state(overrides)
        if state.get("repetitions") != self.repetitions:
            raise ValueError(
                f"{self.directory / STATE} records {state.get('repetitions')} "
                f"repetitions, this run asks for {self.repetitions}: that is "
                f"another experiment, so another directory"
            )
        # A ledger may predate a scenario edit that added cells, or the layout field.
        # The layout itself is settled before this, against the tree the ledger describes.
        state.setdefault("layout", self.layout)
        for run_id_, meta in self.plan().items():
            state["runs"].setdefault(run_id_, {**meta, "state": MISSING, "attempts": 0})

        # A cell's fingerprint moves freely until the cell produces its first result and
        # is frozen after: the line `to_do` draws. `changed_cells` is therefore still
        # true once this loop has run, which is what lets a caller check afterwards.
        measured = self.measured_cells(state)
        cells = state.setdefault("cells", {})
        for name, fingerprint in self.fingerprints().items():
            if name not in measured:
                cells[name] = fingerprint
        return state

    def to_do(self, state: dict, only: tuple[str, ...] = ()) -> list[tuple[str, dict]]:
        """The runs a launch should perform.

        A valid run is never relaunched, whatever its result. That is the whole
        protection: a resume has no power over anything that produced a result.
        """
        out = []
        for run_id_, meta in state["runs"].items():
            if only and meta["cell"] not in only:
                continue
            if meta["state"] in RESUMABLE:
                out.append((run_id_, meta))
        return out

    def record(self, state: dict, run_id_: str, run: Run) -> None:
        entry = state["runs"].setdefault(run_id_, {"cell": run.cell, "repetition": run.repetition})
        entry["state"] = run.state
        entry["attempts"] = entry.get("attempts", 0) + run.attempts
        if run.detail:
            entry["detail"] = run.detail

    def summarise(self, state: dict) -> dict:
        counts: dict[str, int] = {}
        for meta in state["runs"].values():
            counts[meta["state"]] = counts.get(meta["state"], 0) + 1
        state["complete"] = counts.get(MISSING, 0) == 0 and counts.get(EMPTY, 0) == 0
        return counts

    # --- measures -------------------------------------------------------

    def write_measures(self, runs: list[Run]) -> Path:
        path = self.directory / MEASURES
        rows = []
        for r in runs:
            row = asdict(r)
            row["cell"] = r.cell
            rows.append(row)
        write_json(path, rows)
        return path

    def read_measures(self) -> list[Run]:
        path = self.directory / MEASURES
        if not path.is_file():
            return []
        return [Run(**row) for row in json.loads(path.read_text())]

    # --- sessions -------------------------------------------------------

    def archive_sessions(self, run_id_: str, session_dir: Path, exclude=frozenset()) -> list[Path]:
        """Makes a run's session archive equal to what this launch produced, as jsonl.

        Two filters, and both exist to keep one launch's traces from being read as
        another's.

        `exclude` names session files the caller does not want, by file name. The work
        directory keeps a run's session directory from one launch to the next - the run id
        is stable, so the path is - so copying whatever is there would import a previous
        measurement's traces.

        And the archive is **replaced**, not added to. Relaunching an experiment overwrites
        it, so a session left by the previous launch would be attributed to this one: the
        file count would stop matching `attempts`, and a page rendered from the old trace
        would sit there looking current.

        An absent `session_dir` is not an error: the agent may have failed to start at all,
        and that is a run to record, not an exception to raise.
        """
        target = self.location(run_id_) / SESSION
        if target.exists():
            shutil.rmtree(target)
        if not session_dir.is_dir():
            return []
        wanted = [p for p in sorted(session_dir.glob("*.jsonl")) if p.name not in exclude]
        if not wanted:
            return []
        target = self.run_dir(run_id_) / SESSION
        target.mkdir(parents=True, exist_ok=True)
        copied = []
        for source in wanted:
            destination = target / source.name
            destination.write_bytes(source.read_bytes())
            copied.append(destination)
        return copied

    def sessions(self, run_id_: str) -> list[Path]:
        """A run's archived sessions, in order. Empty when none were archived."""
        directory = self.location(run_id_) / SESSION
        return sorted(directory.glob("*.jsonl")) if directory.is_dir() else []

    def write_synthesis(self, text: str, suffix: str = "") -> Path:
        name = f"synthesis{suffix}.md" if suffix else SYNTHESIS
        path = self.directory / name
        path.write_text(text)
        return path

    def write_configuration(self, run_id_: str, configuration: dict) -> Path:
        path = self.run_dir(run_id_) / CONFIGURATION
        write_json(path, configuration)
        return path

    def write_validation(self, run_id_: str, mode: str, payload, stderr: str = "") -> None:
        d = self.run_dir(run_id_) / VALIDATION
        d.mkdir(parents=True, exist_ok=True)
        if payload is not None:
            write_json(d / f"{mode}.json", payload)
        if stderr:
            (d / f"{mode}.stderr").write_text(stderr)


def incomplete_note(counts: dict) -> str:
    """The line that keeps an incomplete matrix from reading as a result."""
    missing = counts.get(MISSING, 0)
    empty = counts.get(EMPTY, 0)
    failed = counts.get(VALIDATOR_FAILED, 0)
    bits = []
    if missing:
        bits.append(f"{missing} never launched")
    if empty:
        bits.append(f"{empty} produced nothing")
    if failed:
        bits.append(f"{failed} with a failed validator")
    if not bits:
        return ""
    return (
        "This matrix is incomplete: "
        + ", ".join(bits)
        + ". No synthesis is published; `--resume` completes it, and a failed "
        "validator is re-scored by `replay` at no token cost."
    )


def unmeasured_note(state: dict, runs: list[Run]) -> str:
    """The line for runs the ledger counts and `measures.json` has no row for.

    A matrix written before measures were saved run by run can hold this: the runs are
    on disk, the ledger calls them measured, and no table can see them. Publishing then
    reports a smaller `n` than was paid for, under a header naming the full repetition
    count, which is the whole family of defect this tool exists to refuse.

    Nothing here can repair it - a row is derived from a measurement, and re-scoring
    needs a row to start from - so the honest move is to name the runs and say what
    getting them back costs.
    """
    measured = {r.id for r in runs}
    lost = sorted(
        run_id
        for run_id, meta in state.get("runs", {}).items()
        if meta["state"] not in RESUMABLE and run_id not in measured
    )
    if not lost:
        return ""
    return (
        f"This matrix cannot be published: {len(lost)} run(s) the ledger counts as "
        f"measured have no row in {MEASURES} - {', '.join(lost)}.\n"
        f"An interrupted launch used to lose them, and no table can be trusted while "
        f"the two files disagree. Relaunching without `--resume` measures the matrix "
        f"again; the runs themselves are still in runs/ to read."
    )
