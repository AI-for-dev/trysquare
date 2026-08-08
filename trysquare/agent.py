# SPDX-License-Identifier: BSD-3-Clause
"""Building the agent invocation, and running it.

The argument list is the experiment. Everything here is explicit on purpose:
discovery is switched off wholesale and every brick is then handed back by path.
Discovery is gated on project trust, it walks up ancestor directories, and it
fails silently - three ways for a cell to measure the absence of the brick it
believes it is measuring.

The stripping of the output stream lives in `measure.py`, because it is a pure
function from text to numbers and belongs where it can be tested without a
subprocess.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import interrupt
from .measure import consumed_tokens, read_file

PI = "pi"


def resolves_to(declared: str, ran: str) -> bool:
    """Whether the model that ran is the one the scenario's pattern asked for.

    `--model` takes a **pattern**, not an id: a scenario declaring `gemma-4` ran as
    `gemma-4-31b`, which is resolution and not substitution. So an equality check
    would refuse every legitimate run, and no check at all would let a fallback to
    the machine's `defaultModel` pass unseen. What is verified is the weaker,
    checkable property: the declared pattern must still be *in* what answered.

    Both sides may carry a `provider/` prefix, and a declared pattern may carry the
    `:<thinking>` shorthand the agent also accepts; neither says anything about
    which model ran, so both are stripped before comparing.
    """
    return _bare(declared) in _bare(ran)


def _bare(model: str) -> str:
    """A model name reduced to what identifies the model itself."""
    return model.rsplit("/", 1)[-1].split(":", 1)[0].strip().lower()


@dataclass
class Outcome:
    """One invocation of the agent, whatever happened to it.

    The stream itself is not here, and that absence is the point. It is the one thing
    the harness handles whose size nobody controls, and holding it is how a single
    runaway run reached 136 GB and had the matrix killed under it. What is kept is
    everything that was ever derived from it - the numbers, the answer, the first
    failure - each bounded by one message rather than by the length of the run. The
    stream stays where it was written, and `trace` says where.
    """

    trace: Path
    response: str
    error: str
    stderr: str
    code: int | None
    duration: int
    timed_out: bool
    usage: dict

    @property
    def produced_something(self) -> bool:
        return consumed_tokens(self.usage)

    @property
    def signalled(self) -> bool:
        """Whether something outside the run ended it.

        A negative status is the signal that killed the child. It is not a result the
        agent produced, and it is not the agent's silence either - which matters
        because those two are told apart by the same emptiness. The OOM killer is the
        case that shows the difference: it used to buy three agent runs in a row, each
        one killed for the same reason as the last.
        """
        return self.code is not None and self.code < 0


def argv(
    prompt: str,
    provider: str,
    model: str,
    thinking: str,
    session_dir: Path,
    extensions: list[Path] | None = None,
    skills: list[Path] | None = None,
    has_context: bool = False,
) -> list[str]:
    """The exact argument list for one run.

    - `-a` is unconditional. In non-interactive mode there is no trust prompt, so
      without a saved decision every `.pi/` resource in a fresh clone is ignored
      **silently**. A fresh clone never has a saved decision. Passing it is a
      validity condition, not a comfort.
    - `-ns -np -ne` switch off skill, prompt-template and extension discovery;
      explicit `--skill` and `-e` paths still load, and they are the only way a
      brick enters.
    - `-nc` unless the cell provides a context file. There is no `--context-file`
      in the agent, so an AGENTS.md can only be obtained by writing it into the
      clone and letting discovery run. Discovery walks up ancestors, so cells
      without context are protected from inheritance and cells with context are
      not. That asymmetry is known and declared rather than hidden.
    - `--thinking` always, never inherited.
    """
    args = [
        "-p",
        "--mode",
        "json",
        "--provider",
        provider,
        "--model",
        model,
        "--session-dir",
        str(session_dir),
        "-a",
        "-ns",
        "-np",
        "-ne",
        "--thinking",
        thinking,
    ]
    if not has_context:
        args.append("-nc")
    for path in extensions or []:
        args += ["-e", str(path)]
    for path in skills or []:
        args += ["--skill", str(path)]
    args.append(prompt)
    return args


def run(cwd: Path, args: list[str], timeout: int, trace: Path) -> Outcome:
    """Runs the agent once, straight into `trace`, and reads back what it says.

    The agent writes to the file itself, so nothing here ever holds the stream. That
    is the whole difference: the file was written at the end anyway, and the copy
    that lived in this process on the way there had no bound.

    `stdin` must be closed. With an open pipe, the agent waits indefinitely for
    something to read and the run freezes without emitting a byte.
    """
    start = time.monotonic()
    trace.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Inside the `try`, so the file is closed on every path before it is read
        # back. Truncating, so an attempt reads its own stream and not the tail of
        # the attempt it is replacing.
        with trace.open("wb") as sink:
            proc = interrupt.run(
                [PI, *args],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        stderr, code, timed_out = proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired:
        stderr, code, timed_out = f"timed out after {timeout}s", None, True
    except OSError as e:
        stderr, code, timed_out = str(e), -1, False

    found = read_file(trace)
    return Outcome(
        trace=trace,
        response=found.response,
        error=found.error,
        stderr=stderr,
        code=code,
        duration=round(time.monotonic() - start),
        timed_out=timed_out,
        usage=found.usage,
    )


def run_until_productive(
    cwd: Path, args: list[str], timeout: int, attempts: int, trace: Path
) -> tuple[Outcome, int]:
    """Retries only while nothing has been produced.

    A run that consumed no tokens produced no result, so there is nothing to
    select between: retrying it is not optional stopping. A run that *did* produce
    something is never retried, whatever its result.

    A run something else ended is not retried either. It looks identical to silence
    from here - no tokens, nothing to select between - and the loop used to answer it
    by launching a fresh agent, which is how one Ctrl-C bought three more.

    Every attempt writes the same `trace`, so it holds the attempt that was kept. The
    sessions are what say what the others did: they are archived one file per attempt.
    """
    outcome = None
    for attempt in range(1, attempts + 1):
        outcome = run(cwd, args, timeout, trace)
        if outcome.produced_something or outcome.signalled:
            return outcome, attempt
    return outcome, attempts


def export_html(session: Path, target: Path, timeout: int = 120) -> Path:
    """Renders one archived session as a standalone page, by the agent itself.

    The agent already knows how to read its own sessions, so nothing here reimplements
    that: a renderer written here would drift from the format it renders, silently, and
    the format is the agent's rather than ours.

    `pi --export` takes no output path and writes `pi-session-<stem>.html` into the
    current directory, so the target directory *is* the working directory. The file is
    then renamed to `<stem>.html`, which puts the page beside the jsonl it came from under
    the same stem - the archive stays readable by looking at it.

    `--offline` because an export reads a file. A startup network call would make
    re-rendering an archive depend on the network being up, which is the opposite of what
    an archive is for.

    Raises `RuntimeError` on anything that went wrong, so a caller has one exception to
    catch and one session's failure need not cost the others.
    """
    target.mkdir(parents=True, exist_ok=True)
    try:
        proc = interrupt.run(
            [PI, "--offline", "--export", str(session.resolve())],
            cwd=target,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"timed out after {timeout}s") from e
    except OSError as e:
        raise RuntimeError(str(e)) from e
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:300] or f"exit {proc.returncode}")

    produced = target / f"pi-session-{session.stem}.html"
    if not produced.is_file():
        raise RuntimeError(f"reported success but wrote no {produced.name}")
    destination = target / f"{session.stem}.html"
    produced.replace(destination)
    return destination


def ambient_thinking(settings: Path | None = None) -> str | None:
    """The thinking level a subagent will actually run at.

    A subagent's level cannot be declared: the frontmatter has no field for it and
    the library passes no option, so it always comes from the operator's settings.
    Read here so the harness can check a scenario against it and refuse rather
    than produce a matrix whose cells claim one level and ran another.
    """
    settings = settings or Path.home() / ".pi" / "agent" / "settings.json"
    if not settings.is_file():
        return None
    try:
        return json.loads(settings.read_text()).get("defaultThinkingLevel")
    except (json.JSONDecodeError, OSError):
        return None


def available() -> bool:
    """Whether the agent binary is on PATH, for a clear message rather than a trace."""
    from shutil import which

    return which(PI) is not None
