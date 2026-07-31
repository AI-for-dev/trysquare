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
      runs/<id>/
        context.json  configuration.json  diff.patch
        session/*.jsonl          the agent's per-message record, one file per attempt
        validation/<mode>.json   validation/<mode>.stderr

The session files are the agent's own trace, copied here so it outlives the work
directory - which is disposable by design and which the OS may purge. The raw event
stream is *not* copied: it is almost entirely streaming deltas and says nothing the
per-message record does not, at five hundred times the size.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from .measure import EMPTY, VALIDATOR_FAILED, Run

STATE = "state.json"
MEASURES = "measures.json"
SYNTHESIS = "synthesis.md"
SESSION = "session"

MISSING = "missing"

# Only these two states may be relaunched by a resume, because only these two
# produced no result at all. A validator failure is not among them: it needs
# re-scoring, which costs no tokens, and re-measuring it would let a resume
# change a run that had already produced something.
RESUMABLE = (MISSING, EMPTY)


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


def run_id(scenario_name: str, cell: str, repetition: int) -> str:
    """A short opaque id, stable for a given (scenario, cell, repetition).

    Opaque so a form can be filled without knowing which cell is being scored, and
    stable so a resume can tell an absent run from one already done. The mapping
    back to the cell lives in `state.json`, deliberately not in the form.
    """
    key = f"{scenario_name}/{cell}/{repetition}".encode()
    return hashlib.blake2s(key, digest_size=4).hexdigest()


class Output:
    """The directory for one experiment, and everything written into it."""

    def __init__(self, root: Path, scenario, repetitions: int | None = None):
        self.scenario = scenario
        self.repetitions = repetitions or scenario.protocol["repetitions"]
        self.directory = Path(root) / experiment_name(scenario, self.repetitions)
        self.runs_dir = self.directory / "runs"

    # --- layout ---------------------------------------------------------

    def prepare(self) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        return self.directory

    def run_dir(self, run_id_: str) -> Path:
        d = self.runs_dir / run_id_
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

    def read_state(self) -> dict:
        path = self.directory / STATE
        if not path.is_file():
            return {}
        return json.loads(path.read_text())

    def write_state(self, state: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / STATE).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")

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
            "overrides": overrides or {},
            "complete": False,
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
        # A ledger may predate a scenario edit that added cells.
        for run_id_, meta in self.plan().items():
            state["runs"].setdefault(run_id_, {**meta, "state": MISSING, "attempts": 0})
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
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
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
        target = self.runs_dir / run_id_ / SESSION
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
        directory = self.runs_dir / run_id_ / SESSION
        return sorted(directory.glob("*.jsonl")) if directory.is_dir() else []

    def write_synthesis(self, text: str, suffix: str = "") -> Path:
        name = f"synthesis{suffix}.md" if suffix else SYNTHESIS
        path = self.directory / name
        path.write_text(text)
        return path

    def write_configuration(self, run_id_: str, configuration: dict) -> Path:
        path = self.run_dir(run_id_) / "configuration.json"
        path.write_text(json.dumps(configuration, indent=2, ensure_ascii=False) + "\n")
        return path

    def write_validation(self, run_id_: str, mode: str, payload, stderr: str = "") -> None:
        d = self.run_dir(run_id_) / "validation"
        d.mkdir(parents=True, exist_ok=True)
        if payload is not None:
            (d / f"{mode}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
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
