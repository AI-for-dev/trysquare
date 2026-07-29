"""What counts as a measurement, and how metrics are combined.

Two rules carry most of the weight here, and both exist because their absence
produced published numbers that were wrong.

1. **A run counts only if it consumed tokens.** A provider that cuts the stream
   leaves `pi` retrying and then returning turns that are real but empty. Such a
   run breaks no rule, touches no file and fails no test, so a naive harness
   records it as exemplary. Five of the bench's defects were this same mistake in
   five disguises: "did not do the work" read as "worked well".

2. **A validator that could not judge never yields a verdict.** A crash, a
   timeout, unreadable JSON or a missing declared metric makes the run invalid,
   not false.

Pure: text and dictionaries in, dictionaries out. No subprocess, no file.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

VALID = "valid"
EMPTY = "empty"
VALIDATOR_FAILED = "validator_failed"


@dataclass
class Run:
    """One execution of one cell, and everything known about it."""

    id: str
    cell: str
    repetition: int
    usage: dict = field(default_factory=dict)
    duration: int = 0
    metrics: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)
    state: str = VALID
    detail: str = ""
    attempts: int = 1

    @property
    def is_valid(self) -> bool:
        return self.state == VALID

    @property
    def retries(self) -> int:
        return self.usage.get("retries", 0)


def strip(stream: str) -> dict:
    """Reduces a `pi --mode json` stream to the numbers a measurement needs.

    Turns are counted on `message_end` events **carrying a usage**, not on
    `turn_end`. The `usage` filter is what makes a counted turn a billed turn:
    without it, a run that produced nothing still reports turns.

    Retries matter beyond logging. When the stream is cut, `pi` replays the turn
    with the whole accumulated context, so input tokens, turns and duration
    inflate without the measured configuration having anything to do with it.
    Measured on the bench: zero retries gives 4 turns and 15.9k input tokens,
    thirteen retries gives 24 turns and 79.4k. Publishing those columns without
    looking at retries means publishing our own load on the provider.
    """
    u = {"input": 0, "output": 0, "cacheRead": 0, "cost": 0.0, "turns": 0, "retries": 0}
    for line in stream.split("\n"):
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "auto_retry_start":
            u["retries"] += 1
            continue
        if event.get("type") != "message_end":
            continue
        usage = (event.get("message") or {}).get("usage")
        if not usage:
            continue
        u["turns"] += 1
        u["input"] += usage.get("input", 0)
        u["output"] += usage.get("output", 0)
        u["cacheRead"] += usage.get("cacheRead", 0)
        u["cost"] += (usage.get("cost") or {}).get("total", 0.0)
    return u


def strip_session(session: str) -> dict:
    """Same numbers, from an archived session file instead of a live stream.

    A session has a different shape from the stream: its line types are
    `session`, `model_change`, `thinking_level_change` and `message`, and usage
    is nested under `message.usage`. Two different paths to the same figures,
    which is exactly what makes comparing them a real test of the stripping layer
    rather than a tautology.

    `retries` is **not** recoverable here: it derives from `auto_retry_start`, a
    stream event that a session does not contain. Reported as None so a caller
    cannot mistake it for zero.
    """
    u = {"input": 0, "output": 0, "cacheRead": 0, "cost": 0.0, "turns": 0, "retries": None}
    for line in session.split("\n"):
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message":
            continue
        usage = (event.get("message") or event).get("usage")
        if not usage:
            continue
        u["turns"] += 1
        u["input"] += usage.get("input", 0)
        u["output"] += usage.get("output", 0)
        u["cacheRead"] += usage.get("cacheRead", 0)
        u["cost"] += (usage.get("cost") or {}).get("total", 0.0)
    return u


def thinking_levels(session: str) -> list[str]:
    """Every thinking level the session recorded.

    Used by the smoke pass to check that the level a cell declared is the level
    that actually ran. The defect that made the thinking cell identical to the
    baseline in every published matrix cannot survive this check.
    """
    levels = []
    for line in session.split("\n"):
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thinking_level_change":
            levels.append(event.get("level") or event.get("thinkingLevel"))
    return levels


def consumed_tokens(usage: dict) -> bool:
    """The one thing that distinguishes a measurement from an incident."""
    return bool(usage.get("turns")) and bool(usage.get("input")) and bool(usage.get("output"))


def kind(value) -> str:
    """How a metric can be aggregated, from its type alone.

    Booleans become rates, numbers become medians, anything else is diagnostic:
    readable in a single run, never carrying a verdict. `issues = ["#1"]` says
    which issue overflowed, and there is no median of that.
    """
    if isinstance(value, bool):
        return "rate"
    if isinstance(value, (int, float)):
        return "median"
    return "diagnostic"


def scorable(value) -> bool:
    return kind(value) in ("rate", "median")


def merge(results: list[tuple[str, dict]], declared: tuple[str, ...]) -> tuple[dict, dict, str, str]:
    """Combines what the validators returned into one measurement line.

    `results` is [(validator mode, parsed JSON), ...]. A validator that failed
    passes None as its payload.

    Returns (metrics, reasons, state, detail). Every declared metric must be
    present; anything extra is kept but cannot be scored, which is what lets a
    general-purpose validator be reused across scenarios and lets a metric
    already paid for be scored later without remeasuring.
    """
    metrics: dict = {}
    reasons: dict = {}

    for mode, payload in results:
        if payload is None:
            return metrics, reasons, VALIDATOR_FAILED, f"validator {mode!r} failed"
        if not isinstance(payload, dict):
            return metrics, reasons, VALIDATOR_FAILED, f"validator {mode!r} returned no object"
        got = payload.get("metrics")
        if not isinstance(got, dict):
            return metrics, reasons, VALIDATOR_FAILED, f"validator {mode!r} returned no metrics"
        metrics.update(got)
        reasons.update(payload.get("reasons") or {})

    missing = [m for m in declared if m not in metrics]
    if missing:
        return (
            metrics,
            reasons,
            VALIDATOR_FAILED,
            f"declared metrics missing: {', '.join(missing)}",
        )
    return metrics, reasons, VALID, ""


def fill_manual(run: Run, manual: dict) -> list[str]:
    """Applies hand-filled metrics, which may only fill a hole.

    A form can supply a metric no mechanism produces. It can never overwrite a
    measured one: that is "the bench computes the verdict, the author does not
    work around it" applied to the interface. Returns the refusals.
    """
    refused = []
    for name, value in manual.items():
        if name in run.metrics:
            refused.append(name)
            continue
        run.metrics[name] = value
    return refused


# --- Aggregation -----------------------------------------------------------


def valid_runs(runs: list[Run], validity: tuple[str, ...] = ()) -> list[Run]:
    """The runs a cell may be aggregated over.

    `validity` names metrics that must be true for a run to count, declared by
    the scenario. `delivered` is the usual one: a run that modified nothing is
    not a disciplined agent, it is an agent that did not work, and without that
    filter it reads as a perfect score.
    """
    out = []
    for r in runs:
        if not r.is_valid:
            continue
        if any(not r.metrics.get(m) for m in validity):
            continue
        out.append(r)
    return out


def rate(runs: list[Run], metric: str) -> tuple[int, int]:
    """How many runs had the metric true, out of how many could say."""
    known = [r for r in runs if metric in r.metrics]
    return sum(1 for r in known if r.metrics[metric]), len(known)


def median(runs: list[Run], key: str) -> float | None:
    values = []
    for r in runs:
        v = r.usage.get(key, r.metrics.get(key))
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        values.append(v)
    return statistics.median(values) if values else None


def series(runs: list[Run], metric: str) -> list[int]:
    """A cell's per-run values for the criterion, as 0/1 or numbers.

    This is what the verdict resamples. Booleans become 0/1 so one code path
    covers both a rate and a median.
    """
    out = []
    for r in runs:
        if metric not in r.metrics:
            continue
        v = r.metrics[metric]
        if isinstance(v, bool):
            out.append(1 if v else 0)
        elif isinstance(v, (int, float)):
            out.append(v)
    return out
