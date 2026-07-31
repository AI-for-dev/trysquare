"""What the bar must never do: change the bytes, or invent a number.

Two properties carry the feature. Off a terminal the output is exactly what it was
before the bar existed, which is what keeps every assertion in `test_cli.py` true.
And the estimate is arithmetic on what has already happened, never a guess dressed
up as one.
"""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from trysquare.progress import OFF, Bar, bar, clock, eta_seconds, wanted


class Terminal(io.StringIO):
    """A stream that claims to be one."""

    def isatty(self) -> bool:
        return True


class TestOffATerminal(unittest.TestCase):
    """A pipe, a redirect or a test must get the bytes it always got."""

    def test_a_line_is_what_print_would_have_written(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with bar(60, "runs", enabled=False) as reporter:
                reporter.line("  ok  rule / off            412s")
        self.assertEqual(buffer.getvalue(), "  ok  rule / off            412s\n")

    def test_square_brackets_survive(self):
        """A validator's `detail` is not rich markup, and losing it loses the record."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            Bar().line("  !! empty: no productive attempt [attempt 3]")
        self.assertIn("[attempt 3]", buffer.getvalue())

    def test_a_warning_still_goes_to_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            Bar().warn("  !! r1/session.jsonl: unreadable")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("unreadable", err.getvalue())

    def test_nothing_to_count_draws_nothing(self):
        with bar(0, "runs", enabled=True) as reporter:
            self.assertFalse(reporter.enabled)

    def test_ticking_past_the_total_does_not_raise(self):
        """A resumed matrix can fire its callback more often than it planned to."""
        with bar(2, "runs", enabled=False) as reporter:
            for _ in range(5):
                reporter.tick()


class TestWhenABarIsWanted(unittest.TestCase):
    def test_not_when_stdout_is_not_a_terminal(self):
        self.assertFalse(wanted(io.StringIO()))

    def test_yes_on_a_terminal(self):
        with patch.dict(os.environ, {"TERM": "xterm"}, clear=True):
            self.assertTrue(wanted(Terminal()))

    def test_not_when_the_flag_says_so(self):
        with patch.dict(os.environ, {"TERM": "xterm"}, clear=True):
            self.assertFalse(wanted(Terminal(), no_progress=True))

    def test_not_when_the_environment_says_so(self):
        with patch.dict(os.environ, {"TERM": "xterm", OFF: "1"}, clear=True):
            self.assertFalse(wanted(Terminal()))

    def test_not_on_a_dumb_terminal(self):
        with patch.dict(os.environ, {"TERM": "dumb"}, clear=True):
            self.assertFalse(wanted(Terminal()))

    def test_no_color_is_not_an_off_switch(self):
        """It asks for no colour, not for no motion. Pinned so it stays deliberate."""
        with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}, clear=True):
            self.assertTrue(wanted(Terminal()))

    def test_a_closed_stream_is_not_a_terminal(self):
        stream = io.StringIO()
        stream.close()
        self.assertFalse(wanted(stream))


class TestTheEstimate(unittest.TestCase):
    """Throughput since launch, and silence until there is any."""

    def test_nothing_is_claimed_before_the_first_run_lands(self):
        self.assertIsNone(eta_seconds(0, 60, 10))
        self.assertIsNone(eta_seconds(1, 60, 0))
        self.assertIsNone(eta_seconds(1, 0, 10))

    def test_a_saturated_pool_is_estimated_exactly(self):
        """Five ten-minute runs done out of thirty-two, five at a time: 27 x 600/5."""
        self.assertEqual(eta_seconds(5, 32, 600), 3240)

    def test_the_estimate_falls_as_the_matrix_advances(self):
        self.assertEqual(eta_seconds(20, 60, 600), 1200)
        self.assertEqual(eta_seconds(40, 60, 1200), 600)
        self.assertEqual(eta_seconds(60, 60, 1800), 0)

    def test_a_matrix_reads_in_hours(self):
        self.assertEqual(clock(3660), "1h 01m")
        self.assertEqual(clock(750), "12m 30s")
        self.assertEqual(clock(45), "45s")


if __name__ == "__main__":
    unittest.main()
