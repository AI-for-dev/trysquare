# Config schema

`trysquare.toml`, found by walking up from the scenario, or given with `--config`.

:::{admonition} The hard rule, enforced at load
:class: danger

A config file may supply **machine paths and load fallbacks, and nothing else.**

`provider`, `model`, `thinking`, `etalon` and `repetitions` raise if set here. They
decide what is measured, so they belong to the scenario - otherwise the same scenario
file would measure something different on another machine.

```text
trysquare.toml: [defaults] may not set thinking, repetitions. These decide what is
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
my-repo = "../my-repo"            # relative to this file, not to the cwd
other = "/absolute/path/ok/too"
remote = "https://github.com/org/repo.git"     # a URL works too
```

A scenario writes `repo = "my-repo"`. Relative paths resolve against the config file,
because the config describes a machine and where the operator happens to be standing
is not part of it.

An unknown name names what is known, and suggests the likely fix when the miss
is a near one:

```text
[repos] has no entry 'my-rpeo' (known: my-repo, other, remote) (did you mean 'my-repo'?). Add it to /path/to/trysquare.toml
```

When no `trysquare.toml` exists at all, the refusal says to create one - with the
two lines it needs - rather than to edit a file that is not there.

### A URL instead of a directory

`https://`, `http://`, `ssh://`, `git://`, `file://` and the scp-like
`git@host:org/repo.git` are all recognised. A URL is **pinned**: cloned once, at the
scenario's etalon tag, into

```text
<workdir>/sources/<name>-<hash of the url>-<tag>/
```

and every run then clones from that local directory. Three consequences worth knowing:

- **A tag moved upstream is ignored.** The directory is keyed by tag, so one that is
  already there is by construction already at the tag being asked for. Nothing is
  refetched mid-matrix, and what the later runs measure cannot drift from what the
  earlier ones did.
- **Editing the URL re-clones.** The hash is part of the directory name, so a changed URL
  lands somewhere else instead of silently reusing the previous repository's clone.
- **`workdir` is disposable.** If the OS purges it, the next run clones again, so a
  `--resume` against a URL needs the network once more.

A URL is taken verbatim: `$VAR` is **not** expanded in one. A username or token coming
from the shell would be invisible inheritance - absent from the archive, and different on
the next machine.

Pinning happens when a run starts, never while planning: `--dry-run` against a URL
touches neither disk nor network.

## `[harness]`

Repositories providing harness bricks, pinned by tag in the scenario.

```toml
[harness]
subagent = "~/Work/Pi/subagent"
# subagent = "https://github.com/org/pi-subagent.git"
```

`~` and `$VAR` are expanded in a path. A URL is accepted on the same terms as in
`[repos]` and taken verbatim; the brick's clone is already keyed by tag.

## `[defaults]`

```{list-table}
:header-rows: 1
:widths: 20 15 65

* - Key
  - Default
  - Meaning
* - `workdir`
  - `$TMPDIR/trysquare`
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
