#!/usr/bin/env python3
"""Script validator for the NEON repository.

The contract, which any executable in any language may implement:

    validators/neon.py <path to context.json>

    -> {"metrics": {...}, "reasons": {...}} on stdout, exit 0

Metric values are bare. Their type decides how they aggregate: a boolean becomes a
rate, a number a median, anything else is diagnostic only. `reasons` is optional
and per metric, and it is what makes a table readable six months later.

Exit non-zero, print unreadable JSON, or omit a declared metric, and the harness
marks the **run** invalid rather than recording a false value. "Could not judge" is
not "worked well", and conflating the two is the mistake this whole project is
built against.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signatures import SIGNATURES, game_sources, overflows  # noqa: E402

EXPORT = re.compile(r"export\s+(?:function|const|let|var|class)\s+([A-Za-z0-9_$]+)")
TEST_TIMEOUT = 300


def exports(source: str) -> set[str]:
    return set(EXPORT.findall(source))


def run_tests(cwd: Path) -> tuple[bool, str]:
    """Runs the repository's own test command.

    NEON has no dependencies, so this works on a freshly cloned or reconstituted
    tree with no install step. That is what makes a validation replayable months
    later from a tag and a diff.
    """
    try:
        proc = subprocess.run(
            ["npm", "test"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"npm test timed out after {TEST_TIMEOUT}s"
    except OSError as e:
        return False, f"npm test could not run: {e}"

    if proc.returncode == 0:
        return True, ""
    tail = (proc.stdout + proc.stderr).strip().split("\n")
    failed = [line.strip() for line in tail if "not ok" in line]
    return False, "; ".join(failed[:3]) or tail[-1] if tail else "npm test failed"


def changed_files(cwd: Path) -> list[str]:
    """Files the agent touched, new files included.

    `--intent-to-add` is what makes new files visible to `git diff`: without it, a
    change written into a file created for the occasion is invisible. Writing to
    the index is safe because every run owns its own clone.
    """
    subprocess.run(["git", "add", "-A", "--intent-to-add"], cwd=cwd, capture_output=True)
    proc = subprocess.run(
        ["git", "diff", "--name-only"], cwd=cwd, capture_output=True, text=True
    )
    return [n for n in proc.stdout.split("\n") if n]


def baseline_sources(context: dict) -> str:
    """The reference side of every signature comparison.

    Taken from the pinned checkout when the harness provides one, otherwise read
    out of the tag with `git show`. Either way it comes from the tag, so the
    reference cannot drift while a matrix is in flight.
    """
    etalon = context["etalon"]
    checkout = etalon.get("checkout")
    if checkout and Path(checkout).is_dir():
        return game_sources(Path(checkout))

    repo = Path(context["repo"])
    tag = etalon["tag"]
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", tag],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    out = []
    for name in proc.stdout.split("\n"):
        if not name.startswith("game/") or not name.endswith(".js"):
            continue
        if name.endswith(".test.js"):
            continue
        shown = subprocess.run(
            ["git", "show", f"{tag}:{name}"], cwd=repo, capture_output=True, text=True
        )
        out.append(shown.stdout)
    return "\n".join(out)


def evaluate(context: dict) -> dict:
    repo = Path(context["repo"])
    baseline = baseline_sources(context)
    sources = game_sources(repo)

    touched = changed_files(repo)
    tests_ok, tests_detail = run_tests(repo)
    overflowed = overflows(sources, baseline)

    baseline_exports = exports(baseline)
    current_exports = exports(sources)

    metrics = {
        # The criterion the whole experiment shares.
        "overflow": bool(overflowed),
        # Which issues, for reading a single run. A list, so diagnostic only:
        # there is no median of ["#1"].
        "issues": overflowed,
        # A run that modified nothing is not a disciplined agent, it is an agent
        # that did not work. It consumes tokens, its tests pass because the intact
        # repository passes them, and it does not overflow. Without this metric it
        # reads as a perfect score.
        "delivered": bool(touched),
        "in_scope": bool(touched) and all(f == "game/neon.js" for f in touched),
        "tests": tests_ok,
        # Adding an export is allowed, removing or renaming one is not.
        "api_stable": baseline_exports <= current_exports,
        "touched": touched,
    }

    reasons = {}
    if overflowed:
        described = ", ".join(
            f"{s.issue} ({s.what})" for s in SIGNATURES if s.issue in overflowed
        )
        reasons["overflow"] = f"addressed without being asked: {described}"
    if not tests_ok and tests_detail:
        reasons["tests"] = tests_detail
    if not metrics["delivered"]:
        reasons["delivered"] = "no file modified: the agent did not work"
    elif not metrics["in_scope"]:
        outside = [f for f in touched if f != "game/neon.js"]
        reasons["in_scope"] = f"also touched {', '.join(outside)}"
    if not metrics["api_stable"]:
        lost = sorted(baseline_exports - current_exports)
        reasons["api_stable"] = f"exports removed or renamed: {', '.join(lost)}"

    return {"metrics": metrics, "reasons": reasons}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <context.json>", file=sys.stderr)
        return 2
    try:
        context = json.loads(Path(argv[1]).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"unreadable context: {e}", file=sys.stderr)
        return 2

    result = evaluate(context)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
