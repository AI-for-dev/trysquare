"""What one run may see, and the record that says what kept it there.

Nothing here spends a token. The backend is a spy that answers in place of the agent, or
docker running something that is not an agent.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from trysquare import agent, config, confine, outputs, runner
from trysquare.cli import isolation_lines
from trysquare.measure import Run
from trysquare.scenario import parse

from tests import gitrepo
from tests.test_scenario import MINIMAL


class Spy:
    """A backend that records what it was asked to run, and runs nothing."""

    name = "spy"
    image = "sha256:spy"
    where = "in a spy"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], confine.Scope, dict]] = []

    def prepare(self, image) -> None:
        pass

    def run(self, argv, scope, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), scope, kwargs))
        return subprocess.CompletedProcess(argv, 0, "1.0.2\n", "")


class TestTheMachineChooses:
    def write(self, tmp_path: Path, isolation: str) -> Path:
        path = tmp_path / config.CONFIG_NAME
        path.write_text(f"[isolation]\n{isolation}\n")
        return path

    def test_an_unknown_backend_is_refused_with_the_known_ones(self, tmp_path):
        with pytest.raises(config.ConfigError, match=r"'nnoe' is not one \(known: none, docker\)"):
            config.load(self.write(tmp_path, 'backend = "nnoe"'))

    def test_a_setting_the_backend_does_not_take_is_refused(self, tmp_path):
        with pytest.raises(config.ConfigError, match="'none' backend takes no cpus"):
            config.load(self.write(tmp_path, 'backend = "none"\ncpus = 2'))

    def test_the_backend_named_is_the_backend_run(self, tmp_path):
        loaded = config.load(self.write(tmp_path, 'backend = "none"'))
        assert confine.backend(loaded.isolation).name == confine.NONE

    def test_a_machine_that_says_nothing_runs_unconfined(self):
        assert confine.backend(config.Config().isolation).name == confine.NONE

    def test_docker_takes_the_variables_it_passes_as_a_list(self, tmp_path):
        with pytest.raises(config.ConfigError, match="is not a list of variable names"):
            config.load(self.write(tmp_path, 'backend = "docker"\nenv = "ILAAS_API_KEY"'))


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
        assert (run.isolation, run.image) == ("spy", "sha256:spy")

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

    def test_a_confined_matrix_names_its_image(self):
        [line] = isolation_lines([Run("a", "c", 0, isolation="docker", image="sha256:ab")])
        assert line == "- isolation `docker`, image `sha256:ab`"

    def test_an_archive_that_predates_the_record_says_nothing(self):
        assert isolation_lines([Run("a", "c", 0)]) == []


class TestTheDockerCommand:
    """What `docker run` is asked, which needs no daemon to check."""

    def argv(self, tmp_path, **kwargs) -> list[str]:
        backend = confine.Docker(**kwargs)
        backend.image = "sha256:abc"
        scope = confine.Scope(writable=(tmp_path / "repo",), readable=(tmp_path / "brick.ts",))
        return backend.argv(
            ["pi", "-p", "go"], scope, tmp_path / "repo", "trysquare-x", tmp_path / "h"
        )

    def test_each_path_is_where_the_host_has_it(self, tmp_path):
        args = self.argv(tmp_path)
        assert f"{tmp_path}/repo:{tmp_path}/repo:rw" in args
        assert f"{tmp_path}/brick.ts:{tmp_path}/brick.ts:ro" in args
        assert f"{tmp_path}/h:{confine.HOME}:rw" in args

    def test_it_runs_from_the_image_id_as_the_operator(self, tmp_path):
        args = self.argv(tmp_path)
        assert args[-4:] == ["sha256:abc", "pi", "-p", "go"]
        assert args[args.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
        assert args[args.index("--workdir") + 1] == str(tmp_path / "repo")

    def test_a_key_is_passed_by_name_and_never_by_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "secret")
        args = self.argv(tmp_path, env=["SOME_KEY"])
        assert "SOME_KEY" in args
        assert not any("secret" in a for a in args)

    def test_no_image_declared_is_refused(self):
        with pytest.raises(RuntimeError, match=r"\[agent\] declares no image"):
            confine.Docker().prepare(None)

    def test_a_daemon_that_does_not_answer_is_said_as_such(self, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        with pytest.raises(RuntimeError, match="docker does not answer here"):
            confine.Docker().prepare("alpine:3")

    def test_a_variable_unset_here_is_refused(self, monkeypatch):
        monkeypatch.delenv("SOME_KEY", raising=False)
        with pytest.raises(RuntimeError, match="names SOME_KEY, unset here"):
            confine.Docker(env=["SOME_KEY"]).prepare("alpine:3")


IMAGE = "alpine:3"


def docker_runs() -> bool:
    if not shutil.which("docker"):
        return False
    found = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return found.returncode == 0


@pytest.mark.skipif(not docker_runs(), reason=f"no docker daemon, or no {IMAGE} image")
class TestInsideDocker:
    @pytest.fixture
    def backend(self) -> confine.Docker:
        backend = confine.Docker()
        backend.prepare(IMAGE)
        return backend

    @pytest.fixture
    def work(self, tmp_path) -> Path:
        """Two runs side by side, the way they sit under the workdir."""
        for run in ("mine", "neighbour"):
            (tmp_path / run / "repo").mkdir(parents=True)
            (tmp_path / run / "repo" / "secret.txt").write_text(run)
        return tmp_path

    def inside(self, backend, work, *command) -> str:
        scope = confine.Scope(writable=(work / "mine" / "repo",))
        done = backend.run(
            command, scope, cwd=work / "mine" / "repo", capture_output=True, text=True
        )
        return done.stdout

    def test_a_run_sees_no_neighbour(self, backend, work):
        assert self.inside(backend, work, "ls", str(work)).split() == ["mine"]
        assert "No such file" in self.inside(
            backend, work, "sh", "-c", f"cat {work}/neighbour/repo/secret.txt 2>&1"
        )

    def test_the_agent_can_write_its_own_home(self, backend, work):
        """`pi` keeps its credential store there, and failed on every run when it could not."""
        said = self.inside(backend, work, "sh", "-c", "touch ~/.pi/agent/auth.json && echo ok")
        assert said == "ok\n"

    def test_a_run_sees_its_own_clone_where_the_host_has_it(self, backend, work):
        assert self.inside(backend, work, "cat", "secret.txt") == "mine"

    def test_what_the_agent_writes_stays_the_operator_s(self, backend, work):
        self.inside(backend, work, "touch", "written")
        assert (work / "mine" / "repo" / "written").stat().st_uid == os.getuid()

    def test_a_container_outlives_no_timeout(self, backend, work):
        """Killing the client outright leaves the container, and its agent, running."""
        with pytest.raises(subprocess.TimeoutExpired):
            backend.run(["sleep", "60"], confine.Scope(), timeout=2)
        left = subprocess.run(
            ["docker", "ps", "--all", "--quiet", "--filter", "name=trysquare-"],
            capture_output=True,
            text=True,
        )
        assert left.stdout.strip() == ""

    def test_an_image_without_the_agent_is_refused_before_any_run(self, backend):
        assert agent.unrunnable(backend, IMAGE) == f"'pi' does not run in image '{IMAGE}'"
