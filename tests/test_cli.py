"""The command line, and the guards that live in it.

Nothing here spends a token: `run` is exercised through `--dry-run`, which is the
mode that exists precisely so wiring can be checked without paying for it.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.gitrepo import a_repo
from tests.test_scenario import MINIMAL
import pytest

from trysquare import cli, parity, repo
from trysquare import assay
from trysquare.assay import Assay, CannotJudge
from trysquare.cli import build_parser, main
from trysquare.scenario import parse

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = str(ROOT / "tests" / "fixtures" / "matrix.toml")
MACHINE = ROOT / "tests" / "fixtures" / "machine.toml"


def out() -> Path:
    return Path(tempfile.mkdtemp())


def compared(argv) -> tuple[int, str]:
    """Runs the CLI in process, and returns its exit code with everything it said."""
    import contextlib
    import io

    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue() + stderr.getvalue()


class TestWhatThePlanSays:
    """A header printed before every run, so its grammar is part of the output.

    `counted` itself is checked in `test_measure.py`, beside where it lives.
    """

    def test_a_matrix_of_one_is_singular_on_both_sides(self):
        assert cli.matrix_line(1, 1) == "1 cell x 1 repetition"

    def test_more_than_one_takes_the_s(self):
        assert cli.matrix_line(2, 3) == "2 cells x 3 repetitions"


class TestParser:
    def test_output_is_required_for_a_run(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", SCENARIO])

    def test_every_subcommand_exists(self):
        actions = [a for a in build_parser()._subparsers._group_actions if hasattr(a, "choices")]
        available = set(actions[0].choices)
        assert available == {
            "init",
            "run",
            "validate",
            "render",
            "replay",
            "compare",
            "parity",
            "form",
        }

    def test_the_three_answers_cannot_be_given_at_once(self):
        """They are the three answers to one question, so two of them says two things -
        refused by argparse rather than by a precedence rule nobody can see."""
        for pair in (
            ("--resume", "--overwrite"),
            ("--extend", "--overwrite"),
            ("--resume", "--extend"),
            ("--resume", "--overwrite", "rule / off"),
        ):
            with pytest.raises(SystemExit):
                build_parser().parse_args(["run", SCENARIO, "-o", "x", *pair])

    def test_overwrite_reads_bare_and_by_cell(self):
        """Absence, the whole matrix, and a list of cells are three different answers, and
        `None` is what tells the first two apart - no cell can be named it."""
        from trysquare.cli import replayed_cells

        def parsed(*extra):
            return build_parser().parse_args(["run", SCENARIO, "-o", "x", *extra])

        assert parsed().overwrite is None
        assert replayed_cells(parsed("--overwrite")) == ()
        assert replayed_cells(parsed("--overwrite", "rule / off")) == ("rule / off",)
        assert replayed_cells(
            parsed("--overwrite", "rule / off", "--overwrite", "rule / high")
        ) == ("rule / off", "rule / high")

    def test_a_bare_overwrite_beside_a_named_one_is_the_whole_matrix(self):
        """The broader answer wins, because it is the one that cannot surprise: nothing
        is kept that the operator asked to measure again."""
        from trysquare.cli import replayed_cells

        args = build_parser().parse_args(
            ["run", SCENARIO, "-o", "x", "--overwrite", "rule / off", "--overwrite"]
        )
        assert replayed_cells(args) == ()

    def test_the_bar_can_be_refused_on_every_command_that_draws_one(self):
        parser = build_parser()
        assert not parser.parse_args(["run", SCENARIO, "-o", "x"]).no_progress
        for argv in (
            ["run", SCENARIO, "-o", "x", "--no-progress"],
            ["render", SCENARIO, "-o", "x", "--no-progress"],
            ["replay", "d", "--scenario", SCENARIO, "--no-progress"],
        ):
            assert parser.parse_args(argv).no_progress, argv[0]


class TestValidate:
    """The same refusals as `run`, before an output directory exists."""

    def quietly(self, argv) -> tuple[int, str]:
        import contextlib
        import io

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue() + stderr.getvalue()

    def test_a_sound_scenario_validates_and_writes_nothing(self, tmp_path):
        code, said = self.quietly(["validate", SCENARIO, "--config", str(MACHINE)])
        assert code == 0
        assert "ok: nothing this scenario references is missing" in said
        assert list(tmp_path.iterdir()) == []

    def test_a_missing_referenced_file_is_a_refusal(self, tmp_path):
        """The same preflight as `run`: a missing brick refuses before anything else."""
        scenario = tmp_path / "s.toml"
        scenario.write_text(
            Path(SCENARIO).read_text().replace("../../examples/validator.py", "absent.py")
        )
        code, said = self.quietly(["validate", str(scenario), "--config", str(MACHINE)])
        assert code == 1
        assert "do not exist" in said and "absent.py" in said

    def test_a_config_file_is_told_apart_from_a_scenario(self):
        code, said = self.quietly(["validate", str(MACHINE)])
        assert code == 1
        assert "config" in said


class TestInit:
    """The skeleton is written once, refuses to overwrite, and is not runnable."""

    def quietly(self, argv) -> tuple[int, str]:
        import contextlib
        import io

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue() + stderr.getvalue()

    def test_init_writes_the_skeleton_and_a_config_when_none_exists(self, tmp_path):
        code, said = self.quietly(["init", str(tmp_path)])
        assert code == 0
        for name in ("scenario.toml", "prompt.md", "hypothesis.md", "trysquare.toml"):
            assert (tmp_path / name).is_file(), name
        assert "trysquare validate" in said

    def test_init_never_overwrites(self, tmp_path):
        (tmp_path / "scenario.toml").write_text("mine")
        code, said = self.quietly(["init", str(tmp_path)])
        assert code == 1
        assert "never overwrites" in said
        assert (tmp_path / "scenario.toml").read_text() == "mine"
        assert not (tmp_path / "prompt.md").exists()

    def test_a_fresh_skeleton_refuses_validation_by_name(self, tmp_path):
        """The validator is deliberately not written: nothing runnable may ship."""
        self.quietly(["init", str(tmp_path)])
        code, said = self.quietly(["validate", str(tmp_path / "scenario.toml")])
        assert code == 1
        assert "score.py" in said


class TestShippedExample:
    def test_the_example_scenario_loads_and_dry_runs(self, tmp_path):
        """The same anti-rot mechanism as examples/validator.py: run it for real."""
        example = str(ROOT / "examples" / "scenario.toml")
        argv = ["run", example, "-o", str(tmp_path), "--config", str(MACHINE), "--dry-run"]
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            assert main(argv) == 0
        assert list(tmp_path.iterdir()) == []


class TestDryRun:
    def quietly(self, argv) -> int:
        """The CLI reports to stdout by design; a test should not echo it."""
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            return main(argv)

    def test_a_dry_run_writes_nothing_and_spends_nothing(self):
        directory = out()
        argv = ["run", SCENARIO, "--output", str(directory), "--config", str(MACHINE), "--dry-run"]
        assert self.quietly(argv) == 0
        assert list(directory.iterdir()) == []

    def test_changing_repetitions_targets_another_directory(self):
        """Which is why a quick run cannot corrupt a published matrix."""
        directory = out()
        self.quietly(["run", SCENARIO, "-o", str(directory), "--repetitions", "3", "--dry-run"])
        # Nothing is written by a dry run, so assert on the name the plan reports.
        from trysquare.outputs import experiment_name
        from trysquare.scenario import load

        s = load(SCENARIO)
        assert experiment_name(s, 3).endswith("_n3")
        assert experiment_name(s, 3) != experiment_name(s)


class TestRemoteRepository:
    """A `[repos]` entry that is a URL, without ever reaching a network.

    The host is under `.invalid`, reserved by RFC 2606, so a mistake in the wiring
    cannot accidentally succeed against a real server.
    """

    URL = "https://example.invalid/tiny.git"

    @pytest.fixture(autouse=True)
    def a_remote_config(self, tmp_path):
        self.home = Path(tempfile.mkdtemp())
        self.workdir = self.home / "work"
        self.config = self.home / "trysquare.toml"
        self.config.write_text(
            f'[repos]\ntiny = "{self.URL}"\n'
            f'[harness]\nsubagent = "{self.home}"\n'
            f'[defaults]\nworkdir = "{self.workdir}"\n'
        )

    def plan(self, directory: Path):
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        return runner.resolve(load(SCENARIO), config_mod.load(self.config), directory)

    def test_a_dry_run_against_a_url_touches_neither_disk_nor_network(self):
        """`--dry-run` spends nothing, and reaching a network is spending."""
        import contextlib
        import io

        directory = out()
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                ["run", SCENARIO, "-o", str(directory), "--config", str(self.config), "--dry-run"]
            )
        assert code == 0
        assert list(directory.iterdir()) == []
        assert not (self.workdir / "sources").exists()

    def test_the_plan_names_the_url_rather_than_the_pinned_path(self):
        """A path under $TMPDIR tells the operator nothing about what is measured."""
        import contextlib
        import io

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            main(["run", SCENARIO, "-o", str(out()), "--config", str(self.config), "--dry-run"])
        assert self.URL in printed.getvalue()

    def test_the_pinned_directory_lives_under_the_workdir(self):
        plan = self.plan(out())
        assert plan.repo_source == self.URL
        assert plan.repo_path.parent == self.workdir / "sources"
        assert not plan.repo_path.exists(), "resolve touches no disk"

    def test_an_unreachable_url_fails_before_anything_is_written(self):
        """60 runs discovering the same unreachable URL is 60 empty measures where one
        refusal was owed. This pins the order of the statements in `execute`."""
        import unittest.mock

        from trysquare import repo as repo_mod
        from trysquare import runner

        plan = self.plan(out())
        boom = repo_mod.RepoError("could not read from remote repository")
        with unittest.mock.patch.object(runner.repo_mod, "pin", side_effect=boom):
            with pytest.raises(repo_mod.RepoError) as e:
                runner.execute(plan)
        assert self.URL in str(e.value)
        assert not plan.output.directory.exists(), "no ledger for runs that cannot run"


class TestRunPlan:
    def plan(self, **overrides):
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        s = load(SCENARIO)
        c = config_mod.load(MACHINE)
        return runner.resolve(s, c, out(), overrides=overrides)

    def test_runs_are_interleaved_across_cells(self):
        """Grouped by cell, the first cell would be measured under an idle provider
        and the last under a loaded one, so the duration column would compare our
        own scheduling rather than the configurations."""
        plan = self.plan()
        first_six = [meta["cell"] for _, meta in plan.todo[:6]]
        assert len(set(first_six)) == 6, "the first six runs should be six different cells"
        assert all(meta["repetition"] == 0 for _, meta in plan.todo[:6])

    def test_every_override_is_announced(self):
        plan = self.plan(repetitions=3, concurrency=10)
        joined = " ".join(plan.notes)
        assert "repetitions 10 -> 3" in joined
        assert "concurrency 5 -> 10" in joined

    def test_only_marks_the_matrix_incomplete(self):
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        plan = runner.resolve(load(SCENARIO), config_mod.load(MACHINE), out(), only=("rule / off",))
        assert plan.runs == 10
        assert any("INCOMPLETE" in n for n in plan.notes)

    def test_only_with_a_typo_is_refused_not_filtered(self):
        """Silently matching nothing ran zero runs and read as nothing left to do."""
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        with pytest.raises(RuntimeError, match="did you mean 'rule / off'"):
            runner.resolve(load(SCENARIO), config_mod.load(MACHINE), out(), only=("rule/off",))

    def test_a_missing_config_file_says_to_create_one(self, tmp_path):
        """Not "add the entry": there is no file to add it to, and the message says so."""
        from trysquare import config as config_mod

        with pytest.raises(config_mod.ConfigError, match=f"no {config_mod.CONFIG_NAME} was found"):
            config_mod.Config().repo("my-repo")


def a_measured_matrix(root: Path, repetitions: int, grouped=None, **ledger) -> "Output":  # noqa: F821
    """A matrix of the fixture, every run measured, without spending a token.

    The whole point of carrying runs forward is that it needs nothing but two ledgers and
    a copy, so the feature is testable exactly like this: on disk, offline, free.
    """
    from trysquare.measure import VALID, Run
    from trysquare.outputs import Output
    from trysquare.scenario import load

    output = Output(root, load(SCENARIO), repetitions, grouped=grouped)
    output.prepare()
    state = {**output.initial_state(), **ledger}
    rows = []
    for run_id, meta in output.plan().items():
        state["runs"][run_id] = {**meta, "state": VALID, "attempts": 1}
        rows.append(
            Run(
                id=run_id,
                cell=meta["cell"],
                repetition=meta["repetition"],
                usage={"input": 10, "output": 5},
                duration=3,
                metrics={"delivered": 1.0, "in_scope": 1.0, "tests": 1.0},
            )
        )
        (output.run_dir(run_id) / "diff.patch").write_text(f"work of {run_id}\n")
        output.write_configuration(run_id, {"cell": meta["cell"], "etalon_commit": "a1b2c3d"})
    output.write_measures(rows)
    output.write_state(state)
    return output


class TestCarryingRunsForward:
    """Measuring more repetitions without paying twice for the ones already measured.

    Everything here runs offline: a carry is two ledgers and a copy.
    """

    def resolved(self, root, repetitions, **kwargs):
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        return runner.resolve(
            load(SCENARIO),
            config_mod.load(MACHINE),
            root,
            overrides={"repetitions": repetitions},
            **kwargs,
        )

    def test_a_carry_files_the_runs_under_this_matrix_s_own_layout(self, tmp_path):
        """The two layouts do not merge, so a blind matrix's runs cannot arrive in a
        grouped one still flat: the tree would hold half of each and no reader could tell
        which half was which."""
        from trysquare.outputs import cell_dir

        source = a_measured_matrix(tmp_path, 2, grouped=False)
        plan = self.resolved(tmp_path, 4, resume=True, extend=True, grouped=True)
        plan.output.absorb(plan.carried, {})

        target = plan.output
        assert target.grouped and not source.grouped
        for run_id, meta in source.read_state()["runs"].items():
            landed = target.location(run_id)
            assert landed.parent.name == cell_dir(meta["cell"])
            assert (landed / "diff.patch").is_file()

    def test_the_lower_matrix_holds_the_very_ids_the_higher_asks_for(self, tmp_path):
        """The premise of the whole feature, asserted rather than assumed: a run id is
        `blake2s(scenario/cell/repetition)` and ignores the repetition *count*, so these
        are those runs and not analogues of them."""
        from trysquare.outputs import Output
        from trysquare.scenario import load

        scenario = load(SCENARIO)
        low = Output(tmp_path, scenario, 2).plan()
        high = Output(tmp_path, scenario, 4).plan()
        assert set(low) < set(high)
        assert all(high[run_id] == meta for run_id, meta in low.items())

    def test_only_the_new_repetitions_are_left_to_measure(self, tmp_path):
        a_measured_matrix(tmp_path, 2)
        plan = self.resolved(tmp_path, 4, resume=True, extend=True)
        assert plan.runs == 12, "6 cells x the 2 repetitions that are new"
        assert all(meta["repetition"] >= 2 for _, meta in plan.todo)
        assert any("CARRIED" in n and "_n2" in n for n in plan.notes)

    def test_a_run_that_produced_nothing_is_not_an_acquired_result(self, tmp_path):
        """`empty` is what a resume relaunches, and a carry may not turn it into a result
        by moving it."""
        from trysquare.measure import EMPTY

        source = a_measured_matrix(tmp_path, 2)
        state = source.read_state()
        victim = next(iter(state["runs"]))
        state["runs"][victim]["state"] = EMPTY
        source.write_state(state)

        plan = self.resolved(tmp_path, 4, resume=True, extend=True)
        assert plan.runs == 13
        assert victim in {run_id for run_id, _ in plan.todo}

    def test_a_ledger_entry_whose_row_is_missing_does_not_travel(self, tmp_path):
        """Carrying one would import `unmeasured_note`'s defect into a fresh matrix: a
        ledger counting a run that no table can see, under a full-looking header."""
        source = a_measured_matrix(tmp_path, 2)
        rows = source.read_measures()
        stranded = rows[0].id
        source.write_measures(rows[1:])

        plan = self.resolved(tmp_path, 4, resume=True, extend=True)
        assert plan.runs == 13
        assert stranded in {run_id for run_id, _ in plan.todo}
        assert any("STRANDED" in n for n in plan.notes)

    def test_a_source_that_disagrees_on_what_is_measured_is_refused(self, tmp_path):
        """`thinking` is not in a directory name, so two matrices can share a name
        without sharing what they measured. This is the only place that can catch it."""
        a_measured_matrix(tmp_path, 2, thinking="high")
        with pytest.raises(RuntimeError, match="thinking"):
            self.resolved(tmp_path, 4, resume=True, extend=True)

    def test_a_cell_rewritten_since_the_source_measured_it_condemns_the_carry(self, tmp_path):
        """A carried run is a measured run, so the refusal that protects a resume has to
        reach it too: completing this matrix would otherwise publish two configurations
        under one cell name, which is what the fingerprint exists to catch."""
        source = a_measured_matrix(tmp_path, 2)
        state = source.read_state()
        # What a rewritten delta looks like to the ledger: the cell declares something
        # other than what its runs were measured under.
        state["cells"]["rule / off"] = "0000000000000000"
        source.write_state(state)

        with pytest.raises(RuntimeError, match="rule / off"):
            self.resolved(tmp_path, 4, resume=True, extend=True)

    def test_the_largest_lower_matrix_is_the_source(self, tmp_path):
        """The cheapest carry, and the one an operator would predict."""
        a_measured_matrix(tmp_path, 2)
        a_measured_matrix(tmp_path, 3)
        plan = self.resolved(tmp_path, 4, resume=True, extend=True)
        assert plan.runs == 6
        assert any("_n3" in n for n in plan.notes if "CARRIED" in n)

    def test_extend_with_nothing_to_carry_is_refused_by_name(self, tmp_path):
        """Silently measuring from scratch would look like a flag that worked, which is
        why `--only` with a typo is refused too."""
        with pytest.raises(RuntimeError, match="--extend"):
            self.resolved(tmp_path, 4, resume=True, extend=True)

    def test_resolving_a_carry_writes_nothing(self, tmp_path):
        """`--dry-run` must stay free, so the decision may not touch the disk."""
        source = a_measured_matrix(tmp_path, 2)
        before = sorted(p.name for p in tmp_path.iterdir())
        plan = self.resolved(tmp_path, 4, resume=True, extend=True)
        assert plan.carried is not None
        assert before == sorted(p.name for p in tmp_path.iterdir())
        assert not plan.output.directory.exists()
        assert source.directory.exists()

    def test_absorbing_copies_the_trees_and_leaves_the_source_alone(self, tmp_path):
        """A carried matrix has to be readable on its own - `render`, `replay` and
        `compare` each read one matrix's own tree."""
        source = a_measured_matrix(tmp_path, 2)
        plan = self.resolved(tmp_path, 4, resume=True, extend=True)
        target = plan.output
        target.absorb(plan.carried, {})

        carried = source.read_state()["runs"]
        assert len(target.read_measures()) == 12
        for run_id in carried:
            assert (target.location(run_id) / "diff.patch").read_text() == f"work of {run_id}\n"
            assert (source.location(run_id) / "diff.patch").is_file()
        state = target.read_state()
        assert state["repetitions"] == 4
        assert all(state["runs"][run_id]["carried"] for run_id in carried)
        assert state["carried"][-1]["from"] == source.directory.name
        assert state["carried"][-1]["etalon_commit"] == "a1b2c3d"
        assert 12 == len(target.to_do(state)), "only the new repetitions are left"

    def test_a_matrix_that_already_holds_runs_is_never_imported_into_twice(self, tmp_path):
        """Once extended, `--extend` is the `--resume` it implies. Looking again would let
        a second launch overwrite what the first one carried."""
        a_measured_matrix(tmp_path, 2)
        first = self.resolved(tmp_path, 4, resume=True, extend=True)
        first.output.absorb(first.carried, {})

        again = self.resolved(tmp_path, 4, resume=True, extend=True)
        assert again.carried is None
        assert again.runs == 12, "the carried runs are still out of reach"

    def test_a_second_extension_keeps_the_whole_chain(self, tmp_path):
        """A matrix extended twice must still be readable back to the launch that first
        measured each run."""
        a_measured_matrix(tmp_path, 2)
        middle = self.resolved(tmp_path, 3, resume=True, extend=True)
        middle.output.absorb(middle.carried, {})

        top = self.resolved(tmp_path, 4, resume=True, extend=True)
        top.output.absorb(top.carried, {})
        chain = top.output.read_state()["carried"]
        assert [record["repetitions"] for record in chain] == [2, 3]


class TestAnExtensionIsNeverSilent:
    """Repetitions are declared in advance. Extending bends that, so it is written down
    everywhere a reader could meet the matrix without having seen the terminal."""

    def carried_matrix(self, root):
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        a_measured_matrix(root, 2)
        plan = runner.resolve(
            load(SCENARIO),
            config_mod.load(MACHINE),
            root,
            overrides={"repetitions": 4},
            resume=True,
            extend=True,
        )
        plan.output.absorb(plan.carried, {})
        return plan.output

    def test_the_note_is_read_from_the_ledger_so_every_later_launch_says_it_too(self, tmp_path):
        """Derived from the flag, it would print once and the matrix would look like one
        launch ever after."""
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        self.carried_matrix(tmp_path)
        later = runner.resolve(
            load(SCENARIO),
            config_mod.load(MACHINE),
            tmp_path,
            overrides={"repetitions": 4},
            resume=True,
        )
        assert any("CARRIED" in n for n in later.notes)

    def test_the_synthesis_names_the_source_and_says_two_launches_are_mixed(self, tmp_path):
        """Durations compare within one launch, and an extended matrix holds two."""
        from trysquare.measure import VALID, Run
        from trysquare.scenario import load

        output = self.carried_matrix(tmp_path)
        state = output.read_state()
        rows = output.read_measures()
        for run_id, meta in state["runs"].items():
            if meta["state"] != VALID:
                meta["state"] = VALID
                rows.append(
                    Run(
                        id=run_id,
                        cell=meta["cell"],
                        repetition=meta["repetition"],
                        usage={"input": 10, "output": 5},
                        duration=3,
                        metrics={"delivered": 1.0, "in_scope": 1.0, "tests": 1.0},
                    )
                )
        output.write_measures(rows)
        output.write_state(state)

        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            assert 0 == cli._write_synthesis(output, load(SCENARIO))
        published = (output.directory / "synthesis.md").read_text()
        assert "carried: 12 of 24 runs" in published
        assert "_n2" in published
        assert "This matrix was extended" in published


class TestMeasuringOneVariantAgain:
    """`--overwrite CELL`: one variant measured again, the rest of the matrix kept.

    Offline like every other plan test here, and the last two go through `execute` with
    `one_run` patched, because what a replay writes to the ledger is the half a resolved
    plan cannot show.
    """

    CELL = "careful ticket / high"

    def resolved(self, root, **kwargs):
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        return runner.resolve(
            load(SCENARIO),
            config_mod.load(MACHINE),
            root,
            overrides={"repetitions": 2},
            **kwargs,
        )

    def rewritten(self, output, cell: str) -> None:
        """What a rewritten delta looks like to the ledger: the cell declares something
        other than what its runs were measured under."""
        state = output.read_state()
        state["cells"][cell] = "0000000000000000"
        output.write_state(state)

    def measured(self, plan):
        """`execute` with the measurement replaced by a row nothing else could have
        written, so a re-measured run is told from a kept one by reading it."""
        import unittest.mock

        from trysquare import runner
        from trysquare.measure import VALID, Run

        def one(_plan, run_id, meta):
            return Run(
                id=run_id,
                cell=meta["cell"],
                repetition=meta["repetition"],
                usage={"input": 99, "output": 99},
                duration=7,
                metrics={"delivered": 1.0, "in_scope": 1.0, "tests": 1.0},
                state=VALID,
            )

        with (
            unittest.mock.patch.object(runner, "prepare_source"),
            unittest.mock.patch.object(runner, "one_run", side_effect=one),
        ):
            return runner.execute(plan)

    def test_only_the_named_cell_is_measured_again(self, tmp_path):
        """Every run of it, whatever its result - that is what makes it an overwrite -
        and no run of any other cell."""
        a_measured_matrix(tmp_path, 2)
        plan = self.resolved(tmp_path, replay=(self.CELL,))
        assert {meta["cell"] for _, meta in plan.todo} == {self.CELL}
        assert plan.runs == 2

    def test_two_cells_can_be_named(self, tmp_path):
        a_measured_matrix(tmp_path, 2)
        plan = self.resolved(tmp_path, replay=(self.CELL, "rule / off"))
        assert {meta["cell"] for _, meta in plan.todo} == {self.CELL, "rule / off"}

    def test_the_note_counts_what_is_measured_against_what_is_kept(self, tmp_path):
        """An operator about to spend reads the plan, so the plan says how much of the
        matrix is out of reach of this launch."""
        a_measured_matrix(tmp_path, 2)
        plan = self.resolved(tmp_path, replay=(self.CELL,))
        note = next(n for n in plan.notes if n.startswith("REPLAY"))
        assert "2 runs of careful ticket / high" in note
        assert "10 runs" in note

    def test_a_replay_says_nothing_about_overwriting_the_whole_ledger(self, tmp_path):
        """The OVERWRITE note names a launch that resets every cell, which is the one
        thing a replay does not do."""
        a_measured_matrix(tmp_path, 2)
        plan = self.resolved(tmp_path, replay=(self.CELL,))
        assert not [n for n in plan.notes if n.startswith("OVERWRITE")]

    def test_runs_of_the_cells_it_keeps_are_left_alone(self, tmp_path):
        """Even the ones that produced nothing: a replay restricts the launch, so
        finishing the matrix stays `--resume`'s job and the plan says so."""
        from trysquare.measure import EMPTY

        output = a_measured_matrix(tmp_path, 2)
        state = output.read_state()
        empty = next(rid for rid, m in state["runs"].items() if m["cell"] == "rule / off")
        state["runs"][empty]["state"] = EMPTY
        output.write_state(state)

        plan = self.resolved(tmp_path, replay=(self.CELL,))
        assert empty not in {run_id for run_id, _ in plan.todo}
        assert any("INCOMPLETE" in n and "--resume" in n for n in plan.notes)

    def test_a_matrix_that_holds_nothing_is_measured_partially_and_says_so(self, tmp_path):
        """Nothing is measured *again* and nothing is kept, so there is no replay to
        announce - but five sixths of the matrix will never have launched, which is the
        same incompleteness however it came about."""
        plan = self.resolved(tmp_path, replay=(self.CELL,))
        assert plan.runs == 2
        assert not [n for n in plan.notes if n.startswith("REPLAY")]
        assert any("INCOMPLETE" in n and "10 runs" in n for n in plan.notes)

    def test_an_unknown_cell_is_refused_with_the_closest_match(self, tmp_path):
        """Filtered instead of refused, a typo would measure nothing and read as a matrix
        with nothing left to do - the reason `--only` refuses one too."""
        a_measured_matrix(tmp_path, 2)
        with pytest.raises(RuntimeError, match="did you mean 'rule / off'"):
            self.resolved(tmp_path, replay=("rulle / off",))

    def test_naming_the_cells_twice_is_refused(self, tmp_path):
        """Both flags restrict the launch to the cells they name, and a precedence rule
        between two lists is the kind nobody can see."""
        a_measured_matrix(tmp_path, 2)
        with pytest.raises(RuntimeError, match="--only would name them a second time"):
            self.resolved(tmp_path, replay=(self.CELL,), only=("rule / off",))

    def test_a_cell_it_keeps_that_changed_under_its_name_is_refused(self, tmp_path):
        """It is kept, so publishing would put its old results beside the new ones under
        one cell name - the defect the fingerprint exists to catch."""
        output = a_measured_matrix(tmp_path, 2)
        self.rewritten(output, "rule / off")
        with pytest.raises(RuntimeError, match="rule / off"):
            self.resolved(tmp_path, replay=(self.CELL,))

    def test_the_refusal_names_the_replay_that_resolves_it(self, tmp_path):
        output = a_measured_matrix(tmp_path, 2)
        self.rewritten(output, "rule / off")
        with pytest.raises(RuntimeError, match='--overwrite "rule / off"'):
            self.resolved(tmp_path, resume=True)

    def test_the_cell_being_replayed_may_declare_something_new(self, tmp_path):
        """The whole point: an edited variant is measured again under its new
        declaration, and nothing about it is left to protect."""
        output = a_measured_matrix(tmp_path, 2)
        self.rewritten(output, self.CELL)
        plan = self.resolved(tmp_path, replay=(self.CELL,))
        assert plan.runs == 2

    def test_a_replay_leaves_a_ledger_a_resume_does_not_refuse(self, tmp_path):
        """`execute` loads the ledger off the disk, so it is the one that has to record
        the new declaration. Kept there, the old digest would have the next `--resume`
        refuse the very runs this launch just measured."""
        output = a_measured_matrix(tmp_path, 2)
        self.rewritten(output, self.CELL)
        self.measured(self.resolved(tmp_path, replay=(self.CELL,)))

        state = output.read_state()
        assert state["cells"][self.CELL] == output.fingerprints()[self.CELL]
        assert output.changed_cells(state) == []

    def test_the_rows_of_the_cells_it_keeps_are_untouched(self, tmp_path):
        """Kept means kept whole: the row in measures.json as much as the ledger entry."""
        output = a_measured_matrix(tmp_path, 2)
        before = {r.id: r for r in output.read_measures()}
        self.measured(self.resolved(tmp_path, replay=(self.CELL,)))

        after = {r.id: r for r in output.read_measures()}
        assert set(after) == set(before)
        kept = [rid for rid, r in after.items() if r.cell != self.CELL]
        assert len(kept) == 10
        assert all(after[rid] == before[rid] for rid in kept)
        assert all(r.duration == 7 for r in after.values() if r.cell == self.CELL)


class TestAskingBeforeSpending:
    """The question a launch asks when results already exist, and everything that has to
    keep working without one."""

    def settled(self, root, *extra, answer=None, terminal=True):
        """`settle_prior` with the terminal and the answer injected, so no test needs one."""
        from trysquare.scenario import load

        args = build_parser().parse_args(["run", SCENARIO, "-o", str(root), *extra])
        asked = []

        def asker(question, answers):
            asked.append((question, answers))
            return answer

        settled = cli.settle_prior(args, load(SCENARIO), asker=asker, terminal=lambda: terminal)
        return settled, asked

    def test_off_a_terminal_the_announced_overwrite_is_kept(self, tmp_path):
        """The path every scripted launch and every other test in this file takes: no
        question, a fresh ledger, and the OVERWRITE note `resolve` already prints."""
        a_measured_matrix(tmp_path, 10)
        settled, asked = self.settled(tmp_path, terminal=False)
        assert settled == (False, False)
        assert asked == []

    def test_a_dry_run_decides_nothing(self, tmp_path):
        """Its whole promise is a free look at what a command would do."""
        a_measured_matrix(tmp_path, 10)
        settled, asked = self.settled(tmp_path, "--dry-run")
        assert settled == (False, False)
        assert asked == []

    def test_nothing_on_disk_is_not_a_question(self, tmp_path):
        settled, asked = self.settled(tmp_path)
        assert settled == (False, False)
        assert asked == []

    def test_a_flag_is_never_second_guessed(self, tmp_path):
        a_measured_matrix(tmp_path, 2)
        for flags, expected in (
            (("--resume",), (True, False)),
            (("--overwrite",), (False, False)),
            # A named overwrite is a flag too: the cells it names are the answer, so
            # there is nothing left to ask.
            (("--overwrite", "rule / off"), (False, False)),
            (("--repetitions", "4", "--extend"), (True, True)),
        ):
            settled, asked = self.settled(tmp_path, *flags)
            assert settled == expected, flags
            assert asked == [], flags

    def test_the_difference_is_a_resume_when_this_matrix_holds_runs(self, tmp_path):
        from trysquare.ask import DIFFERENCE
        from trysquare.measure import EMPTY

        output = a_measured_matrix(tmp_path, 10)
        state = output.read_state()
        state["runs"][next(iter(state["runs"]))]["state"] = EMPTY
        output.write_state(state)

        settled, asked = self.settled(tmp_path, answer=DIFFERENCE)
        assert settled == (True, False)
        assert "59 runs that produced a result and 1 that produced nothing" in asked[0][0][0]

    def test_the_difference_is_an_extend_when_only_a_lower_matrix_exists(self, tmp_path):
        from trysquare.ask import DIFFERENCE

        a_measured_matrix(tmp_path, 2)
        settled, asked = self.settled(tmp_path, "--repetitions", "4", answer=DIFFERENCE)
        assert settled == (True, True)
        assert "_n2" in " ".join(asked[0][0])

    def test_this_matrix_s_own_ledger_comes_before_a_lower_one(self, tmp_path):
        """The nearer fact, and `--resume` is its answer. Pulling a lower matrix into one
        already under way is coherent, but it has to be typed."""
        from trysquare.ask import DIFFERENCE

        a_measured_matrix(tmp_path, 2)
        a_measured_matrix(tmp_path, 4)
        settled, asked = self.settled(tmp_path, "--repetitions", "4", answer=DIFFERENCE)
        assert settled == (True, False)
        assert "_n2" not in " ".join(asked[0][0])

    def test_everything_is_what_the_command_always_did(self, tmp_path):
        from trysquare.ask import EVERYTHING

        a_measured_matrix(tmp_path, 10)
        settled, _ = self.settled(tmp_path, answer=EVERYTHING)
        assert settled == (False, False)

    def test_a_lower_matrix_that_cannot_be_carried_is_not_a_question(self, tmp_path):
        """No answer could make its runs carryable, and the launch says why on its own."""
        a_measured_matrix(tmp_path, 2, thinking="high")
        settled, asked = self.settled(tmp_path, "--repetitions", "4")
        assert settled == (False, False)
        assert asked == []

    def test_aborting_writes_nothing_and_does_not_report_success(self, tmp_path):
        """A launch that measured nothing must not read as one that did."""
        from unittest.mock import patch

        a_measured_matrix(tmp_path, 2)
        argv = [
            *("run", SCENARIO, "-o", str(tmp_path)),
            *("--config", str(MACHINE), "--repetitions", "4"),
        ]
        with patch.object(cli, "settle_prior", return_value=None):
            code, said = compared(argv)
        assert code == 1
        assert "nothing was spent" in said
        assert not (tmp_path / "matrix_etalon-v1_test-provider_test-model_n4").exists()

    def test_the_question_is_asked_once_whatever_until_complete_does(self, tmp_path):
        """`resolve` runs once per pass, so a question inside it would be asked once per
        pass - and would make a pure function block on a human."""
        from unittest.mock import patch

        a_measured_matrix(tmp_path, 2)
        argv = [
            *("run", SCENARIO, "-o", str(tmp_path), "--config", str(MACHINE)),
            *("--repetitions", "2", "--resume", "--until-complete", "3"),
        ]
        with patch.object(cli, "settle_prior", return_value=(True, False)) as settle:
            code, said = compared(argv)
        assert code == 0 and "nothing to do" in said
        assert settle.call_count == 1


class TestPreRunHonesty:
    """What a launch says before anything is spent."""

    def resolved(self, root, **kwargs):
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        return runner.resolve(load(SCENARIO), config_mod.load(MACHINE), root, **kwargs)

    def test_relaunching_an_existing_experiment_is_announced(self, tmp_path):
        """Without --resume the ledger is reset, and that must never be a surprise."""
        from trysquare.outputs import Output
        from trysquare.scenario import load

        o = Output(tmp_path, load(SCENARIO))
        o.prepare()
        o.write_state(o.initial_state())

        plan = self.resolved(tmp_path)
        assert any("OVERWRITE" in n and "--resume" in n for n in plan.notes)
        resumed = self.resolved(tmp_path, resume=True)
        assert not any("OVERWRITE" in n for n in resumed.notes)

    def test_a_finished_experiment_warns_differently(self, tmp_path):
        from trysquare.outputs import Output
        from trysquare.scenario import load

        o = Output(tmp_path, load(SCENARIO))
        o.prepare()
        state = o.initial_state()
        for meta in state["runs"].values():
            meta["state"] = "valid"
        o.write_state(state)

        plan = self.resolved(tmp_path)
        assert any("OVERWRITE" in n and "finished" in n for n in plan.notes)

    def measured_without(self, root, cell: str) -> None:
        """A finished matrix from before the scenario declared `cell`."""
        from trysquare.outputs import Output
        from trysquare.scenario import load

        o = Output(root, load(SCENARIO))
        o.prepare()
        state = o.initial_state()
        state["runs"] = {
            rid: meta | {"state": "valid"}
            for rid, meta in state["runs"].items()
            if meta["cell"] != cell
        }
        o.write_state(state)

    def a_finished_matrix(self, root) -> None:
        from trysquare.outputs import Output
        from trysquare.scenario import load

        o = Output(root, load(SCENARIO))
        o.prepare()
        state = o.initial_state()
        for meta in state["runs"].values():
            meta["state"] = "valid"
        o.write_state(state)

    def rewritten(self, root, **kwargs):
        """The same scenario with its baseline prompt rewritten, resolved against root.

        The replacement carries no `/` and no `.md`, so preflight - which runs first -
        does not take it for a path that does not exist.
        """
        from dataclasses import replace

        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        scenario = load(SCENARIO)
        edited = replace(scenario, task=scenario.task | {"prompt": "fix it, differently"})
        return runner.resolve(edited, config_mod.load(MACHINE), root, **kwargs)

    def test_a_cell_that_changed_under_its_name_is_refused_by_a_resume(self, tmp_path):
        """The operator's next move is a rename or measuring those cells again, so the
        refusal names the cell and both ways out - the second one as a command, since it
        is one the operator can only assemble by reading the list back."""
        self.a_finished_matrix(tmp_path)
        with pytest.raises(RuntimeError) as refusal:
            self.rewritten(tmp_path, resume=True)
        said = str(refusal.value)
        assert "nothing / off" in said
        assert "Rename the cell" in said and '--overwrite "nothing / off"' in said

    def test_relaunching_without_resume_is_not_refused_when_a_cell_changed(self, tmp_path):
        """Nothing is kept, so nothing can be published twice; OVERWRITE already says
        the ledger is reset."""
        self.a_finished_matrix(tmp_path)
        plan = self.rewritten(tmp_path)
        assert plan.runs == 60

    def test_resuming_a_scenario_that_grew_a_cell_measures_only_that_cell(self, tmp_path):
        """Adding a variant to a published matrix costs the variant, not the matrix."""
        self.measured_without(tmp_path, "careful ticket / high")
        plan = self.resolved(tmp_path, resume=True)
        assert {meta["cell"] for _, meta in plan.todo} == {"careful ticket / high"}
        assert plan.runs == 10

    def test_a_cell_the_ledger_does_not_know_is_announced(self, tmp_path):
        """Sixty runs about to become ten is the kind of saving nobody takes on faith,
        so the plan says it before anything is spent."""
        self.measured_without(tmp_path, "careful ticket / high")
        note = next(n for n in self.resolved(tmp_path, resume=True).notes if "ADDED" in n)
        assert "careful ticket / high" in note
        assert "10 runs" in note and "50 runs" in note

    def test_an_unchanged_scenario_announces_no_drift(self, tmp_path):
        """A note printed on every ordinary resume would train the eye to skip it."""
        from trysquare.outputs import Output
        from trysquare.scenario import load

        o = Output(tmp_path, load(SCENARIO))
        o.prepare()
        o.write_state(o.initial_state())
        notes = self.resolved(tmp_path, resume=True).notes
        assert not any("ADDED" in n or "STALE" in n for n in notes)

    def test_a_cell_the_scenario_no_longer_declares_is_announced_as_stale(self, tmp_path):
        """Its measured runs are still rendered, so their cell must be named somewhere."""
        from trysquare.outputs import Output
        from trysquare.scenario import load

        o = Output(tmp_path, load(SCENARIO))
        o.prepare()
        state = o.initial_state()
        state["runs"]["deadbeef"] = {"cell": "witness", "repetition": 0, "state": "valid"}
        o.write_state(state)
        note = next(n for n in self.resolved(tmp_path, resume=True).notes if "STALE" in n)
        assert "witness" in note

    def test_the_duration_bound_is_declared_arithmetic(self, tmp_path):
        from trysquare.cli import _forecast

        plan = self.resolved(tmp_path)
        said = "\n".join(_forecast(plan))
        # 60 runs, 5 at a time, 900s each: 12 batches x 15 min.
        assert "at most ~180 min: 60 runs, 5 at a time, 900s timeout each" in said
        assert "spend: no archived run to estimate from" in said

    def test_the_spend_estimate_comes_from_the_archive_alone(self, tmp_path):
        from trysquare.cli import _forecast
        from trysquare.measure import Run
        from trysquare.outputs import Output
        from trysquare.scenario import load

        o = Output(tmp_path, load(SCENARIO))
        o.prepare()
        o.write_measures(
            [
                Run(id="aa", cell="c", repetition=0, usage={"cost": 0.40, "input": 1}),
                Run(id="bb", cell="c", repetition=1, usage={"cost": 0.60, "input": 1}),
                Run(id="cc", cell="c", repetition=2, state="empty"),
            ]
        )
        plan = self.resolved(tmp_path)
        said = "\n".join(_forecast(plan))
        assert "median $0.50 over 2 valid runs" in said

    def archived(self, root, *usages: dict):
        from trysquare.measure import Run
        from trysquare.outputs import Output
        from trysquare.scenario import load

        o = Output(root, load(SCENARIO))
        o.prepare()
        o.write_measures(
            [Run(id=f"r{i}", cell="c", repetition=i, usage=u) for i, u in enumerate(usages)]
        )

    def said(self, root):
        from trysquare.cli import _forecast

        return "\n".join(_forecast(self.resolved(root)))

    def test_an_archive_without_prices_is_not_an_empty_archive(self, tmp_path):
        """Measured against a real provider: `pi` reported `cost: 0.0` on every run, and
        `0.0` is falsy, so a matrix holding six valid runs still said there was nothing to
        estimate from. It sent the operator to go and measure what they had measured."""
        priceless = {"cost": 0.0, "input": 12_000, "output": 300, "turns": 6}
        self.archived(tmp_path, dict(priceless), dict(priceless))
        said = self.said(tmp_path)
        assert "no archived run to estimate from" not in said
        assert "no price" in said

    def test_an_archive_without_prices_forecasts_in_tokens(self, tmp_path):
        """A provider that reports no price still reports tokens, and the same estimate
        discipline applies to them: the median of what this experiment already holds."""
        priceless = {"cost": 0.0, "input": 12_000, "output": 300, "turns": 6}
        self.archived(tmp_path, dict(priceless), dict(priceless))
        said = self.said(tmp_path)
        assert "12 000 in / 300 out" in said
        assert "720 000 in / 18 000 out" in said

    def test_a_partly_priced_archive_says_how_many_carried_one(self, tmp_path):
        """`over 2 valid runs` read as "there are two", when there were four."""
        self.archived(
            tmp_path,
            {"cost": 0.50, "input": 1, "output": 1, "turns": 1},
            {"cost": 0.0, "input": 1, "output": 1, "turns": 1},
        )
        assert "over 1 of 2 valid runs" in self.said(tmp_path)

    def test_a_dry_run_names_the_missing_binary(self, monkeypatch, capsys):
        from trysquare import cli as cli_mod

        monkeypatch.setattr(cli_mod.agent_mod, "available", lambda: False)
        code = main(["run", SCENARIO, "-o", str(out()), "--config", str(MACHINE), "--dry-run"])
        assert code == 0
        assert "is not on PATH: a real run will refuse" in capsys.readouterr().out


class TestUntilComplete:
    """Bounded auto-resume: only what produced nothing, and never past the bound."""

    METRICS = {"delivered": True, "in_scope": True, "tests": True, "touched": 1, "documented": True}

    def fake_execute(self, passes, empty_on_first):
        """A stand-in that persists state and measures exactly as execute() does."""
        from trysquare.measure import EMPTY, VALID, Run

        def execute(plan, on_run=None):
            plan.output.prepare()
            state = plan.output.load_or_create_state(plan.overrides)
            passes.append([rid for rid, _ in plan.todo])
            done = []
            for rid, meta in plan.todo:
                empty = empty_on_first(meta, len(passes))
                run = Run(
                    id=rid,
                    cell=meta["cell"],
                    repetition=meta["repetition"],
                    usage={} if empty else {"input": 5, "output": 5, "turns": 1, "cost": 0.1},
                    metrics={} if empty else dict(self.METRICS),
                    state=EMPTY if empty else VALID,
                )
                plan.output.record(state, rid, run)
                done.append(run)
            existing = {r.id: r for r in plan.output.read_measures()}
            for run in done:
                existing[run.id] = run
            plan.output.write_measures(list(existing.values()))
            plan.output.summarise(state)
            plan.output.write_state(state)
            return done

        return execute

    def launched(self, tmp_path, monkeypatch, empty_on_first, bound="3") -> tuple[int, str, list]:
        import contextlib
        import io

        from trysquare import cli as cli_mod

        passes: list = []
        monkeypatch.setattr(
            cli_mod.runner_mod, "execute", self.fake_execute(passes, empty_on_first)
        )
        monkeypatch.setattr(cli_mod.agent_mod, "available", lambda: True)
        argv = [
            "run",
            SCENARIO,
            "-o",
            str(tmp_path),
            "--config",
            str(MACHINE),
            "--repetitions",
            "2",
            "--until-complete",
            bound,
            "--no-progress",
        ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(argv)
        return code, stdout.getvalue(), passes

    def test_a_second_pass_relaunches_only_what_produced_nothing(self, tmp_path, monkeypatch):
        code, said, passes = self.launched(
            tmp_path, monkeypatch, empty_on_first=lambda meta, n: n == 1 and meta["repetition"] == 0
        )
        assert code == 0
        assert [len(p) for p in passes] == [12, 6]
        assert set(passes[1]) < set(passes[0])
        assert "pass 2 of at most 3: 6 runs produced nothing" in said

    def test_nothing_missing_means_no_second_pass(self, tmp_path, monkeypatch):
        code, said, passes = self.launched(
            tmp_path, monkeypatch, empty_on_first=lambda meta, n: False
        )
        assert code == 0
        assert [len(p) for p in passes] == [12]
        assert "pass 2" not in said

    def test_the_bound_holds_and_the_leftovers_are_said(self, tmp_path, monkeypatch):
        code, said, passes = self.launched(
            tmp_path, monkeypatch, empty_on_first=lambda meta, n: True, bound="2"
        )
        # Every run of every pass stays empty: two passes, then say what is left.
        assert [len(p) for p in passes] == [12, 12]
        assert "12 runs still produced nothing after 2 passes" in said

    def test_attempts_accumulate_across_passes(self, tmp_path, monkeypatch):
        self.launched(
            tmp_path, monkeypatch, empty_on_first=lambda meta, n: n == 1 and meta["repetition"] == 0
        )
        state = json.loads(next((tmp_path).glob("*_n2/state.json")).read_text())
        twice = [m for m in state["runs"].values() if m["attempts"] == 2]
        assert len(twice) == 6


class TestCompletionOrder:
    """Runs are reported as they finish, and written in the order they were planned.

    Both halves matter, and they pull in opposite directions. Reporting has to follow
    completion or one slow run holds back the ledger of everything that finished behind
    it - an interruption would then lose work that was done. Writing must not, because
    `verdict.gap_interval` resamples `measures.json` with `random.choices`, which draws
    **by index**: two identical matrices whose rows landed in a different order would
    publish different bounds under the same fixed seed. A race must not reach a verdict.
    """

    def plan_and_runs(self, watch=None):
        import time
        import unittest.mock

        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.measure import VALID, Run
        from trysquare.scenario import load

        plan = runner.resolve(
            load(SCENARIO),
            config_mod.load(MACHINE),
            out(),
            overrides={"repetitions": 1, "concurrency": 6},
        )
        slot = {rid: i for i, (rid, _) in enumerate(plan.todo)}

        def slowest_first(_plan, run_id, meta):
            # The run submitted first finishes last, so completion order is the exact
            # reverse of submission order and the two orders cannot be confused.
            time.sleep(0.02 * (len(slot) - slot[run_id]))
            return Run(
                id=run_id,
                cell=meta["cell"],
                repetition=meta["repetition"],
                usage={"input": 1, "output": 1, "turns": 1, "retries": 0},
                duration=1,
                metrics={},
                state=VALID,
            )

        reported = []

        def report(run):
            reported.append(run)
            if watch is not None:
                # The plan is handed over, so a watcher can read what is on disk at
                # the exact moment a run is reported.
                watch(plan, run)

        with (
            unittest.mock.patch.object(runner, "prepare_source"),
            unittest.mock.patch.object(runner, "one_run", side_effect=slowest_first),
        ):
            done = runner.execute(plan, on_run=report)
        return plan, done, reported

    def test_measures_keep_plan_order_whatever_the_completion_order(self):
        plan, done, _ = self.plan_and_runs()
        assert [r.id for r in done] == [rid for rid, _ in plan.todo]
        written = plan.output.read_measures()
        assert [r.id for r in written] == [rid for rid, _ in plan.todo]

    def test_a_run_is_reported_the_moment_it_finishes(self):
        plan, _, reported = self.plan_and_runs()
        assert [r.id for r in reported] == [rid for rid, _ in reversed(plan.todo)], (
            "reporting followed submission order, so the slowest run gated the rest"
        )

    def test_a_finished_run_is_in_measures_before_the_next_one_is_reported(self):
        """What an interrupt keeps. Measured on a real matrix: a Ctrl-C left two runs
        `valid` in `state.json` with no row in `measures.json`, and they were then
        unreachable - `--resume` skips what produced something - so the matrix
        published `complete` over six runs of the eight that were paid for."""
        on_disk = {}

        def watch(plan, run):
            on_disk[run.id] = {r.id for r in plan.output.read_measures()}

        self.plan_and_runs(watch=watch)
        for run_id, measured in on_disk.items():
            assert run_id in measured, f"{run_id} was reported before its row was written"

    def test_measures_keep_plan_order_at_every_intermediate_write(self):
        """Rows are written many times now, and every one of them has to be ordered:
        `verdict.gap_interval` draws by index, so a matrix read between two runs must
        not be in a different order from the same matrix read at the end."""
        snapshots = []

        def watch(plan, _run):
            snapshots.append([r.id for r in plan.output.read_measures()])

        plan, _, _ = self.plan_and_runs(watch=watch)
        planned = [rid for rid, _ in plan.todo]
        for rows in snapshots:
            assert rows == [rid for rid in planned if rid in set(rows)]


class TestInstalledCommand:
    """The `trysquare` name, as a shell gets it.

    A console script is declared in one file and implemented in another, and
    nothing at build time checks that the two agree: a renamed module ships a
    command that raises on the first character typed.
    """

    def declared_target(self) -> str:
        import tomllib

        with open(ROOT / "pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]["scripts"]["trysquare"]

    def test_the_declared_entry_point_resolves_to_a_callable(self):
        import importlib

        module, _, attribute = self.declared_target().partition(":")
        resolved = getattr(importlib.import_module(module), attribute)
        assert callable(resolved)

    def test_the_command_delegates_to_the_shared_subcommands(self):
        """Two launch paths that could diverge would be two tools under one name."""
        from trysquare.scripts import cli_trysquare

        assert cli_trysquare.run_command is main

    def test_no_subcommand_prints_help_rather_than_failing_silently(self):
        import contextlib
        import io

        from trysquare.scripts.cli_trysquare import main as installed

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = installed([])
        assert code == 2
        assert "run" in out.getvalue()

    def test_an_interrupt_names_the_flag_that_resumes(self):
        """Ctrl-C during a matrix is normal. A traceback there hides the fact that
        the completed runs are on disk and need not be paid for twice."""
        import contextlib
        import io
        import unittest.mock

        from trysquare.scripts import cli_trysquare

        err = io.StringIO()
        with unittest.mock.patch.object(
            cli_trysquare, "run_command", side_effect=KeyboardInterrupt
        ):
            with contextlib.redirect_stderr(err):
                code = cli_trysquare.main(["run", SCENARIO, "-o", str(out())])
        assert code == 130
        assert "--resume" in err.getvalue()


class TestCompare:
    def experiment(self, root, name, etalon="v1", cells=("base", "rule"), hit=True) -> Path:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "state.json").write_text(json.dumps({"etalon": etalon, "runs": {}}))
        rows = [
            {
                "id": f"{c}{i}",
                "cell": c,
                "repetition": i,
                "usage": {"input": 1, "output": 1, "turns": 1},
                "metrics": {"overflow": hit},
                "state": "valid",
            }
            for c in cells
            for i in range(2)
        ]
        (directory / "measures.json").write_text(json.dumps(rows))
        return directory

    def compared(self, argv) -> tuple[int, str]:
        return compared(argv)

    def test_two_experiments_tabulate_side_by_side(self, tmp_path):
        left = self.experiment(tmp_path, "left_n2")
        right = self.experiment(tmp_path, "right_n2", hit=False)
        code, said = self.compared(["compare", str(left), str(right)])
        assert code == 0
        assert "| base | 2/2 -> 0/2 |" in said

    def test_different_etalons_still_refuse(self, tmp_path):
        left = self.experiment(tmp_path, "left_n2")
        right = self.experiment(tmp_path, "right_n2", etalon="v2")
        code, said = self.compared(["compare", str(left), str(right)])
        assert code == 1
        assert "different etalons" in said

    def test_a_side_without_measures_is_said(self, tmp_path):
        left = self.experiment(tmp_path, "left_n2")
        bare = tmp_path / "bare_n2"
        bare.mkdir()
        (bare / "state.json").write_text(json.dumps({"etalon": "v1", "runs": {}}))
        code, said = self.compared(["compare", str(left), str(bare)])
        assert code == 0
        assert "nothing to tabulate" in said


class TestParityLayer1:
    """The classification that keeps a label artefact from reading as a defect."""

    def fixture(self, rows) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "m.json").write_text(json.dumps(rows))
        return d / "m.json"

    def archive(self, sessions: dict) -> Path:
        d = Path(tempfile.mkdtemp())
        for ident, (inp, outp, turns) in sessions.items():
            run = d / ident
            run.mkdir()
            lines = [json.dumps({"type": "session", "version": 3})]
            per_in, per_out = inp // turns, outp // turns
            for i in range(turns):
                extra_in = inp - per_in * turns if i == 0 else 0
                extra_out = outp - per_out * turns if i == 0 else 0
                lines.append(
                    json.dumps(
                        {
                            "type": "message",
                            "message": {
                                "usage": {
                                    "input": per_in + extra_in,
                                    "output": per_out + extra_out,
                                }
                            },
                        }
                    )
                )
            (run / "s.jsonl").write_text("\n".join(lines))
        return d

    def rows(self, values: dict, cell="base"):
        return [
            {
                "identifiant": k,
                "cellule": cell,
                "input": v[0],
                "output": v[1],
                "tours": v[2],
                # The bench's validity condition, so layer 3 has something to aggregate
                # when these rows reach it through the command.
                "note": {"livre": True, "tests": True},
            }
            for k, v in values.items()
        ]

    def test_agreement_is_counted_and_reported(self):
        values = {"base-0": (100, 20, 2), "base-1": (300, 30, 3)}
        problems = parity.layer1(self.fixture(self.rows(values)), self.archive(values)).lines
        assert len(problems) == 1
        assert "2/2 sessions reproduce" in problems[0]

    def test_a_crossed_label_is_named_as_such(self):
        """Exact figures found under another id means the numbers are right and the
        labels are crossed, which is an artefact and not a disagreement."""
        published = {"base-0": (100, 20, 2), "base-1": (300, 30, 3)}
        sessions = {"base-0": (300, 30, 3), "base-1": (100, 20, 2)}
        problems = parity.layer1(self.fixture(self.rows(published)), self.archive(sessions)).lines
        labels = [p for p in problems if p.startswith("LABEL")]
        assert len(labels) == 2
        assert all("no aggregate is affected" in p for p in labels)

    def test_a_cross_cell_swap_is_flagged_as_affecting_aggregates(self):
        """The same artefact across two cells would move published numbers."""
        published = [
            {"identifiant": "a", "cellule": "base", "input": 100, "output": 20, "tours": 2},
            {"identifiant": "b", "cellule": "other", "input": 300, "output": 30, "tours": 3},
        ]
        sessions = {"a": (300, 30, 3), "b": (100, 20, 2)}
        problems = parity.layer1(self.fixture(published), self.archive(sessions)).lines
        assert any("DIFFERENT CELL" in p for p in problems)

    def test_a_real_stripping_difference_is_reported_per_field(self):
        published = {"base-0": (100, 20, 2)}
        sessions = {"base-0": (140, 20, 2)}
        problems = parity.layer1(self.fixture(self.rows(published)), self.archive(sessions)).lines
        assert any("base-0/input" in p for p in problems)

    def test_an_empty_archive_says_so(self, tmp_path):
        problems = parity.layer1(self.fixture(self.rows({"base-0": (1, 1, 1)})), tmp_path).lines
        assert any("no archived session" in p for p in problems)

    def test_a_holding_layer_exits_zero(self):
        """A count is not a defect. Printing both in one list made a parity that
        held exit 1, and a caller had no way to tell the two apart."""
        values = {"base-0": (100, 20, 2)}
        measures = self.fixture(self.rows(values))
        code, said = compared(["parity", str(measures), "--archive", str(self.archive(values))])
        assert code == 0, said
        assert "1/1 sessions reproduce" in said

    def test_a_stripping_difference_exits_one(self):
        measures = self.fixture(self.rows({"base-0": (100, 20, 2)}))
        archive = self.archive({"base-0": (140, 20, 2)})
        code, said = compared(["parity", str(measures), "--archive", str(archive)])
        assert code == 1
        assert "base-0/input" in said


class TestTheReplayContext:
    """A re-scoring needs a context, and until now nobody wrote one.

    `replay` rebuilt a tree and said "run the validators against them", while the only
    context on disk was the **archived** one - absolute paths into a work directory under
    `$TMPDIR` that the system may long have purged. So the archived context named a tree
    that no longer existed and the fresh tree was named by nothing, and the promise that
    justifies archiving a tag and a diff rather than a hundred and fifty working trees
    was not executable.
    """

    def archive(self, cell: str = "rule / high") -> Path:
        run_dir = Path(tempfile.mkdtemp())
        (run_dir / "diff.patch").write_text("")
        (run_dir / "configuration.json").write_text(json.dumps({"cell": cell}))
        (run_dir / "session").mkdir()
        (run_dir / "session" / "attempt-1.jsonl").write_text("{}\n")
        return run_dir

    def reconstituted(self) -> Path:
        """A tree standing for what `replay` just rebuilt: the etalon plus a change."""
        clone = a_repo({"a.js": "one\n", "game/b.js": "two\n"})
        (clone / "a.js").write_text("fixed\n")
        return clone

    def context(self) -> dict:
        scenario = parse(MINIMAL)
        source = a_repo({"a.js": "one\n", "game/b.js": "two\n"})
        path = cli.replay_context(
            self.archive(),
            self.reconstituted(),
            source,
            scenario,
            repo.etalon_files(source, "etalon-v1"),
        )
        return json.loads(path.read_text())

    def test_it_names_a_tree_that_now_exists(self):
        assert Path(self.context()["repo"]).is_dir()

    def test_touched_is_recomputed_from_the_reconstituted_tree(self):
        """Not read from the archive: the diff was just replayed, so the tree itself is
        the authority on what it now contains."""
        assert self.context()["touched"] == ["a.js"]

    def test_the_files_at_the_etalon_come_from_the_tag(self):
        assert self.context()["files"] == ["a.js", "game/b.js"]

    def test_the_archived_session_is_what_it_points_at(self):
        """What makes a metric of process replayable at all: the tool calls are in the
        session, which is archived, and not only in the stream, which is not."""
        assert list(Path(self.context()["session"]).glob("*.jsonl"))

    def test_the_declared_metrics_travel_so_a_gap_can_be_named(self):
        assert self.context()["declared"] == ["overflow", "delivered"]

    def test_every_path_is_absolute(self):
        """A documented promise, and what lets the validator's child run somewhere that is
        deliberately not the measured clone. It held by accident until `replay` handed over
        an archive directory as the operator typed it: the archived session went in
        relative, the child resolved it from its own directory, found nothing, and reported
        that the *agent* had no session."""
        context = self.context()
        for key in ("repo", "session"):
            assert Path(context[key]).is_absolute(), context[key]
        assert Path(context["etalon"]["checkout"]).is_absolute()

    def test_what_a_replay_cannot_give_back_is_absent_rather_than_empty(self):
        context = self.context()
        for key in assay.UNREPLAYABLE:
            assert key not in context, key

    def test_a_validator_reading_a_missing_piece_refuses_by_name(self):
        """Which is why no context version number is needed: "the context carries no
        'response'" tells a reader more than "this archive is version 1" ever could."""
        with pytest.raises(CannotJudge) as raised:
            Assay(self.context()).response
        assert "response" in str(raised.value)

    def test_what_the_archive_does_hold_is_readable(self):
        run = Assay(self.context())
        assert run.touched == {"a.js"}
        assert run.etalon == "etalon-v1"


# A validator whose answer depends on the tree it was handed. That dependence is the
# whole instrument here: a run scored against another run's tree gives the other run's
# answer, which is a wrong measurement and not a crash.
TREE_DEPENDENT = """#!/usr/bin/env python3
import json, sys
from pathlib import Path

context = json.loads(Path(sys.argv[1]).read_text())
tree = Path(context["repo"])
text = (tree / "a.js").read_text() if (tree / "a.js").is_file() else ""
json.dump(
    {
        "metrics": {
            "overflow": "over" in text,
            "delivered": bool(context["touched"]),
            # Undeclared, so unscorable, and recorded all the same. A metric of process
            # reads the archived session, and reading it means resolving the path the
            # context handed over from a working directory the caller never named.
            "session_files": len(list(Path(context["session"]).glob("*.jsonl"))),
        }
    },
    sys.stdout,
)
"""

SCENARIO_TOML = """
[scenario]
name = "t"
[task]
repo = "my-repo"
etalon = "etalon-v1"
prompt = "do the thing"
[agent]
provider = "ilaas"
model = "gemma-4-31b"
thinking = "off"
[protocol]
repetitions = 2
concurrency = 1
timeout = 60
[variants.none]
[[validation]]
mode = "script"
command = "v.py"
metrics = ["overflow", "delivered"]
[verdict]
criterion = "overflow"
reference = "none"
"""


@unittest.skipUnless(shutil.which("git"), "git is not on PATH")
class TestReplayRescore:
    """`replay --rescore`, and the collision that hid under it.

    `replay` wrote every run's context to `clone.parent / "validation"` while cloning
    *into* the work directory, so `clone.parent` was the one `replay/` every run shared and
    sixty contexts overwrote each other. Nothing looked wrong: a distinct `context:` line
    was printed for each.

    **It never produced a wrong score**, and the distinction is worth keeping straight. The
    scoring here writes a context and consumes it in the same turn of the loop, so each run
    was always judged on its own tree. What the collision destroyed was the *artifact*: of
    sixty contexts one survived, so the command's own instruction - point your validators at
    those contexts - was executable for one run out of sixty, and a validator failure could
    not be reproduced by hand. It is also a race waiting for the day this loop runs
    concurrently, which is when it would start producing plausible wrong numbers.

    Offline throughout, and no token: the repository is local, the validator is a script,
    and `--rescore` re-runs scripts only.
    """

    @pytest.fixture(autouse=True)
    def a_local_config(self, tmp_path):
        self.home = Path(tempfile.mkdtemp())
        self.source = a_repo({"a.js": "one\n", "game/b.js": "two\n"})

        (self.home / "trysquare.toml").write_text(
            f'[repos]\nmy-repo = "{self.source}"\n[defaults]\nworkdir = "{self.home / "work"}"\n'
        )
        self.scenario = self.home / "s.toml"
        self.scenario.write_text(SCENARIO_TOML)
        validator = self.home / "v.py"
        validator.write_text(TREE_DEPENDENT)
        validator.chmod(0o755)

        self.experiment = self.home / "results" / "t_etalon-v1_ilaas_gemma-4-31b_n2"
        (self.experiment / "runs").mkdir(parents=True)
        # Two runs that a correct scoring must tell apart: one changed `a.js`, one changed
        # nothing at all.
        self.archive("aaaaaaaa", "overflow\n")
        self.archive("bbbbbbbb", None)
        (self.experiment / "measures.json").write_text(
            json.dumps(
                [
                    self.row("aaaaaaaa", 0, {"overflow": False, "delivered": False}),
                    self.row("bbbbbbbb", 1, {"overflow": False, "delivered": False}),
                ]
            )
        )
        (self.experiment / "state.json").write_text(
            json.dumps(
                {
                    "runs": {
                        "aaaaaaaa": {
                            "cell": "none",
                            "repetition": 0,
                            "state": "validator_failed",
                            "attempts": 1,
                            "detail": "validator 'script' failed",
                        },
                        "bbbbbbbb": {
                            "cell": "none",
                            "repetition": 1,
                            "state": "valid",
                            "attempts": 1,
                        },
                    }
                }
            )
        )

    def row(self, run_id: str, repetition: int, metrics: dict) -> dict:
        return {
            "id": run_id,
            "cell": "none",
            "repetition": repetition,
            "usage": {"input": 11, "output": 2, "turns": 3, "retries": 0},
            "duration": 7,
            "metrics": metrics,
            "reasons": {},
            "state": "validator_failed" if run_id == "aaaaaaaa" else "valid",
            "detail": "",
            "attempts": 1,
        }

    def archive(self, run_id: str, content: str | None) -> None:
        run_dir = self.experiment / "runs" / run_id
        (run_dir / "session").mkdir(parents=True)
        (run_dir / "session" / "attempt-1.jsonl").write_text("{}\n")
        (run_dir / "configuration.json").write_text(json.dumps({"cell": "none"}))
        (run_dir / "diff.patch").write_text(self.patch(content) if content else "")

    def patch(self, content: str) -> str:
        """A real patch, made by git, because `apply_diff` runs `git apply` on it."""
        tree = Path(tempfile.mkdtemp()) / "tree"
        repo.clone(self.source, "etalon-v1", tree)
        (tree / "a.js").write_text(content)
        return repo.diff(tree)

    def replay(self, *extra: str) -> int:
        return main(
            [
                "replay",
                str(self.experiment),
                "--scenario",
                str(self.scenario),
                "--config",
                str(self.home / "trysquare.toml"),
                *extra,
            ]
        )

    def measures(self) -> dict:
        rows = json.loads((self.experiment / "measures.json").read_text())
        return {r["id"]: r for r in rows}

    # --- the collision --------------------------------------------------

    def test_each_run_gets_its_own_context(self):
        assert 0 == self.replay()
        contexts = sorted((self.home / "work" / "replay").glob("*/validation/context.json"))
        assert 2 == len(contexts)

    def test_a_context_names_its_own_tree_and_not_a_neighbour_s(self):
        self.replay()
        for path in (self.home / "work" / "replay").glob("*/validation/context.json"):
            run_id = path.parent.parent.name
            assert run_id == Path(json.loads(path.read_text())["repo"]).parent.name

    def test_two_runs_that_differ_are_scored_differently(self):
        """Each run judged on its own tree, which is what a scoring is for. This one passes
        against the collision too - see the class docstring - and is here because it is the
        property that must hold if the loop is ever made concurrent."""
        assert 0 == self.replay("--rescore")
        rows = self.measures()
        assert rows["aaaaaaaa"]["metrics"]["overflow"] is True
        assert rows["bbbbbbbb"]["metrics"]["overflow"] is False

    def test_a_relative_archive_directory_still_finds_the_session(self):
        """The defect that made every metric of process unjudged on a real matrix. The
        operator types `results/...`, so the archived session went into the context
        relative; the validator's child runs beside the tree, resolved it from there, found
        nothing, and reported that the run had no session - a sentence about the agent.
        """
        here = Path.cwd()
        try:
            os.chdir(self.home)
            assert 0 == main(
                [
                    "replay",
                    "results/t_etalon-v1_ilaas_gemma-4-31b_n2",
                    "--scenario",
                    str(self.scenario),
                    "--config",
                    str(self.home / "trysquare.toml"),
                    "--rescore",
                ]
            )
        finally:
            os.chdir(here)
        assert 1 == self.measures()["aaaaaaaa"]["metrics"]["session_files"]

    # --- what a re-scoring may and may not touch -------------------------

    def test_a_repaired_validator_makes_the_run_valid_again(self):
        self.replay("--rescore")
        assert "valid" == self.measures()["aaaaaaaa"]["state"]

    def test_the_ledger_moves_with_the_measures(self):
        """`state.json` decides whether a synthesis is publishable at all, so a validator
        repaired here would otherwise stay counted among the failures and the matrix would
        keep refusing to publish - the case this flag exists for."""
        self.replay("--rescore")
        state = json.loads((self.experiment / "state.json").read_text())
        assert "valid" == state["runs"]["aaaaaaaa"]["state"]
        assert "detail" not in state["runs"]["aaaaaaaa"]

    def test_cost_is_a_fact_about_the_run_and_is_left_alone(self):
        before = self.measures()["aaaaaaaa"]
        self.replay("--rescore")
        after = self.measures()["aaaaaaaa"]
        for field in ("usage", "duration", "attempts"):
            assert before[field] == after[field], field

    def test_the_synthesis_is_rebuilt_from_the_new_measures(self):
        self.replay("--rescore")
        assert (self.experiment / "synthesis.md").is_file()

    def test_the_synthesis_page_is_written_beside_the_markdown(self):
        self.replay("--rescore")
        page = (self.experiment / "synthesis.html").read_text()
        assert page.startswith("<!doctype html>")
        assert "<table>" in page

    def test_without_rescore_nothing_is_rewritten(self):
        before = (self.experiment / "measures.json").read_text()
        assert 0 == self.replay()
        assert before == (self.experiment / "measures.json").read_text()

    # --- what a re-scoring must not lose in silence ----------------------

    def test_a_metric_that_stops_having_a_value_is_named(self):
        """Value before, none after - here because this validator does not return the
        metric at all, which is the same code path as the case that prompted the check: on
        a real matrix, re-scoring to add one metric turned `documented` unjudged on all six
        runs, since the agent's prose lived in the work directory. Either way the metric
        drops out of the score table and the command used to say only "re-scored"."""
        rows = self.measures()
        for run_id, row in rows.items():
            row["metrics"]["documented"] = True
        (self.experiment / "measures.json").write_text(json.dumps(list(rows.values())))

        code, said = compared(
            [
                "replay",
                str(self.experiment),
                "--scenario",
                str(self.scenario),
                "--config",
                str(self.home / "trysquare.toml"),
                "--rescore",
            ]
        )
        assert code == 0, said
        assert "documented no longer has a value on 2 of 2 runs" in said
        assert "paid for is gone" in said

    def test_a_metric_that_keeps_its_value_is_not_named(self):
        """The report must stay quiet when nothing was lost, or it teaches a reader to
        skip it."""
        code, said = compared(
            [
                "replay",
                str(self.experiment),
                "--scenario",
                str(self.scenario),
                "--config",
                str(self.home / "trysquare.toml"),
                "--rescore",
            ]
        )
        assert code == 0
        assert "no longer has a value" not in said
        assert "paid for is gone" not in said

    # --- the refusals ---------------------------------------------------

    def test_a_directory_that_is_not_this_scenario_s_is_refused(self):
        """A directory name is the experiment's identity, so it is also the check: a
        re-scoring across two matrices would rewrite measures that are not its own."""
        other = self.home / "results" / "other_etalon-v1_ilaas_gemma-4-31b_n2"
        (other / "runs").mkdir(parents=True)
        (other / "runs" / "aaaaaaaa").mkdir()
        (other / "runs" / "aaaaaaaa" / "diff.patch").write_text("")
        assert 1 == main(
            [
                "replay",
                str(other),
                "--scenario",
                str(self.scenario),
                "--config",
                str(self.home / "trysquare.toml"),
                "--rescore",
            ]
        )

    def test_a_run_that_produced_nothing_is_left_alone(self):
        """No scoring turns "produced nothing" into a measurement, and overwriting its
        state would hide an incomplete matrix behind a full-looking one."""
        rows = json.loads((self.experiment / "measures.json").read_text())
        for r in rows:
            if r["id"] == "bbbbbbbb":
                r["state"] = "empty"
        (self.experiment / "measures.json").write_text(json.dumps(rows))
        self.replay("--rescore")
        assert "empty" == self.measures()["bbbbbbbb"]["state"]


@unittest.skipUnless(shutil.which("git"), "git is not on PATH")
class TestParityLayer2:
    """Layer 2 as a command: reconstitute the bench's runs, re-score, compare.

    The archive here is the **bench's** layout, which is not this tool's: rows keyed
    `identifiant` with their scoring under a French `note`, and the sessions at the top
    of the run directory rather than under `session/`.

    Offline and free of tokens throughout, which is the property that makes layer 2
    exact: a local repository, a real patch, a script validator, no judge.
    """

    @pytest.fixture(autouse=True)
    def a_bench_archive(self):
        self.home = Path(tempfile.mkdtemp())
        self.source = a_repo({"a.js": "one\n", "game/b.js": "two\n"})
        (self.home / "trysquare.toml").write_text(
            f'[repos]\nmy-repo = "{self.source}"\n[defaults]\nworkdir = "{self.home / "work"}"\n'
        )
        self.scenario = self.home / "s.toml"
        self.scenario.write_text(SCENARIO_TOML)
        validator = self.home / "v.py"
        validator.write_text(TREE_DEPENDENT)
        validator.chmod(0o755)
        self.archive = self.home / "traces"

    def patch(self, content: str) -> str:
        tree = Path(tempfile.mkdtemp()) / "tree"
        repo.clone(self.source, "etalon-v1", tree)
        (tree / "a.js").write_text(content)
        return repo.diff(tree)

    def published(self, note: dict, content: str = "overflow\n") -> Path:
        """One archived run, and the row the bench published for it.

        `tests` is always published and never re-scored: the bench ran a test suite this
        scenario does not declare, which is the out-of-scope case in its plainest form.
        `tests` and `livre` are the bench's validity condition, without which layer 3
        has no run left to aggregate, so both are always published.
        """
        note = {"tests": True, "livre": True, **note}
        run_dir = self.archive / "base-0"
        run_dir.mkdir(parents=True)
        (run_dir / "diff.patch").write_text(self.patch(content))
        (run_dir / "attempt-1.jsonl").write_text("{}\n")
        measures = self.home / "bench.json"
        measures.write_text(
            json.dumps(
                [
                    {
                        "identifiant": "base-0",
                        "cellule": "base",
                        "input": 0,
                        "output": 0,
                        "tours": 0,
                        "note": note,
                    }
                ]
            )
        )
        return measures

    def parity(self, measures: Path, *extra: str) -> tuple[int, str]:
        return compared(
            [
                "parity",
                str(measures),
                "--archive",
                str(self.archive),
                "--config",
                str(self.home / "trysquare.toml"),
                "--reference",
                "base",
                *extra,
            ]
        )

    def test_a_re_scoring_that_agrees_says_so_and_exits_zero(self):
        measures = self.published({"debordement": True, "livre": True})
        code, said = self.parity(measures, "--scenario", str(self.scenario))
        assert code == 0, said
        assert "1/1 runs re-score to their published metrics exactly" in said

    def test_a_wrong_published_value_is_named_and_blocks(self):
        """The whole point of an exact layer: the gap names the run, the metric and
        both computations, so a reader can decide which of the two is wrong."""
        measures = self.published({"debordement": False, "livre": True})
        code, said = self.parity(measures, "--scenario", str(self.scenario))
        assert code == 1
        assert "base-0/overflow: bench False, here True" in said

    def test_the_scoring_reads_the_reconstituted_tree(self):
        """Not the archive: the diff was just applied, so the tree is the authority.
        A run whose diff changed nothing must score differently from one that did."""
        measures = self.published({"debordement": True}, content="one\n")
        code, said = self.parity(measures, "--scenario", str(self.scenario))
        assert code == 1
        assert "base-0/overflow: bench True, here False" in said
        assert "base-0/delivered: bench True, here False" in said

    def test_the_bench_session_is_found_where_the_bench_put_it(self):
        """The bench kept sessions at the top of the run directory. A metric of process
        reads them, so the layout is read rather than assumed."""
        measures = self.published({"debordement": True, "livre": True})
        self.parity(measures, "--scenario", str(self.scenario))
        context = json.loads(
            next((self.home / "work" / "parity").glob("*/validation/context.json")).read_text()
        )
        assert list(Path(context["session"]).glob("*.jsonl"))

    def test_a_metric_the_bench_judged_is_out_of_scope_rather_than_a_gap(self):
        """`apiStable` was scored by a judge, whose verdict costs tokens and is not in
        this archive. Layer 2 names it once instead of reporting sixty disagreements."""
        measures = self.published({"debordement": True, "apiStable": True})
        code, said = self.parity(measures, "--scenario", str(self.scenario))
        assert code == 0, said
        assert "api_stable: no validator here returns it, so it is out of scope" in said

    def test_without_a_scenario_layer_2_says_what_it_needs(self):
        measures = self.published({"debordement": True, "livre": True})
        code, said = self.parity(measures)
        assert code == 0
        assert "layer 2 needs --scenario" in said
