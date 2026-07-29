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
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

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
    for section in ("scenario", "task", "agent", "protocol", "verdict"):
        if section not in raw:
            raise ScenarioError(f"missing section [{section}]")

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
