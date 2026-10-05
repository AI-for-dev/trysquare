"""What one run may see, and the record that says what kept it there.

Nothing here spends a token. The backend is a spy that answers in place of the agent, or
docker running something that is not an agent.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from trysquare import agent, config, confine, outputs, runner, validation
from trysquare.cli import isolation_lines
from trysquare.measure import Run
from trysquare.scenario import parse
from trysquare.secret import Secrets

from tests import gitrepo
from tests.test_relay import Upstream
from tests.test_scenario import MINIMAL


class Spy:
    """A backend that records what it was asked to run, and runs nothing."""

    name = "spy"
    image = "sha256:spy"
    where = "in a spy"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], confine.Scope, dict]] = []

    def prepare(self, image, providers) -> None:
        pass

    def run(self, argv, scope, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), scope, kwargs))
        return subprocess.CompletedProcess(argv, 0, "1.0.2\n", "")


class Talkative(Spy):
    """A spy whose agent answers, so the run goes on to be scored."""

    ANSWER = (
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": "done",
                    "usage": {"input": 1, "output": 1},
                },
            }
        )
        + "\n"
    ).encode()

    def run(self, argv, scope, **kwargs) -> subprocess.CompletedProcess:
        if "stdout" in kwargs:
            kwargs["stdout"].write(self.ANSWER)
        return super().run(argv, scope, **kwargs)


def measured_once(tmp_path, monkeypatch, scenario, spy) -> tuple[Run, Path]:
    """One run of the first cell through `spy`, and the run's work directory."""
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
    monkeypatch.setitem(confine.BACKENDS, Spy.name, lambda: spy)
    run = runner.one_run(plan, "abcd1234", {"cell": scenario.cells[0].name, "repetition": 0})
    return run, tmp_path / "work" / plan.output.directory.name / "abcd1234"


class TestTheMachineChooses:
    def write(self, tmp_path: Path, isolation: str) -> Path:
        path = tmp_path / config.CONFIG_NAME
        path.write_text(f"[isolation]\n{isolation}\n")
        return path

    def test_an_unknown_backend_is_refused_with_the_known_ones(self, tmp_path):
        with pytest.raises(
            config.ConfigError, match=r"'nnoe' is not one \(known: none, docker, bwrap\)"
        ):
            config.load(self.write(tmp_path, 'backend = "nnoe"'))

    def test_a_setting_the_backend_does_not_take_is_refused(self, tmp_path):
        with pytest.raises(config.ConfigError, match="'none' backend takes no cpus"):
            config.load(self.write(tmp_path, 'backend = "none"\ncpus = 2'))

    def test_the_backend_named_is_the_backend_run(self, tmp_path):
        loaded = config.load(self.write(tmp_path, 'backend = "none"'))
        assert confine.backend(loaded.isolation).name == confine.NONE

    def test_a_machine_that_says_nothing_runs_unconfined(self):
        assert confine.backend(config.Config().isolation).name == confine.NONE

    @pytest.mark.parametrize(
        "setting,refusal",
        [
            ("cpus = 0", "cpus = 0 is not a positive number"),
            ("cpus = true", "cpus = True is not a positive number"),
            ('memory = "4 GB"', "memory = '4 GB' is not a size"),
            ("memory = 4", "memory = 4 is not a size"),
        ],
    )
    def test_a_limit_docker_cannot_read_is_refused(self, tmp_path, setting, refusal):
        with pytest.raises(config.ConfigError, match=re.escape(refusal)):
            config.load(self.write(tmp_path, f'backend = "docker"\n{setting}'))

    def test_docker_takes_the_variables_it_passes_as_a_list(self, tmp_path):
        with pytest.raises(config.ConfigError, match="is not a list of variable names"):
            config.load(self.write(tmp_path, 'backend = "docker"\nenv = "ILAAS_API_KEY"'))

    def test_a_secrets_file_is_relative_to_the_config(self, tmp_path):
        loaded = config.load(self.write(tmp_path, 'backend = "docker"\nsecrets = "keys.env"'))
        assert loaded.isolation["secrets"] == str(tmp_path / "keys.env")

    def test_a_secrets_file_that_is_not_a_path_is_refused(self, tmp_path):
        with pytest.raises(config.ConfigError, match="secrets = 3 is not a path"):
            config.load(self.write(tmp_path, 'backend = "docker"\nsecrets = 3'))

    def test_a_models_file_is_relative_to_the_config(self, tmp_path):
        loaded = config.load(self.write(tmp_path, 'backend = "bwrap"\nmodels = "models.json"'))
        assert loaded.isolation["models"] == str(tmp_path / "models.json")

    def test_a_models_file_that_is_not_a_path_is_refused(self, tmp_path):
        with pytest.raises(config.ConfigError, match="models = 3 is not a path"):
            config.load(self.write(tmp_path, 'backend = "docker"\nmodels = 3'))

    def test_a_backend_with_no_relay_takes_no_secrets(self, tmp_path):
        with pytest.raises(config.ConfigError, match="'none' backend takes no secrets"):
            config.load(self.write(tmp_path, 'backend = "none"\nsecrets = "keys.env"'))


class TestOneRun:
    @pytest.fixture
    def measured(self, tmp_path, monkeypatch):
        """One run of a cell, through the spy, and what it recorded."""
        spy = Spy()
        run, work = measured_once(tmp_path, monkeypatch, parse(MINIMAL), spy)
        return run, spy, work

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


class TestTheJudge:
    """The judge is an agent too, with tools, and it ran where the operator stands."""

    JUDGED = MINIMAL | {
        "validation": [
            {
                "mode": "judge",
                "provider": "judging",
                "model": "m",
                "metrics": ["overflow", "delivered"],
            }
        ]
    }

    @pytest.fixture
    def calls(self, tmp_path, monkeypatch):
        """What the judge of one scored run was asked to run."""
        spy = Talkative()
        measured_once(tmp_path, monkeypatch, parse(self.JUDGED), spy)
        return [c for c in spy.calls if c[2].get("cwd") and c[2]["cwd"].name == "judge"]

    def test_the_judge_runs_through_the_backend(self, calls):
        assert calls

    def test_the_judge_may_write_its_dossier_and_read_its_brick_and_nothing_else(self, calls):
        """Its pieces are in its prompt, so it has no business with the clone."""
        _, scope, kwargs = calls[0]
        assert scope.writable == (kwargs["cwd"],)
        assert scope.readable == (validation.JUDGE_BRICK,)

    def test_a_launch_serves_the_judge_s_provider_as_well_as_the_agent_s(self):
        assert parse(self.JUDGED).providers == ("ilaas", "judging")


class TestTheSynthesisSays:
    def test_a_matrix_measured_without_a_boundary_is_warned_about(self):
        [line] = isolation_lines([Run("a", "c", 0, isolation="none")], {})
        assert line.startswith("- isolation `none`: runs were not isolated")

    def test_a_confined_matrix_names_its_image(self):
        [line] = isolation_lines([Run("a", "c", 0, isolation="docker", image="sha256:ab")], {})
        assert line == "- isolation `docker`, image `sha256:ab`"

    def test_the_limits_each_run_had_are_said(self):
        runs = [Run("a", "c", 0, isolation="docker", image="sha256:ab")]
        assert isolation_lines(runs, {"cpus": 2, "memory": "4g"})[1] == (
            "- limits per run: cpus 2, memory 4g"
        )

    def test_an_archive_that_predates_the_record_says_nothing(self):
        assert isolation_lines([Run("a", "c", 0)], {}) == []


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

    def test_the_limits_hold_and_no_swap_gets_past_them(self, tmp_path):
        args = self.argv(tmp_path, cpus=1.5, memory="4g")
        assert args[args.index("--cpus") + 1] == "1.5"
        assert args[args.index("--memory") + 1] == "4g"
        assert args[args.index("--memory-swap") + 1] == "4g"

    def test_no_limit_set_is_no_limit_passed(self, tmp_path):
        args = self.argv(tmp_path)
        assert "--cpus" not in args and "--memory" not in args

    def test_a_key_is_passed_by_name_and_never_by_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "secret")
        args = self.argv(tmp_path, env=["SOME_KEY"])
        assert "SOME_KEY" in args
        assert not any("secret" in a for a in args)

    def test_no_image_declared_is_refused(self):
        with pytest.raises(RuntimeError, match=r"\[agent\] declares no image"):
            confine.Docker().prepare(None, ())

    def test_a_daemon_that_does_not_answer_is_said_as_such(self, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        with pytest.raises(RuntimeError, match="docker does not answer here"):
            confine.Docker().prepare("alpine:3", ())

    def test_a_variable_unset_here_is_refused(self, monkeypatch):
        monkeypatch.delenv("SOME_KEY", raising=False)
        with pytest.raises(RuntimeError, match="names SOME_KEY, unset here"):
            confine.Docker(env=["SOME_KEY"]).prepare("alpine:3", ())


class TestWhatTheHomeStartsWith:
    """Only what the scenario's provider needs, and never a secret: the key stays outside."""

    REACH = confine.Reach(bind="127.0.0.1", host="relay.test")

    @pytest.fixture(autouse=True)
    def key(self, monkeypatch):
        monkeypatch.setenv("ILAAS_API_KEY", "sk-the-real-key")
        monkeypatch.setenv("T", "tok-the-real-token")

    @pytest.fixture
    def relays(self):
        """Every relay a seed started, closed with the test."""
        started = []
        yield started
        for relay in started:
            relay.close()

    def seed(self, relays, providers, env, agent_dir, secrets=None):
        models = agent_dir / "models.json"
        secrets = secrets or Secrets()
        files, started = confine.seed(providers, env, secrets, models, agent_dir, self.REACH)
        relays.extend(started)
        return files, started

    def agent_dir(self, tmp_path, models: dict | None = None, settings: dict | None = None):
        for name, content in (("models.json", models), ("settings.json", settings)):
            if content is not None:
                (tmp_path / name).write_text(json.dumps(content))
        return tmp_path

    def provider(self, **fields) -> dict:
        return {"baseUrl": "https://llm.example/v1", "api": "openai-completions", **fields}

    def test_only_the_scenario_s_provider_is_written(self, tmp_path, relays):
        """Another provider's key, even a literal one, is not the agent's business."""
        models = {
            "providers": {
                "ilaas": self.provider(apiKey="$ILAAS_API_KEY"),
                "other": self.provider(apiKey="sk-literal"),
            }
        }
        files, _ = self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models))
        assert list(files["models.json"]["providers"]) == ["ilaas"]

    def test_the_agent_gets_a_placeholder_and_the_relay_the_key(self, tmp_path, relays):
        models = {"providers": {"ilaas": self.provider(apiKey="$ILAAS_API_KEY")}}
        files, [relay] = self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models))
        written = files["models.json"]["providers"]["ilaas"]
        assert "sk-the-real-key" not in json.dumps(files)
        assert relay.reveal(written["apiKey"]) == ("sk-the-real-key", True)

    def test_the_agent_calls_the_relay_which_calls_the_provider(self, tmp_path, relays):
        models = {"providers": {"ilaas": self.provider(apiKey="$ILAAS_API_KEY")}}
        files, [relay] = self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models))
        written = files["models.json"]["providers"]["ilaas"]
        assert written["baseUrl"] == f"http://relay.test:{relay.port}/v1"
        assert relay.origin == "https://llm.example"

    def test_a_header_built_around_a_variable_keeps_its_scheme(self, tmp_path, relays):
        models = {"providers": {"ilaas": self.provider(headers={"Authorization": "Bearer ${T}"})}}
        files, [relay] = self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models))
        header = files["models.json"]["providers"]["ilaas"]["headers"]["Authorization"]
        assert relay.reveal(header) == ("Bearer tok-the-real-token", True)

    def test_a_provider_with_no_secret_needs_no_relay(self, tmp_path, relays):
        models = {"providers": {"local": self.provider()}}
        files, started = self.seed(relays, ["local"], (), self.agent_dir(tmp_path, models))
        assert started == []
        assert files["models.json"]["providers"]["local"] == self.provider()

    def test_a_judge_on_another_provider_is_written_too(self, tmp_path, relays, monkeypatch):
        """Otherwise the judge finds no provider in its container and scores nothing."""
        monkeypatch.setenv("A", "sk-agent")
        monkeypatch.setenv("B", "sk-judge")
        models = {
            "providers": {
                "ilaas": self.provider(apiKey="$A"),
                "judging": self.provider(baseUrl="https://judge.example/v1", apiKey="$B"),
                "other": self.provider(apiKey="sk-literal"),
            }
        }
        files, started = self.seed(
            relays, ["ilaas", "judging"], (), self.agent_dir(tmp_path, models)
        )
        assert sorted(files["models.json"]["providers"]) == ["ilaas", "judging"]
        assert len(started) == 2, "one relay per provider: each sends its key to one host"

    @pytest.mark.parametrize(
        "fields",
        [
            {"apiKey": "sk-literal"},
            {"apiKey": "sk-$$literal"},
            {"headers": {"Authorization": "Bearer sk-literal"}},
            {"models": [{"id": "m", "headers": {"X-Key": "sk-literal"}}]},
        ],
    )
    def test_a_secret_written_in_the_file_is_refused(self, tmp_path, relays, fields):
        models = {"providers": {"ilaas": self.provider(**fields)}}
        with pytest.raises(
            RuntimeError, match="models.json is written in the file, where the agent"
        ):
            self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models))

    def test_a_command_is_refused(self, tmp_path, relays):
        """It would run inside the container, where it is not what the operator wrote."""
        models = {"providers": {"ilaas": self.provider(apiKey="!pass show ilaas")}}
        with pytest.raises(RuntimeError, match="runs a command"):
            self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models))

    def test_a_key_env_still_passes_in_is_refused(self, tmp_path, relays):
        """Passed in, it would sit in the agent's environment beside the placeholder."""
        models = {"providers": {"ilaas": self.provider(apiKey="${ILAAS_API_KEY}")}}
        with pytest.raises(RuntimeError, match=r"drop ILAAS_API_KEY from \[isolation\] env"):
            self.seed(relays, ["ilaas"], ("ILAAS_API_KEY",), self.agent_dir(tmp_path, models))

    def test_a_key_unset_here_is_refused(self, tmp_path, relays, monkeypatch):
        """Otherwise every run comes back empty, with a provider error to decode."""
        monkeypatch.delenv("ILAAS_API_KEY")
        models = {"providers": {"ilaas": self.provider(apiKey="$ILAAS_API_KEY")}}
        with pytest.raises(RuntimeError, match=r"reads \$ILAAS_API_KEY, unset here and not"):
            self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models))

    def test_a_key_unset_here_is_read_from_the_file(self, tmp_path, relays, monkeypatch):
        monkeypatch.delenv("ILAAS_API_KEY")
        monkeypatch.delenv("T")
        keys = tmp_path / "keys.env"
        keys.write_text('# keys\n\nexport ILAAS_API_KEY="sk-from-file"\nT=tok # note\n')
        entry = self.provider(apiKey="$ILAAS_API_KEY", headers={"X-Token": "$T"})
        models = {"providers": {"ilaas": entry}}
        secrets = Secrets(str(keys))
        files, [relay] = self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models), secrets)
        written = files["models.json"]["providers"]["ilaas"]
        assert "sk-from-file" not in json.dumps(files)
        assert relay.reveal(written["apiKey"]) == ("sk-from-file", True)
        assert relay.reveal(written["headers"]["X-Token"]) == ("tok", True)

    def test_a_key_set_here_wins_over_the_file(self, tmp_path, relays):
        """So CI, and a shell that already exports the key, behave as they did."""
        keys = tmp_path / "keys.env"
        keys.write_text("ILAAS_API_KEY=sk-from-file\n")
        models = {"providers": {"ilaas": self.provider(apiKey="$ILAAS_API_KEY")}}
        secrets = Secrets(str(keys))
        files, [relay] = self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models), secrets)
        written = files["models.json"]["providers"]["ilaas"]["apiKey"]
        assert relay.reveal(written) == ("sk-the-real-key", True)

    @pytest.mark.parametrize(
        "content,refusal",
        [
            (None, "No such file"),
            ("OK=1\nsk-leaked\n", "line 2: not NAME=value"),
            ("ILAAS_API_KEY=\n", "sets ILAAS_API_KEY to nothing"),
        ],
    )
    def test_a_file_that_cannot_be_read_stops_the_launch_without_its_content(
        self, tmp_path, relays, monkeypatch, content, refusal
    ):
        monkeypatch.delenv("ILAAS_API_KEY")
        keys = tmp_path / "keys.env"
        if content is not None:
            keys.write_text(content)
        models = {"providers": {"ilaas": self.provider(apiKey="$ILAAS_API_KEY")}}
        with pytest.raises(RuntimeError, match=refusal) as e:
            self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, models), Secrets(str(keys)))
        assert "sk-leaked" not in str(e.value)

    def test_a_provider_on_two_hosts_is_refused(self, tmp_path, relays):
        """One relay serves one provider host, and the key must reach no other."""
        entry = self.provider(
            apiKey="$ILAAS_API_KEY", models=[{"id": "m", "baseUrl": "https://elsewhere/v1"}]
        )
        with pytest.raises(RuntimeError, match="more than one host"):
            self.seed(
                relays, ["ilaas"], (), self.agent_dir(tmp_path, {"providers": {"ilaas": entry}})
            )

    def test_a_provider_without_an_address_is_refused(self, tmp_path, relays):
        """An entry that only sets a built-in provider's key gives the relay nowhere to go."""
        models = {"providers": {"anthropic": {"apiKey": "$ILAAS_API_KEY"}}}
        with pytest.raises(RuntimeError, match="names no baseUrl"):
            self.seed(relays, ["anthropic"], (), self.agent_dir(tmp_path, models))

    def test_a_built_in_provider_needs_no_file(self, tmp_path, relays):
        models = {"providers": {"ilaas": self.provider(apiKey="sk-literal")}}
        assert self.seed(relays, ["anthropic"], (), self.agent_dir(tmp_path, models)) == ({}, [])

    def test_a_models_file_named_replaces_the_operator_s(self, tmp_path, relays, monkeypatch):
        """The operator's own may hold a literal key or a `!command` for interactive use."""
        home = tmp_path / "home" / ".pi" / "agent"
        home.mkdir(parents=True)
        self.agent_dir(home, {"providers": {"ilaas": self.provider(apiKey="sk-literal")}})
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        named = tmp_path / "bench-models.json"
        named.write_text(json.dumps({"providers": {"ilaas": self.provider(apiKey="$T")}}))
        files, started = confine._settled((), Secrets(), named, ["ilaas"], self.REACH, [])
        relays.extend(started)
        assert files["models.json"]["providers"]["ilaas"]["baseUrl"].startswith("http://relay")

    def test_a_models_file_named_and_missing_is_refused(self, tmp_path):
        backend = confine.Bwrap(models=str(tmp_path / "nowhere.json"))
        with pytest.raises(RuntimeError, match="nowhere.json', no such file here"):
            backend.prepare(None, ["ilaas"])

    def test_of_the_settings_only_the_subagent_thinking_level(self, tmp_path, relays):
        """The rest would be inherited from the operator's machine, which no scenario says."""
        settings = {"defaultThinkingLevel": "high", "compaction": {"enabled": False}}
        files, _ = self.seed(relays, ["ilaas"], (), self.agent_dir(tmp_path, settings=settings))
        assert files == {"settings.json": {"defaultThinkingLevel": "high"}}


IMAGE = "alpine:3"

#: `image/Dockerfile`, under the tag its documentation builds it as.
AGENT_IMAGE = "trysquare-agent"


def docker_runs(image: str = IMAGE) -> bool:
    if not shutil.which("docker"):
        return False
    found = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    return found.returncode == 0


@pytest.mark.skipif(not docker_runs(), reason=f"no docker daemon, or no {IMAGE} image")
class TestInsideDocker:
    @pytest.fixture
    def backend(self) -> confine.Docker:
        backend = confine.Docker()
        backend.prepare(IMAGE, ())
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


class TestTheBwrapCommand:
    """What `bwrap` is asked, which needs no sandbox to check."""

    def argv(self, tmp_path, **kwargs) -> list[str]:
        scope = confine.Scope(writable=(tmp_path / "repo",), readable=(tmp_path / "brick.ts",))
        backend = confine.Bwrap(**kwargs)
        return backend.argv(["pi", "-p", "go"], scope, tmp_path / "repo", tmp_path / "h")

    def pairs(self, args: list[str], flag: str) -> list[str]:
        return [args[i + 1] for i, a in enumerate(args) if a == flag]

    def test_the_run_s_scope_is_where_the_host_has_it(self, tmp_path):
        args = self.argv(tmp_path)
        assert str(tmp_path / "repo") in self.pairs(args, "--bind")
        assert str(tmp_path / "brick.ts") in self.pairs(args, "--ro-bind")
        assert args[args.index("--chdir") + 1] == str(tmp_path / "repo")

    def test_the_system_is_read_only_and_nothing_else_of_the_machine_is_there(self, tmp_path):
        args = self.argv(tmp_path)
        assert set(self.pairs(args, "--ro-bind-try")) == set(confine.SYSTEM)
        assert not {"/home", "/tmp", "/var", "/"} & set(self.pairs(args, "--ro-bind"))

    def test_a_tool_installed_elsewhere_is_bound_read_only(self, tmp_path):
        args = self.argv(tmp_path, bind=["~/.nvm"])
        assert str(Path.home() / ".nvm") in self.pairs(args, "--ro-bind")

    def test_the_sandbox_dies_with_trysquare(self, tmp_path):
        assert "--die-with-parent" in self.argv(tmp_path)

    def test_a_key_reaches_the_sandbox_through_its_environment_and_never_its_argv(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("SOME_KEY", "secret")
        backend = confine.Bwrap(env=["SOME_KEY"])
        args = backend.argv(["pi"], confine.Scope(), None, tmp_path)
        assert not any("secret" in a for a in args)
        assert backend.environment()["SOME_KEY"] == "secret"
        assert set(backend.environment()) == {"PATH", "HOME", "SOME_KEY"}


def bwrap_runs() -> bool:
    return (
        shutil.which("bwrap") is not None
        and confine._run("bwrap", "--ro-bind", "/", "/", "true").returncode == 0
    )


@pytest.mark.skipif(not bwrap_runs(), reason="no working bwrap here")
class TestInsideBwrap:
    @pytest.fixture
    def backend(self) -> confine.Bwrap:
        backend = confine.Bwrap()
        backend.prepare(None, ())
        return backend

    @pytest.fixture
    def work(self, tmp_path) -> Path:
        for run in ("mine", "neighbour"):
            (tmp_path / run / "repo").mkdir(parents=True)
            (tmp_path / run / "repo" / "secret.txt").write_text(run)
        return tmp_path

    def inside(self, backend, work, *command, **kwargs) -> str:
        scope = confine.Scope(writable=(work / "mine" / "repo",))
        done = backend.run(
            command, scope, cwd=work / "mine" / "repo", capture_output=True, text=True, **kwargs
        )
        return done.stdout

    def test_a_run_sees_no_neighbour(self, backend, work):
        assert self.inside(backend, work, "ls", str(work)).split() == ["mine"]
        said = self.inside(backend, work, "sh", "-c", f"cat {work}/neighbour/repo/secret.txt 2>&1")
        assert "No such file" in said

    def test_nothing_of_the_operator_s_home_is_there(self, backend, work):
        assert self.inside(backend, work, "ls", "/home").split() == ["trysquare"]

    def test_what_the_agent_writes_stays_the_operator_s(self, backend, work):
        self.inside(backend, work, "touch", "written")
        assert (work / "mine" / "repo" / "written").stat().st_uid == os.getuid()

    def test_the_agent_can_write_its_own_home(self, backend, work):
        said = self.inside(backend, work, "sh", "-c", "touch ~/.pi/agent/auth.json && echo ok")
        assert said == "ok\n"

    def test_a_timed_out_run_leaves_no_process(self, backend, work):
        """bwrap puts the agent in a session of its own, out of reach of the group kill:
        `--die-with-parent` is what takes it down with bwrap."""
        with pytest.raises(subprocess.TimeoutExpired):
            backend.run(["sleep", "4242"], confine.Scope(), timeout=2)
        deadline = time.monotonic() + 5
        while running("sleep 4242") and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not running("sleep 4242")


def running(command: str) -> bool:
    """Whether a process runs exactly `command`. Read from /proc rather than with
    `pgrep -f`, which also matches any shell whose command line merely mentions it."""
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if (Path("/proc") / pid / "cmdline").read_bytes() == command.replace(
                " ", "\0"
            ).encode() + b"\0":
                return True
        except OSError:
            continue
    return False


@pytest.mark.skipif(not docker_runs(AGENT_IMAGE), reason=f"no {AGENT_IMAGE} image built here")
class TestInsideTheShippedImage:
    """`image/Dockerfile` gives the agent a `pi` that runs. Nothing here calls a provider."""

    @pytest.fixture
    def backend(self) -> confine.Docker:
        backend = confine.Docker()
        backend.prepare(AGENT_IMAGE, ())
        return backend

    def test_the_agent_answers_in_it(self, backend):
        assert agent.unrunnable(backend, AGENT_IMAGE) is None

    def test_a_session_renders_in_it(self, backend, tmp_path):
        session = tmp_path / "session" / "trace.jsonl"
        session.parent.mkdir()
        session.write_bytes(
            (Path(__file__).parent / "fixtures" / "session-minimal.jsonl").read_bytes()
        )
        page = agent.export_html(session, session.parent, backend)
        assert page.read_text().startswith("<!DOCTYPE html>")
        assert page.stat().st_uid == os.getuid()


class KeyStaysOut:
    """What a sandboxed agent can find of its provider's key: nothing, while its requests
    still reach the provider with it. Shared by the backends that confine."""

    SECRET = "sk-probe-the-real-key"

    def made(self) -> confine.Confinement:
        raise NotImplementedError

    def fetch(self, url: str, authorization: str) -> list[str]:
        raise NotImplementedError

    @pytest.fixture
    def provider(self, tmp_path, monkeypatch):
        """A provider described in the operator's models.json, served by a local stand-in."""
        upstream = Upstream()
        agent_dir = tmp_path / "operator" / ".pi" / "agent"
        agent_dir.mkdir(parents=True)
        entry = {
            "baseUrl": f"{upstream.origin}/v1",
            "api": "openai-completions",
            "apiKey": "$PROBE_KEY",
        }
        (agent_dir / "models.json").write_text(json.dumps({"providers": {"probe": entry}}))
        monkeypatch.setenv("HOME", str(tmp_path / "operator"))
        monkeypatch.setenv("PROBE_KEY", self.SECRET)
        yield upstream
        upstream.close()

    @pytest.fixture
    def backend(self, provider):
        backend = self.made()
        backend.prepare(IMAGE, ["probe"])
        yield backend
        for relay in backend._relays:
            relay.close()

    def inside(self, backend, *command) -> str:
        return backend.run(command, confine.Scope(), capture_output=True, text=True).stdout

    def test_the_key_is_neither_in_the_environment_nor_in_the_home(self, backend):
        seen = self.inside(backend, "sh", "-c", "env; cat ~/.pi/agent/models.json")
        assert '"probe"' in seen
        assert self.SECRET not in seen

    def test_a_request_with_the_placeholder_reaches_the_provider_with_the_key(
        self, backend, provider
    ):
        written = backend._seed["models.json"]["providers"]["probe"]
        said = self.inside(
            backend, *self.fetch(f"{written['baseUrl']}/chat", f"Bearer {written['apiKey']}")
        )
        assert said == "ok"
        assert provider.seen[0]["headers"]["Authorization"] == f"Bearer {self.SECRET}"


@pytest.mark.skipif(not docker_runs(), reason=f"no docker daemon, or no {IMAGE} image")
class TestInsideDockerTheKeyStaysOut(KeyStaysOut):
    def made(self):
        return confine.Docker()

    def fetch(self, url, authorization):
        return ["wget", "-q", "-O-", "--header", f"Authorization: {authorization}", url]


@pytest.mark.skipif(not bwrap_runs(), reason="no working bwrap here")
class TestInsideBwrapTheKeyStaysOut(KeyStaysOut):
    def made(self):
        return confine.Bwrap()

    def fetch(self, url, authorization):
        return ["curl", "-s", "--noproxy", "*", "-H", f"Authorization: {authorization}", url]
