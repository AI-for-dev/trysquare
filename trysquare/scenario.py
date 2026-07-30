"""Loading a scenario, and expanding it into the cells it describes.

A scenario is one self-contained experiment in one TOML file: the task, the
configurations to compare, the protocol, and the validation. This module turns
that file into data and refuses it when it is not an experiment.

Nothing here touches the disk beyond reading the file it is handed, and nothing
here runs an agent. That is deliberate: every rule below is a rule about what
counts as a well-formed experiment, and those rules are worth testing without a
network, a clone, or an API key.
"""

from __future__ import annotations

import itertools
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG_SECTIONS, SCENARIO_SECTIONS, which_file

# Keys that decide *what is measured*. They are mandatory in the scenario and
# they can never come from the config file or from a built-in default.
#
# This is not defensiveness, it is the most expensive lesson this project has
# learned. The bench used to read the thinking level from the operator's
# personal settings file, so the cell that was supposed to test thinking was
# silently identical to the baseline in every matrix ever measured. A value that
# is not declared is a value inherited from whoever happened to run the tool.
REQUIRED = (
    ("task", "etalon"),
    ("agent", "provider"),
    ("agent", "model"),
    ("agent", "thinking"),
    ("protocol", "repetitions"),
)

# The metric whose declaration makes `[task].test_command` mandatory.
#
# The command is **declared, never detected**, and the reason is the same lesson as
# `REQUIRED` above wearing different clothes. The obvious detection is `npm test`,
# whose meaning is read from `package.json` - a file inside the perimeter the
# measured agent may edit. Broken code plus a `scripts.test` of `echo ok` scores
# green, and nothing in the output says so. A detected command hands the choice of
# how a run is measured to the very agent being measured.
#
# Every documented migration in comparable tools runs the same way, from detecting
# towards declaring, and none the other way: Heroku's Python buildpack removed two
# heuristics with the motive written into its source, Renovate is mid-removal, and
# SWE-bench - which has exactly this problem - wrote 315 test commands by hand.
#
# Required by the metric rather than by the section, because a scenario that
# measures prose has no suite to name and demanding one would be ceremony.
TESTS_METRIC = "tests"

# Written as a string - the command as an author would type it - and split once, here,
# with `shlex`. So the *file* is ergonomic and the *data* is unambiguous: everything
# downstream receives an argv and never splits again.
#
# The words below only mean anything to a shell, and no shell runs this command. Left to
# reach `subprocess` they would arrive as arguments to the runner and fail in a way nobody
# could read, so they are refused at load time instead. That refusal is the whole reason a
# string is safe to accept: it is louder than the list form it replaces, which merely made
# them harmless.
SHELL_ONLY = frozenset({"&&", "||", ";", "|", ">", ">>", "<", "&"})


def split_command(command: str) -> tuple[str, ...]:
    """One declared command, as an argv.

    The single splitting rule, and the only one: the loader vetting a scenario and the base
    running the command both come here. Two implementations would be the drift this module
    spends its whole length refusing - and a command split two slightly different ways would
    be measured two slightly different ways.

    `shlex` is the shell's own word splitting, quotes included, which is why the scenario can
    carry a string an author would recognise. Nothing here runs a shell; `SHELL_ONLY` above is
    what makes that safe.
    """
    return tuple(shlex.split(command))


class ScenarioError(Exception):
    """A scenario that is not a well-formed experiment.

    Always raised at load time, never at measure time: the point is to fail
    before spending tokens, not after.
    """


@dataclass(frozen=True)
class Cell:
    """One configuration to measure, and how it differs from the baseline."""

    name: str
    delta: dict = field(default_factory=dict)

    @property
    def is_baseline(self) -> bool:
        return not self.delta


@dataclass(frozen=True)
class Validator:
    """One validator, and the metrics it contracts to return.

    `metrics` is a contract rather than documentation: a run whose validator
    omits one of these names is invalid, and a metric nobody declared can never
    carry a verdict.
    """

    mode: str
    metrics: tuple[str, ...]
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    task: dict
    agent: dict
    protocol: dict
    cells: tuple[Cell, ...]
    validators: tuple[Validator, ...]
    verdict: dict
    bricks: dict = field(default_factory=dict)
    hypothesis: str | None = None
    axes: dict = field(default_factory=dict)
    path: Path | None = None

    @property
    def runs(self) -> int:
        """Total executions this scenario asks for."""
        return len(self.cells) * self.protocol["repetitions"]

    @property
    def declared_metrics(self) -> tuple[str, ...]:
        return tuple(m for v in self.validators for m in v.metrics)

    def cell(self, name: str) -> Cell:
        for c in self.cells:
            if c.name == name:
                return c
        raise ScenarioError(f"unknown cell {name!r}")

    @property
    def reference(self) -> str:
        """The cell every gap is measured against, as a cell name."""
        return _reference_name(self.verdict.get("reference"), self.axes)


def load(path: str | Path) -> Scenario:
    """Reads a scenario file and refuses it if it is not an experiment."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ScenarioError(f"{path}: invalid TOML: {e}") from e

    return parse(raw, path=path)


def parse(raw: dict, path: Path | None = None) -> Scenario:
    """Same as `load`, from an already-parsed mapping.

    Split out so the rules can be tested without writing files.
    """
    where = f"{path}: " if path else ""

    _refuse_a_config_file(raw, where)

    for section in SCENARIO_SECTIONS:
        if section not in raw:
            raise ScenarioError(f"{where}missing section [{section}]")

    for section, key in REQUIRED:
        if raw.get(section, {}).get(key) is None:
            raise ScenarioError(
                f"[{section}].{key} is required in the scenario and is never "
                f"inherited: a value that is not declared is a value inherited "
                f"from whoever runs the tool"
            )

    axes = raw.get("axes", {})
    values = raw.get("values", {})
    cells = _expand(axes, values, raw.get("variants", {}))
    if not cells:
        raise ScenarioError("no cells: declare [axes] or [variants]")

    validators = _validators(raw.get("validation", []))
    verdict = dict(raw["verdict"])
    _check_verdict(verdict, validators, cells, axes)
    _check_test_command(raw["task"], validators, where)

    scenario = raw["scenario"]
    return Scenario(
        name=scenario.get("name") or (path.stem if path else "scenario"),
        title=scenario.get("title", ""),
        hypothesis=scenario.get("hypothesis"),
        task=dict(raw["task"]),
        agent=dict(raw["agent"]),
        protocol=dict(raw["protocol"]),
        cells=cells,
        validators=validators,
        verdict=verdict,
        bricks=dict(raw.get("harness", {})),
        axes=axes,
        path=path,
    )


def _refuse_a_config_file(raw: dict, where: str) -> None:
    """Says "that is the config file" instead of "this scenario is malformed".

    `run trysquare.toml` costs nothing but a command, and the section-by-section
    refusal in `parse` reads like a broken scenario rather than the wrong file.
    """
    if which_file(raw) != "config":
        return

    present = ", ".join(f"[{s}]" for s in CONFIG_SECTIONS if s in raw)
    raise ScenarioError(
        f"{where}this is a config file, not a scenario: it carries {present} "
        f"and nothing that describes an experiment. The config describes the "
        f"machine and is found on its own, or given with --config. Pass a "
        f"scenario file - the one holding [task], [agent], [protocol] and "
        f"[verdict]"
    )


def _expand(axes: dict, values: dict, variants: dict) -> tuple[Cell, ...]:
    """Grid cells then named variants, added rather than chosen between.

    A scenario may use both: a grid is concise for the regular part, named
    variants are precise for the irregular one.
    """
    cells: list[Cell] = []

    if axes:
        _check_axes(axes, values)
        names = list(axes)  # declaration order fixes the order of the table
        for combo in itertools.product(*[axes[n] for n in names]):
            delta: dict = {}
            for axis, value in zip(names, combo):
                delta.update(values.get(axis, {}).get(value, {}))
            cells.append(Cell(" / ".join(combo), delta))

    for name, delta in variants.items():
        cells.append(Cell(name, dict(delta)))

    seen = [c.name for c in cells]
    duplicate = {n for n in seen if seen.count(n) > 1}
    if duplicate:
        raise ScenarioError(f"cell declared twice: {', '.join(sorted(duplicate))}")
    return tuple(cells)


def _check_axes(axes: dict, values: dict) -> None:
    """The counterpart of leaving the baseline implicit.

    An axis value with no delta block *is* the baseline, which is concise and
    shows that the baseline is a cell of the matrix rather than a seventh cell
    beside it. But a typo would then produce a silent cell identical to the
    baseline, published twice under two names.

    The rule that makes the typo loud without adding any syntax: the baseline of
    an axis is its first value, and every other value must declare a delta. A
    misspelled value is never the first one, so it has no block, so this raises.
    """
    for axis, declared in axes.items():
        if not declared:
            raise ScenarioError(f"axis {axis!r} has no values")
        for value in declared[1:]:
            if not values.get(axis, {}).get(value):
                known = sorted(values.get(axis, {}))
                raise ScenarioError(
                    f"axis {axis!r}: value {value!r} declares no delta. Only the "
                    f"first value of an axis ({declared[0]!r}) is the baseline. "
                    f"Deltas declared for this axis: {known or 'none'}"
                )


def _check_test_command(task: dict, validators: tuple[Validator, ...], where: str) -> None:
    """Refuses a scenario that scores a test suite without naming the suite.

    See `TESTS_METRIC` for why the command is declared and never detected.

    Written as a string and split once here, with `shlex` - the shell's own word
    splitting, quotes included, which is a rule every author already knows. The file is
    therefore what you would type, and everything downstream receives an argv.

    No shell ever runs it. What that used to buy by refusing strings outright, it now buys
    better: a word that only means something to a shell is **named and refused** at load
    time, instead of reaching the runner as an argument and failing where nobody can read
    it. See `SHELL_ONLY`.

    A command declared by a scenario that scores no tests is kept and not refused: a
    scenario may name its suite before a validator scores it, and refusing that would
    punish writing the file in the order it is natural to write it.
    """
    for i, step in enumerate(task.get("prepare", ())):
        _check_one_command(step, f"[task].prepare[{i}]", where)

    command = task.get("test_command")

    if command is None:
        if TESTS_METRIC in {m for v in validators for m in v.metrics}:
            raise ScenarioError(
                f"{where}a validator declares the {TESTS_METRIC!r} metric, so "
                f"[task].test_command is required: it names the suite that decides "
                f"that metric, as you would type it.\n"
                f'  test_command = "node --test \'game/**/*.test.js\'"\n'
                f"It is declared and never detected, because the obvious detection "
                f"reads `package.json`, which the measured agent may edit - broken "
                f"code plus a test script of `echo ok` would score green."
            )
        return

    _check_one_command(command, "[task].test_command", where)


def _check_one_command(command, field: str, where: str) -> None:
    """One declared command: a string, splittable, and not secretly a shell line."""
    if not isinstance(command, str):
        raise ScenarioError(
            f"{where}{field} must be a string - the command as you would type it - "
            f"got {command!r}"
        )

    try:
        words = split_command(command)
    except ValueError as e:
        raise ScenarioError(
            f"{where}{field} does not split into words ({e}): {command!r}"
        ) from e

    if not words:
        raise ScenarioError(f"{where}{field} is empty")

    smuggled = sorted(set(words) & SHELL_ONLY)
    if smuggled:
        raise ScenarioError(
            f"{where}{field} looks like a shell command: it carries "
            f"{', '.join(smuggled)}.\n"
            f"  {command}\n"
            f"No shell runs it - the words are handed straight to the process - so a "
            f"redirection, a pipe or an `&&` would reach the runner as arguments and fail "
            f"in a way nobody could read. Refused here rather than there. For several "
            f"steps use `prepare`, which is a list; each entry is still one command."
        )


def _validators(declared: list) -> tuple[Validator, ...]:
    if not declared:
        raise ScenarioError("no [[validation]]: a scenario that measures nothing")

    validators = []
    for entry in declared:
        entry = dict(entry)
        mode = entry.pop("mode", None)
        if mode not in ("script", "judge", "form"):
            raise ScenarioError(
                f"[[validation]].mode must be script, judge or form, got {mode!r}"
            )
        metrics = tuple(entry.pop("metrics", ()))
        if not metrics:
            raise ScenarioError(f"validator {mode!r} declares no metrics")
        validators.append(Validator(mode, metrics, entry))

    # Two validators cannot both own a metric name. Refused here, before any
    # measurement, rather than resolved by a last-one-wins rule nobody can see.
    # Independent validators are the whole point: a judge told the script's
    # verdict is anchored on it, and its agreement stops being a signal.
    everything = [m for v in validators for m in v.metrics]
    clash = {m for m in everything if everything.count(m) > 1}
    if clash:
        raise ScenarioError(
            f"metric declared by two validators: {', '.join(sorted(clash))}. "
            f"Rename one (for instance overflow_judge)"
        )
    return tuple(validators)


def _check_verdict(
    verdict: dict, validators: tuple[Validator, ...], cells: tuple[Cell, ...], axes: dict
) -> None:
    criterion = verdict.get("criterion")
    if not criterion:
        raise ScenarioError("[verdict].criterion is required")

    declared = [m for v in validators for m in v.metrics]
    if criterion not in declared:
        raise ScenarioError(
            f"[verdict].criterion is {criterion!r}, which no validator declares. "
            f"Declared metrics: {', '.join(declared)}"
        )

    reference = _reference_name(verdict.get("reference"), axes)
    if reference not in [c.name for c in cells]:
        raise ScenarioError(
            f"[verdict].reference is {reference!r}, which is not a cell. "
            f"Cells: {', '.join(c.name for c in cells)}"
        )

    for metric in verdict.get("validity", ()):
        if metric not in declared:
            raise ScenarioError(
                f"[verdict].validity names {metric!r}, which no validator declares"
            )


def _reference_name(reference, axes: dict) -> str:
    """A cell name, from either form the reference can take.

    Two forms because there are two ways to name a cell: an inline table for a
    grid (`{context = "none", thinking = "off"}`) and a plain string for a
    variant.
    """
    if reference is None:
        raise ScenarioError("[verdict].reference is required")
    if isinstance(reference, str):
        return reference
    if isinstance(reference, dict):
        try:
            return " / ".join(reference[axis] for axis in axes)
        except KeyError as e:
            raise ScenarioError(
                f"[verdict].reference does not give a value for axis {e.args[0]!r}"
            ) from e
    raise ScenarioError(f"[verdict].reference must be a string or a table, got {reference!r}")
