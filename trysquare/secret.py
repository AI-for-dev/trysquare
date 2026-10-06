# SPDX-License-Identifier: BSD-3-Clause
"""Where the value of a secret a relay holds comes from.

The environment trysquare runs in first, so CI and a shell that exports the key work
as they did. Then, for a variable that is unset, the `.env` file `[isolation] secrets`
names. The file is read at most once per launch, and what it holds is kept in memory
only, for the relays to hold.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: One assignment of a `.env` file, `export` prefix allowed.
ASSIGNMENT = re.compile(r"^(?:export\s+)?(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<value>.*)$")


class Secrets:
    """The environment, then the `.env` file `[isolation] secrets` names, if it names one."""

    def __init__(self, file: str | None = None) -> None:
        if file is not None and not isinstance(file, str):
            raise ValueError(f"secrets = {file!r} is not a path")
        self.file = Path(file) if file else None
        self._listed: dict[str, str] | None = None

    def known(self, name: str) -> bool:
        """Whether `name` has a value here."""
        return name in os.environ or name in self._read()

    def value(self, name: str) -> str:
        """The value of `name`, from the environment or else from the file."""
        if name in os.environ:
            return os.environ[name]
        if not (value := self._read()[name]):
            raise RuntimeError(f"[isolation] secrets file {self.file} sets {name} to nothing")
        return value

    def _read(self) -> dict[str, str]:
        if self._listed is None:
            self._listed = {} if self.file is None else _parse(self.file)
        return self._listed


def _parse(path: Path) -> dict[str, str]:
    """The assignments of a `.env` file. A line that is not one is refused by its number,
    never quoted, since it may hold a secret."""
    try:
        text = path.read_text()
    except OSError as e:
        raise RuntimeError(f"[isolation] secrets file {path}: {e.strerror}") from None
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not (found := ASSIGNMENT.match(line)):
            raise RuntimeError(f"[isolation] secrets file {path}, line {number}: not NAME=value")
        values[found["name"]] = _unquoted(found["value"])
    return values


def _unquoted(value: str) -> str:
    """A value without its quotes, or without a trailing ` # comment` when it has none."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()
