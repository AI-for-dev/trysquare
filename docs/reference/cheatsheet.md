---
myst:
  html_meta:
    "description": "Every trysquare command, every flag, and what it costs - on one page."
---

# Cheat sheet

Every command, every flag, and what it costs. {doc}`cli` is the same surface with the
reasoning behind each refusal; this page is for remembering it, and nothing here is
explained twice.

```bash
uv run trysquare <command>       # from a checkout
python -m trysquare <command>    # from an installed environment
trysquare <command>              # after uv tool install
```

`--output` / `-o` roots everything that writes, one directory per experiment, and is
required by `run`, `render` and `form`.

## What each one costs

Seven of the eight cost nothing, which is why measuring and scoring are separate
commands at all.

```{list-table}
:header-rows: 1
:widths: 16 18 66

* - Command
  - Cost
  - What it does
* - `init`
  - <span class="ts-cost">free</span>
  - writes the skeleton of a new experiment, refusing to overwrite
* - `validate`
  - <span class="ts-cost">free</span>
  - checks a scenario end to end, without an output directory
* - `run`
  - <span class="ts-cost ts-cost-spends">tokens</span>
  - measures a scenario
* - `render`
  - <span class="ts-cost">free</span>
  - rebuilds the tables from stored measures
* - `replay`
  - <span class="ts-cost">free</span>
  - reconstitutes archived runs, and re-scores them with `--rescore`
* - `compare`
  - <span class="ts-cost">free</span>
  - compares two experiments, refusing what is not comparable
* - `parity`
  - <span class="ts-cost">free</span>
  - checks this harness against the previous bench, layer by layer
* - `form`
  - <span class="ts-cost">free</span>
  - generates or ingests a blind manual scoring form
```

Two flags spend wall clock rather than tokens: `render --html` (~0.3 s per archived
session) and `parity --smoke` (one clone per run).

## Set up

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} `init [directory]`

Writes `scenario.toml`, `prompt.md`, `hypothesis.md`, and a `trysquare.toml` when none
is found walking up. Default directory: here.

`score.py` is deliberately **not** written, so `validate` refuses the fresh skeleton by
name until the validator is yours.
:::

:::{grid-item-card} `validate <scenario>`

The file loads, every referenced path exists, `[repos]` has an entry, the thinking
precondition holds. No output directory, no token.

`--config <file>`
: config file; defaults to the nearest `trysquare.toml` walking up

`validate` cannot pass what `run` would refuse - the checks are shared. A missing `pi`
on `PATH` is a note, not a failure.
:::
::::

## Measure

::::{grid} 1
:gutter: 3

:::{grid-item-card} `run <scenario> -o <dir>` <span class="ts-cost ts-cost-spends">tokens</span>

`--config <file>`
: config file; defaults to the nearest `trysquare.toml`

`--repetitions N`
: override, **stamped into the directory name** (`..._n3/`)

`--concurrency N`
: override, recorded in `state.json` and the synthesis header

`--timeout N`
: override, recorded in `state.json`

`--only CELL`
: restrict to these cells, repeatable; **leaves the matrix incomplete**, so no
  synthesis is written and `--resume` completes it later

`--resume`
: fill only what produced nothing; a validator failure is re-scored instead

`--until-complete [N]`
: after a pass, relaunch the runs that produced nothing, at most N passes in total
  (default 3). Never a re-measurement

`--dry-run`
: show the whole plan and write nothing at all, not even the output directory

`--no-progress`
: never draw the live bar; also `TRYSQUARE_NO_PROGRESS=1`
:::
::::

It refuses before spending, on a missing brick, a thinking mismatch when the scenario
uses subagents, or an agent with no model. Every override is announced at launch -
`! OVERRIDE: repetitions 10 -> 3` - and relaunching the same experiment **overwrites**
it.

## Read the results

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} `render <scenario> -o <dir>`

`--repetitions N`
: which matrix to read

`--reference CELL`
: score another cell as the baseline, without remeasuring &rarr;
  `synthesis_ref-rule-off.md`

`--html`
: also export each archived session to a standalone page, in the run's own directory

`--no-progress`
: never draw the live bar
:::

:::{grid-item-card} `replay <dir> --scenario <file>`

Clones the etalon at its tag, applies the archived `diff.patch`, writes a fresh context
beside each reconstituted tree. Takes an experiment directory or one run inside it.

`--rescore`
: re-run the **script** validators, then rewrite `measures.json`, `state.json` and the
  synthesis

`--config <file>`
: config file

`usage`, `duration` and `attempts` are never touched, a judge is never re-run, and an
`empty` run is left alone.
:::

:::{grid-item-card} `compare <left> <right>`

Prints every difference between two experiments.

Hard refusal on different etalons. Cost columns set aside unless retries are near zero
on both sides. Rates only, and no verdict.
:::

:::{grid-item-card} `form <scenario> -o <dir>`

Shuffled, with cell names withheld - the same blinding as the judge.

`--ingest <form.toml>`
: merge a filled form back in

A manual metric may fill a hole but never overwrite a measured value.
:::
::::

::::{grid} 1
:gutter: 3

:::{grid-item-card} `parity [bench measures.json]`

Layers run cheapest first, and the command exits non-zero when an exact layer
disagrees.

`--archive <dir>`
: the bench's archived run directories &rarr; adds layer 1

`--scenario <file>`
: whose validators re-score the archive &rarr; adds layer 2, one clone of the etalon
  per run

`--smoke <dir>`
: an experiment directory: layer 4's mechanical checks

`--workdir <dir>`
: where sessions live, for the thinking check

`--reference CELL`
: default `base`

`--criterion METRIC`
: default `overflow`

`--config <file>`
: config file

```text
layer 1  stripping              exact   from archived sessions
layer 2  scoring                exact   from tag + diff.patch
layer 3  aggregation + verdict  exact   from published per-run rows
layer 4  launching the agent    not comparable, it samples
```
:::
::::

## On disk

```text
out/rule-vs-ticket_etalon-v1_ilaas_gemma-4-31b_n10/
  state.json      cells, runs, valid / empty / failed, attempt counters
  measures.json   one line per run
  synthesis.md    scores, costs, gaps and verdicts, when the matrix is complete
  synthesis.html  the same synthesis as a self-contained page
  runs/<id>/      context, configuration, diff, validation output
    session/*.jsonl   the agent's own trace, one file per attempt
    session/*.html    the same trace as a page, on render --html
```

The directory name *is* the experiment's identity, which is why an override that
changes the measurement lands in it.

## Exit codes

```{list-table}
:header-rows: 1
:widths: 10 90

* - Code
  - Meaning
* - `0`
  - Success. For `parity`, every checked layer holds.
* - `1`
  - A refusal, a failed check, or a rendering error. The message says which.
* - `2`
  - Usage error, or a missing required argument.
```

A refusal is exit 1 with a plain message, never a traceback.

## A validator, in one line

```text
score.py <path to context.json>
  -> {"metrics": {...}, "reasons": {...}} on stdout, exit 0
```

A boolean aggregates as a rate, a number as a median, anything else is diagnostic only.
A declared metric the validator omits makes the **run** invalid; extra metrics are
stored but carry no verdict.

## The invariants, in one line each

Each one is a defect that was paid for; {doc}`../guide/invariants` says which.

- **A run counts only if it consumed tokens.**
- **Nothing that changes a measurement may be inherited**, and there are no environment
  variables anywhere in this tool.
- **A validator that could not judge never yields a verdict** - invalid, not false.
- **Two states only: established or inconclusive**, on a resampled interval with a
  fixed seed.
- **Repetitions are declared in advance**, and a resume may only relaunch runs that
  produced nothing.
- **What the harness injects is excluded from scoring.**
- **A judge is blind, and where it cannot be, the harness says so.**
- **Durations compare only within one matrix.**
