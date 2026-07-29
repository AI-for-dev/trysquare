# etabli

A scenario harness for measuring coding agents reproducibly.

One scenario is one self-contained experiment in one TOML file: the task, the
configurations to compare, the protocol, and the validation. The harness runs it,
scores it, and refuses to publish a difference that does not survive resampling.

It exists because measuring an agent is easy to get wrong in ways that look
right. Its predecessor produced six published conclusions that collapsed on
rerun, and twenty catalogued defects, most of them variations on one mistake:
**a run that did not do the work reading as a run that worked well.**

Status: under construction. The pure core (scenario loading, measurement rules,
publication verdict, tables) is in place and tested, including an exact parity
check against the predecessor's published results.

## Requirements

Python >= 3.11, and nothing else. TOML parsing is `tomllib` from the standard
library. A measurement tool that needs an install step is a measurement tool
people work around.

```bash
uv run python -m unittest discover -s tests -t .
```

## The invariants, and why

These are not style preferences. Each one is a defect that was paid for.

- **A run counts only if it consumed tokens.** A provider that cuts the stream
  leaves the agent retrying and then returning turns that are real but empty.
  Such a run breaks no rule, touches no file and fails no test, so a naive
  harness records it as exemplary.
- **Nothing that changes a measurement may be inherited.** Provider, model,
  thinking level, etalon and repetitions are mandatory in the scenario and are
  never supplied by a config file or a default. The predecessor read the thinking
  level from the operator's personal settings, so the cell meant to test thinking
  was silently identical to the baseline in every matrix ever measured.
- **A validator that could not judge never yields a verdict.** A crash, a
  timeout, unreadable JSON or a missing declared metric makes the run invalid,
  not false.
- **Two states only: established or inconclusive.** A gap is publishable if the
  95% interval of its resampled difference excludes zero. The seed is fixed, so a
  verdict is reproducible; a harness that is itself irreproducible measures
  nothing.
- **Repetitions are declared in advance**, and a matrix is never rerun "to see".
  Optional stopping is the cure for a result you happen to like.

## Layout

```
etabli/
  scenario.py   loads and validates a scenario, expands it into cells
  measure.py    what counts as a measurement, how metrics combine
  verdict.py    resampling, fixed seed, two states
  table.py      cell table and gap table
  parity.py     proving this harness reproduces its predecessor
```

The split is by whether code touches the world. Everything above is pure, so the
methodological invariants are testable without a network, a clone, or an API key.

## Licence

Not yet chosen.
