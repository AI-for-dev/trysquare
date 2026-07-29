"""Rendering measures as a table, and gaps as verdicts.

Two tables, and they answer different questions. The cell table says what each
configuration did. The gap table says which differences survive resampling, and
it is the only one a sentence may rest on.

Both are computed from measures alone, which is what lets a rendering defect be
fixed without paying for a matrix again.

Pure: runs in, strings out.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable

from .measure import Run, kind, median, rate, series, valid_runs
from .verdict import ESTABLISHED, judge, mean, points, signed


@dataclass(frozen=True)
class Measure:
    """One column of the gap table."""

    name: str
    extract: Callable[[Run], float]
    stat: Callable[[list[float]], float]
    render: Callable[[float], str]


def cost_measures() -> tuple[Measure, ...]:
    """The columns that describe what a run cost.

    Read these only alongside the retry count. When the provider cuts the
    stream, `pi` replays the turn with the whole accumulated context, so all four
    inflate without the measured configuration having anything to do with it.
    """
    return (
        Measure("in", lambda r: r.usage.get("input", 0), statistics.median, signed),
        Measure("out", lambda r: r.usage.get("output", 0), statistics.median, signed),
        Measure("turns", lambda r: r.usage.get("turns", 0), statistics.median, signed),
        Measure("duration", lambda r: r.duration, statistics.median, signed),
    )


def criterion_measure(criterion: str, sample: Run | None = None) -> Measure:
    """The column that carries the scenario's criterion.

    A boolean criterion is a rate and renders in points; a numeric one is a
    median and renders as a plain number. The type decides, because a scenario
    has no place declaring what Python already knows.
    """
    numeric = sample is not None and kind(sample.metrics.get(criterion)) == "median"
    if numeric:
        return Measure(
            criterion,
            lambda r: r.metrics.get(criterion, 0),
            statistics.median,
            signed,
        )
    return Measure(
        criterion,
        lambda r: 1.0 if r.metrics.get(criterion) else 0.0,
        mean,
        points,
    )


def gap_rows(
    by_cell: dict[str, list[Run]],
    reference: str,
    measures: tuple[Measure, ...],
    validity: tuple[str, ...] = (),
    draws: int | None = None,
    seed: int | None = None,
) -> list[dict]:
    """One row per cell, one verdict per measure, against the reference cell.

    Only valid runs enter a verdict: a run that delivered nothing, or whose tests
    fail, measures neither its cost nor its discipline.
    """
    kwargs = {}
    if draws is not None:
        kwargs["draws"] = draws
    if seed is not None:
        kwargs["seed"] = seed

    valid = {name: valid_runs(runs, validity) for name, runs in by_cell.items()}
    base = valid.get(reference)
    if not base:
        raise ValueError(f"reference cell {reference!r} has no valid run: no gap is judgeable")

    rows = []
    for name, runs in valid.items():
        if name == reference or not runs:
            continue
        cells = []
        for m in measures:
            a = [m.extract(r) for r in base]
            b = [m.extract(r) for r in runs]
            v = judge(a, b, m.stat, **kwargs)
            cells.append(
                {
                    "measure": m.name,
                    "rendered": m.render(v["gap"]),
                    "interval": f"[{m.render(v['low'])}, {m.render(v['high'])}]",
                    "state": v["state"],
                    **v,
                }
            )
        rows.append({"cell": name, "measures": cells})
    return rows


def gap_table(rows: list[dict], reference: str, draws: int, seed: int) -> str:
    """The gap table, in markdown."""
    if not rows:
        return "No cell to compare against the reference."

    names = [c["measure"] for c in rows[0]["measures"]]
    header = "| cell | " + " | ".join(names) + " |"
    sep = "| " + " | ".join(["---"] * (len(names) + 1)) + " |"

    lines = []
    established = []
    for row in rows:
        cells = []
        for c in row["measures"]:
            mark = "*" if c["state"] == ESTABLISHED else "o"
            cells.append(f"{c['rendered']} {mark}")
            if c["state"] == ESTABLISHED:
                established.append(
                    f"- `{row['cell']}`: **{c['measure']} {c['rendered']}**, "
                    f"interval {c['interval']}"
                )
        lines.append(f"| {row['cell']} | " + " | ".join(cells) + " |")

    return "\n".join(
        [
            f"### Gap to `{reference}`, 95% interval by resampling",
            "",
            f"{draws} draws, seed {seed}: the verdict is reproducible.",
            "",
            header,
            sep,
            *lines,
            "",
            "`*` established, the interval excludes zero - `o` inconclusive.",
            "",
            "**No sentence may rest on an `o`.** The table shows them anyway:",
            "hiding a measurement would be another dishonesty, and the dispersion",
            "is precisely what this is for.",
            "",
            "#### What is publishable",
            "",
            *(established or ["- nothing: no gap survives resampling."]),
        ]
    )


def cell_rows(
    by_cell: dict[str, list[Run]], criterion: str, validity: tuple[str, ...] = ()
) -> list[dict]:
    """One row per cell: what it did, and how many runs could say so."""
    rows = []
    for name, runs in by_cell.items():
        ok = valid_runs(runs, validity)
        hits, total = rate(ok, criterion)
        retries = [r for r in ok if r.retries]
        rows.append(
            {
                "cell": name,
                "n": len(ok),
                "invalid": sum(1 for r in runs if not r.is_valid),
                "criterion": f"{hits}/{total}" if total else "-",
                "input": median(ok, "input"),
                "output": median(ok, "output"),
                "turns": median(ok, "turns"),
                "clean_retries": f"{len(ok) - len(retries)}/{len(ok)}",
            }
        )
    return rows
