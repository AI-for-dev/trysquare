"""Running the validators, and keeping them honest.

Validators are **independent**: each receives the same context and cannot see
what the others found. That is not tidiness. A judge told the script's verdict is
anchored on it, and its agreement stops being an independent signal - which was
the only reason to have a judge at all.

A judge is also **blind**: its context carries no cell name and no configuration.
Blinding is not always achievable, though, and pretending otherwise would be
worse than saying so: when the treatment *is* the prompt, handing the judge the
prompt reveals the cell. So the harness reports which declared pieces vary across
cells, and the operator knows how blind the judge really is.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .scenario import Scenario, Validator

# Everything a script validator is told, and nothing more. Handed as one file
# whose path is the single argument, because the file is archived with the run:
# that is what makes a validation replayable by hand months later, which is what
# makes "fix a signature and re-score runs already paid for" true.
CONTEXT_NAME = "context.json"

# Keys withheld from a judge's context, so it cannot know what it is scoring.
BLIND_KEYS = ("cell", "repetition", "configuration", "harness")


@dataclass
class Result:
    """What one validator returned, or why it did not."""

    mode: str
    payload: dict | None
    stderr: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.payload is not None


def where(path: Path | str) -> str:
    """A path as a context carries it: absolute, always.

    "Every path it needs is absolute in the context" is a documented promise, and it is
    what lets the child run somewhere that is deliberately **not** the measured clone. It
    was true by accident rather than by construction: a run's paths come from the work
    directory, which the config makes absolute, so nothing relative had ever been passed.

    `replay` passed one. Its archive directory is whatever the operator typed - `results/...`
    - so the archived session went into the context relative, the validator's child resolved
    it from its own working directory, found nothing, and reported that the run had no
    session. Which reads as a fact about the *agent*: sixty runs said "nothing about the
    agent's process can be read" and the metric of process this file's docstring calls
    replayable was unjudged on every one of them.

    Resolved here, once, so no caller can be the one that forgets.
    """
    return str(Path(path).resolve())


def write_context(
    directory: Path,
    repo: Path,
    etalon: str,
    etalon_checkout: Path,
    prompt_file: Path | None,
    session_dir: Path,
    trace: Path | None,
    cell: str,
    repetition: int,
    blind: bool = False,
    response_file: Path | None = None,
    test_command: str | None = None,
    prepare: list[str] | None = None,
    touched: list[str] | None = None,
    files: list[str] | None = None,
    declared: tuple[str, ...] = (),
) -> Path:
    """Writes the context file a validator is handed.

    `response` is the agent's final prose, extracted once by the harness. A
    validator that needed it would otherwise have to reimplement stream parsing,
    and every validator reimplementing it is every validator getting it slightly
    differently.

    `test_command` is the suite the scenario declared, carried here for that same
    reason and one more: a validator that guessed it would be reading
    `package.json`, a file inside the perimeter the measured agent may edit.

    Carried **as the scenario wrote it** - a string - rather than pre-split. One fact, one
    representation: an archived context read against the scenario file six months later says
    the same thing, with no transformation to know about. And a validator gets the shape its
    own runtime prefers, which is the opposite of what pre-splitting assumed: a shell splits
    a string for free, where a JSON array has to be parsed and rebuilt. Only Python prefers
    an argv, and a Python validator never sees this key - `run.tests()` does the splitting,
    with `scenario.split_command`, which is the same rule the loader vetted it with.

    It is **not** withheld from a blind context. It is a property of the task,
    identical in every cell, so it tells a judge nothing about which configuration
    produced the work in front of it.

    Absent when the scenario names no suite, and an absent key is a different fact
    from an empty command: it says this experiment scores no test suite, which is
    something a validator may need to refuse over rather than score.

    `touched` and `files` are the two facts every validator wanted and each computed for
    itself. `repo.changed_files`, `repo.etalon_files` and `repo.etalon_file` had held
    that knowledge all along, and three shipped validators reimplemented it with a raw
    `subprocess` regardless - one of them landing on a **different answer** for the
    reference side. Computed here once, they cannot be got slightly differently by three
    callers.

    `declared` is the metric names the scenario contracted for. The harness already
    refuses a run whose validator omitted one, but only after the tokens are spent;
    handing them over lets the base say which one is missing before anything is
    recorded. Safe for a blind context: metric names say nothing about a cell.
    """
    context = {
        "repo": where(repo),
        "etalon": {"tag": etalon, "checkout": where(etalon_checkout)},
        "session": where(session_dir),
        "cell": cell,
        "repetition": repetition,
    }
    # Absent rather than the string "None". A replay has no prompt to give back - it lived
    # in the work directory - and a key holding "None" would send a validator to open a
    # file called None instead of telling it the fact is missing.
    if prompt_file is not None:
        context["prompt"] = where(prompt_file)
    if test_command is not None:
        context["test_command"] = test_command
    # The steps to run before the suite. Kept apart from the suite itself because their
    # failures mean different things: one says nobody judged, the other is a measurement.
    if prepare:
        context["prepare"] = list(prepare)
    # Written even when empty, and that is the one place where absent and empty must not
    # be confused: an agent that changed nothing is a **result**, and a validator has to
    # be able to read it. A missing key means nobody looked.
    if touched is not None:
        context["touched"] = sorted(touched)
    if files is not None:
        context["files"] = list(files)
    if declared:
        context["declared"] = list(declared)
    if response_file is not None:
        context["response"] = where(response_file)
    if trace is not None:
        context["trace"] = where(trace)
    if blind:
        context = {k: v for k, v in context.items() if k not in BLIND_KEYS}

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONTEXT_NAME
    path.write_text(json.dumps(context, indent=2) + "\n")
    return path


def run_script(validator: Validator, context: Path, timeout: int, cwd: Path | None = None) -> Result:
    """Runs a script validator: one argument, JSON on stdout.

    The working directory is deliberately *not* the measured clone: a validator
    that wrote a stray file there would be counted as the agent's work by scope
    scoring. Every path it needs is absolute in the context file.

    Including the context file's own path, which is the whole reason it is resolved
    here. This call changes the child's working directory, so a relative path handed to
    it is measured from somewhere the caller never named - and `--output out` is the
    documented way to invoke the tool, so every script validator failed with
    `unreadable context` for want of one `resolve()`.
    """
    command = validator.config.get("command")
    if not command:
        return Result(validator.mode, None, detail="validator declares no command")

    script = Path(command)
    if not script.is_absolute() and cwd is not None:
        script = (cwd / script).resolve()
    if not script.exists():
        return Result(validator.mode, None, detail=f"validator not found: {script}")

    context = context.resolve()

    # A courtesy to Python validators, and only to them. `#!/usr/bin/env python3`
    # catches whatever `python3` is on PATH, which on macOS is 3.9, while this package
    # needs 3.11 for `tomllib`: a validator importing `trysquare.assay` would fail to
    # import, and a failed import reads as an invalid run - on somebody else's machine
    # only, which is the worst kind. Handing the file to the interpreter already running
    # the harness makes the version right by construction rather than by promise.
    #
    # "Any executable, in any language" stays literally true for everything else: no
    # other suffix is touched, and a `.py` file stays runnable by hand.
    argv = [str(script), str(context)]
    if script.suffix == ".py":
        argv.insert(0, sys.executable)

    try:
        proc = subprocess.run(
            argv,
            cwd=context.parent,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Result(validator.mode, None, detail=f"validator timed out after {timeout}s")
    except OSError as e:
        return Result(validator.mode, None, detail=f"validator could not run: {e}")

    if proc.returncode != 0:
        return Result(
            validator.mode,
            None,
            stderr=proc.stderr,
            detail=f"validator exited {proc.returncode}",
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return Result(
            validator.mode,
            None,
            stderr=proc.stderr,
            detail=f"validator returned unreadable JSON: {e}",
        )
    return Result(validator.mode, payload, stderr=proc.stderr)


JUDGE_REQUEST = "judge-request.json"
JUDGE_VERDICT = "verdict.json"
JUDGE_BRICK = "bricks/judge-tool.ts"


def judge_dossier(
    directory: Path,
    validator: Validator,
    rubric: str,
    pieces: dict[str, str],
) -> tuple[Path, str]:
    """Writes what the judge is given, and returns its working directory and prompt.

    The pieces are declared in the scenario, because what the judge is given to read
    is half of what it measures. Nothing about the cell is included: the judge must
    not know which configuration produced the work it scores.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / JUDGE_REQUEST).write_text(
        json.dumps({"metrics": list(validator.metrics), "rubric": rubric}, indent=2) + "\n"
    )
    # A stale verdict from a previous attempt must never be read as this one's.
    verdict = directory / JUDGE_VERDICT
    if verdict.exists():
        verdict.unlink()

    sections = [
        "You are scoring a piece of work against a rubric. You do not know how it was",
        "produced, and you must not speculate: score only what is in front of you.",
        "",
        "## Rubric",
        "",
        rubric.strip(),
        "",
    ]
    for name, text in pieces.items():
        sections += [f"## {name.replace('_', ' ').title()}", "", (text or "(empty)").strip(), ""]
    sections += [
        "## What to do",
        "",
        f"Call the `verdict` tool exactly once, with every metric the rubric defines: "
        f"{', '.join(validator.metrics)}.",
        "Give a short reason for each. Prose outside the tool call is discarded.",
    ]
    return directory, "\n".join(sections)


def run_judge(
    validator: Validator,
    directory: Path,
    prompt: str,
    brick: Path,
    timeout: int,
    attempts: int = 1,
) -> Result:
    """Runs the judge, and reads back the verdict its tool call recorded.

    Retried only while there is **no** usable answer, which is not optional
    stopping: an absent verdict is not a verdict one could have preferred. Once a
    verdict exists it is kept, whatever it says.
    """
    from . import agent as agent_mod

    verdict_path = directory / JUDGE_VERDICT
    detail = ""

    for attempt in range(1, max(1, attempts) + 1):
        args = agent_mod.argv(
            prompt=prompt,
            provider=validator.config.get("provider", ""),
            model=validator.config.get("model", ""),
            thinking=validator.config.get("thinking", "off"),
            session_dir=directory / "session",
            extensions=[brick],
        )
        outcome = agent_mod.run(directory, args, timeout)

        if verdict_path.is_file():
            try:
                payload = json.loads(verdict_path.read_text())
            except json.JSONDecodeError as e:
                detail = f"judge wrote an unreadable verdict: {e}"
                continue
            return Result(validator.mode, payload, stderr=outcome.stderr)

        detail = (
            f"judge did not call the verdict tool (attempt {attempt}/{attempts})"
            if outcome.produced_something
            else f"judge produced nothing (attempt {attempt}/{attempts}): "
            f"{agent_mod.first_error(outcome.stream) or outcome.stderr[:200]}"
        )

    return Result(validator.mode, None, detail=detail)


def blindness(scenario: Scenario) -> dict:
    """How blind a judge actually is in this scenario, piece by piece.

    A piece that varies between cells leaks the treatment. Reported rather than
    forbidden: a judge on a matrix of prompts stays possible, provided it is said.
    """
    judges = [v for v in scenario.validators if v.mode == "judge"]
    if not judges:
        return {}

    varying = set()
    for cell in scenario.cells:
        varying.update(cell.delta)

    report = {}
    for judge in judges:
        pieces = tuple(judge.config.get("pieces", ()))
        leaking = [p for p in pieces if p in varying]
        report[judge.mode] = {
            "pieces": pieces,
            "leaking": leaking,
            "blind": not leaking,
        }
    return report


def describe_blindness(report: dict, cells: int) -> list[str]:
    """The lines printed at launch, so the operator is never surprised."""
    lines = []
    for mode, info in report.items():
        if info["blind"]:
            lines.append(f"  {mode}: blind over {cells} cells (pieces: {', '.join(info['pieces'])})")
        else:
            lines.append(
                f"  ! {mode} is only partially blind: "
                f"{', '.join(info['leaking'])} varies between cells, "
                f"so the judge can identify the treatment"
            )
    return lines


def check_thinking_precondition(declared: str | None, ambient: str | None, uses_subagents: bool) -> str | None:
    """Refuses a scenario whose subagents would think at another level.

    A subagent's thinking level cannot be declared anywhere: the frontmatter has no
    field for it and the library passes no option. What cannot be controlled is
    verified instead, and a mismatch stops the run rather than producing cells that
    claim one level and ran another.

    Returns the refusal message, or None when there is nothing to refuse.
    """
    if not uses_subagents or declared is None or ambient is None:
        return None
    if declared == ambient:
        return None
    return (
        f"the scenario declares thinking = {declared!r}, and the machine's "
        f"defaultThinkingLevel is {ambient!r}.\n"
        f"A subagent cannot declare its thinking level, so subagents would run at "
        f"{ambient!r} while the cell claims {declared!r}.\n"
        f"Either set defaultThinkingLevel to {declared!r} in "
        f"~/.pi/agent/settings.json, or declare thinking = {ambient!r} in the "
        f"scenario so the cell says what will actually run.\n"
        f"Removing `thinking` is not an option: it is mandatory in a scenario, "
        f"because a level that is not declared is a level inherited from whoever "
        f"runs the tool."
    )
