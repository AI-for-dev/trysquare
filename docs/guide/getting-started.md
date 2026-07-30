# Getting started

Fifteen minutes: install, plan a run without spending anything, measure, read the
output.

## Install

```bash
git clone <this repository> trysquare && cd trysquare
uv sync                      # or: pip install -e .
```

The tool has **no runtime dependencies**. Python >= 3.11 is the only requirement,
because TOML parsing uses `tomllib` from the standard library.

Check the test suite. It runs offline and takes about a second:

```bash
uv run python -m unittest discover -s tests -t .
```

:::{tip}
That suite includes an exact parity check against the published results of the tool
this one replaces. If it passes, the aggregation, the validity filter, the resampling
and the rendering all agree with numbers that were previously published - before you
measure anything of your own.
:::

## Point the config at your repository

A scenario never contains a machine path. It names a repository *logically*, and the
config file resolves that name:

```{code-block} toml
:caption: trysquare.toml
[repos]
neon = "../neon"             # relative paths are relative to this file
# neon = "https://github.com/org/neon.git"      # a URL works too

[harness]
subagent = "~/Work/Pi/subagent"

[defaults]
workdir = "$TMPDIR/trysquare"
concurrency = 5
timeout = 900
```

:::{warning}
A config file may only supply **machine paths and load fallbacks**. Setting
`provider`, `model`, `thinking`, `etalon` or `repetitions` here raises, and the
message says why: those decide what is measured, so they belong to the scenario.
Otherwise the same scenario file would measure something different on another
machine, which is exactly the defect that once made a thinking-level cell identical
to its baseline in every published matrix.
:::

A repository entry may be a git URL rather than a directory. It is cloned once, at the
scenario's etalon tag, under `workdir`, and every run clones from there - so nothing has
to be cloned by hand first. See [`[repos]`](../reference/config-schema.md#repos).

## Plan a run without spending anything

```bash
uv run python -m trysquare run scenarios/2x3.toml --output out --dry-run
```

```text
A project rule against a well written ticket, at two reasoning budgets
  6 cells x 10 repetitions
  etalon etalon-v1 of /path/to/neon
  output out/2x3_etalon-v1_ilaas_gemma-4-31b_n10
  60 runs to perform
    3b72b8b4  careful ticket / high  #0
    78ef8aaf  careful ticket / off  #0
    77e47073  nothing / high  #0
    ...
  dry run: nothing was spent
```

Two things worth noticing in that listing.

**The runs are interleaved**: all six cells at repetition 0, then all six at
repetition 1. That is not cosmetic. Interleaved runs see the same provider load,
which is the only reason durations are comparable between cells of one matrix.

**The directory name carries the experiment's identity** - scenario, etalon,
provider, model, and the repetition count. That is also its guard: a quick run at
`--repetitions 3` writes to `..._n3` and *cannot* overwrite a published matrix at ten.

`--dry-run` writes nothing at all, not even the output directory. That was once
false, and a dry run against an existing experiment reset its ledger.

## Measure

```bash
uv run python -m trysquare run scenarios/2x3.toml --output out
```

```text
  ok nothing / off             64s  14036 in / 2286 out  6 turns  2 retries
  ok rule / high              101s  41341 in / 4452 out  8 turns  2 retries
  !! careful ticket / off       0s      0 in /    0 out  0 turns  0 retries  empty: no tokens consumed
```

`ok` means the run counted; `!!` means it did not, with the reason. A run that
consumed no tokens is **not** recorded as a well-behaved agent - it is recorded as
having produced nothing, and it is excluded from every aggregate.

### If it refuses to start

The harness checks what it can before spending anything:

```text
error: these files the scenario references do not exist:
  cell 'rule / off' -> context: /path/to/bricks/AGENTS.md
Paths are relative to the scenario file.
```

```text
refused: the scenario declares thinking = 'off', and the machine's
defaultThinkingLevel is 'high'.
A subagent cannot declare its thinking level, so subagents would run at 'high'
while the cell claims 'off'.
```

Both are deliberate. See {doc}`troubleshooting`.

## Read the output

```text
out/2x3_etalon-v1_ilaas_gemma-4-31b_n10/
  state.json      per-run ledger: cell, state, attempt count
  measures.json   one line per run, the raw material every table is rebuilt from
  synthesis.md    the gap table and the verdicts
  runs/<id>/
    context.json         what the validator was handed
    configuration.json   what this run actually ran
    diff.patch           what the agent changed
    validation/          each validator's output and stderr
```

The synthesis is the part to read:

```text
### Gap to `nothing / off`, 95% interval by resampling

10000 draws, seed 20260729: the verdict is reproducible.

| cell                     | in        | out      | turns | duration | overflow    |
| ------------------------ | --------- | -------- | ----- | -------- | ----------- |
| rule / off               | +11 502 o | -284 o   | +1 o  | -8 o     | -10 pts o   |
| rule / high              | +43 248 * | +4 767 * | +2 o  | +144 *   | -80 pts *   |
| careful ticket / off     | +15 929 o | -296 o   | +3 *  | +7 o     | -100 pts *  |

`*` established, the interval excludes zero - `o` inconclusive.
```

Read that as: **only the `*` rows may be written about.** An `o` is shown rather
than hidden - hiding a measurement would be another dishonesty, and the dispersion
is exactly what a reader needs - but no sentence may rest on one.

## Rebuild a table without remeasuring

Measuring and scoring are separate. When you find a scoring defect - and you will -
fixing it must not cost another matrix:

```bash
uv run python -m trysquare render scenarios/2x3.toml -o out
uv run python -m trysquare render scenarios/2x3.toml -o out --reference "rule / off"
```

The second writes `synthesis_ref-rule-off.md` from the same measures. A reference is
a **rendering choice, not a measurement**, and changing it is how you read an
interaction without paying twice.

## Next

- {doc}`concepts` - the vocabulary everything else assumes
- {doc}`writing-a-scenario` - every key, and what it is for
- {doc}`invariants` - the eight rules, and the defect behind each
