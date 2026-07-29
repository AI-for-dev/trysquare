"""The executable half of the publication standard.

A gap reaches a page only if the harness certifies it. That is deliberate: the
author cannot work around it, and six published conclusions collapsed on rerun
while every one of them looked solid when it was written.

One mechanism covers both rates and medians: replay the draw. Resample the runs
with replacement, recompute the gap to the reference cell on each draw, and the
gap is publishable if its 95% interval excludes zero. Nothing else to read, no
fragile statistic.

This judges a **gap**. An isolated measurement - the glow costs 23% of a frame
budget - asserts no effect: it is published with its dispersion and no verdict.

The seed is fixed. A verdict that is not reproducible would make the harness
itself a source of irreproducibility.

Pure: lists of numbers in, intervals out.
"""

from __future__ import annotations

import random
import statistics

DRAWS = 10_000
SEED = 20260729

ESTABLISHED = "established"
INCONCLUSIVE = "inconclusive"


def mean(values: list[float]) -> float:
    """The statistic for a rate: booleans arrive as 0/1."""
    return sum(values) / len(values)


def gap_interval(
    reference: list[float],
    cell: list[float],
    stat=statistics.median,
    draws: int = DRAWS,
    seed: int = SEED,
) -> tuple[float, float]:
    """95% interval of `stat(cell) - stat(reference)`.

    The RNG is created fresh here rather than shared across calls, so each
    interval depends only on its own inputs and the seed. Two runs of the tool
    over the same measures give the same bounds, and so does a rerun months
    later.

    Draw order matters for byte-identical reproduction: the reference sample is
    drawn before the cell sample on every iteration.
    """
    if not reference or not cell:
        raise ValueError("an empty sample has no interval")

    rng = random.Random(seed)
    gaps = []
    for _ in range(draws):
        a = rng.choices(reference, k=len(reference))
        b = rng.choices(cell, k=len(cell))
        gaps.append(stat(b) - stat(a))
    gaps.sort()
    return (
        gaps[int(0.025 * len(gaps))],
        gaps[min(len(gaps) - 1, int(0.975 * len(gaps)))],
    )


def judge(
    reference: list[float],
    cell: list[float],
    stat=statistics.median,
    draws: int = DRAWS,
    seed: int = SEED,
) -> dict:
    """A gap, its interval, and one of exactly two states.

    Two states only. A third would invite a reading where a gap is "almost"
    something, and almost is how six conclusions got published.
    """
    low, high = gap_interval(reference, cell, stat, draws, seed)
    gap = stat(cell) - stat(reference)
    return {
        "gap": gap,
        "low": low,
        "high": high,
        # Excluding zero is the whole test. Written this way rather than as
        # `low > 0 or high < 0` so that an interval touching zero exactly counts
        # as inconclusive.
        "state": INCONCLUSIVE if low <= 0 <= high else ESTABLISHED,
    }


def signed(x: float) -> str:
    """A signed number with enough precision never to lie.

    Rounding to integers displayed `[-4, -0]` for a bound worth -0.5: the reader
    believes they see an interval containing zero, so an inconclusive result,
    when the computation says the opposite. A non-zero bound must never render as
    zero.
    """
    if x != 0 and abs(x) < 1:
        return f"{x:+.1f}"
    return f"{x:+,.0f}".replace(",", " ")


def points(x: float) -> str:
    """A rate gap, in points. Rates live in 0..1 and read in 0..100."""
    return f"{x * 100:+.0f} pts"
