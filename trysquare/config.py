"""The config file, and the hard rule about what it may not contain.

There are no environment variables anywhere in this tool. Its predecessor had
ten, and an environment variable is invisible inheritance: the reader of a
scenario cannot see it, the archive does not record it, and the value that
actually ran is whatever the shell happened to hold.

**A config file may only supply machine paths and load fallbacks.** Provider, model,
thinking level, etalon and repetitions are mandatory in the scenario and raise when
absent. If they could be inherited from here, the same scenario file would measure
something different on another machine, and that is precisely the defect that made the
thinking cell identical to the baseline in every published matrix.

A repository entry may be a directory or a git URL. Both are addresses, and an address
decides where the code is read from, never what is measured of it.

Precedence: scenario (the experiment) > CLI (explicit and announced) > config
(the machine) > built-in defaults.
"""

from __future__ import annotations

import difflib
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "trysquare.toml"

# A repository entry may name a directory on this machine or a git URL. These two
# forms tell them apart: `scheme://...`, and the scp-like `git@host:org/repo.git`
# that git accepts and that no filesystem path looks like.
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
SCP_LIKE = re.compile(r"^[^/\\:]+@[^/\\:]+:")

# Keys a config file is forbidden to carry. Listed so the refusal can name the
# key and say why, rather than ignoring it silently.
FORBIDDEN = ("provider", "model", "thinking", "etalon", "repetitions")

# Sections that describe a machine, and sections that describe an experiment.
# Both files are TOML and the config is the one lying at the root of the
# repository under a guessable name, so handing one to the argument that wants
# the other is the mix-up an operator actually makes - in both directions.
CONFIG_SECTIONS = ("repos", "harness", "defaults")
SCENARIO_SECTIONS = ("scenario", "task", "agent", "protocol", "verdict")

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


def closest(name: str, known) -> str:
    """A parenthetical to append to a refusal, when the name looks like a typo.

    difflib's default cutoff keeps a far miss silent: suggesting `rule` for
    `banana` would decorate every honest refusal with noise, and a suggestion
    that is usually wrong teaches the reader to skip all of them.
    """
    matches = difflib.get_close_matches(str(name), [str(k) for k in known], n=1)
    return f" (did you mean {matches[0]!r}?)" if matches else ""


def which_file(raw: dict) -> str | None:
    """Tells the two TOML files apart, or says it cannot.

    Returns `"config"`, `"scenario"`, or `None` when the file is too empty or
    too mixed to say. Silence is the useful part: a scenario legitimately
    carries `[harness]` to pin bricks by tag, so a file with sections of both
    kinds is a scenario with an ordinary mistake in it, and the caller's own
    refusals name that mistake better than a guess about which file it is.
    """
    config = any(section in raw for section in CONFIG_SECTIONS)
    scenario = any(section in raw for section in SCENARIO_SECTIONS)
    if config and not scenario:
        return "config"
    if scenario and not config:
        return "scenario"
    return None


@dataclass
class Config:
    repos: dict = field(default_factory=dict)
    harness: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=lambda: dict(BUILTIN_DEFAULTS))
    path: Path | None = None

    def repo(self, name: str) -> Path:
        """Resolves a logical repository name to a directory on this machine.

        A scenario writes `repo = "my-repo"`. It carries no machine path, which is
        what makes it portable and keeps one author's directory layout out of an
        experiment file.

        Raises when the entry is a URL: a URL has no local directory until it has
        been cloned, and answering with a plausible path that nothing has created
        yet is how a caller ends up handing git something that is not there.
        """
        return self._resolve("repos", self.repos, name)

    def harness_repo(self, name: str) -> Path:
        return self._resolve("harness", self.harness, name)

    def remote(self, name: str) -> str | None:
        """The git URL a logical name points at, or None when it names a directory.

        The URL is returned **verbatim**. It is not expanded: a username or a token
        taken from the shell is invisible inheritance in its purest form - it does
        not appear in the archive, and the value that actually ran is whatever the
        environment happened to hold. Abolishing that is what this module is for.
        """
        return self._remote("repos", self.repos, name)

    def harness_remote(self, name: str) -> str | None:
        return self._remote("harness", self.harness, name)

    def _declared(self, section: str, table: dict, name: str) -> str:
        """The raw entry, with the refusal that names what is known.

        Two distinct failures, and the message tells them apart: an entry missing
        from a config file that exists, and no config file at all. The second used
        to borrow the first's wording, and "add it to trysquare.toml" reads as
        "edit the file" when the actual fix is to create one.
        """
        if name in table:
            return str(table[name])
        if self.path is None:
            raise ConfigError(
                f"the scenario names the repository {name!r}, and no {CONFIG_NAME} was "
                f"found walking up from the scenario's directory, so [{section}] declares "
                f"nothing. Create one beside the scenario; two lines are enough:\n"
                f"  [{section}]\n"
                f'  {name} = "/path/to/the/checkout"  # or a git URL'
            )
        known = ", ".join(sorted(table)) or "none"
        raise ConfigError(
            f"[{section}] has no entry {name!r} (known: {known}){closest(name, table)}. "
            f"Add it to {self.path}"
        )

    def _remote(self, section: str, table: dict, name: str) -> str | None:
        declared = self._declared(section, table, name)
        return declared if is_remote(declared) else None

    def _resolve(self, section: str, table: dict, name: str) -> Path:
        declared = self._declared(section, table, name)
        if is_remote(declared):
            raise ConfigError(
                f"[{section}] {name} is a git URL ({declared}), which has no local "
                f"directory until it is cloned. It is pinned under the workdir by "
                f"runner.prepare_source()"
            )
        return expand(declared, relative_to=self.path)

    def workdir(self) -> Path:
        return expand(self.defaults.get("workdir", BUILTIN_DEFAULTS["workdir"]))

    def fallback(self, key: str):
        return self.defaults.get(key, BUILTIN_DEFAULTS.get(key))


def is_remote(value: str) -> bool:
    """Whether a repository entry names a git URL rather than a directory.

    This exists so a URL never reaches `expand()`. `Path("https://host/x")` collapses
    the double slash into `https:/host/x`, which is a *relative* path: git would then
    be handed a directory name resolved against the config file's parent, and the
    failure is a clone of nothing rather than an error anyone can read.

    `file://` counts as remote. `Path()` mangles it exactly the same way, and treating
    it as a URL is also what lets the pinning path be tested end to end without a
    network.
    """
    text = str(value)
    return bool(SCHEME.match(text) or SCP_LIKE.match(text))


def expand(value: str, relative_to: Path | None = None) -> Path:
    """Expands `~`, `$TMPDIR` and friends, then anchors relative paths.

    A relative path in a config file is relative to that file, not to the current
    working directory: the config describes a machine, and where the operator
    happens to stand when running a command is not part of it.

    Only ever called on a path. A URL is kept verbatim - see `is_remote`.
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

    # The mirror of the refusal in `scenario.parse`, and the more dangerous of
    # the two: a scenario read as a config carries no [repos] and no [defaults],
    # so it would load as built-in defaults and a silent absence of machine
    # paths. Nothing about it looks wrong until a run resolves a repository.
    if which_file(raw) == "scenario":
        raise ConfigError(
            f"{found}: this is a scenario file, not a config: it describes an "
            f"experiment, not a machine. Read as a config it would supply "
            f"nothing at all and every default would be the built-in one. Pass "
            f"it as the scenario argument instead; the config is the file "
            f"holding [repos], [harness] and [defaults]"
        )

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
