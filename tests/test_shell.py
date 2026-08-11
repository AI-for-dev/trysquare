"""The effectful shell: argument building, config rules, output state, resume.

Nothing here launches an agent. What is tested is the part that decides *what*
would be launched and *what* may be relaunched, which is where a measurement is
silently lost or a standard silently bent.
"""

import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

from trysquare import agent, config, outputs, repo, runner, validation
from trysquare.measure import EMPTY, VALID, VALIDATOR_FAILED, Run
from trysquare.scenario import Validator, parse
from tests import gitrepo
from tests.gitrepo import a_repo
from tests.test_scenario import GRID, MINIMAL


class TestArgv:
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
        assert "-a" in self.args()

    def test_discovery_is_switched_off_wholesale(self):
        args = self.args()
        for flag in ("-ns", "-np", "-ne"):
            assert flag in args

    def test_context_discovery_is_off_unless_the_cell_provides_one(self):
        assert "-nc" in self.args()
        assert "-nc" not in self.args(has_context=True)

    def test_thinking_is_always_explicit(self):
        args = self.args(thinking="high")
        assert args[args.index("--thinking") + 1] == "high"

    def test_bricks_are_passed_by_path(self):
        args = self.args(extensions=[Path("/x/ext")], skills=[Path("/x/skill")])
        assert args[args.index("-e") + 1] == "/x/ext"
        assert args[args.index("--skill") + 1] == "/x/skill"

    def test_the_prompt_is_last(self):
        assert self.args()[-1] == "do it"


class TestTheModelThatAnswered:
    """`--model` takes a pattern, so the declared value and what ran are two facts.

    Measured on a real matrix: a scenario declaring `gemma-4` ran `gemma-4-31b` in all
    six runs, and nothing in the archive said so.
    """

    def test_a_pattern_that_expands_still_names_its_model(self):
        assert agent.resolves_to("gemma-4", "gemma-4-31b")

    def test_an_exact_id_names_itself(self):
        assert agent.resolves_to("gemma-4-31b", "gemma-4-31b")

    def test_a_different_model_is_not_what_the_pattern_named(self):
        """The fallback case: the machine's defaultModel answering for a declared one."""
        assert not agent.resolves_to("gemma-4", "claude-sonnet-5")

    def test_a_provider_prefix_says_nothing_about_the_model(self):
        assert agent.resolves_to("gemma-4", "ilaas/gemma-4-31b")
        assert agent.resolves_to("ilaas/gemma-4", "local/google/gemma-4-31b")

    def test_the_thinking_shorthand_is_not_part_of_the_name(self):
        """The agent accepts `--model sonnet:high`, and a reasoning level is not a model."""
        assert agent.resolves_to("gemma-4:high", "gemma-4-31b")

    def test_the_comparison_ignores_case_and_padding(self):
        assert agent.resolves_to(" Gemma-4 ", "gemma-4-31b")

    def test_the_archive_records_the_last_model_the_sessions_name(self, tmp_path):
        """The last, because a session may switch models and what ran last is what
        produced the diff being scored."""
        session = tmp_path / "s.jsonl"
        session.write_text(
            "\n".join(
                json.dumps(e)
                for e in (
                    {"type": "model_change", "model": "gemma-4-31b"},
                    {"type": "message", "message": {}},
                    {"type": "model_change", "model": "gemma-4-90b"},
                )
            )
        )
        assert runner.recorded_model([session]) == "gemma-4-90b"

    def test_no_session_records_no_model_rather_than_the_pattern(self, tmp_path):
        """Filling the gap with the intention is exactly how a fallback would hide."""
        assert runner.recorded_model([]) is None
        bare = tmp_path / "b.jsonl"
        bare.write_text(json.dumps({"type": "message", "message": {}}))
        assert runner.recorded_model([bare]) is None


class TestCloneArgv:
    """The flags that make a clone the pinned state and nothing else."""

    @pytest.mark.parametrize("keep_tags", [False, True])
    def test_a_clone_is_pinned_to_the_tag(self, keep_tags):
        args = repo.clone_argv("/s", "etalon-v1", Path("/x"), keep_tags=keep_tags)
        assert "--single-branch" in args
        assert args[args.index("--branch") + 1] == "etalon-v1"

    def test_a_run_clone_drops_the_tags_and_a_pinned_source_keeps_them(self):
        """Every run clones *from* the pinned source by tag name.

        Git happens to keep the tag named by `--branch` even under `--no-tags`, so this
        is not the difference between working and not working. It is the difference
        between resting on documented behaviour and resting on an accident.
        """
        assert "--no-tags" in repo.clone_argv("/s", "t", Path("/x"))
        assert "--no-tags" not in repo.clone_argv("/s", "t", Path("/x"), keep_tags=True)

    def test_a_url_reaches_git_verbatim(self):
        args = repo.clone_argv("https://h/x.git", "t", Path("/x"), keep_tags=True)
        assert "https://h/x.git" in args


class TestACommitEtalon:
    """An etalon may be a commit, because a tag can be moved and a commit cannot."""

    SHA = "d62ccd1f3255c8286ff6b5856860f7635f4d324e"

    @pytest.mark.parametrize(
        "etalon, commit",
        [
            (SHA, True),
            (SHA.upper(), False),
            (SHA[:12], False),
            ("etalon-v1", False),
            ("main", False),
            ("deadbeef" * 5 + "x", False),
        ],
    )
    def test_only_the_full_form_counts_as_a_commit(self, etalon, commit):
        """An abbreviation is not a pin: it designates one object only until the
        repository grows the one that collides with it."""
        assert repo.is_commit(etalon) is commit

    @pytest.mark.parametrize("keep_tags", [False, True])
    def test_a_commit_is_fetched_whole_because_branch_refuses_it(self, keep_tags):
        """`--branch` takes a ref only, and either narrowing flag can leave the wanted
        commit unreachable in a clone that otherwise looks complete."""
        args = repo.clone_argv("/s", self.SHA, Path("/x"), keep_tags=keep_tags)
        assert "--branch" not in args
        assert "--single-branch" not in args
        assert "--no-tags" not in args
        assert args[-2:] == ["/s", "/x"]

    def test_cloning_at_a_commit_leaves_head_on_it(self, tmp_path):
        source = a_repo({"a.txt": "one"})
        first = gitrepo.git(source, "rev-parse", "HEAD").strip()
        gitrepo.write(source, {"a.txt": "two"})
        gitrepo.git(source, "commit", "-qam", "second")

        clone = repo.clone(source, first, tmp_path / "clone")

        assert repo.commit_of(clone, "HEAD") == first
        assert (clone / "a.txt").read_text() == "one"

    def test_a_commit_no_branch_reaches_is_still_reachable(self, tmp_path):
        """The case `--single-branch` would have broken: the etalon is on a side
        branch, and the default branch never held it."""
        source = a_repo({"a.txt": "one"})
        gitrepo.git(source, "checkout", "-q", "-b", "aside")
        gitrepo.write(source, {"a.txt": "aside"})
        gitrepo.git(source, "commit", "-qam", "aside")
        wanted = gitrepo.git(source, "rev-parse", "HEAD").strip()
        gitrepo.git(source, "checkout", "-q", "-")

        clone = repo.clone(source, wanted, tmp_path / "clone")

        assert (clone / "a.txt").read_text() == "aside"

    def test_a_commit_the_source_lacks_is_named_and_leaves_nothing(self, tmp_path):
        """A half-written clone reused later is the kind of failure that produces a
        plausible measurement, so the target goes away with the error."""
        source = a_repo({"a.txt": "one"})
        target = tmp_path / "clone"

        with pytest.raises(repo.RepoError) as raised:
            repo.clone(source, "0" * 40, target)

        assert "0" * 40 in str(raised.value)
        assert not target.exists()

    def test_the_etalon_reads_the_same_whether_it_is_a_tag_or_a_commit(self, tmp_path):
        """Everything downstream reads the etalon as a revision and cannot tell the two
        apart, which is what makes the commit form a drop-in pin."""
        source = a_repo({"a.txt": "one"})
        sha = gitrepo.git(source, "rev-parse", "HEAD").strip()

        by_tag = repo.clone(source, "etalon-v1", tmp_path / "tag")
        by_sha = repo.clone(source, sha, tmp_path / "sha")

        assert repo.etalon_file(by_sha, sha, "a.txt") == repo.etalon_file(
            by_tag, "etalon-v1", "a.txt"
        )
        assert repo.etalon_files(by_sha, sha) == ["a.txt"]
        assert repo.commit_of(by_sha, sha) == sha


class TestConfigRules:
    def write(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / config.CONFIG_NAME).write_text(text)
        return d / config.CONFIG_NAME

    def test_machine_paths_resolve_logical_names(self):
        path = self.write('[repos]\nmy-repo = "../my-repo"\n[harness]\nsub = "~/w/sub"\n')
        c = config.load(path)
        assert str(c.repo("my-repo")).endswith("my-repo")
        assert c.repo("my-repo").is_absolute(), "relative to the config file"

    def test_an_unknown_logical_name_names_what_is_known(self):
        c = config.load(self.write('[repos]\nmy-repo = "../my-repo"\n'))
        with pytest.raises(config.ConfigError) as e:
            c.repo("ghost")
        assert "my-repo" in str(e.value)

    @pytest.mark.parametrize(
        "key,value",
        [
            ("provider", "ilaas"),
            ("model", "gemma-4-31b"),
            ("thinking", "high"),
            ("etalon", "etalon-v1"),
            ("repetitions", 20),
        ],
    )
    def test_the_config_may_not_decide_what_is_measured(self, key, value):
        """The rule that stops a scenario from measuring differently elsewhere."""
        path = self.write(f"[defaults]\n{key} = {json.dumps(value)}\n")
        with pytest.raises(config.ConfigError) as e:
            config.load(path)
        assert key in str(e.value)

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
            assert config.is_remote(value), value
        for value in local:
            assert not config.is_remote(value), value

    def test_a_url_is_never_mangled_into_a_path(self):
        """The whole reason a URL is classified before `expand()` ever sees it.

        `Path("https://h/x.git")` collapses the double slash into `https:/h/x.git`, a
        *relative* path: git would then be handed a directory resolved against the
        config file's parent, and the failure is a clone of nothing rather than an error.
        """
        url = "https://h/x.git"
        c = config.load(self.write(f'[repos]\nmy-repo = "{url}"\n'))
        assert c.remote("my-repo") == url
        assert c.remote("my-repo") != str(Path(url))

    def test_a_url_does_not_inherit_from_the_shell(self):
        """A username or token taken from the environment is invisible inheritance.

        It appears in no archive, and the value that actually ran would be whatever the
        shell happened to hold - the defect this whole module exists to abolish.
        """
        c = config.load(self.write('[repos]\nmy-repo = "git@h:$USER/x.git"\n'))
        assert c.remote("my-repo") == "git@h:$USER/x.git"

    def test_a_local_entry_has_no_url(self):
        c = config.load(self.write('[repos]\nmy-repo = "../my-repo"\n[harness]\nsub = "~/w/sub"\n'))
        assert c.remote("my-repo") is None
        assert c.harness_remote("sub") is None

    def test_a_url_has_no_local_directory_until_it_is_cloned(self):
        c = config.load(self.write('[repos]\nmy-repo = "https://h/x.git"\n'))
        with pytest.raises(config.ConfigError) as e:
            c.repo("my-repo")
        assert "https://h/x.git" in str(e.value)

    def test_a_harness_entry_may_be_a_url_too(self):
        """The two sections have the same shape; accepting one and refusing the other
        would be an asymmetry nobody could guess."""
        c = config.load(self.write('[harness]\nsub = "https://h/sub.git"\n'))
        assert c.harness_remote("sub") == "https://h/sub.git"

    def test_an_unknown_logical_name_names_what_is_known_from_remote_too(self):
        c = config.load(self.write('[repos]\nmy-repo = "../my-repo"\n'))
        with pytest.raises(config.ConfigError) as e:
            c.remote("ghost")
        assert "my-repo" in str(e.value)

    def test_load_fallbacks_are_allowed(self):
        c = config.load(self.write("[defaults]\nconcurrency = 2\ntimeout = 60\n"))
        assert c.fallback("concurrency") == 2
        assert c.fallback("timeout") == 60

    def test_no_config_file_is_not_an_error(self, tmp_path):
        c = config.load(start=tmp_path)
        assert c.fallback("concurrency") == config.BUILTIN_DEFAULTS["concurrency"]

    def test_a_missing_explicit_config_is_an_error(self, tmp_path):
        with pytest.raises(config.ConfigError):
            config.load(tmp_path / "nope.toml")

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
        with pytest.raises(config.ConfigError) as e:
            config.load(path)
        message = str(e.value)
        assert "scenario file" in message
        assert str(path) in message

    def test_a_scenario_pinning_bricks_is_not_mistaken_for_either_file(self):
        """[harness] is legitimate in both, so a file with both stays ambiguous."""
        raw = {"scenario": {"name": "t"}, "harness": {"subagent": "v0.3.0"}}
        assert config.which_file(raw) is None

    def test_the_real_config_reads_as_a_config(self):
        assert config.which_file({"repos": {"my-repo": "../my-repo"}, "defaults": {}}) == "config"

    def test_an_empty_file_is_neither(self):
        assert config.which_file({}) is None


class TestPinnedSources:
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
        assert base.parent == c.workdir() / "sources"
        assert base != runner.source_dir(c, "my-repo", "https://h/other.git", "etalon-v1")
        assert base != runner.source_dir(c, "my-repo", self.URL, "etalon-v2")
        assert base == runner.source_dir(c, "my-repo", self.URL, "etalon-v1"), "stable"

    def test_a_slash_in_a_tag_stays_one_directory(self):
        """`release/1.0` would otherwise put the clone somewhere nobody named."""
        c = self.conf(self.URL)
        directory = runner.source_dir(c, "my-repo", self.URL, "release/1.0")
        assert directory.parent == c.workdir() / "sources"
        assert "release-1.0" in directory.name

    def test_a_local_entry_is_never_cloned(self, tmp_path):
        existing = tmp_path
        c, calls, result = self.pin_calls(str(existing))
        assert result == existing
        assert calls == []

    def test_a_missing_local_entry_names_the_config_file(self):
        with pytest.raises(repo.RepoError) as e:
            self.pin_calls("../nowhere")
        assert config.CONFIG_NAME in str(e.value)
        assert "nowhere" in str(e.value)

    def test_a_remote_is_cloned_once_and_reused(self):
        _, calls, result = self.pin_calls(self.URL, times=3)
        assert len(calls) == 1
        assert (result / runner.READY).is_file()

    def test_a_marker_left_by_another_url_forces_a_fresh_clone(self):
        """A directory that cannot say which repository it holds must not be trusted."""
        c = self.conf(self.URL)
        target = runner.source_dir(c, "my-repo", self.URL, "etalon-v1")
        target.mkdir(parents=True)
        (target / runner.READY).write_text("my-repo@etalon-v1\nhttps://h/somewhere-else.git\n")
        calls: list = []
        with mock.patch.object(runner.repo_mod, "pin", self.fake_pin(calls)):
            runner.prepare_source(c, "my-repo", "etalon-v1")
        assert len(calls) == 1

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
        assert len(calls) == 1
        assert len(set(results)) == 1

    def test_a_clone_failure_names_the_url_the_tag_and_the_config(self):
        """A bad URL is a config bug, so the refusal has to say which file to edit."""
        with pytest.raises(repo.RepoError) as e:
            self.pin_calls(self.URL, fail=True)
        message = str(e.value)
        assert self.URL in message
        assert "etalon-v1" in message
        assert config.CONFIG_NAME in message
        assert "Nothing was measured" in message


class TestNaming:
    def test_the_directory_name_carries_the_experiment_identity(self):
        s = parse(GRID)
        assert outputs.experiment_name(s) == "t_etalon-v1_ilaas_gemma-4-31b_n10"

    def test_changing_repetitions_changes_the_directory(self):
        """Which is why a quick run cannot corrupt a published matrix."""
        s = parse(GRID)
        assert outputs.experiment_name(s) != outputs.experiment_name(s, 3)
        assert outputs.experiment_name(s, 3).endswith("_n3")

    def test_run_ids_are_stable_and_opaque(self):
        first = outputs.run_id("t", "rule / high", 3)
        assert first == outputs.run_id("t", "rule / high", 3)
        assert "rule" not in first
        assert first != outputs.run_id("t", "rule / off", 3)


class TestResume:
    def output(self) -> outputs.Output:
        return outputs.Output(Path(tempfile.mkdtemp()), parse(GRID))

    def test_the_plan_covers_every_cell_and_repetition(self):
        o = self.output()
        assert len(o.plan()) == 60

    def test_everything_starts_missing(self):
        o = self.output()
        state = o.initial_state()
        assert len(o.to_do(state)) == 60

    def test_a_valid_run_is_never_relaunched(self):
        """The whole protection: a resume has no power over a produced result."""
        o = self.output()
        state = o.initial_state()
        run_id = next(iter(state["runs"]))
        o.record(state, run_id, Run(run_id, "none / off", 0, state=VALID))
        assert run_id not in dict(o.to_do(state))

    def test_an_empty_run_is_relaunched(self):
        """It produced no result, so there is nothing to select between."""
        o = self.output()
        state = o.initial_state()
        run_id = next(iter(state["runs"]))
        o.record(state, run_id, Run(run_id, "none / off", 0, state=EMPTY))
        assert run_id in dict(o.to_do(state))

    def test_a_failed_validator_is_not_remeasured(self):
        """It needs re-scoring, which costs no tokens. Re-measuring it would let a
        resume change a run that had already produced something."""
        o = self.output()
        state = o.initial_state()
        run_id = next(iter(state["runs"]))
        o.record(state, run_id, Run(run_id, "none / off", 0, state=VALIDATOR_FAILED))
        assert run_id not in dict(o.to_do(state))

    def test_attempts_accumulate_so_an_abusive_resume_leaves_a_trace(self):
        o = self.output()
        state = o.initial_state()
        run_id = next(iter(state["runs"]))
        for _ in range(3):
            o.record(state, run_id, Run(run_id, "none / off", 0, state=EMPTY, attempts=1))
        assert state["runs"][run_id]["attempts"] == 3

    def test_a_ledger_disagreeing_with_its_directory_is_refused(self):
        o = self.output()
        o.prepare()
        o.write_state(o.initial_state() | {"repetitions": 20})
        with pytest.raises(ValueError):
            o.load_or_create_state()

    def test_only_restricts_the_launch_and_leaves_it_incomplete(self):
        o = self.output()
        state = o.initial_state()
        todo = o.to_do(state, only=("none / off",))
        assert len(todo) == 10
        counts = o.summarise(state)
        assert not state["complete"]
        assert "This matrix is incomplete" in outputs.incomplete_note(counts)

    def test_the_load_is_recorded_whatever_its_origin(self):
        """Retries depend on it, and every cost column depends on retries."""
        state = self.output().initial_state(overrides={"concurrency": 10})
        assert state["concurrency"] == 5
        assert state["overrides"] == {"concurrency": 10}


class TestACellTheLedgerDoesNotKnow:
    """A scenario edited between two launches, and a directory name that cannot say so."""

    def output(self) -> outputs.Output:
        return outputs.Output(Path(tempfile.mkdtemp()), parse(GRID))

    def measured_without(self, o: outputs.Output, cell: str) -> dict:
        """A finished ledger from before the scenario declared `cell`."""
        state = o.initial_state()
        state["runs"] = {
            rid: meta | {"state": VALID}
            for rid, meta in state["runs"].items()
            if meta["cell"] != cell
        }
        return state

    def test_a_cell_the_ledger_never_heard_of_counts_as_added(self):
        o = self.output()
        added, stale = o.cell_drift(self.measured_without(o, "ticket / high"))
        assert added == {"ticket / high": 10}
        assert not stale

    def test_a_cell_the_scenario_dropped_counts_as_stale(self):
        """A renamed variant leaves its old runs behind, and they are still rendered."""
        o = self.output()
        previous = o.initial_state()
        previous["runs"]["deadbeef"] = {"cell": "witness", "repetition": 0, "state": VALID}
        added, stale = o.cell_drift(previous)
        assert stale == {"witness": 1}
        assert not added

    def test_a_ledger_that_matches_the_scenario_drifts_by_nothing(self):
        """A note that fires on every ordinary resume is a note nobody reads."""
        o = self.output()
        assert o.cell_drift(o.initial_state()) == ({}, {})

    def test_resuming_a_scenario_that_grew_a_cell_leaves_the_measured_runs_alone(self):
        """`load_or_create_state` adds the new ids as missing, and `to_do` returns
        those and nothing else."""
        o = self.output()
        o.prepare()
        o.write_state(self.measured_without(o, "ticket / high"))
        todo = o.to_do(o.load_or_create_state())
        assert {meta["cell"] for _, meta in todo} == {"ticket / high"}
        assert len(todo) == 10

    def with_a_dropped_cell(self, o: outputs.Output) -> dict:
        """A ledger from before a variant was renamed: eight of its runs measured,
        two that produced nothing."""
        state = o.initial_state()
        for meta in state["runs"].values():
            meta["state"] = VALID
        for i in range(10):
            state["runs"][f"w{i:02d}"] = {
                "cell": "witness",
                "repetition": i,
                "state": VALID if i < 8 else EMPTY,
                "attempts": 1,
            }
        return state

    def test_a_run_of_a_cell_the_scenario_dropped_is_never_launched(self):
        """There is nothing to launch it as. Launching it anyway spent a clone to
        reach `unknown cell`, recorded it empty, and brought it back next pass."""
        o = self.output()
        assert o.to_do(self.with_a_dropped_cell(o)) == []

    def test_a_dropped_cell_does_not_hold_the_matrix_incomplete(self):
        """No launch could ever lift it, so the matrix could never publish again."""
        o = self.output()
        state = self.with_a_dropped_cell(o)
        o.summarise(state)
        assert state["complete"]

    def test_a_dropped_cell_keeps_its_runs_in_the_ledger(self):
        """It is excluded from what runs and from what counts, never deleted: a cell
        must not vanish from a synthesis silently."""
        o = self.output()
        state = self.with_a_dropped_cell(o)
        o.summarise(state)
        assert sum(1 for m in state["runs"].values() if m["cell"] == "witness") == 10


class TestACellThatChangedUnderItsName:
    """`state.json` records what each cell declares, so a resume cannot complete a
    matrix whose cells no longer mean what their runs were measured under."""

    def output(self, raw: dict, root=None) -> outputs.Output:
        return outputs.Output(root or Path(tempfile.mkdtemp()), parse(raw))

    def digest(self, raw: dict, cell: str) -> str:
        s = parse(raw)
        return outputs.cell_fingerprint(s, s.cell(cell))

    def edited(self, **sections) -> dict:
        """The same scenario with one section rewritten, which is what an author does."""
        return GRID | {name: GRID[name] | values for name, values in sections.items()}

    def reloaded(self, before: dict, after: dict, measured: str | None = None):
        """A ledger written for `before`, then read back against `after`."""
        o = self.output(before)
        o.prepare()
        state = o.initial_state()
        if measured:
            rid = outputs.run_id("t", measured, 0)
            o.record(state, rid, Run(rid, measured, 0, state=VALID))
        o.write_state(state)
        later = self.output(after, o.directory.parent)
        return later, later.load_or_create_state()

    def test_the_ledger_records_what_every_cell_declares(self):
        o = self.output(GRID)
        assert sorted(o.initial_state()["cells"]) == sorted(c.name for c in parse(GRID).cells)

    def test_two_cells_declaring_different_things_are_told_apart(self):
        """A digest that is constant over a matrix guards nothing."""
        assert self.digest(GRID, "none / off") != self.digest(GRID, "rule / high")

    def test_the_same_declaration_digests_the_same_way_twice(self):
        """One that moved on its own would refuse every resume of an unedited file."""
        assert self.digest(GRID, "rule / high") == self.digest(GRID, "rule / high")

    def test_a_cell_that_inherits_the_task_prompt_moves_when_it_is_edited(self):
        """The case a delta-only digest misses: the cell declares nothing of its own
        and still measures something else."""
        after = self.edited(task={"prompt": "do the other thing"})
        assert self.digest(GRID, "none / off") != self.digest(after, "none / off")

    def test_a_cell_declaring_its_own_prompt_is_not_moved_by_the_baseline_one(self):
        """`ticket / off` overrides the prompt, so a refusal there would fire on
        something the cell cannot read."""
        after = self.edited(task={"prompt": "do the other thing"})
        assert self.digest(GRID, "ticket / off") == self.digest(after, "ticket / off")

    def test_a_cell_that_inherits_the_thinking_level_moves_when_it_is_edited(self):
        """`thinking` is not in the directory name, so nothing else would catch it."""
        after = self.edited(agent={"thinking": "high"})
        assert self.digest(GRID, "none / off") != self.digest(after, "none / off")

    def test_a_cell_moves_when_the_brick_it_names_is_repointed(self):
        """The delta names the brick, `[harness]` says what the brick is: a tag moved
        there changes what the cell loads without changing its delta."""
        before = MINIMAL | {
            "variants": {"none": {}, "+kit": {"harness": ["kit"]}},
            "harness": {"kit": {"repo": "https://example.invalid/kit", "tag": "v1"}},
        }
        after = before | {"harness": {"kit": {"repo": "https://example.invalid/kit", "tag": "v2"}}}
        assert self.digest(before, "+kit") != self.digest(after, "+kit")
        assert self.digest(before, "none") == self.digest(after, "none")

    def test_a_cell_whose_runs_produced_nothing_takes_the_new_declaration(self):
        """Nothing was measured under the old one, so there is nothing to protect."""
        after = self.edited(task={"prompt": "do the other thing"})
        o, state = self.reloaded(GRID, after)
        assert o.changed_cells(state) == []
        assert state["cells"]["none / off"] == self.digest(after, "none / off")

    def test_a_cell_with_a_measured_run_is_refused(self):
        """A resume keeps that run, so completing the matrix would publish two
        configurations as one."""
        after = self.edited(task={"prompt": "do the other thing"})
        o, state = self.reloaded(GRID, after, measured="none / off")
        assert o.changed_cells(state) == ["none / off"]

    def test_a_failed_validator_freezes_the_cell_too(self):
        """It produced a result, and re-scoring a run measured under another
        declaration publishes the same two configurations."""
        after = self.edited(task={"prompt": "do the other thing"})
        o = self.output(GRID)
        o.prepare()
        state = o.initial_state()
        rid = outputs.run_id("t", "none / off", 0)
        o.record(state, rid, Run(rid, "none / off", 0, state=VALIDATOR_FAILED))
        o.write_state(state)
        later = self.output(after, o.directory.parent)
        assert later.changed_cells(later.load_or_create_state()) == ["none / off"]

    def test_a_ledger_written_before_fingerprints_existed_is_not_refused(self):
        """A digest that was never recorded cannot have changed, and inventing one now
        would record today's declaration as the one those runs were measured under."""
        o = self.output(GRID)
        o.prepare()
        state = o.initial_state()
        rid = outputs.run_id("t", "none / off", 0)
        o.record(state, rid, Run(rid, "none / off", 0, state=VALID))
        del state["cells"]
        o.write_state(state)

        after = self.output(self.edited(task={"prompt": "do the other thing"}), o.directory.parent)
        state = after.load_or_create_state()
        assert after.changed_cells(state) == []
        assert "none / off" not in state["cells"]


class TestDiscardingOneCellSResults:
    """`Output.replayed`: what `--overwrite CELL` does to a ledger, and to nothing else.

    A resume protects every result on disk. This is the one operation that gives some of
    them up, so what it reaches has to be exactly what it was told to reach.
    """

    def measured(self, raw: dict, cell: str, **extra):
        """A ledger holding one measured run of `cell`, and the digest it was measured
        under."""
        o = outputs.Output(Path(tempfile.mkdtemp()), parse(raw))
        state = o.initial_state()
        rid = outputs.run_id("t", cell, 0)
        o.record(state, rid, Run(rid, cell, 0, state=VALID, detail="a validator said so"))
        state["runs"][rid].update(extra)
        return o, state, rid

    def test_a_run_of_a_named_cell_is_missing_again(self):
        o, state, rid = self.measured(GRID, "none / off")
        o.replayed(state, ("none / off",))
        assert state["runs"][rid]["state"] == outputs.MISSING
        assert rid in {run_id for run_id, _ in o.to_do(state, ("none / off",))}

    def test_what_described_the_discarded_measurement_goes_with_it(self):
        """`attempts` and `detail` both describe a result this launch gives up, and a
        detail left behind would outlive the run that explained it."""
        o, state, rid = self.measured(GRID, "none / off")
        o.replayed(state, ("none / off",))
        assert state["runs"][rid]["attempts"] == 0
        assert "detail" not in state["runs"][rid]

    def test_a_carried_run_measured_here_is_this_matrix_s_own(self):
        """The flag says the run was measured elsewhere and never re-measured, which is
        exactly what stops being true."""
        o, state, rid = self.measured(GRID, "none / off", carried=True)
        o.replayed(state, ("none / off",))
        assert "carried" not in state["runs"][rid]

    def test_no_other_cell_is_touched(self):
        o, state, rid = self.measured(GRID, "none / off")
        other = outputs.run_id("t", "rule / high", 0)
        o.record(state, other, Run(other, "rule / high", 0, state=VALID))
        o.replayed(state, ("none / off",))
        assert state["runs"][other]["state"] == VALID

    def test_the_named_cell_takes_today_s_declaration(self):
        """The half `load_or_create_state` cannot do: it freezes the digest of every cell
        that produced a result, and this cell's result is being given up."""
        o, state, _ = self.measured(GRID, "none / off")
        state["cells"]["none / off"] = "0000000000000000"
        o.replayed(state, ("none / off",))
        assert state["cells"]["none / off"] == o.fingerprints()["none / off"]
        assert o.changed_cells(state) == []

    def test_a_cell_it_keeps_stays_frozen(self):
        """Its result is kept, so the declaration it was measured under is what the
        ledger has to go on saying it was measured under."""
        o, state, _ = self.measured(GRID, "none / off")
        state["cells"]["rule / high"] = "0000000000000000"
        o.replayed(state, ("none / off",))
        assert state["cells"]["rule / high"] == "0000000000000000"


class TestBlindness:
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
        assert report["judge"]["blind"]

    def test_a_piece_that_varies_leaks_the_treatment(self):
        """In the 2x3 matrix the treatment *is* the prompt, so handing the judge
        the prompt reveals which cell it is scoring."""
        report = validation.blindness(self.scenario_with_judge(["context", "diff"]))
        assert not report["judge"]["blind"]
        assert "context" in report["judge"]["leaking"]
        lines = validation.describe_blindness(report, cells=2)
        assert any("partially blind" in line for line in lines)

    def test_no_judge_means_nothing_to_report(self):
        assert validation.blindness(parse(MINIMAL)) == {}

    def test_a_blind_context_withholds_the_cell(self, tmp_path):
        d = tmp_path
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
            given=["game/probe.test.js"],
        )
        context = json.loads(path.read_text())
        assert "cell" not in context
        assert "repetition" not in context
        assert "repo" in context
        # A brick is a configuration: a judge told this tree was handed a probe knows
        # it is looking at the treatment.
        assert "given" not in context

    def test_a_script_context_keeps_the_cell(self, tmp_path):
        d = tmp_path
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
        assert context["cell"] == "rule / high"
        assert context["etalon"]["tag"] == "etalon-v1"

    def test_the_declared_test_command_travels_in_the_context(self, tmp_path):
        """A validator scoring `tests` reads the command here rather than guessing it.

        Carried **as the scenario wrote it**, a string. One fact, one representation: an
        archived context read against the scenario file six months later says the same thing,
        with no transformation to know about.
        """
        d = tmp_path
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
        assert context["test_command"] == "node --test 'game/**/*.test.js'"

    def test_a_blind_context_still_carries_the_test_command(self, tmp_path):
        """It is a property of the **task**, identical in every cell, so it tells a
        judge nothing about which configuration produced the work it scores."""
        d = tmp_path
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
        assert "cell" not in context
        assert context["test_command"] == "node --test"

    def test_the_context_and_the_loader_split_it_the_same_way(self, tmp_path):
        """The point of a single rule: what loads is what runs, with nothing in between.

        Two implementations would let a command be split two slightly different ways, and
        therefore measured two slightly different ways.
        """
        from trysquare.scenario import split_command

        d = tmp_path
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
        assert carried == written
        assert split_command(carried) == ("node", "--test", "game/**/*.test.js")

    def test_prepare_travels_as_written_too(self, tmp_path):
        d = tmp_path
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
        assert json.loads(path.read_text())["prepare"] == ["npm ci"]

    def test_a_scenario_with_no_command_writes_no_key(self, tmp_path):
        """An absent key is what a validator reads as "this scenario names no suite",
        which is not the same fact as an empty command."""
        d = tmp_path
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
        assert "test_command" not in json.loads(path.read_text())


class TestWhatTheHarnessComputesOnce:
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
            assert callable(getattr(repo, name)), name

    def test_changed_files_sees_a_new_file(self):
        """`--intent-to-add` is what makes an untracked file visible to `git diff`.
        Without it a change written into a file created for the occasion is invisible."""
        d = a_repo({"a.js": "one\n"})
        (d / "b.js").write_text("two\n")
        assert repo.changed_files(d) == ["b.js"]

    def test_changed_files_sees_an_edit(self):
        d = a_repo({"a.js": "one\n"})
        (d / "a.js").write_text("two\n")
        assert repo.changed_files(d) == ["a.js"]

    def test_changed_files_sees_a_deletion(self):
        """`git add -A` stages a removal even under `--intent-to-add`, so the index and
        the working tree then agree and a plain `git diff` reports nothing. An agent
        deleting the repository's own test file to make the suite green scored
        `touched = []` - not badly, but as having done nothing at all."""
        d = a_repo({"a.js": "one\n", "b.js": "two\n"})
        (d / "b.js").unlink()
        assert repo.changed_files(d) == ["b.js"]

    def test_the_patch_carries_a_deletion(self):
        """And the archive agreed with the silence: an empty patch, so a replay rebuilt
        a tree where the deleted file was still there."""
        d = a_repo({"a.js": "one\n", "b.js": "two\n"})
        (d / "b.js").unlink()
        assert "deleted file" in repo.diff(d)

    def test_an_untouched_clone_changed_nothing(self):
        """An empty list is a measurement - the agent did not work - so it must not be
        confused with a failure to look."""
        assert repo.changed_files(a_repo({"a.js": "one\n"})) == []

    def test_etalon_files_reads_the_tag_and_not_the_working_tree(self):
        d = a_repo({"a.js": "one\n", "game/b.js": "two\n"})
        (d / "c.js").write_text("late\n")
        assert repo.etalon_files(d, "etalon-v1") == ["a.js", "game/b.js"]

    def test_both_travel_in_the_context(self, tmp_path):
        d = tmp_path
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
        assert context["touched"] == ["game/my-repo.js"]
        assert context["files"] == ["game/my-repo.js", "README.md"]

    def test_an_empty_touched_is_written_rather_than_dropped(self, tmp_path):
        """The one case where absent and empty must not be confused: an agent that
        changed nothing is a result, and a validator has to be able to read it."""
        d = tmp_path
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
        assert json.loads(path.read_text())["touched"] == []


class TestTheArchivedDiffReplays:
    """What `replay` promises rests on one thing: the archived patch has to apply.

    Found on a real matrix. An agent asked to fix a defect ran the declared suite to
    check itself, which left `__pycache__/*.pyc` in the clone, and `replay --rescore`
    then died on the first run:

        cannot apply binary patch to '__pycache__/counter.cpython-314.pyc'
        without full index line

    `git diff` records a binary file as a one-line placeholder that `git apply` refuses,
    and it refuses the **whole** patch - the source change with it. Nothing about the
    archive looks wrong until somebody tries to re-score it, months later.
    """

    def a_clone_of(self, source):
        return repo.clone(source, "etalon-v1", Path(tempfile.mkdtemp()) / "tree")

    def a_run_that_left_a_binary(self, source):
        work = self.a_clone_of(source)
        (work / "a.js").write_text("two\n")
        (work / "build.bin").write_bytes(b"\x00\x01\x02not text\xff")
        return repo.diff(work)

    def test_a_patch_holding_a_binary_file_applies(self):
        source = a_repo({"a.js": "one\n"})
        repo.apply_diff(self.a_clone_of(source), self.a_run_that_left_a_binary(source))

    def test_the_text_change_survives_the_binary_one(self):
        """The failure was total, so this is the half that matters: a `.pyc` nobody
        cares about must not take the source edit being re-scored down with it."""
        source = a_repo({"a.js": "one\n"})
        patch = self.a_run_that_left_a_binary(source)

        replayed = self.a_clone_of(source)
        repo.apply_diff(replayed, patch)
        assert (replayed / "a.js").read_text() == "two\n"
        assert (replayed / "build.bin").read_bytes() == b"\x00\x01\x02not text\xff"

    def test_a_patch_that_does_not_apply_names_the_run(self):
        """Sixty runs in a directory and one bad patch: the message has to say which."""
        source = a_repo({"a.js": "one\n"})
        with pytest.raises(repo.RepoError, match="a7f3"):
            repo.apply_diff(self.a_clone_of(source), "not a patch at all\n", what="a7f3")


class TestScriptValidatorPaths:
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

    def test_a_relative_output_still_reaches_the_context(self, tmp_path):
        root = tmp_path
        script = self.echo_script(root)
        context = self.context_under(root)
        relative = context.relative_to(root)

        previous = Path.cwd()
        os.chdir(root)
        try:
            result = validation.run_script(self.validator(script), relative, timeout=30)
        finally:
            os.chdir(previous)
        assert (result.detail or None) is None, result.stderr

    def test_a_python_validator_runs_on_the_harness_interpreter(self, tmp_path):
        """`#!/usr/bin/env python3` catches Python 3.9 on macOS, and the package needs
        3.11 for `tomllib`. A validator importing `trysquare.assay` under the shebang's
        interpreter would fail to import, which reads as an invalid run.

        Proved by a script that is neither executable nor has a usable shebang: only
        being handed to an interpreter can make it run.
        """
        root = tmp_path
        script = root / "unrunnable.py"
        script.write_text(
            "#!/nowhere/python3\n"
            "import json, sys\n"
            "json.load(open(sys.argv[1]))\n"
            'print(json.dumps({"metrics": {"ok": sys.version_info >= (3, 11)}}))\n'
        )
        script.chmod(0o644)
        result = validation.run_script(self.validator(script), self.context_under(root), timeout=30)
        assert (result.detail or None) is None, result.stderr
        assert result.payload["metrics"]["ok"], (
            "the validator ran on an interpreter older than the package requires"
        )

    def test_a_validator_in_another_language_is_left_alone(self, tmp_path):
        """The courtesy is for `.py` only. "Any executable, in any language" stays
        literally true for everything else."""
        root = tmp_path
        script = root / "echo.sh"
        script.write_text('#!/bin/sh\ncat "$1" > /dev/null\necho \'{"metrics":{"ok":true}}\'\n')
        script.chmod(0o755)
        result = validation.run_script(self.validator(script), self.context_under(root), timeout=30)
        assert (result.detail or None) is None, result.stderr
        assert result.payload["metrics"] == {"ok": True}
        assert result.payload == {"metrics": {"ok": True}}

    def test_an_absolute_output_works_as_it_always_did(self, tmp_path):
        root = tmp_path
        result = validation.run_script(
            self.validator(self.echo_script(root)), self.context_under(root), timeout=30
        )
        assert result.payload == {"metrics": {"ok": True}}


class TestThinkingPrecondition:
    """A subagent's thinking level cannot be declared, so it is verified."""

    def test_a_mismatch_is_refused_when_subagents_are_used(self):
        message = validation.check_thinking_precondition("off", "high", uses_subagents=True)
        assert message is not None
        assert "off" in message
        assert "high" in message

    def test_agreement_passes(self):
        assert validation.check_thinking_precondition("off", "off", True) is None

    def test_without_subagents_there_is_nothing_to_refuse(self):
        assert validation.check_thinking_precondition("off", "high", False) is None

    def test_unknown_ambient_setting_does_not_block(self):
        assert validation.check_thinking_precondition("off", None, True) is None


class TestAgentModels:
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
        assert meta["model"] == "ilaas/gemma-4-31b"
        assert meta["source"] == "file"

    def test_an_override_wins_and_is_recorded_as_such(self):
        """Two places may declare, so the trace settles which one applied."""
        p = self.write_agent("---\nname: explorer\ndescription: x\nmodel: a/b\n---\n")
        meta = repo.agent_frontmatter(p, override="ilaas/gemma-4-31b")
        assert meta["model"] == "ilaas/gemma-4-31b"
        assert meta["source"] == "scenario override"

    def test_an_agent_with_no_model_anywhere_is_refused(self):
        """Nine shipped agents were in this position and ran on the wrong provider."""
        p = self.write_agent("---\nname: explorer\ndescription: x\n---\n")
        meta = repo.agent_frontmatter(p)
        assert meta["model"] is None
        with pytest.raises(repo.RepoError) as e:
            repo.check_agent_models({"explorer": meta})
        assert "explorer" in str(e.value)

    def test_an_override_rescues_a_file_that_declares_nothing(self):
        p = self.write_agent("---\nname: explorer\ndescription: x\n---\n")
        meta = repo.agent_frontmatter(p, override="ilaas/gemma-4-31b")
        repo.check_agent_models({"explorer": meta})


def cell_bricks(bricks: dict, delta: dict) -> dict:
    """What `brick_paths` resolves for one cell of a minimal scenario."""
    from trysquare.scenario import Cell

    raw = MINIMAL | {
        "harness": bricks,
        "variants": {"none": {}, "c": delta or {"thinking": "high"}},
        "verdict": {"criterion": "overflow", "reference": "none"},
    }
    return runner.brick_paths(
        parse(raw), config.Config(path=Path("/x/trysquare.toml")), Cell("c", delta), Path("/x")
    )


class TestSkillBricks:
    """Several skill bricks may coexist, each cited by name in a variant.

    The routing used to hang on the brick being named exactly `skills`, so a
    scenario could never compare skill A alone against skill B alone: one brick
    held every path and a cell loaded all of it or none. `kind = "skills"`
    declares the routing instead of encoding it in a name.
    """

    def test_kind_routes_paths_to_skills_and_loads_no_gate(self):
        got = cell_bricks(
            {"skill-tdd": {"kind": "skills", "paths": ["skills/tdd"]}},
            {"harness": ["skill-tdd"]},
        )
        assert got["skills"] == [Path("/x/skills/tdd")]
        assert got["agents"] == []
        assert got["extensions"] == []

    def test_the_brick_named_skills_still_needs_no_kind(self):
        got = cell_bricks({"skills": {"paths": ["skills/tdd"]}}, {"harness": ["skills"]})
        assert got["skills"] == [Path("/x/skills/tdd")]

    def test_each_cell_loads_only_the_skills_it_cites(self):
        bricks = {
            "skill-tdd": {"kind": "skills", "paths": ["skills/tdd"]},
            "skill-research": {"kind": "skills", "paths": ["skills/research"]},
        }
        one = cell_bricks(bricks, {"harness": ["skill-tdd"]})
        other = cell_bricks(bricks, {"harness": ["skill-research"]})
        assert one["skills"] == [Path("/x/skills/tdd")]
        assert other["skills"] == [Path("/x/skills/research")]


PROBE = {"probe": {"kind": "files", "files": {"game/probe.test.js": "bricks/probe.test.js"}}}


class TestFilesBricks:
    """Material given to the *task*, resolved by where it lands in the tree."""

    def test_a_files_brick_resolves_its_source_against_the_scenario(self):
        got = cell_bricks(PROBE, {"harness": ["probe"]})
        assert got["files"] == {"game/probe.test.js": Path("/x/bricks/probe.test.js")}

    def test_a_files_brick_is_no_skill_and_no_agent(self):
        """It loads nothing into the agent library, so it must load no gate either."""
        got = cell_bricks(PROBE, {"harness": ["probe"]})
        assert got["skills"] == []
        assert got["agents"] == []
        assert got["extensions"] == []

    def test_a_cell_that_cites_nothing_is_given_nothing(self):
        """The baseline must not receive the probe the treatment introduced."""
        assert cell_bricks(PROBE, {}) == cell_bricks({}, {})

    def test_two_bricks_aiming_at_one_destination_are_refused(self):
        """Which of the two the agent read is a question the scenario cannot answer."""
        bricks = PROBE | {
            "other": {"kind": "files", "files": {"game/probe.test.js": "bricks/other.js"}}
        }
        with pytest.raises(RuntimeError, match="twice"):
            cell_bricks(bricks, {"harness": ["probe", "other"]})


class TestGivingFiles:
    """What `repo.give` leaves behind, in a real clone.

    Committed rather than excluded, which is the whole difference between material
    given to the task and harness plumbing: the agent may edit or delete a probe it
    was handed, and an untracked path records neither.
    """

    def clone(self, tmp_path, files=("game/neon.js",)):
        source = a_repo({name: "// etalon\n" for name in files})
        return repo.clone(source, "etalon-v1", tmp_path / "clone")

    def given(self, tmp_path, text="// probe\n", destination="game/probe.test.js"):
        probe = tmp_path / "probe.test.js"
        probe.write_text(text)
        clone = self.clone(tmp_path)
        prepared = repo.Prepared(path=clone, etalon="etalon-v1")
        repo.give(prepared, {destination: probe})
        return clone, prepared

    def test_the_file_lands_where_the_scenario_put_it(self, tmp_path):
        clone, prepared = self.given(tmp_path)
        assert (clone / "game/probe.test.js").read_text() == "// probe\n"
        assert prepared.given == ["game/probe.test.js"]

    def test_giving_costs_nothing_in_scope(self, tmp_path):
        """The commit is what makes the injection free: the diff is taken against it."""
        clone, _ = self.given(tmp_path)
        assert repo.changed_files(clone) == []
        assert repo.diff(clone) == ""

    def test_editing_what_was_given_shows_up(self, tmp_path):
        """A probe is exactly what an agent may be tempted to weaken until it passes."""
        clone, _ = self.given(tmp_path)
        (clone / "game/probe.test.js").write_text("// weakened\n")
        assert repo.changed_files(clone) == ["game/probe.test.js"]
        assert "weakened" in repo.diff(clone)

    def test_deleting_what_was_given_shows_up(self, tmp_path):
        clone, _ = self.given(tmp_path)
        (clone / "game/probe.test.js").unlink()
        assert repo.changed_files(clone) == ["game/probe.test.js"]

    def test_it_is_not_hidden_from_git(self, tmp_path):
        """`.git/info/exclude` is for harness plumbing, and would hide the tampering."""
        clone, prepared = self.given(tmp_path)
        assert prepared.injected == []
        exclude = clone / ".git" / "info" / "exclude"
        assert "probe" not in (exclude.read_text() if exclude.is_file() else "")

    def test_replacing_a_file_the_tag_holds_is_refused(self, tmp_path):
        """It would change the measured code where no diff could ever show it."""
        probe = tmp_path / "probe.test.js"
        probe.write_text("// probe\n")
        clone = self.clone(tmp_path)
        prepared = repo.Prepared(path=clone, etalon="etalon-v1")
        with pytest.raises(repo.RepoError, match="already exists"):
            repo.give(prepared, {"game/neon.js": probe})

    def test_a_missing_source_is_refused(self, tmp_path):
        clone = self.clone(tmp_path)
        prepared = repo.Prepared(path=clone, etalon="etalon-v1")
        with pytest.raises(repo.RepoError, match="not found"):
            repo.give(prepared, {"game/probe.test.js": tmp_path / "nowhere.js"})

    def test_the_patch_of_a_given_file_applies_to_a_rebuilt_tree(self, tmp_path):
        """What a replay does: put the same file back, then apply what the agent did.

        Excluded instead of committed, this patch would not exist at all - and the
        run's tampering would be unscoreable from the archive.
        """
        clone, _ = self.given(tmp_path)
        (clone / "game/probe.test.js").write_text("// weakened\n")
        patch = repo.diff(clone)

        again = self.clone(tmp_path / "second")
        probe = tmp_path / "probe.test.js"
        repo.give(repo.Prepared(path=again, etalon="etalon-v1"), {"game/probe.test.js": probe})
        repo.apply_diff(again, patch)
        assert (again / "game/probe.test.js").read_text() == "// weakened\n"

    def test_nothing_given_leaves_no_commit(self, tmp_path):
        clone = self.clone(tmp_path)
        before = repo.git(["rev-parse", "HEAD"], cwd=clone)
        repo.give(repo.Prepared(path=clone, etalon="etalon-v1"), {})
        assert repo.git(["rev-parse", "HEAD"], cwd=clone) == before


class TestTheSubagentGate:
    """The gate is loaded by the injection, not by the scenario.

    Injecting agent definitions does not make them the only reachable ones: the
    subagent tool's scope is a parameter the model chooses, and the default reaches
    the library's built-in agents, none of which declares a model. A scenario that
    forgot to declare the gate would measure those instead, and say nothing.
    """

    def test_injecting_agents_loads_the_gate(self, tmp_path):
        d = tmp_path
        (d / "explorer.md").write_text("---\nname: explorer\nmodel: a/b\n---\n")
        got = cell_bricks({"subagents": {"paths": ["explorer.md"]}}, {"harness": ["subagents"]})
        assert got["extensions"] == [runner.AGENT_GATE]

    def test_a_cell_without_subagents_loads_nothing(self):
        """The baseline must not carry an extension the treatment introduced."""
        assert cell_bricks({}, {})["extensions"] == []

    def test_the_gate_ships_inside_the_package(self):
        """An installed wheel has no repository around it to resolve against."""
        assert runner.AGENT_GATE.is_file()
        assert runner.AGENT_GATE.parent == Path(runner.__file__).resolve().parent
