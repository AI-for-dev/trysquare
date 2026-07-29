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
