"""The rules that decide whether a run is a measurement or an incident.

The tests named after a disguise correspond to a defect the bench actually
shipped. They exist so it cannot ship again.
"""

import json
import unittest

from etabli.measure import (
    EMPTY,
    VALID,
    VALIDATOR_FAILED,
    Run,
    consumed_tokens,
    fill_manual,
    kind,
    merge,
    rate,
    scorable,
    series,
    strip,
    strip_session,
    thinking_levels,
    valid_runs,
)


def stream(*events) -> str:
    return "\n".join(json.dumps(e) for e in events)


def message_end(inp=100, out=10, turns=1):
    return {"type": "message_end", "message": {"usage": {"input": inp, "output": out}}}


class TestStrip(unittest.TestCase):
    def test_turns_are_counted_on_usage_bearing_messages(self):
        s = stream(
            {"type": "agent_start"},
            {"type": "turn_start"},
            message_end(100, 10),
            message_end(200, 20),
            {"type": "agent_end"},
        )
        u = strip(s)
        self.assertEqual(u["turns"], 2)
        self.assertEqual(u["input"], 300)
        self.assertEqual(u["output"], 30)

    def test_a_message_without_usage_is_not_a_turn(self):
        """The filter that makes a counted turn a billed turn."""
        s = stream({"type": "message_end", "message": {}}, message_end())
        self.assertEqual(strip(s)["turns"], 1)

    def test_retries_are_counted(self):
        s = stream(
            {"type": "auto_retry_start", "attempt": 1},
            {"type": "auto_retry_start", "attempt": 2},
            message_end(),
        )
        self.assertEqual(strip(s)["retries"], 2)

    def test_garbage_lines_are_skipped_not_fatal(self):
        s = "not json\n" + stream(message_end()) + "\n{broken"
        self.assertEqual(strip(s)["turns"], 1)


class TestStripSession(unittest.TestCase):
    """The archived session has a different shape from the live stream.

    Which is what makes comparing the two a real test of the stripping layer
    rather than a tautology.
    """

    def test_usage_is_read_from_message_lines(self):
        s = stream(
            {"type": "session", "version": 3},
            {"type": "thinking_level_change", "level": "off"},
            {"type": "message", "message": {"usage": {"input": 500, "output": 50}}},
            {"type": "message", "message": {"usage": {"input": 100, "output": 10}}},
        )
        u = strip_session(s)
        self.assertEqual(u["turns"], 2)
        self.assertEqual(u["input"], 600)

    def test_retries_are_none_not_zero(self):
        """A session cannot know: `auto_retry_start` is a stream event.

        Reported as None so a caller cannot mistake absence for zero, which would
        silently widen the parity scope to a column that is not archived.
        """
        u = strip_session(stream({"type": "message", "message": {"usage": {"input": 1, "output": 1}}}))
        self.assertIsNone(u["retries"])

    def test_thinking_levels_are_recoverable(self):
        """What lets the smoke pass check the declared level actually ran."""
        s = stream(
            {"type": "thinking_level_change", "level": "high"},
            {"type": "message", "message": {}},
        )
        self.assertEqual(thinking_levels(s), ["high"])


class TestConsumedTokens(unittest.TestCase):
    """"Did not do the work" must never read as "worked well"."""

    def test_a_real_run_counts(self):
        self.assertTrue(consumed_tokens({"turns": 4, "input": 15900, "output": 800}))

    def test_turns_without_tokens_do_not_count(self):
        """Observed in practice: the provider cuts the stream, `pi` retries and
        returns turns that are real but entirely empty."""
        self.assertFalse(consumed_tokens({"turns": 3, "input": 0, "output": 0}))

    def test_nothing_at_all_does_not_count(self):
        self.assertFalse(consumed_tokens({}))


class TestKind(unittest.TestCase):
    def test_booleans_are_rates(self):
        self.assertEqual(kind(True), "rate")

    def test_numbers_are_medians(self):
        self.assertEqual(kind(3), "median")
        self.assertEqual(kind(2.5), "median")

    def test_lists_are_diagnostic_only(self):
        self.assertEqual(kind(["#1"]), "diagnostic")
        self.assertFalse(scorable(["#1"]))

    def test_a_boolean_is_not_read_as_a_number(self):
        """bool is a subclass of int in Python: order matters in `kind`."""
        self.assertEqual(kind(False), "rate")


class TestMerge(unittest.TestCase):
    DECLARED = ("overflow", "delivered")

    def test_metrics_and_reasons_combine(self):
        metrics, reasons, state, _ = merge(
            [
                ("script", {"metrics": {"overflow": True, "delivered": True}, "reasons": {"overflow": "issue #1"}}),
            ],
            self.DECLARED,
        )
        self.assertEqual(state, VALID)
        self.assertEqual(metrics, {"overflow": True, "delivered": True})
        self.assertEqual(reasons["overflow"], "issue #1")

    def test_a_missing_declared_metric_invalidates_the_run(self):
        _, _, state, detail = merge([("script", {"metrics": {"overflow": True}})], self.DECLARED)
        self.assertEqual(state, VALIDATOR_FAILED)
        self.assertIn("delivered", detail)

    def test_an_extra_metric_is_kept(self):
        """So a general validator is reusable, and a metric already paid for can
        be scored later without remeasuring."""
        metrics, _, state, _ = merge(
            [("script", {"metrics": {"overflow": True, "delivered": True, "tests": True}})],
            self.DECLARED,
        )
        self.assertEqual(state, VALID)
        self.assertIn("tests", metrics)

    def test_a_failed_validator_never_yields_false(self):
        metrics, _, state, detail = merge([("script", None)], self.DECLARED)
        self.assertEqual(state, VALIDATOR_FAILED)
        self.assertNotIn("overflow", metrics)
        self.assertIn("script", detail)

    def test_a_validator_returning_no_metrics_object_fails(self):
        _, _, state, _ = merge([("script", {"oops": 1})], self.DECLARED)
        self.assertEqual(state, VALIDATOR_FAILED)

    def test_two_validators_both_contribute(self):
        metrics, _, state, _ = merge(
            [
                ("script", {"metrics": {"overflow": True, "delivered": True}}),
                ("judge", {"metrics": {"usable": False}}),
            ],
            self.DECLARED + ("usable",),
        )
        self.assertEqual(state, VALID)
        self.assertEqual(len(metrics), 3)


class TestManual(unittest.TestCase):
    def test_a_form_fills_a_hole(self):
        run = Run("a", "none", 0, metrics={"overflow": True})
        refused = fill_manual(run, {"readable": True})
        self.assertEqual(refused, [])
        self.assertTrue(run.metrics["readable"])

    def test_a_form_never_overwrites_a_measurement(self):
        """The bench computes the verdict, the author does not work around it."""
        run = Run("a", "none", 0, metrics={"tests": True})
        refused = fill_manual(run, {"tests": False})
        self.assertEqual(refused, ["tests"])
        self.assertTrue(run.metrics["tests"], "the measured value must survive")


class TestAggregation(unittest.TestCase):
    def runs(self):
        return [
            Run("a", "c", 0, metrics={"overflow": True, "delivered": True, "tests": True}),
            Run("b", "c", 1, metrics={"overflow": False, "delivered": True, "tests": True}),
            # Delivered nothing: passes the tests by construction, and without the
            # validity filter reads as a perfect score.
            Run("c", "c", 2, metrics={"overflow": False, "delivered": False, "tests": True}),
            Run("d", "c", 3, state=VALIDATOR_FAILED),
        ]

    def test_validity_filters_the_empty_diff(self):
        ok = valid_runs(self.runs(), ("delivered", "tests"))
        self.assertEqual([r.id for r in ok], ["a", "b"])

    def test_an_invalid_run_never_enters_aggregation(self):
        ok = valid_runs(self.runs())
        self.assertNotIn("d", [r.id for r in ok])

    def test_rate_counts_only_runs_that_could_say(self):
        ok = valid_runs(self.runs(), ("delivered", "tests"))
        self.assertEqual(rate(ok, "overflow"), (1, 2))

    def test_series_turns_booleans_into_ones_and_zeros(self):
        ok = valid_runs(self.runs(), ("delivered", "tests"))
        self.assertEqual(series(ok, "overflow"), [1, 0])


if __name__ == "__main__":
    unittest.main()
