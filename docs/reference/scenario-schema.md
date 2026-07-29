# Scenario schema

Every key of a scenario file, what it does, and whether it is required.

A scenario is validated **entirely at load**, before anything is spent. Every refusal
below happens with no tokens consumed.

## `[scenario]`

```{list-table}
:header-rows: 1
:widths: 20 15 65

* - Key
  - Required
  - Meaning
* - `name`
  - no
  - Short name, used in the output directory. Defaults to the file stem.
* - `title`
  - no
  - One line, printed at launch and used as the synthesis heading.
* - `hypothesis`
  - no
  - Path to a file stating what is predicted **and what would falsify it**. Checked to exist.
```

:::{tip}
Write the hypothesis before measuring. A hypothesis written afterwards is a
conclusion wearing a disguise, and the point of the file is to make a disappointing
result publishable rather than quietly reframed.
:::

## `[task]`

```{list-table}
:header-rows: 1
:widths: 20 15 65

* - Key
  - Required
  - Meaning
* - `repo`
  - yes
  - A **logical name**, resolved by `[repos]` in the config file. Never a path.
* - `etalon`
  - **yes**
  - A git tag, cloned fresh for each run. Never a branch or the working tree.
* - `prompt`
  - no
  - The task given to the agent: inline text, or a path to a file.
```

`repo` being logical is what makes a scenario portable: it carries no author's
directory layout. A value containing `/` or `~` is a mistake the schema does not
prevent but the config resolution will.

## `[agent]`

```{list-table}
:header-rows: 1
:widths: 20 15 65

* - Key
  - Required
  - Meaning
* - `provider`
  - **yes**
  - Provider name. Never inherited.
* - `model`
  - **yes**
  - Model id. Never inherited.
* - `thinking`
  - **yes**
  - Reasoning level. Never inherited, and never omitted.
```

All three raise when absent:

```text
[agent].provider is required in the scenario and is never inherited: a value that
is not declared is a value inherited from whoever runs the tool
```

:::{warning}
`thinking` is mandatory even when you want the machine's default. There is no way to
say "whatever the machine does", because that is the defect that made a thinking cell
identical to its baseline in every published matrix.

If a scenario uses subagents, the harness additionally **refuses to run** when the
declared level differs from the machine's `defaultThinkingLevel`. A subagent's level
cannot be declared anywhere, so what cannot be controlled is verified instead. See
{doc}`../guide/troubleshooting`.
:::

## `[protocol]`

```{list-table}
:header-rows: 1
:widths: 20 15 65

* - Key
  - Required
  - Meaning
* - `repetitions`
  - **yes**
  - Runs per cell. Declared in advance; part of the output directory name.
* - `concurrency`
  - no
  - Parallel runs. Falls back to the config, then 5.
* - `timeout`
  - no
  - Seconds per run. Falls back to the config, then 900.
* - `attempts`
  - no
  - Retries **while nothing has been produced**. Falls back to 3.
```

A plan carries its own load. `concurrency` and `timeout` condition the retry count and
therefore every cost column, so whatever their origin they are written into
`state.json` and printed in the synthesis header.

`attempts` is not optional stopping: a run that consumed no tokens produced no result,
so there is nothing to select between. A run that *did* produce something is never
retried, whatever its result.

## Cells: `[axes]`, `[values]`, `[variants]`

A scenario needs at least one cell, from a grid, from named variants, or from both.

### Grid

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

The cartesian product, named by joining the axis values with ` / ` -
`rule / high`. **Declaration order of the axes fixes the order of the rendered
table**: first axis in rows, second in columns.

`[values.<axis>.<value>]` holds only the **delta** from `[agent]` and `[task]`.

:::{important}
**The first value of an axis is the baseline and declares no delta.** Every other
value must declare one:

```text
axis 'context': value 'tickett' declares no delta. Only the first value of an
axis ('nothing') is the baseline. Deltas declared for this axis: ['rule',
'careful ticket']
```

Without that rule, a misspelling produces a cell identical to the baseline, published
twice under two names, with nothing to reveal it.
:::

### Variants

```toml
[variants.nothing]
# the baseline: no delta

[variants."+subagents"]
harness = ["extension", "agent-gate", "agents"]
```

Irregular cells, named explicitly. Grid and variants **add** rather than exclude, so a
scenario may carry a regular grid plus a couple of named witnesses.

A cell name declared twice raises.

### What a delta may contain

```{list-table}
:header-rows: 1
:widths: 20 80

* - Key
  - Effect
* - `prompt`
  - Replaces the task prompt. Inline text or a path.
* - `context`
  - Writes an `AGENTS.md` into the clone. Inline text or a path.
* - `system`
  - Writes a `.pi/SYSTEM.md` into the clone.
* - `thinking`
  - Overrides the reasoning level for this cell.
* - `harness`
  - A list of brick names from `[harness.*]` to load.
```

## `[harness.<name>]`

Bricks, declared once and cited by name so the pinning lives in one place and cannot
diverge between cells.

```toml
[harness.extension]              # a pinned repository
repo = "subagent"                # logical name, resolved by [harness] in the config
tag = "formation-ai4dev-2026-v1"
load = "extension"               # subdirectory passed to the agent

[harness.agent-gate]             # a single file
load = "../bricks/agent-gate.ts"

[harness.agents]                 # files copied into the clone
paths = ["../agents/explorer.md", "../agents/tester.md"]
model = "ilaas/gemma-4-31b"      # optional override, see below

[harness.skills]
paths = ["../skills/profile"]
```

A brick with `repo` is cloned at its `tag` **once per matrix**, its dependencies are
installed, and every cell loads the same pinned state. An experiment that pins the
measured repository and lets the harness float is measuring the operator.

:::{note}
`[harness.agents].model` is an optional override. Absent, each agent file declares its
own model, which keeps a cheap explorer alongside an expensive coder expressible.
Present, it overrides every file.

Either way the harness **refuses to inject an agent that would end up with no model**:
nine shipped agents once ran on the wrong provider and returned 402s because they
declared none. Two places may declare, so the trace settles which one applied -
`configuration.json` records the model used and where it came from.
:::

## `[[validation]]`

Repeatable. At least one is required - a scenario that measures nothing is refused.

```{list-table}
:header-rows: 1
:widths: 20 15 65

* - Key
  - Required
  - Meaning
* - `mode`
  - **yes**
  - `script`, `judge` or `form`.
* - `metrics`
  - **yes**
  - The names this validator contracts to return.
* - `command`
  - script
  - Path to the executable. Always treated as a path.
* - `rubric`
  - judge
  - Path to the rubric. Always treated as a path.
* - `provider`, `model`, `thinking`
  - judge
  - The judge's own pinned configuration.
* - `pieces`
  - judge
  - What is assembled into the judge's prompt: `prompt`, `response`, `diff`.
```

Two validators cannot declare the same metric. Refused at load, before any
measurement:

```text
metric declared by two validators: overflow. Rename one (for instance
overflow_judge)
```

See {doc}`../guide/validators` for the contract each mode implements.

## `[verdict]`

```{list-table}
:header-rows: 1
:widths: 20 15 65

* - Key
  - Required
  - Meaning
* - `criterion`
  - **yes**
  - The metric carrying the verdict. Must be declared by some validator.
* - `reference`
  - **yes**
  - The cell every gap is measured against.
* - `validity`
  - no
  - Metrics that must be true for a run to enter an aggregate.
* - `draws`
  - no
  - Resampling draws. Default 10 000.
* - `seed`
  - no
  - Resampling seed. Default 20260729.
```

`reference` takes two forms because there are two ways to name a cell:

```toml
reference = { context = "nothing", thinking = "off" }   # a grid
reference = "nothing"                                   # a variant
```

A partial grid reference raises, as does a reference that is not a cell, and a
criterion no validator declares.

:::{warning}
**`validity` must match the task.** `delivered` requires a file to have changed - the
right condition for a task that asks for a diff, and exactly the wrong one for a task
that asks for prose. Getting it wrong eliminates the whole matrix, and the error names
which condition removed how many runs.
:::
