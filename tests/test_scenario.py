"""What counts as a well-formed experiment, and what is refused before spending.

Every refusal here has a cost attached. A scenario that loads when it should not
is a matrix paid for and thrown away, or worse, published.
"""

import re
from pathlib import Path

import pytest

from trysquare.scenario import ScenarioError, parse, split_command

MINIMAL = {
    "scenario": {"name": "t"},
    "task": {"repo": "my-repo", "etalon": "etalon-v1", "prompt": "do the thing"},
    "agent": {"provider": "ilaas", "model": "gemma-4-31b", "thinking": "off"},
    "protocol": {"repetitions": 10, "concurrency": 5, "timeout": 900},
    "variants": {"none": {}, "+rule": {"context": "context/AGENTS.md"}},
    "validation": [{"mode": "script", "command": "v.py", "metrics": ["overflow", "delivered"]}],
    "verdict": {"criterion": "overflow", "reference": "none"},
}

GRID = MINIMAL | {
    "variants": {},
    "axes": {"context": ["none", "rule", "ticket"], "thinking": ["off", "high"]},
    "values": {
        "context": {
            "rule": {"context": "context/AGENTS.md"},
            "ticket": {"prompt": "tickets/t.md"},
        },
        "thinking": {"high": {"thinking": "high"}},
    },
    "verdict": {"criterion": "overflow", "reference": {"context": "none", "thinking": "off"}},
}


def without(d: dict, section: str, key: str) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in d.items()}
    out[section].pop(key)
    return out


class TestRequired:
    """Nothing that changes a measurement may be inherited."""

    # Written out rather than imported from `scenario.REQUIRED`. Reading the rule
    # from the code under test would make this pass just as happily with an entry
    # deleted from it, which is the one failure the rule exists to prevent.
    @pytest.mark.parametrize(
        "section,key",
        [
            ("agent", "provider"),
            ("agent", "model"),
            ("agent", "thinking"),
            ("task", "etalon"),
            ("protocol", "repetitions"),
        ],
    )
    def test_each_experiment_key_is_mandatory(self, section, key):
        with pytest.raises(ScenarioError, match=re.escape(key)):
            parse(without(MINIMAL, section, key))

    @pytest.mark.parametrize("section", ["task", "agent", "protocol", "verdict"])
    def test_a_missing_section_is_named(self, section):
        with pytest.raises(ScenarioError):
            parse({k: v for k, v in MINIMAL.items() if k != section})

    def test_a_missing_section_names_the_file(self):
        d = {k: v for k, v in MINIMAL.items() if k != "verdict"}
        with pytest.raises(ScenarioError, match=re.escape("scenarios/half-written.toml")):
            parse(d, path=Path("scenarios/half-written.toml"))


class TestTheConfigFileHandedIn:
    """The mix-up an operator actually makes, and how it must read.

    Both files are TOML and the config is the one at the root of the repository
    under a guessable name, so `run trysquare.toml` costs nothing but reads as a
    broken scenario unless the refusal names the confusion.
    """

    CONFIG = {
        "repos": {"my-repo": "../my-repo"},
        "harness": {"subagent": "~/Work/Pi/subagent"},
        "defaults": {"workdir": "$TMPDIR/trysquare", "concurrency": 5},
    }

    def test_a_config_file_is_refused_as_such(self):
        with pytest.raises(ScenarioError) as raised:
            parse(self.CONFIG, path=Path("trysquare.toml"))
        message = str(raised.value)
        assert "config file" in message
        assert "trysquare.toml" in message
        assert "--config" in message

    @pytest.mark.parametrize("section", ["repos", "harness", "defaults"])
    def test_any_config_section_alone_is_enough_to_recognise_it(self, section):
        with pytest.raises(ScenarioError, match="config file"):
            parse({section: self.CONFIG[section]})

    def test_a_scenario_with_a_stray_config_section_gets_the_ordinary_refusal(self):
        """[harness] is legitimate in a scenario, which pins bricks by tag."""
        d = {k: v for k, v in MINIMAL.items() if k != "verdict"} | {
            "harness": {"subagent": "v0.3.0"}
        }
        with pytest.raises(ScenarioError, match=re.escape("[verdict]")):
            parse(d)

    def test_an_empty_file_is_not_mistaken_for_a_config(self):
        with pytest.raises(ScenarioError, match=re.escape("[scenario]")):
            parse({})


class TestGrid:
    def test_axes_expand_to_their_product_in_declaration_order(self):
        assert [c.name for c in parse(GRID).cells] == [
            "none / off",
            "none / high",
            "rule / off",
            "rule / high",
            "ticket / off",
            "ticket / high",
        ]

    def test_the_first_axis_value_is_the_baseline(self):
        s = parse(GRID)
        assert s.cells[0].is_baseline
        assert s.cells[0].name == "none / off"

    def test_deltas_accumulate_across_axes(self):
        cell = parse(GRID).cell("rule / high")
        assert cell.delta == {"context": "context/AGENTS.md", "thinking": "high"}

    def test_a_misspelled_axis_value_is_loud(self):
        """The counterpart of leaving the baseline implicit.

        Without this rule, `rule` misspelled produces a cell with no delta, so a
        silent duplicate of the baseline, published twice under two names.
        """
        broken = GRID | {
            "axes": {"context": ["none", "rule", "tickett"], "thinking": ["off", "high"]}
        }
        with pytest.raises(ScenarioError) as raised:
            parse(broken)
        message = str(raised.value)
        assert "tickett" in message
        assert "'none'" in message, "the message must name the actual baseline"

    def test_an_empty_axis_is_refused(self):
        with pytest.raises(ScenarioError):
            parse(GRID | {"axes": {"context": []}})

    def test_grid_and_variants_add_rather_than_exclude(self):
        s = parse(GRID | {"variants": {"witness": {"thinking": "max"}}})
        assert len(s.cells) == 7
        assert s.cells[-1].name == "witness"

    def test_a_cell_declared_twice_is_refused(self):
        with pytest.raises(ScenarioError):
            parse(GRID | {"variants": {"none / off": {"thinking": "max"}}})


class TestValidation:
    def test_two_validators_cannot_own_one_metric(self):
        """Refused at load, before any measurement, not resolved silently."""
        clash = MINIMAL | {
            "validation": [
                {"mode": "script", "command": "v.py", "metrics": ["overflow", "delivered"]},
                {"mode": "judge", "rubric": "r.md", "metrics": ["overflow"]},
            ]
        }
        with pytest.raises(ScenarioError, match="overflow"):
            parse(clash)

    def test_a_validator_must_declare_metrics(self):
        with pytest.raises(ScenarioError):
            parse(MINIMAL | {"validation": [{"mode": "script", "command": "v.py"}]})

    def test_an_unknown_mode_is_refused(self):
        with pytest.raises(ScenarioError):
            parse(MINIMAL | {"validation": [{"mode": "vibes", "metrics": ["x"]}]})

    def test_a_scenario_that_measures_nothing_is_refused(self):
        with pytest.raises(ScenarioError):
            parse(MINIMAL | {"validation": []})


class TestVerdict:
    def test_the_criterion_must_be_a_declared_metric(self):
        with pytest.raises(ScenarioError, match="vibes"):
            parse(MINIMAL | {"verdict": {"criterion": "vibes", "reference": "none"}})

    def test_the_reference_must_be_a_cell(self):
        with pytest.raises(ScenarioError, match="ghost"):
            parse(MINIMAL | {"verdict": {"criterion": "overflow", "reference": "ghost"}})

    def test_a_near_miss_is_suggested_and_a_far_one_is_not(self):
        """A refusal that can name the fix should; one that cannot must stay quiet."""
        with pytest.raises(ScenarioError, match="did you mean 'none'"):
            parse(MINIMAL | {"verdict": {"criterion": "overflow", "reference": "non"}})
        with pytest.raises(ScenarioError) as caught:
            parse(MINIMAL | {"verdict": {"criterion": "overflow", "reference": "zzzzzz"}})
        assert "did you mean" not in str(caught.value)

    def test_a_grid_reference_is_a_table_of_axis_values(self):
        assert parse(GRID).reference == "none / off"

    def test_a_variant_reference_is_a_string(self):
        assert parse(MINIMAL).reference == "none"

    def test_a_partial_grid_reference_is_refused(self):
        broken = GRID | {"verdict": {"criterion": "overflow", "reference": {"context": "none"}}}
        with pytest.raises(ScenarioError):
            parse(broken)

    def test_validity_must_name_declared_metrics(self):
        broken = MINIMAL | {
            "verdict": {"criterion": "overflow", "reference": "none", "validity": ["ghost"]}
        }
        with pytest.raises(ScenarioError):
            parse(broken)


class TestShape:
    def test_runs_is_cells_times_repetitions(self):
        assert parse(GRID).runs == 60

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
        assert set(s.declared_metrics) == {"overflow", "delivered", "usable"}


class TestBrickKind:
    """A `kind` nobody can route is refused before any token is spent.

    A misspelled kind would not raise downstream: the routing falls back to
    agents, so the cell would silently measure subagents where the author
    declared skills.
    """

    def test_a_misspelled_kind_is_refused_with_a_suggestion(self):
        d = MINIMAL | {"harness": {"skill-tdd": {"kind": "skill", "paths": ["skills/tdd"]}}}
        with pytest.raises(ScenarioError, match=re.escape("did you mean 'skills'")):
            parse(d)

    def test_kind_without_paths_is_refused(self):
        d = MINIMAL | {"harness": {"skill-tdd": {"kind": "skills"}}}
        with pytest.raises(ScenarioError, match="no paths"):
            parse(d)

    def test_both_kinds_load(self):
        d = MINIMAL | {
            "harness": {
                "skill-tdd": {"kind": "skills", "paths": ["skills/tdd"]},
                "subagents": {"kind": "agents", "paths": ["agents/explorer.md"]},
            }
        }
        assert set(parse(d).bricks) == {"skill-tdd", "subagents"}


def a_files_brick(**brick) -> dict:
    return MINIMAL | {"harness": {"probe": {"kind": "files"} | brick}}


class TestFilesBrick:
    """A brick that puts material in the measured tree, keyed by where it lands.

    The destination is declared and never derived from the source's basename: the
    path a probe occupies decides whether the repository's own test command picks it
    up, which is a decision of the experiment and not of whoever named the file.
    """

    def test_a_files_table_loads(self):
        d = a_files_brick(files={"game/probe.test.js": "bricks/probe.test.js"})
        assert parse(d).bricks["probe"]["files"] == {"game/probe.test.js": "bricks/probe.test.js"}

    def test_the_kind_without_a_files_table_is_refused(self):
        with pytest.raises(ScenarioError, match="no files"):
            parse(a_files_brick())

    def test_a_files_table_without_the_kind_is_refused(self):
        """Dropping a file into the measured tree is never something a default does."""
        d = MINIMAL | {"harness": {"probe": {"files": {"game/p.js": "p.js"}}}}
        with pytest.raises(ScenarioError, match="no kind"):
            parse(d)

    def test_a_files_table_on_another_kind_is_refused(self):
        """Nothing reads it there, so it would be dropped in silence."""
        d = MINIMAL | {
            "harness": {"probe": {"kind": "skills", "paths": ["s"], "files": {"a": "b"}}}
        }
        with pytest.raises(ScenarioError, match="files table"):
            parse(d)

    def test_a_list_where_a_table_belongs_is_refused(self):
        with pytest.raises(ScenarioError, match="table of"):
            parse(a_files_brick(files=["bricks/probe.test.js"]))

    def test_an_absolute_destination_is_refused(self):
        with pytest.raises(ScenarioError, match="cannot be absolute"):
            parse(a_files_brick(files={"/etc/probe.js": "bricks/probe.test.js"}))

    def test_a_destination_that_climbs_out_is_refused(self):
        """The one thing a run must never do is write outside its clone."""
        with pytest.raises(ScenarioError, match="climb out"):
            parse(a_files_brick(files={"../probe.js": "bricks/probe.test.js"}))

    def test_a_source_that_is_not_a_path_is_refused(self):
        with pytest.raises(ScenarioError, match="path to a file"):
            parse(a_files_brick(files={"game/probe.test.js": ""}))


def scoring_tests(**task) -> dict:
    """A scenario that contracts for the `tests` metric, with `task` keys added."""
    return MINIMAL | {
        "task": MINIMAL["task"] | task,
        "validation": [
            {"mode": "script", "command": "v.py", "metrics": ["overflow", "delivered", "tests"]}
        ],
    }


class TestDeclaredTestCommand:
    """Scoring a test suite means saying which suite, in the scenario.

    The command is declared rather than detected because the obvious detection -
    `npm test`, read from `package.json` - takes its answer from a file inside the
    perimeter the measured agent may edit. Broken code plus a `scripts.test` of
    `echo ok` scores green.
    """

    def test_a_scenario_that_scores_tests_must_declare_the_command(self):
        with pytest.raises(ScenarioError, match="test_command"):
            parse(scoring_tests())

    def test_a_scenario_that_does_not_score_tests_needs_no_command(self):
        """Required by the metric, not by the section: a scenario measuring prose has
        no suite to name, and demanding one would be ceremony."""
        assert "tests" not in parse(MINIMAL).declared_metrics

    def test_a_declared_command_is_split_once_at_load(self):
        """The file carries a string - what you would type - and everything downstream
        receives an argv. `shlex` is the shell's own word splitting, so the quoting rule is
        one every author already knows."""
        s = parse(scoring_tests(test_command="node --test 'game/**/*.test.js'"))
        # The scenario keeps the string; `split_command` is the one rule that turns it into
        # an argv, and it is the same one the loader vetted it with.
        assert s.task["test_command"] == "node --test 'game/**/*.test.js'"
        assert split_command(s.task["test_command"]) == ("node", "--test", "game/**/*.test.js")

    def test_a_list_is_refused_because_one_command_decides(self):
        with pytest.raises(ScenarioError, match="string"):
            parse(scoring_tests(test_command=["node", "--test"]))

    @pytest.mark.parametrize("line", ["npm ci && npm test", "npm test | tee out", "npm test > out"])
    def test_a_shell_word_is_named_and_refused(self, line):
        """What the old list form only made harmless, this refuses out loud. Left alone,
        `&&` would reach the runner as an argument and fail where nobody can read it."""
        with pytest.raises(ScenarioError, match="shell"):
            parse(scoring_tests(test_command=line))

    def test_the_refusal_points_at_prepare(self):
        """Because there is somewhere to put the other step, and saying so is the
        difference between a refusal and a dead end."""
        with pytest.raises(ScenarioError, match="prepare"):
            parse(scoring_tests(test_command="npm ci && npm test"))

    def test_an_empty_command_is_refused(self):
        with pytest.raises(ScenarioError):
            parse(scoring_tests(test_command="   "))

    def test_an_unbalanced_quote_is_refused_at_load(self):
        with pytest.raises(ScenarioError):
            parse(scoring_tests(test_command="node --test 'unclosed"))

    def test_a_command_declared_without_scoring_tests_is_kept(self):
        """Not an error: a scenario may name its suite before a validator scores it,
        and refusing that would punish writing the file in the useful order."""
        s = parse(MINIMAL | {"task": MINIMAL["task"] | {"test_command": "node --test"}})
        assert s.task["test_command"] == "node --test"


class TestOneSplittingRule:
    """`split_command` is the only place a command becomes an argv.

    Both callers come here: the loader that vets a scenario and the base that runs the
    command. Two implementations would be the drift this effort exists to remove, and a
    command split two slightly different ways would be measured two slightly different ways.
    """

    def test_quotes_hold_a_word_together(self):
        assert split_command("node --test 'game/**/*.test.js'") == (
            "node",
            "--test",
            "game/**/*.test.js",
        )

    def test_a_path_with_a_space_survives(self):
        assert split_command('pytest "my tests"') == ("pytest", "my tests")

    def test_it_is_what_the_loader_vets_with(self):
        """So a command that loads is a command that runs, with no second rule in between."""
        with pytest.raises(ScenarioError):
            parse(scoring_tests(test_command="npm ci && npm test"))
        assert "&&" in split_command("npm ci && npm test")


class TestPrepareSteps:
    """Steps that run **before** the suite, whose failure means something else.

    A `prepare` that fails - no network, a dependency that will not install - says nobody
    judged. The suite failing is a measurement. Conflated in one list, a broken network
    would score an agent red on a column that can carry the scenario's validity condition,
    which is "could not judge" filed as "worked badly" one level up.
    """

    def test_prepare_is_a_list_of_commands_each_split(self):
        s = parse(scoring_tests(test_command="npm test", prepare=["npm ci", "npm run build"]))
        assert s.task["prepare"] == ["npm ci", "npm run build"]

    def test_no_prepare_is_the_common_case(self):
        """A repository with nothing to install is what makes a validation replayable
        from a tag and a diff months later."""
        assert parse(scoring_tests(test_command="npm test")).task.get("prepare") is None

    def test_a_shell_word_in_prepare_is_refused_too(self):
        with pytest.raises(ScenarioError, match=re.escape("prepare[0]")):
            parse(scoring_tests(test_command="npm test", prepare=["npm ci && npm run build"]))

    def test_a_prepare_entry_that_is_not_a_string_is_refused(self):
        with pytest.raises(ScenarioError):
            parse(scoring_tests(test_command="npm test", prepare=[["npm", "ci"]]))


class TestDeclaredArtefacts:
    """What running the task leaves behind, named by the only person who can know.

    Declared for the same reason `test_command` is: a built-in list would be a guess about
    somebody else's language, and it would eventually hide a file an agent really wrote.
    """

    def test_the_patterns_are_carried_as_written(self):
        s = parse(scoring_tests(test_command="npm test", artefacts=["__pycache__", "*.pyc"]))
        assert s.task["artefacts"] == ["__pycache__", "*.pyc"]

    def test_declaring_none_is_the_default(self):
        assert parse(scoring_tests(test_command="npm test")).task.get("artefacts") is None

    def test_a_bare_string_is_refused_and_shows_the_shape(self):
        """A string iterates as characters, so `artefacts = "*.pyc"` would turn every path
        holding a `.` into a by-product."""
        with pytest.raises(ScenarioError, match=re.escape('["__pycache__", "*.pyc"]')):
            parse(scoring_tests(test_command="npm test", artefacts="__pycache__"))

    def test_an_entry_that_is_not_a_pattern_is_refused(self):
        with pytest.raises(ScenarioError, match="path pattern"):
            parse(scoring_tests(test_command="npm test", artefacts=["ok", 3]))

    def test_an_empty_entry_is_refused(self):
        """It matches nothing an author meant, and reads as a line half deleted."""
        with pytest.raises(ScenarioError, match="path pattern"):
            parse(scoring_tests(test_command="npm test", artefacts=["  "]))
