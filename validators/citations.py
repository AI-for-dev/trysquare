#!/usr/bin/env python3
"""Mechanical scoring of an analysis note: which real files does it actually cite?

Written because an LLM judge could not carry this criterion. Scored over two
matrices, `note_usable` saturated as soon as the task got easier - 10/10 in every
cell - and worse, all three of the judge's metrics became identical in **40 runs out
of 40**: it decided "good note" once and answered everything the same way. On the
narrower task it had discriminated (19 of 40 runs had mixed verdicts), so the halo was
induced by the task, not fixed. An instrument that stops discriminating exactly when
the thing it measures gets easy is not an instrument.

This one counts. It has no ceiling, no halo, and no opinion:

    cited_paths   distinct real files the note points at        -> median
    exact_paths   of those, cited with their full path          -> median
    bogus_paths   path-like references matching nothing real    -> median
    cites_paths   at least one real file                        -> rate

`bogus_paths` is the reason this is worth more than a bare count. A note inventing
`game/score.js` is worse than one citing nothing, and a rate over "does it cite
paths" scores those two identically.

Existence is checked against the **etalon tag**, not the modified clone: a note that
cites a file the agent itself created is not evidence of having read the repository.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# A path-like token. Deliberately loose about the prefix and strict about the
# extension, because prose says "game/neon.js" and "neon.js" and "`theme.js`" and
# every one of those is a citation.
CANDIDATE = re.compile(r"[\w./-]*\w\.(?:js|mjs|ts|md|json|html|css|yaml|yml|toml|txt)\b")

# Suffixes a note may cite that are not repository files: an agent talking about
# `package.json` in general, or naming a file it proposes to create. Kept out of
# `bogus_paths` only when they match nothing at all, which is the point.
NOISE = {"e.g.js", "etc.js"}


def etalon_files(repo: Path, tag: str) -> list[str]:
    """Every path in the pinned tree."""
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", tag],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return [line for line in proc.stdout.split("\n") if line.strip()]


def score(note: str, tracked: list[str]) -> tuple[dict, dict]:
    """Counts real, exact and invented citations in a note."""
    by_basename: dict[str, str] = {}
    for path in tracked:
        by_basename.setdefault(Path(path).name, path)
    tracked_set = set(tracked)

    real: set[str] = set()
    exact: set[str] = set()
    bogus: set[str] = set()

    for raw in CANDIDATE.findall(note):
        token = raw.strip("`'\"(),;:").lstrip("./")
        if not token or token in NOISE:
            continue
        if token in tracked_set:
            real.add(token)
            exact.add(token)
        elif token in by_basename:
            # "neon.js" in a repository with exactly one neon.js is a citation: it
            # is specific enough to open the file, which is what the criterion is
            # about. Counted as real but not as exact.
            real.add(by_basename[token])
        else:
            bogus.add(token)

    metrics = {
        "cited_paths": len(real),
        "exact_paths": len(exact),
        "bogus_paths": len(bogus),
        "cites_paths": bool(real),
    }
    reasons = {}
    if real:
        reasons["cited_paths"] = "cites " + ", ".join(sorted(real))
    else:
        reasons["cited_paths"] = "no real file cited"
    if bogus:
        reasons["bogus_paths"] = "does not exist at the etalon: " + ", ".join(sorted(bogus))
    return metrics, reasons


def read_note(context: dict) -> str:
    """The agent's final prose.

    Provided by the harness as a file, so this does not reimplement stream parsing.
    """
    path = context.get("response")
    if not path:
        raise SystemExit("context has no `response`: this harness is too old to score notes")
    return Path(path).read_text(errors="replace")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <context.json>", file=sys.stderr)
        return 2
    try:
        context = json.loads(Path(argv[1]).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"unreadable context: {e}", file=sys.stderr)
        return 2

    etalon = context["etalon"]
    repo = Path(etalon.get("checkout") or context["repo"])
    tracked = etalon_files(repo, etalon["tag"])
    if not tracked:
        print(f"no file listed at {etalon['tag']} in {repo}", file=sys.stderr)
        return 1

    metrics, reasons = score(read_note(context), tracked)
    json.dump({"metrics": metrics, "reasons": reasons}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
