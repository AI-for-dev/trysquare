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
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .measure import EMPTY, VALIDATOR_FAILED, Run, counted

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

# The repetition count at the end of a directory name. It is what tells one matrix of an
# experiment from another matrix of the same experiment, and therefore what lets a launch
# at twenty repetitions find the ten a launch before it already paid for.
STAMP = re.compile(r"_n(\d+)$")


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


def identity(scenario) -> str:
    """The directory name without its repetition count.

    What the name says about *what* is measured, as opposed to how many times. Two
    directories sharing it are the same experiment measured a different number of times,
    and `run_id` ignores the count, so the shorter one's runs carry the very ids the
    longer one asks for: they are those runs, not analogues of them.
    """
    parts = [
        scenario.name,
        scenario.task["etalon"],
        scenario.agent["provider"],
        scenario.agent["model"],
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


def experiment_name(scenario, repetitions: int | None = None) -> str:
    """The directory name, which is the experiment's identity."""
    n = repetitions if repetitions is not None else scenario.protocol["repetitions"]
    return f"{identity(scenario)}_n{n}"


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


def measures_in(directory: Path) -> list[Run]:
    """The rows a matrix directory holds, empty when it holds none.

    A free function because three callers read a matrix that is not their own: an
    experiment being carried from, and `compare`'s two sides.
    """
    path = Path(directory) / MEASURES
    if not path.is_file():
        return []
    return [Run(**row) for row in json.loads(path.read_text())]


def matrices(root: Path, scenario) -> dict[int, Path]:
    """Every matrix of this experiment already under `root`, by repetition count.

    Matched by reconstructing the name rather than by globbing it: a model name may
    legitimately carry `[` or `*`, which a glob would read as syntax and silently fail to
    match. A directory without a ledger is not a measurement, so it is not offered.
    """
    root = Path(root)
    if not root.is_dir():
        return {}
    prefix = identity(scenario)
    found = {}
    for directory in sorted(root.iterdir()):
        stamp = STAMP.search(directory.name)
        if not stamp or directory.name != f"{prefix}_n{stamp.group(1)}":
            continue
        if (directory / STATE).is_file():
            found[int(stamp.group(1))] = directory
    return found


@dataclass(frozen=True)
class Carried:
    """What one carry would move out of a lower matrix of the same experiment."""

    directory: Path
    repetitions: int
    entries: dict  # run id -> its ledger entry, state and attempts preserved
    rows: list  # their rows, in the source's own archived order
    # Where each run's tree actually is in the source, resolved against *its* layout - a
    # blind matrix and a grouped one file the same run in two places, and the target files
    # it under its own layout again.
    trees: dict
    concurrency: int | None
    timeout: int | None
    # The commit the source's runs say they measured, read from their archive. The ledger
    # records the repository by its logical name only, so a tag moved upstream between two
    # launches leaves no trace there - and a carry across a moved tag would mix two
    # baselines under one header.
    etalon_commit: str
    # What its cells declared when it measured them. Carried with the runs, so a cell
    # rewritten since is caught by `changed_cells` exactly as it is on a resume - without
    # them the new matrix would record today's declaration as the one those runs were
    # measured under, which is the defect the fingerprint exists to refuse.
    cells: dict
    # Its own provenance, so a chain of extensions stays readable end to end.
    chain: list
    # Runs its ledger counts as measured and its measures.json has no row for. They do not
    # travel: carrying one would import `unmeasured_note`'s defect into a fresh matrix.
    stranded: int
    # Fields of the source ledger that contradict this scenario. Named rather than raised,
    # because the two callers need it differently: `--extend` refuses on it, and a launch
    # without `--extend` uses it to say why it is not offering the carry.
    mismatch: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.entries)


def carryable(root: Path, scenario, plan: dict, repetitions: int) -> Carried | None:
    """The runs a lower matrix of this experiment already holds, or None.

    Reads, and never raises or writes: this is what a `--dry-run` announces, so it must
    cost nothing and it must be the same answer a real launch acts on.

    One source, never two. A matrix assembled out of three measurement sessions is not
    something a reader could be expected to unpick, so the candidates are ranked and the
    best one carries alone: a source that agrees on what is measured beats one that does
    not, and then the largest count wins - which is both the cheapest carry and the one an
    operator would predict.
    """
    lower = {n: d for n, d in matrices(root, scenario).items() if n < repetitions}
    if not lower:
        return None
    ranked = sorted(
        (_carried(d, n, scenario, plan) for n, d in lower.items()),
        key=lambda c: (not c.mismatch, c.repetitions),
    )
    best = ranked[-1]
    return best if len(best) or best.mismatch else None


def _carried(directory: Path, repetitions: int, scenario, plan: dict) -> Carried:
    """What carrying this one directory would move."""
    state = json.loads((directory / STATE).read_text())
    # Only what the target asks for, and only what produced something. A run absent from
    # the plan belongs to a cell this scenario no longer declares, and a run that produced
    # nothing is not an acquired result.
    kept = {
        run_id_: dict(meta)
        for run_id_, meta in state.get("runs", {}).items()
        if run_id_ in plan and meta.get("state") not in RESUMABLE
    }
    rows = [r for r in measures_in(directory) if r.id in kept]
    measured = {r.id for r in rows}
    stranded = sorted(set(kept) - measured)
    entries = {k: v for k, v in kept.items() if k in measured}

    carried_cells = {meta["cell"] for meta in entries.values()}
    runs_dir = directory / RUNS
    grouped = (state.get("layout") or sniff_layout(runs_dir)) == BY_CELL
    trees = {
        run_id_: run_location(runs_dir, run_id_, meta["cell"] if grouped else None)
        for run_id_, meta in entries.items()
    }

    # What decides *what is measured* and is not already guaranteed by the directory name.
    # The name carries the scenario, the etalon, the provider and the model; these two it
    # does not, so these two are the ones that can disagree while the names match.
    declared = {"thinking": scenario.agent["thinking"], "repo": scenario.task["repo"]}
    return Carried(
        directory=directory,
        repetitions=repetitions,
        entries=entries,
        rows=rows,
        trees=trees,
        cells={name: f for name, f in state.get("cells", {}).items() if name in carried_cells},
        concurrency=state.get("concurrency"),
        timeout=state.get("timeout"),
        etalon_commit=_archived_commit(trees),
        chain=list(state.get("carried", ())),
        stranded=len(stranded),
        mismatch=tuple(f for f, value in sorted(declared.items()) if state.get(f) != value),
    )


@dataclass(frozen=True)
class Prior:
    """What is already on disk that a launch has to decide about."""

    # This matrix's own ledger: what produced a result, and what produced nothing.
    kept: int
    leftovers: int
    # A lower matrix of the same experiment, when this one has no ledger of its own.
    source: Carried | None

    @property
    def offerable(self) -> bool:
        """Whether there is a decision to take at all.

        A source that disagrees on what is measured is not one: it cannot be carried
        whatever anybody answers, and the launch says why on its own.
        """
        return bool(self.kept or self.leftovers or (self.source and not self.source.mismatch))


def prior(output: Output) -> Prior:
    """What this launch is about to overwrite, carry or complete.

    Reads and decides nothing, so what a launch would ask can be tested without a terminal
    and a question can be built from the same facts the plan is.
    """
    runs = output.read_state().get("runs", {})
    leftovers = sum(1 for meta in runs.values() if meta["state"] in RESUMABLE)
    return Prior(
        kept=len(runs) - leftovers,
        leftovers=leftovers,
        source=(
            None
            if runs
            else carryable(output.root, output.scenario, output.plan(), output.repetitions)
        ),
    )


def _archived_commit(trees: dict) -> str:
    """The commit the carried runs say they measured, from the first that says anything."""
    for tree in trees.values():
        path = tree / CONFIGURATION
        if path.is_file():
            commit = json.loads(path.read_text()).get("etalon_commit")
            if commit:
                return commit
    return ""


class Output:
    """The directory for one experiment, and everything written into it."""

    def __init__(
        self, root: Path, scenario, repetitions: int | None = None, grouped: bool | None = None
    ):
        self.scenario = scenario
        self.repetitions = repetitions or scenario.protocol["repetitions"]
        # `root` is kept rather than re-derived from `directory.parent`: it is where the
        # other matrices of this same experiment live, and a carry has to look there.
        self.root = Path(root)
        self.directory = self.root / experiment_name(scenario, self.repetitions)
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

    def replayed(self, state: dict, cells: tuple[str, ...]) -> dict:
        """The ledger these cells' results are discarded from. Writes nothing.

        What `--overwrite CELL` means, one cell at a time instead of the whole matrix: a
        run of a named cell returns to `MISSING` and is measured again, and every other
        run of the ledger is left exactly as it was found.

        `attempts` returns to zero and `detail` goes, because both describe the
        measurement being discarded. So does the per-run `carried` flag: a run this launch
        measures itself is this matrix's own run, whatever matrix first paid for it.

        Their fingerprints are re-recorded, and that is the half `load_or_create_state`
        cannot do: it freezes the declaration of every cell that produced a result, so a
        cell re-measured under a new declaration would keep the digest of the old one and
        the next `--resume` would refuse the very runs this launch just measured. Only the
        named cells - a digest written for a cell nobody re-measures would claim today's
        declaration is the one its results were measured under.
        """
        named = set(cells)
        for meta in state["runs"].values():
            if meta["cell"] not in named:
                continue
            meta.update(state=MISSING, attempts=0)
            meta.pop("detail", None)
            meta.pop("carried", None)
        recorded = state.setdefault("cells", {})
        for name, fingerprint in self.fingerprints().items():
            if name in named:
                recorded[name] = fingerprint
        return state

    def seed(self, state: dict, carried: Carried) -> dict:
        """The ledger this matrix would have with a lower one's runs in it. Writes nothing.

        Shared by `resolve`, which must leave the disk untouched, and by `absorb`, which is
        the write. A carried run keeps its own state, usage and attempts: nothing is
        re-measured, so nothing may be restated - and `to_do` then has nothing to relaunch
        for it, which is the whole mechanism.

        The record is a **list**, appended to the source's own, so an experiment extended
        twice can still be read back to the launch that first measured each run.
        """
        for run_id_, entry in carried.entries.items():
            state["runs"][run_id_] = {**entry, "carried": True}
        state.setdefault("cells", {}).update(carried.cells)
        state["carried"] = [
            *carried.chain,
            {
                "from": carried.directory.name,
                "repetitions": carried.repetitions,
                "runs": len(carried),
                "concurrency": carried.concurrency,
                "timeout": carried.timeout,
                "etalon_commit": carried.etalon_commit,
            },
        ]
        return state

    def absorb(self, carried: Carried, overrides: dict | None = None) -> None:
        """Copies a lower matrix's runs in, and writes them down as this one's.

        The trees are **copied**, not linked. `replay --rescore` rewrites
        `validation/<mode>.json` in place, and a link would make a re-scoring of this
        matrix silently rewrite the matrix it was carried from - which is the one that has
        to stay intact for the two to be compared at all.

        Over `load_or_create_state` and never `initial_state`: a run this matrix measured
        itself must survive the carry. That is the same rule as everywhere else here - a
        result already paid for is out of reach.
        """
        self.prepare()
        for run_id_, tree in carried.trees.items():
            if tree.is_dir():
                # Into this matrix's own layout, which may not be the one it came from.
                shutil.copytree(tree, self.location(run_id_), dirs_exist_ok=True)
        archived = {r.id: r for r in self.read_measures()}
        for row in carried.rows:
            archived.setdefault(row.id, row)
        self.write_measures(list(archived.values()))
        self.write_state(self.seed(self.load_or_create_state(overrides), carried))

    def to_do(self, state: dict, only: tuple[str, ...] = ()) -> list[tuple[str, dict]]:
        """The runs a launch should perform.

        A valid run is never relaunched, whatever its result. That is the whole
        protection: a resume has no power over anything that produced a result.

        A run of a cell the scenario no longer declares is not one to perform: there
        is nothing to launch it as. Launching it anyway spent a clone to reach
        `unknown cell`, recorded the run as empty, and left it resumable - so it came
        back on the next pass, and every pass after that.
        """
        declared = {cell.name for cell in self.scenario.cells}
        out = []
        for run_id_, meta in state["runs"].items():
            if only and meta["cell"] not in only:
                continue
            if meta["cell"] not in declared:
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
        """What the ledger holds, and whether this scenario's matrix is finished.

        Counted over the cells the scenario declares. A cell it no longer declares -
        a variant renamed between two launches - keeps its runs and keeps them
        rendered, but its unfinished ones say nothing about the matrix being planned,
        and counting them held `complete` at false with no launch able to lift it.
        """
        declared = {cell.name for cell in self.scenario.cells}
        counts: dict[str, int] = {}
        for meta in state["runs"].values():
            if meta["cell"] not in declared:
                continue
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
        return measures_in(self.directory)

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


def carried_note(state: dict) -> str:
    """The paragraph that keeps an extended matrix from reading as one launch.

    Runs are interleaved so that the durations of one matrix are comparable between its
    cells, under one provider load (see `runner`). A carried run was measured under
    another, so an extended matrix holds two sessions in its cost columns.

    Which is a legitimate thing to publish and not a legitimate thing to leave unsaid, so
    it is written where a reader who never saw the terminal will meet it - beside
    `table.retry_warning`, which exists for exactly this class of reservation.
    """
    chain = state.get("carried") or []
    if not chain:
        return ""
    last = chain[-1]
    total = len(state.get("runs", {}))
    commit = f", etalon at `{last['etalon_commit']}`" if last.get("etalon_commit") else ""
    return (
        f"\n:warning: **This matrix was extended.** {last['runs']} of its {total} runs were "
        f"measured in `{last['from']}` at {counted(last['repetitions'], 'repetition')} "
        f"(concurrency "
        f"{last['concurrency']}, timeout {last['timeout']}s{commit}) and carried here, so "
        f"the decision to measure more repetitions was taken after that matrix could be "
        f"read. Nothing was re-measured and `{last['from']}` is untouched, on disk and in "
        f"git, to compare against. Durations and costs compare within one launch, and this "
        f"matrix holds two.\n"
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
