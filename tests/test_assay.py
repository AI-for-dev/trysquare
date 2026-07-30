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
import unittest
from pathlib import Path

from tests.gitrepo import a_repo
from trysquare import assay
from trysquare.assay import Assay, CannotJudge, Metric, ProbeTimeout


class TestMetric(unittest.TestCase):
    def test_a_metric_reads_as_its_value(self):
        """`if not run.tests():` has to work, or every call site grows a `.value`."""
        self.assertFalse(Metric(False, "1 failure"))
        self.assertTrue(Metric(True))
        self.assertFalse(Metric(0))
        self.assertTrue(Metric(7))

    def test_a_bare_value_needs_no_ceremony(self):
        """Most metrics carry no reason, and wrapping them would be noise."""
        self.assertEqual(assay.report({"delivered": True})["metrics"], {"delivered": True})

    def test_a_reason_is_published_on_a_success_too(self):
        """`citations.py:90-94` puts one on success ("cites a.js, b.js") and it is
        useful. Filtering on failure is not implementable: "failed" is only definable
        for a boolean, and `cited_paths = 7` is neither."""
        out = assay.report({"cited_paths": Metric(7, "cites a.js, b.js")})
        self.assertEqual(out["metrics"]["cited_paths"], 7)
        self.assertEqual(out["reasons"]["cited_paths"], "cites a.js, b.js")

    def test_a_metric_without_a_reason_adds_no_reason(self):
        self.assertEqual(assay.report({"tests": Metric(True)})["reasons"], {})


class TestUnjudged(unittest.TestCase):
    """One metric may be unjudgeable while the rest of the run is fine.

    The real case is the probe that could not run: `issue1.py:310-316` returns
    `{"ok": False, "erreur": "pas de game/ dans le clone"}`, which the harness records
    as `par_face = false` - "could not judge" read as "worked badly", the confusion
    this whole project is built against.
    """

    def test_an_unjudged_metric_leaves_the_metrics(self):
        out = assay.report({"red_first": Metric.unjudged("no session archived")})
        self.assertNotIn("red_first", out["metrics"])

    def test_its_name_is_still_returned_so_a_typo_stays_loud(self):
        """The harness refuses a declared metric that is absent. Keeping the name in
        `unjudged` is what lets a real absence (a typo) stay an error while an honest
        "I cannot say" degrades a denominator instead."""
        out = assay.report({"red_first": Metric.unjudged("no session archived")})
        self.assertEqual(out["unjudged"], {"red_first": "no session archived"})

    def test_the_reason_is_required(self):
        """A denominator that shrank for no stated reason is unreadable six months on."""
        with self.assertRaises(ValueError):
            Metric.unjudged("")

    def test_an_unjudged_metric_is_not_a_false_one(self):
        self.assertNotEqual(Metric.unjudged("no session"), Metric(False, "no session"))


class TestSetsSerialiseSorted(unittest.TestCase):
    def test_a_set_becomes_a_sorted_list(self):
        """Not cosmetic. PYTHONHASHSEED is random, so an unsorted set of strings
        serialises differently from one process to the next: two identical
        measurements would produce byte-different `measures.json`, and `compare` would
        read a difference that is not there."""
        out = assay.report({"touched": {"z.js", "a.js", "m.js"}})
        self.assertEqual(out["metrics"]["touched"], ["a.js", "m.js", "z.js"])

    def test_a_frozenset_too(self):
        out = assay.report({"touched": frozenset({"b", "a"})})
        self.assertEqual(out["metrics"]["touched"], ["a", "b"])

    def test_the_sort_survives_a_subprocess(self):
        """The property that matters is stability across processes, which is exactly
        what a single in-process assertion cannot show."""
        code = (
            "from trysquare import assay;"
            "import json;"
            "print(json.dumps(assay.report({'t': {'game/neon.js', 'README.md', 'p.json'}})"
            "['metrics']['t']))"
        )
        runs = {
            subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=True
            ).stdout.strip()
            for _ in range(4)
        }
        self.assertEqual(len(runs), 1, f"order varied between processes: {runs}")


class TestTheErrorContract(unittest.TestCase):
    """"Could not judge" is not "worked badly", and one validator in four got it right.

    `neon.py:174` calls `evaluate(context)` with no net, so a traceback lands in
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
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["metrics"], {"delivered": True})

    def test_cannot_judge_exits_one_with_a_sentence(self):
        def evaluate(run):
            raise CannotJudge("no session archived")

        code, out, err = self.run_validator(evaluate, self.context())
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("could not score this run", err)
        self.assertIn("no session archived", err)

    def test_any_other_exception_is_caught_too(self):
        """The default has to be right: doing nothing is what `neon.py` did."""

        def evaluate(run):
            raise KeyError("boom")

        code, _, err = self.run_validator(evaluate, self.context())
        self.assertEqual(code, 1)
        self.assertIn("the validator failed", err)

    def test_the_sentence_comes_before_the_traceback(self):
        """The fix for "a traceback reads as a broken validator" is the order, not
        suppression: the trace costs a real debugging session to throw away, and
        `run_script` archives stderr (`outputs.py:286`) for exactly that."""

        def evaluate(run):
            raise KeyError("boom")

        _, _, err = self.run_validator(evaluate, self.context())
        lines = [line for line in err.split("\n") if line.strip()]
        self.assertTrue(lines[0].startswith("the validator failed"), lines[:2])
        self.assertIn("Traceback", err)

    def test_a_cannot_judge_carries_no_traceback(self):
        """It is not a defect, so a trace would only invite reading it as one."""

        def evaluate(run):
            raise CannotJudge("the context names no session")

        _, _, err = self.run_validator(evaluate, self.context())
        self.assertNotIn("Traceback", err)

    def test_an_unreadable_context_exits_two(self):
        code, _, err = self.run_validator(lambda run: {}, Path("/nowhere/context.json"))
        self.assertEqual(code, 2)
        self.assertIn("unreadable context", err)

    def test_a_declared_metric_never_returned_is_refused_early(self):
        """The harness refuses it too, but only after the tokens are spent. Naming it
        here says which one is missing, before anything is recorded."""
        context = self.context(declared=["delivered", "tests"])
        code, _, err = self.run_validator(lambda run: {"delivered": True}, context)
        self.assertEqual(code, 1)
        self.assertIn("tests", err)

    def test_an_extra_metric_is_not_refused(self):
        """`merge` keeps extras on purpose, which is what lets one validator serve
        several scenarios."""
        context = self.context(declared=["delivered"])
        code, out, _ = self.run_validator(
            lambda run: {"delivered": True, "spare": 3}, context
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["metrics"]["spare"], 3)

    def test_an_unjudged_metric_counts_as_returned(self):
        context = self.context(declared=["delivered", "red_first"])
        code, _, _ = self.run_validator(
            lambda run: {"delivered": True, "red_first": Metric.unjudged("no session")},
            context,
        )
        self.assertEqual(code, 0)


class TestTheFakeRefusesToInvent(unittest.TestCase):
    """A fake that answered would put the absence of a measurement in the shape of a
    measurement - an empty set reads as "the agent touched nothing" - which is the
    confusion the error contract exists to prevent, moved into the tests.
    """

    def test_what_the_test_declared_is_readable(self):
        run = Assay.fake(touched={"a.js"})
        self.assertEqual(run.touched, {"a.js"})

    def test_what_it_did_not_declare_raises(self):
        run = Assay.fake(touched={"a.js"})
        with self.assertRaises(CannotJudge) as raised:
            run.files_at_etalon
        self.assertIn("fake", str(raised.exception).lower())

    def test_the_message_names_what_to_declare(self):
        run = Assay.fake(touched={"a.js"})
        with self.assertRaises(CannotJudge) as raised:
            run.response
        self.assertIn("response", str(raised.exception))

    def test_a_method_the_test_stubbed_answers(self):
        run = Assay.fake(touched={"a.js"}, tests=Metric(False, "1 failure"))
        self.assertFalse(run.tests())
        self.assertEqual(run.tests().reason, "1 failure")

    def test_an_unstubbed_method_raises(self):
        run = Assay.fake(touched={"a.js"})
        with self.assertRaises(CannotJudge):
            run.tests()

    def test_a_missing_context_key_is_not_an_empty_value(self):
        """An empty set means the agent touched nothing, which is a measurement. A
        missing key means nobody measured, which is not the same fact."""
        run = Assay({"repo": "/r"})
        with self.assertRaises(CannotJudge) as raised:
            run.touched
        self.assertIn("touched", str(raised.exception))


ETALON = {
    "game/neon.js": "export function step() {}\n// a comment\n",
    "game/theme.js": "export const palette = {};\n",
    "game/neon.test.js": "import {step} from './neon.js';\n",
    "README.md": "# t\n",
}


class TestSourcesAtEtalon(unittest.TestCase):
    """The reference side of any comparison, read **from the tag**.

    `neon.py:88-91` falls back to the checkout's working tree when the harness provides
    one, and `issue1.py:183-195` documents at length why that is wrong: trysquare puts
    the source repository there, whose working tree is on `main`, so the reference
    drifts the moment `main` moves or a classroom fixes the issue in place. Exactly what
    pinning by tag exists to prevent. The base offers only the correct one.
    """

    def setUp(self):
        self.source = a_repo(ETALON)
        self.run = Assay(
            {
                "repo": "/unused",
                "etalon": {"tag": "etalon-v1", "checkout": str(self.source)},
                "files": sorted(ETALON),
            }
        )

    def test_a_pattern_selects_and_the_contents_come_from_the_tag(self):
        text = self.run.sources_at_etalon("game/*.js")
        self.assertIn("export function step", text)
        self.assertIn("export const palette", text)
        self.assertNotIn("# t", text)

    def test_an_exclusion_removes_what_the_pattern_caught(self):
        text = self.run.sources_at_etalon("game/*.js", exclude="*.test.js")
        self.assertNotIn("from './neon.js'", text)
        self.assertIn("export function step", text)

    def test_the_working_tree_is_never_read(self):
        """The whole point. Moving `main` on must not move the reference."""
        (self.source / "game" / "neon.js").write_text("export function step() { fixed; }\n")
        self.assertNotIn("fixed", self.run.sources_at_etalon("game/*.js"))

    def test_a_pattern_matching_nothing_is_empty_rather_than_an_error(self):
        self.assertEqual(self.run.sources_at_etalon("src/*.ts"), "")

    def test_a_missing_checkout_refuses_rather_than_guessing(self):
        run = Assay(
            {"etalon": {"tag": "etalon-v1", "checkout": "/nowhere"}, "files": ["a.js"]}
        )
        with self.assertRaises(CannotJudge):
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


TEST_FILE = {"path": "game/neon.test.js"}
SOURCE_FILE = {"path": "game/neon.js"}


class TestToolCalls(unittest.TestCase):
    """Read from the archived session, which is where they were all along.

    `issue1.py:386-450` reads `context["trace"]`, the raw stream, which is deliberately
    not archived. In the session a single record carries the tool name, the id **and**
    `isError`, so the `toolCallId` reconciliation of `:392-416` - the part its own
    ticket called the ugliest in the file - had no cause but reading the wrong file.
    """

    def test_the_calls_come_back_in_order(self):
        run = Assay(session(("read", SOURCE_FILE), ("edit", TEST_FILE)))
        self.assertEqual([c.name for c in run.tool_calls()], ["read", "edit"])

    def test_a_failure_is_on_the_call_itself(self):
        run = Assay(session(("edit", {}, True), ("edit", TEST_FILE)))
        self.assertEqual([c.failed for c in run.tool_calls()], [True, False])

    def test_the_first_write_is_found_by_path(self):
        run = Assay(session(("read", SOURCE_FILE), ("edit", TEST_FILE)))
        self.assertEqual(run.first_write("game/neon.test.js"), 1)

    def test_a_failed_call_is_not_a_write(self):
        """`pi` rejected two `edit` calls with no `path` on a real run. Counting them
        would date the work before it happened."""
        run = Assay(session(("edit", {}, True), ("edit", TEST_FILE)))
        self.assertEqual(run.first_write("game/neon.test.js"), 1)

    def test_nothing_written_is_none_rather_than_an_error(self):
        run = Assay(session(("read", SOURCE_FILE)))
        self.assertIsNone(run.first_write("game/neon.js"))

    def test_a_shell_redirection_counts_as_a_write(self):
        run = Assay(session(("bash", {"command": "cat > game/neon.test.js <<'EOF'"})))
        self.assertEqual(run.first_write("game/neon.test.js"), 0)

    def test_a_test_file_is_not_the_source_file(self):
        """`game/neon.test.js` ends with `neon.test.js`, never with `neon.js`."""
        run = Assay(session(("edit", TEST_FILE)))
        self.assertIsNone(run.first_write("game/neon.js"))

    def test_an_unknown_tool_refuses_rather_than_answering_no(self):
        """The list of writing tools ages with `pi`, not with trysquare, so it has to
        age loudly: a new writing tool would otherwise make a process metric quietly
        false, and a lower column reads as a less disciplined agent."""
        run = Assay(session(("read", SOURCE_FILE), ("sonar", {"path": "a.js"})))
        with self.assertRaises(CannotJudge) as raised:
            run.first_write("a.js")
        self.assertIn("sonar", str(raised.exception))

    def test_a_subagent_refuses_because_it_writes_without_saying_where(self):
        run = Assay(session(("subagent", {"agent": "coder", "task": "fix it"})))
        with self.assertRaises(CannotJudge) as raised:
            run.first_write("a.js")
        self.assertIn("subagent", str(raised.exception))

    def test_an_absent_session_refuses(self):
        """The session comes from the harness, so its absence is an inability to judge
        and never a statement about the agent."""
        run = Assay({"session": "/nowhere"})
        with self.assertRaises(CannotJudge):
            run.tool_calls()


def a_runner(directory: Path, code: int, out: str = "", err: str = "") -> str:
    """A command standing for a test runner: a chosen exit code and a chosen output.

    A **string**, because that is what a scenario writes and what the context carries.
    `shlex.join` rather than a bare join, so a temporary path holding a space still produces
    one word.
    """
    script = directory / "runner.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({out!r})\n"
        f"sys.stderr.write({err!r})\n"
        f"sys.exit({code})\n"
    )
    return shlex.join([sys.executable, str(script)])


NODE_SPEC = (
    "✔ bounces off the wall (0.25ms)\n"
    "✖ bounces off a brick (0.26ms)\n"
    "ℹ tests 2\n"
    "ℹ fail 1\n"
    "\n"
    "✖ failing tests:\n"
    "test at game/neon.test.js:12:1\n"
    "✖ bounces off a brick (0.26ms)\n"
    "  AssertionError: expected -300 to equal 300\n"
)


class TestTheDeclaredSuite(unittest.TestCase):
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
        self.assertTrue(self.run_with(0, "ℹ pass 2\n"))

    def test_a_red_suite_is_false_rather_than_unjudged(self):
        """A failing suite is a measurement, and the agent's own."""
        result = self.run_with(1, NODE_SPEC)
        self.assertFalse(result)
        self.assertTrue(result.judged)

    def test_the_reason_is_the_runners_own_summary(self):
        reason = self.run_with(1, NODE_SPEC).reason
        self.assertIn("bounces off a brick", reason)
        self.assertIn("expected -300 to equal 300", reason)

    def test_the_reason_of_a_spec_report_is_not_a_closing_brace(self):
        """The exact regression: the old grep found nothing and fell back to the last
        line, which for a spec report is the tail of a diff."""
        self.assertNotEqual(self.run_with(1, NODE_SPEC).reason.strip(), "}")

    def test_a_pytest_summary_is_found_too(self):
        out = "=== short test summary info ===\nFAILED test_a.py::test_one - assert 1 == 2\n"
        self.assertIn("test_one", self.run_with(1, out).reason)

    def test_a_report_with_no_known_marker_says_it_fell_back(self):
        """A reason that does not declare itself a fallback is what hid the Node change."""
        reason = self.run_with(1, "something nobody recognises\n").reason
        self.assertIn("no recognised summary", reason)
        self.assertIn("something nobody recognises", reason)

    def test_output_on_stderr_is_not_a_failing_suite(self):
        """`npm` writes a notice to stderr on a perfectly green run. Reading stderr's
        presence as evidence is what put `npm notice run node --test` in a reason."""
        result = self.run_with(0, "ℹ pass 2\n", err="npm notice run node --test\n")
        self.assertTrue(result)
        self.assertNotIn("npm notice", result.reason)

    def test_an_executable_that_is_not_there_cannot_judge(self):
        d = Path(tempfile.mkdtemp())
        run = Assay({"repo": str(d), "test_command": "/nowhere/runner --test"})
        with self.assertRaises(CannotJudge):
            run.tests()

    def test_a_missing_npm_script_cannot_judge(self):
        """`npm error Missing script: "test"` exits 1, exactly like a failing suite, and
        it means nobody ran anything."""
        with self.assertRaises(CannotJudge):
            self.run_with(1, err='npm error Missing script: "test"\n')

    def test_pytest_collecting_nothing_cannot_judge(self):
        """Exit 5 is "no test collected", which is not a green suite and not a red one.
        `unittest discover` over a pytest test prints `Ran 0 tests`, `OK`, and exits 0 -
        the same hole, silent."""
        with self.assertRaises(CannotJudge):
            self.run_with(5)

    def test_pytest_misused_cannot_judge(self):
        with self.assertRaises(CannotJudge):
            self.run_with(4)

    def test_node_failing_to_load_a_reporter_cannot_judge(self):
        """Exit 7 is `ERR_MODULE_NOT_FOUND`: an inability to judge dressed as a failure."""
        with self.assertRaises(CannotJudge):
            self.run_with(7)

    def test_a_suite_that_never_ends_cannot_judge(self):
        d = Path(tempfile.mkdtemp())
        script = d / "hang.py"
        script.write_text("import time\ntime.sleep(30)\n")
        run = Assay({"repo": str(d), "test_command": shlex.join([sys.executable, str(script)])})
        with self.assertRaises(CannotJudge) as raised:
            run.tests(timeout=1)
        self.assertIn("timed out", str(raised.exception))

    def test_a_scenario_naming_no_suite_cannot_judge(self):
        """An absent `test_command` says this experiment scores no suite, which is
        something to refuse over rather than to score as a failure."""
        run = Assay({"repo": "/r"})
        with self.assertRaises(CannotJudge) as raised:
            run.tests()
        self.assertIn("test_command", str(raised.exception))

    def test_a_preparation_step_runs_before_the_suite(self):
        d = Path(tempfile.mkdtemp())
        run = Assay(
            {
                "repo": str(d),
                "prepare": [a_runner(d, 0, "installed\n")],
                "test_command": shlex.join([sys.executable, "-c", "print('ℹ pass 1')"]),
            }
        )
        self.assertTrue(run.tests())

    def test_a_preparation_step_that_fails_cannot_judge(self):
        """Not a red suite: nobody ran it. No network, or a dependency that will not
        install, would otherwise score the agent red on a column that can carry the
        scenario's validity condition."""
        d = Path(tempfile.mkdtemp())
        run = Assay(
            {
                "repo": str(d),
                "prepare": [a_runner(d, 1, "", "npm ERR! network unreachable\n")],
                "test_command": shlex.join([sys.executable, "-c", "print('ℹ pass 1')"]),
            }
        )
        with self.assertRaises(CannotJudge) as raised:
            run.tests()
        self.assertIn("before the suite ran", str(raised.exception))

    def test_the_suite_does_not_run_when_preparation_failed(self):
        """Otherwise a green suite could paper over a failed install."""
        d = Path(tempfile.mkdtemp())
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
        with self.assertRaises(CannotJudge):
            run.tests()
        self.assertFalse(witness.exists())

    def test_the_command_is_run_as_an_argv_without_a_shell(self):
        """No shell, so a scenario cannot smuggle a redirection past the declaration."""
        d = Path(tempfile.mkdtemp())
        run = Assay({"repo": str(d), "test_command": "echo 'hi > stolen.txt'"})
        self.assertTrue(run.tests())
        self.assertFalse((d / "stolen.txt").exists())


class TestTheProbe(unittest.TestCase):
    """A criterion that is a behaviour executes instead of being recognised.

    No pattern in the diff, no judge, no tokens, and a wrong answer is an assertion that
    breaks. The generic half is here; the probe's text and its cases are the domain's.
    """

    def tree(self, **files) -> Assay:
        d = Path(tempfile.mkdtemp())
        for name, text in {"game/neon.js": "let hidden = 1;\n", **files}.items():
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
        self.assertEqual(answer, {"ok": True, "reached": True})

    def test_appending_reaches_what_the_module_kept_to_itself(self):
        """The replacement for instrumentation by regular expression. A probe concatenated
        into the module runs inside the scope it measures, so it enumerates nothing - and
        the regex it replaces silently found nothing for a top-level `class`, a
        `function*`, a destructured declaration, or a collision moved to a new file."""
        run = self.tree()
        answer = run.probe(
            [sys.executable, "game/neon.js.py"],
            write={"game/neon.js.py": "hidden = 1\n"},
            append={"game/neon.js.py": 'import json\nprint(json.dumps({"hidden": hidden}))\n'},
        )
        self.assertEqual(answer, {"hidden": 1})

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
        self.assertEqual(sorted(p.name for p in clone.rglob("*")), ["game", "neon.js"])

    def test_dropping_removes_what_a_glob_selects(self):
        run = self.tree(**{"game/neon.test.js": "// a test\n"})
        answer = run.probe(
            [sys.executable, "probe.py"],
            write=self.probe_script(
                "import pathlib\n"
                'print(json.dumps({"left": sorted(p.name for p in pathlib.Path("game").iterdir())}))'
            ),
            drop="*.test.js",
        )
        self.assertEqual(answer["left"], ["neon.js"])

    def test_appending_to_something_absent_refuses(self):
        run = self.tree()
        with self.assertRaises(CannotJudge) as raised:
            run.probe([sys.executable, "probe.py"], append={"nowhere.js": "x"})
        self.assertIn("nowhere.js", str(raised.exception))

    def test_an_interpreter_that_is_not_there_refuses(self):
        with self.assertRaises(CannotJudge):
            self.tree().probe(["/nowhere/node", "probe.mjs"])

    def test_an_unreadable_answer_refuses(self):
        """A probe that printed prose rather than JSON did not answer, and prose must not
        be read as a negative answer."""
        run = self.tree()
        with self.assertRaises(CannotJudge) as raised:
            run.probe(
                [sys.executable, "probe.py"],
                write={"probe.py": 'print("everything is fine")\n'},
            )
        self.assertIn("readable", str(raised.exception))

    def test_a_probe_that_answered_negatively_is_an_answer(self):
        """Exit code 0 with `ok: false` is the probe's contract: it reports through JSON,
        so it must not signal a failed assertion by exiting non-zero."""
        run = self.tree()
        answer = run.probe(
            [sys.executable, "probe.py"],
            write=self.probe_script('print(json.dumps({"ok": False, "why": "wrong axis"}))'),
        )
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["why"], "wrong axis")

    def test_a_timeout_refuses_by_default(self):
        run = self.tree()
        with self.assertRaises(ProbeTimeout):
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
        self.assertFalse(scored)
        self.assertIn("does not terminate", scored.reason)

    def test_a_timeout_is_still_a_cannot_judge_for_anyone_who_does_not_look(self):
        """So the unsafe reading is never the one you get by accident."""
        self.assertTrue(issubclass(ProbeTimeout, CannotJudge))


if __name__ == "__main__":
    unittest.main()
