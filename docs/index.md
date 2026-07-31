---
myst:
  html_meta:
    "description": "trysquare - a scenario harness for measuring coding agents reproducibly."
---

# trysquare

> A try square tells a joiner whether a joint is true. This one tells you whether a
> measured difference is.

A scenario harness for measuring coding agents reproducibly.

One scenario is one self-contained experiment in one TOML file: the task, the
configurations to compare, the protocol, and the validation. The harness runs it,
scores it, and **refuses to publish a difference that does not survive resampling**.

:::{admonition} Why this exists
:class: important

Measuring an agent is easy to get wrong in ways that look right.

The tool this one replaces produced **six published conclusions that collapsed on
rerun**, and twenty catalogued defects. Most were variations on a single mistake:
**a run that did not do the work reading as a run that worked well.** A provider cuts
the stream, the agent retries and returns turns that are real but empty. That run
breaks no rule, touches no file, and fails no test - so a naive harness records it as
exemplary.

Every rule in this tool is a defect that was paid for. The documentation says which
one, because a rule whose reason is lost gets removed by the next person who finds it
inconvenient.
:::

## Start here

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`rocket` Getting started
:link: guide/getting-started
:link-type: doc

Install, write a skeleton, plan a run for free, measure, read the output.
:::

:::{grid-item-card} {octicon}`book` Core concepts
:link: guide/concepts
:link-type: doc

Scenario, cell, brick, validator, verdict. The vocabulary the rest assumes.
:::

:::{grid-item-card} {octicon}`pencil` Writing a scenario
:link: guide/writing-a-scenario
:link-type: doc

Grids, variants, bricks, protocol. Every key explained.
:::

:::{grid-item-card} {octicon}`shield-check` The invariants
:link: guide/invariants
:link-type: doc

The eight rules that make a number publishable, and the defect behind each.
:::
::::

## Reference

```{toctree}
:maxdepth: 2
:caption: User guide

guide/getting-started
guide/concepts
guide/writing-a-scenario
guide/validators
guide/invariants
guide/parity
guide/troubleshooting
```

```{toctree}
:maxdepth: 2
:caption: Reference

reference/cli
reference/scenario-schema
reference/config-schema
reference/outputs
reference/api
```

## In one page

```bash
pip install -e .                                   # or: uv sync
cp trysquare.toml my-trysquare.toml                      # point [repos] at your repository

uv run trysquare run my-scenario.toml -o out --dry-run     # plan, spend nothing
uv run trysquare run my-scenario.toml -o out               # measure
```

```{code-block} toml
:caption: The shape of a scenario

[scenario]
name = "rule-vs-ticket"

[task]
repo = "my-repo"             # a logical name, resolved by the config file
etalon = "etalon-v1"         # a tag, cloned; never the working tree
prompt = "tickets/vague.md"  # relative to the scenario; inline text works too

[agent]
provider = "ilaas"           # mandatory, never inherited
model = "gemma-4-31b"        # mandatory
thinking = "off"             # mandatory

[protocol]
repetitions = 10             # declared in advance
concurrency = 5
timeout = 900

[axes]                       # a grid; declaration order fixes the table's order
context = ["nothing", "rule"]
thinking = ["off", "high"]

[values.context.rule]
context = "AGENTS.md"

[values.thinking.high]
thinking = "high"

[[validation]]
mode = "script"
command = "score.py"
metrics = ["in_scope", "delivered", "tests"]

[verdict]
criterion = "in_scope"
reference = { context = "nothing", thinking = "off" }
validity = ["delivered", "tests"]
```

## Requirements

Python >= 3.11: TOML parsing is `tomllib` from the standard library, which is why
the floor is there. `uv sync` - or `pip install -e .` - installs the rest.

:::{note}
Measuring anything also needs the agent binary (`pi`) on `PATH` and a provider you
have access to. Everything else - loading, scoring, aggregation, verdicts, the parity
checks - runs offline, which is why the methodological rules have tests at all.
:::

## Status

Working and tested, with gaps that are listed rather than left to be discovered.
See {doc}`reference/outputs` for what is produced today and
{ref}`not-implemented` for what is not.
