# SPDX-License-Identifier: BSD-3-Clause
"""Where the value of a secret a relay holds comes from.

The environment trysquare runs in first, so CI and a shell that exports the key work
as they did. Then, for a variable that is unset, the command `[isolation.secrets]`
names, run on this machine: a keychain, a password manager, anything that prints the
secret. The command runs at most once per launch, and what it prints is kept in memory
only, for the relays to hold.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping

#: A variable name, as a `$NAME` reference in `models.json` writes it.
NAME = re.compile(r"^[A-Za-z_]\w*$")


class Secrets:
    """The commands `[isolation.secrets]` declares, by variable name."""

    def __init__(self, sources: Mapping[str, object] | None = None) -> None:
        sources = {} if sources is None else sources
        if not isinstance(sources, Mapping):
            raise ValueError(f"secrets = {sources!r} is not a table of variable names")
        self._commands = {name: _command(name, source) for name, source in sources.items()}
        self._values: dict[str, str] = {}

    def known(self, name: str) -> bool:
        """Whether `name` has a value here, without fetching it."""
        return name in os.environ or name in self._commands

    def value(self, name: str) -> str:
        """The value of `name`, from the environment or else from its command."""
        if name in os.environ:
            return os.environ[name]
        if name not in self._values:
            self._values[name] = _fetch(name, self._commands[name])
        return self._values[name]


def _command(name: str, source: object) -> tuple[str, ...]:
    """The argv `source` declares for `name`, refused unless it is `{ command = [...] }`."""
    if not NAME.match(name):
        raise ValueError(f"secrets.{name} is not a variable name")
    alone = isinstance(source, Mapping) and set(source) == {"command"}
    command = source["command"] if alone else None
    if not (isinstance(command, list) and command and all(isinstance(a, str) for a in command)):
        raise ValueError(f'secrets.{name} = {source!r} is not {{ command = ["program", ...] }}')
    return tuple(command)


def _fetch(name: str, command: tuple[str, ...]) -> str:
    """What `command` prints, trimmed. Its stdin and stderr stay the operator's, so a
    tool that asks for a passphrase can ask. A failure names the variable and the exit
    code, never the output, which may hold the secret."""
    where = f"[isolation.secrets] {name}: {command[0]!r}"
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, text=True)
    except OSError as e:
        raise RuntimeError(f"{where} cannot run: {e.strerror}") from None
    if done.returncode != 0:
        raise RuntimeError(f"{where} exited with {done.returncode}")
    value = done.stdout.strip()
    if not value:
        raise RuntimeError(f"{where} printed nothing")
    return value
