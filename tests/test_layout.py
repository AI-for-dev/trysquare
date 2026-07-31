"""Where a run's directory goes, and who is allowed to decide.

A run id is an opaque hash, which is exactly right when a human fills a scoring form -
somebody who knows they are grading the best-equipped cell grades it better - and pure
friction the rest of the time, when reading a diff starts by looking a hash up in
`state.json`.

So the tree takes one of two layouts, and the rule is what the scenario declares: blind
when a `form` validator says a human scores, grouped by cell otherwise. What is guarded
here is that one side owns the decision, that a tree already written is never
contradicted, and that every reader - the synthesis links, `replay`, `parity --smoke`,
the form itself - follows the tree it was given rather than a layout of its own.

Nothing here spends a token.
"""

import json
import tomllib
from pathlib import Path

import pytest

from tests.test_cli import compared
from tests.test_scenario import MINIMAL
from trysquare import outputs, parity
from trysquare.measure import VALID, Run
from trysquare.outputs import BLIND, BY_CELL, Output, archived_run_dirs, cell_dir
from trysquare.scenario import load, parse

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = str(ROOT / "tests" / "fixtures" / "matrix.toml")

HAND_SCORED = MINIMAL | {
    "validation": [*MINIMAL["validation"], {"mode": "form", "metrics": ["readable"]}]
}

# A whole scenario as text, because `form` is reached through the CLI and the CLI takes
# a path. Kept minimal: one cell, one repetition, one metric scored by hand.
HAND_SCORED_TOML = """
[scenario]
name = "hand"

[task]
repo = "tiny"
etalon = "etalon-v1"
prompt = "fix the total"

[agent]
provider = "test-provider"
model = "test-model"
thinking = "off"

[protocol]
repetitions = 1
concurrency = 1
timeout = 60

[variants.none]

[[validation]]
mode = "script"
command = "v.py"
metrics = ["delivered"]

[[validation]]
mode = "form"
metrics = ["readable"]

[verdict]
criterion = "readable"
reference = "none"
"""


def one_run(output: Output) -> tuple[str, str]:
    """The first run of a plan, as (id, cell)."""
    run_id, meta = next(iter(output.plan().items()))
    return run_id, meta["cell"]


class TestWhoDecidesTheLayout:
    def test_a_scenario_nobody_scores_by_hand_is_grouped(self, tmp_path):
        """Nothing is protected by hiding the cell when no human reads the tree."""
        output = Output(tmp_path, parse(MINIMAL))
        run_id, cell = one_run(output)
        assert output.layout == BY_CELL
        assert output.location(run_id) == output.runs_dir / cell_dir(cell) / run_id

    def test_a_form_validator_keeps_the_tree_blind(self, tmp_path):
        """The one case where the id earns its opacity."""
        output = Output(tmp_path, parse(HAND_SCORED))
        run_id, _ = one_run(output)
        assert output.layout == BLIND
        assert output.location(run_id) == output.runs_dir / run_id

    @pytest.mark.parametrize("asked,expected", [(True, BY_CELL), (False, BLIND)])
    def test_an_explicit_answer_wins_over_the_rule(self, tmp_path, asked, expected):
        """Both directions: exploring a hand-scored matrix, and blinding one whose
        scenario declares no form at all."""
        assert Output(tmp_path, parse(HAND_SCORED), grouped=asked).layout == expected
        assert Output(tmp_path, parse(MINIMAL), grouped=asked).layout == expected

    def test_the_layout_is_recorded_in_the_ledger(self, tmp_path):
        """Not in the directory name: it changes where bytes land, not what is
        measured. Recorded so every later command reads the same tree."""
        assert Output(tmp_path, parse(MINIMAL)).initial_state()["layout"] == BY_CELL

    def test_a_ledger_decides_for_a_reader_that_does_not_ask(self, tmp_path):
        """`render`, `replay` and `form` take no layout flag - they read the tree. The
        rule applied a second time could disagree with what is on disk."""
        written = Output(tmp_path, parse(MINIMAL), grouped=False)
        written.prepare()
        written.write_state(written.initial_state())

        assert Output(tmp_path, parse(MINIMAL)).layout == BLIND

    def test_a_tree_whose_ledger_is_gone_is_read_by_its_shape(self, tmp_path):
        """A measures-only tree still renders, and guessing wrong there does not fail
        loudly: it reports missing sessions for runs sitting on disk."""
        written = Output(tmp_path, parse(MINIMAL), grouped=False)
        run_id, _ = one_run(written)
        (written.run_dir(run_id) / "diff.patch").write_text("")

        assert Output(tmp_path, parse(MINIMAL)).layout == BLIND

    def test_asking_for_the_layout_a_tree_contradicts_is_refused(self, tmp_path):
        """The two do not merge: every run would be archived twice, once under each,
        with nothing left to say which half this launch measured."""
        written = Output(tmp_path, parse(MINIMAL), grouped=True)
        written.prepare()
        written.write_state(written.initial_state())

        with pytest.raises(RuntimeError, match="do not merge"):
            Output(tmp_path, parse(MINIMAL), grouped=False)

    def test_two_cells_that_name_one_directory_are_refused(self, tmp_path):
        """`a / b` and `a_b` are two cells and one directory name, so a grouped tree
        would file them together and read as one cell measured twice."""
        colliding = MINIMAL | {
            "variants": {"a / b": {}, "a_b": {"context": "context/AGENTS.md"}},
            "verdict": {"criterion": "overflow", "reference": "a / b"},
        }
        with pytest.raises(RuntimeError, match="both name the directory"):
            Output(tmp_path, parse(colliding), grouped=True)

    def test_a_run_the_scenario_does_not_plan_stays_at_the_root(self, tmp_path):
        """A tree written under another scenario has no cell here to file its runs
        under, and inventing one would put them where nobody named."""
        output = Output(tmp_path, parse(MINIMAL))
        assert output.location("deadbeef") == output.runs_dir / "deadbeef"


class TestEveryReaderFollowsTheTree:
    def archived(self, tmp_path, grouped: bool) -> Output:
        output = Output(tmp_path, load(SCENARIO), repetitions=1, grouped=grouped)
        output.prepare()
        for run_id in output.plan():
            (output.run_dir(run_id) / "diff.patch").write_text("")
        output.write_state(output.initial_state())
        return output

    @pytest.mark.parametrize("grouped", [True, False])
    def test_replay_finds_the_runs_in_either_layout(self, tmp_path, grouped):
        """Discovery is by what a run leaves behind, so one walk reads both trees. The
        leaf is the id in both, which is how a re-scoring finds the row to rewrite."""
        output = self.archived(tmp_path, grouped)
        assert {d.name for d in archived_run_dirs(output.directory)} == set(output.plan())

    @pytest.mark.parametrize("grouped,prefix", [(True, "runs/nothing_off/"), (False, "runs/")])
    def test_a_session_link_points_at_the_directory_that_exists(self, tmp_path, grouped, prefix):
        """The synthesis page is read from the experiment directory, so a link missing
        the cell level points at nothing."""
        output = Output(tmp_path, load(SCENARIO), grouped=grouped)
        run_id = outputs.run_id("matrix", "nothing / off", 0)
        assert output.relative_run(run_id) == prefix + run_id

    def test_the_smoke_pass_reads_a_grouped_tree(self, tmp_path):
        """Layer 4 checks that each run's directory holds its diff, its configuration
        and its validation. Against the wrong layout it reports every one missing."""
        experiment = tmp_path / "exp_n1"
        state = {"runs": {}, "complete": True, "thinking": "off", "layout": BY_CELL}
        for cell in ("nothing / off", "rule / off"):
            run_id = outputs.run_id("matrix", cell, 0)
            state["runs"][run_id] = {"cell": cell, "repetition": 0, "state": VALID, "attempts": 1}
            directory = experiment / "runs" / cell_dir(cell) / run_id
            (directory / "validation").mkdir(parents=True)
            (directory / "diff.patch").write_text("")
            (directory / "configuration.json").write_text(json.dumps({"model": "test-model"}))
        (experiment / "state.json").write_text(json.dumps(state))
        (experiment / "measures.json").write_text("[]")
        (experiment / "synthesis.md").write_text("#")

        # Without a workdir the thinking check is skipped and says so, which is the one
        # problem this tree is expected to have.
        problems = parity.layer4(experiment).problems
        assert problems == ["no workdir given: the thinking level check was skipped"]


class TestTheFormSaysWhatItIs:
    """A form is an artifact handed to a person, so what it claims has to be true."""

    def written(self, tmp_path, grouped=None) -> tuple[Path, Path, str]:
        """A measured hand-scored experiment and its form: (scenario, form, output)."""
        scenario_file = tmp_path / "hand-scored.toml"
        scenario_file.write_text(HAND_SCORED_TOML)
        root = tmp_path / "out"

        output = Output(root, load(scenario_file), grouped=grouped)
        output.prepare()
        run_id, cell = one_run(output)
        output.write_state(output.initial_state())
        output.write_measures([Run(id=run_id, cell=cell, repetition=0, state=VALID)])

        code, said = compared(["form", str(scenario_file), "-o", str(root)])
        assert code == 0, said
        return scenario_file, output.directory / "form-hand.toml", said

    def test_the_form_it_writes_is_the_form_ingest_reads(self, tmp_path):
        """Prose TOML cannot parse is a form nobody can hand in. It used to be written
        uncommented, so `--ingest` refused the file the same command had produced."""
        scenario_file, form, _ = self.written(tmp_path)
        filled = tomllib.loads(form.read_text())
        run_id = next(iter(filled["run"]))

        form.write_text(form.read_text().replace("# readable =", "readable = true"))
        code, said = compared(
            ["form", str(scenario_file), "-o", str(tmp_path / "out"), "--ingest", str(form)]
        )
        assert code == 0 and "1 manual metrics merged" in said
        measured = json.loads((form.parent / "measures.json").read_text())
        assert [row["metrics"]["readable"] for row in measured if row["id"] == run_id] == [True]

    def test_a_blind_tree_is_where_the_form_promises_blindness(self, tmp_path):
        _, form, said = self.written(tmp_path)
        text = form.read_text()
        assert "withheld" in text
        assert '/diff.patch"' in text and "none" not in text
        assert "grouped by cell" not in said

    def test_a_grouped_tree_says_the_form_is_not_blind(self, tmp_path):
        """Not a refusal - grouping is asked for on purpose. But a form whose every
        path names the cell may not read as a blind one, in the file or on screen."""
        _, form, said = self.written(tmp_path, grouped=True)
        text = form.read_text()
        assert "NOT BLIND" in text and "withheld" not in text
        assert 'diff = "runs/none/' in text
        assert "! runs are grouped by cell" in said
        assert max(len(line) for line in text.split("\n")) <= 86
