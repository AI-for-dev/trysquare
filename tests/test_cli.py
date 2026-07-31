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
from trysquare import cli, parity, repo
from trysquare.assay import Assay, CannotJudge
from trysquare.cli import build_parser, main
from trysquare.scenario import parse

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = str(ROOT / "tests" / "fixtures" / "matrix.toml")
MACHINE = ROOT / "tests" / "fixtures" / "machine.toml"


def out() -> Path:
    return Path(tempfile.mkdtemp())


class TestParser(unittest.TestCase):
    def test_output_is_required_for_a_run(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["run", SCENARIO])

    def test_every_subcommand_exists(self):
        actions = [
            a for a in build_parser()._subparsers._group_actions if hasattr(a, "choices")
        ]
        available = set(actions[0].choices)
        self.assertEqual(
            available, {"run", "render", "replay", "compare", "parity", "form"}
        )

    def test_the_bar_can_be_refused_on_every_command_that_draws_one(self):
        parser = build_parser()
        self.assertFalse(parser.parse_args(["run", SCENARIO, "-o", "x"]).no_progress)
        for argv in (
            ["run", SCENARIO, "-o", "x", "--no-progress"],
            ["render", SCENARIO, "-o", "x", "--no-progress"],
            ["replay", "d", "--scenario", SCENARIO, "--no-progress"],
        ):
            self.assertTrue(parser.parse_args(argv).no_progress, argv[0])


class TestDryRun(unittest.TestCase):
    def quietly(self, argv) -> int:
        """The CLI reports to stdout by design; a test should not echo it."""
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            return main(argv)

    def test_a_dry_run_writes_nothing_and_spends_nothing(self):
        directory = out()
        argv = ["run", SCENARIO, "--output", str(directory), "--config", str(MACHINE), "--dry-run"]
        self.assertEqual(self.quietly(argv), 0)
        self.assertEqual(list(directory.iterdir()), [])

    def test_changing_repetitions_targets_another_directory(self):
        """Which is why a quick run cannot corrupt a published matrix."""
        directory = out()
        self.quietly(["run", SCENARIO, "-o", str(directory), "--repetitions", "3", "--dry-run"])
        # Nothing is written by a dry run, so assert on the name the plan reports.
        from trysquare.outputs import experiment_name
        from trysquare.scenario import load

        s = load(SCENARIO)
        self.assertTrue(experiment_name(s, 3).endswith("_n3"))
        self.assertNotEqual(experiment_name(s, 3), experiment_name(s))


class TestRemoteRepository(unittest.TestCase):
    """A `[repos]` entry that is a URL, without ever reaching a network.

    The host is under `.invalid`, reserved by RFC 2606, so a mistake in the wiring
    cannot accidentally succeed against a real server.
    """

    URL = "https://example.invalid/tiny.git"

    def setUp(self):
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
        self.assertEqual(code, 0)
        self.assertEqual(list(directory.iterdir()), [])
        self.assertFalse((self.workdir / "sources").exists())

    def test_the_plan_names_the_url_rather_than_the_pinned_path(self):
        """A path under $TMPDIR tells the operator nothing about what is measured."""
        import contextlib
        import io

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            main(["run", SCENARIO, "-o", str(out()), "--config", str(self.config), "--dry-run"])
        self.assertIn(self.URL, printed.getvalue())

    def test_the_pinned_directory_lives_under_the_workdir(self):
        plan = self.plan(out())
        self.assertEqual(plan.repo_source, self.URL)
        self.assertEqual(plan.repo_path.parent, self.workdir / "sources")
        self.assertFalse(plan.repo_path.exists(), "resolve touches no disk")

    def test_an_unreachable_url_fails_before_anything_is_written(self):
        """60 runs discovering the same unreachable URL is 60 empty measures where one
        refusal was owed. This pins the order of the statements in `execute`."""
        import unittest.mock

        from trysquare import repo as repo_mod
        from trysquare import runner

        plan = self.plan(out())
        boom = repo_mod.RepoError("could not read from remote repository")
        with unittest.mock.patch.object(runner.repo_mod, "pin", side_effect=boom):
            with self.assertRaises(repo_mod.RepoError) as e:
                runner.execute(plan)
        self.assertIn(self.URL, str(e.exception))
        self.assertFalse(plan.output.directory.exists(), "no ledger for runs that cannot run")


class TestRunPlan(unittest.TestCase):
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
        self.assertEqual(len(set(first_six)), 6, "the first six runs should be six different cells")
        self.assertTrue(all(meta["repetition"] == 0 for _, meta in plan.todo[:6]))

    def test_every_override_is_announced(self):
        plan = self.plan(repetitions=3, concurrency=10)
        joined = " ".join(plan.notes)
        self.assertIn("repetitions 10 -> 3", joined)
        self.assertIn("concurrency 5 -> 10", joined)

    def test_only_marks_the_matrix_incomplete(self):
        from trysquare import config as config_mod
        from trysquare import runner
        from trysquare.scenario import load

        plan = runner.resolve(
            load(SCENARIO), config_mod.load(MACHINE), out(), only=("rule / off",)
        )
        self.assertEqual(plan.runs, 10)
        self.assertTrue(any("INCOMPLETE" in n for n in plan.notes))


class TestCompletionOrder(unittest.TestCase):
    """Runs are reported as they finish, and written in the order they were planned.

    Both halves matter, and they pull in opposite directions. Reporting has to follow
    completion or one slow run holds back the ledger of everything that finished behind
    it - an interruption would then lose work that was done. Writing must not, because
    `verdict.gap_interval` resamples `measures.json` with `random.choices`, which draws
    **by index**: two identical matrices whose rows landed in a different order would
    publish different bounds under the same fixed seed. A race must not reach a verdict.
    """

    def plan_and_runs(self):
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
        with unittest.mock.patch.object(runner, "prepare_source"), unittest.mock.patch.object(
            runner, "one_run", side_effect=slowest_first
        ):
            done = runner.execute(plan, on_run=reported.append)
        return plan, done, reported

    def test_measures_keep_plan_order_whatever_the_completion_order(self):
        plan, done, _ = self.plan_and_runs()
        self.assertEqual([r.id for r in done], [rid for rid, _ in plan.todo])
        written = plan.output.read_measures()
        self.assertEqual([r.id for r in written], [rid for rid, _ in plan.todo])

    def test_a_run_is_reported_the_moment_it_finishes(self):
        plan, _, reported = self.plan_and_runs()
        self.assertEqual(
            [r.id for r in reported],
            [rid for rid, _ in reversed(plan.todo)],
            "reporting followed submission order, so the slowest run gated the rest",
        )


class TestInstalledCommand(unittest.TestCase):
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
        self.assertTrue(callable(resolved))

    def test_the_command_delegates_to_the_shared_subcommands(self):
        """Two launch paths that could diverge would be two tools under one name."""
        from trysquare.scripts import cli_trysquare

        self.assertIs(cli_trysquare.run_command, main)

    def test_no_subcommand_prints_help_rather_than_failing_silently(self):
        import contextlib
        import io

        from trysquare.scripts.cli_trysquare import main as installed

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = installed([])
        self.assertEqual(code, 2)
        self.assertIn("run", out.getvalue())

    def test_an_interrupt_names_the_flag_that_resumes(self):
        """Ctrl-C during a matrix is normal. A traceback there hides the fact that
        the completed runs are on disk and need not be paid for twice."""
        import contextlib
        import io
        import unittest.mock

        from trysquare.scripts import cli_trysquare

        err = io.StringIO()
        with unittest.mock.patch.object(cli_trysquare, "run_command", side_effect=KeyboardInterrupt):
            with contextlib.redirect_stderr(err):
                code = cli_trysquare.main(["run", SCENARIO, "-o", str(out())])
        self.assertEqual(code, 130)
        self.assertIn("--resume", err.getvalue())


class TestParityLayer1(unittest.TestCase):
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
            {"identifiant": k, "cellule": cell, "input": v[0], "output": v[1], "tours": v[2]}
            for k, v in values.items()
        ]

    def test_agreement_is_counted_and_reported(self):
        values = {"base-0": (100, 20, 2), "base-1": (300, 30, 3)}
        problems = parity.layer1(self.fixture(self.rows(values)), self.archive(values))
        self.assertEqual(len(problems), 1)
        self.assertIn("2/2 sessions reproduce", problems[0])

    def test_a_crossed_label_is_named_as_such(self):
        """Exact figures found under another id means the numbers are right and the
        labels are crossed, which is an artefact and not a disagreement."""
        published = {"base-0": (100, 20, 2), "base-1": (300, 30, 3)}
        sessions = {"base-0": (300, 30, 3), "base-1": (100, 20, 2)}
        problems = parity.layer1(self.fixture(self.rows(published)), self.archive(sessions))
        labels = [p for p in problems if p.startswith("LABEL")]
        self.assertEqual(len(labels), 2)
        self.assertTrue(all("no aggregate is affected" in p for p in labels))

    def test_a_cross_cell_swap_is_flagged_as_affecting_aggregates(self):
        """The same artefact across two cells would move published numbers."""
        published = [
            {"identifiant": "a", "cellule": "base", "input": 100, "output": 20, "tours": 2},
            {"identifiant": "b", "cellule": "other", "input": 300, "output": 30, "tours": 3},
        ]
        sessions = {"a": (300, 30, 3), "b": (100, 20, 2)}
        problems = parity.layer1(self.fixture(published), self.archive(sessions))
        self.assertTrue(any("DIFFERENT CELL" in p for p in problems))

    def test_a_real_stripping_difference_is_reported_per_field(self):
        published = {"base-0": (100, 20, 2)}
        sessions = {"base-0": (140, 20, 2)}
        problems = parity.layer1(self.fixture(self.rows(published)), self.archive(sessions))
        self.assertTrue(any("base-0/input" in p for p in problems))

    def test_an_empty_archive_says_so(self):
        problems = parity.layer1(self.fixture(self.rows({"base-0": (1, 1, 1)})), Path(tempfile.mkdtemp()))
        self.assertTrue(any("no archived session" in p for p in problems))


class TestTheReplayContext(unittest.TestCase):
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
        self.assertTrue(Path(self.context()["repo"]).is_dir())

    def test_touched_is_recomputed_from_the_reconstituted_tree(self):
        """Not read from the archive: the diff was just replayed, so the tree itself is
        the authority on what it now contains."""
        self.assertEqual(self.context()["touched"], ["a.js"])

    def test_the_files_at_the_etalon_come_from_the_tag(self):
        self.assertEqual(self.context()["files"], ["a.js", "game/b.js"])

    def test_the_archived_session_is_what_it_points_at(self):
        """What makes a metric of process replayable at all: the tool calls are in the
        session, which is archived, and not only in the stream, which is not."""
        self.assertTrue(list(Path(self.context()["session"]).glob("*.jsonl")))

    def test_the_declared_metrics_travel_so_a_gap_can_be_named(self):
        self.assertEqual(self.context()["declared"], ["overflow", "delivered"])

    def test_every_path_is_absolute(self):
        """A documented promise, and what lets the validator's child run somewhere that is
        deliberately not the measured clone. It held by accident until `replay` handed over
        an archive directory as the operator typed it: the archived session went in
        relative, the child resolved it from its own directory, found nothing, and reported
        that the *agent* had no session."""
        context = self.context()
        for key in ("repo", "session"):
            with self.subTest(key=key):
                self.assertTrue(Path(context[key]).is_absolute(), context[key])
        self.assertTrue(Path(context["etalon"]["checkout"]).is_absolute())

    def test_what_a_replay_cannot_give_back_is_absent_rather_than_empty(self):
        context = self.context()
        for key in cli.UNREPLAYABLE:
            self.assertNotIn(key, context, key)

    def test_a_validator_reading_a_missing_piece_refuses_by_name(self):
        """Which is why no context version number is needed: "the context carries no
        'response'" tells a reader more than "this archive is version 1" ever could."""
        with self.assertRaises(CannotJudge) as raised:
            Assay(self.context()).response
        self.assertIn("response", str(raised.exception))

    def test_what_the_archive_does_hold_is_readable(self):
        run = Assay(self.context())
        self.assertEqual(run.touched, {"a.js"})
        self.assertEqual(run.etalon, "etalon-v1")


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
class TestReplayRescore(unittest.TestCase):
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

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.source = a_repo({"a.js": "one\n", "game/b.js": "two\n"})

        (self.home / "trysquare.toml").write_text(
            f'[repos]\nmy-repo = "{self.source}"\n'
            f'[defaults]\nworkdir = "{self.home / "work"}"\n'
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
                        "aaaaaaaa": {"cell": "none", "repetition": 0, "state": "validator_failed",
                                     "attempts": 1, "detail": "validator 'script' failed"},
                        "bbbbbbbb": {"cell": "none", "repetition": 1, "state": "valid",
                                     "attempts": 1},
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
        self.assertEqual(0, self.replay())
        contexts = sorted((self.home / "work" / "replay").glob("*/validation/context.json"))
        self.assertEqual(2, len(contexts))

    def test_a_context_names_its_own_tree_and_not_a_neighbour_s(self):
        self.replay()
        for path in (self.home / "work" / "replay").glob("*/validation/context.json"):
            run_id = path.parent.parent.name
            with self.subTest(run=run_id):
                self.assertEqual(run_id, Path(json.loads(path.read_text())["repo"]).parent.name)

    def test_two_runs_that_differ_are_scored_differently(self):
        """Each run judged on its own tree, which is what a scoring is for. This one passes
        against the collision too - see the class docstring - and is here because it is the
        property that must hold if the loop is ever made concurrent."""
        self.assertEqual(0, self.replay("--rescore"))
        rows = self.measures()
        self.assertEqual(True, rows["aaaaaaaa"]["metrics"]["overflow"])
        self.assertEqual(False, rows["bbbbbbbb"]["metrics"]["overflow"])

    def test_a_relative_archive_directory_still_finds_the_session(self):
        """The defect that made every metric of process unjudged on a real matrix. The
        operator types `results/...`, so the archived session went into the context
        relative; the validator's child runs beside the tree, resolved it from there, found
        nothing, and reported that the run had no session - a sentence about the agent.
        """
        here = Path.cwd()
        try:
            os.chdir(self.home)
            self.assertEqual(
                0,
                main(["replay", "results/t_etalon-v1_ilaas_gemma-4-31b_n2",
                      "--scenario", str(self.scenario),
                      "--config", str(self.home / "trysquare.toml"), "--rescore"]),
            )
        finally:
            os.chdir(here)
        self.assertEqual(1, self.measures()["aaaaaaaa"]["metrics"]["session_files"])

    # --- what a re-scoring may and may not touch -------------------------

    def test_a_repaired_validator_makes_the_run_valid_again(self):
        self.replay("--rescore")
        self.assertEqual("valid", self.measures()["aaaaaaaa"]["state"])

    def test_the_ledger_moves_with_the_measures(self):
        """`state.json` decides whether a synthesis is publishable at all, so a validator
        repaired here would otherwise stay counted among the failures and the matrix would
        keep refusing to publish - the case this flag exists for."""
        self.replay("--rescore")
        state = json.loads((self.experiment / "state.json").read_text())
        self.assertEqual("valid", state["runs"]["aaaaaaaa"]["state"])
        self.assertNotIn("detail", state["runs"]["aaaaaaaa"])

    def test_cost_is_a_fact_about_the_run_and_is_left_alone(self):
        before = self.measures()["aaaaaaaa"]
        self.replay("--rescore")
        after = self.measures()["aaaaaaaa"]
        for field in ("usage", "duration", "attempts"):
            with self.subTest(field=field):
                self.assertEqual(before[field], after[field])

    def test_the_synthesis_is_rebuilt_from_the_new_measures(self):
        self.replay("--rescore")
        self.assertTrue((self.experiment / "synthesis.md").is_file())

    def test_without_rescore_nothing_is_rewritten(self):
        before = (self.experiment / "measures.json").read_text()
        self.assertEqual(0, self.replay())
        self.assertEqual(before, (self.experiment / "measures.json").read_text())

    # --- the refusals ---------------------------------------------------

    def test_a_directory_that_is_not_this_scenario_s_is_refused(self):
        """A directory name is the experiment's identity, so it is also the check: a
        re-scoring across two matrices would rewrite measures that are not its own."""
        other = self.home / "results" / "other_etalon-v1_ilaas_gemma-4-31b_n2"
        (other / "runs").mkdir(parents=True)
        (other / "runs" / "aaaaaaaa").mkdir()
        (other / "runs" / "aaaaaaaa" / "diff.patch").write_text("")
        self.assertEqual(
            1,
            main(["replay", str(other), "--scenario", str(self.scenario),
                  "--config", str(self.home / "trysquare.toml"), "--rescore"]),
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
        self.assertEqual("empty", self.measures()["bbbbbbbb"]["state"])


if __name__ == "__main__":
    unittest.main()
