# Command line reference

```bash
uv run python -m trysquare <command> [options]
trysquare <command> [options]              # if installed
```

`--output` roots everything that writes. Seven commands.

## `run`

Measures a scenario.

```bash
trysquare run <scenario> --output <dir> [options]
```

```{list-table}
:header-rows: 1
:widths: 26 74

* - Option
  - Effect
* - `--output`, `-o`
  - **Required.** Directory every output is written under.
* - `--config`
  - Config file. Defaults to the nearest `trysquare.toml` walking up from the scenario.
* - `--repetitions N`
  - Override. **Stamped into the directory name.**
* - `--concurrency N`
  - Override. Recorded in `state.json` and the synthesis header.
* - `--timeout N`
  - Override. Recorded in `state.json`.
* - `--only CELL`
  - Restrict to these cells. Repeatable. Leaves the matrix **incomplete**.
* - `--resume`
  - Fill only what produced nothing.
* - `--dry-run`
  - Show the plan and write nothing at all.
* - `--no-progress`
  - Never draw the live bar. Also `TRYSQUARE_NO_PROGRESS=1`.
```

### The record scrolls, the bar is pinned

```text
  ok  rule / off               412s  15234 in / 812 out  4 turns  0 retries
  !!  careful ticket / high    901s  ...  timeout: no productive attempt
  ⠹ runs ━━━━━━━━━━╸━━━━━━━━━━  23/60  elapsed 1h 12m  left 1h 55m
```

Every per-run line still prints, unchanged, above the bar: those lines are the record,
and the bar is not. What the bar adds is the part a terminal never said - how many runs
are left, and when the matrix is expected to land.

**The estimate is the throughput since launch**, `elapsed / completed x remaining`, not
a recent rate. Runs finish in batches the width of `--concurrency`, so a sliding-window
estimate would swing by minutes every few seconds while the true arrival time barely
moves. Nothing is claimed until the first run lands.

**The bar is drawn only on a terminal.** Piped, redirected, or captured by a test, the
output is exactly the bytes it was before the bar existed - no escape sequences in a log
file. `--no-progress`, `TRYSQUARE_NO_PROGRESS=1` and `TERM=dumb` each turn it off on a
terminal too. `NO_COLOR` does not: it asks for no colour, not for no motion.

### Overrides are announced and stamped

```text
! OVERRIDE: repetitions 10 -> 3
! OVERRIDE: concurrency 5 -> 10
```

Every override is printed at launch. The previous tool had a protocol declared in a
document and defaults in the code that contradicted it - and the code wins at the
moment somebody types the command, so a published matrix was measured at the wrong
load. A plan that cannot be executed is not a plan.

Then, according to effect:

**What changes the measurement** enters the directory name. `--repetitions 3` writes
to `..._n3/`, so it *cannot* corrupt a published matrix at ten. This is not a check
bolted on; the name already carries the experiment's identity.

**What changes the load** stays in the same directory but is recorded, because it
conditions the retry count and therefore every cost column.

**`--only`** leaves the matrix incomplete, so no synthesis is written and `--resume`
completes it later. The two compose: a cell measured alone for debugging leaves a
resumable matrix rather than a dead end.

### `--dry-run` writes nothing

Not even the output directory. That was once false - `resolve()` performed side
effects belonging to `execute()`, so a dry run against an existing experiment reset
its ledger.

### Before spending anything

`run` refuses on three preconditions:

1. **Missing bricks.** Every referenced path is checked first.
2. **Thinking mismatch** when the scenario uses subagents.
3. **An agent with no model**, from neither its file nor an override.

The plan header states, before anything is spent: a duration **bound** (runs,
concurrency and timeout are declared, so `runs / concurrency x timeout` is
arithmetic, not a guess), a spend **estimate** when this experiment's archive
already holds valid runs (their median cost times the runs to perform - never a
price list), an `OVERWRITE` note when the output directory already holds a
ledger, and - on `--dry-run` - whether a real run would refuse for want of `pi`.

## `validate`

Checks a scenario end to end without an output directory, and without spending a
token: the file loads, every referenced path exists, the config has an entry for
the repository, the thinking precondition holds. The same refusals `run` applies,
shared with it - `validate` cannot pass what `run` would refuse. A note (not a
failure) says when `pi` is missing from PATH, since a run would then refuse.

```bash
trysquare validate <scenario> [--config <file>]
```

## `render`

Rebuilds tables from stored measures. Costs nothing.

```bash
trysquare render <scenario> -o <dir> [--repetitions N] [--reference CELL] [--html]
                                     [--no-progress]
```

Measuring and scoring are separate. When you find a scoring defect - and there were
three in one day on the previous tool - it would be absurd to pay for another hour of
wall clock to fix it. Per-run values are persisted for exactly this.

`--reference` writes a suffixed file from the same measures:

```bash
trysquare render my-scenario.toml -o out --reference "rule / off"
# -> synthesis_ref-rule-off.md
```

A reference is a **rendering choice, not a measurement**. Changing it is how an
interaction is read without paying twice, and it is why the previous tool's
hand-renamed `_ref-thinking` file was a symptom rather than a solution.

### `--html` rebuilds the session pages

```bash
trysquare render my-scenario.toml -o out --html
#   runs/658df337/session/2026-07-30T07-27-59-046Z_019fb1ec.html
#   ...
#   12 session pages written
```

One standalone page per archived session, written **in the run's own directory** beside
the jsonl it came from. The rendering is `pi --export`, so it costs no tokens and needs no
network - see {ref}`session-html`.

Opt-in, because it is the only part of `render` that spends wall clock: roughly 0.3 s per
session, and a matrix at ten repetitions holds sixty of them.

It runs **before the table and independently of it**. No synthesis is written for an
incomplete matrix, and an incomplete matrix is exactly when a trace is wanted, so a
missing table must not take the pages down with it.

Two things are said rather than left to be discovered:

- a run with no archived session is **counted and named** - an output tree measured before
  sessions were archived would otherwise answer with a silence that reads as success;
- a session that will not render is reported on stderr and skipped. One broken trace does
  not cost the rest, the same rule that applies to a run inside a matrix.

Without `pi` on `PATH` the command refuses with a message and exit 1, rather than writing
nothing and claiming success.

## `replay`

Reconstitutes archived runs so they can be re-scored. Costs no tokens.

```bash
trysquare replay <experiment dir or run dir> --scenario <scenario> [--config <file>]
trysquare replay <experiment dir> --scenario <scenario> --rescore [--no-progress]
```

Clones the etalon at its tag, applies the archived `diff.patch`, and writes a fresh
context beside each reconstituted tree, under `<workdir>/replay/<run id>/`. This is what
makes "fix a signature and re-score runs already paid for" true rather than aspirational:
the archive keeps the tag and the patch, not 150 copies of a working tree.

The context is written **fresh** rather than reused. The archived one holds absolute paths
into the work directory of the original run, which lives under `$TMPDIR` by default and
which the system may long since have purged - so it names a tree that no longer exists,
while the tree just rebuilt would be named by nothing. `touched` is recomputed from that
tree, `files` from the tag, and the session is the archived one.

### `--rescore`

Re-runs the scenario's **script** validators over the reconstituted trees, then rewrites
`measures.json`, `state.json` and the synthesis. Still no tokens.

This is what closes the loop. Without it, a corrected signature could be executed against
sixty archived runs and the sixty answers had nowhere to go: only `run` and `form` ever
wrote `measures.json`, so a reader had to assemble a second scoring path of their own -
which is exactly what a single harness exists to prevent.

What it may and may not touch:

`usage`, `duration`, `attempts`
: never. They are facts about the run, not about the scoring. In particular `attempts` is
  what leaves an abusive resume visible in `state.json`, and a re-scoring must not spend
  that.

`state.json`
: rewritten with the new per-run state, and it has to be. That file decides whether a
  synthesis is publishable at all, so a validator repaired here would otherwise stay
  counted among the failures and the matrix would keep refusing to publish.

`validation/<mode>.json`
: replaced by what the validator just returned. Leaving the old payload would put a
  `measures.json` and a `validation/script.json` side by side that disagree, with nothing
  to say which one is the score.

A **judge is not re-run**: its verdict costs tokens, and `replay` exists on the promise
that it costs none. The archived payload is reused instead, which is the right answer and
not a compromise - that verdict is a measurement somebody paid for, and correcting a script
metric must not silently discard it. A run whose archived judge verdict is missing is left
alone rather than scored short.

Two refusals, both before anything is written:

- a run whose state is `empty` is **left alone**. No scoring turns "produced nothing" into
  a measurement, and overwriting its state would hide an incomplete matrix behind a
  full-looking one.
- a directory that is not this scenario's is refused by name. A directory name *is* the
  experiment's identity, so comparing the two names catches a re-scoring that would rewrite
  measures belonging to another matrix.

:::{note}
Three things a replay cannot give back: `prompt`, `response` and `trace`. The first two
lived in the work directory, and the raw stream is deliberately never archived. A
validator reading one of them refuses **by name** - "the context carries no `response`" -
rather than scoring a run it could not see. That is also why there is no context version
number: a named absence says more than a version could.

A metric of **process** does replay, which is worth knowing because it looks as though it
should not: the agent's tool calls are in the archived session, not only in the discarded
stream.
:::

:::{note}
`--rescore` re-runs the validators one run at a time, in order. It is deliberately not
concurrent: a re-scoring is cheap, and the contexts live in per-run directories precisely
so that it *could* be, but nothing here needs it yet.
:::

## `compare`

Compares two experiments, refusing what is not comparable.

```bash
trysquare compare <left dir> <right dir>
```

**Hard refusal** on different etalons - a different baseline means the two measures
are not of the same thing.

**Cost columns set aside** unless retries are near zero on both sides, with the counts
shown.

Everything else is allowed and every difference is printed. A different model is a
legitimate comparison axis; it just has to be declared rather than hidden.

## `parity`

Checks this harness against the tool it replaces. See {doc}`../guide/parity`.

```bash
trysquare parity <bench measures.json> [--archive <traces dir>]
trysquare parity --smoke <experiment dir> [--workdir <dir>]
```

Without `--archive`, layer 3 only. With it, layers 3 and 1. With `--smoke`, layer 4's
mechanical checks over a matrix you measured.

## `form`

Generates or ingests a blind manual scoring form.

```bash
trysquare form <scenario> -o <dir>              # generate
trysquare form <scenario> -o <dir> --ingest <form.toml>
```

The form is **shuffled, with cell names withheld**, the same blinding as the judge and
for the same reason: someone who knows they are scoring the best-equipped cell scores
it better. The id-to-cell mapping lives in `state.json`, deliberately not in the form.

```toml
[run.a7f3]
diff = "runs/a7f3/diff.patch"
# readable_in_class =
```

TOML rather than markdown with blanks, for a mechanical reason: **an absent key is a
metric not yet filled**, so the file parses at any point without a bespoke parser.

On ingest, a manual metric may **fill** a hole but never overwrite a measured value:

```text
2 manual metrics merged, 38 still pending, 1 refused
  refused values would have overwritten a measured metric, which a form may never do
```

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

A refusal is exit 1 with a plain message, never a traceback. A rendering failure says
explicitly that the measures are intact and only `render` needs rerunning.
