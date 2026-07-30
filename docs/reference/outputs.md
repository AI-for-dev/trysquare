# Outputs

Everything is rooted at `--output`. One directory per experiment.

```text
<output>/<scenario>_<etalon>_<provider>_<model>_n<N>/
  state.json        the per-run ledger
  measures.json     one line per run
  synthesis.md      the gap table and the verdicts
  runs/<id>/
    context.json           what the validator was handed
    configuration.json     what this run actually ran
    diff.patch             what the agent changed
    validation/<mode>.json     each validator's output
    validation/<mode>.stderr   kept when a validator fails
```

## The directory name is the guard

The name carries the experiment's identity: scenario, etalon, provider, model and the
repetition count.

That is not decoration. Anything that changes what is measured changes the name, so
`--repetitions 3` writes to `..._n3/` and **cannot** overwrite a published matrix at ten.

Relaunching the same experiment **overwrites** it. The archive of previous versions is
git. A timestamped directory per launch would accumulate variants of one experiment to
choose between, which is optional stopping through the back door.

## `state.json`

The ledger, and what makes a matrix resumable.

```json
{
  "scenario": "2x3",
  "etalon": "etalon-v1",
  "provider": "ilaas",
  "model": "gemma-4-31b",
  "thinking": "off",
  "repetitions": 2,
  "concurrency": 5,
  "timeout": 900,
  "overrides": { "repetitions": 2 },
  "complete": true,
  "runs": {
    "658df337": { "cell": "nothing / off", "repetition": 0, "state": "valid", "attempts": 1 },
    "962d7594": { "cell": "nothing / off", "repetition": 1, "state": "empty", "attempts": 2 }
  }
}
```

Four states: `missing`, `empty`, `validator_failed`, `valid`.

Only `missing` and `empty` are **resumable** - the two that produced no result at all. A
`valid` run is never relaunched whatever its result, and `validator_failed` is re-scored
rather than re-measured.

`attempts` accumulates across resumes, so an abusive one leaves a trace.

The load is recorded whatever its origin, because retries depend on it and every cost
column depends on retries.

:::{note}
`state.json` also holds the **id-to-cell mapping**, which is deliberately *not* in the
manual scoring form: the form is blind.
:::

## `measures.json`

One entry per run, and the raw material every table is rebuilt from.

```json
[
  {
    "id": "658df337",
    "cell": "nothing / off",
    "repetition": 0,
    "usage": { "input": 14036, "output": 2286, "turns": 6, "retries": 2, "cost": 0.0 },
    "duration": 64,
    "metrics": { "overflow": true, "delivered": true, "tests": true, "issues": ["#1"] },
    "reasons": { "overflow": "addressed without being asked: #1" },
    "state": "valid",
    "attempts": 1
  }
]
```

Persisting **per-run** values rather than aggregates is what makes `render` possible.
Keeping only medians would make a matrix permanently unusable for a verdict, and a
matrix costs hours.

## `synthesis.md`

Written **only when the matrix is complete**. An incomplete matrix says so instead:

```text
This matrix is incomplete: 3 never launched, 1 produced nothing. No synthesis is
published; `--resume` completes it.
```

The gap table is the part to read. `*` established, `o` inconclusive, and no sentence
may rest on an `o`.

When retries are present, a warning follows the table and covers even results marked
established - see {doc}`../guide/invariants`.

`render --reference` writes `synthesis_ref-<cell>.md` from the same measures, because a
reference is a rendering choice rather than a measurement.

## `runs/<id>/`

The id is a short opaque hash, **stable** for a given scenario, cell and repetition -
stable so a resume can tell an absent run from a finished one, opaque so a form can be
filled without revealing the cell.

`configuration.json` records what actually ran, including per-agent models and where
each came from:

```json
{
  "cell": "+subagents",
  "etalon": "etalon-v1",
  "thinking": "high",
  "injected": ["AGENTS.md", ".pi/"],
  "agents": {
    "explorer": { "model": "ilaas/gemma-4-31b", "source": "scenario override" }
  }
}
```

Two places may declare a subagent's model, so the trace settles which one applied.

:::{note}
The **raw event stream is not archived.** It is almost entirely streaming deltas, which
teach nothing the per-message record does not: 15.9 MB of stream against 30 KB of
session. What is kept is the tag and the diff, which is exactly what `replay` needs to
reconstitute a tree.
:::

## Where clones and sessions live

Under `workdir` from the config, by default `$TMPDIR/trysquare`. Deliberately outside the
output directory, and deliberately disposable: the OS may purge it, and nothing of value
is lost because `replay` rebuilds a tree from a tag and a diff.

```text
<workdir>/
├── sources/neon-3f2a1b9c-etalon-v1/   a [repos] URL, pinned once at the tag
├── harness/subagent-v1.2/             a [harness] brick, pinned once at its tag
└── <experiment>/<run id>/             one clone, session and trace per run
```

`sources/` exists only for repository entries that are URLs; a `[repos]` entry naming a
directory is read where it already is. Because it lives under a disposable `workdir`, a
`--resume` against a URL after the directory has been purged clones again, and so needs
the network again.

Sessions live there too, which is why `parity --smoke` takes a `--workdir`.

(naming-gap)=
## A known gap in the naming scheme

The directory name carries the scenario name, etalon, provider, model and repetition
count. It does **not** carry the task, the cell definitions, or the rubric.

So editing a prompt - which is unquestionably part of what is measured - produces
results sharing a name with the previous ones while not being comparable to them. That
contradicts the principle the naming scheme exists to serve: *anything that changes what
is measured changes the name.*

This was found the hard way. A scenario's task had to be rewritten between two passes,
because the first version enumerated its own rubric and so measured instruction-following
rather than analysis. Only the repetition count kept the two sets of results apart.

:::{warning}
Until it is fixed: when you change a task, a cell definition or a rubric, **delete the
old output directory** rather than letting a later run overwrite part of it. Two
half-matrices under one name are worse than one missing matrix.

A fix would add a short digest of the resolved experiment - the brick contents that
actually reach the agent, plus the cells, validators and verdict - to the directory name,
so that a prompt edit lands somewhere new on its own.
:::

(not-implemented)=
## Not implemented yet

Stated rather than left to be discovered.

- **HTML output.** `synthesis.html` and a `pages` command are designed but not written;
  only `synthesis.md` is produced.
- **`compare` reports, it does not tabulate.** It applies the refusals and prints what
  differs, without a side-by-side table.
- **`replay` reconstitutes, it does not re-score.** Running the validators over the
  rebuilt trees and rewriting the measures is the remaining step.
- **Parity layer 2 is demonstrated but is not a command.** Two archived runs were
  reconstituted and re-scored by hand and matched exactly; `parity --archive` runs layers
  3 and 1.
