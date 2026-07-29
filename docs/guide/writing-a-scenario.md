# Writing a scenario

A worked walkthrough. For the exhaustive key list see
{doc}`../reference/scenario-schema`.

## Start from the question

A scenario answers one question, and the question decides the cells. Write it as the
title, because it is what the synthesis will be headed with:

```toml
[scenario]
name = "2x3"
title = "A project rule against a well written ticket, at two reasoning budgets"
hypothesis = "../hypotheses/2x3.md"
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
repo = "neon"                      # logical name, resolved by the config
etalon = "etalon-v1"               # a tag, cloned fresh per run
prompt = "../bricks/vague-ticket.md"
```

The prompt may be inline or a path. Prefer a path for anything multi-line: it keeps the
scenario readable, and it lets the prompt be diffed on its own.

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
context = "../bricks/AGENTS.md"

[values.context."careful ticket"]
prompt = "../bricks/careful-ticket.md"

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
harness = ["extension", "agent-gate", "agents"]

[variants."full stack"]
harness = ["extension", "agent-gate", "agents", "skills"]
```

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
paths = ["../agents/explorer.md", "../agents/tester.md"]
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
command = "../validators/neon.py"
metrics = ["overflow", "issues", "delivered", "in_scope", "tests", "api_stable", "touched"]
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
trysquare run scenarios/mine.toml -o out --dry-run
```

This loads and validates everything, checks every referenced path, verifies the thinking
precondition, reports how blind any judge is, and writes nothing.

Then smoke it small:

```bash
trysquare run scenarios/mine.toml -o out --repetitions 2
trysquare parity --smoke out/mine_..._n2
```

`..._n2` cannot touch the matrix you publish later.
