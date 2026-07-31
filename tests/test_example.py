"""The shipped example validator, run for real against the shipped fixture.

Three things at once, and that is what makes it cheap: the fixture the base's own tests
need, the worked example the documentation points at, and the proof that the base is
usable. Because CI runs it, the example cannot rot the way a snippet in a document does -
which is the failure mode of every documented example anywhere.

It is what replaced the example repository trysquare used to carry in its own sources:
that was somebody else's project, and nothing here depends on it.
"""

import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.gitrepo import a_repo
from trysquare import repo, validation
from trysquare.scenario import Validator

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "validator.py"
FIXTURE = ROOT / "tests" / "fixtures" / "tiny"

# The suite the fixture declares, written as a scenario writes it: a string. `python3`
# rather than `sys.executable`, because a scenario must carry no machine path - and a
# trivial suite runs under any Python.
TEST_COMMAND = "python3 -m unittest discover -s tests -t ."

# What running that suite leaves behind, as `examples/scenario.toml` declares it. Kept
# here too, and pinned against the scenario below, so the example and the scenario it
# is scored under cannot drift apart.
ARTEFACTS = ("__pycache__",)


def fixture_files() -> dict[str, str]:
    """The versioned fixture, as a mapping the git helper can commit.

    Only what is versioned. The fixture is importable Python that carries its own
    suite, so anything that imports it - an IDE discovering tests, a stray `pytest`
    aimed here - drops a `__pycache__` beside the sources. Swept up by the glob, the
    bytecode becomes a file the measured agent is judged on, and the example scores
    against a tree nobody wrote. The failure is silent and it is not hypothetical:
    it cost ten tests once.
    """
    return {
        str(path.relative_to(FIXTURE)): path.read_text()
        for path in sorted(FIXTURE.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.fixture
def a_measured_run(tmp_path):
    """A clone standing for one finished run, plus the context a validator is handed.

    Built from the versioned fixture: `git init` on a copy, so the two `git` primitives
    the base uses are exercised for real rather than faked.
    """

    runs = itertools.count()

    def make(
        change: dict | None = None,
        response: str | None = None,
        command: str | None = None,
    ) -> Path:
        where = tmp_path / f"run{next(runs)}"
        where.mkdir()
        source = a_repo(fixture_files())

        clone = where / "clone"
        shutil.copytree(source, clone)
        for name, text in (change or {}).items():
            (clone / name).parent.mkdir(parents=True, exist_ok=True)
            (clone / name).write_text(text)

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
            artefacts=list(ARTEFACTS),
            touched=repo.changed_files(clone),
            files=repo.etalon_files(source, "etalon-v1"),
            declared=("delivered", "in_scope", "tests", "touched", "documented"),
            response_file=_prose(where, response) if response is not None else None,
        )

    return make


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


class TestTheExampleScoresARun:
    def test_a_clean_fix_inside_scope(self, a_measured_run):
        payload = score(
            a_measured_run(
                {"counter.py": "def total(items):\n    return sum(items)  # tidied\n"},
                response="It adds up the basket. " * 10,
            )
        )
        assert payload["metrics"]["delivered"] is True
        assert payload["metrics"]["in_scope"] is True
        assert payload["metrics"]["tests"] is True
        assert payload["metrics"]["touched"] == ["counter.py"]

    def test_a_fix_that_reached_outside_says_where(self, a_measured_run):
        payload = score(
            a_measured_run(
                {
                    "counter.py": "def total(items):\n    return sum(items)\n",
                    "notes.md": "I also left a note.\n",
                },
                response="short",
            )
        )
        assert payload["metrics"]["in_scope"] is False
        assert "notes.md" in payload["reasons"]["in_scope"]

    def test_a_suite_that_cannot_run_is_unjudged_and_the_rest_still_scores(self, a_measured_run):
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
        payload = score(a_measured_run({"counter.py": "x = 1\n"}, command="/nowhere/runner"))
        assert "tests" not in payload["metrics"]
        assert "tests" in payload["unjudged"]
        assert payload["metrics"]["delivered"] is True

    def test_a_broken_fix_names_the_test_that_broke(self, a_measured_run):
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
        assert payload["metrics"]["tests"] is False
        assert "test_it_adds_up" in payload["reasons"]["tests"]

    def test_an_agent_that_did_nothing_is_not_a_perfect_score(self, a_measured_run):
        """The metric that exists because a run which changed nothing consumes tokens,
        passes the tests by construction, and overflows nothing."""
        payload = score(a_measured_run(response="I did nothing at all. " * 5))
        assert payload["metrics"]["delivered"] is False
        assert payload["metrics"]["touched"] == []

    def test_a_touched_set_comes_out_sorted(self, a_measured_run):
        payload = score(
            a_measured_run(
                {"counter.py": "x = 1\n", "z.py": "y = 2\n", "a.py": "z = 3\n"},
                response="everywhere " * 30,
            )
        )
        assert payload["metrics"]["touched"] == ["a.py", "counter.py", "z.py"]


class TestTheExampleDegradesHonestly:
    def test_a_metric_it_cannot_answer_leaves_the_metrics(self, a_measured_run):
        """No prose archived, so `documented` is unjudged - and every other metric of the
        same run still scores. This is the shape a re-scoring takes."""
        payload = score(a_measured_run({"counter.py": "x = 1\n"}))
        assert "documented" not in payload["metrics"]
        assert "documented" in payload["unjudged"]
        assert payload["metrics"]["delivered"] is True

    def test_the_reason_names_what_was_missing(self, a_measured_run):
        payload = score(a_measured_run({"counter.py": "x = 1\n"}))
        assert "response" in payload["unjudged"]["documented"]

    def test_a_short_answer_is_scored_rather_than_unjudged(self, a_measured_run):
        """The distinction the whole base is built around: a bad answer and no answer are
        different facts."""
        payload = score(a_measured_run({"counter.py": "x = 1\n"}, response="terse"))
        assert payload["metrics"]["documented"] is False
        assert "documented" not in payload["unjudged"]


class TestByProductsAreNotTheAgentsWork:
    """Found on a real matrix, and it saturated the criterion.

    Every one of six runs scored `in_scope: false`, and the only thing outside scope was
    `__pycache__/*.pyc` - dropped by the agent running the declared suite to check its own
    fix. So the gap the matrix existed to measure was `+0 pts` in both cells, and six paid
    runs concluded nothing.

    Worse than noise, because it is not random: the runs that scored out of scope are the
    ones where the agent bothered to verify itself. A second matrix, whose agents did not
    run the suite, scored 3/3 on the same code.

    The suite cannot see this on its own - `fixture_files` excludes `__pycache__` so the
    versioned fixture stays clean - which is why it took a real provider to find.
    """

    def a_fix(self) -> str:
        return "def total(items):\n    return sum(items)\n"

    def test_a_pyc_left_by_running_the_suite_is_not_out_of_scope(self, a_measured_run):
        payload = score(
            a_measured_run(
                {
                    "counter.py": self.a_fix(),
                    "__pycache__/counter.cpython-314.pyc": "bytecode",
                    "tests/__pycache__/test_counter.cpython-314.pyc": "bytecode",
                }
            )
        )
        assert payload["metrics"]["in_scope"] is True
        assert payload["metrics"]["delivered"] is True

    def test_the_by_products_are_still_recorded(self, a_measured_run):
        """Filtered out of the verdict, not out of the archive. Hiding a measurement is
        the other dishonesty this harness keeps refusing."""
        payload = score(
            a_measured_run(
                {"counter.py": self.a_fix(), "__pycache__/counter.cpython-314.pyc": "bytecode"}
            )
        )
        assert "__pycache__/counter.cpython-314.pyc" in payload["metrics"]["touched"]

    def test_an_agent_that_only_ran_the_suite_delivered_nothing(self, a_measured_run):
        """The same hole in the other metric: bytecode is not a delivery."""
        payload = score(a_measured_run({"__pycache__/counter.cpython-314.pyc": "bytecode"}))
        assert payload["metrics"]["delivered"] is False

    def test_a_real_file_outside_scope_is_still_out_of_scope(self, a_measured_run):
        """The filter must not become a way of scoring nothing."""
        payload = score(
            a_measured_run(
                {
                    "counter.py": self.a_fix(),
                    "__pycache__/counter.cpython-314.pyc": "bytecode",
                    "README.md": "and a doc nobody asked for\n",
                }
            )
        )
        assert payload["metrics"]["in_scope"] is False
        assert "README.md" in payload["reasons"]["in_scope"]
        assert "__pycache__" not in payload["reasons"]["in_scope"]

    def test_the_shipped_scenario_declares_what_the_example_assumes(self):
        """The example is scored under `examples/scenario.toml`. If the scenario stopped
        declaring the by-products of its own suite, every test above would still pass and
        the shipped pair would be broken."""
        from trysquare.scenario import load

        scenario = load(str(ROOT / "examples" / "scenario.toml"))
        assert tuple(scenario.task["artefacts"]) == ARTEFACTS


class TestTheExampleIsSmallEnoughToBeAnExample:
    def test_it_is_runnable_by_hand(self):
        """The contract is "any executable", so a reader must be able to try it."""
        done = subprocess.run([sys.executable, str(EXAMPLE)], capture_output=True, text=True)
        assert done.returncode == 2
        assert "context.json" in done.stderr

    def test_the_scoring_function_is_short(self):
        """Not style policing: if the example needs fifty lines, the base failed and this
        is the ticket that was supposed to find out."""
        source = EXAMPLE.read_text().split("\n")
        start = next(i for i, line in enumerate(source) if line.startswith("def evaluate"))
        end = next(
            i for i, line in enumerate(source[start + 1 :], start + 1) if line.startswith("def ")
        )
        code = [
            line for line in source[start:end] if line.strip() and not line.strip().startswith("#")
        ]
        assert len(code) < 25, "\n".join(code)

    def test_the_context_it_reads_is_the_one_the_harness_writes(self, a_measured_run):
        """Read through `Assay`, so nothing here reimplements the context's shape."""
        context = json.loads(a_measured_run({"counter.py": "x = 1\n"}).read_text())
        for key in ("repo", "etalon", "touched", "files", "test_command", "declared"):
            assert key in context
