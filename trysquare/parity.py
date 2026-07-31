"""Proving this harness reproduces the bench it replaces.

Parity is demonstrated in layers, and three of them are checkable **exactly**, at
zero tokens, from material already archived. The question "what tolerance" only
arose while we believed two samples had to be compared.

    layer 1  stripping              exact   from archived sessions
    layer 2  scoring                exact   from tag + diff.patch
    layer 3  aggregation + verdict  exact   from the published per-run rows
    layer 4  launching the agent    not comparable, it samples

This module owns layer 3, which needs nothing but a JSON file that already
exists. It can therefore be verified before half of this tool is written.

**Neither tool is the reference.** Two computations are compared over the same
archived material, and the material arbitrates. A gap on an exact layer blocks,
and has exactly three admitted outcomes:

    1. this harness is wrong    -> fix it
    2. the bench was wrong      -> fix the *published* number and record the
                                   defect; the bench has twenty catalogued, so
                                   presuming it correct would be a losing bet
    3. archive artefact         -> documented, removed from the parity scope
                                   (retries, which are a stream event a session
                                   does not contain)
"""

from __future__ import annotations

import json
from pathlib import Path

from .measure import Run
from .table import cost_measures, criterion_measure, gap_rows

# The bench named things in French, and its published rows are the ground truth
# for layer 3. Mapping rather than renaming: the archive is evidence and evidence
# is not edited.
BENCH_METRICS = {
    "debordement": "overflow",
    "issues": "issues",
    "livre": "delivered",
    "perimetre": "in_scope",
    "tests": "tests",
    "apiStable": "api_stable",
    "touches": "touched",
}

# The bench's validity condition, read from its own code rather than assumed:
# `delivered AND tests`. A run whose tests fail did not deliver the ticket, and
# an empty diff passes the tests by construction.
BENCH_VALIDITY = ("delivered", "tests")


def read_bench_measures(path: str | Path) -> dict[str, list[Run]]:
    """Loads the bench's per-run rows, grouped by cell.

    The published JSON is a list of per-run entries, not aggregates, which is the
    single fact that makes exact parity possible.
    """
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a list of per-run measures")

    by_cell: dict[str, list[Run]] = {}
    for i, row in enumerate(rows):
        note = row.get("note") or {}
        run = Run(
            id=row.get("identifiant") or f"row-{i}",
            cell=row["cellule"],
            repetition=i,
            usage={
                "input": row.get("input", 0),
                "output": row.get("output", 0),
                "cacheRead": row.get("cacheRead", 0),
                "turns": row.get("tours", 0),
                "retries": row.get("reprises", 0),
                "cost": row.get("cout", 0.0),
            },
            duration=row.get("duree", 0),
            metrics={BENCH_METRICS.get(k, k): v for k, v in note.items()},
            attempts=row.get("essais", 1),
        )
        by_cell.setdefault(run.cell, []).append(run)
    return by_cell


def layer3(
    measures_path: str | Path,
    reference: str = "base",
    criterion: str = "overflow",
) -> list[dict]:
    """Recomputes the bench's gap table from its own published rows.

    Returns the rows, for a caller to compare against what the bench published.
    Identical inputs and an identical method must give identical output; anything
    else is one of the three outcomes above.
    """
    by_cell = read_bench_measures(measures_path)
    sample = next(iter(by_cell[reference]), None)
    measures = cost_measures() + (criterion_measure(criterion, sample),)
    return gap_rows(by_cell, reference, measures, validity=BENCH_VALIDITY)


def layer1(measures_path: str | Path, archive: str | Path) -> list[str]:
    """Recomputes tokens and turns from the archived sessions.

    A genuine test rather than a tautology: the bench counted `message_end` events
    in the live **stream**, and this reads the archived **session**, whose line
    types are `session`, `model_change`, `thinking_level_change` and `message`,
    with usage nested under `message.usage`. Two different paths to one number.

    `retries` is deliberately not compared. It derives from `auto_retry_start`, a
    stream event a session does not contain, so it is an archive artefact and is
    removed from the parity scope rather than silently treated as zero.

    Returns the differences, empty when the layer holds.
    """
    from .measure import strip_session

    archive = Path(archive)
    rows = json.loads(Path(measures_path).read_text())
    published = {
        r["identifiant"]: (r.get("input"), r.get("output"), r.get("tours"))
        for r in rows
        if r.get("identifiant")
    }
    cell_of = {r["identifiant"]: r.get("cellule") for r in rows if r.get("identifiant")}

    problems = []
    checked = 0
    stripped: dict[str, tuple] = {}

    for ident in published:
        directory = archive / ident
        sessions = sorted(directory.glob("*.jsonl")) if directory.is_dir() else []
        if not sessions:
            continue
        text = "\n".join(p.read_text(errors="replace") for p in sessions)
        got = strip_session(text)
        stripped[ident] = (got["input"], got["output"], got["turns"])
        checked += 1

    for ident, got in stripped.items():
        want = published[ident]
        if got == want:
            continue

        # Before calling this a stripping difference, check whether these exact
        # figures belong to another run. An exact match elsewhere means the numbers
        # are right and the *labels* are crossed, which is an archive artefact
        # rather than a disagreement about how to count.
        owners = [other for other, value in published.items() if value == got and other != ident]
        if owners:
            same_cell = all(cell_of.get(o) == cell_of.get(ident) for o in owners)
            problems.append(
                f"LABEL: {ident} holds the figures published for {', '.join(owners)}"
                + (
                    " - same cell, so no aggregate is affected"
                    if same_cell
                    else " - DIFFERENT CELL, aggregates are affected"
                )
            )
            continue

        for i, (mine, theirs) in enumerate(
            (("input", "input"), ("output", "output"), ("turns", "tours"))
        ):
            if got[i] != want[i]:
                problems.append(f"{ident}/{mine}: bench {want[i]}, from the session {got[i]}")

    if not checked:
        problems.append(f"no archived session found under {archive}")
    else:
        agreeing = sum(1 for i, g in stripped.items() if g == published[i])
        problems.insert(
            0, f"{agreeing}/{checked} sessions reproduce their recorded figures exactly"
        )
    return problems


def layer2(
    measures_path: str | Path,
    archive: str | Path,
    reconstitute,
    validate,
) -> list[str]:
    """Re-scores archived runs by reconstituting their trees, and compares.

    `reconstitute(run_dir) -> Path` rebuilds a tree from the tag and the archived
    diff; `validate(tree) -> dict` returns the metrics. Both are injected so this
    stays testable and so the caller owns the cost of cloning.

    Costs no tokens, which is what makes "fix a signature and re-score runs already
    paid for" true rather than aspirational.
    """
    archive = Path(archive)
    rows = {
        r["identifiant"]: r
        for r in json.loads(Path(measures_path).read_text())
        if r.get("identifiant")
    }
    problems = []

    for ident, row in rows.items():
        directory = archive / ident
        if not (directory / "diff.patch").is_file():
            continue
        try:
            tree = reconstitute(directory)
            got = validate(tree)
        except Exception as e:  # noqa: BLE001 - report, do not abort the layer
            problems.append(f"{ident}: could not re-score: {type(e).__name__}: {e}")
            continue

        want = {BENCH_METRICS.get(k, k): v for k, v in (row.get("note") or {}).items()}
        for metric, expected in want.items():
            if metric not in got:
                problems.append(f"{ident}/{metric}: not returned by the validator")
            elif got[metric] != expected:
                problems.append(f"{ident}/{metric}: bench {expected!r}, here {got[metric]!r}")
    return problems


def layer4(experiment: str | Path, workdir: str | Path | None = None) -> list[str]:
    """The smoke pass: mechanical criteria that do not depend on the sample.

    Layer 4 launches the agent, so its rates are a different sample and can never
    demonstrate parity. What it *can* demonstrate is that the harness is wired, and
    every criterion here is checkable without any statistical claim:

      - every run valid, meaning every run consumed tokens
      - the outputs a complete matrix owes are present
      - each run's directory holds its context, configuration, diff and validation
      - **the thinking level each session recorded equals the level its cell
        declared** - the check that makes the defect which rendered the thinking
        cell identical to the baseline unable to recur

    Returns the failures, empty when the pass holds.
    """
    from .measure import VALID, thinking_levels

    experiment = Path(experiment)
    problems: list[str] = []

    state_path = experiment / "state.json"
    if not state_path.is_file():
        return [f"no state.json in {experiment}"]
    state = json.loads(state_path.read_text())
    runs = state.get("runs", {})

    invalid = {rid: m for rid, m in runs.items() if m.get("state") != VALID}
    if invalid:
        problems.append(
            f"{len(invalid)} of {len(runs)} runs are not valid: "
            + ", ".join(f"{m['cell']} ({m['state']})" for m in list(invalid.values())[:4])
        )
    if not state.get("complete"):
        problems.append("the matrix is not complete")

    for name in ("measures.json", "synthesis.md"):
        if not (experiment / name).is_file():
            problems.append(f"missing output: {name}")

    for rid, meta in runs.items():
        directory = experiment / "runs" / rid
        for name in ("configuration.json", "diff.patch"):
            if not (directory / name).is_file():
                problems.append(f"{meta['cell']}: missing {name}")
        if not (directory / "validation").is_dir():
            problems.append(f"{meta['cell']}: no validation output")

    # The thinking check needs the sessions, which live in the work directory
    # because they are written while the agent runs and must stay out of the
    # measured repository.
    if workdir is None:
        problems.append("no workdir given: the thinking level check was skipped")
        return problems

    work = Path(workdir) / experiment.name
    checked = 0
    for rid, meta in runs.items():
        declared = _declared_thinking(meta["cell"], state.get("thinking"))
        session_dir = work / rid / "session"
        sessions = sorted(session_dir.glob("*.jsonl")) if session_dir.is_dir() else []
        if not sessions:
            problems.append(f"{meta['cell']}: no session to read the thinking level from")
            continue
        text = "\n".join(p.read_text(errors="replace") for p in sessions)
        levels = thinking_levels(text)
        got = levels[-1] if levels else None
        checked += 1
        if got != declared:
            problems.append(
                f"{meta['cell']}: declared thinking {declared!r}, session recorded {got!r}"
            )
    if checked:
        problems.insert(0, f"{checked} sessions checked for the declared thinking level")
    return problems


def _declared_thinking(cell: str, default: str | None) -> str | None:
    """The level a cell asked for, read from its name when it names one.

    A cell name is built from its axis values, so a grid over a `thinking` axis
    carries the level in the name. Falls back to the scenario's declared level.
    """
    for part in reversed(cell.split(" / ")):
        if part in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
            return part
    return default


def compare(rows: list[dict], expected: dict[str, dict[str, str]]) -> list[str]:
    """Checks recomputed rows against the values the bench published.

    Compares the *rendered* strings rather than raw floats: what was published is
    what a reader saw, and a parity that agrees on invisible digits while
    disagreeing on the printed ones would be worthless. Returns the differences,
    empty when parity holds.
    """
    problems = []
    by_name = {r["cell"]: r for r in rows}

    for cell, columns in expected.items():
        if cell not in by_name:
            problems.append(f"{cell}: absent from the recomputed table")
            continue
        got = {c["measure"]: c for c in by_name[cell]["measures"]}
        for measure, want in columns.items():
            if measure not in got:
                problems.append(f"{cell}/{measure}: not computed")
                continue
            c = got[measure]
            mark = "*" if c["state"] == "established" else "o"
            mine = f"{c['rendered']} {mark}"
            if mine != want:
                problems.append(f"{cell}/{measure}: bench {want!r}, here {mine!r}")

    extra = set(by_name) - set(expected)
    if extra:
        problems.append(f"cells not in the published table: {', '.join(sorted(extra))}")
    return problems
