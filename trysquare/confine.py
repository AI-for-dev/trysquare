# SPDX-License-Identifier: BSD-3-Clause
"""Where an agent runs, and what it can see from there.

Every run lives under one work directory and the agent runs as the operator, so
without a boundary an agent can read its neighbours' clones, sessions and traces, the
pinned sources and the output directory. The boundary has to come from the operating
system: an agent told not to look is an agent that was asked.

A backend is **one execution**, not a command prefix: it receives the argv and the
scope of one run, and returns what the child did. That is what lets a backend which is
not a command at all - a microVM - stand behind the same interface.

The scope is given as host paths, and a backend that confines exposes each one at the
same path inside. Nothing in the argv then needs rewriting, and nothing outside the
scope exists for the agent to find.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from typing import Protocol

from . import interrupt
from .relay import Relay
from .secret import Secrets

NONE = "none"
DOCKER = "docker"
BWRAP = "bwrap"


@dataclass(frozen=True)
class Scope:
    """What one run may touch: its own clone and session, and the bricks it loads."""

    writable: tuple[Path, ...] = ()
    readable: tuple[Path, ...] = ()


class Confinement(Protocol):
    name: str
    #: What the runs execute on, recorded beside each of them: empty for the operator's
    #: own machine, the image id for a container. Set by `prepare`.
    image: str
    #: Where that is, for a refusal to name.
    where: str
    #: What each run may use of the machine, as the config set it. Recorded with the
    #: load, because a run held to two CPUs is slower than one that was not.
    limits: dict
    #: Whether the agent runs from the scenario's `[agent] image` rather than from this
    #: machine's tools.
    takes_image: bool
    #: The `[isolation]` settings that rebuild this backend to render a session: where the
    #: agent is found, and no variable, provider or limit, since a render calls nothing.
    settings: dict

    def prepare(self, image: str | None, providers: Sequence[str]) -> None:
        """Checks this machine can run the backend, or raises `RuntimeError` saying why.

        `image` is the scenario's `[agent] image`, and `providers` every provider a launch
        calls: the agent's, and each judge's.
        """
        ...

    def run(self, argv: Sequence[str], scope: Scope, **kwargs) -> subprocess.CompletedProcess:
        """`interrupt.run`, inside the boundary. `kwargs` are `interrupt.run`'s."""
        ...


class Unconfined:
    """No boundary: the agent sees whatever the operator sees.

    Only ever chosen, never fallen back to, and every run it measures says so.
    """

    name = NONE
    image = ""
    where = "on this machine"
    limits: dict = {}
    takes_image = False
    settings = {"backend": NONE}

    def prepare(self, image: str | None, providers: Sequence[str]) -> None:
        pass

    def run(self, argv: Sequence[str], scope: Scope, **kwargs) -> subprocess.CompletedProcess:
        return interrupt.run(argv, **kwargs)


#: Where the agent's home is inside a container. A fresh directory per run, so nothing
#: one run leaves in it reaches the next.
HOME = "/home/trysquare"

#: The one setting the agent's home carries over from the operator's: the level a
#: subagent thinks at, which nothing else can declare and `runner` checks against the
#: scenario. Any other setting would be inherited from the machine, which no scenario says.
SETTINGS = ("defaultThinkingLevel",)

#: A reference to a variable inside a value, `$NAME` or `${NAME}`, as `pi` interpolates
#: it: `Bearer ${TOKEN}` is a header whose secret comes from the environment. `$$` and
#: `$!` are escapes, matched so they are never read as references.
REFERENCE = re.compile(r"\$[$!]|\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)")

#: The scheme and host of a URL, which is what a relay serves.
ORIGIN = re.compile(r"^[a-z][a-z0-9+.-]*://[^/]+", re.IGNORECASE)


@dataclass(frozen=True)
class Reach:
    """Where a relay listens on this machine, and the host the sandbox calls it by."""

    bind: str
    host: str


#: A memory size as docker reads it.
MEMORY = re.compile(r"^\d+[bkmg]$")


def seed(
    providers: Sequence[str],
    env: Sequence[str],
    secrets: Secrets,
    models_file: Path,
    agent_dir: Path,
    reach: Reach,
) -> tuple[dict[str, dict], list[Relay]]:
    """What the agent's home starts with, by file name, and the relays that hold its keys.

    Read from `models_file` and the operator's `agent_dir`, and written rather than copied.
    `models_file` describes the providers the operator uses, and only the providers the
    launch calls go in. Their secrets must be references to variables `secrets` has a value for, and none
    of them reaches the sandbox: each is replaced by a placeholder, the provider's address
    by a relay's, and the relay puts the secret back on the way out (see `relay`). One relay per
    provider, since a relay sends its key to one host only. `auth.json` never goes in. A
    provider `models.json` does not describe is one of `pi`'s own, which reads its key
    from the environment - so from `env`, where the agent can read it too.
    """
    found: dict[str, dict] = {}
    kept: dict[str, dict] = {}
    relays: list[Relay] = []
    models = _read(models_file).get("providers", {})
    try:
        for provider in providers:
            if provider not in models:
                continue
            entry = models[provider]
            where = f"provider {provider!r} in {models_file}"
            names = sorted(
                {
                    n
                    for field, value in _secrets(entry)
                    for n in _check(value, f"{field} of {where}", env, secrets)
                }
            )
            if names:
                entry, relay = _relayed(entry, {n: secrets.value(n) for n in names}, where, reach)
                relays.append(relay)
            kept[provider] = entry
    except BaseException:
        # A provider refused after another was relayed must not leave that relay serving.
        for relay in relays:
            relay.close()
        raise
    if kept:
        found["models.json"] = {"providers": kept}
    settings = _read(agent_dir / "settings.json")
    if thinking := {k: settings[k] for k in SETTINGS if k in settings}:
        found["settings.json"] = thinking
    return found, relays


def _relayed(entry: dict, values: dict[str, str], where: str, reach: Reach) -> tuple[dict, Relay]:
    """The entry the agent gets - placeholders and the relay's address - and the relay
    holding `values`, the secrets by variable name."""
    origins = {ORIGIN.match(url)[0] for url in _addresses(entry) if ORIGIN.match(url)}
    if not origins:
        raise RuntimeError(
            f"{where} names no baseUrl, so there is nowhere to send its key from outside "
            f"the sandbox. Write the provider's address in it"
        )
    if len(origins) > 1:
        raise RuntimeError(
            f"{where} sends requests to more than one host ({', '.join(sorted(origins))}), "
            f"and its key must reach one only"
        )
    placeholders = {n: f"trysquare-{n}-{token_hex(16)}" for n in values}
    relay = Relay(origins.pop(), {placeholders[n]: v for n, v in values.items()}, reach.bind)
    return _rewritten(entry, placeholders, relay.origin, f"http://{reach.host}:{relay.port}"), relay


def _addresses(entry) -> Iterator[str]:
    """Every `baseUrl` in a provider entry, its models' included."""
    if isinstance(entry, list):
        for item in entry:
            yield from _addresses(item)
    elif isinstance(entry, dict):
        for key, value in entry.items():
            if key == "baseUrl" and isinstance(value, str):
                yield value
            else:
                yield from _addresses(value)


def _rewritten(entry, placeholders: dict[str, str], origin: str, relay: str):
    """`entry` with each secret's variables replaced by placeholders and `origin` by `relay`."""
    if isinstance(entry, list):
        return [_rewritten(item, placeholders, origin, relay) for item in entry]
    if not isinstance(entry, dict):
        return entry
    out = {}
    for key, value in entry.items():
        if key == "baseUrl" and isinstance(value, str):
            out[key] = relay + value[len(origin) :]
        elif key == "apiKey" and isinstance(value, str):
            out[key] = _placed(value, placeholders)
        elif key == "headers" and isinstance(value, dict):
            out[key] = {
                k: _placed(v, placeholders) if isinstance(v, str) else v for k, v in value.items()
            }
        else:
            out[key] = _rewritten(value, placeholders, origin, relay)
    return out


def _placed(value: str, placeholders: dict[str, str]) -> str:
    return REFERENCE.sub(lambda m: placeholders[m[1] or m[2]] if m[1] or m[2] else m[0], value)


def _read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def _secrets(entry) -> Iterator[tuple[str, str]]:
    """Every `apiKey` and header value in a provider entry, its models' included."""
    if isinstance(entry, list):
        for item in entry:
            yield from _secrets(item)
    elif isinstance(entry, dict):
        for key, value in entry.items():
            if key == "apiKey" and isinstance(value, str):
                yield key, value
            elif key == "headers" and isinstance(value, dict):
                yield from ((f"header {k}", v) for k, v in value.items() if isinstance(v, str))
            else:
                yield from _secrets(value)


def _check(value: str, where: str, env: Sequence[str], secrets: Secrets) -> list[str]:
    """The variables a secret reads, once it is one the relay can keep outside.

    A value with no variable in it is a secret written out. One that mixes a literal and
    a variable, like `Bearer ${TOKEN}`, is accepted: the literal part is the scheme.
    """
    if value.startswith("!"):
        raise RuntimeError(
            f"the {where} runs a command, which would run inside the container. Put the "
            f"secret in a variable set here or in the [isolation] secrets file, and write $NAME"
        )
    names = [a or b for _, a, b in (m.group(0, 1, 2) for m in REFERENCE.finditer(value)) if a or b]
    if not names:
        raise RuntimeError(
            f"the {where} is written in the file, where the agent could read it. Put it "
            f"in a variable, set here or in the [isolation] secrets file, and write $NAME. "
            f"If this models.json is yours, name one for the sandbox in [isolation] models"
        )
    for name in names:
        if name in env:
            raise RuntimeError(
                f"the {where} reads ${name}, and [isolation] env would pass it into the "
                f"sandbox, where the agent could read it: drop {name} from [isolation] "
                f"env, trysquare adds it to the agent's requests from outside"
            )
        if not secrets.known(name):
            raise RuntimeError(
                f"the {where} reads ${name}, unset here and not in the [isolation] secrets file"
            )
    return names


def _variables(env: Sequence[str]) -> tuple[str, ...]:
    """`[isolation] env`, refused unless it is a list of names."""
    if isinstance(env, str) or not all(isinstance(v, str) for v in env):
        raise ValueError(f"env = {env!r} is not a list of variable names")
    return tuple(env)


def _models(models: str | None) -> Path | None:
    """`[isolation] models`, refused unless it is a path."""
    if models is not None and not isinstance(models, str):
        raise ValueError(f"models = {models!r} is not a path")
    return Path(models) if models else None


def models_file(models: Path | None) -> Path:
    """The `models.json` a sandbox takes its providers from: `[isolation] models`, or the
    operator's own."""
    return models or Path.home() / ".pi" / "agent" / "models.json"


def described(models: Path | None) -> tuple[str, ...]:
    """Every provider the `models.json` a sandbox reads describes."""
    return tuple(_read(models_file(models)).get("providers", {}))


#: What an interactive agent needs of the terminal it draws in. None of it is a secret.
TERMINAL = ("TERM", "COLORTERM", "LANG")


def _settled(
    env: tuple[str, ...],
    secrets: Secrets,
    models: Path | None,
    providers: Sequence[str],
    reach: Reach,
    previous: list[Relay],
) -> tuple[dict[str, dict], list[Relay]]:
    """The agent's home for this launch and its relays, once every variable `env` names is
    set and the `models` file, if one is named, exists. Without one, the providers are
    the operator's own. A launch prepares more than once, and only the last relays serve
    its runs."""
    for relay in previous:
        relay.close()
    unset = [v for v in env if v not in os.environ]
    if unset:
        raise RuntimeError(f"[isolation] env names {', '.join(unset)}, unset here")
    if models is not None and not models.is_file():
        raise RuntimeError(f"[isolation] models = {str(models)!r}, no such file here")
    agent_dir = Path.home() / ".pi" / "agent"
    return seed(providers, env, secrets, models_file(models), agent_dir, reach)


class Docker:
    """One container per run, from the image the scenario declares.

    The container sees the run's scope and nothing else, each path where the host has it.
    It runs as the operator's uid so what the agent writes in the clone stays the
    operator's, and its environment is the variables `[isolation] env` names. It calls
    its provider through the relay on the host, as `host.docker.internal`.
    """

    name = DOCKER
    takes_image = True
    # The agent is the image's, and `image` names it.
    settings = {"backend": DOCKER}

    def __init__(
        self,
        env: Sequence[str] = (),
        secrets: str | None = None,
        models: str | None = None,
        cpus: float | None = None,
        memory: str | None = None,
    ) -> None:
        if cpus is not None and (
            isinstance(cpus, bool) or not isinstance(cpus, int | float) or cpus <= 0
        ):
            raise ValueError(f"cpus = {cpus!r} is not a positive number")
        if memory is not None and not (isinstance(memory, str) and MEMORY.match(memory)):
            raise ValueError(f'memory = {memory!r} is not a size such as "4g" or "512m"')
        self.env = _variables(env)
        self.secrets = Secrets(secrets)
        self.models = _models(models)
        self.limits = {k: v for k, v in (("cpus", cpus), ("memory", memory)) if v is not None}
        self.image = ""
        self.where = "in docker"
        self._seed: dict[str, dict] = {}
        self._relays: list[Relay] = []
        self._live: set[str] = set()
        self._lock = threading.Lock()

    def prepare(self, image: str | None, providers: Sequence[str]) -> None:
        if not image:
            raise RuntimeError(
                "the docker backend runs the image the scenario declares, and [agent] "
                "declares no image"
            )
        running = _docker("version")
        if running.returncode != 0:
            raise RuntimeError(f"docker does not answer here: {running.stderr.strip()[:200]}")
        reach = Reach(bind=_bridge(), host="host.docker.internal")
        self._seed, self._relays = _settled(
            self.env, self.secrets, self.models, providers, reach, self._relays
        )
        found = _docker("image", "inspect", "--format", "{{.Id}}", image)
        if found.returncode != 0:
            raise RuntimeError(
                f"docker cannot find image {image!r}: {found.stderr.strip()[:200]}. "
                f"Pull or build it first, nothing is fetched on a run's behalf"
            )
        if not self.image:
            interrupt.on_hard_exit(self._remove_all)
        # Every run starts from the id, so a tag moved mid-matrix cannot change what the
        # rest of it measures.
        self.image = found.stdout.strip()
        self.where = f"in image {image!r}"

    def argv(
        self,
        argv: Sequence[str],
        scope: Scope,
        cwd: Path | None,
        name: str,
        home: Path,
        tty: bool = False,
    ) -> list[str]:
        """The `docker run` that executes `argv` in `scope`, attached to this terminal when
        `tty` is set. Pure, so it can be asserted."""
        args = [
            "docker",
            "run",
            "--rm",
            "--init",
            "--name",
            name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--volume",
            f"{home}:{HOME}:rw",
            "--env",
            f"HOME={HOME}",
            # Where the relay listens, under the same name on Linux as Docker Desktop has.
            "--add-host",
            "host.docker.internal:host-gateway",
        ]
        if "cpus" in self.limits:
            args += ["--cpus", str(self.limits["cpus"])]
        if "memory" in self.limits:
            # The same ceiling for swap: a limit the run can swap past is not one.
            args += ["--memory", self.limits["memory"], "--memory-swap", self.limits["memory"]]
        if tty:
            args += ["--interactive", "--tty"]
        for variable in (*self.env, *(TERMINAL if tty else ())):
            # By name only: docker copies the value, so a key never sits in an argv.
            args += ["--env", variable]
        mounts = [(p, "rw") for p in scope.writable] + [(p, "ro") for p in scope.readable]
        for path, mode in mounts:
            args += ["--volume", f"{path}:{path}:{mode}"]
        if cwd is not None:
            args += ["--workdir", str(cwd)]
        return [*args, self.image, *argv]

    def run(self, argv: Sequence[str], scope: Scope, **kwargs) -> subprocess.CompletedProcess:
        with self._container(argv, scope, kwargs.pop("cwd", None)) as command:
            return interrupt.run(command, **kwargs)

    def attach(self, argv: Sequence[str], scope: Scope, cwd: Path) -> int:
        """`argv` inside the boundary, in the foreground of this terminal, for a person to
        use: what it reads and draws goes straight through. Its exit status."""
        with self._container(argv, scope, cwd, tty=True) as command:
            return subprocess.run(command).returncode

    @contextmanager
    def _container(
        self, argv: Sequence[str], scope: Scope, cwd: Path | None, tty: bool = False
    ) -> Iterator[list[str]]:
        """The `docker run` of one container, taken down once the caller is done with it."""
        if not self.image:
            raise RuntimeError("the docker backend runs nothing before `prepare`")
        _create(scope)
        name = f"trysquare-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._live.add(name)
        with _home(self._seed, scope) as home:
            try:
                yield self.argv(argv, scope, cwd, name, home, tty)
            finally:
                self._remove(name)

    def _remove(self, name: str) -> None:
        """Takes the container down. `--rm` does it when the client exits normally; a
        client killed outright leaves the container running, and its agent spending."""
        _docker("rm", "--force", name)
        with self._lock:
            self._live.discard(name)

    def _remove_all(self) -> None:
        for name in list(self._live):
            self._remove(name)


#: What a bwrap run sees of the system, read-only: the programs, their libraries and
#: their configuration. Nothing under /home, /tmp or /var, so no other run and nothing
#: of the operator's.
SYSTEM = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")


class Bwrap:
    """One bubblewrap sandbox per run, on this machine's own tools. Linux only.

    The sandbox has its own empty root: the system directories read-only, the run's scope
    at the paths the host has them, `bind` read-only for tools installed elsewhere - a
    `pi` under `~/.nvm`, say - and nothing else. It runs as the operator, shares the
    network to reach the provider, and dies with trysquare, so no run outlives a kill.

    Its environment is `PATH`, `HOME` and the variables `[isolation] env` names, handed
    to bwrap as its own environment rather than as `--setenv` arguments: a variable never
    sits in an argv, where `ps` would show it. The relay listens on the loopback, which
    the sandbox shares.
    """

    name = BWRAP
    image = ""
    where = "under bwrap"
    limits: dict = {}
    takes_image = False

    def __init__(
        self,
        env: Sequence[str] = (),
        secrets: str | None = None,
        models: str | None = None,
        bind: Sequence[str] = (),
    ) -> None:
        self.env = _variables(env)
        self.secrets = Secrets(secrets)
        self.models = _models(models)
        if isinstance(bind, str) or not all(isinstance(p, str) for p in bind):
            raise ValueError(f"bind = {bind!r} is not a list of paths")
        self.bind = tuple(Path(os.path.expanduser(p)) for p in bind)
        self.settings = {"backend": BWRAP, "bind": [str(p) for p in self.bind]}
        self._seed: dict[str, dict] = {}
        self._relays: list[Relay] = []

    def prepare(self, image: str | None, providers: Sequence[str]) -> None:
        reach = Reach(bind="127.0.0.1", host="127.0.0.1")
        self._seed, self._relays = _settled(
            self.env, self.secrets, self.models, providers, reach, self._relays
        )
        probe = _run("bwrap", "--ro-bind", "/", "/", "true")
        if probe.returncode != 0:
            raise RuntimeError(
                f"bwrap cannot make a sandbox here: {probe.stderr.strip()[:200]}. It needs "
                f"bubblewrap installed and unprivileged user namespaces allowed"
            )

    def argv(self, argv: Sequence[str], scope: Scope, cwd: Path | None, home: Path) -> list[str]:
        """The `bwrap` that executes `argv` in `scope`. Pure, so it can be asserted."""
        args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--share-net"]
        for path in SYSTEM:
            args += ["--ro-bind-try", path, path]
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(home), HOME]
        for path in self.bind:
            args += ["--ro-bind", str(path), str(path)]
        for path in scope.writable:
            args += ["--bind", str(path), str(path)]
        for path in scope.readable:
            args += ["--ro-bind", str(path), str(path)]
        if cwd is not None:
            args += ["--chdir", str(cwd)]
        return [*args, "--", *argv]

    def environment(self, *extra: str) -> dict[str, str]:
        """What the sandbox inherits: enough to find the agent, what `env` passes, and the
        `extra` variables that are set here."""
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": HOME,
            **{v: os.environ[v] for v in self.env},
            **{v: os.environ[v] for v in extra if v in os.environ},
        }

    def run(self, argv: Sequence[str], scope: Scope, **kwargs) -> subprocess.CompletedProcess:
        _create(scope)
        with _home(self._seed, scope) as home:
            command = self.argv(argv, scope, kwargs.pop("cwd", None), home)
            return interrupt.run(command, env=self.environment(), **kwargs)

    def attach(self, argv: Sequence[str], scope: Scope, cwd: Path) -> int:
        """As `Docker.attach`. `--new-session` stays: the agent draws in this terminal but
        cannot type into it."""
        _create(scope)
        with _home(self._seed, scope) as home:
            command = self.argv(argv, scope, cwd, home)
            return subprocess.run(command, env=self.environment(*TERMINAL)).returncode


def _bridge() -> str:
    """Where a relay containers can call listens: on docker's bridge, the address
    `host-gateway` resolves to, so nothing outside this machine reaches it. Under Docker
    Desktop that address is inside its VM rather than here, and Desktop forwards
    `host.docker.internal` to the loopback instead."""
    found = _docker(
        "network", "inspect", "bridge", "--format", "{{range .IPAM.Config}}{{.Gateway}}{{end}}"
    )
    gateway = found.stdout.strip()
    if not gateway:
        # An empty address would bind every interface, and offer the key to the network.
        return "127.0.0.1"
    try:
        with socket.socket() as probe:
            probe.bind((gateway, 0))
    except (OSError, ValueError):
        return "127.0.0.1"
    return gateway


def _create(scope: Scope) -> None:
    """The scope's writable directories, made by the operator before the sandbox starts:
    left to docker they would be created as root, and bwrap refuses a missing source."""
    for path in scope.writable:
        path.mkdir(parents=True, exist_ok=True)


@contextmanager
def _home(files: dict[str, dict], scope: Scope) -> Iterator[Path]:
    """A home for one container, owned by the operator, holding what `seed` decided.

    A directory of the operator's rather than files mounted into the image: docker creates
    the directories above a mounted file as root, and `pi` then cannot write its own.

    Its `.gitconfig` marks what the run may write as safe for git. Docker Desktop shows a
    bind mount as owned by another uid than the container's, and git then refuses the
    clone as of "dubious ownership": every `git` the agent runs fails, and so does the
    `git diff` combo builds a node's `diff` read with.
    """
    with tempfile.TemporaryDirectory(prefix="trysquare-home-") as home:
        agent_dir = Path(home) / ".pi" / "agent"
        agent_dir.mkdir(parents=True)
        for name, content in files.items():
            (agent_dir / name).write_text(json.dumps(content, indent=2))
        (Path(home) / ".gitconfig").write_text(gitconfig(scope.writable))
        yield Path(home)


def gitconfig(safe: Sequence[Path]) -> str:
    """A `.gitconfig` trusting each of `safe`, by its exact path rather than `*`.

    Quoted and escaped, since git reads `#` or `;` in a bare value as a comment.
    """
    quoted = (str(path).replace("\\", "\\\\").replace('"', '\\"') for path in safe)
    return "[safe]\n" + "".join(f'\tdirectory = "{path}"\n' for path in quoted)


def _docker(*args: str) -> subprocess.CompletedProcess:
    """Housekeeping around the containers, outside `interrupt.run` on purpose: taking a
    container down has to work after the operator has asked to stop, which is exactly
    when `interrupt.run` refuses to start anything."""
    return _run("docker", *args)


def _run(*argv: str) -> subprocess.CompletedProcess:
    """A short command whose failure is an answer, never an exception."""
    try:
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 1, "", str(e))


BACKENDS = {NONE: Unconfined, DOCKER: Docker, BWRAP: Bwrap}


def backend(settings: dict) -> Confinement:
    """The backend the config's `[isolation]` section describes.

    Raises `ValueError` naming what is wrong, so `config.load` can refuse the file before
    anything runs. Building one checks the settings only: whether the backend can run on
    this machine is asked when a launch starts.
    """
    settings = dict(settings)
    name = settings.pop("backend", NONE)
    if name not in BACKENDS:
        raise ValueError(f"backend = {name!r} is not one (known: {', '.join(BACKENDS)})")
    try:
        return BACKENDS[name](**settings)
    except TypeError:
        raise ValueError(f"the {name!r} backend takes no {', '.join(sorted(settings))}") from None
