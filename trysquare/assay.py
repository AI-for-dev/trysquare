"""The base a validator is written with.

An *assay* is an analysis performed on a sample, which is exactly the trade: one
finished run goes in, named metrics come out. The three obvious names were taken -
`validation` is the harness *running* validators, `measure` and `verdict` own the
aggregation - and the distinction is worth keeping: that module is the caller, this
one is what the callee is written with.

Four names, and an author needs no others::

    from trysquare.assay import validator, Assay, Metric, CannotJudge

    @validator
    def evaluate(run: Assay) -> dict:
        outside = run.touched - {"game/neon.js"}
        return {
            "delivered": bool(run.touched),
            "in_scope": Metric(not outside, f"also touched {', '.join(outside)}"),
            "tests": run.tests(),
        }

    if __name__ == "__main__":
        raise SystemExit(evaluate.cli())

**An attribute costs nothing, a parenthesis costs something.** What the harness
already computed is an attribute (`run.touched`); what has to go and do work is a
method (`run.tests()`). The frontier between the two therefore does not have to be
remembered, it is read - and the harness pre-computes exactly what it can compute
once for every language, because a fact computed in one place cannot drift.

Three states, and each has one way to say it. Getting this wrong is the mistake the
whole project is built against, so the type makes the confusion inexpressible: a
`Metric` has no value meaning "I could not tell".

===============================  ==========================================
I judged it                      a value, or ``Metric(value, reason)``
not **this metric**              ``Metric.unjudged(why)``
not **this run**                 ``raise CannotJudge(why)``
===============================  ==========================================

The last two both end as a non-zero exit and an invalid run, because from the
harness's side there is only one failure mode. The distinction is diagnostic, for
whoever reads `validation/<mode>.stderr` six months later, and it lives in the
wording rather than in the control flow.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import repo

USAGE = "usage: <validator> <context.json>"

# The agent's tool vocabulary, as **observed**: these seven names are every tool that
# appears across 135 archived sessions. The list `issue1.py:125` carries has seven
# entries too, of which five - `create`, `multiedit`, `patch`, `str_replace`,
# `apply_patch` - never occurred once. They read like another agent's vocabulary, and
# they were not harmless: they gave the impression of a coverage nothing had checked.
# Likewise `issue1.py:128` looks for `file_path` and `filePath`, neither of which ever
# appears; only `path` does.
#
# Deriving the fact from the evidence instead of from a list was tried and does not
# work: only `edit` and `subagent` results carry `details`, so the four `write` calls in
# the archives - real writes - would be missed.
WRITES = frozenset({"edit", "write"})
NEVER_WRITES = frozenset({"read", "grep", "ls"})
# Writes through a redirection, so it is examined rather than classified.
SHELL = frozenset({"bash"})
# Writes without naming a path: a subagent edits files the parent call never mentions.
# The documented hole, refused rather than guessed either way.
OPAQUE = frozenset({"subagent"})

# A shell redirection. It is a fact about the shell, identical in the stream and in the
# session, so changing which file is read does not remove the need for it.
REDIRECT = re.compile(r">>?\s*(?:\./)?([A-Za-z0-9_./-]+)")

# How long a declared suite may take. Generous, because it exists only for a suite that
# hangs - which is a defect of the work being measured, not of the harness.
TEST_TIMEOUT = 300

# Where a test runner starts its own summary. Four lines of data and one rule - from the
# marker to the end of the output - which is **not** a parser for TAP, JUnit or spec.
#
# A table of four is acceptable here where a table of four *ecosystem detections* was
# refused, and the asymmetry of consequences is the whole reason: a marker that is missing
# or wrong degrades the **reason** and nothing else, since the verdict comes from the exit
# code, whereas a wrong detection changes the **measure**. Only one of these four is
# exercised today, and the other three cost nothing to have wrong.
SUMMARY_MARKERS = (
    "short test summary info",  # pytest
    "test result:",  # cargo
    "failures:",  # cargo, the per-target list
    "failing tests:",  # node, spec and dot reporters
)

# go prints `ok <pkg>` or `FAIL <pkg>` at the start of a line and nothing before it.
GO_SUMMARY = re.compile(r"^(ok|FAIL)\s", re.M)

# Exit codes that mean nobody managed to run the suite, though they look like a failure.
# pytest documents its own (`pytest.ExitCode` is public API): 4 is a command-line misuse
# and 5 is **no test collected**. node exits 7 on `ERR_MODULE_NOT_FOUND`, which is what an
# unknown `--test-reporter` produces. None of the three is a red suite.
CANNOT_JUDGE_CODES = {
    4: "the runner refused the command line it was given",
    5: "the runner collected no test at all",
    7: "the runner could not load a module it needed",
}

# Said on stderr by npm when the script does not exist. It exits 1, exactly like a failing
# suite, and it means nothing ran.
NO_SUCH_SCRIPT = "Missing script"

# How much output to hand back when no marker is found. Generous on purpose: node and go
# finish on stack traces, so three lines or twenty would cut the answer in half.
FALLBACK_LINES = 60


def _unjudgeable(code: int, output: str) -> str:
    """Why this exit code is not a verdict on the agent, or an empty string."""
    if NO_SUCH_SCRIPT in output:
        return "the declared suite does not exist"
    return CANNOT_JUDGE_CODES.get(code, "")


def summarise(output: str) -> str:
    """The runner's own summary, or a tail that says it is a fallback.

    Anchored on the marker and taken to the end, because every one of the four runners
    writes its summary last. Nothing is parsed.

    A fallback that does not **declare itself** a fallback is what let a real regression
    live: the shipped validator grepped for `not ok`, Node v23 changed its default non-TTY
    reporter from `tap` to `spec`, the grep stopped matching, and a silent `tail[-1]`
    returned a closing brace as the reason for a failing suite. Nothing looked broken for
    two major versions.
    """
    lines = output.rstrip().split("\n")

    for marker in SUMMARY_MARKERS:
        for i, line in enumerate(lines):
            if marker in line:
                return "\n".join(lines[i:]).strip()

    found = GO_SUMMARY.search(output)
    if found:
        return output[found.start() :].strip()

    tail = "\n".join(lines[-FALLBACK_LINES:]).strip()
    return f"no recognised summary, last lines follow:\n{tail}"


def _tool_calls(session: str):
    """Every `toolCall` of a session, paired with the failure its result recorded.

    Paired rather than reconciled: the `toolResult` message carries `toolCallId`,
    `toolName` and `isError` together, so one pass over the lines is enough.
    """
    calls: list[ToolCall] = []
    order: list[str] = []
    failures: dict[str, bool] = {}

    for line in session.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message":
            continue
        message = event.get("message") or {}

        if message.get("role") == "toolResult":
            failures[message.get("toolCallId")] = bool(message.get("isError"))
            continue

        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "toolCall":
                order.append(block.get("id"))
                calls.append(
                    ToolCall(
                        name=block.get("name") or "",
                        arguments=block.get("arguments") or {},
                        failed=False,
                    )
                )

    return [
        ToolCall(c.name, c.arguments, failures.get(call_id, False))
        for c, call_id in zip(calls, order)
    ]


class CannotJudge(Exception):
    """This run cannot be scored, which is not the same as scoring it badly.

    Raised by a validator, and by the base whenever it is asked for something the
    context does not carry. It exits non-zero with a sentence and no traceback: it is
    not a defect, so a trace would only invite reading it as one.
    """


@dataclass(frozen=True)
class Metric:
    """A value and, when it helps, the reason it came out that way.

    Two facts forced this shape rather than a subclass of `int` or `bool`. `bool` is
    **final** in Python, so a value that is both a boolean and carries a reason cannot
    exist - and the boolean is the common case. And metrics cross a JSON boundary, so
    `measure.kind` only ever sees a dehydrated value: an envelope cannot break the
    aggregation because it never reaches it. The entry point unwraps before writing.

    A reason is published whether the value reads as a success or a failure. Filtering
    on failure is not implementable honestly: "failed" is only definable for a boolean,
    and `cited_paths = 7` is neither a success nor a failure - its verdict comes from
    the gap table, not from here. A base that filtered would be wrong about every
    median.
    """

    value: object = None
    reason: str = ""
    judged: bool = True

    def __post_init__(self) -> None:
        if not self.judged and not self.reason:
            raise ValueError(
                "an unjudged metric needs a reason: a denominator that shrank for no "
                "stated reason cannot be read six months later"
            )

    @classmethod
    def unjudged(cls, reason: str) -> Metric:
        """This one metric could not be judged; the rest of the run is fine.

        The name is still returned, which is what keeps the harness's net tight: a
        **typo** in a metric name still produces a genuinely absent key and therefore
        an invalid run, loudly, while an honest "I cannot say" shrinks a denominator
        instead - visibly, since a rate renders as `7/8`.

        The case this exists for: a probe that could not run. `issue1.py:310-316`
        returns `{"ok": False, "erreur": "no game/ in the clone"}`, which the harness
        records as `par_face = false`. That is "could not judge" filed as "worked
        badly", on the metric carrying the verdict.
        """
        return cls(None, reason, judged=False)

    def __bool__(self) -> bool:
        return bool(self.value)


def plain(value):
    """A metric value reduced to what JSON should carry.

    A set becomes a **sorted** list, and the sort is a requirement rather than a
    tidiness. `PYTHONHASHSEED` is random by default, so an unsorted set of strings
    serialises in a different order from one process to the next: two identical
    measurements would produce byte-different `measures.json`, `git diff` would show
    churn that means nothing, and `compare` and `parity` would read a difference that
    is not there.
    """
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


def report(returned: dict) -> dict:
    """Splits what a validator returned into the payload the harness reads.

    `unjudged` is a third key beside `metrics` and `reasons`, and it is an addition to
    the contract rather than a change: a validator that never uses it produces exactly
    what it produced before. The harness moves those names out of the aggregation while
    still counting them as returned.
    """
    metrics: dict = {}
    reasons: dict = {}
    unjudged: dict = {}

    for name, given in returned.items():
        if isinstance(given, Metric):
            if not given.judged:
                unjudged[name] = given.reason
                continue
            value, reason = given.value, given.reason
        else:
            value, reason = given, ""
        metrics[name] = plain(value)
        if reason:
            reasons[name] = reason

    return {"metrics": metrics, "reasons": reasons, "unjudged": unjudged}


@dataclass(frozen=True)
class ToolCall:
    """One call the agent made, as the archived session recorded it."""

    name: str
    arguments: dict
    failed: bool

    def wrote(self, path: str) -> bool:
        """Did this call write to `path`?

        A failed call never counts. `pi` rejected two `edit` calls carrying no `path` on
        a real run, and counting those would date the work before it happened.

        The tool vocabulary ages with the agent and not with trysquare, so it ages
        **loudly**: a name this does not classify raises rather than answering "no". A
        new writing tool would otherwise make a process metric quietly false, and a
        column that dropped would read as a less disciplined agent - a wrong conclusion
        rather than a visible hole.
        """
        if self.failed:
            return False
        if self.name in NEVER_WRITES:
            return False
        if self.name in WRITES:
            named = self.arguments.get("path")
            return isinstance(named, str) and named.lstrip("./") == path.lstrip("./")
        if self.name in SHELL:
            command = self.arguments.get("command")
            if not isinstance(command, str):
                return False
            return any(
                found.lstrip("./") == path.lstrip("./") for found in REDIRECT.findall(command)
            )
        if self.name in OPAQUE:
            raise CannotJudge(
                f"the run delegated to a {self.name!r}, which can write without naming a "
                f"path, so the base cannot say whether {path} was written. A metric of "
                f"process and a scenario with subagents do not currently mix"
            )
        raise CannotJudge(
            f"unknown tool {self.name!r}: the base cannot say whether it writes. The "
            f"tool vocabulary ages with the agent, not with trysquare, so it refuses "
            f"rather than under-counting. Classify it in `trysquare.assay`"
        )


class Assay:
    """One finished run, and everything a validator may ask about it.

    Every attribute and every method goes through `_part`, which is the single seam the
    fake replaces. That is the whole reason for the indirection: a fake that overrode
    one accessor at a time would drift from the real one silently.
    """

    def __init__(self, context: dict):
        self._context = context

    # --- the one seam ----------------------------------------------------

    def _part(self, name: str):
        compute = getattr(self, f"_compute_{name}", None)
        if compute is None:
            raise CannotJudge(
                f"this version of the base cannot produce {name!r}, so nothing about "
                f"this run can be scored on it"
            )
        return compute()

    def _call(self, name: str, *args):
        """A part that takes arguments, whether it is computed or stubbed.

        The real computation is a closure, so it is called. A fake's stub is the answer
        itself - `Assay.fake(tests=Metric(False, "1 failure"))` - because that is what a
        test wants to write, and making the author wrap it in a lambda would be the base
        winning an argument against its own ergonomics.
        """
        part = self._part(name)
        return part(*args) if callable(part) else part

    def _given(self, key: str, what: str):
        """A value the harness pre-computed, or a refusal naming what is missing.

        An absent key is never read as an empty value. An empty set means the agent
        touched nothing, which is a measurement; a missing key means nobody measured,
        which is not.
        """
        if key not in self._context:
            raise CannotJudge(
                f"the context carries no {key!r}, so {what} cannot be read. A harness "
                f"older than this validator writes a context without it"
            )
        return self._context[key]

    # --- what the harness already computed, so an attribute --------------

    @property
    def repo(self) -> Path:
        """The clone that was measured, with the agent's work in it."""
        return Path(self._part("repo"))

    def _compute_repo(self):
        return self._given("repo", "the measured clone")

    @property
    def etalon(self) -> str:
        """The tag the clone started from."""
        return self._part("etalon")

    def _compute_etalon(self):
        return self._given("etalon", "the etalon")["tag"]

    @property
    def touched(self) -> frozenset[str]:
        """The files the agent changed, new files included.

        Computed by the harness, once, with `git add -A --intent-to-add` followed by
        `git diff --name-only`. Two shipped validators each reimplemented it and each
        copied the same comment; `repo.diff` had held the same knowledge all along.
        """
        return frozenset(self._part("touched"))

    def _compute_touched(self):
        return self._given("touched", "the files the agent changed")

    @property
    def files_at_etalon(self) -> tuple[str, ...]:
        """Every path in the pinned tree, unfiltered.

        The list is cheap - one `git ls-tree` - and depends on nothing but the run, so
        the harness computes it. Reading their *contents* depends on a filter that
        belongs to the question, so that stayed a method.
        """
        return tuple(self._part("files_at_etalon"))

    def _compute_files_at_etalon(self):
        return self._given("files", "the files at the etalon")

    @property
    def response(self) -> str:
        """The agent's final prose, extracted once by the harness.

        A validator that parsed the stream itself would be one more validator getting
        it slightly differently.
        """
        return self._part("response")

    def _compute_response(self):
        return Path(self._given("response", "the agent's final prose")).read_text(
            errors="replace"
        )

    @property
    def prompt(self) -> str:
        return self._part("prompt")

    def _compute_prompt(self):
        return Path(self._given("prompt", "the prompt")).read_text(errors="replace")

    @property
    def declared(self) -> tuple[str, ...]:
        """The metric names the scenario contracted for, when the harness says."""
        return tuple(self._context.get("declared", ()))

    # --- what costs something, so a method -------------------------------

    def tests(self, timeout: int = TEST_TIMEOUT) -> Metric:
        """Runs the suite the scenario **declared**, and reports the runner's own summary.

        Declared and never detected. The obvious detection is `npm test`, whose meaning is
        read from `package.json` - a file inside the perimeter the measured agent may edit,
        so broken code plus a test script of `echo ok` scores green. A detected command
        hands the choice of how a run is measured to the agent being measured, and no
        comparable tool has that adversary. Every documented migration elsewhere runs from
        detecting towards declaring and none the other way.

        **Three outcomes, not two.** Green, red, and could-not-judge - the last covering an
        executable that is not there, a timeout, `npm error Missing script`, pytest's exit 4
        and 5, and node's exit 7. Three distinct causes all exit 1, and calling any of them
        a failing suite would be scoring an agent for something it did not do.

        No report format is parsed. One generic mechanism instead: find the summary the
        runner already wrote and hand it back, anchored on its marker and taken to the end.
        A table of four markers is acceptable where a table of four *detections* was not,
        and the asymmetry is the reason - a wrong marker degrades the **reason** while a
        wrong detection changes the **measure**.
        """
        return self._call("tests", timeout)

    def _compute_tests(self):
        command = self._given("test_command", "the declared test suite")
        directory = self.repo

        def run(timeout: int) -> Metric:
            try:
                done = subprocess.run(
                    list(command),
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as e:
                raise CannotJudge(
                    f"the declared suite timed out after {timeout}s: "
                    f"{' '.join(command)}"
                ) from e
            except OSError as e:
                raise CannotJudge(
                    f"the declared suite could not run: {e}. The scenario names "
                    f"{' '.join(command)}"
                ) from e

            # Both streams, for **reading** only. go guarantees its report is on stdout
            # even when a test wrote to stderr, and npm writes a notice to stderr on a
            # perfectly green run - so the presence of stderr is not evidence of anything.
            # Treating it as evidence is what put `npm notice run node --test` in a reason.
            output = done.stdout + done.stderr

            unjudgeable = _unjudgeable(done.returncode, output)
            if unjudgeable:
                raise CannotJudge(f"{unjudgeable}: {' '.join(command)}")
            if done.returncode == 0:
                return Metric(True)
            return Metric(False, summarise(output))

        return run

    def sources_at_etalon(self, pattern: str, exclude: str | None = None) -> str:
        """The text of the pinned files a pattern selects, joined.

        Always from the **tag**, never from a working tree. `neon.py:88-91` falls back
        to the checkout's working tree when the harness provides one, and
        `issue1.py:183-195` documents why that is wrong: trysquare puts the *source
        repository* there, whose working tree is on `main`, so the reference drifts the
        moment `main` moves or a classroom fixes the issue in place - which is exactly
        what pinning by tag exists to prevent. Only the correct one is offered here.

        The clone cannot serve either: it is made with `--no-tags`, so the tag does not
        exist in it. The source repository is the only thing that carries it.

        Patterns are matched per path component, so `game/*.js` does not reach into
        `game/sub/`. The listing is not read again - `files_at_etalon` already holds it -
        so this costs one `git show` per selected file and nothing else.

        The reading itself is `repo.etalon_file`, which the harness has had all along.
        Three shipped validators reimplemented it with a raw `subprocess`.
        """
        return self._call("sources_at_etalon", pattern, exclude)

    def _compute_sources_at_etalon(self):
        etalon = self._given("etalon", "the etalon")
        source = Path(etalon.get("checkout") or "")
        tag = etalon["tag"]
        if not (source / ".git").exists():
            raise CannotJudge(
                f"no source repository in the context ({source}), so the etalon cannot "
                f"be read. Its tag lives there and not in the clone, which is made "
                f"with --no-tags"
            )
        listing = self.files_at_etalon

        def read(pattern: str, exclude: str | None) -> str:
            wanted = sorted(
                name
                for name in listing
                if PurePosixPath(name).match(pattern)
                and not (exclude and PurePosixPath(name).match(exclude))
            )
            return "\n".join(repo.etalon_file(source, tag, name) for name in wanted)

        return read

    def tool_calls(self) -> tuple[ToolCall, ...]:
        """Every tool call of the run, in order, from the **archived session**.

        `issue1.py:386-450` reads `context["trace"]`, the raw stream, which is
        deliberately not archived (`outputs.py:24-27`: five hundred times the size,
        nothing the per-message record does not say). The calls were in the session all
        along, and in a better shape: one `toolResult` record carries the tool name, the
        id and `isError` together, so the `toolCallId` reconciliation that file needed
        had no cause but reading the wrong file.

        One consequence worth stating: a metric of *process* is therefore replayable,
        because the session outlives the work directory. The only figure a session
        cannot give back is `retries` (`measure.py:102-104`).
        """
        return self._part("tool_calls")

    def _compute_tool_calls(self):
        directory = Path(self._given("session", "the agent's tool calls"))
        files = sorted(directory.glob("*.jsonl")) if directory.is_dir() else []
        if not files:
            raise CannotJudge(
                f"no archived session under {directory}, so nothing about the agent's "
                f"process can be read. The session comes from the harness, so its "
                f"absence says nothing about the agent"
            )
        return tuple(_tool_calls("\n".join(p.read_text(errors="replace") for p in files)))

    def first_write(self, path: str) -> int | None:
        """The index of the first call that wrote to `path`, or None.

        The index rather than the call, because what a process metric asks is an
        ordering question: did the first write to the test file come before the first
        write to the source, with a failing suite between them. Two indices answer that;
        a list of calls makes every caller rediscover how.

        Refuses rather than answering "no" when a call might have written and the base
        cannot tell - an unknown tool, or a subagent. See `WRITES`.
        """
        for i, call in enumerate(self.tool_calls()):
            if call.wrote(path):
                return i
        return None

    # --- the fake --------------------------------------------------------

    @classmethod
    def fake(cls, **parts) -> Assay:
        """An `Assay` for a test, answering only what the test declared.

        Asking for anything else **raises**, and that is the point rather than a
        limitation. A fake that answered would put the absence of a measurement into
        the shape of a measurement - an empty set reads as "the agent touched nothing" -
        which is the confusion the error contract exists to prevent, moved into the
        tests. And a test that declares what it reads documents the dependency: a
        validator that grows a new read makes its old tests fail, loudly, which is
        information.
        """
        return _Fake(parts)


class _Fake(Assay):
    def __init__(self, parts: dict):
        super().__init__({})
        self._parts = parts

    def _part(self, name: str):
        if name in self._parts:
            return self._parts[name]
        given = ", ".join(sorted(self._parts)) or "nothing"
        raise CannotJudge(
            f"this is a fake Assay and it was not given {name!r} (it has: {given}). "
            f"Declare it: Assay.fake({name}=...)"
        )


# --- the entry point -------------------------------------------------------


def validator(fn):
    """Makes a plain function the whole of a validator.

    `fn` stays callable, which is the point: a test scores a run with one call instead
    of a subprocess. `test_issue1.py:40-54` had to `ast.parse` its own validator to
    list the metrics it produces, because calling it "would want a clone, a source
    repository carrying the tag and a trace" - that is what this replaces.

    The `if __name__` tail stays explicit rather than running at decoration time. A
    module that scored a run merely by being imported could not be imported by a test.
    """
    fn.cli = lambda argv=None: main(fn, argv)
    return fn


def main(fn, argv=None, write=None, warn=None) -> int:
    """The contract, applied once here instead of in every validator.

    Four validators each wrote this, and one in four got the error contract right.
    `neon.py:174` calls `evaluate(context)` with no net at all, so a traceback goes to
    `script.stderr` and reads six months later as a broken validator when the cause is
    usually the context.

    Everything is caught, because the default has to be the right one. The sentence
    comes **first** and the traceback after it: the trace costs a real debugging
    session to throw away, and `run_script` captures stderr separately for exactly
    that. The fix for "a traceback reads as a broken validator" is the order, not the
    suppression.
    """
    write = write or sys.stdout.write
    warn = warn or sys.stderr.write
    argv = list(sys.argv[1:] if argv is None else argv)

    if len(argv) != 1:
        warn(USAGE + "\n")
        return 2
    try:
        context = json.loads(Path(argv[0]).read_text())
    except (OSError, json.JSONDecodeError) as e:
        warn(f"unreadable context: {e}\n")
        return 2

    run = Assay(context)
    try:
        payload = report(fn(run))
        missing = [
            name
            for name in run.declared
            if name not in payload["metrics"] and name not in payload["unjudged"]
        ]
        if missing:
            raise CannotJudge(
                f"this scenario declares {', '.join(run.declared)} and nothing was "
                f"returned for {', '.join(missing)}. A declared metric that is absent "
                f"makes the run invalid, so it is refused here rather than recorded"
            )
    except CannotJudge as e:
        warn(f"could not score this run: {e}\n")
        return 1
    except Exception as e:  # noqa: BLE001 - a validator must not report a traceback as a score
        warn(f"the validator failed: {type(e).__name__}: {e}\n\n")
        warn(traceback.format_exc())
        return 1

    write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0
