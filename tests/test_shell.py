"""The effectful shell: argument building, config rules, output state, resume.

Nothing here launches an agent. What is tested is the part that decides *what*
would be launched and *what* may be relaunched, which is where a measurement is
silently lost or a standard silently bent.
"""

import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from trysquare import agent, config, outputs, repo, runner, validation
from trysquare.measure import EMPTY, VALID, VALIDATOR_FAILED, Run
from trysquare.scenario import Validator, parse
from tests.gitrepo import a_repo
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


class TestCloneArgv(unittest.TestCase):
    """The flags that make a clone the pinned state and nothing else."""

    def test_a_clone_is_pinned_to_the_tag(self):
        for keep_tags in (False, True):
            with self.subTest(keep_tags=keep_tags):
                args = repo.clone_argv("/s", "etalon-v1", Path("/x"), keep_tags=keep_tags)
                self.assertIn("--single-branch", args)
                self.assertEqual(args[args.index("--branch") + 1], "etalon-v1")

    def test_a_run_clone_drops_the_tags_and_a_pinned_source_keeps_them(self):
        """Every run clones *from* the pinned source by tag name.

        Git happens to keep the tag named by `--branch` even under `--no-tags`, so this
        is not the difference between working and not working. It is the difference
        between resting on documented behaviour and resting on an accident.
        """
        self.assertIn("--no-tags", repo.clone_argv("/s", "t", Path("/x")))
        self.assertNotIn("--no-tags", repo.clone_argv("/s", "t", Path("/x"), keep_tags=True))

    def test_a_url_reaches_git_verbatim(self):
        args = repo.clone_argv("https://h/x.git", "t", Path("/x"), keep_tags=True)
        self.assertIn("https://h/x.git", args)


class TestConfigRules(unittest.TestCase):
    def write(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / config.CONFIG_NAME).write_text(text)
        return d / config.CONFIG_NAME

    def test_machine_paths_resolve_logical_names(self):
        path = self.write('[repos]\nmy-repo = "../my-repo"\n[harness]\nsub = "~/w/sub"\n')
        c = config.load(path)
        self.assertTrue(str(c.repo("my-repo")).endswith("my-repo"))
        self.assertTrue(c.repo("my-repo").is_absolute(), "relative to the config file")

    def test_an_unknown_logical_name_names_what_is_known(self):
        c = config.load(self.write('[repos]\nmy-repo = "../my-repo"\n'))
        with self.assertRaises(config.ConfigError) as e:
            c.repo("ghost")
        self.assertIn("my-repo", str(e.exception))

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

    def test_a_url_is_told_apart_from_a_path(self):
        remote = (
            "https://h/x.git",
            "http://h/x.git",
            "ssh://git@h/x.git",
            "git://h/x.git",
            "file:///tmp/x",
            "git@github.com:org/x.git",
        )
        local = ("../my-repo", "/abs/my-repo", "~/w/sub", "./x", "$TMPDIR/x", "my-repo")
        for value in remote:
            with self.subTest(value=value):
                self.assertTrue(config.is_remote(value))
        for value in local:
            with self.subTest(value=value):
                self.assertFalse(config.is_remote(value))

    def test_a_url_is_never_mangled_into_a_path(self):
        """The whole reason a URL is classified before `expand()` ever sees it.

        `Path("https://h/x.git")` collapses the double slash into `https:/h/x.git`, a
        *relative* path: git would then be handed a directory resolved against the
        config file's parent, and the failure is a clone of nothing rather than an error.
        """
        url = "https://h/x.git"
        c = config.load(self.write(f'[repos]\nmy-repo = "{url}"\n'))
        self.assertEqual(c.remote("my-repo"), url)
        self.assertNotEqual(c.remote("my-repo"), str(Path(url)))

    def test_a_url_does_not_inherit_from_the_shell(self):
        """A username or token taken from the environment is invisible inheritance.

        It appears in no archive, and the value that actually ran would be whatever the
        shell happened to hold - the defect this whole module exists to abolish.
        """
        c = config.load(self.write('[repos]\nmy-repo = "git@h:$USER/x.git"\n'))
        self.assertEqual(c.remote("my-repo"), "git@h:$USER/x.git")

    def test_a_local_entry_has_no_url(self):
        c = config.load(self.write('[repos]\nmy-repo = "../my-repo"\n[harness]\nsub = "~/w/sub"\n'))
        self.assertIsNone(c.remote("my-repo"))
        self.assertIsNone(c.harness_remote("sub"))

    def test_a_url_has_no_local_directory_until_it_is_cloned(self):
        c = config.load(self.write('[repos]\nmy-repo = "https://h/x.git"\n'))
        with self.assertRaises(config.ConfigError) as e:
            c.repo("my-repo")
        self.assertIn("https://h/x.git", str(e.exception))

    def test_a_harness_entry_may_be_a_url_too(self):
        """The two sections have the same shape; accepting one and refusing the other
        would be an asymmetry nobody could guess."""
        c = config.load(self.write('[harness]\nsub = "https://h/sub.git"\n'))
        self.assertEqual(c.harness_remote("sub"), "https://h/sub.git")

    def test_an_unknown_logical_name_names_what_is_known_from_remote_too(self):
        c = config.load(self.write('[repos]\nmy-repo = "../my-repo"\n'))
        with self.assertRaises(config.ConfigError) as e:
            c.remote("ghost")
        self.assertIn("my-repo", str(e.exception))

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
            config.which_file({"repos": {"my-repo": "../my-repo"}, "defaults": {}}), "config"
        )

    def test_an_empty_file_is_neither(self):
        self.assertIsNone(config.which_file({}))


class TestPinnedSources(unittest.TestCase):
    """Pinning a remote once, without ever running git."""

    def conf(self, entry: str):
        d = Path(tempfile.mkdtemp())
        path = d / config.CONFIG_NAME
        path.write_text(f'[repos]\nmy-repo = "{entry}"\n[defaults]\nworkdir = "{d / "work"}"\n')
        return config.load(path)

    def fake_pin(self, calls: list, fail: bool = False):
        def pin(url, etalon, target):
            calls.append((url, etalon, Path(target)))
            if fail:
                raise repo.RepoError("could not read from remote repository")
            Path(target).mkdir(parents=True, exist_ok=True)
            return Path(target)

        return pin

    def pin_calls(self, entry: str, times: int = 1, fail: bool = False):
        """Runs `prepare_source` `times` times against a fake clone."""
        c = self.conf(entry)
        calls: list = []
        with mock.patch.object(runner.repo_mod, "pin", self.fake_pin(calls, fail)):
            for _ in range(times):
                result = runner.prepare_source(c, "my-repo", "etalon-v1")
        return c, calls, result

    URL = "https://h/x.git"

    def test_a_pinned_directory_is_keyed_by_the_url_and_the_tag(self):
        """Editing the URL must not silently reuse the previous repository's clone, and
        a cache hit must be at the tag being asked for rather than merely present."""
        c = self.conf(self.URL)
        base = runner.source_dir(c, "my-repo", self.URL, "etalon-v1")
        self.assertEqual(base.parent, c.workdir() / "sources")
        self.assertNotEqual(base, runner.source_dir(c, "my-repo", "https://h/other.git", "etalon-v1"))
        self.assertNotEqual(base, runner.source_dir(c, "my-repo", self.URL, "etalon-v2"))
        self.assertEqual(base, runner.source_dir(c, "my-repo", self.URL, "etalon-v1"), "stable")

    def test_a_slash_in_a_tag_stays_one_directory(self):
        """`release/1.0` would otherwise put the clone somewhere nobody named."""
        c = self.conf(self.URL)
        directory = runner.source_dir(c, "my-repo", self.URL, "release/1.0")
        self.assertEqual(directory.parent, c.workdir() / "sources")
        self.assertIn("release-1.0", directory.name)

    def test_a_local_entry_is_never_cloned(self):
        existing = Path(tempfile.mkdtemp())
        c, calls, result = self.pin_calls(str(existing))
        self.assertEqual(result, existing)
        self.assertEqual(calls, [])

    def test_a_missing_local_entry_names_the_config_file(self):
        with self.assertRaises(repo.RepoError) as e:
            self.pin_calls("../nowhere")
        self.assertIn(config.CONFIG_NAME, str(e.exception))
        self.assertIn("nowhere", str(e.exception))

    def test_a_remote_is_cloned_once_and_reused(self):
        _, calls, result = self.pin_calls(self.URL, times=3)
        self.assertEqual(len(calls), 1)
        self.assertTrue((result / runner.READY).is_file())

    def test_a_marker_left_by_another_url_forces_a_fresh_clone(self):
        """A directory that cannot say which repository it holds must not be trusted."""
        c = self.conf(self.URL)
        target = runner.source_dir(c, "my-repo", self.URL, "etalon-v1")
        target.mkdir(parents=True)
        (target / runner.READY).write_text("my-repo@etalon-v1\nhttps://h/somewhere-else.git\n")
        calls: list = []
        with mock.patch.object(runner.repo_mod, "pin", self.fake_pin(calls)):
            runner.prepare_source(c, "my-repo", "etalon-v1")
        self.assertEqual(len(calls), 1)

    def test_concurrent_cells_pin_exactly_once(self):
        """Cells run concurrently and all arrive here at the same moment.

        Checking existence and then cloning is not atomic: this is the race that made
        two of four concurrent cells fail on `destination path already exists`, and let
        a third load an extension whose dependencies were never installed.
        """
        c = self.conf(self.URL)
        calls: list = []
        slow = self.fake_pin(calls)

        def pin(url, etalon, target):
            time.sleep(0.02)
            return slow(url, etalon, target)

        results = []
        with mock.patch.object(runner.repo_mod, "pin", pin):
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [
                    pool.submit(runner.prepare_source, c, "my-repo", "etalon-v1") for _ in range(8)
                ]
                results = [f.result() for f in futures]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(set(results)), 1)

    def test_a_clone_failure_names_the_url_the_tag_and_the_config(self):
        """A bad URL is a config bug, so the refusal has to say which file to edit."""
        with self.assertRaises(repo.RepoError) as e:
            self.pin_calls(self.URL, fail=True)
        message = str(e.exception)
        self.assertIn(self.URL, message)
        self.assertIn("etalon-v1", message)
        self.assertIn(config.CONFIG_NAME, message)
        self.assertIn("Nothing was measured", message)


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

    def test_the_declared_test_command_travels_in_the_context(self):
        """A validator scoring `tests` reads the command here rather than guessing it.

        Carried **as the scenario wrote it**, a string. One fact, one representation: an
        archived context read against the scenario file six months later says the same thing,
        with no transformation to know about.
        """
        d = Path(tempfile.mkdtemp())
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=None,
            cell="none",
            repetition=0,
            test_command="node --test 'game/**/*.test.js'",
        )
        context = json.loads(path.read_text())
        self.assertEqual(context["test_command"], "node --test 'game/**/*.test.js'")

    def test_a_blind_context_still_carries_the_test_command(self):
        """It is a property of the **task**, identical in every cell, so it tells a
        judge nothing about which configuration produced the work it scores."""
        d = Path(tempfile.mkdtemp())
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=None,
            cell="none",
            repetition=0,
            blind=True,
            test_command="node --test",
        )
        context = json.loads(path.read_text())
        self.assertNotIn("cell", context)
        self.assertEqual(context["test_command"], "node --test")

    def test_the_context_and_the_loader_split_it_the_same_way(self):
        """The point of a single rule: what loads is what runs, with nothing in between.

        Two implementations would let a command be split two slightly different ways, and
        therefore measured two slightly different ways.
        """
        from trysquare.scenario import split_command

        d = Path(tempfile.mkdtemp())
        written = "node --test 'game/**/*.test.js'"
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=None,
            cell="none",
            repetition=0,
            test_command=written,
        )
        carried = json.loads(path.read_text())["test_command"]
        self.assertEqual(carried, written)
        self.assertEqual(
            split_command(carried), ("node", "--test", "game/**/*.test.js")
        )

    def test_prepare_travels_as_written_too(self):
        d = Path(tempfile.mkdtemp())
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=None,
            cell="none",
            repetition=0,
            test_command="npm test",
            prepare=["npm ci"],
        )
        self.assertEqual(json.loads(path.read_text())["prepare"], ["npm ci"])

    def test_a_scenario_with_no_command_writes_no_key(self):
        """An absent key is what a validator reads as "this scenario names no suite",
        which is not the same fact as an empty command."""
        d = Path(tempfile.mkdtemp())
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=None,
            cell="none",
            repetition=0,
        )
        self.assertNotIn("test_command", json.loads(path.read_text()))


class TestWhatTheHarnessComputesOnce(unittest.TestCase):
    """Two facts every validator wants, computed by the harness so they cannot drift.

    Both were reimplemented per validator. `my-repo.py:67-78` and `issue1.py:167-180` each
    carry the same "files the agent changed", down to the same copied comment, while
    `repo.diff` held the knowledge all along. `citations.py:46-55` and `my-repo.py:95-100`
    each run their own `git ls-tree`. A fact the harness computes cannot be got slightly
    differently by three callers.
    """

    def test_the_harness_already_had_all_three_primitives(self):
        """Not a behaviour test, a pin on a finding: `repo.changed_files`,
        `repo.etalon_files` and `repo.etalon_file` predate this base. Three shipped
        validators reimplemented them with a raw `subprocess` anyway, which is what the
        context and the base are for - exposing what exists, not writing it again."""
        for name in ("changed_files", "etalon_files", "etalon_file"):
            self.assertTrue(callable(getattr(repo, name)), name)

    def test_changed_files_sees_a_new_file(self):
        """`--intent-to-add` is what makes an untracked file visible to `git diff`.
        Without it a change written into a file created for the occasion is invisible."""
        d = a_repo({"a.js": "one\n"})
        (d / "b.js").write_text("two\n")
        self.assertEqual(repo.changed_files(d), ["b.js"])

    def test_changed_files_sees_an_edit(self):
        d = a_repo({"a.js": "one\n"})
        (d / "a.js").write_text("two\n")
        self.assertEqual(repo.changed_files(d), ["a.js"])

    def test_an_untouched_clone_changed_nothing(self):
        """An empty list is a measurement - the agent did not work - so it must not be
        confused with a failure to look."""
        self.assertEqual(repo.changed_files(a_repo({"a.js": "one\n"})), [])

    def test_etalon_files_reads_the_tag_and_not_the_working_tree(self):
        d = a_repo({"a.js": "one\n", "game/b.js": "two\n"})
        (d / "c.js").write_text("late\n")
        self.assertEqual(repo.etalon_files(d, "etalon-v1"), ["a.js", "game/b.js"])

    def test_both_travel_in_the_context(self):
        d = Path(tempfile.mkdtemp())
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=None,
            cell="none",
            repetition=0,
            touched=["game/my-repo.js"],
            files=["game/my-repo.js", "README.md"],
        )
        context = json.loads(path.read_text())
        self.assertEqual(context["touched"], ["game/my-repo.js"])
        self.assertEqual(context["files"], ["game/my-repo.js", "README.md"])

    def test_an_empty_touched_is_written_rather_than_dropped(self):
        """The one case where absent and empty must not be confused: an agent that
        changed nothing is a result, and a validator has to be able to read it."""
        d = Path(tempfile.mkdtemp())
        path = validation.write_context(
            d,
            repo=Path("/r"),
            etalon="etalon-v1",
            etalon_checkout=Path("/e"),
            prompt_file=Path("/p"),
            session_dir=Path("/s"),
            trace=None,
            cell="none",
            repetition=0,
            touched=[],
        )
        self.assertEqual(json.loads(path.read_text())["touched"], [])


class TestScriptValidatorPaths(unittest.TestCase):
    """A validator is run from the context's directory, so the context path must be
    absolute.

    `--output out` is the documented way to invoke the tool, and it made every script
    validator fail with `unreadable context`: the relative path was measured from the
    child's new working directory rather than the caller's. The whole matrix came back
    `validator_failed` after paying for every run.
    """

    def validator(self, script: Path) -> Validator:
        return Validator(mode="script", config={"command": str(script)}, metrics=("ok",))

    def echo_script(self, directory: Path) -> Path:
        """A validator that only proves it could open what it was handed."""
        script = directory / "echo.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "json.load(open(sys.argv[1]))\n"
            'print(json.dumps({"metrics": {"ok": True}}))\n'
        )
        script.chmod(0o755)
        return script

    def context_under(self, root: Path) -> Path:
        directory = root / "out" / "exp" / "runs" / "abcd" / "validation" / "script"
        directory.mkdir(parents=True)
        context = directory / "context.json"
        context.write_text(json.dumps({"repo": "/r"}))
        return context

    def test_a_relative_output_still_reaches_the_context(self):
        root = Path(tempfile.mkdtemp())
        script = self.echo_script(root)
        context = self.context_under(root)
        relative = context.relative_to(root)

        previous = Path.cwd()
        os.chdir(root)
        try:
            result = validation.run_script(self.validator(script), relative, timeout=30)
        finally:
            os.chdir(previous)
        self.assertIsNone(result.detail or None, result.stderr)

    def test_a_python_validator_runs_on_the_harness_interpreter(self):
        """`#!/usr/bin/env python3` catches Python 3.9 on macOS, and the package needs
        3.11 for `tomllib`. A validator importing `trysquare.assay` under the shebang's
        interpreter would fail to import, which reads as an invalid run.

        Proved by a script that is neither executable nor has a usable shebang: only
        being handed to an interpreter can make it run.
        """
        root = Path(tempfile.mkdtemp())
        script = root / "unrunnable.py"
        script.write_text(
            "#!/nowhere/python3\n"
            "import json, sys\n"
            "json.load(open(sys.argv[1]))\n"
            'print(json.dumps({"metrics": {"ok": sys.version_info >= (3, 11)}}))\n'
        )
        script.chmod(0o644)
        result = validation.run_script(
            self.validator(script), self.context_under(root), timeout=30
        )
        self.assertIsNone(result.detail or None, result.stderr)
        self.assertTrue(
            result.payload["metrics"]["ok"],
            "the validator ran on an interpreter older than the package requires",
        )

    def test_a_validator_in_another_language_is_left_alone(self):
        """The courtesy is for `.py` only. "Any executable, in any language" stays
        literally true for everything else."""
        root = Path(tempfile.mkdtemp())
        script = root / "echo.sh"
        script.write_text('#!/bin/sh\ncat "$1" > /dev/null\necho \'{"metrics":{"ok":true}}\'\n')
        script.chmod(0o755)
        result = validation.run_script(
            self.validator(script), self.context_under(root), timeout=30
        )
        self.assertIsNone(result.detail or None, result.stderr)
        self.assertEqual(result.payload["metrics"], {"ok": True})
        self.assertEqual(result.payload, {"metrics": {"ok": True}})

    def test_an_absolute_output_works_as_it_always_did(self):
        root = Path(tempfile.mkdtemp())
        result = validation.run_script(
            self.validator(self.echo_script(root)), self.context_under(root), timeout=30
        )
        self.assertEqual(result.payload, {"metrics": {"ok": True}})


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


class TestTheSubagentGate(unittest.TestCase):
    """The gate is loaded by the injection, not by the scenario.

    Injecting agent definitions does not make them the only reachable ones: the
    subagent tool's scope is a parameter the model chooses, and the default reaches
    the library's built-in agents, none of which declares a model. A scenario that
    forgot to declare the gate would measure those instead, and say nothing.
    """

    def paths(self, bricks: dict, delta: dict) -> dict:
        from trysquare.scenario import Cell

        raw = MINIMAL | {
            "harness": bricks,
            "variants": {"none": {}, "c": delta or {"thinking": "high"}},
            "verdict": {"criterion": "overflow", "reference": "none"},
        }
        return runner.brick_paths(
            parse(raw), config.Config(path=Path("/x/trysquare.toml")), Cell("c", delta), Path("/x")
        )

    def test_injecting_agents_loads_the_gate(self):
        d = Path(tempfile.mkdtemp())
        (d / "explorer.md").write_text("---\nname: explorer\nmodel: a/b\n---\n")
        got = self.paths({"subagents": {"paths": ["explorer.md"]}}, {"harness": ["subagents"]})
        self.assertEqual(got["extensions"], [runner.AGENT_GATE])

    def test_a_cell_without_subagents_loads_nothing(self):
        """The baseline must not carry an extension the treatment introduced."""
        self.assertEqual(self.paths({}, {})["extensions"], [])

    def test_the_gate_ships_inside_the_package(self):
        """An installed wheel has no repository around it to resolve against."""
        self.assertTrue(runner.AGENT_GATE.is_file())
        self.assertEqual(runner.AGENT_GATE.parent, Path(runner.__file__).resolve().parent)


if __name__ == "__main__":
    unittest.main()
