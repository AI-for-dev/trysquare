"""Rendering measures as a table, and gaps as verdicts.

Two tables, and they answer different questions. The score table says what each
cell did, test by test. The gap table says which differences survive resampling,
and it is the only one a sentence may rest on.

Both are computed from measures alone, which is what lets a rendering defect be
fixed without paying for a matrix again.

Pure: runs in, strings out.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable

from .measure import Run, kind, rate, valid_runs
from .verdict import DRAWS, ESTABLISHED, SEED, interval, judge, mean, plain, points, signed


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


def spend_measures() -> tuple[Measure, ...]:
    """The columns of the cost table: what a run cost, as a level rather than a gap.

    Tokens in, tokens out and duration, which is what a reader asks for when
    deciding whether a configuration is affordable at all. `turns` is left to the
    gap table: it is a shape of the conversation rather than a price, and it is
    read against the reference or not at all.

    The same caveat as `cost_measures` applies, and `retry_warning` states it: a
    retry replays the turn with the whole accumulated context, so all three inflate
    without the measured configuration having anything to do with it.
    """
    return (
        Measure("in", lambda r: r.usage.get("input", 0), statistics.median, plain),
        Measure("out", lambda r: r.usage.get("output", 0), statistics.median, plain),
        Measure("duration (s)", lambda r: r.duration, statistics.median, plain),
    )


def spend_rows(
    by_cell: dict[str, list[Run]],
    measures: tuple[Measure, ...],
    validity: tuple[str, ...] = (),
    order: tuple[str, ...] = (),
    draws: int = DRAWS,
    seed: int = SEED,
) -> list[dict]:
    """One row per cell, one median and one 95% interval per measure.

    Aggregated over the runs the *verdict* rests on - valid, and passing
    `[verdict].validity` - and not over every run that consumed tokens. A run that
    delivered nothing is cheap by construction, and averaging it into a price makes
    the configuration that fails most often look like the affordable one.

    Each interval is computed from that cell alone. There is no reference here and
    therefore no state: a level asserts no effect, and the comparison that does
    carry a verdict is the gap table.
    """
    names = [c for c in order if c in by_cell] + [c for c in by_cell if c not in order]
    rows = []
    for name in names:
        runs = valid_runs(by_cell[name], validity)
        cells = []
        for m in measures:
            values = [m.extract(r) for r in runs]
            if not values:
                cells.append("-")
                continue
            low, high = interval(values, m.stat, draws, seed)
            cells.append(f"{m.render(m.stat(values))} [{m.render(low)}, {m.render(high)}]")
        rows.append({"cell": name, "n": len(runs), "spend": cells})
    return rows


def spend_table(rows: list[dict], measures: tuple[Measure, ...], draws: int, seed: int) -> str:
    """The cost table, in markdown: cells in rows, median and interval in columns."""
    if not rows:
        return "No cell to cost."

    names = [m.name for m in measures]
    header = "| cell | n | " + " | ".join(names) + " |"
    sep = "| " + " | ".join(["---"] * (len(names) + 2)) + " |"
    lines = [
        f"| {row['cell']} | {row['n']} | " + " | ".join(row["spend"]) + " |" for row in rows
    ]

    return "\n".join(
        [
            "### Cost, median and 95% interval by resampling",
            "",
            f"{draws} draws, seed {seed}: the interval is reproducible.",
            "",
            header,
            sep,
            *lines,
            "",
            "Over the runs the verdict rests on: valid, and passing `[verdict].validity`.",
            "A level carries no verdict - two intervals that do not overlap are not a",
            "result, and the gap that would be one is in the table below.",
        ]
    )


def scored_metrics(
    runs: list[Run], declared: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Splits the declared metrics into those that count as a test, and the rest.

    A test is a boolean: it passed or it did not, so a cell of the score matrix is
    `x/n`. A number has a median rather than a count, and a list has neither, so
    neither belongs in that matrix - but both are *named* rather than dropped,
    because a declared metric that vanishes from every table reads as a metric that
    was never measured.

    Declaration order is kept: the scenario's `metrics` contract fixes the column
    order, the same way `[axes]` fixes the row order.
    """
    tests, other = [], []
    for name in dict.fromkeys(declared):
        sample = next((r.metrics[name] for r in runs if name in r.metrics), None)
        (tests if kind(sample) == "rate" else other).append(name)
    return tuple(tests), tuple(other)


def score_rows(
    by_cell: dict[str, list[Run]], tests: tuple[str, ...], order: tuple[str, ...] = ()
) -> list[dict]:
    """One row per cell, one `x/n` per test.

    Aggregated over the runs that produced a measurement, and **not** filtered by
    `[verdict].validity`: the validity metrics are columns of this very matrix, and
    a `delivered` column reading 10/10 by construction would hide the thing it is
    there to show.

    `n` is per test rather than per cell, because a validator may return a metric as
    `unjudged`: it drops out of that one denominator and leaves the others intact.
    On a published matrix - complete, every run valid, nothing unjudged - `n` is the
    repetition count in every cell, and a `n` below it is exactly the signal to read.
    """
    names = [c for c in order if c in by_cell] + [c for c in by_cell if c not in order]
    rows = []
    for name in names:
        runs = valid_runs(by_cell[name])
        scores = []
        for metric in tests:
            hits, total = rate(runs, metric)
            scores.append(f"{hits}/{total}" if total else "-")
        rows.append(
            {
                "cell": name,
                "n": len(runs),
                "invalid": sum(1 for r in by_cell[name] if not r.is_valid),
                "scores": scores,
            }
        )
    return rows


def score_table(rows: list[dict], tests: tuple[str, ...], other: tuple[str, ...] = ()) -> str:
    """The score matrix, in markdown: cells in rows, tests in columns."""
    if not rows or not tests:
        return "No test to score: no declared metric is a boolean."

    header = "| cell | " + " | ".join(tests) + " |"
    sep = "| " + " | ".join(["---"] * (len(tests) + 1)) + " |"
    lines = [f"| {row['cell']} | " + " | ".join(row["scores"]) + " |" for row in rows]

    notes = [
        "`x/n`: the test was true in `x` of the `n` runs that could judge it.",
        "A run that produced no measurement is in no denominator here.",
    ]
    if other:
        notes.append(
            "Declared but not scored here, having no `x/n` to show: "
            + ", ".join(f"`{m}`" for m in other)
            + " - a number or a diagnostic, readable per run in `measures.json`."
        )
    dropped = [f"{row['cell']} ({row['invalid']})" for row in rows if row["invalid"]]
    if dropped:
        notes.append("Runs left out as invalid: " + ", ".join(dropped) + ".")

    return "\n".join(["### Scores, cell by test", "", header, sep, *lines, "", *notes])
