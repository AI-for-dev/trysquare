"""Rendering guards found by running the shipped scenarios for real.

Both cases here were discovered the same way: a run completed, and the *table* was
wrong or refused to build. Neither is a measurement failure, which is why both must
produce an explanation rather than a traceback - the measures are safe on disk and
nothing needs remeasuring.
"""


import pytest

from trysquare.measure import VALID, VALIDATOR_FAILED, Run
from trysquare.table import (
    COST_MEASURES,
    cost_measures,
    criterion_measure,
    gap_rows,
    retry_warning,
    score_rows,
    score_table,
    scored_metrics,
    spend_measures,
    spend_rows,
    spend_table,
)


def run(
    cell,
    overflow=True,
    delivered=True,
    tests=True,
    retries=0,
    ident="r",
    input=100,
    output=10,
    duration=10,
    **extra,
):
    return Run(
        id=ident,
        cell=cell,
        repetition=0,
        usage={"input": input, "output": output, "turns": 2, "retries": retries},
        duration=duration,
        metrics={"overflow": overflow, "delivered": delivered, "tests": tests, **extra},
        state=VALID,
    )


class TestValidityMismatch:
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
        with pytest.raises(ValueError) as e:
            gap_rows(self.cells(), "nothing", self.measures(), validity=("delivered",))
        message = str(e.value)
        assert "delivered" in message
        assert "2 of 2" in message, "it should say how many runs each condition removed"

    def test_it_explains_that_validity_must_match_the_task(self):
        with pytest.raises(ValueError) as e:
            gap_rows(self.cells(), "nothing", self.measures(), validity=("delivered",))
        assert "must match the task" in str(e.value)

    def test_it_reports_the_valid_count_so_the_confusion_is_addressed(self):
        """"No valid run" after eight `ok` lines is baffling without this."""
        with pytest.raises(ValueError) as e:
            gap_rows(self.cells(), "nothing", self.measures(), validity=("delivered",))
        assert "2 of them valid" in str(e.value)

    def test_without_the_bad_condition_the_table_builds(self):
        rows = gap_rows(self.cells(), "nothing", self.measures(), validity=())
        assert [r["cell"] for r in rows] == ["+subagents"]

    def test_a_missing_reference_cell_is_still_reported(self):
        with pytest.raises(ValueError) as e:
            gap_rows(self.cells(), "ghost", self.measures())
        assert "ghost" in str(e.value)


class TestRetryWarning:
    """Cost columns must not be read when retries are present.

    The invariant was documented and not enforced: a synthesis published
    `out -414 established` from a matrix with fourteen retries.
    """

    def test_no_retries_means_no_warning(self):
        assert retry_warning({"a": [run("a"), run("a")]}) == ""

    def test_retries_produce_a_warning_naming_the_columns(self):
        text = retry_warning({"a": [run("a", retries=3)]})
        for column in COST_MEASURES:
            assert column in text

    def test_the_warning_counts_retries_and_names_the_cells(self):
        text = retry_warning(
            {"a": [run("a", retries=3), run("a", retries=1)], "b": [run("b", retries=0)]}
        )
        assert "4 retries" in text
        assert "a" in text

    def test_the_warning_covers_established_results_explicitly(self):
        """Otherwise a reader trusts the star and ignores the note."""
        assert "established" in retry_warning({"a": [run("a", retries=1)]})

    def test_a_clean_cell_is_not_blamed(self):
        text = retry_warning({"clean": [run("clean")], "noisy": [run("noisy", retries=2)]})
        assert "noisy" in text
        assert "clean" not in text


class TestScoreMatrix:
    """Cells in rows, tests in columns, `x/n` in the boxes.

    The gap table answers "which difference survives resampling". It cannot answer
    "what did this cell actually do", which is the question a reader asks first and
    which used to require opening `measures.json`.
    """

    DECLARED = ("overflow", "issues", "delivered", "in_scope", "tests", "cited_paths")

    def cells(self):
        return {
            "rule / off": [run("rule / off", overflow=False), run("rule / off")],
            "nothing / off": [run("nothing / off"), run("nothing / off")],
        }

    def sample(self):
        return [run("a", issues=["#1"], in_scope=True, cited_paths=3)]

    def test_only_the_booleans_become_columns(self):
        tests, other = scored_metrics(self.sample(), self.DECLARED)
        assert tests == ("overflow", "delivered", "in_scope", "tests")

    def test_what_is_not_a_test_is_named_rather_than_dropped(self):
        """A declared metric absent from every table reads as one never measured."""
        _, other = scored_metrics(self.sample(), self.DECLARED)
        assert other == ("issues", "cited_paths")
        assert "cited_paths" in score_table(score_rows(self.cells(), ()), ("x",), other)

    def test_a_box_counts_the_runs_that_passed_out_of_those_that_judged(self):
        rows = score_rows(self.cells(), ("overflow", "delivered"))
        assert rows[0]["scores"] == ["1/2", "2/2"]

    def test_an_unjudged_metric_shrinks_its_own_denominator_only(self):
        """`unjudged` means "could not say", which is not "said false"."""
        cells = {"a": [run("a"), Run(id="r", cell="a", repetition=1, metrics={"delivered": True})]}
        rows = score_rows(cells, ("overflow", "delivered"))
        assert rows[0]["scores"] == ["1/1", "2/2"]

    def test_a_metric_no_run_could_judge_shows_a_dash(self):
        assert score_rows(self.cells(), ("ghost",))[0]["scores"] == ["-"]

    def test_rows_follow_the_declared_cell_order(self):
        rows = score_rows(self.cells(), ("overflow",), order=("nothing / off", "rule / off"))
        assert [r["cell"] for r in rows] == ["nothing / off", "rule / off"]

    def test_a_cell_absent_from_the_order_is_still_rendered(self):
        """A ledger may predate a scenario edit; a cell must never vanish silently."""
        rows = score_rows(self.cells(), ("overflow",), order=("nothing / off",))
        assert [r["cell"] for r in rows] == ["nothing / off", "rule / off"]

    def test_an_invalid_run_is_in_no_denominator_and_is_reported(self):
        broken = run("rule / off")
        broken.state = VALIDATOR_FAILED
        cells = {"rule / off": [run("rule / off"), broken]}
        rows = score_rows(cells, ("overflow",))
        assert rows[0]["scores"] == ["1/1"]
        assert "invalid: rule / off (1)" in score_table(rows, ("overflow",))

    def test_the_matrix_names_its_columns_and_its_cells(self):
        text = score_table(score_rows(self.cells(), ("overflow",)), ("overflow",))
        assert "| cell | overflow |" in text
        assert "| rule / off | 1/2 |" in text

    def test_a_scenario_with_no_boolean_metric_says_so(self):
        assert "No test to score" in score_table(score_rows(self.cells(), ()), ())


class TestCostTable:
    """What a run cost, as a level: median and 95% interval, per cell.

    The gap table costs a *difference*, which answers "is this configuration more
    expensive than the reference" and not "can I afford it at all". The second
    question needed `measures.json` and a calculator.
    """

    def cells(self):
        return {
            "rule": [run("rule", input=200, output=20, duration=30) for _ in range(4)],
            "nothing": [run("nothing") for _ in range(4)],
        }

    def test_the_columns_are_the_tokens_and_the_duration(self):
        assert [m.name for m in spend_measures()] == ["in", "out", "duration (s)"]

    def test_a_box_carries_the_median_then_its_interval(self):
        rows = spend_rows(self.cells(), spend_measures())
        assert rows[0]["spend"][0] == "200 [200, 200]"

    def test_a_level_is_rendered_without_a_sign(self):
        """A `+` would read as an increase over a reference this table has none of."""
        text = spend_table(spend_rows(self.cells(), spend_measures()), spend_measures(), 10, 1)
        assert "+" not in text

    def test_a_dispersed_cell_gets_an_interval_wider_than_a_point(self):
        cells = {"a": [run("a", input=i * 1000) for i in range(1, 9)]}
        low, high = spend_rows(cells, spend_measures())[0]["spend"][0].split(" [")[1].split(", ")
        assert low != high.rstrip("]")

    def test_costs_rest_on_the_same_runs_as_the_verdict(self):
        """A run that delivered nothing is cheap by construction: averaging it in
        makes the configuration that fails most often look like the affordable one."""
        cells = {"a": [run("a", input=1000), run("a", input=10, delivered=False)]}
        rows = spend_rows(cells, spend_measures(), validity=("delivered",))
        assert rows[0]["n"] == 1
        assert rows[0]["spend"][0] == "1 000 [1 000, 1 000]"

    def test_a_cell_with_nothing_left_shows_a_dash_rather_than_raising(self):
        cells = {"a": [run("a", delivered=False)]}
        rows = spend_rows(cells, spend_measures(), validity=("delivered",))
        assert rows[0]["spend"] == ["-", "-", "-"]

    def test_rows_follow_the_declared_cell_order(self):
        rows = spend_rows(self.cells(), spend_measures(), order=("nothing", "rule"))
        assert [r["cell"] for r in rows] == ["nothing", "rule"]

    def test_the_table_states_how_many_runs_each_level_rests_on(self):
        text = spend_table(spend_rows(self.cells(), spend_measures()), spend_measures(), 10, 1)
        assert "| cell | n | in | out | duration (s) |" in text
        assert "| rule | 4 |" in text

    def test_the_table_refuses_to_let_a_level_read_as_a_verdict(self):
        """Two intervals that do not overlap are not a result; the gap table is."""
        text = spend_table(spend_rows(self.cells(), spend_measures()), spend_measures(), 10, 1)
        assert "carries no verdict" in text

    def test_the_draws_and_the_seed_are_stated(self):
        text = spend_table(spend_rows(self.cells(), spend_measures()), spend_measures(), 10, 1)
        assert "10 draws, seed 1" in text
