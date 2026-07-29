---
name: profile
description: Measures where the per-frame JavaScript time actually goes and reports the numbers. Use it before proposing, accepting or refusing any performance change, and whenever the question is what is slow, what is expensive, where the bottleneck is, or whether an optimisation is worth doing — including when a ticket already asserts that something is slow.
---

# Profile the frame

Run the profiler and report what it measured. Do not answer from reading the
code: reading tells you what *looks* expensive, measuring tells you what *is*.

## Steps

1. From the repository root:

   ```bash
   node .pi/skills/profile/profile.mjs --scale
   ```

   Add `--hz 120` for a 120 Hz display, `--reps 15` for a tighter median.

2. Read the two tables. The first gives the cost of each part of one frame; the
   second gives how each part grows with the brick count.

3. Report, in this order:

   - which part dominates the per-frame JavaScript, and by what factor;
   - what share of the frame budget the total represents;
   - the measured share of any part the ticket or the user has accused — state
     it even, and especially, when the answer is "too small to matter";
   - what you would change, in what order, and what each change should save.

   That last point is your judgement. The profiler reports facts and says
   nothing about what to fix, on purpose.

4. If the numbers contradict the premise of the request, say so plainly and give
   the figure rather than working around it.

## Reading the numbers

- **% of frame budget** — 16.67 ms at 60 Hz, 8.33 ms at 120 Hz. Past 100 % the
  frame is late and the game visibly stutters.
- **% of per-frame JS** — the share among the measured parts. This is the column
  that says where the work actually is.
- **bricks to fill the budget** — extrapolated from the two widest measured
  points, not measured. It answers "how far can this grow before it hurts". A
  part whose cost does not follow the brick count is reported as `flat` instead.
- Canvas drawing is **not** in these numbers: the profiler measures pure logic,
  which is what runs identically inside and outside a browser.

## Adapting it to another repository

`postes()`, at the bottom of `profile.mjs`, is the only place to change. One
entry per piece of work a frame does. Each `run` must return a number and must
do the same amount of work on every call — a part that consumes its own input,
by destroying bricks for instance, ends up measuring its own tail rather than a
frame.
