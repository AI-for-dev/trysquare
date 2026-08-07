# The invariants

Eight rules decide whether a number may be written about. None is a style
preference: each is a defect that was paid for, and the defect is recorded because a
rule whose reason is lost gets removed by the next person who finds it inconvenient.

The family that produced most of them has one shape: **a run that did not do the work
reading as a run that worked well.** It appeared five times in five disguises in the
tool this one replaces.

## 1. A run counts only if it consumed tokens

```{code-block} python
:caption: trysquare/measure.py
def consumed_tokens(usage: dict) -> bool:
    return bool(usage.get("turns")) and bool(usage.get("input")) and bool(usage.get("output"))
```

A provider that cuts the stream leaves the agent retrying and then returning turns
that are **real but empty**. Such a run breaks no rule, touches no file, and fails no
test - so a naive harness records it as exemplary, and it drags the criterion toward
zero precisely in the slowest, most fragile cells.

Counting turns is not enough on its own: a run killed by the timeout also has real
turns. The filter is turns **and** input **and** output.

## 2. Nothing that changes a measurement may be inherited

`provider`, `model`, `thinking`, `etalon` and `repetitions` are mandatory in the
scenario. A config file cannot supply them, and there are no environment variables
anywhere in the tool.

:::{admonition} The defect
:class: danger

The previous tool read the thinking level from the operator's personal settings file.
So the cell designed to test thinking was **identical to its baseline in every matrix
ever measured**, and every published number had the operator's settings silently in
the loop.
:::

The rule is enforced, not documented:

```text
trysquare.toml: [defaults] may not set thinking. These decide what is measured, so
they belong to the scenario and are never inherited: the same file must not
measure something different on another machine.
```

## 3. A validator that could not judge never yields a verdict

A crash, a timeout, unreadable JSON, or a missing declared metric makes the run
**invalid**, not `false`. It is counted and displayed, its `stderr` is kept, and the
cell is not publishable - but the matrix continues, because the runs already paid for
are what is being protected.

"Could not judge" is not "worked well".

## 4. Two verdict states, and only two

A gap is **established** if the 95% interval of its resampled difference excludes
zero, and **inconclusive** otherwise.

A third state invites a reading where a gap is "almost" something, and almost is how
six conclusions got published and then collapsed on rerun.

The seed is fixed, so a verdict is reproducible. A harness that is itself
irreproducible measures nothing.

:::{note}
The interval is checked as `low <= 0 <= high`, deliberately not `low > 0 or high < 0`:
an interval *touching* zero is inconclusive. Touching is not excluding.
:::

## 5. Repetitions are declared in advance

A matrix is never rerun "to see". Optional stopping is the cure for a result you
happen to like, so the harness removes the opportunity:

- a **resume** may only relaunch runs that produced *nothing* - never launched, or
  launched and consumed no tokens. `run --until-complete [N]` is that same resume,
  bounded and automatic: at most N passes, each relaunching only what produced
  nothing. It reaches nothing a hand-typed `--resume` could not, which is what lets a
  matrix that runs for hours finish itself;
- a validator failure is **re-scored** instead, at no token cost, because the run did
  produce a result and re-measuring it would let a resume change it;
- **attempts are counted** per run, so an abusive resume leaves a trace in
  `state.json`;
- a resume may not change **what a cell is**: `state.json` records a digest of each
  cell's declaration, and a cell that already produced a result and no longer matches
  it is refused. Editing a delta under one name would publish two configurations as
  one, which is what the directory name refuses a level up;
- relaunching the same experiment **overwrites** it. A timestamped directory per
  launch would accumulate variants of one experiment to choose between, which is
  optional stopping through the back door;
- more repetitions are **added, never substituted**. `run --repetitions 20 --extend`
  carries the runs of the same experiment measured at ten - a run id ignores the
  repetition count, so they are the very runs the larger matrix asks for - and measures
  only the difference. Nothing is re-measured, the matrix at ten is copied rather than
  moved and stays publishable beside it, and the carry is written into `state.json` and
  into the synthesis. Deciding to measure more after a matrix could be read is a real
  liberty taken with this rule: it is recorded where a reader will meet it rather than
  forbidden, because the alternative is paying twice for runs one already owns.

## 6. What the harness injects is excluded from scoring

Every brick the harness drops into the clone is added to `.git/info/exclude`.

Without it, scope scoring counts our own configuration as the agent's work, and every
configured cell drops to zero - a measurement of the tooling rather than of the
behaviour under test.

## 7. A judge is blind, and where it cannot be, the harness says so

A judge's context carries no cell name and no configuration.

But blinding is not always achievable, and pretending otherwise would be worse than
admitting it: **when the treatment *is* the prompt, handing the judge the prompt
reveals the cell.** So the harness compares the declared pieces across cells and
reports at launch which ones vary:

```text
judge: blind over 4 cells (pieces: prompt, response, diff)
```

```text
! judge is only partially blind: prompt varies between cells,
  so the judge can identify the treatment
```

No prohibition - a judge on a matrix of prompts stays possible, provided it is said.

The same argument carries to the manual form, which is generated shuffled and with
cell names withheld: someone who knows they are scoring the best-equipped cell scores
it better.

## 8. Durations compare only within one matrix

Runs are **interleaved** across cells - all cells at repetition 0, then all at
repetition 1 - so they see the same provider load.

Across matrices they are not comparable at all, and `compare` sets the cost columns
aside unless retries are near zero on both sides.

:::{admonition} Retries contaminate every cost column
:class: warning

When the stream is cut, the agent replays the turn with the whole accumulated
context. Measured on the previous bench: **zero retries gave 4 turns and 15.9k input
tokens; thirteen retries gave 24 turns and 79.4k.**

Publishing those columns without looking at retries means publishing our own load on
the provider. The synthesis now says so itself when retries are present, including
for columns marked established.
:::

## 9. A run that was cut short is not recorded at all

Stopping a matrix is normal - they run for hours - so the interrupt has to be a
measurement rule and not just a convenience. The rule is that a run interrupted part
way leaves **no row**: it stays `missing` in the ledger, and the next `--resume`
relaunches it like any other run that produced nothing.

The alternative is worse than it looks. Only `missing` and `empty` may be relaunched
(invariant 1), so a run written down as `valid` because its tokens had already been
consumed would be out of reach of every later resume: no metrics, no diff, and
permanently counted as measured.

This is why `interrupt.Stopped` is a `KeyboardInterrupt` rather than an ordinary
exception. `runner.one_run` catches `Exception` around a whole measurement, so one
frozen run cannot cost the matrix, and every path through it ends in a run with a
state. A cancellation must not be able to reach that handler.

The other half of the rule is that everything which *did* finish is kept, including the
runs that finished while the loop was busy writing down another one. They cost exactly
what the recorded one cost, and losing them means paying for them twice.

What is on disk when a matrix is stopped stays readable: state and measures are written
run by run, each to a neighbour renamed over the target, so an interrupt during a write
leaves the previous complete file rather than half of a new one. The measures are
written before the ledger, because an interrupt between the two has to leave the
recoverable half: a row the ledger still calls `missing` is relaunched and overwritten
in place, whereas a ledger entry with no row is a run nothing can reach.

## And one that is not a rule but a habit

**A validity condition must match the task.** `delivered` means "a file changed",
which is the right requirement for a task that asks for a diff and exactly the wrong
one for a task that asks for prose.

This was learned by copying `validity = ["delivered", "tests"]` from one scenario into
another whose task instructs the agent to write no code. Every run was correctly
`delivered = false`, the filter eliminated the whole matrix, and the error said only
"no valid run" immediately after eight successful ones. The message now names which
condition removed how many runs.
