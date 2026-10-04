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

import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import interrupt

NONE = "none"
DOCKER = "docker"


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

    def prepare(self, image: str | None) -> None:
        """Checks this machine can run the backend, or raises `RuntimeError` saying why.

        `image` is what the scenario declares in `[agent] image`.
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

    def prepare(self, image: str | None) -> None:
        pass

    def run(self, argv: Sequence[str], scope: Scope, **kwargs) -> subprocess.CompletedProcess:
        return interrupt.run(argv, **kwargs)


#: Where the agent's home is inside a container. A fresh directory per run, so nothing
#: one run leaves in it reaches the next.
HOME = "/home/trysquare"

#: What the agent's home starts with, copied from the operator's `~/.pi/agent`: the
#: providers it may call and the settings a subagent inherits its thinking level from.
#: Copied rather than mounted, because docker creates the directories above a mounted
#: file as root and `pi` then cannot write its own. Never `auth.json`, whose tokens the
#: agent could read and refresh.
PI_CONFIG = ("models.json", "settings.json")


class Docker:
    """One container per run, from the image the scenario declares.

    The container sees the run's scope and nothing else, each path where the host has it.
    It runs as the operator's uid so what the agent writes in the clone stays the
    operator's, and its environment is the variables `[isolation] env` names: a provider
    key reaches the agent that way, and the agent can read it.
    """

    name = DOCKER

    def __init__(self, env: Sequence[str] = ()) -> None:
        if isinstance(env, str) or not all(isinstance(v, str) for v in env):
            raise ValueError(f"env = {env!r} is not a list of variable names")
        self.env = tuple(env)
        self.image = ""
        self.where = "in docker"
        self._live: set[str] = set()
        self._lock = threading.Lock()

    def prepare(self, image: str | None) -> None:
        if not image:
            raise RuntimeError(
                "the docker backend runs the image the scenario declares, and [agent] "
                "declares no image"
            )
        unset = [v for v in self.env if v not in os.environ]
        if unset:
            raise RuntimeError(f"[isolation] env names {', '.join(unset)}, unset here")
        running = _docker("version")
        if running.returncode != 0:
            raise RuntimeError(f"docker does not answer here: {running.stderr.strip()[:200]}")
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
        self, argv: Sequence[str], scope: Scope, cwd: Path | None, name: str, home: Path
    ) -> list[str]:
        """The `docker run` that executes `argv` in `scope`. Pure, so it can be asserted."""
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
        ]
        for variable in self.env:
            # By name only: docker copies the value, so a key never sits in an argv.
            args += ["--env", variable]
        mounts = [(p, "rw") for p in scope.writable] + [(p, "ro") for p in scope.readable]
        for path, mode in mounts:
            args += ["--volume", f"{path}:{path}:{mode}"]
        if cwd is not None:
            args += ["--workdir", str(cwd)]
        return [*args, self.image, *argv]

    def run(self, argv: Sequence[str], scope: Scope, **kwargs) -> subprocess.CompletedProcess:
        if not self.image:
            raise RuntimeError("the docker backend runs nothing before `prepare`")
        for path in scope.writable:
            # A missing source would be created by docker, as root.
            path.mkdir(parents=True, exist_ok=True)
        name = f"trysquare-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._live.add(name)
        with _home() as home:
            try:
                command = self.argv(argv, scope, kwargs.pop("cwd", None), name, home)
                return interrupt.run(command, **kwargs)
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


@contextmanager
def _home() -> Iterator[Path]:
    """A home for one container, owned by the operator and seeded with `PI_CONFIG`."""
    with tempfile.TemporaryDirectory(prefix="trysquare-home-") as home:
        agent_dir = Path(home) / ".pi" / "agent"
        agent_dir.mkdir(parents=True)
        config = Path.home() / ".pi" / "agent"
        for name in PI_CONFIG:
            if (config / name).is_file():
                shutil.copy(config / name, agent_dir / name)
        yield Path(home)


def _docker(*args: str) -> subprocess.CompletedProcess:
    """Housekeeping around the containers, outside `interrupt.run` on purpose: taking a
    container down has to work after the operator has asked to stop, which is exactly
    when `interrupt.run` refuses to start anything."""
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


BACKENDS = {NONE: Unconfined, DOCKER: Docker}


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
