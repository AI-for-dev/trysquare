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
  launched and consumed no tokens;
- a validator failure is **re-scored** instead, at no token cost, because the run did
  produce a result and re-measuring it would let a resume change it;
- **attempts are counted** per run, so an abusive resume leaves a trace in
  `state.json`;
- relaunching the same experiment **overwrites** it. A timestamped directory per
  launch would accumulate variants of one experiment to choose between, which is
  optional stopping through the back door.

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

## And one that is not a rule but a habit

**A validity condition must match the task.** `delivered` means "a file changed",
which is the right requirement for a task that asks for a diff and exactly the wrong
one for a task that asks for prose.

This was learned by copying `validity = ["delivered", "tests"]` from one scenario into
another whose task instructs the agent to write no code. Every run was correctly
`delivered = false`, the filter eliminated the whole matrix, and the error said only
"no valid run" immediately after eight successful ones. The message now names which
condition removed how many runs.
