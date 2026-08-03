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
metrics = ["in_scope", "delivered"]     # the scenario declares two
# the validator also returns tests, touched, documented
# -> all five are written to measures.json
# -> only in_scope and delivered are scorable
# -> declaring `tests` later and rerunning `render` scores it without remeasuring
```

## Mode `script`

Any executable, in any language. One argument: a path to a context file.

```bash
score.py /path/to/run/validation/script/context.json
```

```json
{
  "repo": "/tmp/trysquare/rule-vs-ticket_.../a7f3/repo",
  "etalon": { "tag": "etalon-v1", "checkout": "/path/to/my-repo" },
  "prompt": "/tmp/.../prompt.txt",
  "session": "/tmp/.../session",
  "trace": "/tmp/.../trace.jsonl",
  "cell": "rule / high",
  "repetition": 3,
  "test_command": "node --test 'src/**/*.test.js'",
  "prepare": [],
  "artefacts": ["node_modules"],
  "touched": ["src/basket.js"],
  "files": ["README.md", "src/basket.js", "src/theme.js"],
  "given": ["src/probe.test.js"],
  "declared": ["in_scope", "delivered", "tests"]
}
```

Read it with {mod}`trysquare.assay` rather than by hand - `run.touched`, `run.etalon`,
`run.sources_at_etalon("src/*.js")`. Four names cover a whole validator, and the module
carries the error contract with them. `examples/validator.py` is a complete one written
that way, and the suite runs it against `tests/fixtures/tiny` so it cannot go stale.

`touched` and `files` are computed by the harness. Three validators written before the
base existed each reimplemented the first with a raw `subprocess`, one of them landing
on a **different answer** for the reference side, while `repo.changed_files` had held
that knowledge all along. A fact computed in one place cannot be got three slightly different ways.

`declared` is what the scenario contracted for, so a validator can be told which metric it
forgot before anything is recorded rather than after the tokens are spent.

### Subtract the by-products before scoring scope

`artefacts` is what the scenario declared as the leavings of running the task. Subtract
them, and score the remainder:

```python
work = run.touched - run.artefacts
```

:::{warning}
Skipping this cost a whole matrix. An agent asked to fix a defect ran the declared suite
to check itself, which left `__pycache__/*.pyc` in the clone; scope scoring counted the
bytecode as its work, and `in_scope` was false in **every run of every cell**. The
criterion saturated at zero and nothing was concludable.

And it is not random noise: the runs that scored out of scope were the ones where the
agent verified itself. The metric ended up anti-correlated with the behaviour it was
meant to reward.
:::

`run.artefacts` is the subset of `run.touched` matching those patterns, and `touched`
itself is never reduced - the filter decides what a verdict rests on, not what is
recorded. Both metrics of the shipped example need the subtraction: `delivered` too,
since bytecode is not a delivery.

### Tell what was given from what was written

`given` is what a [`files` brick](../reference/scenario-schema.md#the-files-kind)
put in the tree before the agent started, and it is empty in every cell that was handed
nothing. Read it whenever a metric depends on that material still being there:

```python
if FICHIER not in run.given:
    return Metric.unjudged("no probe was given to this cell")
```

An absent file is one fact with two causes - never given, or deleted along the way -
and without this key a run that removed the test it was handed scores exactly like a
run that was handed no test at all. Given files are committed on top of the etalon, so
an edit to one appears in `touched` like any other change, and comparing the tree's
copy against the brick's says whether it was weakened.

It is withheld from a judge's context: naming a brick names the configuration.

A scenario that declares nothing gets an empty set, so this line is safe to write
everywhere.

:::{important}
The context is handed as **one file, and it is archived with the run.** That is why
this form was chosen over environment variables or named options: a validation replays
by hand months later with the same command and the same file, which is what makes "fix
a signature and re-score runs already paid for" true.
:::

`etalon.checkout` is a path, not just a tag, because scoring needs the reference side
as *text* and not every validator can run `git`.

`test_command` is the suite that decides the `tests` metric, declared by the scenario and
never guessed here. It is carried **as the scenario wrote it**, so a context read against the
scenario file says the same thing, and so a validator gets the shape its own runtime prefers:
a shell splits a string for free, where a JSON array would have to be parsed and rebuilt.

No shell runs it, though - the loader refused any word that only means something to one. In
Python, {func}`trysquare.scenario.split_command` is the rule the loader vetted it with, and
`run.tests()` already uses it. Elsewhere, `$cmd` unquoted does the same job.

`prepare` is what has to run before the suite - usually nothing. Its failures mean
something else: no network or a dependency that will not install says *nobody judged*, so
the metric is unjudged rather than false. The suite failing is a measurement.

:::{warning}
Do not fall back to `npm test`, or to any command read out of the repository. Its
meaning lives in `package.json`, which is inside the perimeter the measured agent may
edit: broken code plus a test script of `echo ok` scores green, and nothing in the
output says so. The scenario declaring the command is what stops the agent being
measured from choosing how it is measured.

The key is absent when the scenario names no suite, and an absent key is a different
fact from an empty command. It says this experiment scores no test suite - which is
something to refuse over, not something to score as a failure.
:::

A minimal validator, by hand. Any executable in any language may do this, and this is
what the contract is:

```python
#!/usr/bin/env python3
import json, sys
from pathlib import Path

context = json.loads(Path(sys.argv[1]).read_text())
repo = Path(context["repo"])

metrics = {"has_readme": (repo / "README.md").is_file()}
json.dump({"metrics": metrics, "reasons": {}}, sys.stdout)
```

In Python, write it with {mod}`trysquare.assay` instead. The same validator, plus the
error contract, plus a reason attached where the value was found:

```python
#!/usr/bin/env python3
from trysquare.assay import Assay, Metric, validator

SCOPE = frozenset({"counter.py"})


@validator
def evaluate(run: Assay) -> dict:
    outside = run.touched - SCOPE
    return {
        "delivered": bool(run.touched),
        # The reason belongs to the failure. Attached unconditionally it reads
        # `also touched ` on every run that stayed in scope; an empty reason is dropped.
        "in_scope": Metric(
            not outside,
            f"also touched {', '.join(sorted(outside))}" if outside else "",
        ),
        "tests": run.tests(),
        "touched": run.touched,
    }


if __name__ == "__main__":
    raise SystemExit(evaluate.cli())
```

The whole thing is in `examples/validator.py`, and `tests/test_example.py` runs it against
`tests/fixtures/tiny` on every CI build - so unlike a snippet in a document, it cannot rot.
It is also where to see the pattern that matters: wrapping a metric the run cannot answer
so that **one** unanswerable metric does not refuse the run and take every other metric
with it.

Make it executable. The contract says "any executable", so the bit is part of it.

:::{note}
The working directory is deliberately **not** the measured clone: a validator that
wrote a stray file there would be counted as the agent's work by scope scoring. Every
path it needs is absolute in the context.
:::

### Failure

Exit non-zero, print unreadable JSON, exceed the timeout, or omit a declared metric,
and the **run** becomes invalid. Its `stderr` is kept in that run's
`validation/script.stderr`, the cell is not publishable, and the matrix continues.

## Mode `judge`

For what no script can score.

```toml
[[validation]]
mode = "judge"
provider = "ilaas"                     # pinned, and distinct from the model under test
model = "gemma-4-31b"
thinking = "off"
rubric = "rubric.md"
pieces = ["prompt", "response", "diff"]
metrics = ["note_usable", "cites_paths", "says_what_is_missing"]
```

A judge that is the model being judged is not a judge, so pin it separately.

### The verdict is a tool call, not parsed prose

The agent offers no schema option and no response format, so prompt discipline plus
parsing would be the fallback - and it is the wrong one. A judge answering in prose
with a stray code fence produces an unreadable verdict, and an unreadable verdict is
indistinguishable from a negative one unless something catches it.

So `trysquare/judge-tool.ts`, shipped inside the package, registers a `verdict` tool
whose parameters are built from the metrics the scenario declared. The runtime validates
the call before it reaches the harness.

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
