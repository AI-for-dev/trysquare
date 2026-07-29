# Rubric: is this impact note usable?

You are scoring an **impact note**: a short piece of analysis produced before any
code is written, whose job is to tell a maintainer where a change lands and what it
threatens. You are not scoring code, and you are not scoring whether the analysis is
the one you would have written.

You do not know how this note was produced. Do not speculate about it, and do not let
its length, tone or formatting stand in for its content.

## The metrics

### `note_usable`

`true` when a maintainer could act on this note without redoing the investigation
themselves. That requires all three of:

- it names where the change lands, specifically enough to open the file;
- it names at least one thing that depends on the area being changed;
- it does not assert anything the material contradicts.

`false` if it is a restatement of the task, a plan of what the author intends to do
rather than what the change touches, or a summary so general it would fit any
repository.

A note may be short and still be usable. Brevity is not a fault; vagueness is.

### `cites_paths`

`true` when the note points at concrete locations - a file path, or a path with a
line or a symbol name. One accurate citation is enough.

`false` when it refers to code only in prose ("the collision logic", "the rendering
loop") without ever saying where that is.

Judge accuracy, not quantity: a note citing five paths that do not exist scores
`false`. If you cannot tell whether a path exists from the material you were given,
treat a plausible, specific path as accurate.

### `says_what_is_missing`

`true` when the note names something it could not determine, something the task
leaves open, or a risk it cannot rule out. An explicit "the ticket does not say
whether X" counts. So does naming an invariant that must not break.

`false` when the note reads as complete and confident throughout, with no
acknowledged uncertainty.

This is deliberately not a measure of quality. A note that admits a gap is more
usable than one that hides it, and a judge that rewards confidence would select for
the opposite.

## How to answer

Read the material, then call the `verdict` tool exactly once with all three metrics
and a one-sentence reason for each, quoting what decided it. Prose outside the tool
call is discarded.

If the material is empty or unreadable, still call the tool: set every metric
`false` and say so in the reasons. A missing verdict is treated as a broken judge,
not as a negative one, so failing to call the tool loses the run rather than
recording a low score.
