"""Proving this harness reproduces the bench it replaces.

Parity is demonstrated in layers, and three of them are checkable **exactly**, at
zero tokens, from material already archived. The question "what tolerance" only
arose while we believed two samples had to be compared.

    layer 1  stripping              exact   from archived sessions
    layer 2  scoring                exact   from tag + diff.patch
    layer 3  aggregation + verdict  exact   from the published per-run rows
    layer 4  launching the agent    not comparable, it samples

Layer 3 needs nothing but a JSON file that already exists, so it could be
verified before half of this tool was written. Layer 2 needs a tree, so it takes
its reconstitution from the caller: the machinery belongs to `replay`, and the
cost of a clone per run is the caller's to declare.

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
from dataclasses import dataclass, field
from pathlib import Path

from .measure import Run
from .table import cost_measures, criterion_measure, gap_rows


@dataclass(frozen=True)
class Report:
    """What a layer found: what it observed, and what blocks.

    The two are kept apart because "58/60 sessions reproduce exactly" is a layer
    narrating, not a layer failing. Folding both into one list of strings made a
    passing parity exit non-zero, and left the caller sniffing line endings to
    tell a count from a defect.
    """

    observed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return not self.problems

    @property
    def lines(self) -> list[str]:
        return self.observed + self.problems


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


def published_by_id(path: str | Path) -> dict[str, dict]:
    """The published rows keyed by run identifier, which is how an archive is laid out.

    Rows without an identifier are dropped: they cannot be matched to anything on
    disk, so no layer that reads the archive can say a word about them.
    """
    rows = json.loads(Path(path).read_text())
    return {r["identifiant"]: r for r in rows if r.get("identifiant")}


def archived_runs(rows: dict[str, dict], archive: str | Path) -> list[Path]:
    """The published runs an archive can reconstitute: those holding a diff.patch.

    Exposed rather than inlined so a caller can say what a re-scoring is about to
    cost - one clone per run - without restating the rule that decides it.
    """
    archive = Path(archive)
    return [archive / ident for ident in sorted(rows) if (archive / ident / "diff.patch").is_file()]


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


def layer1(measures_path: str | Path, archive: str | Path) -> Report:
    """Recomputes tokens and turns from the archived sessions.

    A genuine test rather than a tautology: the bench counted `message_end` events
    in the live **stream**, and this reads the archived **session**, whose line
    types are `session`, `model_change`, `thinking_level_change` and `message`,
    with usage nested under `message.usage`. Two different paths to one number.

    `retries` is deliberately not compared. It derives from `auto_retry_start`, a
    stream event a session does not contain, so it is an archive artefact and is
    removed from the parity scope rather than silently treated as zero.

    Returns the differences, none when the layer holds.
    """
    from .measure import strip_session

    archive = Path(archive)
    rows = published_by_id(measures_path)
    published = {
        ident: (row.get("input"), row.get("output"), row.get("tours"))
        for ident, row in rows.items()
    }
    cell_of = {ident: row.get("cellule") for ident, row in rows.items()}

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
        return Report(problems=problems)

    agreeing = sum(1 for i, g in stripped.items() if g == published[i])
    return Report(
        observed=[f"{agreeing}/{checked} sessions reproduce their recorded figures exactly"],
        problems=problems,
    )


def layer2(
    measures_path: str | Path,
    archive: str | Path,
    reconstitute,
    validate,
) -> Report:
    """Re-scores archived runs by reconstituting their trees, and compares.

    `reconstitute(run_dir) -> Path` rebuilds the tree from the tag and the archived
    diff and returns the context naming it; `validate(context) -> dict` returns the
    metrics. Both are injected so this stays testable and so the caller owns the cost
    of cloning.

    Costs no tokens, which is what makes "fix a signature and re-score runs already
    paid for" true rather than aspirational.

    A metric no validator returns for **any** run is a statement about scope, not a
    defect: the bench scored some metrics with a judge, whose verdict costs tokens and
    is not in this archive to be reused. Those are named once and left out, the way
    layer 1 leaves out `retries`. A metric returned for some runs and missing for
    others is the opposite - the validator is unreliable - and blocks.
    """
    rows = published_by_id(measures_path)
    runs = archived_runs(rows, archive)
    if not runs:
        return Report(problems=[f"no archived diff found under {archive}"])

    problems: list[str] = []
    absent: dict[str, int] = {}
    agreeing = scored = compared = 0

    for directory in runs:
        try:
            got = validate(reconstitute(directory))
        except Exception as e:  # noqa: BLE001 - report, do not abort the layer
            problems.append(f"{directory.name}: could not re-score: {type(e).__name__}: {e}")
            continue
        scored += 1

        want = {
            BENCH_METRICS.get(k, k): v for k, v in (rows[directory.name].get("note") or {}).items()
        }
        gaps = []
        checked = 0
        for metric, expected in sorted(want.items()):
            if metric not in got:
                absent[metric] = absent.get(metric, 0) + 1
                continue
            checked += 1
            if got[metric] != expected:
                gaps.append(f"{directory.name}/{metric}: bench {expected!r}, here {got[metric]!r}")
        problems.extend(gaps)
        compared += checked
        # A run whose every published metric is out of scope agreed about nothing, and
        # counting it as agreeing would turn an empty comparison into a reassurance.
        if checked and not gaps:
            agreeing += 1

    if not compared:
        problems.append("no published metric came back: these validators compared nothing")

    observed = [f"{agreeing}/{scored} runs re-score to their published metrics exactly"]
    for metric, count in sorted(absent.items()):
        if count == scored:
            observed.append(f"{metric}: no validator here returns it, so it is out of scope")
        else:
            problems.append(
                f"{metric}: returned for {scored - count} of {scored} runs and missing on the rest"
            )
    return Report(observed=observed, problems=problems)


def layer4(experiment: str | Path, workdir: str | Path | None = None) -> Report:
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
      - **the model that answered is the one the scenario's pattern asked for**,
        which is the same check one axis over: `model` is a pattern, and a pattern
        that quietly resolved elsewhere measures a model nobody declared

    Returns the failures, none when the pass holds.
    """
    from .measure import VALID, thinking_levels

    experiment = Path(experiment)
    problems: list[str] = []

    state_path = experiment / "state.json"
    if not state_path.is_file():
        return Report(problems=[f"no state.json in {experiment}"])
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

    # Read from the archive rather than the work directory: `model_id` was written
    # beside the declared pattern when the run was archived, so this check survives a
    # purged workdir - which the thinking check below cannot.
    observed, model_problems = _models_answered(experiment, runs)
    problems.extend(model_problems)

    # The thinking check needs the sessions, which live in the work directory
    # because they are written while the agent runs and must stay out of the
    # measured repository.
    if workdir is None:
        problems.append("no workdir given: the thinking level check was skipped")
        return Report(observed=observed, problems=problems)

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
        observed.append(f"{checked} sessions checked for the declared thinking level")
    return Report(observed=observed, problems=problems)


def _models_answered(experiment: Path, runs: dict) -> tuple[list[str], list[str]]:
    """Checks that each run's model resolved to what its scenario asked for.

    `model` is a pattern, so equality would refuse every legitimate run: `gemma-4`
    answers as `gemma-4-31b`. What must hold is that the pattern is still in what
    answered - a fallback to the machine's `defaultModel` is exactly the case that
    fails it, and it is the case a matrix cannot afford to publish unnoticed.

    An archive written before `model_id` existed carries no such record. That is
    reported as unchecked rather than passed: a check that silently holds over
    material it never read is worse than no check.
    """
    from .agent import resolves_to

    observed: list[str] = []
    problems: list[str] = []
    checked = matched = unrecorded = 0

    for rid, meta in runs.items():
        path = experiment / "runs" / rid / "configuration.json"
        if not path.is_file():
            continue
        configuration = json.loads(path.read_text())
        declared, ran = configuration.get("model"), configuration.get("model_id")
        if not declared:
            continue
        if not ran:
            unrecorded += 1
            continue
        checked += 1
        if resolves_to(declared, ran):
            matched += 1
        else:
            problems.append(
                f"{meta['cell']}: declared model {declared!r}, the session recorded {ran!r}, "
                f"which that pattern does not name"
            )

    # `matched` of `checked`, not `checked` alone: a bare count next to a named failure
    # reads as a reassurance covering runs it does not cover. The same phrasing layer 1
    # uses for the sessions it reproduces.
    if checked:
        observed.append(f"{matched}/{checked} runs ran the model their pattern names")
    if unrecorded:
        observed.append(
            f"{unrecorded} runs record no model_id, so what answered could not be checked"
        )
    return observed, problems


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
