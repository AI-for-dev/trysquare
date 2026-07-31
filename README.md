<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)"
            srcset="docs/_static/logo/trysquare-lockup-dark.svg">
    <img src="docs/_static/logo/trysquare-lockup-light.svg" alt="trysquare" height="46">
  </picture>
</h1>

> A try square tells a joiner whether a joint is true. This one tells you whether a
> measured difference is.

A scenario harness for measuring coding agents reproducibly.

One scenario is one self-contained experiment in one TOML file: the task, the
configurations to compare, the protocol, and the validation. The harness runs it,
scores it, and refuses to publish a difference that does not survive resampling.

It exists because measuring an agent is easy to get wrong in ways that look right.
Its predecessor produced six published conclusions that collapsed on rerun, and
twenty catalogued defects, most of them variations on one mistake: **a run that did
not do the work reading as a run that worked well.**

## Requirements

Python >= 3.11, because TOML parsing is `tomllib` from the standard library.
`uv sync` - or `pip install -e .` - installs the rest.

```bash
uv run --group dev pytest     # 397 tests, no network
```

Measuring anything also needs the agent binary (`pi`) on PATH and a provider you
have access to. Everything else - loading, scoring, aggregation, verdicts, parity -
runs offline.

## Getting started

```bash
cp trysquare.toml my-trysquare.toml     # edit [repos] to point at your repository
uv run trysquare run my-scenario.toml --output out --dry-run
```

`--dry-run` shows the whole plan and writes nothing. Drop it to measure.

No scenario ships with the tool: an experiment is about your repository and your
question, and a shipped one would be somebody else's. **Writing a scenario** below is a
complete file to start from, and `--dry-run` is where you find out it is wired
correctly, for free.

A `[repos]` entry may be a directory or a git URL. A URL is cloned once, at the scenario's
etalon tag, under `workdir`, and every run clones from there - so nothing has to be cloned
by hand first, and a tag moved upstream cannot change what a matrix in flight is measuring.

```bash
uv run trysquare run my-scenario.toml --output out
```

`trysquare` is a console script, so `uv tool install .` - or `pip install -e .` in a
virtual environment - puts the same command on PATH without `uv run` in front of it.
`uv run trysquare --help` lists the subcommands, and `uv run trysquare <command>
--help` the flags of one. `python -m trysquare` runs the same six subcommands from
an installed environment, which is what the tests use.

## Commands

| command | what it does |
| --- | --- |
| `run` | measures a scenario |
| `render` | rebuilds tables from stored measures, without remeasuring; `--html` rebuilds the session pages too |
| `replay` | reconstitutes archived runs; `--rescore` re-runs the script validators and rewrites the measures, at no token cost |
| `compare` | compares two experiments, refusing what is not comparable |
| `parity` | checks this harness against the previous bench, layer by layer |
| `form` | generates or ingests a blind manual scoring form |

`--output <dir>` roots everything that writes. One directory per experiment:

```
out/rule-vs-ticket_etalon-v1_ilaas_gemma-4-31b_n10/
  state.json      cells, runs, valid / empty / failed, attempt counters
  measures.json   one line per run
  synthesis.md    scores, costs, gaps and verdicts, written when the matrix is complete
  runs/<id>/      context, configuration, diff, validation output
    session/*.jsonl   the agent's own trace, one file per attempt
    session/*.html    the same trace as a page, on `render --html`
```

Relaunching the same experiment **overwrites** it. The archive of previous versions
is git. A timestamped directory per launch would accumulate variants of one
experiment to choose between, which is optional stopping through the back door.

## Writing a scenario

```toml
[scenario]
name = "rule-vs-ticket"
title = "A project rule against a well written ticket"
hypothesis = "hypothesis.md"    # declared before measuring

[task]
repo = "my-repo"             # a logical name, resolved by the config file
etalon = "etalon-v1"         # a tag, cloned; never the working tree
prompt = "tickets/vague.md"  # inline text works too

[agent]
provider = "ilaas"           # mandatory, never inherited
model = "gemma-4-31b"        # mandatory
thinking = "off"             # mandatory

[protocol]
repetitions = 10             # declared in advance
concurrency = 5              # a plan carries its own load
timeout = 900

[axes]                       # a grid: declaration order fixes the table's order
context = ["nothing", "rule", "careful ticket"]
thinking = ["off", "high"]

[values.context.rule]        # only the delta from [agent] / [task]
context = "AGENTS.md"

[values.thinking.high]
thinking = "high"

[[validation]]
mode = "script"
command = "score.py"
metrics = ["in_scope", "delivered", "tests"]     # a contract, not a comment

[verdict]
criterion = "in_scope"
reference = { context = "nothing", thinking = "off" }
validity = ["delivered", "tests"]
```

Paths in a scenario are relative to the scenario file. Cells come from a **grid**
(`[axes]` plus `[values]`), from **named variants** (`[variants.<name>]`), or from
both - concise for the regular part, precise for the irregular one.

**The baseline of an axis is its first value**, and it needs no delta block, which
shows that the baseline *is* a cell of the matrix. Every other value must declare a
delta, so a misspelled axis value raises instead of silently producing a duplicate
of the baseline that would be published twice under two names.

## Writing a validator

Any executable in any language:

```
score.py <path to context.json>
  -> {"metrics": {...}, "reasons": {...}} on stdout, exit 0
```

`examples/validator.py` is one, whole, written on `trysquare.assay` - the base that
supplies the entry point, the error contract, and the primitives (files touched,
sources at the etalon, the declared test suite) every validator was reimplementing.
The scoring function in it is nine lines. It is run against `tests/fixtures/tiny` by
the test suite, so it cannot rot the way a snippet in a document does.

The context file is handed as the single argument and **archived with the run**,
which is what makes a validation replayable by hand months later - and what makes
"fix a signature and re-score runs already paid for" true rather than aspirational.

Metric values are bare. Their type decides how they aggregate: a boolean becomes a
rate, a number a median, anything else is diagnostic only. `reasons` is optional and
per metric, and it is what makes a table readable six months later.

`metrics` in the scenario is a contract. A validator that omits a declared metric
makes the **run** invalid; extra metrics are stored but cannot carry a verdict,
which keeps a general validator reusable and lets a metric already paid for be
scored later.

Validators are **independent**: each gets the same context and cannot see what the
others found. A judge told the script's verdict is anchored on it, and its agreement
stops being an independent signal.

A path is checked **before the first token**. A missing brick used to surface as a
validator failure after a matrix had been paid for - or worse, a mistyped prompt path
became the literal string `tickets/vague.md` sent to the agent as its task, and
every run looked entirely normal.

## An LLM judge

For what no script can score, a scenario declares a judge:

```toml
[[validation]]
mode = "judge"
provider = "ilaas"                  # pinned, and distinct from the model under test
model = "gemma-4-31b"               # a judge that is the model being judged is not a judge
thinking = "off"
rubric = "rubric.md"
pieces = ["prompt", "response", "diff"]
metrics = ["note_usable", "cites_paths", "says_what_is_missing"]
```

There is no schema option or response format in the agent, so **the verdict is a
tool**, not parsed prose. The harness ships `trysquare/judge-tool.ts`, which registers
a `verdict` tool whose parameters are built from the metrics the scenario declared,
and the runtime validates the call before it reaches the harness. The format is therefore guaranteed;
whether the tool is called at all still depends on the model, and a judge that never
calls it leaves the run **invalid** rather than scoring zero.

`pieces` is declared because what the judge is given to read is half of what it
measures. `response` is the agent's final prose, not its transcript: a judge asked
whether a note is usable must score the note, not the work behind it.

The judge is **blind** - no cell name, no configuration. Where blinding is impossible
the harness says so at launch instead of pretending: when the treatment *is* the
prompt, handing the judge the prompt reveals the cell.

## The invariants, and why

These are not style preferences. Each one is a defect that was paid for.

- **A run counts only if it consumed tokens.** A provider that cuts the stream
  leaves the agent retrying and then returning turns that are real but empty. Such
  a run breaks no rule, touches no file and fails no test, so a naive harness
  records it as exemplary.
- **Nothing that changes a measurement may be inherited.** Provider, model,
  thinking, etalon and repetitions are mandatory in the scenario and never come
  from the config file or a default. There are no environment variables anywhere in
  this tool: the predecessor had ten, one of which silently decided the thinking
  level of every measurement ever published.
- **A validator that could not judge never yields a verdict.** A crash, a timeout,
  unreadable JSON or a missing declared metric makes the run invalid, not false.
- **Two states only: established or inconclusive.** A gap is publishable if the 95%
  interval of its resampled difference excludes zero. The seed is fixed, because a
  harness that is itself irreproducible measures nothing.
- **Repetitions are declared in advance**, and a matrix is never rerun "to see".
  A resume may only relaunch runs that produced *nothing*; a validator failure is
  re-scored instead, at no token cost. Attempts are counted, so an abusive resume
  leaves a trace.
- **What the harness injects is excluded from scoring.** Without it, scope scoring
  counts our own configuration as the agent's work and every configured cell drops
  to zero: a measurement of our tooling rather than of the behaviour under test.
- **A judge is blind, and where it cannot be, the harness says so.** When the
  treatment *is* the prompt, handing the judge the prompt reveals the cell. The
  harness reports which declared pieces vary across cells rather than pretending.
- **Durations compare only within one matrix.** Runs are interleaved across cells so
  they see the same provider load. Across matrices they are not comparable, and
  `compare` sets cost columns aside when retries are not near zero.

## Parity with the previous bench

Parity is demonstrated in layers, and three of them are exact, at zero tokens:

    layer 1  stripping              exact   from archived sessions
    layer 2  scoring                exact   from tag + diff.patch
    layer 3  aggregation + verdict   exact   from published per-run rows
    layer 4  launching the agent     not comparable, it samples

```bash
uv run trysquare parity <bench measures.json> --archive <bench traces dir>
uv run trysquare parity --smoke <experiment dir>          # layer 4
```

Layer 4 checks only what does not depend on the sample: every run valid, the outputs
complete, each run's directory whole, and - the one that matters most - **the thinking
level each session recorded equals the level its cell declared.** That check is what
makes the defect which rendered the thinking cell identical to the baseline unable to
recur. It concludes nothing about any configuration, and says so.

**Neither tool is the reference.** Two computations are compared over the same
archived material, and the material arbitrates. A gap on an exact layer has three
admitted outcomes: this harness is wrong, the bench was wrong and a published number
needs correcting, or the archive is missing something. Presuming the bench correct
would freeze its twenty defects into its successor.

Layer 3 is checked in the test suite against a committed fixture, and reproduces the
bench's published gap table exactly - all 25 cells including the
established/inconclusive marks.

## Layout

```
trysquare/
  scenario.py   loads and validates a scenario, expands it into cells   \
  measure.py    what counts as a measurement, how metrics combine        |  pure
  verdict.py    resampling, fixed seed, two states                       |
  table.py      score, cost and gap tables                                /
  parity.py     proving this harness reproduces its predecessor
  config.py     machine paths and load fallbacks, and nothing else      \
  repo.py       clone at a tag, inject bricks, exclude them              |  shell
  agent.py      the invocation, and running it                          |
  validation.py validators, blinding, preconditions                     |
  assay.py      the base a validator is written on                       |
  runner.py     orchestration: interleaving, concurrency, archiving      |
  progress.py   a pinned bar for the loops that take hours               |
  cli.py        argparse, overrides, reporting                          /

  judge-tool.ts the judge's verdict tool          \  shipped inside the package:
  agent-gate.ts the subagent scope gate           /  neither is an experiment's choice

examples/validator.py   a whole validator, run by the suite against tests/fixtures/tiny
```

The split is by whether code touches the world. Everything in the pure half is
testable without a network, a clone, or an API key - which is why the
methodological invariants have tests at all.

The two `.ts` bricks live in the package rather than beside it because an installed
wheel has no repository around it, and because neither is a per-experiment choice:
the gate is loaded by the act of injecting subagents, not by a scenario remembering
to ask for it.

## Not implemented yet

Stated rather than left to be discovered:

- **HTML for the synthesis.** `synthesis.html` and a `pages` command are designed but
  not written; only `synthesis.md` is produced. Readable transcripts *are* produced -
  `render --html` writes one page per archived session, in the run's own directory.
- **`compare` reports, it does not tabulate.** It applies the refusals - different
  etalons, contaminated cost columns - and prints what differs, but does not yet
  render a side-by-side table.
- **A judge is not re-scored.** `replay --rescore` re-runs script validators and
  reuses the archived judge verdict, because re-running a judge costs tokens and
  `replay` exists on the promise that it costs none. Correcting a judged metric
  therefore means measuring again.
- **Parity layer 2 has been demonstrated but is not a command.** Two archived runs
  were reconstituted and re-scored by hand, and matched exactly; `parity --archive`
  currently runs layers 3 and 1 only.

## Licence

Not yet chosen.

## Documentation

Full reference and user guide, built with Sphinx and MyST:

```bash
uv venv && uv pip install -e ".[docs]"
make -C docs html          # then open docs/_build/html/index.html
```

- **Guide** - getting started, core concepts, writing a scenario, writing a
  validator, the invariants and their reasoning, parity, troubleshooting
- **Reference** - CLI, scenario schema, config schema, outputs, Python API

Sphinx lives in the `docs` optional group and is never imported by the package.
