"""Layer 3 of parity: aggregation and verdict, checked exactly.

The fixture is the bench's own published measures for the 2x3 matrix of module
2.1 - 60 per-run rows, 6 cells of 10. The expected values below are what the
bench *published*, copied from its markdown output.

This is the strongest test in the suite and the cheapest: no tokens, no model, no
sampling noise. Identical inputs through an identical method must give identical
output. If this fails, exactly one of three things is true, and the third is the
one worth remembering: this harness is wrong, or the bench was wrong and a
published number needs correcting, or the archive is missing something.
"""

import unittest
from pathlib import Path

from trysquare.parity import compare, layer3, read_bench_measures

FIXTURE = Path(__file__).parent / "fixtures" / "bench_2x3_n10.json"

# Copied from the bench's published table, reference cell `base`:
#
#   | +AGENTS.md               | +11 502 o | -284 o  | +1 o | -8 o   | -10 pts o  |
#   | +AGENTS.md +thinking     | +43 248 * | +4 767 *| +2 o | +144 * | -80 pts *  |
#   | +thinking                | +72 289 * | +6 175 *| +4 o | +213 * | -30 pts o  |
#   | +ticket soigne           | +15 929 o | -296 o  | +3 * | +7 o   | -100 pts * |
#   | +ticket soigne +thinking | +52 979 * | +6 873 *| +4 * | +224 * | -100 pts * |
PUBLISHED = {
    "+AGENTS.md": {
        "in": "+11 502 o",
        "out": "-284 o",
        "turns": "+1 o",
        "duration": "-8 o",
        "overflow": "-10 pts o",
    },
    "+AGENTS.md +thinking": {
        "in": "+43 248 *",
        "out": "+4 767 *",
        "turns": "+2 o",
        "duration": "+144 *",
        "overflow": "-80 pts *",
    },
    "+thinking": {
        "in": "+72 289 *",
        "out": "+6 175 *",
        "turns": "+4 o",
        "duration": "+213 *",
        "overflow": "-30 pts o",
    },
    "+ticket soigné": {
        "in": "+15 929 o",
        "out": "-296 o",
        "turns": "+3 *",
        "duration": "+7 o",
        "overflow": "-100 pts *",
    },
    "+ticket soigné +thinking": {
        "in": "+52 979 *",
        "out": "+6 873 *",
        "turns": "+4 *",
        "duration": "+224 *",
        "overflow": "-100 pts *",
    },
}


class TestArchive(unittest.TestCase):
    """What the archive contains is a fact this suite pins down."""

    def test_sixty_runs_in_six_cells(self):
        by_cell = read_bench_measures(FIXTURE)
        self.assertEqual(len(by_cell), 6)
        self.assertEqual(sum(len(v) for v in by_cell.values()), 60)
        for cell, runs in by_cell.items():
            self.assertEqual(len(runs), 10, f"{cell} should have 10 runs")

    def test_per_run_rows_carry_their_scoring(self):
        """Exact parity is only possible because scoring is archived per run."""
        run = read_bench_measures(FIXTURE)["base"][0]
        for metric in ("overflow", "delivered", "tests", "api_stable", "in_scope"):
            self.assertIn(metric, run.metrics)
        self.assertGreater(run.usage["input"], 0)
        self.assertGreater(run.usage["turns"], 0)

    def test_the_matrix_had_no_retries(self):
        """Which is what lets the cost columns be read at all."""
        by_cell = read_bench_measures(FIXTURE)
        total = sum(r.retries for runs in by_cell.values() for r in runs)
        self.assertEqual(total, 0)


class TestLayer3(unittest.TestCase):
    def test_reproduces_the_published_gap_table(self):
        rows = layer3(FIXTURE, reference="base", criterion="overflow")
        problems = compare(rows, PUBLISHED)
        self.assertEqual(problems, [], "\n".join(["parity broken:", *problems]))

    def test_the_headline_result_survives(self):
        """The result module 2.1 rests on, asserted on its own.

        A well written ticket separates completely: -100 points, established.
        """
        rows = layer3(FIXTURE, reference="base", criterion="overflow")
        row = next(r for r in rows if r["cell"] == "+ticket soigné")
        overflow = next(c for c in row["measures"] if c["measure"] == "overflow")
        self.assertEqual(overflow["rendered"], "-100 pts")
        self.assertEqual(overflow["state"], "established")

    def test_the_rule_alone_stays_inconclusive(self):
        """The other half of the lesson, and it is a negative result.

        A convention filed in AGENTS.md, without paying for the reasoning that
        goes and reads it, does not establish anything at n=10.
        """
        rows = layer3(FIXTURE, reference="base", criterion="overflow")
        row = next(r for r in rows if r["cell"] == "+AGENTS.md")
        overflow = next(c for c in row["measures"] if c["measure"] == "overflow")
        self.assertEqual(overflow["state"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
