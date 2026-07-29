# Hypothesis: does a read-only subagent produce a usable impact note?

Declared before measuring.

## Second change: a task with breadth (issue #3 instead of #4)

The n=10 pass on issue #4 de-saturated the criterion - 60 to 80% usable, against 100%
everywhere before - so removing the rubric from the task worked. But **nothing was
established**, and the point estimates went the wrong way: the baseline tied for
highest at 8/10, `+subagents` was lowest at 6/10, every interval straddling zero.

The falsification condition that fired is the first one below: `+extension`, the witness
loading the extension with nobody to delegate to, scored the same as `+subagents`. All
three equipped cells sat within noise of the baseline.

The diagnosis is the task, not the mechanism. **Issue #4's impact is confined to
essentially one file.** A subagent pays off when a task needs *broad* reading that would
otherwise flood the main context, and there was no breadth for delegation to buy.

Issue #3 (night mode) has it, verified in the repository before choosing it rather than
assumed:

- `game/theme.js` holds 9 of the 11 hard-coded colours and both palette exports;
- `game/neon.js` carries 5 theme references, the other 2 colours, and 22 exports;
- `game/bloom.js` consumes the theme and is the contrast-sensitive glow;
- `game/index.html` has 6 style lines and is where a toggle would live;
- `game/neon.test.js` references the theme **zero times** - nothing tests colours at
  all, which is a real risk the ticket does not mention;
- and it cannot land before #6 routes the hard-coded colours, a cross-issue dependency.

So four files to change, five to read, one untested area, one blocking dependency.

**Only the issue changed.** The wording, the rubric and the criterion are untouched, so
the difference stays attributable to breadth alone. `cites_paths` and
`says_what_is_missing` remain declared, so either can be scored from the same measures
afterwards with `render`, without remeasuring.

## The first change: removing the rubric from the task, and why

A smoke pass at n=2 gave **2/2 usable notes in every cell, including the baseline**.
That is one of the falsification conditions below - if `nothing` already scores high
there is nothing to demonstrate - but the cause turned out to be a defect in the task
rather than a finding about subagents.

The first task enumerated the rubric: *"la note doit dire : ou le changement atterrit,
ce qui en depend, ce que le ticket ne dit pas, et ce qui ne doit pas bouger"*. Those
are, almost word for word, the three things the rubric scores. So the cell measured
**obedience to an explicit instruction**, not whether a toolkit produces better
analysis - and it saturated the scale, as a prompt containing the answer to its own
criterion always does. The sister effort had already paid for this exact mistake once.

The task is now a maintainer's real question - *what will this break, tell me what you
found in the code that the ticket does not say* - with no format prescribed. An answer
that paraphrases the ticket now fails `note_usable`, because the rubric marks a
restatement as unusable.

**Only the task changed.** The rubric is untouched, deliberately: changing both at once
would make the difference impossible to attribute.

A consequence worth recording: the task is part of what is measured but is **not** part
of the output directory name, so results from before and after this change would share
a name while not being comparable. The n=2 directory from the first pass was deleted
rather than kept.

## What is predicted

**A read-only subagent leads the note to cite more of the repository than the model
alone does.** The criterion is `cited_paths`: distinct real files the note points at,
checked against the etalon tag, aggregated as a median.

The gain, if any, comes from the agent definitions - not from the mere presence of a
delegation tool, which is what the `+extension` witness is there to separate.

## Why this criterion and not a judge

The criterion was `note_usable`, scored by an LLM judge, for two matrices. It is gone.

It saturated at 10/10 in every cell as soon as the task got easier, and worse, all
three of the judge's metrics came out **identical in 40 runs out of 40** - it decided
"good note" once and answered everything alike. On the narrower task it had
discriminated (19 of 40 runs with mixed verdicts), so the halo was induced by the task
rather than fixed. An instrument that stops discriminating exactly when its subject
gets easy is not an instrument.

A count has no ceiling and no halo. It also produced a **stronger negative** when
applied retroactively to the 40 runs already paid for: gaps of `+0` with bounds of
[-0.5, +0], which rules out an effect of any size worth caring about, where the
judge's wide intervals could not tell "no effect" from "not enough power".

`bogus_paths` travels with it, and it is why this beats a boolean. A note inventing
`game/score.js` is worse than one citing nothing, and "does it cite paths" scores
those two the same.

## What would falsify it

- **`+extension` cites as much as `+subagents`.** Then the gain comes from having a
  delegation tool at all and the definitions are decoration. This is the witness that
  makes the claim falsifiable, and it is why the cell exists.
- **`nothing` already cites everything worth citing.** The etalon has 14 files and a
  good note cites about 3, so the ceiling is low: if the baseline is already at the
  ceiling there is no room to measure and the scenario cannot answer the question.
- **`full stack` cites less than `+subagents`.** Then the profiler skill costs more
  attention than it buys and the stack should stop one brick earlier.
- **`bogus_paths` rises with the toolkit.** Then delegation is producing confident
  invention rather than reading, which would be worse than no effect.
- **The medians move but the intervals never exclude zero at n=10.** Then the effect,
  if real, is smaller than this design can see, and claiming it would need a larger n
  declared in advance rather than after looking.

## What is not claimed

Nothing about whether the note is *correct*, or even useful - only how much of the
repository it demonstrably points at. A note citing five real files and drawing the
wrong conclusion scores well here. That is the price of a mechanical criterion, and it
is worth paying after two matrices in which the judged criterion measured its own
prompt.

Nothing about the subagent mechanism's guarantee. That a read-only agent *cannot*
write is a property of the harness, verified separately; it is not something a rate
over ten runs should be asked to establish.


---

## What happened

Measured at n=10, 40 runs, all valid, no judge.
`mesures/subagents-citations_etalon-v1_ilaas_gemma-4-31b_n10`.

| cell | median | range | exact | bogus | gap to `nothing` |
| --- | --- | --- | --- | --- | --- |
| `nothing` | 3 | 0-3 | 1.5 | 0 | - |
| `+extension` | 3 | 2-4 | 1.5 | 0 | +0, inconclusive |
| `+subagents` | 3 | 3-4 | 2.0 | 0 | +0, inconclusive |
| `full stack` | 3 | 0-3 | 2.0 | 0 | +0, inconclusive |

**The prediction is not supported: nothing is established, and the gaps are exactly
zero.** This replicates the same criterion scored retroactively over the previous
matrix - a different 40 runs, the same medians and the same `+0` gaps. Two independent
samples through one instrument agreeing is the strongest result in the sequence, and
what it agrees on is the absence of an effect.

### The falsification condition that fired

The second one: **the baseline is already at the ceiling.** Every range is pinned
against 3-4 files, `nothing` already reaches 3, and the etalon holds 14 files of which
perhaps 5 are relevant. There is no headroom for a toolkit to demonstrate anything.

So the finding is not "subagents do not help". It is **"NÉON cannot answer this
question"** - a 14-file repository gives delegation nothing to save. That distinction
matters: the first would be a claim about the mechanism, and only the second is
supported.

### What did not fire

`bogus_paths` is 0 in all 40 runs, as it was in the previous matrix. No agent invented
a path, so the "confident invention" condition is ruled out rather than untested.

### One signal below the threshold

`+subagents` is the only cell that never drops below 3 citations (range 3-4, 10/10 on
`cites_paths`), while `nothing` and `full stack` each have a run at 0. The same pattern
appeared in the previous matrix. It is **not established** at n=10, and more
repetitions are the wrong remedy: a criterion pinned to a ceiling does not get
sharper with n, it needs headroom.

### What would make this answerable

A different **etalon**, not a different task or criterion. A repository where an impact
note could plausibly cite fifteen files and a bare model cites four. That is a new
experiment rather than another round of this one, and the instrument built here would
carry over unchanged.
