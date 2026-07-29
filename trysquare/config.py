"""The config file, and the hard rule about what it may not contain.

There are no environment variables anywhere in this tool. Its predecessor had
ten, and an environment variable is invisible inheritance: the reader of a
scenario cannot see it, the archive does not record it, and the value that
actually ran is whatever the shell happened to hold.

**A config file may only supply machine paths and load fallbacks.** Provider,
model, thinking level, etalon and repetitions are mandatory in the scenario and
raise when absent. If they could be inherited from here, the same scenario file
would measure something different on another machine, and that is precisely the
defect that made the thinking cell identical to the baseline in every published
matrix.

Precedence: scenario (the experiment) > CLI (explicit and announced) > config
(the machine) > built-in defaults.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "trysquare.toml"

# Keys a config file is forbidden to carry. Listed so the refusal can name the
# key and say why, rather than ignoring it silently.
FORBIDDEN = ("provider", "model", "thinking", "etalon", "repetitions")

BUILTIN_DEFAULTS = {
    "workdir": "$TMPDIR/trysquare",
    "concurrency": 5,
    "timeout": 900,
    "attempts": 3,
    "draws": 10_000,
    "seed": 20260729,
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    repos: dict = field(default_factory=dict)
    harness: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=lambda: dict(BUILTIN_DEFAULTS))
    path: Path | None = None

    def repo(self, name: str) -> Path:
        """Resolves a logical repository name to a path.

        A scenario writes `repo = "neon"`. It carries no machine path, which is
        what makes it portable and keeps one author's directory layout out of an
        experiment file.
        """
        return self._resolve("repos", self.repos, name)

    def harness_repo(self, name: str) -> Path:
        return self._resolve("harness", self.harness, name)

    def _resolve(self, section: str, table: dict, name: str) -> Path:
        if name not in table:
            known = ", ".join(sorted(table)) or "none"
            raise ConfigError(
                f"[{section}] has no entry {name!r} (known: {known}). "
                f"Add it to {self.path or CONFIG_NAME}"
            )
        return expand(table[name], relative_to=self.path)

    def workdir(self) -> Path:
        return expand(self.defaults.get("workdir", BUILTIN_DEFAULTS["workdir"]))

    def fallback(self, key: str):
        return self.defaults.get(key, BUILTIN_DEFAULTS.get(key))


def expand(value: str, relative_to: Path | None = None) -> Path:
    """Expands `~`, `$TMPDIR` and friends, then anchors relative paths.

    A relative path in a config file is relative to that file, not to the current
    working directory: the config describes a machine, and where the operator
    happens to stand when running a command is not part of it.
    """
    text = os.path.expandvars(str(value))
    if "$TMPDIR" in str(value) and "$TMPDIR" in text:
        # expandvars leaves it alone when TMPDIR is unset.
        import tempfile

        text = text.replace("$TMPDIR", tempfile.gettempdir())
    path = Path(text).expanduser()
    if not path.is_absolute() and relative_to is not None:
        path = (relative_to.parent / path).resolve()
    return path


def load(path: str | Path | None = None, start: Path | None = None) -> Config:
    """Reads the config file, or returns built-in defaults when there is none.

    An absent config file is not an error: a scenario that names no logical
    repository needs nothing resolved.
    """
    if path is not None:
        found = Path(path)
        if not found.exists():
            raise ConfigError(f"config file not found: {found}")
    else:
        found = discover(start or Path.cwd())
        if found is None:
            return Config()

    try:
        raw = tomllib.loads(found.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{found}: invalid TOML: {e}") from e

    defaults = dict(BUILTIN_DEFAULTS) | dict(raw.get("defaults", {}))
    offending = [k for k in FORBIDDEN if k in defaults]
    if offending:
        raise ConfigError(
            f"{found}: [defaults] may not set {', '.join(offending)}. "
            f"These decide what is measured, so they belong to the scenario and "
            f"are never inherited: the same file must not measure something "
            f"different on another machine"
        )

    return Config(
        repos=dict(raw.get("repos", {})),
        harness=dict(raw.get("harness", {})),
        defaults=defaults,
        path=found,
    )


def discover(start: Path) -> Path | None:
    """Walks up from `start` looking for a config file.

    Walking up is a convenience for the operator, never a way to inherit a
    measurement: what is found here can only ever be a path or a load fallback.
    """
    for directory in [start, *start.parents]:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None
