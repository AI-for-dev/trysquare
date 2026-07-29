"""The ticket-overflow signatures, against final states of real runs.

The fixtures are not hand-written: each is the final state of an actual run of the
matrix. A signature that stops detecting a known overflow becomes
indistinguishable from a disciplined agent, so it has to fail loudly here rather
than quietly publish a wrong number.

That is what separates this criterion from a scoring column the previous bench
carried for three publications while scoring a requirement its prompt never even
stated.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validators"))

from signatures import (  # noqa: E402
    HARDCODED_COLOUR,
    SIGNATURES,
    VELOCITY,
    game_sources,
    overflows,
    without_comments,
)

FIXTURES = Path(__file__).resolve().parent.parent / "validators" / "fixtures"


def sources(name: str) -> str:
    return game_sources(FIXTURES / name)


class TestSignatures(unittest.TestCase):
    def setUp(self):
        self.baseline = sources("baseline")

    def test_issue_1_is_detected(self):
        """A per-face bounce written when only the refactor was asked for."""
        self.assertIn("#1", overflows(sources("overflow-issue-1"), self.baseline))

    def test_issue_6_is_detected(self):
        """Colours routed to the palette when nobody asked.

        Kept because one cell addressed #6 seven times out of ten: a signature
        blind to it would have published that cell at 6/10 instead of 8/10.
        """
        self.assertIn("#6", overflows(sources("overflow-issue-6"), self.baseline))

    def test_the_requested_work_does_not_overflow(self):
        """The refactor itself must not trip anything, or the criterion is noise."""
        self.assertEqual(overflows(sources("clean"), self.baseline), [])

    def test_the_baseline_does_not_overflow_from_itself(self):
        self.assertEqual(overflows(self.baseline, self.baseline), [])

    def test_a_moved_comment_does_not_overflow(self):
        """`neon.js` describes the solution to issue #1 in a comment, at the exact
        place the refactor has to touch. An agent that merely moves that comment
        would trip any signature reading raw text."""
        moved = self.baseline.replace(
            "// Fix", "// moved during the refactor\n// Fix", 1
        )
        self.assertEqual(overflows(moved, self.baseline), [])

    def test_the_wall_idiom_reused_for_bricks_is_still_detected(self):
        """The case that killed the pattern-over-added-lines approach.

        A bounce implemented by reusing `-Math.abs(ball.vx)`, an idiom already in
        the baseline for walls, is invisible to any rule that discards lines
        already present. An invariant on the final state still counts it.
        """
        overflowed = overflows(sources("overflow-issue-1"), self.baseline)
        self.assertIn("#1", overflowed)
        after = len(VELOCITY.findall(without_comments(sources("overflow-issue-1"))))
        before = len(VELOCITY.findall(without_comments(self.baseline)))
        self.assertGreater(after, before)

    def test_tests_are_excluded_from_the_sources(self):
        """`test('brickHit: no overlap returns empty array')` talks about overlap
        without implementing anything."""
        self.assertNotIn(".test.js", str(sorted((FIXTURES / "baseline" / "game").glob("*.js"))))
        for path in (FIXTURES / "baseline" / "game").glob("*.test.js"):
            self.assertNotIn(path.read_text()[:80], self.baseline)


class TestHelpers(unittest.TestCase):
    def test_every_signature_has_a_valid_direction(self):
        for s in SIGNATURES:
            self.assertIn(s.direction, ("up", "down"), s.issue)

    def test_every_signature_describes_itself(self):
        """The description ends up in a failure message and in a published table."""
        for s in SIGNATURES:
            self.assertTrue(s.what.strip(), s.issue)

    def test_without_comments_strips_both_forms(self):
        source = "a\n// line\n/* block\nstill block */\nb"
        stripped = without_comments(source)
        self.assertIn("a", stripped)
        self.assertIn("b", stripped)
        self.assertNotIn("line", stripped)
        self.assertNotIn("still block", stripped)

    def test_compound_velocity_assignment_counts(self):
        """`ball.vy *= -1` is a bounce as surely as an explicit assignment."""
        for form in ("ball.vy = 1", "ball.vy *= -1", "ball.vx += 2", "ball.vy -= 1"):
            self.assertTrue(VELOCITY.search(form), form)

    def test_a_palette_lookup_is_not_a_hardcoded_colour(self):
        self.assertTrue(HARDCODED_COLOUR.search("'#ff00aa'"))
        self.assertFalse(HARDCODED_COLOUR.search("theme.colours.brick"))

    def test_missing_game_directory_yields_nothing(self):
        self.assertEqual(game_sources(FIXTURES / "does-not-exist"), "")


class TestFixturesAreReal(unittest.TestCase):
    """The fixtures' value comes from being real final states, not inventions."""

    def test_each_fixture_has_game_sources(self):
        for name in ("baseline", "clean", "overflow-issue-1", "overflow-issue-6"):
            with self.subTest(fixture=name):
                self.assertTrue(sources(name).strip(), f"{name} has no sources")

    def test_the_baseline_already_contains_the_wall_bounce_idiom(self):
        """Which is why discarding already-present lines destroys the signal."""
        self.assertTrue(VELOCITY.search(without_comments(sources("baseline"))))


if __name__ == "__main__":
    unittest.main()
