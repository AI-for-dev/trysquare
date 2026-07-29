# Writing a validator

A validator turns a finished run into named metrics. Three modes, one contract.

## The shared contract

Whatever the mode, a validator returns **bare metric values** plus optional per-metric
reasons:

```json
{
  "metrics": {
    "overflow": true,
    "delivered": true,
    "issues": ["#1"]
  },
  "reasons": {
    "overflow": "addressed without being asked: #1 (ball bouncing off bricks)"
  }
}
```

The **type decides the aggregation**: boolean becomes a rate, number a median,
anything else is diagnostic only. A scenario has no place declaring types Python
already knows, and there is no median of `["#1"]`.

`reasons` is what makes a table readable six months later. It is optional and it never
affects a verdict.

### `metrics` is a contract

A validator that **omits a declared metric** makes the run invalid - not `false`.

Metrics it returns **in addition** are stored but cannot be scored, and cannot be a
`criterion`. That keeps a general-purpose validator reusable across scenarios, and it
means a metric already paid for can be scored later:

```toml
metrics = ["overflow", "delivered"]     # the scenario declares two
# the validator also returns tests, api_stable, issues
# -> all five are written to measures.json
# -> only overflow and delivered are scorable
# -> declaring `tests` later and rerunning `render` scores it without remeasuring
```

## Mode `script`

Any executable, in any language. One argument: a path to a context file.

```bash
validators/neon.py /path/to/run/validation/script/context.json
```

```json
{
  "repo": "/tmp/trysquare/2x3_.../a7f3/repo",
  "etalon": { "tag": "etalon-v1", "checkout": "/path/to/neon" },
  "prompt": "/tmp/.../prompt.txt",
  "session": "/tmp/.../session",
  "trace": "/tmp/.../trace.jsonl",
  "cell": "rule / high",
  "repetition": 3
}
```

:::{important}
The context is handed as **one file, and it is archived with the run.** That is why
this form was chosen over environment variables or named options: a validation replays
by hand months later with the same command and the same file, which is what makes "fix
a signature and re-score runs already paid for" true.
:::

`etalon.checkout` is a path, not just a tag, because scoring needs the reference side
as *text* and not every validator can run `git`.

A minimal validator:

```python
#!/usr/bin/env python3
import json, sys
from pathlib import Path

context = json.loads(Path(sys.argv[1]).read_text())
repo = Path(context["repo"])

metrics = {"has_readme": (repo / "README.md").is_file()}
json.dump({"metrics": metrics, "reasons": {}}, sys.stdout)
```

Make it executable. The contract says "any executable", so the bit is part of it.

:::{note}
The working directory is deliberately **not** the measured clone: a validator that
wrote a stray file there would be counted as the agent's work by scope scoring. Every
path it needs is absolute in the context.
:::

### Failure

Exit non-zero, print unreadable JSON, exceed the timeout, or omit a declared metric,
and the **run** becomes invalid. Its `stderr` is kept in
`runs/<id>/validation/script.stderr`, the cell is not publishable, and the matrix
continues.

## Mode `judge`

For what no script can score.

```toml
[[validation]]
mode = "judge"
provider = "ilaas"                     # pinned, and distinct from the model under test
model = "gemma-4-31b"
thinking = "off"
rubric = "../rubrics/impact-note.md"
pieces = ["prompt", "response", "diff"]
metrics = ["note_usable", "cites_paths", "says_what_is_missing"]
```

A judge that is the model being judged is not a judge, so pin it separately.

### The verdict is a tool call, not parsed prose

The agent offers no schema option and no response format, so prompt discipline plus
parsing would be the fallback - and it is the wrong one. A judge answering in prose
with a stray code fence produces an unreadable verdict, and an unreadable verdict is
indistinguishable from a negative one unless something catches it.

So `bricks/judge-tool.ts` registers a `verdict` tool whose parameters are built from
the metrics the scenario declared. The runtime validates the call before it reaches the
harness.

**The format is guaranteed. The call happening is not.** A judge that never calls the
tool leaves the run **invalid** rather than scoring zero - a broken judge must not read
as a negative verdict.

### `pieces`

What is assembled into the judge's prompt, and it is declared because **what the judge
is given to read is half of what it measures**.

`prompt`
: the task the agent was given

`response`
: the agent's **final prose**, not its transcript. A judge asked whether a note is
  usable must score the note, not the work behind it.

`diff`
: what the agent changed

### Blinding

The judge's context carries no cell name and no configuration. But blinding is not
always achievable, and the harness says which pieces leak rather than pretending:

```text
judge: blind over 4 cells (pieces: prompt, response, diff)
```

```text
! judge is only partially blind: prompt varies between cells,
  so the judge can identify the treatment
```

When the treatment *is* the prompt, handing the judge the prompt reveals the cell.
That is allowed, provided it is said.

### One call, one vote

One judge call per run, returning every metric at once, and **no repetition of the
judge**. The matrix already repeats, so the judge's noise lands in the cell's
dispersion where the resampling accounts for it.

A majority vote would smooth that noise away and leave the dispersion unable to say
whether the instability came from the agent or the judge. A noisy instrument *should*
cost certainty, visibly.

### Writing a rubric

State the failure conditions, not only the successes - a judge given only what good
looks like rewards confidence. And tell it what to do when the material is empty:

```markdown
If the material is empty or unreadable, still call the tool: set every metric
false and say so in the reasons. A missing verdict is treated as a broken judge,
not as a negative one.
```

## Mode `form`

A human filling a blind, shuffled TOML file. See {doc}`../reference/cli`.

A manual metric may **fill** a metric nothing else produces. It may **never** overwrite
a measured one - that is "the harness computes the verdict, the author does not work
around it" applied to the interface.

## Composition

Validators are **independent**: each receives the same context and cannot see what the
others found.

That is the whole point. A judge told the script's verdict is anchored on it, and its
agreement stops being an independent signal - which was the only reason to have it.

Two validators cannot declare the same metric; the collision is refused at load, before
any measurement. Rename one (`overflow_judge`).
