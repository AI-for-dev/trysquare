"""What counts as a well-formed experiment, and what is refused before spending.

Every refusal here has a cost attached. A scenario that loads when it should not
is a matrix paid for and thrown away, or worse, published.
"""

import unittest
from pathlib import Path

from trysquare.scenario import ScenarioError, parse, split_command

MINIMAL = {
    "scenario": {"name": "t"},
    "task": {"repo": "neon", "etalon": "etalon-v1", "prompt": "do the thing"},
    "agent": {"provider": "ilaas", "model": "gemma-4-31b", "thinking": "off"},
    "protocol": {"repetitions": 10, "concurrency": 5, "timeout": 900},
    "variants": {"none": {}, "+rule": {"context": "bricks/AGENTS.md"}},
    "validation": [{"mode": "script", "command": "v.py", "metrics": ["overflow", "delivered"]}],
    "verdict": {"criterion": "overflow", "reference": "none"},
}

GRID = MINIMAL | {
    "variants": {},
    "axes": {"context": ["none", "rule", "ticket"], "thinking": ["off", "high"]},
    "values": {
        "context": {"rule": {"context": "bricks/AGENTS.md"}, "ticket": {"prompt": "bricks/t.md"}},
        "thinking": {"high": {"thinking": "high"}},
    },
    "verdict": {"criterion": "overflow", "reference": {"context": "none", "thinking": "off"}},
}


def without(d: dict, section: str, key: str) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in d.items()}
    out[section].pop(key)
    return out


class TestRequired(unittest.TestCase):
    """Nothing that changes a measurement may be inherited."""

    def test_each_experiment_key_is_mandatory(self):
        for section, key in (
            ("agent", "provider"),
            ("agent", "model"),
            ("agent", "thinking"),
            ("task", "etalon"),
            ("protocol", "repetitions"),
        ):
            with self.subTest(key=f"{section}.{key}"):
                with self.assertRaises(ScenarioError) as e:
                    parse(without(MINIMAL, section, key))
                self.assertIn(key, str(e.exception))

    def test_a_missing_section_is_named(self):
        for section in ("task", "agent", "protocol", "verdict"):
            with self.subTest(section=section):
                d = {k: v for k, v in MINIMAL.items() if k != section}
                with self.assertRaises(ScenarioError):
                    parse(d)

    def test_a_missing_section_names_the_file(self):
        d = {k: v for k, v in MINIMAL.items() if k != "verdict"}
        with self.assertRaises(ScenarioError) as e:
            parse(d, path=Path("scenarios/half-written.toml"))
        self.assertIn("scenarios/half-written.toml", str(e.exception))


class TestTheConfigFileHandedIn(unittest.TestCase):
    """The mix-up an operator actually makes, and how it must read.

    Both files are TOML and the config is the one at the root of the repository
    under a guessable name, so `run trysquare.toml` costs nothing but reads as a
    broken scenario unless the refusal names the confusion.
    """

    CONFIG = {
        "repos": {"neon": "../neon"},
        "harness": {"subagent": "~/Work/Pi/subagent"},
        "defaults": {"workdir": "$TMPDIR/trysquare", "concurrency": 5},
    }

    def test_a_config_file_is_refused_as_such(self):
        with self.assertRaises(ScenarioError) as e:
            parse(self.CONFIG, path=Path("trysquare.toml"))
        message = str(e.exception)
        self.assertIn("config file", message)
        self.assertIn("trysquare.toml", message)
        self.assertIn("--config", message)

    def test_any_config_section_alone_is_enough_to_recognise_it(self):
        for section in ("repos", "harness", "defaults"):
            with self.subTest(section=section):
                with self.assertRaises(ScenarioError) as e:
                    parse({section: self.CONFIG[section]})
                self.assertIn("config file", str(e.exception))

    def test_a_scenario_with_a_stray_config_section_gets_the_ordinary_refusal(self):
        """[harness] is legitimate in a scenario, which pins bricks by tag."""
        d = {k: v for k, v in MINIMAL.items() if k != "verdict"} | {
            "harness": {"subagent": "v0.3.0"}
        }
        with self.assertRaises(ScenarioError) as e:
            parse(d)
        self.assertIn("[verdict]", str(e.exception))

    def test_an_empty_file_is_not_mistaken_for_a_config(self):
        with self.assertRaises(ScenarioError) as e:
            parse({})
        self.assertIn("[scenario]", str(e.exception))


class TestGrid(unittest.TestCase):
    def test_axes_expand_to_their_product_in_declaration_order(self):
        s = parse(GRID)
        self.assertEqual(
            [c.name for c in s.cells],
            [
                "none / off",
                "none / high",
                "rule / off",
                "rule / high",
                "ticket / off",
                "ticket / high",
            ],
        )

    def test_the_first_axis_value_is_the_baseline(self):
        s = parse(GRID)
        self.assertTrue(s.cells[0].is_baseline)
        self.assertEqual(s.cells[0].name, "none / off")

    def test_deltas_accumulate_across_axes(self):
        s = parse(GRID)
        cell = s.cell("rule / high")
        self.assertEqual(cell.delta, {"context": "bricks/AGENTS.md", "thinking": "high"})

    def test_a_misspelled_axis_value_is_loud(self):
        """The counterpart of leaving the baseline implicit.

        Without this rule, `rule` misspelled produces a cell with no delta, so a
        silent duplicate of the baseline, published twice under two names.
        """
        broken = GRID | {"axes": {"context": ["none", "rule", "tickett"], "thinking": ["off", "high"]}}
        with self.assertRaises(ScenarioError) as e:
            parse(broken)
        message = str(e.exception)
        self.assertIn("tickett", message)
        self.assertIn("'none'", message, "the message must name the actual baseline")

    def test_an_empty_axis_is_refused(self):
        with self.assertRaises(ScenarioError):
            parse(GRID | {"axes": {"context": []}})

    def test_grid_and_variants_add_rather_than_exclude(self):
        both = GRID | {"variants": {"witness": {"thinking": "max"}}}
        s = parse(both)
        self.assertEqual(len(s.cells), 7)
        self.assertEqual(s.cells[-1].name, "witness")

    def test_a_cell_declared_twice_is_refused(self):
        clash = GRID | {"variants": {"none / off": {"thinking": "max"}}}
        with self.assertRaises(ScenarioError):
            parse(clash)


class TestValidation(unittest.TestCase):
    def test_two_validators_cannot_own_one_metric(self):
        """Refused at load, before any measurement, not resolved silently."""
        clash = MINIMAL | {
            "validation": [
                {"mode": "script", "command": "v.py", "metrics": ["overflow", "delivered"]},
                {"mode": "judge", "rubric": "r.md", "metrics": ["overflow"]},
            ]
        }
        with self.assertRaises(ScenarioError) as e:
            parse(clash)
        self.assertIn("overflow", str(e.exception))

    def test_a_validator_must_declare_metrics(self):
        with self.assertRaises(ScenarioError):
            parse(MINIMAL | {"validation": [{"mode": "script", "command": "v.py"}]})

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ScenarioError):
            parse(MINIMAL | {"validation": [{"mode": "vibes", "metrics": ["x"]}]})

    def test_a_scenario_that_measures_nothing_is_refused(self):
        with self.assertRaises(ScenarioError):
            parse(MINIMAL | {"validation": []})


class TestVerdict(unittest.TestCase):
    def test_the_criterion_must_be_a_declared_metric(self):
        with self.assertRaises(ScenarioError) as e:
            parse(MINIMAL | {"verdict": {"criterion": "vibes", "reference": "none"}})
        self.assertIn("vibes", str(e.exception))

    def test_the_reference_must_be_a_cell(self):
        with self.assertRaises(ScenarioError) as e:
            parse(MINIMAL | {"verdict": {"criterion": "overflow", "reference": "ghost"}})
        self.assertIn("ghost", str(e.exception))

    def test_a_grid_reference_is_a_table_of_axis_values(self):
        self.assertEqual(parse(GRID).reference, "none / off")

    def test_a_variant_reference_is_a_string(self):
        self.assertEqual(parse(MINIMAL).reference, "none")

    def test_a_partial_grid_reference_is_refused(self):
        broken = GRID | {"verdict": {"criterion": "overflow", "reference": {"context": "none"}}}
        with self.assertRaises(ScenarioError):
            parse(broken)

    def test_validity_must_name_declared_metrics(self):
        broken = MINIMAL | {
            "verdict": {"criterion": "overflow", "reference": "none", "validity": ["ghost"]}
        }
        with self.assertRaises(ScenarioError):
            parse(broken)


class TestShape(unittest.TestCase):
    def test_runs_is_cells_times_repetitions(self):
        self.assertEqual(parse(GRID).runs, 60)

    def test_declared_metrics_span_every_validator(self):
        s = parse(
            MINIMAL
            | {
                "validation": [
                    {"mode": "script", "command": "v.py", "metrics": ["overflow", "delivered"]},
                    {"mode": "judge", "rubric": "r.md", "metrics": ["usable"]},
                ]
            }
        )
        self.assertEqual(set(s.declared_metrics), {"overflow", "delivered", "usable"})


def scoring_tests(**task) -> dict:
    """A scenario that contracts for the `tests` metric, with `task` keys added."""
    return MINIMAL | {
        "task": MINIMAL["task"] | task,
        "validation": [
            {"mode": "script", "command": "v.py", "metrics": ["overflow", "delivered", "tests"]}
        ],
    }


class TestDeclaredTestCommand(unittest.TestCase):
    """Scoring a test suite means saying which suite, in the scenario.

    The command is declared rather than detected because the obvious detection -
    `npm test`, read from `package.json` - takes its answer from a file inside the
    perimeter the measured agent may edit. Broken code plus a `scripts.test` of
    `echo ok` scores green.
    """

    def test_a_scenario_that_scores_tests_must_declare_the_command(self):
        with self.assertRaises(ScenarioError) as raised:
            parse(scoring_tests())
        self.assertIn("test_command", str(raised.exception))

    def test_a_scenario_that_does_not_score_tests_needs_no_command(self):
        """Required by the metric, not by the section: a scenario measuring prose has
        no suite to name, and demanding one would be ceremony."""
        self.assertNotIn("tests", parse(MINIMAL).declared_metrics)

    def test_a_declared_command_is_split_once_at_load(self):
        """The file carries a string - what you would type - and everything downstream
        receives an argv. `shlex` is the shell's own word splitting, so the quoting rule is
        one every author already knows."""
        s = parse(scoring_tests(test_command="node --test 'game/**/*.test.js'"))
        # The scenario keeps the string; `split_command` is the one rule that turns it into
        # an argv, and it is the same one the loader vetted it with.
        self.assertEqual(s.task["test_command"], "node --test 'game/**/*.test.js'")
        self.assertEqual(
            split_command(s.task["test_command"]), ("node", "--test", "game/**/*.test.js")
        )

    def test_a_list_is_refused_because_one_command_decides(self):
        with self.assertRaises(ScenarioError) as raised:
            parse(scoring_tests(test_command=["node", "--test"]))
        self.assertIn("string", str(raised.exception))

    def test_a_shell_word_is_named_and_refused(self):
        """What the old list form only made harmless, this refuses out loud. Left alone,
        `&&` would reach the runner as an argument and fail where nobody can read it."""
        for line in ("npm ci && npm test", "npm test | tee out", "npm test > out"):
            with self.subTest(command=line):
                with self.assertRaises(ScenarioError) as raised:
                    parse(scoring_tests(test_command=line))
                self.assertIn("shell", str(raised.exception))

    def test_the_refusal_points_at_prepare(self):
        """Because there is somewhere to put the other step, and saying so is the
        difference between a refusal and a dead end."""
        with self.assertRaises(ScenarioError) as raised:
            parse(scoring_tests(test_command="npm ci && npm test"))
        self.assertIn("prepare", str(raised.exception))

    def test_an_empty_command_is_refused(self):
        with self.assertRaises(ScenarioError):
            parse(scoring_tests(test_command="   "))

    def test_an_unbalanced_quote_is_refused_at_load(self):
        with self.assertRaises(ScenarioError):
            parse(scoring_tests(test_command="node --test 'unclosed"))

    def test_a_command_declared_without_scoring_tests_is_kept(self):
        """Not an error: a scenario may name its suite before a validator scores it,
        and refusing that would punish writing the file in the useful order."""
        s = parse(MINIMAL | {"task": MINIMAL["task"] | {"test_command": "node --test"}})
        self.assertEqual(s.task["test_command"], "node --test")


class TestOneSplittingRule(unittest.TestCase):
    """`split_command` is the only place a command becomes an argv.

    Both callers come here: the loader that vets a scenario and the base that runs the
    command. Two implementations would be the drift this effort exists to remove, and a
    command split two slightly different ways would be measured two slightly different ways.
    """

    def test_quotes_hold_a_word_together(self):
        self.assertEqual(
            split_command("node --test 'game/**/*.test.js'"),
            ("node", "--test", "game/**/*.test.js"),
        )

    def test_a_path_with_a_space_survives(self):
        self.assertEqual(split_command('pytest "my tests"'), ("pytest", "my tests"))

    def test_it_is_what_the_loader_vets_with(self):
        """So a command that loads is a command that runs, with no second rule in between."""
        with self.assertRaises(ScenarioError):
            parse(scoring_tests(test_command="npm ci && npm test"))
        self.assertIn("&&", split_command("npm ci && npm test"))


class TestPrepareSteps(unittest.TestCase):
    """Steps that run **before** the suite, whose failure means something else.

    A `prepare` that fails - no network, a dependency that will not install - says nobody
    judged. The suite failing is a measurement. Conflated in one list, a broken network
    would score an agent red on a column that can carry the scenario's validity condition,
    which is "could not judge" filed as "worked badly" one level up.
    """

    def test_prepare_is_a_list_of_commands_each_split(self):
        s = parse(scoring_tests(test_command="npm test", prepare=["npm ci", "npm run build"]))
        self.assertEqual(s.task["prepare"], ["npm ci", "npm run build"])

    def test_no_prepare_is_the_common_case(self):
        """NEON has no dependency to install, and that is what makes a validation
        replayable from a tag and a diff months later."""
        self.assertEqual(parse(scoring_tests(test_command="npm test")).task.get("prepare"), None)

    def test_a_shell_word_in_prepare_is_refused_too(self):
        with self.assertRaises(ScenarioError) as raised:
            parse(scoring_tests(test_command="npm test", prepare=["npm ci && npm run build"]))
        self.assertIn("prepare[0]", str(raised.exception))

    def test_a_prepare_entry_that_is_not_a_string_is_refused(self):
        with self.assertRaises(ScenarioError):
            parse(scoring_tests(test_command="npm test", prepare=[["npm", "ci"]]))


if __name__ == "__main__":
    unittest.main()
