"""The publication standard, tested as code.

The point of a fixed seed is that these assertions hold today, tomorrow, and on
someone else's machine.
"""

import statistics

import pytest

from trysquare.verdict import (
    ESTABLISHED,
    INCONCLUSIVE,
    gap_interval,
    holm,
    interval,
    judge,
    mean,
    plain,
    points,
    probability,
    signed,
)


class TestReproducible:
    def test_the_same_inputs_give_the_same_interval(self):
        a, b = [1, 2, 3, 4, 5], [4, 5, 6, 7, 8]
        assert gap_interval(a, b) == gap_interval(a, b)

    def test_the_seed_is_actually_used(self):
        """Guards against a seed parameter that is accepted and ignored.

        Needs a sample with enough resolution to show it: on five small integers
        the median interval quantises to the same bounds for any seed, which is a
        property of the data rather than of the code.
        """
        a = [i * 0.37 for i in range(20)]
        b = [i * 0.41 + 1 for i in range(20)]
        assert gap_interval(a, b) != gap_interval(a, b, seed=1)

    def test_an_empty_sample_has_no_interval(self):
        with pytest.raises(ValueError):
            gap_interval([], [1, 2])


class TestTwoStates:
    def test_complete_separation_is_established(self):
        """The shape of the headline result: 10/10 against 0/10."""
        v = judge([1.0] * 10, [0.0] * 10, mean)
        assert v["state"] == ESTABLISHED
        assert v["gap"] == -1.0

    def test_identical_samples_are_inconclusive(self):
        v = judge([1, 0, 1, 0], [1, 0, 1, 0], mean)
        assert v["state"] == INCONCLUSIVE

    def test_a_small_difference_in_noise_is_inconclusive(self):
        """9/10 against 10/10 is the shape of the rule-alone result."""
        v = judge([1.0] * 10, [1.0] * 9 + [0.0], mean)
        assert v["state"] == INCONCLUSIVE

    def test_an_interval_touching_zero_counts_as_inconclusive(self):
        """Deliberately not `low > 0 or high < 0`: touching is not excluding."""
        v = judge([0.0, 1.0], [0.0, 1.0], mean)
        assert v["state"] == INCONCLUSIVE
        assert v["low"] <= 0
        assert v["high"] >= 0

    @pytest.mark.parametrize("a,b", [([1.0] * 5, [0.0] * 5), ([1, 2, 3], [1, 2, 3])])
    def test_there_is_no_third_state(self, a, b):
        assert judge(a, b, mean)["state"] in (ESTABLISHED, INCONCLUSIVE)


class TestRendering:
    def test_a_nonzero_bound_never_renders_as_zero(self):
        """Rounding to integers displayed `[-4, -0]` for a bound worth -0.5, so a
        reader saw an interval containing zero when the computation said the
        opposite."""
        assert signed(-0.5) == "-0.5"
        assert signed(0.4) == "+0.4"

    def test_zero_renders_as_zero(self):
        assert signed(0) == "+0"

    def test_thousands_are_spaced_not_comma_separated(self):
        assert signed(11502) == "+11 502"
        assert signed(-284) == "-284"

    def test_rates_render_in_points(self):
        assert points(-1.0) == "-100 pts"
        assert points(-0.1) == "-10 pts"

    def test_a_level_carries_no_sign(self):
        """A leading `+` on a cost reads as an increase over something."""
        assert plain(15929) == "15 929"
        assert plain(0) == "0"

    def test_a_level_below_one_still_never_renders_as_zero(self):
        assert plain(0.4) == "0.4"


class TestAbsoluteInterval:
    """A cost is published with its dispersion and no verdict."""

    def test_the_interval_brackets_the_statistic(self):
        values = [10, 12, 14, 16, 18, 20, 22, 24]
        low, high = interval(values)
        assert low <= statistics.median(values) <= high

    def test_a_sample_with_no_dispersion_has_a_point_interval(self):
        assert interval([7] * 10) == (7, 7)

    def test_the_same_inputs_give_the_same_interval(self):
        values = [i * 0.37 for i in range(20)]
        assert interval(values) == interval(values)

    def test_the_seed_is_actually_used(self):
        """Needs a sample with enough resolution to show it, like its counterpart
        for a gap: resampling a median lands on the same order statistics whatever
        the seed, and evenly spaced values put every mean on a lattice. Neither is
        evidence that the seed was ignored."""
        values = [1.0, 2.3, 5.7, 11.13, 0.4, 7.9, 3.14159, 42.0, 8.8, 0.07]
        assert interval(values, mean) != interval(values, mean, seed=1)

    def test_an_empty_sample_has_no_interval(self):
        with pytest.raises(ValueError):
            interval([])


class TestStat:
    def test_mean_is_the_statistic_for_a_rate(self):
        assert mean([1, 0, 1, 0]) == 0.5

    def test_median_is_the_default(self):
        v = judge([1, 2, 3], [10, 20, 30])
        assert v["gap"] == statistics.median([10, 20, 30]) - statistics.median([1, 2, 3])


class TestBootstrapP:
    """The p-value is read off the draws the interval comes from, not a second test."""

    def test_complete_separation_has_no_draw_on_the_other_side(self):
        assert judge([1.0] * 10, [0.0] * 10, mean)["p"] == 0

    def test_identical_constant_samples_give_one(self):
        assert judge([1.0] * 10, [1.0] * 10, mean)["p"] == 1

    @pytest.mark.parametrize(
        "a,b",
        [
            ([1.0] * 10, [1.0] * 9 + [0.0]),
            ([1.0] * 10, [1.0] * 6 + [0.0] * 4),
            ([1.0] * 10, [1.0] * 7 + [0.0] * 3),
            ([i * 0.37 for i in range(20)], [i * 0.41 + 1 for i in range(20)]),
            ([1, 0, 1, 0], [1, 0, 1, 0]),
        ],
    )
    def test_it_agrees_with_the_state(self, a, b):
        """Established puts at most 2.5% of the draws on the far side of zero, so p is
        at most 0.05; inconclusive puts at least that much on both, so p is at least
        0.05. The two readings of one sorted list cannot contradict each other."""
        v = judge(a, b, mean)
        assert (v["p"] <= 0.05) if v["state"] == ESTABLISHED else (v["p"] >= 0.05)

    def test_it_does_not_move_the_interval(self):
        a, b = [i * 0.37 for i in range(20)], [i * 0.41 + 1 for i in range(20)]
        v = judge(a, b)
        assert (v["low"], v["high"]) == gap_interval(a, b)


class TestHolm:
    def test_known_values(self):
        got = holm([0.0128, 0.0083, 0.0128, 0.0164, 0.4506])
        assert got == pytest.approx([0.0512, 0.0415, 0.0512, 0.0512, 0.4506])

    def test_it_never_exceeds_one(self):
        assert holm([0.6, 0.7, 0.9]) == pytest.approx([1.0, 1.0, 1.0])

    def test_a_single_test_is_left_alone(self):
        assert holm([0.03]) == [0.03]

    def test_no_test_gives_nothing(self):
        assert holm([]) == []


class TestProbability:
    def test_three_decimals(self):
        assert probability(0.0512) == "p=0.051"

    def test_a_small_p_never_renders_as_zero(self):
        """The same rule as `signed`: 0.0004 shown as `p=0.000` reads as certainty."""
        assert probability(0.0004) == "p<0.001"
        assert probability(0) == "p<0.001"

    def test_one(self):
        assert probability(1.0) == "p=1.000"
