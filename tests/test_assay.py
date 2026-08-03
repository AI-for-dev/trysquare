"""The base a validator is written with, and the three states it can express.

Every test here stands for a mistake that has already been made once, in a shipped
validator, and cost either a matrix or a reader's afternoon. The references in the
docstrings are to those originals.
"""

import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.gitrepo import a_repo
import pytest

from trysquare import assay
from trysquare.assay import Assay, CannotJudge, Metric, ProbeTimeout


class TestMetric:
    def test_a_metric_reads_as_its_value(self):
        """`if not run.tests():` has to work, or every call site grows a `.value`."""
        assert not Metric(False, "1 failure")
        assert Metric(True)
        assert not Metric(0)
        assert Metric(7)

    def test_a_bare_value_needs_no_ceremony(self):
        """Most metrics carry no reason, and wrapping them would be noise."""
        assert assay.report({"delivered": True})["metrics"] == {"delivered": True}

    def test_a_reason_is_published_on_a_success_too(self):
        """`citations.py:90-94` puts one on success ("cites a.js, b.js") and it is
        useful. Filtering on failure is not implementable: "failed" is only definable
        for a boolean, and `cited_paths = 7` is neither."""
        out = assay.report({"cited_paths": Metric(7, "cites a.js, b.js")})
        assert out["metrics"]["cited_paths"] == 7
        assert out["reasons"]["cited_paths"] == "cites a.js, b.js"

    def test_a_metric_without_a_reason_adds_no_reason(self):
        assert assay.report({"tests": Metric(True)})["reasons"] == {}


class TestUnjudged:
    """One metric may be unjudgeable while the rest of the run is fine.

    The real case is the probe that could not run: `issue1.py:310-316` returns
    `{"ok": False, "erreur": "pas de src/ dans le clone"}`, which the harness records
    as `par_face = false` - "could not judge" read as "worked badly", the confusion
    this whole project is built against.
    """

    def test_an_unjudged_metric_leaves_the_metrics(self):
        out = assay.report({"red_first": Metric.unjudged("no session archived")})
        assert "red_first" not in out["metrics"]

    def test_its_name_is_still_returned_so_a_typo_stays_loud(self):
        """The harness refuses a declared metric that is absent. Keeping the name in
        `unjudged` is what lets a real absence (a typo) stay an error while an honest
        "I cannot say" degrades a denominator instead."""
        out = assay.report({"red_first": Metric.unjudged("no session archived")})
        assert out["unjudged"] == {"red_first": "no session archived"}

    def test_the_reason_is_required(self):
        """A denominator that shrank for no stated reason is unreadable six months on."""
        with pytest.raises(ValueError):
            Metric.unjudged("")

    def test_an_unjudged_metric_is_not_a_false_one(self):
        assert Metric.unjudged("no session") != Metric(False, "no session")


class TestSetsSerialiseSorted:
    def test_a_set_becomes_a_sorted_list(self):
        """Not cosmetic. PYTHONHASHSEED is random, so an unsorted set of strings
        serialises differently from one process to the next: two identical
        measurements would produce byte-different `measures.json`, and `compare` would
        read a difference that is not there."""
        out = assay.report({"touched": {"z.js", "a.js", "m.js"}})
        assert out["metrics"]["touched"] == ["a.js", "m.js", "z.js"]

    def test_a_frozenset_too(self):
        out = assay.report({"touched": frozenset({"b", "a"})})
        assert out["metrics"]["touched"] == ["a", "b"]

    def test_the_sort_survives_a_subprocess(self):
        """The property that matters is stability across processes, which is exactly
        what a single in-process assertion cannot show."""
        code = (
            "from trysquare import assay;"
            "import json;"
            "print(json.dumps(assay.report({'t': {'src/basket.js', 'README.md', 'p.json'}})"
            "['metrics']['t']))"
        )
        runs = {
            subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=True
            ).stdout.strip()
            for _ in range(4)
        }
        assert len(runs) == 1, f"order varied between processes: {runs}"


class TestDeclaredArtefacts:
    """The by-products of running the task, told apart from the agent's work.

    `touched` keeps everything - filtering what a verdict rests on is not licence to hide a
    measurement - so the subtraction belongs to the validator, and this is the set it needs.
    """

    def run(self, touched, artefacts=None):
        context = {"touched": sorted(touched)}
        if artefacts is not None:
            context["artefacts"] = list(artefacts)
        return Assay(context)

    def test_a_named_directory_matches_at_any_depth(self):
        """The common case, and it must not require knowing that `fnmatch` lets `*` cross a
        `/`: that is a detail of Python, not of the experiment."""
        run = self.run(
            {"counter.py", "__pycache__/counter.pyc", "tests/__pycache__/t.pyc"},
            ["__pycache__"],
        )
        assert run.artefacts == {"__pycache__/counter.pyc", "tests/__pycache__/t.pyc"}

    def test_a_glob_matches_the_whole_path(self):
        run = self.run({"counter.py", "__pycache__/counter.pyc"}, ["*.pyc"])
        assert run.artefacts == {"__pycache__/counter.pyc"}

    def test_a_trailing_slash_is_the_same_directory(self):
        """`node_modules/` is how an author writes a directory, and refusing it would be
        pedantry about a character git itself accepts."""
        run = self.run({"a.js", "node_modules/x/y.js"}, ["node_modules/"])
        assert run.artefacts == {"node_modules/x/y.js"}

    def test_touched_is_never_reduced(self):
        run = self.run({"counter.py", "__pycache__/counter.pyc"}, ["__pycache__"])
        assert run.touched == {"counter.py", "__pycache__/counter.pyc"}

    def test_a_scenario_that_declares_nothing_has_no_artefacts(self):
        """A validator written against this keeps working where there is nothing to name,
        and every scenario written before the key existed is such a scenario."""
        run = self.run({"counter.py", "__pycache__/counter.pyc"})
        assert run.artefacts == frozenset()
        assert run.touched - run.artefacts == run.touched

    def test_a_partial_name_does_not_match(self):
        """A filter that over-matches would hide work the agent really did."""
        run = self.run({"cache/data.json", "src/pycache_helper.py"}, ["__pycache__"])
        assert run.artefacts == frozenset()


class TestWhatTheCellWasGiven:
    """A file the harness put in the tree, told apart from one the agent wrote.

    Without it, a probe that is absent from the tree is one fact with two causes -
    never given, or deleted along the way - and a run that removed the test it was
    handed scores like a run that was handed nothing.
    """

    def test_the_paths_a_files_brick_put_in_the_tree(self):
        run = Assay({"given": ["game/probe.test.js"]})
        assert run.given == {"game/probe.test.js"}

    def test_a_cell_handed_nothing_was_given_nothing(self):
        """Most cells, and every scenario written before the key existed."""
        assert Assay({"touched": ["game/neon.js"]}).given == frozenset()

    def test_given_says_nothing_about_touched(self):
        """The two answer different questions: what was handed, and what was changed."""
        run = Assay({"given": ["game/probe.test.js"], "touched": ["game/neon.js"]})
        assert run.given == {"game/probe.test.js"}
        assert run.touched == {"game/neon.js"}

    def test_a_fake_has_to_declare_it(self):
        """Answering "nothing was given" to a validator that never said it read this
        would put the absence of a fact into the shape of a fact."""
        with pytest.raises(CannotJudge):
            Assay.fake(touched=frozenset()).given
        assert Assay.fake(given=["game/probe.test.js"]).given == {"game/probe.test.js"}


class TestTheErrorContract:
    """ "Could not judge" is not "worked badly", and one validator in four got it right.

    Three of four validators called `evaluate(context)` with no net, so a traceback lands in
    `script.stderr` and reads six months later as a broken validator when the cause is
    usually the context.
    """

    def context(self, **extra) -> Path:
        d = Path(tempfile.mkdtemp())
        payload = {"repo": str(d), "etalon": {"tag": "v1", "checkout": str(d)}, **extra}
        path = d / "context.json"
        path.write_text(json.dumps(payload))
        return path

    def run_validator(self, fn, context: Path) -> tuple[int, str, str]:
        out, err = [], []
        code = assay.main(fn, [str(context)], write=out.append, warn=err.append)
        return code, "".join(out), "".join(err)

    def test_a_validator_that_scores_exits_zero_with_json(self):
        code, out, _ = self.run_validator(lambda run: {"delivered": True}, self.context())
        assert code == 0
        assert json.loads(out)["metrics"] == {"delivered": True}

    def test_cannot_judge_exits_one_with_a_sentence(self):
        def evaluate(run):
            raise CannotJudge("no session archived")

        code, out, err = self.run_validator(evaluate, self.context())
        assert code == 1
        assert out == ""
        assert "could not score this run" in err
        assert "no session archived" in err

    def test_any_other_exception_is_caught_too(self):
        """The default has to be right: doing nothing is what most validators did."""

        def evaluate(run):
            raise KeyError("boom")

        code, _, err = self.run_validator(evaluate, self.context())
        assert code == 1
        assert "the validator failed" in err

    def test_the_sentence_comes_before_the_traceback(self):
        """The fix for "a traceback reads as a broken validator" is the order, not
        suppression: the trace costs a real debugging session to throw away, and
        `run_script` archives stderr (`outputs.py:286`) for exactly that."""

        def evaluate(run):
            raise KeyError("boom")

        _, _, err = self.run_validator(evaluate, self.context())
        lines = [line for line in err.split("\n") if line.strip()]
        assert lines[0].startswith("the validator failed"), lines[:2]
        assert "Traceback" in err

    def test_a_cannot_judge_carries_no_traceback(self):
        """It is not a defect, so a trace would only invite reading it as one."""

        def evaluate(run):
            raise CannotJudge("the context names no session")

        _, _, err = self.run_validator(evaluate, self.context())
        assert "Traceback" not in err

    def test_an_unreadable_context_exits_two(self):
        code, _, err = self.run_validator(lambda run: {}, Path("/nowhere/context.json"))
        assert code == 2
        assert "unreadable context" in err

    def test_a_declared_metric_never_returned_is_refused_early(self):
        """The harness refuses it too, but only after the tokens are spent. Naming it
        here says which one is missing, before anything is recorded."""
        context = self.context(declared=["delivered", "tests"])
        code, _, err = self.run_validator(lambda run: {"delivered": True}, context)
        assert code == 1
        assert "tests" in err

    def test_an_extra_metric_is_not_refused(self):
        """`merge` keeps extras on purpose, which is what lets one validator serve
        several scenarios."""
        context = self.context(declared=["delivered"])
        code, out, _ = self.run_validator(lambda run: {"delivered": True, "spare": 3}, context)
        assert code == 0
        assert json.loads(out)["metrics"]["spare"] == 3

    def test_an_unjudged_metric_counts_as_returned(self):
        context = self.context(declared=["delivered", "red_first"])
        code, _, _ = self.run_validator(
            lambda run: {"delivered": True, "red_first": Metric.unjudged("no session")},
            context,
        )
        assert code == 0


class TestTheFakeRefusesToInvent:
    """A fake that answered would put the absence of a measurement in the shape of a
    measurement - an empty set reads as "the agent touched nothing" - which is the
    confusion the error contract exists to prevent, moved into the tests.
    """

    def test_what_the_test_declared_is_readable(self):
        run = Assay.fake(touched={"a.js"})
        assert run.touched == {"a.js"}

    def test_what_it_did_not_declare_raises(self):
        run = Assay.fake(touched={"a.js"})
        with pytest.raises(CannotJudge) as raised:
            run.files_at_etalon
        assert "fake" in str(raised.value).lower()

    def test_the_message_names_what_to_declare(self):
        run = Assay.fake(touched={"a.js"})
        with pytest.raises(CannotJudge) as raised:
            run.response
        assert "response" in str(raised.value)

    def test_a_method_the_test_stubbed_answers(self):
        run = Assay.fake(touched={"a.js"}, tests=Metric(False, "1 failure"))
        assert not run.tests()
        assert run.tests().reason == "1 failure"

    def test_an_unstubbed_method_raises(self):
        run = Assay.fake(touched={"a.js"})
        with pytest.raises(CannotJudge):
            run.tests()

    def test_a_missing_context_key_is_not_an_empty_value(self):
        """An empty set means the agent touched nothing, which is a measurement. A
        missing key means nobody measured, which is not the same fact."""
        run = Assay({"repo": "/r"})
        with pytest.raises(CannotJudge) as raised:
            run.touched
        assert "touched" in str(raised.value)

    def test_a_piece_a_replay_cannot_give_back_says_so(self):
        """The common case, and it used to be told the wrong cause. A replayed context
        never carries the prose, so blaming an old harness sent a reader looking for an
        upgrade that does not exist."""
        run = Assay({"repo": "/r", "touched": []})
        with pytest.raises(CannotJudge) as raised:
            run.response
        said = str(raised.value)
        assert "carries no 'response'" in said
        assert "replayed context" in said
        assert "older than this validator" not in said

    def test_any_other_missing_key_still_suggests_an_older_harness(self):
        """`touched` is written by every run, so its absence really is a stale context -
        and the two diagnoses must not be swapped."""
        run = Assay({"repo": "/r"})
        with pytest.raises(CannotJudge) as raised:
            run.touched
        said = str(raised.value)
        assert "older than this validator" in said
        assert "replayed context" not in said


ETALON = {
    "src/basket.js": "export function step() {}\n// a comment\n",
    "src/theme.js": "export const palette = {};\n",
    "src/basket.test.js": "import {step} from './basket.js';\n",
    "README.md": "# t\n",
}


class TestSourcesAtEtalon:
    """The reference side of any comparison, read **from the tag**.

    A validator that fell back to the checkout's working tree when the harness provides
    one, and `issue1.py:183-195` documents at length why that is wrong: trysquare puts
    the source repository there, whose working tree is on `main`, so the reference
    drifts the moment `main` moves or a classroom fixes the issue in place. Exactly what
    pinning by tag exists to prevent. The base offers only the correct one.
    """

    @pytest.fixture(autouse=True)
    def an_etalon_checkout(self, tmp_path):
        self.source = a_repo(ETALON)
        self.run = Assay(
            {
                "repo": "/unused",
                "etalon": {"tag": "etalon-v1", "checkout": str(self.source)},
                "files": sorted(ETALON),
            }
        )

    def test_a_pattern_selects_and_the_contents_come_from_the_tag(self):
        text = self.run.sources_at_etalon("src/*.js")
        assert "export function step" in text
        assert "export const palette" in text
        assert "# t" not in text

    def test_an_exclusion_removes_what_the_pattern_caught(self):
        text = self.run.sources_at_etalon("src/*.js", exclude="*.test.js")
        assert "from './basket.js'" not in text
        assert "export function step" in text

    def test_the_working_tree_is_never_read(self):
        """The whole point. Moving `main` on must not move the reference."""
        (self.source / "src" / "basket.js").write_text("export function step() { fixed; }\n")
        assert "fixed" not in self.run.sources_at_etalon("src/*.js")

    def test_a_pattern_matching_nothing_is_empty_rather_than_an_error(self):
        assert self.run.sources_at_etalon("src/*.ts") == ""

    def test_a_missing_checkout_refuses_rather_than_guessing(self):
        run = Assay({"etalon": {"tag": "etalon-v1", "checkout": "/nowhere"}, "files": ["a.js"]})
        with pytest.raises(CannotJudge):
            run.sources_at_etalon("*.js")


def session(*calls) -> dict:
    """A context whose archived session contains exactly `calls`.

    Each call is `(tool, arguments)` or `(tool, arguments, failed)`. This is the shape
    the harness archives - `toolCall` blocks and `toolResult` messages - and not the
    raw stream: `test_issue1.py:210-244` built the same helper against the stream,
    which is the file that is *not* archived.
    """
    lines = []
    for i, call in enumerate(calls):
        tool, arguments = call[0], call[1]
        failed = call[2] if len(call) > 2 else False
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": str(i),
                                "name": tool,
                                "arguments": arguments,
                            }
                        ],
                    },
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": str(i),
                        "toolName": tool,
                        "isError": failed,
                        "content": [],
                    },
                }
            )
        )
    d = Path(tempfile.mkdtemp(prefix="assay-session-"))
    (d / "attempt-1.jsonl").write_text("\n".join(lines) + "\n")
    return {"session": str(d)}


TEST_FILE = {"path": "src/basket.test.js"}
SOURCE_FILE = {"path": "src/basket.js"}


class TestToolCalls:
    """Read from the archived session, which is where they were all along.

    `issue1.py:386-450` reads `context["trace"]`, the raw stream, which is deliberately
    not archived. In the session a single record carries the tool name, the id **and**
    `isError`, so the `toolCallId` reconciliation of `:392-416` - the part its own
    ticket called the ugliest in the file - had no cause but reading the wrong file.
    """

    def test_the_calls_come_back_in_order(self):
        run = Assay(session(("read", SOURCE_FILE), ("edit", TEST_FILE)))
        assert [c.name for c in run.tool_calls()] == ["read", "edit"]

    def test_a_failure_is_on_the_call_itself(self):
        run = Assay(session(("edit", {}, True), ("edit", TEST_FILE)))
        assert [c.failed for c in run.tool_calls()] == [True, False]

    def test_the_first_write_is_found_by_path(self):
        run = Assay(session(("read", SOURCE_FILE), ("edit", TEST_FILE)))
        assert run.first_write("src/basket.test.js") == 1

    def test_a_failed_call_is_not_a_write(self):
        """`pi` rejected two `edit` calls with no `path` on a real run. Counting them
        would date the work before it happened."""
        run = Assay(session(("edit", {}, True), ("edit", TEST_FILE)))
        assert run.first_write("src/basket.test.js") == 1

    def test_nothing_written_is_none_rather_than_an_error(self):
        run = Assay(session(("read", SOURCE_FILE)))
        assert run.first_write("src/basket.js") is None

    def test_a_shell_redirection_counts_as_a_write(self):
        run = Assay(session(("bash", {"command": "cat > src/basket.test.js <<'EOF'"})))
        assert run.first_write("src/basket.test.js") == 0

    def test_a_test_file_is_not_the_source_file(self):
        """`src/basket.test.js` ends with `basket.test.js`, never with `basket.js`."""
        run = Assay(session(("edit", TEST_FILE)))
        assert run.first_write("src/basket.js") is None

    def test_an_unknown_tool_refuses_rather_than_answering_no(self):
        """The list of writing tools ages with `pi`, not with trysquare, so it has to
        age loudly: a new writing tool would otherwise make a process metric quietly
        false, and a lower column reads as a less disciplined agent."""
        run = Assay(session(("read", SOURCE_FILE), ("sonar", {"path": "a.js"})))
        with pytest.raises(CannotJudge) as raised:
            run.first_write("a.js")
        assert "sonar" in str(raised.value)

    def test_a_subagent_refuses_because_it_writes_without_saying_where(self):
        run = Assay(session(("subagent", {"agent": "coder", "task": "fix it"})))
        with pytest.raises(CannotJudge) as raised:
            run.first_write("a.js")
        assert "subagent" in str(raised.value)

    def test_an_absent_session_refuses(self):
        """The session comes from the harness, so its absence is an inability to judge
        and never a statement about the agent."""
        run = Assay({"session": "/nowhere"})
        with pytest.raises(CannotJudge):
            run.tool_calls()


def a_runner(directory: Path, code: int, out: str = "", err: str = "") -> str:
    """A command standing for a test runner: a chosen exit code and a chosen output.

    A **string**, because that is what a scenario writes and what the context carries.
    `shlex.join` rather than a bare join, so a temporary path holding a space still produces
    one word.
    """
    script = directory / "runner.py"
    script.write_text(
        f"import sys\nsys.stdout.write({out!r})\nsys.stderr.write({err!r})\nsys.exit({code})\n"
    )
    return shlex.join([sys.executable, str(script)])


NODE_SPEC = (
    "✔ bounces off the wall (0.25ms)\n"
    "✖ bounces off a brick (0.26ms)\n"
    "ℹ tests 2\n"
    "ℹ fail 1\n"
    "\n"
    "✖ failing tests:\n"
    "test at src/basket.test.js:12:1\n"
    "✖ bounces off a brick (0.26ms)\n"
    "  AssertionError: expected -300 to equal 300\n"
)


class TestTheDeclaredSuite:
    """Three outcomes, not two, and a reason that is the runner's own summary.

    The shipped validator greps for `not ok`, which stopped matching in **Node v23.0.0**
    when the default non-TTY reporter went from `tap` to `spec` (nodejs/node#54548, "This
    is a breaking change"). Its silent `tail[-1]` fallback then returned a closing brace
    as the reason for a genuinely failing suite, and nothing looked broken. A fallback that
    does not say it is one is what let that survive two major versions.
    """

    def run_with(self, code: int, out: str = "", err: str = "", **extra):
        d = Path(tempfile.mkdtemp())
        context = {"repo": str(d), "test_command": a_runner(d, code, out, err), **extra}
        return Assay(context).tests()

    def test_a_green_suite(self):
        assert self.run_with(0, "ℹ pass 2\n")

    def test_a_red_suite_is_false_rather_than_unjudged(self):
        """A failing suite is a measurement, and the agent's own."""
        result = self.run_with(1, NODE_SPEC)
        assert not result
        assert result.judged

    def test_the_reason_is_the_runners_own_summary(self):
        reason = self.run_with(1, NODE_SPEC).reason
        assert "bounces off a brick" in reason
        assert "expected -300 to equal 300" in reason

    def test_the_reason_of_a_spec_report_is_not_a_closing_brace(self):
        """The exact regression: the old grep found nothing and fell back to the last
        line, which for a spec report is the tail of a diff."""
        assert self.run_with(1, NODE_SPEC).reason.strip() != "}"

    def test_a_pytest_summary_is_found_too(self):
        out = "=== short test summary info ===\nFAILED test_a.py::test_one - assert 1 == 2\n"
        assert "test_one" in self.run_with(1, out).reason

    def test_a_report_with_no_known_marker_says_it_fell_back(self):
        """A reason that does not declare itself a fallback is what hid the Node change."""
        reason = self.run_with(1, "something nobody recognises\n").reason
        assert "no recognised summary" in reason
        assert "something nobody recognises" in reason

    def test_output_on_stderr_is_not_a_failing_suite(self):
        """`npm` writes a notice to stderr on a perfectly green run. Reading stderr's
        presence as evidence is what put `npm notice run node --test` in a reason."""
        result = self.run_with(0, "ℹ pass 2\n", err="npm notice run node --test\n")
        assert result
        assert "npm notice" not in result.reason

    def test_an_executable_that_is_not_there_cannot_judge(self, tmp_path):
        d = tmp_path
        run = Assay({"repo": str(d), "test_command": "/nowhere/runner --test"})
        with pytest.raises(CannotJudge):
            run.tests()

    def test_a_missing_npm_script_cannot_judge(self):
        """`npm error Missing script: "test"` exits 1, exactly like a failing suite, and
        it means nobody ran anything."""
        with pytest.raises(CannotJudge):
            self.run_with(1, err='npm error Missing script: "test"\n')

    def test_pytest_collecting_nothing_cannot_judge(self):
        """Exit 5 is "no test collected", which is not a green suite and not a red one.
        `unittest discover` over a pytest test prints `Ran 0 tests`, `OK`, and exits 0 -
        the same hole, silent."""
        with pytest.raises(CannotJudge):
            self.run_with(5)

    def test_pytest_misused_cannot_judge(self):
        with pytest.raises(CannotJudge):
            self.run_with(4)

    def test_node_failing_to_load_a_reporter_cannot_judge(self):
        """Exit 7 is `ERR_MODULE_NOT_FOUND`: an inability to judge dressed as a failure."""
        with pytest.raises(CannotJudge):
            self.run_with(7)

    def test_a_suite_that_never_ends_cannot_judge(self, tmp_path):
        d = tmp_path
        script = d / "hang.py"
        script.write_text("import time\ntime.sleep(30)\n")
        run = Assay({"repo": str(d), "test_command": shlex.join([sys.executable, str(script)])})
        with pytest.raises(CannotJudge) as raised:
            run.tests(timeout=1)
        assert "timed out" in str(raised.value)

    def test_a_scenario_naming_no_suite_cannot_judge(self):
        """An absent `test_command` says this experiment scores no suite, which is
        something to refuse over rather than to score as a failure."""
        run = Assay({"repo": "/r"})
        with pytest.raises(CannotJudge) as raised:
            run.tests()
        assert "test_command" in str(raised.value)

    def test_a_preparation_step_runs_before_the_suite(self, tmp_path):
        d = tmp_path
        run = Assay(
            {
                "repo": str(d),
                "prepare": [a_runner(d, 0, "installed\n")],
                "test_command": shlex.join([sys.executable, "-c", "print('ℹ pass 1')"]),
            }
        )
        assert run.tests()

    def test_a_preparation_step_that_fails_cannot_judge(self, tmp_path):
        """Not a red suite: nobody ran it. No network, or a dependency that will not
        install, would otherwise score the agent red on a column that can carry the
        scenario's validity condition."""
        d = tmp_path
        run = Assay(
            {
                "repo": str(d),
                "prepare": [a_runner(d, 1, "", "npm ERR! network unreachable\n")],
                "test_command": shlex.join([sys.executable, "-c", "print('ℹ pass 1')"]),
            }
        )
        with pytest.raises(CannotJudge) as raised:
            run.tests()
        assert "before the suite ran" in str(raised.value)

    def test_the_suite_does_not_run_when_preparation_failed(self, tmp_path):
        """Otherwise a green suite could paper over a failed install."""
        d = tmp_path
        witness = d / "ran"
        run = Assay(
            {
                "repo": str(d),
                "prepare": [a_runner(d, 1)],
                "test_command": shlex.join(
                    [sys.executable, "-c", f"open({str(witness)!r}, 'w').close()"]
                ),
            }
        )
        with pytest.raises(CannotJudge):
            run.tests()
        assert not witness.exists()

    def test_the_command_is_run_as_an_argv_without_a_shell(self, tmp_path):
        """No shell, so a scenario cannot smuggle a redirection past the declaration."""
        d = tmp_path
        run = Assay({"repo": str(d), "test_command": "echo 'hi > stolen.txt'"})
        assert run.tests()
        assert not (d / "stolen.txt").exists()


class TestTheProbe:
    """A criterion that is a behaviour executes instead of being recognised.

    No pattern in the diff, no judge, no tokens, and a wrong answer is an assertion that
    breaks. The generic half is here; the probe's text and its cases are the domain's.
    """

    def tree(self, **files) -> Assay:
        d = Path(tempfile.mkdtemp())
        for name, text in {"src/basket.js": "let hidden = 1;\n", **files}.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(text)
        return Assay({"repo": str(d)})

    def probe_script(self, body: str) -> dict:
        return {"probe.py": f"import json, sys\n{body}\n"}

    def test_a_probe_answers_with_its_own_json(self):
        run = self.tree()
        answer = run.probe(
            [sys.executable, "probe.py"],
            write=self.probe_script('print(json.dumps({"ok": True, "reached": True}))'),
        )
        assert answer == {"ok": True, "reached": True}

    def test_appending_reaches_what_the_module_kept_to_itself(self):
        """The replacement for instrumentation by regular expression. A probe concatenated
        into the module runs inside the scope it measures, so it enumerates nothing - and
        the regex it replaces silently found nothing for a top-level `class`, a
        `function*`, a destructured declaration, or a collision moved to a new file."""
        run = self.tree()
        answer = run.probe(
            [sys.executable, "src/basket.js.py"],
            write={"src/basket.js.py": "hidden = 1\n"},
            append={"src/basket.js.py": 'import json\nprint(json.dumps({"hidden": hidden}))\n'},
        )
        assert answer == {"hidden": 1}

    def test_the_clone_is_never_written_to(self):
        """The harness archives the diff *after* validation, and `repo.diff` runs
        `git add -A --intent-to-add` first, so an untracked file left in the clone enters
        the archived patch without condition - and is replayed at every re-scoring as the
        agent's own work."""
        run = self.tree()
        clone = run.repo
        run.probe(
            [sys.executable, "probe.py"],
            write=self.probe_script('print(json.dumps({"ok": True}))'),
        )
        assert sorted(p.name for p in clone.rglob("*")) == ["basket.js", "src"]

    def test_dropping_removes_what_a_glob_selects(self):
        run = self.tree(**{"src/basket.test.js": "// a test\n"})
        answer = run.probe(
            [sys.executable, "probe.py"],
            write=self.probe_script(
                "import pathlib\n"
                'print(json.dumps({"left": sorted(p.name for p in pathlib.Path("src").iterdir())}))'
            ),
            drop="*.test.js",
        )
        assert answer["left"] == ["basket.js"]

    def test_appending_to_something_absent_refuses(self):
        run = self.tree()
        with pytest.raises(CannotJudge) as raised:
            run.probe([sys.executable, "probe.py"], append={"nowhere.js": "x"})
        assert "nowhere.js" in str(raised.value)

    def test_an_interpreter_that_is_not_there_refuses(self):
        with pytest.raises(CannotJudge):
            self.tree().probe(["/nowhere/node", "probe.mjs"])

    def test_an_unreadable_answer_refuses(self):
        """A probe that printed prose rather than JSON did not answer, and prose must not
        be read as a negative answer."""
        run = self.tree()
        with pytest.raises(CannotJudge) as raised:
            run.probe(
                [sys.executable, "probe.py"],
                write={"probe.py": 'print("everything is fine")\n'},
            )
        assert "readable" in str(raised.value)

    def test_a_probe_that_answered_negatively_is_an_answer(self):
        """Exit code 0 with `ok: false` is the probe's contract: it reports through JSON,
        so it must not signal a failed assertion by exiting non-zero."""
        run = self.tree()
        answer = run.probe(
            [sys.executable, "probe.py"],
            write=self.probe_script('print(json.dumps({"ok": False, "why": "wrong axis"}))'),
        )
        assert not answer["ok"]
        assert answer["why"] == "wrong axis"

    def test_a_timeout_refuses_by_default(self):
        run = self.tree()
        with pytest.raises(ProbeTimeout):
            run.probe(
                [sys.executable, "probe.py"],
                write={"probe.py": "import time\ntime.sleep(30)\n"},
                timeout=1,
            )

    def test_but_a_domain_may_catch_it_and_score_a_failure(self):
        """A probe is milliseconds, so a slow one usually means the **agent's** fix loops
        forever - a failure of the work, not an inability to judge. Only the domain knows,
        so refusing is the safe default and the domain may override it with a reason. The
        shipped validator hardcoded the choice and could not express the other one."""
        run = self.tree()
        try:
            run.probe(
                [sys.executable, "probe.py"],
                write={"probe.py": "import time\ntime.sleep(30)\n"},
                timeout=1,
            )
            scored = None
        except ProbeTimeout as e:
            scored = Metric(False, f"the fix does not terminate: {e}")
        assert not scored
        assert "does not terminate" in scored.reason

    def test_a_timeout_is_still_a_cannot_judge_for_anyone_who_does_not_look(self):
        """So the unsafe reading is never the one you get by accident."""
        assert issubclass(ProbeTimeout, CannotJudge)
