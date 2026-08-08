# SPDX-License-Identifier: BSD-3-Clause
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
from pathlib import Path

VALID = "valid"
EMPTY = "empty"
VALIDATOR_FAILED = "validator_failed"

#: The longest a line may be and still be read as an event. The agent's stream is the
#: one input here with no size anyone controls, and a reader that holds a whole line
#: is only bounded if the writer emits newlines - which nothing promises.
LINE_LIMIT = 8 * 1024 * 1024


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


def decoded(lines):
    """Every JSON object among these lines.

    The one tolerance shared by every reader of these files. A line is decoded if
    it looks like JSON, whatever whitespace surrounds it, and anything else is
    skipped rather than fatal: a cut stream ends mid-line, and the lines before
    the cut are still evidence.
    """
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def events(text: str):
    """Every JSON object in a text held in memory.

    For what is small enough to hold: an archived session, a fixture in a test. The
    agent's own stream is not, and is read by `read_file`.
    """
    return decoded(text.split("\n"))


def one_line(text: str) -> str:
    """Whitespace collapsed, so a `detail` stays on the line that reports it.

    Every `detail` is printed as the tail of a one-line run report and stored in the
    ledger. A provider's message arrives with the newline it was written with, which
    left a blank line under each failing run and a trailing `\\n` inside `state.json`.
    """
    return " ".join(text.split())


def counted(n: int, noun: str, plural: str | None = None) -> str:
    """`1 run`, `2 runs`, `0 runs`, and `2 passes` when `s` is not enough.

    Here rather than beside the parser because four modules print counts, and the one
    that reached a real matrix - `no price on 1 archived valid runs` - was written where
    a helper living in the command line could not be reached.

    A count of the form `x of y` takes its plural from `y` and does not come through
    here: `1 of 3 runs` is already right.
    """
    return f"{n} {noun}" if n == 1 else f"{n} {plural or noun + 's'}"


def _blank_usage() -> dict:
    return {"input": 0, "output": 0, "cacheRead": 0, "cost": 0.0, "turns": 0}


def _add_usage(u: dict, usage: dict | None) -> None:
    """One billed turn, added. The arithmetic a stream and a session share.

    Written once so it cannot drift apart between the two paths, which is what
    `parity` layer 1 compares. What still differs between them - the event type
    that carries a message, and where the usage sits inside it - is decided by the
    caller, so the comparison still tests the extraction.
    """
    if not usage:
        return
    u["turns"] += 1
    u["input"] += usage.get("input", 0)
    u["output"] += usage.get("output", 0)
    u["cacheRead"] += usage.get("cacheRead", 0)
    u["cost"] += (usage.get("cost") or {}).get("total", 0.0)


def _usage_sum(evts, event_type: str, usage_of) -> dict:
    """`_add_usage` over the events of one type."""
    u = _blank_usage()
    for event in evts:
        if event.get("type") == event_type:
            _add_usage(u, usage_of(event))
    return u


@dataclass
class Reading:
    """What one run's stream is worth: the numbers, the answer, the first failure.

    Everything anybody derived from a `pi --mode json` stream, and the reason the
    stream itself never has to be held: these three are bounded where it is not.
    `response` is one assistant message, so its ceiling is the provider's output
    limit rather than the length of the run.
    """

    usage: dict
    response: str
    error: str


def read(evts) -> Reading:
    """Everything a stream says, in one forward pass over it.

    One pass because the stream is the one thing here with no bound: it was walked
    four times, and each walk needed it whole. The three rules are independent -
    a sum, a last, a first - so folding them together answers what the separate
    walks answered.

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
    usage = _blank_usage()
    usage["retries"] = 0
    response, error = "", ""
    for event in evts:
        kind_ = event.get("type")
        if kind_ == "auto_retry_start":
            usage["retries"] += 1
        elif kind_ == "message_end":
            message = event.get("message") or {}
            _add_usage(usage, message.get("usage"))
            response = _assistant_text(message) or response
        if not error:
            error = _error_of(event)
    return Reading(usage=usage, response=response, error=error)


def read_file(path: Path) -> Reading:
    """The same, over a trace on disk, holding one line at a time.

    Read in binary and cut on `b"\\n"` alone, because text mode applies universal
    newlines and would cut on a bare `\\r` too - which a provider writes inside an
    error message, turning one decodable event into two fragments that decode as
    nothing. `errors="replace"` for the same reason the session readers use it: one
    bad byte must not cost the whole file.

    `readline` is bounded, and that bound is the point rather than a precaution.
    Nothing in the format promises a newline, and iterating a file whose writer
    emitted none rebuilds in memory exactly the object this reading exists to avoid.
    A line longer than the limit is not an event, whatever else it is, so it and
    what trails it up to the next newline are dropped.

    An absent file reads as an empty stream: a spawn that failed wrote nothing, and
    that is silence rather than an error to raise here.
    """
    if not path.is_file():
        return read(())
    with path.open("rb") as handle:
        return read(decoded(_lines(handle)))


def _lines(handle):
    """Every line of at most `LINE_LIMIT` bytes, decoded, longer ones dropped whole.

    A chunk that neither ends in a newline nor stopped short of the limit is the head
    of a line too long to be an event; it and everything up to the next newline go.
    """
    dropping = False
    while chunk := handle.readline(LINE_LIMIT):
        ended = chunk.endswith(b"\n")
        over = not ended and len(chunk) >= LINE_LIMIT
        if not dropping and not over:
            yield chunk.decode("utf-8", "replace")
        dropping = over or (dropping and not ended)


def strip_session(session: str) -> dict:
    """Same numbers, from an archived session file instead of a live stream.

    A session has a different shape from the stream: its line types are
    `session`, `model_change`, `thinking_level_change` and `message`, and usage
    is nested under `message.usage`. That shape difference is what is passed to
    `_usage_sum`, so comparing the two paths still tests the extraction - what
    it no longer tests is the addition, which is now written once.

    `retries` is **not** recoverable here: it derives from `auto_retry_start`, a
    stream event that a session does not contain. Reported as None so a caller
    cannot mistake it for zero.
    """
    u = _usage_sum(events(session), "message", lambda e: (e.get("message") or e).get("usage"))
    u["retries"] = None
    return u


def thinking_levels(session: str) -> list[str]:
    """Every thinking level the session recorded.

    Used by the smoke pass to check that the level a cell declared is the level
    that actually ran. The defect that made the thinking cell identical to the
    baseline in every published matrix cannot survive this check.
    """
    levels = []
    for event in events(session):
        if event.get("type") == "thinking_level_change":
            levels.append(event.get("level") or event.get("thinkingLevel"))
    return levels


def models(session: str) -> list[str]:
    """Every model the session recorded, in order.

    A scenario declares `model` as a **pattern**, which the agent resolves against
    the models the provider actually offers: `gemma-4` ran as `gemma-4-31b`. So the
    declared value names an intention, and only the session names what answered.

    The same reason `etalon_commit` is archived beside `etalon`: a name and what that
    name resolved to are two different facts, and an archive keeping only the name
    cannot say what it measured.
    """
    seen = []
    for event in events(session):
        if event.get("type") == "model_change":
            model = event.get("model") or event.get("modelId")
            if model:
                seen.append(model)
    return seen


def strip(stream: str) -> dict:
    """The numbers alone, from a stream held in memory."""
    return read(events(stream)).usage


def final_text(stream: str) -> str:
    """The agent's last piece of prose, from a stream held in memory."""
    return read(events(stream)).response


def _assistant_text(message: dict) -> str:
    """The prose one assistant message stood behind, empty when it carried none.

    Tool calls and reasoning are deliberately excluded - a judge asked "is this
    impact note usable" must score the note, not the work that produced it.
    """
    if message.get("role") != "assistant":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content if content.strip() else ""
    if not isinstance(content, list):
        return ""
    pieces = [
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    joined = "\n".join(p for p in pieces if p.strip())
    return joined if joined.strip() else ""


def _error_of(event: dict) -> str:
    """The failure one event reports, as one readable line, empty when it reports none.

    Collapsed here rather than at the call sites: a provider writes its message with
    whatever newline it likes, and this exists to be printed beside a run.
    """
    for key in ("errorMessage", "error", "finalError"):
        if event.get(key):
            return one_line(str(event[key]))
    message = event.get("message") or {}
    if isinstance(message, dict) and message.get("errorMessage"):
        return one_line(str(message["errorMessage"]))
    return ""


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


def merge(
    results: list[tuple[str, dict]], declared: tuple[str, ...]
) -> tuple[dict, dict, str, str]:
    """Combines what the validators returned into one measurement line.

    `results` is [(validator mode, parsed JSON), ...]. A validator that failed
    passes None as its payload.

    Returns (metrics, reasons, state, detail). Every declared metric must be
    present; anything extra is kept but cannot be scored, which is what lets a
    general-purpose validator be reused across scenarios and lets a metric
    already paid for be scored later without remeasuring.

    A validator may also name a metric under `unjudged`, meaning it could not judge
    **that one** while the rest of the run is fine. The name counts as returned but no
    value is recorded, so `rate` drops it from the denominator - which it always knew
    how to do, "out of how many could say" - and the reason is kept so the hole is
    readable. Recording `false` instead would file "could not judge" as "worked badly",
    which is the one confusion this whole module is built against.

    That the name is *returned* rather than simply omitted is what keeps the net tight:
    a typo produces a genuinely absent key, so it is still an invalid run.
    """
    metrics: dict = {}
    reasons: dict = {}
    answered: set[str] = set()

    for mode, payload in results:
        if payload is None:
            return metrics, reasons, VALIDATOR_FAILED, f"validator {mode!r} failed"
        if not isinstance(payload, dict):
            return metrics, reasons, VALIDATOR_FAILED, f"validator {mode!r} returned no object"
        got = payload.get("metrics")
        if not isinstance(got, dict):
            return metrics, reasons, VALIDATOR_FAILED, f"validator {mode!r} returned no metrics"
        metrics.update(got)
        answered.update(got)
        reasons.update(payload.get("reasons") or {})

        unjudged = payload.get("unjudged") or {}
        if isinstance(unjudged, dict):
            reasons.update(unjudged)
            answered.update(unjudged)

    missing = [m for m in declared if m not in answered]
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
