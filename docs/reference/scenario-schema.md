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
  - A git tag, or a **commit written out in full** (40 hex characters), cloned fresh
    for each run. Never a branch or the working tree. A tag reads well and can be
    moved by whoever owns the repository, after which two matrices report the same
    etalon and measured different code; a commit cannot move, and puts what was
    measured in the directory name. `etalon_commit` records the resolution either way.
* - `prompt`
  - no
  - The task given to the agent: inline text, or a path to a file.
* - `test_command`
  - when `tests` is scored
  - The suite that decides the `tests` metric, **as you would type it**. Declared,
    never detected.
* - `prepare`
  - no
  - Commands to run before the suite, in order. A failure here means nobody judged.
* - `artefacts`
  - no
  - Path patterns for what running the task leaves behind and is not the agent's work.
```

`repo` being logical is what makes a scenario portable: it carries no author's
directory layout. A value containing `/` or `~` is a mistake the schema does not
prevent but the config resolution will.

`test_command` is required as soon as any validator declares the `tests` metric, and
the refusal happens at load time. Required by the *metric* rather than by the section:
a scenario that measures prose has no suite to name, and demanding one would be
ceremony.

```toml
test_command = "node --test 'game/**/*.test.js'"
```

**Declared and never detected**, which is the same lesson as the mandatory keys above
wearing different clothes. The obvious detection is `npm test`, whose meaning is read
from `package.json` - a file inside the perimeter the measured agent may edit. Broken
code plus a test script of `echo ok` scores green, and nothing in the output says so.
A detected command hands the choice of how a run is measured to the agent being
measured.

**A string, and it stays one.** The file is what you would type, `context.json` carries the
same string, and `split_command` turns it into an argv wherever one is needed - the loader
that vets it and the base that runs it come to the same rule, so what loads is what runs.
`shlex` is the shell's own word splitting, quotes included, so the rule is one every author
already knows - and a glob still works when the runner expands it itself, as `node --test`
does.

**No shell ever runs it.** A word that only means something to a shell - `&&`, `|`, `;`,
a redirection - is therefore **named and refused at load time**, rather than reaching the
runner as an argument and failing where nobody can read it.

### `prepare`

For the steps a suite needs before it can run:

```toml
[task]
prepare = ["npm ci"]
test_command = "npm test"
```

Separate from `test_command` because **their failures mean different things**, and the
difference is the one this whole tool is built around. A `prepare` that fails - no network,
a dependency that will not install - means *nobody judged*, so the metric is unjudged rather
than false. The suite failing is a measurement.

Conflated into one list, a broken network would score an agent **red** on a column that can
carry the scenario's `validity` condition - "could not judge" filed as "worked badly", one
level up.

Each entry is one command, under the same rules as `test_command`. A repository that
needs none is worth preferring: nothing to install is what makes a validation replayable
from a tag and a diff months later.

### `artefacts`

What running the task leaves behind that nobody wrote:

```toml
[task]
test_command = "python3 -m unittest discover -s tests -t ."
artefacts = ["__pycache__"]
```

:::{admonition} The defect
:class: danger

Without it, a matrix measured against a real provider scored `in_scope = false` in
**every run of every cell**. The only thing outside scope was `__pycache__/*.pyc`,
dropped by the agent running the declared suite to check its own fix. The criterion
saturated at zero, the gap the matrix existed to measure came out `+0 pts`, and six
paid runs concluded nothing.

Worse than noise, because it is not random: the runs that scored out of scope were the
ones where the agent bothered to verify itself. A second matrix, whose agents did not
run the suite, scored 3/3 on the same code.
:::

**Declared and never detected**, the same lesson as `test_command`. A built-in list
would be a guess about somebody else's language, and it would eventually hide a file an
agent really did write.

A pattern matches the **whole path** with shell globbing, so `*.pyc` catches
`__pycache__/counter.pyc`; or it matches **any component** of it, so `__pycache__`
catches that same file and `tests/__pycache__/t.pyc` without needing a `*`. A trailing
slash is accepted, since `node_modules/` is how the directory is usually written.

It filters what a **verdict** rests on and never what is **recorded**. `touched` stays
complete in the context and in `measures.json`, because hiding a measurement is the
other dishonesty this tool refuses. A validator subtracts:

```python
work = run.touched - run.artefacts
```

See {doc}`../guide/validators`.

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
context = "AGENTS.md"

[values.context."careful ticket"]
prompt = "tickets/careful.md"

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
harness = ["extension", "agents"]
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

[harness.local]                  # a single file, relative to the scenario
load = "extensions/my-hook.ts"

[harness.agents]                 # files copied into the clone
paths = ["agents/explorer.md", "agents/tester.md"]
model = "ilaas/gemma-4-31b"      # optional override, see below

[harness.skills]
paths = ["skills/profile"]
```

A brick with `repo` is cloned at its `tag` **once per matrix**, its dependencies are
installed, and every cell loads the same pinned state. An experiment that pins the
measured repository and lets the harness float is measuring the operator.

`kind` declares what a brick's `paths` are - `"skills"` or `"agents"`. Absent, the
brick named `skills` carries skills and every other brick carries agents. Declaring it
lets several skill bricks coexist, so variants compare skills one at a time instead of
loading one all-or-nothing brick:

```toml
[harness.skill-tdd]
kind = "skills"
paths = ["skills/tdd"]

[harness.skill-research]
kind = "skills"
paths = ["skills/research"]

[variants."+tdd"]
harness = ["skill-tdd"]

[variants."+research"]
harness = ["skill-research"]

[variants."+both"]
harness = ["skill-tdd", "skill-research"]
```

A `kind` outside the known values, or on a brick without `paths`, is refused at load
time: a misspelled kind would silently fall back to agents, and the cell would measure
subagents where its author declared skills.

### The `files` kind

The third kind, and the only one whose material lands in the **measured tree** rather
than in the agent library. It is how a cell hands the task a file the tag does not
hold - a probe, a fixture, a specification the repository's own test command will pick
up:

```toml
[harness.probe]
kind = "files"

[harness.probe.files]
"game/probe.test.js" = "bricks/probe.test.js"

[variants."+probe given"]
harness = ["probe"]
```

A table of **destination = source**, and not a list of `paths`, because a list gives
the destination no name: the file would land under whatever basename it happens to
carry in the scenario's directory, and the path a probe occupies decides whether
`node --test 'game/**/*.test.js'` finds it. That is a decision of the experiment.
Sources are relative to the scenario; destinations are relative to the clone and may
neither be absolute nor climb out of it.

:::{important}
**Given files are committed, not hidden.**

Every other brick is written into `.git/info/exclude`, because harness plumbing is not
the agent's work and scope scoring must not count it. A `files` brick is the exception:
its material is addressed to the *task*, and the agent may edit it or delete it.
Excluded, that would leave no trace anywhere - git ignores an untracked path however it
was left.

So the harness commits them on top of the etalon before the agent starts. The injection
still costs nothing in `touched` and nothing in `diff.patch`, since both are read
against `HEAD`; the moment the agent weakens a probe or deletes it, that shows up like
any other change. A replay puts the same files back, and commits them the same way,
before applying the patch.

**A `files` brick never replaces what the tag holds.** Overwriting a tracked file would
change the measured code while looking like nothing at all - the diff would be taken
against a HEAD that already contains the replacement. It is refused.
:::

:::{note}
`[harness.agents].model` is an optional override. Absent, each agent file declares its
own model, which keeps a cheap explorer alongside an expensive coder expressible.
Present, it overrides every file.

Either way the harness **refuses to inject an agent that would end up with no model**:
nine shipped agents once ran on the wrong provider and returned 402s because they
declared none. Two places may declare, so the trace settles which one applied -
`configuration.json` records the model used and where it came from.
:::

:::{important}
**A cell that injects agents also loads the subagent gate, without declaring it.**

Dropping agent definitions into the clone does not make them the only reachable ones.
The subagent tool takes its scope as a parameter the *model* chooses, and the default
reaches the agent library's own built-in agents - none of which declares a model, so
each would inherit whatever the operator's machine defaults to. A cell injecting
`explorer` would have measured someone else's agent on someone else's settings, and
nothing in the output would have said so.

`trysquare/agent-gate.ts` ships inside the package and is appended to the extensions
of any cell whose `paths` carry agents. It forces the scope and refuses
any agent name the scenario did not inject. It is not a `[harness]` entry, because
forgetting it was one line away from measuring the wrong thing.
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
