# Hypothesis: does a read-only subagent produce a usable impact note?

Declared before measuring.

## The task was changed after a first pass, and why

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

A read-only subagent with a purpose-written definition produces an impact note a
maintainer can act on more often than the model alone does. The gain comes from the
definition, not from the mere existence of a delegation tool.

## What would falsify it

- `+extension` scores as well as `+subagents`. Then the gain comes from having a
  delegation tool at all, and the agent definitions are decoration. This is the
  witness that makes the claim falsifiable, and it is why the cell exists.
- `nothing` already scores high. Then there is nothing to demonstrate: the model
  alone writes usable notes and the toolkit is answering a question nobody has.
- `full stack` scores worse than `+subagents`. Then adding the profiler skill costs
  more attention than it buys, and the stack should stop one brick earlier.
- The judge's verdicts do not reproduce across repetitions of the same cell. Then
  the instrument is too noisy to carry this criterion, and its dispersion will say
  so by widening every interval until nothing is established. That is the honest
  outcome, not a reason to smooth it with a majority vote.

## What is not claimed

Nothing about whether the note is *correct*, only whether it is usable: it names
where the change lands, what depends on it, and what it could not determine. A
correct-but-unusable note and a usable-but-wrong note are different failures, and
this criterion only sees the first.

Nothing about the subagent mechanism's guarantee. That a read-only agent *cannot*
write is a property of the harness, verified separately; it is not something a rate
over ten runs should be asked to establish.

## A note on the judge

The judge is blind: it receives the task, the response and the diff, and nothing
about which cell produced them. Here the task is constant across all four cells, so
blinding is complete. Its verdict is a schema-checked tool call, so the *format* is
guaranteed; whether it calls the tool at all is not, and a judge that does not leaves
the run invalid rather than scoring zero.

One call per run, one vote. The matrix already repeats, so the judge's noise lands in
the cell's dispersion where the resampling accounts for it - and where it is visible
in the table instead of being averaged away.
