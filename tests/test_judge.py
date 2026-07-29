"""The judge, the brick resolution that nearly wasted a matrix, and layer 4.

The first group exists because of a real incident: a scenario referenced
`bricks/vague-ticket.md`, the path did not resolve, and `read_brick` silently
returned the string itself - so twelve runs were paid for with the literal text
"bricks/vague-ticket.md" as the agent's task. Every run looked normal.
"""

import json
import tempfile
import unittest
from pathlib import Path

from etabli import parity, validation
from etabli.measure import final_text
from etabli.runner import looks_like_path, preflight, read_brick, referenced_paths
from etabli.scenario import Validator, parse
from tests.test_scenario import GRID, MINIMAL

ROOT = Path(__file__).resolve().parent.parent


class TestBrickResolution(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        (self.base / "brick.md").write_text("real content")

    def test_an_existing_path_is_read(self):
        self.assertEqual(read_brick(self.base, "brick.md"), "real content")

    def test_a_missing_path_raises_instead_of_becoming_literal_text(self):
        """The silent fallback that sent a path to the agent as its task."""
        with self.assertRaises(RuntimeError) as e:
            read_brick(self.base, "bricks/typo.md")
        self.assertIn("does not exist", str(e.exception))

    def test_inline_text_is_still_allowed(self):
        text = "La collision scanne toutes les briques a chaque frame."
        self.assertEqual(read_brick(self.base, text), text)

    def test_what_counts_as_a_path(self):
        for value in ("bricks/x.md", "a/b", "x.md", "notes.txt"):
            self.assertTrue(looks_like_path(value), value)
        for value in ("do the thing", "off", "high", "a sentence with spaces"):
            self.assertFalse(looks_like_path(value), value)

    def test_none_stays_none(self):
        self.assertIsNone(read_brick(self.base, None))


class TestPreflight(unittest.TestCase):
    """A missing brick must be refused before the first token, not after a matrix."""

    def test_every_referenced_file_is_collected(self):
        s = parse(GRID)
        labels = [label for label, _ in referenced_paths(s, Path("/base"))]
        self.assertTrue(any("cell 'rule / off'" in l for l in labels))
        self.assertTrue(any("validation[script].command" in l for l in labels))

    def test_missing_files_are_listed_with_where_they_were_declared(self):
        s = parse(GRID)
        missing = preflight(s, Path(tempfile.mkdtemp()))
        self.assertTrue(missing)
        self.assertTrue(any("context" in m for m in missing))

    def test_the_shipped_scenarios_pass_preflight(self):
        """Which is what the incident above should have been caught by."""
        from etabli.scenario import load

        for f in sorted((ROOT / "scenarios").glob("*.toml")):
            with self.subTest(scenario=f.name):
                s = load(f)
                self.assertEqual(preflight(s, s.path.parent), [])


class TestFinalText(unittest.TestCase):
    """A judge scores the answer, not the transcript that produced it."""

    def stream(self, *events):
        return "\n".join(json.dumps(e) for e in events)

    def assistant(self, text):
        return {
            "type": "message_end",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }

    def test_the_last_assistant_text_wins(self):
        s = self.stream(self.assistant("first"), self.assistant("final answer"))
        self.assertEqual(final_text(s), "final answer")

    def test_tool_results_are_not_the_answer(self):
        s = self.stream(
            self.assistant("the answer"),
            {"type": "message_end", "message": {"role": "toolResult", "content": "tool output"}},
        )
        self.assertEqual(final_text(s), "the answer")

    def test_an_empty_trailing_message_does_not_erase_the_answer(self):
        s = self.stream(self.assistant("the answer"), self.assistant("   "))
        self.assertEqual(final_text(s), "the answer")

    def test_no_assistant_text_yields_empty(self):
        self.assertEqual(final_text(self.stream({"type": "agent_start"})), "")


class TestJudgeDossier(unittest.TestCase):
    def validator(self):
        return Validator("judge", ("note_usable", "cites_paths"), {"provider": "p", "model": "m"})

    def test_the_request_declares_the_metrics_the_tool_must_require(self):
        d = Path(tempfile.mkdtemp())
        work, prompt = validation.judge_dossier(d, self.validator(), "the rubric", {"diff": "x"})
        request = json.loads((work / validation.JUDGE_REQUEST).read_text())
        self.assertEqual(request["metrics"], ["note_usable", "cites_paths"])
        self.assertIn("the rubric", prompt)
        self.assertIn("note_usable", prompt)

    def test_a_stale_verdict_is_removed(self):
        """Otherwise a previous attempt's answer would be read as this one's."""
        d = Path(tempfile.mkdtemp())
        d.mkdir(exist_ok=True)
        (d / validation.JUDGE_VERDICT).write_text('{"metrics": {"stale": true}}')
        validation.judge_dossier(d, self.validator(), "r", {})
        self.assertFalse((d / validation.JUDGE_VERDICT).exists())

    def test_the_dossier_carries_no_cell(self):
        d = Path(tempfile.mkdtemp())
        _, prompt = validation.judge_dossier(
            d, self.validator(), "r", {"prompt": "task", "diff": "d"}
        )
        for leak in ("cell", "variant", "full stack", "+subagents"):
            self.assertNotIn(leak, prompt)

    def test_declared_pieces_appear_in_order(self):
        d = Path(tempfile.mkdtemp())
        _, prompt = validation.judge_dossier(
            d, self.validator(), "r", {"prompt": "THE TASK", "response": "THE NOTE"}
        )
        self.assertLess(prompt.index("THE TASK"), prompt.index("THE NOTE"))


class TestLayer4(unittest.TestCase):
    """Mechanical criteria only: layer 4 samples, so it proves no rate."""

    def experiment(self, states, thinking_recorded="off", declared_thinking="off"):
        d = Path(tempfile.mkdtemp()) / "exp_n2"
        (d / "runs").mkdir(parents=True)
        work = Path(tempfile.mkdtemp())
        runs = {}
        for i, (cell, state) in enumerate(states):
            rid = f"r{i}"
            runs[rid] = {"cell": cell, "repetition": i, "state": state, "attempts": 1}
            rd = d / "runs" / rid
            (rd / "validation").mkdir(parents=True)
            (rd / "configuration.json").write_text("{}")
            (rd / "diff.patch").write_text("")
            sd = work / d.name / rid / "session"
            sd.mkdir(parents=True)
            (sd / "s.jsonl").write_text(
                json.dumps({"type": "thinking_level_change", "level": thinking_recorded})
            )
        (d / "state.json").write_text(
            json.dumps({"runs": runs, "complete": all(s == "valid" for _, s in states),
                        "thinking": declared_thinking})
        )
        (d / "measures.json").write_text("[]")
        (d / "synthesis.md").write_text("#")
        return d, work

    def test_a_clean_pass_reports_only_the_count(self):
        d, work = self.experiment([("nothing / off", "valid"), ("rule / off", "valid")])
        problems = parity.layer4(d, work)
        self.assertEqual(len(problems), 1)
        self.assertIn("2 sessions checked", problems[0])

    def test_an_invalid_run_fails_the_pass(self):
        d, work = self.experiment([("nothing / off", "valid"), ("rule / off", "empty")])
        problems = parity.layer4(d, work)
        self.assertTrue(any("not valid" in p for p in problems))

    def test_a_thinking_mismatch_fails_the_pass(self):
        """The check that makes the thinking-equals-baseline defect impossible."""
        d, work = self.experiment([("rule / high", "valid")], thinking_recorded="off")
        problems = parity.layer4(d, work)
        self.assertTrue(any("declared thinking 'high'" in p for p in problems))

    def test_a_missing_output_is_named(self):
        d, work = self.experiment([("nothing / off", "valid")])
        (d / "synthesis.md").unlink()
        self.assertTrue(any("synthesis.md" in p for p in parity.layer4(d, work)))

    def test_without_a_workdir_the_thinking_check_is_declared_skipped(self):
        d, _ = self.experiment([("nothing / off", "valid")])
        problems = parity.layer4(d)
        self.assertTrue(any("skipped" in p for p in problems))

    def test_the_declared_level_is_read_from_the_cell_name(self):
        self.assertEqual(parity._declared_thinking("rule / high", "off"), "high")
        self.assertEqual(parity._declared_thinking("nothing / off", "high"), "off")
        self.assertEqual(parity._declared_thinking("full stack", "off"), "off")


if __name__ == "__main__":
    unittest.main()
