# SPDX-License-Identifier: BSD-3-Clause
"""Preparing the repository a run measures, and injecting the harness bricks.

Two decisions here are not interchangeable with the obvious alternatives, and
both come from a trap that was actually hit.

**Clone at an etalon, never copy the working tree.** The measured state has to be
immutable and named, or yesterday's measures no longer compare with tomorrow's.
And a repository may be a *worktree*, whose `.git` is only a file pointing at a
shared gitdir: a recursive copy then gives every run the same gitdir, so one
agent running `git commit` moves the comparison base of every run in flight and
nobody notices.

**A tag is named, not immutable.** `git tag -f` moves one, and a matrix measured
before the move no longer compares with one measured after - the two report the
same etalon and measured different code. That is not hypothetical: two campaigns
of the same scenario were joined by name and turned out to have read two different
versions of the ticket under test, which `commit_of` was the only thing to record.
So an etalon may also be a **commit**, written out in full, and `is_commit` decides
which of the two it is. A commit cannot move, and the directory name then says what
was measured rather than what it was called.

**A remote is pinned as a working tree, never as a bare mirror.** A bare mirror is
smaller and answers `git show` and `git ls-tree` perfectly well, so it looks right.
But `etalon.checkout` is walked as files by a validator reading the reference state,
a bare repository passes an `is_dir()` check and yields an empty reference, and the
whole matrix then reports plausible numbers about nothing. See `pin`.

**Exclude what we injected.** The files the harness drops are not the agent's
work. Without `.git/info/exclude`, scope scoring counts them as changes the agent
made, and every configured cell drops to zero: a measurement of our own tooling
rather than of the behaviour under test.

**Commit what we gave the task.** A `files` brick is the one exception to the
line above, and the difference is who the material is for. Harness plumbing is
addressed to the agent library and belongs outside the repository's history; a
file a brick puts *in the tree* - a probe, a fixture - is addressed to the task,
and the agent may edit it or delete it. Excluded, that tampering would leave no
trace anywhere: git ignores an untracked path whether it was changed, weakened or
removed. Committed, the given state becomes the base the diff is taken against,
so the injection still costs nothing in `touched` and every later move on it is
recorded. It is also what makes a replay exact - see `inject`.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import interrupt
from .config import is_remote

GIT_TIMEOUT = 180

# An etalon written as a commit, which `--branch` cannot take and a checkout must.
#
# The full forty characters and nothing shorter. An abbreviation is not a pin either:
# it designates one object only until the repository grows the one that collides with
# it, and it would resolve here while failing on a fuller clone elsewhere. Refusing it
# also keeps the rule legible - forty hex characters is a commit, anything else is a
# ref - where a length range would leave every reader to guess the boundary.
COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")

# Git is the one child here that would rather ask than fail. It has no terminal to ask
# on - every child the harness starts is a session leader, so that it can be stopped as
# a group - and without this a missing credential is a device error rather than git's
# own sentence naming the repository it could not read. It was never usable anyway: a
# clone runs in a worker thread under `capture_output`, so any prompt it wrote was
# invisible behind the progress bar and waited for an answer nobody could see to give.
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


class RepoError(Exception):
    def __init__(self, *args, detail: str = ""):
        super().__init__(*args)
        # Git's own stderr, kept apart from the message. A wrapper that wants to explain
        # a failure needs the *reason*; splicing in the whole message buries it under a
        # command line carrying the URL and the target path all over again.
        self.detail = detail


@dataclass
class Prepared:
    """A clone ready to be measured, and what we put in it."""

    path: Path
    etalon: str
    injected: list[str] = field(default_factory=list)
    # Kept apart from `injected` because the two are not disposed of the same way:
    # what is injected is hidden from git, what is given is committed. An archive
    # that merged them would not say which of its files the agent could touch.
    given: list[str] = field(default_factory=list)
    agents: dict[str, dict] = field(default_factory=dict)


def git(args: list[str], cwd: Path | None = None, check: bool = True) -> str:
    proc = interrupt.run(
        ["git", *args],
        cwd=cwd,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if check and proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RepoError(f"git {' '.join(args)} failed: {stderr}", detail=stderr)
    return proc.stdout


def is_commit(etalon: str) -> bool:
    """Whether this etalon names a commit outright rather than a ref."""
    return bool(COMMIT.match(etalon))


def clone_argv(source: str, etalon: str, target: Path) -> list[str]:
    """The flags a pinned source is cloned with, separated so they can be asserted.

    `--single-branch --branch <etalon>` keeps the clone to the etalon's history, and the
    tags stay, because every run fetches *from* this directory **by tag name**.

    **A commit takes none of that.** `--branch` refuses anything that is not a ref, so a
    commit etalon is cloned whole and reached by the checkout `pin` runs next:
    `--single-branch` would keep only the default branch, which can leave the wanted
    commit out of a clone that otherwise looks complete.
    """
    if is_commit(etalon):
        return ["clone", "--quiet", source, str(target)]
    return ["clone", "--quiet", "--single-branch", "--branch", etalon, source, str(target)]


def fetch_argv(source: str, etalon: str) -> list[str]:
    """How a run's clone receives the etalon: that revision and its ancestors, no more.

    A fetch rather than a clone, because a clone from a local directory hardlinks its
    whole object store. The commits after the etalon then sit in the run's clone with no
    ref pointing at them, and `git cat-file --batch-all-objects` reads the fix the agent
    is asked to write. A fetch sends only what the etalon reaches, tag or commit alike.
    """
    return ["fetch", "--quiet", "--no-tags", source, etalon]


def _located(source: Path | str) -> str:
    """Where git is to read `source` from. A URL is handed over verbatim: `resolve()`
    would turn it into a path, which is the defect `config.is_remote` exists to prevent.

    The `exists()` check stays as a backstop. `runner.prepare_source` checks earlier and
    says more, but a caller that reaches here with nothing on disk should still be told,
    not left with a bare git error.
    """
    if is_remote(str(source)):
        return str(source)
    source = Path(source)
    if not source.exists():
        raise RepoError(f"repository not found: {source}")
    return str(source.resolve())


def _emptied(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)


def _missing(etalon: str, where: str, target: Path, e: RepoError) -> RepoError:
    """A half-written clone reused later produces a plausible measurement, so the target
    goes away with the error."""
    shutil.rmtree(target, ignore_errors=True)
    kind = "commit" if is_commit(etalon) else "etalon"
    return RepoError(f"{kind} {etalon} is not in {where}", detail=e.detail or str(e))


def clone(source: Path | str, etalon: str, target: Path) -> Path:
    """A working tree of `source` at `etalon`, holding nothing the etalon does not reach.

    HEAD is left detached on the etalon, tag or commit. A tag etalon also keeps its ref,
    so `etalon` resolves in the clone as it does in the source: everything downstream -
    `commit_of`, `etalon_file`, `etalon_files`, the diff a run is scored on - reads the
    etalon as a revision and cannot tell the two apart.
    """
    where = _located(source)
    _emptied(target)
    git(["init", "--quiet", str(target)])
    try:
        git(fetch_argv(where, etalon), cwd=target)
    except RepoError as e:
        raise _missing(etalon, where, target, e) from e
    git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=target)
    if not is_commit(etalon):
        git(["update-ref", f"refs/tags/{etalon}", "FETCH_HEAD"], cwd=target)
    return target


def set_up(target: Path, script: Path, timeout: int) -> None:
    """Runs a scenario's setup script in the clone, and commits what it changed.

    Committed for the reason `give` commits: the diff a run is scored on is taken
    against HEAD, so the script's changes are not counted as the agent's, and a replay
    that runs the script again finds the same base for the patch. What the script leaves
    that the project ignores, such as installed dependencies, stays in the tree and out
    of the commit.

    With the history kept, the commit below still holds whatever the script deleted.
    `forget_history` is what takes it out of the agent's reach.
    """
    done = interrupt.run([str(script)], cwd=target, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        detail = done.stderr.strip()
        raise RepoError(f"setup {script} exited with {done.returncode}: {detail}", detail=detail)
    git(["add", "--all"], cwd=target)
    if git(["status", "--porcelain"], cwd=target).strip():
        git([*GIVER, "commit", "--no-verify", "--quiet", "-m", SETUP_MESSAGE], cwd=target)


def forget_history(target: Path, etalon: str) -> Path:
    """Replaces the clone's repository with a new one whose only commit is HEAD's tree.

    The agent reads `git log` and `git show` as readily as the code, so a cell measuring
    the agent without the project's history must leave none to read: no ancestor, no
    object an ancestor reached. The tree is HEAD's, byte for byte, so the diff a run is
    scored on and the patch a replay applies are unchanged.

    Exactly the tracked files are added again, `--force` included: a tracked file the
    project's `.gitignore` matches is still part of the tree, and an ignored one a setup
    script left behind never was.
    """
    tracked = git(["ls-files", "-z"], cwd=target)
    shutil.rmtree(target / ".git")
    git(["init", "--quiet"], cwd=target)
    with tempfile.NamedTemporaryFile("w") as paths:
        paths.write(tracked)
        paths.flush()
        git(
            ["add", "--force", f"--pathspec-from-file={paths.name}", "--pathspec-file-nul"],
            cwd=target,
        )
    git([*GIVER, "commit", "--no-verify", "--quiet", "-m", etalon], cwd=target)
    if not is_commit(etalon):
        git(["tag", etalon], cwd=target)
    return target


def pin(url: str, etalon: str, target: Path) -> Path:
    """Clones a remote at `etalon` once, as a working tree runs can clone from.

    A working tree and not a bare mirror. `etalon.checkout` is walked as *files* by a
    validator reading the reference state - which is what `Assay.sources_at_etalon`
    does - and a bare repository passes an `is_dir()` check, yields an empty reference,
    and turns every comparison against the etalon into a plausible number about
    nothing. A bare mirror would have been smaller and would have measured nothing,
    silently.

    No agent ever sees this directory: runs `clone` from it, which takes only what the
    etalon reaches.
    """
    _emptied(target)
    git(clone_argv(url, etalon, target))
    if is_commit(etalon):
        try:
            git(["checkout", "--quiet", "--detach", etalon], cwd=target)
        except RepoError as e:
            raise _missing(etalon, url, target, e) from e
    return target


def commit_of(source: Path, etalon: str) -> str | None:
    """The commit an etalon designates, for the archive.

    Peeled with `^{commit}` so an annotated and a lightweight tag record the same kind
    of object: two archives naming different object kinds for the same tag are not
    comparable. Also the only trace left when a tag is moved between two matrices - the
    reason an etalon may now be a commit outright, see `is_commit`.
    """
    out = git(["rev-parse", "--verify", "--quiet", f"{etalon}^{{commit}}"], cwd=source, check=False)
    return out.strip() or None


def etalon_file(source: Path, etalon: str, path: str) -> str:
    """One file's content at the pinned tag, without checking anything out.

    Scoring needs the reference side of a comparison, and reading it from the tag
    means the reference cannot drift while a matrix is in flight.
    """
    return git(["show", f"{etalon}:{path}"], cwd=source, check=False)


def etalon_files(source: Path, etalon: str, pattern: str = "") -> list[str]:
    out = git(["ls-tree", "-r", "--name-only", etalon], cwd=source)
    names = [n for n in out.split("\n") if n]
    return [n for n in names if pattern in n] if pattern else names


def inject(
    prepared: Prepared,
    context: str | None = None,
    system: str | None = None,
    agents: list[Path] | None = None,
    skills: list[Path] | None = None,
    files: dict[str, Path] | None = None,
    agent_model: str | None = None,
) -> Prepared:
    """Drops the harness bricks into the clone and records what was dropped.

    `files` is the only one that lands in the measured tree, and it is committed
    rather than excluded. See the module docstring for why, and `give` for what the
    commit buys a replay.
    """
    d = prepared.path

    give(prepared, files)

    if context:
        (d / "AGENTS.md").write_text(context)
        prepared.injected.append("AGENTS.md")

    if system:
        (d / ".pi").mkdir(exist_ok=True)
        (d / ".pi" / "SYSTEM.md").write_text(system)
        _mark_pi(prepared)

    for source in agents or []:
        if not source.exists():
            raise RepoError(f"agent definition not found: {source}")
        target = d / ".pi" / "agents" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(with_model(source.read_text(), agent_model, source))
        # Read back from the copy, so the trace records the model the subagent will read.
        prepared.agents[source.stem] = agent_frontmatter(target, bool(agent_model))
        _mark_pi(prepared)

    for source in skills or []:
        if not source.exists():
            raise RepoError(f"skill directory not found: {source}")
        target = d / ".pi" / "skills" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
        _mark_pi(prepared)

    if prepared.injected:
        exclude = d / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as f:
            f.write("\n# Injected by the harness, not written by the agent.\n")
            f.write("".join(f"{name}\n" for name in prepared.injected))
    return prepared


def _mark_pi(prepared: Prepared) -> None:
    if ".pi/" not in prepared.injected:
        prepared.injected.append(".pi/")


# An identity of its own, and not the operator's. The commit is the harness speaking,
# it is the only one a reader of `git log` will find above the tag, and a machine with
# no `user.email` configured must not be a machine where half the matrix fails to start.
GIVER = ("-c", "user.name=trysquare", "-c", "user.email=trysquare@localhost")

GIVEN_MESSAGE = "harness: files given to the task before the agent ran"

SETUP_MESSAGE = "harness: setup script run before the agent"


def give(prepared: Prepared, files: dict[str, Path] | None) -> Prepared:
    """Puts a brick's files in the measured tree, and commits them at the etalon.

    The commit is what separates *given to the task* from *written by the agent*
    without hiding anything. `changed_files` and `diff` both compare against HEAD, so
    a file committed here costs nothing in scope scoring - and the moment the agent
    edits or deletes it, that shows up in the patch like any other change. A probe
    handed to an agent is exactly the material an agent may be tempted to weaken until
    it passes, so it is the last thing that should be invisible.

    It is also what makes the run replayable. A reconstitution clones the tag and
    applies the archived patch; the patch's context lines for a given file only match
    if the same file is put back first, and committed the same way. `cli.reconstitute`
    calls this before `apply_diff` for that reason.

    **Never replaces what the tag holds.** Overwriting a tracked file would change the
    measured code while looking like nothing at all: the diff is taken against a HEAD
    that already contains the replacement, so the substitution would be invisible in
    the archive and every cell would be measured on a repository nobody described.
    """
    if not files:
        return prepared

    d = prepared.path
    root = d.resolve()
    added = []
    for destination, source in files.items():
        if not source.exists():
            raise RepoError(f"file brick not found: {source}")
        target = d / destination
        # The loader already refuses an absolute destination and a `..`. This is the
        # backstop for what it cannot see: a symlink inside the clone pointing out of it.
        if not target.resolve().is_relative_to(root):
            raise RepoError(f"file brick destination leaves the clone: {destination}")
        if target.exists():
            raise RepoError(
                f"file brick would replace {destination}, which already exists at "
                f"{prepared.etalon}: a files brick adds to the measured tree and never "
                f"replaces what the tag holds. Replacing it would change the measured "
                f"code invisibly - the diff is taken against a HEAD that already has it."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        added.append(destination)

    git(["add", "--", *added], cwd=d)
    # `--no-verify` because the hook path may come from the operator's global config,
    # which every clone on that machine inherits. A commit hook belonging to somebody
    # else's setup must not be able to fail a measurement.
    git([*GIVER, "commit", "--no-verify", "--quiet", "-m", GIVEN_MESSAGE], cwd=d)
    prepared.given.extend(added)
    return prepared


def frontmatter_lines(lines: list[str], path: Path) -> range:
    """The indices of the frontmatter's lines, between the two `---` fences.

    A file without one is refused: combo and pi skip it as "not an agent", so
    nothing written to it, an override included, would ever be read.
    """
    closing = next((i for i, line in enumerate(lines) if i and line.strip() == "---"), None)
    if not lines or lines[0].strip() != "---" or closing is None:
        raise RepoError(
            f"agent definition {path} has no frontmatter: combo and pi skip a file "
            f"without a `---` block declaring `name:` and `description:`"
        )
    return range(1, closing)


def with_model(text: str, model: str | None, path: Path) -> str:
    """The agent definition with its `model:` set to `model`, every other line kept."""
    lines = text.splitlines(keepends=True)
    block = frontmatter_lines(lines, path)
    if not model:
        return text
    line = f"model: {model}\n"
    declared = [i for i in block if lines[i].partition(":")[0].strip() == "model"]
    for i in declared:
        lines[i] = line
    if not declared:
        lines.insert(block.stop, line)
    return "".join(lines)


def agent_frontmatter(path: Path, overridden: bool = False) -> dict:
    """Reads an agent definition's frontmatter, and settles which model it runs.

    A subagent that declares no model inherits the operator's `defaultModel`. Nine
    shipped agents were in that position, and a session on one provider ran them
    all on another and returned 402s. So the model is resolved here, and where it
    came from is recorded: the declaration may live in two places, but the trace
    settles which one applied.
    """
    lines = path.read_text().splitlines()
    front: dict[str, str] = {}
    for i in frontmatter_lines(lines, path):
        key, sep, value = lines[i].partition(":")
        if sep and key.strip():
            front[key.strip()] = value.strip()

    declared = front.get("model")
    return {
        "name": front.get("name", path.stem),
        "model": declared,
        "source": "scenario override" if overridden else ("file" if declared else None),
        "tools": front.get("tools"),
    }


def check_agent_models(agents: dict[str, dict]) -> None:
    """Refuses to measure a subagent that would run on an inherited model."""
    nameless = sorted(name for name, meta in agents.items() if not meta.get("model"))
    if nameless:
        raise RepoError(
            f"these agents declare no model and no override supplies one: "
            f"{', '.join(nameless)}. They would inherit the operator's "
            f"defaultModel, which is how nine agents once ran on the wrong "
            f"provider and returned 402"
        )


# Both readings below are of the working tree **against HEAD**, and the word `HEAD` is
# load-bearing. `git add -A` stages a removal even under `--intent-to-add`, so a plain
# `git diff` - the index against the working tree - finds the two in agreement and
# reports nothing at all for a file the agent deleted.
#
# What that silence would cost: an agent deleting the repository's own test file to make
# the suite green scores `touched = []` and archives an empty patch. Not scored badly -
# scored as having done nothing, with the archive agreeing, and a replay rebuilding a
# tree where the file is still there.
#
# It is also what lets a `files` brick be committed rather than hidden. Against the
# index, a probe put back by `give` would read as untouched however the agent left it.
HEAD = "HEAD"


def changed_files(d: Path) -> list[str]:
    """Files the agent touched: new files and deletions included.

    `--intent-to-add` is what makes new files appear at all: without it, a change
    written into a file created for the occasion is invisible. It writes to the index,
    which is safe because every run owns its own clone.
    """
    git(["add", "-A", "--intent-to-add"], cwd=d, check=False)
    out = git(["diff", HEAD, "--name-only"], cwd=d, check=False)
    return [n for n in out.split("\n") if n]


def diff(d: Path) -> str:
    """What the agent changed, in a form that can be applied again.

    `--binary` is what makes it applicable. Without it git records a binary file as
    `Binary files /dev/null and b/x.pyc differ`, which carries no content and an
    abbreviated index, and `git apply` refuses it - and refuses the **whole** patch,
    the source changes with it. An agent that runs the declared suite to check its own
    fix leaves `__pycache__/*.pyc` behind, so this is the common case, not the exotic
    one: the archive looks fine until a `replay --rescore` months later cannot use it.

    The extra bytes are base85 of what the agent produced, and only for the files it
    produced. A diff nobody can apply is not smaller, it is empty.
    """
    git(["add", "-A", "--intent-to-add"], cwd=d, check=False)
    return git(["diff", HEAD, "--binary"], cwd=d, check=False)


def apply_diff(d: Path, patch: str, what: str = "") -> None:
    """Replays an archived diff onto a fresh clone.

    This is what makes a validation replayable months later: the archive keeps the
    tag and the patch, not 150 copies of a working tree.

    `what` names whose patch it is. A replay walks every run in a directory, so a
    refusal that says only "the archived diff" leaves the reader to find which of
    sixty it was.
    """
    if not patch.strip():
        return
    proc = interrupt.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=d,
        env=GIT_ENV,
        input=patch,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        whose = f" of {what}" if what else ""
        raise RepoError(f"could not replay the archived diff{whose}: {proc.stderr.strip()}")
