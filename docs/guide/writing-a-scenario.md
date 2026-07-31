# Writing a scenario

A worked walkthrough. For the exhaustive key list see
{doc}`../reference/scenario-schema`.

Two starting points, both alive rather than copied out of this page:

```bash
trysquare init my-experiment      # the shape: every mandatory field, as a placeholder
```

`examples/scenario.toml` in the repository is the same scenario finished - one axis, two
cells, a real validator - wired to the test fixture, and the suite dry-runs it so it
cannot rot. Start from the skeleton to be told what is mandatory; start from the example
to read a whole one.

## Start from the question

A scenario answers one question, and the question decides the cells. Write it as the
title, because it is what the synthesis will be headed with:

```toml
[scenario]
name = "rule-vs-ticket"
title = "A project rule against a well written ticket, at two reasoning budgets"
hypothesis = "hypothesis.md"
```

### Write the hypothesis first

Before the cells, not after. State what is predicted **and what would falsify it**:

```markdown
## What would falsify it

- The careful ticket does not separate from the baseline. Then a well specified
  task is not the lever we claim.
- The witness moves the criterion as much as either brick. Then what is being
  measured is reasoning effort, not context.
```

:::{important}
This is the cheapest honesty available. A hypothesis written afterwards is a conclusion
wearing a disguise, and the falsification list is what makes a disappointing result
publishable instead of quietly reframed.

It also catches designs that cannot fail. If you cannot name what would falsify the
prediction, the scenario is not an experiment.
:::

## Pin the task

```toml
[task]
repo = "my-repo"                   # logical name, resolved by the config
etalon = "etalon-v1"               # a tag, cloned fresh per run
prompt = "tickets/vague.md"        # relative to this file
```

The prompt may be inline or a path. Prefer a path for anything multi-line: it keeps the
scenario readable, and it lets the prompt be diffed on its own.

Three more keys describe what *running* the task involves, and the scenario declares them
because the alternative is detecting them from inside the perimeter the agent may edit:

```toml
test_command = "python3 -m unittest discover -s tests -t ."   # once `tests` is scored
prepare = ["npm ci"]                     # before the suite, in order; usually nothing
artefacts = ["__pycache__", "*.pyc"]     # leavings, not the agent's work
```

An artefact pattern matches a whole path by globbing *or* any single component of it, so
naming a directory is enough - `__pycache__` catches `tests/__pycache__/x.pyc` with no
`*` at all.

:::{warning}
`artefacts` is the one to get right before measuring. An agent that runs the declared
suite to check itself leaves bytecode in the clone; without the declaration, scope
scoring counted it as the agent's work and `in_scope` was false in **every run of every
cell**. Worse than noise: the runs that scored out of scope were exactly the ones where
the agent verified itself.

Declaring the patterns is half of it - a validator subtracts them, which
{doc}`validators` shows in one line.
:::

:::{warning}
Task material is **experimental input, not documentation.** Once a matrix has been
published from a prompt, changing a word of that prompt changes the measurement. The
prompts shipped here are byte-identical to what was measured, and deliberately not
translated for that reason.
:::

## Declare the agent completely

```toml
[agent]
provider = "ilaas"
model = "gemma-4-31b"
thinking = "off"
```

All three are mandatory. There is no way to say "whatever the machine does".

## Declare the protocol, including the load

```toml
[protocol]
repetitions = 10        # ten in a session, twenty to publish
concurrency = 5
timeout = 900
attempts = 3
```

`concurrency` and `timeout` are not comfort settings. They condition the retry count and
therefore every cost column, so a plan carries its own load.

## Choose grid or variants

**A grid** where the design is regular - every combination is meaningful:

```toml
[axes]
context = ["nothing", "rule", "careful ticket"]
thinking = ["off", "high"]

[values.context.rule]
context = "AGENTS.md"

[values.context."careful ticket"]
prompt = "tickets/careful.md"

[values.thinking.high]
thinking = "high"
```

Six cells. **The axis declaration order fixes the table**: `context` in rows,
`thinking` in columns. The first value of each axis is the baseline and declares nothing.

**Variants** where it is not - each cell adds something different:

```toml
[variants.nothing]

[variants."+extension"]
harness = ["extension"]

[variants."+subagents"]
harness = ["extension", "agents"]

[variants."full stack"]
harness = ["extension", "agents", "skills"]
```

A cell listing a brick with `paths` also loads the shipped subagent gate, without
declaring it. Injecting agent definitions does not by itself make them the only
reachable ones, and the default reaches the agent library's own - see
[`[harness.<name>]`](../reference/scenario-schema.md#harnessname).

They combine: a regular grid plus a couple of named witnesses in one scenario.

### Keep a witness

`+extension` above loads the extension with nobody to delegate to. It looks redundant
and it is the most important cell: it tells whether the gain comes from the agent
definitions or merely from having a delegation tool at all.

A scenario without a witness usually cannot attribute its own result.

### Never put the answer in a cell

An earlier version of the careful ticket contained "do not address any other issue" -
the exact negation of the criterion being measured. That cell verified obedience to an
explicit instruction rather than the quality of a harness, and it saturated the scale so
nothing downstream could demonstrate anything.

If a cell contains the answer to the criterion, the matrix measures compliance.

## Declare the bricks once

```toml
[harness.extension]
repo = "subagent"
tag = "formation-ai4dev-2026-v1"
load = "extension"

[harness.agents]
paths = ["agents/explorer.md", "agents/tester.md"]
model = "ilaas/gemma-4-31b"
```

Named once, cited by name from the variants, so the pinning lives in one place and
cannot diverge between cells.

Pin harness repositories by tag. An experiment that pins the measured repository and
lets the harness float is measuring the operator.

## Declare the validation

```toml
[[validation]]
mode = "script"
command = "score.py"
metrics = ["delivered", "in_scope", "tests", "touched", "documented"]
```

Declare every metric you might want to score later, even if the criterion is one of
them. Extra metrics are stored and can be scored afterwards with `render`; metrics never
returned cannot.

See {doc}`validators`.

## Choose the criterion, the reference, and the validity

```toml
[verdict]
criterion = "overflow"
reference = { context = "nothing", thinking = "off" }
validity = ["delivered", "tests"]
```

The reference is a table for a grid and a string for a variant.

:::{danger}
**Validity must match the task.** `delivered` means "a file changed". For a task asking
for a diff, a run that changed nothing is an agent that did not work. For a task asking
for prose, changing nothing is compliance - and copying `["delivered", "tests"]` into
such a scenario eliminates the entire matrix.

When in doubt, declare nothing: a run counts only if it consumed tokens, and a run whose
validator failed is invalid. Both are global.
:::

## Check it before spending

```bash
trysquare validate mine.toml
```

This loads and validates everything, checks every referenced path, verifies the thinking
precondition, reports how blind any judge is, and needs no output directory at all. The
refusals are shared with `run`, so it cannot pass what a run would reject.

Once the file is settled, `run --dry-run` answers the other question - what the matrix
will cost - by adding the plan: the runs to perform, a duration bound, and a spend
estimate from this experiment's archive. It writes nothing either.

Then smoke it small:

```bash
trysquare run mine.toml -o out --repetitions 2
trysquare parity --smoke out/mine_..._n2
```

`..._n2` cannot touch the matrix you publish later.
