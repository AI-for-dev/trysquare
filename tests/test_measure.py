"""The rules that decide whether a run is a measurement or an incident.

The tests named after a disguise correspond to a defect the bench actually
shipped. They exist so it cannot ship again.
"""

import json

from trysquare.measure import (
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


class TestStrip:
    def test_turns_are_counted_on_usage_bearing_messages(self):
        s = stream(
            {"type": "agent_start"},
            {"type": "turn_start"},
            message_end(100, 10),
            message_end(200, 20),
            {"type": "agent_end"},
        )
        u = strip(s)
        assert u["turns"] == 2
        assert u["input"] == 300
        assert u["output"] == 30

    def test_a_message_without_usage_is_not_a_turn(self):
        """The filter that makes a counted turn a billed turn."""
        s = stream({"type": "message_end", "message": {}}, message_end())
        assert strip(s)["turns"] == 1

    def test_retries_are_counted(self):
        s = stream(
            {"type": "auto_retry_start", "attempt": 1},
            {"type": "auto_retry_start", "attempt": 2},
            message_end(),
        )
        assert strip(s)["retries"] == 2

    def test_garbage_lines_are_skipped_not_fatal(self):
        s = "not json\n" + stream(message_end()) + "\n{broken"
        assert strip(s)["turns"] == 1


class TestStripSession:
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
        assert u["turns"] == 2
        assert u["input"] == 600

    def test_retries_are_none_not_zero(self):
        """A session cannot know: `auto_retry_start` is a stream event.

        Reported as None so a caller cannot mistake absence for zero, which would
        silently widen the parity scope to a column that is not archived.
        """
        u = strip_session(
            stream({"type": "message", "message": {"usage": {"input": 1, "output": 1}}})
        )
        assert u["retries"] is None

    def test_thinking_levels_are_recoverable(self):
        """What lets the smoke pass check the declared level actually ran."""
        s = stream(
            {"type": "thinking_level_change", "level": "high"},
            {"type": "message", "message": {}},
        )
        assert thinking_levels(s) == ["high"]


class TestConsumedTokens:
    """ "Did not do the work" must never read as "worked well"."""

    def test_a_real_run_counts(self):
        assert consumed_tokens({"turns": 4, "input": 15900, "output": 800})

    def test_turns_without_tokens_do_not_count(self):
        """Observed in practice: the provider cuts the stream, `pi` retries and
        returns turns that are real but entirely empty."""
        assert not consumed_tokens({"turns": 3, "input": 0, "output": 0})

    def test_nothing_at_all_does_not_count(self):
        assert not consumed_tokens({})


class TestKind:
    def test_booleans_are_rates(self):
        assert kind(True) == "rate"

    def test_numbers_are_medians(self):
        assert kind(3) == "median"
        assert kind(2.5) == "median"

    def test_lists_are_diagnostic_only(self):
        assert kind(["#1"]) == "diagnostic"
        assert not scorable(["#1"])

    def test_a_boolean_is_not_read_as_a_number(self):
        """bool is a subclass of int in Python: order matters in `kind`."""
        assert kind(False) == "rate"


class TestMerge:
    DECLARED = ("overflow", "delivered")

    def test_metrics_and_reasons_combine(self):
        metrics, reasons, state, _ = merge(
            [
                (
                    "script",
                    {
                        "metrics": {"overflow": True, "delivered": True},
                        "reasons": {"overflow": "issue #1"},
                    },
                ),
            ],
            self.DECLARED,
        )
        assert state == VALID
        assert metrics == {"overflow": True, "delivered": True}
        assert reasons["overflow"] == "issue #1"

    def test_a_missing_declared_metric_invalidates_the_run(self):
        _, _, state, detail = merge([("script", {"metrics": {"overflow": True}})], self.DECLARED)
        assert state == VALIDATOR_FAILED
        assert "delivered" in detail

    def test_an_extra_metric_is_kept(self):
        """So a general validator is reusable, and a metric already paid for can
        be scored later without remeasuring."""
        metrics, _, state, _ = merge(
            [("script", {"metrics": {"overflow": True, "delivered": True, "tests": True}})],
            self.DECLARED,
        )
        assert state == VALID
        assert "tests" in metrics

    def test_a_failed_validator_never_yields_false(self):
        metrics, _, state, detail = merge([("script", None)], self.DECLARED)
        assert state == VALIDATOR_FAILED
        assert "overflow" not in metrics
        assert "script" in detail

    def test_a_validator_returning_no_metrics_object_fails(self):
        _, _, state, _ = merge([("script", {"oops": 1})], self.DECLARED)
        assert state == VALIDATOR_FAILED

    def test_two_validators_both_contribute(self):
        metrics, _, state, _ = merge(
            [
                ("script", {"metrics": {"overflow": True, "delivered": True}}),
                ("judge", {"metrics": {"usable": False}}),
            ],
            self.DECLARED + ("usable",),
        )
        assert state == VALID
        assert len(metrics) == 3


class TestUnjudgedMetrics:
    """A validator may say "not this metric" without losing the rest of the run.

    `rate` already knew how to represent it - "out of how many **could say**", so an
    absent key leaves the denominator - and only `merge` forbade it, by invalidating the
    whole run for any declared metric that was missing.

    Safe because the name is still returned: a **typo** produces a genuinely absent key
    and still fails loudly, while an honest "I cannot say" shrinks a denominator, which
    `table.py:228` renders as `7/8` on the face of the result.
    """

    DECLARED = ("overflow", "delivered", "red_first")

    def payload(self):
        return {
            "metrics": {"overflow": True, "delivered": True},
            "reasons": {},
            "unjudged": {"red_first": "no session archived"},
        }

    def test_the_run_stays_valid(self):
        _, _, state, detail = merge([("script", self.payload())], self.DECLARED)
        assert state == VALID, detail

    def test_the_unjudged_metric_is_not_recorded_as_a_value(self):
        """Recording it as `false` would be "could not judge" filed as "worked badly"."""
        metrics, _, _, _ = merge([("script", self.payload())], self.DECLARED)
        assert "red_first" not in metrics

    def test_its_reason_is_kept_so_the_hole_is_readable(self):
        _, reasons, _, _ = merge([("script", self.payload())], self.DECLARED)
        assert reasons["red_first"] == "no session archived"

    def test_a_typo_is_still_an_invalid_run(self):
        """The whole reason the name is returned rather than simply omitted."""
        payload = self.payload()
        payload["unjudged"] = {"red_frist": "no session archived"}
        _, _, state, detail = merge([("script", payload)], self.DECLARED)
        assert state == VALIDATOR_FAILED
        assert "red_first" in detail

    def test_a_validator_that_never_uses_it_is_unaffected(self):
        """The key is an addition to the contract, not a change to it."""
        metrics, _, state, _ = merge(
            [("script", {"metrics": {"overflow": True, "delivered": True}})],
            ("overflow", "delivered"),
        )
        assert state == VALID
        assert metrics == {"overflow": True, "delivered": True}


class TestManual:
    def test_a_form_fills_a_hole(self):
        run = Run("a", "none", 0, metrics={"overflow": True})
        refused = fill_manual(run, {"readable": True})
        assert refused == []
        assert run.metrics["readable"]

    def test_a_form_never_overwrites_a_measurement(self):
        """The bench computes the verdict, the author does not work around it."""
        run = Run("a", "none", 0, metrics={"tests": True})
        refused = fill_manual(run, {"tests": False})
        assert refused == ["tests"]
        assert run.metrics["tests"], "the measured value must survive"


class TestAggregation:
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
        assert [r.id for r in ok] == ["a", "b"]

    def test_an_invalid_run_never_enters_aggregation(self):
        ok = valid_runs(self.runs())
        assert "d" not in [r.id for r in ok]

    def test_rate_counts_only_runs_that_could_say(self):
        ok = valid_runs(self.runs(), ("delivered", "tests"))
        assert rate(ok, "overflow") == (1, 2)

    def test_series_turns_booleans_into_ones_and_zeros(self):
        ok = valid_runs(self.runs(), ("delivered", "tests"))
        assert series(ok, "overflow") == [1, 0]
