"""Rendering guards found by running the shipped scenarios for real.

Both cases here were discovered the same way: a run completed, and the *table* was
wrong or refused to build. Neither is a measurement failure, which is why both must
produce an explanation rather than a traceback - the measures are safe on disk and
nothing needs remeasuring.
"""

import unittest

from trysquare.measure import VALID, Run
from trysquare.table import COST_MEASURES, cost_measures, criterion_measure, gap_rows, retry_warning


def run(cell, overflow=True, delivered=True, tests=True, retries=0, ident="r"):
    return Run(
        id=ident,
        cell=cell,
        repetition=0,
        usage={"input": 100, "output": 10, "turns": 2, "retries": retries},
        duration=10,
        metrics={"overflow": overflow, "delivered": delivered, "tests": tests},
        state=VALID,
    )


class TestValidityMismatch(unittest.TestCase):
    """A validity condition must match the task, and saying so is the whole point.

    The subagents scenario asks the agent to write no code. Every run was correctly
    `delivered = false`, `validity = ["delivered"]` eliminated the entire matrix, and
    the message said only "no valid run" right after eight successful runs.
    """

    def cells(self):
        return {
            "nothing": [run("nothing", delivered=False), run("nothing", delivered=False)],
            "+subagents": [run("+subagents", delivered=False)],
        }

    def measures(self):
        return cost_measures() + (criterion_measure("overflow"),)

    def test_it_names_the_metric_that_eliminated_everything(self):
        with self.assertRaises(ValueError) as e:
            gap_rows(self.cells(), "nothing", self.measures(), validity=("delivered",))
        message = str(e.exception)
        self.assertIn("delivered", message)
        self.assertIn("2 of 2", message, "it should say how many runs each condition removed")

    def test_it_explains_that_validity_must_match_the_task(self):
        with self.assertRaises(ValueError) as e:
            gap_rows(self.cells(), "nothing", self.measures(), validity=("delivered",))
        self.assertIn("must match the task", str(e.exception))

    def test_it_reports_the_valid_count_so_the_confusion_is_addressed(self):
        """"No valid run" after eight `ok` lines is baffling without this."""
        with self.assertRaises(ValueError) as e:
            gap_rows(self.cells(), "nothing", self.measures(), validity=("delivered",))
        self.assertIn("2 of them valid", str(e.exception))

    def test_without_the_bad_condition_the_table_builds(self):
        rows = gap_rows(self.cells(), "nothing", self.measures(), validity=())
        self.assertEqual([r["cell"] for r in rows], ["+subagents"])

    def test_a_missing_reference_cell_is_still_reported(self):
        with self.assertRaises(ValueError) as e:
            gap_rows(self.cells(), "ghost", self.measures())
        self.assertIn("ghost", str(e.exception))


class TestRetryWarning(unittest.TestCase):
    """Cost columns must not be read when retries are present.

    The invariant was documented and not enforced: a synthesis published
    `out -414 established` from a matrix with fourteen retries.
    """

    def test_no_retries_means_no_warning(self):
        self.assertEqual(retry_warning({"a": [run("a"), run("a")]}), "")

    def test_retries_produce_a_warning_naming_the_columns(self):
        text = retry_warning({"a": [run("a", retries=3)]})
        for column in COST_MEASURES:
            self.assertIn(column, text)

    def test_the_warning_counts_retries_and_names_the_cells(self):
        text = retry_warning(
            {"a": [run("a", retries=3), run("a", retries=1)], "b": [run("b", retries=0)]}
        )
        self.assertIn("4 retries", text)
        self.assertIn("a", text)

    def test_the_warning_covers_established_results_explicitly(self):
        """Otherwise a reader trusts the star and ignores the note."""
        self.assertIn("established", retry_warning({"a": [run("a", retries=1)]}))

    def test_a_clean_cell_is_not_blamed(self):
        text = retry_warning({"clean": [run("clean")], "noisy": [run("noisy", retries=2)]})
        self.assertIn("noisy", text)
        self.assertNotIn("clean", text)


if __name__ == "__main__":
    unittest.main()
