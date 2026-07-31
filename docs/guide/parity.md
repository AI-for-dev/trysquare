# Parity with the previous bench

This tool replaces one that had already published results. Proving it computes the same
things is not a formality: if it silently disagreed, either its numbers or the published
ones would be wrong, and nobody would know which.

Parity is demonstrated **in layers**, and three of them are exact at zero tokens.

```{list-table}
:header-rows: 1
:widths: 12 30 18 40

* - Layer
  - What
  - Exactness
  - From
* - 1
  - stripping
  - **exact**
  - archived sessions
* - 2
  - scoring
  - **exact**
  - tag + `diff.patch`
* - 3
  - aggregation + verdict
  - **exact**
  - published per-run rows
* - 4
  - launching the agent
  - not comparable
  - it samples
```

:::{admonition} The question that dissolved
:class: note

The obvious framing is "compare the rates, with a tolerance". That question only exists
while you believe two *samples* must be compared.

The previous tool's published measures turned out to be **60 per-run rows**, not
aggregates, each carrying its full scoring. So three of the four layers need no new
measurement at all, and no tolerance: identical inputs through an identical method must
give identical output.
:::

## Neither tool is the reference

Two computations are compared over the same archived material, and **the material
arbitrates**. A gap on an exact layer blocks, with three admitted outcomes:

1. **this harness is wrong** - fix it;
2. **the bench was wrong** - fix the *published* number and record the defect;
3. **archive artefact** - documented, and removed from the parity scope.

Presuming the bench correct would have been a losing bet: it had twenty catalogued
defects, and freezing them into its successor is exactly what a parity check must avoid.

## Running it

```bash
trysquare parity <measures.json>                          # layer 3
trysquare parity <measures.json> --archive <traces dir>   # layers 3 and 1
trysquare parity <measures.json> --archive <dir> --scenario <s.toml>  # and layer 2
trysquare parity --smoke <experiment dir>                 # layer 4
```

The layers run **cheapest first**: 3 needs one JSON file, 1 reads the archived sessions,
2 clones the etalon once per run. A disagreement therefore surfaces before you pay for
the clones. Every layer that can run does run, and the command exits non-zero if any of
them disagreed.

## Layer 3, in the test suite

Layer 3 runs on a **committed fixture**, so it protects against regression forever:

```bash
uv run --group dev pytest tests/test_parity.py
```

It reproduces the published gap table **exactly** - all 25 cells, five cells by five
measures, including the established/inconclusive marks - in under a second. That single
check validates the aggregation, the validity filter, the resampling, the establishment
rule and the rendering together.

Two details it depends on, both read from the previous tool's source rather than
assumed: its validity condition is `delivered AND tests`, and the resampling creates a
fresh seeded RNG **per interval**, drawing the reference sample before the cell sample.

## Layer 1 is a real test, not a tautology

The previous tool counted `message_end` events in the live **stream**. This one reads
the archived **session**, whose line types are entirely different (`session`,
`model_change`, `thinking_level_change`, `message`, with usage nested under
`message.usage`). Two different paths to the same figures.

:::{warning}
`retries` is **not** compared. It derives from `auto_retry_start`, a stream event a
session does not contain, so it is an archive artefact and is explicitly removed from
the parity scope rather than silently treated as zero.
:::

### What layer 1 actually found

**58 of 60 sessions reproduce their recorded tokens and turns exactly.** The remaining
two are a clean pairwise swap: one directory holds the other's session.

The numbers are right and the **labels** are crossed, so this is outcome 3 - an archive
artefact, not a disagreement about counting. And because both runs belong to the same
cell, **no aggregate and no published number is affected**, which is precisely why layer
3 passes perfectly while layer 1 sees it: layer 3 groups by cell.

The report distinguishes the two cases, because a same-cell swap and a cross-cell swap
have very different consequences:

```text
58/60 sessions reproduce their recorded figures exactly
LABEL: base-9 holds the figures published for base-0 - same cell, so no aggregate is affected
```

## Layer 2 re-scores the archive, and needs a scenario

Layer 2 reconstitutes each archived run - `git clone` at the tag, `git apply` of its
`diff.patch` - and scores the tree again, then compares metric by metric with what the
bench published. Same reconstitution as {doc}`replay <../reference/cli>`, deliberately:
one machinery, so a tree rebuilt for a re-scoring and a tree rebuilt for parity cannot
drift apart.

Scoring means running validators, and which ones is a question the harness cannot
answer for you - hence `--scenario`. Its **script** validators are what re-score the
archive.

:::{warning}
**A judge is not run.** Its verdict costs tokens, layer 2 costs none, and the bench's
archive holds no verdict of its own to reuse the way `replay --rescore` reuses ours. So
a metric only a judge can produce is **out of layer 2's scope**, named once, exactly as
layer 1 treats `retries`.

A metric returned for some runs and missing for others is the opposite case - an
unreliable validator - and it blocks.
:::

```text
layer 2 - scoring, from tag + diff.patch (4 runs, one clone each)
  3/4 runs re-score to their published metrics exactly
  api_stable: no validator here returns it, so it is out of scope
  tests: no validator here returns it, so it is out of scope
  base-1/delivered: bench True, here False
  base-1/overflow: bench True, here False
```

A gap names the run, the metric and **both** computations, because that is what it takes
to decide which of the three outcomes above applies. Counting them would not.

## Layer 4 is a smoke pass, not a comparison

Layer 4 launches the agent, so its rates are a different sample and can never
demonstrate parity. What it demonstrates is that the harness is **wired**, on criteria
that do not depend on the sample:

- every run valid, meaning every run consumed tokens;
- the outputs a complete matrix owes are present;
- each run's directory holds its context, configuration, diff and validation;
- **the thinking level each session recorded equals the level its cell declared;**
- **the model that answered is still the one the scenario's pattern names.**

The last two are why layer 4 exists. The first makes the defect that rendered a thinking
cell identical to its baseline in every published matrix unable to recur silently.

The second is that same defect one axis over. `model` is a **pattern** the agent resolves,
so `gemma-4` legitimately runs `gemma-4-31b`; the check is therefore not equality but
"the pattern must still name what answered", and a fallback to the machine's
`defaultModel` is exactly what fails it. Measured on a real matrix: six runs declared
`gemma-4`, all six sessions recorded `gemma-4-31b`, and nothing in the archive said so.

```text
layer 4 - smoke pass over rule-vs-ticket_etalon-v1_ilaas_gemma-4-31b_n2
  12/12 runs ran the model their pattern names
  12 sessions checked for the declared thinking level

  every mechanical criterion holds. No statistical claim: a smoke
  pass at small n concludes nothing about any configuration.
```

:::{note}
An archive written before `model_id` existed is reported as **unchecked** rather than as
passing - a check that silently holds over material it never read is worse than none:

```text
  6 runs record no model_id, so what answered could not be checked
```
:::

:::{tip}
Run the smoke pass with `--repetitions 2` into its own `..._n2` directory. It cannot
touch a published matrix, and it exercises the whole chain for the price of a handful of
runs.
:::
