# SPDX-License-Identifier: BSD-3-Clause
"""A run driven by a combo `/run` is measured from what the flow spent.

pi runs `/run` before any model turn, so the main session's stream carries no usage at
all: every token is in combo's own subagents, and only its run directory says so. The
fake agent below leaves that directory as combo v0.3.0 does.

Nothing here spends a token.
"""

import json
import sys

import pytest

from trysquare import agent, combo

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
