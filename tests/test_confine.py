"""What one run may see, and the record that says what kept it there.

Nothing here spends a token: the backend is a spy that answers in place of the agent.
"""

import subprocess
from pathlib import Path

import pytest

from trysquare import config, confine, outputs, runner
from trysquare.cli import isolation_lines
from trysquare.measure import Run
from trysquare.scenario import parse

from tests import gitrepo
from tests.test_scenario import MINIMAL


class Spy:
    """A backend that records what it was asked to run, and runs nothing."""

    name = "spy"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], confine.Scope, dict]] = []

    def run(self, argv, scope, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), scope, kwargs))
        return subprocess.CompletedProcess(argv, 0, "1.0.2\n", "")


class TestTheMachineChooses:
    def write(self, tmp_path: Path, isolation: str) -> Path:
        path = tmp_path / config.CONFIG_NAME
        path.write_text(f"[isolation]\n{isolation}\n")
        return path

    def test_an_unknown_backend_is_refused_with_the_known_ones(self, tmp_path):
        with pytest.raises(config.ConfigError, match=r"'nnoe' is not one \(known: none\)"):
            config.load(self.write(tmp_path, 'backend = "nnoe"'))

    def test_a_setting_the_backend_does_not_take_is_refused(self, tmp_path):
        with pytest.raises(config.ConfigError, match="'none' backend takes no cpus"):
            config.load(self.write(tmp_path, 'backend = "none"\ncpus = 2'))

    def test_the_backend_named_is_the_backend_run(self, tmp_path):
        loaded = config.load(self.write(tmp_path, 'backend = "none"'))
        assert confine.backend(loaded.isolation).name == confine.NONE

    def test_a_machine_that_says_nothing_runs_unconfined(self):
        assert confine.backend(config.Config().isolation).name == confine.NONE


class TestOneRun:
    @pytest.fixture
    def measured(self, tmp_path, monkeypatch):
        """One run of a cell, through the spy, and what it recorded."""
        scenario = parse(MINIMAL)
        repo = gitrepo.a_repo({"a.py": "x = 1\n"})
        plan = runner.Plan(
            scenario=scenario,
            config=config.Config(
                repos={"my-repo": str(repo)},
                defaults=config.BUILTIN_DEFAULTS | {"workdir": str(tmp_path / "work")},
                isolation={"backend": Spy.name},
            ),
            output=outputs.Output(tmp_path / "out", scenario),
            repo_path=repo,
            repo_source=str(repo),
            todo=[],
            overrides={},
            blindness={},
            notes=[],
        )
        spy = Spy()
        monkeypatch.setitem(confine.BACKENDS, Spy.name, lambda: spy)
        run = runner.one_run(plan, "abcd1234", {"cell": "none", "repetition": 0})
        return run, spy, tmp_path / "work" / plan.output.directory.name / "abcd1234"

    def test_the_run_says_what_it_ran_inside(self, measured):
        run, _, _ = measured
        assert run.isolation == "spy"

    def test_the_version_and_every_attempt_go_through_the_backend(self, measured):
        """The spy produces nothing, so the run is retried up to `attempts`."""
        _, spy, _ = measured
        assert [argv[1] for argv, _, _ in spy.calls] == ["--version"] + ["-p"] * 3

    def test_the_run_may_write_its_clone_and_its_session_and_nothing_else(self, measured):
        _, spy, work = measured
        _, scope, kwargs = spy.calls[-1]
        assert scope.writable == (work / "repo", work / "session")
        assert kwargs["cwd"] == work / "repo"


class TestTheSynthesisSays:
    def test_a_matrix_measured_without_a_boundary_is_warned_about(self):
        [line] = isolation_lines([Run("a", "c", 0, isolation="none")])
        assert line.startswith("- isolation `none`: runs were not isolated")

    def test_an_archive_that_predates_the_record_says_nothing(self):
        assert isolation_lines([Run("a", "c", 0)]) == []
