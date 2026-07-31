# SPDX-License-Identifier: BSD-3-Clause
"""Preparing the repository a run measures, and injecting the harness bricks.

Two decisions here are not interchangeable with the obvious alternatives, and
both come from a trap that was actually hit.

**Clone at a tag, never copy the working tree.** The measured state has to be
immutable and named, or yesterday's measures no longer compare with tomorrow's.
And a repository may be a *worktree*, whose `.git` is only a file pointing at a
shared gitdir: a recursive copy then gives every run the same gitdir, so one
agent running `git commit` moves the comparison base of every run in flight and
nobody notices.

**A remote is pinned as a working tree, never as a bare mirror.** A bare mirror is
smaller and answers `git show` and `git ls-tree` perfectly well, so it looks right.
But `etalon.checkout` is walked as files by a validator reading the reference state,
a bare repository passes an `is_dir()` check and yields an empty reference, and the
whole matrix then reports plausible numbers about nothing. See `pin`.

**Exclude what we injected.** The files the harness drops are not the agent's
work. Without `.git/info/exclude`, scope scoring counts them as changes the agent
made, and every configured cell drops to zero: a measurement of our own tooling
rather than of the behaviour under test.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import is_remote

GIT_TIMEOUT = 180


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
    agents: dict[str, dict] = field(default_factory=dict)


def git(args: list[str], cwd: Path | None = None, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if check and proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RepoError(f"git {' '.join(args)} failed: {stderr}", detail=stderr)
    return proc.stdout


def clone_argv(source: str, etalon: str, target: Path, keep_tags: bool = False) -> list[str]:
    """The flags a clone is made with, separated so they can be asserted.

    `--single-branch --branch <etalon>` is the reproducibility guarantee: the clone is
    the pinned state and nothing else.

    `--no-tags` is right for a run's clone, where nothing needs the tag ref once HEAD is
    detached on it. A pinned source keeps its tags, because every run clones *from* that
    directory **by tag name**.

    Measured rather than assumed: git does in fact keep the tag named by `--branch` even
    under `--no-tags`, so a pinned source would probably work either way. Nothing
    documents that interaction, though, and the pinned source's whole job is to answer a
    clone by tag - so it does not rest on undocumented behaviour. The cost is the tag
    refs of one branch.
    """
    args = ["clone", "--quiet"]
    if not keep_tags:
        args.append("--no-tags")
    return [*args, "--single-branch", "--branch", etalon, source, str(target)]


def clone(source: Path | str, etalon: str, target: Path, keep_tags: bool = False) -> Path:
    """Clones `source` at tag `etalon` into `target`.

    `source` may be a local directory or a git URL. A URL is handed to git verbatim:
    `resolve()` would turn it into a path, which is the defect `config.is_remote`
    exists to prevent.

    The `exists()` check stays as a backstop. `runner.prepare_source` checks earlier
    and says more, but a caller that reaches here with nothing on disk should still be
    told, not left with a bare git error.
    """
    if is_remote(str(source)):
        where = str(source)
    else:
        source = Path(source)
        if not source.exists():
            raise RepoError(f"repository not found: {source}")
        where = str(source.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    git(clone_argv(where, etalon, target, keep_tags=keep_tags))
    return target


def pin(url: str, etalon: str, target: Path) -> Path:
    """Clones a remote at `etalon` once, as a working tree runs can clone from.

    A working tree and not a bare mirror. `etalon.checkout` is walked as *files* by a
    validator reading the reference state - which is what `Assay.sources_at_etalon`
    does - and a bare repository passes an `is_dir()` check, yields an empty reference,
    and turns every comparison against the etalon into a plausible number about
    nothing. A bare mirror would have been smaller and would have measured nothing,
    silently.
    """
    return clone(url, etalon, target, keep_tags=True)


def commit_of(source: Path, etalon: str) -> str | None:
    """The commit a tag designates, for the archive.

    Peeled with `^{commit}` so an annotated and a lightweight tag record the same kind
    of object: two archives naming different object kinds for the same tag are not
    comparable. Also the only trace left when a tag is moved between two matrices.
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
    agent_model: str | None = None,
) -> Prepared:
    """Drops the harness bricks into the clone and records what was dropped."""
    d = prepared.path

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
        target_dir = d / ".pi" / "agents"
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / source.name)
        prepared.agents[source.stem] = agent_frontmatter(source, agent_model)
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


def agent_frontmatter(path: Path, override: str | None = None) -> dict:
    """Reads an agent definition's frontmatter, and settles which model it runs.

    A subagent that declares no model inherits the operator's `defaultModel`. Nine
    shipped agents were in that position, and a session on one provider ran them
    all on another and returned 402s. So the model is resolved here, and where it
    came from is recorded: the declaration may live in two places, but the trace
    settles which one applied.
    """
    text = path.read_text()
    front: dict[str, str] = {}
    if text.startswith("---"):
        _, _, rest = text.partition("---")
        block, _, _ = rest.partition("---")
        for line in block.split("\n"):
            key, sep, value = line.partition(":")
            if sep and key.strip():
                front[key.strip()] = value.strip()

    declared = front.get("model")
    model = override or declared
    return {
        "name": front.get("name", path.stem),
        "model": model,
        "source": "scenario override" if override else ("file" if declared else None),
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


def changed_files(d: Path) -> list[str]:
    """Files the agent touched, new files included.

    `--intent-to-add` is what makes new files appear in `git diff`: without it, a
    change written into a file created for the occasion is invisible. It writes to
    the index, which is safe because every run owns its own clone.
    """
    git(["add", "-A", "--intent-to-add"], cwd=d, check=False)
    out = git(["diff", "--name-only"], cwd=d, check=False)
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
    return git(["diff", "--binary"], cwd=d, check=False)


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
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=d,
        input=patch,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        whose = f" of {what}" if what else ""
        raise RepoError(f"could not replay the archived diff{whose}: {proc.stderr.strip()}")
