# SPDX-License-Identifier: BSD-3-Clause
"""The executable half of the publication standard.

A gap reaches a page only if the harness certifies it. That is deliberate: the
author cannot work around it, and six published conclusions collapsed on rerun
while every one of them looked solid when it was written.

One mechanism covers both rates and medians: replay the draw. Resample the runs
with replacement, recompute the gap to the reference cell on each draw, and the
gap is publishable if its 95% interval excludes zero - on samples large enough for
any test to reach 0.05, which `floor` decides. Nothing else to read, no fragile
statistic.

This judges a **gap**. An isolated measurement - the glow costs 23% of a frame
budget - asserts no effect: it is published with its dispersion and no verdict.

The seed is fixed. A verdict that is not reproducible would make the harness
itself a source of irreproducibility.

Pure: lists of numbers in, intervals out.
"""

from __future__ import annotations

import bisect
import random
import statistics
from math import comb

DRAWS = 10_000
SEED = 20260729

ESTABLISHED = "established"
INCONCLUSIVE = "inconclusive"


def mean(values: list[float]) -> float:
    """The statistic for a rate: booleans arrive as 0/1."""
    return sum(values) / len(values)


def _draws(one_draw, draws: int, seed: int) -> list[float]:
    """Replays `one_draw` with a fresh seeded RNG and returns the draws, sorted.

    The RNG is created fresh here rather than shared across calls, so each
    interval depends only on its own inputs and the seed. Two runs of the tool
    over the same measures give the same bounds, and so does a rerun months
    later.
    """
    rng = random.Random(seed)
    return sorted(one_draw(rng) for _ in range(draws))


def _bounds(values: list[float]) -> tuple[float, float]:
    """The 95% bounds of sorted draws."""
    return (
        values[int(0.025 * len(values))],
        values[min(len(values) - 1, int(0.975 * len(values)))],
    )


def _p(values: list[float]) -> float:
    """The two-sided p-value of sorted draws: twice the share on the rarer side of zero.

    A draw of exactly zero counts on both sides, so a gap that resamples to zero is
    never read as significant. That is the same choice as `judge`, where an interval
    touching zero is inconclusive, and it is what keeps the two readings in step:
    established means p <= 0.05, inconclusive means p >= 0.05.
    """
    n = len(values)
    at_most_zero = bisect.bisect_right(values, 0) / n
    at_least_zero = (n - bisect.bisect_left(values, 0)) / n
    return min(1.0, 2 * min(at_most_zero, at_least_zero))


def floor(n1: int, n2: int) -> float:
    """The smallest two-sided p that `n1` runs against `n2` can support.

    Resampling one run per cell repeats the observed gap on every draw, so the draws
    alone read p=0 from two numbers. No test can say that much: of the C(n1 + n2, n1)
    ways to split the runs between the cells, the observed split is at best the most
    extreme on its side, which is what a permutation test would report. It takes four
    runs against four to reach 0.05.
    """
    return min(1.0, 2 / comb(n1 + n2, n1))


def gap_draws(
    reference: list[float],
    cell: list[float],
    stat=statistics.median,
    draws: int = DRAWS,
    seed: int = SEED,
) -> list[float]:
    """The resampled values of `stat(cell) - stat(reference)`, sorted.

    Draw order matters for byte-identical reproduction: the reference sample is
    drawn before the cell sample on every iteration.
    """
    if not reference or not cell:
        raise ValueError("an empty sample has no interval")

    def one_draw(rng) -> float:
        a = rng.choices(reference, k=len(reference))
        b = rng.choices(cell, k=len(cell))
        return stat(b) - stat(a)

    return _draws(one_draw, draws, seed)


def gap_interval(
    reference: list[float],
    cell: list[float],
    stat=statistics.median,
    draws: int = DRAWS,
    seed: int = SEED,
) -> tuple[float, float]:
    """95% interval of `stat(cell) - stat(reference)`."""
    return _bounds(gap_draws(reference, cell, stat, draws, seed))


def interval(
    values: list[float],
    stat=statistics.median,
    draws: int = DRAWS,
    seed: int = SEED,
) -> tuple[float, float]:
    """95% interval of `stat(values)` itself, for a measurement that is not a gap.

    The same mechanism, replaying the draw, so a dispersion is read exactly as an
    interval around a gap is. What it never gets is a state: an isolated measurement
    asserts no effect, so `established` would be a category error. A cost is
    published with its dispersion and no verdict, and the comparison that *does*
    carry one lives in the gap table.
    """
    if not values:
        raise ValueError("an empty sample has no interval")

    return _bounds(_draws(lambda rng: stat(rng.choices(values, k=len(values))), draws, seed))


def judge(
    reference: list[float],
    cell: list[float],
    stat=statistics.median,
    draws: int = DRAWS,
    seed: int = SEED,
) -> dict:
    """A gap, its interval, its p-value, and one of exactly two states.

    Two states only. A third would invite a reading where a gap is "almost"
    something, and almost is how six conclusions got published. The p-value comes
    from the same draws as the interval and decides nothing. It exists so a whole
    table can be adjusted for the number of gaps it tests, which an interval cannot.
    """
    values = gap_draws(reference, cell, stat, draws, seed)
    low, high = _bounds(values)
    gap = stat(cell) - stat(reference)
    p = max(_p(values), floor(len(reference), len(cell)))
    return {
        "gap": gap,
        "low": low,
        "high": high,
        "p": p,
        # Excluding zero is the test, on samples large enough to pass one at all.
        # Written this way rather than as `low > 0 or high < 0` so that an interval
        # touching zero exactly counts as inconclusive.
        "state": INCONCLUSIVE if low <= 0 <= high or p > 0.05 else ESTABLISHED,
    }


def holm(p: list[float]) -> list[float]:
    """Holm's step-down adjustment of `p`, in the order given.

    Every gap of a table is its own test at 95%, so fifteen gaps with no real effect
    still yield 0.75 stars on average. Holm bounds the chance of even one false star
    over the whole table, and it assumes nothing about how the gaps are correlated.
    That matters here, since every column of a row resamples the same runs.
    """
    order = sorted(range(len(p)), key=lambda i: p[i])
    adjusted = [0.0] * len(p)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p) - rank) * p[i]))
        adjusted[i] = running
    return adjusted


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


def plain(x: float) -> str:
    """An unsigned number, spaced like `signed` and as unwilling to round to zero.

    A cost is a level rather than a difference, and a leading `+` on one would read
    as an increase over something the reader would then go looking for.
    """
    if x != 0 and abs(x) < 1:
        return f"{x:.1f}"
    return f"{x:,.0f}".replace(",", " ")


def probability(p: float) -> str:
    """A p-value, never rendered as zero: resampling cannot prove a gap certain."""
    return "p<0.001" if p < 0.001 else f"p={p:.3f}"


def points(x: float) -> str:
    """A rate gap, in points. Rates live in 0..1 and read in 0..100."""
    return f"{x * 100:+.0f} pts"
