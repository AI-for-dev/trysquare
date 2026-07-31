"""The judge, the brick resolution that nearly wasted a matrix, and layer 4.

The first group exists because of a real incident: a scenario referenced
`tickets/vague.md`, the path did not resolve, and `read_brick` silently
returned the string itself - so twelve runs were paid for with the literal text
"tickets/vague.md" as the agent's task. Every run looked normal.
"""

import json
from pathlib import Path

import pytest

from trysquare import parity, validation
from trysquare.measure import final_text
from trysquare.runner import looks_like_path, preflight, read_brick, referenced_paths
from trysquare.scenario import Validator, load, parse
from tests.test_scenario import GRID

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = ROOT / "tests" / "fixtures" / "matrix.toml"


class TestBrickResolution:
    @pytest.fixture(autouse=True)
    def a_brick_directory(self, tmp_path):
        self.base = tmp_path
        (self.base / "brick.md").write_text("real content")

    def test_an_existing_path_is_read(self):
        assert read_brick(self.base, "brick.md") == "real content"

    def test_a_missing_path_raises_instead_of_becoming_literal_text(self):
        """The silent fallback that sent a path to the agent as its task."""
        with pytest.raises(RuntimeError) as e:
            read_brick(self.base, "tickets/typo.md")
        assert "does not exist" in str(e.value)

    def test_inline_text_is_still_allowed(self):
        text = "La collision scanne toutes les briques a chaque frame."
        assert read_brick(self.base, text) == text

    def test_what_counts_as_a_path(self):
        for value in ("tickets/x.md", "a/b", "x.md", "notes.txt"):
            assert looks_like_path(value), value
        for value in ("do the thing", "off", "high", "a sentence with spaces"):
            assert not looks_like_path(value), value

    def test_none_stays_none(self):
        assert read_brick(self.base, None) is None


class TestPreflight:
    """A missing brick must be refused before the first token, not after a matrix."""

    def test_every_referenced_file_is_collected(self):
        s = parse(GRID)
        labels = [label for label, _ in referenced_paths(s, Path("/base"))]
        assert any("cell 'rule / off'" in label for label in labels)
        assert any("validation[script].command" in label for label in labels)

    def test_missing_files_are_listed_with_where_they_were_declared(self, tmp_path):
        s = parse(GRID)
        missing = preflight(s, tmp_path)
        assert missing
        assert any("context" in m for m in missing)

    def test_the_scenario_the_repository_carries_passes_preflight(self):
        """Which is what the incident above should have been caught by.

        It swept `scenarios/*.toml` until that directory stopped shipping, and then went
        on passing: no files, no iterations, green. A check that cannot fail is the very
        defect this module is about, so it names the one scenario still in the tree
        rather than a glob that is allowed to come back empty.
        """
        s = load(SCENARIO)
        assert preflight(s, s.path.parent) == []


class TestFinalText:
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
        assert final_text(s) == "final answer"

    def test_tool_results_are_not_the_answer(self):
        s = self.stream(
            self.assistant("the answer"),
            {"type": "message_end", "message": {"role": "toolResult", "content": "tool output"}},
        )
        assert final_text(s) == "the answer"

    def test_an_empty_trailing_message_does_not_erase_the_answer(self):
        s = self.stream(self.assistant("the answer"), self.assistant("   "))
        assert final_text(s) == "the answer"

    def test_no_assistant_text_yields_empty(self):
        assert final_text(self.stream({"type": "agent_start"})) == ""


class TestJudgeDossier:
    def validator(self):
        return Validator("judge", ("note_usable", "cites_paths"), {"provider": "p", "model": "m"})

    def test_the_request_declares_the_metrics_the_tool_must_require(self, tmp_path):
        d = tmp_path
        work, prompt = validation.judge_dossier(d, self.validator(), "the rubric", {"diff": "x"})
        request = json.loads((work / validation.JUDGE_REQUEST).read_text())
        assert request["metrics"] == ["note_usable", "cites_paths"]
        assert "the rubric" in prompt
        assert "note_usable" in prompt

    def test_a_stale_verdict_is_removed(self, tmp_path):
        """Otherwise a previous attempt's answer would be read as this one's."""
        d = tmp_path
        (d / validation.JUDGE_VERDICT).write_text('{"metrics": {"stale": true}}')
        validation.judge_dossier(d, self.validator(), "r", {})
        assert not (d / validation.JUDGE_VERDICT).exists()

    def test_the_dossier_carries_no_cell(self, tmp_path):
        d = tmp_path
        _, prompt = validation.judge_dossier(
            d, self.validator(), "r", {"prompt": "task", "diff": "d"}
        )
        for leak in ("cell", "variant", "full stack", "+subagents"):
            assert leak not in prompt

    def test_declared_pieces_appear_in_order(self, tmp_path):
        d = tmp_path
        _, prompt = validation.judge_dossier(
            d, self.validator(), "r", {"prompt": "THE TASK", "response": "THE NOTE"}
        )
        assert prompt.index("THE TASK") < prompt.index("THE NOTE")


class TestLayer4:
    """Mechanical criteria only: layer 4 samples, so it proves no rate."""

    def experiment(self, tmp_path, states, thinking_recorded="off", declared_thinking="off"):
        d = tmp_path / "exp_n2"
        (d / "runs").mkdir(parents=True)
        work = tmp_path / "work"
        work.mkdir()
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

    def test_a_clean_pass_reports_only_the_count(self, tmp_path):
        d, work = self.experiment(tmp_path, [("nothing / off", "valid"), ("rule / off", "valid")])
        problems = parity.layer4(d, work)
        assert len(problems) == 1
        assert "2 sessions checked" in problems[0]

    def test_an_invalid_run_fails_the_pass(self, tmp_path):
        d, work = self.experiment(tmp_path, [("nothing / off", "valid"), ("rule / off", "empty")])
        problems = parity.layer4(d, work)
        assert any("not valid" in p for p in problems)

    def test_a_thinking_mismatch_fails_the_pass(self, tmp_path):
        """The check that makes the thinking-equals-baseline defect impossible."""
        d, work = self.experiment(tmp_path, [("rule / high", "valid")], thinking_recorded="off")
        problems = parity.layer4(d, work)
        assert any("declared thinking 'high'" in p for p in problems)

    def test_a_missing_output_is_named(self, tmp_path):
        d, work = self.experiment(tmp_path, [("nothing / off", "valid")])
        (d / "synthesis.md").unlink()
        assert any("synthesis.md" in p for p in parity.layer4(d, work))

    def test_without_a_workdir_the_thinking_check_is_declared_skipped(self, tmp_path):
        d, _ = self.experiment(tmp_path, [("nothing / off", "valid")])
        problems = parity.layer4(d)
        assert any("skipped" in p for p in problems)

    def test_the_declared_level_is_read_from_the_cell_name(self):
        assert parity._declared_thinking("rule / high", "off") == "high"
        assert parity._declared_thinking("nothing / off", "high") == "off"
        assert parity._declared_thinking("full stack", "off") == "off"
