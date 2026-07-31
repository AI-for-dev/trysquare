# Outputs

Everything is rooted at `--output`. One directory per experiment.

```text
<output>/<scenario>_<etalon>_<provider>_<model>_n<N>/
  state.json        the per-run ledger
  measures.json     one line per run
  synthesis.md      the score table, the cost table, the gap table and the verdicts
  synthesis.html    the same synthesis as one self-contained page
  runs/<id>/
    configuration.json     what this run actually ran
    diff.patch             what the agent changed
    session/<id>.jsonl         the agent's own trace, one file per attempt
    session/<id>.html          the same trace as a page, on `render --html`
    validation/<mode>.json     each validator's output
    validation/<mode>.stderr   kept when a validator fails
    validation/<mode>/context.json   what that validator was handed
```

`context.json` lives under its validator, not at the run's root: every validator gets
its own, and a judge's is blinded where a script's is not. One file at the root would
have to be two files under one name.

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
  "scenario": "rule-vs-ticket",
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

Three tables, in this order.

The **score table** says what each cell did: cells in rows, in the order the scenario
declares them, and one column per declared boolean metric.

```text
| cell                 | overflow | delivered | in_scope | tests |
| -------------------- | -------- | --------- | -------- | ----- |
| nothing / off        | 10/10    | 10/10     | 9/10     | 9/10  |
| rule / high          | 2/10     | 10/10     | 8/9      | 9/10  |
| careful ticket / off | 0/10     | 10/10     | 9/10     | 9/10  |
```

`x/n` counts the runs where the test was true out of the runs that could judge it. `n`
is the repetition count on a published matrix, and anything below it is the signal to
read: a run left out as invalid, or a metric a validator returned as `unjudged` - which
shrinks that one denominator and no other. A metric that is a number or a diagnostic
has no `x/n`, so it is named under the table rather than dropped from it.

The table is deliberately **not** filtered by `[verdict].validity`: those metrics are
columns of this very matrix, and a `delivered` column reading 10/10 by construction
would hide the thing it is there to show.

The **cost table** says what a run cost: tokens in, tokens out and duration, each as a
median with a 95% interval from the same resampling.

```text
| cell                 | n  | in                      | out                  | duration (s) |
| -------------------- | -- | ----------------------- | -------------------- | ------------ |
| nothing / off        | 10 | 15 929 [14 208, 17 440] | 2 286 [1 902, 2 671] | 64 [58, 79]  |
```

Levels, not gaps, so they carry **no state**: an isolated measurement asserts no
effect, and two intervals that do not overlap are not a result. It is computed over the
runs the verdict rests on - valid, and passing `[verdict].validity` - because a run that
delivered nothing is cheap by construction, and averaging it into a price makes the
configuration that fails most often look like the affordable one. `n` says how many runs
that left. `turns` is not here: it is a shape of the conversation rather than a price,
and it is read against the reference or not at all.

The **gap table** is the part a conclusion rests on. `*` established, `o` inconclusive,
and no sentence may rest on an `o`.

When retries are present, a warning follows the table and covers even results marked
established - see {doc}`../guide/invariants`.

`render --reference` writes `synthesis_ref-<cell>.md` from the same measures, because a
reference is a rendering choice rather than a measurement.

## `synthesis.html`

Written beside the markdown, every time the markdown is - by `run`, `render` and
`replay --rescore` alike. Costs no token and no network: strings in, one file out.

One self-contained page, with no script, no external stylesheet and no font fetched
from anywhere, because an archive is opened years later on a machine that may be
offline and a page that phones home is a page that rots. It links each run's session
pages once `render --html` has written them, and `render --reference` gives it the
same suffix as the markdown.

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

### `session/*.jsonl`

The agent's own trace, in the format the agent writes: **one file per attempt**, so the
count matches `attempts` in `state.json`. A run that produced nothing archives its
session too - it is the only evidence such a run leaves, and it is the run somebody most
wants to read.

Copied here byte for byte rather than left where it was written. The work directory is
disposable by design, so an archive that pointed at it would keep the diff and lose the
reasoning behind it on the next reboot.

Relaunching an experiment **replaces** this directory, exactly as it replaces the rest.
A session left by the previous launch would otherwise be attributed to this one - the file
count would stop matching `attempts`, and a page rendered from the old trace would sit
there looking current.

:::{note}
The **raw event stream is still not archived**, and the distinction matters. The *stream*
is what `pi --mode json` prints while it works, almost entirely streaming deltas: 15.9 MB
of it against 30 KB of session, teaching nothing the per-message record does not. The
*session* is the per-message record, and that is what is kept.
:::

(session-html)=
### `session/*.html`, on `render --html`

Each archived session renders to a standalone page beside it, under the same stem:
`session/<id>.jsonl` gives `session/<id>.html`. The page embeds the whole session and
loads nothing from the network, so it opens from a published archive on a machine that
has none.

The rendering is done by `pi --export`, which is to say by the agent itself. A renderer
written here would drift from the format it renders, silently, and the format is the
agent's rather than ours.

It costs no tokens and it is opt-in - see {doc}`cli`.

## Where clones and sessions live

Under `workdir` from the config, by default `$TMPDIR/trysquare`. Deliberately outside the
output directory, and deliberately disposable: the OS may purge it, and nothing of value
is lost because `replay` rebuilds a tree from a tag and a diff.

```text
<workdir>/
├── sources/my-repo-3f2a1b9c-etalon-v1/  a [repos] URL, pinned once at the tag
├── harness/subagent-v1.2/             a [harness] brick, pinned once at its tag
└── <experiment>/<run id>/             one clone, session and trace per run
```

`sources/` exists only for repository entries that are URLs; a `[repos]` entry naming a
directory is read where it already is. Because it lives under a disposable `workdir`, a
`--resume` against a URL after the directory has been purged clones again, and so needs
the network again.

Sessions are **written** there and **copied** into the archive, which is why
`parity --smoke` still takes a `--workdir`: it reads them where they were written.

A run's session directory survives from one launch to the next, since the run id is
stable and so the path is. So an archive takes only what the launch it belongs to
produced; copying whatever happened to be there would mix a previous measurement's traces
into an archive whose `measures.json` does not describe them.

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

- **A judge is not re-scored.** `replay --rescore` re-runs script validators and reuses the
  archived judge verdict, because re-running a judge costs tokens. Re-scoring a judged
  metric therefore means measuring again. Parity layer 2 inherits the limit: it re-scores
  what a script can score, and names the judged metrics as out of scope.
