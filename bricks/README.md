# bricks

Harness bricks a scenario references by path.

**The task material in here is not translated, and must not be.** The prompts and
`AGENTS.md` are byte-identical to what the published matrix was measured with.
They are experimental inputs, not documentation: changing a word changes the
measurement and voids the parity check against the previous bench. They are in
French because that is what ran.

Everything that is *about* the material - comments, this file, commit messages -
is in English.

| file | what it is |
| --- | --- |
| `vague-ticket.md` | the baseline task: a real but sloppy request |
| `careful-ticket.md` | the same work, asked properly |
| `AGENTS.md` | a project convention, as a permanent context file |
| `agent-gate.ts` | forces subagent scope and refuses agents the scenario did not inject |

## Why the careful ticket does not forbid overflow

An earlier version contained "ne traite aucune autre issue", which is the exact
negation of what the criterion measures. That cell was verifying obedience to an
explicit instruction rather than the quality of a harness, and it saturated the
scale at zero so nothing downstream could demonstrate anything.

The clause is gone. The discipline rule lives in `AGENTS.md`, which is where a
project convention belongs, and the question becomes honest: is a well specified
ticket enough, without forbidding the overflow outright?
