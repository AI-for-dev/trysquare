"""The effectful shell: argument building, config rules, output state, resume.

Nothing here launches an agent. What is tested is the part that decides *what*
would be launched and *what* may be relaunched, which is where a measurement is
silently lost or a standard silently bent.
"""

import json
import tempfile
import unittest
from pathlib import Path

from trysquare import agent, config, outputs, repo, validation
from trysquare.measure import EMPTY, VALID, VALIDATOR_FAILED, Run
from trysquare.scenario import parse
from tests.test_scenario import GRID, MINIMAL


class TestArgv(unittest.TestCase):
    def args(self, **kw):
        base = dict(
            prompt="do it",
            provider="ilaas",
            model="gemma-4-31b",
            thinking="off",
            session_dir=Path("/tmp/s"),
        )
        return agent.argv(**(base | kw))

    def test_approval_is_unconditional(self):
        """Without it, every `.pi/` resource in a fresh clone is ignored silently.

        A fresh clone never has a saved trust decision, so a cell would measure the
        absence of the brick it believes it is measuring.
        """
        self.assertIn("-a", self.args())

    def test_discovery_is_switched_off_wholesale(self):
        args = self.args()
        for flag in ("-ns", "-np", "-ne"):
            self.assertIn(flag, args)

    def test_context_discovery_is_off_unless_the_cell_provides_one(self):
        self.assertIn("-nc", self.args())
        self.assertNotIn("-nc", self.args(has_context=True))

    def test_thinking_is_always_explicit(self):
        args = self.args(thinking="high")
        self.assertEqual(args[args.index("--thinking") + 1], "high")

    def test_bricks_are_passed_by_path(self):
        args = self.args(extensions=[Path("/x/ext")], skills=[Path("/x/skill")])
        self.assertEqual(args[args.index("-e") + 1], "/x/ext")
        self.assertEqual(args[args.index("--skill") + 1], "/x/skill")

    def test_the_prompt_is_last(self):
        self.assertEqual(self.args()[-1], "do it")


class TestConfigRules(unittest.TestCase):
    def write(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / config.CONFIG_NAME).write_text(text)
        return d / config.CONFIG_NAME

    def test_machine_paths_resolve_logical_names(self):
        path = self.write('[repos]\nneon = "../neon"\n[harness]\nsub = "~/w/sub"\n')
        c = config.load(path)
        self.assertTrue(str(c.repo("neon")).endswith("neon"))
        self.assertTrue(c.repo("neon").is_absolute(), "relative to the config file")

    def test_an_unknown_logical_name_names_what_is_known(self):
        c = config.load(self.write('[repos]\nneon = "../neon"\n'))
        with self.assertRaises(config.ConfigError) as e:
            c.repo("ghost")
        self.assertIn("neon", str(e.exception))

    def test_the_config_may_not_decide_what_is_measured(self):
        """The rule that stops a scenario from measuring differently elsewhere."""
        for key, value in (
            ("provider", "ilaas"),
            ("model", "gemma-4-31b"),
            ("thinking", "high"),
            ("etalon", "etalon-v1"),
            ("repetitions", 20),
        ):
            with self.subTest(key=key):
                path = self.write(f"[defaults]\n{key} = {json.dumps(value)}\n")
                with self.assertRaises(config.ConfigError) as e:
                    config.load(path)
                self.assertIn(key, str(e.exception))

    def test_load_fallbacks_are_allowed(self):
        c = config.load(self.write("[defaults]\nconcurrency = 2\ntimeout = 60\n"))
        self.assertEqual(c.fallback("concurrency"), 2)
        self.assertEqual(c.fallback("timeout"), 60)

    def test_no_config_file_is_not_an_error(self):
        c = config.load(start=Path(tempfile.mkdtemp()))
        self.assertEqual(c.fallback("concurrency"), config.BUILTIN_DEFAULTS["concurrency"])

    def test_a_missing_explicit_config_is_an_error(self):
        with self.assertRaises(config.ConfigError):
            config.load(Path(tempfile.mkdtemp()) / "nope.toml")

    def test_a_scenario_handed_to_config_is_refused(self):
        """The dangerous half of the two-file mix-up.

        A scenario read as a config has no [repos] and no [defaults], so it
        loads as built-in defaults and an empty set of machine paths. Nothing
        looks wrong until a run resolves a repository, and what fails then names
        the wrong file.
        """
        path = self.write(
            '[scenario]\nname = "t"\n[task]\netalon = "etalon-v1"\n'
            '[agent]\nprovider = "ilaas"\n[protocol]\nrepetitions = 10\n'
        )
        with self.assertRaises(config.ConfigError) as e:
            config.load(path)
        message = str(e.exception)
        self.assertIn("scenario file", message)
        self.assertIn(str(path), message)

    def test_a_scenario_pinning_bricks_is_not_mistaken_for_either_file(self):
        """[harness] is legitimate in both, so a file with both stays ambiguous."""
        raw = {"scenario": {"name": "t"}, "harness": {"subagent": "v0.3.0"}}
        self.assertIsNone(config.which_file(raw))

    def test_the_real_config_reads_as_a_config(self):
        self.assertEqual(
            config.which_file({"repos": {"neon": "../neon"}, "defaults": {}}), "config"
        )

    def test_an_empty_file_is_neither(self):
        self.assertIsNone(config.which_file({}))


class TestNaming(unittest.TestCase):
    def test_the_directory_name_carries_the_experiment_identity(self):
        s = parse(GRID)
        self.assertEqual(
            outputs.experiment_name(s), "t_etalon-v1_ilaas_gemma-4-31b_n10"
        )

    def test_changing_repetitions_changes_the_directory(self):
        """Which is why a quick run cannot corrupt a published matrix."""
        s = parse(GRID)
        self.assertNotEqual(outputs.experiment_name(s), outputs.experiment_name(s, 3))
        self.assertTrue(outputs.experiment_name(s, 3).endswith("_n3"))

    def test_run_ids_are_stable_and_opaque(self):
        first = outputs.run_id("t", "rule / high", 3)
        self.assertEqual(first, outputs.run_id("t", "rule / high", 3))
        self.assertNotIn("rule", first)
        self.assertNotEqual(first, outputs.run_id("t", "rule / off", 3))


class TestResume(unittest.TestCase):
    def output(self) -> outputs.Output:
        return outputs.Output(Path(tempfile.mkdtemp()), parse(GRID))

    def test_the_plan_covers_every_cell_and_repetition(self):
        o = self.output()
        self.assertEqual(len(o.plan()), 60)

    def test_everything_starts_missing(self):
        o = self.output()
        state = o.initial_state()
        self.assertEqual(len(o.to_do(state)), 60)

    def test_a_valid_run_is_never_relaunched(self):
        """The whole protection: a resume has no power over a produced result."""
        o = self.output()
        state = o.initial_state()
        run_id = next(iter(state["runs"]))
        o.record(state, run_id, Run(run_id, "none / off", 0, state=VALID))
        self.assertNotIn(run_id, dict(o.to_do(state)))

    def test_an_empty_run_is_relaunched(self):
        """It produced no result, so there is nothing to select between."""
        o = self.output()
        state = o.initial_state()
        run_id = next(iter(state["runs"]))
        o.record(state, run_id, Run(run_id, "none / off", 0, state=EMPTY))
        self.assertIn(run_id, dict(o.to_do(state)))

    def test_a_failed_validator_is_not_remeasured(self):
        """It needs re-scoring, which costs no tokens. Re-measuring it would let a
        resume change a run that had already produced something."""
        o = self.output()
        state = o.initial_state()
        run_id = next(iter(state["runs"]))
        o.record(state, run_id, Run(run_id, "none / off", 0, state=VALIDATOR_FAILED))
        self.assertNotIn(run_id, dict(o.to_do(state)))

    def test_attempts_accumulate_so_an_abusive_resume_leaves_a_trace(self):
        o = self.output()
        state = o.initial_state()
        run_id = next(iter(state["runs"]))
        for _ in range(3):
            o.record(state, run_id, Run(run_id, "none / off", 0, state=EMPTY, attempts=1))
        self.assertEqual(state["runs"][run_id]["attempts"], 3)

    def test_a_ledger_disagreeing_with_its_directory_is_refused(self):
        o = self.output()
        o.prepare()
        o.write_state(o.initial_state() | {"repetitions": 20})
        with self.assertRaises(ValueError):
            o.load_or_create_state()

    def test_only_restricts_the_launch_and_leaves_it_incomplete(self):
        o = self.output()
        state = o.initial_state()
        todo = o.to_do(state, only=("none / off",))
        self.assertEqual(len(todo), 10)
        counts = o.summarise(state)
        self.assertFalse(state["complete"])
        self.assertIn("This matrix is incomplete", outputs.incomplete_note(counts))

    def test_the_load_is_recorded_whatever_its_origin(self):
        """Retries depend on it, and every cost column depends on retries."""
        state = self.output().initial_state(overrides={"concurrency": 10})
        self.assertEqual(state["concurrency"], 5)
        self.assertEqual(state["overrides"], {"concurrency": 10})


class TestBlindness(unittest.TestCase):
    def scenario_with_judge(self, pieces):
        return parse(
            MINIMAL
            | {
                "validation": [
                    {"mode": "script", "command": "v.py", "metrics": ["overflow", "delivered"]},
                    {"mode": "judge", "rubric": "r.md", "pieces": pieces, "metrics": ["usable"]},
                ],
                "verdict": {"criterion": "overflow", "reference": "none"},
            }
        )

    def test_a_constant_piece_keeps_the_judge_blind(self):
        report = validation.blindness(self.scenario_with_judge(["response", "diff"]))
        self.assertTrue(report["judge"]["blind"])

    def test_a_piece_that_varies_leaks_the_treatment(self):
        """In the 2x3 matrix the treatment *is* the prompt, so handing the judge
        the prompt reveals which cell it is scoring."""
        report = validation.blindness(self.scenario_with_judge(["context", "diff"]))
        self.assertFalse(report["judge"]["blind"])
        self.assertIn("context", report["judge"]["leaking"])
        lines = validation.describe_blindness(report, cells=2)
        self.assertTrue(any("partially blind" in line for line in lines))

    def test_no_judge_means_nothing_to_report(self):
        self.assertEqual(validation.blindness(parse(MINIMAL)), {})

    def test_a_blind_context_withholds_the_cell(self):
        d = Path(tempfile.mkdtemp())
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=None,
            cell="pile complete",
            repetition=3,
            blind=True,
        )
        context = json.loads(path.read_text())
        self.assertNotIn("cell", context)
        self.assertNotIn("repetition", context)
        self.assertIn("repo", context)

    def test_a_script_context_keeps_the_cell(self):
        d = Path(tempfile.mkdtemp())
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=Path("/t"),
            cell="rule / high",
            repetition=3,
        )
        context = json.loads(path.read_text())
        self.assertEqual(context["cell"], "rule / high")
        self.assertEqual(context["etalon"]["tag"], "etalon-v1")


class TestThinkingPrecondition(unittest.TestCase):
    """A subagent's thinking level cannot be declared, so it is verified."""

    def test_a_mismatch_is_refused_when_subagents_are_used(self):
        message = validation.check_thinking_precondition("off", "high", uses_subagents=True)
        self.assertIsNotNone(message)
        self.assertIn("off", message)
        self.assertIn("high", message)

    def test_agreement_passes(self):
        self.assertIsNone(validation.check_thinking_precondition("off", "off", True))

    def test_without_subagents_there_is_nothing_to_refuse(self):
        self.assertIsNone(validation.check_thinking_precondition("off", "high", False))

    def test_unknown_ambient_setting_does_not_block(self):
        self.assertIsNone(validation.check_thinking_precondition("off", None, True))


class TestAgentModels(unittest.TestCase):
    def write_agent(self, body: str) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "explorer.md"
        p.write_text(body)
        return p

    def test_a_declared_model_is_read_from_the_file(self):
        p = self.write_agent(
            "---\nname: explorer\ndescription: x\nmodel: ilaas/gemma-4-31b\n"
            "tools: read, grep\n---\n\nbody\n"
        )
        meta = repo.agent_frontmatter(p)
        self.assertEqual(meta["model"], "ilaas/gemma-4-31b")
        self.assertEqual(meta["source"], "file")

    def test_an_override_wins_and_is_recorded_as_such(self):
        """Two places may declare, so the trace settles which one applied."""
        p = self.write_agent("---\nname: explorer\ndescription: x\nmodel: a/b\n---\n")
        meta = repo.agent_frontmatter(p, override="ilaas/gemma-4-31b")
        self.assertEqual(meta["model"], "ilaas/gemma-4-31b")
        self.assertEqual(meta["source"], "scenario override")

    def test_an_agent_with_no_model_anywhere_is_refused(self):
        """Nine shipped agents were in this position and ran on the wrong provider."""
        p = self.write_agent("---\nname: explorer\ndescription: x\n---\n")
        meta = repo.agent_frontmatter(p)
        self.assertIsNone(meta["model"])
        with self.assertRaises(repo.RepoError) as e:
            repo.check_agent_models({"explorer": meta})
        self.assertIn("explorer", str(e.exception))

    def test_an_override_rescues_a_file_that_declares_nothing(self):
        p = self.write_agent("---\nname: explorer\ndescription: x\n---\n")
        meta = repo.agent_frontmatter(p, override="ilaas/gemma-4-31b")
        repo.check_agent_models({"explorer": meta})


if __name__ == "__main__":
    unittest.main()
