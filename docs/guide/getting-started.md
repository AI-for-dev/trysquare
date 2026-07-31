# Getting started

Fifteen minutes, and nothing is spent until the last step: install, write a
skeleton, check it, plan the run, measure, read the output.

```bash
trysquare init my-experiment && cd my-experiment
trysquare validate scenario.toml             # free
trysquare run scenario.toml -o out --dry-run # free
trysquare run scenario.toml -o out           # spends
```

## Install

```bash
git clone <this repository> trysquare && cd trysquare
uv sync                      # or: pip install -e .
uv tool install .            # puts trysquare on PATH
```

Python >= 3.11 is the floor, because TOML parsing uses `tomllib` from the standard
library.

Without that last line, every command below needs `uv run` in front of it, from inside
the clone. `python -m trysquare` runs the same subcommands, which is what the tests do.

Check the test suite. It runs offline and takes about fifteen seconds:

```bash
uv run --group dev pytest
```

:::{tip}
That suite includes an exact parity check against the published results of the tool
this one replaces. If it passes, the aggregation, the validity filter, the resampling
and the rendering all agree with numbers that were previously published - before you
measure anything of your own.
:::

## Start from a skeleton

No scenario ships with the tool: an experiment is about your repository and your
question, so a shipped one would be somebody else's. What ships is the shape.

```bash
trysquare init my-experiment       # default: the current directory
```

```text
  written my-experiment/scenario.toml
  written my-experiment/prompt.md
  written my-experiment/hypothesis.md
  written my-experiment/trysquare.toml

Yours to make it an experiment:
  1. point [repos] my-repo at your repository, in trysquare.toml
  2. set provider, model and the etalon tag in scenario.toml
  3. replace prompt.md with the task, and hypothesis.md with the bet
  4. write score.py - examples/validator.py in the trysquare repository is a whole one
then, at no cost: trysquare validate my-experiment/scenario.toml
```

The skeleton is deliberately **not runnable**: `score.py` is yours to write, and
`validate` refuses the fresh skeleton by name until it exists. `init` never
overwrites, and it writes `trysquare.toml` only when none is found walking up - so a
second experiment beside the first shares the machine's paths instead of forking them.

:::{tip}
`examples/scenario.toml` in this repository is the other half of the same answer: a
whole scenario, readable end to end, wired to the test fixture. The suite dry-runs it,
so it cannot rot. Copy that one when reading a finished scenario is easier than filling
in placeholders.
:::

The rest of this page runs from inside that directory (`cd my-experiment`).

## Point the config at your repository

A scenario never contains a machine path. It names a repository *logically*, and the
config file - the `trysquare.toml` `init` just wrote - resolves that name:

```{code-block} toml
:caption: trysquare.toml
[repos]
my-repo = "../my-repo"       # relative paths are relative to this file
# my-repo = "https://github.com/org/my-repo.git"     # a URL works too

[harness]
subagent = "~/work/my-extension"

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

## Check it, before there is an output directory

```bash
trysquare validate scenario.toml
```

```text
A project rule against a well written ticket, at two reasoning budgets
  6 cells x 10 repetitions
  etalon etalon-v1 of /path/to/my-repo
  script validator, owning: delivered, in_scope, tests
  judge validator, owning: overflow
  judge: blind over 6 cells (pieces: prompt, response, diff)
ok: nothing this scenario references is missing
```

Everything `run` would refuse, refused here: the file loads, every referenced path
exists, the config has an entry for the repository, the thinking precondition holds.
The refusals are *shared* with `run` rather than reimplemented, so `validate` cannot
pass what a run would reject - which is what makes it worth putting in a CI hook.

It writes nothing and needs no `--output`. Use it while editing; use `--dry-run`
below once the scenario is settled and the question becomes what the matrix will cost.

## Plan a run without spending anything

```bash
trysquare run scenario.toml --output out --dry-run
```

```text
A project rule against a well written ticket, at two reasoning budgets
  6 cells x 10 repetitions
  etalon etalon-v1 of /path/to/my-repo
  output out/rule-vs-ticket_etalon-v1_ilaas_gemma-4-31b_n10
  60 runs to perform
  at most ~180 min: 60 runs, 5 at a time, 900s timeout each
  spend, from this experiment's archive: median $0.12 over 40 valid runs
    -> ~$7.20 for 60 to perform
    3b72b8b4  careful ticket / high  #0
    78ef8aaf  careful ticket / off  #0
    77e47073  nothing / high  #0
    ...
  dry run: nothing was spent
```

Four things worth noticing in that listing.

**The duration is a bound, the spend is an estimate.** `runs / concurrency x timeout`
is arithmetic on numbers the scenario declared, so it cannot be optimistic. The spend
is the median cost of the valid runs *this experiment's archive already holds*, times
the runs to perform - never a price list, because a maintained price table goes stale
silently and a wrong estimate is worse than none. A scenario that has never run says
`spend: no archived run to estimate from` rather than guessing.

**A ledger that already exists is announced.** Relaunching overwrites it, so the plan
says so before the first token:

```text
  ! OVERWRITE: rule-vs-ticket_etalon-v1_ilaas_gemma-4-31b_n10 exists, 3 of its runs
    produced nothing. Relaunching resets the whole ledger; --resume relaunches only those 3
```

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
trysquare run scenario.toml --output out
```

```text
  ok nothing / off             64s  14036 in / 2286 out  6 turns  2 retries
  ok rule / high              101s  41341 in / 4452 out  8 turns  2 retries
  !! careful ticket / off       0s      0 in /    0 out  0 turns  0 retries  empty: no tokens consumed
```

`ok` means the run counted; `!!` means it did not, with the reason. A run that
consumed no tokens is **not** recorded as a well-behaved agent - it is recorded as
having produced nothing, and it is excluded from every aggregate.

A matrix at ten repetitions runs for hours, and a cut stream leaves a handful of runs
empty. `--until-complete` finishes it without a second command:

```bash
trysquare run scenario.toml --output out --until-complete
```

```text
  pass 2 of at most 3: 3 runs produced nothing, relaunching them and only them
```

That is `--resume`, bounded and automatic. It is not optional stopping: no pass can
reach a run that produced a result, and attempts stay counted per run in `state.json`.

### If it refuses to start

The harness checks what it can before spending anything:

```text
refused: these files the scenario references do not exist:
  cell 'rule / off' -> context: /path/to/AGENTS.md
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
out/rule-vs-ticket_etalon-v1_ilaas_gemma-4-31b_n10/
  state.json      per-run ledger: cell, state, attempt count
  measures.json   one line per run, the raw material every table is rebuilt from
  synthesis.md    the score, cost and gap tables, and the verdicts
  synthesis.html  the same synthesis as one self-contained page
  runs/<id>/
    context.json         what the validator was handed
    configuration.json   what this run actually ran
    diff.patch           what the agent changed
    validation/          each validator's output and stderr
    session/*.jsonl      the agent's own trace, one file per attempt
```

`synthesis.html` is written wherever the markdown is, by `run`, `render` and
`replay --rescore` alike. It costs nothing - strings in, one file out - and it carries
no script, no stylesheet and no font fetched from anywhere, because an archive is
opened years later on a machine that may be offline and a page that phones home is a
page that rots. `render --html` adds a page per archived session, and the synthesis
then links them.

The synthesis is the part to read. It opens with what each cell did, test by test:

```text
### Scores, cell by test

| cell                     | overflow | delivered | in_scope | tests |
| ------------------------ | -------- | --------- | -------- | ----- |
| nothing / off            | 10/10    | 10/10     | 10/10    | 10/10 |
| rule / off               | 9/10     | 10/10     | 9/10     | 10/10 |
| careful ticket / off     | 0/10     | 10/10     | 10/10    | 9/10  |

`x/n`: the test was true in `x` of the `n` runs that could judge it.
```

A denominator below the repetition count is a signal, not noise: a run left out as
invalid, or a metric the validator could not judge on that run. "Could not say" is
never recorded as "said false".

Then what it cost, as a level rather than a difference:

```text
### Cost, median and 95% interval by resampling

| cell                     | n  | in                      | out                  | duration (s)  |
| ------------------------ | -- | ----------------------- | -------------------- | ------------- |
| nothing / off            | 10 | 15 929 [14 208, 17 440] | 2 286 [1 902, 2 671] | 64 [58, 79]   |
| rule / high              | 10 | 43 248 [38 100, 51 002] | 4 767 [3 998, 5 512] | 208 [166, 252]|
```

Read this to decide whether a configuration is affordable, and nothing more: two
intervals that do not overlap are **not** a result. The comparison that carries a
verdict is the next table, and it is computed over the same runs - valid, and passing
`[verdict].validity`, which is why `n` is here.

Then the gaps, which is where a conclusion may come from:

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
trysquare render scenario.toml -o out
trysquare render scenario.toml -o out --reference "rule / off"
```

The second writes `synthesis_ref-rule-off.md` - and `synthesis_ref-rule-off.html`
beside it - from the same measures. A reference is a **rendering choice, not a
measurement**, and changing it is how you read an interaction without paying twice.

## Next

- {doc}`concepts` - the vocabulary everything else assumes
- {doc}`writing-a-scenario` - every key, and what it is for
- {doc}`invariants` - the eight rules, and the defect behind each
