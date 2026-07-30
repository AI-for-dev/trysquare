"""The shipped example validator, run for real against the shipped fixture.

Three things at once, and that is what makes it cheap: the fixture the base's own tests
need, the worked example the documentation points at, and the proof that the base is
usable. Because CI runs it, the example cannot rot the way a snippet in a document does -
which is the failure mode of every documented example anywhere.

It also replaces what NEON used to prove. NEON is an example and is leaving trysquare's
sources; nothing here depends on it.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.gitrepo import a_repo
from trysquare import validation
from trysquare.scenario import Validator

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "validator.py"
FIXTURE = ROOT / "tests" / "fixtures" / "tiny"

# The suite the fixture declares, written as a scenario writes it: a string. `python3`
# rather than `sys.executable`, because a scenario must carry no machine path - and a
# trivial suite runs under any Python.
TEST_COMMAND = "python3 -m unittest discover -s tests -t ."


def fixture_files() -> dict[str, str]:
    """The versioned fixture, as a mapping the git helper can commit."""
    return {
        str(path.relative_to(FIXTURE)): path.read_text()
        for path in sorted(FIXTURE.rglob("*"))
        if path.is_file()
    }


def a_measured_run(
    change: dict | None = None,
    response: str | None = None,
    command: str | None = None,
) -> Path:
    """A clone standing for one finished run, plus the context a validator is handed.

    Built from the versioned fixture: `git init` on a copy, so the two `git` primitives
    the base uses are exercised for real rather than faked.
    """
    where = Path(tempfile.mkdtemp())
    source = a_repo(fixture_files())

    clone = where / "clone"
    shutil.copytree(source, clone)
    for name, text in (change or {}).items():
        (clone / name).parent.mkdir(parents=True, exist_ok=True)
        (clone / name).write_text(text)

    from trysquare import repo

    directory = where / "validation"
    return validation.write_context(
        directory,
        repo=clone,
        etalon="etalon-v1",
        etalon_checkout=source,
        prompt_file=None,
        session_dir=where / "session",
        trace=None,
        cell="none",
        repetition=0,
        test_command=command or TEST_COMMAND,
        touched=repo.changed_files(clone),
        files=repo.etalon_files(source, "etalon-v1"),
        declared=("delivered", "in_scope", "tests", "touched", "documented"),
        response_file=_prose(where, response) if response is not None else None,
    )


def _prose(where: Path, text: str) -> Path:
    path = where / "response.txt"
    path.write_text(text)
    return path


def score(context: Path) -> dict:
    """Runs the example the way the harness would, and returns what it printed."""
    result = validation.run_script(
        Validator(mode="script", metrics=(), config={"command": str(EXAMPLE)}),
        context,
        timeout=120,
    )
    if result.payload is None:
        raise AssertionError(f"{result.detail}\n{result.stderr}")
    return result.payload


class TestTheExampleScoresARun(unittest.TestCase):
    def test_a_clean_fix_inside_scope(self):
        payload = score(
            a_measured_run(
                {"counter.py": "def total(items):\n    return sum(items)  # tidied\n"},
                response="It adds up the basket. " * 10,
            )
        )
        self.assertEqual(payload["metrics"]["delivered"], True)
        self.assertEqual(payload["metrics"]["in_scope"], True)
        self.assertEqual(payload["metrics"]["tests"], True)
        self.assertEqual(payload["metrics"]["touched"], ["counter.py"])

    def test_a_fix_that_reached_outside_says_where(self):
        payload = score(
            a_measured_run(
                {
                    "counter.py": "def total(items):\n    return sum(items)\n",
                    "notes.md": "I also left a note.\n",
                },
                response="short",
            )
        )
        self.assertEqual(payload["metrics"]["in_scope"], False)
        self.assertIn("notes.md", payload["reasons"]["in_scope"])

    def test_a_suite_that_cannot_run_is_unjudged_and_the_rest_still_scores(self):
        """The payoff of the whole design, on a cause that is version-independent.

        This assertion used to ride on a gutted test file, which leaves `unittest discover`
        with nothing to collect. That exits **5** on Python 3.12+ and **0** on 3.11, where
        `NO_TESTS_RAN` did not yet exist - so the same fixture scored unjudged on one
        interpreter and green on another, and CI caught it. The hole is real and it is the
        runner's, not ours: on 3.11 "no test collected" is indistinguishable from "all
        green", which is exactly the silence-read-as-success this tool is built against.

        Pointing `test_command` at something that cannot run says the same thing on every
        version.
        """
        payload = score(
            a_measured_run({"counter.py": "x = 1\n"}, command="/nowhere/runner")
        )
        self.assertNotIn("tests", payload["metrics"])
        self.assertIn("tests", payload["unjudged"])
        self.assertEqual(payload["metrics"]["delivered"], True)

    def test_a_broken_fix_names_the_test_that_broke(self):
        """`unittest` is the one runner where the **fallback** is the better answer: it
        prints its summary line *after* the failure detail, so anchoring on a marker would
        cut off the very thing a reader needs. The generous tail keeps it, and says it is a
        fallback - which is what the shipped validator never did."""
        payload = score(
            a_measured_run(
                {"counter.py": "def total(items):\n    return 0\n"},
                response="I broke it. " * 10,
            )
        )
        self.assertEqual(payload["metrics"]["tests"], False)
        self.assertIn("test_it_adds_up", payload["reasons"]["tests"])

    def test_an_agent_that_did_nothing_is_not_a_perfect_score(self):
        """The metric that exists because a run which changed nothing consumes tokens,
        passes the tests by construction, and overflows nothing."""
        payload = score(a_measured_run(response="I did nothing at all. " * 5))
        self.assertEqual(payload["metrics"]["delivered"], False)
        self.assertEqual(payload["metrics"]["touched"], [])

    def test_a_touched_set_comes_out_sorted(self):
        payload = score(
            a_measured_run(
                {"counter.py": "x = 1\n", "z.py": "y = 2\n", "a.py": "z = 3\n"},
                response="everywhere " * 30,
            )
        )
        self.assertEqual(payload["metrics"]["touched"], ["a.py", "counter.py", "z.py"])


class TestTheExampleDegradesHonestly(unittest.TestCase):
    def test_a_metric_it_cannot_answer_leaves_the_metrics(self):
        """No prose archived, so `documented` is unjudged - and every other metric of the
        same run still scores. This is the shape a re-scoring takes."""
        payload = score(a_measured_run({"counter.py": "x = 1\n"}))
        self.assertNotIn("documented", payload["metrics"])
        self.assertIn("documented", payload["unjudged"])
        self.assertEqual(payload["metrics"]["delivered"], True)

    def test_the_reason_names_what_was_missing(self):
        payload = score(a_measured_run({"counter.py": "x = 1\n"}))
        self.assertIn("response", payload["unjudged"]["documented"])

    def test_a_short_answer_is_scored_rather_than_unjudged(self):
        """The distinction the whole base is built around: a bad answer and no answer are
        different facts."""
        payload = score(a_measured_run({"counter.py": "x = 1\n"}, response="terse"))
        self.assertEqual(payload["metrics"]["documented"], False)
        self.assertNotIn("documented", payload["unjudged"])


class TestTheExampleIsSmallEnoughToBeAnExample(unittest.TestCase):
    def test_it_is_runnable_by_hand(self):
        """The contract is "any executable", so a reader must be able to try it."""
        done = subprocess.run(
            [sys.executable, str(EXAMPLE)], capture_output=True, text=True
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("context.json", done.stderr)

    def test_the_scoring_function_is_short(self):
        """Not style policing: if the example needs fifty lines, the base failed and this
        is the ticket that was supposed to find out."""
        source = EXAMPLE.read_text().split("\n")
        start = next(i for i, line in enumerate(source) if line.startswith("def evaluate"))
        end = next(i for i, line in enumerate(source[start + 1 :], start + 1) if line.startswith("def "))
        code = [
            line
            for line in source[start:end]
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertLess(len(code), 25, "\n".join(code))

    def test_the_context_it_reads_is_the_one_the_harness_writes(self):
        """Read through `Assay`, so nothing here reimplements the context's shape."""
        context = json.loads(a_measured_run({"counter.py": "x = 1\n"}).read_text())
        for key in ("repo", "etalon", "touched", "files", "test_command", "declared"):
            self.assertIn(key, context)


if __name__ == "__main__":
    unittest.main()
