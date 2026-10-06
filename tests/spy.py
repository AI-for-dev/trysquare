"""A whole `trysquare run`, with a fake agent reporting what it found when it started.

Nothing here spends a token: the agent is a script that writes down what a real one
could have read, leaves a session, and changes the tree so the run counts.
"""

import json
from pathlib import Path

from trysquare import agent
from trysquare.cli import main

from tests.test_cli import SCENARIO_TOML, TREE_DEPENDENT

SPYING_AGENT = """#!/usr/bin/env python3
import json, os, subprocess, sys, uuid
args = sys.argv[1:]
if "--version" in args:
    sys.exit(print("0.0.0"))
sessions = args[args.index("--session-dir") + 1]
os.makedirs(sessions, exist_ok=True)
commits = subprocess.run(["git", "rev-list", "--all", "--count"], capture_output=True, text=True)
seen = {"sessions": sorted(os.listdir(sessions)), "commits": int(commits.stdout)}
with open(os.environ["SEEN"], "a") as f:
    f.write(json.dumps(seen) + "\\n")
open(os.path.join(sessions, f"{uuid.uuid4()}.jsonl"), "w").write("{}\\n")
open("a.js", "w").write("changed\\n")
usage = {"input": 10, "output": 1}
print(json.dumps({"type": "message_end", "message": {"role": "assistant", "usage": usage}}))
"""


def launch(tmp_path: Path, monkeypatch, source: Path, scenario: str = SCENARIO_TOML) -> list:
    """Runs `scenario` on `source` once, and returns what each run's agent saw.

    Two runs per cell, in order. Called again, it relaunches the same matrix and returns
    what every agent saw since the first launch.
    """
    fake = tmp_path / "pi"
    fake.write_text(SPYING_AGENT)
    fake.chmod(0o755)
    monkeypatch.setattr(agent, "PI", str(fake))
    monkeypatch.setenv("SEEN", str(tmp_path / "seen"))

    validator = tmp_path / "v.py"
    validator.write_text(TREE_DEPENDENT)
    validator.chmod(0o755)
    config = tmp_path / "trysquare.toml"
    config.write_text(f'[repos]\nmy-repo = "{source}"\n[defaults]\nworkdir = "{tmp_path}"\n')
    (tmp_path / "s.toml").write_text(scenario)

    main(
        ["run", str(tmp_path / "s.toml"), "-o", str(tmp_path / "out"), "--config", str(config)]
        + ["--no-progress", "--overwrite"]
    )
    return [json.loads(line) for line in (tmp_path / "seen").read_text().splitlines()]
