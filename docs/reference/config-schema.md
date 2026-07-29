# Config schema

`etabli.toml`, found by walking up from the scenario, or given with `--config`.

:::{admonition} The hard rule, enforced at load
:class: danger

A config file may supply **machine paths and load fallbacks, and nothing else.**

`provider`, `model`, `thinking`, `etalon` and `repetitions` raise if set here. They
decide what is measured, so they belong to the scenario - otherwise the same scenario
file would measure something different on another machine.

```text
etabli.toml: [defaults] may not set thinking, repetitions. These decide what is
measured, so they belong to the scenario and are never inherited: the same file
must not measure something different on another machine
```
:::

There are **no environment variables** in this tool. The previous one had ten, and one
of them silently decided the thinking level of every published measurement.

## `[repos]`

Measurable repositories, by logical name.

```toml
[repos]
neon = "../neon"                  # relative to this file, not to the cwd
other = "/absolute/path/ok/too"
```

A scenario writes `repo = "neon"`. Relative paths resolve against the config file,
because the config describes a machine and where the operator happens to be standing
is not part of it.

An unknown name names what is known:

```text
[repos] has no entry 'ghost' (known: neon). Add it to /path/to/etabli.toml
```

## `[harness]`

Repositories providing harness bricks, pinned by tag in the scenario.

```toml
[harness]
subagent = "~/Work/Pi/subagent"
```

`~` and `$VAR` are expanded.

## `[defaults]`

```{list-table}
:header-rows: 1
:widths: 20 15 65

* - Key
  - Default
  - Meaning
* - `workdir`
  - `$TMPDIR/etabli`
  - Where clones and sessions live.
* - `concurrency`
  - `5`
  - Fallback when the scenario is silent.
* - `timeout`
  - `900`
  - Fallback, seconds per run.
* - `attempts`
  - `3`
  - Fallback for retries while nothing has been produced.
* - `draws`
  - `10000`
  - Resampling draws.
* - `seed`
  - `20260729`
  - Resampling seed.
```

`workdir` in the system temporary directory is intended: the durable archive keeps
only sources, and `replay` reconstitutes a tree from a tag and a diff when one is
needed again. Nothing of value is lost when the OS purges it.

`concurrency` and `timeout` are **fallbacks only**. "A plan carries its own load"
remains the rule, and whatever their origin they are recorded in `state.json` and
printed in the synthesis header, because they condition the retry count and therefore
every cost column.

`draws` and `seed` sit here because they are method constants rather than experiment
variables: changing them changes how a conclusion is drawn, not what is measured.

## Absent config

Not an error. A scenario that names no logical repository needs nothing resolved, and
the built-in defaults apply. A config given explicitly with `--config` that does not
exist *is* an error.
