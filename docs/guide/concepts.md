# Core concepts

Seven words carry the whole tool. They are worth getting exactly right, because most
confusion about a measurement turns out to be confusion about one of them.

## Scenario

**One self-contained experiment, in one TOML file.** The task, the configurations to
compare, the protocol, and the validation.

Self-contained is a hard requirement, not an aspiration. Anything that decides *what
is measured* - provider, model, thinking level, etalon, repetitions - is mandatory in
the scenario and can never be supplied by a config file, an environment variable, or
a default.

:::{admonition} There are no environment variables in this tool
:class: warning

The tool this one replaces had ten. An environment variable is invisible
inheritance: a reader of the scenario cannot see it, the archive does not record it,
and the value that actually ran is whatever the shell happened to hold.

One of those ten silently decided the thinking level of **every measurement ever
published** with that tool, which meant the cell designed to test thinking was
identical to its baseline in every matrix. Nobody noticed for months.
:::

## Etalon

**The pinned state of the repository being measured**: a git tag, cloned fresh for
every run.

Never the working tree. `main` moves on, a classroom fixes an issue, and yesterday's
measures stop comparing with tomorrow's. A tag makes the measured state immutable
*and* named.

:::{note}
Cloning matters too, beyond immutability. A repository may be a git *worktree*, whose
`.git` is only a file pointing at a shared gitdir. A recursive copy would give every
run the same gitdir - so one agent running `git commit` would move the comparison
base of every run in flight, and nobody would see it happen.
:::

## Cell

**One configuration to measure**, and the delta that distinguishes it from the
baseline.

Cells come from a **grid**, from **named variants**, or from both:

```toml
[axes]                        # grid: the cartesian product
context = ["nothing", "rule", "careful ticket"]
thinking = ["off", "high"]    # -> 6 cells

[variants."full stack"]       # variant: irregular, named
harness = ["extension", "agents"]
```

A grid is concise where the design is regular; variants are precise where it is not.
Declaration order of the axes fixes the order of the rendered table.

:::{important}
**The baseline of an axis is its first value, and it declares no delta.** That shows
the baseline *is* a cell of the matrix, rather than a seventh cell standing beside it.

Every *other* value must declare a delta. Without that rule a misspelled axis value
produces a cell with no delta - a silent duplicate of the baseline, published twice
under two names. With it, the misspelling raises at load.
:::

## Brick

**A piece of harness handed to the agent by explicit path**: a context file, an
extension, a skill directory, an agent definition.

Bricks are always passed explicitly, never discovered. In the agent used here,
discovery is gated on project trust, walks up ancestor directories, and **fails
silently** - three separate ways for a cell to measure the absence of the brick it
believes it is measuring.

Anything the harness injects is added to `.git/info/exclude`. Without that, scope
scoring counts our own configuration as the agent's work, and every configured cell
drops to zero: a measurement of the tooling rather than of the behaviour under test.

## Validator

**Something that turns a finished run into named metrics.** Three modes:

`script`
: Any executable, in any language. Handed one argument - a context file - and prints
  `{"metrics": {...}, "reasons": {...}}`.

`judge`
: A pinned model call scoring against a rubric. Its verdict is a schema-checked tool
  call, not parsed prose.

`form`
: A blind, shuffled TOML file a human fills in. It may **fill** a metric nothing else
  produces; it may never overwrite a measured one.

Validators are **independent**: each receives the same context and cannot see what
the others found. That is not tidiness. A judge told the script's verdict is anchored
on it, and its agreement stops being an independent signal - which was the only
reason to have a judge.

## Metric

**A named value a validator returns.** Its *type* decides how it aggregates:

| type | aggregation | example |
| --- | --- | --- |
| boolean | rate | `overflow = true` -> 7/10 |
| number | median | `turns = 6` |
| anything else | diagnostic only | `issues = ["#1"]` |

There is no median of `["#1"]`, so a list is readable in a single run and can never
carry a verdict. A scenario has no place declaring types Python already knows.

`metrics` in a scenario is a **contract**. A validator that omits a declared metric
makes the *run* invalid; extra metrics are stored but cannot be scored, which keeps a
general-purpose validator reusable and lets a metric already paid for be scored later
without remeasuring.

## Verdict

**Whether a difference may be written about.** Two states, and only two.

A gap between a cell and the reference is resampled 10 000 times with a fixed seed,
and it is **established** if the 95% interval of the difference excludes zero.
Otherwise **inconclusive**.

:::{admonition} Why two states and not three
:class: important

A third state invites a reading where a gap is "almost" something, and almost is how
six conclusions got published and then collapsed.

The seed is fixed for a related reason: a verdict that is not reproducible would make
the harness itself a source of irreproducibility.
:::

A verdict judges a **gap**. An isolated measurement - "the glow costs 23% of a frame
budget" - asserts no effect: it is published with its dispersion and no verdict.

## How they fit together

```text
scenario ──┬─> cells ──────┐
           ├─> etalon ──> clone ──> + bricks ──> agent run ──> raw stream
           ├─> protocol                                            │
           └─> validators <─────────────────────────────────────────┘
                    │
                    v
                 metrics ──> aggregate per cell ──> gap vs reference ──> verdict
                    │                                                      │
                    └──> measures.json (kept per run) ─────────────────> synthesis.md
```

The arrow back from `measures.json` is the important one. Per-run values are
persisted, so any table or verdict can be rebuilt without remeasuring. Keeping only
aggregates would make a matrix permanently unusable for a verdict, and a matrix costs
hours.
