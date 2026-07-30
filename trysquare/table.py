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

from .measure import Run, kind, median, rate, valid_runs
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
        # Say *why* there is nothing left, because "no valid run" right after a log
        # of successful runs is baffling. The usual cause is a validity condition
        # that does not match the task: `delivered` means "modified a file", which is
        # the wrong requirement for a task whose instruction is to write no code.
        present = by_cell.get(reference, [])
        eliminated = {
            metric: sum(1 for r in present if r.is_valid and not r.metrics.get(metric))
            for metric in validity
        }
        culprits = ", ".join(f"{m} ({n} of {len(present)})" for m, n in eliminated.items() if n)
        raise ValueError(
            f"reference cell {reference!r} has no run left to compare against: no gap "
            f"is judgeable.\n"
            f"  {len(present)} runs, {sum(1 for r in present if r.is_valid)} of them valid"
            + (
                f"\n  eliminated by [verdict].validity: {culprits}"
                f"\n  A validity condition must match the task: `delivered` requires a"
                f" file to have changed, which is wrong for a task that asks for prose."
                if culprits
                else ""
            )
        )

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


COST_MEASURES = ("in", "out", "turns", "duration")


def retry_warning(by_cell: dict[str, list[Run]]) -> str:
    """A note when the cost columns must not be read.

    When the provider cuts the stream, the agent replays the turn with the whole
    accumulated context, so tokens, turns and duration inflate without the measured
    configuration having anything to do with it. Measured on the previous bench: zero
    retries gave 4 turns and 15.9k input tokens, thirteen retries gave 24 and 79.4k.

    Publishing those columns without looking at retries means publishing our own load
    on the provider, so the table says so rather than relying on the reader to
    remember.
    """
    total = sum(r.retries for runs in by_cell.values() for r in runs)
    if not total:
        return ""
    affected = sorted(
        name for name, runs in by_cell.items() if any(r.retries for r in runs)
    )
    return (
        f"\n:warning: **The cost columns ({', '.join(COST_MEASURES)}) must not be read "
        f"here.** {total} retries across the matrix, in {', '.join(affected)}. A retry "
        f"replays the turn with the whole accumulated context, so these columns "
        f"reflect our own load on the provider rather than the configuration - "
        f"including any of them marked established.\n"
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
