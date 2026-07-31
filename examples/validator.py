#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""A validator, whole, written on `trysquare.assay`.

This is the worked example, and it is also a test fixture: `tests/test_example.py` runs it
against `tests/fixtures/tiny`, so it cannot rot the way a snippet in a document does.

Everything below is generic except two lines: `SCOPE`, which is what this task put in
reach, and the metric names. That ratio is the point.
"""

from trysquare.assay import Assay, CannotJudge, Metric, validator

# What the ticket asked the agent to touch. A property of the **task**, so it is the same
# for every cell - reading it from the cell would score each configuration with its own
# ruler.
SCOPE = frozenset({"counter.py"})


@validator
def evaluate(run: Assay) -> dict:
    # What the agent wrote, as against what running the task left behind. The scenario
    # declares its by-products and subtracting them here is the whole fix for a real
    # defect: an agent that ran the declared suite to check itself left `__pycache__`,
    # bytecode scored as work, and `in_scope` was false in every run of every cell.
    work = run.touched - run.artefacts
    outside = work - SCOPE

    return {
        # A bare value: no reason to give, so none is given.
        "delivered": bool(work),
        # A value carrying its reason, so nobody has to write an `if` to attach one.
        "in_scope": Metric(not outside, f"also touched {', '.join(sorted(outside))}"),
        # An attribute costs nothing; a parenthesis costs something. This one runs the
        # suite the scenario declared, and carries the runner's own summary as its reason.
        "tests": or_unjudged(run.tests),
        # A set, serialised sorted - diagnostic only, since there is no median of a list.
        "touched": run.touched,
        "documented": or_unjudged(lambda: documented(run)),
    }


def or_unjudged(read) -> Metric:
    """A metric this run cannot answer, said as such rather than losing the whole run.

    This is the pattern worth copying. Without it, one unanswerable metric refuses the run
    and takes **every other metric with it** - including the one carrying the verdict.

    Both metrics above need it, for different reasons. An agent that gutted the test file
    leaves a suite nobody can collect, which is a fact about `tests` and says nothing about
    `in_scope`. And a re-scoring has no prose to read, because the response lived in the
    work directory - so `documented` degrades honestly on a replay while the rest still
    scores.

    Either way the denominator shrinks visibly (`7/8` in the table) instead of a failure
    being recorded that the agent never earned.
    """
    try:
        return read()
    except CannotJudge as why:
        return Metric.unjudged(str(why))


def documented(run: Assay) -> Metric:
    """Did the agent explain itself? The one metric here that is a judgement call."""
    words = len(run.response.split())
    return Metric(words >= 20, f"{words} words")


if __name__ == "__main__":
    raise SystemExit(evaluate.cli())
