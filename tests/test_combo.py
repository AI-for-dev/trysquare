# SPDX-License-Identifier: BSD-3-Clause
"""A run driven by a combo `/run` is measured from what the flow spent.

pi runs `/run` before any model turn, so the main session's stream carries no usage at
all: every token is in combo's own subagents, and only its run directory says so. The
fake agent below leaves that directory as combo v0.4.0 does.

Nothing here spends a token.
"""

import json
import sys
from pathlib import Path

import pytest

from trysquare import agent, combo
from trysquare.cli import main

from tests.gitrepo import a_repo
from tests.test_cli import SCENARIO_TOML, TREE_DEPENDENT

FLOW_TOTAL = {
    "input": 9019,
    "output": 670,
    "cacheRead": 12,
    "cacheWrite": 3,
    "cost": 0.5,
    "turns": 2,
}
ANSWER = "Result of the `fix` flow\n\nok · runs/2026-10-07_19-21-16"


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch):
    monkeypatch.setattr(agent, "PI", sys.executable)


def custom(text: str) -> str:
    message = {"role": "custom", "customType": combo.ANSWER, "content": text}
    return json.dumps({"type": "message_end", "message": message})


def assistant(text: str) -> str:
    message = {"role": "assistant", "content": text, "usage": {"input": 100, "output": 10}}
    return json.dumps({"type": "message_end", "message": message})


def flow(name="2026-10-07_19-21-16", total=FLOW_TOTAL, usage=True, lines=(custom(ANSWER),)):
    """A fake `pi -p "/run ..."`: the run directory combo writes, then the stream."""
    script = f"""
import json, os, sys
d = os.path.join("runs", {name!r})
os.makedirs(d)
open(os.path.join("runs", ".gitignore"), "w").write("*")
open(os.path.join(d, "journal.jsonl"), "w").write('{{"type":"life_start"}}\\n')
if {usage!r}:
    open(os.path.join(d, "usage.json"), "w").write(json.dumps({{"subagents": [], "total": {total!r}}}))
for line in {list(lines)!r}:
    print(line)
"""
    return ["-c", script]


def run(tmp_path, args):
    return agent.run(tmp_path, args, timeout=60, trace=tmp_path / "trace.jsonl")


class TestAFlowIsMeasured:
    def test_a_flow_with_no_main_turn_produced_something(self, tmp_path):
        outcome = run(tmp_path, flow())
        assert outcome.produced_something
        assert outcome.usage == {
            "input": 9019,
            "output": 670,
            "cacheRead": 12,
            "cost": 0.5,
            "turns": 2,
            "retries": 0,
        }

    def test_its_answer_is_the_response(self, tmp_path):
        assert run(tmp_path, flow()).response == ANSWER

    def test_each_part_says_where_it_came_from(self, tmp_path):
        """A reader must tell what the main session spent from what the flow spent."""
        outcome = run(tmp_path, flow(lines=(custom(ANSWER), assistant("done"))))
        assert outcome.sources == {
            "session": {
                "input": 100,
                "output": 10,
                "cacheRead": 0,
                "cost": 0.0,
                "turns": 1,
                "retries": 0,
            },
            "runs/2026-10-07_19-21-16": {
                "input": 9019,
                "output": 670,
                "cacheRead": 12,
                "cost": 0.5,
                "turns": 2,
            },
        }
        assert outcome.usage["input"] == 9119
        assert outcome.usage["turns"] == 3

    def test_a_failed_flow_is_still_a_measurement(self, tmp_path):
        """It spent tokens and produced a result: `failed at` is an answer, not silence."""
        failed = "failed at fix: agent: refused · runs/2026-10-07_19-21-16 · /run resume ..."
        outcome = run(tmp_path, flow(lines=(custom(failed),)))
        assert outcome.produced_something
        assert outcome.response == failed

    def test_a_refused_run_stays_empty(self, tmp_path):
        """combo refuses a bad flow without a word in print mode, and creates no directory."""
        outcome = run(tmp_path, ["-c", "pass"])
        assert not outcome.produced_something
        assert outcome.sources == {}

    def test_a_run_left_by_an_earlier_attempt_is_not_counted_again(self, tmp_path):
        """Attempts share one clone, so the directory of the attempt before is still there."""
        run(tmp_path, flow())
        outcome = run(tmp_path, flow(name="2026-10-07_19-21-16-2"))
        assert list(outcome.sources) == ["session", "runs/2026-10-07_19-21-16-2"]
        assert outcome.usage["input"] == 9019


class TestTheLayoutIsPinned:
    def test_a_run_combo_did_not_close_is_refused(self, tmp_path):
        with pytest.raises(combo.LayoutError, match="no usage.json"):
            run(tmp_path, flow(usage=False))

    def test_a_total_missing_a_figure_is_refused(self, tmp_path):
        total = {k: v for k, v in FLOW_TOTAL.items() if k != "cost"}
        with pytest.raises(combo.LayoutError, match="no total cost"):
            run(tmp_path, flow(total=total))

    def test_a_directory_that_is_not_a_run_is_ignored(self, tmp_path):
        (tmp_path / "runs" / "notes").mkdir(parents=True)
        assert combo.run_dirs(tmp_path) == set()


class TestAFlowIsArchived:
    """The flow's own record is its session: the main one stays empty under a `/run`."""

    @pytest.fixture
    def launched(self, tmp_path, monkeypatch):
        """The run directory of a whole `trysquare run` driven by a fake `/run`."""

        def launch(**fake_flow) -> Path:
            fake = tmp_path / "pi"
            fake.write_text(
                f"#!{sys.executable}\nimport sys\n"
                'if "--version" in sys.argv:\n    sys.exit(print("0.0.0"))\n'
                'open("a.js", "w").write("changed\\n")\n' + flow(**fake_flow)[1]
            )
            fake.chmod(0o755)
            monkeypatch.setattr(agent, "PI", str(fake))
            validator = tmp_path / "v.py"
            validator.write_text(TREE_DEPENDENT)
            validator.chmod(0o755)
            config = tmp_path / "trysquare.toml"
            source = a_repo({"a.js": "one\n"})
            config.write_text(
                f'[repos]\nmy-repo = "{source}"\n[defaults]\nworkdir = "{tmp_path}"\n'
            )
            scenario = SCENARIO_TOML.replace("repetitions = 2", "repetitions = 1")
            (tmp_path / "s.toml").write_text(scenario)
            argv = ["run", str(tmp_path / "s.toml"), "-o", str(tmp_path / "out")]
            main([*argv, "--config", str(config), "--no-progress"])
            (run,) = (tmp_path / "out").glob("*/runs/none/*")
            return run

        return launch

    @pytest.fixture
    def archived(self, launched):
        return launched()

    def test_the_flow_s_run_directory_is_archived_as_the_session(self, archived):
        flow_dir = archived / "session" / "runs" / "2026-10-07_19-21-16"
        assert (flow_dir / "journal.jsonl").read_text() == '{"type":"life_start"}\n'
        assert json.loads((flow_dir / "usage.json").read_text())["total"] == FLOW_TOTAL

    def test_what_the_flow_left_beside_it_is_not_archived(self, archived):
        assert sorted(p.name for p in (archived / "session" / "runs").iterdir()) == [
            "2026-10-07_19-21-16"
        ]

    def test_a_flow_cut_before_combo_measured_it_is_archived_all_the_same(self, launched):
        """A timeout leaves no `usage.json`, and the run fails: its journal is all that says why."""
        archived = launched(usage=False)
        flow_dir = archived / "session" / "runs" / "2026-10-07_19-21-16"
        assert (flow_dir / "journal.jsonl").read_text() == '{"type":"life_start"}\n'
        assert not (flow_dir / "usage.json").exists()


class TestASubagentIsNamed:
    """combo names a subagent's session by when it started and its id. The transcript that
    names its agent is only written once the subagent closes."""

    @staticmethod
    def started(directory: Path, at: str, sid: str) -> None:
        sessions = directory / combo.SESSIONS
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / f"2026-10-08T06-26-{at}Z_{sid}.jsonl").write_text('{"type":"session"}\n')

    @staticmethod
    def journaled(directory: Path, *entries: dict) -> None:
        lines = [{"type": "life_start"}, *entries]
        (directory / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))

    def test_a_closed_subagent_by_its_transcript_and_a_working_one_by_its_visit(self, tmp_path):
        self.started(tmp_path, "27-000", "a1")
        self.started(tmp_path, "35-000", "b2")
        (tmp_path / "look[1]" / "find").mkdir(parents=True)
        (tmp_path / "look[1]" / "find" / "scout.jsonl").write_text('{"id":"a1"}\n')
        self.journaled(
            tmp_path,
            {"type": "visit_start", "path": "look[1]/find", "kind": "agent", "agent": "scout"},
            {"type": "visit_end", "path": "look[1]/find", "kind": "agent"},
            {"type": "visit_start", "path": "fix", "kind": "agent", "agent": "fixer"},
        )
        found = [
            {k: s[k] for k in ("id", "agent", "path", "running")} for s in combo.subagents(tmp_path)
        ]
        assert found == [
            {"id": "a1", "agent": "scout", "path": "look[1]/find", "running": False},
            {"id": "b2", "agent": "fixer", "path": "fix", "running": True},
        ]

    def test_one_no_visit_names_goes_by_its_id(self, tmp_path):
        """A delegated child: a session of its own, and no visit of its own."""
        self.started(tmp_path, "27-000", "a1")
        self.started(tmp_path, "28-000", "c3")
        self.journaled(
            tmp_path, {"type": "visit_start", "path": "fix", "kind": "agent", "agent": "fixer"}
        )
        child = combo.subagents(tmp_path)[1]
        assert (child["id"], child["agent"], child["path"]) == ("c3", None, None)

    def test_a_directory_without_sessions_has_no_subagent(self, tmp_path):
        assert combo.subagents(tmp_path / "missing") == []
