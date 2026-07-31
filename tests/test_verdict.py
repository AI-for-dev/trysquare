"""The publication standard, tested as code.

The point of a fixed seed is that these assertions hold today, tomorrow, and on
someone else's machine.
"""

import statistics
import unittest

from trysquare.verdict import (
    ESTABLISHED,
    INCONCLUSIVE,
    gap_interval,
    interval,
    judge,
    mean,
    plain,
    points,
    signed,
)


class TestReproducible(unittest.TestCase):
    def test_the_same_inputs_give_the_same_interval(self):
        a, b = [1, 2, 3, 4, 5], [4, 5, 6, 7, 8]
        self.assertEqual(gap_interval(a, b), gap_interval(a, b))

    def test_the_seed_is_actually_used(self):
        """Guards against a seed parameter that is accepted and ignored.

        Needs a sample with enough resolution to show it: on five small integers
        the median interval quantises to the same bounds for any seed, which is a
        property of the data rather than of the code.
        """
        a = [i * 0.37 for i in range(20)]
        b = [i * 0.41 + 1 for i in range(20)]
        self.assertNotEqual(gap_interval(a, b), gap_interval(a, b, seed=1))

    def test_an_empty_sample_has_no_interval(self):
        with self.assertRaises(ValueError):
            gap_interval([], [1, 2])


class TestTwoStates(unittest.TestCase):
    def test_complete_separation_is_established(self):
        """The shape of the headline result: 10/10 against 0/10."""
        v = judge([1.0] * 10, [0.0] * 10, mean)
        self.assertEqual(v["state"], ESTABLISHED)
        self.assertEqual(v["gap"], -1.0)

    def test_identical_samples_are_inconclusive(self):
        v = judge([1, 0, 1, 0], [1, 0, 1, 0], mean)
        self.assertEqual(v["state"], INCONCLUSIVE)

    def test_a_small_difference_in_noise_is_inconclusive(self):
        """9/10 against 10/10 is the shape of the rule-alone result."""
        v = judge([1.0] * 10, [1.0] * 9 + [0.0], mean)
        self.assertEqual(v["state"], INCONCLUSIVE)

    def test_an_interval_touching_zero_counts_as_inconclusive(self):
        """Deliberately not `low > 0 or high < 0`: touching is not excluding."""
        v = judge([0.0, 1.0], [0.0, 1.0], mean)
        self.assertEqual(v["state"], INCONCLUSIVE)
        self.assertLessEqual(v["low"], 0)
        self.assertGreaterEqual(v["high"], 0)

    def test_there_is_no_third_state(self):
        for a, b in (([1.0] * 5, [0.0] * 5), ([1, 2, 3], [1, 2, 3])):
            self.assertIn(judge(a, b, mean)["state"], (ESTABLISHED, INCONCLUSIVE))


class TestRendering(unittest.TestCase):
    def test_a_nonzero_bound_never_renders_as_zero(self):
        """Rounding to integers displayed `[-4, -0]` for a bound worth -0.5, so a
        reader saw an interval containing zero when the computation said the
        opposite."""
        self.assertEqual(signed(-0.5), "-0.5")
        self.assertEqual(signed(0.4), "+0.4")

    def test_zero_renders_as_zero(self):
        self.assertEqual(signed(0), "+0")

    def test_thousands_are_spaced_not_comma_separated(self):
        self.assertEqual(signed(11502), "+11 502")
        self.assertEqual(signed(-284), "-284")

    def test_rates_render_in_points(self):
        self.assertEqual(points(-1.0), "-100 pts")
        self.assertEqual(points(-0.1), "-10 pts")

    def test_a_level_carries_no_sign(self):
        """A leading `+` on a cost reads as an increase over something."""
        self.assertEqual(plain(15929), "15 929")
        self.assertEqual(plain(0), "0")

    def test_a_level_below_one_still_never_renders_as_zero(self):
        self.assertEqual(plain(0.4), "0.4")


class TestAbsoluteInterval(unittest.TestCase):
    """A cost is published with its dispersion and no verdict."""

    def test_the_interval_brackets_the_statistic(self):
        values = [10, 12, 14, 16, 18, 20, 22, 24]
        low, high = interval(values)
        self.assertLessEqual(low, statistics.median(values))
        self.assertLessEqual(statistics.median(values), high)

    def test_a_sample_with_no_dispersion_has_a_point_interval(self):
        self.assertEqual(interval([7] * 10), (7, 7))

    def test_the_same_inputs_give_the_same_interval(self):
        values = [i * 0.37 for i in range(20)]
        self.assertEqual(interval(values), interval(values))

    def test_the_seed_is_actually_used(self):
        """Needs a sample with enough resolution to show it, like its counterpart
        for a gap: resampling a median lands on the same order statistics whatever
        the seed, and evenly spaced values put every mean on a lattice. Neither is
        evidence that the seed was ignored."""
        values = [1.0, 2.3, 5.7, 11.13, 0.4, 7.9, 3.14159, 42.0, 8.8, 0.07]
        self.assertNotEqual(interval(values, mean), interval(values, mean, seed=1))

    def test_an_empty_sample_has_no_interval(self):
        with self.assertRaises(ValueError):
            interval([])


class TestStat(unittest.TestCase):
    def test_mean_is_the_statistic_for_a_rate(self):
        self.assertEqual(mean([1, 0, 1, 0]), 0.5)

    def test_median_is_the_default(self):
        v = judge([1, 2, 3], [10, 20, 30])
        self.assertEqual(v["gap"], statistics.median([10, 20, 30]) - statistics.median([1, 2, 3]))


if __name__ == "__main__":
    unittest.main()
