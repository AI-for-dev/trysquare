# Troubleshooting

Every refusal below is deliberate. The message is meant to be enough on its own; this
page adds the reasoning.

Most of them are reached by `trysquare validate <scenario>`, which costs nothing and
needs no output directory. When something is wrong with a scenario, that is the cheap
place to find out.

## "these files the scenario references do not exist"

```text
refused: these files the scenario references do not exist:
  cell 'rule / off' -> context: /path/experiments/context/AGENTS.md
Paths are relative to the scenario file.
```

**Paths are relative to the scenario file**, not to the project root or the working
directory. A scenario in `experiments/` referring to `context/x.md` means
`experiments/context/x.md`; from one directory further down, write `../context/x.md`.

:::{admonition} Why this check exists
:class: note

It did not, once, and it cost a matrix. `read_brick` fell back to treating an
unresolvable path as *inline text*, so `prompt = "tickets/vague.md"` was sent to the
agent as the literal string `tickets/vague.md`. Twelve runs were paid for, and every one
looked normal in the log.

Now a value that looks like a path and does not exist raises, and the whole scenario is
checked **before the first token**.
:::

A skeleton straight out of `trysquare init` hits this on purpose, and names the one
file it deliberately did not write:

```text
refused: these files the scenario references do not exist:
  validation[script].command: /path/my-experiment/score.py
```

Nothing runnable ships: an experiment is about your repository and your question, so a
shipped validator would score somebody else's. Write `score.py` -
`examples/validator.py` is a whole one - and the refusal goes away.

## "init never overwrites"

```text
refused: scenario.toml, prompt.md, hypothesis.md already in /path/my-experiment.
init never overwrites; move them or point it elsewhere
```

Task material is experimental input: once a matrix has been published from a prompt,
replacing that prompt with a placeholder changes what the numbers mean. So `init`
refuses the whole directory rather than skipping the files it would clobber - a partial
write leaves a scenario half from one experiment and half from a skeleton.

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

## "`pi` is not on PATH"

```text
error: 'pi' is not on PATH
```

The agent binary is what a run launches, so a run without it refuses before writing
anything. A `--dry-run` does not refuse - it has nothing to launch - but it says the
same thing as a warning, which is where you want to find out:

```text
  ! 'pi' is not on PATH: a real run will refuse
```

Everything else in this tool is offline: loading, scoring, aggregation, verdicts,
`render`, `replay`, `compare` and `parity` all work without the binary.

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
trysquare replay results/<experiment> --scenario <scenario>.toml --rescore
```

That rebuilds each tree from the tag and the archived diff, re-runs the script validators
against it, and rewrites `measures.json`, `state.json` and the synthesis. No tokens. The run
whose validator failed becomes valid again, and the matrix publishes.

A validator failure is deliberately *not* resumable, because re-measuring a run that
already produced a result would let a resume change it.

The same command fills a metric declared *after* a matrix ran: add the name to the
scenario's `metrics`, return it from the validator, and every archived run is scored on it
at once. That is what "a metric already paid for can be scored later" means in practice.

## "this scenario names ..., and you asked to re-score ..."

```text
refused: this scenario names rule-vs-ticket_etalon-v1_ilaas_gemma-4-31b_n10, and you
asked to re-score other-experiment_etalon-v1_ilaas_gemma-4-31b_n10. A directory name is
the experiment's identity, so re-scoring across the two would rewrite measures that are
not its own
```

A directory name is built from the scenario, the etalon, the provider, the model and the
repetition count - so it *is* the experiment's identity, and comparing it with the name
the given scenario asks for catches a mismatched `--scenario` before anything is written.
Point `--scenario` at the file that measured this directory.

## A validator refuses "the context carries no ..."

```text
the context carries no 'response', so the agent's final prose cannot be read. A
harness older than this validator writes a context without it
```

Expected after `replay`, and it is the honest answer. The prompt and the agent's final
prose lived in the work directory, which the system may purge, and the raw stream is
deliberately never archived - so a replayed context cannot carry them, and a validator
that reads one refuses **by name** instead of scoring on material it does not have.

That named refusal is also why an archived context needs no version number: "the context
carries no 'response'" tells a reader more than "this archive is version 1" ever could.
A metric of process is replayable, though: the tool calls are in the archived session.

The consequence worth watching for is on the **other** side of a `--rescore`. A metric
that answered when the matrix ran becomes unjudged on a replay, so it drops out of the
score table - and re-scoring to fix an unrelated metric is enough to lose it. The command
says so, one line per metric:

```text
  ! documented no longer has a value on 6 of 6 runs: the context carries no 'response'
```

Nothing is corrupted, and the runs are untouched. The previous `measures.json` is in git,
which is where this archive keeps its history.

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

## "`--only` names no cell of this scenario"

```text
refused: --only names no cell of this scenario: 'rulle' (did you mean 'rule'?)
Cells: nothing, rule
```

A cell name that matches nothing used to be *filtered* rather than refused, so
`--only` with a typo in it ran zero runs and looked like an experiment with nothing
left to do. The refusal lists every cell, because a grid names its cells by joining
axis values (`rule / high`) and the exact spelling is easier to copy than to guess.

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
error: could not clone [repos] my-repo at etalon 'etalon-v1'
  url: https://host/my-repo.git
  git: fatal: Remote branch etalon-v1 not found in upstream origin
Nothing was measured. Fix the entry in /path/trysquare.toml, check network access, or
list what the remote has: git ls-remote --tags https://host/my-repo.git
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

## "[repos] has no entry"

```text
error: [repos] has no entry 'tinyy' (known: tiny) (did you mean 'tiny'?). Add it to
/path/trysquare.toml
```

The scenario names a repository logically and the config resolves the name, so the
refusal says what is known and which file to add the line to. The suggestion appears
only when the name is close to one that exists: a suggestion that is usually wrong
teaches the reader to skip all of them, so a far miss stays silent.

## "no trysquare.toml was found"

```text
error: the scenario names the repository 'tiny', and no trysquare.toml was found
walking up from the scenario's directory, so [repos] declares nothing. Create one
beside the scenario; two lines are enough:
  [repos]
  tiny = "/path/to/the/checkout"  # or a git URL
```

A different failure from the one above, and deliberately worded differently: there is
no file to edit. "Add it to trysquare.toml" reads as *edit that file* when the actual
fix is to create one, so the message writes out the two lines instead.

The search walks up from the **scenario's** directory, not the shell's: an experiment
carried to another machine must keep resolving against the config beside it.
`trysquare init` writes one when it finds none.

## A repository path does not exist

```text
error: [repos] my-repo resolves to /path/my-repo, which does not exist. Fix it in
/path/trysquare.toml
```

Relative paths in the config resolve against the config file, not against the current
directory. The check touches the disk, so like the URL failures above it happens when a
run starts rather than during a `--dry-run`.

## "refused: different etalons"

```text
refused: different etalons, etalon-v1 against etalon-v2
```

`compare` puts two experiments side by side, and a different etalon is a different
baseline - the two matrices did not measure the same thing, so there is nothing to
compare. Everything else that differs is *reported* rather than refused, because
comparing two providers or two models is the point of the command.

Cost columns are a separate matter, and not a refusal either:

```text
  ! cost columns set aside: retries 14 on the left, 0 on the right
    -> tokens and durations would reflect our own load
```

One retry on either side is enough to set them aside. Durations only compare within one
matrix, and a retry replays the turn with the whole accumulated context.

## No progress bar appears

By design, in three of the four cases: output is not a terminal (piped, redirected, or
captured by a test), `TERM=dumb`, `TRYSQUARE_NO_PROGRESS` is set, or `--no-progress` was
passed. A bar that lands in a log file is noise in the one place somebody reads later,
so off a terminal the output is exactly the bytes it has always been.

## The cursor is gone after a hard kill

`Ctrl-C` is handled - the bar restores the cursor on its way out - but a `SIGKILL` or a
closed terminal window leaves the live display no chance to. Any terminal recovers with:

```bash
tput cnorm
```
