"""The scenarios and config shipped with the tool must stay loadable.

They are the worked examples. A scenario that no longer parses is a broken
example, and a broken example is worse than none.
"""

import unittest
from pathlib import Path

from etabli import config, outputs, scenario, validation

ROOT = Path(__file__).resolve().parent.parent


class TestShippedScenarios(unittest.TestCase):
    def test_every_scenario_loads(self):
        files = sorted((ROOT / "scenarios").glob("*.toml"))
        self.assertTrue(files, "no scenario shipped")
        for f in files:
            with self.subTest(scenario=f.name):
                scenario.load(f)

    def test_the_2x3_matrix_matches_the_published_shape(self):
        """Six cells of ten, which is the matrix parity is checked against."""
        s = scenario.load(ROOT / "scenarios" / "2x3.toml")
        self.assertEqual(len(s.cells), 6)
        self.assertEqual(s.runs, 60)
        self.assertEqual(s.reference, "nothing / off")
        self.assertEqual(
            [c.name for c in s.cells],
            [
                "nothing / off",
                "nothing / high",
                "rule / off",
                "rule / high",
                "careful ticket / off",
                "careful ticket / high",
            ],
            "axis declaration order fixes the order of the rendered table",
        )

    def test_the_2x3_validity_has_both_halves(self):
        """`delivered` alone is not enough: an empty diff passes the tests."""
        s = scenario.load(ROOT / "scenarios" / "2x3.toml")
        self.assertEqual(set(s.verdict["validity"]), {"delivered", "tests"})

    def test_the_subagents_scenario_uses_variants_and_a_judge(self):
        s = scenario.load(ROOT / "scenarios" / "subagents-judge.toml")
        self.assertEqual(len(s.cells), 4)
        self.assertEqual(s.cells[0].name, "nothing")
        self.assertTrue(s.cells[0].is_baseline)
        self.assertIn("judge", [v.mode for v in s.validators])

    def test_the_judge_is_blind_in_that_scenario(self):
        """Its prompt is constant across all four cells, so nothing leaks."""
        s = scenario.load(ROOT / "scenarios" / "subagents-judge.toml")
        report = validation.blindness(s)
        self.assertTrue(report["judge"]["blind"], report)

    def test_the_agent_gate_is_wired_into_the_subagent_cells(self):
        """Forcing the scope alone would leave the shipped agents reachable."""
        s = scenario.load(ROOT / "scenarios" / "subagents-judge.toml")
        self.assertIn("agent-gate", s.bricks)
        for name in ("+subagents", "full stack"):
            self.assertIn("agent-gate", s.cell(name).delta["harness"], name)

    def test_scenarios_carry_no_machine_paths(self):
        """A repository is named logically, so the file is portable."""
        for f in sorted((ROOT / "scenarios").glob("*.toml")):
            with self.subTest(scenario=f.name):
                s = scenario.load(f)
                repo = str(s.task["repo"])
                self.assertNotIn("/", repo, "a logical name, not a path")
                self.assertNotIn("~", repo)


class TestShippedConfig(unittest.TestCase):
    def test_the_config_loads_and_resolves_its_names(self):
        c = config.load(ROOT / "etabli.toml")
        self.assertTrue(c.repo("neon").is_absolute())
        self.assertTrue(c.harness_repo("subagent").is_absolute())

    def test_every_scenario_repo_is_declared_in_the_config(self):
        """Otherwise the example cannot run on the machine it ships with."""
        c = config.load(ROOT / "etabli.toml")
        for f in sorted((ROOT / "scenarios").glob("*.toml")):
            s = scenario.load(f)
            with self.subTest(scenario=f.name):
                c.repo(s.task["repo"])
                for brick in s.bricks.values():
                    if isinstance(brick, dict) and "repo" in brick:
                        c.harness_repo(brick["repo"])


class TestShippedValidator(unittest.TestCase):
    def test_the_neon_validator_is_executable(self):
        """The contract is "any executable", so the bit is part of it."""
        import os

        path = ROOT / "validators" / "neon.py"
        self.assertTrue(os.access(path, os.X_OK), f"{path} is not executable")

    def test_the_validator_declares_what_the_scenarios_expect(self):
        """Every metric a scenario contracts for must be one this validator can
        return, or every run would be invalid for a missing declared metric."""
        produced = {
            "overflow",
            "issues",
            "delivered",
            "in_scope",
            "tests",
            "api_stable",
            "touched",
        }
        for f in sorted((ROOT / "scenarios").glob("*.toml")):
            s = scenario.load(f)
            for v in s.validators:
                if v.mode != "script":
                    continue
                with self.subTest(scenario=f.name):
                    self.assertEqual(set(v.metrics) - produced, set())


if __name__ == "__main__":
    unittest.main()
