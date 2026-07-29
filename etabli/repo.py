"""Preparing the repository a run measures, and injecting the harness bricks.

Two decisions here are not interchangeable with the obvious alternatives, and
both come from a trap that was actually hit.

**Clone at a tag, never copy the working tree.** The measured state has to be
immutable and named, or yesterday's measures no longer compare with tomorrow's.
And a repository may be a *worktree*, whose `.git` is only a file pointing at a
shared gitdir: a recursive copy then gives every run the same gitdir, so one
agent running `git commit` moves the comparison base of every run in flight and
nobody notices.

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

GIT_TIMEOUT = 180


class RepoError(Exception):
    pass


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
        raise RepoError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def clone(source: Path, etalon: str, target: Path) -> Path:
    """Clones `source` at tag `etalon` into `target`."""
    if not source.exists():
        raise RepoError(f"repository not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    git(
        [
            "clone",
            "--quiet",
            "--no-tags",
            "--single-branch",
            "--branch",
            etalon,
            str(source.resolve()),
            str(target),
        ]
    )
    return target


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
    git(["add", "-A", "--intent-to-add"], cwd=d, check=False)
    return git(["diff"], cwd=d, check=False)


def apply_diff(d: Path, patch: str) -> None:
    """Replays an archived diff onto a fresh clone.

    This is what makes a validation replayable months later: the archive keeps the
    tag and the patch, not 150 copies of a working tree.
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
        raise RepoError(f"could not replay the archived diff: {proc.stderr.strip()}")
