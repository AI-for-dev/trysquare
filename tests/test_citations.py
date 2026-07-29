"""The mechanical citation scorer.

It exists because an LLM judge could not carry this criterion: `note_usable`
saturated at 10/10 once the task got easier, and all three of the judge's metrics
became identical in 40 runs out of 40 - it decided "good note" once and answered
everything alike. On the narrower task it had discriminated, so the halo was induced
by the task rather than fixed.

A counter has no ceiling, no halo, and no opinion.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validators"))

from citations import score  # noqa: E402

TRACKED = [
    "ISSUES.md",
    "README.md",
    "game/bloom.js",
    "game/index.html",
    "game/neon.js",
    "game/neon.test.js",
    "game/theme.js",
    "package.json",
    "serve.js",
]


class TestRealCitations(unittest.TestCase):
    def test_a_full_path_counts_as_real_and_exact(self):
        m, _ = score("The change lands in game/neon.js around brickHit.", TRACKED)
        self.assertEqual(m["cited_paths"], 1)
        self.assertEqual(m["exact_paths"], 1)
        self.assertEqual(m["bogus_paths"], 0)

    def test_a_bare_basename_counts_as_real_but_not_exact(self):
        """"neon.js" is specific enough to open the file, which is the point."""
        m, _ = score("The palette lives in theme.js.", TRACKED)
        self.assertEqual(m["cited_paths"], 1)
        self.assertEqual(m["exact_paths"], 0)

    def test_distinct_files_are_counted_once_each(self):
        note = "game/neon.js calls game/theme.js, and game/neon.js also draws."
        m, _ = score(note, TRACKED)
        self.assertEqual(m["cited_paths"], 2)

    def test_backticks_and_punctuation_are_stripped(self):
        m, _ = score("see `game/theme.js`, then (serve.js).", TRACKED)
        self.assertEqual(m["cited_paths"], 2)

    def test_a_leading_dot_slash_still_matches(self):
        m, _ = score("./game/bloom.js consumes the theme", TRACKED)
        self.assertEqual(m["cited_paths"], 1)


class TestBogusCitations(unittest.TestCase):
    """A note inventing a path is worse than one citing none.

    A boolean "does it cite paths" scores those two identically, which is why this
    validator counts rather than judges.
    """

    def test_an_invented_path_is_counted_as_bogus(self):
        m, r = score("The score logic is in game/score.js.", TRACKED)
        self.assertEqual(m["cited_paths"], 0)
        self.assertEqual(m["bogus_paths"], 1)
        self.assertIn("game/score.js", r["bogus_paths"])

    def test_real_and_bogus_are_counted_separately(self):
        m, _ = score("game/neon.js and game/score.js both matter.", TRACKED)
        self.assertEqual(m["cited_paths"], 1)
        self.assertEqual(m["bogus_paths"], 1)

    def test_a_wrong_directory_is_bogus(self):
        """`src/neon.js` is not `game/neon.js`.

        A qualified path that does not exist is an invented path, not a citation:
        resolving it by basename would reward a note that guessed the layout instead
        of reading it. A *bare* `neon.js` is different - it claims no directory - and
        is covered by `test_a_bare_basename_counts_as_real_but_not_exact`.
        """
        m, _ = score("src/neon.js holds the loop.", TRACKED)
        self.assertEqual(m["cited_paths"], 0)
        self.assertEqual(m["exact_paths"], 0)
        self.assertEqual(m["bogus_paths"], 1)


class TestNoCitations(unittest.TestCase):
    def test_prose_without_paths_scores_zero(self):
        note = "I will refactor the collision logic and improve the rendering loop."
        m, r = score(note, TRACKED)
        self.assertEqual(m["cited_paths"], 0)
        self.assertEqual(m["bogus_paths"], 0)
        self.assertFalse(m["cites_paths"])
        self.assertIn("no real file cited", r["cited_paths"])

    def test_an_empty_note_scores_zero_rather_than_failing(self):
        m, _ = score("", TRACKED)
        self.assertEqual(m["cited_paths"], 0)
        self.assertFalse(m["cites_paths"])


class TestMetricShape(unittest.TestCase):
    """The types decide the aggregation, so they have to be right."""

    def test_counts_are_numbers_and_the_flag_is_a_boolean(self):
        m, _ = score("game/neon.js", TRACKED)
        for key in ("cited_paths", "exact_paths", "bogus_paths"):
            self.assertIsInstance(m[key], int, key)
            self.assertNotIsInstance(m[key], bool, f"{key} must aggregate as a median")
        self.assertIsInstance(m["cites_paths"], bool)

    def test_every_metric_is_always_present(self):
        """A missing declared metric invalidates a run, so none may be conditional."""
        for note in ("", "prose only", "game/neon.js", "game/ghost.js"):
            m, _ = score(note, TRACKED)
            self.assertEqual(
                set(m), {"cited_paths", "exact_paths", "bogus_paths", "cites_paths"}
            )


if __name__ == "__main__":
    unittest.main()
