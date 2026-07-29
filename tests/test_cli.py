"""The command line, and the guards that live in it.

Nothing here spends a token: `run` is exercised through `--dry-run`, which is the
mode that exists precisely so wiring can be checked without paying for it.
"""

import json
import tempfile
import unittest
from pathlib import Path

from etabli import parity
from etabli.cli import build_parser, main

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = str(ROOT / "scenarios" / "2x3.toml")


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


class TestDryRun(unittest.TestCase):
    def quietly(self, argv) -> int:
        """The CLI reports to stdout by design; a test should not echo it."""
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            return main(argv)

    def test_a_dry_run_writes_nothing_and_spends_nothing(self):
        directory = out()
        self.assertEqual(self.quietly(["run", SCENARIO, "--output", str(directory), "--dry-run"]), 0)
        self.assertEqual(list(directory.iterdir()), [])

    def test_changing_repetitions_targets_another_directory(self):
        """Which is why a quick run cannot corrupt a published matrix."""
        directory = out()
        self.quietly(["run", SCENARIO, "-o", str(directory), "--repetitions", "3", "--dry-run"])
        # Nothing is written by a dry run, so assert on the name the plan reports.
        from etabli.outputs import experiment_name
        from etabli.scenario import load

        s = load(SCENARIO)
        self.assertTrue(experiment_name(s, 3).endswith("_n3"))
        self.assertNotEqual(experiment_name(s, 3), experiment_name(s))


class TestRunPlan(unittest.TestCase):
    def plan(self, **overrides):
        from etabli import config as config_mod
        from etabli import runner
        from etabli.scenario import load

        s = load(SCENARIO)
        c = config_mod.load(ROOT / "etabli.toml")
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
        from etabli import config as config_mod
        from etabli import runner
        from etabli.scenario import load

        plan = runner.resolve(
            load(SCENARIO), config_mod.load(ROOT / "etabli.toml"), out(), only=("rule / off",)
        )
        self.assertEqual(plan.runs, 10)
        self.assertTrue(any("INCOMPLETE" in n for n in plan.notes))


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


if __name__ == "__main__":
    unittest.main()
