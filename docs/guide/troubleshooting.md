# Troubleshooting

Every refusal below is deliberate. The message is meant to be enough on its own; this
page adds the reasoning.

## "these files the scenario references do not exist"

```text
error: these files the scenario references do not exist:
  cell 'rule / off' -> context: /path/scenarios/bricks/AGENTS.md
Paths are relative to the scenario file.
```

**Paths are relative to the scenario file**, not to the project root or the working
directory. A scenario in `scenarios/` referring to `bricks/x.md` means
`scenarios/bricks/x.md`; write `../bricks/x.md`.

:::{admonition} Why this check exists
:class: note

It did not, once, and it cost a matrix. `read_brick` fell back to treating an
unresolvable path as *inline text*, so `prompt = "bricks/vague-ticket.md"` was sent to
the agent as the literal string `bricks/vague-ticket.md`. Twelve runs were paid for, and
every one looked normal in the log.

Now a value that looks like a path and does not exist raises, and the whole scenario is
checked **before the first token**.
:::

## "refused: the scenario declares thinking = ... "

```text
refused: the scenario declares thinking = 'off', and the machine's
defaultThinkingLevel is 'high'.
A subagent cannot declare its thinking level, so subagents would run at 'high'
while the cell claims 'off'.
Either set defaultThinkingLevel to 'off' in ~/.pi/agent/settings.json, or declare
thinking = 'high' in the scenario so the cell says what will actually run.
```

A subagent's reasoning level **cannot be declared anywhere**: the agent-definition
frontmatter has no field for it and the library passes no option, so it always comes
from the machine's settings.

What cannot be controlled is verified instead. Two real options:

1. align the machine setting, or
2. declare the ambient level in the scenario, so the cell says what will actually run.

**Removing `thinking` is not one of them** - it is mandatory, because an undeclared
level is an inherited one.

## "these agents declare no model"

```text
error: these agents declare no model and no override supplies one: tester.
They would inherit the operator's defaultModel, which is how nine agents once ran
on the wrong provider and returned 402
```

Add `model:` to the agent's frontmatter, or set `[harness.agents].model` to override
every file at once.

## "metric declared by two validators"

```text
error: metric declared by two validators: overflow. Rename one (for instance
overflow_judge)
```

Two validators cannot own one name. Rename one - naming is free, and prefixing every
metric with its validator would make a metric change name whenever it changes mode.

## "value 'x' declares no delta"

```text
error: axis 'context': value 'tickett' declares no delta. Only the first value of
an axis ('nothing') is the baseline. Deltas declared for this axis: ['rule',
'careful ticket']
```

Almost always a typo. The first value of an axis is the baseline and needs no delta;
every other value must declare one, so a misspelling cannot quietly become a duplicate
of the baseline.

## "reference cell ... has no run left to compare against"

```text
error: reference cell 'nothing' has no run left to compare against: no gap is
judgeable.
  2 runs, 2 of them valid
  eliminated by [verdict].validity: delivered (2 of 2)
  A validity condition must match the task: `delivered` requires a file to have
  changed, which is wrong for a task that asks for prose.

  The measures are intact in out/.../measures.json.
  Fix [verdict].validity and rerun `render` - no remeasuring needed.
```

Nothing is lost. **A validity condition must match the task**: `delivered` requires a
file to have changed, which is right for a task asking for a diff and wrong for one
asking for prose. Fix `[verdict].validity` and rerun `render`.

## Runs marked `empty`

```text
!! nothing / off    0s   0 in / 0 out  0 turns  0 retries  empty: no tokens consumed
```

The run produced nothing, so it is excluded from every aggregate rather than counted as
a well-behaved agent. It is **resumable**: `--resume` relaunches only runs that produced
nothing, because there is no result to select between.

Frequent `empty` runs usually mean the provider is cutting streams. Check the retry
counts in the successful runs.

## A validator failed

```text
!! rule / high   63s  16954 in / 1588 out  7 turns  validator_failed: validator 'script' exited 1
```

Read `runs/<id>/validation/script.stderr`. The run itself is fine - only the scoring
failed - so **it does not need remeasuring**: fix the validator and re-score.

```bash
trysquare replay results/<experiment> --scenario scenarios/<scenario>.toml --rescore
```

That rebuilds each tree from the tag and the archived diff, re-runs the script validators
against it, and rewrites `measures.json`, `state.json` and the synthesis. No tokens. The run
whose validator failed becomes valid again, and the matrix publishes.

A validator failure is deliberately *not* resumable, because re-measuring a run that
already produced a result would let a resume change it.

The same command fills a metric declared *after* a matrix ran: add the name to the
scenario's `metrics`, return it from the validator, and every archived run is scored on it
at once. That is what "a metric already paid for can be scored later" means in practice.

## The synthesis warns about cost columns

```text
:warning: The cost columns (in, out, turns, duration) must not be read here.
14 retries across the matrix ... including any of them marked established.
```

Not a bug. When the stream is cut, the agent replays the turn with the whole accumulated
context, so tokens, turns and duration inflate for reasons that have nothing to do with
the configuration. Measured previously: zero retries gave 4 turns and 15.9k input
tokens; thirteen gave 24 and 79.4k.

The criterion column is unaffected. Only cost is.

## No synthesis was written

The matrix is incomplete, which is a state rather than a failure:

```text
This matrix is incomplete: 3 never launched, 1 produced nothing. No synthesis is
published; `--resume` completes it, and a failed validator is re-scored by
`replay` at no token cost.
```

Expected after `--only`. Publishing a partial matrix as though it were whole is what
this prevents.

## A harness clone failed

```text
empty: RepoError: git clone ... failed: destination path already exists and is
not an empty directory
```

Fixed, but if a stale directory survives from an interrupted run, delete
`<workdir>/harness/` and rerun. Harness preparation is serialised behind a lock with a
readiness marker, so a half-written clone is redone rather than reused - reusing a
partial harness produces a plausible measurement, which is worse than a crash.

## A repository URL could not be cloned

```text
error: could not clone [repos] neon at etalon 'etalon-v1'
  url: https://host/neon.git
  git: fatal: Remote branch etalon-v1 not found in upstream origin
Nothing was measured. Fix the entry in /path/trysquare.toml, check network access, or
list what the remote has: git ls-remote --tags https://host/neon.git
```

Raised **before** anything is written, so nothing was spent and no output directory was
created. The same message covers the three causes, because they are the same clone: a URL
that is wrong, a network that is unreachable, and an etalon tag that does not exist
upstream. `git ls-remote --tags <url>` distinguishes the third.

Note that a `--dry-run` cannot catch any of them: a dry run spends nothing, and reaching a
network is spending.

## A resume against a URL wants the network again

The pinned clone lives under `workdir`, which is disposable and which macOS purges on
reboot. Once it is gone, the next `--resume` clones it again. That is intended: the
durable archive keeps sources, and the runs already paid for are still on disk and are not
paid for twice.

## A repository path does not exist

```text
error: [repos] neon resolves to /path/neon, which does not exist. Fix it in
/path/trysquare.toml
```

Relative paths in the config resolve against the config file, not against the current
directory. The check touches the disk, so like the URL failures above it happens when a
run starts rather than during a `--dry-run`.
